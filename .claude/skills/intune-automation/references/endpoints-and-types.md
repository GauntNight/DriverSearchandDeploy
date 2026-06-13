# Endpoints & `@odata.type` Index

Quick lookup so you never hand-roll a path or a type discriminator. All paths are
relative to `https://graph.microsoft.com/beta/` unless noted. Assignment is always
`POST {path}/{id}/assign` with an `assignments` array (see
`configuration-profiles.md` for the assignment body shapes).

## Endpoints by resource area

### Identity & groups
| Path | Purpose |
|------|---------|
| `users` | Create/read users (POST a user body) |
| `groups` | Create/read Entra groups |
| `devices/{deviceId}` | Read/patch a device object |
| `roleManagement/directory/...` | Entra (directory) role assignments |

### Device configuration
| Path | Purpose |
|------|---------|
| `deviceManagement/deviceConfigurations` | Classic profiles **and** custom OMA-URI profiles |
| `deviceManagement/configurationPolicies` | **Settings Catalog** policies (the modern surface) |
| `deviceManagement/configurationSettings?...` | Search the catalog's setting definitions |
| `deviceManagement/configurationCategories?...` | Browse catalog categories |
| `deviceManagement/templates/...` | Security-baseline / template-backed policies |
| `deviceManagement/groupPolicyDefinitions` / `.../groupPolicyDefinitions/` | ADMX-backed (Administrative Templates) settings |

### Compliance
| Path | Purpose |
|------|---------|
| `deviceManagement/deviceCompliancePolicies` | Per-platform compliance policies |
| `deviceManagement/compliancePolicies` | Newer unified compliance surface |
| `deviceManagement/notificationMessageTemplates` | Non-compliance notification templates (GET for ids) |

### Apps
| Path | Purpose |
|------|---------|
| `deviceAppManagement/mobileApps` | All app types (Win32, Store, MSIX, LOB, managed store apps) |
| `deviceAppManagement/mobileApps/{id}/microsoft.graph.win32LobApp/contentVersions` | Win32 content upload (see packaging ref) |
| `deviceAppManagement/mobileAppConfigurations` | App configuration policies |
| `deviceAppManagement/vppTokens` | Apple VPP tokens |
| `deviceAppManagement/windowsManagementApp/...` | Windows management/Store app integration |
| `deviceManagement/detectedApps` | Inventory of apps Intune has detected |

### Scripts & remediations
| Path | Purpose |
|------|---------|
| `deviceManagement/deviceManagementScripts` | Windows **platform scripts** (run-once PowerShell) |
| `deviceManagement/deviceHealthScripts` | **Proactive remediations** (detection + remediation pair) |
| `deviceManagement/deviceShellScripts` | macOS shell scripts |

### Enrollment & updates
| Path | Purpose |
|------|---------|
| `deviceManagement/windowsAutopilotDeploymentProfiles` | Autopilot profiles (assign via `/assignments`) |
| `deviceManagement/windowsAutopilotDeviceIdentities` | Registered Autopilot device hashes |
| `deviceManagement/deviceEnrollmentConfigurations` | ESP, enrollment restrictions, enrollment-status settings |
| `deviceManagement/windowsFeatureUpdateProfiles` | Feature-update (target version) rings |
| `deviceManagement/windowsDriverUpdateProfiles` | Driver-update profiles |
| `agreements` | Terms-of-use agreements |

### Reporting, audit & RBAC
| Path | Purpose |
|------|---------|
| `deviceManagement/reports/getXxxReport` | Synchronous report (POST filter/select, returns data) |
| `deviceManagement/reports/exportJobs` | Async export job (POST → poll → download) |
| `deviceManagement/auditEvents` / `.../auditEvents/` | Admin audit log |
| `deviceManagement/autopilotEvents?...` | Autopilot deployment events |
| `deviceManagement/roleDefinitions` | Custom Intune RBAC roles |
| `deviceManagement/roleDefinitions('{id}')` | A specific role (note the `('{id}')` key syntax) |
| `deviceManagement/roleAssignments` | Assign a role to members over a scope |
| `deviceManagement/roleScopeTags` | Scope tags (assign via `/assign`) |

### Conditional Access (v1.0)
| Path | Purpose |
|------|---------|
| `v1.0/identity/conditionalAccess/...` | CA policies (note: **v1.0**, not beta) |

## `@odata.type` discriminators

Assignment targets (used inside every `assignments[].target`):
| Type | Means |
|------|-------|
| `#microsoft.graph.groupAssignmentTarget` | Include this group (needs `groupId`) |
| `#microsoft.graph.exclusionGroupAssignmentTarget` | Exclude this group |
| `#microsoft.graph.allDevicesAssignmentTarget` | All devices |
| `#microsoft.graph.allLicensedUsersAssignmentTarget` | All users |

Configuration profile bodies:
| Type | Means |
|------|-------|
| `#microsoft.graph.windows10CustomConfiguration` | Custom OMA-URI profile (in `deviceConfigurations`) |
| `#microsoft.graph.windows10GeneralConfiguration` | Classic Windows device-restriction profile |
| `#microsoft.graph.macOSCustomConfiguration` | macOS custom (.mobileconfig) profile |

Settings Catalog (inside `configurationPolicies` → `settings[].settingInstance`):
| Type | Means |
|------|-------|
| `#microsoft.graph.deviceManagementConfigurationChoiceSettingInstance` | A choice (enum) setting |
| `#microsoft.graph.deviceManagementConfigurationChoiceSettingValue` | The chosen value |
| `#microsoft.graph.deviceManagementConfigurationSimpleSettingInstance` | A single value setting |
| `#microsoft.graph.deviceManagementConfigurationStringSettingValue` | String value |
| `#microsoft.graph.deviceManagementConfigurationIntegerSettingValue` | Integer value |
| `#microsoft.graph.deviceManagementConfigurationGroupSettingCollectionInstance` | A collection/group of settings |
| `#microsoft.graph.deviceManagementConfigurationSimpleSettingCollectionInstance` | Collection of simple values |
| `#microsoft.graph.deviceManagementConfigurationSetting` | Wrapper for a setting in the `settings` array |

Custom OMA-URI values:
| Type | Means |
|------|-------|
| `#microsoft.graph.omaSettingString` | String OMA-URI value |
| `#microsoft.graph.omaSettingInteger` | Integer OMA-URI value |

Compliance policies (one per platform; pair with `scheduledActionsForRule`):
| Type | Platform |
|------|----------|
| `#microsoft.graph.windows10CompliancePolicy` | Windows |
| `#microsoft.graph.androidDeviceOwnerCompliancePolicy` | Android (corporate/device owner) |
| `#microsoft.graph.iosCompliancePolicy` | iOS |
| `#microsoft.graph.macOSCompliancePolicy` | macOS |

Enrollment & Windows Hello:
| Type | Means |
|------|-------|
| `#microsoft.graph.deviceEnrollmentWindowsHelloForBusinessConfiguration` | WHfB enrollment config |
| `#microsoft.graph.depMacOSEnrollmentProfile` | Apple ADE/DEP macOS enrollment profile |

Remediation schedules (inside a `deviceHealthScripts` assignment):
| Type | Cadence |
|------|---------|
| `#microsoft.graph.deviceHealthScriptDailySchedule` | Daily (`interval`, `time`, `useUtc`) |
| `#microsoft.graph.deviceHealthScriptHourlySchedule` | Hourly (`interval`) |

Win32 app rules (inside `mobileApps` Win32 `rules` / `detectionRules`):
| Type | Means |
|------|-------|
| `#microsoft.graph.win32LobAppPowerShellScriptRule` | PowerShell detection **or** requirement rule (set `ruleType`) |
| `#microsoft.graph.win32LobAppFileSystemRule` | File/folder existence/version detection |
| `#microsoft.graph.win32LobAppProductCodeRule` | MSI product-code detection |
| `#microsoft.graph.win32LobAppRegistryRule` | Registry detection |

VPP / managed apps:
| Type | Means |
|------|-------|
| `#microsoft.graph.mobileAppAssignment` | App assignment wrapper |
| `#microsoft.graph.macOsVppAppAssignmentSettings` | macOS VPP assignment settings |
| `#microsoft.graph.macOSOfficeSuiteApp` / `macOSMicrosoftEdgeApp` / `macOSMicrosoftDefenderApp` | First-party macOS managed apps |
