# AutoPackager - Autonomous Software Packaging Factory

## An AI-Powered Platform for Automated Software and Driver Deployment via Microsoft Intune

AutoPackager is an autonomous software packaging and deployment factory that revolutionizes enterprise desktop management by treating it as Infrastructure as Code (IaC). The system automates the entire lifecycle of software and driver updates from discovery to deployment through Microsoft Intune.

## Overview

Manual software packaging is a significant bottleneck for IT departments, consuming thousands of hours annually in repetitive tasks. AutoPackager solves this by creating a closed-loop, autonomous system that leverages Large Language Models (LLMs) to automate key aspects of the packaging process.

## Key Features

- **AI-Driven Discovery**: Automatically detects new driver and software versions from OEM catalogs and vendor websites
- **Intelligent Packaging**: Downloads, packages, and creates .intunewin packages with automated silent installation parameter research
- **Automated Testing**: Validates packages through smoke tests and VM-based testing
- **Phased Deployment**: Deploys to Microsoft Intune with deployment ring strategy (IT Pilot → Early Adopters → Broad → Critical)
- **Zero-Touch Operation**: End-to-end automation from discovery to deployment with minimal human intervention
- **Desktop as Code**: Treats desktop software configuration as version-controlled code

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
    │ • LLM Search │    │ • Download   │   │ • Smoke Test │
    │ • Catalogs   │    │ • PSADT      │   │ • VM Test    │
    └──────────────┘    │ • .intunewin │   └──────────────┘
                        └──────────────┘          │
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

The initial phase focuses on automating driver and BIOS updates for Dell, HP, and Lenovo hardware.

**What's Implemented:**
- ✅ OEM driver catalog integration (HP HPIA, Lenovo, Dell)
- ✅ Orchestration engine with Celery task queue
- ✅ Discovery agent for version checking
- ✅ Packaging agent for .intunewin creation
- ✅ Testing agent with smoke tests
- ✅ Deployment agent with Intune Graph API integration
- ✅ Deployment ring support (Ring 0-3)
- ✅ Database tracking with PostgreSQL / SQLite
- ✅ CLI interface for job management

## Quick Start

### 🚀 One-Click Installation (Recommended)

Run a single script as Administrator — it installs and configures everything automatically.

**What you need before running:**
- Windows workstation with local administrator rights
- Azure account with Global Admin or Application/Group Administrator role
- An LLM API key ([OpenAI](https://platform.openai.com/api-keys) or [Anthropic](https://console.anthropic.com/settings/keys))

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
3. Paste your LLM API key when prompted

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

- **Database**: PostgreSQL or SQLite connection settings
- **Redis**: Celery task queue configuration
- **Intune**: Azure tenant, client credentials, Graph API settings
- **LLM**: OpenAI/Anthropic API configuration
- **OEM Catalogs**: Dell, HP, Lenovo catalog URLs
- **Deployment Rings**: Entra ID group assignments and deferral periods
- **Testing**: VM provider and test settings

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
- **Database**: PostgreSQL / SQLite
- **LLM**: OpenAI GPT-4 / Anthropic Claude
- **Packaging**: PowerShell, PSADT (future)
- **Intune**: Microsoft Graph API, IntuneWin32App module
- **CLI**: Click, Rich (terminal UI)
- **Logging**: Structlog, Python-JSON-Logger

## Project Structure

```
DriverSearchandDeploy/
├── autopackager/           # Main application
│   ├── agents/            # Discovery, Packaging, Testing, Deployment
│   ├── config/            # Configuration files
│   ├── models/            # Database models (Job, Package, Deployment)
│   ├── orchestration/     # Celery tasks and engine
│   └── utils/             # Config, logging, database, Graph client
├── data/                  # Runtime data (downloads, packages, logs)
├── scripts/               # Helper scripts
├── tools/                 # IntuneWinAppUtil.exe, Redis
├── cli.py                 # Command-line interface
├── Install-AutoPackager.bat  # Double-click launcher (handles elevation)
├── Install-AutoPackager.ps1  # One-click Windows installer
├── azure-setup.ps1           # Automated Azure configuration
├── setup.ps1                 # Legacy Windows setup script
├── setup.sh                  # Linux/Mac setup script
└── requirements.txt       # Python dependencies
```

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
- **Pipeline Lifecycle**: [docs/PIPELINE_LIFECYCLE.md](docs/PIPELINE_LIFECYCLE.md) — job orchestration state machine and task flow
- **Technical Whitepaper**: [automated_software_packaging_whitepaper.md](automated_software_packaging_whitepaper.md)
- **PR/FAQ**: [PRFAQ_ Project AutoPackager.md](PRFAQ_%20Project%20AutoPackager.md)
- **Windows App Packaging**: [ch11-windows-app-packaging-reference.md](ch11-windows-app-packaging-reference.md) — Win32 app packaging patterns and best practices

## License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.

## Repository History Notice

This repository was sanitized for public release. Git history prior to the initial public release may contain development artifacts (e.g., temporary files, debug output, or iterative changes). If you require a completely clean history, consider using [BFG Repo Cleaner](https://rtyley.github.io/bfg-repo-cleaner/) or [git filter-repo](https://github.com/newren/git-filter-repo).

## Credits

Built with AI assistance
Version 1.1.0 - Phase 1 with One-Click Installer
