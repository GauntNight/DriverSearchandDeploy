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
    # MSI identity
    upgrade_code: Optional[str] = None
    product_code: Optional[str] = None
    product_name_pattern: Optional[str] = None
    publisher: Optional[str] = None
    # EXE identity (reserved for follow-up PR; loader tolerates today)
    pe_company_name: Optional[str] = None
    pe_product_name: Optional[str] = None
    # Binary fingerprint (any type)
    sha256: Optional[str] = None
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
