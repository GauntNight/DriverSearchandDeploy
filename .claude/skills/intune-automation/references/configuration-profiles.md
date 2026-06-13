# Configuration Profiles & Assignments

Covers the three ways Intune ships settings to Windows/macOS, plus the assignment
body shapes that every policy type reuses.

## Choosing the surface

- **Settings Catalog** (`deviceManagement/configurationPolicies`) — the modern,
  preferred surface. Granular, searchable settings expressed as a `settings` array.
- **Classic device profiles** (`deviceManagement/deviceConfigurations`) — older
  template-style profiles and, importantly, the home of **custom OMA-URI**.
- **ADMX / Administrative Templates** (`deviceManagement/groupPolicyDefinitions`) —
  the Group-Policy-equivalent settings, configured as presentation values against a
  definition id.

## Settings Catalog (configurationPolicies)

POST to `deviceManagement/configurationPolicies`. The policy carries `name`,
`platforms`, `technologies`, and a `settings[]` array. Each entry is a
`deviceManagementConfigurationSetting` wrapping a `settingInstance`. The instance
type depends on the setting kind:

```powershell
$uri = "https://graph.microsoft.com/beta/deviceManagement/configurationPolicies"
$json = @"
{
  "name": "$name",
  "description": "$description",
  "platforms": "windows10",
  "technologies": "mdm",
  "settings": [
    {
      "@odata.type": "#microsoft.graph.deviceManagementConfigurationSetting",
      "settingInstance": {
        "@odata.type": "#microsoft.graph.deviceManagementConfigurationChoiceSettingInstance",
        "settingDefinitionId": "<catalog setting id>",
        "choiceSettingValue": {
          "@odata.type": "#microsoft.graph.deviceManagementConfigurationChoiceSettingValue",
          "value": "<choice value id>",
          "children": []
        }
      }
    }
  ]
}
"@
```

- For a **single value** (string/int) use
  `deviceManagementConfigurationSimpleSettingInstance` with a
  `simpleSettingValue` of `...StringSettingValue` / `...IntegerSettingValue`.
- For **template-backed** settings (security baselines), each instance/value also
  carries a `settingInstanceTemplateReference` / `settingValueTemplateReference`
  with the template id — see `compliance-and-security.md`.
- Discover setting ids by querying `deviceManagement/configurationSettings?...` and
  `deviceManagement/configurationCategories?...` rather than guessing them.

## Custom OMA-URI (deviceConfigurations)

When a setting isn't in the catalog, push it via a custom profile. POST to
`deviceManagement/deviceConfigurations` with type `windows10CustomConfiguration`
and an `omaSettings` array:

```powershell
$uri = "https://graph.microsoft.com/beta/deviceManagement/deviceConfigurations"
$json = @"
{
  "@odata.type": "#microsoft.graph.windows10CustomConfiguration",
  "displayName": "$name",
  "description": "$description",
  "omaSettings": [
    {
      "@odata.type": "#microsoft.graph.omaSettingString",
      "displayName": "FirstSyncStatus",
      "omaUri": "./Vendor/MSFT/DMClient/Provider/MS DM Server/FirstSyncStatus/...",
      "value": "$value"
    }
  ]
}
"@
```

Use `omaSettingInteger` for integer values. The `omaUri` is the literal CSP path.

## ADMX / Administrative Templates

These are configured against a `groupPolicyDefinition` id by supplying presentation
values (e.g. `groupPolicyPresentationValueText`). Look up the definition under
`deviceManagement/groupPolicyDefinitions`, then create a configuration that
references it and attach the presentation values. This surface is fiddlier than the
catalog — prefer the Settings Catalog when the same setting exists there.

## Assignments — the body shapes (reused by ALL policy types)

After creating any policy, POST to `{policyUrl}/{id}/assign`. The body is an
`assignments` array of `target` objects. Mix and match include/exclude/all:

```powershell
$assignUri = "$uri/$id/assign"
$assignJson = @"
{
  "assignments": [
    { "target": { "@odata.type": "#microsoft.graph.groupAssignmentTarget", "groupId": "$includeGroupId" } },
    { "target": { "@odata.type": "#microsoft.graph.exclusionGroupAssignmentTarget", "groupId": "$excludeGroupId" } }
  ]
}
"@
Invoke-MgGraphRequest -Method POST -Uri $assignUri -Body $assignJson -ContentType "application/json"
```

Whole-population targets take no `groupId`:

```json
{ "assignments": [ { "target": { "@odata.type": "#microsoft.graph.allDevicesAssignmentTarget" } } ] }
```

```json
{ "assignments": [ { "target": { "@odata.type": "#microsoft.graph.allLicensedUsersAssignmentTarget" } } ] }
```

Notes:
- A few resource types use a differently-named assignment wrapper (Autopilot uses
  `/assignments` and an `assignments` collection; remediations use
  `deviceHealthScriptAssignments`; apps use `mobileAppAssignment` with intents).
  Those are documented in their own references — but the `target` `@odata.type`
  values above are universal.
- **Assignment filters** narrow a target (e.g. by OS version). They are a separate
  applicability concept layered on top of the group target.
