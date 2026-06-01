# AutoPackager - Automated Driver Packaging for Microsoft Intune

## Catalog-driven automation for packaging and deploying OEM drivers via Microsoft Intune

AutoPackager automates the driver update lifecycle for Dell, HP, and Lenovo hardware: it
checks OEM catalogs for new versions, downloads and packages them as `.intunewin` Win32
apps, runs basic validation, and publishes them to Microsoft Intune using a phased
deployment-ring strategy.

It also packages **MSI software applications** through the same pipeline. Given an MSI and
its install command (for example `msiexec /i 7z2408-x64.msi /qn /norestart`), AutoPackager
reads the MSI's own metadata — product name, version, publisher, product code — and uses it
to auto-fill the Intune app, generate the uninstall command and product-code detection rule,
and roll the package out across the same deployment rings. See
[Packaging MSI Software](#packaging-msi-software).

> **Note on "AI":** Phase 1 is **deterministic** — discovery parses published OEM XML/CAB
> catalogs and install commands are generated from file-type heuristics. There is **no LLM
> in the current code path.** LLM-powered discovery and silent-install-parameter research
> are planned for Phase 2 (see [Roadmap](#roadmap)). Any LLM API key collected by the
> installer is reserved for those future phases and is not used today.

## Overview

Manual driver packaging is a significant bottleneck for IT departments. AutoPackager
reduces that effort by automating the path from "a new driver appeared in the OEM catalog"
to "a tested Win32 app is published and assigned to a pilot ring in Intune" — with optional
automatic promotion across rings and automatic rollback on high failure rates.

## Key Features

- **Catalog-based discovery**: Detects new driver versions by parsing Dell, HP, and Lenovo OEM catalogs (HP support is currently partial — see [Current Status](#current-status))
- **MSI software packaging**: Reads MSI metadata (product name, version, publisher, product code) directly from the file and auto-fills the Intune app, uninstall command, and product-code detection rule from an install command — no LLM required (see [Packaging MSI Software](#packaging-msi-software))
- **Win32 packaging**: Downloads installers/driver packs and builds `.intunewin` packages (CAB driver packs are wrapped with a generated `pnputil` install script)
- **Intune publishing**: Full Graph API content-upload flow (chunked Azure Blob upload, encryption metadata, publish)
- **Basic validation**: Smoke checks on package files and commands, plus optional Hyper-V VM-based install testing
- **Phased deployment**: Deployment-ring strategy (IT Pilot → Early Adopters → Broad → Critical) with optional automatic promotion
- **Automatic rollback**: Reverts to the last known-good package when a deployment's failure rate exceeds a configurable threshold
- **Web dashboard & CLI**: FastAPI dashboard and a `click`-based CLI for job and deployment management

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      DATA SOURCES                           │
│   OEM Catalogs (Dell/HP/Lenovo) | Software Repos | CMDB    │
└──────────────────────┬──────────────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────────────┐
│              ORCHESTRATION ENGINE (Celery + Redis)          │
│   Job Queue | State Machine | Logging | Monitoring          │
└──────────────────────┬──────────────────────────────────────┘
            ┌──────────┴──────────┬──────────────┐
            ▼                     ▼              ▼
    ┌──────────────┐    ┌──────────────┐   ┌──────────────┐
    │ DISCOVERY    │    │ PACKAGING    │   │ TESTING      │
    │ AGENT        │ →  │ AGENT        │ → │ AGENT        │
    │ • Catalog    │    │ • Download   │   │ • Smoke Test │
    │   parse      │    │ • CAB/pnputil│   │ • VM Test    │
    │ • Dell/HP/   │    │ • MSI meta   │   │   (Hyper-V,  │
    │   Lenovo     │    │ • .intunewin │   │    optional) │
    │ • MSI meta   │    │              │   │              │
    └──────────────┘    └──────────────┘   └──────────────┘
                                                  │
                                                  ▼
                                          ┌──────────────┐
                                          │ DEPLOYMENT   │
                                          │ AGENT        │
                                          │ • Graph API  │
                                          │ • Rings      │
                                          └──────┬───────┘
                                                 ▼
                                    ┌─────────────────────┐
                                    │ MICROSOFT INTUNE    │
                                    └─────────────────────┘
```

## Phase 1: Driver Management Automation

The initial phase focuses on automating driver updates for Dell, HP, and Lenovo hardware.

### Current Status

| Capability | Status | Notes |
|------------|--------|-------|
| Orchestration engine (Celery + Redis) | ✅ Working | Discovery → Packaging → Testing → Deployment pipeline with per-stage retries |
| Dell driver discovery | ✅ Working | Parses `DriverPackCatalog.cab` |
| Lenovo driver discovery | ✅ Working | Parses `catalogv2.xml` |
| HP driver discovery | ⚠️ Partial | Catalog parsing returns placeholder SoftPaq URLs; not production-ready |
| MSI software packaging | ✅ Working | Reads MSI metadata (OLE2/Property table, sub-storage-aware) to auto-fill name, version, publisher, product code; builds install/uninstall commands and product-code detection. Deterministic — no LLM |
| **EXE software packaging** | ✅ Working | Reads PE `VS_VERSIONINFO` (`autopackager/utils/pe_metadata.py`); catalog matches by SHA-256 / `pe_company_name` + `pe_product_name`; install command from catalog `install_command_template` or `INSTALLER_FAMILY_SWITCHES` default; detection from catalog `detection_rules`. Refuses to enqueue without a catalog hit (no detection rule = perpetual reinstall) |
| **Wrapped installers (`wrapped_msi` / `wrapped_zip`)** | ⚠️ Mechanism wired, baseline entries unverified | `autopackager/utils/extractors.py` unwraps EXE bundlers (`-sfx_o`, `--extract_msi`) and ZIPs into the data/downloads/extracted/ cache; the rest of the pipeline runs against the inner MSI. PowerToys / Adobe Reader DC / Foxit Reader baseline entries seeded as documented placeholders |
| Packaging → `.intunewin` | ✅ Working | Requires Windows + `IntuneWinAppUtil.exe`; produces a placeholder file if the tool is absent |
| Intune publishing (Graph API) | ✅ Working | Full content-upload + publish flow. Win32 app payload now carries the full attribute set: `msiInformation`, `returnCodes`, `informationUrl`, `notes`, `largeIcon`, `categories` (via the $ref sub-collection), `minimumSupportedOperatingSystem` |
| **End-to-end install on managed device** | ✅ Verified | 7-Zip 24.08, VLC 3.0.23, KeePass 2.61.1, Node.js 24.16.0 LTS, Slack 4.48.102, Zoom 7.0.38856, Webex 46.5.0.35006 (all MSI), and Notepad++ 8.9.6.2 (EXE / NSIS) — pushed via the Celery pipeline, assigned to Ring 0, observed installing on a real Intune-managed device, then confirmed via per-device install report. All also uninstalled cleanly via their catalog uninstall commands |
| **Installer catalog** | ✅ Working | Two-layer YAML catalog. Each entry carries: type (`msi` / `exe`), `installer_family` (controlled vocabulary: `msi`, `inno_setup`, `nsis`, `wix_burn`, `msft_bootstrapper`, `wrapped_msi`, `wrapped_zip`, `custom`), `distribution` (`standard` / `enterprise`), install + uninstall command templates, `detection_rules` (6 catalog-native rule kinds → `win32LobApp*Rule` Graph payloads), Intune attribute overrides (`information_url`, `description`, `categories`, `min_os_version`, `icon_b64`), wrapped-extraction config, and `verified_versions` tracking. Local overlay overrides baseline on `id` collision |
| Testing | ⚠️ Basic | Smoke checks (file/command/rules) by default; real install testing requires Hyper-V (Windows host) |
| Azure VM testing | ❌ Not implemented | Provider raises "not yet implemented" |
| Deployment rings (0–3) | ✅ Working | Initial assignment to Ring 0 on deploy; `_create_deployment_record` writes the tracking row |
| Automatic ring promotion | ✅ Working | Scheduled via Celery Beat when `ring_promotion.auto_promote` is enabled |
| Automatic rollback | ✅ Working | Evaluated during status polling against `rollback.failure_threshold_percent` |
| Intune-native Driver Update Profiles | ⚠️ Available, not wired | `DeploymentAgent.deploy_driver_update_profile()` exists but the default pipeline uses the Win32 path |
| Win32 supersedence | ❌ Stub | `_create_supersedence()` is a placeholder. Planned next: drive supersedence off the installer catalog so promoting a new MSI version auto-unassigns the old one |
| Continuous catalog discovery | ✅ Working | Celery Beat scheduled OEM catalog scanning |
| Deployment status polling | ✅ Working | Uses the modern `POST /beta/.../retrieveDeviceAppInstallationStatusReport` endpoint (the legacy `mobileApps/{id}/deviceStatuses` nav property was retired by Microsoft); records verified versions back into the installer catalog overlay |
| Database tracking | ✅ Working | SQLite by default; PostgreSQL optional (install `psycopg2`) |
| CLI | ✅ Working | `init`, `create-driver-job`, `create-software-job` (MSI + EXE dispatch, wrapped-installer unwrap pre-stage), `inspect-msi`, `inspect-exe`, `jobs list/status/cancel/promote/halt-promotion/purge`, `worker start/purge`, `validate-azure` (`jobs rollback` is a stub — rollback runs automatically via polling) |
| Web dashboard (FastAPI + REST) | ✅ Working | Job, deployment, discovery, and stats endpoints |
| LLM-driven discovery / install-param research | ❌ Planned (Phase 2) | No LLM is used in the current code |
| COTS / general software discovery | ⚠️ Partial | MSI applications are packaged from a supplied install command + MSI metadata (see [Packaging MSI Software](#packaging-msi-software)). Automatic *version* discovery for software (checking vendors for updates) is still Phase 2 |
| Automated test suite | ✅ Working | 597+ tests passing — unit, integration, CLI, API |

## Quick Start

### 🚀 One-Click Installation (Recommended)

Run a single script as Administrator — it installs and configures everything automatically.

**What you need before running:**
- Windows workstation with local administrator rights
- Azure account with Global Admin or Application/Group Administrator role
- (Optional) An LLM API key ([OpenAI](https://platform.openai.com/api-keys) or [Anthropic](https://console.anthropic.com/settings/keys)) — **not used in Phase 1**; reserved for the planned Phase 2 LLM features

**Easiest:** double-click `Install-AutoPackager.bat` — it handles elevation automatically.

**Or from PowerShell (Run as Administrator):**
```powershell
.\Install-AutoPackager.ps1
```

**The script handles everything:**
- ✅ Installs Python 3.12, Git, Redis, and IntuneWinAppUtil.exe
- ✅ Creates Python virtual environment and installs all dependencies
- ✅ Creates the Azure App Registration (or configures an existing one)
- ✅ Adds all required Microsoft Graph API permissions and grants admin consent
- ✅ Creates the 4 deployment ring security groups in Entra ID
- ✅ Writes a complete `.env` configuration file
- ✅ Creates helper scripts: `launch-all.bat`, `create-job.bat`, `list-jobs.bat`

**Minimum manual steps:**
1. Run `.\Install-AutoPackager.ps1`
2. Log in to Azure when the browser opens
3. (Optional) Paste an LLM API key when prompted — this is stored for future Phase 2 use and is not required for driver automation

**See [AUTOMATED_SETUP.md](AUTOMATED_SETUP.md) for all options and flags**

### Already Have an App Registration?

If you've already created an App Registration in Azure Portal, just provide the values when prompted — `azure-setup.ps1` handles all remaining Azure tasks:

```powershell
# Handles groups, permissions, admin consent, and .env generation
.\azure-setup.ps1 -OutputEnvFile
```

### Linux/WSL/Mac Setup

```bash
chmod +x setup.sh
./setup.sh --sqlite
# Then run: .\azure-setup.ps1 on Windows to configure Azure
```

### Manual Installation (Advanced)

<details>
<summary>Click to expand manual installation steps</summary>

**Prerequisites:**
- Python 3.9+
- PostgreSQL 12+ or SQLite
- Redis 6.0+
- Microsoft Azure subscription with Intune
- IntuneWinAppUtil.exe

**Steps:**
```bash
# Clone repository
git clone <repository-url>
cd DriverSearchandDeploy

# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.template .env
# Edit .env with your Azure credentials

# Initialize database
python cli.py init

# Start worker
python cli.py worker start
```

See [IMPLEMENTATION_GUIDE.md](IMPLEMENTATION_GUIDE.md) for full manual setup instructions.

</details>

### Create Your First Driver Job

```bash
# Lenovo ThinkPad example
python cli.py create-driver-job \
  --vendor lenovo \
  --model "ThinkPad X1 Carbon Gen 9" \
  --driver-type "chipset" \
  --current-version "1.0.0"

# HP EliteBook example
python cli.py create-driver-job \
  --vendor hp \
  --model "EliteBook 850 G8" \
  --driver-type "network" \
  --current-version "2.1.0"
```

### Monitor Progress

```bash
# List all jobs
python cli.py jobs list

# Get detailed status
python cli.py jobs status <job-id>
```

## Packaging MSI Software

Beyond OEM drivers, AutoPackager can package any MSI application. You provide the MSI (a
local path and/or a download URL) and its `msiexec` install command; the factory reads the
MSI's metadata and fills in the rest, then runs it through the same packaging → testing →
deployment-ring pipeline.

### How it works

1. **Read metadata.** AutoPackager parses the MSI's OLE2 compound file and `Property` table
   in pure Python (no external tools, COM, or LLM) to extract **ProductName**,
   **ProductVersion**, **ProductCode**, **UpgradeCode**, and **Manufacturer**.
2. **Auto-fill the package.** Product name → Intune display name, Manufacturer → publisher,
   ProductVersion → display version.
3. **Build commands.** The supplied install command is preserved (switches and public
   properties intact); the uninstall command is generated as
   `msiexec /x {ProductCode} /qn /norestart`.
4. **Generate detection.** A Win32 MSI **product-code detection rule** is created from the
   ProductCode and ProductVersion — far more reliable than a synthetic registry key.
5. **Deploy.** The package flows through testing and is published to Intune and assigned to
   Ring 0, with the usual automatic ring promotion and rollback.

### Preview an MSI (no job created)

Inspect what AutoPackager would generate before committing to a job:

```bash
python cli.py inspect-msi "C:\Downloads\7z2408-x64.msi" \
  --install-command "msiexec /i 7z2408-x64.msi /qn /norestart"
```

Example output:

```
        MSI Metadata: 7z2408-x64.msi
  Product Name   7-Zip 24.08 (x64)
  Version        24.08.00.0
  Publisher      Igor Pavlov
  Product Code   {23170F69-40C1-2702-2408-000001000000}
  ...
Generated package commands:
  Install:   msiexec /i 7z2408-x64.msi /qn /norestart
  Uninstall: msiexec /x {23170F69-40C1-2702-2408-000001000000} /qn /norestart
Intune detection rule:
  Type:    #microsoft.graph.win32LobAppProductCodeRule
  Product: {23170F69-40C1-2702-2408-000001000000}
  Version: 24.08.00.0 (greaterThanOrEqual)
```

### Create an MSI software job

```bash
# From a local MSI (metadata read immediately)
python cli.py create-software-job \
  --install-command "msiexec /i 7z2408-x64.msi /qn /norestart" \
  --installer-path "C:\Downloads\7z2408-x64.msi"

# From a download URL (MSI fetched and inspected during the pipeline)
python cli.py create-software-job \
  --install-command "msiexec /i 7z2408-x64.msi /qn /norestart" \
  --download-url "https://www.7-zip.org/a/7z2408-x64.msi"
```

Provide at least one of `--installer-path` or `--download-url`. Use `--name` / `--publisher`
to override the values read from the MSI, and `--current-version` to record the version
already installed.

### Installer catalog (`--install-command` is now optional)

AutoPackager keeps a known-good silent-install command for each MSI it has seen, so the
second time you deploy the same app you don't have to remember its flags. The catalog has
two layers:

- `autopackager/data/installer_catalog.yaml` — committed baseline (seed knowledge curated
  in-repo; treated as read-only at runtime). Ships with 7-Zip seeded.
- `data/installer_catalog.local.yaml` — gitignored, operator-private overlay. Every
  successful `create-software-job` run auto-appends here (override with `--no-save-catalog`).

When `--install-command` is omitted, AutoPackager:

1. Reads the MSI's metadata (UpgradeCode, ProductCode, ProductName, Manufacturer).
2. Looks the installer up in the catalog (UpgradeCode → ProductCode → ProductName +
   Publisher).
3. On a hit, uses the recorded `install_command_template`.
4. On a miss, prompts you interactively (with a sensible default).

Either way, the rest of the pipeline (Packaging → Testing → Deployment → Ring 0) is
unchanged. To override the catalog's recorded flags for a single run, pass
`--install-command "msiexec /i installer.msi /qn ADDLOCAL=ALL"`. To make the override
permanent on this tenant, edit `data/installer_catalog.local.yaml` directly — a local
entry with the same `id` as a baseline entry wins on load.

#### Uninstall command + verified versions

Each MSI catalog entry also records:

- `uninstall_command_template` — auto-populated at append time as
  `msiexec /x {ProductCode} /qn /norestart`. Lets you uninstall the package by
  reading the catalog directly, without going through `PackagingAgent`. The MSI
  ProductCode is embedded literally, so the template can be run verbatim.
- `verified_versions` — populated by the deployment status poller
  (`check_all_deployments`) when at least one device reports a successful install.
  Each entry: `product_version`, `verified_at` (date), `verified_intune_app_id`.
  Idempotent — re-running the poll doesn't add duplicate verified entries for the
  same `(version, app_id)` pair.

Reading a catalog file end-to-end tells the next operator three things about each app:
*what command silently installs it*, *what command cleanly removes it*, and *which
versions we've watched land on a real device*.

> **Note:** This packages a *specific* MSI you supply. Automatically discovering new software
> *versions* from vendors (the way driver discovery scans OEM catalogs) remains a Phase 2
> item — see [Roadmap](#roadmap).

## Configuration

Configure AutoPackager via `autopackager/config/config.yaml`:

- **Database**: SQLite (default) or PostgreSQL connection settings
- **Redis**: Celery task queue configuration
- **Intune**: Azure tenant, client credentials, Graph API settings
- **OEM Catalogs**: Dell, HP, Lenovo catalog URLs
- **Deployment Rings**: Entra ID group assignments and deferral periods
- **Ring Promotion**: dwell time, success thresholds, and auto-promote toggle
- **Rollback**: failure threshold and minimum install count
- **Testing**: VM provider and test settings
- **LLM**: present in config but unused in Phase 1 (reserved for Phase 2)

## Upgrading

### Upgrading to Version 2.x (VM-Based Testing Feature)

The VM-based testing feature adds a new `vm_test_results` column to the Package model to store detailed test results from VM-based driver installation validation.

#### For New Installations

No action needed - the column is created automatically when initializing the database.

#### For Existing Installations

**Option 1: Add Column Manually** (Preserves existing data)

Run this SQL command against your database:

**PostgreSQL:**
```sql
ALTER TABLE packages ADD COLUMN vm_test_results JSON DEFAULT '{}';
```

**SQLite:**
```bash
sqlite3 autopackager.db "ALTER TABLE packages ADD COLUMN vm_test_results TEXT DEFAULT '{}';"
```

**Option 2: Recreate Database** (Development only - **DESTROYS ALL DATA**)

```bash
# Backup first!
cp autopackager.db autopackager.db.backup

# Drop and recreate
rm autopackager.db
python cli.py init
```

#### Verify Upgrade

```python
from autopackager.models.package import Package
from autopackager.utils.database import db_session_scope

with db_session_scope() as session:
    package = session.query(Package).first()
    if package:
        print(f"vm_test_results field: {package.vm_test_results}")
    else:
        print("No packages in database yet - schema is ready")
```

**⚠️ Important:** Test the migration on a copy of your production database before applying to production.

## Deployment Rings

AutoPackager uses a phased rollout strategy:

| Ring | Name | Description | Deferral |
|------|------|-------------|----------|
| Ring 0 | IT Pilot | IT staff testing | 0 days |
| Ring 1 | Early Adopters | Volunteer users | 3 days |
| Ring 2 | Broad Deployment | Majority of users | 7 days |
| Ring 3 | Critical Systems | High-stability devices | 14 days |

## Technology Stack

- **Orchestration**: Python, Celery, Redis
- **Database**: SQLite (default) / PostgreSQL
- **Intune**: Microsoft Graph API (`v1.0` for Win32 apps, `beta` for Driver Update Profiles)
- **Packaging**: `IntuneWinAppUtil.exe`, generated PowerShell/`pnputil` scripts for CAB driver packs
- **Web**: FastAPI + Uvicorn
- **CLI**: Click, Rich (terminal UI)
- **Logging**: Structlog, Python-JSON-Logger
- **LLM (Phase 2, not yet used)**: OpenAI / Anthropic SDKs are installed but not invoked in Phase 1

## Project Structure

```
DriverSearchandDeploy/
├── autopackager/           # Main application
│   ├── agents/            # Discovery, Packaging, Testing, Deployment
│   ├── config/            # Configuration files (config.yaml)
│   ├── models/            # Database models (Job, Package, Deployment, DiscoveryRun)
│   ├── orchestration/     # Celery app, tasks, and orchestration engine
│   ├── services/          # Dashboard data aggregation service
│   ├── utils/             # Config, logging, database, Graph client
│   └── web/               # FastAPI dashboard (api.py + static/)
├── data/                  # Runtime data (downloads, packages, logs, catalogs)
├── docs/                  # Architecture documentation (PIPELINE_LIFECYCLE.md)
├── scripts/               # Example helper scripts
├── tests/                 # pytest suite (unit, integration, cli, api, fixtures)
├── tools/                 # IntuneWinAppUtil.exe, Redis (created by installer)
├── cli.py                 # Command-line interface
├── Install-AutoPackager.bat  # Double-click launcher (handles elevation)
├── Install-AutoPackager.ps1  # One-click Windows installer
├── azure-setup.ps1           # Automated Azure configuration
├── setup.ps1                 # Legacy Windows setup script
├── setup.sh                  # Linux/Mac setup script
├── start-dashboard.bat       # Launch FastAPI dashboard (Windows)
├── start-dashboard.sh        # Launch FastAPI dashboard (Linux/Mac)
└── requirements.txt       # Python dependencies
```

## Limitations & Known Gaps

Be aware of the following before relying on AutoPackager in production:

- **Windows required for real packaging.** `.intunewin` creation depends on `IntuneWinAppUtil.exe`. On other platforms (or when the tool is missing) packaging produces a placeholder file that cannot be published.
- **HP discovery is incomplete.** The HP catalog parser returns placeholder SoftPaq URLs and should not be used to drive real HP driver jobs yet.
- **Testing is shallow by default.** The smoke test only validates that package files exist and commands/detection rules are well-formed. Real installation testing requires a configured Hyper-V host; the Azure VM provider is not implemented.
- **Detection rules are generated heuristically (drivers).** The default registry detection rule for drivers is a best-effort template and may need manual adjustment. MSI software packages instead get a precise product-code detection rule read from the MSI.
- **Software packaging is MSI-only.** The metadata-driven path reads MSI files; `.exe` and other installer types still fall back to file-type heuristics for their install commands.
- **No automatic software version discovery yet.** MSI packaging works from an installer you supply; it does not yet scan vendors for newer software releases (Phase 2).
- **No LLM features yet.** Discovery and install-command generation are fully deterministic. The `llm` config block and the OpenAI/Anthropic dependencies are unused.
- **Supersedence is not implemented.** New versions are published as separate apps; old versions are not automatically superseded.
- **Rollback requires a prior known-good package.** Automatic rollback only works if an earlier deployed + tested package for the same name exists in the database.

## Roadmap

### Phase 2: COTS Software Update Automation (In Progress)
- ✅ **MSI software packaging from metadata + install command** (delivered) — see [Packaging MSI Software](#packaging-msi-software)
- ⬜ LLM-powered software *version* discovery (checking vendors for new releases)
- ⬜ Silent install parameter research for non-MSI installers (`.exe`)
- ⬜ Support for 50+ common applications (Chrome, Adobe, 7-Zip, etc.)
- ⬜ Full PSADT integration

### Phase 3: New Software and Full Autonomy (Planned)
- User-facing portal for software requests
- AI-driven UAT with UI automation
- Self-healing deployment capabilities
- Full CI/CD pipeline ("Desktop as Code")

## Success Metrics

| Metric | Target |
|--------|--------|
| Reduction in manual effort (Phase 1) | 90% |
| Time from driver release to deployment | < 72 hours |
| Supported COTS applications (Phase 2) | 50+ |
| Zero-touch update success rate | 80% |
| First-time deployment success (Phase 3) | 95% |

## Documentation

### Getting Started
- **🚀 One-Click Installer**: `Install-AutoPackager.ps1` — run this first
- **☁️ Azure Setup Script**: `azure-setup.ps1` — automates all Azure configuration
- **📋 Implementation Guide**: [IMPLEMENTATION_GUIDE.md](IMPLEMENTATION_GUIDE.md) — step-by-step walkthrough
- **✅ Quick Start Checklist**: [QUICKSTART_CHECKLIST.md](QUICKSTART_CHECKLIST.md) — track your progress

### Reference Documentation
- **Automated Setup Guide**: [AUTOMATED_SETUP.md](AUTOMATED_SETUP.md) — script options and flags
- **Manual Setup Guide**: [SETUP.md](SETUP.md) — detailed manual installation reference
- **Configuration Reference**: [CONFIG_REFERENCE.md](CONFIG_REFERENCE.md) — every `config.yaml` field with valid values, defaults, and scenarios
- **Pipeline Lifecycle**: [docs/PIPELINE_LIFECYCLE.md](docs/PIPELINE_LIFECYCLE.md) — job orchestration state machine and task flow
- **Test Suite Guide**: [tests/README.md](tests/README.md) — pytest layout, fixtures, and coverage targets
- **Changelog**: [CHANGELOG.md](CHANGELOG.md) — release history
- **Technical Whitepaper**: [automated_software_packaging_whitepaper.md](automated_software_packaging_whitepaper.md)
- **PR/FAQ**: [PRFAQ_ Project AutoPackager.md](PRFAQ_%20Project%20AutoPackager.md)
- **Driver Updates Reference**: [ch04-driver-updates-reference.md](ch04-driver-updates-reference.md) — Intune driver update profiles and Graph API endpoints
- **Windows App Packaging**: [ch11-windows-app-packaging-reference.md](ch11-windows-app-packaging-reference.md) — Win32 app packaging patterns and best practices

### Web Dashboard

A FastAPI dashboard exposes real-time job, deployment, and discovery state.

```bash
# Linux/Mac
./start-dashboard.sh

# Windows
.\start-dashboard.bat

# Manual (any platform)
python -m uvicorn autopackager.web.api:app --host 0.0.0.0 --port 8000
```

Then open <http://localhost:8000>. Interactive API docs are available at `/docs` (Swagger UI) and `/redoc`.

## License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.

## Repository History Notice

This repository was sanitized for public release. Git history prior to the initial public release may contain development artifacts (e.g., temporary files, debug output, or iterative changes). If you require a completely clean history, consider using [BFG Repo Cleaner](https://rtyley.github.io/bfg-repo-cleaner/) or [git filter-repo](https://github.com/newren/git-filter-repo).

## Credits

Built with AI assistance.
Version 1.3.0 — Phase 1 (driver automation): catalog-based discovery, Win32 packaging and
Intune publishing, deployment rings with automatic promotion and rollback, continuous
catalog discovery, status polling, web dashboard, and CLI. Adds MSI software packaging:
metadata-driven Intune apps built from an install command (the start of Phase 2).
