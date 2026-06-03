"""Unit tests for LocalInstallValidator's detached-installer reaper.

The full validate() flow is intentionally skipped under pytest (it performs
real silent installs), but the process-reaping helpers — which guard against
consumer online stubs like ChromeSetup.exe that detach an elevated updater
from the install tree — are pure and unit-testable.
"""

import os

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
