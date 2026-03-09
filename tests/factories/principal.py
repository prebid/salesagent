"""Factory_boy factory for Principal model."""

from __future__ import annotations

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
        principal_id: str = "test_principal",
        tenant_id: str = "test_tenant",
        protocol: str = "mcp",
        dry_run: bool = False,
        auth_token: str | None = None,
        tenant: dict | None = _UNSET,  # type: ignore[assignment]
        **tenant_overrides: object,
    ) -> ResolvedIdentity:
        """Build a ResolvedIdentity without DB persistence.

        Auto-derives tenant dict via TenantFactory.make_tenant().
        Pass explicit tenant=None for auth-error tests.
        Pass **tenant_overrides for domain fields (approval_mode, etc).
        """
        resolved_tenant = (
            TenantFactory.make_tenant(tenant_id=tenant_id, **tenant_overrides) if tenant is _UNSET else tenant
        )
        return ResolvedIdentity(
            principal_id=principal_id,
            tenant_id=tenant_id,
            tenant=resolved_tenant,
            auth_token=auth_token,
            protocol=protocol,
            testing_context=AdCPTestContext(
                dry_run=dry_run,
                mock_time=None,
                jump_to_event=None,
                test_session_id=None,
            ),
        )
