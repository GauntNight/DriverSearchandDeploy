"""Testing Agent - Validate Package Installation"""

import os
import sys
from datetime import datetime, timezone
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
        # Local install validation: actually install the package on this build
        # machine, verify it landed (correcting the detection rule from the real
        # Uninstall key if needed), then uninstall — BEFORE publishing. Defaults
        # on (operator chose "always gate"). Auto-skipped under pytest.
        self.local_validation_config = self.test_config.get('local_install_validation', {})
        self.local_validation_enabled = self.local_validation_config.get('enabled', True)

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
        smoke_test_result = self._run_smoke_tests(package)

        # Initialize combined test result with smoke test results
        test_result = {
            'test_passed': smoke_test_result.get('test_passed', False),
            'smoke_tests': smoke_test_result,
            'local_install_validation': None,
            'vm_test_results': None
        }

        # Local install validation (pre-publish gate): install → verify →
        # discover/correct detection → uninstall, on this build machine. Never
        # runs under pytest (it would install real software) — detected via
        # sys.modules since pytest.ini's TESTING env needs the absent
        # pytest-env plugin.
        running_tests = ('pytest' in sys.modules) or bool(os.environ.get('TESTING'))
        if self.local_validation_enabled and not running_tests:
            lv_result = self._run_local_install_validation(package, job)
            test_result['local_install_validation'] = lv_result
            if not lv_result.get('skipped'):
                test_result['test_passed'] = (
                    test_result['test_passed'] and lv_result.get('passed', False)
                )

        # Check if VM testing is enabled
        vm_testing_enabled = self.test_config.get('vm_testing_enabled', False)

        if vm_testing_enabled:
            logger.info("VM testing enabled, running VM tests", package_id=package.id)

            # Run VM-based tests
            vm_test_result = self.run_vm_test(package)
            test_result['vm_test_results'] = vm_test_result

            # Combine results - both smoke tests and VM tests must pass
            test_result['test_passed'] = (
                smoke_test_result.get('test_passed', False) and
                vm_test_result.get('test_passed', False)
            )

            logger.info(
                "Combined test results",
                package_id=package.id,
                smoke_passed=smoke_test_result.get('test_passed'),
                vm_passed=vm_test_result.get('test_passed'),
                overall_passed=test_result['test_passed']
            )
        else:
            logger.info("VM testing disabled, using smoke test results only", package_id=package.id)

        # Update package test status with combined results
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

    def _run_local_install_validation(self, package: Package, job: Job) -> Dict[str, Any]:
        """Install → verify → discover/correct → uninstall on the build machine.

        On a detection mismatch this corrects the rule from the app's real
        Uninstall key and writes it back to BOTH the Package (so the publish
        uses the right rule) and the catalog overlay (so the next run is right).
        Streams progress to the demo console via the job's event channel.
        """
        from autopackager.agents.testing.local_install_validator import LocalInstallValidator

        def emit(text: str, level: str = "info"):
            try:
                from demo.events import publish_pipeline_event
                publish_pipeline_event(job.id, "testing", text, level=level)
            except Exception:
                pass

        logger.info("Starting local install validation", package_id=package.id)
        validator = LocalInstallValidator(self.local_validation_config, emit=emit)
        result = validator.validate(package, job)

        # Apply corrected detection facts, if any.
        corrected = result.get('corrected_detection_rules')
        if corrected:
            self._apply_detection_corrections(package, job, result)

        logger.info(
            "Local install validation complete",
            package_id=package.id,
            passed=result.get('passed'),
            installed=result.get('installed'),
            detection_fired=result.get('detection_fired'),
            corrected=bool(corrected),
        )
        return result

    def _apply_detection_corrections(self, package: Package, job: Job, result: Dict[str, Any]):
        """Persist corrected detection rule + uninstall command (Package + catalog)."""
        from autopackager.utils.installer_catalog import detection_rule_to_graph

        catalog_rules = result.get('corrected_detection_rules') or []
        uninstall_cmd = result.get('corrected_uninstall_command')

        # 1) Package gets Graph-format rules (what deployment publishes).
        graph_rules = []
        for r in catalog_rules:
            try:
                graph_rules.append(detection_rule_to_graph(r))
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not convert corrected rule to Graph", error=str(exc))
        if graph_rules:
            with db_session_scope() as session:
                pkg = session.query(Package).filter(Package.id == package.id).first()
                if pkg:
                    pkg.detection_rules = graph_rules
                    if uninstall_cmd:
                        pkg.uninstall_command = uninstall_cmd
            logger.info("Package detection rules corrected by validation", package_id=package.id)

        # 2) Catalog overlay gets catalog-format rules (so the next run is right).
        catalog_entry_id = (job.job_metadata or {}).get('catalog_entry_id')
        if catalog_entry_id:
            from autopackager.utils import installer_catalog
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            updates = {'detection_rules': catalog_rules}
            if uninstall_cmd and '{installer_filename}' not in uninstall_cmd:
                updates['uninstall_command_template'] = uninstall_cmd
            installer_catalog.update_overlay_entry(
                catalog_entry_id, updates,
                validation_note=f"validated locally {today}; detection corrected to real Uninstall key",
            )
            logger.info("Catalog entry corrected by validation", entry_id=catalog_entry_id)

    def _update_package_test_status(self, package_id: int, test_result: Dict[str, Any]):
        """Update package test status in database"""
        with db_session_scope() as session:
            package = session.query(Package).filter(Package.id == package_id).first()

            if package:
                package.tested = True
                package.test_passed = test_result.get('test_passed', False)

                # Store smoke test, local install validation, and VM test results
                test_logs = {
                    'smoke_tests': test_result.get('smoke_tests', {}).get('test_results', {}),
                    'local_install_validation': test_result.get('local_install_validation'),
                    'vm_test_results': test_result.get('vm_test_results')
                }
                package.test_logs = str(test_logs)

                logger.info(
                    "Updated package test status",
                    package_id=package_id,
                    passed=package.test_passed,
                    has_vm_results=test_result.get('vm_test_results') is not None
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
        import time
        import subprocess
        from concurrent.futures import TimeoutError as FuturesTimeoutError

        logger.info("Starting VM-based testing", package_id=package.id)

        # Initialize test result structure
        test_result = {
            'test_passed': False,
            'vm_provider': None,
            'test_duration': 0.0,
            'provision_result': {},
            'install_result': {},
            'validation_result': {},
            'cleanup_result': {},
            'error': None
        }

        start_time = time.time()
        provider = None

        try:
            # Step 1: Load VM provider from config
            vm_provider_type = self.test_config.get('vm_provider', 'local')
            vm_config = self.test_config.get('vm_config', {})
            timeout_minutes = self.test_config.get('timeout_minutes', 30)

            test_result['vm_provider'] = vm_provider_type

            logger.info(
                "Loading VM provider",
                provider_type=vm_provider_type,
                package_id=package.id,
                timeout_minutes=timeout_minutes
            )

            # Step 2: Instantiate the appropriate VM provider
            if vm_provider_type == 'local':
                # Use Hyper-V provider for local testing
                from autopackager.agents.testing.vm_providers.hyperv_provider import HyperVProvider

                hyperv_config = vm_config.get('hyperv', {})
                if not hyperv_config:
                    error_msg = 'Hyper-V configuration not found in config'
                    logger.error(error_msg, package_id=package.id)
                    test_result['error'] = error_msg
                    return test_result

                # Add timeout from parent config
                hyperv_config['timeout_minutes'] = timeout_minutes

                provider = HyperVProvider(hyperv_config)

            elif vm_provider_type == 'azure':
                # Use Azure provider for cloud-based testing
                error_msg = 'Azure VM provider not yet implemented'
                logger.error(error_msg, package_id=package.id)
                test_result['error'] = error_msg
                return test_result

            else:
                error_msg = f'Unknown VM provider type: {vm_provider_type}'
                logger.error(
                    "Unknown VM provider type",
                    provider_type=vm_provider_type,
                    package_id=package.id
                )
                test_result['error'] = error_msg
                return test_result

            # Step 3-8: Run complete test workflow via provider
            # The provider's run_test() method handles:
            # - VM provisioning (restore snapshot, start, wait for boot)
            # - Package installation (copy files, execute install command)
            # - Validation (Device Manager, Event Log, detection rules, stability)
            # - Cleanup (stop VM, restore snapshot)
            logger.info("Executing VM test workflow", package_id=package.id)

            # Execute test with timeout enforcement
            test_result = provider.run_test(package)

            # Update duration
            test_result['test_duration'] = time.time() - start_time

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

        except subprocess.TimeoutExpired as e:
            # Handle subprocess timeout errors
            error_msg = f'VM test timeout (subprocess): {str(e)}'
            logger.error(
                "VM test timeout (subprocess)",
                package_id=package.id,
                timeout_minutes=timeout_minutes,
                error=str(e),
                elapsed_time=time.time() - start_time
            )
            test_result['error'] = error_msg
            test_result['test_duration'] = time.time() - start_time
            return test_result

        except TimeoutError as e:
            # Handle timeout errors specifically
            error_msg = f'VM test timeout after {timeout_minutes} minutes: {str(e)}'
            logger.error(
                "VM test timeout",
                package_id=package.id,
                timeout_minutes=timeout_minutes,
                error=str(e),
                elapsed_time=time.time() - start_time
            )
            test_result['error'] = error_msg
            test_result['test_duration'] = time.time() - start_time
            return test_result

        except FuturesTimeoutError as e:
            # Handle concurrent.futures timeout errors
            error_msg = f'VM test timeout after {timeout_minutes} minutes: {str(e)}'
            logger.error(
                "VM test timeout (futures)",
                package_id=package.id,
                timeout_minutes=timeout_minutes,
                error=str(e),
                elapsed_time=time.time() - start_time
            )
            test_result['error'] = error_msg
            test_result['test_duration'] = time.time() - start_time
            return test_result

        except Exception as e:
            # Handle all other exceptions
            error_msg = f'VM test exception: {str(e)}'
            logger.error(
                "VM test failed with exception",
                package_id=package.id,
                error=str(e),
                exception_type=type(e).__name__,
                elapsed_time=time.time() - start_time
            )
            test_result['error'] = error_msg
            test_result['test_duration'] = time.time() - start_time
            return test_result

        finally:
            # Ensure cleanup always runs, even if errors occurred during provider instantiation
            if provider is not None:
                logger.info(
                    "Ensuring VM cleanup",
                    package_id=package.id,
                    has_provider=True
                )
                try:
                    # The provider's run_test() already has cleanup in finally block,
                    # but we ensure cleanup is called if provider was instantiated
                    # and test didn't reach run_test()
                    if not test_result.get('cleanup_result'):
                        logger.info("Running final cleanup", package_id=package.id)
                        cleanup_result = provider.cleanup_vm()
                        test_result['cleanup_result'] = cleanup_result

                        if not cleanup_result.get('success'):
                            logger.warning(
                                "Final VM cleanup incomplete",
                                package_id=package.id,
                                error=cleanup_result.get('error')
                            )
                except Exception as cleanup_error:
                    logger.error(
                        "Final VM cleanup failed",
                        package_id=package.id,
                        error=str(cleanup_error)
                    )
                    test_result['cleanup_result'] = {
                        'success': False,
                        'error': str(cleanup_error)
                    }
            else:
                logger.debug(
                    "No provider to cleanup",
                    package_id=package.id,
                    has_provider=False
                )
