"""Factory_boy factories for core tenant-related models.

Factories: TenantFactory, CurrencyLimitFactory, PropertyTagFactory, PublisherPartnerFactory
Helpers: set_adapter_test_behavior (persist adapter test-behavior to AdapterConfig)
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import factory
from factory import LazyAttribute, RelatedFactory, Sequence, SubFactory

from src.core.database.models import (
    AdapterConfig,
    AuthorizedProperty,
    CurrencyLimit,
    GAMInventory,
    PropertyTag,
    PublisherPartner,
    Tenant,
)


def subdomain_for(tenant_id: str) -> str:
    """Single source of truth for a tenant's DNS subdomain.

    DNS labels cannot contain underscores, so tenant_id underscores map to
    hyphens. This MUST be the only derivation: the persisted ``Tenant.subdomain``
    (ORM factory) and the ``ResolvedIdentity`` tenant dict (``make_tenant``) have
    to agree, because the e2e_rest transport authenticates by sending this
    subdomain as the ``x-adcp-tenant`` header and the live server resolves the
    tenant from it. A mismatch (underscore in the DB row vs hyphen on the wire)
    makes the server fail to resolve the tenant and return 401 — which silently
    parked every e2e_rest delivery scenario on the known-failures ledger.
    """
    return f"pub-{tenant_id}".replace("_", "-")


class TenantFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = Tenant
        sqlalchemy_session = None  # Bound dynamically by IntegrationEnv
        sqlalchemy_session_persistence = "commit"

    tenant_id = Sequence(lambda n: f"tenant_{n:04d}")
    name = LazyAttribute(lambda o: f"Test Publisher {o.tenant_id}")
    subdomain = LazyAttribute(lambda o: subdomain_for(o.tenant_id))
    is_active = True
    billing_plan = "standard"
    ad_server = "mock"
    authorized_emails = factory.LazyFunction(lambda: ["test@example.com"])
    authorized_domains = factory.LazyFunction(lambda: ["example.com"])

    @classmethod
    def make_tenant(cls, tenant_id: str = "test_tenant", **overrides: Any) -> dict[str, Any]:
        """Build a tenant dict without DB persistence.

        Uses same defaults as TenantFactory fields.
        Pass **overrides for domain fields (approval_mode, gemini_api_key, etc).
        """
        subdomain = subdomain_for(tenant_id)
        tenant: dict[str, Any] = {
            "tenant_id": tenant_id,
            "name": f"Test Publisher {tenant_id}",
            "subdomain": subdomain,
            "ad_server": "mock",
        }
        tenant.update(overrides)
        return tenant

    # Auto-create required CurrencyLimit (USD) for budget validation
    currency_usd = RelatedFactory(
        "tests.factories.core.CurrencyLimitFactory",
        factory_related_name="tenant",
    )


class CurrencyLimitFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = CurrencyLimit
        sqlalchemy_session = None
        sqlalchemy_session_persistence = "commit"

    tenant = SubFactory(TenantFactory)
    tenant_id = LazyAttribute(lambda o: o.tenant.tenant_id)
    currency_code = "USD"
    min_package_budget = Decimal("100.00")


class PublisherPartnerFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = PublisherPartner
        sqlalchemy_session = None
        sqlalchemy_session_persistence = "commit"

    tenant = SubFactory(TenantFactory)
    tenant_id = LazyAttribute(lambda o: o.tenant.tenant_id)
    publisher_domain = Sequence(lambda n: f"publisher-{n:04d}.com")
    display_name = LazyAttribute(lambda o: f"Publisher {o.publisher_domain}")
    is_verified = True
    sync_status = "success"


class AuthorizedPropertyFactory(factory.alchemy.SQLAlchemyModelFactory):
    """A verified authorized property — satisfies the create_media_buy setup
    checklist's "Authorized Properties" gate (SetupChecklistService counts
    AuthorizedProperty rows for the tenant). The in-process transports skip the
    gate via the testing context; the live e2e_rest server enforces it, so a
    fully-set-up tenant needs at least one of these.
    """

    class Meta:
        model = AuthorizedProperty
        sqlalchemy_session = None
        sqlalchemy_session_persistence = "commit"

    tenant = SubFactory(TenantFactory)
    tenant_id = LazyAttribute(lambda o: o.tenant.tenant_id)
    property_id = Sequence(lambda n: f"prop_{n:04d}")
    property_type = "website"
    name = LazyAttribute(lambda o: f"Authorized Property {o.property_id}")
    publisher_domain = Sequence(lambda n: f"authorized-{n:04d}.example.com")
    identifiers = LazyAttribute(lambda o: [{"type": "domain", "value": o.publisher_domain}])
    verification_status = "verified"


class AdapterConfigFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = AdapterConfig
        sqlalchemy_session = None
        sqlalchemy_session_persistence = "commit"

    tenant = SubFactory(TenantFactory)
    tenant_id = LazyAttribute(lambda o: o.tenant.tenant_id)
    adapter_type = "mock"


class GAMInventoryFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = GAMInventory
        sqlalchemy_session = None
        sqlalchemy_session_persistence = "commit"

    tenant = SubFactory(TenantFactory)
    tenant_id = LazyAttribute(lambda o: o.tenant.tenant_id)
    inventory_type = "ad_unit"
    inventory_id = Sequence(lambda n: f"au_{n:04d}")
    name = LazyAttribute(lambda o: f"Ad Unit {o.inventory_id}")
    path = LazyAttribute(lambda o: [o.name])
    status = "ACTIVE"
    inventory_metadata = LazyAttribute(
        lambda o: {
            "parent_id": None,
            "has_children": False,
            "ad_unit_code": f"code_{o.inventory_id}",
            "sizes": [{"width": 300, "height": 250}],
        }
    )


class PropertyTagFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = PropertyTag
        sqlalchemy_session = None
        sqlalchemy_session_persistence = "commit"

    tenant = SubFactory(TenantFactory)
    tenant_id = LazyAttribute(lambda o: o.tenant.tenant_id)
    tag_id = Sequence(lambda n: f"tag_{n:04d}")
    name = LazyAttribute(lambda o: f"Tag {o.tag_id}")
    description = LazyAttribute(lambda o: f"Description for {o.name}")


def set_adapter_test_behavior(env: Any, tenant_id: str, **behavior: Any) -> AdapterConfig:
    """Upsert the mock-adapter ``test_behavior`` for a tenant (BDD/E2E support).

    The Docker-hosted mock adapter reads injected behavior — ``manual_approval_required``,
    ``fail_on_create``, ``fail_on_update``, ``error_message``, ``error_details``,
    ``recovery`` — from ``AdapterConfig.config_json["test_behavior"]`` (see
    ``mock_ad_server._read_test_behavior``). In-process transports use the env's
    MagicMock adapter directly and ignore this row; it exists so the same BDD Given
    steps also drive the real adapter over E2E.

    Merges ``behavior`` into any existing ``test_behavior``. Factory-based upsert —
    no raw model construction in step bodies.
    """
    session = env.get_session()
    row = session.get(AdapterConfig, tenant_id)
    if row is None:
        tenant = session.get(Tenant, tenant_id)
        row = AdapterConfigFactory(tenant=tenant, adapter_type="mock")
    config = dict(row.config_json or {})
    test_behavior = dict(config.get("test_behavior", {}))
    test_behavior.update(behavior)
    config["test_behavior"] = test_behavior
    row.config_json = config
    if "manual_approval_required" in behavior:
        # Mirror to the typed column — adapter_helpers reads
        # AdapterConfig.mock_manual_approval_required when constructing the
        # real mock adapter from config (the E2E manual-approval read path).
        row.mock_manual_approval_required = bool(behavior["manual_approval_required"])
    env._commit_factory_data()
    return row
