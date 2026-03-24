# Windows Driver Updates Management via Intune Graph API

> Reference extracted from *Microsoft Intune Cookbook, 2nd Edition* (Andrew Taylor, Packt, Feb 2026), Chapter 4.
> Structured for use with Claude Code scripting workflows against production Intune tenants.

---

## 1. Driver Update Management — Two Mechanisms

Intune provides two distinct mechanisms for controlling driver updates on managed Windows devices. Understanding when each applies is important — they serve different purposes and use different Graph API endpoints.

### Mechanism A: Update Ring Driver Toggle

The **Windows Update for Business** update ring includes a binary driver setting that controls whether Windows Update can search for and install drivers on the device.

- **Setting**: `driversExcluded` (boolean)
- **`false`** = Allow driver updates via Windows Update (default/recommended)
- **`true`** = Block driver updates via Windows Update
- **Use case**: If you prefer vendor-specific driver management tools (Dell Command Update, HP SCCM driver packs, Lenovo System Update), set this to `true` to prevent Windows Update from overriding vendor-managed drivers.

**Double-negative warning**: The property is `driversExcluded`, so `false` means drivers ARE included. Taylor specifically calls this out as a common source of confusion in the automation section.

### Mechanism B: Driver Update Profiles (Dedicated)

Dedicated driver update profiles provide **granular per-driver approval control** — Intune inventories your estate, surfaces available driver updates, and lets you approve or auto-approve them individually.

- **Prerequisite**: Telemetry must be shared with Microsoft. If telemetry is blocked in your tenant, driver updates won't surface.
- **Approval modes**: Manual (review each driver) or Automatic (approve after N days deferral). **This setting is immutable after creation** — you must delete and recreate the profile to change it.
- **Population**: After profile creation, Intune takes 1-2 days to inventory devices and populate available driver updates.
- **Assignment**: Device-based groups only (not user groups). Assign to device groups until filters are available.

---

## 2. Graph API Reference — Driver Update Endpoints

### 2.1 Driver Update Profile Endpoints

| Operation | Method | Endpoint |
|-----------|--------|----------|
| Create driver update profile | POST | `https://graph.microsoft.com/beta/deviceManagement/windowsDriverUpdateProfiles` |
| Assign driver update profile | POST | `https://graph.microsoft.com/beta/deviceManagement/windowsDriverUpdateProfiles/{profileId}/assign` |
| List driver update profiles | GET | `https://graph.microsoft.com/beta/deviceManagement/windowsDriverUpdateProfiles` |

### 2.2 Update Ring Endpoints (driver toggle lives here)

| Operation | Method | Endpoint |
|-----------|--------|----------|
| Create update ring | POST | `https://graph.microsoft.com/beta/deviceManagement/deviceConfigurations` |
| Assign update ring | POST | `https://graph.microsoft.com/beta/deviceManagement/deviceConfigurations/{policyId}/assign` |

### 2.3 Feature Update Endpoints (for context — related pipeline)

| Operation | Method | Endpoint |
|-----------|--------|----------|
| List available feature updates | GET | `https://graph.microsoft.com/beta/deviceManagement/windowsUpdateCatalogItems/microsoft.graph.windowsFeatureUpdateCatalogItem` |
| Create feature update profile | POST | `https://graph.microsoft.com/beta/deviceManagement/windowsFeatureUpdateProfiles` |
| Assign feature update profile | POST | `https://graph.microsoft.com/beta/deviceManagement/windowsFeatureUpdateProfiles/{profileId}/assign` |

### 2.4 Required Scopes

```
DeviceManagementConfiguration.ReadWrite.All
```

---

## 3. JSON Templates

### 3.1 Driver Update Profile — Manual Approval

```json
{
    "approvalType": "manual",
    "description": "$description",
    "displayName": "$name",
    "roleScopeTagIds": [
        "0"
    ]
}
```

### 3.2 Driver Update Profile — Automatic Approval

```json
{
    "approvalType": "automatic",
    "deploymentDeferralInDays": 3,
    "description": "$description",
    "displayName": "$name",
    "roleScopeTagIds": [
        "0"
    ]
}
```

**Note**: `deploymentDeferralInDays` is only valid when `approvalType` is `automatic`. It sets how many days before drivers are auto-approved.

### 3.3 Driver Update Profile — Assignment

```json
{
    "assignments": [
        {
            "target": {
                "@odata.type": "#microsoft.graph.groupAssignmentTarget",
                "groupId": "$groupId"
            }
        }
    ]
}
```

### 3.4 Update Ring (Full — with driver toggle highlighted)

The `driversExcluded` field is the driver toggle within an update ring. Full update ring JSON for reference:

```json
{
    "@odata.type": "#microsoft.graph.windowsUpdateForBusinessConfiguration",
    "allowWindows11Upgrade": true,
    "automaticUpdateMode": "autoInstallAtMaintenanceTime",
    "autoRestartNotificationDismissal": "notConfigured",
    "businessReadyUpdatesOnly": "userDefined",
    "deadlineForFeatureUpdatesInDays": 5,
    "deadlineForQualityUpdatesInDays": 5,
    "deadlineGracePeriodInDays": 3,
    "description": "",
    "displayName": "Windows Updates - Broad Ring",
    "driversExcluded": false,
    "engagedRestartDeadlineInDays": null,
    "engagedRestartSnoozeScheduleForFeatureUpdatesInDays": null,
    "engagedRestartSnoozeScheduleInDays": null,
    "engagedRestartTransitionScheduleForFeatureUpdatesInDays": null,
    "engagedRestartTransitionScheduleInDays": null,
    "featureUpdatesDeferralPeriodInDays": 0,
    "featureUpdatesPaused": false,
    "featureUpdatesRollbackWindowInDays": 10,
    "installationSchedule": {
        "@odata.type": "#microsoft.graph.windowsUpdateActiveHoursInstall",
        "activeHoursEnd": "17:00:00.0000000",
        "activeHoursStart": "08:00:00.0000000"
    },
    "microsoftUpdateServiceAllowed": true,
    "postponeRebootUntilAfterDeadline": false,
    "qualityUpdatesDeferralPeriodInDays": 10,
    "qualityUpdatesPaused": false,
    "roleScopeTagIds": [],
    "scheduleImminentRestartWarningInMinutes": null,
    "scheduleRestartWarningInHours": null,
    "skipChecksBeforeRestart": false,
    "updateNotificationLevel": "restartWarningsOnly",
    "updateWeeks": null,
    "userPauseAccess": "enabled",
    "userWindowsUpdateScanAccess": "enabled"
}
```

### 3.5 Update Ring — Assignment (with Include/Exclude pattern)

Update rings commonly use both included and excluded groups to implement ring-based rollout:

```json
{
    "assignments": [
        {
            "target": {
                "@odata.type": "#microsoft.graph.groupAssignmentTarget",
                "groupId": "$broadGroupId"
            }
        },
        {
            "target": {
                "@odata.type": "#microsoft.graph.exclusionGroupAssignmentTarget",
                "groupId": "$pilotGroupId"
            }
        },
        {
            "target": {
                "@odata.type": "#microsoft.graph.exclusionGroupAssignmentTarget",
                "groupId": "$previewGroupId"
            }
        },
        {
            "target": {
                "@odata.type": "#microsoft.graph.exclusionGroupAssignmentTarget",
                "groupId": "$vipGroupId"
            }
        }
    ]
}
```

**Assignment @odata.type values**:
- `#microsoft.graph.groupAssignmentTarget` = INCLUDE
- `#microsoft.graph.exclusionGroupAssignmentTarget` = EXCLUDE

### 3.6 Feature Update Profile

```json
{
    "description": "$description",
    "displayName": "$displayname",
    "featureUpdateVersion": "$selectedVersion",
    "installFeatureUpdatesOptional": false,
    "installLatestWindows10OnWindows11IneligibleDevice": true,
    "roleScopeTagIds": [],
    "rolloutSettings": {
        "offerEndDateTimeInUTC": null,
        "offerIntervalInDays": null,
        "offerStartDateTimeInUTC": null
    }
}
```

**Getting available versions programmatically**:
```powershell
$url = "https://graph.microsoft.com/beta/deviceManagement/windowsUpdateCatalogItems/microsoft.graph.windowsFeatureUpdateCatalogItem"
$available = (Invoke-MgGraphRequest -Uri $url -Method GET -OutputType PSObject).value
# Only returns versions within their support date
```

---

## 4. Complete Automation Patterns

### 4.1 Create & Assign Driver Update Profile

```powershell
# Variables
$name = "Driver Updates"
$description = "Driver Update Management"
$groupid = "00000000-0000-0000-0000-000000000000"
$driversetting = "manual"  # or "automatic"

# Create profile
$url = "https://graph.microsoft.com/beta/deviceManagement/windowsDriverUpdateProfiles"
$json = @"
{
    "approvalType": "$driversetting",
    "description": "$description",
    "displayName": "$name",
    "roleScopeTagIds": ["0"]
}
"@
# Add deferral if automatic:
# "deploymentDeferralInDays": 3,

$driverpolicy = Invoke-MgGraphRequest -Method POST -Uri $url -Body $json `
    -ContentType "application/json" -OutputType PSObject
$policyid = $driverpolicy.id

# Assign
$assignurl = "https://graph.microsoft.com/beta/deviceManagement/windowsDriverUpdateProfiles/$policyid/assign"
$assignjson = @"
{
    "assignments": [
        {
            "target": {
                "@odata.type": "#microsoft.graph.groupAssignmentTarget",
                "groupId": "$groupid"
            }
        }
    ]
}
"@
Invoke-MgGraphRequest -Method POST -Uri $assignurl -Body $assignjson `
    -ContentType "application/json" -OutputType PSObject
```

### 4.2 Create & Assign Update Ring (Broad Ring Example)

```powershell
# Group IDs for ring-based deployment
$pilotgroupid   = "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
$previewgroupid = "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
$broadgroupid   = "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
$vipgroupid     = "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"

$url = "https://graph.microsoft.com/beta/deviceManagement/deviceConfigurations"

# See Section 3.4 for full JSON payload
$broadpolicy = Invoke-MgGraphRequest -Uri $url -Method Post -Body $broadjson `
    -ContentType "application/json" -OutputType PSObject
$broadpolicyid = $broadpolicy.id

$broadassignurl = "https://graph.microsoft.com/beta/deviceManagement/deviceConfigurations/$broadpolicyid/assign"

# See Section 3.5 for assignment JSON with include/exclude pattern
Invoke-MgGraphRequest -Method POST -Uri $broadassignurl -Body $broadjsonassign `
    -ContentType "application/json"
```

---

## 5. Operational Notes

### Driver approval workflow (manual mode):
1. Create profile and assign to device groups
2. Wait 1-2 days for Intune to inventory devices and surface available drivers
3. Navigate to Devices → Windows updates → Driver Updates → select profile
4. Click "Drivers to review" link
5. Approve or pause individual drivers
6. When approving, select the date for Windows Update to deliver the driver

### Relationship between driver mechanisms:
- **Update Ring** `driversExcluded: false` allows Windows Update to push drivers broadly. This is the coarse-grained control.
- **Driver Update Profiles** provide per-driver approval on top of that. This is the fine-grained control.
- Both can coexist. A common pattern: leave `driversExcluded: false` in update rings for automatic quality/security driver patches, while using a manual Driver Update Profile for firmware and major driver version changes on specific device groups.

### Autopatch integration:
- Autopatch (requires M365 Business Premium, E3, E5, A3, A5, or F3) can manage drivers as part of its automated update service
- Driver update is one of the four policy types that enrolls a device into Autopatch (alongside update ring, quality update, and feature update)
- Autopatch can centrally pause problematic driver updates across managed devices

### Scale considerations for a 200K+ device estate:
- Manual approval doesn't scale well across a mixed fleet with dozens of OEM models. Consider automatic approval with a deferral period for non-critical driver classes, and manual approval only for firmware and GPU drivers that historically cause issues.
- Device-group targeting is essential — segment by hardware model or OEM to control driver rollout granularity.
- Telemetry must be enabled for driver updates to surface. If your estate has mixed telemetry settings, some devices will silently miss driver update profiles.

### Microsoft documentation:
- Driver updates overview: https://learn.microsoft.com/en-us/mem/intune/protect/windows-driver-updates-overview

---

## 6. Companion Scripts Reference

GitHub repo: `https://github.com/PacktPublishing/Microsoft-Intune-Cookbook-Second-Edition/tree/main/Chapter-04`

The repo includes scripts for all four update ring tiers (Pilot, Preview, Broad, VIP) with assignment JSON that demonstrates the include/exclude group pattern.
