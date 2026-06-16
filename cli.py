#!/usr/bin/env python3
"""
AutoPackager CLI - Command Line Interface for AutoPackager
"""

import re
import sys
from typing import Optional

import click
from rich.console import Console
from rich.table import Table
from pathlib import Path

# Force UTF-8 on stdout/stderr so Rich glyphs (✓ ✗) don't crash on cp1252 consoles.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

from autopackager.orchestration.engine import OrchestrationEngine
from autopackager.utils.azure_validator import AzureValidator, AzureConfigurationError, ValidationResult
from autopackager.models.job import JobType, JobState
from autopackager.models.deployment import Deployment
from autopackager.utils.config import get_config
from autopackager.utils.database import init_db, db_session_scope
from autopackager.utils.logger import setup_logging, get_logger
from autopackager.orchestration.tasks import create_packaging_job
from autopackager.agents.deployment.deployment_agent import DeploymentAgent

console = Console()


@click.group()
@click.option('--config', type=click.Path(), help='Path to configuration file')
@click.option('--debug', is_flag=True, help='Enable debug logging')
def cli(config, debug):
    """AutoPackager - Autonomous Software Packaging Factory"""
    # Setup logging
    log_level = "DEBUG" if debug else "INFO"
    setup_logging(log_level=log_level, log_file="data/logs/autopackager.log")


@cli.command()
def init():
    """Initialize database and create tables"""
    console.print("[bold blue]Initializing AutoPackager...[/bold blue]")

    try:
        init_db(create_tables=True)
        console.print("[bold green]✓[/bold green] Database initialized successfully")
    except Exception as e:
        console.print(f"[bold red]✗[/bold red] Failed to initialize database: {str(e)}")
        raise click.Abort()


@cli.command()
@click.option('--vendor', required=True, type=click.Choice(['hp', 'lenovo', 'dell'], case_sensitive=False), help='OEM vendor')
@click.option('--model', required=True, help='Hardware model (e.g., "ThinkPad X1 Carbon Gen 9" or "EliteBook 850 G8")')
@click.option('--driver-type', help='Driver type (e.g., chipset, network, graphics)')
@click.option('--current-version', help='Current driver version')
def create_driver_job(vendor, model, driver_type, current_version):
    """Create a new driver update job"""
    console.print(f"[bold blue]Creating driver update job...[/bold blue]")
    console.print(f"  Vendor: {vendor}")
    console.print(f"  Model: {model}")
    console.print(f"  Driver Type: {driver_type or 'All'}")

    try:
        # Create job via Celery task
        result = create_packaging_job.delay(
            job_type='driver_update',
            software_title=f"{vendor.upper()} {model} Driver Pack",
            vendor=vendor,
            current_version=current_version,
            hardware_model=model,
            driver_type=driver_type
        )

        console.print(f"[bold green]✓[/bold green] Job created successfully")
        console.print(f"  Task ID: {result.id}")
        console.print(f"\nUse 'autopackager jobs list' to check status")

    except Exception as e:
        console.print(f"[bold red]✗[/bold red] Failed to create job: {str(e)}")
        raise click.Abort()


def _installer_is_exe(installer_path: Optional[str], download_url: Optional[str]) -> bool:
    """Return True when the operator's --installer-path / --download-url
    points at an EXE (case-insensitive extension match). MSI is the default
    for everything else."""
    src = installer_path or download_url or ''
    return src.lower().split('?')[0].endswith('.exe')


def _try_unwrap_installer(installer_path: Optional[str]) -> Optional[str]:
    """If the installer matches a wrapped catalog entry, extract the inner
    MSI and return its path. Otherwise return None.

    Handles both wrapped_msi (EXE that bundles an MSI -- Adobe Reader DC)
    and wrapped_zip (ZIP containing an MSI -- Foxit Reader). (PowerToys is
    a Burn bundle shipped as msft_bootstrapper, not unwrapped here.)
    Runs as a pre-stage before extension dispatch so the rest of
    create-software-job sees a regular MSI.

    Returns the extracted MSI path on success; None when the installer
    isn't a recognised wrapper. ExtractionError propagates -- a wrapped
    catalog entry that fails to extract is an operator-visible problem.
    """
    if not installer_path:
        return None
    path = Path(installer_path)
    ext = path.suffix.lower()
    if ext not in ('.exe', '.zip'):
        return None

    from autopackager.utils import installer_catalog
    from autopackager.utils.extractors import extract_wrapped
    from autopackager.utils.pe_metadata import read_pe_metadata, sha256_file, PEParseError

    sha = sha256_file(path)
    pe_meta = None
    if ext == '.exe':
        try:
            pe_meta = read_pe_metadata(path).to_dict()
        except PEParseError:
            pe_meta = None

    entry = installer_catalog.load_catalog().match_exe(pe_metadata=pe_meta, sha256=sha, filename=path.name)
    if not entry or entry.installer_family not in ('wrapped_msi', 'wrapped_zip'):
        return None

    # Stage extraction output under data/downloads/extracted/<entry-id>/
    # so it survives across CLI -> worker handoff. Caller is responsible
    # for cleanup; for now we let it accumulate (operator can prune).
    extract_dir = Path('data/downloads/extracted') / entry.id
    console.print(
        f"\n[bold]Wrapped {entry.installer_family}[/bold] catalog hit: [cyan]{entry.id}[/cyan]"
    )
    console.print(f"  Extracting inner MSI into {extract_dir}...")
    inner_msi = extract_wrapped(path, entry, extract_dir)
    console.print(f"  Inner MSI: [cyan]{inner_msi}[/cyan]")
    return str(inner_msi)


@cli.command('create-software-job')
@click.option('--install-command',
              help='Install command. For MSI, e.g. "msiexec /i 7z2408-x64.msi /qn /norestart". '
                   'For EXE, the silent-install command, e.g. "Git-2.46.0-64-bit.exe /VERYSILENT". '
                   'If omitted, AutoPackager looks the installer up in the catalog '
                   '(autopackager/data/installer_catalog.yaml plus the local overlay).')
@click.option('--installer-path', type=click.Path(exists=True, dir_okay=False),
              help='Local path to the MSI or EXE (metadata is read to auto-fill the package)')
@click.option('--download-url', help='URL to download the installer from during packaging')
@click.option('--name', help='Override the product/display name')
@click.option('--publisher', help='Override the publisher')
@click.option('--current-version', help='Currently installed version, if any')
@click.option('--no-assignment', is_flag=True, default=False,
              help='Publish the app to Intune without assigning it to any ring '
                   '(use for safe test publishes against production tenants).')
@click.option('--no-save-catalog', is_flag=True, default=False,
              help='Skip auto-appending this installer to data/installer_catalog.local.yaml '
                   '(the local overlay). The committed baseline is never modified at runtime.')
@click.option('--supersede', is_flag=True, default=False,
              help='Apply supersedence per the catalog entry\'s declared mode + line. '
                   'Newer versions in the same line get listed in the new Intune Win32 '
                   'app\'s supersedingApps. Mutually exclusive with --supersedes. '
                   'Refused when the catalog entry has supersedence.mode=none.')
@click.option('--supersedes', multiple=True, metavar='ENTRY_ID',
              help='Manually mark the given catalog entry IDs as superseded by this '
                   'publish. Overrides the catalog\'s declared mode/line/supersedes. '
                   'Repeat the flag per ID: --supersedes vlc-3.0.21 --supersedes vlc-3.0.22. '
                   'Mutually exclusive with --supersede.')
def create_software_job(install_command, installer_path, download_url, name, publisher,
                        current_version, no_assignment, no_save_catalog,
                        supersede, supersedes):
    """Create a software packaging job for an MSI or EXE installer.

    MSI path: provide the MSI via --installer-path and/or --download-url. The
    factory reads MSI metadata to auto-fill the Intune package, derives the
    detection rule from the ProductCode, and assigns deployment rings.

    EXE path: provide the EXE via --installer-path and/or --download-url. The
    factory reads PE VS_VERSIONINFO and looks the binary up in the installer
    catalog (by SHA-256, then by CompanyName + ProductName). The catalog
    entry MUST supply detection_rules -- the EXE pipeline refuses to publish
    without them (Intune treats apps with no detection rule as never-installed
    and re-runs the install on every check-in).
    """
    if not installer_path and not download_url:
        console.print("[bold red]✗[/bold red] Provide --installer-path and/or --download-url")
        raise click.Abort()

    if supersede and supersedes:
        console.print("[bold red]✗[/bold red] --supersede and --supersedes are mutually exclusive")
        raise click.Abort()
    supersedence_opt_in = bool(supersede or supersedes)
    supersedes_ids = list(supersedes) if supersedes else None

    # Wrapped-installer pre-stage: if the file is a known wrapped_msi /
    # wrapped_zip per the catalog, extract the inner MSI now so the rest
    # of this command treats it as a normal MSI. Anything that's not a
    # wrapper falls through unchanged.
    try:
        unwrapped = _try_unwrap_installer(installer_path)
    except Exception as exc:  # noqa: BLE001 -- extraction is the operator's problem
        console.print(f"[bold red]✗[/bold red] Wrapped-installer extraction failed: {exc}")
        raise click.Abort()
    if unwrapped:
        installer_path = unwrapped

    if _installer_is_exe(installer_path, download_url):
        _create_exe_software_job(
            install_command, installer_path, download_url,
            name, publisher, current_version, no_assignment, no_save_catalog,
            supersedence_opt_in=supersedence_opt_in,
            supersedes_ids=supersedes_ids,
        )
        return

    from autopackager.utils.msi_metadata import (
        read_msi_metadata,
        parse_install_command,
        MSIParseError,
    )
    from autopackager.utils import installer_catalog

    console.print("[bold blue]Creating MSI software job...[/bold blue]")

    # Read MSI metadata up front when the file is available locally.
    msi_meta = None
    if installer_path:
        try:
            metadata = read_msi_metadata(installer_path)
            msi_meta = metadata.to_dict()
            console.print("\n[bold]Detected MSI metadata:[/bold]")
            console.print(f"  Product Name:    {metadata.product_name or 'N/A'}")
            console.print(f"  Version:         {metadata.product_version or 'N/A'}")
            console.print(f"  Publisher:       {metadata.manufacturer or 'N/A'}")
            console.print(f"  Product Code:    {metadata.product_code or 'N/A'}")
            console.print(f"  Upgrade Code:    {metadata.upgrade_code or 'N/A'}")
        except MSIParseError as e:
            console.print(f"[yellow]⚠[/yellow] Could not read MSI metadata: {e}")

    # Catalog lookup: pick up a known install command when --install-command was omitted.
    catalog_entry = None
    catalog = installer_catalog.load_catalog()
    if msi_meta:
        catalog_entry = catalog.match_msi(msi_meta)
    if catalog_entry:
        console.print(
            f"\n[bold green]Catalog hit:[/bold green] {catalog_entry.id} "
            f"(used {catalog_entry.use_count}x, last {catalog_entry.last_used or 'never'})"
        )

    if not install_command:
        installer_filename = (
            Path(installer_path).name if installer_path
            else Path(download_url).name if download_url else 'installer.msi'
        )
        if catalog_entry:
            install_command = catalog_entry.render_install_command(installer_filename)
            console.print(f"  Using catalog template: [cyan]{install_command}[/cyan]")
        else:
            console.print(
                "\n[yellow]No catalog entry matched this installer.[/yellow] "
                f"Suggested default: [cyan]msiexec /i {installer_filename} /qn /norestart[/cyan]"
            )
            install_command = click.prompt(
                "  Install command",
                default=f"msiexec /i {installer_filename} /qn /norestart",
            )

    parsed = parse_install_command(install_command)
    if parsed.action != 'install':
        console.print(f"[yellow]⚠[/yellow] Install command action is '{parsed.action}', expected install")

    product_name = name or (msi_meta or {}).get('product_name')
    if not product_name and parsed.msi_file:
        product_name = Path(parsed.msi_file).stem
    if not product_name:
        console.print("[bold red]✗[/bold red] Could not determine product name; pass --name")
        raise click.Abort()

    vendor = publisher or (msi_meta or {}).get('manufacturer') or 'Unknown'
    target_version = (msi_meta or {}).get('product_version')

    # Packaging needs a fetchable source: prefer the URL, else the local file.
    installer_source = download_url or str(Path(installer_path).resolve())

    job_metadata = {
        'install_command': install_command,
        'download_url': installer_source,
        'installer_source': installer_source,
    }
    if target_version:
        job_metadata['target_version'] = target_version
    if msi_meta:
        job_metadata['msi_metadata'] = msi_meta
    if no_assignment:
        job_metadata['no_assignment'] = True

    # Resolve supersedence at CLI time (snapshot of catalog state) so the
    # deployment agent applies exactly what the operator saw. Catalog
    # entry might not exist yet if this is a brand-new product -- in that
    # case supersedence is a no-op regardless of --supersede.
    if supersedence_opt_in and catalog_entry and target_version:
        try:
            resolution = installer_catalog.resolve_supersedence(
                catalog, catalog_entry, target_version,
                operator_opted_in=True,
                explicit_supersedes=supersedes_ids,
            )
            if resolution.enabled:
                job_metadata['supersedence_action'] = {
                    'mode_used': resolution.mode_used,
                    'superseded_intune_app_ids': resolution.superseded_intune_app_ids,
                    'demoted_records': [
                        {'entry_id': eid, 'product_version': vv.get('product_version'),
                         'verified_intune_app_id': vv.get('verified_intune_app_id')}
                        for eid, vv in resolution.demoted_records
                    ],
                    'notes': resolution.notes,
                }
                console.print(
                    f"\n[bold]Supersedence:[/bold] mode={resolution.mode_used}, "
                    f"will mark {len(resolution.demoted_records)} prior verified "
                    f"row(s) superseded, {len(resolution.superseded_intune_app_ids)} "
                    f"Intune app id(s) in supersedingApps"
                )
            else:
                console.print("\n[yellow]Supersedence requested but no targets "
                              "found in this line.[/yellow]")
        except installer_catalog.SupersedenceError as exc:
            console.print(f"[bold red]✗[/bold red] {exc}")
            raise click.Abort()
    elif supersedence_opt_in and not catalog_entry:
        console.print(
            "[yellow]⚠[/yellow] --supersede ignored: no catalog entry matched this "
            "installer (supersedence requires a known catalog id)"
        )

    try:
        result = create_packaging_job.delay(
            job_type=JobType.NEW_SOFTWARE.value,
            software_title=product_name,
            vendor=vendor,
            current_version=current_version,
            metadata=job_metadata,
        )

        console.print(f"\n[bold green]✓[/bold green] Job created successfully")
        console.print(f"  Product: {product_name}")
        console.print(f"  Vendor:  {vendor}")
        console.print(f"  Version: {target_version or 'unknown'}")
        console.print(f"  Task ID: {result.id}")
        console.print(f"\nUse 'autopackager jobs list' to check status")

        # Update the installer catalog overlay so future runs of this MSI skip
        # the prompt. Failures here are non-fatal -- the job is already enqueued.
        if not no_save_catalog and msi_meta:
            try:
                installer_filename = Path(parsed.msi_file).name if parsed.msi_file else 'installer.msi'
                template = install_command.replace(installer_filename, '{installer_filename}', 1)
                if catalog_entry:
                    installer_catalog.record_use(catalog_entry.id)
                    console.print(f"  [dim]Catalog: bumped use_count for '{catalog_entry.id}'[/dim]")
                else:
                    new_entry = installer_catalog.add_msi_entry(
                        msi_meta,
                        install_command_template=template,
                        notes=f"Auto-added by create-software-job (task {result.id})",
                    )
                    console.print(
                        f"  [dim]Catalog: added new entry '{new_entry.id}' to "
                        "data/installer_catalog.local.yaml[/dim]"
                    )
            except Exception as catalog_exc:
                console.print(f"  [yellow]⚠ Catalog update skipped: {catalog_exc}[/yellow]")

    except Exception as e:
        console.print(f"[bold red]✗[/bold red] Failed to create job: {str(e)}")
        raise click.Abort()


def _create_exe_software_job(install_command, installer_path, download_url, name,
                             publisher, current_version, no_assignment, no_save_catalog,
                             *, supersedence_opt_in=False, supersedes_ids=None):
    """EXE-specific create-software-job flow. Called from create_software_job
    when the installer extension is .exe.

    EXE differs from MSI in two important ways:
      1. There's no MSI ProductCode to lean on for the detection rule, so
         we require a catalog entry whose detection_rules list is non-empty.
         Refusing to publish without rules is deliberate -- an Intune Win32
         app with no detection rule is treated as never-installed and the
         IME re-runs the install on every device check-in.
      2. The silent-install command varies per installer family (NSIS '/S',
         Inno Setup '/VERYSILENT /NORESTART /SUPPRESSMSGBOXES', Microsoft
         bootstrappers '/quiet /norestart'). The catalog's installer_family
         drives the default; --install-command overrides per-run.
    """
    from autopackager.utils.pe_metadata import read_pe_metadata, sha256_file, PEParseError
    from autopackager.utils import installer_catalog

    console.print("[bold blue]Creating EXE software job...[/bold blue]")

    pe_meta = None
    pe_sha256 = None
    if installer_path:
        try:
            metadata = read_pe_metadata(installer_path)
            pe_meta = metadata.to_dict()
            pe_sha256 = sha256_file(installer_path)
            console.print("\n[bold]Detected PE metadata:[/bold]")
            console.print(f"  Company Name:     {metadata.company_name or 'N/A'}")
            console.print(f"  Product Name:     {metadata.product_name or 'N/A'}")
            console.print(f"  Product Version:  {metadata.product_version or 'N/A'}")
            console.print(f"  File Version:     {metadata.file_version or 'N/A'}")
            console.print(f"  Original Name:    {metadata.original_filename or 'N/A'}")
            console.print(f"  SHA-256:          {pe_sha256[:16]}...")
        except PEParseError as e:
            console.print(f"[yellow]⚠[/yellow] Could not read PE metadata: {e}")

    catalog = installer_catalog.load_catalog()
    catalog_entry = catalog.match_exe(pe_metadata=pe_meta, sha256=pe_sha256,
                                      filename=Path(installer_path).name if installer_path else None)

    if catalog_entry and catalog_entry.escalate_reason:
        console.print(
            f"\n[bold red]⛔ Engineer escalation — not packaging '{catalog_entry.id}'.[/bold red]\n"
            f"{catalog_entry.escalate_reason}\n"
            "This installer is flagged non-packageable in the catalog "
            "(no silent install / no managed build). Nothing was enqueued."
        )
        raise click.Abort()

    if not catalog_entry:
        console.print(
            "\n[bold red]✗ No catalog entry matched this EXE.[/bold red]\n"
            "EXE installers require a catalog entry with detection_rules so the\n"
            "Intune Win32 app has a valid detection rule. Add an entry to\n"
            "[cyan]autopackager/data/installer_catalog.yaml[/cyan] (baseline, shared)\n"
            "or [cyan]data/installer_catalog.local.yaml[/cyan] (tenant-only) with at\n"
            "least: type: exe, installer_family, install_command_template,\n"
            "pe_company_name / pe_product_name (for lookup), and detection_rules.\n"
            "See the Notepad++ baseline entry for a worked example."
        )
        raise click.Abort()

    if not catalog_entry.detection_rules:
        console.print(
            f"\n[bold red]✗ Catalog entry '{catalog_entry.id}' has no "
            "detection_rules.[/bold red]\n"
            "EXE Win32 apps with no detection rule are re-installed by Intune on\n"
            "every device check-in (the IME has no way to tell the app is\n"
            "already there). Add at least one rule -- the 90% case is a\n"
            "registry_version check against the Uninstall key DisplayVersion.\n"
            "See the Notepad++ baseline entry for a worked example."
        )
        raise click.Abort()

    console.print(
        f"\n[bold green]Catalog hit:[/bold green] {catalog_entry.id} "
        f"(used {catalog_entry.use_count}x, last {catalog_entry.last_used or 'never'})"
    )
    console.print(f"  Family:           {catalog_entry.installer_family or 'unknown'}")
    console.print(f"  Detection rules:  {len(catalog_entry.detection_rules)} rule(s)")

    installer_filename = (
        Path(installer_path).name if installer_path
        else Path(download_url).name if download_url else 'installer.exe'
    )

    if not install_command:
        install_command = catalog_entry.render_install_command(installer_filename)
    console.print(f"  Install command:  [cyan]{install_command}[/cyan]")

    product_name = name or (pe_meta or {}).get('product_name')
    if not product_name:
        console.print("[bold red]✗[/bold red] Could not determine product name; pass --name")
        raise click.Abort()
    vendor = publisher or (pe_meta or {}).get('company_name') or 'Unknown'
    target_version = (pe_meta or {}).get('product_version') or (pe_meta or {}).get('file_version')

    installer_source = download_url or str(Path(installer_path).resolve())
    job_metadata = {
        'install_command': install_command,
        'download_url': installer_source,
        'installer_source': installer_source,
        'catalog_entry_id': catalog_entry.id,
    }
    if target_version:
        job_metadata['target_version'] = target_version
    if pe_meta:
        job_metadata['exe_metadata'] = pe_meta
    if pe_sha256:
        job_metadata['sha256'] = pe_sha256
    if no_assignment:
        job_metadata['no_assignment'] = True

    # EXE supersedence: same resolution shape as MSI, just sourced via the
    # EXE catalog match path. See the MSI branch for the rationale.
    if supersedence_opt_in and target_version:
        try:
            resolution = installer_catalog.resolve_supersedence(
                catalog, catalog_entry, target_version,
                operator_opted_in=True,
                explicit_supersedes=supersedes_ids,
            )
            if resolution.enabled:
                job_metadata['supersedence_action'] = {
                    'mode_used': resolution.mode_used,
                    'superseded_intune_app_ids': resolution.superseded_intune_app_ids,
                    'demoted_records': [
                        {'entry_id': eid, 'product_version': vv.get('product_version'),
                         'verified_intune_app_id': vv.get('verified_intune_app_id')}
                        for eid, vv in resolution.demoted_records
                    ],
                    'notes': resolution.notes,
                }
                console.print(
                    f"\n[bold]Supersedence:[/bold] mode={resolution.mode_used}, "
                    f"will mark {len(resolution.demoted_records)} prior verified "
                    f"row(s) superseded, {len(resolution.superseded_intune_app_ids)} "
                    f"Intune app id(s) in supersedingApps"
                )
            else:
                console.print("\n[yellow]Supersedence requested but no targets "
                              "found in this line.[/yellow]")
        except installer_catalog.SupersedenceError as exc:
            console.print(f"[bold red]✗[/bold red] {exc}")
            raise click.Abort()

    try:
        result = create_packaging_job.delay(
            job_type=JobType.NEW_SOFTWARE.value,
            software_title=product_name,
            vendor=vendor,
            current_version=current_version,
            metadata=job_metadata,
        )
        console.print(f"\n[bold green]✓[/bold green] EXE job created successfully")
        console.print(f"  Product: {product_name}")
        console.print(f"  Vendor:  {vendor}")
        console.print(f"  Version: {target_version or 'unknown'}")
        console.print(f"  Task ID: {result.id}")
        console.print(f"\nUse 'autopackager jobs list' to check status")

        # Bump catalog use_count. Don't auto-add brand-new EXEs to the
        # overlay -- that creates entries without detection_rules, which
        # this CLI just refused to enqueue. Operator adds the catalog
        # entry first (with rules), then re-runs.
        if not no_save_catalog:
            try:
                installer_catalog.record_use(catalog_entry.id)
                console.print(f"  [dim]Catalog: bumped use_count for '{catalog_entry.id}'[/dim]")
            except Exception as catalog_exc:
                console.print(f"  [yellow]⚠ Catalog update skipped: {catalog_exc}[/yellow]")
    except Exception as e:
        console.print(f"[bold red]✗[/bold red] Failed to create job: {str(e)}")
        raise click.Abort()


@cli.command('inspect-exe')
@click.argument('exe_path', type=click.Path(exists=True, dir_okay=False))
def inspect_exe_command(exe_path):
    """Read a PE binary and preview the catalog lookup fields.

    Shows the VS_VERSIONINFO StringFileInfo fields the catalog uses to
    match an installer (CompanyName + ProductName) plus the SHA-256
    fingerprint. Use the output to fill in a catalog entry's
    pe_company_name / pe_product_name when you're seeding a new EXE.
    """
    from autopackager.utils.pe_metadata import read_pe_metadata, sha256_file, PEParseError
    from autopackager.utils import installer_catalog

    try:
        metadata = read_pe_metadata(exe_path)
    except PEParseError as e:
        console.print(f"[bold red]✗[/bold red] {e}")
        raise click.Abort()

    sha = sha256_file(exe_path)

    table = Table(title=f"PE Metadata: {Path(exe_path).name}")
    table.add_column("Property", style="cyan")
    table.add_column("Value", style="white")
    for label, key in [
        ("Company Name", "company_name"),
        ("Product Name", "product_name"),
        ("Product Version", "product_version"),
        ("File Version", "file_version"),
        ("File Description", "file_description"),
        ("Original Filename", "original_filename"),
        ("Legal Copyright", "legal_copyright"),
    ]:
        val = getattr(metadata, key, '') or 'N/A'
        table.add_row(label, val)
    table.add_row("SHA-256", sha)
    console.print(table)

    catalog = installer_catalog.load_catalog()
    entry = catalog.match_exe(pe_metadata=metadata.to_dict(), sha256=sha, filename=Path(exe_path).name)
    if entry:
        console.print(
            f"\n[bold green]Catalog hit:[/bold green] {entry.id} "
            f"(family: {entry.installer_family or 'unknown'}, "
            f"{len(entry.detection_rules or [])} detection rule(s))"
        )
        console.print(f"  install_command_template: [cyan]{entry.install_command_template}[/cyan]")
    else:
        console.print(
            "\n[yellow]No catalog match.[/yellow] To enable publishing this EXE, "
            "add a catalog entry with type: exe, the company/product fields "
            "above, an installer_family, install_command_template, and at least "
            "one detection rule."
        )


@cli.command('inspect-msi')
@click.argument('msi_path', type=click.Path(exists=True, dir_okay=False))
@click.option('--install-command', help='Optional install command to derive package commands from')
def inspect_msi_command(msi_path, install_command):
    """Read an MSI and preview the package fields the factory would generate."""
    from autopackager.utils.msi_metadata import inspect_msi, MSIParseError

    try:
        result = inspect_msi(msi_path, install_command)
    except MSIParseError as e:
        console.print(f"[bold red]✗[/bold red] {e}")
        raise click.Abort()

    meta = result['metadata']

    table = Table(title=f"MSI Metadata: {Path(msi_path).name}")
    table.add_column("Property", style="cyan")
    table.add_column("Value", style="white")
    for label, key in [
        ("Product Name", "product_name"),
        ("Version", "product_version"),
        ("Publisher", "manufacturer"),
        ("Product Code", "product_code"),
        ("Upgrade Code", "upgrade_code"),
        ("Language", "language"),
        ("Package Code", "package_code"),
    ]:
        table.add_row(label, str(meta.get(key) or 'N/A'))
    console.print(table)

    console.print("\n[bold]Generated package commands:[/bold]")
    console.print(f"  Install:   {result['install_command']}")
    console.print(f"  Uninstall: {result['uninstall_command']}")

    console.print("\n[bold]Intune detection rule:[/bold]")
    rule = result['detection_rule']
    console.print(f"  Type:    {rule['@odata.type']}")
    console.print(f"  Product: {rule['productCode']}")
    console.print(f"  Version: {rule.get('productVersion') or 'N/A'} ({rule['productVersionOperator']})")


@cli.group()
def jobs():
    """Manage packaging jobs"""
    pass


@jobs.command('list')
@click.option('--state', type=click.Choice([s.value for s in JobState]), help='Filter by state')
@click.option('--limit', type=int, default=20, help='Number of jobs to display')
def list_jobs(state, limit):
    """List packaging jobs"""
    engine = OrchestrationEngine()

    if state:
        job_list = engine.get_jobs_by_state(JobState(state), limit=limit)
    else:
        job_list = engine.get_all_jobs(limit=limit)

    if not job_list:
        console.print("[yellow]No jobs found[/yellow]")
        return

    # Create table
    table = Table(title=f"Packaging Jobs (showing {len(job_list)})")
    table.add_column("ID", style="cyan")
    table.add_column("Title", style="white")
    table.add_column("Vendor", style="blue")
    table.add_column("Version", style="green")
    table.add_column("State", style="yellow")
    table.add_column("Created", style="magenta")

    for job in job_list:
        state_color = {
            JobState.COMPLETED: "green",
            JobState.FAILED: "red",
            JobState.PENDING: "yellow",
            JobState.DISCOVERING: "blue",
            JobState.PACKAGING: "blue",
            JobState.TESTING: "blue",
            JobState.DEPLOYING: "blue"
        }.get(job.state, "white")

        table.add_row(
            str(job.id),
            job.software_title,
            job.vendor or "",
            f"{job.current_version or 'N/A'} → {job.target_version or '?'}",
            f"[{state_color}]{job.state.value}[/{state_color}]",
            job.created_at.strftime("%Y-%m-%d %H:%M") if job.created_at else ""
        )

    console.print(table)


@jobs.command('status')
@click.argument('job_id', type=int)
def job_status(job_id):
    """Get detailed status of a job"""
    engine = OrchestrationEngine()
    job = engine.get_job(job_id)

    if not job:
        console.print(f"[bold red]✗[/bold red] Job {job_id} not found")
        return

    console.print(f"\n[bold]Job #{job.id}: {job.software_title}[/bold]")
    console.print(f"  Type: {job.job_type.value}")
    console.print(f"  State: [{job.state.value}]")
    console.print(f"  Vendor: {job.vendor or 'N/A'}")
    console.print(f"  Current Version: {job.current_version or 'N/A'}")
    console.print(f"  Target Version: {job.target_version or 'N/A'}")

    if job.hardware_model:
        console.print(f"  Hardware Model: {job.hardware_model}")

    console.print(f"  Created: {job.created_at}")
    console.print(f"  Updated: {job.updated_at}")

    if job.error_message:
        console.print(f"  [bold red]Error:[/bold red] {job.error_message}")

    # Display deployment ring information
    package_id = job.job_metadata.get('package_id') if job.job_metadata else None
    if package_id:
        _display_deployment_ring_info(package_id)

    if job.job_metadata:
        console.print(f"\n  Metadata:")
        for key, value in job.job_metadata.items():
            console.print(f"    {key}: {value}")


def _display_deployment_ring_info(package_id: int):
    """Display deployment ring and promotion eligibility information"""
    try:
        with db_session_scope() as session:
            # Get all deployments for this package, ordered by created_at descending
            deployments = session.query(Deployment).filter(
                Deployment.package_id == package_id
            ).order_by(Deployment.created_at.desc()).all()

            if not deployments:
                return

            console.print(f"\n  [bold]Deployment Status:[/bold]")

            deployment_agent = DeploymentAgent()

            for deployment in deployments:
                # Detach from session for use outside context
                session.expunge(deployment)

                # Check promotion eligibility
                is_eligible, reason = deployment_agent.is_eligible_for_promotion(deployment)

                # Display ring information
                ring_name = deployment.ring_name or deployment.ring_id
                ring_color = "green" if is_eligible else "yellow"
                console.print(f"    Ring: [{ring_color}]{ring_name}[/{ring_color}]")
                console.print(f"    Status: {deployment.status.value}")

                # Display promotion eligibility
                if is_eligible:
                    console.print(f"    Promotion: [bold green]✓ Eligible[/bold green] - {reason}")
                else:
                    console.print(f"    Promotion: [yellow]✗ Not Eligible[/yellow] - {reason}")

                    # Calculate time until eligible if it's a time-based restriction
                    if deployment.deployed_at and "hours remaining" in reason:
                        # Extract hours remaining from the reason string
                        match = re.search(r'(\d+\.?\d*)\s+hours remaining', reason)
                        if match:
                            hours_remaining = float(match.group(1))
                            days = int(hours_remaining // 24)
                            hours = int(hours_remaining % 24)
                            if days > 0:
                                console.print(f"    Time until eligible: {days}d {hours}h")
                            else:
                                console.print(f"    Time until eligible: {hours}h")

                # Display install statistics
                if deployment.target_device_count:
                    total_installs = deployment.successful_installs + deployment.failed_installs
                    success_rate = (deployment.successful_installs / total_installs * 100) if total_installs > 0 else 0
                    console.print(f"    Installs: {deployment.successful_installs}/{deployment.target_device_count} successful ({success_rate:.1f}%)")

                console.print("")  # Blank line between deployments

    except Exception as e:
        logger = get_logger(__name__)
        logger.error("Failed to retrieve deployment ring info", error=str(e))
        # Silently fail - don't disrupt the job status output


@jobs.command('cancel')
@click.argument('job_id', type=int)
@click.option('--all-stuck', is_flag=True, help='Cancel all non-terminal jobs')
def cancel_job(job_id, all_stuck):
    """Cancel a job (mark as cancelled in the database)"""
    engine = OrchestrationEngine()

    if all_stuck:
        stuck_states = [JobState.PENDING, JobState.DISCOVERING, JobState.PACKAGING,
                        JobState.TESTING, JobState.DEPLOYING]
        cancelled = 0
        for state in stuck_states:
            for job in engine.get_jobs_by_state(state):
                engine.update_job_state(job.id, JobState.CANCELLED)
                console.print(f"  Cancelled job #{job.id}: {job.software_title}")
                cancelled += 1
        console.print(f"[bold green]✓[/bold green] Cancelled {cancelled} job(s)")
    else:
        job = engine.get_job(job_id)
        if not job:
            console.print(f"[bold red]✗[/bold red] Job {job_id} not found")
            return
        engine.update_job_state(job_id, JobState.CANCELLED)
        console.print(f"[bold green]✓[/bold green] Job #{job_id} cancelled")


@jobs.command('rollback')
@click.argument('job_id', type=int)
@click.option('--yes', is_flag=True, help='Skip confirmation prompt')
def rollback_job(job_id, yes):
    """Rollback a failed deployment"""
    engine = OrchestrationEngine()
    job = engine.get_job(job_id)

    if not job:
        console.print(f"[bold red]✗[/bold red] Job {job_id} not found")
        return

    console.print(f"\n[bold]Job #{job.id}: {job.software_title}[/bold]")
    console.print(f"  State: {job.state.value}")
    console.print(f"  Version: {job.target_version or 'N/A'}")

    if not yes:
        click.confirm(f"\nRollback deployment for job #{job_id}?", abort=True)

    console.print(f"\n[bold yellow]⚠[/bold yellow] Rollback functionality not yet implemented")
    console.print(f"This will remove the deployed application from Intune")



@jobs.command('promote')
@click.argument('deployment_id', type=int)
@click.option('--force', is_flag=True, help='Force promotion even if eligibility checks fail')
def promote_deployment(deployment_id, force):
    """Manually promote a deployment to the next ring"""
    console.print(f"[bold blue]Promoting deployment #{deployment_id}...[/bold blue]\n")

    try:
        deployment_agent = DeploymentAgent()

        # Get deployment details first
        with db_session_scope() as session:
            deployment = session.query(Deployment).filter(
                Deployment.id == deployment_id
            ).first()

            if not deployment:
                console.print(f"[bold red]✗[/bold red] Deployment {deployment_id} not found")
                raise click.Abort()

            # Detach from session for use outside context
            session.expunge(deployment)

        # Check eligibility
        is_eligible, reason = deployment_agent.is_eligible_for_promotion(deployment)

        current_ring_name = deployment.ring_name or deployment.ring_id
        console.print(f"  Current Ring: {current_ring_name}")
        console.print(f"  Status: {deployment.status.value}")

        if not is_eligible and not force:
            console.print(f"\n[bold yellow]✗ Not Eligible for Promotion[/bold yellow]")
            console.print(f"  Reason: {reason}")
            console.print(f"\nUse --force to attempt promotion anyway")
            raise click.Abort()
        elif not is_eligible and force:
            console.print(f"\n[bold yellow]⚠ Warning:[/bold yellow] {reason}")
            console.print(f"  Attempting forced promotion...\n")
        else:
            console.print(f"  Eligibility: [bold green]✓ Eligible[/bold green] - {reason}\n")

        # Attempt promotion
        result = deployment_agent.promote_to_next_ring(deployment_id, force=force)

        console.print(f"[bold green]✓[/bold green] Promotion successful!")
        console.print(f"  From: {result['from_ring']}")
        console.print(f"  To: {result['to_ring']}")
        console.print(f"  Package ID: {result['package_id']}")
        console.print(f"  Intune App ID: {result['intune_app_id']}")

    except ValueError as e:
        console.print(f"[bold red]✗[/bold red] Promotion failed: {str(e)}")
        raise click.Abort()
    except Exception as e:
        console.print(f"[bold red]✗[/bold red] Unexpected error: {str(e)}")
        raise click.Abort()


@jobs.command('halt-promotion')
@click.argument('deployment_id', type=int)
@click.option('--reason', required=True, help='Reason for blocking automatic promotion')
def halt_promotion(deployment_id, reason):
    """Block automatic promotion for a deployment"""
    console.print(f"[bold blue]Halting automatic promotion for deployment #{deployment_id}...[/bold blue]\n")

    try:
        # Get deployment details and update promotion_blocked_reason
        with db_session_scope() as session:
            deployment = session.query(Deployment).filter(
                Deployment.id == deployment_id
            ).first()

            if not deployment:
                console.print(f"[bold red]✗[/bold red] Deployment {deployment_id} not found")
                raise click.Abort()

            # Display current deployment info
            ring_name = deployment.ring_name or deployment.ring_id
            console.print(f"  Deployment ID: {deployment_id}")
            console.print(f"  Ring: {ring_name}")
            console.print(f"  Status: {deployment.status.value}")

            # Check if promotion is already blocked
            if deployment.promotion_blocked_reason:
                console.print(f"\n[bold yellow]⚠ Warning:[/bold yellow] Promotion already blocked")
                console.print(f"  Previous reason: {deployment.promotion_blocked_reason}")
                console.print(f"\nUpdating with new reason...")

            # Update promotion_blocked_reason
            deployment.promotion_blocked_reason = reason

        console.print(f"\n[bold green]✓[/bold green] Automatic promotion blocked successfully")
        console.print(f"  Reason: {reason}")
        console.print(f"\nThis deployment will not be automatically promoted until the block is cleared.")

    except Exception as e:
        console.print(f"[bold red]✗[/bold red] Failed to halt promotion: {str(e)}")
        raise click.Abort()


@jobs.command('purge')
@click.option('--state', default=None,
              type=click.Choice([s.value for s in JobState]),
              help='Only delete jobs in this state (default: all states)')
@click.option('--yes', is_flag=True, help='Skip confirmation prompt')
def purge_jobs(state, yes):
    """Delete job records from the database."""
    engine = OrchestrationEngine()

    # Preview count before deleting
    if state:
        preview = len(engine.get_jobs_by_state(JobState(state)))
    else:
        preview = len(engine.get_all_jobs())

    if preview == 0:
        label = f"{state} " if state else ""
        console.print(f"[yellow]No {label}job records to delete[/yellow]")
        return

    label = f"{state} " if state else ""
    if not yes:
        click.confirm(f"Delete all {preview} {label}job(s) from the database?", abort=True)

    deleted = engine.purge_jobs(state)
    console.print(f"[bold green]✓[/bold green] Deleted {deleted} job(s) from the database")


@cli.group()
def worker():
    """Manage Celery workers"""
    pass


@worker.command('purge')
@click.option('--yes', is_flag=True, help='Skip confirmation prompt')
def purge_queue(yes):
    """Purge all pending tasks from the Celery queue"""
    if not yes:
        click.confirm('This will discard all queued tasks. Continue?', abort=True)

    from autopackager.orchestration.celery_app import celery_app
    count = celery_app.control.purge()
    console.print(f"[bold green]✓[/bold green] Purged {count} task(s) from the queue")


@worker.command('start')
@click.option('--concurrency', type=int, default=4, help='Number of concurrent workers')
def start_worker(concurrency):
    """Start Celery worker"""
    console.print(f"[bold blue]Starting Celery worker with {concurrency} concurrent tasks...[/bold blue]")
    console.print(f"Use Ctrl+C to stop")

    import subprocess
    import sys

    # Invoke celery via the current interpreter's -m so this works without
    # the venv being activated (otherwise `celery` is not on PATH and
    # subprocess.run raises WinError 2 / FileNotFoundError on Windows).
    cmd = [
        sys.executable, '-m', 'celery',
        '-A', 'autopackager.orchestration.celery_app',
        'worker',
        '--loglevel=info',
        f'--concurrency={concurrency}'
    ]

    try:
        subprocess.run(cmd, check=True)
    except KeyboardInterrupt:
        console.print("\n[yellow]Worker stopped[/yellow]")
    except Exception as e:
        console.print(f"[bold red]✗[/bold red] Failed to start worker: {str(e)}")


@cli.group()
def logs():
    """Export shareable log views for customers / packager support"""
    pass


@logs.command('export')
@click.option('--minutes', type=int, default=45,
              help='Trailing time window to export (default 45).')
@click.option('--job', 'job_id', type=int, default=None,
              help='Narrow the package-build view to a single job id (for packager support).')
@click.option('--out', 'out_dir', default=None,
              help='Output directory (default data/logs/exports/).')
def logs_export(minutes, job_id, out_dir):
    """Write two views from the running stack's logs: operational/INFO and
    package-build & execution. The standard pattern for sharing logs with a
    customer or pulling a support log when a package build needs investigation.
    """
    from autopackager.utils import log_export
    res = log_export.export(minutes=minutes, job_id=job_id, out_dir=out_dir)
    if res.get('error'):
        console.print(f"[bold red]✗[/bold red] {res['error']}")
        raise click.Abort()
    console.print(f"[bold green]✓[/bold green] Exported logs ({res['from']} → {res['to']})")
    console.print(f"  INFO/operational : {res['info']}  ({res['info_lines']} lines)")
    label = f" (job {job_id})" if job_id is not None else ""
    console.print(f"  package-build{label}: {res['packaging']}  ({res['packaging_lines']} lines)")
    # Point at the robust installer logs (MSI /l*v + EXE/Burn bootstrapper logs)
    # the local-install validator captured per package.
    if job_id is not None:
        from pathlib import Path as _P
        hits = sorted(_P('data/logs').glob(f'installer_{job_id}_*'))
        if hits:
            console.print(f"  installer logs   : data/logs/installer_{job_id}_*  "
                          f"({len(hits)} file(s) — robust MSI/EXE install logs)")


@cli.group()
def catalog():
    """Inspect and export the installer catalog"""
    pass


@catalog.command('export')
@click.option('--output', '-o', 'output',
              default='autopackager/data/installer_catalog.snapshot.yaml',
              help='Where to write the snapshot (default: committable path under autopackager/data/).')
@click.option('--source', type=click.Choice(['merged', 'overlay', 'baseline']),
              default='merged', show_default=True,
              help='merged = baseline+overlay; overlay = only locally-learned entries; baseline = committed baseline.')
def catalog_export(output, source):
    """Copy the catalog RULES to a tenant-agnostic, committable snapshot.

    Strips every tenant-specific field (usage counts, per-tenant version, and
    verified_versions — which carries tenant-bound Intune app GUIDs) so the
    hard-won install/uninstall commands, detection rules, installer families,
    and supersedence capability are preserved without leaking anything true of
    only this tenant.
    """
    from autopackager.utils import installer_catalog

    try:
        summary = installer_catalog.export_catalog_snapshot(output, source=source)
    except Exception as e:  # noqa: BLE001
        console.print(f"[bold red]✗[/bold red] Catalog export failed: {e}")
        raise SystemExit(1)

    console.print(
        f"[bold green]✓[/bold green] Wrote {summary['entry_count']} entr"
        f"{'y' if summary['entry_count'] == 1 else 'ies'} "
        f"([cyan]{summary['msi']}[/cyan] msi · [cyan]{summary['exe']}[/cyan] exe; "
        f"[yellow]{summary['overlay_only']}[/yellow] not in the shipped baseline) "
        f"→ [bold]{summary['output']}[/bold]"
    )
    console.print(
        "  Tenant-specific fields (use_count, version, verified_versions) were stripped. "
        "Review, then promote into autopackager/data/installer_catalog.yaml to ship."
    )


@cli.command()
def validate_azure():
    """Validate Azure/Intune configuration for deployment"""
    console.print("[bold blue]Validating Azure configuration...[/bold blue]\n")

    validator = AzureValidator()
    try:
        results = validator.validate_all()
    except AzureConfigurationError as e:
        results = e.results

    # Display results table
    table = Table(title="Azure Validation Results")
    table.add_column("Check", style="cyan")
    table.add_column("Status", style="white")
    table.add_column("Details", style="white")

    has_failure = False
    for result in results:
        if result.passed:
            status = "[bold green]PASS[/bold green]"
        else:
            status = "[bold red]FAIL[/bold red]"
            has_failure = True

        table.add_row(
            result.check_name,
            status,
            result.details or result.message
        )

    console.print(table)

    if has_failure:
        console.print("\n[bold red]✗[/bold red] One or more validation checks failed.")

        if click.confirm("\nWould you like to see remediation steps?", default=True):
            console.print("\n[bold yellow]Remediation Steps[/bold yellow]")
            console.print("─" * 50)

            console.print("\n[bold]1. Set required environment variables:[/bold]")
            console.print("   AZURE_TENANT_ID     - Your Azure AD tenant ID")
            console.print("   AZURE_CLIENT_ID     - Application (client) ID")
            console.print("   AZURE_CLIENT_SECRET - Application client secret")

            console.print("\n[bold]2. Register an app in Azure Portal:[/bold]")
            console.print("   https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade")

            console.print("\n[bold]3. Or use az CLI commands:[/bold]")
            console.print("   az ad app create --display-name AutoPackager")
            console.print("   az ad app credential reset --id <app-id>")

            console.print("\n[bold]4. Run the setup script:[/bold]")
            console.print("   .\\azure-setup.ps1")

        sys.exit(1)
    else:
        console.print("\n[bold green]✓[/bold green] All Azure validation checks passed.")
        sys.exit(0)


@cli.command()
def version():
    """Show version information"""
    from autopackager import __version__
    console.print(f"[bold]AutoPackager[/bold] version {__version__}")


@cli.command('discover-unmanaged')
@click.option('--source', type=click.Choice(['intune', 'local', 'both']), default='both',
              help='Where to read installed software: intune (Detected Apps, env-wide), '
                   'local (this device ARP), or both. Falls back to local on a 403.')
@click.option('--format', 'fmt', type=click.Choice(['table', 'json', 'csv']), default='table')
@click.option('--out', type=click.Path(dir_okay=False), help='Write the report to this file.')
@click.option('--show-os', is_flag=True, default=False,
              help='Also list the standard OS-component + known-packageable buckets.')
@click.option('--limit', type=int, default=40, help='Max candidate rows to print.')
def discover_unmanaged(source, fmt, out, show_os, limit):
    """Delta of software installed in the environment but NOT packaged in Intune.

    Sorts every installed app into managed / known-packageable / standard-OS /
    unmanaged-candidate / ignored, and surfaces the unmanaged candidates — the
    backlog of apps that should be packaged but aren't.
    """
    import json
    from autopackager.services import software_delta

    graph_client = None
    if source in ('intune', 'both'):
        try:
            from autopackager.utils.graph_client import GraphAPIClient
            graph_client = GraphAPIClient()
        except Exception as exc:  # noqa: BLE001
            console.print(f"[yellow]⚠[/yellow] Graph client unavailable ({exc}); using local ARP.")

    delta = software_delta.build_delta(source=source, graph_client=graph_client)

    if delta['intune_unavailable']:
        console.print(
            "[yellow]⚠ Intune Detected Apps unavailable[/yellow] — the AutoPackager service "
            "principal needs [cyan]DeviceManagementManagedDevices.Read.All[/cyan] (admin "
            "consent). Showing local ARP only.")

    if fmt in ('json', 'csv'):
        if fmt == 'json':
            text = json.dumps(delta, indent=2, default=str)
        else:
            lines = ["bucket,name,publisher,version,device_count,in_catalog,sources"]
            for bucket in ('unmanaged_candidate', 'known_packageable', 'standard_os_component', 'managed'):
                key = {'unmanaged_candidate': 'candidates',
                       'known_packageable': 'known_packageable',
                       'standard_os_component': 'standard_os_components',
                       'managed': 'managed'}[bucket]
                for r in delta.get(key, []):
                    vals = [bucket, r.get('name', ''), r.get('publisher') or '', r.get('version') or '',
                            str(r.get('device_count') or ''), str(r.get('in_catalog') or ''),
                            ';'.join(r.get('sources', []))]
                    lines.append(",".join('"%s"' % str(v).replace('"', "'") for v in vals))
            text = "\n".join(lines)
        if out:
            Path(out).write_text(text, encoding='utf-8')
            console.print(f"[green]✓[/green] Wrote {fmt.upper()} report to {out}")
        else:
            console.print(text)
        return

    c = delta['counts']
    console.print(
        f"\n[bold]Software delta[/bold]  (source={delta['source']}, host={delta['hostname']}, "
        f"installed={delta['total_installed']})")
    console.print(
        f"  managed: {c['managed']}    known-packageable: {c['known_packageable']}    "
        f"standard-OS: {c['standard_os_component']}    store/MSIX: {c.get('store_app', 0)}    "
        f"[bold red]unmanaged candidates: {c['unmanaged_candidate']}[/bold red]    "
        f"ignored: {c['ignored']}")

    table = Table(title="Unmanaged candidates — should be packaged but aren't")
    table.add_column("Name", style="bold")
    table.add_column("Publisher")
    table.add_column("Version")
    table.add_column("Devices", justify="right")
    table.add_column("In catalog?")
    for r in delta['candidates'][:limit]:
        table.add_row(r.get('name', ''), r.get('publisher') or '', r.get('version') or '',
                      str(r.get('device_count') or ''), r.get('in_catalog') or '—')
    console.print(table)
    extra = len(delta['candidates']) - limit
    if extra > 0:
        console.print(f"  [dim]… and {extra} more (raise --limit or --format json).[/dim]")

    if show_os:
        if delta['known_packageable']:
            console.print("\n[bold]Known-packageable[/bold] (in catalog, not yet published): "
                          + ", ".join(r['name'] for r in delta['known_packageable']))
        if delta['standard_os_components']:
            console.print("[dim]Standard OS components: "
                          + ", ".join(r['name'] for r in delta['standard_os_components']) + "[/dim]")

    if out:
        Path(out).write_text(json.dumps(delta, indent=2, default=str), encoding='utf-8')
        console.print(f"\n[green]✓[/green] Wrote full JSON report to {out}")
    for e in delta['errors']:
        console.print(f"[dim]note: {e}[/dim]")


@cli.command('queue-unmanaged')
@click.option('--source', type=click.Choice(['intune', 'local', 'both']), default='both',
              help='Where to read installed software (passed to the delta).')
@click.option('--include-known/--candidates-only', default=False,
              help='Also queue the known-packageable bucket (in-catalog, not yet '
                   'published), not just the unmanaged candidates.')
@click.option('--limit', type=int, default=10,
              help='Max number of items to queue (highest device-count first).')
@click.option('--mode', type=click.Choice(['replay', 'live', 'off']), default=None,
              help='Research-bridge mode for acquisition/packaging (default: server default).')
@click.option('--yes', is_flag=True, default=False, help='Skip the confirmation prompt.')
def queue_unmanaged(source, include_known, limit, mode, yes):
    """Queue unmanaged-software-delta candidates for packaging (gated, test scope).

    Builds the delta, takes the top unmanaged candidates (optionally also the
    known-packageable bucket), acquires an installer for each (catalog URL or the
    research bridge), and runs the gated discovery→packaging→testing pipeline one
    item at a time. Deployment is HELD for the approval gate — nothing is written
    to the tenant by this command. The CLI blocks until the batch finishes.
    """
    from autopackager.services import software_delta
    from demo import queue as pkg_queue

    graph_client = None
    if source in ('intune', 'both'):
        try:
            from autopackager.utils.graph_client import GraphAPIClient
            graph_client = GraphAPIClient()
        except Exception as exc:  # noqa: BLE001
            console.print(f"[yellow]⚠[/yellow] Graph client unavailable ({exc}); using local ARP.")

    delta = software_delta.build_delta(source=source, graph_client=graph_client)
    rows = list(delta.get('candidates', []))
    if include_known:
        rows += list(delta.get('known_packageable', []))
    rows = rows[:max(0, limit)]
    if not rows:
        console.print("[green]Nothing to queue[/green] — no unmanaged candidates found.")
        return

    table = Table(title=f"Queueing {len(rows)} item(s) — gated, Ring 0 (test)")
    table.add_column("Name", style="bold")
    table.add_column("Publisher")
    table.add_column("Version")
    table.add_column("Bucket")
    table.add_column("In catalog?")
    for r in rows:
        table.add_row(r.get('name', ''), r.get('publisher') or '', r.get('version') or '',
                      r.get('bucket') or '', r.get('in_catalog') or '—')
    console.print(table)

    if not yes and not click.confirm(f"Queue these {len(rows)} item(s) for packaging?", default=True):
        console.print("Aborted.")
        return

    import uuid
    batch_id = uuid.uuid4().hex[:12]
    specs = []
    for r in rows:
        candidate = {
            'name': r.get('name'), 'publisher': r.get('publisher'),
            'version': r.get('version'), 'bucket': r.get('bucket'),
            'in_catalog': r.get('in_catalog'), 'device_count': r.get('device_count'),
        }
        job_id = pkg_queue.create_queue_job_row(candidate, batch_id=batch_id)
        specs.append({'job_id': job_id, 'candidate': candidate})
        console.print(f"  [cyan]queued[/cyan] job #{job_id} — {candidate['name']}")

    console.print(f"\n[bold]Processing batch {batch_id}[/bold] (one item at a time)…")
    pkg_queue.run_batch(specs, mode=mode)
    console.print(f"[green]✓[/green] Batch {batch_id} processed. "
                  "Approve individual jobs to deploy to Ring 0.")


@cli.group()
def drivers():
    """Read-only Intune-native driver management (Windows Driver Update Profiles).

    Surfaces the driver-update delta Windows Update computes for the devices a
    profile targets. These commands never create or modify anything — profile
    creation is an interactive Intune-portal step (see `drivers list-profiles`
    output when the tenant has none).
    """
    pass


def _drivers_graph_client():
    """Build a GraphAPIClient or print a friendly error and return None."""
    try:
        from autopackager.utils.graph_client import GraphAPIClient
        return GraphAPIClient()
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]✗[/red] Graph client unavailable: {exc}")
        return None


@drivers.command('list-profiles')
@click.option('--format', 'fmt', type=click.Choice(['table', 'json']), default='table')
def drivers_list_profiles(fmt):
    """List Windows Driver Update Profiles in the tenant."""
    import json
    gc = _drivers_graph_client()
    if gc is None:
        raise click.Abort()

    profs = gc.list_driver_update_profiles().get('value', []) or []
    if fmt == 'json':
        console.print(json.dumps(profs, indent=2, default=str))
        return

    if not profs:
        console.print(
            "\n[yellow]No Windows Driver Update Profiles in the tenant.[/yellow]\n"
            "Create the first one interactively (this also onboards the tenant's "
            "Windows Update for Business service):\n"
            "  [cyan]intune.microsoft.com → Devices → Windows → Manage updates → "
            "Windows Driver updates → Create profile[/cyan]\n"
            "Use [bold]Manual[/bold] approval (inventory only — nothing installs "
            "without your approval), then assign it to a device group.")
        return

    table = Table(title="Windows Driver Update Profiles")
    table.add_column("Name", style="bold")
    table.add_column("Approval")
    table.add_column("Id")
    for p in profs:
        table.add_row(p.get('displayName') or '', p.get('approvalType') or '—', p.get('id') or '')
    console.print(table)


@drivers.command('inventory')
@click.argument('profile', required=False)
@click.option('--format', 'fmt', type=click.Choice(['table', 'json']), default='table')
@click.option('--needs-review', is_flag=True, default=False,
              help='Only show drivers whose approvalStatus is needsReview.')
@click.option('--out', type=click.Path(dir_okay=False), help='Write the full JSON report to this file.')
@click.option('--limit', type=int, default=40, help='Max driver rows to print per profile.')
def drivers_inventory(profile, fmt, needs_review, out, limit):
    """Show the driver-update delta for a profile (or all profiles).

    PROFILE is an optional profile GUID or display name; omit it to report on
    every profile. Each row is a driver for which Windows Update found a newer
    applicable version (the current-vs-available delta).
    """
    import json
    from autopackager.services import driver_inventory

    gc = _drivers_graph_client()
    if gc is None:
        raise click.Abort()

    report = driver_inventory.build_report(gc, profile=profile)

    if out:
        Path(out).write_text(json.dumps(report, indent=2, default=str), encoding='utf-8')
        console.print(f"[green]✓[/green] Wrote full JSON report to {out}")
    if fmt == 'json':
        console.print(json.dumps(report, indent=2, default=str))
        return

    status = report['status']
    if status == 'no_profiles':
        console.print(
            "\n[yellow]No driver inventory to show.[/yellow] "
            "No matching Windows Driver Update Profile exists — run "
            "[cyan]cli.py drivers list-profiles[/cyan] (it explains how to create one).")
        for e in report['errors']:
            console.print(f"[dim]note: {e}[/dim]")
        return

    console.print(
        f"\n[bold]Driver-update delta[/bold]  (profiles={report['profile_count']}, "
        f"drivers={report['total_drivers']}, "
        f"[bold red]needs review: {report['needs_review']}[/bold red])")

    if status == 'pending':
        console.print(
            "[yellow]⏳ Inventory pending.[/yellow] The profile(s) exist but Windows "
            "Update hasn't surfaced drivers yet — first inventory takes ~1-2 days after "
            "assignment, and only devices with diagnostic data (telemetry) enabled "
            "report drivers.")

    for p in report['profiles']:
        groups = ', '.join(p['assigned_group_ids']) or '—'
        console.print(
            f"\n[bold cyan]{p['display_name']}[/bold cyan]  "
            f"(approval={p['approval_type'] or '—'}, drivers={p['driver_count']}, "
            f"groups={groups})")
        if p['pending']:
            console.print("  [dim]no drivers inventoried yet[/dim]")
            continue
        if p['by_class']:
            console.print("  [dim]by class: "
                          + ", ".join(f"{k}={v}" for k, v in p['by_class'].items()) + "[/dim]")

        rows = p['drivers']
        if needs_review:
            rows = [d for d in rows if d['approval_status'] == 'needsReview']
            if not rows:
                console.print("  [dim](no needs-review drivers)[/dim]")
                continue

        table = Table()
        table.add_column("Driver", style="bold")
        table.add_column("Manufacturer")
        table.add_column("Class")
        table.add_column("Version")
        table.add_column("Category")
        table.add_column("Approval")
        table.add_column("Devices", justify="right")
        for d in rows[:limit]:
            approval = d['approval_status']
            approval_disp = f"[red]{approval}[/red]" if approval == 'needsReview' else approval
            table.add_row(
                d['name'], d['manufacturer'], d['driver_class'], d['version'],
                d['category'], approval_disp, str(d['applicable_device_count']))
        console.print(table)
        extra = len(rows) - limit
        if extra > 0:
            console.print(f"  [dim]… and {extra} more (raise --limit or --format json).[/dim]")

    for e in report['errors']:
        console.print(f"[dim]note: {e}[/dim]")


if __name__ == '__main__':
    cli()
