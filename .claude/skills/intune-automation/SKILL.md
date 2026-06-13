---
name: intune-automation
description: >-
  Automate Microsoft Intune / Microsoft Endpoint Manager via the Microsoft Graph
  API and PowerShell. Use this skill WHENEVER a task touches Intune, MEM, MDM, or
  Graph deviceManagement endpoints — creating or assigning configuration profiles,
  the Settings Catalog, custom OMA-URI, compliance policies, security baselines
  (ASR, LAPS, BitLocker, Defender), Autopilot, ESP, update/feature/driver rings,
  packaging and uploading Win32 (.intunewin) apps, Store/MSIX apps, platform
  scripts, proactive remediations, Win32 detection/requirement scripts, macOS shell
  scripts, Android/iOS/macOS enrollment and VPP, Intune reporting and export jobs,
  audit events, or RBAC roles/scope tags. Reach for it even when the user just says
  "deploy X to Intune", "package this app for Intune", "write the Graph call for…",
  or "assign this policy" — do not hand-roll endpoints or @odata.type values from
  memory; they are easy to get subtly wrong. The references encode exact endpoints,
  payload schemas, and the gotchas that cause silent failures.
---

# Intune Automation (Microsoft Graph + PowerShell)

Distilled from the *Microsoft Intune Cookbook, 2nd Edition* (Andrew Taylor, Packt,
2026). Everything Intune does in its admin GUI is a Microsoft Graph call underneath.
This skill encodes the call patterns so you write working automation on the first
try instead of guessing endpoint paths and `@odata.type` values.

## When you are doing Intune work, read the right reference first

Don't reconstruct payloads from memory. Open the reference that matches the task,
copy the schema, fill in the values. Files live in `references/`:

| Task | Read |
|------|------|
| Auth, the universal request recipe, verbs, errors, beta vs v1.0 | `graph-fundamentals.md` (read this once per session — everything depends on it) |
| "Which endpoint / which `@odata.type`?" lookup | `endpoints-and-types.md` |
| Configuration profiles, Settings Catalog, custom OMA-URI, ADMX, assignments | `configuration-profiles.md` |
| Compliance policies, security baselines, ASR, LAPS, BitLocker, Defender | `compliance-and-security.md` |
| Win32 packaging (`.intunewin`) + the upload flow, Store/MSIX apps, detection rules, supersedence/dependency, assignment intents | `apps-and-win32-packaging.md` |
| Platform scripts, proactive remediations, Win32 detection/requirement scripts, macOS shell scripts | `scripting-and-remediations.md` |
| Autopilot, ESP, enrollment configs, update/feature/driver rings, Android/iOS/macOS enrollment, VPP | `enrollment-updates-platforms.md` |
| Reporting endpoints, export jobs, audit events, RBAC roles/scope tags, Intune Suite | `reporting-monitoring-tenant.md` |

## The one pattern that underlies everything

Almost every Intune automation is the same four moves. Internalize this; the
references just supply the URL and the body for each resource type.

1. **Connect** to Graph (see `graph-fundamentals.md` for interactive vs unattended).
2. **Build the JSON body** — with the correct `@odata.type` discriminator and
   case-sensitive field names.
3. **POST** the body to the resource collection URL to create it; capture the `id`
   from the response.
4. **Assign** by POSTing an `assignments` array to `{resourceUrl}/{id}/assign`.

```powershell
$json = @"
{ "@odata.type": "#microsoft.graph.<resourceType>", ... }
"@
$new   = Invoke-MgGraphRequest -Method POST -Uri $uri -Body $json `
             -ContentType "application/json" -OutputType PSObject
$id    = $new.id
$assignUri = "$uri/$id/assign"
Invoke-MgGraphRequest -Method POST -Uri $assignUri -Body $assignJson `
             -ContentType "application/json"
```

## Non-negotiable gotchas (these cause silent or cryptic failures)

- **Field names are case-sensitive.** `accountEnabled` works; `AccountEnabled`
  returns a generic *malformed request* error. When a POST fails with a vague 400,
  suspect casing first.
- **`@odata.type` is mandatory** on the body of most create calls and on every
  assignment `target`. It is the discriminator that tells Graph which concrete type
  you mean. Wrong or missing type → rejection. Look it up in `endpoints-and-types.md`.
- **Most Intune resources live on the `beta` endpoint**, not `v1.0`. The book uses
  `https://graph.microsoft.com/beta/...` almost everywhere. Some features (e.g.
  assigning roles to groups) *require* beta. Default to beta for deviceManagement
  unless you've confirmed a v1.0 equivalent exists.
- **Script content must be base64 of the UTF-16 (Unicode) bytes**, not UTF-8:
  `[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($script))`. Getting
  the encoding wrong produces scripts that deploy but won't run.
- **Only send what you configure.** Omitted settings inherit defaults; you don't
  need to populate every field. Padding the JSON with fields you don't intend to
  set causes more failures than it prevents.

## Verbs

`GET` read · `POST` create new / invoke action (like `/assign`) · `PATCH` update
existing record · `PUT` full replace (needs the full URL incl. the id) · `DELETE`.

## Scope: what this skill does NOT cover

App-registration walkthroughs in the Azure portal, licensing/purchase decisions,
and pure GUI click-paths are out of scope — this skill is about the programmatic
path. For the official, always-current schema, cross-check Microsoft Graph docs
and the book's companion repo:
`https://github.com/PacktPublishing/Microsoft-Intune-Cookbook-Second-Edition`.
