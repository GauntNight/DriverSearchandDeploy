"""Claude research bridge — the catalog-MISS path.

When a dropped installer doesn't match the catalog, this bridge has Claude
inspect the *real* installer file and produce the two facts the deterministic
pipeline is missing: a silent install command and (for EXEs) a detection rule.
The result is written into the installer-catalog overlay via the EXISTING
catalog write path, so a SECOND run of the same app resolves as a HIT — the
"system visibly learns" beat in the demo.

Three modes, selected by ``DEMO_CLAUDE_MODE`` (or the ``mode`` argument):

  * ``live``   — a real, cold research run. Prefers the ``claude-agent-sdk``
                 Python package; falls back to the ``claude -p`` CLI with
                 ``--output-format stream-json``. Most impressive, slowest,
                 small risk of wandering.
  * ``replay`` — stream a previously captured run from
                 ``demo/fixtures/claude_stream_<app>.ndjson``. Zero risk;
                 honest when disclosed.
  * ``off``    — skip research entirely (hit-only demo).

Every line the model emits is published to the job's Redis channel
(``demo.events``) so the right-hand console shows the research live, and the AI
lamp is driven ready → thinking → ready around the run.

SECURITY (demo/README §6): this bridge is OPERATOR-SIDE ONLY. It runs Claude
with an allowlisted toolset (Read, Bash, Write) scoped to the demo sandbox dir.
It is never shipped to a customer endpoint.

BILLING NOTE: as of 2026-06-15, both ``claude -p`` and Agent SDK usage on
subscription plans draw from a separate monthly Agent SDK credit pool, distinct
from interactive limits. Decide subscription vs API-key billing before relying
on the live path; an exhausted pool surfaces as a lamp ``error`` (not a hang).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Optional

from autopackager.utils.logger import get_logger
from demo import events
from demo.intake import Analysis, SANDBOX_DIR

logger = get_logger(__name__)

_FIXTURES = Path(__file__).resolve().parent / "fixtures"

# The contract the bridge asks the model to emit as its final fenced ```json
# block. Kept small and deterministic so parsing is robust on stage.
_RESULT_KEYS = (
    "install_command_template",
    "uninstall_command_template",
    "detection_rules",
    "installer_family",
    "product_name",
    "publisher",
)

# The version-check ("refresh brain") contract — the small structured result the
# model emits as its final fenced ```json block (spec §2).
_VERSION_KEYS = (
    "latest_version",
    "download_url",
    "is_newer",
)

# The installer-acquisition contract — used when a queued candidate has NO known
# source URL and the agent must FIND the official installer. Returns the URL plus
# where it came from (provenance) and a confidence so the operator can judge it
# before anything is downloaded/installed. LIVE-ONLY (replay/off don't fabricate
# URLs for arbitrary apps — they park the item for a manual installer drop).
_ACQUIRE_KEYS = (
    "download_url",
    "provenance",
    "confidence",
    "product_name",
)


def get_mode(explicit: Optional[str] = None) -> str:
    mode = (explicit or os.environ.get("DEMO_CLAUDE_MODE") or "replay").lower()
    return mode if mode in ("live", "replay", "off") else "replay"


def _research_prompt(analysis: Analysis) -> str:
    """Build the operator-side research prompt for the model."""
    meta = json.dumps(analysis.metadata or {}, indent=2)[:1500]
    return (
        "You are packaging a Windows application for Microsoft Intune (Win32 "
        "app). I have an installer on disk that is NOT in our catalog yet. "
        "Inspect it and determine how to install it silently and how Intune "
        "should detect that it is installed.\n\n"
        f"Installer file: {analysis.path}\n"
        f"Type: {analysis.kind.upper()}\n"
        f"Extracted metadata:\n{meta}\n\n"
        "Steps:\n"
        "1. Read what you can about the installer (you may Read the file header "
        "and Bash tools like `file`/`strings` are available, scoped to the "
        "sandbox).\n"
        "2. Decide the silent install command. Use a template with the literal "
        "placeholder {installer_filename} where the filename goes — e.g. "
        "`msiexec /i {installer_filename} /qn /norestart` for an MSI, or "
        "`{installer_filename} /VERYSILENT /NORESTART` for an Inno Setup EXE.\n"
        "3. For an EXE, also propose at least one Intune detection rule as a "
        "list of objects, each with a `kind` "
        "(registry_version|registry_exists|file_exists|file_version) and the "
        "fields that kind needs (key, value_name, operator, value / path, "
        "file). For an MSI you may leave detection_rules empty — the pipeline "
        "derives a ProductCode rule automatically.\n\n"
        "When done, emit EXACTLY ONE fenced json block as your final message "
        "with this shape (no prose after it):\n"
        "```json\n"
        "{\n"
        '  "install_command_template": "...",\n'
        '  "uninstall_command_template": "..." ,\n'
        '  "detection_rules": [],\n'
        '  "installer_family": "msi|inno_setup|nsis|wix_burn|msft_bootstrapper|custom",\n'
        '  "product_name": "...",\n'
        '  "publisher": "..."\n'
        "}\n"
        "```\n"
    )


# --- Catalog write-back -----------------------------------------------------

def _apply_catalog_result(analysis: Analysis, result: Dict[str, Any]) -> Optional[str]:
    """Write the researched install command + detection rule into the overlay.

    Returns the new catalog entry id, or None on failure. Uses the EXISTING
    ``add_msi_entry`` / ``add_exe_entry`` write paths so a re-run resolves HIT.
    """
    from autopackager.utils import installer_catalog

    template = (result.get("install_command_template") or "").strip()
    if not template:
        # Safe deterministic fallback so the pipeline can still proceed.
        template = (
            "msiexec /i {installer_filename} /qn /norestart"
            if analysis.kind == "msi"
            else "{installer_filename} /S"
        )

    try:
        if analysis.kind == "msi":
            entry = installer_catalog.add_msi_entry(
                analysis.metadata or {},
                install_command_template=template,
                notes="Researched by Claude bridge (demo)",
            )
        else:
            detection_rules = result.get("detection_rules") or []
            entry = installer_catalog.add_exe_entry(
                analysis.metadata or {},
                install_command_template=template,
                installer_family=result.get("installer_family"),
                detection_rules=detection_rules,
                sha256=analysis.sha256,
                notes="Researched by Claude bridge (demo)",
            )
        return entry.id
    except Exception as exc:  # noqa: BLE001
        logger.error("Catalog write-back failed", error=str(exc))
        return None


def _parse_last_json_block(text: str) -> Optional[Dict[str, Any]]:
    """Extract the last fenced ```json block (or last bare {...}) from text."""
    if not text:
        return None
    blocks = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = blocks[-1] if blocks else None
    if candidate is None:
        # last resort: a bare top-level object
        m = re.findall(r"(\{(?:[^{}]|\{[^{}]*\})*\})", text, re.DOTALL)
        candidate = m[-1] if m else None
    if not candidate:
        return None
    try:
        obj = json.loads(candidate)
        return {k: obj.get(k) for k in _RESULT_KEYS if k in obj}
    except (ValueError, TypeError):
        return None


# --- Replay mode ------------------------------------------------------------

def _fixture_for(analysis: Analysis) -> Optional[Path]:
    """Pick a replay fixture for this app, falling back to a generic one."""
    slug = re.sub(r"[^a-z0-9]+", "-", (analysis.product_name or analysis.filename or "").lower()).strip("-")
    candidates = []
    if slug:
        candidates.append(_FIXTURES / f"claude_stream_{slug}.ndjson")
    candidates.append(_FIXTURES / f"claude_stream_{analysis.kind}.ndjson")
    candidates.append(_FIXTURES / "claude_stream_generic.ndjson")
    for c in candidates:
        if c.exists():
            return c
    return None


def _run_replay(job_id: Any, analysis: Analysis) -> Dict[str, Any]:
    """Stream a captured run and apply its catalog_result.

    Replay fixture format (one JSON object per line):
      {"text": "...", "level": "info", "delay_ms": 400}   # console line
      {"catalog_result": { ...result keys... }}            # final, applies write-back
    """
    fixture = _fixture_for(analysis)
    if fixture is None:
        events.publish_claude_event(
            job_id, "No replay fixture found; using deterministic fallback.",
            level="warn",
        )
        result: Dict[str, Any] = {}
    else:
        events.publish_claude_event(
            job_id, f"[replay] streaming captured research: {fixture.name}",
        )
        result = {}
        for line in fixture.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except (ValueError, TypeError):
                continue
            if "catalog_result" in obj:
                result = obj["catalog_result"] or {}
                continue
            delay = obj.get("delay_ms", 350)
            time.sleep(min(max(delay, 0), 4000) / 1000.0)
            events.publish_claude_event(
                job_id, obj.get("text", ""), level=obj.get("level", "info"),
            )
    entry_id = _apply_catalog_result(analysis, result)
    return {"entry_id": entry_id, "mode": "replay"}


# --- Live mode (SDK preferred, CLI fallback) --------------------------------

def _run_live_sdk(job_id: Any, analysis: Analysis) -> Optional[Dict[str, Any]]:
    """Try the claude-agent-sdk path. Returns None if the SDK isn't available."""
    try:
        from claude_agent_sdk import query, ClaudeAgentOptions  # type: ignore
    except Exception:
        return None

    events.publish_claude_event(job_id, "[live] AutoPackager research session opening…")
    options = ClaudeAgentOptions(
        allowed_tools=["Read", "Bash", "Write"],
        cwd=str(SANDBOX_DIR),
    )
    collected: list[str] = []

    import asyncio

    async def _go():
        async for message in query(prompt=_research_prompt(analysis), options=options):
            text = _stringify_sdk_message(message)
            if text:
                collected.append(text)
                events.publish_claude_event(job_id, text)

    asyncio.run(_go())
    result = _parse_last_json_block("\n".join(collected)) or {}
    entry_id = _apply_catalog_result(analysis, result)
    return {"entry_id": entry_id, "mode": "live-sdk"}


def _stringify_sdk_message(message: Any) -> str:
    """Best-effort one-line rendering of an SDK message/tool event."""
    try:
        # Most SDK message objects expose .content blocks with .text or tool info
        content = getattr(message, "content", None)
        if isinstance(content, list):
            parts = []
            for block in content:
                t = getattr(block, "text", None)
                if t:
                    parts.append(t)
                else:
                    name = getattr(block, "name", None)
                    if name:
                        parts.append(f"⚙ tool: {name}")
            return " ".join(parts).strip()
        if content:
            return str(content)
        return ""
    except Exception:
        return ""


def _run_live_cli(job_id: Any, analysis: Analysis) -> Dict[str, Any]:
    """Fallback live path via `claude -p ... --output-format stream-json`."""
    cmd = [
        "claude", "-p", _research_prompt(analysis),
        "--output-format", "stream-json", "--verbose",
        "--allowedTools", "Read,Bash,Write",
    ]
    events.publish_claude_event(job_id, "[live] launching AutoPackager research run…")
    collected: list[str] = []
    try:
        proc = subprocess.Popen(
            cmd, cwd=str(SANDBOX_DIR), stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, bufsize=1,
        )
    except FileNotFoundError:
        events.publish_claude_event(
            job_id, "research engine not found on PATH; using deterministic fallback.",
            level="error",
        )
        entry_id = _apply_catalog_result(analysis, {})
        return {"entry_id": entry_id, "mode": "fallback"}

    assert proc.stdout is not None
    for raw in proc.stdout:
        raw = raw.strip()
        if not raw:
            continue
        line_text = _render_ndjson_line(raw)
        if line_text:
            collected.append(line_text)
            events.publish_claude_event(job_id, line_text)
    proc.wait(timeout=10)
    result = _parse_last_json_block("\n".join(collected)) or {}
    entry_id = _apply_catalog_result(analysis, result)
    return {"entry_id": entry_id, "mode": "live-cli"}


def _render_ndjson_line(raw: str) -> str:
    """Turn one claude stream-json NDJSON line into a console-friendly string.

    Tolerant: unknown shapes pass through truncated rather than being dropped,
    so the audience always sees motion.
    """
    try:
        obj = json.loads(raw)
    except (ValueError, TypeError):
        return raw[:300]
    typ = obj.get("type")
    if typ == "assistant":
        msg = obj.get("message", {})
        parts = []
        for block in msg.get("content", []) or []:
            if block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                parts.append(f"⚙ tool: {block.get('name')}")
        return " ".join(p for p in parts if p).strip()[:500]
    if typ == "tool_result":
        return "↳ tool result received"
    if typ == "result":
        return "✓ research run complete"
    return ""


# --- Public entry point -----------------------------------------------------

def research_and_learn(
    job_id: Any, analysis: Analysis, mode: Optional[str] = None
) -> Dict[str, Any]:
    """Run the research bridge for a catalog-miss installer.

    Returns ``{"entry_id": <new catalog id or None>, "mode": <mode>}``. Drives
    the AI lamp thinking → ready (or error). Never raises into the caller — a
    bridge failure publishes an error line and returns a fallback result so the
    pipeline can still proceed deterministically.
    """
    mode = get_mode(mode)
    if mode == "off":
        events.publish_claude_event(
            job_id, "Research bridge disabled (DEMO_CLAUDE_MODE=off).", level="warn",
        )
        return {"entry_id": None, "mode": "off"}

    events.publish_lamp(job_id, "thinking", "researching package…")
    events.publish_claude_event(
        job_id, f"Catalog miss — invoking research agent ({mode}).",
    )
    try:
        if mode == "replay":
            out = _run_replay(job_id, analysis)
        else:  # live
            out = _run_live_sdk(job_id, analysis) or _run_live_cli(job_id, analysis)
        if out.get("entry_id"):
            events.publish_claude_event(
                job_id, f"Catalog updated → entry '{out['entry_id']}'. "
                "The system just learned this installer.",
            )
        events.publish_lamp(job_id, "ready", "authenticated · standing by")
        return out
    except Exception as exc:  # noqa: BLE001
        logger.error("Research bridge failed", error=str(exc))
        events.publish_claude_event(job_id, f"Research bridge error: {exc}", level="error")
        events.publish_lamp(job_id, "error", "research failed")
        # Deterministic fallback so the demo can still proceed.
        entry_id = _apply_catalog_result(analysis, {})
        return {"entry_id": entry_id, "mode": "error-fallback"}


# === Version-check brain (the "refresh" — spec §2) ==========================
#
# Given an app's known source URL and currently-deployed version, ask the model
# what the latest available version is. Returns a small structured result; the
# caller (the refresh endpoint or the daily Beat task) decides what to do with
# ``is_newer``. This is MORE deterministic than full packaging research (one
# focused lookup, no file authoring), so ``live`` is lower-risk here — but the
# same three modes apply.


def _version_check_prompt(app_label: str, current_version: str, source_url: str) -> str:
    """Focused version-check prompt (NOT a full packaging research run)."""
    return (
        "You are tracking new releases of a Windows application we manage in "
        "Microsoft Intune. Check the vendor source and report the latest "
        "available version and its direct download URL.\n\n"
        f"Application: {app_label}\n"
        f"Currently deployed version: {current_version or '(unknown)'}\n"
        f"Known source URL: {source_url or '(none on file)'}\n\n"
        "Account for the vendor's version taxonomy (e.g. '2024 R2' vs "
        "'2024.2', build suffixes, release channels) when deciding what the "
        "latest version is and whether it is newer than the deployed one.\n\n"
        "When done, emit EXACTLY ONE fenced json block as your final message "
        "(no prose after it):\n"
        "```json\n"
        "{\n"
        '  "latest_version": "...",\n'
        '  "download_url": "...",\n'
        '  "is_newer": true\n'
        "}\n"
        "```\n"
    )


def _parse_version_result(text: str) -> Optional[Dict[str, Any]]:
    """Extract the last fenced ```json block and keep only version-check keys."""
    if not text:
        return None
    blocks = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = blocks[-1] if blocks else None
    if candidate is None:
        m = re.findall(r"(\{(?:[^{}]|\{[^{}]*\})*\})", text, re.DOTALL)
        candidate = m[-1] if m else None
    if not candidate:
        return None
    try:
        obj = json.loads(candidate)
    except (ValueError, TypeError):
        return None
    return {k: obj.get(k) for k in _VERSION_KEYS if k in obj}


def _decide_is_newer(latest_version: Optional[str], current_version: Optional[str],
                     model_claim: Any) -> bool:
    """Authoritative is-newer decision.

    Prefer a real version comparison over the model's self-reported flag: if we
    can parse both versions, ``compare_catalog_versions`` wins; only when we
    can't (missing/garbage version) do we trust the model's ``is_newer`` claim.
    """
    from autopackager.utils.version_comparison import compare_catalog_versions

    if latest_version and current_version:
        try:
            return compare_catalog_versions(latest_version, current_version) > 0
        except Exception:  # noqa: BLE001 -- never let comparison crash a check
            pass
    return bool(model_claim)


def _version_fixture_for(slug: str) -> Optional[Path]:
    """Pick a replay fixture for a version check, generic fallback last."""
    candidates = []
    clean = re.sub(r"[^a-z0-9]+", "-", (slug or "").lower()).strip("-")
    if clean:
        candidates.append(_FIXTURES / f"version_check_{clean}.ndjson")
    candidates.append(_FIXTURES / "version_check_generic.ndjson")
    for c in candidates:
        if c.exists():
            return c
    return None


def _run_version_replay(job_id: Any, slug: str) -> Dict[str, Any]:
    """Stream a captured version-check run; final line carries version_result."""
    fixture = _version_fixture_for(slug)
    result: Dict[str, Any] = {}
    if fixture is None:
        if job_id is not None:
            events.publish_claude_event(
                job_id, "No version-check replay fixture found.", level="warn")
        return result
    if job_id is not None:
        events.publish_claude_event(
            job_id, f"[replay] streaming version check: {fixture.name}")
    for line in fixture.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (ValueError, TypeError):
            continue
        if "version_result" in obj:
            result = obj["version_result"] or {}
            continue
        delay = obj.get("delay_ms", 300)
        time.sleep(min(max(delay, 0), 4000) / 1000.0)
        if job_id is not None:
            events.publish_claude_event(
                job_id, obj.get("text", ""), level=obj.get("level", "info"))
    return {k: result.get(k) for k in _VERSION_KEYS if k in result}


def _run_version_live(job_id: Any, app_label: str, current_version: str,
                      source_url: str) -> Dict[str, Any]:
    """Live version check. Prefers the Agent SDK, falls back to the CLI.

    Read-only by intent: the model only needs to read the vendor page, so we
    don't grant Write. Tolerant of a missing engine (returns ``{}``).
    """
    prompt = _version_check_prompt(app_label, current_version, source_url)
    collected: list[str] = []

    # SDK path
    try:
        from claude_agent_sdk import query, ClaudeAgentOptions  # type: ignore
        import asyncio

        if job_id is not None:
            events.publish_claude_event(job_id, "[live] checking vendor source…")
        options = ClaudeAgentOptions(allowed_tools=["Read", "Bash"], cwd=str(SANDBOX_DIR))

        async def _go():
            async for message in query(prompt=prompt, options=options):
                text = _stringify_sdk_message(message)
                if text:
                    collected.append(text)
                    if job_id is not None:
                        events.publish_claude_event(job_id, text)

        asyncio.run(_go())
        return _parse_version_result("\n".join(collected)) or {}
    except ImportError:
        pass  # SDK not installed — fall through to CLI

    # CLI fallback
    cmd = [
        "claude", "-p", prompt, "--output-format", "stream-json", "--verbose",
        "--allowedTools", "Read,Bash",
    ]
    if job_id is not None:
        events.publish_claude_event(job_id, "[live] launching version check…")
    try:
        proc = subprocess.Popen(
            cmd, cwd=str(SANDBOX_DIR), stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, bufsize=1,
        )
    except FileNotFoundError:
        if job_id is not None:
            events.publish_claude_event(
                job_id, "research engine not found on PATH.", level="error")
        return {}
    assert proc.stdout is not None
    for raw in proc.stdout:
        raw = raw.strip()
        if not raw:
            continue
        line_text = _render_ndjson_line(raw)
        if line_text:
            collected.append(line_text)
            if job_id is not None:
                events.publish_claude_event(job_id, line_text)
    proc.wait(timeout=10)
    return _parse_version_result("\n".join(collected)) or {}


def check_version(
    app_label: str,
    current_version: Optional[str],
    source_url: Optional[str],
    *,
    mode: Optional[str] = None,
    job_id: Any = None,
    slug: Optional[str] = None,
) -> Dict[str, Any]:
    """Check whether a newer version of ``app_label`` exists upstream.

    Returns ``{"latest_version", "download_url", "is_newer", "current_version",
    "mode"}``. Never raises — a bridge failure returns ``is_newer=False`` so the
    caller treats it as "up to date / inconclusive" rather than crashing.

    ``job_id`` is OPTIONAL: when provided, research lines + the AI lamp are
    streamed to that job's SSE channel (used by the daily Beat task if it ever
    streams). The interactive refresh endpoint drives the lamp client-side and
    passes ``job_id=None``.
    """
    mode = get_mode(mode)
    slug = slug or app_label
    base = {
        "latest_version": None, "download_url": None, "is_newer": False,
        "current_version": current_version, "mode": mode,
    }
    if mode == "off":
        return base

    if job_id is not None:
        events.publish_lamp(job_id, "thinking", "checking vendor source…")
    try:
        if mode == "replay":
            result = _run_version_replay(job_id, slug)
        else:  # live
            result = _run_version_live(job_id, app_label, current_version or "", source_url or "")
        latest = (result.get("latest_version") or "").strip() or None
        is_newer = _decide_is_newer(latest, current_version, result.get("is_newer"))
        out = {
            "latest_version": latest,
            "download_url": (result.get("download_url") or "").strip() or None,
            "is_newer": is_newer,
            "current_version": current_version,
            "mode": mode,
        }
        if job_id is not None:
            msg = (f"New version available: {latest} (deployed {current_version})"
                   if is_newer else f"Up to date ({current_version or 'unknown'}).")
            events.publish_claude_event(job_id, msg)
            events.publish_lamp(job_id, "ready", "authenticated · standing by")
        return out
    except Exception as exc:  # noqa: BLE001
        logger.error("Version check failed", app=app_label, error=str(exc))
        if job_id is not None:
            events.publish_claude_event(job_id, f"Version check error: {exc}", level="error")
            events.publish_lamp(job_id, "error", "version check failed")
        return base


# === Installer acquisition (find the URL for an unknown app) =================
#
# When a queued candidate has no catalog source URL and the version-check brain
# can't resolve one, the agent searches the web for the official installer. This
# is LIVE-ONLY by design (operator decision): replay/off don't fabricate URLs —
# they park the item for a manual installer drop. The result is NEVER acted on
# automatically; the caller surfaces the URL + provenance + confidence for an
# operator confirm before downloading/installing (the supply-chain guardrail —
# see the ChromeSetup-stub and RealPlayer-bundleware war stories).


def _find_url_prompt(name: str, publisher: str) -> str:
    """Focused 'find the official installer' prompt (web search)."""
    return (
        "You are sourcing a Windows application for silent enterprise deployment "
        "via Microsoft Intune. Find the OFFICIAL vendor download URL for a "
        "silently-installable Windows installer.\n\n"
        f"Application: {name}\n"
        f"Publisher: {publisher or '(unknown)'}\n\n"
        "Requirements:\n"
        "1. Prefer the VENDOR'S OWN site over mirrors/aggregators (no "
        "softonic/cnet/filehippo/uptodown etc.).\n"
        "2. Prefer an enterprise/offline/full installer (.msi when available, "
        "else a silent-capable .exe) over a tiny web 'stub'/'online' bootstrapper "
        "that downloads at run time — stubs break unattended install.\n"
        "3. The URL must be a DIRECT link to the installer file (ends in .msi/"
        ".exe/.zip), not a landing page.\n"
        "4. If you cannot find an official direct installer link with reasonable "
        "confidence, return an empty download_url rather than guessing.\n\n"
        "Use web search/fetch to verify the link is the official current build. "
        "When done, emit EXACTLY ONE fenced json block as your final message (no "
        "prose after it):\n"
        "```json\n"
        "{\n"
        '  "download_url": "https://vendor.example/app/Setup-x64.msi",\n'
        '  "provenance": "found on the official downloads page vendor.example/download",\n'
        '  "confidence": "high|medium|low",\n'
        '  "product_name": "..."\n'
        "}\n"
        "```\n"
    )


def _parse_acquire_result(text: str) -> Optional[Dict[str, Any]]:
    """Extract the last fenced ```json block and keep only acquisition keys."""
    if not text:
        return None
    blocks = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = blocks[-1] if blocks else None
    if candidate is None:
        m = re.findall(r"(\{(?:[^{}]|\{[^{}]*\})*\})", text, re.DOTALL)
        candidate = m[-1] if m else None
    if not candidate:
        return None
    try:
        obj = json.loads(candidate)
    except (ValueError, TypeError):
        return None
    return {k: obj.get(k) for k in _ACQUIRE_KEYS if k in obj}


def _run_find_url_live(job_id: Any, name: str, publisher: str) -> Dict[str, Any]:
    """Live installer search. Prefers the Agent SDK, falls back to the CLI.

    Grants web tools (WebSearch/WebFetch) plus Read/Bash so the model can find
    AND sanity-check the vendor link. Tolerant of a missing engine (returns {}).
    """
    prompt = _find_url_prompt(name, publisher)
    collected: list[str] = []

    # SDK path
    try:
        from claude_agent_sdk import query, ClaudeAgentOptions  # type: ignore
        import asyncio

        if job_id is not None:
            events.publish_claude_event(job_id, "[live] searching for an official installer…")
        options = ClaudeAgentOptions(
            allowed_tools=["WebSearch", "WebFetch", "Read", "Bash"],
            cwd=str(SANDBOX_DIR),
        )

        async def _go():
            async for message in query(prompt=prompt, options=options):
                text = _stringify_sdk_message(message)
                if text:
                    collected.append(text)
                    if job_id is not None:
                        events.publish_claude_event(job_id, text)

        asyncio.run(_go())
        return _parse_acquire_result("\n".join(collected)) or {}
    except ImportError:
        pass  # SDK not installed — fall through to CLI

    # CLI fallback
    cmd = [
        "claude", "-p", prompt, "--output-format", "stream-json", "--verbose",
        "--allowedTools", "WebSearch,WebFetch,Read,Bash",
    ]
    if job_id is not None:
        events.publish_claude_event(job_id, "[live] launching installer search…")
    try:
        proc = subprocess.Popen(
            cmd, cwd=str(SANDBOX_DIR), stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, bufsize=1,
        )
    except FileNotFoundError:
        if job_id is not None:
            events.publish_claude_event(
                job_id, "research engine not found on PATH.", level="error")
        return {}
    assert proc.stdout is not None
    for raw in proc.stdout:
        raw = raw.strip()
        if not raw:
            continue
        line_text = _render_ndjson_line(raw)
        if line_text:
            collected.append(line_text)
            if job_id is not None:
                events.publish_claude_event(job_id, line_text)
    proc.wait(timeout=10)
    return _parse_acquire_result("\n".join(collected)) or {}


def find_installer_url(
    name: str,
    publisher: Optional[str] = None,
    *,
    mode: Optional[str] = None,
    job_id: Any = None,
    slug: Optional[str] = None,
) -> Dict[str, Any]:
    """Find an official installer URL for ``name`` by ``publisher``.

    Returns ``{"download_url", "provenance", "confidence", "product_name",
    "mode"}`` (``download_url`` None when nothing trustworthy was found). LIVE
    ONLY: in ``replay``/``off`` returns ``download_url=None`` without fabricating
    a link, so the caller parks the item for a manual installer drop. Never
    raises — a failure returns an empty result.
    """
    mode = get_mode(mode)
    base = {
        "download_url": None, "provenance": None, "confidence": None,
        "product_name": None, "mode": mode,
    }
    if mode != "live":
        # No canned URLs for arbitrary apps — honest miss → manual drop.
        return base

    if job_id is not None:
        events.publish_lamp(job_id, "thinking", "searching for an installer…")
    try:
        result = _run_find_url_live(job_id, name, publisher or "")
        url = (result.get("download_url") or "").strip() or None
        out = {
            "download_url": url,
            "provenance": (result.get("provenance") or "").strip() or None,
            "confidence": (result.get("confidence") or "").strip().lower() or None,
            "product_name": (result.get("product_name") or "").strip() or None,
            "mode": mode,
        }
        if job_id is not None:
            msg = (f"Found a candidate installer: {url}" if url
                   else "No official installer URL found — manual drop needed.")
            events.publish_claude_event(job_id, msg)
            events.publish_lamp(job_id, "ready", "authenticated · standing by")
        return out
    except Exception as exc:  # noqa: BLE001
        logger.error("Installer search failed", app=name, error=str(exc))
        if job_id is not None:
            events.publish_claude_event(job_id, f"Installer search error: {exc}", level="error")
            events.publish_lamp(job_id, "error", "installer search failed")
        return base
