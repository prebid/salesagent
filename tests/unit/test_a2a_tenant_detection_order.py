"""Regression test: A2A tenant detection must match resolve_identity() strategy order.

The canonical strategy order (in resolved_identity.py:_detect_tenant) is:
  1. Host header → virtual host FIRST, then subdomain
  2. x-adcp-tenant → subdomain lookup, then direct ID
  3. Apx-Incoming-Host → virtual host lookup
  4. localhost fallback → "default" tenant

Bug salesagent-cvju: A2A's _create_tool_context_from_a2a() tries subdomain FIRST
then virtual host, the opposite of _detect_tenant(). For a Host header like
"acme.example.com" where "acme" is also a virtual host, the two transports
resolve to different tenants.

Additionally, A2A is missing the localhost fallback (strategy 4).
"""

from unittest.mock import patch

from src.core.resolved_identity import _detect_tenant


class TestTenantDetectionStrategyOrder:
    """Verify _detect_tenant tries virtual host BEFORE subdomain."""

    @patch("src.core.resolved_identity.get_tenant_by_subdomain")
    @patch("src.core.resolved_identity.get_tenant_by_virtual_host")
    def test_virtual_host_takes_priority_over_subdomain(self, mock_vhost, mock_subdomain):
        """When both virtual host and subdomain match, virtual host wins."""
        vhost_tenant = {"tenant_id": "vhost-tenant", "subdomain": "acme"}
        subdomain_tenant = {"tenant_id": "subdomain-tenant", "subdomain": "acme"}

        mock_vhost.return_value = vhost_tenant
        mock_subdomain.return_value = subdomain_tenant

        headers = {"host": "acme.example.com"}
        tenant_id, tenant_context = _detect_tenant(headers)

        assert tenant_id == "vhost-tenant", (
            f"Expected virtual host tenant 'vhost-tenant' but got '{tenant_id}'. "
            "Virtual host lookup must take priority over subdomain extraction."
        )
        # Subdomain should NOT have been called since virtual host matched
        mock_subdomain.assert_not_called()

    @patch("src.core.resolved_identity.get_tenant_by_subdomain")
    @patch("src.core.resolved_identity.get_tenant_by_virtual_host")
    def test_localhost_fallback_resolves_default_tenant(self, mock_vhost, mock_subdomain):
        """localhost with no other match should resolve to 'default' tenant."""
        mock_vhost.return_value = None
        default_tenant = {"tenant_id": "default", "subdomain": "default"}
        mock_subdomain.return_value = default_tenant

        headers = {"host": "localhost:8000"}
        tenant_id, tenant_context = _detect_tenant(headers)

        assert tenant_id == "default", (
            f"Expected 'default' tenant for localhost, got '{tenant_id}'. "
            "Strategy 4 (localhost fallback) must resolve to 'default'."
        )


class TestA2ATenantDetectionMatchesCanonical:
    """A2A's _resolve_a2a_identity must use the same strategy order as _detect_tenant.

    This test verifies the A2A server delegates to resolve_identity() rather than
    implementing its own tenant detection. If A2A has its own implementation,
    the strategy order may diverge (which is the bug).
    """

    @patch("src.core.resolved_identity.resolve_identity")
    def test_a2a_delegates_to_resolve_identity(self, mock_resolve):
        """A2A should delegate to resolve_identity(), not hand-roll tenant detection.

        We verify behaviorally: call _resolve_a2a_identity and assert
        resolve_identity() was called with protocol='a2a'. If A2A has its own
        inline tenant detection, resolve_identity() would NOT be called.
        """
        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler
        from src.core.resolved_identity import ResolvedIdentity
        from tests.a2a_helpers import make_a2a_context

        ctx = make_a2a_context(auth_token="test-token", headers={"host": "acme.example.com"})
        mock_resolve.return_value = ResolvedIdentity(
            principal_id="test-principal",
            tenant_id="acme",
            tenant={"tenant_id": "acme"},
            protocol="a2a",
        )

        handler = AdCPRequestHandler.__new__(AdCPRequestHandler)
        handler._resolve_a2a_identity(auth_token="test-token", context=ctx)

        mock_resolve.assert_called_once()
        call_kwargs = mock_resolve.call_args
        assert call_kwargs.kwargs.get("protocol") == "a2a" or (
            len(call_kwargs.args) == 0 and call_kwargs[1].get("protocol") == "a2a"
        ), (
            "A2A _resolve_a2a_identity must call resolve_identity(protocol='a2a'). "
            "If it uses inline tenant detection, the strategy order diverges (bug salesagent-cvju)."
        )
