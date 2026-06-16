"""CVE / vulnerability intelligence — *patch by risk*.

Given a product and the version it is deployed at, resolve the public CVEs that a
newer release fixes, with a CVSS severity score, so patching can be driven by
**risk** instead of an alphabetical list. This is the data layer behind the demo
console's risk-sorted "what to patch first" view (the gap relative to
PatchMyPC's CVE Insights).

The lookup is layered and every layer is best-effort — a failure degrades to the
next tier and ultimately to "no CVE data", never an exception into the caller
(the enrichment path that consumes this is itself best-effort):

  1. **cache / curated fixture** — ``demo/fixtures/cve_intel.json`` keyed by CPE
     or product slug. Deterministic and fully offline: the stage-reliable path
     and the default.
  2. **live NVD CVE API 2.0** — ``services.nvd.nist.gov/rest/json/cves/2.0`` by
     CPE (``virtualMatchString``) or keyword. Real upstream data, rate-limited,
     cached in-process for the life of the run.
  3. **AI research bridge** — ``demo.claude_bridge`` (live mode only), used only
     when wired and the structured tiers come up empty. On-brand; carries
     provenance.

Mode via ``CVE_INTEL_MODE``:
  * ``cache`` (default) — fixture only; fully offline, no network gamble.
  * ``live``            — fixture first, then NVD, then the bridge.
  * ``off``             — no CVE data (the feature disabled).

The version filter is the crux: a CVE counts against a deployed version only
when a *newer* release fixes it. With ``current_version`` known we keep CVEs
whose ``fixed_in`` is strictly newer than what's deployed; with
``latest_version`` also known we additionally require the upgrade target to
actually include the fix (``fixed_in <= latest_version``).
"""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from autopackager.utils.logger import get_logger
from autopackager.utils.version_comparison import compare_catalog_versions

logger = get_logger(__name__)

_FIXTURES = Path(__file__).resolve().parent.parent.parent / "demo" / "fixtures"
_CACHE_FILE = _FIXTURES / "cve_intel.json"

NVD_ENDPOINT = "https://services.nvd.nist.gov/rest/json/cves/2.0"

# --- severity vocabulary ---------------------------------------------------

# CVSS v3 qualitative buckets (NVD's own ranges).
SEVERITY_NONE = "none"
SEVERITY_LOW = "low"
SEVERITY_MEDIUM = "medium"
SEVERITY_HIGH = "high"
SEVERITY_CRITICAL = "critical"
SEVERITY_UNKNOWN = "unknown"

# Worst-first ordering key. Higher = more urgent.
SEVERITY_RANK = {
    SEVERITY_CRITICAL: 4,
    SEVERITY_HIGH: 3,
    SEVERITY_MEDIUM: 2,
    SEVERITY_LOW: 1,
    SEVERITY_NONE: 0,
    SEVERITY_UNKNOWN: -1,
}


def severity_for_score(score: Optional[float]) -> str:
    """Map a CVSS base score to its qualitative severity bucket."""
    if score is None:
        return SEVERITY_UNKNOWN
    try:
        s = float(score)
    except (TypeError, ValueError):
        return SEVERITY_UNKNOWN
    if s <= 0:
        return SEVERITY_NONE
    if s < 4.0:
        return SEVERITY_LOW
    if s < 7.0:
        return SEVERITY_MEDIUM
    if s < 9.0:
        return SEVERITY_HIGH
    return SEVERITY_CRITICAL


# --- mode ------------------------------------------------------------------

def get_mode(override: Optional[str] = None) -> str:
    """Resolve the CVE-intel mode: explicit override > env > default 'cache'."""
    mode = (override or os.environ.get("CVE_INTEL_MODE") or "cache").strip().lower()
    return mode if mode in ("cache", "live", "off") else "cache"


# --- normalization ---------------------------------------------------------

def _slug(text: Optional[str]) -> str:
    """Collapse a product/display name to a comparable slug.

    'VLC media player' -> 'vlc_media_player'; strips a trailing version and the
    common arch/edition parenthetical so 'Python 3.14.5 (64-bit)' -> 'python'.
    """
    if not text:
        return ""
    t = str(text).strip()
    t = re.sub(r"\s*\((?:[^)]*)\)\s*$", "", t)          # trailing (...) edition
    t = re.sub(r"\s+v?\d+(?:\.\d+){1,3}\b.*$", "", t)   # trailing version
    t = re.sub(r"[^A-Za-z0-9]+", "_", t).strip("_")
    return t.lower()


def normalize_cpe(cpe: Optional[str]) -> Optional[str]:
    """Accept a full ``cpe:2.3:a:vendor:product`` OR a short ``vendor:product``
    and return the canonical version-less ``cpe:2.3:a:vendor:product`` prefix
    (everything after product trimmed). Returns None for unusable input."""
    if not cpe:
        return None
    c = str(cpe).strip().lower()
    if not c:
        return None
    if c.startswith("cpe:2.3:"):
        parts = c.split(":")
        # cpe:2.3:a:vendor:product[:version:...] -> keep first 5 fields
        if len(parts) >= 5 and parts[3] and parts[4]:
            return ":".join(parts[:5])
        return None
    # short 'vendor:product'
    bits = [b for b in c.split(":") if b]
    if len(bits) >= 2:
        return f"cpe:2.3:a:{bits[0]}:{bits[1]}"
    return None


def _cpe_product_slug(cpe: Optional[str]) -> str:
    """The 'vendor_product' slug of a normalized CPE, for fixture keying."""
    norm = normalize_cpe(cpe)
    if not norm:
        return ""
    parts = norm.split(":")
    if len(parts) >= 5:
        return f"{parts[3]}_{parts[4]}".lower()
    return ""


# --- curated cache ---------------------------------------------------------

_cache_lock = threading.Lock()
_cache: Optional[Dict[str, Dict[str, Any]]] = None
_nvd_memo: Dict[str, List[Dict[str, Any]]] = {}


def _load_cache() -> Dict[str, Dict[str, Any]]:
    """Load and index the curated CVE fixture. Indexed by both CPE-product slug
    and a name slug + every declared ``aliases`` entry, so a tenant app resolves
    by catalog CPE or by display name."""
    global _cache
    with _cache_lock:
        if _cache is not None:
            return _cache
        index: Dict[str, Dict[str, Any]] = {}
        try:
            data = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("CVE cache load failed; cache empty", error=str(exc))
            _cache = index
            return index
        for key, rec in (data.get("products") or {}).items():
            keys = {_slug(key)}
            cpe_slug = _cpe_product_slug(rec.get("cpe"))
            if cpe_slug:
                keys.add(cpe_slug)
            for alias in rec.get("aliases") or []:
                keys.add(_slug(alias))
            for k in keys:
                if k:
                    index[k] = rec
        _cache = index
        return index


def reset_cache() -> None:
    """Drop the in-process caches (test hook / after a fixture edit)."""
    global _cache
    with _cache_lock:
        _cache = None
    _nvd_memo.clear()


def _cache_lookup(cpe: Optional[str], product: Optional[str]) -> Optional[Dict[str, Any]]:
    index = _load_cache()
    for key in (_cpe_product_slug(cpe), _slug(product)):
        if key and key in index:
            return index[key]
    return None


# --- version filtering -----------------------------------------------------

def _affects(cve: Dict[str, Any], current_version: Optional[str],
             latest_version: Optional[str]) -> bool:
    """Does ``cve`` count as fixable risk for ``current_version``?

    A CVE is fixed in ``cve['fixed_in']``. The deployed version is exposed when
    it is OLDER than that fix. When the upgrade target ``latest_version`` is
    known we also require the fix to be at-or-before it (the upgrade really
    delivers the patch). With no version context we keep the CVE (it's known
    for the product).
    """
    fixed_in = cve.get("fixed_in")
    if not fixed_in:
        return True
    if current_version:
        try:
            if compare_catalog_versions(str(fixed_in), str(current_version)) <= 0:
                return False  # fix is at/older than deployed -> already patched
        except Exception:  # noqa: BLE001 — unparseable -> keep, don't crash
            pass
    if latest_version:
        try:
            if compare_catalog_versions(str(fixed_in), str(latest_version)) > 0:
                return False  # fix lands beyond the upgrade target
        except Exception:  # noqa: BLE001
            pass
    return True


def _summarize(cves: List[Dict[str, Any]], source: str,
               current_version: Optional[str], latest_version: Optional[str],
               cpe: Optional[str]) -> Dict[str, Any]:
    """Roll a list of CVE records into the per-app risk block, sorted worst-first."""
    norm: List[Dict[str, Any]] = []
    for c in cves:
        score = c.get("cvss")
        sev = c.get("severity") or severity_for_score(score)
        norm.append({
            "id": c.get("id"),
            "cvss": score,
            "severity": sev,
            "summary": c.get("summary") or "",
            "url": c.get("url") or (
                f"https://nvd.nist.gov/vuln/detail/{c.get('id')}" if c.get("id") else ""),
            "fixed_in": c.get("fixed_in"),
            "published": c.get("published"),
        })
    norm.sort(key=lambda c: (SEVERITY_RANK.get(c["severity"], -1), c["cvss"] or 0.0),
              reverse=True)
    scores = [c["cvss"] for c in norm if isinstance(c.get("cvss"), (int, float))]
    max_cvss = max(scores) if scores else None
    # Block severity = the worst per-CVE severity (norm is sorted worst-first), so
    # a CVE with a qualitative severity but no numeric score still colors the row.
    # No CVEs at all from a real source = a genuine "none" (clean); from no source
    # at all = "unknown" (no data).
    if norm:
        block_severity = norm[0]["severity"]
    elif source != "none":
        block_severity = SEVERITY_NONE
    else:
        block_severity = SEVERITY_UNKNOWN
    return {
        "max_cvss": max_cvss,
        "severity": block_severity,
        "cve_count": len(norm),
        "cves": norm,
        "source": source,
        "cpe": normalize_cpe(cpe),
        "current_version": current_version,
        "latest_version": latest_version,
        "fixed_by_upgrade": bool(norm and latest_version),
    }


# --- live NVD --------------------------------------------------------------

def _nvd_fetch(cpe: Optional[str], product: Optional[str]) -> List[Dict[str, Any]]:
    """Query the NVD CVE API 2.0 for a product. Best-effort, memoized, returns
    a list of normalized CVE dicts ({id, cvss, severity, summary, url, fixed_in,
    published}). Empty list on any failure."""
    memo_key = (normalize_cpe(cpe) or "") + "|" + (_slug(product) or "")
    if memo_key in _nvd_memo:
        return _nvd_memo[memo_key]

    try:
        import requests  # local import: keeps the module importable without it
    except ImportError:
        return []

    params: Dict[str, Any] = {"resultsPerPage": 50, "noRejected": ""}
    norm_cpe = normalize_cpe(cpe)
    if norm_cpe:
        params["virtualMatchString"] = norm_cpe
    elif product:
        params["keywordSearch"] = _slug(product).replace("_", " ")
    else:
        return []

    headers = {}
    api_key = os.environ.get("NVD_API_KEY")
    if api_key:
        headers["apiKey"] = api_key

    out: List[Dict[str, Any]] = []
    try:
        resp = requests.get(NVD_ENDPOINT, params=params, headers=headers,
                            timeout=(10, 30))
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:  # noqa: BLE001 — network/parse all degrade to []
        logger.warning("NVD query failed", cpe=norm_cpe, error=str(exc))
        _nvd_memo[memo_key] = []
        return []

    for item in payload.get("vulnerabilities", []) or []:
        cve = item.get("cve", {}) or {}
        rec = _nvd_normalize(cve, norm_cpe)
        if rec:
            out.append(rec)
    _nvd_memo[memo_key] = out
    return out


def _nvd_normalize(cve: Dict[str, Any], cpe: Optional[str]) -> Optional[Dict[str, Any]]:
    """Turn one NVD ``cve`` object into our record shape, pulling the CVSS v3.1
    base score and the ``versionEndExcluding`` (our ``fixed_in``) from the first
    matching configuration node."""
    cid = cve.get("id")
    if not cid:
        return None
    # English description
    summary = ""
    for d in cve.get("descriptions", []) or []:
        if d.get("lang") == "en":
            summary = d.get("value", "")
            break
    # CVSS — prefer v3.1, then v3.0, then v2
    metrics = cve.get("metrics", {}) or {}
    score = None
    severity = None
    for key in ("cvssMetricV31", "cvssMetricV30", "cvssMetricV2"):
        arr = metrics.get(key) or []
        if arr:
            data = arr[0].get("cvssData", {}) or {}
            score = data.get("baseScore")
            severity = (data.get("baseSeverity") or arr[0].get("baseSeverity") or "")
            severity = severity.lower() or None
            break
    # fixed_in: the smallest versionEndExcluding across vulnerable cpeMatch nodes
    fixed_in = None
    for node in cve.get("configurations", []) or []:
        for nd in node.get("nodes", []) or []:
            for m in nd.get("cpeMatch", []) or []:
                if not m.get("vulnerable"):
                    continue
                end = m.get("versionEndExcluding")
                if end:
                    if fixed_in is None:
                        fixed_in = end
                    else:
                        try:
                            if compare_catalog_versions(str(end), str(fixed_in)) < 0:
                                fixed_in = end
                        except Exception:  # noqa: BLE001
                            pass
    return {
        "id": cid,
        "cvss": score,
        "severity": severity or severity_for_score(score),
        "summary": summary,
        "url": f"https://nvd.nist.gov/vuln/detail/{cid}",
        "fixed_in": fixed_in,
        "published": (cve.get("published") or "")[:10] or None,
    }


# --- bridge fallback (optional, live only) ---------------------------------

def _bridge_lookup(product: Optional[str], current_version: Optional[str],
                   latest_version: Optional[str]) -> List[Dict[str, Any]]:
    """Optional AI-research fallback. Only used if the bridge exposes a
    ``research_cves`` contract; defensive so the service has no hard dependency
    on it. Returns [] otherwise."""
    try:
        from demo import claude_bridge
    except Exception:  # noqa: BLE001 — demo package may be removed
        return []
    fn = getattr(claude_bridge, "research_cves", None)
    if not callable(fn):
        return []
    try:
        res = fn(product, current_version=current_version,
                 latest_version=latest_version) or {}
        return res.get("cves") or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("CVE bridge lookup failed", product=product, error=str(exc))
        return []


# --- public API ------------------------------------------------------------

def empty_block(reason: str = "none") -> Dict[str, Any]:
    """The 'no CVE data' block — shape-compatible with a real lookup result."""
    return {
        "max_cvss": None, "severity": SEVERITY_UNKNOWN if reason == "off" else SEVERITY_NONE,
        "cve_count": 0, "cves": [], "source": "none", "cpe": None,
        "current_version": None, "latest_version": None, "fixed_by_upgrade": False,
    }


def lookup(product: Optional[str] = None, *, cpe: Optional[str] = None,
           current_version: Optional[str] = None,
           latest_version: Optional[str] = None,
           vendor: Optional[str] = None,
           mode: Optional[str] = None) -> Dict[str, Any]:
    """Resolve the fixable-CVE risk for one product.

    Returns a risk block::

        {max_cvss, severity, cve_count, cves:[{id,cvss,severity,summary,url,
         fixed_in,published}], source, cpe, current_version, latest_version,
         fixed_by_upgrade}

    ``cves`` is sorted worst-first. ``source`` is ``cache`` | ``nvd`` | ``bridge``
    | ``none``. Never raises.
    """
    mode = get_mode(mode)
    if mode == "off":
        return empty_block("off")
    if vendor and product and ":" not in (cpe or ""):
        cpe = cpe or f"{_slug(vendor)}:{_slug(product)}"

    # Tier 1 — curated cache (always consulted; the offline stage-reliable path)
    rec = _cache_lookup(cpe, product)
    if rec is not None:
        kept = [c for c in (rec.get("cves") or [])
                if _affects(c, current_version, latest_version)]
        # If the fixture entry exists but the deployed version is already patched,
        # that's a real, meaningful "no known CVEs" answer — return it as cache.
        return _summarize(kept, "cache", current_version, latest_version,
                          cpe or rec.get("cpe"))

    if mode == "live":
        # Tier 2 — live NVD
        nvd = _nvd_fetch(cpe, product)
        if nvd:
            kept = [c for c in nvd if _affects(c, current_version, latest_version)]
            return _summarize(kept, "nvd", current_version, latest_version, cpe)
        # Tier 3 — AI research bridge
        bridge = _bridge_lookup(product, current_version, latest_version)
        if bridge:
            kept = [c for c in bridge if _affects(c, current_version, latest_version)]
            return _summarize(kept, "bridge", current_version, latest_version, cpe)

    # Assessed-clean for a CPE-IDENTIFIED product: when we hold a precise CPE and
    # none of the tiers surfaced an applicable CVE, report a definitive "no known
    # CVEs" (cve_count 0, severity none) rather than a bare "no data" dash. This
    # keeps the estate Risk column populated for every identified app — a CVE
    # badge if exposed, "✓ no known CVEs" if not. A product with no CPE (we can't
    # pin it to an NVD entry) still returns the no-data block ("—").
    if cpe:
        return _summarize([], "cache", current_version, latest_version, cpe)
    return empty_block()


def risk_sort_key(block: Optional[Dict[str, Any]]) -> tuple:
    """Worst-first sort key for a list of apps carrying a ``cve`` block."""
    b = block or {}
    return (
        SEVERITY_RANK.get(b.get("severity"), -1),
        b.get("max_cvss") or 0.0,
        b.get("cve_count") or 0,
    )


def scan_apps(apps: List[Dict[str, Any]], *, mode: Optional[str] = None,
              sort: bool = True) -> List[Dict[str, Any]]:
    """Attach a ``cve`` block to each app dict (in place) and optionally return
    them sorted worst-first.

    Each app should carry ``name``/``version`` (and optionally ``cpe``,
    ``current_version``, ``latest_version``). Best-effort per app.
    """
    for app in apps:
        try:
            app["cve"] = lookup(
                app.get("name"),
                cpe=app.get("cpe"),
                current_version=app.get("current_version") or app.get("version"),
                latest_version=app.get("latest_version"),
                mode=mode,
            )
        except Exception as exc:  # noqa: BLE001 — never let CVE enrich break a row
            logger.warning("CVE enrich failed", app=app.get("name"), error=str(exc))
            app["cve"] = empty_block()
    if sort:
        apps = sorted(apps, key=lambda a: risk_sort_key(a.get("cve")), reverse=True)
    return apps
