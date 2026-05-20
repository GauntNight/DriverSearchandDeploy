"""Unit tests for CLI commands using Click's CliRunner"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from click.testing import CliRunner
from datetime import datetime

from cli import cli
from autopackager.models.job import Job, JobType, JobState
from autopackager.models.package import Package


@pytest.fixture
def cli_runner():
    """Create a Click CLI runner for testing"""
    return CliRunner()


@pytest.fixture
def mock_engine():
    """Create a mock OrchestrationEngine"""
    engine = Mock()
    engine.get_all_jobs = Mock(return_value=[])
    engine.get_jobs_by_state = Mock(return_value=[])
    engine.get_job = Mock(return_value=None)
    engine.update_job_state = Mock()
    engine.purge_jobs = Mock(return_value=0)
    return engine


@pytest.fixture
def sample_cli_job():
    """Create a sample Job for CLI tests"""
    job = Mock(spec=Job)
    job.id = 1
    job.job_type = Mock()
    job.job_type.value = 'driver_update'
    job.state = Mock()
    job.state.value = 'pending'
    job.software_title = 'Intel Chipset Driver'
    job.current_version = '10.1.0.1000'
    job.target_version = '10.1.18383.8213'
    job.vendor = 'Dell'
    job.hardware_model = 'Latitude 7490'
    job.driver_type = 'Chipset'
    job.created_at = datetime(2024, 1, 15, 10, 30)
    job.updated_at = datetime(2024, 1, 15, 10, 35)
    job.error_message = None
    job.job_metadata = {'catalog_url': 'https://example.com/catalog.xml'}
    return job


# ============================================================================
# Init Command Tests
# ============================================================================

class TestInitCommand:
    """Test cases for 'init' command"""

    @patch('cli.init_db')
    def test_init_success(self, mock_init_db, cli_runner):
        """Test successful database initialization"""
        mock_init_db.return_value = None

        result = cli_runner.invoke(cli, ['init'])

        assert result.exit_code == 0
        assert 'Initializing AutoPackager' in result.output
        assert '✓' in result.output
        assert 'Database initialized successfully' in result.output
        mock_init_db.assert_called_once_with(create_tables=True)

    @patch('cli.init_db')
    def test_init_failure(self, mock_init_db, cli_runner):
        """Test database initialization failure"""
        mock_init_db.side_effect = Exception('Database connection failed')

        result = cli_runner.invoke(cli, ['init'])

        assert result.exit_code == 1
        assert 'Failed to initialize database' in result.output
        assert 'Database connection failed' in result.output


# ============================================================================
# Create Driver Job Command Tests
# ============================================================================

class TestCreateDriverJobCommand:
    """Test cases for 'create-driver-job' command"""

    @patch('cli.create_packaging_job')
    def test_create_driver_job_success(self, mock_create_job, cli_runner):
        """Test successful driver job creation"""
        # Mock Celery task result
        mock_result = Mock()
        mock_result.id = 'task-123-456'
        mock_create_job.delay.return_value = mock_result

        result = cli_runner.invoke(cli, [
            'create-driver-job',
            '--vendor', 'dell',
            '--model', 'Latitude 7490',
            '--driver-type', 'Chipset',
            '--current-version', '10.1.0.1000'
        ])

        assert result.exit_code == 0
        assert 'Creating driver update job' in result.output
        assert 'Vendor: dell' in result.output
        assert 'Model: Latitude 7490' in result.output
        assert 'Driver Type: Chipset' in result.output
        assert '✓' in result.output
        assert 'Job created successfully' in result.output
        assert 'task-123-456' in result.output

        # Verify the task was called with correct parameters
        mock_create_job.delay.assert_called_once_with(
            job_type='driver_update',
            software_title='DELL Latitude 7490 Driver Pack',
            vendor='dell',
            current_version='10.1.0.1000',
            hardware_model='Latitude 7490',
            driver_type='Chipset'
        )

    @patch('cli.create_packaging_job')
    def test_create_driver_job_without_optional_params(self, mock_create_job, cli_runner):
        """Test driver job creation without optional parameters"""
        mock_result = Mock()
        mock_result.id = 'task-789'
        mock_create_job.delay.return_value = mock_result

        result = cli_runner.invoke(cli, [
            'create-driver-job',
            '--vendor', 'hp',
            '--model', 'EliteBook 850 G8'
        ])

        assert result.exit_code == 0
        assert 'Driver Type: All' in result.output
        mock_create_job.delay.assert_called_once_with(
            job_type='driver_update',
            software_title='HP EliteBook 850 G8 Driver Pack',
            vendor='hp',
            current_version=None,
            hardware_model='EliteBook 850 G8',
            driver_type=None
        )

    def test_create_driver_job_missing_required_params(self, cli_runner):
        """Test driver job creation with missing required parameters"""
        result = cli_runner.invoke(cli, ['create-driver-job', '--vendor', 'dell'])

        assert result.exit_code != 0
        assert 'Missing option' in result.output or 'Error' in result.output

    @patch('cli.create_packaging_job')
    def test_create_driver_job_failure(self, mock_create_job, cli_runner):
        """Test driver job creation failure"""
        mock_create_job.delay.side_effect = Exception('Celery broker not available')

        result = cli_runner.invoke(cli, [
            'create-driver-job',
            '--vendor', 'lenovo',
            '--model', 'ThinkPad X1 Carbon Gen 9'
        ])

        assert result.exit_code == 1
        assert 'Failed to create job' in result.output
        assert 'Celery broker not available' in result.output

    def test_create_driver_job_invalid_vendor(self, cli_runner):
        """Test driver job creation with invalid vendor"""
        result = cli_runner.invoke(cli, [
            'create-driver-job',
            '--vendor', 'invalid',
            '--model', 'Some Model'
        ])

        assert result.exit_code != 0
        assert 'Invalid value' in result.output or 'Error' in result.output


# ============================================================================
# Jobs List Command Tests
# ============================================================================

class TestJobsListCommand:
    """Test cases for 'jobs list' command"""

    @patch('cli.OrchestrationEngine')
    def test_list_jobs_empty(self, mock_engine_class, cli_runner, mock_engine):
        """Test listing jobs when no jobs exist"""
        mock_engine.get_all_jobs.return_value = []
        mock_engine_class.return_value = mock_engine

        result = cli_runner.invoke(cli, ['jobs', 'list'])

        assert result.exit_code == 0
        assert 'No jobs found' in result.output

    @patch('cli.OrchestrationEngine')
    def test_list_jobs_with_data(self, mock_engine_class, cli_runner, mock_engine, sample_cli_job):
        """Test listing jobs with data"""
        mock_engine.get_all_jobs.return_value = [sample_cli_job]
        mock_engine_class.return_value = mock_engine

        result = cli_runner.invoke(cli, ['jobs', 'list'])

        assert result.exit_code == 0
        assert 'Packaging Jobs' in result.output
        assert 'Intel Chipset' in result.output  # Partial match due to table truncation
        assert 'Dell' in result.output
        assert '10.1.0.1000' in result.output
        assert '10.1.18383.8213' in result.output
        assert 'pending' in result.output.lower()
        mock_engine.get_all_jobs.assert_called_once_with(limit=20)

    @patch('cli.OrchestrationEngine')
    def test_list_jobs_with_state_filter(self, mock_engine_class, cli_runner, mock_engine, sample_cli_job):
        """Test listing jobs filtered by state"""
        sample_cli_job.state = JobState.COMPLETED
        mock_engine.get_jobs_by_state.return_value = [sample_cli_job]
        mock_engine_class.return_value = mock_engine

        result = cli_runner.invoke(cli, ['jobs', 'list', '--state', 'completed'])

        assert result.exit_code == 0
        assert 'Intel Chipset' in result.output  # Partial match due to table truncation
        mock_engine.get_jobs_by_state.assert_called_once_with(JobState.COMPLETED, limit=20)

    @patch('cli.OrchestrationEngine')
    def test_list_jobs_with_custom_limit(self, mock_engine_class, cli_runner, mock_engine):
        """Test listing jobs with custom limit"""
        mock_engine.get_all_jobs.return_value = []
        mock_engine_class.return_value = mock_engine

        result = cli_runner.invoke(cli, ['jobs', 'list', '--limit', '50'])

        assert result.exit_code == 0
        mock_engine.get_all_jobs.assert_called_once_with(limit=50)

    @patch('cli.OrchestrationEngine')
    def test_list_jobs_multiple_states(self, mock_engine_class, cli_runner, mock_engine):
        """Test listing jobs with different states shows correct colors"""
        job_completed = Mock(spec=Job)
        job_completed.id = 1
        job_completed.state = Mock()
        job_completed.state.value = 'completed'
        job_completed.software_title = 'Completed Job'
        job_completed.vendor = 'Dell'
        job_completed.current_version = '1.0'
        job_completed.target_version = '2.0'
        job_completed.created_at = datetime(2024, 1, 15, 10, 30)

        job_failed = Mock(spec=Job)
        job_failed.id = 2
        job_failed.state = Mock()
        job_failed.state.value = 'failed'
        job_failed.software_title = 'Failed Job'
        job_failed.vendor = 'HP'
        job_failed.current_version = '1.0'
        job_failed.target_version = '2.0'
        job_failed.created_at = datetime(2024, 1, 15, 11, 30)

        mock_engine.get_all_jobs.return_value = [job_completed, job_failed]
        mock_engine_class.return_value = mock_engine

        result = cli_runner.invoke(cli, ['jobs', 'list'])

        assert result.exit_code == 0
        assert 'Completed Job' in result.output
        assert 'Failed Job' in result.output


# ============================================================================
# Job Status Command Tests
# ============================================================================

class TestJobStatusCommand:
    """Test cases for 'jobs status' command"""

    @patch('cli.OrchestrationEngine')
    def test_job_status_found(self, mock_engine_class, cli_runner, mock_engine, sample_cli_job):
        """Test getting status of existing job"""
        mock_engine.get_job.return_value = sample_cli_job
        mock_engine_class.return_value = mock_engine

        result = cli_runner.invoke(cli, ['jobs', 'status', '1'])

        assert result.exit_code == 0
        assert 'Job #1' in result.output
        assert 'Intel Chipset' in result.output
        assert 'driver_update' in result.output
        assert 'State:' in result.output  # Just check the field is present
        assert 'Dell' in result.output
        assert '10.1.0.1000' in result.output
        assert '10.1.18383.8213' in result.output
        assert 'Latitude 7490' in result.output
        assert 'catalog_url' in result.output
        mock_engine.get_job.assert_called_once_with(1)

    @patch('cli.OrchestrationEngine')
    def test_job_status_not_found(self, mock_engine_class, cli_runner, mock_engine):
        """Test getting status of non-existent job"""
        mock_engine.get_job.return_value = None
        mock_engine_class.return_value = mock_engine

        result = cli_runner.invoke(cli, ['jobs', 'status', '999'])

        assert result.exit_code == 0
        assert 'Job 999 not found' in result.output
        mock_engine.get_job.assert_called_once_with(999)

    @patch('cli.OrchestrationEngine')
    def test_job_status_with_error(self, mock_engine_class, cli_runner, mock_engine, sample_cli_job):
        """Test job status display with error message"""
        sample_cli_job.error_message = 'Download failed: Connection timeout'
        mock_engine.get_job.return_value = sample_cli_job
        mock_engine_class.return_value = mock_engine

        result = cli_runner.invoke(cli, ['jobs', 'status', '1'])

        assert result.exit_code == 0
        assert 'Error:' in result.output
        assert 'Download failed: Connection timeout' in result.output

    def test_job_status_invalid_job_id(self, cli_runner):
        """Test job status with invalid job ID"""
        result = cli_runner.invoke(cli, ['jobs', 'status', 'invalid'])

        assert result.exit_code != 0


# ============================================================================
# Job Cancel Command Tests
# ============================================================================

class TestJobCancelCommand:
    """Test cases for 'jobs cancel' command"""

    @patch('cli.OrchestrationEngine')
    def test_cancel_single_job_success(self, mock_engine_class, cli_runner, mock_engine, sample_cli_job):
        """Test cancelling a single job"""
        mock_engine.get_job.return_value = sample_cli_job
        mock_engine_class.return_value = mock_engine

        result = cli_runner.invoke(cli, ['jobs', 'cancel', '1'])

        assert result.exit_code == 0
        assert '✓' in result.output
        assert 'Job #1 cancelled' in result.output
        mock_engine.get_job.assert_called_once_with(1)
        mock_engine.update_job_state.assert_called_once_with(1, JobState.CANCELLED)

    @patch('cli.OrchestrationEngine')
    def test_cancel_single_job_not_found(self, mock_engine_class, cli_runner, mock_engine):
        """Test cancelling a non-existent job"""
        mock_engine.get_job.return_value = None
        mock_engine_class.return_value = mock_engine

        result = cli_runner.invoke(cli, ['jobs', 'cancel', '999'])

        assert result.exit_code == 0
        assert 'Job 999 not found' in result.output

    @patch('cli.OrchestrationEngine')
    def test_cancel_all_stuck_jobs(self, mock_engine_class, cli_runner, mock_engine):
        """Test cancelling all stuck jobs"""
        job1 = Mock(spec=Job)
        job1.id = 1
        job1.software_title = 'Job 1'

        job2 = Mock(spec=Job)
        job2.id = 2
        job2.software_title = 'Job 2'

        # Setup the engine to return stuck jobs for different states
        mock_engine.get_jobs_by_state.side_effect = [
            [job1],  # PENDING
            [],      # DISCOVERING
            [job2],  # PACKAGING
            [],      # TESTING
            []       # DEPLOYING
        ]
        mock_engine_class.return_value = mock_engine

        result = cli_runner.invoke(cli, ['jobs', 'cancel', '1', '--all-stuck'])

        assert result.exit_code == 0
        assert '✓' in result.output
        assert 'Cancelled 2 job(s)' in result.output
        assert 'Cancelled job #1: Job 1' in result.output
        assert 'Cancelled job #2: Job 2' in result.output

        # Verify update_job_state was called twice
        assert mock_engine.update_job_state.call_count == 2


# ============================================================================
# Jobs Purge Command Tests
# ============================================================================

class TestJobsPurgeCommand:
    """Test cases for 'jobs purge' command"""

    @patch('cli.OrchestrationEngine')
    def test_purge_jobs_no_confirmation_aborts(self, mock_engine_class, cli_runner, mock_engine):
        """Test that purge without confirmation aborts"""
        mock_engine.get_all_jobs.return_value = [Mock(), Mock(), Mock()]
        mock_engine_class.return_value = mock_engine

        # Simulate user saying 'n' to confirmation
        result = cli_runner.invoke(cli, ['jobs', 'purge'], input='n\n')

        assert result.exit_code == 1
        assert mock_engine.purge_jobs.call_count == 0

    @patch('cli.OrchestrationEngine')
    def test_purge_jobs_with_yes_flag(self, mock_engine_class, cli_runner, mock_engine):
        """Test purging jobs with --yes flag"""
        mock_engine.get_all_jobs.return_value = [Mock(), Mock(), Mock()]
        mock_engine.purge_jobs.return_value = 3
        mock_engine_class.return_value = mock_engine

        result = cli_runner.invoke(cli, ['jobs', 'purge', '--yes'])

        assert result.exit_code == 0
        assert '✓' in result.output
        assert 'Deleted 3 job(s)' in result.output
        mock_engine.purge_jobs.assert_called_once_with(None)

    @patch('cli.OrchestrationEngine')
    def test_purge_jobs_with_state_filter(self, mock_engine_class, cli_runner, mock_engine):
        """Test purging jobs with state filter"""
        mock_engine.get_jobs_by_state.return_value = [Mock(), Mock()]
        mock_engine.purge_jobs.return_value = 2
        mock_engine_class.return_value = mock_engine

        result = cli_runner.invoke(cli, ['jobs', 'purge', '--state', 'completed', '--yes'])

        assert result.exit_code == 0
        assert 'Deleted 2 job(s)' in result.output
        mock_engine.purge_jobs.assert_called_once_with('completed')

    @patch('cli.OrchestrationEngine')
    def test_purge_jobs_empty_database(self, mock_engine_class, cli_runner, mock_engine):
        """Test purging when no jobs exist"""
        mock_engine.get_all_jobs.return_value = []
        mock_engine_class.return_value = mock_engine

        result = cli_runner.invoke(cli, ['jobs', 'purge', '--yes'])

        assert result.exit_code == 0
        assert 'No' in result.output
        assert 'job records to delete' in result.output


# ============================================================================
# Worker Purge Command Tests
# ============================================================================

class TestWorkerPurgeCommand:
    """Test cases for 'worker purge' command"""

    @patch('autopackager.orchestration.celery_app.celery_app')
    def test_worker_purge_with_yes_flag(self, mock_celery_app, cli_runner):
        """Test purging Celery queue with --yes flag"""
        mock_celery_app.control.purge.return_value = 5

        result = cli_runner.invoke(cli, ['worker', 'purge', '--yes'])

        assert result.exit_code == 0
        assert '✓' in result.output
        assert 'Purged 5 task(s)' in result.output
        mock_celery_app.control.purge.assert_called_once()

    @patch('autopackager.orchestration.celery_app.celery_app')
    def test_worker_purge_no_confirmation_aborts(self, mock_celery_app, cli_runner):
        """Test that worker purge without confirmation aborts"""
        result = cli_runner.invoke(cli, ['worker', 'purge'], input='n\n')

        assert result.exit_code == 1
        assert mock_celery_app.control.purge.call_count == 0


# ============================================================================
# Version Command Tests
# ============================================================================

class TestVersionCommand:
    """Test cases for 'version' command"""

    @patch('autopackager.__version__', '0.1.0')
    def test_version_command(self, cli_runner):
        """Test version command displays version"""
        result = cli_runner.invoke(cli, ['version'])

        assert result.exit_code == 0
        assert 'AutoPackager' in result.output
        assert 'version' in result.output
        assert '0.1.0' in result.output


# ============================================================================
# Global Options Tests
# ============================================================================

class TestGlobalOptions:
    """Test cases for global CLI options"""

    @patch('cli.setup_logging')
    @patch('cli.init_db')
    def test_debug_flag(self, mock_init_db, mock_setup_logging, cli_runner):
        """Test --debug flag enables debug logging"""
        result = cli_runner.invoke(cli, ['--debug', 'init'])

        # Verify setup_logging was called with DEBUG level
        assert mock_setup_logging.call_count > 0
        call_kwargs = mock_setup_logging.call_args[1]
        assert call_kwargs.get('log_level') == 'DEBUG'

    @patch('cli.init_db')
    def test_help_option(self, mock_init_db, cli_runner):
        """Test --help option displays help"""
        result = cli_runner.invoke(cli, ['--help'])

        assert result.exit_code == 0
        assert 'AutoPackager' in result.output
        assert 'init' in result.output
        assert 'create-driver-job' in result.output
        assert 'jobs' in result.output


# ============================================================================
# Error Handling Tests
# ============================================================================

class TestErrorHandling:
    """Test cases for error handling across commands"""

    def test_invalid_command(self, cli_runner):
        """Test invalid command name"""
        result = cli_runner.invoke(cli, ['invalid-command'])

        assert result.exit_code != 0

    def test_invalid_subcommand(self, cli_runner):
        """Test invalid subcommand"""
        result = cli_runner.invoke(cli, ['jobs', 'invalid'])

        assert result.exit_code != 0

    @patch('cli.OrchestrationEngine')
    def test_orchestration_engine_initialization_error(self, mock_engine_class, cli_runner):
        """Test error when OrchestrationEngine fails to initialize"""
        mock_engine_class.side_effect = Exception('Database connection failed')

        result = cli_runner.invoke(cli, ['jobs', 'list'])

        assert result.exit_code != 0
