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
        return _upgrade_legacy_format_ids(values)


class GetProductsRequest(LibraryGetProductsRequest):
    """Extends library GetProductsWholesaleRequest into a single class spanning all three modes.

    Base class: GetProductsWholesaleRequest (brief optional, buying_mode='wholesale').
    We widen buying_mode to str|None so a single class covers brief/wholesale/refine modes
    without forcing callers through the library's discriminated union.

    Library provides: account, brand, brief, catalog, context, ext, fields, filters,
    pagination, property_list, refine.

    Internal-only: product_selectors (excluded from external serialization).

    Validators:
    - _normalize_refine_entry_id_field (mode='before'): bridges the rc.3 -> 3.0.6 wire
      rename of refine entry id fields (product_id / proposal_id <-> id). Removable when
      the installed adcp library targets spec 3.0.6+; detected by
      tests/unit/test_architecture_adcp_library_field_skew.py.
    - _validate_buying_mode_invariants (mode='after'): enforces AdCP cross-mode rules
      (brief required for brief mode, refine forbidden in brief/wholesale, etc.). Mirrors
      the seven rule rows at tests/bdd/features/BR-UC-001-discover-available-inventory.feature:313-319.
    """

    model_config = ConfigDict(extra=get_pydantic_extra_mode())

    # Widen buying_mode from Literal['wholesale'] to str|None to span all three modes.
    # The cross-mode invariants below enforce which combinations are valid.
    buying_mode: str | None = Field(  # type: ignore[assignment]
        None,
        description=(
            "Buyer intent: 'brief' (publisher curates from the natural-language brief), "
            "'wholesale' (buyer requests raw inventory and applies their own audiences; "
            "brief and refine forbidden), or 'refine' (iterate on a previous response via "
            "the refine array; brief forbidden). v3 clients MUST include buying_mode; "
            "pre-v3 clients are defaulted to 'brief' at the transport boundary."
        ),
    )

    # Internal-only fields (not in AdCP spec)
    product_selectors: LibraryCatalog | None = Field(
        None,
        description="Selectors to filter the brand manifest product catalog for product discovery",
        exclude=True,
    )

    @model_validator(mode="before")
    @classmethod
    def _normalize_refine_entry_id_field(cls, values: Any) -> Any:
        """Bridge rc.3 <-> 3.0.6 wire shape on refine entries.

        The installed adcp Python library targets spec rc.3; the released spec 3.0.6 (and
        the @adcp/sdk storyboard runner) ship two changes for product- and proposal-scope
        refine entries that the library variants reject under extra='forbid':

        1. Id field renamed: rc.3 uses `id`; 3.0.6 uses `product_id` / `proposal_id`.
        2. `action` is required in rc.3 but defaulted to 'include' in 3.0.6.

        This pre-validator normalizes inbound dicts to rc.3 shape before discriminated
        union routing, so storyboard payloads parse against the installed library.

        Rules:
        - scope='product' with product_id -> rewrite to id
        - scope='proposal' with proposal_id -> rewrite to id
        - both id and product_id/proposal_id present and equal -> drop the wire-name form
        - both present and different -> raise (the wire payload is internally inconsistent)
        - product/proposal scope without action -> default to 'include' (3.0.6 default)
        - scope='request' or unknown scope -> pass through untouched
        """
        if not isinstance(values, dict):
            return values
        refine = values.get("refine")
        if not isinstance(refine, list):
            return values

        normalized: list[Any] = []
        for entry in refine:
            if not isinstance(entry, dict):
                normalized.append(entry)
                continue
            scope = entry.get("scope")
            wire_key = "product_id" if scope == "product" else "proposal_id" if scope == "proposal" else None

            new_entry: dict = dict(entry)
            if wire_key is not None and wire_key in new_entry:
                wire_val = new_entry[wire_key]
                if "id" in new_entry and new_entry["id"] != wire_val:
                    raise ValueError(
                        f"refine entry has both 'id' ({new_entry['id']!r}) and {wire_key!r} "
                        f"({wire_val!r}) with different values; provide only one"
                    )
                del new_entry[wire_key]
                new_entry["id"] = wire_val

            # 3.0.6 defaults action to 'include' on product/proposal scope; rc.3 lib requires it.
            if scope in {"product", "proposal"} and "action" not in new_entry:
                new_entry["action"] = "include"

            normalized.append(new_entry)

        return {**values, "refine": normalized}

    @model_validator(mode="after")
    def _validate_buying_mode_invariants(self) -> "GetProductsRequest":
        """Enforce AdCP cross-mode rules.

        Rule sources: AdCP 3.0 spec (description on each variant's buying_mode Literal) and
        tests/bdd/features/BR-UC-001-discover-available-inventory.feature:313-319.

        The transport wrapper is responsible for defaulting pre-v3 clients to 'brief' before
        the request reaches this validator. If buying_mode is None at this point, the client
        is a v3 client that omitted the required field.
        """
        mode = self.buying_mode

        if mode is None:
            raise ValueError("buying_mode is required (must be one of 'brief', 'wholesale', 'refine')")
        if mode not in {"brief", "wholesale", "refine"}:
            raise ValueError(f"buying_mode must be one of 'brief', 'wholesale', 'refine'; got {mode!r}")

        has_brief = bool(self.brief and self.brief.strip())
        refine_present = self.refine is not None

        if mode == "brief":
            if not has_brief:
                raise ValueError("brief is required when buying_mode is 'brief'")
            if refine_present:
                raise ValueError("refine must not be provided when buying_mode is 'brief'")
        elif mode == "wholesale":
            if has_brief:
                raise ValueError("brief must not be provided when buying_mode is 'wholesale'")
            if refine_present:
                raise ValueError("refine must not be provided when buying_mode is 'wholesale'")
        else:  # mode == "refine"
            if has_brief:
                raise ValueError("brief must not be provided when buying_mode is 'refine'")
            if not refine_present:
                raise ValueError("refine array is required when buying_mode is 'refine'")

        return self


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
