"""Unit tests for LocalInstallValidator's detached-installer reaper.

The full validate() flow is intentionally skipped under pytest (it performs
real silent installs), but the process-reaping helpers — which guard against
consumer online stubs like ChromeSetup.exe that detach an elevated updater
from the install tree — are pure and unit-testable.
"""

import os
from pathlib import Path
from unittest.mock import Mock

import pytest

from autopackager.agents.testing import local_install_validator as liv
from autopackager.agents.testing.local_install_validator import LocalInstallValidator


@pytest.fixture
def force_windows(monkeypatch):
    """The reaper is a no-op off Windows; force os.name='nt' so the logic runs
    regardless of the host running the test suite (CI is Linux)."""
    monkeypatch.setattr(liv.os, "name", "nt")


def _validator(monkeypatch, after_snapshot, killed):
    v = LocalInstallValidator()
    monkeypatch.setattr(LocalInstallValidator, "_running_by_name",
                        staticmethod(lambda: after_snapshot))
    monkeypatch.setattr(LocalInstallValidator, "_kill_tree",
                        staticmethod(lambda pid: killed.append(pid)))
    return v


def test_reaps_only_new_denylisted_processes(force_windows, monkeypatch):
    # Before: an updater.exe (pid 100) already running — must be left alone.
    before = {"updater.exe": [100]}
    # After install: pid 100 still there + NEW updater.exe(200), a NEW
    # googleupdate.exe(300), and an unrelated chrome.exe(400) not on the
    # denylist (the browser itself — reaping it is out of scope here).
    after = {
        "updater.exe": [100, 200],
        "googleupdate.exe": [300],
        "chrome.exe": [400],
    }
    killed = []
    v = _validator(monkeypatch, after, killed)

    reaped = v._reap_detached_installers(before)

    assert set(killed) == {200, 300}          # new denylisted only
    assert 100 not in killed                  # pre-existing left alone
    assert 400 not in killed                  # non-denylisted left alone
    assert any("updater.exe(200)" in r for r in reaped)
    assert any("googleupdate.exe(300)" in r for r in reaped)


def test_reap_noop_when_nothing_new(force_windows, monkeypatch):
    before = {"updater.exe": [100]}
    after = {"updater.exe": [100]}  # same pid, nothing spawned
    killed = []
    v = _validator(monkeypatch, after, killed)

    reaped = v._reap_detached_installers(before)

    assert killed == []
    assert reaped == []


def test_reap_is_noop_off_windows(monkeypatch):
    monkeypatch.setattr(liv.os, "name", "posix")
    killed = []
    v = _validator(monkeypatch, {"updater.exe": [999]}, killed)

    reaped = v._reap_detached_installers({})

    assert killed == []
    assert reaped == []


def test_running_by_name_parses_tasklist_csv(force_windows, monkeypatch):
    sample = (
        '"chrome.exe","400","Console","1","120,000 K"\n'
        '"updater.exe","200","Services","0","8,000 K"\n'
        '"updater.exe","201","Services","0","8,000 K"\n'
    )

    class _Res:
        stdout = sample

    monkeypatch.setattr(liv.subprocess, "run", lambda *a, **k: _Res())
    procs = LocalInstallValidator._running_by_name()

    assert procs["chrome.exe"] == [400]
    assert sorted(procs["updater.exe"]) == [200, 201]


# --- Install retry ladder + engineer escalation ----------------------------

def test_install_candidates_exe_offers_capped_alternates():
    v = LocalInstallValidator()
    pkg = Mock()
    pkg.install_command = "Setup.exe /VERYSILENT /NORESTART /SUPPRESSMSGBOXES"
    cands = v._install_candidates(pkg, Path("Setup.exe"))
    assert cands[0] == "Setup.exe /VERYSILENT /NORESTART /SUPPRESSMSGBOXES"
    assert len(cands) == LocalInstallValidator._MAX_INSTALL_ATTEMPTS  # capped at 3
    assert len(set(cands)) == len(cands)                              # de-duped
    assert all(c.startswith("Setup.exe ") for c in cands)            # same exe token


def test_install_candidates_msi_single_attempt():
    v = LocalInstallValidator()
    pkg = Mock()
    pkg.install_command = "msiexec /i Foo.msi /qn /norestart"
    assert v._install_candidates(pkg, Path("Foo.msi")) == ["msiexec /i Foo.msi /qn /norestart"]


def test_install_candidates_empty_command():
    v = LocalInstallValidator()
    pkg = Mock()
    pkg.install_command = ""
    assert v._install_candidates(pkg, Path("x.exe")) == []


def _mock_validate_env(monkeypatch, tmp_path, run_fn, eval_fn, install_command):
    """Patch out every winreg/subprocess/file touchpoint so validate()'s ladder
    can be exercised portably (incl. on Linux CI)."""
    installer = tmp_path / "App.exe"
    installer.write_bytes(b"MZ")
    monkeypatch.setattr(liv, "IS_WINDOWS", True)
    monkeypatch.setattr(LocalInstallValidator, "_resolve_installer", lambda self, p, j: installer)
    monkeypatch.setattr(LocalInstallValidator, "_catalog_style_rules", lambda self, p: [])
    monkeypatch.setattr(LocalInstallValidator, "_snapshot_uninstall_keys", lambda self: {})
    monkeypatch.setattr(LocalInstallValidator, "_running_by_name", staticmethod(lambda: {}))
    monkeypatch.setattr(LocalInstallValidator, "_reap_detached_installers", lambda self, b: [])
    monkeypatch.setattr(LocalInstallValidator, "_discover_new_entry", lambda self, b, a, p: None)
    monkeypatch.setattr(LocalInstallValidator, "_attempt_uninstall",
                        lambda self, p, d, res: res.__setitem__("uninstalled", True))
    monkeypatch.setattr(LocalInstallValidator, "_run", run_fn)
    monkeypatch.setattr(LocalInstallValidator, "_eval_rules", eval_fn)
    pkg = Mock()
    pkg.id = 1
    pkg.install_command = install_command
    pkg.detection_rules = []
    pkg.installer_path = str(installer)
    return pkg


def test_validate_escalates_when_no_silent_install(monkeypatch, tmp_path):
    # Every attempt times out (UI popped → rc 1460) → engineer escalation.
    pkg = _mock_validate_env(
        monkeypatch, tmp_path,
        run_fn=lambda self, cmd, cwd, timeout: (1460, "timeout"),
        eval_fn=lambda self, rules: (False, ""),
        install_command="App.exe /VERYSILENT",
    )
    res = LocalInstallValidator().validate(pkg, job=None)
    assert res["installed"] is False
    assert res["passed"] is False
    assert res["needs_engineer_review"] is True
    assert res["install_attempts"] == LocalInstallValidator._MAX_INSTALL_ATTEMPTS


def test_validate_recovers_with_alternate_switch(monkeypatch, tmp_path):
    # First switch hangs (1460); the second installs cleanly and detection fires.
    calls = {"n": 0}

    def run_fn(self, cmd, cwd, timeout):
        calls["n"] += 1
        return (1460, "timeout") if calls["n"] == 1 else (0, "ok")

    pkg = _mock_validate_env(
        monkeypatch, tmp_path,
        run_fn=run_fn,
        eval_fn=lambda self, rules: (True, "detected"),
        install_command="App.exe /wrongswitch",
    )
    res = LocalInstallValidator().validate(pkg, job=None)
    assert res["installed"] is True
    assert res["needs_engineer_review"] is False
    assert res["passed"] is True
    # The working alternate (second candidate = "App.exe /S") is recorded.
    assert res["corrected_install_command"] == "App.exe /S"
    assert res["install_attempts"] == 2


def test_verify_with_settle_polls_until_detached_install_lands(monkeypatch):
    """A detached installer (Squirrel/Burn) isn't detected on the first check but
    lands during the settle window; _verify_with_settle must poll, not give up."""
    v = LocalInstallValidator()
    v.settle_poll_seconds = 0  # don't actually sleep in the test
    monkeypatch.setattr(liv.time, "sleep", lambda s: None)
    monkeypatch.setattr(LocalInstallValidator, "_snapshot_uninstall_keys", lambda self: {})
    monkeypatch.setattr(LocalInstallValidator, "_discover_new_entry", lambda self, b, a, p: None)
    seq = iter([(False, ""), (False, ""), (True, "detected")])
    monkeypatch.setattr(LocalInstallValidator, "_eval_rules", lambda self, rules: next(seq))
    fired, detail, discovered = v._verify_with_settle([{"kind": "registry_version"}], {}, Mock(), settle_seconds=30)
    assert fired is True
    assert detail == "detected"


def test_verify_with_settle_returns_immediately_on_first_match(monkeypatch):
    """A synchronous install fires on the first check — no settle delay incurred."""
    v = LocalInstallValidator()
    monkeypatch.setattr(LocalInstallValidator, "_snapshot_uninstall_keys", lambda self: {})
    monkeypatch.setattr(LocalInstallValidator, "_discover_new_entry", lambda self, b, a, p: None)
    calls = {"n": 0}

    def eval_fn(self, rules):
        calls["n"] += 1
        return (True, "ok")

    monkeypatch.setattr(LocalInstallValidator, "_eval_rules", eval_fn)
    fired, _, _ = v._verify_with_settle([{"kind": "x"}], {}, Mock(), settle_seconds=120)
    assert fired is True
    assert calls["n"] == 1  # only one evaluation; never entered the poll loop
