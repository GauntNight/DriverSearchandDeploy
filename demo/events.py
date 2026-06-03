"""Redis pub/sub plumbing for the demo console.

One channel per job: ``demo:events:{job_id}``. Both event sources — the
pipeline (Celery tasks) and the Claude research bridge — publish to the same
channel, so the right-hand console multiplexes them into one stream. The SSE
endpoint (``GET /api/demo/stream/{job_id}``) is the only subscriber.

Reuses the same Redis instance the Celery broker already runs on (host/port/db
from ``config['redis']``) — no new infrastructure.

Event envelope (JSON, one per publish)::

    {
      "ts":     "2026-06-02T14:32:11.123Z",
      "type":   "console" | "lamp" | "state" | "hello" | "end",
      "source": "pipeline" | "claude" | "system",
      "level":  "info" | "warn" | "error",
      "state":  "packaging",          # job state, when known
      "text":   "Built .intunewin (84 MB)",
      "lamp":   "thinking",           # only on type == "lamp"
      ...                              # source-specific extras pass through
    }

Design rule: publishing must never raise into the caller. The core pipeline
calls these through a lazy, exception-swallowing hook; a Redis hiccup must not
fail a real packaging job. Every publish is best-effort.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, Optional

# redis-py is already a dependency (Celery's broker). Import defensively so a
# missing/broken redis never takes down the importing module.
try:  # pragma: no cover - trivial import guard
    import redis as _redis
except Exception:  # pragma: no cover
    _redis = None  # type: ignore[assignment]


CHANNEL_TEMPLATE = "demo:events:{job_id}"
# Approval gate key (optional human-in-the-loop beat). Set when the operator
# clicks "Approve" in the UI; the demo deployment kick-off waits on it.
GATE_KEY_TEMPLATE = "demo:gate:{job_id}"


def channel_for(job_id: Any) -> str:
    return CHANNEL_TEMPLATE.format(job_id=job_id)


def gate_key_for(job_id: Any) -> str:
    return GATE_KEY_TEMPLATE.format(job_id=job_id)


def _redis_kwargs() -> Dict[str, Any]:
    """Read Redis connection settings from the app config (same as Celery)."""
    try:
        from autopackager.utils.config import get_config

        rc = get_config().get("redis", {}) or {}
        return {
            "host": rc.get("host", "localhost"),
            "port": int(rc.get("port", 6379)),
            "db": int(rc.get("db", 0)),
        }
    except Exception:
        return {"host": "localhost", "port": 6379, "db": 0}


_sync_client = None


def _client():
    """Lazily build a shared sync Redis client (decode_responses=True)."""
    global _sync_client
    if _redis is None:
        return None
    if _sync_client is None:
        try:
            _sync_client = _redis.Redis(decode_responses=True, **_redis_kwargs())
        except Exception:
            _sync_client = None
    return _sync_client


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + \
        f"{datetime.now(timezone.utc).microsecond // 1000:03d}Z"


def publish_event(
    job_id: Any,
    *,
    type: str = "console",
    source: str = "system",
    text: str = "",
    level: str = "info",
    state: Optional[str] = None,
    **extra: Any,
) -> bool:
    """Publish one event envelope to a job's channel. Best-effort; never raises.

    Returns True if the publish was attempted against a live client. The return
    value is advisory — callers in the core pipeline ignore it.
    """
    client = _client()
    if client is None:
        return False
    envelope: Dict[str, Any] = {
        "ts": _now_iso(),
        "type": type,
        "source": source,
        "level": level,
        "text": text,
    }
    if state is not None:
        envelope["state"] = state
    envelope.update(extra)
    try:
        client.publish(channel_for(job_id), json.dumps(envelope))
        return True
    except Exception:
        return False


# --- Convenience wrappers --------------------------------------------------

def publish_pipeline_event(
    job_id: Any, state: Optional[str], text: str, level: str = "info", **extra: Any
) -> bool:
    """A pipeline step line (left stepper + console)."""
    return publish_event(
        job_id, type="console", source="pipeline", text=text, level=level,
        state=state, **extra,
    )


def publish_state(job_id: Any, state: str, **extra: Any) -> bool:
    """A bare state-transition marker (drives the left stepper)."""
    return publish_event(
        job_id, type="state", source="pipeline", text="", state=state, **extra,
    )


def publish_claude_event(job_id: Any, text: str, level: str = "info", **extra: Any) -> bool:
    """A line from the Claude research bridge."""
    return publish_event(
        job_id, type="console", source="claude", text=text, level=level, **extra,
    )


def publish_lamp(job_id: Any, lamp_state: str, sublabel: str = "", **extra: Any) -> bool:
    """Drive the AI lamp orb. lamp_state in
    {offline, checking, ready, thinking, error}."""
    return publish_event(
        job_id, type="lamp", source="system", lamp=lamp_state, text=sublabel, **extra,
    )


def publish_end(job_id: Any, ok: bool = True, text: str = "") -> bool:
    """Terminal marker so the SSE client can close cleanly."""
    return publish_event(
        job_id, type="end", source="system", text=text, level="info" if ok else "error",
        ok=ok,
    )


# --- Approval gate ---------------------------------------------------------

def set_gate_approved(job_id: Any) -> bool:
    client = _client()
    if client is None:
        return False
    try:
        client.set(gate_key_for(job_id), "approved", ex=3600)
        return True
    except Exception:
        return False


def is_gate_approved(job_id: Any) -> bool:
    client = _client()
    if client is None:
        return False
    try:
        return client.get(gate_key_for(job_id)) == "approved"
    except Exception:
        return False


# --- Async subscriber (used by the SSE endpoint) ---------------------------

async def asubscribe(job_id: Any):
    """Async generator yielding decoded event dicts for one job's channel.

    Uses ``redis.asyncio``. Yields each published envelope as a dict. Closes
    when it sees a ``type == "end"`` event or the consumer stops iterating.
    """
    import redis.asyncio as aredis

    client = aredis.Redis(decode_responses=True, **_redis_kwargs())
    pubsub = client.pubsub()
    await pubsub.subscribe(channel_for(job_id))
    try:
        while True:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=1.0
            )
            if message is None:
                # Idle tick — lets the SSE layer emit a keep-alive comment.
                yield None
                continue
            data = message.get("data")
            if not data:
                continue
            try:
                envelope = json.loads(data)
            except (ValueError, TypeError):
                continue
            yield envelope
            if envelope.get("type") == "end":
                break
    finally:
        try:
            await pubsub.unsubscribe(channel_for(job_id))
            await pubsub.aclose()
            await client.aclose()
        except Exception:
            pass


def ping() -> bool:
    """Liveness check for preflight."""
    client = _client()
    if client is None:
        return False
    try:
        return bool(client.ping())
    except Exception:
        return False
