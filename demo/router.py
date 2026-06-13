"""Additive FastAPI router for the demo console.

Included into the existing ``app`` via ``mount_demo(app)`` from
``autopackager/web/api.py``. Everything here is namespaced under ``/demo`` and
``/api/demo`` so it can't collide with the core dashboard, and the whole thing
is removable by deleting this package + the one ``mount_demo`` call.

Endpoints:
  GET  /demo                              -> three-panel console UI
  GET  /api/demo/preflight                -> readiness lights (AI/Redis/Graph/worker)
  POST /api/demo/jobs                      -> intake (file | url | driver); returns job_id + branch
  GET  /api/demo/stream/{job_id}           -> SSE event stream (pipeline + claude + lamp)
  GET  /api/demo/intune/apps               -> live tenant Win32 apps (fixture fallback)
  GET  /api/demo/intune/verify-url         -> deep-link to the Intune portal
  POST /api/demo/jobs/{job_id}/approve     -> release the optional Ring 0 gate
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from autopackager.utils.logger import get_logger
from demo import events, intake, preflight, intune_view, claude_bridge
from demo import queue as pkg_queue

logger = get_logger(__name__)

_STATIC = Path(__file__).resolve().parent / "static"

demo_router = APIRouter()


@demo_router.get("/api/demo/preflight")
async def api_preflight():
    """Run readiness checks. Slightly slow in live mode (Claude health check)."""
    return await asyncio.to_thread(preflight.run_all)


@demo_router.get("/api/demo/intune/apps")
async def api_intune_apps(counts: bool = True, refresh: bool = False):
    """Center-panel apps view, served stale-while-revalidate.

    Includes per-app install counts by default (the lifecycle "clean" signal),
    fetched via the modern installation-status report; the SWR cache + disk
    snapshot keep the extra per-app calls off the hot path.

    Returns the cached snapshot instantly (memory, or a disk snapshot on a cold
    start) and refreshes in the background once stale, so the panel stays
    responsive instead of blocking on a live Graph fan-out every poll. Pass
    ``refresh=1`` for a synchronous full reload (the manual-refresh / post-publish
    "whole load"). The result carries a ``cache`` block ``{hit, age_s,
    revalidating, source}``.
    """
    view = await asyncio.to_thread(
        intune_view.get_apps_view_cached, counts, force=refresh)
    return view


@demo_router.get("/api/demo/intune/verify-url")
async def api_verify_url(app_id: Optional[str] = None):
    return {"url": intune_view.verify_in_intune_url(app_id)}


@demo_router.get("/api/demo/intune/{app_id}/cves")
async def api_app_cves(app_id: str, mode: Optional[str] = None):
    """CVE risk detail for one Win32 app (the row's detail drawer).

    Returns ``{app_id, name, version, cve: {max_cvss, severity, cve_count,
    cves:[...], source, ...}}``. Pass ``mode=live`` to force a fresh NVD/AI
    lookup for just this app (the default ``cache`` is the offline curated
    path). Best-effort: an unresolvable app returns an empty CVE block, never an
    error.
    """
    return await asyncio.to_thread(_app_cves, app_id, mode)


def _app_cves(app_id: str, mode: Optional[str]) -> dict:
    from autopackager.services import cve_intel

    view = intune_view.get_apps_view(False)
    row = next((a for a in (view.get("apps") or []) if a.get("id") == app_id), None)
    if not row:
        return {"app_id": app_id, "name": None, "version": None,
                "cve": cve_intel.empty_block()}
    # In cache mode the row already carries its CVE block from the view; only do
    # a fresh lookup when a different mode is explicitly requested (e.g. live).
    cve = row.get("cve")
    if mode and mode != "cache":
        cpe = None
        eid = row.get("catalog_entry_id")
        if eid:
            from autopackager.utils import installer_catalog
            entry = installer_catalog.load_catalog().by_id(eid)
            cpe = getattr(entry, "cpe", None) if entry else None
        cve = cve_intel.lookup(
            row.get("name"), cpe=cpe,
            current_version=row.get("current_version") or row.get("version"),
            mode=mode)
    return {
        "app_id": app_id,
        "name": row.get("name"),
        "version": row.get("current_version") or row.get("version"),
        "cve": cve or cve_intel.empty_block(),
    }


@demo_router.get("/api/demo/intune/software-delta")
async def api_software_delta(source: str = "both"):
    """The unmanaged-software gap: installed-but-not-packaged software, classified.

    ``source`` ∈ {intune, local, both}. Builds from Intune Detected Apps (env-wide)
    + local ARP, vs the managed (published) + catalog set. Degrades to local ARP
    when the SP lacks DeviceManagementManagedDevices.Read.All (intune_unavailable).
    """
    return await asyncio.to_thread(_build_software_delta, source)


def _build_software_delta(source: str) -> dict:
    from autopackager.services import software_delta

    if source not in ("intune", "local", "both"):
        source = "both"
    graph_client = None
    if source in ("intune", "both"):
        try:
            from autopackager.utils.graph_client import GraphAPIClient
            graph_client = GraphAPIClient()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Graph client unavailable for software-delta", error=str(exc))
    delta = software_delta.build_delta(source=source, graph_client=graph_client)
    # Best-effort CVE risk on the actionable buckets, so the gap modal can badge
    # known-vulnerable installed-but-unmanaged software by severity.
    try:
        if isinstance(delta, dict):
            for key in ("candidates", "known_packageable"):
                rows = delta.get(key)
                if isinstance(rows, list):
                    intune_view.enrich_cves(rows)
    except Exception as exc:  # noqa: BLE001 — enrichment never blocks the delta
        logger.warning("software-delta CVE enrichment failed", error=str(exc))
    return delta


# --- Packaging queue (from the software-delta backlog) ---------------------

@demo_router.post("/api/demo/queue")
async def api_queue(request: Request, background: BackgroundTasks):
    """Queue selected delta candidates for packaging.

    Body ``{items: [{name, publisher?, version?, bucket?, in_catalog?}, ...],
    mode?}``. Each item becomes a gated (test-scope) Job row immediately, then a
    single background runner processes them ONE AT A TIME (acquire installer →
    gated discovery→packaging→testing; deployment held for the approval gate).

    Returns ``{batch_id, jobs: [{job_id, name}]}`` so the console can stream the
    queued jobs in sequence and offer a Cancel.
    """
    body = await request.json()
    items = body.get("items") or []
    mode = body.get("mode") or None
    if not isinstance(items, list) or not items:
        return JSONResponse(
            {"error": "items (a non-empty list of candidates) required"}, status_code=400)
    try:
        batch_id, specs = await asyncio.to_thread(_create_queue_rows, items)
    except Exception as exc:  # noqa: BLE001
        logger.error("Queue intake failed", error=str(exc), exc_info=True)
        return JSONResponse({"error": str(exc)}, status_code=500)
    if not specs:
        return JSONResponse({"error": "no valid candidates (each needs a name)"}, status_code=400)
    background.add_task(pkg_queue.run_batch, specs, mode=mode)
    return {
        "batch_id": batch_id,
        "jobs": [{"job_id": s["job_id"], "name": s["candidate"].get("name")} for s in specs],
    }


def _create_queue_rows(items: list):
    """Create a Job row per candidate; return (batch_id, specs). Runs in a thread."""
    import uuid

    batch_id = uuid.uuid4().hex[:12]
    specs = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        name = (raw.get("name") or "").strip()
        if not name:
            continue
        candidate = {
            "name": name,
            "publisher": raw.get("publisher"),
            "version": raw.get("version"),
            "bucket": raw.get("bucket"),
            "in_catalog": raw.get("in_catalog") or raw.get("catalog_entry_id"),
            "device_count": raw.get("device_count"),
        }
        job_id = pkg_queue.create_queue_job_row(candidate, batch_id=batch_id)
        specs.append({"job_id": job_id, "candidate": candidate})
    return batch_id, specs


@demo_router.post("/api/demo/queue/cancel")
async def api_queue_cancel(request: Request):
    """Cancel a queue batch — marks every not-yet-terminal job CANCELLED so the
    runner stops advancing. Body ``{job_ids: [int, ...]}``."""
    body = await request.json()
    job_ids = body.get("job_ids") or []
    try:
        ids = [int(j) for j in job_ids]
    except (TypeError, ValueError):
        return JSONResponse({"error": "job_ids must be integers"}, status_code=400)
    n = await asyncio.to_thread(pkg_queue.cancel_batch, ids)
    return {"cancelled": n}


@demo_router.post("/api/demo/jobs/{job_id}/cancel")
async def api_cancel_job(job_id: int):
    """Cancel a single in-flight action (the right-panel Cancel button)."""
    ok = await asyncio.to_thread(pkg_queue.cancel_job, job_id)
    return {"job_id": job_id, "cancelled": bool(ok)}


@demo_router.post("/api/demo/queue/{job_id}/confirm-url")
async def api_queue_confirm_url(job_id: int, request: Request, background: BackgroundTasks):
    """Approve an agent-FOUND installer URL for an ``awaiting_confirm`` queue item.

    Body ``{url?}`` — optional override of the stashed proposed URL (lets the
    operator correct it). Resumes the gated pipeline: download → analyze →
    discovery→packaging→testing (deployment still held for the approval gate).
    """
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    url = (body or {}).get("url") or None
    if url and not intake.is_known_installer(url):
        return JSONResponse(
            {"error": "url must point to a direct .msi, .exe, or .zip installer."},
            status_code=400)
    background.add_task(pkg_queue.confirm_and_package, job_id, url)
    return {"job_id": job_id, "branch": "queue-confirm"}


@demo_router.post("/api/demo/queue/{job_id}/installer")
async def api_queue_installer(job_id: int, request: Request, background: BackgroundTasks):
    """Resume an awaiting-installer queue item once the operator drops a file."""
    form = await request.form()
    upload = form.get("file")
    if upload is None or not hasattr(upload, "filename"):
        return JSONResponse({"error": "file required"}, status_code=400)
    if not intake.is_known_installer(upload.filename):
        return JSONResponse(
            {"error": f"Unsupported file type (got '{upload.filename}')."}, status_code=400)
    data = await upload.read()
    saved = await asyncio.to_thread(intake.save_upload, upload.filename, data)
    background.add_task(pkg_queue.finalize_with_installer, job_id, str(saved))
    return {"job_id": job_id, "branch": "queue-installer"}


@demo_router.post("/api/demo/intune/check-version")
async def api_check_version(request: Request):
    """The 'refresh' brain (spec §2): is there a newer version upstream?

    Body ``{app_id, app_label?, current_version?, mode?}``. Resolves the app to
    its catalog entry (authoritative source URL + deployed version), runs the
    focused version-check bridge, and returns
    ``{is_newer, latest_version, download_url, current_version, entry_id, mode}``.
    The lamp is driven client-side for this synchronous call; no SSE channel.
    """
    body = await request.json()
    app_id = body.get("app_id")
    return await asyncio.to_thread(_check_version_sync, body, app_id)


def _live_app_ids():
    """Best-effort set of Intune Win32 app ids currently in the tenant; None on
    any failure (so callers fall back to unreconciled behaviour rather than
    wrongly treating every app as deleted)."""
    try:
        from autopackager.utils.graph_client import GraphAPIClient
        apps = (GraphAPIClient().get_win32_apps() or {}).get("value", [])
        return {a.get("id") for a in apps if a.get("id")}
    except Exception as exc:  # noqa: BLE001
        try:
            logger.warning("Live app-id fetch failed; version check unreconciled", error=str(exc))
        except Exception:
            pass
        return None


def _check_version_sync(body: dict, app_id: Optional[str]) -> dict:
    from autopackager.utils import installer_catalog

    # Reconcile the overlay against the live tenant FIRST: a stale
    # verified_versions row (an app deleted in a prior demo/cleanup) would
    # otherwise baseline the check on a version no longer deployed — e.g. a
    # deleted 26.01 making a live 26.00 wrongly report "up to date".
    live_ids = _live_app_ids()
    if live_ids is not None:
        try:
            installer_catalog.prune_stale_verified_versions(live_ids)
        except Exception as exc:  # noqa: BLE001
            logger.warning("verified_versions reconcile failed", error=str(exc))

    catalog = installer_catalog.load_catalog()
    entry, row = intune_view.find_entry_for_app_id(catalog, app_id)
    if entry:
        # Compare against the NEWEST deployed version of this product, not the
        # clicked row's version. Refreshing an N-1 row (or the newest itself)
        # then correctly reports "up to date" when the latest is already
        # present, and only flags an upgrade for something newer than the newest
        # we've shipped — never re-offering a version already deployed.
        newest_version, _newest_app = intune_view.newest_verified_version(entry)
        current_version = (
            newest_version or (row or {}).get("product_version") or body.get("current_version")
        )
        source_url = entry.canonical_download_url
        slug = entry.id
        label = body.get("app_label") or entry.id
        entry_id = entry.id
    else:
        current_version = body.get("current_version")
        source_url = None
        slug = body.get("app_label") or app_id
        label = body.get("app_label") or (app_id or "app")
        entry_id = None

    result = claude_bridge.check_version(
        label, current_version, source_url,
        mode=body.get("mode") or None, slug=slug,
    )
    result["entry_id"] = entry_id

    # No duplicates: if the "newer" version already exists somewhere in this
    # product line's deployed apps (same version, or older than the newest
    # deployed), it is NOT an upgrade — suppress the option so we never create a
    # duplicate of an app already in the tenant.
    latest = result.get("latest_version")
    if latest and result.get("is_newer"):
        from autopackager.utils.version_comparison import compare_catalog_versions
        deployed = intune_view.deployed_versions_for_app(app_id)
        try:
            already = any(compare_catalog_versions(latest, dv) <= 0 for dv in deployed)
        except Exception:  # noqa: BLE001
            already = latest in deployed
        if already:
            result["is_newer"] = False
            result["already_deployed"] = True
    return result


_TERMINAL_JOB_STATES = {"completed", "failed", "cancelled"}


def _inflight_upgrade_for_app(app_id: Optional[str]) -> Optional[int]:
    """Return the job id of an IN-FLIGHT upgrade for the same product, or None.

    Soft concurrency signal (not a lock): the demo lets the operator launch a
    second upgrade of the same app on purpose if they really mean to, but we
    warn first — a duplicate publish is almost always an accidental double-click
    or two people driving the console at once. Matches by catalog entry (the
    product line), falling back to the superseded app id.
    """
    if not app_id:
        return None
    try:
        from autopackager.orchestration.engine import OrchestrationEngine
        from autopackager.utils import installer_catalog

        catalog = installer_catalog.load_catalog()
        entry, _ = intune_view.find_entry_for_app_id(catalog, app_id)
        target_entry = entry.id if entry else None

        for j in OrchestrationEngine().get_all_jobs():
            if (j.state.value if j.state else "") in _TERMINAL_JOB_STATES:
                continue
            md = j.job_metadata or {}
            if "_upgrade" not in md and "supersedence_action" not in md:
                continue
            up = md.get("_upgrade") or {}
            if up.get("old_app_id") == app_id:
                return j.id
            jeid = md.get("catalog_entry_id")
            if not jeid and up.get("old_app_id"):
                e2, _ = intune_view.find_entry_for_app_id(catalog, up["old_app_id"])
                jeid = e2.id if e2 else None
            if target_entry and jeid == target_entry:
                return j.id
    except Exception as exc:  # noqa: BLE001 — advisory only; never block the upgrade
        logger.warning("In-flight upgrade check failed", error=str(exc))
    return None


@demo_router.post("/api/demo/intune/upgrade")
async def api_upgrade(request: Request, background: BackgroundTasks):
    """Package + supersede + deploy a newer version (spec §3/§4).

    Two intake shapes:
      * JSON ``{app_id, scope, download_url?, mode?, gate?, old_entry_id?}`` —
        if ``download_url`` is reachable, the new build is fetched in the
        background (narrated over SSE) and the pipeline dispatched. If it's
        absent/unreachable, returns ``{awaiting_upload: true}`` so the operator
        drops the installer.
      * multipart ``file`` + ``app_id`` + ``scope`` (+gate) — the manual-upload
        fallback; the dropped installer is packaged directly.

    Returns ``{job_id, branch: "upgrade"}`` (or ``{awaiting_upload: true}``).
    """
    content_type = request.headers.get("content-type", "")
    try:
        if content_type.startswith("multipart/form-data"):
            form = await request.form()
            app_id = form.get("app_id")
            scope = (form.get("scope") or "test").lower()
            gate = _as_bool(form.get("gate"))
            old_entry_id = form.get("old_entry_id") or None
            upload = form.get("file")
            if not app_id or upload is None or not hasattr(upload, "filename"):
                return JSONResponse({"error": "app_id and file required"}, status_code=400)
            if not intake.is_known_installer(upload.filename):
                return JSONResponse(
                    {"error": f"Unsupported file type (got '{upload.filename}')."},
                    status_code=400,
                )
            if not _as_bool(form.get("force")):
                existing = await asyncio.to_thread(_inflight_upgrade_for_app, app_id)
                if existing:
                    return {
                        "in_flight": True, "existing_job_id": existing,
                        "warning": (f"An upgrade for this app is already in progress "
                                    f"(job #{existing}). Start another anyway?"),
                    }
            data = await upload.read()
            saved = await asyncio.to_thread(intake.save_upload, upload.filename, data)
            job_id = await asyncio.to_thread(
                intake.enqueue_upgrade_job, app_id, str(saved), scope,
            )
            return {"job_id": job_id, "branch": "upgrade"}

        body = await request.json()
        app_id = body.get("app_id")
        scope = (body.get("scope") or "test").lower()
        gate = _as_bool(body.get("gate"))
        mode = body.get("mode") or None
        download_url = body.get("download_url")
        old_entry_id = body.get("old_entry_id") or None
        if not app_id:
            return JSONResponse({"error": "app_id required"}, status_code=400)
        if scope not in ("test", "all"):
            return JSONResponse({"error": "scope must be 'test' or 'all'"}, status_code=400)
        # Soft concurrency guard (not a lock): warn if an upgrade for this
        # product is already running; the client re-POSTs with force=true to
        # proceed deliberately.
        if not _as_bool(body.get("force")):
            existing = await asyncio.to_thread(_inflight_upgrade_for_app, app_id)
            if existing:
                return {
                    "in_flight": True,
                    "existing_job_id": existing,
                    "warning": (f"An upgrade for this app is already in progress "
                                f"(job #{existing}). Start another anyway?"),
                }
        if not download_url or not intake.is_known_installer(download_url):
            # No client-supplied URL — ATTEMPT acquisition before asking for a
            # manual drop. Cascade (demo/queue.resolve_acquisition): catalog
            # canonical URL -> version-check brain -> agentic web search (live
            # only). "Always try first" before falling back to a drop.
            acq = await asyncio.to_thread(_resolve_upgrade_source, app_id, mode)
            cand_url = acq.get("download_url")
            src = acq.get("source")
            if cand_url and src in ("catalog", "version-check"):
                download_url = cand_url  # trusted source -> fetch below
            elif cand_url and src == "agent-search":
                # Agent-found URL is UNTRUSTED (supply-chain guardrail): surface
                # it for an operator confirm rather than auto-downloading. The
                # client re-POSTs with this URL as download_url to proceed.
                return {
                    "awaiting_confirm": True,
                    "app_id": app_id,
                    "scope": scope,
                    "proposed_url": cand_url,
                    "provenance": acq.get("provenance"),
                    "confidence": acq.get("confidence"),
                    "message": "Found a candidate source via web search — confirm to fetch it.",
                }
            else:
                # Tried the known source (and, in live mode, a web search) and
                # found nothing fetchable — NOW ask for a manual drop.
                return {
                    "awaiting_upload": True,
                    "app_id": app_id,
                    "scope": scope,
                    "message": "No source found automatically — drop the newer installer to continue.",
                }
        job_id = await asyncio.to_thread(intake.begin_upgrade_job, app_id, scope, gate=gate)
        background.add_task(
            _run_upgrade_pipeline, job_id, app_id, scope, download_url, gate, old_entry_id,
        )
        return {"job_id": job_id, "branch": "upgrade"}
    except Exception as exc:  # noqa: BLE001
        try:
            logger.error("Demo upgrade failed", error=str(exc), exc_info=True)
        except Exception:
            pass
        return JSONResponse({"error": str(exc)}, status_code=500)


def _live_app_label(app_id: Optional[str]):
    """Return ``(displayName, publisher)`` for a tenant app id, trimmed — used to
    give the upgrade's installer search a proper human product name. Best-effort:
    returns ``(None, None)`` on any failure (caller falls back to the slug)."""
    if not app_id:
        return None, None
    try:
        from autopackager.utils.graph_client import GraphAPIClient

        app = GraphAPIClient().get_win32_app(app_id) or {}
        name = (app.get("displayName") or "").strip() or None
        pub = (app.get("publisher") or "").strip() or None
        return name, pub
    except Exception as exc:  # noqa: BLE001
        try:
            logger.warning("Live app label lookup failed", app_id=app_id, error=str(exc))
        except Exception:
            pass
        return None, None


def _resolve_upgrade_source(app_id: Optional[str], mode: Optional[str]) -> dict:
    """Attempt to acquire a download URL for an upgrade BEFORE asking for a drop.

    Resolves the app to its catalog entry and runs the demo/queue acquisition
    cascade (catalog ``canonical_download_url`` -> version-check brain -> agentic
    web search, live only). Returns ``resolve_acquisition``'s dict
    (``{download_url, source, provenance, confidence}``). Never raises -- any
    failure resolves to "no URL" so the caller falls back to a manual drop.
    """
    try:
        from autopackager.utils import installer_catalog
        from demo import queue as demo_queue

        catalog = installer_catalog.load_catalog()
        entry, row = intune_view.find_entry_for_app_id(catalog, app_id)
        version = None
        if entry:
            newest, _newest_app = intune_view.newest_verified_version(entry)
            version = newest or (row or {}).get("product_version")
        # Search with the app's REAL display name + publisher, not the catalog
        # slug. The slug ("sharex", empty publisher) is a degraded query — the
        # agent web search needs the human product name ("ShareX", "ShareX Team")
        # to find the official latest installer. Fall back to the slug/app_id.
        disp_name, disp_pub = _live_app_label(app_id)
        candidate = {
            "name": (disp_name or (entry.id if entry else None) or app_id or "application"),
            "publisher": (disp_pub or (entry.publisher if entry else "") or ""),
            "version": version,
            "in_catalog": entry.id if entry else None,
        }
        return demo_queue.resolve_acquisition(candidate, mode=mode)
    except Exception as exc:  # noqa: BLE001
        try:
            logger.warning("Upgrade source resolution failed", app_id=app_id, error=str(exc))
        except Exception:
            pass
        return {"download_url": None, "source": None, "provenance": None, "confidence": None}


def _run_upgrade_pipeline(
    job_id: int, old_app_id: str, scope: str, download_url: str,
    gate: bool, old_entry_id: Optional[str],
):
    """Background: fetch the newer installer, attach upgrade metadata, dispatch.

    Mirrors ``_run_miss_pipeline`` — runs in the threadpool and narrates to the
    job's SSE channel throughout. The fetch is retried a few times before
    failing (transient network blips shouldn't sink an upgrade).
    """
    time.sleep(1.2)  # let the browser's SSE subscription come up first
    events.publish_pipeline_event(
        job_id, "pending", f"Fetching the newer version… ({download_url})",
    )
    saved = None
    last_exc: Optional[Exception] = None
    for attempt in range(1, 4):  # a few tries before giving up
        try:
            saved = intake.download_to_sandbox(download_url)
            break
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt < 3:
                events.publish_pipeline_event(
                    job_id, "pending",
                    f"Fetch attempt {attempt}/3 failed ({exc}); retrying…", level="warn")
                time.sleep(min(attempt * 1.5, 4))
    if saved is None:
        events.publish_pipeline_event(
            job_id, "failed",
            f"Could not fetch the newer installer after 3 tries: {last_exc}", level="error")
        try:
            from autopackager.orchestration.engine import OrchestrationEngine
            from autopackager.models.job import JobState
            OrchestrationEngine().update_job_state(
                job_id, JobState.FAILED, error_message=f"upgrade download failed: {last_exc}")
        except Exception:
            pass
        return
    events.publish_pipeline_event(
        job_id, "pending", f"Got the installer ({saved.name}) — packaging the upgrade now.",
    )
    intake.finalize_upgrade_job(
        job_id, old_app_id, str(saved), scope, gate=gate, old_entry_id=old_entry_id,
    )


@demo_router.post("/api/demo/jobs")
async def api_create_job(request: Request, background: BackgroundTasks):
    """Intake: drag-drop file (multipart), vendor URL or driver form (JSON).

    Returns ``{job_id, branch}``. On a catalog HIT the pipeline is dispatched
    immediately. On a MISS the job row is created, the research bridge runs in
    the background (streaming to the same SSE channel), and the pipeline is
    dispatched once the catalog has learned the installer.
    """
    content_type = request.headers.get("content-type", "")

    gate = False
    mode: Optional[str] = None

    try:
        if content_type.startswith("multipart/form-data"):
            form = await request.form()
            gate = _as_bool(form.get("gate"))
            mode = form.get("mode") or None
            upload = form.get("file")
            if upload is None or not hasattr(upload, "filename"):
                return JSONResponse({"error": "no file provided"}, status_code=400)
            if not intake.is_known_installer(upload.filename):
                return JSONResponse(
                    {"error": f"Unsupported file type — only .msi, .exe, or .zip "
                              f"are accepted (got '{upload.filename}')."},
                    status_code=400,
                )
            data = await upload.read()
            saved = await asyncio.to_thread(intake.save_upload, upload.filename, data)
            return await _handle_installer(saved, gate, mode, background)

        # JSON body: url OR driver form
        body = await request.json()
        gate = _as_bool(body.get("gate"))
        mode = body.get("mode") or None

        if body.get("url"):
            if not intake.is_known_installer(body["url"]):
                return JSONResponse(
                    {"error": "URL must point to a direct .msi, .exe, or .zip installer link."},
                    status_code=400,
                )
            saved = await asyncio.to_thread(intake.download_to_sandbox, body["url"])
            return await _handle_installer(saved, gate, mode, background)

        if body.get("vendor") and body.get("model"):
            job_id = await asyncio.to_thread(
                intake.enqueue_driver_job,
                body["vendor"], body["model"],
                body.get("driver_type"), body.get("current_version"),
            )
            return {"job_id": job_id, "branch": "driver"}

        return JSONResponse(
            {"error": "provide a file, a url, or vendor+model"}, status_code=400,
        )
    except Exception as exc:  # noqa: BLE001
        # Guard the log call: a console-encoding hiccup must not mask the
        # JSON error response the client needs.
        try:
            logger.error("Demo intake failed", error=str(exc), exc_info=True)
        except Exception:
            pass
        return JSONResponse({"error": str(exc)}, status_code=500)


async def _handle_installer(
    saved_path: Path, gate: bool, mode: Optional[str], background: BackgroundTasks
):
    """Analyze a saved installer and route HIT vs MISS."""
    # Safety net: never analyze/run an unknown type even if a future caller
    # forgets to validate. Remove the staged file so junk doesn't accumulate.
    if not intake.is_known_installer(saved_path.name):
        try:
            saved_path.unlink()
        except OSError:
            pass
        return JSONResponse(
            {"error": f"Unsupported file type '{saved_path.suffix}' — "
                      "only .msi, .exe, or .zip are accepted."},
            status_code=400,
        )
    analysis = await asyncio.to_thread(intake.analyze, saved_path)

    # Known non-packageable (escalate / don't package): the matched catalog
    # entry carries an escalate_reason (e.g. RealPlayer bundleware). Escalate
    # immediately — create a job row for the record + SSE channel, mark it
    # failed/engineer-review, and run NO packaging or install attempt.
    if analysis.escalate:
        job_id = await asyncio.to_thread(intake.create_software_job_row, analysis, gate=gate)
        background.add_task(_run_escalation, job_id, analysis.escalate_reason or
                            "Known non-packageable installer — engineer review required.")
        return {"job_id": job_id, "branch": "escalate", "analysis": analysis.to_dict()}

    # Consumer-vs-enterprise: the dropped installer matched a consumer build
    # that redirects to a better (enterprise) one. Create the job, then fetch
    # the right installer on the user's behalf with live console feedback.
    if analysis.prefer_entry_id:
        job_id = await asyncio.to_thread(intake.create_software_job_row, analysis, gate=gate)
        background.add_task(_run_substitution_pipeline, job_id, str(saved_path), gate, mode)
        return {"job_id": job_id, "branch": "substituted", "analysis": analysis.to_dict()}

    if analysis.branch == "hit":
        job_id = await asyncio.to_thread(intake.enqueue_software_job, analysis, gate=gate)
        # Narrate the deterministic branch as the very first console line.
        events.publish_pipeline_event(
            job_id, "pending",
            f"Catalog hit — deterministic package ({analysis.catalog_entry_id})",
        )
        return {
            "job_id": job_id, "branch": "hit",
            "analysis": analysis.to_dict(),
        }

    # MISS — create the row first so the research bridge has a channel, then
    # run research in the background and dispatch once the catalog has learned.
    job_id = await asyncio.to_thread(intake.create_software_job_row, analysis, gate=gate)
    background.add_task(_run_miss_pipeline, job_id, str(saved_path), gate, mode)
    return {
        "job_id": job_id, "branch": "miss",
        "analysis": analysis.to_dict(),
    }


def _run_escalation(job_id: int, reason: str):
    """Background: mark a known non-packageable installer as failed + engineer
    escalation and narrate it. No packaging, no install attempt, no dispatch."""
    time.sleep(1.2)  # let the browser's SSE subscription come up first
    events.publish_pipeline_event(
        job_id, "pending", f"Known non-packageable installer — {reason}", level="warn")
    events.publish_pipeline_event(
        job_id, "failed",
        f"⛔ Failed — ENGINEER ESCALATION: {reason} Nothing was installed or published.",
        level="error", escalation=True)
    try:
        from autopackager.orchestration.engine import OrchestrationEngine
        from autopackager.models.job import JobState
        OrchestrationEngine().update_job_state(
            job_id, JobState.FAILED, error_message=reason,
            metadata_update={"needs_engineer_review": True, "escalation_reason": reason})
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not mark escalation job failed", job_id=job_id, error=str(exc))
    events.publish_end(job_id, ok=False, text="escalated")


def _run_miss_pipeline(job_id: int, saved_path: str, gate: bool, mode: Optional[str]):
    """Background: research the unknown installer, then dispatch the pipeline.

    Runs in FastAPI's threadpool (this is a sync function). Publishes to the
    job's SSE channel throughout.
    """
    # Small cushion so the browser's SSE subscription is live before the first
    # research line is published (Redis pub/sub has no backlog).
    time.sleep(1.2)
    events.publish_pipeline_event(
        job_id, "pending", "Catalog miss — invoking research agent.",
    )
    analysis = intake.analyze(Path(saved_path))
    claude_bridge.research_and_learn(job_id, analysis, mode=mode)

    # Re-analyze: the catalog should now resolve this installer as a HIT.
    refined = intake.analyze(Path(saved_path))
    if refined.branch == "hit":
        events.publish_pipeline_event(
            job_id, "pending",
            f"Re-resolved as catalog hit ({refined.catalog_entry_id}) — packaging now deterministic.",
        )
    intake.update_software_metadata(job_id, refined)
    intake.dispatch_pipeline(job_id, gate=gate)


def _run_substitution_pipeline(job_id: int, saved_path: str, gate: bool, mode: Optional[str]):
    """Background: a consumer build was dropped — surface the caveat, fetch the
    enterprise installer, and run the real pipeline on THAT. If no enterprise
    alternative is fetchable, fail loudly rather than publish the consumer build.
    """
    time.sleep(1.2)  # let the browser's SSE subscription come up first
    analysis = intake.analyze(Path(saved_path))  # consumer build: has prefer + caveats
    events.publish_pipeline_event(
        job_id, "pending",
        f"Consumer build detected ({analysis.catalog_entry_id or analysis.filename}) — "
        "not a managed-deployment target.", level="warn")
    if analysis.consumer_caveats:
        events.publish_pipeline_event(job_id, "pending", analysis.consumer_caveats, level="warn")
    events.publish_pipeline_event(
        job_id, "pending", "Fetching the enterprise installer on your behalf…")
    try:
        substitute = intake.substitute_with_enterprise(analysis)
    except Exception as exc:  # noqa: BLE001
        substitute = None
        events.publish_pipeline_event(
            job_id, "pending", f"Could not fetch the enterprise build: {exc}", level="error")
    if not substitute:
        events.publish_pipeline_event(
            job_id, "failed",
            "No enterprise alternative available — refusing to publish the consumer build.",
            level="error")
        try:
            from autopackager.orchestration.engine import OrchestrationEngine
            from autopackager.models.job import JobState
            OrchestrationEngine().update_job_state(
                job_id, JobState.FAILED,
                error_message="consumer build; no enterprise substitute available")
        except Exception:
            pass
        return
    events.publish_pipeline_event(
        job_id, "pending",
        f"Got the enterprise build: {substitute.product_name or substitute.filename} "
        f"{substitute.version or ''} ({substitute.catalog_entry_id}) — packaging this instead.")
    intake.update_software_metadata(job_id, substitute)
    intake.dispatch_pipeline(job_id, gate=gate)


@demo_router.post("/api/demo/jobs/{job_id}/approve")
async def api_approve(job_id: int):
    """Release the optional Ring 0 approval gate and dispatch deployment."""
    events.set_gate_approved(job_id)
    # Persist the approval on the job BEFORE dispatching deployment so the
    # pipeline-level gate backstop (deployment_task) lets this run through.
    await asyncio.to_thread(_mark_gate_approved, job_id)
    events.publish_pipeline_event(job_id, "deploying", "Approved — promoting to Ring 0.")
    await asyncio.to_thread(intake.dispatch_deployment, job_id)
    return {"job_id": job_id, "approved": True}


def _mark_gate_approved(job_id: int) -> None:
    """Persist gate_approved=True on the job so deployment_task's gate backstop
    permits the deploy. Best-effort (the Redis flag is the primary signal)."""
    try:
        from autopackager.orchestration.engine import OrchestrationEngine

        engine = OrchestrationEngine()
        job = engine.get_job(job_id)
        if job and job.state:
            engine.update_job_state(job_id, job.state, metadata_update={"gate_approved": True})
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not persist gate_approved", job_id=job_id, error=str(exc))


@demo_router.get("/api/demo/jobs/{job_id}/logs")
async def api_job_logs(job_id: int):
    """Human-readable diagnostic log for a job — error + escalation reason +
    install attempts + the local-install validator log + smoke-test results. Lets
    an engineer inspect a FAILED execution (the 'View logs' card action)."""
    text = await asyncio.to_thread(_assemble_job_logs, job_id)
    if text is None:
        return JSONResponse({"error": f"job {job_id} not found"}, status_code=404)
    return {"job_id": job_id, "logs": text}


def _assemble_job_logs(job_id: int) -> Optional[str]:
    from autopackager.orchestration.engine import OrchestrationEngine

    job = OrchestrationEngine().get_job(job_id)
    if not job:
        return None
    md = job.job_metadata or {}
    out = [f"# Job {job_id} — {job.software_title or ''}".rstrip(),
           f"state: {job.state.value if job.state else '?'}"]
    if job.error_message:
        out.append(f"\n## Error\n{job.error_message}")
    diag_keys = ("install_command", "corrected_install_command", "needs_engineer_review",
                 "escalation_reason", "install_attempts", "download_url", "sha256",
                 "catalog_entry_id", "target_version")
    diag = {k: md.get(k) for k in diag_keys if md.get(k) is not None}
    if diag:
        out.append("\n## Diagnostics")
        out.extend(f"{k}: {v}" for k, v in diag.items())
    pkg_id = md.get("package_id")
    if pkg_id:
        try:
            from autopackager.utils.database import db_session_scope
            from autopackager.models.package import Package

            with db_session_scope() as s:
                pkg = s.query(Package).filter(Package.id == pkg_id).first()
                if pkg and pkg.test_logs:
                    out.append("\n## Test logs\n" + str(pkg.test_logs))
        except Exception as exc:  # noqa: BLE001
            out.append(f"\n(test logs unavailable: {exc})")
    return "\n".join(out)


@demo_router.post("/api/demo/jobs/{job_id}/retry")
async def api_retry_job(job_id: int):
    """Re-run a failed job's pipeline (the 'Retry' card action). Clears the
    failure flags, resets to a fresh start, and re-dispatches — gating is
    preserved so a queue item still holds at the approval gate."""
    ok = await asyncio.to_thread(_retry_job, job_id)
    if not ok:
        return JSONResponse({"error": f"job {job_id} not found"}, status_code=404)
    return {"job_id": job_id, "retried": True}


def _retry_job(job_id: int) -> bool:
    from autopackager.orchestration.engine import OrchestrationEngine
    from autopackager.models.job import JobState

    engine = OrchestrationEngine()
    job = engine.get_job(job_id)
    if not job:
        return False
    gate = bool((job.job_metadata or {}).get("demo_gate_deploy"))
    # Clear the escalation flags so a fresh validator run isn't pre-judged, reset
    # to a clean pipeline start, and re-dispatch.
    engine.update_job_state(
        job_id, JobState.PENDING,
        metadata_update={"needs_engineer_review": False, "escalation_reason": None,
                         "retry_requested": True},
    )
    events.publish_pipeline_event(job_id, "pending", "↻ Retry requested — re-running the pipeline…")
    intake.dispatch_pipeline(job_id, gate=gate)
    return True


@demo_router.get("/api/demo/stream/{job_id}")
async def api_stream(job_id: int):
    """Server-Sent Events stream for one job's console + lamp + stepper."""

    async def event_gen():
        # Initial hello so the client knows the stream is live.
        yield _sse({"type": "hello", "job_id": job_id})
        # Emit the job's CURRENT state from the DB so the stepper is correct
        # even if early events were missed or the browser reconnected (Redis
        # pub/sub has no backlog). Best-effort.
        try:
            state = await asyncio.to_thread(_current_job_state, job_id)
            if state:
                yield _sse({"type": "state", "source": "pipeline", "state": state})
        except Exception:
            pass
        try:
            async for envelope in events.asubscribe(job_id):
                if envelope is None:
                    # Idle keep-alive comment (prevents proxy timeouts).
                    yield ": keep-alive\n\n"
                    continue
                yield _sse(envelope)
                if envelope.get("type") == "end":
                    break
        except asyncio.CancelledError:  # client disconnected
            raise
        except Exception as exc:  # noqa: BLE001
            yield _sse({"type": "console", "level": "error",
                        "source": "system", "text": f"stream error: {exc}"})

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


_NO_CACHE = {"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache"}


@demo_router.get("/demo")
async def demo_index():
    index = _STATIC / "index.html"
    if index.exists():
        # No-cache so an operator iterating on the demo always gets the latest
        # UI without a hard-refresh (the demo is actively developed against).
        return FileResponse(str(index), headers=_NO_CACHE)
    return JSONResponse({"error": "demo UI not found"}, status_code=404)


@demo_router.get("/demo/stream")
async def demo_stream_page():
    """The batch-stream page: a live grid of all packages in a queue batch, each
    independently actionable (approve / confirm-url / drop-installer). Opened
    with ``?batch=<batch_id>`` from the console when a batch is queued."""
    page = _STATIC / "stream.html"
    if page.exists():
        return FileResponse(str(page), headers=_NO_CACHE)
    return JSONResponse({"error": "stream UI not found"}, status_code=404)


@demo_router.get("/api/demo/queue/{batch_id}/snapshot")
async def api_queue_snapshot(batch_id: str):
    """Initial render data for the batch-stream page: one entry per queued job.

    The live SSE (below) carries only deltas — this gives the page the full set
    of cards (and their current state) on load / reconnect."""
    jobs = await asyncio.to_thread(pkg_queue.jobs_for_batch, batch_id)
    return {"batch_id": batch_id, "jobs": jobs}


@demo_router.get("/api/demo/stream/batch/{batch_id}")
async def api_stream_batch(batch_id: str):
    """Fan-in SSE for a whole queue batch: every job's console + lamp + stepper
    events on one stream, each tagged with its ``job_id`` so the page can route
    it to the right card. Resolves the batch's job ids up front (the set is fixed
    at batch creation)."""

    async def event_gen():
        jobs = await asyncio.to_thread(pkg_queue.jobs_for_batch, batch_id)
        job_ids = [j["job_id"] for j in jobs]
        yield _sse({"type": "hello", "batch_id": batch_id, "job_ids": job_ids})
        # Seed each card with its current DB state (Redis pub/sub has no backlog).
        for j in jobs:
            yield _sse({"type": "state", "source": "pipeline",
                        "job_id": j["job_id"], "state": j.get("state")})
        if not job_ids:
            yield _sse({"type": "end", "batch_id": batch_id})
            return
        try:
            async for envelope in events.asubscribe_many(job_ids):
                if envelope is None:
                    yield ": keep-alive\n\n"
                    continue
                yield _sse(envelope)
        except asyncio.CancelledError:  # client disconnected
            raise
        except Exception as exc:  # noqa: BLE001
            yield _sse({"type": "console", "level": "error",
                        "source": "system", "text": f"stream error: {exc}"})

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _current_job_state(job_id: int) -> Optional[str]:
    """Read a job's current state string from the DB (for stream resync)."""
    try:
        from autopackager.orchestration.engine import OrchestrationEngine

        job = OrchestrationEngine().get_job(job_id)
        return job.state.value if job and job.state else None
    except Exception:
        return None


def _sse(payload: Any) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def mount_demo(app) -> None:
    """Attach the demo router + static assets to the existing FastAPI app.

    Called once, additively, from ``autopackager/web/api.py``. Safe to remove.
    """
    app.include_router(demo_router)
    if _STATIC.exists():
        app.mount("/demo/static", _NoCacheStatic(directory=str(_STATIC)), name="demo_static")
    logger.info("Demo console mounted at /demo")


class _NoCacheStatic(StaticFiles):
    """StaticFiles that tells the browser never to cache demo assets. The demo
    is actively iterated on; cached demo.js/css otherwise leaves an operator
    staring at a stale UI after a fix until they hard-refresh."""

    def is_not_modified(self, response_headers, request_headers) -> bool:  # noqa: D401
        return False  # never serve a 304 — always send the current bytes

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers.update(_NO_CACHE)
        return response
