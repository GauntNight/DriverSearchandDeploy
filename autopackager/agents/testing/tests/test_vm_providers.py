"""Unit tests for VMProvider base class"""

import unittest
from unittest.mock import Mock, patch, MagicMock
from pathlib import Path

from autopackager.agents.testing.vm_providers.base import VMProvider
from autopackager.models.package import Package


class ConcreteVMProvider(VMProvider):
    """Concrete implementation of VMProvider for testing"""

    def __init__(self, config):
        super().__init__(config)
        self.provision_called = False
        self.install_called = False
        self.validate_called = False
        self.cleanup_called = False

    def provision_vm(self):
        self.provision_called = True
        return {'success': True, 'vm_id': 'test-vm', 'ip_address': '192.168.1.100', 'error': None}

    def install_package(self, package):
        self.install_called = True
        return {'success': True, 'install_logs': 'Installation successful', 'exit_code': 0, 'error': None}

    def validate_installation(self, package):
        self.validate_called = True
        return {'success': True, 'validation_results': {}, 'device_status': 'OK', 'event_log_errors': [], 'error': None}

    def cleanup_vm(self):
        self.cleanup_called = True
        return {'success': True, 'error': None}


class TestVMProvider(unittest.TestCase):
    """Test cases for VMProvider base class"""

    def setUp(self):
        """Set up test fixtures"""
        self.config = {
            'vm_name': 'TestVM',
            'timeout_minutes': 30
        }
        self.provider = ConcreteVMProvider(self.config)
        self.package = Mock(spec=Package)
        self.package.id = 1
        self.package.name = 'Test Driver Package'

    def test_abstract_methods_must_be_implemented(self):
        """Verify VMProvider cannot be instantiated without implementing abstract methods"""
        with self.assertRaises(TypeError):
            VMProvider({'vm_name': 'test'})

    def test_init_sets_config_attributes(self):
        """Test that __init__ correctly sets configuration attributes"""
        self.assertEqual(self.provider.vm_name, 'TestVM')
        self.assertEqual(self.provider.timeout, 30 * 60)  # Converted to seconds
        self.assertEqual(self.provider.config, self.config)

    def test_run_test_orchestrates_workflow(self):
        """Test that run_test() calls provision, install, validate, cleanup in order"""
        result = self.provider.run_test(self.package)

        # Verify all methods were called
        self.assertTrue(self.provider.provision_called)
        self.assertTrue(self.provider.install_called)
        self.assertTrue(self.provider.validate_called)
        self.assertTrue(self.provider.cleanup_called)

        # Verify result structure
        self.assertIn('test_passed', result)
        self.assertIn('vm_provider', result)
        self.assertIn('test_duration', result)
        self.assertIn('provision_result', result)
        self.assertIn('install_result', result)
        self.assertIn('validation_result', result)
        self.assertIn('cleanup_result', result)

        # Verify test passed
        self.assertTrue(result['test_passed'])
        self.assertIsNone(result['error'])

    def test_run_test_stops_on_provision_failure(self):
        """Test that run_test() stops if provision_vm fails"""
        # Override provision to fail
        self.provider.provision_vm = Mock(return_value={
            'success': False,
            'vm_id': None,
            'ip_address': None,
            'error': 'Provisioning failed'
        })

        result = self.provider.run_test(self.package)

        # Verify provision was called but install was not
        self.provider.provision_vm.assert_called_once()
        self.assertFalse(self.provider.install_called)
        self.assertFalse(self.provider.validate_called)

        # Cleanup should still be called
        self.assertTrue(self.provider.cleanup_called)

        # Verify test failed
        self.assertFalse(result['test_passed'])
        self.assertIn('VM provisioning failed', result['error'])

    def test_run_test_stops_on_install_failure(self):
        """Test that run_test() stops if install_package fails"""
        # Override install to fail
        self.provider.install_package = Mock(return_value={
            'success': False,
            'install_logs': '',
            'exit_code': 1,
            'error': 'Installation failed'
        })

        result = self.provider.run_test(self.package)

        # Verify provision and install were called but validate was not
        self.assertTrue(self.provider.provision_called)
        self.provider.install_package.assert_called_once()
        self.assertFalse(self.provider.validate_called)

        # Cleanup should still be called
        self.assertTrue(self.provider.cleanup_called)

        # Verify test failed
        self.assertFalse(result['test_passed'])
        self.assertIn('Package installation failed', result['error'])

    def test_run_test_stops_on_validation_failure(self):
        """Test that run_test() stops if validate_installation fails"""
        # Override validate to fail
        self.provider.validate_installation = Mock(return_value={
            'success': False,
            'validation_results': {},
            'device_status': 'Error',
            'event_log_errors': ['Error event'],
            'error': 'Validation failed'
        })

        result = self.provider.run_test(self.package)

        # Verify all steps were called except validation failed
        self.assertTrue(self.provider.provision_called)
        self.assertTrue(self.provider.install_called)
        self.provider.validate_installation.assert_called_once()

        # Cleanup should still be called
        self.assertTrue(self.provider.cleanup_called)

        # Verify test failed
        self.assertFalse(result['test_passed'])
        self.assertIn('Package validation failed', result['error'])

    def test_cleanup_runs_in_finally_block(self):
        """Verify cleanup runs even when errors occur"""
        # Make provision raise an exception
        self.provider.provision_vm = Mock(side_effect=Exception('Provision error'))

        result = self.provider.run_test(self.package)

        # Verify cleanup was still called
        self.assertTrue(self.provider.cleanup_called)

        # Verify test failed with exception
        self.assertFalse(result['test_passed'])
        self.assertIn('Test exception', result['error'])

    def test_cleanup_failure_is_logged_but_doesnt_raise(self):
        """Test that cleanup failures are logged but don't prevent result return"""
        # Make cleanup fail
        self.provider.cleanup_vm = Mock(return_value={
            'success': False,
            'error': 'Cleanup failed'
        })

        result = self.provider.run_test(self.package)

        # Test should still complete
        self.assertIsNotNone(result)
        self.assertIn('cleanup_result', result)
        self.assertFalse(result['cleanup_result']['success'])

    def test_cleanup_exception_is_caught(self):
        """Test that cleanup exceptions are caught and recorded"""
        # Make cleanup raise exception
        self.provider.cleanup_vm = Mock(side_effect=Exception('Cleanup exception'))

        result = self.provider.run_test(self.package)

        # Test should still complete
        self.assertIsNotNone(result)
        self.assertIn('cleanup_result', result)
        self.assertFalse(result['cleanup_result']['success'])
        self.assertIn('Cleanup exception', result['cleanup_result']['error'])

    def test_test_duration_is_recorded(self):
        """Test that test duration is recorded"""
        result = self.provider.run_test(self.package)

        self.assertIn('test_duration', result)
        self.assertGreater(result['test_duration'], 0)
        self.assertIsInstance(result['test_duration'], float)

    def test_copy_file_to_vm_not_implemented(self):
        """Test that copy_file_to_vm raises NotImplementedError in base class"""
        with self.assertRaises(NotImplementedError):
            self.provider.copy_file_to_vm(Path('/test/path'), 'C:\\dest')

    def test_execute_command_not_implemented(self):
        """Test that execute_command raises NotImplementedError in base class"""
        with self.assertRaises(NotImplementedError):
            self.provider.execute_command('test command')


if __name__ == '__main__':
    unittest.main()
