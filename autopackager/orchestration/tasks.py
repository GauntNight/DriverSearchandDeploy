"""Celery Tasks for Job Processing"""

from celery import chain
from autopackager.orchestration.celery_app import celery_app
from autopackager.orchestration.engine import OrchestrationEngine
from autopackager.models.job import JobState, JobType
from autopackager.utils.logger import get_logger

logger = get_logger(__name__)


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

    try:
        # Import here to avoid circular dependencies
        from autopackager.agents.discovery import DiscoveryAgent

        agent = DiscoveryAgent()
        job = engine.get_job(job_id)

        # Execute discovery
        result = agent.discover(job)

        if result.get('update_available'):
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

    try:
        # Import here to avoid circular dependencies
        from autopackager.agents.packaging import PackagingAgent

        agent = PackagingAgent()
        job = engine.get_job(job_id)

        # Execute packaging
        result = agent.package(job)

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

    try:
        # Import here to avoid circular dependencies
        from autopackager.agents.testing import TestingAgent

        agent = TestingAgent()
        job = engine.get_job(job_id)

        # Execute testing
        result = agent.test(job)

        if result.get('test_passed'):
            logger.info("Testing passed", job_id=job_id)
            return {"job_id": job_id, "test_passed": True}
        else:
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

    logger.info("Starting deployment phase", job_id=job_id)

    engine = OrchestrationEngine()
    engine.update_job_state(job_id, JobState.DEPLOYING)

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
        # Extract the real error from tenacity RetryError
        original = e
        if hasattr(e, 'last_attempt'):
            try:
                original = e.last_attempt.result()
            except Exception as inner:
                original = inner

        error_detail = str(original)
        if hasattr(original, 'response') and original.response is not None:
            try:
                error_detail = f"HTTP {original.response.status_code}: {original.response.json()}"
            except Exception:
                error_detail = f"HTTP {original.response.status_code}: {original.response.text}"

        logger.error("Deployment failed", job_id=job_id, error=error_detail)

        if engine.can_retry_job(job_id):
            retry_count = engine.increment_retry_count(job_id)
            logger.info("Retrying deployment", job_id=job_id, retry_count=retry_count)
            raise self.retry(exc=e, countdown=engine.retry_delay)
        else:
            engine.mark_job_failed(job_id, f"Deployment failed: {error_detail}")
            raise


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
