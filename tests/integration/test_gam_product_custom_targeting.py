"""A GAM product's custom targeting reaches the line item in the shape GAM accepts (#1034).

``implementation_config.custom_targeting_keys`` is internal and never crosses the AdCP wire,
so no BDD scenario or storyboard can see it: the only observable is what the adapter sends
GAM. These tests drive the real adapter against a real database, with only the GAM client
stood in, and read ``customTargeting`` off the line item handed to ``createLineItems``.

GAM allows a custom targeting tree three levels deep, with an OR at the top: an OR set of
AND sets of ``CustomCriteria`` (API v202602, ``LineItemService.Targeting.customTargeting``).
Buyer and product targeting are read into one model of that shape (``CustomTargeting``),
ANDed there, and rendered once.
"""

from datetime import UTC, datetime, timedelta

import pytest

from src.adapters.base import AdapterCreateRequest
from src.core.exceptions import AdCPAdapterError, AdCPValidationError
from src.core.schemas import MediaPackage, Principal, Targeting
from tests.factories import ProductFactory, TenantFactory
from tests.factories.request import CreateMediaBuyRequestFactory, PackageRequestFactory
from tests.helpers.gam_client import (
    build_gam_adapter,
    gam_criteria_set,
    gam_criterion,
    stub_gam_client_manager,
)

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

#: What the admin targeting widget saves: OR'd groups of AND'd criteria, GAM ids throughout.
WIDGET_GROUPS = {
    "groups": [
        {
            "criteria": [
                {"keyId": "11001", "values": ["448512", "448513"]},
                {"keyId": "11002", "values": ["448601"], "exclude": True},
            ]
        },
        {"criteria": [{"keyId": "11003", "values": ["448701"]}]},
    ]
}

#: Values the stand-in GAM holds by name, for the paths that resolve names to ids.
SPORTS, PREMIUM = 448001, 557002
GAM_VALUES = {(11001, "sports"): SPORTS, (22002, "premium"): PREMIUM}


def _create_media_buy(client_manager, custom_targeting_keys: dict, targeting_overlay: Targeting | None = None) -> None:
    """Create a one-package media buy, through ``client_manager``, for a product carrying ``custom_targeting_keys``."""
    tenant = TenantFactory()
    product = ProductFactory(
        tenant=tenant,
        delivery_type="non_guaranteed",
        implementation_config={
            "targeted_ad_unit_ids": ["23312403859"],
            "creative_placeholders": [{"width": 300, "height": 250}],
            "custom_targeting_keys": custom_targeting_keys,
        },
    )
    start_time = datetime.now(UTC) + timedelta(days=1)
    end_time = start_time + timedelta(days=7)
    request = AdapterCreateRequest.from_buyer_request(
        CreateMediaBuyRequestFactory.build(
            packages=[PackageRequestFactory.build(product_id=product.product_id)],
            start_time=start_time,
            end_time=end_time,
        )
    )
    package = MediaPackage(
        package_id="pkg_001",
        name="Package 1",
        delivery_type="non_guaranteed",
        cpm=5.0,
        impressions=10000,
        format_ids=[],
        product_id=product.product_id,
        targeting_overlay=targeting_overlay,
    )

    adapter = build_gam_adapter(
        client_manager,
        principal=Principal(
            principal_id="test_principal",
            name="Test Principal",
            platform_mappings={"google_ad_manager": {"advertiser_id": "789"}},
        ),
        config={"network_code": "123456", "trafficker_id": "456", "refresh_token": "test_token"},
        tenant_id=tenant.tenant_id,
    )
    pricing = {"pricing_model": "cpm", "rate": 5.0, "currency": "USD", "is_fixed": True, "bid_price": None}
    adapter.create_media_buy(request, [package], start_time, end_time, package_pricing_info={"pkg_001": pricing})


def _custom_targeting_sent_to_gam(custom_targeting_keys: dict, targeting_overlay: Targeting | None = None) -> dict:
    """The ``customTargeting`` of the one line item a media buy for the product sent GAM."""
    client_manager = stub_gam_client_manager(custom_targeting_values=GAM_VALUES)
    _create_media_buy(client_manager, custom_targeting_keys, targeting_overlay)
    [create_call] = client_manager.get_service("LineItemService").createLineItems.call_args_list
    [line_item] = create_call.args[0]
    return line_item["targeting"]["customTargeting"]


def test_widget_groups_on_a_product_reach_gam_as_criteria_sets(bound_factory_session):
    """The groups format the admin widget saves used to reach GAM as a raw "groups" field."""
    sent = _custom_targeting_sent_to_gam(WIDGET_GROUPS)

    assert sent == gam_criteria_set(
        "OR",
        [
            gam_criteria_set("AND", [gam_criterion(11001, [448512, 448513]), gam_criterion(11002, [448601], "IS_NOT")]),
            gam_criteria_set("AND", [gam_criterion(11003, [448701])]),
        ],
    )


def test_legacy_key_value_on_a_product_resolves_the_value_name(bound_factory_session):
    """A legacy ``{key_id: value_name}`` product config is resolved to GAM value ids.

    The builder makes a bare AND set of it; GAM takes only an OR at the top, so it is sent
    as an OR holding that one AND set.
    """
    sent = _custom_targeting_sent_to_gam({"11001": "sports"})

    assert sent == gam_criteria_set("OR", [gam_criteria_set("AND", [gam_criterion(11001, [SPORTS])])])


def test_buyer_and_product_targeting_are_both_required(bound_factory_session):
    """Buyer AND product targeting, distributed over the product's OR'd groups.

    Wrapping the two trees in one AND set would put an AND above an OR, which GAM refuses,
    so ``buyer AND (group1 OR group2)`` reaches GAM as ``(buyer AND group1) OR (buyer AND group2)``.
    """
    buyer_overlay = Targeting(custom={"gam": {"key_values": {"22002": "premium"}}})

    sent = _custom_targeting_sent_to_gam(WIDGET_GROUPS, targeting_overlay=buyer_overlay)

    buyer = gam_criterion(22002, [PREMIUM])
    assert sent == gam_criteria_set(
        "OR",
        [
            gam_criteria_set(
                "AND", [buyer, gam_criterion(11001, [448512, 448513]), gam_criterion(11002, [448601], "IS_NOT")]
            ),
            gam_criteria_set("AND", [buyer, gam_criterion(11003, [448701])]),
        ],
    )


def test_targeting_gam_refuses_leaves_no_order_behind(bound_factory_session):
    """Package targeting is built before the order, so a refused value creates no empty order."""
    client_manager = stub_gam_client_manager(custom_targeting_values=GAM_VALUES)

    with pytest.raises(AdCPAdapterError):
        _create_media_buy(client_manager, {"11001": "a value GAM does not hold"})

    assert client_manager.get_service("OrderService").createOrders.call_args_list == []


def test_a_buyer_key_that_is_not_a_gam_key_id_is_the_buyers_to_correct(bound_factory_session):
    """Buyer key-values are read into the same model, so a key GAM cannot take is refused before any order."""
    client_manager = stub_gam_client_manager(custom_targeting_values=GAM_VALUES)
    buyer_overlay = Targeting(custom={"gam": {"key_values": {"sport": "football"}}})

    with pytest.raises(AdCPValidationError):
        _create_media_buy(client_manager, WIDGET_GROUPS, targeting_overlay=buyer_overlay)

    assert client_manager.get_service("OrderService").createOrders.call_args_list == []


def test_a_name_and_the_id_it_resolves_to_reach_gam_once(bound_factory_session):
    """A converted legacy name can sit beside the ID the widget picks for it; GAM gets the ID once."""
    sent = _custom_targeting_sent_to_gam(
        {"groups": [{"criteria": [{"keyId": "11001", "values": [{"name": "sports"}, str(SPORTS)]}]}]}
    )

    assert sent == gam_criteria_set("OR", [gam_criteria_set("AND", [gam_criterion(11001, [SPORTS])])])
