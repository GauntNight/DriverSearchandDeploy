"""Driver-update inventory — the per-driver delta Windows Update surfaces.

Reads Intune **Windows Driver Update Profiles** and their ``driverInventories``
(Graph beta) and shapes them into a structured report: each *applicable* driver
with its available version, class, category, and approval status — grouped and
counted for display. Each inventory row is a driver for which Windows Update
found a newer version applicable to the devices the profile targets, so the
list **is** the current-vs-available delta.

This is the **read** side of Intune-native driver management (WUfB). It creates
and modifies nothing. Profile *creation* is currently blocked under app-only
auth until the tenant's WUfB deployment service is onboarded (see
:meth:`GraphAPIClient.create_driver_update_profile`) — but once a profile
exists and Windows Update has inventoried its devices (the ~1-2 day WUfB sync
after first assignment), this surfaces the delta under the SP token.

Per-profile ``status``:
  * ``no_profiles`` — the tenant has no Windows Driver Update Profiles yet.
  * ``pending``     — profile(s) exist but inventory is empty (WUfB still
                      syncing, or the targeted devices lack telemetry — driver
                      updates only surface when diagnostic data is enabled).
  * ``populated``   — at least one profile has driver rows to review.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from autopackager.utils.logger import get_logger

logger = get_logger(__name__)

# windowsDriverUpdateInventory enum domains (Graph beta).
APPROVAL_STATUSES = ("needsReview", "approved", "declined", "suspended")
CATEGORIES = ("recommended", "previouslyApproved", "other")

_GUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

# Sort key for the actionable view: drivers that need a decision first, then
# recommended ones, then the rest — alphabetical within each band.
_APPROVAL_ORDER = {"needsReview": 0, "suspended": 1, "approved": 2, "declined": 3}
_CATEGORY_ORDER = {"recommended": 0, "previouslyApproved": 1, "other": 2}


def _shape_driver(row: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a raw ``windowsDriverUpdateInventory`` row to snake_case."""
    return {
        "id": row.get("id"),
        "name": row.get("name") or "",
        "manufacturer": row.get("manufacturer") or "",
        "version": row.get("version") or "",
        "driver_class": row.get("driverClass") or "",
        "category": row.get("category") or "",
        "approval_status": row.get("approvalStatus") or "",
        "applicable_device_count": row.get("applicableDeviceCount") or 0,
        "release_date_time": row.get("releaseDateTime"),
        "deploy_date_time": row.get("deployDateTime"),
    }


def _driver_sort_key(d: Dict[str, Any]):
    return (
        _APPROVAL_ORDER.get(d["approval_status"], 9),
        _CATEGORY_ORDER.get(d["category"], 9),
        d["name"].lower(),
    )


def _count(values: List[str], domain) -> Dict[str, int]:
    """Tally ``values`` into a dict keyed by every member of ``domain`` (so
    zero-count buckets are present) plus any unexpected value seen."""
    counts = {k: 0 for k in domain}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    return counts


def _assigned_group_ids(profile: Dict[str, Any]) -> List[str]:
    ids = []
    for a in profile.get("assignments", []) or []:
        gid = (a.get("target") or {}).get("groupId")
        if gid:
            ids.append(gid)
    return ids


def _resolve_profiles(graph_client, profile: Optional[str]) -> List[Dict[str, Any]]:
    """Return the profile stubs to report on.

    ``profile`` may be a GUID, a display name, or ``None`` (all profiles).
    Raises ``LookupError`` if a named/identified profile can't be found.
    """
    if profile is None:
        return graph_client.list_driver_update_profiles().get("value", []) or []

    if _GUID_RE.match(profile):
        prof = graph_client.get_driver_update_profile(profile)
        if prof:
            return [prof]
        raise LookupError(f"No driver update profile with id {profile!r}")

    prof = graph_client.find_driver_update_profile_by_name(profile)
    if prof:
        return [prof]
    raise LookupError(f"No driver update profile named {profile!r}")


def build_report(graph_client, profile: Optional[str] = None) -> Dict[str, Any]:
    """Build the driver-inventory delta report.

    Args:
        graph_client: an authenticated ``GraphAPIClient``.
        profile: optional profile GUID or display name to scope to a single
            profile; ``None`` reports on every profile in the tenant.

    Returns a dict::

        {
          "status": "no_profiles" | "pending" | "populated",
          "profile_count": int,
          "total_drivers": int,
          "needs_review": int,            # actionable across all profiles
          "profiles": [ { ...per-profile... } ],
          "errors": [ "..." ],
        }

    Never raises for an *empty* tenant or a still-syncing profile — those are
    the ``no_profiles`` / ``pending`` states. Per-profile fetch errors (e.g. a
    transient 403/5xx) are collected into ``errors`` and that profile is
    skipped, so one bad profile doesn't sink the whole report.
    """
    errors: List[str] = []

    try:
        stubs = _resolve_profiles(graph_client, profile)
    except LookupError as exc:
        return {
            "status": "no_profiles",
            "profile_count": 0,
            "total_drivers": 0,
            "needs_review": 0,
            "profiles": [],
            "errors": [str(exc)],
        }

    profiles: List[Dict[str, Any]] = []
    total_drivers = 0
    total_needs_review = 0

    for stub in stubs:
        pid = stub.get("id")
        name = stub.get("displayName") or ""
        try:
            # Re-fetch with assignments so we can show the targeted groups, then
            # pull the driver rows. (The list endpoint omits assignments.)
            detail = graph_client.get_driver_update_profile(pid, expand_assignments=True) or stub
            rows = graph_client.list_driver_inventory(pid)
        except Exception as exc:  # noqa: BLE001 — collect & continue
            logger.warning("Driver inventory fetch failed", profile_id=pid, error=str(exc))
            errors.append(f"{name or pid}: {exc}")
            continue

        drivers = sorted((_shape_driver(r) for r in rows), key=_driver_sort_key)
        by_approval = _count([d["approval_status"] for d in drivers], APPROVAL_STATUSES)
        by_category = _count([d["category"] for d in drivers], CATEGORIES)
        by_class: Dict[str, int] = {}
        for d in drivers:
            cls = d["driver_class"] or "(unknown)"
            by_class[cls] = by_class.get(cls, 0) + 1

        needs_review = by_approval.get("needsReview", 0)
        total_drivers += len(drivers)
        total_needs_review += needs_review

        profiles.append({
            "id": pid,
            "display_name": name,
            "approval_type": detail.get("approvalType"),
            "assigned_group_ids": _assigned_group_ids(detail),
            "driver_count": len(drivers),
            "pending": len(drivers) == 0,
            "needs_review": needs_review,
            "counts": {"by_approval": by_approval, "by_category": by_category},
            "by_class": dict(sorted(by_class.items(), key=lambda kv: (-kv[1], kv[0]))),
            "drivers": drivers,
        })

    if not profiles:
        status = "no_profiles"
    elif total_drivers == 0:
        status = "pending"
    else:
        status = "populated"

    return {
        "status": status,
        "profile_count": len(profiles),
        "total_drivers": total_drivers,
        "needs_review": total_needs_review,
        "profiles": profiles,
        "errors": errors,
    }
