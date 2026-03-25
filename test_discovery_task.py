#!/usr/bin/env python
"""Test script for continuous_catalog_discovery task"""

import sys
import logging
from datetime import datetime

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def test_prerequisites():
    """Test that all prerequisites are met"""
    print("\n=== Testing Prerequisites ===\n")

    # Test 1: Config loads
    try:
        from autopackager.utils.config import get_config
        config = get_config()
        discovery_config = config.get('discovery_schedule', {})
        monitored = discovery_config.get('monitored_models', [])
        print(f"✓ Config loaded successfully")
        print(f"  - Discovery enabled: {discovery_config.get('enabled')}")
        print(f"  - Monitored models: {len(monitored)}")
        if monitored:
            for idx, model in enumerate(monitored):
                print(f"    {idx+1}. {model.get('vendor')} {model.get('model')} ({model.get('driver_type')})")
    except Exception as e:
        print(f"✗ Config error: {e}")
        return False

    # Test 2: Database models import
    try:
        from autopackager.models.discovery_run import DiscoveryRun
        from autopackager.models.job import Job
        print(f"✓ Database models imported successfully")
    except Exception as e:
        print(f"✗ Model import error: {e}")
        return False

    # Test 3: Task imports
    try:
        from autopackager.orchestration.tasks import continuous_catalog_discovery
        print(f"✓ Task imported successfully")
    except Exception as e:
        print(f"✗ Task import error: {e}")
        return False

    # Test 4: Redis connection
    try:
        import redis
        r = redis.Redis(host='localhost', port=6379, db=0)
        r.ping()
        print(f"✓ Redis connection OK")
    except Exception as e:
        print(f"⚠ Redis connection failed: {e}")
        print(f"  Note: Redis must be running for Celery tasks")

    # Test 5: Database connection
    try:
        from autopackager.utils.database import db_session_scope
        with db_session_scope() as session:
            pass
        print(f"✓ Database connection OK")
    except Exception as e:
        print(f"⚠ Database connection failed: {e}")
        print(f"  Note: Database must be accessible for task execution")

    return True

def check_discovery_runs():
    """Check existing DiscoveryRun records"""
    print("\n=== Existing Discovery Runs ===\n")

    try:
        from autopackager.utils.database import db_session_scope
        from autopackager.models.discovery_run import DiscoveryRun

        with db_session_scope() as session:
            runs = session.query(DiscoveryRun).order_by(DiscoveryRun.started_at.desc()).limit(5).all()
            if runs:
                print(f"Found {len(runs)} recent discovery runs:")
                for run in runs:
                    status = "✓ Completed" if run.completed_at else "⏳ In Progress"
                    error = f" (Error: {run.error_message})" if run.error_message else ""
                    print(f"  ID {run.id}: {status}{error}")
                    print(f"    Started: {run.started_at}")
                    if run.completed_at:
                        print(f"    Completed: {run.completed_at}")
                        print(f"    Catalogs scanned: {run.catalogs_scanned}")
                        print(f"    New versions found: {run.new_versions_found}")
                        print(f"    Jobs created: {run.jobs_created}")
                        if run.oem_results:
                            print(f"    OEM results: {run.oem_results}")
                    print()
            else:
                print("No discovery runs found in database")
    except Exception as e:
        print(f"Error querying discovery runs: {e}")

def check_jobs():
    """Check for existing jobs"""
    print("\n=== Existing Jobs ===\n")

    try:
        from autopackager.utils.database import db_session_scope
        from autopackager.models.job import Job

        with db_session_scope() as session:
            jobs = session.query(Job).order_by(Job.created_at.desc()).limit(5).all()
            if jobs:
                print(f"Found {len(jobs)} recent jobs:")
                for job in jobs:
                    print(f"  Job {job.id}: {job.software_title}")
                    print(f"    Vendor: {job.vendor}, Model: {job.hardware_model}")
                    print(f"    Target Version: {job.target_version}")
                    print(f"    State: {job.state}")
                    print()
            else:
                print("No jobs found in database")
    except Exception as e:
        print(f"Error querying jobs: {e}")

if __name__ == '__main__':
    print("=" * 60)
    print("Continuous Catalog Discovery - Manual Test")
    print("=" * 60)

    # Run prerequisite tests
    if test_prerequisites():
        print("\n✓ All prerequisites passed")
    else:
        print("\n✗ Some prerequisites failed - task may not execute properly")

    # Check existing data
    check_discovery_runs()
    check_jobs()

    print("\n" + "=" * 60)
    print("To run the discovery task manually, execute:")
    print("  celery -A autopackager.orchestration.celery_app call autopackager.continuous_catalog_discovery")
    print("=" * 60)
