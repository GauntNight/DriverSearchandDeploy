"""Mock Microsoft Graph API Client for testing"""

from unittest.mock import Mock, MagicMock
from typing import Dict, Any, List, Optional
import time


# Sample Graph API Response Data

SAMPLE_WIN32_APP = {
    'id': 'app-id-12345',
    'displayName': 'Test Driver Package',
    'description': 'Test Driver Package v1.0.0 - Dell',
    'publisher': 'Dell',
    'developer': 'Dell',
    'owner': 'Dell',
    'fileName': 'test-package.intunewin',
    'setupFilePath': 'installer.exe',
    'installCommandLine': 'installer.exe /S',
    'uninstallCommandLine': 'installer.exe /U',
    'displayVersion': '1.0.0',
    'publishingState': 'published',
    'committedContentVersion': '1',
    '@odata.type': '#microsoft.graph.win32LobApp',
    'installExperience': {
        'runAsAccount': 'system',
        'deviceRestartBehavior': 'suppress'
    },
    'rules': [
        {
            '@odata.type': '#microsoft.graph.win32LobAppRegistryRule',
            'ruleType': 'detection',
            'keyPath': 'HKLM\\SOFTWARE\\Test',
            'valueName': 'Version',
            'operationType': 'string',
            'comparisonValue': '1.0.0'
        }
    ],
    'minimumSupportedOperatingSystem': {
        'v10_1607': True
    }
}

SAMPLE_WIN32_APPS_RESPONSE = {
    'value': [SAMPLE_WIN32_APP],
    '@odata.count': 1
}

SAMPLE_CONTENT_VERSION = {
    'id': '1',
    '@odata.type': '#microsoft.graph.mobileAppContent'
}

SAMPLE_CONTENT_FILE = {
    'id': 'file-id-12345',
    'name': 'test-package.intunewin',
    'size': 1024000,
    'sizeEncrypted': 1024512,
    'uploadState': 'azureStorageUriRequestSuccess',
    'azureStorageUri': 'https://test-storage.blob.core.windows.net/test-container/test-file?sv=2021-10-04&sr=b&sig=test',
    '@odata.type': '#microsoft.graph.mobileAppContentFile'
}

SAMPLE_CONTENT_FILE_COMMITTED = {
    'id': 'file-id-12345',
    'name': 'test-package.intunewin',
    'size': 1024000,
    'sizeEncrypted': 1024512,
    'uploadState': 'commitFileSuccess',
    '@odata.type': '#microsoft.graph.mobileAppContentFile'
}

SAMPLE_ENTRA_GROUP = {
    'id': 'group-id-12345',
    'displayName': 'IT Pilot Ring',
    'description': 'Pilot deployment group',
    'mailEnabled': False,
    'securityEnabled': True,
    '@odata.type': '#microsoft.graph.group'
}

SAMPLE_APP_INSTALL_SUMMARY = {
    'id': 'summary-id-12345',
    'installedDeviceCount': 8,
    'failedDeviceCount': 1,
    'pendingInstallDeviceCount': 1,
    'notApplicableDeviceCount': 0,
    '@odata.type': '#microsoft.graph.mobileAppInstallSummary'
}

SAMPLE_DEVICE_STATUS_SUCCESS = {
    'id': 'status-id-1',
    'deviceName': 'TEST-DEVICE-01',
    'deviceId': 'device-id-1',
    'installState': 'installed',
    'errorCode': 0,
    'lastSyncDateTime': '2024-01-15T10:30:00Z',
    '@odata.type': '#microsoft.graph.mobileAppInstallStatus'
}

SAMPLE_DEVICE_STATUS_FAILED = {
    'id': 'status-id-2',
    'deviceName': 'TEST-DEVICE-02',
    'deviceId': 'device-id-2',
    'installState': 'failed',
    'errorCode': -2147024894,
    'lastSyncDateTime': '2024-01-15T10:30:00Z',
    '@odata.type': '#microsoft.graph.mobileAppInstallStatus'
}

SAMPLE_DEVICE_STATUS_PENDING = {
    'id': 'status-id-3',
    'deviceName': 'TEST-DEVICE-03',
    'deviceId': 'device-id-3',
    'installState': 'pending',
    'errorCode': 0,
    'lastSyncDateTime': '2024-01-15T10:30:00Z',
    '@odata.type': '#microsoft.graph.mobileAppInstallStatus'
}

SAMPLE_DEVICE_STATUSES_RESPONSE = {
    'value': [
        SAMPLE_DEVICE_STATUS_SUCCESS,
        SAMPLE_DEVICE_STATUS_FAILED,
        SAMPLE_DEVICE_STATUS_PENDING
    ],
    '@odata.count': 3
}

SAMPLE_DRIVER_UPDATE_PROFILE = {
    'id': 'profile-id-12345',
    'displayName': 'Driver Updates - Latitude 7490',
    'description': 'Driver update management for Latitude 7490 (Dell) — manual approval',
    'approvalType': 'manual',
    'roleScopeTagIds': ['0'],
    '@odata.type': '#microsoft.graph.windowsDriverUpdateProfile'
}

SAMPLE_DRIVER_UPDATE_PROFILES_RESPONSE = {
    'value': [SAMPLE_DRIVER_UPDATE_PROFILE],
    '@odata.count': 1
}


class MockGraphClient:
    """Mock Microsoft Graph API Client for testing

    This mock mimics the GraphAPIClient interface and provides
    realistic responses for testing without making actual API calls.
    """

    def __init__(self, fail_auth=False, fail_requests=False):
        """Initialize mock Graph client

        Args:
            fail_auth: If True, authentication will fail
            fail_requests: If True, API requests will fail
        """
        self.tenant_id = 'test-tenant-id'
        self.client_id = 'test-client-id'
        self.client_secret = 'test-client-secret'
        self.graph_endpoint = 'https://graph.microsoft.com'
        self.api_version = 'v1.0'
        self.access_token = None

        self.fail_auth = fail_auth
        self.fail_requests = fail_requests

        # Track method calls for test assertions
        self.calls = {
            'authenticate': 0,
            'get': [],
            'post': [],
            'patch': [],
            'delete': [],
            'create_win32_app': 0,
            'update_win32_app': 0,
            'delete_win32_app': 0,
            'upload_to_azure_storage': 0
        }

        # Storage for created resources (for stateful testing)
        self.apps = {}
        self.content_versions = {}
        self.content_files = {}

        if not fail_auth:
            self._authenticate()

    def _authenticate(self):
        """Mock authentication"""
        self.calls['authenticate'] += 1

        if self.fail_auth:
            raise Exception("Authentication failed: Invalid credentials")

        self.access_token = 'mock-access-token-12345'

    def _get_headers(self):
        """Mock headers"""
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }

    def get(self, endpoint, params=None):
        """Mock GET request"""
        self.calls['get'].append({'endpoint': endpoint, 'params': params})

        if self.fail_requests:
            raise Exception(f"GET request failed: {endpoint}")

        # Return appropriate response based on endpoint
        if 'mobileApps' in endpoint and 'win32LobApp' in endpoint:
            return SAMPLE_WIN32_APPS_RESPONSE
        elif 'groups' in endpoint:
            return SAMPLE_ENTRA_GROUP
        elif 'installSummary' in endpoint:
            return SAMPLE_APP_INSTALL_SUMMARY
        elif 'deviceStatuses' in endpoint:
            return SAMPLE_DEVICE_STATUSES_RESPONSE
        elif 'contentVersions' in endpoint and 'files' in endpoint:
            return SAMPLE_CONTENT_FILE
        elif 'windowsDriverUpdateProfiles' in endpoint:
            return SAMPLE_DRIVER_UPDATE_PROFILES_RESPONSE

        return {'value': []}

    def post(self, endpoint, data=None):
        """Mock POST request"""
        self.calls['post'].append({'endpoint': endpoint, 'data': data})

        if self.fail_requests:
            raise Exception(f"POST request failed: {endpoint}")

        # Return appropriate response based on endpoint
        if 'mobileApps' in endpoint and 'contentVersions' not in endpoint and 'assign' not in endpoint:
            # Create Win32 app
            return SAMPLE_WIN32_APP
        elif 'contentVersions' in endpoint and 'files' not in endpoint:
            # Create content version
            return SAMPLE_CONTENT_VERSION
        elif 'files' in endpoint and 'commit' not in endpoint:
            # Create content file
            return SAMPLE_CONTENT_FILE
        elif 'commit' in endpoint:
            # Commit file or version
            return None
        elif 'assign' in endpoint:
            # App assignment
            return None
        elif 'windowsDriverUpdateProfiles' in endpoint:
            # Create driver update profile
            return SAMPLE_DRIVER_UPDATE_PROFILE

        return {}

    def patch(self, endpoint, data=None):
        """Mock PATCH request"""
        self.calls['patch'].append({'endpoint': endpoint, 'data': data})

        if self.fail_requests:
            raise Exception(f"PATCH request failed: {endpoint}")

        # PATCH typically returns None (204 No Content)
        return None

    def delete(self, endpoint):
        """Mock DELETE request"""
        self.calls['delete'].append({'endpoint': endpoint})

        if self.fail_requests:
            raise Exception(f"DELETE request failed: {endpoint}")

        return True

    # Win32 app methods

    def get_win32_apps(self):
        """Mock get Win32 apps"""
        return self.get("deviceAppManagement/mobileApps?$filter=isof('microsoft.graph.win32LobApp')")

    def get_win32_app(self, app_id):
        """Mock get Win32 app"""
        response = self.get(f"deviceAppManagement/mobileApps/{app_id}")
        if isinstance(response, dict) and 'value' not in response:
            return response
        return SAMPLE_WIN32_APP

    def create_win32_app(self, app_data):
        """Mock create Win32 app"""
        self.calls['create_win32_app'] += 1
        response = self.post("deviceAppManagement/mobileApps", app_data)
        return response

    def update_win32_app(self, app_id, app_data):
        """Mock update Win32 app"""
        self.calls['update_win32_app'] += 1
        return self.patch(f"deviceAppManagement/mobileApps/{app_id}", app_data)

    def delete_win32_app(self, app_id):
        """Mock delete Win32 app"""
        self.calls['delete_win32_app'] += 1
        return self.delete(f"deviceAppManagement/mobileApps/{app_id}")

    # Content publishing methods

    def create_content_version(self, app_id):
        """Mock create content version"""
        return self.post(
            f"deviceAppManagement/mobileApps/{app_id}/microsoft.graph.win32LobApp/contentVersions",
            {}
        )

    def create_content_file(self, app_id, version_id, file_name, unencrypted_size, encrypted_size):
        """Mock create content file"""
        file_data = {
            "@odata.type": "#microsoft.graph.mobileAppContentFile",
            "name": file_name,
            "size": unencrypted_size,
            "sizeEncrypted": encrypted_size,
            "isDependency": False,
        }
        return self.post(
            f"deviceAppManagement/mobileApps/{app_id}/microsoft.graph.win32LobApp/contentVersions/{version_id}/files",
            file_data
        )

    def get_content_file(self, app_id, version_id, file_id):
        """Mock get content file"""
        # Simulate state progression: first call returns azureStorageUriRequestSuccess,
        # subsequent calls return commitFileSuccess
        endpoint = f"deviceAppManagement/mobileApps/{app_id}/microsoft.graph.win32LobApp/contentVersions/{version_id}/files/{file_id}"

        # Check if this file has been committed
        key = f"{app_id}_{version_id}_{file_id}"
        if key in self.content_files and self.content_files[key].get('committed'):
            return SAMPLE_CONTENT_FILE_COMMITTED

        return SAMPLE_CONTENT_FILE

    def commit_content_file(self, app_id, version_id, file_id, encryption_info):
        """Mock commit content file"""
        # Mark file as committed for stateful behavior
        key = f"{app_id}_{version_id}_{file_id}"
        self.content_files[key] = {'committed': True}

        return self.post(
            f"deviceAppManagement/mobileApps/{app_id}/microsoft.graph.win32LobApp/contentVersions/{version_id}/files/{file_id}/commit",
            {"fileEncryptionInfo": encryption_info}
        )

    def commit_content_version(self, app_id, version_id):
        """Mock commit content version"""
        return self.patch(
            f"deviceAppManagement/mobileApps/{app_id}",
            {
                "@odata.type": "#microsoft.graph.win32LobApp",
                "committedContentVersion": str(version_id),
            }
        )

    def wait_for_azure_storage_uri(self, app_id, version_id, file_id, timeout=300):
        """Mock wait for Azure Storage URI"""
        # Immediately return URI without waiting
        return SAMPLE_CONTENT_FILE['azureStorageUri']

    def wait_for_file_commit(self, app_id, version_id, file_id, timeout=300):
        """Mock wait for file commit"""
        # Immediately return without waiting
        return None

    def upload_to_azure_storage(self, sas_url, file_path):
        """Mock upload to Azure Storage"""
        self.calls['upload_to_azure_storage'] += 1
        # Simulate successful upload without actually uploading
        return None

    # Group assignment methods

    def assign_app_to_group(self, app_id, group_id, intent="required"):
        """Mock assign app to group"""
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
        """Mock get Entra ID group"""
        return self.get(f"groups/{group_id}")

    # Driver Update Profile methods (beta API)

    def _beta_post(self, endpoint, data=None):
        """Mock POST to beta endpoint"""
        return self.post(endpoint, data)

    def _beta_get(self, endpoint, params=None):
        """Mock GET from beta endpoint"""
        return self.get(endpoint, params)

    def create_driver_update_profile(self, display_name, description='', approval_type='manual', deferral_days=3):
        """Mock create driver update profile"""
        payload = {
            'approvalType': approval_type,
            'description': description,
            'displayName': display_name,
            'roleScopeTagIds': ['0'],
        }
        if approval_type == 'automatic':
            payload['deploymentDeferralInDays'] = deferral_days

        return self._beta_post('deviceManagement/windowsDriverUpdateProfiles', payload)

    def assign_driver_update_profile(self, profile_id, group_id):
        """Mock assign driver update profile"""
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
            payload
        )

    def list_driver_update_profiles(self):
        """Mock list driver update profiles"""
        return self._beta_get('deviceManagement/windowsDriverUpdateProfiles')

    # Deployment status methods

    def get_app_install_summary(self, app_id):
        """Mock get app install summary"""
        return self.get(f"deviceAppManagement/mobileApps/{app_id}/installSummary")

    def get_app_device_statuses(self, app_id):
        """Mock get app device statuses with pagination"""
        # Return all statuses at once (no pagination in mock)
        response = self.get(f"deviceAppManagement/mobileApps/{app_id}/deviceStatuses")
        return response.get('value', [])

    def _parse_install_statuses(self, device_statuses):
        """Mock parse install statuses"""
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
                counts['pending'] += 1

        return counts


def create_mock_graph_client(**kwargs):
    """Factory function to create a mock Graph client

    Args:
        **kwargs: Arguments to pass to MockGraphClient constructor

    Returns:
        MockGraphClient instance
    """
    return MockGraphClient(**kwargs)


def create_sample_win32_app(app_id=None, display_name=None, publishing_state='published'):
    """Create a sample Win32 app response

    Args:
        app_id: Optional app ID (default: 'app-id-12345')
        display_name: Optional display name (default: 'Test Driver Package')
        publishing_state: Publishing state (default: 'published')

    Returns:
        Dict with Win32 app structure
    """
    app = SAMPLE_WIN32_APP.copy()
    if app_id:
        app['id'] = app_id
    if display_name:
        app['displayName'] = display_name
    if publishing_state:
        app['publishingState'] = publishing_state
    return app


def create_sample_device_status(device_name, install_state='installed', error_code=0):
    """Create a sample device status response

    Args:
        device_name: Device name
        install_state: Install state (installed, failed, pending)
        error_code: Error code (default: 0)

    Returns:
        Dict with device status structure
    """
    return {
        'id': f'status-{device_name}',
        'deviceName': device_name,
        'deviceId': f'device-{device_name}',
        'installState': install_state,
        'errorCode': error_code,
        'lastSyncDateTime': '2024-01-15T10:30:00Z',
        '@odata.type': '#microsoft.graph.mobileAppInstallStatus'
    }
