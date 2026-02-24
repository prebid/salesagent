"""Centralized version compatibility transform registry.

Provides a single function `apply_version_compat(tool_name, response_dict, adcp_version)`
that transports call at their boundary after serialization. Transforms are registered
per-tool and only applied when clients declare adcp_version < 3.0.
"""

from collections.abc import Callable
from typing import Any

from src.core.product_conversion import add_v2_compat_to_products, needs_v2_compat

# Registry: tool_name → transform function(response_dict) -> response_dict
_TRANSFORMS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {}


def _get_products_v2_compat(response_dict: dict[str, Any]) -> dict[str, Any]:
    """Apply v2 compat fields to get_products response."""
    if "products" in response_dict:
        response_dict["products"] = add_v2_compat_to_products(response_dict["products"])
    return response_dict


# Register transforms
_TRANSFORMS["get_products"] = _get_products_v2_compat


def apply_version_compat(
    tool_name: str,
    response_dict: dict[str, Any],
    adcp_version: str | None,
) -> dict[str, Any]:
    """Apply registered version compat transforms for a tool.

    Called at the transport boundary (MCP, A2A, REST) after serialization.
    Skips transforms entirely for V3+ clients.

    Args:
        tool_name: Name of the tool (e.g., "get_products")
        response_dict: Serialized response dictionary
        adcp_version: Client's declared AdCP version (None → applies compat)

    Returns:
        Response dict with compat fields added (or unchanged for V3+)
    """
    if not needs_v2_compat(adcp_version):
        return response_dict
    transform = _TRANSFORMS.get(tool_name)
    if transform:
        return transform(response_dict)
    return response_dict
