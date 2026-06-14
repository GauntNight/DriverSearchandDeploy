"""Stale-while-revalidate cache for the center-panel apps view.

The center panel polls continuously; building the view live is a slow Graph
fan-out. These tests pin the caching contract: a cold load is synchronous, warm
reads are served from memory without re-hitting Graph, a disk snapshot makes a
cold start instant, and ``force`` always does a full reload.
"""

import time

import pytest

from demo import intune_view

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    # Isolate the disk snapshot and reset the in-memory cache between tests.
    monkeypatch.setattr(intune_view, "_CACHE_DIR", tmp_path)
    monkeypatch.setattr(intune_view, "_SNAPSHOT_PATH", tmp_path / "snap.json")
    with intune_view._apps_lock:
        intune_view._apps_cache.update(data=None, ts=0.0, counts=False)
    yield
    with intune_view._apps_lock:
        intune_view._apps_cache.update(data=None, ts=0.0, counts=False)


def _view():
    return {"mode": "live", "apps": [{"id": "a", "name": "X", "version": "1"}],
            "error": None}


def test_cold_load_is_live_then_served_from_cache(monkeypatch):
    calls = []
    monkeypatch.setattr(intune_view, "get_apps_view",
                        lambda counts=False: (calls.append(1), _view())[1])
    r1 = intune_view.get_apps_view_cached()
    assert r1["cache"]["hit"] is False and r1["cache"]["source"] == "live"
    assert len(calls) == 1
    # Within TTL the next reads are pure cache hits — no extra live build.
    r2 = intune_view.get_apps_view_cached()
    r3 = intune_view.get_apps_view_cached()
    assert r2["cache"]["hit"] is True and r2["cache"]["source"] == "memory"
    assert len(calls) == 1
    assert r3["apps"] == r1["apps"]


def test_force_always_reloads(monkeypatch):
    calls = []
    monkeypatch.setattr(intune_view, "get_apps_view",
                        lambda counts=False: (calls.append(1), _view())[1])
    intune_view.get_apps_view_cached()
    intune_view.get_apps_view_cached(force=True)
    assert len(calls) == 2


def test_snapshot_persisted_and_used_on_cold_start(monkeypatch):
    monkeypatch.setattr(intune_view, "get_apps_view", lambda counts=False: _view())
    intune_view.get_apps_view_cached()
    assert intune_view._SNAPSHOT_PATH.exists()  # persisted to disk
    # Simulate a server restart: memory empty, snapshot on disk.
    with intune_view._apps_lock:
        intune_view._apps_cache.update(data=None, ts=0.0)
    r = intune_view.get_apps_view_cached()
    assert r["cache"]["source"] == "snapshot"
    assert r["cache"]["revalidating"] is True   # background refresh kicked
    assert r["apps"]


def test_stale_returns_cached_and_revalidates(monkeypatch):
    monkeypatch.setattr(intune_view, "get_apps_view", lambda counts=False: _view())
    intune_view.get_apps_view_cached()
    # ttl=0 forces the next read to look stale -> cached hit + revalidate flag.
    r = intune_view.get_apps_view_cached(ttl=0.0)
    assert r["cache"]["hit"] is True
    assert r["cache"]["revalidating"] is True


def test_invalidate_forces_next_live(monkeypatch):
    calls = []
    monkeypatch.setattr(intune_view, "get_apps_view",
                        lambda counts=False: (calls.append(1), _view())[1])
    intune_view.get_apps_view_cached()
    intune_view.invalidate_apps_cache()
    # snapshot still on disk, so a cold read uses it (instant) + revalidates,
    # rather than blocking — invalidate clears memory, not the paint fallback.
    r = intune_view.get_apps_view_cached()
    assert r["cache"]["source"] in ("snapshot", "live")


def test_cache_never_raises_on_live_failure(monkeypatch):
    # First, seed a good snapshot.
    monkeypatch.setattr(intune_view, "get_apps_view", lambda counts=False: _view())
    intune_view.get_apps_view_cached()
    # Now make live builds raise; a forced reload must surface the error path of
    # get_apps_view itself (which already degrades to fixture) — here we just
    # assert the wrapper doesn't blow up the background refresh path.
    with intune_view._apps_lock:
        intune_view._apps_cache.update(ts=0.0)  # mark stale

    def boom(counts=False):
        raise RuntimeError("graph down")

    monkeypatch.setattr(intune_view, "get_apps_view", boom)
    r = intune_view.get_apps_view_cached(ttl=0.0)  # stale -> serve cache + bg refresh
    assert r["apps"]            # still served the last good snapshot
    time.sleep(0.05)            # let the background thread run + swallow the error


def test_cached_serve_reapplies_fresh_lifecycle_settings(monkeypatch):
    # Regression: toggling autoupdate must NOT appear to flip back on the next
    # (cached) poll. The flag is re-read fresh on every serve, not frozen in the
    # SWR cache alongside the Graph data.
    from demo import intune_view, lifecycle_settings
    base = {"mode": "live", "error": None,
            "apps": [{"id": "a", "name": "VLC media player", "version": "3.0.23",
                      "product_line": "name:vlc media player", "auto_update": False}]}
    monkeypatch.setattr(intune_view, "get_apps_view",
                        lambda counts=False: {**base, "apps": [dict(base["apps"][0])]})
    monkeypatch.setattr(lifecycle_settings, "get",
                        lambda pl: {"auto_update": False, "auto_delete_when_clean": False})
    intune_view.get_apps_view_cached()  # cold -> live -> caches (auto_update False)

    # operator toggles autoupdate ON
    monkeypatch.setattr(lifecycle_settings, "get",
                        lambda pl: {"auto_update": True, "auto_delete_when_clean": False})
    r = intune_view.get_apps_view_cached()  # warm cached serve
    assert r["cache"]["source"] == "memory"
    assert r["apps"][0]["auto_update"] is True
