"""Product-related Pydantic schemas for the AdCP protocol.

Extracted from src/core/schemas/__init__.py to reduce file size.
All classes are re-exported from src.core.schemas for backward compatibility.
"""

from typing import Any

from adcp.types import Catalog as LibraryCatalog
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
    format_ids: list[FormatId] = Field(  # type: ignore[assignment]
        ...,
        description="Supported creative formats for this placement",
        min_length=1,
    )


class Product(LibraryProduct):
    """Product schema extending library Product with internal fields.

    Inherits all AdCP-compliant fields from adcp library's Product,
    ensuring we stay in sync with spec updates. Adds only internal-only
    fields that we need for our implementation.

    This pattern ensures:
    - External serialization uses library Product (spec-compliant)
    - Internal code has extra fields it needs (implementation_config)
    - No conversion functions needed - inheritance handles it
    - Automatic updates when library Product changes
    """

    # Internal-only fields — excluded from serialization.
    implementation_config: dict[str, Any] | None = Field(
        default=None,
        description="Internal: Ad server-specific configuration for implementing this product",
        exclude=True,  # Exclude from serialization by default
    )

    # Filter-related fields (not in AdCP Product spec, but needed for filtering)
    countries: list[str] | None = Field(
        default=None,
        description="Internal: Country codes (ISO 3166-1 alpha-2) where this product is available",
        exclude=True,  # Exclude from serialization by default
    )
    # channels: inherited from library Product as list[MediaChannel] | None (public per AdCP spec)

    # Device type targeting (from targeting_template.device_targets in DB)
    device_types: list[str] | None = Field(
        default=None,
        description="Internal: Device types this product supports (mobile, desktop, tablet, ctv, etc.)",
        exclude=True,  # Exclude from serialization by default
    )

    # Principal access control
    allowed_principal_ids: list[str] | None = Field(
        default=None,
        description="Internal: Principal IDs that can see this product. NULL/empty means visible to all.",
        exclude=True,  # Exclude from serialization by default
    )

    # Pricing rules (AdCP V3): ``fixed_price`` present = fixed pricing,
    # ``floor_price`` present = auction with floor. The consolidated
    # CpmPricingOption/VcpmPricingOption types enforce this.
    #
    # ``publisher_properties`` non-emptiness is enforced by the library
    # ``Product`` itself via ``MinLen(1)`` — no local validator needed.

    # No model_dump override: internal-only fields are marked ``exclude=True`` on
    # the field declaration (Pydantic strips them automatically), and the
    # library Product is the source of truth for what's on the wire.


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
    """Extends library GetProductsWholesaleRequest (adcp 3.9: GetProductsRequest is a union alias).

    Base class: GetProductsWholesaleRequest (brief optional, buying_mode='wholesale').
    We widen buying_mode to str|None so callers aren't forced into a single mode.

    Library provides: account, brand, brief, buyer_campaign_ref, catalog,
    context, ext, fields, filters, pagination, property_list, refine.

    Internal-only: product_selectors (excluded from external serialization).
    """

    model_config = ConfigDict(extra=get_pydantic_extra_mode())

    # Widen buying_mode from Literal['wholesale'] to str|None (we accept any mode or none)
    buying_mode: str | None = Field(  # type: ignore[assignment]
        None,
        description="Buyer intent: 'brief' (publisher curates) or 'wholesale' (buyer applies own audiences)",
    )

    # Internal-only field — excluded from external serialization.
    product_selectors: LibraryCatalog | None = Field(
        None,
        description="Selectors to filter the brand manifest product catalog for product discovery",
        exclude=True,
    )


class GetProductsResponse(NestedModelSerializerMixin, LibraryGetProductsResponse):
    """Extends library GetProductsResponse - all fields inherited from AdCP spec.

    Per AdCP PR #113, this response contains ONLY domain data.
    Protocol fields (status, task_id, message, context_id) are added by the
    protocol layer (MCP, A2A, REST) via ProtocolEnvelope wrapper.
    """

    def __str__(self) -> str:
        """Return human-readable message for protocol layer.

        Used by both MCP (for display) and A2A (for task messages).
        Provides conversational text without adding non-spec fields to the schema.
        """
        count = len(self.products)

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
            all(not pricing_option_has_rate(po) for po in p.pricing_options) for p in self.products if p.pricing_options
        ):
            return f"{base_msg} Please connect through an authorized buying agent for pricing data."

        return base_msg


class ProductCatalog(SalesAgentBaseModel):
    """E-commerce product feed information."""

    url: str = Field(..., description="URL to product catalog feed")
    format: str | None = Field(None, description="Feed format (e.g., 'google_merchant', 'json', 'xml')")
