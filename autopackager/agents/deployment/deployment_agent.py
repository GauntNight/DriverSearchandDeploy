"""Deployment Agent - Deploy to Microsoft Intune"""

import shutil
import tempfile
import time
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Dict, Any

from autopackager.models.job import Job
from autopackager.models.package import Package
from autopackager.models.deployment import Deployment, DeploymentStatus
from autopackager.utils.config import get_config
from autopackager.utils.database import db_session_scope
from autopackager.utils.graph_client import GraphAPIClient
from autopackager.utils.logger import get_logger

logger = get_logger(__name__)


class DeploymentAgent:
    """Agent responsible for deploying packages to Microsoft Intune"""

    def __init__(self):
        self.config = get_config()
        self.deployment_rings = self.config.get('deployment_rings', [])
        self.graph_client = None

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

        # Assign to Ring 0 (IT Pilot)
        self._assign_to_ring(intune_app_id, package, ring_index=0)

        # Update package deployment status
        self._update_package_deployment_status(package.id, intune_app_id)

        logger.info("Deployment completed", job_id=job.id, intune_app_id=intune_app_id)

        return {
            'intune_app_id': intune_app_id,
            'status': 'deployed',
            'ring': self.deployment_rings[0]['name'] if self.deployment_rings else 'Unknown'
        }

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

        existing_apps = graph_client.get_win32_apps()
        existing_app = None
        for app in existing_apps.get('value', []):
            if app.get('displayName') == package.name:
                existing_app = app
                break

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

        # Upload .intunewin content and flip publishingState to 'published'
        self._upload_and_publish(graph_client, app_id, package)

        return app_id

    def _prepare_app_data(self, package: Package, job: Job) -> Dict[str, Any]:
        """Prepare Intune app data structure (Graph API v1.0 schema)."""
        rules = self._normalize_rules(package.detection_rules)

        return {
            '@odata.type': '#microsoft.graph.win32LobApp',
            'displayName': package.name,
            'description': f"{package.name} v{package.version} - {job.vendor}",
            'publisher': job.vendor,
            'fileName': Path(package.intunewin_path).name,
            'installCommandLine': package.install_command,
            'uninstallCommandLine': package.uninstall_command or 'cmd /c exit 0',
            'installExperience': {
                'runAsAccount': 'system',
                'deviceRestartBehavior': 'suppress'
            },
            'rules': rules,
            'minimumSupportedOperatingSystem': {
                'v10_1607': True  # Windows 10 1607+
            }
        }

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
                status=DeploymentStatus.IN_PROGRESS
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

    def _get_package(self, package_id: int) -> Package:
        """Get package by ID"""
        with db_session_scope() as session:
            package = session.query(Package).filter(Package.id == package_id).first()
            if package:
                session.expunge(package)
            return package

    # ---------------------------------------------------------------------------
    # Ring promotion (future)
    # ---------------------------------------------------------------------------

    def promote_to_next_ring(self, deployment_id: int):
        """Promote deployment to next ring (for future automation)"""
        logger.info("Promoting deployment to next ring", deployment_id=deployment_id)
        # TODO: Implement ring promotion logic
        logger.warning("Ring promotion not yet implemented")

    def get_deployment_status(self, intune_app_id: str) -> Dict[str, Any]:
        """Get deployment status from Intune"""
        logger.info("Fetching deployment status", app_id=intune_app_id)
        graph_client = self._get_graph_client()
        try:
            # TODO: Implement status checking via Graph API
            return {
                'app_id': intune_app_id,
                'status': 'unknown',
                'note': 'Status checking not fully implemented'
            }
        except Exception as e:
            logger.error("Failed to get deployment status", error=str(e))
            return {'error': str(e)}
