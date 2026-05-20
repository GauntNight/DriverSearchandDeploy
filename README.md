# AutoPackager - Automated Driver Packaging for Microsoft Intune

## Catalog-driven automation for packaging and deploying OEM drivers via Microsoft Intune

AutoPackager automates the driver update lifecycle for Dell, HP, and Lenovo hardware: it
checks OEM catalogs for new versions, downloads and packages them as `.intunewin` Win32
apps, runs basic validation, and publishes them to Microsoft Intune using a phased
deployment-ring strategy.

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
    │ • Dell/HP/   │    │ • .intunewin │   │   (Hyper-V,  │
    │   Lenovo     │    │              │   │    optional) │
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
| Packaging → `.intunewin` | ✅ Working | Requires Windows + `IntuneWinAppUtil.exe`; produces a placeholder file if the tool is absent |
| Intune publishing (Graph API) | ✅ Working | Full content-upload + publish flow |
| Testing | ⚠️ Basic | Smoke checks (file/command/rules) by default; real install testing requires Hyper-V (Windows host) |
| Azure VM testing | ❌ Not implemented | Provider raises "not yet implemented" |
| Deployment rings (0–3) | ✅ Working | Initial assignment to Ring 0 on deploy |
| Automatic ring promotion | ✅ Working | Scheduled via Celery Beat when `ring_promotion.auto_promote` is enabled |
| Automatic rollback | ✅ Working | Evaluated during status polling against `rollback.failure_threshold_percent` |
| Intune-native Driver Update Profiles | ⚠️ Available, not wired | `DeploymentAgent.deploy_driver_update_profile()` exists but the default pipeline uses the Win32 path |
| Win32 supersedence | ❌ Stub | `_create_supersedence()` is a placeholder |
| Continuous catalog discovery | ✅ Working | Celery Beat scheduled OEM catalog scanning |
| Deployment status polling | ✅ Working | Syncs Intune per-device install state |
| Database tracking | ✅ Working | SQLite by default; PostgreSQL optional (install `psycopg2`) |
| CLI | ✅ Working | `init`, `create-driver-job`, `jobs list/status/cancel/promote/halt-promotion/purge`, `worker start/purge`, `validate-azure` (`jobs rollback` is a stub — rollback runs automatically via polling) |
| Web dashboard (FastAPI + REST) | ✅ Working | Job, deployment, discovery, and stats endpoints |
| LLM-driven discovery / install-param research | ❌ Planned (Phase 2) | No LLM is used in the current code |
| COTS / general software discovery | ❌ Planned (Phase 2) | Driver updates only today |
| Automated test suite | ✅ Working | unit, integration, CLI, API |

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
- **Detection rules are generated heuristically.** The default registry detection rule is a best-effort template and may need manual adjustment per driver.
- **No LLM features yet.** Discovery and install-command generation are fully deterministic. The `llm` config block and the OpenAI/Anthropic dependencies are unused in Phase 1.
- **Supersedence is not implemented.** New versions are published as separate apps; old versions are not automatically superseded.
- **Rollback requires a prior known-good package.** Automatic rollback only works if an earlier deployed + tested package for the same name exists in the database.

## Roadmap

### Phase 2: COTS Software Update Automation (Planned)
- LLM-powered software version discovery
- Silent install parameter research
- Support for 50+ common applications (Chrome, Adobe, 7-Zip, etc.)
- Full PSADT integration

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
Version 1.2.0 — Phase 1 (driver automation): catalog-based discovery, Win32 packaging and
Intune publishing, deployment rings with automatic promotion and rollback, continuous
catalog discovery, status polling, web dashboard, and CLI.
