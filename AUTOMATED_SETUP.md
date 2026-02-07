# Automated Setup Scripts

AutoPackager includes automated setup scripts to quickly get you up and running.

## Quick Start

### Windows (PowerShell)

```powershell
# Run setup script (will prompt for admin if needed)
.\setup.ps1

# For testing with SQLite (easier):
.\setup.ps1 -UseSQLite
```

### Linux / WSL / Mac

```bash
# Make script executable
chmod +x setup.sh

# Run setup
./setup.sh

# For testing with SQLite (easier):
./setup.sh --sqlite

# Skip Redis installation (if you'll install it manually):
./setup.sh --skip-redis
```

---

## What the Scripts Do

The automated setup scripts handle:

1. ✅ **Check Prerequisites** - Verify Python, Git, etc. are installed
2. ✅ **Install System Dependencies** - Redis, PostgreSQL, cabextract
3. ✅ **Create Virtual Environment** - Python venv setup
4. ✅ **Install Python Packages** - All requirements.txt dependencies
5. ✅ **Download Redis** (Windows only) - Automatic Redis download
6. ✅ **Create .env File** - From template
7. ✅ **Configure Database** - SQLite or PostgreSQL
8. ✅ **Initialize Database** - Create tables and schema
9. ✅ **Create Helper Scripts** - Quick start batch/shell files

---

## After Setup Completes

### 1. Edit Configuration

```bash
# Edit .env with your Azure credentials
notepad .env       # Windows
nano .env          # Linux/Mac
```

Fill in:
- `AZURE_TENANT_ID`
- `AZURE_CLIENT_ID`
- `AZURE_CLIENT_SECRET`
- `RING0_GROUP_ID` through `RING3_GROUP_ID`

### 2. Start Services

**Windows:**
```cmd
# Terminal 1: Start Redis
.\start-redis.bat

# Terminal 2: Start Worker
.\start-worker.bat
```

**Linux/Mac:**
```bash
# Terminal 1: Start Redis
./start-redis.sh

# Terminal 2: Start Worker
./start-worker.sh
```

### 3. Create First Job

**Windows:**
```cmd
.\create-job.bat --vendor dell --model "Latitude 5420"
```

**Linux/Mac:**
```bash
./create-job.sh --vendor dell --model "Latitude 5420"
```

### 4. Monitor Progress

**Windows:**
```cmd
.\list-jobs.bat
```

**Linux/Mac:**
```bash
./list-jobs.sh
```

---

## Helper Scripts Created

After setup, you'll have these quick-start scripts:

| Script | Purpose |
|--------|---------|
| `start-redis` | Start Redis server |
| `start-worker` | Start Celery worker |
| `create-job` | Create driver job (passes args through) |
| `list-jobs` | List all jobs (passes args through) |

---

## Troubleshooting

### Python Not Found (Windows)

1. Download Python from https://www.python.org/downloads/
2. **Important**: Check "Add Python to PATH" during installation
3. Restart PowerShell and try again

### Permission Denied (Linux/Mac)

```bash
# Make script executable
chmod +x setup.sh

# Run with sudo for system packages
sudo ./setup.sh
```

### Redis Already Running

```bash
# Check if Redis is running
redis-cli ping

# If it returns PONG, you're good to go!
```

### Database Connection Failed

**SQLite (Easiest for Testing):**
```bash
# Re-run with SQLite option
./setup.sh --sqlite        # Linux/Mac
.\setup.ps1 -UseSQLite     # Windows
```

**PostgreSQL:**
```bash
# Check PostgreSQL is running
sudo systemctl status postgresql

# Create database manually
sudo -u postgres psql
CREATE DATABASE autopackager;
CREATE USER autopackager_user WITH PASSWORD 'your_password';
GRANT ALL PRIVILEGES ON DATABASE autopackager TO autopackager_user;
\q
```

### Script Fails Midway

The scripts are idempotent - safe to run multiple times. If a step fails:

1. Fix the issue (install missing package, etc.)
2. Run the script again
3. It will skip completed steps

---

## Manual Installation

If automated setup doesn't work, see:
- **IMPLEMENTATION_GUIDE.md** - Detailed step-by-step manual setup
- **SETUP.md** - Reference documentation

---

## Uninstallation

To remove AutoPackager:

```bash
# Remove virtual environment
rm -rf venv

# Remove data (optional - has your packages/logs)
rm -rf data

# Remove Redis (Windows only)
rm -rf tools/redis
```

---

## Advanced Options

### Custom Python Version

```bash
# Use specific Python version
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Install in Docker

```bash
# Coming soon - Docker Compose setup
docker-compose up -d
```

### Production Deployment

For production use:
1. Use PostgreSQL (not SQLite)
2. Configure proper Redis persistence
3. Set up systemd services for worker
4. Configure log rotation
5. Set up monitoring and alerting

See **IMPLEMENTATION_GUIDE.md** for production deployment guidance.

---

## What Gets Installed

### System Packages (Linux)
- `cabextract` - Extract CAB files (Dell/HP catalogs)
- `redis-server` - Task queue backend
- `postgresql` - Database (if not using SQLite)

### Python Packages
- See `requirements.txt` for full list
- Key packages: Celery, Redis, SQLAlchemy, MSAL, requests

### Downloaded Tools
- Redis for Windows (Windows only)
- IntuneWinAppUtil.exe (manual download required)

---

## Script Source Code

- **Windows**: `setup.ps1` - PowerShell script
- **Linux/Mac**: `setup.sh` - Bash script

Both scripts are heavily commented and can be customized for your environment.

---

## Quick Reference

### Full Installation (One Command)

**Windows (PowerShell as Admin):**
```powershell
.\setup.ps1 -UseSQLite
```

**Linux/WSL/Mac:**
```bash
./setup.sh --sqlite
```

### Estimated Time
- **Windows**: 5-10 minutes
- **Linux**: 3-5 minutes (package downloads vary)

### Disk Space Required
- ~500 MB for Python packages and dependencies
- ~50 MB for Redis
- Variable for driver packages (grows over time)

---

## Need Help?

1. Check **IMPLEMENTATION_GUIDE.md** for detailed troubleshooting
2. Review script output for specific error messages
3. Check logs in `data/logs/autopackager.log`
4. Verify Azure credentials in `.env` file

---

**Ready to start?** Run the setup script for your platform above! 🚀
