"""Unit tests for the rewritten Lenovo SCCM driver-pack matcher.

The legacy ``_find_lenovo_driver`` assumed a ``Products -> Product -> Driver``
catalog that does not exist — the real ``catalogv2.xml`` is
``ModelList -> Model -> SCCM`` — so Lenovo discovery matched nothing. These
tests pin the rewrite: model match by machine Type or name, OS filtering
(win10/win11), and newest-pack selection.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from autopackager.agents.discovery.discovery_agent import DiscoveryAgent


def _sccm(os_code, version, date, url):
    return {"@os": os_code, "@version": version, "@date": date, "#text": url}


def _model(name, types, sccm, arch="Intel"):
    return {
        "@name": name,
        "@arch": arch,
        "Types": {"Type": types},
        "BIOS": {"@version": "1.0", "#text": "https://x/bios.exe"},
        "SCCM": sccm,
    }


# A Model with multiple SCCM packs spanning win10/win11 + feature releases.
# Lenovo sets @date per-model (uniform), so the Windows-release tie-break is
# what actually discriminates — mirror that here.
X1G9 = _model(
    "ThinkPad X1 Carbon 9TH Gen Type 20XW 20XX",
    ["20XW", "20XX"],
    [
        _sccm("win10", "1909", "2023-01-01", "https://x/w1064_1909.exe"),
        _sccm("win10", "22H2", "2023-01-01", "https://x/w1064_22h2.exe"),
        _sccm("win11", "22H2", "2023-01-01", "https://x/w11_22h2.exe"),
        _sccm("win11", "24H2", "2023-01-01", "https://x/w11_24h2.exe"),
    ],
)
X1G10 = _model(
    "ThinkPad X1 Carbon 10TH Gen Type 21CB 21CC",
    ["21CB", "21CC"],
    [_sccm("win11", "22H2", "2023-05-01", "https://x/g10_w11.exe")],
)
# BIOS-only model — no SCCM driver pack at all.
NODRIVERS = {"@name": "ThinkCentre M715Q", "@arch": "AMD",
             "Types": {"Type": ["10M4"]}, "BIOS": {"#text": "https://x/b.exe"}}

CATALOG = {"ModelList": {"@version": "1.0", "Model": [X1G9, X1G10, NODRIVERS]}}


@pytest.fixture
def agent():
    return DiscoveryAgent()


# --------------------------------------------------------------------------- #
# Catalog shape — legacy code matched nothing; this must match
# --------------------------------------------------------------------------- #

def test_finds_pack_in_real_modellist_shape(agent):
    pack = agent._find_lenovo_driver(CATALOG, "20XW", target_os="Windows11")
    assert pack is not None
    assert pack["name"] == "ThinkPad X1 Carbon 9TH Gen Type 20XW 20XX"


def test_legacy_products_shape_returns_none(agent):
    # A catalog in the OLD assumed shape yields nothing (no ModelList).
    legacy = {"Products": {"Product": {"@name": "ThinkPad", "Driver": []}}}
    assert agent._find_lenovo_driver(legacy, "ThinkPad", target_os="Windows11") is None


# --------------------------------------------------------------------------- #
# OS filtering + newest selection
# --------------------------------------------------------------------------- #

def test_win11_picks_newest_release(agent):
    pack = agent._find_lenovo_driver(CATALOG, "20XW", target_os="Windows11")
    assert pack["os_code"] == "win11"
    assert pack["windows_release"] == "24H2"
    assert pack["url"] == "https://x/w11_24h2.exe"


def test_win10_picks_newest_not_stale_1909(agent):
    pack = agent._find_lenovo_driver(CATALOG, "20XW", target_os="Windows10")
    assert pack["os_code"] == "win10"
    assert pack["windows_release"] == "22H2"


def test_no_os_filter_picks_newest_overall(agent):
    pack = agent._find_lenovo_driver(CATALOG, "20XW", target_os=None)
    assert pack["windows_release"] == "24H2"


def test_os_filter_relaxes_when_no_pack_for_os(agent):
    # X1G10 only has a win11 pack; asking for win10 relaxes the filter.
    pack = agent._find_lenovo_driver(CATALOG, "21CB", target_os="Windows10")
    assert pack["os_code"] == "win11"


# --------------------------------------------------------------------------- #
# Model matching — type code, exact name, substring fallback
# --------------------------------------------------------------------------- #

def test_match_by_machine_type_case_insensitive(agent):
    pack = agent._find_lenovo_driver(CATALOG, "21cb", target_os="Windows11")
    assert pack["name"] == "ThinkPad X1 Carbon 10TH Gen Type 21CB 21CC"


def test_match_by_exact_name(agent):
    pack = agent._find_lenovo_driver(
        CATALOG, "ThinkPad X1 Carbon 9TH Gen Type 20XW 20XX", target_os="Windows11")
    assert pack["windows_release"] == "24H2"


def test_match_by_name_substring(agent):
    pack = agent._find_lenovo_driver(CATALOG, "X1 Carbon 10TH Gen", target_os="Windows11")
    assert pack["name"] == "ThinkPad X1 Carbon 10TH Gen Type 21CB 21CC"


def test_no_match_returns_none(agent):
    assert agent._find_lenovo_driver(CATALOG, "Surface Laptop 5", target_os="Windows11") is None


def test_model_without_sccm_returns_none(agent):
    pack = agent._find_lenovo_driver(CATALOG, "10M4", target_os="Windows11")
    assert pack is None  # BIOS-only model has no driver pack


# --------------------------------------------------------------------------- #
# Robustness
# --------------------------------------------------------------------------- #

def test_single_model_and_single_sccm_not_in_lists(agent):
    cat = {"ModelList": {"Model": {
        "@name": "ThinkPad T14 Gen 3 Type 21AH 21AJ",
        "Types": {"Type": "21AH"},
        "SCCM": _sccm("win11", "22H2", "2023-01-01", "https://x/t14.exe"),
    }}}
    pack = agent._find_lenovo_driver(cat, "21AH", target_os="Windows11")
    assert pack["url"] == "https://x/t14.exe"


def test_driver_type_param_is_ignored(agent):
    # Whole-model SCCM bundle — driver_type doesn't change the result.
    a = agent._find_lenovo_driver(CATALOG, "20XW", driver_type="network", target_os="Windows11")
    b = agent._find_lenovo_driver(CATALOG, "20XW", driver_type=None, target_os="Windows11")
    assert a["url"] == b["url"]


@pytest.mark.parametrize("raw,expected", [
    ("22H2", 2202), ("21H2", 2102), ("24H2", 2402),
    ("1909", 1909), ("*", -1), ("", -1), (None, -1),
])
def test_win_release_key(raw, expected):
    assert DiscoveryAgent._win_release_key(raw) == expected


# --------------------------------------------------------------------------- #
# _discover_lenovo_driver integration (OS from job metadata)
# --------------------------------------------------------------------------- #

def test_discover_lenovo_uses_metadata_os(agent):
    job = SimpleNamespace(id=1, vendor="lenovo", hardware_model="20XW",
                          driver_type=None, current_version=None,
                          job_metadata={"target_os": "Windows11"})
    with patch.object(agent, "_download_lenovo_catalog", return_value="<xml/>"), \
         patch("autopackager.agents.discovery.discovery_agent.xmltodict.parse", return_value=CATALOG):
        result = agent._discover_lenovo_driver(job)
    assert result["update_available"] is True
    assert result["os_code"] == "win11"
    assert result["target_os"] == "Windows11"
    assert result["download_url"] == "https://x/w11_24h2.exe"
