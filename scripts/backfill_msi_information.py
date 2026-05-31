"""One-shot backfill: PATCH msiInformation onto already-published Win32 apps.

Apps that were published before this fix landed have an empty Intune portal
"Version" column because the create-time payload omitted ``msiInformation``
(displayVersion is silently dropped by Graph for Win32 MSI apps -- the portal
reads from ``msiInformation.productVersion``). This script walks the local
Package + Deployment tables, finds packages with MSI metadata that have a
known intune_app_id, and PATCHes the ``msiInformation`` block onto each app
via Graph.

Usage:
    ./venv/Scripts/python.exe scripts/backfill_msi_information.py            # dry-run, prints what it would do
    ./venv/Scripts/python.exe scripts/backfill_msi_information.py --apply    # actually PATCH

Safe to re-run: PATCH is idempotent for ``msiInformation``.
"""

import argparse
import sys
from pathlib import Path

# Allow running as `python scripts/backfill_msi_information.py` from repo root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from autopackager.models.deployment import Deployment
from autopackager.models.package import Package
from autopackager.utils.database import db_session_scope
from autopackager.utils.graph_client import GraphAPIClient


def build_msi_information(package: Package) -> dict | None:
    """Return the Graph ``msiInformation`` block for ``package`` or None."""
    meta = package.package_metadata or {}
    pc, pv = meta.get('msi_product_code'), meta.get('msi_product_version')
    if not (pc and pv):
        return None
    install_lower = (package.install_command or '').lower()
    per_user = 'msiinstallperuser=1' in install_lower
    return {
        'productCode': pc,
        'productVersion': pv,
        'upgradeCode': meta.get('msi_upgrade_code') or pc,
        'requiresReboot': False,
        'packageType': 'perUser' if per_user else 'perMachine',
        'productName': package.name,
        'publisher': package.vendor or 'Unknown',
    }


def main(apply_changes: bool) -> int:
    client = GraphAPIClient()
    candidates: list[tuple[Package, str]] = []

    with db_session_scope() as session:
        # Two sources of intune_app_id: Deployment rows (newer pipeline runs)
        # and Package.intune_app_id (older runs predating the Deployment
        # tracking, e.g. VLC and the ZZ_TEST 7-Zip verification packages).
        # Pre-resolve everything from the ORM while the session is open.
        deployments = session.query(Deployment).all()
        for dep in deployments:
            if not dep.intune_app_id:
                continue
            pkg = session.query(Package).filter(Package.id == dep.package_id).first()
            if not pkg:
                continue
            msi = build_msi_information(pkg)
            if msi:
                candidates.append((pkg.name, pkg.version, dep.intune_app_id, msi))

        packages_with_app_id = session.query(Package).filter(Package.intune_app_id.isnot(None)).all()
        for pkg in packages_with_app_id:
            msi = build_msi_information(pkg)
            if msi:
                candidates.append((pkg.name, pkg.version, pkg.intune_app_id, msi))

    # De-dupe on intune_app_id (a package can have multiple deployment records
    # across rings, and the same app can be reachable via both Deployment and
    # Package.intune_app_id; we only need to PATCH the underlying Intune app
    # once).
    seen = set()
    unique = []
    for name, ver, app_id, msi in candidates:
        if app_id in seen:
            continue
        seen.add(app_id)
        unique.append((name, ver, app_id, msi))

    if not unique:
        print('No MSI-derived packages with known intune_app_id found. Nothing to do.')
        return 0

    print(f'Found {len(unique)} app(s) eligible for msiInformation backfill:')
    for name, ver, app_id, msi in unique:
        print(f'  - {name:35s} {ver:15s} {app_id}  ({msi["productCode"]})')

    if not apply_changes:
        print()
        print('Dry-run only. Re-run with --apply to PATCH.')
        return 0

    print()
    print('Applying PATCHes...')
    failures = []
    for name, ver, app_id, msi in unique:
        try:
            client.patch(
                f'deviceAppManagement/mobileApps/{app_id}',
                {'@odata.type': '#microsoft.graph.win32LobApp', 'msiInformation': msi},
            )
            # Verify the PATCH took
            after = client.get(f'deviceAppManagement/mobileApps/{app_id}')
            got = (after.get('msiInformation') or {}).get('productVersion')
            ok = got == ver
            print(f'  {"OK " if ok else "??"} {name:35s} -> productVersion now {got!r}')
            if not ok:
                failures.append((name, app_id, f'productVersion mismatch: expected {ver!r}, got {got!r}'))
        except Exception as exc:  # noqa: BLE001 -- per-app failures shouldn't abort the whole backfill
            print(f'  FAIL {name}: {exc}')
            failures.append((name, app_id, str(exc)))

    print()
    print(f'Done. {len(unique) - len(failures)} succeeded, {len(failures)} failed.')
    if failures:
        for name, app_id, err in failures:
            print(f'  FAIL {name} ({app_id}): {err}')
        return 1
    return 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--apply', action='store_true',
                        help='Actually PATCH (default: dry-run).')
    args = parser.parse_args()
    sys.exit(main(args.apply))
