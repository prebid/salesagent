"""Product-related Pydantic schemas for the AdCP protocol.

Extracted from src/core/schemas/__init__.py to reduce file size.
All classes are re-exported from src.core.schemas for backward compatibility.
"""

from typing import Any, ClassVar

from adcp.types import BrandReference as LibraryBrandReference
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
    AdcpResponse,
    BuyerRequest,
    FormatId,
    NestedModelSerializerMixin,
    SalesAgentBaseModel,
)

# Private alias: product.py is star-imported by the package __init__, and a bare
# `PricingOption` import here would re-export the pricing wrapper over the legacy
# flat PricingOption that src.core.schemas still exposes (see pricing.py's naming note).
from src.core.schemas.pricing import PricingOption as _PricingOption


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

    # reporting_capabilities is INHERITED as required (core/product.json /required). It used
    # to be redeclared here with a default, which relaxed a spec-required field -- the axis
    # the inheritance guard names -- so a wire model fabricated a value the row did not hold.
    # The default lives at the row-to-model edge instead (src/core/product_conversion.py
    # default_reporting_capabilities), for the rows that still store NULL; the NOT NULL
    # migration that retires it is salesagent-3cs7o.21.

    # Narrowed to the local pricing wrapper (src.core.schemas.pricing) so every
    # member carries our extra policy and the derived is_fixed property. Same
    # wire shape and constraints as the SDK field it overrides; the [assignment]
    # ignore is the expected cost of narrowing a list element type (invariance).
    pricing_options: list[_PricingOption] = Field(  # type: ignore[assignment]
        description="Available pricing models for this product",
        min_length=1,
    )

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

    # No wire shaping of its own. implementation_config is Field(exclude=True) at its
    # declaration; expires_at is a PINNED field (core/product.json) and stays on the wire
    # -- a strip of it here used to hide a spec field. Nulls are omitted by exclude_none at
    # every typed level; pricing_options=[] (the anonymous-user shape) is an empty array,
    # kept as the spec requires.


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

    This pattern ensures:
    - External requests use library Filters (spec-compliant)
    - We automatically get spec updates when library updates
    - No manual field duplication = no drift from spec

    It declares no local extension. ``device_types`` used to be one, and the boundary made
    it unreachable: ``core/product-filters.json`` does not declare it, so the
    accepted-shape strip refuses it in development and drops it in production before the
    filter could ever run.
    """


class GetProductsRequest(BuyerRequest, LibraryGetProductsRequest):
    """Extends library GetProductsWholesaleRequest (adcp 3.9: GetProductsRequest is a union alias).

    Base class: GetProductsWholesaleRequest (brief optional, buying_mode='wholesale').
    We widen buying_mode to str|None so callers aren't forced into a single mode.

    Library provides: account, brand, brief, buyer_campaign_ref, catalog,
    context, ext, fields, filters, pagination, property_list, refine.

    No internal-only field is declared here. ``product_selectors`` used to be, under
    ``exclude=True``; it was a non-spec ALIAS of the inherited spec field ``catalog``
    (identical annotation), it was read nowhere, no builder accepted it, and
    ``_get_products_impl`` is typed to the SDK's own request model, which never declared
    it -- so nothing could set it and nothing could read it. Deleted rather than moved to
    an extended model, because there is no caller for such a model to serve. See
    docs/development/building-tools.md.

    push_notification_config is inherited from the adcp library parent (added in the
    6.6 SDK / spec 3.1.1); no local redeclaration.
    """

    TAGS: ClassVar[tuple[str, ...]] = (
        "products",
        "inventory",
        "catalog",
        "adcp",
    )

    # The spec's type, matching the library parent. This field used to be declared WIDER
    # (``| dict | str``) so the announced shape would admit the brand shorthand -- a bare
    # onto the tool's ``__annotations__`` and FastMCP validates against it. Narrowing it
    # without a replacement is what broke the shorthand twice (18 mcp scenarios, then 16).
    #
    # The shorthand is no longer accepted anywhere: the compat layer that coerced it was
    # deleted whole. This DTO announces the spec's shape and nothing else.
    brand: LibraryBrandReference | None = Field(default=None, description="Brand reference")

    model_config = ConfigDict(extra=get_pydantic_extra_mode())

    # Widen buying_mode from Literal['wholesale'] to str|None (we accept any mode or none)
    buying_mode: str | None = Field(  # type: ignore[assignment]
        None,
        description="Buyer intent: 'brief' (publisher curates) or 'wholesale' (buyer applies own audiences)",
    )


class GetProductsResponse(NestedModelSerializerMixin, LibraryGetProductsResponse, AdcpResponse):
    """Extends library GetProductsResponse - all fields inherited from AdCP spec.

    Per AdCP PR #113, this response contains ONLY domain data.
    Protocol fields (status, task_id, message, context_id) are added by the
    protocol layer (MCP, A2A, REST) via ProtocolEnvelope wrapper.
    """

    # Required (no default): pinned 3.1 get-products-response marks 'products'
    # required. The SDK base declares it optional (list | None); redeclare it
    # required so the model cannot construct an under-specified shape (#1399 Plan-B).
    products: list[LibraryProduct]


class ProductCatalog(SalesAgentBaseModel):
    """E-commerce product feed information."""

    url: str = Field(..., description="URL to product catalog feed")
    format: str | None = Field(None, description="Feed format (e.g., 'google_merchant', 'json', 'xml')")
