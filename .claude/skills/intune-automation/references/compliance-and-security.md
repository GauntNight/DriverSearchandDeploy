# Compliance & Security

Compliance policies and the security-hardening surfaces (baselines, ASR, LAPS,
BitLocker, Defender). Compliance has one structural rule people forget; baselines
are just template-backed Settings Catalog policies.

## Compliance policies

POST to `deviceManagement/deviceCompliancePolicies`. Pick the platform via
`@odata.type`. **The block that everyone forgets is `scheduledActionsForRule`** —
without it, the policy create can fail or the non-compliance actions won't fire.

```powershell
$uri = "https://graph.microsoft.com/beta/deviceManagement/deviceCompliancePolicies"
$json = @"
{
  "@odata.type": "#microsoft.graph.windows10CompliancePolicy",
  "displayName": "$name",
  "description": "$description",
  "passwordRequiredType": "deviceDefault",
  "bitLockerEnabled": true,
  "secureBootEnabled": true,
  "tpmRequired": true,
  "defenderEnabled": true,
  "rtpEnabled": true,
  "antivirusRequired": true,
  "antiSpywareRequired": true,
  "activeFirewallRequired": true,
  "codeIntegrityEnabled": true,
  "signatureOutOfDate": true,
  "deviceThreatProtectionEnabled": false,
  "deviceThreatProtectionRequiredSecurityLevel": "unavailable",
  "roleScopeTagIds": [ "0" ],
  "scheduledActionsForRule": [
    {
      "ruleName": "PasswordRequired",
      "scheduledActionConfigurations": [
        { "actionType": "block",  "gracePeriodHours": 12,   "notificationMessageCCList": [], "notificationTemplateId": "" },
        { "actionType": "retire", "gracePeriodHours": 4320, "notificationMessageCCList": [], "notificationTemplateId": "" }
      ]
    }
  ]
}
"@
```

Rules of thumb:
- **Only include settings you actually want enforced.** Omitted settings are simply
  not evaluated. The example is mostly `true` because the author chose to enforce
  most things — that's a choice, not a requirement.
- `deviceThreatProtectionRequiredSecurityLevel: "unavailable"` is the safe value
  when the tenant lacks Defender for Endpoint licensing — it removes the
  threat-level check rather than failing devices over a feature you don't license.
- **Notification templates**: `notificationTemplateId` references a template id.
  Get ids via `GET deviceManagement/notificationMessageTemplates`. Build the
  template before referencing it.
- Action types include `block`, `retire`, and notification/email actions, each with
  its own `gracePeriodHours`.

Other platforms — same structure, swap the type and the platform-relevant fields:
- Android (device owner): `#microsoft.graph.androidDeviceOwnerCompliancePolicy`
- iOS: `#microsoft.graph.iosCompliancePolicy`
- macOS: `#microsoft.graph.macOSCompliancePolicy`

Assign exactly like any other policy: `POST .../deviceCompliancePolicies/{id}/assign`
with an `assignments` array (see `configuration-profiles.md`).

## Security baselines

Baselines are template-backed **Settings Catalog** policies. You create them at
`deviceManagement/configurationPolicies` (or via `deviceManagement/templates/...`)
and each setting carries a `settingInstanceTemplateReference`
(`settingInstanceTemplateId`) and `settingValueTemplateReference` that bind it to
the baseline template. Mechanically identical to a Settings Catalog policy
(`configuration-profiles.md`) — the template references are the only addition.

```json
"settingInstance": {
  "@odata.type": "#microsoft.graph.deviceManagementConfigurationChoiceSettingInstance",
  "settingDefinitionId": "...",
  "settingInstanceTemplateReference": { "settingInstanceTemplateId": "26c1a943-562d-4286-..." },
  "choiceSettingValue": {
    "@odata.type": "#microsoft.graph.deviceManagementConfigurationChoiceSettingValue",
    "value": "...",
    "settingValueTemplateReference": { "settingValueTemplateId": "..." },
    "children": []
  }
}
```

## Attack Surface Reduction (ASR), Defender hardening

ASR rules and Defender hardening are delivered as Settings Catalog policies the
same way — they're catalog settings under the Defender/ASR categories. Search the
catalog (`configurationSettings?...`) for the ASR rule ids and set each to its
enforcement mode (block / audit / warn / disabled). No special endpoint; it's a
`configurationPolicies` POST.

## Windows LAPS (Local Administrator Password Solution)

LAPS is configured as a Settings Catalog / device configuration policy that points
the local admin account backup at Entra. To **read** a backed-up local credential
programmatically, use `deviceLocalCredentials/...` (a privileged read — requires
`DeviceLocalCredential.Read.All`). Retrieving the actual password is a sensitive
operation; gate it behind appropriate RBAC.

## BitLocker

BitLocker policy is part of the endpoint-security / Settings Catalog disk-encryption
surface. The cookbook also shows toggling tenant-level encryption-reporting behavior
via a `PUT`/`PATCH` to the relevant configuration endpoint. As always: configure
only the BitLocker settings you intend to enforce; the rest inherit defaults.
