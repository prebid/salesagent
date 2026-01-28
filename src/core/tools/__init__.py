"""
Raw AdCP tool functions without FastMCP decorators.

This module re-exports raw wrapper functions from individual tool modules.
Each raw function is defined in its respective tool module and simply calls
the shared _impl() function.

This eliminates the monolithic __init__.py pattern and keeps code organized
by tool domain.
"""

# Re-export raw functions from tool modules
from src.core.tools.capabilities import get_adcp_capabilities_raw
from src.core.tools.creative_formats import list_creative_formats_raw
from src.core.tools.creatives import list_creatives_raw, sync_creatives_raw
from src.core.tools.media_buy_create import create_media_buy_raw
from src.core.tools.media_buy_delivery import get_media_buy_delivery_raw
from src.core.tools.media_buy_update import update_media_buy_raw
from src.core.tools.performance import update_performance_index_raw
from src.core.tools.products import get_products_raw
from src.core.tools.properties import list_authorized_properties_raw

# Signals tools removed - should come from dedicated signals agents, not sales agent

__all__ = [
    "get_adcp_capabilities_raw",
    "get_products_raw",
    "create_media_buy_raw",
    "sync_creatives_raw",
    "list_creatives_raw",
    "list_creative_formats_raw",
    "list_authorized_properties_raw",
    "update_media_buy_raw",
    "get_media_buy_delivery_raw",
    "update_performance_index_raw",
]
