"""Shared financial validation for media buy create and update paths.

This module has no transport or session awareness. It operates on plain
values and returns validation results so create/update paths can share
the same policy checks without duplicating comparison logic.
"""

import logging
from decimal import Decimal

from src.core.errors.details import BudgetDetails
from src.core.exceptions import AdCPSalesAgentError

logger = logging.getLogger(__name__)


def raise_if_validation_failed(
    reason: str | None,
    *,
    exc_type: type[AdCPSalesAgentError[BudgetDetails]],
    requested_budget: Decimal,
    budget_limit: Decimal,
) -> None:
    """Raise ``exc_type`` with the two numbers that decided it, when ``reason`` is non-empty.

    Shared one-liner so the budget ``validate_*`` call sites in the create and
    update media-buy paths express their failure path uniformly. Each site
    selects the spec-specific subclass — ``AdCPBudgetTooLowError`` for
    minimum-spend shortfalls, ``AdCPBudgetExceededError`` for daily-spend
    ceilings — so the wire code reflects the failure kind.

    ``exc_type`` is PARAMETERIZED, and that is the point of this signature. It was
    ``type[AdCPSalesAgentError]`` with a default, which binds ``DetailsT`` to ``Any``, and
    ``Any`` accepted the ``details={"reason": reason}`` this used to raise. A dict has no
    ``to_wire``, so the boundary's failure builder raised while rendering the envelope —
    inside the ``except Exception`` that would have wrapped it — and a BUDGET_EXCEEDED /
    correctable / 422 reached the buyer as INTERNAL_ERROR / transient / 500, telling them to
    retry a request that can never succeed. The default went with it: all six call sites
    name their class, and a default that types as ``Any`` is how the hole stayed open.

    ``reason`` is the validator's first-party diagnostic and is LOGGED, not carried. It is an
    authored sentence naming which of several limits was hit, and an authored sentence has no
    slot: buyer-facing text comes from ``CODE_TABLE`` via the read-only ``message`` property,
    and ``internal_detail`` takes an exception rather than a string. The two numbers are the
    machine-readable half, and ``BudgetDetails`` is where they already go — the GAM adapter
    fills the same class.
    """
    if reason:
        logger.warning("Budget validation refused the request: %s", reason)
        raise exc_type(details=BudgetDetails(requested_budget=str(requested_budget), budget_limit=str(budget_limit)))


def validate_budget_positive(
    budget: Decimal,
    field: str = "budget",
) -> str | None:
    """Check that a budget value is strictly positive.

    Returns an error message if budget <= 0, or None if valid.
    Used by both create_media_buy and update_media_buy paths.
    """
    if budget <= 0:
        return (
            f"Budget must be a positive amount (got {budget} on {field}). Set each package budget to a positive amount."
        )
    return None


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
    trailer: str = "The same minimum applies to updates as to creation.",
) -> str | None:
    """Check that a package budget meets the minimum spend requirement.

    Args:
        subject: Label for the budget kind, e.g. "Package" or "Total".
        trailer: Trailing sentence that varies by call site (create vs update path).

    Returns:
        An error message string if validation fails, or None if the budget is acceptable.
    """
    if package_budget < min_package_budget:
        return (
            f"{subject} budget ({package_budget} {currency}) does not meet the minimum spend "
            f"requirement ({min_package_budget} {currency}). "
            f"{trailer}"
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
    trailer: str = "Flight date changes that reduce daily budget are not allowed to bypass limits.",
) -> str | None:
    """Check that a package's daily spend does not exceed the limit.

    Args:
        subject: Full noun phrase for the budget kind, e.g. "Package daily" or "Daily".
                 Combined with " budget" to form the message prefix.
        limit_label: Description of the limit, e.g. "maximum daily spend per package".
        trailer: Trailing sentence that varies by call site (create vs update path).

    Returns:
        An error message string if validation fails, or None if within limits.
    """
    if flight_days <= 0:
        flight_days = 1
    daily = package_budget / Decimal(str(flight_days))
    if daily > max_daily_spend:
        return f"{subject} budget ({daily} {currency}) exceeds {limit_label} ({max_daily_spend} {currency}). {trailer}"
    return None
