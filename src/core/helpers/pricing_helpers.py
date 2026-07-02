"""Pricing option helper utilities.

Handles the RootModel wrapper pattern used by adcp 2.14.0+ for discriminated unions.
"""

from typing import Any


def pricing_option_has_rate(pricing_option: Any) -> bool:
    """Check if a pricing option exposes buyer-visible pricing.

    Handles multiple formats:
    - Dict format (from JSON/serialization)
    - Pydantic RootModel wrapper (adcp 2.14.0+)
    - Direct attribute access (SQLAlchemy models)

    AdCP v2 used ``rate``. AdCP v3 renamed that value to ``fixed_price`` for
    fixed pricing and ``floor_price`` for auction floors, so all three fields
    indicate that pricing is present.

    Args:
        pricing_option: A pricing option in any supported format

    Returns:
        True if the pricing option has a non-None pricing value.
    """
    price_fields = ("rate", "fixed_price", "floor_price")

    # Dict format (JSON/serialization)
    if isinstance(pricing_option, dict):
        return any(pricing_option.get(field) is not None for field in price_fields)

    # Try RootModel wrapper first (adcp 2.14.0+ Pydantic models)
    root = getattr(pricing_option, "root", None)
    if root is not None:
        return any(getattr(root, field, None) is not None for field in price_fields)

    # Direct attribute (SQLAlchemy models or plain objects)
    return any(getattr(pricing_option, field, None) is not None for field in price_fields)
