# AutoPackager Test Suite

Comprehensive pytest-based test suite for the AutoPackager automated driver deployment system.

## Overview

This test suite provides extensive coverage of all core AutoPackager modules including:
- **Discovery agents** - OEM catalog parsing (Dell, HP, Lenovo) and version comparison
- **Packaging agents** - Driver download and .intunewin package creation
- **Testing agents** - Smoke tests and VM-based validation
- **Deployment agents** - Microsoft Graph API interactions
- **Database models** - Package, Job, and Deployment models
- **Orchestration engine** - Celery task coordination and state management
- **CLI commands** - Command-line interface
- **Web API** - RESTful API endpoints

All tests are designed to run without external dependencies through comprehensive mocking.

## Coverage Targets

| Module | Target | Current |
|--------|--------|---------|
| Agents | ≥70% | - |
| Models | ≥70% | - |
| Orchestration | ≥70% | - |
| Utils | ≥60% | - |
| Web | ≥60% | - |

Run `pytest --cov` to generate current coverage report.

## Test Structure

```
tests/
├── conftest.py                    # Shared fixtures and configuration
├── __init__.py
│
├── unit/                          # Unit tests (isolated, mocked)
│   ├── test_discovery_agent.py    # Catalog parsing & version comparison
│   ├── test_packaging_agent.py    # Download & .intunewin creation
│   ├── test_testing_agent_main.py # Smoke tests & VM coordination
│   ├── test_deployment_agent.py   # Graph API interactions
│   ├── test_models.py             # Database models
│   └── test_utils.py              # Utility functions
│
├── integration/                   # Integration tests (component interactions)
│   ├── test_celery_tasks.py       # Celery task execution
│   ├── test_orchestration_engine.py # End-to-end job orchestration
│   ├── test_full_pipeline.py      # Complete pipeline workflows
│   └── test_continuous_discovery.py # Continuous catalog discovery + DiscoveryRun
│
├── cli/                           # CLI command tests
│   └── test_cli_commands.py       # Click CLI testing
│
├── api/                           # Web API tests
│   └── test_web_api.py            # FastAPI endpoint testing
│
└── fixtures/                      # Test data and mocks
    ├── sample_catalogs.py         # OEM catalog XML samples
    ├── mock_graph_api.py          # Microsoft Graph API mocks
    ├── dell_catalog_sample.xml    # Dell sample data
    ├── hp_catalog_sample.xml      # HP sample data
    └── lenovo_catalog_sample.xml  # Lenovo sample data
```

## Running Tests

### All Tests
```bash
# Run entire test suite with coverage
pytest

# Run with verbose output
pytest -v

# Run with extra verbose output (show test names)
pytest -vv
```

### By Category
```bash
# Unit tests only
pytest tests/unit/

# Integration tests only
pytest tests/integration/

# CLI tests only
pytest tests/cli/

# API tests only
pytest tests/api/
```

### By Marker
```bash
# Only fast unit tests (no external dependencies)
pytest -m unit

# Integration tests
pytest -m integration

# Skip slow tests
pytest -m "not slow"

# Tests that don't require Redis
pytest -m "not requires_redis"

# Tests that don't require database
pytest -m "not requires_db"
```

### Single Test File or Function
```bash
# Single file
pytest tests/unit/test_discovery_agent.py

# Single test class
pytest tests/unit/test_discovery_agent.py::TestDellCatalogParser

# Single test function
pytest tests/unit/test_discovery_agent.py::TestDellCatalogParser::test_parse_dell_catalog_success
```

### With Coverage
```bash
# Generate coverage report
pytest --cov

# Generate HTML coverage report (opens in browser)
pytest --cov --cov-report=html
open htmlcov/index.html  # Linux/Mac
start htmlcov\index.html  # Windows

# Coverage for specific module
pytest --cov=autopackager.agents.discovery
```

### Debugging Tests
```bash
# Show print statements and logging
pytest -s

# Drop into debugger on failure
pytest --pdb

# Show local variables in traceback
pytest --showlocals

# Run last failed tests only
pytest --lf

# Run failed tests first, then others
pytest --ff
```

## Test Markers

Tests are categorized with pytest markers for selective execution:

| Marker | Description |
|--------|-------------|
| `@pytest.mark.unit` | Unit tests (isolated, no external dependencies) |
| `@pytest.mark.integration` | Integration tests (component interactions) |
| `@pytest.mark.cli` | CLI command tests |
| `@pytest.mark.api` | Web API endpoint tests |
| `@pytest.mark.slow` | Tests that take significant time (>5 seconds) |
| `@pytest.mark.requires_redis` | Tests requiring Redis connection |
| `@pytest.mark.requires_db` | Tests requiring database connection |
| `@pytest.mark.requires_graph_api` | Tests requiring Microsoft Graph API |
| `@pytest.mark.requires_vm` | Tests requiring VM provider (Hyper-V) |

## Mocking Strategy

### External Services

All external dependencies are mocked to enable fast, reliable tests without network access:

| Dependency | Mock Strategy |
|------------|---------------|
| **Redis** | In-memory mock or pytest fixtures |
| **PostgreSQL** | SQLite in-memory database |
| **Microsoft Graph API** | `responses` library for HTTP mocking |
| **OEM Catalogs** | Static XML samples in `fixtures/` |
| **File System** | `unittest.mock.mock_open` and temp directories |
| **IntuneWinAppUtil.exe** | Mock subprocess calls |
| **LLM APIs** | Mock responses with sample data |
| **Celery** | Mock `@shared_task` decorator |

### Mock Libraries Used

- **`unittest.mock`** - Core mocking (Mock, MagicMock, patch, mock_open)
- **`responses`** - HTTP request mocking
- **`pytest-mock`** - Pytest integration for mocks
- **`fakeredis`** - In-memory Redis replacement (if needed)

### Common Mock Patterns

#### Mocking Configuration
```python
@patch('autopackager.config.get_config')
def test_with_mock_config(mock_get_config):
    mock_get_config.return_value = {
        'oem_catalogs': {...},
        'graph_api': {...}
    }
    # Test code here
```

#### Mocking External HTTP Calls
```python
import responses

@responses.activate
def test_catalog_download():
    responses.add(
        responses.GET,
        'https://downloads.dell.com/catalog/DriverPackCatalog.cab',
        body=DELL_CATALOG,
        status=200
    )
    # Test code here
```

#### Mocking File System
```python
from unittest.mock import mock_open, patch

def test_file_read():
    mock_data = "file content"
    with patch('builtins.open', mock_open(read_data=mock_data)):
        # Test code here
```

#### Mocking Database
```python
def test_database_query(db_session, sample_package):
    """Use pytest fixtures for in-memory database"""
    # db_session and sample_package are fixtures from conftest.py
    result = db_session.query(Package).filter_by(id=sample_package.id).first()
    assert result.name == sample_package.name
```

## Shared Fixtures

The `conftest.py` file provides shared fixtures available to all tests:

### Database Fixtures
- `db_engine` - In-memory SQLite engine
- `db_session` - SQLAlchemy session with automatic rollback
- `sample_package` - Pre-created Package model instance
- `sample_job` - Pre-created Job model instance
- `sample_deployment` - Pre-created Deployment model instance

### Mock Object Fixtures
- `mock_package` - Mock Package object
- `mock_testing_agent` - Mock TestingAgent
- `mock_discovery_agent` - Mock DiscoveryAgent
- `mock_packaging_agent` - Mock PackagingAgent
- `mock_deployment_agent` - Mock DeploymentAgent

### Configuration Fixtures
- `mock_test_config` - Mock test configuration
- `mock_graph_config` - Mock Microsoft Graph configuration

### Test Data Fixtures
- `sample_catalog_data` - Sample OEM catalog structures
- `sample_graph_response` - Sample Graph API responses
- `sample_vm_test_result` - Sample VM test results

### Usage Example
```python
def test_package_creation(db_session, sample_package):
    """Fixtures are automatically injected by pytest"""
    assert sample_package.id is not None
    assert sample_package.vendor == 'Dell'
```

## Writing New Tests

### Unit Test Template
```python
"""Unit tests for [Module Name]"""

import pytest
from unittest.mock import Mock, patch, MagicMock

from autopackager.agents.example import ExampleAgent


@pytest.mark.unit
class TestExampleAgent:
    """Test cases for ExampleAgent"""

    @pytest.fixture
    def agent(self):
        """Create agent instance for testing"""
        with patch('autopackager.config.get_config', return_value={}):
            return ExampleAgent()

    def test_method_success(self, agent):
        """Test successful method execution"""
        result = agent.some_method()
        assert result is not None

    def test_method_with_mock(self, agent):
        """Test method with mocked dependencies"""
        with patch.object(agent, '_internal_method', return_value='mocked'):
            result = agent.some_method()
            assert result == 'mocked'

    def test_error_handling(self, agent):
        """Test error handling"""
        with pytest.raises(ValueError):
            agent.some_method(invalid_param=True)
```

### Integration Test Template
```python
"""Integration tests for [Module Name]"""

import pytest
from unittest.mock import Mock, patch

from autopackager.orchestration.engine import OrchestrationEngine


@pytest.mark.integration
class TestOrchestrationFlow:
    """Test cases for orchestration workflows"""

    @pytest.fixture
    def engine(self, db_session):
        """Create engine with real database"""
        with patch('autopackager.config.get_config', return_value={}):
            return OrchestrationEngine(session=db_session)

    def test_full_workflow(self, engine, sample_job):
        """Test complete job workflow"""
        # Test workflow from start to finish
        engine.start_job(sample_job)
        assert sample_job.state == JobState.RUNNING
```

### CLI Test Template
```python
"""CLI command tests"""

import pytest
from click.testing import CliRunner
from unittest.mock import patch

from cli import cli


@pytest.mark.cli
class TestCommand:
    """Test cases for CLI command"""

    def test_command_success(self):
        """Test successful command execution"""
        runner = CliRunner()
        result = runner.invoke(cli, ['command', '--option', 'value'])
        assert result.exit_code == 0
        assert 'Success' in result.output
```

## Code Coverage

### Configuration

Coverage is configured in `pytest.ini` and `.coveragerc`:
- Focus on core modules: `autopackager/agents`, `autopackager/models`, `autopackager/orchestration`
- Exclude test files, VM providers, and `__pycache__`
- Generate terminal, HTML, and XML reports

### Viewing Coverage

**Terminal Report** (shows missing lines)
```bash
pytest --cov
```

**HTML Report** (interactive browser view)
```bash
pytest --cov --cov-report=html
open htmlcov/index.html
```

**Coverage for Specific File**
```bash
pytest --cov=autopackager.agents.discovery.discovery_agent tests/unit/test_discovery_agent.py
```

### Improving Coverage

1. **Identify gaps**: Check HTML report for uncovered lines
2. **Write tests**: Add tests for uncovered code paths
3. **Focus on critical paths**: Prioritize error handling and edge cases
4. **Don't game the metric**: Tests should verify behavior, not just execute code

## Common Patterns

### Testing Async Functions
```python
@pytest.mark.asyncio
async def test_async_function():
    """Test async function"""
    result = await some_async_function()
    assert result is not None
```

### Testing Exceptions
```python
def test_exception_raised():
    """Test that exception is raised"""
    with pytest.raises(ValueError, match="expected error message"):
        function_that_raises()
```

### Parametrized Tests
```python
@pytest.mark.parametrize("input,expected", [
    ("dell", "Dell"),
    ("hp", "HP"),
    ("lenovo", "Lenovo"),
])
def test_vendor_normalization(input, expected):
    """Test vendor name normalization"""
    assert normalize_vendor(input) == expected
```

### Testing with Temporary Files
```python
def test_file_operation(tmp_path):
    """Test file operation with temporary directory"""
    test_file = tmp_path / "test.txt"
    test_file.write_text("content")
    result = process_file(test_file)
    assert result is not None
```

### Mocking Time
```python
from unittest.mock import patch
from datetime import datetime

def test_time_dependent():
    """Test time-dependent behavior"""
    fixed_time = datetime(2024, 1, 15, 12, 0, 0)
    with patch('autopackager.utils.datetime') as mock_datetime:
        mock_datetime.utcnow.return_value = fixed_time
        result = function_using_time()
        assert result.timestamp == fixed_time
```

## Continuous Integration

Tests are designed to run in CI/CD pipelines:

### GitHub Actions Example
```yaml
- name: Run tests
  run: |
    pytest --cov --cov-report=xml --cov-report=term
    
- name: Upload coverage
  uses: codecov/codecov-action@v3
  with:
    file: ./coverage.xml
```

### Requirements
- Python 3.8+
- All dependencies in `requirements.txt`
- No external services (Redis, PostgreSQL, etc.) needed
- Test suite completes in <5 minutes

## Troubleshooting

### Tests Fail with Import Errors
**Problem**: `ModuleNotFoundError` for `autopackager`

**Solution**: Ensure you're running from project root and package is installed
```bash
pip install -e .
pytest
```

### Database Errors
**Problem**: `sqlalchemy.exc.OperationalError`

**Solution**: Database fixtures handle cleanup automatically. If issues persist:
```bash
# Delete any stale test databases
rm -f test_*.db
```

### Mock Not Working
**Problem**: Real service is called instead of mock

**Solution**: Ensure mock is patched at the correct location
```python
# Wrong: patches where imported from
@patch('requests.get')

# Right: patches where used
@patch('autopackager.agents.discovery.requests.get')
```

### Coverage Not Matching
**Problem**: Coverage report shows 0% or missing modules

**Solution**: Check that modules are in coverage paths (`pytest.ini`)
```bash
# Verify coverage configuration
cat pytest.ini | grep cov=
```

### Slow Tests
**Problem**: Test suite takes too long

**Solution**: Mark slow tests and skip them during development
```python
@pytest.mark.slow
def test_heavy_operation():
    # Time-consuming test
    pass

# Run without slow tests
pytest -m "not slow"
```

### Fixture Not Found
**Problem**: `fixture 'xyz' not found`

**Solution**: Ensure fixture is defined in `conftest.py` or imported properly
```python
# Add to conftest.py or import with pytest_plugins
pytest_plugins = ['tests.fixtures.custom_fixtures']
```

## Best Practices

1. **Test Naming**: Use descriptive names that explain what's being tested
   - Good: `test_dell_catalog_parser_handles_malformed_xml`
   - Bad: `test_parser` or `test_1`

2. **One Assertion Per Test**: Each test should verify one specific behavior
   - Makes failures easier to diagnose
   - Tests are more maintainable

3. **Arrange-Act-Assert**: Structure tests clearly
   ```python
   def test_example():
       # Arrange - set up test data
       agent = DiscoveryAgent()
       
       # Act - perform the action
       result = agent.discover()
       
       # Assert - verify the result
       assert result['success'] is True
   ```

4. **Mock External Dependencies**: Never call real external services
   - Keeps tests fast
   - Ensures reliability
   - Prevents side effects

5. **Use Fixtures**: Share common setup via fixtures
   - Reduces code duplication
   - Makes tests more maintainable
   - Ensures consistent test data

6. **Test Error Cases**: Don't just test the happy path
   - Test invalid inputs
   - Test error conditions
   - Test edge cases

7. **Keep Tests Independent**: Each test should run in isolation
   - No shared state between tests
   - Order shouldn't matter
   - Can run tests in parallel

8. **Document Complex Tests**: Add docstrings explaining what and why
   ```python
   def test_complex_scenario(self):
       """Test that version comparison handles pre-release tags correctly
       
       Dell catalogs sometimes include 'A00', 'A01' suffixes on versions.
       These should be normalized before comparison to prevent false positives.
       """
       # Test implementation
   ```

## Resources

- [Pytest Documentation](https://docs.pytest.org/)
- [unittest.mock Documentation](https://docs.python.org/3/library/unittest.mock.html)
- [Responses Library](https://github.com/getsentry/responses)
- [Click Testing](https://click.palletsprojects.com/en/8.1.x/testing/)
- [FastAPI Testing](https://fastapi.tiangolo.com/tutorial/testing/)

## Contributing

When adding new features:

1. **Write tests first** (TDD) or alongside implementation
2. **Maintain coverage** at ≥70% for core modules
3. **Add fixtures** for reusable test data to `conftest.py`
4. **Mark tests appropriately** with pytest markers
5. **Update this README** if adding new test categories or patterns
6. **Run full suite** before submitting PR: `pytest --cov`

## Support

For questions or issues with the test suite:
1. Check this README first
2. Review existing test files for patterns
3. Check `conftest.py` for available fixtures
4. Consult pytest documentation
5. Open an issue with details about the problem
