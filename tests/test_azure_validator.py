"""Unit tests for Azure Configuration Validator"""

import unittest
from unittest.mock import Mock, patch, MagicMock

from autopackager.utils.azure_validator import (
    AzureConfigurationError,
    AzureValidator,
    ValidationResult,
)


def _make_config(tenant_id='test-tenant', client_id='test-client', client_secret='test-secret',
                 deployment_rings=None):
    """Build a mock config dict."""
    if deployment_rings is None:
        deployment_rings = [
            {'ring_id': 0, 'name': 'Ring 0 - IT Pilot', 'entra_group_id': 'group-id-ring0'},
            {'ring_id': 1, 'name': 'Ring 1 - Early Adopters', 'entra_group_id': 'group-id-ring1'},
        ]
    return {
        'intune': {
            'tenant_id': tenant_id,
            'client_id': client_id,
            'client_secret': client_secret,
            'graph_endpoint': 'https://graph.microsoft.com',
            'graph_api_version': 'v1.0',
        },
        'deployment_rings': deployment_rings,
    }


def _build_validator(**kwargs):
    """Create an AzureValidator with mocked config."""
    config = _make_config(**kwargs)
    with patch('autopackager.utils.azure_validator.get_config', return_value=config):
        return AzureValidator()


class TestValidateConfig(unittest.TestCase):
    """Tests for validate_config()"""

    def test_validate_config_all_fields_present(self):
        """Valid config passes validation."""
        validator = _build_validator()
        result = validator.validate_config()
        self.assertTrue(result.passed)
        self.assertEqual(result.check_name, 'config')

    def test_validate_config_missing_tenant_id(self):
        """Missing tenant_id is detected."""
        validator = _build_validator(tenant_id='')
        result = validator.validate_config()
        self.assertFalse(result.passed)
        self.assertIn('tenant_id', result.message)

    def test_validate_config_missing_client_id(self):
        """Missing client_id is detected."""
        validator = _build_validator(client_id='')
        result = validator.validate_config()
        self.assertFalse(result.passed)
        self.assertIn('client_id', result.message)

    def test_validate_config_missing_client_secret(self):
        """Missing client_secret is detected."""
        validator = _build_validator(client_secret='')
        result = validator.validate_config()
        self.assertFalse(result.passed)
        self.assertIn('client_secret', result.message)

    def test_validate_config_placeholder_values(self):
        """Placeholder strings like '${AZURE_TENANT_ID}' or 'your_tenant_id_here' are detected."""
        validator = _build_validator(tenant_id='${AZURE_TENANT_ID}')
        result = validator.validate_config()
        self.assertFalse(result.passed)
        self.assertIn('tenant_id', result.message)

        # Also detect your_*_here pattern
        validator2 = _build_validator(tenant_id='your_tenant_id_here')
        result2 = validator2.validate_config()
        self.assertFalse(result2.passed)
        self.assertIn('tenant_id', result2.message)


class TestValidateAuthentication(unittest.TestCase):
    """Tests for validate_authentication()"""

    @patch('autopackager.utils.azure_validator.ConfidentialClientApplication')
    def test_validate_authentication_success(self, mock_msal_cls):
        """Mock MSAL returning access_token."""
        mock_app = Mock()
        mock_app.acquire_token_for_client.return_value = {'access_token': 'mock-token'}
        mock_msal_cls.return_value = mock_app

        validator = _build_validator()
        result = validator.validate_authentication()

        self.assertTrue(result.passed)
        self.assertEqual(validator._access_token, 'mock-token')

    @patch('autopackager.utils.azure_validator.ConfidentialClientApplication')
    def test_validate_authentication_invalid_secret(self, mock_msal_cls):
        """Mock MSAL returning error for invalid secret."""
        mock_app = Mock()
        mock_app.acquire_token_for_client.return_value = {
            'error': 'invalid_client',
            'error_description': 'Invalid client secret provided',
        }
        mock_msal_cls.return_value = mock_app

        validator = _build_validator()
        result = validator.validate_authentication()

        self.assertFalse(result.passed)
        self.assertIn('Invalid client secret', result.details)

    @patch('autopackager.utils.azure_validator.ConfidentialClientApplication')
    def test_validate_authentication_invalid_tenant(self, mock_msal_cls):
        """Mock MSAL tenant not found."""
        mock_app = Mock()
        mock_app.acquire_token_for_client.return_value = {
            'error': 'invalid_request',
            'error_description': 'Tenant not found',
        }
        mock_msal_cls.return_value = mock_app

        validator = _build_validator()
        result = validator.validate_authentication()

        self.assertFalse(result.passed)
        self.assertIn('Tenant not found', result.details)


class TestValidateGraphAccess(unittest.TestCase):
    """Tests for validate_graph_access()"""

    @patch('autopackager.utils.azure_validator.requests.get')
    def test_validate_graph_access_success(self, mock_get):
        """Mock requests.get returning 200."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_get.return_value = mock_response

        validator = _build_validator()
        validator._access_token = 'mock-token'
        result = validator.validate_graph_access()

        self.assertTrue(result.passed)

    @patch('autopackager.utils.azure_validator.requests.get')
    def test_validate_graph_access_forbidden(self, mock_get):
        """Mock 403 response."""
        mock_response = Mock()
        mock_response.status_code = 403
        mock_response.json.return_value = {'error': {'code': 'Authorization_RequestDenied'}}
        mock_get.return_value = mock_response

        validator = _build_validator()
        validator._access_token = 'mock-token'
        result = validator.validate_graph_access()

        self.assertFalse(result.passed)
        self.assertIn('403', result.message)

    @patch('autopackager.utils.azure_validator.requests.get')
    def test_validate_graph_access_network_error(self, mock_get):
        """Mock ConnectionError."""
        mock_get.side_effect = ConnectionError('Network unreachable')

        validator = _build_validator()
        validator._access_token = 'mock-token'
        result = validator.validate_graph_access()

        self.assertFalse(result.passed)
        self.assertIn('Network unreachable', result.details)


class TestValidateDeploymentRings(unittest.TestCase):
    """Tests for validate_deployment_rings()"""

    def test_validate_deployment_rings_all_valid(self):
        """All groups exist."""
        validator = _build_validator()
        result = validator.validate_deployment_rings()
        self.assertTrue(result.passed)

    def test_validate_deployment_rings_missing_group(self):
        """One group missing."""
        rings = [
            {'ring_id': 0, 'name': 'Ring 0', 'entra_group_id': 'group-id-ring0'},
            {'ring_id': 1, 'name': 'Ring 1', 'entra_group_id': ''},
        ]
        validator = _build_validator(deployment_rings=rings)
        result = validator.validate_deployment_rings()
        self.assertFalse(result.passed)
        self.assertIn('Ring 1', result.message)

    def test_validate_deployment_rings_empty_config(self):
        """Empty rings list is a warning/failure."""
        validator = _build_validator(deployment_rings=[])
        result = validator.validate_deployment_rings()
        self.assertFalse(result.passed)
        self.assertIn('No deployment rings', result.message)


class TestValidateAll(unittest.TestCase):
    """Tests for validate_all()"""

    @patch('autopackager.utils.azure_validator.requests.get')
    @patch('autopackager.utils.azure_validator.ConfidentialClientApplication')
    def test_validate_all_aggregates_results(self, mock_msal_cls, mock_get):
        """validate_all combines all checks."""
        # Auth succeeds
        mock_app = Mock()
        mock_app.acquire_token_for_client.return_value = {'access_token': 'mock-token'}
        mock_msal_cls.return_value = mock_app

        # Graph succeeds
        mock_response = Mock()
        mock_response.status_code = 200
        mock_get.return_value = mock_response

        validator = _build_validator()
        results = validator.validate_all()

        self.assertEqual(len(results), 4)
        self.assertTrue(all(r.passed for r in results))


class TestAzureConfigurationError(unittest.TestCase):
    """Tests for AzureConfigurationError exception"""

    def test_azure_configuration_error_contains_details(self):
        """Exception message formatting includes failed check details."""
        results = [
            ValidationResult(check_name='config', passed=False,
                             message='Missing fields', details='Set tenant_id'),
            ValidationResult(check_name='auth', passed=True, message='OK'),
        ]
        exc = AzureConfigurationError(results)

        self.assertIn('config', str(exc))
        self.assertIn('Missing fields', str(exc))
        self.assertIn('Set tenant_id', str(exc))
        # Passing checks should not appear as failures
        self.assertNotIn('[auth]', str(exc))


class TestDeploymentAgentValidation(unittest.TestCase):
    """Tests for validation integration with DeploymentAgent"""

    @patch('autopackager.agents.deployment.deployment_agent.get_config')
    def test_deployment_agent_validates_before_deploy(self, mock_get_config):
        """deploy() calls validation."""
        mock_get_config.return_value = _make_config()

        from autopackager.agents.deployment.deployment_agent import DeploymentAgent

        agent = DeploymentAgent()

        mock_job = Mock()
        mock_job.job_metadata = {'package_id': 1}

        with patch.object(agent, '_validate_azure_config') as mock_validate:
            with patch('autopackager.agents.deployment.deployment_agent.db_session_scope') as mock_db:
                mock_session = MagicMock()
                mock_db.return_value.__enter__.return_value = mock_session
                mock_package = Mock()
                mock_package.test_passed = True
                mock_package.intune_app_id = None
                mock_session.query.return_value.filter.return_value.first.return_value = mock_package

                with patch.object(agent, '_get_graph_client'):
                    with patch.object(agent, '_create_or_update_intune_app', return_value='app-123'):
                        with patch.object(agent, '_assign_to_ring'):
                            agent.deploy(mock_job)

                mock_validate.assert_called_once()


class TestDeploymentTaskNoRetryOnConfigError(unittest.TestCase):
    """Tests for Celery task behavior on config error"""

    @patch('autopackager.orchestration.tasks.OrchestrationEngine')
    def test_deployment_task_no_retry_on_config_error(self, mock_engine_cls):
        """Celery task fails without retry on AzureConfigurationError."""
        mock_engine = Mock()
        mock_engine_cls.return_value = mock_engine

        from autopackager.orchestration.tasks import deployment_task

        # Patch AzureValidator where it's imported inside the task function
        failed_results = [
            ValidationResult(check_name='config', passed=False, message='Missing tenant_id'),
        ]
        with patch('autopackager.utils.azure_validator.AzureValidator') as mock_validator_cls:
            mock_validator_cls.return_value.validate_all.side_effect = AzureConfigurationError(failed_results)

            result = deployment_task.run(None, 1)

        # Should mark job as failed, not retry
        mock_engine.mark_job_failed.assert_called_once()
        self.assertTrue(result.get('validation_failed'))


if __name__ == '__main__':
    unittest.main()
