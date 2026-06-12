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

## Endpoints (additive)

```
GET  /demo                                    three-panel console UI
GET  /demo/stream                             batch-stream page (one card per queued package)
GET  /api/demo/preflight                      readiness lights (AI/Redis/Graph/worker)
POST /api/demo/jobs                           intake: multipart file | JSON {url} | JSON {vendor,model,...}
GET  /api/demo/stream/{job_id}                SSE: pipeline + AutoPackager + lamp events (one job)
GET  /api/demo/stream/batch/{batch_id}        SSE: fan-in for a whole queue batch (events tagged with job_id)
GET  /api/demo/queue/{batch_id}/snapshot      batch cards + per-job state (initial render / reconnect)
GET  /api/demo/intune/apps                     live tenant Win32 apps (fixture fallback)
GET  /api/demo/intune/verify-url               deep-link to the Intune portal
GET  /api/demo/intune/software-delta           installed-but-not-packaged gap (source=intune|local|both)
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

### Approval gate

Queue items (and any job launched with the Ring 0 gate) are **held before deployment**: the gated
pipeline runs discovery → packaging → testing only, and `/approve` dispatches deployment separately.
Two backstops keep an un-approved job from reaching the tenant — `deployment_task` refuses to deploy
unless `gate_approved` is persisted on the job (set by `/approve`), and the **Approve** buttons
confirm before publishing so an accidental click can't write to Intune.
