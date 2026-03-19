import warnings
from datetime import date, datetime

# --- V2.3 Pydantic Models (Bearer Auth, Restored & Complete) ---
# --- MCP Status System (AdCP PR #77) ---
from enum import Enum
from typing import TYPE_CHECKING, Any, Literal, TypeAlias

if TYPE_CHECKING:
    from src.core.schemas.creative import Creative, CreativeApproval

from adcp import Error
from adcp.types import CreateMediaBuyRequest as LibraryCreateMediaBuyRequest

# Import main request/response types from stable API
from adcp.types import Format as LibraryFormat
from adcp.types import (
    FormatCategory as FormatTypeEnum,
)

# Import types from stable API (per adcp 2.7.0+)
from adcp.types import FormatId as LibraryFormatId
from adcp.types import PackageRequest as LibraryPackageRequest
from adcp.types import (
    PriceGuidance,  # Replaces local PriceGuidance class
    PricingModel,  # Replaces local PricingModel enum (lowercase members: .cpm, .cpc, etc.)
)
from adcp.types.aliases import (
    CreateMediaBuyErrorResponse as AdCPCreateMediaBuyError,
)
from adcp.types.aliases import (
    CreateMediaBuySuccessResponse as AdCPCreateMediaBuySuccess,
)
from adcp.types.aliases import Package as AdCPPackage
from adcp.types.aliases import (
    UpdateMediaBuyErrorResponse as AdCPUpdateMediaBuyError,
)
from adcp.types.aliases import (
    UpdateMediaBuySuccessResponse as AdCPUpdateMediaBuySuccess,
)
from adcp.types.base import AdCPBaseModel as LibraryAdCPBaseModel
from adcp.types.generated_poc.core.context import ContextObject
from adcp.types.generated_poc.enums.media_buy_status import MediaBuyStatus

# Import types from stable API (per adcp 2.9.0+ - all types now in stable)
# Note: AffectedPackage was removed in 2.9.0, use Package instead
from adcp.types.generated_poc.media_buy.package_update import PackageUpdate1 as LibraryPackageUpdate1
from adcp.types.generated_poc.media_buy.update_media_buy_request import (
    UpdateMediaBuyRequest1 as LibraryUpdateMediaBuyRequest1,
)

from src.core.config import get_pydantic_extra_mode

# For backward compatibility, alias AdCPPackage as LibraryPackage (TypeAlias for mypy)
LibraryPackage: TypeAlias = AdCPPackage
# Simple types that match library exactly
# V3: Structured geo targeting types
from adcp.types import ActivateSignalRequest as LibraryActivateSignalRequest
from adcp.types import BrandManifest as LibraryBrandManifest
from adcp.types import (
    CpcPricingOption,
    CpcvPricingOption,
    CpmPricingOption,  # V3: consolidated from CpmAuctionPricingOption/CpmFixedRatePricingOption
    CppPricingOption,
    CpvPricingOption,
    FlatRatePricingOption,
    GeoCountry,
    GeoMetro,
    GeoPostalArea,
    GeoRegion,
    TargetingOverlay,
    VcpmPricingOption,  # V3: consolidated from VcpmAuctionPricingOption/VcpmFixedRatePricingOption
)

# AdCP creative types for schema definitions
from adcp.types import CreativePolicy as LibraryCreativePolicy
from adcp.types import FrequencyCap as LibraryFrequencyCap
from adcp.types import GetSignalsResponse as LibraryGetSignalsResponse
from adcp.types import Measurement as LibraryMeasurement
from adcp.types import PlatformDeployment as LibraryPlatformDeployment
from adcp.types import Pricing as LibraryPricing
from adcp.types import Property as LibraryProperty
from adcp.types import Signal as LibrarySignal
from adcp.types import SignalFilters as LibrarySignalFilters
from pydantic import (
    AnyUrl,
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    model_serializer,
    model_validator,
)

# Type alias for the union of all AdCP pricing option types (V3 consolidated)
AdCPPricingOption = (
    CpmPricingOption
    | VcpmPricingOption
    | CpcPricingOption
    | CpcvPricingOption
    | CpvPricingOption
    | CppPricingOption
    | FlatRatePricingOption
)


# Helper function for creating AnyUrl instances (eliminates mypy warnings)
def url(value: str) -> AnyUrl:
    """Convert string to AnyUrl for type-safe URL construction.

    This helper eliminates mypy warnings when passing strings to AnyUrl fields.
    Pydantic's AnyUrl accepts strings at runtime and validates/converts them automatically.

    Usage:
        FormatId(agent_url=url("https://example.com"), id="test")

    Args:
        value: URL string to convert

    Returns:
        AnyUrl instance (auto-validated by Pydantic)
    """
    return AnyUrl(value)  # Pydantic handles string -> AnyUrl conversion


class NestedModelSerializerMixin:
    """Mixin that ensures nested Pydantic models use their custom model_dump().

    Pydantic's default serialization doesn't automatically call custom model_dump() methods
    on nested models. This mixin introspects all fields and explicitly calls model_dump()
    on any nested BaseModel instances, ensuring internal fields are properly excluded.

    This approach is resilient to schema changes - no hardcoded field names.

    Usage:
        class MyResponse(NestedModelSerializerMixin, SalesAgentBaseModel):
            nested_field: NestedModel
            # Automatically serializes nested_field correctly
    """

    @model_serializer(mode="wrap")
    def _serialize_nested_models(self, serializer, info):
        """Automatically serialize nested Pydantic models using their custom model_dump()."""
        # Get default serialization
        data = serializer(self)

        # Introspect all fields and re-serialize nested Pydantic models
        for field_name, _ in self.__class__.model_fields.items():
            if field_name not in data:
                continue

            field_value = getattr(self, field_name, None)
            if field_value is None:
                continue

            # Handle list of Pydantic models
            if isinstance(field_value, list) and field_value:
                if isinstance(field_value[0], BaseModel):
                    data[field_name] = [item.model_dump(mode=info.mode) for item in field_value]
            # Handle single Pydantic model
            elif isinstance(field_value, BaseModel):
                data[field_name] = field_value.model_dump(mode=info.mode)

        return data


class SalesAgentBaseModel(LibraryAdCPBaseModel):
    """Base model for all internal salesagent schemas.

    Extends the adcp library's AdCPBaseModel to add environment-aware validation:
    - Production: extra="ignore" (forward compatible, accepts future schema fields)
    - Non-production: extra="forbid" (strict, catches bugs early)

    Inherits from library base:
    - model_dump(exclude_none=True) — AdCP spec compliance
    - model_dump_json(exclude_none=True) — AdCP spec compliance
    - model_summary() — human-readable protocol responses

    The validation mode is set at class definition time based on the ENVIRONMENT variable.
    """

    model_config = ConfigDict(extra=get_pydantic_extra_mode())


class CreateMediaBuySuccess(AdCPCreateMediaBuySuccess):
    """Successful create_media_buy response extending adcp v1.2.1 type.

    Extends the official adcp CreateMediaBuySuccess type with internal workflow tracking.
    Per AdCP PR #113, this response contains ONLY domain data.
    Protocol fields (status, task_id, message, context_id) are added by the
    protocol layer (MCP, A2A, REST) via ProtocolEnvelope wrapper.
    """

    # Internal fields (excluded from AdCP responses)
    workflow_step_id: str | None = None

    @model_serializer(mode="wrap")
    def _serialize_model(self, serializer, info):
        """Serialize model, excluding internal fields by default."""
        # Get base serialization
        data = serializer(self)

        # Exclude internal fields from protocol responses
        # (unless explicitly requested via model_dump_internal)
        if not info.context or not info.context.get("include_internal"):
            data.pop("workflow_step_id", None)

        # Auto-handle nested Pydantic models
        # For packages array, exclude internal platform_line_item_id from AdCP responses
        for field_name in self.__class__.model_fields:
            field_value = getattr(self, field_name, None)
            if field_value is None:
                continue

            if isinstance(field_value, list) and field_value:
                if isinstance(field_value[0], BaseModel):
                    # Exclude internal fields from Package objects in AdCP responses
                    if field_name == "packages":
                        data[field_name] = [
                            item.model_dump(exclude={"platform_line_item_id"}, mode=info.mode) for item in field_value
                        ]
                    else:
                        data[field_name] = [item.model_dump(mode=info.mode) for item in field_value]
            elif isinstance(field_value, BaseModel):
                data[field_name] = field_value.model_dump(mode=info.mode)

        return data

    def model_dump_internal(self, **kwargs):
        """Dump including internal fields for database storage and internal processing."""
        return self.model_dump(context={"include_internal": True}, **kwargs)

    def __str__(self) -> str:
        """Return human-readable summary message for protocol envelope."""
        if self.media_buy_id:
            return f"Media buy {self.media_buy_id} created successfully."
        else:
            return f"Media buy {self.buyer_ref} created."


class CreateMediaBuyError(AdCPCreateMediaBuyError):
    """Failed create_media_buy response extending adcp v1.2.1 type.

    Extends the official adcp CreateMediaBuyError type.
    Per AdCP PR #113, this response contains ONLY domain data.
    """

    def __str__(self) -> str:
        """Return human-readable summary message for protocol envelope."""
        if self.errors:
            return f"Media buy creation encountered {len(self.errors)} error(s)."
        else:
            return "Media buy creation failed."


# Union type for create_media_buy operation
CreateMediaBuyResponse = CreateMediaBuySuccess | CreateMediaBuyError


class CreateMediaBuyResult(SalesAgentBaseModel):
    """Wrapper combining create_media_buy domain response with protocol status.

    Serializes to {"status": "...", ...response_fields}, allowing callers to
    pass the model directly to ToolResult without calling model_dump().

    Supports tuple unpacking (response, status) for backward compatibility
    with existing callers and tests.
    """

    status: str
    response: CreateMediaBuySuccess | CreateMediaBuyError

    @model_serializer(mode="wrap")
    def _serialize(self, serializer, info):
        result = self.response.model_dump(mode=info.mode)
        result["status"] = self.status
        return result

    def __iter__(self):
        """Support tuple unpacking: response, status = result."""
        return iter((self.response, self.status))

    def __str__(self) -> str:
        return str(self.response)


# --- Update Media Buy Response Components ---


class AffectedPackage(LibraryPackage):
    """Affected package in UpdateMediaBuySuccess response.

    Extends adcp library Package with internal tracking fields.
    Note: In AdCP 2.12.0+, affected_packages uses the full Package type.

    Library Package required fields (adcp 2.12.0):
    - package_id: Publisher's package identifier
    - paused: Boolean indicating whether package is paused (replaces old status enum)
    """

    # Internal fields for tracking what changed (not in AdCP spec)
    changes_applied: dict[str, Any] | None = Field(
        None,
        description="Internal: Detailed changes applied to package (creative_ids added/removed, etc.)",
        exclude=True,
    )
    buyer_package_ref: str | None = Field(
        None, description="Internal: Buyer's package reference (legacy compatibility)", exclude=True
    )


class UpdateMediaBuySuccess(AdCPUpdateMediaBuySuccess):
    """Successful update_media_buy response extending adcp v1.2.1 type.

    Extends the official adcp UpdateMediaBuySuccess type with internal workflow tracking.
    Per AdCP PR #113, this response contains ONLY domain data.
    Protocol fields (status, task_id, message, context_id) are added by the
    protocol layer (MCP, A2A, REST) via ProtocolEnvelope wrapper.
    """

    # Override affected_packages to use our extended AffectedPackage type
    # This allows us to include internal tracking fields (changes_applied, buyer_package_ref)
    # while still being AdCP-compliant (those fields are excluded via exclude=True)
    # Pydantic allows subclass override at runtime but mypy doesn't recognize this
    affected_packages: list[AffectedPackage] | None = None  # type: ignore[assignment]

    # Internal fields (excluded from AdCP responses)
    workflow_step_id: str | None = None

    @model_serializer(mode="wrap")
    def _serialize_model(self, serializer, info):
        """Serialize model, excluding internal fields by default."""
        # Get base serialization
        data = serializer(self)

        # Exclude workflow_step_id from protocol responses
        # (unless explicitly requested via model_dump_internal)
        if not info.context or not info.context.get("include_internal"):
            data.pop("workflow_step_id", None)

        # Explicitly serialize affected_packages to ensure AffectedPackage.model_dump() is called
        # This ensures internal fields (changes_applied, buyer_package_ref) are excluded via exclude=True
        if "affected_packages" in data and self.affected_packages:
            data["affected_packages"] = [pkg.model_dump(mode=info.mode) for pkg in self.affected_packages]

        # Auto-handle other nested Pydantic models
        for field_name in self.__class__.model_fields:
            if field_name == "affected_packages":
                continue  # Already handled above

            field_value = getattr(self, field_name, None)
            if field_value is None:
                continue

            if isinstance(field_value, list) and field_value:
                if isinstance(field_value[0], BaseModel):
                    data[field_name] = [item.model_dump(mode=info.mode) for item in field_value]
            elif isinstance(field_value, BaseModel):
                data[field_name] = field_value.model_dump(mode=info.mode)

        return data

    def model_dump_internal(self, **kwargs):
        """Dump including internal fields for database storage and internal processing."""
        return self.model_dump(context={"include_internal": True}, **kwargs)

    def __str__(self) -> str:
        """Return human-readable summary message for protocol envelope."""
        if self.affected_packages:
            return f"Media buy {self.media_buy_id} updated: {len(self.affected_packages)} package(s) affected."
        else:
            return f"Media buy {self.media_buy_id} updated successfully."


class UpdateMediaBuyError(AdCPUpdateMediaBuyError):
    """Failed update_media_buy response extending adcp v1.2.1 type.

    Extends the official adcp UpdateMediaBuyError type.
    Per AdCP PR #113, this response contains ONLY domain data.
    """

    def __str__(self) -> str:
        """Return human-readable summary message for protocol envelope."""
        if self.errors:
            return f"Media buy update encountered {len(self.errors)} error(s)."
        else:
            return "Media buy update failed."


# Union type for update_media_buy operation
UpdateMediaBuyResponse = UpdateMediaBuySuccess | UpdateMediaBuyError


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


# PricingModel is now imported from adcp.types (adcp library)
# Library uses lowercase member names: .cpm, .vcpm, .cpc, .cpcv, .cpv, .cpp, .flat_rate

# PriceGuidance is now imported from adcp.types.stable (adcp 2.7.0+)
# The library version has the same fields and behavior as our previous local class


class PricingParameters(SalesAgentBaseModel):
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


class PricingOption(SalesAgentBaseModel):
    """A pricing model option offered by a publisher for a product per AdCP spec.

    V3 Migration: Consolidated pricing fields:
    - rate → fixed_price (for fixed-rate pricing)
    - floor added at top level as floor_price (was in price_guidance)
    - is_fixed removed (determined by presence of fixed_price vs floor_price)
    - price_guidance now only contains percentiles (p25, p50, p75, p90)
    """

    pricing_option_id: str = Field(
        ..., description="Unique identifier for this pricing option within the product (e.g., 'cpm_usd_guaranteed')"
    )
    pricing_model: PricingModel = Field(..., description="The pricing model for this option")
    currency: str = Field(..., pattern="^[A-Z]{3}$", description="ISO 4217 currency code (e.g., USD, EUR, GBP)")

    # V3: Consolidated pricing fields - use fixed_price OR floor_price, not both
    fixed_price: float | None = Field(None, ge=0, description="Fixed rate for this pricing model (V3: replaces rate)")
    floor_price: float | None = Field(
        None, ge=0, description="Floor price for auction-based pricing (V3: was price_guidance.floor)"
    )

    # V3: price_guidance now only contains percentiles, no floor
    price_guidance: PriceGuidance | None = Field(
        None, description="Pricing guidance with percentiles (p25, p50, p75, p90) for auction-based pricing"
    )
    min_spend_per_package: float | None = Field(
        None, ge=0, description="Minimum spend requirement per package using this pricing option"
    )

    # Internal fields - not in AdCP spec, used for adapter capability tracking
    is_fixed: bool | None = Field(
        None,
        description="Internal: Whether this is a fixed rate (true) or auction-based (false). Computed from fixed_price presence.",
    )
    supported: bool | None = Field(
        None, description="Whether this pricing model is supported by the current adapter (populated at discovery time)"
    )
    unsupported_reason: str | None = Field(
        None, description="Reason why this pricing model is not supported (if supported=false)"
    )

    @model_validator(mode="after")
    def validate_pricing_option(self) -> "PricingOption":
        """Validate pricing option per AdCP V3 spec constraints."""
        # V3: Must have either fixed_price or floor_price (not both, not neither)
        has_fixed = self.fixed_price is not None
        has_floor = self.floor_price is not None

        if has_fixed and has_floor:
            raise ValueError("Cannot have both fixed_price and floor_price - use one or the other")
        if not has_fixed and not has_floor:
            raise ValueError("Must have either fixed_price (for fixed-rate) or floor_price (for auction)")

        # Auto-compute is_fixed for internal use
        object.__setattr__(self, "is_fixed", has_fixed)
        return self

    def model_dump(self, **kwargs):
        """Override to exclude internal fields for AdCP V3 compliance.

        V3 uses separate schemas (cpm-pricing-option, vcpm-pricing-option, etc.)
        determined by which pricing fields are present:
        - fixed_price present = fixed-rate pricing
        - floor_price present = auction pricing with floor

        Excludes internal fields (is_fixed, supported, unsupported_reason) from
        external responses. Also excludes None values to match AdCP spec where
        optional fields should be omitted rather than set to null.
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


class AssetRequirement(SalesAgentBaseModel):
    """Asset requirement specification per AdCP spec."""

    asset_id: str = Field(..., description="Asset identifier used as key in creative manifest assets object")
    asset_type: str = Field(..., description="Type of asset required")
    asset_role: str | None = Field(None, description="Optional descriptive label (not used for referencing)")
    required: bool = Field(True, description="Whether this asset is required")
    quantity: int = Field(default=1, ge=1, description="Number of assets of this type required")
    requirements: dict[str, Any] | None = Field(None, description="Specific requirements for this asset type")


class FormatReference(SalesAgentBaseModel):
    """Reference to a format from a specific creative agent.

    DEPRECATED: Use FormatId instead. This class is maintained for backward compatibility.
    FormatReference serializes as FormatId (with 'id' field) but accepts 'format_id' for legacy code.

    Used in Product.format_ids to store full format references with agent URL.
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


class Format(LibraryFormat):
    """Creative format definition per AdCP spec.

    Extends the adcp library's Format class. The format_id.agent_url field identifies
    the authoritative creative agent that provides this format (e.g., the reference
    creative agent at https://creative.adcontextprotocol.org).

    Note: All spec-defined fields are inherited from adcp.types.stable.Format.
    We only add internal fields here marked with exclude=True.
    """

    # Internal fields for backward compatibility and convenience
    # These are NOT part of the AdCP spec and are excluded from serialization
    platform_config: dict[str, Any] | None = Field(
        None,
        exclude=True,
        description="Internal: Platform-specific configuration (e.g., gam, kevel) for creative mapping",
    )
    category: Literal["standard", "custom", "generative"] | None = Field(
        None, exclude=True, description="Internal: Format category (not in AdCP spec)"
    )
    is_standard: bool | None = Field(
        None, exclude=True, description="Internal: Whether this follows IAB specifications (not in AdCP spec)"
    )
    requirements: dict[str, Any] | None = Field(
        None,
        exclude=True,
        description="Internal: Legacy technical specifications (not in AdCP spec, use renders instead)",
    )
    iab_specification: str | None = Field(
        None, exclude=True, description="Internal: Name of IAB specification (not in AdCP spec)"
    )
    accepts_3p_tags: bool | None = Field(
        None, exclude=True, description="Internal: Whether format accepts third-party tags (not in AdCP spec)"
    )

    @property
    def agent_url(self) -> str | None:
        """Convenience property to access agent_url from format_id.

        Returns the agent_url from format_id.agent_url per AdCP spec.
        This property exists for backward compatibility with code that expects format.agent_url.

        Returns:
            Agent URL string, or None if not available
        """
        return str(self.format_id.agent_url) if self.format_id.agent_url else None

    def get_primary_dimensions(self) -> tuple[int, int] | None:
        """Extract primary dimensions from renders array or format_id parameters.

        Checks in order:
        1. Parameterized format_id (AdCP 2.5) - width/height on FormatId
        2. Renders array - first render's dimensions
        3. Requirements field (legacy, internal)

        Returns:
            Tuple of (width, height) in pixels, or None if not available.
        """
        # Try format_id parameters first (AdCP 2.5 parameterized formats)
        # Access width/height directly — works with both library FormatId and our subclass
        if self.format_id.width is not None and self.format_id.height is not None:
            return (self.format_id.width, self.format_id.height)

        # Try renders field (AdCP spec - renders is list of Render objects)
        if self.renders and len(self.renders) > 0:
            primary_render = self.renders[0]  # First render is typically primary
            if primary_render.dimensions:
                render_dims = primary_render.dimensions
                # dimensions is a Dimensions object with width/height attributes
                if render_dims.width is not None and render_dims.height is not None:
                    return (int(render_dims.width), int(render_dims.height))

        # Fallback to requirements field (legacy, internal field)
        if self.requirements:
            width = self.requirements.get("width")
            height = self.requirements.get("height")
            if width is not None and height is not None:
                return (int(width), int(height))

        return None

    def get_form_value(self) -> str:
        """Get the value used in HTML form submissions for this format.

        This method provides a consistent way to construct format identifiers
        for use in form checkboxes and validation. It handles both FormatId
        objects and string format_id values.

        Returns:
            String in format "agent_url|format_id" for use in forms

        Example:
            >>> fmt = Format(format_id=FormatId(agent_url="...", id="display_300x250"), ...)
            >>> fmt.get_form_value()
            'https://creative.adcontextprotocol.org/|display_300x250'
        """
        return f"{self.format_id.agent_url}|{self.format_id.id}"


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
                Format(
                    format_id=FormatId(agent_url=url("https://creative.adcontextprotocol.org"), id=format_id),
                    name=format_id.replace("_", " ").title(),
                    type=FormatTypeEnum.display,  # Default to display type
                )
            )
    return formats


class FrequencyCap(LibraryFrequencyCap):
    """Frequency capping extending AdCP library type with scope.

    Inherits suppress_minutes: float from library.
    Adds scope field for media buy vs package level capping.
    """

    scope: Literal["media_buy", "package"] = Field("media_buy", description="Apply at media buy or package level")


class TargetingCapability(SalesAgentBaseModel):
    """Defines targeting dimension capabilities and restrictions."""

    dimension: str  # e.g., "geo_country", "key_value"
    access: Literal["overlay", "managed_only", "both", "removed"] = "overlay"
    description: str | None = None
    allowed_values: list[str] | None = None  # For restricted value sets
    axe_signal: bool | None = False  # Whether this is an AXE signal dimension


# Mapping from legacy v2 geo fields to v3 structured fields.
# Each tuple: (v2_field_name, v3_field_name, transform_fn_or_None).
# transform_fn receives the truthy list value and returns the v3 value.
# None means passthrough (value used as-is).
def _prefix_us_regions(v: list[str]) -> list[str]:
    """Legacy DB stores bare US state codes; GeoRegion requires ISO 3166-2."""
    return [r if "-" in r else f"US-{r}" for r in v]


_LEGACY_GEO_FIELDS: list[tuple[str, str, Any]] = [
    ("geo_country_any_of", "geo_countries", None),
    ("geo_country_none_of", "geo_countries_exclude", None),
    ("geo_region_any_of", "geo_regions", _prefix_us_regions),
    ("geo_region_none_of", "geo_regions_exclude", _prefix_us_regions),
    ("geo_metro_any_of", "geo_metros", lambda v: [{"system": "nielsen_dma", "values": v}]),
    ("geo_metro_none_of", "geo_metros_exclude", lambda v: [{"system": "nielsen_dma", "values": v}]),
    ("geo_zip_any_of", "geo_postal_areas", lambda v: [{"system": "us_zip", "values": v}]),
    ("geo_zip_none_of", "geo_postal_areas_exclude", lambda v: [{"system": "us_zip", "values": v}]),
]


# Mapping from device_platform (OS-level, AdCP TargetingOverlay) to
# device_type_any_of (form factor, internal targeting).
# Each platform maps to a list of form factors the device typically has.
_PLATFORM_TO_FORM_FACTORS: dict[str, list[str]] = {
    "ios": ["mobile", "tablet"],
    "android": ["mobile", "tablet"],
    "windows": ["desktop"],
    "macos": ["desktop"],
    "linux": ["desktop"],
    "chromeos": ["desktop"],
    "tvos": ["ctv"],
    "tizen": ["ctv"],
    "webos": ["ctv"],
    "fire_os": ["ctv"],
    "roku_os": ["ctv"],
    # "unknown" intentionally omitted — maps to no form factors
}


class Targeting(TargetingOverlay):
    """Targeting extending AdCP TargetingOverlay with internal dimensions.

    Inherits v3 structured geo fields from library:
    - geo_countries, geo_regions, geo_metros, geo_postal_areas
    - frequency_cap, axe_include_segment, axe_exclude_segment

    Adds exclusion extensions, internal dimensions, and a legacy normalizer
    that converts flat DB fields to v3 structured format.
    """

    model_config = ConfigDict(extra=get_pydantic_extra_mode())

    # --- Inherited from TargetingOverlay (7 fields): ---
    # geo_countries: list[GeoCountry] | None
    # geo_regions: list[GeoRegion] | None
    # geo_metros: list[GeoMetro] | None
    # geo_postal_areas: list[GeoPostalArea] | None
    # frequency_cap: FrequencyCap | None  (overridden below)
    # axe_include_segment: str | None
    # axe_exclude_segment: str | None

    # Override frequency_cap to use our extended FrequencyCap with scope
    frequency_cap: FrequencyCap | None = None

    # --- Geo exclusion extensions (not in library) ---
    geo_countries_exclude: list[GeoCountry] | None = None  # type: ignore[assignment]
    geo_regions_exclude: list[GeoRegion] | None = None  # type: ignore[assignment]
    geo_metros_exclude: list[GeoMetro] | None = None  # type: ignore[assignment]
    geo_postal_areas_exclude: list[GeoPostalArea] | None = None  # type: ignore[assignment]

    # --- Internal dimensions (unchanged) ---

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

    # Transient normalizer signal: set by normalize_legacy_geo when city targeting
    # fields are encountered in legacy data. Consumed by adapters (e.g. GAM
    # build_targeting) to raise an explicit error instead of silently ignoring.
    had_city_targeting: bool = Field(default=False, exclude=True)

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_geo(cls, values: Any) -> Any:
        """Convert flat DB geo fields to v3 structured format.

        Handles reconstruction from legacy database JSON where fields were stored as:
        - geo_country_any_of: ["US", "CA"] → geo_countries: [GeoCountry("US"), ...]
        - geo_region_any_of: ["CA", "NY"] → geo_regions: [GeoRegion("US-CA"), ...]
        - geo_metro_any_of: ["501"] → geo_metros: [{system: "nielsen_dma", values: ["501"]}]
        - geo_zip_any_of: ["10001"] → geo_postal_areas: [{system: "us_zip", values: ["10001"]}]
        - *_none_of variants → *_exclude variants
        """
        if not isinstance(values, dict):
            return values

        for v2_key, v3_key, transform in _LEGACY_GEO_FIELDS:
            if v2_key not in values:
                continue
            v = values.pop(v2_key)
            if v and v3_key not in values:
                values[v3_key] = transform(v) if transform else v

        # City targeting removed in v3. Set a transient flag so downstream consumers
        # (e.g. GAM build_targeting) can raise an explicit error instead of silently ignoring.
        # Pop both unconditionally to avoid short-circuit leaving one in the dict.
        city_any = values.pop("geo_city_any_of", None)
        city_none = values.pop("geo_city_none_of", None)
        if city_any or city_none:
            values["had_city_targeting"] = True

        # device_platform (OS-level, from AdCP TargetingOverlay) → device_type_any_of
        # (form factor, consumed by adapters). Only populate if device_type_any_of
        # is not already explicitly set — explicit values take precedence.
        dp = values.get("device_platform")
        if dp and not values.get("device_type_any_of"):
            form_factors: set[str] = set()
            for platform in dp:
                # Handle both enum values and raw strings
                p = platform.value if hasattr(platform, "value") else str(platform)
                form_factors.update(_PLATFORM_TO_FORM_FACTORS.get(p, []))
            if form_factors:
                values["device_type_any_of"] = sorted(form_factors)

        return values

    def model_dump(self, **kwargs):
        """Override to provide AdCP-compliant responses while preserving internal fields."""
        kwargs.setdefault("mode", "json")
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
        kwargs.setdefault("mode", "json")
        # Don't exclude internal fields or managed fields
        kwargs.pop("exclude", None)  # Remove any exclude parameter
        return super().model_dump(**kwargs)

    def dict(self, **kwargs):
        """Override dict to always exclude managed fields (for backward compat)."""
        kwargs["exclude"] = kwargs.get("exclude", set())
        if isinstance(kwargs["exclude"], set):
            kwargs["exclude"].add("key_value_pairs")
        return super().dict(**kwargs)


class Budget(SalesAgentBaseModel):
    """Budget object with multi-currency support (AdCP spec compliant)."""

    total: float = Field(..., gt=0, description="Total budget amount (AdCP spec field name)")
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
class Measurement(LibraryMeasurement):
    """Measurement capabilities included with a product per AdCP spec.

    Extends library type - all fields inherited from AdCP spec.
    """

    pass  # All fields inherited from library


class AIReviewPolicy(SalesAgentBaseModel):
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


class CreativePolicy(LibraryCreativePolicy):
    """Extends library CreativePolicy with AI provenance requirements.

    Library provides: co_branding, landing_page, templates_available.
    Local extension adds provenance_required for EU AI Act Article 50 compliance.
    """

    provenance_required: bool | None = Field(
        default=None,
        description="When True, creatives must include AI provenance metadata (EU AI Act Article 50)",
    )


# --- Core Schemas ---


class Principal(SalesAgentBaseModel):
    """Principal object containing authentication and adapter mapping information."""

    principal_id: str
    name: str
    platform_mappings: dict[str, Any]

    def get_adapter_id(self, adapter_name: str) -> str | None:
        """Get the adapter-specific ID for this principal."""
        from src.core.platform_mappings import resolve_adapter_id

        return resolve_adapter_id(self.platform_mappings, adapter_name)


# --- Performance Index ---
class ProductPerformance(SalesAgentBaseModel):
    product_id: str
    performance_index: float  # 1.0 = baseline, 1.2 = 20% better, 0.8 = 20% worse
    confidence_score: float | None = None  # 0.0 to 1.0


class UpdatePerformanceIndexRequest(SalesAgentBaseModel):
    media_buy_id: str
    performance_data: list[ProductPerformance]
    context: ContextObject | None = Field(
        None, description="Application-level context provided by the client (echoed in responses)"
    )


class UpdatePerformanceIndexResponse(SalesAgentBaseModel):
    status: str
    detail: str
    context: ContextObject | None = Field(None, description="Application-level context echoed from the request")

    def __str__(self) -> str:
        """Return human-readable text for MCP content field."""
        return self.detail


# --- Discovery ---
# Note: FormatType is imported from adcp library as FormatTypeEnum


class FormatId(LibraryFormatId):
    """AdCP format identifier - extends library FormatId with convenience methods.

    Note: The inherited agent_url field has type AnyUrl, but Pydantic accepts strings
    at runtime and automatically validates/converts them. This causes mypy warnings
    (str vs AnyUrl) which are safe to ignore - the code works correctly at runtime.

    AdCP 2.5+ supports parameterized format IDs with width/height/duration_ms fields.
    """

    def __str__(self) -> str:
        """Return human-readable format identifier for display in UIs."""
        return self.id

    def __repr__(self) -> str:
        """Return representation for debugging."""
        return f"FormatId(id='{self.id}', agent_url='{self.agent_url}')"

    def get_dimensions(self) -> tuple[int, int] | None:
        """Get dimensions from parameterized FormatId (AdCP 2.5).

        Returns:
            Tuple of (width, height) in pixels, or None if not specified.
        """
        if self.width is not None and self.height is not None:
            return (self.width, self.height)
        return None

    def get_duration_ms(self) -> float | None:
        """Get duration from parameterized FormatId (AdCP 2.5).

        Returns:
            Duration in milliseconds, or None if not specified.
        """
        return self.duration_ms


def _upgrade_legacy_format_ids(values: dict) -> dict:
    """Convert dict format_ids entries to FormatId objects (AdCP v2.4 compliance).

    Shared validator logic used by PackageRequest, ProductFilters, and
    ListCreativeFormatsRequest.  Each class delegates its
    ``@model_validator(mode="before")`` to this function.
    """
    if not isinstance(values, dict):
        return values

    format_ids = values.get("format_ids")
    if format_ids and isinstance(format_ids, list):
        upgraded = []
        for fmt_id in format_ids:
            if isinstance(fmt_id, dict) and "agent_url" in fmt_id and "id" in fmt_id:
                upgraded.append(FormatId(**fmt_id))
            else:
                upgraded.append(fmt_id)
        values["format_ids"] = upgraded

    return values


# --- Brand Manifest Models (AdCP v1.8.0) ---


class LogoAsset(SalesAgentBaseModel):
    """Logo asset with metadata."""

    url: str = Field(..., description="URL to logo asset")
    width: int | None = Field(None, ge=1, description="Logo width in pixels")
    height: int | None = Field(None, ge=1, description="Logo height in pixels")
    tags: list[str] | None = Field(None, description="Tags for logo usage (e.g., 'primary', 'square', 'white')")


class BrandColors(SalesAgentBaseModel):
    """Brand color palette."""

    primary: str | None = Field(None, pattern="^#[0-9A-Fa-f]{6}$", description="Primary brand color (hex)")
    secondary: str | None = Field(None, pattern="^#[0-9A-Fa-f]{6}$", description="Secondary brand color (hex)")
    accent: str | None = Field(None, pattern="^#[0-9A-Fa-f]{6}$", description="Accent color (hex)")
    background: str | None = Field(None, pattern="^#[0-9A-Fa-f]{6}$", description="Background color (hex)")
    text: str | None = Field(None, pattern="^#[0-9A-Fa-f]{6}$", description="Text color (hex)")


class FontGuidance(SalesAgentBaseModel):
    """Typography guidelines."""

    primary: str | None = Field(None, description="Primary font family")
    secondary: str | None = Field(None, description="Secondary font family")
    weights: list[str] | None = Field(None, description="Recommended font weights")


class BrandAsset(SalesAgentBaseModel):
    """Multimedia brand asset."""

    url: str = Field(..., description="URL to brand asset")
    asset_type: str = Field(..., description="Asset type (image, video, audio, etc.)")
    tags: list[str] | None = Field(None, description="Asset tags for categorization")
    width: int | None = Field(None, ge=1, description="Asset width in pixels")
    height: int | None = Field(None, ge=1, description="Asset height in pixels")
    duration: float | None = Field(None, ge=0, description="Duration in seconds (for video/audio)")


# Use library BrandManifest directly - all fields inherited from AdCP spec
BrandManifest: TypeAlias = LibraryBrandManifest


class BrandManifestRef(SalesAgentBaseModel):
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


# --- Package Schemas (Extend adcp library for proper request/response separation) ---


class PackageRequest(LibraryPackageRequest):
    """Package request schema (for CreateMediaBuyRequest).

    Extends adcp library PackageRequest with internal fields.
    Used when CREATING media buys - has creative_ids/creatives/format_ids but no package_id/status.

    Library PackageRequest required fields per AdCP spec:
    - budget, buyer_ref, pricing_option_id, product_id
    """

    model_config = ConfigDict(extra=get_pydantic_extra_mode())

    # Internal fields (not in AdCP spec) - excluded from API responses
    tenant_id: str | None = Field(None, description="Internal: Tenant ID for multi-tenancy", exclude=True)
    media_buy_id: str | None = Field(None, description="Internal: Associated media buy ID", exclude=True)
    platform_line_item_id: str | None = Field(
        None, description="Internal: Platform-specific line item ID", exclude=True
    )
    created_at: datetime | None = Field(None, description="Internal: Creation timestamp", exclude=True)
    updated_at: datetime | None = Field(None, description="Internal: Last update timestamp", exclude=True)
    metadata: dict[str, Any] | None = Field(None, description="Internal: Additional metadata", exclude=True)

    # Legacy field (deprecated - use pricing_option_id instead)
    pricing_model: PricingModel | None = Field(
        None,
        description="DEPRECATED: Use pricing_option_id instead. Selected pricing model for backward compatibility.",
        exclude=True,
    )

    impressions: float | None = Field(None, description="Legacy: Impression goal (use budget instead)", exclude=True)
    # Override creatives type: parent expects CreativeAsset, we use our extended Creative
    # Pydantic validates at runtime but mypy sees type mismatch
    creatives: list["Creative"] | None = Field(  # type: ignore[assignment]
        None,
        description="Full creative objects to upload and assign at creation time (alternative to creative_ids)",
    )
    # V3: creative_ids moved to local extension for backward compatibility with internal code
    # Library V3 uses creatives (full objects), but internal code often uses creative_ids (string list)
    creative_ids: list[str] | None = Field(
        None,
        description="Internal: List of creative IDs to assign (alternative to full creatives objects)",
        exclude=True,
    )
    # Override library TargetingOverlay -> our Targeting with internal fields + legacy normalizer
    targeting_overlay: Targeting | None = None

    @model_validator(mode="before")
    @classmethod
    def remove_invalid_fields(cls, values: dict) -> dict:
        """Remove fields that are not valid in PackageRequest per AdCP spec.

        Handles reconstruction from database where Package (response) may be stored
        but we need PackageRequest (request) for validation.

        Response-only fields to remove:
        - status: Only in Package response, not in PackageRequest
        - package_id: Assigned by publisher, not in request
        """
        if not isinstance(values, dict):
            return values

        # Create copy to avoid mutating input dict (critical for shared/cached dicts)
        values = values.copy()

        # Remove response-only fields when reconstructing from database
        values.pop("status", None)
        values.pop("package_id", None)

        return values

    @model_validator(mode="before")
    @classmethod
    def upgrade_legacy_format_ids(cls, values: dict) -> dict:
        """Convert dict format_ids to FormatId objects (AdCP v2.4 compliance)."""
        return _upgrade_legacy_format_ids(values)


class Package(LibraryPackage):
    """Package response schema (for CreateMediaBuySuccess and responses).

    Extends adcp library Package with internal fields.
    Used in RESPONSES - has package_id/status but no creative_ids/format_ids (those become creative_assignments/format_ids_to_provide).

    Library Package required fields:
    - package_id, status
    """

    # Internal fields (not in AdCP spec) - excluded from API responses
    tenant_id: str | None = Field(None, description="Internal: Tenant ID for multi-tenancy", exclude=True)
    media_buy_id: str | None = Field(None, description="Internal: Associated media buy ID", exclude=True)
    platform_line_item_id: str | None = Field(
        None, description="Internal: Platform-specific line item ID for creative association", exclude=True
    )
    created_at: datetime | None = Field(None, description="Internal: Creation timestamp", exclude=True)
    updated_at: datetime | None = Field(None, description="Internal: Last update timestamp", exclude=True)
    metadata: dict[str, Any] | None = Field(None, description="Internal: Additional metadata", exclude=True)

    # Legacy field (deprecated - use pricing_option_id instead)
    pricing_model: PricingModel | None = Field(
        None,
        description="DEPRECATED: Use pricing_option_id instead. Selected pricing model for backward compatibility.",
        exclude=True,
    )

    # Note: No need for validate_required hack - library Package already has package_id and status as required fields!

    def model_dump_internal(self, **kwargs):
        """Dump including internal fields for database storage and internal processing."""
        # Get base dump with all AdCP fields
        result = super().model_dump(mode="python", exclude_none=False, **kwargs)

        # Manually add internal fields that are marked with exclude=True
        # (Pydantic's exclude=True at field level cannot be overridden via parameters)
        result["tenant_id"] = self.tenant_id
        result["media_buy_id"] = self.media_buy_id
        result["platform_line_item_id"] = self.platform_line_item_id
        result["created_at"] = self.created_at
        result["updated_at"] = self.updated_at
        result["metadata"] = self.metadata
        result["pricing_model"] = self.pricing_model

        return result


# --- Media Buy Lifecycle ---
class CreateMediaBuyRequest(LibraryCreateMediaBuyRequest):
    """Extends library CreateMediaBuyRequest from AdCP spec.

    Per AdCP spec, the required fields are:
    - brand: BrandReference (with domain and optional brand_id)
    - buyer_ref: str (buyer's reference identifier)
    - packages: list[PackageRequest] (array of package configurations)
    - start_time: str | datetime ('asap' or ISO 8601 datetime)
    - end_time: datetime (ISO 8601 datetime)

    Optional fields:
    - context: dict (application-level context)
    - ext: dict (extension object for custom fields)
    - po_number: str (purchase order number)
    - reporting_webhook: dict (webhook configuration)
    """

    model_config = ConfigDict(extra=get_pydantic_extra_mode())

    # Override packages to use our PackageRequest (which overrides targeting_overlay
    # to Targeting instead of library TargetingOverlay, enabling the legacy normalizer).
    # extra='forbid' prevents arbitrary field injection at buyer boundary.
    packages: list[PackageRequest] | None = None  # type: ignore[assignment]

    @model_validator(mode="after")
    def validate_timezone_aware(self):
        """Validate that datetime fields are timezone-aware.

        AdCP spec requires ISO 8601 datetime strings with timezone information.
        This validator ensures all datetime fields have timezone info.
        The literal string 'asap' is also valid per AdCP spec.
        """
        if self.start_time and self.start_time != "asap":
            if isinstance(self.start_time, datetime) and self.start_time.tzinfo is None:
                raise ValueError("start_time must be timezone-aware (ISO 8601 with timezone) or 'asap'")
        if self.end_time and self.end_time.tzinfo is None:
            raise ValueError("end_time must be timezone-aware (ISO 8601 with timezone)")
        return self

    # Helper properties for common access patterns
    @property
    def flight_start_date(self) -> date | None:
        """Extract date from start_time for display purposes."""
        # start_time is StartTiming (RootModel[datetime | 'asap']); unwrap via .root
        inner = self.start_time.root if self.start_time else None
        if isinstance(inner, datetime):
            return inner.date()
        return None

    @property
    def flight_end_date(self) -> date | None:
        """Extract date from end_time for display purposes."""
        return self.end_time.date() if self.end_time else None

    def get_total_budget(self) -> float:
        """Calculate total budget by summing all package budgets.

        Per AdCP spec, budget is specified at the package level, not the media buy level.
        This method calculates the total by summing all package budgets.
        """
        if self.packages:
            total = 0.0
            for package in self.packages:
                if package.budget:
                    total += float(package.budget)
            return total
        return 0.0

    def get_product_ids(self) -> list[str]:
        """Extract unique product IDs from packages per AdCP spec.

        Per AdCP spec, packages use product_id (singular, required) field.
        Returns list of unique product IDs (no duplicates).
        """
        if self.packages:
            product_ids = []
            for package in self.packages:
                if package.product_id:
                    product_ids.append(package.product_id)
            # Remove duplicates while preserving order
            return list(dict.fromkeys(product_ids))
        return []


class CheckMediaBuyStatusRequest(SalesAgentBaseModel):
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


class CheckMediaBuyStatusResponse(SalesAgentBaseModel):
    media_buy_id: str
    buyer_ref: str
    status: str  # pending_creative, active, paused, completed, failed
    packages: list[dict[str, Any]] | None = None
    budget_spent: Budget | None = None
    budget_remaining: Budget | None = None
    creative_count: int = 0


# --- Additional Schema Classes ---
class MediaPackage(SalesAgentBaseModel):
    package_id: str
    name: str
    delivery_type: Literal["guaranteed", "non_guaranteed"]
    cpm: float
    impressions: int
    # Accept library FormatId (not our extended FormatId) to avoid validation errors
    # when Product from library returns LibraryFormatId instances
    format_ids: list[LibraryFormatId]  # FormatId objects per AdCP spec
    targeting_overlay: Targeting | None = None
    buyer_ref: str | None = None  # Optional buyer reference from request package
    product_id: str | None = None  # Product ID for this package
    budget: float | None = None  # Budget allocation in the currency specified by the pricing option
    creative_ids: list[str] | None = None  # Creative IDs to assign to this package


class PackagePerformance(SalesAgentBaseModel):
    package_id: str
    performance_index: float


class AssetStatus(SalesAgentBaseModel):
    asset_id: str | None = None  # Asset identifier
    creative_id: str | None = None  # GAM creative ID (may be None for pending/failed)
    status: str  # Status: draft, active, submitted, failed, etc.
    message: str | None = None  # Status message
    workflow_step_id: str | None = None  # HITL workflow step ID for manual approval


# Unified update models
class PackageUpdate(SalesAgentBaseModel):
    """Updates to apply to a specific package."""

    package_id: str
    active: bool | None = None  # True to activate, False to pause
    budget: float | None = Field(None, ge=0)  # Budget allocation in the currency specified by the pricing option
    impressions: int | None = None  # Direct impression goal (overrides budget calculation)
    cpm: float | None = None  # Update CPM rate
    daily_budget: float | None = None  # Daily spend cap
    daily_impressions: int | None = None  # Daily impression cap
    pacing: Literal["even", "asap", "front_loaded"] | None = None
    creative_ids: list[str] | None = None  # Update creative assignments
    targeting_overlay: Targeting | None = None  # Package-specific targeting refinements


class UpdatePackageRequest(SalesAgentBaseModel):
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
class AdCPPackageUpdate(LibraryPackageUpdate1):
    """Package-specific update extending library type.

    Inherits all fields from library (budget, paused, targeting_overlay,
    creative_assignments, creatives, bid_price, ext, impressions, pacing).

    Adds creative_ids — spec-mandated field missing from library codegen.
    TODO(adcp-library): Remove creative_ids once upstream codegen adds it.
    """

    model_config = ConfigDict(extra=get_pydantic_extra_mode())
    # Override package_id to be optional (library variant1 has it required for oneOf)
    package_id: str | None = None  # type: ignore[assignment]
    # Spec field missing from library codegen (adcp#208)
    creative_ids: list[str] | None = None


class UpdateMediaBuyRequest(LibraryUpdateMediaBuyRequest1):
    """Update media buy request extending library type.

    Inherits all AdCP fields from library (paused, start_time, end_time,
    packages, push_notification_config, context, reporting_webhook, ext).

    Overrides:
    - media_buy_id: optional (library variant1 requires it; oneOf handled at app level)
    - start_time/end_time: accept raw datetime/str (backward compat with A2A path)
    - packages: use our AdCPPackageUpdate (adds creative_ids)
    - budget: campaign-level budget (not in library — convenience field)
    - today: internal testing field
    """

    model_config = ConfigDict(extra=get_pydantic_extra_mode())
    # Override media_buy_id to be optional (library variant1 has it required for oneOf)
    media_buy_id: str | None = None  # type: ignore[assignment]
    # Override datetime fields to accept raw strings (A2A path sends ISO strings)
    start_time: datetime | Literal["asap"] | None = None  # type: ignore[assignment]
    end_time: datetime | None = None
    # Override packages to use our extended type with creative_ids
    packages: list[AdCPPackageUpdate] | None = None  # type: ignore[assignment]
    # Campaign-level budget (not in library spec — convenience field)
    budget: Budget | None = None
    # Internal testing field
    today: date | None = Field(None, exclude=True, description="For testing/simulation only - not part of AdCP spec")

    @model_validator(mode="before")
    @classmethod
    def unwrap_and_parse(cls, values):
        """Unwrap RootModel packages and parse datetime strings."""
        if not isinstance(values, dict):
            return values

        # Unwrap RootModel packages (FastMCP produces library PackageUpdate RootModel,
        # but JSON/dict input arrives as plain dicts — guard needed in pre-validator)
        if "packages" in values and values["packages"]:
            unwrapped = []
            for pkg in values["packages"]:
                if isinstance(pkg, RootModel):
                    unwrapped.append(pkg.root.model_dump(mode="json"))
                else:
                    unwrapped.append(pkg)
            values["packages"] = unwrapped

        # Parse ISO 8601 datetime strings (A2A path sends raw strings)
        if "start_time" in values:
            start_time = values["start_time"]
            if isinstance(start_time, str) and start_time != "asap":
                values["start_time"] = datetime.fromisoformat(start_time)

        if "end_time" in values:
            end_time = values["end_time"]
            if isinstance(end_time, str):
                values["end_time"] = datetime.fromisoformat(end_time)

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

    @model_validator(mode="after")
    def validate_identification_xor(self):
        """Enforce AdCP oneOf: exactly one of media_buy_id or buyer_ref required.

        Per update-media-buy-request.json, the request uses oneOf to require
        either media_buy_id or buyer_ref, but not both and not neither.
        """
        has_id = self.media_buy_id is not None
        has_ref = self.buyer_ref is not None
        if has_id and has_ref:
            raise ValueError("Provide either media_buy_id or buyer_ref, not both (AdCP oneOf constraint)")
        if not has_id and not has_ref:
            raise ValueError("Either media_buy_id or buyer_ref is required (AdCP oneOf constraint)")
        return self

    def has_updatable_fields(self) -> bool:
        """Check whether this request includes at least one updatable field.

        Returns True if any field beyond the identifier (media_buy_id/buyer_ref)
        is set. Used by _build_update_request to enforce BR-RULE-022.
        """
        return any(
            f is not None
            for f in (
                self.paused,
                self.start_time,
                self.end_time,
                self.packages,
                self.budget,
                self.push_notification_config,
                self.reporting_webhook,
                self.context,
                self.ext,
            )
        )

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


# --- Human-in-the-Loop Task Queue ---


class HumanTask(SalesAgentBaseModel):
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


class CreateHumanTaskRequest(SalesAgentBaseModel):
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


class CreateHumanTaskResponse(SalesAgentBaseModel):
    """Response from creating a human task."""

    task_id: str
    status: str
    due_by: datetime | None = None

    def __str__(self) -> str:
        """Return human-readable text for MCP content field."""
        return f"Task {self.task_id} created with status: {self.status}"


class GetPendingTasksRequest(SalesAgentBaseModel):
    """Request for pending human tasks."""

    principal_id: str | None = None  # Filter by principal
    task_type: str | None = None  # Filter by type
    priority: str | None = None  # Filter by minimum priority
    assigned_to: str | None = None  # Filter by assignee
    include_overdue: bool = True


class GetPendingTasksResponse(NestedModelSerializerMixin, SalesAgentBaseModel):
    """Response with pending tasks."""

    tasks: list[HumanTask]
    total_count: int
    overdue_count: int


class AssignTaskRequest(SalesAgentBaseModel):
    """Request to assign a task."""

    task_id: str
    assigned_to: str


class CompleteTaskRequest(SalesAgentBaseModel):
    """Request to complete a task."""

    task_id: str
    resolution: str  # approved, rejected, completed, cannot_complete
    resolution_detail: str | None = None
    resolved_by: str


class VerifyTaskRequest(SalesAgentBaseModel):
    """Request to verify if a task was completed correctly."""

    task_id: str
    expected_outcome: dict[str, Any] | None = None  # What the task should have accomplished


class VerifyTaskResponse(SalesAgentBaseModel):
    """Response from task verification."""

    task_id: str
    verified: bool
    actual_state: dict[str, Any]
    expected_state: dict[str, Any] | None = None
    discrepancies: list[str] = []


class MarkTaskCompleteRequest(SalesAgentBaseModel):
    """Admin request to mark a task as complete with verification."""

    task_id: str
    override_verification: bool = False  # Force complete even if verification fails
    completed_by: str


# Targeting capabilities
class GetTargetingCapabilitiesRequest(SalesAgentBaseModel):
    """Query targeting capabilities for channels."""

    channels: list[str] | None = None  # If None, return all channels
    include_aee_dimensions: bool = True


class TargetingDimensionInfo(SalesAgentBaseModel):
    """Information about a single targeting dimension."""

    key: str
    display_name: str
    description: str
    data_type: str
    required: bool = False
    values: list[str] | None = None


class ChannelTargetingCapabilities(SalesAgentBaseModel):
    """Targeting capabilities for a specific channel."""

    channel: str
    overlay_dimensions: list[TargetingDimensionInfo]
    aee_dimensions: list[TargetingDimensionInfo] | None = None


class GetTargetingCapabilitiesResponse(NestedModelSerializerMixin, SalesAgentBaseModel):
    """Response with targeting capabilities."""

    capabilities: list[ChannelTargetingCapabilities]


class CheckAXERequirementsRequest(SalesAgentBaseModel):
    """Check if required AXE dimensions are supported."""

    channel: str
    required_dimensions: list[str]


class CheckAXERequirementsResponse(SalesAgentBaseModel):
    """Response for AXE requirements check."""

    supported: bool
    missing_dimensions: list[str]
    available_dimensions: list[str]


# Creative macro is now a simple string passed via AXE axe_signals


# --- Signal Discovery ---
class SignalDeployment(LibraryPlatformDeployment):
    """Extends library PlatformDeployment with internal signal deployment fields.

    Library provides: platform, account, is_live, type, activation_key,
    deployed_at, estimated_activation_duration_minutes.

    Local additions (internal-only, excluded from responses):
    - scope: Derived from deployment type for internal routing
    - decisioning_platform_segment_id: Platform-specific segment ID after activation
    """

    model_config = ConfigDict(extra=get_pydantic_extra_mode())

    scope: Literal["platform-wide", "account-specific"] = Field(
        default="platform-wide", description="Deployment scope (internal)", exclude=True
    )
    decisioning_platform_segment_id: str | None = Field(
        default=None, description="Platform-specific segment ID (internal)", exclude=True
    )


class SignalPricing(LibraryPricing):
    """Extends library Pricing for signal-specific pricing.

    Library provides: cpm, currency. This subclass preserves the library
    schema while allowing future internal field additions.
    """

    model_config = ConfigDict(extra=get_pydantic_extra_mode())


class Signal(LibrarySignal):
    """Extends library Signal with internal fields and local deployment/pricing types.

    Library provides: signal_agent_segment_id, name, description, signal_type,
    data_provider, coverage_percentage, deployments, pricing — all inherited
    from AdCP spec.

    Local overrides:
    - signal_type: Literal instead of enum (string serialization in model_dump)
    - deployments: local SignalDeployment (has scope, decisioning_platform_segment_id)
    - pricing: local SignalPricing (same structure, different base)
    - Internal fields with Field(exclude=True): tenant_id, created_at, updated_at, metadata
    """

    model_config = ConfigDict(extra=get_pydantic_extra_mode())

    # Override types that differ from library
    signal_type: Literal["marketplace", "custom", "owned"] = Field(..., description="Type of signal")  # type: ignore[assignment]
    deployments: list[SignalDeployment] = Field(..., description="Array of platform deployments")  # type: ignore[assignment]
    pricing: SignalPricing = Field(..., description="Pricing information")

    # Internal fields (not in AdCP spec, excluded from serialization)
    tenant_id: str | None = Field(None, description="Internal: Tenant ID for multi-tenancy", exclude=True)
    created_at: datetime | None = Field(None, description="Internal: Creation timestamp", exclude=True)
    updated_at: datetime | None = Field(None, description="Internal: Last update timestamp", exclude=True)
    metadata: dict[str, Any] | None = Field(None, description="Internal: Additional metadata", exclude=True)

    # Backward compatibility properties (deprecated)
    # Note: signal_id is now a library field in adcp 3.6.0 (SignalId | None)
    # The old @property signal_id that mapped to signal_agent_segment_id is removed
    # to avoid conflict with the new library field.

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

    def model_dump_internal(self, **kwargs: Any) -> dict[str, Any]:
        """Dump including internal fields for database storage.

        Pydantic v2's Field(exclude=True) cannot be overridden via model_dump parameters.
        We manually include internal fields by accessing the attributes directly.
        """
        data = super().model_dump(exclude=set(), **kwargs)

        # Manually add excluded fields
        for field_name in ("tenant_id", "created_at", "updated_at", "metadata"):
            val = getattr(self, field_name, None)
            if val is not None:
                data[field_name] = val

        return data


class SignalFilters(LibrarySignalFilters):
    """Signal filters per AdCP get-signals-request schema.

    Extends library type - all fields inherited.
    """

    pass  # All fields inherited from library


# GetSignalsRequest — library 3.6.0 exports a UnionType (GetSignalsRequest1 | GetSignalsRequest2).
# GS1: signal_spec required, signal_ids optional (discovery by text).
# GS2: signal_ids required, signal_spec optional (lookup by ID).
# We alias to GS1 (text-based discovery) which is our primary use case.
# This gives us a concrete BaseModel class for construction, model_fields, and mypy.
from adcp.types.generated_poc.signals.get_signals_request import (  # noqa: E402, F401
    GetSignalsRequest1 as GetSignalsRequest,
)


class GetSignalsResponse(NestedModelSerializerMixin, LibraryGetSignalsResponse):
    """Extends library GetSignalsResponse with local Signal type.

    Library provides: signals, errors, context, ext — all inherited from AdCP spec.
    Local override: signals uses local Signal type (with exclude=True internal fields).
    """

    model_config = ConfigDict(extra=get_pydantic_extra_mode())

    signals: list[Signal] = Field(..., description="Array of available signals")  # type: ignore[assignment]

    def __str__(self) -> str:
        """Return human-readable summary message for protocol envelope."""
        count = len(self.signals)
        if count == 0:
            return "No signals found matching your criteria."
        elif count == 1:
            return "Found 1 signal."
        return f"Found {count} signals."


# --- Signal Activation ---
class ActivateSignalRequest(LibraryActivateSignalRequest):
    """Extends library ActivateSignalRequest with local extension fields.

    Library provides: signal_agent_segment_id, deployments, context, ext.
    Local extensions: campaign_id, media_buy_id (unused in impl, kept for API compat).

    NOTE: ActivateSignalResponse is NOT migrated — library uses RootModel
    discriminated union (success|error) which is fundamentally incompatible
    with the local flat model pattern.
    """

    model_config = ConfigDict(extra=get_pydantic_extra_mode())

    # Extension fields (not in library spec)
    campaign_id: str | None = Field(None, description="Optional campaign ID to activate signal for")
    media_buy_id: str | None = Field(None, description="Optional media buy ID to activate signal for")

    @property
    def signal_id(self) -> str:
        """DEPRECATED: Use signal_agent_segment_id instead."""
        warnings.warn(
            "signal_id is deprecated. Use signal_agent_segment_id instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.signal_agent_segment_id


class ActivateSignalResponse(SalesAgentBaseModel):
    """Response from signal activation.

    NOT migrated to library base (evaluated in salesagent-xeb):
    1. Library uses RootModel[SuccessVariant | ErrorVariant] — cannot add fields
    2. Library has no signal_id field (no request correlation in response)
    3. Library uses structured list[Deployment] vs our generic activation_details dict
    4. Library enforces atomic success/error; we allow both simultaneously
    """

    signal_id: str = Field(..., description="Activated signal ID")
    activation_details: dict[str, Any] | None = Field(None, description="Platform-specific activation details")
    errors: list[Error] | None = Field(None, description="Optional error reporting")
    context: ContextObject | None = Field(None, description="Application-level context echoed from the request")

    def __str__(self) -> str:
        """Return human-readable summary message for protocol envelope."""
        if self.errors:
            return f"Signal {self.signal_id} activation encountered {len(self.errors)} error(s)."
        return f"Signal {self.signal_id} activated successfully."


# --- Simulation and Time Progression Control ---
class SimulationControlRequest(SalesAgentBaseModel):
    """Control simulation time progression and events."""

    strategy_id: str = Field(..., description="Strategy ID to control (must be simulation strategy with 'sim_' prefix)")
    action: Literal["jump_to", "reset", "set_scenario"] = Field(..., description="Action to perform on the simulation")
    parameters: dict[str, Any] = Field(default_factory=dict, description="Action-specific parameters")
    context: ContextObject | None = Field(None, description="Application-level context echoed from the request")


class SimulationControlResponse(SalesAgentBaseModel):
    """Response from simulation control operations."""

    status: Literal["ok", "error"] = "ok"
    message: str | None = None
    current_state: dict[str, Any] | None = None
    simulation_time: datetime | None = None
    context: ContextObject | None = Field(None, description="Application-level context echoed from the request")

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
# Use library types directly - all fields inherited from AdCP spec
# V3: Property uses property-specific Identifier, not generic Identifier
from adcp.types.generated_poc.core.property import Identifier as PropertySpecificIdentifier

PropertyIdentifier: TypeAlias = PropertySpecificIdentifier  # Property-specific identifier
Property: TypeAlias = LibraryProperty


class PropertyTagMetadata(SalesAgentBaseModel):
    """Metadata for a property tag."""

    name: str = Field(..., description="Human-readable name for this tag")
    description: str = Field(..., description="Description of what this tag represents")


class ListAuthorizedPropertiesRequest(SalesAgentBaseModel):
    """Request payload for list_authorized_properties task (AdCP spec).

    Note: This type was removed from adcp 3.2.0, so we define it locally.

    Fields:
    - context: Application-level context (optional)
    - ext: Extension object for custom fields (optional)
    - property_tags: Filter to specific property tags (optional)
    - publisher_domains: Filter to specific publisher domains (optional)
    """

    context: ContextObject | None = Field(default=None, description="Application-level context")
    ext: dict[str, Any] | None = Field(default=None, description="Extension object for custom fields")
    property_tags: list[str] | None = Field(default=None, description="Filter to specific property tags")
    publisher_domains: list[str] | None = Field(default=None, description="Filter to specific publisher domains")


class ListAuthorizedPropertiesResponse(NestedModelSerializerMixin, SalesAgentBaseModel):
    """Response payload for list_authorized_properties task (AdCP v2.4 spec compliant).

    NOTE: Does not extend library type yet because local publisher_domains type
    (list[str]) differs from library type (list[PublisherDomain]). Migration tracked in issue #824.

    Per official AdCP v2.4 spec, this response lists publisher domains.
    Buyers fetch property definitions from each publisher's adagents.json file.

    Protocol fields (status, task_id, message, context_id) are added by the
    protocol layer (MCP, A2A, REST) via ProtocolEnvelope wrapper.
    """

    publisher_domains: list[str] = Field(..., description="Publisher domains this agent is authorized to represent")
    context: ContextObject | None = Field(None, description="Application-level context echoed from the request")
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
        description="ISO 8601 timestamp of when the agent's publisher authorization list was last updated.",
    )
    errors: list[Error] | None = Field(
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


# --- Get Media Buys Types ---
# These types match the adcp 3.6.0 spec for get_media_buys.
# When the project migrates to adcp >=3.6.0, these can be replaced with library imports.


from src.core.schemas.delivery import DeliveryStatus  # noqa: E402


class SnapshotUnavailableReason(str, Enum):
    """Reason why a delivery snapshot is not available."""

    SNAPSHOT_UNSUPPORTED = "SNAPSHOT_UNSUPPORTED"
    SNAPSHOT_TEMPORARILY_UNAVAILABLE = "SNAPSHOT_TEMPORARILY_UNAVAILABLE"


class ApprovalStatus(str, Enum):
    """Approval status value for a creative assignment in a get_media_buys response."""

    pending_review = "pending_review"
    approved = "approved"
    rejected = "rejected"


class Snapshot(SalesAgentBaseModel):
    """Near-real-time delivery snapshot for a package.

    Matches the adcp 3.6.0 Snapshot type spec.
    as_of is required so consumers know the data freshness.
    """

    as_of: datetime = Field(..., description="ISO 8601 timestamp when this snapshot was captured by the platform")
    impressions: float = Field(..., ge=0.0, description="Total impressions delivered since package start")
    spend: float = Field(..., ge=0.0, description="Total spend since package start")
    staleness_seconds: int = Field(..., ge=0, description="Maximum age of this data in seconds")
    clicks: float | None = Field(default=None, ge=0.0, description="Total clicks since package start (when available)")
    pacing_index: float | None = Field(
        default=None, ge=0.0, description="Current delivery pace relative to expected (1.0 = on track)"
    )
    delivery_status: DeliveryStatus | None = Field(
        default=None, description="Operational delivery state of this package"
    )
    currency: str | None = Field(default=None, description="ISO 4217 currency code for spend in this snapshot")


class GetMediaBuysPackage(SalesAgentBaseModel):
    """Package details within a GetMediaBuys response."""

    package_id: str = Field(..., description="Package identifier")
    buyer_ref: str | None = Field(default=None, description="Buyer reference for this package")
    budget: float | None = Field(default=None, description="Package budget allocation")
    bid_price: float | None = Field(default=None, description="Bid price for auction-based pricing")
    product_id: str | None = Field(default=None, description="Product identifier for this package")
    start_time: str | None = Field(default=None, description="Package start time (ISO 8601)")
    end_time: str | None = Field(default=None, description="Package end time (ISO 8601)")
    paused: bool | None = Field(default=None, description="Whether this package is paused")
    creative_approvals: list["CreativeApproval"] | None = Field(
        default=None, description="Creative approval state for creatives assigned to this package"
    )
    snapshot: Snapshot | None = Field(
        default=None, description="Near-real-time delivery snapshot (present when include_snapshot=true)"
    )
    snapshot_unavailable_reason: SnapshotUnavailableReason | None = Field(
        default=None, description="Reason snapshot is unavailable (present when include_snapshot=true but no snapshot)"
    )


class GetMediaBuysMediaBuy(SalesAgentBaseModel):
    """Media buy details in a GetMediaBuys response."""

    media_buy_id: str = Field(..., description="Publisher media buy identifier")
    buyer_ref: str | None = Field(default=None, description="Buyer reference identifier")
    buyer_campaign_ref: str | None = Field(default=None, description="Buyer campaign reference")
    status: MediaBuyStatus = Field(..., description="Current media buy status")
    currency: str = Field(..., description="ISO 4217 currency code")
    total_budget: float = Field(..., description="Total budget across all packages")
    packages: list[GetMediaBuysPackage] = Field(..., description="Packages within this media buy")
    created_at: datetime | None = Field(default=None, description="When this media buy was created")
    updated_at: datetime | None = Field(default=None, description="When this media buy was last updated")

    def model_dump(self, **kwargs):
        result = super().model_dump(**kwargs)
        if "packages" in result and self.packages:
            result["packages"] = [pkg.model_dump(**kwargs) for pkg in self.packages]
        return result


class GetMediaBuysRequest(SalesAgentBaseModel):
    """Request to retrieve media buys.

    Matches the adcp 3.6.0 GetMediaBuysRequest spec.
    Defined locally because adcp 3.6.0 is not yet required.
    """

    media_buy_ids: list[str] | None = Field(default=None, description="Specific media buy IDs to retrieve")
    buyer_refs: list[str] | None = Field(default=None, description="Buyer references to filter by")
    status_filter: Any | None = Field(default=None, description="Filter by status (MediaBuyStatus or list)")
    include_snapshot: bool = Field(default=False, description="Include near-real-time delivery snapshot per package")
    account_id: str | None = Field(default=None, description="Account to filter to")
    context: ContextObject | None = Field(default=None, description="Application-level context")


class GetMediaBuysResponse(NestedModelSerializerMixin, SalesAgentBaseModel):
    """Response from get_media_buys.

    Matches the adcp 3.6.0 GetMediaBuysResponse spec.
    """

    media_buys: list[GetMediaBuysMediaBuy] = Field(..., description="List of matching media buys")
    errors: list[Any] | None = Field(default=None, description="Errors encountered during retrieval")
    context: ContextObject | None = Field(default=None, description="Application-level context from the request")

    def model_dump(self, **kwargs):
        result = super().model_dump(**kwargs)
        if "media_buys" in result and self.media_buys:
            result["media_buys"] = [mb.model_dump(**kwargs) for mb in self.media_buys]
        return result


# Re-export product schemas for backward compatibility.
# These were extracted to src.core.schemas.product but must remain
# importable from src.core.schemas.
from src.core.schemas.product import (  # noqa: E402
    GetProductsRequest as GetProductsRequest,
)
from src.core.schemas.product import (
    GetProductsResponse as GetProductsResponse,
)
from src.core.schemas.product import (
    Placement as Placement,
)
from src.core.schemas.product import (
    Product as Product,
)
from src.core.schemas.product import (
    ProductCard as ProductCard,
)
from src.core.schemas.product import (
    ProductCardDetailed as ProductCardDetailed,
)
from src.core.schemas.product import (
    ProductCatalog as ProductCatalog,
)
from src.core.schemas.product import (
    ProductFilters as ProductFilters,
)
