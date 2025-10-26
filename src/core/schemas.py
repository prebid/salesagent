import uuid
import warnings
from datetime import UTC, date, datetime, time

# UTC timezone for timezone-aware datetime objects
UTC = UTC

# --- V2.3 Pydantic Models (Bearer Auth, Restored & Complete) ---
# --- MCP Status System (AdCP PR #77) ---
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator


class AdCPBaseModel(BaseModel):
    """Base model for all AdCP request/response schemas.

    Provides environment-aware validation:
    - Production: extra="ignore" (forward compatible, accepts future schema fields)
    - Non-production: extra="forbid" (strict, catches bugs early)

    This allows clients to use newer schema versions in production without breaking,
    while maintaining strict validation during development and testing.

    The validation mode is determined at runtime based on the ENVIRONMENT variable.
    """

    # Default to ignoring extra fields (will be overridden in __init__ based on environment)
    model_config = ConfigDict(extra="ignore")

    def __init__(self, **data):
        """Initialize model with environment-aware validation."""
        from src.core.config import is_production

        # In non-production, validate strictly (forbid extra fields)
        if not is_production():
            # Get all valid field names for this model (access from class, not instance)
            valid_fields = set(self.__class__.model_fields.keys())
            provided_fields = set(data.keys())
            extra_fields = provided_fields - valid_fields

            if extra_fields:
                from pydantic import ValidationError

                raise ValidationError.from_exception_data(
                    self.__class__.__name__,
                    [
                        {
                            "type": "extra_forbidden",
                            "loc": (field,),
                            "msg": "Extra inputs are not permitted",
                            "input": data[field],
                        }
                        for field in extra_fields
                    ],
                )

        # Call parent __init__ which will ignore extra fields in production
        super().__init__(**data)


class TaskStatus(str, Enum):
    """Standardized task status enum per AdCP MCP Status specification.

    Provides crystal clear guidance on when operations need clarification,
    approval, or other human input with consistent status handling across
    MCP and A2A protocols.
    """

    SUBMITTED = "submitted"
    WORKING = "working"
    INPUT_REQUIRED = "input-required"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"
    REJECTED = "rejected"
    AUTH_REQUIRED = "auth-required"
    UNKNOWN = "unknown"

    @classmethod
    def from_operation_state(
        cls, operation_type: str, has_errors: bool = False, requires_approval: bool = False, requires_auth: bool = False
    ) -> str:
        """Convert operation state to appropriate status for decision trees.

        Args:
            operation_type: Type of operation (discovery, creation, activation, etc.)
            has_errors: Whether the operation encountered errors
            requires_approval: Whether the operation requires human approval
            requires_auth: Whether the operation requires authentication

        Returns:
            Appropriate TaskStatus value for client decision making
        """
        if requires_auth:
            return cls.AUTH_REQUIRED
        if has_errors:
            return cls.FAILED
        if requires_approval:
            return cls.INPUT_REQUIRED
        if operation_type in ["discovery", "listing"]:
            return cls.COMPLETED  # Discovery operations complete immediately
        if operation_type in ["creation", "activation", "update"]:
            return cls.WORKING  # Async operations in progress
        return cls.UNKNOWN


# --- Core Models ---


# --- Pricing Models (AdCP PR #88) ---
class PricingModel(str, Enum):
    """Supported pricing models per AdCP spec."""

    CPM = "cpm"  # Cost per 1,000 impressions
    VCPM = "vcpm"  # Cost per 1,000 viewable impressions
    CPC = "cpc"  # Cost per click
    CPCV = "cpcv"  # Cost per completed view (100% completion)
    CPV = "cpv"  # Cost per view at threshold
    CPP = "cpp"  # Cost per point (GRP-based)
    FLAT_RATE = "flat_rate"  # Fixed cost regardless of delivery


class PriceGuidance(BaseModel):
    """Pricing guidance for auction-based pricing per AdCP spec."""

    floor: float = Field(..., ge=0, description="Minimum bid price - publisher will reject bids under this value")
    p25: float | None = Field(None, ge=0, description="25th percentile winning price")
    p50: float | None = Field(None, ge=0, description="Median winning price")
    p75: float | None = Field(None, ge=0, description="75th percentile winning price")
    p90: float | None = Field(None, ge=0, description="90th percentile winning price")

    def model_dump(self, **kwargs):
        """Exclude null percentile values per AdCP spec (only floor is required)."""
        if "exclude_none" not in kwargs:
            kwargs["exclude_none"] = True
        return super().model_dump(**kwargs)


class PricingParameters(BaseModel):
    """Additional parameters specific to pricing models per AdCP spec."""

    # CPP parameters
    demographic: str | None = Field(None, description="Target demographic for CPP pricing (e.g., 'A18-49', 'W25-54')")
    min_points: float | None = Field(None, ge=0, description="Minimum GRPs/TRPs required for CPP pricing")

    # CPV parameters
    view_threshold: float | None = Field(
        None, ge=0, le=1, description="Percentage of video/audio that must be viewed for CPV pricing (0.0 to 1.0)"
    )

    # CPA/CPL parameters (reserved for future use)
    action_type: str | None = Field(
        None, description="Type of action for CPA pricing (e.g., 'purchase', 'sign_up', 'download')"
    )
    attribution_window_days: int | None = Field(
        None, ge=1, description="Attribution window in days for CPA/CPL pricing"
    )

    # DOOH parameters
    duration_hours: float | None = Field(None, ge=0, description="Duration in hours for time-based flat rate pricing")
    sov_percentage: float | None = Field(
        None, ge=0, le=100, description="Guaranteed share of voice as percentage (0-100)"
    )
    loop_duration_seconds: int | None = Field(None, ge=1, description="Duration of ad loop rotation in seconds")
    min_plays_per_hour: int | None = Field(
        None, ge=0, description="Minimum number of times ad plays per hour (frequency guarantee)"
    )
    venue_package: str | None = Field(
        None, description="Named venue package identifier (e.g., 'times_square_network', 'airport_terminals')"
    )
    estimated_impressions: int | None = Field(
        None, ge=0, description="Estimated impressions for this pricing option (informational)"
    )
    daypart: str | None = Field(
        None, description="Specific daypart for time-based pricing (e.g., 'morning_commute', 'evening_prime')"
    )


class PricingOption(BaseModel):
    """A pricing model option offered by a publisher for a product per AdCP spec."""

    pricing_option_id: str = Field(
        ..., description="Unique identifier for this pricing option within the product (e.g., 'cpm_usd_guaranteed')"
    )
    pricing_model: PricingModel = Field(..., description="The pricing model for this option")
    rate: float | None = Field(None, ge=0, description="The rate for this pricing model (required if is_fixed=true)")
    currency: str = Field(..., pattern="^[A-Z]{3}$", description="ISO 4217 currency code (e.g., USD, EUR, GBP)")
    is_fixed: bool = Field(..., description="Whether this is a fixed rate (true) or auction-based (false)")
    price_guidance: PriceGuidance | None = Field(
        None, description="Pricing guidance for auction-based pricing (required if is_fixed=false)"
    )
    parameters: PricingParameters | None = Field(None, description="Additional pricing model-specific parameters")
    min_spend_per_package: float | None = Field(
        None, ge=0, description="Minimum spend requirement per package using this pricing option"
    )

    # Adapter capability annotations (populated dynamically, not stored in database)
    supported: bool | None = Field(
        None, description="Whether this pricing model is supported by the current adapter (populated at discovery time)"
    )
    unsupported_reason: str | None = Field(
        None, description="Reason why this pricing model is not supported (if supported=false)"
    )

    @model_validator(mode="after")
    def validate_pricing_option(self) -> "PricingOption":
        """Validate pricing option per AdCP spec constraints."""
        if self.is_fixed and self.rate is None:
            raise ValueError("rate is required when is_fixed=true")
        if not self.is_fixed and self.price_guidance is None:
            raise ValueError("price_guidance is required when is_fixed=false")
        return self

    def model_dump(self, **kwargs):
        """Override to exclude is_fixed for AdCP compliance.

        AdCP uses separate schemas (cpm-fixed-option, cpm-auction-option, etc.)
        instead of a single schema with is_fixed flag. We exclude is_fixed and
        internal fields (supported, unsupported_reason) from external responses.

        Also excludes None values to match AdCP spec where optional fields should
        be omitted rather than set to null (e.g., rate in auction-based pricing).
        """
        exclude = kwargs.get("exclude", set())
        if isinstance(exclude, set):
            # Exclude internal fields that aren't in AdCP spec
            exclude.update({"is_fixed", "supported", "unsupported_reason"})
            kwargs["exclude"] = exclude

        # Set exclude_none=True by default for AdCP compliance
        # This ensures nested models (PriceGuidance) also exclude None values
        if "exclude_none" not in kwargs:
            kwargs["exclude_none"] = True

        return super().model_dump(**kwargs)

    def model_dump_internal(self, **kwargs):
        """Dump including all fields for database storage and internal processing."""
        kwargs.pop("exclude", None)  # Remove any exclude parameter
        return super().model_dump(**kwargs)


class AssetRequirement(BaseModel):
    """Asset requirement specification per AdCP spec."""

    asset_id: str = Field(..., description="Asset identifier used as key in creative manifest assets object")
    asset_type: str = Field(..., description="Type of asset required")
    asset_role: str | None = Field(None, description="Optional descriptive label (not used for referencing)")
    required: bool = Field(True, description="Whether this asset is required")
    quantity: int = Field(default=1, ge=1, description="Number of assets of this type required")
    requirements: dict[str, Any] | None = Field(None, description="Specific requirements for this asset type")


class FormatReference(BaseModel):
    """Reference to a format from a specific creative agent.

    DEPRECATED: Use FormatId instead. This class is maintained for backward compatibility.
    FormatReference serializes as FormatId (with 'id' field) but accepts 'format_id' for legacy code.

    Used in Product.formats to store full format references with agent URL.
    This enables dynamic format resolution from the correct creative agent.

    Example:
        {
            "agent_url": "https://creative.adcontextprotocol.org",
            "format_id": "display_300x250_image"  # Serializes as "id" per AdCP spec
        }
    """

    agent_url: str = Field(
        ..., description="URL of the creative agent that provides this format (must be registered in tenant config)"
    )
    format_id: str = Field(..., serialization_alias="id", description="Format ID within that agent's format catalog")


class Format(BaseModel):
    """Creative format definition per AdCP v2.4 spec.

    Represents a creative format with its requirements. The agent_url field identifies
    the authoritative creative agent that provides this format (e.g., the reference
    creative agent at https://creative.adcontextprotocol.org).
    """

    format_id: "FormatId" = Field(..., description="Format identifier (FormatId object per AdCP spec)")
    agent_url: str | None = Field(
        None,
        description="Base URL of the agent that provides this format (authoritative source). "
        "E.g., 'https://creative.adcontextprotocol.org', 'https://dco.example.com'",
    )
    name: str = Field(..., description="Human-readable format name")
    type: Literal["audio", "video", "display", "native", "dooh", "rich_media", "universal", "generative"] = Field(
        ..., description="Media type of this format"
    )
    category: Literal["standard", "custom", "generative"] | None = Field(None, description="Format category")
    is_standard: bool | None = Field(
        None, description="Whether this follows IAB specifications or AdCP standard format definitions"
    )
    iab_specification: str | None = Field(None, description="Name of the IAB specification (if applicable)")
    description: str | None = Field(None, description="Human-readable description of the format")
    renders: list[dict[str, Any]] | None = Field(
        None,
        description="Specification of rendered pieces (AdCP v2.4 spec). "
        "Each render contains role and dimensions. Most formats have a single 'primary' render.",
    )
    requirements: dict[str, Any] | None = Field(
        None, description="Technical specifications for this format (e.g., dimensions, duration, file size limits)"
    )
    assets_required: list[AssetRequirement] | None = Field(
        None, description="Array of required assets or asset groups for this format"
    )
    delivery: dict[str, Any] | None = Field(
        None, description="Delivery method specifications (e.g., hosted, VAST, third-party tags)"
    )
    accepts_3p_tags: bool | None = Field(
        None, description="Whether this format can accept third-party served creative tags"
    )
    supported_macros: list[str] | None = Field(None, description="List of universal macros supported by this format")
    platform_config: dict[str, Any] | None = Field(
        None, description="Platform-specific configuration (e.g., gam, kevel) for creative mapping"
    )
    output_format_ids: list["FormatId"] | None = Field(
        None,
        description="For generative formats: array of FormatId objects this format can generate per AdCP spec",
    )

    def get_primary_dimensions(self) -> tuple[int, int] | None:
        """Extract primary dimensions from renders array.

        Returns:
            Tuple of (width, height) in pixels, or None if not available.
        """
        # Try renders field first (AdCP v2.4 spec)
        if self.renders and len(self.renders) > 0:
            primary_render = self.renders[0]  # First render is typically primary
            if "dimensions" in primary_render:
                dims = primary_render["dimensions"]
                width = dims.get("width")
                height = dims.get("height")
                if width is not None and height is not None:
                    return (int(width), int(height))

        # Fallback to requirements field (legacy)
        if self.requirements:
            width = self.requirements.get("width")
            height = self.requirements.get("height")
            if width is not None and height is not None:
                return (int(width), int(height))

        return None


# FORMAT_REGISTRY removed - now using dynamic format discovery via CreativeAgentRegistry
#
# The static FORMAT_REGISTRY has been replaced with dynamic format discovery per AdCP v2.4.
# Format lookups now go through CreativeAgentRegistry which queries creative agents via MCP:
#   - Default agent: https://creative.adcontextprotocol.org
#   - Tenant-specific agents: Configured in creative_agents database table
#
# Migration guide:
#   - Old: FORMAT_REGISTRY["display_300x250"]
#   - New: format_resolver.get_format("display_300x250", tenant_id="...")
#
# See:
#   - src/core/creative_agent_registry.py for registry implementation
#   - src/core/format_resolver.py for format resolution functions


def get_format_by_id(format_id: str, tenant_id: str | None = None) -> Format | None:
    """Get a Format object by its ID from creative agent registry.

    Args:
        format_id: Format identifier
        tenant_id: Optional tenant ID for tenant-specific agents

    Returns:
        Format object or None if not found
    """
    from src.core.format_resolver import get_format

    try:
        return get_format(format_id, tenant_id=tenant_id)
    except ValueError:
        return None


def convert_format_ids_to_formats(format_ids: list[str], tenant_id: str | None = None) -> list[Format]:
    """Convert a list of format ID strings to Format objects.

    This function is used to ensure AdCP schema compliance by converting
    internal format ID representations to full Format objects via dynamic discovery.

    Args:
        format_ids: List of format IDs to resolve
        tenant_id: Optional tenant ID for tenant-specific agents

    Returns:
        List of Format objects
    """
    formats = []
    for format_id in format_ids:
        format_obj = get_format_by_id(format_id, tenant_id=tenant_id)
        if format_obj:
            formats.append(format_obj)
        else:
            # For unknown format IDs, create a minimal Format object with FormatId
            formats.append(
                Format(  # type: ignore[call-arg]
                    format_id=FormatId(agent_url="https://creative.adcontextprotocol.org", id=format_id),
                    name=format_id.replace("_", " ").title(),
                    type="display",  # Default to display
                )
            )
    return formats


class FrequencyCap(BaseModel):
    """Simple frequency capping configuration.

    Provides basic impression suppression at the media buy or package level.
    More sophisticated frequency management is handled by the AXE layer.
    """

    suppress_minutes: int = Field(..., gt=0, description="Suppress impressions for this many minutes after serving")
    scope: Literal["media_buy", "package"] = Field("media_buy", description="Apply at media buy or package level")


class TargetingCapability(BaseModel):
    """Defines targeting dimension capabilities and restrictions."""

    dimension: str  # e.g., "geo_country", "key_value"
    access: Literal["overlay", "managed_only", "both"] = "overlay"
    description: str | None = None
    allowed_values: list[str] | None = None  # For restricted value sets
    axe_signal: bool | None = False  # Whether this is an AXE signal dimension


class Targeting(BaseModel):
    """Comprehensive targeting options for media buys.

    All fields are optional and can be combined for precise audience targeting.
    Platform adapters will map these to their specific targeting capabilities.
    Uses any_of/none_of pattern for consistent include/exclude across all dimensions.

    Note: Some targeting dimensions are managed-only and cannot be set via overlay.
    These are typically used for AXE signal integration.
    """

    # Geographic targeting - aligned with OpenRTB (overlay access)
    geo_country_any_of: list[str] | None = None  # ISO country codes: ["US", "CA", "GB"]
    geo_country_none_of: list[str] | None = None

    geo_region_any_of: list[str] | None = None  # Region codes: ["NY", "CA", "ON"]
    geo_region_none_of: list[str] | None = None

    geo_metro_any_of: list[str] | None = None  # Metro/DMA codes: ["501", "803"]
    geo_metro_none_of: list[str] | None = None

    geo_city_any_of: list[str] | None = None  # City names: ["New York", "Los Angeles"]
    geo_city_none_of: list[str] | None = None

    geo_zip_any_of: list[str] | None = None  # Postal codes: ["10001", "90210"]
    geo_zip_none_of: list[str] | None = None

    # Device and platform targeting
    device_type_any_of: list[str] | None = None  # ["mobile", "desktop", "tablet", "ctv", "audio", "dooh"]
    device_type_none_of: list[str] | None = None

    os_any_of: list[str] | None = None  # Operating systems: ["iOS", "Android", "Windows"]
    os_none_of: list[str] | None = None

    browser_any_of: list[str] | None = None  # Browsers: ["Chrome", "Safari", "Firefox"]
    browser_none_of: list[str] | None = None

    # Content and contextual targeting
    content_cat_any_of: list[str] | None = None  # IAB content categories
    content_cat_none_of: list[str] | None = None

    keywords_any_of: list[str] | None = None  # Keyword targeting
    keywords_none_of: list[str] | None = None

    # Audience targeting
    audiences_any_of: list[str] | None = None  # Audience segments
    audiences_none_of: list[str] | None = None

    # Signal targeting - can use signal IDs from get_signals endpoint
    signals: list[str] | None = None  # Signal IDs like ["auto_intenders_q1_2025", "sports_content"]

    # Media type targeting
    media_type_any_of: list[str] | None = None  # ["video", "audio", "display", "native"]
    media_type_none_of: list[str] | None = None

    # Frequency control
    frequency_cap: FrequencyCap | None = None  # Impression limits per user/period

    # Connection type targeting
    connection_type_any_of: list[int] | None = None  # OpenRTB connection types
    connection_type_none_of: list[int] | None = None

    # Platform-specific custom targeting
    custom: dict[str, Any] | None = None  # Platform-specific targeting options

    # Key-value targeting (managed-only for AXE signals)
    # These are not exposed in overlay - only set by orchestrator/AXE
    key_value_pairs: dict[str, str] | None = None  # e.g., {"aee_segment": "high_value", "aee_score": "0.85"}

    # Internal fields (not in AdCP spec)
    tenant_id: str | None = Field(None, description="Internal: Tenant ID for multi-tenancy")
    created_at: datetime | None = Field(None, description="Internal: Creation timestamp")
    updated_at: datetime | None = Field(None, description="Internal: Last update timestamp")
    metadata: dict[str, Any] | None = Field(None, description="Internal: Additional metadata")

    def model_dump(self, **kwargs):
        """Override to provide AdCP-compliant responses while preserving internal fields."""
        # Default to excluding internal and managed fields for AdCP compliance
        exclude = kwargs.get("exclude", set())
        if isinstance(exclude, set):
            # Add internal and managed fields to exclude by default
            exclude.update(
                {
                    "key_value_pairs",  # Managed-only field
                    "tenant_id",
                    "created_at",
                    "updated_at",
                    "metadata",  # Internal fields
                }
            )
            kwargs["exclude"] = exclude

        return super().model_dump(**kwargs)

    def model_dump_internal(self, **kwargs):
        """Dump including internal and managed fields for database storage and internal processing."""
        # Don't exclude internal fields or managed fields
        kwargs.pop("exclude", None)  # Remove any exclude parameter
        return super().model_dump(**kwargs)

    def dict(self, **kwargs):
        """Override dict to always exclude managed fields (for backward compat)."""
        kwargs["exclude"] = kwargs.get("exclude", set())
        if isinstance(kwargs["exclude"], set):
            kwargs["exclude"].add("key_value_pairs")
        return super().dict(**kwargs)


class Budget(BaseModel):
    """Budget object with multi-currency support (AdCP spec compliant)."""

    total: float = Field(..., description="Total budget amount (AdCP spec field name)")
    currency: str = Field(..., description="ISO 4217 currency code (e.g., 'USD', 'EUR')")
    daily_cap: float | None = Field(None, description="Optional daily spending limit")
    pacing: Literal["even", "asap", "daily_budget"] = Field("even", description="Budget pacing strategy")
    auto_pause_on_budget_exhaustion: bool | None = Field(
        None, description="Whether to pause campaign when budget is exhausted"
    )

    def model_dump_internal(self, **kwargs):
        """Dump including all fields for internal processing."""
        return super().model_dump(**kwargs)


# Budget utility functions for v1.8.0 compatibility
def extract_budget_amount(budget: "Budget | float | dict | None", default_currency: str = "USD") -> tuple[float, str]:
    """Extract budget amount and currency from various budget formats (v1.8.0 compatible).

    Handles:
    - v1.8.0 format: simple float (currency should be from pricing option)
    - Legacy format: Budget object with total and currency
    - Dict format: {'total': float, 'currency': str}
    - None: returns (0.0, default_currency)

    Args:
        budget: Budget in any supported format
        default_currency: Currency to use for v1.8.0 float budgets.
                         **IMPORTANT**: This should be the currency from the selected
                         pricing option, not an arbitrary default.

    Returns:
        Tuple of (amount, currency)

    Note:
        Per AdCP v1.8.0, currency is determined by the pricing option selected for
        the package, not by the budget field. The default_currency parameter allows
        callers to pass the pricing option's currency for v1.8.0 float budgets.
        For legacy Budget objects, the currency from the object is used instead.

    Example:
        # v1.8.0: currency from package pricing option
        package_currency = request.packages[0].currency  # From pricing option
        amount, currency = extract_budget_amount(request.budget, package_currency)

        # Legacy: currency from Budget object
        amount, currency = extract_budget_amount(Budget(total=5000, currency="EUR"))
    """
    if budget is None:
        return (0.0, default_currency)
    elif isinstance(budget, dict):
        return (budget.get("total", 0.0), budget.get("currency", default_currency))
    elif isinstance(budget, int | float):
        return (float(budget), default_currency)
    else:
        # Budget object with .total and .currency attributes
        return (budget.total, budget.currency)


# AdCP Compliance Models
class Measurement(BaseModel):
    """Measurement capabilities included with a product per AdCP spec."""

    type: str = Field(
        ..., description="Type of measurement", examples=["incremental_sales_lift", "brand_lift", "foot_traffic"]
    )
    attribution: str = Field(
        ..., description="Attribution methodology", examples=["deterministic_purchase", "probabilistic"]
    )
    window: str | None = Field(None, description="Attribution window", examples=["30_days", "7_days"])
    reporting: str = Field(
        ..., description="Reporting frequency and format", examples=["weekly_dashboard", "real_time_api"]
    )


class CreativePolicy(BaseModel):
    """Creative requirements and restrictions for a product per AdCP spec."""

    co_branding: Literal["required", "optional", "none"] = Field(..., description="Co-branding requirement")
    landing_page: Literal["any", "retailer_site_only", "must_include_retailer"] = Field(
        ..., description="Landing page requirements"
    )
    templates_available: bool = Field(..., description="Whether creative templates are provided")


class AIReviewPolicy(BaseModel):
    """Configuration for AI-powered creative review with confidence thresholds.

    This policy defines how AI confidence scores map to approval decisions:
    - High confidence approvals/rejections are automatic
    - Low confidence or sensitive categories require human review
    - Confidence thresholds are configurable per tenant
    """

    auto_approve_threshold: float = Field(
        0.90,
        ge=0.0,
        le=1.0,
        description="Confidence threshold for auto-approval (>= this value). AI must be at least this confident to auto-approve.",
    )
    auto_reject_threshold: float = Field(
        0.10,
        ge=0.0,
        le=1.0,
        description="Confidence threshold for auto-rejection (<= this value). AI must be this certain or less to auto-reject.",
    )
    always_require_human_for: list[str] = Field(
        default_factory=lambda: ["political", "healthcare", "financial"],
        description="Creative categories that always require human review regardless of AI confidence",
    )
    learn_from_overrides: bool = Field(
        True,
        description="Track when humans disagree with AI decisions for model improvement",
    )


class Product(BaseModel):
    product_id: str
    name: str
    description: str
    formats: list["FormatId | FormatReference"] | list[str] = Field(
        serialization_alias="format_ids",
        description="Array of supported creative format IDs - structured format_id objects with agent_url and id",
    )
    delivery_type: Literal["guaranteed", "non_guaranteed"]

    # NEW: Pricing options (AdCP PR #88)
    # Note: This is populated from database relationship, not a column
    # REQUIRED: All products must have at least one pricing option in database
    # Can be empty list for anonymous users (hidden for privacy)
    pricing_options: list[PricingOption] = Field(
        default_factory=list,
        description="Available pricing models for this product (AdCP PR #88). May be empty for unauthenticated requests.",
    )

    # Pricing fields (AdCP PR #88)
    floor_cpm: float | None = Field(
        None, description="Calculated dynamically from pricing_options price_guidance", gt=0
    )
    recommended_cpm: float | None = Field(
        None, description="Calculated dynamically from pricing_options price_guidance", gt=0
    )

    # Other fields
    measurement: Measurement | None = Field(None, description="Measurement capabilities included with this product")
    creative_policy: CreativePolicy | None = Field(None, description="Creative requirements and restrictions")
    is_custom: bool = Field(default=False)
    brief_relevance: str | None = Field(
        None, description="Explanation of why this product matches the brief (populated when brief is provided)"
    )
    expires_at: datetime | None = None
    implementation_config: dict[str, Any] | None = Field(
        default=None,
        description="Ad server-specific configuration for implementing this product (placements, line item settings, etc.)",
    )
    # AdCP property authorization fields (at least one required per spec)
    properties: list["Property"] | None = Field(
        None,
        description="Full property objects covered by this product for adagents.json validation",
        min_length=1,
    )
    property_tags: list[str] | None = Field(
        None,
        description="Tags identifying groups of properties (use list_authorized_properties for details)",
        min_length=1,
    )
    # AdCP PR #79 fields - populated dynamically from historical reporting data
    # These are NOT stored in database, calculated on-demand from product_performance_metrics
    estimated_exposures: int | None = Field(None, description="Estimated impressions (calculated dynamically)", gt=0)

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
    def validate_properties_or_tags(self) -> "Product":
        """Validate that at least one of properties or property_tags is provided per AdCP spec.

        Per AdCP spec, products must have either:
        - properties: Full Property objects for adagents.json validation
        - property_tags: Tag strings (buyers use list_authorized_properties for details)
        """
        has_properties = self.properties and len(self.properties) > 0
        has_tags = self.property_tags and len(self.property_tags) > 0

        if not has_properties and not has_tags:
            raise ValueError(
                "Product must have either 'properties' or 'property_tags' per AdCP spec. "
                "Use property_tags=['all_inventory'] as a default if unsure."
            )

        if has_properties and has_tags:
            raise ValueError(
                "Product cannot have both 'properties' and 'property_tags' (AdCP oneOf constraint). "
                "Use properties for full validation OR property_tags for tag-based authorization, not both."
            )

        return self

    @property
    def format_ids(self) -> list[str]:
        """AdCP spec compliant property name for formats.

        Returns format IDs only (for backward compatibility).
        If formats are FormatReference objects, extracts format_id from each.
        """
        if not self.formats:
            return []

        # Handle legacy string format IDs
        if isinstance(self.formats[0], str):
            return self.formats  # type: ignore

        # Handle new FormatReference objects
        return [fmt.format_id for fmt in self.formats]  # type: ignore

    @field_serializer("formats", when_used="json")
    def serialize_formats_for_json(self, formats: list) -> list:
        """Serialize formats as FormatId objects per AdCP spec.

        Returns list of FormatId objects with agent_url and id fields.
        Pydantic will automatically serialize these as dicts with both fields.

        For unknown format IDs, uses a default agent_url to ensure graceful handling
        of legacy data.
        """
        if not formats:
            return []

        # Default agent_url for unknown formats
        DEFAULT_AGENT_URL = "https://creative.adcontextprotocol.org"

        result = []
        for fmt in formats:
            if isinstance(fmt, str):
                # Legacy string format - convert to FormatId object
                from src.core.format_cache import upgrade_legacy_format_id

                try:
                    result.append(upgrade_legacy_format_id(fmt))
                except ValueError:
                    # Unknown format - use default agent_url
                    result.append(FormatId(agent_url=DEFAULT_AGENT_URL, id=fmt))
            elif isinstance(fmt, FormatId):
                # Already a FormatId object
                result.append(fmt)
            elif isinstance(fmt, dict):
                # Dict representation - convert to FormatId
                if "id" in fmt and "agent_url" in fmt:
                    result.append(FormatId(agent_url=fmt["agent_url"], id=fmt["id"]))
                elif "id" in fmt:
                    # Missing agent_url - try upgrade, fallback to default
                    from src.core.format_cache import upgrade_legacy_format_id

                    try:
                        result.append(upgrade_legacy_format_id(fmt["id"]))
                    except ValueError:
                        result.append(FormatId(agent_url=DEFAULT_AGENT_URL, id=fmt["id"]))
                else:
                    raise ValueError(f"Invalid format dict: {fmt}")
            else:
                # Other object types (like FormatReference)
                if hasattr(fmt, "agent_url") and hasattr(fmt, "id"):
                    result.append(FormatId(agent_url=fmt.agent_url, id=fmt.id))
                elif hasattr(fmt, "format_id"):
                    from src.core.format_cache import upgrade_legacy_format_id

                    try:
                        result.append(upgrade_legacy_format_id(fmt.format_id))
                    except ValueError:
                        result.append(FormatId(agent_url=DEFAULT_AGENT_URL, id=fmt.format_id))
                else:
                    raise ValueError(f"Cannot serialize format: {fmt}")

        return result

    @property
    def pricing_summary(self) -> str | None:
        """Generate human-readable pricing summary for display to buyers (AdCP PR #88).

        Returns string like: "CPM: $8-$15 (auction), CPCV: $0.35 (fixed)"
        Returns None if no pricing information available.
        """
        if not self.pricing_options or len(self.pricing_options) == 0:
            return None

        summary_parts = []
        for option in self.pricing_options:
            model = option.pricing_model.value if hasattr(option.pricing_model, "value") else option.pricing_model
            model_upper = model.upper()

            if option.is_fixed and option.rate:
                # Fixed pricing: show rate
                summary_parts.append(f"{model_upper}: ${option.rate:.2f} ({option.currency}, fixed)")
            elif not option.is_fixed and option.price_guidance:
                # Auction pricing: show floor-p90 range
                floor = option.price_guidance.floor
                p90 = option.price_guidance.p90 if option.price_guidance.p90 else option.price_guidance.p50
                if p90 and p90 != floor:
                    summary_parts.append(f"{model_upper}: ${floor:.2f}-${p90:.2f} ({option.currency}, auction)")
                else:
                    summary_parts.append(f"{model_upper}: ${floor:.2f}+ ({option.currency}, auction)")
            else:
                # Incomplete pricing info
                summary_parts.append(f"{model_upper} ({option.currency})")

        return ", ".join(summary_parts) if summary_parts else None

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

        # Add computed pricing_summary for buyer convenience (AdCP PR #88)
        if self.pricing_summary:
            data["pricing_summary"] = self.pricing_summary

        # Remove null fields per AdCP spec
        # Only truly required fields should always be present
        core_fields = {
            "product_id",
            "name",
            "description",
            "format_ids",
            "delivery_type",
            "is_custom",
        }

        adcp_data = {}
        for key, value in data.items():
            # Include core fields always, and non-null optional fields
            # Exclude empty pricing_options (for anonymous users)
            if key == "pricing_options" and value == []:
                continue
            if key in core_fields or value is not None:
                adcp_data[key] = value

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


# --- Core Schemas ---


class Principal(BaseModel):
    """Principal object containing authentication and adapter mapping information."""

    principal_id: str
    name: str
    platform_mappings: dict[str, Any]

    def get_adapter_id(self, adapter_name: str) -> str | None:
        """Get the adapter-specific ID for this principal."""
        # Map adapter short names to platform keys
        adapter_platform_map = {
            "gam": "google_ad_manager",
            "google_ad_manager": "google_ad_manager",
            "kevel": "kevel",
            "triton": "triton",
            "mock": "mock",
        }

        platform_key = adapter_platform_map.get(adapter_name)
        if not platform_key:
            return None

        platform_data = self.platform_mappings.get(platform_key, {})
        if isinstance(platform_data, dict):
            # Try common field names for advertiser ID
            for field in ["advertiser_id", "id", "company_id"]:
                if field in platform_data:
                    return str(platform_data[field]) if platform_data[field] else None

        # Fallback to old format for backwards compatibility
        old_field_map = {
            "gam": "gam_advertiser_id",
            "kevel": "kevel_advertiser_id",
            "triton": "triton_advertiser_id",
            "mock": "mock_advertiser_id",
        }
        old_field = old_field_map.get(adapter_name)
        if old_field and old_field in self.platform_mappings:
            return str(self.platform_mappings[old_field]) if self.platform_mappings[old_field] else None

        return None


# --- Performance Index ---
class ProductPerformance(BaseModel):
    product_id: str
    performance_index: float  # 1.0 = baseline, 1.2 = 20% better, 0.8 = 20% worse
    confidence_score: float | None = None  # 0.0 to 1.0


class UpdatePerformanceIndexRequest(AdCPBaseModel):
    media_buy_id: str
    performance_data: list[ProductPerformance]


class UpdatePerformanceIndexResponse(AdCPBaseModel):
    status: str
    detail: str

    def __str__(self) -> str:
        """Return human-readable text for MCP content field."""
        return self.detail


# --- Discovery ---
class FormatType(str, Enum):
    """Valid format types per AdCP spec."""

    VIDEO = "video"
    DISPLAY = "display"
    AUDIO = "audio"
    # Note: "native" is not in cached AdCP schema v1.6.0, only video/display/audio


class DeliveryType(str, Enum):
    """Valid delivery types per AdCP spec."""

    GUARANTEED = "guaranteed"
    NON_GUARANTEED = "non_guaranteed"


class ProductFilters(BaseModel):
    """Structured filters for product discovery per AdCP spec."""

    delivery_type: DeliveryType | None = Field(
        None,
        description="Filter by delivery type",
    )
    is_fixed_price: bool | None = Field(
        None,
        description="Filter for fixed price vs auction products",
    )
    format_types: list[FormatType] | None = Field(
        None,
        description="Filter by format types",
    )
    format_ids: list["FormatId"] | None = Field(
        None,
        description="Filter by specific format IDs",
    )
    standard_formats_only: bool | None = Field(
        None,
        description="Only return products accepting IAB standard formats",
    )

    @model_validator(mode="before")
    @classmethod
    def upgrade_legacy_format_ids(cls, values: dict) -> dict:
        """Convert dict format_ids to FormatId objects (AdCP v2.4 compliance)."""
        if not isinstance(values, dict):
            return values

        format_ids = values.get("format_ids")
        if format_ids and isinstance(format_ids, list):
            # Convert any dict format_ids to FormatId objects
            upgraded = []
            for fmt_id in format_ids:
                if isinstance(fmt_id, dict) and "agent_url" in fmt_id and "id" in fmt_id:
                    # Dict with FormatId structure - convert to FormatId object
                    upgraded.append(FormatId(**fmt_id))
                else:
                    # Already a FormatId object - pass through
                    upgraded.append(fmt_id)
            values["format_ids"] = upgraded

        return values


class GetProductsRequest(AdCPBaseModel):
    brief: str = Field(
        "",
        description="Brief description of the advertising campaign or requirements (optional)",
    )
    brand_manifest: "BrandManifest | str" = Field(
        ...,
        description="Brand information manifest (inline object or URL string). REQUIRED per AdCP v2.2.0 spec.",
    )
    adcp_version: str = Field(
        "1.0.0",
        description="AdCP schema version for this request",
        pattern=r"^\d+\.\d+\.\d+$",
    )
    filters: ProductFilters | None = Field(
        None,
        description="Structured filters for product discovery",
    )


class Error(BaseModel):
    """Standard error structure per AdCP spec."""

    code: str = Field(..., description="Error code")
    message: str = Field(..., description="Human-readable error message")
    details: dict[str, Any] | None = Field(None, description="Additional error details")


class GetProductsResponse(AdCPBaseModel):
    """Response for get_products tool (AdCP v2.4 spec compliant).

    Per AdCP PR #113, this response contains ONLY domain data.
    Protocol fields (status, task_id, message, context_id) are added by the
    protocol layer (MCP, A2A, REST) via ProtocolEnvelope wrapper.
    """

    # Required AdCP domain fields
    products: list[Product] = Field(..., description="List of available advertising products")

    # Optional AdCP domain fields
    errors: list[Error] | None = Field(None, description="Task-specific errors and warnings")

    def model_dump(self, **kwargs):
        """Override to ensure products use AdCP-compliant serialization."""
        data = {}

        # Respect exclude parameter from kwargs
        exclude = kwargs.get("exclude", set())
        if not isinstance(exclude, set):
            exclude = set(exclude) if exclude else set()

        # Serialize products using their custom model_dump method
        if "products" not in exclude:
            if self.products:
                data["products"] = [product.model_dump(**kwargs) for product in self.products]
            else:
                data["products"] = []

        # Add other fields, excluding None values for AdCP compliance
        if "errors" not in exclude and self.errors is not None:
            data["errors"] = self.errors

        return data

    def model_dump_internal(self, **kwargs):
        """Override to ensure products use internal field names for reconstruction."""
        data = {}

        # Serialize products using their internal model_dump method
        if self.products:
            data["products"] = [product.model_dump_internal(**kwargs) for product in self.products]
        else:
            data["products"] = []

        # Add other fields
        if self.errors is not None:
            data["errors"] = self.errors

        return data

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
        if count > 0 and all(
            all(po.rate is None for po in p.pricing_options) for p in self.products if p.pricing_options
        ):
            return f"{base_msg} Please connect through an authorized buying agent for pricing data."

        return base_msg


class ListCreativeFormatsRequest(AdCPBaseModel):
    """Request for list_creative_formats tool.

    All parameters are optional filters per AdCP spec.
    """

    adcp_version: str = Field(
        default="1.0.0",
        pattern=r"^\d+\.\d+\.\d+$",
        description="AdCP schema version for this request (e.g., '1.0.0')",
    )
    type: str | None = Field(None, description="Filter by format type (audio, video, display)")
    standard_only: bool | None = Field(None, description="Only return IAB standard formats")
    category: str | None = Field(None, description="Filter by format category (standard, custom)")
    format_ids: list["FormatId"] | None = Field(
        None, description="Return only these specific format IDs (e.g., from get_products response)"
    )
    asset_types: list[str] | None = Field(
        None,
        description="Filter to formats that include these asset types (e.g., ['image', 'text'], ['javascript'])",
    )
    max_width: int | None = Field(
        None, description="Maximum width in pixels (inclusive). Returns formats with width <= this value"
    )
    max_height: int | None = Field(
        None, description="Maximum height in pixels (inclusive). Returns formats with height <= this value"
    )
    min_width: int | None = Field(None, description="Minimum width in pixels (inclusive)")
    min_height: int | None = Field(None, description="Minimum height in pixels (inclusive)")
    is_responsive: bool | None = Field(None, description="Filter for responsive formats that adapt to container size")
    name_search: str | None = Field(None, description="Search for formats by name (case-insensitive partial match)")

    @model_validator(mode="before")
    @classmethod
    def upgrade_legacy_format_ids(cls, values: dict) -> dict:
        """Convert dict format_ids to FormatId objects (AdCP v2.4 compliance)."""
        if not isinstance(values, dict):
            return values

        format_ids = values.get("format_ids")
        if format_ids and isinstance(format_ids, list):
            # Convert any dict format_ids to FormatId objects
            upgraded = []
            for fmt_id in format_ids:
                if isinstance(fmt_id, dict) and "agent_url" in fmt_id and "id" in fmt_id:
                    # Dict with FormatId structure - convert to FormatId object
                    upgraded.append(FormatId(**fmt_id))
                else:
                    # Already a FormatId object - pass through
                    upgraded.append(fmt_id)
            values["format_ids"] = upgraded

        return values


class ListCreativeFormatsResponse(AdCPBaseModel):
    """Response for list_creative_formats tool (AdCP v2.4 spec compliant).

    Per AdCP PR #113, this response contains ONLY domain data.
    Protocol fields (status, task_id, message, context_id) are added by the
    protocol layer (MCP, A2A, REST) via ProtocolEnvelope wrapper.
    """

    formats: list[Format] = Field(..., description="Full format definitions per AdCP spec")
    creative_agents: list[dict[str, Any]] | None = Field(
        None, description="Creative agents providing additional formats"
    )
    errors: list[Error] | None = Field(None, description="Task-specific errors and warnings")

    def __str__(self) -> str:
        """Return human-readable message for protocol layer.

        Used by both MCP (for display) and A2A (for task messages).
        Provides conversational text without adding non-spec fields to the schema.
        """
        count = len(self.formats)
        if count == 0:
            return "No creative formats are currently supported."
        elif count == 1:
            return "Found 1 creative format."
        else:
            return f"Found {count} creative formats."


# --- Creative Lifecycle ---
class CreativeGroup(BaseModel):
    """Groups creatives for organizational and management purposes."""

    group_id: str
    principal_id: str
    name: str
    description: str | None = None
    created_at: datetime
    tags: list[str] | None = []


class FormatId(BaseModel):
    """AdCP v2.4 format identifier object."""

    agent_url: str = Field(..., description="URL of the agent defining this format")
    id: str = Field(..., pattern=r"^[a-zA-Z0-9_-]+$", description="Format identifier")

    model_config = {"extra": "forbid"}

    def __str__(self) -> str:
        """Return human-readable format identifier for display in UIs."""
        return self.id

    def __repr__(self) -> str:
        """Return representation for debugging."""
        return f"FormatId(id='{self.id}', agent_url='{self.agent_url}')"


class Creative(BaseModel):
    """Individual creative asset in the creative library - AdCP spec compliant."""

    # Core identification fields
    creative_id: str
    name: str

    # AdCP spec compliant fields
    format: FormatId = Field(
        alias="format_id", description="Creative format identifier with agent_url namespace (AdCP v2.4+)"
    )
    url: str = Field(alias="content_uri", description="URL of the creative content per AdCP spec")

    @model_validator(mode="before")
    @classmethod
    def validate_format_id(cls, values):
        """Validate and upgrade format_id to AdCP v2.4 namespaced format.

        Automatically upgrades legacy string format_id to FormatId object with agent_url.
        Uses cached format mappings or defaults to AdCP reference implementation.
        """
        from src.core.format_cache import upgrade_legacy_format_id

        format_val = values.get("format_id") or values.get("format")
        if format_val is not None:
            try:
                # Upgrade to FormatId object (handles strings, dicts, objects)
                upgraded = upgrade_legacy_format_id(format_val)
                # Set both format_id and format to ensure consistency
                values["format_id"] = upgraded
                values["format"] = upgraded
            except ValueError as e:
                raise ValueError(f"Invalid format_id: {e}")
        return values

    media_url: str | None = Field(None, description="Alternative media URL (typically same as url)")
    click_url: str | None = Field(None, alias="click_through_url", description="Landing page URL per AdCP spec")

    # Content dimensions and properties (AdCP spec)
    duration: float | None = Field(None, description="Duration in seconds (for video/audio)", gt=-1)
    width: int | None = Field(None, description="Width in pixels (for video/display)", gt=-1)
    height: int | None = Field(None, description="Height in pixels (for video/display)", gt=-1)

    # Creative status and review (AdCP spec)
    status: str = Field(default="pending", description="Creative status per AdCP spec")
    platform_id: str | None = Field(None, description="Platform-specific ID assigned to the creative")
    review_feedback: str | None = Field(None, description="Feedback from platform review (if any)")

    # Compliance information (AdCP spec)
    compliance: dict[str, Any] | None = Field(None, description="Compliance review status")

    # Package assignments (AdCP spec)
    package_assignments: list[str] | None = Field(
        None, description="Package IDs or buyer_refs to assign this creative to"
    )

    # Multi-asset support (AdCP spec v2.4+)
    # Assets are keyed by asset_id from the format's asset_requirements
    # Example: {"main_image": {"asset_type": "image", "url": "..."}, "logo": {"asset_type": "image", "url": "..."}}
    assets: dict[str, dict[str, Any]] | None = Field(
        None,
        description="Assets keyed by asset_id from format asset_requirements (AdCP v2.4+). "
        "Keys MUST match asset_id values from the format specification.",
    )

    # === AdCP v1.3+ Creative Management Fields ===
    # Fully compliant with AdCP specification for third-party tags and native creatives

    snippet: str | None = Field(
        None, description="HTML/JS/VAST snippet for third-party creatives (mutually exclusive with media_url)"
    )

    snippet_type: Literal["html", "javascript", "vast_xml", "vast_url"] | None = Field(
        None, description="Type of snippet content (required when snippet is provided)"
    )

    template_variables: dict[str, Any] | None = Field(
        None,
        description="Variables for native ad templates per AdCP spec",
        examples=[
            {
                "headline": "Amazing Product",
                "body": "This product will change your life",
                "main_image_url": "https://cdn.example.com/product.jpg",
                "logo_url": "https://cdn.example.com/logo.png",
                "cta_text": "Shop Now",
                "advertiser_name": "Brand Name",
                "price": "$99.99",
                "star_rating": "4.5",
            }
        ],
    )

    # Platform-specific extension (not in core AdCP spec)
    delivery_settings: dict[str, Any] | None = Field(
        None,
        description="Platform-specific delivery configuration (extension)",
        examples=[
            {
                "safe_frame_compatible": True,
                "ssl_required": True,
                "orientation_lock": "FREE_ORIENTATION",
                "tracking_urls": ["https://..."],
            }
        ],
    )

    # Internal fields (not in AdCP spec, but available for internal use)
    principal_id: str  # Internal - not in AdCP spec
    group_id: str | None = None  # Internal - not in AdCP spec
    created_at: datetime  # Internal timestamp
    updated_at: datetime  # Internal timestamp
    has_macros: bool | None = False  # Internal processing
    macro_validation: dict[str, Any] | None = None  # Internal processing
    asset_mapping: dict[str, str] | None = Field(default_factory=dict)  # Internal mapping
    metadata: dict[str, Any] | None = Field(default_factory=dict)  # Internal metadata

    # Backward compatibility properties (deprecated)
    @property
    def format_id(self) -> str:
        """Backward compatibility for format_id.

        DEPRECATED: Use format instead.
        This property will be removed in a future version.
        """
        warnings.warn(
            "format_id is deprecated and will be removed in a future version. Use format instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.format.id

    @property
    def content_uri(self) -> str:
        """Backward compatibility for content_uri.

        DEPRECATED: Use url instead.
        This property will be removed in a future version.
        """
        warnings.warn(
            "content_uri is deprecated and will be removed in a future version. Use url instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.url

    @property
    def click_through_url(self) -> str | None:
        """Backward compatibility for click_through_url.

        DEPRECATED: Use click_url instead.
        This property will be removed in a future version.
        """
        warnings.warn(
            "click_through_url is deprecated and will be removed in a future version. Use click_url instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.click_url

    def get_format_string(self) -> str:
        """Get format ID string from FormatId object.

        Returns:
            String format identifier (e.g., "display_300x250")
        """
        return self.format.id

    def get_format_agent_url(self) -> str:
        """Get agent URL from FormatId object.

        Returns:
            Agent URL string (e.g., "https://creative.adcontextprotocol.org")
        """
        return self.format.agent_url

    def model_dump(self, **kwargs):
        """Override to provide AdCP-compliant responses while preserving internal fields."""
        # Default to excluding internal fields for AdCP compliance
        exclude = kwargs.get("exclude", set())
        if isinstance(exclude, set):
            # Add internal fields to exclude by default for AdCP compliance
            exclude.update(
                {
                    "principal_id",
                    "group_id",
                    "created_at",
                    "updated_at",
                    "has_macros",
                    "macro_validation",
                    "asset_mapping",
                    "metadata",
                    # Extended delivery fields (our implementation-specific extensions)
                    # These can be included by explicitly requesting them
                    "content_type",
                    "content",
                    "delivery_settings",
                }
            )
            kwargs["exclude"] = exclude

        data = super().model_dump(**kwargs)

        # Ensure media_url defaults to url if not set (AdCP spec requirement)
        if "media_url" in data and data["media_url"] is None and "url" in data:
            data["media_url"] = data["url"]

        # Set default compliance status if not provided
        if "compliance" in data and data["compliance"] is None:
            data["compliance"] = {"status": "pending", "issues": []}

        return data

    def model_dump_internal(self, **kwargs):
        """Dump including internal fields for database storage and internal processing."""
        # Don't exclude internal fields
        kwargs.pop("exclude", None)  # Remove any exclude parameter
        data = super().model_dump(**kwargs)

        # For internal dumps, also include alias field names for backward compatibility
        # This ensures that tests expecting both field names can access them
        if "format" in data:
            data["format_id"] = data["format"]
        if "url" in data:
            data["content_uri"] = data["url"]
        if "click_url" in data:
            data["click_through_url"] = data["click_url"]

        return data

    # === AdCP v1.3+ Helper Methods ===

    def get_creative_type(self) -> str:
        """Determine the creative type based on AdCP v1.3+ fields."""
        if self.snippet and self.snippet_type:
            if self.snippet_type in ["vast_xml", "vast_url"]:
                return "vast"
            else:
                return "third_party_tag"
        elif self.template_variables:
            return "native"
        elif self.media_url or (self.url and not self._is_html_snippet(self.url)):
            return "hosted_asset"
        elif self._is_html_snippet(self.url):
            # Auto-detect from URL for legacy support
            return "third_party_tag"
        else:
            return "hosted_asset"  # Default

    def _is_html_snippet(self, content: str) -> bool:
        """Detect if content is HTML/JS snippet rather than URL."""
        if not content:
            return False

        # Check for HTML/JS indicators
        html_indicators = ["<script", "<iframe", "<ins", "<div", "<span", "document.write", "innerHTML"]
        return any(indicator in content for indicator in html_indicators)

    def get_snippet_content(self) -> str | None:
        """Get the snippet content for third-party creatives (AdCP v1.3+ field)."""
        if self.snippet:
            return self.snippet
        elif self._is_html_snippet(self.url):
            return self.url  # Auto-detect from URL
        return None

    def get_template_variables_dict(self) -> dict[str, Any] | None:
        """Get native template variables (AdCP v1.3+ field)."""
        return self.template_variables

    def get_primary_content_url(self) -> str:
        """Get the primary content URL for hosted assets."""
        return self.media_url or self.url

    def set_third_party_snippet(self, snippet: str, snippet_type: str, settings: dict[str, Any] | None = None) -> None:
        """Convenience method to set up a third-party tag creative (AdCP v1.3+)."""
        self.snippet = snippet
        self.snippet_type = snippet_type  # type: ignore[assignment]
        if settings:
            self.delivery_settings = settings

    def set_native_template_variables(
        self, template_vars: dict[str, Any], settings: dict[str, Any] | None = None
    ) -> None:
        """Convenience method to set up a native creative (AdCP v1.3+)."""
        self.template_variables = template_vars
        if settings:
            self.delivery_settings = settings

    @model_validator(mode="after")
    def validate_creative_fields(self) -> "Creative":
        """Validate AdCP creative field requirements and mutual exclusivity."""
        # Check mutual exclusivity: media_url XOR snippet
        has_media = bool(self.media_url or (self.url and not self._is_html_snippet(self.url)))
        has_snippet = bool(self.snippet)

        if has_media and has_snippet:
            raise ValueError("Creative cannot have both media content and snippet - they are mutually exclusive")

        # Validate snippet_type is provided when snippet is present
        if self.snippet and not self.snippet_type:
            raise ValueError("snippet_type is required when snippet is provided")

        # Validate snippet_type values
        if self.snippet_type and not self.snippet:
            raise ValueError("snippet is required when snippet_type is provided")

        return self


class CreativeAdaptation(BaseModel):
    """Suggested adaptation or variant of a creative."""

    adaptation_id: str
    format_id: FormatId
    name: str
    description: str
    preview_url: str | None = None
    changes_summary: list[str] = Field(default_factory=list)
    rationale: str | None = None
    estimated_performance_lift: float | None = None  # Percentage improvement expected


class CreativeStatus(BaseModel):
    creative_id: str
    status: Literal["pending_review", "approved", "rejected", "adaptation_required"]
    detail: str
    estimated_approval_time: datetime | None = None
    suggested_adaptations: list[CreativeAdaptation] = Field(default_factory=list)


class CreativeAssignment(BaseModel):
    """Maps creatives to packages with distribution control."""

    assignment_id: str
    media_buy_id: str
    package_id: str
    creative_id: str

    # Distribution control
    weight: int | None = 100  # Relative weight for rotation
    percentage_goal: float | None = None  # Percentage of impressions
    rotation_type: Literal["weighted", "sequential", "even"] | None = "weighted"

    # Override settings (platform-specific)
    override_click_url: str | None = None
    override_start_date: datetime | None = None
    override_end_date: datetime | None = None

    # Targeting override (creative-specific targeting)
    targeting_overlay: Targeting | None = None

    is_active: bool = True

    @model_validator(mode="after")
    def validate_timezone_aware(self):
        """Validate that datetime override fields are timezone-aware.

        AdCP spec requires ISO 8601 datetime strings with timezone information.
        """
        if self.override_start_date and self.override_start_date.tzinfo is None:
            raise ValueError("override_start_date must be timezone-aware (ISO 8601 with timezone)")
        if self.override_end_date and self.override_end_date.tzinfo is None:
            raise ValueError("override_end_date must be timezone-aware (ISO 8601 with timezone)")
        return self


class AddCreativeAssetsRequest(AdCPBaseModel):
    """Request to add creative assets to a media buy (AdCP spec compliant)."""

    media_buy_id: str | None = None
    buyer_ref: str | None = None
    assets: list[Creative]  # Renamed from 'creatives' to match spec

    def model_validate(cls, values):
        # Ensure at least one of media_buy_id or buyer_ref is provided
        if not values.get("media_buy_id") and not values.get("buyer_ref"):
            raise ValueError("Either media_buy_id or buyer_ref must be provided")
        return values

    # Backward compatibility
    @property
    def creatives(self) -> list[Creative]:
        """Backward compatibility for existing code."""
        return self.assets


class AddCreativeAssetsResponse(AdCPBaseModel):
    """Response from adding creative assets (AdCP spec compliant)."""

    statuses: list[CreativeStatus]


# Legacy aliases for backward compatibility (to be removed)
SubmitCreativesRequest = AddCreativeAssetsRequest
SubmitCreativesResponse = AddCreativeAssetsResponse


class SyncCreativesRequest(AdCPBaseModel):
    """Request to sync creative assets to centralized library (AdCP v2.4 spec compliant).

    Supports bulk operations, patch updates, and assignment management.
    Creatives are synced to a central library and can be used across multiple media buys.
    """

    creatives: list[Creative] = Field(..., description="Array of creative assets to sync (create or update)")
    patch: bool = Field(
        False,
        description="When true, only provided fields are updated (partial update). When false, entire creative is replaced (full upsert).",
    )
    assignments: dict[str, list[str]] | None = Field(
        None, description="Optional bulk assignment of creatives to packages. Maps creative_id to array of package IDs."
    )
    delete_missing: bool = Field(
        False,
        description="When true, creatives not included in this sync will be archived. Use with caution for full library replacement.",
    )
    dry_run: bool = Field(
        False,
        description="When true, preview changes without applying them. Returns what would be created/updated/deleted.",
    )
    validation_mode: Literal["strict", "lenient"] = Field(
        "strict",
        description="Validation strictness. 'strict' fails entire sync on any validation error. 'lenient' processes valid creatives and reports errors.",
    )
    push_notification_config: dict[str, Any] | None = Field(
        None,
        description="Application-level webhook config (NOTE: Protocol-level push notifications via A2A/MCP transport take precedence)",
    )


class SyncSummary(BaseModel):
    """Summary of sync operation results."""

    total_processed: int = Field(..., ge=0, description="Total number of creatives processed")
    created: int = Field(..., ge=0, description="Number of new creatives created")
    updated: int = Field(..., ge=0, description="Number of existing creatives updated")
    unchanged: int = Field(..., ge=0, description="Number of creatives that were already up-to-date")
    failed: int = Field(..., ge=0, description="Number of creatives that failed validation or processing")
    deleted: int = Field(0, ge=0, description="Number of creatives deleted/archived (when delete_missing=true)")


class SyncCreativeResult(BaseModel):
    """Detailed result for a single creative in sync operation."""

    creative_id: str = Field(..., description="Creative ID from the request")
    action: Literal["created", "updated", "unchanged", "failed", "deleted"] = Field(
        ..., description="Action taken for this creative"
    )
    status: str | None = Field(None, description="Current approval status of the creative")
    platform_id: str | None = Field(None, description="Platform-specific ID assigned to the creative")
    changes: list[str] = Field(
        default_factory=list, description="List of field names that were modified (for 'updated' action)"
    )
    errors: list[str] = Field(default_factory=list, description="Validation or processing errors (for 'failed' action)")
    warnings: list[str] = Field(default_factory=list, description="Non-fatal warnings about this creative")
    review_feedback: str | None = Field(None, description="Feedback from platform review process")
    assigned_to: list[str] | None = Field(
        None,
        description="Package IDs this creative was successfully assigned to (only present when assignments were requested)",
    )
    assignment_errors: dict[str, str] | None = Field(
        None, description="Assignment errors by package ID (only present when assignment failures occurred)"
    )


class AssignmentsSummary(BaseModel):
    """Summary of assignment operations."""

    total_assignments_processed: int = Field(
        ..., ge=0, description="Total number of creative-package assignment operations processed"
    )
    assigned: int = Field(..., ge=0, description="Number of successful creative-package assignments")
    unassigned: int = Field(..., ge=0, description="Number of creative-package unassignments")
    failed: int = Field(..., ge=0, description="Number of assignment operations that failed")


class AssignmentResult(BaseModel):
    """Detailed result for creative-package assignments."""

    creative_id: str = Field(..., description="Creative that was assigned/unassigned")
    assigned_packages: list[str] = Field(
        default_factory=list, description="Packages successfully assigned to this creative"
    )
    unassigned_packages: list[str] = Field(
        default_factory=list, description="Packages successfully unassigned from this creative"
    )
    failed_packages: list[dict[str, str]] = Field(
        default_factory=list, description="Packages that failed to assign/unassign (package_id + error)"
    )


class SyncCreativesResponse(AdCPBaseModel):
    """Response from syncing creative assets (AdCP v2.4 spec compliant).

    Per AdCP PR #113, this response contains ONLY domain data.
    Protocol fields (status, task_id, message, context_id) are added by the
    protocol layer (MCP, A2A, REST) via ProtocolEnvelope wrapper.

    Official spec: /schemas/v1/media-buy/sync-creatives-response.json
    """

    # Required fields (per official spec)
    creatives: list[SyncCreativeResult] = Field(..., description="Results for each creative processed")

    # Optional fields (per official spec)
    dry_run: bool | None = Field(None, description="Whether this was a dry run (no actual changes made)")

    def __str__(self) -> str:
        """Return human-readable summary message for protocol envelope."""
        # Count actions from creatives list
        created = sum(1 for c in self.creatives if c.action == "created")
        updated = sum(1 for c in self.creatives if c.action == "updated")
        deleted = sum(1 for c in self.creatives if c.action == "deleted")
        failed = sum(1 for c in self.creatives if c.action == "failed")

        parts = []
        if created:
            parts.append(f"{created} created")
        if updated:
            parts.append(f"{updated} updated")
        if deleted:
            parts.append(f"{deleted} deleted")
        if failed:
            parts.append(f"{failed} failed")

        if parts:
            msg = f"Creative sync completed: {', '.join(parts)}"
        else:
            msg = "Creative sync completed: no changes"

        if self.dry_run:
            msg += " (dry run)"

        return msg


class ListCreativesRequest(AdCPBaseModel):
    """Request to list and search creative library (AdCP spec compliant)."""

    media_buy_id: str | None = Field(None, description="Filter by media buy ID")
    buyer_ref: str | None = Field(None, description="Filter by buyer reference")
    status: str | None = Field(None, description="Filter by creative status (pending, approved, rejected)")
    format: str | None = Field(None, description="Filter by creative format")
    tags: list[str] | None = Field(None, description="Filter by tags")
    created_after: datetime | None = Field(None, description="Filter by creation date")
    created_before: datetime | None = Field(None, description="Filter by creation date")
    search: str | None = Field(None, description="Search in creative names and descriptions")

    # AdCP spec fields
    filters: dict[str, Any] | None = Field(None, description="Advanced filtering options")
    pagination: dict[str, Any] | None = Field(None, description="Pagination parameters (page, limit)")
    sort: dict[str, Any] | None = Field(None, description="Sort configuration (field, direction)")
    fields: list[str] | None = Field(None, description="Specific fields to return")
    include_performance: bool = Field(False, description="Include performance metrics")
    include_assignments: bool = Field(False, description="Include package assignments")
    include_sub_assets: bool = Field(False, description="Include sub-assets (e.g., video thumbnails)")
    page: int = Field(1, ge=1, description="Page number for pagination")
    limit: int = Field(50, ge=1, le=1000, description="Number of results per page")
    sort_by: str | None = Field("created_date", description="Sort field (created_date, name, status)")
    sort_order: Literal["asc", "desc"] = Field("desc", description="Sort order")

    @model_validator(mode="after")
    def validate_timezone_aware(self):
        """Validate that datetime fields are timezone-aware.

        AdCP spec requires ISO 8601 datetime strings with timezone information.
        """
        if self.created_after and self.created_after.tzinfo is None:
            raise ValueError("created_after must be timezone-aware (ISO 8601 with timezone)")
        if self.created_before and self.created_before.tzinfo is None:
            raise ValueError("created_before must be timezone-aware (ISO 8601 with timezone)")
        return self


class QuerySummary(BaseModel):
    """Summary of the query that was executed."""

    total_matching: int = Field(..., ge=0, description="Total creatives matching filters")
    returned: int = Field(..., ge=0, description="Number of creatives in this response")
    filters_applied: list[str] = Field(default_factory=list)
    sort_applied: dict[str, str] | None = None


class Pagination(BaseModel):
    """Pagination information for navigating results."""

    limit: int = Field(..., ge=1)
    offset: int = Field(..., ge=0)
    has_more: bool = Field(...)
    total_pages: int | None = Field(None, ge=0)
    current_page: int | None = Field(None, ge=1)


class ListCreativesResponse(AdCPBaseModel):
    """Response from listing creative assets (AdCP v2.4 spec compliant).

    Per AdCP PR #113, this response contains ONLY domain data.
    Protocol fields (status, task_id, message, context_id) are added by the
    protocol layer (MCP, A2A, REST) via ProtocolEnvelope wrapper.
    """

    # Required AdCP domain fields
    query_summary: QuerySummary = Field(..., description="Summary of the query that was executed")
    pagination: Pagination = Field(..., description="Pagination information for navigating results")
    creatives: list[Creative] = Field(..., description="Array of creative assets")

    # Optional AdCP domain fields
    format_summary: dict[str, int] | None = Field(None, description="Breakdown by format type")
    status_summary: dict[str, int] | None = Field(None, description="Breakdown by creative status")

    def __str__(self) -> str:
        """Return human-readable summary message for protocol envelope."""
        count = self.query_summary.returned
        total = self.query_summary.total_matching
        if count == total:
            return f"Found {count} creative{'s' if count != 1 else ''}."
        else:
            return f"Showing {count} of {total} creatives."


class CheckCreativeStatusRequest(AdCPBaseModel):
    creative_ids: list[str]


class CheckCreativeStatusResponse(AdCPBaseModel):
    statuses: list[CreativeStatus]


# New creative management endpoints
class CreateCreativeGroupRequest(AdCPBaseModel):
    name: str
    description: str | None = None
    tags: list[str] | None = []


class CreateCreativeGroupResponse(AdCPBaseModel):
    group: CreativeGroup


class CreateCreativeRequest(AdCPBaseModel):
    """Create a creative in the library (not tied to a media buy)."""

    group_id: str | None = None
    format_id: str
    content_uri: str
    name: str
    click_through_url: str | None = None
    metadata: dict[str, Any] | None = {}


class CreateCreativeResponse(AdCPBaseModel):
    creative: Creative
    status: CreativeStatus
    suggested_adaptations: list[CreativeAdaptation] = Field(default_factory=list)

    def __str__(self) -> str:
        """Return human-readable text for MCP content field."""
        return f"Creative {self.creative.creative_id} created with status: {self.status.status}"


class AssignCreativeRequest(AdCPBaseModel):
    """Assign a creative from the library to a package."""

    media_buy_id: str
    package_id: str
    creative_id: str
    weight: int | None = 100
    percentage_goal: float | None = None
    rotation_type: Literal["weighted", "sequential", "even"] | None = "weighted"
    override_click_url: str | None = None
    override_start_date: datetime | None = None
    override_end_date: datetime | None = None
    targeting_overlay: Targeting | None = None

    @model_validator(mode="after")
    def validate_timezone_aware(self):
        """Validate that datetime override fields are timezone-aware.

        AdCP spec requires ISO 8601 datetime strings with timezone information.
        """
        if self.override_start_date and self.override_start_date.tzinfo is None:
            raise ValueError("override_start_date must be timezone-aware (ISO 8601 with timezone)")
        if self.override_end_date and self.override_end_date.tzinfo is None:
            raise ValueError("override_end_date must be timezone-aware (ISO 8601 with timezone)")
        return self


class AssignCreativeResponse(AdCPBaseModel):
    assignment: CreativeAssignment


class GetCreativesRequest(AdCPBaseModel):
    """Get creatives with optional filtering."""

    group_id: str | None = None
    media_buy_id: str | None = None
    status: str | None = None
    tags: list[str] | None = None
    include_assignments: bool = False


class GetCreativesResponse(AdCPBaseModel):
    creatives: list[Creative]
    assignments: list[CreativeAssignment] | None = None


# Admin tools
class GetPendingCreativesRequest(AdCPBaseModel):
    """Admin-only: Get all pending creatives across all principals."""

    principal_id: str | None = None  # Filter by principal if specified
    limit: int | None = 100


class GetPendingCreativesResponse(AdCPBaseModel):
    pending_creatives: list[dict[str, Any]]  # Includes creative + principal info


class ApproveCreativeRequest(AdCPBaseModel):
    """Admin-only: Approve or reject a creative."""

    creative_id: str
    action: Literal["approve", "reject"]
    reason: str | None = None


class ApproveCreativeResponse(AdCPBaseModel):
    creative_id: str
    new_status: str
    detail: str


class AdaptCreativeRequest(AdCPBaseModel):
    media_buy_id: str
    original_creative_id: str
    target_format_id: str
    new_creative_id: str
    instructions: str | None = None


# --- Brand Manifest Models (AdCP v1.8.0) ---


class LogoAsset(BaseModel):
    """Logo asset with metadata."""

    url: str = Field(..., description="URL to logo asset")
    width: int | None = Field(None, ge=1, description="Logo width in pixels")
    height: int | None = Field(None, ge=1, description="Logo height in pixels")
    tags: list[str] | None = Field(None, description="Tags for logo usage (e.g., 'primary', 'square', 'white')")


class BrandColors(BaseModel):
    """Brand color palette."""

    primary: str | None = Field(None, pattern="^#[0-9A-Fa-f]{6}$", description="Primary brand color (hex)")
    secondary: str | None = Field(None, pattern="^#[0-9A-Fa-f]{6}$", description="Secondary brand color (hex)")
    accent: str | None = Field(None, pattern="^#[0-9A-Fa-f]{6}$", description="Accent color (hex)")
    background: str | None = Field(None, pattern="^#[0-9A-Fa-f]{6}$", description="Background color (hex)")
    text: str | None = Field(None, pattern="^#[0-9A-Fa-f]{6}$", description="Text color (hex)")


class FontGuidance(BaseModel):
    """Typography guidelines."""

    primary: str | None = Field(None, description="Primary font family")
    secondary: str | None = Field(None, description="Secondary font family")
    weights: list[str] | None = Field(None, description="Recommended font weights")


class BrandAsset(BaseModel):
    """Multimedia brand asset."""

    url: str = Field(..., description="URL to brand asset")
    asset_type: str = Field(..., description="Asset type (image, video, audio, etc.)")
    tags: list[str] | None = Field(None, description="Asset tags for categorization")
    width: int | None = Field(None, ge=1, description="Asset width in pixels")
    height: int | None = Field(None, ge=1, description="Asset height in pixels")
    duration: float | None = Field(None, ge=0, description="Duration in seconds (for video/audio)")


class ProductCatalog(BaseModel):
    """E-commerce product feed information."""

    url: str = Field(..., description="URL to product catalog feed")
    format: str | None = Field(None, description="Feed format (e.g., 'google_merchant', 'json', 'xml')")


class BrandManifest(BaseModel):
    """Standardized brand information manifest for creative generation and media buying.

    Per AdCP spec, either url OR name is required (at least one must be present).
    This is a legacy model - prefer using generated schemas from schemas_generated/.
    """

    # At least one required (enforced by anyOf in AdCP spec)
    url: str | None = Field(None, description="Brand website URL")
    name: str | None = Field(None, description="Brand/business name")

    # Optional fields
    logos: list[LogoAsset] | None = Field(None, description="Brand logo assets")
    colors: BrandColors | None = Field(None, description="Brand color palette")
    fonts: FontGuidance | None = Field(None, description="Typography guidelines")
    tone: str | None = Field(None, description="Brand voice and tone description")
    tagline: str | None = Field(None, description="Brand tagline or slogan")
    assets: list[BrandAsset] | None = Field(None, description="Additional brand assets")
    product_catalog: ProductCatalog | None = Field(None, description="Product catalog information")
    disclaimers: list[str] | None = Field(None, description="Required legal disclaimers")
    industry: str | None = Field(None, description="Industry/category")
    target_audience: str | None = Field(None, description="Target audience description")
    contact_info: dict[str, Any] | None = Field(None, description="Contact information")
    metadata: dict[str, Any] | None = Field(None, description="Creation/update metadata")

    # NOTE: Legacy model kept for backward compatibility with tests
    # For new code, use generated schemas which properly handle anyOf constraints


class BrandManifestRef(BaseModel):
    """Brand manifest reference - can be inline object or URL string.

    Per AdCP spec, this supports two formats:
    1. Inline BrandManifest object
    2. URL string pointing to hosted manifest JSON
    """

    # We'll handle this as a union type during validation
    manifest: BrandManifest | str = Field(
        ...,
        description="Brand manifest: either inline BrandManifest object or URL string to hosted manifest",
    )

    @model_validator(mode="before")
    @classmethod
    def parse_manifest_ref(cls, values):
        """Handle both inline manifest and URL string formats."""
        if isinstance(values, str):
            # Direct string = URL reference
            return {"manifest": values}
        elif isinstance(values, dict):
            if "manifest" not in values:
                # If no manifest field, treat entire dict as inline manifest
                return {"manifest": values}
        return values


class Package(BaseModel):
    """Package object - AdCP spec compliant.

    Note: In create-media-buy-request, clients only provide buyer_ref+products.
    Server generates package_id and sets initial status per AdCP package schema.
    """

    # AdCP Package object fields (required in responses, generated during creation)
    package_id: str | None = Field(None, description="Publisher's unique identifier for the package")
    status: Literal["draft", "active", "paused", "completed"] | None = Field(None, description="Status of the package")

    # AdCP optional fields
    buyer_ref: str | None = Field(None, description="Buyer's reference identifier for this package")
    product_id: str | None = Field(None, description="ID of the product this package is based on (single product)")
    products: list[str] | None = Field(None, description="Array of product IDs to include in this package")
    budget: Budget | float | None = Field(None, description="Package-specific budget (Budget object or number)")
    impressions: float | None = Field(None, description="Impression goal for this package", gt=-1)
    targeting_overlay: Targeting | None = Field(None, description="Package-specific targeting")
    creative_ids: list[str] | None = Field(None, description="Creative IDs to assign to this package")
    creative_assignments: list[dict[str, Any]] | None = Field(
        None, description="Creative assets assigned to this package"
    )
    # AdCP v2.4 request field (input) - array of FormatId objects
    format_ids: list[FormatId] | None = Field(
        None,
        description="Format IDs for this package (array of FormatId objects with agent_url and id per AdCP v2.4)",
    )

    # AdCP v2.4 response field (output) - array of FormatId objects
    format_ids_to_provide: list[FormatId] | None = Field(
        None,
        description="Format IDs that creative assets will be provided for this package (array of FormatId objects per AdCP v2.4)",
    )

    # NEW: Pricing model selection (AdCP PR #88)
    pricing_model: PricingModel | None = Field(
        None, description="Selected pricing model for this package (from product's pricing_options)"
    )
    bid_price: float | None = Field(
        None, ge=0, description="Bid price for auction-based pricing (required if pricing option is auction-based)"
    )
    pacing: Literal["even", "asap", "front_loaded"] | None = Field(None, description="Pacing strategy for this package")

    # Internal fields (not in AdCP spec)
    tenant_id: str | None = Field(None, description="Internal: Tenant ID for multi-tenancy")
    media_buy_id: str | None = Field(None, description="Internal: Associated media buy ID")
    platform_line_item_id: str | None = Field(
        None, description="Internal: Platform-specific line item ID for creative association"
    )
    created_at: datetime | None = Field(None, description="Internal: Creation timestamp")
    updated_at: datetime | None = Field(None, description="Internal: Last update timestamp")
    metadata: dict[str, Any] | None = Field(None, description="Internal: Additional metadata")

    @model_validator(mode="before")
    @classmethod
    def upgrade_legacy_format_ids(cls, values: dict) -> dict:
        """Convert dict format_ids to FormatId objects (AdCP v2.4 compliance)."""
        if not isinstance(values, dict):
            return values

        format_ids = values.get("format_ids")
        if format_ids and isinstance(format_ids, list):
            # Convert any dict format_ids to FormatId objects
            upgraded = []
            for fmt_id in format_ids:
                if isinstance(fmt_id, dict) and "agent_url" in fmt_id and "id" in fmt_id:
                    # Dict with FormatId structure - convert to FormatId object
                    upgraded.append(FormatId(**fmt_id))
                else:
                    # Already a FormatId object - pass through
                    upgraded.append(fmt_id)
            values["format_ids"] = upgraded

        return values

    def model_dump(self, **kwargs):
        """Override to provide AdCP-compliant responses while preserving internal fields."""
        # Default to excluding internal fields for AdCP compliance
        exclude = kwargs.get("exclude", set())
        if isinstance(exclude, set):
            # Add internal fields to exclude by default
            # Legacy format fields also excluded (migrated to format_ids_to_provide)
            exclude.update(
                {
                    "tenant_id",
                    "media_buy_id",
                    "platform_line_item_id",
                    "created_at",
                    "updated_at",
                    "metadata",
                    "format_ids",
                    "formats_to_provide",
                }
            )
            kwargs["exclude"] = exclude

        data = super().model_dump(**kwargs)

        # Ensure required AdCP fields are present for responses
        # (These should be set during package creation/processing)
        if data.get("package_id") is None:
            raise ValueError("Package missing required package_id for AdCP response")
        if data.get("status") is None:
            raise ValueError("Package missing required status for AdCP response")

        return data

    def model_dump_internal(self, **kwargs):
        """Dump including internal fields for database storage and internal processing."""
        # Don't exclude internal fields
        kwargs.pop("exclude", None)  # Remove any exclude parameter
        return super().model_dump(**kwargs)


# --- Media Buy Lifecycle ---
class CreateMediaBuyRequest(AdCPBaseModel):
    # Required AdCP v2.2.0 fields (per https://adcontextprotocol.org/schemas/v1/media-buy/create-media-buy-request.json)
    buyer_ref: str | None = Field(
        None, description="Buyer reference for tracking (optional, buyer-provided identifier)"
    )
    brand_manifest: "BrandManifest | str" = Field(
        ...,
        description="Brand information manifest (inline object or URL string). REQUIRED per AdCP v2.2.0 spec.",
    )
    packages: list[Package] = Field(..., description="Array of packages with products and budgets (REQUIRED)")
    start_time: datetime | Literal["asap"] = Field(
        ..., description="Campaign start time: ISO 8601 datetime or 'asap' for immediate start (REQUIRED)"
    )
    end_time: datetime = Field(..., description="Campaign end time (ISO 8601) (REQUIRED)")
    budget: Budget | float = Field(
        ...,
        description="Overall campaign budget (Budget object or number). Currency determined by package pricing options (REQUIRED).",
    )

    # Deprecated fields (for backward compatibility - legacy format conversion only)
    currency: str | None = Field(
        None,
        pattern="^[A-Z]{3}$",
        description="DEPRECATED: Use Package.currency instead. Currency code that will be copied to all packages for backward compatibility.",
    )

    # Legacy fields (for backward compatibility)
    product_ids: list[str] | None = Field(None, description="Legacy: Product IDs (converted to packages)")
    start_date: date | None = Field(None, description="Legacy: Start date (converted to start_time)")
    end_date: date | None = Field(None, description="Legacy: End date (converted to end_time)")
    total_budget: float | None = Field(None, description="Legacy: Total budget (converted to Budget object)")

    # Common fields
    campaign_name: str | None = Field(None, description="Campaign name for display purposes")
    targeting_overlay: Targeting | None = None
    po_number: str | None = Field(None, description="Purchase order number for tracking")
    pacing: Literal["even", "asap", "daily_budget"] = "even"  # Legacy field
    daily_budget: float | None = None  # Legacy field
    creatives: list[Creative] | None = None
    reporting_webhook: dict[str, Any] | None = Field(
        None, description="Optional webhook configuration for automated reporting delivery"
    )
    # AXE signal requirements
    required_axe_signals: list[str] | None = None  # Required targeting signals
    enable_creative_macro: bool | None = False  # Enable AXE to provide creative_macro signal
    strategy_id: str | None = Field(
        None,
        description="Optional strategy ID for linking operations and enabling simulation/testing modes",
    )

    # Webhook/callback support for MCP protocol (AdCP spec naming)
    webhook_url: str | None = Field(
        None,
        description="Optional webhook URL for status notifications (MCP protocol). For A2A, use A2A push notification methods instead.",
    )
    webhook_auth_token: str | None = Field(
        None,
        description="Optional authentication token for webhook callbacks (MCP protocol). Used as Bearer token in Authorization header.",
    )
    push_notification_config: dict[str, Any] | None = Field(
        None,
        description="Application-level webhook config (NOTE: Protocol-level push notifications via A2A/MCP transport take precedence)",
    )

    @model_validator(mode="before")
    @classmethod
    def handle_legacy_format(cls, values):
        """Convert legacy format to new format."""
        if not isinstance(values, dict):
            return values

        # Handle brand_manifest field (can be inline object or URL string)
        # If it's a string (URL), leave as-is - Pydantic will handle it
        # If it's a dict (inline manifest), Pydantic will parse it as BrandManifest
        # No conversion needed, Pydantic union type handles both

        # If using legacy format, convert to new format
        if "product_ids" in values and not values.get("packages"):
            # Convert product_ids to packages
            # Note: AdCP create-media-buy-request only requires products from client
            # Server generates package_id and initial status per AdCP package schema
            # buyer_ref is optional and should only be set by the buyer/client
            product_ids = values.get("product_ids") or []  # Handle None
            packages = []
            for i, pid in enumerate(product_ids):
                package_uuid = uuid.uuid4().hex[:6]
                packages.append(
                    {
                        "package_id": f"pkg_{i}_{package_uuid}",  # Server-generated per AdCP spec
                        # buyer_ref is NOT auto-generated - it's the buyer's identifier
                        "product_id": pid,  # Use product_id (singular) per current validation
                        "status": "draft",  # Server sets initial status per AdCP package schema
                    }
                )
            values["packages"] = packages

        # Convert dates to datetimes with defensive handling
        # Handle start_date -> start_time conversion (only if start_time not provided)
        if "start_date" in values and not values.get("start_time"):
            start_date = values["start_date"]
            if start_date is not None:
                if isinstance(start_date, str):
                    start_date = date.fromisoformat(start_date)
                values["start_time"] = datetime.combine(start_date, time.min, tzinfo=UTC)

        # Handle end_date -> end_time conversion (only if end_time not provided)
        if "end_date" in values and not values.get("end_time"):
            end_date = values["end_date"]
            if end_date is not None:
                if isinstance(end_date, str):
                    end_date = date.fromisoformat(end_date)
                values["end_time"] = datetime.combine(end_date, time.max, tzinfo=UTC)

        # Convert total_budget to Budget object (only if not None)
        if "total_budget" in values and values["total_budget"] is not None and not values.get("budget"):
            total_budget = values["total_budget"]
            pacing = values.get("pacing", "even")
            daily_cap = values.get("daily_budget")

            values["budget"] = {
                "total": total_budget,
                "currency": "USD",  # Default currency
                "pacing": pacing,
                "daily_cap": daily_cap,
            }

        # buyer_ref is optional and should NOT be auto-generated
        # It's the buyer's identifier, not ours to create

        return values

    @model_validator(mode="after")
    def validate_timezone_aware(self):
        """Validate that datetime fields are timezone-aware.

        AdCP spec requires ISO 8601 datetime strings with timezone information.
        This validator ensures all datetime fields have timezone info.
        The literal string 'asap' is also valid per AdCP v1.7.0.
        """
        if self.start_time and self.start_time != "asap" and self.start_time.tzinfo is None:
            raise ValueError("start_time must be timezone-aware (ISO 8601 with timezone) or 'asap'")
        if self.end_time and self.end_time.tzinfo is None:
            raise ValueError("end_time must be timezone-aware (ISO 8601 with timezone)")
        return self

    # Note: Currency validation removed - currency comes from product pricing options
    # and is looked up dynamically when needed. We keep req.currency as deprecated
    # for backward compatibility but don't enforce currency consistency at request time.

    # Backward compatibility properties for old field names
    @property
    def flight_start_date(self) -> date | None:
        """Backward compatibility for old field name."""
        if isinstance(self.start_time, datetime):
            return self.start_time.date()
        return None

    @property
    def flight_end_date(self) -> date | None:
        """Backward compatibility for old field name."""
        return self.end_time.date() if self.end_time else None

    def get_total_budget(self) -> float:
        """Get total budget, handling both new and legacy formats."""
        # AdCP v2.4: Sum budgets from all packages
        if self.packages:
            total = 0.0
            for package in self.packages:
                # Handle both Package objects and dicts
                if isinstance(package, dict):
                    budget = package.get("budget")
                    if budget:
                        # Budget can be: dict, number, or Budget object
                        if isinstance(budget, dict):
                            total += budget.get("total", 0.0)
                        elif isinstance(budget, int | float):
                            total += float(budget)
                        else:
                            total += budget.total
                else:
                    # Package object
                    if package.budget:
                        # Budget can be number or Budget object
                        if isinstance(package.budget, int | float):
                            total += float(package.budget)
                        else:
                            total += package.budget.total
            if total > 0:
                return total

        # Legacy format: top-level budget
        if self.budget:
            if isinstance(self.budget, int | float):
                return float(self.budget)
            return self.budget.total
        return self.total_budget or 0.0

    def get_product_ids(self) -> list[str]:
        """Extract all product IDs from packages for backward compatibility.

        Supports both singular product_id and plural products fields per AdCP spec.
        """
        if self.packages:
            product_ids = []
            for package in self.packages:
                # Check both products (array) and product_id (single) fields
                if package.products:
                    product_ids.extend(package.products)
                elif package.product_id:
                    product_ids.append(package.product_id)
            return product_ids
        return self.product_ids or []


class CreateMediaBuyResponse(AdCPBaseModel):
    """Response from create_media_buy operation (AdCP v2.4 spec compliant).

    Per AdCP PR #113, this response contains ONLY domain data.
    Protocol fields (status, task_id, message, context_id) are added by the
    protocol layer (MCP, A2A, REST) via ProtocolEnvelope wrapper.
    """

    # Required AdCP domain fields
    buyer_ref: str = Field(..., description="Buyer's reference identifier for this media buy")

    # Optional AdCP domain fields
    media_buy_id: str | None = Field(None, description="Publisher's unique identifier for the created media buy")
    creative_deadline: datetime | None = Field(None, description="ISO 8601 timestamp for creative upload deadline")
    packages: list[dict[str, Any]] = Field(default_factory=list, description="Created packages with IDs")
    errors: list[Error] | None = Field(None, description="Task-specific errors and warnings")

    # Internal fields (excluded from AdCP responses)
    workflow_step_id: str | None = None

    def model_dump(self, **kwargs):
        """Override to provide AdCP-compliant responses while preserving internal fields."""
        # Default to excluding internal fields for AdCP compliance
        exclude = kwargs.get("exclude", set())
        if isinstance(exclude, set):
            # Add internal fields to exclude by default
            exclude.add("workflow_step_id")
            kwargs["exclude"] = exclude
        return super().model_dump(**kwargs)

    def model_dump_internal(self, **kwargs):
        """Dump including internal fields for database storage and internal processing."""
        # Don't exclude internal fields
        kwargs.pop("exclude", None)  # Remove any exclude parameter
        return super().model_dump(**kwargs)

    def __str__(self) -> str:
        """Return human-readable summary message for protocol envelope."""
        if self.media_buy_id:
            return f"Media buy {self.media_buy_id} created successfully."
        elif self.errors:
            return f"Media buy creation for {self.buyer_ref} encountered {len(self.errors)} error(s)."
        else:
            return f"Media buy {self.buyer_ref} created."


class CheckMediaBuyStatusRequest(AdCPBaseModel):
    media_buy_id: str | None = None
    buyer_ref: str | None = None
    strategy_id: str | None = Field(
        None,
        description="Optional strategy ID for consistent simulation/testing context",
    )

    def model_validate(cls, values):
        # Ensure at least one of media_buy_id or buyer_ref is provided
        if not values.get("media_buy_id") and not values.get("buyer_ref"):
            raise ValueError("Either media_buy_id or buyer_ref must be provided")
        return values


class CheckMediaBuyStatusResponse(AdCPBaseModel):
    media_buy_id: str
    buyer_ref: str
    status: str  # pending_creative, active, paused, completed, failed
    packages: list[dict[str, Any]] | None = None
    budget_spent: Budget | None = None
    budget_remaining: Budget | None = None
    creative_count: int = 0


class LegacyUpdateMediaBuyRequest(AdCPBaseModel):
    """Legacy update request - kept for backward compatibility."""

    media_buy_id: str
    new_budget: float | None = None
    new_targeting_overlay: Targeting | None = None
    creative_assignments: dict[str, list[str]] | None = None


class GetMediaBuyDeliveryRequest(AdCPBaseModel):
    """Request delivery data for one or more media buys.

    AdCP-compliant request matching official get-media-buy-delivery-request schema.

    Examples:
    - Single buy: media_buy_ids=["buy_123"]
    - Multiple buys: buyer_refs=["ref_123", "ref_456"]
    - All active buys: status_filter="active"
    - All buys: status_filter="all"
    - Date range: start_date="2025-01-01", end_date="2025-01-31"
    """

    media_buy_ids: list[str] | None = Field(
        None, description="Array of publisher media buy IDs to get delivery data for"
    )
    buyer_refs: list[str] | None = Field(None, description="Array of buyer reference IDs to get delivery data for")
    status_filter: str | list[str] | None = Field(
        None,
        description="Filter by status. Can be a single status or array of statuses: 'active', 'pending', 'paused', 'completed', 'failed', 'all'",
    )
    start_date: str | None = Field(
        None, description="Start date for reporting period (YYYY-MM-DD)", pattern=r"^\d{4}-\d{2}-\d{2}$"
    )
    end_date: str | None = Field(
        None, description="End date for reporting period (YYYY-MM-DD)", pattern=r"^\d{4}-\d{2}-\d{2}$"
    )


# AdCP-compliant delivery models
class DeliveryTotals(BaseModel):
    """Aggregate metrics for a media buy or package."""

    impressions: float = Field(ge=0, description="Total impressions delivered")
    spend: float = Field(ge=0, description="Total amount spent")
    clicks: float | None = Field(None, ge=0, description="Total clicks (if applicable)")
    ctr: float | None = Field(None, ge=0, le=1, description="Click-through rate (clicks/impressions)")
    video_completions: float | None = Field(None, ge=0, description="Total video completions (if applicable)")
    completion_rate: float | None = Field(
        None, ge=0, le=1, description="Video completion rate (completions/impressions)"
    )


class PackageDelivery(BaseModel):
    """Metrics broken down by package."""

    package_id: str = Field(description="Publisher's package identifier")
    buyer_ref: str | None = Field(None, description="Buyer's reference identifier for this package")
    impressions: float = Field(ge=0, description="Package impressions")
    spend: float = Field(ge=0, description="Package spend")
    clicks: float | None = Field(None, ge=0, description="Package clicks")
    video_completions: float | None = Field(None, ge=0, description="Package video completions")
    pacing_index: float | None = Field(
        None, ge=0, description="Delivery pace (1.0 = on track, <1.0 = behind, >1.0 = ahead)"
    )


class DailyBreakdown(BaseModel):
    """Day-by-day delivery metrics."""

    date: str = Field(description="Date (YYYY-MM-DD)", pattern=r"^\d{4}-\d{2}-\d{2}$")
    impressions: float = Field(ge=0, description="Daily impressions")
    spend: float = Field(ge=0, description="Daily spend")


class MediaBuyDeliveryData(BaseModel):
    """AdCP-compliant delivery data for a single media buy."""

    media_buy_id: str = Field(description="Publisher's media buy identifier")
    buyer_ref: str | None = Field(None, description="Buyer's reference identifier for this media buy")
    status: Literal["ready", "active", "paused", "completed", "failed"] = Field(
        description="Current media buy status. 'ready' means scheduled to go live at flight start date."
    )
    totals: DeliveryTotals = Field(description="Aggregate metrics for this media buy across all packages")
    by_package: list[PackageDelivery] = Field(description="Metrics broken down by package")
    daily_breakdown: list[DailyBreakdown] | None = Field(None, description="Day-by-day delivery")


class ReportingPeriod(BaseModel):
    """Date range for the report."""

    start: str = Field(description="ISO 8601 start timestamp")
    end: str = Field(description="ISO 8601 end timestamp")


class AggregatedTotals(BaseModel):
    """Combined metrics across all returned media buys."""

    impressions: float = Field(ge=0, description="Total impressions delivered across all media buys")
    spend: float = Field(ge=0, description="Total amount spent across all media buys")
    clicks: float | None = Field(None, ge=0, description="Total clicks across all media buys (if applicable)")
    video_completions: float | None = Field(
        None, ge=0, description="Total video completions across all media buys (if applicable)"
    )
    media_buy_count: int = Field(ge=0, description="Number of media buys included in the response")


class GetMediaBuyDeliveryResponse(AdCPBaseModel):
    """AdCP v2.4-compliant response for get_media_buy_delivery task.

    Per AdCP PR #113, this response contains ONLY domain data.
    Protocol fields (status, task_id, message, context_id) are added by the
    protocol layer (MCP, A2A, REST) via ProtocolEnvelope wrapper.
    """

    reporting_period: ReportingPeriod = Field(..., description="Date range for the report")
    currency: str = Field(..., description="ISO 4217 currency code", pattern=r"^[A-Z]{3}$")
    aggregated_totals: AggregatedTotals = Field(..., description="Combined metrics across all returned media buys")
    media_buy_deliveries: list[MediaBuyDeliveryData] = Field(
        ..., description="Array of delivery data for each media buy"
    )
    errors: list[dict] | None = Field(None, description="Task-specific errors and warnings")

    def __str__(self) -> str:
        """Return human-readable summary message for protocol envelope."""
        count = len(self.media_buy_deliveries)
        if count == 0:
            return "No delivery data found for the specified period."
        elif count == 1:
            return "Retrieved delivery data for 1 media buy."
        return f"Retrieved delivery data for {count} media buys."


# Deprecated - kept for backward compatibility
class GetAllMediaBuyDeliveryRequest(AdCPBaseModel):
    """DEPRECATED: Use GetMediaBuyDeliveryRequest with filter='all' instead."""

    today: date
    media_buy_ids: list[str] | None = None


class GetAllMediaBuyDeliveryResponse(AdCPBaseModel):
    """DEPRECATED: Use GetMediaBuyDeliveryResponse instead."""

    deliveries: list[MediaBuyDeliveryData]
    total_spend: float
    total_impressions: int
    active_count: int
    summary_date: date


# --- Additional Schema Classes ---
class MediaPackage(BaseModel):
    package_id: str
    name: str
    delivery_type: Literal["guaranteed", "non_guaranteed"]
    cpm: float
    impressions: int
    format_ids: list[FormatId]  # FormatId objects per AdCP spec
    targeting_overlay: Optional["Targeting"] = None
    buyer_ref: str | None = None  # Optional buyer reference from request package
    product_id: str | None = None  # Product ID for this package
    budget: Optional["Budget"] = None  # Budget information from request


class PackagePerformance(BaseModel):
    package_id: str
    performance_index: float


class AssetStatus(BaseModel):
    asset_id: str | None = None  # Asset identifier
    creative_id: str | None = None  # GAM creative ID (may be None for pending/failed)
    status: str  # Status: draft, active, submitted, failed, etc.
    message: str | None = None  # Status message
    workflow_step_id: str | None = None  # HITL workflow step ID for manual approval


class UpdateMediaBuyResponse(AdCPBaseModel):
    """Response from update_media_buy operation (AdCP v2.4 spec compliant).

    Per AdCP PR #113, this response contains ONLY domain data.
    Protocol fields (status, task_id, message, context_id) are added by the
    protocol layer (MCP, A2A, REST) via ProtocolEnvelope wrapper.
    """

    # Required AdCP domain fields
    media_buy_id: str = Field(..., description="Publisher's unique identifier for the media buy")
    buyer_ref: str = Field(..., description="Buyer's reference identifier for this media buy")

    # Optional AdCP domain fields
    implementation_date: datetime | None = Field(None, description="When the update will take effect")
    affected_packages: list[dict[str, Any]] = Field(default_factory=list, description="Packages affected by update")
    errors: list[Error] | None = Field(None, description="Task-specific errors and warnings")

    # Internal fields (excluded from AdCP responses)
    workflow_step_id: str | None = None

    def model_dump(self, **kwargs):
        """Override to provide AdCP-compliant responses while preserving internal fields."""
        # Default to excluding internal fields for AdCP compliance
        exclude = kwargs.get("exclude", set())
        if isinstance(exclude, set):
            # Add internal fields to exclude by default
            exclude.add("workflow_step_id")
            kwargs["exclude"] = exclude
        return super().model_dump(**kwargs)

    def model_dump_internal(self, **kwargs):
        """Dump including internal fields for database storage and internal processing."""
        # Don't exclude internal fields
        kwargs.pop("exclude", None)  # Remove any exclude parameter
        return super().model_dump(**kwargs)

    def __str__(self) -> str:
        """Return human-readable summary message for protocol envelope."""
        if self.errors:
            return f"Media buy {self.media_buy_id} update encountered {len(self.errors)} error(s)."
        elif self.affected_packages:
            return f"Media buy {self.media_buy_id} updated: {len(self.affected_packages)} package(s) affected."
        else:
            return f"Media buy {self.media_buy_id} updated successfully."


# Unified update models
class PackageUpdate(BaseModel):
    """Updates to apply to a specific package."""

    package_id: str
    active: bool | None = None  # True to activate, False to pause
    budget: Budget | float | None = None  # Budget object (AdCP spec) or legacy float
    impressions: int | None = None  # Direct impression goal (overrides budget calculation)
    cpm: float | None = None  # Update CPM rate
    daily_budget: float | None = None  # Daily spend cap
    daily_impressions: int | None = None  # Daily impression cap
    pacing: Literal["even", "asap", "front_loaded"] | None = None
    creative_ids: list[str] | None = None  # Update creative assignments
    targeting_overlay: Targeting | None = None  # Package-specific targeting refinements


class UpdatePackageRequest(AdCPBaseModel):
    """Update one or more packages within a media buy.

    Uses PATCH semantics: Only packages mentioned are affected.
    Omitted packages remain unchanged.
    To remove a package from delivery, set active=false.
    To add new packages, use create_media_buy or add_packages (future tool).
    """

    media_buy_id: str
    packages: list[PackageUpdate]  # List of package updates
    today: date | None = None  # For testing/simulation


# AdCP-compliant supporting models for update-media-buy-request
class AdCPPackageUpdate(BaseModel):
    """Package-specific update per AdCP update-media-buy-request schema."""

    package_id: str | None = None
    buyer_ref: str | None = None
    budget: Budget | None = None
    active: bool | None = None
    targeting_overlay: Targeting | None = None
    creative_ids: list[str] | None = None

    # NOTE: No Python validator needed - AdCP schema has oneOf constraint for package_id/buyer_ref
    # Schema validation at /schemas/v1/media-buy/update-media-buy-request.json enforces this


class UpdateMediaBuyRequest(AdCPBaseModel):
    """AdCP-compliant update media buy request per update-media-buy-request schema.

    Fully compliant with AdCP specification:
    - OneOf constraint: either media_buy_id OR buyer_ref (not both)
    - Uses start_time/end_time (datetime) per AdCP spec
    - Budget object contains currency and pacing
    - Packages array for package-specific updates
    - All fields optional except the oneOf identifier
    """

    # AdCP oneOf constraint: either media_buy_id OR buyer_ref
    media_buy_id: str | None = None
    buyer_ref: str | None = None

    # Campaign-level updates (all optional per AdCP spec)
    active: bool | None = None
    start_time: datetime | Literal["asap"] | None = None  # AdCP uses datetime or 'asap', not date
    end_time: datetime | None = None  # AdCP uses datetime, not date
    budget: Budget | None = None  # Budget object contains currency/pacing
    packages: list[AdCPPackageUpdate] | None = None
    push_notification_config: dict[str, Any] | None = Field(
        None,
        description="Application-level webhook config (NOTE: Protocol-level push notifications via A2A/MCP transport take precedence)",
    )
    today: date | None = Field(None, exclude=True, description="For testing/simulation only - not part of AdCP spec")

    # NOTE: No Python validator needed for oneOf constraint - AdCP schema enforces media_buy_id/buyer_ref oneOf
    # Schema validation at /schemas/v1/media-buy/update-media-buy-request.json enforces this

    @model_validator(mode="after")
    def validate_timezone_aware(self):
        """Validate that datetime fields are timezone-aware.

        AdCP spec requires ISO 8601 datetime strings with timezone information.
        This validator ensures all datetime fields have timezone info.
        The literal string 'asap' is also valid per AdCP v1.7.0.
        """
        if self.start_time and self.start_time != "asap" and self.start_time.tzinfo is None:
            raise ValueError("start_time must be timezone-aware (ISO 8601 with timezone) or 'asap'")
        if self.end_time and self.end_time.tzinfo is None:
            raise ValueError("end_time must be timezone-aware (ISO 8601 with timezone)")
        return self

    # Backward compatibility properties (deprecated)
    @property
    def flight_start_date(self) -> date | None:
        """DEPRECATED: Use start_time instead. Backward compatibility only."""
        if isinstance(self.start_time, datetime):
            warnings.warn("flight_start_date is deprecated. Use start_time instead.", DeprecationWarning, stacklevel=2)
            return self.start_time.date()
        return None

    @property
    def flight_end_date(self) -> date | None:
        """DEPRECATED: Use end_time instead. Backward compatibility only."""
        if self.end_time:
            warnings.warn("flight_end_date is deprecated. Use end_time instead.", DeprecationWarning, stacklevel=2)
            return self.end_time.date()
        return None


# Adapter-specific response schemas
class AdapterPackageDelivery(BaseModel):
    package_id: str
    impressions: int
    spend: float


class AdapterGetMediaBuyDeliveryResponse(AdCPBaseModel):
    """Response from adapter's get_media_buy_delivery method"""

    media_buy_id: str
    reporting_period: ReportingPeriod
    totals: DeliveryTotals
    by_package: list[AdapterPackageDelivery]
    currency: str


# --- Human-in-the-Loop Task Queue ---


class HumanTask(BaseModel):
    """Task requiring human intervention."""

    task_id: str
    task_type: (
        str  # creative_approval, permission_exception, configuration_required, compliance_review, manual_approval
    )
    principal_id: str
    adapter_name: str | None = None
    status: str = "pending"  # pending, assigned, in_progress, completed, failed, escalated
    priority: str = "medium"  # low, medium, high, urgent

    # Context
    media_buy_id: str | None = None
    creative_id: str | None = None
    operation: str | None = None
    error_detail: str | None = None
    context_data: dict[str, Any] | None = None

    # Assignment
    assigned_to: str | None = None
    assigned_at: datetime | None = None

    # Timing
    created_at: datetime
    updated_at: datetime
    due_by: datetime | None = None
    completed_at: datetime | None = None

    # Resolution
    resolution: str | None = None  # approved, rejected, completed, cannot_complete
    resolution_detail: str | None = None
    resolved_by: str | None = None


class CreateHumanTaskRequest(AdCPBaseModel):
    """Request to create a human task."""

    task_type: str
    priority: str = "medium"
    adapter_name: str | None = None  # Added to match HumanTask schema

    # Context
    media_buy_id: str | None = None
    creative_id: str | None = None
    operation: str | None = None
    error_detail: str | None = None
    context_data: dict[str, Any] | None = None

    # SLA
    due_in_hours: int | None = None  # Hours until due


class CreateHumanTaskResponse(AdCPBaseModel):
    """Response from creating a human task."""

    task_id: str
    status: str
    due_by: datetime | None = None

    def __str__(self) -> str:
        """Return human-readable text for MCP content field."""
        return f"Task {self.task_id} created with status: {self.status}"


class GetPendingTasksRequest(AdCPBaseModel):
    """Request for pending human tasks."""

    principal_id: str | None = None  # Filter by principal
    task_type: str | None = None  # Filter by type
    priority: str | None = None  # Filter by minimum priority
    assigned_to: str | None = None  # Filter by assignee
    include_overdue: bool = True


class GetPendingTasksResponse(AdCPBaseModel):
    """Response with pending tasks."""

    tasks: list[HumanTask]
    total_count: int
    overdue_count: int


class AssignTaskRequest(AdCPBaseModel):
    """Request to assign a task."""

    task_id: str
    assigned_to: str


class CompleteTaskRequest(AdCPBaseModel):
    """Request to complete a task."""

    task_id: str
    resolution: str  # approved, rejected, completed, cannot_complete
    resolution_detail: str | None = None
    resolved_by: str


class VerifyTaskRequest(AdCPBaseModel):
    """Request to verify if a task was completed correctly."""

    task_id: str
    expected_outcome: dict[str, Any] | None = None  # What the task should have accomplished


class VerifyTaskResponse(AdCPBaseModel):
    """Response from task verification."""

    task_id: str
    verified: bool
    actual_state: dict[str, Any]
    expected_state: dict[str, Any] | None = None
    discrepancies: list[str] = []


class MarkTaskCompleteRequest(AdCPBaseModel):
    """Admin request to mark a task as complete with verification."""

    task_id: str
    override_verification: bool = False  # Force complete even if verification fails
    completed_by: str


# Targeting capabilities
class GetTargetingCapabilitiesRequest(AdCPBaseModel):
    """Query targeting capabilities for channels."""

    channels: list[str] | None = None  # If None, return all channels
    include_aee_dimensions: bool = True


class TargetingDimensionInfo(BaseModel):
    """Information about a single targeting dimension."""

    key: str
    display_name: str
    description: str
    data_type: str
    required: bool = False
    values: list[str] | None = None


class ChannelTargetingCapabilities(BaseModel):
    """Targeting capabilities for a specific channel."""

    channel: str
    overlay_dimensions: list[TargetingDimensionInfo]
    aee_dimensions: list[TargetingDimensionInfo] | None = None


class GetTargetingCapabilitiesResponse(AdCPBaseModel):
    """Response with targeting capabilities."""

    capabilities: list[ChannelTargetingCapabilities]


class CheckAXERequirementsRequest(AdCPBaseModel):
    """Check if required AXE dimensions are supported."""

    channel: str
    required_dimensions: list[str]


class CheckAXERequirementsResponse(AdCPBaseModel):
    """Response for AXE requirements check."""

    supported: bool
    missing_dimensions: list[str]
    available_dimensions: list[str]


# Creative macro is now a simple string passed via AXE axe_signals


# --- Signal Discovery ---
class SignalDeployment(BaseModel):
    """Platform deployment information for a signal - AdCP spec compliant."""

    platform: str = Field(..., description="Platform name")
    account: str | None = Field(None, description="Specific account if applicable")
    is_live: bool = Field(..., description="Whether signal is currently active")
    scope: Literal["platform-wide", "account-specific"] = Field(..., description="Deployment scope")
    decisioning_platform_segment_id: str | None = Field(None, description="Platform-specific segment ID")
    estimated_activation_duration_minutes: float | None = Field(None, description="Time to activate if not live", gt=-1)


class SignalPricing(BaseModel):
    """Pricing information for a signal - AdCP spec compliant."""

    cpm: float = Field(..., description="Cost per thousand impressions", gt=-1)
    currency: str = Field(..., description="Currency code", pattern="^[A-Z]{3}$")


class Signal(BaseModel):
    """Represents an available signal - AdCP spec compliant."""

    # Core AdCP fields (required)
    signal_agent_segment_id: str = Field(..., description="Unique identifier for the signal")
    name: str = Field(..., description="Human-readable signal name")
    description: str = Field(..., description="Detailed signal description")
    signal_type: Literal["marketplace", "custom", "owned"] = Field(..., description="Type of signal")
    data_provider: str = Field(..., description="Name of the data provider")
    coverage_percentage: float = Field(..., description="Percentage of audience coverage", gt=-1, le=100)
    deployments: list[SignalDeployment] = Field(..., description="Array of platform deployments")
    pricing: SignalPricing = Field(..., description="Pricing information")

    # Internal fields (not in AdCP spec)
    tenant_id: str | None = Field(None, description="Internal: Tenant ID for multi-tenancy")
    created_at: datetime | None = Field(None, description="Internal: Creation timestamp")
    updated_at: datetime | None = Field(None, description="Internal: Last update timestamp")
    metadata: dict[str, Any] | None = Field(None, description="Internal: Additional metadata")

    # Backward compatibility properties (deprecated)
    @property
    def signal_id(self) -> str:
        """Backward compatibility for signal_id.

        DEPRECATED: Use signal_agent_segment_id instead.
        This property will be removed in a future version.
        """
        warnings.warn(
            "signal_id is deprecated and will be removed in a future version. Use signal_agent_segment_id instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.signal_agent_segment_id

    @property
    def type(self) -> str:
        """Backward compatibility for type.

        DEPRECATED: Use signal_type instead.
        This property will be removed in a future version.
        """
        warnings.warn(
            "type is deprecated and will be removed in a future version. Use signal_type instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.signal_type

    def model_dump(self, **kwargs):
        """Override to provide AdCP-compliant responses while preserving internal fields."""
        # Default to excluding internal fields for AdCP compliance
        exclude = kwargs.get("exclude", set())
        if isinstance(exclude, set):
            # Add internal fields to exclude by default
            exclude.update({"tenant_id", "created_at", "updated_at", "metadata"})
            kwargs["exclude"] = exclude

        return super().model_dump(**kwargs)

    def model_dump_internal(self, **kwargs):
        """Dump including internal fields for database storage and internal processing."""
        # Don't exclude internal fields
        kwargs.pop("exclude", None)  # Remove any exclude parameter
        return super().model_dump(**kwargs)


# AdCP-compliant supporting models for get-signals-request
class SignalDeliverTo(BaseModel):
    """Delivery requirements per AdCP get-signals-request schema."""

    platforms: str | list[str] = Field(
        "all", description="Target platforms: 'all' or array of platform names (defaults to 'all')"
    )
    accounts: list[dict[str, str]] | None = Field(None, description="Specific platform-account combinations")
    countries: list[str] = Field(
        default_factory=lambda: ["US"],
        description="Countries where signals will be used (ISO codes, defaults to ['US'])",
    )

    @model_validator(mode="after")
    def validate_accounts_structure(self):
        """Validate accounts array structure if provided."""
        if self.accounts:
            for account in self.accounts:
                if not isinstance(account, dict) or "platform" not in account or "account" not in account:
                    raise ValueError("Each account must have 'platform' and 'account' fields")
        return self


class SignalFilters(BaseModel):
    """Signal filters per AdCP get-signals-request schema."""

    catalog_types: list[Literal["marketplace", "custom", "owned"]] | None = None
    data_providers: list[str] | None = None
    max_cpm: float | None = Field(None, ge=0, description="Maximum CPM price filter")
    min_coverage_percentage: float | None = Field(None, ge=0, le=100, description="Minimum coverage requirement")


class GetSignalsRequest(AdCPBaseModel):
    """AdCP-compliant request to discover available signals per get-signals-request schema.

    Fully compliant with AdCP specification:
    - Required: signal_spec (natural language description)
    - Required: deliver_to (delivery requirements)
    - Optional: filters (refinement criteria)
    - Optional: max_results (result limit)
    """

    signal_spec: str = Field("", description="Natural language description of the desired signals")
    deliver_to: SignalDeliverTo | None = Field(None, description="Where the signals need to be delivered")
    filters: SignalFilters | None = Field(None, description="Filters to refine results")
    max_results: int | None = Field(None, ge=1, description="Maximum number of results to return")

    # Backward compatibility properties (deprecated)
    @property
    def query(self) -> str:
        """DEPRECATED: Use signal_spec instead. Backward compatibility only."""
        warnings.warn("query is deprecated. Use signal_spec instead.", DeprecationWarning, stacklevel=2)
        return self.signal_spec

    @property
    def limit(self) -> int | None:
        """DEPRECATED: Use max_results instead. Backward compatibility only."""
        if self.max_results:
            warnings.warn("limit is deprecated. Use max_results instead.", DeprecationWarning, stacklevel=2)
        return self.max_results


class GetSignalsResponse(AdCPBaseModel):
    """Response containing available signals (AdCP v2.4 spec compliant).

    Per AdCP PR #113, this response contains ONLY domain data.
    Protocol fields (status, task_id, message, context_id) are added by the
    protocol layer (MCP, A2A, REST) via ProtocolEnvelope wrapper.
    """

    signals: list[Signal] = Field(..., description="Array of available signals")

    def __str__(self) -> str:
        """Return human-readable summary message for protocol envelope."""
        count = len(self.signals)
        if count == 0:
            return "No signals found matching your criteria."
        elif count == 1:
            return "Found 1 signal."
        return f"Found {count} signals."


# --- Signal Activation ---
class ActivateSignalRequest(AdCPBaseModel):
    """Request to activate a signal for use in campaigns."""

    signal_id: str = Field(..., description="Signal ID to activate")
    campaign_id: str | None = Field(None, description="Optional campaign ID to activate signal for")
    media_buy_id: str | None = Field(None, description="Optional media buy ID to activate signal for")


class ActivateSignalResponse(AdCPBaseModel):
    """Response from signal activation (AdCP v2.4 spec compliant).

    Per AdCP PR #113, this response contains ONLY domain data.
    Protocol fields (status, task_id, message, context_id) are added by the
    protocol layer (MCP, A2A, REST) via ProtocolEnvelope wrapper.
    """

    signal_id: str = Field(..., description="Activated signal ID")
    activation_details: dict[str, Any] | None = Field(None, description="Platform-specific activation details")
    errors: list[Error] | None = Field(None, description="Optional error reporting")

    def __str__(self) -> str:
        """Return human-readable summary message for protocol envelope."""
        if self.errors:
            return f"Signal {self.signal_id} activation encountered {len(self.errors)} error(s)."
        return f"Signal {self.signal_id} activated successfully."


# --- Simulation and Time Progression Control ---
class SimulationControlRequest(AdCPBaseModel):
    """Control simulation time progression and events."""

    strategy_id: str = Field(..., description="Strategy ID to control (must be simulation strategy with 'sim_' prefix)")
    action: Literal["jump_to", "reset", "set_scenario"] = Field(..., description="Action to perform on the simulation")
    parameters: dict[str, Any] = Field(default_factory=dict, description="Action-specific parameters")


class SimulationControlResponse(AdCPBaseModel):
    """Response from simulation control operations."""

    status: Literal["ok", "error"] = "ok"
    message: str | None = None
    current_state: dict[str, Any] | None = None
    simulation_time: datetime | None = None

    def __str__(self) -> str:
        """Return human-readable text for MCP content field."""
        if self.message:
            return self.message
        return f"Simulation control: {self.status}"


# --- Authorized Properties Constants ---

# Valid property types per AdCP specification
PROPERTY_TYPES = ["website", "mobile_app", "ctv_app", "dooh", "podcast", "radio", "streaming_audio"]

# Valid verification statuses
VERIFICATION_STATUSES = ["pending", "verified", "failed"]

# Valid identifier types by property type (AdCP compliant mappings)
IDENTIFIER_TYPES_BY_PROPERTY_TYPE = {
    "website": ["domain", "subdomain"],
    "mobile_app": ["bundle_id", "store_id"],
    "ctv_app": ["roku_store_id", "amazon_store_id", "samsung_store_id", "lg_store_id"],
    "dooh": ["venue_id", "network_id"],
    "podcast": ["podcast_guid", "rss_feed_url"],
    "radio": ["station_call_sign", "stream_url"],
    "streaming_audio": ["platform_id", "stream_id"],
}

# Property form field requirements
PROPERTY_REQUIRED_FIELDS = ["property_type", "name", "identifiers", "publisher_domain"]

# Property form validation rules
PROPERTY_VALIDATION_RULES = {
    "name": {"min_length": 1, "max_length": 255},
    "publisher_domain": {"min_length": 1, "max_length": 255},
    "property_type": {"allowed_values": PROPERTY_TYPES},
    "verification_status": {"allowed_values": VERIFICATION_STATUSES},
    "tag_id": {"pattern": r"^[a-z0-9_]+$", "max_length": 50},
}

# Supported file types for bulk upload
SUPPORTED_UPLOAD_FILE_TYPES = [".json", ".csv"]

# Property form error messages
PROPERTY_ERROR_MESSAGES = {
    "missing_required_field": "Property type, name, and publisher domain are required",
    "invalid_property_type": "Invalid property type: {property_type}. Must be one of: {valid_types}",
    "invalid_file_type": "Only JSON and CSV files are supported",
    "no_file_selected": "No file selected",
    "at_least_one_identifier": "At least one identifier is required",
    "identifier_incomplete": "Identifier {index}: Both type and value are required",
    "invalid_json": "Invalid JSON format: {error}",
    "invalid_tag_id": "Tag ID must contain only letters, numbers, and underscores",
    "tag_already_exists": "Tag '{tag_id}' already exists",
    "all_fields_required": "All fields are required",
    "property_not_found": "Property not found",
    "tenant_not_found": "Tenant not found",
}


# --- Authorized Properties (AdCP Spec) ---
class PropertyIdentifier(BaseModel):
    """Identifier for an advertising property."""

    type: str = Field(
        ..., description="Type of identifier (e.g., 'domain', 'bundle_id', 'roku_store_id', 'podcast_guid')"
    )
    value: str = Field(
        ...,
        description="The identifier value. For domain type: 'example.com' matches www.example.com and m.example.com only; 'subdomain.example.com' matches that specific subdomain; '*.example.com' matches all subdomains",
    )


class Property(BaseModel):
    """An advertising property that can be validated via adagents.json (AdCP spec)."""

    property_type: Literal["website", "mobile_app", "ctv_app", "dooh", "podcast", "radio", "streaming_audio"] = Field(
        ..., description="Type of advertising property"
    )
    name: str = Field(..., description="Human-readable property name")
    identifiers: list[PropertyIdentifier] = Field(
        ..., min_length=1, description="Array of identifiers for this property"
    )
    tags: list[str] | None = Field(
        None, description="Tags for categorization and grouping (e.g., network membership, content categories)"
    )
    publisher_domain: str = Field(
        ..., description="Domain where adagents.json should be checked for authorization validation"
    )

    def model_dump(self, **kwargs) -> dict[str, Any]:
        """Return AdCP-compliant property representation."""
        data = super().model_dump(**kwargs)
        # Ensure tags is always present per AdCP schema
        if data.get("tags") is None:
            data["tags"] = []
        return data


class PropertyTagMetadata(BaseModel):
    """Metadata for a property tag."""

    name: str = Field(..., description="Human-readable name for this tag")
    description: str = Field(..., description="Description of what this tag represents")


class ListAuthorizedPropertiesRequest(AdCPBaseModel):
    """Request parameters for discovering all properties this agent is authorized to represent (AdCP spec)."""

    adcp_version: str = Field(
        default="1.0.0", pattern=r"^\d+\.\d+\.\d+$", description="AdCP schema version for this request"
    )
    tags: list[str] | None = Field(None, description="Filter properties by specific tags (optional)")

    @model_validator(mode="before")
    @classmethod
    def normalize_tags(cls, data):
        """Ensure tags are lowercase with underscores only."""
        if isinstance(data, dict) and "tags" in data and data["tags"]:
            data["tags"] = [tag.lower().replace("-", "_") for tag in data["tags"]]
        return data


class ListAuthorizedPropertiesResponse(AdCPBaseModel):
    """Response payload for list_authorized_properties task (AdCP v2.4 spec compliant).

    Per official AdCP v2.4 spec at https://adcontextprotocol.org/schemas/v1/media-buy/list-authorized-properties-response.json,
    this response lists publisher domains. Buyers fetch property definitions from each publisher's adagents.json file.

    Protocol fields (status, task_id, message, context_id) are added by the
    protocol layer (MCP, A2A, REST) via ProtocolEnvelope wrapper.
    """

    publisher_domains: list[str] = Field(..., description="Publisher domains this agent is authorized to represent")
    tags: dict[str, PropertyTagMetadata] = Field(
        default_factory=dict, description="Metadata for each tag referenced by properties"
    )
    primary_channels: list[str] | None = Field(
        None, description="Primary advertising channels in this portfolio (helps buyers filter relevance)"
    )
    primary_countries: list[str] | None = Field(
        None, description="Primary countries (ISO 3166-1 alpha-2 codes) where properties are concentrated"
    )
    portfolio_description: str | None = Field(
        None, description="Markdown-formatted description of the property portfolio", max_length=5000
    )
    advertising_policies: str | None = Field(
        None,
        description=(
            "Publisher's advertising content policies, restrictions, and guidelines in natural language. "
            "May include prohibited categories, blocked advertisers, restricted tactics, brand safety requirements, "
            "or links to full policy documentation."
        ),
        min_length=1,
        max_length=10000,
    )
    last_updated: str | None = Field(
        None,
        description="ISO 8601 timestamp of when the agent's publisher authorization list was last updated. Buyers can use this to determine if their cached publisher adagents.json files might be stale.",
    )
    errors: list[dict[str, Any]] | None = Field(
        None, description="Task-specific errors and warnings (e.g., property availability issues)"
    )

    def __str__(self) -> str:
        """Return human-readable message for protocol layer.

        Used by both MCP (for display) and A2A (for task messages).
        Provides conversational text without adding non-spec fields to the schema.
        """
        count = len(self.publisher_domains)
        if count == 0:
            return "No authorized publisher domains found."
        elif count == 1:
            return "Found 1 authorized publisher domain."
        else:
            return f"Found {count} authorized publisher domains."

    def model_dump(self, **kwargs) -> dict[str, Any]:
        """Return AdCP-compliant response."""
        data = super().model_dump(**kwargs)
        # Ensure errors is always present per AdCP schema
        if data.get("errors") is None:
            data["errors"] = []
        return data
