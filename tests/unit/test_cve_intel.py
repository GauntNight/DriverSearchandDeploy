"""Unit tests for the CVE intelligence service (``services/cve_intel.py``) and
the demo CVE enrichment hook.

The curated fixture (``demo/fixtures/cve_intel.json``) is the offline source of
truth here — these tests run fully offline (mode='cache') and never touch NVD.
"""

import pytest

from autopackager.services import cve_intel

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _fresh_cache():
    cve_intel.reset_cache()
    yield
    cve_intel.reset_cache()


# --- severity bucketing ----------------------------------------------------

@pytest.mark.parametrize("score,expected", [
    (None, "unknown"),
    (0.0, "none"),
    (0.1, "low"),
    (3.9, "low"),
    (4.0, "medium"),
    (6.9, "medium"),
    (7.0, "high"),
    (8.9, "high"),
    (9.0, "critical"),
    (10.0, "critical"),
])
def test_severity_for_score(score, expected):
    assert cve_intel.severity_for_score(score) == expected


def test_severity_for_score_non_numeric():
    assert cve_intel.severity_for_score("oops") == "unknown"


# --- CPE normalization -----------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("cpe:2.3:a:videolan:vlc_media_player", "cpe:2.3:a:videolan:vlc_media_player"),
    ("cpe:2.3:a:videolan:vlc_media_player:3.0.20:*:*:*", "cpe:2.3:a:videolan:vlc_media_player"),
    ("videolan:vlc_media_player", "cpe:2.3:a:videolan:vlc_media_player"),
    ("CPE:2.3:A:7-Zip:7-Zip", "cpe:2.3:a:7-zip:7-zip"),
    (None, None),
    ("", None),
    ("garbage", None),
])
def test_normalize_cpe(raw, expected):
    assert cve_intel.normalize_cpe(raw) == expected


def test_slug():
    assert cve_intel._slug("VLC media player 3.0.21") == "vlc_media_player"
    assert cve_intel._slug("Python 3.14.5 (64-bit)") == "python"
    assert cve_intel._slug("7-Zip") == "7_zip"


# --- cache lookup + version filtering --------------------------------------

def test_vlc_outdated_lights_up():
    r = cve_intel.lookup("VLC media player",
                         cpe="cpe:2.3:a:videolan:vlc_media_player",
                         current_version="3.0.20", mode="cache")
    assert r["source"] == "cache"
    assert r["severity"] == "high"
    assert r["max_cvss"] == 8.0
    assert r["cve_count"] == 1
    assert r["cves"][0]["id"] == "CVE-2024-46461"


def test_vlc_current_is_clean():
    r = cve_intel.lookup("VLC media player", current_version="3.0.21", mode="cache")
    assert r["source"] == "cache"        # the product IS known...
    assert r["cve_count"] == 0           # ...but the deployed version is patched
    assert r["severity"] == "none"
    assert r["max_cvss"] is None


def test_seven_zip_version_filter_is_precise():
    # 24.08: only the 24.09-fixed MotW CVE applies; the 24.07-fixed one is patched.
    r = cve_intel.lookup("7-Zip", current_version="24.08", mode="cache")
    ids = {c["id"] for c in r["cves"]}
    assert ids == {"CVE-2025-0411"}
    assert r["cve_count"] == 1
    # 24.00: both apply.
    r2 = cve_intel.lookup("7-Zip", current_version="24.00", mode="cache")
    assert r2["cve_count"] == 2
    assert r2["max_cvss"] == 7.8


def test_python_critical_hero():
    r = cve_intel.lookup("Python 3.12.0", current_version="3.12.0", mode="cache")
    assert r["severity"] == "critical"
    assert r["max_cvss"] == 10.0
    # worst-first: the critical sorts above the high
    assert r["cves"][0]["id"] == "CVE-2024-12718"
    assert r["cves"][0]["severity"] == "critical"


def test_python_current_is_clean():
    r = cve_intel.lookup("Python 3.14.5", current_version="3.14.5", mode="cache")
    assert r["cve_count"] == 0
    assert r["severity"] == "none"


def test_resolves_by_name_without_cpe():
    # No catalog CPE — resolution falls back to the display-name alias.
    r = cve_intel.lookup("Notepad++", current_version="8.8.1", mode="cache")
    assert r["cve_count"] == 1
    assert r["max_cvss"] == 7.3


def test_unknown_product_returns_no_data():
    r = cve_intel.lookup("Totally Unknown App", current_version="1.0", mode="cache")
    assert r["source"] == "none"
    assert r["cve_count"] == 0


def test_off_mode_disables():
    r = cve_intel.lookup("VLC media player", current_version="3.0.20", mode="off")
    assert r["source"] == "none"
    assert r["cve_count"] == 0


def test_no_version_keeps_all_cves():
    r = cve_intel.lookup("7-Zip", mode="cache")  # no current_version
    assert r["cve_count"] == 2


# --- _affects unit ---------------------------------------------------------

def test_affects_latest_bound():
    cve = {"id": "X", "fixed_in": "25.00"}
    # current is vulnerable, but the chosen upgrade target (24.09) doesn't reach the fix
    assert cve_intel._affects(cve, "24.00", "24.09") is False
    # upgrade target reaches the fix
    assert cve_intel._affects(cve, "24.00", "25.00") is True


def test_affects_no_fixed_in_kept():
    assert cve_intel._affects({"id": "X"}, "1.0", "2.0") is True


# --- sorting ---------------------------------------------------------------

def test_risk_sort_key_orders_worst_first():
    crit = {"severity": "critical", "max_cvss": 9.1, "cve_count": 1}
    high = {"severity": "high", "max_cvss": 8.9, "cve_count": 5}
    none = {"severity": "none", "max_cvss": None, "cve_count": 0}
    ordered = sorted([none, high, crit], key=cve_intel.risk_sort_key, reverse=True)
    assert [b["severity"] for b in ordered] == ["critical", "high", "none"]


def test_scan_apps_sorts_and_enriches():
    apps = [
        {"name": "VLC media player", "version": "3.0.20"},   # high
        {"name": "Python 3.12.0", "version": "3.12.0"},      # critical
        {"name": "Unknown", "version": "1.0"},               # no data
    ]
    out = cve_intel.scan_apps(apps, mode="cache")
    assert out[0]["name"] == "Python 3.12.0"
    assert out[0]["cve"]["severity"] == "critical"
    # every app got a block
    assert all("cve" in a for a in out)


# --- NVD parser (offline; no network) --------------------------------------

def test_nvd_normalize_parses_score_and_fixed_in():
    sample = {
        "id": "CVE-2030-0001",
        "published": "2030-01-02T00:00:00.000",
        "descriptions": [
            {"lang": "es", "value": "no"},
            {"lang": "en", "value": "A buffer overflow."},
        ],
        "metrics": {
            "cvssMetricV31": [
                {"cvssData": {"baseScore": 9.8, "baseSeverity": "CRITICAL"}}
            ]
        },
        "configurations": [
            {"nodes": [
                {"cpeMatch": [
                    {"vulnerable": True, "versionEndExcluding": "2.5"},
                    {"vulnerable": True, "versionEndExcluding": "2.0"},
                ]}
            ]}
        ],
    }
    rec = cve_intel._nvd_normalize(sample, "cpe:2.3:a:x:y")
    assert rec["id"] == "CVE-2030-0001"
    assert rec["cvss"] == 9.8
    assert rec["severity"] == "critical"
    assert rec["summary"] == "A buffer overflow."
    assert rec["fixed_in"] == "2.0"     # smallest versionEndExcluding
    assert rec["published"] == "2030-01-02"


def test_empty_block_shape():
    b = cve_intel.empty_block()
    for key in ("max_cvss", "severity", "cve_count", "cves", "source",
                "fixed_by_upgrade"):
        assert key in b
    assert b["cve_count"] == 0
    assert b["cves"] == []


# --- enrichment hook -------------------------------------------------------

def test_enrich_cves_attaches_blocks():
    from demo import intune_view

    apps = [
        {"id": "a1", "name": "VLC media player", "version": "3.0.20"},
        {"id": "a2", "name": "Python 3.14.5", "version": "3.14.5"},
    ]
    intune_view.enrich_cves(apps)
    assert apps[0]["cve"]["severity"] == "high"
    assert apps[1]["cve"]["cve_count"] == 0


def test_catalog_entry_carries_cpe():
    from autopackager.utils import installer_catalog

    cat = installer_catalog.load_catalog()
    vlc = cat.by_id("vlc-media-player")
    assert vlc is not None
    assert vlc.cpe == "cpe:2.3:a:videolan:vlc_media_player"
