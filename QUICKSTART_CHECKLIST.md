# AutoPackager Quick Start Checklist

Use this checklist to track your implementation progress.

## ☐ Azure Configuration

### App Registration
- [ ] Created App Registration named `AutoPackager-ServicePrincipal`
- [ ] Saved **Tenant ID**: `___________________________________`
- [ ] Saved **Client ID**: `___________________________________`
- [ ] Saved **Client Secret**: `___________________________________`
- [ ] Added API Permission: `DeviceManagementApps.ReadWrite.All`
- [ ] Added API Permission: `DeviceManagementConfiguration.ReadWrite.All`
- [ ] Added API Permission: `Group.Read.All`
- [ ] Added API Permission: `GroupMember.Read.All`
- [ ] Granted admin consent for all permissions

### Deployment Ring Groups
- [ ] Created `AutoPackager-Ring0-ITPilot`
  - Object ID: `___________________________________`
- [ ] Created `AutoPackager-Ring1-EarlyAdopters`
  - Object ID: `___________________________________`
- [ ] Created `AutoPackager-Ring2-BroadDeployment`
  - Object ID: `___________________________________`
- [ ] Created `AutoPackager-Ring3-CriticalSystems`
  - Object ID: `___________________________________`
- [ ] Added test account to Ring 0 group

---

## ☐ Workstation Setup

### Prerequisites
- [ ] Python 3.9+ installed
- [ ] PostgreSQL installed (or using SQLite)
- [ ] Redis installed and running
- [ ] Git installed
- [ ] cabextract installed (Linux) or 7-Zip (Windows)
- [ ] Downloaded `IntuneWinAppUtil.exe`

### Project Setup
- [ ] Cloned repository
- [ ] Created Python virtual environment
- [ ] Activated virtual environment
- [ ] Installed requirements: `pip install -r requirements.txt`
- [ ] Placed `IntuneWinAppUtil.exe` in `tools/` directory

---

## ☐ Configuration

### Environment Variables
- [ ] Copied `.env.template` to `.env`
- [ ] Set `AZURE_TENANT_ID`
- [ ] Set `AZURE_CLIENT_ID`
- [ ] Set `AZURE_CLIENT_SECRET`
- [ ] Set `RING0_GROUP_ID`
- [ ] Set `RING1_GROUP_ID`
- [ ] Set `RING2_GROUP_ID`
- [ ] Set `RING3_GROUP_ID`
- [ ] Set `DB_PASSWORD` (if using PostgreSQL)
- [ ] Set `LLM_API_KEY` (optional for Phase 1)

### Database Configuration
- [ ] Chose database: ☐ SQLite  ☐ PostgreSQL
- [ ] If PostgreSQL: Created database `autopackager`
- [ ] If PostgreSQL: Created user `autopackager_user`
- [ ] If SQLite: Updated `config.yaml` to use SQLite
- [ ] Initialized database: `python cli.py init`

---

## ☐ Testing & Validation

### Services Running
- [ ] Redis server is running
  - Test: `redis-cli ping` returns `PONG`
- [ ] Celery worker started
  - Command: `python cli.py worker start`
  - Verified 6 tasks registered

### Dell Laptop Information
- [ ] Determined exact Dell model: `___________________________________`
- [ ] Laptop is Entra ID joined
- [ ] Laptop added to Ring 0 group

### First Job
- [ ] Created driver discovery job
  - Command used: `___________________________________`
  - Job ID: `___________________________________`
- [ ] Monitored job status: `python cli.py jobs status <id>`
- [ ] Job completed successfully
- [ ] Verified package in Intune admin center

---

## ☐ Verification in Intune

- [ ] Logged into https://intune.microsoft.com
- [ ] Navigated to **Apps → Windows**
- [ ] Found driver package in app list
- [ ] Checked **Assignments** → Ring 0 group assigned
- [ ] Triggered sync on Dell test laptop
- [ ] Verified installation started on device

---

## ☐ Optional Enhancements

- [ ] Configured automated catalog refresh (cron/Task Scheduler)
- [ ] Set up log monitoring
- [ ] Created additional test jobs for other models
- [ ] Documented hardware inventory
- [ ] Customized deployment ring deferral periods
- [ ] Set up backup for database

---

## Common Commands Reference

```bash
# Virtual environment
source venv/bin/activate          # Linux/Mac
venv\Scripts\activate             # Windows

# Database
python cli.py init

# Worker
python cli.py worker start

# Jobs
python cli.py create-driver-job --vendor dell --model "YOUR-MODEL"
python cli.py jobs list
python cli.py jobs status <id>
python cli.py jobs list --state completed

# Services
redis-server                      # Start Redis
redis-cli ping                    # Test Redis
sudo systemctl status postgresql  # Check PostgreSQL (Linux)
```

---

## Troubleshooting Quick Fixes

| Problem | Quick Fix |
|---------|-----------|
| Authentication failed | Check `.env` credentials, verify API permissions granted |
| Database connection error | Verify PostgreSQL running or switch to SQLite |
| Redis connection refused | Run `redis-server` |
| No driver pack found | Verify exact Dell model name |
| Worker not processing | Check Redis connection, restart worker |
| Import errors | Re-run `pip install -r requirements.txt` |

---

## Success Criteria

✅ You've successfully implemented AutoPackager when:

1. Worker is processing jobs without errors
2. Driver discovery completes successfully
3. Package appears in Intune admin center
4. Package is assigned to Ring 0 group
5. Test laptop can install the driver package

---

## Next Steps After Success

1. **Test additional Dell models** from your environment
2. **Monitor first real deployment** on test laptop
3. **Document your hardware inventory** for automation
4. **Plan Phase 2**: COTS software update automation
5. **Set up production monitoring** and alerting

---

**Implementation Time**: ~90 minutes
**Support**: See `IMPLEMENTATION_GUIDE.md` for detailed instructions
