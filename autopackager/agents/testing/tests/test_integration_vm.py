"""Integration tests for VM-based testing workflow"""

import unittest
from unittest.mock import Mock, patch, MagicMock

from autopackager.agents.testing.testing_agent import TestingAgent
from autopackager.agents.testing.vm_providers.hyperv_provider import HyperVProvider
from autopackager.models.package import Package


class TestVMTestingIntegration(unittest.TestCase):
    """Integration tests for VM-based testing workflow"""

    @patch('autopackager.agents.testing.vm_providers.hyperv_provider.subprocess.run')
    def test_complete_vm_testing_workflow(self, mock_subprocess):
        """Test complete workflow from TestingAgent to HyperVProvider"""
        # Mock all PowerShell commands to succeed
        def mock_subprocess_run(cmd, **kwargs):
            command = cmd[2] if len(cmd) > 2 else ''

            # Mock different PowerShell commands
            if 'Restore-VMSnapshot' in command:
                return Mock(returncode=0, stdout='', stderr='')
            elif 'Start-VM' in command:
                return Mock(returncode=0, stdout='', stderr='')
            elif 'Heartbeat' in command:
                return Mock(returncode=0, stdout='OkApplicationsHealthy\n', stderr='')
            elif 'Copy-VMFile' in command:
                return Mock(returncode=0, stdout='', stderr='')
            elif 'Invoke-Command' in command:
                return Mock(returncode=0, stdout='Installation complete\n', stderr='')
            elif 'Get-PnpDevice' in command:
                return Mock(returncode=0, stdout='[]\n', stderr='')
            elif 'Get-WinEvent' in command:
                return Mock(returncode=0, stdout='[]\n', stderr='')
            elif 'Get-Process' in command:
                return Mock(returncode=0, stdout='System\n', stderr='')
            elif 'Get-Service' in command:
                return Mock(returncode=0, stdout='50\n', stderr='')
            elif 'Minidump' in command:
                return Mock(returncode=0, stdout='0\n', stderr='')
            elif 'Stop-VM' in command:
                return Mock(returncode=0, stdout='', stderr='')
            elif 'IPAddresses' in command:
                return Mock(returncode=0, stdout='192.168.1.100\n', stderr='')
            else:
                return Mock(returncode=0, stdout='', stderr='')

        mock_subprocess.side_effect = mock_subprocess_run

        # Create test package
        package = Mock(spec=Package)
        package.id = 1
        package.name = 'Test Driver'
        package.intunewin_path = '/test/package.intunewin'
        package.install_command = 'installer.exe /S'
        package.detection_rules = []

        # Mock Path to simulate package file exists
        with patch('autopackager.agents.testing.vm_providers.hyperv_provider.Path') as mock_path:
            mock_package_path = MagicMock()
            mock_package_path.exists.return_value = True
            mock_package_path.parent.glob.side_effect = [
                [Mock(name='installer.exe', __str__=lambda s: '/test/installer.exe')],
                []
            ]
            mock_path.return_value = mock_package_path

            # Mock Path() constructor for destination paths
            mock_path.side_effect = lambda x: MagicMock(parent=MagicMock(__str__=lambda s: 'C:\\Temp'))

            # Configure agent for VM testing
            agent = TestingAgent()
            agent.test_config = {
                'vm_testing_enabled': True,
                'vm_provider': 'local',
                'vm_config': {
                    'hyperv': {
                        'vm_name': 'TestVM',
                        'snapshot_name': 'clean_snapshot',
                        'switch_name': 'Default Switch',
                        'boot_timeout_seconds': 1  # Short timeout for testing
                    }
                },
                'timeout_minutes': 30
            }

            # Run VM test
            result = agent.run_vm_test(package)

            # Verify workflow completed
            self.assertIsNotNone(result)
            self.assertIn('test_passed', result)
            self.assertIn('vm_provider', result)

            # Verify PowerShell commands were called
            self.assertGreater(mock_subprocess.call_count, 0)

            # Verify test passed
            self.assertTrue(result.get('test_passed', False))

    @patch('autopackager.agents.testing.testing_agent.HyperVProvider')
    def test_testing_agent_combines_smoke_and_vm_results(self, mock_provider_class):
        """Test that test() method combines smoke test and VM test results"""
        # Mock VM provider
        mock_provider = Mock()
        mock_provider.run_test.return_value = {
            'test_passed': True,
            'vm_provider': 'HyperVProvider',
            'test_duration': 120.0,
            'provision_result': {'success': True, 'vm_id': 'TestVM', 'ip_address': '192.168.1.100'},
            'install_result': {'success': True, 'install_logs': 'Install complete', 'exit_code': 0},
            'validation_result': {'success': True, 'validation_results': {}, 'device_status': 'OK'},
            'cleanup_result': {'success': True}
        }
        mock_provider_class.return_value = mock_provider

        # Create test package with valid paths
        package = Mock(spec=Package)
        package.id = 1
        package.name = 'Test Driver'
        package.intunewin_path = '/test/package.intunewin'
        package.install_command = 'installer.exe /S'
        package.detection_rules = {'type': 'file', 'path': '/test'}
        package.vm_test_results = {}

        # Configure agent
        agent = TestingAgent()
        agent.test_config = {
            'vm_testing_enabled': True,
            'vm_provider': 'local',
            'vm_config': {
                'hyperv': {'vm_name': 'TestVM', 'snapshot_name': 'clean'}
            },
            'timeout_minutes': 30
        }

        # Patch smoke test methods
        with patch.object(agent, '_validate_package') as mock_validate, \
             patch.object(agent, '_validate_install_command') as mock_install, \
             patch.object(agent, '_validate_detection_rules') as mock_detection, \
             patch.object(agent, '_update_package_test_status') as mock_update:

            mock_validate.return_value = {'valid': True}
            mock_install.return_value = {'valid': True}
            mock_detection.return_value = {'valid': True}

            # Run test
            result = agent.test(package)

            # Verify both smoke and VM tests ran
            mock_validate.assert_called_once()
            mock_provider.run_test.assert_called_once()

            # Verify result structure
            self.assertIn('test_passed', result)
            self.assertIn('smoke_tests', result)
            self.assertIn('vm_test_results', result)

            # Verify VM test results are included
            self.assertEqual(result['vm_test_results'], mock_provider.run_test.return_value)

    @patch('autopackager.agents.testing.vm_providers.hyperv_provider.subprocess.run')
    def test_end_to_end_with_validation_failure(self, mock_subprocess):
        """Test complete workflow when validation detects errors"""
        # Mock provision and install to succeed, but validation to find errors
        def mock_subprocess_run(cmd, **kwargs):
            command = cmd[2] if len(cmd) > 2 else ''

            if 'Restore-VMSnapshot' in command or 'Start-VM' in command:
                return Mock(returncode=0, stdout='', stderr='')
            elif 'Heartbeat' in command:
                return Mock(returncode=0, stdout='OkApplicationsHealthy\n', stderr='')
            elif 'Copy-VMFile' in command or 'Invoke-Command' in command:
                return Mock(returncode=0, stdout='Success\n', stderr='')
            elif 'Get-PnpDevice' in command:
                # Mock device error
                return Mock(
                    returncode=0,
                    stdout='[{"FriendlyName":"Bad Device","Status":"Error"}]\n',
                    stderr=''
                )
            elif 'Stop-VM' in command:
                return Mock(returncode=0, stdout='', stderr='')
            else:
                return Mock(returncode=0, stdout='', stderr='')

        mock_subprocess.side_effect = mock_subprocess_run

        # Create test package
        package = Mock(spec=Package)
        package.id = 1
        package.intunewin_path = '/test/package.intunewin'
        package.install_command = 'installer.exe /S'
        package.detection_rules = []

        # Mock Path
        with patch('autopackager.agents.testing.vm_providers.hyperv_provider.Path') as mock_path:
            mock_package_path = MagicMock()
            mock_package_path.exists.return_value = True
            mock_package_path.parent.glob.side_effect = [
                [Mock(name='installer.exe', __str__=lambda s: '/test/installer.exe')],
                []
            ]
            mock_path.return_value = mock_package_path
            mock_path.side_effect = lambda x: MagicMock(parent=MagicMock(__str__=lambda s: 'C:\\Temp'))

            # Configure agent
            agent = TestingAgent()
            agent.test_config = {
                'vm_testing_enabled': True,
                'vm_provider': 'local',
                'vm_config': {
                    'hyperv': {
                        'vm_name': 'TestVM',
                        'snapshot_name': 'clean',
                        'boot_timeout_seconds': 1
                    }
                },
                'timeout_minutes': 30
            }

            # Run VM test
            result = agent.run_vm_test(package)

            # Verify test failed due to validation error
            self.assertFalse(result.get('test_passed', True))
            self.assertIn('validation', result.get('error', '').lower())

            # Verify cleanup still ran (Stop-VM called)
            stop_calls = [call for call in mock_subprocess.call_args_list
                          if len(call[0]) > 0 and len(call[0][0]) > 2 and 'Stop-VM' in call[0][0][2]]
            self.assertGreater(len(stop_calls), 0, "Cleanup (Stop-VM) should have been called")

    @patch('autopackager.agents.testing.vm_providers.hyperv_provider.subprocess.run')
    def test_cleanup_runs_even_on_provision_failure(self, mock_subprocess):
        """Test that cleanup runs even when provision fails"""
        # Mock restore snapshot to fail
        def mock_subprocess_run(cmd, **kwargs):
            command = cmd[2] if len(cmd) > 2 else ''

            if 'Restore-VMSnapshot' in command:
                return Mock(returncode=1, stdout='', stderr='Snapshot not found')
            elif 'Stop-VM' in command:
                return Mock(returncode=0, stdout='', stderr='')
            else:
                return Mock(returncode=0, stdout='', stderr='')

        mock_subprocess.side_effect = mock_subprocess_run

        # Create HyperVProvider
        config = {
            'vm_name': 'TestVM',
            'snapshot_name': 'clean',
            'switch_name': 'Default Switch',
            'boot_timeout_seconds': 1,
            'timeout_minutes': 30
        }
        provider = HyperVProvider(config)

        # Create test package
        package = Mock(spec=Package)
        package.id = 1

        # Run test
        result = provider.run_test(package)

        # Verify test failed
        self.assertFalse(result['test_passed'])

        # Verify cleanup was attempted
        self.assertIn('cleanup_result', result)

    def test_hyperv_provider_integration_with_validators(self):
        """Test that HyperVProvider correctly uses validator classes"""
        from autopackager.agents.testing.vm_validators import (
            DeviceManagerValidator,
            EventLogValidator,
            SystemStabilityValidator
        )

        # Mock command executor
        mock_executor = Mock()
        mock_executor.return_value = {
            'exit_code': 0,
            'stdout': '[]\n',
            'stderr': ''
        }

        # Test validators
        device_validator = DeviceManagerValidator(mock_executor)
        result = device_validator.validate()
        self.assertTrue(result['passed'])

        event_validator = EventLogValidator(mock_executor)
        result = event_validator.validate()
        self.assertTrue(result['passed'])

        stability_validator = SystemStabilityValidator(Mock(side_effect=[
            {'exit_code': 0, 'stdout': 'System\n', 'stderr': ''},
            {'exit_code': 0, 'stdout': '50\n', 'stderr': ''},
            {'exit_code': 0, 'stdout': '0\n', 'stderr': ''}
        ]))
        result = stability_validator.validate()
        self.assertTrue(result['passed'])


if __name__ == '__main__':
    unittest.main()
