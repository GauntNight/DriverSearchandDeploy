"""Tests for the log-export views (operational/INFO + package-build) and the
validator's per-package installer-log capture."""
import tempfile
import time
from pathlib import Path

import pytest

from autopackager.utils import log_export


def _write_worker_log(tmp_path):
    log = tmp_path / "worker-2026-06-16.out.log"
    log.write_text(
        "[2026-06-16 11:00:00,000: WARNING/MainProcess] 2026-06-16 11:00:00 [info     ] Starting packaging phase       job_id=9\n"
        "[2026-06-16 11:00:01,000: WARNING/MainProcess] 2026-06-16 11:00:01 [debug    ] GET request                    url=https://graph\n"
        "INFO   Compressing the source folder 'data\\packages\\Foo'\n"
        "INFO   File 'foo.intunewin' has been generated successfully\n"
        "[2026-06-16 11:00:05,000: WARNING/MainProcess] 2026-06-16 11:00:05 [info     ] Deployment completed           job_id=9\n",
        encoding="utf-8",
    )
    return log


def test_export_splits_info_and_packaging(tmp_path):
    _write_worker_log(tmp_path)
    res = log_export.export(minutes=600, out_dir=str(tmp_path / "out"), log_dir=str(tmp_path))
    info = Path(res["info"]).read_text(encoding="utf-8")
    pkg = Path(res["packaging"]).read_text(encoding="utf-8")
    # INFO view: operational lines kept, debug HTTP chatter stripped.
    assert "Starting packaging phase" in info
    assert "Deployment completed" in info
    assert "GET request" not in info
    # Package-build view: the IntuneWinAppUtil build detail is captured.
    assert "Compressing the source folder" in pkg
    assert "has been generated" in pkg
    assert res["info_lines"] >= 2 and res["packaging_lines"] >= 1


def test_export_no_logs_returns_error(tmp_path):
    res = log_export.export(minutes=45, out_dir=str(tmp_path / "out"), log_dir=str(tmp_path))
    assert "error" in res


def test_capture_installer_logs_prefixes_and_copies(tmp_path, monkeypatch):
    from autopackager.agents.testing import local_install_validator as liv

    fake_temp = tmp_path / "temp"; fake_temp.mkdir()
    log_dir = tmp_path / "logs"; log_dir.mkdir()
    # an installer-named log written "now" + an unrelated old log that must be skipped
    (fake_temp / "Snagit_2019_setup.log").write_text("MSI verbose log", encoding="utf-8")
    stale = fake_temp / "unrelated.log"; stale.write_text("noise", encoding="utf-8")
    import os
    os.utime(stale, (time.time() - 86400, time.time() - 86400))  # 1 day old

    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(fake_temp))
    v = liv.LocalInstallValidator({})
    got = v._capture_installer_logs(log_dir, "installer_9_snagit_", time.time() - 120,
                                    Path("snagit-latest.exe"))
    assert any("Snagit_2019_setup.log" in n for n in got)
    assert (log_dir / "installer_9_snagit_Snagit_2019_setup.log").exists()
    # the old, unrelated log is not captured
    assert not any("unrelated" in n for n in got)
