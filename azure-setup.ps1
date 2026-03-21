<#
.SYNOPSIS
    AutoPackager Azure Setup Script - Automates ALL Azure configuration.

.DESCRIPTION
    After you create an App Registration in the Azure Portal (the ONE manual step),
    this script handles everything else:
      - Validates your App Registration
      - Adds all required Microsoft Graph API permissions
      - Grants tenant-wide admin consent
      - Creates the 4 deployment ring security groups in Entra ID
      - Optionally creates the App Registration itself (so you can skip the portal entirely)
      - Writes a ready-to-use .env file

    Prerequisites:
      - Azure CLI installed (script will offer to install it if missing)
      - Account with Global Admin or Application Administrator + Group Administrator roles

.PARAMETER TenantId
    Your Azure AD Tenant (Directory) ID.
    Find it: Azure Portal > Microsoft Entra ID > Overview > Tenant ID.

.PARAMETER ClientId
    Application (Client) ID of your App Registration.
    Find it: Azure Portal > App Registrations > your app > Overview.

.PARAMETER ClientSecret
    Client Secret value (not the secret ID).
    Create it: App Registration > Certificates & secrets > New client secret.

.PARAMETER CreateAppRegistration
    Skip the manual App Registration step - create it fully automatically.
    Requires -TenantId only. ClientId/Secret will be created and output.

.PARAMETER AppName
    Name for a new App Registration (used with -CreateAppRegistration).
    Default: AutoPackager-ServicePrincipal

.PARAMETER OutputEnvFile
    Write all credentials to .env in the current directory.

.PARAMETER EnvFilePath
    Path for the .env file. Default: .\.env

.EXAMPLE
    # Minimum steps: You created the App Registration, provide 3 values, script does the rest
    .\azure-setup.ps1 -TenantId "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" `
                      -ClientId "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" `
                      -ClientSecret "your-secret-value" `
                      -OutputEnvFile

.EXAMPLE
    # Zero manual Azure steps - script creates everything
    .\azure-setup.ps1 -TenantId "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" `
                      -CreateAppRegistration `
                      -OutputEnvFile

.EXAMPLE
    # Interactive mode - script prompts for all inputs
    .\azure-setup.ps1 -OutputEnvFile
#>

param(
    [string]$TenantId,
    [string]$ClientId,
    [string]$ClientSecret,
    [switch]$CreateAppRegistration,
    [string]$AppName = "AutoPackager-ServicePrincipal",
    [switch]$OutputEnvFile,
    [string]$EnvFilePath = ".\.env"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ------------------------------------------------------------------------------
# HELPERS
# ------------------------------------------------------------------------------

function Write-Step { param([string]$Msg) Write-Host "`n[$Msg]" -ForegroundColor Cyan }
function Write-OK   { param([string]$Msg) Write-Host "  OK  $Msg" -ForegroundColor Green }
function Write-Warn { param([string]$Msg) Write-Host "  WARN $Msg" -ForegroundColor Yellow }
function Write-Fail { param([string]$Msg) Write-Host "  FAIL $Msg" -ForegroundColor Red }
function Write-Info { param([string]$Msg) Write-Host "       $Msg" -ForegroundColor Gray }

function Invoke-Az {
    param([string[]]$Args)
    $result = & az @Args 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "az $($Args -join ' ') failed: $result"
    }
    return $result
}

function Invoke-AzJson {
    param([string[]]$Args)
    $result = Invoke-Az ($Args + @("--output", "json"))
    return $result | ConvertFrom-Json
}

# ------------------------------------------------------------------------------
# BANNER
# ------------------------------------------------------------------------------

Write-Host ""
Write-Host "+--------------------------------------------------+" -ForegroundColor Cyan
Write-Host "|     AutoPackager - Azure Configuration Setup     |" -ForegroundColor Cyan
Write-Host "+--------------------------------------------------+" -ForegroundColor Cyan
Write-Host ""

# ------------------------------------------------------------------------------
# STEP 1: ENSURE AZURE CLI IS INSTALLED
# ------------------------------------------------------------------------------

Write-Step "1/7  Checking Azure CLI"

$azAvailable = $null -ne (Get-Command az -ErrorAction SilentlyContinue)

if (-not $azAvailable) {
    Write-Warn "Azure CLI not found."
    Write-Host ""
    $install = Read-Host "  Install Azure CLI now? (Y/N)"
    if ($install -match "^[Yy]") {
        Write-Info "Installing Azure CLI via winget..."
        $wingetAvailable = $null -ne (Get-Command winget -ErrorAction SilentlyContinue)
        if ($wingetAvailable) {
            winget install --id Microsoft.AzureCLI --silent --accept-package-agreements --accept-source-agreements
        } else {
            Write-Info "Winget not available. Downloading Azure CLI installer..."
            $cliInstaller = "$env:TEMP\AzureCLI.msi"
            Invoke-WebRequest -Uri "https://aka.ms/installazurecliwindows" -OutFile $cliInstaller -UseBasicParsing
            Write-Info "Running installer (this may take a minute)..."
            Start-Process msiexec.exe -ArgumentList "/I `"$cliInstaller`" /quiet /norestart" -Wait
            Remove-Item $cliInstaller -Force -ErrorAction SilentlyContinue
        }
        # Refresh PATH
        $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH", "Machine") + ";" +
                    [System.Environment]::GetEnvironmentVariable("PATH", "User")
        if ($null -eq (Get-Command az -ErrorAction SilentlyContinue)) {
            Write-Fail "Azure CLI installation failed. Please install manually:"
            Write-Info "  https://learn.microsoft.com/en-us/cli/azure/install-azure-cli-windows"
            exit 1
        }
        Write-OK "Azure CLI installed successfully"
    } else {
        Write-Fail "Azure CLI is required. Exiting."
        exit 1
    }
} else {
    $azVersion = (az version --output json | ConvertFrom-Json)."azure-cli"
    Write-OK "Azure CLI $azVersion"
}

# ------------------------------------------------------------------------------
# STEP 2: COLLECT INPUTS
# ------------------------------------------------------------------------------

Write-Step "2/7  Collecting configuration"

if (-not $TenantId) {
    Write-Host ""
    Write-Host "  Your Tenant ID is in: Azure Portal > Microsoft Entra ID > Overview" -ForegroundColor Gray
    $TenantId = (Read-Host "  Enter your Azure Tenant ID").Trim()
}

if (-not $TenantId -or $TenantId.Length -lt 10) {
    Write-Fail "Tenant ID is required."
    exit 1
}

Write-OK "Tenant ID: $TenantId"

# ------------------------------------------------------------------------------
# STEP 3: AZURE LOGIN
# ------------------------------------------------------------------------------

Write-Step "3/7  Signing in to Azure"
Write-Info "A browser window will open for authentication..."
Write-Host ""

try {
    $loginResult = az login --tenant $TenantId --output json | ConvertFrom-Json
    $accountName = $loginResult[0].user.name
    Write-OK "Signed in as: $accountName"
} catch {
    Write-Fail "Login failed: $_"
    exit 1
}

# Set active subscription/tenant
az account set --subscription (az account show --query id -o tsv) 2>$null

# ------------------------------------------------------------------------------
# STEP 4: APP REGISTRATION (create or validate existing)
# ------------------------------------------------------------------------------

Write-Step "4/7  App Registration"

if ($CreateAppRegistration) {
    Write-Info "Creating App Registration: $AppName"

    # Check if already exists
    $existingApp = az ad app list --display-name $AppName --output json | ConvertFrom-Json
    if ($existingApp.Count -gt 0) {
        Write-Warn "App '$AppName' already exists. Using existing registration."
        $ClientId = $existingApp[0].appId
        Write-OK "App ID: $ClientId"
    } else {
        $newApp = az ad app create --display-name $AppName --output json | ConvertFrom-Json
        $ClientId = $newApp.appId
        Write-OK "Created App Registration: $AppName"
        Write-OK "Client ID: $ClientId"

        # Create a service principal for the app
        $sp = az ad sp create --id $ClientId --output json | ConvertFrom-Json
        Write-OK "Created Service Principal"
    }

    # Create a client secret
    Write-Info "Creating client secret (valid for 2 years)..."
    $secretResult = az ad app credential reset `
        --id $ClientId `
        --display-name "AutoPackager-Secret" `
        --years 2 `
        --output json | ConvertFrom-Json
    $ClientSecret = $secretResult.password
    Write-OK "Client secret created"
    Write-Warn "SAVE THIS SECRET NOW - it cannot be retrieved again:"
    Write-Host "  Client Secret: $ClientSecret" -ForegroundColor Yellow

} else {
    # Use existing App Registration
    if (-not $ClientId) {
        Write-Host ""
        Write-Host "  Your Client ID is in: Azure Portal > App Registrations > your app > Overview" -ForegroundColor Gray
        $ClientId = (Read-Host "  Enter your App Registration Client ID").Trim()
    }
    if (-not $ClientSecret) {
        Write-Host ""
        Write-Host "  Create a secret: App Registration > Certificates & secrets > New client secret" -ForegroundColor Gray
        $secureSecret = Read-Host "  Enter your Client Secret" -AsSecureString
        $ClientSecret = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
            [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureSecret)
        )
    }

    # Validate the app exists
    try {
        $app = az ad app show --id $ClientId --output json | ConvertFrom-Json
        Write-OK "Validated App: $($app.displayName)"
    } catch {
        Write-Fail "App Registration not found for Client ID: $ClientId"
        Write-Info "Ensure the App Registration exists and the Client ID is correct."
        exit 1
    }

    # Ensure service principal exists
    $sp = az ad sp show --id $ClientId --output json 2>$null | ConvertFrom-Json
    if (-not $sp) {
        Write-Info "Creating service principal for App Registration..."
        $sp = az ad sp create --id $ClientId --output json | ConvertFrom-Json
        Write-OK "Service principal created"
    } else {
        Write-OK "Service principal exists"
    }
}

# ------------------------------------------------------------------------------
# STEP 5: API PERMISSIONS + ADMIN CONSENT
# ------------------------------------------------------------------------------

Write-Step "5/7  Configuring Microsoft Graph API permissions"

$graphAppId = "00000003-0000-0000-c000-000000000000"

# Dynamically look up permission IDs from Microsoft Graph service principal
Write-Info "Looking up permission IDs from Microsoft Graph..."
$graphSP = az ad sp show --id $graphAppId --output json | ConvertFrom-Json

$requiredPermissions = @(
    "DeviceManagementApps.ReadWrite.All",
    "DeviceManagementConfiguration.ReadWrite.All",
    "Group.Read.All",
    "GroupMember.Read.All"
)

$permissionsToAdd = @()
foreach ($permName in $requiredPermissions) {
    $role = $graphSP.appRoles | Where-Object { $_.value -eq $permName }
    if (-not $role) {
        Write-Fail "Permission not found in Microsoft Graph: $permName"
        exit 1
    }
    $permissionsToAdd += "$($role.id)=Role"
    Write-Info "Found: $permName ($($role.id))"
}

# Add permissions
Write-Info "Adding API permissions to App Registration..."
try {
    az ad app permission add `
        --id $ClientId `
        --api $graphAppId `
        --api-permissions $permissionsToAdd `
        --output none
    Write-OK "API permissions added"
} catch {
    # May fail if already added - check existing
    Write-Warn "Permission add may have partially failed (permissions may already exist): $_"
}

# Grant admin consent
Write-Info "Granting tenant-wide admin consent..."
try {
    az ad app permission admin-consent --id $ClientId --output none
    Write-OK "Admin consent granted"
} catch {
    Write-Warn "Admin consent failed - you may need Global Admin role."
    Write-Warn "Grant manually: Azure Portal > App Registrations > API Permissions > Grant admin consent"
}

# Verify permissions
Write-Info "Verifying permissions..."
$perms = az ad app permission list --id $ClientId --output json | ConvertFrom-Json
if ($perms.Count -gt 0) {
    Write-OK "Permissions configured ($($perms.Count) permission set(s))"
} else {
    Write-Warn "Could not verify permissions. Check Azure Portal manually."
}

# ------------------------------------------------------------------------------
# STEP 6: CREATE DEPLOYMENT RING SECURITY GROUPS
# ------------------------------------------------------------------------------

Write-Step "6/7  Creating deployment ring security groups"

$rings = @(
    @{ Name = "AutoPackager-Ring0-ITPilot";          Nickname = "AutoPackager-Ring0"; Description = "AutoPackager Ring 0 - IT Pilot (0-day deferral)" },
    @{ Name = "AutoPackager-Ring1-EarlyAdopters";    Nickname = "AutoPackager-Ring1"; Description = "AutoPackager Ring 1 - Early Adopters (3-day deferral)" },
    @{ Name = "AutoPackager-Ring2-BroadDeployment";  Nickname = "AutoPackager-Ring2"; Description = "AutoPackager Ring 2 - Broad Deployment (7-day deferral)" },
    @{ Name = "AutoPackager-Ring3-CriticalSystems";  Nickname = "AutoPackager-Ring3"; Description = "AutoPackager Ring 3 - Critical Systems (14-day deferral)" }
)

$groupIds = @{}

foreach ($ring in $rings) {
    # Check if group already exists
    $existing = az ad group list --display-name $ring.Name --output json | ConvertFrom-Json
    if ($existing.Count -gt 0) {
        $groupId = $existing[0].id
        Write-Warn "$($ring.Name) already exists (ID: $groupId)"
        $groupIds[$ring.Nickname] = $groupId
    } else {
        $newGroup = az ad group create `
            --display-name $ring.Name `
            --mail-nickname $ring.Nickname `
            --description $ring.Description `
            --output json | ConvertFrom-Json
        $groupId = $newGroup.id
        Write-OK "Created: $($ring.Name) (ID: $groupId)"
        $groupIds[$ring.Nickname] = $groupId
    }
}

# ------------------------------------------------------------------------------
# STEP 7: OUTPUT RESULTS
# ------------------------------------------------------------------------------

Write-Step "7/7  Configuration complete"

Write-Host ""
Write-Host "+--------------------------------------------------+" -ForegroundColor Green
Write-Host "|          Azure Setup Complete                    |" -ForegroundColor Green
Write-Host "+--------------------------------------------------+" -ForegroundColor Green
Write-Host ""
Write-Host "  Your credentials:" -ForegroundColor White
Write-Host "  AZURE_TENANT_ID    = $TenantId" -ForegroundColor Cyan
Write-Host "  AZURE_CLIENT_ID    = $ClientId" -ForegroundColor Cyan
Write-Host "  AZURE_CLIENT_SECRET= $ClientSecret" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Deployment ring group IDs:" -ForegroundColor White
Write-Host "  RING0_GROUP_ID     = $($groupIds['AutoPackager-Ring0'])" -ForegroundColor Cyan
Write-Host "  RING1_GROUP_ID     = $($groupIds['AutoPackager-Ring1'])" -ForegroundColor Cyan
Write-Host "  RING2_GROUP_ID     = $($groupIds['AutoPackager-Ring2'])" -ForegroundColor Cyan
Write-Host "  RING3_GROUP_ID     = $($groupIds['AutoPackager-Ring3'])" -ForegroundColor Cyan
Write-Host ""

# Write .env file
if ($OutputEnvFile) {
    $envContent = @"
# AutoPackager Environment Variables
# Generated by azure-setup.ps1 on $(Get-Date -Format "yyyy-MM-dd HH:mm:ss")

# Database (SQLite for testing)
DB_PASSWORD=

# Azure / Intune
AZURE_TENANT_ID=$TenantId
AZURE_CLIENT_ID=$ClientId
AZURE_CLIENT_SECRET=$ClientSecret

# Deployment Ring Group IDs (Entra ID)
RING0_GROUP_ID=$($groupIds['AutoPackager-Ring0'])
RING1_GROUP_ID=$($groupIds['AutoPackager-Ring1'])
RING2_GROUP_ID=$($groupIds['AutoPackager-Ring2'])
RING3_GROUP_ID=$($groupIds['AutoPackager-Ring3'])

# LLM API Key (get from https://platform.openai.com or https://console.anthropic.com)
LLM_API_KEY=your_llm_api_key_here
"@

    $envContent | Out-File -FilePath $EnvFilePath -Encoding UTF8 -Force
    Write-OK ".env file written to: $(Resolve-Path $EnvFilePath)"
    Write-Warn "ACTION REQUIRED: Edit .env and set LLM_API_KEY"
}

# Return values as object (useful when called from Install-AutoPackager.ps1)
return @{
    TenantId    = $TenantId
    ClientId    = $ClientId
    ClientSecret = $ClientSecret
    Ring0GroupId = $groupIds['AutoPackager-Ring0']
    Ring1GroupId = $groupIds['AutoPackager-Ring1']
    Ring2GroupId = $groupIds['AutoPackager-Ring2']
    Ring3GroupId = $groupIds['AutoPackager-Ring3']
}
