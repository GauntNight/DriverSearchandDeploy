# Apps & Win32 Packaging

The hardest automation in Intune, because uploading a Win32 app is a multi-step
async dance with Azure Storage — not a single POST. This reference gives you the
flow, the detection-rule shapes, and the simpler app types.

## App types and where they live

Everything is a `deviceAppManagement/mobileApps` object discriminated by
`@odata.type`. The common ones:
- **Win32** (`win32LobApp`) — your primary deployment method. Wrap EXE/MSI in
  `.intunewin`. MSI line-of-business apps are best wrapped as Win32 too.
- **Microsoft Store** (new Store integration) — minimal steps, no packaging.
- **MSIX** — for capturing/repackaging legacy installers into MSIX.
- **macOS first-party** — `macOSOfficeSuiteApp`, `macOSMicrosoftEdgeApp`,
  `macOSMicrosoftDefenderApp`, plus VPP apps.

## Packaging into `.intunewin`

Use Microsoft's content-prep tool, `IntuneWinAppUtil.exe`
(`https://github.com/microsoft/Microsoft-Win32-Content-Prep-Tool`). It zips +
encrypts the source folder and emits a `detection.xml` (inside the `.intunewin`,
which is itself a zip) carrying the filename, sizes, and the **encryption keys** you
must later hand back to Graph at commit time.

Good practice baked into the cookbook's approach:
- Keep source tidy: one folder per app with the installer + your install /
  uninstall / detection PowerShell scripts, then point the tool's setup file at
  your install script (`install<appid>.ps1`).
- Drive everything through PowerShell wrappers so install/uninstall/detection logic
  is reproducible and parameterized rather than hand-entered in the GUI.
- Install commands typically invoke your wrapped script:
  `powershell.exe -ExecutionPolicy Bypass -File install<appid>.ps1`.

## The Win32 upload flow (the part that bites)

Creating a Win32 app creates an **app stub**, then uploads the encrypted content to
**Azure Storage**, then links them. You cannot skip or reorder these. Sequence:

1. **Create the app stub** — `POST deviceAppManagement/mobileApps` with a
   `win32LobApp` body containing display info, `installCommandLine`,
   `uninstallCommandLine`, `installExperience` (runAsAccount system/user), the
   `rules`/`detectionRules` array, and `returnCodes`. The GUI will show "not ready"
   while content uploads. Capture the app `id`.
2. **Read `detection.xml`** from inside the `.intunewin` to get the unencrypted +
   encrypted file sizes and the `encryptionInfo` (keys, IV, MAC).
3. **Create a content version** —
   `POST mobileApps/{id}/microsoft.graph.win32LobApp/contentVersions`. Capture its id.
4. **Create the file entry** —
   `POST .../contentVersions/{cvId}/files` with the `name`, `size`, and
   `sizeEncrypted`. Capture the file id.
5. **Poll the file** — `GET .../files/{fileId}` until
   `uploadState` reports the Azure Storage SAS URI is ready
   (`azureStorageUriRequestSuccess`). The SAS URI arrives as `azureStorageUri`.
   **Do not proceed until this succeeds** — it is genuinely slow.
6. **Upload to Azure Storage** — PUT the encrypted bytes to `azureStorageUri` in
   **blocks** (block-list upload), not one shot, for anything non-trivial in size.
7. **Commit the file** — `POST .../files/{fileId}/commit` with the `encryptionInfo`
   you pulled from `detection.xml`. Poll again until `commitFileSuccess`.
8. **Point the app at the committed version** — `PATCH mobileApps/{id}` setting
   `committedContentVersion` to `{cvId}`.
9. **Assign** (see "Assignment intents" below).

> The cookbook ships a working reference implementation of this exact flow:
> `Microsoft-Intune-Cookbook-Second-Edition/blob/main/Chapter-11/create-deploy-win32.ps1`.
> Its helper functions map to the steps above: `new-win32app` (orchestrator),
> `new-detectionrule`, `Get-DefaultReturnCodes`, `Invoke-UploadWin32Lob`,
> `Get-IntuneWinXML` (reads detection.xml), `Get-Win32AppBody`,
> `Start-WaitForFileProcessing` (the poll loop), `UploadFileToAzureStorage`, and
> `grant-win32app` (assignment). When implementing, mirror that structure — it has
> already solved the polling/timing pitfalls. Build in waits/retries around steps
> 5 and 7; both are eventually-consistent.

## Detection rules

The app's `rules` (or legacy `detectionRules`) array decides whether the app is
already installed. Common shapes:

- **PowerShell** (`win32LobAppPowerShellScriptRule`) — base64 (UTF-16!) the script;
  set `ruleType: "detection"`. **`runAsAccount` MUST be `null` for a detection
  rule** — any other value fails with *"The RunAsAccount property may not be set for
  Win32LobAppPowerShellScriptRule instances used for app detection."* A simple
  `Test-Path` returning exit 0 (found) / 1 (not found) is the canonical pattern.
- **File system** (`win32LobAppFileSystemRule`) — check a path/file exists or
  matches a version.
- **MSI product code** (`win32LobAppProductCodeRule`) — for MSI-based installs.
- **Registry** (`win32LobAppRegistryRule`) — check a key/value.

```json
"rules": [
  {
    "@odata.type": "#microsoft.graph.win32LobAppPowerShellScriptRule",
    "ruleType": "detection",
    "runAsAccount": null,
    "runAs32Bit": false,
    "enforceSignatureCheck": false,
    "operationType": "notConfigured",
    "operator": "notConfigured",
    "comparisonValue": null,
    "displayName": null,
    "scriptContent": "$base64script"
  }
]
```

(The same `win32LobAppPowerShellScriptRule` type, with `ruleType: "requirement"`
and `runAsAccount` set, is used for **requirement** scripts — see
`scripting-and-remediations.md`.)

## Microsoft Store apps

The modern Store integration is short: `POST mobileApps` with the Store app type and
the package identifier, then assign. No content upload, no `.intunewin`. To update
or remove later, `PATCH`/`DELETE` `mobileApps/{id}`.

## MSIX

MSIX packaging captures changes an installer makes and produces an MSIX you then
deploy through `mobileApps`. Useful for per-user contexts and legacy apps that don't
behave as clean Win32 silent installs.

## Supersedence & dependencies

- **Supersedence** — when a new version releases, mark the new app as superseding
  the old one so Intune upgrades (or uninstalls-then-installs) cleanly instead of
  leaving both. Configured via the app's supersedence relationship.
- **Dependencies** — Intune/Autopilot can't sequence apps the way ConfigMgr can; a
  dependency relationship is the supported way to force a prerequisite (runtime,
  framework, or another app) to install first.

## Assignment intents

App assignments use `mobileAppAssignment` and carry an **intent**, unlike policies:
- `required` — install automatically.
- `available` — show in Company Portal for user-initiated install.
- `uninstall` — actively remove.

Target with the same `@odata.type` values as everywhere else
(`groupAssignmentTarget`, etc.). A common pattern (used in the reference script) is
separate **Install** and **Uninstall** groups so membership drives the action.
VPP/macOS assignments carry extra settings (e.g. `macOsVppAppAssignmentSettings`).
