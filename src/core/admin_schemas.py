"""Pydantic request/response models for the admin APIs.

Organized by API surface:
- Platform (multi-tenant) models
- Tenant admin models
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from pydantic import Field

from src.core.schemas import SalesAgentBaseModel

# ---------------------------------------------------------------------------
# Platform (Multi-Tenant) API Models
# ---------------------------------------------------------------------------


class CreateTenantRequest(SalesAgentBaseModel):
    name: str
    subdomain: str
    ad_server: Literal["google_ad_manager", "mock", "kevel", "triton", "broadstreet"]
    creator_email: str | None = None
    authorized_emails: list[str] = []
    authorized_domains: list[str] = []
    is_active: bool = True
    billing_plan: str = "standard"
    billing_contact: str | None = None
    create_default_principal: bool = True
    enable_axe_signals: bool = True
    human_review_required: bool = True
    auto_approve_format_ids: list[str] = Field(default_factory=lambda: ["display_300x250"])
    policy_settings: dict[str, Any] = Field(default_factory=dict)
    # Webhook URLs
    slack_webhook_url: str | None = None
    slack_audit_webhook_url: str | None = None
    hitl_webhook_url: str | None = None
    # Adapter-specific fields (passed through to adapter config)
    gam_network_code: str | None = None
    gam_refresh_token: str | None = None
    gam_trafficker_id: str | None = None
    gam_manual_approval_required: bool = False
    kevel_network_id: str | None = None
    kevel_api_key: str | None = None
    triton_station_id: str | None = None
    triton_api_key: str | None = None
    mock_dry_run: bool = False


class UpdateTenantRequest(SalesAgentBaseModel):
    name: str | None = None
    is_active: bool | None = None
    billing_plan: str | None = None
    billing_contact: str | None = None
    enable_axe_signals: bool | None = None
    authorized_emails: list[str] | None = None
    authorized_domains: list[str] | None = None
    slack_webhook_url: str | None = None
    slack_audit_webhook_url: str | None = None
    hitl_webhook_url: str | None = None
    auto_approve_format_ids: list[str] | None = None
    human_review_required: bool | None = None
    policy_settings: dict[str, Any] | None = None
    adapter_config: dict[str, Any] | None = None


class TenantResponse(SalesAgentBaseModel):
    tenant_id: str
    name: str
    subdomain: str
    is_active: bool
    billing_plan: str | None = None
    ad_server: str | None = None
    created_at: str | None = None
    adapter_configured: bool = False


class TenantDetailResponse(TenantResponse):
    billing_contact: str | None = None
    updated_at: str | None = None
    settings: dict[str, Any] = Field(default_factory=dict)
    adapter_config: dict[str, Any] | None = None
    principals_count: int = 0


class CreateTenantResponse(SalesAgentBaseModel):
    tenant_id: str
    name: str
    subdomain: str
    admin_token: str
    default_principal_token: str | None = None


class DeleteTenantRequest(SalesAgentBaseModel):
    hard_delete: bool = False


class TenantListResponse(SalesAgentBaseModel):
    tenants: list[TenantResponse]
    count: int


# ---------------------------------------------------------------------------
# Sync API Models
# ---------------------------------------------------------------------------


class TriggerSyncRequest(SalesAgentBaseModel):
    sync_type: Literal["full", "inventory", "targeting", "selective"] = "full"
    force: bool = False
    sync_types: list[str] = Field(default_factory=list)  # For selective sync
    custom_targeting_limit: int = 1000
    audience_segment_limit: int | None = None


class SyncStatusResponse(SalesAgentBaseModel):
    sync_id: str
    tenant_id: str
    adapter_type: str | None = None
    sync_type: str | None = None
    status: str
    started_at: str | None = None
    completed_at: str | None = None
    duration_seconds: float | None = None
    triggered_by: str | None = None
    summary: dict[str, Any] | None = None
    error: str | None = None


class SyncHistoryResponse(SalesAgentBaseModel):
    total: int
    limit: int
    offset: int
    results: list[dict[str, Any]]


class SyncStatsResponse(SalesAgentBaseModel):
    status_counts: dict[str, int]
    recent_failures: list[dict[str, Any]]
    stale_tenants: list[dict[str, Any]]
    since: str


class SyncTenantInfo(SalesAgentBaseModel):
    tenant_id: str
    name: str
    subdomain: str
    has_adapter_config: bool
    gam_network_code: str | None = None
    last_sync: dict[str, Any] | None = None


class SyncTenantsResponse(SalesAgentBaseModel):
    total: int
    tenants: list[SyncTenantInfo]


# ---------------------------------------------------------------------------
# Tenant Admin API Models — Configuration Layer
# ---------------------------------------------------------------------------


class AdapterConfigRequest(SalesAgentBaseModel):
    adapter_type: str
    config: dict[str, Any] = Field(default_factory=dict)


class AdapterConfigResponse(SalesAgentBaseModel):
    adapter_type: str
    created_at: str | None = None
    updated_at: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)


class AdapterCapabilitiesResponse(SalesAgentBaseModel):
    supports_inventory_sync: bool = False
    supports_inventory_profiles: bool = False
    inventory_entity_label: str = "Items"
    supports_custom_targeting: bool = False
    supports_geo_targeting: bool = True
    supports_dynamic_products: bool = False
    supported_pricing_models: list[str] = Field(default_factory=list)
    supports_webhooks: bool = False
    supports_realtime_reporting: bool = False


class CurrencyLimitRequest(SalesAgentBaseModel):
    currency_code: str = Field(..., min_length=3, max_length=3)
    min_package_budget: Decimal | None = None
    max_daily_package_spend: Decimal | None = None


class CurrencyLimitResponse(SalesAgentBaseModel):
    tenant_id: str
    currency_code: str
    min_package_budget: float | None = None
    max_daily_package_spend: float | None = None
    created_at: str | None = None
    updated_at: str | None = None


class UpdateCurrencyLimitRequest(SalesAgentBaseModel):
    min_package_budget: Decimal | None = None
    max_daily_package_spend: Decimal | None = None


class PropertyTagRequest(SalesAgentBaseModel):
    tag_id: str
    name: str
    description: str = ""


class PropertyTagResponse(SalesAgentBaseModel):
    tag_id: str
    tenant_id: str
    name: str
    description: str
    created_at: str | None = None


# ---------------------------------------------------------------------------
# Tenant Admin API Models — Inventory & Properties Layer
# ---------------------------------------------------------------------------


class AuthorizedPropertyRequest(SalesAgentBaseModel):
    property_id: str | None = None  # Auto-generated if not provided
    property_type: str = "website"
    name: str
    publisher_domain: str
    identifiers: list[dict[str, str]] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class AuthorizedPropertyResponse(SalesAgentBaseModel):
    property_id: str
    tenant_id: str
    property_type: str
    name: str
    publisher_domain: str
    identifiers: list[dict[str, str]] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    verification_status: str = "pending"
    created_at: str | None = None


class UpdateAuthorizedPropertyRequest(SalesAgentBaseModel):
    name: str | None = None
    publisher_domain: str | None = None
    property_type: str | None = None
    identifiers: list[dict[str, str]] | None = None
    tags: list[str] | None = None


class BulkPropertyUploadRequest(SalesAgentBaseModel):
    properties: list[AuthorizedPropertyRequest]


class InventoryProfileRequest(SalesAgentBaseModel):
    name: str
    description: str | None = None
    inventory_config: dict[str, Any] = Field(default_factory=dict)
    format_ids: list[dict[str, str]] = Field(default_factory=list)
    publisher_properties: list[dict[str, Any]] = Field(default_factory=list)
    targeting_template: dict[str, Any] | None = None


class UpdateInventoryProfileRequest(SalesAgentBaseModel):
    name: str | None = None
    description: str | None = None
    inventory_config: dict[str, Any] | None = None
    format_ids: list[dict[str, str]] | None = None
    publisher_properties: list[dict[str, Any]] | None = None
    targeting_template: dict[str, Any] | None = None


class InventoryProfileResponse(SalesAgentBaseModel):
    id: int
    profile_id: str
    tenant_id: str
    name: str
    description: str | None = None
    inventory_config: dict[str, Any] = Field(default_factory=dict)
    format_ids: list[dict[str, str]] = Field(default_factory=list)
    publisher_properties: list[dict[str, Any]] = Field(default_factory=list)
    targeting_template: dict[str, Any] | None = None
    created_at: str | None = None
    updated_at: str | None = None


# ---------------------------------------------------------------------------
# Tenant Admin API Models — Products + Principals
# ---------------------------------------------------------------------------


class PricingOptionRequest(SalesAgentBaseModel):
    pricing_model: str  # cpm, vcpm, cpc, cpcv, cpp, cpv, flat_rate
    rate: Decimal | None = None
    currency: str = "USD"
    is_fixed: bool = True
    price_guidance: dict[str, Any] | None = None
    parameters: dict[str, Any] | None = None
    min_spend_per_package: Decimal | None = None


class CreateProductRequest(SalesAgentBaseModel):
    product_id: str | None = None  # Auto-generated if not provided
    name: str
    description: str | None = None
    delivery_type: str = "guaranteed"
    format_ids: list[dict[str, str]] = Field(default_factory=list)
    pricing_options: list[PricingOptionRequest] = Field(default_factory=list)
    targeting_template: dict[str, Any] = Field(default_factory=dict)
    # Property authorization (XOR — exactly one required)
    property_ids: list[str] | None = None
    property_tags: list[str] | None = None
    # Optional
    inventory_profile_id: int | None = None
    channels: list[str] | None = None
    countries: list[str] | None = None
    measurement: dict[str, Any] | None = None
    creative_policy: dict[str, Any] | None = None
    # Adapter-specific
    implementation_config: dict[str, Any] | None = None


class UpdateProductRequest(SalesAgentBaseModel):
    name: str | None = None
    description: str | None = None
    delivery_type: str | None = None
    format_ids: list[dict[str, str]] | None = None
    pricing_options: list[PricingOptionRequest] | None = None
    targeting_template: dict[str, Any] | None = None
    property_ids: list[str] | None = None
    property_tags: list[str] | None = None
    channels: list[str] | None = None
    countries: list[str] | None = None
    implementation_config: dict[str, Any] | None = None


class ProductResponse(SalesAgentBaseModel):
    tenant_id: str
    product_id: str
    name: str
    description: str | None = None
    delivery_type: str | None = None
    format_ids: list[dict[str, str]] = Field(default_factory=list)
    pricing_options: list[dict[str, Any]] = Field(default_factory=list)
    property_ids: list[str] | None = None
    property_tags: list[str] | None = None
    channels: list[str] | None = None
    countries: list[str] | None = None
    created_at: str | None = None


class CreatePrincipalRequest(SalesAgentBaseModel):
    name: str
    platform_mappings: dict[str, Any] = Field(default_factory=dict)


class UpdatePrincipalRequest(SalesAgentBaseModel):
    name: str | None = None
    platform_mappings: dict[str, Any] | None = None


class GAMAdvertiserSearchRequest(SalesAgentBaseModel):
    search: str | None = None
    limit: int = 500
    fetch_all: bool = False


class PrincipalResponse(SalesAgentBaseModel):
    tenant_id: str
    principal_id: str
    name: str
    platform_mappings: dict[str, Any] = Field(default_factory=dict)
    access_token: str | None = None  # Only returned on create/regenerate
    created_at: str | None = None
    media_buy_count: int = 0
