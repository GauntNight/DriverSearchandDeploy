"""Base VM Provider Interface"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
from pathlib import Path

from autopackager.models.package import Package
from autopackager.utils.logger import get_logger

logger = get_logger(__name__)


class VMProvider(ABC):
    """Abstract base class for VM providers (Hyper-V, Azure, etc.)"""

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize VM provider with configuration

        Args:
            config: VM provider configuration dictionary
        """
        self.config = config
        self.vm_name = config.get('vm_name')
        self.timeout = config.get('timeout_minutes', 30) * 60  # Convert to seconds
        logger.info(
            "Initialized VM provider",
            provider=self.__class__.__name__,
            vm_name=self.vm_name
        )

    @abstractmethod
    def provision_vm(self) -> Dict[str, Any]:
        """
        Provision a clean VM instance for testing

        This should:
        1. Restore VM from a clean snapshot
        2. Start the VM
        3. Wait for VM to be ready (network, OS boot complete)
        4. Verify VM is accessible

        Returns:
            Dict containing:
                - success: bool
                - vm_id: str (identifier for the VM instance)
                - ip_address: Optional[str] (VM IP if applicable)
                - error: Optional[str] (error message if failed)

        Raises:
            Exception: If VM provisioning fails critically
        """
        pass

    @abstractmethod
    def install_package(self, package: Package) -> Dict[str, Any]:
        """
        Install driver/software package in the VM

        This should:
        1. Copy package files to VM
        2. Execute install command
        3. Monitor installation progress
        4. Capture installation logs

        Args:
            package: Package model instance with installation details

        Returns:
            Dict containing:
                - success: bool
                - install_logs: str (installation output/logs)
                - exit_code: Optional[int] (installation command exit code)
                - error: Optional[str] (error message if failed)

        Raises:
            Exception: If installation fails critically
        """
        pass

    @abstractmethod
    def validate_installation(self, package: Package) -> Dict[str, Any]:
        """
        Validate package installation in the VM

        This should:
        1. Check Device Manager for driver status (if driver)
        2. Verify detection rules pass
        3. Check Windows Event Log for errors
        4. Test system stability (no BSODs, crashes)

        Args:
            package: Package model instance with validation criteria

        Returns:
            Dict containing:
                - success: bool
                - validation_results: Dict[str, bool] (results per check)
                - device_status: Optional[str] (device manager status)
                - event_log_errors: List[str] (any error events found)
                - error: Optional[str] (error message if failed)

        Raises:
            Exception: If validation fails critically
        """
        pass

    @abstractmethod
    def cleanup_vm(self) -> Dict[str, Any]:
        """
        Clean up VM after testing

        This should:
        1. Stop the VM gracefully
        2. Restore to clean snapshot (optional, for next run)
        3. Free any allocated resources

        Returns:
            Dict containing:
                - success: bool
                - error: Optional[str] (error message if failed)

        Raises:
            Exception: If cleanup fails critically
        """
        pass

    def run_test(self, package: Package) -> Dict[str, Any]:
        """
        Run complete test workflow for a package

        This is a template method that orchestrates the full test:
        1. Provision VM
        2. Install package
        3. Validate installation
        4. Cleanup VM

        Args:
            package: Package model instance to test

        Returns:
            Dict containing complete test results:
                - test_passed: bool
                - vm_provider: str
                - test_duration: float (seconds)
                - provision_result: Dict
                - install_result: Dict
                - validation_result: Dict
                - cleanup_result: Dict
                - error: Optional[str]
        """
        import time

        start_time = time.time()
        test_result = {
            'test_passed': False,
            'vm_provider': self.__class__.__name__,
            'test_duration': 0.0,
            'provision_result': {},
            'install_result': {},
            'validation_result': {},
            'cleanup_result': {},
            'error': None
        }

        try:
            # Step 1: Provision VM
            logger.info("Provisioning VM for testing", package_id=package.id)
            provision_result = self.provision_vm()
            test_result['provision_result'] = provision_result

            if not provision_result.get('success'):
                test_result['error'] = f"VM provisioning failed: {provision_result.get('error')}"
                return test_result

            # Step 2: Install package
            logger.info("Installing package in VM", package_id=package.id)
            install_result = self.install_package(package)
            test_result['install_result'] = install_result

            if not install_result.get('success'):
                test_result['error'] = f"Package installation failed: {install_result.get('error')}"
                return test_result

            # Step 3: Validate installation
            logger.info("Validating package installation", package_id=package.id)
            validation_result = self.validate_installation(package)
            test_result['validation_result'] = validation_result

            if not validation_result.get('success'):
                test_result['error'] = f"Package validation failed: {validation_result.get('error')}"
                return test_result

            # All steps succeeded
            test_result['test_passed'] = True
            logger.info("VM test completed successfully", package_id=package.id)

        except Exception as e:
            logger.error("VM test failed with exception", package_id=package.id, error=str(e))
            test_result['error'] = f"Test exception: {str(e)}"

        finally:
            # Step 4: Always cleanup VM
            logger.info("Cleaning up VM", package_id=package.id)
            try:
                cleanup_result = self.cleanup_vm()
                test_result['cleanup_result'] = cleanup_result

                if not cleanup_result.get('success'):
                    logger.warning(
                        "VM cleanup incomplete",
                        package_id=package.id,
                        error=cleanup_result.get('error')
                    )
            except Exception as cleanup_error:
                logger.error(
                    "VM cleanup failed",
                    package_id=package.id,
                    error=str(cleanup_error)
                )
                test_result['cleanup_result'] = {
                    'success': False,
                    'error': str(cleanup_error)
                }

            # Calculate test duration
            test_result['test_duration'] = time.time() - start_time

        return test_result

    def copy_file_to_vm(self, source_path: Path, destination_path: str) -> bool:
        """
        Copy a file from host to VM (helper method for subclasses)

        Args:
            source_path: Path to file on host machine
            destination_path: Destination path in VM

        Returns:
            bool: True if copy succeeded, False otherwise
        """
        raise NotImplementedError("Subclass must implement copy_file_to_vm()")

    def execute_command(self, command: str, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        Execute a command in the VM (helper method for subclasses)

        Args:
            command: Command to execute
            timeout: Optional timeout in seconds

        Returns:
            Dict containing:
                - success: bool
                - stdout: str
                - stderr: str
                - exit_code: int
        """
        raise NotImplementedError("Subclass must implement execute_command()")
