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