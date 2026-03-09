"""Integration tests for list_creatives authentication and authorization.

Tests verify that:
1. list_creatives requires authentication (unlike discovery endpoints)
2. Authenticated users only see their own creatives
3. Unauthenticated requests are rejected

Uses CreativeListEnv harness + factory_boy, consistent with other behavioral tests.
"""

import pytest

from src.core.exceptions import AdCPAuthenticationError
from tests.factories import (
    CreativeFactory,
    PrincipalFactory,
    TenantFactory,
)
from tests.harness import CreativeListEnv, make_identity

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


class TestInvalidTokenAtTransportBoundary:
    """Transport boundary rejects invalid token strings via resolve_identity()."""

    def test_bad_token_string_rejected(self, integration_db):
        """SECURITY: An actual bad token string → AdCPAuthenticationError at boundary."""
        from src.core.resolved_identity import resolve_identity

        # Create a real tenant so _detect_tenant succeeds
        with CreativeListEnv():
            tenant = TenantFactory(tenant_id="token_test_tenant")
            # Pass a fabricated bad token that doesn't match any principal in the DB
            headers = {
                "x-adcp-auth": "bad-token-xyz-not-a-real-token",
                "x-adcp-tenant": tenant.tenant_id,
            }
            with pytest.raises(AdCPAuthenticationError, match="invalid"):
                resolve_identity(headers, require_valid_token=True)


class TestListCreativesAuthentication:
    """Integration tests for list_creatives authentication."""

    def test_unauthenticated_request_should_fail(self, integration_db):
        """SECURITY: identity=None → AdCPAuthenticationError."""
        with CreativeListEnv() as env:
            with pytest.raises(AdCPAuthenticationError):
                env.call_impl(identity=None)

    def test_no_principal_should_fail(self, integration_db):
        """SECURITY: principal_id=None → AdCPAuthenticationError."""
        identity = make_identity(principal_id=None, tenant={"tenant_id": "t1", "name": "T1"})
        with CreativeListEnv() as env:
            with pytest.raises(AdCPAuthenticationError):
                env.call_impl(identity=identity)

    def test_authenticated_user_sees_only_own_creatives(self, integration_db):
        """SECURITY: principal A sees only their own creatives, not B's."""
        with CreativeListEnv() as env:
            tenant = TenantFactory(tenant_id="auth_test_tenant")
            p_a = PrincipalFactory(tenant=tenant, principal_id="advertiser_a")
            p_b = PrincipalFactory(tenant=tenant, principal_id="advertiser_b")

            for i in range(3):
                CreativeFactory(
                    tenant=tenant,
                    principal=p_a,
                    creative_id=f"creative_a_{i}",
                    name=f"Advertiser A Creative {i}",
                )
            for i in range(2):
                CreativeFactory(
                    tenant=tenant,
                    principal=p_b,
                    creative_id=f"creative_b_{i}",
                    name=f"Advertiser B Creative {i}",
                )

            identity_a = make_identity(
                principal_id="advertiser_a",
                tenant_id="auth_test_tenant",
                tenant={"tenant_id": "auth_test_tenant", "name": "Auth Test Tenant"},
            )
            response = env.call_impl(identity=identity_a)

        assert len(response.creatives) == 3
        assert response.query_summary.total_matching == 3
        for creative in response.creatives:
            assert creative.principal_id == "advertiser_a"

    def test_different_principal_sees_different_creatives(self, integration_db):
        """SECURITY: principal B sees only their own creatives, not A's."""
        with CreativeListEnv() as env:
            tenant = TenantFactory(tenant_id="auth_test_tenant")
            p_a = PrincipalFactory(tenant=tenant, principal_id="advertiser_a")
            p_b = PrincipalFactory(tenant=tenant, principal_id="advertiser_b")

            for i in range(3):
                CreativeFactory(
                    tenant=tenant,
                    principal=p_a,
                    creative_id=f"creative_a_{i}",
                )
            for i in range(2):
                CreativeFactory(
                    tenant=tenant,
                    principal=p_b,
                    creative_id=f"creative_b_{i}",
                )

            identity_b = make_identity(
                principal_id="advertiser_b",
                tenant_id="auth_test_tenant",
                tenant={"tenant_id": "auth_test_tenant", "name": "Auth Test Tenant"},
            )
            response = env.call_impl(identity=identity_b)

        assert len(response.creatives) == 2
        assert response.query_summary.total_matching == 2
        for creative in response.creatives:
            assert creative.principal_id == "advertiser_b"
