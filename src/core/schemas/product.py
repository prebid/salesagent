"""Product-related Pydantic schemas for the AdCP protocol.

Extracted from src/core/schemas/__init__.py to reduce file size.
All classes are re-exported from src.core.schemas for backward compatibility.
"""

from typing import Any

from adcp.types import GetProductsRequest as LibraryGetProductsRequest
from adcp.types import GetProductsResponse as LibraryGetProductsResponse
from adcp.types import Placement as LibraryPlacement
from adcp.types import Product as LibraryProduct
from adcp.types import ProductCard as LibraryProductCard
from adcp.types import ProductCardDetailed as LibraryProductCardDetailed
from adcp.types import ProductFilters as LibraryFilters
from adcp.types import PromotedProducts as LibraryPromotedProducts
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

    # Internal-only fields (not in AdCP spec)
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

    @model_validator(mode="after")
    def validate_pricing_fields(self) -> "Product":
        """Validate pricing_options per AdCP spec.

        Per AdCP PR #88: All products must use pricing_options in the database.
        However, pricing_options may be empty in API responses for anonymous/unauthenticated
        users to hide pricing information.
        """
        # pricing_options defaults to empty list if not provided
        # This allows filtering pricing info for anonymous users
        return self

    @model_validator(mode="after")
    def validate_publisher_properties(self) -> "Product":
        """Validate publisher_properties per AdCP spec.

        Per AdCP spec, products must have at least one publisher property.
        """
        if not self.publisher_properties or len(self.publisher_properties) == 0:
            raise ValueError(
                "Product must have at least one publisher_property per AdCP spec. "
                "Properties identify the inventory covered by this product."
            )

        return self

    # Note: In AdCP V3, pricing is determined by field presence:
    # - fixed_price present = fixed pricing
    # - floor_price present = auction pricing with floor
    # The consolidated CpmPricingOption/VcpmPricingOption types handle this automatically.

    def model_dump(self, **kwargs):
        """Return AdCP-compliant model dump with proper field names, excluding internal fields and null values."""
        # Exclude internal/non-spec fields
        kwargs["exclude"] = kwargs.get("exclude", set())
        if isinstance(kwargs["exclude"], set):
            kwargs["exclude"].update({"implementation_config", "expires_at"})

        data = super().model_dump(**kwargs)

        # Convert formats to format_ids per AdCP spec
        if "formats" in data:
            data["format_ids"] = data.pop("formats")

        # Remove null fields per AdCP spec
        # Only truly required fields should always be present
        core_fields = {
            "product_id",
            "name",
            "description",
            "format_ids",
            "delivery_type",
            "delivery_measurement",
            "is_custom",
        }

        adcp_data = {}
        for key, value in data.items():
            # Include core fields always, and non-null optional fields
            # Note: pricing_options=[] is valid for anonymous users (no pricing shown)
            # Per AdCP spec, pricing_options is required but can be empty array
            if key in core_fields or value is not None:
                adcp_data[key] = value
            # Include empty pricing_options explicitly (required per AdCP schema)
            elif key == "pricing_options" and value == []:
                adcp_data[key] = []

        return adcp_data

    def model_dump_internal(self, **kwargs):
        """Return internal model dump including all fields for database operations."""
        return super().model_dump(**kwargs)

    def model_dump_adcp_compliant(self, **kwargs):
        """Return model dump for AdCP schema compliance."""
        return self.model_dump(**kwargs)

    def dict(self, **kwargs):
        """Override dict to maintain backward compatibility."""
        return self.model_dump(**kwargs)


class ProductFilters(LibraryFilters):
    """Product filters extending library Filters from AdCP spec.

    Inherits all AdCP-compliant filter fields from adcp library's Filters class,
    ensuring we stay in sync with spec updates. All fields come from the library:
    - delivery_type: Filter by delivery type (guaranteed, auction)
    - format_ids: Filter by specific format IDs
    - format_types: Filter by format types (video, display, audio)
    - is_fixed_price: Filter for fixed price vs auction products
    - min_exposures: Minimum exposures for measurement validity
    - standard_formats_only: Only return IAB standard formats

    Local extensions (not in AdCP product-filters.json):
    - device_types: Filter by device form factors (mobile, desktop, tablet, ctv, etc.)

    This pattern ensures:
    - External requests use library Filters (spec-compliant)
    - We automatically get spec updates when library updates
    - No manual field duplication = no drift from spec
    """

    # Local extension: device type filtering
    device_types: list[str] | None = Field(
        default=None,
        description="Filter by device form factors (mobile, desktop, tablet, ctv, dooh, audio)",
    )

    @model_validator(mode="before")
    @classmethod
    def upgrade_legacy_format_ids(cls, values: dict) -> dict:
        """Convert dict format_ids to FormatId objects (AdCP v2.4 compliance)."""
        return _upgrade_legacy_format_ids(values)


class GetProductsRequest(LibraryGetProductsRequest):
    """Extends library GetProductsRequest with spec and internal fields.

    Library provides: account_id, brand, brief, buyer_campaign_ref, catalog,
    context, ext, filters, pagination, property_list — all inherited from AdCP 3.6.

    Local extensions: buying_mode, account.

    Internal-only: product_selectors (excluded from external serialization).
    """

    model_config = ConfigDict(extra=get_pydantic_extra_mode())

    # brand is inherited from library as BrandReference | None.
    # BrandReference has .domain (required str) and .brand_id (optional BrandId).

    # Local extensions not yet in adcp library
    buying_mode: str | None = Field(
        None,
        description="Buyer intent: 'brief' (publisher curates) or 'wholesale' (buyer applies own audiences)",
    )
    account: dict[str, Any] | None = Field(
        None,
        description="Account for product lookup. Returns products with pricing specific to this account's rate card (spec: account-ref.json)",
    )

    # Internal-only fields (not in AdCP spec)
    product_selectors: LibraryPromotedProducts | None = Field(
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
