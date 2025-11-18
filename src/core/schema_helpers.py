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

# Filters type - import from get_products_request module
try:
    from adcp.types.generated_poc.get_products_request import Filters
except ImportError:
    # Fallback: Filters might be a nested type or not exported
    Filters = dict[str, Any]  # type: ignore


def create_get_products_request(
    brief: str = "",
    brand_manifest: dict[str, Any] | None = None,
    filters: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
) -> GetProductsRequest:
    """Create GetProductsRequest aligned with adcp v1.2.1 spec.

    Args:
        brief: Natural language description of campaign requirements
        brand_manifest: Brand information as dict. Must follow AdCP BrandManifest schema.
                       Example: {"name": "Acme", "url": "https://acme.com"}
                       Or: {"url": "https://acme.com"}
        filters: Structured filters for product discovery

    Returns:
        GetProductsRequest

    Examples:
        >>> req = create_get_products_request(
        ...     brand_manifest={"name": "Acme", "url": "https://acme.com"},
        ...     brief="Display ads"
        ... )
    """
    # Adapt brand_manifest to ensure 'name' field exists (adcp 2.5.0 requirement)
    brand_manifest_adapted = brand_manifest
    if brand_manifest and isinstance(brand_manifest, dict):
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

    # Create GetProductsRequest directly - adcp library handles validation
    # Type ignores: dict inputs are validated by Pydantic at runtime
    return GetProductsRequest(
        brand_manifest=brand_manifest_adapted,  # type: ignore[arg-type]
        brief=brief or None,
        filters=filters,  # type: ignore[arg-type]
        context=context,
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
        products: List of matching products
        errors: List of errors (if any)

    Returns:
        GetProductsResponse
    """
    return GetProductsResponse(
        products=products,  # type: ignore[arg-type]
        errors=errors,
        context=request_context,
    )


# Re-export commonly used generated types for convenience
__all__ = [
    "create_get_products_request",
    "create_get_products_response",
    # Re-export types for type hints
    "GetProductsRequest",
    "GetProductsResponse",
    "Product",
]
