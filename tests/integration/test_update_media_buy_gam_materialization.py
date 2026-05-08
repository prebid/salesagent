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

    def test_first_update_writes_materialization_audit_log(self, factory_session):
        """First update_media_buy on a projected id writes an audit entry.

        Direct ``materialize_projected_buy`` calls don't audit (caller
        decides) — the audit fires from ``_update_media_buy_impl`` after
        its UoW commits.
        """
        from src.core.database.models import AuditLog

        sc = build_assigned_order_scenario()
        GAMLineItemFactory(tenant=sc.tenant, order_id=sc.order.order_id)
        factory_session.commit()

        # No-op update triggers materialization without rejecting.
        _update_media_buy_impl(
            req=UpdateMediaBuyRequest(media_buy_id=f"gam_{sc.order.order_id}"),
            identity=make_identity(sc.tenant.tenant_id, sc.principal.principal_id),
        )

        with get_db_session() as session:
            entries = session.scalars(
                select(AuditLog)
                .where(AuditLog.tenant_id == sc.tenant.tenant_id)
                .where(AuditLog.operation == "AdCP.materialize_imported_buy")
            ).all()

        assert len(entries) == 1
        entry = entries[0]
        assert entry.principal_id == sc.principal.principal_id
        assert entry.success is True
        assert entry.details["gam_order_id"] == sc.order.order_id
        assert entry.details["media_buy_id"] == f"gam_{sc.order.order_id}"
        assert entry.details["source"] == "gam_import"

    def test_audit_log_fires_even_on_mutation_rejection(self, factory_session):
        """Materialization audit fires even when the update mutation is rejected.

        Materialization happens read-side; the rejection just blocks the
        adapter writeback. The audit record of "agent X claimed order Y"
        stands either way.
        """
        from src.core.database.models import AuditLog

        sc = build_assigned_order_scenario()
        GAMLineItemFactory(tenant=sc.tenant, order_id=sc.order.order_id)
        factory_session.commit()

        result = _update_media_buy_impl(
            req=UpdateMediaBuyRequest(media_buy_id=f"gam_{sc.order.order_id}", paused=True),
            identity=make_identity(sc.tenant.tenant_id, sc.principal.principal_id),
        )
        assert isinstance(result, UpdateMediaBuyError)

        with get_db_session() as session:
            entries = session.scalars(
                select(AuditLog)
                .where(AuditLog.tenant_id == sc.tenant.tenant_id)
                .where(AuditLog.operation == "AdCP.materialize_imported_buy")
            ).all()
        assert len(entries) == 1

    def test_audit_log_does_not_fire_on_subsequent_updates(self, factory_session):
        """Once materialized, subsequent update_media_buy calls don't re-audit."""
        from src.core.database.models import AuditLog

        sc = build_assigned_order_scenario()
        GAMLineItemFactory(tenant=sc.tenant, order_id=sc.order.order_id)
        factory_session.commit()

        # First call materializes + audits
        _update_media_buy_impl(
            req=UpdateMediaBuyRequest(media_buy_id=f"gam_{sc.order.order_id}"),
            identity=make_identity(sc.tenant.tenant_id, sc.principal.principal_id),
        )
        # Second call hits the existing row — should not audit again
        _update_media_buy_impl(
            req=UpdateMediaBuyRequest(media_buy_id=f"gam_{sc.order.order_id}"),
            identity=make_identity(sc.tenant.tenant_id, sc.principal.principal_id),
        )

        with get_db_session() as session:
            entries = session.scalars(
                select(AuditLog)
                .where(AuditLog.tenant_id == sc.tenant.tenant_id)
                .where(AuditLog.operation == "AdCP.materialize_imported_buy")
            ).all()
        assert len(entries) == 1


class TestMaterializedBuyExtGam:
    """A materialized buy continues to surface ext.gam on subsequent reads."""

    def test_ext_gam_survives_materialization(self, factory_session):
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

        materialized = next(mb for mb in result.media_buys if mb.media_buy_id == f"gam_{sc.order.order_id}")
        assert materialized.ext == {
            "gam": {
                "imported": True,
                "order_id": sc.order.order_id,
                "advertiser_id": sc.advertiser.advertiser_id,
            }
        }


class TestMaterializeRaceResolution:
    """Concurrent materializations of the same gam_<order_id> resolve cleanly.

    The PK ``media_buy_id`` collision triggers IntegrityError on the
    losing inserter; ``materialize_projected_buy`` rolls back and re-
    fetches the winner's row. Both callers end up with the same
    materialized buy, no duplicates.
    """

    def test_pk_collision_returns_existing_row(self, factory_session):
        from src.core.database.database_session import get_engine

        sc = build_assigned_order_scenario(line_item_count=1)
        factory_session.commit()

        # Simulate a winning concurrent transaction by writing the row
        # ourselves on a separate session, mid-call.
        engine = get_engine()
        from sqlalchemy.orm import Session as SASession

        with SASession(bind=engine) as winner_session:
            materialize_projected_buy(
                winner_session,
                sc.tenant.tenant_id,
                sc.principal.principal_id,
                f"gam_{sc.order.order_id}",
            )
            winner_session.commit()

        # Loser tries to materialize the same order on a fresh session.
        # IntegrityError fires on flush; the function rolls back and
        # returns the existing winner row.
        with SASession(bind=engine) as loser_session:
            result = materialize_projected_buy(
                loser_session,
                sc.tenant.tenant_id,
                sc.principal.principal_id,
                f"gam_{sc.order.order_id}",
            )

            assert result is not None
            assert result.media_buy_id == f"gam_{sc.order.order_id}"
            assert result.principal_id == sc.principal.principal_id
            assert result.source == "gam_import"

        # Exactly one row exists.
        with get_db_session() as session:
            rows = session.scalars(select(MediaBuy).where(MediaBuy.media_buy_id == f"gam_{sc.order.order_id}")).all()
            assert len(rows) == 1


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
