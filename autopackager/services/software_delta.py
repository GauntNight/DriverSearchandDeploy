"""Unmanaged-software delta — what's installed in the environment but not packaged.

Combines the installed-software inventory (Intune **Detected Apps** across managed
devices + the local device's **ARP** registry) with the managed/known set
(published Intune Win32 apps + the installer catalog), classifies every app, and
surfaces the actionable gap: third-party and Microsoft *apps* that are installed
but not managed. Microsoft "standard OS components" are captured as their own
bucket (operator decision: only true OS components/runtimes — Microsoft apps like
Azure CLI / VS Code / PowerShell are candidates).

Buckets:
  * ``managed``               — matches a published Intune Win32 app.
  * ``known_packageable``     — matches an installer-catalog entry but isn't
                                published yet (we already know how to package it).
  * ``standard_os_component`` — hidden OS bit (ARP ``SystemComponent=1``) or a
                                curated Microsoft-OS pattern (Edge, WebView2,
                                VC++ redist, .NET runtime, …).
  * ``unmanaged_candidate``   — everything else. THE actionable delta.
  * ``ignored``               — update/KB/hotfix entries.

Sources degrade gracefully: if Intune ``detectedApps`` 403s (the SP lacks
``DeviceManagementManagedDevices.Read.All``) the delta is built from local ARP
and ``intune_unavailable`` is set so the caller can prompt for the grant.
"""

from __future__ import annotations

import re
import socket
from typing import Any, Dict, List, Optional, Tuple

from autopackager.utils.config import get_config
from autopackager.utils.logger import get_logger

logger = get_logger(__name__)

BUCKETS = (
    "managed",
    "known_packageable",
    "standard_os_component",
    "store_app",
    "unmanaged_candidate",
    "ignored",
)

# MSIX / Store / provisioned apps come back from Intune detectedApps as package-
# family identifiers: "Microsoft.WindowsTerminal", "MicrosoftCorporationII.Quick
# Assist". They are NOT Win32 packaging candidates (managed via Store
# integration, not .intunewin), so they're bucketed out. The discriminator: no
# spaces AND the segment after the first dot is CamelCase (uppercase start) —
# this matches PFNs but NOT vendor names like "Node.js" (lowercase "js").
_STORE_APP = re.compile(r"^[A-Za-z0-9]+\.[A-Z][\w.]*$")
# Intune detectedApps reports the signing-cert subject as the publisher, e.g.
# "CN=Microsoft Corporation, O=Microsoft Corporation, L=Redmond, S=Washington,
# C=US" — pull the human CN out.
_CN = re.compile(r"\bCN=([^,]+)")

_HOSTNAME = socket.gethostname()

# --- normalization ---------------------------------------------------------

# A trailing parenthetical that names an arch / edition / install scope, e.g.
# "(64-bit)", "(x64 edition)", "(User)" — stripped so versions/editions of the
# same product normalize together.
_ARCH_EDITION = re.compile(
    r"\s*\((?:[^)]*\b(?:x64|x86|64-bit|32-bit|64 bit|32 bit|amd64|arm64|"
    r"user|machine|per-user|per-machine|edition)\b[^)]*)\)\s*$",
    re.I,
)
_TRAILING_VERSION = re.compile(r"\s+v?\d+(?:\.\d+){1,3}\b.*$")
_PUBLISHER_NOISE = re.compile(
    r"[\s,]+(?:inc|llc|ltd|corp|corporation|gmbh|co|pbc|foundation|software|"
    r"technologies|technology|team|systems|labs)\.?\b.*$",
    re.I,
)


def normalize_name(name: Optional[str]) -> str:
    """Collapse a DisplayName to a comparable key (no arch/edition/version)."""
    if not name:
        return ""
    n = _ARCH_EDITION.sub("", str(name).strip())
    n = _TRAILING_VERSION.sub("", n)
    return re.sub(r"\s+", " ", n).strip().lower()


def clean_publisher(publisher: Optional[str]) -> Optional[str]:
    """Human-friendly publisher: pull ``CN=`` out of an X.509 DN (the form Intune
    detectedApps reports), else return as-is."""
    if not publisher:
        return publisher
    m = _CN.search(str(publisher))
    return m.group(1).strip().strip('"') if m else str(publisher).strip()


def normalize_publisher(publisher: Optional[str]) -> str:
    """Collapse publisher variants (``RealNetworks, Inc.`` ≈ ``Realnetworks``)."""
    if not publisher:
        return ""
    p = _PUBLISHER_NOISE.sub("", clean_publisher(publisher) or "")
    return re.sub(r"[^a-z0-9]+", "", p.lower())


def _is_store_app(name: Optional[str]) -> bool:
    """True for MSIX/Store package-family names (dotted, no spaces)."""
    if not name:
        return False
    n = str(name).strip()
    return " " not in n and "." in n and bool(_STORE_APP.match(n))


# --- catalog / managed indexes ---------------------------------------------

def _catalog_index(catalog) -> List[Tuple[str, str]]:
    """[(normalized_name, entry_id)] from catalog product/PE names."""
    out: List[Tuple[str, str]] = []
    for e in catalog.entries:
        for nm in (getattr(e, "product_name_pattern", None), getattr(e, "pe_product_name", None)):
            if nm:
                out.append((normalize_name(nm), e.id))
    return out


def _catalog_match(norm_name: str, catalog_index: List[Tuple[str, str]]) -> Optional[str]:
    """Return the catalog entry id matching ``norm_name``, or None.

    Exact match always wins. A fuzzy substring match (either direction) requires
    BOTH names to be >= 4 chars — otherwise a short name lands inside an
    unrelated longer one (the real bug: "git" is a substring of "snagit", so
    Git matched the snagit-2023 entry). Short names match only exactly.
    """
    if not norm_name:
        return None
    for cnorm, cid in catalog_index:
        if not cnorm:
            continue
        if cnorm == norm_name:
            return cid
        if (len(cnorm) >= 4 and len(norm_name) >= 4
                and (cnorm in norm_name or norm_name in cnorm)):
            return cid
    return None


# --- classification --------------------------------------------------------

def classify(row: Dict[str, Any], managed_index: set, catalog_index, os_patterns, ignore_patterns) -> str:
    name = row.get("name") or ""
    norm = normalize_name(name)
    if any(p.search(name) for p in ignore_patterns):
        return "ignored"
    if norm and norm in managed_index:
        return "managed"
    if row.get("system_component") or any(p.search(name) for p in os_patterns):
        return "standard_os_component"
    if _is_store_app(name):
        return "store_app"
    if _catalog_match(norm, catalog_index):
        return "known_packageable"
    return "unmanaged_candidate"


def _compile(patterns) -> list:
    out = []
    for p in patterns or []:
        try:
            out.append(re.compile(p, re.I))
        except re.error as exc:
            logger.warning("Bad software_delta pattern; skipping", pattern=p, error=str(exc))
    return out


# --- delta build -----------------------------------------------------------

def build_delta(source: str = "both", graph_client=None, config: Optional[dict] = None) -> Dict[str, Any]:
    """Build the unmanaged-software delta.

    ``source`` ∈ {``intune``, ``local``, ``both``}. ``graph_client`` is required
    for the Intune source and for the managed (published-app) set; pass None for
    a local-only, offline delta. Never raises — source failures land in
    ``errors`` and the buckets reflect whatever was collected.
    """
    cfg = (config or get_config()).get("software_delta", {}) or {}
    os_patterns = _compile(cfg.get("microsoft_os_components"))
    ignore_patterns = _compile(cfg.get("ignore_patterns"))

    from autopackager.utils import installer_catalog
    catalog = installer_catalog.load_catalog()
    catalog_index = _catalog_index(catalog)

    errors: List[str] = []

    # Managed = currently-published Intune Win32 apps.
    managed_index: set = set()
    if graph_client is not None:
        try:
            for a in graph_client.get_win32_apps().get("value", []) or []:
                managed_index.add(normalize_name(a.get("displayName")))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"managed apps unavailable: {exc}")

    # Installed inventory, de-duped across sources by (normalized name, publisher).
    installed: Dict[Tuple[str, str], Dict[str, Any]] = {}

    def add(name, publisher, version, source_tag, device_count=None, system_component=False):
        norm = normalize_name(name)
        if not norm:
            return
        publisher = clean_publisher(publisher)
        key = (norm, normalize_publisher(publisher))
        row = installed.get(key)
        if row is None:
            row = {"name": name, "publisher": publisher, "version": version,
                   "device_count": device_count, "system_component": bool(system_component),
                   "sources": set()}
            installed[key] = row
        row["sources"].add(source_tag)
        if system_component:
            row["system_component"] = True
        if device_count is not None:
            row["device_count"] = max(row.get("device_count") or 0, device_count)
        if not row.get("version") and version:
            row["version"] = version

    intune_unavailable = False
    if source in ("intune", "both"):
        if graph_client is None:
            errors.append("intune source requested but no graph client provided")
            intune_unavailable = True
        else:
            try:
                for a in graph_client.list_detected_apps():
                    add(a.get("displayName"), a.get("publisher"), a.get("version"),
                        "intune", a.get("deviceCount"))
            except Exception as exc:  # noqa: BLE001
                intune_unavailable = True
                errors.append(f"detectedApps unavailable (need DeviceManagementManagedDevices.Read.All?): {exc}")

    if source in ("local", "both"):
        from autopackager.utils.arp import read_local_arp
        rows = read_local_arp()
        for r in rows:
            add(r.get("name"), r.get("publisher"), r.get("version"),
                f"arp:{_HOSTNAME}", None, r.get("system_component"))
        if not rows:
            errors.append("local ARP returned no rows (non-Windows host?)")

    buckets: Dict[str, List[Dict[str, Any]]] = {b: [] for b in BUCKETS}
    for row in installed.values():
        bucket = classify(row, managed_index, catalog_index, os_patterns, ignore_patterns)
        row["bucket"] = bucket
        row["in_catalog"] = _catalog_match(normalize_name(row["name"]), catalog_index)
        row["sources"] = sorted(row["sources"])
        buckets[bucket].append(row)

    def _sort(rows):
        return sorted(rows, key=lambda r: (-(r.get("device_count") or 0), (r.get("name") or "").lower()))

    counts = {b: len(v) for b, v in buckets.items()}
    return {
        "source": source,
        "hostname": _HOSTNAME,
        "intune_unavailable": intune_unavailable,
        "counts": counts,
        "total_installed": sum(counts.values()),
        "candidates": _sort(buckets["unmanaged_candidate"]),
        "known_packageable": _sort(buckets["known_packageable"]),
        "standard_os_components": _sort(buckets["standard_os_component"]),
        "managed": _sort(buckets["managed"]),
        "errors": errors,
    }
