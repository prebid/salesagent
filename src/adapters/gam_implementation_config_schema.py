"""
Google Ad Manager Implementation Config Schema

This module defines the structure of implementation_config for GAM products.
Based on analysis of Prebid Line Item Manager patterns.
"""

from typing import Any

from pydantic import BaseModel, Field, field_validator


class CreativePlaceholder(BaseModel):
    """Defines expected creative specifications"""

    width: int = Field(..., description="Creative width in pixels")
    height: int = Field(..., description="Creative height in pixels")
    expected_creative_count: int = Field(1, description="Number of creatives expected for this size")
    is_native: bool = Field(False, description="Whether this is a native creative placeholder")


class FrequencyCap(BaseModel):
    """Defines frequency capping rules"""

    max_impressions: int = Field(..., description="Maximum impressions")
    time_unit: str = Field(..., description="Time unit: MINUTE, HOUR, DAY, WEEK, MONTH, LIFETIME")
    time_range: int = Field(1, description="Number of time units")


class PlacementTargeting(BaseModel):
    """GAM targeting configuration for a specific placement.

    This enables creative-level targeting in GAM. Each placement maps to:
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


class GAMImplementationConfig(BaseModel):
    """
    Complete configuration for creating GAM line items.
    This config is stored in the products table implementation_config field.
    """

    # Order-level settings
    order_name_template: str = Field(
        "AdCP-{po_number}-{product_name}-{timestamp}",
        description="Template for order naming. Variables: {po_number}, {product_name}, {timestamp}, {principal_name}",
    )
    applied_team_ids: list[int] = Field(default_factory=list, description="GAM team IDs for access control")

    # Line item basic settings
    line_item_type: str = Field("STANDARD", description="Type: STANDARD, SPONSORSHIP, NETWORK, HOUSE, PRICE_PRIORITY")
    priority: int = Field(
        8, description="Priority level (1-16, lower number = higher priority). Standard is 8, Deals are 4-6"
    )

    # Delivery settings
    creative_rotation_type: str = Field(
        "EVEN", description="How to rotate creatives: EVEN, OPTIMIZED, MANUAL, SEQUENTIAL"
    )
    delivery_rate_type: str = Field("EVENLY", description="Delivery pacing: EVENLY, FRONTLOADED, AS_FAST_AS_POSSIBLE")

    # Pricing and goals
    cost_type: str = Field("CPM", description="Pricing model: CPM, CPC, CPD, CPA")
    discount_type: str | None = Field(None, description="Discount type if applicable: PERCENTAGE, ABSOLUTE_VALUE")
    discount_value: float | None = Field(
        None, description="Discount value (percentage or absolute based on discount_type)"
    )

    primary_goal_type: str = Field("LIFETIME", description="Goal type: LIFETIME, DAILY, NONE")
    primary_goal_unit_type: str = Field(
        "IMPRESSIONS", description="Goal unit: IMPRESSIONS, CLICKS, VIEWABLE_IMPRESSIONS"
    )

    # Creative specifications
    creative_placeholders: list[CreativePlaceholder] = Field(
        ..., description="Expected creative sizes and specifications"
    )

    # Ad unit/placement targeting
    targeted_ad_unit_ids: list[str] = Field(default_factory=list, description="Specific GAM ad unit IDs to target")
    excluded_ad_unit_ids: list[str] = Field(default_factory=list, description="GAM ad unit IDs to exclude")
    targeted_placement_ids: list[str] = Field(default_factory=list, description="GAM placement IDs to target")
    include_descendants: bool = Field(True, description="Include child ad units in targeting")

    # Frequency capping
    frequency_caps: list[FrequencyCap] = Field(default_factory=list, description="Frequency capping rules")

    # Competition and exclusions
    competitive_exclusion_labels: list[str] = Field(
        default_factory=list, description="Labels to prevent competitive ads from serving together"
    )

    # Video-specific settings (optional)
    environment_type: str = Field("BROWSER", description="Environment: BROWSER or VIDEO_PLAYER")
    companion_delivery_option: str | None = Field(None, description="For video: OPTIONAL, AT_LEAST_ONE, ALL")
    video_max_duration: int | None = Field(None, description="Maximum video duration in milliseconds")
    skip_offset: int | None = Field(None, description="When skip button appears (milliseconds from start)")

    # Advanced settings
    disable_viewability_avg_revenue_optimization: bool = Field(
        False, description="Disable viewability-based optimization"
    )
    allow_overbook: bool = Field(False, description="Allow overbooking of inventory")
    skip_inventory_check: bool = Field(False, description="Skip inventory availability check")

    # Custom targeting template
    custom_targeting_keys: dict[str, Any] = Field(
        default_factory=dict, description="Custom key-value pairs for targeting"
    )

    # Native ad settings
    native_style_id: str | None = Field(
        None,
        description="GAM creative template id (TemplateCreative.creativeTemplateId) that native creatives of this product are built against",
    )

    # Creative-level placement targeting
    placement_targeting: list[PlacementTargeting] = Field(
        default_factory=list,
        description="Creative-level targeting for placements. Maps placement_ids to GAM targeting rules.",
    )

    # Automation settings for non-guaranteed orders
    non_guaranteed_automation: str = Field(
        "manual",
        description="Automation mode for non-guaranteed line item types: 'automatic' (instant activation), 'confirmation_required' (human approval then auto-activation), 'manual' (human handles all steps)",
    )

    @field_validator("line_item_type")
    def validate_line_item_type(cls, v):
        valid_types = {"STANDARD", "SPONSORSHIP", "NETWORK", "HOUSE", "PRICE_PRIORITY"}
        if v not in valid_types:
            raise ValueError(f"Invalid line_item_type. Must be one of: {valid_types}")
        return v

    @field_validator("priority")
    def validate_priority(cls, v):
        if not 1 <= v <= 16:
            raise ValueError("Priority must be between 1 and 16")
        return v

    @field_validator("cost_type")
    def validate_cost_type(cls, v):
        valid_types = {"CPM", "CPC", "CPD", "CPA"}
        if v not in valid_types:
            raise ValueError(f"Invalid cost_type. Must be one of: {valid_types}")
        return v

    @field_validator("non_guaranteed_automation")
    def validate_non_guaranteed_automation(cls, v):
        valid_modes = {"automatic", "confirmation_required", "manual"}
        if v not in valid_modes:
            raise ValueError(f"Invalid non_guaranteed_automation. Must be one of: {valid_modes}")
        return v


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
    "video_max_duration": 30000,  # 30 seconds
    "skip_offset": 5000,  # Skip after 5 seconds
    "companion_delivery_option": "OPTIONAL",
    "include_descendants": True,
}
