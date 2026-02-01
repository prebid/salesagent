# from .xandr import XandrAdapter  # Temporarily disabled - needs schema updates
from .base import AdapterCapabilities as AdapterCapabilities
from .base import AdServerAdapter as AdServerAdapter
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
    "creative_engine": CreativeEngineAdapter,
    # 'xandr': XandrAdapter,
    # 'microsoft_monetize': XandrAdapter
}

# Note: Adapter schemas are registered lazily in src/adapters/schemas/__init__.py
# to avoid circular imports. Call get_adapter_schemas() to access them.


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
