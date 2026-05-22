"""Factory_boy factory for TenantSignal model."""

from __future__ import annotations

import factory
from factory import LazyAttribute, Sequence, SubFactory

from src.core.database.models import TenantSignal
from tests.factories.core import TenantFactory


class TenantSignalFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = TenantSignal
        sqlalchemy_session = None
        sqlalchemy_session_persistence = "commit"

    tenant = SubFactory(TenantFactory)
    tenant_id = LazyAttribute(lambda o: o.tenant.tenant_id)
    signal_id = Sequence(lambda n: f"audience_signal_{n:04d}")
    name = LazyAttribute(lambda o: f"Signal {o.signal_id}")
    description = LazyAttribute(lambda o: f"Description for {o.name}")
    value_type = "binary"
    categories = factory.LazyFunction(list)
    tags = factory.LazyFunction(list)
    range_min = None
    range_max = None
    adapter_config = factory.LazyFunction(lambda: {"kind": "audience_segment", "segment_id": "12345"})
    data_provider = "publisher_1p"
    targeting_dimension = "audience"
