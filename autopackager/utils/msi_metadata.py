"""MSI metadata extraction and ``msiexec`` command parsing.

This module lets the factory take a plain MSI install command such as::

    msiexec.exe /i 7z2408-x64.msi /qn /norestart

plus the MSI file itself, and automatically derive everything Intune needs:
product name, version, publisher, product code (for uninstall + detection) and
the upgrade code.

Reading MSI metadata is normally done through the Windows Installer COM API,
which only exists on Windows.  To stay cross-platform (the rest of the factory
already parses OEM catalogs on Linux/CI) the reader parses the MSI's OLE2
Compound File structure and ``Property`` table directly in pure Python — no
external tools or third-party packages required.
"""

import codecs
import shlex
import struct
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlparse
from urllib.request import url2pathname


class MSIParseError(Exception):
    """Raised when a file cannot be parsed as an MSI/OLE2 compound file."""


# ---------------------------------------------------------------------------
# OLE2 / Compound File Binary Format reader (minimal, read-only)
# ---------------------------------------------------------------------------

class _CompoundFile:
    """Minimal reader for the OLE2 Compound File container used by MSI files."""

    _SIGNATURE = b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'
    ENDOFCHAIN = 0xFFFFFFFE
    FREESECT = 0xFFFFFFFF

    def __init__(self, data: bytes):
        if data[:8] != self._SIGNATURE:
            raise MSIParseError("Not an OLE2 compound file (bad signature)")

        self.data = data
        self.sector_size = 1 << struct.unpack_from('<H', data, 0x1e)[0]
        self.mini_sector_size = 1 << struct.unpack_from('<H', data, 0x20)[0]
        self.first_dir_sector = struct.unpack_from('<I', data, 0x30)[0]
        self.mini_cutoff = struct.unpack_from('<I', data, 0x38)[0]
        self.first_minifat_sector = struct.unpack_from('<I', data, 0x3c)[0]
        self.num_minifat_sectors = struct.unpack_from('<I', data, 0x40)[0]
        self.first_difat_sector = struct.unpack_from('<I', data, 0x44)[0]
        self.num_difat_sectors = struct.unpack_from('<I', data, 0x48)[0]

        self._build_fat()
        self._build_minifat()
        self._read_directory()

    def _sector_bytes(self, sector: int) -> bytes:
        off = (sector + 1) * self.sector_size
        return self.data[off: off + self.sector_size]

    def _difat(self) -> List[int]:
        entries = list(struct.unpack_from('<109I', self.data, 0x4c))
        sector = self.first_difat_sector
        per_sector = self.sector_size // 4
        remaining = self.num_difat_sectors
        while sector not in (self.ENDOFCHAIN, self.FREESECT) and remaining > 0:
            block = struct.unpack_from('<%dI' % per_sector, self._sector_bytes(sector))
            entries.extend(block[:-1])
            sector = block[-1]
            remaining -= 1
        return [s for s in entries if s != self.FREESECT]

    def _build_fat(self):
        per_sector = self.sector_size // 4
        fat: List[int] = []
        for fat_sector in self._difat():
            fat.extend(struct.unpack_from('<%dI' % per_sector, self._sector_bytes(fat_sector)))
        self.fat = fat

    def _chain(self, start: int) -> List[int]:
        sectors: List[int] = []
        seen = set()
        s = start
        while s not in (self.ENDOFCHAIN, self.FREESECT) and s < len(self.fat):
            if s in seen:
                break
            seen.add(s)
            sectors.append(s)
            s = self.fat[s]
        return sectors

    def _read_chain(self, start: int) -> bytes:
        return b''.join(self._sector_bytes(s) for s in self._chain(start))

    def _build_minifat(self):
        if self.num_minifat_sectors and self.first_minifat_sector not in (self.ENDOFCHAIN, self.FREESECT):
            raw = self._read_chain(self.first_minifat_sector)
            self.minifat = list(struct.unpack_from('<%dI' % (len(raw) // 4), raw))
        else:
            self.minifat = []

    def _read_directory(self):
        raw = self._read_chain(self.first_dir_sector)
        self.entries = []
        for i in range(0, len(raw) - 127, 128):
            entry = raw[i:i + 128]
            name_len = struct.unpack_from('<H', entry, 0x40)[0]
            name = entry[:name_len - 2].decode('utf-16-le', errors='replace') if name_len >= 2 else ''
            self.entries.append({
                'name': name,
                'type': entry[0x42],
                'start': struct.unpack_from('<I', entry, 0x74)[0],
                'size': struct.unpack_from('<Q', entry, 0x78)[0],
            })
        self.root = next((e for e in self.entries if e['type'] == 5), None)

    def _read_minichain(self, start: int, size: int) -> bytes:
        if not hasattr(self, '_ministream'):
            self._ministream = self._read_chain(self.root['start']) if self.root else b''
        chunks = []
        seen = set()
        s = start
        while s not in (self.ENDOFCHAIN, self.FREESECT) and s < len(self.minifat):
            if s in seen:
                break
            seen.add(s)
            off = s * self.mini_sector_size
            chunks.append(self._ministream[off: off + self.mini_sector_size])
            s = self.minifat[s]
        return b''.join(chunks)[:size]

    def read_stream(self, entry: dict) -> bytes:
        size = entry['size']
        if size < self.mini_cutoff:
            return self._read_minichain(entry['start'], size)
        return self._read_chain(entry['start'])[:size]


# ---------------------------------------------------------------------------
# MSI table decoding
# ---------------------------------------------------------------------------

# Base64-style alphabet MSI uses to mangle table/stream names.
_MIME_B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._"


def decode_streamname(name: str) -> str:
    """Decode an MSI-mangled stream name back to its table name.

    MSI encodes table names so each source character maps into the
    ``0x3800``-``0x4840`` Unicode range. Characters outside that range
    (e.g. the ``\\x05SummaryInformation`` stream) pass through unchanged.
    """
    out = []
    for ch in name:
        c = ord(ch)
        if 0x3800 <= c < 0x4840:
            if c >= 0x4800:
                out.append(_MIME_B64[c - 0x4800])
                continue
            c -= 0x3800
            out.append(_MIME_B64[c & 0x3f])
            out.append(_MIME_B64[(c >> 6) & 0x3f])
            continue
        out.append(ch)
    return ''.join(out)


def _codepage_to_encoding(codepage: int) -> str:
    if not codepage:
        return 'cp1252'
    if codepage == 65001:
        return 'utf-8'
    try:
        codecs.lookup('cp%d' % codepage)
        return 'cp%d' % codepage
    except LookupError:
        return 'cp1252'


def _streams_by_table_name(cfb: _CompoundFile) -> Dict[str, dict]:
    return {
        decode_streamname(e['name']): e
        for e in cfb.entries
        if e['type'] == 2
    }


def _load_string_pool(cfb: _CompoundFile, streams: Dict[str, dict]):
    """Return ``(strings, bytes_per_strref)`` decoded from the MSI string pool.

    ``strings`` is a list indexed by string id (id 0 is the empty string).
    """
    pool_entry = streams.get('_StringPool')
    data_entry = streams.get('_StringData')
    if not pool_entry or not data_entry:
        return [''], 2

    pool = cfb.read_stream(pool_entry)
    sdata = cfb.read_stream(data_entry)

    codepage = struct.unpack_from('<I', pool, 0)[0] if len(pool) >= 4 else 0
    bytes_per_strref = 3 if (codepage & 0x80000000) else 2
    encoding = _codepage_to_encoding(codepage & 0x7fffffff)

    strings = ['']
    offset = 0
    i = 4  # entries start after the 4-byte codepage header
    n = len(pool)
    while i + 4 <= n:
        length = struct.unpack_from('<H', pool, i)[0]
        refcount = struct.unpack_from('<H', pool, i + 2)[0]
        i += 4
        # A zero-length entry with a non-zero refcount marks a "long" string:
        # the real length spans this entry's refcount (high word) and the next
        # entry's length (low word), consuming an extra pool slot.
        if length == 0 and refcount != 0 and i + 4 <= n:
            low = struct.unpack_from('<H', pool, i)[0]
            i += 4
            length = (refcount << 16) | low
        strings.append(sdata[offset: offset + length].decode(encoding, errors='replace') if length else '')
        offset += length

    return strings, bytes_per_strref


def _read_property_table(cfb: _CompoundFile, streams: Dict[str, dict]) -> Dict[str, str]:
    """Decode the MSI ``Property`` table into a ``{name: value}`` dict.

    The ``Property`` table has a fixed two-column string schema
    (``Property``, ``Value``), and MSI stores table cells column-major, so the
    stream is simply ``N`` property-name string refs followed by ``N`` value
    string refs.
    """
    prop_entry = streams.get('Property')
    if not prop_entry:
        return {}

    strings, strref = _load_string_pool(cfb, streams)
    if len(strings) <= 1:
        return {}

    raw = cfb.read_stream(prop_entry)
    rows = (len(raw) // strref) // 2
    if rows == 0:
        return {}

    def ref(index: int) -> int:
        off = index * strref
        if strref == 2:
            return struct.unpack_from('<H', raw, off)[0]
        chunk = raw[off:off + 3]
        return chunk[0] | (chunk[1] << 8) | (chunk[2] << 16)

    props: Dict[str, str] = {}
    for r in range(rows):
        name = strings[ref(r)] if ref(r) < len(strings) else ''
        value = strings[ref(rows + r)] if ref(rows + r) < len(strings) else ''
        if name:
            props[name] = value
    return props


def _read_propvalue(data: bytes, pos: int, vtype: int):
    if vtype == 0x1e:  # VT_LPSTR
        length = struct.unpack_from('<I', data, pos + 4)[0]
        return data[pos + 8: pos + 8 + length].rstrip(b'\x00').decode('cp1252', errors='replace')
    if vtype == 0x1f:  # VT_LPWSTR
        length = struct.unpack_from('<I', data, pos + 4)[0]
        return data[pos + 8: pos + 8 + length * 2].decode('utf-16-le', errors='replace').rstrip('\x00')
    if vtype == 0x03:  # VT_I4
        return struct.unpack_from('<i', data, pos + 4)[0]
    if vtype == 0x02:  # VT_I2
        return struct.unpack_from('<h', data, pos + 4)[0]
    return None


def _read_summary_information(cfb: _CompoundFile) -> Dict[str, object]:
    entry = next(
        (e for e in cfb.entries
         if e['type'] == 2 and e['name'] and ord(e['name'][0]) == 5 and 'SummaryInformation' in e['name']),
        None,
    )
    if not entry:
        return {}

    data = cfb.read_stream(entry)
    if len(data) < 48:
        return {}

    section_offset = struct.unpack_from('<I', data, 44)[0]
    if section_offset + 8 > len(data):
        return {}

    num_props = struct.unpack_from('<I', data, section_offset + 4)[0]
    pidsi = {
        2: 'title', 3: 'subject', 4: 'author', 6: 'comments',
        7: 'template', 9: 'revision_number', 18: 'creating_application',
    }

    result: Dict[str, object] = {}
    p = section_offset + 8
    for _ in range(num_props):
        if p + 8 > len(data):
            break
        pid = struct.unpack_from('<I', data, p)[0]
        value_offset = struct.unpack_from('<I', data, p + 4)[0]
        p += 8
        if pid not in pidsi:
            continue
        vpos = section_offset + value_offset
        if vpos + 4 > len(data):
            continue
        vtype = struct.unpack_from('<I', data, vpos)[0]
        value = _read_propvalue(data, vpos, vtype)
        if value not in (None, ''):
            result[pidsi[pid]] = value
    return result


# ---------------------------------------------------------------------------
# Public metadata model + reader
# ---------------------------------------------------------------------------

@dataclass
class MSIMetadata:
    """Metadata extracted from an MSI file."""

    product_name: str = ''
    product_version: str = ''
    product_code: str = ''
    upgrade_code: str = ''
    manufacturer: str = ''
    language: str = ''
    package_code: str = ''
    title: str = ''
    subject: str = ''
    comments: str = ''
    creating_application: str = ''
    all_properties: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> 'MSIMetadata':
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in (data or {}).items() if k in known})


def read_msi_metadata(msi_path) -> MSIMetadata:
    """Parse an MSI file and return its metadata.

    Raises :class:`MSIParseError` if the file is not a readable MSI.
    """
    path = Path(msi_path)
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise MSIParseError(f"Could not read MSI file: {exc}") from exc

    cfb = _CompoundFile(data)
    streams = _streams_by_table_name(cfb)
    props = _read_property_table(cfb, streams)

    try:
        summary = _read_summary_information(cfb)
    except Exception:
        summary = {}

    return MSIMetadata(
        product_name=props.get('ProductName') or str(summary.get('subject', '')),
        product_version=props.get('ProductVersion', ''),
        product_code=props.get('ProductCode', ''),
        upgrade_code=props.get('UpgradeCode', ''),
        manufacturer=props.get('Manufacturer') or str(summary.get('author', '')),
        language=props.get('ProductLanguage', ''),
        package_code=str(summary.get('revision_number', '')),
        title=str(summary.get('title', '')),
        subject=str(summary.get('subject', '')),
        comments=props.get('ARPCOMMENTS') or str(summary.get('comments', '')),
        creating_application=str(summary.get('creating_application', '')),
        all_properties=props,
    )


# ---------------------------------------------------------------------------
# msiexec command parsing
# ---------------------------------------------------------------------------

_ACTION_FLAGS = {
    '/i': 'install', '/package': 'install',
    '/x': 'uninstall', '/uninstall': 'uninstall',
    '/a': 'admin',
    '/j': 'advertise',
    '/p': 'patch', '/update': 'patch',
    '/f': 'repair',
}


@dataclass
class ParsedInstallCommand:
    """A parsed ``msiexec`` command line."""

    executable: str = 'msiexec'
    action: str = 'install'
    msi_file: Optional[str] = None
    properties: Dict[str, str] = field(default_factory=dict)
    switches: List[str] = field(default_factory=list)
    raw: str = ''

    def rebuild(self, installer_filename: str) -> str:
        """Rebuild the command pointing at ``installer_filename``.

        Used so the Intune ``installCommandLine`` references the installer as it
        is actually named inside the .intunewin package, while preserving the
        admin-supplied switches and public properties.
        """
        flag = {
            'install': '/i', 'uninstall': '/x', 'admin': '/a', 'patch': '/p', 'repair': '/f',
        }.get(self.action, '/i')
        parts = ['msiexec', flag, installer_filename]
        parts.extend(self.switches)
        parts.extend(f'{k}={v}' for k, v in self.properties.items())
        return ' '.join(parts)


def parse_install_command(command: str) -> ParsedInstallCommand:
    """Parse an ``msiexec`` install command into its components."""
    parsed = ParsedInstallCommand(raw=command or '')
    if not command:
        return parsed

    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        tokens = command.split()

    if not tokens:
        return parsed

    parsed.executable = tokens[0]
    action_seen = False

    for token in tokens[1:]:
        lowered = token.lower()

        # Public property assignment, e.g. ALLUSERS=1 or INSTALLDIR="C:\App"
        if '=' in token and not token.startswith(('/', '-')):
            key, _, value = token.partition('=')
            if key and key[0].isalpha():
                parsed.properties[key] = value
                continue

        if lowered.endswith(('.msi', '.msp')):
            parsed.msi_file = token
            continue

        flag = '/' + lowered.lstrip('/-')
        if flag in _ACTION_FLAGS and not action_seen:
            parsed.action = _ACTION_FLAGS[flag]
            action_seen = True
            # Handle a glued form like "/iC:\path\app.msi"
            stripped = token[2:] if len(token) > 2 and token[1] in 'iIxXaApP' else ''
            if stripped.lower().endswith(('.msi', '.msp')):
                parsed.msi_file = stripped
            continue

        if token.startswith(('/', '-')):
            parsed.switches.append(token)

    return parsed


def build_uninstall_command(product_code: str, switches: Optional[List[str]] = None,
                            installer_filename: Optional[str] = None) -> str:
    """Build an MSI uninstall command.

    Prefers ``msiexec /x {ProductCode}`` (the robust, location-independent form);
    falls back to uninstalling by the installer filename when no product code is
    available.
    """
    quiet = switches if switches else ['/qn', '/norestart']
    target = product_code or installer_filename
    if not target:
        return 'cmd /c exit 0'
    return 'msiexec /x ' + target + (' ' + ' '.join(quiet) if quiet else '')


def build_product_code_detection_rule(product_code: str, product_version: str = '') -> dict:
    """Build an Intune Win32 MSI product-code detection rule (Graph v1.0)."""
    rule = {
        '@odata.type': '#microsoft.graph.win32LobAppProductCodeRule',
        'ruleType': 'detection',
        'productCode': product_code,
        'productVersionOperator': 'notConfigured',
        'productVersion': None,
    }
    if product_version:
        rule['productVersionOperator'] = 'greaterThanOrEqual'
        rule['productVersion'] = product_version
    return rule


def resolve_local_path(source: str) -> Optional[Path]:
    """Return a local filesystem path for ``source`` if it is local, else None.

    Handles plain paths and ``file://`` URIs; returns None for http(s) URLs.
    """
    if not source:
        return None
    parsed = urlparse(source)
    if parsed.scheme in ('http', 'https'):
        return None
    if parsed.scheme == 'file':
        return Path(url2pathname(parsed.path))
    return Path(source)


def inspect_msi(msi_path, install_command: Optional[str] = None) -> dict:
    """High-level convenience: read MSI metadata and derive Intune fields.

    Returns a dict with the raw metadata plus suggested install/uninstall
    commands and a detection rule, ready to drop into a packaging job.
    """
    metadata = read_msi_metadata(msi_path)
    installer_name = Path(msi_path).name

    parsed = parse_install_command(install_command) if install_command else None
    if parsed and parsed.action == 'install':
        suggested_install = parsed.rebuild(installer_name)
        uninstall_switches = parsed.switches or None
    else:
        suggested_install = f'msiexec /i {installer_name} /qn /norestart'
        uninstall_switches = None

    return {
        'metadata': metadata.to_dict(),
        'installer_filename': installer_name,
        'install_command': suggested_install,
        'uninstall_command': build_uninstall_command(
            metadata.product_code, uninstall_switches, installer_name
        ),
        'detection_rule': build_product_code_detection_rule(
            metadata.product_code, metadata.product_version
        ),
    }
