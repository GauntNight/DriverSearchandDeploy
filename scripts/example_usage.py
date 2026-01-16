#!/usr/bin/env python3
"""
Example Usage Script for AutoPackager

This script demonstrates how to programmatically create and manage
packaging jobs using the AutoPackager API.
"""

import sys
import time
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from autopackager.orchestration.engine import OrchestrationEngine
from autopackager.models.job import JobType, JobState
from autopackager.utils.database import init_db
from autopackager.utils.logger import setup_logging, get_logger

# Setup logging
setup_logging(log_level="INFO", log_file="data/logs/example.log")
logger = get_logger(__name__)


def example_create_driver_job():
    """Example: Create a driver update job"""
    logger.info("=== Example: Creating Driver Update Job ===")

    # Initialize database
    init_db(create_tables=True)

    # Create orchestration engine
    engine = OrchestrationEngine()

    # Create a Dell driver update job
    job = engine.create_job(
        job_type=JobType.DRIVER_UPDATE,
        software_title="Dell Latitude 7490 Chipset Driver",
        vendor="dell",
        current_version="1.0.0",
        hardware_model="Latitude 7490",
        driver_type="chipset",
        metadata={
            "created_by": "example_script",
            "purpose": "demonstration"
        }
    )

    logger.info(f"Created job #{job.id}: {job.software_title}")
    logger.info(f"  State: {job.state.value}")
    logger.info(f"  Vendor: {job.vendor}")
    logger.info(f"  Model: {job.hardware_model}")

    return job


def example_monitor_job(job_id: int):
    """Example: Monitor job status"""
    logger.info(f"=== Example: Monitoring Job #{job_id} ===")

    engine = OrchestrationEngine()

    # Poll job status
    for _ in range(10):
        job = engine.get_job(job_id)

        if not job:
            logger.error(f"Job {job_id} not found")
            break

        logger.info(f"Job #{job.id} status: {job.state.value}")

        if job.state in [JobState.COMPLETED, JobState.FAILED]:
            logger.info(f"Job finished with state: {job.state.value}")
            if job.error_message:
                logger.error(f"Error: {job.error_message}")
            break

        time.sleep(5)


def example_list_jobs():
    """Example: List all jobs"""
    logger.info("=== Example: Listing All Jobs ===")

    engine = OrchestrationEngine()
    jobs = engine.get_all_jobs(limit=10)

    logger.info(f"Found {len(jobs)} jobs:")
    for job in jobs:
        logger.info(
            f"  #{job.id}: {job.software_title} "
            f"[{job.state.value}] - {job.vendor}"
        )


def example_filter_jobs_by_state():
    """Example: Filter jobs by state"""
    logger.info("=== Example: Filtering Jobs by State ===")

    engine = OrchestrationEngine()

    # Get completed jobs
    completed = engine.get_jobs_by_state(JobState.COMPLETED, limit=5)
    logger.info(f"Completed jobs: {len(completed)}")

    # Get failed jobs
    failed = engine.get_jobs_by_state(JobState.FAILED, limit=5)
    logger.info(f"Failed jobs: {len(failed)}")

    # Get pending jobs
    pending = engine.get_jobs_by_state(JobState.PENDING, limit=5)
    logger.info(f"Pending jobs: {len(pending)}")


if __name__ == "__main__":
    logger.info("Starting AutoPackager examples")

    # Example 1: Create a job
    job = example_create_driver_job()

    # Example 2: List all jobs
    example_list_jobs()

    # Example 3: Filter jobs by state
    example_filter_jobs_by_state()

    # Example 4: Monitor a specific job
    # Note: In real usage, you would start the Celery worker to process the job
    # example_monitor_job(job.id)

    logger.info("Examples completed")
    logger.info("\nTo process the job, start the Celery worker:")
    logger.info("  python cli.py worker start")
