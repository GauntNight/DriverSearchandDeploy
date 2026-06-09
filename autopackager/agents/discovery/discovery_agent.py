"""Discovery Agent - Find New Software/Driver Versions"""

import os
import re
import requests
import xmltodict
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional

from autopackager.models.job import Job, JobType
from autopackager.utils.config import get_config
from autopackager.utils.logger import get_logger
from autopackager.utils.version_comparison import VersionComparator

logger = get_logger(__name__)


class DiscoveryAgent:
    """Agent responsible for discovering new software and driver versions"""

    def __init__(self):
        self.config = get_config()
        self.oem_catalogs = self.config['oem_catalogs']
        self.version_comparator = VersionComparator()

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

        # Resolve the target OS (Windows10/Windows11) so we don't hand a
        # Win10 pack to a Win11 device. Sourced from the model's Intune
        # managed device when available; None means "don't filter by OS".
        target_os = self._resolve_target_os(job)

        # Find matching driver pack for the model
        driver_pack = self._find_dell_driver_pack(
            catalog_data,
            job.hardware_model,
            job.driver_type,
            target_os=target_os,
        )

        if driver_pack:
            latest_version = driver_pack.get('dellVersion', 'Unknown')
            # Catalog paths are relative to the Dell download base URL
            base_url = catalog_config.get('base_url', 'https://downloads.dell.com/').rstrip('/')
            download_url = f"{base_url}/{driver_pack.get('path', '')}"

            # Compare versions
            update_available = self._compare_versions(job.current_version, latest_version, job.vendor)

            return {
                'update_available': update_available,
                'latest_version': latest_version,
                'download_url': download_url,
                'release_notes': driver_pack.get('releaseNotes', ''),
                'release_date': driver_pack.get('dateTime', ''),
                'file_size': driver_pack.get('size', 0),
                'target_os': target_os,
                'os_code': driver_pack.get('osCode', ''),
            }
        else:
            logger.warning("No driver pack found", model=job.hardware_model, target_os=target_os)
            return {'update_available': False, 'target_os': target_os}

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
        driver_type: Optional[str] = None,
        target_os: Optional[str] = None,
    ) -> Optional[Dict]:
        """Find the best matching Dell driver pack for a model (+ OS).

        Selection logic — fixes three legacy bugs that surfaced live against a
        real Latitude 5420 (it used to return a "Latitude 5420 Rugged" /
        Windows10 / 2021 pack for a plain Latitude 5420 on Windows 11):

          1. **Exact model match.** Normalised, exact model-name match so
             ``"Latitude 5420"`` does not also match ``"Latitude 5420 Rugged"``
             or ``"E5420"`` via substring. Falls back to substring only when
             there is no exact match (logged), to stay resilient to catalogs
             that qualify the model name.
          2. **OS filter.** Keep only packs whose ``osCode`` set contains
             ``target_os`` (e.g. ``"Windows11"``). Skipped when ``target_os``
             is None; relaxed with a warning if the OS filter empties the set.
          3. **Newest wins.** Among the survivors, pick the latest by release
             date (tie-broken by the Dell A-rev version) instead of the first
             pack in document order.
        """
        try:
            packs = catalog_data['DriverPackManifest']['DriverPackage']
        except (KeyError, TypeError):
            return None
        if not isinstance(packs, list):
            packs = [packs]

        try:
            infos = [self._dell_pack_info(p) for p in packs]
            target = self._normalize_model(hardware_model)

            exact = [pi for pi in infos if target and target in pi['models_norm']]
            candidates = exact
            if not candidates:
                candidates = [pi for pi in infos
                              if target and any(target in m for m in pi['models_norm'])]
                if candidates:
                    logger.warning(
                        "No exact Dell model match; falling back to substring",
                        model=hardware_model, matched=len(candidates))

            if not candidates:
                return None

            if target_os:
                os_matched = [pi for pi in candidates if target_os in pi['os_codes']]
                if os_matched:
                    candidates = os_matched
                else:
                    logger.warning(
                        "No Dell pack for target OS; ignoring OS filter",
                        model=hardware_model, target_os=target_os)

            # Newest by release date, tie-broken by Dell A-rev version.
            best = max(candidates, key=lambda pi: (pi['date_key'], pi['version_key']))
            logger.info(
                "Selected Dell driver pack", model=hardware_model, target_os=target_os,
                version=best['pack_return']['dellVersion'],
                os_code=best['pack_return']['osCode'],
                candidates=len(candidates))
            return best['pack_return']

        except Exception as e:
            logger.error("Error parsing Dell catalog", error=str(e))
            return None

    @staticmethod
    def _as_list(value):
        """Normalise xmltodict's dict-or-list-or-None into a list."""
        if value is None:
            return []
        return value if isinstance(value, list) else [value]

    @staticmethod
    def _normalize_model(name: Optional[str]) -> str:
        """Case-fold, trim, and collapse internal whitespace for model compare."""
        return ' '.join((name or '').strip().casefold().split())

    @staticmethod
    def _dell_arev_to_int(version: Optional[str]) -> int:
        """Map a Dell A-rev version (``A13`` -> 13, ``A00`` -> 0) to an int.

        Returns -1 for anything that doesn't parse, so non-A-rev versions
        simply lose the tie-break to dated packs rather than crashing.
        """
        try:
            return int(str(version or '').strip().lstrip('Aa') or -1)
        except ValueError:
            return -1

    def _dell_pack_info(self, pack: Dict) -> Dict[str, Any]:
        """Pre-extract the fields the selector needs from one DriverPackage."""
        models = []
        for brand in self._as_list((pack.get('SupportedSystems') or {}).get('Brand')):
            if not isinstance(brand, dict):
                continue
            for m in self._as_list(brand.get('Model')):
                models.append(m.get('@name', '') if isinstance(m, dict) else str(m))

        os_codes = {
            o.get('@osCode', '')
            for o in self._as_list((pack.get('SupportedOperatingSystems') or {}).get('OperatingSystem'))
            if isinstance(o, dict)
        }

        date_raw = pack.get('@dateTime') or ''
        try:
            date_key = datetime.fromisoformat(date_raw)
        except (ValueError, TypeError):
            date_key = datetime.min

        return {
            'models_norm': {self._normalize_model(m) for m in models},
            'os_codes': os_codes,
            'date_key': date_key,
            'version_key': self._dell_arev_to_int(pack.get('@dellVersion')),
            'pack_return': {
                'name': pack.get('@name', ''),
                'dellVersion': pack.get('@dellVersion', ''),
                'path': pack.get('@path', ''),
                'releaseNotes': pack.get('@releaseNotes', ''),
                'dateTime': pack.get('@dateTime', ''),
                'size': pack.get('@size', 0),
                'osCode': ','.join(sorted(c for c in os_codes if c)),
            },
        }

    def _resolve_target_os(self, job) -> Optional[str]:
        """Resolve the Dell ``osCode`` to filter packs by for this job.

        Order of precedence:
          1. ``job.job_metadata['target_os']`` if the caller set it explicitly.
          2. The OS of an Intune managed device of this hardware model — when a
             model spans Win10/Win11 we prefer the newest (Windows11).
          3. ``None`` (no OS filter).

        Best-effort: any failure talking to Graph degrades to None so driver
        discovery never hard-fails on the OS lookup (and unit tests, which have
        no Graph, simply get None).
        """
        meta = getattr(job, 'job_metadata', None)
        if isinstance(meta, dict) and meta.get('target_os'):
            return meta['target_os']

        model = getattr(job, 'hardware_model', None)
        if not model:
            return None

        try:
            from autopackager.utils.graph_client import GraphAPIClient
            gc = GraphAPIClient()
            resp = gc.get(
                "deviceManagement/managedDevices"
                "?$filter=operatingSystem eq 'Windows'"
                "&$select=model,operatingSystem,osVersion&$top=100")
            wanted = self._normalize_model(model)
            codes = set()
            for d in resp.get('value', []) or []:
                if self._normalize_model(d.get('model')) == wanted:
                    code = self._os_code_from_os_version(d.get('osVersion'))
                    if code:
                        codes.add(code)
            if not codes:
                return None
            for pref in ('Windows11', 'Windows10'):  # newest wins on mixed fleets
                if pref in codes:
                    logger.info("Resolved target OS from Intune device",
                                model=model, target_os=pref)
                    return pref
            return sorted(codes)[0]
        except Exception as e:  # noqa: BLE001 — OS lookup is best-effort
            logger.warning("Could not resolve target OS from Intune; no OS filter",
                           model=model, error=str(e))
            return None

    @staticmethod
    def _os_code_from_os_version(os_version: Optional[str]) -> Optional[str]:
        """Map a Windows ``managedDevice.osVersion`` to a Dell catalog osCode.

        Windows 10 and 11 both report ``10.0.<build>``; build >= 22000 is
        Windows 11.
        """
        try:
            parts = str(os_version or '').split('.')
            build = int(parts[2]) if len(parts) >= 3 else 0
        except (ValueError, IndexError):
            return None
        if build >= 22000:
            return 'Windows11'
        if build > 0:
            return 'Windows10'
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
            update_available = self._compare_versions(job.current_version, latest_version, job.vendor)

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

        # Resolve target OS (Windows10/Windows11) so we pick the right SCCM
        # driver pack for the device's OS, same as the Dell path.
        target_os = self._resolve_target_os(job)

        # Find matching driver for the model
        driver_info = self._find_lenovo_driver(
            catalog_data,
            job.hardware_model,
            job.driver_type,
            target_os=target_os,
        )

        if driver_info:
            latest_version = driver_info.get('version', 'Unknown')
            download_url = driver_info.get('url', '')

            # Compare versions
            update_available = self._compare_versions(job.current_version, latest_version, job.vendor)

            return {
                'update_available': update_available,
                'latest_version': latest_version,
                'download_url': download_url,
                'release_notes': driver_info.get('releaseNotes', ''),
                'release_date': driver_info.get('releaseDate', ''),
                'file_size': driver_info.get('size', 0),
                'target_os': target_os,
                'os_code': driver_info.get('os_code', ''),
            }
        else:
            logger.warning("No Lenovo driver found", model=job.hardware_model, target_os=target_os)
            return {'update_available': False, 'target_os': target_os}

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

        # Read XML. utf-8-sig strips the BOM Lenovo ships, which would otherwise
        # make xmltodict/expat choke ("not well-formed") on the leading ﻿.
        with open(xml_file, 'r', encoding='utf-8-sig') as f:
            return f.read()

    # Map our canonical OS code to Lenovo's catalog @os values.
    _LENOVO_OS_CODE = {'Windows11': 'win11', 'Windows10': 'win10'}

    def _find_lenovo_driver(
        self,
        catalog_data: Dict,
        hardware_model: str,
        driver_type: Optional[str] = None,
        target_os: Optional[str] = None,
    ) -> Optional[Dict]:
        """Find the best Lenovo SCCM driver pack for a model (+ OS).

        The real ``catalogv2.xml`` is ``ModelList -> Model -> SCCM`` — the
        SCCM/MECM driver pack — NOT the ``Products -> Product -> Driver`` shape
        the legacy code assumed (which matched **nothing**, leaving Lenovo
        discovery non-functional). Each ``Model`` carries machine ``Types``
        (e.g. ``20XW``), a ``BIOS`` element, and one or more ``SCCM`` packs
        keyed by ``@os`` (``win10``/``win11``), Windows feature ``@version``
        (``1909``/``22H2``), and ``@date``; ``#text`` is the full pack URL.

        Selection:
          1. **Match the model** by machine ``Type`` code (exact), else exact
             normalized name, else normalized-substring (logged fallback).
             Lenovo names embed gen + type codes (``ThinkPad X1 Carbon 9TH Gen
             Type 20XW 20XX``), so the machine type is the most reliable key.
          2. **OS filter** the SCCM packs to ``win10``/``win11`` (relaxed with a
             warning if it empties the set).
          3. **Newest wins** — latest by ``@date``, tie-broken by Windows
             feature version.

        ``driver_type`` is accepted for signature parity but doesn't apply: an
        SCCM pack is a whole-model driver bundle, not a per-category driver.
        """
        try:
            models = catalog_data['ModelList']['Model']
        except (KeyError, TypeError):
            return None
        if not isinstance(models, list):
            models = [models]

        target = self._normalize_model(hardware_model)
        if not target:
            return None

        def types_of(model):
            node = (model.get('Types') or {}).get('Type')
            return {self._normalize_model(t) for t in self._as_list(node) if isinstance(t, str)}

        def name_of(model):
            return self._normalize_model(model.get('@name'))

        by_type = [m for m in models if target in types_of(m)]
        by_name_exact = [m for m in models if name_of(m) == target]
        by_name_sub = [m for m in models if target and target in name_of(m)]
        matched = by_type or by_name_exact or by_name_sub
        if not matched:
            return None
        if not (by_type or by_name_exact):
            logger.warning("No exact Lenovo model/type match; using name substring",
                           model=hardware_model, matched=len(matched))

        # Gather every SCCM pack across the matched model(s).
        packs = []
        for model in matched:
            for sccm in self._as_list(model.get('SCCM')):
                if isinstance(sccm, dict):
                    packs.append((model, sccm))
        if not packs:
            return None

        lenovo_os = self._LENOVO_OS_CODE.get(target_os)
        if lenovo_os:
            os_packs = [(m, s) for (m, s) in packs if s.get('@os') == lenovo_os]
            if os_packs:
                packs = os_packs
            else:
                logger.warning("No Lenovo SCCM pack for target OS; ignoring OS filter",
                               model=hardware_model, target_os=target_os)

        def date_key(sccm):
            try:
                return datetime.fromisoformat(sccm.get('@date'))
            except (ValueError, TypeError):
                return datetime.min

        best_model, best = max(
            packs, key=lambda ms: (date_key(ms[1]), self._win_release_key(ms[1].get('@version'))))
        logger.info(
            "Selected Lenovo driver pack", model=hardware_model, target_os=target_os,
            os_code=best.get('@os'), date=best.get('@date'),
            windows_release=best.get('@version'), candidates=len(packs))
        return {
            'name': best_model.get('@name', ''),
            'version': best.get('@date', '') or best.get('@version', ''),
            'url': best.get('#text', '') or '',
            'releaseNotes': '',
            'releaseDate': best.get('@date', ''),
            'size': 0,
            'os_code': best.get('@os', ''),
            'windows_release': best.get('@version', ''),
        }

    @staticmethod
    def _win_release_key(version: Optional[str]) -> int:
        """Sortable key for a Windows feature-release string, for tie-breaks.

        ``22H2`` -> 2202, ``21H2`` -> 2102, ``1909`` -> 1909, ``*``/unknown ->
        -1. Only used to break ties when two SCCM packs share a release date,
        so cross-scheme magnitude consistency isn't important.
        """
        s = str(version or '').strip().upper()
        m = re.match(r'^(\d{2})H(\d)$', s)
        if m:
            return int(m.group(1)) * 100 + int(m.group(2))
        try:
            return int(s)
        except ValueError:
            return -1

    def _discover_software(self, job: Job) -> Dict[str, Any]:
        """Discover software metadata for an MSI or EXE packaging job.

        Unlike driver discovery (which scans OEM catalogs for newer versions),
        software discovery inspects the provided installer to auto-fill the
        version, publisher, and identification fields. The admin supplies the
        installer (local path or URL) and an install command; this step turns
        the installer's own metadata into everything packaging / Intune need.

        Branches on file extension: .msi -> read MSI Property table; .exe ->
        read PE VS_VERSIONINFO. Both end up in job_metadata under their
        respective keys (msi_metadata / exe_metadata) so PackagingAgent can
        tell them apart.
        """
        from autopackager.utils.msi_metadata import MSIMetadata, read_msi_metadata

        logger.info("Discovering software metadata", job_id=job.id, software=job.software_title)

        metadata = job.job_metadata or {}
        install_command = metadata.get('install_command')

        # EXE branch: PE VS_VERSIONINFO instead of MSI Property table.
        is_exe = self._software_source_is_exe(job)
        if is_exe:
            return self._discover_exe(job, metadata, install_command)

        # Reuse metadata already read at job-creation time when available, so
        # workers don't need access to the admin's local MSI file.
        msi_meta = metadata.get('msi_metadata')

        if not msi_meta:
            msi_path = self._ensure_local_msi(job)
            if msi_path:
                try:
                    msi_meta = read_msi_metadata(msi_path).to_dict()
                    logger.info(
                        "Read MSI metadata",
                        job_id=job.id,
                        product_name=msi_meta.get('product_name'),
                        product_version=msi_meta.get('product_version'),
                    )
                except Exception as e:
                    logger.warning("Failed to read MSI metadata", job_id=job.id, error=str(e))

        msi_meta = msi_meta or MSIMetadata().to_dict()

        latest_version = (
            msi_meta.get('product_version')
            or metadata.get('target_version')
            or job.target_version
            or 'unknown'
        )

        return {
            'update_available': True,
            'latest_version': latest_version,
            'download_url': metadata.get('download_url') or job.download_url,
            'release_notes': metadata.get('release_notes') or job.release_notes or '',
            'msi_metadata': msi_meta,
            'install_command': install_command,
            'product_name': msi_meta.get('product_name'),
            'manufacturer': msi_meta.get('manufacturer'),
        }

    @staticmethod
    def _software_source_is_exe(job: Job) -> bool:
        """True when the job's installer source has an .exe extension."""
        metadata = job.job_metadata or {}
        src = (metadata.get('installer_source')
               or metadata.get('download_url')
               or job.download_url or '')
        return str(src).lower().split('?')[0].endswith('.exe')

    def _discover_exe(self, job: Job, metadata: dict, install_command: Optional[str]) -> Dict[str, Any]:
        """Discovery for EXE software jobs: read PE VS_VERSIONINFO + SHA-256.

        The catalog entry that matched at job-creation time is referenced by
        catalog_entry_id in job_metadata; packaging will resolve it again at
        publish time to get the detection_rules and installer_family.
        """
        from autopackager.utils.pe_metadata import PEMetadata, read_pe_metadata, sha256_file

        exe_meta = metadata.get('exe_metadata')
        sha = metadata.get('sha256')
        if not exe_meta or not sha:
            # CLI usually pre-populates these; only re-read when the worker
            # is processing a job created from a URL-only source.
            local_path = self._ensure_local_exe(job)
            if local_path:
                try:
                    if not exe_meta:
                        exe_meta = read_pe_metadata(local_path).to_dict()
                    if not sha:
                        sha = sha256_file(local_path)
                    logger.info(
                        "Read PE metadata",
                        job_id=job.id,
                        product_name=exe_meta.get('product_name'),
                        product_version=exe_meta.get('product_version'),
                    )
                except Exception as e:
                    logger.warning("Failed to read PE metadata", job_id=job.id, error=str(e))

        exe_meta = exe_meta or PEMetadata().to_dict()

        latest_version = (
            exe_meta.get('product_version')
            or exe_meta.get('file_version')
            or metadata.get('target_version')
            or job.target_version
            or 'unknown'
        )

        return {
            'update_available': True,
            'latest_version': latest_version,
            'download_url': metadata.get('download_url') or job.download_url,
            'release_notes': metadata.get('release_notes') or job.release_notes or '',
            'exe_metadata': exe_meta,
            'sha256': sha,
            'catalog_entry_id': metadata.get('catalog_entry_id'),
            'install_command': install_command,
            'product_name': exe_meta.get('product_name'),
            'manufacturer': exe_meta.get('company_name'),
        }

    def _ensure_local_exe(self, job: Job) -> Optional[Path]:
        """Return a local path to the job's EXE, downloading it if it is a URL.

        Mirrors the MSI version exactly; kept separate so each branch can
        evolve independently (e.g., EXE may want to verify a signature
        before reading metadata).
        """
        from autopackager.utils.msi_metadata import resolve_local_path

        metadata = job.job_metadata or {}
        source = (
            metadata.get('installer_source')
            or metadata.get('download_url')
            or job.download_url
        )
        if not source:
            return None

        local = resolve_local_path(source)
        if local is not None:
            return local if local.exists() else None

        try:
            cache_dir = Path(self.config['paths']['downloads'])
            cache_dir.mkdir(parents=True, exist_ok=True)
            filename = source.split('/')[-1].split('?')[0] or 'installer.exe'
            dest = cache_dir / filename
            if not dest.exists() or self._is_cache_stale(dest):
                logger.info("Downloading EXE for metadata read", url=source)
                response = requests.get(source, timeout=300)
                response.raise_for_status()
                with open(dest, 'wb') as f:
                    f.write(response.content)
            return dest
        except Exception as e:
            logger.warning("Could not fetch EXE for metadata read", error=str(e))
            return None

    def _ensure_local_msi(self, job: Job) -> Optional[Path]:
        """Return a local path to the job's MSI, downloading it if it is a URL."""
        from autopackager.utils.msi_metadata import resolve_local_path

        metadata = job.job_metadata or {}
        source = (
            metadata.get('installer_source')
            or metadata.get('download_url')
            or job.download_url
        )
        if not source:
            return None

        local = resolve_local_path(source)
        if local is not None:
            return local if local.exists() else None

        # Remote URL — download to a cache dir so we can read its metadata.
        try:
            cache_dir = Path(self.config['paths']['downloads'])
            cache_dir.mkdir(parents=True, exist_ok=True)
            filename = source.split('/')[-1].split('?')[0] or 'installer.msi'
            dest = cache_dir / filename
            if not dest.exists() or self._is_cache_stale(dest):
                logger.info("Downloading MSI for metadata read", url=source)
                response = requests.get(source, timeout=300)
                response.raise_for_status()
                with open(dest, 'wb') as f:
                    f.write(response.content)
            return dest
        except Exception as e:
            logger.warning("Could not fetch MSI for metadata read", error=str(e))
            return None

    def _compare_versions(self, current: Optional[str], latest: str, vendor: Optional[str] = None) -> bool:
        """Compare version strings to determine if update is available

        Args:
            current: Current version string (can be None)
            latest: Latest version string to compare
            vendor: OEM vendor (dell, hp, lenovo) for vendor-specific parsing

        Returns:
            True if latest version is newer than current version
            Returns True if current is None (no current version)
            Returns False if version parsing fails
        """
        try:
            return self.version_comparator.is_newer(current, latest, vendor)
        except Exception as e:
            logger.error(
                "Version comparison failed",
                current=current,
                latest=latest,
                vendor=vendor,
                error=str(e)
            )
            # Default to False (don't update) if we can't parse versions
            return False

    def _is_cache_stale(self, file_path: Path, max_age_hours: int = 24) -> bool:
        """Check if cached file is older than max_age_hours"""
        if not file_path.exists():
            return True

        file_age = datetime.now().timestamp() - file_path.stat().st_mtime
        return file_age > (max_age_hours * 3600)
