"""Installer Catalog -- known-good silent-install commands for MSIs and EXEs.

The catalog has two layers:
  * `autopackager/data/installer_catalog.yaml` -- committed baseline, seed
    knowledge curated in-repo. Treated as read-only at runtime.
  * `data/installer_catalog.local.yaml` -- gitignored, operator-private
    overlay. All runtime additions and use-count updates write here.

Merge precedence on load is `local` over `baseline`: if a local entry shares
an `id` with a baseline entry, the local copy wins. This lets the operator
override a shipped template (e.g. add a public-property switch) without
forking the baseline file.

Match priority for MSIs (highest -> lowest):
  1. UpgradeCode exact match
  2. ProductCode exact match
  3. (product_name_pattern substring, case-insensitive) AND publisher
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import yaml

from autopackager.utils.logger import get_logger

logger = get_logger(__name__)

CATALOG_VERSION = 1


# Controlled vocabulary for CatalogEntry.installer_family. Adding a new
# value is fine; spelling typos that fall outside this set just log a
# warning at load time and treat the entry as 'custom'.
INSTALLER_FAMILIES = {
    'msi',                # plain Windows Installer .msi
    'inno_setup',         # Jordan Russell's Inno Setup (Git for Windows, VS Code, WinSCP, GIMP, Audacity)
    'nsis',               # Nullsoft Scriptable Install System (Notepad++, classic OpenSSH)
    'wix_burn',           # WiX Burn bundle / bootstrapper (.NET SDK, Visual Studio installer)
    'msft_bootstrapper',  # Microsoft custom bootstrapper (.NET Runtime, VC++ Redistributable)
    'wrapped_msi',        # EXE that extracts to an MSI (Adobe Reader DC, PowerToys)
    'wrapped_zip',        # ZIP that contains an installer (Foxit Reader)
    'custom',             # vendor-specific or unknown
}


# Standard silent-install switch strings per installer family. Operators
# can still override per-entry via install_command_template; this map only
# fires when the catalog entry omits install_command_template entirely.
INSTALLER_FAMILY_SWITCHES = {
    'msi':               '/qn /norestart',
    'inno_setup':        '/VERYSILENT /NORESTART /SUPPRESSMSGBOXES',
    'nsis':              '/S',
    'wix_burn':          '/quiet /norestart',
    'msft_bootstrapper': '/quiet /norestart',
    # wrapped_msi / wrapped_zip don't get switches here -- a pre-stage
    # extraction step produces a real MSI which is then driven via its
    # own catalog entry (resolved by ProductCode after extraction).
    'wrapped_msi':       None,
    'wrapped_zip':       None,
    'custom':            '',
}


# Controlled vocabulary for the ``kind`` field of catalog detection rule
# dicts. Each kind maps to one Graph win32LobApp*Rule type (see
# detection_rule_to_graph). The naming is deliberately verbose so YAML
# catalog entries are self-describing; the Graph payload's flag-soup
# ("operationType: version, operator: greaterThanOrEqual") is harder to
# scan when auditing hundreds of catalog entries.
DETECTION_RULE_KINDS = {
    'msi_product_code',   # MSI ProductCode (with optional version compare)
    'file_exists',        # File or folder presence
    'file_version',       # PE/DLL file version compared to a value
    'registry_exists',    # Registry key/value presence
    'registry_value',     # Registry string value compared to a value
    'registry_version',   # Registry value parsed as a version, compared to a value
}


def default_silent_switches(family: Optional[str]) -> Optional[str]:
    """Return the standard silent-install switch string for ``family``.

    Returns None for families that require a pre-stage extraction step
    (wrapped_msi, wrapped_zip) and an empty string for 'custom' (operator
    must supply switches via install_command_template).
    """
    if not family:
        return None
    return INSTALLER_FAMILY_SWITCHES.get(family)


def detection_rule_to_graph(rule: dict, rule_type: str = 'detection') -> dict:
    """Convert a catalog detection rule dict to a Graph win32LobApp*Rule.

    Catalog rules are intentionally easier to author than the raw Graph
    payload -- ``kind: registry_version`` reads better in a YAML file than
    the equivalent ``operationType: version`` + ``operator:
    greaterThanOrEqual`` + ``@odata.type`` triple.

    Raises ValueError for unknown kinds; required fields per kind:
      msi_product_code  -- product_code [+ operator + version]
      file_exists       -- path + file (or folder)
      file_version      -- path + file + operator + value
      registry_exists   -- key [+ value_name]
      registry_value    -- key + value_name + operator + value
      registry_version  -- key + value_name + operator + value
    """
    kind = rule.get('kind')
    if kind not in DETECTION_RULE_KINDS:
        raise ValueError(
            f"Unknown detection rule kind: {kind!r}. "
            f"Supported kinds: {sorted(DETECTION_RULE_KINDS)}"
        )
    check32 = rule.get('check_32bit_on_64bit', False)

    if kind == 'msi_product_code':
        return {
            '@odata.type': '#microsoft.graph.win32LobAppProductCodeRule',
            'ruleType': rule_type,
            'productCode': rule['product_code'],
            'productVersionOperator': rule.get('operator', 'notConfigured'),
            'productVersion': rule.get('version'),
        }

    if kind in ('file_exists', 'file_version'):
        return {
            '@odata.type': '#microsoft.graph.win32LobAppFileSystemRule',
            'ruleType': rule_type,
            'path': rule['path'],
            'fileOrFolderName': rule.get('file') or rule.get('folder'),
            'check32BitOn64System': check32,
            'operationType': 'exists' if kind == 'file_exists' else 'version',
            'operator': rule.get('operator', 'notConfigured'),
            'comparisonValue': rule.get('value'),
        }

    # registry_exists / registry_value / registry_version
    op_type = {
        'registry_exists':  'exists',
        'registry_value':   'string',
        'registry_version': 'version',
    }[kind]
    return {
        '@odata.type': '#microsoft.graph.win32LobAppRegistryRule',
        'ruleType': rule_type,
        'keyPath': rule['key'],
        'valueName': rule.get('value_name'),
        'check32BitOn64System': check32,
        'operationType': op_type,
        'operator': rule.get('operator', 'notConfigured'),
        'comparisonValue': rule.get('value'),
    }

# Resolve once at import. ``__file__`` is .../autopackager/utils/installer_catalog.py
# Baseline ships next door under .../autopackager/data/installer_catalog.yaml.
# Local overlay lives at repo-root/data/installer_catalog.local.yaml so the
# operator's additions never accidentally end up staged.
_PKG_ROOT = Path(__file__).resolve().parent.parent
_REPO_ROOT = _PKG_ROOT.parent
BASELINE_PATH = _PKG_ROOT / "data" / "installer_catalog.yaml"
LOCAL_PATH = _REPO_ROOT / "data" / "installer_catalog.local.yaml"


@dataclass
class CatalogEntry:
    """A single known installer. Optional fields apply per `type`."""

    id: str
    type: str  # 'msi' | 'exe'
    install_command_template: str
    uninstall_command_template: Optional[str] = None
    # ---- Installer engine family ----------------------------------------
    # Identifies the bootstrapper / installer framework so we can derive
    # silent-install switches when no install_command_template is supplied
    # (via INSTALLER_FAMILY_SWITCHES) and so operators can filter the
    # catalog by engine (e.g., "show me every NSIS app we support").
    # See INSTALLER_FAMILIES for the controlled vocabulary.
    installer_family: Optional[str] = None
    # MSI identity
    upgrade_code: Optional[str] = None
    product_code: Optional[str] = None
    product_name_pattern: Optional[str] = None
    publisher: Optional[str] = None
    # EXE identity (read from the PE VS_VERSIONINFO resource at packaging
    # time; loader tolerates today, EXE packaging consumer arrives in a
    # follow-up PR).
    pe_company_name: Optional[str] = None
    pe_product_name: Optional[str] = None
    # Binary fingerprint (any type)
    sha256: Optional[str] = None
    # ---- Detection rules ------------------------------------------------
    # List of normalized detection rule dicts (see DETECTION_RULE_KINDS
    # for the kind vocabulary and detection_rule_to_graph() for the
    # Graph-payload conversion). For MSI packages the pipeline derives a
    # ProductCode rule automatically from the MSI's metadata, so this
    # field is normally left unset for type='msi'. For EXE packages,
    # operators MUST supply at least one rule -- there's no MSI
    # ProductCode to lean on and the synthetic registry rule
    # PackagingAgent generates today is a poor stand-in.
    detection_rules: Optional[list] = None
    # ---- Intune app-attribute overrides --------------------------------
    # All four are agnostic curated knowledge: same value across every
    # operator / tenant for a given installer. Belong in the baseline.
    # Each falls back to MSI-derived defaults when unset (see
    # DeploymentAgent._prepare_app_data).
    #
    # information_url -- "Help and support" URL surfaced in the Intune
    #   portal. Default source: MSI's ARPHELPLINK / ARPURLINFOABOUT
    #   property. Override here to point at a curated landing page.
    information_url: Optional[str] = None
    # description -- short app description shown in the portal's Notes
    #   field. Default source: MSI's Subject summary property. Override
    #   for marketing copy / operator notes.
    description: Optional[str] = None
    # categories -- Intune mobileAppCategory display names (e.g.,
    #   "Productivity", "Business"). Resolved to category IDs at publish
    #   time and attached via /mobileApps/{id}/categories/$ref. Empty
    #   list / None = no categories assigned.
    categories: Optional[list] = None
    # min_os_version -- Windows release the app requires (e.g., "1607",
    #   "1809", "22H2"). Translated to win32LobApp's
    #   windowsMinimumOperatingSystem flag dict. Default: "1607".
    min_os_version: Optional[str] = None
    # icon_b64 -- operator-supplied app icon (base64-encoded image bytes).
    #   Use this when the MSI ships an icon as a PE resource (Slack-style)
    #   and the pure-Python extractor returns nothing. Mime type sniffed
    #   from the leading magic bytes at publish time.
    icon_b64: Optional[str] = None
    # ---- Wrapped-installer extraction --------------------------------
    # Only meaningful for installer_family in {'wrapped_msi', 'wrapped_zip'}.
    # The catalog "wrapper" entry identifies the outer EXE/ZIP; the
    # inner MSI it produces is what actually ships through the rest of
    # the pipeline.
    #
    # extract_command_template -- shell command that extracts the inner
    #   MSI from a wrapped_msi installer. Template variables:
    #     {installer_path}  -- absolute path to the wrapper EXE
    #     {extract_dir}     -- absolute path to extraction destination
    #   The command runs with cwd=extract_dir so installers that write
    #   to the current directory (PowerToys: --extract_msi) land their
    #   output in the right place. Vendor-specific:
    #     Adobe Reader DC: '"{installer_path}" -sfx_o "{extract_dir}" -sfx_ne'
    #     PowerToys      : '"{installer_path}" --extract_msi'
    #   Ignored when family is wrapped_zip (zipfile.extractall handles it).
    extract_command_template: Optional[str] = None
    # extracted_msi_pattern -- glob (rglob form) matched against the
    #   extraction directory to locate the inner MSI. Defaults to '*.msi'.
    #   Override when the wrapper produces multiple MSIs and you need to
    #   pick a specific one (e.g., 'Reader-en_US.msi' for Adobe). The
    #   largest match wins -- defends against tiny accessory MSIs
    #   bundled alongside the main product MSI.
    extracted_msi_pattern: Optional[str] = None
    # Lifecycle / usage
    notes: str = ""
    first_seen: str = ""
    last_used: str = ""
    use_count: int = 0
    # Proven-good versions. Populated by record_verification() after a deploy
    # is observed installing on a real device. Each item:
    #   {product_version, verified_at (YYYY-MM-DD), verified_intune_app_id}
    verified_versions: list = field(default_factory=list)

    def render_install_command(self, installer_filename: str) -> str:
        return self.install_command_template.format(installer_filename=installer_filename)

    def render_uninstall_command(self, installer_filename: str = "") -> Optional[str]:
        # MSI uninstall templates embed the ProductCode literally, e.g.
        # ``msiexec /x {23170F69-...} /qn``. Those braces are NOT format
        # placeholders -- calling .format() on them raises. Only template
        # when an explicit {installer_filename} placeholder is present.
        if not self.uninstall_command_template:
            return None
        if "{installer_filename}" in self.uninstall_command_template:
            return self.uninstall_command_template.format(installer_filename=installer_filename)
        return self.uninstall_command_template


@dataclass
class Catalog:
    """In-memory view of the merged baseline + local overlay."""

    entries: list[CatalogEntry] = field(default_factory=list)

    def by_id(self, entry_id: str) -> Optional[CatalogEntry]:
        return next((e for e in self.entries if e.id == entry_id), None)

    def match_msi(self, msi_metadata: dict) -> Optional[CatalogEntry]:
        """Find a catalog entry matching an MSI's parsed metadata.

        ``msi_metadata`` is the dict produced by ``read_msi_metadata().to_dict()``
        (keys: product_name, product_version, product_code, upgrade_code,
        manufacturer, ...).
        """
        if not msi_metadata:
            return None
        msi_entries = [e for e in self.entries if e.type == "msi"]

        upgrade = _normalise_guid(msi_metadata.get("upgrade_code"))
        if upgrade:
            for e in msi_entries:
                if _normalise_guid(e.upgrade_code) == upgrade:
                    return e

        product = _normalise_guid(msi_metadata.get("product_code"))
        if product:
            for e in msi_entries:
                if _normalise_guid(e.product_code) == product:
                    return e

        name = (msi_metadata.get("product_name") or "").lower()
        pub = (msi_metadata.get("manufacturer") or "").lower()
        if name:
            for e in msi_entries:
                pattern = (e.product_name_pattern or "").lower()
                if not pattern or pattern not in name:
                    continue
                if e.publisher and pub and e.publisher.lower() not in pub:
                    continue
                return e

        return None

    def match_by_product_code(self, product_code: Optional[str]) -> Optional[CatalogEntry]:
        """Convenience wrapper for ProductCode-only lookup (used during verification)."""
        if not product_code:
            return None
        target = _normalise_guid(product_code)
        for e in self.entries:
            if e.type == "msi" and _normalise_guid(e.product_code) == target:
                return e
        return None

    def match_exe(self, pe_metadata: Optional[dict] = None,
                  sha256: Optional[str] = None) -> Optional[CatalogEntry]:
        """Find an EXE catalog entry by PE metadata or sha256 fingerprint.

        Match priority (highest -> lowest):
          1. sha256 exact (catches a specific known build, e.g. a tested
             version we've pinned in the baseline)
          2. pe_company_name + pe_product_name (case-insensitive substring
             match -- vendors often suffix builds with extra text, e.g.
             "Notepad++" vs "Notepad++ (32-bit)")

        Returns None for non-EXE matches (use match_msi for MSIs).
        """
        exe_entries = [e for e in self.entries if e.type == "exe"]

        if sha256:
            sha = sha256.strip().lower()
            for e in exe_entries:
                if (e.sha256 or "").strip().lower() == sha:
                    return e

        if not pe_metadata:
            return None
        company = (pe_metadata.get("company_name") or "").lower()
        product = (pe_metadata.get("product_name") or "").lower()
        if not (company or product):
            return None
        for e in exe_entries:
            cat_company = (e.pe_company_name or "").lower()
            cat_product = (e.pe_product_name or "").lower()
            if cat_company and company and cat_company not in company and company not in cat_company:
                continue
            if cat_product and product and cat_product not in product and product not in cat_product:
                continue
            if cat_company or cat_product:
                return e
        return None


def add_exe_entry(
    pe_metadata: dict,
    install_command_template: str,
    *,
    installer_family: Optional[str] = None,
    detection_rules: Optional[list] = None,
    sha256: Optional[str] = None,
    notes: str = "",
) -> 'CatalogEntry':
    """Append a new EXE entry to the local overlay.

    ``detection_rules`` is required for type='exe' in practice (the pipeline
    needs at least one rule to publish into Intune), but we don't enforce
    it here -- it's the CLI's job to refuse a catalog miss without an
    operator-supplied rule. Storing an entry without rules is fine for
    discovery / fingerprinting purposes.
    """
    product = (pe_metadata.get("product_name") or "").strip()
    entry_id = _slugify(product) or _slugify(
        pe_metadata.get("original_filename") or "exe-app"
    )

    overlay = _local_overlay_entries()
    if any(e.id == entry_id for e in overlay):
        record_use(entry_id)
        return next(e for e in overlay if e.id == entry_id)

    today = _today_iso()
    entry = CatalogEntry(
        id=entry_id,
        type="exe",
        installer_family=installer_family,
        install_command_template=install_command_template,
        pe_company_name=pe_metadata.get("company_name") or None,
        pe_product_name=product or None,
        sha256=sha256,
        detection_rules=detection_rules,
        notes=notes,
        first_seen=today,
        last_used=today,
        use_count=1,
    )
    overlay.append(entry)
    _write_local(overlay)
    logger.info("Catalog entry added", entry_id=entry_id, type="exe")
    return entry


def _normalise_guid(value: Optional[str]) -> Optional[str]:
    """Canonicalise an MSI GUID for case- and brace-insensitive comparison."""
    if not value:
        return None
    return value.strip().strip("{}").lower()


def _load_yaml_file(path: Path) -> dict:
    if not path.exists():
        return {"version": CATALOG_VERSION, "entries": []}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        logger.warning("Failed to parse catalog file", path=str(path), error=str(exc))
        return {"version": CATALOG_VERSION, "entries": []}
    if data.get("version") not in (None, CATALOG_VERSION):
        logger.warning(
            "Catalog version mismatch; loading defensively",
            path=str(path),
            seen=data.get("version"),
            expected=CATALOG_VERSION,
        )
    return data


def _entry_from_dict(raw: dict) -> Optional[CatalogEntry]:
    valid_keys = {f.name for f in fields(CatalogEntry)}
    try:
        return CatalogEntry(**{k: v for k, v in raw.items() if k in valid_keys})
    except TypeError as exc:
        logger.warning("Skipping malformed catalog entry", error=str(exc), raw=raw)
        return None


def load_catalog() -> Catalog:
    """Load baseline + local overlay; local entries override baseline by `id`."""
    by_id: dict[str, CatalogEntry] = {}
    for path in (BASELINE_PATH, LOCAL_PATH):
        data = _load_yaml_file(path)
        for raw in data.get("entries") or []:
            entry = _entry_from_dict(raw)
            if entry:
                by_id[entry.id] = entry
    return Catalog(entries=list(by_id.values()))


def _today_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _write_local(catalog_entries: list[CatalogEntry]) -> None:
    """Atomically write the local overlay file."""
    LOCAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": CATALOG_VERSION,
        "entries": [
            {k: v for k, v in asdict(e).items() if v not in (None, "")}
            for e in catalog_entries
        ],
    }
    tmp = LOCAL_PATH.with_suffix(LOCAL_PATH.suffix + ".tmp")
    tmp.write_text(
        yaml.safe_dump(payload, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )
    tmp.replace(LOCAL_PATH)


def _local_overlay_entries() -> list[CatalogEntry]:
    data = _load_yaml_file(LOCAL_PATH)
    out: list[CatalogEntry] = []
    for raw in data.get("entries") or []:
        entry = _entry_from_dict(raw)
        if entry:
            out.append(entry)
    return out


def record_use(entry_id: str) -> None:
    """Bump use_count and last_used for an existing entry, in the local overlay.

    If the entry only exists in the baseline, a thin overlay copy is created
    so the baseline file stays pristine.
    """
    overlay = _local_overlay_entries()
    overlay_ids = {e.id for e in overlay}

    target: Optional[CatalogEntry] = None
    for e in overlay:
        if e.id == entry_id:
            target = e
            break

    if target is None:
        baseline_catalog = Catalog(
            entries=[
                _entry_from_dict(r)
                for r in (_load_yaml_file(BASELINE_PATH).get("entries") or [])
                if _entry_from_dict(r) is not None
            ]
        )
        base = baseline_catalog.by_id(entry_id)
        if base is None:
            logger.warning("record_use called for unknown entry", entry_id=entry_id)
            return
        target = CatalogEntry(**asdict(base))
        overlay.append(target)

    target.use_count = (target.use_count or 0) + 1
    target.last_used = _today_iso()
    if not target.first_seen:
        target.first_seen = target.last_used

    _write_local(overlay)
    logger.info(
        "Catalog entry use recorded",
        entry_id=entry_id,
        use_count=target.use_count,
        last_used=target.last_used,
    )


def add_msi_entry(
    msi_metadata: dict,
    install_command_template: str,
    *,
    notes: str = "",
) -> CatalogEntry:
    """Append a new MSI entry to the local overlay.

    `id` is derived from the product name (lowercased, non-alnum -> '-'). If an
    entry with that id already exists in the overlay, ``record_use`` is called
    instead of duplicating.
    """
    name = (msi_metadata.get("product_name") or "").strip()
    entry_id = _slugify(name) or _slugify(msi_metadata.get("product_code") or "msi-app")

    overlay = _local_overlay_entries()
    if any(e.id == entry_id for e in overlay):
        record_use(entry_id)
        return next(e for e in overlay if e.id == entry_id)

    today = _today_iso()
    product_code = msi_metadata.get("product_code")
    # Uninstall is deterministic for MSIs once the ProductCode is known. Record
    # the canonical form here so the catalog file is self-contained -- future
    # operator can read the YAML and run the uninstall string verbatim without
    # going back through PackagingAgent.
    uninstall_template = (
        f"msiexec /x {product_code} /qn /norestart"
        if product_code else None
    )
    entry = CatalogEntry(
        id=entry_id,
        type="msi",
        install_command_template=install_command_template,
        uninstall_command_template=uninstall_template,
        upgrade_code=msi_metadata.get("upgrade_code"),
        product_code=product_code,
        product_name_pattern=name or None,
        publisher=msi_metadata.get("manufacturer") or None,
        notes=notes,
        first_seen=today,
        last_used=today,
        use_count=1,
    )
    overlay.append(entry)
    _write_local(overlay)
    logger.info("Catalog entry added", entry_id=entry_id, type="msi")
    return entry


def record_verification(
    entry_id: str,
    product_version: Optional[str],
    intune_app_id: Optional[str],
) -> None:
    """Record that an entry has been observed installing on a real device.

    Called by ``DeploymentAgent.check_all_deployments`` after a deployment's
    ``successful_installs`` count crosses zero. Idempotent on
    (product_version, intune_app_id): re-running against the same pair is a
    no-op so repeated polls don't accumulate duplicate verified entries.

    Writes to the local overlay; the committed baseline stays untouched.
    """
    overlay = _local_overlay_entries()
    target: Optional[CatalogEntry] = None
    for e in overlay:
        if e.id == entry_id:
            target = e
            break

    if target is None:
        baseline_catalog = Catalog(
            entries=[
                _entry_from_dict(r)
                for r in (_load_yaml_file(BASELINE_PATH).get("entries") or [])
                if _entry_from_dict(r) is not None
            ]
        )
        base = baseline_catalog.by_id(entry_id)
        if base is None:
            logger.warning("record_verification called for unknown entry", entry_id=entry_id)
            return
        target = CatalogEntry(**asdict(base))
        overlay.append(target)

    new_record = {
        "product_version": product_version or "unknown",
        "verified_at": _today_iso(),
        "verified_intune_app_id": intune_app_id or "",
    }

    # Idempotency: same (product_version, intune_app_id) is treated as a no-op
    # so the verified_versions list doesn't grow every time the poll runs.
    for existing in (target.verified_versions or []):
        if (
            existing.get("product_version") == new_record["product_version"]
            and existing.get("verified_intune_app_id") == new_record["verified_intune_app_id"]
        ):
            logger.debug(
                "Verification already recorded; skipping",
                entry_id=entry_id,
                product_version=product_version,
            )
            return

    if target.verified_versions is None:
        target.verified_versions = []
    target.verified_versions.append(new_record)
    _write_local(overlay)
    logger.info(
        "Catalog entry verified",
        entry_id=entry_id,
        product_version=product_version,
        intune_app_id=intune_app_id,
    )


def _slugify(value: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in (value or "").lower()).strip("-")
