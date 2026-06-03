"""Center-panel data: a live "Intune > Apps > Windows" view (NOT an iframe).

Builds the center-panel rows from the REAL tenant via the existing
``GraphAPIClient`` — Win32 apps with their ring assignments resolved to group
display names. Falls back to ``demo/fixtures/intune_apps.json`` when Graph isn't
configured, so the console still demos on a credential-less laptop.

We deliberately reuse the generic ``client.get(...)`` with
``$expand=assignments`` so ring resolution costs one request, not one-per-app.
Group-id → ring-name mapping comes from the local ``deployment_rings`` config
(no Graph call), with a cached ``get_group`` fallback for unknown groups.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from autopackager.utils.config import get_config
from autopackager.utils.logger import get_logger

logger = get_logger(__name__)

_FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _ring_name_map() -> Dict[str, str]:
    """group_id -> friendly ring label, from config deployment_rings."""
    out: Dict[str, str] = {}
    for ring in get_config().get("deployment_rings", []) or []:
        gid = ring.get("entra_group_id")
        if gid:
            rid = ring.get("ring_id", "")
            name = ring.get("name", "")
            label = f"{rid.capitalize()} — {name}" if rid.startswith("ring") else name
            # Normalize "ring0" -> "Ring 0"
            label = label.replace("Ring0", "Ring 0").replace("ring0", "Ring 0")
            out[gid] = label
    return out


def _version_from_name(name: Optional[str]) -> str:
    """Best-effort version fallback parsed from a displayName like 'GIMP 3.2.4'."""
    import re

    if not name:
        return ""
    m = re.search(r"(\d+(?:\.\d+){1,3})", name)
    return m.group(1) if m else ""


def _intent_label(intent: Optional[str]) -> str:
    return {
        "required": "Required",
        "available": "Available",
        "uninstall": "Uninstall",
    }.get((intent or "").lower(), intent or "")


def get_apps_view(include_counts: bool = False) -> Dict[str, Any]:
    """Return ``{"mode": "live"|"fixture", "apps": [...], "error": ...}``.

    Each app row: ``{id, name, version, publisher, created, assignments:
    [{ring, intent, group_id}], installed, pending}``.
    """
    try:
        return _live_view(include_counts=include_counts)
    except Exception as exc:  # noqa: BLE001 — any failure ⇒ fixture mode
        logger.warning("Intune live view failed; using fixture", error=str(exc))
        view = _fixture_view()
        view["error"] = str(exc)
        return view


def _live_view(include_counts: bool = False) -> Dict[str, Any]:
    from autopackager.utils.graph_client import GraphAPIClient

    client = GraphAPIClient()
    ring_names = _ring_name_map()
    group_cache: Dict[str, str] = dict(ring_names)

    def group_label(gid: Optional[str]) -> str:
        if not gid:
            return "All / unknown"
        if gid in group_cache:
            return group_cache[gid]
        try:
            grp = client.get_group(gid)
            label = grp.get("displayName") or gid
        except Exception:
            label = gid
        group_cache[gid] = label
        return label

    # Reading displayVersion reliably is fiddly (see KB 04): the v1.0 LIST
    # projection omits it, and ANY $expand (list OR single-entity) strips
    # derived-type scalar props off the polymorphic win32LobApp. The ONLY call
    # that returns it is the BETA single-entity GET with no $select / no
    # $expand. So: list ids, then per app do (1) a beta GET for the full app
    # incl. displayVersion and (2) a separate assignments call. Small tenant →
    # cheap.
    listing = client.get(
        "deviceAppManagement/mobileApps?$filter=isof('microsoft.graph.win32LobApp')"
        "&$orderby=createdDateTime desc"
    )
    rows: List[Dict[str, Any]] = []
    for summary in listing.get("value", []) or []:
        app_id = summary.get("id")
        app = summary
        try:
            app = client._beta_get(f"deviceAppManagement/mobileApps/{app_id}")
        except Exception:
            pass
        assignments = []
        try:
            assign_resp = client.get(f"deviceAppManagement/mobileApps/{app_id}/assignments")
            assign_list = assign_resp.get("value", []) or []
        except Exception:
            assign_list = []
        for a in assign_list:
            target = a.get("target", {}) or {}
            gid = target.get("groupId")
            assignments.append({
                "ring": group_label(gid),
                "intent": _intent_label(a.get("intent")),
                "group_id": gid,
            })
        row = {
            "id": app_id,
            "name": (app.get("displayName") or "").strip(),
            "version": (app.get("displayVersion") or "").strip()
                        or _version_from_name(app.get("displayName")),
            "publisher": (app.get("publisher") or "").strip(),
            "created": (app.get("createdDateTime") or "")[:10],
            "assignments": assignments,
            "installed": None,
            "pending": None,
        }
        if include_counts and app.get("id"):
            try:
                summary = client.get_app_install_summary(app["id"])
                row["installed"] = summary.get("installedDeviceCount")
                row["pending"] = summary.get("pendingInstallDeviceCount")
            except Exception:
                pass
        rows.append(row)
    return {"mode": "live", "apps": rows, "error": None}


def _fixture_view() -> Dict[str, Any]:
    path = _FIXTURES / "intune_apps.json"
    if not path.exists():
        return {"mode": "fixture", "apps": [], "error": "fixture file missing"}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        return {"mode": "fixture", "apps": [], "error": str(exc)}
    apps = data.get("apps", data) if isinstance(data, dict) else data
    return {"mode": "fixture", "apps": apps, "error": None}


def verify_in_intune_url(app_id: Optional[str] = None) -> str:
    """Deep-link to the Intune portal.

    Verified routes (Intune admin center SPA, fragment-routed):
      * Windows apps blade:
        https://intune.microsoft.com/#view/Microsoft_Intune_DeviceSettings/AppsWindowsMenu/~/windowsApps
      * Specific app overview:
        https://intune.microsoft.com/#view/Microsoft_Intune_Apps/SettingsMenu/~/0/appId/{id}

    These are SPA fragment routes (the portal refuses iframing but opens fine in
    a new tab). Confirm against current portal docs before a high-stakes demo —
    Microsoft reshuffles blade ids occasionally.
    """
    base = "https://intune.microsoft.com/#view"
    if app_id:
        return f"{base}/Microsoft_Intune_Apps/SettingsMenu/~/0/appId/{app_id}"
    return f"{base}/Microsoft_Intune_DeviceSettings/AppsWindowsMenu/~/windowsApps"
