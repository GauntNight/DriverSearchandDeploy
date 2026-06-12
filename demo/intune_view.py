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
    [{ring, intent, group_id}], installed, pending}`` plus supersedence-demo
    fields added by ``_enrich_apps``: ``catalog_entry_id``, ``current_version``,
    ``source_url_known``, ``version_state`` (``current`` | ``pending`` |
    ``N-1`` | ``N-2`` | ``""``).
    """
    try:
        view = _live_view(include_counts=include_counts)
    except Exception as exc:  # noqa: BLE001 — any failure ⇒ fixture mode
        logger.warning("Intune live view failed; using fixture", error=str(exc))
        view = _fixture_view()
        view["error"] = str(exc)
    # Reconcile the catalog overlay against the LIVE tenant (live mode only — never
    # prune against fixtures, which would delete real history). Removes stale
    # verified_versions rows pointing at deleted apps so version-state badges and
    # the version-check baseline reflect what's actually on the estate.
    if view.get("mode") == "live":
        try:
            from autopackager.utils import installer_catalog
            live_ids = {a.get("id") for a in (view.get("apps") or []) if a.get("id")}
            installer_catalog.prune_stale_verified_versions(live_ids)
        except Exception as exc:  # noqa: BLE001 — reconcile is best-effort
            logger.warning("verified_versions reconcile failed", error=str(exc))
    try:
        _enrich_apps(view.get("apps") or [])
    except Exception as exc:  # noqa: BLE001 — enrichment is best-effort
        logger.warning("Intune view enrichment failed", error=str(exc))
    return view


# --- Supersedence-demo enrichment ------------------------------------------

def find_entry_for_app_id(catalog, app_id: Optional[str]):
    """Locate the catalog entry + verified_versions row for an Intune app id.

    Scans ``entry.verified_versions[].verified_intune_app_id`` (the tenant-bound
    GUID recorded by the publish / polling hooks). Returns ``(entry, row)`` or
    ``(None, None)``. No Graph call — pure overlay lookup.
    """
    if not app_id:
        return None, None
    for entry in catalog.entries:
        for vv in entry.verified_versions or []:
            if vv.get("verified_intune_app_id") == app_id:
                return entry, vv
    return None, None


def newest_verified_version(entry):
    """Return ``(version, intune_app_id)`` of the HIGHEST-version verified row
    for ``entry`` (the newest version we've deployed), or ``(None, None)``.

    Used as the baseline for "is there something newer upstream?" — so refreshing
    ANY row (even an N-1) compares against the chain's newest, never re-offering a
    version that's already deployed.
    """
    import functools
    from autopackager.utils.version_comparison import compare_catalog_versions

    rows = [vv for vv in (entry.verified_versions or []) if vv.get("product_version")]
    if not rows:
        return None, None
    try:
        rows.sort(
            key=functools.cmp_to_key(
                lambda a, b: compare_catalog_versions(
                    a.get("product_version", ""), b.get("product_version", ""))
            ),
            reverse=True,
        )
    except Exception:  # noqa: BLE001
        pass
    top = rows[0]
    return top.get("product_version"), top.get("verified_intune_app_id")


def _version_state_for(entry, matched_row) -> str:
    """Derive the badge state for a matched verified_versions row by VERSION RANK.

    The badge reflects position in the version chain, not the row's ``status``
    field: all of the entry's verified versions are ranked newest-first, so the
    highest version is ``current`` (the demo's "Current" — even while its first
    device install is still pending), the next is ``N-1``, then ``N-2`` …. This
    is what the spec means by "the app a Current supersedes is N-1" and it is
    robust to a freshly-published version still sitting at status ``pending``.
    """
    if not matched_row:
        return ""
    import functools
    from autopackager.utils.version_comparison import compare_catalog_versions

    rows = [vv for vv in (entry.verified_versions or []) if vv.get("product_version")]
    if not rows:
        return "current"
    try:
        rows.sort(
            key=functools.cmp_to_key(
                lambda a, b: compare_catalog_versions(
                    a.get("product_version", ""), b.get("product_version", ""))
            ),
            reverse=True,
        )
    except Exception:  # noqa: BLE001
        pass
    target_aid = matched_row.get("verified_intune_app_id")
    for idx, vv in enumerate(rows):
        if vv is matched_row or (target_aid and vv.get("verified_intune_app_id") == target_aid):
            return "current" if idx == 0 else f"N-{idx}"
    return "current"


def _enrich_apps(apps: List[Dict[str, Any]]) -> None:
    """Augment each app row in place with supersedence-demo fields.

    A row that maps to a catalog entry gets its chain position
    (``version_state`` = ``current`` / ``N-1`` / …). A row we CANNOT place in a
    chain (no catalog overlay record — e.g. an app installed out-of-band) gets
    ``version_state=""`` so it shows NO badge — it must not claim "Current".
    """
    if not apps:
        return
    from autopackager.utils import installer_catalog

    catalog = installer_catalog.load_catalog()
    for row in apps:
        entry, matched = find_entry_for_app_id(catalog, row.get("id"))
        if entry:
            row["catalog_entry_id"] = entry.id
            row["current_version"] = (
                (matched or {}).get("product_version") or row.get("version") or None
            )
            row["source_url_known"] = bool(entry.canonical_download_url)
            row["version_state"] = _version_state_for(entry, matched)
        else:
            row.setdefault("catalog_entry_id", None)
            row.setdefault("current_version", row.get("version") or None)
            row.setdefault("source_url_known", False)
            row.setdefault("version_state", "")  # unplaceable → no badge, never "Current"


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
