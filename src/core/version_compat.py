"""Centralized version compatibility transform registry.

Provides `apply_version_compat(tool_name, response, adcp_version)` that
transports call at their boundary. When the response has a model with products,
v2 compat fields are derived from model attributes (not post-hoc dict mutation).
Transforms are registered per-tool and only applied for pre-3.0 clients.
"""

from typing import Any

from src.core.product_conversion import dump_products_v2_compat, needs_v2_compat


def apply_version_compat(
    tool_name: str,
    response: Any,
    adcp_version: str | None,
) -> dict[str, Any]:
    """Apply registered version compat transforms for a tool.

    Called at the transport boundary (MCP, A2A, REST). For V3+ clients,
    serializes with standard model_dump(). For pre-3.0 clients, pricing
    options are serialized with v2 compat fields derived from models.

    The response can be:
    - A Pydantic model with .products attribute (preferred — enables model-level v2 compat)
    - A pre-serialized dict (legacy path — v2 compat skipped since models are unavailable)

    Args:
        tool_name: Name of the tool (e.g., "get_products")
        response: Response model or pre-serialized dict
        adcp_version: Client's declared AdCP version (None -> applies compat)

    Returns:
        Serialized response dict, with v2 compat fields added for pre-3.0 clients
    """
    # If response is already a dict, serialize it as-is (no model available for v2 compat)
    if isinstance(response, dict):
        return response

    # V3+ clients: standard serialization, no compat needed
    if not needs_v2_compat(adcp_version):
        return response.model_dump(mode="json")

    # Pre-3.0 clients: apply model-level v2 compat transforms
    if tool_name == "get_products" and hasattr(response, "products"):
        response_dict = response.model_dump(mode="json")
        # Replace pricing_options with v2-compat serialization from models
        if response.products:
            v2_products = dump_products_v2_compat(response.products)
            response_dict["products"] = v2_products
        return response_dict

    # Unknown tool or no transform: standard serialization
    return response.model_dump(mode="json")
