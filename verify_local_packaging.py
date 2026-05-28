#!/usr/bin/env python
"""Verify end-to-end MSI packaging against a real Windows-Installer MSI.

Drives PackagingAgent.package() directly so the MSI metadata reader,
install/uninstall command generation, and IntuneWinAppUtil.exe
integration are exercised on a real MSI without bringing up Redis,
Celery, or any Azure dependency.

Prerequisites:
- Python venv with ``requirements.txt`` installed
- ``tools/IntuneWinAppUtil.exe`` present (downloaded by
  ``Install-AutoPackager.ps1``, or manually from
  https://github.com/microsoft/Microsoft-Win32-Content-Prep-Tool)
- Windows host (IntuneWinAppUtil.exe is Windows-only)

The 7-Zip test MSI is downloaded automatically into
``data/test_msis/`` on first run.

Exits 0 on success, non-zero on any verification failure. Asserts the
``.intunewin`` contains an Intune ``Detection.xml`` whose ``MsiInfo``
block matches the Windows Installer COM ground truth for 7-Zip 24.08
x64, so a regression in the MSI metadata reader, the install-command
parser, or the IntuneWinAppUtil invocation surfaces here.
"""

import os
import sys
import urllib.request
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

sys.path.insert(0, os.path.dirname(__file__))

from autopackager.agents.packaging import PackagingAgent
from autopackager.models.job import Job, JobState, JobType
from autopackager.utils.database import db_session_scope
from autopackager.utils.msi_metadata import read_msi_metadata


REPO = Path(__file__).parent
SOURCE_MSI = REPO / "data" / "test_msis" / "7z2408-x64.msi"
SOURCE_MSI_URL = "https://www.7-zip.org/a/7z2408-x64.msi"
INSTALL_COMMAND = "msiexec /i 7z2408-x64.msi /qn /norestart"

# Ground truth — confirmed via the Windows Installer COM API on the real
# 7-Zip 24.08 x64 MSI. If 7-Zip publishes a new build under the same URL
# these will need updating.
EXPECTED = {
    "ProductCode":    "{23170F69-40C1-2702-2408-000001000000}",
    "ProductVersion": "24.08.00.0",
    "UpgradeCode":    "{23170F69-40C1-2702-0000-000004000000}",
    "Publisher":      "Igor Pavlov",
    "Name":           "7-Zip 24.08 (x64 edition)",
    "SetupFile":      "7z2408-x64.msi",
}


def ensure_source_msi() -> None:
    if SOURCE_MSI.exists():
        return
    SOURCE_MSI.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading test MSI from {SOURCE_MSI_URL} ...")
    urllib.request.urlretrieve(SOURCE_MSI_URL, SOURCE_MSI)
    print(f"  saved to {SOURCE_MSI} ({SOURCE_MSI.stat().st_size} bytes)")


def create_job() -> int:
    metadata = read_msi_metadata(SOURCE_MSI)
    print(f"MSI metadata read: {metadata.product_name} {metadata.product_version}")

    with db_session_scope() as session:
        job = Job(
            job_type=JobType.NEW_SOFTWARE,
            state=JobState.PACKAGING,
            software_title=metadata.product_name,
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


def assert_detection_xml(intunewin: Path) -> None:
    """Open the .intunewin, find Detection.xml, and assert MsiInfo matches."""
    with zipfile.ZipFile(intunewin) as zf:
        detection_name = next(
            (n for n in zf.namelist() if n.endswith("Detection.xml")), None
        )
        if not detection_name:
            raise AssertionError("Detection.xml missing from .intunewin")
        xml = zf.read(detection_name).decode("utf-8")

    root = ET.fromstring(xml)
    found = {
        "Name":           root.findtext("Name", default=""),
        "SetupFile":      root.findtext("SetupFile", default=""),
        "ProductCode":    root.findtext("MsiInfo/MsiProductCode", default=""),
        "ProductVersion": root.findtext("MsiInfo/MsiProductVersion", default=""),
        "UpgradeCode":    root.findtext("MsiInfo/MsiUpgradeCode", default=""),
        "Publisher":      root.findtext("MsiInfo/MsiPublisher", default=""),
    }

    print("\nDetection.xml verification:")
    failures = []
    for key, expected in EXPECTED.items():
        actual = found.get(key, "")
        status = "OK" if actual == expected else "FAIL"
        print(f"  [{status}] {key}: {actual!r}")
        if actual != expected:
            failures.append((key, expected, actual))

    if failures:
        raise AssertionError(
            f"{len(failures)} Detection.xml field(s) did not match ground truth: "
            + ", ".join(f"{k} expected {e!r} got {a!r}" for k, e, a in failures)
        )


def main() -> int:
    ensure_source_msi()

    job_id = create_job()
    job = fetch_job(job_id)

    agent = PackagingAgent()
    result = agent.package(job)

    print("\nPackagingAgent result:")
    for k, v in result.items():
        print(f"  {k}: {v}")

    expected_uninstall = (
        f"msiexec /x {EXPECTED['ProductCode']} /qn /norestart"
    )
    if result["uninstall_command"] != expected_uninstall:
        raise AssertionError(
            "uninstall_command does not use ProductCode: "
            f"got {result['uninstall_command']!r}, expected {expected_uninstall!r}"
        )

    intunewin = Path(result["intunewin_path"])
    if not intunewin.is_absolute():
        intunewin = REPO / intunewin
    if not intunewin.exists() or intunewin.stat().st_size < 1024:
        raise AssertionError(f".intunewin missing or implausibly small: {intunewin}")

    assert_detection_xml(intunewin)
    print("\nLocal packaging verification PASSED.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as exc:
        print(f"\nFAIL: {exc}", file=sys.stderr)
        sys.exit(1)
