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

logger = get_logger(__name__)

_STATIC = Path(__file__).resolve().parent / "static"

demo_router = APIRouter()


@demo_router.get("/api/demo/preflight")
async def api_preflight():
    """Run readiness checks. Slightly slow in live mode (Claude health check)."""
    return await asyncio.to_thread(preflight.run_all)


@demo_router.get("/api/demo/intune/apps")
async def api_intune_apps(counts: bool = False):
    view = await asyncio.to_thread(intune_view.get_apps_view, counts)
    return view


@demo_router.get("/api/demo/intune/verify-url")
async def api_verify_url(app_id: Optional[str] = None):
    return {"url": intune_view.verify_in_intune_url(app_id)}


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


def _check_version_sync(body: dict, app_id: Optional[str]) -> dict:
    from autopackager.utils import installer_catalog

    catalog = installer_catalog.load_catalog()
    entry, row = intune_view.find_entry_for_app_id(catalog, app_id)
    if entry:
        current_version = (row or {}).get("product_version") or body.get("current_version")
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
    return result


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
        if not download_url or not intake.is_known_installer(download_url):
            # No fetchable source — fall back to a manual drop.
            return {
                "awaiting_upload": True,
                "app_id": app_id,
                "scope": scope,
                "message": "Source unavailable — awaiting manual upload.",
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


def _run_upgrade_pipeline(
    job_id: int, old_app_id: str, scope: str, download_url: str,
    gate: bool, old_entry_id: Optional[str],
):
    """Background: fetch the newer installer, attach upgrade metadata, dispatch.

    Mirrors ``_run_miss_pipeline`` — runs in the threadpool and narrates to the
    job's SSE channel throughout.
    """
    time.sleep(1.2)  # let the browser's SSE subscription come up first
    events.publish_pipeline_event(
        job_id, "pending", f"Fetching the newer version… ({download_url})",
    )
    try:
        saved = intake.download_to_sandbox(download_url)
    except Exception as exc:  # noqa: BLE001
        events.publish_pipeline_event(
            job_id, "failed", f"Could not fetch the newer installer: {exc}", level="error")
        try:
            from autopackager.orchestration.engine import OrchestrationEngine
            from autopackager.models.job import JobState
            OrchestrationEngine().update_job_state(
                job_id, JobState.FAILED, error_message=f"upgrade download failed: {exc}")
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
    events.publish_pipeline_event(job_id, "deploying", "Approved — promoting to Ring 0.")
    await asyncio.to_thread(intake.dispatch_deployment, job_id)
    return {"job_id": job_id, "approved": True}


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


@demo_router.get("/demo")
async def demo_index():
    index = _STATIC / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return JSONResponse({"error": "demo UI not found"}, status_code=404)


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
        app.mount("/demo/static", StaticFiles(directory=str(_STATIC)), name="demo_static")
    logger.info("Demo console mounted at /demo")
