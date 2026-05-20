"""Unit tests for PackagingAgent packaging functionality"""

import unittest
from unittest.mock import Mock, patch, MagicMock, mock_open
from pathlib import Path
import hashlib

from autopackager.agents.packaging.packaging_agent import PackagingAgent
from autopackager.models.job import Job
from autopackager.models.package import Package


class TestPackagingAgentPackaging(unittest.TestCase):
    """Test cases for PackagingAgent packaging methods"""

    def setUp(self):
        """Set up test fixtures"""
        with patch('autopackager.agents.packaging.packaging_agent.get_config'):
            self.agent = PackagingAgent()
            self.agent.downloads_path = Path('/test/downloads')
            self.agent.packages_path = Path('/test/packages')
            self.agent.intunewin_util = Path('/test/IntuneWinAppUtil.exe')

        self.job = Mock(spec=Job)
        self.job.id = 1
        self.job.software_title = 'Test Driver'
        self.job.vendor = 'TestVendor'
        self.job.download_url = 'https://example.com/driver.exe'
        self.job.job_metadata = {
            'download_url': 'https://example.com/driver.exe',
            'target_version': '1.0.0',
            'release_notes': 'Test release'
        }

    @patch('autopackager.agents.packaging.packaging_agent.PackagingAgent._save_package')
    @patch('autopackager.agents.packaging.packaging_agent.PackagingAgent._generate_detection_rules')
    @patch('autopackager.agents.packaging.packaging_agent.PackagingAgent._create_intunewin_package')
    @patch('autopackager.agents.packaging.packaging_agent.PackagingAgent._generate_install_commands')
    @patch('autopackager.agents.packaging.packaging_agent.Path.mkdir')
    @patch('autopackager.agents.packaging.packaging_agent.Path.rename')
    @patch('autopackager.agents.packaging.packaging_agent.PackagingAgent._download_installer')
    def test_package_success(self, mock_download, mock_rename, mock_mkdir,
                            mock_gen_install, mock_intunewin, mock_detection, mock_save):
        """Test successful packaging workflow"""
        # Mock download
        installer_path = Path('/test/downloads/driver.exe')
        mock_download.return_value = installer_path

        # Mock install command generation
        mock_gen_install.return_value = ('driver.exe /S', 'driver.exe /uninstall')

        # Mock intunewin creation
        intunewin_path = Path('/test/packages/Test_Driver_1.0.0/output/driver.intunewin')
        mock_intunewin.return_value = intunewin_path

        # Mock detection rules
        mock_detection.return_value = [{'rule': 'test'}]

        # Mock package save
        mock_package = Mock(spec=Package)
        mock_package.id = 1
        mock_save.return_value = mock_package

        result = self.agent.package(self.job)

        # Verify workflow
        mock_download.assert_called_once()
        mock_gen_install.assert_called_once()
        mock_intunewin.assert_called_once()
        mock_detection.assert_called_once()
        mock_save.assert_called_once()

        # Verify result
        self.assertEqual(result['package_id'], 1)
        self.assertIn('intunewin_path', result)
        self.assertIn('install_command', result)

    def test_package_raises_error_without_download_url(self):
        """Test that packaging fails when no download URL is provided"""
        self.job.download_url = None
        self.job.job_metadata = {}

        with self.assertRaises(ValueError) as context:
            self.agent.package(self.job)

        self.assertIn('download URL', str(context.exception))

    @patch('autopackager.agents.packaging.packaging_agent.requests.get')
    @patch('autopackager.agents.packaging.packaging_agent.PackagingAgent._calculate_file_hash')
    @patch('builtins.open', new_callable=mock_open)
    @patch('autopackager.agents.packaging.packaging_agent.Path.stat')
    def test_download_installer_success(self, mock_stat, mock_file, mock_hash, mock_get):
        """Test successful installer download"""
        # Mock response
        mock_response = Mock()
        mock_response.headers = {'content-length': '1024'}
        mock_response.iter_content = Mock(return_value=[b'data'])
        mock_get.return_value = mock_response

        # Mock file size
        mock_stat_result = Mock()
        mock_stat_result.st_size = 1024
        mock_stat.return_value = mock_stat_result

        # Mock hash
        mock_hash.return_value = 'abc123def456'

        result = self.agent._download_installer('https://example.com/driver.exe', self.job)

        # Verify download
        mock_get.assert_called_once()
        self.assertEqual(result.name, 'driver.exe')

    @patch('autopackager.agents.packaging.packaging_agent.requests.get')
    def test_download_installer_handles_http_error(self, mock_get):
        """Test handling of HTTP errors during download"""
        mock_get.side_effect = Exception('HTTP 404')

        with self.assertRaises(Exception) as context:
            self.agent._download_installer('https://example.com/missing.exe', self.job)

        self.assertIn('404', str(context.exception))

    @patch('autopackager.agents.packaging.packaging_agent.requests.get')
    @patch('builtins.open', new_callable=mock_open)
    @patch('autopackager.agents.packaging.packaging_agent.Path.stat')
    def test_download_installer_detects_incomplete_download(self, mock_stat, mock_file, mock_get):
        """Test detection of incomplete downloads"""
        # Mock response with content-length
        mock_response = Mock()
        mock_response.headers = {'content-length': '2048'}
        mock_response.iter_content = Mock(return_value=[b'data'])
        mock_get.return_value = mock_response

        # Mock file size (smaller than expected)
        mock_stat_result = Mock()
        mock_stat_result.st_size = 1024
        mock_stat.return_value = mock_stat_result

        with self.assertRaises(Exception) as context:
            self.agent._download_installer('https://example.com/driver.exe', self.job)

        self.assertIn('incomplete', str(context.exception).lower())

    def test_calculate_file_hash(self):
        """Test file hash calculation"""
        test_content = b'test file content'
        expected_hash = hashlib.sha256(test_content).hexdigest()

        with patch('builtins.open', mock_open(read_data=test_content)):
            result = self.agent._calculate_file_hash(Path('/test/file.txt'))

        self.assertEqual(result, expected_hash)

    def test_guess_file_extension_exe(self):
        """Test extension guessing for EXE files"""
        result = self.agent._guess_file_extension('https://example.com/installer.exe')
        self.assertEqual(result, '.exe')

    def test_guess_file_extension_msi(self):
        """Test extension guessing for MSI files"""
        result = self.agent._guess_file_extension('https://example.com/setup.msi')
        self.assertEqual(result, '.msi')

    def test_guess_file_extension_cab(self):
        """Test extension guessing for CAB files"""
        result = self.agent._guess_file_extension('https://example.com/drivers.cab')
        self.assertEqual(result, '.cab')

    def test_guess_file_extension_default(self):
        """Test default extension when unknown"""
        result = self.agent._guess_file_extension('https://example.com/unknown')
        self.assertEqual(result, '.exe')

    @patch('autopackager.agents.packaging.packaging_agent.datetime')
    def test_create_package_name(self, mock_datetime):
        """Test package name generation"""
        mock_now = Mock()
        mock_now.strftime.return_value = '20240427_123000'
        mock_datetime.now.return_value = mock_now

        result = self.agent._create_package_name(self.job)

        self.assertIn('Test_Driver', result)
        self.assertIn('1.0.0', result)
        self.assertIn('20240427_123000', result)

    def test_generate_install_commands_exe(self):
        """Test install command generation for EXE files"""
        installer_path = Path('/test/installer.exe')

        install_cmd, uninstall_cmd = self.agent._generate_install_commands(self.job, installer_path)

        self.assertIn('installer.exe', install_cmd)
        self.assertIn('/S', install_cmd)
        self.assertIn('uninstall', uninstall_cmd)

    def test_generate_install_commands_msi(self):
        """Test install command generation for MSI files"""
        installer_path = Path('/test/setup.msi')

        install_cmd, uninstall_cmd = self.agent._generate_install_commands(self.job, installer_path)

        self.assertIn('msiexec', install_cmd)
        self.assertIn('/i', install_cmd)
        self.assertIn('setup.msi', install_cmd)
        self.assertIn('/quiet', install_cmd)

    @patch('autopackager.agents.packaging.packaging_agent.PackagingAgent._generate_cab_install_script')
    def test_generate_install_commands_cab(self, mock_gen_script):
        """Test install command generation for CAB driver packs"""
        installer_path = Path('/test/drivers.cab')
        mock_gen_script.return_value = 'Install-DriverPack.ps1'

        install_cmd, uninstall_cmd = self.agent._generate_install_commands(self.job, installer_path)

        # Verify PowerShell wrapper is used
        self.assertIn('PowerShell.exe', install_cmd)
        self.assertIn('Install-DriverPack.ps1', install_cmd)
        self.assertIn('-ExecutionPolicy Bypass', install_cmd)
        mock_gen_script.assert_called_once_with(installer_path)

    @patch('autopackager.agents.packaging.packaging_agent.Path.write_text')
    def test_generate_cab_install_script(self, mock_write):
        """Test CAB install script generation"""
        installer_path = Path('/test/package/drivers.cab')

        result = self.agent._generate_cab_install_script(installer_path)

        # Verify script name
        self.assertEqual(result, 'Install-DriverPack.ps1')

        # Verify script was written
        mock_write.assert_called_once()
        script_content = mock_write.call_args[0][0]

        # Verify script contains required elements
        self.assertIn('drivers.cab', script_content)
        self.assertIn('expand.exe', script_content)
        self.assertIn('pnputil.exe', script_content)
        self.assertIn('/add-driver', script_content)

    @patch('autopackager.agents.packaging.packaging_agent.subprocess.run')
    @patch('autopackager.agents.packaging.packaging_agent.Path.mkdir')
    @patch('autopackager.agents.packaging.packaging_agent.Path.exists')
    @patch('autopackager.agents.packaging.packaging_agent.Path.glob')
    def test_create_intunewin_package_success(self, mock_glob, mock_exists, mock_mkdir, mock_run):
        """Test successful .intunewin package creation"""
        mock_exists.return_value = True
        installer_path = Path('/test/package/installer.exe')
        package_dir = Path('/test/package')

        # Mock intunewin file creation
        intunewin_file = Mock()
        intunewin_file.name = 'installer.intunewin'
        mock_glob.return_value = [intunewin_file]

        # Mock subprocess success
        mock_result = Mock()
        mock_result.stdout = 'Package created successfully'
        mock_run.return_value = mock_result

        result = self.agent._create_intunewin_package(package_dir, installer_path)

        # Verify subprocess was called correctly
        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        self.assertIn('-c', call_args)
        self.assertIn('-s', call_args)
        self.assertIn('-o', call_args)

    @patch('autopackager.agents.packaging.packaging_agent.Path.exists')
    @patch('autopackager.agents.packaging.packaging_agent.Path.touch')
    @patch('autopackager.agents.packaging.packaging_agent.Path.mkdir')
    def test_create_intunewin_package_simulates_when_util_missing(self, mock_mkdir, mock_touch, mock_exists):
        """Test that missing IntuneWinAppUtil.exe creates placeholder"""
        mock_exists.return_value = False
        installer_path = Path('/test/package/installer.exe')
        package_dir = Path('/test/package')

        result = self.agent._create_intunewin_package(package_dir, installer_path)

        # Verify placeholder was created
        mock_touch.assert_called_once()

    @patch('autopackager.agents.packaging.packaging_agent.subprocess.run')
    @patch('autopackager.agents.packaging.packaging_agent.Path.mkdir')
    @patch('autopackager.agents.packaging.packaging_agent.Path.exists')
    def test_create_intunewin_package_handles_subprocess_error(self, mock_exists, mock_mkdir, mock_run):
        """Test handling of subprocess errors during package creation"""
        mock_exists.return_value = True
        mock_run.side_effect = Exception('IntuneWinAppUtil.exe failed')

        installer_path = Path('/test/package/installer.exe')
        package_dir = Path('/test/package')

        with self.assertRaises(Exception):
            self.agent._create_intunewin_package(package_dir, installer_path)

    def test_generate_detection_rules(self):
        """Test detection rule generation for Intune"""
        result = self.agent._generate_detection_rules(self.job)

        # Verify structure
        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 0)

        # Verify first rule
        rule = result[0]
        self.assertEqual(rule['@odata.type'], '#microsoft.graph.win32LobAppRegistryRule')
        self.assertEqual(rule['ruleType'], 'detection')
        self.assertIn('Testvendor', rule['keyPath'])
        self.assertEqual(rule['valueName'], '1.0.0')
        self.assertEqual(rule['operationType'], 'exists')

    @patch('autopackager.agents.packaging.packaging_agent.db_session_scope')
    def test_save_package(self, mock_db_session):
        """Test saving package to database"""
        # Mock session
        mock_session = Mock()
        mock_db_session.return_value.__enter__ = Mock(return_value=mock_session)
        mock_db_session.return_value.__exit__ = Mock(return_value=False)

        # Mock package with ID after flush
        def set_package_id(*args, **kwargs):
            if hasattr(mock_session.add.call_args[0][0], 'id'):
                mock_session.add.call_args[0][0].id = 123

        mock_session.flush.side_effect = set_package_id

        # Mock _get_package
        with patch.object(self.agent, '_get_package') as mock_get:
            mock_package = Mock(spec=Package)
            mock_package.id = 123
            mock_get.return_value = mock_package

            result = self.agent._save_package(
                self.job,
                Path('/test/package.intunewin'),
                Path('/test/installer.exe'),
                'installer.exe /S',
                'installer.exe /uninstall',
                [{'rule': 'test'}]
            )

        # Verify package was saved
        mock_session.add.assert_called_once()
        added_package = mock_session.add.call_args[0][0]
        self.assertEqual(added_package.name, 'Test Driver')
        self.assertEqual(added_package.version, '1.0.0')
        self.assertEqual(added_package.vendor, 'TestVendor')

    @patch('autopackager.agents.packaging.packaging_agent.requests.get')
    @patch('autopackager.agents.packaging.packaging_agent.PackagingAgent._calculate_file_hash')
    @patch('builtins.open', new_callable=mock_open)
    @patch('autopackager.agents.packaging.packaging_agent.Path.stat')
    def test_download_installer_generates_filename_when_missing(self, mock_stat, mock_file, mock_hash, mock_get):
        """Test filename generation when URL doesn't contain a filename"""
        # Mock response
        mock_response = Mock()
        mock_response.headers = {'content-length': '1024'}
        mock_response.iter_content = Mock(return_value=[b'data'])
        mock_get.return_value = mock_response

        # Mock file size
        mock_stat_result = Mock()
        mock_stat_result.st_size = 1024
        mock_stat.return_value = mock_stat_result

        # Mock hash
        mock_hash.return_value = 'abc123'

        # URL without filename
        result = self.agent._download_installer('https://example.com/download?id=123', self.job)

        # Verify filename was generated
        self.assertIn('Test_Driver', result.name)
        self.assertIn('1.0.0', result.name)

    def test_generate_install_commands_unknown_type(self):
        """Test install command generation for unknown file types"""
        installer_path = Path('/test/unknown.bin')

        install_cmd, uninstall_cmd = self.agent._generate_install_commands(self.job, installer_path)

        # Verify defaults are used
        self.assertEqual(install_cmd, 'unknown.bin')
        self.assertEqual(uninstall_cmd, 'cmd /c exit 0')

    def _msi_job(self):
        """Build a job representing an MSI software package with metadata."""
        job = Mock(spec=Job)
        job.id = 2
        job.software_title = '7-Zip 24.08 (x64)'
        job.vendor = 'Igor Pavlov'
        job.download_url = '/tmp/7z2408-x64.msi'
        job.job_metadata = {
            'install_command': 'msiexec.exe /i 7z2408-x64.msi /qn /norestart',
            'target_version': '24.08.00.0',
            'msi_metadata': {
                'product_code': '{23170F69-40C1-2702-2408-000001000000}',
                'product_version': '24.08.00.0',
                'upgrade_code': '{23170F69-40C1-2702-0000-000004000000}',
            },
        }
        return job

    def test_generate_install_commands_msi_uses_admin_command(self):
        """MSI install command honors the admin-provided switches."""
        job = self._msi_job()
        installer_path = Path('/test/7z2408-x64.msi')

        install_cmd, uninstall_cmd = self.agent._generate_install_commands(job, installer_path)

        self.assertEqual(install_cmd, 'msiexec /i 7z2408-x64.msi /qn /norestart')
        # Uninstall prefers the product code from the MSI metadata
        self.assertIn('/x {23170F69-40C1-2702-2408-000001000000}', uninstall_cmd)
        self.assertIn('/qn', uninstall_cmd)

    def test_generate_install_commands_msi_default_without_command(self):
        """MSI without an admin command falls back to a sensible default."""
        installer_path = Path('/test/setup.msi')

        install_cmd, uninstall_cmd = self.agent._generate_install_commands(self.job, installer_path)

        self.assertEqual(install_cmd, 'msiexec /i setup.msi /quiet /norestart')
        self.assertEqual(uninstall_cmd, 'msiexec /x setup.msi /quiet /norestart')

    def test_generate_detection_rules_msi_product_code(self):
        """MSI metadata produces a product-code detection rule."""
        job = self._msi_job()

        rules = self.agent._generate_detection_rules(job)

        self.assertEqual(len(rules), 1)
        rule = rules[0]
        self.assertEqual(rule['@odata.type'], '#microsoft.graph.win32LobAppProductCodeRule')
        self.assertEqual(rule['productCode'], '{23170F69-40C1-2702-2408-000001000000}')
        self.assertEqual(rule['productVersion'], '24.08.00.0')

    @patch('autopackager.agents.packaging.packaging_agent.shutil.copy2')
    @patch('autopackager.agents.packaging.packaging_agent.PackagingAgent._calculate_file_hash')
    @patch('autopackager.agents.packaging.packaging_agent.Path.exists')
    def test_download_installer_copies_local_file(self, mock_exists, mock_hash, mock_copy):
        """A local installer path is copied rather than downloaded over HTTP."""
        mock_exists.return_value = True
        mock_hash.return_value = 'abc123'

        result = self.agent._download_installer('/tmp/7z2408-x64.msi', self.job)

        mock_copy.assert_called_once()
        self.assertEqual(result.name, '7z2408-x64.msi')


if __name__ == '__main__':
    unittest.main()
