"""Per-app "clean since" tracking for the retire lifecycle.

An old (N-1 / N-2) version with 0 confirmed installs is *clean* — nothing on the
estate is running it. We record WHEN it first went clean so the daily Beat can
retire it once it's stayed clean for the retention window (default 30 days). The
timer resets if installs reappear (a straggler device checks back in).

Stored as JSON at ``data/demo_clean_tracking.json`` (gitignored runtime state),
keyed by Intune app id: ``{clean_since: iso8601|null, last_installed: int}``.
Removable with the rest of ``demo/``.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from autopackager.utils.logger import get_logger

logger = get_logger(__name__)

_PATH = Path(__file__).resolve().parent.parent / "data" / "demo_clean_tracking.json"
_lock = threading.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _load() -> Dict[str, Any]:
    try:
        data = json.loads(_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(data: Dict[str, Any]) -> None:
    try:
        _PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(_PATH)
    except OSError as exc:
        logger.warning("clean tracking save failed", error=str(exc))


def observe(app_id: Optional[str], installed: Optional[int]) -> Optional[str]:
    """Record a fresh install-count observation; return the app's ``clean_since``.

    ``installed == 0`` starts the clean timer (if not already running);
    ``installed > 0`` (a device still has it) resets it; ``installed is None``
    (count unavailable) leaves the timer untouched.
    """
    if not app_id:
        return None
    with _lock:
        data = _load()
        rec = data.get(app_id) or {}
        if installed == 0:
            if not rec.get("clean_since"):
                rec["clean_since"] = _now().isoformat()
        elif isinstance(installed, int) and installed > 0:
            rec["clean_since"] = None
        rec["last_installed"] = installed
        data[app_id] = rec
        _save(data)
        return rec.get("clean_since")


def all_records() -> Dict[str, Any]:
    """Whole tracking table (one read) — for stamping many rows on a serve."""
    with _lock:
        return _load()


def days_since(iso: Optional[str]) -> Optional[float]:
    """Days since an iso8601 timestamp (None if absent/unparseable)."""
    if not iso:
        return None
    try:
        return (_now() - datetime.fromisoformat(iso)).total_seconds() / 86400.0
    except (ValueError, TypeError):
        return None


def clean_since(app_id: Optional[str]) -> Optional[str]:
    if not app_id:
        return None
    with _lock:
        return (_load().get(app_id) or {}).get("clean_since")


def clean_days(app_id: Optional[str]) -> Optional[float]:
    """How long the app has been continuously clean, in days (None if not clean)."""
    cs = clean_since(app_id)
    if not cs:
        return None
    try:
        dt = datetime.fromisoformat(cs)
        return (_now() - dt).total_seconds() / 86400.0
    except (ValueError, TypeError):
        return None


def forget(app_id: Optional[str]) -> None:
    """Drop tracking for an app (after it's retired/deleted, or pruned)."""
    if not app_id:
        return
    with _lock:
        data = _load()
        if app_id in data:
            data.pop(app_id, None)
            _save(data)
