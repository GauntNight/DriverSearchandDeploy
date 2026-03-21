"""Testing Agent - Validate Package Installation"""

from typing import Dict, Any
from pathlib import Path

from autopackager.models.job import Job
from autopackager.models.package import Package
from autopackager.utils.config import get_config
from autopackager.utils.database import db_session_scope
from autopackager.utils.logger import get_logger

logger = get_logger(__name__)


class TestingAgent:
    """Agent responsible for testing package installations"""

    def __init__(self):
        self.config = get_config()
        self.test_config = self.config.get('testing', {})
        self.enabled = self.test_config.get('enabled', True)

    def test(self, job: Job) -> Dict[str, Any]:
        """
        Main testing method - validates package installation
        """
        logger.info("Starting testing", job_id=job.id)

        if not self.enabled:
            logger.warning("Testing disabled in configuration")
            return {
                'test_passed': True,
                'note': 'Testing disabled in configuration'
            }

        # Get package from job metadata
        package_id = job.job_metadata.get('package_id')
        if not package_id:
            raise ValueError("No package ID in job metadata")

        package = self._get_package(package_id)
        if not package:
            raise ValueError(f"Package {package_id} not found")

        # Run smoke tests
        test_result = self._run_smoke_tests(package)

        # Update package test status
        self._update_package_test_status(package_id, test_result)

        return test_result

    def _run_smoke_tests(self, package: Package) -> Dict[str, Any]:
        """
        Run basic smoke tests on the package
        For Phase 1, this is a simplified implementation
        """
        logger.info("Running smoke tests", package_id=package.id)

        # Phase 1: Basic validation checks
        # In a full implementation, this would:
        # 1. Provision a clean VM
        # 2. Deploy the package
        # 3. Verify installation
        # 4. Test basic functionality
        # 5. Verify uninstallation
        # 6. Restore VM snapshot

        test_results = {
            'package_validation': self._validate_package_files(package),
            'command_validation': self._validate_commands(package),
            'detection_rules_validation': self._validate_detection_rules(package)
        }

        # Determine overall pass/fail
        all_passed = all(test_results.values())

        if all_passed:
            logger.info("All smoke tests passed", package_id=package.id)
            return {
                'test_passed': True,
                'test_results': test_results,
                'message': 'All smoke tests passed'
            }
        else:
            logger.error("Smoke tests failed", package_id=package.id, results=test_results)
            return {
                'test_passed': False,
                'test_results': test_results,
                'error_message': 'One or more smoke tests failed'
            }

    def _validate_package_files(self, package: Package) -> bool:
        """Validate that package files exist and are valid"""
        logger.debug("Validating package files", package_id=package.id)

        # Check if .intunewin file exists
        intunewin_path = Path(package.intunewin_path)
        if not intunewin_path.exists():
            logger.error("IntuneWin file not found", path=str(intunewin_path))
            return False

        # Check file size
        file_size = intunewin_path.stat().st_size
        if file_size == 0:
            logger.error("IntuneWin file is empty")
            return False

        logger.debug("Package files validated", size_mb=file_size / (1024 * 1024))
        return True

    def _validate_commands(self, package: Package) -> bool:
        """Validate installation commands"""
        logger.debug("Validating commands", package_id=package.id)

        # Check if install command exists
        if not package.install_command:
            logger.error("No install command defined")
            return False

        # Basic validation - command should not be empty or just whitespace
        if not package.install_command.strip():
            logger.error("Install command is empty")
            return False

        logger.debug("Commands validated", install_cmd=package.install_command)
        return True

    def _validate_detection_rules(self, package: Package) -> bool:
        """Validate detection rules"""
        logger.debug("Validating detection rules", package_id=package.id)

        # Check if detection rules exist
        if not package.detection_rules:
            logger.warning("No detection rules defined")
            return True  # Not critical for Phase 1

        # Basic validation - should be a list
        if not isinstance(package.detection_rules, list):
            logger.error("Detection rules not in list format")
            return False

        logger.debug("Detection rules validated", count=len(package.detection_rules))
        return True

    def _update_package_test_status(self, package_id: int, test_result: Dict[str, Any]):
        """Update package test status in database"""
        with db_session_scope() as session:
            package = session.query(Package).filter(Package.id == package_id).first()

            if package:
                package.tested = True
                package.test_passed = test_result.get('test_passed', False)
                package.test_logs = str(test_result.get('test_results', {}))

                logger.info(
                    "Updated package test status",
                    package_id=package_id,
                    passed=package.test_passed
                )

    def _get_package(self, package_id: int) -> Package:
        """Get package by ID"""
        with db_session_scope() as session:
            package = session.query(Package).filter(Package.id == package_id).first()
            if package:
                session.expunge(package)
            return package

    def run_vm_test(self, package: Package) -> Dict[str, Any]:
        """
        Run full VM-based test
        Provisions a VM, installs the package, validates installation, and cleans up

        Steps:
        1. Load VM provider from config
        2. Provision VM (restore snapshot, start VM, wait for boot)
        3. Copy package to VM
        4. Install package using install command
        5. Run validators (Device Manager, Event Log, detection rules, system stability)
        6. Collect logs
        7. Clean up VM
        8. Return test results dict

        Args:
            package: Package model instance to test

        Returns:
            Dict containing:
                - test_passed: bool
                - vm_provider: str (provider type used)
                - test_duration: float (seconds)
                - provision_result: Dict
                - install_result: Dict
                - validation_result: Dict
                - cleanup_result: Dict
                - error: Optional[str]
        """
        logger.info("Starting VM-based testing", package_id=package.id)

        # Step 1: Load VM provider from config
        vm_provider_type = self.test_config.get('vm_provider', 'local')
        vm_config = self.test_config.get('vm_config', {})

        logger.info(
            "Loading VM provider",
            provider_type=vm_provider_type,
            package_id=package.id
        )

        try:
            # Step 2: Instantiate the appropriate VM provider
            if vm_provider_type == 'local':
                # Use Hyper-V provider for local testing
                from autopackager.agents.testing.vm_providers.hyperv_provider import HyperVProvider

                hyperv_config = vm_config.get('hyperv', {})
                if not hyperv_config:
                    logger.error("Hyper-V configuration not found in config")
                    return {
                        'test_passed': False,
                        'error': 'Hyper-V configuration not found in config'
                    }

                # Add timeout from parent config
                hyperv_config['timeout_minutes'] = self.test_config.get('timeout_minutes', 30)

                provider = HyperVProvider(hyperv_config)

            elif vm_provider_type == 'azure':
                # Use Azure provider for cloud-based testing
                logger.error("Azure VM provider not yet implemented")
                return {
                    'test_passed': False,
                    'error': 'Azure VM provider not yet implemented'
                }

            else:
                logger.error(
                    "Unknown VM provider type",
                    provider_type=vm_provider_type
                )
                return {
                    'test_passed': False,
                    'error': f'Unknown VM provider type: {vm_provider_type}'
                }

            # Step 3-8: Run complete test workflow via provider
            # The provider's run_test() method handles:
            # - VM provisioning (restore snapshot, start, wait for boot)
            # - Package installation (copy files, execute install command)
            # - Validation (Device Manager, Event Log, detection rules, stability)
            # - Cleanup (stop VM, restore snapshot)
            logger.info("Executing VM test workflow", package_id=package.id)

            test_result = provider.run_test(package)

            # Log test completion
            if test_result.get('test_passed'):
                logger.info(
                    "VM test completed successfully",
                    package_id=package.id,
                    duration=test_result.get('test_duration')
                )
            else:
                logger.error(
                    "VM test failed",
                    package_id=package.id,
                    error=test_result.get('error'),
                    duration=test_result.get('test_duration')
                )

            return test_result

        except Exception as e:
            logger.error(
                "VM test failed with exception",
                package_id=package.id,
                error=str(e)
            )
            return {
                'test_passed': False,
                'error': f'VM test exception: {str(e)}',
                'vm_provider': vm_provider_type,
                'test_duration': 0.0
            }
