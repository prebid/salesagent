"""Factory_boy factory for PushNotificationConfig model."""

from __future__ import annotations

import factory
from factory import LazyAttribute, Sequence, SubFactory

from src.core.database.models import PushNotificationConfig
from tests.factories.core import TenantFactory
from tests.factories.principal import PrincipalFactory


class PushNotificationConfigFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = PushNotificationConfig
        sqlalchemy_session = None
        sqlalchemy_session_persistence = "commit"

    tenant = SubFactory(TenantFactory)
    principal = SubFactory(PrincipalFactory, tenant=factory.SelfAttribute("..tenant"))

    id = Sequence(lambda n: f"webhook_{n:04d}")
    tenant_id = LazyAttribute(lambda o: o.tenant.tenant_id)
    principal_id = LazyAttribute(lambda o: o.principal.principal_id)
    url = factory.LazyFunction(lambda: "https://example.com/webhook")
    is_active = True
