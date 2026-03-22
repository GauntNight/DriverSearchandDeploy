"""Deployment Agent - Deploy to Microsoft Intune"""

from typing import Dict, Any, List
from pathlib import Path

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
        Main deployment method - publishes package to Intune
        """
        logger.info("Starting deployment", job_id=job.id)

        # Get package from job metadata
        package_id = job.job_metadata.get('package_id')
        if not package_id:
            raise ValueError("No package ID in job metadata")

        package = self._get_package(package_id)
        if not package:
            raise ValueError(f"Package {package_id} not found")

        # Verify package has passed testing
        if not package.test_passed:
            raise Exception("Package has not passed testing - cannot deploy")

        # Create or update Intune app
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

    def _create_or_update_intune_app(self, package: Package, job: Job) -> str:
        """Create new or update existing Intune Win32 app"""
        logger.info("Creating/updating Intune app", package_id=package.id)

        graph_client = self._get_graph_client()

        # Check if app already exists (by name)
        existing_apps = graph_client.get_win32_apps()
        existing_app = None

        for app in existing_apps.get('value', []):
            if app.get('displayName') == package.name:
                existing_app = app
                break

        # Prepare app data
        app_data = self._prepare_app_data(package, job)

        if existing_app:
            # Update existing app
            logger.info("Updating existing app", app_id=existing_app['id'])
            graph_client.update_win32_app(existing_app['id'], app_data)

            # Create supersedence relationship
            self._create_supersedence(graph_client, existing_app['id'], package)

            return existing_app['id']
        else:
            # Create new app
            logger.info("Creating new app", name=package.name)
            new_app = graph_client.create_win32_app(app_data)
            return new_app['id']

    def _prepare_app_data(self, package: Package, job: Job) -> Dict[str, Any]:
        """Prepare Intune app data structure"""
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
            'detectionRules': package.detection_rules or [],
            'requirementRules': package.requirements or [],
            'minimumSupportedOperatingSystem': {
                'v10_1607': True  # Windows 10 1607+
            }
        }

    def _create_supersedence(
        self,
        graph_client: GraphAPIClient,
        existing_app_id: str,
        new_package: Package
    ):
        """Create supersedence relationship between old and new versions"""
        logger.info("Creating supersedence relationship", old_app_id=existing_app_id)

        # TODO: Implement supersedence using Graph API
        # This requires uploading the new .intunewin file first
        # and then creating the supersedence relationship

        logger.warning("Supersedence creation not fully implemented")

    def _assign_to_ring(self, intune_app_id: str, package: Package, ring_index: int = 0):
        """Assign app to deployment ring"""
        if ring_index >= len(self.deployment_rings):
            logger.error("Invalid ring index", ring_index=ring_index)
            return

        ring = self.deployment_rings[ring_index]
        logger.info("Assigning to ring", ring_name=ring['name'], group_id=ring['entra_group_id'])

        graph_client = self._get_graph_client()

        try:
            # Assign app to Entra ID group
            graph_client.assign_app_to_group(
                intune_app_id,
                ring['entra_group_id'],
                intent='required'
            )

            # Create deployment record
            self._create_deployment_record(package.id, intune_app_id, ring)

        except Exception as e:
            logger.error("Failed to assign to ring", error=str(e))
            raise

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

    def promote_to_next_ring(self, deployment_id: int):
        """Promote deployment to next ring (for future automation)"""
        logger.info("Promoting deployment to next ring", deployment_id=deployment_id)

        # TODO: Implement ring promotion logic
        # 1. Check deployment success metrics
        # 2. If successful, assign to next ring
        # 3. Update deployment record

        logger.warning("Ring promotion not yet implemented")

    def get_deployment_status(self, intune_app_id: str) -> Dict[str, Any]:
        """Get deployment status from Intune"""
        logger.info("Fetching deployment status", app_id=intune_app_id)

        graph_client = self._get_graph_client()

        try:
            # Get app installation status
            # TODO: Implement status checking via Graph API

            return {
                'app_id': intune_app_id,
                'status': 'unknown',
                'note': 'Status checking not fully implemented'
            }

        except Exception as e:
            logger.error("Failed to get deployment status", error=str(e))
            return {'error': str(e)}
