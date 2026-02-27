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

from adcp import GetProductsRequest, GetProductsResponse, Product
from adcp.types import PropertyListReference
from adcp.types.generated_poc.core.brand_ref import BrandReference
from adcp.types.generated_poc.core.context import ContextObject
from adcp.types.generated_poc.core.product_filters import ProductFilters
from adcp.types.generated_poc.core.reporting_webhook import ReportingWebhook


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
) -> GetProductsRequest:
    """Create GetProductsRequest aligned with adcp v3.6.0 spec.

    Args:
        brief: Natural language description of campaign requirements
        brand: Brand reference per adcp 3.6.0 (BrandReference or dict with domain field).
               Example: BrandReference(domain="acme.com") or {"domain": "acme.com"}
        filters: Structured filters for product discovery (dict or ProductFilters)
        property_list: Property list reference for filtering by buyer's property list
        context: Application-level context (dict or ContextObject)

    Returns:
        GetProductsRequest

    Examples:
        >>> req = create_get_products_request(
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
    )


def create_get_products_response(
    products: list[Product | dict[str, Any]],
    errors: list | None = None,
    request_context: dict[str, Any] | None = None,
) -> GetProductsResponse:
    """Create GetProductsResponse.

    Note: The generated GetProductsResponse is already a simple BaseModel,
    so this helper mainly just provides defaults and type conversion.

    Args:
        products: List of matching products (Product objects or dicts)
        errors: List of errors (if any)

    Returns:
        GetProductsResponse
    """
    # Convert dict products to Product objects
    product_list: list[Product] = []
    for p in products:
        if isinstance(p, dict):
            product_list.append(Product(**p))
        else:
            product_list.append(p)

    return GetProductsResponse(
        products=product_list,
        errors=errors,
        context=to_context_object(request_context),
    )


# Re-export commonly used generated types for convenience
__all__ = [
    "to_brand_reference",
    "to_context_object",
    "to_reporting_webhook",
    "create_get_products_request",
    "create_get_products_response",
    # Re-export types for type hints
    "BrandReference",
    "GetProductsRequest",
    "GetProductsResponse",
    "Product",
    "ContextObject",
    "ReportingWebhook",
]
