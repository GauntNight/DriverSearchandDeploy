"""Microsoft Graph API Client for Intune"""

import requests
from msal import ConfidentialClientApplication
from tenacity import retry, stop_after_attempt, wait_exponential

from autopackager.utils.config import get_config
from autopackager.utils.logger import get_logger

logger = get_logger(__name__)


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

        return response.json()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def patch(self, endpoint, data=None):
        """Make a PATCH request to Graph API"""
        url = f"{self.graph_endpoint}/{self.api_version}/{endpoint}"
        logger.debug("PATCH request", url=url)

        response = requests.patch(url, headers=self._get_headers(), json=data)
        self._raise_with_details(response)

        return response.json()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def delete(self, endpoint):
        """Make a DELETE request to Graph API"""
        url = f"{self.graph_endpoint}/{self.api_version}/{endpoint}"
        logger.debug("DELETE request", url=url)

        response = requests.delete(url, headers=self._get_headers())
        self._raise_with_details(response)

        return response.status_code == 204

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
        """Update an existing Win32 app"""
        logger.info("Updating Win32 app", app_id=app_id)
        return self.patch(f"deviceAppManagement/mobileApps/{app_id}", app_data)

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
