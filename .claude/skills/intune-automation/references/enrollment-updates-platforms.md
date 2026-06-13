# Enrollment, Updates & Platforms

Windows Autopilot/ESP/updates plus the Android/iOS/macOS enrollment and app spec
that differ from the Windows path.

## Windows Autopilot

- **Deployment profiles** — `POST deviceManagement/windowsAutopilotDeploymentProfiles`.
  Note: assign via the **`/assignments`** sub-collection
  (`.../windowsAutopilotDeploymentProfiles/{id}/assignments`), not `/assign`, and the
  assignment uses an `assignments` collection of targets.
- **Device identities** — `deviceManagement/windowsAutopilotDeviceIdentities` holds
  registered hardware hashes. POST to register a device hash; GET to inventory.
- **Autopilot events** — `deviceManagement/autopilotEvents?...` for deployment
  telemetry (success/failure of OOBE runs).

## Enrollment Status Page (ESP) & enrollment configs

`deviceManagement/deviceEnrollmentConfigurations` is a shared collection for ESP,
enrollment restrictions, and related enrollment-time settings. Because it's shared:
- Different config kinds are discriminated by their `@odata.type`.
- Items have a **priority** ordering — when you create one you may need to set/adjust
  its priority relative to existing configs.
- Update a specific config with `PATCH .../deviceEnrollmentConfigurations/{id}` and
  assign with `.../{id}/assign`.

**Windows Hello for Business** enrollment config uses
`#microsoft.graph.deviceEnrollmentWindowsHelloForBusinessConfiguration` in this same
collection.

## Windows updates

- **Feature updates** (pin/target a Windows version) —
  `POST deviceManagement/windowsFeatureUpdateProfiles`, assign via
  `.../{id}/assign`.
- **Driver updates** —
  `POST deviceManagement/windowsDriverUpdateProfiles`, assign via `.../{id}/assign`.
  Driver profiles can run in manual or automatic approval modes.
- **Quality update / update rings** are configured through the update-policy
  surface the same way (create policy → assign). Pattern is identical: build JSON,
  POST, capture id, assign.

## Android

- Enroll via the relevant enrollment profile/token, then deploy config and apps.
- **Compliance**: `androidDeviceOwnerCompliancePolicy` (corporate/device-owner) —
  see `compliance-and-security.md`.
- Android management often pairs with a **Conditional Access** policy
  (`v1.0/identity/conditionalAccess/...`) to require compliance/approved apps before
  granting access. CA is on **v1.0**, not beta.

## iOS

- **Compliance**: `iosCompliancePolicy`.
- **Apple VPP**: tokens live at `deviceAppManagement/vppTokens`; VPP apps are
  `mobileApps` assigned with VPP-specific assignment settings. iOS app deployment
  via VPP requires a valid token synced first.
- App configuration policies: `deviceAppManagement/mobileAppConfigurations`.

## macOS

- **Enrollment** (Apple ADE/DEP): `#microsoft.graph.depMacOSEnrollmentProfile`.
- **Shell scripts**: `deviceManagement/deviceShellScripts` (see
  `scripting-and-remediations.md`).
- **Custom profiles** (.mobileconfig): `macOSCustomConfiguration` via
  `deviceConfigurations`.
- **First-party apps**: `macOSOfficeSuiteApp`, `macOSMicrosoftEdgeApp`,
  `macOSMicrosoftDefenderApp` — `mobileApps` objects you POST and assign.
- **Compliance**: `macOSCompliancePolicy`.

## Pattern reminder

Every item above is still the same loop: build JSON with the right `@odata.type` →
POST to the collection → capture `id` → assign. The only real variations are
(a) Autopilot's `/assignments` sub-collection, and (b) CA living on v1.0. When in
doubt, GET the existing objects in the collection to see the exact shape Intune
expects before you POST a new one.
