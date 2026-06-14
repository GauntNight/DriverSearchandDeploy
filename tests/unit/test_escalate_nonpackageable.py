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


# --- Unidentifiable EXE (no readable VS_VERSIONINFO + no catalog match) -------
# VLC's NSIS .exe yields all-empty PE metadata; with no catalog entry it must
# escalate, not publish a malformed app (filename name, "unknown" version, a
# placeholder detection rule Intune can never satisfy).

def test_exe_has_identity_helper():
    from demo import intake
    assert intake._exe_has_identity({}) is False
    assert intake._exe_has_identity(
        {"product_name": "", "company_name": "", "file_version": ""}) is False
    assert intake._exe_has_identity({"product_name": "VLC media player"}) is True
    assert intake._exe_has_identity({"file_version": "3.0.20.0"}) is True


def test_unreadable_exe_escalates(tmp_path):
    from unittest import mock
    from demo import intake

    # A filename matching NO catalog filename_pattern (so the identity-less
    # filename match below can't rescue it) + all-blank PE metadata must escalate.
    p = tmp_path / "mystery-setup.exe"
    p.write_bytes(b"MZ not a real pe")
    with mock.patch("autopackager.utils.pe_metadata.read_pe_metadata") as rp, \
         mock.patch("autopackager.utils.pe_metadata.sha256_file", return_value="abc123"):
        rp.return_value.to_dict.return_value = {
            "product_name": "", "company_name": "", "product_version": "",
            "file_version": "", "original_filename": "", "file_description": "",
        }
        a = intake.analyze(p)
    assert a.escalate is True
    assert a.escalate_reason and "MSI" in a.escalate_reason
    assert a.blocker == a.escalate_reason


def test_identifiable_exe_miss_does_not_escalate(tmp_path):
    # An EXE we CAN read identity from is a normal miss (research path), not an
    # escalation — only the fully-unreadable case escalates.
    from unittest import mock
    from demo import intake

    p = tmp_path / "SomeApp-setup.exe"
    p.write_bytes(b"MZ not a real pe")
    with mock.patch("autopackager.utils.pe_metadata.read_pe_metadata") as rp, \
         mock.patch("autopackager.utils.pe_metadata.sha256_file", return_value="abc123"):
        rp.return_value.to_dict.return_value = {
            "product_name": "Some App", "company_name": "Some Vendor",
            "product_version": "1.2.3",
        }
        a = intake.analyze(p)
    assert a.escalate is False
    assert a.branch == "miss"


# --- Upgrade path respects the escalate guard --------------------------------
# A version-check (esp. live mode) can hand back a vendor UI .exe whose metadata
# is unreadable. The upgrade must fail cleanly, NOT publish a malformed
# superseding app.

def test_finalize_upgrade_escalates_instead_of_publishing(tmp_path):
    from unittest import mock
    from demo import intake

    inst = tmp_path / "vlc-3.0.23-win64.exe"
    inst.write_bytes(b"MZ fake")
    esc = intake.Analysis(
        kind="exe", path=str(inst), filename=inst.name, branch="miss",
        escalate=True, escalate_reason="no readable version info; use the MSI",
    )
    with mock.patch.object(intake, "analyze", return_value=esc), \
         mock.patch.object(intake, "dispatch_pipeline") as dispatch, \
         mock.patch.object(intake, "_apply_upgrade_metadata") as apply_md, \
         mock.patch.object(intake, "OrchestrationEngine") as Engine, \
         mock.patch("demo.events.publish_pipeline_event"), \
         mock.patch("demo.events.publish_end"):
        intake.finalize_upgrade_job(42, "old-app-id", str(inst), "all")
        # The malformed installer must NOT be packaged/dispatched...
        dispatch.assert_not_called()
        apply_md.assert_not_called()
        # ...and the job must be marked failed.
        Engine.return_value.update_job_state.assert_called_once()


# --- Identity-less EXE rescued by a filename catalog entry --------------------
# VLC ships an NSIS .exe with NO VS_VERSIONINFO. A catalog entry keyed by
# filename_pattern (with pe_product_name as the canonical name) must match it,
# and analyze() must inherit name/publisher from the entry + parse the version
# from the filename — so it packages as "VLC media player", not a malformed app.

def test_match_exe_filename_only_for_identityless_installer():
    from autopackager.utils.installer_catalog import Catalog, CatalogEntry
    cat = Catalog(entries=[CatalogEntry(
        id="vlc-exe", type="exe", installer_family="nsis",
        filename_pattern="vlc-", pe_product_name="VLC media player",
        publisher="VideoLAN", install_command_template='"{installer_filename}" /S',
        detection_rules=[{"kind": "file_version", "path": r"C:\X", "file": "vlc.exe",
                          "operator": "greaterThanOrEqual", "value": "3.0.0.0"}],
    )])
    # All-blank PE metadata (the real VLC .exe case) still matches by filename.
    m = cat.match_exe(pe_metadata={"company_name": "", "product_name": ""},
                      sha256=None, filename="vlc-3.0.20-win64.exe")
    assert m is not None and m.id == "vlc-exe"
    # A non-matching filename with blank PE does NOT match.
    assert cat.match_exe(pe_metadata={}, sha256=None, filename="other.exe") is None


def test_version_from_filename_helper():
    from demo import intake
    assert intake._version_from_filename("vlc-3.0.20-win64.exe") == "3.0.20"
    assert intake._version_from_filename("vlc-3.0.21-win32.exe") == "3.0.21"
    assert intake._version_from_filename("setup.exe") is None


def test_real_baseline_has_vlc_exe_entry():
    from autopackager.utils import installer_catalog
    e = installer_catalog.load_catalog().by_id("vlc-media-player-exe")
    assert e is not None and e.type == "exe"
    assert e.filename_pattern and e.pe_product_name == "VLC media player"
    assert e.detection_rules  # must publish with a real detection rule


# --- Unrecognized installer type (no extension / wrong extension) -------------
# A download with no installer extension — e.g. a vendor "stable channel" URL
# saved as 'stable' — must escalate before any parser runs, NOT get mis-routed
# to the MSI parser and published as a malformed `msiexec /i stable` app.

def test_no_extension_file_escalates(tmp_path):
    from demo import intake
    p = tmp_path / "stable"          # no extension
    p.write_bytes(b"not an installer")
    a = intake.analyze(p)
    assert a.escalate is True
    assert a.kind == "unknown"
    assert a.branch == "miss"
    assert "not a recognized installer" in (a.escalate_reason or "")
    assert a.blocker == a.escalate_reason
    # Never computes a bogus install command for an unrecognized type.
    assert a.install_command is None


def test_unknown_extension_escalates(tmp_path):
    from demo import intake
    p = tmp_path / "readme.txt"
    p.write_bytes(b"hello")
    a = intake.analyze(p)
    assert a.escalate is True
    assert a.install_command is None


def test_is_known_installer_helper():
    from demo import intake
    assert intake.is_known_installer("foo.msi") is True
    assert intake.is_known_installer("foo.exe") is True
    assert intake.is_known_installer("foo.zip") is True
    assert intake.is_known_installer("https://x.test/setup.exe?token=1") is True
    assert intake.is_known_installer("stable") is False
    assert intake.is_known_installer("https://x.test/stable") is False
    assert intake.is_known_installer(None) is False


# --- Empty / unreadable MSI Property table ------------------------------------
# A file that ends in .msi but whose Property table is empty (corrupt, or a
# non-MSI renamed) yields no identity. With no catalog match it must escalate,
# NOT publish a malformed app named after the filename with a placeholder rule.

def test_msi_has_identity_helper():
    from demo import intake
    assert intake._msi_has_identity({}) is False
    assert intake._msi_has_identity({"product_name": "", "product_code": ""}) is False
    assert intake._msi_has_identity({"product_name": "7-Zip"}) is True
    assert intake._msi_has_identity({"upgrade_code": "{GUID}"}) is True
    assert intake._msi_has_identity({"product_code": "{GUID}"}) is True


def test_empty_msi_escalates(monkeypatch, tmp_path):
    from demo import intake
    from autopackager.utils.installer_catalog import Catalog

    p = tmp_path / "mystery.msi"
    p.write_bytes(b"not a real msi")
    meta = Mock()
    meta.to_dict = lambda: {}     # empty Property table
    monkeypatch.setattr("autopackager.utils.msi_metadata.read_msi_metadata", lambda x: meta)
    monkeypatch.setattr("autopackager.utils.installer_catalog.load_catalog",
                        lambda: Catalog(entries=[]))
    a = intake.analyze(p)
    assert a.escalate is True
    assert "not a valid MSI" in (a.escalate_reason or "")
    assert a.install_command is None


def test_identifiable_msi_miss_does_not_escalate(monkeypatch, tmp_path):
    # A readable MSI that matches no catalog entry is a normal miss with the
    # deterministic default command — NOT an escalation.
    from demo import intake
    from autopackager.utils.installer_catalog import Catalog

    p = tmp_path / "someapp.msi"
    p.write_bytes(b"not a real msi")
    meta = Mock()
    meta.to_dict = lambda: {"product_name": "Some App", "product_code": "{GUID}"}
    monkeypatch.setattr("autopackager.utils.msi_metadata.read_msi_metadata", lambda x: meta)
    monkeypatch.setattr("autopackager.utils.installer_catalog.load_catalog",
                        lambda: Catalog(entries=[]))
    a = intake.analyze(p)
    assert a.escalate is False
    assert a.branch == "miss"
    assert a.install_command == "msiexec /i someapp.msi /qn /norestart"
