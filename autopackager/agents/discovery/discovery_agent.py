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
            # Catalog paths are relative to the Dell download base URL
            base_url = catalog_config.get('base_url', 'https://downloads.dell.com/').rstrip('/')
            download_url = f"{base_url}/{driver_pack.get('path', '')}"

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
                # Brand can be a dict (single brand) or list (multiple brands) due to
                # how xmltodict parses repeated XML elements.
                brands = pack.get('SupportedSystems', {}).get('Brand', {})
                if not isinstance(brands, list):
                    brands = [brands]

                supported_systems = []
                for brand in brands:
                    models = brand.get('Model', [])
                    if not isinstance(models, list):
                        models = [models]
                    supported_systems.extend(models)

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
        """Discover HP driver updates from HPIA platform list"""
        logger.info("Discovering HP drivers", job_id=job.id, model=job.hardware_model)

        # Download and parse HP catalog
        catalog_config = self.oem_catalogs['hp']
        catalog_xml = self._download_hp_catalog(catalog_config)

        # Parse catalog
        catalog_data = xmltodict.parse(catalog_xml)

        # Find matching driver for the model
        driver_info = self._find_hp_driver(
            catalog_data,
            job.hardware_model,
            job.driver_type
        )

        if driver_info:
            latest_version = driver_info.get('version', 'Unknown')
            download_url = driver_info.get('url', '')

            # Compare versions
            update_available = self._compare_versions(job.current_version, latest_version)

            return {
                'update_available': update_available,
                'latest_version': latest_version,
                'download_url': download_url,
                'release_notes': driver_info.get('description', ''),
                'release_date': driver_info.get('releaseDate', ''),
                'file_size': driver_info.get('size', 0)
            }
        else:
            logger.warning("No HP driver found", model=job.hardware_model)
            return {'update_available': False}

    def _download_hp_catalog(self, catalog_config: Dict) -> str:
        """Download HP platform list catalog"""
        catalog_url = catalog_config['catalog_url']
        catalog_path = Path(catalog_config['catalog_path'])
        catalog_path.mkdir(parents=True, exist_ok=True)

        cab_file = catalog_path / 'platformlist.cab'
        xml_file = catalog_path / 'platformlist.xml'

        # Download CAB if not cached or older than 24 hours
        if not cab_file.exists() or self._is_cache_stale(cab_file):
            logger.info("Downloading HP catalog", url=catalog_url)
            response = requests.get(catalog_url, timeout=60)
            response.raise_for_status()

            with open(cab_file, 'wb') as f:
                f.write(response.content)

            # Extract CAB file
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

    def _find_hp_driver(
        self,
        catalog_data: Dict,
        hardware_model: str,
        driver_type: Optional[str] = None
    ) -> Optional[Dict]:
        """Find matching driver in HP catalog"""
        try:
            # HP catalog structure: ImagePal -> Platform
            platforms = catalog_data.get('ImagePal', {}).get('Platform', [])

            if not isinstance(platforms, list):
                platforms = [platforms]

            for platform in platforms:
                # Check if model matches
                platform_name = platform.get('@SystemName', '')
                product_code = platform.get('@ProductCode', '')

                if (hardware_model.lower() in platform_name.lower() or
                    hardware_model.lower() in product_code.lower()):

                    # Found matching platform
                    # For demonstration, return SoftPaq info for drivers
                    # In production, this would parse specific driver SoftPaqs

                    return {
                        'name': f"HP {platform_name} Driver Pack",
                        'version': platform.get('@OSReleaseIdList', 'Latest'),
                        'url': f"https://ftp.hp.com/pub/softpaq/sp{platform.get('@ProductCode', '00000')}/",
                        'description': f"Driver pack for HP {platform_name}",
                        'releaseDate': datetime.now().strftime('%Y-%m-%d'),
                        'size': 0  # Would be determined from SoftPaq metadata
                    }

        except Exception as e:
            logger.error("Error parsing HP catalog", error=str(e))

        return None

    def _discover_lenovo_driver(self, job: Job) -> Dict[str, Any]:
        """Discover Lenovo driver updates from catalog"""
        logger.info("Discovering Lenovo drivers", job_id=job.id, model=job.hardware_model)

        # Download and parse Lenovo catalog
        catalog_config = self.oem_catalogs['lenovo']
        catalog_xml = self._download_lenovo_catalog(catalog_config)

        # Parse catalog
        catalog_data = xmltodict.parse(catalog_xml)

        # Find matching driver for the model
        driver_info = self._find_lenovo_driver(
            catalog_data,
            job.hardware_model,
            job.driver_type
        )

        if driver_info:
            latest_version = driver_info.get('version', 'Unknown')
            download_url = driver_info.get('url', '')

            # Compare versions
            update_available = self._compare_versions(job.current_version, latest_version)

            return {
                'update_available': update_available,
                'latest_version': latest_version,
                'download_url': download_url,
                'release_notes': driver_info.get('releaseNotes', ''),
                'release_date': driver_info.get('releaseDate', ''),
                'file_size': driver_info.get('size', 0)
            }
        else:
            logger.warning("No Lenovo driver found", model=job.hardware_model)
            return {'update_available': False}

    def _download_lenovo_catalog(self, catalog_config: Dict) -> str:
        """Download Lenovo driver catalog XML"""
        catalog_url = catalog_config['catalog_url']
        catalog_path = Path(catalog_config['catalog_path'])
        catalog_path.mkdir(parents=True, exist_ok=True)

        xml_file = catalog_path / 'catalogv2.xml'

        # Download XML if not cached or older than 24 hours
        if not xml_file.exists() or self._is_cache_stale(xml_file):
            logger.info("Downloading Lenovo catalog", url=catalog_url)
            response = requests.get(catalog_url, timeout=60)
            response.raise_for_status()

            with open(xml_file, 'wb') as f:
                f.write(response.content)

        # Read XML
        with open(xml_file, 'r', encoding='utf-8') as f:
            return f.read()

    def _find_lenovo_driver(
        self,
        catalog_data: Dict,
        hardware_model: str,
        driver_type: Optional[str] = None
    ) -> Optional[Dict]:
        """Find matching driver in Lenovo catalog"""
        try:
            # Lenovo catalog structure: Products -> Product -> Driver
            products = catalog_data.get('Products', {}).get('Product', [])

            if not isinstance(products, list):
                products = [products]

            for product in products:
                # Check if model matches
                model_name = product.get('@name', '')
                model_type = product.get('@type', '')

                if hardware_model.lower() in model_name.lower():
                    # Found matching product
                    drivers = product.get('Driver', [])

                    if not isinstance(drivers, list):
                        drivers = [drivers]

                    # Filter by driver type if specified
                    for driver in drivers:
                        driver_category = driver.get('@category', '').lower()

                        # If specific driver type requested, filter
                        if driver_type and driver_type.lower() not in driver_category:
                            continue

                        # Return first matching driver
                        # In production, would return the latest version
                        return {
                            'name': driver.get('@name', 'Unknown Driver'),
                            'version': driver.get('@version', 'Unknown'),
                            'url': driver.get('URL', {}).get('#text', ''),
                            'releaseNotes': driver.get('@rebootType', ''),
                            'releaseDate': driver.get('@date', ''),
                            'size': int(driver.get('@size', 0))
                        }

                    # If no specific driver type match, return driver pack
                    if drivers:
                        first_driver = drivers[0]
                        return {
                            'name': f"Lenovo {model_name} Driver Pack",
                            'version': first_driver.get('@version', 'Latest'),
                            'url': first_driver.get('URL', {}).get('#text', ''),
                            'releaseNotes': f"Driver pack for {model_name}",
                            'releaseDate': first_driver.get('@date', ''),
                            'size': int(first_driver.get('@size', 0))
                        }

        except Exception as e:
            logger.error("Error parsing Lenovo catalog", error=str(e))

        return None

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
