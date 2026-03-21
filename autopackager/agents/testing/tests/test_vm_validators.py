"""Unit tests for VM validators"""

import unittest
from unittest.mock import Mock, MagicMock

from autopackager.agents.testing.vm_validators import (
    DeviceManagerValidator,
    EventLogValidator,
    SystemStabilityValidator
)


class TestDeviceManagerValidator(unittest.TestCase):
    """Test cases for DeviceManagerValidator"""

    def setUp(self):
        """Set up test fixtures"""
        self.mock_executor = Mock()

    def test_passes_when_no_errors(self):
        """Test validation passes when no device errors found"""
        self.mock_executor.return_value = {
            'exit_code': 0,
            'stdout': '[]\n',
            'stderr': ''
        }

        validator = DeviceManagerValidator(self.mock_executor)
        result = validator.validate()

        # Verify result
        self.assertTrue(result['passed'])
        self.assertEqual(result['error_devices'], [])
        self.assertIsNone(result['error'])

    def test_passes_when_output_is_null(self):
        """Test validation passes when output is null"""
        self.mock_executor.return_value = {
            'exit_code': 0,
            'stdout': 'null\n',
            'stderr': ''
        }

        validator = DeviceManagerValidator(self.mock_executor)
        result = validator.validate()

        # Verify result
        self.assertTrue(result['passed'])
        self.assertEqual(result['error_devices'], [])

    def test_fails_when_device_errors_found(self):
        """Test validation fails when device errors detected"""
        self.mock_executor.return_value = {
            'exit_code': 0,
            'stdout': '[{"FriendlyName":"Bad Device","Status":"Error","InstanceId":"123"}]\n',
            'stderr': ''
        }

        validator = DeviceManagerValidator(self.mock_executor)
        result = validator.validate()

        # Verify result
        self.assertFalse(result['passed'])
        self.assertEqual(len(result['error_devices']), 1)
        self.assertEqual(result['error_devices'][0]['FriendlyName'], 'Bad Device')
        self.assertIn('Found 1 device(s) with errors', result['error'])

    def test_handles_single_device_not_array(self):
        """Test validation handles single device (not in array)"""
        self.mock_executor.return_value = {
            'exit_code': 0,
            'stdout': '{"FriendlyName":"Bad Device","Status":"Error"}\n',
            'stderr': ''
        }

        validator = DeviceManagerValidator(self.mock_executor)
        result = validator.validate()

        # Verify result
        self.assertFalse(result['passed'])
        self.assertEqual(len(result['error_devices']), 1)

    def test_fails_when_command_fails(self):
        """Test validation fails when command execution fails"""
        self.mock_executor.return_value = {
            'exit_code': 1,
            'stdout': '',
            'stderr': 'Command failed'
        }

        validator = DeviceManagerValidator(self.mock_executor)
        result = validator.validate()

        # Verify result
        self.assertFalse(result['passed'])
        self.assertIn('Device Manager query failed', result['error'])

    def test_handles_invalid_json(self):
        """Test validation handles invalid JSON output"""
        self.mock_executor.return_value = {
            'exit_code': 0,
            'stdout': 'invalid json{',
            'stderr': ''
        }

        validator = DeviceManagerValidator(self.mock_executor)
        result = validator.validate()

        # Verify result
        self.assertFalse(result['passed'])
        self.assertIn('Failed to parse Device Manager output', result['error'])

    def test_handles_executor_exception(self):
        """Test validation handles executor exceptions"""
        self.mock_executor.side_effect = Exception('Executor failed')

        validator = DeviceManagerValidator(self.mock_executor)
        result = validator.validate()

        # Verify result
        self.assertFalse(result['passed'])
        self.assertIn('Device Manager validation exception', result['error'])


class TestEventLogValidator(unittest.TestCase):
    """Test cases for EventLogValidator"""

    def setUp(self):
        """Set up test fixtures"""
        self.mock_executor = Mock()

    def test_passes_when_no_errors(self):
        """Test validation passes when no event log errors"""
        self.mock_executor.return_value = {
            'exit_code': 0,
            'stdout': '[]\n',
            'stderr': ''
        }

        validator = EventLogValidator(self.mock_executor)
        result = validator.validate()

        # Verify result
        self.assertTrue(result['passed'])
        self.assertEqual(result['events'], [])
        self.assertIsNone(result['error'])

    def test_passes_when_no_events_found_message(self):
        """Test validation passes when stderr indicates no events"""
        self.mock_executor.return_value = {
            'exit_code': 1,
            'stdout': '',
            'stderr': 'No events were found that match the specified selection criteria.'
        }

        validator = EventLogValidator(self.mock_executor)
        result = validator.validate()

        # Verify result
        self.assertTrue(result['passed'])
        self.assertEqual(result['events'], [])

    def test_fails_when_errors_found(self):
        """Test validation fails when error events detected"""
        self.mock_executor.return_value = {
            'exit_code': 0,
            'stdout': '[{"TimeCreated":"2026-03-20","LevelDisplayName":"Error","Message":"Driver failed"}]\n',
            'stderr': ''
        }

        validator = EventLogValidator(self.mock_executor)
        result = validator.validate()

        # Verify result
        self.assertFalse(result['passed'])
        self.assertEqual(len(result['events']), 1)
        self.assertIn('Found 1 error/warning event(s)', result['error'])

    def test_handles_single_event_not_array(self):
        """Test validation handles single event (not in array)"""
        self.mock_executor.return_value = {
            'exit_code': 0,
            'stdout': '{"TimeCreated":"2026-03-20","LevelDisplayName":"Warning","Message":"Test"}\n',
            'stderr': ''
        }

        validator = EventLogValidator(self.mock_executor)
        result = validator.validate()

        # Verify result
        self.assertFalse(result['passed'])
        self.assertEqual(len(result['events']), 1)

    def test_uses_custom_lookback_and_max_events(self):
        """Test validation uses custom lookback and max_events parameters"""
        self.mock_executor.return_value = {
            'exit_code': 0,
            'stdout': '[]\n',
            'stderr': ''
        }

        validator = EventLogValidator(self.mock_executor)
        result = validator.validate(lookback_minutes=10, max_events=20)

        # Verify executor was called with correct parameters
        call_args = self.mock_executor.call_args[0][0]
        self.assertIn('AddMinutes(-10)', call_args)
        self.assertIn('MaxEvents 20', call_args)

    def test_handles_invalid_json(self):
        """Test validation handles invalid JSON output"""
        self.mock_executor.return_value = {
            'exit_code': 0,
            'stdout': 'invalid json{',
            'stderr': ''
        }

        validator = EventLogValidator(self.mock_executor)
        result = validator.validate()

        # Verify result
        self.assertFalse(result['passed'])
        self.assertIn('Failed to parse Event Log output', result['error'])

    def test_handles_executor_exception(self):
        """Test validation handles executor exceptions"""
        self.mock_executor.side_effect = Exception('Executor failed')

        validator = EventLogValidator(self.mock_executor)
        result = validator.validate()

        # Verify result
        self.assertFalse(result['passed'])
        self.assertIn('Event Log validation exception', result['error'])


class TestSystemStabilityValidator(unittest.TestCase):
    """Test cases for SystemStabilityValidator"""

    def setUp(self):
        """Set up test fixtures"""
        self.mock_executor = Mock()

    def test_passes_when_system_stable(self):
        """Test validation passes for stable system"""
        # Mock responses for all three checks
        self.mock_executor.side_effect = [
            {'exit_code': 0, 'stdout': 'System\n', 'stderr': ''},  # Get-Process check
            {'exit_code': 0, 'stdout': '50\n', 'stderr': ''},  # Service count
            {'exit_code': 0, 'stdout': '0\n', 'stderr': ''}  # Minidump check
        ]

        validator = SystemStabilityValidator(self.mock_executor)
        result = validator.validate()

        # Verify result
        self.assertTrue(result['passed'])
        self.assertIn('50 services running', result['details'])
        self.assertIsNone(result['error'])

    def test_fails_when_system_not_responsive(self):
        """Test validation fails when system doesn't respond"""
        self.mock_executor.return_value = {
            'exit_code': 1,
            'stdout': '',
            'stderr': 'System not responding'
        }

        validator = SystemStabilityValidator(self.mock_executor)
        result = validator.validate()

        # Verify result
        self.assertFalse(result['passed'])
        self.assertIn('System appears unresponsive or crashed', result['error'])

    def test_fails_when_crash_dumps_found(self):
        """Test validation fails when recent crash dumps found"""
        # Mock responses
        self.mock_executor.side_effect = [
            {'exit_code': 0, 'stdout': 'System\n', 'stderr': ''},  # Get-Process check
            {'exit_code': 0, 'stdout': '50\n', 'stderr': ''},  # Service count
            {'exit_code': 0, 'stdout': '2\n', 'stderr': ''}  # Minidump check (2 crashes)
        ]

        validator = SystemStabilityValidator(self.mock_executor)
        result = validator.validate()

        # Verify result
        self.assertFalse(result['passed'])
        self.assertIn('Found 2 recent crash dump(s)', result['details'])
        self.assertIn('System crashed 2 time(s) recently', result['error'])

    def test_fails_when_low_service_count(self):
        """Test validation fails when service count is suspiciously low"""
        # Mock responses
        self.mock_executor.side_effect = [
            {'exit_code': 0, 'stdout': 'System\n', 'stderr': ''},  # Get-Process check
            {'exit_code': 0, 'stdout': '5\n', 'stderr': ''},  # Service count (too low)
            {'exit_code': 0, 'stdout': '0\n', 'stderr': ''}  # Minidump check
        ]

        validator = SystemStabilityValidator(self.mock_executor)
        result = validator.validate()

        # Verify result
        self.assertFalse(result['passed'])
        self.assertIn('Only 5 services running', result['details'])
        self.assertIn('System may be in degraded state', result['error'])

    def test_handles_service_count_parse_error(self):
        """Test validation handles service count parse errors"""
        # Mock responses
        self.mock_executor.side_effect = [
            {'exit_code': 0, 'stdout': 'System\n', 'stderr': ''},  # Get-Process check
            {'exit_code': 0, 'stdout': 'invalid\n', 'stderr': ''},  # Service count (invalid)
            {'exit_code': 0, 'stdout': '0\n', 'stderr': ''}  # Minidump check
        ]

        validator = SystemStabilityValidator(self.mock_executor)
        result = validator.validate()

        # Verify result - should fail due to 0 services
        self.assertFalse(result['passed'])

    def test_handles_crash_count_parse_error(self):
        """Test validation handles crash count parse errors"""
        # Mock responses
        self.mock_executor.side_effect = [
            {'exit_code': 0, 'stdout': 'System\n', 'stderr': ''},  # Get-Process check
            {'exit_code': 0, 'stdout': '50\n', 'stderr': ''},  # Service count
            {'exit_code': 0, 'stdout': 'invalid\n', 'stderr': ''}  # Minidump check (invalid)
        ]

        validator = SystemStabilityValidator(self.mock_executor)
        result = validator.validate()

        # Verify result - should pass since crashes defaults to 0
        self.assertTrue(result['passed'])

    def test_handles_executor_exception(self):
        """Test validation handles executor exceptions"""
        self.mock_executor.side_effect = Exception('Executor failed')

        validator = SystemStabilityValidator(self.mock_executor)
        result = validator.validate()

        # Verify result
        self.assertFalse(result['passed'])
        self.assertIn('System stability validation exception', result['error'])


if __name__ == '__main__':
    unittest.main()
