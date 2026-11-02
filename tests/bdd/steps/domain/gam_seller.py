"""Steps for the local-gam-*.feature files: what a Google Ad Manager seller sends GAM.

The Given makes the env's tenant a GAM seller (``GamSellerMixin.sell_through_gam``). The
Thens read two things: the buyer's wire response, and the stand-in GAM network's record of
what the seller sent it and what it created. Their expectations are the scenario's
literals, or links the stand-in itself established (the id it gave the order), so nothing
is compared with what the harness seeded.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pytest_bdd import given, parsers, then

from tests.bdd.steps._outcome_helpers import wire_field


@given(
    parsers.re(
        r'the seller sells through Google Ad Manager as trafficker "(?P<trafficker_id>[^"]+)" on ad unit '
        r'"(?P<ad_unit_id>[^"]+)", and the buyer is its advertiser "(?P<advertiser_id>[^"]+)"'
        r'(?:, on a network (?:in time zone "(?P<network_time_zone>[^"]+)"'
        r'|that refuses to name its time zone with "(?P<network_fault>[^"]+)"))?'
    )
)
def given_seller_sells_through_gam(
    ctx: dict,
    trafficker_id: str,
    ad_unit_id: str,
    advertiser_id: str,
    network_time_zone: str | None,
    network_fault: str | None,
) -> None:
    """Onboard the env's tenant to GAM and sell the scenario's product on one ad unit.

    The optional clause states the GAM network's own time zone, or the ``ApiError`` reason
    with which the network refuses to name it; without it the stand-in network keeps its
    default zone. A scenario that needs another seller fact adds a clause to this sentence
    rather than a second seller Given.
    """
    ctx["env"].sell_through_gam(
        trafficker_id=trafficker_id,
        advertiser_id=advertiser_id,
        ad_unit_id=ad_unit_id,
        products=[ctx["default_product"]],
        network_time_zone=network_time_zone,
        network_fault=network_fault,
    )


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


#: The keys of a GAM line item or order that carry its flight.
_FLIGHT_KEYS = ("startDateTimeType", "startDateTime", "endDateTime")


def _flights(ctx: dict, service: str, method: str) -> list[dict[str, Any]]:
    """For each object the stand-in GAM network created through ``service.method``, its flight keys that are present."""
    return [
        {key: created[key] for key in _FLIGHT_KEYS if key in created}
        for created in ctx["env"].gam_objects_created(service, method)
    ]


def _gam_datetime(literal: str, time_zone: str) -> dict[str, Any]:
    """The GAM ``DateTime`` for a ``YYYY-MM-DD HH:MM:SS`` wall clock read in ``time_zone``."""
    wall_clock = datetime.strptime(literal, "%Y-%m-%d %H:%M:%S")
    return {
        "date": {"year": wall_clock.year, "month": wall_clock.month, "day": wall_clock.day},
        "hour": wall_clock.hour,
        "minute": wall_clock.minute,
        "second": wall_clock.second,
        "timeZoneId": time_zone,
    }


@then(
    parsers.re(
        r'Google Ad Manager received one line item, starting (?:at "(?P<start>[^"]+)"|immediately) '
        r'and ending at "(?P<end>[^"]+)" in time zone "(?P<zone>[^"]+)"'
    )
)
def then_one_line_item_flight(ctx: dict, start: str | None, end: str, zone: str) -> None:
    """GAM created exactly one line item, whose flight is the scenario's wall clocks in the stated zone.

    A stated start is an explicit ``startDateTime``; "immediately" is
    ``startDateTimeType: IMMEDIATELY`` with no ``startDateTime``. Exact equality, so a second
    line item, a start sent in both forms, or a wrong zone label fails it.
    """
    expected_start: dict[str, Any] = (
        {"startDateTime": _gam_datetime(start, zone)} if start is not None else {"startDateTimeType": "IMMEDIATELY"}
    )
    expected = [{**expected_start, "endDateTime": _gam_datetime(end, zone)}]
    flights = _flights(ctx, "LineItemService", "createLineItems")
    assert flights == expected, f"expected GAM to create one line item with flight {expected}; it created {flights}"


@then("Google Ad Manager received one order, carrying no flight dates of its own")
def then_one_order_without_flight(ctx: dict) -> None:
    """GAM created exactly one order, and the seller sent it none of the flight keys GAM derives from its line items."""
    flights = _flights(ctx, "OrderService", "createOrders")
    assert flights == [{}], f"expected GAM to create one order carrying no flight dates; it created {flights}"


@then("Google Ad Manager received no order and no line item")
def then_no_order_and_no_line_item(ctx: dict) -> None:
    """The seller sent GAM no order and no line item: the refusal left nothing behind in GAM."""
    sent = {
        method: ctx["env"].gam_objects_sent(service, method)
        for service, method in (("OrderService", "createOrders"), ("LineItemService", "createLineItems"))
    }
    assert sent == {"createOrders": [], "createLineItems": []}, f"expected GAM to receive nothing; it received {sent}"
