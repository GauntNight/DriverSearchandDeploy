"""Unit tests for Discovery Agent"""

import unittest
from unittest.mock import Mock, patch, MagicMock, mock_open
from datetime import datetime, timedelta
from pathlib import Path

from autopackager.agents.discovery.discovery_agent import DiscoveryAgent
from autopackager.models.job import Job, JobType


class TestDiscoveryAgentCore(unittest.TestCase):
    """Test cases for Discovery Agent core functionality"""

    def setUp(self):
        """Set up test fixtures"""
        # Mock config
        self.mock_config = {
            'oem_catalogs': {
                'dell': {
                    'catalog_url': 'https://downloads.dell.com/catalog/DriverPackCatalog.cab',
                    'catalog_path': '/tmp/dell_catalog',
                    'base_url': 'https://downloads.dell.com'
                },
                'hp': {
                    'catalog_url': 'https://hpia.hpcloud.hp.com/downloads/platformlist.cab',
                    'catalog_path': '/tmp/hp_catalog'
                },
                'lenovo': {
                    'catalog_url': 'https://download.lenovo.com/catalog/catalogv2.xml',
                    'catalog_path': '/tmp/lenovo_catalog'
                }
            }
        }

        # Create agent with mocked config
        with patch('autopackager.agents.discovery.discovery_agent.get_config', return_value=self.mock_config):
            self.agent = DiscoveryAgent()

        # Sample job
        self.driver_job = Mock(spec=Job)
        self.driver_job.id = 1
        self.driver_job.job_type = JobType.DRIVER_UPDATE
        self.driver_job.software_title = 'Dell Latitude 7420 Drivers'
        self.driver_job.vendor = 'dell'
        self.driver_job.hardware_model = 'Latitude 7420'
        self.driver_job.driver_type = 'chipset'
        self.driver_job.current_version = 'A00'

        self.software_job = Mock(spec=Job)
        self.software_job.id = 2
        self.software_job.job_type = JobType.SOFTWARE_UPDATE
        self.software_job.software_title = 'Adobe Acrobat Reader DC'
        self.software_job.vendor = 'adobe'
        self.software_job.current_version = '2023.001.20093'

    def test_discover_routes_to_driver_discovery(self):
        """Test that discover routes driver jobs correctly"""
        with patch.object(self.agent, '_discover_driver', return_value={'update_available': True}):
            result = self.agent.discover(self.driver_job)

            self.agent._discover_driver.assert_called_once_with(self.driver_job)
            self.assertTrue(result['update_available'])

    def test_discover_routes_to_software_discovery(self):
        """Test that discover routes software jobs correctly"""
        with patch.object(self.agent, '_discover_software', return_value={'update_available': False}):
            result = self.agent.discover(self.software_job)

            self.agent._discover_software.assert_called_once_with(self.software_job)
            self.assertFalse(result['update_available'])

    def test_discover_driver_routes_to_dell(self):
        """Test that _discover_driver routes to Dell correctly"""
        self.driver_job.vendor = 'dell'

        with patch.object(self.agent, '_discover_dell_driver', return_value={'update_available': True}):
            result = self.agent._discover_driver(self.driver_job)

            self.agent._discover_dell_driver.assert_called_once_with(self.driver_job)
            self.assertTrue(result['update_available'])

    def test_discover_driver_routes_to_hp(self):
        """Test that _discover_driver routes to HP correctly"""
        self.driver_job.vendor = 'hp'

        with patch.object(self.agent, '_discover_hp_driver', return_value={'update_available': True}):
            result = self.agent._discover_driver(self.driver_job)

            self.agent._discover_hp_driver.assert_called_once_with(self.driver_job)
            self.assertTrue(result['update_available'])

    def test_discover_driver_routes_to_lenovo(self):
        """Test that _discover_driver routes to Lenovo correctly"""
        self.driver_job.vendor = 'lenovo'

        with patch.object(self.agent, '_discover_lenovo_driver', return_value={'update_available': True}):
            result = self.agent._discover_driver(self.driver_job)

            self.agent._discover_lenovo_driver.assert_called_once_with(self.driver_job)
            self.assertTrue(result['update_available'])

    def test_discover_driver_rejects_unsupported_vendor(self):
        """Test that unsupported vendor raises ValueError"""
        self.driver_job.vendor = 'unsupported_oem'

        with self.assertRaises(ValueError) as context:
            self.agent._discover_driver(self.driver_job)

        self.assertIn('Unsupported OEM vendor', str(context.exception))


class TestDiscoveryAgentDell(unittest.TestCase):
    """Test cases for Dell driver discovery"""

    def setUp(self):
        """Set up test fixtures"""
        self.mock_config = {
            'oem_catalogs': {
                'dell': {
                    'catalog_url': 'https://downloads.dell.com/catalog/DriverPackCatalog.cab',
                    'catalog_path': '/tmp/dell_catalog',
                    'base_url': 'https://downloads.dell.com'
                }
            }
        }

        with patch('autopackager.agents.discovery.discovery_agent.get_config', return_value=self.mock_config):
            self.agent = DiscoveryAgent()

        self.job = Mock(spec=Job)
        self.job.id = 1
        self.job.hardware_model = 'Latitude 7420'
        self.job.driver_type = 'chipset'
        self.job.current_version = 'A00'
        self.job.vendor = 'dell'

        # Sample Dell catalog XML
        self.sample_catalog_data = {
            'DriverPackManifest': {
                'DriverPackage': {
                    '@name': 'Latitude 7420 Driver Pack',
                    '@dellVersion': 'A01',
                    '@path': 'FOLDER01234/Latitude_7420_A01.CAB',
                    '@releaseNotes': 'Updated drivers',
                    '@dateTime': '2024-01-15',
                    '@size': 1024000,
                    'SupportedSystems': {
                        'Brand': {
                            'Model': {
                                '@name': 'Latitude 7420'
                            }
                        }
                    }
                }
            }
        }

    @patch('autopackager.agents.discovery.discovery_agent.xmltodict.parse')
    def test_discover_dell_driver_success(self, mock_xmltodict):
        """Test successful Dell driver discovery"""
        mock_xmltodict.return_value = self.sample_catalog_data

        with patch.object(self.agent, '_download_dell_catalog', return_value='<xml>'):
            result = self.agent._discover_dell_driver(self.job)

            self.assertTrue(result['update_available'])
            self.assertEqual(result['latest_version'], 'A01')
            self.assertIn('FOLDER01234/Latitude_7420_A01.CAB', result['download_url'])
            self.assertEqual(result['release_notes'], 'Updated drivers')
            self.assertEqual(result['release_date'], '2024-01-15')
            self.assertEqual(result['file_size'], 1024000)

    @patch('autopackager.agents.discovery.discovery_agent.xmltodict.parse')
    def test_discover_dell_driver_no_match(self, mock_xmltodict):
        """Test Dell driver discovery when no matching driver pack found"""
        mock_xmltodict.return_value = self.sample_catalog_data

        self.job.hardware_model = 'NonExistentModel'

        with patch.object(self.agent, '_download_dell_catalog', return_value='<xml>'):
            result = self.agent._discover_dell_driver(self.job)

            self.assertFalse(result['update_available'])

    @patch('autopackager.agents.discovery.discovery_agent.xmltodict.parse')
    def test_discover_dell_driver_handles_catalog_error(self, mock_xmltodict):
        """Test handling of catalog download errors"""
        with patch.object(self.agent, '_download_dell_catalog', side_effect=Exception('Network error')):
            with self.assertRaises(Exception):
                self.agent._discover_dell_driver(self.job)

    def test_find_dell_driver_pack_with_matching_model(self):
        """Test finding driver pack with matching model"""
        result = self.agent._find_dell_driver_pack(
            self.sample_catalog_data,
            'Latitude 7420',
            'chipset'
        )

        self.assertIsNotNone(result)
        self.assertEqual(result['name'], 'Latitude 7420 Driver Pack')
        self.assertEqual(result['dellVersion'], 'A01')

    def test_find_dell_driver_pack_handles_multiple_brands(self):
        """Test handling of multiple brands in catalog"""
        catalog_data = {
            'DriverPackManifest': {
                'DriverPackage': {
                    '@name': 'Multi-Model Driver Pack',
                    '@dellVersion': 'A01',
                    '@path': 'FOLDER/driver.CAB',
                    'SupportedSystems': {
                        'Brand': [
                            {
                                'Model': {'@name': 'Latitude 5420'}
                            },
                            {
                                'Model': {'@name': 'Latitude 7420'}
                            }
                        ]
                    }
                }
            }
        }

        result = self.agent._find_dell_driver_pack(catalog_data, 'Latitude 7420')

        self.assertIsNotNone(result)
        self.assertEqual(result['name'], 'Multi-Model Driver Pack')

    def test_find_dell_driver_pack_handles_multiple_models(self):
        """Test handling of multiple models per brand"""
        catalog_data = {
            'DriverPackManifest': {
                'DriverPackage': {
                    '@name': 'Multi-Model Driver Pack',
                    '@dellVersion': 'A01',
                    '@path': 'FOLDER/driver.CAB',
                    'SupportedSystems': {
                        'Brand': {
                            'Model': [
                                {'@name': 'Latitude 5420'},
                                {'@name': 'Latitude 7420'}
                            ]
                        }
                    }
                }
            }
        }

        result = self.agent._find_dell_driver_pack(catalog_data, 'Latitude 7420')

        self.assertIsNotNone(result)

    def test_find_dell_driver_pack_returns_none_on_no_match(self):
        """Test that None is returned when no match found"""
        result = self.agent._find_dell_driver_pack(
            self.sample_catalog_data,
            'NonExistentModel'
        )

        self.assertIsNone(result)

    def test_find_dell_driver_pack_handles_parsing_errors(self):
        """Test handling of malformed catalog data"""
        malformed_data = {'DriverPackManifest': None}

        result = self.agent._find_dell_driver_pack(malformed_data, 'Latitude 7420')

        self.assertIsNone(result)

    @patch('autopackager.agents.discovery.discovery_agent.requests.get')
    @patch('autopackager.agents.discovery.discovery_agent.Path.mkdir')
    @patch('autopackager.agents.discovery.discovery_agent.Path.exists')
    def test_download_dell_catalog_fresh_download(self, mock_exists, mock_mkdir, mock_requests):
        """Test downloading Dell catalog when not cached"""
        # Mock file doesn't exist (will trigger download)
        mock_exists.return_value = False
        mock_requests.return_value.content = b'CAB content'
        mock_requests.return_value.raise_for_status = Mock()

        # Mock subprocess and file operations
        with patch('subprocess.run') as mock_subprocess, \
             patch('builtins.open', mock_open(read_data='<xml>catalog</xml>')) as mock_file:

            result = self.agent._download_dell_catalog(self.mock_config['oem_catalogs']['dell'])

            # Verify download was called
            mock_requests.assert_called_once()
            # Verify subprocess was called to extract CAB
            mock_subprocess.assert_called_once()
            # Verify result
            self.assertEqual(result, '<xml>catalog</xml>')

    @patch('autopackager.agents.discovery.discovery_agent.Path.exists')
    @patch('builtins.open', new_callable=mock_open, read_data='<xml>cached</xml>')
    def test_download_dell_catalog_uses_cache(self, mock_file, mock_exists):
        """Test using cached catalog when not stale"""
        mock_exists.return_value = True

        with patch.object(self.agent, '_is_cache_stale', return_value=False):
            result = self.agent._download_dell_catalog(self.mock_config['oem_catalogs']['dell'])

            self.assertEqual(result, '<xml>cached</xml>')


class TestDiscoveryAgentHP(unittest.TestCase):
    """Test cases for HP driver discovery"""

    def setUp(self):
        """Set up test fixtures"""
        self.mock_config = {
            'oem_catalogs': {
                'hp': {
                    'catalog_url': 'https://hpia.hpcloud.hp.com/downloads/platformlist.cab',
                    'catalog_path': '/tmp/hp_catalog'
                }
            }
        }

        with patch('autopackager.agents.discovery.discovery_agent.get_config', return_value=self.mock_config):
            self.agent = DiscoveryAgent()

        self.job = Mock(spec=Job)
        self.job.id = 1
        self.job.hardware_model = 'EliteBook 840 G8'
        self.job.driver_type = 'chipset'
        self.job.current_version = None
        self.job.vendor = 'hp'

        self.sample_catalog_data = {
            'ImagePal': {
                'Platform': {
                    '@SystemName': 'HP EliteBook 840 G8',
                    '@ProductCode': '12345',
                    '@OSReleaseIdList': '22H2'
                }
            }
        }

    @patch('autopackager.agents.discovery.discovery_agent.xmltodict.parse')
    def test_discover_hp_driver_success(self, mock_xmltodict):
        """Test successful HP driver discovery"""
        mock_xmltodict.return_value = self.sample_catalog_data

        with patch.object(self.agent, '_download_hp_catalog', return_value='<xml>'):
            result = self.agent._discover_hp_driver(self.job)

            self.assertTrue(result['update_available'])
            self.assertEqual(result['latest_version'], '22H2')
            self.assertIn('sp12345', result['download_url'].lower())

    @patch('autopackager.agents.discovery.discovery_agent.xmltodict.parse')
    def test_discover_hp_driver_no_match(self, mock_xmltodict):
        """Test HP driver discovery when no matching driver found"""
        mock_xmltodict.return_value = self.sample_catalog_data

        self.job.hardware_model = 'NonExistentModel'

        with patch.object(self.agent, '_download_hp_catalog', return_value='<xml>'):
            result = self.agent._discover_hp_driver(self.job)

            self.assertFalse(result['update_available'])

    def test_find_hp_driver_with_matching_name(self):
        """Test finding HP driver with matching system name"""
        result = self.agent._find_hp_driver(
            self.sample_catalog_data,
            'EliteBook 840 G8'
        )

        self.assertIsNotNone(result)
        self.assertIn('EliteBook 840 G8', result['name'])

    def test_find_hp_driver_with_matching_product_code(self):
        """Test finding HP driver with matching product code"""
        result = self.agent._find_hp_driver(
            self.sample_catalog_data,
            '12345'
        )

        self.assertIsNotNone(result)
        self.assertIn('EliteBook 840 G8', result['name'])

    def test_find_hp_driver_handles_multiple_platforms(self):
        """Test handling of multiple platforms in catalog"""
        catalog_data = {
            'ImagePal': {
                'Platform': [
                    {
                        '@SystemName': 'HP ProBook 450 G8',
                        '@ProductCode': '11111',
                        '@OSReleaseIdList': '22H2'
                    },
                    {
                        '@SystemName': 'HP EliteBook 840 G8',
                        '@ProductCode': '12345',
                        '@OSReleaseIdList': '22H2'
                    }
                ]
            }
        }

        result = self.agent._find_hp_driver(catalog_data, 'EliteBook 840 G8')

        self.assertIsNotNone(result)
        self.assertIn('EliteBook 840 G8', result['name'])

    def test_find_hp_driver_returns_none_on_no_match(self):
        """Test that None is returned when no match found"""
        result = self.agent._find_hp_driver(
            self.sample_catalog_data,
            'NonExistentModel'
        )

        self.assertIsNone(result)

    def test_find_hp_driver_handles_parsing_errors(self):
        """Test handling of malformed catalog data"""
        malformed_data = {'ImagePal': None}

        result = self.agent._find_hp_driver(malformed_data, 'EliteBook 840 G8')

        self.assertIsNone(result)


class TestDiscoveryAgentLenovo(unittest.TestCase):
    """Test cases for Lenovo driver discovery"""

    def setUp(self):
        """Set up test fixtures"""
        self.mock_config = {
            'oem_catalogs': {
                'lenovo': {
                    'catalog_url': 'https://download.lenovo.com/catalog/catalogv2.xml',
                    'catalog_path': '/tmp/lenovo_catalog'
                }
            }
        }

        with patch('autopackager.agents.discovery.discovery_agent.get_config', return_value=self.mock_config):
            self.agent = DiscoveryAgent()

        self.job = Mock(spec=Job)
        self.job.id = 1
        self.job.hardware_model = 'ThinkPad X1 Carbon Gen 9'
        self.job.driver_type = 'chipset'
        self.job.current_version = '1.0.0'
        self.job.vendor = 'lenovo'

        self.sample_catalog_data = {
            'Products': {
                'Product': {
                    '@name': 'ThinkPad X1 Carbon Gen 9',
                    '@type': 'ThinkPad',
                    'Driver': {
                        '@name': 'Intel Chipset Driver',
                        '@version': '10.1.18838.8283',
                        '@category': 'Chipset',
                        '@date': '2024-01-15',
                        '@size': '5242880',
                        'URL': {
                            '#text': 'https://download.lenovo.com/drivers/driver.exe'
                        }
                    }
                }
            }
        }

    @patch('autopackager.agents.discovery.discovery_agent.xmltodict.parse')
    def test_discover_lenovo_driver_success(self, mock_xmltodict):
        """Test successful Lenovo driver discovery"""
        mock_xmltodict.return_value = self.sample_catalog_data

        with patch.object(self.agent, '_download_lenovo_catalog', return_value='<xml>'):
            result = self.agent._discover_lenovo_driver(self.job)

            self.assertTrue(result['update_available'])
            self.assertEqual(result['latest_version'], '10.1.18838.8283')
            self.assertEqual(result['download_url'], 'https://download.lenovo.com/drivers/driver.exe')
            self.assertEqual(result['file_size'], 5242880)

    @patch('autopackager.agents.discovery.discovery_agent.xmltodict.parse')
    def test_discover_lenovo_driver_no_match(self, mock_xmltodict):
        """Test Lenovo driver discovery when no matching driver found"""
        mock_xmltodict.return_value = self.sample_catalog_data

        self.job.hardware_model = 'NonExistentModel'

        with patch.object(self.agent, '_download_lenovo_catalog', return_value='<xml>'):
            result = self.agent._discover_lenovo_driver(self.job)

            self.assertFalse(result['update_available'])

    def test_find_lenovo_driver_with_matching_model(self):
        """Test finding Lenovo driver with matching model"""
        result = self.agent._find_lenovo_driver(
            self.sample_catalog_data,
            'ThinkPad X1 Carbon Gen 9',
            'chipset'
        )

        self.assertIsNotNone(result)
        self.assertEqual(result['name'], 'Intel Chipset Driver')
        self.assertEqual(result['version'], '10.1.18838.8283')

    def test_find_lenovo_driver_filters_by_driver_type(self):
        """Test filtering drivers by type"""
        catalog_data = {
            'Products': {
                'Product': {
                    '@name': 'ThinkPad X1 Carbon Gen 9',
                    '@type': 'ThinkPad',
                    'Driver': [
                        {
                            '@name': 'Intel Chipset Driver',
                            '@version': '1.0.0',
                            '@category': 'Chipset',
                            '@date': '2024-01-15',
                            '@size': '5242880',
                            'URL': {'#text': 'https://example.com/chipset.exe'}
                        },
                        {
                            '@name': 'Network Driver',
                            '@version': '2.0.0',
                            '@category': 'Network',
                            '@date': '2024-01-15',
                            '@size': '10485760',
                            'URL': {'#text': 'https://example.com/network.exe'}
                        }
                    ]
                }
            }
        }

        result = self.agent._find_lenovo_driver(catalog_data, 'ThinkPad X1 Carbon Gen 9', 'chipset')

        self.assertIsNotNone(result)
        self.assertEqual(result['name'], 'Intel Chipset Driver')
        self.assertEqual(result['version'], '1.0.0')

    def test_find_lenovo_driver_handles_multiple_products(self):
        """Test handling of multiple products in catalog"""
        catalog_data = {
            'Products': {
                'Product': [
                    {
                        '@name': 'ThinkPad T14',
                        '@type': 'ThinkPad',
                        'Driver': {
                            '@name': 'Driver 1',
                            '@version': '1.0.0',
                            '@category': 'Chipset',
                            'URL': {'#text': 'https://example.com/driver1.exe'}
                        }
                    },
                    {
                        '@name': 'ThinkPad X1 Carbon Gen 9',
                        '@type': 'ThinkPad',
                        'Driver': {
                            '@name': 'Driver 2',
                            '@version': '2.0.0',
                            '@category': 'Chipset',
                            'URL': {'#text': 'https://example.com/driver2.exe'}
                        }
                    }
                ]
            }
        }

        result = self.agent._find_lenovo_driver(catalog_data, 'ThinkPad X1 Carbon Gen 9')

        self.assertIsNotNone(result)
        self.assertEqual(result['name'], 'Driver 2')

    def test_find_lenovo_driver_returns_none_on_no_match(self):
        """Test that None is returned when no match found"""
        result = self.agent._find_lenovo_driver(
            self.sample_catalog_data,
            'NonExistentModel'
        )

        self.assertIsNone(result)

    def test_find_lenovo_driver_handles_parsing_errors(self):
        """Test handling of malformed catalog data"""
        malformed_data = {'Products': None}

        result = self.agent._find_lenovo_driver(malformed_data, 'ThinkPad X1 Carbon Gen 9')

        self.assertIsNone(result)

    @patch('autopackager.agents.discovery.discovery_agent.requests.get')
    @patch('builtins.open', new_callable=mock_open, read_data='<xml>catalog</xml>')
    @patch('autopackager.agents.discovery.discovery_agent.Path.exists')
    def test_download_lenovo_catalog_fresh_download(self, mock_exists, mock_file, mock_requests):
        """Test downloading Lenovo catalog when not cached"""
        mock_exists.return_value = False
        mock_requests.return_value.content = b'<xml>catalog</xml>'
        mock_requests.return_value.raise_for_status = Mock()

        with patch.object(self.agent, '_is_cache_stale', return_value=True):
            result = self.agent._download_lenovo_catalog(self.mock_config['oem_catalogs']['lenovo'])

            mock_requests.assert_called_once()
            self.assertEqual(result, '<xml>catalog</xml>')


class TestDiscoveryAgentUtilities(unittest.TestCase):
    """Test cases for utility methods"""

    def setUp(self):
        """Set up test fixtures"""
        mock_config = {'oem_catalogs': {}}

        with patch('autopackager.agents.discovery.discovery_agent.get_config', return_value=mock_config):
            self.agent = DiscoveryAgent()

    def test_compare_versions_returns_true_when_different(self):
        """Test version comparison returns true when versions differ"""
        result = self.agent._compare_versions('1.0.0', '1.0.1', vendor='dell')
        self.assertTrue(result)

    def test_compare_versions_returns_false_when_same(self):
        """Test version comparison returns false when versions are same"""
        result = self.agent._compare_versions('A01', 'A01', vendor='dell')
        self.assertFalse(result)

    def test_compare_versions_returns_true_when_current_is_none(self):
        """Test version comparison returns true when current version is None"""
        result = self.agent._compare_versions(None, 'A01', vendor='dell')
        self.assertTrue(result)

    def test_compare_versions_handles_empty_string(self):
        """Test version comparison handles empty string"""
        result = self.agent._compare_versions('', 'A01', vendor='dell')
        self.assertTrue(result)

    def test_compare_versions_dell_a_series(self):
        """Test Dell A-series version comparison (A00 < A01, A13 < A14)"""
        # A00 is older than A01
        result = self.agent._compare_versions('A00', 'A01', vendor='dell')
        self.assertTrue(result)

        # A13 is older than A14
        result = self.agent._compare_versions('A13', 'A14', vendor='dell')
        self.assertTrue(result)

        # A14 is NOT older than A13
        result = self.agent._compare_versions('A14', 'A13', vendor='dell')
        self.assertFalse(result)

        # A00 is NOT older than A00
        result = self.agent._compare_versions('A00', 'A00', vendor='dell')
        self.assertFalse(result)

    def test_compare_versions_dell_semantic(self):
        """Test Dell semantic version comparison (1.14.2 < 1.15.0)"""
        # 1.14.2 is older than 1.15.0
        result = self.agent._compare_versions('1.14.2', '1.15.0', vendor='dell')
        self.assertTrue(result)

        # 1.15.0 is NOT older than 1.14.2
        result = self.agent._compare_versions('1.15.0', '1.14.2', vendor='dell')
        self.assertFalse(result)

        # 1.99.99 is older than 2.0.0
        result = self.agent._compare_versions('1.99.99', '2.0.0', vendor='dell')
        self.assertTrue(result)

    def test_compare_versions_hp_sp_prefix(self):
        """Test HP SP-prefixed version comparison (SP142354 < SP142355)"""
        # SP142354 is older than SP142355
        result = self.agent._compare_versions('SP142354', 'SP142355', vendor='hp')
        self.assertTrue(result)

        # SP99999 is older than SP100000
        result = self.agent._compare_versions('SP99999', 'SP100000', vendor='hp')
        self.assertTrue(result)

        # SP142355 is NOT older than SP142354
        result = self.agent._compare_versions('SP142355', 'SP142354', vendor='hp')
        self.assertFalse(result)

        # SP142355 is NOT older than SP142355
        result = self.agent._compare_versions('SP142355', 'SP142355', vendor='hp')
        self.assertFalse(result)

    def test_compare_versions_hp_standard(self):
        """Test HP standard version comparison (1.2.2 < 1.2.3)"""
        # 1.2.2 is older than 1.2.3
        result = self.agent._compare_versions('1.2.2', '1.2.3', vendor='hp')
        self.assertTrue(result)

        # 1.2.3 is NOT older than 1.2.2
        result = self.agent._compare_versions('1.2.3', '1.2.2', vendor='hp')
        self.assertFalse(result)

    def test_compare_versions_lenovo_multisegment(self):
        """Test Lenovo multi-segment version comparison"""
        # 1.81.0.23 is older than 1.82.0.24
        result = self.agent._compare_versions('1.81.0.23', '1.82.0.24', vendor='lenovo')
        self.assertTrue(result)

        # 10.1.18838.8282 is older than 10.1.18838.8283
        result = self.agent._compare_versions('10.1.18838.8282', '10.1.18838.8283', vendor='lenovo')
        self.assertTrue(result)

        # 1.82.0.24 is NOT older than 1.81.0.23
        result = self.agent._compare_versions('1.82.0.24', '1.81.0.23', vendor='lenovo')
        self.assertFalse(result)

        # Equal versions
        result = self.agent._compare_versions('1.82.0.24', '1.82.0.24', vendor='lenovo')
        self.assertFalse(result)

    def test_compare_versions_padding_normalization(self):
        """Test that padding differences are normalized (01.02 == 1.2)"""
        # 01.02 should equal 1.2
        result = self.agent._compare_versions('01.02', '1.2', vendor='dell')
        self.assertFalse(result)

        # 1.2 should equal 01.02
        result = self.agent._compare_versions('1.2', '01.02', vendor='dell')
        self.assertFalse(result)

    def test_compare_versions_with_vendor_routing(self):
        """Test that vendor parameter correctly routes to appropriate parser"""
        # Dell A-series should work with dell vendor
        result = self.agent._compare_versions('A00', 'A01', vendor='dell')
        self.assertTrue(result)

        # HP SP-prefix should work with hp vendor
        result = self.agent._compare_versions('SP100', 'SP101', vendor='hp')
        self.assertTrue(result)

        # Lenovo multi-segment should work with lenovo vendor
        result = self.agent._compare_versions('1.0.0.1', '1.0.0.2', vendor='lenovo')
        self.assertTrue(result)

    def test_compare_versions_without_vendor(self):
        """Test that version comparison works without vendor parameter"""
        # Should still work with standard semantic versions
        result = self.agent._compare_versions('1.0.0', '1.0.1')
        self.assertTrue(result)

        result = self.agent._compare_versions('1.0.1', '1.0.0')
        self.assertFalse(result)

    def test_is_cache_stale_returns_true_for_nonexistent_file(self):
        """Test cache staleness check returns true for nonexistent file"""
        fake_path = Path('/fake/path/that/does/not/exist.xml')
        result = self.agent._is_cache_stale(fake_path)
        self.assertTrue(result)

    @patch('autopackager.agents.discovery.discovery_agent.Path.exists')
    @patch('autopackager.agents.discovery.discovery_agent.Path.stat')
    def test_is_cache_stale_returns_true_for_old_file(self, mock_stat, mock_exists):
        """Test cache staleness check returns true for old file"""
        mock_exists.return_value = True

        # Mock file that's 25 hours old
        old_timestamp = (datetime.now() - timedelta(hours=25)).timestamp()
        mock_stat_result = Mock()
        mock_stat_result.st_mtime = old_timestamp
        mock_stat.return_value = mock_stat_result

        fake_path = Path('/fake/path/old_file.xml')
        result = self.agent._is_cache_stale(fake_path, max_age_hours=24)

        self.assertTrue(result)

    @patch('autopackager.agents.discovery.discovery_agent.Path.exists')
    @patch('autopackager.agents.discovery.discovery_agent.Path.stat')
    def test_is_cache_stale_returns_false_for_fresh_file(self, mock_stat, mock_exists):
        """Test cache staleness check returns false for fresh file"""
        mock_exists.return_value = True

        # Mock file that's 1 hour old
        recent_timestamp = (datetime.now() - timedelta(hours=1)).timestamp()
        mock_stat_result = Mock()
        mock_stat_result.st_mtime = recent_timestamp
        mock_stat.return_value = mock_stat_result

        fake_path = Path('/fake/path/fresh_file.xml')
        result = self.agent._is_cache_stale(fake_path, max_age_hours=24)

        self.assertFalse(result)

    @patch('autopackager.agents.discovery.discovery_agent.Path.exists')
    @patch('autopackager.agents.discovery.discovery_agent.Path.stat')
    def test_is_cache_stale_respects_custom_max_age(self, mock_stat, mock_exists):
        """Test cache staleness check respects custom max_age_hours"""
        mock_exists.return_value = True

        # Mock file that's 10 hours old
        timestamp = (datetime.now() - timedelta(hours=10)).timestamp()
        mock_stat_result = Mock()
        mock_stat_result.st_mtime = timestamp
        mock_stat.return_value = mock_stat_result

        fake_path = Path('/fake/path/file.xml')

        # Should be fresh with 24 hour max age
        result = self.agent._is_cache_stale(fake_path, max_age_hours=24)
        self.assertFalse(result)

        # Should be stale with 8 hour max age
        result = self.agent._is_cache_stale(fake_path, max_age_hours=8)
        self.assertTrue(result)

    def test_discover_software_returns_not_implemented(self):
        """Test software discovery returns Phase 2 note"""
        job = Mock(spec=Job)
        job.id = 1
        job.software_title = 'Adobe Acrobat'

        result = self.agent._discover_software(job)

        self.assertFalse(result['update_available'])
        self.assertIn('Phase 2', result['note'])


if __name__ == '__main__':
    unittest.main()
