"""VM Validation Utilities - Validators for post-installation checks"""

import json
from typing import Dict, Any, List, Callable, Optional

from autopackager.utils.logger import get_logger

logger = get_logger(__name__)


class DeviceManagerValidator:
    """Validator for checking Device Manager status in VM"""

    def __init__(self, command_executor: Callable[[str, Optional[int]], Dict[str, Any]]):
        """
        Initialize Device Manager validator

        Args:
            command_executor: Callable that executes PowerShell commands in VM
                             Should return dict with: success, stdout, stderr, exit_code
        """
        self.execute_command = command_executor

    def validate(self) -> Dict[str, Any]:
        """
        Check Device Manager for devices with error status

        Returns:
            Dict containing:
                - passed: bool (True if no error devices found)
                - error_devices: List[Dict] (devices with errors, if any)
                - error: Optional[str] (error message if check failed)
        """
        logger.debug("Running Device Manager validation")

        try:
            # Query for devices that are not in "OK" status
            check_cmd = (
                'Get-PnpDevice | '
                'Where-Object {$_.Status -ne "OK"} | '
                'Select-Object FriendlyName, Status, InstanceId | '
                'ConvertTo-Json'
            )

            result = self.execute_command(check_cmd, timeout=30)

            if result.get('exit_code') != 0:
                logger.warning(
                    "Device Manager check command failed",
                    stderr=result.get('stderr')
                )
                return {
                    'passed': False,
                    'error_devices': [],
                    'error': f"Device Manager query failed: {result.get('stderr')}"
                }

            # Parse output
            output = result.get('stdout', '').strip()

            if not output or output == '[]' or output == 'null':
                # No error devices found
                logger.debug("No error devices found in Device Manager")
                return {
                    'passed': True,
                    'error_devices': [],
                    'error': None
                }

            # Parse JSON output
            try:
                error_devices = json.loads(output)

                # Handle single device (not array)
                if isinstance(error_devices, dict):
                    error_devices = [error_devices]

                logger.warning(
                    "Error devices found in Device Manager",
                    count=len(error_devices)
                )

                return {
                    'passed': False,
                    'error_devices': error_devices,
                    'error': f"Found {len(error_devices)} device(s) with errors"
                }

            except json.JSONDecodeError as e:
                logger.error("Failed to parse Device Manager output", error=str(e))
                return {
                    'passed': False,
                    'error_devices': [],
                    'error': f"Failed to parse Device Manager output: {str(e)}"
                }

        except Exception as e:
            logger.error("Device Manager validation failed", error=str(e))
            return {
                'passed': False,
                'error_devices': [],
                'error': f"Device Manager validation exception: {str(e)}"
            }


class EventLogValidator:
    """Validator for checking Windows Event Log for driver-related errors"""

    def __init__(self, command_executor: Callable[[str, Optional[int]], Dict[str, Any]]):
        """
        Initialize Event Log validator

        Args:
            command_executor: Callable that executes PowerShell commands in VM
                             Should return dict with: success, stdout, stderr, exit_code
        """
        self.execute_command = command_executor

    def validate(self, lookback_minutes: int = 5, max_events: int = 10) -> Dict[str, Any]:
        """
        Check Windows Event Log for recent error/warning events

        Args:
            lookback_minutes: How many minutes back to search for events
            max_events: Maximum number of events to retrieve

        Returns:
            Dict containing:
                - passed: bool (True if no errors/warnings found)
                - events: List[Dict] (error/warning events found)
                - error: Optional[str] (error message if check failed)
        """
        logger.debug(
            "Running Event Log validation",
            lookback_minutes=lookback_minutes,
            max_events=max_events
        )

        try:
            # Query System event log for errors and warnings
            check_cmd = (
                f'Get-WinEvent -FilterHashtable @{{'
                f'LogName="System"; '
                f'Level=2,3; '  # 2=Error, 3=Warning
                f'StartTime=(Get-Date).AddMinutes(-{lookback_minutes})'
                f'}} '
                f'-MaxEvents {max_events} '
                f'-ErrorAction SilentlyContinue | '
                f'Select-Object TimeCreated, LevelDisplayName, ProviderName, Message | '
                f'ConvertTo-Json'
            )

            result = self.execute_command(check_cmd, timeout=30)

            if result.get('exit_code') != 0:
                # Command might fail if no events found - this is OK
                stderr = result.get('stderr', '')
                if 'No events were found' in stderr or not stderr:
                    logger.debug("No error/warning events found in Event Log")
                    return {
                        'passed': True,
                        'events': [],
                        'error': None
                    }
                else:
                    logger.warning("Event Log check command failed", stderr=stderr)
                    return {
                        'passed': False,
                        'events': [],
                        'error': f"Event Log query failed: {stderr}"
                    }

            # Parse output
            output = result.get('stdout', '').strip()

            if not output or output == '[]' or output == 'null':
                # No events found
                logger.debug("No error/warning events found in Event Log")
                return {
                    'passed': True,
                    'events': [],
                    'error': None
                }

            # Parse JSON output
            try:
                events = json.loads(output)

                # Handle single event (not array)
                if isinstance(events, dict):
                    events = [events]

                logger.warning(
                    "Error/warning events found in Event Log",
                    count=len(events)
                )

                return {
                    'passed': False,
                    'events': events,
                    'error': f"Found {len(events)} error/warning event(s) in recent logs"
                }

            except json.JSONDecodeError as e:
                logger.error("Failed to parse Event Log output", error=str(e))
                return {
                    'passed': False,
                    'events': [],
                    'error': f"Failed to parse Event Log output: {str(e)}"
                }

        except Exception as e:
            logger.error("Event Log validation failed", error=str(e))
            return {
                'passed': False,
                'events': [],
                'error': f"Event Log validation exception: {str(e)}"
            }


class SystemStabilityValidator:
    """Validator for checking system stability (responsiveness, no crashes)"""

    def __init__(self, command_executor: Callable[[str, Optional[int]], Dict[str, Any]]):
        """
        Initialize System Stability validator

        Args:
            command_executor: Callable that executes PowerShell commands in VM
                             Should return dict with: success, stdout, stderr, exit_code
        """
        self.execute_command = command_executor

    def validate(self) -> Dict[str, Any]:
        """
        Check system stability by verifying VM is responsive

        This is a basic check that ensures the system hasn't crashed or become
        unresponsive after driver installation. In a full implementation, this
        could monitor for BSODs, check crash dumps, verify critical services, etc.

        Returns:
            Dict containing:
                - passed: bool (True if system is stable and responsive)
                - details: str (details about the check)
                - error: Optional[str] (error message if check failed)
        """
        logger.debug("Running System Stability validation")

        try:
            # Check 1: Verify basic PowerShell execution works
            basic_cmd = 'Get-Process | Select-Object -First 1'
            basic_result = self.execute_command(basic_cmd, timeout=10)

            if basic_result.get('exit_code') != 0:
                logger.error("System stability check failed - system not responsive")
                return {
                    'passed': False,
                    'details': 'System failed to respond to basic commands',
                    'error': 'System appears unresponsive or crashed'
                }

            # Check 2: Verify critical services are running
            services_cmd = (
                'Get-Service | '
                'Where-Object {$_.Status -eq "Running"} | '
                'Measure-Object | '
                'Select-Object -ExpandProperty Count'
            )
            services_result = self.execute_command(services_cmd, timeout=10)

            if services_result.get('exit_code') != 0:
                logger.warning("Could not check service status")
                running_services = 0
            else:
                try:
                    running_services = int(services_result.get('stdout', '0').strip())
                except ValueError:
                    running_services = 0

            # Check 3: Check for recent crash dumps (bugcheck)
            crash_cmd = (
                'Get-ChildItem C:\\Windows\\Minidump -ErrorAction SilentlyContinue | '
                'Where-Object {$_.LastWriteTime -gt (Get-Date).AddMinutes(-10)} | '
                'Measure-Object | '
                'Select-Object -ExpandProperty Count'
            )
            crash_result = self.execute_command(crash_cmd, timeout=10)

            recent_crashes = 0
            if crash_result.get('exit_code') == 0:
                try:
                    recent_crashes = int(crash_result.get('stdout', '0').strip())
                except ValueError:
                    recent_crashes = 0

            # Evaluate results
            if recent_crashes > 0:
                logger.error(
                    "System stability check failed - recent crash dumps found",
                    crash_count=recent_crashes
                )
                return {
                    'passed': False,
                    'details': f'Found {recent_crashes} recent crash dump(s)',
                    'error': f'System crashed {recent_crashes} time(s) recently'
                }

            if running_services < 10:
                logger.warning(
                    "System stability check uncertain - low service count",
                    service_count=running_services
                )
                return {
                    'passed': False,
                    'details': f'Only {running_services} services running (expected more)',
                    'error': 'System may be in degraded state'
                }

            # System appears stable
            logger.debug(
                "System stability check passed",
                running_services=running_services,
                recent_crashes=recent_crashes
            )
            return {
                'passed': True,
                'details': f'System responsive with {running_services} services running',
                'error': None
            }

        except Exception as e:
            logger.error("System stability validation failed", error=str(e))
            return {
                'passed': False,
                'details': 'Exception during stability check',
                'error': f"System stability validation exception: {str(e)}"
            }
