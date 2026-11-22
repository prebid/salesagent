"""Steps for the local-gam-*.feature files: what a Google Ad Manager seller sends GAM, and what it reports.

local-gam-seller.feature, local-gam-line-item-flight.feature, local-gam-geo-targeting.feature,
local-gam-creative-kinds.feature (create_media_buy on ``MediaBuyCreateEnv``) and
local-uc006-gam-assignment-trafficking.feature (sync_creatives and the seller's creative
approval on ``CreativeSyncEnv``): the Given makes the
env's tenant a GAM seller (``GamSellerMixin.sell_through_gam``). The Thens read two things: the
buyer's wire response, and the stand-in GAM network's record of what the seller sent it and what
it created. Their expectations are the scenario's literals, or links the stand-in itself
established (the ids it gave the order and the creative), so nothing is compared with what the
harness seeded.

local-gam-delivery-report.feature: the Givens make the tenant a GAM seller whose network
reports for the buyer's advertiser (``DeliveryPollEnv.report_through_gam``); the UC-004 steps
send the request and read the buyer's wire response.

local-gam-buy-readback.feature: the Givens build the seller's products and the buyer's packages,
and state GAM's report per line item the stand-in network created (``serve_gam_report_rows``).
The Then compares the delivery report with the ids create_media_buy returned.

local-gam-line-item-goal.feature: the Givens build the seller's products and the buyer's
packages, or a buy that awaits the seller's approval. The Thens read the line items the seller
sent the stand-in network, and the approval's result.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from typing import Any

from pytest_bdd import given, parsers, then, when

from tests.bdd.steps._outcome_helpers import (
    assert_by_package_exactly,
    capture_created_media_buy,
    wire_entry,
    wire_entry_errors,
    wire_field,
)
from tests.bdd.steps.domain.uc006_sync_creatives import _ask_assignment_terms, when_sync_creative_with_assignments
from tests.bdd.steps.generic._account_resolution import ensure_tenant_principal
from tests.bdd.steps.generic._creative_seeding import store_creative, store_package_creative
from tests.bdd.steps.generic._dispatch import dispatch_request
from tests.bdd.steps.generic._table import comma_list
from tests.bdd.steps.generic._table import rows as table_rows
from tests.factories.creative_asset import (
    LIBRARY_IMAGE_FORMAT,
    build_assets,
    html_spec,
    image_spec,
    library_image_assets,
    parse_sizes,
    text_spec,
    url_spec,
)
from tests.helpers.gam_client import gam_delivery_row


@given(
    parsers.re(
        r'the seller sells through Google Ad Manager as trafficker "(?P<trafficker_id>[^"]+)" on ad unit '
        r'"(?P<ad_unit_id>[^"]+)", and the buyer is its advertiser "(?P<advertiser_id>[^"]+)"'
        r'(?:, on a network (?:in time zone "(?P<network_time_zone>[^"]+)"'
        r'|that refuses to name its time zone with "(?P<network_fault>[^"]+)"))?'
        r"(?:, with product formats '(?P<product_formats>[^']*)')?"
        r"(?:, with product settings '(?P<product_settings>[^']*)')?"
    )
)
def given_seller_sells_through_gam(
    ctx: dict,
    trafficker_id: str,
    ad_unit_id: str,
    advertiser_id: str,
    network_time_zone: str | None,
    network_fault: str | None,
    product_formats: str | None,
    product_settings: str | None,
) -> None:
    """Onboard the env's tenant to GAM and sell every product the scenario built on one ad unit.

    The products are the scenario's default product and any further products a Given built
    beside it (``ctx["extra_products"]``). An env that seeds no product (``CreativeSyncEnv``)
    gets one here, with the default pricing option, so its booked buys are on a product the
    seller sells.

    The optional network clause states the GAM network's own time zone, or the ``ApiError``
    reason with which the network refuses to name it; without it the stand-in network keeps
    its default zone. The optional product-formats clause is a JSON object of the format
    arms each product sells (``format_ids``, ``format_options``; an arm it omits is none),
    from which the GAM configuration is generated. The optional
    product-settings clause is a JSON object of ``implementation_config`` keys the seller
    wrote over the generated GAM configuration of each product it sells. A scenario that
    needs another seller fact adds a clause to this sentence rather than a second seller
    Given.
    """
    from tests.bdd.steps.generic.given_media_buy import scenario_products

    if "default_product" not in ctx:
        ensure_tenant_principal(ctx, ctx["env"])
        ctx["default_product"] = _priced_product(ctx)
    ctx["env"].sell_through_gam(
        trafficker_id=trafficker_id,
        advertiser_id=advertiser_id,
        ad_unit_id=ad_unit_id,
        products=list(scenario_products(ctx).values()),
        network_time_zone=network_time_zone,
        network_fault=network_fault,
        implementation_config=json.loads(product_settings) if product_settings is not None else None,
        product_formats=json.loads(product_formats) if product_formats is not None else None,
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
    parsers.parse(
        'the package references the buyer\'s approved 300x250 image creative "{ready_id}" showing "{ready_url}" '
        'and its creative "{held_id}" showing "{held_url}" that awaits the seller\'s review'
    )
)
def given_package_references_ready_and_held_creatives(
    ctx: dict, ready_id: str, ready_url: str, held_id: str, held_url: str
) -> None:
    """Two library creatives in the format the GAM product sells, both named in the package's creative_ids."""
    store_package_creative(ctx, ready_id, library_image_assets(ready_url), fmt=LIBRARY_IMAGE_FORMAT)
    store_package_creative(
        ctx, held_id, library_image_assets(held_url), fmt=LIBRARY_IMAGE_FORMAT, status="pending_review"
    )


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
    parsers.re(
        r'the package references the buyer\'s 300x250 image creative "(?P<creative_id>[^"]+)" showing '
        r'"(?P<url>[^"]+)", (?P<status>approved|awaiting the seller\'s review) and already held by '
        r'Google Ad Manager as creative "(?P<gam_id>\d+)"$'
    )
)
def given_package_references_creative_gam_holds(
    ctx: dict, creative_id: str, url: str, status: str, gam_id: str
) -> None:
    """A library creative whose record holds GAM's id for it, named in the package's creative_ids."""
    store_package_creative(
        ctx,
        creative_id,
        library_image_assets(url),
        fmt=LIBRARY_IMAGE_FORMAT,
        status="approved" if status == "approved" else "pending_review",
        ad_server_creative_id=ctx["env"].hold_gam_creative(gam_id, url),
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
        'another buyer of the seller holds an approved 300x250 image creative "{creative_id}" showing "{url}"'
    )
)
def given_another_buyer_holds_creative(ctx: dict, creative_id: str, url: str) -> None:
    """A second principal of the tenant, with a creative under an id the buyer also uses."""
    from tests.factories import PrincipalFactory

    store_creative(
        ctx,
        creative_id,
        library_image_assets(url),
        fmt=LIBRARY_IMAGE_FORMAT,
        principal=PrincipalFactory(tenant=ctx["tenant"]),
    )


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


@given(
    parsers.parse(
        'the buyer holds an approved 300x250 image creative "{creative_id}" showing "{url}", '
        "recorded with its own id as the ad server's"
    )
)
def given_buyer_holds_creative_recorded_with_own_id(ctx: dict, creative_id: str, url: str) -> None:
    """A library creative whose record holds the buyer's id under ``platform_creative_id``, as #2247 wrote it."""
    store_creative(
        ctx, creative_id, library_image_assets(url), fmt=LIBRARY_IMAGE_FORMAT, platform_creative_id=creative_id
    )


@given(
    parsers.parse(
        'the buyer holds an approved 300x250 HTML creative "{creative_id}" with markup "{markup}", '
        'recorded with the creative agent\'s preview link "{preview_url}" as its URL'
    )
)
def given_buyer_holds_html_creative(ctx: dict, creative_id: str, markup: str, preview_url: str) -> None:
    """A library creative in the reference HTML format, recorded as sync_creatives records one.

    Its one asset is HTML markup (core/assets/html-asset.json: ``asset_type: "html"``, the
    markup in ``content``). The sync records the first render of the creative agent's preview
    as the creative's root ``url``, ``width`` and ``height``
    (src/core/tools/creatives/_processing.py).
    """
    store_creative(
        ctx,
        creative_id,
        build_assets(html_spec("html_creative", content=markup)),
        fmt="display_300x250_html",
        url=preview_url,
        width=300,
        height=250,
    )


@given(
    parsers.parse(
        'the package references the buyer\'s approved 300x250 HTML creative "{creative_id}" with markup "{markup}", '
        "stored without a URL"
    )
)
def given_package_references_html_creative(ctx: dict, creative_id: str, markup: str) -> None:
    """A library creative in the reference HTML format whose one asset is its markup, and no root URL or size."""
    assets = build_assets(html_spec("html_creative", content=markup))
    store_package_creative(ctx, creative_id, assets, fmt="display_300x250_html")


@given(
    parsers.parse(
        'the package references the buyer\'s approved native_in_feed creative "{creative_id}" titled "{title}" '
        'by "{advertiser}", landing on "{landing_url}", with main image "{image_url}"'
    )
)
def given_package_references_native_creative(
    ctx: dict, creative_id: str, title: str, advertiser: str, landing_url: str, image_url: str
) -> None:
    """A canonical ``native_in_feed`` creative: its required slots and a main image.

    AdCP 3.2.7 formats/canonical/native_in_feed.json requires ``title``,
    ``advertiser_name`` and ``landing_page_url``; ``main_image`` is optional.
    """
    assets = build_assets(
        text_spec("title", content=title),
        text_spec("advertiser_name", content=advertiser),
        url_spec("landing_page_url", url=landing_url),
        image_spec("main_image", url=image_url),
    )
    store_package_creative(ctx, creative_id, assets, kind="native_in_feed")


@given(
    parsers.re(
        r'Google Ad Manager sells product "(?P<product_id>[^"]+)" with product formats \'(?P<product_formats>[^\']*)\''
        r"(?:, with product settings '(?P<product_settings>[^']*)')?$"
    )
)
def given_gam_sells_product_with_formats(
    ctx: dict, product_id: str, product_formats: str, product_settings: str | None
) -> None:
    """One product of the GAM seller sells its own format arms, over its own configuration keys.

    The seller Given sells every product with one set of formats; this re-sells *product_id*
    with *product_formats* (``core/product.json``'s ``format_ids``/``format_options``) and the
    ``implementation_config`` keys in *product_settings*, as the Admin UI's product form
    writes them.
    """
    from tests.bdd.steps.generic.given_media_buy import seeded_product

    ctx["env"].sell_product_through_gam(
        seeded_product(ctx, product_id),
        product_formats=json.loads(product_formats),
        implementation_config=json.loads(product_settings) if product_settings is not None else {},
    )


@given(parsers.parse('the seller\'s ad server is now "{adapter_type}", which takes no creatives from the seller'))
def given_seller_ad_server_takes_no_creatives(ctx: dict, adapter_type: str) -> None:
    """The tenant's adapter becomes *adapter_type*; its packages keep the line item ids they store."""
    tenant = ctx["tenant"]
    tenant.ad_server = adapter_type
    tenant.adapter_config.adapter_type = adapter_type
    ctx["env"]._commit_factory_data()


@given("a media buy awaits the seller's approval with one package on the product, stored without its pricing_info")
def given_pending_buy_without_pricing_info(ctx: dict) -> None:
    """Persist a 14-day buy awaiting approval whose one package row has its budget and no ``pricing_info``.

    The row a pending writer stored before it kept the terms of the selected option: the
    request names the product's first option, and nothing on the row says which option the
    package bought.
    """
    from tests.factories import MediaPackageFactory
    from tests.helpers.media_buy_approval import pending_media_buy

    package = {
        "product_id": ctx["default_product"].product_id,
        "budget": 420.0,
        "pricing_option_id": ctx["default_pricing_option"].pricing_option_id,
    }
    media_buy = pending_media_buy(
        tenant=ctx["tenant"],
        principal=ctx["principal"],
        package=package,
        starts_in_days=1,
        flight_days=14,
        budget=Decimal("420"),
    )
    MediaPackageFactory(media_buy=media_buy, package_id="pkg_a", budget=Decimal("420"), package_config=package)
    ctx["env"]._commit_factory_data()


@when(parsers.parse("{days:d} days pass before the seller approves the media buy"))
def when_days_pass_before_approval(ctx: dict, days: int) -> None:
    """Move the awaiting buy's stored flight back, which is what the days passing does to it."""
    ctx["env"].move_pending_flight_back(days)


@when("the seller approves the media buy")
def when_seller_approves_media_buy(ctx: dict) -> None:
    """The seller approves the one buy awaiting approval, as every Admin UI approval route does."""
    ctx["approval_result"] = ctx["env"].approve_pending_media_buy()


@when(
    parsers.re(
        r"the Buyer Agent sends the create_media_buy request, which the seller books (?P<booked>at once|on approving it)$"
    )
)
def when_create_booked(ctx: dict, booked: str) -> None:
    """The buyer's create, then the seller's approval of the buy when the seller books it on approving it.

    The create every scenario sends and the approval "the seller approves the media buy" runs,
    so one outline grades a GAM invariant on both paths that push creatives at booking: the
    auto-approved create and the seller's approval. Which path books the buy is the row's.
    """
    from tests.bdd.steps.domain.uc002_create_media_buy import when_send_create_media_buy

    when_send_create_media_buy(ctx)
    if booked == "on approving it":
        when_seller_approves_media_buy(ctx)


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


@then("Google Ad Manager holds the order it created as approved")
def then_gam_holds_created_order_approved(ctx: dict) -> None:
    """GAM created exactly one order, and holds it APPROVED (``Order.status``), as the seller's approval left it."""
    statuses = [order.get("status") for order in _orders_created(ctx)]
    assert statuses == ["APPROVED"], f"expected GAM to hold one APPROVED order; it holds orders in {statuses}"


#: The keys of a GAM line item or order that carry its flight.
_FLIGHT_KEYS = ("startDateTimeType", "startDateTime", "endDateTime")


def _flights(ctx: dict, service: str, method: str) -> list[dict[str, Any]]:
    """For each object the stand-in GAM network created through ``service.method``, its flight keys that are present."""
    return ctx["env"].gam_flights(service, method, _FLIGHT_KEYS)


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


# ═══════════════════════════════════════════════════════════════════════
# sync_creatives onto buys already booked in GAM
# (local-uc006-gam-assignment-trafficking.feature, on CreativeSyncEnv)
# ═══════════════════════════════════════════════════════════════════════


@given("the creative agent serves the reference creative formats")
def given_creative_agent_serves_reference_formats(ctx: dict) -> None:
    """The creative agent answers a lookup of a reference-catalog format with that format."""
    ctx["env"].serve_reference_formats()


def _priced_product(ctx: dict) -> Any:
    """A product of the scenario's tenant with the default pricing option, as the route's seed builds one."""
    from tests.factories import PricingOptionFactory, ProductFactory

    product = ProductFactory(tenant=ctx["tenant"])
    PricingOptionFactory(product=product)
    return product


_BOOKED_PACKAGE = r'package "([^"]+)" is line item "(\d+)" accepting (\d+)x(\d+)'


@given(
    parsers.re(
        r"(?P<owner>the buyer|another buyer of the seller) holds a buy booked in Google Ad Manager as "
        r'order "(?P<order_id>\d+)"'
        rf'(?: in status "(?P<status>[^"]+)")? whose (?P<packages>{_BOOKED_PACKAGE}(?: and {_BOOKED_PACKAGE})*)'
        r"(?:, on a product selling '(?P<product_formats>[^']*)')?$"
    )
)
def given_buyer_holds_booked_buy(
    ctx: dict, owner: str, order_id: str, status: str | None, packages: str, product_formats: str | None
) -> None:
    """A buy booked in GAM, the buyer's or a second GAM advertiser's: each package is its stated line item of the order.

    The buy is on the scenario's default product. The optional product clause books it on a
    product of its own instead, which the seller sells through GAM with *product_formats*
    (the seller Given's grammar: ``core/product.json``'s ``format_ids``/``format_options``),
    so a scenario can state what the booked package's product accepts.
    """
    from tests.factories import PrincipalFactory

    principal = None
    if owner != "the buyer":
        # A buy booked in GAM implies a GAM advertiser.
        principal = PrincipalFactory(
            tenant=ctx["tenant"], platform_mappings={"google_ad_manager": {"advertiser_id": "3000002"}}
        )
    product = ctx["default_product"]
    if product_formats is not None:
        product = _priced_product(ctx)
        ctx["env"].sell_product_through_gam(
            product, product_formats=json.loads(product_formats), implementation_config={}
        )
    media_buy = ctx["env"].book_in_gam(
        principal=principal,
        line_items=[
            (package, line_item, (int(width), int(height)))
            for package, line_item, width, height in re.findall(_BOOKED_PACKAGE, packages)
        ],
        order_id=order_id,
        product=product,
        status=status or "pending_creatives",
    )
    if principal is None:
        # The buyer's own booked buy: the one the seller's approval of a creative pushes to.
        ctx["live_media_buy"] = media_buy


@given(parsers.re(r'the buyer has (?P<state>paused|left) package "(?P<package>[^"]+)" of the buy$'))
def given_buyer_set_package_paused(ctx: dict, state: str, package: str) -> None:
    """The package's stored ``paused``, as ``update_media_buy`` leaves it; "left" is a package not paused."""
    if state == "paused":
        ctx["env"].pause_booked_package(package)


@given(
    parsers.re(
        r"the buyer holds a buy (?P<held>awaiting the seller's approval|the seller approved and holds for its "
        r'creatives), not booked in Google Ad Manager, with package "(?P<package>[^"]+)"$'
    )
)
def given_buyer_holds_unbooked_buy(ctx: dict, held: str, package: str) -> None:
    """A buy not booked yet, awaiting the seller or already approved: its one package is no line item yet."""
    status = "pending_approval" if held == "awaiting the seller's approval" else "pending_creatives"
    ctx["env"].hold_unbooked_buy(package_id=package, product=ctx["default_product"], status=status)


def _image_creative(creative_id: str, url: str) -> dict[str, Any]:
    """A 300x250 image creative as the buyer sends it, in the reference format the GAM product sells."""
    from src.core.format_cache import DEFAULT_AGENT_URL
    from tests.factories.creative_asset import named_format_assets
    from tests.factories.request import CreativeAssetRequestFactory

    return CreativeAssetRequestFactory.payload(
        creative_id=creative_id,
        name=f"Creative {creative_id}",
        format_id={"agent_url": DEFAULT_AGENT_URL, "id": "display_300x250_image"},
        assets=named_format_assets("display_300x250_image", media_url=url),
    )


@given(
    parsers.re(
        r'the buyer (?P<tense>syncs|synced) a 300x250 image creative "(?P<creative_id>[^"]+)" showing '
        r'"(?P<url>[^"]+)" assigned to packages? "(?P<packages>[^"]+)"'
        r"(?: with weight (?P<weight>\d+))?(?P<dry_run> as a dry run)?$"
    )
)
def given_buyer_syncs_image_creative(
    ctx: dict, tense: str, creative_id: str, url: str, packages: str, weight: str | None, dry_run: str | None
) -> None:
    """The buyer's sync of one image creative assigned to the stated packages: sent now ("synced"), or by the When.

    A sync sent now goes through the scenario's transport like the When's, and must have
    succeeded, so a later step grades what the seller did with it rather than a refusal.
    """
    ctx["creatives"] = [_image_creative(creative_id, url)]
    ctx["assignments"] = {creative_id: comma_list(packages)}
    if weight is not None:
        for package in ctx["assignments"][creative_id]:
            _ask_assignment_terms(ctx, creative_id, package, weight=int(weight))
    if dry_run:
        ctx["dry_run"] = True
    if tense == "synced":
        when_sync_creative_with_assignments(ctx)
        assert ctx.get("error") is None, f"the buyer's earlier sync of {creative_id!r} failed: {ctx.get('error')!r}"
        ctx.pop("creatives", None)
        ctx.pop("assignments", None)
        ctx.pop("assignment_terms", None)
        ctx.pop("dry_run", None)


_REFERENCES_LIBRARY_CREATIVE = (
    r'references library creative "(?P<creative_id>[^"]+)", without resending it, '
    r'assigned to packages? "(?P<packages>[^"]+)"$'
)


@when(parsers.re("the Buyer Agent " + _REFERENCES_LIBRARY_CREATIVE))
def when_buyer_references_library_creative(ctx: dict, creative_id: str, packages: str) -> None:
    """Sync an assignment of a library creative without resending its content.

    ``creatives`` is required (sync-creatives-request.json, minItems 1), so it carries one
    other creative, assigned nowhere.
    """
    ctx["creatives"] = [_image_creative(f"{creative_id}-unassigned", "https://example.com/unassigned.png")]
    ctx["assignments"] = {creative_id: comma_list(packages)}
    when_sync_creative_with_assignments(ctx)


@given(parsers.parse('Google Ad Manager refuses associations on line item "{line_item}" with "{reason}"'))
def given_gam_refuses_association(ctx: dict, line_item: str, reason: str) -> None:
    """GAM refuses this one line item's associations with a SOAP fault, and takes the others."""
    ctx["env"].refuse_gam_association(line_item, reason)


@given(parsers.parse('Google Ad Manager refuses to update creatives with "{reason}"'))
def given_gam_refuses_creative_updates(ctx: dict, reason: str) -> None:
    """GAM refuses creative updates with a SOAP fault, until it takes them again."""
    ctx["env"].refuse_gam_creative_updates(reason)


@when("Google Ad Manager takes creative updates again")
def when_gam_takes_creative_updates(ctx: dict) -> None:
    """The fault has cleared."""
    ctx["env"].refuse_gam_creative_updates(None)


@given(parsers.parse('Google Ad Manager refuses to read the order\'s line items with "{reason}"'))
def given_gam_refuses_line_item_read(ctx: dict, reason: str) -> None:
    """GAM refuses the read every push starts with, with a SOAP fault."""
    ctx["env"].refuse_gam_line_item_read(reason)


@when(parsers.parse('the seller approves creative "{creative_id}"'))
def when_seller_approves_creative(ctx: dict, creative_id: str) -> None:
    """The seller approves the creative in the Admin UI's review queue, keeping what the route answered."""
    ctx.setdefault("seller_creative_approvals", {})[creative_id] = ctx["env"].approve_creative_as_seller(creative_id)


def _creatives_created(ctx: dict) -> list[Any]:
    """The creatives the stand-in GAM network created, each carrying the id it assigned."""
    return ctx["env"].gam_objects_created("CreativeService", "createCreatives")


def _line_item_created(ctx: dict) -> Any:
    """The one line item the stand-in GAM network created, carrying the id it assigned."""
    line_items = _line_items_created(ctx)
    assert len(line_items) == 1, f"'the line item it created' names one line item; GAM created {len(line_items)}"
    return line_items[0]


def _assert_holds_exactly(held: list[Any], expected: list[Any], what: str) -> None:
    """GAM holds exactly *expected* of *what*, compared as a multiset (``collections.Counter``).

    A missing, extra or repeated item fails and their order does not. A real network does not
    answer in the order the seller created things: read back in id order, run 025d613f held a
    creative on line items "5000001,5000002" as [5000002, 5000001], and run f18f96f3 held the
    creatives "ready.png, held.png", created in that order, as [held.png, ready.png].
    """
    assert Counter(held) == Counter(expected), (
        f"expected GAM to hold {what} {sorted(expected)}; it holds {sorted(held)}"
    )


def _assert_creatives_created(ctx: dict, expected: list[tuple[str, str, str | None, str | None]]) -> None:
    """GAM created exactly *expected*: (type, advertiser id, image URL, snippet) of each creative, in any order."""
    created = [
        (c["xsi_type"], str(c["advertiserId"]), c.get("imageUrl"), c.get("snippet")) for c in _creatives_created(ctx)
    ]
    _assert_holds_exactly(created, expected, "the creatives")


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


@then(
    parsers.re(
        r"Google Ad Manager received one line item, which starts serving (?P<starts>immediately|at its scheduled start)$"
    )
)
def then_one_line_item_starts(ctx: dict, starts: str) -> None:
    """GAM created one line item whose start is IMMEDIATELY, or an explicit ``startDateTime`` and no start type."""
    (flight,) = _flights(ctx, "LineItemService", "createLineItems")
    start_keys = {key: value for key, value in flight.items() if key.startswith("startDateTime")}
    if starts == "immediately":
        assert start_keys == {"startDateTimeType": "IMMEDIATELY"}, f"expected an immediate start; GAM received {flight}"
    else:
        assert set(start_keys) == {"startDateTime"}, f"expected an explicit start; GAM received {flight}"


@then("Google Ad Manager received one order, carrying no flight dates of its own")
def then_one_order_without_flight(ctx: dict) -> None:
    """GAM created exactly one order, and the seller sent it none of the flight keys GAM derives from its line items."""
    flights = _flights(ctx, "OrderService", "createOrders")
    assert flights == [{}], f"expected GAM to create one order carrying no flight dates; it created {flights}"


@then(
    parsers.re(
        r"Google Ad Manager created (?:one image creative|image creatives) for advertiser "
        r'"(?P<advertiser_id>\d+)" showing "(?P<urls>[^"]+)"$'
    )
)
def then_gam_created_image_creatives(ctx: dict, advertiser_id: str, urls: str) -> None:
    """GAM holds exactly these creatives from this scenario: images for the advertiser, showing the URLs."""
    _assert_creatives_created(
        ctx, [("ImageRedirectCreative", advertiser_id, url.strip(), None) for url in urls.split(",")]
    )


@then(
    parsers.re(
        r'Google Ad Manager created one third-party creative for advertiser "(?P<advertiser_id>\d+)" whose snippet is '
        r"(?:\"(?P<double_quoted>[^\"]+)\"|'(?P<single_quoted>[^']+)') and which names no image URL$"
    )
)
def then_gam_created_one_third_party_creative(
    ctx: dict, advertiser_id: str, double_quoted: str | None, single_quoted: str | None
) -> None:
    """GAM holds exactly one creative from this scenario: the markup itself, not a picture of a page.

    The snippet is quoted with whichever quote it does not contain, and compared byte for byte.
    """
    # Byte equality, macro tokens included: core/assets/display-tag-asset.json:17 at
    # adcp@8d1d3244b, "Tokens remain byte-preserved until the resolver named by each
    # declaration substitutes them."
    _assert_creatives_created(ctx, [("ThirdPartyCreative", advertiser_id, None, double_quoted or single_quoted)])


@then(parsers.parse('Google Ad Manager tracks that creative\'s impressions at exactly "{urls}"'))
def then_gam_tracks_impressions_at(ctx: dict, urls: str) -> None:
    """The one creative GAM created fires exactly these impression trackers, in order, in GAM's macro spelling."""
    (creative,) = _creatives_created(ctx)
    trackers = list(creative.get("thirdPartyImpressionTrackingUrls") or [])
    # docs/creative/universal-macros.mdx:694 at adcp@8d1d3244b: the sales agent "Translates
    # platform macros to their ad server's syntax", ``{CACHEBUSTER}`` -> ``%%CACHEBUSTER%%`` (GAM).
    assert trackers == comma_list(urls), f"expected GAM to fire {comma_list(urls)}; the creative fires {trackers}"
    # Equality already rules it out; stated because a tracker still carrying an AdCP token is
    # the failure this grades, and GAM would fire it with the braces in it.
    leftover = [url for url in trackers if re.search(r"\{[A-Z_]+\}", url)]
    assert not leftover, f"GAM fires trackers still in AdCP macro spelling: {leftover}"


@then(
    parsers.parse(
        'Google Ad Manager created one image redirect creative for advertiser "{advertiser_id}" showing '
        '"{image_url}" and clicking through to "{destination_url}"'
    )
)
def then_gam_created_one_image_redirect(ctx: dict, advertiser_id: str, image_url: str, destination_url: str) -> None:
    """GAM holds exactly one creative from this scenario: an image redirect over exactly this pair of URLs."""
    # Byte equality on both URLs. A display_tag pair keeps its macro tokens:
    # core/assets/display-tag-asset.json:119 at adcp@8d1d3244b, "The pair MUST NOT be split,
    # mixed, or revised independently." An image's landing page arrives in GAM's macro
    # spelling, docs/creative/universal-macros.mdx:694, so its expected value is GAM's.
    created = [
        (c["xsi_type"], str(c["advertiserId"]), c.get("imageUrl"), c.get("destinationUrl"))
        for c in _creatives_created(ctx)
    ]
    expected = [("ImageRedirectCreative", advertiser_id, image_url, destination_url)]
    assert created == expected, f"expected GAM to create {expected}; it created {created}"


@then(
    parsers.parse(
        'Google Ad Manager created one HTML5 creative for advertiser "{advertiser_id}" whose html5 asset is the '
        "buyer's zip bundle, in a file with a zip extension, and which names no image URL"
    )
)
def then_gam_created_one_html5_creative(ctx: dict, advertiser_id: str) -> None:
    """GAM holds exactly one creative from this scenario: an ``Html5Creative`` carrying the bundle's own bytes.

    Ad Manager API v202602 ``Html5Creative.html5Asset``: the ``assetByteArray`` "must be a
    zip bundle" and the ``fileName`` "must have a zip extension". In process the bytes are the
    ones the buyer's bundle host served, so a bundle the seller did not fetch cannot pass; a
    real network does not read the bytes back, and answers the asset GAM holds by its
    ``assetId`` (``gam_asset_is_the_bundle``).
    """
    created = _creatives_created(ctx)
    kinds = [(c["xsi_type"], str(c["advertiserId"]), c.get("imageUrl")) for c in created]
    assert kinds == [("Html5Creative", advertiser_id, None)], f"expected one Html5Creative; GAM created {created}"
    asset = created[0]["html5Asset"]
    assert ctx["env"].gam_asset_is_the_bundle(asset, ctx["html5_bundle"]), f"the html5 asset {asset} is not the bundle"
    assert str(asset.get("fileName", "")).endswith(".zip"), f"the html5 asset's file name {asset.get('fileName')!r}"


@then(parsers.re(r"Google Ad Manager created that creative at size (?P<width>\d+)x(?P<height>\d+)$"))
def then_gam_created_creative_at_size(ctx: dict, width: str, height: str) -> None:
    """The one creative GAM created has this ``size``: GAM keeps a creative's size for life."""
    sizes = [c.get("size") for c in _creatives_created(ctx)]
    expected = [{"width": int(width), "height": int(height)}]
    assert sizes == expected, f"expected GAM to create one creative at {expected}; it created sizes {sizes}"


@then(
    parsers.re(
        r"Google Ad Manager associated that creative with the line item it created, "
        r'(?:overriding its size with "(?P<sizes>[^"]+)"|at its own size)$'
    )
)
def then_gam_associated_with_size_override(ctx: dict, sizes: str | None) -> None:
    """One association, of the one creative onto the one line item, carrying exactly these sizes, or none.

    Ad Manager API v202602 ``LineItemCreativeAssociation.sizes``: "Overrides the value set for
    Creative.size, which allows the creative to be served to ad units that would otherwise
    not be compatible for its actual size." "At its own size" is an association stating none.
    The scenarios say the association "carries" its sizes, which is a set: they are compared
    as a multiset, so a missing, extra or repeated size fails and their order does not. A
    real network does not keep the order the seller sent. The creative's own size, the
    first one, is graded by "created that creative at size".
    """
    (creative,) = _creatives_created(ctx)
    held = [
        (str(a["creativeId"]), str(a["lineItemId"]), _size_counts(a.get("sizes") or [])) for a in _licas_created(ctx)
    ]
    expected = [(str(creative["id"]), str(_line_item_created(ctx)["id"]), _size_counts(parse_sizes(sizes or "")))]
    assert held == expected, f"expected GAM to hold {expected}; it holds {held}"


def _size_counts(sizes: list[Any]) -> Counter[tuple[int, int]]:
    """Each (width, height) of *sizes*, counted: the sizes as a multiset."""
    return Counter((int(size["width"]), int(size["height"])) for size in sizes)


@then(
    parsers.re(
        r"Google Ad Manager serves that creative on "
        r'(?:the "(?P<product_id>[^"]+)" package\'s line item|line item "(?P<line_item>\d+)") '
        r'at sizes "(?P<sizes>[^"]+)" only$'
    )
)
def then_gam_serves_creative_on_package_line_item_at(
    ctx: dict, product_id: str | None, line_item: str | None, sizes: str
) -> None:
    """On the line item of *product_id*'s package, or on *line_item*, the one creative GAM created serves at *sizes*.

    Ad Manager API v202602 ``LineItemCreativeAssociation.sizes``: "Overrides the value set for
    Creative.size". So the sizes a creative serves at on a line item are its association's
    ``sizes``, or the creative's own ``size`` when the association states none. One
    association of the creative on that line item. A buy GAM created names the line item by
    its package's product; a buy the scenario booked names it by the id it stated.
    """
    (creative,) = _creatives_created(ctx)
    line_item_id = line_item or str(_line_item_for(ctx, str(product_id))["id"])
    served = [
        list(a.get("sizes") or [creative["size"]])
        for a in _licas_created(ctx)
        if (str(a["creativeId"]), str(a["lineItemId"])) == (str(creative["id"]), line_item_id)
    ]
    assert served == [parse_sizes(sizes)], (
        f"expected creative {creative['id']} to serve at {sizes} on line item {line_item_id}; GAM serves it at {served}"
    )


@then(parsers.parse('creative "{creative_id}" failed with error {code} and recovery {recovery}'))
def then_creative_failed_with(ctx: dict, creative_id: str, code: str, recovery: str) -> None:
    """The buyer's entry for the creative is ``failed``, and carries exactly this one code and recovery."""
    entry = wire_entry(ctx, "creatives", creative_id=creative_id)
    errors = wire_entry_errors(ctx, "creatives", creative_id=creative_id)
    reported = (entry.get("action"), [(error.get("code"), error.get("recovery")) for error in errors])
    assert reported == ("failed", [(code, recovery)]), f"expected {code} {recovery} on a failed entry; got {entry}"


@then(parsers.parse('Google Ad Manager holds one creative, showing "{url}"'))
def then_gam_holds_one_creative(ctx: dict, url: str) -> None:
    """Of everything the seller sent, GAM holds exactly one creative, and it shows *url*.

    Read from what GAM holds now, so it also grades an update: a creative the seller updated
    in place is still the one creative, showing the new content, and one it did not update
    still shows the old.
    """
    (held,) = _creatives_created(ctx)
    assert held.get("imageUrl") == url, f"expected GAM to hold the creative showing {url}; it holds {held}"
    ctx["gam_creative_last_read"] = _modification_of(held)


def _modification_of(creative: Mapping[str, Any]) -> tuple[str, Any, Any]:
    """(id, ``lastModifiedDateTime``, image URL) of a GAM creative: what an update would change.

    A copy of the values, never the object: the stand-in updates the creative it holds in place.
    """
    return str(creative["id"]), creative.get("lastModifiedDateTime"), creative.get("imageUrl")


@then("Google Ad Manager still holds that creative as it last read it, unmodified")
def then_gam_creative_unmodified(ctx: dict) -> None:
    """The one creative GAM holds was not modified since "holds one creative" last read it: nothing more was sent.

    Ad Manager API v202602 ``Creative.lastModifiedDateTime``: "The date and time this creative
    was last modified. This attribute is assigned by Google and is readonly." An
    ``updateCreatives`` re-sending the same content leaves ``imageUrl`` as it was and moves
    this stamp, on the stand-in (which stamps each update) and on a live network alike. The
    stamp read must be there, or the comparison would hold whatever was sent. Ceiling: a live
    network stamps to the second, so an update within the same second as the read is not seen.
    """
    last_read = ctx["gam_creative_last_read"]
    assert last_read[1] is not None, f"GAM held no lastModifiedDateTime on the creative it last read: {last_read}"
    (held,) = _creatives_created(ctx)
    assert _modification_of(held) == last_read, (
        f"expected GAM to hold the creative as it last read it, {last_read}; it holds {_modification_of(held)}"
    )


@then(
    parsers.re(
        r'Google Ad Manager holds that creative on line items "(?P<line_items>[^"]+)" as (?P<status>serving|not serving)$'
    )
)
def then_gam_association_serving(ctx: dict, line_items: str, status: str) -> None:
    """Each association of the one creative GAM created is ACTIVE (serving) or INACTIVE, line items in order."""
    (creative,) = _creatives_created(ctx)
    expected = [
        (line_item.strip(), "ACTIVE" if status == "serving" else "INACTIVE") for line_item in line_items.split(",")
    ]
    held = [
        (str(a["lineItemId"]), a.get("status", "ACTIVE"))
        for a in _licas_created(ctx)
        if str(a["creativeId"]) == str(creative["id"])
    ]
    assert held == expected, f"expected GAM to hold that creative as {expected}; it holds {held}"


@then("Google Ad Manager was asked to stop serving that creative, then to serve it again")
def then_gam_serving_actions(ctx: dict) -> None:
    """GAM received exactly a deactivate and then an activate of the associations of the one creative it created."""
    (creative,) = _creatives_created(ctx)
    sent = [
        (action["xsi_type"], [v["value"]["value"] for v in statement["values"] if v["key"] == "creativeId"])
        for action, statement in ctx["env"].gam_actions_performed(
            "LineItemCreativeAssociationService", "performLineItemCreativeAssociationAction"
        )
    ]
    expected = [
        ("DeactivateLineItemCreativeAssociations", [int(creative["id"])]),
        ("ActivateLineItemCreativeAssociations", [int(creative["id"])]),
    ]
    assert sent == expected, f"expected GAM to receive {expected}; it received {sent}"


@then("Google Ad Manager was asked to change no association's serving")
def then_gam_no_serving_change(ctx: dict) -> None:
    """Nothing reached GAM's association activate or deactivate action."""
    calls = ctx["env"].gam_actions_performed(
        "LineItemCreativeAssociationService", "performLineItemCreativeAssociationAction"
    )
    assert calls == [], f"expected no association action; GAM received {calls}"


@then("Google Ad Manager created no creative")
def then_gam_created_no_creative(ctx: dict) -> None:
    """Nothing reached GAM's CreativeService."""
    created = _creatives_created(ctx)
    assert created == [], f"expected GAM to create no creative; it created {created}"


def _licas_created(ctx: dict) -> list[Any]:
    """The line item creative associations the stand-in GAM network created, in order."""
    return ctx["env"].gam_objects_created("LineItemCreativeAssociationService", "createLineItemCreativeAssociations")


def _associations(ctx: dict) -> list[tuple[str, str]]:
    """(creative id, line item id) of each line item creative association GAM created, in order."""
    return [(str(a["creativeId"]), str(a["lineItemId"])) for a in _licas_created(ctx)]


@then(parsers.re(r'Google Ad Manager associated that creative with line items "(?P<line_items>[^"]+)"$'))
def then_gam_associated_with_line_items(ctx: dict, line_items: str) -> None:
    """GAM holds the one creative it created on exactly these line items, each once, in any order."""
    created = _creatives_created(ctx)
    assert len(created) == 1, f"'that creative' names one creative; GAM created {len(created)}"
    expected = [(str(created[0]["id"]), line_item.strip()) for line_item in line_items.split(",")]
    _assert_holds_exactly(_associations(ctx), expected, "the associations")


@then(parsers.parse('Google Ad Manager associated that creative with line item "{line_item}" at weight {weight:d}'))
def then_gam_associated_at_weight(ctx: dict, line_item: str, weight: int) -> None:
    """The association carries the assignment's weight as GAM's manual rotation weight."""
    reported = [(str(a["lineItemId"]), a.get("manualCreativeRotationWeight")) for a in _licas_created(ctx)]
    assert reported == [(line_item, weight)], f"expected one association at weight {weight}; GAM holds {reported}"


@then(parsers.parse('Google Ad Manager holds line item "{line_item}" on manual creative rotation'))
def then_gam_line_item_manual_rotation(ctx: dict, line_item: str) -> None:
    """GAM holds the line item on MANUAL rotation (``LineItem.creativeRotationType``), which it needs to honour weights."""
    rotation = ctx["env"].gam_line_item(line_item).get("creativeRotationType")
    assert rotation == "MANUAL", f"expected GAM to hold line item {line_item} on MANUAL rotation; it holds {rotation}"


@then("Google Ad Manager changed no line item")
def then_gam_changed_no_line_item(ctx: dict) -> None:
    """Nothing reached GAM's line item update."""
    updated = ctx["env"].gam_objects_sent("LineItemService", "updateLineItems")
    assert updated == [], f"expected GAM to receive no line item update; it received {updated}"


@then(
    parsers.re(
        r'^Google Ad Manager received one line item whose geo targeting includes "(?P<targeted>[^"]+)" '
        r'and excludes "(?P<excluded>[^"]*)"$'
    )
)
def then_one_line_item_geo_targeting(ctx: dict, targeted: str, excluded: str) -> None:
    """The seller sent exactly one line item, whose geoTargeting holds exactly these GAM location ids.

    *targeted* and *excluded* are comma-separated GAM geo target ids; an empty *excluded*
    means the line item carries no excludedLocations. GAM ORs the ids of a location list,
    so their order carries nothing and they are compared as sets: an extra location (a
    union of inclusion fields) or a missing one fails.
    """
    line_items = [
        {
            key: {str(location["id"]) for location in locations}
            # A line item with no geoTargeting reads as {}, which no expectation equals.
            for key, locations in item["targeting"].get("geoTargeting", {}).items()
        }
        for item in ctx["env"].gam_objects_sent("LineItemService", "createLineItems")
    ]
    expected = {"targetedLocations": set(targeted.split(","))}
    if excluded:
        expected["excludedLocations"] = set(excluded.split(","))
    assert line_items == [expected], (
        f"expected GAM to receive one line item geo-targeted {expected}; it received {line_items}"
    )


#: The line item columns the booking Then compares, each read off the line item the seller sent.
_LINE_ITEM_READERS = {
    "lineItemType": lambda item: item["lineItemType"],
    "priority": lambda item: item["priority"],
    "costType": lambda item: item["costType"],
    "costPerUnitMicros": lambda item: item["costPerUnit"]["microAmount"],
    "goalType": lambda item: item["primaryGoal"]["goalType"],
    "unitType": lambda item: item["primaryGoal"]["unitType"],
    "units": lambda item: item["primaryGoal"]["units"],
}

#: The columns whose cells are integers in GAM's line item.
_INTEGER_COLUMNS = frozenset({"priority", "costPerUnitMicros", "units"})


@then("Google Ad Manager received these line items, in package order:")
def then_line_items_in_package_order(ctx: dict, datatable: Any) -> None:
    """The seller sent GAM exactly these line items, one per package in the request's order.

    Columns: ``lineItemType | priority | costType | costPerUnitMicros | goalType | unitType |
    units``; ``costPerUnitMicros`` is the ``microAmount`` of the line item's ``costPerUnit``,
    the price GAM bills each unit (or, for CPD, each day) at. The whole list is compared, so
    a missing, extra or reordered line item fails it. An integer column is compared with its
    type, so a float goal (GAM's ``Goal.units`` is a long) fails it.
    """
    expected = [
        {column: (int, int(cell)) if column in _INTEGER_COLUMNS else cell for column, cell in row.items()}
        for row in table_rows(datatable)
    ]
    sent = [
        {
            column: (type(value), value) if column in _INTEGER_COLUMNS else value
            for column, value in ((column, read(item)) for column, read in _LINE_ITEM_READERS.items())
        }
        for item in ctx["env"].gam_objects_sent("LineItemService", "createLineItems")
    ]
    assert sent == expected, (
        f"expected GAM to receive line items {expected}; it received {sent} (approval: {ctx.get('approval_result')})"
    )


@then("the seller's approval books the media buy")
def then_approval_books_media_buy(ctx: dict) -> None:
    """The approval ran the adapter to the end, and no buy of the tenant awaits approval any longer."""
    result = ctx["approval_result"]
    assert result.ok, f"expected the seller's approval to book the media buy; it returned {result}"
    pending = ctx["env"].media_buys_awaiting_approval()
    assert pending == [], f"expected no media buy to await approval; {len(pending)} still do"


def assert_media_buy_still_awaits_approval(ctx: dict) -> None:
    """The one media buy the scenario held is still ``pending_approval``, read off the stored row."""
    pending = ctx["env"].media_buys_awaiting_approval()
    assert len(pending) == 1, f"expected the media buy to still await approval; {len(pending)} buys do"


@then("the seller's approval fails on the package's missing pricing_info, and the media buy still awaits approval")
def then_approval_fails_on_missing_pricing_info(ctx: dict) -> None:
    """The approval failed naming the stored package's missing ``pricing_info``, and left the buy awaiting approval.

    A store defect the buyer cannot correct: CONFIGURATION_ERROR, its ``field`` the stored
    field at fault, as ``media_buy_delivery`` refuses the same missing terms.
    """
    error = failed_approval_error(ctx, "CONFIGURATION_ERROR")
    assert error.field == "pricing_info", f"expected the refusal to name pricing_info; it names {error.field!r}"
    assert_media_buy_still_awaits_approval(ctx)


@then("the media buy still awaits approval")
def then_media_buy_still_awaits_approval(ctx: dict) -> None:
    """A refused approval booked nothing, so the buy is left awaiting approval for the seller to retry."""
    assert_media_buy_still_awaits_approval(ctx)


def failed_approval_error(ctx: dict, code: str) -> Any:
    """The typed error the seller's approval failed with (``ApprovalResult.error``), asserted to carry *code*."""
    from src.core.tools.media_buy_create import ApprovalOutcome

    result = ctx["approval_result"]
    error = result.error
    assert result.outcome is ApprovalOutcome.FAILED and error is not None and str(error.error_code) == code, (
        f"expected the seller's approval to fail with {code}; it returned {result}"
    )
    return error


@then(parsers.parse('the seller\'s approval fails with error code "{code}"'))
def then_approval_fails_with_code(ctx: dict, code: str) -> None:
    """The approval failed, and its typed error carries the code."""
    failed_approval_error(ctx, code)


@then(parsers.parse('the seller\'s approval fails with error code "{code}" naming field "{field}"'))
def then_approval_fails_with_code_naming_field(ctx: dict, code: str, field: str) -> None:
    """The approval failed with the code, its ``error.field`` pointing where the stored buy names the subject.

    The same pointer a create answers with: the approval's refusal is the one object a
    pre-booking refusal is built as (``_execute_adapter_media_buy_creation``).
    """
    error = failed_approval_error(ctx, code)
    assert error.field == field, f"expected the seller's approval to name field {field!r}; it names {error.field!r}"


@then("Google Ad Manager received no order and no line item")
def then_no_order_and_no_line_item(ctx: dict) -> None:
    """The seller sent GAM no order and no line item: the refusal left nothing behind in GAM."""
    sent = {
        method: ctx["env"].gam_objects_sent(service, method)
        for service, method in (("OrderService", "createOrders"), ("LineItemService", "createLineItems"))
    }
    assert sent == {"createOrders": [], "createLineItems": []}, f"expected GAM to receive nothing; it received {sent}"


@then(
    parsers.re(
        r'Google Ad Manager associated (?:creative "(?P<gam_id>\d+)"|no creative) with the line item it created$'
    )
)
def then_gam_associated_held_creative_with_created_line_item(ctx: dict, gam_id: str | None) -> None:
    """GAM holds the stated creative on the one line item it created, once, and nothing else; or nothing at all."""
    expected = [(gam_id, str(_line_item_created(ctx)["id"]))] if gam_id else []
    assert _associations(ctx) == expected, f"expected GAM to hold {expected}; it holds {_associations(ctx)}"


@then("Google Ad Manager associated each creative it created with the line item it created")
def then_gam_associated_each_creative_with_created_line_item(ctx: dict) -> None:
    """Each creative GAM created is on the one line item GAM created, once, in creation order."""
    line_item_id = str(_line_item_created(ctx)["id"])
    expected = [(str(c["id"]), line_item_id) for c in _creatives_created(ctx)]
    assert _associations(ctx) == expected, f"expected GAM to hold {expected}; it holds {_associations(ctx)}"


@then("Google Ad Manager was asked to approve the order it created, on booking and on the creative's approval")
def then_gam_approved_created_order_twice(ctx: dict) -> None:
    """Both approvals the seller sent GAM name the one order GAM created."""
    (order,) = _orders_created(ctx)
    approvals = [
        str(bound["value"]["value"])
        for action, statement in ctx["env"].gam_actions_performed("OrderService", "performOrderAction")
        if action["xsi_type"].startswith("Approve")
        for bound in statement["values"]
        if bound["key"] == "orderId"
    ]
    assert approvals == [str(order["id"])] * 2, f"expected two approvals of order {order['id']}; GAM got {approvals}"
