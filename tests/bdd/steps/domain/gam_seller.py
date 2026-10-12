"""Steps for the local Google Ad Manager features: what a GAM seller sends GAM, and what it reports.

local-gam-seller.feature: the Given makes the env's tenant a GAM seller
(``GamSellerMixin.sell_through_gam``). The Thens read two things: the buyer's wire response,
and the stand-in GAM network's record of what the seller sent it and what it created. Their
expectations are the scenario's literals, or links the stand-in itself established (the id it
gave the order), so nothing is compared with what the harness seeded.

local-gam-delivery-report.feature: the Givens make the tenant a GAM seller whose network
reports for the buyer's advertiser (``DeliveryPollEnv.report_through_gam``); the UC-004 steps
send the request and read the buyer's wire response.
"""

from __future__ import annotations

from typing import Any

from pytest_bdd import given, parsers, then, when

from tests.bdd.steps._outcome_helpers import (
    assert_by_package_exactly,
    capture_created_media_buy,
    wire_entry,
    wire_field,
)
from tests.bdd.steps.generic._dispatch import dispatch_request
from tests.bdd.steps.generic._table import rows as table_rows
from tests.helpers.gam_client import gam_delivery_row


@given(
    parsers.parse(
        'the seller sells through Google Ad Manager as trafficker "{trafficker_id}" on ad unit '
        '"{ad_unit_id}", and the buyer is its advertiser "{advertiser_id}"'
    )
)
def given_seller_sells_through_gam(ctx: dict, trafficker_id: str, ad_unit_id: str, advertiser_id: str) -> None:
    """Onboard the env's tenant to GAM and sell every product the scenario built on one ad unit.

    The products are the scenario's default product and any further products a Given built
    beside it (``ctx["extra_products"]``).
    """
    from tests.bdd.steps.generic.given_media_buy import scenario_products

    ctx["env"].sell_through_gam(
        trafficker_id=trafficker_id,
        advertiser_id=advertiser_id,
        ad_unit_id=ad_unit_id,
        products=list(scenario_products(ctx).values()),
    )


@given(parsers.re(r"the seller's (?P<delivery>guaranteed|non-guaranteed) products? (?:has|have) pricing options:"))
def given_seller_products_with_pricing_options(ctx: dict, delivery: str, datatable: Any) -> None:
    """Build the scenario's products, one per ``product_id`` in the table, with the listed pricing options.

    Columns: ``product_id | pricing_option_id | pricing_model | fixed_price | floor_price``.
    A row with a ``fixed_price`` is a fixed option at that rate; a row without one is an
    auction option with that ``floor_price``. Rows keep their order within each product, so
    a product's first row is its first pricing option.

    The first product becomes the scenario's default product and its first option the
    default pricing option, the two keys the route's seed sets; every further product is
    added to ``ctx["extra_products"]``, which the seller Given sells beside the default
    product. This step does not make the tenant a GAM seller: the seller Given that follows
    it does.
    """
    from decimal import Decimal

    env = ctx["env"]
    options: dict[str, list[dict[str, Any]]] = {}
    for row in table_rows(datatable):
        fixed_price = row["fixed_price"]
        options.setdefault(row["product_id"], []).append(
            {
                "pricing_option_id": row["pricing_option_id"],
                "pricing_model": row["pricing_model"],
                "is_fixed": bool(fixed_price),
                "rate": Decimal(fixed_price) if fixed_price else None,
                "price_guidance": None if fixed_price else {"floor": float(row["floor_price"])},
            }
        )
    built = [
        env.setup_product_chain(
            ctx["tenant"],
            product_id=product_id,
            delivery_type=delivery.replace("-", "_"),
            pricing_options=product_options,
        )
        for product_id, product_options in options.items()
    ]
    env._commit_factory_data()
    (ctx["default_product"], ctx["default_pricing_option"]), *extra = built
    ctx["extra_products"] = extra


@given(parsers.parse("the buyer's packages, over a {days:d}-day flight, are:"))
def given_buyer_packages_over_flight(ctx: dict, days: int, datatable: Any) -> None:
    """Replace the request's packages with the table's, over a flight exactly ``days`` long.

    Columns: ``product_id | pricing_option_id | budget | bid_price``; an empty ``bid_price``
    sends none. Built by ``build_request_packages``, the DTO-bound package builder.
    """
    from tests.bdd.steps.generic.given_media_buy import _ensure_request_defaults, _set_flight_days
    from tests.harness.create_request import build_request_packages

    kwargs = _ensure_request_defaults(ctx)
    _set_flight_days(kwargs, days)
    rows = table_rows(datatable)
    kwargs["packages"] = build_request_packages(
        [(row["product_id"], row["pricing_option_id"]) for row in rows],
        terms=[
            {"budget": float(row["budget"]), **({"bid_price": float(row["bid_price"])} if row["bid_price"] else {})}
            for row in rows
        ],
    )


@given("the buyer already has another active media buy with this seller")
def given_buyer_has_another_active_buy(ctx: dict) -> None:
    """One more buy of this principal, active and mid-flight, with one package.

    A delivery request that names the created buy must report that buy alone, not the
    principal's other active buy. The Then compares the reported buys with the created one,
    so this buy needs no name.
    """
    from tests.factories import MediaBuyFactory

    MediaBuyFactory(tenant=ctx["tenant"], principal=ctx["principal"], status="active")


def _line_item_for(ctx: dict, product_id: str) -> Any:
    """The line item GAM created for the package on *product_id*, matched by its own price.

    Not by position: among the line items the stand-in network created, the one whose
    ``costPerUnit`` is the product's fixed rate. Exactly one line item per package, and
    exactly one match.
    """
    from tests.bdd.steps.generic.given_media_buy import seeded_product

    line_items = _line_items_created(ctx)
    packages = ctx["request_kwargs"]["packages"]
    assert len(line_items) == len(packages), (
        f"expected GAM to create one line item per package ({len(packages)}); it created {len(line_items)}"
    )
    rate = seeded_product(ctx, product_id).pricing_options[0].rate
    micros = round(float(rate) * 1_000_000)
    matching = [item for item in line_items if int(item["costPerUnit"]["microAmount"]) == micros]
    assert len(matching) == 1, (
        f"expected exactly one line item priced {rate} for the {product_id!r} package; "
        f"line items cost {[item['costPerUnit']['microAmount'] for item in line_items]}"
    )
    return matching[0]


@given(
    parsers.re(
        r"Google Ad Manager reports (?P<impressions>\d+) impressions and (?P<spend>[\d.]+) spend for the "
        r"\"(?P<reported>[^\"]+)\" package's line item, and "
        r"(?:(?P<other_impressions>\d+) impressions and (?P<other_spend>[\d.]+) spend|nothing) for the "
        r"\"(?P<other>[^\"]+)\" package's line item"
    )
)
def given_gam_reports_line_items(
    ctx: dict,
    impressions: str,
    spend: str,
    reported: str,
    other_impressions: str | None,
    other_spend: str | None,
    other: str,
) -> None:
    """GAM's finished report holds a row for each package's line item it states figures for.

    A package is named by its product's id. "nothing" is no row for that line item. Each
    row names the line item and order the stand-in network created, and the order's
    advertiser.
    """
    orders = {str(order["id"]): order for order in _orders_created(ctx)}
    stated = [(reported, impressions, spend)]
    if other_impressions is not None and other_spend is not None:
        stated.append((other, other_impressions, other_spend))
    rows = []
    for product_id, product_impressions, product_spend in stated:
        line_item = _line_item_for(ctx, product_id)
        order = orders[str(line_item["orderId"])]
        rows.append(
            gam_delivery_row(
                advertiser_id=str(order["advertiserId"]),
                order_id=str(order["id"]),
                line_item_id=str(line_item["id"]),
                impressions=int(product_impressions),
                spend=float(product_spend),
            )
        )
    ctx["env"].serve_gam_report_rows(rows)


@when("the Buyer Agent requests delivery for the media buy it just created")
def when_request_delivery_for_created_buy(ctx: dict) -> None:
    """Capture the buy and packages create_media_buy returned, and request that buy's delivery.

    The ids are read off the create response's wire. The request is a built
    ``GetMediaBuyDeliveryRequest``, so every transport sends the same request.
    """
    from src.core.schemas import GetMediaBuyDeliveryRequest

    media_buy_id = capture_created_media_buy(ctx)
    ctx["created_package_ids"] = {
        package["product_id"]: package["package_id"] for package in wire_field(ctx, "packages")
    }
    dispatch_request(ctx, req=GetMediaBuyDeliveryRequest(media_buy_ids=[media_buy_id]))


@then(
    "the delivery report covers only the media buy it just created, "
    "with exactly these packages under the ids create_media_buy returned:"
)
def then_delivery_covers_created_buy_packages(ctx: dict, datatable: Any) -> None:
    """The report is the created buy alone, and its packages are the created ones with these figures.

    Each row's ``package`` is a product id; it names the package create_media_buy
    returned for that product.
    """
    media_buy_id = ctx["created_media_buy_id"]
    reported = [delivery.get("media_buy_id") for delivery in wire_field(ctx, "media_buy_deliveries")]
    assert reported == [media_buy_id], (
        f"expected a report of exactly the created buy {media_buy_id!r}; it reported {reported}"
    )
    assert_by_package_exactly(
        wire_entry(ctx, "media_buy_deliveries", media_buy_id=media_buy_id),
        table_rows(datatable),
        package_of=lambda product_id: ctx["created_package_ids"][product_id],
    )


@given(
    parsers.parse(
        "the seller reports delivery from Google Ad Manager, which has reported {impressions:d} impressions "
        'and {spend:g} spend so far for the buyer\'s advertiser "{advertiser_id}"'
    )
)
def given_gam_reported_so_far(ctx: dict, impressions: int, spend: float, advertiser_id: str) -> None:
    """GAM's report for the buyer's advertiser holds the figures it has so far."""
    ctx["env"].report_through_gam(advertiser_id=advertiser_id, impressions=impressions, spend=spend)


@given(
    parsers.parse(
        "the seller reports delivery from Google Ad Manager, which has reported nothing yet "
        'for the buyer\'s advertiser "{advertiser_id}"'
    )
)
def given_gam_reported_nothing(ctx: dict, advertiser_id: str) -> None:
    """GAM's report for the buyer's advertiser finishes with no rows."""
    ctx["env"].report_through_gam(advertiser_id=advertiser_id)


@given(
    parsers.parse(
        "the seller reports delivery from Google Ad Manager, which fails the report job "
        'for the buyer\'s advertiser "{advertiser_id}"'
    )
)
def given_gam_fails_report_job(ctx: dict, advertiser_id: str) -> None:
    """GAM's report job for the buyer's advertiser ends FAILED."""
    ctx["env"].report_through_gam(advertiser_id=advertiser_id, job_status="FAILED")


def _orders_created(ctx: dict) -> list[Any]:
    """The orders the stand-in GAM network created, each carrying the id it assigned."""
    return ctx["env"].gam_objects_created("OrderService", "createOrders")


def _line_items_created(ctx: dict) -> list[Any]:
    """The line items the stand-in GAM network created, each carrying the id it assigned."""
    return ctx["env"].gam_objects_created("LineItemService", "createLineItems")


@then(
    parsers.parse(
        "the buyer holds a media buy booked in Google Ad Manager as one order, "
        'for advertiser "{advertiser_id}", trafficked by "{trafficker_id}"'
    )
)
def then_one_order_for_advertiser(ctx: dict, advertiser_id: str, trafficker_id: str) -> None:
    """The buyer received a media_buy_id, and GAM created exactly one order for its advertiser."""
    wire_field(ctx, "media_buy_id")
    orders = [(str(order["advertiserId"]), str(order["traffickerId"])) for order in _orders_created(ctx)]
    assert orders == [(advertiser_id, trafficker_id)], (
        f"expected GAM to create one order for advertiser {advertiser_id} trafficked by {trafficker_id}; "
        f"it created {orders}"
    )


@then(
    parsers.parse(
        'Google Ad Manager received one line item, in that order, targeting ad unit "{ad_unit_id}" with its descendants'
    )
)
def then_one_line_item_on_ad_unit(ctx: dict, ad_unit_id: str) -> None:
    """The seller sent one line item, under the order GAM created, targeting the product's ad unit."""
    orders = _orders_created(ctx)
    assert len(orders) == 1, f"expected GAM to create exactly one order; it created {len(orders)}"
    line_items = [
        (str(item["orderId"]), item["targeting"]["inventoryTargeting"]["targetedAdUnits"])
        for item in ctx["env"].gam_objects_sent("LineItemService", "createLineItems")
    ]
    expected = [(str(orders[0]["id"]), [{"adUnitId": ad_unit_id, "includeDescendants": True}])]
    assert line_items == expected, f"expected GAM to receive line items {expected}; it received {line_items}"
