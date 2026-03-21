"""VM Provider Abstraction Layer"""

from autopackager.agents.testing.vm_providers.base import VMProvider
from autopackager.agents.testing.vm_providers.hyperv_provider import HyperVProvider

__all__ = ['VMProvider', 'HyperVProvider']
