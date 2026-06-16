"""Stop and restart the full AutoPackager local stack (Redis + Celery worker +
FastAPI dashboard) in one command.

Why this exists
---------------
The demo stack is three long-running processes that have to come up in order
(Redis first, then the worker, then uvicorn). Restarting them by hand — find the
PIDs, kill them, delete the stale Redis dump, relaunch each detached, health-check
the ports — is fiddly and easy to get wrong (a leftover ``dump.rdb`` re-seeds the
broker with stale jobs; a half-killed worker double-processes the queue). This
script does the whole cycle deterministically and idempotently.

What it does
------------
1. **Stop** every running stack process: ``redis-server.exe`` / ``memurai.exe``
   by image name, plus any ``python`` process whose command line is the Celery
   worker (``cli.py worker``) or the uvicorn dashboard
   (``autopackager.web.api``). Safe to run when nothing is up.
2. **Delete ``dump.rdb``** (repo root + ``data/``) before Redis restarts — the
   standing rule: a stale dump re-seeds the broker with old jobs.
3. **Start** Redis, then the worker, then uvicorn — each detached, with stdout/
   stderr redirected to ``data/logs/<name>.out.log`` so this script can exit
   without taking them down.
4. **Health-check** ports 6379 and 8000 and report what came up.

Usage
-----
    python scripts/restart_stack.py            # full stop + start (default)
    python scripts/restart_stack.py --stop     # stop only
    python scripts/restart_stack.py --start    # start only (no stop first)
    python scripts/restart_stack.py --no-worker # skip the Celery worker

Demo modes (``DEMO_CLAUDE_MODE``, ``CVE_INTEL_MODE``) are inherited from the
environment / ``.env`` — this script does not override them. Windows-only
(taskkill / detached process flags); that's the only platform the stack runs on.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = REPO_ROOT / "data" / "logs"

# Optional HTTPS for the demo vanity URL (https://demo.autopackager.com). If both
# the cert and key exist (generated out-of-band into data/demo_certs/), a second
# uvicorn is brought up on 443 with TLS, serving the SAME app — native SSE, no
# proxy in the middle. Binding 443 needs elevation. Absent → HTTPS is skipped.
HTTPS_PORT = 443
_CERT = REPO_ROOT / "data" / "demo_certs" / "demo.cert.pem"
_KEY = REPO_ROOT / "data" / "demo_certs" / "demo.key.pem"

# Canonical venv is ./venv (CLAUDE.md); fall back to the installer's ./.venv.
_VENV = REPO_ROOT / "venv" / "Scripts" / "python.exe"
_VENV_ALT = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
PYTHON = str(_VENV if _VENV.exists() else _VENV_ALT if _VENV_ALT.exists() else sys.executable)

# Detached-process flags so children outlive this script (Windows only).
_DETACHED = 0
if os.name == "nt":
    _DETACHED = (
        getattr(subprocess, "DETACHED_PROCESS", 0)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    )


def _run(cmd: list[str]) -> str:
    """Run a short command, return stdout (never raises)."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=30).stdout or ""
    except Exception:  # noqa: BLE001
        return ""


def _port_listening(port: int) -> bool:
    return _pid_on_port(port) is not None


def _pid_on_port(port: int) -> str | None:
    """PID listening on a TCP port (None if nothing is)."""
    out = _run(["netstat", "-ano", "-p", "TCP"])
    for line in out.splitlines():
        if f":{port} " in line and "LISTENING" in line.upper():
            pid = line.split()[-1]
            if pid.isdigit():
                return pid
    return None


# --- stop -------------------------------------------------------------------

def _python_stack_pids() -> list[str]:
    """PIDs of python processes that are the worker or the dashboard.

    Uses a PowerShell CIM query rather than ``wmic`` — Windows 11 deprecated and
    often removes ``wmic.exe`` (it is gone on this build), so the old wmic scan
    silently found nothing and left the dashboard running. CIM is the supported
    replacement and ships on every modern Windows.
    """
    ps = (
        "Get-CimInstance Win32_Process -Filter "
        "\"Name='python.exe' or Name='pythonw.exe'\" | "
        "ForEach-Object { \"$($_.ProcessId)`t$($_.CommandLine)\" }"
    )
    out = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps])
    pids: list[str] = []
    for line in out.splitlines():
        if "\t" not in line:
            continue
        pid, _, cmdline = line.partition("\t")
        pid = pid.strip()
        low = cmdline.lower()
        is_stack = (
            "autopackager.web.api" in low
            or "uvicorn" in low
            or ("cli.py" in low and "worker" in low)
            or "celery" in low
        )
        if is_stack and pid.isdigit():
            pids.append(pid)
    # Fallback: whatever holds :8000 / :443 is our dashboard even if cmdline
    # detection missed it (e.g. a differently-launched uvicorn).
    for port in (8000, HTTPS_PORT):
        p = _pid_on_port(port)
        if p and p not in pids:
            pids.append(p)
    return pids


def stop() -> None:
    print("== Stopping stack ==")
    # Redis / Memurai by image name (kills any stray instance too).
    for img in ("redis-server.exe", "memurai.exe"):
        r = _run(["taskkill", "/F", "/IM", img])
        if "SUCCESS" in r.upper():
            print(f"  stopped {img}")
    # Worker + dashboard by PID (with /T to take child processes too).
    pids = _python_stack_pids()
    for pid in pids:
        r = _run(["taskkill", "/F", "/T", "/PID", pid])
        if "SUCCESS" in r.upper():
            print(f"  stopped python PID {pid} (worker/dashboard)")
    if not pids:
        print("  no worker/dashboard python processes found")
    # Give the OS a moment to free the ports before we relaunch.
    time.sleep(1.5)


def _delete_dump_rdb() -> None:
    """Remove stale Redis dumps so the broker starts empty (standing rule)."""
    for p in (REPO_ROOT / "dump.rdb", REPO_ROOT / "data" / "dump.rdb"):
        try:
            if p.exists():
                p.unlink()
                print(f"  deleted {p.relative_to(REPO_ROOT)}")
        except OSError as exc:
            print(f"  WARN could not delete {p}: {exc}")


# --- start ------------------------------------------------------------------

def _spawn(name: str, cmd: list[str]) -> None:
    """Launch a detached background process logging to data/logs/<name>.out.log."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log = open(LOG_DIR / f"{name}.out.log", "ab")
    subprocess.Popen(
        cmd, cwd=str(REPO_ROOT), stdout=log, stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL, creationflags=_DETACHED, close_fds=True,
    )


def _redis_cmd() -> list[str] | None:
    bundled = REPO_ROOT / "tools" / "redis" / "redis-server.exe"
    conf = REPO_ROOT / "redis.conf"
    conf_arg = [str(conf)] if conf.exists() else []
    if bundled.exists():
        return [str(bundled), *conf_arg]
    for exe in ("memurai", "redis-server"):
        if _run(["where", exe]).strip():
            return [exe, *conf_arg]
    return None


def start(*, worker: bool = True) -> None:
    print("== Starting stack ==")
    _delete_dump_rdb()

    # 1) Redis
    if _port_listening(6379):
        print("  Redis already listening on 6379 — leaving it")
    else:
        cmd = _redis_cmd()
        if not cmd:
            print("  ERROR: Redis not found (no tools/redis, memurai, or redis-server)")
        else:
            _spawn("redis", cmd)
            print(f"  started Redis: {cmd[0]}")
            time.sleep(2)

    # 2) Celery worker (+ Beat)
    if worker:
        _spawn("worker", [PYTHON, "cli.py", "worker", "start"])
        print("  started Celery worker (cli.py worker start)")
        time.sleep(2)
    else:
        print("  skipped Celery worker (--no-worker)")

    # 3) FastAPI dashboard (HTTP on 8000)
    if _port_listening(8000):
        print("  Port 8000 already in use — not starting a second uvicorn")
    else:
        _spawn("dashboard", [
            PYTHON, "-m", "uvicorn", "autopackager.web.api:app",
            "--host", "0.0.0.0", "--port", "8000",
        ])
        print("  started dashboard (uvicorn :8000)")

    # 4) HTTPS dashboard (TLS on 443) — only if the demo cert exists.
    if _CERT.exists() and _KEY.exists():
        if _port_listening(HTTPS_PORT):
            print(f"  Port {HTTPS_PORT} already in use — not starting a second HTTPS uvicorn")
        else:
            _spawn("dashboard-https", [
                PYTHON, "-m", "uvicorn", "autopackager.web.api:app",
                "--host", "0.0.0.0", "--port", str(HTTPS_PORT),
                "--ssl-certfile", str(_CERT), "--ssl-keyfile", str(_KEY),
            ])
            print(f"  started HTTPS dashboard (uvicorn :{HTTPS_PORT} TLS)")
    else:
        print("  no demo cert — HTTPS skipped (run the cert-gen step to enable)")


def _health() -> None:
    print("== Health ==")
    time.sleep(2)
    print(f"  Redis  (6379): {'UP' if _port_listening(6379) else 'down'}")
    print(f"  Dashbd (8000): {'UP' if _port_listening(8000) else 'starting…'}")
    if _CERT.exists() and _KEY.exists():
        print(f"  HTTPS   (443): {'UP' if _port_listening(HTTPS_PORT) else 'starting…'}")
    print(f"  Logs:          {LOG_DIR}")
    print("  Console:       http://localhost:8000/demo")
    if _CERT.exists():
        print("  Vanity URL:    https://demo.autopackager.com")


def main() -> int:
    ap = argparse.ArgumentParser(description="Restart the AutoPackager local stack.")
    ap.add_argument("--stop", action="store_true", help="stop only")
    ap.add_argument("--start", action="store_true", help="start only (no stop first)")
    ap.add_argument("--no-worker", action="store_true", help="don't start the Celery worker")
    args = ap.parse_args()

    print(f"AutoPackager stack control — python: {PYTHON}")
    if args.stop:
        stop()
        return 0
    if not args.start:
        stop()
    start(worker=not args.no_worker)
    _health()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
