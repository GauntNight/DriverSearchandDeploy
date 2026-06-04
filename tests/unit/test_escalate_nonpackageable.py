"""Known non-packageable installers (escalate / don't package) + the
detached-installer reaper denylist for RealPlayer.

RealPlayer is consumer bundleware: no silent install/uninstall, no managed
build to redirect to. The catalog flags it with ``escalate_reason`` so intake
escalates immediately (no install attempt), and its detaching stub processes
are on the reaper denylist so an escalated run leaves no trace.
"""

from pathlib import Path
from unittest.mock import Mock

from autopackager.utils.installer_catalog import Catalog, CatalogEntry


def _escalate_catalog():
    return Catalog(entries=[CatalogEntry(
        id="realplayer", type="exe",
        pe_company_name="RealNetworks", pe_product_name="RealNetworks Installer",
        escalate_reason="consumer bundleware; no silent install — escalate",
    )])


def test_catalog_entry_without_install_command_loads():
    # Marker entries (escalate / consumer redirect) need no install command.
    e = CatalogEntry(id="x", type="exe", escalate_reason="nope")
    assert e.install_command_template == ""
    assert e.escalate_reason == "nope"


def test_match_exe_resolves_escalate_entry():
    cat = _escalate_catalog()
    m = cat.match_exe(
        pe_metadata={"company_name": "RealNetworks, Inc.",
                     "product_name": "RealNetworks Installer (32-bit)"},
        sha256=None, filename="RealPlayer.exe",
    )
    assert m is not None and m.id == "realplayer"
    assert m.escalate_reason


def test_analyze_flags_escalate_before_any_install(monkeypatch, tmp_path):
    from demo import intake

    pe = Mock()
    pe.to_dict = lambda: {"company_name": "RealNetworks, Inc.",
                          "product_name": "RealNetworks Installer (32-bit)"}
    monkeypatch.setattr("autopackager.utils.pe_metadata.read_pe_metadata", lambda p: pe)
    monkeypatch.setattr("autopackager.utils.pe_metadata.sha256_file", lambda p: "deadbeef")
    monkeypatch.setattr("autopackager.utils.installer_catalog.load_catalog", _escalate_catalog)

    analysis = intake.analyze(tmp_path / "RealPlayer.exe")
    assert analysis.escalate is True
    assert "bundleware" in (analysis.escalate_reason or "")
    # Escalation short-circuits before any install command is computed.
    assert analysis.install_command is None
    d = analysis.to_dict()
    assert d["escalate"] is True and d["escalate_reason"]


def test_reaper_denylist_includes_realplayer():
    from autopackager.agents.testing import local_install_validator as liv
    for name in ("realplay.exe", "realplayerupdatesvc.exe", "rndlp.exe", "rpdsvc.exe"):
        assert name in liv._DETACHED_INSTALLER_NAMES


def test_real_baseline_catalog_has_realplayer_escalate_entry():
    # The shipped baseline must carry the RealPlayer escalate marker.
    from autopackager.utils import installer_catalog
    e = installer_catalog.load_catalog().by_id("realplayer")
    assert e is not None
    assert e.escalate_reason
    assert not e.detection_rules  # never publishes
