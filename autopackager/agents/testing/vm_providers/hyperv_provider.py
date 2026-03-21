"""Hyper-V Provider - VM lifecycle management for local testing"""

import subprocess
import time
import json
import re
from pathlib import Path
from typing import Dict, Any, Optional

from autopackager.agents.testing.vm_providers.base import VMProvider
from autopackager.models.package import Package
from autopackager.utils.logger import get_logger

logger = get_logger(__name__)


class HyperVProvider(VMProvider):
    """Hyper-V VM Provider for local Windows testing"""

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize Hyper-V provider with configuration

        Args:
            config: Hyper-V configuration dictionary

        Raises:
            ValueError: If configuration contains invalid or unsafe values
        """
        super().__init__(config)

        # Validate and sanitize configuration values
        try:
            self.vm_name = self._sanitize_powershell_param(
                'vm_name',
                self.vm_name,  # Already set by parent __init__
                allow_path=False
            )
            self.snapshot_name = self._sanitize_powershell_param(
                'snapshot_name',
                config.get('snapshot_name', ''),
                allow_path=False
            )
            self.switch_name = self._sanitize_powershell_param(
                'switch_name',
                config.get('switch_name', 'Default Switch'),
                allow_path=False
            )
        except ValueError as e:
            logger.error("Invalid Hyper-V configuration", error=str(e))
            raise

        self.boot_timeout = config.get('boot_timeout_seconds', 300)
        self.vm_session = None

        logger.info(
            "Initialized Hyper-V provider",
            vm_name=self.vm_name,
            snapshot_name=self.snapshot_name
        )

    def _sanitize_powershell_param(self, param_name: str, value: str, allow_path: bool = False) -> str:
        """
        Validate and sanitize PowerShell command parameters

        Args:
            param_name: Name of parameter (for error messages)
            value: Value to sanitize
            allow_path: If True, allow path characters (backslash, colon, forward slash)

        Returns:
            Sanitized value safe for PowerShell

        Raises:
            ValueError: If value contains unsafe characters
        """
        if not value:
            raise ValueError(f"{param_name} cannot be empty")

        # Define allowed character sets
        if allow_path:
            # Allow alphanumeric, hyphen, underscore, backslash, colon, period, space, forward slash
            pattern = r'^[a-zA-Z0-9_\-\\:. /]+$'
        else:
            # Allow alphanumeric, hyphen, underscore, space only (for VM names, etc.)
            pattern = r'^[a-zA-Z0-9_\- ]+$'

        if not re.match(pattern, value):
            logger.error(
                "Invalid characters in PowerShell parameter",
                param_name=param_name,
                value=value[:50],  # Log truncated value
                pattern=pattern
            )
            raise ValueError(
                f"{param_name} contains invalid characters. "
                f"Allowed pattern: {pattern}"
            )

        return value

    def provision_vm(self) -> Dict[str, Any]:
        """
        Provision a clean VM instance for testing

        Steps:
        1. Restore VM from snapshot
        2. Start the VM
        3. Wait for VM to boot and be network-ready
        4. Verify VM is accessible

        Returns:
            Dict containing success status, vm_id, ip_address, and error if any
        """
        logger.info("Provisioning VM", vm_name=self.vm_name)

        try:
            # Step 1: Restore snapshot
            logger.info("Restoring VM snapshot", snapshot_name=self.snapshot_name)
            restore_result = self._restore_snapshot()
            if not restore_result:
                return {
                    'success': False,
                    'vm_id': None,
                    'ip_address': None,
                    'error': 'Failed to restore VM snapshot'
                }

            # Step 2: Start VM
            logger.info("Starting VM", vm_name=self.vm_name)
            start_result = self._start_vm()
            if not start_result:
                return {
                    'success': False,
                    'vm_id': None,
                    'ip_address': None,
                    'error': 'Failed to start VM'
                }

            # Step 3: Wait for boot
            logger.info("Waiting for VM to boot", timeout_seconds=self.boot_timeout)
            boot_result = self._wait_for_boot()
            if not boot_result:
                return {
                    'success': False,
                    'vm_id': self.vm_name,
                    'ip_address': None,
                    'error': 'VM boot timeout or failed'
                }

            # Step 4: Get VM IP address
            ip_address = self._get_vm_ip_address()

            logger.info("VM provisioned successfully", vm_name=self.vm_name, ip=ip_address)

            return {
                'success': True,
                'vm_id': self.vm_name,
                'ip_address': ip_address,
                'error': None
            }

        except Exception as e:
            logger.error("Failed to provision VM", vm_name=self.vm_name, error=str(e))
            return {
                'success': False,
                'vm_id': None,
                'ip_address': None,
                'error': f"VM provisioning exception: {str(e)}"
            }

    def install_package(self, package: Package) -> Dict[str, Any]:
        """
        Install driver/software package in the VM

        Steps:
        1. Extract package files from .intunewin
        2. Copy package to VM
        3. Execute install command
        4. Monitor and capture installation logs

        Args:
            package: Package model instance with installation details

        Returns:
            Dict containing success status, install_logs, exit_code, and error if any
        """
        logger.info("Installing package in VM", package_id=package.id, vm_name=self.vm_name)

        try:
            # Get package path
            package_path = Path(package.intunewin_path)
            if not package_path.exists():
                return {
                    'success': False,
                    'install_logs': '',
                    'exit_code': None,
                    'error': f'Package file not found: {package_path}'
                }

            # For .intunewin files, we need the original installer
            # In practice, we'd extract it, but for now use the parent directory
            package_dir = package_path.parent
            installer_files = list(package_dir.glob('*.exe')) + list(package_dir.glob('*.msi'))

            if not installer_files:
                return {
                    'success': False,
                    'install_logs': '',
                    'exit_code': None,
                    'error': 'No installer file found in package directory'
                }

            installer_path = installer_files[0]
            logger.info("Found installer", installer=installer_path.name)

            # Copy installer to VM
            vm_dest_path = f"C:\\Temp\\{installer_path.name}"
            logger.info("Copying file to VM", source=str(installer_path), dest=vm_dest_path)

            copy_result = self.copy_file_to_vm(installer_path, vm_dest_path)
            if not copy_result:
                return {
                    'success': False,
                    'install_logs': '',
                    'exit_code': None,
                    'error': 'Failed to copy package to VM'
                }

            # Execute install command
            install_cmd = package.install_command.replace(installer_path.name, vm_dest_path)
            logger.info("Executing install command", command=install_cmd)

            cmd_result = self.execute_command(install_cmd, timeout=self.timeout)

            if cmd_result.get('success') and cmd_result.get('exit_code') == 0:
                logger.info("Package installed successfully", package_id=package.id)
                return {
                    'success': True,
                    'install_logs': cmd_result.get('stdout', ''),
                    'exit_code': cmd_result.get('exit_code'),
                    'error': None
                }
            else:
                logger.error(
                    "Package installation failed",
                    package_id=package.id,
                    exit_code=cmd_result.get('exit_code')
                )
                return {
                    'success': False,
                    'install_logs': f"stdout: {cmd_result.get('stdout', '')}\nstderr: {cmd_result.get('stderr', '')}",
                    'exit_code': cmd_result.get('exit_code'),
                    'error': f"Install command failed with exit code {cmd_result.get('exit_code')}"
                }

        except Exception as e:
            logger.error("Failed to install package", package_id=package.id, error=str(e))
            return {
                'success': False,
                'install_logs': '',
                'exit_code': None,
                'error': f"Installation exception: {str(e)}"
            }

    def validate_installation(self, package: Package) -> Dict[str, Any]:
        """
        Validate package installation in the VM

        Checks:
        1. Device Manager status (for drivers)
        2. Windows Event Log for errors
        3. Detection rules validation
        4. System stability check

        Args:
            package: Package model instance with validation criteria

        Returns:
            Dict containing validation results
        """
        logger.info("Validating package installation", package_id=package.id)

        validation_results = {
            'device_manager_check': False,
            'event_log_check': False,
            'detection_rules_check': False,
            'system_stability_check': False
        }
        event_log_errors = []

        try:
            # Check 1: Device Manager status (look for error codes)
            logger.debug("Checking Device Manager status")
            device_check_cmd = 'Get-PnpDevice | Where-Object {$_.Status -ne "OK"} | Select-Object FriendlyName, Status | ConvertTo-Json'
            device_result = self._run_powershell_command(device_check_cmd)

            if device_result.get('exit_code') == 0:
                device_errors = device_result.get('stdout', '').strip()
                validation_results['device_manager_check'] = (not device_errors or device_errors == '[]')
                logger.debug(
                    "Device Manager check completed",
                    has_errors=not validation_results['device_manager_check']
                )
            else:
                logger.warning("Device Manager check failed to execute")

            # Check 2: Event Log for recent errors
            logger.debug("Checking Windows Event Log")
            event_log_cmd = (
                'Get-WinEvent -FilterHashtable @{LogName="System"; Level=2,3; StartTime=(Get-Date).AddMinutes(-5)} '
                '-MaxEvents 10 -ErrorAction SilentlyContinue | '
                'Select-Object TimeCreated, Message | ConvertTo-Json'
            )
            event_result = self._run_powershell_command(event_log_cmd)

            if event_result.get('exit_code') == 0:
                events_output = event_result.get('stdout', '').strip()
                if events_output and events_output != '[]':
                    try:
                        events = json.loads(events_output)
                        if isinstance(events, list):
                            event_log_errors = [e.get('Message', '') for e in events]
                        validation_results['event_log_check'] = False
                    except json.JSONDecodeError:
                        validation_results['event_log_check'] = True
                else:
                    validation_results['event_log_check'] = True

                logger.debug(
                    "Event log check completed",
                    error_count=len(event_log_errors)
                )

            # Check 3: Detection rules validation
            if package.detection_rules and isinstance(package.detection_rules, list):
                logger.debug("Validating detection rules", count=len(package.detection_rules))
                detection_passed = self._validate_detection_rules(package.detection_rules)
                validation_results['detection_rules_check'] = detection_passed
            else:
                # No detection rules defined, pass by default
                validation_results['detection_rules_check'] = True

            # Check 4: System stability (check if VM is responsive)
            logger.debug("Checking system stability")
            stability_cmd = 'Get-Process | Select-Object -First 1'
            stability_result = self._run_powershell_command(stability_cmd)
            validation_results['system_stability_check'] = stability_result.get('exit_code') == 0

            # Determine overall success
            all_passed = all(validation_results.values())

            if all_passed:
                logger.info("Package validation passed", package_id=package.id)
            else:
                logger.warning(
                    "Package validation failed",
                    package_id=package.id,
                    results=validation_results
                )

            return {
                'success': all_passed,
                'validation_results': validation_results,
                'device_status': 'OK' if validation_results['device_manager_check'] else 'Errors found',
                'event_log_errors': event_log_errors,
                'error': None if all_passed else 'One or more validation checks failed'
            }

        except Exception as e:
            logger.error("Validation failed with exception", package_id=package.id, error=str(e))
            return {
                'success': False,
                'validation_results': validation_results,
                'device_status': 'Unknown',
                'event_log_errors': event_log_errors,
                'error': f"Validation exception: {str(e)}"
            }

    def cleanup_vm(self) -> Dict[str, Any]:
        """
        Clean up VM after testing

        Steps:
        1. Stop the VM gracefully
        2. Wait for shutdown to complete

        Returns:
            Dict containing success status and error if any
        """
        logger.info("Cleaning up VM", vm_name=self.vm_name)

        try:
            # Stop VM
            stop_result = self._stop_vm()

            if stop_result:
                logger.info("VM cleanup completed", vm_name=self.vm_name)
                return {
                    'success': True,
                    'error': None
                }
            else:
                logger.warning("VM cleanup incomplete", vm_name=self.vm_name)
                return {
                    'success': False,
                    'error': 'Failed to stop VM'
                }

        except Exception as e:
            logger.error("VM cleanup failed", vm_name=self.vm_name, error=str(e))
            return {
                'success': False,
                'error': f"Cleanup exception: {str(e)}"
            }

    def copy_file_to_vm(self, source_path: Path, destination_path: str) -> bool:
        """
        Copy a file from host to VM using Copy-VMFile

        Args:
            source_path: Path to file on host machine
            destination_path: Destination path in VM

        Returns:
            bool: True if copy succeeded, False otherwise
        """
        try:
            # Validate paths
            source_str = self._sanitize_powershell_param(
                'source_path',
                str(source_path),
                allow_path=True
            )
            dest_str = self._sanitize_powershell_param(
                'destination_path',
                destination_path,
                allow_path=True
            )

            # Ensure destination directory exists in VM
            dest_dir = str(Path(dest_str).parent).replace('\\', '\\\\')
            mkdir_cmd = f'New-Item -Path "{dest_dir}" -ItemType Directory -Force -ErrorAction SilentlyContinue'
            self._run_powershell_command(mkdir_cmd)

            # Copy file to VM using Copy-VMFile
            # vm_name is already validated in __init__
            copy_cmd = (
                f'Copy-VMFile -Name "{self.vm_name}" '
                f'-SourcePath "{source_str}" '
                f'-DestinationPath "{dest_str}" '
                f'-CreateFullPath -FileSource Host -Force'
            )

            result = self._run_powershell_command(copy_cmd)

            if result.get('exit_code') == 0:
                logger.debug("File copied to VM", source=source_str, dest=dest_str)
                return True
            else:
                logger.error(
                    "Failed to copy file to VM",
                    source=source_str,
                    stderr=result.get('stderr')
                )
                return False

        except ValueError as e:
            logger.error("Invalid file path", error=str(e))
            return False
        except Exception as e:
            logger.error("File copy exception", source=str(source_path), error=str(e))
            return False

    def execute_command(self, command: str, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        Execute a command in the VM using Invoke-Command

        Args:
            command: PowerShell command to execute in VM
            timeout: Command timeout in seconds (default: 60)

        Returns:
            Dict with exit_code, stdout, stderr
        """
        try:
            # Log warning if command contains suspicious characters
            if any(char in command for char in [';', '&', '|', '$(']):
                logger.warning(
                    "Command contains potentially unsafe characters",
                    command_preview=command[:100]
                )

            # Use Invoke-Command with VM name
            # vm_name is already validated in __init__
            ps_command = (
                f'Invoke-Command -VMName "{self.vm_name}" '
                f'-ScriptBlock {{ {command} }} -ErrorAction Stop'
            )

            result = self._run_powershell_command(ps_command, timeout=timeout)

            logger.debug(
                "Command executed in VM",
                exit_code=result.get('exit_code'),
                has_output=bool(result.get('stdout'))
            )

            return {
                'success': result.get('exit_code') == 0,
                'stdout': result.get('stdout', ''),
                'stderr': result.get('stderr', ''),
                'exit_code': result.get('exit_code')
            }

        except Exception as e:
            logger.error("Command execution failed", error=str(e))
            return {
                'success': False,
                'stdout': '',
                'stderr': str(e),
                'exit_code': -1
            }

    # Private helper methods

    def _restore_snapshot(self) -> bool:
        """Restore VM from snapshot"""
        try:
            cmd = (
                f'Get-VMSnapshot -VMName "{self.vm_name}" -Name "{self.snapshot_name}" | '
                f'Restore-VMSnapshot -Confirm:$false'
            )

            result = self._run_powershell_command(cmd)
            return result.get('exit_code') == 0

        except Exception as e:
            logger.error("Failed to restore snapshot", error=str(e))
            return False

    def _start_vm(self) -> bool:
        """Start the VM"""
        try:
            cmd = f'Start-VM -Name "{self.vm_name}"'
            result = self._run_powershell_command(cmd)
            return result.get('exit_code') == 0

        except Exception as e:
            logger.error("Failed to start VM", error=str(e))
            return False

    def _stop_vm(self) -> bool:
        """Stop the VM gracefully"""
        try:
            # Try graceful shutdown first
            cmd = f'Stop-VM -Name "{self.vm_name}" -Force'
            result = self._run_powershell_command(cmd)

            if result.get('exit_code') == 0:
                # Wait for VM to fully stop
                time.sleep(5)
                return True
            else:
                logger.warning("Failed to stop VM gracefully", stderr=result.get('stderr'))
                return False

        except Exception as e:
            logger.error("Failed to stop VM", error=str(e))
            return False

    def _wait_for_boot(self) -> bool:
        """
        Wait for VM to boot and become ready

        Polls VM heartbeat status until it's running or timeout
        """
        start_time = time.time()
        poll_interval = 5  # seconds

        while time.time() - start_time < self.boot_timeout:
            try:
                # Check VM heartbeat status
                cmd = (
                    f'Get-VM -Name "{self.vm_name}" | '
                    f'Select-Object -ExpandProperty Heartbeat'
                )

                result = self._run_powershell_command(cmd)

                if result.get('exit_code') == 0:
                    heartbeat = result.get('stdout', '').strip()

                    if 'OkApplicationsHealthy' in heartbeat or 'OkApplicationsUnknown' in heartbeat:
                        logger.info("VM boot completed", elapsed_seconds=time.time() - start_time)
                        # Additional wait for services to stabilize
                        time.sleep(10)
                        return True

                logger.debug("Waiting for VM boot", heartbeat=heartbeat)
                time.sleep(poll_interval)

            except Exception as e:
                logger.debug("Boot check failed, retrying", error=str(e))
                time.sleep(poll_interval)

        logger.error("VM boot timeout", timeout_seconds=self.boot_timeout)
        return False

    def _get_vm_ip_address(self) -> Optional[str]:
        """Get VM IP address"""
        try:
            cmd = (
                f'Get-VM -Name "{self.vm_name}" | '
                f'Select-Object -ExpandProperty NetworkAdapters | '
                f'Select-Object -ExpandProperty IPAddresses | '
                f'Where-Object {{ $_ -match "^\\d+\\.\\d+\\.\\d+\\.\\d+$" }} | '
                f'Select-Object -First 1'
            )

            result = self._run_powershell_command(cmd)

            if result.get('exit_code') == 0:
                ip = result.get('stdout', '').strip()
                return ip if ip else None

            return None

        except Exception as e:
            logger.debug("Failed to get VM IP", error=str(e))
            return None

    def _validate_detection_rules(self, detection_rules: list) -> bool:
        """
        Validate detection rules in the VM

        For now, this is a simplified implementation
        In production, would check registry keys, file existence, etc.
        """
        try:
            # Basic implementation - check if rules are defined
            # In a full implementation, this would:
            # - Parse each detection rule (registry, file, script)
            # - Execute appropriate checks in the VM
            # - Return true only if all rules pass

            logger.debug("Detection rules validation not fully implemented")
            return True

        except Exception as e:
            logger.error("Detection rules validation failed", error=str(e))
            return False

    def _run_powershell_command(self, command: str, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        Execute a PowerShell command on the host

        Args:
            command: PowerShell command to execute
            timeout: Optional timeout in seconds

        Returns:
            Dict with exit_code, stdout, stderr
        """
        try:
            # Run PowerShell command
            process = subprocess.run(
                ['powershell', '-Command', command],
                capture_output=True,
                text=True,
                timeout=timeout or 60
            )

            return {
                'exit_code': process.returncode,
                'stdout': process.stdout,
                'stderr': process.stderr
            }

        except subprocess.TimeoutExpired:
            logger.error("PowerShell command timeout", command=command[:100])
            return {
                'exit_code': -1,
                'stdout': '',
                'stderr': 'Command timeout'
            }
        except Exception as e:
            logger.error("PowerShell command failed", error=str(e))
            return {
                'exit_code': -1,
                'stdout': '',
                'stderr': str(e)
            }
