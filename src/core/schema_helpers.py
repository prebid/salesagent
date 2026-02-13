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
from adcp.types.generated_poc.core.brand_manifest import BrandManifest
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


def create_get_products_request(
    brief: str = "",
    brand_manifest: dict[str, Any] | BrandManifest | None = None,
    filters: dict[str, Any] | ProductFilters | None = None,
    context: dict[str, Any] | ContextObject | None = None,
) -> GetProductsRequest:
    """Create GetProductsRequest aligned with adcp v1.2.1 spec.

    Args:
        brief: Natural language description of campaign requirements
        brand_manifest: Brand information as dict or BrandManifest. Must follow AdCP BrandManifest schema.
                       Example: {"name": "Acme", "url": "https://acme.com"}
                       Or: {"url": "https://acme.com"}
        filters: Structured filters for product discovery (dict or ProductFilters)
        context: Application-level context (dict or ContextObject)

    Returns:
        GetProductsRequest

    Examples:
        >>> req = create_get_products_request(
        ...     brand_manifest={"name": "Acme", "url": "https://acme.com"},
        ...     brief="Display ads"
        ... )
    """
    # Handle brand_manifest - can be dict, BrandManifest, or None
    brand_manifest_obj: BrandManifest | None = None
    if brand_manifest is not None:
        if isinstance(brand_manifest, BrandManifest):
            brand_manifest_obj = brand_manifest
        elif isinstance(brand_manifest, dict):
            # Adapt brand_manifest to ensure 'name' field exists (adcp 2.5.0 requirement)
            brand_manifest_adapted = brand_manifest
            if "name" not in brand_manifest:
                # If only 'url' provided, use domain as name
                if "url" in brand_manifest:
                    from urllib.parse import urlparse

                    url_str = brand_manifest["url"]
                    domain = urlparse(url_str).netloc or url_str
                    brand_manifest_adapted = {**brand_manifest, "name": domain}
                else:
                    # Fallback: use a placeholder name
                    brand_manifest_adapted = {**brand_manifest, "name": "Brand"}
            brand_manifest_obj = BrandManifest(**brand_manifest_adapted)

    # Handle filters - can be dict, ProductFilters, or None
    filters_obj: ProductFilters | None = None
    if filters is not None:
        if isinstance(filters, ProductFilters):
            filters_obj = filters
        elif isinstance(filters, dict):
            filters_obj = ProductFilters(**filters)

    return GetProductsRequest(
        brand_manifest=brand_manifest_obj,
        brief=brief or None,
        filters=filters_obj,
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
    "to_context_object",
    "to_reporting_webhook",
    "create_get_products_request",
    "create_get_products_response",
    # Re-export types for type hints
    "GetProductsRequest",
    "GetProductsResponse",
    "Product",
    "ContextObject",
    "ReportingWebhook",
]
