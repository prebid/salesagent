"""Factory_boy factory for InventoryReviewState model."""

from __future__ import annotations

import factory
from factory import LazyAttribute, Sequence, SubFactory

from src.core.database.models import InventoryReviewState
from tests.factories.core import TenantFactory


class InventoryReviewStateFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = InventoryReviewState
        sqlalchemy_session = None
        sqlalchemy_session_persistence = "commit"

    tenant = SubFactory(TenantFactory)
    tenant_id = LazyAttribute(lambda o: o.tenant.tenant_id)
    adapter = "gam"
    entity_type = "ad_unit"
    external_id = Sequence(lambda n: f"adunit_{n:06d}")
    status = "pending"
    reviewed_at = None
    reviewed_by = None
