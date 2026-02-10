"""Broadstreet Ads Adapter.

Full-featured adapter for Broadstreet Ads supporting:
- CPM and FLAT_RATE pricing
- HTML, static image, and text ad formats
- Template-based formats (3D Cube, YouTube, Gallery, etc.)
- HITL workflows
- Inventory sync
"""

from .adapter import BroadstreetAdapter
from .client import BroadstreetAPIError, BroadstreetClient
from .config_schema import (
    BROADSTREET_TEMPLATES,
    BroadstreetImplementationConfig,
    CreativeSize,
    ZoneTargeting,
    get_template_info,
    parse_implementation_config,
    validate_template_assets,
)
from .schemas import BroadstreetConnectionConfig, BroadstreetProductConfig

__all__ = [
    "BROADSTREET_TEMPLATES",
    "BroadstreetAdapter",
    "BroadstreetAPIError",
    "BroadstreetClient",
    "BroadstreetConnectionConfig",
    "BroadstreetImplementationConfig",
    "BroadstreetProductConfig",
    "CreativeSize",
    "ZoneTargeting",
    "get_template_info",
    "parse_implementation_config",
    "validate_template_assets",
]
