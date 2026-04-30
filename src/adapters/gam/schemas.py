"""Google Ad Manager adapter configuration schemas.

Single source of truth for GAM-adapter Pydantic shapes:
- GAMProductConfig: per-product implementation_config (registered as
  product_config_class on GoogleAdManager; validated at the adapter
  boundary on read via parse_implementation_config)

Reconciles the legacy `GAMImplementationConfig(BaseModel)` (which was never
imported anywhere in src/ and lacked the BaseProductConfig contract) into a
proper registered schema. Phase 2 of #996 — sister to MockProductConfig
(#1240) and BroadstreetProductConfig (#1241).

Audit-driven additions
----------------------

Two fields surfaced during the read-site audit but missing from the legacy
schema have been added: `supported_format_types` (used in
gam/managers/orders.py:476) and `time_zone` (used at lines 898, 905). Both
match the production-observed defaults.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from src.adapters.base import BaseProductConfig


class CreativePlaceholder(BaseModel):
    """Defines expected creative specifications for a GAM line item."""

    width: int = Field(..., description="Creative width in pixels")
    height: int = Field(..., description="Creative height in pixels")
    expected_creative_count: int = Field(1, description="Number of creatives expected for this size")
    is_native: bool = Field(False, description="Whether this is a native creative placeholder")


class FrequencyCap(BaseModel):
    """Defines GAM frequency capping rules."""

    max_impressions: int = Field(..., description="Maximum impressions")
    time_unit: str = Field(..., description="Time unit: MINUTE, HOUR, DAY, WEEK, MONTH, LIFETIME")
    time_range: int = Field(1, description="Number of time units")


class PlacementTargeting(BaseModel):
    """GAM targeting configuration for a specific placement.

    Enables creative-level targeting in GAM. Each placement maps to:
    - A targetingName used in LICA associations
    - GAM targeting criteria (customTargeting, geoTargeting, etc.)

    When a buyer assigns a creative to a placement_id, the LICA is created
    with the corresponding targetingName, applying the targeting as an AND
    with line item targeting.
    """

    placement_id: str = Field(..., description="AdCP placement_id (must match Product.placements[].placement_id)")
    targeting_name: str = Field(..., description="GAM targetingName for LICA association")
    targeting: dict[str, Any] = Field(
        default_factory=dict, description="GAM targeting criteria (customTargeting, geoTargeting, etc.)"
    )


class GAMProductConfig(BaseProductConfig):
    """Per-product GAM adapter configuration.

    Stored in Product.implementation_config; controls how GAM Orders and
    Line Items are created when fulfilling a media buy. Registered as
    GoogleAdManager.product_config_class and validated at the adapter
    boundary on read.
    """

    adapter_type: Literal["google_ad_manager"] = Field(
        default="google_ad_manager",
        description="Adapter discriminator for typed implementation_config dispatch.",
    )

    # Order-level settings
    order_name_template: str = Field(
        default="AdCP-{po_number}-{product_name}-{timestamp}",
        description="Template for order naming. Variables: {po_number}, {product_name}, {timestamp}, {principal_name}",
    )
    applied_team_ids: list[int] = Field(default_factory=list, description="GAM team IDs for access control")

    # Line item basic settings
    line_item_type: str = Field(
        default="STANDARD",
        description="Type: STANDARD, SPONSORSHIP, NETWORK, HOUSE, PRICE_PRIORITY",
    )
    priority: int = Field(
        default=8,
        description="Priority level (1-16, lower number = higher priority). Standard is 8, Deals are 4-6",
    )

    # Delivery settings
    creative_rotation_type: str = Field(
        default="EVEN", description="How to rotate creatives: EVEN, OPTIMIZED, MANUAL, SEQUENTIAL"
    )
    delivery_rate_type: str = Field(
        default="EVENLY", description="Delivery pacing: EVENLY, FRONTLOADED, AS_FAST_AS_POSSIBLE"
    )
    time_zone: str = Field(
        default="America/New_York",
        description="GAM time-zone ID for line item start/end times. (Audit: orders.py:898,905)",
    )

    # Pricing and goals
    cost_type: str = Field(default="CPM", description="Pricing model: CPM, CPC, CPD, CPA")
    discount_type: str | None = Field(
        default=None, description="Discount type if applicable: PERCENTAGE, ABSOLUTE_VALUE"
    )
    discount_value: float | None = Field(
        default=None, description="Discount value (percentage or absolute based on discount_type)"
    )

    primary_goal_type: str = Field(default="LIFETIME", description="Goal type: LIFETIME, DAILY, NONE")
    primary_goal_unit_type: str = Field(
        default="IMPRESSIONS", description="Goal unit: IMPRESSIONS, CLICKS, VIEWABLE_IMPRESSIONS"
    )

    # Creative specifications
    creative_placeholders: list[CreativePlaceholder] = Field(
        default_factory=list, description="Expected creative sizes and specifications"
    )
    supported_format_types: list[str] = Field(
        default_factory=lambda: ["display", "video", "native"],
        description="AdCP format types this product accepts. (Audit: orders.py:476)",
    )

    # Ad unit/placement targeting
    targeted_ad_unit_ids: list[str] = Field(default_factory=list, description="Specific GAM ad unit IDs to target")
    excluded_ad_unit_ids: list[str] = Field(default_factory=list, description="GAM ad unit IDs to exclude")
    targeted_placement_ids: list[str] = Field(default_factory=list, description="GAM placement IDs to target")
    include_descendants: bool = Field(default=True, description="Include child ad units in targeting")

    # Frequency capping
    frequency_caps: list[FrequencyCap] = Field(default_factory=list, description="Frequency capping rules")

    # Competition and exclusions
    competitive_exclusion_labels: list[str] = Field(
        default_factory=list, description="Labels to prevent competitive ads from serving together"
    )

    # Video-specific settings
    environment_type: str = Field(default="BROWSER", description="Environment: BROWSER or VIDEO_PLAYER")
    companion_delivery_option: str | None = Field(default=None, description="For video: OPTIONAL, AT_LEAST_ONE, ALL")
    video_max_duration: int | None = Field(default=None, description="Maximum video duration in milliseconds")
    skip_offset: int | None = Field(default=None, description="When skip button appears (milliseconds from start)")

    # Advanced settings
    disable_viewability_avg_revenue_optimization: bool = Field(
        default=False, description="Disable viewability-based optimization"
    )
    allow_overbook: bool = Field(default=False, description="Allow overbooking of inventory")
    skip_inventory_check: bool = Field(default=False, description="Skip inventory availability check")

    # Custom targeting template
    custom_targeting_keys: dict[str, Any] = Field(
        default_factory=dict, description="Custom key-value pairs for targeting"
    )

    # Native ad settings
    native_style_id: str | None = Field(default=None, description="GAM native style ID if using native ads")

    # Creative-level placement targeting
    placement_targeting: list[PlacementTargeting] = Field(
        default_factory=list,
        description="Creative-level targeting for placements. Maps placement_ids to GAM targeting rules.",
    )

    # Automation settings for non-guaranteed orders
    non_guaranteed_automation: str = Field(
        default="manual",
        description=(
            "Automation mode for non-guaranteed line item types: 'automatic' (instant activation), "
            "'confirmation_required' (human approval then auto-activation), 'manual' (human handles all steps)"
        ),
    )

    @field_validator("line_item_type")
    @classmethod
    def validate_line_item_type(cls, v: str) -> str:
        valid = {"STANDARD", "SPONSORSHIP", "NETWORK", "HOUSE", "PRICE_PRIORITY"}
        if v not in valid:
            raise ValueError(f"Invalid line_item_type. Must be one of: {valid}")
        return v

    @field_validator("priority")
    @classmethod
    def validate_priority(cls, v: int) -> int:
        if not 1 <= v <= 16:
            raise ValueError("Priority must be between 1 and 16")
        return v

    @field_validator("cost_type")
    @classmethod
    def validate_cost_type(cls, v: str) -> str:
        valid = {"CPM", "CPC", "CPD", "CPA"}
        if v not in valid:
            raise ValueError(f"Invalid cost_type. Must be one of: {valid}")
        return v

    @field_validator("non_guaranteed_automation")
    @classmethod
    def validate_non_guaranteed_automation(cls, v: str) -> str:
        valid = {"automatic", "confirmation_required", "manual"}
        if v not in valid:
            raise ValueError(f"Invalid non_guaranteed_automation. Must be one of: {valid}")
        return v


def parse_implementation_config(config: dict[str, Any] | None) -> GAMProductConfig | None:
    """Parse Product.implementation_config into a validated GAMProductConfig.

    GAM has no meaningful default config (most fields are tenant-specific
    GAM IDs), so empty/None input returns None — callers must handle the
    absence explicitly. Non-empty input is validated strictly;
    ValidationError propagates on malformed shapes.
    """
    if not config:
        return None
    return GAMProductConfig.model_validate(config)


# Example configuration for a standard display product
EXAMPLE_DISPLAY_CONFIG = {
    "order_name_template": "AdCP-{po_number}-Display-{timestamp}",
    "line_item_type": "STANDARD",
    "priority": 8,
    "creative_rotation_type": "EVEN",
    "delivery_rate_type": "EVENLY",
    "cost_type": "CPM",
    "primary_goal_type": "LIFETIME",
    "primary_goal_unit_type": "IMPRESSIONS",
    "creative_placeholders": [
        {"width": 300, "height": 250, "expected_creative_count": 1},
        {"width": 728, "height": 90, "expected_creative_count": 1},
        {"width": 320, "height": 50, "expected_creative_count": 1},
    ],
    "include_descendants": True,
    "frequency_caps": [{"max_impressions": 3, "time_unit": "DAY", "time_range": 1}],
}

# Example configuration for a video product
EXAMPLE_VIDEO_CONFIG = {
    "order_name_template": "AdCP-{po_number}-Video-{timestamp}",
    "line_item_type": "STANDARD",
    "priority": 6,
    "creative_rotation_type": "OPTIMIZED",
    "delivery_rate_type": "EVENLY",
    "cost_type": "CPM",
    "primary_goal_type": "LIFETIME",
    "primary_goal_unit_type": "IMPRESSIONS",
    "creative_placeholders": [{"width": 640, "height": 480, "expected_creative_count": 1}],
    "environment_type": "VIDEO_PLAYER",
    "video_max_duration": 30000,
    "skip_offset": 5000,
    "companion_delivery_option": "OPTIONAL",
    "include_descendants": True,
}
