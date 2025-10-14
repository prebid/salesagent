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
    BrandManifest8,
    BrandManifest9,
    BrandManifest10,
    Filters,
    Filters1,
    GetProductsRequest,
    GetProductsRequest1,
    GetProductsRequest2,
)
from src.core.schemas_generated._schemas_v1_media_buy_get_products_response_json import (
    GetProductsResponse,
    Products,
    Products1,
)


def create_get_products_request(
    brief: str = "",
    promoted_offering: str | None = None,
    brand_manifest: BrandManifest | BrandManifest8 | str | dict[str, Any] | None = None,
    filters: Filters | Filters1 | dict[str, Any] | None = None,
) -> GetProductsRequest:
    """Create GetProductsRequest, automatically choosing the right variant.

    The generated schema has two variants (oneOf in JSON Schema):
    - GetProductsRequest1: requires promoted_offering
    - GetProductsRequest2: requires brand_manifest

    This helper automatically picks the right variant and handles backward compatibility.

    Args:
        brief: Natural language description of campaign requirements
        promoted_offering: DEPRECATED - Use brand_manifest instead
        brand_manifest: Brand information (object, URL string, or dict)
        filters: Structured filters for product discovery

    Returns:
        GetProductsRequest (RootModel wrapping the appropriate variant)

    Examples:
        >>> # Legacy promoted_offering (auto-converts to brand_manifest)
        >>> req = create_get_products_request(
        ...     promoted_offering="https://acme.com",
        ...     brief="Video ads"
        ... )

        >>> # Modern brand_manifest
        >>> req = create_get_products_request(
        ...     brand_manifest={"name": "Acme", "url": "https://acme.com"},
        ...     brief="Display ads"
        ... )
    """
    # Backward compatibility: convert promoted_offering to brand_manifest
    if promoted_offering and not brand_manifest:
        if promoted_offering.startswith("http"):
            brand_manifest = {"url": promoted_offering}
        else:
            brand_manifest = {"name": promoted_offering}

    # Convert filters dict to proper type if needed
    if isinstance(filters, dict):
        filters_obj: Filters | Filters1 | None = Filters(**filters)
    else:
        filters_obj = filters

    # Determine which variant to use FIRST, then convert brand_manifest to correct type
    if promoted_offering and not brand_manifest:
        # Use variant 1 (promoted_offering required, no brand_manifest)
        variant = GetProductsRequest1(
            promoted_offering=promoted_offering,
            brief=brief or None,
            filters=filters_obj,
        )
    else:
        # Use variant 2 (brand_manifest required)
        # Convert brand_manifest to GetProductsRequest2 types (BrandManifest9/BrandManifest10)
        if isinstance(brand_manifest, dict):
            # Choose correct variant based on what's required
            # BrandManifest9: url is required (name optional)
            # BrandManifest10: name is required (url optional)
            if "url" in brand_manifest and brand_manifest["url"] is not None:
                # Has url - use BrandManifest9 (url-required variant)
                brand_manifest_obj: BrandManifest9 | BrandManifest10 | AnyUrl = BrandManifest9(**brand_manifest)
            elif "name" in brand_manifest and brand_manifest["name"] is not None:
                # Has name but no url - use BrandManifest10 (name-required variant)
                brand_manifest_obj = BrandManifest10(**brand_manifest)
            else:
                # Neither url nor name - will fail validation (AdCP requires one)
                raise ValueError("brand_manifest requires at least one of: url, name")
        elif isinstance(brand_manifest, str):
            # URL string
            brand_manifest_obj = AnyUrl(brand_manifest)  # type: ignore[assignment]
        else:
            brand_manifest_obj = brand_manifest  # type: ignore[assignment]

        variant = GetProductsRequest2(
            brand_manifest=brand_manifest_obj,
            promoted_offering=promoted_offering,
            brief=brief or None,
            filters=filters_obj,
        )

    # Wrap in RootModel
    return GetProductsRequest(root=variant)


def create_get_products_response(
    products: list[Products | Products1 | dict[str, Any]],
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
    "BrandManifest8",
    "Filters",
    "Filters1",
    "Products",
    "Products1",
]
