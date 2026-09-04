"""E2E: the TMP discovery contract is reachable and conformant through the real stack.

The contract an out-of-process router polls every 30s was never exercised through
the deployed stack: nothing failed if the router were not mounted in the container
image, if middleware ordering differed under the real server, or if the proxy did
not route ``/tenant/...`` (#1197 review). Every other tier grades it in-process
against ``src.app.app``.

One GET, not a four-transport fan-out: the endpoint is single-surface by design —
there is no MCP or A2A discovery tool, and the in-process suites already cover the
auth matrix, the lifecycle filtering and the schema validity. What only this tier
can show is that the route is served, authenticated and schema-valid *through nginx
by the deployed image*.
"""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select

from src.routes.tmp_providers import DISCOVERY_ROUTE, PROVIDER_REGISTRATION_SCHEMA
from tests.e2e.utils import live_db_env, wait_for_server_readiness
from tests.factories import delete_tmp_providers, replace_tmp_providers
from tests.helpers.pinned_schema import validate_against_pinned_schema

pytestmark = pytest.mark.e2e

_TENANT_SUBDOMAIN = "ci-test"


def _seed_provider(live_server: dict) -> tuple[str, str]:
    """Register one active provider on the live DB; return (tenant_id, provider_id)."""
    from src.core.database.models import Tenant

    with live_db_env(live_server) as env:
        session = env.get_session()
        tenant = session.scalars(select(Tenant).filter_by(subdomain=_TENANT_SUBDOMAIN)).first()
        if tenant is None:
            raise RuntimeError(
                f"Tenant with subdomain {_TENANT_SUBDOMAIN!r} not found in the live e2e DB — "
                "did the stack's init_database_ci.py seed run?"
            )
        provider = replace_tmp_providers(
            env,
            tenant.tenant_id,
            name="E2E Discovery Provider",
            endpoint="https://e2e-discovery.example.com/tmp",
            context_match=True,
            identity_match=False,
            timeout_ms=250,
        )
        return tenant.tenant_id, provider.provider_id


def _discovery_url(base_url: str, tenant_id: str) -> str:
    """Build the polled URL from the route's own declaration, not a re-typed path.

    If the decorator and this test disagreed about the path, that would be the
    drift ``DISCOVERY_ROUTE`` exists to prevent — so the test consumes the same
    constant the route is registered from.
    """
    return base_url + DISCOVERY_ROUTE.format(tenant_id=tenant_id)


class TestDiscoveryContractOverLiveHttp:
    """GET the discovery contract through nginx against the deployed image."""

    def test_authenticated_poll_returns_schema_valid_providers(self, live_server, test_auth_token):
        """A tenant-scoped credential gets the tenant's providers, each schema-valid."""
        base_url = live_server["mcp"]
        wait_for_server_readiness(base_url)

        tenant_id, provider_id = _seed_provider(live_server)
        try:
            response = httpx.get(
                _discovery_url(base_url, tenant_id),
                headers={"x-adcp-auth": test_auth_token},
                timeout=30,
            )
        finally:
            with live_db_env(live_server) as env:
                delete_tmp_providers(env, tenant_id)

        assert response.status_code == 200, f"discovery failed: {response.status_code} {response.text}"
        body = response.json()
        assert body["tenant_id"] == tenant_id
        assert provider_id in {p["provider_id"] for p in body["providers"]}

        for entry in body["providers"]:
            validate_against_pinned_schema(PROVIDER_REGISTRATION_SCHEMA, entry)
            # `name` is admin-only; the closed schema has no such property, so its
            # absence is what keeps the entry conformant on the real wire.
            assert "name" not in entry

    def test_unauthenticated_poll_is_rejected(self, live_server):
        """The fail-closed half, through the real middleware stack.

        Grades what the in-process suites structurally cannot: that
        ``UnifiedAuthMiddleware`` is actually installed in the deployed image and
        ordered ahead of this route.
        """
        base_url = live_server["mcp"]
        wait_for_server_readiness(base_url)

        response = httpx.get(_discovery_url(base_url, "ci-test-tenant"), timeout=30)

        assert response.status_code == 401, f"expected 401, got {response.status_code} {response.text}"
