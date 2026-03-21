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