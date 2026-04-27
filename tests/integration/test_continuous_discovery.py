"""Integration tests for continuous catalog discovery task"""

import pytest
from unittest.mock import Mock, patch, MagicMock, call
from datetime import datetime

from autopackager.orchestration.tasks import continuous_catalog_discovery
from autopackager.models.discovery_run import DiscoveryRun
from autopackager.models.job import Job, JobState, JobType


# Mark all tests in this module as integration tests
pytestmark = pytest.mark.integration


class TestContinuousCatalogDiscovery:
    """Tests for continuous_catalog_discovery Celery task"""

    @patch('autopackager.orchestration.tasks.create_packaging_job')
    @patch('autopackager.utils.config.get_config')
    @patch('autopackager.utils.database.db_session_scope')
    @patch('autopackager.agents.discovery.DiscoveryAgent')
    def test_discovery_finds_updates(self, mock_agent_class, mock_db_session, mock_config, mock_create_job):
        """Test successful discovery with updates found"""
        # Setup config
        mock_config.return_value = {
            'discovery_schedule': {
                'enabled': True,
                'monitored_models': [
                    {
                        'vendor': 'Dell',
                        'model': 'Latitude 7490',
                        'driver_type': 'Chipset',
                        'current_version': '1.0.0'
                    },
                    {
                        'vendor': 'HP',
                        'model': 'EliteBook 840 G8',
                        'driver_type': 'Audio',
                        'current_version': '2.0.0'
                    }
                ]
            }
        }

        # Setup database session
        mock_session = MagicMock()
        mock_db_session.return_value.__enter__.return_value = mock_session

        # Mock DiscoveryRun - set ID when flush is called
        run_id_counter = [123]  # Use list to allow modification in closure
        def mock_add(obj):
            if isinstance(obj, DiscoveryRun):
                obj.id = run_id_counter[0]
        mock_session.add.side_effect = mock_add
        mock_session.flush.return_value = None

        # Mock query for finding existing jobs and updating run
        mock_run = Mock(spec=DiscoveryRun)
        mock_run.id = 123
        mock_session.query.return_value.filter.return_value.first.side_effect = [
            None,      # No existing job for Dell
            mock_run   # Final call for updating run
        ]

        # Setup discovery agent
        mock_agent = Mock()
        mock_agent_class.return_value = mock_agent

        # First model has update, second doesn't
        mock_agent.discover.side_effect = [
            {
                'update_available': True,
                'latest_version': '1.5.0',
                'download_url': 'https://example.com/driver1.exe',
                'release_notes': 'Bug fixes'
            },
            {
                'update_available': False
            }
        ]

        # Execute task
        result = continuous_catalog_discovery.apply().get()

        # Verify results
        assert result['run_id'] == 123
        assert result['catalogs_scanned'] == 2
        assert result['new_versions_found'] == 1
        assert result['jobs_created'] == 1
        assert result['oem_results']['Dell']['scanned'] == 1
        assert result['oem_results']['Dell']['updates_found'] == 1
        assert result['oem_results']['HP']['scanned'] == 1
        assert result['oem_results']['HP']['updates_found'] == 0

        # Verify discovery agent was called for both models
        assert mock_agent.discover.call_count == 2

        # Verify create_packaging_job was called once
        mock_create_job.delay.assert_called_once()
        call_kwargs = mock_create_job.delay.call_args[1]
        assert call_kwargs['vendor'] == 'Dell'
        assert call_kwargs['hardware_model'] == 'Latitude 7490'
        assert call_kwargs['metadata']['target_version'] == '1.5.0'
        assert call_kwargs['metadata']['discovered_by'] == 'continuous_catalog_discovery'

        # Verify discovery run was updated with results
        assert mock_run.completed_at is not None
        assert mock_run.catalogs_scanned == 2
        assert mock_run.new_versions_found == 1
        assert mock_run.jobs_created == 1

    @patch('autopackager.orchestration.tasks.create_packaging_job')
    @patch('autopackager.utils.config.get_config')
    @patch('autopackager.utils.database.db_session_scope')
    @patch('autopackager.agents.discovery.DiscoveryAgent')
    def test_discovery_skips_duplicates(self, mock_agent_class, mock_db_session, mock_config, mock_create_job):
        """Test that discovery skips duplicate jobs"""
        # Setup config
        mock_config.return_value = {
            'discovery_schedule': {
                'enabled': True,
                'monitored_models': [
                    {
                        'vendor': 'Dell',
                        'model': 'Latitude 7490',
                        'driver_type': 'Chipset',
                        'current_version': '1.0.0'
                    }
                ]
            }
        }

        # Setup database session
        mock_session = MagicMock()
        mock_db_session.return_value.__enter__.return_value = mock_session

        # Mock DiscoveryRun
        mock_run = Mock(spec=DiscoveryRun)
        mock_run.id = 456

        # Mock existing job
        existing_job = Mock(spec=Job)
        existing_job.id = 789
        existing_job.vendor = 'Dell'
        existing_job.hardware_model = 'Latitude 7490'
        existing_job.target_version = '1.5.0'
        existing_job.state = JobState.PENDING

        mock_session.query.return_value.filter.return_value.first.side_effect = [
            mock_run,        # First call for creating run
            existing_job,    # Existing job found
            mock_run         # Final call for updating run
        ]

        # Setup discovery agent
        mock_agent = Mock()
        mock_agent_class.return_value = mock_agent
        mock_agent.discover.return_value = {
            'update_available': True,
            'latest_version': '1.5.0',
            'download_url': 'https://example.com/driver.exe',
            'release_notes': 'Bug fixes'
        }

        # Execute task
        result = continuous_catalog_discovery.apply().get()

        # Verify no job was created
        mock_create_job.delay.assert_not_called()
        assert result['jobs_created'] == 0
        assert result['new_versions_found'] == 1

    @patch('autopackager.utils.config.get_config')
    @patch('autopackager.utils.database.db_session_scope')
    def test_discovery_disabled(self, mock_db_session, mock_config):
        """Test discovery when disabled in config"""
        # Setup config with discovery disabled
        mock_config.return_value = {
            'discovery_schedule': {
                'enabled': False
            }
        }

        # Execute task
        result = continuous_catalog_discovery.apply().get()

        # Verify disabled status
        assert result['status'] == 'disabled'

        # Verify no database operations occurred
        mock_db_session.assert_not_called()

    @patch('autopackager.utils.config.get_config')
    @patch('autopackager.utils.database.db_session_scope')
    def test_discovery_no_models_configured(self, mock_db_session, mock_config):
        """Test discovery with no monitored models"""
        # Setup config with empty monitored_models
        mock_config.return_value = {
            'discovery_schedule': {
                'enabled': True,
                'monitored_models': []
            }
        }

        # Setup database session
        mock_session = MagicMock()
        mock_db_session.return_value.__enter__.return_value = mock_session

        # Mock DiscoveryRun - set ID when flush is called
        def mock_add(obj):
            if isinstance(obj, DiscoveryRun):
                obj.id = 999
        mock_session.add.side_effect = mock_add
        mock_session.flush.return_value = None

        # Mock query for updating run
        mock_run = Mock(spec=DiscoveryRun)
        mock_run.id = 999
        mock_session.query.return_value.filter.return_value.first.return_value = mock_run

        # Execute task
        result = continuous_catalog_discovery.apply().get()

        # Verify status
        assert result['status'] == 'no_models_configured'
        assert result['run_id'] == 999

        # Verify discovery run was updated with error message
        assert mock_run.completed_at is not None
        assert mock_run.error_message == "No monitored_models configured"

    @patch('autopackager.orchestration.tasks.create_packaging_job')
    @patch('autopackager.utils.config.get_config')
    @patch('autopackager.utils.database.db_session_scope')
    @patch('autopackager.agents.discovery.DiscoveryAgent')
    def test_discovery_continues_on_model_error(self, mock_agent_class, mock_db_session, mock_config, mock_create_job):
        """Test that discovery continues when one model fails"""
        # Setup config with two models
        mock_config.return_value = {
            'discovery_schedule': {
                'enabled': True,
                'monitored_models': [
                    {
                        'vendor': 'Dell',
                        'model': 'Latitude 7490',
                        'driver_type': 'Chipset'
                    },
                    {
                        'vendor': 'HP',
                        'model': 'EliteBook 840 G8',
                        'driver_type': 'Audio'
                    }
                ]
            }
        }

        # Setup database session
        mock_session = MagicMock()
        mock_db_session.return_value.__enter__.return_value = mock_session

        # Mock DiscoveryRun - set ID when flush is called
        def mock_add(obj):
            if isinstance(obj, DiscoveryRun):
                obj.id = 111
        mock_session.add.side_effect = mock_add
        mock_session.flush.return_value = None

        # Mock query for finding existing jobs and updating run
        mock_run = Mock(spec=DiscoveryRun)
        mock_run.id = 111
        mock_session.query.return_value.filter.return_value.first.side_effect = [
            None,      # No existing job for HP
            mock_run   # Updating run
        ]

        # Setup discovery agent - first model fails, second succeeds
        mock_agent = Mock()
        mock_agent_class.return_value = mock_agent
        mock_agent.discover.side_effect = [
            Exception("Dell catalog unavailable"),
            {
                'update_available': True,
                'latest_version': '2.5.0',
                'download_url': 'https://example.com/hp-driver.exe',
                'release_notes': 'New features'
            }
        ]

        # Execute task
        result = continuous_catalog_discovery.apply().get()

        # Verify that second model was processed despite first failing
        assert result['catalogs_scanned'] == 1
        assert result['new_versions_found'] == 1
        assert result['jobs_created'] == 1
        assert result['oem_results']['HP']['scanned'] == 1
        assert result['oem_results']['HP']['updates_found'] == 1

        # Verify job was created for HP
        mock_create_job.delay.assert_called_once()
        call_kwargs = mock_create_job.delay.call_args[1]
        assert call_kwargs['vendor'] == 'HP'

    @patch('autopackager.utils.config.get_config')
    @patch('autopackager.utils.database.db_session_scope')
    def test_discovery_handles_invalid_model_config(self, mock_db_session, mock_config):
        """Test discovery skips invalid model configurations"""
        # Setup config with invalid models
        mock_config.return_value = {
            'discovery_schedule': {
                'enabled': True,
                'monitored_models': [
                    {
                        'vendor': 'Dell'
                        # Missing 'model' field
                    },
                    {
                        'model': 'Latitude 7490'
                        # Missing 'vendor' field
                    },
                    {
                        'vendor': 'HP',
                        'model': 'EliteBook 840 G8'
                        # Valid config
                    }
                ]
            }
        }

        # Setup database session
        mock_session = MagicMock()
        mock_db_session.return_value.__enter__.return_value = mock_session

        # Mock DiscoveryRun
        mock_run = Mock(spec=DiscoveryRun)
        mock_run.id = 222
        mock_session.query.return_value.filter.return_value.first.return_value = mock_run

        # Setup discovery agent - should only be called once for valid config
        with patch('autopackager.agents.discovery.DiscoveryAgent') as mock_agent_class:
            mock_agent = Mock()
            mock_agent_class.return_value = mock_agent
            mock_agent.discover.return_value = {
                'update_available': False
            }

            # Execute task
            result = continuous_catalog_discovery.apply().get()

            # Verify only valid model was scanned
            assert mock_agent.discover.call_count == 1
            assert result['catalogs_scanned'] == 1
            assert result['oem_results']['HP']['scanned'] == 1

    @patch('autopackager.utils.config.get_config')
    @patch('autopackager.utils.database.db_session_scope')
    @patch('autopackager.agents.discovery.DiscoveryAgent')
    def test_discovery_handles_task_failure(self, mock_agent_class, mock_db_session, mock_config):
        """Test error handling when discovery task fails"""
        # Setup config
        mock_config.return_value = {
            'discovery_schedule': {
                'enabled': True,
                'monitored_models': [
                    {
                        'vendor': 'Dell',
                        'model': 'Latitude 7490'
                    }
                ]
            }
        }

        # Setup database session that raises exception
        mock_db_session.side_effect = Exception("Database connection failed")

        # Execute task and expect retry exception
        with pytest.raises(Exception) as exc_info:
            continuous_catalog_discovery.apply().get()

        # Task should retry on failure
        assert "Database connection failed" in str(exc_info.value)

    @patch('autopackager.orchestration.tasks.create_packaging_job')
    @patch('autopackager.utils.config.get_config')
    @patch('autopackager.utils.database.db_session_scope')
    @patch('autopackager.agents.discovery.DiscoveryAgent')
    def test_discovery_tracks_multiple_oems(self, mock_agent_class, mock_db_session, mock_config, mock_create_job):
        """Test that OEM results are tracked correctly for multiple vendors"""
        # Setup config with multiple models from same vendor
        mock_config.return_value = {
            'discovery_schedule': {
                'enabled': True,
                'monitored_models': [
                    {
                        'vendor': 'Dell',
                        'model': 'Latitude 7490',
                        'driver_type': 'Chipset'
                    },
                    {
                        'vendor': 'Dell',
                        'model': 'OptiPlex 7090',
                        'driver_type': 'Audio'
                    },
                    {
                        'vendor': 'HP',
                        'model': 'EliteBook 840 G8',
                        'driver_type': 'Network'
                    }
                ]
            }
        }

        # Setup database session
        mock_session = MagicMock()
        mock_db_session.return_value.__enter__.return_value = mock_session

        # Mock DiscoveryRun - set ID when flush is called
        def mock_add(obj):
            if isinstance(obj, DiscoveryRun):
                obj.id = 333
        mock_session.add.side_effect = mock_add
        mock_session.flush.return_value = None

        # Mock query for finding existing jobs and updating run
        mock_run = Mock(spec=DiscoveryRun)
        mock_run.id = 333
        mock_session.query.return_value.filter.return_value.first.side_effect = [
            None,      # No existing job for Dell model 1
            None,      # No existing job for Dell model 2
            mock_run   # Updating run
        ]

        # Setup discovery agent - Dell models have updates, HP doesn't
        mock_agent = Mock()
        mock_agent_class.return_value = mock_agent
        mock_agent.discover.side_effect = [
            {'update_available': True, 'latest_version': '1.5.0', 'download_url': 'url1', 'release_notes': 'notes1'},
            {'update_available': True, 'latest_version': '2.5.0', 'download_url': 'url2', 'release_notes': 'notes2'},
            {'update_available': False}
        ]

        # Execute task
        result = continuous_catalog_discovery.apply().get()

        # Verify OEM tracking
        assert result['oem_results']['Dell']['scanned'] == 2
        assert result['oem_results']['Dell']['updates_found'] == 2
        assert result['oem_results']['HP']['scanned'] == 1
        assert result['oem_results']['HP']['updates_found'] == 0
        assert result['catalogs_scanned'] == 3
        assert result['new_versions_found'] == 2
        assert result['jobs_created'] == 2
