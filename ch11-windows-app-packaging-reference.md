# Windows Application Packaging & Deployment via Intune Graph API

> Reference extracted from *Microsoft Intune Cookbook, 2nd Edition* (Andrew Taylor, Packt, Feb 2026), Chapter 11.
> Structured for use with AutoPackager and Claude Code scripting workflows.
> GitHub companion: https://github.com/PacktPublishing/Microsoft-Intune-Cookbook-Second-Edition/tree/main/Chapter-11

---

## 1. Application Types & When to Use Each

### Microsoft Store Apps (WinGet)
- **Graph entity**: `#microsoft.graph.winGetApp`
- **Use when**: App exists in the Microsoft Store; you want automatic updates even if Store is blocked by policy.
- **Install context**: User or System (set via `installExperience.runAsAccount`).
- **Key gotcha**: Intune's store search filters by child-safe ratings. If an app doesn't appear, search by its Store App ID instead of name.
- **Updates**: Handled automatically by the Store; no manual update management required.

### MSIX / Line-of-Business
- **Graph entity**: Line-of-business app upload.
- **Use when**: You need user-level containerized installs, AppAttach for AVD, or clean uninstall/revert.
- **Prerequisites**: Code-signing certificate (public or self-signed + deployed via Intune), clean packaging VM with snapshots.
- **Key gotcha**: MSIX can be reverted by end users — never package apps with embedded databases. Client-server apps are fine.
- **Timestamp server**: Always specify one (e.g., `https://ca.signfiles.com/TSAServer.aspx`) so packages remain valid after certificate expiry.

### Win32 (.intunewin) — PRIMARY METHOD
- **Graph entity**: `#microsoft.graph.win32LobApp`
- **Use when**: Default choice for all standard application deployments. Always wrap MSI into Win32.
- **Why not raw MSI**: MSI uses `msiexec` service; Win32 uses the Intune Management Extension (IME). They're unaware of each other, causing Autopilot clashes. Win32 also gives requirements, detection, supersedence, and dependencies — none available with MSI LOB.
- **The intunewin format**: Encrypted ZIP uploaded with manifest to Azure Blob storage. Device downloads, decrypts, and executes.

### Microsoft 365 Apps (Office)
- **Deployment method**: Wrap as Win32 using Office Deployment Tool (ODT), NOT the built-in M365 app type.
- **Why**: Built-in M365 deploys as policy-like, can clash with running installations, loses detection/requirements/supersedence/dependencies. Win32 via ODT uses IME, avoids conflicts, and works reliably with ESP.

---

## 2. Win32 App Lifecycle — Full Pipeline

This is the core pipeline AutoPackager needs to replicate programmatically.

### 2.1 Source Folder Structure (Recommended)
```
<AppName>/
├── Source/          # Raw installer, config files, scripts
├── Output/          # .intunewin file goes here
├── Detection/       # Detection scripts
├── Documentation/   # Notes, changelogs
└── Testing/         # Test results
```

**Critical**: IntuneWinAppUtil grabs *every file* in the Source directory. Keep it clean.

### 2.2 Installation Methods (in order of capability)

| Method | Install Command in Intune | Use When |
|--------|--------------------------|----------|
| Direct MSI/EXE | `msiexec.exe /i myinstaller.msi /qn` or `myinstaller.exe /silent` | Simple installs, no pre/post customization needed |
| Batch script | `install.bat` | Comfortable with batch; need pre/post steps |
| PowerShell script | `powershell.exe -ExecutionPolicy Bypass -file myinstaller.ps1` | Need logic, hardware checks, conditional installs |
| PSADT v3 | `.\\ServiceUI.exe -Process:explorer.exe Deploy-Application.exe` | Need user interaction, pre/post commands, running app checks |
| PSADT v4 | `%SystemRoot%\\System32\\WindowsPowerShell\\v1.0\\PowerShell.exe -ExecutionPolicy Bypass -NoProfile -File Invoke-AppDeployToolkit.ps1` | Same as v3; ServiceUI built-in |

**64-bit note**: Calling `powershell.exe` defaults to 32-bit. For native 64-bit, use the full path `%SystemRoot%\\System32\\WindowsPowerShell\\v1.0\\PowerShell.exe` or use `sysnative`.

**New capability**: Win32 apps now support selecting a PowerShell script separately from the intunewin file, allowing script changes without full repackage.

### 2.3 Install Context

#### System Context
- Runs as SYSTEM user (testable via `psexec.exe -I -s cmd.exe`)
- Full admin permissions, no user profile access
- Paths: Temp → `C:\Windows\Temp`, AppData → `C:\WINDOWS\system32\config\systemprofile\AppData\...`, User profile → `C:\users\Public`
- Use `serviceui.exe` for user-level interaction from system context
- **Best for**: Standard Win32 apps that traditionally need elevation

#### User Context
- Runs as logged-in user; no elevation
- Paths: Standard user profile paths (`C:\users\username\...`)
- **Best for**: Teams, VS Code, OneDrive, MSIX/AppX, some MSI installers
- **Gotcha**: If MSI is hardcoded to user context, Intune grays out context selector. Fix by editing MSI (Orca) or wrapping with script.

**Rule**: Don't mix User and System context in the same application. Pick early.

### 2.4 Detection Methods

| Type | Best For | Example |
|------|----------|---------|
| MSI Product Code | MSI-based installs | Auto-populated GUID |
| File presence/version | EXE-based installs | Check `C:\Program Files\7-Zip\7z.exe` exists |
| Registry key/value | Complex installs | Check `HKLM\SOFTWARE\...\VersionMajor` |
| Custom PowerShell script | Anything complex | Exit 0 = detected, Exit 1 = not detected |

#### MSI Product Code Extraction Script
```powershell
$path = "PATH TO MSI"
$comObjWI = New-Object -ComObject WindowsInstaller.Installer
$MSIDatabase = $comObjWI.GetType().InvokeMember(
    "OpenDatabase", "InvokeMethod", $Null, $comObjWI, @($Path, 0))
$Query = "SELECT Value FROM Property WHERE Property = 'ProductCode'"
$View = $MSIDatabase.GetType().InvokeMember(
    "OpenView", "InvokeMethod", $null, $MSIDatabase, ($Query))
$View.GetType().InvokeMember("Execute", "InvokeMethod", $null, $View, $null)
$Record = $View.GetType().InvokeMember(
    "Fetch", "InvokeMethod", $null, $View, $null)
$Value = $Record.GetType().InvokeMember(
    "StringData", "GetProperty", $null, $Record, 1)
Write-Host "MSI code: $Value" -ForegroundColor Green
```

#### Custom Detection Script Pattern
```powershell
# File-based detection
if (Test-Path "C:\Program Files\MyApp\app.exe") {
    Write-Output "Detected"
    exit 0
} else {
    exit 1
}
```

```powershell
# Service-based detection
$service = Get-Service -Name "MozillaMaintenance"
if ($service.Status -eq "Running") {
    Write-Output "Detected and running"
    exit 0
} else {
    exit 1
}
```

### 2.5 Supersedence & Dependencies

#### Supersedence
- **Purpose**: Replace old app version with new. Can uninstall old first or update in-place.
- **Advantage over manual**: No duplicate assignments, no monitoring multiple apps.
- **Can be configured**: During initial deployment or after (post-deployment allows testing first).

#### Dependencies
- **Purpose**: Ensure prerequisites (.NET, Java, VC++ Redistributables) are installed before primary app.
- **Behavior**: Intune checks if dependency is installed; if not, installs it first, then deploys primary app.
- **Solves**: Intune/Autopilot's inability to sequence apps like SCCM.

---

## 3. Graph API Reference — Application Endpoints

### 3.1 Required Scopes
```
DeviceManagementApps.ReadWrite.All
```
(Part of the full scope set from Chapter 1 connection setup.)

### 3.2 Core Endpoints

| Operation | Method | Endpoint |
|-----------|--------|----------|
| Create app | POST | `https://graph.microsoft.com/beta/deviceAppManagement/mobileApps` |
| Assign app | POST | `https://graph.microsoft.com/Beta/deviceAppManagement/mobileApps/{appId}/assign` |
| List apps | GET | `https://graph.microsoft.com/beta/deviceAppManagement/mobileApps` |
| Create content version | POST | `https://graph.microsoft.com/beta/deviceAppManagement/mobileApps/{appId}/microsoft.graph.win32LobApp/contentVersions` |
| Create file reference | POST | `.../contentVersions/{versionId}/files` |
| Commit file | POST | `.../contentVersions/{versionId}/files/{fileId}/commit` |
| Enable MAM connector | POST | `https://graph.microsoft.com/beta/deviceManagement/mobileThreatDefenseConnectors` |
| Create config policy | POST | `https://graph.microsoft.com/beta/deviceManagement/configurationPolicies` |
| Assign config policy | POST | `https://graph.microsoft.com/beta/deviceManagement/configurationPolicies/{policyId}/assign` |
| Create CA policy | POST | `https://graph.microsoft.com/v1.0/identity/conditionalAccess/policies` |

### 3.3 Microsoft Store Search (External, not Graph)

| Operation | Method | Endpoint |
|-----------|--------|----------|
| Search store | POST | `https://storeedgefd.dsx.mp.microsoft.com/v9.0/manifestSearch` |
| Get package manifest | GET | `https://storeedgefd.dsx.mp.microsoft.com/v9.0/packageManifests/{PackageIdentifier}` |
| Get product details/image | GET | `https://storeedgefd.dsx.mp.microsoft.com/v9.0/products/{appId}?market=US&locale=en-US&deviceFamily=Windows.Desktop` |

---

## 4. JSON Templates

### 4.1 WinGet (Store) App Creation
```json
{
    "@odata.type": "#microsoft.graph.winGetApp",
    "categories": [],
    "description": "$description",
    "developer": "$developer",
    "displayName": "$displayName",
    "informationUrl": "$informationUrl",
    "installExperience": {
        "runAsAccount": "$scope"
    },
    "isFeatured": false,
    "largeIcon": {
        "@odata.type": "#microsoft.graph.mimeContent",
        "type": "string",
        "value": "$base64ImageString"
    },
    "notes": "",
    "owner": "",
    "packageIdentifier": "$packageIdentifier",
    "privacyInformationUrl": "$privacyUrl",
    "publisher": "$publisher",
    "repositoryType": "microsoftStore",
    "roleScopeTagIds": []
}
```

### 4.2 App Assignment (Generic — works for Store, Win32, etc.)
```json
{
    "mobileAppAssignments": [
        {
            "@odata.type": "#microsoft.graph.mobileAppAssignment",
            "intent": "Required",
            "settings": {
                "@odata.type": "#microsoft.graph.winGetAppAssignmentSettings",
                "installTimeSettings": null,
                "notifications": "showAll",
                "restartSettings": null
            },
            "target": {
                "@odata.type": "#microsoft.graph.groupAssignmentTarget",
                "groupId": "$groupId"
            }
        }
    ]
}
```
**Intent values**: `Required`, `Available` (Available for enrolled devices), `Uninstall`

### 4.3 Group Creation (for Install/Uninstall groups)
```json
{
    "displayName": "App-$appId-$groupType",
    "description": "$groupType group for $appName",
    "mailEnabled": false,
    "mailNickname": "App-$appId-$groupType",
    "securityEnabled": true
}
```

### 4.4 Office Update Policy (Settings Catalog)

**Channel values** (display name → API value):
| Display Name | API Value |
|-------------|-----------|
| Current | `current` |
| Monthly Enterprise | `monthlyenterprise` |
| Semi-Annual | `deferred` |
| Semi-Annual (Preview) | `firstreleasedeferred` |
| Current Preview | `firstreleasecurrent` |
| Beta | `insiderfast` |

Policy created via `POST /beta/deviceManagement/configurationPolicies` with settings catalog payload containing:
- `Enable Automatic Updates` → Enabled
- `Update Channel` → channel value from table above

### 4.5 Windows MAM Connector Enable
```json
{
    "windowsMobileApplicationManagementEnabled": true
}
```

---

## 5. Win32 Upload Pipeline — Programmatic Flow

This is the complex multi-step process that AutoPackager must implement. The book references a full script at `Chapter-11/create-deploy-win32.ps1` in the companion repo.

### Step-by-step orchestration:

```
1. Set variables (app name, ID, download URL, detection path, install/uninstall strings)
2. Download IntuneWinAppUtil.exe
3. Create temp directory structure
4. Download installer to Source folder
5. Create Entra ID groups (Install + Uninstall) via Graph POST
6. Generate install script → write to .ps1 file
7. Generate uninstall script → write to .ps1 file
8. Generate detection script → write to .ps1 file
9. Run IntuneWinAppUtil to create .intunewin from source
10. Wait for packaging to complete (polling loop)
```

### Upload to Intune (nested function chain):

```
11. new-win32app() orchestrates:
    a. new-detectionrule()        → Build detection JSON (PowerShell/file/registry/MSI)
    b. GetDefaultReturnCodes()    → Get standard return code definitions
    c. Invoke-UploadWin32Lob()    → Main upload function:
        i.   Test-SourceFile()        → Validate .intunewin exists
        ii.  Get-IntuneWinXML()       → Read detection.xml from .intunewin
        iii. Get-Win32AppBody()       → Build app JSON (MSI auto-populates; EXE uses passed values)
        iv.  Add detection rules + return codes to JSON
        v.   POST to Graph → Create app stub (shows "not ready" in UI)
        vi.  Extract encryption keys from detection.xml
        vii. Get-IntuneWinFile()      → Detect file size
        viii.POST file reference to Graph
        ix.  Start-WaitForFileProcessing() → Poll until ready
        x.   UploadFileToAzureStorage()    → Upload to Azure Blob via Graph-provided URL
        xi.  Remove local .intunewin
        xii. POST commit to Graph
        xiii.Start-WaitForFileProcessing() → Poll until committed
        xiv. Sleep for processing
```

### Assign:

```
12. grant-win32app() → GET app ID, POST assignment JSON with group IDs
```

### Key functions to implement in AutoPackager:
- `new-detectionrule` — Builds detection JSON for PowerShell, file, registry, or MSI types
- `GetDefaultReturnCodes` — Standard Win32 return code definitions
- `Invoke-UploadWin32Lob` — The full upload orchestration
- `Test-SourceFile` — Validate intunewin file
- `Get-IntuneWinXML` — Parse the detection.xml inside intunewin
- `Get-Win32AppBody` — Build the Graph API request body
- `UploadFileToAzureStorage` — Azure Blob upload using SAS URL from Graph
- `Start-WaitForFileProcessing` — Polling loop for async operations
- `grant-win32app` — Assignment function

---

## 6. Office Deployment as Win32 — Specific Pattern

### Build Steps:
1. Configure XML via Office Customization Tool (`https://config.office.com/deploymentsettings`)
2. Download ODT from Microsoft
3. Place `setup.exe` + `Configuration.xml` + `uninstall.xml` in Source folder
4. Package with IntuneWinAppUtil (setup.exe as installer)
5. Upload to Intune as Win32

### Install/Uninstall Commands:
```
Install:   setup.exe /configure Configuration.xml
Uninstall: setup.exe /configure uninstall.xml
```

### Uninstall XML Template:
```xml
<Configuration>
    <Display Level="None" AcceptEULA="True" />
    <Property Name="FORCEAPPSHUTDOWN" Value="True" />
    <Remove>
        <Product ID="O365ProPlusRetail">
        </Product>
    </Remove>
</Configuration>
```

### Detection:
- **Registry key**: `HKLM\SOFTWARE\Microsoft\Office\ClickToRun`
- **Value**: `LastScenarioResult`
- **Method**: Exists
- **32-bit association**: No (set to No to avoid WOW6432Node lookup)

### Deployment Considerations:
- Set update channel to match your Broad ring to avoid post-install downgrade
- Set `Show installation to user` → No (system context)
- Set `Shut Down running applications` → Yes (for Autopilot); consider PSADT prompting for post-enrollment
- Watch `Uninstall any MSI versions of Office` — will remove Visio/Project if deploying individually

---

## 7. Windows App Protection (MAM) — Conditional Access Pattern

### Two-policy architecture for personal devices:

**Policy 1: Block non-browser access on unmanaged Windows devices**
- Targets: All users, All cloud apps
- Platform: Windows only
- Client apps: Everything EXCEPT Browser
- Filter: Exclude `device.deviceOwnership -eq "Company"`
- Grant: Require device to be marked as compliant (auto-blocks unmanaged)

**Policy 2: Require app protection for browser on unmanaged Windows devices**
- Targets: All users, All cloud apps
- Platform: Windows only
- Client apps: Browser only
- Filter: Exclude `device.deviceOwnership -eq "Company"`
- Grant: Require app protection policy
- Session (optional): Block downloads via Conditional Access App Control

### Prerequisites:
- Enable Windows Security Center connector (`mobileThreatDefenseConnectors`)
- Personal devices must NOT be allowed to enroll in Intune (configured in Tenant Administration)
- Currently only supports Microsoft Edge

---

## 8. Assignment Best Practices

### General Pattern:
- **Required**: Force install; appears under "Installed Applications" in Company Portal only.
- **Available for enrolled devices**: Self-service via Company Portal.
- **Uninstall**: Removes the application.

### Recommended approach:
Always create dedicated Entra ID groups for both Install and Uninstall, even for broadly deployed apps. This gives flexibility to quickly remove an app without reconfiguring assignments.

### Group naming convention (from automation scripts):
```
App-{AppId}-Install
App-{AppId}-Uninstall
```

### Assignment extras:
- Installation deadline
- Grace period for restarts
- Show/suppress install notifications
- Allow available uninstall (user self-service removal in Company Portal)

---

## 9. Companion Scripts Reference

All scripts available at: `https://github.com/PacktPublishing/Microsoft-Intune-Cookbook-Second-Edition/tree/main/Chapter-11`

| Script | Purpose |
|--------|---------|
| `create-deploy-win32.ps1` | Full Win32 packaging, upload, and assignment pipeline |
| `add-office-updatepolicy.ps1` | Office update ring via Settings Catalog + Graph |
| `windows-MAM.ps1` | Windows MAM app protection policy creation |
| `windows-mam-conditional-access.ps1` | CA policies for MAM enforcement |

---

## 10. AutoPackager Integration Notes

### What maps directly to AutoPackager's pipeline:
- The Win32 upload orchestration (Section 5) is essentially the Graph API module workstream
- Store app deployment (Section 4.1) maps to a simpler "store-aware" packaging path
- Detection method generation (Section 2.4) is a candidate for LLM-assisted automation — given an installer, infer the right detection rule type and parameters
- The `IntuneWinAppUtil` call (Step 9 of Section 5) is the `.intunewin` creation step that AutoPackager's core pipeline must wrap

### Where AutoPackager adds value beyond this book:
- **Automated detection inference**: Given an installer, determine file paths, registry keys, or MSI product codes without manual inspection
- **Silent install string discovery**: Analyze installer type and generate install/uninstall commands
- **Dependency resolution**: Automatically identify required runtimes and create dependency chains
- **Supersedence management**: Track app versions and auto-configure supersedence when uploading updates
- **Scale**: This book's patterns work for single-app workflows. AutoPackager needs to handle batch operations across hundreds of apps with CI/CD integration

### Graph API patterns to reuse:
- All `Invoke-MgGraphRequest` patterns are directly portable
- The Azure Blob upload flow (SAS URL from Graph → chunked upload) is the most complex piece and is well-documented in the companion script
- Assignment JSON is generic across app types with minor `@odata.type` variations in settings
