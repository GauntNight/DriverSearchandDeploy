"""Discovery Agent - Find New Software/Driver Versions"""

import os
import requests
import xmltodict
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional

from autopackager.models.job import Job, JobType
from autopackager.utils.config import get_config
from autopackager.utils.logger import get_logger

logger = get_logger(__name__)


class DiscoveryAgent:
    """Agent responsible for discovering new software and driver versions"""

    def __init__(self):
        self.config = get_config()
        self.oem_catalogs = self.config['oem_catalogs']

    def discover(self, job: Job) -> Dict[str, Any]:
        """
        Main discovery method - routes to appropriate discovery strategy
        based on job type and vendor
        """
        logger.info(
            "Starting discovery",
            job_id=job.id,
            software_title=job.software_title,
            vendor=job.vendor
        )

        if job.job_type == JobType.DRIVER_UPDATE:
            return self._discover_driver(job)
        else:
            return self._discover_software(job)

    def _discover_driver(self, job: Job) -> Dict[str, Any]:
        """Discover driver updates from OEM catalogs"""
        vendor = job.vendor.lower()

        if vendor == 'dell':
            return self._discover_dell_driver(job)
        elif vendor == 'hp':
            return self._discover_hp_driver(job)
        elif vendor == 'lenovo':
            return self._discover_lenovo_driver(job)
        else:
            raise ValueError(f"Unsupported OEM vendor: {vendor}")

    def _discover_dell_driver(self, job: Job) -> Dict[str, Any]:
        """Discover Dell driver updates from DriverPackCatalog.cab"""
        logger.info("Discovering Dell drivers", job_id=job.id, model=job.hardware_model)

        # Download and parse Dell catalog
        catalog_config = self.oem_catalogs['dell']
        catalog_xml = self._download_dell_catalog(catalog_config)

        # Parse catalog
        catalog_data = xmltodict.parse(catalog_xml)

        # Find matching driver pack for the model
        driver_pack = self._find_dell_driver_pack(
            catalog_data,
            job.hardware_model,
            job.driver_type
        )

        if driver_pack:
            latest_version = driver_pack.get('dellVersion', 'Unknown')
            download_url = driver_pack.get('path', '')

            # Compare versions
            update_available = self._compare_versions(job.current_version, latest_version)

            return {
                'update_available': update_available,
                'latest_version': latest_version,
                'download_url': download_url,
                'release_notes': driver_pack.get('releaseNotes', ''),
                'release_date': driver_pack.get('dateTime', ''),
                'file_size': driver_pack.get('size', 0)
            }
        else:
            logger.warning("No driver pack found", model=job.hardware_model)
            return {'update_available': False}

    def _download_dell_catalog(self, catalog_config: Dict) -> str:
        """Download Dell driver catalog CAB file and extract XML"""
        catalog_url = catalog_config['catalog_url']
        catalog_path = Path(catalog_config['catalog_path'])
        catalog_path.mkdir(parents=True, exist_ok=True)

        cab_file = catalog_path / 'DriverPackCatalog.cab'
        xml_file = catalog_path / 'DriverPackCatalog.xml'

        # Download CAB if not cached or older than 24 hours
        if not cab_file.exists() or self._is_cache_stale(cab_file):
            logger.info("Downloading Dell catalog", url=catalog_url)
            response = requests.get(catalog_url, timeout=60)
            response.raise_for_status()

            with open(cab_file, 'wb') as f:
                f.write(response.content)

            # Extract CAB file (requires expand.exe on Windows or cabextract on Linux)
            import subprocess
            try:
                subprocess.run(
                    ['expand', str(cab_file), str(xml_file)],
                    check=True,
                    capture_output=True
                )
            except FileNotFoundError:
                # Try cabextract on Linux
                subprocess.run(
                    ['cabextract', '-d', str(catalog_path), str(cab_file)],
                    check=True,
                    capture_output=True
                )

        # Read XML
        with open(xml_file, 'r', encoding='utf-8') as f:
            return f.read()

    def _find_dell_driver_pack(
        self,
        catalog_data: Dict,
        hardware_model: str,
        driver_type: Optional[str] = None
    ) -> Optional[Dict]:
        """Find matching driver pack in Dell catalog"""
        try:
            driver_packs = catalog_data['DriverPackManifest']['DriverPackage']

            if not isinstance(driver_packs, list):
                driver_packs = [driver_packs]

            for pack in driver_packs:
                supported_systems = pack.get('SupportedSystems', {}).get('Brand', {}).get('Model', [])

                if not isinstance(supported_systems, list):
                    supported_systems = [supported_systems]

                # Check if hardware model matches
                for system in supported_systems:
                    if isinstance(system, dict):
                        system_name = system.get('@name', '')
                    else:
                        system_name = str(system)

                    if hardware_model.lower() in system_name.lower():
                        # Match found, return driver pack info
                        return {
                            'name': pack.get('@name', ''),
                            'dellVersion': pack.get('@dellVersion', ''),
                            'path': pack.get('@path', ''),
                            'releaseNotes': pack.get('@releaseNotes', ''),
                            'dateTime': pack.get('@dateTime', ''),
                            'size': pack.get('@size', 0)
                        }

        except Exception as e:
            logger.error("Error parsing Dell catalog", error=str(e))

        return None

    def _discover_hp_driver(self, job: Job) -> Dict[str, Any]:
        """Discover HP driver updates"""
        logger.info("Discovering HP drivers", job_id=job.id, model=job.hardware_model)

        # TODO: Implement HP driver discovery
        # HP uses HPIA (HP Image Assistant) and reference files
        # For Phase 1, we can use a placeholder

        logger.warning("HP driver discovery not yet implemented")
        return {'update_available': False, 'note': 'HP discovery not implemented'}

    def _discover_lenovo_driver(self, job: Job) -> Dict[str, Any]:
        """Discover Lenovo driver updates"""
        logger.info("Discovering Lenovo drivers", job_id=job.id, model=job.hardware_model)

        # TODO: Implement Lenovo driver discovery
        # Lenovo uses Thin Installer and XML catalogs

        logger.warning("Lenovo driver discovery not yet implemented")
        return {'update_available': False, 'note': 'Lenovo discovery not implemented'}

    def _discover_software(self, job: Job) -> Dict[str, Any]:
        """Discover software updates (for Phase 2)"""
        logger.info("Discovering software updates", job_id=job.id, software=job.software_title)

        # TODO: Implement software discovery using LLM
        # This will be part of Phase 2

        logger.warning("Software discovery not yet implemented (Phase 2)")
        return {'update_available': False, 'note': 'Software discovery Phase 2'}

    def _compare_versions(self, current: Optional[str], latest: str) -> bool:
        """Compare version strings to determine if update is available"""
        if not current:
            return True

        # Simple string comparison for now
        # TODO: Implement proper version comparison (semver)
        return current != latest

    def _is_cache_stale(self, file_path: Path, max_age_hours: int = 24) -> bool:
        """Check if cached file is older than max_age_hours"""
        if not file_path.exists():
            return True

        file_age = datetime.now().timestamp() - file_path.stat().st_mtime
        return file_age > (max_age_hours * 3600)
