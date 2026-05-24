"""Pydantic models for FreeWheel Publisher API responses.

Models cover both API surfaces:

- v4 (JSON, inventory taxonomy): :class:`Site`, :class:`SiteSection`,
  :class:`SiteGroup`, :class:`Series`, :class:`Video`, :class:`VideoGroup`,
  :class:`InventoryPackage`
- v3 (XML, commercial entities): :class:`Advertiser`, :class:`Campaign`,
  :class:`InsertionOrder`, :class:`Placement`, :class:`Agency`

Both surfaces wrap lists in pagination envelopes with the same essential
shape (current page, page size, total). The :class:`PaginatedResponse`
generic captures that, with item-typed subclasses for ergonomics.

These models are intentionally permissive (``extra="ignore"``) because the
FreeWheel API adds fields without versioning; tests should still validate
expected fields are present, but unexpected fields must not break parsing.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from pydantic import AliasChoices, BaseModel, BeforeValidator, ConfigDict, Field


def _empty_string_to_none(value: Any) -> Any:
    """v3 XML elements with no children serialise as ``""``. Map those to
    ``None`` when the field type is a nested model."""
    if value == "":
        return None
    return value


_OptionalNested = BeforeValidator(_empty_string_to_none)


class _APIModel(BaseModel):
    """Base model with forward-compatible settings.

    FreeWheel evolves response shapes without version bumps, so we ignore
    unknown fields rather than failing validation.
    """

    model_config = ConfigDict(extra="ignore")


# ---------- pagination ----------


class Link(_APIModel):
    """HATEOAS-style link (v4 inventory)."""

    rel: str
    href: str


class PaginatedResponse[T](_APIModel):
    """v4 paginated envelope.

    FreeWheel uses two field-naming conventions across the v4 surface:

    - Inventory taxonomy (sites, videos, etc.) → ``total_count`` / ``total_page``
    - Creative resources (and family)         → ``total``       / ``total_pages``

    Both are accepted here via ``AliasChoices`` so callers don't have to pick
    a wrapper per resource.
    """

    page: int = 1
    per_page: int = 10
    total_count: int = Field(default=0, validation_alias=AliasChoices("total_count", "total"))
    total_page: int = Field(default=1, validation_alias=AliasChoices("total_page", "total_pages"))
    items: list[T] = Field(default_factory=list)
    links: list[Link] = Field(default_factory=list)


# ---------- v4 inventory ----------


class _InventoryEntity(_APIModel):
    """Common fields for v4 inventory entities."""

    id: int
    name: str | None = None
    status: str | None = None
    description: str | None = None
    external_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    links: list[Link] = Field(default_factory=list)


class Site(_InventoryEntity):
    """Top-level inventory unit (e.g. a streaming property or app)."""

    tag: str | None = None
    rating: str | None = None
    url: str | None = None
    metadata: str | None = None
    session_duration: int | None = None
    customized_metadata: dict[str, str] = Field(default_factory=dict)


class SiteSection(_InventoryEntity):
    """A subdivision within a Site (e.g. a content section)."""


class SiteGroup(_InventoryEntity):
    """A grouping of Sites. Site groups can also contain other site groups."""


class Series(_InventoryEntity):
    """A content series. Parent of Videos."""

    network_id: int | None = None
    rating: str | None = None
    metadata: str | None = None
    vod_metadata: str | None = None


class Video(_InventoryEntity):
    """A single video asset."""

    network_id: int | None = None
    title1: str | None = None
    title2: str | None = None
    duration: int | None = None
    rating: str | None = None
    season: int | None = None
    actor: str | None = None
    director: str | None = None
    producer: str | None = None
    writer: str | None = None
    genres: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    air_dates: list[str] = Field(default_factory=list)
    secondary_ids: list[str] = Field(default_factory=list)
    display_right_start_date: datetime | None = None
    display_right_end_date: datetime | None = None
    asset_aging_date: datetime | None = None
    upstream_asset_id: int | None = None
    upstream_network_id: int | None = None
    mirror_type: str | None = None


class VideoGroup(_InventoryEntity):
    """A grouping of Videos (e.g. an audience segment or content collection)."""

    network_id: int | None = None
    rating: str | None = None


class Rendition(_APIModel):
    """One rendition of a creative — the actual ad payload reference.

    For VAST-tag-forwarding setups (the common pattern observed against
    Talpa's network), ``uri`` carries a third-party VAST tag URL that the
    publisher's ad server resolves at delivery time. ``content`` is used
    for hosted-asset uploads instead.
    """

    id: int | None = None
    uri: str | None = None
    content: str | None = None
    content_type: str | None = None
    content_type_id: int | None = None
    width: int | None = None
    height: int | None = None
    quality: str | None = None
    bitrate: int | None = None
    device_pixel_ratio: int | None = None
    https_compatibility: str | None = None
    file_size: int | None = None
    status: str | None = None
    rendition_transcode_profile_id: int | None = None
    vast_rendition: bool | None = None
    operator_network_id: int | None = None


class CreativeMessage(_APIModel):
    """Validation / processing message attached to a creative."""

    type: str
    content: str


class Creative(_APIModel):
    """v4 creative resource — the publisher-side creative record.

    Lives at ``/services/v4/creative_resources``. ``advertiser_ids`` controls
    which advertisers can attach this creative; the actual delivery linkage
    (creative ↔ placement) lives in the separate ``creative_instances``
    endpoint family.

    Renditions are returned inline only when the request includes
    ``?include=renditions`` — without that query flag, only the creative
    envelope's metadata is returned.
    """

    id: int
    name: str | None = None
    base_ad_unit: str | None = None
    base_ad_unit_id: int | None = None
    external_id: str | None = None
    description: str | None = None
    duration: int | None = None
    duration_type: str | None = None
    status: str | None = None
    rating: str | None = None
    rating_id: int | None = None
    tag_type: str | None = None
    tag_type_id: int | None = None
    tv_vod_object_type: str | None = None
    tv_vod_object_id: int | None = None
    clearcast_approval: str | None = None
    clearcast_code_ids: list[int] | None = None
    clearcast_note: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    advertiser_ids: list[int] = Field(default_factory=list)
    agency_ids: list[int] = Field(default_factory=list)
    renditions: list[Rendition] = Field(default_factory=list)
    messages: list[CreativeMessage] = Field(default_factory=list)


class InventoryPackage(_APIModel):
    """A pre-built inventory bundle. Often empty for new networks.

    Shape inferred from an empty-list response; concrete fields will be
    refined when a non-empty fixture is captured.
    """

    id: int | None = None
    name: str | None = None
    status: str | None = None


# ---------- v3 commercial ----------


class _CommercialEntity(_APIModel):
    """Common fields for v3 commercial entities.

    Note: ``created_at``/``updated_at`` are kept as strings because v3
    serialises timestamps in a non-ISO format (``YYYY-MM-DD HH:MM:SS +ZZZZ``)
    that Pydantic's datetime parser rejects. Callers that need datetimes
    can parse them explicitly.
    """

    id: int
    name: str | None = None
    status: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class Schedule(_APIModel):
    """Shared schedule envelope used by Campaigns/IOs/Placements."""

    timezone: str | None = None
    start_date: str | None = None
    end_date: str | None = None


class Budget(_APIModel):
    """Insertion order budget. Currently only IMPRESSION_TARGET observed.

    ``impression`` carries the target impression count for that budget model.
    Other models (e.g. BUDGET_TARGET) would expose different fields; we'll
    add them when fixtures show them.
    """

    budget_model: str | None = None
    impression: int | None = None
    over_delivery_value: int | None = None


class Advertiser(_CommercialEntity):
    """Top-level commercial entity. Owns campaigns."""

    external_id: str | None = None
    billing_term: str | None = None


class Agency(_CommercialEntity):
    """An agency (intermediary between advertiser and publisher)."""

    external_id: str | None = None
    billing_term: str | None = None


class Campaign(_CommercialEntity):
    """A campaign. Created with ``advertiser_id`` minimum; auto-fills
    ``network_id`` from the bearer's context."""

    advertiser_id: int | None = None
    agency_id: int | None = None
    network_id: int | None = None
    description: str | None = None
    external_id: str | None = None
    start_date: str | None = None
    end_date: str | None = None


class InsertionOrder(_CommercialEntity):
    """An insertion order. Lives under a campaign; has its own budget."""

    campaign_id: int | None = None
    advertiser_id: int | None = None
    description: str | None = None
    client_po: str | None = None
    brand_id: str | None = None
    external_id: str | None = None
    primary_sales_person: str | None = None
    primary_trafficker: str | None = None
    stage: str | None = None
    currency: str | None = None
    schedule: Annotated[Schedule | None, _OptionalNested] = None
    budget: Annotated[Budget | None, _OptionalNested] = None


class Placement(_CommercialEntity):
    """A delivery slot within the insertion order's media plan.

    The single-item endpoint returns ``insertion_order_id`` and the
    descriptive fields; the list endpoint returns the ``schedule`` envelope.
    Both shapes parse against this model.
    """

    insertion_order_id: int | None = None
    placement_type: str | None = None
    description: str | None = None
    external_id: str | None = None
    instruction: str | None = None
    schedule: Annotated[Schedule | None, _OptionalNested] = None


class NightlyForecast(_APIModel):
    """Forecasting API v4 nightly result for a single placement.

    The endpoint returns the latest nightly forecast snapshot for a FreeWheel
    placement. It is not a full reporting export, but it includes current
    delivered impressions/budget and final-delivery projections, which are
    enough to back AdCP pacing surfaces when the Query Reporting API is not
    granted.
    """

    placement_id: int
    run_time: str | None = None
    on_schedule_indicator: int | None = None
    unconstrained_gross_available: int | None = None
    gross_available: int | None = None
    net_available: int | None = None
    net_unreserved_available: int | None = None
    exchange_currency: str | None = None
    budget_is_shared: bool | None = None
    budget: str | None = None
    delivered_budget: Decimal | None = None
    delivered_impressions: int | None = None
    forecast_to_be_delivered_budget: Decimal | None = None
    forecast_to_be_delivered_impressions: int | None = None
    forecast_final_delivery_rate: int | None = None
