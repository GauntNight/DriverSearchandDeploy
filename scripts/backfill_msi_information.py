"""One-shot backfill: PATCH the full attribute set onto already-published Win32 apps.

Apps that were published before the attribute-completeness fixes landed have
gaps in the Intune portal:
  - "Version" column blank          (missing msiInformation)
  - reboot-required installs marked failed (missing returnCodes)
  - no Help URL                     (missing informationUrl)
  - no notes                        (missing notes)
  - no app icon                     (missing largeIcon)
  - no category badges              (missing categories sub-collection)

This script walks the local Package + Deployment tables, finds packages with
MSI metadata that have a known intune_app_id, and PATCHes every fixable
attribute onto each app via Graph. Fields are sourced in priority order:
  1. catalog override (curated, agnostic, ships in baseline YAML)
  2. package_metadata captured at packaging time (new packages only)
  3. re-read from the MSI file at installer_path (older packages)
  4. Standard defaults

Usage:
    ./venv/Scripts/python.exe scripts/backfill_msi_information.py            # dry-run
    ./venv/Scripts/python.exe scripts/backfill_msi_information.py --apply    # actually PATCH

Safe to re-run: every PATCH is idempotent.
"""

import argparse
import base64
import sys
from pathlib import Path

# Allow running as `python scripts/backfill_msi_information.py` from repo root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from autopackager.models.deployment import Deployment
from autopackager.models.package import Package
from autopackager.utils.database import db_session_scope
from autopackager.utils.graph_client import GraphAPIClient
from autopackager.utils.installer_catalog import load_catalog
from autopackager.utils.msi_metadata import read_msi_icon, read_msi_metadata

_DEFAULT_RETURN_CODES = [
    {'returnCode': 0,    'type': 'success'},
    {'returnCode': 1707, 'type': 'success'},
    {'returnCode': 3010, 'type': 'softReboot'},
    {'returnCode': 1641, 'type': 'hardReboot'},
    {'returnCode': 1618, 'type': 'retry'},
]

_WIN_RELEASE_TO_FLAG = {
    '1607': 'v10_1607', '1703': 'v10_1703', '1709': 'v10_1709',
    '1803': 'v10_1803', '1809': 'v10_1809', '1903': 'v10_1903',
    '1909': 'v10_1909', '2004': 'v10_2004', '20H2': 'v10_2H20',
    '21H1': 'v10_21H1', '21H2': 'v10_21H2', '22H2': 'v10_22H2',
}


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


def build_full_patch(package: Package, catalog) -> dict:
    """Build the full attribute patch payload for ``package``.

    Reads from package_metadata first (newer packages have richer metadata),
    falls back to re-reading the MSI from installer_path (older packages),
    and finally to the catalog override / hardcoded defaults.
    """
    meta = package.package_metadata or {}
    catalog_entry = catalog.match_by_product_code(meta.get('msi_product_code'))

    # Re-read MSI from disk for fields the older packaging didn't capture.
    # Stored icon bytes are only trusted when their mime is already a Graph-
    # compatible image type: earlier packaging revisions stored raw ICO bytes
    # which Intune rejects ("Icon in invalid format."), so we re-extract for
    # those entries to get a clean PNG out of the ICO container.
    msi_help_link = meta.get('msi_help_link')
    msi_subject = meta.get('msi_subject')
    stored_icon_mime = meta.get('msi_icon_mime')
    stored_icon_b64 = meta.get('msi_icon_b64')
    _GRAPH_OK_MIMES = {'image/png', 'image/jpeg', 'image/gif'}
    if stored_icon_mime in _GRAPH_OK_MIMES and stored_icon_b64:
        msi_icon_mime, msi_icon_b64 = stored_icon_mime, stored_icon_b64
    else:
        msi_icon_mime, msi_icon_b64 = None, None
    if (not msi_help_link or not msi_subject or not msi_icon_b64) and package.installer_path:
        msi_path = Path(package.installer_path)
        if msi_path.exists():
            try:
                m = read_msi_metadata(msi_path)
                if not msi_help_link:
                    msi_help_link = m.all_properties.get('ARPHELPLINK') or m.all_properties.get('ARPURLINFOABOUT')
                if not msi_subject:
                    msi_subject = m.subject or m.all_properties.get('ARPCOMMENTS')
                if not msi_icon_b64:
                    ico = read_msi_icon(msi_path)
                    if ico:
                        msi_icon_mime, blob = ico
                        msi_icon_b64 = base64.b64encode(blob).decode('ascii')
            except Exception:  # noqa: BLE001 -- best-effort enrichment
                pass

    patch: dict = {'@odata.type': '#microsoft.graph.win32LobApp'}

    msi_info = build_msi_information(package)
    if msi_info:
        patch['msiInformation'] = msi_info

    patch['returnCodes'] = list(_DEFAULT_RETURN_CODES)

    min_os = (catalog_entry.min_os_version if catalog_entry else None) or '1607'
    patch['minimumSupportedOperatingSystem'] = {
        _WIN_RELEASE_TO_FLAG.get(min_os, 'v10_1607'): True
    }

    info_url = (
        (catalog_entry.information_url if catalog_entry else None)
        or msi_help_link
    )
    if info_url:
        patch['informationUrl'] = info_url

    notes_parts = []
    catalog_desc = catalog_entry.description if catalog_entry else None
    if catalog_desc or msi_subject:
        notes_parts.append(catalog_desc or msi_subject)
    if notes_parts:
        patch['notes'] = '\n'.join(notes_parts)

    icon_b64 = (catalog_entry.icon_b64 if catalog_entry else None) or msi_icon_b64
    icon_mime = msi_icon_mime
    if catalog_entry and catalog_entry.icon_b64:
        from autopackager.utils.msi_metadata import _detect_image_mime
        try:
            icon_mime = _detect_image_mime(base64.b64decode(catalog_entry.icon_b64))
        except Exception:  # noqa: BLE001
            icon_mime = None
    if icon_b64 and icon_mime:
        patch['largeIcon'] = {'type': icon_mime, 'value': icon_b64}

    return patch


def main(apply_changes: bool) -> int:
    client = GraphAPIClient()
    candidates: list[tuple[Package, str]] = []

    catalog = load_catalog()

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
            patch = build_full_patch(pkg, catalog)
            if 'msiInformation' in patch:
                candidates.append((pkg.name, pkg.version, dep.intune_app_id, patch))

        packages_with_app_id = session.query(Package).filter(Package.intune_app_id.isnot(None)).all()
        for pkg in packages_with_app_id:
            patch = build_full_patch(pkg, catalog)
            if 'msiInformation' in patch:
                candidates.append((pkg.name, pkg.version, pkg.intune_app_id, patch))

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

    print(f'Found {len(unique)} app(s) eligible for backfill:')
    for name, ver, app_id, patch in unique:
        fields_set = sorted(k for k in patch if k != '@odata.type')
        large_icon_note = ''
        if 'largeIcon' in fields_set:
            large_icon_note = f' (icon {len(patch["largeIcon"]["value"])} chars)'
        print(f'  - {name:35s} {ver:15s} {app_id}')
        print(f'      fields: {", ".join(fields_set)}{large_icon_note}')

    if not apply_changes:
        print()
        print('Dry-run only. Re-run with --apply to PATCH.')
        return 0

    print()
    print('Applying PATCHes...')
    failures = []
    for name, ver, app_id, patch in unique:
        try:
            client.patch(f'deviceAppManagement/mobileApps/{app_id}', patch)
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
