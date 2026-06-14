"""The retire ACTION — the back half of the application lifecycle.

An old (N-1/N-2) version that has gone clean (0 installs) past the retention
window is *retire-eligible* (computed in ``intune_view.apply_lifecycle_settings``).
What happens then depends on the product's ``auto_delete_when_clean`` toggle:

  * OFF (default) → **relabel "Retired"** — keep the Intune object, mark it
    locally (``retire_state``) so the UI shows a struck-through "Retired" badge.
    Reversible, tenant-safe.
  * ON            → **delete** the Intune app. Because Intune refuses to delete an
    app that is still the *target* of a supersedence relationship (the newer
    build supersedes it), we first clear those incoming relationships on the
    superseding app(s), then DELETE.

``run_retire_sweep()`` applies this across the estate (called by the daily Beat);
``retire_app()`` is the single-app primitive (the manual "Retire" button + the
sweep both call it). Everything is best-effort and never raises into the Beat.
Removable with the rest of ``demo/``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from autopackager.utils.logger import get_logger

logger = get_logger(__name__)


# --- supersedence-aware delete ---------------------------------------------

def _relationship_payload(rel: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Convert a GET'd relationship object into the minimal shape
    ``updateRelationships`` expects (it REPLACES the whole set, so we must
    re-send the ones we keep). Returns None for shapes we don't recognize."""
    otype = rel.get("@odata.type") or ""
    target = rel.get("targetId")
    if not target:
        return None
    if "mobileAppSupersedence" in otype:
        return {
            "@odata.type": "#microsoft.graph.mobileAppSupersedence",
            "targetId": target,
            "supersedenceType": rel.get("supersedenceType") or "update",
        }
    if "mobileAppDependency" in otype:
        return {
            "@odata.type": "#microsoft.graph.mobileAppDependency",
            "targetId": target,
            "dependencyType": rel.get("dependencyType") or "detect",
        }
    return None


def _clear_incoming_supersedence(graph_client, app_id: str) -> int:
    """Remove every supersedence relationship that POINTS AT ``app_id`` (i.e. the
    newer apps that supersede it), so Intune will allow the delete. Returns the
    number of source apps re-written. Best-effort.
    """
    try:
        rels = graph_client._beta_get(
            f"deviceAppManagement/mobileApps/{app_id}/relationships"
        ).get("value", []) or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not read relationships before delete",
                       app_id=app_id, error=str(exc))
        return 0

    # Source apps that supersede this one (relationship target is us).
    sources = {
        r.get("sourceId")
        for r in rels
        if "mobileAppSupersedence" in (r.get("@odata.type") or "")
        and r.get("targetId") == app_id and r.get("sourceId")
        and r.get("sourceId") != app_id
    }
    rewritten = 0
    for src in sources:
        try:
            src_rels = graph_client._beta_get(
                f"deviceAppManagement/mobileApps/{src}/relationships"
            ).get("value", []) or []
            # Keep everything EXCEPT the relationship targeting the app we delete.
            keep = [
                p for r in src_rels
                if not (r.get("targetId") == app_id)
                for p in (_relationship_payload(r),) if p
            ]
            graph_client._beta_post(
                f"deviceAppManagement/mobileApps/{src}/updateRelationships",
                data={"relationships": keep},
            )
            rewritten += 1
            logger.info("Cleared supersedence link before delete",
                        source_app=src, removed_target=app_id, kept=len(keep))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not clear supersedence link",
                           source_app=src, target=app_id, error=str(exc))
    return rewritten


def delete_app_clearing_supersedence(graph_client, app_id: str) -> Dict[str, Any]:
    """Clear incoming supersedence relationships, then delete the Intune app."""
    cleared = _clear_incoming_supersedence(graph_client, app_id)
    graph_client.delete_win32_app(app_id)
    return {"deleted": True, "supersedence_links_cleared": cleared}


# --- single-app primitive ---------------------------------------------------

def retire_app(app_id: str, *, delete: bool = False,
               graph_client=None) -> Dict[str, Any]:
    """Retire one app. ``delete=False`` (default) relabels it "Retired" (local
    marker, object kept); ``delete=True`` removes the Intune object (clearing
    supersedence first). Returns a result dict; never raises."""
    from demo import retire_state, clean_tracking

    if not app_id:
        return {"ok": False, "error": "no app_id"}
    try:
        if delete:
            if graph_client is None:
                from autopackager.utils.graph_client import GraphAPIClient
                graph_client = GraphAPIClient()
            res = delete_app_clearing_supersedence(graph_client, app_id)
            # Drop all local tracking for the now-gone app.
            retire_state.forget(app_id)
            clean_tracking.forget(app_id)
            logger.info("Retired (deleted) app", app_id=app_id, **res)
            return {"ok": True, "app_id": app_id, "action": "deleted", **res}
        # Relabel path — keep the object, mark it locally.
        since = retire_state.mark_retired(app_id)
        logger.info("Retired (relabeled) app", app_id=app_id, since=since)
        return {"ok": True, "app_id": app_id, "action": "retired",
                "retired_since": since}
    except Exception as exc:  # noqa: BLE001 — surface to caller, don't crash
        try:
            from autopackager.utils.graph_client import format_graph_error
            msg = format_graph_error(exc, action="delete app")
        except Exception:  # noqa: BLE001 — formatting must never mask the error
            msg = str(exc)
        logger.error("Retire action failed", app_id=app_id, delete=delete, error=msg)
        return {"ok": False, "app_id": app_id,
                "action": "delete" if delete else "retire", "error": msg}


# --- estate sweep (daily Beat) ----------------------------------------------

def run_retire_sweep(view: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Walk the estate and retire every retire-eligible old version: delete it if
    its product has ``auto_delete_when_clean`` ON, else relabel "Retired".

    Driven by the daily Beat (independent of the daily *upgrade* flag — the
    relabel is reversible and a delete only fires on an explicit per-product
    opt-in). Already-retired and not-yet-eligible apps are skipped. Best-effort;
    one app's failure never sinks the run.
    """
    from demo import intune_view

    try:
        if view is None:
            view = intune_view.get_apps_view_cached(True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Retire sweep could not load apps view", error=str(exc))
        return {"swept": 0, "actions": [], "error": str(exc)}

    graph_client = None
    actions: List[Dict[str, Any]] = []
    for app in view.get("apps") or []:
        if not app.get("retire_eligible") or app.get("retired"):
            continue
        delete = bool(app.get("auto_delete_when_clean"))
        if delete and graph_client is None:
            try:
                from autopackager.utils.graph_client import GraphAPIClient
                graph_client = GraphAPIClient()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Retire sweep: no Graph client; relabel-only",
                               error=str(exc))
                delete = False
        res = retire_app(app.get("id"), delete=delete, graph_client=graph_client)
        res["name"] = app.get("name")
        actions.append(res)

    if actions:
        logger.info("Retire sweep complete", swept=len(actions),
                    deleted=sum(1 for a in actions if a.get("action") == "deleted"),
                    relabeled=sum(1 for a in actions if a.get("action") == "retired"))
    return {"swept": len(actions), "actions": actions}
