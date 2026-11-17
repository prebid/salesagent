"""
Google Ad Manager Implementation Config Schema

This module defines the structure of implementation_config for GAM products.
Based on analysis of Prebid Line Item Manager patterns.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    TypeAdapter,
    field_serializer,
    field_validator,
)


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


#: A custom targeting value as GAM targets it: an ``int`` is a GAM value ID, a ``str`` is a
#: value name the adapter looks up (or creates). Strict, so a string of digits never becomes an
#: ID by coercion; the stored format decides which one a value is.
CustomTargetingValue = StrictInt | StrictStr


class CustomCriterion(BaseModel):
    """One GAM ``CustomCriteria``: the key is one of ``values``, or with ``exclude`` none of them."""

    model_config = ConfigDict(frozen=True)

    key_id: StrictInt
    values: tuple[CustomTargetingValue, ...] = Field(min_length=1)
    exclude: bool = False


class CustomTargeting(BaseModel):
    """GAM custom targeting: an OR of branches, each an AND of criteria.

    This is the one shape GAM takes. ``LineItemService.Targeting.customTargeting`` (GAM API
    v202602, the version the pinned googleads 49.0.0 calls) is at most three levels deep: an
    OR set of AND sets of ``CustomCriteria``. No branches means no custom targeting.

    A product's ``custom_targeting_keys`` is stored in one of three formats, all read by
    ``from_stored``; ``to_stored`` writes the groups one, which the targeting widget edits.
    Buyer key-values and AXE segments are the legacy one, ``from_key_values``.
    """

    model_config = ConfigDict(frozen=True)

    branches: tuple[Annotated[tuple[CustomCriterion, ...], Field(min_length=1)], ...] = ()

    def __and__(self, other: CustomTargeting) -> CustomTargeting:
        """Targeting that matches only where both do.

        ``(A1 OR A2) AND B`` is ``(A1 AND B) OR (A2 AND B)``, so n branches ANDed with m
        become n*m.
        """
        if not self.branches:
            return other
        if not other.branches:
            return self
        return CustomTargeting(branches=tuple(mine + theirs for mine in self.branches for theirs in other.branches))

    @classmethod
    def from_stored(cls, stored: object) -> CustomTargeting:
        """Read a product's ``custom_targeting_keys`` in any of its three stored formats.

        - groups, what the targeting widget saves:
          ``{"groups": [{"criteria": [{"keyId": ..., "values": [...], "exclude": bool}]}]}``,
          the groups ORed and the criteria in a group ANDed;
        - include/exclude: ``{"include": {key_id: [values]}, "exclude": {...}, "operator": "AND" | "OR"}``;
        - legacy key-values, as ``from_key_values`` reads them.

        In the first two, a value of digits is a GAM value ID and anything else a value name.
        In groups, ``{"name": ...}`` is a value name whatever it is spelled with; that is how
        ``to_stored`` keeps a name of digits a name. A criterion with no values, and a group
        with no criteria, is left out: the widget saves those while a row is still being
        filled in. Anything else malformed, an unknown key included, raises
        ``pydantic.ValidationError``, as does a document that is not an object.
        """
        stored = _STORED_DOCUMENT.validate_python(stored)
        if "groups" in stored:
            groups = _StoredGroups.model_validate(stored).groups
            return cls(
                branches=tuple(
                    branch
                    for group in groups
                    if (branch := tuple(_criterion(c.key_id, c.values, c.exclude) for c in group.criteria if c.values))
                )
            )
        if "include" in stored or "exclude" in stored:
            config = _StoredIncludeExclude.model_validate(stored)
            criteria = [_criterion(key_id, values, False) for key_id, values in config.include.items() if values]
            criteria += [_criterion(key_id, values, True) for key_id, values in config.exclude.items() if values]
            if config.operator == "OR":
                return cls(branches=tuple((criterion,) for criterion in criteria))
            return cls(branches=(tuple(criteria),) if criteria else ())
        return cls.from_key_values(stored)

    @classmethod
    def from_key_values(cls, key_values: dict[str, Any]) -> CustomTargeting:
        """Read legacy key-values: ``{key_id: value_name}``, a ``NOT_<key_id>`` key excluding.

        Every key is ANDed in one branch, and every value is a value name, even one of digits.
        """
        names = _LEGACY_KEY_VALUES.validate_python(key_values)
        criteria = tuple(
            CustomCriterion(key_id=_gam_id(key.removeprefix("NOT_")), values=(name,), exclude=key.startswith("NOT_"))
            for key, name in names.items()
        )
        return cls(branches=(criteria,) if criteria else ())

    def to_stored(self) -> dict[str, Any]:
        """This targeting in the groups format, which ``from_stored`` reads back unchanged.

        Key and value IDs are written as strings of digits, the way the widget writes the IDs
        it is given; a value name is written ``{"name": ...}``, so a name of digits stays a name.
        """
        return {
            "groups": [
                {
                    "criteria": [
                        {
                            "keyId": str(criterion.key_id),
                            "values": [str(v) if isinstance(v, int) else {"name": v} for v in criterion.values],
                            "exclude": criterion.exclude,
                        }
                        for criterion in branch
                    ]
                }
                for branch in self.branches
            ]
        }


def _gam_id(id_or_name: str | int) -> int | str:
    """A string of digits as the int GAM ID it spells; anything else unchanged."""
    if isinstance(id_or_name, str) and id_or_name.isascii() and id_or_name.isdecimal():
        return int(id_or_name)
    return id_or_name


def _criterion(key_id: str | int, values: Sequence[_StoredValue], exclude: bool) -> CustomCriterion:
    """A criterion from the groups or include/exclude format, where a value of digits is a GAM value ID."""
    return CustomCriterion(
        key_id=_gam_id(key_id),
        values=tuple(v.name if isinstance(v, _StoredName) else _gam_id(v) for v in values),
        exclude=exclude,
    )


class _Stored(BaseModel):
    """A stored format refuses a key it does not know, rather than serve without it."""

    model_config = ConfigDict(extra="forbid")


class _StoredName(_Stored):
    name: Annotated[StrictStr, Field(min_length=1)]


_StoredValue = StrictInt | StrictStr | _StoredName


class _StoredCriterion(_Stored):
    key_id: StrictStr | StrictInt = Field(alias="keyId")
    values: list[_StoredValue] = Field(default_factory=list)
    exclude: StrictBool = False


class _StoredGroup(_Stored):
    criteria: list[_StoredCriterion] = Field(default_factory=list)


class _StoredGroups(_Stored):
    groups: list[_StoredGroup]


class _StoredIncludeExclude(_Stored):
    include: dict[str, list[CustomTargetingValue]] = Field(default_factory=dict)
    exclude: dict[str, list[CustomTargetingValue]] = Field(default_factory=dict)
    operator: Literal["AND", "OR"] = "AND"


_STORED_DOCUMENT = TypeAdapter(dict[str, Any])
_LEGACY_KEY_VALUES = TypeAdapter(dict[str, Annotated[StrictStr, Field(min_length=1)]])


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
    custom_targeting_keys: CustomTargeting = Field(
        default_factory=CustomTargeting,
        description=(
            "Custom targeting every line item must match, ANDed with the buyer's. Stored in the "
            "groups format the targeting widget saves, or the older legacy or include/exclude format"
        ),
    )

    # Native ad settings
    native_style_id: str | None = Field(None, description="GAM native style ID if using native ads")

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

    @field_validator("custom_targeting_keys", mode="before")
    @classmethod
    def read_stored_custom_targeting(cls, stored: object) -> CustomTargeting:
        """Stored rows keep their format; all three are read into the model."""
        return stored if isinstance(stored, CustomTargeting) else CustomTargeting.from_stored(stored)

    @field_serializer("custom_targeting_keys")
    def write_stored_custom_targeting(self, custom_targeting: CustomTargeting) -> dict[str, Any]:
        """Written in the groups format, so a dumped config reads back as the same config."""
        return custom_targeting.to_stored()

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
