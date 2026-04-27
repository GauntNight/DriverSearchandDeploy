"""Dashboard Service - Data Aggregation for Web Dashboard"""

from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from sqlalchemy import func, desc

from autopackager.models.job import Job, JobState, JobType
from autopackager.models.deployment import Deployment, DeploymentStatus
from autopackager.models.package import Package
from autopackager.models.discovery_run import DiscoveryRun
from autopackager.utils.database import db_session_scope
from autopackager.utils.logger import get_logger
from autopackager.utils.config import get_config

logger = get_logger(__name__)


class DashboardService:
    """Service for aggregating dashboard data from multiple sources"""

    def __init__(self):
        self.config = get_config()
        logger.info("Dashboard service initialized")

    def get_statistics(self) -> Dict[str, Any]:
        """Get overall dashboard statistics"""
        logger.debug("Fetching dashboard statistics")

        with db_session_scope() as session:
            # Job statistics by state
            job_stats = {}
            for state in JobState:
                count = session.query(func.count(Job.id)).filter(
                    Job.state == state
                ).scalar() or 0
                job_stats[state.value] = count

            # Total jobs
            total_jobs = session.query(func.count(Job.id)).scalar() or 0

            # Deployment statistics
            total_deployments = session.query(func.count(Deployment.id)).scalar() or 0

            successful_deployments = session.query(
                func.count(Deployment.id)
            ).filter(
                Deployment.status == DeploymentStatus.SUCCESSFUL
            ).scalar() or 0

            failed_deployments = session.query(
                func.count(Deployment.id)
            ).filter(
                Deployment.status == DeploymentStatus.FAILED
            ).scalar() or 0

            in_progress_deployments = session.query(
                func.count(Deployment.id)
            ).filter(
                Deployment.status == DeploymentStatus.IN_PROGRESS
            ).scalar() or 0

            # Package statistics
            total_packages = session.query(func.count(Package.id)).scalar() or 0
            tested_packages = session.query(
                func.count(Package.id)
            ).filter(
                Package.tested == True
            ).scalar() or 0

            deployed_packages = session.query(
                func.count(Package.id)
            ).filter(
                Package.deployed == True
            ).scalar() or 0

            # Recent activity counts (last 24 hours)
            twenty_four_hours_ago = datetime.utcnow() - timedelta(hours=24)

            recent_jobs = session.query(
                func.count(Job.id)
            ).filter(
                Job.created_at >= twenty_four_hours_ago
            ).scalar() or 0

            recent_deployments = session.query(
                func.count(Deployment.id)
            ).filter(
                Deployment.created_at >= twenty_four_hours_ago
            ).scalar() or 0

            # Discovery run statistics
            total_discovery_runs = session.query(func.count(DiscoveryRun.id)).scalar() or 0

            completed_discovery_runs = session.query(
                func.count(DiscoveryRun.id)
            ).filter(
                DiscoveryRun.completed_at.isnot(None)
            ).scalar() or 0

            failed_discovery_runs = session.query(
                func.count(DiscoveryRun.id)
            ).filter(
                DiscoveryRun.error_message.isnot(None)
            ).scalar() or 0

            recent_discovery_runs = session.query(
                func.count(DiscoveryRun.id)
            ).filter(
                DiscoveryRun.started_at >= twenty_four_hours_ago
            ).scalar() or 0

            # Aggregate discovery metrics
            total_catalogs_scanned = session.query(
                func.sum(DiscoveryRun.catalogs_scanned)
            ).scalar() or 0

            total_versions_found = session.query(
                func.sum(DiscoveryRun.new_versions_found)
            ).scalar() or 0

            total_jobs_from_discovery = session.query(
                func.sum(DiscoveryRun.jobs_created)
            ).scalar() or 0

            stats = {
                'jobs': {
                    'total': total_jobs,
                    'by_state': job_stats,
                    'recent_24h': recent_jobs
                },
                'deployments': {
                    'total': total_deployments,
                    'successful': successful_deployments,
                    'failed': failed_deployments,
                    'in_progress': in_progress_deployments,
                    'recent_24h': recent_deployments
                },
                'packages': {
                    'total': total_packages,
                    'tested': tested_packages,
                    'deployed': deployed_packages
                },
                'discovery_runs': {
                    'total': total_discovery_runs,
                    'completed': completed_discovery_runs,
                    'failed': failed_discovery_runs,
                    'recent_24h': recent_discovery_runs,
                    'total_catalogs_scanned': total_catalogs_scanned,
                    'total_versions_found': total_versions_found,
                    'total_jobs_created': total_jobs_from_discovery
                },
                'timestamp': datetime.utcnow().isoformat()
            }

            logger.debug("Statistics fetched", total_jobs=total_jobs)
            return stats

    def get_jobs(
        self,
        state: Optional[JobState] = None,
        limit: Optional[int] = None,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """Get jobs with optional filtering"""
        logger.debug("Fetching jobs", state=state.value if state else None, limit=limit)

        with db_session_scope() as session:
            query = session.query(Job).order_by(desc(Job.created_at))

            if state:
                query = query.filter(Job.state == state)

            if offset > 0:
                query = query.offset(offset)

            if limit:
                query = query.limit(limit)

            jobs = query.all()

            # Convert to dictionaries
            result = [job.to_dict() for job in jobs]

            logger.debug("Jobs fetched", count=len(result))
            return result

    def get_job_by_id(self, job_id: int) -> Optional[Dict[str, Any]]:
        """Get a single job by ID"""
        logger.debug("Fetching job by ID", job_id=job_id)

        with db_session_scope() as session:
            job = session.query(Job).filter(Job.id == job_id).first()

            if not job:
                logger.warning("Job not found", job_id=job_id)
                return None

            return job.to_dict()

    def get_deployments(
        self,
        status: Optional[DeploymentStatus] = None,
        limit: Optional[int] = None,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """Get deployments with optional filtering"""
        logger.debug("Fetching deployments", status=status.value if status else None)

        with db_session_scope() as session:
            query = session.query(Deployment).order_by(desc(Deployment.created_at))

            if status:
                query = query.filter(Deployment.status == status)

            if offset > 0:
                query = query.offset(offset)

            if limit:
                query = query.limit(limit)

            deployments = query.all()

            # Convert to dictionaries and include package info
            result = []
            for deployment in deployments:
                deployment_dict = deployment.to_dict()

                # Get associated package info
                package = session.query(Package).filter(
                    Package.id == deployment.package_id
                ).first()

                if package:
                    deployment_dict['package_name'] = package.name
                    deployment_dict['package_version'] = package.version
                    deployment_dict['vendor'] = package.vendor

                result.append(deployment_dict)

            logger.debug("Deployments fetched", count=len(result))
            return result

    def get_deployment_ring_status(self) -> Dict[str, Any]:
        """Get deployment status grouped by ring"""
        logger.debug("Fetching deployment ring status")

        with db_session_scope() as session:
            # Get all active deployments grouped by ring
            rings = {}

            deployments = session.query(Deployment).filter(
                Deployment.status.in_([
                    DeploymentStatus.PENDING,
                    DeploymentStatus.IN_PROGRESS,
                    DeploymentStatus.SUCCESSFUL
                ])
            ).all()

            for deployment in deployments:
                ring_id = deployment.ring_id

                if ring_id not in rings:
                    rings[ring_id] = {
                        'ring_id': ring_id,
                        'ring_name': deployment.ring_name,
                        'deployments': [],
                        'total_devices': 0,
                        'successful': 0,
                        'failed': 0,
                        'pending': 0
                    }

                deployment_dict = deployment.to_dict()

                # Get package info
                package = session.query(Package).filter(
                    Package.id == deployment.package_id
                ).first()

                if package:
                    deployment_dict['package_name'] = package.name
                    deployment_dict['package_version'] = package.version

                rings[ring_id]['deployments'].append(deployment_dict)
                rings[ring_id]['total_devices'] += deployment.target_device_count or 0
                rings[ring_id]['successful'] += deployment.successful_installs or 0
                rings[ring_id]['failed'] += deployment.failed_installs or 0
                rings[ring_id]['pending'] += deployment.pending_installs or 0

            # Convert to sorted list
            result = {
                'rings': sorted(rings.values(), key=lambda x: x['ring_id']),
                'timestamp': datetime.utcnow().isoformat()
            }

            logger.debug("Ring status fetched", ring_count=len(rings))
            return result

    def get_recent_activity(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Get recent activity timeline from jobs and deployments"""
        logger.debug("Fetching recent activity", limit=limit)

        with db_session_scope() as session:
            activity = []

            # Get recent jobs
            recent_jobs = session.query(Job).order_by(
                desc(Job.updated_at)
            ).limit(limit).all()

            for job in recent_jobs:
                activity.append({
                    'type': 'job',
                    'id': job.id,
                    'timestamp': job.updated_at.isoformat() if job.updated_at else job.created_at.isoformat(),
                    'state': job.state.value,
                    'title': job.software_title,
                    'vendor': job.vendor,
                    'job_type': job.job_type.value,
                    'error_message': job.error_message
                })

            # Get recent deployments
            recent_deployments = session.query(Deployment).order_by(
                desc(Deployment.updated_at)
            ).limit(limit).all()

            for deployment in recent_deployments:
                package = session.query(Package).filter(
                    Package.id == deployment.package_id
                ).first()

                activity.append({
                    'type': 'deployment',
                    'id': deployment.id,
                    'timestamp': deployment.updated_at.isoformat() if deployment.updated_at else deployment.created_at.isoformat(),
                    'status': deployment.status.value,
                    'ring_id': deployment.ring_id,
                    'ring_name': deployment.ring_name,
                    'package_name': package.name if package else None,
                    'package_version': package.version if package else None,
                    'successful_installs': deployment.successful_installs,
                    'failed_installs': deployment.failed_installs,
                    'error_message': deployment.error_message
                })

            # Sort all activity by timestamp (most recent first)
            activity.sort(key=lambda x: x['timestamp'], reverse=True)

            # Limit to requested count
            result = activity[:limit]

            logger.debug("Activity fetched", count=len(result))
            return result

    def get_packages(
        self,
        deployed_only: bool = False,
        tested_only: bool = False,
        limit: Optional[int] = None,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """Get packages with optional filtering"""
        logger.debug("Fetching packages", deployed_only=deployed_only, tested_only=tested_only)

        with db_session_scope() as session:
            query = session.query(Package).order_by(desc(Package.created_at))

            if deployed_only:
                query = query.filter(Package.deployed == True)

            if tested_only:
                query = query.filter(Package.tested == True)

            if offset > 0:
                query = query.offset(offset)

            if limit:
                query = query.limit(limit)

            packages = query.all()

            # Convert to dictionaries
            result = [package.to_dict() for package in packages]

            logger.debug("Packages fetched", count=len(result))
            return result

    def get_fleet_coverage(self) -> Dict[str, Any]:
        """Get fleet driver coverage statistics"""
        logger.debug("Fetching fleet coverage data")

        with db_session_scope() as session:
            # Get all completed driver update jobs
            driver_jobs = session.query(Job).filter(
                Job.job_type == JobType.DRIVER_UPDATE,
                Job.state == JobState.COMPLETED
            ).all()

            # Group by vendor and driver type
            coverage = {}

            for job in driver_jobs:
                vendor = job.vendor or 'Unknown'
                driver_type = job.driver_type or 'Unknown'

                if vendor not in coverage:
                    coverage[vendor] = {
                        'vendor': vendor,
                        'driver_types': {},
                        'total_updates': 0
                    }

                if driver_type not in coverage[vendor]['driver_types']:
                    coverage[vendor]['driver_types'][driver_type] = {
                        'type': driver_type,
                        'count': 0,
                        'latest_version': None
                    }

                coverage[vendor]['driver_types'][driver_type]['count'] += 1
                coverage[vendor]['total_updates'] += 1

                # Track latest version
                if job.target_version:
                    coverage[vendor]['driver_types'][driver_type]['latest_version'] = job.target_version

            result = {
                'coverage': list(coverage.values()),
                'timestamp': datetime.utcnow().isoformat()
            }

            logger.debug("Fleet coverage fetched", vendor_count=len(coverage))
            return result

    def get_discovery_runs(
        self,
        limit: Optional[int] = None,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """Get discovery runs with optional limit and offset"""
        logger.debug("Fetching discovery runs", limit=limit)

        with db_session_scope() as session:
            query = session.query(DiscoveryRun).order_by(desc(DiscoveryRun.started_at))

            if offset > 0:
                query = query.offset(offset)

            if limit:
                query = query.limit(limit)

            runs = query.all()

            # Convert to dictionaries
            result = [run.to_dict() for run in runs]

            logger.debug("Discovery runs fetched", count=len(result))
            return result

    def get_discovery_run_by_id(self, run_id: int) -> Optional[Dict[str, Any]]:
        """Get a single discovery run by ID"""
        logger.debug("Fetching discovery run by ID", run_id=run_id)

        with db_session_scope() as session:
            run = session.query(DiscoveryRun).filter(DiscoveryRun.id == run_id).first()

            if not run:
                logger.warning("Discovery run not found", run_id=run_id)
                return None

            return run.to_dict()
