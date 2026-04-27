"""Unit tests for utility modules (config, database, logger, graph_client)"""

import pytest
import os
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock, mock_open
from sqlalchemy import create_engine
from contextlib import contextmanager
import structlog

from autopackager.utils.config import substitute_env_vars, load_config, get_config
from autopackager.utils.database import get_database_url, init_db, get_db_session, db_session_scope
from autopackager.utils.logger import setup_logging, get_logger
from autopackager.utils.graph_client import GraphAPIClient, _expected_blocks


# ============================================================================
# Config Utility Tests
# ============================================================================

class TestConfigUtility:
    """Test cases for configuration management utilities"""

    def test_substitute_env_vars_with_simple_string(self):
        """Test environment variable substitution with simple string"""
        with patch.dict(os.environ, {'TEST_VAR': 'test_value'}):
            config = {'key': '${TEST_VAR}'}
            result = substitute_env_vars(config)
            assert result['key'] == 'test_value'

    def test_substitute_env_vars_with_nested_dict(self):
        """Test environment variable substitution in nested dictionaries"""
        with patch.dict(os.environ, {'DB_HOST': 'localhost', 'DB_PORT': '5432'}):
            config = {
                'database': {
                    'host': '${DB_HOST}',
                    'port': '${DB_PORT}'
                }
            }
            result = substitute_env_vars(config)
            assert result['database']['host'] == 'localhost'
            assert result['database']['port'] == '5432'

    def test_substitute_env_vars_with_list(self):
        """Test environment variable substitution in lists"""
        with patch.dict(os.environ, {'VENDOR1': 'Dell', 'VENDOR2': 'HP'}):
            config = {
                'vendors': ['${VENDOR1}', '${VENDOR2}', 'Lenovo']
            }
            result = substitute_env_vars(config)
            assert result['vendors'] == ['Dell', 'HP', 'Lenovo']

    def test_substitute_env_vars_missing_variable(self):
        """Test that missing environment variables are left as-is"""
        with patch.dict(os.environ, {}, clear=True):
            config = {'key': '${MISSING_VAR}'}
            result = substitute_env_vars(config)
            assert result['key'] == '${MISSING_VAR}'

    def test_substitute_env_vars_no_substitution(self):
        """Test that strings without ${} are unchanged"""
        config = {'key': 'plain_value', 'number': 123, 'bool': True}
        result = substitute_env_vars(config)
        assert result == config

    def test_substitute_env_vars_partial_substitution(self):
        """Test partial substitution in strings"""
        with patch.dict(os.environ, {'HOST': 'example.com'}):
            config = {'url': 'https://${HOST}/api'}
            result = substitute_env_vars(config)
            assert result['url'] == 'https://example.com/api'

    def test_load_config_with_valid_yaml(self, tmp_path):
        """Test loading configuration from valid YAML file"""
        config_file = tmp_path / "test_config.yaml"
        config_content = """
database:
  type: postgresql
  host: localhost
  port: 5432
intune:
  tenant_id: test-tenant
  client_id: test-client
"""
        config_file.write_text(config_content)

        config = load_config(str(config_file))

        assert config['database']['type'] == 'postgresql'
        assert config['database']['host'] == 'localhost'
        assert config['intune']['tenant_id'] == 'test-tenant'

    def test_load_config_with_env_vars(self, tmp_path):
        """Test loading configuration with environment variable substitution"""
        config_file = tmp_path / "test_config.yaml"
        config_content = """
database:
  host: ${DB_HOST}
  password: ${DB_PASSWORD}
"""
        config_file.write_text(config_content)

        with patch.dict(os.environ, {'DB_HOST': 'db.example.com', 'DB_PASSWORD': 'secret123'}):
            config = load_config(str(config_file))

            assert config['database']['host'] == 'db.example.com'
            assert config['database']['password'] == 'secret123'

    @patch('autopackager.utils.config.load_config')
    def test_get_config_singleton(self, mock_load_config):
        """Test that get_config returns the same instance"""
        mock_config = {'test': 'config'}
        mock_load_config.return_value = mock_config

        # Clear any cached config
        if hasattr(get_config, '_config'):
            delattr(get_config, '_config')

        # First call should load config
        config1 = get_config()
        assert config1 == mock_config
        assert mock_load_config.call_count == 1

        # Second call should return cached config
        config2 = get_config()
        assert config2 == mock_config
        assert config2 is config1
        assert mock_load_config.call_count == 1  # Not called again

        # Cleanup
        if hasattr(get_config, '_config'):
            delattr(get_config, '_config')


# ============================================================================
# Database Utility Tests
# ============================================================================

class TestDatabaseUtility:
    """Test cases for database management utilities"""

    @patch('autopackager.utils.database.get_config')
    def test_get_database_url_postgresql(self, mock_get_config):
        """Test PostgreSQL database URL construction"""
        mock_get_config.return_value = {
            'database': {
                'type': 'postgresql',
                'user': 'testuser',
                'password': 'testpass',
                'host': 'localhost',
                'port': 5432,
                'name': 'testdb'
            }
        }

        url = get_database_url()
        assert url == 'postgresql://testuser:testpass@localhost:5432/testdb'

    @patch('autopackager.utils.database.get_config')
    def test_get_database_url_sqlite(self, mock_get_config):
        """Test SQLite database URL construction"""
        mock_get_config.return_value = {
            'database': {
                'type': 'sqlite',
                'path': 'test.db'
            }
        }

        url = get_database_url()
        assert url == 'sqlite:///test.db'

    @patch('autopackager.utils.database.get_config')
    def test_get_database_url_sqlite_default_path(self, mock_get_config):
        """Test SQLite database URL with default path"""
        mock_get_config.return_value = {
            'database': {
                'type': 'sqlite'
            }
        }

        url = get_database_url()
        assert url == 'sqlite:///autopackager.db'

    @patch('autopackager.utils.database.get_config')
    def test_get_database_url_unsupported_type(self, mock_get_config):
        """Test error handling for unsupported database type"""
        mock_get_config.return_value = {
            'database': {
                'type': 'mysql'
            }
        }

        with pytest.raises(ValueError, match="Unsupported database type: mysql"):
            get_database_url()

    @patch('autopackager.utils.database.get_database_url')
    @patch('autopackager.utils.database.create_engine')
    @patch('autopackager.utils.database.scoped_session')
    def test_init_db_creates_engine(self, mock_scoped_session, mock_create_engine, mock_get_url):
        """Test database initialization creates engine and session factory"""
        mock_get_url.return_value = 'sqlite:///:memory:'
        mock_engine = Mock()
        mock_create_engine.return_value = mock_engine

        # Reset global state
        import autopackager.utils.database as db_module
        db_module._engine = None
        db_module._session_factory = None

        engine = init_db(create_tables=False)

        assert engine == mock_engine
        mock_create_engine.assert_called_once_with(
            'sqlite:///:memory:',
            pool_pre_ping=True,
            pool_size=10,
            max_overflow=20
        )
        mock_scoped_session.assert_called_once()

    @patch('autopackager.utils.database.get_database_url')
    @patch('autopackager.utils.database.create_engine')
    @patch('autopackager.utils.database.scoped_session')
    @patch('autopackager.models.deployment.Base')
    @patch('autopackager.models.package.Base')
    @patch('autopackager.models.job.Base')
    def test_init_db_creates_tables(self, mock_job_base, mock_package_base, mock_deployment_base,
                                    mock_scoped_session, mock_create_engine, mock_get_url):
        """Test database initialization with table creation"""
        mock_get_url.return_value = 'sqlite:///:memory:'
        mock_engine = Mock()
        mock_engine.metadata = Mock()
        mock_create_engine.return_value = mock_engine

        # Reset global state
        import autopackager.utils.database as db_module
        db_module._engine = None
        db_module._session_factory = None

        init_db(create_tables=True)

        # Verify create_all was called for each Base
        mock_job_base.metadata.create_all.assert_called_once_with(mock_engine)
        mock_package_base.metadata.create_all.assert_called_once_with(mock_engine)
        mock_deployment_base.metadata.create_all.assert_called_once_with(mock_engine)

    @patch('autopackager.utils.database.init_db')
    def test_get_db_session_initializes_if_needed(self, mock_init_db):
        """Test get_db_session initializes database if not already done"""
        # Reset global state
        import autopackager.utils.database as db_module
        db_module._session_factory = None

        # Mock session factory
        mock_session = Mock()
        mock_factory = Mock(return_value=mock_session)

        def side_effect():
            db_module._session_factory = mock_factory
            return Mock()

        mock_init_db.side_effect = side_effect

        session = get_db_session()

        mock_init_db.assert_called_once()
        assert session == mock_session

    def test_db_session_scope_commits_on_success(self, db_engine):
        """Test that db_session_scope commits on successful execution"""
        # Set up session factory
        import autopackager.utils.database as db_module
        from sqlalchemy.orm import sessionmaker, scoped_session

        db_module._engine = db_engine
        db_module._session_factory = scoped_session(
            sessionmaker(bind=db_engine, autocommit=False, autoflush=False)
        )

        # Mock commit to verify it's called
        with patch.object(db_module._session_factory(), 'commit') as mock_commit:
            with db_session_scope() as session:
                pass  # Successful execution

            mock_commit.assert_called_once()

    def test_db_session_scope_rolls_back_on_exception(self, db_engine):
        """Test that db_session_scope rolls back on exception"""
        # Set up session factory
        import autopackager.utils.database as db_module
        from sqlalchemy.orm import sessionmaker, scoped_session

        db_module._engine = db_engine
        db_module._session_factory = scoped_session(
            sessionmaker(bind=db_engine, autocommit=False, autoflush=False)
        )

        # Mock rollback to verify it's called
        with patch.object(db_module._session_factory(), 'rollback') as mock_rollback:
            with pytest.raises(ValueError):
                with db_session_scope() as session:
                    raise ValueError("Test error")

            mock_rollback.assert_called_once()


# ============================================================================
# Logger Utility Tests
# ============================================================================

class TestLoggerUtility:
    """Test cases for logging configuration utilities"""

    @patch('autopackager.utils.logger.logging.basicConfig')
    @patch('autopackager.utils.logger.structlog.configure')
    def test_setup_logging_default_level(self, mock_structlog_config, mock_basic_config):
        """Test logging setup with default INFO level"""
        setup_logging()

        mock_basic_config.assert_called_once()
        call_args = mock_basic_config.call_args[1]
        assert call_args['level'] == 20  # logging.INFO

        mock_structlog_config.assert_called_once()

    @patch('autopackager.utils.logger.logging.basicConfig')
    @patch('autopackager.utils.logger.structlog.configure')
    def test_setup_logging_custom_level(self, mock_structlog_config, mock_basic_config):
        """Test logging setup with custom log level"""
        setup_logging(log_level='DEBUG')

        mock_basic_config.assert_called_once()
        call_args = mock_basic_config.call_args[1]
        assert call_args['level'] == 10  # logging.DEBUG

    @patch('autopackager.utils.logger.logging.basicConfig')
    @patch('autopackager.utils.logger.structlog.configure')
    @patch('autopackager.utils.logger.logging.FileHandler')
    def test_setup_logging_with_file(self, mock_file_handler, mock_structlog_config, mock_basic_config, tmp_path):
        """Test logging setup with file output"""
        log_file = tmp_path / "logs" / "test.log"

        # Configure the mock to return a proper handler with a level attribute
        mock_handler_instance = Mock()
        mock_handler_instance.level = 20  # logging.INFO
        mock_file_handler.return_value = mock_handler_instance

        setup_logging(log_level='INFO', log_file=str(log_file))

        # Verify directory was created
        assert log_file.parent.exists()

        # Verify file handler was created
        mock_file_handler.assert_called_once_with(str(log_file))

    @patch('autopackager.utils.logger.logging.basicConfig')
    @patch('autopackager.utils.logger.structlog.configure')
    def test_setup_logging_configures_structlog(self, mock_structlog_config, mock_basic_config):
        """Test that structlog is properly configured"""
        setup_logging()

        mock_structlog_config.assert_called_once()
        call_args = mock_structlog_config.call_args[1]

        # Verify processors are configured
        assert 'processors' in call_args
        assert len(call_args['processors']) > 0

        # Verify context_class and logger_factory
        assert call_args['context_class'] == dict
        assert call_args['cache_logger_on_first_use'] is True

    def test_get_logger_returns_structlog_logger(self):
        """Test that get_logger returns a structlog logger instance"""
        logger = get_logger('test_logger')

        # Verify it's a structlog logger
        assert hasattr(logger, 'info')
        assert hasattr(logger, 'error')
        assert hasattr(logger, 'debug')
        assert hasattr(logger, 'warning')

    def test_get_logger_with_name(self):
        """Test that get_logger accepts a name parameter"""
        logger1 = get_logger('logger1')
        logger2 = get_logger('logger2')

        # Both should be valid loggers
        assert logger1 is not None
        assert logger2 is not None


# ============================================================================
# Graph Client Utility Tests
# ============================================================================

class TestGraphClientUtility:
    """Test cases for Microsoft Graph API client utilities"""

    @patch('autopackager.utils.graph_client.get_config')
    @patch('autopackager.utils.graph_client.ConfidentialClientApplication')
    def test_graph_client_initialization(self, mock_msal_app, mock_get_config):
        """Test GraphAPIClient initialization and authentication"""
        mock_get_config.return_value = {
            'intune': {
                'tenant_id': 'test-tenant-id',
                'client_id': 'test-client-id',
                'client_secret': 'test-secret',
                'graph_endpoint': 'https://graph.microsoft.com',
                'graph_api_version': 'v1.0'
            }
        }

        mock_app_instance = Mock()
        mock_app_instance.acquire_token_for_client.return_value = {
            'access_token': 'test-access-token'
        }
        mock_msal_app.return_value = mock_app_instance

        client = GraphAPIClient()

        assert client.tenant_id == 'test-tenant-id'
        assert client.client_id == 'test-client-id'
        assert client.access_token == 'test-access-token'
        mock_app_instance.acquire_token_for_client.assert_called_once()

    @patch('autopackager.utils.graph_client.get_config')
    @patch('autopackager.utils.graph_client.ConfidentialClientApplication')
    def test_graph_client_authentication_failure(self, mock_msal_app, mock_get_config):
        """Test GraphAPIClient handles authentication failures"""
        mock_get_config.return_value = {
            'intune': {
                'tenant_id': 'test-tenant-id',
                'client_id': 'test-client-id',
                'client_secret': 'test-secret',
                'graph_endpoint': 'https://graph.microsoft.com',
                'graph_api_version': 'v1.0'
            }
        }

        mock_app_instance = Mock()
        mock_app_instance.acquire_token_for_client.return_value = {
            'error': 'invalid_client',
            'error_description': 'Invalid client credentials'
        }
        mock_msal_app.return_value = mock_app_instance

        with pytest.raises(Exception, match='Authentication failed'):
            GraphAPIClient()

    @patch('autopackager.utils.graph_client.get_config')
    @patch('autopackager.utils.graph_client.ConfidentialClientApplication')
    def test_graph_client_get_headers(self, mock_msal_app, mock_get_config):
        """Test _get_headers returns correct authorization headers"""
        mock_get_config.return_value = {
            'intune': {
                'tenant_id': 'test-tenant-id',
                'client_id': 'test-client-id',
                'client_secret': 'test-secret',
                'graph_endpoint': 'https://graph.microsoft.com',
                'graph_api_version': 'v1.0'
            }
        }

        mock_app_instance = Mock()
        mock_app_instance.acquire_token_for_client.return_value = {
            'access_token': 'test-access-token'
        }
        mock_msal_app.return_value = mock_app_instance

        client = GraphAPIClient()
        headers = client._get_headers()

        assert headers['Authorization'] == 'Bearer test-access-token'
        assert headers['Content-Type'] == 'application/json'

    @patch('autopackager.utils.graph_client.get_config')
    @patch('autopackager.utils.graph_client.ConfidentialClientApplication')
    @patch('autopackager.utils.graph_client.requests.get')
    def test_graph_client_get_request(self, mock_requests_get, mock_msal_app, mock_get_config):
        """Test GraphAPIClient GET request"""
        mock_get_config.return_value = {
            'intune': {
                'tenant_id': 'test-tenant-id',
                'client_id': 'test-client-id',
                'client_secret': 'test-secret',
                'graph_endpoint': 'https://graph.microsoft.com',
                'graph_api_version': 'v1.0'
            }
        }

        mock_app_instance = Mock()
        mock_app_instance.acquire_token_for_client.return_value = {
            'access_token': 'test-access-token'
        }
        mock_msal_app.return_value = mock_app_instance

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'value': [{'id': '123'}]}
        mock_requests_get.return_value = mock_response

        client = GraphAPIClient()
        result = client.get('deviceAppManagement/mobileApps')

        assert result == {'value': [{'id': '123'}]}
        mock_requests_get.assert_called_once()

    @patch('autopackager.utils.graph_client.get_config')
    @patch('autopackager.utils.graph_client.ConfidentialClientApplication')
    @patch('autopackager.utils.graph_client.requests.post')
    def test_graph_client_post_request(self, mock_requests_post, mock_msal_app, mock_get_config):
        """Test GraphAPIClient POST request"""
        mock_get_config.return_value = {
            'intune': {
                'tenant_id': 'test-tenant-id',
                'client_id': 'test-client-id',
                'client_secret': 'test-secret',
                'graph_endpoint': 'https://graph.microsoft.com',
                'graph_api_version': 'v1.0'
            }
        }

        mock_app_instance = Mock()
        mock_app_instance.acquire_token_for_client.return_value = {
            'access_token': 'test-access-token'
        }
        mock_msal_app.return_value = mock_app_instance

        mock_response = Mock()
        mock_response.status_code = 201
        mock_response.content = b'{"id": "new-app-id"}'
        mock_response.json.return_value = {'id': 'new-app-id'}
        mock_requests_post.return_value = mock_response

        client = GraphAPIClient()
        result = client.post('deviceAppManagement/mobileApps', data={'displayName': 'Test App'})

        assert result == {'id': 'new-app-id'}
        mock_requests_post.assert_called_once()

    @patch('autopackager.utils.graph_client.get_config')
    @patch('autopackager.utils.graph_client.ConfidentialClientApplication')
    @patch('autopackager.utils.graph_client.requests.delete')
    def test_graph_client_delete_request(self, mock_requests_delete, mock_msal_app, mock_get_config):
        """Test GraphAPIClient DELETE request"""
        mock_get_config.return_value = {
            'intune': {
                'tenant_id': 'test-tenant-id',
                'client_id': 'test-client-id',
                'client_secret': 'test-secret',
                'graph_endpoint': 'https://graph.microsoft.com',
                'graph_api_version': 'v1.0'
            }
        }

        mock_app_instance = Mock()
        mock_app_instance.acquire_token_for_client.return_value = {
            'access_token': 'test-access-token'
        }
        mock_msal_app.return_value = mock_app_instance

        mock_response = Mock()
        mock_response.status_code = 204
        mock_requests_delete.return_value = mock_response

        client = GraphAPIClient()
        result = client.delete('deviceAppManagement/mobileApps/app-id-123')

        assert result is True
        mock_requests_delete.assert_called_once()

    @patch('autopackager.utils.graph_client.get_config')
    @patch('autopackager.utils.graph_client.ConfidentialClientApplication')
    def test_graph_client_parse_response_no_content(self, mock_msal_app, mock_get_config):
        """Test _parse_response handles 204 No Content correctly"""
        mock_get_config.return_value = {
            'intune': {
                'tenant_id': 'test-tenant-id',
                'client_id': 'test-client-id',
                'client_secret': 'test-secret',
                'graph_endpoint': 'https://graph.microsoft.com',
                'graph_api_version': 'v1.0'
            }
        }

        mock_app_instance = Mock()
        mock_app_instance.acquire_token_for_client.return_value = {
            'access_token': 'test-access-token'
        }
        mock_msal_app.return_value = mock_app_instance

        client = GraphAPIClient()

        mock_response = Mock()
        mock_response.status_code = 204
        mock_response.content = b''

        result = client._parse_response(mock_response)
        assert result is None

    @patch('autopackager.utils.graph_client.get_config')
    @patch('autopackager.utils.graph_client.ConfidentialClientApplication')
    def test_graph_client_azure_url_with_params(self, mock_msal_app, mock_get_config):
        """Test _azure_url_with_params appends query parameters correctly"""
        mock_get_config.return_value = {
            'intune': {
                'tenant_id': 'test-tenant-id',
                'client_id': 'test-client-id',
                'client_secret': 'test-secret',
                'graph_endpoint': 'https://graph.microsoft.com',
                'graph_api_version': 'v1.0'
            }
        }

        mock_app_instance = Mock()
        mock_app_instance.acquire_token_for_client.return_value = {
            'access_token': 'test-access-token'
        }
        mock_msal_app.return_value = mock_app_instance

        client = GraphAPIClient()

        sas_url = 'https://storage.azure.com/container/file?sv=2020-08-04&sig=xyz'
        result = client._azure_url_with_params(sas_url, comp='block', blockid='abc123')

        assert 'comp=block' in result
        assert 'blockid=abc123' in result
        assert 'sv=2020-08-04' in result
        assert 'sig=xyz' in result

    def test_expected_blocks_calculation(self):
        """Test _expected_blocks calculates correct number of chunks"""
        chunk_size = 6 * 1024 * 1024  # 6 MB

        # Exactly one chunk
        assert _expected_blocks(chunk_size) == 1

        # Less than one chunk
        assert _expected_blocks(chunk_size - 1) == 1

        # Exactly two chunks
        assert _expected_blocks(chunk_size * 2) == 2

        # Two chunks plus a byte
        assert _expected_blocks(chunk_size * 2 + 1) == 3

        # Large file
        assert _expected_blocks(100 * 1024 * 1024) == 17  # ~100 MB


# ============================================================================
# Integration Tests for Utility Module Interactions
# ============================================================================

class TestUtilityModuleIntegration:
    """Test cases for interactions between utility modules"""

    @patch('autopackager.utils.database.get_config')
    @patch('autopackager.utils.database.create_engine')
    def test_database_uses_config(self, mock_create_engine, mock_get_config):
        """Test that database module correctly uses config module"""
        mock_get_config.return_value = {
            'database': {
                'type': 'postgresql',
                'user': 'testuser',
                'password': 'testpass',
                'host': 'localhost',
                'port': 5432,
                'name': 'testdb'
            }
        }

        # Reset global state
        import autopackager.utils.database as db_module
        db_module._engine = None
        db_module._session_factory = None

        url = get_database_url()

        assert 'postgresql://testuser:testpass@localhost:5432/testdb' == url
        mock_get_config.assert_called()

    @patch('autopackager.utils.graph_client.get_config')
    @patch('autopackager.utils.graph_client.ConfidentialClientApplication')
    def test_graph_client_uses_config(self, mock_msal_app, mock_get_config):
        """Test that graph client correctly uses config module"""
        test_config = {
            'intune': {
                'tenant_id': 'tenant-123',
                'client_id': 'client-456',
                'client_secret': 'secret-789',
                'graph_endpoint': 'https://graph.microsoft.com',
                'graph_api_version': 'v1.0'
            }
        }
        mock_get_config.return_value = test_config

        mock_app_instance = Mock()
        mock_app_instance.acquire_token_for_client.return_value = {
            'access_token': 'test-token'
        }
        mock_msal_app.return_value = mock_app_instance

        client = GraphAPIClient()

        assert client.tenant_id == 'tenant-123'
        assert client.client_id == 'client-456'
        mock_get_config.assert_called()
