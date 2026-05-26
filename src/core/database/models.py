"""SQLAlchemy models for database schema."""

import json
import logging
from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from adcp.types import BrandReference, CreditLimit, Setup

# For storage we want the account response/core shape (URL-only), not the
# sync_governance request shape where ``authentication`` is required. Sellers
# don't always have credentials at the time the Account row is created.
from adcp.types.generated_poc.core.account import (
    GovernanceAgent,
)
from sqlalchemy import (
    DECIMAL,
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from src.core.database.json_type import JSONType
from src.core.json_validators import JSONValidatorMixin
from src.core.utils.encryption import SECRET_CIPHERTEXT_PREFIX

# Minimal-but-spec-valid baseline for ``Product.reporting_capabilities`` per
# adcp 4.4. The same value is used as both the SQL ``server_default`` (for
# raw INSERTs) and the Python-side ``default`` (for in-memory ORM
# construction); deriving the SQL default from this dict via ``json.dumps``
# guarantees the two paths cannot drift.
PRODUCT_REPORTING_CAPABILITIES_DEFAULT: dict = {
    "available_reporting_frequencies": ["daily"],
    "expected_delay_minutes": 0,
    "timezone": "UTC",
    "supports_webhooks": False,
    "available_metrics": ["impressions"],
    "date_range_support": "date_range",
}

logger = logging.getLogger(__name__)


def _decrypt_optional_secret(
    stored_value: str | None,
    *,
    secret_name: str,
    owner_id: str,
    allow_legacy_plaintext: bool = False,
) -> str | None:
    """Decrypt a Fernet-backed optional secret column.

    ``allow_legacy_plaintext`` is for columns that were historically stored in
    plaintext and may still contain old rows until the backfill migration runs.
    """
    if not stored_value:
        return None

    from src.core.exceptions import AdCPConfigurationError
    from src.core.utils.encryption import decrypt_api_key

    try:
        has_prefix = stored_value.startswith(SECRET_CIPHERTEXT_PREFIX)
        if allow_legacy_plaintext and not has_prefix:
            return stored_value
        ciphertext = stored_value.removeprefix(SECRET_CIPHERTEXT_PREFIX)
        return decrypt_api_key(ciphertext)
    except ValueError as exc:
        raise AdCPConfigurationError(f"Failed to decrypt {secret_name} for {owner_id}") from exc


def _encrypt_optional_secret(value: str | None) -> str | None:
    """Encrypt an optional secret value for storage."""
    if not value:
        return None

    from src.core.utils.encryption import encrypt_api_key

    return f"{SECRET_CIPHERTEXT_PREFIX}{encrypt_api_key(value)}"


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models using SQLAlchemy 2.0 declarative style."""

    pass


class Tenant(Base, JSONValidatorMixin):
    __tablename__ = "tenants"

    tenant_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    subdomain: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    virtual_host: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    billing_plan: Mapped[str] = mapped_column(String(50), default="standard")
    billing_contact: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # New columns from migration
    ad_server: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # NOTE: currency_code, max_daily_budget, min_product_spend moved to currency_limits table
    enable_axe_signals: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    authorized_emails: Mapped[list[str] | None] = mapped_column(JSONType, nullable=True)
    authorized_domains: Mapped[list[str] | None] = mapped_column(JSONType, nullable=True)
    slack_webhook_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    slack_audit_webhook_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    hitl_webhook_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    admin_token: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # List of format ID strings (just the id part, not full FormatId objects)
    # Validated at database level via CHECK constraint (see migration: rename_formats_to_format_ids)
    auto_approve_format_ids: Mapped[list[str] | None] = mapped_column(JSONType, nullable=True)
    human_review_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    policy_settings: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    supported_billing: Mapped[list[str] | None] = mapped_column(JSONType, nullable=True)  # BR-RULE-059
    account_approval_mode: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )  # BR-RULE-060: auto|credit_review|legal_review
    signals_agent_config: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    creative_review_criteria: Mapped[str | None] = mapped_column(Text, nullable=True)
    _gemini_api_key: Mapped[str | None] = mapped_column("gemini_api_key", String(500), nullable=True)
    approval_mode: Mapped[str] = mapped_column(String(50), nullable=False, default="require-human")
    creative_auto_approve_threshold: Mapped[float] = mapped_column(Float, nullable=False, server_default="0.9")
    creative_auto_reject_threshold: Mapped[float] = mapped_column(Float, nullable=False, server_default="0.1")
    ai_policy: Mapped[dict | None] = mapped_column(
        JSONType, nullable=True, comment="AI review policy configuration with confidence thresholds"
    )
    advertising_policy: Mapped[dict | None] = mapped_column(
        JSONType,
        nullable=True,
        comment="Advertising policy configuration with prohibited categories, tactics, and advertisers",
    )

    # Pydantic AI configuration for multi-model support
    # Structure: {"provider": "anthropic", "model": "claude-sonnet-4-20250514", "api_key": "encrypted:...", ...}
    ai_config: Mapped[dict | None] = mapped_column(
        JSONType,
        nullable=True,
        comment="Pydantic AI configuration: provider, model, api_key (encrypted), logfire_token, settings",
    )

    # Naming templates (business rules - shared across all adapters)
    order_name_template: Mapped[str | None] = mapped_column(
        String(500), nullable=True, server_default="{campaign_name|brand_name} - {media_buy_id} - {date_range}"
    )
    line_item_name_template: Mapped[str | None] = mapped_column(
        String(500), nullable=True, server_default="{order_name} - {product_name}"
    )
    # When False, skip the AI naming agent even if {auto_name} is in the
    # template — caller falls back to the brand name. Lets tenants without a
    # Gemini key opt out without rewriting templates.
    auto_naming_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    # When True, the delivery webhook scheduler also sends heartbeat reports
    # for buys in pending_start (warm-up window) and paused — not just active
    # and completed. Lets buyers stop polling for "did my flight start yet?"
    # and "is the pause still in effect?". Default True. See issue #48.
    report_pre_start_buys: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    # Measurement providers configuration
    # Structure: {"providers": ["Provider 1", "Provider 2"], "default": "Provider 1"}
    measurement_providers: Mapped[dict | None] = mapped_column(JSONType, nullable=True)

    # Brand manifest policy - controls product discovery access
    # Values: "require_auth" (standard B2B - signup to see pricing), "require_brand" (brand context required), "public" (visible to all)
    brand_manifest_policy: Mapped[str] = mapped_column(String(50), nullable=False, server_default="require_auth")

    # Auth setup mode - when True, test credentials work; when False, only SSO works
    # New tenants start in setup mode until SSO is configured and tested
    auth_setup_mode: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    # Product ranking prompt - optional AI prompt for ranking products based on brief
    # When set, get_products will use AI to rank and filter products
    product_ranking_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Favicon URL - custom favicon for the tenant's admin UI
    # Can be an absolute URL or a path to an uploaded file (e.g., /static/favicons/tenant_id/favicon.ico)
    favicon_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Embedded mode: platform-managed surfaces are locked to the Tenant Management API.
    # When True, the model-layer write guard (embedded_tenant_guard) blocks non-API mutations
    # to platform-managed columns/tables (Tenant core fields, AdapterConfig). Publisher-managed
    # tables (Product, Principal, Creative, etc.) remain writable from the UI regardless.
    is_embedded: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
    )
    # Identifier for the tenant in the upstream platform (e.g. Scope3 org id).
    # Indexed but not unique — a single org may own multiple tenants in the future.
    external_org_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Name of the upstream platform that owns this managed tenant ("scope3", etc.).
    external_source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # When True, _create_media_buy_impl auto-provisions GAM advertisers for
    # Accounts in pending_provision (calls CompanyService.createCompanies on
    # first buy). When False, returns ACCOUNT_NOT_PROVISIONED so publishers
    # map manually via the Admin UI / Tenant Management API.
    # Default False keeps today's open-instance behavior intact; embedded-mode
    # provisioning sets True per-tenant.
    auto_provision_advertisers: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
    )
    # public_agent_url is what publishers list in their adagents.json to
    # authorize this tenant's agent. Embedded-mode tenants share one
    # (https://interchange.io); self-hosted publishers use their own salesagent.
    public_agent_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Absolute URL of the operator's brand.json (typically
    # ``https://{operator_domain}/.well-known/brand.json``). Surfaced on
    # ``get_adcp_capabilities → identity.brand_json_url`` so receivers verifying
    # our outbound signatures can fetch our operator-side keys.
    # See docs/design/signing-non-embedded.md.
    brand_json_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    # Buyer-advertiser routing.
    # Required-before-activation fallback advertiser. Buys whose
    # (operator_domain, brand_house, brand_id) triple doesn't match a
    # routing rule fall through to this advertiser; if NULL, the routing
    # chain raises TENANT_NOT_ACTIVATED (Q3: implicit activation —
    # buyer-protocol error IS the contract).
    default_gam_advertiser_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Per-tenant sync cadence override (minutes). NULL = use the cron's
    # default 6h. sync_all_tenants.py branches on this when picking
    # tenants per run.
    sync_cadence_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Embed-mode breadcrumb root override. Shape: ``{"label": str, "url": str}``.
    # Only meaningful when ``is_embedded`` is True — open-instance tenants ignore
    # the value. Replaces the default first crumb ("Dashboard") with the
    # upstream host's storefront entry point so embedded breadcrumb trails feel
    # native to the host.
    embed_breadcrumb_root: Mapped[dict | None] = mapped_column(JSONType, nullable=True)

    # Per-tenant gate (#145). When True, creatives at status='pending_review'
    # are held back from the ad-server upload until a human flips them to
    # 'approved' — closing the auto-create-path window where the local
    # pending_review flag was informational only. Default False preserves
    # today's behavior byte-for-byte.
    creative_pre_approval_gate_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    # Relationships
    products = relationship("Product", back_populates="tenant", cascade="all, delete-orphan")
    principals = relationship("Principal", back_populates="tenant", cascade="all, delete-orphan")
    users = relationship("User", back_populates="tenant", cascade="all, delete-orphan")
    accounts = relationship("Account", back_populates="tenant", cascade="all, delete-orphan")
    media_buys = relationship("MediaBuy", back_populates="tenant", cascade="all, delete-orphan", overlaps="media_buys")
    # tasks table removed - replaced by workflow_steps
    audit_logs = relationship("AuditLog", back_populates="tenant", cascade="all, delete-orphan")
    strategies = relationship("Strategy", back_populates="tenant", cascade="all, delete-orphan", overlaps="strategies")
    currency_limits = relationship("CurrencyLimit", back_populates="tenant", cascade="all, delete-orphan")
    adapter_config = relationship(
        "AdapterConfig",
        back_populates="tenant",
        uselist=False,
        cascade="all, delete-orphan",
    )
    creative_agents = relationship(
        "CreativeAgent",
        back_populates="tenant",
        cascade="all, delete-orphan",
    )
    signals_agents = relationship(
        "SignalsAgent",
        back_populates="tenant",
        cascade="all, delete-orphan",
    )
    auth_config = relationship(
        "TenantAuthConfig",
        back_populates="tenant",
        uselist=False,
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        CheckConstraint("position(':' in tenant_id) = 0", name="ck_tenants_tenant_id_no_colon"),
        Index("idx_subdomain", "subdomain"),
        Index("ix_tenants_virtual_host", "virtual_host", unique=True),
        Index("ix_tenants_external_org_id", "external_org_id"),
    )

    # JSON validators are inherited from JSONValidatorMixin
    # No need for duplicate validators here

    @property
    def gemini_api_key(self) -> str | None:
        """Get decrypted Gemini API key."""
        return _decrypt_optional_secret(
            self._gemini_api_key,
            secret_name="Gemini API key",
            owner_id=f"tenant {self.tenant_id}",
        )

    @gemini_api_key.setter
    def gemini_api_key(self, value: str | None) -> None:
        """Set encrypted Gemini API key."""
        self._gemini_api_key = _encrypt_optional_secret(value)

    @property
    def primary_domain(self) -> str | None:
        """Get primary domain for this tenant (virtual_host or subdomain-based)."""
        return self.virtual_host or (f"{self.subdomain}.example.com" if self.subdomain else None)

    @property
    def is_gam_tenant(self) -> bool:
        """Check if this tenant is using Google Ad Manager adapter.

        Checks both legacy ad_server field and current adapter_config.adapter_type.
        This is the single source of truth for GAM tenant detection.

        Returns:
            bool: True if tenant is using GAM, False otherwise
        """
        # Check legacy ad_server field
        if self.ad_server == "google_ad_manager":
            return True

        # Check adapter_config relationship
        if self.adapter_config and self.adapter_config.adapter_type == "google_ad_manager":
            return True

        return False


# CreativeFormat model removed - table dropped in migration f2addf453200 (Oct 13, 2025)
# Creative formats are now fetched from creative agents via AdCP protocol
# Historical note: Previously stored format definitions locally, now use AdCP list_creative_formats


class Product(Base, JSONValidatorMixin):
    __tablename__ = "products"

    tenant_id: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"),
        primary_key=True,
    )
    product_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Type hint: list of FormatId dicts with {agent_url: str, id: str}
    # Validated at database level via CHECK constraint (see migration: rename_formats_to_format_ids)
    format_ids: Mapped[list[dict[str, str]]] = mapped_column(JSONType, nullable=False)
    # Type hint: targeting template dict structure
    targeting_template: Mapped[dict] = mapped_column(JSONType, nullable=False)
    delivery_type: Mapped[str] = mapped_column(String(50), nullable=False)

    # Other fields
    # Type hint: measurement dict (AdCP measurement object)
    measurement: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    # Type hint: creative policy dict (AdCP creative policy object)
    creative_policy: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    # Type hint: price guidance dict (legacy field)
    price_guidance: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    is_custom: Mapped[bool] = mapped_column(Boolean, default=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Type hint: countries list
    countries: Mapped[list[str] | None] = mapped_column(JSONType, nullable=True)
    # Advertising channels (e.g., ["display", "video", "native"])
    channels: Mapped[list[str] | None] = mapped_column(JSONType, nullable=True)
    # Type hint: implementation config dict
    implementation_config: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    # AdCP property authorization fields (at least one required per spec)
    # XOR constraint: exactly one of (properties, property_ids, property_tags) must be set
    # Type hint: list of Property dicts for validation (legacy, full objects)
    properties: Mapped[list[dict] | None] = mapped_column(JSONType, nullable=True)
    # Type hint: list of property ID strings (AdCP 2.0.0 by_id variant)
    property_ids: Mapped[list[str] | None] = mapped_column(JSONType, nullable=True)
    # Type hint: list of tag strings (AdCP 2.0.0 by_tag variant)
    property_tags: Mapped[list[str] | None] = mapped_column(JSONType, nullable=True)
    # Note: PR #79 fields (estimated_exposures, floor_cpm, recommended_cpm) are NOT stored in database
    # They are calculated dynamically from product_performance_metrics table

    # Inventory profile reference (optional)
    # If set, product uses inventory profile configuration instead of custom config
    inventory_profile_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("inventory_profiles.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Product detail fields (AdCP v1 spec compliance)
    # Type hint: delivery measurement dict with provider (required) and notes (optional)
    delivery_measurement: Mapped[dict] = mapped_column(
        JSONType,
        nullable=False,
        server_default=text('\'{"provider": "publisher"}\'::jsonb'),
    )
    # Type hint: product card dict with format_id and manifest
    product_card: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    # Type hint: detailed product card dict with format_id and manifest
    product_card_detailed: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    # Type hint: list of placement dicts (each with placement_id, name, description, format_ids)
    placements: Mapped[list[dict] | None] = mapped_column(JSONType, nullable=True)
    # Type hint: reporting capabilities dict (AdCP 4.4: required on wire)
    # ``default`` populates the attribute on Python-side construction (in-memory
    # models, factory helpers); ``server_default`` covers DB INSERTs that bypass
    # the ORM (raw SQL, legacy clients). Both derive from the same dict
    # constant so they cannot drift.
    reporting_capabilities: Mapped[dict] = mapped_column(
        JSONType,
        nullable=False,
        default=lambda: dict(PRODUCT_REPORTING_CAPABILITIES_DEFAULT),
        server_default=text(f"'{json.dumps(PRODUCT_REPORTING_CAPABILITIES_DEFAULT)}'::jsonb"),
    )

    # AdCP 3.6.0 product fields
    property_targeting_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    signal_targeting_allowed: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=False)
    # Type hint: CatalogMatch object (matching criteria for product catalogs)
    catalog_match: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    # Type hint: list of CatalogType enum values
    catalog_types: Mapped[list | None] = mapped_column(JSONType, nullable=True)
    # Type hint: ConversionTracking object (conversion measurement config)
    conversion_tracking: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    # Type hint: list of DataProviderSignalSelector objects
    data_provider_signals: Mapped[list | None] = mapped_column(JSONType, nullable=True)
    # Type hint: list of SignalListing objects bundled into this product
    included_signals: Mapped[list | None] = mapped_column(JSONType, nullable=True)
    # Type hint: SignalTargetingRules object for product-level signal composition
    signal_targeting_rules: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    # Type hint: list of ProductSignalTargetingOption objects selectable on this product
    signal_targeting_options: Mapped[list | None] = mapped_column(JSONType, nullable=True)
    # Type hint: DeliveryForecast object (delivery predictions)
    forecast: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    # Type hint: list of ProductAction enum values allowed for this product
    allowed_actions: Mapped[list | None] = mapped_column(JSONType, nullable=True)
    # Type hint: list of FormatOption objects advertised by the product
    format_options: Mapped[list | None] = mapped_column(JSONType, nullable=True)
    # Type hint: vendor metric optimization metadata
    vendor_metric_optimization: Mapped[dict | None] = mapped_column(JSONType, nullable=True)

    # Dynamic product fields
    # Type hint: whether this product is a dynamic template that generates variants
    is_dynamic: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Type hint: whether this product is a variant generated from a dynamic template
    is_dynamic_variant: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Type hint: product_id of parent template (for variants only)
    parent_product_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Type hint: array of signals agent IDs to query for this dynamic product
    signals_agent_ids: Mapped[list[str] | None] = mapped_column(JSONType, nullable=True)
    # Type hint: template string for variant name generation (macros: {{name}}, {{signal.name}}, etc.)
    variant_name_template: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Type hint: template string for variant description generation (macros: {{description}}, {{signal.name}}, etc.)
    variant_description_template: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Type hint: maximum number of signal variants to create from this template
    max_signals: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    # Type hint: activation key from signal (key/value pair for targeting)
    activation_key: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    # Type hint: full signal metadata from signals agent response
    signal_metadata: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    # Type hint: when variants were last synced from signals agent
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Type hint: when variant was archived (soft delete)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Type hint: days until variant expires (null = use tenant default)
    variant_ttl_days: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Principal access control
    # Type hint: list of principal IDs that can see this product
    # NULL or empty means visible to all principals (default)
    allowed_principal_ids: Mapped[list[str] | None] = mapped_column(JSONType, nullable=True)

    # Relationships
    tenant = relationship("Tenant", back_populates="products")
    inventory_profile = relationship("InventoryProfile", back_populates="products")
    # No SQLAlchemy cascade - let database CASCADE handle pricing_options deletion
    # This avoids triggering the prevent_empty_pricing_options constraint
    # Use passive_deletes=True to tell SQLAlchemy to rely on database CASCADE
    pricing_options = relationship("PricingOption", back_populates="product", passive_deletes=True)

    # Effective properties - auto-resolve from inventory profile if set
    @property
    def effective_format_ids(self) -> list[dict[str, str]]:
        """Get format_ids from inventory profile (if set) or product itself.

        Returns format_ids as list of FormatId dicts: [{"agent_url": str, "id": str}, ...]
        When inventory_profile_id is set, returns current profile's format_ids (auto-updates).
        When inventory_profile_id is null, returns product's own format_ids.

        Database validation ensures all format_ids match AdCP FormatId spec.
        """
        if self.inventory_profile_id and self.inventory_profile:
            return self.inventory_profile.format_ids
        return self.format_ids

    @property
    def effective_properties(self) -> list[dict] | None:
        """Get publisher properties from inventory profile (if set) or product itself.

        Returns properties in AdCP 2.0.0 discriminated union format:
        - all variant: {publisher_domain, selection_type='all'} (default)
        - by_id variant: {publisher_domain, property_ids, selection_type='by_id'}
        - by_tag variant: {publisher_domain, property_tags, selection_type='by_tag'}
        - legacy: Full Property objects (for backward compatibility)

        When inventory_profile_id is set, returns current profile's properties (auto-updates).
        When inventory_profile_id is null, converts product's authorization to AdCP format.

        If no properties/property_ids/property_tags are set, defaults to "all" variant
        (all properties from this publisher).
        """
        from src.core.helpers.publisher_property_helpers import ensure_selection_type

        if self.inventory_profile_id and self.inventory_profile:
            return ensure_selection_type(self.inventory_profile.publisher_properties)

        # Convert product's authorization to AdCP publisher_properties format
        if self.properties:
            return ensure_selection_type(self.properties)
        elif self.property_ids:
            # AdCP 2.0.0 by_id variant
            # Get publisher_domain from tenant (use subdomain or virtual_host)
            if hasattr(self, "tenant") and self.tenant:
                publisher_domain = self.tenant.virtual_host or f"{self.tenant.subdomain}.example.com"
            else:
                publisher_domain = "unknown"
            return [
                {"publisher_domain": publisher_domain, "property_ids": self.property_ids, "selection_type": "by_id"}
            ]
        elif self.property_tags:
            # AdCP 2.0.0 by_tag variant
            # Get publisher_domain from tenant (use subdomain or virtual_host)
            if hasattr(self, "tenant") and self.tenant:
                publisher_domain = self.tenant.virtual_host or f"{self.tenant.subdomain}.example.com"
            else:
                publisher_domain = "unknown"
            return [
                {"publisher_domain": publisher_domain, "property_tags": self.property_tags, "selection_type": "by_tag"}
            ]

        # Default: Use "all" variant (all properties from this publisher)
        # This ensures products always have publisher_properties as required by AdCP spec
        if hasattr(self, "tenant") and self.tenant:
            publisher_domain = self.tenant.virtual_host or f"{self.tenant.subdomain}.example.com"
        else:
            publisher_domain = "unknown"
        return [{"publisher_domain": publisher_domain, "selection_type": "all"}]

    @property
    def effective_property_tags(self) -> list[str] | None:
        """Get property tags from inventory profile (if set) or product itself.

        Returns property_tags array (list of tag strings).
        When inventory_profile_id is set, derives tags from profile's properties (auto-updates).
        When inventory_profile_id is null, returns product's own property_tags (legacy).
        """
        if self.inventory_profile_id and self.inventory_profile:
            # For profile-based products, we use properties not tags
            # Return None to indicate properties should be used instead
            return None
        return self.property_tags

    @property
    def effective_implementation_config(self) -> dict:
        """Get GAM implementation config from inventory profile (if set) or product itself.

        Returns implementation_config dict with GAM-specific settings.
        When inventory_profile_id is set, overlays profile inventory onto product config (auto-updates).
        When inventory_profile_id is null, returns product's own config (legacy).

        Key fields for GAM adapter:
        - targeted_ad_unit_ids: List of GAM ad unit IDs
        - targeted_placement_ids: List of GAM placement IDs
        - include_descendants: Whether to include child ad units
        """
        config = dict(self.implementation_config or {})
        if self.inventory_profile_id and self.inventory_profile:
            profile = self.inventory_profile
            inventory_config = profile.inventory_config or {}
            ad_units = inventory_config.get("ad_units") or []
            placements = inventory_config.get("placements") or []

            if ad_units:
                config["targeted_ad_unit_ids"] = [str(ad_unit_id) for ad_unit_id in ad_units]
            if placements:
                config["targeted_placement_ids"] = [str(placement_id) for placement_id in placements]
            if "include_descendants" in inventory_config:
                config["include_descendants"] = bool(inventory_config.get("include_descendants"))

        return config

    __table_args__ = (
        Index("idx_products_tenant", "tenant_id"),
        # Enforce AdCP spec: products must have EITHER properties OR property_tags (not both, not neither)
        CheckConstraint(
            "(properties IS NOT NULL AND property_tags IS NULL) OR (properties IS NULL AND property_tags IS NOT NULL)",
            name="ck_product_properties_xor",
        ),
    )


class PricingOption(Base):
    """Pricing option for a product (AdCP PR #88).

    Each product can have multiple pricing options with different pricing models,
    currencies, and rate structures (fixed or auction-based).
    """

    __tablename__ = "pricing_options"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(50), nullable=False)
    product_id: Mapped[str] = mapped_column(String(100), nullable=False)
    pricing_model: Mapped[str] = mapped_column(String(20), nullable=False)
    rate: Mapped[Decimal | None] = mapped_column(DECIMAL(10, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    is_fixed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    price_guidance: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    parameters: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    min_spend_per_package: Mapped[Decimal | None] = mapped_column(DECIMAL(10, 2), nullable=True)

    # Relationships
    product = relationship("Product", back_populates="pricing_options")

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "product_id"],
            ["products.tenant_id", "products.product_id"],
            ondelete="CASCADE",
        ),
        Index("idx_pricing_options_product", "tenant_id", "product_id"),
    )


class CurrencyLimit(Base):
    """Currency-specific budget limits per tenant.

    Each tenant can support multiple currencies with different min/max limits.
    This avoids FX conversion and provides currency-specific controls.

    **IMPORTANT**: All limits are per-package (not per media buy) to prevent
    buyers from splitting large budgets across many packages/line items.
    """

    __tablename__ = "currency_limits"

    tenant_id: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"),
        primary_key=True,
    )
    currency_code: Mapped[str] = mapped_column(String(3), primary_key=True)

    # Minimum total budget per package/line item in this currency
    min_package_budget: Mapped[Decimal | None] = mapped_column(DECIMAL(15, 2), nullable=True)

    # Maximum daily spend per package/line item in this currency
    # Prevents buyers from creating many small line items to bypass limits
    max_daily_package_spend: Mapped[Decimal | None] = mapped_column(DECIMAL(15, 2), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    tenant = relationship("Tenant", back_populates="currency_limits")

    __table_args__ = (
        Index("idx_currency_limits_tenant", "tenant_id"),
        UniqueConstraint("tenant_id", "currency_code", name="uq_currency_limit"),
    )


class Principal(Base, JSONValidatorMixin):
    __tablename__ = "principals"

    tenant_id: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"),
        primary_key=True,
    )
    principal_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    platform_mappings: Mapped[dict] = mapped_column(JSONType, nullable=False)
    # Bearer token for direct-to-/mcp authentication. Required for
    # open-instance principals (buyer agents that hit the sales agent's
    # AdCP surface directly). Nullable so embedded-mode principals — where
    # the host (storefront) is the only agent and the sales agent never
    # receives requests directly from a buyer — can be created without one.
    access_token: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    # Buyer-agent URL — informational; populated from brand.json at admit
    # time so it shows up in audit logs and the admin UI. NOT used by the
    # verifier hot path; rotation of agent_url in brand.json doesn't need
    # operator action because the verifier walks brand.json itself.
    agent_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    # Operator-typed buyer domain. The TRUST ANCHOR — verifier constructs
    # ``https://<brand_domain>/.well-known/brand.json`` and walks it via
    # adcp.signing.BrandJsonJwksResolver, which auto-refreshes on cooldown
    # + unknown-kid cascade. NULL means bearer-only auth (legacy path).
    # See docs/design/signing-non-embedded.md.
    brand_domain: Mapped[str | None] = mapped_column(String(253), nullable=True)
    # When True, unsigned requests from this principal are rejected. When False
    # and agent_url is set, the verifier accepts signed requests but does not
    # require them (mixed mode for migration). Ignored when agent_url is NULL
    # (bearer-only principals can't sign). Replaces tenant-wide required_for.
    signing_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    # Most-recent successful signed-request verification. Updated by the
    # verifier middleware; reset to NULL when the operator changes
    # ``brand_domain`` so the strict-admit guard doesn't carry stale
    # evidence forward across trust-root changes. ``agent_url`` change
    # does NOT reset — the verifier walks brand.json and the library
    # handles agent_url rotation automatically.
    last_signed_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Whether this buyer agent is allowed to be billed (i.e. accept Accounts
    # with billing="agent"). Default True keeps existing principals billable.
    # When False, the account-create / account-update path rejects requests
    # that try to set billing="agent" for any account owned by this principal.
    # billing="operator" / NULL is always allowed regardless. See BR-RULE-061.
    billing_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    # Storefront-supplied stable identifier for idempotent principal
    # creation via /api/v1/principals. Unique per (tenant, external_id)
    # when non-null; NULL for principals created through legacy paths.
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    tenant = relationship("Tenant", back_populates="principals")
    media_buys = relationship("MediaBuy", back_populates="principal", overlaps="media_buys")
    strategies = relationship("Strategy", back_populates="principal", overlaps="strategies")
    push_notification_configs = relationship(
        "PushNotificationConfig",
        back_populates="principal",
        overlaps="push_notification_configs,tenant",
    )

    __table_args__ = (
        Index("idx_principals_tenant", "tenant_id"),
        Index("idx_principals_token", "access_token"),
        Index(
            "idx_principals_agent_url",
            "tenant_id",
            "agent_url",
            postgresql_where=text("agent_url IS NOT NULL"),
        ),
        Index(
            "idx_principals_external_id",
            "tenant_id",
            "external_id",
            unique=True,
            postgresql_where=text("external_id IS NOT NULL"),
        ),
    )

    def get_adapter_id(self, adapter_name: str) -> str | None:
        """Get the adapter-specific ID for this principal.

        Delegates to the shared resolve_adapter_id() so ORM and Pydantic
        Principal objects use identical lookup logic.
        """
        from src.core.platform_mappings import resolve_adapter_id

        return resolve_adapter_id(self.platform_mappings, adapter_name)


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(50), ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    google_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_login: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Relationships
    tenant = relationship("Tenant", back_populates="users")

    __table_args__ = (
        CheckConstraint("role IN ('admin', 'member', 'viewer')", name="ck_users_role"),
        UniqueConstraint("tenant_id", "email", name="uq_users_tenant_email"),  # Unique per tenant
        Index("idx_users_tenant", "tenant_id"),
        Index("idx_users_email", "email"),
        Index("idx_users_google_id", "google_id"),
    )


class TenantAuthConfig(Base):
    """Per-tenant authentication configuration for OIDC/SSO."""

    __tablename__ = "tenant_auth_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(
        String(50), ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, unique=True
    )

    # OIDC configuration
    oidc_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    oidc_provider: Mapped[str | None] = mapped_column(String(50), nullable=True)  # google, microsoft, custom
    oidc_discovery_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    oidc_client_id: Mapped[str | None] = mapped_column(String(500), nullable=True)
    oidc_client_secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)  # Fernet encrypted
    oidc_scopes: Mapped[str | None] = mapped_column(String(500), nullable=True, default="openid email profile")
    oidc_logout_url: Mapped[str | None] = mapped_column(String(500), nullable=True)  # IdP logout endpoint

    # Verification state - tracks last successful OAuth test
    oidc_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    oidc_verified_redirect_uri: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), onupdate=func.now(), nullable=True)

    # Relationships
    tenant: Mapped["Tenant"] = relationship(back_populates="auth_config")

    __table_args__ = (Index("idx_tenant_auth_configs_tenant_id", "tenant_id", unique=True),)

    @property
    def oidc_client_secret(self) -> str | None:
        """Decrypt and return the OIDC client secret."""
        return _decrypt_optional_secret(
            self.oidc_client_secret_encrypted,
            secret_name="OIDC client secret",
            owner_id=f"tenant {self.tenant_id}",
        )

    @oidc_client_secret.setter
    def oidc_client_secret(self, value: str | None) -> None:
        """Encrypt and store the OIDC client secret."""
        self.oidc_client_secret_encrypted = _encrypt_optional_secret(value)


class Creative(Base):
    """Creative database model matching the actual creatives table schema."""

    __tablename__ = "creatives"

    creative_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    principal_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    agent_url: Mapped[str] = mapped_column(String(500), nullable=False)
    format: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending")

    # Data field stores creative content and metadata as JSON
    data: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)

    # Format parameters for parameterized FormatId (AdCP 2.5 format templates)
    # Stores width, height, duration_ms when format is parameterized
    format_parameters: Mapped[dict | None] = mapped_column(JSONType, nullable=True)

    # Relationships and metadata
    group_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, server_default=func.current_timestamp()
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    strategy_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Relationships
    tenant = relationship("Tenant", backref="creatives")
    reviews = relationship("CreativeReview", back_populates="creative", cascade="all, delete-orphan")

    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["tenant_id", "principal_id"], ["principals.tenant_id", "principals.principal_id"]),
        Index("idx_creatives_tenant", "tenant_id"),
        Index("idx_creatives_principal", "tenant_id", "principal_id"),
        Index("idx_creatives_status", "status"),
        Index("idx_creatives_format_namespace", "agent_url", "format"),  # AdCP v2.4 format namespacing
    )


class CreativeReview(Base):
    """Creative review records for analytics and learning.

    Stores AI and human review decisions to enable:
    - Review history tracking per creative
    - AI accuracy measurement and improvement
    - Human override analytics
    - Confidence threshold tuning
    """

    __tablename__ = "creative_reviews"

    review_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    creative_id: Mapped[str] = mapped_column(String(100), nullable=False)
    tenant_id: Mapped[str] = mapped_column(
        String(50), ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False
    )
    principal_id: Mapped[str] = mapped_column(String(100), nullable=False)

    # Review metadata
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    review_type: Mapped[str] = mapped_column(String(20), nullable=False)
    reviewer_email: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # AI decision
    ai_decision: Mapped[str | None] = mapped_column(String(20), nullable=True)
    confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    policy_triggered: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Review details
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    recommendations: Mapped[dict | None] = mapped_column(JSONType, nullable=True)

    # Learning system
    human_override: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    final_decision: Mapped[str] = mapped_column(String(20), nullable=False)

    # Relationships
    creative = relationship("Creative", back_populates="reviews")
    tenant = relationship("Tenant", overlaps="creative,reviews")

    __table_args__ = (
        ForeignKeyConstraint(
            ["creative_id", "tenant_id", "principal_id"],
            ["creatives.creative_id", "creatives.tenant_id", "creatives.principal_id"],
            ondelete="CASCADE",
        ),
        Index("ix_creative_reviews_creative_id", "creative_id"),
        Index("ix_creative_reviews_tenant_id", "tenant_id"),
        Index("ix_creative_reviews_reviewed_at", "reviewed_at"),
        Index("ix_creative_reviews_review_type", "review_type"),
        Index("ix_creative_reviews_final_decision", "final_decision"),
    )


class CreativeAssignment(Base):
    """Creative assignments to media buy packages.

    Supports adcp#208 creative management capabilities:
    - weight: Rotation weight for creative delivery (0-100)
    - placement_ids: Placement-specific targeting within package
    """

    __tablename__ = "creative_assignments"

    assignment_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(50), ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False
    )
    creative_id: Mapped[str] = mapped_column(String(100), nullable=False)
    principal_id: Mapped[str] = mapped_column(String(100), nullable=False)
    media_buy_id: Mapped[str] = mapped_column(String(100), nullable=False)
    package_id: Mapped[str] = mapped_column(String(100), nullable=False)
    weight: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    # adcp#208: placement-specific targeting within package
    placement_ids: Mapped[list[str] | None] = mapped_column(JSONType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    # Relationships
    tenant = relationship("Tenant")

    __table_args__ = (
        ForeignKeyConstraint(
            ["creative_id", "tenant_id", "principal_id"],
            ["creatives.creative_id", "creatives.tenant_id", "creatives.principal_id"],
        ),
        ForeignKeyConstraint(["media_buy_id"], ["media_buys.media_buy_id"]),
        Index("idx_creative_assignments_tenant", "tenant_id"),
        Index("idx_creative_assignments_creative", "creative_id"),
        Index("idx_creative_assignments_media_buy", "media_buy_id"),
        UniqueConstraint("tenant_id", "creative_id", "media_buy_id", "package_id", name="uq_creative_assignment"),
    )


class Account(Base):
    """Billing account per AdCP spec (core/account.json).

    Represents the relationship between a buyer and seller, determining
    rate cards, payment terms, and billing entity.
    """

    __tablename__ = "accounts"

    tenant_id: Mapped[str] = mapped_column(
        String(50), ForeignKey("tenants.tenant_id", ondelete="CASCADE"), primary_key=True
    )
    account_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    # Required fields (AdCP spec)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)

    # Optional fields (AdCP spec)
    advertiser: Mapped[str | None] = mapped_column(String(255), nullable=True)
    billing_proxy: Mapped[str | None] = mapped_column(String(255), nullable=True)
    operator: Mapped[str | None] = mapped_column(String(255), nullable=True)
    billing: Mapped[str | None] = mapped_column(String(20), nullable=True)
    rate_card: Mapped[str | None] = mapped_column(String(255), nullable=True)
    payment_terms: Mapped[str | None] = mapped_column(String(20), nullable=True)
    account_scope: Mapped[str | None] = mapped_column(String(20), nullable=True)
    brand: Mapped[BrandReference | None] = mapped_column(JSONType(model=BrandReference), nullable=True)
    credit_limit: Mapped[CreditLimit | None] = mapped_column(JSONType(model=CreditLimit), nullable=True)
    setup: Mapped[Setup | None] = mapped_column(JSONType(model=Setup), nullable=True)
    governance_agents: Mapped[list[GovernanceAgent] | None] = mapped_column(
        JSONType(model=GovernanceAgent, is_list=True), nullable=True
    )
    sandbox: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=False)
    ext: Mapped[dict | None] = mapped_column(JSONType, nullable=True)

    # Internal fields (not in AdCP spec)
    principal_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    platform_mappings: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    # Sprint 1.8: which path the routing chain took to attach the
    # gam_advertiser_id on this Account. Used by /recent-buyers to
    # color-code matches vs fall-throughs without re-running resolution.
    # Legacy rows are NULL; surfaces as "unknown" in API responses.
    resolved_via: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    tenant = relationship("Tenant", back_populates="accounts")

    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'pending_approval', 'pending_provision', 'rejected', "
            "'payment_required', 'suspended', 'closed')",
            name="ck_accounts_status",
        ),
        CheckConstraint(
            "billing IS NULL OR billing IN ('operator', 'agent')",
            name="ck_accounts_billing",
        ),
        CheckConstraint(
            "payment_terms IS NULL OR payment_terms IN ('net_15', 'net_30', 'net_45', 'net_60', 'net_90', 'prepay')",
            name="ck_accounts_payment_terms",
        ),
        CheckConstraint(
            "account_scope IS NULL OR account_scope IN ('operator', 'brand', 'operator_brand', 'agent')",
            name="ck_accounts_account_scope",
        ),
        CheckConstraint(
            "resolved_via IS NULL OR resolved_via IN ('account', 'sandbox', 'exact', 'house', 'operator', 'default')",
            name="ck_accounts_resolved_via",
        ),
        Index("idx_accounts_tenant", "tenant_id"),
        Index("idx_accounts_status", "status"),
        Index("idx_accounts_operator", "operator"),
    )


class AgentAccountAccess(Base):
    """Junction table linking principals (agents) to accounts they can access.

    Enables multi-agent visibility scoping: different agents see different accounts.
    """

    __tablename__ = "agent_account_access"

    tenant_id: Mapped[str] = mapped_column(String(50), nullable=False, primary_key=True)
    principal_id: Mapped[str] = mapped_column(String(50), nullable=False, primary_key=True)
    account_id: Mapped[str] = mapped_column(String(100), nullable=False, primary_key=True)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "principal_id"],
            ["principals.tenant_id", "principals.principal_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "account_id"],
            ["accounts.tenant_id", "accounts.account_id"],
            ondelete="CASCADE",
        ),
        Index("idx_agent_account_access_account", "tenant_id", "account_id"),
    )


class MediaBuy(Base):
    __tablename__ = "media_buys"

    media_buy_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(50), ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False
    )
    principal_id: Mapped[str] = mapped_column(String(50), nullable=False)
    order_name: Mapped[str] = mapped_column(String(255), nullable=False)
    advertiser_name: Mapped[str] = mapped_column(String(255), nullable=False)
    campaign_objective: Mapped[str | None] = mapped_column(String(100), nullable=True)
    kpi_goal: Mapped[str | None] = mapped_column(String(255), nullable=True)
    budget: Mapped[Decimal | None] = mapped_column(DECIMAL(15, 2))
    currency: Mapped[str] = mapped_column(String(3), nullable=True, default="USD")
    start_date: Mapped[Date] = mapped_column(Date, nullable=False)
    end_date: Mapped[Date] = mapped_column(Date, nullable=False)
    start_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    raw_request: Mapped[dict] = mapped_column(JSONType, nullable=False)
    strategy_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_paused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    account_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Origin marker. ``adcp`` for buys created via the AdCP protocol;
    # ``gam_import`` for buys materialized from gam_orders when an
    # assigned buyer first updates an imported order.
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="adcp", server_default="adcp")
    # Adapter-side ID (e.g. GAM order ID). Populated for both native and
    # imported buys so lookups can resolve by either the canonical
    # ``media_buy_id`` or the adapter ID.
    external_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Delivery snapshot — populated opportunistically when a buyer calls
    # get_media_buy_delivery, so the publisher dashboard can show pacing
    # without making an adapter call on render. Nullable until first poll.
    delivered_impressions: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    delivered_amount: Mapped[Decimal | None] = mapped_column(DECIMAL(15, 2), nullable=True)
    delivery_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    tenant = relationship("Tenant", back_populates="media_buys", overlaps="media_buys")
    principal = relationship(
        "Principal",
        foreign_keys=[tenant_id, principal_id],
        primaryjoin="and_(MediaBuy.tenant_id==Principal.tenant_id, MediaBuy.principal_id==Principal.principal_id)",
        overlaps="media_buys,tenant",
    )
    strategy = relationship("Strategy", back_populates="media_buys")
    packages = relationship("MediaPackage", back_populates="media_buy", cascade="all, delete-orphan")
    account = relationship(
        "Account",
        foreign_keys=[tenant_id, account_id],
        primaryjoin="and_(MediaBuy.tenant_id==Account.tenant_id, MediaBuy.account_id==Account.account_id)",
        overlaps="media_buys,principal,tenant",
        viewonly=True,
    )
    # Removed tasks and context relationships - using ObjectWorkflowMapping instead

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "principal_id"],
            ["principals.tenant_id", "principals.principal_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["strategy_id"],
            ["strategies.strategy_id"],
            ondelete="SET NULL",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "account_id"],
            ["accounts.tenant_id", "accounts.account_id"],
            ondelete="SET NULL",
        ),
        Index("idx_media_buys_tenant", "tenant_id"),
        Index("idx_media_buys_status", "status"),
        Index("idx_media_buys_strategy", "strategy_id"),
        Index("idx_media_buys_account", "account_id"),
        Index(
            "idx_media_buys_idempotency_key",
            "tenant_id",
            "principal_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        Index(
            "idx_media_buys_external_id",
            "tenant_id",
            "external_id",
            unique=True,
            postgresql_where=text("external_id IS NOT NULL"),
        ),
    )


class MediaPackage(Base):
    """Media package model for structured querying of media buy packages.

    Stores packages separately from MediaBuy.raw_request for efficient lookups
    by package_id, which is needed for creative assignments.

    AdCP package-level fields (budget, bid_price, pacing) are stored as dedicated
    columns for query performance and data integrity, while package_config maintains
    the full package structure for backward compatibility.
    """

    __tablename__ = "media_packages"

    media_buy_id: Mapped[str] = mapped_column(
        String(100), ForeignKey("media_buys.media_buy_id"), primary_key=True, nullable=False
    )
    package_id: Mapped[str] = mapped_column(String(100), primary_key=True, nullable=False)

    # AdCP package-level fields (extracted for querying and constraints)
    budget: Mapped[Decimal | None] = mapped_column(
        DECIMAL(15, 2),
        nullable=True,
        comment="Package budget allocation (AdCP spec: number, package-level)",
    )
    bid_price: Mapped[Decimal | None] = mapped_column(
        DECIMAL(15, 2),
        nullable=True,
        comment="Bid price for auction-based pricing (AdCP spec: number, optional)",
    )
    pacing: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
        comment="Pacing strategy: even, asap, front_loaded (AdCP enum)",
    )

    # Full package configuration (includes all AdCP fields + internal fields)
    package_config: Mapped[dict] = mapped_column(JSONType, nullable=False)

    # Relationships
    media_buy = relationship("MediaBuy", back_populates="packages")

    __table_args__ = (
        Index("idx_media_packages_media_buy", "media_buy_id"),
        Index("idx_media_packages_package", "package_id"),
        Index("idx_media_packages_budget", "budget", postgresql_where=text("budget IS NOT NULL")),
        CheckConstraint("budget > 0", name="ck_media_packages_budget_positive"),
        CheckConstraint("bid_price >= 0", name="ck_media_packages_bid_price_non_negative"),
        CheckConstraint(
            "pacing IN ('even', 'asap', 'front_loaded')",
            name="ck_media_packages_pacing_values",
        ),
    )


# DEPRECATED: Task and HumanTask models removed - replaced by WorkflowStep system
# Tables may still exist in database for backward compatibility but are not used by application
# Dashboard now uses only audit_logs table for activity tracking
# Workflow operations use WorkflowStep and ObjectWorkflowMapping tables


class AuditLog(Base):
    __tablename__ = "audit_logs"

    log_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(
        String(50), ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    operation: Mapped[str] = mapped_column(String(100), nullable=False)
    principal_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    principal_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    adapter_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    details: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    strategy_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Managed-tenant external identity propagation (sprint 1 of managed tenant mode).
    # Populated when a mutation originates from an upstream-platform user (e.g. Scope3
    # Storefront). All four are optional — open-instance audit rows leave them NULL.
    external_user_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    external_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    external_org_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    external_source: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # RFC 9421 signed-request verification trail. NULL on rows for unsigned
    # requests and on legacy rows. Populated by SigningVerifyMiddleware when
    # the verifier accepts a signature. The (verified_agent_url, verified_key_id)
    # pair stamps "this row was signed with kid X published at agent_url Y."
    # Calling principal is identified via principal_id (already on the row).
    verified_agent_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    verified_key_id: Mapped[str | None] = mapped_column(String(256), nullable=True)

    # Relationships
    tenant = relationship("Tenant", back_populates="audit_logs")

    __table_args__ = (
        ForeignKeyConstraint(
            ["strategy_id"],
            ["strategies.strategy_id"],
            ondelete="SET NULL",
        ),
        Index("idx_audit_logs_tenant", "tenant_id"),
        Index("idx_audit_logs_timestamp", "timestamp"),
        Index("idx_audit_logs_strategy", "strategy_id"),
    )


class TenantManagementConfig(Base):
    __tablename__ = "superadmin_config"

    config_key: Mapped[str] = mapped_column(String(100), primary_key=True)
    config_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    updated_by: Mapped[str | None] = mapped_column(String(255), nullable=True)


# Backwards compatibility alias
SuperadminConfig = TenantManagementConfig


class WebhookSubscription(Base):
    """Outbound webhook subscription owned by a tenant.

    [embedded-mode](../../../../docs/design/embedded-mode.md) publishes
    tenant lifecycle events (workflow.created, workflow.decided,
    media_buy.status_changed, sync.completed, sync.failed,
    tenant.config_changed) to URLs registered here.

    The subscription secret is stored hashed (sha256 hex) — the plaintext is
    returned to the API caller exactly once at create time. Lost secrets
    require re-registering the webhook.

    ``event_types`` is the list of event types the receiver wants. Empty list
    means "all events" (matching the spec's documented default).
    """

    __tablename__ = "webhook_subscriptions"

    webhook_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"),
        nullable=False,
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    event_types: Mapped[list[str]] = mapped_column(JSONType, nullable=False, default=list)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    secret_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    extra_headers: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_delivery_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_delivery_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("idx_webhook_subscriptions_tenant", "tenant_id"),
        Index(
            "idx_webhook_subscriptions_active",
            "tenant_id",
            "is_active",
        ),
    )


class AdapterConfig(Base):
    __tablename__ = "adapter_config"

    tenant_id: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"),
        primary_key=True,
    )
    adapter_type: Mapped[str] = mapped_column(String(50), nullable=False)

    # Mock adapter
    mock_dry_run: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # Google Ad Manager
    gam_network_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    gam_refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    _gam_service_account_json: Mapped[str | None] = mapped_column(
        "gam_service_account_json",
        Text,
        nullable=True,
        comment="Encrypted service account key. Required to authenticate AS the service account when calling GAM API. Partner must also add the email to their GAM for access.",
    )
    gam_service_account_email: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment="Email of auto-provisioned service account. Partner adds this to their GAM user list with appropriate permissions.",
    )
    gam_auth_method: Mapped[str] = mapped_column(String(50), nullable=False, server_default="oauth")
    gam_trafficker_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    gam_network_currency: Mapped[str | None] = mapped_column(
        String(3),
        nullable=True,
        comment="Primary currency code from GAM network (ISO 4217). Auto-populated on connection test.",
    )
    gam_secondary_currencies: Mapped[list | None] = mapped_column(
        JSONType,
        nullable=True,
        comment="Secondary currency codes enabled in GAM network (ISO 4217 array). Auto-populated on connection test.",
    )
    gam_network_timezone: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        comment="Timezone of the GAM network (e.g., 'America/New_York'). Auto-populated on connection test.",
    )
    gam_manual_approval_required: Mapped[bool] = mapped_column(Boolean, default=False)
    gam_order_name_template: Mapped[str | None] = mapped_column(String(500), nullable=True)
    gam_line_item_name_template: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # AXE (Audience Exchange) custom targeting keys (AdCP spec requires separate keys for each purpose)
    # These are adapter-agnostic and work with GAM, Mock, or any other adapter
    # Note: gam_axe_custom_targeting_key was removed - use the three separate keys below
    axe_include_key: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        comment="Custom targeting key for AXE include segments (axe_include_segment) - works with all adapters",
    )
    axe_exclude_key: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        comment="Custom targeting key for AXE exclude segments (axe_exclude_segment) - works with all adapters",
    )
    axe_macro_key: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        comment="Custom targeting key for AXE creative macro segments (enable_creative_macro) - works with all adapters",
    )

    # Custom targeting key ID mappings for GAM
    # Maps key names → GAM custom targeting key IDs (e.g., {"axe_include_segment": "123456789"})
    # This allows the adapter to resolve key names to IDs without additional API calls
    custom_targeting_keys: Mapped[dict] = mapped_column(JSONType, nullable=False, server_default=text("'{}'::jsonb"))

    # NOTE: gam_company_id (advertiser_id) is per-principal, stored in Principal.platform_mappings

    # Sprint 1.8 + 1.6: per-tenant sandbox advertiser. Lazy-populated by
    # ensure_sandbox_advertiser() on first sandbox call; the routing chain
    # short-circuits sandbox=true buys to this advertiser (don't bill,
    # don't pollute reports, don't count against inventory).
    gam_sandbox_advertiser_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Mock
    mock_manual_approval_required: Mapped[bool] = mapped_column(Boolean, default=False)

    # Schema-driven configuration (coexists with legacy columns during migration)
    config_json: Mapped[dict] = mapped_column(
        JSONType,
        nullable=False,
        server_default=text("'{}'::jsonb"),
        comment="Schema-validated adapter configuration",
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    tenant = relationship("Tenant", back_populates="adapter_config")

    __table_args__ = (Index("idx_adapter_config_type", "adapter_type"),)

    @property
    def gam_service_account_json(self) -> str | None:
        """Get decrypted GAM service account JSON."""
        return _decrypt_optional_secret(
            self._gam_service_account_json,
            secret_name="GAM service account JSON",
            owner_id=f"tenant {self.tenant_id}",
        )

    @gam_service_account_json.setter
    def gam_service_account_json(self, value: str | None) -> None:
        """Set encrypted GAM service account JSON."""
        self._gam_service_account_json = _encrypt_optional_secret(value)


class CreativeAgent(Base):
    """Tenant-specific creative agent configuration.

    Each tenant can register custom creative agents in addition to the default
    AdCP creative agent at https://creative.adcontextprotocol.org
    """

    __tablename__ = "creative_agents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_url: Mapped[str] = mapped_column(String(500), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    auth_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    auth_header: Mapped[str | None] = mapped_column(String(100), nullable=True)
    _auth_credentials: Mapped[str | None] = mapped_column("auth_credentials", Text, nullable=True)
    timeout: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    tenant = relationship("Tenant", back_populates="creative_agents")

    __table_args__ = (
        Index("idx_creative_agents_tenant", "tenant_id"),
        Index("idx_creative_agents_enabled", "enabled"),
    )

    @property
    def auth_credentials(self) -> str | None:
        """Get decrypted auth credentials for private creative agents."""
        return _decrypt_optional_secret(
            self._auth_credentials,
            secret_name="creative agent auth credentials",
            owner_id=f"creative_agent {self.id or 'new'} tenant {self.tenant_id}",
            allow_legacy_plaintext=True,
        )

    @auth_credentials.setter
    def auth_credentials(self, value: str | None) -> None:
        """Set encrypted auth credentials for private creative agents."""
        self._auth_credentials = _encrypt_optional_secret(value)


class SignalsAgent(Base):
    """Tenant-specific signals discovery agent configuration.

    Each tenant can register custom signals agents for product discovery enhancement.
    Priority and max_signal_products are configured per-product, not per-agent.
    """

    __tablename__ = "signals_agents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_url: Mapped[str] = mapped_column(String(500), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    auth_type: Mapped[str | None] = mapped_column(String(50), nullable=True)  # "bearer", "api_key", etc.
    auth_header: Mapped[str | None] = mapped_column(String(100), nullable=True)  # e.g., "x-api-key", "Authorization"
    _auth_credentials: Mapped[str | None] = mapped_column("auth_credentials", Text, nullable=True)
    forward_promoted_offering: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    timeout: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    tenant = relationship("Tenant", back_populates="signals_agents")

    __table_args__ = (
        Index("idx_signals_agents_tenant", "tenant_id"),
        Index("idx_signals_agents_enabled", "enabled"),
    )

    @property
    def auth_credentials(self) -> str | None:
        """Get decrypted auth credentials for private signals agents."""
        return _decrypt_optional_secret(
            self._auth_credentials,
            secret_name="signals agent auth credentials",
            owner_id=f"signals_agent {self.id or 'new'} tenant {self.tenant_id}",
            allow_legacy_plaintext=True,
        )

    @auth_credentials.setter
    def auth_credentials(self, value: str | None) -> None:
        """Set encrypted auth credentials for private signals agents."""
        self._auth_credentials = _encrypt_optional_secret(value)


class GAMInventory(Base):
    __tablename__ = "gam_inventory"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(50), ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False
    )
    inventory_type: Mapped[str] = mapped_column(String(30), nullable=False)
    inventory_id: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    path: Mapped[list | None] = mapped_column(JSONType, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    inventory_metadata: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    last_synced: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=func.now(), onupdate=func.now()
    )

    # Relationships
    tenant = relationship("Tenant")

    __table_args__ = (
        UniqueConstraint("tenant_id", "inventory_type", "inventory_id", name="uq_gam_inventory"),
        Index("idx_gam_inventory_tenant", "tenant_id"),
        Index("idx_gam_inventory_type", "inventory_type"),
        Index("idx_gam_inventory_status", "status"),
        Index("idx_gam_inventory_tenant_type_status", "tenant_id", "inventory_type", "status"),
    )


class InventoryProfile(Base, JSONValidatorMixin):
    """Reusable inventory configuration template.

    An inventory profile is a named collection of:
    - Inventory (ad units, placements)
    - Creative formats (which formats work with this inventory)
    - Publisher properties (which sites/apps/properties this represents)
    - Optional default targeting rules

    Multiple products can reference the same profile. When the profile is updated,
    all products using it automatically reflect the changes.
    """

    __tablename__ = "inventory_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"),
        nullable=False,
    )

    # Profile identification
    profile_id: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Inventory configuration
    # Structure: {
    #   "ad_units": ["23312403859", "23312403860"],
    #   "placements": ["45678901"],
    #   "include_descendants": true
    # }
    inventory_config: Mapped[dict] = mapped_column(JSONType, nullable=False)

    # Creative formats (FormatId objects)
    # Structure: [{"agent_url": "...", "id": "display_300x250_image"}]
    # Validated at database level via CHECK constraint (see migration: rename_formats_to_format_ids)
    format_ids: Mapped[list] = mapped_column(JSONType, nullable=False)

    # Publisher properties (AdCP spec-compliant)
    # Structure: [
    #   {
    #     "publisher_domain": "cnn.com",
    #     "property_ids": ["cnn_homepage"],  # OR
    #     "property_tags": ["premium_news"]
    #   }
    # ]
    publisher_properties: Mapped[list] = mapped_column(JSONType, nullable=False)

    # Optional default targeting template
    # Structure: AdCP targeting object
    targeting_template: Mapped[dict | None] = mapped_column(JSONType, nullable=True)

    # Optional GAM integration
    gam_preset_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    gam_preset_sync_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))

    # Typed AdCP capability narrowings (formats, channels, targeting_dimensions)
    # so storefronts can pre-validate compositions client-side without
    # round-tripping to the agent. Vocabulary references AdCP DecisioningCapabilities;
    # bundles express narrowings, never redeclarations. Optional in v1.
    # Shape: {"formats": [format_id, ...], "channels": [channel, ...],
    #         "targeting_dimensions": [dim_name, ...]}
    constraints: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    # Content hash for cache invalidation via If-None-Match on the REST API.
    etag: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=func.now(), onupdate=func.now()
    )

    # Relationships
    tenant = relationship("Tenant")
    products = relationship("Product", back_populates="inventory_profile")

    __table_args__ = (
        UniqueConstraint("tenant_id", "profile_id", name="uq_inventory_profile"),
        Index("idx_inventory_profiles_tenant", "tenant_id"),
    )


class InventoryBundleReference(Base):
    """Denormalized "is this synced entity in ≥1 inventory bundle?" lookup.

    Backs the Job 1 (Discovery) coverage analytics on the dashboard
    (#485). A row's existence means the entity is referenced by at
    least one ``InventoryProfile``. No state machine, no review/skip
    semantics — operators don't review-each-ad-unit, they author
    bundles and the same placement gets reused across many of them.

    Used to compute "of N synced ad units, M appear in a bundle" without
    forcing a JSON-blob JOIN over every ``InventoryProfile`` at request
    time. The table is kept fresh at bundle-save time by
    :func:`src.services.inventory_review_state_sync.recompute_in_bundle_status`.

    Keyed by ``(tenant_id, adapter, entity_type, external_id)``. The
    ``entity_type`` slot leaves room for future denormalizations
    (e.g. signal candidates) without duplicating the schema.
    """

    __tablename__ = "inventory_bundle_reference"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"),
        nullable=False,
    )
    # ``gam`` | ``freewheel`` | ``springserve`` — adapter that owns the entity.
    adapter: Mapped[str] = mapped_column(String(50), nullable=False)
    # ``ad_unit`` | ``placement`` today.
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # Adapter-native id. Strings — adapters vary (some are int-like, some not).
    external_id: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=func.now(), onupdate=func.now()
    )

    tenant = relationship("Tenant")

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "adapter",
            "entity_type",
            "external_id",
            name="uq_inventory_bundle_reference",
        ),
        Index(
            "idx_inventory_bundle_reference_tenant_type",
            "tenant_id",
            "entity_type",
        ),
        Index(
            "idx_inventory_bundle_reference_tenant_adapter_type",
            "tenant_id",
            "adapter",
            "entity_type",
        ),
    )


class TenantSignal(Base, JSONValidatorMixin):
    """Operator-authored map of one adapter targeting capability.

    Replaces ``CustomTargetingProfile``. The previous shape baked
    GAM-specific structure (``key_values``, ``audience_segments``) into the
    schema, which doesn't generalize across Freewheel, Broadstreet,
    SpringServe, or future adapters. The new shape mirrors AdCP's existing
    ``Signal`` type so the storefront can render UI for any signal type —
    categorical taxonomies, numeric ranges, binary toggles — without
    per-adapter branching.

    The storefront sees the AdCP-vocab fields (``signal_id``, ``name``,
    ``value_type``, ``categories``, ``range``, ``targeting_dimension``).
    The ``adapter_config`` blob is operator-authored, opaque to the
    storefront, and consumed by the per-adapter materializer at compose
    time to produce the right ``implementation_config`` for whichever ad
    server the tenant uses.

    Composition of multiple signals on a single buy (e.g. ``audience.X``
    AND ``geo.Y`` AND NOT ``audience.Z``) reuses the existing
    custom-targeting expression machinery — no parallel "signal bundle"
    entity. Operators declare atomic capabilities; storefronts compose at
    request time.
    """

    __tablename__ = "tenant_signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"),
        nullable=False,
    )
    # Stable AdCP-style identifier (e.g. ``audience_sports_fans``,
    # ``kv_vertical``, ``weather_temperature_f``). Storefront references this.
    signal_id: Mapped[str] = mapped_column(String(200), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # AdCP SignalValueType — drives storefront UI rendering.
    # ``binary`` (boolean toggle), ``categorical`` (multi-select), ``numeric``
    # (slider / range picker).
    value_type: Mapped[str] = mapped_column(String(32), nullable=False)

    # Categorical taxonomy. Empty list for non-categorical signals.
    categories: Mapped[list[str]] = mapped_column(JSONType, nullable=False, default=list)

    # AdCP ``Signal.tags`` — lowercase string labels for grouping / filtering
    # within the catalog. Pattern enforced at the API boundary, not in the
    # column itself (Postgres can't cheaply validate per-element).
    tags: Mapped[list[str]] = mapped_column(JSONType, nullable=False, default=list)

    # Numeric bounds (when value_type='numeric'). NULL when N/A.
    range_min: Mapped[Decimal | None] = mapped_column(DECIMAL(20, 6), nullable=True)
    range_max: Mapped[Decimal | None] = mapped_column(DECIMAL(20, 6), nullable=True)

    # Adapter-specific resolution map. Operator-authored, opaque to the
    # storefront, consumed by the per-adapter materializer. Examples:
    #   GAM custom KV: {"kind": "custom_key_value", "key_id": "12345",
    #                   "value_ids": {"sports": "11111", "news": "22222"}}
    #   GAM audience:  {"kind": "audience_segment", "segment_id": "98765"}
    #   Freewheel:     {"kind": "audience_item", "audience_item_id": "..."}
    #                | {"kind": "viewership_profile", "id": "..."}
    # Adapter materializers validate shape at compose time.
    adapter_config: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)

    # Where the signal originates (publisher first-party, 3p data provider,
    # derived). Informational only; storefront UX may surface it.
    data_provider: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # AdCP-standard targeting-dimension this signal narrows. Cross-checked
    # against ``InventoryProfile.constraints.targeting_dimensions`` so the
    # storefront can pre-validate compositions client-side.
    targeting_dimension: Mapped[str | None] = mapped_column(String(64), nullable=True)

    etag: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=func.now(), onupdate=func.now()
    )

    tenant = relationship("Tenant")

    __table_args__ = (
        UniqueConstraint("tenant_id", "signal_id", name="uq_tenant_signal"),
        Index("idx_tenant_signals_tenant", "tenant_id"),
    )


class ProductInventoryMapping(Base):
    __tablename__ = "product_inventory_mappings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(50), ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[str] = mapped_column(String(50), nullable=False)
    inventory_type: Mapped[str] = mapped_column(String(30), nullable=False)
    inventory_id: Mapped[str] = mapped_column(String(50), nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=func.now())

    # Add foreign key constraint for product
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "product_id"],
            ["products.tenant_id", "products.product_id"],
            ondelete="CASCADE",
        ),
        Index("idx_product_inventory_mapping", "tenant_id", "product_id"),
        UniqueConstraint(
            "tenant_id",
            "product_id",
            "inventory_type",
            "inventory_id",
            name="uq_product_inventory",
        ),
    )


class FormatPerformanceMetrics(Base):
    """Cached historical reporting metrics for dynamic pricing (AdCP PR #79).

    Stores aggregated GAM reporting data by country + creative format.
    Much simpler than product-level: GAM naturally reports COUNTRY_CODE + CREATIVE_SIZE.
    Populated by scheduled job that queries GAM ReportService.
    Used to calculate floor_cpm, recommended_cpm, and estimated_exposures dynamically.
    """

    __tablename__ = "format_performance_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(50), ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False
    )
    country_code: Mapped[str | None] = mapped_column(String(3), nullable=True)
    creative_size: Mapped[str] = mapped_column(String(20), nullable=False)

    # Time period for these metrics
    period_start: Mapped[Date] = mapped_column(Date, nullable=False)
    period_end: Mapped[Date] = mapped_column(Date, nullable=False)

    # Volume metrics from GAM reporting (COUNTRY_CODE + CREATIVE_SIZE dimensions)
    total_impressions: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    total_clicks: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    total_revenue_micros: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    # Calculated pricing metrics (in USD)
    average_cpm: Mapped[Decimal | None] = mapped_column(DECIMAL(10, 2), nullable=True)
    median_cpm: Mapped[Decimal | None] = mapped_column(DECIMAL(10, 2), nullable=True)
    p75_cpm: Mapped[Decimal | None] = mapped_column(DECIMAL(10, 2), nullable=True)
    p90_cpm: Mapped[Decimal | None] = mapped_column(DECIMAL(10, 2), nullable=True)

    # Metadata
    line_item_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_updated: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=func.now())

    # Relationships
    tenant = relationship("Tenant")

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "country_code",
            "creative_size",
            "period_start",
            "period_end",
            name="uq_format_perf_metrics",
        ),
        Index("idx_format_perf_tenant", "tenant_id"),
        Index("idx_format_perf_country_size", "country_code", "creative_size"),
        Index("idx_format_perf_period", "period_start", "period_end"),
    )


class GAMOrder(Base):
    __tablename__ = "gam_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(50), ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False
    )
    order_id: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    advertiser_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    advertiser_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    agency_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    agency_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    trafficker_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    trafficker_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    salesperson_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    salesperson_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    start_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    end_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    unlimited_end_date: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    total_budget: Mapped[float | None] = mapped_column(Float, nullable=True)
    currency_code: Mapped[str | None] = mapped_column(String(10), nullable=True)
    external_order_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    po_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_modified_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_programmatic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    applied_labels: Mapped[list | None] = mapped_column(JSONType, nullable=True)
    effective_applied_labels: Mapped[list | None] = mapped_column(JSONType, nullable=True)
    custom_field_values: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    order_metadata: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    last_synced: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=func.now(), onupdate=func.now()
    )

    # Relationships
    tenant = relationship("Tenant")
    line_items = relationship(
        "GAMLineItem",
        back_populates="order",
        foreign_keys="GAMLineItem.order_id",
        primaryjoin="and_(GAMOrder.tenant_id==GAMLineItem.tenant_id, GAMOrder.order_id==GAMLineItem.order_id)",
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "order_id", name="uq_gam_orders"),
        Index("idx_gam_orders_tenant", "tenant_id"),
        Index("idx_gam_orders_order_id", "order_id"),
        Index("idx_gam_orders_status", "status"),
        Index("idx_gam_orders_advertiser", "advertiser_id"),
    )


class GAMLineItem(Base):
    __tablename__ = "gam_line_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(50), ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False
    )
    line_item_id: Mapped[str] = mapped_column(String(50), nullable=False)
    order_id: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    line_item_type: Mapped[str] = mapped_column(String(30), nullable=False)
    priority: Mapped[int | None] = mapped_column(Integer, nullable=True)
    start_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    end_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    unlimited_end_date: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    auto_extension_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    cost_per_unit: Mapped[float | None] = mapped_column(Float, nullable=True)
    discount_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    discount: Mapped[float | None] = mapped_column(Float, nullable=True)
    contracted_units_bought: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    delivery_rate_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    goal_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    primary_goal_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    primary_goal_units: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    impression_limit: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    click_limit: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    target_platform: Mapped[str | None] = mapped_column(String(20), nullable=True)
    environment_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    allow_overbook: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    skip_inventory_check: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reserve_at_creation: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    stats_impressions: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    stats_clicks: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    stats_ctr: Mapped[float | None] = mapped_column(Float, nullable=True)
    stats_video_completions: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    stats_video_starts: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    stats_viewable_impressions: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    delivery_indicator_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    delivery_data: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    targeting: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    creative_placeholders: Mapped[list | None] = mapped_column(JSONType, nullable=True)
    frequency_caps: Mapped[list | None] = mapped_column(JSONType, nullable=True)
    applied_labels: Mapped[list | None] = mapped_column(JSONType, nullable=True)
    effective_applied_labels: Mapped[list | None] = mapped_column(JSONType, nullable=True)
    custom_field_values: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    third_party_measurement_settings: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    video_max_duration: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    line_item_metadata: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    last_modified_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    creation_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_synced: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=func.now(), onupdate=func.now()
    )

    # Relationships
    tenant = relationship("Tenant")
    order = relationship(
        "GAMOrder",
        back_populates="line_items",
        foreign_keys=[tenant_id, order_id],
        primaryjoin="and_(GAMLineItem.tenant_id==GAMOrder.tenant_id, GAMLineItem.order_id==GAMOrder.order_id)",
        overlaps="tenant",
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "line_item_id", name="uq_gam_line_items"),
        Index("idx_gam_line_items_tenant", "tenant_id"),
        Index("idx_gam_line_items_line_item_id", "line_item_id"),
        Index("idx_gam_line_items_order_id", "order_id"),
        Index("idx_gam_line_items_status", "status"),
        Index("idx_gam_line_items_type", "line_item_type"),
    )


class SyncJob(Base):
    __tablename__ = "sync_jobs"

    sync_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(50), ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False
    )
    adapter_type: Mapped[str] = mapped_column(String(50), nullable=False)
    sync_type: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    triggered_by: Mapped[str] = mapped_column(String(50), nullable=False)
    triggered_by_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    progress: Mapped[dict | None] = mapped_column(JSONType, nullable=True)  # Real-time progress tracking

    # Relationships
    tenant = relationship("Tenant")

    __table_args__ = (
        Index("idx_sync_jobs_tenant", "tenant_id"),
        Index("idx_sync_jobs_status", "status"),
        Index("idx_sync_jobs_started", "started_at"),
    )


class Context(Base):
    """Simple conversation tracker for asynchronous operations.

    For synchronous operations, no context is needed.
    For asynchronous operations, workflow_steps table is the source of truth for status.
    This just tracks the conversation history for clarifications and refinements.
    """

    __tablename__ = "contexts"

    context_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(50), ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False
    )
    principal_id: Mapped[str] = mapped_column(String(50), nullable=False)

    # Simple conversation tracking
    conversation_history: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Relationships
    tenant = relationship("Tenant")
    principal = relationship(
        "Principal",
        foreign_keys=[tenant_id, principal_id],
        primaryjoin="and_(Context.tenant_id==Principal.tenant_id, Context.principal_id==Principal.principal_id)",
        overlaps="tenant",
    )
    # Direct object relationships removed - using ObjectWorkflowMapping instead
    workflow_steps = relationship("WorkflowStep", back_populates="context", cascade="all, delete-orphan")

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "principal_id"],
            ["principals.tenant_id", "principals.principal_id"],
            ondelete="CASCADE",
        ),
        Index("idx_contexts_tenant", "tenant_id"),
        Index("idx_contexts_principal", "principal_id"),
        Index("idx_contexts_last_activity", "last_activity_at"),
    )


class WorkflowStep(Base, JSONValidatorMixin):
    """Represents an individual step/task in a workflow.

    This serves as a work queue where each step can be queried, updated, and tracked independently.
    Steps represent tool calls, approvals, notifications, etc.
    """

    __tablename__ = "workflow_steps"

    # SQLAlchemy 2.0 style with Mapped[] annotations for proper type inference
    step_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    context_id: Mapped[str] = mapped_column(
        String(100),
        ForeignKey("contexts.context_id", ondelete="CASCADE"),
    )
    step_type: Mapped[str] = mapped_column(String(50))  # tool_call, approval, notification, etc.
    tool_name: Mapped[str | None] = mapped_column(String(100))  # MCP tool name if applicable
    request_data: Mapped[dict | None] = mapped_column(JSONType)  # Original request JSON
    response_data: Mapped[dict | None] = mapped_column(JSONType)  # Response/result JSON
    status: Mapped[str] = mapped_column(
        String(20), default="pending"
    )  # pending, in_progress, completed, failed, requires_approval
    owner: Mapped[str] = mapped_column(String(20))  # principal, publisher, system
    assigned_to: Mapped[str | None] = mapped_column(String(255))  # Specific user/system if assigned
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)
    transaction_details: Mapped[dict | None] = mapped_column(JSONType)  # Actual API calls made to GAM, etc.
    comments: Mapped[list] = mapped_column(JSONType, default=list)  # Array of {user, timestamp, comment} objects

    # Relationships
    context = relationship("Context", back_populates="workflow_steps")
    object_mappings = relationship(
        "ObjectWorkflowMapping",
        back_populates="workflow_step",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("idx_workflow_steps_context", "context_id"),
        Index("idx_workflow_steps_status", "status"),
        Index("idx_workflow_steps_owner", "owner"),
        Index("idx_workflow_steps_assigned", "assigned_to"),
        Index("idx_workflow_steps_created", "created_at"),
    )


class ObjectWorkflowMapping(Base):
    """Maps workflow steps to business objects throughout their lifecycle.

    This allows tracking all CRUD operations and workflow steps for any object
    (media_buy, creative, product, etc.) without tight coupling.

    Example: Query for 'media_buy', '1234' to see every action taken over its lifecycle.
    """

    __tablename__ = "object_workflow_mapping"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    object_type: Mapped[str] = mapped_column(String(50), nullable=False)
    object_id: Mapped[str] = mapped_column(String(100), nullable=False)
    step_id: Mapped[str] = mapped_column(
        String(100),
        ForeignKey("workflow_steps.step_id", ondelete="CASCADE"),
        nullable=False,
    )
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    # Relationships
    workflow_step = relationship("WorkflowStep", back_populates="object_mappings")

    __table_args__ = (
        Index("idx_object_workflow_type_id", "object_type", "object_id"),
        Index("idx_object_workflow_step", "step_id"),
        Index("idx_object_workflow_created", "created_at"),
    )


class Strategy(Base, JSONValidatorMixin):
    """Strategy definitions for both production and simulation contexts.

    Strategies define behavior patterns for campaigns and simulations.
    Production strategies control pacing, bidding, and optimization.
    Simulation strategies (prefix 'sim_') enable testing scenarios.
    """

    __tablename__ = "strategies"

    strategy_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    tenant_id: Mapped[str | None] = mapped_column(
        String(50), ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=True
    )
    principal_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    config: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    is_simulation: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    tenant = relationship("Tenant", back_populates="strategies", overlaps="strategies,tenant")
    principal = relationship("Principal", back_populates="strategies", overlaps="strategies,tenant")
    states = relationship("StrategyState", back_populates="strategy", cascade="all, delete-orphan")
    media_buys = relationship("MediaBuy", back_populates="strategy")

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "principal_id"], ["principals.tenant_id", "principals.principal_id"], ondelete="CASCADE"
        ),
        Index("idx_strategies_tenant", "tenant_id"),
        Index("idx_strategies_principal", "tenant_id", "principal_id"),
        Index("idx_strategies_simulation", "is_simulation"),
    )

    @property
    def is_production_strategy(self) -> bool:
        """Check if this is a production (non-simulation) strategy."""
        return not self.is_simulation

    def get_config_value(self, key: str, default=None):
        """Get a configuration value with fallback."""
        return self.config.get(key, default) if self.config else default


class StrategyState(Base, JSONValidatorMixin):
    """Persistent state storage for simulation strategies.

    Stores simulation state like current time, triggered events,
    media buy states, etc. Enables pause/resume of simulations.
    """

    __tablename__ = "strategy_states"

    strategy_id: Mapped[str] = mapped_column(String(255), nullable=False, primary_key=True)
    state_key: Mapped[str] = mapped_column(String(255), nullable=False, primary_key=True)
    state_value: Mapped[dict] = mapped_column(JSONType, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    strategy = relationship("Strategy", back_populates="states")

    __table_args__ = (
        ForeignKeyConstraint(["strategy_id"], ["strategies.strategy_id"], ondelete="CASCADE"),
        Index("idx_strategy_states_id", "strategy_id"),
    )


class AuthorizedProperty(Base, JSONValidatorMixin):
    """Properties (websites, apps, etc.) that this agent is authorized to represent.

    Used for the list_authorized_properties AdCP endpoint.
    Stores property details and verification status.
    """

    __tablename__ = "authorized_properties"

    property_id: Mapped[str] = mapped_column(String(100), nullable=False, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(50), nullable=False, primary_key=True)
    property_type: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    identifiers: Mapped[list[dict]] = mapped_column(JSONType, nullable=False)
    tags: Mapped[list[str] | None] = mapped_column(JSONType, nullable=True)
    publisher_domain: Mapped[str] = mapped_column(String(255), nullable=False)
    verification_status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    verification_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verification_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    tenant = relationship("Tenant", backref="authorized_properties")

    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
        CheckConstraint(
            "property_type IN ('website', 'mobile_app', 'ctv_app', 'dooh', 'podcast', 'radio', 'streaming_audio')",
            name="ck_property_type",
        ),
        CheckConstraint("verification_status IN ('pending', 'verified', 'failed')", name="ck_verification_status"),
        Index("idx_authorized_properties_tenant", "tenant_id"),
        Index("idx_authorized_properties_domain", "publisher_domain"),
        Index("idx_authorized_properties_type", "property_type"),
        Index("idx_authorized_properties_verification", "verification_status"),
    )


class AdvertiserRoutingRule(Base):
    """Sprint 1.8 — buyer-advertiser routing rules.

    Ordered overrides keyed by ``(operator_domain, brand_house, brand_id)``
    with NULL-as-wildcard. The resolution chain in
    :mod:`src.services.buyer_advertiser_routing` reads these rows
    when a buy comes in carrying inline ``account: AccountReference``
    (operator + brand + sandbox triple). Precedence: exact → house
    wildcard → operator wildcard → tenant default → reject.
    """

    __tablename__ = "advertiser_routing_rules"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)  # "rule_<random>"
    tenant_id: Mapped[str] = mapped_column(
        String(50), ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False
    )
    # Sprint 5 — buyer agent (Principal) the rule applies to. NULL = "any
    # agent" (preserves Sprint 1.8 behavior; required for backward
    # compatibility with rows that predate this column). Embedded tenants
    # leave this NULL since the host is the only buyer.
    principal_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # Buyer agent's domain (e.g. interchange.io, buyer.scope3.com).
    # Validated AAO-side on POST — must publish a valid adagents.json
    # listing this tenant's public_agent_url. Always populated.
    operator_domain: Mapped[str] = mapped_column(String(255), nullable=False)
    # Parent brand domain (e.g. coca-cola.com). NULL = "any house under
    # this operator" (operator wildcard).
    brand_house: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Specific brand within the house (e.g. sprite). NULL = "any brand
    # under this house" (house wildcard).
    brand_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Target advertiser. Validated against the synced gam advertisers
    # cache on POST/PATCH (must reference a real advertiser in the
    # tenant's GAM network).
    gam_advertiser_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    tenant = relationship("Tenant", backref="advertiser_routing_rules")

    __table_args__ = (
        # Uniqueness on the natural key with NULLs participating via
        # COALESCE coercion (Postgres treats NULL as distinct in UNIQUE
        # by default). Mirrors the index alembic creates in
        # 0042_e7a4c2b9d5f1; declared here so Base.metadata.create_all()
        # (used by integration tests) builds the same constraint.
        Index(
            "uq_routing_rule_natural_key",
            "tenant_id",
            text("COALESCE(principal_id, '')"),
            "operator_domain",
            text("COALESCE(brand_house, '')"),
            text("COALESCE(brand_id, '')"),
            unique=True,
        ),
        Index("idx_routing_rules_tenant", "tenant_id"),
        Index("idx_routing_rules_operator", "tenant_id", "operator_domain"),
    )


class GamAdvertiser(Base):
    """Sprint 5 piece D — synced cache of GAM advertisers per tenant.

    Powers the searchable picker in the Buyer Routing UI (default
    advertiser + routing-rule rows). Hydrated by the
    ``sync_advertisers`` worker reading
    ``CompanyService.getCompaniesByStatement WHERE type = 'ADVERTISER'``.
    The endpoint ``GET /tenants/{id}/gam/advertisers`` reads from this
    cache, never from live GAM (10k+ advertiser networks make a per-
    keystroke round-trip prohibitively slow).

    Soft-delete on disappearance: advertisers that drop out of GAM are
    flagged ``status='inactive'`` rather than hard-deleted, because
    routing rules might reference them. The picker hides inactive rows
    by default; the routing-rule editor surfaces a warning if a rule
    points at an inactive advertiser.
    """

    __tablename__ = "gam_advertisers"

    tenant_id: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    advertiser_id: Mapped[str] = mapped_column(String(64), primary_key=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    currency_code: Mapped[str | None] = mapped_column(String(3), nullable=True)
    # GAM company status surfaced verbatim ("active", "inactive") so
    # the picker can render badges without a translation layer.
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    # Buyer agent (Principal) assigned to this advertiser. NULL means no
    # agent has claimed it yet — orders for this advertiser are not
    # surfaced to any buyer via get_media_buys. Set via the buyer
    # mapping UI.
    principal_id: Mapped[str | None] = mapped_column(String(50), nullable=True)

    tenant = relationship("Tenant", backref="gam_advertisers")

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "principal_id"],
            ["principals.tenant_id", "principals.principal_id"],
            ondelete="SET NULL",
            name="fk_gam_advertisers_principal",
        ),
        Index("idx_gam_advertisers_tenant", "tenant_id"),
        # Compound index supports the case-insensitive name search in
        # GET /gam/advertisers. Mirrors the migration index so
        # Base.metadata.create_all() (used by integration tests) builds
        # the same constraint.
        Index("idx_gam_advertisers_name", "tenant_id", "name"),
        Index(
            "idx_gam_advertisers_principal",
            "tenant_id",
            "principal_id",
            postgresql_where=text("principal_id IS NOT NULL"),
        ),
    )


class PropertyTag(Base, JSONValidatorMixin):
    """Metadata for property tags used in authorized properties.

    Provides human-readable names and descriptions for tags
    referenced in the list_authorized_properties response.
    """

    __tablename__ = "property_tags"

    tag_id: Mapped[str] = mapped_column(String(50), nullable=False, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(50), nullable=False, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    tenant = relationship("Tenant", backref="property_tags")

    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
        Index("idx_property_tags_tenant", "tenant_id"),
    )


class FreeWheelInventory(Base, JSONValidatorMixin):
    """Local cache of FreeWheel inventory taxonomy.

    Stores FW Sites, SiteSections, SiteGroups, Series, VideoGroups,
    AdUnitPackages, AdUnitNodes, and StandardAttributes as JSON-blob rows
    keyed by ``(tenant_id, entity_type, entity_id)``. The cache is used by
    the FreeWheel adapter's product configuration UI so publishers can
    pick targeting from their FW inventory without round-tripping to the
    FW API on every page render.

    NOT exposed to AdCP buyers — buyer-facing property discovery goes
    through the AAO lookup path (adagents.json + brand.json). This is a
    private adapter-side cache.

    Refreshed on demand via the adapter settings "Sync Inventory" button.
    """

    __tablename__ = "freewheel_inventory"

    tenant_id: Mapped[str] = mapped_column(String(50), nullable=False, primary_key=True)
    entity_type: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        primary_key=True,
        comment=(
            "FW entity kind: site, site_section, site_group, series, "
            "video_group, ad_unit_package, ad_unit_node, standard_attribute"
        ),
    )
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False, primary_key=True)
    name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    parent_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    raw_json: Mapped[dict] = mapped_column(JSONType, nullable=False)
    last_synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    # Relationships
    tenant = relationship("Tenant", backref="freewheel_inventory")

    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
        Index("idx_freewheel_inventory_tenant_type", "tenant_id", "entity_type"),
        Index("idx_freewheel_inventory_parent", "tenant_id", "parent_id"),
    )


class FreeWheelPlacementStats(Base):
    """Per-placement delivery stats cache for the FreeWheel adapter.

    Populated by a periodic Query Reporting API sync (pending Tier 2 FW
    scope grant — see docs/adapters/freewheel/README.md). Read by
    ``FreeWheelAdapter.get_packages_snapshot`` and
    ``get_media_buy_delivery`` so AdCP delivery surfaces can serve results
    without round-tripping to FreeWheel on every request.

    Stays empty until the reporting sync is wired up. Adapter reads are
    defensive — missing rows surface as ``None`` snapshots / zero
    delivery, never errors.

    Spend is stored as currency-minor-unit micros (1 EUR = 1_000_000
    micros) to avoid floating-point precision loss when aggregating.
    """

    __tablename__ = "freewheel_placement_stats"

    tenant_id: Mapped[str] = mapped_column(String(50), nullable=False, primary_key=True)
    placement_id: Mapped[str] = mapped_column(String(64), nullable=False, primary_key=True)
    insertion_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    impressions: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    completed_views: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    clicks: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    spend_micros: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    delivery_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    tenant = relationship("Tenant", backref="freewheel_placement_stats")

    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
        Index("idx_fw_placement_stats_tenant_io", "tenant_id", "insertion_order_id"),
    )


class SpringServeInventory(Base, JSONValidatorMixin):
    """Local cache of SpringServe inventory taxonomy.

    Stores SpringServe Supply Partners, Supply Tags, Supply Groups, and
    Accounts as JSON-blob rows keyed by ``(tenant_id, entity_type,
    entity_id)``. The cache is used by the SpringServe adapter's product
    configuration UI so publishers can pick targeting from their
    SpringServe inventory without round-tripping to the SpringServe API
    on every page render.

    NOT exposed to AdCP buyers -- buyer-facing property discovery goes
    through the AAO lookup path (adagents.json + brand.json). This is
    a private adapter-side cache.

    Refreshed on demand via the adapter settings "Sync Inventory" button
    or the periodic AdapterSyncScheduler.
    """

    __tablename__ = "springserve_inventory"

    tenant_id: Mapped[str] = mapped_column(String(50), nullable=False, primary_key=True)
    entity_type: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        primary_key=True,
        comment=("SpringServe entity kind: supply_partner, supply_router, supply_tag, key, value_list"),
    )
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False, primary_key=True)
    name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # Explicit foreign-key columns rather than a polymorphic ``parent_id``.
    # Each row populates only the FKs that apply to its entity_type:
    #   supply_partner row  -> all FKs NULL
    #   supply_router row   -> supply_partner_id set
    #   supply_tag row      -> supply_partner_id set; supply_router_id set
    #                         iff the tag belongs to a router (orphans allowed)
    #   key row             -> all FKs NULL
    #   value_list row      -> key_id set
    supply_partner_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    supply_router_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    key_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    raw_json: Mapped[dict] = mapped_column(JSONType, nullable=False)
    last_synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    tenant = relationship("Tenant", backref="springserve_inventory")

    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
        Index("idx_springserve_inventory_tenant_type", "tenant_id", "entity_type"),
        Index("idx_springserve_inventory_router", "tenant_id", "supply_router_id"),
        Index("idx_springserve_inventory_key", "tenant_id", "key_id"),
    )


class SpringServeDemandTagStats(Base):
    """Per-demand-tag delivery stats cache for the SpringServe adapter.

    Populated by a periodic Reporting API sync (Stage 4 -- the sync
    writer is wired but the SpringServe Reporting scope grant lands
    separately). Read by ``SpringServeAdapter.get_packages_snapshot``
    and ``get_media_buy_delivery`` so AdCP delivery surfaces serve
    results without round-tripping to SpringServe on every request.

    Stays empty until the reporting sync is wired and scope is granted.
    Adapter reads raise ``DeliveryDataUnavailable`` on empty cache --
    we don't fabricate zeros for missing data.

    Spend is stored as currency-minor-unit micros (1 EUR = 1_000_000
    micros) to avoid floating-point precision loss when aggregating.
    """

    __tablename__ = "springserve_demand_tag_stats"

    tenant_id: Mapped[str] = mapped_column(String(50), nullable=False, primary_key=True)
    demand_tag_id: Mapped[str] = mapped_column(String(64), nullable=False, primary_key=True)
    campaign_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    impressions: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    completed_views: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    clicks: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    spend_micros: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    delivery_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    tenant = relationship("Tenant", backref="springserve_demand_tag_stats")

    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
        Index("idx_ss_demand_tag_stats_tenant_campaign", "tenant_id", "campaign_id"),
    )


class PublisherPartner(Base, JSONValidatorMixin):
    """Publisher domains that this tenant has partnerships with.

    Tracks which publishers the tenant works with and verification status
    of whether the tenant's agent is listed in each publisher's adagents.json.

    The actual property IDs/tags are fetched fresh from adagents.json (not cached),
    but this table tracks partnership status and last sync time.
    """

    __tablename__ = "publisher_partners"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(50), nullable=False)
    publisher_domain: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sync_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", comment="pending, success, error"
    )
    sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # AAO status counts — populated by sync_publisher_partners + the per-row
    # refresh endpoint. Drive the "47 / 200 authorized" UI without re-hitting
    # AAO on every page render.
    total_properties: Mapped[int | None] = mapped_column(Integer, nullable=True)
    authorized_properties: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_refreshed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_fetch_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Literal PublisherPartnerStatusKind ("authorized" | "unbound" |
    # "pending" | "no_properties" | "unreachable"). Persisted so
    # _partner_to_dict can render the operationally-different post-fetch
    # states the prior derivation collapsed together (e.g. "unbound"
    # publisher whose products bind permissively vs "no_properties"
    # publisher who has no inventory at all). See salesagent#377. NULL on
    # rows that haven't been refreshed since the column was added.
    aao_status_kind: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    tenant = relationship("Tenant", backref="publisher_partners")

    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
        UniqueConstraint("tenant_id", "publisher_domain", name="uq_tenant_publisher"),
        CheckConstraint("sync_status IN ('pending', 'success', 'error')", name="ck_sync_status"),
        Index("idx_publisher_partners_tenant", "tenant_id"),
        Index("idx_publisher_partners_domain", "publisher_domain"),
        Index("idx_publisher_partners_verified", "is_verified"),
    )


class PushNotificationConfig(Base, JSONValidatorMixin):
    """Push notification configuration for async operation callbacks.

    Stores buyer-provided webhook URLs where the server should POST
    notifications when task status changes (e.g., submitted → completed).
    Supports multiple authentication methods (bearer, basic, none).
    """

    __tablename__ = "push_notification_configs"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(50), nullable=False)
    principal_id: Mapped[str] = mapped_column(String(50), nullable=False)
    session_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    operation_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    account_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    subscriber_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    event_types: Mapped[list[str] | None] = mapped_column(JSONType, nullable=True)
    authentication_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    authentication_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    validation_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    webhook_secret: Mapped[str | None] = mapped_column(String(500), nullable=True)
    purpose: Mapped[str] = mapped_column(
        String(32), nullable=False, default="async_task", server_default=text("'async_task'")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    auth_blocked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    signing_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="hmac", server_default=text("'hmac'"))

    # Relationships
    tenant = relationship("Tenant", backref="push_notification_configs", overlaps="principal")
    principal = relationship(
        "Principal",
        back_populates="push_notification_configs",
        overlaps="push_notification_configs,tenant",
        foreign_keys=[tenant_id, principal_id],
    )

    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
        ForeignKeyConstraint(
            ["tenant_id", "principal_id"], ["principals.tenant_id", "principals.principal_id"], ondelete="CASCADE"
        ),
        Index("idx_push_notification_configs_tenant", "tenant_id"),
        Index("idx_push_notification_configs_principal", "tenant_id", "principal_id"),
        Index("idx_push_notification_configs_account", "tenant_id", "account_id"),
    )

    def __repr__(self):
        return (
            f"<PushNotificationConfig("
            f"id='{self.id}', "
            f"tenant_id='{self.tenant_id}', "
            f"principal_id='{self.principal_id}', "
            f"session_id='{self.session_id}', "
            f"url='{self.url}', "
            f"operation_id='{self.operation_id}', "
            f"account_id='{self.account_id}', "
            f"subscriber_id='{self.subscriber_id}', "
            f"purpose='{self.purpose}', "
            f"authentication_type='{self.authentication_type}', "
            f"authentication_token='***', "
            f"validation_token='***', "
            f"webhook_secret='***', "
            f"is_active={self.is_active}, "
            f"created_at={self.created_at}, "
            f"updated_at={self.updated_at}"
            f")>"
        )


class WebhookDeliveryRecord(Base):
    """Tracks webhook delivery attempts with retry history.

    Records all webhook POST requests for audit and debugging purposes.
    Enables tracking of delivery success rates, retry patterns, and failures.
    """

    __tablename__ = "webhook_deliveries"

    delivery_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(50), ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False
    )
    webhook_url: Mapped[str] = mapped_column(String(500), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONType, nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    object_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Delivery tracking
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Error tracking
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_code: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    # Relationships
    tenant = relationship("Tenant")

    __table_args__ = (
        Index("idx_webhook_deliveries_tenant", "tenant_id"),
        Index("idx_webhook_deliveries_status", "status"),
        Index("idx_webhook_deliveries_event_type", "event_type"),
        Index("idx_webhook_deliveries_object_id", "object_id"),
        Index("idx_webhook_deliveries_created", "created_at"),
    )


class WebhookDeliveryLog(Base):
    """Tracks delivery report webhook sends for AdCP compliance.

    Records each delivery_report webhook notification with sequence tracking,
    retry history, and performance metrics. Used to demonstrate compliance
    with buyer webhook notification requirements.
    """

    __tablename__ = "webhook_delivery_log"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String, nullable=False)
    principal_id: Mapped[str] = mapped_column(String, nullable=False)
    media_buy_id: Mapped[str] = mapped_column(
        String, ForeignKey("media_buys.media_buy_id", ondelete="CASCADE"), nullable=False
    )
    webhook_url: Mapped[str] = mapped_column(String, nullable=False)
    task_type: Mapped[str] = mapped_column(String, nullable=False)  # "media_buy_delivery"

    # AdCP webhook metadata
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    notification_type: Mapped[str | None] = mapped_column(
        String, nullable=True
    )  # "scheduled", "final", "delayed", "adjusted"

    # Retry tracking
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    status: Mapped[str] = mapped_column(String, nullable=False)  # "success", "failed", "retrying"
    http_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Performance metrics
    payload_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Body capture for buyer-side debug visibility (#101). Truncated to
    # ~64KB at insert time by the persistence helper. Both nullable:
    # older rows pre-date these columns, and non-HTTP failures
    # (connection refused, timeout) have no response_body.
    request_payload: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    response_body: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    tenant = relationship("Tenant")
    principal = relationship("Principal", overlaps="tenant")
    media_buy = relationship("MediaBuy")

    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
        ForeignKeyConstraint(
            ["tenant_id", "principal_id"], ["principals.tenant_id", "principals.principal_id"], ondelete="CASCADE"
        ),
        Index("idx_webhook_log_media_buy", "media_buy_id"),
        Index("idx_webhook_log_tenant", "tenant_id"),
        Index("idx_webhook_log_status", "status"),
        Index("idx_webhook_log_created_at", "created_at"),
    )


# ---------------------------------------------------------------------------
# Signing (non-embedded mode) — per-buyer-agent trust model
# ---------------------------------------------------------------------------
# See docs/design/signing-non-embedded.md (rev 5). Each buyer-protocol
# Principal optionally carries an ``agent_url``. When set, the verifier
# fetches JWKS from ``<agent_url>/.well-known/jwks.json`` and verifies inbound
# signatures. The salesagent trusts the buyer agent for its operator
# identity — no brand.json chain walk, no operator-attestation step. Operators
# and brands are orthogonal dimensions of Accounts (Account.{operator, brand,
# billing}), NOT auth subjects.


class TenantSigningPolicy(Base):
    """Per-tenant request-signing policy. One row per tenant."""

    __tablename__ = "tenant_signing_policy"

    tenant_id: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"),
        primary_key=True,
    )
    # Master switch — when False the verifier never runs.
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # List of AdCP operation names that REQUIRE a signature. Empty list => signing optional.
    required_for: Mapped[list[str]] = mapped_column(JSONType, nullable=False, default=list)
    # required | forbidden | either
    covers_digest_policy: Mapped[str] = mapped_column(String(16), nullable=False, default="either")
    max_skew_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    max_window_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        CheckConstraint(
            "covers_digest_policy IN ('required', 'forbidden', 'either')",
            name="chk_tenant_signing_policy_digest",
        ),
    )


class TenantSigningCredential(Base):
    """Salesagent's own outbound signing key reference.

    Stores a KMS reference (or local PEM file path in dev) plus the cached public
    JWK so the admin UI can render a copy-paste-ready brand.json snippet for the
    operator to publish at their house_domain. Private bytes never live in our
    process for KMS-backed credentials.
    """

    __tablename__ = "tenant_signing_credentials"

    tenant_id: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"),
        primary_key=True,
    )
    purpose: Mapped[str] = mapped_column(String(64), primary_key=True)
    # local_pem | gcp_kms | aws_kms | hashicorp_vault
    backend: Mapped[str] = mapped_column(String(32), nullable=False)
    # KMS key resource name OR file path; format depends on backend.
    backend_ref: Mapped[str] = mapped_column(String(1024), nullable=False)
    public_jwk: Mapped[dict] = mapped_column(JSONType, nullable=False)
    key_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    rotated_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "backend IN ('local_pem', 'gcp_kms', 'aws_kms', 'hashicorp_vault')",
            name="chk_tenant_signing_credentials_backend",
        ),
        Index(
            "idx_tenant_signing_credentials_active",
            "tenant_id",
            "purpose",
            "is_active",
            postgresql_where=text("is_active"),
        ),
        # At-most-one-active per (tenant, purpose). Mirrored in the
        # alembic migration so production gets the same enforcement.
        # Without this, two concurrent admin sessions both creating an
        # active credential succeed; the snapshot loader then picks one
        # arbitrarily and the operator-published JWKS may list the other.
        Index(
            "ux_tenant_signing_credentials_active",
            "tenant_id",
            "purpose",
            unique=True,
            postgresql_where=text("is_active = TRUE"),
        ),
    )


class Proposal(Base):
    """``proposals`` table schema mirror — table-creation-only declaration.

    The runtime owner of this table is
    :class:`adcp.decisioning.pg.proposal_store.PgProposalStore` (adcp
    5.5.0), which speaks raw psycopg3 with explicit-column INSERTs.
    salesagent code MUST NOT query through this ORM model — every read
    and write goes through :func:`core.decisioning.proposal_store.get_proposal_store`.

    This class exists so :data:`Base.metadata` knows the table when
    :func:`sqlalchemy.MetaData.create_all` runs (every integration test
    fixture rebuilds per-test DBs via ``Base.metadata.create_all``,
    bypassing Alembic). Without it, the table is missing from the test
    DB and ``PgProposalStore`` raises ``UndefinedTable`` on every
    proposal-path dispatch.

    Production runs Alembic (migration ``t2u3v4w5x6y7``), which is the
    source of truth for the schema. The column types here are chosen
    to produce the same DDL ``create_all`` would emit — namely they
    omit ``COLLATE "C"`` (test-only perf optimization) but otherwise
    match. The ``CheckConstraint`` on ``state`` and the partial unique
    + partial expires_at indexes are replicated so test DBs match
    production behavior for any SQL we ever might add against this
    table (we don't today, but the principle stands).

    The generated ``tenant_id`` column is declared via
    :class:`sqlalchemy.Computed` so SQLAlchemy emits the matching
    ``GENERATED ALWAYS AS ... STORED`` clause. The FK + cascade to
    ``tenants`` is preserved.
    """

    __tablename__ = "proposals"

    account_id: Mapped[str] = mapped_column(Text, primary_key=True)
    proposal_id: Mapped[str] = mapped_column(Text, primary_key=True)
    state: Mapped[str] = mapped_column(Text, nullable=False)
    recipes: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    proposal_payload: Mapped[dict] = mapped_column(JSONType, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    media_buy_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    recipe_schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    tenant_id: Mapped[str] = mapped_column(
        String(50),
        Computed("split_part(account_id, ':', 1)", persisted=True),
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"),
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint(
            "state IN ('draft', 'committed', 'consuming', 'consumed')",
            name="ck_proposals_state",
        ),
        Index(
            "ux_proposals_account_media_buy",
            "account_id",
            "media_buy_id",
            unique=True,
            postgresql_where=text("media_buy_id IS NOT NULL"),
        ),
        Index(
            "ix_proposals_expires_at",
            "expires_at",
            postgresql_where=text("expires_at IS NOT NULL"),
        ),
        Index("ix_proposals_tenant_id", "tenant_id"),
    )
