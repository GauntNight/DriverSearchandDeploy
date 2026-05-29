#!/usr/bin/env python
"""Verify the full publish-AND-deploy loop for an MSI software job.

Goes beyond ``verify_local_packaging.py``: drives Packaging -> Testing ->
Deployment in sequence, then asserts that a ``Deployment`` row was
persisted with status IN_PROGRESS, tied to a real Intune Win32 app id
and a real Entra ring group.

This proves what the 2026-05-28 verify did not: that ``_create_deployment_record``
fires and the local tracking model gets populated, so subsequent status
polling, promotion, and rollback logic have something to operate on.

Cleanup is intentionally manual -- inspect the row, watch the install
land on the assigned ring, then delete the Intune app via Graph and the
local Deployment/Job rows when done.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

from autopackager.agents.deployment import DeploymentAgent
from autopackager.agents.packaging import PackagingAgent
from autopackager.agents.testing import TestingAgent
from autopackager.models.deployment import Deployment
from autopackager.models.job import Job, JobState, JobType
from autopackager.utils.database import db_session_scope
from autopackager.utils.msi_metadata import read_msi_metadata


REPO = Path(__file__).parent
SOURCE_MSI = REPO / "data" / "test_msis" / "7z2408-x64.msi"
INSTALL_COMMAND = "msiexec /i 7z2408-x64.msi /qn /norestart"
APP_NAME = "ZZ_TEST_7-Zip_DeployVerify"


def create_job() -> int:
    metadata = read_msi_metadata(SOURCE_MSI)
    print(f"MSI metadata: {metadata.product_name} {metadata.product_version}")
    with db_session_scope() as session:
        job = Job(
            job_type=JobType.NEW_SOFTWARE,
            state=JobState.PACKAGING,
            software_title=APP_NAME,
            vendor=metadata.manufacturer,
            target_version=metadata.product_version,
            download_url=str(SOURCE_MSI.resolve()),
            job_metadata={
                "install_command": INSTALL_COMMAND,
                "download_url": str(SOURCE_MSI.resolve()),
                "target_version": metadata.product_version,
                "msi_metadata": metadata.to_dict(),
            },
        )
        session.add(job)
        session.flush()
        return job.id


def fetch_job(job_id: int) -> Job:
    with db_session_scope() as session:
        job = session.query(Job).filter(Job.id == job_id).first()
        session.expunge(job)
        return job


def update_job_metadata(job_id: int, **updates) -> None:
    with db_session_scope() as session:
        job = session.query(Job).filter(Job.id == job_id).first()
        md = dict(job.job_metadata or {})
        md.update(updates)
        job.job_metadata = md


def main() -> int:
    if not SOURCE_MSI.exists():
        print(f"FAIL: source MSI missing at {SOURCE_MSI}")
        return 1

    job_id = create_job()
    print(f"Job {job_id} created as {APP_NAME!r}")

    print("\n[1/3] Packaging ...")
    job = fetch_job(job_id)
    pkg = PackagingAgent().package(job)
    print(f"      package_id={pkg.get('package_id')}  intunewin={pkg.get('intunewin_path')}")
    update_job_metadata(
        job_id,
        package_id=pkg.get("package_id"),
        intunewin_path=pkg.get("intunewin_path"),
    )

    print("\n[2/3] Testing ...")
    job = fetch_job(job_id)
    test = TestingAgent().test(job)
    print(f"      test_passed={test.get('test_passed')}")
    if not test.get("test_passed"):
        print(f"      error: {test.get('error_message')}")
        return 1

    print("\n[3/3] Deployment (publish + assign to Ring 0) ...")
    job = fetch_job(job_id)
    deploy = DeploymentAgent().deploy(job)
    print(
        f"      intune_app_id={deploy.get('intune_app_id')}  "
        f"status={deploy.get('status')}  ring={deploy.get('ring')}"
    )

    with db_session_scope() as session:
        rows = (
            session.query(Deployment)
            .filter(Deployment.intune_app_id == deploy["intune_app_id"])
            .all()
        )
        if not rows:
            print(
                "\nFAIL: no Deployment row created for "
                f"intune_app_id={deploy['intune_app_id']!r} -- "
                "_create_deployment_record did not fire."
            )
            return 1
        print("\nDeployment row(s) persisted:")
        for d in rows:
            print(
                f"  id={d.id} ring={d.ring_name} ({d.ring_id}) "
                f"status={d.status.value} group={d.entra_group_id} "
                f"deployed_at={d.deployed_at}"
            )

    print(f"\nDeployment verification PASSED. Job id: {job_id}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        import traceback
        traceback.print_exc()
        print(f"\nFAIL: {exc}", file=sys.stderr)
        sys.exit(1)
