"""Microsoft Graph API Client for Intune"""

import base64
import json
import time
import requests
from msal import ConfidentialClientApplication
from tenacity import retry, stop_after_attempt, wait_exponential
from urllib.parse import urlparse, urlencode, parse_qs, urlunparse

from autopackager.utils.config import get_config
from autopackager.utils.logger import get_logger

logger = get_logger(__name__)


def format_graph_error(exc, *, action=None):
    """Turn a Graph/requests/tenacity failure into one concise, human line.

    Backend chokepoint for surfacing Graph errors to operators (the demo
    console, job ``error_message``, logs) WITHOUT dumping the raw
    ``{'error': {...}}`` dict. Handles the three shapes we actually raise:

      * ``tenacity.RetryError`` — unwrapped to the underlying exception.
      * ``requests.HTTPError`` — Graph error ``code``/``message`` pulled from
        the JSON body (Intune sometimes nests a second JSON blob in
        ``message`` whose real text is under ``Message`` — unwrapped too).
      * anything else (network/auth/programming) — type + str, truncated.

    Common failure classes get an actionable sentence:
      * 403 / ``Authorization_RequestDenied`` / "insufficient privileges" →
        names it as a missing service-principal role (e.g. creating a new ring
        group needs ``Group.ReadWrite.All``).
      * 400 ``ModelValidationFailure`` → "Intune rejected the request payload".
      * 429 throttling, 404 not-found → labelled plainly.

    Never raises — error formatting must not mask the original error.
    """
    err = exc
    # Unwrap tenacity RetryError -> the last underlying exception.
    last = getattr(err, "last_attempt", None)
    if last is not None:
        try:
            err = last.exception() or err
        except Exception:
            pass

    resp = getattr(err, "response", None)
    status = getattr(resp, "status_code", None)
    if not isinstance(status, int):
        status = None
    code = message = None
    if resp is not None:
        body = None
        try:
            body = resp.json()
        except Exception:
            body = None
        if isinstance(body, dict):
            gerr = body.get("error")
            if isinstance(gerr, dict):
                code = gerr.get("code")
                message = gerr.get("message")
                # Intune nests a second JSON blob in `message`; its real text
                # is under "Message" (see updateRelationships / assign 400s).
                if isinstance(message, str) and message.lstrip().startswith("{"):
                    try:
                        message = json.loads(message).get("Message") or message
                    except Exception:
                        pass
            elif isinstance(gerr, str):
                message = gerr
        if not isinstance(message, str) or not message:
            # Fall back to the raw body text; str() guards Mock/None in tests.
            message = str(getattr(resp, "text", "") or "")[:300] or None

    prefix = f"{action}: " if action else ""
    msg = (message or "").strip() if isinstance(message, str) else ""
    low = msg.lower()

    if status == 403 or (code and "Authorization" in code) or "privileg" in low:
        hint = ("The AutoPackager service principal is missing a required Graph "
                "role - grant it in Entra and retry "
                "(creating a new deployment ring group needs Group.ReadWrite.All).")
        return f"{prefix}Insufficient Graph permissions (403{f' {code}' if code else ''}). {hint}" + (f" Detail: {msg}" if msg else "")
    if status == 400 and code == "ModelValidationFailure":
        return f"{prefix}Intune rejected the request payload (400 ModelValidationFailure): {msg or 'invalid property in the request'}"
    if status == 429:
        return f"{prefix}Throttled by Graph (429) — backing off and retrying." + (f" Detail: {msg}" if msg else "")
    if status == 404:
        return f"{prefix}Graph resource not found (404): {msg or 'the target app or group no longer exists.'}"
    if status is not None:
        return f"{prefix}Graph API error (HTTP {status}{f' {code}' if code else ''}): {msg or 'see logs for the full response.'}"
    return f"{prefix}{type(err).__name__}: {str(err)[:300]}"


# 6 MB chunks for Azure block blob upload
_AZURE_UPLOAD_CHUNK_SIZE = 6 * 1024 * 1024

# Intune `retrieveDeviceAppInstallationStatusReport` returns InstallState as an
# integer enum. Mapping to the lowercase strings `_parse_install_statuses`
# already understands. Verified empirically against a known-installed device:
# row [.., InstallState=1, HexErrorCode="", ..] == successful install.
_INSTALL_STATE_INT_TO_STRING = {
    0: "notapplicable",
    1: "installed",
    2: "failed",
    3: "pending",
    4: "unknown",
    5: "notinstalled",
}


class GraphAPIClient:
    """Client for Microsoft Graph API interactions"""

    def __init__(self):
        config = get_config()
        self.intune_config = config['intune']

        self.tenant_id = self.intune_config['tenant_id']
        self.client_id = self.intune_config['client_id']
        self.client_secret = self.intune_config['client_secret']
        self.graph_endpoint = self.intune_config['graph_endpoint']
        self.api_version = self.intune_config['graph_api_version']

        self.authority = f"https://login.microsoftonline.com/{self.tenant_id}"
        self.scope = ["https://graph.microsoft.com/.default"]

        self.access_token = None
        self._authenticate()

    def _authenticate(self):
        """Authenticate and get access token"""
        logger.info("Authenticating to Microsoft Graph API")

        app = ConfidentialClientApplication(
            self.client_id,
            authority=self.authority,
            client_credential=self.client_secret
        )

        result = app.acquire_token_for_client(scopes=self.scope)

        if "access_token" in result:
            self.access_token = result["access_token"]
            logger.info("Successfully authenticated to Graph API")
        else:
            error = result.get("error_description", "Unknown error")
            logger.error("Failed to authenticate", error=error)
            raise Exception(f"Authentication failed: {error}")

    def _get_headers(self):
        """Get headers for API requests"""
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }

    def _raise_with_details(self, response):
        """Check response status and raise with detailed error info"""
        try:
            response.raise_for_status()
        except requests.HTTPError as e:
            try:
                error_body = response.json()
            except Exception:
                error_body = response.text
            logger.error(
                "Graph API error",
                method=response.request.method,
                url=response.url,
                status_code=response.status_code,
                error_body=error_body,
            )
            raise

    def _parse_response(self, response):
        """Return JSON body, or None for 204 No Content responses"""
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def get(self, endpoint, params=None):
        """Make a GET request to Graph API"""
        url = f"{self.graph_endpoint}/{self.api_version}/{endpoint}"
        logger.debug("GET request", url=url, params=params)

        response = requests.get(url, headers=self._get_headers(), params=params)
        self._raise_with_details(response)

        return response.json()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def post(self, endpoint, data=None):
        """Make a POST request to Graph API"""
        url = f"{self.graph_endpoint}/{self.api_version}/{endpoint}"
        logger.debug("POST request", url=url)

        response = requests.post(url, headers=self._get_headers(), json=data)
        self._raise_with_details(response)

        return self._parse_response(response)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def patch(self, endpoint, data=None):
        """Make a PATCH request to Graph API"""
        url = f"{self.graph_endpoint}/{self.api_version}/{endpoint}"
        logger.debug("PATCH request", url=url)

        response = requests.patch(url, headers=self._get_headers(), json=data)
        self._raise_with_details(response)

        return self._parse_response(response)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def delete(self, endpoint):
        """Make a DELETE request to Graph API"""
        url = f"{self.graph_endpoint}/{self.api_version}/{endpoint}"
        logger.debug("DELETE request", url=url)

        response = requests.delete(url, headers=self._get_headers())
        self._raise_with_details(response)

        return response.status_code == 204

    # ---------------------------------------------------------------------------
    # Win32 app CRUD
    # ---------------------------------------------------------------------------

    def get_win32_apps(self):
        """Get all Win32 apps from Intune"""
        logger.info("Fetching Win32 apps from Intune")
        return self.get("deviceAppManagement/mobileApps?$filter=isof('microsoft.graph.win32LobApp')")

    def get_win32_app(self, app_id):
        """Get a specific Win32 app"""
        logger.info("Fetching Win32 app", app_id=app_id)
        return self.get(f"deviceAppManagement/mobileApps/{app_id}")

    def list_detected_apps(self, page_limit: int = 50):
        """Return Intune's Detected Apps inventory across all managed devices.

        ``deviceManagement/detectedApps`` is Intune's aggregated installed-
        software list (derived from device ARP), with a per-app ``deviceCount``.
        Pages by following ``@odata.nextLink``. Each row:
        ``{id, displayName, version, publisher, platform, deviceCount, sizeInByte}``.

        Requires ``DeviceManagementManagedDevices.Read.All`` on the app
        registration — without it Graph returns **403**; the exception
        propagates so callers can fall back to local ARP.

        ``page_limit`` caps how many nextLink pages we follow (safety bound on
        very large tenants).
        """
        logger.info("Fetching Intune detected apps inventory")
        results = []
        data = self.get("deviceManagement/detectedApps?$top=100&$orderby=deviceCount desc")
        pages = 0
        while True:
            results.extend(data.get("value", []) or [])
            nxt = data.get("@odata.nextLink")
            pages += 1
            if not nxt or pages >= page_limit:
                break
            resp = requests.get(nxt, headers=self._get_headers())
            self._raise_with_details(resp)
            data = resp.json()
        logger.info("Detected apps fetched", count=len(results), pages=pages)
        return results

    def list_device_detected_apps(self, device_id: str):
        """Detected apps for a single managed device (per-device drill-down).

        ``managedDevices/{id}/detectedApps``. Same Read.All requirement.
        """
        logger.info("Fetching detected apps for device", device_id=device_id)
        return self.get(
            f"deviceManagement/managedDevices/{device_id}/detectedApps"
        ).get("value", []) or []

    def create_win32_app(self, app_data):
        """Create a new Win32 app"""
        logger.info("Creating Win32 app", app_name=app_data.get('displayName'))
        return self.post("deviceAppManagement/mobileApps", app_data)

    def update_win32_app(self, app_id, app_data):
        """Update an existing Win32 app metadata"""
        logger.info("Updating Win32 app", app_id=app_id)
        return self.patch(f"deviceAppManagement/mobileApps/{app_id}", app_data)

    def delete_win32_app(self, app_id):
        """Delete a Win32 app from Intune"""
        logger.info("Deleting Win32 app", app_id=app_id)
        return self.delete(f"deviceAppManagement/mobileApps/{app_id}")

    # ---------------------------------------------------------------------------
    # Win32 app content publishing (upload + publish flow)
    # ---------------------------------------------------------------------------

    def create_content_version(self, app_id):
        """Create a new content version for a Win32 app"""
        logger.info("Creating content version", app_id=app_id)
        return self.post(
            f"deviceAppManagement/mobileApps/{app_id}"
            f"/microsoft.graph.win32LobApp/contentVersions",
            {}
        )

    def create_content_file(self, app_id, version_id, file_name, unencrypted_size, encrypted_size):
        """Create a content file entry and obtain the Azure Storage upload URI"""
        logger.info("Creating content file entry", app_id=app_id, version_id=version_id)
        file_data = {
            "@odata.type": "#microsoft.graph.mobileAppContentFile",
            "name": file_name,
            "size": unencrypted_size,
            "sizeEncrypted": encrypted_size,
            "isDependency": False,
        }
        return self.post(
            f"deviceAppManagement/mobileApps/{app_id}"
            f"/microsoft.graph.win32LobApp/contentVersions/{version_id}/files",
            file_data
        )

    def get_content_file(self, app_id, version_id, file_id):
        """Get current status of a content file (for polling)"""
        return self.get(
            f"deviceAppManagement/mobileApps/{app_id}"
            f"/microsoft.graph.win32LobApp/contentVersions/{version_id}/files/{file_id}"
        )

    def commit_content_file(self, app_id, version_id, file_id, encryption_info):
        """Tell Intune the file upload is complete and provide encryption metadata"""
        logger.info("Committing content file", app_id=app_id, file_id=file_id)
        return self.post(
            f"deviceAppManagement/mobileApps/{app_id}"
            f"/microsoft.graph.win32LobApp/contentVersions/{version_id}"
            f"/files/{file_id}/commit",
            {"fileEncryptionInfo": encryption_info}
        )

    def commit_content_version(self, app_id, version_id):
        """Set committedContentVersion on the app — triggers publishingState → published"""
        logger.info("Committing content version to app", app_id=app_id, version_id=version_id)
        return self.patch(
            f"deviceAppManagement/mobileApps/{app_id}",
            {
                "@odata.type": "#microsoft.graph.win32LobApp",
                "committedContentVersion": str(version_id),
            }
        )

    def wait_for_azure_storage_uri(self, app_id, version_id, file_id, timeout=300):
        """Poll until Intune provides the Azure Blob Storage SAS URI for upload"""
        logger.info("Waiting for Azure Storage URI", app_id=app_id, file_id=file_id)
        deadline = time.time() + timeout
        while time.time() < deadline:
            info = self.get_content_file(app_id, version_id, file_id)
            state = info.get('uploadState', '')
            if state == 'azureStorageUriRequestSuccess':
                uri = info.get('azureStorageUri')
                if uri:
                    logger.info("Azure Storage URI ready", file_id=file_id)
                    return uri
            if 'failure' in state.lower() or 'error' in state.lower():
                raise Exception(f"Azure Storage URI request failed with state: {state}")
            logger.debug("Waiting for Azure Storage URI", upload_state=state)
            time.sleep(5)
        raise Exception(f"Timed out ({timeout}s) waiting for Azure Storage URI")

    def wait_for_file_commit(self, app_id, version_id, file_id, timeout=300):
        """Poll until Intune confirms the uploaded file has been committed"""
        logger.info("Waiting for file commit confirmation", app_id=app_id, file_id=file_id)
        deadline = time.time() + timeout
        while time.time() < deadline:
            info = self.get_content_file(app_id, version_id, file_id)
            state = info.get('uploadState', '')
            if state == 'commitFileSuccess':
                logger.info("File commit confirmed", file_id=file_id)
                return
            if 'failure' in state.lower():
                raise Exception(f"File commit failed with state: {state}")
            logger.debug("Waiting for file commit", upload_state=state)
            time.sleep(5)
        raise Exception(f"Timed out ({timeout}s) waiting for file commit")

    def upload_to_azure_storage(self, sas_url, file_path):
        """Upload a file to Azure Blob Storage using chunked block blob upload"""
        from pathlib import Path

        file_size = Path(file_path).stat().st_size
        if file_size == 0:
            raise ValueError(f"Cannot upload empty file: {file_path}")

        logger.info("Uploading file to Azure Storage", file_path=str(file_path), size_bytes=file_size)

        block_ids = []
        block_num = 0

        with open(file_path, 'rb') as fh:
            while True:
                chunk = fh.read(_AZURE_UPLOAD_CHUNK_SIZE)
                if not chunk:
                    break

                # Block IDs must be base64-encoded and all the same length
                raw_id = f"{block_num:032d}".encode()
                block_id = base64.b64encode(raw_id).decode()
                block_ids.append(block_id)

                upload_url = self._azure_url_with_params(
                    sas_url, comp="block", blockid=block_id
                )
                self._put_block_with_retry(upload_url, chunk)

                block_num += 1
                logger.debug("Uploaded block", block_num=block_num, total_blocks=_expected_blocks(file_size))

        # Commit the block list
        block_list_xml = (
            '<?xml version="1.0" encoding="utf-8"?><BlockList>'
            + "".join(f"<Latest>{bid}</Latest>" for bid in block_ids)
            + "</BlockList>"
        )
        commit_url = self._azure_url_with_params(sas_url, comp="blocklist")
        resp = requests.put(
            commit_url,
            data=block_list_xml.encode("utf-8"),
            headers={"Content-Type": "application/xml"},
        )
        resp.raise_for_status()

        logger.info("Azure Storage upload complete", blocks=len(block_ids), size_bytes=file_size)

    @staticmethod
    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=2, min=2, max=60),
        reraise=True,
    )
    def _put_block_with_retry(url, data):
        """Upload a single block with retry on transient Azure errors (503, 500, 429)."""
        resp = requests.put(url, data=data, headers={"x-ms-blob-type": "BlockBlob"})
        if resp.status_code in (429, 500, 503):
            logger.warning(
                "Transient Azure error, retrying",
                status=resp.status_code,
                body=resp.text[:200],
            )
            resp.raise_for_status()
        resp.raise_for_status()

    @staticmethod
    def _azure_url_with_params(sas_url, **extra_params):
        """Append extra query parameters to a SAS URL without clobbering existing ones"""
        parsed = urlparse(sas_url)
        existing = parse_qs(parsed.query, keep_blank_values=True)
        # parse_qs returns lists; flatten for urlencode
        flat = {k: v[0] for k, v in existing.items()}
        flat.update(extra_params)
        new_query = urlencode(flat)
        return urlunparse(parsed._replace(query=new_query))

    # ---------------------------------------------------------------------------
    # Group assignment
    # ---------------------------------------------------------------------------

    def assign_app_to_group(self, app_id, group_id, intent="required", settings=None):
        """Assign an app to an Entra ID group.

        Args:
            app_id: Win32 app id.
            group_id: target Entra group id.
            intent: ``required`` | ``available`` | ``uninstall``.
            settings: optional ``mobileAppAssignmentSettings`` dict attached to
                the assignment. For Win32 apps this is a
                ``#microsoft.graph.win32LobAppAssignmentSettings`` carrying e.g.
                ``autoUpdateSettings.autoUpdateSupersededApps='enabled'`` — the
                flag that makes a superseded install actually auto-upgrade on
                already-targeted devices (see ``mobileAppSupersedence`` /
                ``supersedenceType='update'``). Omitted callers are unchanged.
        """
        logger.info(
            "Assigning app to group",
            app_id=app_id, group_id=group_id, intent=intent,
            with_settings=bool(settings),
        )

        assignment = {
            "target": {
                "@odata.type": "#microsoft.graph.groupAssignmentTarget",
                "groupId": group_id
            },
            "intent": intent
        }
        if settings:
            assignment["settings"] = settings

        assignment_data = {"mobileAppAssignments": [assignment]}

        return self.post(f"deviceAppManagement/mobileApps/{app_id}/assign", assignment_data)

    @staticmethod
    def win32_auto_update_settings(auto_update_superseded=True):
        """Build a ``win32LobAppAssignmentSettings`` block that enables (or
        disables) auto-update of superseded apps for a Win32 assignment.

        Returns the dict to pass as ``assign_app_to_group(..., settings=...)``.
        """
        return {
            "@odata.type": "#microsoft.graph.win32LobAppAssignmentSettings",
            "autoUpdateSettings": {
                # Graph property is `autoUpdateSupersededAppsState` (enum
                # win32LobAutoUpdateSupersededAppsState). The shorter
                # `autoUpdateSupersededApps` does not exist on the type and
                # makes /assign 400 with ModelValidationFailure.
                "autoUpdateSupersededAppsState": "enabled" if auto_update_superseded else "notConfigured",
            },
        }

    def get_group(self, group_id):
        """Get Entra ID group information"""
        logger.info("Fetching Entra ID group", group_id=group_id)
        return self.get(f"groups/{group_id}")

    # ---------------------------------------------------------------------------
    # Driver Update Profiles (Intune-native driver management — ch04 reference)
    # Uses beta API endpoint as these are not yet in v1.0.
    # ---------------------------------------------------------------------------

    def _beta_post(self, endpoint, data=None):
        """POST to the Graph beta endpoint (driver update profiles require beta)."""
        url = f"{self.graph_endpoint}/beta/{endpoint}"
        logger.debug("POST (beta) request", url=url)
        response = requests.post(url, headers=self._get_headers(), json=data)
        self._raise_with_details(response)
        return self._parse_response(response)

    def _beta_get(self, endpoint, params=None):
        """GET from the Graph beta endpoint."""
        url = f"{self.graph_endpoint}/beta/{endpoint}"
        logger.debug("GET (beta) request", url=url, params=params)
        response = requests.get(url, headers=self._get_headers(), params=params)
        self._raise_with_details(response)
        return response.json()

    def create_driver_update_profile(
        self,
        display_name: str,
        description: str = '',
        approval_type: str = 'manual',
        deferral_days: int = 3,
    ):
        """Create an Intune Driver Update Profile.

        Args:
            display_name: Profile display name.
            description: Profile description.
            approval_type: ``'manual'`` or ``'automatic'``.
            deferral_days: Days before auto-approval (only for automatic mode).

        Returns:
            The created profile JSON (includes ``id``).

        .. warning::
            **App-only (client-credentials) creation is currently blocked in
            the ngbg test tenant.** Observed 2026-06-08: this POST returns a
            403 from the Intune Windows-Update-for-Business backend (the
            ``SoftwareUpdateService`` proxy) *even though* the service
            principal holds ``DeviceManagementConfiguration.ReadWrite.All``
            (the Graph scope gate passes — the error has no "must have scope"
            text). The most likely cause is that the tenant's WUfB deployment
            service has not been onboarded yet; the first driver-update
            profile must be created interactively by a signed-in admin in the
            Intune portal (Devices → Windows → Driver updates), which onboards
            the tenant. After that, the *read* surface below
            (:meth:`get_driver_update_profile`, :meth:`list_driver_inventory`)
            works under the SP token. See the discovery journal
            (``driver-mgmt-wufb``) for the full trace.
        """
        logger.info(
            "Creating driver update profile",
            name=display_name,
            approval_type=approval_type,
        )
        payload = {
            'approvalType': approval_type,
            'description': description,
            'displayName': display_name,
            'roleScopeTagIds': ['0'],
        }
        if approval_type == 'automatic':
            payload['deploymentDeferralInDays'] = deferral_days

        return self._beta_post(
            'deviceManagement/windowsDriverUpdateProfiles', payload
        )

    def assign_driver_update_profile(self, profile_id: str, group_id: str):
        """Assign a Driver Update Profile to a device group."""
        logger.info(
            "Assigning driver update profile",
            profile_id=profile_id,
            group_id=group_id,
        )
        payload = {
            'assignments': [
                {
                    'target': {
                        '@odata.type': '#microsoft.graph.groupAssignmentTarget',
                        'groupId': group_id,
                    }
                }
            ]
        }
        return self._beta_post(
            f'deviceManagement/windowsDriverUpdateProfiles/{profile_id}/assign',
            payload,
        )

    def list_driver_update_profiles(self):
        """List all Driver Update Profiles in the tenant."""
        logger.info("Listing driver update profiles")
        return self._beta_get('deviceManagement/windowsDriverUpdateProfiles')

    def get_driver_update_profile(self, profile_id: str, expand_assignments: bool = False):
        """Fetch a single Driver Update Profile by id.

        With ``expand_assignments=True`` the returned object carries an
        ``assignments`` collection (each ``target.groupId`` is a targeted
        group). Read-only; works under the app-only SP token.
        """
        logger.info("Fetching driver update profile", profile_id=profile_id)
        endpoint = f'deviceManagement/windowsDriverUpdateProfiles/{profile_id}'
        if expand_assignments:
            endpoint += '?$expand=assignments'
        return self._beta_get(endpoint)

    def find_driver_update_profile_by_name(self, display_name: str):
        """Return the first profile whose ``displayName`` matches, else None.

        Convenience for the CLI/service so operators can reference a profile
        by its human name instead of the GUID.
        """
        for prof in self.list_driver_update_profiles().get('value', []) or []:
            if prof.get('displayName') == display_name:
                return prof
        return None

    def list_driver_inventory(self, profile_id: str, page_limit: int = 50):
        """Return the per-driver inventory (the update *delta*) for a profile.

        ``windowsDriverUpdateProfiles/{id}/driverInventories`` is the list of
        ``windowsDriverUpdateInventory`` rows Windows Update surfaced as
        *applicable* to the devices the profile targets — i.e. each row is a
        driver for which a newer version is available. Each row:
        ``{id, name, version, manufacturer, driverClass, category,
        approvalStatus, applicableDeviceCount, releaseDateTime,
        deployDateTime}`` where:

        - ``category`` ∈ ``recommended | previouslyApproved | other``
        - ``approvalStatus`` ∈ ``needsReview | approved | declined | suspended``

        Pages by following ``@odata.nextLink`` (``page_limit`` bounds it).
        Read-only; works under the app-only SP token. Returns ``[]`` while the
        profile is still being inventoried (the ~1-2 day WUfB sync after first
        assignment) — an empty list is the *pending* state, not an error.
        """
        logger.info("Fetching driver inventory", profile_id=profile_id)
        results = []
        data = self._beta_get(
            f'deviceManagement/windowsDriverUpdateProfiles/{profile_id}/driverInventories'
        )
        pages = 0
        while True:
            results.extend(data.get('value', []) or [])
            nxt = data.get('@odata.nextLink')
            pages += 1
            if not nxt or pages >= page_limit:
                break
            resp = requests.get(nxt, headers=self._get_headers())
            self._raise_with_details(resp)
            data = resp.json()
        logger.info("Driver inventory fetched", profile_id=profile_id,
                    count=len(results), pages=pages)
        return results

    # ---------------------------------------------------------------------------
    # Deployment Status (Win32 app install status tracking)
    # ---------------------------------------------------------------------------

    def get_app_install_summary(self, app_id):
        """Get install summary for a Win32 app.

        Returns aggregate counts of install status across all targeted devices:
        - installedDeviceCount
        - failedDeviceCount
        - pendingInstallDeviceCount
        - notApplicableDeviceCount

        Args:
            app_id: The Intune mobile app ID.

        Returns:
            Install summary JSON with device counts by status.
        """
        logger.info("Fetching app install summary", app_id=app_id)
        return self.get(f"deviceAppManagement/mobileApps/{app_id}/installSummary")

    def get_app_device_statuses(self, app_id):
        """Get per-device install status for a Win32 app with pagination support.

        Returns detailed install status for each device that has been targeted:
        - deviceName
        - deviceId
        - installState (installed, failed, pending, etc.)
        - errorCode
        - lastSyncDateTime

        Args:
            app_id: The Intune mobile app ID.

        Returns:
            List of device status objects aggregated from all pages.
        """
        logger.info("Fetching app device statuses", app_id=app_id)

        # Intune retired the old `mobileApps/{id}/deviceStatuses` navigation
        # property (it is not declared on `mobileApp` in either v1.0 or beta
        # $metadata as of 2026). Per-device install state is now exposed via
        # the reports action `retrieveDeviceAppInstallationStatusReport`,
        # which POSTs a query and returns a paged rows-and-schema payload.
        body = {
            "select": [
                "DeviceName",
                "UserPrincipalName",
                "InstallState",
                "InstallStateDetail",
                "HexErrorCode",
                "LastModifiedDateTime",
            ],
            "filter": f"(ApplicationId eq '{app_id}')",
            "skip": 0,
            "top": 1000,
            "orderBy": [],
        }
        url = (
            f"{self.graph_endpoint}/beta/deviceManagement/reports/"
            "retrieveDeviceAppInstallationStatusReport"
        )

        all_statuses: list[dict] = []
        while True:
            logger.debug("POST (beta report) request", url=url, skip=body["skip"])
            response = requests.post(url, headers=self._get_headers(), json=body)
            self._raise_with_details(response)
            # Reports return application/octet-stream; body is JSON anyway.
            # Reports endpoint returns application/octet-stream; body is JSON.
            payload = json.loads(response.content.decode("utf-8"))
            columns = [c["Column"] for c in payload.get("Schema", [])]
            rows = payload.get("Values", []) or []
            for row in rows:
                rec = dict(zip(columns, row))
                all_statuses.append({
                    "deviceName": rec.get("DeviceName"),
                    "deviceId": rec.get("DeviceId"),  # not in default select, kept for parity
                    "userPrincipalName": rec.get("UserPrincipalName"),
                    "installState": _INSTALL_STATE_INT_TO_STRING.get(
                        rec.get("InstallState"), "unknown"
                    ),
                    "installStateDetail": rec.get("InstallStateDetail"),
                    "errorCode": rec.get("HexErrorCode") or None,
                    "lastSyncDateTime": rec.get("LastModifiedDateTime"),
                })

            total = payload.get("TotalRowCount") or 0
            if not rows or (body["skip"] + len(rows)) >= total:
                break
            body["skip"] += len(rows)
            logger.debug("Fetched report page", rows=len(rows), total_so_far=len(all_statuses))

        logger.info("Fetched all device statuses", app_id=app_id, total_devices=len(all_statuses))
        return all_statuses

    def _parse_install_statuses(self, device_statuses):
        """Parse device status list into aggregated counts and failed device details.

        Aggregates install status from device-level responses into summary counts
        for tracking deployment progress. Extracts device details for failed
        installations to enable troubleshooting.

        Args:
            device_statuses: List of device status objects from get_app_device_statuses().
                Each object contains deviceName, deviceId, installState, errorCode, etc.

        Returns:
            Dict with keys:
                - installed: Count of devices with successful install
                - failed: Count of devices with failed install
                - pending: Count of devices with pending install
                - not_applicable: Count of devices where app is not applicable
                - failed_device_details: List of dicts with device info for failed installs
        """
        counts = {
            'installed': 0,
            'failed': 0,
            'pending': 0,
            'not_applicable': 0,
            'failed_device_details': []
        }

        for status in device_statuses:
            install_state = status.get('installState', '').lower()

            if install_state in ('installed', 'success'):
                counts['installed'] += 1
            elif install_state in ('failed', 'error'):
                counts['failed'] += 1
                # Capture failed device details for troubleshooting
                counts['failed_device_details'].append({
                    'device_name': status.get('deviceName'),
                    'device_id': status.get('deviceId'),
                    'error_code': status.get('errorCode'),
                    'install_state': status.get('installState'),
                    'last_sync': status.get('lastSyncDateTime')
                })
            elif install_state in ('pending', 'downloading', 'notinstalled'):
                counts['pending'] += 1
            elif install_state in ('notapplicable', 'not_applicable'):
                counts['not_applicable'] += 1
            else:
                # Unknown states treated as pending for safety
                logger.debug("Unknown install state", state=install_state, device_id=status.get('deviceId'))
                counts['pending'] += 1

        logger.debug(
            "Parsed install statuses",
            total_devices=len(device_statuses),
            installed=counts['installed'],
            failed=counts['failed'],
            pending=counts['pending'],
            not_applicable=counts['not_applicable']
        )

        return counts


def _expected_blocks(file_size):
    import math
    return math.ceil(file_size / _AZURE_UPLOAD_CHUNK_SIZE)
