"""Packaging Agent - Create .intunewin Packages"""

import os
import shutil
import requests
import hashlib
import subprocess
from pathlib import Path
from typing import Dict, Any
from datetime import datetime

from autopackager.models.job import Job
from autopackager.models.package import Package
from autopackager.utils.config import get_config
from autopackager.utils.database import db_session_scope
from autopackager.utils.logger import get_logger
from autopackager.utils.msi_metadata import (
    parse_install_command,
    build_uninstall_command,
    build_product_code_detection_rule,
    resolve_local_path,
)

logger = get_logger(__name__)


class PackagingAgent:
    """Agent responsible for downloading and packaging software/drivers"""

    def __init__(self):
        self.config = get_config()
        self.downloads_path = Path(self.config['paths']['downloads'])
        self.packages_path = Path(self.config['paths']['packages'])
        self.intunewin_util = Path(self.config['paths']['intunewin_util'])

        # Create directories if they don't exist
        self.downloads_path.mkdir(parents=True, exist_ok=True)
        self.packages_path.mkdir(parents=True, exist_ok=True)

    def package(self, job: Job) -> Dict[str, Any]:
        """
        Main packaging method - downloads software/driver and creates .intunewin package
        """
        logger.info(
            "Starting packaging",
            job_id=job.id,
            software_title=job.software_title
        )

        # Get download URL from job metadata
        download_url = job.job_metadata.get('download_url') or job.download_url

        if not download_url:
            raise ValueError("No download URL provided")

        # Download the installer
        installer_path = self._download_installer(download_url, job)

        # Create package directory
        package_name = self._create_package_name(job)
        package_dir = self.packages_path / package_name
        package_dir.mkdir(parents=True, exist_ok=True)

        # Move installer to package directory
        installer_dest = package_dir / installer_path.name
        installer_path.rename(installer_dest)

        # Generate installation commands
        install_cmd, uninstall_cmd = self._generate_install_commands(job, installer_dest)

        # Wrapper packages bundle additional installers (e.g. Wireshark + the
        # Npcap capture driver) behind a generated install.cmd, so a single Win32
        # app delivers every piece. The .intunewin setup file becomes the script
        # and the install/uninstall commands invoke it.
        setup_file = installer_dest
        catalog_entry = self._catalog_entry_for_job(job)
        if catalog_entry and catalog_entry.is_wrapper:
            setup_file, install_cmd, uninstall_cmd = self._stage_wrapper_components(
                job, package_dir, installer_dest, install_cmd, uninstall_cmd, catalog_entry
            )

        # Create .intunewin package
        intunewin_path = self._create_intunewin_package(package_dir, setup_file)

        # Generate detection rules
        detection_rules = self._generate_detection_rules(job)

        # Save package to database
        package = self._save_package(
            job,
            intunewin_path,
            installer_dest,
            install_cmd,
            uninstall_cmd,
            detection_rules
        )

        logger.info("Packaging completed", job_id=job.id, package_id=package.id)

        return {
            'package_id': package.id,
            'intunewin_path': str(intunewin_path),
            'install_command': install_cmd,
            'uninstall_command': uninstall_cmd
        }

    def _download_installer(self, url: str, job: Job) -> Path:
        """Obtain the installer locally — copy a local file or download a URL."""
        # Local files (admin-provided MSI path or file:// URI) are copied in
        # rather than fetched over HTTP.
        local_source = resolve_local_path(url)
        if local_source is not None:
            return self._copy_local_installer(local_source)

        logger.info("Downloading installer", url=url)

        # Extract filename from URL
        filename = url.split('/')[-1]
        if not filename or '.' not in filename:
            # Generate filename based on job info
            ext = self._guess_file_extension(url)
            filename = f"{job.software_title.replace(' ', '_')}_{job.job_metadata.get('target_version', 'latest')}{ext}"

        download_path = self.downloads_path / filename

        # Download with progress
        response = requests.get(url, stream=True, timeout=300)
        response.raise_for_status()

        total_size = int(response.headers.get('content-length', 0))
        logger.info("Downloading", filename=filename, size_mb=total_size / (1024 * 1024))

        with open(download_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

        # Verify download
        actual_size = download_path.stat().st_size
        if total_size > 0 and actual_size != total_size:
            raise Exception(f"Download incomplete: {actual_size} / {total_size} bytes")

        # Calculate hash for verification
        file_hash = self._calculate_file_hash(download_path)
        logger.info("Download complete", filename=filename, sha256=file_hash[:16])

        return download_path

    def _copy_local_installer(self, source: Path) -> Path:
        """Copy an admin-provided local installer into the downloads directory."""
        if not source.exists():
            raise FileNotFoundError(f"Installer not found: {source}")

        logger.info("Using local installer", source=str(source))
        download_path = self.downloads_path / source.name
        shutil.copy2(source, download_path)

        file_hash = self._calculate_file_hash(download_path)
        logger.info("Local installer staged", filename=source.name, sha256=file_hash[:16])

        return download_path

    def _calculate_file_hash(self, file_path: Path) -> str:
        """Calculate SHA256 hash of file"""
        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()

    def _guess_file_extension(self, url: str) -> str:
        """Guess file extension from URL"""
        # Common installer extensions
        if 'exe' in url.lower():
            return '.exe'
        elif 'msi' in url.lower():
            return '.msi'
        elif 'cab' in url.lower():
            return '.cab'
        else:
            return '.exe'  # Default

    def _create_package_name(self, job: Job) -> str:
        """Create package directory name"""
        safe_title = job.software_title.replace(' ', '_').replace('/', '_')
        version = job.job_metadata.get('target_version', 'unknown')
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        return f"{safe_title}_{version}_{timestamp}"

    def _catalog_entry_for_job(self, job: Job):
        """Resolve the catalog entry referenced by an EXE software job.

        CLI's _create_exe_software_job stores catalog_entry_id in
        job_metadata after a successful catalog match. Packaging looks it
        up again here rather than at job-creation time so the entry's
        detection_rules / installer_family etc. are always picked up fresh
        (operator may have edited the catalog between create and publish).
        Returns None on miss; caller decides whether that's fatal.
        """
        catalog_entry_id = (job.job_metadata or {}).get('catalog_entry_id')
        if not catalog_entry_id:
            return None
        try:
            from autopackager.utils.installer_catalog import load_catalog
            catalog = load_catalog()
            return catalog.by_id(catalog_entry_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Catalog lookup failed", job_id=job.id, error=str(exc))
            return None

    def _generate_install_commands(self, job: Job, installer_path: Path) -> tuple:
        """Generate install and uninstall commands based on installer type.

        For CAB driver packs the bare filename is not executable — we need to
        expand the CAB and stage the drivers via ``pnputil``.  Per the
        ch11 reference, PowerShell wrapped via the full 64-bit path is the
        recommended approach for Intune Win32 apps.
        """
        filename = installer_path.name.lower()

        if filename.endswith('.exe'):
            install_cmd, uninstall_cmd = self._generate_exe_commands(job, installer_path)
        elif filename.endswith('.msi'):
            install_cmd, uninstall_cmd = self._generate_msi_commands(job, installer_path)
        elif filename.endswith('.cab'):
            # CAB driver pack: expand, then install drivers via pnputil.
            # Generate a companion PowerShell install script so the Intune
            # install command is a single script invocation.
            script_name = self._generate_cab_install_script(installer_path)
            install_cmd = (
                r"%SystemRoot%\System32\WindowsPowerShell\v1.0\PowerShell.exe "
                f"-ExecutionPolicy Bypass -NoProfile -File {script_name}"
            )
            uninstall_cmd = "cmd /c exit 0"
        else:
            # Unknown type — use filename directly as best-effort
            install_cmd = str(installer_path.name)
            uninstall_cmd = "cmd /c exit 0"

        logger.info("Generated install command", install_cmd=install_cmd)

        return install_cmd, uninstall_cmd

    def _generate_exe_commands(self, job: Job, installer_path: Path) -> tuple:
        """Build EXE install/uninstall commands.

        Source priority:
          1. Admin-supplied install_command in job_metadata (override)
          2. Catalog entry's install_command_template (rendered against the
             actual installer filename)
          3. Family default switches from INSTALLER_FAMILY_SWITCHES, applied
             to the bare installer filename
          4. Last-resort: filename + ' /S' (NSIS default)

        Uninstall: catalog uninstall_command_template wins; otherwise we
        leave a no-op (cmd /c exit 0) and warn -- EXE uninstall is
        installer-specific (NSIS expects 'uninstall.exe /S', Inno Setup
        expects unins000.exe in INSTALLDIR, etc.) and there's no safe
        generic fallback.
        """
        from autopackager.utils.installer_catalog import default_silent_switches

        metadata = job.job_metadata or {}
        user_command = metadata.get('install_command')
        catalog_entry = self._catalog_entry_for_job(job)

        if user_command:
            install_cmd = user_command
        elif catalog_entry and catalog_entry.install_command_template:
            install_cmd = catalog_entry.render_install_command(installer_path.name)
        else:
            family = catalog_entry.installer_family if catalog_entry else None
            switches = default_silent_switches(family) or '/S'
            install_cmd = f"{installer_path.name} {switches}".strip()

        uninstall_cmd = "cmd /c exit 0"
        if catalog_entry and catalog_entry.uninstall_command_template:
            uninstall_cmd = catalog_entry.render_uninstall_command(installer_path.name) or uninstall_cmd
        else:
            logger.warning(
                "EXE has no catalog uninstall_command_template; publishing with no-op uninstall. "
                "Intune will report uninstall success but the app stays installed.",
                job_id=job.id,
            )

        return install_cmd, uninstall_cmd

    def _generate_msi_commands(self, job: Job, installer_path: Path) -> tuple:
        """Build MSI install/uninstall commands.

        Honors an admin-supplied ``install_command`` (preserving its switches and
        public properties) and prefers ``msiexec /x {ProductCode}`` for
        uninstall when the product code is known from the MSI metadata.
        """
        metadata = job.job_metadata or {}
        msi_meta = metadata.get('msi_metadata') or {}
        product_code = msi_meta.get('product_code')
        user_command = metadata.get('install_command')

        if user_command:
            parsed = parse_install_command(user_command)
            install_cmd = parsed.rebuild(installer_path.name)
            uninstall_switches = parsed.switches or None
        else:
            install_cmd = f"msiexec /i {installer_path.name} /quiet /norestart"
            uninstall_switches = None

        if product_code:
            uninstall_cmd = build_uninstall_command(
                product_code, uninstall_switches, installer_path.name
            )
        elif user_command:
            uninstall_cmd = build_uninstall_command(
                '', uninstall_switches, installer_path.name
            )
        else:
            uninstall_cmd = f"msiexec /x {installer_path.name} /quiet /norestart"

        return install_cmd, uninstall_cmd

    def _generate_cab_install_script(self, installer_path: Path) -> str:
        """Create a PowerShell script that expands a CAB driver pack and
        installs drivers via ``pnputil``.

        The script is placed alongside the CAB in the package source folder so
        IntuneWinAppUtil bundles it into the .intunewin file.

        Returns the script filename (not the full path) for use as the Intune
        install command.
        """
        cab_name = installer_path.name
        script_name = "Install-DriverPack.ps1"
        script_path = installer_path.parent / script_name

        script_content = f"""\
# Install-DriverPack.ps1
# Auto-generated driver pack installer for {cab_name}
# Expands the CAB and stages drivers via pnputil.

$ErrorActionPreference = 'Stop'

$cabFile  = Join-Path $PSScriptRoot '{cab_name}'
$expandDir = Join-Path $env:TEMP 'DriverPackExpand'

# Clean previous expansion if present
if (Test-Path $expandDir) {{
    Remove-Item $expandDir -Recurse -Force
}}
New-Item -ItemType Directory -Path $expandDir -Force | Out-Null

# Expand CAB contents
Write-Output "Expanding $cabFile to $expandDir ..."
expand.exe $cabFile -F:* $expandDir
if ($LASTEXITCODE -ne 0) {{
    Write-Error "expand.exe failed with exit code $LASTEXITCODE"
    exit $LASTEXITCODE
}}

# Install drivers — pnputil stages all INF files found under the expanded tree
Write-Output 'Installing drivers via pnputil ...'
pnputil.exe /add-driver "$expandDir\\*.inf" /subdirs /install
$pnpExit = $LASTEXITCODE

# Clean up
Remove-Item $expandDir -Recurse -Force -ErrorAction SilentlyContinue

if ($pnpExit -ne 0) {{
    Write-Error "pnputil.exe failed with exit code $pnpExit"
    exit $pnpExit
}}

Write-Output 'Driver pack installed successfully.'
exit 0
"""
        script_path.write_text(script_content, encoding='utf-8')
        logger.info("Generated CAB install script", script=script_name, cab=cab_name)

        return script_name

    def _wrapper_component_search_dirs(self) -> list:
        """Directories searched for an operator-supplied wrapper component file
        (e.g. a licensed Npcap OEM installer with no public download)."""
        data_root = self.downloads_path.parent
        return [
            data_root / 'wrapper_components',
            data_root / 'test_msis',
            self.downloads_path,
        ]

    def _resolve_component_file(self, component: dict, package_dir: Path) -> Path:
        """Locate (download or find) one wrapper component's installer and copy
        it into the package source folder. Raises ValueError (escalation) when a
        required component cannot be found -- never publish a half-complete app.
        """
        cid = component.get('id', '<unnamed>')
        source = component.get('source')
        acquisition = component.get('acquisition') or ('url' if source else 'operator_supplied')

        local_src = None
        if acquisition == 'url' and source:
            if str(source).lower().startswith(('http://', 'https://')):
                # download directly (job-less): stream to the downloads dir
                filename = str(source).split('/')[-1] or f'{cid}.exe'
                dest = self.downloads_path / filename
                resp = requests.get(source, stream=True, timeout=300)
                resp.raise_for_status()
                with open(dest, 'wb') as fh:
                    for chunk in resp.iter_content(chunk_size=8192):
                        fh.write(chunk)
                local_src = dest
            else:
                cand = Path(source)
                if cand.exists():
                    local_src = cand
        if local_src is None:
            # operator_supplied (or url miss): search known dirs by filename_hint
            hint = (component.get('filename_hint') or cid).lower()
            for d in self._wrapper_component_search_dirs():
                if not d.exists():
                    continue
                for f in sorted(d.iterdir()):
                    if f.is_file() and hint in f.name.lower():
                        local_src = f
                        break
                if local_src is not None:
                    break

        if local_src is None:
            lic = component.get('license_note')
            raise ValueError(
                f"Wrapper component '{cid}' installer not found -- cannot build a "
                f"complete package. Supply the installer (filename containing "
                f"'{component.get('filename_hint') or cid}') in data/wrapper_components/."
                + (f" LICENSE: {lic}" if lic else "")
            )

        dest = package_dir / local_src.name
        if local_src.resolve() != dest.resolve():
            shutil.copy2(local_src, dest)
        logger.info("Staged wrapper component", component=cid, file=dest.name)
        return dest

    def _stage_wrapper_components(
        self, job, package_dir: Path, primary_installer: Path,
        primary_install_cmd: str, primary_uninstall_cmd: str, catalog_entry,
    ) -> tuple:
        """Stage every extra_component installer into the package folder and
        generate install.cmd / uninstall.cmd that run the primary then each
        component silently (from the package root via %~dp0). Returns
        (setup_file_path, install_command, uninstall_command) for the Win32 app.
        Detection is merged separately in _generate_detection_rules (Intune ANDs
        all rules, so the app is 'installed' only when every piece is present).
        """
        steps = [(primary_installer.name, primary_install_cmd, primary_uninstall_cmd)]
        for comp in catalog_entry.extra_components or []:
            required = comp.get('required', True)
            try:
                comp_file = self._resolve_component_file(comp, package_dir)
            except ValueError:
                if required:
                    raise
                logger.warning("Optional wrapper component missing -- skipping",
                               component=comp.get('id'))
                continue
            ci = (comp.get('install_command_template') or '').format(installer_filename=comp_file.name)
            cu = comp.get('uninstall_command_template') or ''
            if cu:
                cu = cu.format(installer_filename=comp_file.name) if '{installer_filename}' in cu else cu
            steps.append((comp_file.name, ci.strip(), cu.strip()))

        install_script = self._render_wrapper_script(
            [(name, ic) for name, ic, _ in steps], kind='install'
        )
        # Uninstall in REVERSE order (components first, then the primary).
        uninstall_steps = [(name, uc) for name, _, uc in reversed(steps) if uc]
        uninstall_script = self._render_wrapper_script(uninstall_steps, kind='uninstall')

        # newline='' so the explicit CRLF in the rendered script is NOT doubled to
        # CR CR LF by Windows text-mode translation (which corrupts cmd parsing).
        (package_dir / 'install.cmd').write_text(install_script, encoding='ascii', errors='replace', newline='')
        (package_dir / 'uninstall.cmd').write_text(uninstall_script, encoding='ascii', errors='replace', newline='')
        logger.info("Generated wrapper scripts", components=len(steps) - 1, entry=catalog_entry.id)

        # `.\` makes the script reference explicit so the IME / validator can run
        # it even when the cwd is not searched (NoDefaultCurrentDirectoryInExePath).
        return package_dir / 'install.cmd', r'cmd /c .\install.cmd', r'cmd /c .\uninstall.cmd'

    @staticmethod
    def _render_wrapper_script(steps: list, kind: str) -> str:
        """Build an install.cmd / uninstall.cmd that runs each (filename, command)
        from the package root, in order. cmd waits for each directly-invoked
        program to finish (GUI installers like NSIS/Inno included — cmd blocks on
        a directly-run child regardless of subsystem; only ``start`` without
        ``/wait`` would race ahead, so we deliberately do NOT use ``start``).
        Success codes 0 / 3010 (soft reboot) / 1641 (hard reboot) pass; any other
        non-zero aborts with that code so a failed component fails the whole app.
        """
        lines = [
            '@echo off',
            'setlocal EnableExtensions',
            'cd /d "%~dp0"',
            # Put the package root on PATH so bare installer-exe names resolve even
            # when NoDefaultCurrentDirectoryInExePath is set (common on hardened /
            # Intune-managed endpoints, where cmd does NOT search the cwd for
            # executables). msiexec is on the system PATH and resolves its .msi
            # argument relative to the cwd, so this covers both EXE and MSI steps.
            'set "PATH=%~dp0;%PATH%"',
            f'rem Auto-generated wrapper {kind} script (AutoPackager)',
            '',
        ]
        for name, cmd in steps:
            if not cmd:
                continue
            lines.append(f'echo [AutoPackager] {kind}: {name}')
            lines.append(cmd)
            lines.append('set "RC=%ERRORLEVEL%"')
            lines.append('if not "%RC%"=="0" if not "%RC%"=="3010" if not "%RC%"=="1641" '
                         f'( echo [AutoPackager] {kind} step "{name}" failed RC=%RC% ^& exit /b %RC% )')
            lines.append('')
        lines.append('exit /b 0')
        return '\r\n'.join(lines) + '\r\n'

    def _create_intunewin_package(self, package_dir: Path, installer_path: Path) -> Path:
        """Create .intunewin package using IntuneWinAppUtil.exe"""
        logger.info("Creating .intunewin package")

        output_dir = package_dir / "output"
        output_dir.mkdir(exist_ok=True)

        # Check if IntuneWinAppUtil.exe exists
        if not self.intunewin_util.exists():
            logger.warning(
                "IntuneWinAppUtil.exe not found - package creation will be simulated",
                expected_path=str(self.intunewin_util)
            )
            # For development, create a placeholder .intunewin file
            intunewin_path = output_dir / f"{installer_path.stem}.intunewin"
            intunewin_path.touch()
            return intunewin_path

        # Run IntuneWinAppUtil.exe
        # Usage: IntuneWinAppUtil.exe -c <source_folder> -s <setup_file> -o <output_folder> -q
        try:
            result = subprocess.run(
                [
                    str(self.intunewin_util),
                    '-c', str(package_dir),
                    '-s', installer_path.name,
                    '-o', str(output_dir),
                    '-q'  # Quiet mode
                ],
                capture_output=True,
                text=True,
                check=True
            )

            logger.info("IntuneWin package created", output=result.stdout)

        except subprocess.CalledProcessError as e:
            logger.error("Failed to create .intunewin package", error=e.stderr)
            raise

        # Find the created .intunewin file
        intunewin_files = list(output_dir.glob('*.intunewin'))
        if not intunewin_files:
            raise Exception("No .intunewin file created")

        return intunewin_files[0]

    def _generate_detection_rules(self, job: Job) -> list:
        """Generate detection rules for Intune (Graph API v1.0 schema).

        Graph API v1.0 uses ``rules`` with ``win32LobAppRegistryRule``
        (not the beta ``detectionRules`` / ``win32LobAppRegistryDetection``).

        Source priority:
          1. MSI ProductCode rule when MSI metadata supplies a product code.
             Far more reliable than any synthetic key.
          2. Catalog detection_rules converted via detection_rule_to_graph.
             REQUIRED for EXE jobs -- the CLI rejects EXE jobs without
             catalog entries, but this is a safety net for direct queue
             insertion.
          3. Synthetic registry detection (legacy driver path). Kept for
             the OEM driver workflow where there's no MSI metadata and no
             catalog entry; emits a placeholder rule the operator can fix
             via the portal.
        """
        from autopackager.utils.installer_catalog import detection_rule_to_graph

        msi_meta = (job.job_metadata or {}).get('msi_metadata') or {}
        product_code = msi_meta.get('product_code')
        if product_code:
            return [build_product_code_detection_rule(
                product_code, msi_meta.get('product_version', '')
            )]

        catalog_entry = self._catalog_entry_for_job(job)
        if catalog_entry and (catalog_entry.detection_rules or catalog_entry.is_wrapper):
            # Collect the primary entry's rules, then (for a wrapper package) every
            # component's rules. Intune requires ALL detection rules to pass, so the
            # app reports "installed" only when the primary AND every bundled
            # component (e.g. Wireshark AND the Npcap driver) are present.
            raw_rules = list(catalog_entry.detection_rules or [])
            if catalog_entry.is_wrapper:
                for comp in catalog_entry.extra_components or []:
                    raw_rules.extend(comp.get('detection_rules') or [])
            converted = []
            for raw_rule in raw_rules:
                try:
                    converted.append(detection_rule_to_graph(raw_rule))
                except ValueError as exc:
                    logger.warning(
                        "Skipping malformed catalog detection rule",
                        catalog_entry=catalog_entry.id,
                        error=str(exc),
                    )
            if converted:
                return converted

        target_version = job.job_metadata.get('target_version', '')
        vendor = (job.vendor or 'Unknown').capitalize()

        detection_rules = [
            {
                '@odata.type': '#microsoft.graph.win32LobAppRegistryRule',
                'ruleType': 'detection',
                'check32BitOn64System': False,
                'keyPath': f'HKEY_LOCAL_MACHINE\\SOFTWARE\\{vendor}\\UpdatePackage\\Log',
                'valueName': target_version,
                'operationType': 'exists',
                'operator': 'notConfigured',
                'comparisonValue': None,
            }
        ]

        return detection_rules

    def _save_package(
        self,
        job: Job,
        intunewin_path: Path,
        installer_path: Path,
        install_cmd: str,
        uninstall_cmd: str,
        detection_rules: list
    ) -> Package:
        """Save package information to database"""
        msi_meta = (job.job_metadata or {}).get('msi_metadata') or {}
        package_metadata = {
            'job_id': job.id,
            'download_url': job.job_metadata.get('download_url'),
            'release_notes': job.job_metadata.get('release_notes')
        }
        if msi_meta:
            package_metadata['msi_product_code'] = msi_meta.get('product_code')
            package_metadata['msi_upgrade_code'] = msi_meta.get('upgrade_code')
            package_metadata['msi_product_version'] = msi_meta.get('product_version')
            # Additional MSI fields used by DeploymentAgent to populate
            # informationUrl / notes / largeIcon in the Intune app payload.
            # These come straight from the MSI's own Property table so a
            # newly-packaged app shows up in Intune with the vendor's help
            # link, a short description, and an icon -- without operator
            # configuration. The catalog can still override any of these.
            all_props = msi_meta.get('all_properties') or {}
            help_link = all_props.get('ARPHELPLINK') or all_props.get('ARPURLINFOABOUT')
            if help_link:
                package_metadata['msi_help_link'] = help_link
            subject = msi_meta.get('subject') or all_props.get('ARPCOMMENTS')
            if subject:
                package_metadata['msi_subject'] = subject
            # Extract the MSI's product icon (when it ships as a usable image
            # rather than a PE with embedded icon resources). Stored base64 in
            # the package row so DeploymentAgent doesn't have to re-read the
            # MSI at publish time.
            try:
                from autopackager.utils.msi_metadata import read_msi_icon
                icon_result = read_msi_icon(installer_path)
                if icon_result:
                    import base64
                    mime, blob = icon_result
                    package_metadata['msi_icon_mime'] = mime
                    package_metadata['msi_icon_b64'] = base64.b64encode(blob).decode('ascii')
            except Exception as icon_exc:  # noqa: BLE001 -- icon extraction is opportunistic
                logger.debug("MSI icon extraction skipped", error=str(icon_exc))

        # EXE branch: store the PE metadata + catalog reference so
        # DeploymentAgent can tell EXE from MSI (skips msiInformation,
        # sources detection rules from the catalog entry referenced here).
        exe_meta = (job.job_metadata or {}).get('exe_metadata') or {}
        if exe_meta and not msi_meta:
            package_metadata['exe_product_name'] = exe_meta.get('product_name')
            package_metadata['exe_company_name'] = exe_meta.get('company_name')
            package_metadata['exe_product_version'] = exe_meta.get('product_version')
            package_metadata['exe_file_version'] = exe_meta.get('file_version')
            sha = (job.job_metadata or {}).get('sha256')
            if sha:
                package_metadata['sha256'] = sha
            catalog_id = (job.job_metadata or {}).get('catalog_entry_id')
            if catalog_id:
                package_metadata['catalog_entry_id'] = catalog_id

        with db_session_scope() as session:
            package = Package(
                name=job.software_title,
                version=job.job_metadata.get('target_version', 'unknown'),
                vendor=job.vendor,
                intunewin_path=str(intunewin_path),
                installer_path=str(installer_path),
                install_command=install_cmd,
                uninstall_command=uninstall_cmd,
                detection_rules=detection_rules,
                package_metadata=package_metadata
            )

            session.add(package)
            session.flush()

            package_id = package.id

        return self._get_package(package_id)

    def _get_package(self, package_id: int) -> Package:
        """Get package by ID"""
        with db_session_scope() as session:
            package = session.query(Package).filter(Package.id == package_id).first()
            if package:
                session.expunge(package)
            return package
