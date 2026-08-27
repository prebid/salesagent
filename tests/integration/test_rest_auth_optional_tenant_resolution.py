"""REST auth-optional discovery endpoints must resolve tenant even with no credentials.

Core Invariant (AdCP INV-4, v3.1.1 get_adcp_capabilities.mdx L23): discovery
skills describe the SELLER, not the caller — an anonymous request must still
resolve tenant context from headers (Host / x-adcp-tenant / Apx-Incoming-Host),
identically to an authenticated one, so it receives the full seller-scoped
response rather than a degraded/minimal one.

_resolve_auth_dep() (src/core/auth_context.py) is the FastAPI dependency
backing every "auth-optional" REST route (get_capabilities, get_products,
list_creative_formats, list_authorized_properties). It short-circuited to
``return None`` whenever no auth token was presented, BEFORE ever calling
resolve_identity() — skipping header-based tenant detection entirely. This
broke tenant resolution for every anonymous REST discovery call, unlike
resolve_identity_from_context() (MCP/A2A), which always calls
resolve_identity() regardless of token presence (salesagent-zna9).

Only observable over e2e_rest: the in-process REST test dispatcher overrides
the FastAPI dependency directly with the test's identity object, bypassing
_resolve_auth_dep()'s real logic — which is why in-process rest/mcp/a2a all
passed while only e2e_rest exposed the bug.
"""

from __future__ import annotations

import pytest

from src.core.auth_context import AuthContext, _resolve_auth_dep
from tests.factories import TenantFactory
from tests.harness._base import BareIntegrationEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


class TestResolveAuthDepTenantResolution:
    """_resolve_auth_dep() must resolve tenant from headers regardless of auth-token presence."""

    def test_no_auth_token_still_resolves_tenant_from_header(self, integration_db):
        """No x-adcp-auth token at all -> tenant still resolves via x-adcp-tenant header.

        Before the fix: auth_ctx.auth_token is falsy -> immediate ``return None``,
        never calling resolve_identity(), so identity.tenant was unreachable.
        """
        with BareIntegrationEnv(tenant_id="rest_disc_t1") as env:
            TenantFactory(tenant_id="rest_disc_t1", subdomain="rest-disc-t1")
            env.get_session()  # commit factory data

            auth_ctx = AuthContext(auth_token=None, headers={"x-adcp-tenant": "rest-disc-t1"})

            identity = _resolve_auth_dep(auth_ctx)

        assert identity is not None, (
            "_resolve_auth_dep must return a ResolvedIdentity (not bare None) so discovery "
            "_impl functions can read identity.tenant for anonymous callers — matching "
            "resolve_identity_from_context()'s MCP/A2A behavior"
        )
        assert identity.tenant is not None
        assert identity.tenant["tenant_id"] == "rest_disc_t1"
        assert identity.principal_id is None, "still correctly anonymous — no principal resolved"

    def test_unresolvable_token_still_resolves_tenant_from_header(self, integration_db):
        """A presented-but-unresolvable token -> tenant still resolves via header.

        Before the fix: resolve_identity() ran but ``if not identity.principal_id: return None``
        discarded the resolved tenant anyway.
        """
        with BareIntegrationEnv(tenant_id="rest_disc_t2") as env:
            TenantFactory(tenant_id="rest_disc_t2", subdomain="rest-disc-t2")
            env.get_session()  # commit factory data

            auth_ctx = AuthContext(
                auth_token="not-a-real-token",
                headers={"x-adcp-tenant": "rest-disc-t2", "x-adcp-auth": "not-a-real-token"},
            )

            identity = _resolve_auth_dep(auth_ctx)

        assert identity is not None
        assert identity.tenant is not None
        assert identity.tenant["tenant_id"] == "rest_disc_t2"
        assert identity.principal_id is None, "token didn't resolve to a principal, but tenant must still be present"
