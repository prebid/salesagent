"""Unit tests for shared financial validation helpers."""

from decimal import Decimal

from src.core.tools.financial_validation import (
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
