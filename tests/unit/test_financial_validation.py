"""Unit tests for shared financial validation helpers."""

from decimal import Decimal

import pytest

from src.core.errors.details import BudgetDetails
from src.core.exceptions import AdCPBudgetTooLowError
from src.core.tools.financial_validation import (
    raise_if_validation_failed,
    validate_max_campaign_budget,
    validate_max_daily_package_spend,
    validate_min_package_budget,
)


def test_validate_max_campaign_budget_rejects_above_limit() -> None:
    error = validate_max_campaign_budget(
        campaign_budget=Decimal("10000001"),
        max_campaign_budget=Decimal("10000000"),
        currency="USD",
    )

    assert error is not None
    assert "maximum allowed campaign budget" in error


def test_validate_max_campaign_budget_accepts_equal_limit() -> None:
    error = validate_max_campaign_budget(
        campaign_budget=Decimal("10000000"),
        max_campaign_budget=Decimal("10000000"),
        currency="USD",
    )

    assert error is None


def test_validate_min_package_budget_rejects_below_minimum() -> None:
    error = validate_min_package_budget(
        package_budget=Decimal("99"),
        min_package_budget=Decimal("100"),
        currency="EUR",
    )

    assert error is not None
    assert "minimum spend requirement" in error


def test_validate_min_package_budget_accepts_equal_minimum() -> None:
    error = validate_min_package_budget(
        package_budget=Decimal("100"),
        min_package_budget=Decimal("100"),
        currency="EUR",
    )

    assert error is None


def test_validate_max_daily_package_spend_rejects_above_limit() -> None:
    error = validate_max_daily_package_spend(
        package_budget=Decimal("3100"),
        flight_days=3,
        max_daily_spend=Decimal("1000"),
        currency="USD",
    )

    assert error is not None
    assert "exceeds maximum" in error


def test_validate_max_daily_package_spend_accepts_equal_limit() -> None:
    error = validate_max_daily_package_spend(
        package_budget=Decimal("3000"),
        flight_days=3,
        max_daily_spend=Decimal("1000"),
        currency="USD",
    )

    assert error is None


# ---------------------------------------------------------------------------
# Context/subject/limit_label parameters
# ---------------------------------------------------------------------------


def test_validate_min_package_budget_subject_overrides_prefix() -> None:
    error = validate_min_package_budget(
        package_budget=Decimal("99"),
        min_package_budget=Decimal("100"),
        currency="USD",
        subject="Total",
    )

    assert error is not None
    assert error.startswith("Total budget")
    assert "Package budget" not in error


def test_validate_min_package_budget_trailer_overrides_trailing_sentence() -> None:
    error = validate_min_package_budget(
        package_budget=Decimal("99"),
        min_package_budget=Decimal("100"),
        currency="USD",
        trailer="for products in this package",
    )

    assert error is not None
    assert "for products in this package" in error
    assert "The same minimum applies" not in error


def test_validate_max_daily_package_spend_subject_overrides_prefix() -> None:
    error = validate_max_daily_package_spend(
        package_budget=Decimal("3100"),
        flight_days=3,
        max_daily_spend=Decimal("1000"),
        currency="USD",
        subject="Daily",
    )

    assert error is not None
    assert "Daily budget" in error
    assert "Package daily budget" not in error


def test_validate_max_daily_package_spend_limit_label_overrides_limit_text() -> None:
    error = validate_max_daily_package_spend(
        package_budget=Decimal("3100"),
        flight_days=3,
        max_daily_spend=Decimal("1000"),
        currency="USD",
        limit_label="maximum daily spend per package",
    )

    assert error is not None
    assert "exceeds maximum daily spend per package" in error


def test_validate_max_daily_package_spend_trailer_overrides_trailing_sentence() -> None:
    error = validate_max_daily_package_spend(
        package_budget=Decimal("3100"),
        flight_days=3,
        max_daily_spend=Decimal("1000"),
        currency="USD",
        trailer="This protects against accidental large budgets.",
    )

    assert error is not None
    assert "This protects against accidental large budgets." in error
    assert "Flight date changes" not in error


def test_raise_if_validation_failed_is_noop_on_empty_message() -> None:
    # None and "" are both no-ops — there is no failure to raise.
    for reason in (None, ""):
        assert (
            raise_if_validation_failed(
                reason,
                exc_type=AdCPBudgetTooLowError,
                requested_budget=Decimal("1"),
                budget_limit=Decimal("100"),
            )
            is None
        )


def test_raise_if_validation_failed_carries_the_two_numbers_that_decided_it() -> None:
    """The details block is a declared class holding the comparison, not a dict.

    What the wire does with this is BDD's (the daily-spend-cap outlines on a2a/mcp/rest);
    what is graded here is the helper's own contract, because the dict it used to build
    had no ``to_wire`` and killed the boundary's failure builder mid-render.

    There is no default-``exc_type`` case to grade any more. The default was
    ``type[AdCPSalesAgentError]``, which binds ``DetailsT`` to ``Any``, and ``Any`` is what
    let the dict through — so the parameter is required and every call site names its class.
    The former test asserted ``error_code == "VALIDATION_ERROR"`` off that default; the code
    is a ClassVar resolved through CODE_TABLE, so it only ever compared the table to itself.
    """
    with pytest.raises(AdCPBudgetTooLowError) as exc_info:
        raise_if_validation_failed(
            "package below minimum spend",
            exc_type=AdCPBudgetTooLowError,
            requested_budget=Decimal("50"),
            budget_limit=Decimal("100"),
        )

    assert exc_info.value.details == BudgetDetails(requested_budget="50", budget_limit="100")
