"""Factory_boy factory for TMPProvider model."""

from __future__ import annotations

import uuid
from typing import Any

import factory
from factory import LazyAttribute, Sequence, SubFactory

from src.core.database.models import TMPProvider
from tests.factories.core import TenantFactory


class TMPProviderFactory(factory.alchemy.SQLAlchemyModelFactory):
    """Factory for TMPProvider ORM instances.

    Creates active providers by default.  Override ``status``, ``priority``,
    ``tenant_id`` etc. as needed for specific test scenarios.

    Note: ``provider_id`` has both a server default and an ORM default in
    production; generating it here avoids a round-trip.  It must be hyphen-free
    (``uuid4().hex``) — the pinned provider-registration schema constrains
    ``provider_id`` to ``^[A-Za-z0-9_]+$``, so a canonical UUID would seed rows
    whose discovery entries the schema rejects (#1197 review).
    """

    class Meta:
        model = TMPProvider
        sqlalchemy_session = None
        sqlalchemy_session_persistence = "commit"
        exclude = ["tenant"]

    tenant = SubFactory(TenantFactory)

    provider_id = factory.LazyFunction(lambda: uuid.uuid4().hex)
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


def replace_tmp_providers(env: Any, tenant_id: str, **fields: Any) -> TMPProvider:
    """Make *tenant_id* have exactly one TMP provider, built by the factory.

    The e2e analogue of ``set_adapter_test_behavior`` (``tests/factories/core.py``):
    a shared factory-backed helper so out-of-process tests configure the live DB
    through :class:`TMPProviderFactory` instead of hand-constructing the ORM row
    (CLAUDE.md Pattern #8 — no ``session.add()`` in test bodies).

    Existing providers for the tenant are deleted first — by calling
    :func:`delete_tmp_providers`, not by re-implementing it. That is the point of
    "replace": the sync fans out to *every* syncable provider, so a row left by
    an earlier run would add unrelated POSTs — and unresolvable-host errors — to
    this tenant's fan-out.

    Args:
        env: Harness environment exposing ``get_session()`` (real-DB envs).
        tenant_id: Tenant the provider is registered under.
        **fields: Factory field overrides (``endpoint``, ``name``, ``status``, …).

    Returns the created provider.
    """
    delete_tmp_providers(env, tenant_id)

    TMPProviderFactory._meta.sqlalchemy_session = env.get_session()
    try:
        return TMPProviderFactory(tenant_id=tenant_id, tenant=None, **fields)
    finally:
        TMPProviderFactory._meta.sqlalchemy_session = None


def delete_tmp_providers(env: Any, tenant_id: str) -> None:
    """Remove every TMP provider row for *tenant_id* (teardown counterpart)."""
    from sqlalchemy import select

    session = env.get_session()
    for row in session.scalars(select(TMPProvider).filter_by(tenant_id=tenant_id)).all():
        session.delete(row)
    session.commit()


def plant_seller_agent_host(env: Any, tenant_id: str, host: str) -> None:
    """Give *tenant_id* a public ``virtual_host`` so TMP sync can resolve a seller agent.

    ``_resolve_seller_agent_url`` needs an https URL for
    ``AvailablePackage.seller_agent`` and correctly SKIPS the sync when it cannot
    build one, so a test that registers a provider but leaves the tenant without
    a public host observes no delivery — and would fail for a reason unrelated to
    what it grades. Planting the host here (rather than in each caller) keeps that
    precondition attached to the provider seeding it belongs to.
    """
    from sqlalchemy import select

    from src.core.database.models import Tenant

    session = env.get_session()
    tenant = session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
    if tenant is None:
        raise LookupError(f"Cannot plant a seller-agent host: no tenant {tenant_id!r}")
    tenant.virtual_host = host
    session.commit()
