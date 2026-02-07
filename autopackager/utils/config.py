"""Configuration Management"""

import os
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
            try:
                return template.substitute(os.environ)
            except KeyError as e:
                raise ValueError(f"Missing environment variable: {e}")
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


def get_config():
    """Singleton pattern for configuration"""
    if not hasattr(get_config, '_config'):
        get_config._config = load_config()
    return get_config._config
