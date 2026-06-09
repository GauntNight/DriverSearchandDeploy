"""Unit tests for the corrected Dell driver-pack matcher.

Regression coverage for the three bugs found live against a real Latitude 5420
(it used to return a "Latitude 5420 Rugged" / Windows10 / A10 / 2021 pack for a
plain Latitude 5420 on Windows 11):
  1. substring model collision (base vs Rugged vs E5420)
  2. no OS filtering (Win10 pack for a Win11 device)
  3. first-document-order instead of newest version/date
"""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from autopackager.agents.discovery.discovery_agent import DiscoveryAgent


def _pack(name, version, os_code, date, path, size="100", models=None):
    """One xmltodict-shaped DriverPackage. ``models`` overrides the single name."""
    if models is None:
        model_node = {"@name": name}
    else:
        model_node = [{"@name": m} for m in models]
    return {
        "@name": f"{name} {version}",
        "@dellVersion": version,
        "@path": path,
        "@dateTime": date,
        "@size": size,
        "SupportedSystems": {"Brand": {"Model": model_node}},
        "SupportedOperatingSystems": {"OperatingSystem": {"@osCode": os_code}},
    }


# Mirrors the real catalog situation for the 5420 family.
CATALOG = {
    "DriverPackManifest": {
        "DriverPackage": [
            _pack("Latitude 5420", "A10", "Windows10", "2021-08-05T10:39:39", "p/5420-win10-A10.cab"),
            _pack("Latitude 5420", "A18", "Windows10", "2024-07-05T00:58:28", "p/5420-win10-A18.exe"),
            _pack("Latitude 5420", "A13", "Windows11", "2024-07-05T00:58:28", "p/5420-win11-A13.exe"),
            _pack("Latitude 5420 Rugged", "A00", "Windows11", "2023-04-27T05:36:32", "p/5420-rugged-win11-A00.exe"),
            _pack("E5420", "A11", "Windows7", "2014-06-05T00:00:00", "p/E5420-win7-A11.cab"),
        ]
    }
}


@pytest.fixture
def agent():
    return DiscoveryAgent()


# --------------------------------------------------------------------------- #
# Model matching — exact, no Rugged/E5420 collision
# --------------------------------------------------------------------------- #

def test_exact_match_excludes_rugged_and_e5420(agent):
    pack = agent._find_dell_driver_pack(CATALOG, "Latitude 5420", target_os="Windows11")
    assert pack["dellVersion"] == "A13"
    assert "rugged" not in pack["path"].lower()
    assert pack["osCode"] == "Windows11"


def test_rugged_model_matches_only_rugged(agent):
    pack = agent._find_dell_driver_pack(CATALOG, "Latitude 5420 Rugged", target_os="Windows11")
    assert pack["dellVersion"] == "A00"
    assert "rugged" in pack["path"].lower()


# --------------------------------------------------------------------------- #
# OS filtering
# --------------------------------------------------------------------------- #

def test_os_filter_win11_picks_a13(agent):
    pack = agent._find_dell_driver_pack(CATALOG, "Latitude 5420", target_os="Windows11")
    assert pack["dellVersion"] == "A13"


def test_os_filter_win10_picks_newest_a18_not_stale_a10(agent):
    pack = agent._find_dell_driver_pack(CATALOG, "Latitude 5420", target_os="Windows10")
    assert pack["dellVersion"] == "A18"  # newest Win10, not the 2021 A10


def test_no_os_filter_picks_newest_overall(agent):
    pack = agent._find_dell_driver_pack(CATALOG, "Latitude 5420", target_os=None)
    # A13 and A18 share the newest date; A18 wins the version tie-break.
    assert pack["dellVersion"] == "A18"


def test_os_filter_relaxes_when_no_pack_for_os(agent):
    # No Win7 pack for the base 5420 -> filter relaxes, returns newest base pack.
    pack = agent._find_dell_driver_pack(CATALOG, "Latitude 5420", target_os="Windows7")
    assert pack["dellVersion"] in {"A13", "A18"}


def test_no_match_returns_none(agent):
    assert agent._find_dell_driver_pack(CATALOG, "Precision 7560", target_os="Windows11") is None


# --------------------------------------------------------------------------- #
# Substring fallback (only when no exact match)
# --------------------------------------------------------------------------- #

def test_substring_fallback_when_no_exact(agent):
    # "Latitude 542" isn't an exact model name, but is a substring of the
    # normalized base/rugged names -> fallback engages and still returns a pack.
    pack = agent._find_dell_driver_pack(CATALOG, "Latitude 542", target_os="Windows11")
    assert pack is not None


# --------------------------------------------------------------------------- #
# Multi-shape robustness (xmltodict dict-or-list, missing nodes)
# --------------------------------------------------------------------------- #

def test_handles_list_models_and_missing_os(agent):
    cat = {"DriverPackManifest": {"DriverPackage": [
        _pack("Latitude 5420", "A13", "Windows11", "2024-07-05T00:00:00", "p/x.exe",
              models=["Latitude 5420", "Latitude 5420 AIO"]),
        {"@name": "broken", "@dellVersion": "A01", "@path": "p/b.cab",
         "@dateTime": "bad-date", "SupportedSystems": None,
         "SupportedOperatingSystems": None},
    ]}}
    pack = agent._find_dell_driver_pack(cat, "Latitude 5420", target_os="Windows11")
    assert pack["dellVersion"] == "A13"


def test_single_pack_not_in_list(agent):
    cat = {"DriverPackManifest": {"DriverPackage":
           _pack("Latitude 5420", "A13", "Windows11", "2024-07-05T00:00:00", "p/x.exe")}}
    pack = agent._find_dell_driver_pack(cat, "Latitude 5420", target_os="Windows11")
    assert pack["dellVersion"] == "A13"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("raw,expected", [
    ("10.0.26200.7623", "Windows11"),
    ("10.0.22000.100", "Windows11"),
    ("10.0.19045.4000", "Windows10"),
    ("10.0.18363.1", "Windows10"),
    ("10.0.0", None),  # build 0 is degenerate -> no OS filter (safe)
    ("", None),
    (None, None),
    ("garbage", None),
])
def test_os_code_from_os_version(raw, expected):
    assert DiscoveryAgent._os_code_from_os_version(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("A13", 13), ("A00", 0), ("A9", 9), ("", -1), ("1.0", -1), (None, -1),
])
def test_dell_arev_to_int(raw, expected):
    assert DiscoveryAgent._dell_arev_to_int(raw) == expected


def test_normalize_model():
    assert DiscoveryAgent._normalize_model("  Latitude   5420 ") == "latitude 5420"
    assert DiscoveryAgent._normalize_model(None) == ""


# --------------------------------------------------------------------------- #
# Target-OS resolution
# --------------------------------------------------------------------------- #

def test_resolve_target_os_prefers_explicit_metadata(agent):
    job = SimpleNamespace(hardware_model="Latitude 5420",
                          job_metadata={"target_os": "Windows10"})
    assert agent._resolve_target_os(job) == "Windows10"


def test_resolve_target_os_none_without_model(agent):
    job = SimpleNamespace(hardware_model=None, job_metadata={})
    assert agent._resolve_target_os(job) is None


def test_resolve_target_os_from_intune_device(agent, monkeypatch):
    fake = Mock()
    fake.get.return_value = {"value": [
        {"model": "Latitude 5420", "operatingSystem": "Windows", "osVersion": "10.0.26200.7623"},
        {"model": "Virtual Machine", "operatingSystem": "Windows", "osVersion": "10.0.19045.1"},
    ]}
    monkeypatch.setattr("autopackager.utils.graph_client.GraphAPIClient", lambda: fake)
    job = SimpleNamespace(hardware_model="Latitude 5420", job_metadata={})
    assert agent._resolve_target_os(job) == "Windows11"


def test_resolve_target_os_graph_failure_degrades_to_none(agent, monkeypatch):
    def boom():
        raise RuntimeError("no graph")
    monkeypatch.setattr("autopackager.utils.graph_client.GraphAPIClient", boom)
    job = SimpleNamespace(hardware_model="Latitude 5420", job_metadata={})
    assert agent._resolve_target_os(job) is None
