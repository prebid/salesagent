"""Slice-1 contract tests for GH #1088: eager Principal on ResolvedIdentity.

Core Invariant (salesagent-8xi7): identity/principal resolution happens exactly
once at the transport boundary — ``_impl`` functions receive a ResolvedIdentity
carrying an eagerly-loaded principal and never query principal/auth tables.

These tests pin the Slice-1 contract:

1. ``resolve_identity()`` (the single funnel for all three transports:
   transport_helpers.py MCP, adcp_a2a_server.py A2A, app.py REST) returns an
   identity whose ``principal`` field is a ``src.core.schemas.Principal``
   matching the token's principal row — at zero extra query cost, because
   ``auth_utils._lookup_principal`` already loads the full row at the boundary.
2. The admin-token path has no Principal row, so ``identity.principal`` stays
   ``None`` (consistent with today's ``get_principal_object`` returning None).
3. ``PrincipalFactory.make_identity`` populates ``identity.principal`` from
   factory args WITHOUT any DB query — identities are built before Given steps,
   i.e. before any principal row exists.

TDD red: ResolvedIdentity has no ``principal`` field yet, so every test fails
with AttributeError on ``identity.principal``.
"""

import pytest

from src.core.resolved_identity import resolve_identity
from src.core.schemas import Principal as PrincipalSchema
from tests.factories import PrincipalFactory, TenantFactory
from tests.harness._base import IntegrationEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


@pytest.mark.requires_db
class TestResolveIdentityEagerPrincipal:
    """resolve_identity eagerly populates identity.principal from the token row."""

    @pytest.mark.parametrize("protocol", ["mcp", "a2a", "rest"])
    def test_token_auth_populates_principal_object(self, integration_db, protocol):
        """Each transport protocol funnels through get_principal_from_token and
        gets an identity carrying the full Principal object for the token's row."""
        tenant_id = f"eager-principal-{protocol}"
        with IntegrationEnv() as _env:
            tenant = TenantFactory(tenant_id=tenant_id)
            principal = PrincipalFactory(
                tenant=tenant,
                principal_id=f"eager_p_{protocol}",
                name="Eager Principal Advertiser",
                access_token=f"eager-token-{protocol}",
                platform_mappings={"mock": {"advertiser_id": "adv_eager"}},
            )

            identity = resolve_identity(
                headers={"x-adcp-tenant": tenant_id},
                auth_token=principal.access_token,
                protocol=protocol,
            )

            assert identity.principal_id == principal.principal_id
            assert isinstance(identity.principal, PrincipalSchema), (
                f"identity.principal must be an eagerly-loaded schemas.Principal, got {type(identity.principal)!r}"
            )
            assert identity.principal.principal_id == principal.principal_id
            assert identity.principal.name == principal.name
            assert identity.principal.platform_mappings == {"mock": {"advertiser_id": "adv_eager"}}

    def test_global_token_lookup_populates_principal_object(self, integration_db):
        """Global (tenant-less) token lookup also carries the eager principal."""
        with IntegrationEnv() as _env:
            tenant = TenantFactory(tenant_id="eager-principal-global")
            principal = PrincipalFactory(
                tenant=tenant,
                principal_id="eager_p_global",
                access_token="eager-token-global",
            )

            # No tenant headers: tenant is discovered from the token row itself.
            identity = resolve_identity(headers={}, auth_token=principal.access_token, protocol="mcp")

            assert identity.tenant_id == tenant.tenant_id
            assert identity.principal_id == principal.principal_id
            assert isinstance(identity.principal, PrincipalSchema)
            assert identity.principal.principal_id == principal.principal_id

    def test_admin_token_yields_no_principal_object(self, integration_db):
        """Admin tokens have no Principal row: principal_id is the synthetic
        '<tenant>_admin' and identity.principal stays None (byte-identical with
        today's get_principal_object behavior for admin identities)."""
        tenant_id = "eager-principal-admin"
        with IntegrationEnv() as _env:
            TenantFactory(tenant_id=tenant_id, admin_token="eager-admin-token")

            identity = resolve_identity(
                headers={"x-adcp-tenant": tenant_id},
                auth_token="eager-admin-token",
                protocol="mcp",
            )

            assert identity.principal_id == f"{tenant_id}_admin"
            assert identity.principal is None


@pytest.mark.requires_db
class TestMakeIdentityEagerPrincipal:
    """PrincipalFactory.make_identity populates principal from factory args, no DB."""

    def test_make_identity_populates_principal_without_db(self, integration_db):
        """Identities are built BEFORE Given steps create any rows: the principal
        object must come from the factory args, not a DB lookup. The principal_id
        used here deliberately has NO row in the database — a DB-querying
        implementation would find nothing and leave principal None."""
        identity = PrincipalFactory.make_identity(
            principal_id="mk_no_db_principal",
            tenant_id="mk_no_db_tenant",
        )

        assert isinstance(identity.principal, PrincipalSchema), (
            f"make_identity must build identity.principal from factory args (no DB), got {type(identity.principal)!r}"
        )
        assert identity.principal.principal_id == "mk_no_db_principal"

    def test_make_identity_unauthenticated_has_no_principal(self, integration_db):
        """principal_id=None (unauthenticated identity) carries no principal object."""
        identity = PrincipalFactory.make_identity(principal_id=None, tenant_id="mk_no_db_tenant")

        assert identity.principal_id is None
        assert identity.principal is None
