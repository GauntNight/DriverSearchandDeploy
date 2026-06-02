# Automated Setup Scripts

AutoPackager includes two setup scripts that eliminate manual configuration steps.

---

## Scripts Overview

| Script | Purpose |
|---|---|
| `Install-AutoPackager.ps1` | **One-click Windows installer** — installs everything (local + Azure) |
| `azure-setup.ps1` | **Azure-only setup** — run standalone if local setup is already done |
| `setup.ps1` | Legacy Windows setup (local only, no Azure automation) |
| `setup.sh` | Linux/Mac setup (local only, no Azure automation) |

---

## Install-AutoPackager.ps1 (Recommended)

The primary installer. Run it once and AutoPackager is fully ready.

### What it installs

**Local (fully automatic, no prompts):**

| Component | Method |
|---|---|
| Python 3.12 | `winget` → python.org MSI fallback |
| Git | `winget` |
| Python venv + all pip packages | `pip install -r requirements.txt` |
| Redis | winget (Memurai) → Chocolatey → GitHub archive fallback |
| IntuneWinAppUtil.exe | Downloaded from Microsoft GitHub |
| SQLite database | Configured + schema initialised |
| Data directories | `data/downloads`, `data/packages`, `data/logs`, `data/catalogs/*` |
| Helper scripts | `launch-all.bat`, `start-redis.bat`, `start-worker.bat`, `create-job.bat`, `list-jobs.bat` |

**Azure (requires one browser login):**

| Task | Automated |
|---|---|
| Create App Registration | Optional (`-CreateAppRegistration` flag) |
| Configure existing App Registration | ✅ |
| Add Microsoft Graph API permissions | ✅ |
| Grant tenant-wide admin consent | ✅ |
| Create 4 deployment ring security groups | ✅ |
| Write `.env` with all credentials | ✅ |

### Basic usage

**Easiest — double-click in File Explorer:**
```
Install-AutoPackager.bat
```
The `.bat` file detects whether it has Administrator rights and re-launches itself elevated via a UAC prompt if needed. No manual PowerShell setup required.

**From an existing PowerShell (Run as Administrator):**
```powershell
.\Install-AutoPackager.ps1
```

### Parameters

| Parameter | Description |
|---|---|
| `-SkipAzure` | Skip Azure setup (configure later with `azure-setup.ps1`) |
| `-SkipPython` | Skip Python installation check |
| `-UseSQLite:$false` | Use PostgreSQL instead of SQLite |
| `-LlmProvider` | `openai` (default) or `anthropic` |
| `-LlmApiKey` | Provide API key directly (skips interactive prompt) |
| `-TenantId` | Provide Tenant ID directly (skips prompt) |
| `-CreateAppRegistration` | Create a new App Registration automatically |

### Examples

```powershell
# Full install — interactive prompts for credentials
.\Install-AutoPackager.ps1

# Full install — create App Registration automatically (no portal needed)
.\Install-AutoPackager.ps1 -TenantId "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" -CreateAppRegistration

# Local install only — configure Azure later
.\Install-AutoPackager.ps1 -SkipAzure

# Fully scripted — no interactive prompts
.\Install-AutoPackager.ps1 `
    -TenantId "your-tenant-id" `
    -LlmApiKey "sk-your-key" `
    -CreateAppRegistration
```

### Estimated time

- Local install: 5–10 minutes (depends on download speed)
- Azure configuration: 2–3 minutes
- **Total: ~10–15 minutes**

---

## azure-setup.ps1

Automates all Azure configuration. Use this standalone if:
- You've already run `setup.ps1` or `setup.sh` for local setup
- You want to re-configure Azure without re-running the full installer
- You need to reconfigure after a credential rotation

### What it does

1. Installs Azure CLI if not present
2. Opens browser for Azure login
3. Validates or creates the App Registration
4. Dynamically looks up Microsoft Graph permission IDs (no hardcoded GUIDs)
5. Adds all required API permissions
6. Grants tenant-wide admin consent
7. Creates 4 Entra ID security groups
8. Writes a complete `.env` file

### Examples

```powershell
# Configure existing App Registration — prompts interactively
.\azure-setup.ps1 -OutputEnvFile

# Configure existing App Registration — provide values directly
.\azure-setup.ps1 `
    -TenantId "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" `
    -ClientId "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" `
    -ClientSecret "your-secret-value" `
    -OutputEnvFile

# Create App Registration automatically — only need Tenant ID
.\azure-setup.ps1 -TenantId "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" `
                  -CreateAppRegistration `
                  -OutputEnvFile

# Write .env to a specific path
.\azure-setup.ps1 -OutputEnvFile -EnvFilePath "C:\AutoPackager\.env"
```

### Required Azure role

Admin consent requires **Global Administrator** or both:
- Application Administrator
- Group Administrator

If admin consent fails, grant it manually:
**Azure Portal → App Registrations → [your app] → API permissions → Grant admin consent**

---

## After Setup: Starting AutoPackager

### Option A — Start everything at once

```cmd
.\launch-all.bat
```

Opens two windows: Redis server and Celery worker.

### Option B — Start services individually

```cmd
# Window 1: Redis
.\start-redis.bat

# Window 2: Celery worker
.\start-worker.bat
```

### Create your first driver job

```cmd
.\create-job.bat --vendor dell --model "Latitude 5420" --driver-type chipset
```

### Monitor jobs

```cmd
.\list-jobs.bat
```

---

## Helper Scripts Reference

Scripts created by `Install-AutoPackager.ps1`:

| Script | What it does |
|---|---|
| `launch-all.bat` | Starts Redis and Celery worker in separate windows |
| `start-redis.bat` | Starts Redis server (`tools\redis\redis-server.exe redis.conf`) |
| `start-worker.bat` | Activates venv and starts Celery worker |
| `create-job.bat [args]` | Passes all arguments to `python cli.py create-driver-job` |
| `list-jobs.bat [args]` | Passes all arguments to `python cli.py jobs list` |

---

## Linux/Mac Setup

Use `setup.sh` for the local setup, then run `azure-setup.ps1` on a Windows machine (or Azure Cloud Shell) for the Azure configuration.

```bash
chmod +x setup.sh
./setup.sh --sqlite
```

Available flags:

```bash
./setup.sh --sqlite       # Use SQLite (default for testing)
./setup.sh --skip-redis   # Skip Redis installation
```

---

## Troubleshooting

### PowerShell execution policy blocked

Use `Install-AutoPackager.bat` instead — it sets `-ExecutionPolicy Bypass` automatically so you never see this error.

If running the `.ps1` directly:
```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Install-AutoPackager.ps1
```

### Python not found after installation

Close and reopen PowerShell as Administrator (PATH refresh required).

### Azure CLI not found after installation

Close and reopen PowerShell. If still not found, add manually:
```
C:\Program Files (x86)\Microsoft SDKs\Azure\CLI2\wbin
```

### Admin consent failed

Your account may not have Global Admin role. Options:
1. Ask a Global Admin to run `.\azure-setup.ps1 -OutputEnvFile` on your behalf
2. Grant consent manually: Azure Portal → App Registrations → API permissions → Grant admin consent

### Database initialisation warnings

If `python cli.py init` shows warnings during install, this is normal if Azure credentials aren't yet written. Re-run after `.env` is complete:

```powershell
.\venv\Scripts\activate.ps1
python cli.py init
```

### Redis download failed

Download manually from [https://github.com/microsoftarchive/redis/releases](https://github.com/microsoftarchive/redis/releases) and extract to `tools\redis\`.

---

## Disk Space Required

| Component | Space |
|---|---|
| Python packages (venv) | ~500 MB |
| Redis | ~50 MB |
| Application data (grows over time) | Variable |

---

## Uninstalling

```powershell
# Remove virtual environment
Remove-Item -Recurse -Force .\venv

# Remove downloaded tools
Remove-Item -Recurse -Force .\tools\redis

# Remove data (optional — contains your packages and logs)
Remove-Item -Recurse -Force .\data
```

The Azure App Registration and Entra ID groups are not removed by uninstalling locally. Delete them from Azure Portal if no longer needed.

---

## Need Help?

1. Check `QUICKSTART_CHECKLIST.md` (Troubleshooting Quick Fixes) or `SETUP.md`
2. Review script output for specific error messages
3. Check logs in `data/logs/autopackager.log`
4. Verify Azure credentials in `.env`
