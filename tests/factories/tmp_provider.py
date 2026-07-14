"""Factory_boy factory for TMPProvider model."""

from __future__ import annotations

import uuid

import factory
from factory import LazyAttribute, Sequence, SubFactory

from src.core.database.models import TMPProvider
from tests.factories.core import TenantFactory


class TMPProviderFactory(factory.alchemy.SQLAlchemyModelFactory):
    """Factory for TMPProvider ORM instances.

    Creates active providers by default.  Override ``status``, ``priority``,
    ``tenant_id`` etc. as needed for specific test scenarios.

    Note: ``provider_id`` uses a server-default (gen_random_uuid()) in
    production, so we generate a UUID client-side here to avoid a round-trip.
    """

    class Meta:
        model = TMPProvider
        sqlalchemy_session = None
        sqlalchemy_session_persistence = "commit"
        exclude = ["tenant"]

    tenant = SubFactory(TenantFactory)

    provider_id = factory.LazyFunction(lambda: str(uuid.uuid4()))
    tenant_id = LazyAttribute(lambda o: o.tenant.tenant_id)
    name = Sequence(lambda n: f"Provider {n:04d}")
    endpoint = LazyAttribute(lambda o: f"https://{o.name.lower().replace(' ', '-')}.example.com/tmp")
    context_match = True
    identity_match = False
    countries = None
    uid_types = None
    properties = None
    timeout_ms = 200
    priority = 0
    status = "active"
    auth_type = None
    # _auth_credentials is the raw column; leave None (no encryption in factory)
    _auth_credentials = None
    health_status = None
    last_health_checked_at = None
