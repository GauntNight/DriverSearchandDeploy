"""Pytest configuration and shared fixtures for test suite"""

import pytest
from unittest.mock import Mock, MagicMock
from datetime import datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session

from autopackager.models.base import Base
from autopackager.models.package import Package
from autopackager.models.job import Job, JobType, JobState
from autopackager.models.deployment import Deployment, DeploymentStatus


# Database Fixtures
@pytest.fixture(scope='function')
def db_engine():
    """Create in-memory SQLite engine for testing"""
    engine = create_engine('sqlite:///:memory:', echo=False)
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture(scope='function')
def db_session(db_engine):
    """Create a new database session for each test"""
    Session = scoped_session(sessionmaker(bind=db_engine))
    session = Session()
    yield session
    session.rollback()
    session.close()
    Session.remove()


# Model Fixtures
@pytest.fixture
def sample_package(db_session):
    """Create a sample Package model instance"""
    package = Package(
        name='Test Driver Package',
        version='1.0.0',
        vendor='Dell',
        intunewin_path='/test/package.intunewin',
        installer_path='/test/installer.exe',
        install_command='installer.exe /S',
        uninstall_command='installer.exe /U',
        detection_rules=[
            {
                'type': 'registry',
                'path': 'HKLM\\SOFTWARE\\Test',
                'value': 'Version',
                'data': '1.0.0'
            }
        ],
        requirements={
            'min_os_version': '10.0.19041',
            'architecture': 'x64'
        },
        tested=False,
        test_passed=None,
        deployed=False,
        created_at=datetime.utcnow()
    )
    db_session.add(package)
    db_session.commit()
    return package


@pytest.fixture
def sample_job(db_session):
    """Create a sample Job model instance"""
    job = Job(
        job_type=JobType.DRIVER_UPDATE,
        state=JobState.PENDING,
        software_title='Intel Chipset Driver',
        current_version='10.1.0.1000',
        target_version='10.1.18383.8213',
        vendor='Dell',
        hardware_model='Latitude 7490',
        driver_type='Chipset',
        created_at=datetime.utcnow()
    )
    db_session.add(job)
    db_session.commit()
    return job


@pytest.fixture
def sample_deployment(db_session, sample_package):
    """Create a sample Deployment model instance"""
    deployment = Deployment(
        package_id=sample_package.id,
        intune_app_id='test-app-id-123',
        status=DeploymentStatus.PENDING,
        ring_id='pilot',
        ring_name='Pilot Ring',
        entra_group_id='group-id-123',
        target_device_count=10,
        created_at=datetime.utcnow()
    )
    db_session.add(deployment)
    db_session.commit()
    return deployment


# Mock Package Fixtures
@pytest.fixture
def mock_package():
    """Create a mock Package object"""
    package = Mock(spec=Package)
    package.id = 1
    package.name = 'Test Driver Package'
    package.version = '1.0.0'
    package.vendor = 'Dell'
    package.intunewin_path = '/test/package.intunewin'
    package.installer_path = '/test/installer.exe'
    package.install_command = 'installer.exe /S'
    package.uninstall_command = 'installer.exe /U'
    package.detection_rules = []
    package.requirements = {}
    package.tested = False
    package.test_passed = None
    package.deployed = False
    package.intune_app_id = None
    package.vm_test_results = {}
    return package


# Agent Mock Fixtures
@pytest.fixture
def mock_testing_agent():
    """Create a mock TestingAgent"""
    agent = Mock()
    agent.test_config = {
        'vm_testing_enabled': True,
        'vm_provider': 'local',
        'vm_config': {
            'hyperv': {
                'vm_name': 'TestVM',
                'snapshot_name': 'clean_snapshot',
                'switch_name': 'Default Switch',
                'boot_timeout_seconds': 300
            }
        },
        'timeout_minutes': 30
    }
    return agent


@pytest.fixture
def mock_discovery_agent():
    """Create a mock DiscoveryAgent"""
    agent = Mock()
    agent.vendors = ['Dell', 'HP', 'Lenovo']
    return agent


@pytest.fixture
def mock_packaging_agent():
    """Create a mock PackagingAgent"""
    agent = Mock()
    agent.intunewin_wrapper_path = '/path/to/IntuneWinAppUtil.exe'
    return agent


@pytest.fixture
def mock_deployment_agent():
    """Create a mock DeploymentAgent"""
    agent = Mock()
    agent.graph_client = MagicMock()
    return agent


# Configuration Fixtures
@pytest.fixture
def mock_test_config():
    """Create a mock test configuration"""
    return {
        'vm_testing_enabled': True,
        'vm_provider': 'local',
        'vm_config': {
            'hyperv': {
                'vm_name': 'TestVM',
                'snapshot_name': 'clean_snapshot',
                'switch_name': 'Default Switch',
                'boot_timeout_seconds': 300
            }
        },
        'timeout_minutes': 30,
        'smoke_tests_enabled': True
    }


@pytest.fixture
def mock_graph_config():
    """Create a mock Microsoft Graph configuration"""
    return {
        'tenant_id': 'test-tenant-id',
        'client_id': 'test-client-id',
        'client_secret': 'test-client-secret',
        'authority': 'https://login.microsoftonline.com/test-tenant-id'
    }


# Common Test Data Fixtures
@pytest.fixture
def sample_catalog_data():
    """Sample OEM catalog data for testing parsers"""
    return {
        'dell': {
            'name': 'Dell Command | Update Catalog',
            'drivers': [
                {
                    'name': 'Intel Chipset Driver',
                    'version': '10.1.18383.8213',
                    'category': 'Chipset',
                    'release_date': '2021-03-15',
                    'download_url': 'https://downloads.dell.com/test.exe',
                    'supported_models': ['Latitude 7490', 'OptiPlex 7070']
                }
            ]
        },
        'hp': {
            'name': 'HP SoftPaq Download Manager',
            'drivers': [
                {
                    'name': 'Intel Graphics Driver',
                    'version': '27.20.100.8783',
                    'category': 'Graphics',
                    'release_date': '2021-04-20',
                    'download_url': 'https://ftp.hp.com/test.exe',
                    'supported_models': ['EliteBook 850 G7']
                }
            ]
        }
    }


@pytest.fixture
def sample_graph_response():
    """Sample Microsoft Graph API response"""
    return {
        'value': [
            {
                'id': 'app-id-123',
                'displayName': 'Test Driver Package',
                'publisher': 'Dell',
                'version': '1.0.0',
                'installCommandLine': 'installer.exe /S',
                'uninstallCommandLine': 'installer.exe /U'
            }
        ]
    }


@pytest.fixture
def sample_vm_test_result():
    """Sample VM test result"""
    return {
        'test_passed': True,
        'vm_provider': 'HyperVProvider',
        'test_duration': 120.5,
        'provision_result': {
            'success': True,
            'vm_id': 'TestVM',
            'ip_address': '192.168.1.100'
        },
        'install_result': {
            'success': True,
            'install_logs': 'Install complete',
            'exit_code': 0
        },
        'validation_result': {
            'success': True,
            'validation_results': {},
            'device_status': 'OK'
        },
        'cleanup_result': {
            'success': True
        }
    }


# Cleanup Fixtures
@pytest.fixture(autouse=True)
def reset_singletons():
    """Reset any singleton instances between tests"""
    yield
    # Add any singleton cleanup logic here if needed
