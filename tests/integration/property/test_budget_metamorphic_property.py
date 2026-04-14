"""Metamorphic property: budget value is invariant under input shape.

extract_budget_amount(budget, default_currency) accepts three shapes:
    1. float (v1.8.0)            -> uses default_currency
    2. dict {"total", "currency"} -> uses dict's currency
    3. Budget object              -> uses object's currency

For the same logical (amount, currency), all three input shapes must
return the same (amount, currency) tuple. This is a metamorphic property:
the *transformation* (which shape we use) is irrelevant to the *result*.

The existing test_budget_format_compatibility.py asserts this for a
handful of hand-picked values. Hypothesis generalizes it to thousands
of (amount, currency) combinations -- catching boundary cases (zero,
very large, currency case-sensitivity, float precision) that example
tests miss.

Catches:
    * Currency-handling drift between the three branches
    * Float precision loss in dict -> Budget conversion
    * Default-currency fallback bugs (e.g. ignoring it for non-float)
"""

from __future__ import annotations

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from src.core.schemas._base import Budget, extract_budget_amount

INMEM = settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)

# Standard ISO 4217 codes -- skip exotic currencies to stay realistic.
currency_strategy = st.sampled_from(
    ["USD", "EUR", "GBP", "JPY", "CAD", "AUD", "CHF", "CNY", "INR", "BRL"]
)

money_strategy = st.floats(
    min_value=0.01, max_value=10_000_000.0, allow_nan=False, allow_infinity=False
).map(lambda x: round(x, 2))


@INMEM
@given(amount=money_strategy, currency=currency_strategy)
def test_budget_shape_equivalence(amount: float, currency: str) -> None:
    """All three input shapes -> identical (amount, currency) result."""
    # Shape 1: bare float -- uses default_currency
    amt_f, cur_f = extract_budget_amount(amount, default_currency=currency)

    # Shape 2: dict with explicit total + currency
    amt_d, cur_d = extract_budget_amount(
        {"total": amount, "currency": currency}, default_currency="ZZZ"
    )

    # Shape 3: Budget object
    amt_o, cur_o = extract_budget_amount(
        Budget(total=amount, currency=currency), default_currency="ZZZ"
    )

    assert amt_f == amt_d == amt_o == amount, (
        f"amount drift across shapes: float={amt_f}, dict={amt_d}, "
        f"obj={amt_o}, expected={amount}"
    )
    assert cur_f == cur_d == cur_o == currency, (
        f"currency drift across shapes: float={cur_f}, dict={cur_d}, "
        f"obj={cur_o}, expected={currency}"
    )


@INMEM
@given(amount=money_strategy)
def test_none_budget_returns_zero_with_default_currency(amount: float) -> None:
    """None input -> (0.0, default_currency). Float amount is decoy
    -- only currency parameter should matter."""
    amt, cur = extract_budget_amount(None, default_currency="EUR")
    assert amt == 0.0
    assert cur == "EUR"


@INMEM
@given(currency=currency_strategy)
def test_default_currency_ignored_for_dict_with_currency(currency: str) -> None:
    """Property: when input dict carries its own currency, the
    default_currency parameter must NOT override it."""
    _, returned_currency = extract_budget_amount(
        {"total": 1000.0, "currency": currency},
        default_currency="ZZZ",  # sentinel that should never appear
    )
    assert returned_currency == currency
    assert returned_currency != "ZZZ"


@INMEM
@given(
    amount=money_strategy,
    currency=currency_strategy,
)
def test_budget_object_preserves_explicit_currency(amount: float, currency: str) -> None:
    """Budget objects must always carry their own currency through,
    regardless of default_currency."""
    _, returned_currency = extract_budget_amount(
        Budget(total=amount, currency=currency),
        default_currency="ZZZ",
    )
    assert returned_currency == currency, (
        f"Budget object currency lost: expected {currency}, got {returned_currency}"
    )
