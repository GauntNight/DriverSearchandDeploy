"""Integration tests for end-to-end pipeline workflow"""

import pytest
from unittest.mock import Mock, patch, MagicMock, call
from datetime import datetime
from contextlib import contextmanager

from autopackager.orchestration.engine import OrchestrationEngine
from autopackager.orchestration.tasks import (
    create_packaging_job,
    process_job,
    discovery_task,
    packaging_task,
    testing_task,
    deployment_task
)
from autopackager.models.job import Job, JobState, JobType
from autopackager.models.package import Package


# Mark all tests in this module as integration tests
pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def mock_db_session_scope(db_session):
    """Fixture to patch db_session_scope with test database session"""
    @contextmanager
    def _mock_db_session_scope():
        yield db_session

    with patch('autopackager.orchestration.engine.db_session_scope', _mock_db_session_scope):
        yield


class TestFullPipelineIntegration:
    """Tests for complete end-to-end pipeline workflow"""

    @patch('autopackager.orchestration.tasks.deployment_task')
    @patch('autopackager.orchestration.tasks.testing_task')
    @patch('autopackager.orchestration.tasks.packaging_task')
    @patch('autopackager.orchestration.tasks.discovery_task')
    @patch('autopackager.orchestration.engine.get_config')
    def test_full_pipeline_success(
        self,
        mock_get_config,
        mock_discovery,
        mock_packaging,
        mock_testing,
        mock_deployment,
        db_session
    ):
        """Test complete pipeline from job creation through deployment"""
        # Setup config
        mock_get_config.return_value = {
            'jobs': {
                'max_retries': 3,
                'retry_delay_seconds': 60,
                'concurrent_jobs': 5
            }
        }

        # Create job
        engine = OrchestrationEngine()
        job = engine.create_job(
            job_type=JobType.DRIVER_UPDATE,
            software_title='Intel Chipset Driver',
            vendor='Dell',
            current_version='10.1.0.1000',
            hardware_model='Latitude 7490',
            driver_type='Chipset'
        )

        assert job.state == JobState.PENDING
        initial_job_id = job.id

        # Simulate discovery phase
        engine.update_job_state(job.id, JobState.DISCOVERING)
        job = engine.get_job(job.id)
        assert job.state == JobState.DISCOVERING

        # Discovery completes successfully
        engine.update_job_state(
            job.id,
            JobState.PENDING,
            metadata_update={
                'target_version': '10.1.18383.8213',
                'download_url': 'https://downloads.dell.com/driver.exe',
                'release_notes': 'Bug fixes and improvements'
            }
        )

        # Simulate packaging phase
        engine.update_job_state(job.id, JobState.PACKAGING)
        job = engine.get_job(job.id)
        assert job.state == JobState.PACKAGING

        # Create package in database
        package = Package(
            name='Intel Chipset Driver',
            version='10.1.18383.8213',
            vendor='Dell',
            intunewin_path='/packages/intel-chipset-driver.intunewin',
            installer_path='/downloads/driver.exe',
            install_command='driver.exe /S',
            uninstall_command='driver.exe /U',
            detection_rules=[
                {
                    'type': 'registry',
                    'path': 'HKLM\\SOFTWARE\\Intel\\Chipset',
                    'value': 'Version',
                    'data': '10.1.18383.8213'
                }
            ],
            requirements={
                'min_os_version': '10.0.19041',
                'architecture': 'x64'
            },
            tested=False,
            deployed=False
        )
        db_session.add(package)
        db_session.commit()

        # Packaging completes successfully
        engine.update_job_state(
            job.id,
            JobState.PENDING,
            metadata_update={
                'package_id': package.id,
                'intunewin_path': package.intunewin_path
            }
        )

        # Simulate testing phase
        engine.update_job_state(job.id, JobState.TESTING)
        job = engine.get_job(job.id)
        assert job.state == JobState.TESTING

        # Update package as tested
        package.tested = True
        package.test_passed = True
        package.vm_test_results = {
            'test_passed': True,
            'vm_provider': 'HyperVProvider',
            'test_duration': 120.5
        }
        db_session.commit()

        # Testing completes successfully
        engine.update_job_state(job.id, JobState.PENDING)

        # Simulate deployment phase
        engine.update_job_state(job.id, JobState.DEPLOYING)
        job = engine.get_job(job.id)
        assert job.state == JobState.DEPLOYING

        # Update package as deployed
        package.deployed = True
        package.intune_app_id = 'app-id-12345'
        db_session.commit()

        # Deployment completes successfully
        engine.mark_job_completed(
            job.id,
            metadata_update={
                'intune_app_id': 'app-id-12345',
                'deployment_status': 'success'
            }
        )

        # Verify final state
        final_job = engine.get_job(job.id)
        assert final_job.state == JobState.COMPLETED
        assert final_job.job_metadata['target_version'] == '10.1.18383.8213'
        assert final_job.job_metadata['package_id'] == package.id
        assert final_job.job_metadata['intune_app_id'] == 'app-id-12345'
        assert final_job.completed_at is not None

        # Verify package state
        assert package.tested is True
        assert package.test_passed is True
        assert package.deployed is True
        assert package.intune_app_id == 'app-id-12345'

    @patch('autopackager.agents.discovery.DiscoveryAgent')
    @patch('autopackager.agents.packaging.PackagingAgent')
    @patch('autopackager.agents.testing.TestingAgent')
    @patch('autopackager.agents.deployment.DeploymentAgent')
    @patch('autopackager.orchestration.engine.get_config')
    def test_full_pipeline_with_agent_mocks(
        self,
        mock_get_config,
        mock_deployment_agent_class,
        mock_testing_agent_class,
        mock_packaging_agent_class,
        mock_discovery_agent_class,
        db_session
    ):
        """Test complete pipeline with mocked agents to verify agent interactions"""
        # Setup config
        mock_get_config.return_value = {
            'jobs': {
                'max_retries': 3,
                'retry_delay_seconds': 60,
                'concurrent_jobs': 5
            }
        }

        # Setup mock agents
        mock_discovery_agent = Mock()
        mock_discovery_agent_class.return_value = mock_discovery_agent
        mock_discovery_agent.discover.return_value = {
            'update_available': True,
            'latest_version': '10.1.18383.8213',
            'download_url': 'https://downloads.dell.com/driver.exe',
            'release_notes': 'Bug fixes'
        }

        mock_packaging_agent = Mock()
        mock_packaging_agent_class.return_value = mock_packaging_agent
        mock_packaging_agent.package.return_value = {
            'package_id': 1,
            'intunewin_path': '/packages/driver.intunewin'
        }

        mock_testing_agent = Mock()
        mock_testing_agent_class.return_value = mock_testing_agent
        mock_testing_agent.test.return_value = {
            'test_passed': True,
            'vm_provider': 'HyperVProvider',
            'test_duration': 120.5
        }

        mock_deployment_agent = Mock()
        mock_deployment_agent_class.return_value = mock_deployment_agent
        mock_deployment_agent.deploy.return_value = {
            'intune_app_id': 'app-id-12345',
            'status': 'success'
        }

        # Create job
        engine = OrchestrationEngine()
        job = engine.create_job(
            job_type=JobType.DRIVER_UPDATE,
            software_title='Intel Chipset Driver',
            vendor='Dell',
            current_version='10.1.0.1000',
            hardware_model='Latitude 7490',
            driver_type='Chipset'
        )

        # Execute discovery phase
        engine.update_job_state(job.id, JobState.DISCOVERING)
        discovery_result = mock_discovery_agent.discover(job)

        assert discovery_result['update_available'] is True
        mock_discovery_agent.discover.assert_called_once_with(job)

        engine.update_job_state(
            job.id,
            JobState.PENDING,
            metadata_update={
                'target_version': discovery_result['latest_version'],
                'download_url': discovery_result['download_url']
            }
        )

        # Execute packaging phase
        engine.update_job_state(job.id, JobState.PACKAGING)
        job = engine.get_job(job.id)
        packaging_result = mock_packaging_agent.package(job)

        mock_packaging_agent.package.assert_called_once_with(job)

        engine.update_job_state(
            job.id,
            JobState.PENDING,
            metadata_update={
                'package_id': packaging_result['package_id'],
                'intunewin_path': packaging_result['intunewin_path']
            }
        )

        # Execute testing phase
        engine.update_job_state(job.id, JobState.TESTING)
        job = engine.get_job(job.id)
        testing_result = mock_testing_agent.test(job)

        assert testing_result['test_passed'] is True
        mock_testing_agent.test.assert_called_once_with(job)

        engine.update_job_state(job.id, JobState.PENDING)

        # Execute deployment phase
        engine.update_job_state(job.id, JobState.DEPLOYING)
        job = engine.get_job(job.id)
        deployment_result = mock_deployment_agent.deploy(job)

        mock_deployment_agent.deploy.assert_called_once_with(job)

        engine.mark_job_completed(
            job.id,
            metadata_update={
                'intune_app_id': deployment_result['intune_app_id'],
                'deployment_status': deployment_result['status']
            }
        )

        # Verify all agents were called in correct order
        final_job = engine.get_job(job.id)
        assert final_job.state == JobState.COMPLETED
        assert final_job.job_metadata['target_version'] == '10.1.18383.8213'
        assert final_job.job_metadata['package_id'] == 1
        assert final_job.job_metadata['intune_app_id'] == 'app-id-12345'

    @patch('autopackager.orchestration.engine.get_config')
    def test_pipeline_failure_at_discovery(self, mock_get_config, db_session):
        """Test pipeline handles failure during discovery phase"""
        mock_get_config.return_value = {
            'jobs': {
                'max_retries': 3,
                'retry_delay_seconds': 60,
                'concurrent_jobs': 5
            }
        }

        engine = OrchestrationEngine()
        job = engine.create_job(
            job_type=JobType.DRIVER_UPDATE,
            software_title='Test Driver',
            vendor='Dell'
        )

        # Simulate discovery failure
        engine.update_job_state(job.id, JobState.DISCOVERING)
        error_message = "Failed to fetch catalog from OEM"
        engine.mark_job_failed(job.id, error_message)

        # Verify job state
        failed_job = engine.get_job(job.id)
        assert failed_job.state == JobState.FAILED
        assert failed_job.error_message == error_message
        assert failed_job.completed_at is None  # Failed jobs don't get completed_at

    @patch('autopackager.orchestration.engine.get_config')
    def test_pipeline_failure_at_packaging(self, mock_get_config, db_session):
        """Test pipeline handles failure during packaging phase"""
        mock_get_config.return_value = {
            'jobs': {
                'max_retries': 3,
                'retry_delay_seconds': 60,
                'concurrent_jobs': 5
            }
        }

        engine = OrchestrationEngine()
        job = engine.create_job(
            job_type=JobType.DRIVER_UPDATE,
            software_title='Test Driver',
            vendor='Dell'
        )

        # Discovery succeeds
        engine.update_job_state(job.id, JobState.DISCOVERING)
        engine.update_job_state(
            job.id,
            JobState.PENDING,
            metadata_update={'target_version': '1.2.3'}
        )

        # Packaging fails
        engine.update_job_state(job.id, JobState.PACKAGING)
        error_message = "Download failed: network timeout"
        engine.mark_job_failed(job.id, error_message)

        # Verify job state
        failed_job = engine.get_job(job.id)
        assert failed_job.state == JobState.FAILED
        assert failed_job.error_message == error_message

    @patch('autopackager.orchestration.engine.get_config')
    def test_pipeline_failure_at_testing(self, mock_get_config, db_session):
        """Test pipeline handles failure during testing phase"""
        mock_get_config.return_value = {
            'jobs': {
                'max_retries': 3,
                'retry_delay_seconds': 60,
                'concurrent_jobs': 5
            }
        }

        engine = OrchestrationEngine()
        job = engine.create_job(
            job_type=JobType.DRIVER_UPDATE,
            software_title='Test Driver',
            vendor='Dell'
        )

        # Discovery and packaging succeed
        engine.update_job_state(job.id, JobState.DISCOVERING)
        engine.update_job_state(job.id, JobState.PENDING)
        engine.update_job_state(job.id, JobState.PACKAGING)
        engine.update_job_state(
            job.id,
            JobState.PENDING,
            metadata_update={'package_id': 1}
        )

        # Testing fails
        engine.update_job_state(job.id, JobState.TESTING)
        error_message = "Package failed smoke tests"
        engine.mark_job_failed(job.id, error_message)

        # Verify job state
        failed_job = engine.get_job(job.id)
        assert failed_job.state == JobState.FAILED
        assert failed_job.error_message == error_message

    @patch('autopackager.orchestration.engine.get_config')
    def test_pipeline_no_update_available(self, mock_get_config, db_session):
        """Test pipeline handles case when no update is available"""
        mock_get_config.return_value = {
            'jobs': {
                'max_retries': 3,
                'retry_delay_seconds': 60,
                'concurrent_jobs': 5
            }
        }

        engine = OrchestrationEngine()
        job = engine.create_job(
            job_type=JobType.DRIVER_UPDATE,
            software_title='Test Driver',
            vendor='Dell',
            current_version='10.1.18383.8213'
        )

        # Discovery finds no update needed
        engine.update_job_state(job.id, JobState.DISCOVERING)
        engine.mark_job_completed(
            job.id,
            metadata_update={'no_update_needed': True}
        )

        # Verify job completed without going through other phases
        completed_job = engine.get_job(job.id)
        assert completed_job.state == JobState.COMPLETED
        assert completed_job.job_metadata.get('no_update_needed') is True
        assert 'package_id' not in completed_job.job_metadata
        assert 'intune_app_id' not in completed_job.job_metadata

    @patch('autopackager.orchestration.engine.get_config')
    def test_pipeline_state_transitions(self, mock_get_config, db_session):
        """Test all valid state transitions in the pipeline"""
        mock_get_config.return_value = {
            'jobs': {
                'max_retries': 3,
                'retry_delay_seconds': 60,
                'concurrent_jobs': 5
            }
        }

        engine = OrchestrationEngine()
        job = engine.create_job(
            job_type=JobType.DRIVER_UPDATE,
            software_title='Test Driver',
            vendor='Dell'
        )

        # Track state transitions
        states = [job.state]

        # PENDING -> DISCOVERING
        engine.update_job_state(job.id, JobState.DISCOVERING)
        job = engine.get_job(job.id)
        states.append(job.state)

        # DISCOVERING -> PENDING
        engine.update_job_state(job.id, JobState.PENDING)
        job = engine.get_job(job.id)
        states.append(job.state)

        # PENDING -> PACKAGING
        engine.update_job_state(job.id, JobState.PACKAGING)
        job = engine.get_job(job.id)
        states.append(job.state)

        # PACKAGING -> PENDING
        engine.update_job_state(job.id, JobState.PENDING)
        job = engine.get_job(job.id)
        states.append(job.state)

        # PENDING -> TESTING
        engine.update_job_state(job.id, JobState.TESTING)
        job = engine.get_job(job.id)
        states.append(job.state)

        # TESTING -> PENDING
        engine.update_job_state(job.id, JobState.PENDING)
        job = engine.get_job(job.id)
        states.append(job.state)

        # PENDING -> DEPLOYING
        engine.update_job_state(job.id, JobState.DEPLOYING)
        job = engine.get_job(job.id)
        states.append(job.state)

        # DEPLOYING -> COMPLETED
        engine.mark_job_completed(job.id)
        job = engine.get_job(job.id)
        states.append(job.state)

        # Verify expected state transitions
        expected_states = [
            JobState.PENDING,
            JobState.DISCOVERING,
            JobState.PENDING,
            JobState.PACKAGING,
            JobState.PENDING,
            JobState.TESTING,
            JobState.PENDING,
            JobState.DEPLOYING,
            JobState.COMPLETED
        ]
        assert states == expected_states

    @patch('autopackager.orchestration.engine.get_config')
    def test_pipeline_with_retry_logic(self, mock_get_config, db_session):
        """Test pipeline retry behavior on transient failures"""
        mock_get_config.return_value = {
            'jobs': {
                'max_retries': 3,
                'retry_delay_seconds': 60,
                'concurrent_jobs': 5
            }
        }

        engine = OrchestrationEngine()
        job = engine.create_job(
            job_type=JobType.DRIVER_UPDATE,
            software_title='Test Driver',
            vendor='Dell'
        )

        # First attempt fails
        engine.update_job_state(job.id, JobState.DISCOVERING)
        assert engine.can_retry_job(job.id) is True

        retry_count = engine.increment_retry_count(job.id)
        assert retry_count == 1
        job = engine.get_job(job.id)
        assert job.retry_count == 1

        # Second attempt fails
        assert engine.can_retry_job(job.id) is True
        retry_count = engine.increment_retry_count(job.id)
        assert retry_count == 2

        # Third attempt fails
        assert engine.can_retry_job(job.id) is True
        retry_count = engine.increment_retry_count(job.id)
        assert retry_count == 3

        # Fourth attempt should not be allowed
        assert engine.can_retry_job(job.id) is False
        engine.mark_job_failed(job.id, "Max retries exceeded")

        failed_job = engine.get_job(job.id)
        assert failed_job.state == JobState.FAILED
        assert failed_job.retry_count == 3

    @patch('autopackager.orchestration.engine.get_config')
    def test_pipeline_metadata_updates(self, mock_get_config, db_session):
        """Test metadata accumulation throughout the pipeline"""
        mock_get_config.return_value = {
            'jobs': {
                'max_retries': 3,
                'retry_delay_seconds': 60,
                'concurrent_jobs': 5
            }
        }

        engine = OrchestrationEngine()
        job = engine.create_job(
            job_type=JobType.DRIVER_UPDATE,
            software_title='Test Driver',
            vendor='Dell',
            metadata={'initial_key': 'initial_value'}
        )

        # Verify initial metadata
        assert job.job_metadata['initial_key'] == 'initial_value'

        # Update metadata during discovery
        engine.update_job_state(
            job.id,
            JobState.DISCOVERING,
            metadata_update={'discovery_timestamp': '2024-01-01T00:00:00'}
        )
        job = engine.get_job(job.id)
        assert job.job_metadata['initial_key'] == 'initial_value'
        assert job.job_metadata['discovery_timestamp'] == '2024-01-01T00:00:00'

        # Update metadata during packaging
        engine.update_job_state(
            job.id,
            JobState.PACKAGING,
            metadata_update={'package_id': 123, 'package_size_mb': 45.2}
        )
        job = engine.get_job(job.id)
        assert job.job_metadata['initial_key'] == 'initial_value'
        assert job.job_metadata['discovery_timestamp'] == '2024-01-01T00:00:00'
        assert job.job_metadata['package_id'] == 123
        assert job.job_metadata['package_size_mb'] == 45.2

        # Update metadata during completion
        engine.mark_job_completed(
            job.id,
            metadata_update={'intune_app_id': 'app-123', 'deployment_ring': 'pilot'}
        )
        job = engine.get_job(job.id)

        # All metadata should be preserved
        assert job.job_metadata['initial_key'] == 'initial_value'
        assert job.job_metadata['discovery_timestamp'] == '2024-01-01T00:00:00'
        assert job.job_metadata['package_id'] == 123
        assert job.job_metadata['package_size_mb'] == 45.2
        assert job.job_metadata['intune_app_id'] == 'app-123'
        assert job.job_metadata['deployment_ring'] == 'pilot'


class TestPipelineEdgeCases:
    """Tests for edge cases and error conditions in the pipeline"""

    @patch('autopackager.orchestration.engine.get_config')
    def test_pipeline_with_multiple_jobs(self, mock_get_config, db_session):
        """Test pipeline can handle multiple concurrent jobs"""
        mock_get_config.return_value = {
            'jobs': {
                'max_retries': 3,
                'retry_delay_seconds': 60,
                'concurrent_jobs': 5
            }
        }

        engine = OrchestrationEngine()

        # Create multiple jobs
        jobs = []
        for i in range(3):
            job = engine.create_job(
                job_type=JobType.DRIVER_UPDATE,
                software_title=f'Test Driver {i}',
                vendor='Dell'
            )
            jobs.append(job)

        # Verify all jobs created
        assert len(jobs) == 3
        for i, job in enumerate(jobs):
            assert job.software_title == f'Test Driver {i}'
            assert job.state == JobState.PENDING

        # Update jobs to different states
        engine.update_job_state(jobs[0].id, JobState.DISCOVERING)
        engine.update_job_state(jobs[1].id, JobState.PACKAGING)
        engine.mark_job_completed(jobs[2].id)

        # Verify independent state management
        job0 = engine.get_job(jobs[0].id)
        job1 = engine.get_job(jobs[1].id)
        job2 = engine.get_job(jobs[2].id)

        assert job0.state == JobState.DISCOVERING
        assert job1.state == JobState.PACKAGING
        assert job2.state == JobState.COMPLETED

    @patch('autopackager.orchestration.engine.get_config')
    def test_pipeline_job_not_found(self, mock_get_config, db_session):
        """Test pipeline handles non-existent job gracefully"""
        mock_get_config.return_value = {
            'jobs': {
                'max_retries': 3,
                'retry_delay_seconds': 60,
                'concurrent_jobs': 5
            }
        }

        engine = OrchestrationEngine()

        # Try to get non-existent job
        job = engine.get_job(99999)
        assert job is None

    @patch('autopackager.orchestration.engine.get_config')
    def test_pipeline_with_software_update_job_type(self, mock_get_config, db_session):
        """Test pipeline with SOFTWARE_UPDATE job type instead of DRIVER_UPDATE"""
        mock_get_config.return_value = {
            'jobs': {
                'max_retries': 3,
                'retry_delay_seconds': 60,
                'concurrent_jobs': 5
            }
        }

        engine = OrchestrationEngine()
        job = engine.create_job(
            job_type=JobType.SOFTWARE_UPDATE,
            software_title='Adobe Reader DC',
            vendor='Adobe'
        )

        assert job.job_type == JobType.SOFTWARE_UPDATE
        assert job.driver_type is None
        assert job.hardware_model is None

        # Process through pipeline
        engine.update_job_state(job.id, JobState.DISCOVERING)
        engine.update_job_state(job.id, JobState.PACKAGING)
        engine.update_job_state(job.id, JobState.TESTING)
        engine.update_job_state(job.id, JobState.DEPLOYING)
        engine.mark_job_completed(job.id)

        completed_job = engine.get_job(job.id)
        assert completed_job.state == JobState.COMPLETED
        assert completed_job.job_type == JobType.SOFTWARE_UPDATE
