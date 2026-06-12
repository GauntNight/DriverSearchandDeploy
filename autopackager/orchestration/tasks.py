"""Celery Tasks for Job Processing"""

from celery import chain
from autopackager.orchestration.celery_app import celery_app
from autopackager.orchestration.engine import OrchestrationEngine
from autopackager.models.job import JobState, JobType
from autopackager.utils.logger import get_logger

logger = get_logger(__name__)


def _demo_event(job_id, state, text, level="info", **extra):
    """Additive, optional demo-console hook.

    Publishes a one-line step event to the demo's Redis channel so the demo
    UI can narrate the pipeline live. This is the ONLY coupling between the
    core pipeline and the (removable) ``demo/`` package: a lazy import wrapped
    so a missing ``demo`` package or a Redis hiccup can never affect a real
    packaging job. Delete ``demo/`` and these calls become silent no-ops.
    """
    try:
        from demo.events import publish_pipeline_event

        publish_pipeline_event(job_id, state, text, level=level, **extra)
    except Exception:
        pass


@celery_app.task(bind=True, name='autopackager.create_packaging_job')
def create_packaging_job(
    self,
    job_type: str,
    software_title: str,
    vendor: str,
    current_version: str = None,
    hardware_model: str = None,
    driver_type: str = None,
    metadata: dict = None
):
    """Create a new packaging job and start processing"""
    logger.info("Creating packaging job", software_title=software_title, vendor=vendor)

    engine = OrchestrationEngine()

    # Create the job
    job = engine.create_job(
        job_type=JobType(job_type),
        software_title=software_title,
        vendor=vendor,
        current_version=current_version,
        hardware_model=hardware_model,
        driver_type=driver_type,
        metadata=metadata
    )

    # Start the processing pipeline
    process_job.delay(job.id)

    return {"job_id": job.id, "status": "created"}


@celery_app.task(bind=True, name='autopackager.process_job')
def process_job(self, job_id: int):
    """Main job processing pipeline"""
    logger.info("Processing job", job_id=job_id)

    engine = OrchestrationEngine()
    job = engine.get_job(job_id)

    if not job:
        logger.error("Job not found", job_id=job_id)
        return {"error": "Job not found"}

    try:
        # Create a pipeline: Discovery -> Packaging -> Testing -> Deployment
        pipeline = chain(
            discovery_task.s(job_id),
            packaging_task.s(job_id),
            testing_task.s(job_id),
            deployment_task.s(job_id)
        )

        # Execute the pipeline
        result = pipeline.apply_async()

        return {"job_id": job_id, "pipeline_id": result.id}

    except Exception as e:
        logger.error("Failed to start job pipeline", job_id=job_id, error=str(e))
        engine.mark_job_failed(job_id, str(e))
        raise


@celery_app.task(bind=True, name='autopackager.discovery_task')
def discovery_task(self, job_id: int):
    """Discovery phase - find new versions"""
    logger.info("Starting discovery phase", job_id=job_id)

    engine = OrchestrationEngine()
    engine.update_job_state(job_id, JobState.DISCOVERING)
    _demo_event(job_id, "discovering", "Discovery started — resolving installer identity")

    try:
        # Import here to avoid circular dependencies
        from autopackager.agents.discovery import DiscoveryAgent

        agent = DiscoveryAgent()
        job = engine.get_job(job_id)

        # Execute discovery
        result = agent.discover(job)

        if result.get('update_available'):
            _ver = result.get('latest_version') or 'target'
            _demo_event(job_id, "discovering", f"Discovery complete — {_ver} ready to package")
            # Update job with discovered information
            metadata_update = {
                'target_version': result.get('latest_version'),
                'download_url': result.get('download_url'),
                'release_notes': result.get('release_notes')
            }

            # Carry MSI metadata forward so packaging can build install/uninstall
            # commands and product-code detection without re-reading the file.
            if result.get('msi_metadata'):
                metadata_update['msi_metadata'] = result['msi_metadata']
            if result.get('install_command'):
                metadata_update['install_command'] = result['install_command']

            engine.update_job_state(
                job_id,
                JobState.PENDING,
                metadata_update=metadata_update
            )
            logger.info("Discovery completed - update available", job_id=job_id)
            return {"job_id": job_id, "update_available": True}
        else:
            # No update needed, mark as completed
            engine.mark_job_completed(job_id, metadata_update={'no_update_needed': True})
            logger.info("Discovery completed - no update needed", job_id=job_id)
            return {"job_id": job_id, "update_available": False, "completed": True}

    except Exception as e:
        logger.error("Discovery failed", job_id=job_id, error=str(e))

        if engine.can_retry_job(job_id):
            retry_count = engine.increment_retry_count(job_id)
            logger.info("Retrying discovery", job_id=job_id, retry_count=retry_count)
            raise self.retry(exc=e, countdown=engine.retry_delay)
        else:
            engine.mark_job_failed(job_id, f"Discovery failed: {str(e)}")
            raise


@celery_app.task(bind=True, name='autopackager.packaging_task')
def packaging_task(self, previous_result, job_id: int):
    """Packaging phase - create .intunewin package"""
    # If previous task indicated no update needed, skip
    if previous_result and previous_result.get('completed'):
        logger.info("Skipping packaging - no update needed", job_id=job_id)
        return previous_result

    logger.info("Starting packaging phase", job_id=job_id)

    engine = OrchestrationEngine()
    engine.update_job_state(job_id, JobState.PACKAGING)
    _demo_event(job_id, "packaging", "Packaging started — building .intunewin")

    try:
        # Import here to avoid circular dependencies
        from autopackager.agents.packaging import PackagingAgent

        agent = PackagingAgent()
        job = engine.get_job(job_id)

        # Execute packaging
        result = agent.package(job)

        # Narrate the produced artifact size for the demo console.
        try:
            from pathlib import Path as _Path

            _iw = result.get('intunewin_path')
            if _iw and _Path(_iw).exists():
                _mb = _Path(_iw).stat().st_size / (1024 * 1024)
                _demo_event(job_id, "packaging", f"Built .intunewin ({_mb:.1f} MB)")
            else:
                _demo_event(job_id, "packaging", "Packaging complete")
        except Exception:
            pass

        # Update job with package information
        engine.update_job_state(
            job_id,
            JobState.PENDING,
            metadata_update={
                'package_id': result.get('package_id'),
                'intunewin_path': result.get('intunewin_path')
            }
        )

        logger.info("Packaging completed", job_id=job_id, package_id=result.get('package_id'))
        return {"job_id": job_id, "package_id": result.get('package_id')}

    except Exception as e:
        logger.error("Packaging failed", job_id=job_id, error=str(e))

        if engine.can_retry_job(job_id):
            retry_count = engine.increment_retry_count(job_id)
            logger.info("Retrying packaging", job_id=job_id, retry_count=retry_count)
            raise self.retry(exc=e, countdown=engine.retry_delay)
        else:
            engine.mark_job_failed(job_id, f"Packaging failed: {str(e)}")
            raise


@celery_app.task(bind=True, name='autopackager.testing_task')
def testing_task(self, previous_result, job_id: int):
    """Testing phase - validate package"""
    # If previous task indicated completion, skip
    if previous_result and previous_result.get('completed'):
        logger.info("Skipping testing - no update needed", job_id=job_id)
        return previous_result

    logger.info("Starting testing phase", job_id=job_id)

    engine = OrchestrationEngine()
    engine.update_job_state(job_id, JobState.TESTING)
    _demo_event(job_id, "testing", "Testing started — validating package + detection rules")

    try:
        # Import here to avoid circular dependencies
        from autopackager.agents.testing import TestingAgent

        agent = TestingAgent()
        job = engine.get_job(job_id)

        # Execute testing
        result = agent.test(job)

        if result.get('test_passed'):
            logger.info("Testing passed", job_id=job_id)
            # `gate=True` lets the demo UI reveal the optional "Approve" button
            # when the operator launched the job in approval-gate mode.
            _demo_event(job_id, "testing", "Smoke test passed", gate=True)
            return {"job_id": job_id, "test_passed": True}

        # Local install-validation failures are DETERMINISTIC — retrying just
        # re-installs the app, and for a non-silent installer that re-launches
        # its UI (the Firefox-EXE infinite-loop incident). Fail terminally,
        # NO retry, and make sure nothing was left running.
        liv = result.get('local_install_validation')
        if liv and not liv.get('skipped') and not liv.get('passed'):
            escalate = bool(liv.get('needs_engineer_review'))
            attempts = liv.get('install_attempts')
            if escalate:
                # The install ladder tried multiple silent strategies and none
                # produced a verifiable install (e.g. RealPlayer-style
                # bundleware). Mark FAILED and flag for engineer review so an
                # operator can determine a manual silent command (or reject it).
                msg = (f"Engineer escalation: no verifiable silent install after "
                       f"{attempts} attempt(s) — manual review required.")
                engine.update_job_state(
                    job_id, JobState.FAILED, error_message=msg,
                    metadata_update={'needs_engineer_review': True,
                                     'escalation_reason': msg},
                )
                _demo_event(
                    job_id, "failed",
                    f"⛔ Failed — ENGINEER ESCALATION: no silent install after "
                    f"{attempts} attempts. Manual review required.",
                    level="error", escalation=True)
            else:
                msg = ("Local install validation failed — install could not be verified "
                       f"silently; not retrying. errors={liv.get('errors')}")
                engine.mark_job_failed(job_id, msg)
                _demo_event(job_id, "failed",
                            "Install validation failed — publish blocked (no retry). "
                            "Check the silent-install command.", level="error")
            logger.error("Install validation failed (terminal, no retry)",
                         job_id=job_id, detail=msg, escalation=escalate)
            return {"job_id": job_id, "test_passed": False,
                    "validation_failed": True, "needs_engineer_review": escalate}

        error_msg = f"Testing failed: {result.get('error_message')}"
        logger.error("Testing failed", job_id=job_id, error=error_msg)
        engine.mark_job_failed(job_id, error_msg)
        raise Exception(error_msg)

    except Exception as e:
        logger.error("Testing phase error", job_id=job_id, error=str(e))

        if engine.can_retry_job(job_id):
            retry_count = engine.increment_retry_count(job_id)
            logger.info("Retrying testing", job_id=job_id, retry_count=retry_count)
            raise self.retry(exc=e, countdown=engine.retry_delay)
        else:
            engine.mark_job_failed(job_id, f"Testing failed: {str(e)}")
            raise


@celery_app.task(bind=True, name='autopackager.deployment_task')
def deployment_task(self, previous_result, job_id: int):
    """Deployment phase - publish to Intune"""
    # If previous task indicated completion, skip
    if previous_result and previous_result.get('completed'):
        logger.info("Skipping deployment - no update needed", job_id=job_id)
        return previous_result

    # If testing did NOT pass (validation failure / engineer escalation), do not
    # deploy. The chain still calls this task, but deploying would fail on
    # "package has not passed testing" and then retry-loop. The job was already
    # marked FAILED by testing_task — just short-circuit.
    if previous_result and (previous_result.get('validation_failed')
                            or previous_result.get('test_passed') is False):
        logger.info("Skipping deployment - testing did not pass", job_id=job_id,
                    needs_engineer_review=previous_result.get('needs_engineer_review'))
        return previous_result

    logger.info("Starting deployment phase", job_id=job_id)

    engine = OrchestrationEngine()
    engine.update_job_state(job_id, JobState.DEPLOYING)
    _demo_event(job_id, "deploying", "Deploying — creating Win32 app + uploading content")

    try:
        # Validate Azure configuration before attempting deployment
        from autopackager.utils.azure_validator import AzureValidator, AzureConfigurationError

        try:
            AzureValidator().validate_all()
        except AzureConfigurationError as e:
            error_msg = f"Azure configuration validation failed: {str(e)}"
            logger.error("Deployment blocked by validation", job_id=job_id, error=error_msg)
            engine.mark_job_failed(job_id, error_msg)
            return {"job_id": job_id, "error": error_msg, "validation_failed": True}

        # Import here to avoid circular dependencies
        from autopackager.agents.deployment import DeploymentAgent

        agent = DeploymentAgent()
        job = engine.get_job(job_id)

        # Execute deployment
        result = agent.deploy(job)

        _app_id = result.get('intune_app_id')
        _ring = result.get('ring') or 'Ring 0 (IT Pilot)'
        _demo_event(job_id, "deploying", f"Published to Intune (app {_app_id})")
        if not result.get('ring') or _ring != 'unassigned':
            _demo_event(job_id, "deploying", f"Assigned {_ring}")
        _demo_event(job_id, "completed", "Deployment complete ✓")

        # Mark job as completed
        engine.mark_job_completed(
            job_id,
            metadata_update={
                'intune_app_id': result.get('intune_app_id'),
                'deployment_status': result.get('status')
            }
        )

        logger.info("Deployment completed", job_id=job_id, intune_app_id=result.get('intune_app_id'))
        return {"job_id": job_id, "intune_app_id": result.get('intune_app_id'), "completed": True}

    except Exception as e:
        # Format Graph/requests/tenacity failures into one clean operator line
        # (no raw {'error': {...}} dicts on the UI). Unwrapping of tenacity
        # RetryError + Graph error code/message extraction lives in the helper.
        from autopackager.utils.graph_client import format_graph_error

        error_detail = format_graph_error(e, action="Deployment failed")

        logger.error("Deployment failed", job_id=job_id, error=error_detail)

        if engine.can_retry_job(job_id):
            retry_count = engine.increment_retry_count(job_id)
            logger.info("Retrying deployment", job_id=job_id, retry_count=retry_count)
            raise self.retry(exc=e, countdown=engine.retry_delay)
        else:
            engine.mark_job_failed(job_id, error_detail)
            _demo_event(job_id, "error", error_detail, level="error")
            # Raise a clean, picklable error — the original may be a tenacity
            # RetryError that Celery can't pickle (UnpickleableExceptionWrapper
            # noise in the worker). The job is already marked FAILED above.
            raise RuntimeError(error_detail) from None


@celery_app.task(bind=True, name='autopackager.poll_deployment_status')
def poll_deployment_status(self):
    """Poll deployment status for all in-progress deployments"""
    logger.info("Starting deployment status polling")

    try:
        # Import here to avoid circular dependencies
        from autopackager.agents.deployment import DeploymentAgent

        agent = DeploymentAgent()

        # Check all in-progress deployments
        result = agent.check_all_deployments()

        logger.info(
            "Deployment status polling completed",
            total_checked=result.get('total_checked', 0),
            successful_updates=result.get('successful_updates', 0),
            failed_updates=result.get('failed_updates', 0),
            total_installed=result.get('summary', {}).get('total_installed', 0),
            total_failed=result.get('summary', {}).get('total_failed', 0),
            total_pending=result.get('summary', {}).get('total_pending', 0),
            total_not_applicable=result.get('summary', {}).get('total_not_applicable', 0)
        )

        return result

    except Exception as e:
        logger.error("Deployment status polling failed", error=str(e))

        # Retry with exponential backoff (2 minutes initial delay)
        raise self.retry(exc=e, countdown=120, max_retries=3)


@celery_app.task(bind=True, name='autopackager.continuous_catalog_discovery')
def continuous_catalog_discovery(self):
    """Continuous catalog discovery - scan OEM catalogs for new driver versions"""
    logger.info("Starting continuous catalog discovery")

    try:
        # Import here to avoid circular dependencies
        from autopackager.agents.discovery import DiscoveryAgent
        from autopackager.models.discovery_run import DiscoveryRun
        from autopackager.models.job import Job
        from autopackager.utils.database import db_session_scope
        from autopackager.utils.config import get_config
        from datetime import datetime

        config = get_config()
        discovery_config = config.get('discovery_schedule', {})

        # Check if discovery is enabled
        if not discovery_config.get('enabled', False):
            logger.info("Continuous catalog discovery is disabled in config")
            return {'status': 'disabled'}

        # Create discovery run record
        with db_session_scope() as session:
            discovery_run = DiscoveryRun(
                started_at=datetime.utcnow(),
                catalogs_scanned=0,
                new_versions_found=0,
                jobs_created=0,
                oem_results={}
            )
            session.add(discovery_run)
            session.flush()
            run_id = discovery_run.id

        agent = DiscoveryAgent()
        catalogs_scanned = 0
        new_versions_found = 0
        jobs_created = 0
        oem_results = {}

        # Get monitored models from config
        monitored_models = discovery_config.get('monitored_models', [])

        if not monitored_models:
            logger.warning("No monitored_models configured in discovery_schedule")
            # Update discovery run with warning
            with db_session_scope() as session:
                run = session.query(DiscoveryRun).filter(DiscoveryRun.id == run_id).first()
                if run:
                    run.completed_at = datetime.utcnow()
                    run.error_message = "No monitored_models configured"
            return {'status': 'no_models_configured', 'run_id': run_id}

        # Scan each monitored model
        for model_config in monitored_models:
            vendor = model_config.get('vendor')
            hardware_model = model_config.get('model')
            driver_type = model_config.get('driver_type', 'all')

            if not vendor or not hardware_model:
                logger.warning("Invalid model config - missing vendor or model", config=model_config)
                continue

            logger.info(
                "Scanning catalog for model",
                vendor=vendor,
                hardware_model=hardware_model,
                driver_type=driver_type
            )

            try:
                # Create a dummy job object for discovery
                dummy_job = Job(
                    id=0,  # Dummy ID
                    job_type=JobType.DRIVER_UPDATE,
                    software_title=f"{vendor} {hardware_model} Driver",
                    vendor=vendor,
                    hardware_model=hardware_model,
                    driver_type=driver_type,
                    current_version=model_config.get('current_version'),
                    state=JobState.PENDING
                )

                # Execute discovery
                result = agent.discover(dummy_job)
                catalogs_scanned += 1

                # Track OEM-specific results
                if vendor not in oem_results:
                    oem_results[vendor] = {'scanned': 0, 'updates_found': 0}
                oem_results[vendor]['scanned'] += 1

                if result.get('update_available'):
                    new_versions_found += 1
                    oem_results[vendor]['updates_found'] += 1

                    target_version = result.get('latest_version')
                    download_url = result.get('download_url')

                    logger.info(
                        "New driver version found",
                        vendor=vendor,
                        model=hardware_model,
                        version=target_version
                    )

                    # Check for duplicate jobs
                    with db_session_scope() as session:
                        existing_job = session.query(Job).filter(
                            Job.vendor == vendor,
                            Job.hardware_model == hardware_model,
                            Job.target_version == target_version,
                            Job.state.notin_([JobState.FAILED, JobState.CANCELLED])
                        ).first()

                        if existing_job:
                            logger.info(
                                "Skipping duplicate job",
                                vendor=vendor,
                                model=hardware_model,
                                version=target_version,
                                existing_job_id=existing_job.id
                            )
                        else:
                            # Create new packaging job
                            create_packaging_job.delay(
                                job_type=JobType.DRIVER_UPDATE.value,
                                software_title=f"{vendor} {hardware_model} Driver",
                                vendor=vendor,
                                hardware_model=hardware_model,
                                driver_type=driver_type,
                                current_version=model_config.get('current_version'),
                                metadata={
                                    'target_version': target_version,
                                    'download_url': download_url,
                                    'release_notes': result.get('release_notes'),
                                    'discovered_by': 'continuous_catalog_discovery'
                                }
                            )
                            jobs_created += 1
                            logger.info(
                                "Created packaging job for new driver version",
                                vendor=vendor,
                                model=hardware_model,
                                version=target_version
                            )

            except Exception as e:
                logger.error(
                    "Failed to discover driver for model",
                    vendor=vendor,
                    model=hardware_model,
                    error=str(e)
                )
                # Continue with next model
                continue

        # Update discovery run with results
        with db_session_scope() as session:
            run = session.query(DiscoveryRun).filter(DiscoveryRun.id == run_id).first()
            if run:
                run.completed_at = datetime.utcnow()
                run.catalogs_scanned = catalogs_scanned
                run.new_versions_found = new_versions_found
                run.jobs_created = jobs_created
                run.oem_results = oem_results

        logger.info(
            "Continuous catalog discovery completed",
            catalogs_scanned=catalogs_scanned,
            new_versions_found=new_versions_found,
            jobs_created=jobs_created,
            oem_results=oem_results
        )

        return {
            'run_id': run_id,
            'catalogs_scanned': catalogs_scanned,
            'new_versions_found': new_versions_found,
            'jobs_created': jobs_created,
            'oem_results': oem_results
        }

    except Exception as e:
        logger.error("Continuous catalog discovery failed", error=str(e))

        # Try to update discovery run with error
        try:
            with db_session_scope() as session:
                run = session.query(DiscoveryRun).filter(DiscoveryRun.id == run_id).first()
                if run:
                    run.completed_at = datetime.utcnow()
                    run.error_message = str(e)
        except Exception:
            pass

        # Retry with exponential backoff (5 minutes initial delay)
        raise self.retry(exc=e, countdown=300, max_retries=3)


@celery_app.task(bind=True, name='autopackager.check_ring_promotions')
def check_ring_promotions(self):
    """Check and promote deployments to next ring based on success criteria"""
    logger.info("Starting ring promotion check")

    try:
        # Import here to avoid circular dependencies
        from autopackager.agents.deployment import DeploymentAgent

        agent = DeploymentAgent()

        # Check for promotions
        result = agent.check_and_promote_eligible_deployments()

        logger.info(
            "Ring promotion check completed",
            total_checked=result.get('total_checked', 0),
            eligible_count=result.get('eligible_count', 0),
            promoted_count=result.get('promoted_count', 0),
            failed_promotions=result.get('failed_promotions', 0)
        )

        return result

    except Exception as e:
        logger.error("Ring promotion check failed", error=str(e))

        # Retry with exponential backoff (5 minutes initial delay)
        raise self.retry(exc=e, countdown=300, max_retries=3)


@celery_app.task(bind=True, name='autopackager.check_app_versions')
def check_app_versions(self):
    """Daily version check for managed software apps (the scheduled half of the
    demo's 'refresh' brain — spec §2).

    Walks catalog entries that have a known source URL and at least one
    'newest' verified version, asks the version-check bridge whether something
    newer is upstream, and LOGS findings. It is DETECTION-ONLY: it never
    dispatches an upgrade unattended — the actual package/supersede/deploy stays
    operator-gated through the demo UI. Honours ``DEMO_CLAUDE_MODE``.
    """
    logger.info("Starting scheduled app version check")
    try:
        from autopackager.utils.config import get_config
        from autopackager.utils import installer_catalog

        vc_config = get_config().get('version_check_schedule', {}) or {}
        if not vc_config.get('enabled', False):
            logger.info("Scheduled version check is disabled in config")
            return {'status': 'disabled'}

        # Imported lazily: the demo bridge is optional and operator-side only.
        from demo import claude_bridge

        catalog = installer_catalog.load_catalog()
        checked = 0
        newer = []
        for entry in catalog.entries:
            if not entry.canonical_download_url:
                continue
            newest = next(
                (vv for vv in (entry.verified_versions or [])
                 if vv.get('status') == 'newest' and vv.get('product_version')),
                None,
            )
            if not newest:
                continue
            checked += 1
            result = claude_bridge.check_version(
                entry.id, newest.get('product_version'), entry.canonical_download_url,
                slug=entry.id,
            )
            if result.get('is_newer'):
                finding = {
                    'entry_id': entry.id,
                    'deployed_version': newest.get('product_version'),
                    'latest_version': result.get('latest_version'),
                    'download_url': result.get('download_url'),
                }
                newer.append(finding)
                logger.info("Newer version available for managed app", **finding)

        logger.info(
            "Scheduled app version check completed",
            checked=checked, newer_available=len(newer),
        )
        return {'status': 'completed', 'checked': checked, 'newer': newer}

    except Exception as e:
        logger.error("Scheduled app version check failed", error=str(e))
        raise self.retry(exc=e, countdown=300, max_retries=2)
