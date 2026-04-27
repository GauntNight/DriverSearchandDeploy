"""Unit tests for Web API endpoints"""

import pytest
from unittest.mock import Mock, patch, MagicMock, mock_open
from datetime import datetime, timedelta
from contextlib import contextmanager
from fastapi.testclient import TestClient


# ============================================================================
# Module-level imports and setup
# ============================================================================

# Mock configuration before importing the API
@pytest.fixture(scope='module', autouse=True)
def mock_config_and_db():
    """Mock configuration and database for the entire test module"""
    mock_config = {
        'dashboard': {},
        'jobs': {'max_retries': 3, 'retry_delay_seconds': 60, 'concurrent_jobs': 5},
        'redis': {'host': 'localhost', 'port': 6379, 'db': 0}
    }

    @contextmanager
    def mock_db_session_scope():
        yield Mock()

    with patch('autopackager.utils.config.get_config', return_value=mock_config), \
         patch('autopackager.utils.database.db_session_scope', mock_db_session_scope), \
         patch('autopackager.utils.database.init_db'), \
         patch('autopackager.web.api.OrchestrationEngine'), \
         patch('autopackager.web.api.DashboardService'):
        yield


@pytest.fixture
def test_client():
    """Get a test client"""
    from autopackager.web import api
    return TestClient(api.app)


# ============================================================================
# Health Check Endpoint Tests
# ============================================================================

class TestHealthEndpoint:
    """Test cases for health check endpoint"""

    def test_health_check_returns_healthy_status(self, test_client):
        """Test health check endpoint returns healthy status"""
        response = test_client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data['status'] == 'healthy'
        assert data['service'] == 'autopackager-dashboard'


# ============================================================================
# Jobs Endpoint Tests
# ============================================================================

class TestJobsEndpoints:
    """Test cases for jobs API endpoints"""

    def test_list_jobs_empty(self, test_client):
        """Test listing jobs when none exist"""
        with patch('autopackager.web.api.engine') as mock_engine:
            mock_engine.get_all_jobs.return_value = []

            response = test_client.get("/api/jobs")

            assert response.status_code == 200
            data = response.json()
            assert data['jobs'] == []
            assert data['count'] == 0
            assert data['filter'] is None
            mock_engine.get_all_jobs.assert_called_once_with(limit=100)

    def test_list_jobs_with_results(self, test_client):
        """Test listing jobs with results"""
        with patch('autopackager.web.api.engine') as mock_engine:
            mock_job = Mock()
            mock_job.to_dict.return_value = {
                'id': 1,
                'software_title': 'Intel Chipset Driver',
                'vendor': 'Dell'
            }
            mock_engine.get_all_jobs.return_value = [mock_job]

            response = test_client.get("/api/jobs")

            assert response.status_code == 200
            data = response.json()
            assert len(data['jobs']) == 1
            assert data['count'] == 1
            assert data['jobs'][0]['software_title'] == 'Intel Chipset Driver'

    def test_list_jobs_with_state_filter(self, test_client):
        """Test listing jobs filtered by state"""
        with patch('autopackager.web.api.engine') as mock_engine:
            mock_job = Mock()
            mock_job.to_dict.return_value = {'id': 1, 'state': 'pending'}
            mock_engine.get_jobs_by_state.return_value = [mock_job]

            response = test_client.get("/api/jobs?state=pending")

            assert response.status_code == 200
            data = response.json()
            assert len(data['jobs']) == 1
            assert data['filter'] == {'state': 'pending'}

    def test_list_jobs_with_invalid_state(self, test_client):
        """Test listing jobs with invalid state filter"""
        response = test_client.get("/api/jobs?state=invalid_state")

        assert response.status_code == 400
        data = response.json()
        assert 'Invalid state' in data['detail']

    def test_list_jobs_with_custom_limit(self, test_client):
        """Test listing jobs with custom limit"""
        with patch('autopackager.web.api.engine') as mock_engine:
            mock_engine.get_all_jobs.return_value = []

            response = test_client.get("/api/jobs?limit=50")

            assert response.status_code == 200
            mock_engine.get_all_jobs.assert_called_once_with(limit=50)

    def test_list_jobs_with_limit_validation(self, test_client):
        """Test listing jobs with limit outside valid range"""
        # Test limit too large
        response = test_client.get("/api/jobs?limit=2000")
        assert response.status_code == 422

        # Test limit too small
        response = test_client.get("/api/jobs?limit=0")
        assert response.status_code == 422

    def test_get_job_by_id_found(self, test_client):
        """Test getting a specific job that exists"""
        with patch('autopackager.web.api.engine') as mock_engine:
            mock_job = Mock()
            mock_job.to_dict.return_value = {'id': 1, 'software_title': 'Intel Chipset Driver'}
            mock_engine.get_job.return_value = mock_job

            response = test_client.get("/api/jobs/1")

            assert response.status_code == 200
            data = response.json()
            assert data['id'] == 1
            assert data['software_title'] == 'Intel Chipset Driver'

    def test_get_job_by_id_not_found(self, test_client):
        """Test getting a specific job that doesn't exist"""
        with patch('autopackager.web.api.engine') as mock_engine:
            mock_engine.get_job.return_value = None

            response = test_client.get("/api/jobs/999")

            assert response.status_code == 404
            data = response.json()
            assert 'not found' in data['detail'].lower()

    def test_list_jobs_handles_exception(self, test_client):
        """Test that list jobs handles exceptions gracefully"""
        with patch('autopackager.web.api.engine') as mock_engine:
            mock_engine.get_all_jobs.side_effect = Exception("Database error")

            response = test_client.get("/api/jobs")

            assert response.status_code == 500
            data = response.json()
            assert 'Internal server error' in data['detail']

    def test_get_job_handles_exception(self, test_client):
        """Test that get job handles exceptions gracefully"""
        with patch('autopackager.web.api.engine') as mock_engine:
            mock_engine.get_job.side_effect = Exception("Database error")

            response = test_client.get("/api/jobs/1")

            assert response.status_code == 500
            data = response.json()
            assert 'Internal server error' in data['detail']


# ============================================================================
# Deployments Endpoint Tests
# ============================================================================

class TestDeploymentsEndpoints:
    """Test cases for deployments API endpoints"""

    def test_list_deployments_empty(self, test_client):
        """Test listing deployments when none exist"""
        with patch('autopackager.web.api.dashboard_service') as mock_service:
            mock_service.get_deployments.return_value = []

            response = test_client.get("/api/deployments")

            assert response.status_code == 200
            data = response.json()
            assert data['deployments'] == []
            assert data['count'] == 0
            assert data['filter'] is None

    def test_list_deployments_with_results(self, test_client):
        """Test listing deployments with results"""
        with patch('autopackager.web.api.dashboard_service') as mock_service:
            sample_deployment = {'id': 1, 'ring_name': 'Pilot Ring'}
            mock_service.get_deployments.return_value = [sample_deployment]

            response = test_client.get("/api/deployments")

            assert response.status_code == 200
            data = response.json()
            assert len(data['deployments']) == 1
            assert data['deployments'][0]['ring_name'] == 'Pilot Ring'

    def test_list_deployments_with_status_filter(self, test_client):
        """Test listing deployments filtered by status"""
        with patch('autopackager.web.api.dashboard_service') as mock_service:
            mock_service.get_deployments.return_value = [{'id': 1}]

            response = test_client.get("/api/deployments?status=pending")

            assert response.status_code == 200
            data = response.json()
            assert data['filter'] == {'status': 'pending'}

    def test_list_deployments_with_invalid_status(self, test_client):
        """Test listing deployments with invalid status filter"""
        response = test_client.get("/api/deployments?status=invalid_status")

        assert response.status_code == 400
        data = response.json()
        assert 'Invalid status' in data['detail']

    def test_list_deployments_handles_exception(self, test_client):
        """Test that list deployments handles exceptions gracefully"""
        with patch('autopackager.web.api.dashboard_service') as mock_service:
            mock_service.get_deployments.side_effect = Exception("Database error")

            response = test_client.get("/api/deployments")

            assert response.status_code == 500
            data = response.json()
            assert 'Internal server error' in data['detail']


# ============================================================================
# Deployment Rings Endpoint Tests
# ============================================================================

class TestDeploymentRingsEndpoint:
    """Test cases for deployment rings API endpoint"""

    def test_get_deployment_rings_empty(self, test_client):
        """Test getting deployment rings when none exist"""
        with patch('autopackager.web.api.dashboard_service') as mock_service:
            mock_service.get_deployment_ring_status.return_value = {
                'rings': [],
                'timestamp': datetime.utcnow().isoformat()
            }

            response = test_client.get("/api/deployments/rings")

            assert response.status_code == 200
            data = response.json()
            assert data['rings'] == []
            assert 'timestamp' in data

    def test_get_deployment_rings_with_data(self, test_client):
        """Test getting deployment rings with data"""
        with patch('autopackager.web.api.dashboard_service') as mock_service:
            ring_data = {
                'rings': [
                    {
                        'ring_id': 'pilot',
                        'ring_name': 'Pilot Ring',
                        'total_devices': 10,
                        'successful': 8
                    }
                ],
                'timestamp': datetime.utcnow().isoformat()
            }
            mock_service.get_deployment_ring_status.return_value = ring_data

            response = test_client.get("/api/deployments/rings")

            assert response.status_code == 200
            data = response.json()
            assert len(data['rings']) == 1
            assert data['rings'][0]['ring_id'] == 'pilot'

    def test_get_deployment_rings_handles_exception(self, test_client):
        """Test that deployment rings endpoint handles exceptions gracefully"""
        with patch('autopackager.web.api.dashboard_service') as mock_service:
            mock_service.get_deployment_ring_status.side_effect = Exception("Database error")

            response = test_client.get("/api/deployments/rings")

            assert response.status_code == 500
            data = response.json()
            assert 'Internal server error' in data['detail']


# ============================================================================
# Statistics Endpoint Tests
# ============================================================================

class TestStatisticsEndpoint:
    """Test cases for statistics API endpoint"""

    def test_get_statistics_empty(self, test_client):
        """Test getting statistics with no data"""
        with patch('autopackager.web.api.dashboard_service') as mock_service:
            mock_service.get_statistics.return_value = {
                'jobs': {'total': 0, 'by_state': {}, 'recent_24h': 0},
                'deployments': {'total': 0, 'successful': 0, 'failed': 0, 'in_progress': 0, 'recent_24h': 0},
                'packages': {'total': 0, 'tested': 0, 'deployed': 0},
                'discovery_runs': {
                    'total': 0,
                    'completed': 0,
                    'failed': 0,
                    'recent_24h': 0,
                    'total_catalogs_scanned': 0,
                    'total_versions_found': 0,
                    'total_jobs_created': 0
                },
                'timestamp': datetime.utcnow().isoformat()
            }

            response = test_client.get("/api/stats")

            assert response.status_code == 200
            data = response.json()
            assert data['jobs']['total'] == 0
            assert data['deployments']['total'] == 0
            assert data['discovery_runs']['total'] == 0

    def test_get_statistics_with_data(self, test_client):
        """Test getting statistics with data"""
        with patch('autopackager.web.api.dashboard_service') as mock_service:
            stats = {
                'jobs': {'total': 100, 'by_state': {'completed': 70}, 'recent_24h': 15},
                'deployments': {'total': 50, 'successful': 40, 'failed': 5, 'in_progress': 5, 'recent_24h': 10},
                'packages': {'total': 45, 'tested': 40, 'deployed': 35},
                'discovery_runs': {
                    'total': 25,
                    'completed': 23,
                    'failed': 2,
                    'recent_24h': 5,
                    'total_catalogs_scanned': 150,
                    'total_versions_found': 78,
                    'total_jobs_created': 42
                },
                'timestamp': datetime.utcnow().isoformat()
            }
            mock_service.get_statistics.return_value = stats

            response = test_client.get("/api/stats")

            assert response.status_code == 200
            data = response.json()
            assert data['jobs']['total'] == 100
            assert data['deployments']['successful'] == 40
            assert data['discovery_runs']['total'] == 25
            assert data['discovery_runs']['completed'] == 23
            assert data['discovery_runs']['total_catalogs_scanned'] == 150

    def test_get_statistics_handles_exception(self, test_client):
        """Test that statistics endpoint handles exceptions gracefully"""
        with patch('autopackager.web.api.dashboard_service') as mock_service:
            mock_service.get_statistics.side_effect = Exception("Database error")

            response = test_client.get("/api/stats")

            assert response.status_code == 500
            data = response.json()
            assert 'Internal server error' in data['detail']


# ============================================================================
# Activity Endpoint Tests
# ============================================================================

class TestActivityEndpoint:
    """Test cases for activity API endpoint"""

    def test_get_recent_activity_empty(self, test_client):
        """Test getting recent activity when none exists"""
        with patch('autopackager.web.api.dashboard_service') as mock_service:
            mock_service.get_recent_activity.return_value = []

            response = test_client.get("/api/activity")

            assert response.status_code == 200
            data = response.json()
            assert data['activity'] == []
            assert data['count'] == 0

    def test_get_recent_activity_with_data(self, test_client):
        """Test getting recent activity with data"""
        with patch('autopackager.web.api.dashboard_service') as mock_service:
            activity_data = [
                {'type': 'job', 'id': 1, 'timestamp': datetime.utcnow().isoformat()},
                {'type': 'deployment', 'id': 1, 'timestamp': datetime.utcnow().isoformat()}
            ]
            mock_service.get_recent_activity.return_value = activity_data

            response = test_client.get("/api/activity")

            assert response.status_code == 200
            data = response.json()
            assert len(data['activity']) == 2
            assert data['count'] == 2

    def test_get_recent_activity_with_limit_validation(self, test_client):
        """Test getting recent activity with limit outside valid range"""
        # Test limit too large
        response = test_client.get("/api/activity?limit=1000")
        assert response.status_code == 422

        # Test limit too small
        response = test_client.get("/api/activity?limit=0")
        assert response.status_code == 422

    def test_get_recent_activity_handles_exception(self, test_client):
        """Test that activity endpoint handles exceptions gracefully"""
        with patch('autopackager.web.api.dashboard_service') as mock_service:
            mock_service.get_recent_activity.side_effect = Exception("Database error")

            response = test_client.get("/api/activity")

            assert response.status_code == 500
            data = response.json()
            assert 'Internal server error' in data['detail']


# ============================================================================
# Root Endpoint Tests
# ============================================================================

class TestRootEndpoint:
    """Test cases for root endpoint"""

    def test_root_endpoint_serves_dashboard(self, test_client):
        """Test root endpoint serves the dashboard HTML page"""
        response = test_client.get("/")

        assert response.status_code == 200
        # The actual index.html exists and should be served
        assert 'html' in response.text.lower()
