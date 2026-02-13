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
- ✅ Database tracking with PostgreSQL
- ✅ CLI interface for job management

## Quick Start

### 🚀 Automated Setup (Recommended)

**Windows (PowerShell):**
```powershell
.\setup.ps1 -UseSQLite
```

**Linux/WSL/Mac:**
```bash
chmod +x setup.sh
./setup.sh --sqlite
```

The automated setup will:
- ✅ Check prerequisites and install dependencies
- ✅ Create Python virtual environment
- ✅ Install Redis (Windows: automatic download)
- ✅ Configure database (SQLite for testing)
- ✅ Initialize database and create directories
- ✅ Create helper scripts for easy operation

**See [AUTOMATED_SETUP.md](AUTOMATED_SETUP.md) for details**

### Manual Installation (Alternative)

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
source venv/bin/activate

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
├── tools/                 # IntuneWinAppUtil.exe
├── cli.py                 # Command-line interface
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
- **🚀 Implementation Guide**: [IMPLEMENTATION_GUIDE.md](IMPLEMENTATION_GUIDE.md) - **START HERE for deployment**
- **✅ Quick Start Checklist**: [QUICKSTART_CHECKLIST.md](QUICKSTART_CHECKLIST.md) - Track your progress

### Reference Documentation
- **Setup Guide**: [SETUP.md](SETUP.md) - Detailed installation instructions
- **Technical Whitepaper**: [automated_software_packaging_whitepaper.md](automated_software_packaging_whitepaper.md)
- **PR/FAQ**: [PRFAQ_ Project AutoPackager.md](PRFAQ_%20Project%20AutoPackager.md)

## License

Internal use only - Enterprise project

## Credits

Built with AI assistance
Version 0.1.0 - Phase 1 Implementation
