"""Unit tests for autopackager.utils.installer_catalog.

The module reads two YAML files from fixed locations on disk. These tests
redirect both paths to a temp directory via monkeypatch so they never touch
the committed baseline or the operator's overlay.
"""

from __future__ import annotations

import yaml

import pytest

from autopackager.utils import installer_catalog as ic


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_catalog_paths(tmp_path, monkeypatch):
    """Point BASELINE_PATH and LOCAL_PATH at temp files for the test."""
    baseline = tmp_path / "baseline.yaml"
    local = tmp_path / "local.yaml"
    monkeypatch.setattr(ic, "BASELINE_PATH", baseline)
    monkeypatch.setattr(ic, "LOCAL_PATH", local)
    return baseline, local


def _write_yaml(path, payload):
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


SEED_BASELINE = {
    "version": 1,
    "entries": [
        {
            "id": "7-zip",
            "type": "msi",
            "upgrade_code": "{23170F69-40C1-2702-0000-000004000000}",
            "product_name_pattern": "7-Zip",
            "publisher": "Igor Pavlov",
            "install_command_template": "msiexec /i {installer_filename} /qn /norestart",
            "first_seen": "2026-05-29",
            "last_used": "2026-05-29",
            "use_count": 1,
        }
    ],
}


SEVEN_ZIP_MSI_METADATA = {
    "product_name": "7-Zip 24.08 (x64 edition)",
    "product_version": "24.08.00.0",
    "product_code": "{23170F69-40C1-2702-2408-000001000000}",
    "upgrade_code": "{23170F69-40C1-2702-0000-000004000000}",
    "manufacturer": "Igor Pavlov",
}


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------

class TestMatchMsi:
    def test_match_by_upgrade_code(self, temp_catalog_paths):
        baseline, _local = temp_catalog_paths
        _write_yaml(baseline, SEED_BASELINE)

        catalog = ic.load_catalog()
        match = catalog.match_msi(SEVEN_ZIP_MSI_METADATA)

        assert match is not None
        assert match.id == "7-zip"

    def test_match_by_upgrade_code_is_brace_and_case_insensitive(self, temp_catalog_paths):
        baseline, _ = temp_catalog_paths
        _write_yaml(baseline, SEED_BASELINE)

        catalog = ic.load_catalog()
        match = catalog.match_msi({
            **SEVEN_ZIP_MSI_METADATA,
            "upgrade_code": "23170F69-40C1-2702-0000-000004000000",  # no braces, mixed case
        })
        assert match is not None
        assert match.id == "7-zip"

    def test_match_by_product_name_when_upgrade_code_missing(self, temp_catalog_paths):
        baseline, _ = temp_catalog_paths
        _write_yaml(baseline, SEED_BASELINE)

        catalog = ic.load_catalog()
        match = catalog.match_msi({
            "product_name": "7-Zip 24.08 (x64 edition)",
            "manufacturer": "Igor Pavlov",
        })
        assert match is not None
        assert match.id == "7-zip"

    def test_no_match_returns_none(self, temp_catalog_paths):
        baseline, _ = temp_catalog_paths
        _write_yaml(baseline, SEED_BASELINE)

        catalog = ic.load_catalog()
        match = catalog.match_msi({
            "product_name": "Totally Different App",
            "manufacturer": "Someone Else",
            "upgrade_code": "{00000000-0000-0000-0000-000000000000}",
        })
        assert match is None

    def test_empty_metadata_returns_none(self, temp_catalog_paths):
        baseline, _ = temp_catalog_paths
        _write_yaml(baseline, SEED_BASELINE)

        assert ic.load_catalog().match_msi({}) is None
        assert ic.load_catalog().match_msi(None) is None


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

class TestRenderInstallCommand:
    def test_substitutes_installer_filename(self, temp_catalog_paths):
        baseline, _ = temp_catalog_paths
        _write_yaml(baseline, SEED_BASELINE)

        entry = ic.load_catalog().by_id("7-zip")
        rendered = entry.render_install_command("7z2408-x64.msi")

        assert rendered == "msiexec /i 7z2408-x64.msi /qn /norestart"


# ---------------------------------------------------------------------------
# Append / record
# ---------------------------------------------------------------------------

class TestAddMsiEntry:
    def test_new_msi_creates_overlay_entry(self, temp_catalog_paths):
        baseline, local = temp_catalog_paths
        _write_yaml(baseline, {"version": 1, "entries": []})

        entry = ic.add_msi_entry(
            {
                "product_name": "Notepad++",
                "manufacturer": "Notepad++ Team",
                "upgrade_code": "{11111111-1111-1111-1111-111111111111}",
                "product_code": "{22222222-2222-2222-2222-222222222222}",
            },
            install_command_template="msiexec /i {installer_filename} /qn",
        )

        assert entry.id == "notepad"
        assert entry.use_count == 1
        assert entry.first_seen == entry.last_used  # same day

        # Persisted to overlay
        on_disk = yaml.safe_load(local.read_text(encoding="utf-8"))
        assert on_disk["version"] == 1
        ids = [e["id"] for e in on_disk["entries"]]
        assert "notepad" in ids

    def test_duplicate_add_falls_through_to_record_use(self, temp_catalog_paths):
        baseline, local = temp_catalog_paths
        _write_yaml(baseline, {"version": 1, "entries": []})

        ic.add_msi_entry(
            SEVEN_ZIP_MSI_METADATA,
            install_command_template="msiexec /i {installer_filename} /qn /norestart",
        )
        ic.add_msi_entry(
            SEVEN_ZIP_MSI_METADATA,
            install_command_template="msiexec /i {installer_filename} /qn /norestart",
        )

        on_disk = yaml.safe_load(local.read_text(encoding="utf-8"))
        matching = [e for e in on_disk["entries"] if e["id"].startswith("7-zip")]
        assert len(matching) == 1
        assert matching[0]["use_count"] == 2


class TestRecordUse:
    def test_baseline_entry_use_increments_in_overlay_without_modifying_baseline(
        self, temp_catalog_paths
    ):
        baseline, local = temp_catalog_paths
        _write_yaml(baseline, SEED_BASELINE)

        original_baseline_bytes = baseline.read_bytes()

        ic.record_use("7-zip")
        ic.record_use("7-zip")

        assert baseline.read_bytes() == original_baseline_bytes  # untouched

        overlay = yaml.safe_load(local.read_text(encoding="utf-8"))
        seven_zip = next(e for e in overlay["entries"] if e["id"] == "7-zip")
        # Baseline started at use_count=1; overlay copy gets bumped twice from there.
        assert seven_zip["use_count"] == 3

    def test_unknown_entry_is_no_op(self, temp_catalog_paths, caplog):
        baseline, local = temp_catalog_paths
        _write_yaml(baseline, {"version": 1, "entries": []})

        ic.record_use("never-heard-of-it")

        assert not local.exists() or yaml.safe_load(local.read_text())["entries"] == []


# ---------------------------------------------------------------------------
# Merge precedence
# ---------------------------------------------------------------------------

class TestUninstallTemplate:
    def test_add_msi_entry_populates_uninstall_from_product_code(self, temp_catalog_paths):
        baseline, local = temp_catalog_paths
        _write_yaml(baseline, {"version": 1, "entries": []})

        entry = ic.add_msi_entry(
            SEVEN_ZIP_MSI_METADATA,
            install_command_template="msiexec /i {installer_filename} /qn /norestart",
        )

        assert entry.uninstall_command_template == (
            "msiexec /x {23170F69-40C1-2702-2408-000001000000} /qn /norestart"
        )

    def test_add_msi_entry_skips_uninstall_when_no_product_code(self, temp_catalog_paths):
        baseline, _ = temp_catalog_paths
        _write_yaml(baseline, {"version": 1, "entries": []})

        entry = ic.add_msi_entry(
            {"product_name": "Mystery App", "manufacturer": "Anon"},
            install_command_template="msiexec /i {installer_filename} /qn",
        )

        assert entry.uninstall_command_template is None

    def test_render_uninstall_command_returns_template_verbatim(self, temp_catalog_paths):
        baseline, _ = temp_catalog_paths
        _write_yaml(baseline, {
            "version": 1,
            "entries": [
                {
                    **SEED_BASELINE["entries"][0],
                    "uninstall_command_template": (
                        "msiexec /x {23170F69-40C1-2702-2408-000001000000} /qn /norestart"
                    ),
                }
            ],
        })

        entry = ic.load_catalog().by_id("7-zip")
        assert entry.render_uninstall_command() == (
            "msiexec /x {23170F69-40C1-2702-2408-000001000000} /qn /norestart"
        )


class TestMatchByProductCode:
    def test_finds_entry(self, temp_catalog_paths):
        baseline, _ = temp_catalog_paths
        _write_yaml(baseline, {
            "version": 1,
            "entries": [
                {
                    **SEED_BASELINE["entries"][0],
                    "product_code": "{23170F69-40C1-2702-2408-000001000000}",
                }
            ],
        })

        catalog = ic.load_catalog()
        entry = catalog.match_by_product_code("{23170F69-40C1-2702-2408-000001000000}")

        assert entry is not None
        assert entry.id == "7-zip"

    def test_brace_and_case_insensitive(self, temp_catalog_paths):
        baseline, _ = temp_catalog_paths
        _write_yaml(baseline, {
            "version": 1,
            "entries": [
                {
                    **SEED_BASELINE["entries"][0],
                    "product_code": "{23170F69-40C1-2702-2408-000001000000}",
                }
            ],
        })

        catalog = ic.load_catalog()
        entry = catalog.match_by_product_code("23170f69-40c1-2702-2408-000001000000")
        assert entry is not None and entry.id == "7-zip"

    def test_no_match_returns_none(self, temp_catalog_paths):
        baseline, _ = temp_catalog_paths
        _write_yaml(baseline, SEED_BASELINE)

        assert ic.load_catalog().match_by_product_code(
            "{00000000-0000-0000-0000-000000000000}"
        ) is None
        assert ic.load_catalog().match_by_product_code(None) is None


class TestRecordVerification:
    def test_appends_verified_version_to_overlay(self, temp_catalog_paths):
        baseline, local = temp_catalog_paths
        _write_yaml(baseline, SEED_BASELINE)

        ic.record_verification(
            entry_id="7-zip",
            product_version="24.08.00.0",
            intune_app_id="4d015deb-f322-4543-9930-c76b7aa21f84",
        )

        on_disk = yaml.safe_load(local.read_text(encoding="utf-8"))
        verified = next(e for e in on_disk["entries"] if e["id"] == "7-zip")["verified_versions"]
        assert len(verified) == 1
        assert verified[0]["product_version"] == "24.08.00.0"
        assert verified[0]["verified_intune_app_id"] == "4d015deb-f322-4543-9930-c76b7aa21f84"
        assert verified[0]["verified_at"]  # date string present

    def test_idempotent_on_same_version_and_app_id(self, temp_catalog_paths):
        baseline, local = temp_catalog_paths
        _write_yaml(baseline, SEED_BASELINE)

        for _ in range(3):
            ic.record_verification("7-zip", "24.08.00.0", "4d015deb-...")

        on_disk = yaml.safe_load(local.read_text(encoding="utf-8"))
        verified = next(e for e in on_disk["entries"] if e["id"] == "7-zip")["verified_versions"]
        assert len(verified) == 1

    def test_distinct_versions_accumulate(self, temp_catalog_paths):
        baseline, local = temp_catalog_paths
        _write_yaml(baseline, SEED_BASELINE)

        ic.record_verification("7-zip", "24.08.00.0", "app-1")
        ic.record_verification("7-zip", "24.09.00.0", "app-2")

        on_disk = yaml.safe_load(local.read_text(encoding="utf-8"))
        verified = next(e for e in on_disk["entries"] if e["id"] == "7-zip")["verified_versions"]
        assert len(verified) == 2

    def test_unknown_entry_is_no_op(self, temp_catalog_paths):
        baseline, local = temp_catalog_paths
        _write_yaml(baseline, {"version": 1, "entries": []})

        ic.record_verification("nope", "1.0", "app-x")

        assert not local.exists() or yaml.safe_load(local.read_text())["entries"] == []

    def test_baseline_stays_untouched(self, temp_catalog_paths):
        baseline, _ = temp_catalog_paths
        _write_yaml(baseline, SEED_BASELINE)
        original = baseline.read_bytes()

        ic.record_verification("7-zip", "24.08.00.0", "app-z")

        assert baseline.read_bytes() == original


class TestLoadMergeOrder:
    def test_local_entry_overrides_baseline_with_same_id(self, temp_catalog_paths):
        baseline, local = temp_catalog_paths
        _write_yaml(baseline, SEED_BASELINE)
        _write_yaml(local, {
            "version": 1,
            "entries": [
                {
                    "id": "7-zip",
                    "type": "msi",
                    "install_command_template": "msiexec /i {installer_filename} /qn ADDLOCAL=ALL",
                    "notes": "Local override -- needs ADDLOCAL=ALL on this tenant",
                }
            ],
        })

        entry = ic.load_catalog().by_id("7-zip")
        assert "ADDLOCAL=ALL" in entry.install_command_template
        assert "Local override" in entry.notes


# ---------------------------------------------------------------------------
# EXE schema additions: installer_family, detection_rules, helpers
# ---------------------------------------------------------------------------

class TestInstallerFamilySwitches:
    """Stable mapping is load-bearing for EXE packaging: when an entry sets
    installer_family but omits install_command_template, the family default
    drives the silent-install string. A regression here ships installs with
    the wrong switches (or none) and breaks every EXE deployment.
    """

    def test_every_family_has_a_switch_entry(self):
        for fam in ic.INSTALLER_FAMILIES:
            assert fam in ic.INSTALLER_FAMILY_SWITCHES, fam

    def test_wrapped_families_have_no_direct_switches(self):
        # wrapped_msi / wrapped_zip must surface as None so callers know to
        # invoke a pre-stage extractor rather than running the EXE/ZIP
        # directly with a guessed switch string.
        assert ic.INSTALLER_FAMILY_SWITCHES['wrapped_msi'] is None
        assert ic.INSTALLER_FAMILY_SWITCHES['wrapped_zip'] is None

    def test_custom_family_returns_empty_string(self):
        # 'custom' means "operator supplied install_command_template; do
        # not append anything". Distinct from None (= no derivation).
        assert ic.INSTALLER_FAMILY_SWITCHES['custom'] == ''

    def test_known_family_defaults(self):
        assert ic.default_silent_switches('inno_setup') == '/VERYSILENT /NORESTART /SUPPRESSMSGBOXES'
        assert ic.default_silent_switches('nsis') == '/S'
        assert ic.default_silent_switches('msft_bootstrapper') == '/quiet /norestart'
        assert ic.default_silent_switches('wix_burn') == '/quiet /norestart'
        assert ic.default_silent_switches('msi') == '/qn /norestart'

    def test_unknown_family_returns_none(self):
        # None / typo / unknown: caller falls back to operator-supplied
        # install_command_template rather than running a wrong command.
        assert ic.default_silent_switches(None) is None
        assert ic.default_silent_switches('') is None
        assert ic.default_silent_switches('innosetup') is None  # typo


class TestDetectionRuleToGraph:
    """Catalog rule dicts must convert to Graph win32LobApp*Rule payloads
    exactly. A bad mapping ships broken detection that lets every device
    re-attempt the install forever (false negative), or marks every device
    "installed" when nothing changed (false positive).
    """

    def test_msi_product_code_with_version_compare(self):
        r = ic.detection_rule_to_graph({
            'kind': 'msi_product_code',
            'product_code': '{ABC}',
            'operator': 'greaterThanOrEqual',
            'version': '1.2.3',
        })
        assert r['@odata.type'] == '#microsoft.graph.win32LobAppProductCodeRule'
        assert r['ruleType'] == 'detection'
        assert r['productCode'] == '{ABC}'
        assert r['productVersionOperator'] == 'greaterThanOrEqual'
        assert r['productVersion'] == '1.2.3'

    def test_msi_product_code_minimal(self):
        r = ic.detection_rule_to_graph({'kind': 'msi_product_code', 'product_code': '{X}'})
        assert r['productCode'] == '{X}'
        assert r['productVersionOperator'] == 'notConfigured'
        assert r['productVersion'] is None

    def test_file_exists_with_file(self):
        r = ic.detection_rule_to_graph({
            'kind': 'file_exists',
            'path': r'C:\Program Files\App',
            'file': 'app.exe',
        })
        assert r['@odata.type'] == '#microsoft.graph.win32LobAppFileSystemRule'
        assert r['operationType'] == 'exists'
        assert r['path'] == r'C:\Program Files\App'
        assert r['fileOrFolderName'] == 'app.exe'
        assert r['check32BitOn64System'] is False

    def test_file_exists_with_folder(self):
        # Graph's fileOrFolderName carries either; we accept 'folder' as
        # the YAML alias for readability when the operator means a dir.
        r = ic.detection_rule_to_graph({
            'kind': 'file_exists',
            'path': r'C:\Program Files',
            'folder': 'AppName',
        })
        assert r['fileOrFolderName'] == 'AppName'

    def test_file_version_compare(self):
        r = ic.detection_rule_to_graph({
            'kind': 'file_version',
            'path': r'C:\Program Files\App',
            'file': 'app.exe',
            'operator': 'greaterThanOrEqual',
            'value': '1.0.0.0',
        })
        assert r['operationType'] == 'version'
        assert r['operator'] == 'greaterThanOrEqual'
        assert r['comparisonValue'] == '1.0.0.0'

    def test_registry_exists(self):
        r = ic.detection_rule_to_graph({
            'kind': 'registry_exists',
            'key': r'HKLM\Software\App',
            'value_name': 'Installed',
        })
        assert r['@odata.type'] == '#microsoft.graph.win32LobAppRegistryRule'
        assert r['operationType'] == 'exists'
        assert r['keyPath'] == r'HKLM\Software\App'
        assert r['valueName'] == 'Installed'

    def test_registry_value_string_compare(self):
        r = ic.detection_rule_to_graph({
            'kind': 'registry_value',
            'key': r'HKLM\Software\App',
            'value_name': 'Channel',
            'operator': 'equal',
            'value': 'stable',
        })
        assert r['operationType'] == 'string'
        assert r['comparisonValue'] == 'stable'

    def test_registry_version_compare(self):
        # The 90% case for EXE detection -- vendors put DisplayVersion under
        # the standard Uninstall key. Inno Setup / NSIS / Microsoft
        # bootstrappers all use this pattern.
        r = ic.detection_rule_to_graph({
            'kind': 'registry_version',
            'key': r'HKLM\Software\Microsoft\Windows\CurrentVersion\Uninstall\App',
            'value_name': 'DisplayVersion',
            'operator': 'greaterThanOrEqual',
            'value': '1.2.3',
        })
        assert r['operationType'] == 'version'
        assert r['operator'] == 'greaterThanOrEqual'
        assert r['comparisonValue'] == '1.2.3'

    def test_check_32bit_on_64bit_flows_through(self):
        r = ic.detection_rule_to_graph({
            'kind': 'registry_exists',
            'key': r'HKLM\Software\Wow6432Node\App',
            'check_32bit_on_64bit': True,
        })
        assert r['check32BitOn64System'] is True

    def test_requirement_rule_type_overrides_detection(self):
        # Requirement rules use the same shapes as detection rules; only
        # ruleType differs. Same converter, different rule_type kwarg.
        r = ic.detection_rule_to_graph(
            {'kind': 'file_exists', 'path': r'C:\\', 'file': 'x'},
            rule_type='requirement',
        )
        assert r['ruleType'] == 'requirement'

    def test_unknown_kind_raises(self):
        with pytest.raises(ValueError, match='Unknown detection rule kind'):
            ic.detection_rule_to_graph({'kind': 'magic_rule', 'foo': 'bar'})

    def test_missing_kind_raises(self):
        with pytest.raises(ValueError, match='Unknown detection rule kind'):
            ic.detection_rule_to_graph({'product_code': '{X}'})


class TestCatalogEntryExeFields:
    def test_loads_installer_family_and_detection_rules(self, temp_catalog_paths):
        baseline, _local = temp_catalog_paths
        _write_yaml(baseline, {
            'version': 1,
            'entries': [{
                'id': 'notepad-plus-plus',
                'type': 'exe',
                'installer_family': 'nsis',
                'pe_company_name': 'Notepad++ Team',
                'pe_product_name': 'Notepad++',
                'install_command_template': '{installer_filename} /S',
                'uninstall_command_template': r'"C:\Program Files\Notepad++\uninstall.exe" /S',
                'detection_rules': [
                    {
                        'kind': 'registry_version',
                        'key': r'HKLM\Software\Microsoft\Windows\CurrentVersion\Uninstall\Notepad++',
                        'value_name': 'DisplayVersion',
                        'operator': 'greaterThanOrEqual',
                        'value': '8.0.0',
                    }
                ],
            }],
        })
        e = ic.load_catalog().by_id('notepad-plus-plus')
        assert e.type == 'exe'
        assert e.installer_family == 'nsis'
        assert e.pe_company_name == 'Notepad++ Team'
        assert e.pe_product_name == 'Notepad++'
        assert isinstance(e.detection_rules, list)
        assert len(e.detection_rules) == 1
        assert e.detection_rules[0]['kind'] == 'registry_version'

    def test_existing_msi_entry_loads_without_new_fields(self, temp_catalog_paths):
        # New fields are all optional. A pre-existing MSI entry that
        # predates this schema bump must continue to load cleanly.
        baseline, _local = temp_catalog_paths
        _write_yaml(baseline, {
            'version': 1,
            'entries': [{
                'id': '7-zip', 'type': 'msi',
                'install_command_template': 'msiexec /i {installer_filename} /qn /norestart',
            }],
        })
        e = ic.load_catalog().by_id('7-zip')
        assert e.installer_family is None
        assert e.detection_rules is None

    def test_match_exe_by_sha256(self, temp_catalog_paths):
        """SHA-256 match wins over PE-name match -- pins a specific known
        build and avoids accidentally matching a different installer that
        happens to share CompanyName/ProductName.
        """
        baseline, _ = temp_catalog_paths
        _write_yaml(baseline, {
            'version': 1,
            'entries': [{
                'id': 'notepad-plus-plus',
                'type': 'exe',
                'install_command_template': '{installer_filename} /S',
                'sha256': 'abc123' + 'd' * 58,
                'pe_company_name': 'Notepad++ Team',
                'pe_product_name': 'Notepad++',
            }],
        })
        entry = ic.load_catalog().match_exe(sha256='abc123' + 'd' * 58)
        assert entry is not None
        assert entry.id == 'notepad-plus-plus'

    def test_match_exe_by_pe_company_and_product(self, temp_catalog_paths):
        """Falls back to CompanyName + ProductName when SHA-256 unmatched.
        Substring match in both directions so vendor build suffixes like
        "Notepad++ (32-bit)" still match the catalog "Notepad++" entry.
        """
        baseline, _ = temp_catalog_paths
        _write_yaml(baseline, {
            'version': 1,
            'entries': [{
                'id': 'notepad-plus-plus',
                'type': 'exe',
                'install_command_template': '{installer_filename} /S',
                'pe_company_name': 'Notepad++ Team',
                'pe_product_name': 'Notepad++',
            }],
        })
        entry = ic.load_catalog().match_exe(pe_metadata={
            'company_name': 'Notepad++ Team',
            'product_name': 'Notepad++ (32-bit)',
        })
        assert entry is not None
        assert entry.id == 'notepad-plus-plus'

    def test_match_exe_prefers_exact_product_name_over_substring_overlap(self, temp_catalog_paths):
        """When one entry's pe_product_name is a substring of another's
        (e.g. 'Snagit' vs 'Snagit 2023'), an installer with the shorter
        name must match the shorter entry -- not the longer one whose
        pattern happens to contain the installer name. Without an exact
        pass, catalog iteration order picks the wrong entry; surfaced live
        during the Snagit 2026-06-01 pressure test.
        """
        baseline, _ = temp_catalog_paths
        _write_yaml(baseline, {
            'version': 1,
            'entries': [
                {
                    'id': 'snagit-2023-bootstrapper',
                    'type': 'exe',
                    'install_command_template': '{installer_filename} /quiet',
                    'pe_company_name': 'TechSmith Corporation',
                    'pe_product_name': 'Snagit 2023',
                },
                {
                    'id': 'snagit-bootstrapper',
                    'type': 'exe',
                    'install_command_template': '{installer_filename} /quiet',
                    'pe_company_name': 'TechSmith Corporation',
                    'pe_product_name': 'Snagit',
                },
            ],
        })
        catalog = ic.load_catalog()
        # Installer is the NEW Snagit (no year suffix). Must hit snagit-bootstrapper.
        entry = catalog.match_exe(pe_metadata={
            'company_name': 'TechSmith Corporation',
            'product_name': 'Snagit',
        })
        assert entry is not None
        assert entry.id == 'snagit-bootstrapper'

        # Installer is the OLD Snagit 2023. Must still hit snagit-2023-bootstrapper
        # (exact match wins; substring fallback is not needed here).
        entry = catalog.match_exe(pe_metadata={
            'company_name': 'TechSmith Corporation',
            'product_name': 'Snagit 2023',
        })
        assert entry is not None
        assert entry.id == 'snagit-2023-bootstrapper'

    def test_match_exe_returns_none_when_unmatched(self, temp_catalog_paths):
        baseline, _ = temp_catalog_paths
        _write_yaml(baseline, {
            'version': 1,
            'entries': [{
                'id': 'notepad-plus-plus',
                'type': 'exe',
                'install_command_template': '{installer_filename} /S',
                'pe_company_name': 'Notepad++ Team',
                'pe_product_name': 'Notepad++',
            }],
        })
        # No SHA-256 match, PE names don't overlap
        entry = ic.load_catalog().match_exe(pe_metadata={
            'company_name': 'Some Other Vendor',
            'product_name': 'Unrelated App',
        })
        assert entry is None

    def test_wrapped_installer_fields_load_and_round_trip(self, temp_catalog_paths):
        """wrapped_msi entries need extract_command_template +
        extracted_msi_pattern to survive the YAML round trip; wrapped_zip
        only needs the pattern. Both must reach the dataclass intact so
        extract_wrapped() can read them.
        """
        baseline, _ = temp_catalog_paths
        _write_yaml(baseline, {
            'version': 1,
            'entries': [
                {
                    'id': 'powertoys', 'type': 'exe',
                    'installer_family': 'wrapped_msi',
                    'install_command_template': 'msiexec /i {installer_filename} /qn',
                    'extract_command_template': '"{installer_path}" --extract_msi',
                    'extracted_msi_pattern': 'PowerToys*.msi',
                },
                {
                    'id': 'foxit-pdf-reader', 'type': 'exe',
                    'installer_family': 'wrapped_zip',
                    'install_command_template': 'msiexec /i {installer_filename} /qn',
                    'extracted_msi_pattern': 'FoxitPDFReader*.msi',
                },
            ],
        })
        catalog = ic.load_catalog()
        pt = catalog.by_id('powertoys')
        assert pt.extract_command_template == '"{installer_path}" --extract_msi'
        assert pt.extracted_msi_pattern == 'PowerToys*.msi'

        foxit = catalog.by_id('foxit-pdf-reader')
        assert foxit.extract_command_template is None  # wrapped_zip doesn't need one
        assert foxit.extracted_msi_pattern == 'FoxitPDFReader*.msi'

    def test_match_exe_does_not_return_msi_entries(self, temp_catalog_paths):
        """match_exe must not return type=msi entries even when the
        PE/SHA fields would otherwise look like a match. Different
        installer type means different packaging path.
        """
        baseline, _ = temp_catalog_paths
        _write_yaml(baseline, {
            'version': 1,
            'entries': [{
                'id': '7-zip-msi', 'type': 'msi',
                'install_command_template': 'msiexec /i {installer_filename} /qn',
                'sha256': 'sharedsha' + 'd' * 55,
                # pe_* fields shouldn't be on MSI entries in practice but
                # tolerate them defensively.
                'pe_company_name': 'Igor Pavlov',
                'pe_product_name': '7-Zip',
            }],
        })
        assert ic.load_catalog().match_exe(sha256='sharedsha' + 'd' * 55) is None
        assert ic.load_catalog().match_exe(pe_metadata={
            'company_name': 'Igor Pavlov', 'product_name': '7-Zip',
        }) is None

    def test_distribution_field_loads_from_yaml(self, temp_catalog_paths):
        """Both DISTRIBUTION_KINDS values survive the YAML round trip.
        Mixed-distribution catalogs (standard + enterprise sitting
        side-by-side) are the intended use case -- many COTS apps ship
        both editions and the catalog must carry them distinctly.
        """
        baseline, _ = temp_catalog_paths
        _write_yaml(baseline, {
            'version': 1,
            'entries': [
                {
                    'id': 'acrobat-reader', 'type': 'exe',
                    'install_command_template': '{installer_filename} /qn',
                    'distribution': 'standard',
                },
                {
                    'id': 'acrobat-pro-enterprise', 'type': 'msi',
                    'install_command_template': 'msiexec /i {installer_filename} /qn',
                    'distribution': 'enterprise',
                },
            ],
        })
        catalog = ic.load_catalog()
        assert catalog.by_id('acrobat-reader').distribution == 'standard'
        assert catalog.by_id('acrobat-pro-enterprise').distribution == 'enterprise'

    def test_distribution_optional_on_load(self):
        """Existing catalogs that predate the field must still load. The
        loader does NOT infer 'standard' on missing values -- it leaves
        the field None so audits can distinguish 'unmarked' from
        'explicitly standard'. CLI helpers (add_msi_entry / add_exe_entry)
        do set 'standard' by default on newly-added entries.
        """
        from autopackager.utils.installer_catalog import _entry_from_dict
        entry = _entry_from_dict({
            'id': 'pre-existing', 'type': 'msi',
            'install_command_template': 'msiexec /i {installer_filename} /qn',
        })
        assert entry.distribution is None

    def test_distribution_kinds_controlled_vocabulary(self):
        """If we silently accept arbitrary distribution strings the
        ontology drifts (operators write 'Standard', 'ENT', 'biz', etc.).
        Pinning the set so a regression test catches typos at code-
        review time -- the loader itself is intentionally lenient (won't
        reject misspelled entries) because catalogs are shared across
        operators with different conventions, but downstream consumers
        should compare against this set.
        """
        assert ic.DISTRIBUTION_KINDS == {'standard', 'enterprise'}

    def test_add_msi_entry_defaults_distribution_to_standard(self, temp_catalog_paths):
        baseline, _ = temp_catalog_paths
        _write_yaml(baseline, {'version': 1, 'entries': []})
        entry = ic.add_msi_entry({
            'product_name': 'New App',
            'product_code': '{NEW}',
            'manufacturer': 'Vendor',
        }, install_command_template='msiexec /i {installer_filename} /qn')
        assert entry.distribution == 'standard'

    def test_add_msi_entry_accepts_enterprise_distribution(self, temp_catalog_paths):
        baseline, _ = temp_catalog_paths
        _write_yaml(baseline, {'version': 1, 'entries': []})
        entry = ic.add_msi_entry(
            {'product_name': 'Enterprise App', 'product_code': '{E}', 'manufacturer': 'V'},
            install_command_template='msiexec /i {installer_filename} /qn ENTERPRISE=1',
            distribution='enterprise',
        )
        assert entry.distribution == 'enterprise'

    def test_baseline_marks_every_entry_with_distribution(self):
        """Belt-and-suspenders contract: the committed baseline must mark
        every entry's distribution. Catalogs landing in main without this
        marker leak un-audited entries into every downstream operator's
        merged view.
        """
        from autopackager.utils.installer_catalog import (
            BASELINE_PATH, _load_yaml_file, _entry_from_dict,
        )
        if not BASELINE_PATH.exists():
            pytest.skip(f"Baseline missing at {BASELINE_PATH}")
        raw = _load_yaml_file(BASELINE_PATH)
        unmarked = []
        for entry_raw in raw.get('entries') or []:
            entry = _entry_from_dict(entry_raw)
            if entry and entry.distribution is None:
                unmarked.append(entry.id)
        assert not unmarked, (
            f"Baseline entries missing 'distribution': {unmarked}. "
            "Mark explicitly as 'standard' or 'enterprise'."
        )

    def test_supersedence_field_loads_from_yaml(self, temp_catalog_paths):
        """All four mode values round-trip through YAML, including the
        mode-specific extras (version_pattern, supersedes list).
        """
        baseline, _ = temp_catalog_paths
        _write_yaml(baseline, {
            'version': 1,
            'entries': [
                {
                    'id': 'vlc', 'type': 'msi', 'distribution': 'standard',
                    'install_command_template': 'msiexec /i {installer_filename} /qn',
                    'supersedence': {'line': 'vlc', 'mode': 'generic'},
                },
                {
                    'id': 'foo-1.6', 'type': 'msi', 'distribution': 'standard',
                    'install_command_template': 'msiexec /i {installer_filename} /qn',
                    'supersedence': {
                        'line': 'foo', 'mode': 'specific',
                        'version_pattern': r'^1\.6\.\d+$',
                    },
                },
                {
                    'id': 'enterprise-app-2008', 'type': 'msi', 'distribution': 'enterprise',
                    'install_command_template': 'msiexec /i {installer_filename} /qn',
                    'supersedence': {
                        'mode': 'manual',
                        'supersedes': ['enterprise-app-2007', 'enterprise-app-2006'],
                    },
                },
                {
                    'id': 'java-jdk-17', 'type': 'msi', 'distribution': 'standard',
                    'install_command_template': 'msiexec /i {installer_filename} /qn',
                    'supersedence': {'mode': 'none'},
                },
            ],
        })
        catalog = ic.load_catalog()
        assert catalog.by_id('vlc').supersedence == {'line': 'vlc', 'mode': 'generic'}
        assert catalog.by_id('foo-1.6').supersedence['version_pattern'] == r'^1\.6\.\d+$'
        assert catalog.by_id('enterprise-app-2008').supersedence['supersedes'] == [
            'enterprise-app-2007', 'enterprise-app-2006',
        ]
        assert catalog.by_id('java-jdk-17').supersedence == {'mode': 'none'}

    def test_supersedence_modes_controlled_vocabulary(self):
        """Pin the mode set so a typo or rename in any consumer breaks a
        test, not the deploy pipeline silently. Operators and machine
        generators both depend on this vocabulary."""
        assert ic.SUPERSEDENCE_MODES == {'generic', 'specific', 'manual', 'none'}

    def test_verified_version_statuses_controlled_vocabulary(self):
        """Same -- the publish-time state machine writes these values
        and the polling hook reads them. Stable vocabulary required."""
        assert ic.VERIFIED_VERSION_STATUSES == {
            'newest', 'superseded', 'historical', 'manual', 'pending',
        }

    def test_record_verification_marks_new_records_newest(self, temp_catalog_paths):
        """Minimum status state-machine behaviour: a freshly verified
        deployment lands with status='newest'. Demotion of prior
        'newest' rows to 'historical' / 'superseded' is the publish-time
        responsibility (separate PR).
        """
        baseline, local = temp_catalog_paths
        _write_yaml(local, {
            'version': 1,
            'entries': [{
                'id': 'app1', 'type': 'msi',
                'install_command_template': 'msiexec /i {installer_filename} /qn',
                'product_code': '{ABC}',
                'verified_versions': [],
            }],
        })
        ic.record_verification('app1', '1.2.3', 'app-id-1')
        entry = ic.load_catalog().by_id('app1')
        assert len(entry.verified_versions) == 1
        assert entry.verified_versions[0]['status'] == 'newest'

    def test_add_msi_entry_sets_default_supersedence_block(self, temp_catalog_paths):
        """New auto-added entries get supersedence={line: id, mode: generic}.
        Capability-only -- operator still has to opt in at publish time."""
        baseline, _ = temp_catalog_paths
        _write_yaml(baseline, {'version': 1, 'entries': []})
        entry = ic.add_msi_entry(
            {'product_name': 'NewApp', 'product_code': '{X}', 'manufacturer': 'V'},
            install_command_template='msiexec /i {installer_filename} /qn',
        )
        assert entry.supersedence == {'line': entry.id, 'mode': 'generic'}

    def test_baseline_has_no_top_level_version_field(self):
        """Contract: the committed baseline is shared across all operators
        and tenants; the ``version`` field is per-operator state (different
        operators may be on different versions at the same time). Allowing
        ``version`` in the baseline would leak one operator's state into
        every other operator's view on the next pull.
        """
        from autopackager.utils.installer_catalog import BASELINE_PATH, _load_yaml_file
        if not BASELINE_PATH.exists():
            pytest.skip(f"Baseline missing at {BASELINE_PATH}")
        raw = _load_yaml_file(BASELINE_PATH)
        leaked = []
        for entry_raw in raw.get('entries') or []:
            if 'version' in entry_raw:
                leaked.append(entry_raw.get('id'))
        assert not leaked, (
            f"Baseline entries with top-level 'version' field: {leaked}. "
            "The 'version' field is overlay-only (per-tenant state). "
            "Remove it from the baseline; create-software-job will set it "
            "in the operator's overlay at publish time."
        )

    def test_baseline_entries_declare_explicit_supersedence_mode(self):
        """Contract: every baseline entry must declare a supersedence.mode
        explicitly. Silent default mode (anything in the YAML missing the
        block) is fine at runtime but ambiguous for audit -- a reader of
        the baseline file should never wonder "is this entry opted in to
        supersedence or not?".
        """
        from autopackager.utils.installer_catalog import BASELINE_PATH, _load_yaml_file
        if not BASELINE_PATH.exists():
            pytest.skip(f"Baseline missing at {BASELINE_PATH}")
        raw = _load_yaml_file(BASELINE_PATH)
        unmarked = []
        for entry_raw in raw.get('entries') or []:
            sup = entry_raw.get('supersedence') or {}
            if 'mode' not in sup:
                unmarked.append(entry_raw.get('id'))
        assert not unmarked, (
            f"Baseline entries missing 'supersedence.mode': {unmarked}. "
            "Declare explicitly as one of: generic, specific, manual, none."
        )

    def test_record_verification_demotes_prior_newest_to_historical_when_publishing_newer(self, temp_catalog_paths):
        """Without --supersede (i.e., status state-machine only -- not the
        Intune supersedence path), a newer publish should:
          * mark the new row 'newest'
          * demote the prior 'newest' row to 'historical' (no Intune action)
        """
        baseline, local = temp_catalog_paths
        _write_yaml(local, {
            'version': 1,
            'entries': [{
                'id': 'app1', 'type': 'msi', 'product_code': '{X}',
                'install_command_template': 'msiexec /i {installer_filename} /qn',
                'supersedence': {'line': 'app1', 'mode': 'generic'},
                'verified_versions': [{
                    'product_version': '1.0.0',
                    'verified_at': '2026-05-01',
                    'verified_intune_app_id': 'old-app',
                    'status': 'newest',
                }],
            }],
        })
        ic.record_verification('app1', '1.1.0', 'new-app')
        entry = ic.load_catalog().by_id('app1')
        statuses = {vv['product_version']: vv['status'] for vv in entry.verified_versions}
        assert statuses == {'1.0.0': 'historical', '1.1.0': 'newest'}

    def test_record_verification_marks_rollback_as_manual(self, temp_catalog_paths):
        """Publishing an OLDER version than the current 'newest' is a rollback:
        the new row gets 'manual' status (sits outside the natural chain)
        and the prior 'newest' row is NOT demoted.
        """
        baseline, local = temp_catalog_paths
        _write_yaml(local, {
            'version': 1,
            'entries': [{
                'id': 'app1', 'type': 'msi', 'product_code': '{X}',
                'install_command_template': 'msiexec /i {installer_filename} /qn',
                'supersedence': {'line': 'app1', 'mode': 'generic'},
                'verified_versions': [{
                    'product_version': '2.0.0',
                    'verified_at': '2026-05-01',
                    'verified_intune_app_id': 'top-app',
                    'status': 'newest',
                }],
            }],
        })
        ic.record_verification('app1', '1.5.0', 'older-app')
        entry = ic.load_catalog().by_id('app1')
        statuses = {vv['product_version']: vv['status'] for vv in entry.verified_versions}
        assert statuses == {'2.0.0': 'newest', '1.5.0': 'manual'}

    def test_resolve_supersedence_no_opt_in_is_no_op(self, temp_catalog_paths):
        """Without ``operator_opted_in=True`` the resolver returns a
        disabled resolution regardless of what the catalog declares.
        Supersedence is opt-in at publish time, never automatic.
        """
        baseline, _ = temp_catalog_paths
        _write_yaml(baseline, {'version': 1, 'entries': [{
            'id': 'vlc', 'type': 'msi',
            'install_command_template': 'msiexec /i {installer_filename} /qn',
            'supersedence': {'line': 'vlc', 'mode': 'generic'},
            'verified_versions': [{
                'product_version': '3.0.22', 'verified_intune_app_id': 'old', 'status': 'newest',
            }],
        }]})
        catalog = ic.load_catalog()
        res = ic.resolve_supersedence(catalog, catalog.by_id('vlc'), '3.0.23',
                                       operator_opted_in=False)
        assert res.enabled is False
        assert res.demoted_records == []

    def test_resolve_supersedence_generic_marks_older_versions(self, temp_catalog_paths):
        baseline, _ = temp_catalog_paths
        _write_yaml(baseline, {'version': 1, 'entries': [{
            'id': 'vlc', 'type': 'msi',
            'install_command_template': 'msiexec /i {installer_filename} /qn',
            'supersedence': {'line': 'vlc', 'mode': 'generic'},
            'verified_versions': [
                {'product_version': '3.0.21', 'verified_intune_app_id': 'app-21', 'status': 'historical'},
                {'product_version': '3.0.22', 'verified_intune_app_id': 'app-22', 'status': 'newest'},
            ],
        }]})
        catalog = ic.load_catalog()
        res = ic.resolve_supersedence(catalog, catalog.by_id('vlc'), '3.0.23',
                                       operator_opted_in=True)
        assert res.enabled is True
        assert sorted(res.superseded_intune_app_ids) == ['app-21', 'app-22']
        assert res.mode_used == 'generic'

    def test_resolve_supersedence_generic_skips_equal_or_newer(self, temp_catalog_paths):
        """Generic mode only demotes STRICTLY older rows. A re-publish of
        the same version should not mark the existing row superseded
        (that would be self-destructive)."""
        baseline, _ = temp_catalog_paths
        _write_yaml(baseline, {'version': 1, 'entries': [{
            'id': 'vlc', 'type': 'msi',
            'install_command_template': 'msiexec /i {installer_filename} /qn',
            'supersedence': {'line': 'vlc', 'mode': 'generic'},
            'verified_versions': [
                {'product_version': '3.0.22', 'verified_intune_app_id': 'app-22', 'status': 'newest'},
                {'product_version': '4.0.0',  'verified_intune_app_id': 'app-40', 'status': 'manual'},
            ],
        }]})
        catalog = ic.load_catalog()
        res = ic.resolve_supersedence(catalog, catalog.by_id('vlc'), '3.0.22',
                                       operator_opted_in=True)
        assert res.enabled is False
        assert res.superseded_intune_app_ids == []

    def test_resolve_supersedence_mode_none_on_publishing_entry_raises(self, temp_catalog_paths):
        """The DENY shield in the from-direction. Catalog explicitly says
        'this entry never supersedes anything' -- operator passing
        --supersede gets a refusal, not a silent no-op.
        """
        baseline, _ = temp_catalog_paths
        _write_yaml(baseline, {'version': 1, 'entries': [{
            'id': 'python-3.11', 'type': 'msi',
            'install_command_template': 'msiexec /i {installer_filename} /qn',
            'supersedence': {'mode': 'none'},
            'verified_versions': [],
        }]})
        catalog = ic.load_catalog()
        with pytest.raises(ic.SupersedenceError, match='mode=none'):
            ic.resolve_supersedence(catalog, catalog.by_id('python-3.11'), '3.11.10',
                                     operator_opted_in=True)

    def test_resolve_supersedence_mode_none_on_target_shields_it(self, temp_catalog_paths):
        """The DENY shield in the to-direction. Even when a publishing
        entry's mode is generic and the line matches, a target with
        mode: none cannot be marked superseded.
        """
        baseline, _ = temp_catalog_paths
        _write_yaml(baseline, {'version': 1, 'entries': [
            {
                'id': 'foo-current', 'type': 'msi',
                'install_command_template': 'msiexec /i {installer_filename} /qn',
                'supersedence': {'line': 'foo', 'mode': 'generic'},
                'verified_versions': [],
            },
            {
                'id': 'foo-legacy', 'type': 'msi',
                'install_command_template': 'msiexec /i {installer_filename} /qn',
                'supersedence': {'line': 'foo', 'mode': 'none'},
                'verified_versions': [{
                    'product_version': '0.5.0',
                    'verified_intune_app_id': 'legacy-app',
                    'status': 'newest',
                }],
            },
        ]})
        catalog = ic.load_catalog()
        res = ic.resolve_supersedence(catalog, catalog.by_id('foo-current'), '2.0.0',
                                       operator_opted_in=True)
        # foo-legacy was in the line but shielded by mode: none
        assert 'legacy-app' not in res.superseded_intune_app_ids
        assert any('shielded_by_mode_none' in n for n in res.notes)

    def test_resolve_supersedence_specific_uses_version_pattern(self, temp_catalog_paths):
        """Specific mode -- the regex filters which verified_versions are
        in this line, even when they all share the same `line` string.
        """
        baseline, _ = temp_catalog_paths
        _write_yaml(baseline, {'version': 1, 'entries': [{
            'id': 'java-line-17', 'type': 'msi',
            'install_command_template': 'msiexec /i {installer_filename} /qn',
            'supersedence': {
                'line': 'java',
                'mode': 'specific',
                'version_pattern': r'^17\.\d+\.\d+$',
            },
            'verified_versions': [
                {'product_version': '17.0.12', 'verified_intune_app_id': 'j17-12', 'status': 'historical'},
                {'product_version': '17.0.13', 'verified_intune_app_id': 'j17-13', 'status': 'newest'},
                # An out-of-line row that happens to share entry+line
                {'product_version': '21.0.4',  'verified_intune_app_id': 'j21-4',  'status': 'newest'},
            ],
        }]})
        catalog = ic.load_catalog()
        res = ic.resolve_supersedence(catalog, catalog.by_id('java-line-17'), '17.0.14',
                                       operator_opted_in=True)
        # Only the two 17.x rows are in the line; 21.x is filtered out by
        # the version_pattern regex.
        assert sorted(res.superseded_intune_app_ids) == ['j17-12', 'j17-13']

    def test_resolve_supersedence_manual_uses_catalog_supersedes_list(self, temp_catalog_paths):
        baseline, _ = temp_catalog_paths
        _write_yaml(baseline, {'version': 1, 'entries': [
            {
                'id': 'enterprise-app-2008', 'type': 'msi',
                'install_command_template': 'msiexec /i {installer_filename} /qn',
                'supersedence': {
                    'mode': 'manual',
                    'supersedes': ['enterprise-app-2007'],
                },
                'verified_versions': [],
            },
            {
                'id': 'enterprise-app-2007', 'type': 'msi',
                'install_command_template': 'msiexec /i {installer_filename} /qn',
                'supersedence': {'line': 'eapp', 'mode': 'generic'},
                'verified_versions': [{
                    'product_version': '2007.4.7',
                    'verified_intune_app_id': 'eapp-2007',
                    'status': 'newest',
                }],
            },
        ]})
        catalog = ic.load_catalog()
        res = ic.resolve_supersedence(catalog, catalog.by_id('enterprise-app-2008'),
                                       '2008.4.7', operator_opted_in=True)
        assert res.mode_used == 'manual'
        assert 'eapp-2007' in res.superseded_intune_app_ids

    def test_resolve_supersedence_explicit_supersedes_overrides_catalog(self, temp_catalog_paths):
        """``explicit_supersedes`` (from --supersedes <ids>) takes precedence
        over the catalog's mode/line/pattern. Honours the operator's intent.
        """
        baseline, _ = temp_catalog_paths
        _write_yaml(baseline, {'version': 1, 'entries': [
            {
                'id': 'a', 'type': 'msi',
                'install_command_template': 'msiexec /i {installer_filename} /qn',
                'supersedence': {'line': 'a', 'mode': 'generic'},
                'verified_versions': [],
            },
            {
                'id': 'b', 'type': 'msi',
                'install_command_template': 'msiexec /i {installer_filename} /qn',
                'supersedence': {'line': 'b', 'mode': 'generic'},
                'verified_versions': [{
                    'product_version': '1.0',
                    'verified_intune_app_id': 'b-1',
                    'status': 'newest',
                }],
            },
        ]})
        catalog = ic.load_catalog()
        # Publishing 'a', but explicit_supersedes targets 'b' (cross-line).
        res = ic.resolve_supersedence(catalog, catalog.by_id('a'), '2.0',
                                       operator_opted_in=True,
                                       explicit_supersedes=['b'])
        assert res.mode_used == 'manual_cli'
        assert 'b-1' in res.superseded_intune_app_ids

    def test_round_trip_through_local_overlay_write(self, temp_catalog_paths):
        # Adding an EXE entry to the overlay must preserve the new fields
        # across a write+read cycle. _write_local strips empty values, so
        # we verify detection_rules with content survives.
        _baseline, local = temp_catalog_paths
        _write_yaml(local, {
            'version': 1,
            'entries': [{
                'id': 'demo',
                'type': 'exe',
                'installer_family': 'inno_setup',
                'install_command_template': '{installer_filename} /VERYSILENT',
                'detection_rules': [{'kind': 'file_exists', 'path': r'C:\App', 'folder': 'App'}],
            }],
        })
        # record_use writes the overlay back out -- if our dataclass
        # serialization loses installer_family or detection_rules, the
        # next load shows it missing.
        ic.record_use('demo')
        reloaded = ic.load_catalog().by_id('demo')
        assert reloaded.installer_family == 'inno_setup'
        assert reloaded.detection_rules == [
            {'kind': 'file_exists', 'path': r'C:\App', 'folder': 'App'}
        ]
