"""Demo intake: file/URL/driver -> identity -> catalog hit/miss -> enqueue.

This module is a thin orchestration layer over EXISTING extraction, matching,
and enqueue code. It does NOT reimplement metadata parsing or catalog matching
— it calls ``read_msi_metadata`` / ``read_pe_metadata`` / ``sha256_file`` and
``Catalog.match_msi`` / ``Catalog.match_exe`` exactly as the CLI does, and it
builds the same ``job_metadata`` shape the Celery pipeline already consumes.

Two structural notes:

* It creates the ``Job`` row directly via ``OrchestrationEngine`` (instead of
  the ``create_packaging_job`` Celery task) purely so the HTTP layer gets the
  ``job_id`` synchronously — the demo needs it immediately to open the SSE
  channel. The pipeline that then runs is the real one (``process_job`` / the
  same task chain).
* The catalog-MISS path returns ``branch="miss"`` without enqueuing; the router
  invokes the Claude bridge, which writes a catalog entry, after which
  ``enqueue_software_job`` is called again and now resolves as a HIT.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from autopackager.models.job import JobType
from autopackager.orchestration.engine import OrchestrationEngine
from autopackager.utils.logger import get_logger

logger = get_logger(__name__)

# Scoped demo sandbox — NOT C:\ root (security guardrail, demo/README §6).
_REPO_ROOT = Path(__file__).resolve().parent.parent
SANDBOX_DIR = _REPO_ROOT / "data" / "demo_sandbox"
UPLOAD_DIR = SANDBOX_DIR / "uploads"


# The only installer types the demo will process. Anything else (a misclicked
# document, an image, a URL that isn't a direct installer link) is rejected
# before any work happens.
KNOWN_EXTENSIONS = {".msi", ".exe", ".zip"}


def is_known_installer(name: Optional[str]) -> bool:
    """True if ``name`` (a filename or URL) ends in a supported installer type."""
    if not name:
        return False
    base = str(name).split("?")[0].split("#")[0]
    return Path(base).suffix.lower() in KNOWN_EXTENSIONS


def ensure_dirs() -> None:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class Analysis:
    """Result of identifying a dropped/downloaded installer."""

    kind: str                       # "msi" | "exe"
    path: str                       # absolute path in the sandbox
    filename: str
    branch: str                     # "hit" | "miss"
    metadata: Dict[str, Any] = field(default_factory=dict)
    sha256: Optional[str] = None
    catalog_entry_id: Optional[str] = None
    product_name: Optional[str] = None
    version: Optional[str] = None
    publisher: Optional[str] = None
    install_command: Optional[str] = None
    detection_rule_count: int = 0
    # Populated for EXE miss / EXE hit-without-rules so the UI can explain why
    # the deterministic path can't run yet.
    blocker: Optional[str] = None
    # Consumer-vs-enterprise: set when the matched catalog entry redirects to a
    # better build (the enterprise MSI). The intake layer fetches it instead.
    prefer_entry_id: Optional[str] = None
    consumer_caveats: Optional[str] = None
    substituted_from: Optional[str] = None  # set on a substituted analysis

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "filename": self.filename,
            "branch": self.branch,
            "sha256": self.sha256,
            "catalog_entry_id": self.catalog_entry_id,
            "product_name": self.product_name,
            "version": self.version,
            "publisher": self.publisher,
            "install_command": self.install_command,
            "detection_rule_count": self.detection_rule_count,
            "blocker": self.blocker,
            "prefer_entry_id": self.prefer_entry_id,
            "consumer_caveats": self.consumer_caveats,
            "substituted_from": self.substituted_from,
            "metadata": self.metadata,
        }


# --- File / URL acquisition -------------------------------------------------

def save_upload(filename: str, data: bytes) -> Path:
    """Persist an uploaded installer into the sandbox; return its path."""
    ensure_dirs()
    safe = Path(filename).name  # strip any path components
    dest = UPLOAD_DIR / safe
    dest.write_bytes(data)
    logger.info("Saved demo upload", path=str(dest), bytes=len(data))
    return dest


def download_to(url: str, dest: Path) -> Path:
    """Stream a URL to a specific destination path (follows redirects)."""
    import requests

    ensure_dirs()
    dest.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Downloading installer", url=url, dest=str(dest))
    with requests.get(url, stream=True, timeout=300, allow_redirects=True) as resp:
        resp.raise_for_status()
        with open(dest, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    fh.write(chunk)
    return dest


def download_to_sandbox(url: str) -> Path:
    """Download an installer URL into the sandbox; return its path."""
    name = Path(urlparse(url).path).name or "installer.bin"
    return download_to(url, UPLOAD_DIR / name)


def substitute_with_enterprise(analysis: "Analysis") -> Optional["Analysis"]:
    """If ``analysis`` matched a consumer build that redirects to a better
    catalog entry (``prefer_entry_id``) carrying a ``canonical_download_url``,
    fetch that enterprise installer and return a fresh Analysis for it.

    Returns None when there is no redirect or no fetchable alternative — the
    caller then surfaces the caveat and stops rather than publishing the
    consumer build.
    """
    if not analysis.prefer_entry_id:
        return None
    from autopackager.utils import installer_catalog

    catalog = installer_catalog.load_catalog()
    target = catalog.by_id(analysis.prefer_entry_id)
    if not target or not target.canonical_download_url:
        logger.warning("No fetchable enterprise alternative",
                       prefer=analysis.prefer_entry_id)
        return None
    ext = {"msi": ".msi", "exe": ".exe", "zip": ".zip"}.get(target.type, ".bin")
    dest = UPLOAD_DIR / f"{target.id}{ext}"
    download_to(target.canonical_download_url, dest)
    substitute = analyze(dest)
    substitute.substituted_from = analysis.catalog_entry_id or analysis.filename
    return substitute


# --- Identity + catalog match ----------------------------------------------

def analyze(path: Path) -> Analysis:
    """Extract identity and resolve catalog hit/miss for an installer file.

    Uses the SAME extractors and matchers as the CLI. MSI vs EXE is decided by
    extension (matching ``cli._installer_is_exe``).
    """
    from autopackager.utils import installer_catalog

    path = Path(path)
    is_exe = path.suffix.lower() == ".exe"
    catalog = installer_catalog.load_catalog()

    if is_exe:
        from autopackager.utils.pe_metadata import (
            read_pe_metadata, sha256_file, PEParseError,
        )

        pe_meta: Dict[str, Any] = {}
        try:
            pe_meta = read_pe_metadata(path).to_dict()
        except PEParseError:
            pe_meta = {}
        sha = sha256_file(path)
        entry = catalog.match_exe(pe_metadata=pe_meta, sha256=sha, filename=path.name)
        analysis = Analysis(
            kind="exe",
            path=str(path.resolve()),
            filename=path.name,
            branch="hit" if entry else "miss",
            metadata=pe_meta,
            sha256=sha,
            catalog_entry_id=entry.id if entry else None,
            product_name=pe_meta.get("product_name") or None,
            version=pe_meta.get("product_version") or pe_meta.get("file_version") or None,
            publisher=pe_meta.get("company_name") or None,
        )
        if entry:
            rules = entry.detection_rules or []
            analysis.detection_rule_count = len(rules)
            analysis.install_command = entry.render_install_command(path.name)
            if entry.prefer_entry_id:
                # Consumer build with a known-better enterprise alternative.
                # Don't blocker-miss on missing rules — substitution replaces
                # this installer entirely.
                analysis.prefer_entry_id = entry.prefer_entry_id
                analysis.consumer_caveats = entry.consumer_caveats
            elif not rules:
                # A hit with no detection rules can't publish deterministically;
                # the research bridge is the correct path to author one.
                analysis.branch = "miss"
                analysis.blocker = (
                    f"catalog entry '{entry.id}' has no detection rules"
                )
        return analysis

    # MSI
    from autopackager.utils.msi_metadata import read_msi_metadata, MSIParseError

    msi_meta: Dict[str, Any] = {}
    try:
        msi_meta = read_msi_metadata(path).to_dict()
    except MSIParseError as exc:
        logger.warning("MSI parse failed", error=str(exc))
    entry = catalog.match_msi(msi_meta) if msi_meta else None
    analysis = Analysis(
        kind="msi",
        path=str(path.resolve()),
        filename=path.name,
        branch="hit" if entry else "miss",
        metadata=msi_meta,
        catalog_entry_id=entry.id if entry else None,
        product_name=msi_meta.get("product_name") or None,
        version=msi_meta.get("product_version") or None,
        publisher=msi_meta.get("manufacturer") or None,
    )
    if entry:
        analysis.install_command = entry.render_install_command(path.name)
        if entry.prefer_entry_id:
            analysis.prefer_entry_id = entry.prefer_entry_id
            analysis.consumer_caveats = entry.consumer_caveats
    else:
        # MSI miss still has a deterministic default, but per the demo spec we
        # route unknown installers through the research bridge so the audience
        # sees the model author the command + detection rule.
        analysis.install_command = f"msiexec /i {path.name} /qn /norestart"
    return analysis


# --- Job metadata construction (mirrors cli.py) -----------------------------

def _build_software_job_metadata(analysis: Analysis) -> Dict[str, Any]:
    """Build the ``job_metadata`` dict the pipeline expects for a software job.

    Mirrors ``cli.create_software_job`` / ``_create_exe_software_job`` exactly.
    """
    installer_source = analysis.path  # local path; packaging resolves file://-ish
    md: Dict[str, Any] = {
        "install_command": analysis.install_command,
        "download_url": installer_source,
        "installer_source": installer_source,
    }
    if analysis.version:
        md["target_version"] = analysis.version
    if analysis.kind == "msi":
        if analysis.metadata:
            md["msi_metadata"] = analysis.metadata
    else:  # exe
        if analysis.catalog_entry_id:
            md["catalog_entry_id"] = analysis.catalog_entry_id
        if analysis.metadata:
            md["exe_metadata"] = analysis.metadata
        if analysis.sha256:
            md["sha256"] = analysis.sha256
    return md


# --- Enqueue ----------------------------------------------------------------

def create_job_row(
    *,
    job_type: JobType,
    software_title: str,
    vendor: str,
    current_version: Optional[str],
    job_metadata: Dict[str, Any],
    gate: bool,
    hardware_model: Optional[str] = None,
    driver_type: Optional[str] = None,
) -> int:
    """Create the Job row (synchronously) and return its id — no dispatch.

    Splitting create from dispatch lets the catalog-MISS path open the job's
    SSE channel first (so research lines have somewhere to stream), run the
    research bridge, refine the metadata, and only then dispatch the pipeline.
    """
    engine = OrchestrationEngine()
    if gate:
        job_metadata = {**job_metadata, "demo_gate_deploy": True}
    job = engine.create_job(
        job_type=job_type,
        software_title=software_title,
        vendor=vendor,
        current_version=current_version,
        hardware_model=hardware_model,
        driver_type=driver_type,
        metadata=job_metadata,
    )
    return job.id


def dispatch_pipeline(job_id: int, *, gate: bool = False) -> None:
    """Dispatch the real pipeline for an already-created job row.

    When ``gate`` is True the deployment stage is held back — only
    discovery→packaging→testing are enqueued; the demo's ``/approve`` endpoint
    enqueues deployment after the operator clicks Approve.
    """
    # Defer the import so a missing Celery/broker at import time doesn't break
    # the whole module (e.g. fixture-mode rehearsal on a laptop).
    from autopackager.orchestration.tasks import (
        process_job, discovery_task, packaging_task, testing_task,
    )
    from celery import chain

    if gate:
        chain(
            discovery_task.s(job_id),
            packaging_task.s(job_id),
            testing_task.s(job_id),
        ).apply_async()
    else:
        process_job.delay(job_id)
    logger.info("Demo job dispatched", job_id=job_id, gate=gate)


def update_software_metadata(job_id: int, analysis: Analysis) -> None:
    """Refresh a job's metadata from a (re-)resolved analysis.

    Used on the MISS path after the research bridge has written a catalog entry
    — the re-analyzed installer now carries the researched install command and
    (for EXE) ``catalog_entry_id``.
    """
    from autopackager.models.job import JobState

    engine = OrchestrationEngine()
    engine.update_job_state(
        job_id, JobState.PENDING,
        metadata_update=_build_software_job_metadata(analysis),
        # Re-point the job's identity at the (re-)resolved installer so the
        # deployed app's displayName/vendor follow it — critical on the
        # substitution path, where the row was created from the consumer stub
        # (e.g. "Google Installer (x86)") but we deploy the enterprise MSI
        # ("Google Chrome"). The displayVersion follows via target_version in
        # the metadata above.
        software_title=(analysis.product_name or Path(analysis.filename).stem),
        vendor=(analysis.publisher or None),
    )


def enqueue_software_job(analysis: Analysis, *, gate: bool = False) -> int:
    """Create + dispatch a software job from a resolved analysis (HIT path)."""
    vendor = analysis.publisher or "Unknown"
    title = analysis.product_name or Path(analysis.filename).stem
    job_id = create_job_row(
        job_type=JobType.NEW_SOFTWARE,
        software_title=title,
        vendor=vendor,
        current_version=None,
        job_metadata=_build_software_job_metadata(analysis),
        gate=gate,
    )
    dispatch_pipeline(job_id, gate=gate)
    return job_id


def create_software_job_row(analysis: Analysis, *, gate: bool = False) -> int:
    """Create the job row for a software job WITHOUT dispatching (MISS path)."""
    vendor = analysis.publisher or "Unknown"
    title = analysis.product_name or Path(analysis.filename).stem
    return create_job_row(
        job_type=JobType.NEW_SOFTWARE,
        software_title=title,
        vendor=vendor,
        current_version=None,
        job_metadata=_build_software_job_metadata(analysis),
        gate=gate,
    )


def enqueue_driver_job(
    vendor: str,
    model: str,
    driver_type: Optional[str],
    current_version: Optional[str],
    *,
    gate: bool = False,
) -> int:
    """Enqueue a driver-update job (mirrors ``cli.create_driver_job``)."""
    job_id = create_job_row(
        job_type=JobType.DRIVER_UPDATE,
        software_title=f"{vendor.upper()} {model} Driver Pack",
        vendor=vendor,
        current_version=current_version,
        job_metadata={},
        gate=gate,
        hardware_model=model,
        driver_type=driver_type,
    )
    dispatch_pipeline(job_id, gate=gate)
    return job_id


def dispatch_deployment(job_id: int) -> None:
    """Enqueue the deployment stage for a gated job (called on /approve)."""
    from autopackager.orchestration.tasks import deployment_task

    # deployment_task signature is (previous_result, job_id); pass a benign
    # previous result so it doesn't short-circuit on `completed`.
    deployment_task.apply_async(args=[{"job_id": job_id}, job_id])
    logger.info("Demo deployment dispatched after approval", job_id=job_id)


def cleanup_sandbox() -> int:
    """Remove all staged uploads. Returns count removed. (Rehearsal helper.)"""
    if not UPLOAD_DIR.exists():
        return 0
    n = 0
    for child in UPLOAD_DIR.iterdir():
        try:
            if child.is_file():
                child.unlink()
            else:
                shutil.rmtree(child)
            n += 1
        except OSError:
            pass
    return n
