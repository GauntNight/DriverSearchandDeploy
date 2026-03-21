"""Unit tests for TestingAgent VM testing functionality"""

import unittest
from unittest.mock import Mock, patch, MagicMock

from autopackager.agents.testing.testing_agent import TestingAgent
from autopackager.models.package import Package


class TestTestingAgentVMTesting(unittest.TestCase):
    """Test cases for TestingAgent VM testing methods"""

    def setUp(self):
        """Set up test fixtures"""
        self.agent = TestingAgent()
        self.agent.test_config = {
            'vm_testing_enabled': True,
            'vm_provider': 'local',
            'vm_config': {
                'hyperv': {
                    'vm_name': 'TestVM',
                    'snapshot_name': 'clean_snapshot',
                    'switch_name': 'Default Switch',
                    'boot_timeout_seconds': 300
                }
            },
            'timeout_minutes': 30
        }

        self.package = Mock(spec=Package)
        self.package.id = 1
        self.package.name = 'Test Driver Package'
        self.package.intunewin_path = '/test/package.intunewin'
        self.package.install_command = 'installer.exe /S'
        self.package.detection_rules = []

    @patch('autopackager.agents.testing.testing_agent.HyperVProvider')
    def test_run_vm_test_success(self, mock_provider_class):
        """Test successful VM test workflow"""
        # Mock provider instance
        mock_provider = Mock()
        mock_provider.run_test.return_value = {
            'test_passed': True,
            'vm_provider': 'HyperVProvider',
            'test_duration': 120.5,
            'provision_result': {'success': True, 'vm_id': 'TestVM', 'ip_address': '192.168.1.100'},
            'install_result': {'success': True, 'install_logs': 'Install complete', 'exit_code': 0},
            'validation_result': {'success': True, 'validation_results': {}, 'device_status': 'OK'},
            'cleanup_result': {'success': True}
        }
        mock_provider_class.return_value = mock_provider

        result = self.agent.run_vm_test(self.package)

        # Verify provider was instantiated correctly
        mock_provider_class.assert_called_once()
        call_args = mock_provider_class.call_args[0][0]
        self.assertEqual(call_args['vm_name'], 'TestVM')
        self.assertEqual(call_args['snapshot_name'], 'clean_snapshot')

        # Verify run_test was called
        mock_provider.run_test.assert_called_once_with(self.package)

        # Verify result
        self.assertTrue(result['test_passed'])
        self.assertEqual(result['vm_provider'], 'HyperVProvider')
        self.assertIn('provision_result', result)
        self.assertIn('install_result', result)
        self.assertIn('validation_result', result)

    @patch('autopackager.agents.testing.testing_agent.HyperVProvider')
    def test_run_vm_test_handles_provider_errors(self, mock_provider_class):
        """Test error handling when provider initialization fails"""
        mock_provider_class.side_effect = Exception('Provider init failed')

        result = self.agent.run_vm_test(self.package)

        # Verify error handling
        self.assertFalse(result['test_passed'])
        self.assertIn('exception', result.get('error', '').lower())

    @patch('autopackager.agents.testing.testing_agent.HyperVProvider')
    def test_run_vm_test_handles_test_failure(self, mock_provider_class):
        """Test handling of test failures from provider"""
        # Mock provider to return failed test
        mock_provider = Mock()
        mock_provider.run_test.return_value = {
            'test_passed': False,
            'vm_provider': 'HyperVProvider',
            'test_duration': 60.0,
            'provision_result': {'success': True},
            'install_result': {'success': False, 'error': 'Installation failed'},
            'validation_result': {},
            'cleanup_result': {'success': True},
            'error': 'Installation failed'
        }
        mock_provider_class.return_value = mock_provider

        result = self.agent.run_vm_test(self.package)

        # Verify result reflects failure
        self.assertFalse(result['test_passed'])
        self.assertIn('Installation failed', result['error'])

    def test_run_vm_test_rejects_missing_hyperv_config(self):
        """Test that missing Hyper-V config is handled"""
        # Remove hyperv config
        self.agent.test_config['vm_config'] = {}

        result = self.agent.run_vm_test(self.package)

        # Verify error
        self.assertFalse(result['test_passed'])
        self.assertIn('configuration not found', result.get('error', '').lower())

    def test_run_vm_test_rejects_missing_vm_config(self):
        """Test that missing vm_config is handled"""
        # Remove vm_config entirely
        del self.agent.test_config['vm_config']

        result = self.agent.run_vm_test(self.package)

        # Verify error
        self.assertFalse(result['test_passed'])
        self.assertIn('configuration not found', result.get('error', '').lower())

    @patch('autopackager.agents.testing.testing_agent.HyperVProvider')
    def test_run_vm_test_passes_timeout_to_provider(self, mock_provider_class):
        """Test that timeout configuration is passed to provider"""
        mock_provider = Mock()
        mock_provider.run_test.return_value = {
            'test_passed': True,
            'vm_provider': 'HyperVProvider',
            'test_duration': 10.0,
            'provision_result': {'success': True},
            'install_result': {'success': True},
            'validation_result': {'success': True},
            'cleanup_result': {'success': True}
        }
        mock_provider_class.return_value = mock_provider

        # Set custom timeout
        self.agent.test_config['timeout_minutes'] = 45

        result = self.agent.run_vm_test(self.package)

        # Verify timeout was passed
        call_args = mock_provider_class.call_args[0][0]
        self.assertEqual(call_args['timeout_minutes'], 45)

    @patch('autopackager.agents.testing.testing_agent.HyperVProvider')
    def test_run_vm_test_uses_default_timeout(self, mock_provider_class):
        """Test that default timeout is used when not specified"""
        # Remove timeout from config
        del self.agent.test_config['timeout_minutes']

        mock_provider = Mock()
        mock_provider.run_test.return_value = {
            'test_passed': True,
            'vm_provider': 'HyperVProvider',
            'test_duration': 10.0,
            'provision_result': {'success': True},
            'install_result': {'success': True},
            'validation_result': {'success': True},
            'cleanup_result': {'success': True}
        }
        mock_provider_class.return_value = mock_provider

        result = self.agent.run_vm_test(self.package)

        # Verify default timeout was used (30 minutes)
        call_args = mock_provider_class.call_args[0][0]
        self.assertEqual(call_args.get('timeout_minutes', 30), 30)

    def test_run_vm_test_returns_error_structure_on_exception(self):
        """Test that exceptions return proper error structure"""
        # Remove test_config to cause an exception
        self.agent.test_config = None

        result = self.agent.run_vm_test(self.package)

        # Verify error structure
        self.assertIsInstance(result, dict)
        self.assertIn('test_passed', result)
        self.assertFalse(result['test_passed'])
        self.assertIn('error', result)

    @patch('autopackager.agents.testing.testing_agent.HyperVProvider')
    def test_run_vm_test_handles_provider_runtime_error(self, mock_provider_class):
        """Test handling of provider runtime errors during test execution"""
        mock_provider = Mock()
        mock_provider.run_test.side_effect = RuntimeError('VM crashed during test')
        mock_provider_class.return_value = mock_provider

        result = self.agent.run_vm_test(self.package)

        # Verify error handling
        self.assertFalse(result['test_passed'])
        self.assertIn('exception', result.get('error', '').lower())


if __name__ == '__main__':
    unittest.main()
