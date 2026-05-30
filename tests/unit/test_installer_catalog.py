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
