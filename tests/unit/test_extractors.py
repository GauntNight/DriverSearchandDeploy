"""Unit tests for autopackager.utils.extractors.

Two extraction strategies share a function:
  * wrapped_zip -- uniform: zipfile.extractall
  * wrapped_msi -- vendor-specific subprocess command

Both must end with an inner MSI located via the catalog entry's
extracted_msi_pattern (defaults to '*.msi'); the largest match wins so
that vendor bundles shipping a small accessory MSI alongside the main
product don't accidentally route to the wrong target.
"""

from __future__ import annotations

import io
import os
import platform
import struct
import sys
import tempfile
import zipfile
from pathlib import Path

import pytest

from autopackager.utils.extractors import ExtractionError, extract_wrapped
from autopackager.utils.installer_catalog import CatalogEntry


def _write_fake_msi(path: Path, size_kb: int = 1) -> None:
    """Write a file with the MSI OLE2 magic. Real MSIs are valid OLE2
    compound documents, but ``extract_wrapped`` only checks the filename
    pattern -- the bytes content doesn't matter at extraction time."""
    path.write_bytes(b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1' + os.urandom(size_kb * 1024))


def _make_zip(zip_path: Path, files: dict) -> None:
    """Build a ZIP file. ``files`` maps in-zip filename to bytes."""
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)


class TestExtractWrappedZip:
    def test_extracts_inner_msi(self, tmp_path):
        zip_path = tmp_path / 'foxit-bundle.zip'
        msi_bytes = b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1' + b'\x00' * 512
        _make_zip(zip_path, {'FoxitPDFReader.msi': msi_bytes, 'README.txt': b'hi'})

        entry = CatalogEntry(
            id='foxit-pdf-reader', type='exe', installer_family='wrapped_zip',
            install_command_template='msiexec /i {installer_filename} /qn',
        )
        out_dir = tmp_path / 'out'
        result = extract_wrapped(zip_path, entry, out_dir)
        assert result.exists()
        assert result.name == 'FoxitPDFReader.msi'
        assert result.read_bytes() == msi_bytes

    def test_picks_largest_when_multiple_msis(self, tmp_path):
        """ZIPs that bundle a tiny installer-helper MSI alongside the main
        product MSI (Foxit historically shipped a 'FoxitPhantomPDFReg.msi'
        registration stub in the same archive) should route to the larger
        of the two."""
        zip_path = tmp_path / 'bundle.zip'
        big_msi = b'\xd0\xcf\x11\xe0' + os.urandom(10_000)
        small_msi = b'\xd0\xcf\x11\xe0' + b'x' * 100
        _make_zip(zip_path, {
            'FoxitPDFReader_main.msi': big_msi,
            'helper-stub.msi': small_msi,
        })
        entry = CatalogEntry(
            id='multi', type='exe', installer_family='wrapped_zip',
            install_command_template='msiexec /i {installer_filename} /qn',
        )
        result = extract_wrapped(zip_path, entry, tmp_path / 'out')
        assert result.name == 'FoxitPDFReader_main.msi'

    def test_respects_pattern_when_set(self, tmp_path):
        zip_path = tmp_path / 'bundle.zip'
        _make_zip(zip_path, {
            'Reader-en_US.msi': b'\xd0\xcf\x11\xe0' + b'a' * 5000,
            'Reader-de_DE.msi': b'\xd0\xcf\x11\xe0' + b'b' * 5000,
            'Reader-fr_FR.msi': b'\xd0\xcf\x11\xe0' + b'c' * 5000,
        })
        entry = CatalogEntry(
            id='locale', type='exe', installer_family='wrapped_zip',
            install_command_template='msiexec /i {installer_filename} /qn',
            extracted_msi_pattern='Reader-en_US.msi',
        )
        result = extract_wrapped(zip_path, entry, tmp_path / 'out')
        assert result.name == 'Reader-en_US.msi'

    def test_raises_when_no_msi_in_zip(self, tmp_path):
        zip_path = tmp_path / 'bundle.zip'
        _make_zip(zip_path, {'README.txt': b'no installer here', 'icon.png': b'\x89PNG'})
        entry = CatalogEntry(
            id='no-msi', type='exe', installer_family='wrapped_zip',
            install_command_template='msiexec /i {installer_filename} /qn',
        )
        with pytest.raises(ExtractionError, match='No MSI matching'):
            extract_wrapped(zip_path, entry, tmp_path / 'out')

    def test_error_lists_extracted_files_for_diagnosis(self, tmp_path):
        """When extraction succeeds but no MSI matches the pattern, the
        error must list what WAS extracted so the operator can fix
        extracted_msi_pattern without rebuilding the bundle locally.
        """
        zip_path = tmp_path / 'bundle.zip'
        _make_zip(zip_path, {
            'subdir/install.msi': b'\xd0\xcf\x11\xe0' + b'\x00' * 100,
        })
        entry = CatalogEntry(
            id='wrong-pattern', type='exe', installer_family='wrapped_zip',
            install_command_template='msiexec /i {installer_filename} /qn',
            extracted_msi_pattern='nope-*.msi',
        )
        with pytest.raises(ExtractionError) as ei:
            extract_wrapped(zip_path, entry, tmp_path / 'out')
        # Diagnostic listing should reference the actual extracted MSI
        assert 'install.msi' in str(ei.value)

    def test_raises_on_corrupt_zip(self, tmp_path):
        bad = tmp_path / 'broken.zip'
        bad.write_bytes(b'not really a zip')
        entry = CatalogEntry(
            id='bad', type='exe', installer_family='wrapped_zip',
            install_command_template='msiexec /i {installer_filename} /qn',
        )
        with pytest.raises(ExtractionError, match='Not a valid ZIP'):
            extract_wrapped(bad, entry, tmp_path / 'out')


class TestExtractWrappedMsi:
    def test_requires_extract_command_template(self, tmp_path):
        fake_exe = tmp_path / 'installer.exe'
        fake_exe.write_bytes(b'MZ' + b'\x00' * 100)
        entry = CatalogEntry(
            id='no-cmd', type='exe', installer_family='wrapped_msi',
            install_command_template='msiexec /i {installer_filename} /qn',
            # extract_command_template deliberately omitted
        )
        with pytest.raises(ExtractionError, match='requires extract_command_template'):
            extract_wrapped(fake_exe, entry, tmp_path / 'out')

    def test_runs_command_with_extract_dir_as_cwd(self, tmp_path):
        """The PowerToys pattern (--extract_msi drops the MSI into the
        current directory) requires cwd=extract_dir. The Adobe pattern
        (-sfx_o "<dest>") doesn't care, but using cwd=extract_dir is the
        correct default. Use a Python one-liner that writes an MSI to
        cwd so we can verify cwd handling cross-platform.
        """
        fake_exe = tmp_path / 'installer.exe'
        fake_exe.write_bytes(b'MZ')

        # Build a portable extract command that writes a fake MSI into the
        # current working directory -- exercising the cwd contract without
        # depending on a real vendor extractor.
        python = sys.executable.replace('\\', '/')
        script = (
            "import sys; "
            "open('extracted.msi','wb').write(b'\\xd0\\xcf\\x11\\xe0' + b'x'*1000)"
        )
        entry = CatalogEntry(
            id='cwd-test', type='exe', installer_family='wrapped_msi',
            install_command_template='msiexec /i {installer_filename} /qn',
            extract_command_template=f'"{python}" -c "{script}"',
        )
        out_dir = tmp_path / 'out'
        result = extract_wrapped(fake_exe, entry, out_dir)
        assert result.exists()
        assert result.parent == out_dir.resolve()
        assert result.name == 'extracted.msi'

    def test_subprocess_failure_raises_extraction_error(self, tmp_path):
        fake_exe = tmp_path / 'installer.exe'
        fake_exe.write_bytes(b'MZ')
        entry = CatalogEntry(
            id='will-fail', type='exe', installer_family='wrapped_msi',
            install_command_template='msiexec /i {installer_filename} /qn',
            extract_command_template='exit 7',  # both cmd.exe and sh
        )
        with pytest.raises(ExtractionError, match='exit 7'):
            extract_wrapped(fake_exe, entry, tmp_path / 'out')


class TestExtractWrappedFamilyValidation:
    @pytest.mark.parametrize('family', ['msi', 'inno_setup', 'nsis', 'custom', None])
    def test_rejects_non_wrapped_family(self, tmp_path, family):
        installer = tmp_path / 'fake.bin'
        installer.write_bytes(b'noop')
        entry = CatalogEntry(
            id='wrong-family', type='exe', installer_family=family,
            install_command_template='whatever',
        )
        with pytest.raises(ExtractionError, match='not a wrapped type'):
            extract_wrapped(installer, entry, tmp_path / 'out')
