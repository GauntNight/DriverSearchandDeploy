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


def test_daily_flag_roundtrip(tmp_path, monkeypatch):
    from demo import lifecycle_settings as ls
    monkeypatch.setattr(ls, "_PATH", tmp_path / "lc.json")
    assert ls.get_daily() is False
    ls.set_daily(True)
    assert ls.get_daily() is True
    # the reserved daily key is excluded from per-line settings
    ls.set_flags("name:vlc", auto_update=True)
    assert "__daily_update__" not in ls.all_settings()
    ls.set_daily(False)
    assert ls.get_daily() is False


def test_run_daily_off_is_noop(monkeypatch):
    from demo import router, lifecycle_settings
    monkeypatch.setattr(lifecycle_settings, "get_daily", lambda: False)
    res = router.run_daily()
    assert res["enabled"] is False and res["acted"] == []


def test_run_daily_upgrades_only_autoupdate_apps(monkeypatch):
    from demo import router, lifecycle_settings
    monkeypatch.setattr(lifecycle_settings, "get_daily", lambda: True)
    plan = [
        {"app_id": "a", "name": "A", "status": "update", "auto_update": True,
         "download_url": "u", "entry_id": "ea"},
        {"app_id": "b", "name": "B", "status": "update", "auto_update": False,
         "download_url": "u", "entry_id": "eb"},          # not autoupdate -> skip
        {"app_id": "c", "name": "C", "status": "up-to-date", "auto_update": True},  # not an update
    ]
    monkeypatch.setattr(router, "_discover_updates", lambda app_ids, mode: plan)
    dispatched = []
    monkeypatch.setattr(router.intake, "begin_upgrade_job",
                        lambda aid, scope, gate=False: dispatched.append((aid, scope, gate)) or 99)
    monkeypatch.setattr(router, "_run_upgrade_pipeline", lambda *a, **k: None)
    res = router.run_daily()
    assert res["enabled"] is True
    assert [d["app_id"] for d in res["acted"]] == ["a"]   # only the autoupdate-ON update
    assert dispatched == [("a", "all", False)]            # full-auto (gate False)


def test_clean_tracking_observe(tmp_path, monkeypatch):
    from demo import clean_tracking as ct
    monkeypatch.setattr(ct, "_PATH", tmp_path / "ct.json")
    cs = ct.observe("a", 0)               # 0 installs -> clean timer starts
    assert cs and ct.clean_since("a") == cs
    assert ct.observe("a", 0) == cs       # still clean -> timer unchanged
    assert ct.observe("a", 2) is None     # a device has it again -> reset
    assert ct.clean_since("a") is None
    ct.observe("a", 0)                     # clean again
    assert ct.observe("a", None) is not None   # unknown count -> leave timer running


def test_apply_lifecycle_retire_and_risk(tmp_path, monkeypatch):
    from demo import intune_view, lifecycle_settings, clean_tracking
    import autopackager.utils.config as cfg
    monkeypatch.setattr(clean_tracking, "_PATH", tmp_path / "ct.json")
    monkeypatch.setattr(lifecycle_settings, "_PATH", tmp_path / "ls.json")
    # window 0 -> a clean old version is immediately retire-eligible
    monkeypatch.setattr(cfg, "get_config", lambda: {"lifecycle": {"clean_window_days": 0}})
    clean_tracking.observe("a", 0)        # the old version is clean

    apps = [
        {"id": "a", "product_line": "name:vlc", "version_state": "N-1", "installed": 0,
         "cve": {"cve_count": 1, "severity": "high", "max_cvss": 8.0}},
        {"id": "b", "product_line": "name:vlc", "version_state": "current", "installed": 1,
         "cve": {"cve_count": 1, "severity": "high", "max_cvss": 8.0}},
    ]
    intune_view.apply_lifecycle_settings(apps)
    by = {a["id"]: a for a in apps}
    # old + clean past the window -> retire-eligible; 0 installs -> CVE risk cleared
    assert by["a"]["retire_eligible"] is True
    assert by["a"]["risk_active"] is False
    # latest with installs + CVE -> active risk, not retire-eligible
    assert by["b"]["retire_eligible"] is False
    assert by["b"]["risk_active"] is True
