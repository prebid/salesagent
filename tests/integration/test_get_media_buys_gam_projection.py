"""Integration tests for the GAM-orders → media_buys projection.

When an operator assigns a buyer agent to a ``GamAdvertiser`` via the
buyer-routing UI, ``get_media_buys`` should surface that advertiser's
GAM orders as projected media buys for the assigned agent. Unassigned
advertisers stay invisible to all buyers.
"""

from __future__ import annotations

import pytest
from adcp.types.generated_poc.enums.media_buy_status import MediaBuyStatus

from src.core.schemas import GetMediaBuysRequest
from src.core.tools.media_buy_list import _get_media_buys_impl
from tests.factories import GAMLineItemFactory, PrincipalFactory
from tests.integration._gam_projection_helpers import (
    build_assigned_order_scenario,
    make_identity,
)

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


class TestProjectionVisibility:
    """Projection respects advertiser→agent assignment."""

    def test_assigned_advertiser_orders_appear_for_owner(self, factory_session):
        sc = build_assigned_order_scenario(line_item_count=1)

        result = _get_media_buys_impl(
            req=GetMediaBuysRequest(),
            identity=make_identity(sc.tenant.tenant_id, sc.principal.principal_id),
        )

        ids = [mb.media_buy_id for mb in result.media_buys]
        assert f"gam_{sc.order.order_id}" in ids

    def test_unassigned_advertiser_orders_not_visible(self, factory_session):
        sc = build_assigned_order_scenario(advertiser_principal_id=None)

        result = _get_media_buys_impl(
            req=GetMediaBuysRequest(),
            identity=make_identity(sc.tenant.tenant_id, sc.principal.principal_id),
        )

        ids = [mb.media_buy_id for mb in result.media_buys]
        assert f"gam_{sc.order.order_id}" not in ids

    def test_other_principals_advertiser_orders_not_visible(self, factory_session):
        sc = build_assigned_order_scenario()
        outsider = PrincipalFactory(tenant=sc.tenant)

        result = _get_media_buys_impl(
            req=GetMediaBuysRequest(),
            identity=make_identity(sc.tenant.tenant_id, outsider.principal_id),
        )

        ids = [mb.media_buy_id for mb in result.media_buys]
        assert f"gam_{sc.order.order_id}" not in ids


class TestProjectionStatusMapping:
    """GAM order status maps to AdCP MediaBuyStatus correctly."""

    @pytest.mark.parametrize(
        "gam_status,expected",
        [
            ("DRAFT", MediaBuyStatus.pending_start),
            ("PENDING_APPROVAL", MediaBuyStatus.pending_start),
            ("DISAPPROVED", MediaBuyStatus.rejected),
            ("PAUSED", MediaBuyStatus.paused),
            ("CANCELED", MediaBuyStatus.canceled),
            ("ARCHIVED", MediaBuyStatus.canceled),
        ],
    )
    def test_terminal_status_short_circuits(self, factory_session, gam_status, expected):
        sc = build_assigned_order_scenario(order_status=gam_status)

        result = _get_media_buys_impl(
            req=GetMediaBuysRequest(status_filter=expected),
            identity=make_identity(sc.tenant.tenant_id, sc.principal.principal_id),
        )

        ids = [mb.media_buy_id for mb in result.media_buys]
        assert f"gam_{sc.order.order_id}" in ids
        projected = next(mb for mb in result.media_buys if mb.media_buy_id == f"gam_{sc.order.order_id}")
        assert projected.status == expected

    def test_approved_active_dates_yields_active(self, factory_session):
        sc = build_assigned_order_scenario()

        result = _get_media_buys_impl(
            req=GetMediaBuysRequest(),
            identity=make_identity(sc.tenant.tenant_id, sc.principal.principal_id),
        )

        projected = next((mb for mb in result.media_buys if mb.media_buy_id == f"gam_{sc.order.order_id}"), None)
        assert projected is not None
        assert projected.status == MediaBuyStatus.active


class TestProjectionPackages:
    """Line items project as packages with stable ids."""

    def test_line_items_appear_as_packages(self, factory_session):
        sc = build_assigned_order_scenario()
        li1 = GAMLineItemFactory(tenant=sc.tenant, order_id=sc.order.order_id, status="DELIVERING")
        li2 = GAMLineItemFactory(tenant=sc.tenant, order_id=sc.order.order_id, status="DELIVERING")

        result = _get_media_buys_impl(
            req=GetMediaBuysRequest(),
            identity=make_identity(sc.tenant.tenant_id, sc.principal.principal_id),
        )

        projected = next(mb for mb in result.media_buys if mb.media_buy_id == f"gam_{sc.order.order_id}")
        package_ids = sorted([p.package_id for p in projected.packages])
        assert package_ids == sorted([f"gam_li_{li1.line_item_id}", f"gam_li_{li2.line_item_id}"])


class TestProjectionExtGam:
    """Projected entries surface ext.gam so buyers can distinguish them."""

    def test_projected_buy_has_ext_gam(self, factory_session):
        sc = build_assigned_order_scenario(line_item_count=1)

        result = _get_media_buys_impl(
            req=GetMediaBuysRequest(),
            identity=make_identity(sc.tenant.tenant_id, sc.principal.principal_id),
        )

        projected = next(mb for mb in result.media_buys if mb.media_buy_id == f"gam_{sc.order.order_id}")
        assert projected.ext == {
            "gam": {
                "imported": True,
                "order_id": sc.order.order_id,
                "advertiser_id": sc.advertiser.advertiser_id,
            }
        }

    def test_projected_package_has_ext_gam_with_line_item_id(self, factory_session):
        sc = build_assigned_order_scenario()
        li = GAMLineItemFactory(tenant=sc.tenant, order_id=sc.order.order_id, status="DELIVERING")

        result = _get_media_buys_impl(
            req=GetMediaBuysRequest(),
            identity=make_identity(sc.tenant.tenant_id, sc.principal.principal_id),
        )

        projected = next(mb for mb in result.media_buys if mb.media_buy_id == f"gam_{sc.order.order_id}")
        package = next(p for p in projected.packages if p.package_id == f"gam_li_{li.line_item_id}")
        assert package.ext == {
            "gam": {
                "imported": True,
                "line_item_id": li.line_item_id,
                "order_id": sc.order.order_id,
                "line_item_status": "DELIVERING",
            }
        }
