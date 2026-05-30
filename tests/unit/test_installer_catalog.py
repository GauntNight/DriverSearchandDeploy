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
