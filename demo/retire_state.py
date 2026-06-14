"""Per-app "retired" marker for the lifecycle's relabel path.

When an old (N-1/N-2) version goes clean past the retention window and its
product is NOT set to auto-delete, we **relabel it "Retired"** rather than
deleting the Intune object (the operator may want the historical record). That
relabel is recorded HERE as a local marker keyed by Intune app id — a reversible,
tenant-safe signal the UI reads to show the "Retired" badge (``version_state ->
retired``). The Intune object is left untouched; only the auto-delete path (see
``demo/retire.py``) removes it.

Stored as JSON at ``data/demo_retire_state.json`` (gitignored runtime state):
``{<app_id>: {"retired_since": iso8601}}``. Removable with the rest of ``demo/``.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from autopackager.utils.logger import get_logger

logger = get_logger(__name__)

_PATH = Path(__file__).resolve().parent.parent / "data" / "demo_retire_state.json"
_lock = threading.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
        logger.warning("retire state save failed", error=str(exc))


def mark_retired(app_id: Optional[str]) -> Optional[str]:
    """Mark an app retired (idempotent); returns its ``retired_since``."""
    if not app_id:
        return None
    with _lock:
        data = _load()
        rec = data.get(app_id) or {}
        if not rec.get("retired_since"):
            rec["retired_since"] = _now_iso()
        data[app_id] = rec
        _save(data)
        return rec.get("retired_since")


def is_retired(app_id: Optional[str]) -> bool:
    if not app_id:
        return False
    with _lock:
        return bool((_load().get(app_id) or {}).get("retired_since"))


def all_retired() -> Dict[str, Any]:
    """Whole table (one read) — for stamping many rows on a serve."""
    with _lock:
        return _load()


def forget(app_id: Optional[str]) -> None:
    """Drop the marker (after the app is deleted, or un-retired)."""
    if not app_id:
        return
    with _lock:
        data = _load()
        if app_id in data:
            data.pop(app_id, None)
            _save(data)
