"""Local install validation — install → verify → discover → uninstall.

This is the pre-publish gate the operator runs by hand for every package:
actually install the thing, confirm it really landed, capture the *real*
detection facts (uninstall registry key, DisplayVersion, QuietUninstallString),
then uninstall to leave the build machine clean. Only after this passes should
the package publish to Intune.

It runs on the build machine (ADMIN_BUILD_1) by default; a Hyper-V path can be
slotted in later behind the same interface. Windows-only (uses ``winreg`` and
silent installers); on any other platform ``validate()`` returns a skipped
result so Linux CI is unaffected.

Why it also *corrects*: a guessed EXE detection rule (e.g. the generic
``...\\Uninstall\\{app}_is1`` placeholder) won't fire. Rather than just fail,
the validator snapshots the Uninstall hives before install, diffs after, and
finds the app's actual Uninstall key — yielding a correct
``registry_version`` rule and a real ``QuietUninstallString``. Those
corrections are written back to the Package (so the publish uses them) and to
the catalog entry (so the next run is right from the start).
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from autopackager.utils.logger import get_logger
from autopackager.utils.version_comparison import compare_catalog_versions

logger = get_logger(__name__)

IS_WINDOWS = sys.platform == "win32"

# Registry roots we scan for an app's ARP (Add/Remove Programs) entry.
_UNINSTALL_SUBPATH = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"


def _hive_consts():
    import winreg

    return {
        "HKEY_LOCAL_MACHINE": winreg.HKEY_LOCAL_MACHINE,
        "HKLM": winreg.HKEY_LOCAL_MACHINE,
        "HKEY_CURRENT_USER": winreg.HKEY_CURRENT_USER,
        "HKCU": winreg.HKEY_CURRENT_USER,
        "HKEY_CLASSES_ROOT": winreg.HKEY_CLASSES_ROOT,
        "HKEY_USERS": winreg.HKEY_USERS,
    }


class LocalInstallValidator:
    """Install → verify → discover → uninstall, on the local machine."""

    def __init__(self, config: Optional[dict] = None, emit: Optional[Callable[[str, str], None]] = None):
        cfg = (config or {})
        self.timeout = int(cfg.get("timeout_seconds", 600))
        self.uninstall_timeout = int(cfg.get("uninstall_timeout_seconds", 300))
        # emit(text, level) streams human progress lines (→ demo console). Optional.
        self._emit = emit or (lambda text, level="info": None)

    def emit(self, text: str, level: str = "info") -> None:
        try:
            self._emit(text, level)
        except Exception:
            pass

    # -- public ------------------------------------------------------------

    def validate(self, package, job=None) -> Dict[str, Any]:
        """Run the full local install validation for a package.

        Returns a result dict (see module docstring). ``passed`` is True when
        the installer ran AND the app was verified present (by the configured
        rule or a corrected one). Uninstall is attempted and reported; a failed
        uninstall is a loud warning, not a hard gate (the install was proven).
        """
        result: Dict[str, Any] = {
            "passed": False,
            "skipped": False,
            "installed": False,
            "install_rc": None,
            "detection_fired": False,
            "discovered": None,
            "corrected_detection_rules": None,
            "corrected_uninstall_command": None,
            "corrected_install_family": None,
            "uninstalled": False,
            "errors": [],
            "log": [],
        }

        if not IS_WINDOWS:
            result["skipped"] = True
            result["passed"] = True  # don't block non-Windows CI
            result["log"].append("local install validation skipped (not Windows)")
            return result

        installer = self._resolve_installer(package, job)
        if not installer or not installer.exists():
            result["errors"].append(f"installer not found for local validation: {installer}")
            self.emit("Install validation: installer file not found — cannot validate", "error")
            return result

        install_cmd = (getattr(package, "install_command", "") or "").strip()
        if not install_cmd:
            result["errors"].append("no install command on package")
            return result

        detection_rules = self._catalog_style_rules(package)

        # 1) Snapshot ARP before install (for discovery via diff)
        before = self._snapshot_uninstall_keys()

        # 2) Install
        self.emit(f"Install validation: installing {installer.name} silently…")
        rc, out = self._run(install_cmd, cwd=installer.parent, timeout=self.timeout)
        result["install_rc"] = rc
        result["log"].append(f"install rc={rc}")
        # rc!=0 isn't always fatal (some installers return non-zero on success),
        # so we judge by detection, not rc alone.
        if rc not in (0, 3010):  # 3010 = success, reboot required
            result["log"].append(f"install returned rc={rc} (continuing — verifying by detection)")
            self.emit(f"Installer exit code {rc} — verifying by detection rather than exit code…",
                      "warn" if rc else "info")

        # 3) Verify: did the configured detection rule fire?
        fired, fired_detail = self._eval_rules(detection_rules)
        result["detection_fired"] = fired
        result["log"].append(f"configured detection fired={fired} ({fired_detail})")

        # 4) Discover the real ARP entry (diff snapshot)
        after = self._snapshot_uninstall_keys()
        discovered = self._discover_new_entry(before, after, package)
        if discovered:
            result["discovered"] = discovered
            result["installed"] = True
            self.emit(
                f"Verified installed: '{discovered['display_name']}' "
                f"v{discovered.get('display_version') or '?'} "
                f"(reg key {discovered['key_leaf']})"
            )
        elif fired:
            result["installed"] = True
            self.emit("Verified installed (configured detection rule matched).")

        # 5) If configured rule didn't fire but we found the real entry → correct
        if not fired and discovered:
            corrected = self._build_corrected_rule(discovered)
            if corrected:
                result["corrected_detection_rules"] = [corrected]
                result["corrected_uninstall_command"] = self._silent_uninstall_command(discovered)
                self.emit(
                    "Configured detection rule did NOT match — corrected it from the "
                    f"real Uninstall key ({discovered['key_leaf']}, DisplayVersion "
                    f">= {discovered.get('display_version') or '0'}).",
                    "warn",
                )
                # Re-evaluate with the corrected rule to confirm it fires now.
                refired, _ = self._eval_rules([corrected])
                result["detection_fired"] = refired
                result["log"].append(f"corrected detection fires={refired}")

        # passed = installer ran AND app verified present (configured or corrected)
        result["passed"] = bool(result["installed"]) and bool(
            result["detection_fired"] or result["corrected_detection_rules"]
        )

        # 6) Uninstall (cleanup) — best effort, loud on failure
        self._attempt_uninstall(package, discovered, result)

        if result["passed"]:
            self.emit("Install validation PASSED — install verified and rolled back.")
        else:
            self.emit("Install validation FAILED — see console; publish will be blocked.", "error")
        return result

    # -- installer resolution ---------------------------------------------

    def _resolve_installer(self, package, job) -> Optional[Path]:
        # The packaged copy is the most reliable source.
        p = getattr(package, "installer_path", None)
        if p and Path(p).exists():
            return Path(p)
        # Fall back to the original source recorded on the job.
        if job is not None:
            src = (job.job_metadata or {}).get("installer_source") or \
                  (job.job_metadata or {}).get("download_url")
            if src:
                from autopackager.utils.msi_metadata import resolve_local_path

                local = resolve_local_path(src)
                if local and Path(local).exists():
                    return Path(local)
        return None

    def _catalog_style_rules(self, package) -> List[dict]:
        """Return the package's detection rules in catalog dict form.

        Packaging may store either catalog-style dicts (``kind``) or Graph-style
        dicts (``@odata.type``). We evaluate catalog-style; Graph-style rules are
        converted best-effort for evaluation.
        """
        rules = getattr(package, "detection_rules", None) or []
        out: List[dict] = []
        for r in rules:
            if not isinstance(r, dict):
                continue
            if "kind" in r:
                out.append(r)
            elif r.get("@odata.type", "").endswith("win32LobAppProductCodeRule"):
                out.append({"kind": "msi_product_code", "product_code": r.get("productCode"),
                            "operator": r.get("productVersionOperator"), "value": r.get("productVersion")})
            elif r.get("@odata.type", "").endswith("win32LobAppRegistryRule"):
                op_map = {"version": "registry_version", "string": "registry_value",
                          "exists": "registry_exists"}
                out.append({"kind": op_map.get(r.get("operationType"), "registry_exists"),
                            "key": r.get("keyPath"), "value_name": r.get("valueName"),
                            "operator": r.get("operator"), "value": r.get("comparisonValue"),
                            "check_32bit_on_64bit": r.get("check32BitOn64System", False)})
            elif r.get("@odata.type", "").endswith("win32LobAppFileSystemRule"):
                out.append({"kind": "file_exists" if r.get("operationType") == "exists" else "file_version",
                            "path": r.get("path"), "file": r.get("fileOrFolderName"),
                            "operator": r.get("operator"), "value": r.get("comparisonValue")})
        return out

    # -- detection-rule evaluation ----------------------------------------

    def _eval_rules(self, rules: List[dict]) -> tuple:
        """Evaluate catalog-style detection rules against the live system.

        Intune detection semantics: an app is 'detected' when ALL rules match.
        Returns (all_matched, detail_string).
        """
        if not rules:
            return False, "no rules"
        details = []
        all_ok = True
        for rule in rules:
            ok, why = self._eval_one(rule)
            details.append(f"{rule.get('kind')}={ok}")
            all_ok = all_ok and ok
        return all_ok, "; ".join(details)

    def _eval_one(self, rule: dict) -> tuple:
        kind = rule.get("kind")
        try:
            if kind in ("registry_exists", "registry_value", "registry_version"):
                return self._eval_registry(rule)
            if kind == "msi_product_code":
                return self._eval_msi_product_code(rule)
            if kind in ("file_exists", "file_version"):
                return self._eval_file(rule)
        except Exception as exc:  # noqa: BLE001
            return False, f"error: {exc}"
        return False, f"unknown kind {kind}"

    def _parse_reg_path(self, key: str) -> tuple:
        """Split a catalog registry key into (root_const, subpath, force_wow32)."""
        import winreg  # noqa: F401  (ensures winreg available on this platform)

        hives = _hive_consts()
        raw = (key or "").replace("/", "\\").lstrip("\\")
        root_name, _, sub = raw.partition("\\")
        root = hives.get(root_name.upper(), hives["HKLM"])
        force_wow32 = False
        # Some catalog keys embed WOW6432Node literally; strip it and force the
        # 32-bit view so the read works on a 64-bit OS.
        if "WOW6432Node\\" in sub:
            sub = sub.replace("WOW6432Node\\", "")
            force_wow32 = True
        return root, sub, force_wow32

    def _open_key(self, root, sub, wow32: bool):
        import winreg

        access = winreg.KEY_READ | (winreg.KEY_WOW64_32KEY if wow32 else winreg.KEY_WOW64_64KEY)
        return winreg.OpenKey(root, sub, 0, access)

    def _eval_registry(self, rule: dict) -> tuple:
        import winreg

        root, sub, force32 = self._parse_reg_path(rule.get("key", ""))
        wow32 = bool(rule.get("check_32bit_on_64bit")) or force32
        # Try the requested view; if missing, try the other view (installers are
        # inconsistent about where they land on 64-bit Windows).
        for view32 in ({wow32, True, False}):
            try:
                hkey = self._open_key(root, sub, view32)
            except FileNotFoundError:
                continue
            try:
                if rule.get("kind") == "registry_exists" and not rule.get("value_name"):
                    return True, "key exists"
                value_name = rule.get("value_name") or ""
                try:
                    val, _ = winreg.QueryValueEx(hkey, value_name)
                except FileNotFoundError:
                    return False, "value missing"
                if rule.get("kind") == "registry_exists":
                    return True, "value exists"
                if rule.get("kind") == "registry_value":
                    return (str(val) == str(rule.get("value")), f"value={val}")
                # registry_version
                cmp = compare_catalog_versions(str(val), str(rule.get("value") or "0"))
                return self._apply_operator(cmp, rule.get("operator")), f"version={val}"
            finally:
                hkey.Close()
        return False, "key not found in either view"

    def _eval_msi_product_code(self, rule: dict) -> tuple:
        pc = (rule.get("product_code") or "").strip()
        if not pc:
            return False, "no product code"
        # Authoritative: ask Windows Installer whether this product is installed
        # (same check Intune uses). Crucially this returns "not installed" for
        # WRAPPER MSIs (e.g. the Firefox enterprise MSI) that don't register a
        # real MSI product — which lets the discovery pass correct the rule to a
        # real registry rule, instead of shipping a ProductCode rule that would
        # never detect and would make the IME re-install every check-in.
        try:
            import ctypes
            state = ctypes.windll.msi.MsiQueryProductStateW(ctypes.c_wchar_p(pc))
            if state == 5:  # INSTALLSTATE_DEFAULT
                return True, "MsiQueryProductState=installed"
        except Exception:  # noqa: BLE001
            pass
        # Fallback: ARP key literally named after the ProductCode GUID.
        import winreg
        sub = _UNINSTALL_SUBPATH + "\\" + pc
        for view32 in (False, True):
            try:
                self._open_key(winreg.HKEY_LOCAL_MACHINE, sub, view32).Close()
                return True, "product code present (registry)"
            except FileNotFoundError:
                continue
        return False, "product code not installed"

    def _eval_file(self, rule: dict) -> tuple:
        base = rule.get("path") or ""
        name = rule.get("file") or ""
        target = Path(base) / name if name else Path(base)
        if rule.get("kind") == "file_exists":
            return target.exists(), f"exists={target.exists()}"
        # file_version — compare via the PE/MSI file version if obtainable
        if not target.exists():
            return False, "file missing"
        return True, "file present (version compare not implemented locally)"

    @staticmethod
    def _apply_operator(cmp: int, operator: Optional[str]) -> bool:
        op = (operator or "greaterThanOrEqual")
        return {
            "equal": cmp == 0, "notEqual": cmp != 0,
            "greaterThan": cmp > 0, "greaterThanOrEqual": cmp >= 0,
            "lessThan": cmp < 0, "lessThanOrEqual": cmp <= 0,
        }.get(op, cmp >= 0)

    # -- ARP discovery (snapshot diff) ------------------------------------

    def _snapshot_uninstall_keys(self) -> Dict[str, dict]:
        """Map 'view:leaf' -> {display_name, display_version, ...} across hives."""
        import winreg

        out: Dict[str, dict] = {}
        targets = [
            (winreg.HKEY_LOCAL_MACHINE, False, "HKLM64"),
            (winreg.HKEY_LOCAL_MACHINE, True, "HKLM32"),
            (winreg.HKEY_CURRENT_USER, False, "HKCU"),
        ]
        for root, view32, tag in targets:
            try:
                base = self._open_key(root, _UNINSTALL_SUBPATH, view32)
            except FileNotFoundError:
                continue
            try:
                i = 0
                while True:
                    try:
                        leaf = winreg.EnumKey(base, i)
                    except OSError:
                        break
                    i += 1
                    info = self._read_arp_entry(root, view32, leaf, tag)
                    if info:
                        out[f"{tag}:{leaf}"] = info
            finally:
                base.Close()
        return out

    def _read_arp_entry(self, root, view32: bool, leaf: str, tag: str) -> Optional[dict]:
        import winreg

        try:
            hkey = self._open_key(root, _UNINSTALL_SUBPATH + "\\" + leaf, view32)
        except FileNotFoundError:
            return None
        try:
            def val(name):
                try:
                    v, _ = winreg.QueryValueEx(hkey, name)
                    return v
                except FileNotFoundError:
                    return None
            dn = val("DisplayName")
            if not dn:
                return None
            full_key = (
                ("HKEY_LOCAL_MACHINE" if root == winreg.HKEY_LOCAL_MACHINE else "HKEY_CURRENT_USER")
                + "\\" + _UNINSTALL_SUBPATH + "\\" + leaf
            )
            return {
                "key_leaf": leaf,
                "key_path": full_key,
                "view32": view32,
                "tag": tag,
                "display_name": dn,
                "display_version": val("DisplayVersion"),
                "publisher": val("Publisher"),
                "uninstall_string": val("UninstallString"),
                "quiet_uninstall": val("QuietUninstallString"),
            }
        finally:
            hkey.Close()

    def _discover_new_entry(self, before: dict, after: dict, package) -> Optional[dict]:
        """Pick the ARP entry that appeared after install, matching the product."""
        new_keys = [k for k in after if k not in before]
        candidates = [after[k] for k in new_keys]
        if not candidates:
            return None
        name = (getattr(package, "name", "") or "").lower()
        # Prefer a new entry whose DisplayName overlaps the product name.
        for c in candidates:
            dn = (c.get("display_name") or "").lower()
            if name and (name in dn or dn in name or self._token_overlap(name, dn)):
                return c
        # Else the single new entry, if unambiguous.
        if len(candidates) == 1:
            return candidates[0]
        # Else the one carrying an uninstall string (most app-like).
        for c in candidates:
            if c.get("quiet_uninstall") or c.get("uninstall_string"):
                return c
        return candidates[0]

    @staticmethod
    def _token_overlap(a: str, b: str) -> bool:
        ta = {t for t in a.replace("-", " ").split() if len(t) > 2}
        tb = {t for t in b.replace("-", " ").split() if len(t) > 2}
        return bool(ta & tb)

    def _build_corrected_rule(self, discovered: dict) -> Optional[dict]:
        """Build a registry_version detection rule from a discovered ARP entry."""
        if not discovered.get("key_leaf"):
            return None
        version = discovered.get("display_version")
        rule = {
            "kind": "registry_version" if version else "registry_exists",
            "key": discovered["key_path"],
            "value_name": "DisplayVersion" if version else "DisplayName",
            "check_32bit_on_64bit": bool(discovered.get("view32")),
        }
        if version:
            rule["operator"] = "greaterThanOrEqual"
            rule["value"] = str(version)
        return rule

    # -- uninstall ---------------------------------------------------------

    @staticmethod
    def _silent_uninstall_command(discovered: Optional[dict]) -> Optional[str]:
        """Best silent uninstall command from a discovered ARP entry.

        Prefers QuietUninstallString; else appends the right silent switch to
        UninstallString (msiexec → /qn /norestart; an NSIS/Inno uninstaller exe
        like Firefox's helper.exe → /S).
        """
        if not discovered:
            return None
        q = discovered.get("quiet_uninstall")
        if q:
            return q
        us = discovered.get("uninstall_string")
        if not us:
            return None
        low = us.lower()
        if "msiexec" in low:
            return us if ("/qn" in low or "/quiet" in low) else us + " /qn /norestart"
        return us + " /S"

    def _attempt_uninstall(self, package, discovered: Optional[dict], result: dict) -> None:
        cmd = self._silent_uninstall_command(discovered)
        if not cmd and getattr(package, "uninstall_command", None):
            cmd = package.uninstall_command
        if not cmd:
            result["log"].append("no uninstall command available; left installed")
            self.emit("No uninstall command available — app left installed on the box.", "warn")
            return
        self.emit("Install validation: uninstalling (cleanup)…")
        rc, out = self._run(cmd, cwd=None, timeout=self.uninstall_timeout)
        result["log"].append(f"uninstall rc={rc}")
        # Confirm removal by POLLING — NSIS/Burn uninstallers (Firefox's
        # helper.exe, Snagit's Burn) copy themselves to temp and return
        # immediately while the real removal runs asynchronously, so an instant
        # re-check sees the app still present.
        gone = True
        if discovered:
            for _ in range(10):  # up to ~30s
                still, _ = self._eval_registry({
                    "kind": "registry_exists", "key": discovered["key_path"],
                    "check_32bit_on_64bit": discovered.get("view32"),
                })
                gone = not still
                if gone:
                    break
                time.sleep(3)
        result["uninstalled"] = bool(gone)
        if gone:
            self.emit("Uninstalled cleanly — build machine left as it was found.")
        else:
            self.emit("Uninstall did not fully remove the app — manual cleanup may be needed.", "warn")

    # -- subprocess --------------------------------------------------------

    def _run(self, command: str, cwd: Optional[Path], timeout: int) -> tuple:
        """Execute an install/uninstall command the way Windows does.

        Direct CreateProcess (shell=False), NOT via cmd.exe. Inno Setup
        installers (e.g. GIMP) return exit code 1 when launched through a
        ``cmd /c`` shell wrapper — the elevation-relaunch's exit code doesn't
        propagate through the shell — but install cleanly when exec'd directly.
        This mirrors PowerShell's Start-Process. The command string is parsed
        into argv with Windows quoting; a bare installer filename is resolved
        against ``cwd``.
        """
        logger.info("Local validation exec", command=command[:160], cwd=str(cwd) if cwd else None)
        try:
            argv = shlex.split(command, posix=False)
        except ValueError:
            argv = command.split()
        argv = [a.strip('"') for a in argv if a]
        if argv and cwd and not os.path.isabs(argv[0]):
            cand = Path(cwd) / argv[0]
            if cand.exists():
                argv[0] = str(cand)
        try:
            proc = subprocess.Popen(
                argv, cwd=str(cwd) if cwd else None, shell=False,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Local validation exec failed to launch", error=str(exc))
            return 1, str(exc)
        try:
            out, _ = proc.communicate(timeout=timeout)
            return proc.returncode, (out or "")[-600:]
        except subprocess.TimeoutExpired:
            # A non-silent installer that popped a UI will hang here. Kill the
            # WHOLE process tree (the installer relaunches children), not just
            # the direct child, so no installer window is left on screen.
            self.emit(f"Command timed out after {timeout}s — killing installer + its window…", "error")
            self._kill_tree(proc.pid)
            try:
                proc.communicate(timeout=15)
            except Exception:  # noqa: BLE001
                pass
            return 1460, "timeout (process tree killed)"  # 1460 = ERROR_TIMEOUT

    @staticmethod
    def _kill_tree(pid: int) -> None:
        """Force-kill a process and all its descendants (Windows taskkill /T)."""
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True, timeout=30,
            )
        except Exception:  # noqa: BLE001
            pass
