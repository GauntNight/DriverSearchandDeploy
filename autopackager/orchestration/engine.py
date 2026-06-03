"""Orchestration Engine - Central Job Management"""

from datetime import datetime
from typing import Optional, List

from autopackager.models.job import Job, JobState, JobType
from autopackager.utils.database import db_session_scope
from autopackager.utils.logger import get_logger
from autopackager.utils.config import get_config

logger = get_logger(__name__)


class OrchestrationEngine:
    """Central orchestration engine for managing packaging jobs"""

    def __init__(self):
        self.config = get_config()
        self.max_retries = self.config['jobs']['max_retries']
        self.retry_delay = self.config['jobs']['retry_delay_seconds']
        self.concurrent_jobs = self.config['jobs']['concurrent_jobs']

    def create_job(
        self,
        job_type: JobType,
        software_title: str,
        vendor: str,
        current_version: Optional[str] = None,
        hardware_model: Optional[str] = None,
        driver_type: Optional[str] = None,
        metadata: Optional[dict] = None
    ) -> Job:
        """Create a new packaging job"""
        logger.info(
            "Creating new job",
            job_type=job_type.value,
            software_title=software_title,
            vendor=vendor
        )

        with db_session_scope() as session:
            job = Job(
                job_type=job_type,
                state=JobState.PENDING,
                software_title=software_title,
                vendor=vendor,
                current_version=current_version,
                hardware_model=hardware_model,
                driver_type=driver_type,
                job_metadata=metadata or {}
            )

            session.add(job)
            session.flush()

            job_id = job.id
            logger.info("Job created", job_id=job_id)

        return self.get_job(job_id)

    def get_job(self, job_id: int) -> Optional[Job]:
        """Get a job by ID"""
        with db_session_scope() as session:
            job = session.query(Job).filter(Job.id == job_id).first()
            if job:
                # Detach from session
                session.expunge(job)
            return job

    def job_exists(
        self,
        job_type: JobType,
        software_title: str,
        vendor: str,
        current_version: Optional[str] = None,
        hardware_model: Optional[str] = None,
        driver_type: Optional[str] = None
    ) -> bool:
        """Check if a duplicate job already exists in non-terminal state"""
        with db_session_scope() as session:
            # Exclude terminal states
            terminal_states = [JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED]

            query = session.query(Job).filter(
                Job.job_type == job_type,
                Job.software_title == software_title,
                Job.vendor == vendor,
                Job.state.notin_(terminal_states)
            )

            # Add optional filters if provided
            if current_version is not None:
                query = query.filter(Job.current_version == current_version)

            if hardware_model is not None:
                query = query.filter(Job.hardware_model == hardware_model)

            if driver_type is not None:
                query = query.filter(Job.driver_type == driver_type)

            existing_job = query.first()

            if existing_job:
                logger.info(
                    "Duplicate job found",
                    existing_job_id=existing_job.id,
                    job_type=job_type.value,
                    software_title=software_title,
                    vendor=vendor
                )
                return True

            return False

    def update_job_state(
        self,
        job_id: int,
        new_state: JobState,
        error_message: Optional[str] = None,
        metadata_update: Optional[dict] = None,
        software_title: Optional[str] = None,
        vendor: Optional[str] = None,
    ) -> Job:
        """Update job state.

        ``software_title``/``vendor`` are optional column updates used when a
        job's identity changes after creation — e.g. the demo's consumer→
        enterprise substitution re-points a job at a different installer, so the
        deployed Intune app's displayName (sourced from ``software_title`` via
        ``package.name``) must follow the substitute, not the consumer stub.
        """
        logger.info("Updating job state", job_id=job_id, new_state=new_state.value)

        with db_session_scope() as session:
            job = session.query(Job).filter(Job.id == job_id).first()

            if not job:
                raise ValueError(f"Job {job_id} not found")

            job.state = new_state
            job.updated_at = datetime.utcnow()

            if error_message:
                job.error_message = error_message

            if software_title:
                job.software_title = software_title
            if vendor:
                job.vendor = vendor

            if new_state == JobState.COMPLETED:
                job.completed_at = datetime.utcnow()

            if metadata_update:
                # Create a new dict so SQLAlchemy detects the change.
                # Reassigning the same mutated object is a no-op to SQLAlchemy's
                # change tracker because it compares by identity against the
                # committed state, causing the UPDATE to silently skip this column.
                job.job_metadata = {**(job.job_metadata or {}), **metadata_update}

            session.flush()
            session.expunge(job)

            # Additive, optional demo-console hook. Surfaces failed transitions
            # in the demo's live console. Lazy + exception-swallowed so the
            # removable demo package can never affect core job processing.
            if new_state == JobState.FAILED:
                try:
                    from demo.events import publish_pipeline_event

                    publish_pipeline_event(
                        job_id, "failed",
                        f"Failed: {error_message or 'see logs'}", level="error",
                    )
                except Exception:
                    pass

            return job

    def increment_retry_count(self, job_id: int) -> int:
        """Increment job retry count"""
        with db_session_scope() as session:
            job = session.query(Job).filter(Job.id == job_id).first()

            if not job:
                raise ValueError(f"Job {job_id} not found")

            job.retry_count += 1
            session.flush()

            return job.retry_count

    def get_pending_jobs(self, limit: Optional[int] = None) -> List[Job]:
        """Get all pending jobs"""
        with db_session_scope() as session:
            query = session.query(Job).filter(Job.state == JobState.PENDING)

            if limit:
                query = query.limit(limit)

            jobs = query.all()

            # Detach from session
            for job in jobs:
                session.expunge(job)

            return jobs

    def get_jobs_by_state(self, state: JobState, limit: Optional[int] = None) -> List[Job]:
        """Get jobs by state"""
        with db_session_scope() as session:
            query = session.query(Job).filter(Job.state == state)

            if limit:
                query = query.limit(limit)

            jobs = query.all()

            # Detach from session
            for job in jobs:
                session.expunge(job)

            return jobs

    def get_all_jobs(self, limit: Optional[int] = None) -> List[Job]:
        """Get all jobs"""
        with db_session_scope() as session:
            query = session.query(Job).order_by(Job.created_at.desc())

            if limit:
                query = query.limit(limit)

            jobs = query.all()

            # Detach from session
            for job in jobs:
                session.expunge(job)

            return jobs

    def can_retry_job(self, job_id: int) -> bool:
        """Check if a job can be retried"""
        job = self.get_job(job_id)

        if not job:
            return False

        return job.retry_count < self.max_retries

    def mark_job_failed(self, job_id: int, error_message: str):
        """Mark a job as failed"""
        logger.error("Marking job as failed", job_id=job_id, error=error_message)
        self.update_job_state(job_id, JobState.FAILED, error_message=error_message)

    def mark_job_completed(self, job_id: int, metadata_update: Optional[dict] = None):
        """Mark a job as completed"""
        logger.info("Marking job as completed", job_id=job_id)
        self.update_job_state(job_id, JobState.COMPLETED, metadata_update=metadata_update)

    def delete_job(self, job_id: int) -> bool:
        """Delete a single job record by ID. Returns True if deleted."""
        with db_session_scope() as session:
            job = session.query(Job).filter(Job.id == job_id).first()
            if not job:
                return False
            session.delete(job)
            return True

    def purge_jobs(self, state: str = None) -> int:
        """Delete all jobs, or only jobs matching a given state. Returns count deleted."""
        with db_session_scope() as session:
            q = session.query(Job)
            if state:
                q = q.filter(Job.state == state)
            count = q.count()
            q.delete(synchronize_session=False)
            return count
