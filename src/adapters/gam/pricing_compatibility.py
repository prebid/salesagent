"""
GAM Pricing Model and Line Item Type Compatibility

This module defines the compatibility between AdCP pricing models and GAM line item types,
based on the official Google Ad Manager API specifications.

Key concepts:
- AdCP pricing models: cpm, vcpm, cpc, flat_rate (exposed to clients)
- GAM cost types: CPM, VCPM, CPC, CPD (used by GAM API)
- CPD is NOT an AdCP pricing model - it's used internally to translate FLAT_RATE

Source: https://developers.google.com/ad-manager/api/reference/ForecastService.CostType
"""

import logging
from collections.abc import Mapping
from typing import Any, Literal, NamedTuple

from src.core.errors.details import CapabilityRefusalDetails
from src.core.exceptions import AdCPCapabilityNotSupportedError

logger = logging.getLogger(__name__)

# Type aliases for clarity
PricingModel = Literal["cpm", "vcpm", "cpc", "flat_rate"]  # AdCP pricing models only
GAMCostType = Literal["CPM", "VCPM", "CPC", "CPD"]  # GAM internal types
LineItemType = Literal["STANDARD", "SPONSORSHIP", "NETWORK", "PRICE_PRIORITY", "BULK", "HOUSE"]

#: The types that take only a DAILY goal whose units are a percentage of impressions
#: (Goal.goalType, Goal.units), not a count.
SHARE_OF_VOICE_TYPES = frozenset({"SPONSORSHIP", "NETWORK", "HOUSE"})

#: The goal types GAM accepts on each count type (Goal.goalType); the first is the type's
#: goal when the product configures none.
GOAL_TYPES: dict[str, tuple[str, ...]] = {
    "STANDARD": ("LIFETIME",),
    "BULK": ("LIFETIME",),
    "PRICE_PRIORITY": ("LIFETIME", "DAILY", "NONE"),
}


class LineItemPlan(NamedTuple):
    """How one package is booked in GAM."""

    line_item_type: LineItemType
    priority: int
    goal_type: str
    unit_type: str


class PricingCompatibility:
    """Defines compatibility between pricing models and line item types.

    Note: CPD is a GAM cost type but NOT an AdCP pricing model. We use it internally
    to translate FLAT_RATE pricing (total campaign cost / days = CPD rate).
    """

    # Official GAM compatibility matrix (what GAM API supports)
    # Source: GAM API LineItem and CostType specifications
    COMPATIBILITY_MATRIX = {
        "STANDARD": {"CPM", "CPC", "VCPM", "CPM_IN_TARGET"},
        "SPONSORSHIP": {"CPM", "CPC", "CPD"},
        "NETWORK": {"CPM", "CPC", "CPD"},
        "PRICE_PRIORITY": {"CPM", "CPC"},
        "BULK": {"CPM"},
        "HOUSE": {"CPM"},
    }

    # AdCP to GAM cost type mapping (internal translation)
    ADCP_TO_GAM_COST_TYPE = {
        "cpm": "CPM",
        "vcpm": "VCPM",
        "cpc": "CPC",
        "flat_rate": "CPD",  # Internal: Translate FLAT_RATE to CPD (total / days)
    }

    @classmethod
    def is_compatible(cls, line_item_type: LineItemType, pricing_model: PricingModel) -> bool:
        """Check if pricing model is compatible with line item type.

        Args:
            line_item_type: GAM line item type
            pricing_model: AdCP pricing model

        Returns:
            True if compatible, False otherwise
        """
        gam_cost_type = cls.ADCP_TO_GAM_COST_TYPE.get(pricing_model)
        if not gam_cost_type:
            return False
        return gam_cost_type in cls.COMPATIBILITY_MATRIX.get(line_item_type, set())

    @classmethod
    def get_compatible_line_item_types(cls, pricing_model: PricingModel) -> set[str]:
        """Get all line item types compatible with pricing model.

        Args:
            pricing_model: AdCP pricing model

        Returns:
            Set of compatible line item types
        """
        gam_cost_type = cls.ADCP_TO_GAM_COST_TYPE.get(pricing_model)
        if not gam_cost_type:
            return set()

        compatible: set[str] = set()
        for line_item_type, cost_types in cls.COMPATIBILITY_MATRIX.items():
            if gam_cost_type in cost_types:
                compatible.add(line_item_type)
        return compatible

    @classmethod
    def select_line_item_type(
        cls, pricing_model: PricingModel, is_guaranteed: bool = False, override_type: LineItemType | None = None
    ) -> LineItemType:
        """Select the line item type a package is booked as.

        ``override_type`` (the product's configured type) is used when it can book the
        package: GAM supports the package's pricing model on it, and a share-of-voice type
        (SPONSORSHIP, NETWORK, HOUSE) is used only for a flat-rate package. Otherwise the
        type is auto-selected from the pricing model and the product's delivery guarantee.

        Two reasons an override falls back rather than refusing. The configuration belongs
        to the product and the pricing model to the package, and a product may offer
        options its configured type cannot book. And a share-of-voice type takes only a
        DAILY goal of a percentage of impressions: that bounds a flat-rate package, whose
        price is fixed, but not a CPM, vCPM or CPC package, whose budget would reach GAM in
        no form (AdCP 3.1.1 pricing-models.mdx: the budget is the buyer's cost).

        Args:
            pricing_model: AdCP pricing model (cpm, vcpm, cpc, flat_rate)
            is_guaranteed: Whether campaign requires guaranteed delivery
            override_type: Optional explicit line item type from product config

        Returns:
            The line item type to book
        """
        if (
            override_type
            and cls.is_compatible(override_type, pricing_model)
            and (override_type not in SHARE_OF_VOICE_TYPES or pricing_model == "flat_rate")
        ):
            return override_type

        # Decision tree for automatic selection
        if pricing_model == "flat_rate":
            # FLAT_RATE → CPD (Cost Per Day) - GAM's native flat fee pricing model
            # Using SPONSORSHIP because:
            # - SPONSORSHIP supports CPD cost type natively
            # - CPD means advertiser pays flat fee per day regardless of impressions/clicks
            # - SPONSORSHIP uses percentage-based DAILY goals (100% to serve on all matching requests)
            # - This matches the semantic intent of "flat rate" - fixed cost per day
            return "SPONSORSHIP"  # FLAT_RATE → CPD, SPONSORSHIP supports CPD natively

        if pricing_model == "vcpm":
            return "STANDARD"  # VCPM only works with STANDARD in GAM

        if is_guaranteed:
            return "STANDARD"  # Guaranteed delivery

        # Default for CPC/CPM non-guaranteed
        return "PRICE_PRIORITY"

    @classmethod
    def plan_line_item(
        cls,
        pricing_model: PricingModel,
        *,
        is_guaranteed: bool,
        implementation_config: Mapping[str, Any],
        product_id: str,
    ) -> LineItemPlan:
        """Decide a package's line item type, priority, goal type and unit type, in that order.

        The one reader of the product's ``line_item_type``, ``primary_goal_type`` and
        ``primary_goal_unit_type``.

        1. Type: ``select_line_item_type`` with the configured type as the override.
        2. Priority: the booked type's default (``get_default_priority``).
        3. Goal type: the configured ``primary_goal_type`` when GAM accepts it on the booked
           type (DAILY on a share-of-voice type, ``GOAL_TYPES`` otherwise); otherwise the
           type's goal.
        4. Unit type: IMPRESSIONS on a share-of-voice type; CLICKS for CPC; VIEWABLE_IMPRESSIONS
           for vCPM; otherwise the configured ``primary_goal_unit_type``, IMPRESSIONS when none.
        """
        configured_type = implementation_config.get("line_item_type")
        line_item_type = cls.select_line_item_type(pricing_model, is_guaranteed, configured_type)
        if configured_type and configured_type != line_item_type:
            logger.info(
                "Product %s configures line item type %s, which cannot book a %s package; booking %s",
                product_id,
                configured_type,
                pricing_model,
                line_item_type,
            )

        accepted_goal_types = ("DAILY",) if line_item_type in SHARE_OF_VOICE_TYPES else GOAL_TYPES[line_item_type]
        configured_goal_type = implementation_config.get("primary_goal_type")
        goal_type = configured_goal_type if configured_goal_type in accepted_goal_types else accepted_goal_types[0]
        if configured_goal_type and configured_goal_type != goal_type:
            logger.info(
                "Product %s configures goal type %s, which GAM does not accept on %s; booking %s",
                product_id,
                configured_goal_type,
                line_item_type,
                goal_type,
            )

        if line_item_type in SHARE_OF_VOICE_TYPES:
            unit_type = "IMPRESSIONS"
        elif pricing_model == "cpc":
            unit_type = "CLICKS"
        elif pricing_model == "vcpm":
            unit_type = "VIEWABLE_IMPRESSIONS"
        else:
            unit_type = implementation_config.get("primary_goal_unit_type", "IMPRESSIONS")

        return LineItemPlan(line_item_type, cls.get_default_priority(line_item_type), goal_type, unit_type)

    @classmethod
    def goal_units(cls, plan: LineItemPlan, *, lifetime_units: int, flight_days: int) -> int:
        """The goal units for a planned line item, from the package's lifetime volume.

        A share-of-voice type books 100 (percent of impressions); ``select_line_item_type``
        books those types only for flat rate, whose price is fixed. A DAILY goal on a count
        type is the lifetime volume spread over the flight's days. Otherwise (LIFETIME, NONE)
        the lifetime volume. It converts a volume it is given; it computes none.
        """
        if plan.line_item_type in SHARE_OF_VOICE_TYPES:
            return 100
        if plan.goal_type == "DAILY":
            return lifetime_units // flight_days
        return lifetime_units

    @classmethod
    def get_gam_cost_type(cls, pricing_model: PricingModel) -> str:
        """Convert AdCP pricing model to GAM cost type.

        Args:
            pricing_model: AdCP pricing model

        Returns:
            GAM cost type (CPM, VCPM, CPC, or CPD)

        Raises:
            AdCPCapabilityNotSupportedError: If pricing model not supported —
                a buyer-correctable capability gap (UNSUPPORTED_FEATURE /
                correctable per the pinned spec), never a bare ValueError,
                which generic handlers wrap into SERVICE_UNAVAILABLE/transient.
        """
        cost_type = cls.ADCP_TO_GAM_COST_TYPE.get(pricing_model)
        if not cost_type:
            raise AdCPCapabilityNotSupportedError(
                details=CapabilityRefusalDetails(capability="pricing_model", rejected_value=pricing_model)
            )
        return cost_type

    @classmethod
    def get_default_priority(cls, line_item_type: LineItemType) -> int:
        """Get default priority for line item type (GAM best practices).

        Args:
            line_item_type: GAM line item type

        Returns:
            Default priority level (1-16, lower = higher priority)
        """
        priorities = {
            "SPONSORSHIP": 4,
            "STANDARD": 8,
            "PRICE_PRIORITY": 12,
            "BULK": 12,
            "NETWORK": 12,
            "HOUSE": 16,
        }
        return priorities.get(line_item_type, 8)
