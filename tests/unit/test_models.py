"""Unit tests for database models"""

import unittest
from datetime import datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session

from autopackager.models.base import Base
from autopackager.models.job import Job, JobType, JobState
from autopackager.models.package import Package
from autopackager.models.deployment import Deployment, DeploymentStatus
from autopackager.models.discovery_run import DiscoveryRun


class TestJobModel(unittest.TestCase):
    """Test cases for Job model"""

    def setUp(self):
        """Set up test database"""
        self.engine = create_engine('sqlite:///:memory:', echo=False)
        Base.metadata.create_all(self.engine)
        Session = scoped_session(sessionmaker(bind=self.engine))
        self.session = Session()

    def tearDown(self):
        """Clean up test database"""
        self.session.rollback()
        self.session.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def test_job_creation_minimal(self):
        """Test creating a job with minimal required fields"""
        job = Job(
            job_type=JobType.DRIVER_UPDATE,
            state=JobState.PENDING,
            software_title='Test Driver'
        )
        self.session.add(job)
        self.session.commit()

        self.assertIsNotNone(job.id)
        self.assertEqual(job.job_type, JobType.DRIVER_UPDATE)
        self.assertEqual(job.state, JobState.PENDING)
        self.assertEqual(job.software_title, 'Test Driver')
        self.assertEqual(job.retry_count, 0)
        self.assertIsNotNone(job.created_at)

    def test_job_creation_full(self):
        """Test creating a job with all fields"""
        now = datetime.utcnow()
        job = Job(
            job_type=JobType.DRIVER_UPDATE,
            state=JobState.DISCOVERING,
            software_title='Intel Chipset Driver',
            current_version='10.1.0.1000',
            target_version='10.1.18383.8213',
            vendor='Dell',
            hardware_model='Latitude 7490',
            driver_type='Chipset',
            download_url='https://downloads.dell.com/test.exe',
            release_notes='Bug fixes and improvements',
            package_id=123,
            intunewin_path='/path/to/package.intunewin',
            created_at=now,
            retry_count=2,
            error_message='Test error',
            job_metadata={'key': 'value'}
        )
        self.session.add(job)
        self.session.commit()

        self.assertIsNotNone(job.id)
        self.assertEqual(job.software_title, 'Intel Chipset Driver')
        self.assertEqual(job.current_version, '10.1.0.1000')
        self.assertEqual(job.target_version, '10.1.18383.8213')
        self.assertEqual(job.vendor, 'Dell')
        self.assertEqual(job.hardware_model, 'Latitude 7490')
        self.assertEqual(job.driver_type, 'Chipset')
        self.assertEqual(job.download_url, 'https://downloads.dell.com/test.exe')
        self.assertEqual(job.package_id, 123)
        self.assertEqual(job.retry_count, 2)
        self.assertEqual(job.error_message, 'Test error')
        self.assertEqual(job.job_metadata, {'key': 'value'})

    def test_job_state_enum(self):
        """Test all job state enum values"""
        states = [
            JobState.PENDING,
            JobState.DISCOVERING,
            JobState.PACKAGING,
            JobState.TESTING,
            JobState.DEPLOYING,
            JobState.COMPLETED,
            JobState.FAILED,
            JobState.CANCELLED
        ]

        for state in states:
            job = Job(
                job_type=JobType.DRIVER_UPDATE,
                state=state,
                software_title='Test'
            )
            self.session.add(job)
            self.session.commit()

            retrieved = self.session.query(Job).filter_by(id=job.id).first()
            self.assertEqual(retrieved.state, state)
            self.session.delete(retrieved)
            self.session.commit()

    def test_job_type_enum(self):
        """Test all job type enum values"""
        types = [
            JobType.DRIVER_UPDATE,
            JobType.SOFTWARE_UPDATE,
            JobType.NEW_SOFTWARE
        ]

        for job_type in types:
            job = Job(
                job_type=job_type,
                state=JobState.PENDING,
                software_title='Test'
            )
            self.session.add(job)
            self.session.commit()

            retrieved = self.session.query(Job).filter_by(id=job.id).first()
            self.assertEqual(retrieved.job_type, job_type)
            self.session.delete(retrieved)
            self.session.commit()

    def test_job_to_dict(self):
        """Test job to_dict() method"""
        now = datetime.utcnow()
        job = Job(
            job_type=JobType.DRIVER_UPDATE,
            state=JobState.PENDING,
            software_title='Test Driver',
            current_version='1.0',
            target_version='2.0',
            vendor='Dell',
            hardware_model='Latitude',
            driver_type='Chipset',
            download_url='http://test.com',
            created_at=now,
            retry_count=1,
            error_message='Error',
            job_metadata={'test': 'data'}
        )
        self.session.add(job)
        self.session.commit()

        result = job.to_dict()

        self.assertIsInstance(result, dict)
        self.assertEqual(result['job_type'], 'driver_update')
        self.assertEqual(result['state'], 'pending')
        self.assertEqual(result['software_title'], 'Test Driver')
        self.assertEqual(result['current_version'], '1.0')
        self.assertEqual(result['target_version'], '2.0')
        self.assertEqual(result['vendor'], 'Dell')
        self.assertEqual(result['hardware_model'], 'Latitude')
        self.assertEqual(result['driver_type'], 'Chipset')
        self.assertEqual(result['download_url'], 'http://test.com')
        self.assertEqual(result['retry_count'], 1)
        self.assertEqual(result['error_message'], 'Error')
        self.assertEqual(result['metadata'], {'test': 'data'})
        self.assertIsNotNone(result['created_at'])

    def test_job_repr(self):
        """Test job __repr__() method"""
        job = Job(
            job_type=JobType.DRIVER_UPDATE,
            state=JobState.PENDING,
            software_title='Test Driver'
        )
        self.session.add(job)
        self.session.commit()

        repr_str = repr(job)
        self.assertIn('Job', repr_str)
        self.assertIn('Test Driver', repr_str)
        self.assertIn('PENDING', repr_str)

    def test_job_default_metadata(self):
        """Test that job_metadata defaults to empty dict"""
        job = Job(
            job_type=JobType.DRIVER_UPDATE,
            state=JobState.PENDING,
            software_title='Test'
        )
        self.session.add(job)
        self.session.commit()

        self.assertEqual(job.job_metadata, {})

    def test_job_timestamps(self):
        """Test job timestamp behavior"""
        job = Job(
            job_type=JobType.DRIVER_UPDATE,
            state=JobState.PENDING,
            software_title='Test'
        )
        self.session.add(job)
        self.session.commit()

        # created_at should be set
        self.assertIsNotNone(job.created_at)

        # updated_at should be set
        self.assertIsNotNone(job.updated_at)

        # completed_at should not be set
        self.assertIsNone(job.completed_at)


class TestPackageModel(unittest.TestCase):
    """Test cases for Package model"""

    def setUp(self):
        """Set up test database"""
        self.engine = create_engine('sqlite:///:memory:', echo=False)
        Base.metadata.create_all(self.engine)
        Session = scoped_session(sessionmaker(bind=self.engine))
        self.session = Session()

    def tearDown(self):
        """Clean up test database"""
        self.session.rollback()
        self.session.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def test_package_creation_minimal(self):
        """Test creating a package with minimal required fields"""
        package = Package(
            name='Test Package',
            version='1.0.0',
            intunewin_path='/path/to/package.intunewin'
        )
        self.session.add(package)
        self.session.commit()

        self.assertIsNotNone(package.id)
        self.assertEqual(package.name, 'Test Package')
        self.assertEqual(package.version, '1.0.0')
        self.assertEqual(package.intunewin_path, '/path/to/package.intunewin')
        self.assertFalse(package.tested)
        self.assertFalse(package.deployed)
        self.assertIsNotNone(package.created_at)

    def test_package_creation_full(self):
        """Test creating a package with all fields"""
        now = datetime.utcnow()
        detection_rules = [
            {
                'type': 'registry',
                'path': 'HKLM\\SOFTWARE\\Test',
                'value': 'Version',
                'data': '1.0.0'
            }
        ]
        requirements = {
            'min_os_version': '10.0.19041',
            'architecture': 'x64'
        }
        vm_results = {
            'test_passed': True,
            'duration': 120.5
        }

        package = Package(
            name='Dell Chipset Driver',
            version='10.1.18383.8213',
            vendor='Dell',
            intunewin_path='/packages/dell_chipset.intunewin',
            installer_path='/packages/installer.exe',
            psadt_script_path='/packages/Deploy-Application.ps1',
            install_command='Deploy-Application.ps1 -DeploymentType Install',
            uninstall_command='Deploy-Application.ps1 -DeploymentType Uninstall',
            detection_rules=detection_rules,
            requirements=requirements,
            tested=True,
            test_passed=True,
            test_logs='All tests passed',
            vm_test_results=vm_results,
            intune_app_id='app-123',
            deployed=True,
            created_at=now,
            package_metadata={'source': 'catalog'}
        )
        self.session.add(package)
        self.session.commit()

        self.assertIsNotNone(package.id)
        self.assertEqual(package.name, 'Dell Chipset Driver')
        self.assertEqual(package.version, '10.1.18383.8213')
        self.assertEqual(package.vendor, 'Dell')
        self.assertEqual(package.installer_path, '/packages/installer.exe')
        self.assertEqual(package.install_command, 'Deploy-Application.ps1 -DeploymentType Install')
        self.assertEqual(package.detection_rules, detection_rules)
        self.assertEqual(package.requirements, requirements)
        self.assertTrue(package.tested)
        self.assertTrue(package.test_passed)
        self.assertEqual(package.vm_test_results, vm_results)
        self.assertEqual(package.intune_app_id, 'app-123')
        self.assertTrue(package.deployed)

    def test_package_to_dict(self):
        """Test package to_dict() method"""
        now = datetime.utcnow()
        package = Package(
            name='Test Package',
            version='1.0.0',
            vendor='Dell',
            intunewin_path='/test.intunewin',
            install_command='install.exe',
            uninstall_command='uninstall.exe',
            detection_rules=[{'type': 'file'}],
            tested=True,
            test_passed=True,
            vm_test_results={'passed': True},
            intune_app_id='app-123',
            deployed=True,
            created_at=now,
            package_metadata={'key': 'value'}
        )
        self.session.add(package)
        self.session.commit()

        result = package.to_dict()

        self.assertIsInstance(result, dict)
        self.assertEqual(result['name'], 'Test Package')
        self.assertEqual(result['version'], '1.0.0')
        self.assertEqual(result['vendor'], 'Dell')
        self.assertEqual(result['intunewin_path'], '/test.intunewin')
        self.assertEqual(result['install_command'], 'install.exe')
        self.assertEqual(result['uninstall_command'], 'uninstall.exe')
        self.assertEqual(result['detection_rules'], [{'type': 'file'}])
        self.assertTrue(result['tested'])
        self.assertTrue(result['test_passed'])
        self.assertEqual(result['vm_test_results'], {'passed': True})
        self.assertEqual(result['intune_app_id'], 'app-123')
        self.assertTrue(result['deployed'])
        self.assertIsNotNone(result['created_at'])
        self.assertEqual(result['metadata'], {'key': 'value'})

    def test_package_repr(self):
        """Test package __repr__() method"""
        package = Package(
            name='Test Package',
            version='1.0.0',
            intunewin_path='/test.intunewin'
        )
        self.session.add(package)
        self.session.commit()

        repr_str = repr(package)
        self.assertIn('Package', repr_str)
        self.assertIn('Test Package', repr_str)
        self.assertIn('1.0.0', repr_str)

    def test_package_default_values(self):
        """Test package default field values"""
        package = Package(
            name='Test',
            version='1.0',
            intunewin_path='/test'
        )
        self.session.add(package)
        self.session.commit()

        self.assertEqual(package.detection_rules, [])
        self.assertEqual(package.requirements, {})
        self.assertFalse(package.tested)
        self.assertIsNone(package.test_passed)
        self.assertFalse(package.deployed)
        self.assertEqual(package.vm_test_results, {})
        self.assertEqual(package.package_metadata, {})

    def test_package_boolean_flags(self):
        """Test package boolean flags"""
        package = Package(
            name='Test',
            version='1.0',
            intunewin_path='/test',
            tested=True,
            test_passed=False,
            deployed=True
        )
        self.session.add(package)
        self.session.commit()

        retrieved = self.session.query(Package).filter_by(id=package.id).first()
        self.assertTrue(retrieved.tested)
        self.assertFalse(retrieved.test_passed)
        self.assertTrue(retrieved.deployed)


class TestDeploymentModel(unittest.TestCase):
    """Test cases for Deployment model"""

    def setUp(self):
        """Set up test database"""
        self.engine = create_engine('sqlite:///:memory:', echo=False)
        Base.metadata.create_all(self.engine)
        Session = scoped_session(sessionmaker(bind=self.engine))
        self.session = Session()

        # Create a package for foreign key relationship
        self.package = Package(
            name='Test Package',
            version='1.0.0',
            intunewin_path='/test.intunewin'
        )
        self.session.add(self.package)
        self.session.commit()

    def tearDown(self):
        """Clean up test database"""
        self.session.rollback()
        self.session.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def test_deployment_creation_minimal(self):
        """Test creating a deployment with minimal required fields"""
        deployment = Deployment(
            package_id=self.package.id,
            intune_app_id='app-123',
            ring_id='pilot'
        )
        self.session.add(deployment)
        self.session.commit()

        self.assertIsNotNone(deployment.id)
        self.assertEqual(deployment.package_id, self.package.id)
        self.assertEqual(deployment.intune_app_id, 'app-123')
        self.assertEqual(deployment.ring_id, 'pilot')
        self.assertEqual(deployment.status, DeploymentStatus.PENDING)
        self.assertEqual(deployment.target_device_count, 0)
        self.assertIsNotNone(deployment.created_at)

    def test_deployment_creation_full(self):
        """Test creating a deployment with all fields"""
        now = datetime.utcnow()
        device_status = [
            {'device_id': 'device-1', 'status': 'success'},
            {'device_id': 'device-2', 'status': 'pending'}
        ]

        deployment = Deployment(
            package_id=self.package.id,
            intune_app_id='app-123',
            intune_assignment_id='assignment-456',
            ring_id='pilot',
            ring_name='Pilot Ring',
            entra_group_id='group-789',
            status=DeploymentStatus.IN_PROGRESS,
            target_device_count=100,
            successful_installs=75,
            failed_installs=5,
            pending_installs=15,
            not_applicable_installs=5,
            created_at=now,
            error_message='Some error',
            deployment_metadata={'key': 'value'},
            device_status_details=device_status
        )
        self.session.add(deployment)
        self.session.commit()

        self.assertIsNotNone(deployment.id)
        self.assertEqual(deployment.package_id, self.package.id)
        self.assertEqual(deployment.intune_app_id, 'app-123')
        self.assertEqual(deployment.ring_name, 'Pilot Ring')
        self.assertEqual(deployment.entra_group_id, 'group-789')
        self.assertEqual(deployment.target_device_count, 100)
        self.assertEqual(deployment.successful_installs, 75)
        self.assertEqual(deployment.failed_installs, 5)
        self.assertEqual(deployment.pending_installs, 15)
        self.assertEqual(deployment.not_applicable_installs, 5)
        self.assertEqual(deployment.error_message, 'Some error')
        self.assertEqual(deployment.deployment_metadata, {'key': 'value'})
        self.assertEqual(deployment.device_status_details, device_status)

    def test_deployment_status_enum(self):
        """Test all deployment status enum values"""
        statuses = [
            DeploymentStatus.PENDING,
            DeploymentStatus.IN_PROGRESS,
            DeploymentStatus.SUCCESSFUL,
            DeploymentStatus.FAILED,
            DeploymentStatus.SUPERSEDED
        ]

        for status in statuses:
            deployment = Deployment(
                package_id=self.package.id,
                intune_app_id=f'app-{status.value}',
                ring_id='test',
                status=status
            )
            self.session.add(deployment)
            self.session.commit()

            retrieved = self.session.query(Deployment).filter_by(id=deployment.id).first()
            self.assertEqual(retrieved.status, status)
            self.session.delete(retrieved)
            self.session.commit()

    def test_deployment_to_dict(self):
        """Test deployment to_dict() method"""
        now = datetime.utcnow()
        deployment = Deployment(
            package_id=self.package.id,
            intune_app_id='app-123',
            ring_id='pilot',
            ring_name='Pilot Ring',
            status=DeploymentStatus.IN_PROGRESS,
            target_device_count=100,
            successful_installs=75,
            failed_installs=5,
            pending_installs=15,
            not_applicable_installs=5,
            created_at=now,
            error_message='Error',
            deployment_metadata={'key': 'value'},
            device_status_details=[{'id': '1'}]
        )
        self.session.add(deployment)
        self.session.commit()

        result = deployment.to_dict()

        self.assertIsInstance(result, dict)
        self.assertEqual(result['package_id'], self.package.id)
        self.assertEqual(result['intune_app_id'], 'app-123')
        self.assertEqual(result['ring_id'], 'pilot')
        self.assertEqual(result['ring_name'], 'Pilot Ring')
        self.assertEqual(result['status'], 'in_progress')
        self.assertEqual(result['target_device_count'], 100)
        self.assertEqual(result['successful_installs'], 75)
        self.assertEqual(result['failed_installs'], 5)
        self.assertEqual(result['pending_installs'], 15)
        self.assertEqual(result['not_applicable_installs'], 5)
        self.assertEqual(result['error_message'], 'Error')
        self.assertEqual(result['metadata'], {'key': 'value'})
        self.assertEqual(result['device_status_details'], [{'id': '1'}])
        self.assertIsNotNone(result['created_at'])

    def test_deployment_repr(self):
        """Test deployment __repr__() method"""
        deployment = Deployment(
            package_id=self.package.id,
            intune_app_id='app-123',
            ring_id='pilot',
            status=DeploymentStatus.PENDING
        )
        self.session.add(deployment)
        self.session.commit()

        repr_str = repr(deployment)
        self.assertIn('Deployment', repr_str)
        self.assertIn('pilot', repr_str)
        self.assertIn('PENDING', repr_str)

    def test_deployment_default_counters(self):
        """Test deployment counter defaults"""
        deployment = Deployment(
            package_id=self.package.id,
            intune_app_id='app-123',
            ring_id='test'
        )
        self.session.add(deployment)
        self.session.commit()

        self.assertEqual(deployment.target_device_count, 0)
        self.assertEqual(deployment.successful_installs, 0)
        self.assertEqual(deployment.failed_installs, 0)
        self.assertEqual(deployment.pending_installs, 0)
        self.assertEqual(deployment.not_applicable_installs, 0)

    def test_deployment_foreign_key_relationship(self):
        """Test deployment foreign key to package"""
        deployment = Deployment(
            package_id=self.package.id,
            intune_app_id='app-123',
            ring_id='test'
        )
        self.session.add(deployment)
        self.session.commit()

        # Verify the foreign key relationship
        self.assertEqual(deployment.package_id, self.package.id)

        # Query by package_id
        deployments = self.session.query(Deployment).filter_by(package_id=self.package.id).all()
        self.assertEqual(len(deployments), 1)
        self.assertEqual(deployments[0].id, deployment.id)


class TestDiscoveryRunModel(unittest.TestCase):
    """Test cases for DiscoveryRun model"""

    def setUp(self):
        """Set up test database"""
        self.engine = create_engine('sqlite:///:memory:', echo=False)
        Base.metadata.create_all(self.engine)
        Session = scoped_session(sessionmaker(bind=self.engine))
        self.session = Session()

    def tearDown(self):
        """Clean up test database"""
        self.session.rollback()
        self.session.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def test_discovery_run_creation_minimal(self):
        """Test creating a discovery run with minimal fields"""
        run = DiscoveryRun()
        self.session.add(run)
        self.session.commit()

        self.assertIsNotNone(run.id)
        self.assertIsNotNone(run.started_at)
        self.assertEqual(run.catalogs_scanned, 0)
        self.assertEqual(run.new_versions_found, 0)
        self.assertEqual(run.jobs_created, 0)

    def test_discovery_run_creation_full(self):
        """Test creating a discovery run with all fields"""
        now = datetime.utcnow()
        oem_data = {
            'dell': {
                'drivers_found': 5,
                'new_versions': 2
            },
            'hp': {
                'drivers_found': 3,
                'new_versions': 1
            }
        }

        run = DiscoveryRun(
            started_at=now,
            completed_at=now,
            catalogs_scanned=3,
            new_versions_found=3,
            jobs_created=3,
            oem_results=oem_data,
            error_message='Test error'
        )
        self.session.add(run)
        self.session.commit()

        self.assertIsNotNone(run.id)
        self.assertEqual(run.catalogs_scanned, 3)
        self.assertEqual(run.new_versions_found, 3)
        self.assertEqual(run.jobs_created, 3)
        self.assertEqual(run.oem_results, oem_data)
        self.assertEqual(run.error_message, 'Test error')

    def test_discovery_run_to_dict(self):
        """Test discovery run to_dict() method"""
        now = datetime.utcnow()
        run = DiscoveryRun(
            started_at=now,
            completed_at=now,
            catalogs_scanned=2,
            new_versions_found=1,
            jobs_created=1,
            oem_results={'dell': {'found': 1}},
            error_message='Error'
        )
        self.session.add(run)
        self.session.commit()

        result = run.to_dict()

        self.assertIsInstance(result, dict)
        self.assertIsNotNone(result['started_at'])
        self.assertIsNotNone(result['completed_at'])
        self.assertEqual(result['catalogs_scanned'], 2)
        self.assertEqual(result['new_versions_found'], 1)
        self.assertEqual(result['jobs_created'], 1)
        self.assertEqual(result['oem_results'], {'dell': {'found': 1}})
        self.assertEqual(result['error_message'], 'Error')

    def test_discovery_run_repr(self):
        """Test discovery run __repr__() method"""
        run = DiscoveryRun(
            catalogs_scanned=3,
            new_versions_found=5,
            jobs_created=2
        )
        self.session.add(run)
        self.session.commit()

        repr_str = repr(run)
        self.assertIn('DiscoveryRun', repr_str)
        self.assertIn('3', repr_str)  # catalogs_scanned
        self.assertIn('5', repr_str)  # new_versions_found
        self.assertIn('2', repr_str)  # jobs_created

    def test_discovery_run_default_oem_results(self):
        """Test that oem_results defaults to empty dict"""
        run = DiscoveryRun()
        self.session.add(run)
        self.session.commit()

        self.assertEqual(run.oem_results, {})

    def test_discovery_run_metrics(self):
        """Test discovery run metric tracking"""
        run = DiscoveryRun(
            catalogs_scanned=5,
            new_versions_found=10,
            jobs_created=8
        )
        self.session.add(run)
        self.session.commit()

        retrieved = self.session.query(DiscoveryRun).filter_by(id=run.id).first()
        self.assertEqual(retrieved.catalogs_scanned, 5)
        self.assertEqual(retrieved.new_versions_found, 10)
        self.assertEqual(retrieved.jobs_created, 8)


if __name__ == '__main__':
    unittest.main()
