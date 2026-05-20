# AutoPackager Implementation Guide

## Getting Started with Your Test Environment

This guide covers two paths to get AutoPackager running:

- **[Fast Path (Recommended)](#fast-path-automated-setup)** — One script does everything. ~10 minutes.
- **[Manual Path](#manual-setup-reference)** — Step-by-step instructions for advanced users or non-Windows environments.

---

## Fast Path: Automated Setup

### What you need

| Requirement | Notes |
|---|---|
| Windows workstation | Local administrator rights required |
| Azure account | Global Admin **or** Application Administrator + Group Administrator roles |
| LLM API key | [OpenAI](https://platform.openai.com/api-keys) or [Anthropic](https://console.anthropic.com/settings/keys) |

That's it. The installer handles Python, Redis, IntuneWinAppUtil, the virtual environment, the database, and all Azure configuration.

---

### Step 1: Run the installer

**Easiest:** double-click `Install-AutoPackager.bat` in File Explorer — it requests Administrator rights automatically via a UAC prompt.

**Or from an existing PowerShell (Run as Administrator):**

```powershell
.\Install-AutoPackager.ps1
```

The script will work through these stages automatically:

1. **Python 3.12** — installs via `winget` (or direct download fallback)
2. **Git** — installs via `winget`
3. **Python venv + dependencies** — creates `.\venv` and runs `pip install`
4. **Redis for Windows** — downloads to `.\tools\redis\`
5. **IntuneWinAppUtil.exe** — downloads from Microsoft GitHub to `.\tools\`
6. **Data directories** — creates `data/downloads`, `data/packages`, `data/logs`, `data/catalogs`
7. **Database** — configures SQLite and initialises the schema
8. **LLM API key** — prompts you to paste your key
9. **Azure** — launches `azure-setup.ps1` (browser login, then fully automated)
10. **`.env` file** — written with all credentials
11. **Helper scripts** — creates `launch-all.bat`, `create-job.bat`, `list-jobs.bat`

---

### Step 2: Log in to Azure

When prompted, the script opens a browser window. Sign in with your Azure admin account.

The `azure-setup.ps1` script then automatically:

- Validates or creates your App Registration (`AutoPackager-ServicePrincipal`)
- Looks up the correct Microsoft Graph permission IDs dynamically
- Adds all required API permissions (Application type):
  - `DeviceManagementApps.ReadWrite.All`
  - `DeviceManagementConfiguration.ReadWrite.All`
  - `Group.Read.All`
  - `GroupMember.Read.All`
- Grants tenant-wide admin consent
- Creates 4 Entra ID security groups:
  - `AutoPackager-Ring0-ITPilot`
  - `AutoPackager-Ring1-EarlyAdopters`
  - `AutoPackager-Ring2-BroadDeployment`
  - `AutoPackager-Ring3-CriticalSystems`

---

### Step 3: Paste your LLM API key

When prompted, enter your OpenAI (`sk-...`) or Anthropic (`sk-ant-...`) key. It is written directly into `.env`.

---

### Step 4: Start AutoPackager

```cmd
.\launch-all.bat
```

This opens two windows: one for Redis, one for the Celery worker. Both must stay open while AutoPackager is running.

Alternatively, start them separately:

```cmd
# Window 1
.\start-redis.bat

# Window 2
.\start-worker.bat
```

---

### Step 5: Create your first driver job

Find your Dell laptop's exact model name:

```powershell
Get-WmiObject -Class Win32_ComputerSystem | Select-Object Model
# Example output: Latitude 5420
```

Then create the job:

```cmd
.\create-job.bat --vendor dell --model "Latitude 5420" --driver-type chipset
```

---

### Step 6: Monitor progress

```cmd
.\list-jobs.bat
```

Jobs progress through these states:
`pending` → `discovering` → `packaging` → `testing` → `deploying` → `completed`

---

### Step 7: Verify in Intune

1. Go to [https://intune.microsoft.com](https://intune.microsoft.com)
2. Navigate to **Apps → Windows**
3. Your driver package should appear
4. Check **Assignments** — it should be assigned to `AutoPackager-Ring0-ITPilot`

---

### Already have an App Registration?

If you've already created an App Registration in the Azure Portal, the installer will prompt for your Client ID and Client Secret. Alternatively, run `azure-setup.ps1` standalone:

```powershell
.\azure-setup.ps1 -TenantId "your-tenant-id" `
                  -ClientId "your-client-id" `
                  -ClientSecret "your-secret" `
                  -OutputEnvFile
```

To skip App Registration creation entirely and let the script create everything from your Tenant ID alone:

```powershell
.\Install-AutoPackager.ps1 -TenantId "your-tenant-id" -CreateAppRegistration
```

---

### Timeline

| Stage | Time |
|---|---|
| Script runs (local install) | ~5–10 min |
| Azure login + automated config | ~2–3 min |
| First driver job created | ~1 min |
| **Total** | **~10–15 min** |

---

---

## Manual Setup Reference

<details>
<summary>Click to expand — for advanced users, Linux/WSL environments, or if the automated installer cannot be used</summary>

### Part 1: Azure Configuration (15 minutes)

#### Step 1: Create Azure App Registration

1. Go to [https://portal.azure.com](https://portal.azure.com) and sign in with your M365 admin account.

2. Navigate to: **Microsoft Entra ID → App registrations → New registration**
   - **Name**: `AutoPackager-ServicePrincipal`
   - **Supported account types**: Accounts in this organizational directory only
   - **Redirect URI**: Leave blank
   - Click **Register**

3. Save your credentials:
   - Copy **Application (client) ID** → `AZURE_CLIENT_ID`
   - Copy **Directory (tenant) ID** → `AZURE_TENANT_ID`

4. Create a client secret: **Certificates & secrets → New client secret**
   - **Description**: `AutoPackager Secret`
   - **Expires**: 24 months
   - **⚠️ Copy the Value immediately** → `AZURE_CLIENT_SECRET` (shown only once)

5. Add API permissions: **API permissions → Add a permission → Microsoft Graph → Application permissions**

   Add all four:
   - `DeviceManagementApps.ReadWrite.All`
   - `DeviceManagementConfiguration.ReadWrite.All`
   - `Group.Read.All`
   - `GroupMember.Read.All`

6. Click **Grant admin consent for [Your Tenant]** → **Yes**

---

### Part 2: Create Deployment Ring Groups (5 minutes)

Navigate to **Microsoft Entra ID → Groups → New Group** and create four Security groups:

| Group name | Description | Members |
|---|---|---|
| `AutoPackager-Ring0-ITPilot` | IT staff for initial driver testing | Add your test account |
| `AutoPackager-Ring1-EarlyAdopters` | Volunteer users | (can leave empty) |
| `AutoPackager-Ring2-BroadDeployment` | General user population | (can leave empty) |
| `AutoPackager-Ring3-CriticalSystems` | High-stability devices | (can leave empty) |

For each group, copy the **Object Id** — you'll need these for `.env`.

---

### Part 3: Install Prerequisites (20 minutes)

#### Windows

```powershell
# Install Python 3.9+ from https://www.python.org/downloads/
# Check "Add Python to PATH" during installation

# Install Redis — download from:
# https://github.com/microsoftarchive/redis/releases

# Install Git from: https://git-scm.com/download/win
```

#### Linux / WSL

```bash
sudo apt-get update
sudo apt-get install python3 python3-pip python3-venv redis-server cabextract git
```

#### Download IntuneWinAppUtil.exe

1. Go to: [https://github.com/microsoft/Microsoft-Win32-Content-Prep-Tool](https://github.com/microsoft/Microsoft-Win32-Content-Prep-Tool)
2. Download `IntuneWinAppUtil.exe`
3. Place it in `tools/IntuneWinAppUtil.exe`

---

### Part 4: Install AutoPackager (10 minutes)

```bash
# Create virtual environment
python3 -m venv venv

# Activate
source venv/bin/activate        # Linux/Mac
# venv\Scripts\activate         # Windows

# Install dependencies
pip install -r requirements.txt
```

---

### Part 5: Configuration (10 minutes)

```bash
cp .env.template .env
```

Edit `.env`:

```bash
# Azure/Intune (from Part 1)
AZURE_TENANT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
AZURE_CLIENT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
AZURE_CLIENT_SECRET=your~secret~value

# Deployment Ring Group IDs (from Part 2)
RING0_GROUP_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
RING1_GROUP_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
RING2_GROUP_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
RING3_GROUP_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx

# LLM API Key
LLM_API_KEY=sk-your-api-key-here
```

Configure SQLite in `autopackager/config/config.yaml` (for testing):

```yaml
database:
  type: "sqlite"
  path: "data/autopackager.db"
```

**For detailed configuration documentation**, see [CONFIG_REFERENCE.md](CONFIG_REFERENCE.md) for comprehensive reference covering all configuration sections, valid values, and environment variable mapping.

---

### Part 6: Initialise and Test (15 minutes)

```bash
# Create data directories
mkdir -p data/downloads data/packages data/logs data/catalogs/dell data/catalogs/hp data/catalogs/lenovo

# Initialise database
python cli.py init

# Terminal 1: Start Redis
redis-server                           # Linux/Mac
# tools\redis\redis-server.exe         # Windows

# Terminal 2: Start worker
python cli.py worker start --concurrency 2
```

---

### Part 7: Run Your First Job

```bash
# Get Dell model name (PowerShell)
Get-WmiObject -Class Win32_ComputerSystem | Select-Object Model

# Create a driver job
python cli.py create-driver-job \
  --vendor dell \
  --model "Latitude 5420" \
  --driver-type "chipset" \
  --current-version "1.0.0"

# Or package an MSI application (metadata read from the MSI itself)
python cli.py inspect-msi "C:\Downloads\7z2408-x64.msi" \
  --install-command "msiexec /i 7z2408-x64.msi /qn /norestart"   # preview only
python cli.py create-software-job \
  --install-command "msiexec /i 7z2408-x64.msi /qn /norestart" \
  --installer-path "C:\Downloads\7z2408-x64.msi"

# Monitor
python cli.py jobs list
python cli.py jobs status 1
```

See [Packaging MSI Software](README.md#packaging-msi-software) for the full MSI workflow.

</details>

---

## Troubleshooting

| Issue | Solution |
|---|---|
| Authentication failed | Check `.env` credentials; verify API permissions have admin consent; check secret expiry |
| Database connection failed | Run with SQLite: `.\Install-AutoPackager.ps1` uses SQLite by default |
| Redis connection refused | Run `.\start-redis.bat` or `redis-cli ping` to verify |
| No driver pack found | Verify exact model name; try without `--driver-type` |
| IntuneWinAppUtil.exe not found | Re-run `.\Install-AutoPackager.ps1` — it downloads it automatically |
| Admin consent failed | Requires Global Admin role; grant manually in Azure Portal → API Permissions |
| Worker not processing | Check Redis is running; restart worker with `.\start-worker.bat` |

---

## Production Checklist

Before moving to production:

- [ ] Switch to PostgreSQL (not SQLite): update `config.yaml` and re-run `python cli.py init` (see [CONFIG_REFERENCE.md](CONFIG_REFERENCE.md#2-database-configuration))
- [ ] Configure log rotation for `data/logs/`
- [ ] Set up monitoring (health checks, error alerts)
- [ ] Create a database backup strategy
- [ ] Review deployment ring deferral periods in `config.yaml` (see [CONFIG_REFERENCE.md](CONFIG_REFERENCE.md#7-deployment-rings-configuration))
- [ ] Test rollback procedures
- [ ] Document your hardware inventory
- [ ] Set up automated catalog refresh (Task Scheduler / cron)
- [ ] Configure LLM API for Phase 2 (software updates)
- [ ] Create operational runbooks

---

## Quick Reference Commands

```bash
# Initialize database
python cli.py init

# Start worker
python cli.py worker start

# Create driver job
python cli.py create-driver-job --vendor dell --model "Latitude 5420"

# List jobs
python cli.py jobs list

# Job status
python cli.py jobs status <id>

# Filter by state
python cli.py jobs list --state completed
python cli.py jobs list --state failed

# Cancel a job (or every non-terminal job with --all-stuck)
python cli.py jobs cancel <id>

# Delete job rows from the database (optional --state filter)
python cli.py jobs purge --yes

# Drain queued Celery tasks
python cli.py worker purge --yes

# Show version
python cli.py version

# Start the FastAPI web dashboard
python -m uvicorn autopackager.web.api:app --host 0.0.0.0 --port 8000
```

Windows helper scripts (created by installer):

```cmd
.\launch-all.bat          Launch Redis + worker in separate windows
.\start-redis.bat         Start Redis only
.\start-worker.bat        Start Celery worker only
.\create-job.bat [args]   Create a driver job
.\list-jobs.bat [args]    List jobs
```

---

## Support Resources

- **Automated Setup**: `AUTOMATED_SETUP.md`
- **Manual Setup Reference**: `SETUP.md`
- **Technical Documentation**: `automated_software_packaging_whitepaper.md`
- **PR/FAQ**: `PRFAQ_ Project AutoPackager.md`
- **Example Scripts**: `scripts/example_usage.py`
