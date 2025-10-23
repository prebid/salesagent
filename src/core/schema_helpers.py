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

from pydantic import AnyUrl

from src.core.schemas_generated._schemas_v1_media_buy_get_products_request_json import (
    BrandManifest,
    BrandManifest10,
    Filters,
    GetProductsRequest,
)
from src.core.schemas_generated._schemas_v1_media_buy_get_products_response_json import (
    GetProductsResponse,
    Product,
)


def create_get_products_request(
    brief: str = "",
    promoted_offering: str | None = None,
    brand_manifest: BrandManifest | BrandManifest10 | str | dict[str, Any] | None = None,
    filters: Filters | dict[str, Any] | None = None,
) -> GetProductsRequest:
    """Create GetProductsRequest.

    The new schema (post-regeneration) is a single flat class with all optional fields.

    Args:
        brief: Natural language description of campaign requirements
        promoted_offering: Advertiser's promoted offering URL or name
        brand_manifest: Brand information (object, URL string, or dict)
        filters: Structured filters for product discovery

    Returns:
        GetProductsRequest

    Examples:
        >>> # With brand_manifest
        >>> req = create_get_products_request(
        ...     brand_manifest={"name": "Acme", "url": "https://acme.com"},
        ...     brief="Display ads"
        ... )

        >>> # With promoted_offering (backward compat)
        >>> req = create_get_products_request(
        ...     promoted_offering="https://acme.com",
        ...     brief="Video ads"
        ... )
    """
    # Convert filters dict to proper type if needed
    if isinstance(filters, dict):
        filters_obj: Filters | None = Filters(**filters)
    else:
        filters_obj = filters

    # Convert brand_manifest to proper type
    brand_manifest_obj: BrandManifest | BrandManifest10 | AnyUrl | None = None
    if isinstance(brand_manifest, dict):
        # Choose BrandManifest or BrandManifest10 based on what's provided
        if "url" in brand_manifest and brand_manifest["url"] is not None:
            # Has url - use BrandManifest (url-required variant)
            brand_manifest_obj = BrandManifest(**brand_manifest)
        elif "name" in brand_manifest:
            # Only name - use BrandManifest10 (both optional)
            brand_manifest_obj = BrandManifest10(**brand_manifest)
    elif isinstance(brand_manifest, str):
        # URL string
        brand_manifest_obj = AnyUrl(brand_manifest)  # type: ignore[assignment]
    else:
        brand_manifest_obj = brand_manifest  # type: ignore[assignment]

    # Handle promoted_offering → brand_manifest conversion (backward compat)
    if promoted_offering and not brand_manifest_obj:
        # Convert promoted_offering to brand_manifest for AdCP spec compliance
        brand_manifest_obj = BrandManifest10(name=promoted_offering)

    # Create single flat GetProductsRequest (AdCP spec fields only)
    return GetProductsRequest(
        brand_manifest=brand_manifest_obj,  # type: ignore[arg-type]
        brief=brief or None,
        filters=filters_obj,
    )


def create_get_products_response(
    products: list[Product | dict[str, Any]],
    status: str = "completed",
    errors: list | None = None,
) -> GetProductsResponse:
    """Create GetProductsResponse.

    Note: The generated GetProductsResponse is already a simple BaseModel,
    so this helper mainly just provides defaults and type conversion.

    Args:
        products: List of matching products
        status: Response status (default: "completed")
        errors: List of errors (if any)

    Returns:
        GetProductsResponse
    """
    return GetProductsResponse(
        products=products,  # type: ignore[arg-type]
        status=status,  # type: ignore[arg-type]
        errors=errors,
    )


# Re-export commonly used generated types for convenience
__all__ = [
    "create_get_products_request",
    "create_get_products_response",
    # Re-export types for type hints
    "GetProductsRequest",
    "GetProductsResponse",
    "BrandManifest",
    "BrandManifest10",
    "Filters",
    "Product",
]
