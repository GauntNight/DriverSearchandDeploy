"""Configuration Management"""

import os
import re
import yaml
from pathlib import Path
from dotenv import load_dotenv
from string import Template

# Load environment variables
load_dotenv()


def substitute_env_vars(config_dict):
    """Recursively substitute environment variables in config"""
    if isinstance(config_dict, dict):
        return {k: substitute_env_vars(v) for k, v in config_dict.items()}
    elif isinstance(config_dict, list):
        return [substitute_env_vars(item) for item in config_dict]
    elif isinstance(config_dict, str):
        # Replace ${VAR_NAME} with environment variable value
        if '${' in config_dict:
            template = Template(config_dict)
            return template.safe_substitute(os.environ)
        return config_dict
    else:
        return config_dict


def load_config(config_path=None):
    """Load configuration from YAML file with environment variable substitution"""
    if config_path is None:
        # Default to config.yaml in the config directory
        base_dir = Path(__file__).parent.parent
        config_path = base_dir / "config" / "config.yaml"

    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)

    # Substitute environment variables
    config = substitute_env_vars(config)

    return config


def validate_config_fields(config, required_fields):
    """Validate that required config fields exist and are not placeholder/empty values.

    Args:
        config: Dictionary of configuration values to validate.
        required_fields: List of dot-notation key paths (e.g. ['azure.tenant_id', 'app.name']).

    Returns:
        List of error strings for any invalid fields. Empty list means all valid.
    """
    placeholder_patterns = [
        re.compile(r'\$\{'),           # Un-substituted env vars like ${VAR}
        re.compile(r'^your_\w+_here$', re.IGNORECASE),  # Placeholder values
        re.compile(r'^<.+>$'),         # Angle-bracket placeholders like <your-value>
        re.compile(r'^TODO', re.IGNORECASE),  # TODO placeholders
        re.compile(r'^CHANGE_ME$', re.IGNORECASE),
    ]

    errors = []
    for field_path in required_fields:
        keys = field_path.split('.')
        value = config
        found = True
        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                errors.append(f"Missing required config field: {field_path}")
                found = False
                break

        if not found:
            continue

        # Check for empty/None values
        if value is None or (isinstance(value, str) and value.strip() == ''):
            errors.append(f"Config field '{field_path}' is empty")
            continue

        # Check for placeholder patterns in string values
        if isinstance(value, str):
            for pattern in placeholder_patterns:
                if pattern.search(value):
                    errors.append(
                        f"Config field '{field_path}' contains a placeholder value: {value}"
                    )
                    break

    return errors


def get_config():
    """Singleton pattern for configuration"""
    if not hasattr(get_config, '_config'):
        get_config._config = load_config()
    return get_config._config
