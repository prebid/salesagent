"""Factory_boy factories for core tenant-related models.

Factories: TenantFactory, CurrencyLimitFactory, PropertyTagFactory, PublisherPartnerFactory
Helpers: set_adapter_test_behavior (persist adapter test-behavior to AdapterConfig),
get_or_create (idempotent factory seeding for shared-DB e2e_rest scenarios)
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
from src.core.database.repositories.adapter_config import AdapterConfigRepository


def get_or_create(env: Any, model: type, filters: dict[str, Any], create: Any):
    """Idempotent factory seeding for shared-DB (e2e_rest) BDD scenarios.

    Over e2e_rest all scenarios share one live-server database, so a prior
    scenario's row (same PK) can survive into this one — the per-scenario
    ``_reset_e2e_db`` and the env's factory session don't reliably target the
    same connection — and a plain factory insert hits a UniqueViolation
    (jdy1-M3, #1418). Look the row up on the env's factory session first and
    reuse it. On in-process transports each test gets a rolled-back DB, the
    lookup misses, and this behaves exactly like calling the factory.

    ``create`` is a zero-arg callable running the factory, so lookup keys and
    factory kwargs stay independent (e.g. Principal filters use ``tenant_id``
    while ``PrincipalFactory`` takes the ``tenant`` object).
    """
    from sqlalchemy import select

    session = getattr(env, "_session", None)
    if session is not None:
        existing = session.scalars(select(model).filter_by(**filters)).first()
        if existing is not None:
            return existing
    return create()


def tenant_subdomain(tenant_id: str) -> str:
    """Derive a tenant's subdomain from its tenant_id.

    Single source of truth for subdomain derivation (#1418). DNS labels cannot
    contain underscores, so tenant_id underscores map to hyphens; the
    normalization is also required because subdomains feed publisher_domain
    (``f"{subdomain}.example.com"``) and the AdCP domain regex rejects
    underscores. This MUST be the only derivation: the persisted
    ``Tenant.subdomain`` (ORM factory) and the ``ResolvedIdentity`` tenant dict
    (``make_tenant``) have to agree, because the e2e_rest transport
    authenticates by sending this subdomain as the ``x-adcp-tenant`` header and
    the live server resolves the tenant from it. A mismatch (underscore in the
    DB row vs hyphen on the wire) makes the server fail to resolve the tenant
    and return 401 — which silently parked every e2e_rest delivery scenario on
    the known-failures ledger.
    """
    return f"pub-{tenant_id}".replace("_", "-")


class TenantFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = Tenant
        sqlalchemy_session = None  # Bound dynamically by IntegrationEnv
        sqlalchemy_session_persistence = "commit"

    tenant_id = Sequence(lambda n: f"tenant_{n:04d}")
    name = LazyAttribute(lambda o: f"Test Publisher {o.tenant_id}")
    subdomain = LazyAttribute(lambda o: tenant_subdomain(o.tenant_id))
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
        subdomain = tenant_subdomain(tenant_id)
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


def set_adapter_test_behavior(env: Any, tenant_id: str, **behavior: Any) -> AdapterConfig:
    """Persist adapter test-behavior to the tenant's AdapterConfig row.

    BDD Given steps configure the in-process mock adapter directly (attribute /
    side_effect on ``env.mock["adapter"]``), then call this helper to mirror the
    same behavior into the database so the DB-backed mock adapter
    (``mock_ad_server._read_test_behavior``) picks up failure/approval injection
    for Docker-hosted / out-of-process transports.

    All keyword behavior flags (``fail_on_create``, ``fail_on_update``,
    ``error_message``, ``error_details``, ``recovery``, ``manual_approval_required``)
    are merged into ``config_json["test_behavior"]`` — accumulating across calls so
    a later flag does not clobber an earlier one. ``manual_approval_required`` is
    additionally written to the ``mock_manual_approval_required`` column, which is
    the read path used when the real mock adapter is constructed from config.

    Args:
        env: Harness environment exposing ``get_session()`` (real-DB envs).
        tenant_id: Tenant whose AdapterConfig to upsert.
        **behavior: Test-behavior flags to persist.
    """
    session = env.get_session()
    repo = AdapterConfigRepository(session, tenant_id)
    row = repo.find_by_tenant()
    if row is None:
        row = AdapterConfig(tenant_id=tenant_id, adapter_type="mock")
        session.add(row)

    # Reassign config_json (rather than mutating in place) so SQLAlchemy's JSON
    # change tracking marks the column dirty and emits an UPDATE.
    config_json = dict(row.config_json or {})
    test_behavior = dict(config_json.get("test_behavior") or {})
    test_behavior.update(behavior)
    config_json["test_behavior"] = test_behavior
    row.config_json = config_json

    if "manual_approval_required" in behavior:
        row.mock_manual_approval_required = bool(behavior["manual_approval_required"])

    session.commit()
    return row


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
