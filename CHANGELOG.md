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