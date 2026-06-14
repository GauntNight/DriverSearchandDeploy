# AutoPackager — Mission Control (demo console)

A single-screen, three-panel demo that shows AutoPackager taking one app from
**intake → Intune → Ring 0**, with the AI research step narrating live. It is a
**demo-grade** layer on top of the existing FastAPI app + agents — not a
production hardening pass.

```
┌───────────────┬──────────────────────────────┬───────────────┐
│  Pipeline     │  Intune "Production" view     │  Agent console │
│  status       │  (live Graph data, NOT iframe)│  + AI lamp     │
└───────────────┴──────────────────────────────┴───────────────┘
```

Everything new lives under `demo/` plus one additive `mount_demo(app)` call in
`autopackager/web/api.py` and a few additive event-publishing hooks in
`autopackager/orchestration/tasks.py` + `engine.py`. Delete `demo/` and that
one mount line and the core is untouched (the hooks degrade to silent no-ops).

---

## How to run

From the repo root, with the canonical venv:

```powershell
# 1. Infra (Redis broker + pub/sub, the Celery worker, the FastAPI app)
./start-redis.bat
./venv/Scripts/python.exe cli.py worker start              # in its own terminal
./venv/Scripts/python.exe -m uvicorn autopackager.web.api:app --port 8000
#   …or ./launch-all.bat to start all three at once.

# 2. Open the console
#    http://localhost:8000/demo
```

The header shows four readiness lights — **AI / Redis / Graph / Worker** — and a
`mode:` badge. Green Redis is enough to run (fixture mode covers a
credential-less laptop); green Graph means the center panel is live tenant data.

---

## The three intake modes (operator's on-stage gesture)

1. **Drag-and-drop / browse** an `.msi` / `.exe` onto the drop strip — the
   primary mode. The backend saves it to a scoped sandbox
   (`data/demo_sandbox/uploads/`, **not** `C:\` root), extracts identity with
   the existing MSI/PE extractors, and resolves **catalog HIT vs MISS**.
2. **Paste a vendor download URL** — downloaded into the sandbox, then the same
   extract → match path.
3. **Driver form** (vendor / model / driver-type) — the existing driver job.

**Catalog hit** → deterministic package, no model in the loop (the lamp stays
green — "that one didn't wake the AI"). **Catalog miss** → the Claude research
bridge runs, reads the real installer, writes a catalog entry, and the pipeline
proceeds. **Re-running the same app now resolves as a HIT** — the system
visibly learns.

Optional **Ring 0 approval gate**: tick the box before intake. The pipeline
holds at "Testing passed"; click **Approve ▶ Ring 0** to release the deploy.

---

## `DEMO_CLAUDE_MODE` — the miss path

Set the environment variable before launching uvicorn (and the worker):

| mode | behaviour | risk |
| --- | --- | --- |
| `replay` (default) | streams a captured run from `demo/fixtures/claude_stream_*.ndjson`, then writes the catalog entry | none — honest when disclosed |
| `live` | a real cold research run — prefers `claude-agent-sdk`, falls back to `claude -p --output-format stream-json`; allowlisted tools (Read, Bash, Write) scoped to the sandbox | slower, small risk of wandering |
| `off` | skip research; hit-only demo | none |

```powershell
$env:DEMO_CLAUDE_MODE = "replay"   # or "live" / "off"
```

For the **live** path, install the optional SDK once:
`./venv/Scripts/python.exe -m pip install -r demo/requirements.txt`.
If the SDK isn't present the bridge automatically uses the `claude` CLI already
on this box. Both authenticate through the local Claude Code session — no window
needs to stay open.

> **Billing (as of 2026-06-15):** `claude -p` and the Agent SDK on subscription
> plans draw from a separate monthly Agent SDK credit pool, distinct from
> interactive limits. An exhausted pool surfaces as a **red lamp** with a clear
> sublabel — never a silent hang. Decide subscription vs `ANTHROPIC_API_KEY`
> billing before relying on the live path.

---

## Security guardrails (apply even in the demo)

- The Claude research bridge runs **operator-side only** — never on anything
  resembling a customer endpoint.
- The bridge's working directory is scoped to `data/demo_sandbox/`, **not** the
  `C:\` root, with an allowlisted toolset (no `--dangerously-skip-permissions`).
- The **deterministic catalog path is the only component destined for customer
  hands.** The research path is operator-only until it is API-gated and
  sandboxed.

---

## Rehearsal checklist

- Pick the exact **hit app** and **miss app** in advance; run both end to end at
  least twice. (Good hit: a 7-Zip / VLC MSI already in the catalog. Good miss:
  an Inno Setup `.exe` — Claude authoring a registry detection rule reads well.)
- Decide `live` vs `replay` for the miss path per audience (cold live = more
  impressive, more risk). For a replay of a specific app, capture/author
  `demo/fixtures/claude_stream_<product-slug>.ndjson` (see the format below);
  the bridge picks it by product-name slug, then by installer kind, then the
  generic `claude_stream_<msi|exe>.ndjson`.
- Confirm the center view shows **live tenant** (badge), not fixture, and that
  the **Verify in Intune ↗** deep-link lands. Keep the real Intune portal open
  in a second tab as a fallback.
- For a clean re-run of the "miss → learns → hit" beat, remove the entry the
  bridge added from `data/installer_catalog.local.yaml` (and delete the staged
  upload via the sandbox), so the next run is a miss again.

### Replay fixture format

One JSON object per line:

```
{"text": "console line shown to the room", "level": "info", "delay_ms": 350}
{"catalog_result": {"install_command_template": "...{installer_filename}...",
                    "installer_family": "inno_setup",
                    "detection_rules": [ ... ]}}
```

`text` lines stream to the right console with the given delay; the final
`catalog_result` is written to the catalog overlay (the "learn" beat). For MSIs
`detection_rules` can be empty — the pipeline derives a ProductCode rule.

---

## What's wired (and what isn't)

- **Real** pipeline: intake creates a `Job` row and dispatches the same Celery
  chain (`discovery → packaging → testing → deployment`) the CLI uses. Events
  are published from the orchestration layer to Redis (`demo:events:{job_id}`)
  and streamed to the browser over **SSE** (`/api/demo/stream/{job_id}`).
- **Real** center panel: `GET /api/demo/intune/apps` proxies `get_win32_apps()`
  with `$expand=assignments` and resolves ring names; falls back to
  `demo/fixtures/intune_apps.json` (labelled "fixture mode") with no creds.
- **Not** wired: no iframe of the Intune portal (it sends `frame-ancestors
  deny`); the center panel is the answer instead. The deploy stage still hits
  the real tenant when Graph is configured — run a credential-less laptop in
  fixture mode for safe UI rehearsal.

### Responsiveness — stale-while-revalidate cache

Building the center view live is a Graph fan-out (list + per-app beta GET +
per-app assignments) that takes seconds; re-running it on every 4 s poll made the
panel feel like it was perpetually searching. `intune_view.get_apps_view_cached`
fixes that:

- The last good view is served **instantly** from memory (~4 ms vs ~9 s live) and
  **refreshed in the background** once it ages past 25 s (stale-while-revalidate).
- A **disk snapshot** (`data/demo_cache/apps_snapshot.json`) makes even the first
  paint after a server restart instant, then revalidates.
- Background polls stay cached; only a **tenant write** (publish/approve) or the
  manual **Refresh ⟳** button forces a synchronous live reload
  (`?refresh=1`). The header shows a freshness badge — "live · just now" /
  "cached · 12 s", pulsing while a background refresh runs.

### EXE installers (including metadata-less ones)

EXE is a first-class intake type. Most installers carry PE `VS_VERSIONINFO`
(ShareX, Notepad++) and resolve normally. Some ship **none at all** — VLC's NSIS
`.exe` returns blank ProductName/Version (Windows' own Properties tab agrees) — so
they're matched by a catalog entry's **`filename_pattern`**, which also supplies
the name/publisher; the version is parsed from the filename. An EXE that is both
unidentifiable **and** matches no catalog entry **escalates** (a clean failure with
a "use the vendor MSI" message) rather than publishing an app Intune can never
detect. Prefer the `.msi` when a vendor ships both (cleaner ProductCode detection).

## Endpoints (additive)

```
GET  /demo                                    three-panel console UI
GET  /demo/stream                             batch-stream page (one card per queued package)
GET  /api/demo/preflight                      readiness lights (AI/Redis/Graph/worker)
POST /api/demo/jobs                           intake: multipart file | JSON {url} | JSON {vendor,model,...}
GET  /api/demo/stream/{job_id}                SSE: pipeline + AutoPackager + lamp events (one job)
GET  /api/demo/stream/batch/{batch_id}        SSE: fan-in for a whole queue batch (events tagged with job_id)
GET  /api/demo/queue/{batch_id}/snapshot      batch cards + per-job state (initial render / reconnect)
GET  /api/demo/intune/apps                     live tenant Win32 apps (stale-while-revalidate cached; each row carries a `cve` risk block). ?refresh=1 forces a synchronous live reload
GET  /api/demo/intune/{app_id}/cves            CVE risk detail for one app (?mode=live re-scans NVD)
GET  /api/demo/intune/verify-url               deep-link to the Intune portal
GET  /api/demo/intune/software-delta           installed-but-not-packaged gap (source=intune|local|both; rows carry `cve`)
POST /api/demo/intune/{app_id}/autoupdate      toggle per-product autoupdate (on=full auto, off=gated)
POST /api/demo/intune/{app_id}/auto-delete     toggle per-product auto-delete-when-clean (on=delete, off=relabel Retired)
POST /api/demo/intune/{app_id}/retire          retire an old version now ({delete?}: relabel "Retired" or delete)
POST /api/demo/intune/check-updates            on-demand discovery: check every Latest app, dispatch upgrades
POST /api/demo/daily-update                     toggle the global daily-update Beat flag
POST /api/demo/jobs/{job_id}/approve           release the Ring 0 gate (UI confirms first) + deploy
POST /api/demo/jobs/{job_id}/retry             re-run a failed job's pipeline (gating preserved)
GET  /api/demo/jobs/{job_id}/logs              human-readable diagnostic log for a (failed) job
```

### Batch-stream page (`/demo/stream?batch=<id>`)

Queuing **more than one** package opens the batch-stream page (also reachable from the console's
header **Batch stream** pill). It is a live grid — one card per package — fed by a single fan-in SSE
(`/api/demo/stream/batch/{batch_id}`) that multiplexes every job's events and tags each with its
`job_id`. Unlike the single-action console, **each card resolves its own prompt independently**
(approve / confirm-url / drop-installer), so a batch never makes the operator wait on one keyhole.
A **failed** card exposes **Retry** and **View logs**. The page is reconnect-safe: the snapshot
endpoint reseeds a parked action from the persisted `queue_origin` state, since the live prompt
events themselves have no Redis backlog.

### CVE risk — "patch by risk"

The center "Intune · Apps" table carries a **Risk** column: each app is correlated
with the public CVEs a newer release fixes, scored by CVSS severity, and the table
sorts **worst-first**. A severity badge (red `CRITICAL 9.8` → green `✓ no known
CVEs`) opens a **detail drawer** listing each CVE (NVD link, score, summary, the
version it's `fixed in`), and **Patch now** runs the app through the existing
version-check → supersedence → Ring 0 pipeline. The panel header shows an
estate-risk roll-up; the software-gap modal badges vulnerable unmanaged software.

Data comes from `autopackager/services/cve_intel.py`, layered and best-effort:

| `CVE_INTEL_MODE` | behaviour |
| --- | --- |
| `cache` (default) | curated offline fixture (`demo/fixtures/cve_intel.json`) — fully offline, stage-reliable |
| `live` | cache first, then the **NVD CVE API 2.0** by CPE (set `NVD_API_KEY` to raise the rate limit), then an optional AI-bridge fallback |
| `off` | no CVE data (feature disabled) |

A CVE counts against an app only when a **newer** release fixes it, so a current
app shows green and an outdated one lights up. The `cpe` catalog field
(`cpe:2.3:a:vendor:product`) keys the precise NVD lookup; products without one
still resolve by display-name alias against the curated fixture.

> **Rehearsal — staging the red badges.** A fully up-to-date tenant correctly
> shows **all green**. To demo the risk story, pre-stage a deliberately-**old**
> build before the show: **VLC 3.0.20** (HIGH 8.0), **7-Zip 24.08** (HIGH 7.8),
> **Notepad++ 8.8.1** (HIGH 7.3), or **Python 3.12.0** for the **CRITICAL 10.0**
> tarfile RCE. Publish it through the normal pipeline; it appears red in the Risk
> column; **Patch now** finds the current build, supersedes it, and on the next
> 4-second poll the badge clears — the whole "vulnerable → one click → patched"
> arc on stage. Keep `CVE_INTEL_MODE=cache` for a guaranteed-offline run, or
> `live` to show a real NVD query (have a backup in case the feed is slow).

> **"Patch now" — use replay for the version check.** The version-check step
> (`DEMO_CLAUDE_MODE`) is separate from `CVE_INTEL_MODE`. In **replay** it returns
> a deterministic latest build from a `version_check_<slug>.ndjson` fixture (with a
> real download URL). In **live** the agent web-searches, which is unreliable for a
> fine-grained bump — and for VLC it returns the vendor's default **`.exe`**, which
> ships no metadata. For a predictable on-stage upgrade, run "Patch now" in replay.
> (VLC trivia worth knowing: **3.0.21 was `.exe`-only**; the newest VLC with a
> managed `.msi` is **3.0.23**, which the bundled VLC fixture targets.)

### Application lifecycle (version state → autoupdate → retire)

The center table is a lifecycle worklist. The server ranks every deployed app in a product line by
its Graph `displayVersion`, so each row reads **Latest** / **N-1** / **N-2** (reliable even in a
device-less tenant). An install-count pill is the **clean** signal: for an old version, 0 installs
means nothing on the estate runs it, and a timer (`demo/clean_tracking.py`) counts how long it has
stayed clean.

Two per-product toggles on the **Latest** row drive automation (both keyed by product line, so they
apply across the product's versions):

- **`auto ⟳`** — autoupdate. ON = a discovered newer version is full-auto upgraded; OFF (default) =
  the upgrade is packaged + tested and **held at the Ring-0 gate**.
- **`del ⌫`** — auto-delete-when-clean. ON = once an old version is clean past
  `lifecycle.clean_window_days` (default 30) it is **deleted** from Intune; OFF (default) = it is
  **relabeled "Retired"** (struck-through badge, object kept — reversible).

**Discovery** runs on demand (**Check updates**) and daily (the **Daily** Beat toggle): a
version-check cascade (catalog → internet) over every Latest app, dispatching an upgrade per genuine
newer build (full-auto or gated per the product's setting), with a no-duplicate guard. The same daily
Beat runs a **retire sweep** — relabeling every retire-eligible old version, or deleting the ones
whose product opted into auto-delete (clearing incoming supersedence links first). An old row also
carries a manual **Retire**/**Delete** button (a confirm appears only when it actually deletes).

A retired version is terminal: its badge reads "Retired", it is no longer retire-eligible, and its
CVE risk is drained (0 installs). Lifecycle flags + clean/retire state are re-applied on **every**
serve (even cached ones), so a freshly-toggled flag takes effect on the next paint.

> **Rehearsal — staging a retirement.** A single-version tenant shows everything as **Latest** with
> nothing to retire (correct). To demo the back half: publish an **old** build, then upgrade it (the
> supersedence flow) so the old build becomes **N-1**. With 0 installs it shows **clean**; lower
> `lifecycle.clean_window_days` to `0` in `config.yaml` (worker restart) to make it **retire-ready**
> immediately. Then either click **Retire** (relabels "Retired"), or turn on **`del ⌫`** and click
> **Delete** (removes it from Intune). The daily Beat does the same unattended.

### Approval gate

Queue items (and any job launched with the Ring 0 gate) are **held before deployment**: the gated
pipeline runs discovery → packaging → testing only, and `/approve` dispatches deployment separately.
Two backstops keep an un-approved job from reaching the tenant — `deployment_task` refuses to deploy
unless `gate_approved` is persisted on the job (set by `/approve`), and the **Approve** buttons
confirm before publishing so an accidental click can't write to Intune.
