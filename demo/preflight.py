"""Demo boot / pre-warm readiness checks.

Surfaces four readiness lights in the UI header:

  1. AI ready   — Claude is authenticated on this box (SDK present, or a quick
                  ``claude -p "ok"`` health check succeeds).
  2. Redis      — the pub/sub + Celery broker is reachable.
  3. Graph      — the tenant credentials validate (else the center panel falls
                  back to fixture mode).
  4. Worker     — a Celery worker appears to be consuming (best-effort).

The legitimate "pre-start Claude in the back" step is the AI health check: it
confirms the box is logged in ahead of time. We do NOT keep an interactive
window open — authentication lives on the machine, so any subprocess/SDK call
is already authenticated.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import Any, Dict

from autopackager.utils.logger import get_logger
from demo import events

logger = get_logger(__name__)


def check_redis() -> Dict[str, Any]:
    ok = events.ping()
    return {"ok": ok, "detail": "reachable" if ok else "not reachable"}


def check_ai() -> Dict[str, Any]:
    """Confirm Claude is available on this box.

    Order: (1) is the Agent SDK importable? (2) does a fast ``claude -p "ok"``
    succeed? Either one ⇒ ready. Reports the chosen mode and why.
    """
    mode = os.environ.get("DEMO_CLAUDE_MODE", "replay").lower()
    if mode == "off":
        return {"ok": True, "state": "ready", "detail": "research disabled (off mode)",
                "mode": mode}
    if mode == "replay":
        return {"ok": True, "state": "ready", "detail": "replay mode — no live auth needed",
                "mode": mode}

    # live mode: verify real availability
    sdk = False
    try:
        import claude_agent_sdk  # noqa: F401
        sdk = True
    except Exception:
        sdk = False

    cli = shutil.which("claude") is not None
    if not (sdk or cli):
        return {"ok": False, "state": "error",
                "detail": "no claude-agent-sdk and no `claude` CLI on PATH",
                "mode": mode}

    # Fast health check via CLI (cheap, authenticates through local session).
    if cli:
        try:
            proc = subprocess.run(
                ["claude", "-p", "ok"], capture_output=True, text=True, timeout=45,
            )
            if proc.returncode == 0:
                return {"ok": True, "state": "ready",
                        "detail": f"health check passed ({'sdk+cli' if sdk else 'cli'})",
                        "mode": mode}
            tail = (proc.stderr or proc.stdout or "").strip()[-200:]
            return {"ok": False, "state": "error",
                    "detail": f"health check failed: {tail}", "mode": mode}
        except subprocess.TimeoutExpired:
            return {"ok": False, "state": "error",
                    "detail": "health check timed out", "mode": mode}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "state": "error", "detail": str(exc), "mode": mode}
    # SDK present but no CLI: assume ready (SDK authenticates via local config).
    return {"ok": True, "state": "ready", "detail": "claude-agent-sdk present", "mode": mode}


def check_graph() -> Dict[str, Any]:
    """Validate tenant credentials. Failure ⇒ center panel uses fixture mode."""
    try:
        from autopackager.utils.azure_validator import AzureValidator

        validator = AzureValidator()
        auth = validator.validate_authentication()
        if not auth.passed:
            return {"ok": False, "detail": auth.message, "fixture": True}
        access = validator.validate_graph_access()
        return {
            "ok": bool(access.passed),
            "detail": access.message if not access.passed else "tenant reachable",
            "fixture": not bool(access.passed),
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": str(exc), "fixture": True}


def check_worker() -> Dict[str, Any]:
    """Best-effort check that a Celery worker is consuming."""
    try:
        from autopackager.orchestration.celery_app import celery_app

        replies = celery_app.control.ping(timeout=1.0)
        n = len(replies or [])
        return {"ok": n > 0, "detail": f"{n} worker(s) responding"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": f"no worker ping ({exc})"}


def run_all() -> Dict[str, Any]:
    """Run every readiness check and return a single status payload."""
    ai = check_ai()
    redis = check_redis()
    graph = check_graph()
    worker = check_worker()
    return {
        "ai": ai,
        "redis": redis,
        "graph": graph,
        "worker": worker,
        "ready": bool(redis["ok"]),  # the demo can run (fixture mode) as long as redis is up
        "lamp": ai.get("state", "offline"),
    }
