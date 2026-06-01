"""PE / EXE version metadata extraction (pure Python, cross-platform).

Mirrors the contract of ``msi_metadata.py`` for Windows PE32 / PE32+
executables. Reads the VS_VERSIONINFO resource (RT_VERSION, type 16) from
the .rsrc section and returns the StringFileInfo strings used to identify
the binary in the installer catalog: CompanyName, ProductName,
ProductVersion, FileVersion, OriginalFilename, FileDescription.

No external dependencies (no pefile / pywin32). Stays consistent with the
existing ``msi_metadata.py`` pure-Python pattern so the pipeline still
runs on Linux CI for tests.

Reference: Microsoft PE Format spec; VS_VERSIONINFO format documented at
https://learn.microsoft.com/en-us/windows/win32/menurc/vs-versioninfo
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict


class PEParseError(Exception):
    """Raised when a file cannot be parsed as a PE binary."""


@dataclass
class PEMetadata:
    """VS_VERSIONINFO StringFileInfo strings from a PE binary.

    Empty strings (rather than None) mean the field wasn't present in the
    VS_VERSIONINFO -- typical for installers that omit fields like
    OriginalFilename. ``all_strings`` carries the full key->value dict so
    callers can look up non-canonical keys (e.g., InternalName).
    """

    company_name: str = ''
    product_name: str = ''
    product_version: str = ''
    file_version: str = ''
    file_description: str = ''
    original_filename: str = ''
    legal_copyright: str = ''
    all_strings: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> 'PEMetadata':
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in (data or {}).items() if k in known})


def sha256_file(path) -> str:
    """Hex SHA-256 digest of a file, streamed in 1 MiB chunks."""
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


# PE / resource constants
_DOS_MAGIC = b'MZ'
_PE_MAGIC = b'PE\x00\x00'
_RT_VERSION = 16  # Windows resource type id for VS_VERSIONINFO


def _align4(n: int) -> int:
    return (n + 3) & ~3


def read_pe_metadata(pe_path) -> PEMetadata:
    """Parse a PE binary and return its VS_VERSIONINFO StringFileInfo strings.

    Raises PEParseError when the file isn't a PE; returns an empty
    PEMetadata (all fields blank) when the PE has no version resource.
    """
    path = Path(pe_path)
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise PEParseError(f"Could not read file: {exc}") from exc

    if len(data) < 0x40 or data[:2] != _DOS_MAGIC:
        raise PEParseError("Not a PE binary (missing MZ signature)")

    e_lfanew = struct.unpack_from('<I', data, 0x3C)[0]
    if e_lfanew + 24 > len(data) or data[e_lfanew:e_lfanew + 4] != _PE_MAGIC:
        raise PEParseError("Not a PE binary (missing PE signature)")

    coff_off = e_lfanew + 4
    num_sections = struct.unpack_from('<H', data, coff_off + 2)[0]
    size_of_optional = struct.unpack_from('<H', data, coff_off + 16)[0]
    section_table_off = coff_off + 20 + size_of_optional

    # Locate the .rsrc section (40 bytes per IMAGE_SECTION_HEADER)
    rsrc = None
    for i in range(num_sections):
        sec_off = section_table_off + i * 40
        name = data[sec_off:sec_off + 8].rstrip(b'\x00').decode('latin-1', errors='replace')
        if name == '.rsrc':
            rsrc = {
                'virtual_address': struct.unpack_from('<I', data, sec_off + 12)[0],
                'pointer_to_raw_data': struct.unpack_from('<I', data, sec_off + 20)[0],
            }
            break
    if not rsrc:
        return PEMetadata()  # PE without version info -- still valid

    rsrc_base_file = rsrc['pointer_to_raw_data']
    rsrc_base_rva = rsrc['virtual_address']

    def rva_to_file(rva: int) -> int:
        return rsrc_base_file + (rva - rsrc_base_rva)

    def read_dir(offset: int):
        """Iterate IMAGE_RESOURCE_DIRECTORY_ENTRY children.

        Yields (name_or_id, is_directory, sub_offset). ``sub_offset`` is
        relative to the resource directory base (rsrc_base_file).
        """
        if offset + 16 > len(data):
            return
        named = struct.unpack_from('<H', data, offset + 12)[0]
        id_entries = struct.unpack_from('<H', data, offset + 14)[0]
        for i in range(named + id_entries):
            ent = offset + 16 + i * 8
            if ent + 8 > len(data):
                return
            name_or_id = struct.unpack_from('<I', data, ent)[0]
            child = struct.unpack_from('<I', data, ent + 4)[0]
            is_dir = bool(child & 0x80000000)
            child &= 0x7FFFFFFF
            yield name_or_id, is_dir, child

    # Walk root -> Type=RT_VERSION -> first Name -> first Language -> data
    version_type_dir = None
    for nid, is_dir, child in read_dir(rsrc_base_file):
        # Type entries: low 31 bits are the id when high bit is 0
        if not (nid & 0x80000000) and nid == _RT_VERSION and is_dir:
            version_type_dir = rsrc_base_file + child
            break
    if version_type_dir is None:
        return PEMetadata()

    name_entries = list(read_dir(version_type_dir))
    if not name_entries:
        return PEMetadata()
    _, is_dir, child = name_entries[0]
    if not is_dir:
        return PEMetadata()
    name_dir = rsrc_base_file + child

    lang_entries = list(read_dir(name_dir))
    if not lang_entries:
        return PEMetadata()
    _, is_dir, child = lang_entries[0]
    if is_dir:
        return PEMetadata()
    data_entry = rsrc_base_file + child

    # IMAGE_RESOURCE_DATA_ENTRY: OffsetToData (RVA), Size, CodePage, Reserved
    if data_entry + 16 > len(data):
        return PEMetadata()
    rva = struct.unpack_from('<I', data, data_entry)[0]
    size = struct.unpack_from('<I', data, data_entry + 4)[0]
    vs_off = rva_to_file(rva)
    if vs_off < 0 or vs_off + size > len(data):
        return PEMetadata()
    return _parse_vs_versioninfo(data[vs_off: vs_off + size])


def _parse_vs_versioninfo(blob: bytes) -> PEMetadata:
    """Parse a VS_VERSIONINFO binary structure into PEMetadata.

    The format is a nested tree of WCHAR-keyed blocks with 32-bit alignment
    after each (key, value) pair. Block header: ``wLength`` (2),
    ``wValueLength`` (2), ``wType`` (2). For VS_VERSIONINFO itself the
    value is a VS_FIXEDFILEINFO struct; for child StringFileInfo /
    VarFileInfo blocks the value is empty and the payload is more nested
    blocks. We only need the StringFileInfo->StringTable->String chain.
    """
    meta = PEMetadata()
    if len(blob) < 6:
        return meta

    w_length = struct.unpack_from('<H', blob, 0)[0]
    w_value_length = struct.unpack_from('<H', blob, 2)[0]
    pos = 6
    # szKey "VS_VERSION_INFO" (WCHAR null-terminated)
    while pos + 2 <= len(blob) and blob[pos:pos + 2] != b'\x00\x00':
        pos += 2
    pos += 2
    pos = _align4(pos)
    # VS_FIXEDFILEINFO occupies w_value_length bytes (binary, not WCHAR)
    if w_value_length:
        pos += w_value_length
        pos = _align4(pos)

    end = min(w_length, len(blob))
    while pos + 6 <= end:
        ch_len = struct.unpack_from('<H', blob, pos)[0]
        if ch_len == 0:
            break
        ch_end = min(pos + ch_len, len(blob))
        kp = pos + 6
        kp_start = kp
        while kp + 2 <= len(blob) and blob[kp:kp + 2] != b'\x00\x00':
            kp += 2
        key = blob[kp_start:kp].decode('utf-16-le', errors='replace')
        kp += 2
        kp = _align4(kp)

        if key == 'StringFileInfo':
            _parse_string_file_info(blob, kp, ch_end, meta.all_strings)

        pos = _align4(pos + ch_len)

    meta.company_name = meta.all_strings.get('CompanyName', '')
    meta.product_name = meta.all_strings.get('ProductName', '')
    meta.product_version = meta.all_strings.get('ProductVersion', '')
    meta.file_version = meta.all_strings.get('FileVersion', '')
    meta.file_description = meta.all_strings.get('FileDescription', '')
    meta.original_filename = meta.all_strings.get('OriginalFilename', '')
    meta.legal_copyright = meta.all_strings.get('LegalCopyright', '')
    return meta


def _parse_string_file_info(blob: bytes, start: int, end: int, out: Dict[str, str]) -> None:
    """Walk StringFileInfo -> StringTable* -> String*, populating ``out``.

    The String block's ``wValueLength`` is supposed to be the value's length
    in WORDS (Unicode chars including the terminating null). In the wild
    several installer toolchains set it wrong -- WiX Burn (PowerToys uses
    this), older NSIS, custom Microsoft bootstrappers -- some write the
    length in BYTES, some include the null terminator inconsistently, some
    leave it zero. Trusting it produces values that bleed into the next
    String entry's bytes ("Microsoft Corporation X0 FileDescription Po...").

    Defensive read: ignore ``wValueLength`` and walk the value as a
    null-terminated WCHAR string bounded by the block's wLength. This
    matches what Windows' actual VerQueryValue does for the same blobs
    and produces the strings the Intune portal / inspect-exe expect.
    """
    st_pos = start
    while st_pos + 6 <= end:
        st_len = struct.unpack_from('<H', blob, st_pos)[0]
        if st_len == 0:
            break
        st_end = min(st_pos + st_len, end)
        # Skip the StringTable key (locale code like "040904E4")
        kp = st_pos + 6
        while kp + 2 <= len(blob) and blob[kp:kp + 2] != b'\x00\x00':
            kp += 2
        kp += 2
        kp = _align4(kp)
        # String entries follow
        s_pos = kp
        while s_pos + 6 <= st_end:
            s_len = struct.unpack_from('<H', blob, s_pos)[0]
            if s_len == 0:
                break
            s_end = min(s_pos + s_len, st_end)
            sk_pos = s_pos + 6
            sk_start = sk_pos
            while sk_pos + 2 <= s_end and blob[sk_pos:sk_pos + 2] != b'\x00\x00':
                sk_pos += 2
            key = blob[sk_start:sk_pos].decode('utf-16-le', errors='replace')
            sk_pos += 2
            sk_pos = _align4(sk_pos)
            # Read value as a null-terminated WCHAR string bounded by the
            # String block's own wLength (s_end). See docstring for why
            # wValueLength isn't trusted here.
            ve = sk_pos
            while ve + 2 <= s_end and blob[ve:ve + 2] != b'\x00\x00':
                ve += 2
            value = blob[sk_pos:ve].decode('utf-16-le', errors='replace')
            out[key] = value
            s_pos = _align4(s_pos + s_len)
        st_pos = _align4(st_pos + st_len)
