# Scripting & Remediations

Four distinct script surfaces, all sharing the **base64-of-UTF-16** encoding rule.
Get the encoding wrong and the object deploys but the script silently won't run.

```powershell
# THE encoding for every scriptContent field below:
$base64 = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($script))
```

## 1. Windows platform scripts (run-once PowerShell)

`POST deviceManagement/deviceManagementScripts`. These run a PowerShell script on
assigned devices (once, then on re-sync per Intune's logic).

```powershell
$uri = "https://graph.microsoft.com/beta/deviceManagement/deviceManagementScripts"
$base64encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($script))
$json = @"
{
  "displayName": "$name",
  "description": "$description",
  "enforceSignatureCheck": true,
  "fileName": "$fileName.ps1",
  "runAs32Bit": false,
  "runAsAccount": "system",
  "scriptContent": "$base64encoded"
}
"@
```

- `runAsAccount`: `"system"` or `"user"`.
- `runAs32Bit`: force 32-bit PowerShell host when needed.
- Assign via `POST .../deviceManagementScripts/{id}/assign` with a normal
  `assignments` array (`groupAssignmentTarget`, etc.).

## 2. Proactive remediations (deviceHealthScripts)

`POST deviceManagement/deviceHealthScripts`. A **detection + remediation pair**:
detection runs on a schedule; if it exits non-zero, the remediation runs. Both
scripts are base64 (UTF-16).

```powershell
$uri = "https://graph.microsoft.com/beta/deviceManagement/deviceHealthScripts"
$detectionbase64encoded   = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($detectionScript))
$remediationbase64encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($remediationScript))
$json = @"
{
  "displayName": "$name",
  "description": "$description",
  "detectionScriptContent": "$detectionbase64encoded",
  "remediationScriptContent": "$remediationbase64encoded",
  "enforceSignatureCheck": false,
  "runAs32Bit": $runas32,
  "runAsAccount": "$runas"
}
"@
```

**Scheduling lives in the assignment**, not the script object, and uses a
`runSchedule` whose `@odata.type` switches by cadence:

```powershell
# Daily:
$schedule = @"
"runSchedule": {
  "@odata.type": "#microsoft.graph.deviceHealthScriptDailySchedule",
  "interval": $scheduleFrequency,
  "time": "$startTime",
  "useUtc": false
},
"@
# Hourly:
# "runSchedule": { "@odata.type": "#microsoft.graph.deviceHealthScriptHourlySchedule", "interval": $interval },
```

The assignment uses a **remediation-specific wrapper**, not the generic
`assignments` key:

```powershell
$assignUri  = "https://graph.microsoft.com/beta/deviceManagement/deviceHealthScripts/$scriptid/assign"
$assignJson = @"
{
  "deviceHealthScriptAssignments": [
    {
      "runRemediationScript": true,
      $schedule
      "target": {
        "@odata.type": "#microsoft.graph.groupAssignmentTarget",
        "groupId": "$groupid"
      }
    }
  ]
}
"@
```

## 3. Win32 app detection scripts

A detection rule on a Win32 app (lives on the `mobileApps` object, not a script
endpoint). Type `win32LobAppPowerShellScriptRule`, `ruleType: "detection"`.
**`runAsAccount` MUST be `null`** for detection (see
`apps-and-win32-packaging.md` for the full rule shape and the exact error you get
if you set it). Detection scripts signal "installed" by exit code / stdout, not by
returning data.

## 4. Win32 app requirement scripts

Same `win32LobAppPowerShellScriptRule` type, but `ruleType: "requirement"`. Here
`runAsAccount` **is** settable (e.g. `"system"`), and you add the output-matching
fields so Intune evaluates the script's result:

```json
{
  "@odata.type": "#microsoft.graph.win32LobAppPowerShellScriptRule",
  "ruleType": "requirement",
  "runAsAccount": "system",
  "runAs32Bit": false,
  "enforceSignatureCheck": false,
  "operationType": "<string|integer|boolean|...>",
  "operator": "<equal|greaterThan|...>",
  "comparisonValue": "<expected>",
  "displayName": "$fileName",
  "scriptContent": "$base64script"
}
```

The requirement script decides **whether the app is even applicable** before
install is attempted — distinct from detection (which decides if it's already
present).

## 5. macOS shell scripts

`POST deviceManagement/deviceShellScripts`. macOS equivalent of platform scripts —
a shell script (base64) run on assigned Macs, with execution-frequency and
retry settings. Assign with the standard `assignments` array. Custom macOS
configuration profiles (`.mobileconfig`) go through `macOSCustomConfiguration`
instead (see `configuration-profiles.md`).

## Quick chooser

| You want to… | Use |
|--------------|-----|
| Run a script once to configure something | Platform script (`deviceManagementScripts`) |
| Continuously detect + auto-fix drift | Proactive remediation (`deviceHealthScripts`) |
| Decide if a Win32 app is already installed | Win32 **detection** rule (`runAsAccount: null`) |
| Gate whether a Win32 app should install at all | Win32 **requirement** rule |
| Run a script on Macs | `deviceShellScripts` |
