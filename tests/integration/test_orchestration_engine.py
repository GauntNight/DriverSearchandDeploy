"""Integration tests for Orchestration Engine"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime
from contextlib import contextmanager

from autopackager.orchestration.engine import OrchestrationEngine
from autopackager.models.job import Job, JobState, JobType


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


class TestOrchestrationEngineJobCreation:
    """Tests for OrchestrationEngine job creation"""

    @patch('autopackager.orchestration.engine.get_config')
    def test_create_job_with_all_parameters(self, mock_get_config, db_session):
        """Test creating a job with all parameters"""
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
            software_title='Intel Chipset Driver',
            vendor='Dell',
            current_version='10.1.0.1000',
            hardware_model='Latitude 7490',
            driver_type='Chipset',
            metadata={'test_key': 'test_value'}
        )

        # Verify job created
        assert job is not None
        assert job.id is not None
        assert job.job_type == JobType.DRIVER_UPDATE
        assert job.software_title == 'Intel Chipset Driver'
        assert job.vendor == 'Dell'
        assert job.current_version == '10.1.0.1000'
        assert job.hardware_model == 'Latitude 7490'
        assert job.driver_type == 'Chipset'
        assert job.state == JobState.PENDING
        assert job.job_metadata['test_key'] == 'test_value'
        assert job.retry_count == 0

        # Verify job persisted in database
        retrieved_job = engine.get_job(job.id)
        assert retrieved_job is not None
        assert retrieved_job.id == job.id
        assert retrieved_job.software_title == 'Intel Chipset Driver'

    @patch('autopackager.orchestration.engine.get_config')
    def test_create_job_with_minimal_parameters(self, mock_get_config, db_session):
        """Test creating a job with only required parameters"""
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
            software_title='Adobe Reader',
            vendor='Adobe'
        )

        assert job is not None
        assert job.job_type == JobType.SOFTWARE_UPDATE
        assert job.software_title == 'Adobe Reader'
        assert job.vendor == 'Adobe'
        assert job.current_version is None
        assert job.hardware_model is None
        assert job.driver_type is None
        assert job.job_metadata == {}

    @patch('autopackager.orchestration.engine.get_config')
    def test_create_multiple_jobs(self, mock_get_config, db_session):
        """Test creating multiple jobs"""
        mock_get_config.return_value = {
            'jobs': {
                'max_retries': 3,
                'retry_delay_seconds': 60,
                'concurrent_jobs': 5
            }
        }

        engine = OrchestrationEngine()

        job1 = engine.create_job(
            job_type=JobType.DRIVER_UPDATE,
            software_title='Driver 1',
            vendor='Dell'
        )

        job2 = engine.create_job(
            job_type=JobType.DRIVER_UPDATE,
            software_title='Driver 2',
            vendor='HP'
        )

        assert job1.id != job2.id
        assert job1.software_title == 'Driver 1'
        assert job2.software_title == 'Driver 2'

        # Verify both jobs persisted
        all_jobs = engine.get_all_jobs()
        assert len(all_jobs) >= 2


class TestOrchestrationEngineJobRetrieval:
    """Tests for OrchestrationEngine job retrieval methods"""

    @patch('autopackager.orchestration.engine.get_config')
    def test_get_job_existing(self, mock_get_config, sample_job):
        """Test retrieving an existing job"""
        mock_get_config.return_value = {
            'jobs': {
                'max_retries': 3,
                'retry_delay_seconds': 60,
                'concurrent_jobs': 5
            }
        }

        engine = OrchestrationEngine()
        job = engine.get_job(sample_job.id)

        assert job is not None
        assert job.id == sample_job.id
        assert job.software_title == sample_job.software_title

    @patch('autopackager.orchestration.engine.get_config')
    def test_get_job_nonexistent(self, mock_get_config, db_session):
        """Test retrieving a non-existent job"""
        mock_get_config.return_value = {
            'jobs': {
                'max_retries': 3,
                'retry_delay_seconds': 60,
                'concurrent_jobs': 5
            }
        }

        engine = OrchestrationEngine()
        job = engine.get_job(999999)

        assert job is None

    @patch('autopackager.orchestration.engine.get_config')
    def test_get_pending_jobs(self, mock_get_config, db_session):
        """Test retrieving pending jobs"""
        mock_get_config.return_value = {
            'jobs': {
                'max_retries': 3,
                'retry_delay_seconds': 60,
                'concurrent_jobs': 5
            }
        }

        engine = OrchestrationEngine()

        # Create multiple jobs with different states
        job1 = engine.create_job(
            job_type=JobType.DRIVER_UPDATE,
            software_title='Pending Job 1',
            vendor='Dell'
        )

        job2 = engine.create_job(
            job_type=JobType.DRIVER_UPDATE,
            software_title='Pending Job 2',
            vendor='HP'
        )

        job3 = engine.create_job(
            job_type=JobType.DRIVER_UPDATE,
            software_title='Completed Job',
            vendor='Lenovo'
        )
        engine.update_job_state(job3.id, JobState.COMPLETED)

        # Get pending jobs
        pending_jobs = engine.get_pending_jobs()

        assert len(pending_jobs) >= 2
        pending_ids = [job.id for job in pending_jobs]
        assert job1.id in pending_ids
        assert job2.id in pending_ids
        assert job3.id not in pending_ids

    @patch('autopackager.orchestration.engine.get_config')
    def test_get_pending_jobs_with_limit(self, mock_get_config, db_session):
        """Test retrieving pending jobs with limit"""
        mock_get_config.return_value = {
            'jobs': {
                'max_retries': 3,
                'retry_delay_seconds': 60,
                'concurrent_jobs': 5
            }
        }

        engine = OrchestrationEngine()

        # Create 5 pending jobs
        for i in range(5):
            engine.create_job(
                job_type=JobType.DRIVER_UPDATE,
                software_title=f'Job {i}',
                vendor='Dell'
            )

        # Get only 2
        pending_jobs = engine.get_pending_jobs(limit=2)

        assert len(pending_jobs) == 2

    @patch('autopackager.orchestration.engine.get_config')
    def test_get_jobs_by_state(self, mock_get_config, db_session):
        """Test retrieving jobs by specific state"""
        mock_get_config.return_value = {
            'jobs': {
                'max_retries': 3,
                'retry_delay_seconds': 60,
                'concurrent_jobs': 5
            }
        }

        engine = OrchestrationEngine()

        # Create jobs in different states
        job1 = engine.create_job(
            job_type=JobType.DRIVER_UPDATE,
            software_title='Discovering Job',
            vendor='Dell'
        )
        engine.update_job_state(job1.id, JobState.DISCOVERING)

        job2 = engine.create_job(
            job_type=JobType.DRIVER_UPDATE,
            software_title='Another Discovering Job',
            vendor='HP'
        )
        engine.update_job_state(job2.id, JobState.DISCOVERING)

        job3 = engine.create_job(
            job_type=JobType.DRIVER_UPDATE,
            software_title='Packaging Job',
            vendor='Lenovo'
        )
        engine.update_job_state(job3.id, JobState.PACKAGING)

        # Get jobs in DISCOVERING state
        discovering_jobs = engine.get_jobs_by_state(JobState.DISCOVERING)

        assert len(discovering_jobs) >= 2
        for job in discovering_jobs:
            assert job.state == JobState.DISCOVERING

    @patch('autopackager.orchestration.engine.get_config')
    def test_get_all_jobs(self, mock_get_config, db_session):
        """Test retrieving all jobs"""
        mock_get_config.return_value = {
            'jobs': {
                'max_retries': 3,
                'retry_delay_seconds': 60,
                'concurrent_jobs': 5
            }
        }

        engine = OrchestrationEngine()

        # Create jobs
        job1 = engine.create_job(
            job_type=JobType.DRIVER_UPDATE,
            software_title='Job 1',
            vendor='Dell'
        )

        job2 = engine.create_job(
            job_type=JobType.DRIVER_UPDATE,
            software_title='Job 2',
            vendor='HP'
        )

        all_jobs = engine.get_all_jobs()

        assert len(all_jobs) >= 2
        job_ids = [job.id for job in all_jobs]
        assert job1.id in job_ids
        assert job2.id in job_ids

    @patch('autopackager.orchestration.engine.get_config')
    def test_get_all_jobs_ordered_by_created_at(self, mock_get_config, db_session):
        """Test that get_all_jobs returns jobs ordered by created_at desc"""
        mock_get_config.return_value = {
            'jobs': {
                'max_retries': 3,
                'retry_delay_seconds': 60,
                'concurrent_jobs': 5
            }
        }

        engine = OrchestrationEngine()

        # Create jobs in sequence
        job1 = engine.create_job(
            job_type=JobType.DRIVER_UPDATE,
            software_title='First Job',
            vendor='Dell'
        )

        job2 = engine.create_job(
            job_type=JobType.DRIVER_UPDATE,
            software_title='Second Job',
            vendor='HP'
        )

        all_jobs = engine.get_all_jobs()

        # Most recent job should be first
        assert all_jobs[0].id == job2.id
        assert all_jobs[1].id == job1.id


class TestOrchestrationEngineJobStateManagement:
    """Tests for OrchestrationEngine job state management"""

    @patch('autopackager.orchestration.engine.get_config')
    def test_update_job_state_simple(self, mock_get_config, db_session):
        """Test updating job state"""
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
            software_title='Test Job',
            vendor='Dell'
        )

        assert job.state == JobState.PENDING

        # Update to DISCOVERING
        updated_job = engine.update_job_state(job.id, JobState.DISCOVERING)

        assert updated_job.state == JobState.DISCOVERING
        assert updated_job.updated_at is not None

        # Verify persisted
        retrieved_job = engine.get_job(job.id)
        assert retrieved_job.state == JobState.DISCOVERING

    @patch('autopackager.orchestration.engine.get_config')
    def test_update_job_state_with_error_message(self, mock_get_config, db_session):
        """Test updating job state with error message"""
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
            software_title='Test Job',
            vendor='Dell'
        )

        # Update to FAILED with error
        error_msg = 'Discovery failed: Network timeout'
        updated_job = engine.update_job_state(
            job.id,
            JobState.FAILED,
            error_message=error_msg
        )

        assert updated_job.state == JobState.FAILED
        assert updated_job.error_message == error_msg

    @patch('autopackager.orchestration.engine.get_config')
    def test_update_job_state_with_metadata(self, mock_get_config, db_session):
        """Test updating job state with metadata"""
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
            software_title='Test Job',
            vendor='Dell',
            metadata={'initial': 'data'}
        )

        # Update with new metadata
        metadata_update = {
            'target_version': '2.0.0',
            'download_url': 'https://example.com/driver.exe'
        }

        updated_job = engine.update_job_state(
            job.id,
            JobState.PENDING,
            metadata_update=metadata_update
        )

        # Verify metadata merged
        assert updated_job.job_metadata['initial'] == 'data'
        assert updated_job.job_metadata['target_version'] == '2.0.0'
        assert updated_job.job_metadata['download_url'] == 'https://example.com/driver.exe'

    @patch('autopackager.orchestration.engine.get_config')
    def test_update_job_state_updates_title_and_vendor(self, mock_get_config, db_session):
        """software_title/vendor can be re-pointed (consumer→enterprise substitution).

        The demo creates the job row from the consumer stub then substitutes the
        enterprise installer; the deployed app's displayName (← software_title)
        must follow the substitute, not the stub.
        """
        mock_get_config.return_value = {
            'jobs': {'max_retries': 3, 'retry_delay_seconds': 60, 'concurrent_jobs': 5}
        }
        engine = OrchestrationEngine()
        job = engine.create_job(
            job_type=JobType.NEW_SOFTWARE,
            software_title='Google Installer (x86)',  # consumer stub PE name
            vendor='Google LLC',
        )

        updated = engine.update_job_state(
            job.id, JobState.PENDING,
            metadata_update={'target_version': '149.0.7827.54'},
            software_title='Google Chrome',  # substituted enterprise MSI name
            vendor='Google LLC',
        )

        assert updated.software_title == 'Google Chrome'
        assert updated.vendor == 'Google LLC'
        assert updated.job_metadata['target_version'] == '149.0.7827.54'

    @patch('autopackager.orchestration.engine.get_config')
    def test_update_job_state_leaves_title_when_not_given(self, mock_get_config, db_session):
        """Omitting software_title/vendor must not clobber the existing values."""
        mock_get_config.return_value = {
            'jobs': {'max_retries': 3, 'retry_delay_seconds': 60, 'concurrent_jobs': 5}
        }
        engine = OrchestrationEngine()
        job = engine.create_job(
            job_type=JobType.NEW_SOFTWARE, software_title='Keep Me', vendor='Acme',
        )

        updated = engine.update_job_state(job.id, JobState.DISCOVERING)

        assert updated.software_title == 'Keep Me'
        assert updated.vendor == 'Acme'

    @patch('autopackager.orchestration.engine.get_config')
    def test_update_job_state_to_completed_sets_timestamp(self, mock_get_config, db_session):
        """Test that updating to COMPLETED sets completed_at timestamp"""
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
            software_title='Test Job',
            vendor='Dell'
        )

        assert job.completed_at is None

        # Update to COMPLETED
        updated_job = engine.update_job_state(job.id, JobState.COMPLETED)

        assert updated_job.completed_at is not None
        assert isinstance(updated_job.completed_at, datetime)

    @patch('autopackager.orchestration.engine.get_config')
    def test_update_job_state_nonexistent_raises_error(self, mock_get_config, db_session):
        """Test that updating non-existent job raises ValueError"""
        mock_get_config.return_value = {
            'jobs': {
                'max_retries': 3,
                'retry_delay_seconds': 60,
                'concurrent_jobs': 5
            }
        }

        engine = OrchestrationEngine()

        with pytest.raises(ValueError) as exc_info:
            engine.update_job_state(999999, JobState.DISCOVERING)

        assert 'Job 999999 not found' in str(exc_info.value)

    @patch('autopackager.orchestration.engine.get_config')
    def test_mark_job_failed(self, mock_get_config, db_session):
        """Test mark_job_failed convenience method"""
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
            software_title='Test Job',
            vendor='Dell'
        )

        error_msg = 'Test error message'
        engine.mark_job_failed(job.id, error_msg)

        retrieved_job = engine.get_job(job.id)
        assert retrieved_job.state == JobState.FAILED
        assert retrieved_job.error_message == error_msg

    @patch('autopackager.orchestration.engine.get_config')
    def test_mark_job_completed(self, mock_get_config, db_session):
        """Test mark_job_completed convenience method"""
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
            software_title='Test Job',
            vendor='Dell'
        )

        metadata = {'intune_app_id': 'app-123'}
        engine.mark_job_completed(job.id, metadata_update=metadata)

        retrieved_job = engine.get_job(job.id)
        assert retrieved_job.state == JobState.COMPLETED
        assert retrieved_job.completed_at is not None
        assert retrieved_job.job_metadata['intune_app_id'] == 'app-123'


class TestOrchestrationEngineRetryLogic:
    """Tests for OrchestrationEngine retry logic"""

    @patch('autopackager.orchestration.engine.get_config')
    def test_increment_retry_count(self, mock_get_config, db_session):
        """Test incrementing retry count"""
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
            software_title='Test Job',
            vendor='Dell'
        )

        assert job.retry_count == 0

        # Increment
        new_count = engine.increment_retry_count(job.id)
        assert new_count == 1

        # Verify persisted
        retrieved_job = engine.get_job(job.id)
        assert retrieved_job.retry_count == 1

        # Increment again
        new_count = engine.increment_retry_count(job.id)
        assert new_count == 2

    @patch('autopackager.orchestration.engine.get_config')
    def test_can_retry_job_under_max(self, mock_get_config, db_session):
        """Test can_retry_job returns True when under max retries"""
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
            software_title='Test Job',
            vendor='Dell'
        )

        # Should be able to retry
        assert engine.can_retry_job(job.id) is True

        # Increment retry count
        engine.increment_retry_count(job.id)
        assert engine.can_retry_job(job.id) is True

        engine.increment_retry_count(job.id)
        assert engine.can_retry_job(job.id) is True

    @patch('autopackager.orchestration.engine.get_config')
    def test_can_retry_job_at_max(self, mock_get_config, db_session):
        """Test can_retry_job returns False when at max retries"""
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
            software_title='Test Job',
            vendor='Dell'
        )

        # Increment to max retries
        for _ in range(3):
            engine.increment_retry_count(job.id)

        # Should not be able to retry
        assert engine.can_retry_job(job.id) is False

    @patch('autopackager.orchestration.engine.get_config')
    def test_can_retry_job_nonexistent(self, mock_get_config, db_session):
        """Test can_retry_job returns False for non-existent job"""
        mock_get_config.return_value = {
            'jobs': {
                'max_retries': 3,
                'retry_delay_seconds': 60,
                'concurrent_jobs': 5
            }
        }

        engine = OrchestrationEngine()

        assert engine.can_retry_job(999999) is False


class TestOrchestrationEngineDuplicateDetection:
    """Tests for OrchestrationEngine duplicate job detection"""

    @patch('autopackager.orchestration.engine.get_config')
    def test_job_exists_exact_match(self, mock_get_config, db_session):
        """Test job_exists detects exact duplicate"""
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
            software_title='Intel Chipset Driver',
            vendor='Dell',
            current_version='1.0.0',
            hardware_model='Latitude 7490',
            driver_type='Chipset'
        )

        # Check for duplicate
        exists = engine.job_exists(
            job_type=JobType.DRIVER_UPDATE,
            software_title='Intel Chipset Driver',
            vendor='Dell',
            current_version='1.0.0',
            hardware_model='Latitude 7490',
            driver_type='Chipset'
        )

        assert exists is True

    @patch('autopackager.orchestration.engine.get_config')
    def test_job_exists_no_match(self, mock_get_config, db_session):
        """Test job_exists returns False when no duplicate"""
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
            software_title='Intel Chipset Driver',
            vendor='Dell',
            current_version='1.0.0',
            hardware_model='Latitude 7490'
        )

        # Check for different job
        exists = engine.job_exists(
            job_type=JobType.DRIVER_UPDATE,
            software_title='Different Driver',
            vendor='HP'
        )

        assert exists is False

    @patch('autopackager.orchestration.engine.get_config')
    def test_job_exists_ignores_terminal_states(self, mock_get_config, db_session):
        """Test job_exists ignores jobs in terminal states"""
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
            software_title='Intel Chipset Driver',
            vendor='Dell'
        )

        # Mark as completed
        engine.update_job_state(job.id, JobState.COMPLETED)

        # Should not detect as duplicate (completed is terminal)
        exists = engine.job_exists(
            job_type=JobType.DRIVER_UPDATE,
            software_title='Intel Chipset Driver',
            vendor='Dell'
        )

        assert exists is False

    @patch('autopackager.orchestration.engine.get_config')
    def test_job_exists_detects_non_terminal_states(self, mock_get_config, db_session):
        """Test job_exists detects jobs in non-terminal states"""
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
            software_title='Intel Chipset Driver',
            vendor='Dell'
        )

        # Update to DISCOVERING (non-terminal)
        engine.update_job_state(job.id, JobState.DISCOVERING)

        # Should still detect as duplicate
        exists = engine.job_exists(
            job_type=JobType.DRIVER_UPDATE,
            software_title='Intel Chipset Driver',
            vendor='Dell'
        )

        assert exists is True

    @patch('autopackager.orchestration.engine.get_config')
    def test_job_exists_with_optional_fields(self, mock_get_config, db_session):
        """Test job_exists with optional field matching"""
        mock_get_config.return_value = {
            'jobs': {
                'max_retries': 3,
                'retry_delay_seconds': 60,
                'concurrent_jobs': 5
            }
        }

        engine = OrchestrationEngine()

        # Create job with specific hardware model
        job1 = engine.create_job(
            job_type=JobType.DRIVER_UPDATE,
            software_title='Intel Chipset Driver',
            vendor='Dell',
            hardware_model='Latitude 7490'
        )

        # Check with different hardware model - should not match
        exists = engine.job_exists(
            job_type=JobType.DRIVER_UPDATE,
            software_title='Intel Chipset Driver',
            vendor='Dell',
            hardware_model='OptiPlex 7070'
        )

        assert exists is False

        # Check with same hardware model - should match
        exists = engine.job_exists(
            job_type=JobType.DRIVER_UPDATE,
            software_title='Intel Chipset Driver',
            vendor='Dell',
            hardware_model='Latitude 7490'
        )

        assert exists is True


class TestOrchestrationEngineJobDeletion:
    """Tests for OrchestrationEngine job deletion"""

    @patch('autopackager.orchestration.engine.get_config')
    def test_delete_job_existing(self, mock_get_config, db_session):
        """Test deleting an existing job"""
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
            software_title='Test Job',
            vendor='Dell'
        )

        job_id = job.id

        # Delete job
        result = engine.delete_job(job_id)

        assert result is True

        # Verify job deleted
        retrieved_job = engine.get_job(job_id)
        assert retrieved_job is None

    @patch('autopackager.orchestration.engine.get_config')
    def test_delete_job_nonexistent(self, mock_get_config, db_session):
        """Test deleting a non-existent job returns False"""
        mock_get_config.return_value = {
            'jobs': {
                'max_retries': 3,
                'retry_delay_seconds': 60,
                'concurrent_jobs': 5
            }
        }

        engine = OrchestrationEngine()

        result = engine.delete_job(999999)

        assert result is False

    @patch('autopackager.orchestration.engine.get_config')
    def test_purge_jobs_all(self, mock_get_config, db_session):
        """Test purging all jobs"""
        mock_get_config.return_value = {
            'jobs': {
                'max_retries': 3,
                'retry_delay_seconds': 60,
                'concurrent_jobs': 5
            }
        }

        engine = OrchestrationEngine()

        # Create multiple jobs
        for i in range(5):
            engine.create_job(
                job_type=JobType.DRIVER_UPDATE,
                software_title=f'Job {i}',
                vendor='Dell'
            )

        # Purge all
        count = engine.purge_jobs()

        assert count == 5

        # Verify all deleted
        all_jobs = engine.get_all_jobs()
        assert len(all_jobs) == 0

    @patch('autopackager.orchestration.engine.get_config')
    def test_purge_jobs_by_state(self, mock_get_config, db_session):
        """Test purging jobs by state"""
        mock_get_config.return_value = {
            'jobs': {
                'max_retries': 3,
                'retry_delay_seconds': 60,
                'concurrent_jobs': 5
            }
        }

        engine = OrchestrationEngine()

        # Create jobs in different states
        job1 = engine.create_job(
            job_type=JobType.DRIVER_UPDATE,
            software_title='Failed Job 1',
            vendor='Dell'
        )
        engine.update_job_state(job1.id, JobState.FAILED)

        job2 = engine.create_job(
            job_type=JobType.DRIVER_UPDATE,
            software_title='Failed Job 2',
            vendor='HP'
        )
        engine.update_job_state(job2.id, JobState.FAILED)

        job3 = engine.create_job(
            job_type=JobType.DRIVER_UPDATE,
            software_title='Pending Job',
            vendor='Lenovo'
        )

        # Purge only FAILED jobs
        count = engine.purge_jobs(state=JobState.FAILED)

        assert count == 2

        # Verify FAILED jobs deleted
        failed_jobs = engine.get_jobs_by_state(JobState.FAILED)
        assert len(failed_jobs) == 0

        # Verify PENDING job still exists
        pending_jobs = engine.get_jobs_by_state(JobState.PENDING)
        assert len(pending_jobs) >= 1


class TestOrchestrationEngineConfiguration:
    """Tests for OrchestrationEngine configuration"""

    @patch('autopackager.orchestration.engine.get_config')
    def test_engine_loads_config(self, mock_get_config):
        """Test that engine loads configuration on init"""
        mock_get_config.return_value = {
            'jobs': {
                'max_retries': 5,
                'retry_delay_seconds': 120,
                'concurrent_jobs': 10
            }
        }

        engine = OrchestrationEngine()

        assert engine.max_retries == 5
        assert engine.retry_delay == 120
        assert engine.concurrent_jobs == 10

    @patch('autopackager.orchestration.engine.get_config')
    def test_engine_uses_default_config(self, mock_get_config):
        """Test that engine handles missing config gracefully"""
        mock_get_config.return_value = {
            'jobs': {
                'max_retries': 3,
                'retry_delay_seconds': 60,
                'concurrent_jobs': 5
            }
        }

        engine = OrchestrationEngine()

        # Should not raise exception
        assert engine.max_retries == 3
        assert engine.retry_delay == 60


class TestOrchestrationEngineIntegrationScenarios:
    """Integration tests for complete orchestration scenarios"""

    @patch('autopackager.orchestration.engine.get_config')
    def test_full_job_lifecycle(self, mock_get_config, db_session):
        """Test complete job lifecycle from creation to completion"""
        mock_get_config.return_value = {
            'jobs': {
                'max_retries': 3,
                'retry_delay_seconds': 60,
                'concurrent_jobs': 5
            }
        }

        engine = OrchestrationEngine()

        # 1. Create job
        job = engine.create_job(
            job_type=JobType.DRIVER_UPDATE,
            software_title='Intel Chipset Driver',
            vendor='Dell',
            hardware_model='Latitude 7490'
        )

        assert job.state == JobState.PENDING

        # 2. Start discovery
        engine.update_job_state(job.id, JobState.DISCOVERING)
        job = engine.get_job(job.id)
        assert job.state == JobState.DISCOVERING

        # 3. Discovery complete, update metadata
        engine.update_job_state(
            job.id,
            JobState.PENDING,
            metadata_update={
                'target_version': '2.0.0',
                'download_url': 'https://example.com/driver.exe'
            }
        )

        # 4. Start packaging
        engine.update_job_state(job.id, JobState.PACKAGING)
        job = engine.get_job(job.id)
        assert job.state == JobState.PACKAGING

        # 5. Packaging complete
        engine.update_job_state(
            job.id,
            JobState.PENDING,
            metadata_update={'package_id': 'pkg-123'}
        )

        # 6. Start testing
        engine.update_job_state(job.id, JobState.TESTING)
        job = engine.get_job(job.id)
        assert job.state == JobState.TESTING

        # 7. Testing complete, start deployment
        engine.update_job_state(job.id, JobState.DEPLOYING)
        job = engine.get_job(job.id)
        assert job.state == JobState.DEPLOYING

        # 8. Mark as completed
        engine.mark_job_completed(
            job.id,
            metadata_update={'intune_app_id': 'app-123'}
        )

        # Verify final state
        job = engine.get_job(job.id)
        assert job.state == JobState.COMPLETED
        assert job.completed_at is not None
        assert job.job_metadata['target_version'] == '2.0.0'
        assert job.job_metadata['package_id'] == 'pkg-123'
        assert job.job_metadata['intune_app_id'] == 'app-123'

    @patch('autopackager.orchestration.engine.get_config')
    def test_job_retry_scenario(self, mock_get_config, db_session):
        """Test job retry scenario"""
        mock_get_config.return_value = {
            'jobs': {
                'max_retries': 3,
                'retry_delay_seconds': 60,
                'concurrent_jobs': 5
            }
        }

        engine = OrchestrationEngine()

        # Create job
        job = engine.create_job(
            job_type=JobType.DRIVER_UPDATE,
            software_title='Intel Chipset Driver',
            vendor='Dell'
        )

        # First attempt fails
        engine.update_job_state(job.id, JobState.DISCOVERING)
        assert engine.can_retry_job(job.id) is True
        engine.increment_retry_count(job.id)

        # Second attempt fails
        assert engine.can_retry_job(job.id) is True
        engine.increment_retry_count(job.id)

        # Third attempt fails
        assert engine.can_retry_job(job.id) is True
        engine.increment_retry_count(job.id)

        # Max retries reached
        assert engine.can_retry_job(job.id) is False
        engine.mark_job_failed(job.id, 'Max retries exceeded')

        # Verify final state
        job = engine.get_job(job.id)
        assert job.state == JobState.FAILED
        assert job.retry_count == 3
        assert 'Max retries exceeded' in job.error_message

    @patch('autopackager.orchestration.engine.get_config')
    def test_concurrent_job_management(self, mock_get_config, db_session):
        """Test managing multiple concurrent jobs"""
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
        for i in range(5):
            job = engine.create_job(
                job_type=JobType.DRIVER_UPDATE,
                software_title=f'Driver {i}',
                vendor='Dell'
            )
            jobs.append(job)

        # Verify all created
        assert len(jobs) == 5

        # Move jobs through different states
        engine.update_job_state(jobs[0].id, JobState.DISCOVERING)
        engine.update_job_state(jobs[1].id, JobState.PACKAGING)
        engine.update_job_state(jobs[2].id, JobState.TESTING)
        engine.update_job_state(jobs[3].id, JobState.DEPLOYING)
        engine.mark_job_completed(jobs[4].id)

        # Verify states
        assert engine.get_job(jobs[0].id).state == JobState.DISCOVERING
        assert engine.get_job(jobs[1].id).state == JobState.PACKAGING
        assert engine.get_job(jobs[2].id).state == JobState.TESTING
        assert engine.get_job(jobs[3].id).state == JobState.DEPLOYING
        assert engine.get_job(jobs[4].id).state == JobState.COMPLETED

        # Get jobs by state
        discovering = engine.get_jobs_by_state(JobState.DISCOVERING)
        assert len(discovering) >= 1

        completed = engine.get_jobs_by_state(JobState.COMPLETED)
        assert len(completed) >= 1
