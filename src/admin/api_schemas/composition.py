"""Pydantic schemas for the Embedded Composition API (``/api/v1/...``).

Server-to-server REST surface for operator-side authoring of:
- Inventory profiles (operator declares the inventory that backs wholesale
  Products in their catalog).
- Tenant signals (operator declares adapter targeting capabilities; surface
  through the AdCP ``get_signals`` tool).
- Advertiser mappings (``AccountReference`` → adapter advertiser routing).

Storefront discovery + composition itself runs through standard AdCP tools
(``get_products``, ``get_signals``, ``create_media_buy``) — there is no
separate "compose product" write on this surface. See
``.context/embedded-composition-design.md``.

Adapter-agnostic at the boundary:
- ``InventoryProfileRead`` exposes only AdCP-vocab metadata to the
  storefront. Adapter-specific ``inventory_config`` is operator-authored
  on Create/Update.
- ``TenantSignalRead`` mirrors AdCP's existing ``Signal`` shape
  (``value_type``, ``categories``, ``range``). The opaque
  ``adapter_config`` resolution map is operator-authored on Create/Update
  only.
- Advertiser-mapping bodies use ``AccountPattern`` — same field names as
  AdCP ``AccountReference``, with ``brand`` optional for wildcards.

All schemas follow the project ``get_pydantic_extra_mode()`` convention:
forbid unknown fields in dev/CI, ignore them in production.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.core.config import get_pydantic_extra_mode

_EXTRA_MODE = get_pydantic_extra_mode()


def _config() -> ConfigDict:
    return ConfigDict(extra=_EXTRA_MODE)


# ---------------------------------------------------------------------------
# Capability narrowings (AdCP-vocab; storefront-visible)
# ---------------------------------------------------------------------------


class ProfileConstraints(BaseModel):
    """Typed AdCP capability narrowings on an inventory profile.

    Vocabulary references the agent's declared ``DecisioningCapabilities``;
    profiles express narrowings, never redeclarations. Lets the storefront
    pre-validate ``(inventory ∩ signal_selections ∩ buyer_targeting)``
    client-side.
    """

    model_config = _config()

    formats: list[str] = Field(default_factory=list, description="Allowed AdCP format ids")
    channels: list[str] = Field(default_factory=list, description="Allowed AdCP channel names")
    targeting_dimensions: list[str] = Field(
        default_factory=list,
        description="AdCP-standard targeting-dimension names usable on this inventory",
    )


# ---------------------------------------------------------------------------
# Inventory profiles
# ---------------------------------------------------------------------------


class InventoryProfileCreate(BaseModel):
    """Operator-authored. Includes the adapter-shaped ``inventory_config``
    blob (GAM placements, FW sites, Broadstreet zones, …) and the
    AdCP-vocab ``constraints`` narrowings."""

    model_config = _config()

    profile_id: str = Field(..., min_length=1, max_length=100)
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    inventory_config: dict = Field(
        default_factory=dict,
        description=(
            "Adapter-specific inventory selection. Operator-authored, opaque to the "
            "storefront. Shape depends on tenant.ad_server (GAM: {ad_units, placements, "
            "include_descendants}; Freewheel: {site_ids, video_group_ids, ...}; "
            "Broadstreet: {zone_ids}; etc.)."
        ),
    )
    format_ids: list[dict] = Field(default_factory=list)
    publisher_properties: list[dict] = Field(default_factory=list)
    targeting_template: dict | None = None
    constraints: ProfileConstraints | None = None


class InventoryProfileUpdate(BaseModel):
    model_config = _config()

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    inventory_config: dict | None = None
    format_ids: list[dict] | None = None
    publisher_properties: list[dict] | None = None
    targeting_template: dict | None = None
    constraints: ProfileConstraints | None = None


class InventoryProfileRead(BaseModel):
    """Storefront-facing read. AdCP-vocab metadata only — no adapter-shaped
    fields. Operators that need to inspect the underlying ``inventory_config``
    can use the admin UI; this surface is for storefront discovery and
    composition."""

    model_config = _config()

    profile_id: str
    name: str
    description: str | None
    constraints: ProfileConstraints | None
    etag: str | None
    created_at: datetime
    updated_at: datetime


class InventoryProfileListResponse(BaseModel):
    model_config = _config()
    inventory_profiles: list[InventoryProfileRead]


# ---------------------------------------------------------------------------
# Products — profile-backed wholesale catalog entries
# ---------------------------------------------------------------------------


_DELIVERY_TYPE = Literal["guaranteed", "non_guaranteed"]
_PRICING_MODEL = Literal["cpm"]


class ProductPricingOptionWrite(BaseModel):
    """Pricing option persisted with a profile-backed product."""

    model_config = _config()

    pricing_model: _PRICING_MODEL = "cpm"
    currency: str = Field(default="USD", min_length=3, max_length=3)
    is_fixed: bool = False
    rate: Decimal | None = Field(
        default=None,
        description="Fixed price for fixed options. Optional for auction CPM with price_guidance.",
    )
    price_guidance: dict | None = Field(
        default=None,
        description="Auction guidance; CPM auction options require this, e.g. {floor, p50, p75}.",
    )
    parameters: dict | None = None
    min_spend_per_package: Decimal | None = None

    @model_validator(mode="after")
    def _validate_price_shape(self) -> ProductPricingOptionWrite:
        if self.is_fixed and self.rate is None:
            raise ValueError("fixed pricing options require rate")
        if not self.is_fixed and self.pricing_model == "cpm" and not self.price_guidance:
            raise ValueError("auction cpm pricing options require price_guidance")
        return self


class ProductCreate(BaseModel):
    """Create a profile-backed wholesale product."""

    model_config = _config()

    product_id: str = Field(..., min_length=1, max_length=100)
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    inventory_profile_id: str = Field(..., min_length=1, max_length=100)
    delivery_type: _DELIVERY_TYPE = "non_guaranteed"
    pricing_options: list[ProductPricingOptionWrite] = Field(..., min_length=1)
    countries: list[str] | None = None
    channels: list[str] | None = None
    property_targeting_allowed: bool = False
    signal_targeting_allowed: bool = True
    allowed_principal_ids: list[str] | None = None
    catalog_match: dict | None = None
    catalog_types: list[str] | None = None
    data_provider_signals: list[dict] | None = None
    forecast: dict | None = None


class ProductUpdate(BaseModel):
    model_config = _config()

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    inventory_profile_id: str | None = Field(default=None, min_length=1, max_length=100)
    delivery_type: _DELIVERY_TYPE | None = None
    pricing_options: list[ProductPricingOptionWrite] | None = Field(default=None, min_length=1)
    countries: list[str] | None = None
    channels: list[str] | None = None
    property_targeting_allowed: bool | None = None
    signal_targeting_allowed: bool | None = None
    allowed_principal_ids: list[str] | None = None
    catalog_match: dict | None = None
    catalog_types: list[str] | None = None
    data_provider_signals: list[dict] | None = None
    forecast: dict | None = None


class ProductPricingOptionRead(BaseModel):
    model_config = _config()

    pricing_option_id: str
    pricing_model: str
    currency: str
    is_fixed: bool
    rate: Decimal | None
    price_guidance: dict | None
    parameters: dict | None
    min_spend_per_package: Decimal | None


class ProductRead(BaseModel):
    model_config = _config()

    product_id: str
    name: str
    description: str | None
    inventory_profile_id: str | None
    delivery_type: str
    pricing_options: list[ProductPricingOptionRead]
    countries: list[str] | None
    channels: list[str] | None
    property_targeting_allowed: bool
    signal_targeting_allowed: bool | None
    allowed_principal_ids: list[str] | None
    catalog_match: dict | None
    catalog_types: list[str] | None
    data_provider_signals: list[dict] | None
    forecast: dict | None


class ProductListResponse(BaseModel):
    model_config = _config()
    products: list[ProductRead]


# ---------------------------------------------------------------------------
# Tenant signals — operator's map of adapter targeting capabilities
# ---------------------------------------------------------------------------


_SIGNAL_VALUE_TYPE = Literal["binary", "categorical", "numeric"]


class SignalRange(BaseModel):
    """Numeric bounds for a ``value_type='numeric'`` signal."""

    model_config = _config()

    min: Decimal | None = None
    max: Decimal | None = None


class TenantSignalCreate(BaseModel):
    """Operator-authored. ``adapter_config`` is the opaque resolution map
    consumed by the per-adapter materializer at compose time."""

    model_config = _config()

    signal_id: str = Field(
        ...,
        min_length=1,
        max_length=200,
        pattern=r"^[A-Za-z0-9_-]+$",
        description="Stable AdCP signal_id. Use only letters, numbers, underscores, and hyphens.",
    )
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    value_type: _SIGNAL_VALUE_TYPE
    categories: list[str] = Field(default_factory=list, description="Taxonomy when value_type='categorical'")
    range: SignalRange | None = Field(default=None, description="Bounds when value_type='numeric'")
    adapter_config: dict = Field(
        default_factory=dict,
        description=(
            "Adapter-specific resolution map. Operator-authored, opaque to storefront. "
            "Examples: GAM custom KV → {kind: 'custom_key_value', key_id: '...', value_ids: {...}}; "
            "GAM audience → {kind: 'audience_segment', segment_id: '...'}; "
            "Freewheel → {kind: 'audience_item', audience_item_id: '...'}."
        ),
    )
    data_provider: str | None = None
    targeting_dimension: str | None = Field(
        default=None,
        description="AdCP-standard dimension this signal narrows (audience, contextual, weather, ...)",
    )


class TenantSignalUpdate(BaseModel):
    model_config = _config()

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    value_type: _SIGNAL_VALUE_TYPE | None = None
    categories: list[str] | None = None
    range: SignalRange | None = None
    adapter_config: dict | None = None
    data_provider: str | None = None
    targeting_dimension: str | None = None


class TenantSignalRead(BaseModel):
    """Storefront-facing read. Mirrors AdCP ``Signal`` vocabulary — no
    ``adapter_config`` echo. Storefront uses ``value_type`` + ``categories`` /
    ``range`` to render UI."""

    model_config = _config()

    signal_id: str
    name: str
    description: str | None
    value_type: _SIGNAL_VALUE_TYPE
    categories: list[str]
    range: SignalRange | None
    data_provider: str | None
    targeting_dimension: str | None
    etag: str | None
    created_at: datetime
    updated_at: datetime


class TenantSignalListResponse(BaseModel):
    model_config = _config()
    signals: list[TenantSignalRead]


# ---------------------------------------------------------------------------
# Advertiser mappings (operator + brand → adapter advertiser routing)
# ---------------------------------------------------------------------------


class AccountBrandPattern(BaseModel):
    """Brand component of an :class:`AccountPattern`. Same field names as AdCP
    ``BrandReference`` (``domain``, ``brand_id``) but both are optional so a
    routing rule can wildcard the brand house, the brand id, or both."""

    model_config = _config()

    domain: str | None = Field(default=None, max_length=255)
    brand_id: str | None = Field(default=None, max_length=255)


class AccountPattern(BaseModel):
    """Routing-rule pattern over an AdCP account.

    Structurally mirrors ``AccountReference`` (operator + brand + sandbox)
    so the storefront can paste the same shape it uses on
    ``create_media_buy`` — but ``brand`` is optional here, and components
    inside ``brand`` are individually optional, because routing rules
    treat NULL columns as wildcards per the existing resolution chain
    (exact → house wildcard → operator wildcard → tenant default).

    Strict AccountReference targeting one account uses every field; routing
    patterns may leave some absent.
    """

    model_config = _config()

    operator: str = Field(..., min_length=1, max_length=255)
    brand: AccountBrandPattern | None = None
    sandbox: bool = False


class AdvertiserMappingCreate(BaseModel):
    """Route buys carrying ``account: AccountReference`` to a specific adapter
    advertiser. The natural key is ``account`` (operator + brand + sandbox);
    NULL components on ``brand`` act as wildcards.
    """

    model_config = _config()

    account: AccountPattern
    adapter_advertiser_id: str = Field(..., min_length=1, max_length=64)


class AdvertiserMappingUpdate(BaseModel):
    model_config = _config()

    adapter_advertiser_id: str | None = Field(default=None, min_length=1, max_length=64)


class AdvertiserMappingRead(BaseModel):
    model_config = _config()

    mapping_id: str
    account: AccountPattern
    adapter_advertiser_id: str
    created_at: datetime
    updated_at: datetime


class AdvertiserMappingListResponse(BaseModel):
    model_config = _config()
    advertiser_mappings: list[AdvertiserMappingRead]


class AdvertiserSummary(BaseModel):
    """Entry in the synced adapter-advertiser cache. Read-only mirror of the
    operator's GAM (or other adapter) advertiser list."""

    model_config = _config()

    adapter_advertiser_id: str
    name: str
    status: str
    currency_code: str | None = None
    synced_at: datetime


class AdvertiserListResponse(BaseModel):
    model_config = _config()
    advertisers: list[AdvertiserSummary]


# ---------------------------------------------------------------------------
# Generic error envelope
# ---------------------------------------------------------------------------


class ApiError(BaseModel):
    """Generic error envelope mirroring ``tenant_management.ApiError``."""

    model_config = _config()

    error: str
    message: str
    details: dict | None = None
