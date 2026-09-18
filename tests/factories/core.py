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
    CreativeAgent,
    CurrencyLimit,
    GAMInventory,
    PropertyTag,
    PublisherPartner,
    SignalsAgent,
    Tenant,
)
from src.core.tenant_context import TenantContext


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
    #: Its OWN column with its OWN sequence, not derived from tenant_id or subdomain.
    #: This directory's guide says why: deriving one independent column from another
    #: invents a shape constraint on the source and then needs normalization and rules to
    #: hold it up — which is exactly the history ``tenant_subdomain`` above carries.
    #:
    #: Set BY DEFAULT so the suite resolves its tenant the way a DEPLOYMENT does: from the
    #: ``Host``, against ``tenants.virtual_host``. Production nginx derives
    #: ``x-adcp-tenant`` from the host (``nginx-multi-tenant.conf``: ``map $host $tenant``)
    #: and no caller sends it, so a suite where every caller asserts the header itself
    #: grades a path no deployment runs. It also left the virtual_host branches unexecuted
    #: — which is how a tenant host reached ``publisher_properties[].publisher_domain``
    #: with a port in it and failed every product, graded by nothing.
    #:
    #: A ``.example.com`` name, so the value is a domain wherever one is expected.
    virtual_host = Sequence(lambda n: f"vhost-{n:04d}.example.com")
    is_active = True
    billing_plan = "standard"
    ad_server = "mock"
    # Auto-approval by default: the Tenant model's column default is True
    # (production-safe), but factory tenants back tests whose success paths
    # assume a synchronous create_media_buy. The live e2e server reads this ROW
    # (in-process transports mock the adapter, so the gate never fires there),
    # and with True every e2e_rest create takes the manual-approval path and
    # returns CreateMediaBuySubmitted with no media_buy_id. Mirrors the seeded
    # dev tenant (src/core/database/database.py human_review_required=False).
    # Manual-approval scenarios opt IN explicitly via Given steps
    # (tests/bdd/steps/generic/given_media_buy.py) or per-test overrides.
    human_review_required = False
    authorized_emails = factory.LazyFunction(lambda: ["test@example.com"])
    authorized_domains = factory.LazyFunction(lambda: ["example.com"])
    # #1592 T1a: no capability blocks declared by default, so a factory tenant
    # reproduces the pre-declaration capabilities wire. Scenarios opt in through
    # CapabilitiesEnv.declare_capabilities(), never by overriding this directly.
    capability_declarations = None

    @classmethod
    def make_tenant(cls, tenant_id: str = "test_tenant", **overrides: Any) -> TenantContext:
        """Build a TenantContext without DB persistence, the type the resolver hands on.

        Uses same defaults as TenantFactory fields.
        Pass **overrides for domain fields (approval_mode, gemini_api_key, etc).
        """
        # Overrides win over the defaults rather than colliding with them. Spelling the
        # defaults as keyword arguments meant passing name=, subdomain= or ad_server=
        # raised "got multiple values for keyword argument", so a caller holding a whole
        # tenant dict could not hand it over — which is what made ``tenant={...}`` a dead
        # end at every call site.
        return TenantContext(
            **{
                "tenant_id": tenant_id,
                "name": f"Test Publisher {tenant_id}",
                "subdomain": tenant_subdomain(tenant_id),
                "ad_server": "mock",
                **overrides,
            }
        )

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


class CreativeAgentFactory(factory.alchemy.SQLAlchemyModelFactory):
    """A stored creative-agent row — the operator configuration a probe dials.

    Every column the dial reads (``auth_type``/``auth_credentials``,
    ``auth_header``, ``timeout``) is a plain default here rather than a
    generated one, so a test that cares about one of them sets exactly that one
    and the rest stay boring.
    """

    class Meta:
        model = CreativeAgent
        sqlalchemy_session = None
        sqlalchemy_session_persistence = "commit"

    tenant = SubFactory(TenantFactory)
    tenant_id = LazyAttribute(lambda o: o.tenant.tenant_id)
    agent_url = Sequence(lambda n: f"https://creative-{n:04d}.example.com/mcp")
    name = Sequence(lambda n: f"Creative Agent {n:04d}")
    enabled = True
    priority = 10
    auth_type = None
    auth_header = None
    auth_credentials = None
    timeout = 30


class SignalsAgentFactory(factory.alchemy.SQLAlchemyModelFactory):
    """A stored signals-agent row. Mirrors :class:`CreativeAgentFactory`."""

    class Meta:
        model = SignalsAgent
        sqlalchemy_session = None
        sqlalchemy_session_persistence = "commit"

    tenant = SubFactory(TenantFactory)
    tenant_id = LazyAttribute(lambda o: o.tenant.tenant_id)
    agent_url = Sequence(lambda n: f"https://signals-{n:04d}.example.com/mcp")
    name = Sequence(lambda n: f"Signals Agent {n:04d}")
    enabled = True
    auth_type = None
    auth_header = None
    auth_credentials = None
    forward_promoted_offering = True
    timeout = 30


def set_adapter_type(env: Any, tenant_id: str, adapter_type: str) -> AdapterConfig:
    """Point a tenant's ``AdapterConfig`` at *adapter_type* (BDD/E2E support).

    ``resolve_tenant_adapter_type`` reads this column as the authoritative adapter
    for a tenant, so writing a name outside ``ADAPTER_REGISTRY`` reproduces an
    ordinary operator misconfiguration: ``get_adapter_class`` refuses it with
    ``AdCPConfigurationError``. That is a real production state, which is why the
    capability-degradation scenarios use it instead of a fault-injection flag.

    Factory-based upsert -- no raw model construction in step bodies.
    """
    session = env.get_session()
    row = session.get(AdapterConfig, tenant_id)
    if row is None:
        row = AdapterConfigFactory(tenant=session.get(Tenant, tenant_id), adapter_type=adapter_type)
    else:
        row.adapter_type = adapter_type
    env._commit_factory_data()
    return row


def set_adapter_test_behavior(env: Any, tenant_id: str, **behavior: Any) -> AdapterConfig:
    """Upsert the mock-adapter ``test_behavior`` for a tenant (BDD/E2E support).

    The Docker-hosted mock adapter reads injected behavior — ``manual_approval_required``,
    ``fail_on_create``, ``fail_on_update``, ``error_message``, ``error_details``,
    ``recovery``, ``reject_on_create``/``rejection_reason`` — from
    ``AdapterConfig.config_json["test_behavior"]`` (see
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
