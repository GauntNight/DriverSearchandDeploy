# AutoPackager Pipeline Lifecycle

## Overview

AutoPackager's core value proposition is its fully automated pipeline that transforms driver/software discovery into production-ready Intune deployments with zero human intervention. The pipeline is implemented as a **Celery task chain** orchestrated by the `OrchestrationEngine` and executes four sequential stages:

```
Discovery → Packaging → Testing → Deployment
```

Each stage is a separate Celery task that:
- Updates job state in the database before execution
- Performs its specialized work (finding updates, downloading installers, running tests, etc.)
- Passes results to the next stage via the Celery chain
- Implements retry logic with exponential backoff on failure
- Marks the job as `failed` if retries are exhausted

## State Machine

### Valid States

Jobs progress through the following states (defined in `autopackager/models/job.py`):

| State | Description |
|-------|-------------|
| `pending` | Job created, waiting to be picked up by worker |
| `discovering` | Discovery agent searching for updates (OEM catalogs, vendor websites) |
| `packaging` | Packaging agent downloading installer and creating .intunewin package |
| `testing` | Testing agent running smoke tests and optional VM-based validation |
| `deploying` | Deployment agent publishing to Intune and assigning to Ring 0 |
| `completed` | Job finished successfully - package deployed to Intune |
| `failed` | Job failed after exhausting retries (see error_message field) |
| `cancelled` | Job manually cancelled by operator (not yet implemented) |

### State Transition Diagram

```
                         ┌─────────────┐
                         │   PENDING   │
                         └──────┬──────┘
                                │
                    ┌───────────▼───────────┐
                    │    DISCOVERING        │
                    │  (discovery_task)     │
                    └───────┬───────────────┘
                            │
                ┌───────────┴──────────┐
                │                      │
         No Update Found          Update Found
                │                      │
                ▼                      ▼
         ┌──────────┐          ┌─────────────┐
         │COMPLETED │          │  PACKAGING  │
         └──────────┘          │(packaging_task)│
                               └──────┬────────┘
                                      │
                               ┌──────▼────────┐
                               │   TESTING     │
                               │(testing_task) │
                               └──────┬────────┘
                                      │
                            ┌─────────┴─────────┐
                            │                   │
                      Tests Failed        Tests Passed
                            │                   │
                            ▼                   ▼
                      ┌──────────┐        ┌──────────────┐
                      │  FAILED  │        │  DEPLOYING   │
                      └──────────┘        │(deployment_task)│
                                          └──────┬─────────┘
                                                 │
                                        ┌────────┴────────┐
                                        │                 │
                                  Deploy Failed     Deploy Success
                                        │                 │
                                        ▼                 ▼
                                  ┌──────────┐      ┌──────────┐
                                  │  FAILED  │      │COMPLETED │
                                  └──────────┘      └──────────┘

                       ┌─────────────────────────────────┐
                       │   Any stage can transition to:  │
                       │   • FAILED (after retries)      │
                       │   • CANCELLED (manual action)   │
                       └─────────────────────────────────┘
```

### State Transition Rules

**Valid Transitions:**
- `pending` → `discovering` (worker picks up job)
- `discovering` → `completed` (no update needed)
- `discovering` → `packaging` (update available)
- `packaging` → `testing`
- `testing` → `deploying` (tests passed)
- `testing` → `failed` (tests failed after retries)
- `deploying` → `completed` (deployment successful)
- Any active state → `failed` (after max retries exhausted)
- Any active state → `cancelled` (manual operator action)

**Invalid Transitions:**
- Cannot skip stages (e.g., `discovering` → `deploying`)
- Cannot go backwards (e.g., `deploying` → `packaging`)
- Cannot resume from `completed` or `failed` (create new job instead)

## Pipeline Stages

### 1. Discovery (`discovering`)

**Implemented by:** `autopackager/agents/discovery/discovery_agent.py`
**Celery Task:** `discovery_task()` in `autopackager/orchestration/tasks.py`

**Purpose:** Find new driver/software versions from OEM catalogs or vendor websites.

**What Happens:**
1. Job state updated to `discovering`
2. Route to appropriate discovery strategy based on `job.vendor`:
   - **Dell:** Download `DriverPackCatalog.cab`, extract XML, search for matching model + driver type
   - **HP:** Download HPIA platform list, find model GUID, query HP Cloud catalog
   - **Lenovo:** Download `catalogv2.xml`, search by machine type + driver category
3. Compare discovered version against `job.current_version`
4. If update available:
   - Populate `job_metadata` with `target_version`, `download_url`, `release_notes`
   - Return `{"update_available": True}` to next stage
5. If no update:
   - Mark job as `completed` with `{"no_update_needed": True}`
   - Skip remaining pipeline stages

**Data Passed to Next Stage:**
```json
{
  "job_id": 42,
  "update_available": true,
  "latest_version": "A12",
  "download_url": "https://downloads.dell.com/...",
  "release_notes": "Fixed stability issues..."
}
```

**Retry Behavior:**
- **Max Retries:** Configured in `config.yaml` → `jobs.max_retries` (default: 3)
- **Retry Delay:** `jobs.retry_delay_seconds` (default: 300 seconds / 5 minutes)
- **Failure Conditions:** Network errors, catalog parse failures, OEM API downtime

---

### 2. Packaging (`packaging`)

**Implemented by:** `autopackager/agents/packaging/packaging_agent.py`
**Celery Task:** `packaging_task()` in `autopackager/orchestration/tasks.py`

**Purpose:** Download installer and create Microsoft Intune .intunewin package.

**What Happens:**
1. Skip if previous stage returned `{"completed": True}` (no update needed)
2. Job state updated to `packaging`
3. Download installer from `download_url` to `data/downloads/`
4. Create package directory under `data/packages/{package_name}/`
5. Move installer to package directory
6. Generate silent installation commands:
   - **Drivers:** Typically `/silent /install` or OEM-specific flags
   - **Software:** Research common silent flags (.exe: `/S`, .msi: `/quiet /norestart`)
   - Uses LLM to analyze installer and suggest optimal parameters
7. Create detection rules (registry, file version, MSI product code)
8. Run `IntuneWinAppUtil.exe` to create `.intunewin` package
9. Save `Package` record to database with all metadata
10. Return package ID to next stage

**Data Passed to Next Stage:**
```json
{
  "job_id": 42,
  "package_id": 17,
  "intunewin_path": "data/packages/Dell_Latitude_5420_Chipset_A12/Dell_Chipset_A12.intunewin",
  "install_command": "setup.exe /s /v/qn",
  "uninstall_command": "msiexec /x {GUID} /qn"
}
```

**Retry Behavior:**
- **Retries on:** Download failures, disk I/O errors, IntuneWinAppUtil crashes
- **Not retried:** Invalid download URL (fails immediately)

---

### 3. Testing (`testing`)

**Implemented by:** `autopackager/agents/testing/testing_agent.py`
**Celery Task:** `testing_task()` in `autopackager/orchestration/tasks.py`

**Purpose:** Validate package installation before production deployment.

**What Happens:**
1. Skip if previous stage returned `{"completed": True}`
2. Job state updated to `testing`
3. Retrieve package from database using `package_id`
4. **Smoke Tests** (always run):
   - Verify `.intunewin` file exists and is readable
   - Validate package size > 0 bytes
   - Check install/uninstall commands are non-empty
   - Verify detection rules are well-formed
5. **VM Tests** (if `testing.vm_testing_enabled: true`):
   - Provision clean Windows VM from snapshot
   - Copy `.intunewin` package to VM
   - Run install command silently
   - Verify detection rules trigger (file exists, registry key present, etc.)
   - Run uninstall command
   - Verify clean removal
   - Restore VM to snapshot
6. Update package `test_passed` status in database
7. Return test results to next stage

**Test Result Structure:**
```json
{
  "job_id": 42,
  "test_passed": true,
  "smoke_tests": {
    "test_passed": true,
    "checks": {
      "file_exists": true,
      "file_size_valid": true,
      "commands_valid": true,
      "detection_rules_valid": true
    }
  },
  "vm_test_results": {
    "test_passed": true,
    "install_success": true,
    "detection_success": true,
    "uninstall_success": true,
    "duration_seconds": 142
  }
}
```

**Retry Behavior:**
- **Retries on:** VM provisioning failures, transient network errors
- **Not retried:** Test failures (install doesn't work, detection fails)
- **Effect of Failure:** Job marked `failed`, package flagged `test_passed=false`, deployment blocked

**Configuration:**
```yaml
testing:
  enabled: true
  vm_testing_enabled: false  # Set to true for full VM validation
  vm_provider: "hyperv"  # or "azure"
  timeout_minutes: 30
```

---

### 4. Deployment (`deploying`)

**Implemented by:** `autopackager/agents/deployment/deployment_agent.py`
**Celery Task:** `deployment_task()` in `autopackager/orchestration/tasks.py`

**Purpose:** Publish package to Microsoft Intune and assign to Ring 0 (IT Pilot).

**What Happens:**
1. Skip if previous stage returned `{"completed": True}`
2. Job state updated to `deploying`
3. Verify package passed testing (blocks if `package.test_passed == false`)
4. Authenticate with Microsoft Graph API
5. Create or update Intune Win32 app:
   - If app exists and `publishingState == "published"`: PATCH metadata
   - If app exists but not published: DELETE broken app shell, recreate
   - If app doesn't exist: CREATE new app
6. Upload `.intunewin` content:
   - Create content version
   - Upload file in chunks (Azure Blob Storage)
   - Commit content version
   - Wait for `publishingState` to flip to `"published"`
7. Assign to Ring 0 (IT Pilot):
   - Target Entra ID group: `AutoPackager-Ring0-ITPilot`
   - Assignment intent: `required`
   - Notifications: enabled
8. Save deployment record to database
9. Mark job `completed`

**Deployment Rings (Phase 1):**
```yaml
deployment_rings:
  - name: "IT Pilot"
    ring_id: "ring0"
    entra_group_id: "${RING0_GROUP_ID}"
    deferral_days: 0         # Deploys immediately
  - name: "Early Adopters"
    ring_id: "ring1"
    deferral_days: 3         # Manual promotion (future phase)
  - name: "Broad Deployment"
    ring_id: "ring2"
    deferral_days: 7         # Manual promotion (future phase)
  - name: "Critical Systems"
    ring_id: "ring3"
    deferral_days: 14        # Manual promotion (future phase)
```

**Data Returned:**
```json
{
  "job_id": 42,
  "intune_app_id": "a1b2c3d4-e5f6-...",
  "status": "deployed",
  "ring": "IT Pilot",
  "completed": true
}
```

**Retry Behavior:**
- **Retries on:** Graph API throttling (429), transient auth errors, blob upload failures
- **Not retried:** Permission errors (403), package test failures
- **Graph API Specifics:**
  - Uses `tenacity` library for exponential backoff
  - Max 5 retries per Graph API call
  - Respects `Retry-After` headers on 429 responses

---

## Data Flow

### Job Metadata Evolution

The `job.job_metadata` JSON field accumulates data as the pipeline progresses:

**Initial State (after `create_job`):**
```json
{
  "created_by": "cli",
  "manual_job": false
}
```

**After Discovery:**
```json
{
  "created_by": "cli",
  "manual_job": false,
  "target_version": "A12",
  "download_url": "https://downloads.dell.com/FOLDER123/Dell_Chipset_A12.exe",
  "release_notes": "Improved PCIe stability on 12th Gen Intel platforms",
  "release_date": "2024-03-15",
  "file_size": 45678912
}
```

**After Packaging:**
```json
{
  "created_by": "cli",
  "manual_job": false,
  "target_version": "A12",
  "download_url": "https://downloads.dell.com/...",
  "release_notes": "...",
  "package_id": 17,
  "intunewin_path": "data/packages/Dell_Latitude_5420_Chipset_A12/Dell_Chipset_A12.intunewin"
}
```

**After Deployment (final state):**
```json
{
  "created_by": "cli",
  "manual_job": false,
  "target_version": "A12",
  "download_url": "https://downloads.dell.com/...",
  "package_id": 17,
  "intunewin_path": "data/packages/.../Dell_Chipset_A12.intunewin",
  "intune_app_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "deployment_status": "deployed"
}
```

### Database Relationships

```
┌──────────────────┐
│       Job        │
│  • job_type      │──┐
│  • state         │  │
│  • vendor        │  │
│  • retry_count   │  │
│  • job_metadata  │  │
└──────────────────┘  │
                      │ job.job_metadata.package_id
                      │
                      ▼
              ┌──────────────────┐
              │     Package      │
              │  • intunewin_path│──┐
              │  • install_cmd   │  │
              │  • test_passed   │  │
              └──────────────────┘  │
                                    │ package.id
                                    │
                                    ▼
                            ┌──────────────────┐
                            │   Deployment     │
                            │  • intune_app_id │
                            │  • ring_id       │
                            │  • status        │
                            └──────────────────┘
```

---

## Retry and Failure Handling

### Retry Configuration

Defined in `autopackager/config/config.yaml`:

```yaml
jobs:
  max_retries: 3              # Max retry attempts per stage
  retry_delay_seconds: 300    # 5 minutes between retries
  concurrent_jobs: 5          # Max parallel Celery workers
```

### Retry Mechanism

Each Celery task implements this pattern:

```python
try:
    # Execute stage logic
    result = agent.execute(job)
    return result
except Exception as e:
    logger.error("Stage failed", error=str(e))

    if engine.can_retry_job(job_id):
        retry_count = engine.increment_retry_count(job_id)
        logger.info("Retrying", retry_count=retry_count)
        # Celery retry with exponential backoff
        raise self.retry(exc=e, countdown=engine.retry_delay)
    else:
        # Retries exhausted
        engine.mark_job_failed(job_id, f"Stage failed: {str(e)}")
        raise
```

**Retry Flow:**
1. Exception caught by task's `try/except`
2. Check if `job.retry_count < max_retries` (default: 3)
3. If yes: increment `retry_count`, schedule retry after `retry_delay_seconds`
4. If no: mark job `failed`, store error message, stop pipeline

**Exponential Backoff:**
- First retry: after 5 minutes
- Second retry: after 5 minutes
- Third retry: after 5 minutes
- Fourth attempt fails → job marked `failed`

*(Note: Current implementation uses fixed delay; exponential backoff is roadmap item)*

### Failure States

**Soft Failures (retried):**
- Network timeouts downloading installer
- OEM catalog temporarily unavailable
- Graph API throttling (429 responses)
- VM provisioning timeout

**Hard Failures (not retried):**
- Invalid download URL (404)
- Package test failures (install doesn't work)
- Insufficient Intune permissions (403)
- Invalid configuration (missing `AZURE_CLIENT_ID`)

### Error Logging

All failures are logged with structured context:

```python
logger.error(
    "Packaging failed",
    job_id=42,
    error=str(e),
    vendor="dell",
    model="Latitude 5420",
    driver_type="chipset"
)
```

Logs are written to:
- **Console:** Colored output for interactive use
- **File:** `data/logs/autopackager.log` (JSON format)
- **Database:** `job.error_message` field for failed jobs

---

## Job Cancellation

### Current State (Phase 1)

Job cancellation is **partially implemented**:
- `JobState.CANCELLED` enum exists
- No CLI command or API endpoint to trigger cancellation
- Celery tasks do not poll for cancellation

### Roadmap (Future Phases)

**CLI Interface:**
```bash
python cli.py jobs cancel <job-id>
```

**Implementation Strategy:**
1. Update `job.state = CANCELLED` in database
2. Each Celery task checks state before execution:
   ```python
   if engine.get_job(job_id).state == JobState.CANCELLED:
       logger.info("Job cancelled by user")
       return {"cancelled": True}
   ```
3. Celery chain stops propagating to next stage

**Cleanup on Cancel:**
- Delete downloaded installer files
- Remove partially created packages
- Rollback database records (optional, configurable)

---

## Pipeline Construction

### Celery Chain Execution

The pipeline is implemented as a **Celery chain** in `process_job()`:

```python
from celery import chain

pipeline = chain(
    discovery_task.s(job_id),
    packaging_task.s(job_id),
    testing_task.s(job_id),
    deployment_task.s(job_id)
)

result = pipeline.apply_async()
```

**How Chains Work:**
1. Each task receives output from previous task as first argument
2. `packaging_task(previous_result, job_id)` gets discovery result
3. If discovery returns `{"completed": True}`, packaging/testing/deployment skip
4. Failure in any task breaks the chain (unless retried)

**Chain ID Tracking:**
- Returned as `pipeline_id` in `process_job()` response
- Can be used with Celery's result backend to query status:
  ```python
  from autopackager.orchestration.celery_app import celery_app
  result = celery_app.AsyncResult(pipeline_id)
  print(result.state)  # PENDING, STARTED, SUCCESS, FAILURE
  ```

---

## Monitoring and Observability

### Job State Tracking

**CLI Commands:**
```bash
# List all jobs with current state
python cli.py jobs list

# Get detailed job status
python cli.py jobs status <job-id>

# Filter by state
python cli.py jobs list --state failed
python cli.py jobs list --state deploying
```

**Job Status Output:**
```
Job ID: 42
Status: deploying
Type: driver_update
Title: Dell Latitude 5420 - Chipset Driver
Vendor: dell
Current Version: A10
Target Version: A12
Created: 2024-03-20 14:32:01
Updated: 2024-03-20 14:48:22
Retry Count: 0/3
Error: None

Metadata:
  package_id: 17
  intune_app_id: a1b2c3d4-e5f6-7890-abcd-ef1234567890
  deployment_status: in_progress
```

### Deployment Status Polling

AutoPackager includes a **periodic task** to poll Intune deployment status:

**Celery Beat Schedule:**
```python
# Runs every 15 minutes (configurable)
celery_app.conf.beat_schedule = {
    'poll-deployment-status': {
        'task': 'autopackager.poll_deployment_status',
        'schedule': crontab(minute='*/15'),
    },
}
```

**What It Does:**
1. Query Graph API for all deployed apps
2. Get installation status per device:
   - `installed` → success
   - `failed` → error (capture error code)
   - `pending` → waiting for device check-in
   - `notApplicable` → device not in target group
3. Update `deployment.status` in database
4. Log summary metrics

**Metrics Tracked:**
```json
{
  "total_checked": 12,
  "successful_updates": 9,
  "failed_updates": 1,
  "summary": {
    "total_installed": 234,
    "total_failed": 3,
    "total_pending": 45,
    "total_not_applicable": 12
  }
}
```

---

## Configuration Reference

### Orchestration Settings

**File:** `autopackager/config/config.yaml`

```yaml
# Job Processing
jobs:
  max_retries: 3              # Retry attempts per stage before marking failed
  retry_delay_seconds: 300    # Delay between retries (5 minutes)
  concurrent_jobs: 5          # Max parallel workers (Celery concurrency)

# Celery / Redis
redis:
  host: "localhost"
  port: 6379
  db: 0

# Testing
testing:
  enabled: true
  vm_testing_enabled: false   # Enable VM-based installation testing
  vm_provider: "hyperv"       # hyperv or azure
  timeout_minutes: 30

# Deployment Rings
deployment_rings:
  - name: "IT Pilot"
    ring_id: "ring0"
    entra_group_id: "${RING0_GROUP_ID}"
    deferral_days: 0

# Status Polling
status_polling:
  enabled: true
  polling_interval_minutes: 15
  max_devices_per_poll: 1000
  retry_on_rate_limit: true
```

---

## Troubleshooting

### Stuck Jobs

**Symptom:** Job remains in `discovering`, `packaging`, `testing`, or `deploying` for > 30 minutes.

**Diagnosis:**
```bash
# Check Celery worker logs
tail -f data/logs/autopackager.log

# Inspect Redis queue
redis-cli
> LLEN celery
> LRANGE celery 0 -1
```

**Common Causes:**
- Celery worker crashed (restart with `python cli.py worker start`)
- Redis connection lost (check `redis-server` is running)
- Network issue blocking download/API call
- OEM catalog website down

**Resolution:**
1. Check worker is running: `ps aux | grep celery`
2. Restart worker if needed
3. Job will auto-retry from last checkpoint

---

### Failed Jobs

**Symptom:** Job state = `failed`, `error_message` populated.

**Diagnosis:**
```bash
python cli.py jobs status <job-id>
# Check "Error" field for root cause
```

**Common Errors:**

| Error Message | Cause | Resolution |
|---------------|-------|------------|
| `Discovery failed: HTTP 404` | Invalid catalog URL | Update `config.yaml` OEM catalog URL |
| `Packaging failed: No download URL` | Discovery didn't find update | Check OEM catalog has driver for model |
| `Testing failed: Package validation failed` | Detection rules incorrect | Review package detection rules |
| `Deployment failed: HTTP 403 Forbidden` | Missing Graph API permissions | Run `azure-setup.ps1` to grant permissions |
| `Deployment failed: Package has not passed testing` | Tests failed | Review test logs, fix package, retry |

**Recovery:**
- Fix underlying issue (permissions, network, config)
- Delete failed job: `python cli.py jobs delete <job-id>`
- Create new job with same parameters

---

### Debugging Pipeline Stages

**Enable Debug Logging:**
```yaml
# config.yaml
logging:
  level: "DEBUG"  # Was "INFO"
```

**Trace a Job Through Pipeline:**
```bash
# Watch logs in real-time
tail -f data/logs/autopackager.log | grep "job_id=42"

# Filter by stage
tail -f data/logs/autopackager.log | grep "discovery"
tail -f data/logs/autopackager.log | grep "packaging"
```

**Inspect Celery Task State:**
```python
from autopackager.orchestration.celery_app import celery_app

# Get task result by ID
result = celery_app.AsyncResult('task-id-from-logs')
print(result.state)      # PENDING, STARTED, SUCCESS, FAILURE
print(result.info)       # Exception info if failed
print(result.traceback)  # Full stack trace
```

---

## Performance Tuning

### Concurrent Jobs

**Default:** 5 parallel workers
**Tuning:**
```yaml
jobs:
  concurrent_jobs: 10  # Process 10 jobs simultaneously
```

**Celery Worker Configuration:**
```bash
# Start worker with custom concurrency
celery -A autopackager.orchestration.celery_app worker \
  --loglevel=INFO \
  --concurrency=10
```

**Considerations:**
- Each job uses CPU for packaging + network for downloads
- Monitor RAM usage (Intune .intunewin files can be 500MB+)
- Graph API has rate limits (~1000 requests/hour per app)

### Retry Delays

**Default:** 5 minutes between retries
**Tuning for faster retry:**
```yaml
jobs:
  retry_delay_seconds: 60  # Retry after 1 minute
```

**Tradeoff:** Faster retries may hit rate limits (OEM catalogs, Graph API).

---

## Future Enhancements

**Planned for Future Phases:**

1. **Automatic Ring Progression:**
   - Monitor Ring 0 deployment success rate
   - Auto-promote to Ring 1 after 3 days if success > 95%
   - Auto-promote to Ring 2 after 7 days, Ring 3 after 14 days

2. **Job Cancellation:**
   - CLI command: `python cli.py jobs cancel <job-id>`
   - Graceful cleanup (delete downloads, remove partial packages)

3. **Pipeline Webhooks:**
   - Send notifications on state transitions (Teams, email, Slack)
   - Example: "Job 42 completed - Dell Latitude 5420 Chipset deployed to Ring 0"

4. **Job Prioritization:**
   - High-priority jobs (critical security updates) jump queue
   - Low-priority jobs (optional software) defer during business hours

5. **Exponential Backoff:**
   - First retry: 1 minute
   - Second retry: 5 minutes
   - Third retry: 15 minutes
   - Reduces load on OEM catalogs and Graph API

6. **Pipeline Checkpoints:**
   - Save intermediate state (downloaded installer, created package)
   - Resume from checkpoint on retry instead of restarting stage

---

## Related Documentation

- **[README.md](../README.md)** — Project overview and architecture
- **[IMPLEMENTATION_GUIDE.md](../IMPLEMENTATION_GUIDE.md)** — Setup and configuration
- **Architecture Diagrams** — See README.md for visual pipeline flow
- **API Reference** — `autopackager/orchestration/engine.py` docstrings

---

## Questions?

- **Stuck jobs?** → See [Troubleshooting](#troubleshooting)
- **Failed deployments?** → Check Graph API permissions with `azure-setup.ps1`
- **Want to contribute?** → Read pipeline code in `autopackager/orchestration/`

**Key Files:**
- State machine: `autopackager/models/job.py`
- Pipeline orchestration: `autopackager/orchestration/tasks.py`
- Engine: `autopackager/orchestration/engine.py`
- Config: `autopackager/config/config.yaml`
