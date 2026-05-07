"""Integration tests for materializing projected GAM orders on update_media_buy.

When a buyer calls ``update_media_buy`` with a projected ``gam_<order_id>``
id, the impl materializes a real ``media_buys`` row (with ``source =
'gam_import'`` and ``external_id = order_id``) plus matching
``media_packages`` rows from the synced GAM line items, then proceeds
with the normal update flow.

After materialization, subsequent calls find the existing row and skip
re-materialization. The projection in get_media_buys also skips orders
that have already been materialized.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import MediaBuy, MediaPackage
from src.core.exceptions import AdCPAuthorizationError
from src.core.schemas import GetMediaBuysRequest, UpdateMediaBuyError, UpdateMediaBuyRequest
from src.core.tools._gam_projection import (
    is_projected_media_buy_id,
    materialize_projected_buy,
)
from src.core.tools.media_buy_list import _get_media_buys_impl
from src.core.tools.media_buy_update import _update_media_buy_impl
from tests.factories import GAMLineItemFactory, PrincipalFactory
from tests.integration._gam_projection_helpers import (
    build_assigned_order_scenario,
    make_identity,
)

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


class TestProjectedIdHelpers:
    def test_is_projected_id_recognizes_order_prefix(self):
        assert is_projected_media_buy_id("gam_12345")
        assert is_projected_media_buy_id("gam_order_abc")

    def test_is_projected_id_rejects_line_item_prefix(self):
        assert not is_projected_media_buy_id("gam_li_67890")

    def test_is_projected_id_rejects_native(self):
        assert not is_projected_media_buy_id("mb_abcdef123456")


class TestMaterializeProjectedBuy:
    def test_creates_media_buy_row_with_source_and_external_id(self, factory_session):
        sc = build_assigned_order_scenario(line_item_count=2)

        materialized = materialize_projected_buy(
            factory_session,
            sc.tenant.tenant_id,
            sc.principal.principal_id,
            f"gam_{sc.order.order_id}",
        )

        assert materialized.media_buy_id == f"gam_{sc.order.order_id}"
        assert materialized.external_id == sc.order.order_id
        assert materialized.source == "gam_import"
        assert materialized.principal_id == sc.principal.principal_id
        assert materialized.tenant_id == sc.tenant.tenant_id

        packages = factory_session.scalars(
            select(MediaPackage).where(MediaPackage.media_buy_id == materialized.media_buy_id)
        ).all()
        assert len(packages) == 2

    def test_rejects_caller_not_assigned_to_advertiser(self, factory_session):
        sc = build_assigned_order_scenario()
        outsider = PrincipalFactory(tenant=sc.tenant)

        with pytest.raises(AdCPAuthorizationError):
            materialize_projected_buy(
                factory_session,
                sc.tenant.tenant_id,
                outsider.principal_id,
                f"gam_{sc.order.order_id}",
            )

    def test_rejects_unknown_order_id(self, factory_session):
        sc = build_assigned_order_scenario()

        with pytest.raises(AdCPAuthorizationError):
            materialize_projected_buy(
                factory_session,
                sc.tenant.tenant_id,
                sc.principal.principal_id,
                "gam_does_not_exist",
            )


class TestProjectionSkipsMaterialized:
    """Once an order is materialized, the projection should not double-count it."""

    def test_materialized_order_appears_once(self, factory_session):
        sc = build_assigned_order_scenario(line_item_count=1)

        materialize_projected_buy(
            factory_session,
            sc.tenant.tenant_id,
            sc.principal.principal_id,
            f"gam_{sc.order.order_id}",
        )
        factory_session.commit()

        result = _get_media_buys_impl(
            req=GetMediaBuysRequest(),
            identity=make_identity(sc.tenant.tenant_id, sc.principal.principal_id),
        )

        ids = [mb.media_buy_id for mb in result.media_buys]
        assert ids.count(f"gam_{sc.order.order_id}") == 1


class TestUpdateMediaBuyRejectsMutatingImportedBuy:
    """Mutating fields on imported buys are rejected until adapter writeback exists."""

    def test_pause_on_imported_buy_returns_not_implemented(self, factory_session):
        sc = build_assigned_order_scenario()
        GAMLineItemFactory(tenant=sc.tenant, order_id=sc.order.order_id)
        factory_session.commit()

        result = _update_media_buy_impl(
            req=UpdateMediaBuyRequest(media_buy_id=f"gam_{sc.order.order_id}", paused=True),
            identity=make_identity(sc.tenant.tenant_id, sc.principal.principal_id),
        )

        assert isinstance(result, UpdateMediaBuyError)
        assert result.errors[0].code == "not_implemented"

        # Materialization happened despite the rejection — first read locked
        # the buyer's ownership, so a follow-up call doesn't re-materialize.
        with get_db_session() as session:
            row = session.scalars(select(MediaBuy).filter_by(media_buy_id=f"gam_{sc.order.order_id}")).first()
            assert row is not None
            assert row.source == "gam_import"


class TestUpdateMediaBuyMaterializesOnFirstWrite:
    """update_media_buy with a projected id materializes the buy."""

    def test_first_update_materializes(self, factory_session):
        sc = build_assigned_order_scenario()
        GAMLineItemFactory(tenant=sc.tenant, order_id=sc.order.order_id)
        factory_session.commit()

        # Sanity: no media_buys row yet
        with get_db_session() as session:
            assert session.scalars(select(MediaBuy).filter_by(media_buy_id=f"gam_{sc.order.order_id}")).first() is None

        # No-op update — just trigger materialization
        _update_media_buy_impl(
            req=UpdateMediaBuyRequest(media_buy_id=f"gam_{sc.order.order_id}"),
            identity=make_identity(sc.tenant.tenant_id, sc.principal.principal_id),
        )

        # Now there's a real row
        with get_db_session() as session:
            row = session.scalars(select(MediaBuy).filter_by(media_buy_id=f"gam_{sc.order.order_id}")).first()
            assert row is not None
            assert row.source == "gam_import"
            assert row.external_id == sc.order.order_id
            assert row.principal_id == sc.principal.principal_id
