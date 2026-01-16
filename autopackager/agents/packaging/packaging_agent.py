"""Packaging Agent - Create .intunewin Packages"""

import os
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
        download_url = job.metadata.get('download_url') or job.download_url

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

        # Create .intunewin package
        intunewin_path = self._create_intunewin_package(package_dir, installer_dest)

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
        """Download installer from URL"""
        logger.info("Downloading installer", url=url)

        # Extract filename from URL
        filename = url.split('/')[-1]
        if not filename or '.' not in filename:
            # Generate filename based on job info
            ext = self._guess_file_extension(url)
            filename = f"{job.software_title.replace(' ', '_')}_{job.metadata.get('target_version', 'latest')}{ext}"

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
        version = job.metadata.get('target_version', 'unknown')
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        return f"{safe_title}_{version}_{timestamp}"

    def _generate_install_commands(self, job: Job, installer_path: Path) -> tuple:
        """Generate install and uninstall commands"""
        filename = installer_path.name.lower()

        if filename.endswith('.exe'):
            # Common silent install parameters for EXE
            install_cmd = f"{installer_path.name} /S /quiet /norestart"
            uninstall_cmd = None  # Will be determined from registry
        elif filename.endswith('.msi'):
            # MSI silent install
            install_cmd = f"msiexec /i {installer_path.name} /quiet /norestart"
            uninstall_cmd = f"msiexec /x {installer_path.name} /quiet /norestart"
        else:
            # Generic
            install_cmd = str(installer_path.name)
            uninstall_cmd = None

        logger.info("Generated install command", install_cmd=install_cmd)

        return install_cmd, uninstall_cmd

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
        """Generate detection rules for Intune"""
        # For drivers, use file version detection
        # For software, use registry or file path detection

        detection_rules = []

        # Simple file-based detection for now
        detection_rules.append({
            'type': 'file',
            'path': 'C:\\Windows\\System32\\drivers',  # Example for drivers
            'file': f"{job.software_title.replace(' ', '_')}.sys",
            'detectionType': 'exists',
            'check32BitOn64System': False
        })

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
        with db_session_scope() as session:
            package = Package(
                name=job.software_title,
                version=job.metadata.get('target_version', 'unknown'),
                vendor=job.vendor,
                intunewin_path=str(intunewin_path),
                installer_path=str(installer_path),
                install_command=install_cmd,
                uninstall_command=uninstall_cmd,
                detection_rules=detection_rules,
                metadata={
                    'job_id': job.id,
                    'download_url': job.metadata.get('download_url'),
                    'release_notes': job.metadata.get('release_notes')
                }
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
