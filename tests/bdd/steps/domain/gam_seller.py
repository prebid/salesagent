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

from pytest_bdd import given, parsers, then

from tests.bdd.steps._outcome_helpers import wire_field


@given(
    parsers.parse(
        'the seller sells through Google Ad Manager as trafficker "{trafficker_id}" on ad unit '
        '"{ad_unit_id}", and the buyer is its advertiser "{advertiser_id}"'
    )
)
def given_seller_sells_through_gam(ctx: dict, trafficker_id: str, ad_unit_id: str, advertiser_id: str) -> None:
    """Onboard the env's tenant to GAM and sell the scenario's product on one ad unit."""
    ctx["env"].sell_through_gam(
        trafficker_id=trafficker_id,
        advertiser_id=advertiser_id,
        ad_unit_id=ad_unit_id,
        products=[ctx["default_product"]],
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
