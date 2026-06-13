# Graph Fundamentals

The foundation for every other reference. Read this once per session before doing
Intune automation; the domain references assume you already have a connected Graph
session and know the universal request shape.

## Module install + import

```powershell
Install-Module -Name Microsoft.Graph.Authentication -Scope CurrentUser -Repository PSGallery -Force
Import-Module Microsoft.Graph.Authentication
```

`Microsoft.Graph.Authentication` is the only module strictly required — it provides
`Connect-MgGraph` and `Invoke-MgGraphRequest`, which let you call any raw Graph URI.
You do **not** need the giant per-workload SDK modules to call endpoints directly,
and avoiding them keeps automation fast and version-stable.

## Authentication

### Interactive (dev, one-off, exploratory)

Best for development and ad-hoc work. Request every scope you'll touch up front so
you can reuse the session across all stages:

```powershell
Connect-MgGraph -Scopes `
  DeviceManagementConfiguration.ReadWrite.All, `
  DeviceManagementApps.ReadWrite.All, `
  DeviceManagementManagedDevices.ReadWrite.All, `
  DeviceManagementServiceConfig.ReadWrite.All, `
  DeviceManagementRBAC.ReadWrite.All, `
  Directory.Read.All, `
  Group.ReadWrite.All, `
  Policy.ReadWrite.ConditionalAccess, `
  RoleManagement.ReadWrite.Directory
```

Trim scopes to least privilege for production identities. The list above is a
broad superset that covers most Intune automation.

### Unattended / app-only (production, CI, AutoPackager-style pipelines)

For headless automation, register an Entra app, grant it **application** Graph
permissions (admin-consented), and authenticate with the client-credentials grant.
Two equivalent approaches:

**a) `Connect-MgGraph` with app credentials** (preferred — you then keep using
`Invoke-MgGraphRequest` exactly as in interactive mode):

```powershell
$secure = ConvertTo-SecureString $clientSecret -AsPlainText -Force
$cred   = [System.Management.Automation.PSCredential]::new($clientId, $secure)
Connect-MgGraph -TenantId $tenantId -ClientSecretCredential $cred
# Certificate auth is better than a secret for long-lived automation:
# Connect-MgGraph -TenantId $tenantId -ClientId $clientId -CertificateThumbprint $thumb
```

**b) Raw token, then a bearer header** (when you want full control of the HTTP call
or are not using the Graph module):

```powershell
$body = @{
    client_id     = $clientId
    client_secret = $clientSecret
    scope         = "https://graph.microsoft.com/.default"
    grant_type    = "client_credentials"
}
$token = (Invoke-RestMethod -Method POST `
    -Uri "https://login.microsoftonline.com/$tenantId/oauth2/v2.0/token" `
    -ContentType "application/x-www-form-urlencoded" -Body $body).access_token

$headers = @{
    Authorization  = "Bearer $token"
    "Content-Type" = "application/json"
}
Invoke-RestMethod -Uri $uri -Headers $headers -Method GET
```

> The cookbook also demonstrates the OAuth2 **device-code** flow for a few legacy
> non-Graph endpoints (e.g. `main.iam.ad.ext.azure.com` for tenant roaming
> settings). That flow exists when an action has no first-party Graph endpoint; for
> normal Intune resources, use one of the patterns above.

## The universal request

`Invoke-MgGraphRequest` is the workhorse. It handles auth on the active session and
takes any URI, so you are never limited to whatever cmdlets a module happens to expose.

```powershell
$uri  = "https://graph.microsoft.com/beta/deviceManagement/<resourceCollection>"

# Create
$new  = Invoke-MgGraphRequest -Method POST -Uri $uri -Body $json `
            -ContentType "application/json" -OutputType PSObject
$id   = $new.id        # capture for assignment / follow-up PATCH

# Read (and pull a property out of the result)
$existing = Invoke-MgGraphRequest -Method GET -Uri $uri -OutputType PSObject
$existing.value         # collection endpoints wrap results in a .value array

# Update one record
Invoke-MgGraphRequest -Method PATCH -Uri "$uri/$id" -Body $patchJson -ContentType "application/json"
```

Key switches:
- `-OutputType PSObject` — returns a usable object you can dot-walk (`$x.id`,
  `$x.value`). Without it you get a hashtable. Use PSObject when you need the `id`.
- `-OutputFilePath <path>` — streams the raw response to a file. Essential for the
  reporting endpoints, which return a downloadable payload rather than an object.

## beta vs v1.0

- Intune `deviceManagement` and `deviceAppManagement` resources are overwhelmingly
  on **`/beta`** in practice. Default there.
- `/v1.0` is the stable surface; a minority of Intune-adjacent calls use it (e.g.
  `/v1.0/identity/conditionalAccess/...`).
- Some capabilities only exist on beta (example from the book: assigning directory
  roles to groups requires the beta `/users` and group endpoints).
- beta schemas can change without notice. Pin behavior with tests if you depend on
  a beta-only field.

## Error handling

- A vague **"malformed request" / 400** almost always means a casing mistake in a
  field name or a missing/incorrect `@odata.type`. Check those before anything else.
- For async operations (Win32 content upload, large report export jobs), Graph
  returns a "processing" state. **Poll** until the state flips to success before the
  next step; don't assume completion. See the relevant domain reference for the
  exact poll field.
- Wrap production calls in `try/catch`. A failed `Invoke-MgGraphRequest` throws; the
  thrown error body usually names the offending property.

## Finding referenced IDs

Many bodies need an id you must look up first (group id, notification template id,
role definition id, scope tag id). Pattern: `GET` the collection, filter to the
display name, take `.id`.

```powershell
$groupId = (Invoke-MgGraphRequest -Method GET -OutputType PSObject `
    -Uri "https://graph.microsoft.com/beta/groups?`$filter=displayName eq 'Intune-Users'").value[0].id
```
