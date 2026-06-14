"""Phase-3b lifecycle: the retire ACTION (relabel / delete) + estate sweep."""

from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.unit


# --- retire_state store ------------------------------------------------------

def test_retire_state_roundtrip(tmp_path, monkeypatch):
    from demo import retire_state as rs
    monkeypatch.setattr(rs, "_PATH", tmp_path / "rs.json")

    assert rs.is_retired("a") is False
    since = rs.mark_retired("a")
    assert since and rs.is_retired("a") is True
    # idempotent — re-marking keeps the original timestamp
    assert rs.mark_retired("a") == since
    assert "a" in rs.all_retired()
    rs.forget("a")
    assert rs.is_retired("a") is False
    # None / empty id is a no-op
    assert rs.mark_retired(None) is None
    assert rs.is_retired(None) is False


# --- retire_app: relabel path ------------------------------------------------

def test_retire_app_relabel_marks_retired(tmp_path, monkeypatch):
    from demo import retire, retire_state, clean_tracking
    monkeypatch.setattr(retire_state, "_PATH", tmp_path / "rs.json")
    monkeypatch.setattr(clean_tracking, "_PATH", tmp_path / "ct.json")
    # The relabel path must NEVER touch Graph.
    monkeypatch.setattr(
        "autopackager.utils.graph_client.GraphAPIClient",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("graph used on relabel")))

    res = retire.retire_app("app1", delete=False)
    assert res["ok"] is True and res["action"] == "retired"
    assert retire_state.is_retired("app1") is True


# --- retire_app: delete path -------------------------------------------------

def test_retire_app_delete_calls_graph_and_forgets(tmp_path, monkeypatch):
    from demo import retire, retire_state, clean_tracking
    monkeypatch.setattr(retire_state, "_PATH", tmp_path / "rs.json")
    monkeypatch.setattr(clean_tracking, "_PATH", tmp_path / "ct.json")
    retire_state.mark_retired("app1")
    clean_tracking.observe("app1", 0)

    gc = MagicMock()
    gc._beta_get.return_value = {"value": []}     # no incoming supersedence
    res = retire.retire_app("app1", delete=True, graph_client=gc)

    assert res["ok"] is True and res["action"] == "deleted"
    gc.delete_win32_app.assert_called_once_with("app1")
    # local tracking is dropped after the object is gone
    assert retire_state.is_retired("app1") is False
    assert clean_tracking.clean_since("app1") is None


def test_delete_clears_incoming_supersedence(monkeypatch):
    from demo import retire
    gc = MagicMock()
    # app "old" is superseded by "new" (relationship target == old).
    def beta_get(path):
        if "old/relationships" in path:
            return {"value": [{
                "@odata.type": "#microsoft.graph.mobileAppSupersedence",
                "sourceId": "new", "targetId": "old", "supersedenceType": "update",
            }]}
        if "new/relationships" in path:
            return {"value": [{
                "@odata.type": "#microsoft.graph.mobileAppSupersedence",
                "sourceId": "new", "targetId": "old", "supersedenceType": "update",
            }]}
        return {"value": []}
    gc._beta_get.side_effect = beta_get

    res = retire.delete_app_clearing_supersedence(gc, "old")
    assert res["deleted"] is True and res["supersedence_links_cleared"] == 1
    # the superseding app "new" was rewritten with the link to "old" removed (empty)
    post_calls = [c for c in gc._beta_post.call_args_list if "new/updateRelationships" in c.args[0]]
    assert len(post_calls) == 1
    assert post_calls[0].kwargs["data"]["relationships"] == []
    gc.delete_win32_app.assert_called_once_with("old")


def test_delete_preserves_unrelated_relationships(monkeypatch):
    from demo import retire
    gc = MagicMock()
    def beta_get(path):
        if "old/relationships" in path:
            return {"value": [{
                "@odata.type": "#microsoft.graph.mobileAppSupersedence",
                "sourceId": "new", "targetId": "old", "supersedenceType": "update"}]}
        if "new/relationships" in path:
            return {"value": [
                {"@odata.type": "#microsoft.graph.mobileAppSupersedence",
                 "sourceId": "new", "targetId": "old", "supersedenceType": "update"},
                {"@odata.type": "#microsoft.graph.mobileAppSupersedence",
                 "sourceId": "new", "targetId": "older", "supersedenceType": "update"},
            ]}
        return {"value": []}
    gc._beta_get.side_effect = beta_get

    retire.delete_app_clearing_supersedence(gc, "old")
    post = [c for c in gc._beta_post.call_args_list if "new/updateRelationships" in c.args[0]][0]
    kept = post.kwargs["data"]["relationships"]
    # the unrelated link to "older" survives; the one to "old" is gone
    assert [r["targetId"] for r in kept] == ["older"]


def test_retire_app_delete_failure_is_reported(tmp_path, monkeypatch):
    from demo import retire, retire_state, clean_tracking
    monkeypatch.setattr(retire_state, "_PATH", tmp_path / "rs.json")
    monkeypatch.setattr(clean_tracking, "_PATH", tmp_path / "ct.json")
    gc = MagicMock()
    gc._beta_get.return_value = {"value": []}
    gc.delete_win32_app.side_effect = RuntimeError("graph 500")
    res = retire.retire_app("app1", delete=True, graph_client=gc)
    assert res["ok"] is False and "graph 500" in res["error"]


# --- estate sweep ------------------------------------------------------------

def test_run_retire_sweep_relabels_and_deletes(tmp_path, monkeypatch):
    from demo import retire, retire_state, clean_tracking, intune_view
    monkeypatch.setattr(retire_state, "_PATH", tmp_path / "rs.json")
    monkeypatch.setattr(clean_tracking, "_PATH", tmp_path / "ct.json")

    apps = [
        # eligible, auto-delete OFF -> relabel
        {"id": "relabel", "name": "R", "retire_eligible": True,
         "auto_delete_when_clean": False, "retired": False},
        # eligible, auto-delete ON -> delete
        {"id": "del", "name": "D", "retire_eligible": True,
         "auto_delete_when_clean": True, "retired": False},
        # not eligible -> skip
        {"id": "keep", "name": "K", "retire_eligible": False,
         "auto_delete_when_clean": True, "retired": False},
        # already retired -> skip
        {"id": "done", "name": "Z", "retire_eligible": True,
         "auto_delete_when_clean": False, "retired": True},
    ]
    monkeypatch.setattr(intune_view, "get_apps_view_cached", lambda *a, **k: {"apps": apps})

    gc = MagicMock()
    gc._beta_get.return_value = {"value": []}
    monkeypatch.setattr("autopackager.utils.graph_client.GraphAPIClient", lambda *a, **k: gc)

    res = retire.run_retire_sweep()
    by = {a["app_id"]: a for a in res["actions"]}
    assert res["swept"] == 2
    assert by["relabel"]["action"] == "retired"
    assert by["del"]["action"] == "deleted"
    assert "keep" not in by and "done" not in by
    gc.delete_win32_app.assert_called_once_with("del")
    assert retire_state.is_retired("relabel") is True


def test_run_retire_sweep_handles_view_error(monkeypatch):
    from demo import retire, intune_view
    monkeypatch.setattr(intune_view, "get_apps_view_cached",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("graph down")))
    res = retire.run_retire_sweep()
    assert res["swept"] == 0 and res["actions"] == []


# --- apply_lifecycle_settings stamps the retired state -----------------------

def test_apply_lifecycle_marks_retired_row(tmp_path, monkeypatch):
    from demo import intune_view, lifecycle_settings, clean_tracking, retire_state
    import autopackager.utils.config as cfg
    monkeypatch.setattr(clean_tracking, "_PATH", tmp_path / "ct.json")
    monkeypatch.setattr(lifecycle_settings, "_PATH", tmp_path / "ls.json")
    monkeypatch.setattr(retire_state, "_PATH", tmp_path / "rs.json")
    monkeypatch.setattr(cfg, "get_config", lambda: {"lifecycle": {"clean_window_days": 0}})

    clean_tracking.observe("old", 0)
    retire_state.mark_retired("old")     # operator already retired it
    apps = [{"id": "old", "product_line": "name:vlc", "version_state": "N-1",
             "installed": 0, "cve": {"cve_count": 1, "severity": "high", "max_cvss": 8.0}}]
    intune_view.apply_lifecycle_settings(apps)
    row = apps[0]
    # retired is terminal: relabeled, not "eligible" again, risk drained
    assert row["retired"] is True
    assert row["version_state"] == "retired"
    assert row["retire_eligible"] is False
    assert row["risk_active"] is False
