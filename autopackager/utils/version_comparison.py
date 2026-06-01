"""Version Comparison Utility

Provides semantic version comparison for OEM-specific driver version formats.
Supports Dell A-series, HP SP-prefixed, and Lenovo multi-segment versions.
"""

import re
from typing import Optional, Tuple, List
from dataclasses import dataclass, field


@dataclass
class SemanticVersion:
    """Normalized version representation for comparison"""

    major: int = 0
    minor: int = 0
    patch: int = 0
    build: int = 0
    segments: List[int] = field(default_factory=list)
    prerelease: Optional[str] = None
    metadata: Optional[str] = None
    original: str = ""

    def __post_init__(self):
        """Ensure segments list is populated from major.minor.patch.build"""
        if not self.segments:
            self.segments = [self.major, self.minor, self.patch, self.build]

    def compare(self, other: 'SemanticVersion') -> int:
        """Compare two semantic versions

        Returns:
            -1 if self < other
            0 if self == other
            1 if self > other
        """
        # Compare segments first (handles multi-segment versions)
        max_segments = max(len(self.segments), len(other.segments))

        for i in range(max_segments):
            self_val = self.segments[i] if i < len(self.segments) else 0
            other_val = other.segments[i] if i < len(other.segments) else 0

            if self_val < other_val:
                return -1
            elif self_val > other_val:
                return 1

        # If all segments are equal, check prerelease
        # Version with prerelease is less than version without
        if self.prerelease and not other.prerelease:
            return -1
        elif not self.prerelease and other.prerelease:
            return 1
        elif self.prerelease and other.prerelease:
            # Compare prerelease strings lexicographically
            if self.prerelease < other.prerelease:
                return -1
            elif self.prerelease > other.prerelease:
                return 1

        return 0

    def __lt__(self, other):
        return self.compare(other) < 0

    def __le__(self, other):
        return self.compare(other) <= 0

    def __gt__(self, other):
        return self.compare(other) > 0

    def __ge__(self, other):
        return self.compare(other) >= 0

    def __eq__(self, other):
        return self.compare(other) == 0

    def __ne__(self, other):
        return self.compare(other) != 0


class BaseVersionParser:
    """Base class for vendor-specific version parsers"""

    def parse(self, version_string: str) -> Optional[SemanticVersion]:
        """Parse a version string into a SemanticVersion

        Args:
            version_string: Version string to parse

        Returns:
            SemanticVersion object or None if parsing fails
        """
        raise NotImplementedError


class DellVersionParser(BaseVersionParser):
    """Parser for Dell-specific version formats

    Supports:
    - A-series BIOS versions: A00, A01, A14, etc.
    - Standard semantic versions: 1.15.0, 2.0.1, etc.
    """

    A_SERIES_PATTERN = re.compile(r'^A(\d+)$', re.IGNORECASE)
    SEMANTIC_PATTERN = re.compile(
        r'^(\d+)\.(\d+)(?:\.(\d+))?(?:\.(\d+))?'
        r'(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?'
        r'(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$'
    )

    def parse(self, version_string: str) -> Optional[SemanticVersion]:
        """Parse Dell version string"""
        if not version_string:
            return None

        version_string = version_string.strip()

        # Try A-series pattern first
        match = self.A_SERIES_PATTERN.match(version_string)
        if match:
            # A-series: treat the number as the major version
            version_num = int(match.group(1))
            return SemanticVersion(
                major=version_num,
                minor=0,
                patch=0,
                build=0,
                segments=[version_num, 0, 0, 0],
                original=version_string
            )

        # Try standard semantic version
        match = self.SEMANTIC_PATTERN.match(version_string)
        if match:
            major = int(match.group(1))
            minor = int(match.group(2))
            patch = int(match.group(3)) if match.group(3) else 0
            build = int(match.group(4)) if match.group(4) else 0
            prerelease = match.group(5)
            metadata = match.group(6)

            return SemanticVersion(
                major=major,
                minor=minor,
                patch=patch,
                build=build,
                segments=[major, minor, patch, build],
                prerelease=prerelease,
                metadata=metadata,
                original=version_string
            )

        return None


class HPVersionParser(BaseVersionParser):
    """Parser for HP-specific version formats

    Supports:
    - SP-prefixed versions: SP142355, SP100000, etc.
    - Standard semantic versions: 1.2.3, 2.0.1, etc.
    """

    SP_PATTERN = re.compile(r'^SP(\d+)$', re.IGNORECASE)
    SEMANTIC_PATTERN = re.compile(
        r'^(\d+)\.(\d+)(?:\.(\d+))?(?:\.(\d+))?'
        r'(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?'
        r'(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$'
    )

    def parse(self, version_string: str) -> Optional[SemanticVersion]:
        """Parse HP version string"""
        if not version_string:
            return None

        version_string = version_string.strip()

        # Try SP-prefixed pattern first
        match = self.SP_PATTERN.match(version_string)
        if match:
            # SP versions: treat the number as the major version
            sp_num = int(match.group(1))
            return SemanticVersion(
                major=sp_num,
                minor=0,
                patch=0,
                build=0,
                segments=[sp_num, 0, 0, 0],
                original=version_string
            )

        # Try standard semantic version
        match = self.SEMANTIC_PATTERN.match(version_string)
        if match:
            major = int(match.group(1))
            minor = int(match.group(2))
            patch = int(match.group(3)) if match.group(3) else 0
            build = int(match.group(4)) if match.group(4) else 0
            prerelease = match.group(5)
            metadata = match.group(6)

            return SemanticVersion(
                major=major,
                minor=minor,
                patch=patch,
                build=build,
                segments=[major, minor, patch, build],
                prerelease=prerelease,
                metadata=metadata,
                original=version_string
            )

        return None


class LenovoVersionParser(BaseVersionParser):
    """Parser for Lenovo-specific version formats

    Supports:
    - Multi-segment versions: 1.82.0.24, 10.1.18838.8283, etc.
    - Handles any number of dot-separated numeric segments
    """

    MULTI_SEGMENT_PATTERN = re.compile(
        r'^(\d+(?:\.\d+)+)'
        r'(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?'
        r'(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$'
    )

    def parse(self, version_string: str) -> Optional[SemanticVersion]:
        """Parse Lenovo version string"""
        if not version_string:
            return None

        version_string = version_string.strip()

        match = self.MULTI_SEGMENT_PATTERN.match(version_string)
        if match:
            # Split version into segments
            version_parts = match.group(1).split('.')
            segments = [int(part) for part in version_parts]

            # Pad to at least 4 segments for consistency
            while len(segments) < 4:
                segments.append(0)

            prerelease = match.group(2)
            metadata = match.group(3)

            return SemanticVersion(
                major=segments[0] if len(segments) > 0 else 0,
                minor=segments[1] if len(segments) > 1 else 0,
                patch=segments[2] if len(segments) > 2 else 0,
                build=segments[3] if len(segments) > 3 else 0,
                segments=segments,
                prerelease=prerelease,
                metadata=metadata,
                original=version_string
            )

        return None


class VersionComparator:
    """Main entry point for version comparison

    Provides vendor-aware version comparison with support for
    Dell, HP, and Lenovo version formats.
    """

    def __init__(self):
        """Initialize version comparator with vendor-specific parsers"""
        self.parsers = {
            'dell': DellVersionParser(),
            'hp': HPVersionParser(),
            'lenovo': LenovoVersionParser()
        }
        # Default parser for unknown vendors
        self.default_parser = LenovoVersionParser()  # Most flexible

    def _get_parser(self, vendor: Optional[str]) -> BaseVersionParser:
        """Get appropriate parser for vendor

        Args:
            vendor: Vendor name (case-insensitive)

        Returns:
            Appropriate version parser for the vendor
        """
        if not vendor:
            return self.default_parser

        vendor_lower = vendor.lower()
        return self.parsers.get(vendor_lower, self.default_parser)

    def parse(self, version_string: str, vendor: Optional[str] = None) -> Optional[SemanticVersion]:
        """Parse a version string using vendor-specific parser

        Args:
            version_string: Version string to parse
            vendor: Vendor name (dell, hp, lenovo)

        Returns:
            SemanticVersion object or None if parsing fails
        """
        parser = self._get_parser(vendor)
        return parser.parse(version_string)

    def compare(self, v1: str, v2: str, vendor: Optional[str] = None) -> int:
        """Compare two version strings

        Args:
            v1: First version string
            v2: Second version string
            vendor: Vendor name for context-aware parsing

        Returns:
            -1 if v1 < v2
            0 if v1 == v2
            1 if v1 > v2

        Raises:
            ValueError: If either version string cannot be parsed
        """
        sem_v1 = self.parse(v1, vendor)
        sem_v2 = self.parse(v2, vendor)

        if sem_v1 is None:
            raise ValueError(f"Cannot parse version string: {v1}")
        if sem_v2 is None:
            raise ValueError(f"Cannot parse version string: {v2}")

        return sem_v1.compare(sem_v2)

    def is_newer(self, current: Optional[str], latest: str, vendor: Optional[str] = None) -> bool:
        """Determine if latest version is newer than current version

        Args:
            current: Current version string (can be None or empty string)
            latest: Latest version string to compare
            vendor: Vendor name for context-aware parsing

        Returns:
            True if latest is newer than current, False otherwise
            Returns True if current is None or empty string (no current version)
        """
        # No current version means any latest version is "newer"
        if current is None or current == '':
            return True

        try:
            result = self.compare(current, latest, vendor)
            return result < 0  # current < latest means latest is newer
        except ValueError:
            # If we can't parse, default to False (don't update)
            return False

    def compare_versions(self, current: Optional[str], latest: str, vendor: Optional[str] = None) -> bool:
        """Alias for is_newer for backward compatibility

        Args:
            current: Current version string (can be None)
            latest: Latest version string to compare
            vendor: Vendor name for context-aware parsing

        Returns:
            True if versions are different (latest is newer), False otherwise
        """
        return self.is_newer(current, latest, vendor)


# ---------------------------------------------------------------------------
# Catalog supersedence: PEP 440 with vendor-format normalisation
# ---------------------------------------------------------------------------

def _normalise_for_pep440(v: str) -> str:
    """Massage vendor-shaped version strings into something PEP 440 accepts.

    PEP 440 rejects formats common in the wild:
      * Java's ``1.8.0_341`` -- underscore-then-digits is not PEP 440. We map
        ``_`` to ``.`` so it parses as ``1.8.0.341``. Ordering is preserved
        because the digits after ``_`` are monotonically increasing builds.
      * Some vendor builds use ``-`` as a build separator (``17.0.13-7``)
        which PEP 440 treats as a pre-release suffix; we replace it with
        ``.`` so the build number sorts naturally.

    Leaves SemVer-y / Adobe-style / Mozilla-style versions untouched
    (``3.0.23``, ``26.001.21563``, ``17.0.14`` etc. parse directly).
    """
    return (v or "").strip().replace("_", ".").replace("-", ".")


def compare_catalog_versions(a: str, b: str) -> int:
    """Compare two version strings used in catalog supersedence chains.

    Returns -1 if a < b, 0 if a == b, +1 if a > b.

    Uses ``packaging.version.Version`` (PEP 440) after _normalise_for_pep440
    transforms vendor-shaped versions into a PEP 440-acceptable form. Falls
    back to natural-sort tuple comparison on parse failure -- never raises,
    so a malformed version doesn't take down the supersedence logic.
    """
    from packaging.version import Version, InvalidVersion

    na, nb = _normalise_for_pep440(a), _normalise_for_pep440(b)
    try:
        va, vb = Version(na), Version(nb)
    except InvalidVersion:
        return _natural_sort_compare(na, nb)
    if va < vb:
        return -1
    if va > vb:
        return 1
    return 0


def _natural_sort_compare(a: str, b: str) -> int:
    """Last-resort comparator: split on non-alphanumerics and compare
    segments as ints when both sides are numeric, lexicographically otherwise.

    Handles things like build IDs that PEP 440 can't parse. Won't be
    semantically correct for every vendor's nonsense format, but it's
    deterministic and reasonable -- which beats raising mid-publish.
    """
    def _segments(s: str):
        return [int(part) if part.isdigit() else part.lower()
                for part in re.split(r'[^A-Za-z0-9]+', s) if part]

    sa, sb = _segments(a), _segments(b)
    for x, y in zip(sa, sb):
        if isinstance(x, int) and isinstance(y, int):
            if x != y:
                return -1 if x < y else 1
        else:
            xs, ys = str(x), str(y)
            if xs != ys:
                return -1 if xs < ys else 1
    if len(sa) != len(sb):
        return -1 if len(sa) < len(sb) else 1
    return 0
