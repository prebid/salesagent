"""Unit tests for PR04 financial guardrails: F-05, F-07, F-08.

F-05 — Budget ceiling: updates exceeding MAX_CAMPAIGN_BUDGET are rejected.
F-07 — Currency preservation: float-only budget updates use existing DB currency.
F-08 — Min-spend parity: package budget updates honor currency_limit.min_package_budget.
"""

from decimal import Decimal

from src.core.schemas import Budget, UpdateMediaBuyError
from src.core.tools.media_buy_update import MAX_CAMPAIGN_BUDGET
from tests.harness.media_buy_update import MediaBuyUpdateEnv

# ---------------------------------------------------------------------------
# F-05: Budget ceiling
# ---------------------------------------------------------------------------


def test_max_campaign_budget_constant_is_ten_million() -> None:
    """Default ceiling must be 10,000,000."""
    assert MAX_CAMPAIGN_BUDGET == Decimal("10000000")


def test_extreme_budget_rejected() -> None:
    """Budget exceeding MAX_CAMPAIGN_BUDGET must return UpdateMediaBuyError."""
    with MediaBuyUpdateEnv() as env:
        result = env.call_impl(budget=Budget(total=888_888_888, currency="USD"))

    assert isinstance(result, UpdateMediaBuyError)
    assert result.errors
    assert result.errors[0].code == "budget_ceiling_exceeded"


# ---------------------------------------------------------------------------
# F-05: Constant is configurable via env var
# ---------------------------------------------------------------------------


def test_max_campaign_budget_env_override(monkeypatch) -> None:
    """MAX_CAMPAIGN_BUDGET should reflect MAX_CAMPAIGN_BUDGET_USD env var."""
    import importlib

    monkeypatch.setenv("MAX_CAMPAIGN_BUDGET_USD", "5000000")
    import src.core.tools.media_buy_update as mod

    importlib.reload(mod)
    assert mod.MAX_CAMPAIGN_BUDGET == Decimal("5000000")
    # Restore
    importlib.reload(mod)


# ---------------------------------------------------------------------------
# F-08: Min-spend parity via CurrencyLimitRepository
# ---------------------------------------------------------------------------


def test_package_budget_uses_currency_limit_repository() -> None:
    """Package min-spend validation must go through uow.currency_limits, not raw session selects."""
    with MediaBuyUpdateEnv() as env:
        env.set_media_buy(currency="EUR")
        env.set_currency_limit(min_package_budget=Decimal("100"))

        result = env.call_impl(packages=[{"package_id": "pkg-1", "budget": 50.0}])

        env.mock["uow"].return_value.currency_limits.get_for_currency.assert_called_with("EUR")
        env.mock["uow"].return_value.session.scalars.assert_not_called()

    assert isinstance(result, UpdateMediaBuyError)
    assert result.errors
    assert result.errors[0].code == "budget_below_minimum"
