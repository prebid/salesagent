"""Steps for what a Google Ad Manager seller sends GAM.

Serves local-gam-seller.feature (create_media_buy on ``MediaBuyCreateEnv``) and
local-uc006-gam-assignment-trafficking.feature (sync_creatives and the seller's creative
approval on ``CreativeSyncEnv``). The seller Given makes the env's tenant a GAM seller
(``GamSellerMixin.sell_through_gam``). The Thens read two things: the buyer's wire response,
and the stand-in GAM network's record of what the seller sent it and what it created. Their
expectations are the scenario's literals, or links the stand-in itself established (the ids
it gave the order and the creative), so nothing is compared with what the harness seeded.
"""

from __future__ import annotations

from typing import Any

from pytest_bdd import given, parsers, then, when

from tests.bdd.steps._outcome_helpers import wire_field
from tests.bdd.steps.domain.uc006_sync_creatives import _ask_assignment_terms, when_sync_creative_with_assignments
from tests.bdd.steps.generic._account_resolution import ensure_tenant_principal


@given(
    parsers.parse(
        'the seller sells through Google Ad Manager as trafficker "{trafficker_id}" on ad unit '
        '"{ad_unit_id}", and the buyer is its advertiser "{advertiser_id}"'
    )
)
def given_seller_sells_through_gam(ctx: dict, trafficker_id: str, ad_unit_id: str, advertiser_id: str) -> None:
    """Onboard the env's tenant to GAM and sell the scenario's product on one ad unit.

    An env that seeds no product (``CreativeSyncEnv``) gets one here, with the default
    pricing option, so its booked buys are on a product the seller sells.
    """
    if "default_product" not in ctx:
        from tests.factories import PricingOptionFactory, ProductFactory

        ensure_tenant_principal(ctx, ctx["env"])
        ctx["default_product"] = ProductFactory(tenant=ctx["tenant"])
        PricingOptionFactory(product=ctx["default_product"])
    ctx["env"].sell_through_gam(
        trafficker_id=trafficker_id,
        advertiser_id=advertiser_id,
        ad_unit_id=ad_unit_id,
        products=[ctx["default_product"]],
    )


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
    from tests.bdd.steps.generic.given_media_buy import _add_creative_ids_to_package

    _library_image_creative(ctx, ready_id, ready_url)
    _library_image_creative(ctx, held_id, held_url, status="pending_review")
    ctx["env"]._commit_factory_data()
    _add_creative_ids_to_package(ctx, [ready_id, held_id])


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
    from tests.bdd.steps.generic.given_media_buy import _add_creative_ids_to_package

    _library_image_creative(
        ctx,
        creative_id,
        url,
        status="approved" if status == "approved" else "pending_review",
        ad_server_creative_id=gam_id,
    )
    ctx["env"]._commit_factory_data()
    _add_creative_ids_to_package(ctx, [creative_id])


def _library_image_creative(
    ctx: dict, creative_id: str, url: str, *, status: str = "approved", principal: Any = None, **data: Any
) -> None:
    """A 300x250 image creative stored in the library, in the reference format the GAM product sells.

    The buyer's own unless *principal* names another; *data* adds keys to its stored record.
    """
    from tests.factories.creative_asset import image_spec

    _library_creative(
        ctx,
        creative_id,
        "display_300x250_image",
        image_spec("banner_image", url=url),
        status=status,
        principal=principal,
        **data,
    )


def _library_creative(
    ctx: dict,
    creative_id: str,
    format_id: str,
    asset: Any,
    *,
    status: str = "approved",
    principal: Any = None,
    **data: Any,
) -> None:
    """A creative stored in the library with one asset, in reference-catalog format *format_id*."""
    from tests.factories.creative import CreativeFactory
    from tests.factories.creative_asset import build_assets

    CreativeFactory(
        creative_id=creative_id,
        tenant=ctx["tenant"],
        principal=principal or ctx["principal"],
        format=format_id,
        status=status,
        data={"assets": build_assets(asset), **data},
    )


@given(
    parsers.parse(
        'another buyer of the seller holds an approved 300x250 image creative "{creative_id}" showing "{url}"'
    )
)
def given_another_buyer_holds_creative(ctx: dict, creative_id: str, url: str) -> None:
    """A second principal of the tenant, with a creative under an id the buyer also uses."""
    from tests.factories import PrincipalFactory

    _library_image_creative(ctx, creative_id, url, principal=PrincipalFactory(tenant=ctx["tenant"]))
    ctx["env"]._commit_factory_data()


@given(
    parsers.parse(
        'the buyer holds an approved 300x250 image creative "{creative_id}" showing "{url}", '
        "recorded with its own id as the ad server's"
    )
)
def given_buyer_holds_creative_recorded_with_own_id(ctx: dict, creative_id: str, url: str) -> None:
    """A library creative whose record holds the buyer's id under ``platform_creative_id``, as #2247 wrote it."""
    _library_image_creative(ctx, creative_id, url, platform_creative_id=creative_id)
    ctx["env"]._commit_factory_data()


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
    from tests.factories.creative_asset import asset_spec

    _library_creative(
        ctx,
        creative_id,
        "display_300x250_html",
        asset_spec("html_creative", "html", content=markup),
        url=preview_url,
        width=300,
        height=250,
    )
    ctx["env"]._commit_factory_data()


@given(parsers.parse('the seller\'s ad server is now "{adapter_type}", which takes no creatives from the seller'))
def given_seller_ad_server_takes_no_creatives(ctx: dict, adapter_type: str) -> None:
    """The tenant's adapter becomes *adapter_type*; its packages keep the line item ids they store."""
    tenant = ctx["tenant"]
    tenant.ad_server = adapter_type
    tenant.adapter_config.adapter_type = adapter_type
    ctx["env"]._commit_factory_data()


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


# ═══════════════════════════════════════════════════════════════════════
# sync_creatives onto buys already booked in GAM
# (local-uc006-gam-assignment-trafficking.feature, on CreativeSyncEnv)
# ═══════════════════════════════════════════════════════════════════════


@given("the creative agent serves the reference creative formats")
def given_creative_agent_serves_reference_formats(ctx: dict) -> None:
    """The creative agent answers a lookup of a reference-catalog format with that format."""
    ctx["env"].serve_reference_formats()


@given(
    parsers.re(
        r"(?P<owner>the buyer|another buyer of the seller) holds a buy booked in Google Ad Manager as "
        r'order "(?P<order_id>\d+)"'
        r'(?: in status "(?P<status>[^"]+)")? whose package "(?P<package>[^"]+)" is line item '
        r'"(?P<line_item>\d+)" accepting (?P<width>\d+)x(?P<height>\d+)$'
    )
)
def given_buyer_holds_booked_buy(
    ctx: dict, owner: str, order_id: str, status: str | None, package: str, line_item: str, width: str, height: str
) -> None:
    """A buy booked in GAM, the buyer's or a second GAM advertiser's: its one package is the stated line item."""
    from tests.factories import PrincipalFactory

    principal = None
    if owner != "the buyer":
        # A buy booked in GAM implies a GAM advertiser.
        principal = PrincipalFactory(
            tenant=ctx["tenant"], platform_mappings={"google_ad_manager": {"advertiser_id": "3000002"}}
        )
    ctx["env"].book_in_gam(
        principal=principal,
        package_id=package,
        line_item_id=line_item,
        size=(int(width), int(height)),
        order_id=order_id,
        product=ctx["default_product"],
        status=status or "pending_creatives",
    )


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
    from tests.factories.creative_asset import build_assets, image_spec
    from tests.factories.request import CreativeAssetRequestFactory

    return CreativeAssetRequestFactory.payload(
        creative_id=creative_id,
        name=f"Creative {creative_id}",
        format_id={"agent_url": DEFAULT_AGENT_URL, "id": "display_300x250_image"},
        assets=build_assets(image_spec("banner_image", url=url)),
    )


@given(
    parsers.re(
        r'the buyer (?P<tense>syncs|synced) a 300x250 image creative "(?P<creative_id>[^"]+)" showing '
        r'"(?P<url>[^"]+)" assigned to package "(?P<package>[^"]+)"'
        r"(?: with weight (?P<weight>\d+))?(?P<dry_run> as a dry run)?$"
    )
)
def given_buyer_syncs_image_creative(
    ctx: dict, tense: str, creative_id: str, url: str, package: str, weight: str | None, dry_run: str | None
) -> None:
    """The buyer's sync of one image creative assigned to one package: sent now ("synced"), or by the When.

    A sync sent now goes through the scenario's transport like the When's, and must have
    succeeded, so a later step grades what the seller did with it rather than a refusal.
    """
    ctx["creatives"] = [_image_creative(creative_id, url)]
    ctx["assignments"] = {creative_id: [package]}
    if weight is not None:
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
    'references library creative "{creative_id}", without resending it, assigned to package "{package}"'
)


@when(parsers.parse("the Buyer Agent " + _REFERENCES_LIBRARY_CREATIVE))
def when_buyer_references_library_creative(ctx: dict, creative_id: str, package: str) -> None:
    """Sync an assignment of a library creative without resending its content.

    ``creatives`` is required (sync-creatives-request.json, minItems 1), so it carries one
    other creative, assigned nowhere.
    """
    ctx["creatives"] = [_image_creative(f"{creative_id}-unassigned", "https://example.com/unassigned.png")]
    ctx["assignments"] = {creative_id: [package]}
    when_sync_creative_with_assignments(ctx)


@when(parsers.parse('the seller approves creative "{creative_id}"'))
def when_seller_approves_creative(ctx: dict, creative_id: str) -> None:
    """The seller approves the creative in the Admin UI's review queue."""
    ctx["env"].approve_creative_as_seller(creative_id)


def _creatives_created(ctx: dict) -> list[Any]:
    """The creatives the stand-in GAM network created, each carrying the id it assigned."""
    return ctx["env"].gam_objects_created("CreativeService", "createCreatives")


def _line_item_created(ctx: dict) -> Any:
    """The one line item the stand-in GAM network created, carrying the id it assigned."""
    line_items = ctx["env"].gam_objects_created("LineItemService", "createLineItems")
    assert len(line_items) == 1, f"'the line item it created' names one line item; GAM created {len(line_items)}"
    return line_items[0]


def _assert_creatives_created(ctx: dict, expected: list[tuple[str, str, str | None, str | None]]) -> None:
    """GAM created exactly *expected*: (type, advertiser id, image URL, snippet) of each creative, in order."""
    created = [
        (c["xsi_type"], str(c["advertiserId"]), c.get("imageUrl"), c.get("snippet")) for c in _creatives_created(ctx)
    ]
    assert created == expected, f"expected GAM to create {expected}; it created {created}"


@then(
    parsers.re(
        r"Google Ad Manager created (?:one image creative|image creatives) for advertiser "
        r'"(?P<advertiser_id>\d+)" showing "(?P<urls>[^"]+)"$'
    )
)
def then_gam_created_image_creatives(ctx: dict, advertiser_id: str, urls: str) -> None:
    """GAM holds exactly these creatives from this scenario, in order: images for the advertiser, showing the URLs."""
    _assert_creatives_created(
        ctx, [("ImageRedirectCreative", advertiser_id, url.strip(), None) for url in urls.split(",")]
    )


@then(
    parsers.parse(
        'Google Ad Manager created one third-party creative for advertiser "{advertiser_id}" whose snippet is '
        '"{snippet}" and which names no image URL'
    )
)
def then_gam_created_one_third_party_creative(ctx: dict, advertiser_id: str, snippet: str) -> None:
    """GAM holds exactly one creative from this scenario: the markup itself, not a picture of a page."""
    _assert_creatives_created(ctx, [("ThirdPartyCreative", advertiser_id, None, snippet)])


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
    """GAM holds the one creative it created on exactly these line items, each once, in this order."""
    created = _creatives_created(ctx)
    assert len(created) == 1, f"'that creative' names one creative; GAM created {len(created)}"
    expected = [(str(created[0]["id"]), line_item.strip()) for line_item in line_items.split(",")]
    assert _associations(ctx) == expected, f"expected GAM to hold {expected}; it holds {_associations(ctx)}"


@then(parsers.parse('Google Ad Manager associated that creative with line item "{line_item}" at weight {weight:d}'))
def then_gam_associated_at_weight(ctx: dict, line_item: str, weight: int) -> None:
    """The association carries the assignment's weight as GAM's manual rotation weight."""
    reported = [(str(a["lineItemId"]), a.get("manualCreativeRotationWeight")) for a in _licas_created(ctx)]
    assert reported == [(line_item, weight)], f"expected one association at weight {weight}; GAM holds {reported}"


@then(parsers.parse('Google Ad Manager set line item "{line_item}" to manual creative rotation'))
def then_gam_line_item_manual_rotation(ctx: dict, line_item: str) -> None:
    """The seller updated the line item to MANUAL rotation, which GAM needs to honour weights."""
    updated = ctx["env"].gam_objects_sent("LineItemService", "updateLineItems")
    reported = [(str(item["id"]), item["creativeRotationType"]) for item in updated]
    assert reported == [(line_item, "MANUAL")], f"expected line item {line_item} set to MANUAL; GAM got {reported}"


@then("Google Ad Manager changed no line item")
def then_gam_changed_no_line_item(ctx: dict) -> None:
    """Nothing reached GAM's line item update."""
    updated = ctx["env"].gam_objects_sent("LineItemService", "updateLineItems")
    assert updated == [], f"expected GAM to receive no line item update; it received {updated}"


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
    order_actions = ctx["env"].mock["gam_client"].return_value.GetService("OrderService").performOrderAction
    approvals = [
        str(bound["value"]["value"])
        for action, statement in (call.args for call in order_actions.call_args_list)
        if action["xsi_type"].startswith("Approve")
        for bound in statement["values"]
        if bound["key"] == "orderId"
    ]
    assert approvals == [str(order["id"])] * 2, f"expected two approvals of order {order['id']}; GAM got {approvals}"
