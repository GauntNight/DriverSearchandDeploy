"""Unit tests for wrapper (multi-component) packaging.

A wrapper catalog entry bundles a primary installer + one or more
``extra_components`` into a single Win32 app driven by a generated install.cmd,
with detection = the AND of every piece's rules. The canonical case is
Wireshark + the Npcap OEM capture driver.
"""

from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from autopackager.agents.packaging.packaging_agent import PackagingAgent
from autopackager.models.job import Job
from autopackager.utils import installer_catalog
from autopackager.utils.installer_catalog import CatalogEntry


def _wrapper_entry():
    return CatalogEntry(
        id="wstest",
        type="exe",
        installer_family="nsis",
        install_command_template="{installer_filename} /S",
        uninstall_command_template='"C:\\Program Files\\Wireshark\\uninstall-wireshark.exe" /S',
        detection_rules=[{
            "kind": "registry_version",
            "key": "HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\Wireshark",
            "value_name": "DisplayVersion", "operator": "greaterThanOrEqual",
            "value": "0.0.0", "check_32bit_on_64bit": False,
        }],
        extra_components=[{
            "id": "npcap-oem", "filename_hint": "npcap-oem",
            "acquisition": "operator_supplied", "required": True,
            "install_command_template": "{installer_filename} /S /winpcap_mode=yes",
            "uninstall_command_template": '"C:\\Program Files\\Npcap\\Uninstall.exe" /S',
            "detection_rules": [{
                "kind": "registry_version",
                "key": "HKLM\\SOFTWARE\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\NpcapInst",
                "value_name": "DisplayVersion", "operator": "greaterThanOrEqual",
                "value": "0.0.0", "check_32bit_on_64bit": True,
            }],
        }],
    )


def _agent(tmp_path):
    with patch("autopackager.agents.packaging.packaging_agent.get_config"):
        agent = PackagingAgent()
    agent.downloads_path = tmp_path / "data" / "downloads"
    agent.packages_path = tmp_path / "data" / "packages"
    agent.downloads_path.mkdir(parents=True, exist_ok=True)
    agent.packages_path.mkdir(parents=True, exist_ok=True)
    agent.intunewin_util = tmp_path / "IntuneWinAppUtil.exe"
    return agent


# ---- catalog schema ----

def test_is_wrapper_property():
    assert _wrapper_entry().is_wrapper is True
    assert CatalogEntry(id="plain", type="msi").is_wrapper is False


def test_real_wireshark_entry_is_a_wrapper_with_npcap():
    """The shipped baseline wireshark entry models the Npcap dependency."""
    entry = installer_catalog.load_catalog().by_id("wireshark")
    assert entry.is_wrapper is True
    comp = entry.extra_components[0]
    assert comp["id"] == "npcap-oem"
    assert comp["acquisition"] == "operator_supplied"
    assert "NpcapInst" in comp["detection_rules"][0]["key"]


# ---- script generation ----

def test_render_install_script_runs_each_step_in_order():
    script = PackagingAgent._render_wrapper_script(
        [("Wireshark.exe", "Wireshark.exe /S"),
         ("npcap-oem.exe", "npcap-oem.exe /S /winpcap_mode=yes")],
        kind="install",
    )
    assert 'cd /d "%~dp0"' in script
    # both steps present, primary before component
    assert script.index("Wireshark.exe /S") < script.index("npcap-oem.exe /S")
    # 3010 (reboot) treated as success alongside 0
    assert '"%RC%"=="3010"' in script
    assert script.rstrip().endswith("exit /b 0")
    # CRLF line endings for a .cmd
    assert "\r\n" in script


def test_render_script_skips_empty_commands():
    script = PackagingAgent._render_wrapper_script(
        [("a.exe", "a.exe /S"), ("b.exe", "")], kind="uninstall"
    )
    assert "a.exe /S" in script
    assert "b.exe" not in script


# ---- detection merge ----

def test_detection_rules_merge_primary_and_components(tmp_path):
    agent = _agent(tmp_path)
    job = Mock(spec=Job)
    job.id = 1
    job.vendor = "Wireshark"
    job.job_metadata = {"catalog_entry_id": "wstest"}
    with patch.object(agent, "_catalog_entry_for_job", return_value=_wrapper_entry()):
        rules = agent._generate_detection_rules(job)
    keys = [r.get("keyPath") for r in rules]
    assert any("Uninstall\\Wireshark" in k for k in keys)
    assert any("NpcapInst" in k for k in keys)
    assert len(rules) == 2  # Intune ANDs them: installed only when BOTH present


# ---- staging + escalation ----

def test_missing_required_component_escalates(tmp_path):
    agent = _agent(tmp_path)
    package_dir = agent.packages_path / "pkg"
    package_dir.mkdir()
    primary = package_dir / "Wireshark.exe"
    primary.write_bytes(b"MZ")
    job = Mock(spec=Job)
    job.id = 1
    # no npcap-oem file anywhere -> required component cannot be resolved
    with pytest.raises(ValueError) as exc:
        agent._stage_wrapper_components(
            job, package_dir, primary, "Wireshark.exe /S",
            "uninstall-wireshark.exe /S", _wrapper_entry(),
        )
    assert "npcap-oem" in str(exc.value)


def test_optional_component_missing_is_skipped(tmp_path):
    agent = _agent(tmp_path)
    package_dir = agent.packages_path / "pkg"
    package_dir.mkdir()
    primary = package_dir / "Wireshark.exe"
    primary.write_bytes(b"MZ")
    entry = _wrapper_entry()
    entry.extra_components[0]["required"] = False
    job = Mock(spec=Job)
    job.id = 1
    setup, install_cmd, uninstall_cmd = agent._stage_wrapper_components(
        job, package_dir, primary, "Wireshark.exe /S",
        "uninstall-wireshark.exe /S", entry,
    )
    assert setup.name == "install.cmd"
    # only the primary step made it into the script
    assert "npcap-oem" not in (package_dir / "install.cmd").read_text()


def test_component_present_is_staged_and_scripts_written(tmp_path):
    agent = _agent(tmp_path)
    # operator drops the OEM installer in data/wrapper_components/
    comp_dir = agent.downloads_path.parent / "wrapper_components"
    comp_dir.mkdir(parents=True, exist_ok=True)
    (comp_dir / "npcap-oem-1.83.exe").write_bytes(b"MZ-oem")

    package_dir = agent.packages_path / "pkg"
    package_dir.mkdir()
    primary = package_dir / "Wireshark.exe"
    primary.write_bytes(b"MZ")

    job = Mock(spec=Job)
    job.id = 1
    setup, install_cmd, uninstall_cmd = agent._stage_wrapper_components(
        job, package_dir, primary, "Wireshark.exe /S",
        "uninstall-wireshark.exe /S", _wrapper_entry(),
    )

    # component file copied into the package source folder
    assert (package_dir / "npcap-oem-1.83.exe").exists()
    # scripts written + Win32 commands point at them
    assert (package_dir / "install.cmd").exists()
    assert (package_dir / "uninstall.cmd").exists()
    assert install_cmd == "cmd /c install.cmd"
    assert uninstall_cmd == "cmd /c uninstall.cmd"

    install_txt = (package_dir / "install.cmd").read_text()
    assert "Wireshark.exe /S" in install_txt
    assert "npcap-oem-1.83.exe /S /winpcap_mode=yes" in install_txt
    # uninstall runs components FIRST (reverse order), then the primary
    uninstall_txt = (package_dir / "uninstall.cmd").read_text()
    assert uninstall_txt.index("Npcap") < uninstall_txt.index("uninstall-wireshark.exe")
