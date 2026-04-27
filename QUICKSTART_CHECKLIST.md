# AutoPackager Quick Start Checklist

Use this checklist to track your implementation progress.

---

## Automated Path (Recommended)

If you are using `Install-AutoPackager.ps1`, most items below are handled automatically.
The items marked **[AUTO]** require no manual action.

### Before You Start

- [ ] Confirmed local administrator rights on your Windows workstation
- [ ] Confirmed Azure account with Global Admin or Application/Group Administrator role
- [ ] Obtained LLM API key
  - OpenAI: https://platform.openai.com/api-keys
  - Anthropic: https://console.anthropic.com/settings/keys

### Run the Installer

- [ ] Double-clicked `Install-AutoPackager.bat` (handles elevation automatically)
  - OR opened PowerShell as Administrator and ran `.\Install-AutoPackager.ps1`
- [ ] **[AUTO]** Python 3.12 installed
- [ ] **[AUTO]** Git installed
- [ ] **[AUTO]** Python virtual environment created (`.\venv`)
- [ ] **[AUTO]** All Python dependencies installed
- [ ] **[AUTO]** Redis downloaded to `.\tools\redis\`
- [ ] **[AUTO]** IntuneWinAppUtil.exe downloaded to `.\tools\`
- [ ] **[AUTO]** SQLite database configured and initialised
- [ ] **[AUTO]** Data directories created

### Azure Login (one browser prompt)

- [ ] Logged in to Azure when browser opened
- [ ] **[AUTO]** App Registration created or validated
- [ ] **[AUTO]** Microsoft Graph API permissions added
- [ ] **[AUTO]** Admin consent granted
- [ ] **[AUTO]** `AutoPackager-Ring0-ITPilot` group created
- [ ] **[AUTO]** `AutoPackager-Ring1-EarlyAdopters` group created
- [ ] **[AUTO]** `AutoPackager-Ring2-BroadDeployment` group created
- [ ] **[AUTO]** `AutoPackager-Ring3-CriticalSystems` group created
- [ ] **[AUTO]** `.env` file written with all credentials

### Final Steps

- [ ] Entered LLM API key when prompted (or edited `.env` to set `LLM_API_KEY`)
- [ ] Verified `.env` has no placeholder values remaining

---

## Manual Path (Alternative)

Complete these if you are not using the automated installer.

### Azure: App Registration

- [ ] Created App Registration named `AutoPackager-ServicePrincipal`
- [ ] Saved **Tenant ID**: `___________________________________`
- [ ] Saved **Client ID**: `___________________________________`
- [ ] Saved **Client Secret**: `___________________________________`
- [ ] Added API Permission: `DeviceManagementApps.ReadWrite.All`
- [ ] Added API Permission: `DeviceManagementConfiguration.ReadWrite.All`
- [ ] Added API Permission: `Group.Read.All`
- [ ] Added API Permission: `GroupMember.Read.All`
- [ ] Granted admin consent for all permissions

### Azure: Deployment Ring Groups

- [ ] Created `AutoPackager-Ring0-ITPilot`
  - Object ID: `___________________________________`
- [ ] Created `AutoPackager-Ring1-EarlyAdopters`
  - Object ID: `___________________________________`
- [ ] Created `AutoPackager-Ring2-BroadDeployment`
  - Object ID: `___________________________________`
- [ ] Created `AutoPackager-Ring3-CriticalSystems`
  - Object ID: `___________________________________`

### Workstation Setup

- [ ] Python 3.9+ installed
- [ ] Redis installed and running
- [ ] Git installed
- [ ] IntuneWinAppUtil.exe placed in `tools/` directory
- [ ] Python virtual environment created (`venv/`)
- [ ] Dependencies installed: `pip install -r requirements.txt`
- [ ] `.env` created from template and filled in
- [ ] SQLite configured in `config.yaml` (or PostgreSQL set up)
- [ ] Database initialised: `python cli.py init`

---

## Testing & Validation

### Services Running

- [ ] Redis server is running
  - Test: `redis-cli ping` returns `PONG`
  - Windows: `.\start-redis.bat`
- [ ] Celery worker started
  - Windows: `.\start-worker.bat`
  - Linux: `python cli.py worker start`
  - Verified all 8 tasks registered in output (`create_packaging_job`, `process_job`, `discovery_task`, `packaging_task`, `testing_task`, `deployment_task`, `poll_deployment_status`, `continuous_catalog_discovery`)

### Test Device

- [ ] Identified Dell model name: `___________________________________`
  - Command: `Get-WmiObject -Class Win32_ComputerSystem | Select-Object Model`
- [ ] Device is Entra ID joined
- [ ] Device added to `AutoPackager-Ring0-ITPilot` group

### First Driver Job

- [ ] Created first driver job:
  - Windows: `.\create-job.bat --vendor dell --model "YOUR-MODEL"`
  - Linux: `python cli.py create-driver-job --vendor dell --model "YOUR-MODEL"`
  - Job ID: `___________________________________`
- [ ] Monitored job status until `completed`
- [ ] Package visible in Intune admin center

---

## Web Dashboard (Optional)

- [ ] Started the FastAPI dashboard:
  - Linux/Mac: `./start-dashboard.sh`
  - Windows: `.\start-dashboard.bat`
  - Manual: `python -m uvicorn autopackager.web.api:app --host 0.0.0.0 --port 8000`
- [ ] Opened http://localhost:8000 and confirmed jobs/deployments/discovery panels populate
- [ ] Reviewed API docs at http://localhost:8000/docs

---

## Verification in Intune

- [ ] Logged into https://intune.microsoft.com
- [ ] Navigated to **Apps → Windows**
- [ ] Found driver package in app list
- [ ] Checked **Assignments** → Ring 0 group assigned
- [ ] Triggered Intune sync on test device:
  - Settings → Accounts → Access work or school → [Account] → Info → Sync
- [ ] Verified installation started on device

---

## Optional Enhancements

- [ ] Configured automated catalog refresh (Task Scheduler / cron)
- [ ] Set up log monitoring for `data/logs/autopackager.log`
- [ ] Created driver jobs for additional hardware models
- [ ] Documented hardware inventory
- [ ] Customised deployment ring deferral periods in `config.yaml`
- [ ] Set up database backups (if using PostgreSQL)
- [ ] Switched to PostgreSQL for production use

---

## Common Commands

```cmd
# Windows helper scripts (created by installer)
.\launch-all.bat                                     Launch Redis + worker
.\create-job.bat --vendor dell --model "MODEL"       Create a driver job
.\list-jobs.bat                                      List all jobs
.\list-jobs.bat --state completed                    Filter by state
```

```bash
# Cross-platform CLI commands
python cli.py init                                   Initialise database
python cli.py worker start                           Start Celery worker
python cli.py create-driver-job --vendor dell --model "MODEL"
python cli.py jobs list
python cli.py jobs status <id>
python cli.py jobs list --state failed
```

---

## Troubleshooting Quick Fixes

| Problem | Quick Fix |
|---|---|
| Authentication failed | Check `.env` credentials; verify API permissions granted admin consent |
| Database connection error | Verify SQLite configured in `config.yaml`; re-run `python cli.py init` |
| Redis connection refused | Run `.\start-redis.bat` |
| No driver pack found | Verify exact Dell model name using `wmic computersystem get model` |
| Worker not processing | Check Redis is running; restart with `.\start-worker.bat` |
| Import errors | Re-run `pip install -r requirements.txt` inside the venv |
| Admin consent failed | Requires Global Admin; grant manually in Azure Portal |

---

## Success Criteria

You've successfully implemented AutoPackager when:

1. ✅ Worker is processing jobs without errors
2. ✅ Driver discovery completes successfully
3. ✅ Package appears in Intune admin center
4. ✅ Package is assigned to Ring 0 group
5. ✅ Test laptop installs the driver package

---

## Next Steps After Success

1. **Test additional Dell models** from your environment
2. **Monitor first real deployment** on test laptop
3. **Document your hardware inventory** for automation
4. **Plan Phase 2**: COTS software update automation
5. **Set up production monitoring** and alerting
6. **Switch to PostgreSQL** for production use

---

**Implementation Time (automated path)**: ~10–15 minutes
**Implementation Time (manual path)**: ~90 minutes
**Support**: See `IMPLEMENTATION_GUIDE.md` for detailed instructions
