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