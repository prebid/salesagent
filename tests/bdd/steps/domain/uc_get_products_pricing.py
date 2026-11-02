"""Steps for BR-UC-GET-PRODUCTS-pricing-options: stored pricing rows on the buyer's wire.

Setup builds REAL ``PricingOption`` rows through ``PricingOptionFactory`` — the same
factory every other product fixture uses — and dispatch goes through the shared
``dispatch_request``. There is no fixture machinery here: a hand-built option dict is
what the unit tests these scenarios replace were made of, and it is how eight of the
nine pricing models came to be graded against the translator rather than against what
a buyer receives.

Assertions read ``wire_entry``/``wire_dict`` — the serialized body, not the typed
payload. The typed payload has already coerced every field to its declared type, so it
cannot show a serialization defect; the wire can.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from pytest_bdd import given, parsers, then

from tests.bdd.steps._outcome_helpers import wire_entry
from tests.factories import PricingOptionFactory, ProductFactory

#: Gherkin spells "no value" as the bare word ``none`` — it is a table cell, not JSON.
_ABSENT = "none"


def _json_or_none(cell: str) -> Any:
    """A JSON literal from an Examples cell, or ``None`` for the bare word ``none``."""
    cell = cell.strip()
    return None if cell == _ABSENT else json.loads(cell)


def _decimal_or_none(cell: str) -> Decimal | None:
    """A stored ``rate`` from an Examples cell, or ``None`` for the bare word ``none``."""
    cell = cell.strip()
    return None if cell == _ABSENT else Decimal(cell)


def _store_option(ctx: dict, *, same_product: bool = False, **columns: Any) -> None:
    """Persist one pricing option, on a new product or the one already under test.

    ``pricing_option_id`` is deliberately NOT passed: the point of most of these
    scenarios is which id the seller assigns, so supplying one would answer the
    question the scenario asks.
    """
    if same_product and "product" in ctx:
        product = ctx["product"]
    else:
        product = ProductFactory(tenant=ctx["tenant"])
        ctx["product"] = product
    PricingOptionFactory(product=product, **columns)


def _announced_options(ctx: dict) -> list[dict[str, Any]]:
    """The ``pricing_options`` of the one product, exactly as the buyer received them."""
    product = wire_entry(ctx, "products", index=0)
    options = product.get("pricing_options")
    assert options, f"product carries no pricing_options on the wire: {product!r}"
    return options


def _the_announced_option(ctx: dict) -> dict[str, Any]:
    """The single announced option, refusing to pick one when there are several."""
    options = _announced_options(ctx)
    assert len(options) == 1, f"expected exactly one pricing option, got {len(options)}: {options!r}"
    return options[0]


# ── Given steps ─────────────────────────────────────────────────────


@given(
    parsers.parse(
        'the seller stores a fixed "{pricing_model}" pricing option at {rate} "{currency}" with parameters {parameters}'
    )
)
def given_fixed_option(ctx: dict, pricing_model: str, rate: str, currency: str, parameters: str) -> None:
    _store_option(
        ctx,
        pricing_model=pricing_model,
        currency=currency,
        is_fixed=True,
        rate=_decimal_or_none(rate),
        price_guidance=None,
        parameters=_json_or_none(parameters),
    )


@given(
    parsers.parse(
        'the seller stores an auction "{pricing_model}" pricing option with guidance {guidance} in "{currency}"'
    )
)
def given_auction_option(ctx: dict, pricing_model: str, guidance: str, currency: str) -> None:
    _store_option(
        ctx,
        pricing_model=pricing_model,
        currency=currency,
        is_fixed=False,
        rate=None,
        price_guidance=_json_or_none(guidance),
        parameters=None,
    )


@given(
    parsers.parse(
        'the seller stores an auction "{pricing_model}" pricing option with guidance {guidance} '
        'in "{currency}" on the same product'
    )
)
def given_auction_option_same_product(ctx: dict, pricing_model: str, guidance: str, currency: str) -> None:
    _store_option(
        ctx,
        same_product=True,
        pricing_model=pricing_model,
        currency=currency,
        is_fixed=False,
        rate=None,
        price_guidance=_json_or_none(guidance),
        parameters=None,
    )


# ── Then steps ──────────────────────────────────────────────────────


@then(parsers.parse("the buyer receives the pricing option {announced}"))
def then_announced_option_equals(ctx: dict, announced: str) -> None:
    """The WHOLE announced option, compared by value.

    Whole-object equality rather than field-by-field: a field the seller adds that the
    option's schema does not declare is exactly as much of a defect as a field it drops,
    and only equality catches both.
    """
    assert _the_announced_option(ctx) == json.loads(announced)


@then(parsers.parse('the announced pricing option "{field}" is "{expected}"'))
def then_announced_field(ctx: dict, field: str, expected: str) -> None:
    assert _the_announced_option(ctx)[field] == expected


@then(parsers.parse("the announced pricing option ids are {expected}"))
def then_announced_ids(ctx: dict, expected: str) -> None:
    assert sorted(option["pricing_option_id"] for option in _announced_options(ctx)) == json.loads(expected)
