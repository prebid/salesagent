"""Factory_boy factory for Principal model."""

from __future__ import annotations

from enum import Enum, auto
from typing import Any, Literal

import factory
from factory import LazyAttribute, Sequence, SubFactory

from src.core.database.models import Principal
from src.core.resolved_identity import ResolvedIdentity
from src.core.testing_hooks import AdCPTestContext
from tests.factories.core import TenantFactory


class _Sentinel(Enum):
    """Typed omitted-arg sentinel (distinct from explicit ``None``)."""

    USE_DEFAULT = auto()


_UNSET = _Sentinel.USE_DEFAULT


class PrincipalFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = Principal
        sqlalchemy_session = None
        sqlalchemy_session_persistence = "commit"

    tenant = SubFactory(TenantFactory)
    tenant_id = LazyAttribute(lambda o: o.tenant.tenant_id)
    principal_id = Sequence(lambda n: f"principal_{n:04d}")
    name = LazyAttribute(lambda o: f"Test Advertiser {o.principal_id}")
    access_token = Sequence(lambda n: f"token_{n:08d}")
    platform_mappings = factory.LazyFunction(lambda: {"mock": {"advertiser_id": "test_adv"}})

    @classmethod
    def make_identity(
        cls,
        principal_id: str | None = "test_principal",
        tenant_id: str | None = "test_tenant",
        protocol: str = "mcp",
        dry_run: bool = False,
        auth_token: str | None = None,
        tenant: Any = _UNSET,
        testing_context: AdCPTestContext | None | Literal[_Sentinel.USE_DEFAULT] = _UNSET,
        **tenant_overrides: object,
    ) -> ResolvedIdentity:
        """Build a ResolvedIdentity without DB persistence.

        Auto-derives tenant dict via TenantFactory.make_tenant().
        Pass explicit tenant=None for auth-error tests.
        Pass **tenant_overrides for domain fields (approval_mode, etc).
        Pass testing_context to override the default (e.g. set
        test_session_id for harness routing).

        ``testing_context`` meanings:
        - omitted / sentinel (default): populated ``AdCPTestContext``
        - ``testing_context=None``: explicit None (anonymous A2A discovery;
          production ``AdCPTestContext.from_headers({})`` returns None)

        ``testing_context`` meanings:
        - omitted / sentinel (default): populated ``AdCPTestContext``
        - ``testing_context=None``: explicit None (anonymous A2A discovery;
          production ``AdCPTestContext.from_headers({})`` returns None)

        ``tenant`` is typed ``Any`` to match the underlying
        ``ResolvedIdentity.tenant`` field, which accepts plain dicts in
        most call sites and lazy proxies (``LazyTenantContext``) in tests
        that need deferred config resolution.

        When ``tenant`` is omitted and ``tenant_id is None``, skip
        ``make_tenant`` (would build a ``pub-None`` subdomain) and set
        ``resolved_tenant=None``.
        """
        if tenant is _UNSET:
            if tenant_id is None:
                resolved_tenant = None
            else:
                resolved_tenant = TenantFactory.make_tenant(tenant_id=tenant_id, **tenant_overrides)
        else:
            resolved_tenant = tenant
        if testing_context is _UNSET:
            testing_context = AdCPTestContext(
                dry_run=dry_run,
                mock_time=None,
                jump_to_event=None,
                test_session_id=None,
            )
        return ResolvedIdentity(
            principal_id=principal_id,
            tenant_id=tenant_id,
            tenant=resolved_tenant,
            auth_token=auth_token,
            protocol=protocol,
            testing_context=testing_context,
        )

    @classmethod
    def make_anonymous_a2a_identity(cls, tenant_id: str | None = None, **kwargs: object) -> ResolvedIdentity:
        """Anonymous A2A discovery identity — principal_id/tenant None, protocol a2a.

        Production always returns ResolvedIdentity (never None) for discovery.
        ``testing_context=None`` matches ``AdCPTestContext.from_headers({})``.
        """
        return cls.make_identity(
            principal_id=None,
            tenant_id=tenant_id,
            tenant=None,
            protocol="a2a",
            testing_context=None,
            **kwargs,
        )


# Public alias for ``_UNSET``, assigned after class creation (not in the class
# body) so factory_boy's metaclass never sees it as a declared Principal
# field. Lets callers that wrap ``make_identity`` (e.g. a test module's own
# ``make_identity(testing_context=...)`` helper) default-forward "caller
# didn't pass this" without minting a second private sentinel object for the
# same omitted-vs-explicit-None concept.
PrincipalFactory.UNSET = _UNSET
