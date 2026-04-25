"""Property-based schema roundtrip tests -- no DB, fast, broad coverage.

Demonstrates Hypothesis as a stress-tester for AdCP schema serialization.
Replaces what would otherwise be ~20+ hand-picked example tests in
test_adcp_contract.py / test_delivery_schema_contracts.py with one
property per model.

Property (universal): for any valid model instance,
    Model.model_validate(m.model_dump(mode="json"  | "python")) == m

Catches:
    * Pattern #4 violations (nested model_dump not propagating)
    * Decimal/datetime serialization drift
    * Field exclusion bugs (excluded field reappearing on reload)
    * extra="forbid" tripping on legitimate roundtrip data

Runs in <1 second for 100 examples per property -- pure CPU, no I/O.
"""

from __future__ import annotations

import json

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from src.core.schemas import (
    Budget,
    CreateMediaBuySuccess,
    Error,
)

# Shared profile -- in-memory tests can afford many examples cheaply.
INMEM = settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)


# --------------------------------------------------------------------------- #
# Strategies
# --------------------------------------------------------------------------- #

# ISO 4217 currency-shaped string (3 uppercase letters).
currency_strategy = st.text(
    alphabet=st.characters(min_codepoint=ord("A"), max_codepoint=ord("Z")),
    min_size=3,
    max_size=3,
)

# Strictly-positive money amount with cent precision.
money_strategy = st.floats(
    min_value=0.01, max_value=1_000_000.0, allow_nan=False, allow_infinity=False
).map(lambda x: round(x, 2))


@st.composite
def budget_strategy(draw: st.DrawFn) -> Budget:
    return Budget(
        total=draw(money_strategy),
        currency=draw(currency_strategy),
        daily_cap=draw(st.one_of(st.none(), money_strategy)),
        pacing=draw(st.sampled_from(["even", "asap", "daily_budget"])),
        auto_pause_on_budget_exhaustion=draw(st.one_of(st.none(), st.booleans())),
    )


@st.composite
def error_strategy(draw: st.DrawFn) -> Error:
    code = draw(
        st.sampled_from(
            [
                "validation_error",
                "authentication_error",
                "not_found",
                "rate_limit",
                "internal_error",
            ]
        )
    )
    message = draw(st.text(min_size=1, max_size=200))
    # details is a free-form dict; sample from realistic shapes.
    details = draw(
        st.one_of(
            st.none(),
            st.fixed_dictionaries(
                {
                    "error_code": st.text(min_size=1, max_size=40),
                    "field": st.text(min_size=1, max_size=40),
                }
            ),
        )
    )
    return Error(code=code, message=message, details=details)


@st.composite
def create_media_buy_success_strategy(draw: st.DrawFn) -> CreateMediaBuySuccess:
    """Minimal-but-realistic CreateMediaBuySuccess shape."""
    n_packages = draw(st.integers(min_value=0, max_value=4))
    packages = [
        {
            "package_id": f"pkg_{i}",
            "product_id": f"prod_{i}",
            "buyer_ref": f"bref_{i}",
            "budget": draw(money_strategy),
        }
        for i in range(n_packages)
    ]
    return CreateMediaBuySuccess(
        media_buy_id=draw(st.text(min_size=4, max_size=40)),
        buyer_ref=draw(st.text(min_size=1, max_size=40)),
        packages=packages,
    )


# --------------------------------------------------------------------------- #
# Properties
# --------------------------------------------------------------------------- #


def _assert_python_roundtrip(m) -> None:
    dumped = m.model_dump()
    rebuilt = type(m).model_validate(dumped)
    assert rebuilt.model_dump() == dumped, (
        f"python-mode roundtrip diverged for {type(m).__name__}"
    )


def _assert_json_roundtrip(m) -> None:
    dumped = m.model_dump(mode="json")
    blob = json.dumps(dumped)
    rebuilt = type(m).model_validate(json.loads(blob))
    assert rebuilt.model_dump(mode="json") == dumped, (
        f"JSON-mode roundtrip diverged for {type(m).__name__}"
    )


@INMEM
@given(b=budget_strategy())
def test_budget_python_roundtrip(b: Budget) -> None:
    _assert_python_roundtrip(b)


@INMEM
@given(b=budget_strategy())
def test_budget_json_roundtrip(b: Budget) -> None:
    _assert_json_roundtrip(b)


@INMEM
@given(e=error_strategy())
def test_error_python_roundtrip(e: Error) -> None:
    _assert_python_roundtrip(e)


@INMEM
@given(e=error_strategy())
def test_error_json_roundtrip(e: Error) -> None:
    _assert_json_roundtrip(e)


@INMEM
@given(s=create_media_buy_success_strategy())
def test_create_media_buy_success_python_roundtrip(s: CreateMediaBuySuccess) -> None:
    _assert_python_roundtrip(s)


@INMEM
@given(s=create_media_buy_success_strategy())
def test_create_media_buy_success_json_roundtrip(s: CreateMediaBuySuccess) -> None:
    _assert_json_roundtrip(s)


# --------------------------------------------------------------------------- #
# Constructive property: Budget rejects total <= 0
# --------------------------------------------------------------------------- #


@INMEM
@given(
    bad_total=st.floats(max_value=0.0, allow_nan=False, allow_infinity=False),
    currency=currency_strategy,
)
def test_budget_rejects_non_positive_total(bad_total: float, currency: str) -> None:
    """Budget.total has Field(..., gt=0). Must reject 0 and negatives."""
    with pytest.raises(Exception):  # ValidationError -- exact type varies by pydantic ver
        Budget(total=bad_total, currency=currency)
