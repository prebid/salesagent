"""Pydantic schemas for the Tenant Management API.

See ``docs/design/managed-tenant-mode-sprint-1.md`` for the per-endpoint contract.
All schemas use the project-wide ``get_pydantic_extra_mode()`` helper so they
forbid unknown fields in dev/CI and ignore them in production (CLAUDE.md
pattern #7).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal, cast

from adcp.types import Error as LibraryError
from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic.json_schema import SkipJsonSchema

from src.admin.api_schemas.composition import TenantSignalCreate
from src.admin.api_schemas.publisher_properties import PublisherPropertySelector
from src.admin.services.adapter_connection_tester import AdapterErrorCode, RemediationHint
from src.core.config import get_pydantic_extra_mode
from src.services.catalog_event_types import TENANT_MANAGEMENT_CATALOG_EVENT_TYPES

_EXTRA_MODE = get_pydantic_extra_mode()
CreativeApprovalSetting = Literal["auto", "manual", "ai"]
MediaBuyApprovalSetting = Literal["auto", "manual"]


def _config() -> ConfigDict:
    """Return a fresh ConfigDict for each schema."""
    return ConfigDict(extra=_EXTRA_MODE)


# ---------------------------------------------------------------------------
# Sprint 1.8 §6 — shared validator for the operator's public agent URL
# ---------------------------------------------------------------------------


def _validate_public_agent_url(value: str) -> str:
    """HTTPS-only URL validator for public_agent_url.

    Sprint 1.8 §6: public_agent_url is what publishers list in
    adagents.json to authorize this tenant. Must be HTTPS — adagents.json
    fetch is HTTPS-only, and a non-HTTPS agent URL would never verify.
    """
    stripped = value.strip()
    if not stripped.startswith("https://"):
        raise ValueError(
            f"public_agent_url must start with 'https://'; got {value!r}. adagents.json fetch is HTTPS-only."
        )
    return stripped


def _reject_null_approval_alias(value: str | None) -> str | None:
    """Reject explicit null for compact embedded approval settings."""
    if value is None:
        raise ValueError("omit approval fields to use the default or leave them unchanged; null is not supported")
    return value


# ---------------------------------------------------------------------------
# Embed-mode breadcrumb root override
# ---------------------------------------------------------------------------


class EmbedBreadcrumbRoot(BaseModel):
    """First-crumb override for embedded-mode admin pages.

    When the upstream host renders the salesagent admin UI inside its own
    chrome (``tenant.is_embedded=True``), the first breadcrumb crumb should
    point back to the host's storefront homepage rather than the
    salesagent's tenant dashboard. ``label`` is the visible link text;
    ``url`` is the absolute HTTPS link the host wants the user dropped at.

    Validated at schema boundary so bad input never reaches the rendered
    template; the same model also gates the per-request
    ``X-Embed-Breadcrumb-Root`` header value (see
    :func:`src.admin.utils.breadcrumbs.resolve_embed_breadcrumb_root`).
    """

    model_config = ConfigDict(extra="forbid")

    label: str = Field(..., min_length=1, max_length=120)
    url: str = Field(..., min_length=1, max_length=500)

    @field_validator("url")
    @classmethod
    def _check_https(cls, value: str) -> str:
        stripped = value.strip()
        # HTTPS in production. Localhost http:// allowed so dev / Storefront
        # local stacks (http://localhost:3000) can wire embed_breadcrumb_root
        # without a TLS cert. Loopback only — no broader http:// allowance.
        if stripped.startswith("https://"):
            return stripped
        if stripped.startswith("http://localhost") or stripped.startswith("http://127.0.0.1"):
            return stripped
        raise ValueError(
            f"embed_breadcrumb_root.url must start with 'https://' "
            f"(or 'http://localhost' / 'http://127.0.0.1' for dev); got {value!r}"
        )


# ---------------------------------------------------------------------------
# Adapter config — discriminated union
#
# Discriminated by ``type``; embedder clients (Scope3 storefront) pass the
# type plus fields specific to that adapter. Secret fields use SecretStr so
# they're never logged via the model's default repr. Persistence-layer
# encryption is handled separately by the adapter's own Pydantic schema
# (e.g. FreeWheelConnectionConfig) when the values land in
# AdapterConfig.config_json.
# ---------------------------------------------------------------------------


class GAMAdapterConfig(BaseModel):
    """Google Ad Manager adapter configuration."""

    model_config = _config()

    type: Literal["google_ad_manager"] = "google_ad_manager"
    network_code: str = Field(..., min_length=1, max_length=32)
    service_account_email: str = Field(..., min_length=3, max_length=255)
    # Full JSON of the service account key, encrypted at rest by the model layer.
    service_account_key_json: SecretStr
    # Optional: present when authenticating via OAuth refresh token instead of an SA key.
    refresh_token: SecretStr | None = None


class MockAdapterConfig(BaseModel):
    """Mock adapter configuration (no real backend)."""

    model_config = _config()

    type: Literal["mock"] = "mock"
    # Mock adapter takes no real config; dry_run is a useful test hook.
    dry_run: bool = False


class FreeWheelAdapterConfig(BaseModel):
    """FreeWheel Publisher API adapter configuration.

    Exactly one auth path is required: ``username`` + ``password`` (OAuth2
    password grant — the canonical path, auto-refreshing) or ``api_token``
    (pre-minted bearer, escape hatch for partner-provisioned tokens).
    """

    model_config = _config()

    type: Literal["freewheel"] = "freewheel"
    username: str | None = Field(default=None, max_length=255)
    password: SecretStr | None = None
    api_token: SecretStr | None = None
    environment: Literal["production", "staging"] = "production"
    default_advertiser_id: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def _require_credentials(self) -> FreeWheelAdapterConfig:
        has_password_grant = bool(self.username) and bool(self.password)
        has_token = bool(self.api_token)
        if not has_password_grant and not has_token:
            raise ValueError("FreeWheel config requires either (username + password) or api_token")
        return self


class BroadstreetAdapterConfig(BaseModel):
    """Broadstreet adapter configuration."""

    model_config = _config()

    type: Literal["broadstreet"] = "broadstreet"
    network_id: str = Field(..., min_length=1, max_length=64)
    api_key: SecretStr
    default_advertiser_id: str | None = Field(default=None, max_length=64)
    campaign_name_template: str = Field(default="AdCP-{po_number}-{product_name}", max_length=500)


class SpringServeAdapterConfig(BaseModel):
    """SpringServe (Magnite) ad-server adapter configuration.

    Exactly one auth path is required: ``email`` + ``password`` (the
    canonical path -- the SpringServe API mints a 2-hour token from
    these credentials and the adapter caches + refreshes) or
    ``api_token`` (a pre-minted token, escape hatch for partner-
    provisioned tokens; no auto-refresh).
    """

    model_config = _config()

    type: Literal["springserve"] = "springserve"
    email: str | None = Field(default=None, max_length=255)
    password: SecretStr | None = None
    api_token: SecretStr | None = None
    environment: Literal["production"] = "production"
    default_demand_partner_id: int | None = None
    rate_currency: str = Field(default="USD", pattern="^[A-Z]{3}$", min_length=3, max_length=3)
    demand_class: Literal["line_item", "tag"] = "line_item"
    enable_key_value_targeting: bool = False

    @field_validator("rate_currency", mode="before")
    @classmethod
    def _normalize_rate_currency(cls, value: str) -> str:
        return value.upper() if isinstance(value, str) else value

    @model_validator(mode="after")
    def _require_credentials(self) -> SpringServeAdapterConfig:
        has_password_grant = bool(self.email) and bool(self.password)
        has_token = bool(self.api_token)
        if not has_password_grant and not has_token:
            raise ValueError("SpringServe config requires either (email + password) or api_token")
        return self


# Public discriminated alias used in request/response schemas.
# Triton (``type="triton"``) is intentionally absent — the adapter is parked
# while Triton's APIs aren't production-ready. Restoring is a one-line union
# addition + registry re-add when their APIs come back; the source module
# under src/adapters/triton/ is preserved.
AdapterConfig = Annotated[
    GAMAdapterConfig | MockAdapterConfig | FreeWheelAdapterConfig | BroadstreetAdapterConfig | SpringServeAdapterConfig,
    Field(discriminator="type"),
]


GAM_ORDER_NAME_MACROS = [
    {"name": "campaign_name", "description": "Brand-derived campaign name; falls back to Campaign."},
    {"name": "brand_name", "description": "Brand domain/name from the media-buy request."},
    {"name": "promoted_offering", "description": "Backward-compatible alias for brand_name."},
    {"name": "auto_name", "description": "AI-generated name when AI naming is enabled and configured."},
    {"name": "date_range", "description": "Formatted flight date range."},
    {"name": "month_year", "description": "Month and year for the flight start."},
    {"name": "media_buy_id", "description": "Pre-created media-buy/order identifier."},
    {"name": "buyer_ref", "description": "Backward-compatible alias for media_buy_id."},
    {"name": "package_count", "description": "Number of packages in the media buy."},
    {"name": "start_date", "description": "Flight start date in YYYY-MM-DD format."},
    {"name": "end_date", "description": "Flight end date in YYYY-MM-DD format."},
]

GAM_LINE_ITEM_NAME_MACROS = [
    {"name": "order_name", "description": "Resolved parent GAM order name."},
    {"name": "product_name", "description": "Product name associated with the package."},
    {"name": "package_name", "description": "Package name from the request, falling back to product_name."},
    {"name": "package_index", "description": "1-based package position in the media buy."},
]

BROADSTREET_CAMPAIGN_NAME_MACROS = [
    {"name": "po_number", "description": "PO number from the media-buy request, or unknown."},
    {"name": "product_name", "description": "Name of the first product in the media buy."},
    {"name": "advertiser_name", "description": "Principal/advertiser display name."},
    {"name": "timestamp", "description": "UTC timestamp in YYYYMMDD_HHMMSS format."},
]


class TemplateMacro(BaseModel):
    """One supported naming-template macro."""

    model_config = _config()

    name: str
    description: str


class AdapterSettingsSchemaResponse(BaseModel):
    """Runtime adapter settings schema with template-macro metadata."""

    model_config = _config()

    type: str
    schema_: dict[str, Any] = Field(..., alias="schema")
    template_macros: dict[str, list[TemplateMacro]] = Field(default_factory=dict)


class GoogleAdManagerSettings(BaseModel):
    """GAM runtime settings that affect how buys are materialized."""

    model_config = _config()

    type: Literal["google_ad_manager"] = "google_ad_manager"
    order_name_template: str | None = Field(
        default=None,
        max_length=500,
        description="Template used for GAM order names. Uses {macro} syntax and supports fallback syntax like {campaign_name|brand_name}.",
        json_schema_extra={"x-supported-macros": cast(Any, GAM_ORDER_NAME_MACROS)},
    )
    line_item_name_template: str | None = Field(
        default=None,
        max_length=500,
        description="Template used for GAM line item names. Uses {macro} syntax.",
        json_schema_extra={"x-supported-macros": cast(Any, GAM_LINE_ITEM_NAME_MACROS)},
    )
    auto_naming_enabled: bool = Field(
        default=True,
        description="Allow {auto_name} in order_name_template to invoke tenant AI naming when AI configuration is available.",
    )
    manual_approval_required: bool = Field(
        default=False,
        description="Require manual approval before GAM orders are pushed live.",
    )


class FreeWheelSettings(BaseModel):
    """FreeWheel runtime settings that affect how buys are materialized."""

    model_config = _config()

    type: Literal["freewheel"] = "freewheel"
    default_advertiser_id: str | None = Field(
        default=None,
        max_length=64,
        description="Fallback FreeWheel advertiser ID for principals without an explicit FreeWheel mapping.",
    )


class BroadstreetSettings(BaseModel):
    """Broadstreet runtime settings that affect how buys are materialized."""

    model_config = _config()

    type: Literal["broadstreet"] = "broadstreet"
    default_advertiser_id: str | None = Field(
        default=None,
        max_length=64,
        description="Fallback Broadstreet advertiser ID for principals without an explicit Broadstreet mapping.",
    )
    campaign_name_template: str = Field(
        default="AdCP-{po_number}-{product_name}",
        max_length=500,
        description="Template used for Broadstreet campaign names. Uses {macro} syntax.",
        json_schema_extra={"x-supported-macros": cast(Any, BROADSTREET_CAMPAIGN_NAME_MACROS)},
    )


class SpringServeSettings(BaseModel):
    """SpringServe runtime settings that affect how buys are materialized."""

    model_config = _config()

    type: Literal["springserve"] = "springserve"
    default_demand_partner_id: int | None = Field(
        default=None,
        description=("Fallback SpringServe Demand Partner ID for principals without an explicit SpringServe mapping."),
    )
    rate_currency: str = Field(
        default="USD",
        pattern="^[A-Z]{3}$",
        min_length=3,
        max_length=3,
        description="ISO 4217 currency used for SpringServe Campaign and Demand Tag rates.",
    )
    demand_class: Literal["line_item", "tag"] = Field(
        default="line_item",
        description=(
            "'line_item' means SpringServe hosts buyer creative assets; 'tag' means buyer-supplied "
            "third-party VAST/audio tags pass through without creative binding."
        ),
    )
    enable_key_value_targeting: bool = Field(
        default=False,
        description="Translate AdCP signals into SpringServe demand_tag_keys entries on created demand tags.",
    )

    @field_validator("rate_currency", mode="before")
    @classmethod
    def _normalize_rate_currency(cls, value: str) -> str:
        return value.upper() if isinstance(value, str) else value


class AdapterSettingsValidationError(BaseModel):
    """One validation error for adapter runtime settings."""

    model_config = _config()

    field: str
    message: str


class AdapterSettingsValidationResponse(BaseModel):
    """Validation result for adapter runtime settings."""

    model_config = _config()

    valid: bool
    errors: list[AdapterSettingsValidationError] = Field(default_factory=list)
    preview: dict[str, str] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Adapter discovery — embedder-facing catalog of supported adapter types
# ---------------------------------------------------------------------------


class AdapterCapabilitiesSummary(BaseModel):
    """Operational capabilities flags surfaced on the discovery endpoint.

    Mirrors ``src.adapters.base.AdapterCapabilities`` (the class-level
    static on each adapter). Each adapter declares its caps; the discovery
    endpoint just surfaces them to embedder clients so they can decide
    which adapter to surface and which UI sections to render.
    """

    model_config = _config()

    supports_inventory_sync: bool = False
    supports_inventory_profiles: bool = False
    inventory_entity_label: str | None = None
    supports_custom_targeting: bool = False
    supports_geo_targeting: bool = False
    supports_dynamic_products: bool = False
    supported_pricing_models: list[str] = Field(default_factory=list)
    supports_webhooks: bool = False
    supports_realtime_reporting: bool = False
    supports_reporting_sync: bool = False
    reporting_bundled_with_inventory: bool = False


class AdapterUnsupportedFeature(BaseModel):
    """Machine-readable unsupported capability surfaced by adapter contracts."""

    model_config = _config()

    feature: str = Field(..., min_length=1, description="Stable unsupported feature identifier.")
    reason: str = Field(..., min_length=1)
    remediation: str | None = Field(default=None, min_length=1)


class AdapterCapabilitiesResponse(AdapterCapabilitiesSummary):
    """Full response from ``GET /adapters/{adapter_type}/capabilities``."""

    type: str = Field(..., description="Adapter type identifier.")
    contract_version: str = Field(..., description="Version of this adapter's tenant-management contract.")
    supports_audiences: bool = False
    supports_forecasting: bool = False
    supports_reporting: bool = False
    supports_pricing_recommendations: bool = False
    supports_inventory_configuration_authoring: bool = False
    supports_signal_mapping_authoring: bool = False
    supports_materialization_preview: bool = False
    sync_streams: list[str] = Field(default_factory=list)
    supported_object_types: list[str] = Field(default_factory=list)
    supported_signal_types: list[str] = Field(default_factory=list)
    unsupported_features: list[AdapterUnsupportedFeature] = Field(default_factory=list)


class AdapterCatalogEntry(BaseModel):
    """One supported adapter type returned by the discovery endpoint."""

    model_config = _config()

    type: str = Field(..., description="Adapter type identifier (use as AdapterConfig.type).")
    name: str = Field(..., description="Human-readable adapter name.")
    description: str = Field(..., description="Short description of what this adapter does.")
    tier: Literal["live", "test"] = Field(
        default="live",
        description=(
            "Whether this adapter is meant for production use. ``live`` = real ad-server "
            "integration intended for production pickers. ``test`` = simulated/dev-only "
            "adapter (Mock) — embedders should filter these out of production UI by default."
        ),
    )
    default_channels: list[str] = Field(default_factory=list)
    contract_version: str = Field(..., description="Version of this adapter's tenant-management contract.")
    capabilities_url: str = Field(..., description="Adapter-specific capabilities URL.")
    capabilities: AdapterCapabilitiesSummary
    connection_schema: dict[str, Any] = Field(
        ..., description="JSON Schema for this adapter's typed connection config (the discriminated union member)."
    )


class ListAdaptersResponse(BaseModel):
    """Response from ``GET /adapters`` — full catalog of supported adapter types."""

    model_config = _config()

    adapters: list[AdapterCatalogEntry]
    count: int


# ---------------------------------------------------------------------------
# Provision: request + response
# ---------------------------------------------------------------------------


class InitialPrincipalRequest(BaseModel):
    """Optional initial advertiser created at provision time."""

    model_config = _config()

    name: str = Field(..., min_length=1, max_length=255)
    # GAM advertiser ID, etc. Optional — not all adapters use external IDs.
    external_advertiser_id: str | None = Field(default=None, max_length=255)


class ProvisionTenantRequest(BaseModel):
    """Request body for ``POST /tenants/provision``."""

    model_config = _config()

    # Identity
    name: str = Field(..., min_length=1, max_length=255)
    external_org_id: str = Field(..., min_length=1, max_length=255)
    external_source: str = Field(..., min_length=1, max_length=64)
    contact_email: EmailStr

    # AAO model (sprint 1.7).
    # ``public_agent_url`` is what publishers list in adagents.json to
    # authorize this tenant. Embedded-mode tenants share the platform's
    # interchange.io URL; the provision route defaults to it when omitted.
    public_agent_url: str = Field(default="https://interchange.io", min_length=1, max_length=500)

    # Adapter config (required — a tenant without an adapter is useless)
    adapter: AdapterConfig

    # Defaults
    default_currency: str = Field(default="USD", min_length=3, max_length=3)
    billing_plan: str = Field(default="standard", max_length=64)

    # Sprint 1.8 — fall-through advertiser. Optional at provision time;
    # required before activation (enforced by the routing chain at
    # create_media_buy time, not by an explicit /activate endpoint).
    default_gam_advertiser_id: str | None = Field(default=None, max_length=64)

    # When True, the provision endpoint will ensure adapter-specific default
    # resources exist (e.g. the Interchange default advertiser for GAM), creating
    # them if absent. Requires the adapter credentials to have create permission.
    # Defaults to False — callers must opt in explicitly.
    provision_default_resources: bool = False

    # Optional convenience: create one principal in the same call
    initial_principal: InitialPrincipalRequest | None = None

    # Storefront-facing approval controls. These compact aliases map to the
    # seller's internal approval fields at provision time.
    creative_approval: CreativeApprovalSetting = "manual"
    media_buy_approval: MediaBuyApprovalSetting = "manual"

    # Embed-mode breadcrumb root override. Only meaningful when the
    # tenant is embedded inside an upstream host — open-instance
    # tenants ignore this even if set.
    embed_breadcrumb_root: EmbedBreadcrumbRoot | None = None

    # Sprint 1.8 §6: HTTPS-only public_agent_url.
    @field_validator("public_agent_url")
    @classmethod
    def _check_public_agent_url(cls, value: str) -> str:
        return _validate_public_agent_url(value)

    @field_validator("creative_approval", "media_buy_approval")
    @classmethod
    def _reject_null_approval_aliases(cls, value: str | None) -> str | None:
        return _reject_null_approval_alias(value)


class ProvisionedPrincipalResponse(BaseModel):
    """Initial principal returned from provision.

    Includes the principal's ``access_token`` so the host product can stamp
    ``x-adcp-auth`` on buyer-protocol calls (or store the token for the host's
    own buyer agents to use). The token is the value already persisted in
    ``Principal.access_token`` — exposing it here just avoids forcing host
    products into out-of-band DB reads to discover something we already minted.

    Identity-propagation via ``X-Identity-*`` headers (the sprint 2 buyer-protocol
    middleware) is still the long-term direction; this token is what unblocks
    host products today and remains the canonical bearer for any caller that
    isn't routing through the trusted-network identity proxy.
    """

    model_config = _config()

    principal_id: str
    name: str
    access_token: str


class AdapterStatusResponse(BaseModel):
    """Compact adapter status block returned in provision responses."""

    model_config = _config()

    type: str
    configured: bool
    connection_test_passed: bool
    connection_test_error: str | None = None


class ProvisionTenantResponse(BaseModel):
    """Response body for ``POST /tenants/provision``."""

    model_config = _config()

    tenant_id: str
    name: str
    external_org_id: str
    external_source: str
    # ``managed_externally`` retained as a deprecated alias of ``is_embedded``
    # so existing Storefront callers continue to function during the rename.
    is_embedded: Literal[True] = True
    managed_externally: Literal[True] = True
    created_at: datetime

    # Surfaces — URLs the upstream platform needs to know about.
    mcp_url: AnyHttpUrl
    a2a_url: AnyHttpUrl
    admin_url_path: str

    adapter: AdapterStatusResponse

    initial_principal: ProvisionedPrincipalResponse | None = None


# ---------------------------------------------------------------------------
# Tenant lifecycle: list / get / patch
# ---------------------------------------------------------------------------


class TenantSummary(BaseModel):
    """Compact tenant entry for ``GET /tenants`` listings."""

    model_config = _config()

    tenant_id: str
    name: str
    # Subdomain is open-instance metadata; included so legacy callers can still pivot on it.
    subdomain: str | None = None
    external_org_id: str | None = None
    external_source: str | None = None
    is_embedded: bool
    # ``managed_externally`` retained as a deprecated alias of ``is_embedded``
    # so existing Storefront callers continue to function during the rename.
    managed_externally: bool
    is_active: bool
    billing_plan: str
    ad_server: str | None = None
    adapter_configured: bool
    created_at: datetime


class TenantDetail(TenantSummary):
    """Full tenant detail returned from ``GET /tenants/{id}`` and lifecycle responses."""

    model_config = _config()

    contact_email: EmailStr | None = None
    default_currency: str | None = None
    # AAO model (sprint 1.7). Nullable on detail responses because
    # legacy open-instance tenants migrated from the AuthorizedProperty
    # path don't have it populated yet.
    public_agent_url: str | None = None
    # Sprint 1.8 — fall-through advertiser. Nullable until activation.
    default_gam_advertiser_id: str | None = None
    # Embed-mode breadcrumb root override (only meaningful when
    # ``is_embedded`` is true).
    embed_breadcrumb_root: EmbedBreadcrumbRoot | None = None
    # Storefront-facing approval controls. These are compact aliases for the
    # seller's internal approval fields.
    creative_approval: CreativeApprovalSetting | None = None
    media_buy_approval: MediaBuyApprovalSetting | None = None


class ListTenantsResponse(BaseModel):
    model_config = _config()

    tenants: list[TenantSummary]
    count: int


class UpdateTenantRequest(BaseModel):
    """PATCH body — only platform-managed fields are exposed.

    ``external_org_id`` and ``external_source`` are NOT modifiable post-creation;
    they identify the tenant's relationship with the upstream platform.
    ``is_active`` is changed via the deactivate/reactivate endpoints, not PATCH.
    """

    model_config = _config()

    name: str | None = Field(default=None, min_length=1, max_length=255)
    contact_email: EmailStr | None = None
    billing_plan: str | None = Field(default=None, max_length=64)
    # AAO model — patchable post-creation. An upstream platform rotating
    # its public_agent_url flows through here.
    public_agent_url: str | None = Field(default=None, min_length=1, max_length=500)
    # Sprint 1.8 — fall-through advertiser. Patchable any time. PATCH with
    # an explicit empty string is rejected by the schema. Omit to leave
    # unchanged; send null to clear and re-open the routing readiness blocker.
    default_gam_advertiser_id: str | None = Field(default=None, min_length=1, max_length=64)
    # Embed-mode breadcrumb root override. Patch with a non-null object
    # to install/replace the override. PATCH with omitted key leaves the
    # current value alone (other fields use the same omit-to-leave
    # semantic).
    embed_breadcrumb_root: EmbedBreadcrumbRoot | None = None
    creative_approval: CreativeApprovalSetting | SkipJsonSchema[None] = None
    media_buy_approval: MediaBuyApprovalSetting | SkipJsonSchema[None] = None

    @field_validator("creative_approval", "media_buy_approval")
    @classmethod
    def _reject_null_approval_aliases(cls, value: str | None) -> str | None:
        return _reject_null_approval_alias(value)

    # Sprint 1.8 §6: same public_agent_url validator as ProvisionTenantRequest.
    # ``mode='before'`` lets us short-circuit on None (PATCH with field
    # absent should pass through unchanged).
    @field_validator("public_agent_url")
    @classmethod
    def _check_public_agent_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_public_agent_url(value)


# ---------------------------------------------------------------------------
# Adapter management
# ---------------------------------------------------------------------------


class AdapterConfigResponse(BaseModel):
    """Adapter config view returned with secrets redacted."""

    model_config = _config()

    type: str
    configured: bool
    network_code: str | None = None
    service_account_email: str | None = None
    # Always redacted — clients should not see secret material on read paths.
    service_account_key_json: str | None = None
    refresh_token: str | None = None


class AdapterCapabilityCheck(BaseModel):
    """One capability check result returned by adapter probes.

    ``test-connection`` stays non-mutating, so write capabilities that require
    a live mutation should be reported as ``not_checked`` rather than inferred
    from successful authentication.
    """

    model_config = _config()

    capability: str = Field(..., min_length=1, max_length=128)
    status: Literal["passed", "failed", "not_checked"]
    message: str | None = Field(default=None, max_length=500)
    error_code: AdapterErrorCode | None = None
    remediation: RemediationHint | None = None
    details: dict[str, Any] | None = None


class TestConnectionResponse(BaseModel):
    """Result of an adapter connection probe.

    ``error_code`` classifies the fault into a closed set of typed values
    so the UI can branch without parsing the human-readable ``error``.
    The same code appears as the suffix of the ``adapter_{code}`` error
    in :class:`ApiError` envelopes from the provision / PUT paths. See
    :mod:`src.admin.services.adapter_connection_tester`.

    ``remediation`` (when populated) tells the UI WHO can fix the problem
    — useful for ``permission_denied`` (vendor enables a role vs customer
    rebinds the account) where the error code alone isn't actionable.
    """

    model_config = _config()

    success: bool
    error: str | None = None
    error_code: AdapterErrorCode | None = None
    remediation: RemediationHint | None = None
    details: dict[str, Any] | None = None
    capability_checks: list[AdapterCapabilityCheck] = Field(default_factory=list)
    tested_at: datetime


# ---------------------------------------------------------------------------
# Sprint 1.5 — preview adapter (no persistence)
# ---------------------------------------------------------------------------


class PreviewAdapterRequest(BaseModel):
    """Pre-provision adapter probe — same union as :class:`ProvisionTenantRequest`."""

    model_config = _config()

    adapter: AdapterConfig


class PreviewAdapterResponse(BaseModel):
    """Adapter preview with network metadata for the Storefront UX.

    ``ok=False`` (bad creds) is returned with HTTP 200 — Storefront renders
    this inline. Hard errors (malformed body, missing API key) still surface
    via the normal 4xx path.

    ``error_code`` carries the same typed classification as
    :class:`TestConnectionResponse` so the UI can branch on machine-readable
    fault categories rather than parsing ``error``.
    """

    model_config = _config()

    ok: bool
    network_name: str | None = None
    network_code: str | None = None
    currency_code: str | None = None
    time_zone: str | None = None
    inventory_reachable: bool = False
    error: str | None = None
    error_code: AdapterErrorCode | None = None
    remediation: RemediationHint | None = None
    details: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Wholesale product authoring — embedded storefront API
# ---------------------------------------------------------------------------


class FormatIdRef(BaseModel):
    """AdCP FormatId reference used by wholesale-product authoring."""

    model_config = _config()

    agent_url: str = Field(..., min_length=1, max_length=2048)
    id: str = Field(..., min_length=1, max_length=255)
    width: int | None = Field(default=None, ge=1)
    height: int | None = Field(default=None, ge=1)
    duration_ms: int | None = Field(default=None, ge=1)


class WholesalePricingOptionResponse(BaseModel):
    """One pricing option returned to embedder clients."""

    model_config = _config()

    pricing_option_id: str = Field(..., min_length=1, max_length=128)
    pricing_model: str = Field(..., min_length=1, max_length=32)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    is_fixed: bool = True
    rate: Decimal | None = None
    price_guidance: dict[str, Any] | None = None
    parameters: dict[str, Any] | None = None
    min_spend_per_package: Decimal | None = None


class WholesaleSlotRequirement(BaseModel):
    """Optional slot-level requirements for multi-asset formats."""

    model_config = _config()

    slot_id: str = Field(..., min_length=1, max_length=128)
    name: str | None = Field(default=None, max_length=255)
    asset_type: str | None = Field(default=None, max_length=64)
    width: int | None = Field(default=None, ge=1)
    height: int | None = Field(default=None, ge=1)
    duration_ms: int | None = Field(default=None, ge=1)
    required: bool = True
    requirements: dict[str, Any] | None = None


class WholesaleCreativeFormat(BaseModel):
    """Creative format accepted by a wholesale product."""

    model_config = _config()

    format_id: FormatIdRef
    slot_requirements: list[WholesaleSlotRequirement] = Field(default_factory=list)


class InventoryExecutionSelector(BaseModel):
    """Adapter selector that tells the ad server where a wholesale product executes."""

    model_config = _config()

    selector_type: str = Field(..., min_length=1, max_length=64)
    external_id: str = Field(..., min_length=1, max_length=255)
    name: str | None = Field(default=None, max_length=512)
    options: dict[str, Any] = Field(default_factory=dict)


class WholesaleFormatBinding(BaseModel):
    """Adapter-specific binding for a buyer-facing creative format."""

    model_config = _config()

    format_id: FormatIdRef
    adapter_config: dict[str, Any] = Field(default_factory=dict)


class WholesaleInventoryExecution(BaseModel):
    """Adapter execution section of a wholesale product."""

    model_config = _config()

    adapter: str = Field(..., min_length=1, max_length=64)
    selectors: list[InventoryExecutionSelector] = Field(default_factory=list)
    format_bindings: list[WholesaleFormatBinding] = Field(default_factory=list)


class WholesaleInventory(BaseModel):
    """Inventory section of a wholesale product."""

    model_config = _config()

    publisher_properties: list[PublisherPropertySelector] = Field(default_factory=list)
    creative_formats: list[WholesaleCreativeFormat] = Field(default_factory=list)
    execution: WholesaleInventoryExecution


WholesaleProductStatus = Literal["draft", "active", "archived"]


class WholesaleProductBase(BaseModel):
    """Shared wholesale-product fields."""

    model_config = _config()

    wholesale_product_id: str | None = Field(default=None, min_length=1, max_length=100)
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    status: WholesaleProductStatus = "active"
    delivery_type: str = Field(default="non_guaranteed", min_length=1, max_length=50)
    channels: list[str] | None = None
    inventory: WholesaleInventory
    targeting_capabilities: dict[str, Any] = Field(default_factory=dict)
    optimization_capabilities: dict[str, Any] = Field(default_factory=dict)
    allowed_actions: list[str] | None = None
    format_options: list[dict[str, Any]] | None = None
    video_placement_types: list[str] | None = None
    vendor_metric_optimization: dict[str, Any] | None = None
    allowed_principal_ids: list[str] | None = None


class WholesaleProductRequest(WholesaleProductBase):
    """Create/update body for wholesale-product authoring."""

    delivery_type: Literal["non_guaranteed"] = "non_guaranteed"


class WholesaleProductResponse(WholesaleProductBase):
    """Wholesale product as persisted and returned to embedder clients."""

    wholesale_product_id: str = Field(..., min_length=1, max_length=100)
    product_id: str = Field(..., min_length=1, max_length=100)
    forecast: dict[str, Any] | None = Field(
        default=None,
        description="System-owned forecast metadata populated by Sales Agent syncs when available.",
    )
    pricing_options: list[WholesalePricingOptionResponse] = Field(default_factory=list)
    inventory_profile_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ListWholesaleProductsResponse(BaseModel):
    """List response for wholesale products."""

    model_config = _config()

    wholesale_products: list[WholesaleProductResponse]
    count: int


class DeleteWholesaleProductResponse(BaseModel):
    """Delete response for wholesale products."""

    model_config = _config()

    success: bool
    message: str


class WholesaleValidationIssue(BaseModel):
    """One validation issue for a wholesale-product draft."""

    model_config = _config()

    code: str
    message: str
    field: str | None = None
    severity: Literal["error", "warning"] = "error"


class WholesaleProductValidationResponse(BaseModel):
    """Validation result for wholesale-product authoring."""

    model_config = _config()

    valid: bool
    issues: list[WholesaleValidationIssue] = Field(default_factory=list)


class WholesaleProductPreviewResponse(BaseModel):
    """Non-persisted product projection for authoring previews."""

    model_config = _config()

    validation: WholesaleProductValidationResponse
    buyer_projection: dict[str, Any]
    adapter_projection: dict[str, Any]


class InventorySelectorTypeCapability(BaseModel):
    """Selector type supported by an adapter for wholesale products."""

    model_config = _config()

    selector_type: str
    label: str
    description: str | None = None
    supports_search: bool = True
    supports_parent_filter: bool = False
    option_schema: dict[str, Any] = Field(default_factory=dict)


class CreativeBindingSchema(BaseModel):
    """Format-binding schema advertised by an adapter."""

    model_config = _config()

    selector_type: str | None = None
    schema_: dict[str, Any] = Field(default_factory=dict, alias="schema")


class InventoryAdapterCapabilitiesResponse(BaseModel):
    """Tenant-specific adapter capabilities for wholesale-product authoring."""

    model_config = _config()

    adapter: str
    selector_types: list[InventorySelectorTypeCapability]
    creative_binding_schemas: list[CreativeBindingSchema] = Field(default_factory=list)
    targeting_capabilities: dict[str, Any] = Field(default_factory=dict)
    pricing_capabilities: dict[str, Any] = Field(default_factory=dict)
    optimization_capabilities: dict[str, Any] = Field(default_factory=dict)


class InventorySelectorSummary(BaseModel):
    """One cached ad-server selector candidate."""

    model_config = _config()

    selector_type: str
    external_id: str
    name: str | None = None
    path: list[str] | None = None
    parent_id: str | None = None
    status: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ListInventorySelectorsResponse(BaseModel):
    """Search/list response for ad-server selectors."""

    model_config = _config()

    selectors: list[InventorySelectorSummary]
    count: int
    next_cursor: str | None = None


# ---------------------------------------------------------------------------
# Signal mapping authoring — embedded storefront API
# ---------------------------------------------------------------------------


SignalValueType = Literal["binary", "categorical", "numeric"]


class SignalMappingKindCapability(BaseModel):
    """One adapter mapping kind that can back a buyer-facing signal."""

    model_config = _config()

    mapping_kind: str = Field(..., min_length=1, max_length=80)
    label: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    candidate_type: str | None = Field(default=None, max_length=80)
    supports_search: bool = True
    supports_parent_filter: bool = False
    adapter_config_schema: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "JSON Schema for adapter_config. Mapping kinds may include x-authoring metadata "
            "for generic signal-authoring UI construction."
        ),
    )


class SignalCandidateTypeCapability(BaseModel):
    """One adapter candidate type that a signal-authoring client can browse."""

    model_config = _config()

    candidate_type: str = Field(..., min_length=1, max_length=80)
    label: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    mapping_kind: str | None = Field(default=None, max_length=80)
    directly_mappable: bool = False
    browse_only: bool = False
    parent_candidate_type: str | None = Field(default=None, max_length=80)
    child_candidate_types: list[str] = Field(default_factory=list)
    supports_search: bool = True
    supports_pagination: bool = True
    supports_parent_filter: bool = False


class SignalMappingKindTargetingSemantics(BaseModel):
    """Targeting behavior for one signal mapping kind."""

    model_config = _config()

    mapping_kind: str = Field(..., min_length=1, max_length=80)
    supports_composed: bool = False
    composition_models: list[str] = Field(default_factory=list)
    supported_modes: list[str] = Field(default_factory=list)
    buyer_targeting_fields: list[str] = Field(default_factory=list)
    participates_in_composed_authoring: bool = False
    exclusive_with_other_signals: bool = False
    notes: str | None = None


class SignalTargetingSemantics(BaseModel):
    """Adapter-level signal targeting and composition behavior."""

    model_config = _config()

    supports_composed: bool = False
    composition_models: list[str] = Field(default_factory=list)
    supported_modes: list[str] = Field(default_factory=list)
    buyer_targeting_fields: list[str] = Field(default_factory=list)
    mapping_kinds: list[SignalMappingKindTargetingSemantics] = Field(default_factory=list)


class SignalAdapterCapabilitiesResponse(BaseModel):
    """Tenant-specific adapter capabilities for signal mapping authoring."""

    model_config = _config()

    adapter: str
    supports_signal_mapping_authoring: bool
    mapping_kinds: list[SignalMappingKindCapability] = Field(default_factory=list)
    supported_candidate_types: list[str] = Field(default_factory=list)
    candidate_types: list[SignalCandidateTypeCapability] = Field(default_factory=list)
    default_candidate_type: str | None = None
    targeting_semantics: SignalTargetingSemantics = Field(default_factory=SignalTargetingSemantics)
    value_types: list[str] = Field(default_factory=lambda: ["binary", "categorical", "numeric"])


class SignalCandidateSummary(BaseModel):
    """One synced adapter object that can help create a signal mapping."""

    model_config = _config()

    candidate_type: str
    external_id: str
    name: str | None = None
    parent_id: str | None = None
    path: list[str] | None = None
    mapping_kind: str | None = None
    adapter_config_template: dict[str, Any] | None = None
    default_signal: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ListSignalCandidatesResponse(BaseModel):
    """Search/list response for adapter signal candidates."""

    model_config = _config()

    candidates: list[SignalCandidateSummary]
    count: int
    next_cursor: str | None = None


class SignalMappingRequest(TenantSignalCreate):
    """Create/update body for operator-authored signal mappings."""

    tags: list[str] = Field(default_factory=list)


class SignalMappingResponse(SignalMappingRequest):
    """Signal mapping as persisted and returned to embedder clients."""

    etag: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ListSignalMappingsResponse(BaseModel):
    """List response for signal mappings."""

    model_config = _config()

    signals: list[SignalMappingResponse]
    count: int


class DeleteSignalMappingResponse(BaseModel):
    """Delete response for signal mappings."""

    model_config = _config()

    success: bool
    message: str


class SignalMappingValidationIssue(BaseModel):
    """One validation issue for a signal mapping draft."""

    model_config = _config()

    code: str
    message: str
    field: str | None = None
    severity: Literal["error", "warning"] = "error"


class SignalMappingValidationResponse(BaseModel):
    """Validation result for signal mapping authoring."""

    model_config = _config()

    valid: bool
    issues: list[SignalMappingValidationIssue] = Field(default_factory=list)


class PublisherDomainSummary(BaseModel):
    """Publisher domain known to this tenant."""

    model_config = _config()

    publisher_domain: str
    display_name: str | None = None
    is_verified: bool = False
    sync_status: str | None = None
    total_properties: int | None = None
    authorized_properties: int | None = None


class PublisherPropertySummary(BaseModel):
    """One publisher property available for product mapping."""

    model_config = _config()

    property_id: str
    publisher_domain: str
    property_type: str
    name: str
    identifiers: list[dict[str, Any]] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    verification_status: str | None = None


class AllowedPublisherSelector(BaseModel):
    """Prebuilt selector option for publisher-property mapping UI."""

    model_config = _config()

    publisher_domain: str
    selection_type: Literal["all", "by_id", "by_tag"]
    property_ids: list[str] | None = None
    property_tags: list[str] | None = None
    label: str


class PublisherPropertiesResponse(BaseModel):
    """Publisher-domain/property discovery response."""

    model_config = _config()

    domains: list[PublisherDomainSummary]
    properties: list[PublisherPropertySummary]
    allowed_selectors: list[AllowedPublisherSelector]


class LookupPublisherPropertiesRequest(BaseModel):
    """Resolve and cache one publisher domain's AAO property structure."""

    model_config = _config()

    publisher_domain: str = Field(..., min_length=1, max_length=500)
    force_refresh: bool = False


class PublisherPropertiesLookupResponse(PublisherPropertiesResponse):
    """Domain lookup response plus the synced property mapping surface."""

    model_config = _config()

    publisher_domain: str
    agent_url: str
    is_authorized: bool
    aao_status: Literal["authorized", "unbound", "pending", "no_properties", "unreachable"]
    error: str | None = None
    total_properties: int
    authorized_properties: int
    property_ids: list[str]
    property_tags: list[str]
    sync: dict[str, Any] | None = None


class CreativeFormatSummary(BaseModel):
    """Creative format option exposed to the embedding storefront."""

    model_config = _config()

    format_id: FormatIdRef
    name: str
    dimensions: str | None = None
    asset_types: list[str] = Field(default_factory=list)
    requirements: dict[str, Any] = Field(default_factory=dict)
    raw: dict[str, Any] = Field(default_factory=dict)


class CreativeFormatDiscoveryError(LibraryError):
    """Creative-format discovery error context for authoring clients."""

    model_config = _config()


class ListCreativeFormatsForAuthoringQuery(BaseModel):
    """Filters for creative-format discovery in wholesale-product authoring."""

    model_config = _config()

    q: str | None = Field(default=None, max_length=255)
    asset_type: list[str] = Field(default_factory=list)


class ListCreativeFormatsForAuthoringResponse(BaseModel):
    """Creative-format discovery for wholesale-product authoring."""

    model_config = _config()

    creative_formats: list[CreativeFormatSummary]
    count: int
    errors: list[CreativeFormatDiscoveryError] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Sprint 1.5 — consolidated tenant status (GET /tenants/{tid}/status)
# ---------------------------------------------------------------------------


class StatusAdapterBlock(BaseModel):
    """Adapter block of the tenant status snapshot."""

    model_config = _config()

    type: str
    connected: bool
    last_tested_at: datetime | None = None
    last_test_error: str | None = None


SyncStatus = Literal["success", "failed", "running", "never_run"]
SyncSeverity = Literal["ok", "warning", "critical"]
SyncIssueCategory = Literal["auth", "transient", "permanent", "stale", "unknown"]
SyncIssueAction = Literal["reconnect_adapter", "retry_sync", "wait", "contact_support"]


class StatusSyncIssue(BaseModel):
    """Storefront-safe issue summary for a sync stream."""

    model_config = _config()

    code: str
    category: SyncIssueCategory
    message: str
    retryable: bool
    action: SyncIssueAction


class StatusSyncRunBlock(BaseModel):
    """One sync-run summary inside :class:`StatusSyncsBlock`."""

    model_config = _config()

    last_run_at: datetime | None = None
    status: SyncStatus = "never_run"
    severity: SyncSeverity = "warning"
    last_success_at: datetime | None = None
    issue: StatusSyncIssue | None = None
    item_count: int | None = None
    error: str | None = None


class StatusSyncsBlock(BaseModel):
    """Recent state of each sync category for a tenant."""

    model_config = _config()

    inventory: StatusSyncRunBlock = Field(default_factory=StatusSyncRunBlock)
    custom_targeting: StatusSyncRunBlock = Field(default_factory=StatusSyncRunBlock)
    advertisers: StatusSyncRunBlock = Field(default_factory=StatusSyncRunBlock)
    reporting: StatusSyncRunBlock = Field(default_factory=StatusSyncRunBlock)
    signal_coverage: StatusSyncRunBlock = Field(default_factory=StatusSyncRunBlock)
    pricing_availability: StatusSyncRunBlock = Field(default_factory=StatusSyncRunBlock)


class StatusWorkflowsBlock(BaseModel):
    """Open-workflow summary."""

    model_config = _config()

    open_count: int = 0
    oldest_opened_at: datetime | None = None
    by_kind: dict[str, int] = Field(default_factory=dict)


class StatusMediaBuysBlock(BaseModel):
    """Top-level media-buy counters (a buy contains 1+ packages)."""

    model_config = _config()

    active_count: int = 0
    pending_approval_count: int = 0


class StatusPackagesBlock(BaseModel):
    """Package-level counters (line items inside media buys)."""

    model_config = _config()

    active_count: int = 0
    paused_count: int = 0


class StatusCreativesBlock(BaseModel):
    """Creative-level counters."""

    model_config = _config()

    active_count: int = 0
    pending_review_count: int = 0
    rejected_last_24h_count: int = 0


class StatusProductsBlock(BaseModel):
    """Wholesale product-level counters.

    Distinct from :class:`StatusPackagesBlock` — one product can have
    multiple priced packages, so package counts don't answer "what is
    the publisher actually selling?". Storefront surfaces ``active_count``
    on its homepage as the primary "what's the publisher doing" signal.

    Counts are based on complete, wholesale-owned InventoryProfile rows,
    which are the durable source for the wholesale product feed.
    """

    model_config = _config()

    active_count: int = 0
    draft_count: int = 0
    archived_count: int = 0


class StatusInventoryProfilesBlock(BaseModel):
    """Inventory-profile counters.

    Inventory profiles are the reusable ingredient layer: the ad-server
    selectors, publisher properties, and creative-format constraints that
    wholesale products are composed from. These counts are intentionally
    separate from :class:`StatusProductsBlock`, which counts sellable
    wholesale products.
    """

    model_config = _config()

    total_count: int = 0
    complete_count: int = 0
    incomplete_count: int = 0
    wholesale_owned_count: int = 0


class StatusWebhooksBlock(BaseModel):
    """Outbound-webhook summary. ``None`` until sprint 6 lands the table."""

    model_config = _config()

    last_24h: dict[str, Any] = Field(default_factory=dict)
    last_failure_at: datetime | None = None


# ---------------------------------------------------------------------------
# Sprint 1.8 §7 — setup_tasks block on /status
# ---------------------------------------------------------------------------


SetupTaskSeverity = Literal["blocker", "warning", "info"]
SetupTaskScope = Literal["platform", "publisher"]


class SetupTaskItem(BaseModel):
    """One configuration-completeness item in the status setup_tasks block.

    Severity drives Storefront UI urgency; scope drives routing — Scope3
    escalates ``platform`` items internally (it's the host's job to
    finish provisioning), while ``publisher`` items deep-link into the
    iframe at the matching Settings tab.
    """

    model_config = _config()

    id: str
    name: str
    severity: SetupTaskSeverity
    scope: SetupTaskScope
    description: str
    is_complete: bool
    # Path relative to the tenant root (``/settings#aao``, not
    # ``/tenant/{id}/settings#aao``) so Storefront can compose with
    # whatever iframe prefix it chooses. Null when the task has no
    # configuration UI (rare — most are routed via Settings anchors).
    configure_path: str | None = None


class SetupTasksBlock(BaseModel):
    """Configuration-completeness rollup for ``GET /tenants/{tid}/status``.

    Replaces the separate ``setup_checklist`` round-trip — Storefront
    renders the homepage checklist directly off this block.
    """

    model_config = _config()

    blocker_count: int = 0
    warning_count: int = 0
    items: list[SetupTaskItem] = Field(default_factory=list)


class TenantStatusResponse(BaseModel):
    """``GET /tenants/{tid}/status`` — one round-trip operational snapshot."""

    model_config = _config()

    adapter: StatusAdapterBlock
    syncs: StatusSyncsBlock = Field(default_factory=StatusSyncsBlock)
    workflows: StatusWorkflowsBlock = Field(default_factory=StatusWorkflowsBlock)
    media_buys: StatusMediaBuysBlock = Field(default_factory=StatusMediaBuysBlock)
    packages: StatusPackagesBlock = Field(default_factory=StatusPackagesBlock)
    products: StatusProductsBlock = Field(default_factory=StatusProductsBlock)
    inventory_profiles: StatusInventoryProfilesBlock = Field(default_factory=StatusInventoryProfilesBlock)
    creatives: StatusCreativesBlock = Field(default_factory=StatusCreativesBlock)
    webhooks: StatusWebhooksBlock | None = None
    setup_tasks: SetupTasksBlock = Field(default_factory=SetupTasksBlock)
    fetched_at: datetime


# ---------------------------------------------------------------------------
# Sprint 1.6 — pre-map advertisers (POST/GET /tenants/{tid}/accounts)
# ---------------------------------------------------------------------------


class BrandRef(BaseModel):
    """Compact brand reference. Mirrors AdCP ``BrandReference`` minimally —
    ``domain`` is the natural-key field; ``brand_id`` is an optional
    publisher-side stable id when one brand owns multiple domains."""

    model_config = _config()

    domain: str = Field(..., min_length=1, max_length=255)
    brand_id: str | None = Field(default=None, max_length=255)


class CreateAccountRequest(BaseModel):
    """Pre-map a GAM advertiser to a billing key.

    Upserts by the same natural key ``_sync_accounts_impl`` uses so the
    next ``sync_accounts`` call from a buyer agent finds the row already
    wired and skips ``pending_provision`` entirely.

    Validation:
    - ``billing=agent`` requires ``buyer_agent_principal_id``.
    - ``sandbox=True`` rejects ``gam_advertiser_id`` — sandbox routes to
      the per-tenant sandbox advertiser, not a caller-specified one.
    """

    model_config = _config()

    operator: str = Field(..., min_length=1, max_length=255)
    brand: BrandRef
    billing: Literal["operator", "agent"]
    buyer_agent_principal_id: str | None = Field(default=None, max_length=100)
    sandbox: bool = False

    gam_advertiser_id: str | None = Field(default=None, max_length=64)
    gam_advertiser_name: str | None = Field(default=None, max_length=255)

    name: str | None = Field(default=None, max_length=255)
    payment_terms: Literal["net_15", "net_30", "net_45", "net_60", "net_90", "prepay"] | None = None
    rate_card: str | None = Field(default=None, max_length=255)


class AccountSummary(BaseModel):
    """Compact account view used by list endpoints."""

    model_config = _config()

    account_id: str
    name: str
    status: str
    operator: str | None = None
    brand: dict[str, Any] | None = None
    billing: str | None = None
    sandbox: bool | None = None
    buyer_agent_principal_id: str | None = None
    gam_advertiser_id: str | None = None
    gam_advertiser_name: str | None = None
    advertiser_mapped: bool


class AccountDetail(AccountSummary):
    """Full account view returned from POST/GET-by-id."""

    model_config = _config()

    payment_terms: str | None = None
    rate_card: str | None = None
    created_at: datetime
    updated_at: datetime


class ListAccountsManagedResponse(BaseModel):
    """Response body for ``GET /tenants/{tid}/accounts``."""

    model_config = _config()

    accounts: list[AccountSummary]
    count: int


# ---------------------------------------------------------------------------
# Sprint 1.8 — buyer-advertiser routing rules
# ---------------------------------------------------------------------------


class BuyerAdvertiserMapping(BaseModel):
    """Routing rule on the wire — the public face of an
    ``AdvertiserRoutingRule`` ORM row.

    Vocabulary alignment with Scope3 Storefront UI: external surface uses
    "buyer-advertiser-mapping"; internal storage table is named
    ``advertiser_routing_rules`` because the impl IS a precedence-ordered
    routing chain. One-line mapping at the API boundary.
    """

    model_config = _config()

    id: str
    principal_id: str | None = None
    operator_domain: str
    brand_house: str | None = None
    brand_id: str | None = None
    gam_advertiser_id: str
    created_at: datetime
    updated_at: datetime


class CreateBuyerAdvertiserMappingRequest(BaseModel):
    """``POST /tenants/{tid}/buyer-advertiser-mappings`` body."""

    model_config = _config()

    principal_id: str | None = Field(default=None, max_length=50)
    operator_domain: str = Field(..., min_length=1, max_length=255)
    brand_house: str | None = Field(default=None, max_length=255)
    brand_id: str | None = Field(default=None, max_length=255)
    gam_advertiser_id: str = Field(..., min_length=1, max_length=64)


class UpdateBuyerAdvertiserMappingRequest(BaseModel):
    """``PATCH /tenants/{tid}/buyer-advertiser-mappings/{mid}`` body.

    ``operator_domain`` is intentionally absent — changing it requires
    DELETE + POST so the natural-key uniqueness constraint can't be
    silently violated by a partial-update flow.
    """

    model_config = _config()

    principal_id: str | None = Field(default=None, max_length=50)
    brand_house: str | None = Field(default=None, max_length=255)
    brand_id: str | None = Field(default=None, max_length=255)
    gam_advertiser_id: str | None = Field(default=None, min_length=1, max_length=64)


class ListBuyerAdvertiserMappingsResponse(BaseModel):
    """``GET /tenants/{tid}/buyer-advertiser-mappings`` response."""

    model_config = _config()

    mappings: list[BuyerAdvertiserMapping]
    count: int


# ---------------------------------------------------------------------------
# Sprint 1.8 §4 — recent-buyers rollup
# ---------------------------------------------------------------------------


class RecentBuyer(BaseModel):
    """One distinct ``(operator, brand_house, brand_id)`` triple seen in
    recent traffic, with the GAM advertiser it resolved to and how.

    Powers Storefront's "buyer routing" widget — publishers can see at a
    glance which buyers are landing on the default advertiser
    (``resolved_via=default``) and might want their own bucket, vs.
    which already match a specific routing rule.
    """

    model_config = _config()

    operator_domain: str
    brand_house: str | None = None
    brand_id: str | None = None
    last_seen_at: datetime
    request_count: int
    resolved_gam_advertiser_id: str | None = None
    # ``"account" | "sandbox" | "exact" | "house" | "operator" | "default" | "unknown"``
    # — "unknown" surfaces NULL for legacy Account rows that predate sprint 1.8.
    resolved_via: str


class ListRecentBuyersResponse(BaseModel):
    """``GET /tenants/{tid}/recent-buyers`` response."""

    model_config = _config()

    buyers: list[RecentBuyer]


# ---------------------------------------------------------------------------
# Sprint 1.8 §8 — collapsed refresh endpoint
# ---------------------------------------------------------------------------


class RefreshResponse(BaseModel):
    """``POST /tenants/{tid}/refresh`` response (HTTP 202) — fan-out of sync run ids.

    Storefront reads ``GET /tenants/{tid}/status`` (``syncs`` block) for
    progress per sync type. Re-POST within 60 seconds returns the SAME
    ids (idempotent — avoids hammering GAM when a publisher mashes the
    button).
    """

    model_config = _config()

    sync_run_ids: dict[str, str] = Field(default_factory=dict)
    started_at: datetime


class RefreshConflictResponse(BaseModel):
    """``POST /tenants/{tid}/refresh`` response (HTTP 409) — sync already running.

    Mirrors the 202 :class:`RefreshResponse` shape (``sync_run_ids`` +
    ``started_at`` at the top level so receivers don't need a second
    parse path) plus a structured error block. Issue #463: the
    storefront's "Retry" UI needs a clear signal that the click
    triggered nothing new — the indistinguishable 202-with-reused-ids
    response is the problem this shape fixes.
    """

    model_config = _config()

    error: str = "sync_already_running"
    message: str
    sync_run_ids: dict[str, str] = Field(default_factory=dict)
    started_at: datetime
    running_sync_types: list[str] = Field(default_factory=list)


class TargetingValuesRefreshResponse(BaseModel):
    """``POST /tenants/{tid}/targeting/values/{key_id}/refresh`` response."""

    model_config = _config()

    key_id: str
    synced: int


# ---------------------------------------------------------------------------
# Sprint 5 piece D — GAM advertisers cache
# ---------------------------------------------------------------------------


class GamAdvertiser(BaseModel):
    """One row from the synced ``gam_advertisers`` cache, on the wire.

    Source of truth is GAM's ``CompanyService.getCompaniesByStatement
    WHERE type = 'ADVERTISER'``; this projection is what the Buyer
    Routing UI's picker renders.
    """

    model_config = _config()

    id: str
    name: str
    currency_code: str | None = None
    status: str


class ListGamAdvertisersResponse(BaseModel):
    """``GET /tenants/{tid}/gam/advertisers`` response.

    Cursor pagination with opaque base64-encoded offset (same shape as
    other list endpoints — Storefront treats it as a sealed token).

    ``synced_at`` reports the most-recent ``gam_advertisers.synced_at``
    for the tenant so the picker can show "Last synced 5 minutes ago".
    NULL when no advertisers have ever synced.
    """

    model_config = _config()

    advertisers: list[GamAdvertiser]
    next_cursor: str | None = None
    synced_at: datetime | None = None


class EnsureGamAdvertiserRequest(BaseModel):
    """``POST /tenants/{tid}/gam/advertisers:ensure`` body."""

    model_config = _config()

    name: str = Field(..., min_length=1, max_length=255)
    dry_run: bool = False


class EnsureGamAdvertiserResponse(BaseModel):
    """Idempotent advertiser ensure response.

    ``created`` is true only when the endpoint actually created a GAM company.
    If the advertiser already existed in cache or GAM, ``created`` is false.
    """

    model_config = _config()

    advertiser: GamAdvertiser
    created: bool
    dry_run: bool = False


# ---------------------------------------------------------------------------
# Sprint 3 — workflow approve/reject + read drill-downs
# ---------------------------------------------------------------------------


WorkflowStatus = Literal["pending", "approved", "rejected", "cancelled", "expired"]
WorkflowDecisionAction = Literal["approve", "reject"]
WorkflowDecisionSource = Literal["scope3_storefront", "salesagent_ui", "management_api"]


class WorkflowDecision(BaseModel):
    """One approve/reject event recorded against a workflow."""

    model_config = _config()

    decided_at: datetime
    decision: WorkflowDecisionAction
    decided_by_email: str | None = None
    decided_by_source: str
    notes: str | None = None


class WorkflowSummary(BaseModel):
    """Compact workflow entry for ``GET /workflows`` listings.

    ``subject_type``/``subject_id`` are the object the workflow is gating
    (e.g. ``media_buy``/``mb_1234``). ``workflow_type`` is the human-readable
    kind (``media_buy_approval``, ``creative_approval``, etc.) — sourced from
    the WorkflowStep's ``tool_name`` falling back to ``step_type``.
    """

    model_config = _config()

    workflow_id: str
    workflow_type: str
    status: WorkflowStatus
    subject_type: str
    subject_id: str
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None = None
    requested_by_principal_id: str | None = None
    requested_by_principal_name: str | None = None


class WorkflowDetail(WorkflowSummary):
    """Full workflow detail. Adds description, full request context, and
    the decisions audit trail."""

    model_config = _config()

    description: str = ""
    context: dict[str, Any] = Field(default_factory=dict)
    decisions: list[WorkflowDecision] = Field(default_factory=list)


class ListWorkflowsResponse(BaseModel):
    """``GET /tenants/{tid}/workflows`` response with cursor pagination."""

    model_config = _config()

    workflows: list[WorkflowSummary]
    count: int
    next_cursor: str | None = None


class ApproveWorkflowRequest(BaseModel):
    """``POST /workflows/{wid}/approve`` body. Notes are optional on approve."""

    model_config = _config()

    notes: str | None = None


class RejectWorkflowRequest(BaseModel):
    """``POST /workflows/{wid}/reject`` body. Notes are required on reject —
    every rejection has to carry a reason for the audit trail."""

    model_config = _config()

    notes: str = Field(..., min_length=1)


# ---------------------------------------------------------------------------
# Sprint 3 — media-buy read endpoints (no write methods exposed)
# ---------------------------------------------------------------------------


MediaBuyStatus = Literal[
    "pending_approval",
    "active",
    "paused",
    "completed",
    "cancelled",
    "failed",
    # Other statuses present in the database surface as-is (e.g. "draft",
    # "approved", "live"). Keep the literal list aligned with the values
    # the existing buyer protocol writes.
    "draft",
    "approved",
    "live",
    "running",
    "submitted",
    "pending",
]
PacingState = Literal["on_pace", "underpacing", "overpacing"]


class MediaBuySummary(BaseModel):
    """Compact media-buy entry for ``GET /media-buys`` listings.

    ``pacing`` is computed (delivered ÷ expected-by-now); ``None`` when the
    buy hasn't started yet or delivery data is unavailable.
    """

    model_config = _config()

    media_buy_id: str
    buyer_ref: str | None = None
    principal_id: str
    principal_name: str
    status: str
    flight_start_date: date
    flight_end_date: date
    total_budget: Decimal
    currency: str
    delivered_impressions: int | None = None
    delivered_spend: Decimal | None = None
    pacing: PacingState | None = None
    created_at: datetime


class StatusEvent(BaseModel):
    """One status-change entry in a media buy's history."""

    model_config = _config()

    occurred_at: datetime
    status: str
    note: str | None = None


class MediaBuyDetail(MediaBuySummary):
    """Full media-buy detail including products, targeting, creatives, and
    status history."""

    model_config = _config()

    products: list[str] = Field(default_factory=list)
    targeting: dict[str, Any] | None = None
    creatives: list[str] = Field(default_factory=list)
    status_history: list[StatusEvent] = Field(default_factory=list)


class ListMediaBuysResponse(BaseModel):
    """``GET /tenants/{tid}/media-buys`` response with cursor pagination."""

    model_config = _config()

    media_buys: list[MediaBuySummary]
    count: int
    next_cursor: str | None = None


# ---------------------------------------------------------------------------
# Sprint 3 — audit log read endpoint
# ---------------------------------------------------------------------------


AuditActorType = Literal["user", "system", "management_api", "super_admin", "buyer_agent"]


class AuditLogEntry(BaseModel):
    """One audit-log entry on the wire."""

    model_config = _config()

    audit_log_id: str
    occurred_at: datetime
    action: str
    subject_type: str
    subject_id: str
    actor_type: AuditActorType
    actor_email: str | None = None
    external_user_email: str | None = None
    external_user_id: str | None = None
    external_org_id: str | None = None
    external_source: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class ListAuditLogResponse(BaseModel):
    """``GET /tenants/{tid}/audit-log`` response with cursor pagination."""

    model_config = _config()

    entries: list[AuditLogEntry]
    count: int
    next_cursor: str | None = None


# ---------------------------------------------------------------------------
# Sprint 3 — sync history read endpoint
# ---------------------------------------------------------------------------


SyncRunStatus = Literal["success", "failed", "in_progress", "cancelled"]
SyncRunType = Literal["inventory", "custom_targeting", "advertisers"]


class SyncRunInfo(BaseModel):
    """One sync-run entry in the historical timeline."""

    model_config = _config()

    sync_id: str
    sync_type: str
    started_at: datetime
    completed_at: datetime | None = None
    status: str
    duration_seconds: int | None = None
    items_processed: int = 0
    items_failed: int = 0
    error_summary: str | None = None


class ListSyncHistoryResponse(BaseModel):
    """``GET /tenants/{tid}/sync-history`` response with cursor pagination."""

    model_config = _config()

    runs: list[SyncRunInfo]
    count: int
    next_cursor: str | None = None


# ---------------------------------------------------------------------------
# Sprint 6 — outbound webhook subscription endpoints
# ---------------------------------------------------------------------------


# The supported event taxonomy. Receivers MAY subscribe to a subset by listing
# specific values, or to ALL events by passing an empty list. The taxonomy
# matches the events the salesagent publishes; rejections at create time are
# noisier than silently accepting an unknown type.
WEBHOOK_EVENT_TYPES: tuple[str, ...] = (
    "workflow.created",
    "workflow.decided",
    "media_buy.created",
    "media_buy.status_changed",
    "creative.created",
    "creative.status_changed",
    "principal.created",
    *TENANT_MANAGEMENT_CATALOG_EVENT_TYPES,
    # ``sync_run`` (not ``sync``) — the noun is the persistent SyncJob row,
    # the verb-past pattern is ``<entity>.<verb-past>`` consistent with the
    # rest of the catalog. The payload's ``data.sync_run_id`` matches.
    "sync_run.completed",
    "sync_run.failed",
    # Derived storefront alerting event. Emitted on committed sync-run
    # transitions when public health severity changes for a tenant sync
    # stream; raw run events remain immutable for correlation and admin
    # drill-downs.
    "sync_health.changed",
    "tenant.config_changed",
)

WebhookEventType = Annotated[str, Field(json_schema_extra={"enum": list(WEBHOOK_EVENT_TYPES)})]


class CreateWebhookSubscriptionRequest(BaseModel):
    """``POST /tenants/{tid}/webhooks`` body.

    ``event_types``: subset of :data:`WEBHOOK_EVENT_TYPES`; empty list means
    "all events". ``secret`` may be omitted, in which case the server
    generates one and returns it in the create response (exactly once).
    """

    model_config = _config()

    url: str = Field(..., min_length=1)
    event_types: list[WebhookEventType] = Field(default_factory=list)
    description: str | None = None
    extra_headers: dict[str, str] | None = None
    secret: str | None = Field(default=None, min_length=32)

    @field_validator("url")
    @classmethod
    def _https_only(cls, value: str) -> str:
        # Spec section "Security": HTTPS-only enforcement at create time.
        # The local-dev exception lives in the route handler so this schema
        # stays purely declarative.
        stripped = value.strip()
        if not stripped:
            raise ValueError("url must be non-empty")
        return stripped


class WebhookSubscriptionSummary(BaseModel):
    """Subscription record without the secret. Used for list + get + delete responses."""

    model_config = _config()

    webhook_id: str
    url: str
    event_types: list[str]
    description: str | None = None
    extra_headers: dict[str, str] | None = None
    is_active: bool
    consecutive_failures: int = 0
    last_delivery_at: datetime | None = None
    last_delivery_status: int | None = None
    created_at: datetime


class WebhookSubscriptionCreatedResponse(WebhookSubscriptionSummary):
    """Create-only response carrying the plaintext secret.

    The ``secret`` field is returned exactly once at create time; subsequent
    GETs omit it. Loss of the plaintext requires re-registering the webhook.
    """

    model_config = _config()

    secret: SecretStr


class ListWebhooksResponse(BaseModel):
    """``GET /tenants/{tid}/webhooks`` response.

    Subscriptions are returned without secrets (use the create response if
    you still have it; otherwise re-register).
    """

    model_config = _config()

    webhooks: list[WebhookSubscriptionSummary]
    count: int


class WebhookTestDeliveryResult(BaseModel):
    """One synthetic event delivery result included in :class:`WebhookTestResponse`."""

    model_config = _config()

    event_type: str
    event_id: str
    delivered: bool
    response_status: int | None = None
    latency_ms: int | None = None
    error: str | None = None


class WebhookTestResponse(BaseModel):
    """``POST /tenants/{tid}/webhooks/{wid}/test`` response.

    Returns synthetic delivery results (one per registered event type) so
    operators can verify the receiver works for every event they subscribe to.
    """

    model_config = _config()

    delivered: bool
    results: list[WebhookTestDeliveryResult]


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ApiError(BaseModel):
    """Standard problem-detail shape used by every Tenant Management endpoint."""

    model_config = _config()

    error: str
    message: str
    details: dict[str, Any] | None = None
