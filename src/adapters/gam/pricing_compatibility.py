"""
GAM Pricing Model and Line Item Type Compatibility

This module defines the compatibility between AdCP pricing models and GAM line item types,
based on the official Google Ad Manager API v202411 specifications.

Key concepts:
- AdCP pricing models: cpm, vcpm, cpc, flat_rate (exposed to clients)
- GAM cost types: CPM, VCPM, CPC, CPD (used by GAM API)
- CPD is NOT an AdCP pricing model - it's used internally to translate FLAT_RATE

Source: https://developers.google.com/ad-manager/api/reference/v202411/ForecastService.CostType
"""

from typing import Literal

# Type aliases for clarity
PricingModel = Literal["cpm", "vcpm", "cpc", "flat_rate"]  # AdCP pricing models only
GAMCostType = Literal["CPM", "VCPM", "CPC", "CPD"]  # GAM internal types
LineItemType = Literal["STANDARD", "SPONSORSHIP", "NETWORK", "PRICE_PRIORITY", "BULK", "HOUSE"]


class PricingCompatibility:
    """Defines compatibility between pricing models and line item types.

    Note: CPD is a GAM cost type but NOT an AdCP pricing model. We use it internally
    to translate FLAT_RATE pricing (total campaign cost / days = CPD rate).
    """

    # Official GAM compatibility matrix (what GAM API supports)
    # Source: GAM API v202411 LineItem and CostType specifications
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
        """Select appropriate line item type based on campaign characteristics.

        Args:
            pricing_model: AdCP pricing model (cpm, vcpm, cpc, flat_rate)
            is_guaranteed: Whether campaign requires guaranteed delivery
            override_type: Optional explicit line item type from product config

        Returns:
            Recommended line item type

        Raises:
            ValueError: If override_type is incompatible with pricing_model
        """
        # Validate override if provided
        if override_type:
            if not cls.is_compatible(override_type, pricing_model):
                compatible = cls.get_compatible_line_item_types(pricing_model)
                raise ValueError(
                    f"Line item type '{override_type}' is not compatible with pricing model '{pricing_model}'. "
                    f"GAM supports {pricing_model.upper()} with: {', '.join(sorted(compatible))}"
                )
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
    def get_gam_cost_type(cls, pricing_model: PricingModel) -> str:
        """Convert AdCP pricing model to GAM cost type.

        Args:
            pricing_model: AdCP pricing model

        Returns:
            GAM cost type (CPM, VCPM, CPC, or CPD)

        Raises:
            ValueError: If pricing model not supported
        """
        cost_type = cls.ADCP_TO_GAM_COST_TYPE.get(pricing_model)
        if not cost_type:
            raise ValueError(f"Pricing model '{pricing_model}' not supported by GAM adapter")
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
            "NETWORK": 16,
            "HOUSE": 16,
        }
        return priorities.get(line_item_type, 8)
