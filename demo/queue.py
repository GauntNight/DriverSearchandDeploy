"""Packaging queue — turn the software-delta backlog into packaging jobs.

The unmanaged-software delta (``autopackager.services.software_delta``) yields
*identities* — ``{name, publisher, version, in_catalog, ...}`` — but the whole
pipeline (discovery → packaging → testing → deployment) starts from an
*installer file on disk*. This module bridges that gap: given a selected
candidate it **acquires** an installer (catalog ``canonical_download_url`` →
else the version-check research bridge), then hands the file to the EXISTING
``demo.intake`` enqueue path. No new pipeline, no new DB table — a queued item
is just a ``Job`` row tagged with a ``queue_origin`` marker, so the demo's
existing job/SSE machinery visualizes it for free.

Posture (operator decision): queued items are always **gated** (discovery →
packaging → testing only; deployment held for the demo ``/approve`` gate) and
**test scope** (Ring 0). A bulk select must never silently write the tenant.

Serialization: items are processed **one at a time**. The single Celery worker
already serializes the box-bound install validation, but processing the batch
sequentially also makes the "one action at a time" UX honest and makes cancel
responsive — the runner checks the job's DB state (the cancel signal) before and
during each item, and only dispatches the next once the current one has settled
(reached the approval gate, completed, or failed).

Everything here is additive and lives under the removable ``demo/`` package.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from autopackager.models.job import JobState, JobType
from autopackager.utils.logger import get_logger
from demo import events, intake, claude_bridge

logger = get_logger(__name__)

# Marker key stamped into job_metadata so a job born from the delta is
# identifiable as a queue item (and carries its originating candidate identity).
QUEUE_ORIGIN_KEY = "queue_origin"

# Terminal job states — the runner stops waiting on an item once it reaches one.
_TERMINAL = {JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED}

# Overall per-item wait backstop (seconds). A real packaging+test (with a local
# install validation) can take minutes; this only guards against a wedged item
# so the batch can't hang forever.
_SETTLE_TIMEOUT = 1800


# --- Acquisition -----------------------------------------------------------

def resolve_acquisition(
    candidate: Dict[str, Any], *, mode: Optional[str] = None, job_id: Any = None
) -> Dict[str, Optional[str]]:
    """Resolve a download URL for a delta candidate.

    Strategy (each step's ``source`` drives the trust decision downstream):
      1. ``catalog`` — ``known_packageable`` whose catalog entry carries a
         ``canonical_download_url`` (operator-curated; trusted, auto-proceeds).
      2. ``version-check`` — the version-check brain returns a download URL,
         using the catalog entry's source URL as a hint (known product; trusted).
      3. ``agent-search`` — for a genuinely UNKNOWN candidate, the agent searches
         the web for the official installer (LIVE only). This URL is NOT trusted:
         the caller parks the item for an operator confirm before downloading.

    Returns ``{"download_url", "source", "latest_version", "provenance",
    "confidence"}`` (``download_url`` None → caller parks for a manual drop).
    Never raises.
    """
    name = candidate.get("name") or ""
    publisher = candidate.get("publisher") or ""
    version = candidate.get("version")
    entry_id = candidate.get("in_catalog") or candidate.get("catalog_entry_id")

    out: Dict[str, Optional[str]] = {
        "download_url": None, "source": None, "latest_version": version,
        "provenance": None, "confidence": None,
    }
    label = (f"{name} {publisher}".strip()) or entry_id or "application"
    slug = entry_id or name

    # KNOWN product (has a catalog entry): trust the curated catalog URL, then the
    # version-check brain (the catalog gives us a product + source context).
    entry = None
    if entry_id:
        try:
            from autopackager.utils import installer_catalog

            entry = installer_catalog.load_catalog().by_id(entry_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Queue: catalog lookup failed", entry_id=entry_id, error=str(exc))
    if entry:
        if entry.canonical_download_url:
            out.update(download_url=entry.canonical_download_url, source="catalog")
            return out
        try:
            res = claude_bridge.check_version(
                label, version, entry.canonical_download_url,
                mode=mode, job_id=job_id, slug=slug,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Queue: version-check bridge failed", candidate=name, error=str(exc))
            res = {}
        url = (res.get("download_url") or "").strip() or None
        if url and intake.is_known_installer(url):
            out.update(download_url=url, source="version-check",
                       latest_version=res.get("latest_version") or version)
            return out
        # Known product but no curated/version URL — fall through to a web search,
        # which (being agent-found) still requires operator confirm.

    # UNKNOWN candidate (or a known product with no resolvable URL): the agent
    # searches the web for the official installer (LIVE only). This URL is NOT
    # trusted — the caller requires an operator confirm before download/install.
    try:
        found = claude_bridge.find_installer_url(
            name, publisher, mode=mode, job_id=job_id, slug=slug)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Queue: installer search failed", candidate=name, error=str(exc))
        found = {}
    furl = (found.get("download_url") or "").strip() or None
    if furl and intake.is_known_installer(furl):
        out.update(download_url=furl, source="agent-search",
                   provenance=found.get("provenance"), confidence=found.get("confidence"))
        return out

    return out  # nothing found → caller parks for a manual installer drop


# --- Job-row creation (the queue is just tagged Job rows) -------------------

def create_queue_job_row(
    candidate: Dict[str, Any], *, batch_id: str, scope: str = "test"
) -> int:
    """Create the PENDING Job row for a queued candidate (no dispatch yet).

    Stamps a ``queue_origin`` block so the row is identifiable as a queue item
    and carries the originating identity. Always gated (deployment held for the
    approval gate). Returns the job id.
    """
    name = candidate.get("name") or "Unknown software"
    publisher = candidate.get("publisher") or "Unknown"
    origin = {
        "name": name,
        "publisher": candidate.get("publisher"),
        "version": candidate.get("version"),
        "bucket": candidate.get("bucket"),
        "in_catalog": candidate.get("in_catalog") or candidate.get("catalog_entry_id"),
        "device_count": candidate.get("device_count"),
        "batch_id": batch_id,
        "scope": scope,
        "state": "queued",
    }
    return intake.create_job_row(
        job_type=JobType.NEW_SOFTWARE,
        software_title=name,
        vendor=publisher,
        current_version=candidate.get("version"),
        job_metadata={QUEUE_ORIGIN_KEY: origin},
        gate=True,  # queue items are always gated — never auto-write the tenant
    )


# --- Batch lookup (for the batch-stream page) ------------------------------

def jobs_for_batch(batch_id: str) -> List[Dict[str, Any]]:
    """Return the queue jobs belonging to ``batch_id``, newest-created first.

    Each item: ``{job_id, name, publisher, version, state, origin_state,
    proposed_url}``. ``state`` is the live DB job state (``pending`` |
    ``discovering`` | ... | ``failed``); ``origin_state`` is the demo sub-state
    persisted in the ``queue_origin`` marker (``acquiring`` |
    ``awaiting_installer`` | ``awaiting_confirm`` | ``packaging`` | ...). The
    latter is what lets the batch-stream page reseed a PARKED action (drop /
    confirm) on a late-join or refresh, since those prompts arrive only as live
    SSE events (Redis pub/sub has no backlog). Pure DB read — no Graph/Redis.
    """
    from autopackager.orchestration.engine import OrchestrationEngine

    out: List[Dict[str, Any]] = []
    try:
        for job in OrchestrationEngine().get_all_jobs():
            md = job.job_metadata or {}
            origin = md.get(QUEUE_ORIGIN_KEY) or {}
            if origin.get("batch_id") != batch_id:
                continue
            out.append({
                "job_id": job.id,
                "name": origin.get("name") or job.software_title or f"job {job.id}",
                "publisher": origin.get("publisher"),
                "version": origin.get("version"),
                "state": job.state.value if job.state else "pending",
                "origin_state": origin.get("state"),
                "proposed_url": md.get("queue_proposed_url"),
            })
    except Exception as exc:  # noqa: BLE001
        logger.warning("jobs_for_batch failed", batch_id=batch_id, error=str(exc))
    # get_all_jobs is ordered newest-first by id; keep that for a stable grid.
    return out


# --- Cancel ----------------------------------------------------------------

def _is_cancelled(job_id: int) -> bool:
    """True if the job has been marked CANCELLED (the queue's cancel signal)."""
    try:
        from autopackager.orchestration.engine import OrchestrationEngine

        job = OrchestrationEngine().get_job(job_id)
        return bool(job and job.state == JobState.CANCELLED)
    except Exception:  # noqa: BLE001
        return False


def cancel_job(job_id: int, *, reason: str = "cancelled by operator") -> bool:
    """Mark a job CANCELLED and close its SSE stream.

    Best-effort. A job already mid-flight in a Celery stage won't halt that
    in-progress stage (Celery acks_late redelivery), but no FURTHER stage runs
    and the runner stops advancing the batch — which is what the operator's
    Cancel button promises.
    """
    from autopackager.orchestration.engine import OrchestrationEngine

    try:
        job = OrchestrationEngine().get_job(job_id)
        if job and job.state in _TERMINAL:
            return False  # already settled; nothing to cancel
        OrchestrationEngine().update_job_state(
            job_id, JobState.CANCELLED, error_message=reason,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Queue: cancel failed", job_id=job_id, error=str(exc))
        return False
    events.publish_pipeline_event(job_id, "failed", f"⊘ Cancelled — {reason}", level="warn")
    events.publish_end(job_id, ok=False, text="cancelled")
    return True


def cancel_batch(job_ids: List[int], *, reason: str = "batch cancelled") -> int:
    """Cancel every not-yet-terminal job in a batch. Returns the count cancelled."""
    return sum(1 for jid in job_ids if cancel_job(jid, reason=reason))


# --- Settle wait (let one item finish before the next starts) --------------

def _wait_for_settle(job_id: int, timeout: int = _SETTLE_TIMEOUT) -> str:
    """Block until a dispatched item is done with its machine-bound work.

    A gated item has NO distinct "awaiting approval" job state — it parks in
    TESTING after the test passes — so the reliable settle signals come over the
    job's demo Redis channel: a ``gate=True`` console event (gated test passed),
    or a ``state in {completed, failed}`` event. We subscribe BEFORE the caller
    dispatches (caller ordering) so there's no missed-event race, and also poll
    the DB state each tick to catch an operator Cancel (which lands as a DB
    CANCELLED, not a pipeline event).

    Returns one of: ``"gate"``, ``"completed"``, ``"failed"``, ``"cancelled"``,
    ``"timeout"``. Falls back to pure state-polling if Redis pub/sub is absent.
    """
    deadline = time.monotonic() + timeout
    client = events._client()
    pubsub = None
    if client is not None:
        try:
            pubsub = client.pubsub()
            pubsub.subscribe(events.channel_for(job_id))
        except Exception:  # noqa: BLE001
            pubsub = None

    try:
        while time.monotonic() < deadline:
            if _is_cancelled(job_id):
                return "cancelled"
            # Terminal DB state is authoritative even without an event.
            settled = _terminal_reason(job_id)
            if settled:
                return settled

            if pubsub is None:
                time.sleep(1.0)
                continue
            try:
                msg = pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            except Exception:  # noqa: BLE001
                time.sleep(1.0)
                continue
            if not msg:
                continue
            env = _decode(msg.get("data"))
            if not env:
                continue
            if env.get("gate") is True:
                return "gate"
            state = env.get("state")
            if state == "completed":
                return "completed"
            if state == "failed":
                return "failed"
        return "timeout"
    finally:
        if pubsub is not None:
            try:
                pubsub.unsubscribe(events.channel_for(job_id))
                pubsub.close()
            except Exception:  # noqa: BLE001
                pass


def _terminal_reason(job_id: int) -> Optional[str]:
    try:
        from autopackager.orchestration.engine import OrchestrationEngine

        job = OrchestrationEngine().get_job(job_id)
        if not job:
            return None
        if job.state == JobState.COMPLETED:
            return "completed"
        if job.state == JobState.FAILED:
            return "failed"
        if job.state == JobState.CANCELLED:
            return "cancelled"
    except Exception:  # noqa: BLE001
        return None
    return None


def _decode(data: Any) -> Optional[Dict[str, Any]]:
    if not data:
        return None
    import json

    try:
        return json.loads(data)
    except (ValueError, TypeError):
        return None


# --- Per-item acquire + package -------------------------------------------

def acquire_and_package(
    job_id: int, candidate: Dict[str, Any], *, mode: Optional[str] = None,
) -> str:
    """Acquire an installer for ``candidate`` and dispatch the gated pipeline.

    Returns a short outcome tag: ``"dispatched"``, ``"awaiting_confirm"``,
    ``"awaiting_installer"``, ``"failed"``, or ``"cancelled"``. Narrates over the
    job's SSE channel. Mirrors ``demo.router._run_miss_pipeline`` but starts from
    an identity instead of a dropped file.

    Trust gate: a curated catalog URL or a version-check URL (known product)
    proceeds automatically; an **agent-searched** URL (genuinely unknown app) is
    parked for an operator confirm before anything is downloaded/installed — the
    supply-chain guardrail (ChromeSetup-stub / RealPlayer-bundleware lessons).
    """
    name = candidate.get("name") or "software"
    if _is_cancelled(job_id):
        return "cancelled"

    _set_origin_state(job_id, "acquiring")
    events.publish_lamp(job_id, "thinking", "finding installer…")
    events.publish_pipeline_event(
        job_id, "pending", f"Queued — finding an installer for {name}…",
    )

    acq = resolve_acquisition(candidate, mode=mode, job_id=job_id)
    url = acq.get("download_url")
    source = acq.get("source")
    if not url:
        # No fetchable source: park for a manual installer drop (mirrors the
        # upgrade path's awaiting_upload fallback). Leave the row PENDING.
        _set_origin_state(job_id, "awaiting_installer")
        events.publish_pipeline_event(
            job_id, "pending",
            f"No download source resolved for {name} — drop the installer to continue.",
            level="warn", awaiting_installer=True,
        )
        events.publish_lamp(job_id, "ready", "authenticated · standing by")
        return "awaiting_installer"

    if source == "agent-search":
        # Agent-FOUND URL → require operator confirm before download/install.
        provenance = acq.get("provenance") or "agent web search"
        confidence = acq.get("confidence") or "unknown"
        _merge_metadata(job_id, {
            "queue_proposed_url": url,
            "queue_url_provenance": provenance,
            "queue_url_confidence": confidence,
        })
        _set_origin_state(job_id, "awaiting_confirm")
        events.publish_pipeline_event(
            job_id, "pending",
            f"Found a candidate installer for {name} (confidence: {confidence}). "
            f"Source: {provenance}. Confirm before download/install:\n{url}",
            level="warn", awaiting_confirm=True, proposed_url=url,
            provenance=provenance, confidence=confidence,
        )
        events.publish_lamp(job_id, "ready", "authenticated · standing by")
        return "awaiting_confirm"

    # Trusted source (catalog / version-check) → proceed automatically.
    events.publish_pipeline_event(
        job_id, "pending", f"Found installer ({source}) — fetching {url}",
    )
    return _download_then(job_id, url, mode=mode)


def _download_then(job_id: int, url: str, *, mode: Optional[str] = None) -> str:
    """Download ``url`` into the sandbox, then analyze + dispatch. Tag outcome."""
    from autopackager.orchestration.engine import OrchestrationEngine

    try:
        saved = intake.download_to_sandbox(url)
    except Exception as exc:  # noqa: BLE001
        OrchestrationEngine().update_job_state(
            job_id, JobState.FAILED, error_message=f"queue download failed: {exc}",
        )
        events.publish_pipeline_event(
            job_id, "failed", f"Could not fetch the installer: {exc}", level="error",
        )
        events.publish_end(job_id, ok=False, text="download failed")
        return "failed"
    if _is_cancelled(job_id):
        return "cancelled"
    return _analyze_and_dispatch(job_id, str(saved), mode=mode)


def _analyze_and_dispatch(job_id: int, saved_path: str, *, mode: Optional[str] = None) -> str:
    """Analyze a staged installer (escalate/substitute/research as needed) and
    dispatch the gated pipeline. Shared by acquire / confirm / manual-drop."""
    from pathlib import Path
    from autopackager.orchestration.engine import OrchestrationEngine

    analysis = intake.analyze(Path(saved_path))

    # Known non-packageable (e.g. RealPlayer bundleware): escalate, install
    # nothing — exactly the dropped-file behaviour.
    if analysis.escalate:
        reason = analysis.escalate_reason or "Known non-packageable installer."
        OrchestrationEngine().update_job_state(
            job_id, JobState.FAILED, error_message=reason,
            metadata_update={"needs_engineer_review": True, "escalation_reason": reason},
        )
        events.publish_pipeline_event(
            job_id, "failed",
            f"⛔ Failed — ENGINEER ESCALATION: {reason} Nothing was installed or published.",
            level="error", escalation=True,
        )
        events.publish_end(job_id, ok=False, text="escalated")
        return "failed"

    # Consumer build that redirects to an enterprise one → fetch the better one.
    if analysis.prefer_entry_id:
        events.publish_pipeline_event(
            job_id, "pending",
            "Consumer build — fetching the enterprise installer on your behalf…",
            level="warn",
        )
        try:
            sub = intake.substitute_with_enterprise(analysis)
            if sub:
                analysis = sub
        except Exception as exc:  # noqa: BLE001
            logger.warning("Queue: enterprise substitution failed", error=str(exc))

    # Catalog miss → research the installer (authors a catalog entry), re-analyze.
    if analysis.branch != "hit":
        events.publish_pipeline_event(
            job_id, "pending", "Catalog miss — researching how to package this…",
        )
        claude_bridge.research_and_learn(job_id, analysis, mode=mode)
        analysis = intake.analyze(Path(analysis.path))

    intake.update_software_metadata(job_id, analysis)
    _set_origin_state(job_id, "packaging")
    intake.dispatch_pipeline(job_id, gate=True)
    events.publish_pipeline_event(
        job_id, "pending", "Installer ready — running the gated packaging pipeline.",
    )
    return "dispatched"


def confirm_and_package(
    job_id: int, url: Optional[str] = None, *, mode: Optional[str] = None,
) -> str:
    """Resume an ``awaiting_confirm`` item once the operator approves the URL.

    ``url`` overrides the stashed proposed URL (lets the operator correct it).
    Downloads, analyzes, and dispatches the gated pipeline. Returns the outcome.
    """
    from autopackager.orchestration.engine import OrchestrationEngine

    if _is_cancelled(job_id):
        return "cancelled"
    if not url:
        job = OrchestrationEngine().get_job(job_id)
        url = (job.job_metadata or {}).get("queue_proposed_url") if job else None
    if not url or not intake.is_known_installer(url):
        events.publish_pipeline_event(
            job_id, "failed", "No valid installer URL to confirm.", level="error")
        events.publish_end(job_id, ok=False, text="no url")
        return "failed"
    _set_origin_state(job_id, "acquiring")
    events.publish_pipeline_event(
        job_id, "pending", f"Confirmed — fetching {url}",
    )
    return _download_then(job_id, url, mode=mode)


def _merge_metadata(job_id: int, updates: Dict[str, Any]) -> None:
    """Merge keys into a job's metadata without changing its state. Best-effort."""
    try:
        from autopackager.orchestration.engine import OrchestrationEngine

        engine = OrchestrationEngine()
        job = engine.get_job(job_id)
        if not job:
            return
        engine.update_job_state(job_id, job.state, metadata_update=updates)
    except Exception:  # noqa: BLE001
        pass


def _set_origin_state(job_id: int, state: str) -> None:
    """Update the ``queue_origin.state`` sub-field for UI/debugging. Best-effort."""
    try:
        from autopackager.orchestration.engine import OrchestrationEngine

        engine = OrchestrationEngine()
        job = engine.get_job(job_id)
        if not job:
            return
        origin = dict((job.job_metadata or {}).get(QUEUE_ORIGIN_KEY) or {})
        origin["state"] = state
        engine.update_job_state(
            job_id, job.state, metadata_update={QUEUE_ORIGIN_KEY: origin},
        )
    except Exception:  # noqa: BLE001
        pass


# --- Batch runner ----------------------------------------------------------

def run_batch(job_specs: List[Dict[str, Any]], *, mode: Optional[str] = None) -> None:
    """Process a batch of pre-created queue rows ONE AT A TIME.

    ``job_specs`` is ``[{"job_id": int, "candidate": {...}}, ...]`` (rows already
    created by ``create_queue_job_row`` so they all show as queued immediately).
    For each: skip if cancelled, acquire + dispatch the gated pipeline, then wait
    for it to settle (gate / complete / fail / cancel) before starting the next.
    A blocking/background helper — call it off the request thread.
    """
    total = len(job_specs)
    for idx, spec in enumerate(job_specs, start=1):
        job_id = spec["job_id"]
        candidate = spec["candidate"]
        name = candidate.get("name") or f"item {idx}"

        if _is_cancelled(job_id):
            events.publish_pipeline_event(
                job_id, "failed", "⊘ Skipped — batch cancelled.", level="warn")
            events.publish_end(job_id, ok=False, text="cancelled")
            continue

        log_prefix = f"[{idx}/{total}] {name}"
        logger.info("Queue: processing item", item=log_prefix, job_id=job_id)
        outcome = acquire_and_package(job_id, candidate, mode=mode)

        if outcome == "dispatched":
            reason = _wait_for_settle(job_id)
            logger.info("Queue: item settled", item=log_prefix, job_id=job_id, reason=reason)
            if reason == "cancelled":
                # Operator cancelled the in-flight item — stop the whole batch.
                _cancel_remaining(job_specs[idx:])
                break
        elif outcome in ("awaiting_installer", "awaiting_confirm"):
            # Parked for a human (manual drop or URL confirm) — don't block the
            # rest of the batch on it; the operator resumes it independently.
            continue
        elif outcome == "cancelled":
            _cancel_remaining(job_specs[idx:])
            break
        # "failed" → just move on to the next item.


def _cancel_remaining(remaining: List[Dict[str, Any]]) -> None:
    for spec in remaining:
        jid = spec.get("job_id")
        if jid is not None and not _is_cancelled(jid):
            cancel_job(jid, reason="batch cancelled")


# --- Manual-installer fallback (an awaiting item gets its file dropped) -----

def finalize_with_installer(job_id: int, installer_path: str, *, mode: Optional[str] = None) -> str:
    """Resume an awaiting queue item once the operator drops an installer.

    Analyzes the dropped file (research on a miss), attaches metadata, and
    dispatches the gated pipeline. Returns ``"dispatched"`` / ``"failed"``.
    """
    if _is_cancelled(job_id):
        return "cancelled"
    events.publish_pipeline_event(
        job_id, "pending", "Installer received — packaging it now.")
    return _analyze_and_dispatch(job_id, installer_path, mode=mode)
