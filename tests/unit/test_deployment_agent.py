"""Unit tests for Deployment Agent"""

import unittest
from unittest.mock import Mock, patch, MagicMock, mock_open
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import zipfile

from autopackager.agents.deployment.deployment_agent import DeploymentAgent
from autopackager.models.job import Job, JobType
from autopackager.models.package import Package
from autopackager.models.deployment import Deployment, DeploymentStatus


class TestDeploymentAgentCore(unittest.TestCase):
    """Test cases for Deployment Agent core functionality"""

    def setUp(self):
        """Set up test fixtures"""
        # Mock config
        self.mock_config = {
            'deployment_rings': [
                {
                    'ring_id': 0,
                    'name': 'Ring 0 - IT Pilot',
                    'entra_group_id': 'group-id-ring0'
                },
                {
                    'ring_id': 1,
                    'name': 'Ring 1 - Early Adopters',
                    'entra_group_id': 'group-id-ring1'
                }
            ]
        }

        # Create agent with mocked config
        with patch('autopackager.agents.deployment.deployment_agent.get_config', return_value=self.mock_config):
            self.agent = DeploymentAgent()

        # Mock Azure validation so unit tests don't require real Azure credentials
        self.validate_patcher = patch.object(self.agent, '_validate_azure_config')
        self.mock_validate_azure = self.validate_patcher.start()
        self.addCleanup(self.validate_patcher.stop)

        # Sample job
        self.job = Mock(spec=Job)
        self.job.id = 1
        self.job.job_type = JobType.DRIVER_UPDATE
        self.job.vendor = 'Dell'
        self.job.hardware_model = 'Latitude 7420'
        self.job.target_version = 'A10'
        self.job.software_title = 'Chipset Drivers'
        self.job.release_notes = 'Updated chipset drivers'
        self.job.job_metadata = {
            'package_id': 100,
            'release_date': '2024-01-15',
            'release_notes': 'Improved stability'
        }

        # Sample package
        self.package = Mock(spec=Package)
        self.package.id = 100
        self.package.name = 'Dell Latitude 7420 Chipset Drivers'
        self.package.vendor = 'Dell'
        self.package.version = 'A10'
        self.package.intunewin_path = '/tmp/package.intunewin'
        self.package.installer_path = '/tmp/installer.exe'
        self.package.install_command = 'installer.exe /S'
        self.package.uninstall_command = 'installer.exe /U'
        self.package.test_passed = True
        self.package.deployed = False
        self.package.intune_app_id = None
        self.package.detection_rules = [
            {
                '@odata.type': '#microsoft.graph.win32LobAppRegistryRule',
                'ruleType': 'detection',
                'keyPath': 'HKLM\\Software\\Dell\\Chipset',
                'valueName': 'Version',
                'operationType': 'string',
                'comparisonValue': 'A10'
            }
        ]

    @patch('autopackager.agents.deployment.deployment_agent.db_session_scope')
    def test_deploy_success(self, mock_db_session):
        """Test successful deployment workflow"""
        # Mock database session
        mock_session = MagicMock()
        mock_db_session.return_value.__enter__.return_value = mock_session
        mock_session.query.return_value.filter.return_value.first.return_value = self.package

        # Mock graph client methods
        with patch.object(self.agent, '_get_graph_client') as mock_get_client:
            mock_graph_client = Mock()
            mock_get_client.return_value = mock_graph_client

            with patch.object(self.agent, '_create_or_update_intune_app', return_value='app-id-123') as mock_create_update:
                with patch.object(self.agent, '_assign_to_ring') as mock_assign:
                    result = self.agent.deploy(self.job)

                    # Verify result
                    self.assertEqual(result['intune_app_id'], 'app-id-123')
                    self.assertEqual(result['status'], 'deployed')
                    self.assertEqual(result['ring'], 'Ring 0 - IT Pilot')

                    # Verify methods were called (inside context so mocks are still active)
                    mock_create_update.assert_called_once_with(self.package, self.job)
                    mock_assign.assert_called_once_with('app-id-123', self.package, ring_index=0)

    @patch('autopackager.agents.deployment.deployment_agent.db_session_scope')
    def test_deploy_rejects_missing_package_id(self, mock_db_session):
        """Test that deploy rejects job without package_id"""
        self.job.job_metadata = {}

        with self.assertRaises(ValueError) as context:
            self.agent.deploy(self.job)

        self.assertIn('No package ID', str(context.exception))

    @patch('autopackager.agents.deployment.deployment_agent.db_session_scope')
    def test_deploy_rejects_missing_package(self, mock_db_session):
        """Test that deploy rejects job when package not found"""
        mock_session = MagicMock()
        mock_db_session.return_value.__enter__.return_value = mock_session
        mock_session.query.return_value.filter.return_value.first.return_value = None

        with self.assertRaises(ValueError) as context:
            self.agent.deploy(self.job)

        self.assertIn('not found', str(context.exception))

    @patch('autopackager.agents.deployment.deployment_agent.db_session_scope')
    def test_deploy_rejects_untested_package(self, mock_db_session):
        """Test that deploy rejects package that hasn't passed testing"""
        self.package.test_passed = False

        mock_session = MagicMock()
        mock_db_session.return_value.__enter__.return_value = mock_session
        mock_session.query.return_value.filter.return_value.first.return_value = self.package

        with self.assertRaises(Exception) as context:
            self.agent.deploy(self.job)

        self.assertIn('has not passed testing', str(context.exception))

    def test_prepare_app_data_with_full_metadata(self):
        """Test app data preparation with complete metadata"""
        app_data = self.agent._prepare_app_data(self.package, self.job)

        # Verify basic fields
        self.assertEqual(app_data['@odata.type'], '#microsoft.graph.win32LobApp')
        self.assertEqual(app_data['displayName'], self.package.name)
        self.assertEqual(app_data['publisher'], 'Dell')
        self.assertEqual(app_data['displayVersion'], 'A10')
        self.assertEqual(app_data['installCommandLine'], 'installer.exe /S')
        self.assertEqual(app_data['uninstallCommandLine'], 'installer.exe /U')

        # Verify description includes version and model
        self.assertIn('A10', app_data['description'])
        self.assertIn('Latitude 7420', app_data['description'])

        # Verify detection rules
        self.assertIsInstance(app_data['rules'], list)
        self.assertGreater(len(app_data['rules']), 0)

        # Verify install experience
        self.assertEqual(app_data['installExperience']['runAsAccount'], 'system')
        self.assertEqual(app_data['installExperience']['deviceRestartBehavior'], 'suppress')

    def test_prepare_app_data_derives_setup_file_from_installer_path(self):
        """Test that setupFilePath is derived from installer_path"""
        app_data = self.agent._prepare_app_data(self.package, self.job)
        self.assertEqual(app_data['setupFilePath'], 'installer.exe')

    def test_prepare_app_data_derives_setup_file_from_install_command(self):
        """Test that setupFilePath is derived from install_command when installer_path is missing"""
        self.package.installer_path = None
        app_data = self.agent._prepare_app_data(self.package, self.job)
        self.assertEqual(app_data['setupFilePath'], 'installer.exe')

    def test_prepare_app_data_includes_notes(self):
        """Test that notes are included in app data"""
        app_data = self.agent._prepare_app_data(self.package, self.job)

        self.assertIn('notes', app_data)
        self.assertIn('2024-01-15', app_data['notes'])
        self.assertIn('Improved stability', app_data['notes'])
        self.assertIn('Latitude 7420', app_data['notes'])

    def test_prepare_app_data_includes_vendor_support_url(self):
        """Test that vendor support URL is included"""
        app_data = self.agent._prepare_app_data(self.package, self.job)
        self.assertIn('informationUrl', app_data)
        self.assertEqual(app_data['informationUrl'], 'https://www.dell.com/support/home')

    def test_get_vendor_support_url_dell(self):
        """Test Dell vendor support URL"""
        url = self.agent._get_vendor_support_url('Dell')
        self.assertEqual(url, 'https://www.dell.com/support/home')

    def test_get_vendor_support_url_hp(self):
        """Test HP vendor support URL"""
        url = self.agent._get_vendor_support_url('HP')
        self.assertEqual(url, 'https://support.hp.com/drivers')

    def test_get_vendor_support_url_lenovo(self):
        """Test Lenovo vendor support URL"""
        url = self.agent._get_vendor_support_url('Lenovo')
        self.assertEqual(url, 'https://support.lenovo.com/solutions/ht003029')

    def test_get_vendor_support_url_unknown_vendor(self):
        """Test unknown vendor returns empty string"""
        url = self.agent._get_vendor_support_url('UnknownVendor')
        self.assertEqual(url, '')

    def test_get_vendor_support_url_case_insensitive(self):
        """Test vendor URL matching is case-insensitive"""
        url = self.agent._get_vendor_support_url('DELL')
        self.assertEqual(url, 'https://www.dell.com/support/home')

    def test_normalize_rules_converts_beta_to_v1(self):
        """Test that beta schema rules are converted to v1.0"""
        beta_rules = [
            {
                '@odata.type': '#microsoft.graph.win32LobAppRegistryDetection',
                'keyPath': 'HKLM\\Software\\Test',
                'valueName': 'Version',
                'detectionType': 'string',
                'detectionValue': '1.0'
            }
        ]

        normalized = self.agent._normalize_rules(beta_rules)

        self.assertEqual(normalized[0]['@odata.type'], '#microsoft.graph.win32LobAppRegistryRule')
        self.assertEqual(normalized[0]['operationType'], 'string')
        self.assertEqual(normalized[0]['comparisonValue'], '1.0')
        self.assertEqual(normalized[0]['ruleType'], 'detection')

    def test_normalize_rules_preserves_v1_format(self):
        """Test that v1.0 rules are preserved as-is"""
        v1_rules = [
            {
                '@odata.type': '#microsoft.graph.win32LobAppRegistryRule',
                'keyPath': 'HKLM\\Software\\Test',
                'ruleType': 'detection',
                'operationType': 'string'
            }
        ]

        normalized = self.agent._normalize_rules(v1_rules)

        self.assertEqual(normalized[0]['@odata.type'], '#microsoft.graph.win32LobAppRegistryRule')
        self.assertEqual(normalized[0]['operationType'], 'string')
        self.assertEqual(normalized[0]['ruleType'], 'detection')

    def test_normalize_rules_handles_empty_list(self):
        """Test that empty rules list is handled"""
        normalized = self.agent._normalize_rules([])
        self.assertEqual(normalized, [])

    def test_normalize_rules_handles_none(self):
        """Test that None rules is handled"""
        normalized = self.agent._normalize_rules(None)
        self.assertEqual(normalized, [])

    @patch('autopackager.agents.deployment.deployment_agent.GraphAPIClient')
    def test_create_or_update_intune_app_creates_new_app(self, mock_graph_class):
        """Test creating a new Intune app when none exists"""
        mock_graph_client = Mock()
        mock_graph_class.return_value = mock_graph_client
        self.agent.graph_client = mock_graph_client

        # No existing apps
        mock_graph_client.get_win32_apps.return_value = {'value': []}
        mock_graph_client.create_win32_app.return_value = {'id': 'new-app-id'}

        with patch.object(self.agent, '_upload_and_publish') as mock_upload:
            app_id = self.agent._create_or_update_intune_app(self.package, self.job)

            self.assertEqual(app_id, 'new-app-id')
            mock_graph_client.create_win32_app.assert_called_once()
            mock_upload.assert_called_once_with(mock_graph_client, 'new-app-id', self.package)

    @patch('autopackager.agents.deployment.deployment_agent.GraphAPIClient')
    def test_create_or_update_intune_app_updates_published_app(self, mock_graph_class):
        """Test updating an existing published app"""
        mock_graph_client = Mock()
        mock_graph_class.return_value = mock_graph_client
        self.agent.graph_client = mock_graph_client

        # Existing published app
        existing_app = {
            'id': 'existing-app-id',
            'displayName': self.package.name,
            'publishingState': 'published'
        }
        mock_graph_client.get_win32_apps.return_value = {'value': [existing_app]}

        with patch.object(self.agent, '_upload_and_publish') as mock_upload:
            app_id = self.agent._create_or_update_intune_app(self.package, self.job)

            self.assertEqual(app_id, 'existing-app-id')
            mock_graph_client.update_win32_app.assert_called_once()
            mock_upload.assert_called_once_with(mock_graph_client, 'existing-app-id', self.package)

    @patch('autopackager.agents.deployment.deployment_agent.GraphAPIClient')
    @patch('autopackager.agents.deployment.deployment_agent.time.sleep')
    def test_create_or_update_intune_app_deletes_unpublished_app(self, mock_sleep, mock_graph_class):
        """Test that unpublished apps are deleted and recreated"""
        mock_graph_client = Mock()
        mock_graph_class.return_value = mock_graph_client
        self.agent.graph_client = mock_graph_client

        # Existing unpublished app
        existing_app = {
            'id': 'broken-app-id',
            'displayName': self.package.name,
            'publishingState': 'notPublished'
        }
        mock_graph_client.get_win32_apps.return_value = {'value': [existing_app]}
        mock_graph_client.create_win32_app.return_value = {'id': 'new-app-id'}

        with patch.object(self.agent, '_upload_and_publish'):
            app_id = self.agent._create_or_update_intune_app(self.package, self.job)

        self.assertEqual(app_id, 'new-app-id')
        mock_graph_client.delete_win32_app.assert_called_once_with('broken-app-id')
        mock_graph_client.create_win32_app.assert_called_once()
        mock_sleep.assert_called_once_with(3)

    @patch('autopackager.agents.deployment.deployment_agent.db_session_scope')
    def test_assign_to_ring_success(self, mock_db_session):
        """Test successful ring assignment"""
        mock_session = MagicMock()
        mock_db_session.return_value.__enter__.return_value = mock_session

        mock_graph_client = Mock()
        with patch.object(self.agent, '_get_graph_client', return_value=mock_graph_client):
            self.agent._assign_to_ring('app-id-123', self.package, ring_index=0)

        mock_graph_client.assign_app_to_group.assert_called_once_with(
            'app-id-123',
            'group-id-ring0',
            intent='required'
        )

    @patch('autopackager.agents.deployment.deployment_agent.db_session_scope')
    def test_assign_to_ring_handles_invalid_ring_index(self, mock_db_session):
        """Test that invalid ring index is handled gracefully"""
        mock_graph_client = Mock()
        with patch.object(self.agent, '_get_graph_client', return_value=mock_graph_client):
            # Should not raise exception, just log error and return
            self.agent._assign_to_ring('app-id-123', self.package, ring_index=99)

        # Verify no assignment was attempted
        mock_graph_client.assign_app_to_group.assert_not_called()

    @patch('autopackager.agents.deployment.deployment_agent.db_session_scope')
    def test_assign_to_ring_propagates_graph_api_errors(self, mock_db_session):
        """Test that Graph API errors are propagated"""
        mock_session = MagicMock()
        mock_db_session.return_value.__enter__.return_value = mock_session

        mock_graph_client = Mock()
        mock_graph_client.assign_app_to_group.side_effect = Exception('Graph API error')

        with patch.object(self.agent, '_get_graph_client', return_value=mock_graph_client):
            with self.assertRaises(Exception) as context:
                self.agent._assign_to_ring('app-id-123', self.package, ring_index=0)

            self.assertIn('Graph API error', str(context.exception))

    @patch('autopackager.agents.deployment.deployment_agent.db_session_scope')
    def test_create_deployment_record(self, mock_db_session):
        """Test creating deployment record"""
        mock_session = MagicMock()
        mock_db_session.return_value.__enter__.return_value = mock_session

        ring = self.mock_config['deployment_rings'][0]
        self.agent._create_deployment_record(self.package.id, 'app-id-123', ring)

        # Verify deployment object was created and added
        mock_session.add.assert_called_once()
        deployment = mock_session.add.call_args[0][0]

        self.assertIsInstance(deployment, Deployment)
        self.assertEqual(deployment.package_id, self.package.id)
        self.assertEqual(deployment.intune_app_id, 'app-id-123')
        self.assertEqual(deployment.ring_id, 0)
        self.assertEqual(deployment.ring_name, 'Ring 0 - IT Pilot')
        self.assertEqual(deployment.status, DeploymentStatus.IN_PROGRESS)

    @patch('autopackager.agents.deployment.deployment_agent.db_session_scope')
    def test_update_package_deployment_status(self, mock_db_session):
        """Test updating package deployment status"""
        mock_session = MagicMock()
        mock_db_session.return_value.__enter__.return_value = mock_session
        mock_session.query.return_value.filter.return_value.first.return_value = self.package

        self.agent._update_package_deployment_status(self.package.id, 'app-id-123')

        # Verify package was updated
        self.assertTrue(self.package.deployed)
        self.assertEqual(self.package.intune_app_id, 'app-id-123')

    @patch('autopackager.agents.deployment.deployment_agent.db_session_scope')
    def test_get_package_success(self, mock_db_session):
        """Test getting package by ID"""
        mock_session = MagicMock()
        mock_db_session.return_value.__enter__.return_value = mock_session
        mock_session.query.return_value.filter.return_value.first.return_value = self.package

        package = self.agent._get_package(self.package.id)

        self.assertEqual(package, self.package)
        mock_session.expunge.assert_called_once_with(self.package)

    @patch('autopackager.agents.deployment.deployment_agent.db_session_scope')
    def test_get_package_returns_none_when_not_found(self, mock_db_session):
        """Test getting package returns None when not found"""
        mock_session = MagicMock()
        mock_db_session.return_value.__enter__.return_value = mock_session
        mock_session.query.return_value.filter.return_value.first.return_value = None

        package = self.agent._get_package(999)

        self.assertIsNone(package)

    def test_parse_intunewin_success(self):
        """Test parsing .intunewin file successfully"""
        # Create a temporary .intunewin file
        with tempfile.NamedTemporaryFile(suffix='.intunewin', delete=False) as tmp_file:
            intunewin_path = tmp_file.name

            # Create mock Detection.xml content
            detection_xml = """<?xml version="1.0" encoding="utf-8"?>
<ApplicationInfo>
    <UnencryptedContentSize>1024000</UnencryptedContentSize>
    <EncryptedContentSize>1024512</EncryptedContentSize>
    <EncryptionInfo>
        <EncryptionKey>base64encodedkey</EncryptionKey>
        <MacKey>base64encodedmac</MacKey>
        <InitializationVector>base64encodediv</InitializationVector>
        <Mac>base64encodedmac</Mac>
        <ProfileIdentifier>ProfileVersion1</ProfileIdentifier>
        <FileDigest>base64digest</FileDigest>
        <FileDigestAlgorithm>SHA256</FileDigestAlgorithm>
    </EncryptionInfo>
</ApplicationInfo>"""

            # Create the .intunewin ZIP structure
            with zipfile.ZipFile(intunewin_path, 'w') as zf:
                zf.writestr('IntunePackage.intunewin', b'encrypted binary content here')
                zf.writestr('Detection.xml', detection_xml)

        try:
            result = self.agent._parse_intunewin(intunewin_path)

            # Verify result structure
            self.assertIn('encrypted_path', result)
            self.assertIn('unencrypted_size', result)
            self.assertIn('encrypted_size', result)
            self.assertIn('encryption_info', result)

            # Verify sizes
            self.assertEqual(result['unencrypted_size'], 1024000)

            # Verify encryption info
            enc_info = result['encryption_info']
            self.assertEqual(enc_info['encryptionKey'], 'base64encodedkey')
            self.assertEqual(enc_info['macKey'], 'base64encodedmac')
            self.assertEqual(enc_info['initializationVector'], 'base64encodediv')
            self.assertEqual(enc_info['fileDigestAlgorithm'], 'SHA256')

        finally:
            # Cleanup
            Path(intunewin_path).unlink(missing_ok=True)
            if 'encrypted_path' in result and Path(result['encrypted_path']).exists():
                Path(result['encrypted_path']).unlink(missing_ok=True)

    def test_parse_intunewin_missing_content_file(self):
        """Test that missing IntunePackage.intunewin is detected"""
        with tempfile.NamedTemporaryFile(suffix='.intunewin', delete=False) as tmp_file:
            intunewin_path = tmp_file.name

            # Create ZIP without IntunePackage.intunewin
            with zipfile.ZipFile(intunewin_path, 'w') as zf:
                zf.writestr('Detection.xml', '<xml/>')

        try:
            with self.assertRaises(Exception) as context:
                self.agent._parse_intunewin(intunewin_path)

            self.assertIn('IntunePackage.intunewin not found', str(context.exception))

        finally:
            Path(intunewin_path).unlink(missing_ok=True)

    def test_parse_intunewin_missing_detection_xml(self):
        """Test that missing Detection.xml is detected"""
        with tempfile.NamedTemporaryFile(suffix='.intunewin', delete=False) as tmp_file:
            intunewin_path = tmp_file.name

            # Create ZIP without Detection.xml
            with zipfile.ZipFile(intunewin_path, 'w') as zf:
                zf.writestr('IntunePackage.intunewin', b'content')

        try:
            with self.assertRaises(Exception) as context:
                self.agent._parse_intunewin(intunewin_path)

            self.assertIn('Detection.xml not found', str(context.exception))

        finally:
            Path(intunewin_path).unlink(missing_ok=True)

    def test_upload_and_publish_rejects_missing_file(self):
        """Test that missing .intunewin file is detected"""
        self.package.intunewin_path = '/nonexistent/file.intunewin'

        mock_graph_client = Mock()

        with self.assertRaises(Exception) as context:
            self.agent._upload_and_publish(mock_graph_client, 'app-id', self.package)

        self.assertIn('not found', str(context.exception))

    def test_upload_and_publish_rejects_empty_file(self):
        """Test that empty .intunewin file is detected"""
        # Create empty temp file
        with tempfile.NamedTemporaryFile(suffix='.intunewin', delete=False) as tmp_file:
            intunewin_path = tmp_file.name

        self.package.intunewin_path = intunewin_path

        try:
            mock_graph_client = Mock()

            with self.assertRaises(Exception) as context:
                self.agent._upload_and_publish(mock_graph_client, 'app-id', self.package)

            self.assertIn('empty', str(context.exception))

        finally:
            Path(intunewin_path).unlink(missing_ok=True)

    def test_get_deployment_status_success(self):
        """Test getting deployment status from Intune"""
        mock_graph_client = Mock()
        mock_graph_client.get_app_device_statuses.return_value = [
            {'installState': 'installed'},
            {'installState': 'installed'},
            {'installState': 'failed', 'errorCode': '0x80070005'}
        ]
        mock_graph_client._parse_install_statuses.return_value = {
            'installed': 2,
            'failed': 1,
            'pending': 0,
            'not_applicable': 0,
            'failed_device_details': [
                {'deviceName': 'PC-001', 'errorCode': '0x80070005'}
            ]
        }

        with patch.object(self.agent, '_get_graph_client', return_value=mock_graph_client):
            result = self.agent.get_deployment_status('app-id-123')

        self.assertEqual(result['app_id'], 'app-id-123')
        self.assertEqual(result['installed_count'], 2)
        self.assertEqual(result['failed_count'], 1)
        self.assertEqual(result['total_targeted'], 3)
        self.assertEqual(len(result['failed_devices']), 1)

    def test_get_deployment_status_handles_errors(self):
        """Test error handling in get_deployment_status"""
        mock_graph_client = Mock()
        mock_graph_client.get_app_device_statuses.side_effect = Exception('Graph API error')

        with patch.object(self.agent, '_get_graph_client', return_value=mock_graph_client):
            result = self.agent.get_deployment_status('app-id-123')

        self.assertEqual(result['app_id'], 'app-id-123')
        self.assertIn('error', result)
        self.assertIn('Graph API error', result['error'])

    @patch('autopackager.agents.deployment.deployment_agent.db_session_scope')
    @patch('autopackager.agents.deployment.deployment_agent.datetime')
    def test_update_deployment_status(self, mock_datetime, mock_db_session):
        """Test updating deployment record with status data"""
        mock_now = datetime(2024, 1, 15, 12, 0, 0)
        mock_datetime.utcnow.return_value = mock_now

        mock_deployment = Mock(spec=Deployment)
        mock_session = MagicMock()
        mock_db_session.return_value.__enter__.return_value = mock_session
        mock_session.query.return_value.filter.return_value.first.return_value = mock_deployment

        status_data = {
            'installed_count': 10,
            'failed_count': 2,
            'pending_count': 3,
            'not_applicable_count': 1,
            'total_targeted': 16,
            'failed_devices': [{'deviceName': 'PC-001'}]
        }

        self.agent.update_deployment_status(1, status_data)

        # Verify deployment was updated
        self.assertEqual(mock_deployment.successful_installs, 10)
        self.assertEqual(mock_deployment.failed_installs, 2)
        self.assertEqual(mock_deployment.pending_installs, 3)
        self.assertEqual(mock_deployment.not_applicable_installs, 1)
        self.assertEqual(mock_deployment.target_device_count, 16)
        self.assertEqual(mock_deployment.last_status_check, mock_now)
        self.assertEqual(mock_deployment.device_status_details, [{'deviceName': 'PC-001'}])

    @patch('autopackager.agents.deployment.deployment_agent.db_session_scope')
    def test_update_deployment_status_handles_missing_deployment(self, mock_db_session):
        """Test that missing deployment is handled"""
        mock_session = MagicMock()
        mock_db_session.return_value.__enter__.return_value = mock_session
        mock_session.query.return_value.filter.return_value.first.return_value = None

        with self.assertRaises(ValueError) as context:
            self.agent.update_deployment_status(999, {})

        self.assertIn('not found', str(context.exception))

    @patch('autopackager.agents.deployment.deployment_agent.db_session_scope')
    def test_check_all_deployments_success(self, mock_db_session):
        """Test checking all in-progress deployments"""
        # Mock deployments
        mock_deployment1 = Mock(spec=Deployment)
        mock_deployment1.id = 1
        mock_deployment1.intune_app_id = 'app-id-1'
        mock_deployment1.ring_name = 'Ring 0'
        mock_deployment1.status = DeploymentStatus.IN_PROGRESS

        mock_deployment2 = Mock(spec=Deployment)
        mock_deployment2.id = 2
        mock_deployment2.intune_app_id = 'app-id-2'
        mock_deployment2.ring_name = 'Ring 1'
        mock_deployment2.status = DeploymentStatus.IN_PROGRESS

        mock_session = MagicMock()
        mock_db_session.return_value.__enter__.return_value = mock_session
        mock_session.query.return_value.filter.return_value.all.return_value = [
            mock_deployment1, mock_deployment2
        ]

        with patch.object(self.agent, 'get_deployment_status') as mock_get_status:
            with patch.object(self.agent, 'update_deployment_status') as mock_update_status:
                mock_get_status.side_effect = [
                    {
                        'installed_count': 5,
                        'failed_count': 1,
                        'pending_count': 2,
                        'not_applicable_count': 0,
                        'total_targeted': 8
                    },
                    {
                        'installed_count': 10,
                        'failed_count': 0,
                        'pending_count': 5,
                        'not_applicable_count': 1,
                        'total_targeted': 16
                    }
                ]

                result = self.agent.check_all_deployments()

        # Verify result
        self.assertEqual(result['total_checked'], 2)
        self.assertEqual(result['successful_updates'], 2)
        self.assertEqual(result['failed_updates'], 0)
        self.assertEqual(result['summary']['total_installed'], 15)
        self.assertEqual(result['summary']['total_failed'], 1)
        self.assertEqual(result['summary']['total_pending'], 7)

    @patch('autopackager.agents.deployment.deployment_agent.db_session_scope')
    def test_check_all_deployments_handles_no_deployments(self, mock_db_session):
        """Test check_all_deployments with no in-progress deployments"""
        mock_session = MagicMock()
        mock_db_session.return_value.__enter__.return_value = mock_session
        mock_session.query.return_value.filter.return_value.all.return_value = []

        result = self.agent.check_all_deployments()

        self.assertEqual(result['total_checked'], 0)
        self.assertEqual(result['successful_updates'], 0)
        self.assertEqual(result['failed_updates'], 0)

    @patch('autopackager.agents.deployment.deployment_agent.db_session_scope')
    def test_check_all_deployments_handles_errors(self, mock_db_session):
        """Test that check_all_deployments handles individual deployment errors"""
        mock_deployment1 = Mock(spec=Deployment)
        mock_deployment1.id = 1
        mock_deployment1.intune_app_id = 'app-id-1'
        mock_deployment1.ring_name = 'Ring 0'
        mock_deployment1.status = DeploymentStatus.IN_PROGRESS

        mock_session = MagicMock()
        mock_db_session.return_value.__enter__.return_value = mock_session
        mock_session.query.return_value.filter.return_value.all.return_value = [mock_deployment1]

        with patch.object(self.agent, 'get_deployment_status') as mock_get_status:
            mock_get_status.return_value = {'error': 'Graph API error'}

            result = self.agent.check_all_deployments()

        # Verify error was logged
        self.assertEqual(result['total_checked'], 1)
        self.assertEqual(result['successful_updates'], 0)
        self.assertEqual(result['failed_updates'], 1)
        self.assertEqual(len(result['errors']), 1)

    @patch('autopackager.agents.deployment.deployment_agent.GraphAPIClient')
    def test_deploy_driver_update_profile_creates_new_profile(self, mock_graph_class):
        """Test creating a new driver update profile"""
        mock_graph_client = Mock()
        mock_graph_class.return_value = mock_graph_client
        self.agent.graph_client = mock_graph_client

        # No existing profiles
        mock_graph_client.list_driver_update_profiles.return_value = {'value': []}
        mock_graph_client.create_driver_update_profile.return_value = {'id': 'profile-id-123'}

        result = self.agent.deploy_driver_update_profile(self.job, approval_type='manual', deferral_days=3)

        self.assertEqual(result['profile_id'], 'profile-id-123')
        self.assertEqual(result['status'], 'created')
        self.assertEqual(result['approval_type'], 'manual')
        mock_graph_client.create_driver_update_profile.assert_called_once()
        mock_graph_client.assign_driver_update_profile.assert_called_once_with('profile-id-123', 'group-id-ring0')

    @patch('autopackager.agents.deployment.deployment_agent.GraphAPIClient')
    def test_deploy_driver_update_profile_handles_existing_profile(self, mock_graph_class):
        """Test that existing driver update profile is detected"""
        mock_graph_client = Mock()
        mock_graph_class.return_value = mock_graph_client
        self.agent.graph_client = mock_graph_client

        # Existing profile
        existing_profile = {
            'id': 'existing-profile-id',
            'displayName': 'Driver Updates - Latitude 7420'
        }
        mock_graph_client.list_driver_update_profiles.return_value = {'value': [existing_profile]}

        result = self.agent.deploy_driver_update_profile(self.job)

        self.assertEqual(result['profile_id'], 'existing-profile-id')
        self.assertEqual(result['status'], 'already_exists')
        mock_graph_client.create_driver_update_profile.assert_not_called()


class TestDeploymentAgentPromotion(unittest.TestCase):
    """Test cases for Deployment Agent promotion eligibility logic"""

    def setUp(self):
        """Set up test fixtures"""
        # Mock config with promotion enabled
        self.mock_config = {
            'deployment_rings': [
                {
                    'ring_id': 0,
                    'name': 'Ring 0 - IT Pilot',
                    'entra_group_id': 'group-id-ring0'
                },
                {
                    'ring_id': 1,
                    'name': 'Ring 1 - Early Adopters',
                    'entra_group_id': 'group-id-ring1'
                },
                {
                    'ring_id': 2,
                    'name': 'Ring 2 - Production',
                    'entra_group_id': 'group-id-ring2'
                }
            ],
            'ring_promotion': {
                'enabled': True,
                'evaluation_period_hours': 48,
                'minimum_install_count': 10,
                'success_threshold_percent': 90.0
            }
        }

        # Create agent with mocked config
        with patch('autopackager.agents.deployment.deployment_agent.get_config', return_value=self.mock_config):
            self.agent = DeploymentAgent()

        # Mock Azure validation so unit tests don't require real Azure credentials
        self.validate_patcher = patch.object(self.agent, '_validate_azure_config')
        self.mock_validate_azure = self.validate_patcher.start()
        self.addCleanup(self.validate_patcher.stop)

        # Sample deployment with typical eligible state
        self.deployment = Mock(spec=Deployment)
        self.deployment.id = 1
        self.deployment.package_id = 100
        self.deployment.intune_app_id = 'app-id-123'
        self.deployment.ring_id = 0
        self.deployment.ring_name = 'Ring 0 - IT Pilot'
        self.deployment.status = DeploymentStatus.IN_PROGRESS
        self.deployment.deployed_at = datetime.utcnow() - timedelta(hours=50)
        self.deployment.successful_installs = 15
        self.deployment.failed_installs = 1
        self.deployment.pending_installs = 2
        self.deployment.target_device_count = 18
        self.deployment.promotion_blocked_reason = None

    @patch('autopackager.agents.deployment.deployment_agent.datetime')
    def test_is_eligible_for_promotion_success(self, mock_datetime):
        """Test successful eligibility check when all criteria are met"""
        mock_now = datetime(2024, 1, 15, 12, 0, 0)
        mock_datetime.utcnow.return_value = mock_now
        self.deployment.deployed_at = mock_now - timedelta(hours=50)

        eligible, reason = self.agent.is_eligible_for_promotion(self.deployment)

        self.assertTrue(eligible)
        self.assertIn('Eligible for promotion to Ring 1', reason)
        self.assertIn('success rate:', reason)

    def test_is_eligible_for_promotion_disabled_in_config(self):
        """Test that promotion disabled in config is detected"""
        # Disable promotion in config
        self.agent.config['ring_promotion']['enabled'] = False

        eligible, reason = self.agent.is_eligible_for_promotion(self.deployment)

        self.assertFalse(eligible)
        self.assertEqual(reason, "Ring promotion is disabled in configuration")

    def test_is_eligible_for_promotion_manually_blocked(self):
        """Test that manually blocked promotion is detected"""
        self.deployment.promotion_blocked_reason = "Critical bug detected in Ring 0"

        eligible, reason = self.agent.is_eligible_for_promotion(self.deployment)

        self.assertFalse(eligible)
        self.assertIn("Promotion manually blocked", reason)
        self.assertIn("Critical bug detected", reason)

    def test_is_eligible_for_promotion_unknown_ring_id(self):
        """Test that unknown ring_id is detected"""
        self.deployment.ring_id = 999

        eligible, reason = self.agent.is_eligible_for_promotion(self.deployment)

        self.assertFalse(eligible)
        self.assertIn("Unknown ring_id", reason)

    def test_is_eligible_for_promotion_already_at_final_ring(self):
        """Test that deployments at final ring are not eligible"""
        self.deployment.ring_id = 2  # Final ring in our config

        eligible, reason = self.agent.is_eligible_for_promotion(self.deployment)

        self.assertFalse(eligible)
        self.assertEqual(reason, "Already at final ring")

    def test_is_eligible_for_promotion_status_not_in_progress(self):
        """Test that non-IN_PROGRESS status is detected"""
        self.deployment.status = DeploymentStatus.SUCCESSFUL

        eligible, reason = self.agent.is_eligible_for_promotion(self.deployment)

        self.assertFalse(eligible)
        self.assertIn("Deployment status is successful", reason)
        self.assertIn("not IN_PROGRESS", reason)

    def test_is_eligible_for_promotion_no_deployed_at_timestamp(self):
        """Test that missing deployed_at timestamp is detected"""
        self.deployment.deployed_at = None

        eligible, reason = self.agent.is_eligible_for_promotion(self.deployment)

        self.assertFalse(eligible)
        self.assertEqual(reason, "Deployment has no deployed_at timestamp")

    @patch('autopackager.agents.deployment.deployment_agent.datetime')
    def test_is_eligible_for_promotion_dwell_time_not_met(self, mock_datetime):
        """Test that insufficient dwell time is detected"""
        mock_now = datetime(2024, 1, 15, 12, 0, 0)
        mock_datetime.utcnow.return_value = mock_now
        # Only 24 hours since deployment (need 48)
        self.deployment.deployed_at = mock_now - timedelta(hours=24)

        eligible, reason = self.agent.is_eligible_for_promotion(self.deployment)

        self.assertFalse(eligible)
        self.assertIn("Dwell time not met", reason)
        self.assertIn("24.0 hours remaining", reason)

    @patch('autopackager.agents.deployment.deployment_agent.datetime')
    def test_is_eligible_for_promotion_minimum_install_count_not_met(self, mock_datetime):
        """Test that minimum install count requirement is enforced"""
        mock_now = datetime(2024, 1, 15, 12, 0, 0)
        mock_datetime.utcnow.return_value = mock_now
        self.deployment.deployed_at = mock_now - timedelta(hours=50)
        # Only 5 total installs (need 10)
        self.deployment.successful_installs = 4
        self.deployment.failed_installs = 1

        eligible, reason = self.agent.is_eligible_for_promotion(self.deployment)

        self.assertFalse(eligible)
        self.assertIn("Minimum install count not met", reason)
        self.assertIn("5/10", reason)

    @patch('autopackager.agents.deployment.deployment_agent.datetime')
    def test_is_eligible_for_promotion_success_rate_below_threshold(self, mock_datetime):
        """Test that success rate threshold is enforced"""
        mock_now = datetime(2024, 1, 15, 12, 0, 0)
        mock_datetime.utcnow.return_value = mock_now
        self.deployment.deployed_at = mock_now - timedelta(hours=50)
        # Success rate: 12/20 = 60% (need 90%)
        self.deployment.successful_installs = 12
        self.deployment.failed_installs = 8

        eligible, reason = self.agent.is_eligible_for_promotion(self.deployment)

        self.assertFalse(eligible)
        self.assertIn("Success rate", reason)
        self.assertIn("60.0%", reason)
        self.assertIn("below threshold 90.0%", reason)

    @patch('autopackager.agents.deployment.deployment_agent.datetime')
    def test_is_eligible_for_promotion_zero_installs(self, mock_datetime):
        """Test that zero installs is detected"""
        mock_now = datetime(2024, 1, 15, 12, 0, 0)
        mock_datetime.utcnow.return_value = mock_now
        self.deployment.deployed_at = mock_now - timedelta(hours=50)
        self.deployment.successful_installs = 0
        self.deployment.failed_installs = 0

        eligible, reason = self.agent.is_eligible_for_promotion(self.deployment)

        self.assertFalse(eligible)
        # Zero installs fails the minimum install count check first
        self.assertIn("Minimum install count not met", reason)
        self.assertIn("0/10", reason)

    @patch('autopackager.agents.deployment.deployment_agent.datetime')
    def test_is_eligible_for_promotion_edge_case_minimum_threshold(self, mock_datetime):
        """Test eligibility at exact minimum threshold"""
        mock_now = datetime(2024, 1, 15, 12, 0, 0)
        mock_datetime.utcnow.return_value = mock_now
        self.deployment.deployed_at = mock_now - timedelta(hours=48)  # Exact minimum
        # Exactly 10 installs with 90% success rate
        self.deployment.successful_installs = 9
        self.deployment.failed_installs = 1

        eligible, reason = self.agent.is_eligible_for_promotion(self.deployment)

        self.assertTrue(eligible)
        self.assertIn('Eligible for promotion', reason)

    @patch('autopackager.agents.deployment.deployment_agent.datetime')
    def test_is_eligible_for_promotion_perfect_success_rate(self, mock_datetime):
        """Test eligibility with 100% success rate"""
        mock_now = datetime(2024, 1, 15, 12, 0, 0)
        mock_datetime.utcnow.return_value = mock_now
        self.deployment.deployed_at = mock_now - timedelta(hours=72)
        self.deployment.successful_installs = 20
        self.deployment.failed_installs = 0

        eligible, reason = self.agent.is_eligible_for_promotion(self.deployment)

        self.assertTrue(eligible)
        self.assertIn('success rate: 100.0%', reason)

    @patch('autopackager.agents.deployment.deployment_agent.datetime')
    def test_is_eligible_for_promotion_custom_evaluation_period(self, mock_datetime):
        """Test custom evaluation period from config"""
        mock_now = datetime(2024, 1, 15, 12, 0, 0)
        mock_datetime.utcnow.return_value = mock_now
        # Set custom evaluation period to 24 hours
        self.agent.config['ring_promotion']['evaluation_period_hours'] = 24
        self.deployment.deployed_at = mock_now - timedelta(hours=25)

        eligible, reason = self.agent.is_eligible_for_promotion(self.deployment)

        self.assertTrue(eligible)
        self.assertIn('Eligible for promotion', reason)

    @patch('autopackager.agents.deployment.deployment_agent.datetime')
    def test_is_eligible_for_promotion_custom_minimum_install_count(self, mock_datetime):
        """Test custom minimum install count from config"""
        mock_now = datetime(2024, 1, 15, 12, 0, 0)
        mock_datetime.utcnow.return_value = mock_now
        self.deployment.deployed_at = mock_now - timedelta(hours=50)
        # Set custom minimum to 5
        self.agent.config['ring_promotion']['minimum_install_count'] = 5
        self.deployment.successful_installs = 5
        self.deployment.failed_installs = 0

        eligible, reason = self.agent.is_eligible_for_promotion(self.deployment)

        self.assertTrue(eligible)
        self.assertIn('Eligible for promotion', reason)

    @patch('autopackager.agents.deployment.deployment_agent.datetime')
    def test_is_eligible_for_promotion_custom_success_threshold(self, mock_datetime):
        """Test custom success threshold from config"""
        mock_now = datetime(2024, 1, 15, 12, 0, 0)
        mock_datetime.utcnow.return_value = mock_now
        self.deployment.deployed_at = mock_now - timedelta(hours=50)
        # Set custom threshold to 80%
        self.agent.config['ring_promotion']['success_threshold_percent'] = 80.0
        # 85% success rate
        self.deployment.successful_installs = 17
        self.deployment.failed_installs = 3

        eligible, reason = self.agent.is_eligible_for_promotion(self.deployment)

        self.assertTrue(eligible)
        self.assertIn('success rate: 85.0%', reason)


if __name__ == '__main__':
    unittest.main()
