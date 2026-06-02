# AutoPackager Setup Guide

> **Looking for the quickest way to get started?**
> Run `.\Install-AutoPackager.ps1` as Administrator (or double-click
> `Install-AutoPackager.bat` for guided UAC elevation). It installs Python,
> Redis, IntuneWinAppUtil, all Python dependencies, and configures Azure
> automatically — by default it **creates a brand new App Registration and
> client secret** in your tenant so you don't need to provision anything in
> the portal first. See [AUTOMATED_SETUP.md](AUTOMATED_SETUP.md) for full
> details.
>
> This document is the **manual setup reference** for advanced users and
> Linux/WSL environments.

---

## Windows Installer Quick Reference

The Windows installer can be launched two ways:

```cmd
:: Recommended for end users - handles UAC elevation automatically
Install-AutoPackager.bat

:: Or, from an elevated PowerShell prompt
.\Install-AutoPackager.ps1
```

Both entry points accept the same switches. The batch wrapper forwards `%*`
straight to the PowerShell script, so any of the examples below also work
when prefixed with `Install-AutoPackager.bat` instead of
`.\Install-AutoPackager.ps1`.

### Default behaviour (no arguments)

- Installs Python, Git, Redis, IntuneWinAppUtil, the venv, and dependencies.
- Opens a browser to sign in to Azure.
- **Creates a new App Registration** named `AutoPackager-ServicePrincipal`
  in your tenant and **generates a fresh client secret** (valid 2 years).
- Adds Microsoft Graph permissions and grants tenant-wide admin consent.
- Creates the four deployment ring groups in Entra ID.
- Writes a complete `.env` with all credentials.

### Available switches (Install-AutoPackager.ps1 / .bat)

| Switch | Default | Purpose |
| --- | --- | --- |
| `-SkipAzure` | off | Skip the entire Azure step; run `.\azure-setup.ps1` later. |
| `-SkipPython` | off | Skip the Python install check (assume 3.9+ is present). |
| `-UseSQLite` | `$true` | Use SQLite. Pass `-UseSQLite:$false` for PostgreSQL. |
| `-LlmProvider` | `openai` | LLM provider: `openai` or `anthropic`. |
| `-LlmApiKey` | _(prompted)_ | OpenAI/Anthropic API key. Skips the prompt. |
| `-TenantId` | _(prompted)_ | Azure Tenant ID. Skips the tenant prompt. |
| `-CreateAppRegistration` | `$true` | Create a new App Registration + client secret. Pass `-CreateAppRegistration:$false` to reuse an existing app. |
| `-UseExistingAppRegistration` | off | Opt out of the default. Equivalent to `-CreateAppRegistration:$false`. |
| `-AppName` | `AutoPackager-ServicePrincipal` | Display name for the new App Registration. |
| `-ClientId` | _(prompted when reusing)_ | Existing App Registration Client ID. Implies `-UseExistingAppRegistration`. |
| `-ClientSecret` | _(prompted when reusing)_ | Existing client secret value. Implies `-UseExistingAppRegistration`. |

### Common command lines

```powershell
# Default - zero-touch Azure: a new App Registration + secret are created for you
.\Install-AutoPackager.ps1

# Default, but skip the tenant prompt
.\Install-AutoPackager.ps1 -TenantId "00000000-0000-0000-0000-000000000000"

# Default, fully unattended (also skips the LLM key prompt)
.\Install-AutoPackager.ps1 -TenantId "<tid>" -LlmApiKey "sk-..."

# Customise the new App Registration's display name
.\Install-AutoPackager.ps1 -AppName "Contoso-AutoPackager-Prod"

# Reuse an existing App Registration you already created
.\Install-AutoPackager.ps1 -UseExistingAppRegistration `
                           -TenantId "<tid>" `
                           -ClientId "<cid>" `
                           -ClientSecret "<secret>"

# Same, using the negated-switch form
.\Install-AutoPackager.ps1 -CreateAppRegistration:$false -TenantId "<tid>"

# Local-only install; configure Azure later via .\azure-setup.ps1
.\Install-AutoPackager.ps1 -SkipAzure

# Use Anthropic Claude instead of OpenAI
.\Install-AutoPackager.ps1 -LlmProvider anthropic -LlmApiKey "sk-ant-..."

# PostgreSQL instead of SQLite
.\Install-AutoPackager.ps1 -UseSQLite:$false
```

### Running azure-setup.ps1 standalone

`azure-setup.ps1` accepts the same App Registration semantics if you ever
need to re-run just the Azure portion:

```powershell
# Create a new App Registration + secret (zero-touch)
.\azure-setup.ps1 -TenantId "<tid>" -CreateAppRegistration -OutputEnvFile

# Reuse an existing App Registration
.\azure-setup.ps1 -TenantId "<tid>" -ClientId "<cid>" -ClientSecret "<secret>" -OutputEnvFile

# Fully interactive
.\azure-setup.ps1 -OutputEnvFile
```

---

## Phase 1: Driver Management Automation

This guide will help you set up AutoPackager for automated driver management and deployment via Microsoft Intune.

## Prerequisites

### System Requirements
- Python 3.9 or higher
- PostgreSQL 12+ or SQLite (for development)
- Redis 6.0+ (for Celery task queue)
- Windows or Linux environment
- Microsoft Azure subscription with Intune licensing

### Required Tools
- IntuneWinAppUtil.exe (for creating .intunewin packages)
- Git
- cabextract (Linux) or expand.exe (Windows) for OEM catalog extraction

## Installation Steps

### 1. Clone the Repository

```bash
git clone <repository-url>
cd DriverSearchandDeploy
```

### 2. Create Python Virtual Environment

```bash
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Download IntuneWinAppUtil.exe

Download the Microsoft Win32 Content Prep Tool from:
https://github.com/microsoft/Microsoft-Win32-Content-Prep-Tool

Place `IntuneWinAppUtil.exe` in the `tools/` directory:

```bash
mkdir -p tools
# Download and place IntuneWinAppUtil.exe in tools/
```

### 5. Setup Database

#### Option A: PostgreSQL (Production)

```bash
# Install PostgreSQL (Ubuntu/Debian)
sudo apt-get install postgresql postgresql-contrib

# Create database and user
sudo -u postgres psql
CREATE DATABASE autopackager;
CREATE USER autopackager_user WITH PASSWORD 'your_secure_password';
GRANT ALL PRIVILEGES ON DATABASE autopackager TO autopackager_user;
\q
```

#### Option B: SQLite (Development)

For development/testing, you can use SQLite by updating `config.yaml`:

```yaml
database:
  type: "sqlite"
  path: "autopackager.db"
```

### 6. Setup Redis

#### Ubuntu/Debian
```bash
sudo apt-get install redis-server
sudo systemctl start redis
sudo systemctl enable redis
```

#### macOS
```bash
brew install redis
brew services start redis
```

#### Windows
Download Redis for Windows from: https://github.com/microsoftarchive/redis/releases

### 7. Configure Environment Variables

Copy the environment template and fill in your values:

```bash
cp .env.template .env
```

Edit `.env` with your credentials:

```bash
# Database
DB_PASSWORD=your_database_password

# Azure/Intune (from App Registration)
AZURE_TENANT_ID=your-tenant-id
AZURE_CLIENT_ID=your-client-id
AZURE_CLIENT_SECRET=your-client-secret

# Deployment Ring Group IDs (Entra ID Group IDs)
RING0_GROUP_ID=your-it-pilot-group-id
RING1_GROUP_ID=your-early-adopters-group-id
RING2_GROUP_ID=your-broad-deployment-group-id
RING3_GROUP_ID=your-critical-systems-group-id

# LLM API Key (OpenAI or Anthropic)
LLM_API_KEY=your-llm-api-key
```

### 8. Azure App Registration Setup

Create an Azure App Registration with the following API permissions:

1. Go to Azure Portal → App Registrations → New Registration
2. Name: "AutoPackager Service Principal"
3. Add API Permissions (Application type):
   - `DeviceManagementApps.ReadWrite.All`
   - `DeviceManagementConfiguration.ReadWrite.All`
   - `DeviceManagementManagedDevices.PrivilegedOperations.All`
   - `Group.Read.All`
   - `GroupMember.Read.All`
   - `GroupMember.ReadWrite.All`
4. Grant admin consent
5. Create a client secret
6. Copy Tenant ID, Client ID, and Client Secret to `.env`

### 9. Create Deployment Ring Groups in Entra ID

Create four Entra ID groups for deployment rings:

1. **AutoPackager-Ring0-ITPilot** - IT staff for initial testing
2. **AutoPackager-Ring1-EarlyAdopters** - Volunteer users
3. **AutoPackager-Ring2-BroadDeployment** - Majority of users
4. **AutoPackager-Ring3-CriticalSystems** - High-stability devices

Copy the Group IDs to your `.env` file.

### 10. Initialize Database

```bash
python cli.py init
```

This will create all necessary database tables.

### 11. Verify Configuration

Review and customize `autopackager/config/config.yaml`:

- Update OEM catalog URLs if needed
- Adjust deployment ring deferral days
- Configure logging levels
- Set job retry parameters

**For detailed configuration documentation**, see [CONFIG_REFERENCE.md](CONFIG_REFERENCE.md) for comprehensive field-level reference covering all 11 configuration sections, valid values, environment variable mapping, and common scenarios.

## Running AutoPackager

### Start the Celery Worker

In one terminal:

```bash
source venv/bin/activate
python cli.py worker start --concurrency 4
```

This starts the background task processor.

### Create a Driver Update Job

In another terminal:

```bash
source venv/bin/activate

# Example: Create Lenovo driver job
python cli.py create-driver-job \
  --vendor lenovo \
  --model "ThinkPad X1 Carbon Gen 9" \
  --driver-type "chipset" \
  --current-version "1.0.0"

# Example: Create HP driver job
python cli.py create-driver-job \
  --vendor hp \
  --model "EliteBook 850 G8" \
  --driver-type "network" \
  --current-version "2.1.0"
```

### Monitor Jobs

```bash
# List all jobs
python cli.py jobs list

# Get detailed status
python cli.py jobs status <job-id>

# Filter by state
python cli.py jobs list --state completed

# Cancel a stuck job (or every non-terminal job with --all-stuck)
python cli.py jobs cancel <job-id>

# Drain all queued Celery tasks
python cli.py worker purge --yes
```

### Web Dashboard

For a real-time view of jobs, deployments, deployment rings, and discovery runs, start the FastAPI dashboard:

```bash
# Linux/Mac
./start-dashboard.sh

# Windows
.\start-dashboard.bat

# Manual (any platform)
python -m uvicorn autopackager.web.api:app --host 0.0.0.0 --port 8000
```

Open <http://localhost:8000>. Swagger UI is at `/docs`, ReDoc at `/redoc`.

### Optional: Continuous Catalog Discovery

To have AutoPackager scan OEM catalogs on a schedule and auto-create packaging jobs for new driver versions, leave `discovery_schedule.enabled: true` in `config.yaml`, populate `monitored_models`, and start Celery Beat alongside the worker:

```bash
celery -A autopackager.orchestration.celery_app beat --loglevel=info
```

See [CONFIG_REFERENCE.md](CONFIG_REFERENCE.md) section 13 (Discovery Schedule Configuration) for details.

## Directory Structure

```
DriverSearchandDeploy/
├── autopackager/              # Main application code
│   ├── agents/               # Discovery, Packaging, Testing, Deployment agents
│   ├── config/               # config.yaml
│   ├── models/               # Database models (Job, Package, Deployment, DiscoveryRun)
│   ├── orchestration/        # Celery app, tasks, and orchestration engine
│   ├── services/             # Dashboard data aggregation service
│   ├── utils/                # Utilities (config, logging, database, Graph API)
│   └── web/                  # FastAPI dashboard (api.py + static/)
├── data/                     # Runtime data
│   ├── downloads/            # Downloaded installers
│   ├── packages/             # Created packages
│   ├── logs/                 # Application logs
│   └── catalogs/             # OEM driver catalogs
├── docs/                     # Architecture documentation (PIPELINE_LIFECYCLE.md)
├── scripts/                  # Example helper scripts
├── tests/                    # pytest suite (unit, integration, cli, api, fixtures)
├── tools/                    # External tools (IntuneWinAppUtil.exe, Redis)
├── cli.py                    # Command-line interface
├── start-dashboard.sh        # Launch FastAPI dashboard (Linux/Mac)
├── start-dashboard.bat       # Launch FastAPI dashboard (Windows)
├── requirements.txt          # Python dependencies
└── .env                      # Environment variables (not in git)
```

## Testing

### Run Unit Tests

```bash
# Full suite with coverage report (uses pytest.ini + .coveragerc defaults)
pytest

# Or by category
pytest tests/unit/
pytest tests/integration/
pytest tests/cli/
pytest tests/api/
```

See [tests/README.md](tests/README.md) for the full test guide (markers, fixtures, mocking strategy).

### Test OEM Catalog Discovery

```bash
# Test Lenovo discovery
python cli.py create-driver-job \
  --vendor lenovo \
  --model "ThinkPad T14 Gen 2" \
  --current-version "1.0"

# Test HP discovery
python cli.py create-driver-job \
  --vendor hp \
  --model "ProBook 450 G8" \
  --current-version "1.0"
```

## Troubleshooting

### Database Connection Issues

Check PostgreSQL is running:
```bash
sudo systemctl status postgresql
```

Test connection:
```bash
psql -h localhost -U autopackager_user -d autopackager
```

### Redis Connection Issues

Check Redis is running:
```bash
redis-cli ping
# Should return: PONG
```

### Celery Worker Not Processing Jobs

1. Check worker logs for errors
2. Verify Redis connection
3. Ensure database is initialized
4. Check `.env` file has correct credentials

### Graph API Authentication Errors

1. Verify App Registration permissions are granted
2. Check tenant ID, client ID, and client secret
3. Ensure client secret hasn't expired
4. Verify API permissions have admin consent

## Next Steps

- Configure hardware inventory source (CMDB/ServiceNow)
- Set up automated catalog refresh (cron job)
- Configure monitoring and alerting
- Implement automated deployment ring progression
- Move to Phase 2: COTS software updates

## Support

For issues and questions, please refer to:
- Quick Start + troubleshooting: `QUICKSTART_CHECKLIST.md`
- Configuration reference: `CONFIG_REFERENCE.md`
- Design history (whitepaper, PR/FAQ): `docs/design-history/`
- GitHub Issues: (if applicable)
