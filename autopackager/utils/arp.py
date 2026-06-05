"""Add/Remove-Programs (ARP) inventory reader.

Reads installed software from the Windows Uninstall registry hives (HKLM 64-bit,
HKLM 32-bit/WOW6432Node, HKCU) — the same source Intune's Detected Apps inventory
is ultimately derived from. Used by the software-delta service to build the
"installed but not packaged" gap from a device's ground truth.

Windows-only (``winreg``). Returns ``[]`` on non-Windows so callers (and CI on
Linux) degrade gracefully.

NOTE: this mirrors the hive-walk in
``local_install_validator._snapshot_uninstall_keys`` but is kept standalone so the
load-bearing validator stays untouched. It additionally captures
``system_component`` and ``windows_installer`` — the flags the inventory
classifier needs to separate hidden OS components from real apps.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

UNINSTALL_SUBPATH = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
IS_WINDOWS = os.name == "nt"


def _open_key(root, sub: str, wow32: bool):
    import winreg

    access = winreg.KEY_READ | (winreg.KEY_WOW64_32KEY if wow32 else winreg.KEY_WOW64_64KEY)
    return winreg.OpenKey(root, sub, 0, access)


def _as_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _read_entry(root, view32: bool, leaf: str, view_tag: str) -> Optional[Dict[str, Any]]:
    import winreg

    try:
        hkey = _open_key(root, UNINSTALL_SUBPATH + "\\" + leaf, view32)
    except FileNotFoundError:
        return None
    try:
        def val(name):
            try:
                v, _ = winreg.QueryValueEx(hkey, name)
                return v
            except FileNotFoundError:
                return None

        display_name = val("DisplayName")
        if not display_name:
            return None
        version = val("DisplayVersion")
        publisher = val("Publisher")
        return {
            "name": str(display_name).strip(),
            "version": str(version).strip() if version else None,
            "publisher": str(publisher).strip() if publisher else None,
            "system_component": _as_int(val("SystemComponent")) == 1,
            "windows_installer": _as_int(val("WindowsInstaller")) == 1,
            "has_uninstall": bool(val("UninstallString") or val("QuietUninstallString")),
            "install_date": val("InstallDate"),
            "key_leaf": leaf,
            "view": view_tag,
        }
    finally:
        hkey.Close()


def read_local_arp() -> List[Dict[str, Any]]:
    """Return all installed-software rows from this machine's Uninstall hives.

    De-duplicated by (name, version) across the three views. Returns ``[]`` on
    non-Windows. Each row::

        {name, version, publisher, system_component, windows_installer,
         has_uninstall, install_date, key_leaf, view}
    """
    if not IS_WINDOWS:
        return []
    import winreg

    targets = [
        (winreg.HKEY_LOCAL_MACHINE, False, "HKLM64"),
        (winreg.HKEY_LOCAL_MACHINE, True, "HKLM32"),
        (winreg.HKEY_CURRENT_USER, False, "HKCU"),
    ]
    rows: List[Dict[str, Any]] = []
    seen = set()
    for root, view32, view_tag in targets:
        try:
            base = _open_key(root, UNINSTALL_SUBPATH, view32)
        except FileNotFoundError:
            continue
        try:
            i = 0
            while True:
                try:
                    leaf = winreg.EnumKey(base, i)
                except OSError:
                    break
                i += 1
                entry = _read_entry(root, view32, leaf, view_tag)
                if not entry:
                    continue
                key = (entry["name"].lower(), entry.get("version") or "")
                if key in seen:
                    continue
                seen.add(key)
                rows.append(entry)
        finally:
            base.Close()
    return rows
