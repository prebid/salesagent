# from .xandr import XandrAdapter  # Temporarily disabled - needs schema updates
from dataclasses import dataclass

from .base import AdapterCapabilities as AdapterCapabilities
from .base import AdServerAdapter as AdServerAdapter
from .base import BaseConnectionConfig as BaseConnectionConfig
from .base import BaseProductConfig as BaseProductConfig
from .base import TargetingCapabilities as TargetingCapabilities
from .creative_engine import CreativeEngineAdapter
from .google_ad_manager import GoogleAdManager as GAMAdapter
from .kevel import Kevel as KevelAdapter
from .mock_ad_server import MockAdServer as MockAdapter
from .triton_digital import TritonDigital as TritonAdapter

# Map of adapter type strings to adapter classes
ADAPTER_REGISTRY = {
    "gam": GAMAdapter,
    "google_ad_manager": GAMAdapter,
    "kevel": KevelAdapter,
    "mock": MockAdapter,
    "triton": TritonAdapter,
    "triton_digital": TritonAdapter,
    "creative_engine": CreativeEngineAdapter,
    # 'xandr': XandrAdapter,
    # 'microsoft_monetize': XandrAdapter
}


@dataclass
class AdapterSchemas:
    """Container for an adapter's schema classes and capabilities."""

    connection_config: type[BaseConnectionConfig] | None
    product_config: type[BaseProductConfig] | None
    capabilities: AdapterCapabilities | None


def get_adapter_schemas(adapter_type: str) -> AdapterSchemas | None:
    """Get schemas for an adapter type.

    Args:
        adapter_type: The adapter type identifier (e.g., "mock", "google_ad_manager")

    Returns:
        AdapterSchemas if adapter exists, None otherwise
    """
    adapter_class = ADAPTER_REGISTRY.get(adapter_type.lower())
    if not adapter_class:
        return None

    # Get schemas from class attributes
    return AdapterSchemas(
        connection_config=getattr(adapter_class, "connection_config_class", None),
        product_config=getattr(adapter_class, "product_config_class", None),
        capabilities=getattr(adapter_class, "capabilities", None),
    )


def get_adapter(adapter_type: str, config: dict, principal):
    """Factory function to get the appropriate adapter instance."""
    adapter_class = ADAPTER_REGISTRY.get(adapter_type.lower())
    if not adapter_class:
        raise ValueError(f"Unknown adapter type: {adapter_type}")
    return adapter_class(config, principal)


def get_adapter_class(adapter_type: str):
    """Get the adapter class for a given adapter type."""
    adapter_class = ADAPTER_REGISTRY.get(adapter_type.lower())
    if not adapter_class:
        raise ValueError(f"Unknown adapter type: {adapter_type}")
    return adapter_class
