# AutoPackager Configuration Reference (config.yaml)

> Reference documentation for `autopackager/config/config.yaml`
> Structured for IT administrators deploying AutoPackager in production environments.
> All 13 configuration sections documented with environment variable mapping, valid values, and common scenarios.

---

## 1. Overview — Configuration Architecture

AutoPackager uses a **hybrid configuration system** combining YAML static configuration with environment variable injection. This design separates secrets (credentials, API keys) from configuration structure.

### Configuration File Location
```
autopackager/config/config.yaml
```

### Environment Variable Substitution

Config.yaml supports `${VAR_NAME}` syntax for environment variable substitution. At runtime, the configuration loader (defined in `autopackager/utils/config.py`) reads `.env` from the project root and replaces all `${VAR_NAME}` placeholders with actual values.

**Example:**
```yaml
intune:
  tenant_id: "${AZURE_TENANT_ID}"
  client_id: "${AZURE_CLIENT_ID}"
  client_secret: "${AZURE_CLIENT_SECRET}"
```

When `.env` contains:
```bash
AZURE_TENANT_ID=a1b2c3d4-e5f6-7890-abcd-ef1234567890
AZURE_CLIENT_ID=12345678-1234-1234-1234-123456789012
AZURE_CLIENT_SECRET=your_secret_value_here
```

The resulting loaded config will have actual values substituted.

**Gotcha:** If an environment variable is **not defined**, the substitution leaves the placeholder unchanged (e.g., `"${MISSING_VAR}"`). This causes runtime errors. Always verify your `.env` file against `.env.template` before deployment.

### Configuration Sections (13 Total)

| Section | Purpose | Environment Variables Required |
|---------|---------|-------------------------------|
| `database` | Database connection settings | `DB_PASSWORD` (PostgreSQL only) |
| `redis` | Redis connection for Celery | None (localhost defaults) |
| `intune` | Microsoft Graph API credentials | `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET` |
| `llm` | LLM provider configuration | `LLM_API_KEY` |
| `oem_catalogs` | Dell/HP/Lenovo driver catalog sources | None |
| `deployment_rings` | Phased rollout group mapping | `RING0_GROUP_ID`, `RING1_GROUP_ID`, `RING2_GROUP_ID`, `RING3_GROUP_ID` |
| `paths` | Local filesystem directories | None |
| `testing` | Test harness configuration (with nested `vm_config`) | `AZURE_TEST_RG`, `AZURE_VM_ADMIN_PASSWORD` (Azure VMs only) |
| `logging` | Log format and verbosity | None |
| `jobs` | Job retry and concurrency | None |
| `status_polling` | Device status polling intervals | None |
| `discovery_schedule` | Continuous catalog discovery schedule and monitored hardware models | `DISCOVERY_NOTIFICATION_EMAIL` (optional) |
| `dashboard` | Optional FastAPI web dashboard CORS overrides | None |

---

## 2. Database Configuration

Controls where AutoPackager stores job state, driver metadata, and deployment history.

### Fields

| Field | Type | Valid Values | Default | Description |
|-------|------|--------------|---------|-------------|
| `type` | string | `"sqlite"`, `"postgresql"` | `"sqlite"` | Database engine. Use SQLite for dev/test; PostgreSQL for production. |
| `path` | string | File path | `"data/autopackager.db"` | **SQLite only**: Relative or absolute path to database file. Ignored for PostgreSQL. |
| `host` | string | Hostname/IP | (not in default config) | **PostgreSQL only**: Database server hostname. Add if using PostgreSQL. |
| `port` | integer | Port number | `5432` | **PostgreSQL only**: Database server port. |
| `database` | string | Database name | `"autopackager"` | **PostgreSQL only**: Database name. |
| `user` | string | Username | `"autopackager_user"` | **PostgreSQL only**: Database username. |
| `password` | string | Password or `${DB_PASSWORD}` | N/A | **PostgreSQL only**: Database password. Use environment variable for production. |

### Examples

#### SQLite (Default — Development/Testing)
```yaml
database:
  type: "sqlite"
  path: "data/autopackager.db"
```

**Use when:** Single-server deployment, <500 devices, no HA requirement.

#### PostgreSQL (Production)
```yaml
database:
  type: "postgresql"
  host: "db.contoso.local"
  port: 5432
  database: "autopackager"
  user: "autopackager_user"
  password: "${DB_PASSWORD}"
```

**Use when:** Multi-server deployment, >500 devices, HA/clustering required.

**Gotcha:** PostgreSQL requires manual database creation **before** running `python cli.py init`. See SETUP.md section 5 for SQL commands. SQLite auto-creates the file on first run.

---

## 3. Redis Configuration

Redis is the message broker for Celery (background task queue). The worker processes driver discovery, packaging, and deployment jobs asynchronously via Redis.

### Fields

| Field | Type | Valid Values | Default | Description |
|-------|------|--------------|---------|-------------|
| `host` | string | Hostname/IP | `"localhost"` | Redis server hostname. Use `"localhost"` for single-server; use external hostname for multi-server. |
| `port` | integer | Port number | `6379` | Redis server port (6379 is Redis default). |
| `db` | integer | 0-15 | `0` | Redis database number. Use 0 unless you have multiple applications sharing Redis. |

### Example

```yaml
redis:
  host: "localhost"
  port: 6379
  db: 0
```

### Multi-Server Setup

For HA deployments with multiple Celery workers on different servers, point all workers to a central Redis instance:

```yaml
redis:
  host: "redis.contoso.local"
  port: 6379
  db: 0
```

**Gotcha:** Redis must be running **before** starting the Celery worker. Test connectivity with `redis-cli -h localhost -p 6379 ping` (should return `PONG`). See SETUP.md section 6 for installation steps.

**Performance Note:** AutoPackager does not require Redis persistence. If you're using a dedicated Redis instance, disabling snapshots (`save ""` in `redis.conf`) can improve performance.

---

## 4. Intune Configuration

Microsoft Graph API credentials for deploying packages to Intune and assigning to device groups.

### Fields

| Field | Type | Valid Values | Default | Description |
|-------|------|--------------|---------|-------------|
| `tenant_id` | string | GUID or `${AZURE_TENANT_ID}` | **Required** | Azure AD tenant ID (from Azure Portal → App Registrations). |
| `client_id` | string | GUID or `${AZURE_CLIENT_ID}` | **Required** | App Registration client ID (application ID). |
| `client_secret` | string | Secret or `${AZURE_CLIENT_SECRET}` | **Required** | App Registration client secret. **Must use environment variable in production.** |
| `graph_api_version` | string | `"v1.0"`, `"beta"` | `"v1.0"` | Graph API version. Use `"v1.0"` for production; `"beta"` for preview features. |
| `graph_endpoint` | string | URL | `"https://graph.microsoft.com"` | Graph API base URL. Leave default unless using sovereign clouds (e.g., `https://graph.microsoft.us` for GCC High). |

### Example

```yaml
intune:
  tenant_id: "${AZURE_TENANT_ID}"
  client_id: "${AZURE_CLIENT_ID}"
  client_secret: "${AZURE_CLIENT_SECRET}"
  graph_api_version: "v1.0"
  graph_endpoint: "https://graph.microsoft.com"
```

### Required App Registration Permissions

The App Registration **must** have these **Application-type** permissions (not Delegated) with **admin consent granted**:

- `DeviceManagementApps.ReadWrite.All`
- `DeviceManagementConfiguration.ReadWrite.All`
- `DeviceManagementManagedDevices.PrivilegedOperations.All`
- `Group.Read.All`
- `GroupMember.Read.All`
- `GroupMember.ReadWrite.All`

**Setup Steps:**
1. Azure Portal → App Registrations → New Registration
2. Name: `AutoPackager-ServicePrincipal`
3. API Permissions → Add permissions → Microsoft Graph → Application permissions
4. Add the 4 permissions above
5. Grant admin consent for [your tenant]
6. Certificates & secrets → New client secret → Copy value to `.env`

**Automated Setup:** Run `.\Install-AutoPackager.ps1` (Windows) — it creates the App Registration and grants permissions automatically.

**Gotcha:** Client secrets **expire**. Default expiry is 1-2 years. Set a calendar reminder 1 month before expiry to rotate the secret. Expired secrets cause `401 Unauthorized` errors in deployment logs.

---

## 5. LLM Configuration

LLM (Large Language Model) integration for intelligent driver metadata extraction, installation script generation, and detection script creation. AutoPackager supports OpenAI, Azure OpenAI, and Anthropic.

### Fields

| Field | Type | Valid Values | Default | Description |
|-------|------|--------------|---------|-------------|
| `provider` | string | `"openai"`, `"azure_openai"`, `"anthropic"` | `"openai"` | LLM provider. |
| `api_key` | string | API key or `${LLM_API_KEY}` | **Required** | LLM API key. **Must use environment variable in production.** |
| `model` | string | Model name | `"gpt-4-turbo-preview"` | Model identifier. Valid values depend on provider (see table below). |
| `temperature` | float | 0.0 - 2.0 | `0.2` | Sampling temperature. Lower = more deterministic. Keep 0.1-0.3 for code generation. |
| `max_tokens` | integer | 1 - model limit | `4096` | Maximum output tokens. Higher = longer responses but slower/costlier. |

### Provider-Specific Model Values

| Provider | `provider` Value | Common `model` Values |
|----------|------------------|----------------------|
| OpenAI | `"openai"` | `"gpt-4-turbo-preview"`, `"gpt-4"`, `"gpt-3.5-turbo"` |
| Azure OpenAI | `"azure_openai"` | Your deployment name (e.g., `"gpt4-deployment"`) |
| Anthropic | `"anthropic"` | `"claude-3-opus-20240229"`, `"claude-3-sonnet-20240229"`, `"claude-3-haiku-20240307"` |

### Examples

#### OpenAI (Default)
```yaml
llm:
  provider: "openai"
  api_key: "${LLM_API_KEY}"
  model: "gpt-4-turbo-preview"
  temperature: 0.2
  max_tokens: 4096
```

**Get API key:** https://platform.openai.com/api-keys

#### Azure OpenAI (Enterprise — Private Endpoint)
```yaml
llm:
  provider: "azure_openai"
  api_key: "${LLM_API_KEY}"
  model: "gpt4-deployment"  # Your Azure deployment name
  temperature: 0.2
  max_tokens: 4096
  azure_endpoint: "${AZURE_OPENAI_ENDPOINT}"  # Add this field
```

**Additional `.env` variables for Azure OpenAI:**
```bash
LLM_API_KEY=your_azure_openai_key
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/
```

#### Anthropic Claude
```yaml
llm:
  provider: "anthropic"
  api_key: "${LLM_API_KEY}"
  model: "claude-3-sonnet-20240229"
  temperature: 0.2
  max_tokens: 4096
```

**Get API key:** https://console.anthropic.com/settings/keys

**Cost Guidance:**
- **Development/Testing:** Use `gpt-3.5-turbo` (OpenAI) or `claude-3-haiku` (Anthropic) — significantly cheaper, good enough for testing.
- **Production:** Use `gpt-4-turbo-preview` (OpenAI) or `claude-3-sonnet` (Anthropic) — better at complex driver metadata extraction and PowerShell script generation.

**Gotcha:** `temperature` values above 0.5 can cause non-deterministic script generation, making testing unreliable. Keep ≤0.3 for production.

---

## 6. OEM Catalogs Configuration

Controls which OEM (Dell, HP, Lenovo) driver catalogs AutoPackager downloads and indexes. Each vendor publishes driver metadata in different formats (CAB, XML) at different URLs.

### Structure

```yaml
oem_catalogs:
  <vendor>:
    enabled: true/false
    catalog_url: "<URL>"
    catalog_path: "data/catalogs/<vendor>"
    # Optional vendor-specific fields
```

### Fields (Per Vendor)

| Field | Type | Description |
|-------|------|-------------|
| `enabled` | boolean | `true` to index this vendor's drivers; `false` to skip. Disable unused vendors to speed up catalog refresh. |
| `catalog_url` | string | URL to download vendor's driver catalog metadata file. |
| `catalog_path` | string | Local directory to store downloaded catalog files. |
| `base_url` | string | **Dell only**: Base URL for constructing driver download URLs. |

### Default Configuration

```yaml
oem_catalogs:
  dell:
    enabled: true
    catalog_url: "https://downloads.dell.com/catalog/DriverPackCatalog.cab"
    base_url: "https://downloads.dell.com/"
    catalog_path: "data/catalogs/dell"
  hp:
    enabled: true
    catalog_url: "https://hpia.hpcloud.hp.com/ref/platformlist.cab"
    catalog_path: "data/catalogs/hp"
  lenovo:
    enabled: true
    catalog_url: "https://download.lenovo.com/cdrt/td/catalogv2.xml"
    catalog_path: "data/catalogs/lenovo"
```

### Common Customizations

#### Disable Unused Vendors

If your organization only uses Dell and HP devices:

```yaml
oem_catalogs:
  dell:
    enabled: true
    catalog_url: "https://downloads.dell.com/catalog/DriverPackCatalog.cab"
    base_url: "https://downloads.dell.com/"
    catalog_path: "data/catalogs/dell"
  hp:
    enabled: true
    catalog_url: "https://hpia.hpcloud.hp.com/ref/platformlist.cab"
    catalog_path: "data/catalogs/hp"
  lenovo:
    enabled: false  # Skip Lenovo catalog downloads
    catalog_url: "https://download.lenovo.com/cdrt/td/catalogv2.xml"
    catalog_path: "data/catalogs/lenovo"
```

**Benefit:** Faster catalog refresh (runs as scheduled job). Each catalog download takes 2-5 minutes; disabling unused vendors saves time.

#### Air-Gapped / Proxy Environments

If your environment blocks direct internet access, download catalogs manually and update `catalog_url` to point to an internal file share:

```yaml
oem_catalogs:
  dell:
    enabled: true
    catalog_url: "file:///\\fileserver\catalogs\dell\DriverPackCatalog.cab"
    base_url: "file:///\\fileserver\dell-drivers\"
    catalog_path: "data/catalogs/dell"
```

**Note:** AutoPackager still needs internet access to download actual driver installers. For fully air-gapped deployments, you'll need to pre-download drivers to a local share and modify the download agent.

**Gotcha:** Catalog URLs occasionally change when vendors update infrastructure. If catalog refresh fails with `404 Not Found`, check the vendor's support site for the new URL.

---

## 7. Deployment Rings Configuration

Defines phased rollout groups for driver deployments. AutoPackager assigns packages to Entra ID groups based on deployment ring strategy (IT Pilot → Early Adopters → Broad Deployment → Critical Systems).

### Structure

```yaml
deployment_rings:
  - name: "<ring name>"
    ring_id: "<internal identifier>"
    entra_group_id: "${ENV_VAR}"
    deferral_days: <integer>
```

Each ring is a YAML list item (note the `-` prefix).

### Fields (Per Ring)

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Human-readable ring name (for logs/UI). |
| `ring_id` | string | Internal identifier used in code (e.g., `ring0`, `ring1`). Must be unique. |
| `entra_group_id` | string | Entra ID (Azure AD) group object ID. **Use environment variable for production.** |
| `deferral_days` | integer | Days to wait after Ring 0 deployment before deploying to this ring. Ring 0 should be `0`. |

### Default Configuration

```yaml
deployment_rings:
  - name: "IT Pilot"
    ring_id: "ring0"
    entra_group_id: "${RING0_GROUP_ID}"
    deferral_days: 0
  - name: "Early Adopters"
    ring_id: "ring1"
    entra_group_id: "${RING1_GROUP_ID}"
    deferral_days: 3
  - name: "Broad Deployment"
    ring_id: "ring2"
    entra_group_id: "${RING2_GROUP_ID}"
    deferral_days: 7
  - name: "Critical Systems"
    ring_id: "ring3"
    entra_group_id: "${RING3_GROUP_ID}"
    deferral_days: 14
```

**Corresponding `.env` entries:**
```bash
RING0_GROUP_ID=12345678-1234-1234-1234-123456789012
RING1_GROUP_ID=abcdefab-abcd-abcd-abcd-abcdefabcdef
RING2_GROUP_ID=fedcbafe-fedc-fedc-fedc-fedcbafedcba
RING3_GROUP_ID=09876543-0987-0987-0987-098765432109
```

### Group Creation

**Option 1: Automated** — `.\Install-AutoPackager.ps1` creates these groups automatically:
- `AutoPackager-Ring0-ITPilot`
- `AutoPackager-Ring1-EarlyAdopters`
- `AutoPackager-Ring2-BroadDeployment`
- `AutoPackager-Ring3-CriticalSystems`

**Option 2: Manual** — Entra ID → Groups → New group → Security → Name → Create. Copy Object ID to `.env`.

### Ring Strategy Best Practices

| Ring | Target Audience | Device Count | Purpose |
|------|----------------|--------------|---------|
| Ring 0 (IT Pilot) | IT staff, test devices | 5-20 | Initial validation. Should include at least one device per hardware model in your estate. |
| Ring 1 (Early Adopters) | Volunteer users, non-critical | 50-200 | Real-world validation. Users who tolerate issues and provide feedback. |
| Ring 2 (Broad Deployment) | General user population | 80% of estate | Standard rollout after rings 0-1 validate stability. |
| Ring 3 (Critical Systems) | Executives, production, clinical | High-stability only | Deploy last after extended validation. Often manual approval. |

### Customization Example — Conservative Healthcare Deployment

```yaml
deployment_rings:
  - name: "IT Lab"
    ring_id: "ring0"
    entra_group_id: "${RING0_GROUP_ID}"
    deferral_days: 0
  - name: "Administrative Staff"
    ring_id: "ring1"
    entra_group_id: "${RING1_GROUP_ID}"
    deferral_days: 7  # Wait 1 week
  - name: "Clinical Non-Critical"
    ring_id: "ring2"
    entra_group_id: "${RING2_GROUP_ID}"
    deferral_days: 14  # Wait 2 weeks
  - name: "Clinical Workstations"
    ring_id: "ring3"
    entra_group_id: "${RING3_GROUP_ID}"
    deferral_days: 30  # Wait 1 month
  - name: "Medical Devices (FDA-regulated)"
    ring_id: "ring4"
    entra_group_id: "${RING4_GROUP_ID}"
    deferral_days: 90  # Manual approval, long validation
```

**Note:** You can add as many rings as needed. Just ensure `ring_id` values are unique.

**Gotcha:** Deferral days are relative to Ring 0 deployment, **not** the previous ring. If Ring 0 deploys on Jan 1, Ring 2 (`deferral_days: 7`) deploys on Jan 8, even if Ring 1 (`deferral_days: 3`, Jan 4) had issues. Use the jobs dashboard to manually pause ring progression if needed.

---

## 8. Paths Configuration

Local filesystem directories for downloads, packages, logs, and tooling. All paths are relative to the project root unless specified as absolute.

### Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `downloads` | string | `"data/downloads"` | Directory for downloaded driver installers (before packaging). |
| `packages` | string | `"data/packages"` | Directory for generated .intunewin packages (after packaging). |
| `logs` | string | `"data/logs"` | Directory for application logs (separate from job-specific logs). |
| `psadt_template` | string | `"scripts/psadt"` | Directory containing PSAppDeployToolkit template for wrapping installers. |
| `intunewin_util` | string | `"tools/IntuneWinAppUtil.exe"` | Path to Microsoft IntuneWinAppUtil.exe (content prep tool). |

### Default Configuration

```yaml
paths:
  downloads: "data/downloads"
  packages: "data/packages"
  logs: "data/logs"
  psadt_template: "scripts/psadt"
  intunewin_util: "tools/IntuneWinAppUtil.exe"
```

### Customization Examples

#### Network Share for Package Storage

If you want packages stored on a central file share (for multi-server deployments):

```yaml
paths:
  downloads: "data/downloads"  # Keep local (temp files)
  packages: "\\\\fileserver\\autopackager\\packages"  # UNC path
  logs: "data/logs"
  psadt_template: "scripts/psadt"
  intunewin_util: "tools/IntuneWinAppUtil.exe"
```

**Gotcha:** UNC paths require the Celery worker service account to have **write permissions** on the share. Test with `New-Item \\fileserver\autopackager\packages\test.txt` from the worker server.

#### Separate Disk for High I/O

If you're processing many large driver packages (e.g., Dell BIOS updates >500MB), move `downloads` and `packages` to a dedicated high-speed disk:

```yaml
paths:
  downloads: "E:\\autopackager-data\\downloads"
  packages: "E:\\autopackager-data\\packages"
  logs: "data/logs"
  psadt_template: "scripts/psadt"
  intunewin_util: "tools/IntuneWinAppUtil.exe"
```

**Performance Note:** IntuneWinAppUtil.exe is **I/O-intensive**. SSDs significantly reduce packaging time (3-5x faster than spinning disks for large packages).

**Gotcha:** If `intunewin_util` path is incorrect or the file is missing, packaging jobs fail with `FileNotFoundError`. The `.\Install-AutoPackager.ps1` script downloads this automatically to `tools/IntuneWinAppUtil.exe`.

---

## 9. Testing Configuration

Controls automated installation testing. AutoPackager can optionally test packages on a VM before deploying to Intune, validating that the installer runs successfully and detection scripts work correctly.

### Top-Level Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | boolean | `true` | Global testing toggle. If `false`, skips all testing stages (packages deploy directly to Intune). |
| `vm_testing_enabled` | boolean | `false` | VM-based installation testing toggle. **Must explicitly enable** (default `false` for backward compatibility). |
| `vm_provider` | string | `"local"` | VM provider: `"local"` (Hyper-V) or `"azure"` (Azure VMs). |
| `snapshot_name` | string | `"clean_windows_11"` | **Deprecated — use `vm_config.hyperv.snapshot_name` instead.** Kept for backward compatibility. |
| `timeout_minutes` | integer | `30` | **Deprecated — use `vm_config.timeout_minutes` instead.** Global timeout for test operations. |

### Nested Section: `vm_config`

The `vm_config` section contains provider-specific settings. Structure:

```yaml
testing:
  vm_config:
    hyperv:
      # Hyper-V settings
    azure:
      # Azure settings
    # Common settings
    timeout_minutes: 30
    max_retries: 2
    cleanup_on_failure: true
```

#### Hyper-V Provider Settings (`vm_config.hyperv`)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `vm_name` | string | `"AutoPackager-TestVM"` | Hyper-V VM name. Must exist before enabling VM testing. |
| `snapshot_name` | string | `"clean_windows_11"` | Hyper-V snapshot/checkpoint name. VM reverts to this snapshot before each test. |
| `switch_name` | string | `"Default Switch"` | Hyper-V virtual switch name. Use `"Default Switch"` or your custom switch. |
| `memory_mb` | integer | `4096` | VM memory allocation in MB. 4GB minimum for Windows 11. |
| `processors` | integer | `2` | Number of virtual CPUs. Minimum 2 recommended. |
| `boot_timeout_seconds` | integer | `300` | Max seconds to wait for VM to boot and become network-accessible. |

#### Azure Provider Settings (`vm_config.azure`)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `resource_group` | string | `"${AZURE_TEST_RG}"` | Azure resource group for test VMs. **Use environment variable.** |
| `vm_size` | string | `"Standard_D2s_v3"` | Azure VM SKU. `Standard_D2s_v3` = 2 vCPU, 8GB RAM. |
| `image_reference` | object | See below | OS image reference (publisher/offer/SKU/version). |
| `admin_username` | string | `"azureuser"` | VM admin username. |
| `admin_password` | string | `"${AZURE_VM_ADMIN_PASSWORD}"` | VM admin password. **Use environment variable.** |
| `location` | string | `"eastus"` | Azure region. Use region closest to your Intune tenant. |
| `boot_timeout_seconds` | integer | `600` | Max seconds to wait for Azure VM to deploy and boot. |

**Azure `image_reference` structure:**
```yaml
image_reference:
  publisher: "MicrosoftWindowsDesktop"
  offer: "Windows-11"
  sku: "win11-22h2-pro"
  version: "latest"
```

#### Common Settings (All Providers)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `timeout_minutes` | integer | `30` | Max minutes for entire test operation (install + detect + cleanup). |
| `max_retries` | integer | `2` | Number of retries if test fails due to transient errors (network, VM boot). |
| `cleanup_on_failure` | boolean | `true` | If `true`, delete/revert VM even if test fails. If `false`, leave VM running for troubleshooting. |

### Examples

#### VM Testing Disabled (Default)

```yaml
testing:
  enabled: true
  vm_testing_enabled: false  # No VM testing
  vm_provider: "local"
  snapshot_name: "clean_windows_11"
  timeout_minutes: 30
```

**Behavior:** Packages skip installation testing and deploy directly to Intune after packaging.

**Use when:** Initial setup, development, or when you trust vendor-provided installers without validation.

#### Hyper-V Local Testing (Recommended for Single-Server)

```yaml
testing:
  enabled: true
  vm_testing_enabled: true  # Enable VM testing
  vm_provider: "local"  # Use Hyper-V

  vm_config:
    hyperv:
      vm_name: "AutoPackager-TestVM"
      snapshot_name: "clean_windows_11"
      switch_name: "Default Switch"
      memory_mb: 4096
      processors: 2
      boot_timeout_seconds: 300

    timeout_minutes: 30
    max_retries: 2
    cleanup_on_failure: true
```

**Prerequisites:**
1. Windows Server or Windows 10/11 Pro with Hyper-V enabled
2. A VM named `AutoPackager-TestVM` created in Hyper-V Manager
3. A snapshot named `clean_windows_11` on that VM (clean Windows install with network connectivity)

**Setup Steps (Hyper-V):**
1. Hyper-V Manager → New → Virtual Machine → Name: `AutoPackager-TestVM`
2. Assign memory: 4096 MB
3. Connect to virtual switch: `Default Switch`
4. Install Windows 11 Pro
5. Run Windows Update, install any prerequisite software
6. Hyper-V Manager → Right-click VM → Checkpoint → Name: `clean_windows_11`

**Gotcha:** If the snapshot doesn't exist, testing fails with `SnapshotNotFoundError`. Create snapshots using Hyper-V Manager (right-click VM → Checkpoint).

#### Azure Cloud Testing (Multi-Server/HA Deployments)

```yaml
testing:
  enabled: true
  vm_testing_enabled: true  # Enable VM testing
  vm_provider: "azure"  # Use Azure VMs

  vm_config:
    azure:
      resource_group: "${AZURE_TEST_RG}"
      vm_size: "Standard_D2s_v3"
      image_reference:
        publisher: "MicrosoftWindowsDesktop"
        offer: "Windows-11"
        sku: "win11-22h2-pro"
        version: "latest"
      admin_username: "azureuser"
      admin_password: "${AZURE_VM_ADMIN_PASSWORD}"
      location: "eastus"
      boot_timeout_seconds: 600

    timeout_minutes: 30
    max_retries: 2
    cleanup_on_failure: true
```

**Additional `.env` variables:**
```bash
AZURE_TEST_RG=autopackager-testing-rg
AZURE_VM_ADMIN_PASSWORD=YourSecurePassword123!
```

**Behavior:** AutoPackager creates a new Azure VM for each test, runs the installer, validates detection, then **deletes the VM**. Clean state for every test.

**Cost Note:** Azure VMs are billed per-minute. `Standard_D2s_v3` costs ~$0.096/hour (~$0.002/minute). Each test takes 5-10 minutes, so cost per test is ~$0.01-0.02. Budget accordingly for high-volume deployments.

**Gotcha:** Azure VM creation requires additional API permissions beyond Graph API. The service principal needs `Virtual Machine Contributor` role on the resource group. The `.\Install-AutoPackager.ps1` script does **not** configure this automatically — you must grant it manually in Azure Portal.

---

## 10. Logging Configuration

Controls log output format, verbosity, and destination.

### Fields

| Field | Type | Valid Values | Default | Description |
|-------|------|--------------|---------|-------------|
| `level` | string | `"DEBUG"`, `"INFO"`, `"WARNING"`, `"ERROR"`, `"CRITICAL"` | `"INFO"` | Minimum log level to record. `DEBUG` = verbose (development); `INFO` = standard (production). |
| `format` | string | `"json"`, `"text"` | `"json"` | Log output format. Use `json` for machine parsing (Splunk, ELK); `text` for human readability. |
| `file` | string | File path | `"data/logs/autopackager.log"` | Log file path. Logs are appended (not rotated automatically). |

### Default Configuration

```yaml
logging:
  level: "INFO"
  format: "json"
  file: "data/logs/autopackager.log"
```

### Log Levels Explained

| Level | What Gets Logged | Use Case |
|-------|------------------|----------|
| `DEBUG` | Every function call, API request/response, variable values | Troubleshooting complex issues, development |
| `INFO` | Job start/complete, major steps, API calls | Standard production logging (recommended) |
| `WARNING` | Recoverable errors, retries, deprecated config | Production monitoring |
| `ERROR` | Job failures, API errors, exceptions | Production alerts |
| `CRITICAL` | System failures, database corruption | Immediate escalation |

### Examples

#### Development — Verbose Text Logs

```yaml
logging:
  level: "DEBUG"
  format: "text"
  file: "data/logs/autopackager.log"
```

**Output format:**
```
2026-03-24 14:32:01 - INFO - Job 12345 started: Dell Latitude 5420 Chipset Driver
2026-03-24 14:32:05 - DEBUG - Downloading from https://downloads.dell.com/...
2026-03-24 14:32:47 - INFO - Download complete: 45.2 MB in 42 seconds
```

#### Production — JSON Logs for SIEM

```yaml
logging:
  level: "INFO"
  format: "json"
  file: "data/logs/autopackager.log"
```

**Output format:**
```json
{"timestamp": "2026-03-24T14:32:01Z", "level": "INFO", "message": "Job 12345 started: Dell Latitude 5420 Chipset Driver", "job_id": "12345", "vendor": "dell", "model": "Latitude 5420"}
{"timestamp": "2026-03-24T14:32:47Z", "level": "INFO", "message": "Download complete: 45.2 MB in 42 seconds", "job_id": "12345", "bytes": 47400960, "duration_seconds": 42}
```

**Benefit:** JSON logs can be ingested into Splunk, ELK, Azure Monitor, etc., for dashboards and alerting.

#### Network Share Logs (Multi-Server HA)

```yaml
logging:
  level: "INFO"
  format: "json"
  file: "\\\\fileserver\\autopackager-logs\\autopackager.log"
```

**Use when:** Multiple Celery workers need centralized logging.

**Gotcha:** AutoPackager does **not rotate logs automatically**. Large deployments can generate GB of logs per month. Set up log rotation via:
- **Windows:** Task Scheduler running `Compress-Archive` + `Remove-Item` weekly
- **Linux:** `logrotate` with daily/weekly rotation

Example `logrotate` config:
```
/path/to/data/logs/autopackager.log {
    daily
    rotate 30
    compress
    missingok
    notifempty
    create 0644 celery celery
}
```

---

## 11. Jobs Configuration

Controls job retry behavior and concurrency limits for the Celery worker.

### Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `max_retries` | integer | `3` | Number of retries for failed jobs before marking as permanently failed. |
| `retry_delay_seconds` | integer | `300` | Delay in seconds between retries (5 minutes default). |
| `concurrent_jobs` | integer | `5` | Maximum number of jobs the Celery worker processes in parallel. |

### Default Configuration

```yaml
jobs:
  max_retries: 3
  retry_delay_seconds: 300
  concurrent_jobs: 5
```

### Retry Behavior

When a job fails (e.g., network error downloading driver, API rate limit), AutoPackager automatically retries with exponential backoff:

1. First failure: Wait `retry_delay_seconds` (5 minutes), retry
2. Second failure: Wait `retry_delay_seconds * 2` (10 minutes), retry
3. Third failure: Wait `retry_delay_seconds * 3` (15 minutes), retry
4. After `max_retries` (3), mark job as `failed_permanent`

**Gotcha:** Transient errors (network timeouts, Graph API throttling) are retried. **Permanent errors** (invalid credentials, file not found) skip retries and fail immediately.

### Concurrency Tuning

`concurrent_jobs` controls how many packaging jobs run simultaneously. Recommendations:

| Server Specs | Recommended `concurrent_jobs` | Reasoning |
|--------------|-------------------------------|-----------|
| 4 CPU cores, 8GB RAM | `2-3` | IntuneWinAppUtil.exe is CPU-intensive; avoid overloading |
| 8 CPU cores, 16GB RAM | `5` (default) | Balanced for typical enterprise workload |
| 16 CPU cores, 32GB RAM | `10-15` | High-volume deployments (500+ drivers/month) |

**Gotcha:** Setting `concurrent_jobs` too high can cause:
- **CPU starvation** (system becomes unresponsive)
- **Disk I/O bottlenecks** (especially on spinning disks)
- **API rate limiting** (Graph API has per-tenant limits)

**Performance Note:** If jobs are queuing (status `pending` for >30 minutes), increase `concurrent_jobs`. If server CPU is consistently >80%, decrease it.

### Example — High-Volume Deployment

```yaml
jobs:
  max_retries: 5  # More retries for large-scale (transient errors more likely)
  retry_delay_seconds: 600  # 10 minutes (Graph API throttling)
  concurrent_jobs: 10  # Process 10 jobs in parallel
```

**Use when:** Enterprise with 500+ unique hardware models, processing 50+ driver updates/day.

### Example — Conservative/Low-Resource

```yaml
jobs:
  max_retries: 2  # Fail fast
  retry_delay_seconds: 300
  concurrent_jobs: 2  # Low resource usage
```

**Use when:** Running on a small VM (2 CPU, 4GB RAM) or sharing server with other applications.

---

## 12. Status Polling Configuration

Controls how frequently AutoPackager queries Intune for device installation status after deploying packages. This data populates the deployment dashboard and triggers ring progression.

### Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | boolean | `true` | Enable/disable status polling. If `false`, deployments complete without status tracking. |
| `polling_interval_minutes` | integer | `15` | How often (in minutes) to poll Intune for installation status. |
| `max_devices_per_poll` | integer | `1000` | Maximum devices to query per polling cycle (Graph API pagination limit). |
| `retry_on_rate_limit` | boolean | `true` | If `true`, automatically retry when hitting Graph API rate limits. If `false`, skip until next interval. |

### Default Configuration

```yaml
status_polling:
  enabled: true
  polling_interval_minutes: 15
  max_devices_per_poll: 1000
  retry_on_rate_limit: true
```

### How Status Polling Works

1. After deploying a package to Ring 0, AutoPackager starts a background polling job
2. Every `polling_interval_minutes` (15 min), it queries Graph API:
   ```
   GET /deviceManagement/mobileApps/{appId}/deviceStatuses
   ```
3. Aggregates results: `installed`, `failed`, `pending`, `not_applicable`
4. When Ring 0 reaches success threshold (e.g., 95% installed, <5% failed), triggers Ring 1 deployment
5. Continues polling Rings 1-3 until all complete or timeout

### Polling Interval Tuning

| Interval | Use Case | API Load |
|----------|----------|----------|
| 5 minutes | Critical deployments (BIOS updates, security drivers) | High — may hit rate limits with >1000 devices |
| 15 minutes (default) | Standard deployments | Moderate — balanced |
| 60 minutes | Non-critical, low-priority updates | Low — minimal API usage |

**Gotcha:** Intune caches device status for 5-10 minutes. Polling more frequently than every 5 minutes doesn't get fresher data and wastes API quota.

### Rate Limiting

Graph API has tenant-wide rate limits (~2000 requests/minute). Each status poll makes 1 request per `max_devices_per_poll` devices (1 request for 0-1000 devices, 2 requests for 1001-2000, etc.).

**If you hit rate limits:**
1. Increase `polling_interval_minutes` (e.g., 15 → 30)
2. Decrease `max_devices_per_poll` (e.g., 1000 → 500) — spreads load across more intervals
3. Enable `retry_on_rate_limit: true` (default) — automatically backs off and retries

**Rate limit errors look like:**
```json
{"error": {"code": "TooManyRequests", "message": "Rate limit exceeded. Retry after 60 seconds."}}
```

With `retry_on_rate_limit: true`, AutoPackager waits the requested duration (60s) and retries automatically.

### Example — Large Deployment (5000+ Devices)

```yaml
status_polling:
  enabled: true
  polling_interval_minutes: 30  # Slower polling to avoid rate limits
  max_devices_per_poll: 500  # Smaller batches
  retry_on_rate_limit: true
```

**Reasoning:** 5000 devices / 500 per poll = 10 API calls per polling cycle. At 30-minute intervals, that's 20 calls/hour (well below rate limits).

### Example — Disable Status Polling

```yaml
status_polling:
  enabled: false
  polling_interval_minutes: 15
  max_devices_per_poll: 1000
  retry_on_rate_limit: true
```

**Use when:** You don't need automated ring progression and rely on manual promotion via the dashboard.

**Gotcha:** If `enabled: false`, the deployment dashboard will show "Status Unknown" for all deployments. Intune still deploys packages — you just don't get visibility in AutoPackager.

---

## 13. Discovery Schedule Configuration

Controls the **continuous catalog discovery** background task. When enabled, Celery Beat runs `autopackager.continuous_catalog_discovery` on a fixed interval, scanning each configured OEM catalog for new driver versions and creating packaging jobs for any new versions found in `monitored_models`. Implementation lives in `autopackager/orchestration/tasks.py` (`continuous_catalog_discovery`) and is wired into Beat in `autopackager/orchestration/celery_app.py`.

### Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | boolean | `true` | Master toggle. When `false`, the task is not registered with Beat and will return `{'status': 'disabled'}` if invoked manually. |
| `interval_hours` | number | `24` | How often (in hours) Beat triggers the discovery task. Fractional values are allowed for testing (e.g., `0.0167` ≈ 1 minute). |
| `catalogs` | list | `[dell, hp, lenovo]` | Informational list of OEMs the task is expected to scan. Actual scanning is driven by `monitored_models`. |
| `notification_email` | string | `${DISCOVERY_NOTIFICATION_EMAIL}` | Optional email for future notifications. Currently informational. |
| `retry_on_failure` | boolean | `true` | Whether the Celery task retries with exponential backoff on uncaught exceptions. |
| `max_retries` | integer | `3` | Maximum Celery retries before giving up for the current run. |
| `monitored_models` | list of objects | (one Dell sample) | Models to scan on every run. Each item: `vendor`, `model`, `driver_type` (use `"all"` for everything), `current_version`. |

### Default Configuration

```yaml
discovery_schedule:
  enabled: true
  interval_hours: 24
  catalogs:
    - dell
    - hp
    - lenovo
  notification_email: "${DISCOVERY_NOTIFICATION_EMAIL}"
  retry_on_failure: true
  max_retries: 3
  monitored_models:
    - vendor: "Dell"
      model: "Latitude 7400"
      driver_type: "all"
      current_version: "1.0.0"
```

### How It Works

1. Beat fires `autopackager.continuous_catalog_discovery` every `interval_hours`.
2. The task creates a `DiscoveryRun` row (`discovery_runs` table) to track the scan.
3. For each entry in `monitored_models`, it calls the `DiscoveryAgent` against the matching OEM catalog.
4. If a newer version is found, it checks the `jobs` table for a non-terminal job at the same `vendor`/`hardware_model`/`target_version`. If none exists, it enqueues `create_packaging_job` and the standard pipeline takes over.
5. The `DiscoveryRun` is updated with `catalogs_scanned`, `new_versions_found`, `jobs_created`, and a per-OEM breakdown in `oem_results`.

### Example — Disable Continuous Discovery

```yaml
discovery_schedule:
  enabled: false
```

Use when you only want operator-driven jobs created via `python cli.py create-driver-job`.

### Example — Multiple Models, Faster Cadence

```yaml
discovery_schedule:
  enabled: true
  interval_hours: 6
  monitored_models:
    - vendor: "Dell"
      model: "Latitude 5420"
      driver_type: "all"
      current_version: "1.0.0"
    - vendor: "HP"
      model: "EliteBook 850 G8"
      driver_type: "network"
      current_version: "2.1.0"
    - vendor: "Lenovo"
      model: "ThinkPad X1 Carbon Gen 9"
      driver_type: "chipset"
      current_version: "1.5.0"
```

**Gotcha:** Beat needs to be running separately from the worker. Start it with:

```bash
celery -A autopackager.orchestration.celery_app beat --loglevel=info
```

A worker alone will not trigger scheduled tasks.

**Gotcha:** Inspect run history through the dashboard (`GET /api/discovery/runs`, or the `Discovery` panel in the web UI) or directly in the database via `SELECT * FROM discovery_runs ORDER BY started_at DESC;`.

---

## 14. Dashboard Configuration (Optional)

Controls the FastAPI web dashboard exposed at `autopackager/web/api.py`. The dashboard works without any explicit `dashboard:` block — defaults are used. Add the section only if you need to customise CORS origins.

### Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `cors_origins` | list of strings | `["http://localhost:8000", "http://127.0.0.1:8000"]` | Origins allowed by the FastAPI CORS middleware. |

### Example — Permit a Reverse Proxy

```yaml
dashboard:
  cors_origins:
    - "http://localhost:8000"
    - "http://127.0.0.1:8000"
    - "https://autopackager.contoso.local"
```

**Note:** Bind host and port are controlled by the launch script / `uvicorn` invocation, not by this config block. The bundled `start-dashboard.sh` honours `DASHBOARD_HOST`, `DASHBOARD_PORT`, and `DASHBOARD_WORKERS` environment variables.

---

## 15. Environment Variable Mapping Reference

Complete mapping of `.env` variables to `config.yaml` placeholders.

| .env Variable | config.yaml Location | Purpose | Required? |
|---------------|----------------------|---------|-----------|
| `DB_PASSWORD` | `database.password` | PostgreSQL database password | PostgreSQL only |
| `AZURE_TENANT_ID` | `intune.tenant_id` | Azure AD tenant ID | Yes |
| `AZURE_CLIENT_ID` | `intune.client_id` | App Registration client ID | Yes |
| `AZURE_CLIENT_SECRET` | `intune.client_secret` | App Registration client secret | Yes |
| `LLM_API_KEY` | `llm.api_key` | LLM API key (OpenAI/Anthropic/Azure) | Yes |
| `RING0_GROUP_ID` | `deployment_rings[0].entra_group_id` | IT Pilot group object ID | Yes |
| `RING1_GROUP_ID` | `deployment_rings[1].entra_group_id` | Early Adopters group object ID | Yes |
| `RING2_GROUP_ID` | `deployment_rings[2].entra_group_id` | Broad Deployment group object ID | Yes |
| `RING3_GROUP_ID` | `deployment_rings[3].entra_group_id` | Critical Systems group object ID | Yes |
| `AZURE_TEST_RG` | `testing.vm_config.azure.resource_group` | Azure resource group for test VMs | Azure VMs only |
| `AZURE_VM_ADMIN_PASSWORD` | `testing.vm_config.azure.admin_password` | Azure VM admin password | Azure VMs only |
| `AZURE_OPENAI_ENDPOINT` | `llm.azure_endpoint` | Azure OpenAI endpoint URL | Azure OpenAI only |

### Validating .env File

There is no built-in `validate-config` command yet. The recommended quick check is to load the resolved config and look for unsubstituted `${VAR_NAME}` placeholders:

```bash
python -c "from autopackager.utils.config import get_config; \
import json, re; cfg = get_config(); \
text = json.dumps(cfg); \
unresolved = sorted(set(re.findall(r'\\$\\{([^}]+)\\}', text))); \
print('OK' if not unresolved else 'Missing env vars: ' + ', '.join(unresolved))"
```

If any variables are reported, add them to `.env` and re-run. As a sanity check on credentials, `python cli.py init` will surface database/Graph configuration problems on first use.

---

## 16. Common Configuration Scenarios

### Scenario A: Small Business (< 100 Devices, Single Server)

```yaml
# Minimal setup — all defaults
database:
  type: "sqlite"
  path: "data/autopackager.db"

redis:
  host: "localhost"
  port: 6379
  db: 0

intune:
  tenant_id: "${AZURE_TENANT_ID}"
  client_id: "${AZURE_CLIENT_ID}"
  client_secret: "${AZURE_CLIENT_SECRET}"
  graph_api_version: "v1.0"
  graph_endpoint: "https://graph.microsoft.com"

llm:
  provider: "openai"
  api_key: "${LLM_API_KEY}"
  model: "gpt-3.5-turbo"  # Cheaper for small-scale
  temperature: 0.2
  max_tokens: 4096

testing:
  enabled: true
  vm_testing_enabled: false  # No VM testing for simplicity

logging:
  level: "INFO"
  format: "json"
  file: "data/logs/autopackager.log"

jobs:
  max_retries: 3
  retry_delay_seconds: 300
  concurrent_jobs: 2  # Low concurrency for small server
```

**Rationale:** SQLite, no VM testing, low concurrency, cheaper LLM model.

### Scenario B: Enterprise (500+ Devices, HA Multi-Server)

```yaml
database:
  type: "postgresql"
  host: "db.contoso.local"
  port: 5432
  database: "autopackager"
  user: "autopackager_user"
  password: "${DB_PASSWORD}"

redis:
  host: "redis.contoso.local"
  port: 6379
  db: 0

intune:
  tenant_id: "${AZURE_TENANT_ID}"
  client_id: "${AZURE_CLIENT_ID}"
  client_secret: "${AZURE_CLIENT_SECRET}"
  graph_api_version: "v1.0"
  graph_endpoint: "https://graph.microsoft.com"

llm:
  provider: "azure_openai"  # Private endpoint for compliance
  api_key: "${LLM_API_KEY}"
  model: "gpt4-deployment"
  temperature: 0.2
  max_tokens: 4096
  azure_endpoint: "${AZURE_OPENAI_ENDPOINT}"

testing:
  enabled: true
  vm_testing_enabled: true
  vm_provider: "azure"  # Cloud-based testing

  vm_config:
    azure:
      resource_group: "${AZURE_TEST_RG}"
      vm_size: "Standard_D2s_v3"
      image_reference:
        publisher: "MicrosoftWindowsDesktop"
        offer: "Windows-11"
        sku: "win11-22h2-pro"
        version: "latest"
      admin_username: "azureuser"
      admin_password: "${AZURE_VM_ADMIN_PASSWORD}"
      location: "eastus"
      boot_timeout_seconds: 600

    timeout_minutes: 30
    max_retries: 2
    cleanup_on_failure: true

logging:
  level: "INFO"
  format: "json"
  file: "\\\\fileserver\\autopackager-logs\\autopackager.log"

jobs:
  max_retries: 5
  retry_delay_seconds: 600
  concurrent_jobs: 10  # High concurrency for powerful server

status_polling:
  enabled: true
  polling_interval_minutes: 30  # Slower to avoid rate limits
  max_devices_per_poll: 500
  retry_on_rate_limit: true
```

**Rationale:** PostgreSQL HA, Azure VM testing, centralized logging, high concurrency, Azure OpenAI for compliance.

### Scenario C: Healthcare (Highly Regulated, Conservative Rollout)

```yaml
deployment_rings:
  - name: "IT Lab"
    ring_id: "ring0"
    entra_group_id: "${RING0_GROUP_ID}"
    deferral_days: 0
  - name: "Administrative Staff"
    ring_id: "ring1"
    entra_group_id: "${RING1_GROUP_ID}"
    deferral_days: 7
  - name: "Clinical Non-Critical"
    ring_id: "ring2"
    entra_group_id: "${RING2_GROUP_ID}"
    deferral_days: 21
  - name: "Clinical Workstations"
    ring_id: "ring3"
    entra_group_id: "${RING3_GROUP_ID}"
    deferral_days: 45
  - name: "Medical Devices (FDA)"
    ring_id: "ring4"
    entra_group_id: "${RING4_GROUP_ID}"
    deferral_days: 90  # Manual approval required

testing:
  enabled: true
  vm_testing_enabled: true  # Required validation
  vm_provider: "local"  # On-prem for compliance

  vm_config:
    hyperv:
      vm_name: "AutoPackager-TestVM"
      snapshot_name: "clean_windows_11_hipaa_compliant"
      switch_name: "Internal-Secure"
      memory_mb: 8192  # More resources for thorough testing
      processors: 4
      boot_timeout_seconds: 300

jobs:
  max_retries: 2  # Fail fast for manual review
  retry_delay_seconds: 300
  concurrent_jobs: 3

status_polling:
  enabled: true
  polling_interval_minutes: 60  # Less frequent (not time-critical)
  max_devices_per_poll: 500
  retry_on_rate_limit: true
```

**Rationale:** Extended deferral days (45-90 days for critical systems), mandatory VM testing, conservative job settings.

---

## 17. Configuration Gotchas & Best Practices

### ✅ Best Practices

1. **Always use environment variables for secrets** — Never hardcode `client_secret`, `api_key`, or `password` in `config.yaml`. Use `${VAR_NAME}` placeholders.

2. **Validate config before production** — After any change, load the resolved config (see *Validating .env File* above) to catch unresolved `${VAR_NAME}` placeholders, then run `python cli.py init` to confirm the database and credentials are wired up correctly.

3. **Start with SQLite, migrate to PostgreSQL** — Use SQLite for initial testing (simpler setup), switch to PostgreSQL for production (better concurrency, HA support).

4. **Enable VM testing for production** — Don't skip `vm_testing_enabled: true` in production. Catching bad installers before Intune deployment saves hours of troubleshooting.

5. **Tune concurrency based on server specs** — Don't blindly use default `concurrent_jobs: 5`. Monitor CPU and adjust accordingly.

6. **Set up log rotation** — Logs grow indefinitely. Configure `logrotate` (Linux) or Task Scheduler (Windows) to compress/archive old logs.

7. **Use descriptive ring names** — `"IT Pilot"` is better than `"Ring 0"` in logs and dashboards. IT admins will understand the intent.

8. **Document custom changes** — If you modify default values (e.g., increase `deferral_days`), add a comment in `config.yaml` explaining why:
   ```yaml
   deferral_days: 21  # Extended to 3 weeks per Security team requirement (ticket #12345)
   ```

### ❌ Common Gotchas

1. **Missing environment variables fail silently** — If `.env` doesn't define `${RING2_GROUP_ID}`, the config loader (`Template.safe_substitute` in `autopackager/utils/config.py`) leaves the literal string `"${RING2_GROUP_ID}"` in place, causing cryptic Graph API errors later. Use the unresolved-placeholder check in *Validating .env File* to catch this before runtime.

2. **Client secret expiry causes sudden failures** — App Registration secrets expire (default 1-2 years). Set calendar reminders to rotate before expiry.

3. **Hyper-V snapshots must exist before enabling VM testing** — If `snapshot_name: "clean_windows_11"` doesn't exist, tests fail immediately with `SnapshotNotFoundError`.

4. **`concurrent_jobs` too high causes CPU starvation** — IntuneWinAppUtil.exe is CPU-bound. On a 4-core server, `concurrent_jobs: 10` makes the system unresponsive. Start conservative (2-3), scale up gradually.

5. **UNC paths require proper permissions** — If `paths.packages` is a UNC path (`\\fileserver\share`), the Celery worker service account needs **write permissions**. Test with `New-Item` before production.

6. **Graph API rate limits aren't per-app, they're per-tenant** — If other applications (Power Automate, custom scripts) share the same tenant, they contribute to the same rate limit quota. Coordinate with other teams.

7. **Status polling faster than 5 minutes is pointless** — Intune caches device status for 5-10 minutes. Polling every 1 minute doesn't get fresher data and wastes API quota.

8. **Azure VM testing costs add up** — Each test creates/deletes a VM. At `Standard_D2s_v3` ($0.096/hour), 100 tests/day = ~$1.60/day = ~$48/month. Budget accordingly.

9. **Deferral days are relative to Ring 0, not previous ring** — If Ring 0 deploys on Jan 1 and has issues, Ring 2 (`deferral_days: 7`) still deploys on Jan 8 even if you haven't fixed Ring 1's problems. Use the dashboard to manually pause progression.

10. **YAML indentation errors break config loading** — YAML is whitespace-sensitive. If you get `yaml.scanner.ScannerError`, check for tabs vs. spaces. Use **2 spaces** for indentation (not tabs).

---

## 18. Validation & Troubleshooting

### Validate Configuration

There is no dedicated CLI validator yet. To sanity-check `config.yaml` and `.env` together:

```bash
# Activate virtual environment (if not already active)
.\venv\Scripts\activate  # Windows
source venv/bin/activate  # Linux/macOS

# Load and inspect resolved config (catches YAML syntax errors and unresolved ${VAR_NAME} placeholders)
python -c "from autopackager.utils.config import get_config; \
import json, re; cfg = get_config(); \
text = json.dumps(cfg); \
unresolved = sorted(set(re.findall(r'\\$\\{([^}]+)\\}', text))); \
print('OK' if not unresolved else 'Missing env vars: ' + ', '.join(unresolved))"

# Then exercise the database and credentials end-to-end
python cli.py init
```

**What this catches:**
- YAML syntax errors (raised by the `yaml.safe_load` call inside `get_config()`)
- Unresolved environment variable placeholders
- Database connection / schema problems (surfaced by `python cli.py init`)

It does **not** validate semantics like `database.type: "mysql"` — invalid field values surface at runtime when SQLAlchemy or the Graph client tries to use them.

### Troubleshooting Steps

#### Problem: `401 Unauthorized` from Graph API

**Cause:** Invalid or expired credentials.

**Fix:**
1. Verify `.env` has correct `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`
2. Check client secret hasn't expired (Azure Portal → App Registrations → Certificates & secrets)
3. Verify API permissions are granted with admin consent

#### Problem: Jobs stuck in `pending` state

**Cause:** Celery worker not running or Redis unreachable.

**Fix:**
1. Check worker is running: `.\start-worker.bat` (Windows) or `python cli.py worker start` (Linux)
2. Check Redis: `redis-cli ping` (should return `PONG`)
3. Check logs: `data/logs/autopackager.log`

#### Problem: VM testing fails with `SnapshotNotFoundError`

**Cause:** Hyper-V snapshot doesn't exist.

**Fix:**
1. Open Hyper-V Manager
2. Select the VM (`AutoPackager-TestVM`)
3. Right-click → Checkpoint → Name must match `testing.vm_config.hyperv.snapshot_name`

#### Problem: Status polling causes `TooManyRequests` errors

**Cause:** Hitting Graph API rate limits.

**Fix:**
1. Increase `status_polling.polling_interval_minutes` (e.g., 15 → 30)
2. Decrease `status_polling.max_devices_per_poll` (e.g., 1000 → 500)
3. Verify `status_polling.retry_on_rate_limit: true` (should be default)

#### Problem: Packaging fails with `FileNotFoundError: IntuneWinAppUtil.exe`

**Cause:** IntuneWinAppUtil.exe not found at `paths.intunewin_util`.

**Fix:**
1. Download from https://github.com/microsoft/Microsoft-Win32-Content-Prep-Tool
2. Place in `tools/IntuneWinAppUtil.exe`
3. Or run `.\Install-AutoPackager.ps1` to download automatically

---

## 19. Further Reading

- **QUICKSTART_CHECKLIST.md** — Main first-run guide (automated + manual paths, troubleshooting)
- **AUTOMATED_SETUP.md** — Installer flags and what `Install-AutoPackager.ps1` does
- **SETUP.md** — Manual installation steps (database setup, Redis, Azure App Registration)
- **docs/design-history/** — Original whitepaper and PR/FAQ (pre-release vision)

For Graph API reference (Intune endpoints, JSON schemas):
- **docs/claude-reference/ch04-driver-updates-reference.md** — Driver update profiles, update rings
- **docs/claude-reference/ch11-windows-app-packaging-reference.md** — Win32 app deployment, detection methods

---

*This reference was created for AutoPackager IT administrators. For questions or issues, refer to the GitHub repository or internal support channels.*
