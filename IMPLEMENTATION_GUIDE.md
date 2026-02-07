# AutoPackager Implementation Guide
## Getting Started with Your Test Environment

This guide will walk you through implementing AutoPackager in your new M365/Intune environment with your Dell test laptop.

---

## Part 1: Azure Configuration (15 minutes)

### Step 1: Create Azure App Registration

1. **Navigate to Azure Portal**
   - Go to https://portal.azure.com
   - Sign in with your M365 admin account

2. **Create App Registration**
   ```
   Azure Active Directory → App registrations → New registration
   ```

   - **Name**: `AutoPackager-ServicePrincipal`
   - **Supported account types**: Accounts in this organizational directory only
   - **Redirect URI**: Leave blank
   - Click **Register**

3. **Save Your Credentials** (you'll need these later)
   - Copy **Application (client) ID** → This is your `AZURE_CLIENT_ID`
   - Copy **Directory (tenant) ID** → This is your `AZURE_TENANT_ID`

4. **Create Client Secret**
   ```
   Certificates & secrets → New client secret
   ```

   - **Description**: `AutoPackager Secret`
   - **Expires**: 24 months (or per your policy)
   - Click **Add**
   - **⚠️ CRITICAL**: Copy the **Value** immediately → This is your `AZURE_CLIENT_SECRET`
   - You won't be able to see this again!

5. **Configure API Permissions**
   ```
   API permissions → Add a permission → Microsoft Graph → Application permissions
   ```

   Add these permissions:
   - `DeviceManagementApps.ReadWrite.All`
   - `DeviceManagementConfiguration.ReadWrite.All`
   - `Group.Read.All`
   - `GroupMember.Read.All`

6. **Grant Admin Consent**
   - Click **Grant admin consent for [Your Tenant]**
   - Click **Yes**
   - All permissions should now show ✓ "Granted for [Your Tenant]"

---

## Part 2: Create Deployment Ring Groups (5 minutes)

### Step 2: Create Entra ID Groups

Navigate to **Azure Active Directory → Groups → New Group**

Create these 4 groups:

#### Ring 0 - IT Pilot
```
Group type: Security
Group name: AutoPackager-Ring0-ITPilot
Description: IT staff for initial driver testing
Members: Add your test account
```

#### Ring 1 - Early Adopters
```
Group type: Security
Group name: AutoPackager-Ring1-EarlyAdopters
Description: Volunteer users for early driver deployment
Members: (Can leave empty for now)
```

#### Ring 2 - Broad Deployment
```
Group type: Security
Group name: AutoPackager-Ring2-BroadDeployment
Description: General user population
Members: (Can leave empty for now)
```

#### Ring 3 - Critical Systems
```
Group type: Security
Group name: AutoPackager-Ring3-CriticalSystems
Description: High-stability devices (servers, executives, etc.)
Members: (Can leave empty for now)
```

**Save the Object IDs for each group** - you'll need these for configuration.

To get Object IDs:
1. Click on each group
2. Copy the **Object Id** field
3. Keep these handy!

---

## Part 3: Workstation Setup (20 minutes)

### Step 3: Install Prerequisites

#### Option A: Windows Workstation

```powershell
# Install Python 3.9+ from python.org
# Download and install from: https://www.python.org/downloads/

# Install PostgreSQL (or use SQLite for testing)
# Download from: https://www.postgresql.org/download/windows/

# Install Redis (Windows version)
# Download from: https://github.com/microsoftarchive/redis/releases
# Extract and run redis-server.exe

# Install Git
# Download from: https://git-scm.com/download/win

# Install cabextract alternative (7-Zip works)
# Download from: https://www.7-zip.org/
```

#### Option B: Linux/WSL (Recommended for Development)

```bash
# Update system
sudo apt-get update

# Install Python 3.9+
sudo apt-get install python3 python3-pip python3-venv

# Install PostgreSQL
sudo apt-get install postgresql postgresql-contrib

# Install Redis
sudo apt-get install redis-server

# Install cabextract (for Dell/HP catalogs)
sudo apt-get install cabextract

# Install Git
sudo apt-get install git
```

### Step 4: Download IntuneWinAppUtil.exe

1. Go to: https://github.com/microsoft/Microsoft-Win32-Content-Prep-Tool
2. Download the latest release
3. Save `IntuneWinAppUtil.exe` (you'll place this in the project later)

---

## Part 4: Install AutoPackager (10 minutes)

### Step 5: Clone and Setup

```bash
# Clone the repository
cd /path/to/your/workspace
git clone <your-repository-url>
cd DriverSearchandDeploy

# Create virtual environment
python3 -m venv venv

# Activate virtual environment
# On Linux/Mac:
source venv/bin/activate
# On Windows:
venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### Step 6: Place IntuneWinAppUtil.exe

```bash
# Create tools directory
mkdir -p tools

# Place IntuneWinAppUtil.exe in tools/
# Copy the file you downloaded earlier to: tools/IntuneWinAppUtil.exe
```

---

## Part 5: Configuration (10 minutes)

### Step 7: Configure Environment Variables

```bash
# Copy environment template
cp .env.template .env

# Edit .env file
nano .env  # or use your favorite editor
```

Fill in your values from earlier:

```bash
# Database (for testing, can use SQLite - see next step)
DB_PASSWORD=YourSecurePasswordHere

# Azure/Intune (from Step 1)
AZURE_TENANT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
AZURE_CLIENT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
AZURE_CLIENT_SECRET=your~secret~value~here

# Deployment Ring Group IDs (from Step 2)
RING0_GROUP_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx  # ITPilot
RING1_GROUP_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx  # EarlyAdopters
RING2_GROUP_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx  # BroadDeployment
RING3_GROUP_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx  # CriticalSystems

# LLM API Key (optional for Phase 1 - driver discovery)
# Get from: https://platform.openai.com/api-keys or https://console.anthropic.com/
LLM_API_KEY=sk-your-api-key-here
```

### Step 8: Configure Database

#### Option A: SQLite (Easiest for Testing)

Edit `autopackager/config/config.yaml`:

```yaml
database:
  type: "sqlite"
  path: "data/autopackager.db"
```

#### Option B: PostgreSQL (Production-Ready)

```bash
# Create database (Linux)
sudo -u postgres psql
CREATE DATABASE autopackager;
CREATE USER autopackager_user WITH PASSWORD 'YourSecurePasswordHere';
GRANT ALL PRIVILEGES ON DATABASE autopackager TO autopackager_user;
\q
```

Keep the default in `config.yaml`:
```yaml
database:
  type: "postgresql"
  host: "localhost"
  port: 5432
  name: "autopackager"
  user: "autopackager_user"
  password: "${DB_PASSWORD}"
```

---

## Part 6: Initialize and Test (15 minutes)

### Step 9: Initialize Database

```bash
# Make sure virtual environment is activated
source venv/bin/activate  # or venv\Scripts\activate on Windows

# Initialize database
python cli.py init
```

You should see:
```
✓ Database initialized successfully
```

### Step 10: Start Redis

```bash
# Linux/Mac
redis-server

# Windows
# Run redis-server.exe from the Redis folder
```

### Step 11: Start Celery Worker

Open a **new terminal** (keep Redis running):

```bash
# Navigate to project
cd /path/to/DriverSearchandDeploy

# Activate virtual environment
source venv/bin/activate

# Start worker
python cli.py worker start --concurrency 2
```

You should see Celery starting up with:
```
[tasks]
  . autopackager.create_packaging_job
  . autopackager.process_job
  . autopackager.discovery_task
  . autopackager.packaging_task
  . autopackager.testing_task
  . autopackager.deployment_task
```

---

## Part 7: Run Your First Job! (10 minutes)

### Step 12: Get Your Dell Model Information

On your Dell test laptop, find the exact model:

```powershell
# PowerShell
Get-WmiObject -Class Win32_ComputerSystem | Select-Object Model

# Or Command Prompt
wmic computersystem get model
```

Example output: `Latitude 5420`, `Precision 5560`, `OptiPlex 7090`, etc.

### Step 13: Create Driver Discovery Job

Open **another terminal** (keep worker running):

```bash
# Navigate to project
cd /path/to/DriverSearchandDeploy

# Activate virtual environment
source venv/bin/activate

# Create driver job (replace with YOUR Dell model)
python cli.py create-driver-job \
  --vendor dell \
  --model "Latitude 5420" \
  --driver-type "chipset" \
  --current-version "1.0.0"
```

### Step 14: Monitor Job Progress

```bash
# List all jobs
python cli.py jobs list

# Get detailed status (replace <job-id> with the actual ID)
python cli.py jobs status 1
```

You should see the job progress through states:
- `pending` → `discovering` → `packaging` → `testing` → `deploying` → `completed`

### Step 15: Verify in Intune

1. Go to **Microsoft Intune admin center**: https://intune.microsoft.com
2. Navigate to **Apps → Windows**
3. You should see your driver package appear!
4. Check **Assignments** - it should be assigned to your `AutoPackager-Ring0-ITPilot` group

---

## Troubleshooting

### Issue: "Authentication failed"
**Solution**:
- Verify your Azure credentials in `.env`
- Check that API permissions are granted admin consent
- Ensure client secret hasn't expired

### Issue: "Database connection failed"
**Solution**:
- If using PostgreSQL, verify it's running: `sudo systemctl status postgresql`
- Try SQLite for testing (see Step 8)
- Check password in `.env` matches database

### Issue: "No driver pack found"
**Solution**:
- Verify exact Dell model name matches catalog
- Try without `--driver-type` to get full driver pack
- Check Dell catalog downloaded to `data/catalogs/dell/`

### Issue: "Redis connection refused"
**Solution**:
- Ensure Redis is running: `redis-cli ping` (should return `PONG`)
- Start Redis: `redis-server` or Windows equivalent

### Issue: "IntuneWinAppUtil.exe not found"
**Solution**:
- Verify file exists at `tools/IntuneWinAppUtil.exe`
- Check file permissions (needs execute)
- For testing, the system will create placeholder .intunewin files

---

## Next Steps

### 1. Monitor Your First Deployment
```bash
# Watch job progress
watch -n 5 'python cli.py jobs list'

# Check logs
tail -f data/logs/autopackager.log
```

### 2. Test Deployment on Dell Laptop

1. Ensure Dell laptop is Entra ID joined
2. Add device to `AutoPackager-Ring0-ITPilot` group
3. On the laptop, sync Intune:
   ```
   Settings → Accounts → Access work or school → [Your Account] → Info → Sync
   ```
4. Check Company Portal for the driver package

### 3. Add More Hardware Models

```bash
# Create jobs for different models
python cli.py create-driver-job \
  --vendor dell \
  --model "Precision 5560"

python cli.py create-driver-job \
  --vendor dell \
  --model "OptiPlex 7090"
```

### 4. Explore Advanced Features

- Review `autopackager/config/config.yaml` for customization
- Adjust deployment ring deferral periods
- Configure automated catalog refresh (cron job)
- Set up monitoring and alerting

---

## Production Checklist

Before moving to production:

- [ ] Use PostgreSQL (not SQLite)
- [ ] Configure proper logging rotation
- [ ] Set up monitoring (health checks, error alerts)
- [ ] Create proper backup strategy for database
- [ ] Review and adjust deployment ring deferral periods
- [ ] Test rollback procedures
- [ ] Document your hardware inventory
- [ ] Set up automated catalog refresh
- [ ] Configure LLM API for Phase 2 (software updates)
- [ ] Create operational runbooks

---

## Support Resources

- **Setup Guide**: `SETUP.md`
- **Technical Documentation**: `automated_software_packaging_whitepaper.md`
- **PR/FAQ**: `PRFAQ_ Project AutoPackager.md`
- **Example Scripts**: `scripts/example_usage.py`

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

# Show version
python cli.py version
```

---

## Timeline Summary

- **Part 1-2 (Azure Setup)**: 20 minutes
- **Part 3-4 (Install)**: 30 minutes
- **Part 5-6 (Configure & Init)**: 25 minutes
- **Part 7 (First Job)**: 10 minutes

**Total Time**: ~90 minutes for complete setup and first driver deployment!

---

Good luck! 🚀
