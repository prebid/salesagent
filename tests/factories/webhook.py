"""Factory_boy factories for webhook-related models.

Includes the buyer-protocol :class:`PushNotificationConfig` factory and the
Sprint 6 :class:`WebhookSubscription` factory used by Tenant Management API
integration tests.
"""

from __future__ import annotations

import factory
from factory import LazyAttribute, Sequence, SubFactory

from src.core.database.models import PushNotificationConfig, WebhookSubscription
from src.core.database.repositories.webhook_subscription import generate_secret, hash_secret
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
    signing_mode = "hmac"


class WebhookSubscriptionFactory(factory.alchemy.SQLAlchemyModelFactory):
    """Sprint 6 outbound webhook subscription factory.

    The plaintext secret is generated and stored on the factory under
    ``_plaintext_secret`` so tests can read it back to compute expected
    HMAC signatures. The DB only carries the hash.

    The ``tenant`` parameter is a factory-only convenience: tests pass
    ``tenant=some_tenant`` and ``tenant_id`` resolves from it. The model
    itself has no ``tenant`` relationship, so the field is excluded from
    instantiation via the ``exclude`` Meta.
    """

    class Meta:
        model = WebhookSubscription
        sqlalchemy_session = None
        sqlalchemy_session_persistence = "commit"
        exclude = ("_plaintext_secret", "tenant")

    tenant = SubFactory(TenantFactory)
    tenant_id = LazyAttribute(lambda o: o.tenant.tenant_id)

    webhook_id = Sequence(lambda n: f"wh_test_{n:04d}")
    url = factory.LazyFunction(lambda: "https://receiver.example.com/webhook")
    event_types = factory.LazyFunction(list)
    description = "test subscription"
    extra_headers = None
    is_active = True
    consecutive_failures = 0
    last_delivery_at = None
    last_delivery_status = None

    _plaintext_secret = factory.LazyFunction(generate_secret)
    secret_hash = LazyAttribute(lambda o: hash_secret(o._plaintext_secret))
