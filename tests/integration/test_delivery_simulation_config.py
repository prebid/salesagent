"""Integration coverage for server-side delivery seeding (#1418).

Real Postgres. Verifies (a) the repository get/upsert + tenant isolation, and
(b) the Mock adapter returns the seeded payload when a row exists for its
(tenant, media_buy). These exercise the same object the harness builds, so
in-process and e2e return byte-identical delivery numbers by construction.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session as SASession

from src.adapters.mock_ad_server import MockAdServer
from src.core.database.database_session import get_engine
from src.core.database.repositories import DeliverySimulationConfigRepository
from src.core.schemas import Principal
from src.core.schemas.delivery import (
    AdapterGetMediaBuyDeliveryResponse,
    AdapterPackageDelivery,
    DeliveryTotals,
    ReportingPeriod,
)

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


def _seeded_response(media_buy_id: str, impressions: int = 5000) -> AdapterGetMediaBuyDeliveryResponse:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return AdapterGetMediaBuyDeliveryResponse(
        media_buy_id=media_buy_id,
        reporting_period=ReportingPeriod(start=start, end=start + timedelta(days=7)),
        totals=DeliveryTotals(impressions=impressions, spend=impressions * 0.01, clicks=impressions / 200),
        by_package=[AdapterPackageDelivery(package_id="pkg_001", impressions=impressions, spend=impressions * 0.01)],
        currency="USD",
    )


@pytest.fixture
def bound_session(integration_db):
    """A session bound to the test DB with all factories registered to it."""
    from tests.factories import ALL_FACTORIES

    engine = get_engine()
    session = SASession(bind=engine)
    previous = [f._meta.sqlalchemy_session for f in ALL_FACTORIES]
    for f in ALL_FACTORIES:
        f._meta.sqlalchemy_session = session
    try:
        yield session
    finally:
        for f, prev in zip(ALL_FACTORIES, previous, strict=True):
            f._meta.sqlalchemy_session = prev
        session.rollback()
        session.close()


class TestRepository:
    def test_upsert_then_get_roundtrips_payload(self, bound_session):
        from tests.factories import TenantFactory

        tenant = TenantFactory()
        repo = DeliverySimulationConfigRepository(bound_session, tenant.tenant_id)

        payload = _seeded_response("mb_repo").model_dump(mode="json")
        repo.upsert("mb_repo", payload)
        bound_session.commit()

        row = repo.get("mb_repo")
        assert row is not None
        assert row.tenant_id == tenant.tenant_id
        assert row.media_buy_id == "mb_repo"
        assert row.response_payload == payload

    def test_upsert_updates_existing_row(self, bound_session):
        from tests.factories import TenantFactory

        tenant = TenantFactory()
        repo = DeliverySimulationConfigRepository(bound_session, tenant.tenant_id)

        first = repo.upsert("mb_update", _seeded_response("mb_update", impressions=1000).model_dump(mode="json"))
        bound_session.commit()
        second = repo.upsert("mb_update", _seeded_response("mb_update", impressions=9000).model_dump(mode="json"))
        bound_session.commit()

        # Same PK -> same row mutated, not a second insert.
        assert first is second
        assert repo.get("mb_update").response_payload["totals"]["impressions"] == 9000

    def test_tenant_isolation(self, bound_session):
        """A row for tenant A is invisible to a repo scoped to tenant B."""
        from tests.factories import TenantFactory

        tenant_a = TenantFactory()
        tenant_b = TenantFactory()
        DeliverySimulationConfigRepository(bound_session, tenant_a.tenant_id).upsert(
            "mb_shared", _seeded_response("mb_shared").model_dump(mode="json")
        )
        bound_session.commit()

        assert DeliverySimulationConfigRepository(bound_session, tenant_a.tenant_id).get("mb_shared") is not None
        assert DeliverySimulationConfigRepository(bound_session, tenant_b.tenant_id).get("mb_shared") is None


class TestFactory:
    def test_factory_writes_row_consumable_by_repository(self, bound_session):
        from tests.factories import DeliverySimulationConfigFactory, TenantFactory

        tenant = TenantFactory()
        payload = _seeded_response("mb_factory", impressions=7777).model_dump(mode="json")
        DeliverySimulationConfigFactory(
            tenant=tenant,
            media_buy_id="mb_factory",
            response_payload=payload,
        )
        bound_session.commit()

        row = DeliverySimulationConfigRepository(bound_session, tenant.tenant_id).get("mb_factory")
        assert row is not None
        assert row.response_payload["totals"]["impressions"] == 7777


class TestAdapterReadsSeededRow:
    def test_adapter_returns_seeded_response(self, bound_session):
        """MockAdServer.get_media_buy_delivery returns the seeded row verbatim."""
        from tests.factories import DeliverySimulationConfigFactory, TenantFactory

        tenant = TenantFactory()
        seeded = _seeded_response("mb_adapter", impressions=12345)
        DeliverySimulationConfigFactory(
            tenant=tenant,
            media_buy_id="mb_adapter",
            response_payload=seeded.model_dump(mode="json"),
        )
        bound_session.commit()

        principal = Principal(principal_id="p_int", name="Int Principal", platform_mappings={})
        adapter = MockAdServer(config={}, principal=principal, tenant_id=tenant.tenant_id)
        # No in-memory entry: if the DB read failed, this would hit the random fallback.
        adapter._media_buys = {}

        today = datetime(2026, 1, 5, tzinfo=UTC)
        result = adapter.get_media_buy_delivery(
            "mb_adapter",
            ReportingPeriod(start=datetime(2026, 1, 1, tzinfo=UTC), end=today),
            today,
        )

        assert result.totals.impressions == 12345
        assert result.by_package[0].package_id == "pkg_001"
        assert result.by_package[0].impressions == 12345
        assert result.currency == "USD"

    def test_adapter_falls_through_when_no_row(self, bound_session):
        """No seeded row -> existing fallback path (empty by_package)."""
        from tests.factories import TenantFactory

        tenant = TenantFactory()
        bound_session.commit()

        principal = Principal(principal_id="p_int2", name="Int Principal 2", platform_mappings={})
        adapter = MockAdServer(config={}, principal=principal, tenant_id=tenant.tenant_id)
        adapter._media_buys = {}

        today = datetime(2026, 1, 5, tzinfo=UTC)
        result = adapter.get_media_buy_delivery(
            "mb_absent",
            ReportingPeriod(start=datetime(2026, 1, 1, tzinfo=UTC), end=today),
            today,
        )

        assert result.media_buy_id == "mb_absent"
        assert result.by_package == []
