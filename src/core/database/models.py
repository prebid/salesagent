"""SQLAlchemy models for database schema."""

from decimal import Decimal

from sqlalchemy import (
    DECIMAL,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
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


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models using SQLAlchemy 2.0 declarative style."""

    pass


class Tenant(Base, JSONValidatorMixin):
    __tablename__ = "tenants"

    tenant_id = Column(String(50), primary_key=True)
    name = Column(String(200), nullable=False)
    subdomain = Column(String(100), unique=True, nullable=False)
    virtual_host = Column(Text, nullable=True)  # For Approximated.app virtual hosts
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())
    is_active = Column(Boolean, default=True)
    billing_plan = Column(String(50), default="standard")
    billing_contact = Column(String(255))

    # New columns from migration
    ad_server = Column(String(50))
    max_daily_budget = Column(Integer, nullable=False, default=10000)
    enable_axe_signals = Column(Boolean, nullable=False, default=True)
    authorized_emails = Column(JSONType)  # JSON array
    authorized_domains = Column(JSONType)  # JSON array
    slack_webhook_url = Column(String(500))
    slack_audit_webhook_url = Column(String(500))
    hitl_webhook_url = Column(String(500))
    admin_token = Column(String(100))
    auto_approve_formats = Column(JSONType)  # JSON array
    human_review_required = Column(Boolean, nullable=False, default=True)
    policy_settings = Column(JSONType)  # JSON object
    signals_agent_config = Column(JSONType)  # JSON object for upstream signals discovery agent configuration

    # Relationships
    products = relationship("Product", back_populates="tenant", cascade="all, delete-orphan")
    principals = relationship("Principal", back_populates="tenant", cascade="all, delete-orphan")
    users = relationship("User", back_populates="tenant", cascade="all, delete-orphan")
    media_buys = relationship("MediaBuy", back_populates="tenant", cascade="all, delete-orphan", overlaps="media_buys")
    # tasks table removed - replaced by workflow_steps
    audit_logs = relationship("AuditLog", back_populates="tenant", cascade="all, delete-orphan")
    strategies = relationship("Strategy", back_populates="tenant", cascade="all, delete-orphan", overlaps="strategies")
    adapter_config = relationship(
        "AdapterConfig",
        back_populates="tenant",
        uselist=False,
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("idx_subdomain", "subdomain"),
        Index("ix_tenants_virtual_host", "virtual_host", unique=True),
    )

    # JSON validators are inherited from JSONValidatorMixin
    # No need for duplicate validators here


class CreativeFormat(Base):
    __tablename__ = "creative_formats"

    format_id = Column(String(50), primary_key=True)
    tenant_id = Column(String(50), ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=True)
    name = Column(String(200), nullable=False)
    type = Column(String(20), nullable=False)
    description = Column(Text)
    width = Column(Integer)
    height = Column(Integer)
    duration_seconds = Column(Integer)
    max_file_size_kb = Column(Integer)
    specs = Column(JSONType, nullable=False)  # JSONB in PostgreSQL
    is_standard = Column(Boolean, default=True)
    is_foundational = Column(Boolean, default=False)
    extends = Column(
        String(50),
        ForeignKey("creative_formats.format_id", ondelete="RESTRICT"),
        nullable=True,
    )
    modifications = Column(JSONType, nullable=True)  # JSONB in PostgreSQL
    source_url = Column(Text)
    created_at = Column(DateTime, server_default=func.now())
    # updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())  # TEMPORARILY DISABLED - migration 018 not applied in production

    # Relationships
    tenant = relationship("Tenant", backref="creative_formats")
    base_format = relationship("CreativeFormat", remote_side=[format_id], backref="extensions")

    __table_args__ = (CheckConstraint("type IN ('display', 'video', 'audio', 'native')"),)


class Product(Base, JSONValidatorMixin):
    __tablename__ = "products"

    tenant_id = Column(
        String(50),
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"),
        primary_key=True,
    )
    product_id = Column(String(100), primary_key=True)
    name = Column(String(200), nullable=False)
    description = Column(Text)
    formats = Column(JSONType, nullable=False)  # JSONB in PostgreSQL
    targeting_template = Column(JSONType, nullable=False)  # JSONB in PostgreSQL
    delivery_type = Column(String(50), nullable=False)
    is_fixed_price = Column(Boolean, nullable=False)
    cpm: Mapped[Decimal | None] = mapped_column(DECIMAL(10, 2))
    min_spend: Mapped[Decimal | None] = mapped_column(DECIMAL(10, 2), nullable=True)  # AdCP spec field
    measurement = Column(JSONType, nullable=True)  # JSONB in PostgreSQL - AdCP measurement object
    creative_policy = Column(JSONType, nullable=True)  # JSONB in PostgreSQL - AdCP creative policy object
    price_guidance = Column(JSONType)  # JSONB in PostgreSQL - Legacy field
    is_custom = Column(Boolean, default=False)
    expires_at = Column(DateTime)
    countries = Column(JSONType)  # JSONB in PostgreSQL
    implementation_config = Column(JSONType)  # JSONB in PostgreSQL
    # Note: PR #79 fields (currency, estimated_exposures, floor_cpm, recommended_cpm) are NOT stored in database
    # They are calculated dynamically from product_performance_metrics table

    # Relationships
    tenant = relationship("Tenant", back_populates="products")

    __table_args__ = (Index("idx_products_tenant", "tenant_id"),)


class Principal(Base, JSONValidatorMixin):
    __tablename__ = "principals"

    tenant_id = Column(
        String(50),
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"),
        primary_key=True,
    )
    principal_id = Column(String(50), primary_key=True)
    name = Column(String(200), nullable=False)
    platform_mappings = Column(JSONType, nullable=False)  # JSONB in PostgreSQL
    access_token = Column(String(255), unique=True, nullable=False)
    created_at = Column(DateTime, server_default=func.now())

    # Relationships
    tenant = relationship("Tenant", back_populates="principals")
    media_buys = relationship("MediaBuy", back_populates="principal", overlaps="media_buys")
    strategies = relationship("Strategy", back_populates="principal", overlaps="strategies")

    __table_args__ = (
        Index("idx_principals_tenant", "tenant_id"),
        Index("idx_principals_token", "access_token"),
    )


class User(Base):
    __tablename__ = "users"

    user_id = Column(String(50), primary_key=True)
    tenant_id = Column(String(50), ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False)
    email = Column(String(255), nullable=False)  # Removed unique=True to allow multi-tenant access
    name = Column(String(200), nullable=False)
    role = Column(String(20), nullable=False)
    google_id = Column(String(255))
    created_at = Column(DateTime, server_default=func.now())
    last_login = Column(DateTime)
    is_active = Column(Boolean, default=True)

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

    creative_id = Column(String(100), primary_key=True)
    tenant_id = Column(String(50), ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False)
    principal_id = Column(String(100), nullable=False)
    name = Column(String(255), nullable=False)
    format = Column(String(100), nullable=False)  # Format field matches database schema
    status = Column(String(50), nullable=False, default="pending")

    # Data field stores creative content and metadata as JSON
    data = Column(JSONType, nullable=False, default=dict)

    # Relationships and metadata
    group_id = Column(String(100), nullable=True)
    created_at = Column(DateTime, nullable=True, server_default=func.current_timestamp())
    updated_at = Column(DateTime, nullable=True)
    approved_at = Column(DateTime, nullable=True)
    approved_by = Column(String(255), nullable=True)
    strategy_id = Column(String(255), nullable=True)  # Missing field from database

    # Relationships
    tenant = relationship("Tenant", backref="creatives")

    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["tenant_id", "principal_id"], ["principals.tenant_id", "principals.principal_id"]),
        Index("idx_creatives_tenant", "tenant_id"),
        Index("idx_creatives_principal", "tenant_id", "principal_id"),
        Index("idx_creatives_status", "status"),
    )


class CreativeAssignment(Base):
    """Creative assignments to media buy packages."""

    __tablename__ = "creative_assignments"

    assignment_id = Column(String(100), primary_key=True)
    tenant_id = Column(String(50), ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False)
    creative_id = Column(String(100), nullable=False)
    media_buy_id = Column(String(100), nullable=False)
    package_id = Column(String(100), nullable=False)
    weight = Column(Integer, nullable=False, default=100)
    created_at = Column(DateTime, nullable=False, server_default=func.now())

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

    media_buy_id = Column(String(100), primary_key=True)
    tenant_id = Column(String(50), ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False)
    principal_id = Column(String(50), nullable=False)
    buyer_ref = Column(String(100), nullable=True, index=True)  # AdCP v2.4 buyer reference
    order_name = Column(String(255), nullable=False)
    advertiser_name = Column(String(255), nullable=False)
    campaign_objective = Column(String(100))
    kpi_goal = Column(String(255))
    budget: Mapped[Decimal | None] = mapped_column(DECIMAL(15, 2))
    currency = Column(String(3), nullable=True, default="USD")  # ISO 4217 currency code
    start_date = Column(Date, nullable=False)  # Legacy field, keep for compatibility
    end_date = Column(Date, nullable=False)  # Legacy field, keep for compatibility
    start_time = Column(DateTime, nullable=True)  # AdCP v2.4 datetime scheduling
    end_time = Column(DateTime, nullable=True)  # AdCP v2.4 datetime scheduling
    status = Column(String(20), nullable=False, default="draft")
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    approved_at = Column(DateTime)
    approved_by = Column(String(255))
    raw_request = Column(JSONType, nullable=False)  # JSONB in PostgreSQL
    strategy_id = Column(String(255), nullable=True)  # Strategy reference for linking operations

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
        Index("idx_media_buys_tenant", "tenant_id"),
        Index("idx_media_buys_status", "status"),
        Index("idx_media_buys_strategy", "strategy_id"),
    )


# DEPRECATED: Task and HumanTask models removed - replaced by WorkflowStep system
# Tables may still exist in database for backward compatibility but are not used by application
# Dashboard now uses only audit_logs table for activity tracking
# Workflow operations use WorkflowStep and ObjectWorkflowMapping tables


class AuditLog(Base):
    __tablename__ = "audit_logs"

    log_id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(String(50), ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False)
    timestamp = Column(DateTime, server_default=func.now())
    operation = Column(String(100), nullable=False)
    principal_name = Column(String(255))
    principal_id = Column(String(50))
    adapter_id = Column(String(50))
    success = Column(Boolean, nullable=False)
    error_message = Column(Text)
    details = Column(JSONType)  # JSONB in PostgreSQL
    strategy_id = Column(String(255), nullable=True)  # Strategy reference for linking operations

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

    config_key = Column(String(100), primary_key=True)
    config_value = Column(Text)
    description = Column(Text)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    updated_by = Column(String(255))


# Backwards compatibility alias
SuperadminConfig = TenantManagementConfig


class AdapterConfig(Base):
    __tablename__ = "adapter_config"

    tenant_id = Column(
        String(50),
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"),
        primary_key=True,
    )
    adapter_type = Column(String(50), nullable=False)

    # Mock adapter
    mock_dry_run = Column(Boolean)

    # Google Ad Manager
    gam_network_code = Column(String(50))
    gam_refresh_token = Column(Text)
    gam_trafficker_id = Column(String(50))  # Tenant-level: publisher's trafficker (defaults to authenticated user)
    gam_manual_approval_required = Column(Boolean, default=False)
    gam_order_name_template = Column(String(500))  # Template for order names, e.g., "{campaign_name} - {date_range}"
    gam_line_item_name_template = Column(String(500))  # Template for line item names, e.g., "{product_name}"
    # NOTE: gam_company_id (advertiser_id) is per-principal, stored in Principal.platform_mappings

    # Kevel
    kevel_network_id = Column(String(50))
    kevel_api_key = Column(String(100))
    kevel_manual_approval_required = Column(Boolean, default=False)

    # Triton
    triton_station_id = Column(String(50))
    triton_api_key = Column(String(100))

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    # Relationships
    tenant = relationship("Tenant", back_populates="adapter_config")

    __table_args__ = (Index("idx_adapter_config_type", "adapter_type"),)


class GAMInventory(Base):
    __tablename__ = "gam_inventory"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(String(50), ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False)
    inventory_type = Column(
        String(30), nullable=False
    )  # 'ad_unit', 'placement', 'label', 'custom_targeting_key', 'custom_targeting_value'
    inventory_id = Column(String(50), nullable=False)  # GAM ID
    name = Column(String(200), nullable=False)
    path = Column(JSONType)  # Array of path components for ad units
    status = Column(String(20), nullable=False)
    inventory_metadata = Column(JSONType)  # Full inventory details
    last_synced = Column(DateTime, nullable=False, default=func.now())
    created_at = Column(DateTime, nullable=False, default=func.now())
    updated_at = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())

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

    id = Column(Integer, primary_key=True)
    tenant_id = Column(String(50), ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False)
    product_id = Column(String(50), nullable=False)
    inventory_type = Column(String(30), nullable=False)  # 'ad_unit' or 'placement'
    inventory_id = Column(String(50), nullable=False)  # GAM inventory ID
    is_primary = Column(Boolean, default=False)  # Primary targeting for the product
    created_at = Column(DateTime, nullable=False, default=func.now())

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

    id = Column(Integer, primary_key=True)
    tenant_id = Column(String(50), ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False)
    country_code = Column(String(3), nullable=True)  # ISO-3166-1 alpha-3, NULL = all countries
    creative_size = Column(String(20), nullable=False)  # "300x250", "728x90", "1920x1080", etc.

    # Time period for these metrics
    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)

    # Volume metrics from GAM reporting (COUNTRY_CODE + CREATIVE_SIZE dimensions)
    total_impressions = Column(BigInteger, nullable=False, default=0)
    total_clicks = Column(BigInteger, nullable=False, default=0)
    total_revenue_micros = Column(BigInteger, nullable=False, default=0)

    # Calculated pricing metrics (in USD)
    average_cpm: Mapped[Decimal | None] = mapped_column(DECIMAL(10, 2), nullable=True)
    median_cpm: Mapped[Decimal | None] = mapped_column(DECIMAL(10, 2), nullable=True)
    p75_cpm: Mapped[Decimal | None] = mapped_column(DECIMAL(10, 2), nullable=True)  # 75th percentile
    p90_cpm: Mapped[Decimal | None] = mapped_column(DECIMAL(10, 2), nullable=True)  # 90th percentile

    # Metadata
    line_item_count = Column(Integer, nullable=False, default=0)  # Number of line items in aggregate
    last_updated = Column(DateTime, nullable=False, default=func.now())
    created_at = Column(DateTime, nullable=False, default=func.now())

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

    id = Column(Integer, primary_key=True)
    tenant_id = Column(String(50), ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False)
    order_id = Column(String(50), nullable=False)  # GAM Order ID
    name = Column(String(200), nullable=False)
    advertiser_id = Column(String(50), nullable=True)
    advertiser_name = Column(String(255), nullable=True)
    agency_id = Column(String(50), nullable=True)
    agency_name = Column(String(255), nullable=True)
    trafficker_id = Column(String(50), nullable=True)
    trafficker_name = Column(String(255), nullable=True)
    salesperson_id = Column(String(50), nullable=True)
    salesperson_name = Column(String(255), nullable=True)
    status = Column(String(20), nullable=False)  # DRAFT, PENDING_APPROVAL, APPROVED, PAUSED, CANCELED, DELETED
    start_date = Column(DateTime, nullable=True)
    end_date = Column(DateTime, nullable=True)
    unlimited_end_date = Column(Boolean, nullable=False, default=False)
    total_budget = Column(Float, nullable=True)
    currency_code = Column(String(10), nullable=True)
    external_order_id = Column(String(100), nullable=True)  # PO number
    po_number = Column(String(100), nullable=True)
    notes = Column(Text, nullable=True)
    last_modified_date = Column(DateTime, nullable=True)
    is_programmatic = Column(Boolean, nullable=False, default=False)
    applied_labels = Column(JSONType, nullable=True)  # List of label IDs
    effective_applied_labels = Column(JSONType, nullable=True)  # List of label IDs
    custom_field_values = Column(JSONType, nullable=True)
    order_metadata = Column(JSONType, nullable=True)  # Additional GAM fields
    last_synced = Column(DateTime, nullable=False, default=func.now())
    created_at = Column(DateTime, nullable=False, default=func.now())
    updated_at = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())

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

    id = Column(Integer, primary_key=True)
    tenant_id = Column(String(50), ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False)
    line_item_id = Column(String(50), nullable=False)  # GAM Line Item ID
    order_id = Column(String(50), nullable=False)  # GAM Order ID
    name = Column(String(200), nullable=False)
    status = Column(String(20), nullable=False)  # DRAFT, PENDING_APPROVAL, APPROVED, PAUSED, ARCHIVED, CANCELED
    line_item_type = Column(String(30), nullable=False)  # STANDARD, SPONSORSHIP, NETWORK, HOUSE, etc.
    priority = Column(Integer, nullable=True)
    start_date = Column(DateTime, nullable=True)
    end_date = Column(DateTime, nullable=True)
    unlimited_end_date = Column(Boolean, nullable=False, default=False)
    auto_extension_days = Column(Integer, nullable=True)
    cost_type = Column(String(20), nullable=True)  # CPM, CPC, CPD, CPA
    cost_per_unit = Column(Float, nullable=True)
    discount_type = Column(String(20), nullable=True)  # PERCENTAGE, ABSOLUTE_VALUE
    discount = Column(Float, nullable=True)
    contracted_units_bought = Column(BigInteger, nullable=True)
    delivery_rate_type = Column(String(30), nullable=True)  # EVENLY, FRONTLOADED, AS_FAST_AS_POSSIBLE
    goal_type = Column(String(20), nullable=True)  # LIFETIME, DAILY, NONE
    primary_goal_type = Column(String(20), nullable=True)  # IMPRESSIONS, CLICKS, etc.
    primary_goal_units = Column(BigInteger, nullable=True)
    impression_limit = Column(BigInteger, nullable=True)
    click_limit = Column(BigInteger, nullable=True)
    target_platform = Column(String(20), nullable=True)  # WEB, MOBILE, ANY
    environment_type = Column(String(20), nullable=True)  # BROWSER, VIDEO_PLAYER
    allow_overbook = Column(Boolean, nullable=False, default=False)
    skip_inventory_check = Column(Boolean, nullable=False, default=False)
    reserve_at_creation = Column(Boolean, nullable=False, default=False)
    stats_impressions = Column(BigInteger, nullable=True)
    stats_clicks = Column(BigInteger, nullable=True)
    stats_ctr = Column(Float, nullable=True)
    stats_video_completions = Column(BigInteger, nullable=True)
    stats_video_starts = Column(BigInteger, nullable=True)
    stats_viewable_impressions = Column(BigInteger, nullable=True)
    delivery_indicator_type = Column(
        String(30), nullable=True
    )  # UNDER_DELIVERY, EXPECTED_DELIVERY, OVER_DELIVERY, etc.
    delivery_data = Column(JSONType, nullable=True)  # Detailed delivery stats
    targeting = Column(JSONType, nullable=True)  # Full targeting criteria
    creative_placeholders = Column(JSONType, nullable=True)  # Creative sizes and companions
    frequency_caps = Column(JSONType, nullable=True)
    applied_labels = Column(JSONType, nullable=True)
    effective_applied_labels = Column(JSONType, nullable=True)
    custom_field_values = Column(JSONType, nullable=True)
    third_party_measurement_settings = Column(JSONType, nullable=True)
    video_max_duration = Column(BigInteger, nullable=True)
    line_item_metadata = Column(JSONType, nullable=True)  # Additional GAM fields
    last_modified_date = Column(DateTime, nullable=True)
    creation_date = Column(DateTime, nullable=True)
    external_id = Column(String(255), nullable=True)
    last_synced = Column(DateTime, nullable=False, default=func.now())
    created_at = Column(DateTime, nullable=False, default=func.now())
    updated_at = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())

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

    sync_id = Column(String(50), primary_key=True)
    tenant_id = Column(String(50), ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False)
    adapter_type = Column(String(50), nullable=False)
    sync_type = Column(String(20), nullable=False)  # inventory, targeting, full, orders
    status = Column(String(20), nullable=False)  # pending, running, completed, failed
    started_at = Column(DateTime, nullable=False)
    completed_at = Column(DateTime)
    summary = Column(Text)  # JSON with counts, details
    error_message = Column(Text)
    triggered_by = Column(String(50), nullable=False)  # user, cron, system
    triggered_by_id = Column(String(255))  # user email or system identifier

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

    context_id = Column(String(100), primary_key=True)
    tenant_id = Column(String(50), ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False)
    principal_id = Column(String(50), nullable=False)

    # Simple conversation tracking
    conversation_history = Column(JSONType, nullable=False, default=list)  # Clarifications and refinements only
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    last_activity_at = Column(DateTime, nullable=False, server_default=func.now())

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

    step_id = Column(String(100), primary_key=True)
    context_id = Column(
        String(100),
        ForeignKey("contexts.context_id", ondelete="CASCADE"),
        nullable=False,
    )
    step_type = Column(String(50), nullable=False)  # tool_call, approval, notification, etc.
    tool_name = Column(String(100), nullable=True)  # MCP tool name if applicable
    request_data = Column(JSONType, nullable=True)  # Original request JSON
    response_data = Column(JSONType, nullable=True)  # Response/result JSON
    status = Column(
        String(20), nullable=False, default="pending"
    )  # pending, in_progress, completed, failed, requires_approval
    owner = Column(String(20), nullable=False)  # principal, publisher, system
    assigned_to = Column(String(255), nullable=True)  # Specific user/system if assigned
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    completed_at = Column(DateTime, nullable=True)
    error_message = Column(Text, nullable=True)
    transaction_details = Column(JSONType, nullable=True)  # Actual API calls made to GAM, etc.
    comments = Column(JSONType, nullable=False, default=list)  # Array of {user, timestamp, comment} objects

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

    id = Column(Integer, primary_key=True, autoincrement=True)
    object_type = Column(String(50), nullable=False)  # media_buy, creative, product, etc.
    object_id = Column(String(100), nullable=False)  # The actual object's ID
    step_id = Column(
        String(100),
        ForeignKey("workflow_steps.step_id", ondelete="CASCADE"),
        nullable=False,
    )
    action = Column(String(50), nullable=False)  # create, update, approve, reject, etc.
    created_at = Column(DateTime, nullable=False, server_default=func.now())

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

    strategy_id = Column(String(255), primary_key=True)
    tenant_id = Column(String(50), ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=True)
    principal_id = Column(String(100), nullable=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    config = Column(JSONType, nullable=False, default=dict)
    is_simulation = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())

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

    strategy_id = Column(String(255), nullable=False, primary_key=True)
    state_key = Column(String(255), nullable=False, primary_key=True)
    state_value = Column(JSONType, nullable=False)
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())

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

    property_id = Column(String(100), nullable=False, primary_key=True)
    tenant_id = Column(String(50), nullable=False, primary_key=True)
    property_type = Column(
        String(20), nullable=False
    )  # website, mobile_app, ctv_app, dooh, podcast, radio, streaming_audio
    name = Column(String(255), nullable=False)
    identifiers = Column(JSONType, nullable=False)  # Array of {type, value} objects
    tags = Column(JSONType, nullable=True)  # Array of tag strings
    publisher_domain = Column(String(255), nullable=False)  # Domain for adagents.json verification
    verification_status = Column(String(20), nullable=False, default="pending")  # pending, verified, failed
    verification_checked_at = Column(DateTime, nullable=True)
    verification_error = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())

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

    tag_id = Column(String(50), nullable=False, primary_key=True)
    tenant_id = Column(String(50), nullable=False, primary_key=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())

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

    id = Column(String(50), primary_key=True)
    tenant_id = Column(String(50), nullable=False)
    principal_id = Column(String(50), nullable=False)
    session_id = Column(String(100), nullable=True)  # Optional A2A session tracking
    url = Column(Text, nullable=False)
    authentication_type = Column(String(50), nullable=True)  # bearer, basic, none
    authentication_token = Column(Text, nullable=True)
    validation_token = Column(Text, nullable=True)  # For validating webhook ownership
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())
    is_active = Column(Boolean, default=True)

    # Relationships
    tenant = relationship("Tenant", backref="push_notification_configs")
    principal = relationship("Principal", backref="push_notification_configs", overlaps="push_notification_configs")

    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
        ForeignKeyConstraint(
            ["tenant_id", "principal_id"], ["principals.tenant_id", "principals.principal_id"], ondelete="CASCADE"
        ),
        Index("idx_push_notification_configs_tenant", "tenant_id"),
        Index("idx_push_notification_configs_principal", "tenant_id", "principal_id"),
    )
