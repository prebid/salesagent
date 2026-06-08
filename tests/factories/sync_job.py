"""Factory_boy factory for SyncJob model."""

from __future__ import annotations

from datetime import UTC, datetime

import factory
from factory import LazyAttribute, LazyFunction, Sequence, SubFactory

from src.core.database.models import SyncJob
from tests.factories.core import TenantFactory


class SyncJobFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = SyncJob
        sqlalchemy_session = None
        sqlalchemy_session_persistence = "commit"

    tenant = SubFactory(TenantFactory)
    tenant_id = LazyAttribute(lambda o: o.tenant.tenant_id)
    sync_id = Sequence(lambda n: f"sync_{n:08d}")
    adapter_type = "google_ad_manager"
    sync_type = "order_approval"
    status = "running"
    started_at = LazyFunction(lambda: datetime.now(UTC))
    triggered_by = "test"
    triggered_by_id = LazyAttribute(lambda o: f"trigger_{o.sync_id}")
    progress = LazyFunction(
        lambda: {
            "order_id": "order_test",
            "media_buy_id": "mb_test",
            "principal_id": "principal_test",
            "attempts": 0,
            "max_attempts": 12,
            "phase": "Starting approval polling",
        }
    )
