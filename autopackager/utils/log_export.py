"""Shareable log export — the standard pattern behind ``cli.py logs export``.

Produces two customer/support-facing views from the running stack's logs:

* **operational / INFO** — pipeline + web/Graph status at INFO level (debug HTTP
  chatter stripped, Celery wrappers removed for readability).
* **package-build & execution** — the worker-side build detail (IntuneWinAppUtil
  compress/encrypt/hash/upload, install-command generation, catalog resolution)
  plus any errors/tracebacks, so packagers have a support log if a build fails.

Sources are the per-day ``worker-*.out.log`` (pipeline) and ``dashboard*-*.out.log``
(web/Graph) files written by ``scripts/restart_stack.py``. Filter by a trailing
time window (``minutes``) and optionally narrow the packaging view to one job.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

LOG_DIR = Path("data/logs")

_TS = re.compile(r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)")
_CELERY = re.compile(r"^\[\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d+: \w+/\w+\]\s*")
_INFO = re.compile(r"\[(info|warning|error)")
_PKG = re.compile(
    r"Compressing|Compressed|has been generated|Calculated size|IntuneWin|"
    r"Encrypting|encrypted|SHA256 hash|Generated (install|uninstall)|Using local installer|"
    r"installer staged|Commands validated|Starting (packaging|testing|deployment|discovery)|"
    r"Local validation|local_install|Local install|Testing passed|Created (deployment|Win32|new app)|"
    r"content version|chunk|Azure Blob|publishingState|Deployment (completed|rings)|"
    r"Updated package|escalat|needs_engineer|Packaging|package_id|catalog hit|Catalog entry|"
    r"displayVersion set",
    re.I,
)
_ERR = re.compile(r"\[error|ERROR/|Traceback|needs_engineer|escalat|\bfailed\b", re.I)


def _clean(line: str) -> str:
    return _CELERY.sub("", line.rstrip("\n"))


def _ts_of(line: str) -> Optional[datetime]:
    m = _TS.search(line)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _source_files(log_dir: Path):
    files: list[Path] = []
    for pat in ("worker*.out.log", "dashboard-https*.out.log", "dashboard*.out.log"):
        files += sorted(log_dir.glob(pat))
    seen, uniq = set(), []
    for f in files:
        if f not in seen:
            seen.add(f)
            uniq.append(f)
    return uniq


def _read_tagged(log_dir: Path):
    """Return (worker_lines, web_lines) across all source files."""
    worker, web = [], []
    for f in _source_files(log_dir):
        bucket = worker if f.name.startswith("worker") else web
        try:
            for line in f.open(encoding="utf-8", errors="replace"):
                bucket.append(line.rstrip("\n"))
        except OSError:
            continue
    return worker, web


def _in_window(rows, cutoff):
    """Keep lines at/after cutoff; untimestamped lines inherit the current state
    (so a multi-line tool block stays attached to its timestamped neighbour)."""
    kept, keeping = [], False
    for l in rows:
        t = _ts_of(l)
        if t is not None:
            keeping = t >= cutoff
        if keeping:
            kept.append(l)
    return kept


def export(minutes: int = 45, *, job_id: Optional[int] = None,
           out_dir: Optional[str] = None, log_dir: Optional[str] = None) -> dict:
    """Write the INFO + packaging views for the last ``minutes`` and return paths.

    If ``job_id`` is given, the packaging view is narrowed to that job's lines
    (``job_id=<id>`` plus the contiguous build/tool output around them).
    """
    ld = Path(log_dir) if log_dir else LOG_DIR
    out = Path(out_dir) if out_dir else (ld / "exports")
    out.mkdir(parents=True, exist_ok=True)

    worker, web = _read_tagged(ld)
    allts = [t for l in (worker + web) for t in [_ts_of(l)] if t]
    if not allts:
        return {"error": f"no timestamped log lines found under {ld}"}
    maxts = max(allts)
    cutoff = maxts - timedelta(minutes=minutes)

    wkept = _in_window(worker, cutoff)
    webkept = _in_window(web, cutoff)

    # INFO view — operational status, debug stripped, chronological.
    info = []
    for l in webkept + wkept:
        if "[debug" in l:
            continue
        if _INFO.search(l) or "Task autopackager" in l:
            info.append(_clean(l))
    info.sort(key=lambda l: (_TS.search(l).group(1) if _TS.search(l) else ""))

    # Packaging view — worker build/exec + full tracebacks; optional job filter.
    job_tag = f"job_id={job_id}" if job_id is not None else None
    pkg, in_tb, near_job = [], False, (job_id is None)
    for l in wkept:
        if job_tag is not None:
            if job_tag in l:
                near_job = True
            elif _ts_of(l) is not None and "job_id=" in l and job_tag not in l:
                near_job = False  # a different job's timestamped line ends the window
        if "Traceback (most recent call last)" in l:
            in_tb = True
        if in_tb:
            if near_job:
                pkg.append(_clean(l))
            if re.match(r"\S", _clean(l)) and re.search(r"(Error|Exception):", _clean(l)):
                in_tb = False
            continue
        if near_job and (_PKG.search(l) or _ERR.search(l)):
            pkg.append(_clean(l))

    stamp = maxts.strftime("%Y-%m-%d_%H%M")
    suffix = f"_job{job_id}" if job_id is not None else ""
    info_path = out / f"logs_{stamp}_INFO.log"
    pkg_path = out / f"logs_{stamp}{suffix}_packaging.log"
    head = "# AutoPackager {what} — {a:%Y-%m-%d %H:%M} to {b:%H:%M} ({n} lines)\n\n"
    info_path.write_text(
        head.format(what="operational/INFO log", a=cutoff, b=maxts, n=len(info))
        + "\n".join(info) + "\n", encoding="utf-8")
    pkg_path.write_text(
        head.format(what=f"package-build & execution log{(' — job ' + str(job_id)) if job_id is not None else ''}",
                    a=cutoff, b=maxts, n=len(pkg))
        + "\n".join(pkg) + "\n", encoding="utf-8")
    return {
        "info": str(info_path), "packaging": str(pkg_path),
        "window_minutes": minutes, "job_id": job_id,
        "info_lines": len(info), "packaging_lines": len(pkg),
        "from": cutoff.isoformat(sep=" "), "to": maxts.isoformat(sep=" "),
    }
