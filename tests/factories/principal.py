"""Factory_boy factory for Principal model."""

from __future__ import annotations

from typing import Any

import factory
from factory import LazyAttribute, Sequence, SubFactory

from src.core.database.models import Principal
from src.core.resolved_identity import ResolvedIdentity
from src.core.testing_hooks import AdCPTestContext
from tests.factories.core import TenantFactory

_UNSET = object()


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
        tenant_id: str = "test_tenant",
        protocol: str = "mcp",
        dry_run: bool = False,
        auth_token: str | None = None,
        tenant: Any = _UNSET,
        testing_context: AdCPTestContext | None = None,
        **tenant_overrides: object,
    ) -> ResolvedIdentity:
        """Build a ResolvedIdentity without DB persistence.

        Auto-derives tenant dict via TenantFactory.make_tenant().
        Pass explicit tenant=None for auth-error tests.
        Pass **tenant_overrides for domain fields (approval_mode, etc).
        Pass testing_context to override the default (e.g. set
        test_session_id for harness routing).

        ``tenant`` is typed ``Any`` to match the underlying
        ``ResolvedIdentity.tenant`` field, which accepts plain dicts in
        most call sites and lazy proxies (``LazyTenantContext``) in tests
        that need deferred config resolution.
        """
        resolved_tenant = (
            TenantFactory.make_tenant(tenant_id=tenant_id, **tenant_overrides) if tenant is _UNSET else tenant
        )
        if testing_context is None:
            testing_context = AdCPTestContext(
                dry_run=dry_run,
                mock_time=None,
                jump_to_event=None,
                test_session_id=None,
            )
        principal_obj = None
        if principal_id is not None:
            from src.core.schemas import Principal as SchemaPrincipal

            principal_obj = SchemaPrincipal(
                principal_id=principal_id,
                name=f"Test Advertiser {principal_id}",
                platform_mappings={"mock": {"advertiser_id": f"mock-{principal_id}"}},
            )
        return ResolvedIdentity(
            principal_id=principal_id,
            principal=principal_obj,
            tenant_id=tenant_id,
            tenant=resolved_tenant,
            auth_token=auth_token,
            protocol=protocol,
            testing_context=testing_context,
        )
