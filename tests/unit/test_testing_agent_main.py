"""Unit tests for Testing Agent main functionality"""

import unittest
from unittest.mock import Mock, patch, MagicMock
from pathlib import Path

from autopackager.agents.testing.testing_agent import TestingAgent
from autopackager.models.job import Job
from autopackager.models.package import Package


class TestTestingAgentCore(unittest.TestCase):
    """Test cases for Testing Agent core functionality"""

    def setUp(self):
        """Set up test fixtures"""
        # Mock config
        self.mock_config = {
            'testing': {
                'enabled': True,
                'vm_testing_enabled': False,
                'vm_provider': 'local',
                'vm_config': {
                    'hyperv': {
                        'vm_name': 'TestVM',
                        'snapshot_name': 'clean_snapshot'
                    }
                },
                'timeout_minutes': 30
            }
        }

        # Create agent with mocked config
        with patch('autopackager.agents.testing.testing_agent.get_config', return_value=self.mock_config):
            self.agent = TestingAgent()

        # Mock job
        self.job = Mock(spec=Job)
        self.job.id = 1
        self.job.job_metadata = {'package_id': 1}

        # Mock package
        self.package = Mock(spec=Package)
        self.package.id = 1
        self.package.name = 'Test Driver Package'
        self.package.intunewin_path = '/tmp/test_package.intunewin'
        self.package.install_command = 'installer.exe /S'
        self.package.detection_rules = []
        self.package.tested = False
        self.package.test_passed = False
        self.package.test_logs = None

    def test_agent_initialization_with_config(self):
        """Test that agent initializes with config correctly"""
        self.assertIsNotNone(self.agent.config)
        self.assertEqual(self.agent.test_config, self.mock_config['testing'])
        self.assertTrue(self.agent.enabled)

    def test_agent_initialization_without_testing_config(self):
        """Test that agent handles missing testing config"""
        with patch('autopackager.agents.testing.testing_agent.get_config', return_value={}):
            agent = TestingAgent()
            self.assertEqual(agent.test_config, {})
            self.assertTrue(agent.enabled)  # Default to True

    @patch('autopackager.agents.testing.testing_agent.TestingAgent._update_package_test_status')
    @patch('autopackager.agents.testing.testing_agent.TestingAgent._get_package')
    @patch('autopackager.agents.testing.testing_agent.TestingAgent._run_smoke_tests')
    def test_test_runs_smoke_tests_successfully(self, mock_smoke_tests, mock_get_package, mock_update_status):
        """Test that test() runs smoke tests successfully"""
        mock_get_package.return_value = self.package
        mock_smoke_tests.return_value = {
            'test_passed': True,
            'test_results': {},
            'message': 'All smoke tests passed'
        }

        result = self.agent.test(self.job)

        mock_get_package.assert_called_once_with(1)
        mock_smoke_tests.assert_called_once_with(self.package)
        mock_update_status.assert_called_once()

        self.assertTrue(result['test_passed'])
        self.assertIn('smoke_tests', result)
        self.assertIsNone(result['vm_test_results'])

    @patch('autopackager.agents.testing.testing_agent.TestingAgent._update_package_test_status')
    @patch('autopackager.agents.testing.testing_agent.TestingAgent._get_package')
    @patch('autopackager.agents.testing.testing_agent.TestingAgent._run_smoke_tests')
    def test_test_returns_disabled_when_testing_disabled(self, mock_smoke_tests, mock_get_package, mock_update_status):
        """Test that test() returns early when testing is disabled"""
        self.agent.enabled = False

        result = self.agent.test(self.job)

        mock_get_package.assert_not_called()
        mock_smoke_tests.assert_not_called()
        mock_update_status.assert_not_called()

        self.assertTrue(result['test_passed'])
        self.assertEqual(result['note'], 'Testing disabled in configuration')

    def test_test_raises_error_when_package_id_missing(self):
        """Test that test() raises error when package ID is missing"""
        self.job.job_metadata = {}

        with self.assertRaises(ValueError) as context:
            self.agent.test(self.job)

        self.assertIn('No package ID', str(context.exception))

    @patch('autopackager.agents.testing.testing_agent.TestingAgent._get_package')
    def test_test_raises_error_when_package_not_found(self, mock_get_package):
        """Test that test() raises error when package is not found"""
        mock_get_package.return_value = None

        with self.assertRaises(ValueError) as context:
            self.agent.test(self.job)

        self.assertIn('Package 1 not found', str(context.exception))

    @patch('autopackager.agents.testing.testing_agent.TestingAgent._update_package_test_status')
    @patch('autopackager.agents.testing.testing_agent.TestingAgent._get_package')
    @patch('autopackager.agents.testing.testing_agent.TestingAgent._run_smoke_tests')
    @patch('autopackager.agents.testing.testing_agent.TestingAgent.run_vm_test')
    def test_test_runs_vm_tests_when_enabled(self, mock_vm_test, mock_smoke_tests, mock_get_package, mock_update_status):
        """Test that test() runs VM tests when VM testing is enabled"""
        self.agent.test_config['vm_testing_enabled'] = True
        mock_get_package.return_value = self.package
        mock_smoke_tests.return_value = {
            'test_passed': True,
            'test_results': {},
            'message': 'All smoke tests passed'
        }
        mock_vm_test.return_value = {
            'test_passed': True,
            'vm_provider': 'HyperVProvider'
        }

        result = self.agent.test(self.job)

        mock_smoke_tests.assert_called_once_with(self.package)
        mock_vm_test.assert_called_once_with(self.package)

        self.assertTrue(result['test_passed'])
        self.assertIn('smoke_tests', result)
        self.assertIn('vm_test_results', result)
        self.assertTrue(result['vm_test_results']['test_passed'])

    @patch('autopackager.agents.testing.testing_agent.TestingAgent._update_package_test_status')
    @patch('autopackager.agents.testing.testing_agent.TestingAgent._get_package')
    @patch('autopackager.agents.testing.testing_agent.TestingAgent._run_smoke_tests')
    @patch('autopackager.agents.testing.testing_agent.TestingAgent.run_vm_test')
    def test_test_fails_when_smoke_tests_fail_with_vm_enabled(self, mock_vm_test, mock_smoke_tests, mock_get_package, mock_update_status):
        """Test that test() fails when smoke tests fail even with VM tests passing"""
        self.agent.test_config['vm_testing_enabled'] = True
        mock_get_package.return_value = self.package
        mock_smoke_tests.return_value = {
            'test_passed': False,
            'test_results': {},
            'error_message': 'Smoke tests failed'
        }
        mock_vm_test.return_value = {
            'test_passed': True,
            'vm_provider': 'HyperVProvider'
        }

        result = self.agent.test(self.job)

        # Both must pass for overall pass
        self.assertFalse(result['test_passed'])

    @patch('autopackager.agents.testing.testing_agent.TestingAgent._update_package_test_status')
    @patch('autopackager.agents.testing.testing_agent.TestingAgent._get_package')
    @patch('autopackager.agents.testing.testing_agent.TestingAgent._run_smoke_tests')
    @patch('autopackager.agents.testing.testing_agent.TestingAgent.run_vm_test')
    def test_test_fails_when_vm_tests_fail(self, mock_vm_test, mock_smoke_tests, mock_get_package, mock_update_status):
        """Test that test() fails when VM tests fail"""
        self.agent.test_config['vm_testing_enabled'] = True
        mock_get_package.return_value = self.package
        mock_smoke_tests.return_value = {
            'test_passed': True,
            'test_results': {},
            'message': 'All smoke tests passed'
        }
        mock_vm_test.return_value = {
            'test_passed': False,
            'error': 'VM test failed'
        }

        result = self.agent.test(self.job)

        # Both must pass for overall pass
        self.assertFalse(result['test_passed'])


class TestTestingAgentSmokeTests(unittest.TestCase):
    """Test cases for Testing Agent smoke test methods"""

    def setUp(self):
        """Set up test fixtures"""
        mock_config = {
            'testing': {
                'enabled': True,
                'vm_testing_enabled': False
            }
        }

        with patch('autopackager.agents.testing.testing_agent.get_config', return_value=mock_config):
            self.agent = TestingAgent()

        self.package = Mock(spec=Package)
        self.package.id = 1
        self.package.name = 'Test Package'
        self.package.intunewin_path = '/tmp/test.intunewin'
        self.package.install_command = 'installer.exe /S'
        self.package.detection_rules = []

    @patch('autopackager.agents.testing.testing_agent.TestingAgent._validate_detection_rules')
    @patch('autopackager.agents.testing.testing_agent.TestingAgent._validate_commands')
    @patch('autopackager.agents.testing.testing_agent.TestingAgent._validate_package_files')
    def test_run_smoke_tests_passes_all_validations(self, mock_validate_files, mock_validate_commands, mock_validate_rules):
        """Test that _run_smoke_tests passes when all validations pass"""
        mock_validate_files.return_value = True
        mock_validate_commands.return_value = True
        mock_validate_rules.return_value = True

        result = self.agent._run_smoke_tests(self.package)

        mock_validate_files.assert_called_once_with(self.package)
        mock_validate_commands.assert_called_once_with(self.package)
        mock_validate_rules.assert_called_once_with(self.package)

        self.assertTrue(result['test_passed'])
        self.assertIn('test_results', result)
        self.assertEqual(result['message'], 'All smoke tests passed')

    @patch('autopackager.agents.testing.testing_agent.TestingAgent._validate_detection_rules')
    @patch('autopackager.agents.testing.testing_agent.TestingAgent._validate_commands')
    @patch('autopackager.agents.testing.testing_agent.TestingAgent._validate_package_files')
    def test_run_smoke_tests_fails_when_file_validation_fails(self, mock_validate_files, mock_validate_commands, mock_validate_rules):
        """Test that _run_smoke_tests fails when file validation fails"""
        mock_validate_files.return_value = False
        mock_validate_commands.return_value = True
        mock_validate_rules.return_value = True

        result = self.agent._run_smoke_tests(self.package)

        self.assertFalse(result['test_passed'])
        self.assertIn('error_message', result)

    @patch('autopackager.agents.testing.testing_agent.TestingAgent._validate_detection_rules')
    @patch('autopackager.agents.testing.testing_agent.TestingAgent._validate_commands')
    @patch('autopackager.agents.testing.testing_agent.TestingAgent._validate_package_files')
    def test_run_smoke_tests_fails_when_command_validation_fails(self, mock_validate_files, mock_validate_commands, mock_validate_rules):
        """Test that _run_smoke_tests fails when command validation fails"""
        mock_validate_files.return_value = True
        mock_validate_commands.return_value = False
        mock_validate_rules.return_value = True

        result = self.agent._run_smoke_tests(self.package)

        self.assertFalse(result['test_passed'])
        self.assertIn('error_message', result)

    @patch('autopackager.agents.testing.testing_agent.TestingAgent._validate_detection_rules')
    @patch('autopackager.agents.testing.testing_agent.TestingAgent._validate_commands')
    @patch('autopackager.agents.testing.testing_agent.TestingAgent._validate_package_files')
    def test_run_smoke_tests_fails_when_detection_rules_validation_fails(self, mock_validate_files, mock_validate_commands, mock_validate_rules):
        """Test that _run_smoke_tests fails when detection rules validation fails"""
        mock_validate_files.return_value = True
        mock_validate_commands.return_value = True
        mock_validate_rules.return_value = False

        result = self.agent._run_smoke_tests(self.package)

        self.assertFalse(result['test_passed'])
        self.assertIn('error_message', result)

    @patch('pathlib.Path.stat')
    @patch('pathlib.Path.exists')
    def test_validate_package_files_passes_when_file_exists(self, mock_exists, mock_stat):
        """Test that _validate_package_files passes when file exists and is valid"""
        mock_exists.return_value = True
        mock_stat.return_value = Mock(st_size=1024 * 1024)  # 1 MB

        result = self.agent._validate_package_files(self.package)

        self.assertTrue(result)

    @patch('pathlib.Path.exists')
    def test_validate_package_files_fails_when_file_missing(self, mock_exists):
        """Test that _validate_package_files fails when file doesn't exist"""
        mock_exists.return_value = False

        result = self.agent._validate_package_files(self.package)

        self.assertFalse(result)

    @patch('pathlib.Path.stat')
    @patch('pathlib.Path.exists')
    def test_validate_package_files_fails_when_file_empty(self, mock_exists, mock_stat):
        """Test that _validate_package_files fails when file is empty"""
        mock_exists.return_value = True
        mock_stat.return_value = Mock(st_size=0)

        result = self.agent._validate_package_files(self.package)

        self.assertFalse(result)

    def test_validate_commands_passes_when_install_command_exists(self):
        """Test that _validate_commands passes when install command exists"""
        self.package.install_command = 'installer.exe /S'

        result = self.agent._validate_commands(self.package)

        self.assertTrue(result)

    def test_validate_commands_fails_when_install_command_missing(self):
        """Test that _validate_commands fails when install command is missing"""
        self.package.install_command = None

        result = self.agent._validate_commands(self.package)

        self.assertFalse(result)

    def test_validate_commands_fails_when_install_command_empty(self):
        """Test that _validate_commands fails when install command is empty"""
        self.package.install_command = ''

        result = self.agent._validate_commands(self.package)

        self.assertFalse(result)

    def test_validate_commands_fails_when_install_command_whitespace(self):
        """Test that _validate_commands fails when install command is only whitespace"""
        self.package.install_command = '   '

        result = self.agent._validate_commands(self.package)

        self.assertFalse(result)

    def test_validate_detection_rules_passes_when_empty(self):
        """Test that _validate_detection_rules passes when detection rules are empty"""
        self.package.detection_rules = []

        result = self.agent._validate_detection_rules(self.package)

        self.assertTrue(result)  # Not critical for Phase 1

    def test_validate_detection_rules_passes_when_none(self):
        """Test that _validate_detection_rules passes when detection rules are None"""
        self.package.detection_rules = None

        result = self.agent._validate_detection_rules(self.package)

        self.assertTrue(result)  # Not critical for Phase 1

    def test_validate_detection_rules_passes_when_valid_list(self):
        """Test that _validate_detection_rules passes when detection rules are a valid list"""
        self.package.detection_rules = [
            {'type': 'file', 'path': 'C:\\test\\driver.sys'}
        ]

        result = self.agent._validate_detection_rules(self.package)

        self.assertTrue(result)

    def test_validate_detection_rules_fails_when_not_list(self):
        """Test that _validate_detection_rules fails when detection rules are not a list"""
        self.package.detection_rules = "invalid"

        result = self.agent._validate_detection_rules(self.package)

        self.assertFalse(result)


class TestTestingAgentDatabase(unittest.TestCase):
    """Test cases for Testing Agent database operations"""

    def setUp(self):
        """Set up test fixtures"""
        mock_config = {
            'testing': {
                'enabled': True
            }
        }

        with patch('autopackager.agents.testing.testing_agent.get_config', return_value=mock_config):
            self.agent = TestingAgent()

    @patch('autopackager.agents.testing.testing_agent.db_session_scope')
    def test_update_package_test_status_updates_passed_status(self, mock_db_session):
        """Test that _update_package_test_status updates package when test passes"""
        # Mock database session and package
        mock_session = MagicMock()
        mock_package = Mock(spec=Package)
        mock_package.id = 1

        mock_db_session.return_value.__enter__.return_value = mock_session
        mock_session.query.return_value.filter.return_value.first.return_value = mock_package

        test_result = {
            'test_passed': True,
            'smoke_tests': {
                'test_results': {
                    'package_validation': True,
                    'command_validation': True
                }
            },
            'vm_test_results': None
        }

        self.agent._update_package_test_status(1, test_result)

        # Verify package was updated
        self.assertTrue(mock_package.tested)
        self.assertTrue(mock_package.test_passed)
        self.assertIsNotNone(mock_package.test_logs)

    @patch('autopackager.agents.testing.testing_agent.db_session_scope')
    def test_update_package_test_status_updates_failed_status(self, mock_db_session):
        """Test that _update_package_test_status updates package when test fails"""
        mock_session = MagicMock()
        mock_package = Mock(spec=Package)
        mock_package.id = 1

        mock_db_session.return_value.__enter__.return_value = mock_session
        mock_session.query.return_value.filter.return_value.first.return_value = mock_package

        test_result = {
            'test_passed': False,
            'smoke_tests': {
                'test_results': {
                    'package_validation': False
                }
            },
            'vm_test_results': None
        }

        self.agent._update_package_test_status(1, test_result)

        self.assertTrue(mock_package.tested)
        self.assertFalse(mock_package.test_passed)

    @patch('autopackager.agents.testing.testing_agent.db_session_scope')
    def test_update_package_test_status_stores_vm_test_results(self, mock_db_session):
        """Test that _update_package_test_status stores VM test results"""
        mock_session = MagicMock()
        mock_package = Mock(spec=Package)
        mock_package.id = 1

        mock_db_session.return_value.__enter__.return_value = mock_session
        mock_session.query.return_value.filter.return_value.first.return_value = mock_package

        test_result = {
            'test_passed': True,
            'smoke_tests': {
                'test_results': {}
            },
            'vm_test_results': {
                'test_passed': True,
                'vm_provider': 'HyperVProvider'
            }
        }

        self.agent._update_package_test_status(1, test_result)

        # Verify VM results are stored in test_logs
        self.assertIn('vm_test_results', mock_package.test_logs)

    @patch('autopackager.agents.testing.testing_agent.db_session_scope')
    def test_update_package_test_status_handles_missing_package(self, mock_db_session):
        """Test that _update_package_test_status handles missing package gracefully"""
        mock_session = MagicMock()
        mock_db_session.return_value.__enter__.return_value = mock_session
        mock_session.query.return_value.filter.return_value.first.return_value = None

        test_result = {
            'test_passed': True,
            'smoke_tests': {},
            'vm_test_results': None
        }

        # Should not raise an error
        self.agent._update_package_test_status(999, test_result)

    @patch('autopackager.agents.testing.testing_agent.db_session_scope')
    def test_get_package_returns_package(self, mock_db_session):
        """Test that _get_package returns package when found"""
        mock_session = MagicMock()
        mock_package = Mock(spec=Package)
        mock_package.id = 1

        mock_db_session.return_value.__enter__.return_value = mock_session
        mock_session.query.return_value.filter.return_value.first.return_value = mock_package

        result = self.agent._get_package(1)

        self.assertIsNotNone(result)
        self.assertEqual(result.id, 1)
        mock_session.expunge.assert_called_once_with(mock_package)

    @patch('autopackager.agents.testing.testing_agent.db_session_scope')
    def test_get_package_returns_none_when_not_found(self, mock_db_session):
        """Test that _get_package returns None when package not found"""
        mock_session = MagicMock()
        mock_db_session.return_value.__enter__.return_value = mock_session
        mock_session.query.return_value.filter.return_value.first.return_value = None

        result = self.agent._get_package(999)

        self.assertIsNone(result)


if __name__ == '__main__':
    unittest.main()
