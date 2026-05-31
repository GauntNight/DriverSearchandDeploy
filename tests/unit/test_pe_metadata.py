"""Unit tests for autopackager.utils.pe_metadata.

The PE metadata reader is pure-Python: parses the PE COFF + optional header,
walks the resource directory tree to find RT_VERSION, decodes
VS_VERSIONINFO -> StringFileInfo -> StringTable -> Strings. Tests cover:

  - _parse_vs_versioninfo against hand-crafted bytes (the format is
    self-contained, so this exercises the inner decoder hermetically).
  - sha256_file against a real binary on disk.
  - read_pe_metadata smoke against tools/IntuneWinAppUtil.exe -- a real,
    Microsoft-signed PE that ships in the repo. This is technically a
    fixture-dependent test but the binary is committed.
  - read_pe_metadata error paths (non-PE file, missing PE signature).
"""

from __future__ import annotations

import struct
import tempfile
from pathlib import Path

import pytest

from autopackager.utils.pe_metadata import (
    PEMetadata,
    PEParseError,
    _parse_vs_versioninfo,
    read_pe_metadata,
    sha256_file,
)


def _wcstr(s: str) -> bytes:
    """Return a UTF-16-LE null-terminated WCHAR string."""
    return s.encode('utf-16-le') + b'\x00\x00'


def _key_padded_for_block(key: str) -> bytes:
    """Return key bytes padded so (6-byte header + key + padding) is 4-aligned.

    All four VersionInfo block types share the same shape: 6-byte header
    (wLength, wValueLength, wType) followed by a WCHAR null-terminated
    key. The key must be padded so the next field (value or children)
    starts on a 32-bit boundary RELATIVE TO THE START OF THE BLOB,
    which means we account for the 6-byte header here.
    """
    raw = _wcstr(key)
    pad = (-(6 + len(raw))) % 4
    return raw + b'\x00' * pad


def _pad_to_4(b: bytes) -> bytes:
    """Pad a block to a 4-byte boundary (trailing padding for the next block)."""
    return b + b'\x00' * ((-len(b)) % 4)


def _build_string_entry(key: str, value: str) -> bytes:
    """Build a String block: header + key + value (WCHAR-aligned).

    String block layout (per Win32 VersionInfo spec):
      wLength (2): total bytes of this block (no trailing pad)
      wValueLength (2): length of value IN CHARACTERS (incl null)
      wType (2): 1 = text
      szKey (WCHAR null-terminated, padded so value is 4-aligned)
      Value (WCHAR null-terminated)
    """
    key_bytes = _key_padded_for_block(key)
    val_wchars = value + '\x00'
    val_bytes = val_wchars.encode('utf-16-le')
    body = key_bytes + val_bytes
    header = struct.pack('<HHH', 6 + len(body), len(val_wchars), 1)
    return _pad_to_4(header + body)


def _build_string_table(locale: str, strings: list) -> bytes:
    """Build one StringTable block containing several String children."""
    key_bytes = _key_padded_for_block(locale)
    children = b''.join(_build_string_entry(k, v) for k, v in strings)
    body = key_bytes + children
    header = struct.pack('<HHH', 6 + len(body), 0, 1)
    return _pad_to_4(header + body)


def _build_string_file_info(tables: list) -> bytes:
    key_bytes = _key_padded_for_block('StringFileInfo')
    children = b''.join(tables)
    body = key_bytes + children
    header = struct.pack('<HHH', 6 + len(body), 0, 1)
    return _pad_to_4(header + body)


def _build_vs_versioninfo(string_pairs: list) -> bytes:
    """Build a complete VS_VERSIONINFO blob with a single StringTable."""
    key_bytes = _key_padded_for_block('VS_VERSION_INFO')
    fixed = b'\x00' * 52  # VS_FIXEDFILEINFO (binary, we don't need contents)
    sfi = _build_string_file_info([_build_string_table('040904E4', string_pairs)])
    body = key_bytes + fixed + sfi
    header = struct.pack('<HHH', 6 + len(body), 52, 0)
    return _pad_to_4(header + body)


class TestParseVsVersionInfo:
    """The inner VS_VERSIONINFO decoder runs on bytes already extracted
    from the PE .rsrc section -- isolates the structural walking from the
    PE container parsing.
    """

    def test_extracts_known_string_keys(self):
        blob = _build_vs_versioninfo([
            ('CompanyName', 'Acme Corp'),
            ('ProductName', 'Acme Widget'),
            ('ProductVersion', '1.2.3.4'),
            ('FileVersion', '1.2.3.4'),
            ('OriginalFilename', 'widget-setup.exe'),
        ])
        meta = _parse_vs_versioninfo(blob)
        assert meta.company_name == 'Acme Corp'
        assert meta.product_name == 'Acme Widget'
        assert meta.product_version == '1.2.3.4'
        assert meta.file_version == '1.2.3.4'
        assert meta.original_filename == 'widget-setup.exe'

    def test_unknown_keys_land_in_all_strings(self):
        blob = _build_vs_versioninfo([
            ('InternalName', 'widget'),
            ('Comments', 'Custom build for QA'),
        ])
        meta = _parse_vs_versioninfo(blob)
        assert meta.all_strings.get('InternalName') == 'widget'
        assert meta.all_strings.get('Comments') == 'Custom build for QA'
        # Canonical fields are blank when the key isn't supplied
        assert meta.product_name == ''
        assert meta.company_name == ''

    def test_empty_blob_returns_empty_metadata(self):
        meta = _parse_vs_versioninfo(b'')
        assert meta == PEMetadata()

    def test_truncated_blob_doesnt_crash(self):
        # The format is self-describing via wLength but a truncated blob
        # should be tolerated -- error means we missed an offset check.
        blob = _build_vs_versioninfo([('CompanyName', 'X')])
        truncated = blob[: len(blob) // 2]
        meta = _parse_vs_versioninfo(truncated)
        # Just ensure no exception; field contents are best-effort
        assert isinstance(meta, PEMetadata)


class TestSha256File:
    def test_returns_hex_digest_of_64_chars(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b'hello world')
            path = f.name
        try:
            digest = sha256_file(path)
            assert len(digest) == 64
            assert all(c in '0123456789abcdef' for c in digest)
            # Known SHA-256 of "hello world"
            assert digest == 'b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9'
        finally:
            Path(path).unlink()


class TestReadPeMetadata:
    """Tests against a real PE binary committed to the repo
    (tools/IntuneWinAppUtil.exe -- Microsoft-signed, ships in the install).
    The Microsoft binary has every standard VS_VERSIONINFO field set, so
    it's a stable golden fixture.
    """

    INTUNE_TOOL = Path(__file__).resolve().parents[2] / 'tools' / 'IntuneWinAppUtil.exe'

    @pytest.fixture(autouse=True)
    def _skip_if_no_pe(self):
        if not self.INTUNE_TOOL.exists():
            pytest.skip(f"Fixture PE not present: {self.INTUNE_TOOL}")

    def test_extracts_microsoft_intune_metadata(self):
        meta = read_pe_metadata(self.INTUNE_TOOL)
        assert meta.company_name == 'Microsoft Corporation'
        assert 'Intune' in meta.product_name
        # IntuneWinAppUtil.exe is on the 6.2509.x line (file version, not
        # the longer composite product version which includes a git sha).
        assert meta.file_version.startswith('6.')
        assert meta.original_filename == 'IntuneWinAppUtil.exe'
        # all_strings is a superset of the typed fields
        assert 'LegalCopyright' in meta.all_strings

    def test_rejects_non_pe_file(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b'this is not a PE binary at all')
            path = f.name
        try:
            with pytest.raises(PEParseError, match='MZ signature'):
                read_pe_metadata(path)
        finally:
            Path(path).unlink()

    def test_rejects_mz_without_pe_signature(self):
        # Just an MZ header pointing past the file: the secondary PE
        # signature check has to catch this, not just the MZ check.
        with tempfile.NamedTemporaryFile(delete=False) as f:
            blob = bytearray(0x80)
            blob[:2] = b'MZ'
            struct.pack_into('<I', blob, 0x3C, 0x1000)  # e_lfanew past EOF
            f.write(bytes(blob))
            path = f.name
        try:
            with pytest.raises(PEParseError, match='PE signature'):
                read_pe_metadata(path)
        finally:
            Path(path).unlink()


class TestPEMetadataDataclass:
    def test_to_dict_round_trip(self):
        m = PEMetadata(
            company_name='X', product_name='Y', product_version='1.0',
            all_strings={'CompanyName': 'X', 'ProductName': 'Y', 'ProductVersion': '1.0'},
        )
        d = m.to_dict()
        m2 = PEMetadata.from_dict(d)
        assert m2.company_name == 'X'
        assert m2.product_name == 'Y'
        assert m2.product_version == '1.0'
        assert m2.all_strings == d['all_strings']

    def test_from_dict_ignores_unknown_keys(self):
        m = PEMetadata.from_dict({
            'company_name': 'X',
            'rogue_field': 'should be ignored',
        })
        assert m.company_name == 'X'
        # No exception, no leakage of unknown attributes
