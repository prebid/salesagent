"""SQLAlchemy models for database schema."""

import logging
from decimal import Decimal

from sqlalchemy import (
    DECIMAL,
    BigInteger,
    Boolean,
    CheckConstraint,
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
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from src.core.database.json_type import JSONType
from src.core.json_validators import JSONValidatorMixin

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models using SQLAlchemy 2.0 declarative style."""

    pass


class Tenant(Base, JSONValidatorMixin):
    __tablename__ = "tenants"

    tenant_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    subdomain: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    virtual_host: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
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
    auto_approve_formats: Mapped[list[str] | None] = mapped_column(JSONType, nullable=True)
    human_review_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    policy_settings: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    signals_agent_config: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    creative_review_criteria: Mapped[str | None] = mapped_column(Text, nullable=True)
    _gemini_api_key: Mapped[str | None] = mapped_column("gemini_api_key", String(500), nullable=True)
    approval_mode: Mapped[str] = mapped_column(String(50), nullable=False, default="require-human")
    ai_policy: Mapped[dict | None] = mapped_column(
        JSONType, nullable=True, comment="AI review policy configuration with confidence thresholds"
    )
    advertising_policy: Mapped[dict | None] = mapped_column(
        JSONType,
        nullable=True,
        comment="Advertising policy configuration with prohibited categories, tactics, and advertisers",
    )

    # Naming templates (business rules - shared across all adapters)
    order_name_template: Mapped[str | None] = mapped_column(
        String(500), nullable=True, server_default="{campaign_name|brand_name} - {buyer_ref} - {date_range}"
    )
    line_item_name_template: Mapped[str | None] = mapped_column(
        String(500), nullable=True, server_default="{order_name} - {product_name}"
    )

    # Relationships
    products = relationship("Product", back_populates="tenant", cascade="all, delete-orphan")
    principals = relationship("Principal", back_populates="tenant", cascade="all, delete-orphan")
    users = relationship("User", back_populates="tenant", cascade="all, delete-orphan")
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

    __table_args__ = (
        Index("idx_subdomain", "subdomain"),
        Index("ix_tenants_virtual_host", "virtual_host", unique=True),
    )

    # JSON validators are inherited from JSONValidatorMixin
    # No need for duplicate validators here

    @property
    def gemini_api_key(self) -> str | None:
        """Get decrypted Gemini API key."""
        if not self._gemini_api_key:
            return None
        from src.core.utils.encryption import decrypt_api_key

        try:
            return decrypt_api_key(self._gemini_api_key)
        except ValueError:
            logger.warning(f"Failed to decrypt Gemini API key for tenant {self.tenant_id}")
            return None

    @gemini_api_key.setter
    def gemini_api_key(self, value: str | None) -> None:
        """Set encrypted Gemini API key."""
        if not value:
            self._gemini_api_key = None
            return

        from src.core.utils.encryption import encrypt_api_key

        self._gemini_api_key = encrypt_api_key(value)


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
    formats: Mapped[list[dict[str, str]]] = mapped_column(JSONType, nullable=False)
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
    expires_at: Mapped[DateTime | None] = mapped_column(DateTime, nullable=True)
    # Type hint: countries list
    countries: Mapped[list[str] | None] = mapped_column(JSONType, nullable=True)
    # Type hint: implementation config dict
    implementation_config: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    # AdCP property authorization fields (at least one required per spec)
    # Type hint: list of Property dicts for validation
    properties: Mapped[list[dict] | None] = mapped_column(JSONType, nullable=True)
    # Type hint: list of tag strings
    property_tags: Mapped[list[str] | None] = mapped_column(JSONType, nullable=True)
    # Note: PR #79 fields (estimated_exposures, floor_cpm, recommended_cpm) are NOT stored in database
    # They are calculated dynamically from product_performance_metrics table

    # Relationships
    tenant = relationship("Tenant", back_populates="products")
    # No SQLAlchemy cascade - let database CASCADE handle pricing_options deletion
    # This avoids triggering the prevent_empty_pricing_options constraint
    # Use passive_deletes=True to tell SQLAlchemy to rely on database CASCADE
    pricing_options = relationship("PricingOption", back_populates="product", passive_deletes=True)

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

    created_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
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
    access_token: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    created_at: Mapped[DateTime] = mapped_column(DateTime, server_default=func.now())

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
    )


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
    created_at: Mapped[DateTime] = mapped_column(DateTime, server_default=func.now())
    last_login: Mapped[DateTime | None] = mapped_column(DateTime, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Relationships
    tenant = relationship("Tenant", back_populates="users")

    __table_args__ = (
        CheckConstraint("role IN ('admin', 'manager', 'viewer')"),
        UniqueConstraint("tenant_id", "email", name="uq_users_tenant_email"),  # Unique per tenant
        Index("idx_users_tenant", "tenant_id"),
        Index("idx_users_email", "email"),
        Index("idx_users_google_id", "google_id"),
    )


class Creative(Base):
    """Creative database model matching the actual creatives table schema."""

    __tablename__ = "creatives"

    creative_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(50), ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False
    )
    principal_id: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    agent_url: Mapped[str] = mapped_column(String(500), nullable=False)
    format: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending")

    # Data field stores creative content and metadata as JSON
    data: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)

    # Relationships and metadata
    group_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[DateTime | None] = mapped_column(
        DateTime, nullable=True, server_default=func.current_timestamp()
    )
    updated_at: Mapped[DateTime | None] = mapped_column(DateTime, nullable=True)
    approved_at: Mapped[DateTime | None] = mapped_column(DateTime, nullable=True)
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
    creative_id: Mapped[str] = mapped_column(
        String(100), ForeignKey("creatives.creative_id", ondelete="CASCADE"), nullable=False
    )
    tenant_id: Mapped[str] = mapped_column(
        String(50), ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False
    )

    # Review metadata
    reviewed_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False, server_default=func.now())
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
    tenant = relationship("Tenant")

    __table_args__ = (
        Index("ix_creative_reviews_creative_id", "creative_id"),
        Index("ix_creative_reviews_tenant_id", "tenant_id"),
        Index("ix_creative_reviews_reviewed_at", "reviewed_at"),
        Index("ix_creative_reviews_review_type", "review_type"),
        Index("ix_creative_reviews_final_decision", "final_decision"),
    )


class CreativeAssignment(Base):
    """Creative assignments to media buy packages."""

    __tablename__ = "creative_assignments"

    assignment_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(50), ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False
    )
    creative_id: Mapped[str] = mapped_column(String(100), nullable=False)
    media_buy_id: Mapped[str] = mapped_column(String(100), nullable=False)
    package_id: Mapped[str] = mapped_column(String(100), nullable=False)
    weight: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    created_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False, server_default=func.now())

    # Relationships
    tenant = relationship("Tenant")

    __table_args__ = (
        ForeignKeyConstraint(["creative_id"], ["creatives.creative_id"]),
        ForeignKeyConstraint(["media_buy_id"], ["media_buys.media_buy_id"]),
        Index("idx_creative_assignments_tenant", "tenant_id"),
        Index("idx_creative_assignments_creative", "creative_id"),
        Index("idx_creative_assignments_media_buy", "media_buy_id"),
        UniqueConstraint("tenant_id", "creative_id", "media_buy_id", "package_id", name="uq_creative_assignment"),
    )


class MediaBuy(Base):
    __tablename__ = "media_buys"

    media_buy_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(50), ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False
    )
    principal_id: Mapped[str] = mapped_column(String(50), nullable=False)
    buyer_ref: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    order_name: Mapped[str] = mapped_column(String(255), nullable=False)
    advertiser_name: Mapped[str] = mapped_column(String(255), nullable=False)
    campaign_objective: Mapped[str | None] = mapped_column(String(100), nullable=True)
    kpi_goal: Mapped[str | None] = mapped_column(String(255), nullable=True)
    budget: Mapped[Decimal | None] = mapped_column(DECIMAL(15, 2))
    currency: Mapped[str] = mapped_column(String(3), nullable=True, default="USD")
    start_date: Mapped[Date] = mapped_column(Date, nullable=False)
    end_date: Mapped[Date] = mapped_column(Date, nullable=False)
    start_time: Mapped[DateTime | None] = mapped_column(DateTime, nullable=True)
    end_time: Mapped[DateTime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    created_at: Mapped[DateTime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    approved_at: Mapped[DateTime | None] = mapped_column(DateTime, nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    raw_request: Mapped[dict] = mapped_column(JSONType, nullable=False)
    strategy_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Relationships
    tenant = relationship("Tenant", back_populates="media_buys", overlaps="media_buys")
    principal = relationship(
        "Principal",
        foreign_keys=[tenant_id, principal_id],
        primaryjoin="and_(MediaBuy.tenant_id==Principal.tenant_id, MediaBuy.principal_id==Principal.principal_id)",
        overlaps="media_buys,tenant",
    )
    strategy = relationship("Strategy", back_populates="media_buys")
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
        UniqueConstraint(
            "tenant_id",
            "principal_id",
            "buyer_ref",
            name="uq_media_buys_buyer_ref",
        ),
        Index("idx_media_buys_tenant", "tenant_id"),
        Index("idx_media_buys_status", "status"),
        Index("idx_media_buys_strategy", "strategy_id"),
    )


class MediaPackage(Base):
    """Media package model for structured querying of media buy packages.

    Stores packages separately from MediaBuy.raw_request for efficient lookups
    by package_id, which is needed for creative assignments.
    """

    __tablename__ = "media_packages"

    media_buy_id: Mapped[str] = mapped_column(
        String(100), ForeignKey("media_buys.media_buy_id"), primary_key=True, nullable=False
    )
    package_id: Mapped[str] = mapped_column(String(100), primary_key=True, nullable=False)
    package_config: Mapped[dict] = mapped_column(JSONType, nullable=False)

    __table_args__ = (
        Index("idx_media_packages_media_buy", "media_buy_id"),
        Index("idx_media_packages_package", "package_id"),
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
    timestamp: Mapped[DateTime] = mapped_column(DateTime, server_default=func.now())
    operation: Mapped[str] = mapped_column(String(100), nullable=False)
    principal_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    principal_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    adapter_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    details: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    strategy_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

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
    updated_at: Mapped[DateTime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    updated_by: Mapped[str | None] = mapped_column(String(255), nullable=True)


# Backwards compatibility alias
SuperadminConfig = TenantManagementConfig


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
    gam_manual_approval_required: Mapped[bool] = mapped_column(Boolean, default=False)
    gam_order_name_template: Mapped[str | None] = mapped_column(String(500), nullable=True)
    gam_line_item_name_template: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # NOTE: gam_company_id (advertiser_id) is per-principal, stored in Principal.platform_mappings

    # Kevel
    kevel_network_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    kevel_api_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    kevel_manual_approval_required: Mapped[bool] = mapped_column(Boolean, default=False)

    # Mock
    mock_manual_approval_required: Mapped[bool] = mapped_column(Boolean, default=False)

    # Triton
    triton_station_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    triton_api_key: Mapped[str | None] = mapped_column(String(100), nullable=True)

    created_at: Mapped[DateTime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    # Relationships
    tenant = relationship("Tenant", back_populates="adapter_config")

    __table_args__ = (Index("idx_adapter_config_type", "adapter_type"),)

    @property
    def gam_service_account_json(self) -> str | None:
        """Get decrypted GAM service account JSON."""
        if not self._gam_service_account_json:
            return None
        from src.core.utils.encryption import decrypt_api_key

        try:
            return decrypt_api_key(self._gam_service_account_json)
        except ValueError:
            logger.warning(f"Failed to decrypt GAM service account JSON for tenant {self.tenant_id}")
            return None

    @gam_service_account_json.setter
    def gam_service_account_json(self, value: str | None) -> None:
        """Set encrypted GAM service account JSON."""
        if not value:
            self._gam_service_account_json = None
            return

        from src.core.utils.encryption import encrypt_api_key

        self._gam_service_account_json = encrypt_api_key(value)


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
    auth_credentials: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    # Relationships
    tenant = relationship("Tenant", back_populates="creative_agents")

    __table_args__ = (
        Index("idx_creative_agents_tenant", "tenant_id"),
        Index("idx_creative_agents_enabled", "enabled"),
    )


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
    last_synced: Mapped[DateTime] = mapped_column(DateTime, nullable=False, default=func.now())
    created_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False, default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False, default=func.now(), onupdate=func.now())

    # Relationships
    tenant = relationship("Tenant")

    __table_args__ = (
        UniqueConstraint("tenant_id", "inventory_type", "inventory_id", name="uq_gam_inventory"),
        Index("idx_gam_inventory_tenant", "tenant_id"),
        Index("idx_gam_inventory_type", "inventory_type"),
        Index("idx_gam_inventory_status", "status"),
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
    created_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False, default=func.now())

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
    last_updated: Mapped[DateTime] = mapped_column(DateTime, nullable=False, default=func.now())
    created_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False, default=func.now())

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
    start_date: Mapped[DateTime | None] = mapped_column(DateTime, nullable=True)
    end_date: Mapped[DateTime | None] = mapped_column(DateTime, nullable=True)
    unlimited_end_date: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    total_budget: Mapped[float | None] = mapped_column(Float, nullable=True)
    currency_code: Mapped[str | None] = mapped_column(String(10), nullable=True)
    external_order_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    po_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_modified_date: Mapped[DateTime | None] = mapped_column(DateTime, nullable=True)
    is_programmatic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    applied_labels: Mapped[list | None] = mapped_column(JSONType, nullable=True)
    effective_applied_labels: Mapped[list | None] = mapped_column(JSONType, nullable=True)
    custom_field_values: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    order_metadata: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    last_synced: Mapped[DateTime] = mapped_column(DateTime, nullable=False, default=func.now())
    created_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False, default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False, default=func.now(), onupdate=func.now())

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
    start_date: Mapped[DateTime | None] = mapped_column(DateTime, nullable=True)
    end_date: Mapped[DateTime | None] = mapped_column(DateTime, nullable=True)
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
    last_modified_date: Mapped[DateTime | None] = mapped_column(DateTime, nullable=True)
    creation_date: Mapped[DateTime | None] = mapped_column(DateTime, nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_synced: Mapped[DateTime] = mapped_column(DateTime, nullable=False, default=func.now())
    created_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False, default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False, default=func.now(), onupdate=func.now())

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

    sync_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(50), ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False
    )
    adapter_type: Mapped[str] = mapped_column(String(50), nullable=False)
    sync_type: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    started_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False)
    completed_at: Mapped[DateTime | None] = mapped_column(DateTime, nullable=True)
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
    created_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    last_activity_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False, server_default=func.now())

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
    created_at: Mapped[DateTime] = mapped_column(DateTime, server_default=func.now())
    completed_at: Mapped[DateTime | None] = mapped_column(DateTime)
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
    created_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False, server_default=func.now())

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
    created_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
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
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
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
    verification_checked_at: Mapped[DateTime | None] = mapped_column(DateTime, nullable=True)
    verification_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
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
    created_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    tenant = relationship("Tenant", backref="property_tags")

    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
        Index("idx_property_tags_tenant", "tenant_id"),
    )


class PushNotificationConfig(Base, JSONValidatorMixin):
    """A2A push notification configuration for async operation callbacks.

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
    authentication_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    authentication_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    validation_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    webhook_secret: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

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
    last_attempt_at: Mapped[DateTime | None] = mapped_column(DateTime, nullable=True)
    delivered_at: Mapped[DateTime | None] = mapped_column(DateTime, nullable=True)

    # Error tracking
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_code: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Timestamps
    created_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False, server_default=func.now())

    # Relationships
    tenant = relationship("Tenant")

    __table_args__ = (
        Index("idx_webhook_deliveries_tenant", "tenant_id"),
        Index("idx_webhook_deliveries_status", "status"),
        Index("idx_webhook_deliveries_event_type", "event_type"),
        Index("idx_webhook_deliveries_object_id", "object_id"),
        Index("idx_webhook_deliveries_created", "created_at"),
    )
