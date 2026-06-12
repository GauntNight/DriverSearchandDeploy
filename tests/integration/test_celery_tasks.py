"""Integration tests for Celery tasks"""

import pytest
from unittest.mock import Mock, patch, MagicMock, call
from datetime import datetime

from autopackager.orchestration.tasks import (
    create_packaging_job,
    process_job,
    discovery_task,
    packaging_task,
    testing_task,
    deployment_task,
    poll_deployment_status,
    continuous_catalog_discovery
)
from autopackager.models.job import Job, JobState, JobType
from autopackager.models.discovery_run import DiscoveryRun


# Mark all tests in this module as integration tests
pytestmark = pytest.mark.integration


class TestCreatePackagingJob:
    """Tests for create_packaging_job Celery task"""

    @patch('autopackager.orchestration.tasks.process_job')
    @patch('autopackager.orchestration.tasks.OrchestrationEngine')
    def test_create_packaging_job_success(self, mock_engine_class, mock_process_job):
        """Test successful job creation"""
        # Setup mock engine
        mock_engine = Mock()
        mock_engine_class.return_value = mock_engine

        # Create mock job
        mock_job = Mock(spec=Job)
        mock_job.id = 123
        mock_engine.create_job.return_value = mock_job

        # Execute task
        result = create_packaging_job.apply(kwargs={
            'job_type': 'driver_update',
            'software_title': 'Test Driver',
            'vendor': 'Dell',
            'current_version': '1.0.0',
            'hardware_model': 'Latitude 7490',
            'driver_type': 'Chipset',
            'metadata': {'test': 'data'}
        }).get()

        # Verify engine create_job was called correctly
        mock_engine.create_job.assert_called_once_with(
            job_type=JobType.DRIVER_UPDATE,
            software_title='Test Driver',
            vendor='Dell',
            current_version='1.0.0',
            hardware_model='Latitude 7490',
            driver_type='Chipset',
            metadata={'test': 'data'}
        )

        # Verify process_job was triggered
        mock_process_job.delay.assert_called_once_with(123)

        # Verify result
        assert result['job_id'] == 123
        assert result['status'] == 'created'

    @patch('autopackager.orchestration.tasks.process_job')
    @patch('autopackager.orchestration.tasks.OrchestrationEngine')
    def test_create_packaging_job_with_minimal_params(self, mock_engine_class, mock_process_job):
        """Test job creation with minimal parameters"""
        mock_engine = Mock()
        mock_engine_class.return_value = mock_engine

        mock_job = Mock(spec=Job)
        mock_job.id = 456
        mock_engine.create_job.return_value = mock_job

        result = create_packaging_job.apply(kwargs={
            'job_type': 'software_update',
            'software_title': 'Adobe Reader',
            'vendor': 'Adobe'
        }).get()

        # Verify None values passed through
        call_kwargs = mock_engine.create_job.call_args[1]
        assert call_kwargs['current_version'] is None
        assert call_kwargs['hardware_model'] is None
        assert call_kwargs['driver_type'] is None
        assert call_kwargs['metadata'] is None

        assert result['job_id'] == 456


class TestProcessJob:
    """Tests for process_job Celery task"""

    @patch('autopackager.orchestration.tasks.chain')
    @patch('autopackager.orchestration.tasks.discovery_task')
    @patch('autopackager.orchestration.tasks.packaging_task')
    @patch('autopackager.orchestration.tasks.testing_task')
    @patch('autopackager.orchestration.tasks.deployment_task')
    @patch('autopackager.orchestration.tasks.OrchestrationEngine')
    def test_process_job_creates_pipeline(
        self, mock_engine_class, mock_deploy, mock_test,
        mock_package, mock_discover, mock_chain
    ):
        """Test that process_job creates correct task chain"""
        # Setup
        mock_engine = Mock()
        mock_engine_class.return_value = mock_engine

        mock_job = Mock(spec=Job)
        mock_job.id = 1
        mock_engine.get_job.return_value = mock_job

        # Mock chain
        mock_pipeline = Mock()
        mock_result = Mock()
        mock_result.id = 'pipeline-123'
        mock_pipeline.apply_async.return_value = mock_result
        mock_chain.return_value = mock_pipeline

        # Execute
        result = process_job.apply(kwargs={'job_id': 1}).get()

        # Verify chain created with correct tasks
        mock_chain.assert_called_once()
        chain_args = mock_chain.call_args[0]
        assert len(chain_args) == 4

        # Verify pipeline executed
        mock_pipeline.apply_async.assert_called_once()

        # Verify result
        assert result['job_id'] == 1
        assert result['pipeline_id'] == 'pipeline-123'

    @patch('autopackager.orchestration.tasks.OrchestrationEngine')
    def test_process_job_handles_missing_job(self, mock_engine_class):
        """Test error handling when job not found"""
        mock_engine = Mock()
        mock_engine_class.return_value = mock_engine
        mock_engine.get_job.return_value = None

        result = process_job.apply(kwargs={'job_id': 999}).get()

        assert result['error'] == 'Job not found'

    @patch('autopackager.orchestration.tasks.chain')
    @patch('autopackager.orchestration.tasks.OrchestrationEngine')
    def test_process_job_handles_pipeline_exception(self, mock_engine_class, mock_chain):
        """Test error handling when pipeline creation fails"""
        mock_engine = Mock()
        mock_engine_class.return_value = mock_engine

        mock_job = Mock(spec=Job)
        mock_job.id = 1
        mock_engine.get_job.return_value = mock_job

        # Make chain raise an exception
        mock_chain.side_effect = Exception('Pipeline creation failed')

        # Execute and expect exception
        with pytest.raises(Exception) as exc_info:
            process_job.apply(kwargs={'job_id': 1}).get()

        assert 'Pipeline creation failed' in str(exc_info.value)

        # Verify job marked as failed
        mock_engine.mark_job_failed.assert_called_once_with(1, 'Pipeline creation failed')


class TestDiscoveryTask:
    """Tests for discovery_task Celery task"""

    @patch('autopackager.agents.discovery.DiscoveryAgent')
    @patch('autopackager.orchestration.tasks.OrchestrationEngine')
    def test_discovery_task_update_available(self, mock_engine_class, mock_agent_class):
        """Test discovery task when update is available"""
        # Setup engine
        mock_engine = Mock()
        mock_engine_class.return_value = mock_engine

        mock_job = Mock(spec=Job)
        mock_job.id = 1
        mock_engine.get_job.return_value = mock_job

        # Setup agent
        mock_agent = Mock()
        mock_agent_class.return_value = mock_agent
        mock_agent.discover.return_value = {
            'update_available': True,
            'latest_version': '2.0.0',
            'download_url': 'https://example.com/driver.exe',
            'release_notes': 'Bug fixes'
        }

        # Execute
        result = discovery_task.apply(kwargs={'job_id': 1}).get()

        # Verify state updated to DISCOVERING
        mock_engine.update_job_state.assert_any_call(1, JobState.DISCOVERING)

        # Verify agent called
        mock_agent.discover.assert_called_once_with(mock_job)

        # Verify state updated to PENDING with metadata
        assert mock_engine.update_job_state.call_count == 2
        second_call = mock_engine.update_job_state.call_args_list[1]
        assert second_call[0][0] == 1
        assert second_call[0][1] == JobState.PENDING
        assert second_call[1]['metadata_update']['target_version'] == '2.0.0'
        assert second_call[1]['metadata_update']['download_url'] == 'https://example.com/driver.exe'

        # Verify result
        assert result['job_id'] == 1
        assert result['update_available'] is True

    @patch('autopackager.agents.discovery.DiscoveryAgent')
    @patch('autopackager.orchestration.tasks.OrchestrationEngine')
    def test_discovery_task_no_update_needed(self, mock_engine_class, mock_agent_class):
        """Test discovery task when no update is needed"""
        mock_engine = Mock()
        mock_engine_class.return_value = mock_engine

        mock_job = Mock(spec=Job)
        mock_engine.get_job.return_value = mock_job

        mock_agent = Mock()
        mock_agent_class.return_value = mock_agent
        mock_agent.discover.return_value = {'update_available': False}

        result = discovery_task.apply(kwargs={'job_id': 1}).get()

        # Verify job marked as completed
        mock_engine.mark_job_completed.assert_called_once()
        call_args = mock_engine.mark_job_completed.call_args
        assert call_args[0][0] == 1
        assert call_args[1]['metadata_update']['no_update_needed'] is True

        # Verify result
        assert result['update_available'] is False
        assert result['completed'] is True

    @patch('autopackager.agents.discovery.DiscoveryAgent')
    @patch('autopackager.orchestration.tasks.OrchestrationEngine')
    def test_discovery_task_retry_on_failure(self, mock_engine_class, mock_agent_class):
        """Test discovery task retries on failure"""
        mock_engine = Mock()
        mock_engine_class.return_value = mock_engine
        mock_engine.can_retry_job.return_value = True
        mock_engine.increment_retry_count.return_value = 1
        mock_engine.retry_delay = 60

        mock_job = Mock(spec=Job)
        mock_engine.get_job.return_value = mock_job

        mock_agent = Mock()
        mock_agent_class.return_value = mock_agent
        mock_agent.discover.side_effect = Exception('Discovery failed')

        # Execute and expect retry
        with pytest.raises(Exception):
            discovery_task.apply(kwargs={'job_id': 1}).get()

        # Verify retry count incremented (may be called multiple times due to Celery retries)
        assert mock_engine.increment_retry_count.called
        assert mock_engine.increment_retry_count.call_args[0][0] == 1

        # Verify job not marked as failed (will retry)
        mock_engine.mark_job_failed.assert_not_called()

    @patch('autopackager.agents.discovery.DiscoveryAgent')
    @patch('autopackager.orchestration.tasks.OrchestrationEngine')
    def test_discovery_task_fails_after_max_retries(self, mock_engine_class, mock_agent_class):
        """Test discovery task fails after max retries"""
        mock_engine = Mock()
        mock_engine_class.return_value = mock_engine
        mock_engine.can_retry_job.return_value = False

        mock_job = Mock(spec=Job)
        mock_engine.get_job.return_value = mock_job

        mock_agent = Mock()
        mock_agent_class.return_value = mock_agent
        mock_agent.discover.side_effect = Exception('Discovery failed')

        # Execute and expect exception
        with pytest.raises(Exception) as exc_info:
            discovery_task.apply(kwargs={'job_id': 1}).get()

        # Verify job marked as failed
        mock_engine.mark_job_failed.assert_called_once()
        call_args = mock_engine.mark_job_failed.call_args[0]
        assert call_args[0] == 1
        assert 'Discovery failed' in call_args[1]


class TestPackagingTask:
    """Tests for packaging_task Celery task"""

    @patch('autopackager.agents.packaging.PackagingAgent')
    @patch('autopackager.orchestration.tasks.OrchestrationEngine')
    def test_packaging_task_success(self, mock_engine_class, mock_agent_class):
        """Test successful packaging task"""
        mock_engine = Mock()
        mock_engine_class.return_value = mock_engine

        mock_job = Mock(spec=Job)
        mock_engine.get_job.return_value = mock_job

        mock_agent = Mock()
        mock_agent_class.return_value = mock_agent
        mock_agent.package.return_value = {
            'package_id': 'pkg-123',
            'intunewin_path': '/path/to/package.intunewin'
        }

        result = packaging_task.apply(kwargs={
            'previous_result': {'job_id': 1},
            'job_id': 1
        }).get()

        # Verify state updated to PACKAGING
        mock_engine.update_job_state.assert_any_call(1, JobState.PACKAGING)

        # Verify agent called
        mock_agent.package.assert_called_once_with(mock_job)

        # Verify metadata updated
        assert mock_engine.update_job_state.call_count == 2
        second_call = mock_engine.update_job_state.call_args_list[1]
        assert second_call[1]['metadata_update']['package_id'] == 'pkg-123'

        # Verify result
        assert result['package_id'] == 'pkg-123'

    @patch('autopackager.orchestration.tasks.OrchestrationEngine')
    def test_packaging_task_skips_if_completed(self, mock_engine_class):
        """Test packaging task skips if previous task marked as completed"""
        mock_engine = Mock()
        mock_engine_class.return_value = mock_engine

        previous_result = {'completed': True, 'job_id': 1}

        result = packaging_task.apply(kwargs={
            'previous_result': previous_result,
            'job_id': 1
        }).get()

        # Verify state not updated
        mock_engine.update_job_state.assert_not_called()

        # Verify result passed through
        assert result == previous_result

    @patch('autopackager.agents.packaging.PackagingAgent')
    @patch('autopackager.orchestration.tasks.OrchestrationEngine')
    def test_packaging_task_retry_on_failure(self, mock_engine_class, mock_agent_class):
        """Test packaging task retries on failure"""
        mock_engine = Mock()
        mock_engine_class.return_value = mock_engine
        mock_engine.can_retry_job.return_value = True
        mock_engine.increment_retry_count.return_value = 1
        mock_engine.retry_delay = 60

        mock_job = Mock(spec=Job)
        mock_engine.get_job.return_value = mock_job

        mock_agent = Mock()
        mock_agent_class.return_value = mock_agent
        mock_agent.package.side_effect = Exception('Packaging failed')

        with pytest.raises(Exception):
            packaging_task.apply(kwargs={
                'previous_result': None,
                'job_id': 1
            }).get()

        # Verify retry count incremented (may be called multiple times due to Celery retries)
        assert mock_engine.increment_retry_count.called
        assert mock_engine.increment_retry_count.call_args[0][0] == 1
        mock_engine.mark_job_failed.assert_not_called()


class TestTestingTask:
    """Tests for testing_task Celery task"""

    @patch('autopackager.agents.testing.TestingAgent')
    @patch('autopackager.orchestration.tasks.OrchestrationEngine')
    def test_testing_task_success(self, mock_engine_class, mock_agent_class):
        """Test successful testing task"""
        mock_engine = Mock()
        mock_engine_class.return_value = mock_engine

        mock_job = Mock(spec=Job)
        mock_engine.get_job.return_value = mock_job

        mock_agent = Mock()
        mock_agent_class.return_value = mock_agent
        mock_agent.test.return_value = {'test_passed': True}

        result = testing_task.apply(kwargs={
            'previous_result': {'job_id': 1},
            'job_id': 1
        }).get()

        # Verify state updated to TESTING
        mock_engine.update_job_state.assert_called_once_with(1, JobState.TESTING)

        # Verify agent called
        mock_agent.test.assert_called_once_with(mock_job)

        # Verify result
        assert result['test_passed'] is True

    @patch('autopackager.agents.testing.TestingAgent')
    @patch('autopackager.orchestration.tasks.OrchestrationEngine')
    def test_testing_task_handles_failure(self, mock_engine_class, mock_agent_class):
        """Test testing task handles test failures"""
        mock_engine = Mock()
        mock_engine_class.return_value = mock_engine
        mock_engine.can_retry_job.return_value = False  # Prevent retries

        mock_job = Mock(spec=Job)
        mock_engine.get_job.return_value = mock_job

        mock_agent = Mock()
        mock_agent_class.return_value = mock_agent
        mock_agent.test.return_value = {
            'test_passed': False,
            'error_message': 'Installation failed'
        }

        with pytest.raises(Exception) as exc_info:
            testing_task.apply(kwargs={
                'previous_result': {'job_id': 1},
                'job_id': 1
            }).get()

        # Verify job marked as failed
        assert mock_engine.mark_job_failed.called
        call_args = mock_engine.mark_job_failed.call_args[0]
        assert call_args[0] == 1
        assert 'Installation failed' in call_args[1]

    @patch('autopackager.orchestration.tasks.OrchestrationEngine')
    def test_testing_task_skips_if_completed(self, mock_engine_class):
        """Test testing task skips if previous task marked as completed"""
        mock_engine = Mock()
        mock_engine_class.return_value = mock_engine

        previous_result = {'completed': True}

        result = testing_task.apply(kwargs={
            'previous_result': previous_result,
            'job_id': 1
        }).get()

        mock_engine.update_job_state.assert_not_called()
        assert result == previous_result


class TestDeploymentTask:
    """Tests for deployment_task Celery task"""

    @patch('autopackager.utils.azure_validator.AzureValidator')
    @patch('autopackager.agents.deployment.DeploymentAgent')
    @patch('autopackager.orchestration.tasks.OrchestrationEngine')
    def test_deployment_task_success(self, mock_engine_class, mock_agent_class, mock_validator_class):
        """Test successful deployment task"""
        mock_engine = Mock()
        mock_engine_class.return_value = mock_engine

        mock_job = Mock(spec=Job)
        mock_engine.get_job.return_value = mock_job

        mock_agent = Mock()
        mock_agent_class.return_value = mock_agent
        mock_agent.deploy.return_value = {
            'intune_app_id': 'app-123',
            'status': 'deployed'
        }

        result = deployment_task.apply(kwargs={
            'previous_result': {'job_id': 1},
            'job_id': 1
        }).get()

        # Verify state updated to DEPLOYING
        mock_engine.update_job_state.assert_called_once_with(1, JobState.DEPLOYING)

        # Verify agent called
        mock_agent.deploy.assert_called_once_with(mock_job)

        # Verify job marked as completed
        mock_engine.mark_job_completed.assert_called_once()
        call_args = mock_engine.mark_job_completed.call_args
        assert call_args[0][0] == 1
        assert call_args[1]['metadata_update']['intune_app_id'] == 'app-123'

        # Verify result
        assert result['intune_app_id'] == 'app-123'
        assert result['completed'] is True

    @patch('autopackager.utils.azure_validator.AzureValidator')
    @patch('autopackager.agents.deployment.DeploymentAgent')
    @patch('autopackager.orchestration.tasks.OrchestrationEngine')
    def test_deployment_task_retry_on_failure(self, mock_engine_class, mock_agent_class, mock_validator_class):
        """Test deployment task retries on failure"""
        mock_engine = Mock()
        mock_engine_class.return_value = mock_engine
        mock_engine.can_retry_job.return_value = True
        mock_engine.increment_retry_count.return_value = 1
        mock_engine.retry_delay = 60

        mock_job = Mock(spec=Job)
        mock_engine.get_job.return_value = mock_job

        mock_agent = Mock()
        mock_agent_class.return_value = mock_agent
        mock_agent.deploy.side_effect = Exception('Deployment failed')

        with pytest.raises(Exception):
            deployment_task.apply(kwargs={
                'previous_result': {'job_id': 1},
                'job_id': 1
            }).get()

        # Verify retry count incremented (may be called multiple times due to Celery retries)
        assert mock_engine.increment_retry_count.called
        assert mock_engine.increment_retry_count.call_args[0][0] == 1

    @patch('autopackager.orchestration.tasks.OrchestrationEngine')
    def test_deployment_task_skips_if_completed(self, mock_engine_class):
        """Test deployment task skips if previous task marked as completed"""
        mock_engine = Mock()
        mock_engine_class.return_value = mock_engine

        previous_result = {'completed': True}

        result = deployment_task.apply(kwargs={
            'previous_result': previous_result,
            'job_id': 1
        }).get()

        mock_engine.update_job_state.assert_not_called()
        assert result == previous_result

    @patch('autopackager.utils.azure_validator.AzureValidator')
    @patch('autopackager.agents.deployment.DeploymentAgent')
    @patch('autopackager.orchestration.tasks.OrchestrationEngine')
    def test_deployment_task_handles_http_error(self, mock_engine_class, mock_agent_class, mock_validator_class):
        """Test deployment task handles HTTP errors gracefully"""
        mock_engine = Mock()
        mock_engine_class.return_value = mock_engine
        mock_engine.can_retry_job.return_value = False

        mock_job = Mock(spec=Job)
        mock_engine.get_job.return_value = mock_job

        # Create mock HTTP error
        mock_response = Mock()
        mock_response.status_code = 403
        mock_response.json.return_value = {'error': 'Forbidden'}

        mock_error = Exception('HTTP error')
        mock_error.response = mock_response

        mock_agent = Mock()
        mock_agent_class.return_value = mock_agent
        mock_agent.deploy.side_effect = mock_error

        with pytest.raises(Exception):
            deployment_task.apply(kwargs={
                'previous_result': {'job_id': 1},
                'job_id': 1
            }).get()

        # Verify error message includes HTTP details
        mock_engine.mark_job_failed.assert_called_once()
        error_msg = mock_engine.mark_job_failed.call_args[0][1]
        # Cleanly formatted (no raw {'error': ...} dict); surfaces the 403 as a
        # missing-permission message via format_graph_error.
        assert '403' in error_msg
        assert 'permission' in error_msg.lower()
        assert '{' not in error_msg


class TestPollDeploymentStatus:
    """Tests for poll_deployment_status Celery task"""

    @patch('autopackager.agents.deployment.DeploymentAgent')
    def test_poll_deployment_status_success(self, mock_agent_class):
        """Test successful deployment status polling"""
        mock_agent = Mock()
        mock_agent_class.return_value = mock_agent
        mock_agent.check_all_deployments.return_value = {
            'total_checked': 5,
            'successful_updates': 3,
            'failed_updates': 1,
            'summary': {
                'total_installed': 100,
                'total_failed': 5,
                'total_pending': 20,
                'total_not_applicable': 10
            }
        }

        result = poll_deployment_status.apply().get()

        # Verify agent called
        mock_agent.check_all_deployments.assert_called_once()

        # Verify result
        assert result['total_checked'] == 5
        assert result['successful_updates'] == 3
        assert result['summary']['total_installed'] == 100

    @patch('autopackager.agents.deployment.DeploymentAgent')
    def test_poll_deployment_status_retry_on_failure(self, mock_agent_class):
        """Test polling retries on failure"""
        mock_agent = Mock()
        mock_agent_class.return_value = mock_agent
        mock_agent.check_all_deployments.side_effect = Exception('Polling failed')

        with pytest.raises(Exception):
            poll_deployment_status.apply().get()


class TestContinuousCatalogDiscovery:
    """Tests for continuous_catalog_discovery Celery task"""

    @patch('autopackager.orchestration.tasks.create_packaging_job')
    @patch('autopackager.utils.database.db_session_scope')
    @patch('autopackager.agents.discovery.DiscoveryAgent')
    @patch('autopackager.utils.config.get_config')
    def test_continuous_discovery_disabled(
        self, mock_get_config, mock_agent_class, mock_db_scope, mock_create_job
    ):
        """Test continuous discovery when disabled in config"""
        mock_get_config.return_value = {
            'discovery_schedule': {'enabled': False}
        }

        result = continuous_catalog_discovery.apply().get()

        assert result['status'] == 'disabled'
        # Verify no discovery run
        mock_agent_class.assert_not_called()

    @patch('autopackager.orchestration.tasks.create_packaging_job')
    @patch('autopackager.utils.database.db_session_scope')
    @patch('autopackager.agents.discovery.DiscoveryAgent')
    @patch('autopackager.utils.config.get_config')
    def test_continuous_discovery_no_monitored_models(
        self, mock_get_config, mock_agent_class, mock_db_scope, mock_create_job
    ):
        """Test continuous discovery with no monitored models"""
        mock_get_config.return_value = {
            'discovery_schedule': {
                'enabled': True,
                'monitored_models': []
            }
        }

        # Mock database session
        mock_session = MagicMock()
        mock_db_scope.return_value.__enter__ = Mock(return_value=mock_session)
        mock_db_scope.return_value.__exit__ = Mock(return_value=None)

        result = continuous_catalog_discovery.apply().get()

        assert result['status'] == 'no_models_configured'
        assert 'run_id' in result

    @patch('autopackager.orchestration.tasks.create_packaging_job')
    @patch('autopackager.utils.database.db_session_scope')
    @patch('autopackager.agents.discovery.DiscoveryAgent')
    @patch('autopackager.utils.config.get_config')
    def test_continuous_discovery_finds_updates(
        self, mock_get_config, mock_agent_class, mock_db_scope, mock_create_job
    ):
        """Test continuous discovery finds and creates jobs for updates"""
        mock_get_config.return_value = {
            'discovery_schedule': {
                'enabled': True,
                'monitored_models': [
                    {
                        'vendor': 'dell',
                        'model': 'Latitude 7490',
                        'driver_type': 'chipset',
                        'current_version': '1.0.0'
                    }
                ]
            }
        }

        # Mock discovery agent
        mock_agent = Mock()
        mock_agent_class.return_value = mock_agent
        mock_agent.discover.return_value = {
            'update_available': True,
            'latest_version': '2.0.0',
            'download_url': 'https://example.com/driver.exe',
            'release_notes': 'Updates'
        }

        # Mock database session
        mock_session = MagicMock()
        mock_query = Mock()
        mock_query.filter.return_value.first.return_value = None  # No existing job
        mock_session.query.return_value = mock_query
        mock_db_scope.return_value.__enter__ = Mock(return_value=mock_session)
        mock_db_scope.return_value.__exit__ = Mock(return_value=None)

        result = continuous_catalog_discovery.apply().get()

        # Verify discovery agent called
        mock_agent.discover.assert_called_once()

        # Verify job created
        mock_create_job.delay.assert_called_once()
        call_kwargs = mock_create_job.delay.call_args[1]
        assert call_kwargs['vendor'] == 'dell'
        assert call_kwargs['hardware_model'] == 'Latitude 7490'
        assert call_kwargs['metadata']['target_version'] == '2.0.0'

        # Verify result
        assert result['catalogs_scanned'] == 1
        assert result['new_versions_found'] == 1
        assert result['jobs_created'] == 1

    @patch('autopackager.orchestration.tasks.create_packaging_job')
    @patch('autopackager.utils.database.db_session_scope')
    @patch('autopackager.agents.discovery.DiscoveryAgent')
    @patch('autopackager.utils.config.get_config')
    def test_continuous_discovery_skips_duplicate_jobs(
        self, mock_get_config, mock_agent_class, mock_db_scope, mock_create_job
    ):
        """Test continuous discovery skips creating duplicate jobs"""
        mock_get_config.return_value = {
            'discovery_schedule': {
                'enabled': True,
                'monitored_models': [
                    {
                        'vendor': 'dell',
                        'model': 'Latitude 7490',
                        'driver_type': 'chipset',
                        'current_version': '1.0.0'
                    }
                ]
            }
        }

        mock_agent = Mock()
        mock_agent_class.return_value = mock_agent
        mock_agent.discover.return_value = {
            'update_available': True,
            'latest_version': '2.0.0',
            'download_url': 'https://example.com/driver.exe'
        }

        # Mock existing job
        mock_existing_job = Mock(spec=Job)
        mock_existing_job.id = 999

        mock_session = MagicMock()
        mock_query = Mock()
        mock_query.filter.return_value.first.return_value = mock_existing_job
        mock_session.query.return_value = mock_query
        mock_db_scope.return_value.__enter__ = Mock(return_value=mock_session)
        mock_db_scope.return_value.__exit__ = Mock(return_value=None)

        result = continuous_catalog_discovery.apply().get()

        # Verify no job created
        mock_create_job.delay.assert_not_called()

        # Verify stats
        assert result['new_versions_found'] == 1
        assert result['jobs_created'] == 0

    @patch('autopackager.orchestration.tasks.create_packaging_job')
    @patch('autopackager.utils.database.db_session_scope')
    @patch('autopackager.agents.discovery.DiscoveryAgent')
    @patch('autopackager.utils.config.get_config')
    def test_continuous_discovery_handles_discovery_errors(
        self, mock_get_config, mock_agent_class, mock_db_scope, mock_create_job
    ):
        """Test continuous discovery handles errors gracefully and continues"""
        mock_get_config.return_value = {
            'discovery_schedule': {
                'enabled': True,
                'monitored_models': [
                    {
                        'vendor': 'dell',
                        'model': 'Model1'
                    },
                    {
                        'vendor': 'hp',
                        'model': 'Model2'
                    }
                ]
            }
        }

        mock_agent = Mock()
        mock_agent_class.return_value = mock_agent
        # First call fails, second succeeds
        mock_agent.discover.side_effect = [
            Exception('Discovery failed'),
            {'update_available': False}
        ]

        mock_session = MagicMock()
        mock_db_scope.return_value.__enter__ = Mock(return_value=mock_session)
        mock_db_scope.return_value.__exit__ = Mock(return_value=None)

        result = continuous_catalog_discovery.apply().get()

        # Verify second model was still checked
        assert mock_agent.discover.call_count == 2
        assert result['catalogs_scanned'] == 1  # Only successful one counted

    @patch('autopackager.utils.database.db_session_scope')
    @patch('autopackager.utils.config.get_config')
    def test_continuous_discovery_retries_on_exception(
        self, mock_get_config, mock_db_scope
    ):
        """Test continuous discovery retries on critical exception"""
        mock_get_config.side_effect = Exception('Config load failed')

        mock_session = MagicMock()
        mock_db_scope.return_value.__enter__ = Mock(return_value=mock_session)
        mock_db_scope.return_value.__exit__ = Mock(return_value=None)

        with pytest.raises(Exception):
            continuous_catalog_discovery.apply().get()


class TestTaskChaining:
    """Tests for Celery task chaining behavior"""

    @patch('autopackager.utils.azure_validator.AzureValidator')
    @patch('autopackager.agents.deployment.DeploymentAgent')
    @patch('autopackager.agents.testing.TestingAgent')
    @patch('autopackager.agents.packaging.PackagingAgent')
    @patch('autopackager.agents.discovery.DiscoveryAgent')
    @patch('autopackager.orchestration.tasks.OrchestrationEngine')
    def test_full_pipeline_success(
        self, mock_engine_class, mock_discovery_class, mock_packaging_class,
        mock_testing_class, mock_deployment_class, mock_validator_class
    ):
        """Test full pipeline execution from discovery to deployment"""
        # Setup engine
        mock_engine = Mock()
        mock_engine_class.return_value = mock_engine

        mock_job = Mock(spec=Job)
        mock_job.id = 1
        mock_engine.get_job.return_value = mock_job

        # Setup agents
        mock_discovery = Mock()
        mock_discovery_class.return_value = mock_discovery
        mock_discovery.discover.return_value = {
            'update_available': True,
            'latest_version': '2.0.0',
            'download_url': 'https://example.com/driver.exe'
        }

        mock_packaging = Mock()
        mock_packaging_class.return_value = mock_packaging
        mock_packaging.package.return_value = {
            'package_id': 'pkg-123',
            'intunewin_path': '/path/to/package.intunewin'
        }

        mock_testing = Mock()
        mock_testing_class.return_value = mock_testing
        mock_testing.test.return_value = {'test_passed': True}

        mock_deployment = Mock()
        mock_deployment_class.return_value = mock_deployment
        mock_deployment.deploy.return_value = {
            'intune_app_id': 'app-123',
            'status': 'deployed'
        }

        # Execute pipeline
        discovery_result = discovery_task.apply(kwargs={'job_id': 1}).get()
        packaging_result = packaging_task.apply(kwargs={
            'previous_result': discovery_result,
            'job_id': 1
        }).get()
        testing_result = testing_task.apply(kwargs={
            'previous_result': packaging_result,
            'job_id': 1
        }).get()
        deployment_result = deployment_task.apply(kwargs={
            'previous_result': testing_result,
            'job_id': 1
        }).get()

        # Verify all phases executed
        assert discovery_result['update_available'] is True
        assert packaging_result['package_id'] == 'pkg-123'
        assert testing_result['test_passed'] is True
        assert deployment_result['intune_app_id'] == 'app-123'
        assert deployment_result['completed'] is True

    @patch('autopackager.agents.discovery.DiscoveryAgent')
    @patch('autopackager.orchestration.tasks.OrchestrationEngine')
    def test_pipeline_short_circuits_on_no_update(
        self, mock_engine_class, mock_discovery_class
    ):
        """Test pipeline short-circuits when no update is needed"""
        mock_engine = Mock()
        mock_engine_class.return_value = mock_engine

        mock_job = Mock(spec=Job)
        mock_engine.get_job.return_value = mock_job

        mock_discovery = Mock()
        mock_discovery_class.return_value = mock_discovery
        mock_discovery.discover.return_value = {'update_available': False}

        # Execute discovery
        discovery_result = discovery_task.apply(kwargs={'job_id': 1}).get()

        # Verify job completed
        assert discovery_result['completed'] is True
        mock_engine.mark_job_completed.assert_called_once()

        # Execute packaging with completed result
        packaging_result = packaging_task.apply(kwargs={
            'previous_result': discovery_result,
            'job_id': 1
        }).get()

        # Verify packaging skipped
        assert packaging_result == discovery_result

        # Verify state not updated (no PACKAGING state)
        update_calls = [call for call in mock_engine.update_job_state.call_args_list
                       if call[0][1] == JobState.PACKAGING]
        assert len(update_calls) == 0
