"""Microsoft Graph API Client for Intune"""

import base64
import time
import requests
from msal import ConfidentialClientApplication
from tenacity import retry, stop_after_attempt, wait_exponential
from urllib.parse import urlparse, urlencode, parse_qs, urlunparse

from autopackager.utils.config import get_config
from autopackager.utils.logger import get_logger

logger = get_logger(__name__)

# 6 MB chunks for Azure block blob upload
_AZURE_UPLOAD_CHUNK_SIZE = 6 * 1024 * 1024


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

    def assign_app_to_group(self, app_id, group_id, intent="required"):
        """Assign an app to an Entra ID group"""
        logger.info("Assigning app to group", app_id=app_id, group_id=group_id, intent=intent)

        assignment_data = {
            "mobileAppAssignments": [
                {
                    "target": {
                        "@odata.type": "#microsoft.graph.groupAssignmentTarget",
                        "groupId": group_id
                    },
                    "intent": intent
                }
            ]
        }

        return self.post(f"deviceAppManagement/mobileApps/{app_id}/assign", assignment_data)

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


def _expected_blocks(file_size):
    import math
    return math.ceil(file_size / _AZURE_UPLOAD_CHUNK_SIZE)
