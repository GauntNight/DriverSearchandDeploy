# Reporting, Monitoring & Tenant Admin

How to pull data out of Intune (reports, exports, audit), and how to manage the
tenant's RBAC.

## Reports — two mechanisms

### Synchronous report endpoints

`POST deviceManagement/reports/getXxxReport` (e.g. `getAppsInstallSummaryReport`).
The body filters and shapes the result; the response is the report payload, which
you typically stream to a file with `-OutputFilePath`.

```powershell
$uri = "https://graph.microsoft.com/beta/deviceManagement/reports/getAppsInstallSummaryReport"
$json = @"
{
  "filter": "",
  "orderBy": [],
  "select": [ "DisplayName", "Publisher", "Platform", "AppVersion",
              "FailedDevicePercentage", "FailedDeviceCount", "FailedUserCount", "ApplicationId" ]
}
"@
$tempfile = "$env:TEMP\appinstallstatus.txt"
Invoke-MgGraphRequest -Method POST -Uri $uri -Body $json -ContentType "application/json" -OutputFilePath $tempfile
```

- `select` chooses columns; an empty `filter` returns everything.
- Different reports expose different column names — GET the report's metadata or
  consult the report's own schema for valid `select`/`filter` fields.
- These endpoints (e.g. `getDeviceComplianceReport`, `getAppsInstallSummaryReport`,
  device/config status reports) cover most day-to-day reporting.

### Async export jobs (large datasets)

`POST deviceManagement/reports/exportJobs` to request a job, then **poll** the job
until its status is complete, then **download** from the returned URL. Use this when
a synchronous call would be too large or times out. Same filter/select shaping, but
you wait for the job rather than getting data inline.

## Audit & device events

- **Admin audit log** — `deviceManagement/auditEvents` (and
  `.../auditEvents/{id}`). Every admin change is here: who changed what, when. Great
  for change-tracking and incident forensics.
- **Autopilot events** — `deviceManagement/autopilotEvents?...`.
- **Detected apps** — `deviceManagement/detectedApps` for software inventory.

GET these with `$filter`/`$select`/`$top` query params and page through `.value` +
`@odata.nextLink`.

## Intune RBAC (roles & scope tags)

### Custom roles

`POST deviceManagement/roleDefinitions` with `rolePermissions` → `resourceActions`
(allowed/notAllowed action strings). Read a specific role with the key syntax
`deviceManagement/roleDefinitions('{roleId}')`.

```powershell
$uri = "https://graph.microsoft.com/beta/deviceManagement/roleDefinitions"
$json = @"
{
  "displayName": "$name",
  "description": "$description",
  "rolePermissions": [
    { "resourceActions": [ { "allowedResourceActions": [ "Microsoft.Intune_..." ], "notAllowedResourceActions": [] } ] }
  ],
  "roleScopeTagIds": [ "0" ]
}
"@
```

### Role assignments

`POST deviceManagement/roleAssignments` to bind members to a role over a scope
(scope = groups of admins + scope tags defining which objects the role can see).

### Scope tags

`POST deviceManagement/roleScopeTags` to create a scope tag, then
`POST .../roleScopeTags/{id}/assign` to apply it. Most resource bodies accept a
`roleScopeTagIds` array (default `["0"]` = the built-in Default tag). Scope tags are
how you partition a tenant so a regional admin only sees their region's objects —
**least privilege done right**: prefer several tightly-scoped roles over a single
broad Global Administrator.

## Tenant administration & Intune Suite

- **Tenant admin** tasks (quiet-time policies for notifications, admin task review,
  org-wide settings) follow the same create→assign pattern against their respective
  endpoints.
- **Intune Suite** add-ons (e.g. Endpoint Privilege Management, Remote Help,
  Advanced Analytics, Enterprise App Management) are licensed extras. Each is
  enabled/configured through its own deviceManagement endpoint and automated with
  the same Graph patterns — there's no separate API style. Check current licensing
  inclusion (E3/E5/Business Premium) before assuming availability, and consult
  Microsoft's current docs since the lineup changes.
