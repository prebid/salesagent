"""Product-related Pydantic schemas for the AdCP protocol.

Extracted from src/core/schemas/__init__.py to reduce file size.
All classes are re-exported from src.core.schemas for backward compatibility.
"""

from adcp.types import GetProductsResponse as LibraryGetProductsResponse
from adcp.types import GetProductsWholesaleRequest as LibraryGetProductsRequest
from adcp.types import Placement as LibraryPlacement
from adcp.types import Product as LibraryProduct
from adcp.types import ProductCard as LibraryProductCard
from adcp.types import ProductCardDetailed as LibraryProductCardDetailed
from adcp.types import ProductFilters as LibraryFilters
from pydantic import ConfigDict, Field, model_validator

from src.core.config import get_pydantic_extra_mode
from src.core.schemas._base import (
    FormatId,
    NestedModelSerializerMixin,
    SalesAgentBaseModel,
    _upgrade_legacy_format_ids,
)


class ProductCard(LibraryProductCard):
    """Visual card for displaying products in user interfaces per AdCP spec.

    Extends library type - all fields inherited.
    Can be rendered via preview_creative or pre-generated.
    Standard card is 300x400px for marketplace display.
    """

    pass  # All fields inherited from library


class ProductCardDetailed(LibraryProductCardDetailed):
    """Detailed card with carousel and full specifications per AdCP spec.

    Extends library type - all fields inherited.
    Provides rich product presentation similar to media kit pages.
    """

    pass  # All fields inherited from library


class Placement(LibraryPlacement):
    """Extends library Placement with stricter field requirements.

    Library makes description and format_ids optional, but our implementation
    requires them for all placements.
    """

    model_config = ConfigDict(extra=get_pydantic_extra_mode())

    description: str = Field(..., description="Detailed description of the placement")
    format_ids: list[FormatId] = Field(
        ...,
        description="Supported creative formats for this placement",
        min_length=1,
    )


# Wire-shape Product is just the AdCP library type. Internal fields live on
# ``src.core.resolved_product.ResolvedProduct``; the production wire path goes
# through :func:`src.core.product_conversion.convert_product_model_to_resolved`,
# which builds the wire shape from explicit ORM fields only.
Product = LibraryProduct


class ProductFilters(LibraryFilters):
    """Product filters extending library Filters from AdCP spec.

    All filter fields come from the library — see adcp ProductFilters for the
    full list (delivery_type, format_ids, format_types, is_fixed_price,
    min_exposures, standard_formats_only, countries, regions, metros,
    channels, etc.).
    """

    @model_validator(mode="before")
    @classmethod
    def upgrade_legacy_format_ids(cls, values: dict) -> dict:
        return _upgrade_legacy_format_ids(values)


class GetProductsRequest(LibraryGetProductsRequest):
    """Extends library GetProductsRequest with live-spec cache precondition fields.

    The installed adcp 5.7.0 library exposes the wholesale/pricing cache
    preconditions; ``if_catalog_version`` remains a compatibility shim for
    earlier v3.1 schema revisions.
    """

    if_catalog_version: str | None = Field(
        None,
        description=("Deprecated catalog_version token accepted for compatibility with earlier v3.1 schema revisions."),
    )


class GetProductsResponse(NestedModelSerializerMixin, LibraryGetProductsResponse):
    """Extends library GetProductsResponse - all fields inherited from AdCP spec.

    Per AdCP PR #113, this response contains ONLY domain data.
    Protocol fields (status, task_id, message, context_id) are added by the
    protocol layer (MCP, A2A, REST) via ProtocolEnvelope wrapper.
    """

    model_config = ConfigDict(
        extra=get_pydantic_extra_mode(),
        use_enum_values=True,
        validate_default=True,
    )

    def __str__(self) -> str:
        """Return human-readable message for protocol layer.

        Used by both MCP (for display) and A2A (for task messages).
        Provides conversational text without adding non-spec fields to the schema.
        """
        products = self.products or []
        count = len(products)

        # Base message
        if count == 0:
            base_msg = "No products matched your requirements."
        elif count == 1:
            base_msg = "Found 1 product that matches your requirements."
        else:
            base_msg = f"Found {count} products that match your requirements."

        # Check if this looks like an anonymous response (all pricing options have no rates)
        # Import here to avoid circular import (schemas -> helpers -> auth -> schemas)
        from src.core.helpers.pricing_helpers import pricing_option_has_rate

        if count > 0 and all(
            all(not pricing_option_has_rate(po) for po in p.pricing_options) for p in products if p.pricing_options
        ):
            return f"{base_msg} Please connect through an authorized buying agent for pricing data."

        return base_msg


class ProductCatalog(SalesAgentBaseModel):
    """E-commerce product feed information."""

    url: str = Field(..., description="URL to product catalog feed")
    format: str | None = Field(None, description="Feed format (e.g., 'google_merchant', 'json', 'xml')")
