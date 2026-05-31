"""Unit tests for MSI metadata extraction and msiexec command parsing."""

import os
import struct
import tempfile
import unittest
from math import ceil
from pathlib import Path
from unittest.mock import patch

from autopackager.utils.msi_metadata import (
    MSIMetadata,
    ParsedInstallCommand,
    parse_install_command,
    build_uninstall_command,
    build_product_code_detection_rule,
    resolve_local_path,
    decode_streamname,
    read_msi_metadata,
    _read_property_table,
)

_SECTOR = 512
_MINI = 64
_MINI_CUTOFF = 4096
_FREESECT = 0xFFFFFFFF
_ENDOFCHAIN = 0xFFFFFFFE
_FATSECT = 0xFFFFFFFD
_NOSTREAM = 0xFFFFFFFF


def _build_property_table_blob(props):
    """Build (Property-table, _StringPool, _StringData) blobs for ``props``."""
    strings, name_ids, value_ids = [], {}, {}
    for name, value in props.items():
        strings.append(name)
        name_ids[name] = len(strings)
        strings.append(value)
        value_ids[name] = len(strings)
    header = struct.pack('<I', 1252)
    pool = header + b''.join(struct.pack('<HH', len(s.encode('cp1252')), 1) for s in strings)
    sdata = b''.join(s.encode('cp1252') for s in strings)
    refs = [name_ids[n] for n in props] + [value_ids[n] for n in props]
    prop_stream = struct.pack('<%dH' % len(refs), *refs)
    return prop_stream, pool, sdata


def _build_minimal_msi(props, pad_stringdata_to=0, substorage_props=None,
                       substorage_name='TransformStorage'):
    """Construct a minimal but valid OLE2/MSI compound file from a property dict.

    Stream names are stored as plain ASCII; the reader's name de-mangling is a
    no-op on ASCII, so this exercises the full OLE2 container path (FAT chains,
    directory, mini stream) and the MSI string-pool / Property-table decode.

    When ``substorage_props`` is provided, the file also contains a child
    storage with its own ``Property`` / ``_StringPool`` / ``_StringData``
    streams, mimicking the language-transform sub-storages real MSIs (Webex,
    Office, anything with a Wix bundle of localizations) carry. The parser
    must return values from the root tables, not the sub-storage tables.
    """
    prop_stream, pool, sdata = _build_property_table_blob(props)
    if pad_stringdata_to and len(sdata) < pad_stringdata_to:
        sdata += b'\x00' * (pad_stringdata_to - len(sdata))

    streams = {'Property': prop_stream, '_StringPool': pool, '_StringData': sdata}

    sub_streams = {}
    if substorage_props:
        sub_prop, sub_pool, sub_sdata = _build_property_table_blob(substorage_props)
        sub_streams = {'Property': sub_prop, '_StringPool': sub_pool, '_StringData': sub_sdata}

    sectors, fat = [], []

    def add_chain(data):
        if not data:
            return _ENDOFCHAIN
        nsec = ceil(len(data) / _SECTOR)
        start = len(sectors)
        for i in range(nsec):
            sectors.append(data[i * _SECTOR:(i + 1) * _SECTOR].ljust(_SECTOR, b'\x00'))
            fat.append(0)
        for i in range(nsec):
            fat[start + i] = (start + i + 1) if i < nsec - 1 else _ENDOFCHAIN
        return start

    # Namespace sub-storage streams when allocating space so they don't
    # collide with the root streams that share their names.
    all_streams = dict(streams)
    for name, data in sub_streams.items():
        all_streams[f'__SUB__{name}'] = data
    mini_streams = {k: v for k, v in all_streams.items() if len(v) < _MINI_CUTOFF}
    big_streams = {k: v for k, v in all_streams.items() if len(v) >= _MINI_CUTOFF}

    ministream, minifat, mini_start = b'', [], {}
    for name, data in mini_streams.items():
        msec = ceil(len(data) / _MINI)
        start = len(minifat)
        mini_start[name] = start
        for i in range(msec):
            minifat.append((start + i + 1) if i < msec - 1 else _ENDOFCHAIN)
        ministream += data.ljust(msec * _MINI, b'\x00')

    big_start = {name: add_chain(data) for name, data in big_streams.items()}
    cont_start = add_chain(ministream) if ministream else _ENDOFCHAIN

    if minifat:
        minifat_bytes = struct.pack('<%dI' % len(minifat), *minifat)
        minifat_start = add_chain(minifat_bytes)
        num_minifat = ceil(len(minifat_bytes) / _SECTOR)
    else:
        minifat_start, num_minifat = _ENDOFCHAIN, 0

    def dirent(name, etype, start, size, left=_NOSTREAM, right=_NOSTREAM, child=_NOSTREAM):
        b = bytearray(128)
        nm = name.encode('utf-16-le')
        b[0:len(nm)] = nm
        struct.pack_into('<H', b, 0x40, len(nm) + 2)
        b[0x42] = etype
        b[0x43] = 1
        struct.pack_into('<I', b, 0x44, left)
        struct.pack_into('<I', b, 0x48, right)
        struct.pack_into('<I', b, 0x4C, child)
        struct.pack_into('<I', b, 0x74, 0 if start == _ENDOFCHAIN else start)
        struct.pack_into('<Q', b, 0x78, size)
        return bytes(b)

    # Directory layout:
    #   0:                       Root Entry (type 5) -> child = first root stream
    #   1..n_root:               root streams, right-chained
    #   n_root+1:                substorage (type 1, only when sub_streams) -> child = first sub stream
    #   n_root+2..n_root+1+n_sub:sub streams, right-chained
    #
    # The last root stream's right points at the substorage so the OLE2
    # red-black-tree walk reaches it as part of the root storage's children.
    root_items = list(streams.items())
    sub_items = list(sub_streams.items())
    n_root, n_sub = len(root_items), len(sub_items)
    sub_entry_id = n_root + 1 if n_sub else _NOSTREAM
    sub_first_id = n_root + 2 if n_sub else _NOSTREAM

    dir_bytes = dirent('Root Entry', 5, cont_start, len(ministream),
                       child=1 if root_items else sub_entry_id)
    for idx, (name, data) in enumerate(root_items):
        s = big_start[name] if name in big_start else mini_start[name]
        right = (idx + 2) if idx + 1 < n_root else sub_entry_id
        dir_bytes += dirent(name, 2, s, len(data), right=right)
    if n_sub:
        dir_bytes += dirent(substorage_name, 1, 0, 0, child=sub_first_id)
        for idx, (name, data) in enumerate(sub_items):
            key = f'__SUB__{name}'
            s = big_start[key] if key in big_start else mini_start[key]
            right = (sub_first_id + idx + 1) if idx + 1 < n_sub else _NOSTREAM
            dir_bytes += dirent(name, 2, s, len(data), right=right)
    if len(dir_bytes) % _SECTOR:
        dir_bytes += b'\x00' * (_SECTOR - len(dir_bytes) % _SECTOR)
    dir_start = add_chain(dir_bytes)

    D = len(sectors)
    nfat = 1
    while ceil((D + nfat) / (_SECTOR // 4)) > nfat:
        nfat += 1
    fat.extend([_FREESECT] * (D + nfat - len(fat)))
    fat_indices = list(range(D, D + nfat))
    for idx in fat_indices:
        fat[idx] = _FATSECT
    fat_padded = fat + [_FREESECT] * (nfat * (_SECTOR // 4) - len(fat))
    fat_bytes = struct.pack('<%dI' % len(fat_padded), *fat_padded)
    for i in range(nfat):
        sectors.append(fat_bytes[i * _SECTOR:(i + 1) * _SECTOR])

    hdr = bytearray(_SECTOR)
    hdr[0:8] = b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'
    struct.pack_into('<H', hdr, 0x1A, 0x0003)
    struct.pack_into('<H', hdr, 0x1C, 0xFFFE)
    struct.pack_into('<H', hdr, 0x1E, 9)
    struct.pack_into('<H', hdr, 0x20, 6)
    struct.pack_into('<I', hdr, 0x2C, nfat)
    struct.pack_into('<I', hdr, 0x30, dir_start)
    struct.pack_into('<I', hdr, 0x38, _MINI_CUTOFF)
    struct.pack_into('<I', hdr, 0x3C, minifat_start)
    struct.pack_into('<I', hdr, 0x40, num_minifat)
    struct.pack_into('<I', hdr, 0x44, _ENDOFCHAIN)
    struct.pack_into('<I', hdr, 0x48, 0)
    difat = fat_indices + [_FREESECT] * (109 - len(fat_indices))
    struct.pack_into('<109I', hdr, 0x4C, *difat[:109])

    return bytes(hdr) + b''.join(sectors)


class _FakeCompoundFile:
    """Minimal stand-in for _CompoundFile that serves canned stream bytes."""

    def read_stream(self, entry):
        return entry['data']


def _build_string_pool(strings, codepage=1252):
    header = struct.pack('<I', codepage)
    pool = header + b''.join(
        struct.pack('<HH', len(s.encode('cp1252')), 1) for s in strings
    )
    data = b''.join(s.encode('cp1252') for s in strings)
    return pool, data


class TestStreamNameDecoding(unittest.TestCase):
    # The MSI mangling alphabet is digits-first
    # (``0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz._``),
    # so index 0 decodes to ``'0'``, index 10 to ``'A'``, index 36 to ``'a'``.

    def test_single_char_decode(self):
        self.assertEqual(decode_streamname(chr(0x4800)), '0')
        self.assertEqual(decode_streamname(chr(0x4801)), '1')
        self.assertEqual(decode_streamname(chr(0x4800 + 10)), 'A')
        self.assertEqual(decode_streamname(chr(0x4800 + 36)), 'a')

    def test_two_char_decode(self):
        # Low 6 bits = first character, high 6 bits = second character.
        self.assertEqual(decode_streamname(chr(0x3800)), '00')
        self.assertEqual(decode_streamname(chr(0x3801)), '10')
        # 0x3F3F -> low=63 ('_'), high=28 ('S') -> '_S'
        # (real first two chars of the ``_StringData`` table stream name).
        self.assertEqual(decode_streamname(chr(0x3F3F)), '_S')

    def test_real_msi_table_names_decode(self):
        # Round-trip: mangle a canonical MSI table name with the reference
        # encoding rules, then confirm ``decode_streamname`` recovers it.
        # If the mangling alphabet regresses, every real MSI's
        # ``Property`` / ``_StringPool`` / ``_StringData`` streams stop being
        # discoverable and metadata extraction silently returns empty.
        alphabet = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz._'

        def encode(name):
            out, i = [], 0
            while i < len(name):
                a = alphabet.index(name[i])
                if i + 1 < len(name) and name[i + 1] in alphabet:
                    b = alphabet.index(name[i + 1])
                    out.append(chr(0x3800 + (b << 6) + a))
                    i += 2
                else:
                    out.append(chr(0x4800 + a))
                    i += 1
            return ''.join(out)

        for table in ('_StringData', '_StringPool', 'Property', '_Columns', '_Tables'):
            self.assertEqual(decode_streamname(encode(table)), table)
            # Real MSI streams carry a leading 0x4840 table marker. It is a
            # delimiter, not part of the table name, and must be skipped.
            self.assertEqual(decode_streamname(chr(0x4840) + encode(table)), table)

    def test_ascii_passthrough(self):
        self.assertEqual(decode_streamname('Property'), 'Property')
        self.assertEqual(decode_streamname('_StringData'), '_StringData')


class TestPropertyTableDecoding(unittest.TestCase):
    def test_decodes_column_major_property_table(self):
        strings = [
            'ProductName', '7-Zip 24.08 (x64)',
            'ProductVersion', '24.08.00.0',
            'ProductCode', '{23170F69-40C1-2702-2408-000001000000}',
        ]
        pool, data = _build_string_pool(strings)
        # Column-major: 3 name refs (ids 1,3,5) then 3 value refs (ids 2,4,6)
        prop = struct.pack('<6H', 1, 3, 5, 2, 4, 6)

        streams = {
            'Property': {'data': prop},
            '_StringPool': {'data': pool},
            '_StringData': {'data': data},
        }

        result = _read_property_table(_FakeCompoundFile(), streams)

        self.assertEqual(result['ProductName'], '7-Zip 24.08 (x64)')
        self.assertEqual(result['ProductVersion'], '24.08.00.0')
        self.assertEqual(result['ProductCode'], '{23170F69-40C1-2702-2408-000001000000}')

    def test_missing_property_stream_returns_empty(self):
        self.assertEqual(_read_property_table(_FakeCompoundFile(), {}), {})


class TestEndToEndMSIParsing(unittest.TestCase):
    """Round-trip a synthetic MSI through the full OLE2 reader."""

    PROPS = {
        'ProductName': '7-Zip 24.08 (x64)',
        'ProductVersion': '24.08.00.0',
        'ProductCode': '{23170F69-40C1-2702-2408-000001000000}',
        'UpgradeCode': '{23170F69-40C1-2702-0000-000004000000}',
        'Manufacturer': 'Igor Pavlov',
        'ProductLanguage': '1033',
    }

    def _round_trip(self, pad):
        data = _build_minimal_msi(self.PROPS, pad_stringdata_to=pad)
        fd, path = tempfile.mkstemp(suffix='.msi')
        try:
            with os.fdopen(fd, 'wb') as f:
                f.write(data)
            return read_msi_metadata(path)
        finally:
            os.unlink(path)

    def test_mini_stream_path(self):
        """Small streams are read through the mini-FAT / mini-stream."""
        meta = self._round_trip(pad=0)
        self.assertEqual(meta.product_name, '7-Zip 24.08 (x64)')
        self.assertEqual(meta.product_version, '24.08.00.0')
        self.assertEqual(meta.product_code, '{23170F69-40C1-2702-2408-000001000000}')
        self.assertEqual(meta.upgrade_code, '{23170F69-40C1-2702-0000-000004000000}')
        self.assertEqual(meta.manufacturer, 'Igor Pavlov')
        self.assertEqual(meta.language, '1033')

    def test_main_fat_path(self):
        """A stream padded past the 4 KiB cutoff is read through the main FAT."""
        meta = self._round_trip(pad=5000)
        self.assertEqual(meta.product_name, '7-Zip 24.08 (x64)')
        self.assertEqual(meta.product_code, '{23170F69-40C1-2702-2408-000001000000}')

    def test_substorage_property_table_does_not_shadow_root(self):
        """A sub-storage's ``Property`` stream must NOT shadow the root one.

        Real-world MSIs that embed language transforms or feature variants
        (Webex App, multi-locale Office bundles, anything Wix-bundled with a
        per-locale storage) keep one ``Property`` / ``_StringPool`` /
        ``_StringData`` trio per sub-storage in addition to the root tables.

        A flat scan over ``cfb.entries`` followed by a name-keyed dict picks
        the **last** Property stream encountered and silently shadows the
        real root Property table with a transform fragment -- typically only
        12-18 bytes, decoding to 1-2 useless rows. After
        ``read_msi_metadata`` falls back to the SummaryInformation stream
        for ``ProductName`` and ``Manufacturer`` (the only two values it
        knows how to recover from outside the Property table),
        ``ProductCode``, ``ProductVersion``, ``UpgradeCode`` and
        ``ProductLanguage`` come back as ``''`` -- which then propagates into
        an empty Intune detection rule and a ``msiexec /x <filename>``
        uninstall command (no ProductCode), both of which silently break.

        The parser now walks the OLE2 red-black sibling tree under the root
        storage and only considers root-level streams. This test pins that
        behaviour so a regression to a flat scan is caught immediately.
        """
        substorage_props = {
            'ProductName': 'WRONG_NAME_FROM_TRANSFORM',
            'ProductVersion': '0.0.0.0',
            'ProductCode': '{00000000-0000-0000-0000-000000000000}',
            'UpgradeCode': '{11111111-1111-1111-1111-111111111111}',
            'Manufacturer': 'WRONG_MFR',
            'ProductLanguage': '9999',
        }
        data = _build_minimal_msi(self.PROPS, substorage_props=substorage_props,
                                  substorage_name='Transform1033')
        fd, path = tempfile.mkstemp(suffix='.msi')
        try:
            with os.fdopen(fd, 'wb') as f:
                f.write(data)
            meta = read_msi_metadata(path)
        finally:
            os.unlink(path)

        self.assertEqual(meta.product_name, '7-Zip 24.08 (x64)')
        self.assertEqual(meta.product_version, '24.08.00.0')
        self.assertEqual(meta.product_code, '{23170F69-40C1-2702-2408-000001000000}')
        self.assertEqual(meta.upgrade_code, '{23170F69-40C1-2702-0000-000004000000}')
        self.assertEqual(meta.manufacturer, 'Igor Pavlov')
        self.assertEqual(meta.language, '1033')
        # Sanity: none of the sub-storage's poison values bled through.
        self.assertNotIn('WRONG', meta.product_name)
        self.assertNotIn('00000000-0000-0000-0000-000000000000', meta.product_code)


class TestParseInstallCommand(unittest.TestCase):
    def test_parses_seven_zip_example(self):
        parsed = parse_install_command('msiexec.exe /i 7z2408-x64.msi /qn /norestart')
        self.assertEqual(parsed.action, 'install')
        self.assertEqual(parsed.msi_file, '7z2408-x64.msi')
        self.assertEqual(parsed.switches, ['/qn', '/norestart'])
        self.assertEqual(parsed.properties, {})

    def test_case_insensitive_action_flag(self):
        parsed = parse_install_command('msiexec /I app.msi /quiet')
        self.assertEqual(parsed.action, 'install')
        self.assertEqual(parsed.msi_file, 'app.msi')

    def test_public_properties_captured(self):
        parsed = parse_install_command('msiexec /i app.msi ALLUSERS=1 /qn')
        self.assertEqual(parsed.properties, {'ALLUSERS': '1'})
        self.assertEqual(parsed.switches, ['/qn'])

    def test_uninstall_action(self):
        parsed = parse_install_command('msiexec /x {GUID} /qn')
        self.assertEqual(parsed.action, 'uninstall')

    def test_empty_command(self):
        parsed = parse_install_command('')
        self.assertIsInstance(parsed, ParsedInstallCommand)
        self.assertIsNone(parsed.msi_file)

    def test_rebuild_preserves_switches_and_properties(self):
        parsed = parse_install_command('msiexec /i original.msi ALLUSERS=1 /qn /norestart')
        rebuilt = parsed.rebuild('renamed.msi')
        self.assertEqual(rebuilt, 'msiexec /i renamed.msi /qn /norestart ALLUSERS=1')


class TestUninstallCommand(unittest.TestCase):
    def test_prefers_product_code(self):
        self.assertEqual(
            build_uninstall_command('{GUID}'),
            'msiexec /x {GUID} /qn /norestart',
        )

    def test_custom_switches(self):
        self.assertEqual(
            build_uninstall_command('{GUID}', ['/quiet']),
            'msiexec /x {GUID} /quiet',
        )

    def test_falls_back_to_filename(self):
        self.assertEqual(
            build_uninstall_command('', None, 'app.msi'),
            'msiexec /x app.msi /qn /norestart',
        )

    def test_no_target_returns_noop(self):
        self.assertEqual(build_uninstall_command('', None, None), 'cmd /c exit 0')


class TestDetectionRule(unittest.TestCase):
    def test_rule_with_version(self):
        rule = build_product_code_detection_rule('{GUID}', '24.08.00.0')
        self.assertEqual(rule['@odata.type'], '#microsoft.graph.win32LobAppProductCodeRule')
        self.assertEqual(rule['ruleType'], 'detection')
        self.assertEqual(rule['productCode'], '{GUID}')
        self.assertEqual(rule['productVersion'], '24.08.00.0')
        self.assertEqual(rule['productVersionOperator'], 'greaterThanOrEqual')

    def test_rule_without_version(self):
        rule = build_product_code_detection_rule('{GUID}')
        self.assertEqual(rule['productVersionOperator'], 'notConfigured')
        self.assertIsNone(rule['productVersion'])


class TestResolveLocalPath(unittest.TestCase):
    def test_http_url_is_not_local(self):
        self.assertIsNone(resolve_local_path('http://example.com/app.msi'))
        self.assertIsNone(resolve_local_path('https://example.com/app.msi'))

    def test_plain_path_is_local(self):
        self.assertEqual(resolve_local_path('/tmp/app.msi'), Path('/tmp/app.msi'))

    def test_file_uri_is_local(self):
        self.assertEqual(resolve_local_path('file:///tmp/app.msi'), Path('/tmp/app.msi'))

    def test_empty_returns_none(self):
        self.assertIsNone(resolve_local_path(''))


class TestMSIMetadataModel(unittest.TestCase):
    def test_from_dict_ignores_unknown_keys(self):
        meta = MSIMetadata.from_dict({'product_name': '7-Zip', 'bogus': 'x'})
        self.assertEqual(meta.product_name, '7-Zip')

    def test_round_trip(self):
        meta = MSIMetadata(product_name='7-Zip', product_version='24.08.00.0')
        self.assertEqual(MSIMetadata.from_dict(meta.to_dict()), meta)


if __name__ == '__main__':
    unittest.main()
