# AutoPackager Quick Setup Script for Windows
# This script automates the installation and configuration of AutoPackager
# Run as: .\setup.ps1

param(
    [switch]$SkipPythonCheck,
    [switch]$UseSQLite
)

# ------------------------------------------------------------------------------
# DEPRECATION WARNING
# ------------------------------------------------------------------------------
Write-Host ""
Write-Host "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!" -ForegroundColor Red
Write-Host "!!                                                          !!" -ForegroundColor Red
Write-Host "!!   DEPRECATED - This script is no longer maintained       !!" -ForegroundColor Red
Write-Host "!!                                                          !!" -ForegroundColor Red
Write-Host "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!" -ForegroundColor Red
Write-Host ""
Write-Host "  This script (setup.ps1) has been replaced by:" -ForegroundColor Yellow
Write-Host "    .\Install-AutoPackager.ps1" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Install-AutoPackager.ps1 provides:" -ForegroundColor Yellow
Write-Host "    - Automatic Python & Git installation" -ForegroundColor White
Write-Host "    - Full Azure / Entra ID configuration" -ForegroundColor White
Write-Host "    - Redis & IntuneWinAppUtil auto-download" -ForegroundColor White
Write-Host "    - One-click setup with minimal prompts" -ForegroundColor White
Write-Host ""
$continue = Read-Host "  Continue with this DEPRECATED script anyway? (Y/N)"
if ($continue -notmatch "^[Yy]") {
    Write-Host ""
    Write-Host "  To use the new installer, run:" -ForegroundColor Green
    Write-Host "    .\Install-AutoPackager.ps1" -ForegroundColor Cyan
    Write-Host ""
    exit 0
}
Write-Host ""

Write-Host "==================================" -ForegroundColor Cyan
Write-Host "AutoPackager Setup Script v1.0" -ForegroundColor Cyan
Write-Host "==================================" -ForegroundColor Cyan
Write-Host ""

# Function to check if command exists
function Test-CommandExists {
    param($command)
    $null = Get-Command $command -ErrorAction SilentlyContinue
    return $?
}

# Step 1: Check Prerequisites
Write-Host "[1/8] Checking prerequisites..." -ForegroundColor Yellow

# Check Python
if (-not $SkipPythonCheck) {
    if (-not (Test-CommandExists python)) {
        Write-Host "ERROR: Python not found!" -ForegroundColor Red
        Write-Host "Please install Python 3.9+ from https://www.python.org/downloads/" -ForegroundColor Red
        Write-Host "Make sure to check 'Add Python to PATH' during installation" -ForegroundColor Yellow
        exit 1
    }

    $pythonVersion = python --version 2>&1
    Write-Host "  Found: $pythonVersion" -ForegroundColor Green
}

# Check Git
if (-not (Test-CommandExists git)) {
    Write-Host "WARNING: Git not found. You may need it for updates." -ForegroundColor Yellow
} else {
    Write-Host "  Git: OK" -ForegroundColor Green
}

Write-Host ""

# Step 2: Create Virtual Environment
Write-Host "[2/8] Creating Python virtual environment..." -ForegroundColor Yellow

if (Test-Path "venv") {
    Write-Host "  Virtual environment already exists, skipping..." -ForegroundColor Yellow
} else {
    python -m venv venv
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: Failed to create virtual environment" -ForegroundColor Red
        exit 1
    }
    Write-Host "  Virtual environment created successfully" -ForegroundColor Green
}

Write-Host ""

# Step 3: Activate Virtual Environment and Install Dependencies
Write-Host "[3/8] Installing Python dependencies..." -ForegroundColor Yellow

# Activate venv
& .\venv\Scripts\Activate.ps1

# Upgrade pip quietly
python -m pip install --upgrade pip --quiet

# Install requirements (no PostgreSQL driver - using SQLite for testing)
# Show progress so user can see it working
pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "ERROR: Failed to install one or more dependencies" -ForegroundColor Red
    Write-Host "Try running manually to see the full error:" -ForegroundColor Yellow
    Write-Host "  pip install -r requirements.txt" -ForegroundColor Cyan
    exit 1
}

Write-Host "  Dependencies installed successfully" -ForegroundColor Green
Write-Host ""

# Step 4: Check/Install Redis
Write-Host "[4/8] Checking Redis installation..." -ForegroundColor Yellow

$redisPath = ".\tools\redis"
if (Test-Path "$redisPath\redis-server.exe") {
    Write-Host "  Redis found at $redisPath" -ForegroundColor Green
} else {
    Write-Host "  Redis not found. Downloading Redis for Windows..." -ForegroundColor Yellow

    # Create tools directory
    New-Item -ItemType Directory -Force -Path ".\tools" | Out-Null
    New-Item -ItemType Directory -Force -Path "$redisPath" | Out-Null

    # Download Redis
    $redisUrl = "https://github.com/microsoftarchive/redis/releases/download/win-3.0.504/Redis-x64-3.0.504.zip"
    $redisZip = ".\tools\redis.zip"

    try {
        Write-Host "  Downloading from $redisUrl..." -ForegroundColor Yellow
        Invoke-WebRequest -Uri $redisUrl -OutFile $redisZip -UseBasicParsing

        # Extract Redis
        Write-Host "  Extracting Redis..." -ForegroundColor Yellow
        Expand-Archive -Path $redisZip -DestinationPath $redisPath -Force
        Remove-Item $redisZip

        Write-Host "  Redis installed successfully" -ForegroundColor Green
    } catch {
        Write-Host "  WARNING: Could not download Redis automatically" -ForegroundColor Yellow
        Write-Host "  Please download Redis manually from:" -ForegroundColor Yellow
        Write-Host "  https://github.com/microsoftarchive/redis/releases" -ForegroundColor Yellow
    }
}

Write-Host ""

# Step 5: Download IntuneWinAppUtil
Write-Host "[5/8] Checking IntuneWinAppUtil.exe..." -ForegroundColor Yellow

New-Item -ItemType Directory -Force -Path ".\tools" | Out-Null

if (Test-Path ".\tools\IntuneWinAppUtil.exe") {
    Write-Host "  IntuneWinAppUtil.exe found" -ForegroundColor Green
} else {
    Write-Host "  IntuneWinAppUtil.exe not found" -ForegroundColor Yellow
    Write-Host "  Please download it manually from:" -ForegroundColor Yellow
    Write-Host "  https://github.com/microsoft/Microsoft-Win32-Content-Prep-Tool" -ForegroundColor Yellow
    Write-Host "  and place it in the 'tools' folder" -ForegroundColor Yellow
}

Write-Host ""

# Step 6: Create .env file
Write-Host "[6/8] Creating .env configuration file..." -ForegroundColor Yellow

if (Test-Path ".env") {
    Write-Host "  .env file already exists, skipping..." -ForegroundColor Yellow
} else {
    Copy-Item ".env.template" ".env"
    Write-Host "  .env file created from template" -ForegroundColor Green
    Write-Host "  IMPORTANT: Edit .env file with your Azure credentials!" -ForegroundColor Yellow
}

Write-Host ""

# Step 7: Configure for SQLite or PostgreSQL
Write-Host "[7/8] Configuring database..." -ForegroundColor Yellow

if ($UseSQLite) {
    Write-Host "  Configuring for SQLite (testing mode)..." -ForegroundColor Yellow

    # Update config.yaml to use SQLite
    $configFile = ".\autopackager\config\config.yaml"
    $config = Get-Content $configFile -Raw

    # Backup original config
    Copy-Item $configFile "$configFile.backup" -Force

    # Replace database configuration using regex
    $sqliteConfig = @"
database:
  type: "sqlite"
  path: "data/autopackager.db"
"@

    # Replace the database section (from 'database:' to the next top-level key)
    $config = $config -replace '(?s)database:.*?(?=\n[a-z_]+:)', $sqliteConfig

    # Write updated config
    $config | Set-Content -Path $configFile -Force

    Write-Host "  SQLite configuration set" -ForegroundColor Green
    Write-Host "  Note: Original config backed up to config.yaml.backup" -ForegroundColor Yellow
} else {
    Write-Host "  Using PostgreSQL configuration" -ForegroundColor Green
    Write-Host "  Make sure PostgreSQL is installed and configured" -ForegroundColor Yellow
    Write-Host "  Update DB_PASSWORD in .env file" -ForegroundColor Yellow
}

Write-Host ""

# Step 8: Initialize Database
Write-Host "[8/8] Initializing database..." -ForegroundColor Yellow

# Create data directories
New-Item -ItemType Directory -Force -Path ".\data\downloads" | Out-Null
New-Item -ItemType Directory -Force -Path ".\data\packages" | Out-Null
New-Item -ItemType Directory -Force -Path ".\data\logs" | Out-Null
New-Item -ItemType Directory -Force -Path ".\data\catalogs\dell" | Out-Null
New-Item -ItemType Directory -Force -Path ".\data\catalogs\hp" | Out-Null
New-Item -ItemType Directory -Force -Path ".\data\catalogs\lenovo" | Out-Null

# Initialize database
python cli.py init

if ($LASTEXITCODE -eq 0) {
    Write-Host "  Database initialized successfully" -ForegroundColor Green
} else {
    Write-Host "  WARNING: Database initialization had issues" -ForegroundColor Yellow
    Write-Host "  You may need to configure your database settings in .env" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "==================================" -ForegroundColor Cyan
Write-Host "Setup Complete!" -ForegroundColor Green
Write-Host "==================================" -ForegroundColor Cyan
Write-Host ""

# Next Steps
Write-Host "NEXT STEPS:" -ForegroundColor Yellow
Write-Host ""
Write-Host "1. Edit .env file with your Azure credentials:" -ForegroundColor White
Write-Host "   - AZURE_TENANT_ID" -ForegroundColor Gray
Write-Host "   - AZURE_CLIENT_ID" -ForegroundColor Gray
Write-Host "   - AZURE_CLIENT_SECRET" -ForegroundColor Gray
Write-Host "   - RING0_GROUP_ID, RING1_GROUP_ID, RING2_GROUP_ID, RING3_GROUP_ID" -ForegroundColor Gray
Write-Host ""

Write-Host "2. Start Redis (in a new PowerShell window):" -ForegroundColor White
Write-Host "   .\tools\redis\redis-server.exe" -ForegroundColor Cyan
Write-Host ""

Write-Host "3. Start Celery Worker (in a new PowerShell window):" -ForegroundColor White
Write-Host "   .\venv\Scripts\Activate.ps1" -ForegroundColor Cyan
Write-Host "   python cli.py worker start" -ForegroundColor Cyan
Write-Host ""

Write-Host "4. Create your first driver job (in another PowerShell window):" -ForegroundColor White
Write-Host "   .\venv\Scripts\Activate.ps1" -ForegroundColor Cyan
Write-Host "   python cli.py create-driver-job --vendor dell --model ""Latitude 5420""" -ForegroundColor Cyan
Write-Host ""

Write-Host "5. Monitor jobs:" -ForegroundColor White
Write-Host "   python cli.py jobs list" -ForegroundColor Cyan
Write-Host ""

Write-Host "For detailed instructions, see IMPLEMENTATION_GUIDE.md" -ForegroundColor Yellow
Write-Host ""

# Create helper batch files
Write-Host "Creating helper scripts..." -ForegroundColor Yellow

# start-redis.bat
@"
@echo off
echo Starting Redis Server...
tools\redis\redis-server.exe redis.conf
"@ | Out-File -FilePath "start-redis.bat" -Encoding ASCII

# start-worker.bat
@"
@echo off
echo Activating Python virtual environment...
call venv\Scripts\activate.bat
echo Starting Celery Worker...
python cli.py worker start
"@ | Out-File -FilePath "start-worker.bat" -Encoding ASCII

# create-job.bat
@"
@echo off
call venv\Scripts\activate.bat
python cli.py create-driver-job %*
"@ | Out-File -FilePath "create-job.bat" -Encoding ASCII

# list-jobs.bat
@"
@echo off
call venv\Scripts\activate.bat
python cli.py jobs list %*
"@ | Out-File -FilePath "list-jobs.bat" -Encoding ASCII

Write-Host "  Created start-redis.bat" -ForegroundColor Green
Write-Host "  Created start-worker.bat" -ForegroundColor Green
Write-Host "  Created create-job.bat" -ForegroundColor Green
Write-Host "  Created list-jobs.bat" -ForegroundColor Green
Write-Host ""

Write-Host "Quick start commands:" -ForegroundColor Yellow
Write-Host "  .\start-redis.bat    - Start Redis server" -ForegroundColor White
Write-Host "  .\start-worker.bat   - Start Celery worker" -ForegroundColor White
Write-Host "  .\create-job.bat --vendor dell --model ""YourModel""" -ForegroundColor White
Write-Host "  .\list-jobs.bat      - List all jobs" -ForegroundColor White
Write-Host ""
