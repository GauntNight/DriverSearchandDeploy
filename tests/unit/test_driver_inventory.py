"""Unit tests for the read-only driver-inventory delta path.

Covers the ``driver_inventory`` service shaping/classification and the
``GraphAPIClient.list_driver_inventory`` paging helper. Everything is mocked —
no Graph auth or network.
"""

from unittest.mock import Mock

import pytest

from autopackager.services import driver_inventory as di


# --------------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------------- #

def _row(name, approval, category, cls, version="1.0", devices=1, manufacturer="ACME"):
    """Build a raw windowsDriverUpdateInventory row."""
    return {
        "id": f"drv-{name}",
        "name": name,
        "manufacturer": manufacturer,
        "version": version,
        "driverClass": cls,
        "category": category,
        "approvalStatus": approval,
        "applicableDeviceCount": devices,
        "releaseDateTime": "2026-05-01T00:00:00Z",
        "deployDateTime": None,
    }


# Four drivers spanning the approval/category/class space.
SAMPLE_ROWS = [
    _row("NVIDIA GPU", "needsReview", "recommended", "Display", version="32.0"),
    _row("Intel NIC", "approved", "previouslyApproved", "Net", version="27.0"),
    _row("Realtek Audio", "needsReview", "other", "MEDIA", version="6.0"),
    _row("AMD Chipset", "declined", "other", "System", version="5.0"),
]

PROFILE_DETAIL = {
    "id": "prof-1",
    "displayName": "Driver Updates - Latitude 5420 (pilot)",
    "approvalType": "manual",
    "assignments": [{"target": {"groupId": "group-aaa"}}],
}


def _mock_client(profiles=None, detail=PROFILE_DETAIL, inventory=None, by_name=None):
    """A Mock GraphAPIClient with the four methods the service uses."""
    gc = Mock()
    gc.list_driver_update_profiles.return_value = {"value": profiles if profiles is not None else []}
    gc.get_driver_update_profile.return_value = detail
    gc.list_driver_inventory.return_value = inventory if inventory is not None else []
    gc.find_driver_update_profile_by_name.return_value = by_name
    return gc


# --------------------------------------------------------------------------- #
# _shape_driver
# --------------------------------------------------------------------------- #

def test_shape_driver_normalizes_to_snake_case():
    shaped = di._shape_driver(SAMPLE_ROWS[0])
    assert shaped["name"] == "NVIDIA GPU"
    assert shaped["driver_class"] == "Display"
    assert shaped["approval_status"] == "needsReview"
    assert shaped["category"] == "recommended"
    assert shaped["applicable_device_count"] == 1


def test_shape_driver_tolerates_missing_fields():
    shaped = di._shape_driver({})
    assert shaped["name"] == ""
    assert shaped["applicable_device_count"] == 0
    assert shaped["version"] == ""


# --------------------------------------------------------------------------- #
# build_report — states
# --------------------------------------------------------------------------- #

def test_no_profiles_when_tenant_empty():
    gc = _mock_client(profiles=[])
    report = di.build_report(gc)
    assert report["status"] == "no_profiles"
    assert report["profile_count"] == 0
    assert report["profiles"] == []


def test_pending_when_profile_exists_but_inventory_empty():
    gc = _mock_client(profiles=[{"id": "prof-1", "displayName": "P"}], inventory=[])
    report = di.build_report(gc)
    assert report["status"] == "pending"
    assert report["profile_count"] == 1
    assert report["total_drivers"] == 0
    assert report["profiles"][0]["pending"] is True


def test_populated_counts_and_grouping():
    gc = _mock_client(profiles=[{"id": "prof-1", "displayName": "P"}], inventory=SAMPLE_ROWS)
    report = di.build_report(gc)

    assert report["status"] == "populated"
    assert report["total_drivers"] == 4
    assert report["needs_review"] == 2

    p = report["profiles"][0]
    assert p["approval_type"] == "manual"
    assert p["assigned_group_ids"] == ["group-aaa"]
    assert p["counts"]["by_approval"] == {
        "needsReview": 2, "approved": 1, "declined": 1, "suspended": 0,
    }
    assert p["counts"]["by_category"] == {
        "recommended": 1, "previouslyApproved": 1, "other": 2,
    }
    # all four classes distinct, count 1 each, alphabetical
    assert list(p["by_class"].keys()) == ["Display", "MEDIA", "Net", "System"]


def test_drivers_sorted_needs_review_first_then_recommended():
    gc = _mock_client(profiles=[{"id": "prof-1", "displayName": "P"}], inventory=SAMPLE_ROWS)
    report = di.build_report(gc)
    order = [d["name"] for d in report["profiles"][0]["drivers"]]
    # needsReview (recommended -> other), then approved, then declined
    assert order == ["NVIDIA GPU", "Realtek Audio", "Intel NIC", "AMD Chipset"]


# --------------------------------------------------------------------------- #
# build_report — profile resolution
# --------------------------------------------------------------------------- #

def test_resolve_by_display_name():
    stub = {"id": "prof-1", "displayName": "Driver Updates - Latitude 5420 (pilot)"}
    gc = _mock_client(by_name=stub, inventory=SAMPLE_ROWS)
    report = di.build_report(gc, profile="Driver Updates - Latitude 5420 (pilot)")
    assert report["status"] == "populated"
    gc.find_driver_update_profile_by_name.assert_called_once()
    gc.list_driver_update_profiles.assert_not_called()


def test_resolve_by_guid_uses_get_not_search():
    guid = "11111111-2222-3333-4444-555555555555"
    detail = dict(PROFILE_DETAIL, id=guid)
    gc = _mock_client(detail=detail, inventory=SAMPLE_ROWS)
    report = di.build_report(gc, profile=guid)
    assert report["status"] == "populated"
    gc.get_driver_update_profile.assert_called()
    gc.find_driver_update_profile_by_name.assert_not_called()


def test_unknown_name_returns_no_profiles_with_error():
    gc = _mock_client(by_name=None)
    report = di.build_report(gc, profile="Does Not Exist")
    assert report["status"] == "no_profiles"
    assert report["errors"]
    assert "Does Not Exist" in report["errors"][0]


def test_per_profile_fetch_error_is_collected_not_raised():
    gc = _mock_client(profiles=[{"id": "prof-1", "displayName": "Bad"}])
    gc.get_driver_update_profile.side_effect = RuntimeError("503 backend")
    report = di.build_report(gc)
    # one bad profile -> skipped, surfaced in errors, status no_profiles (none succeeded)
    assert report["profiles"] == []
    assert report["errors"] and "Bad" in report["errors"][0]
    assert report["status"] == "no_profiles"


def test_mixed_good_and_bad_profiles():
    profiles = [{"id": "good", "displayName": "Good"}, {"id": "bad", "displayName": "Bad"}]
    gc = _mock_client(profiles=profiles, inventory=SAMPLE_ROWS)

    def get_detail(pid, expand_assignments=False):
        if pid == "bad":
            raise RuntimeError("boom")
        return dict(PROFILE_DETAIL, id="good", displayName="Good")

    gc.get_driver_update_profile.side_effect = get_detail
    report = di.build_report(gc)
    assert report["status"] == "populated"
    assert report["profile_count"] == 1
    assert report["profiles"][0]["display_name"] == "Good"
    assert any("Bad" in e for e in report["errors"])


# --------------------------------------------------------------------------- #
# GraphAPIClient.list_driver_inventory — paging
# --------------------------------------------------------------------------- #

class _FakeResp:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200
        self.content = b"{}"

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_list_driver_inventory_follows_nextlink(monkeypatch):
    from autopackager.utils.graph_client import GraphAPIClient

    client = GraphAPIClient.__new__(GraphAPIClient)  # bypass eager auth in __init__
    client.access_token = "tok"
    client.graph_endpoint = "https://graph.microsoft.com"

    # First page via _beta_get, with a nextLink to a second page.
    client._beta_get = Mock(return_value={
        "value": [{"id": "d1"}],
        "@odata.nextLink": "https://graph.microsoft.com/page2",
    })
    monkeypatch.setattr(
        "autopackager.utils.graph_client.requests.get",
        lambda url, headers=None: _FakeResp({"value": [{"id": "d2"}]}),
    )

    rows = client.list_driver_inventory("prof-1")
    assert [r["id"] for r in rows] == ["d1", "d2"]
    client._beta_get.assert_called_once()


def test_list_driver_inventory_respects_page_limit(monkeypatch):
    from autopackager.utils.graph_client import GraphAPIClient

    client = GraphAPIClient.__new__(GraphAPIClient)
    client.access_token = "tok"
    client.graph_endpoint = "https://graph.microsoft.com"

    client._beta_get = Mock(return_value={
        "value": [{"id": "d1"}],
        "@odata.nextLink": "https://graph.microsoft.com/page2",
    })
    # Every nextLink keeps returning another nextLink — the limit must stop it.
    monkeypatch.setattr(
        "autopackager.utils.graph_client.requests.get",
        lambda url, headers=None: _FakeResp({
            "value": [{"id": "dx"}], "@odata.nextLink": "https://graph.microsoft.com/again",
        }),
    )

    rows = client.list_driver_inventory("prof-1", page_limit=3)
    # page 1 (_beta_get) + 2 nextLink pages = 3 pages
    assert len(rows) == 3
