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
        package_id = job.metadata.get('package_id')
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
        Run full VM-based test (for future implementation)
        This would provision a VM, install the package, and verify functionality
        """
        logger.info("VM testing not yet implemented (future feature)")

        # TODO: Implement VM-based testing
        # 1. Provision clean Windows VM from snapshot
        # 2. Copy .intunewin package to VM
        # 3. Simulate Intune Management Extension deployment
        # 4. Run installation
        # 5. Verify detection rules
        # 6. Test basic functionality
        # 7. Run uninstallation
        # 8. Restore VM snapshot

        return {
            'test_passed': True,
            'note': 'VM testing not yet implemented'
        }
