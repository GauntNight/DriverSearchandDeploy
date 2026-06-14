"""Per-tenant, per-product-line lifecycle settings for the demo console.

Operator preferences that govern the application lifecycle, keyed by the stable
``product_line`` string (e.g. ``line:vlc-media-player`` / ``name:notepad++``) so
they apply to a product across all its deployed versions:

  * ``auto_update``            — when a newer version is discovered, full
                                 auto-upgrade (deploy unattended). Default off →
                                 a **gated** upgrade (packaged + tested, held at
                                 the Ring-0 approval gate).
  * ``auto_delete_when_clean`` — once an old version goes clean (0 installs for
                                 the retention window), delete its Intune app.
                                 Default off → relabel "Retired", keep the object.
                                 (Consumed in the clean/retire phase.)

Stored as JSON at ``data/demo_lifecycle.json`` (gitignored runtime state). Tiny,
read-mostly; a process-wide lock keeps writes consistent. Removable with the rest
of ``demo/``.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from autopackager.utils.logger import get_logger

logger = get_logger(__name__)

_PATH = Path(__file__).resolve().parent.parent / "data" / "demo_lifecycle.json"
_lock = threading.Lock()

DEFAULTS: Dict[str, bool] = {"auto_update": False, "auto_delete_when_clean": False}


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
        logger.warning("lifecycle settings save failed", error=str(exc))


def get(product_line: Optional[str]) -> Dict[str, bool]:
    """Resolved settings for a product line (defaults merged in)."""
    if not product_line:
        return dict(DEFAULTS)
    with _lock:
        stored = _load().get(product_line) or {}
    return {**DEFAULTS, **{k: bool(v) for k, v in stored.items() if k in DEFAULTS}}


def set_flags(product_line: Optional[str], **flags: bool) -> Dict[str, bool]:
    """Update one or more flags for a product line; returns the resolved entry.
    Unknown flag names are ignored."""
    if not product_line:
        return dict(DEFAULTS)
    valid = {k: bool(v) for k, v in flags.items() if k in DEFAULTS}
    if not valid:
        return get(product_line)
    with _lock:
        data = _load()
        entry = {**DEFAULTS, **(data.get(product_line) or {}), **valid}
        data[product_line] = entry
        _save(data)
    return entry


def all_settings() -> Dict[str, Dict[str, bool]]:
    """Every stored product line's settings (defaults merged) — for the Beat.
    Reserved ``__`` keys (e.g. the global daily flag) are excluded."""
    with _lock:
        data = _load()
    return {pl: {**DEFAULTS, **(v or {})}
            for pl, v in data.items() if not pl.startswith("__")}


# --- Global daily-update flag ----------------------------------------------
# When on, the daily Beat acts on apps whose product has auto_update enabled
# ("set to Update"). Stored under a reserved key in the same JSON.
_DAILY_KEY = "__daily_update__"


def get_daily() -> bool:
    with _lock:
        return bool((_load().get(_DAILY_KEY) or {}).get("enabled"))


def set_daily(enabled: bool) -> bool:
    with _lock:
        data = _load()
        data[_DAILY_KEY] = {"enabled": bool(enabled)}
        _save(data)
    return bool(enabled)
