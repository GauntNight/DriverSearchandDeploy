<#
.SYNOPSIS
    AutoPackager One-Click Installer for Windows

.DESCRIPTION
    Installs and configures EVERYTHING needed to run AutoPackager on a local Windows
    workstation. Run this script once with local admin rights and it handles:

    LOCAL SETUP (fully automatic):
      - Python 3.12  (installs via winget or silent MSI if not present)
      - Git           (installs via winget if not present)
      - Python virtual environment + all pip dependencies
      - Redis for Windows  (downloads and configures)
      - IntuneWinAppUtil.exe  (downloads from Microsoft GitHub)
      - SQLite database  (zero-config, built into Python)
      - All data directories and runtime folders
      - Helper .bat scripts for daily use

    AZURE SETUP (requires 1 browser login):
      - Option A: You already created the App Registration ->provide 3 values ->done
      - Option B: Script creates the App Registration too ->provide 0 values ->done
      - Creates 4 deployment ring security groups in Entra ID
      - Configures all Microsoft Graph API permissions
      - Grants tenant-wide admin consent
      - Writes a complete .env file with all credentials

    MINIMUM STEPS FOR YOU:
      1. Run this script  (.\Install-AutoPackager.ps1)
      2. Log in when the browser opens
      3. Paste in your LLM API key (OpenAI or Anthropic)
      That's it. Everything else is automated.

.PARAMETER SkipAzure
    Skip Azure configuration (useful if you only want to set up the local environment
    and configure Azure later with azure-setup.ps1).

.PARAMETER SkipPython
    Skip Python installation check (if you know Python 3.9+ is already installed).

.PARAMETER UseSQLite
    Use SQLite for the database (default, recommended for testing).
    Use -UseSQLite:$false to configure PostgreSQL instead.

.PARAMETER LlmProvider
    Which LLM provider to use: "openai" (default) or "anthropic".

.PARAMETER LlmApiKey
    Your OpenAI or Anthropic API key. If not provided, script will prompt.

.PARAMETER TenantId
    Azure Tenant ID (skips the prompt if provided).

.PARAMETER CreateAppRegistration
    Pass to azure-setup.ps1 to create the App Registration automatically.

.EXAMPLE
    # Full automatic install (interactive prompts for credentials)
    .\Install-AutoPackager.ps1

.EXAMPLE
    # Full install, skip Azure setup for now
    .\Install-AutoPackager.ps1 -SkipAzure

.EXAMPLE
    # Full install, create App Registration automatically
    .\Install-AutoPackager.ps1 -TenantId "your-tenant-id" -CreateAppRegistration

.EXAMPLE
    # Full install, provide all credentials up front (no prompts)
    .\Install-AutoPackager.ps1 -TenantId "tid" -LlmApiKey "sk-..."
#>

param(
    [switch]$SkipAzure,
    [switch]$SkipPython,
    [bool]$UseSQLite = $true,
    [ValidateSet("openai", "anthropic")]
    [string]$LlmProvider = "openai",
    [string]$LlmApiKey,
    [string]$TenantId,
    [switch]$CreateAppRegistration
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# ------------------------------------------------------------------------------
# HELPERS
# ------------------------------------------------------------------------------

function Write-Banner {
    param([string]$Title, [ConsoleColor]$Color = "Cyan")
    Write-Host ""
    Write-Host ("-" * 60) -ForegroundColor $Color
    Write-Host "  $Title" -ForegroundColor $Color
    Write-Host ("-" * 60) -ForegroundColor $Color
    Write-Host ""
}

function Write-Step { param([string]$N, [string]$Msg) Write-Host "[Step $N] $Msg" -ForegroundColor Yellow }
function Write-OK   { param([string]$Msg) Write-Host "  [OK]  $Msg" -ForegroundColor Green }
function Write-Warn { param([string]$Msg) Write-Host " [WARN] $Msg" -ForegroundColor Yellow }
function Write-Fail { param([string]$Msg) Write-Host " [FAIL] $Msg" -ForegroundColor Red }
function Write-Info { param([string]$Msg) Write-Host "        $Msg" -ForegroundColor Gray }

function Test-CommandExists {
    param([string]$Command)
    return $null -ne (Get-Command $Command -ErrorAction SilentlyContinue)
}

function Get-PythonCommand {
    # Returns the python command name if Python 3.9+ is found, else $null.
    # Guards against the Windows Store "App Execution Alias" stub:
    #   - Get-Command finds it (it's a real .exe path)
    #   - Running it writes to stderr and exits non-zero
    #   - Under $ErrorActionPreference="Stop", stderr from native commands
    #     becomes a NativeCommandError and throws before we can check anything
    # Fix: temporarily set EAP to SilentlyContinue while probing each command.
    foreach ($cmd in @("python", "python3", "py")) {
        if (-not (Test-CommandExists $cmd)) { continue }
        try {
            $savedEAP = $ErrorActionPreference
            $ErrorActionPreference = "SilentlyContinue"
            $ver = & $cmd --version 2>&1
            $exitCode = $LASTEXITCODE
            $ErrorActionPreference = $savedEAP

            if ($exitCode -ne 0) { continue }   # Store stub or broken install
            if ($ver -match "Python (\d+)\.(\d+)") {
                $major = [int]$Matches[1]
                $minor = [int]$Matches[2]
                if ($major -eq 3 -and $minor -ge 9) {
                    return $cmd
                }
            }
        } catch {
            $ErrorActionPreference = $savedEAP
            continue
        }
    }
    return $null
}

function Invoke-WithRetry {
    param([scriptblock]$ScriptBlock, [string]$Name, [int]$Retries = 3)
    for ($i = 0; $i -le $Retries; $i++) {
        try {
            & $ScriptBlock
            return
        } catch {
            if ($i -lt $Retries) {
                $wait = [math]::Pow(2, $i + 1)
                Write-Warn "$Name failed (attempt $($i+1)/$Retries). Retrying in ${wait}s..."
                Start-Sleep -Seconds $wait
            } else {
                throw
            }
        }
    }
}

# ------------------------------------------------------------------------------
# DIAGNOSTICS TRACKER
# ------------------------------------------------------------------------------

$diagnostics = [ordered]@{
    "Python"              = @{ Status = "Skipped"; Detail = "" }
    "Git"                 = @{ Status = "Skipped"; Detail = "" }
    "VenvAndDeps"         = @{ Status = "Skipped"; Detail = "" }
    "Redis"               = @{ Status = "Skipped"; Detail = "" }
    "IntuneWinAppUtil"    = @{ Status = "Skipped"; Detail = "" }
    "DirectoriesAndDB"    = @{ Status = "Skipped"; Detail = "" }
    "LlmApiKey"           = @{ Status = "Skipped"; Detail = "" }
    "AzureSetup"          = @{ Status = "Skipped"; Detail = "" }
    "EnvFile"             = @{ Status = "Skipped"; Detail = "" }
    "HelperScripts"       = @{ Status = "Skipped"; Detail = "" }
}

# ------------------------------------------------------------------------------
# SELF-ELEVATION CHECK
# ------------------------------------------------------------------------------

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator
)

if (-not $isAdmin) {
    Write-Host ""
    Write-Host " NOTE: Not running as Administrator." -ForegroundColor Yellow
    Write-Host " Python/software installation may fail without admin rights." -ForegroundColor Yellow
    Write-Host " Recommend: Right-click PowerShell > Run as Administrator" -ForegroundColor Yellow
    Write-Host ""
    $cont = Read-Host " Continue anyway? (Y/N)"
    if ($cont -notmatch "^[Yy]") { exit 0 }
}

# ------------------------------------------------------------------------------
# BANNER
# ------------------------------------------------------------------------------

Write-Host ""
Write-Host "+----------------------------------------------------------+" -ForegroundColor Cyan
Write-Host "|       AutoPackager - Complete Installation Script        |" -ForegroundColor Cyan
Write-Host "|                                                          |" -ForegroundColor Cyan
Write-Host "|  Minimum steps YOU need to take:                        |" -ForegroundColor White
Write-Host "|    1. Run this script                                    |" -ForegroundColor White
Write-Host "|    2. Log in to Azure when the browser opens             |" -ForegroundColor White
Write-Host "|    3. Paste your LLM API key (OpenAI or Anthropic)       |" -ForegroundColor White
Write-Host "|    Everything else is automated.                         |" -ForegroundColor White
Write-Host "+----------------------------------------------------------+" -ForegroundColor Cyan

# Change to script directory
Set-Location $scriptDir

$totalSteps = if ($SkipAzure) { 8 } else { 10 }
$step = 0

# ------------------------------------------------------------------------------
# STEP 1: PYTHON
# ------------------------------------------------------------------------------

$step++
Write-Banner "Step $step/$totalSteps  Python 3.9+"

$pythonCmd = Get-PythonCommand

if ($SkipPython) {
    if (-not $pythonCmd) { $pythonCmd = "python" }
    $ver = & $pythonCmd --version 2>&1
    Write-OK "Python check skipped. Using: $pythonCmd ($ver)"
} elseif ($pythonCmd) {
    $ver = & $pythonCmd --version 2>&1
    Write-OK "Found: $ver (command: $pythonCmd)"
} else {
    # Not found (or only a Windows Store stub was present)
    Write-Warn "Python 3.9+ not found. Installing Python 3.12..."
    Write-Info "If you see an App Execution Alias warning, disable it:"
    Write-Info "Settings > Apps > Advanced app settings > App execution aliases > python.exe OFF"
    Write-Host ""

    $installed = $false

    # Try winget first (Windows 10 1809+ / Windows 11)
    if (Test-CommandExists winget) {
        Write-Info "Trying winget install..."
        winget install --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
        # winget exit codes: 0 = success, -1978335189 (0x8A150011) = already installed
        if ($LASTEXITCODE -eq 0 -or $LASTEXITCODE -eq -1978335189) {
            $installed = $true
            Write-OK "Python 3.12 installed via winget"
        } else {
            Write-Warn "winget install returned exit code $LASTEXITCODE - will try direct download"
        }
    }

    # Fallback: direct download from python.org
    if (-not $installed) {
        Write-Info "Downloading Python 3.12 installer from python.org..."
        $pyInstaller = "$env:TEMP\python-3.12.0-amd64.exe"
        $pyUrl = "https://www.python.org/ftp/python/3.12.0/python-3.12.0-amd64.exe"
        Invoke-WithRetry -Name "Python download" -ScriptBlock {
            Invoke-WebRequest -Uri $pyUrl -OutFile $pyInstaller -UseBasicParsing
        }
        Write-Info "Installing Python 3.12 (silent, adds to PATH)..."
        $installArgs = "/quiet InstallAllUsers=0 PrependPath=1 Include_test=0 Include_doc=0"
        Start-Process -FilePath $pyInstaller -ArgumentList $installArgs -Wait
        Remove-Item $pyInstaller -Force -ErrorAction SilentlyContinue
        $installed = $true
        Write-OK "Python 3.12 installed"
    }

    # Refresh PATH so the new install is visible in this session
    $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH", "Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("PATH", "User")

    $pythonCmd = Get-PythonCommand
    if (-not $pythonCmd) {
        Write-Fail "Python not found after installation."
        Write-Info "If the Windows Store stub is blocking, disable it in:"
        Write-Info "Settings > Apps > Advanced app settings > App execution aliases"
        Write-Info "Then re-run this script."
        Write-Info "Or download manually: https://www.python.org/downloads/"
        exit 1
    }
    $ver = & $pythonCmd --version 2>&1
    Write-OK "Verified: $ver"
}

$diagnostics["Python"] = @{ Status = "Pass"; Detail = "$(& $pythonCmd --version 2>&1)" }

# ------------------------------------------------------------------------------
# STEP 2: GIT (optional but recommended)
# ------------------------------------------------------------------------------

$step++
Write-Banner "Step $step/$totalSteps  Git"

if (Test-CommandExists git) {
    $gitVer = git --version
    Write-OK "$gitVer"
} else {
    Write-Warn "Git not found."
    if (Test-CommandExists winget) {
        Write-Info "Installing Git via winget..."
        winget install --id Git.Git --silent --accept-package-agreements --accept-source-agreements
        if ($LASTEXITCODE -eq 0 -or $LASTEXITCODE -eq -1978335189) {
            Write-OK "Git installed"
        } else {
            Write-Warn "Could not install Git automatically (exit: $LASTEXITCODE). It is optional but recommended."
            Write-Info "Install manually from https://git-scm.com/download/win"
        }
    } else {
        Write-Warn "Install Git manually from https://git-scm.com/download/win (optional)"
    }
}

if (Test-CommandExists git) {
    $diagnostics["Git"] = @{ Status = "Pass"; Detail = "$(git --version)" }
} else {
    $diagnostics["Git"] = @{ Status = "Warn"; Detail = "Git not installed (optional)" }
}

# ------------------------------------------------------------------------------
# STEP 3: PYTHON VIRTUAL ENVIRONMENT + DEPENDENCIES
# ------------------------------------------------------------------------------

$step++
Write-Banner "Step $step/$totalSteps  Python Virtual Environment and Dependencies"

if (Test-Path ".\venv\Scripts\python.exe") {
    Write-OK "Virtual environment already exists"
} else {
    Write-Info "Creating virtual environment in .\venv ..."
    & $pythonCmd -m venv venv
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "Failed to create virtual environment"
        exit 1
    }
    Write-OK "Virtual environment created"
}

$venvPython = ".\venv\Scripts\python.exe"
$venvPip    = ".\venv\Scripts\pip.exe"

Write-Info "Upgrading pip..."
& $venvPython -m pip install --upgrade pip --quiet

Write-Info "Installing dependencies from requirements.txt (this may take 2-3 minutes)..."
& $venvPip install -r requirements.txt --no-warn-script-location
if ($LASTEXITCODE -ne 0) {
    # Retry once - transient network errors are common during bulk installs
    Write-Warn "First install attempt failed. Retrying..."
    & $venvPip install -r requirements.txt --no-warn-script-location
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "Dependency installation failed."
        Write-Info "Run manually to see errors:  .\venv\Scripts\pip.exe install -r requirements.txt"
        exit 1
    }
}
Write-OK "All Python dependencies installed"
$diagnostics["VenvAndDeps"] = @{ Status = "Pass"; Detail = "venv created; dependencies installed" }

# ------------------------------------------------------------------------------
# STEP 4: REDIS FOR WINDOWS
# ------------------------------------------------------------------------------

$step++
Write-Banner "Step $step/$totalSteps  Redis (message broker)"

$redisDir = ".\tools\redis"
$redisExe = "$redisDir\redis-server.exe"

if (Test-Path $redisExe) {
    Write-OK "Redis already installed at $redisDir"
} else {
    New-Item -ItemType Directory -Force -Path $redisDir | Out-Null

    # Try Chocolatey first
    if (Test-CommandExists choco) {
        Write-Info "Installing Redis via Chocolatey..."
        try {
            choco install redis-64 --yes --no-progress
            $chocoRedis = "C:\tools\redis\redis-server.exe"
            if (Test-Path $chocoRedis) {
                # Copy to our tools dir so scripts can find it
                Copy-Item "C:\tools\redis\*" $redisDir -Recurse -Force
                Write-OK "Redis installed via Chocolatey"
            }
        } catch {
            Write-Warn "Chocolatey install failed: $_"
        }
    }

    # Fallback: download archived Windows port
    if (-not (Test-Path $redisExe)) {
        Write-Info "Downloading Redis for Windows..."
        $redisZip = ".\tools\redis.zip"
        $redisUrl = "https://github.com/microsoftarchive/redis/releases/download/win-3.0.504/Redis-x64-3.0.504.zip"
        Invoke-WithRetry -Name "Redis download" -ScriptBlock {
            Invoke-WebRequest -Uri $redisUrl -OutFile $redisZip -UseBasicParsing
        }
        Write-Info "Extracting Redis..."
        Expand-Archive -Path $redisZip -DestinationPath $redisDir -Force
        Remove-Item $redisZip -Force
    }

    if (Test-Path $redisExe) {
        Write-OK "Redis installed successfully"
    } else {
        Write-Warn "Redis not found after download. You may need to install it manually."
        Write-Info "https://github.com/microsoftarchive/redis/releases"
    }
}

if (Test-Path $redisExe) {
    $diagnostics["Redis"] = @{ Status = "Pass"; Detail = "redis-server.exe found at $redisDir" }
} else {
    $diagnostics["Redis"] = @{ Status = "Warn"; Detail = "Redis not found; install manually" }
}

# ------------------------------------------------------------------------------
# STEP 5: INTUNEWINAPPUTIL.EXE
# ------------------------------------------------------------------------------

$step++
Write-Banner "Step $step/$totalSteps  IntuneWinAppUtil.exe (Microsoft Win32 Content Prep Tool)"

New-Item -ItemType Directory -Force -Path ".\tools" | Out-Null
$intuneUtil = ".\tools\IntuneWinAppUtil.exe"

if (Test-Path $intuneUtil) {
    Write-OK "IntuneWinAppUtil.exe already present"
} else {
    Write-Info "Downloading IntuneWinAppUtil.exe from Microsoft GitHub..."
    $intuneUrl = "https://github.com/microsoft/Microsoft-Win32-Content-Prep-Tool/raw/master/IntuneWinAppUtil.exe"
    try {
        Invoke-WithRetry -Name "IntuneWinAppUtil download" -ScriptBlock {
            Invoke-WebRequest -Uri $intuneUrl -OutFile $intuneUtil -UseBasicParsing
        }
        Write-OK "IntuneWinAppUtil.exe downloaded"
    } catch {
        Write-Warn "Could not download IntuneWinAppUtil.exe: $_"
        Write-Info "Download manually from: https://github.com/microsoft/Microsoft-Win32-Content-Prep-Tool"
        Write-Info "Place it in the 'tools' folder."
    }
}

if (Test-Path $intuneUtil) {
    $diagnostics["IntuneWinAppUtil"] = @{ Status = "Pass"; Detail = "IntuneWinAppUtil.exe present" }
} else {
    $diagnostics["IntuneWinAppUtil"] = @{ Status = "Warn"; Detail = "Not downloaded; download manually from GitHub" }
}

# ------------------------------------------------------------------------------
# STEP 6: DATA DIRECTORIES + DATABASE
# ------------------------------------------------------------------------------

$step++
Write-Banner "Step $step/$totalSteps  Directories & Database"

$dirs = @(
    ".\data\downloads",
    ".\data\packages",
    ".\data\logs",
    ".\data\catalogs\dell",
    ".\data\catalogs\hp",
    ".\data\catalogs\lenovo"
)
foreach ($d in $dirs) {
    New-Item -ItemType Directory -Force -Path $d | Out-Null
}
Write-OK "Data directories created"

# Configure SQLite in config.yaml
if ($UseSQLite) {
    $configFile = ".\autopackager\config\config.yaml"
    if (Test-Path $configFile) {
        $config = Get-Content $configFile -Raw
        if ($config -match 'type:\s*"postgresql"') {
            Copy-Item $configFile "$configFile.backup" -Force
            $sqliteBlock = "database:`n  type: `"sqlite`"`n  path: `"data/autopackager.db`""
            $config = $config -replace '(?s)database:.*?(?=\n[a-z_]+:)', $sqliteBlock
            $config | Set-Content $configFile -Force
            Write-OK "config.yaml updated to use SQLite (backup: config.yaml.backup)"
        } else {
            Write-OK "config.yaml already set to SQLite"
        }
    }
}

# Initialise database
# Suppress EAP during this call: cli.py writes JSON structured logs to stderr
# (via structlog/python-json-logger), which PowerShell treats as NativeCommandError
# under $ErrorActionPreference="Stop" even when the command succeeds.
Write-Info "Initialising database schema..."
$savedEAP = $ErrorActionPreference
$ErrorActionPreference = "SilentlyContinue"
$initOutput = & $venvPython cli.py init 2>&1
$initExit = $LASTEXITCODE
$ErrorActionPreference = $savedEAP

# Print any non-JSON output lines (the human-readable status messages)
$initOutput | Where-Object { $_ -notmatch '^\s*\{' } | ForEach-Object { Write-Info $_ }

if ($initExit -eq 0) {
    Write-OK "Database initialised"
} else {
    Write-Warn "Database init had issues - may need Azure credentials first (run again after .env is set)"
}

if ($initExit -eq 0) {
    $diagnostics["DirectoriesAndDB"] = @{ Status = "Pass"; Detail = "Directories created; database initialised" }
} else {
    $diagnostics["DirectoriesAndDB"] = @{ Status = "Warn"; Detail = "Directories created; database init needs Azure credentials" }
}

# ------------------------------------------------------------------------------
# STEP 7: LLM API KEY
# ------------------------------------------------------------------------------

$step++
Write-Banner "Step $step/$totalSteps  LLM API Key"

if (-not $LlmApiKey) {
    Write-Host "  AutoPackager uses an LLM for intelligent driver discovery." -ForegroundColor White
    Write-Host ""
    Write-Host "  OpenAI  : https://platform.openai.com/api-keys" -ForegroundColor Gray
    Write-Host "  Anthropic: https://console.anthropic.com/settings/keys" -ForegroundColor Gray
    Write-Host ""

    if ($LlmProvider -eq "openai") {
        Write-Host "  Configured provider: OpenAI (gpt-4-turbo-preview)" -ForegroundColor Gray
    } else {
        Write-Host "  Configured provider: Anthropic (Claude)" -ForegroundColor Gray
    }

    $secureKey = Read-Host "  Enter your LLM API key (or press Enter to skip and add later)" -AsSecureString
    if ($secureKey.Length -gt 0) {
        $LlmApiKey = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
            [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
        )
        Write-OK "LLM API key captured"
    } else {
        $LlmApiKey = "your_llm_api_key_here"
        Write-Warn "No key provided. Edit .env later and set LLM_API_KEY."
    }
}

if ($LlmApiKey -and $LlmApiKey -ne "your_llm_api_key_here") {
    $diagnostics["LlmApiKey"] = @{ Status = "Pass"; Detail = "API key provided" }
} else {
    $diagnostics["LlmApiKey"] = @{ Status = "Warn"; Detail = "No key provided; edit .env to set LLM_API_KEY" }
}

# ------------------------------------------------------------------------------
# STEP 8: AZURE SETUP
# ------------------------------------------------------------------------------

$azureResult = $null

if (-not $SkipAzure) {
    $step++
    Write-Banner "Step $step/$totalSteps  Azure Configuration"
    Write-Host "  This step will open your browser to log in to Azure." -ForegroundColor White
    Write-Host "  The script will then configure everything automatically." -ForegroundColor White
    Write-Host ""
    Write-Host "  What you need:"  -ForegroundColor White

    if ($CreateAppRegistration) {
        Write-Host "   - Your Azure Tenant ID (already provided: $TenantId)" -ForegroundColor Gray
        Write-Host "   - Global Admin or Application+Group Admin role" -ForegroundColor Gray
    } else {
        Write-Host "   - Your Azure Tenant ID" -ForegroundColor Gray
        Write-Host "   - Your App Registration Client ID" -ForegroundColor Gray
        Write-Host "   - A Client Secret from the App Registration" -ForegroundColor Gray
        Write-Host "   - Global Admin or Application+Group Admin role (for admin consent)" -ForegroundColor Gray
        Write-Host ""
        Write-Host "  Don't have an App Registration yet?" -ForegroundColor Yellow
        Write-Host "  Option 1: Create one manually (5 min) at portal.azure.com > App registrations" -ForegroundColor Gray
        Write-Host "  Option 2: Re-run with -CreateAppRegistration to skip that step entirely" -ForegroundColor Gray
    }

    Write-Host ""
    $runAzure = Read-Host "  Run Azure setup now? (Y/N)"

    if ($runAzure -match "^[Yy]") {
        # Use hashtable splatting, not array splatting.
        # Array splatting @("-SwitchName") does not reliably bind switch parameters
        # by name - PowerShell may treat elements as positional arguments, causing
        # the first string ("-OutputEnvFile") to be bound to $TenantId.
        $azureArgs = @{
            OutputEnvFile = $true
            EnvFilePath   = (Join-Path $scriptDir ".env")
        }
        if ($TenantId)              { $azureArgs["TenantId"]              = $TenantId }
        if ($CreateAppRegistration) { $azureArgs["CreateAppRegistration"] = $true }

        try {
            $azureResult = & "$scriptDir\azure-setup.ps1" @azureArgs
        } catch {
            # Exceptions from the script itself (parse errors, etc.)
            Write-Fail "Azure setup failed with exception: $_"
            $azureResult = $null
        }
        # azure-setup.ps1 uses exit 1 on failure; that sets $LASTEXITCODE but
        # does NOT throw, so try/catch alone is insufficient.
        if ($LASTEXITCODE -ne 0 -or $null -eq $azureResult) {
            Write-Fail "Azure setup did not complete successfully (exit code: $LASTEXITCODE)."
            Write-Warn "Common cause: M365/Intune-only tenant with no Azure subscription - update applied, re-run to retry."
            Write-Warn "You can also run .\azure-setup.ps1 -OutputEnvFile separately after this installer finishes."
            $SkipAzure = $true
        } else {
            Write-OK "Azure configuration complete"
        }
    } else {
        Write-Warn "Azure setup skipped. Run .\azure-setup.ps1 when ready."
        $SkipAzure = $true
    }
}

if (-not $SkipAzure -and $azureResult -ne $null) {
    $diagnostics["AzureSetup"] = @{ Status = "Pass"; Detail = "Azure configuration complete" }
} elseif ($SkipAzure) {
    $diagnostics["AzureSetup"] = @{ Status = "Warn"; Detail = "Skipped; run azure-setup.ps1 separately" }
} else {
    $diagnostics["AzureSetup"] = @{ Status = "Fail"; Detail = "Azure setup failed; run azure-setup.ps1 -OutputEnvFile" }
}

# ------------------------------------------------------------------------------
# STEP 9: WRITE / UPDATE .env FILE
# ------------------------------------------------------------------------------

$step++
Write-Banner "Step $step/$totalSteps  Writing .env file"

if ($SkipAzure -or $azureResult -eq $null) {
    # No Azure result - write a template .env or update existing with LLM key
    if (-not (Test-Path ".\.env")) {
        Copy-Item ".env.template" ".env"
        Write-OK ".env created from template"
    }

    # Update LLM_API_KEY in existing .env
    if ($LlmApiKey -ne "your_llm_api_key_here") {
        $envContent = Get-Content ".\.env" -Raw
        $envContent = $envContent -replace "LLM_API_KEY=.*", "LLM_API_KEY=$LlmApiKey"
        $envContent | Set-Content ".\.env" -Force
        Write-OK "LLM_API_KEY updated in .env"
    }
    Write-Warn "ACTION REQUIRED: Edit .env and fill in AZURE_TENANT_ID, AZURE_CLIENT_ID, etc."
} else {
    # azure-setup.ps1 already wrote the .env - just update the LLM key
    if ($LlmApiKey -ne "your_llm_api_key_here" -and (Test-Path ".\.env")) {
        $envContent = Get-Content ".\.env" -Raw
        $envContent = $envContent -replace "LLM_API_KEY=.*", "LLM_API_KEY=$LlmApiKey"
        $envContent | Set-Content ".\.env" -Force
    }
    Write-OK ".env written with all credentials"
}

if (Test-Path ".\.env") {
    $diagnostics["EnvFile"] = @{ Status = "Pass"; Detail = ".env file present" }
} else {
    $diagnostics["EnvFile"] = @{ Status = "Fail"; Detail = ".env file missing; copy .env.template to .env" }
}

# ------------------------------------------------------------------------------
# STEP 10: HELPER SCRIPTS
# ------------------------------------------------------------------------------

$step++
Write-Banner "Step $step/$totalSteps  Creating helper scripts"

# start-redis.bat
@"
@echo off
echo Starting Redis Server...
echo Press Ctrl+C to stop.
tools\redis\redis-server.exe redis.conf
"@ | Out-File -FilePath "start-redis.bat" -Encoding ASCII

# start-worker.bat
@"
@echo off
echo Activating virtual environment...
call venv\Scripts\activate.bat
echo Starting Celery worker (Ctrl+C to stop)...
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

# launch-all.bat - starts Redis and Worker in separate windows
@"
@echo off
echo AutoPackager Launcher
echo =====================
echo Starting Redis in a new window...
start "Redis Server" cmd /k "tools\redis\redis-server.exe redis.conf"
timeout /t 2 /nobreak >nul
echo Starting Celery Worker in a new window...
start "Celery Worker" cmd /k "call venv\Scripts\activate.bat && python cli.py worker start"
echo.
echo Both services started.
echo To create a driver job, run:
echo   create-job.bat --vendor dell --model "Your Model"
echo.
pause
"@ | Out-File -FilePath "launch-all.bat" -Encoding ASCII

Write-OK "Created: start-redis.bat"
Write-OK "Created: start-worker.bat"
Write-OK "Created: create-job.bat"
Write-OK "Created: list-jobs.bat"
Write-OK "Created: launch-all.bat  (starts everything at once)"
$diagnostics["HelperScripts"] = @{ Status = "Pass"; Detail = "All helper .bat scripts created" }

# ------------------------------------------------------------------------------
# DIAGNOSTIC REPORT
# ------------------------------------------------------------------------------

Write-Host ""
Write-Banner "Installation Diagnostic Report"

$hasFailures = $false
foreach ($key in $diagnostics.Keys) {
    $entry = $diagnostics[$key]
    $status = $entry.Status
    $detail = $entry.Detail
    switch ($status) {
        "Pass"    { Write-Host "  [PASS]  $key" -ForegroundColor Green -NoNewline; Write-Host "  $detail" -ForegroundColor Gray }
        "Warn"    { Write-Host "  [WARN]  $key" -ForegroundColor Yellow -NoNewline; Write-Host "  $detail" -ForegroundColor Gray }
        "Fail"    { Write-Host "  [FAIL]  $key" -ForegroundColor Red -NoNewline; Write-Host "  $detail" -ForegroundColor Gray; $hasFailures = $true }
        "Skipped" { Write-Host "  [SKIP]  $key" -ForegroundColor DarkGray -NoNewline; Write-Host "  $detail" -ForegroundColor Gray }
    }
}

if ($hasFailures) {
    Write-Host ""
    Write-Host "  One or more steps failed. Remediation hints:" -ForegroundColor Red
    foreach ($key in $diagnostics.Keys) {
        if ($diagnostics[$key].Status -eq "Fail") {
            switch ($key) {
                "Python"           { Write-Host "    - Python: Install Python 3.9+ from https://www.python.org/downloads/" -ForegroundColor Yellow }
                "VenvAndDeps"      { Write-Host "    - VenvAndDeps: Run '.\venv\Scripts\pip.exe install -r requirements.txt' manually" -ForegroundColor Yellow }
                "Redis"            { Write-Host "    - Redis: Download from https://github.com/microsoftarchive/redis/releases" -ForegroundColor Yellow }
                "IntuneWinAppUtil" { Write-Host "    - IntuneWinAppUtil: Download from https://github.com/microsoft/Microsoft-Win32-Content-Prep-Tool" -ForegroundColor Yellow }
                "DirectoriesAndDB" { Write-Host "    - DirectoriesAndDB: Run '.\venv\Scripts\python.exe cli.py init' after configuring .env" -ForegroundColor Yellow }
                "AzureSetup"      { Write-Host "    - AzureSetup: Run '.\azure-setup.ps1 -OutputEnvFile' separately" -ForegroundColor Yellow }
                "EnvFile"         { Write-Host "    - EnvFile: Copy .env.template to .env and fill in credentials" -ForegroundColor Yellow }
                default           { Write-Host "    - ${key}: $($diagnostics[$key].Detail)" -ForegroundColor Yellow }
            }
        }
    }
}

Write-Host ""

# ------------------------------------------------------------------------------
# FINAL SUMMARY
# ------------------------------------------------------------------------------

Write-Host ""
Write-Host "+----------------------------------------------------------+" -ForegroundColor Green
Write-Host "|                Installation Complete!                    |" -ForegroundColor Green
Write-Host "+----------------------------------------------------------+" -ForegroundColor Green
Write-Host ""
Write-Host " What was installed:" -ForegroundColor White
Write-Host "   Python virtual environment  ->.\venv" -ForegroundColor Gray
Write-Host "   Redis for Windows           ->.\tools\redis\" -ForegroundColor Gray
Write-Host "   IntuneWinAppUtil.exe        ->.\tools\" -ForegroundColor Gray
Write-Host "   SQLite database             ->.\data\autopackager.db" -ForegroundColor Gray
Write-Host "   Configuration               ->.\.env  and  autopackager\config\config.yaml" -ForegroundColor Gray
Write-Host ""

if ($SkipAzure) {
    Write-Host " PENDING - Azure setup not yet run:" -ForegroundColor Yellow
    Write-Host "   Run: .\azure-setup.ps1 -OutputEnvFile" -ForegroundColor Cyan
    Write-Host "   Or:  .\azure-setup.ps1 -TenantId <id> -CreateAppRegistration -OutputEnvFile" -ForegroundColor Cyan
    Write-Host ""
}

$envExists = Test-Path ".\.env"
$llmSet = $false
if ($envExists) {
    $envContent = Get-Content ".\.env" -Raw
    $llmSet = $envContent -notmatch "LLM_API_KEY=your_llm_api_key_here" -and
              $envContent -match "LLM_API_KEY=\S+"
}

if (-not $llmSet) {
    Write-Host " ACTION REQUIRED:" -ForegroundColor Red
    Write-Host "   Edit .env and set LLM_API_KEY to your OpenAI or Anthropic key" -ForegroundColor Yellow
    Write-Host ""
}

Write-Host " TO START AUTOPACKAGER:" -ForegroundColor White
Write-Host "   Option A - Start everything at once:" -ForegroundColor Gray
Write-Host "     .\launch-all.bat" -ForegroundColor Cyan
Write-Host ""
Write-Host "   Option B - Start services individually:" -ForegroundColor Gray
Write-Host "     Window 1: .\start-redis.bat" -ForegroundColor Cyan
Write-Host "     Window 2: .\start-worker.bat" -ForegroundColor Cyan
Write-Host ""
Write-Host " TO CREATE YOUR FIRST DRIVER JOB:" -ForegroundColor White
Write-Host "   .\create-job.bat --vendor dell --model ""Latitude 5420"" --driver-type chipset" -ForegroundColor Cyan
Write-Host ""
Write-Host " TO MONITOR JOBS:" -ForegroundColor White
Write-Host "   .\list-jobs.bat" -ForegroundColor Cyan
Write-Host ""
Write-Host " DOCUMENTATION:" -ForegroundColor White
Write-Host "   IMPLEMENTATION_GUIDE.md  - Full walkthrough" -ForegroundColor Gray
Write-Host "   WINDOWS_TESTING.md       - Troubleshooting" -ForegroundColor Gray
Write-Host ""
