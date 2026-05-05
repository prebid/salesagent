"""Helper functions for working with generated schemas.

This module provides convenience functions for constructing complex generated schemas
without losing type safety. Unlike adapters (which wrap schemas in dict[str, Any]),
these helpers work directly with the generated Pydantic models.

Philosophy:
- Generated schemas are the source of truth (always in sync with AdCP spec)
- Helpers make construction easier without sacrificing type safety
- Custom logic (validators, conversions) lives here, not in wrapper classes
"""

from typing import Any

from adcp import GetProductsResponse, Product
from adcp.types import PropertyListReference
from adcp.types.generated_poc.core.brand_ref import BrandReference
from adcp.types.generated_poc.core.context import ContextObject
from adcp.types.generated_poc.core.product_filters import ProductFilters
from adcp.types.generated_poc.core.reporting_webhook import ReportingWebhook

from src.core.schemas.product import GetProductsRequest


def to_context_object(context: dict[str, Any] | ContextObject | None) -> ContextObject | None:
    """Convert dict context to ContextObject for adcp 2.12.0+ compatibility.

    Args:
        context: Context as dict or ContextObject or None

    Returns:
        ContextObject or None
    """
    if context is None:
        return None
    if isinstance(context, ContextObject):
        return context
    if isinstance(context, dict):
        return ContextObject(**context)
    return None  # Fallback for unexpected types


def to_reporting_webhook(webhook: dict[str, Any] | ReportingWebhook | None) -> ReportingWebhook | None:
    """Convert dict to ReportingWebhook for adcp type compatibility.

    Args:
        webhook: Webhook config as dict or ReportingWebhook or None

    Returns:
        ReportingWebhook or None
    """
    if webhook is None:
        return None
    if isinstance(webhook, ReportingWebhook):
        return webhook
    if isinstance(webhook, dict):
        return ReportingWebhook(**webhook)
    return None  # Fallback for unexpected types


def to_brand_reference(brand: dict[str, Any] | BrandReference | None) -> BrandReference | None:
    """Convert dict brand to BrandReference for adcp 3.6.0 compatibility.

    Args:
        brand: Brand as dict or BrandReference or None

    Returns:
        BrandReference or None
    """
    if brand is None:
        return None
    if isinstance(brand, BrandReference):
        return brand
    if isinstance(brand, dict):
        return BrandReference(**brand)
    return None  # Fallback for unexpected types


def to_property_list_reference(
    property_list: dict[str, Any] | PropertyListReference | None,
) -> PropertyListReference | None:
    """Convert dict to PropertyListReference for adcp compatibility.

    Args:
        property_list: Property list reference as dict or PropertyListReference or None

    Returns:
        PropertyListReference or None
    """
    if property_list is None:
        return None
    if isinstance(property_list, PropertyListReference):
        return property_list
    if isinstance(property_list, dict):
        return PropertyListReference(**property_list)
    return None  # Fallback for unexpected types


def create_get_products_request(
    brief: str = "",
    brand: dict[str, Any] | BrandReference | None = None,
    filters: dict[str, Any] | ProductFilters | None = None,
    property_list: dict[str, Any] | PropertyListReference | None = None,
    context: dict[str, Any] | ContextObject | None = None,
    buying_mode: str | None = None,
    refine: list[dict[str, Any]] | None = None,
) -> GetProductsRequest:
    """Create GetProductsRequest aligned with the AdCP 3.0 three-mode contract.

    Args:
        brief: Natural language description of campaign requirements (required when
            buying_mode='brief'; forbidden when buying_mode='wholesale' or 'refine')
        brand: Brand reference per adcp 3.6.0 (BrandReference or dict with domain field).
               Example: BrandReference(domain="acme.com") or {"domain": "acme.com"}
        filters: Structured filters for product discovery (dict or ProductFilters)
        property_list: Property list reference for filtering by buyer's property list
        context: Application-level context (dict or ContextObject)
        buying_mode: Buyer intent — 'brief' / 'wholesale' / 'refine'. Required for v3
            clients (the transport wrapper is responsible for defaulting pre-v3 clients
            to 'brief' before calling this helper).
        refine: Array of change requests for refining a previous get_products response;
            only valid when buying_mode='refine'.

    Returns:
        GetProductsRequest

    Examples:
        >>> req = create_get_products_request(
        ...     buying_mode="brief",
        ...     brand=BrandReference(domain="acme.com"),
        ...     brief="Display ads"
        ... )
    """
    # Handle filters - can be dict, ProductFilters, or None
    filters_obj: ProductFilters | None = None
    if filters is not None:
        if isinstance(filters, ProductFilters):
            filters_obj = filters
        elif isinstance(filters, dict):
            filters_obj = ProductFilters(**filters)

    return GetProductsRequest(
        brand=to_brand_reference(brand),
        brief=brief or None,
        filters=filters_obj,
        property_list=to_property_list_reference(property_list),
        context=to_context_object(context),
        buying_mode=buying_mode,
        refine=refine,
    )


# Re-export commonly used generated types for convenience
__all__ = [
    "to_brand_reference",
    "to_context_object",
    "to_reporting_webhook",
    "create_get_products_request",
    # Re-export types for type hints
    "BrandReference",
    "GetProductsRequest",
    "GetProductsResponse",
    "Product",
    "ContextObject",
    "ReportingWebhook",
]
