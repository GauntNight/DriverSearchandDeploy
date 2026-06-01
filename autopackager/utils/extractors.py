"""Wrapped-installer extraction helpers.

Some vendors ship their MSI installer inside an outer container -- either
an executable (Adobe Reader DC's -sfx_o-style self-extractor, PowerToys'
``--extract_msi``) or a ZIP bundle (Foxit Reader). The Intune pipeline
needs the inner MSI; this module unwraps the outer container so the rest
of the pipeline can treat the result as a regular MSI job.

Catalog entries with installer_family in {'wrapped_msi', 'wrapped_zip'}
carry the wrapper's identity (sha256 + pe_company_name + pe_product_name),
the extraction command template, and the glob pattern that picks the
inner MSI out of the extraction directory.

The functions in this module are deliberately small / synchronous:
extraction is a pre-stage to the existing async Celery pipeline, run by
the CLI (or future worker step) before a job is enqueued.
"""

from __future__ import annotations

import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from autopackager.utils.installer_catalog import CatalogEntry


class ExtractionError(Exception):
    """Raised when wrapper extraction fails (subprocess error, bad ZIP,
    no inner MSI found, missing extract_command_template, etc.).
    """


# Extraction timeout: vendor extractors are usually fast (seconds) but
# Adobe Reader's self-extractor can take 30-60s on a slow disk. Cap at
# 5 minutes -- anything longer is almost certainly a hang.
_EXTRACT_TIMEOUT_S = 300


def extract_wrapped(installer_path, entry: 'CatalogEntry', dest_dir) -> Path:
    """Extract the inner MSI from a wrapped installer.

    Args:
        installer_path: Path to the wrapper (EXE or ZIP) on disk.
        entry: Catalog entry whose installer_family identifies the
            extraction strategy. Must be wrapped_msi or wrapped_zip.
        dest_dir: Directory to extract into. Created if missing. NOT
            cleaned up on success -- the caller owns it (so the MSI
            stays readable through the rest of the pipeline).

    Returns:
        Absolute path to the extracted MSI. When the extracted dir
        contains multiple MSIs matching the entry's pattern (or default
        '*.msi'), the largest file is returned -- defends against tiny
        accessory MSIs bundled alongside the main product.

    Raises:
        ExtractionError: extraction failed, command missing for
            wrapped_msi, or no matching MSI found in the output.
    """
    installer_path = Path(installer_path).resolve()
    dest_dir = Path(dest_dir).resolve()
    dest_dir.mkdir(parents=True, exist_ok=True)

    family = entry.installer_family
    if family == 'wrapped_zip':
        try:
            with zipfile.ZipFile(installer_path) as z:
                z.extractall(dest_dir)
        except zipfile.BadZipFile as exc:
            raise ExtractionError(f"Not a valid ZIP: {installer_path}: {exc}") from exc
    elif family == 'wrapped_msi':
        if not entry.extract_command_template:
            raise ExtractionError(
                "wrapped_msi entry requires extract_command_template; "
                f"catalog entry {entry.id!r} has none"
            )
        cmd = entry.extract_command_template.format(
            installer_path=str(installer_path),
            extract_dir=str(dest_dir),
        )
        # shell=True because vendor extraction commands frequently have
        # nested quoting that Python's argv splitting mishandles.
        # cwd=dest_dir so installers that write to the current directory
        # (PowerToys' --extract_msi is the canonical example) drop the
        # MSI into the right place without needing an explicit destination.
        try:
            result = subprocess.run(
                cmd, shell=True, cwd=str(dest_dir),
                capture_output=True, timeout=_EXTRACT_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired as exc:
            raise ExtractionError(
                f"Extraction timed out after {_EXTRACT_TIMEOUT_S}s: {cmd!r}"
            ) from exc
        if result.returncode != 0:
            raise ExtractionError(
                f"Extraction failed (exit {result.returncode}): {cmd!r}\n"
                f"stderr: {result.stderr.decode(errors='replace')[:1000]}"
            )
    else:
        raise ExtractionError(
            f"installer_family {family!r} is not a wrapped type; "
            "expected 'wrapped_msi' or 'wrapped_zip'"
        )

    pattern = entry.extracted_msi_pattern or '*.msi'
    matches = list(dest_dir.rglob(pattern))
    if not matches:
        # Provide a useful error: list what we DID find so the operator
        # can update extracted_msi_pattern.
        all_files = [p.relative_to(dest_dir) for p in dest_dir.rglob('*') if p.is_file()]
        sample = ', '.join(str(p) for p in all_files[:10])
        more = f' ({len(all_files) - 10} more)' if len(all_files) > 10 else ''
        raise ExtractionError(
            f"No MSI matching {pattern!r} found in {dest_dir}. "
            f"Extracted files: {sample}{more}"
        )
    return max(matches, key=lambda p: p.stat().st_size)


def clean_extraction_dir(dest_dir) -> None:
    """Remove the extraction directory and everything in it. Best-effort.

    Call after the pipeline has consumed the inner MSI to free disk
    (Adobe Reader extracts ~500MB; Foxit ~300MB).
    """
    shutil.rmtree(Path(dest_dir), ignore_errors=True)
