"""Deployment Agent - Deploy to Microsoft Intune"""

import re
import shutil
import tempfile
import time
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional

from autopackager.models.job import Job
from autopackager.models.package import Package
from autopackager.models.deployment import Deployment, DeploymentStatus
from autopackager.utils.config import get_config
from autopackager.utils.database import db_session_scope
from autopackager.utils.azure_validator import AzureConfigurationError, AzureValidator
from autopackager.utils.graph_client import GraphAPIClient
from autopackager.utils.installer_catalog import CatalogEntry, load_catalog
from autopackager.utils.logger import get_logger
from autopackager.utils.msi_metadata import _detect_image_mime

logger = get_logger(__name__)

# Standard Windows Installer (MsiExec) exit codes. Without these, Intune
# treats anything non-zero as a failed install -- including 3010 ("reboot
# required, install succeeded") and 1641 ("install succeeded, reboot
# initiated"), which spuriously fails clean deployments and triggers the
# rollback heuristic. Reference:
# https://learn.microsoft.com/en-us/troubleshoot/windows-server/application-management/msiexec-command-line-reference
_DEFAULT_RETURN_CODES = [
    {'returnCode': 0,    'type': 'success'},
    {'returnCode': 1707, 'type': 'success'},
    {'returnCode': 3010, 'type': 'softReboot'},
    {'returnCode': 1641, 'type': 'hardReboot'},
    {'returnCode': 1618, 'type': 'retry'},
]

# Map of Windows release identifiers to the win32LobApp
# windowsMinimumOperatingSystem flag name. Graph stores the chosen flag in
# minimumSupportedWindowsRelease on read, but accepts the flag-dict form on
# write -- so we always send the canonical version=True dict.
_WIN_RELEASE_TO_FLAG = {
    '1607': 'v10_1607', '1703': 'v10_1703', '1709': 'v10_1709',
    '1803': 'v10_1803', '1809': 'v10_1809', '1903': 'v10_1903',
    '1909': 'v10_1909', '2004': 'v10_2004', '20H2': 'v10_2H20',
    '21H1': 'v10_21H1', '21H2': 'v10_21H2', '22H2': 'v10_22H2',
}

# Trailer we stamp into the Intune app's notes field so the upsert lookup
# can tell which catalog entry produced the app. Without this, two catalog
# entries that legitimately share a displayName (e.g. an MSI distribution
# and an EXE bootstrapper for the same product line, both showing up as
# "Snagit" in Intune) collide -- the second publish PATCHes the first and
# corrupts its detection/uninstall metadata. The marker lives on its own
# trailing line so it stays visually separable from operator-facing notes.
_CATALOG_MARKER_RE = re.compile(r'\[autopackager:catalog=([^\]\s]+)\]')


def _build_catalog_marker(entry_id: str) -> str:
    return f'[autopackager:catalog={entry_id}]'


def _extract_catalog_marker(notes: Optional[str]) -> Optional[str]:
    if not notes:
        return None
    m = _CATALOG_MARKER_RE.search(notes)
    return m.group(1) if m else None


class DeploymentAgent:
    """Agent responsible for deploying packages to Microsoft Intune"""

    def __init__(self):
        self.config = get_config()
        self.deployment_rings = self.config.get('deployment_rings', [])
        self.graph_client = None

    def _validate_azure_config(self) -> None:
        """Validate Azure/Intune configuration before deployment.

        Runs all Azure validation checks. Configuration, authentication, and
        Graph API access failures are critical and will raise
        AzureConfigurationError. Deployment ring validation failures are
        logged as warnings but do not block deployment.
        """
        validator = AzureValidator()

        results = []
        critical_failures = []

        for check_name, check_fn in [
            ('config', validator.validate_config),
            ('auth', validator.validate_authentication),
            ('graph_access', validator.validate_graph_access),
        ]:
            result = check_fn()
            results.append(result)
            if not result.passed:
                critical_failures.append(result)

        ring_result = validator.validate_deployment_rings()
        results.append(ring_result)
        if not ring_result.passed:
            logger.warning(
                "Deployment ring validation failed (non-blocking)",
                check=ring_result.check_name,
                message=ring_result.message,
            )

        if critical_failures:
            raise AzureConfigurationError(results)

        logger.info("Azure configuration validation passed")

    def _get_graph_client(self) -> GraphAPIClient:
        """Get or create Graph API client"""
        if self.graph_client is None:
            self.graph_client = GraphAPIClient()
        return self.graph_client

    def deploy(self, job: Job) -> Dict[str, Any]:
        """
        Main deployment method - publishes package to Intune and assigns Ring 0
        """
        logger.info("Starting deployment", job_id=job.id)

        self._validate_azure_config()

        package_id = job.job_metadata.get('package_id')
        if not package_id:
            raise ValueError("No package ID in job metadata")

        package = self._get_package(package_id)
        if not package:
            raise ValueError(f"Package {package_id} not found")

        if not package.test_passed:
            raise Exception("Package has not passed testing - cannot deploy")

        # Create/update Intune app, upload content, and publish
        intune_app_id = self._create_or_update_intune_app(package, job)

        # Honour an opt-out for safe test publishes against production tenants:
        # callers can set metadata['no_assignment']=True to skip ring assignment.
        skip_assignment = bool(job.job_metadata.get('no_assignment'))
        if skip_assignment:
            logger.info(
                "Skipping ring assignment (no_assignment flag set)",
                job_id=job.id,
                intune_app_id=intune_app_id,
            )
        else:
            self._assign_to_ring(intune_app_id, package, ring_index=0)

        # Post-publish: verify the published metadata is correct, fix if not.
        try:
            self._verify_published_metadata(intune_app_id, package, job)
        except Exception as exc:  # noqa: BLE001 -- verification is a safety net
            logger.warning("Post-publish metadata verification errored", error=str(exc))

        # Update package deployment status
        self._update_package_deployment_status(package.id, intune_app_id)

        logger.info("Deployment completed", job_id=job.id, intune_app_id=intune_app_id)

        if skip_assignment:
            ring_label = 'unassigned'
        else:
            ring_label = self.deployment_rings[0]['name'] if self.deployment_rings else 'Unknown'

        return {
            'intune_app_id': intune_app_id,
            'status': 'deployed',
            'ring': ring_label,
        }

    def _verify_published_metadata(self, app_id: str, package: Package, job: Job) -> Dict[str, Any]:
        """Post-publish: confirm detection rules landed on the app; fix if not.

        This is the 'verify publishing metadata is correct; fix if not' step of
        the workflow. Detection rules are readable only via
        ``GET /beta/.../mobileApps/{id}`` with NO ``$select`` (v1.0 / $select
        return them null — see KB 04). When the published app carries no
        detection rule but we intended one, re-PATCH the ``win32LobApp`` (the
        existing requirement rules are preserved).
        """
        def emit(text: str, level: str = 'info'):
            try:
                from demo.events import publish_pipeline_event
                publish_pipeline_event(job.id, 'deploying', text, level=level)
            except Exception:
                pass

        graph_client = self._get_graph_client()
        try:
            app = graph_client._beta_get(f"deviceAppManagement/mobileApps/{app_id}")
        except Exception as exc:  # noqa: BLE001
            emit(f"Could not read back published metadata: {exc}", 'warn')
            return {'verified': False, 'fixed': False}

        rules = app.get('rules') or app.get('detectionRules') or []

        def _is_detection(r):
            return (r.get('ruleType') == 'detection') or ('Detection' in (r.get('@odata.type') or ''))

        detection_present = [r for r in rules if _is_detection(r)]
        intended = self._normalize_rules(package.detection_rules) if package.detection_rules else []

        if intended and not detection_present:
            emit("Published app has no detection rule — re-applying the corrected rule…", 'warn')
            try:
                preserved = [r for r in rules if not _is_detection(r)]
                graph_client.update_win32_app(app_id, {
                    '@odata.type': '#microsoft.graph.win32LobApp',
                    'rules': preserved + intended,
                })
                emit("Detection rule re-applied to the published app ✓")
                return {'verified': True, 'fixed': True}
            except Exception as exc:  # noqa: BLE001
                emit(f"Failed to fix published detection rule: {exc}", 'error')
                return {'verified': False, 'fixed': False}

        version = app.get('displayVersion') or package.version or '?'
        emit(f"Published metadata verified — detection rule present, version {version} ✓")
        return {'verified': bool(detection_present), 'fixed': False}

    # ---------------------------------------------------------------------------
    # App create / update / delete
    # ---------------------------------------------------------------------------

    def _create_or_update_intune_app(self, package: Package, job: Job) -> str:
        """
        Ensure a clean, published Win32 app exists in Intune for this package.

        Strategy for existing apps:
          - publishingState == 'published'  → update metadata then re-upload content
          - any other state (notPublished, processing, …) → delete the broken shell
            and create from scratch so we start with a clean object
        """
        logger.info("Creating/updating Intune app", package_id=package.id)

        graph_client = self._get_graph_client()

        # When the operator requested supersedence at create-software-job
        # time, this publish MUST land as a NEW Intune app -- not a PATCH
        # of the existing displayName match. Intune supersedence is a
        # link BETWEEN apps; PATCHing the existing app gives us nothing
        # to link to (the old app id would equal the new one). Skipping
        # the displayName lookup forces a clean create_win32_app() and
        # supersedingApps lands on the new id pointing at the prior
        # version's id.
        supersedence_requested = bool((job.job_metadata or {}).get('supersedence_action'))

        existing_app = None
        if not supersedence_requested:
            existing_apps = graph_client.get_win32_apps().get('value', [])
            existing_app = self._find_existing_app_for_upsert(package, existing_apps)

        app_data = self._prepare_app_data(package, job)

        if existing_app:
            app_id = existing_app['id']
            publishing_state = existing_app.get('publishingState', 'notPublished')
            logger.info(
                "Found existing app",
                app_id=app_id,
                publishing_state=publishing_state,
            )

            if publishing_state == 'published':
                # Safe to PATCH metadata on a published app
                logger.info("Updating metadata on published app", app_id=app_id)
                graph_client.update_win32_app(app_id, app_data)
            else:
                # App is in a broken / incomplete state — delete and start fresh
                logger.warning(
                    "App is not published — deleting and recreating",
                    app_id=app_id,
                    publishing_state=publishing_state,
                )
                graph_client.delete_win32_app(app_id)
                # Brief pause to let the delete propagate before creating the new app
                time.sleep(3)
                new_app = graph_client.create_win32_app(app_data)
                app_id = new_app['id']
                logger.info("Recreated app", new_app_id=app_id)
        else:
            logger.info("Creating new app", name=package.name)
            new_app = graph_client.create_win32_app(app_data)
            app_id = new_app['id']

        # Attach Intune categories from the catalog (best-effort -- failures
        # don't block content upload). Categories are a separate $ref
        # sub-collection on the app, not a property of the win32LobApp
        # payload, so they need their own POSTs.
        catalog_entry = self._lookup_catalog_entry(package)
        if catalog_entry and catalog_entry.categories:
            self._assign_categories(graph_client, app_id, catalog_entry.categories)

        # Upload .intunewin content and flip publishingState to 'published'.
        # MUST run before supersedence: Intune's updateRelationships action
        # rejects calls against an app whose PublishingState is not
        # 'Published' ("Invalid operation: app's PublishingState is not
        # 'Published'", verified against the ngbg tenant 2026-06-01).
        self._upload_and_publish(graph_client, app_id, package)

        # Beta-only field: displayVersion populates the Intune portal's
        # "App Version" column. v1.0 POST/PATCH silently drops the field
        # (no error, just doesn't persist), and v1.0 GET returns None even
        # when the field is set via beta. Must be PATCHed AFTER
        # _upload_and_publish: against an app still in 'notPublished' state
        # the beta PATCH returns 200 but the value never lands (verified
        # against the ngbg tenant 2026-06-01, app id 76468611...).
        if package.version:
            try:
                import requests as _requests
                _requests.patch(
                    f'{graph_client.graph_endpoint}/beta/deviceAppManagement/mobileApps/{app_id}',
                    headers=graph_client._get_headers(),
                    json={
                        '@odata.type': '#microsoft.graph.win32LobApp',
                        'displayVersion': package.version,
                    },
                    timeout=30,
                )
                logger.info("displayVersion set via beta", app_id=app_id, version=package.version)
            except Exception as exc:  # noqa: BLE001 -- portal cosmetic; don't fail publish
                logger.warning("Could not set displayVersion via beta", error=str(exc))

        # Apply supersedence (operator opted in via --supersede or
        # --supersedes at create-software-job time). The CLI resolved the
        # action against the catalog snapshot; we just execute it. Two
        # operations:
        #   1. POST /mobileApps/{id}/updateRelationships with a
        #      mobileAppSupersedence per app being superseded.
        #   2. Demote the matching verified_versions rows to
        #      status='superseded' in the local overlay so the catalog
        #      reflects the new chain state.
        self._apply_supersedence(graph_client, app_id, job)

        # Record the publish on the catalog overlay as a 'pending'
        # verified_versions row. This lets the NEXT publish's
        # resolve_supersedence find this app id in the catalog -- before
        # the device has actually installed it. The polling hook later
        # promotes 'pending' -> 'newest' / 'manual' / 'historical' once
        # installed_count > 0 on a real device.
        if catalog_entry and package.version:
            try:
                from autopackager.utils.installer_catalog import record_publish
                record_publish(catalog_entry.id, package.version, app_id)
            except Exception as rec_exc:  # noqa: BLE001
                logger.warning(
                    "Failed to record catalog publish",
                    entry_id=catalog_entry.id, error=str(rec_exc),
                )

        return app_id

    def _apply_supersedence(self, graph_client, new_app_id: str, job: Job) -> None:
        """Execute the supersedence action the CLI resolved (if any).

        Reads ``job.job_metadata['supersedence_action']`` (dict produced by
        ``resolve_supersedence``). When present, POSTs each
        ``mobileAppSupersedence`` relationship via the Graph
        ``updateRelationships`` action and then commits the status
        demotions to the local catalog overlay. Failures inside the
        relationship POST log + raise -- supersedence is a load-bearing
        operator intent and silently dropping it would be worse than
        failing the publish.
        """
        action = (job.job_metadata or {}).get('supersedence_action')
        if not action:
            return
        superseded_ids = action.get('superseded_intune_app_ids') or []
        if not superseded_ids:
            logger.info("Supersedence action has no Intune app ids to link", app_id=new_app_id)
        else:
            relationships = [
                {
                    '@odata.type': '#microsoft.graph.mobileAppSupersedence',
                    'targetId': sid,
                    'supersedenceType': 'update',
                }
                for sid in superseded_ids
            ]
            # The Intune updateRelationships action is exposed only on the
            # beta endpoint -- POST to /v1.0/.../updateRelationships
            # returns "Resource not found for the segment
            # 'updateRelationships'". Verified against the ngbg tenant
            # 2026-06-01.
            try:
                graph_client._beta_post(
                    f'deviceAppManagement/mobileApps/{new_app_id}/updateRelationships',
                    data={'relationships': relationships},
                )
                logger.info(
                    "Recorded supersedence relationships",
                    new_app_id=new_app_id,
                    superseded_count=len(superseded_ids),
                    superseded_app_ids=superseded_ids,
                )
            except Exception as exc:  # noqa: BLE001 -- bubble up; operator wants to know
                logger.error(
                    "Failed to record supersedence relationships",
                    new_app_id=new_app_id,
                    error=str(exc),
                )
                raise

        # Catalog overlay demotions. Idempotent at row level.
        demoted = action.get('demoted_records') or []
        if demoted:
            try:
                self._commit_supersedence_status(demoted)
            except Exception as exc:  # noqa: BLE001 -- catalog drift, not a publish failure
                logger.warning(
                    "Could not commit supersedence status to overlay",
                    error=str(exc),
                )

    @staticmethod
    def _commit_supersedence_status(demoted_records: list) -> None:
        """Mark the named (entry_id, intune_app_id) rows as superseded in
        the local overlay. Mirror of
        installer_catalog.apply_supersedence_status, fed by the dict-shaped
        ``demoted_records`` we serialise into job_metadata (the original
        SupersedenceResolution doesn't survive Celery serialisation as a
        dataclass).
        """
        from autopackager.utils.installer_catalog import (
            _local_overlay_entries, _write_local,
        )
        target_keys = {
            (r['entry_id'], r.get('product_version'), r.get('verified_intune_app_id'))
            for r in demoted_records
        }
        overlay = _local_overlay_entries()
        changed = 0
        for entry in overlay:
            for vv in entry.verified_versions or []:
                key = (entry.id, vv.get('product_version'), vv.get('verified_intune_app_id'))
                if key in target_keys and vv.get('status') != 'superseded':
                    vv['status'] = 'superseded'
                    changed += 1
        if changed:
            _write_local(overlay)
            logger.info("Catalog supersedence status committed", rows=changed)

    def _assign_categories(self, graph_client, app_id: str, category_names: list) -> None:
        """Attach Intune mobileAppCategory entries to ``app_id`` by display name.

        Unknown category names log a warning and are skipped (we don't auto-
        create custom categories -- operator can pre-create them in the
        portal). Already-attached categories return 409 and are logged at
        debug; that's the expected idempotent path on a re-publish.
        """
        if not category_names:
            return
        try:
            existing = graph_client.get('deviceAppManagement/mobileAppCategories')
        except Exception as exc:  # noqa: BLE001 -- read failure shouldn't block publish
            logger.warning("Could not fetch Intune categories", error=str(exc))
            return
        by_name = {c.get('displayName'): c.get('id') for c in existing.get('value', [])}
        for name in category_names:
            cat_id = by_name.get(name)
            if not cat_id:
                logger.warning("Skipping unknown Intune category", category=name, app_id=app_id)
                continue
            ref_body = {
                '@odata.id': (
                    f'https://graph.microsoft.com/v1.0/deviceAppManagement/'
                    f'mobileAppCategories/{cat_id}'
                )
            }
            try:
                graph_client.post(
                    f'deviceAppManagement/mobileApps/{app_id}/categories/$ref',
                    data=ref_body,
                )
                logger.info("Category attached", category=name, app_id=app_id)
            except Exception as exc:  # noqa: BLE001 -- 409 already-attached is benign
                logger.debug(
                    "Category attach skipped (already present or error)",
                    category=name, app_id=app_id, error=str(exc),
                )

    def _find_existing_app_for_upsert(
        self,
        package: Package,
        existing_apps: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """Return the Intune app this publish should PATCH, or None to create fresh.

        When a catalog entry produced this package, require its id to match
        the marker stamped into the candidate app's notes field. Two catalog
        entries can legitimately share a displayName (an MSI distribution and
        an EXE bootstrapper for the same product line both surface as e.g.
        "Snagit" in Intune) -- without the marker check the second publish
        PATCHes the first, leaving an app whose installer/displayVersion belong
        to one entry but whose detection rule and uninstall command belong to
        the other. Refuse to match in the absence of a marker rather than
        silently fall back to displayName-only, which is the original bug.

        For driver jobs (no catalog entry) and operator overrides that opt out
        of the catalog, keep the prior displayName-only behavior so legitimate
        re-publishes of the same product update the existing app in place.
        """
        name_matches = [
            app for app in existing_apps
            if app.get('displayName') == package.name
        ]
        if not name_matches:
            return None

        catalog_entry = self._lookup_catalog_entry(package)
        target_id = catalog_entry.id if catalog_entry else None

        if target_id:
            for app in name_matches:
                if _extract_catalog_marker(app.get('notes')) == target_id:
                    return app
            return None

        return name_matches[0]

    def _lookup_catalog_entry(self, package: Package) -> 'CatalogEntry | None':
        """Find the catalog entry that matches this package.

        Two paths:
          * MSI packages: lookup by msi_product_code (existing behaviour).
          * EXE packages: lookup by catalog_entry_id captured at
            create-software-job time. EXE packages always go through the
            catalog (the CLI refuses to enqueue an EXE without one) so the
            id is guaranteed to be in package_metadata when it's an EXE.

        Returns None for packages with neither identifier or when the
        catalog itself can't be loaded. Failure is non-fatal -- the rest of
        _prepare_app_data tolerates a missing catalog entry and falls back
        to package_metadata / hardcoded defaults.
        """
        pkg_meta = (package.package_metadata or {}) if hasattr(package, 'package_metadata') else {}
        if not isinstance(pkg_meta, dict):
            return None
        try:
            catalog = load_catalog()
        except Exception as exc:  # noqa: BLE001 -- catalog is opportunistic
            logger.debug("Catalog load skipped", error=str(exc))
            return None

        # MSI: cascade through UpgradeCode -> ProductCode -> name/publisher.
        # ProductCode alone is too tight: each released version of a product
        # gets its own ProductCode, so a fresh version (e.g. 7.6.2 with a
        # new GUID) misses the catalog entry that was registered against
        # 7.6.1's GUID. UpgradeCode is the stable per-product identifier
        # MSI guarantees across versions, which is exactly what supersedence
        # needs to find the catalog row that owns this product line.
        #
        # IMPORTANT: only run the MSI cascade when the package actually
        # carries an MSI-specific identifier (ProductCode or UpgradeCode).
        # Without this gate, an EXE package whose package_metadata has only
        # a catalog_entry_id would fall through to match_msi's name fallback
        # via package.name / package.vendor and silently land on an MSI
        # catalog entry whose product_name_pattern overlaps -- e.g. a
        # SnagitSetup-2023 EXE publish (package.name "Snagit 2023") matched
        # the snagit-2023 MSI entry instead of snagit-2023-bootstrapper,
        # which then made the deployment marker mis-stamp and clobber the
        # MSI app (caught live 2026-06-02).
        has_msi_id = bool(pkg_meta.get('msi_product_code') or pkg_meta.get('msi_upgrade_code'))
        if has_msi_id:
            msi_meta_for_match = {
                'product_code': pkg_meta.get('msi_product_code'),
                'upgrade_code': pkg_meta.get('msi_upgrade_code'),
                'product_name': pkg_meta.get('msi_product_name') or package.name,
                'manufacturer': pkg_meta.get('msi_manufacturer') or package.vendor,
            }
            entry = catalog.match_msi(msi_meta_for_match)
            if entry:
                return entry

        # EXE: lookup by the catalog id captured at job-creation time. The
        # id is more stable than re-matching by SHA-256 / PE name because
        # the operator may have updated the catalog entry between create
        # and publish.
        catalog_entry_id = pkg_meta.get('catalog_entry_id')
        if catalog_entry_id:
            return catalog.by_id(catalog_entry_id)

        return None

    def _prepare_app_data(self, package: Package, job: Job) -> Dict[str, Any]:
        """Prepare Intune app data structure (Graph API v1.0 schema)."""
        rules = self._normalize_rules(package.detection_rules)
        catalog_entry = self._lookup_catalog_entry(package)

        # setupFilePath is required by Graph API — the installer filename inside
        # the .intunewin package.  Derive from the stored installer_path, or fall
        # back to the first token of the install command (the executable name).
        if package.installer_path:
            setup_file = Path(package.installer_path).name
        else:
            setup_file = package.install_command.split()[0] if package.install_command else Path(package.intunewin_path).stem

        vendor = job.vendor or package.vendor or 'Unknown'
        version = package.version or job.target_version or 'Unknown'
        hardware_model = job.hardware_model or ''
        description = f"{package.name} v{version} - {vendor}"
        if hardware_model:
            description = f"{package.name} v{version} for {hardware_model} - {vendor}"

        # Sources for the Intune app attributes, in priority order:
        #   1. catalog override (curated, env-agnostic, ships in baseline YAML)
        #   2. MSI metadata captured at packaging time (msi_help_link / msi_subject / msi_icon_b64)
        #   3. driver/vendor defaults (informationUrl from Dell/HP/Lenovo URL map)
        #   4. None (field omitted from payload)
        pkg_meta = (package.package_metadata or {}) if hasattr(package, 'package_metadata') else {}
        if not isinstance(pkg_meta, dict):
            pkg_meta = {}

        # informationUrl: catalog override -> MSI ARPHELPLINK -> driver vendor URL
        information_url = (
            (catalog_entry.information_url if catalog_entry else None)
            or pkg_meta.get('msi_help_link')
            or self._get_vendor_support_url(vendor, hardware_model)
        )

        # notes: build a per-job summary first (driver releases / hardware
        # context), then prepend the catalog or MSI's app description when
        # present so the most important context is at the top of the field.
        notes_parts = []
        catalog_description = catalog_entry.description if catalog_entry else None
        app_description = catalog_description or pkg_meta.get('msi_subject')
        if app_description:
            notes_parts.append(app_description)
        release_date = job.job_metadata.get('release_date', '')
        release_notes = job.job_metadata.get('release_notes') or job.release_notes or ''
        if release_date:
            notes_parts.append(f"Release date: {release_date}")
        if release_notes:
            notes_parts.append(release_notes)
        if job.job_type and job.job_type.value == 'driver_update' and hardware_model:
            notes_parts.append(f"Hardware model: {hardware_model}")
        # Catalog marker trailer (see _CATALOG_MARKER_RE). Only stamped when
        # the source job resolved to a catalog entry so the upsert lookup can
        # disambiguate MSI vs EXE distributions sharing a displayName.
        if catalog_entry and catalog_entry.id:
            notes_parts.append(_build_catalog_marker(catalog_entry.id))
        notes = '\n'.join(notes_parts) if notes_parts else ''

        # minimumSupportedOperatingSystem: Graph accepts the flag-dict form on
        # write but exposes the chosen value as the string
        # ``minimumSupportedWindowsRelease`` on read. Default to 1607 (the
        # oldest still-supported Windows 10 release that won't filter any
        # modern device out via NotApplicable).
        min_os = (catalog_entry.min_os_version if catalog_entry else None) or '1607'
        min_os_flag = _WIN_RELEASE_TO_FLAG.get(min_os, 'v10_1607')

        app_data = {
            '@odata.type': '#microsoft.graph.win32LobApp',
            'displayName': package.name,
            'description': description,
            'publisher': vendor,
            'developer': vendor,
            'owner': vendor,
            'fileName': Path(package.intunewin_path).name,
            'setupFilePath': setup_file,
            'installCommandLine': package.install_command,
            'uninstallCommandLine': package.uninstall_command or 'cmd /c exit 0',
            'installExperience': {
                'runAsAccount': 'system',
                'deviceRestartBehavior': 'suppress'
            },
            'rules': rules,
            'returnCodes': list(_DEFAULT_RETURN_CODES),
            'minimumSupportedOperatingSystem': {
                min_os_flag: True
            }
        }

        # Only include optional fields when they have values.
        # NOTE on `displayVersion`: Win32LobApp's `displayVersion` field is
        # silently dropped by Graph for MSI-derived apps -- the Intune portal's
        # "Version" column is populated from `msiInformation.productVersion`,
        # not from `displayVersion`. We still set displayVersion so the field
        # is correct on EXE/script Win32 apps (where it does flow through);
        # for MSI apps it's a harmless no-op and the msiInformation block
        # below is what actually surfaces in the portal.
        if version and version != 'Unknown':
            app_data['displayVersion'] = version
        if information_url:
            app_data['informationUrl'] = information_url
        if notes:
            app_data['notes'] = notes

        # largeIcon: catalog override (operator-supplied for PE-icon MSIs like
        # Slack) wins; otherwise use the icon PackagingAgent extracted from
        # the MSI. Graph expects ``{type: mime, value: base64}``.
        catalog_icon_b64 = catalog_entry.icon_b64 if catalog_entry else None
        icon_b64 = catalog_icon_b64 or pkg_meta.get('msi_icon_b64')
        icon_mime = pkg_meta.get('msi_icon_mime')
        if catalog_icon_b64:
            # Catalog-supplied icon: sniff the mime from its decoded bytes so
            # we don't trust an out-of-date mime hint paired with new bytes.
            try:
                import base64 as _b64
                icon_mime = _detect_image_mime(_b64.b64decode(catalog_icon_b64))
            except Exception:  # noqa: BLE001
                icon_mime = None
        if icon_b64 and icon_mime:
            app_data['largeIcon'] = {'type': icon_mime, 'value': icon_b64}

        # MSI-derived Win32 apps: populate msiInformation so the Intune portal
        # shows Version / Publisher / ProductCode etc. on the app row. Without
        # this block, every MSI app we publish shows blank Version in Intune
        # (the version is only visible inside the Description text). Source
        # data is captured by PackagingAgent into package.package_metadata when
        # the installer is an MSI (see PackagingAgent.package() msi_* keys).
        msi_product_code = pkg_meta.get('msi_product_code')
        msi_product_version = pkg_meta.get('msi_product_version')
        if msi_product_code and msi_product_version:
            # Per-user vs per-machine: MSIINSTALLPERUSER=1 in the install
            # command's public properties forces per-user; absent it the MSI
            # installs per-machine (which is also what AutoPackager generates
            # by default for Intune Win32 apps).
            install_lower = (package.install_command or '').lower()
            per_user = 'msiinstallperuser=1' in install_lower
            app_data['msiInformation'] = {
                'productCode': msi_product_code,
                'productVersion': msi_product_version,
                'upgradeCode': pkg_meta.get('msi_upgrade_code') or msi_product_code,
                'requiresReboot': False,
                'packageType': 'perUser' if per_user else 'perMachine',
                'productName': package.name,
                'publisher': vendor,
            }

        return app_data

    @staticmethod
    def _get_vendor_support_url(vendor: str, hardware_model: str = '') -> str:
        """Return a vendor-specific support URL. Deterministic — no LLM needed."""
        vendor_lower = (vendor or '').lower()
        if vendor_lower == 'dell':
            return 'https://www.dell.com/support/home'
        elif vendor_lower == 'hp':
            return 'https://support.hp.com/drivers'
        elif vendor_lower == 'lenovo':
            return 'https://support.lenovo.com/solutions/ht003029'
        return ''

    def _normalize_rules(self, rules: list) -> list:
        """Convert legacy beta-schema detection rules to Graph API v1.0 format."""
        if not rules:
            return []
        normalized = []
        for rule in rules:
            r = dict(rule)
            odata = r.get('@odata.type', '')
            if 'RegistryDetection' in odata:
                r['@odata.type'] = '#microsoft.graph.win32LobAppRegistryRule'
                r.setdefault('ruleType', 'detection')
                if 'detectionType' in r:
                    r['operationType'] = r.pop('detectionType')
                if 'detectionValue' in r:
                    r['comparisonValue'] = r.pop('detectionValue')
            r.setdefault('ruleType', 'detection')
            normalized.append(r)
        return normalized

    # ---------------------------------------------------------------------------
    # Content upload and publish
    # ---------------------------------------------------------------------------

    def _upload_and_publish(self, graph_client: GraphAPIClient, app_id: str, package: Package):
        """
        Full Win32 content publish flow:
          1. Parse .intunewin to extract encrypted binary + encryption metadata
          2. Create a content version
          3. Create a file entry → get Azure Blob SAS URI
          4. Upload encrypted binary in chunks
          5. Commit the file (provide encryption info to Intune)
          6. PATCH app with committedContentVersion → publishingState becomes 'published'
        """
        intunewin_path = package.intunewin_path

        if not Path(intunewin_path).exists():
            raise Exception(f".intunewin file not found: {intunewin_path}")

        if Path(intunewin_path).stat().st_size == 0:
            raise Exception(
                f".intunewin file is empty (was IntuneWinAppUtil.exe present during packaging?): {intunewin_path}"
            )

        content_info = self._parse_intunewin(intunewin_path)
        encrypted_path = content_info['encrypted_path']

        try:
            logger.info("Starting content upload", app_id=app_id)

            # Step 1 – create content version
            version = graph_client.create_content_version(app_id)
            version_id = version['id']
            logger.info("Content version created", version_id=version_id)

            # Step 2 – create file entry (triggers Intune to provision Azure Storage URI)
            file_entry = graph_client.create_content_file(
                app_id,
                version_id,
                file_name=Path(intunewin_path).stem + ".intunewin",
                unencrypted_size=content_info['unencrypted_size'],
                encrypted_size=content_info['encrypted_size'],
            )
            file_id = file_entry['id']
            logger.info("Content file entry created", file_id=file_id)

            # Step 3 – wait for Azure Storage SAS URI
            azure_uri = graph_client.wait_for_azure_storage_uri(app_id, version_id, file_id)

            # Step 4 – upload encrypted binary to Azure Blob Storage
            graph_client.upload_to_azure_storage(azure_uri, encrypted_path)

            # Step 5 – commit file with encryption metadata
            graph_client.commit_content_file(
                app_id, version_id, file_id,
                content_info['encryption_info']
            )
            graph_client.wait_for_file_commit(app_id, version_id, file_id)

            # Step 6 – commit version to app → triggers publishingState = 'published'
            graph_client.commit_content_version(app_id, version_id)
            logger.info("App published successfully", app_id=app_id, version_id=version_id)

        finally:
            # Clean up the temp extracted file
            if encrypted_path and Path(encrypted_path).exists():
                try:
                    Path(encrypted_path).unlink()
                except Exception:
                    pass

    def _parse_intunewin(self, intunewin_path: str) -> Dict[str, Any]:
        """
        Extract the encrypted content binary and encryption metadata from a .intunewin file.

        .intunewin is a ZIP containing:
          - IntunePackage.intunewin  (AES-256-CBC encrypted installer)
          - Detection.xml            (file sizes + key/IV/MAC metadata)
        """
        tmp_path = None
        try:
            with zipfile.ZipFile(intunewin_path, 'r') as zf:
                names = zf.namelist()

                # Locate encrypted content file
                content_entry = next(
                    (n for n in names if n.lower().endswith('intunepackage.intunewin')),
                    None
                )
                if not content_entry:
                    raise Exception(
                        f"IntunePackage.intunewin not found inside {intunewin_path}. "
                        f"Found: {names}"
                    )

                # Locate Detection.xml
                detection_entry = next(
                    (n for n in names if n.lower().endswith('detection.xml')),
                    None
                )
                if not detection_entry:
                    raise Exception(
                        f"Detection.xml not found inside {intunewin_path}. "
                        f"Found: {names}"
                    )

                # Parse Detection.xml for sizes and encryption metadata
                with zf.open(detection_entry) as xml_fh:
                    tree = ET.parse(xml_fh)
                root = tree.getroot()

                unencrypted_size = int(root.findtext('UnencryptedContentSize', '0'))
                encrypted_size_xml = int(root.findtext('EncryptedContentSize', '0'))

                enc_node = root.find('EncryptionInfo')
                if enc_node is None:
                    raise Exception("EncryptionInfo element missing from Detection.xml")

                encryption_info = {
                    'encryptionKey':        enc_node.findtext('EncryptionKey'),
                    'macKey':               enc_node.findtext('MacKey'),
                    'initializationVector': enc_node.findtext('InitializationVector'),
                    'mac':                  enc_node.findtext('Mac'),
                    'profileIdentifier':    enc_node.findtext('ProfileIdentifier', 'ProfileVersion1'),
                    'fileDigest':           enc_node.findtext('FileDigest'),
                    'fileDigestAlgorithm':  enc_node.findtext('FileDigestAlgorithm', 'SHA256'),
                }

                # Extract the encrypted binary to a temp file for uploading
                tmp_fd, tmp_path = tempfile.mkstemp(suffix='.intunewin')
                import os
                os.close(tmp_fd)

                with zf.open(content_entry) as src, open(tmp_path, 'wb') as dst:
                    shutil.copyfileobj(src, dst)

            encrypted_size_actual = Path(tmp_path).stat().st_size

            return {
                'encrypted_path':  tmp_path,
                'unencrypted_size': unencrypted_size,
                # Prefer size from the file itself; fall back to XML value
                'encrypted_size':  encrypted_size_actual or encrypted_size_xml,
                'encryption_info': encryption_info,
            }

        except Exception:
            # Clean up temp file if parsing fails
            if tmp_path and Path(tmp_path).exists():
                try:
                    Path(tmp_path).unlink()
                except Exception:
                    pass
            raise

    # ---------------------------------------------------------------------------
    # Ring assignment and deployment records
    # ---------------------------------------------------------------------------

    def _assign_to_ring(self, intune_app_id: str, package: Package, ring_index: int = 0):
        """Assign app to deployment ring"""
        if ring_index >= len(self.deployment_rings):
            logger.error("Invalid ring index", ring_index=ring_index)
            return

        ring = self.deployment_rings[ring_index]
        logger.info("Assigning to ring", ring_name=ring['name'], group_id=ring['entra_group_id'])

        graph_client = self._get_graph_client()

        try:
            graph_client.assign_app_to_group(
                intune_app_id,
                ring['entra_group_id'],
                intent='required'
            )
            self._create_deployment_record(package.id, intune_app_id, ring)

        except Exception as e:
            logger.error("Failed to assign to ring", error=str(e))
            raise

    def remove_app_assignment(self, intune_app_id: str, group_id: str):
        """Remove failed Intune app assignment from Entra group"""
        logger.info("Removing app assignment", app_id=intune_app_id, group_id=group_id)

        graph_client = self._get_graph_client()

        try:
            graph_client.remove_app_assignment(intune_app_id, group_id)
            logger.info("App assignment removed successfully", app_id=intune_app_id, group_id=group_id)

        except Exception as e:
            logger.error("Failed to remove app assignment", app_id=intune_app_id, group_id=group_id, error=str(e))
            raise

    def _create_supersedence(
        self,
        graph_client: GraphAPIClient,
        existing_app_id: str,
        new_package: Package
    ):
        """Create supersedence relationship between old and new versions"""
        logger.info("Creating supersedence relationship", old_app_id=existing_app_id)
        # TODO: Implement supersedence using Graph API
        logger.warning("Supersedence creation not fully implemented")

    def _create_deployment_record(self, package_id: int, intune_app_id: str, ring: Dict):
        """Create deployment tracking record"""
        with db_session_scope() as session:
            deployment = Deployment(
                package_id=package_id,
                intune_app_id=intune_app_id,
                ring_id=ring['ring_id'],
                ring_name=ring['name'],
                entra_group_id=ring['entra_group_id'],
                status=DeploymentStatus.IN_PROGRESS,
                deployed_at=datetime.utcnow()
            )
            session.add(deployment)
            logger.info("Created deployment record", ring=ring['name'])

    def _update_package_deployment_status(self, package_id: int, intune_app_id: str):
        """Update package deployment status"""
        with db_session_scope() as session:
            package = session.query(Package).filter(Package.id == package_id).first()
            if package:
                package.deployed = True
                package.intune_app_id = intune_app_id
                logger.info("Updated package deployment status", package_id=package_id)

    def _record_catalog_verification(self, intune_app_id: str) -> None:
        """Look the install up in the installer catalog by ProductCode and
        record this deployment as a proven-good version. Idempotent.

        Only Win32 apps that originated as MSI software jobs can be located
        (the lookup is by ProductCode stored on the Package). Driver and
        catalog-miss EXE deployments are silently skipped.
        """
        # Import lazily to avoid pulling the catalog module into every
        # deployment-related code path that imports DeploymentAgent.
        from autopackager.utils import installer_catalog

        with db_session_scope() as session:
            deployment = (
                session.query(Deployment)
                .filter(Deployment.intune_app_id == intune_app_id)
                .first()
            )
            if not deployment or not deployment.package_id:
                return
            package = (
                session.query(Package)
                .filter(Package.id == deployment.package_id)
                .first()
            )
            if not package:
                return
            metadata = package.package_metadata or {}
            product_code = metadata.get("msi_product_code")
            product_version = metadata.get("msi_product_version") or package.version

        if not product_code:
            return

        catalog = installer_catalog.load_catalog()
        entry = catalog.match_by_product_code(product_code)
        if not entry:
            return

        installer_catalog.record_verification(
            entry_id=entry.id,
            product_version=product_version,
            intune_app_id=intune_app_id,
        )

    def _get_package(self, package_id: int) -> Package:
        """Get package by ID"""
        with db_session_scope() as session:
            package = session.query(Package).filter(Package.id == package_id).first()
            if package:
                session.expunge(package)
            return package

    def get_previous_package(self, package_id: int) -> Package:
        """Find the last known-good package version for rollback.

        Searches for the most recent package with the same name that was
        successfully deployed and tested. Returns None if no previous
        known-good package exists.

        Args:
            package_id: ID of the current/failed package

        Returns:
            Previous known-good Package, or None if not found
        """
        current_package = self._get_package(package_id)
        if not current_package:
            logger.warning("Current package not found", package_id=package_id)
            return None

        with db_session_scope() as session:
            previous_package = (
                session.query(Package)
                .filter(
                    Package.name == current_package.name,
                    Package.id != current_package.id,
                    Package.deployed == True,
                    Package.test_passed == True,
                    Package.created_at < current_package.created_at
                )
                .order_by(Package.created_at.desc())
                .first()
            )

            if previous_package:
                session.expunge(previous_package)
                logger.info(
                    "Found previous package for rollback",
                    current_package_id=package_id,
                    previous_package_id=previous_package.id,
                    previous_version=previous_package.version
                )
            else:
                logger.warning(
                    "No previous known-good package found",
                    package_id=package_id,
                    package_name=current_package.name
                )

            return previous_package

    # ---------------------------------------------------------------------------
    # Ring promotion (future)
    # ---------------------------------------------------------------------------

    def is_eligible_for_promotion(self, deployment: Deployment) -> tuple[bool, str]:
        """Check if a deployment is eligible for promotion to the next ring.

        Eligibility criteria:
        1. Dwell time has elapsed since deployed_at (evaluation_period_hours)
        2. Success rate meets or exceeds success_threshold_percent
        3. Minimum install count has been reached
        4. Not already at the final ring
        5. Promotion is not manually blocked

        Args:
            deployment: The Deployment object to check

        Returns:
            tuple: (is_eligible: bool, reason: str)
                - is_eligible: True if eligible for promotion, False otherwise
                - reason: Human-readable explanation of eligibility status
        """
        # Get promotion configuration
        promotion_config = self.config.get('ring_promotion', {})

        if not promotion_config.get('enabled', False):
            return False, "Ring promotion is disabled in configuration"

        # Check if manually blocked
        if deployment.promotion_blocked_reason:
            return False, f"Promotion manually blocked: {deployment.promotion_blocked_reason}"

        # Check if already at final ring
        current_ring_index = None
        for idx, ring in enumerate(self.deployment_rings):
            if ring['ring_id'] == deployment.ring_id:
                current_ring_index = idx
                break

        if current_ring_index is None:
            return False, f"Unknown ring_id: {deployment.ring_id}"

        if current_ring_index >= len(self.deployment_rings) - 1:
            return False, "Already at final ring"

        # Check if deployment is in progress
        if deployment.status != DeploymentStatus.IN_PROGRESS:
            return False, f"Deployment status is {deployment.status.value}, not IN_PROGRESS"

        # Check if deployed_at is set
        if not deployment.deployed_at:
            return False, "Deployment has no deployed_at timestamp"

        # Check dwell time
        evaluation_period_hours = promotion_config.get('evaluation_period_hours', 48)
        hours_since_deployment = (datetime.utcnow() - deployment.deployed_at).total_seconds() / 3600

        if hours_since_deployment < evaluation_period_hours:
            hours_remaining = evaluation_period_hours - hours_since_deployment
            return False, f"Dwell time not met: {hours_remaining:.1f} hours remaining"

        # Check minimum install count
        minimum_install_count = promotion_config.get('minimum_install_count', 10)
        total_installs = deployment.successful_installs + deployment.failed_installs

        if total_installs < minimum_install_count:
            return False, f"Minimum install count not met: {total_installs}/{minimum_install_count}"

        # Calculate success rate
        if total_installs == 0:
            return False, "No install attempts recorded yet"

        success_rate = (deployment.successful_installs / total_installs) * 100
        success_threshold = promotion_config.get('success_threshold_percent', 90.0)

        if success_rate < success_threshold:
            return False, f"Success rate {success_rate:.1f}% below threshold {success_threshold}%"

        # All criteria met
        next_ring = self.deployment_rings[current_ring_index + 1]
        return True, f"Eligible for promotion to {next_ring['name']} (success rate: {success_rate:.1f}%)"

    def promote_to_next_ring(self, deployment_id: int) -> Dict[str, Any]:
        """Promote deployment to next ring.

        Validates eligibility criteria, assigns the app to the next ring's
        Entra group, and creates a new deployment record for tracking.

        Args:
            deployment_id: ID of the deployment to promote

        Returns:
            Dict with promotion details: deployment_id, from_ring, to_ring, status

        Raises:
            ValueError: If deployment not found or ineligible for promotion
            Exception: If assignment to next ring fails
        """
        logger.info("Promoting deployment to next ring", deployment_id=deployment_id)

        # Get deployment record
        with db_session_scope() as session:
            deployment = session.query(Deployment).filter(Deployment.id == deployment_id).first()
            if not deployment:
                raise ValueError(f"Deployment {deployment_id} not found")

            package_id = deployment.package_id
            intune_app_id = deployment.intune_app_id
            current_ring_id = deployment.ring_id

        # Check eligibility for promotion
        eligible, reason = self.is_eligible_for_promotion(deployment)
        if not eligible:
            logger.warning(
                "Deployment not eligible for promotion",
                deployment_id=deployment_id,
                reason=reason
            )
            raise ValueError(f"Deployment not eligible for promotion: {reason}")

        # Find current ring index
        current_ring_index = None
        for idx, ring in enumerate(self.deployment_rings):
            if ring['ring_id'] == current_ring_id:
                current_ring_index = idx
                break

        if current_ring_index is None:
            raise ValueError(f"Ring {current_ring_id} not found in deployment configuration")

        # Get next ring
        next_ring_index = current_ring_index + 1
        if next_ring_index >= len(self.deployment_rings):
            raise ValueError("Already at final ring - cannot promote further")

        next_ring = self.deployment_rings[next_ring_index]
        current_ring = self.deployment_rings[current_ring_index]

        # Get package for assignment
        package = self._get_package(package_id)
        if not package:
            raise ValueError(f"Package {package_id} not found")

        logger.info(
            "Promoting to next ring",
            deployment_id=deployment_id,
            from_ring=current_ring['name'],
            to_ring=next_ring['name'],
            intune_app_id=intune_app_id
        )

        # Assign to next ring
        self._assign_to_ring(intune_app_id, package, next_ring_index)

        # Update current deployment status to SUCCESSFUL
        with db_session_scope() as session:
            deployment = session.query(Deployment).filter(Deployment.id == deployment_id).first()
            if deployment:
                deployment.status = DeploymentStatus.SUCCESSFUL
                deployment.completed_at = datetime.utcnow()
                deployment.promoted_at = datetime.utcnow()

        logger.info(
            "Promotion completed successfully",
            deployment_id=deployment_id,
            from_ring=current_ring['name'],
            to_ring=next_ring['name'],
            package_id=package_id
        )

        return {
            'deployment_id': deployment_id,
            'package_id': package_id,
            'from_ring': current_ring['name'],
            'to_ring': next_ring['name'],
            'status': 'promoted',
            'intune_app_id': intune_app_id
        }

    def check_and_promote_eligible_deployments(self) -> Dict[str, Any]:
        """Check all in-progress deployments and promote eligible ones to next ring.

        Queries all deployments with status IN_PROGRESS that are not at the final
        ring, evaluates each for promotion eligibility based on dwell time and
        success thresholds, and automatically promotes eligible deployments.
        Designed to be called by a periodic Celery task.

        Returns:
            Dict with keys:
                - total_checked: Total number of deployments evaluated
                - eligible_count: Number of deployments eligible for promotion
                - promoted_count: Number of deployments successfully promoted
                - failed_promotions: Number of promotions that failed
                - errors: List of error details for failed promotions
                - promotions: List of promotion details for successful promotions
        """
        logger.info("Starting automated ring promotion check")

        # Check if auto-promotion is enabled
        promotion_config = self.config.get('ring_promotion', {})
        if not promotion_config.get('auto_promote', False):
            logger.info("Auto-promotion is disabled in configuration")
            return {
                'total_checked': 0,
                'eligible_count': 0,
                'promoted_count': 0,
                'failed_promotions': 0,
                'errors': [],
                'promotions': [],
                'message': 'Auto-promotion disabled in configuration'
            }

        with db_session_scope() as session:
            # Query all IN_PROGRESS deployments
            deployments = session.query(Deployment).filter(
                Deployment.status == DeploymentStatus.IN_PROGRESS
            ).all()

            total_checked = len(deployments)
            logger.info("Found in-progress deployments for promotion check", count=total_checked)

            if total_checked == 0:
                return {
                    'total_checked': 0,
                    'eligible_count': 0,
                    'promoted_count': 0,
                    'failed_promotions': 0,
                    'errors': [],
                    'promotions': []
                }

            eligible_count = 0
            promoted_count = 0
            failed_promotions = 0
            errors = []
            promotions = []

            # Check each deployment for promotion eligibility
            for deployment in deployments:
                try:
                    # Skip deployments at final ring
                    current_ring_index = None
                    for idx, ring in enumerate(self.deployment_rings):
                        if ring['ring_id'] == deployment.ring_id:
                            current_ring_index = idx
                            break

                    if current_ring_index is None:
                        logger.warning(
                            "Deployment has unknown ring_id",
                            deployment_id=deployment.id,
                            ring_id=deployment.ring_id
                        )
                        continue

                    if current_ring_index >= len(self.deployment_rings) - 1:
                        logger.debug(
                            "Deployment at final ring - skipping",
                            deployment_id=deployment.id,
                            ring=deployment.ring_name
                        )
                        continue

                    # Check if eligible for promotion
                    eligible, reason = self.is_eligible_for_promotion(deployment)

                    if eligible:
                        eligible_count += 1
                        logger.info(
                            "Deployment eligible for promotion",
                            deployment_id=deployment.id,
                            ring=deployment.ring_name,
                            reason=reason
                        )

                        # Attempt to promote
                        try:
                            promotion_result = self.promote_to_next_ring(deployment.id)
                            promoted_count += 1
                            promotions.append(promotion_result)

                            logger.info(
                                "Deployment promoted successfully",
                                deployment_id=deployment.id,
                                from_ring=promotion_result['from_ring'],
                                to_ring=promotion_result['to_ring']
                            )

                        except Exception as e:
                            logger.error(
                                "Failed to promote eligible deployment",
                                deployment_id=deployment.id,
                                error=str(e),
                                exc_info=True
                            )
                            failed_promotions += 1
                            errors.append({
                                'deployment_id': deployment.id,
                                'ring': deployment.ring_name,
                                'error': str(e)
                            })

                    else:
                        logger.debug(
                            "Deployment not eligible for promotion",
                            deployment_id=deployment.id,
                            ring=deployment.ring_name,
                            reason=reason
                        )

                except Exception as e:
                    logger.error(
                        "Error checking deployment eligibility",
                        deployment_id=deployment.id,
                        error=str(e),
                        exc_info=True
                    )
                    errors.append({
                        'deployment_id': deployment.id,
                        'ring': deployment.ring_name,
                        'error': f"Eligibility check failed: {str(e)}"
                    })

        result = {
            'total_checked': total_checked,
            'eligible_count': eligible_count,
            'promoted_count': promoted_count,
            'failed_promotions': failed_promotions,
            'errors': errors,
            'promotions': promotions
        }

        logger.info(
            "Automated ring promotion check completed",
            total_checked=total_checked,
            eligible=eligible_count,
            promoted=promoted_count,
            failed=failed_promotions
        )

        return result

    # ---------------------------------------------------------------------------
    # Driver Update Profiles (Intune-native driver management — ch04 reference)
    # ---------------------------------------------------------------------------

    def deploy_driver_update_profile(
        self,
        job: Job,
        approval_type: str = 'manual',
        deferral_days: int = 3,
    ) -> Dict[str, Any]:
        """Create an Intune Driver Update Profile and assign it to Ring 0.

        This is the Intune-native approach from ch04 — instead of packaging a
        CAB as a Win32 app, it creates a Driver Update Profile that lets Intune
        / Windows Update surface and manage driver updates for the targeted
        device group.

        Use ``approval_type='manual'`` for firmware/GPU drivers that need
        review, or ``'automatic'`` with a deferral for routine driver updates.

        Note: The approval type is **immutable** after creation. To change it
        you must delete and recreate the profile.
        """
        logger.info(
            "Creating driver update profile deployment",
            job_id=job.id,
            approval_type=approval_type,
        )

        self._validate_azure_config()

        hardware_model = job.hardware_model or job.software_title
        display_name = f"Driver Updates - {hardware_model}"
        description = (
            f"Driver update management for {hardware_model} "
            f"({job.vendor or 'Unknown'}) — {approval_type} approval"
        )

        graph_client = self._get_graph_client()

        # Check for existing profile with the same name
        existing = graph_client.list_driver_update_profiles()
        for profile in existing.get('value', []):
            if profile.get('displayName') == display_name:
                logger.info(
                    "Driver update profile already exists",
                    profile_id=profile['id'],
                )
                return {
                    'profile_id': profile['id'],
                    'status': 'already_exists',
                    'display_name': display_name,
                }

        profile = graph_client.create_driver_update_profile(
            display_name=display_name,
            description=description,
            approval_type=approval_type,
            deferral_days=deferral_days,
        )
        profile_id = profile['id']

        # Assign to Ring 0 device group
        if self.deployment_rings:
            ring = self.deployment_rings[0]
            graph_client.assign_driver_update_profile(
                profile_id, ring['entra_group_id']
            )
            logger.info(
                "Driver update profile assigned",
                profile_id=profile_id,
                ring=ring['name'],
            )

        logger.info(
            "Driver update profile deployment complete",
            job_id=job.id,
            profile_id=profile_id,
        )

        return {
            'profile_id': profile_id,
            'status': 'created',
            'display_name': display_name,
            'approval_type': approval_type,
            'note': (
                'Intune will take 1-2 days to inventory devices and populate '
                'available driver updates for this profile.'
            ),
        }

    def get_deployment_status(self, intune_app_id: str) -> Dict[str, Any]:
        """Get deployment status from Intune via Graph API.

        Queries Microsoft Graph API for real-time install status of the Win32 app,
        including successful installs, failed installs, pending installs, and
        not-applicable devices. Returns aggregated counts plus device-level details
        for failed installations.

        Args:
            intune_app_id: The Intune mobile app ID

        Returns:
            Dict with keys:
                - app_id: The Intune app ID
                - installed_count: Number of successful installs
                - failed_count: Number of failed installs
                - pending_count: Number of pending installs
                - not_applicable_count: Number of not-applicable devices
                - failed_devices: List of failed device details (device name, error code, etc.)
                - total_targeted: Total number of targeted devices
                - error: Error message if status check fails
        """
        logger.info("Fetching deployment status", app_id=intune_app_id)
        graph_client = self._get_graph_client()

        try:
            # Get detailed per-device status with pagination support
            device_statuses = graph_client.get_app_device_statuses(intune_app_id)

            # Parse device statuses into aggregated counts and failed device details
            parsed_status = graph_client._parse_install_statuses(device_statuses)

            result = {
                'app_id': intune_app_id,
                'installed_count': parsed_status['installed'],
                'failed_count': parsed_status['failed'],
                'pending_count': parsed_status['pending'],
                'not_applicable_count': parsed_status['not_applicable'],
                'failed_devices': parsed_status['failed_device_details'],
                'total_targeted': len(device_statuses)
            }

            logger.info(
                "Deployment status retrieved",
                app_id=intune_app_id,
                installed=result['installed_count'],
                failed=result['failed_count'],
                pending=result['pending_count'],
                total=result['total_targeted']
            )

            return result

        except Exception as e:
            logger.error("Failed to get deployment status", app_id=intune_app_id, error=str(e))
            return {
                'app_id': intune_app_id,
                'error': str(e)
            }

    def calculate_failure_rate(self, total_count: int, failed_count: int) -> float:
        """Calculate the failure rate as a percentage.

        Computes the percentage of failed deployments out of the total number
        of targeted devices. Returns 0.0 if total_count is 0 to avoid division
        by zero.

        Args:
            total_count: Total number of targeted devices
            failed_count: Number of failed deployments

        Returns:
            Failure rate as a percentage (0.0 to 100.0)
        """
        if total_count == 0:
            return 0.0
        return (failed_count / total_count) * 100.0

    def calculate_success_rate(self, successful_installs: int, total_targeted: int) -> float:
        """Calculate the success rate as a percentage.

        Computes the percentage of successful deployments out of the total number
        of targeted devices. Returns 0.0 if total_targeted is 0 to avoid division
        by zero.

        Args:
            successful_installs: Number of successful installations
            total_targeted: Total number of targeted devices

        Returns:
            Success rate as a percentage (0.0 to 100.0)
        """
        if total_targeted == 0:
            return 0.0
        return (successful_installs / total_targeted) * 100.0

    def should_trigger_rollback(self, successful: int, failed: int, pending: int) -> bool:
        """Evaluate if deployment meets rollback criteria.

        Checks if the deployment failure rate exceeds the configured threshold
        and if there are enough completed installations to make a reliable
        decision. Pending installations are excluded from the calculation.

        Args:
            successful: Number of successful installations
            failed: Number of failed installations
            pending: Number of pending installations (not used in calculation)

        Returns:
            True if rollback should be triggered, False otherwise
        """
        rollback_config = self.config.get('rollback', {})

        # Check if rollback is enabled in configuration
        if not rollback_config.get('enabled', False):
            logger.debug("Rollback disabled in configuration")
            return False

        # Get threshold and minimum count from config
        failure_threshold = rollback_config.get('failure_threshold_percent', 20.0)
        minimum_install_count = rollback_config.get('minimum_install_count', 5)

        # Calculate total attempted installs (exclude pending)
        total_attempted = successful + failed

        # Check if we have enough data to make a decision
        if total_attempted < minimum_install_count:
            logger.debug(
                "Insufficient install count for rollback decision",
                total_attempted=total_attempted,
                minimum_required=minimum_install_count
            )
            return False

        # Calculate failure rate
        failure_rate = self.calculate_failure_rate(total_attempted, failed)

        # Determine if rollback should be triggered
        should_rollback = failure_rate > failure_threshold

        logger.info(
            "Rollback evaluation",
            successful=successful,
            failed=failed,
            pending=pending,
            failure_rate=failure_rate,
            threshold=failure_threshold,
            should_rollback=should_rollback
        )

        return should_rollback

    def execute_rollback(
        self,
        deployment_id: int,
        failure_rate: float,
        affected_device_count: int,
        reason: str = "Automatic rollback due to failure threshold exceeded"
    ) -> Dict[str, Any]:
        """
        Execute full rollback flow for a failed deployment.

        Orchestrates the complete rollback process:
        1. Remove failed assignment from affected ring
        2. Get previous known-good package version
        3. Re-deploy previous package to the same ring
        4. Update deployment status to ROLLED_BACK
        5. Log rollback event with full context

        Args:
            deployment_id: ID of the deployment to roll back
            failure_rate: Failure rate percentage that triggered rollback
            affected_device_count: Number of devices affected by failed deployment
            reason: Reason for rollback (default: automatic threshold exceeded)

        Returns:
            Dict with rollback details: previous_package_id, previous_version, status
        """
        logger.info(
            "Starting rollback execution",
            deployment_id=deployment_id,
            failure_rate=failure_rate,
            affected_device_count=affected_device_count
        )

        # Get deployment record and extract key information
        with db_session_scope() as session:
            deployment = session.query(Deployment).filter(Deployment.id == deployment_id).first()
            if not deployment:
                raise ValueError(f"Deployment {deployment_id} not found")

            package_id = deployment.package_id
            intune_app_id = deployment.intune_app_id
            group_id = deployment.entra_group_id
            ring_id = deployment.ring_id
            failed_count = deployment.failed_installs

        # Step 1: Remove failed assignment from affected ring
        logger.info("Removing failed app assignment", app_id=intune_app_id, group_id=group_id)
        self.remove_app_assignment(intune_app_id, group_id)

        # Step 2: Get previous known-good package
        logger.info("Finding previous known-good package", current_package_id=package_id)
        previous_package = self.get_previous_package(package_id)
        if not previous_package:
            raise ValueError(f"No previous known-good package found for package {package_id}")

        if not previous_package.intune_app_id:
            raise ValueError(
                f"Previous package {previous_package.id} was not properly deployed "
                "(missing intune_app_id)"
            )

        # Step 3: Re-deploy previous package to the same ring
        # Find the ring index from ring_id (e.g., "ring0" -> 0)
        ring_index = None
        for idx, ring in enumerate(self.deployment_rings):
            if ring['ring_id'] == ring_id:
                ring_index = idx
                break

        if ring_index is None:
            raise ValueError(f"Ring {ring_id} not found in deployment configuration")

        logger.info(
            "Re-deploying previous package to ring",
            previous_package_id=previous_package.id,
            previous_version=previous_package.version,
            intune_app_id=previous_package.intune_app_id,
            ring_index=ring_index,
            ring_id=ring_id
        )
        self._assign_to_ring(previous_package.intune_app_id, previous_package, ring_index)

        # Step 4: Update deployment status to ROLLED_BACK
        with db_session_scope() as session:
            deployment = session.query(Deployment).filter(Deployment.id == deployment_id).first()
            if deployment:
                deployment.status = DeploymentStatus.ROLLED_BACK
                deployment.rolled_back_at = datetime.utcnow()
                deployment.rollback_reason = (
                    f"{reason}. Failure rate: {failure_rate:.1f}% "
                    f"({failed_count}/{affected_device_count} devices). "
                    f"Rolled back to version {previous_package.version}"
                )
                deployment.previous_package_id = previous_package.id

        # Step 5: Log rollback event with full context
        logger.info(
            "Rollback completed successfully",
            deployment_id=deployment_id,
            previous_package_id=previous_package.id,
            previous_version=previous_package.version,
            failure_rate=failure_rate,
            affected_devices=affected_device_count,
            failed_count=failed_count,
            target_version=previous_package.version
        )

        return {
            'deployment_id': deployment_id,
            'previous_package_id': previous_package.id,
            'previous_version': previous_package.version,
            'status': 'rolled_back',
            'failure_rate': failure_rate,
            'affected_devices': affected_device_count
        }

    def update_deployment_status(self, deployment_id: int, status_data: Dict[str, Any]):
        """Update deployment record with latest status data from Intune.

        Takes the status data dict returned by get_deployment_status() and persists
        it to the Deployment record in the database. Updates install counts, sets
        the last_status_check timestamp, and stores device-level failure details.

        Args:
            deployment_id: The deployment record ID to update
            status_data: Dict containing status data with keys:
                - installed_count: Number of successful installs
                - failed_count: Number of failed installs
                - pending_count: Number of pending installs
                - not_applicable_count: Number of not-applicable devices
                - total_targeted: Total number of targeted devices
                - failed_devices: List of failed device details

        Raises:
            ValueError: If deployment_id not found
            Exception: If database update fails
        """
        logger.info("Updating deployment status", deployment_id=deployment_id)

        with db_session_scope() as session:
            deployment = session.query(Deployment).filter(Deployment.id == deployment_id).first()
            if not deployment:
                logger.error("Deployment not found", deployment_id=deployment_id)
                raise ValueError(f"Deployment {deployment_id} not found")

            # Update install counts
            deployment.successful_installs = status_data.get('installed_count', 0)
            deployment.failed_installs = status_data.get('failed_count', 0)
            deployment.pending_installs = status_data.get('pending_count', 0)
            deployment.not_applicable_installs = status_data.get('not_applicable_count', 0)
            deployment.target_device_count = status_data.get('total_targeted', 0)

            # Update timestamp
            deployment.last_status_check = datetime.utcnow()

            # Store failed device details
            deployment.device_status_details = status_data.get('failed_devices', [])

            logger.info(
                "Deployment status updated",
                deployment_id=deployment_id,
                installed=deployment.successful_installs,
                failed=deployment.failed_installs,
                pending=deployment.pending_installs,
                not_applicable=deployment.not_applicable_installs,
                total=deployment.target_device_count
            )

    def check_all_deployments(self) -> Dict[str, Any]:
        """Check status of all in-progress deployments and update their records.

        Queries all deployments with status IN_PROGRESS from the database,
        fetches current install status from Intune for each, and persists
        the updated status data. This method is designed to be called by
        a periodic Celery task for batch status polling.

        Returns:
            Dict with keys:
                - total_checked: Total number of deployments checked
                - successful_updates: Number of deployments successfully updated
                - failed_updates: Number of deployments that failed to update
                - errors: List of error details for failed updates
                - summary: Aggregated install counts across all deployments
        """
        logger.info("Starting batch deployment status check")

        with db_session_scope() as session:
            # Query all IN_PROGRESS deployments
            deployments = session.query(Deployment).filter(
                Deployment.status == DeploymentStatus.IN_PROGRESS
            ).all()

            total_checked = len(deployments)
            logger.info("Found in-progress deployments", count=total_checked)

            if total_checked == 0:
                return {
                    'total_checked': 0,
                    'successful_updates': 0,
                    'failed_updates': 0,
                    'errors': [],
                    'rollbacks_triggered': 0,
                    'rollback_errors': [],
                    'summary': {
                        'total_installed': 0,
                        'total_failed': 0,
                        'total_pending': 0,
                        'total_not_applicable': 0
                    }
                }

            successful_updates = 0
            failed_updates = 0
            errors = []
            rollbacks_triggered = 0
            rollback_errors = []
            aggregate_stats = {
                'total_installed': 0,
                'total_failed': 0,
                'total_pending': 0,
                'total_not_applicable': 0
            }

            # Check each deployment
            for deployment in deployments:
                # Capture identifiers up front: update_deployment_status() opens a
                # nested db_session_scope() that detaches `deployment`, so any
                # later read of `deployment.id` / `deployment.intune_app_id` --
                # particularly from the except handler below -- raises
                # DetachedInstanceError instead of logging the real failure.
                deployment_id = deployment.id
                intune_app_id = deployment.intune_app_id
                ring_name = deployment.ring_name
                try:
                    logger.info(
                        "Checking deployment status",
                        deployment_id=deployment_id,
                        intune_app_id=intune_app_id,
                        ring=ring_name
                    )

                    # Fetch status from Intune
                    status_data = self.get_deployment_status(intune_app_id)

                    # Check for errors in status fetch
                    if 'error' in status_data:
                        logger.error(
                            "Failed to get deployment status",
                            deployment_id=deployment_id,
                            error=status_data['error']
                        )
                        failed_updates += 1
                        errors.append({
                            'deployment_id': deployment_id,
                            'intune_app_id': intune_app_id,
                            'error': status_data['error']
                        })
                        continue

                    # Update deployment record with status data
                    self.update_deployment_status(deployment_id, status_data)

                    # Aggregate statistics
                    aggregate_stats['total_installed'] += status_data.get('installed_count', 0)
                    aggregate_stats['total_failed'] += status_data.get('failed_count', 0)
                    aggregate_stats['total_pending'] += status_data.get('pending_count', 0)
                    aggregate_stats['total_not_applicable'] += status_data.get('not_applicable_count', 0)

                    successful_updates += 1

                    logger.info(
                        "Deployment status updated successfully",
                        deployment_id=deployment_id,
                        installed=status_data.get('installed_count', 0),
                        failed=status_data.get('failed_count', 0),
                        pending=status_data.get('pending_count', 0)
                    )

                    # Promote the installer to the catalog's "verified" list when
                    # at least one device reports a successful install. Failure
                    # here must not break polling -- log and continue.
                    if status_data.get('installed_count', 0) > 0:
                        try:
                            self._record_catalog_verification(intune_app_id)
                        except Exception as catalog_exc:
                            logger.debug(
                                "Catalog verification recording skipped",
                                deployment_id=deployment_id,
                                error=str(catalog_exc),
                            )

                    # Evaluate automatic rollback against the freshly-polled status.
                    # should_trigger_rollback() respects the rollback.enabled config
                    # flag and threshold, so this is a no-op when rollback is disabled.
                    installed = status_data.get('installed_count', 0)
                    failed = status_data.get('failed_count', 0)
                    pending = status_data.get('pending_count', 0)

                    if self.should_trigger_rollback(installed, failed, pending):
                        failure_rate = self.calculate_failure_rate(installed + failed, failed)
                        logger.warning(
                            "Deployment exceeded failure threshold - triggering rollback",
                            deployment_id=deployment_id,
                            failure_rate=failure_rate,
                            failed=failed,
                            installed=installed
                        )
                        try:
                            rollback_result = self.execute_rollback(
                                deployment_id,
                                failure_rate=failure_rate,
                                affected_device_count=failed
                            )
                            rollbacks_triggered += 1
                            logger.info(
                                "Automatic rollback completed",
                                deployment_id=deployment_id,
                                previous_version=rollback_result.get('previous_version')
                            )
                        except Exception as rollback_exc:
                            logger.error(
                                "Automatic rollback failed",
                                deployment_id=deployment_id,
                                error=str(rollback_exc),
                                exc_info=True
                            )
                            rollback_errors.append({
                                'deployment_id': deployment_id,
                                'error': str(rollback_exc)
                            })

                except Exception as e:
                    logger.error(
                        "Error checking deployment status",
                        deployment_id=deployment_id,
                        error=str(e),
                        exc_info=True
                    )
                    failed_updates += 1
                    errors.append({
                        'deployment_id': deployment_id,
                        'intune_app_id': intune_app_id,
                        'error': str(e)
                    })

        result = {
            'total_checked': total_checked,
            'successful_updates': successful_updates,
            'failed_updates': failed_updates,
            'errors': errors,
            'rollbacks_triggered': rollbacks_triggered,
            'rollback_errors': rollback_errors,
            'summary': aggregate_stats
        }

        logger.info(
            "Batch deployment status check completed",
            total_checked=total_checked,
            successful=successful_updates,
            failed=failed_updates,
            rollbacks_triggered=rollbacks_triggered,
            total_installed=aggregate_stats['total_installed'],
            total_failed=aggregate_stats['total_failed']
        )

        return result
