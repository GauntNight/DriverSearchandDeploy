"""Unit tests for Version Comparison Utility"""

import unittest

from autopackager.utils.version_comparison import (
    VersionComparator,
    DellVersionParser,
    HPVersionParser,
    LenovoVersionParser,
    SemanticVersion
)


class TestDellVersionComparison(unittest.TestCase):
    """Test cases for Dell version parsing and comparison"""

    def setUp(self):
        """Set up test fixtures"""
        self.parser = DellVersionParser()
        self.comparator = VersionComparator()

    def test_parse_dell_a_series_basic(self):
        """Test parsing Dell A-series BIOS versions"""
        version = self.parser.parse('A00')
        self.assertIsNotNone(version)
        self.assertEqual(version.major, 0)
        self.assertEqual(version.original, 'A00')

    def test_parse_dell_a_series_double_digit(self):
        """Test parsing Dell A-series with double digits"""
        version = self.parser.parse('A14')
        self.assertIsNotNone(version)
        self.assertEqual(version.major, 14)

    def test_parse_dell_a_series_case_insensitive(self):
        """Test Dell A-series parsing is case-insensitive"""
        v1 = self.parser.parse('A01')
        v2 = self.parser.parse('a01')
        self.assertEqual(v1.major, v2.major)

    def test_dell_a_series_comparison(self):
        """Test comparing Dell A-series versions: A14 > A13"""
        result = self.comparator.compare('A14', 'A13', 'dell')
        self.assertEqual(result, 1)

    def test_dell_a_series_comparison_basic(self):
        """Test comparing Dell A-series versions: A01 > A00"""
        result = self.comparator.compare('A01', 'A00', 'dell')
        self.assertEqual(result, 1)

    def test_dell_a_series_equal(self):
        """Test Dell A-series versions are equal: A05 == A05"""
        result = self.comparator.compare('A05', 'A05', 'dell')
        self.assertEqual(result, 0)

    def test_parse_dell_semantic_version(self):
        """Test parsing Dell semantic versions"""
        version = self.parser.parse('1.15.0')
        self.assertIsNotNone(version)
        self.assertEqual(version.major, 1)
        self.assertEqual(version.minor, 15)
        self.assertEqual(version.patch, 0)

    def test_dell_semantic_version_comparison(self):
        """Test comparing Dell semantic versions: 1.15.0 > 1.14.2"""
        result = self.comparator.compare('1.15.0', '1.14.2', 'dell')
        self.assertEqual(result, 1)

    def test_dell_semantic_major_version_bump(self):
        """Test Dell semantic major version bump: 2.0.0 > 1.99.99"""
        result = self.comparator.compare('2.0.0', '1.99.99', 'dell')
        self.assertEqual(result, 1)

    def test_dell_is_newer_true(self):
        """Test is_newer returns True for newer Dell versions"""
        self.assertTrue(self.comparator.is_newer('A13', 'A14', 'dell'))
        self.assertTrue(self.comparator.is_newer('1.14.0', '1.15.0', 'dell'))

    def test_dell_is_newer_false(self):
        """Test is_newer returns False for older or equal Dell versions"""
        self.assertFalse(self.comparator.is_newer('A14', 'A13', 'dell'))
        self.assertFalse(self.comparator.is_newer('1.15.0', '1.14.0', 'dell'))
        self.assertFalse(self.comparator.is_newer('A05', 'A05', 'dell'))


class TestHPVersionComparison(unittest.TestCase):
    """Test cases for HP version parsing and comparison"""

    def setUp(self):
        """Set up test fixtures"""
        self.parser = HPVersionParser()
        self.comparator = VersionComparator()

    def test_parse_hp_sp_prefixed(self):
        """Test parsing HP SP-prefixed versions"""
        version = self.parser.parse('SP142355')
        self.assertIsNotNone(version)
        self.assertEqual(version.major, 142355)
        self.assertEqual(version.original, 'SP142355')

    def test_parse_hp_sp_case_insensitive(self):
        """Test HP SP parsing is case-insensitive"""
        v1 = self.parser.parse('SP142355')
        v2 = self.parser.parse('sp142355')
        self.assertEqual(v1.major, v2.major)

    def test_hp_sp_prefixed_comparison(self):
        """Test comparing HP SP-prefixed versions: SP142355 > SP142354"""
        result = self.comparator.compare('SP142355', 'SP142354', 'hp')
        self.assertEqual(result, 1)

    def test_hp_sp_prefixed_rollover(self):
        """Test HP SP rollover: SP100000 > SP99999"""
        result = self.comparator.compare('SP100000', 'SP99999', 'hp')
        self.assertEqual(result, 1)

    def test_hp_sp_equal(self):
        """Test HP SP versions are equal: SP142355 == SP142355"""
        result = self.comparator.compare('SP142355', 'SP142355', 'hp')
        self.assertEqual(result, 0)

    def test_parse_hp_semantic_version(self):
        """Test parsing HP standard semantic versions"""
        version = self.parser.parse('1.2.3')
        self.assertIsNotNone(version)
        self.assertEqual(version.major, 1)
        self.assertEqual(version.minor, 2)
        self.assertEqual(version.patch, 3)

    def test_hp_semantic_version_comparison(self):
        """Test comparing HP semantic versions: 1.2.3 > 1.2.2"""
        result = self.comparator.compare('1.2.3', '1.2.2', 'hp')
        self.assertEqual(result, 1)

    def test_hp_is_newer_true(self):
        """Test is_newer returns True for newer HP versions"""
        self.assertTrue(self.comparator.is_newer('SP142354', 'SP142355', 'hp'))
        self.assertTrue(self.comparator.is_newer('1.2.2', '1.2.3', 'hp'))

    def test_hp_is_newer_false(self):
        """Test is_newer returns False for older or equal HP versions"""
        self.assertFalse(self.comparator.is_newer('SP142355', 'SP142354', 'hp'))
        self.assertFalse(self.comparator.is_newer('1.2.3', '1.2.2', 'hp'))


class TestLenovoVersionComparison(unittest.TestCase):
    """Test cases for Lenovo version parsing and comparison"""

    def setUp(self):
        """Set up test fixtures"""
        self.parser = LenovoVersionParser()
        self.comparator = VersionComparator()

    def test_parse_lenovo_four_segment(self):
        """Test parsing Lenovo 4-segment versions"""
        version = self.parser.parse('1.82.0.24')
        self.assertIsNotNone(version)
        self.assertEqual(version.major, 1)
        self.assertEqual(version.minor, 82)
        self.assertEqual(version.patch, 0)
        self.assertEqual(version.build, 24)
        self.assertEqual(len(version.segments), 4)

    def test_parse_lenovo_multi_segment_extended(self):
        """Test parsing Lenovo extended multi-segment versions"""
        version = self.parser.parse('10.1.18838.8283')
        self.assertIsNotNone(version)
        self.assertEqual(version.major, 10)
        self.assertEqual(version.minor, 1)
        self.assertEqual(version.patch, 18838)
        self.assertEqual(version.build, 8283)

    def test_lenovo_multi_segment_comparison(self):
        """Test comparing Lenovo multi-segment versions: 1.82.0.24 > 1.81.0.23"""
        result = self.comparator.compare('1.82.0.24', '1.81.0.23', 'lenovo')
        self.assertEqual(result, 1)

    def test_lenovo_multi_segment_extended_comparison(self):
        """Test comparing extended Lenovo versions: 10.1.18838.8283 > 10.1.18838.8282"""
        result = self.comparator.compare('10.1.18838.8283', '10.1.18838.8282', 'lenovo')
        self.assertEqual(result, 1)

    def test_lenovo_major_version_bump(self):
        """Test Lenovo major version bump: 2.0.0.0 > 1.99.99.99"""
        result = self.comparator.compare('2.0.0.0', '1.99.99.99', 'lenovo')
        self.assertEqual(result, 1)

    def test_lenovo_is_newer_true(self):
        """Test is_newer returns True for newer Lenovo versions"""
        self.assertTrue(self.comparator.is_newer('1.81.0.23', '1.82.0.24', 'lenovo'))
        self.assertTrue(self.comparator.is_newer('10.1.18838.8282', '10.1.18838.8283', 'lenovo'))

    def test_lenovo_is_newer_false(self):
        """Test is_newer returns False for older or equal Lenovo versions"""
        self.assertFalse(self.comparator.is_newer('1.82.0.24', '1.81.0.23', 'lenovo'))
        self.assertFalse(self.comparator.is_newer('1.82.0.24', '1.82.0.24', 'lenovo'))


class TestEdgeCases(unittest.TestCase):
    """Test cases for edge cases and special scenarios"""

    def setUp(self):
        """Set up test fixtures"""
        self.comparator = VersionComparator()

    def test_padding_differences_are_equal(self):
        """Test that padding differences are handled: 01.02 == 1.2"""
        # Using Lenovo parser which handles numeric versions
        result = self.comparator.compare('01.02', '1.2', 'lenovo')
        self.assertEqual(result, 0)

    def test_prerelease_less_than_release(self):
        """Test that pre-release versions are less than release: 1.0.0-alpha < 1.0.0"""
        v1 = self.comparator.parse('1.0.0-alpha', 'dell')
        v2 = self.comparator.parse('1.0.0', 'dell')
        self.assertIsNotNone(v1)
        self.assertIsNotNone(v2)
        self.assertTrue(v1 < v2)

    def test_prerelease_comparison(self):
        """Test comparing pre-release tags: 1.0.0-alpha < 1.0.0-beta"""
        v1 = self.comparator.parse('1.0.0-alpha', 'dell')
        v2 = self.comparator.parse('1.0.0-beta', 'dell')
        self.assertIsNotNone(v1)
        self.assertIsNotNone(v2)
        self.assertTrue(v1 < v2)

    def test_metadata_ignored_in_comparison(self):
        """Test that build metadata is parsed but doesn't affect comparison"""
        v1 = self.comparator.parse('1.0.0+20130313144700', 'dell')
        v2 = self.comparator.parse('1.0.0+20140313144700', 'dell')
        self.assertIsNotNone(v1)
        self.assertIsNotNone(v2)
        # Metadata should be parsed
        self.assertIsNotNone(v1.metadata)
        self.assertIsNotNone(v2.metadata)
        # But versions should be equal
        self.assertEqual(v1.compare(v2), 0)

    def test_none_current_version_returns_true(self):
        """Test that None current version means update is available"""
        self.assertTrue(self.comparator.is_newer(None, 'A01', 'dell'))
        self.assertTrue(self.comparator.is_newer(None, 'SP142355', 'hp'))
        self.assertTrue(self.comparator.is_newer(None, '1.82.0.24', 'lenovo'))

    def test_invalid_version_returns_none(self):
        """Test that invalid version strings return None"""
        version = self.comparator.parse('invalid-version', 'dell')
        self.assertIsNone(version)

    def test_invalid_version_comparison_returns_false(self):
        """Test that comparison with invalid version returns False"""
        # is_newer should return False when parsing fails (don't update)
        self.assertFalse(self.comparator.is_newer('1.0.0', 'invalid', 'dell'))

    def test_empty_string_returns_none(self):
        """Test that empty string returns None"""
        version = self.comparator.parse('', 'dell')
        self.assertIsNone(version)

    def test_whitespace_trimming(self):
        """Test that whitespace is trimmed from version strings"""
        v1 = self.comparator.parse('  A01  ', 'dell')
        v2 = self.comparator.parse('A01', 'dell')
        self.assertIsNotNone(v1)
        self.assertIsNotNone(v2)
        self.assertEqual(v1.compare(v2), 0)


class TestVersionComparatorIntegration(unittest.TestCase):
    """Test cases for VersionComparator integration and vendor routing"""

    def setUp(self):
        """Set up test fixtures"""
        self.comparator = VersionComparator()

    def test_vendor_routing_dell(self):
        """Test that Dell vendor routes to Dell parser"""
        # A-series should only work with Dell parser
        result = self.comparator.compare('A14', 'A13', 'dell')
        self.assertEqual(result, 1)

    def test_vendor_routing_hp(self):
        """Test that HP vendor routes to HP parser"""
        # SP-prefixed should only work with HP parser
        result = self.comparator.compare('SP142355', 'SP142354', 'hp')
        self.assertEqual(result, 1)

    def test_vendor_routing_lenovo(self):
        """Test that Lenovo vendor routes to Lenovo parser"""
        result = self.comparator.compare('1.82.0.24', '1.81.0.23', 'lenovo')
        self.assertEqual(result, 1)

    def test_vendor_case_insensitive(self):
        """Test that vendor name is case-insensitive"""
        result1 = self.comparator.compare('A14', 'A13', 'DELL')
        result2 = self.comparator.compare('A14', 'A13', 'Dell')
        result3 = self.comparator.compare('A14', 'A13', 'dell')
        self.assertEqual(result1, result2)
        self.assertEqual(result2, result3)

    def test_unknown_vendor_uses_default(self):
        """Test that unknown vendor uses default parser (Lenovo)"""
        # Should use Lenovo parser for unknown vendor
        result = self.comparator.compare('1.2.3', '1.2.2', 'unknown_vendor')
        self.assertEqual(result, 1)

    def test_none_vendor_uses_default(self):
        """Test that None vendor uses default parser"""
        result = self.comparator.compare('1.2.3', '1.2.2', None)
        self.assertEqual(result, 1)

    def test_compare_versions_alias(self):
        """Test that compare_versions is an alias for is_newer"""
        result1 = self.comparator.is_newer('A13', 'A14', 'dell')
        result2 = self.comparator.compare_versions('A13', 'A14', 'dell')
        self.assertEqual(result1, result2)

    def test_semantic_version_operators(self):
        """Test SemanticVersion comparison operators"""
        v1 = self.comparator.parse('1.0.0', 'dell')
        v2 = self.comparator.parse('2.0.0', 'dell')

        self.assertTrue(v1 < v2)
        self.assertTrue(v1 <= v2)
        self.assertTrue(v2 > v1)
        self.assertTrue(v2 >= v1)
        self.assertFalse(v1 == v2)
        self.assertTrue(v1 != v2)

    def test_semantic_version_equality_operators(self):
        """Test SemanticVersion equality operators"""
        v1 = self.comparator.parse('1.0.0', 'dell')
        v2 = self.comparator.parse('1.0.0', 'dell')

        self.assertTrue(v1 == v2)
        self.assertTrue(v1 <= v2)
        self.assertTrue(v1 >= v2)
        self.assertFalse(v1 != v2)
        self.assertFalse(v1 < v2)
        self.assertFalse(v1 > v2)


if __name__ == '__main__':
    unittest.main()
