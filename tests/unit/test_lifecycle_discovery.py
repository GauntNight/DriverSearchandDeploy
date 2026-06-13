"""Phase-2 lifecycle: per-product autoupdate settings + the discovery loop."""

import pytest

pytestmark = pytest.mark.unit


def test_lifecycle_settings_roundtrip(tmp_path, monkeypatch):
    from demo import lifecycle_settings as ls
    monkeypatch.setattr(ls, "_PATH", tmp_path / "lc.json")

    # defaults
    assert ls.get("line:vlc") == {"auto_update": False, "auto_delete_when_clean": False}
    assert ls.get(None) == ls.DEFAULTS

    # set one flag, the other keeps its default
    ls.set_flags("line:vlc", auto_update=True)
    assert ls.get("line:vlc")["auto_update"] is True
    assert ls.get("line:vlc")["auto_delete_when_clean"] is False

    # set the second flag; the first persists
    ls.set_flags("line:vlc", auto_delete_when_clean=True)
    s = ls.get("line:vlc")
    assert s["auto_update"] is True and s["auto_delete_when_clean"] is True

    # unknown flags are ignored; other lines are independent
    ls.set_flags("line:vlc", bogus=True)
    assert "bogus" not in ls.get("line:vlc")
    assert ls.get("line:other") == ls.DEFAULTS

    # survives a reload (persisted to disk)
    assert ls.all_settings()["line:vlc"]["auto_update"] is True


def test_discover_updates_classifies(monkeypatch):
    from demo import router, intune_view

    apps = [
        {"id": "a", "name": "App A", "version": "1.0", "version_state": "current", "auto_update": False},
        {"id": "b", "name": "App B", "version": "2.0", "version_state": "current", "auto_update": True},
        {"id": "c", "name": "App C", "version": "3.0", "version_state": "current", "auto_update": False},
        {"id": "n", "name": "App old", "version": "0.9", "version_state": "N-1", "auto_update": False},
        {"id": "z", "name": "App Z", "version": "5.0", "version_state": "current", "auto_update": False},
    ]
    monkeypatch.setattr(intune_view, "get_apps_view_cached", lambda *a, **k: {"apps": apps})

    def fake_check(body, app_id):
        return {
            "a": {"is_newer": True, "latest_version": "1.1", "download_url": "u", "entry_id": "ea"},
            "b": {"is_newer": True, "latest_version": "2.1", "download_url": "u", "entry_id": "eb"},
            "c": {"is_newer": False, "latest_version": "3.0", "download_url": None, "entry_id": None},
            "z": {"is_newer": True, "latest_version": "5.1", "download_url": None, "entry_id": None},  # no source
        }[app_id]
    monkeypatch.setattr(router, "_check_version_sync", fake_check)

    plan = router._discover_updates(None, "replay")
    by = {p["app_id"]: p for p in plan}

    assert "n" not in by  # N-1 is skipped (already superseded)
    assert by["a"]["status"] == "update" and by["a"]["auto_update"] is False
    assert by["b"]["status"] == "update" and by["b"]["auto_update"] is True
    assert by["c"]["status"] == "up-to-date"
    assert by["z"]["status"] == "no-source"   # newer but nothing fetchable


def test_discover_updates_respects_app_ids_filter(monkeypatch):
    from demo import router, intune_view
    apps = [
        {"id": "a", "name": "A", "version": "1.0", "version_state": "current", "auto_update": False},
        {"id": "b", "name": "B", "version": "2.0", "version_state": "current", "auto_update": False},
    ]
    monkeypatch.setattr(intune_view, "get_apps_view_cached", lambda *a, **k: {"apps": apps})
    monkeypatch.setattr(router, "_check_version_sync",
                        lambda body, app_id: {"is_newer": False, "latest_version": "1.0"})
    plan = router._discover_updates(["a"], None)
    assert [p["app_id"] for p in plan] == ["a"]
