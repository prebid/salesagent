"""Factory_boy factories for user and tenant auth config models.

Factories: UserFactory, TenantAuthConfigFactory
"""

from __future__ import annotations

import factory
from factory import LazyAttribute, Sequence, SubFactory

from src.core.database.models import TenantAuthConfig, User
from tests.factories.core import TenantFactory


class UserFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = User
        sqlalchemy_session = None  # Bound dynamically by IntegrationEnv / factory fixture
        sqlalchemy_session_persistence = "commit"

    tenant = SubFactory(TenantFactory)
    tenant_id = LazyAttribute(lambda o: o.tenant.tenant_id)
    user_id = Sequence(lambda n: f"user_{n:04d}")
    email = Sequence(lambda n: f"user_{n:04d}@example.com")
    name = LazyAttribute(lambda o: f"User {o.user_id}")
    role = "viewer"
    is_active = True


class TenantAuthConfigFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = TenantAuthConfig
        sqlalchemy_session = None
        sqlalchemy_session_persistence = "commit"

    tenant = SubFactory(TenantFactory)
    tenant_id = LazyAttribute(lambda o: o.tenant.tenant_id)
    oidc_enabled = False
    oidc_provider = "google"
    oidc_discovery_url = "https://accounts.google.com/.well-known/openid-configuration"
    oidc_client_id = Sequence(lambda n: f"client-id-{n:04d}.apps.googleusercontent.com")
    oidc_scopes = "openid email profile"
