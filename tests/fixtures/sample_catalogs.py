"""Sample OEM catalog data for testing"""

from pathlib import Path

# Path to fixtures directory
FIXTURES_DIR = Path(__file__).parent

# Dell catalog sample
DELL_CATALOG_PATH = FIXTURES_DIR / "dell_catalog_sample.xml"
with open(DELL_CATALOG_PATH, "r", encoding="utf-8") as f:
    DELL_CATALOG = f.read()

# HP catalog sample
HP_CATALOG_PATH = FIXTURES_DIR / "hp_catalog_sample.xml"
with open(HP_CATALOG_PATH, "r", encoding="utf-8") as f:
    HP_CATALOG = f.read()

# Lenovo catalog sample
LENOVO_CATALOG_PATH = FIXTURES_DIR / "lenovo_catalog_sample.xml"
with open(LENOVO_CATALOG_PATH, "r", encoding="utf-8") as f:
    LENOVO_CATALOG = f.read()


# Sample catalog metadata for easy reference
DELL_MODELS = [
    "Latitude 5420",
    "Latitude 7420",
    "OptiPlex 7090",
    "Precision 5570",
]

HP_MODELS = [
    "HP EliteBook 840 G8",
    "HP EliteBook 850 G8",
    "HP ProBook 450 G9",
    "HP ZBook Firefly 14 G9",
    "HP EliteDesk 800 G8",
]

LENOVO_MODELS = [
    "ThinkPad X1 Carbon Gen 9",
    "ThinkPad T14 Gen 2",
    "ThinkCentre M90a Gen 3",
    "ThinkStation P360 Tiny",
]


def get_catalog_for_vendor(vendor: str) -> str:
    """Get catalog XML content for the specified vendor

    Args:
        vendor: Vendor name (dell, hp, or lenovo - case insensitive)

    Returns:
        XML catalog content as string

    Raises:
        ValueError: If vendor is not supported
    """
    vendor_lower = vendor.lower()

    if vendor_lower == "dell":
        return DELL_CATALOG
    elif vendor_lower == "hp":
        return HP_CATALOG
    elif vendor_lower == "lenovo":
        return LENOVO_CATALOG
    else:
        raise ValueError(f"Unsupported vendor: {vendor}")


def get_models_for_vendor(vendor: str) -> list:
    """Get list of model names in sample catalog for the specified vendor

    Args:
        vendor: Vendor name (dell, hp, or lenovo - case insensitive)

    Returns:
        List of model name strings

    Raises:
        ValueError: If vendor is not supported
    """
    vendor_lower = vendor.lower()

    if vendor_lower == "dell":
        return DELL_MODELS
    elif vendor_lower == "hp":
        return HP_MODELS
    elif vendor_lower == "lenovo":
        return LENOVO_MODELS
    else:
        raise ValueError(f"Unsupported vendor: {vendor}")
