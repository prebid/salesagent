# from .xandr import XandrAdapter  # Temporarily disabled - needs schema updates
from dataclasses import dataclass

from .base import AdapterCapabilities as AdapterCapabilities
from .base import AdServerAdapter as AdServerAdapter
from .base import BaseConnectionConfig as BaseConnectionConfig
from .base import BaseProductConfig as BaseProductConfig
from .base import TargetingCapabilities as TargetingCapabilities
from .broadstreet import BroadstreetAdapter
from .creative_engine import CreativeEngineAdapter
from .google_ad_manager import GoogleAdManager as GAMAdapter
from .kevel import Kevel as KevelAdapter
from .mock_ad_server import MockAdServer as MockAdapter
from .triton_digital import TritonDigital as TritonAdapter

# Map of adapter type strings to adapter classes
ADAPTER_REGISTRY = {
    "gam": GAMAdapter,
    "google_ad_manager": GAMAdapter,
    "broadstreet": BroadstreetAdapter,
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


def get_adapter_default_channels(adapter_type: str) -> list[str]:
    """Get default advertising channels for an adapter type.

    Default channels are defined on each adapter class's default_channels attribute.

    Args:
        adapter_type: Adapter type name (e.g., "google_ad_manager", "mock", "kevel", "triton")

    Returns:
        List of default channel names for the adapter
    """
    adapter_class = ADAPTER_REGISTRY.get(adapter_type)
    if adapter_class and hasattr(adapter_class, "default_channels"):
        return adapter_class.default_channels
    return []


def get_adapter_default_delivery_measurement(adapter_type: str) -> dict[str, str]:
    """Get default delivery_measurement for an adapter type.

    Per AdCP spec, delivery_measurement is REQUIRED on all products.
    This function returns the adapter-specific default when a product
    does not have delivery_measurement configured.

    Args:
        adapter_type: Adapter type name (e.g., "google_ad_manager", "mock")

    Returns:
        Dict with at least "provider" key for the adapter's default measurement.
    """
    adapter_class = ADAPTER_REGISTRY.get(adapter_type)
    if adapter_class and hasattr(adapter_class, "default_delivery_measurement"):
        return adapter_class.default_delivery_measurement
    return {"provider": "publisher"}
