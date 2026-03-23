"""Shared financial validation for media buy create and update paths.

This module has no transport or session awareness. It operates on plain
values and returns validation results so create/update paths can share
the same policy checks without duplicating comparison logic.
"""

from decimal import Decimal


def validate_max_campaign_budget(
    *,
    campaign_budget: Decimal,
    max_campaign_budget: Decimal,
    currency: str,
) -> str | None:
    """Check that the campaign budget does not exceed the configured ceiling."""
    if campaign_budget > max_campaign_budget:
        return (
            f"Budget {campaign_budget} {currency} exceeds the maximum allowed campaign budget "
            f"({max_campaign_budget} {currency}). "
            "Contact your publisher representative if you require a higher limit."
        )
    return None


def validate_min_package_budget(
    *,
    package_budget: Decimal,
    min_package_budget: Decimal,
    currency: str,
    subject: str = "Package",
    context: str = "The same minimum applies to updates as to creation.",
) -> str | None:
    """Check that a package budget meets the minimum spend requirement.

    Args:
        subject: Label for the budget kind, e.g. "Package" or "Total".
        context: Trailing sentence that varies by call site (create vs update path).

    Returns:
        An error message string if validation fails, or None if the budget is acceptable.
    """
    if package_budget < min_package_budget:
        return (
            f"{subject} budget ({package_budget} {currency}) does not meet the minimum spend "
            f"requirement ({min_package_budget} {currency}). "
            f"{context}"
        )
    return None


def validate_max_daily_package_spend(
    *,
    package_budget: Decimal,
    flight_days: int,
    max_daily_spend: Decimal,
    currency: str,
    subject: str = "Package daily",
    limit_label: str = "maximum",
    context: str = "Flight date changes that reduce daily budget are not allowed to bypass limits.",
) -> str | None:
    """Check that a package's daily spend does not exceed the limit.

    Args:
        subject: Full noun phrase for the budget kind, e.g. "Package daily" or "Daily".
                 Combined with " budget" to form the message prefix.
        limit_label: Description of the limit, e.g. "maximum daily spend per package".
        context: Trailing sentence that varies by call site (create vs update path).

    Returns:
        An error message string if validation fails, or None if within limits.
    """
    if flight_days <= 0:
        flight_days = 1
    daily = package_budget / Decimal(str(flight_days))
    if daily > max_daily_spend:
        return f"{subject} budget ({daily} {currency}) exceeds {limit_label} ({max_daily_spend} {currency}). {context}"
    return None
