## [Unreleased]

## [1.13.1] - 2026-06-17

Wrapper-packaging robustness fixes, found by validating the generated scripts end-to-end on a real
(hardened) endpoint. The wrapper runtime is now verified: a Wireshark + PuTTY wrapper installs both
components via the generated `install.cmd`, both detection rules fire, and `uninstall.cmd` removes
both.

### Fixed

- **Wrapper scripts had doubled `CR CR LF` line endings.** `_render_wrapper_script` emits `\r\n`, but
  `Path.write_text` then re-translated each `\n` to `\r\n` (Windows text mode), producing `\r\r\n`,
  which corrupts `cmd` parsing (`cd /d "%~dp0"` fails with "The system cannot find the path
  specified"). Now written with `newline=''`.
- **Wrapper scripts failed on hardened/Intune-managed endpoints where the cwd is not searched for
  executables** (`NoDefaultCurrentDirectoryInExePath=1`). A bare installer-exe name in the script was
  "not recognized". The generated script now prepends the package root to `PATH`
  (`set "PATH=%~dp0;%PATH%"`) so both EXE and MSI steps resolve. (Also removed an over-corrective
  `start /wait` — `cmd` already waits for a directly-invoked installer, GUI or console.)

### Notes

- **Free Npcap confirms no silent install** (empirically: `npcap-1.88.exe /S` pops a UI and installs
  nothing). The Wireshark wrapper therefore still requires a licensed **Npcap OEM** installer in
  `data/wrapper_components/`; the free edition's 5-system allowance is a usage right, not a
  silent-install unlock.

## [1.13.0] - 2026-06-17

EXE catalog expansion + multi-component (wrapper) packaging. Four new EXE baseline entries were
packaged through the live pipeline to Ring 0, and the pipeline gained the ability to deliver more
than one installer in a single Win32 app. The deterministic four-stage pipeline is unchanged. Full
suite: 920 tests passing (+ the known `created_at` ordering flake, which passes in isolation).

### Added

- **Multi-component (wrapper) packaging — `CatalogEntry.extra_components`.** A catalog entry can now
  bundle a primary installer **plus additional installers** into one `.intunewin`. At package time
  `PackagingAgent` stages every component (downloaded from a `source` URL or found by `filename_hint`
  in `data/wrapper_components/`), generates `install.cmd` / `uninstall.cmd` that run each silently
  from the package root (reboot-aware; components uninstall in reverse), sets the Win32 install/
  uninstall commands to `cmd /c install.cmd` / `cmd /c uninstall.cmd`, and **merges detection** —
  the primary's rules plus every component's rules. Intune ANDs detection rules, so the app reports
  "installed" only when every piece is present. A missing `required` component **escalates** at
  packaging (no half-publish). The canonical case is **Wireshark + the Npcap capture driver** (the
  `wireshark` baseline entry carries an `npcap-oem` component). 8 unit tests in
  `tests/unit/test_wrapper_packaging.py`.
- **Four new EXE catalog entries** (live-validated to Ring 0 on `ADMIN_BUILD_1`): `beyond-compare`
  (Inno, per-user), `treesize-free` (Inno, per-user), `intellij-idea` (NSIS, per-machine; 2025.3
  unified build), `wireshark` (NSIS, per-machine; modeled as a wrapper pending the Npcap OEM file).

### Notes

- **Per-user Inno installers.** Beyond Compare 5 and TreeSize Free install per-user (HKCU `_is1` key,
  `%LOCALAPPDATA%`) **even when the installer runs elevated**, so their entries set
  `install_context: user`; a system-context publish of a per-user app installs into the SYSTEM
  profile and never detects for the real user.
- **Wireshark needs Npcap OEM for capture.** The free Npcap installer has no silent install (`/S` is
  OEM-only) and a 5-system cap, so a functional Wireshark deployment requires a licensed Npcap OEM
  installer dropped in `data/wrapper_components/`; until then the wrapper build escalates rather than
  shipping a capture-less app.

## [1.12.0] - 2026-06-14

Application lifecycle management for general (COTS) software — the center panel now reads as a
true lifecycle worklist: reliable version state, a clean/retirement signal, automatic version
discovery, per-product autoupdate, and a retire/delete back half. Plus intake hardening so a
non-installer file can never publish a malformed app, and a one-command stack restart. The
deterministic four-stage pipeline is unchanged. Full suite: 908 tests passing.

### Added

- **Reliable version state (Latest / N-1 / N-2).** Every deployed app in a product line is ranked
  by its Graph `displayVersion` (`intune_view._assign_version_states`), so the badge is correct even
  in a device-less tenant where the `verified_versions` overlay is empty. Apps are grouped by a
  name-only `product_line` key that normalizes the `_NN` dedupe suffix, so catalog-matched and
  name-matched versions of the same product land in one chain.
- **Install-count "clean" signal + retirement clock.** Live install counts drive a per-app `clean`
  state (0 confirmed installs). `demo/clean_tracking.py` records WHEN an old version first went clean
  (`data/demo_clean_tracking.json`); the timer resets if a straggler device reappears. Once an old
  version stays clean past `lifecycle.clean_window_days` (default 30) it is **retire-eligible**.
- **Per-product autoupdate toggle.** A toggle on the Latest row (keyed by product line): ON =
  full-auto upgrade when a newer version is found; OFF (default) = a **gated** upgrade held at the
  Ring-0 approval gate. `POST /api/demo/intune/{id}/autoupdate`.
- **Automatic version discovery — daily + on-demand.** The **Check updates** button (and a global
  **Daily** Beat toggle) run a version-check cascade over every Latest app (catalog → internet),
  dispatching an upgrade per genuine newer build — full-auto if the product's autoupdate is on, else
  gated. A no-duplicate guard never offers/creates a version already in the tenant. The daily Beat
  (`check_app_versions`) honors the global daily flag and each product's setting.
- **Retire action (phase 3b).** A new per-product **Auto-Delete-When-Clean** toggle
  (`POST /api/demo/intune/{id}/auto-delete`) governs what happens to a retire-eligible old version:
  OFF (default) **relabels it "Retired"** (local marker, Intune object kept — reversible); ON
  **deletes** the Intune app, first clearing the incoming supersedence relationships that would
  otherwise block the delete (unrelated relationships on the superseding app are preserved). A manual
  **Retire/Delete** button on each old row does the same on demand (a confirm appears only when it
  deletes). The daily Beat runs an estate **retire sweep** alongside the update run. New modules
  `demo/retire.py` + `demo/retire_state.py`; `POST /api/demo/intune/{id}/retire`.
- **Reusable stack restart.** `scripts/restart_stack.py` + `restart-all.bat` stop the whole local
  stack (Redis + Celery worker + uvicorn), clear the stale Redis dump, relaunch all three detached
  with logs in `data/logs/`, and health-check the ports (`--stop` / `--start` / `--no-worker`). Uses
  a PowerShell CIM query (Windows 11 no longer ships `wmic`) plus a port-8000 fallback.

### Changed

- **CVE risk is gated on active exposure.** A vulnerable build with 0 installs (clean) — or a
  retired version — no longer shows an active risk badge; the risk is "drained". Unknown install
  counts stay active so a real exposure is never hidden.
- **"Patch now" only on the Latest version.** An N-1/N-2 is already superseded (and a clean one is
  for retirement, not patching), so the action is hidden there.
- **No Approve→Ring 0 confirm pop-up.** The deployment gate is released directly; an approval
  gate/screen is on the roadmap.
- Lifecycle settings (`auto_update`, `auto_delete_when_clean`) and clean/retire state are applied
  fresh on **every** serve (including SWR-cached ones), so a just-toggled flag or a just-elapsed
  clean window takes effect immediately without waiting for the Graph cache to revalidate.

### Fixed

- **Intake hardening.** `demo/intake.analyze()` now **escalates** (instead of publishing a malformed
  app) for (a) unrecognized / no-extension files — e.g. a vendor "stable channel" URL saved as
  `stable` (guard runs before any parser, so the upgrade/discovery/queue download-then-analyze paths
  are covered too); and (b) a `.msi` whose Property table is empty/unreadable with no catalog match
  (new `_msi_has_identity()`).
- **Autoupdate toggle no longer flips back** on the next poll (the SWR cache had frozen the flag).
- **Replay version-check reports up-to-date** for fixture-less apps instead of fabricating a "+1"
  version.
- Table column alignment + the three-panel layout were tightened.

## [1.11.0] - 2026-06-13

Demo robustness + first-class EXE support for installers that carry no metadata. Hardens the
intake and upgrade paths so a non-packageable installer can never publish a malformed Intune
app, makes the center panel responsive under continuous polling, and closes the "identity-less
EXE" gap through the catalog. The deterministic four-stage pipeline is unchanged. Full suite:
876 tests passing.

### Added

- **First-class EXE support for identity-less installers.** Some EXE installers ship **no**
  `VS_VERSIONINFO` at all — VLC's NSIS `.exe` returns blank ProductName/Version/Company (Windows'
  own Properties tab reads the same), so PE-based matching and identity extraction both come up
  empty. `Catalog.match_exe` gains a **filename-only pass** that matches purely on a catalog
  entry's `filename_pattern` when PE product is blank (gated so it never overrides the PE-present
  filename+product disambiguation, e.g. VS Code user vs system). On such a HIT, `analyze()` now
  **inherits name/publisher from the catalog entry** and **parses the version from the filename**
  (`vlc-3.0.20-win64.exe` → `3.0.20`), so the app publishes as "VLC media player" 3.0.20, not the
  filename. New baseline entry `vlc-media-player-exe` (filename match, NSIS `/S`, file-version
  detection on the installed `vlc.exe`, NSIS uninstaller, shared supersedence line, CVE CPE).
- **Stale-while-revalidate cache for the center apps view** (`intune_view.get_apps_view_cached`).
  The view is served from cache instantly (~4 ms vs a ~9 s live Graph fan-out) and refreshed in
  the background once it ages past 25 s; a **disk snapshot** (`data/demo_cache/apps_snapshot.json`)
  makes the first paint after a restart instant too. `GET /api/demo/intune/apps?refresh=1` forces a
  synchronous full reload (manual **Refresh** button + the post-publish payoff). The console shows a
  freshness badge ("live · just now" / "cached · 12 s", pulsing while revalidating).
- **`CatalogEntry.cpe`** (added in 1.10.0) is now also set on the VLC `.exe` entry; the
  `vlc-media-player` supersedence line is shared between the MSI and EXE entries (same product).

### Fixed / hardened

- **Intake escalates an unidentifiable EXE** instead of publishing a malformed app. An uploaded
  `.exe` whose `VS_VERSIONINFO` is unreadable **and** that matches no catalog entry was flowing down
  the catalog-MISS path and publishing a Win32 app with the filename as its display name, version
  "unknown", and a placeholder registry detection rule Intune can never satisfy (so the IME
  re-installs forever). `analyze()` now escalates that case with an actionable "use the vendor MSI"
  message; the router's existing escalate branch fails the job cleanly without writing to the tenant.
- **The upgrade / "Patch now" path now respects the escalate guard.** It previously called
  `analyze()` but ignored `escalate`, so a version-check that handed back an unidentifiable installer
  (live mode returning VLC's `.exe`) published a malformed *superseding* app and wired supersedence
  to the real one. `finalize_upgrade_job` now fails the upgrade cleanly in that case.
- **Deterministic VLC version-check fixture.** Replay now returns `3.0.23` (the newest VLC build with
  a managed `.msi`) with the real download URL, instead of the generic bump to `3.0.21` — which
  VideoLAN ships **`.exe`-only**, so it couldn't be fetched.

## [1.10.0] - 2026-06-12

Demo Mission Control gains **CVE-driven patch prioritization** — "patch by risk". Each Intune app is
correlated with the public CVEs a newer release fixes and a CVSS severity score, so the console reads
as a risk worklist sorted worst-first instead of an alphabetical app list (the capability gap relative
to PatchMyPC's CVE Insights). The deterministic four-stage pipeline is unchanged; this is an
operator-side enrichment + prioritization layer that hands the worst offenders straight into the
existing version-check → supersedence → Ring 0 upgrade flow. Full suite: 863 tests passing.

### Added

- **CVE intelligence service (`autopackager/services/cve_intel.py`).** `lookup()` resolves, for a
  product + deployed version, the CVEs a newer release fixes, with a CVSS base score and severity
  bucket. Layered and best-effort (never raises): a curated offline **cache** (the default,
  stage-reliable), then the live **NVD CVE API 2.0** by CPE (`virtualMatchString`, optional
  `NVD_API_KEY`), then an optional **AI research-bridge** fallback. Precise version filtering — a
  CVE counts only when its `fixed_in` is newer than what's deployed (and, when an upgrade target is
  known, at or before it). Mode via `CVE_INTEL_MODE` (`cache` | `live` | `off`).
- **Curated CVE fixture (`demo/fixtures/cve_intel.json`).** Real, sourced CVEs (real NVD CVSS +
  fixed-in versions) for VLC, 7-Zip, Notepad++, Python (incl. the CVSS 10.0 tarfile RCE
  CVE-2024-12718), and Go. Keyed by CPE or display-name alias.
- **`CatalogEntry.cpe`** — an agnostic, baseline-eligible NVD CPE key (`vendor:product`, version
  appended at query time), set on the VLC / 7-Zip / Notepad++ / Go baseline entries.
- **Risk UI.** The center "Intune · Apps" table gains a **Risk** column (severity badge with CVSS
  score, worst-first sort, a pulsing critical), a **CVE detail drawer** (per-CVE id → NVD link, score,
  summary, fixed-in version, plus a live NVD re-scan), a one-click **Patch now** that runs the app
  through the existing upgrade pipeline, and an **estate-risk roll-up** in the panel header. The
  software-gap modal badges known-vulnerable unmanaged software too.
- **Endpoints.** `GET /api/demo/intune/{app_id}/cves` (drawer detail; `?mode=live` forces a fresh
  NVD lookup for one app). The apps view and software-delta now carry a per-row `cve` block.

### Notes

- For a demo with red badges, **pre-stage a deliberately-old build** (e.g. VLC 3.0.20, 7-Zip 24.08,
  Notepad++ 8.8.1, Python 3.12.0 for the CRITICAL). A fully-current tenant correctly shows all-green.
  "Patch now" on a staged-old app finds the newer build, supersedes it, and the badge clears.

## [1.9.0] - 2026-06-12

Demo Mission Control gains an interactive multi-package **batch-stream** view, the approval gate is
hardened against accidental / stray tenant writes, and the supersedence-upgrade deploy path is made
idempotent (a single new version no longer fans out into duplicate Intune apps). The deterministic
four-stage pipeline is unchanged. Full suite: 827 tests passing.

### Added

- **Interactive batch-stream page (`/demo/stream`).** A live grid with one card per queued package,
  each independently actionable (approve / confirm-url / drop-installer) — a multi-package queue no
  longer funnels through the console's single-action lock, so prompts for different items can't
  interrupt each other. Backed by a fan-in SSE (`events.asubscribe_many`, every event tagged with
  `job_id`), a snapshot endpoint (`/api/demo/queue/{batch_id}/snapshot`) that reseeds a parked
  action on reconnect/refresh from the persisted `queue_origin` state, and `queue.jobs_for_batch()`.
  The console shows a header **Batch stream** pill (localStorage-persisted, with a live
  running/done count) and opens the page automatically for any multi-item queue.
- **Retry + View-logs on failed executions.** `GET /api/demo/jobs/{id}/logs` assembles a
  human-readable diagnostic (error + escalation reason + install attempts + the local-install
  validator / smoke-test logs); `POST /api/demo/jobs/{id}/retry` re-dispatches the pipeline (gating
  preserved). Failed batch-stream cards expose both so an engineer can inspect and re-run.

### Changed

- **Approval-gate hardening (defense in depth).** `deployment_task` now refuses to deploy a *gated*
  job unless `gate_approved` is persisted on it (set by `/approve` before it re-dispatches
  deployment), so no stray dispatch path (a retry, a `process_job` chain) can publish a gated job to
  the tenant. The **Approve** buttons (batch-stream cards + console) now confirm before publishing —
  an accidental click can no longer write to Intune.
- **Upgrade installer search uses the app's real display name + publisher** (resolved via Graph),
  not the catalog slug. The degraded query (e.g. `"sharex"`, empty publisher) was making the upgrade
  fall straight through to "drop an installer"; it now performs a valid web search.
- **Version badge defaults to "Current"** for an app that can't yet be placed in a supersedence
  chain, instead of showing no badge — resolved to `N-1`/`N-2` once a refresh populates the chain.
- **Demo research bridge rebranded to AutoPackager.** The SSE event source and every surfaced string
  are `autopackager` / "AutoPackager"; "Claude" is no longer surfaced (the third-party
  `claude_agent_sdk` / `claude` CLI references stay accurate).

### Fixed

- **Supersedence-upgrade assignment 400 → duplicate apps.** `win32_auto_update_settings` emitted the
  non-existent Graph property `autoUpdateSupersededApps` (correct name:
  `autoUpdateSupersededAppsState`), and that block is `available`-intent only — so a `scope=all`
  upgrade's `required` assignment 400'd, `deployment_task` retried, and each retry minted a fresh app
  (`_01/_02/…`). Fixed the property name and stopped attaching auto-update settings to a `required`
  assignment (the `mobileAppSupersedence(update)` relationship already drives the upgrade).
- **Deploy is idempotent across retries.** The created Intune app id is persisted on the job before
  any post-create step (upload / supersedence / assign); on retry the deploy reuses that published
  app — or deletes a non-published shell — instead of creating a duplicate.
- **Clean Graph errors.** `format_graph_error()` surfaces a single readable operator line (403 →
  missing service-principal role incl. a `Group.ReadWrite.All` hint, 400 `ModelValidationFailure`,
  429, 404) instead of a raw `{'error': {...}}` dict; `deployment_task` now raises a picklable error
  and emits a demo error event.

## [1.8.0] - 2026-06-10

Catalog expansion from a real enterprise software inventory, plus a deployment-agent
feature that unlocks **per-user installers** for fleet deployment. The deterministic
four-stage pipeline is unchanged. Full suite: 784 tests passing.

### Added

- **Detached-install settle-wait in the local validator.** After an install attempt returns, the
  validator now POLLS detection over a settle window (`install_settle_seconds`, default 120) instead
  of checking once. Detached/async installers — Squirrel stubs (Postman, Insomnia) and WiX Burn
  bundles (AWS Session Manager Plugin) that exec the real install in a child and return immediately —
  are caught once their child finishes, instead of being wrongly escalated. A synchronous install
  still satisfies the first poll (no added latency). This unblocked **Postman**, **Insomnia**, and the
  **AWS Session Manager Plugin** (all now published live).
- **Intune user-context Win32 support** (`CatalogEntry.install_context`). A catalog entry
  may declare `install_context: user` (default `system`); `DeploymentAgent._prepare_app_data`
  maps it to the Win32 `installExperience.runAsAccount`. Per-user installers — Squirrel apps
  (Postman, Insomnia) and Inno installers that ignore `/ALLUSERS` (Greenshot) — write to
  `%LOCALAPPDATA%` and an HKCU Uninstall key; published in the default system context Intune can
  only detect/uninstall them for the profile that ran the install, so they never deploy cleanly to
  a fleet. With `install_context: user` Intune installs in the logged-on user's context and
  evaluates HKCU detection per-user. Verified live (Greenshot republished with `runAsAccount=user`).
- **Catalog growth — Batch 1 & 2** (built from a 44,952-row enterprise inventory triaged to 2,955
  unique products by packaging ease). New baseline entries, each with `canonical_download_url` +
  agnostic install/uninstall/detection facts, live-verified on a real device (install → detect →
  uninstall → publish → Ring-0 assign): **AWS CLI v2** (MSI), **WinSCP** (Inno; `/SILENT` uninstall),
  **Audacity** (Inno, per-machine), **Greenshot** (Inno, per-user → user-context), **DBeaver CE**
  (NSIS; silent switch is `/S /allusers`), **Power BI Desktop** (~640 MB MSI-bootstrapper; file-version
  detection). Cataloged with codes/URL captured but live-publish deferred: **Microsoft Edge** (the
  install→uninstall validation would remove the inbox system browser) and **AWS Session Manager
  Plugin** (WiX Burn detached install — needs a validator settle-wait).

### Changed

- **`load_catalog()` merges baseline + overlay FIELD-BY-FIELD** instead of whole-entry replace by
  `id`. A baseline-only agnostic field (e.g. `install_context`, or curated detection rules the
  auto-appended overlay never captured) now survives when the overlay holds a same-id stub; the
  env-specific fields the overlay does set (`use_count`, `verified_versions`, `first_seen`/
  `last_used`, `version`) still win. Fixes a footgun where any baseline edit to an app that had
  already been run once was silently shadowed by its overlay entry.

## [1.7.0] - 2026-06-08

Phase 2 groundwork: an **operator-side AI research bridge** plus the discovery/queue machinery that uses it, surfaced through a **Mission Control demo console** — all additive under the removable `demo/` package and the new `software_delta` service, with the deterministic four-stage pipeline unchanged. Also includes the prior repository cleanup and documentation refresh. Full suite: 715 tests collected.

### Added

- **AI research bridge** (`demo/claude_bridge.py`). Runs a real Claude agent via the `claude-agent-sdk` package (preferred) or the `claude -p --output-format stream-json` CLI (fallback), authenticated through the local Claude session. Three contracts, each emitting one final fenced ```json block:
  - **Catalog-miss research** (`research_and_learn`) — inspects a dropped installer (allowlisted Read/Bash/Write scoped to `data/demo_sandbox/`) and produces the silent install command + (for EXE) detection rules, written back through the existing `installer_catalog.add_msi_entry` / `add_exe_entry` overlay path so a second run of the same app resolves as a deterministic HIT.
  - **Version check** (`check_version`) — reports the latest upstream version for a known app; `_decide_is_newer` trusts a real PEP 440 `compare_catalog_versions` over the model's self-reported `is_newer` when both versions parse.
  - **Installer acquisition** (`find_installer_url`) — web-searches (WebSearch/WebFetch) for the official vendor installer for an unknown app, returning URL + provenance + confidence. LIVE-only; never auto-acted-on — the caller requires an operator confirm before download/install (supply-chain guardrail).
  - Three modes via `DEMO_CLAUDE_MODE`: `live` / `replay` (default, streams `demo/fixtures/claude_stream_*.ndjson`) / `off`. The bridge never raises into its caller — failure drives the AI lamp to `error` and returns a deterministic fallback.
- **Unmanaged-software delta** (`autopackager/services/software_delta.py`). Combines installed inventory (Intune Detected Apps + local ARP via `autopackager/utils/arp.py`) with the managed set (published Win32 apps + installer catalog) and classifies every app into `managed` / `known_packageable` / `standard_os_component` / `store_app` / `unmanaged_candidate` / `ignored`. Surfaces the actionable "installed but not packaged" gap; degrades to local-ARP-only (`intune_unavailable`) when the SP lacks `DeviceManagementManagedDevices.Read.All`. New `software_delta` config block (`microsoft_os_components`, `ignore_patterns`).
- **Packaging queue from the delta** (`demo/queue.py`). Turns selected delta candidates into gated packaging jobs processed one at a time. Acquisition cascade: curated catalog `canonical_download_url` (trusted) → version-check brain (known product) → agent web-search (unknown app → parked for operator confirm). A queue item is just a `Job` row tagged `queue_origin` — no new pipeline or DB table — always Ring-0-scoped and deploy-gated, with a single-action lock and responsive cancel.
- **Pre-publish install validation + retry ladder** (`autopackager/agents/testing/local_install_validator.py`). Actually installs the package on the host and verifies by detection rule or a new ARP entry (discovery-via-diff) before publishing. Capped retry ladder (`_MAX_INSTALL_ATTEMPTS=3`) probes alternate EXE silent switches; a working non-primary switch is recorded as `corrected_install_command`. Non-silent installers (UI timeout, rc 1460) and bundleware exhaust the ladder and are flagged `needs_engineer_review` (ENGINEER ESCALATION) instead of publishing an app Intune can never mark installed. `_reap_detached_installers` kills detached consumer stubs (e.g. ChromeSetup). Catalog entries can declare `escalate` (e.g. RealPlayer) to install nothing and escalate immediately.
- **Mission Control demo console** (`demo/`). Single-screen three-panel console (pipeline status · live Intune view · AI agent console + lamp) showing intake → Intune → Ring 0 with the AI research narrating live over SSE. Additive endpoints under `/api/demo/...` (`preflight`, `POST /jobs`, `GET /stream/{job_id}`, `GET /intune/apps`, `GET /intune/software-delta`, `POST /jobs/{job_id}/approve`). Fully removable: delete `demo/` + the one `mount_demo(app)` line and the core is untouched.
- **Catalog growth.** Consumer→enterprise taxonomies (Chrome, VS Code user/system) and dev-tool entries (R, Git for Windows, RStudio, Go, CMake, Temurin JDK 21, Snagit); RealPlayer marked escalate/don't-package.
- **Tenant-agnostic catalog snapshot / export** (`installer_catalog.export_catalog_snapshot` + `cli.py catalog export`). Copies the hard-won catalog *rules* (install/uninstall commands, detection rules, installer family, supersedence capability, Intune attribute overrides) into a committable YAML while stripping every tenant-specific field — `first_seen`, `last_used`, `use_count`, `version`, and `verified_versions` (which carries tenant-bound Intune app GUIDs), enforced by the new `OVERLAY_ONLY_FIELDS` set + a sanitization guard. `--source merged|overlay|baseline` selects scope; entries are sorted by id for stable diffs. The first export captured 38 rules (16 learned only in the local overlay) so that knowledge can be committed and promoted into the shipped baseline instead of living only in the gitignored overlay.

### Removed

- **9 dead diagnostic harnesses** from the repo root — one-off `verify_*`/`test_*` scripts with no references, superseded by the `tests/{unit,integration,cli,api}/` suite: `verify_celery_beat_schedule.py`, `verify_dashboard_e2e.py`, `verify_deployment_status_polling.py`, `verify_e2e_continuous_discovery.py`, `verify_subtask_2_4.py`, `test_dashboard.py`, `test_discovery_mock.py`, `test_discovery_task.py`, `run-verification.sh`.
- **Committed Redis binaries** (`tools/redis/`, ~25 MB of third-party `.exe`/`.pdb`/`.dll`/`.docx`). Now gitignored and fetched at install time.
- **`IMPLEMENTATION_GUIDE.md`** — ~70% duplicate of `AUTOMATED_SETUP.md`/`SETUP.md`/`QUICKSTART_CHECKLIST.md`; unique content (full command reference, production-checklist items) folded into `QUICKSTART_CHECKLIST.md`.

### Changed

- **Redis bootstrap** — `Install-AutoPackager.ps1` now prefers Memurai (maintained, Redis-compatible) via winget, then falls back to the existing Chocolatey → archived-zip path. `start-redis.bat`/`launch-all.bat` skip if port 6379 is already served and resolve a server from `tools\redis` or PATH.
- **Documentation restructure** — external/aspirational docs moved out of the product-doc root: whitepaper + PR/FAQ → `docs/design-history/`; Intune Cookbook chapter references (ch04/ch11) → `docs/claude-reference/`. Each new folder has a README explaining its role. README version (1.3.0 → 1.6.0) and test count (597+ → 621 passing) corrected; added a documentation map.

### Version

- `__version__` bumped to `1.7.0`. README Current Status / Credits and CLAUDE.md refreshed (test count 621 → 715; new capability rows; corrected the stale "No LLM is used" row to reflect the operator-side AI research bridge).

## [1.6.0] - 2026-06-01

End-to-end MSI supersedence: the operator opts in per publish, AutoPackager creates a new Intune Win32 app for the new version, links it to the prior version via Intune's `mobileAppSupersedence` relationship, and updates the catalog overlay's `verified_versions` state machine to reflect the new chain. Pilot-verified live against the ngbg tenant with PowerShell 7.6.1 → 7.6.2 and PuTTY 0.83 → 0.84.

### Added

- **Catalog supersedence schema.** New `CatalogEntry.supersedence` block declares a CAPABILITY (which versions in which line, by what strategy) without forcing any behaviour at publish time. Supersedence is **never automatic** — the operator opts in per publish via the CLI's `--supersede` (catalog-mode) or `--supersedes <id...>` (manual override) flag. Four modes via the new `SUPERSEDENCE_MODES` controlled vocabulary:
  - `generic`: newer version supersedes older within the same `line`, by PEP 440 ordering.
  - `specific`: same as generic, but `version_pattern` (a `re.fullmatch` regex) filters which versions belong to the line. Used for parallel-maintained sub-lines (e.g., `^1\\.6\\.\\d+$` for Java 1.6.x).
  - `manual`: catalog declares explicit `supersedes: [entry-id, ...]` list.
  - `none`: **DENY in both directions** — entry never supersedes anything and is shielded from being marked superseded. Overrides any operator opt-in flag. Use for developer middleware where parallel versions are intentional (JDK 8 / 11 / 17 / 21, .NET 6 / 8 / 9, Python 3.x lines, Node LTS lines).
- **CLI opt-in.** `cli.py create-software-job` gains `--supersede` (use the catalog's declared chain at default `mode: generic`) and `--supersedes <id>` (explicit overrides, repeatable). Silent by default — no interactive prompt. Mutually exclusive. `mode: none` is a DENY shield that overrides both flags in either direction. Resolution runs at CLI time against the catalog snapshot and is stashed in `job.job_metadata['supersedence_action']` for the deployment agent to execute.
- **`status` field on `verified_versions[]` rows.** New `VERIFIED_VERSION_STATUSES` controlled vocabulary: `newest`, `superseded`, `historical`, `manual`, `pending`. Machine-maintained by `record_publish()` (writes `pending` at publish time) and `record_verification()` (line-aware state machine: promotes `pending` → `newest` on the first device install, demotes prior `newest` → `superseded` or `historical` based on whether the operator opted in). Status is stored, not computed, and re-evaluated at publish time.
- **`record_publish()`.** New idempotent function called by the deployment agent immediately after `_upload_and_publish` — writes a `pending` `verified_versions` row with the freshly-minted Intune app id. Without it, supersedence on the *next* publish has no target rows in the catalog (the overlay was empty until a device actually installed), and the `supersedingApps` relationship never got created.
- **`apply_supersedence_status()`.** Companion that demotes the matching `verified_versions` rows to `status: superseded` in the local overlay once Intune confirms the relationship.
- **`resolve_supersedence()`.** Pure-function planner: given a catalog, a publishing entry, the operator's opt-in (`--supersede` / `--supersedes`), and the catalog snapshot, returns a `SupersedenceResolution` with `mode_used`, `superseded_intune_app_ids` (for the Graph POST), `demoted_records` (for the overlay write), and `notes` (operator-visible). `mode: none` short-circuits both directions.
- **Version comparison** (`autopackager/utils/version_comparison.py`). `compare_catalog_versions(a, b)` via PEP 440 `packaging.version.Version` with vendor-format normalisation: underscores and hyphens collapse to dots before parsing, so Java-style `1.8.0_341` and `1.8.0-341` compare against `1.8.0.341` correctly. Falls back to natural-sort for genuinely non-PEP440 strings.
- **Graph wiring.** Supersedence relationships POST to `/beta/deviceAppManagement/mobileApps/{id}/updateRelationships` with the `mobileAppSupersedence` shape (`targetId`, `supersedenceType: 'update'`). The action is **beta-only** — v1.0 returns *"Resource not found for the segment 'updateRelationships'"*. Verified live against the ngbg tenant.
- **`displayVersion` populated via beta PATCH.** The Intune portal's "App Version" column reads from this field, but v1.0 POST/PATCH silently drops it (no error, just doesn't persist) and v1.0 GET returns `None` even when beta has set it. The deployment agent now PATCHes `displayVersion` against `/beta/deviceAppManagement/mobileApps/{id}` after every content commit. Verified visible end-to-end against the test tenant.
- **Per-entry `version` field (overlay-only).** The current/intended version of an entry's installer. Distinct from `verified_versions` (publish history). Different operators may be on different versions of the same product at the same time, so this is intentionally tenant-private state — a contract test asserts the committed baseline never carries this field.
- **17 new tests** in `tests/unit/test_installer_catalog.py` covering `resolve_supersedence` (catalog mode, manual mode, explicit `--supersedes`, `mode: none` DENY shield in both directions, no-prior-row), the `verified_versions` state machine (pending → newest, prior-newest demotion paths, manual flag), `record_publish` idempotency, and two contract tests that read the actual baseline YAML to assert no `version` field leaks and every entry spells out `supersedence.mode` explicitly.
- **`add_msi_entry` / `add_exe_entry` default supersedence block.** Auto-added entries now ship with `supersedence: {line: <id>, mode: generic}`. Capability-only declaration; operator opts in at publish time. Operators who want a stricter default (`mode: none` for developer middleware) edit the overlay after the auto-add.

### Changed

- **Deployment-stage ordering reworked**, in three places — every change was found on a live tenant rather than a unit-test surface:
  - `_apply_supersedence` and `record_publish` now run **after** `_upload_and_publish`. Intune's `updateRelationships` action rejects calls against an app whose `publishingState` is not `Published` (*"Invalid operation: app's PublishingState is not 'Published'"*). The previous order raced ahead of content commit and left `notPublished` orphan apps behind on every supersedence attempt.
  - `displayVersion` beta PATCH also moved **after** `_upload_and_publish`. PATCH against a `notPublished` app returns 204 but the value never lands — the portal "App Version" column stayed blank on every freshly-created app.
  - `_lookup_catalog_entry` now uses `Catalog.match_msi` (UpgradeCode → ProductCode → name/publisher cascade) instead of `match_by_product_code` alone. Every released MSI version carries a unique ProductCode, so a fresh version missed the catalog entry registered against the prior version's GUID. UpgradeCode is the stable per-product identifier MSI guarantees across versions — exactly what supersedence needs to find the catalog row that owns this product line.
- **`_create_or_update_intune_app` skips the existing-app `displayName` lookup when supersedence is requested.** Intune supersedence is a relationship *between* apps; PATCHing the existing app gives the deployment agent nothing to link to (the old app id would equal the new one). Skipping the lookup forces a clean `create_win32_app()` and `supersedingApps` lands on the new id pointing at the prior version's id.
- **Baseline YAML header docs** expanded with the supersedence semantics, the overlay-only contract for `version` / `verified_versions[].status`, and the controlled-vocabulary references. The "What must never appear in the baseline" subsection is the new authoritative list for catalog contributors.
- **All five committed baseline entries** (7-Zip, Notepad++, PowerToys, Adobe Reader DC, Foxit PDF Reader) gain explicit `supersedence: {line: <id>, mode: generic}` blocks. Operators can override per-tenant in their overlay (e.g., `mode: none` for compliance lock).
- **`record_verification`** is now line-aware: promotes the matching `pending` row to `newest` (or `manual` when the operator flagged the publish as manual rollback), demotes any prior `newest` for the same line to `superseded` (if the supersedence chain was honoured) or `historical` (if the operator did not opt in). Idempotent on `(product_version, intune_app_id)`.

### Dependencies

- `packaging>=22.0` added to `requirements.txt`. Used by `autopackager.utils.version_comparison.compare_catalog_versions` for PEP 440-aware version ordering. Transitively present via `pytest`/`setuptools`, but pinned explicitly so a downstream `pytest` change can't break supersedence comparison.

### Version

- `__version__` bumped to `1.6.0`.

Full suite: 506 pass on the file-DB harness, 1 known pre-existing flake (`test_get_all_jobs_ordered_by_created_at` when `data/autopackager.db` has accumulated state — reproduces on `main` HEAD without supersedence changes).

---

## [1.5.0] - 2026-06-01

### Added

- **EXE installer support, end-to-end.** The pipeline now handles `.exe` software jobs alongside `.msi`. PE VS_VERSIONINFO is read via a new pure-Python reader (`autopackager/utils/pe_metadata.py`); the catalog matches by SHA-256, then by `pe_company_name` + `pe_product_name` substring; the EXE branch in `PackagingAgent._generate_install_commands` sources the silent-install string from the catalog entry's `install_command_template` or the family default in `INSTALLER_FAMILY_SWITCHES`. Detection rules for EXE come from the catalog's `detection_rules` list (converted to `win32LobAppRegistryRule` / `win32LobAppFileSystemRule` via `detection_rule_to_graph`). `cli.py inspect-exe` previews the PE strings + catalog match status; `cli.py create-software-job` refuses to enqueue an EXE without a matching catalog entry whose `detection_rules` is non-empty (apps with no detection rule cause the IME to re-install on every check-in). Pilot-verified against Notepad++ 8.9.6.2: 20s pipeline, app `15967287-...` installed cleanly on the test device.
- **Catalog schema for EXE and wrapped installers.** New `CatalogEntry` fields: `installer_family` (controlled vocabulary: `msi`, `inno_setup`, `nsis`, `wix_burn`, `msft_bootstrapper`, `wrapped_msi`, `wrapped_zip`, `custom`); `detection_rules` (catalog-native rule kinds: `msi_product_code`, `file_exists`, `file_version`, `registry_exists`, `registry_value`, `registry_version`); `extract_command_template` + `extracted_msi_pattern` for wrapped families. Defaults for silent-install switches per family are in `INSTALLER_FAMILY_SWITCHES`. Notepad++ is seeded in the baseline as a worked NSIS example; PowerToys / Adobe Reader DC / Foxit PDF Reader as placeholders for the wrapped families (with notes calling out the per-vendor caveats uncovered during pilots).
- **`wrapped_msi` / `wrapped_zip` extractor pre-stage.** `autopackager/utils/extractors.py::extract_wrapped` unwraps EXE bundlers (Adobe `-sfx_o`, PowerToys `--extract_msi`, etc.) and ZIP archives (Foxit-style enterprise packs) into a destination directory and returns the path to the inner MSI. `cli.py create-software-job` invokes the extractor at the top of the command so the rest of the flow treats the result as a regular MSI job. Tests cover both wrapped types plus the corrupt-input and missing-MSI failure modes; a synthetic round-trip (real MSI inside a ZIP → extract → re-read) confirms the contract end-to-end.
- **Catalog `distribution` field** (`standard | enterprise`). Disambiguates the many COTS apps that ship both a consumer and an enterprise installer for the same product (Adobe Reader DC vs Acrobat Pro for Enterprise, Zoom Workplace vs Zoom for Government, Slack vs Enterprise Grid). Defaults to `standard` for new entries via the CLI; all five baseline entries explicitly marked. A contract test in `tests/unit/test_installer_catalog.py` reads the actual committed baseline and asserts every entry has the field set, so future catalog edits can't leak unmarked entries into main.
- **Full Intune Win32 app attribute coverage** for MSI-derived apps. `DeploymentAgent._prepare_app_data` now emits `msiInformation` (Intune portal's `Version` / `Publisher` / `Product Code` columns read from this, not from `displayVersion` which Graph silently drops for Win32 MSIs), `returnCodes` (standard `MsiExec` exit codes so reboot-required installs aren't marked failed), `informationUrl` (catalog override → MSI `ARPHELPLINK` → driver-vendor map), `notes` (catalog `description` → MSI `Subject`), `largeIcon` (catalog `icon_b64` → MSI's `ARPPRODUCTICON` icon, sniffed and validated to a Graph-compatible PNG), `minimumSupportedOperatingSystem`, and a `categories` $ref sub-collection assignment via the Intune category endpoint. `scripts/backfill_msi_information.py` PATCHes every populatable attribute onto already-published apps (re-reads the MSI when `package_metadata` doesn't carry the newer fields; ignores stored icon bytes whose mime isn't Graph-compatible).
- `cli.py inspect-exe` — analogous to `inspect-msi`: dumps PE VS_VERSIONINFO + SHA-256 + catalog match status.

### Changed

- **MSI metadata parser walks the OLE2 storage tree** instead of iterating all directory entries flat. Real-world MSIs that embed language transforms or feature variants (Webex App, multi-locale Office bundles, anything Wix-bundled with per-locale storages) ship a full `Property` / `_StringPool` / `_StringData` trio inside each sub-storage in addition to the root tables. The previous flat scan let a dict comprehension shadow the root `Property` (164 bytes) with the last transform's `Property` (18 bytes), producing empty `ProductCode` / `Version` / `UpgradeCode` / `Language`. Fix: `_CompoundFile.root_children()` walks the OLE2 red-black sibling tree under the root storage; `_streams_by_table_name` and `_read_summary_information` consider only root-level streams. Pinned via a regression test that builds a synthetic MSI with a poisoned sub-storage Property table.
- **PE VS_VERSIONINFO reader is defensive about `wValueLength`**. Several installer toolchains (WiX Burn — PowerToys uses this — older NSIS, custom Microsoft bootstrappers) set the per-string `wValueLength` field incorrectly. Trusting it produces garbled values that bleed into the next entry's bytes. `_parse_string_file_info` now reads each value as a null-terminated WCHAR string bounded by the enclosing `String` block's `wLength` — matches what Windows' `VerQueryValue` does for the same blobs.
- **`db_session_scope` is now re-entrant on a single thread.** Nested `with db_session_scope()` calls share the outermost Session; only the outermost commits / rolls-back / closes (and calls `_session_factory.remove()` to clear the scoped registry). Resolves a `DetachedInstanceError` in `check_all_deployments` on iter 2: the inner scope opened by `update_deployment_status` was committing and closing the shared `scoped_session`, expiring every ORM object the outer loop was still holding. The existing unit tests mocked both scopes and never exercised the failure path; `tests/unit/test_database.py` covers the four shapes of the regression with real Sessions.
- README "Current Status" reflects EXE support and the attribute-completeness fixes.

### Removed

- Nothing.

### Version

- `__version__` bumped to `1.5.0`.

---

## [1.4.0] - 2026-05-30

### Added
- **Installer catalog** — two-layer YAML registry of known-good silent install/uninstall commands. Committed baseline at `autopackager/data/installer_catalog.yaml` ships seed knowledge (MSI-intrinsic fields only); per-operator overlay at `data/installer_catalog.local.yaml` (gitignored) accumulates use counts and `verified_versions`. `cli.py create-software-job` consults the catalog before requiring `--install-command`; on a miss it prompts interactively; on success it auto-appends to the overlay (override with `--no-save-catalog`). MSI match priority: UpgradeCode → ProductCode → ProductName pattern + Publisher. Local entries override baseline on `id` collision.
- `autopackager/utils/installer_catalog.py` — load/match/append for the catalog, with `Catalog.match_msi`, `Catalog.match_by_product_code`, `add_msi_entry`, `record_use`, and `record_verification`.
- `uninstall_command_template` on every catalog entry. For MSIs, auto-populated from the ProductCode as `msiexec /x {ProductCode} /qn /norestart`, so the catalog file alone is enough to uninstall an app without going back through `PackagingAgent`.
- `verified_versions` list on every catalog entry. `DeploymentAgent.check_all_deployments` hooks `record_verification` after a deployment's `installed_count > 0`, looking up the catalog entry by the package's ProductCode. Idempotent on (product_version, intune_app_id) so repeated polls don't accumulate duplicates.
- `verify_deployment.py` — new regression sibling to `verify_local_packaging.py` that drives Packaging → Testing → Deployment end-to-end and asserts a `Deployment` row persists with the expected ring/group/status. Downloads the 7-Zip MSI on demand so the harness is self-contained on a fresh clone.

### Changed
- **`DeploymentAgent.get_app_device_statuses`** now uses the modern reports endpoint `POST /beta/deviceManagement/reports/retrieveDeviceAppInstallationStatusReport` instead of the retired `/mobileApps/{id}/deviceStatuses` navigation property (Microsoft removed it from both v1.0 and beta `$metadata`). Maps the integer `InstallState` enum back to the lowercase strings the existing `_parse_install_statuses` understands.
- **`DeploymentAgent.check_all_deployments`** captures `deployment_id` / `intune_app_id` / `ring_name` in locals at the top of the loop body to avoid `DetachedInstanceError` after `update_deployment_status()` opens a nested session.
- `azure-setup.ps1` now grants two additional application roles to the SP: `GroupMember.ReadWrite.All` (so the SP can manage Ring 0 membership without an interactive admin) and `DeviceManagementManagedDevices.PrivilegedOperations.All` (so the SP can trigger device syncs via `POST /managedDevices/{id}/syncDevice`).
- `cli.py` reconfigures `sys.stdout`/`sys.stderr` to UTF-8 with `errors="replace"` at module load. Eliminates the cp1252 console crash on all Rich-print sites (✓ / ✗ glyphs in `validate-azure` and elsewhere).
- README "Current Status" table refreshed: adds rows for end-to-end install validation (7-Zip + VLC verified on a real Intune-managed device) and the installer catalog; updates the deployment status polling note to reflect the reports endpoint migration.

### Removed
- Stale one-shot verification reports retired from the repo (`WINDOWS_TESTING.md`, `E2E-VERIFICATION-REPORT.md`, `E2E_VERIFICATION_RESULTS.md`, `BEAT_INTEGRATION_VERIFICATION.md`, `MANUAL_TEST_REPORT.md`). The features they documented are now covered by `tests/` or by current reference docs.
- Third-party MSI fixtures (`data/test_msis/*.msi`) no longer tracked in git. Downloaded on demand by `verify_local_packaging.py` and `verify_deployment.py`.

### Version
- `__version__` bumped to `1.4.0`.

---

## [1.3.0] - 2026-05-20

### Added
- **MSI software packaging** — the pipeline can now package arbitrary MSI applications, not just OEM drivers. Given an MSI (local path and/or download URL) and an `msiexec` install command, AutoPackager reads the MSI's own metadata and auto-fills the Intune package.
- `autopackager/utils/msi_metadata.py` — a dependency-free, cross-platform MSI reader. Parses the MSI's OLE2 compound file and `Property` table (plus the SummaryInformation stream) in pure Python to extract `ProductName`, `ProductVersion`, `ProductCode`, `UpgradeCode`, `Manufacturer`, and `ProductLanguage`. Also provides an `msiexec` command parser (`parse_install_command`), uninstall/detection-rule builders (`build_uninstall_command`, `build_product_code_detection_rule`), and a high-level `inspect_msi()` helper. No external tools, COM, or LLM required.
- New CLI commands:
  - `create-software-job` — creates an MSI packaging job (`--install-command`, `--installer-path`, `--download-url`, `--name`, `--publisher`, `--current-version`).
  - `inspect-msi` — previews the metadata, install/uninstall commands, and Intune detection rule AutoPackager would generate for an MSI, without creating a job.
- Unit tests in `tests/unit/test_msi_metadata.py`, including an end-to-end round-trip that builds a synthetic MSI and reads it back through both the mini-stream and main-FAT code paths.

### Changed
- `DiscoveryAgent._discover_software()` is now implemented: for non-driver jobs it reads MSI metadata (reusing metadata captured at job-creation time, or downloading the MSI when only a URL is supplied) and carries it forward through the job. It previously returned a "Phase 2 not implemented" stub.
- `PackagingAgent` for MSI installers now honors the admin-supplied install command (preserving switches and public properties), prefers `msiexec /x {ProductCode}` for uninstall, emits a `win32LobAppProductCodeRule` detection rule when a product code is known, and copies local/`file://` installer sources instead of fetching over HTTP.
- `discovery_task` persists `msi_metadata` and `install_command` onto the job so packaging can build commands and detection without re-reading the file.
- `__version__` bumped to `1.3.0`.

---

## [1.2.0] - 2026-04-27

### Added
- **Continuous catalog discovery** — new `continuous_catalog_discovery` Celery task and Celery Beat schedule that periodically scans configured OEM catalogs (Dell/HP/Lenovo) for new driver versions and creates packaging jobs automatically. Toggled by `discovery_schedule.enabled`; interval controlled by `discovery_schedule.interval_hours` (default 24). Honours a `monitored_models` list and skips duplicate jobs.
- `DiscoveryRun` SQLAlchemy model and `discovery_runs` table tracking each scheduled scan: `started_at`, `completed_at`, `catalogs_scanned`, `new_versions_found`, `jobs_created`, `oem_results`, `error_message`. Auto-created by `init_db()`.
- New REST endpoints on the dashboard API: `GET /api/discovery/runs`, `GET /api/discovery/runs/{run_id}`. Discovery aggregates added to `GET /api/stats`.
- **Web dashboard** — FastAPI app at `autopackager/web/api.py` with HTML/CSS/JS frontend in `autopackager/web/static/`. Endpoints: `/`, `/health`, `/api/jobs`, `/api/jobs/{id}`, `/api/deployments`, `/api/deployments/rings`, `/api/stats`, `/api/activity`, `/api/discovery/runs`, `/api/discovery/runs/{id}`. Auto-refreshes every 5 seconds. Interactive docs at `/docs` and `/redoc`.
- `start-dashboard.bat` and `start-dashboard.sh` launch scripts for the dashboard server.
- `autopackager/services/dashboard_service.py` aggregates job, deployment, package, and discovery-run data for the dashboard.
- **Comprehensive automated test suite** under `tests/` with pytest configuration in `pytest.ini` and coverage settings in `.coveragerc`. Includes:
  - Unit tests for all four agents, models, and utilities (`tests/unit/`)
  - Integration tests for Celery tasks, the orchestration engine, the full pipeline, and continuous discovery (`tests/integration/`)
  - CLI command tests (`tests/cli/`) and Web API tests (`tests/api/`)
  - Shared fixtures in `tests/conftest.py` and sample OEM catalogs/Graph API mocks in `tests/fixtures/`
- `jobs cancel` and `jobs purge` CLI sub-commands; `worker purge` to drain the Celery queue.

### Changed
- `autopackager/orchestration/celery_app.py` builds `beat_schedule` dynamically based on `status_polling.enabled` and `discovery_schedule.enabled`. Uses `celery.schedules.schedule(run_every=...)` instead of crontab expressions.
- `requirements.txt` adds `fastapi`, `uvicorn`, and test dependencies (`pytest`, `pytest-asyncio`, `pytest-cov`, `responses`).
- `autopackager/models/__init__.py` exports `DiscoveryRun`.

---

## [1.1.2] - 2026-03-21

### Fixed
- `Install-AutoPackager.ps1`: Fixed crash when Windows "App Execution Alias" Python stub is present. The Store stub registers itself with `Get-Command` but writes to stderr and exits non-zero when run; under `$ErrorActionPreference = "Stop"` this throws a `NativeCommandError` before the exit code can be checked. `Get-PythonCommand` now temporarily sets `$ErrorActionPreference = "SilentlyContinue"` while probing each candidate command, captures the exit code explicitly, and skips any command that returns non-zero. Clear actionable instructions (disable the alias in Settings > Apps > Advanced app settings) are shown if no real Python is found.

---

## [1.1.1] - 2026-03-21

### Fixed
- `Install-AutoPackager.ps1`: Replaced all Unicode box-drawing characters (`─`, `╔`, `║`, `╚`, `╝`) with plain ASCII equivalents (`-`, `+`, `|`). PowerShell on Windows reads script files as ANSI (Windows-1252) by default when no BOM is present; the UTF-8 encoded box-drawing characters were corrupting the parser and causing a cascade of false syntax errors throughout the entire script.
- `Install-AutoPackager.ps1`: Renamed `$args` variable to `$installArgs` in the Python silent install block. `$args` is a PowerShell automatic variable and assigning to it is unreliable under `Set-StrictMode -Version Latest`.
- `Install-AutoPackager.ps1`: Fixed `winget` exit code handling — winget returns `-1978335189` (not an exception) when a package is already installed; the old `try/catch` block never caught this, so it would fall through to the direct download unnecessarily.
- `Install-AutoPackager.ps1`: Added retry on pip install failure (single retry); added `--no-warn-script-location` to suppress benign PATH warnings.
- `azure-setup.ps1`: Same Unicode box-drawing character fix applied.

### Added
- `Install-AutoPackager.bat`: Double-click launcher that automatically requests Administrator privileges via UAC and runs the `.ps1` with `-ExecutionPolicy Bypass`. Eliminates the need to manually open an elevated PowerShell or change the system execution policy.

---

## [1.1.0] - 2026-03-21

### Added
- `Install-AutoPackager.ps1` — one-click Windows installer that handles the complete local setup (Python 3.12, Git, Redis, IntuneWinAppUtil.exe, Python venv, SQLite database, data directories, helper scripts) and then calls `azure-setup.ps1` for Azure configuration. Minimum user steps: run script → browser login → paste LLM API key.
- `azure-setup.ps1` — standalone Azure configuration script. Accepts Tenant ID, Client ID, and Client Secret; dynamically looks up Microsoft Graph permission IDs; adds all required API permissions; grants admin consent; creates the 4 deployment ring security groups in Entra ID; writes a complete `.env` file. Supports `-CreateAppRegistration` to skip the Azure Portal entirely.
- `launch-all.bat` helper script (created by installer) to start Redis and Celery worker in separate windows with one command.
- Documentation fully updated to reflect new automated installation path across README, IMPLEMENTATION_GUIDE, AUTOMATED_SETUP, QUICKSTART_CHECKLIST, and SETUP.

### Changed
- README Quick Start section now leads with `Install-AutoPackager.ps1` as the primary installation method.
- IMPLEMENTATION_GUIDE restructured: automated path is now the primary section; manual setup moved to a collapsible reference section. Estimated time updated from ~90 minutes to ~10–15 minutes.
- AUTOMATED_SETUP rewritten to document both new scripts with full parameter reference and examples.
- QUICKSTART_CHECKLIST updated to distinguish automated (`[AUTO]`) tasks from manual ones.
- SETUP.md updated with a prominent callout at the top pointing to the automated installer; missing `GroupMember.Read.All` permission added to manual steps.

---

## [1.0.0] - 2026-03-20

### Added
- Windows app packaging reference documentation with step-by-step guidance for deploying applications on Windows platforms
- Navigation links to packaging documentation in README and implementation guides for easy access