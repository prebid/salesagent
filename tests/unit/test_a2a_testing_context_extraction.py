"""Regression tests for A2A testing context extraction from headers.

Bug: A2A transport doesn't extract testing context (X-Dry-Run, X-Test-Session-ID,
etc.) from HTTP request headers. MCP correctly extracts via TestContext.from_context(),
but A2A has no equivalent extraction path. This means test headers sent to A2A
endpoints are silently ignored.

Regression prevention: https://github.com/prebid/salesagent/pull/1066
Beads: salesagent-2yt6
"""

from unittest.mock import patch

from src.a2a_server.adcp_a2a_server import AdCPRequestHandler
from src.core.resolved_identity import ResolvedIdentity
from tests.a2a_helpers import make_a2a_context


class TestA2ATestingContextExtraction:
    """A2A transport should extract testing context from HTTP headers."""

    def test_dry_run_header_passed_to_resolve_identity(self):
        """X-Dry-Run header should be extracted and passed to resolve_identity.

        Verifies _resolve_a2a_identity calls resolve_identity with testing_context
        that has dry_run=True when X-Dry-Run: true header is present.
        """
        handler = AdCPRequestHandler()

        headers = {
            "authorization": "Bearer test-token",
            "x-adcp-tenant": "test-tenant",
            "x-dry-run": "true",
        }
        ctx = make_a2a_context(auth_token="test-token", headers=headers)

        mock_identity = ResolvedIdentity(
            principal_id="test_principal",
            tenant_id="test-tenant",
            tenant={"tenant_id": "test-tenant"},
            protocol="a2a",
        )

        with patch("src.core.resolved_identity.resolve_identity", return_value=mock_identity) as mock_resolve:
            handler._resolve_a2a_identity("test-token", require_valid_token=True, context=ctx)

        mock_resolve.assert_called_once()
        call_kwargs = mock_resolve.call_args.kwargs
        testing_ctx = call_kwargs.get("testing_context")
        assert testing_ctx is not None, (
            "_resolve_a2a_identity should pass testing_context to resolve_identity when test headers are present."
        )
        assert testing_ctx.dry_run is True, "X-Dry-Run: true header should set testing_context.dry_run=True"

    def test_test_session_id_passed_to_resolve_identity(self):
        """X-Test-Session-ID header should be extracted and passed to resolve_identity."""
        handler = AdCPRequestHandler()

        headers = {
            "authorization": "Bearer test-token",
            "x-adcp-tenant": "test-tenant",
            "x-test-session-id": "session-abc-123",
        }
        ctx = make_a2a_context(auth_token="test-token", headers=headers)

        mock_identity = ResolvedIdentity(
            principal_id="test_principal",
            tenant_id="test-tenant",
            tenant={"tenant_id": "test-tenant"},
            protocol="a2a",
        )

        with patch("src.core.resolved_identity.resolve_identity", return_value=mock_identity) as mock_resolve:
            handler._resolve_a2a_identity("test-token", require_valid_token=True, context=ctx)

        call_kwargs = mock_resolve.call_args.kwargs
        testing_ctx = call_kwargs.get("testing_context")
        assert testing_ctx is not None, "_resolve_a2a_identity should pass testing_context to resolve_identity."
        assert testing_ctx.test_session_id == "session-abc-123", (
            "X-Test-Session-ID header should be extracted by A2A transport."
        )

    def test_no_test_headers_passes_none_context(self):
        """When no test headers are present, testing_context=None should be passed."""
        handler = AdCPRequestHandler()

        headers = {
            "authorization": "Bearer test-token",
            "x-adcp-tenant": "test-tenant",
        }
        ctx = make_a2a_context(auth_token="test-token", headers=headers)

        mock_identity = ResolvedIdentity(
            principal_id="test_principal",
            tenant_id="test-tenant",
            tenant={"tenant_id": "test-tenant"},
            protocol="a2a",
        )

        with patch("src.core.resolved_identity.resolve_identity", return_value=mock_identity) as mock_resolve:
            handler._resolve_a2a_identity("test-token", require_valid_token=True, context=ctx)

        call_kwargs = mock_resolve.call_args.kwargs
        testing_ctx = call_kwargs.get("testing_context")
        assert testing_ctx is None, (
            "resolve_identity should receive testing_context=None when no test headers present. "
            f"Got {testing_ctx}, which may activate testing behavior unconditionally."
        )


class TestAdCPTestContextFromHeaders:
    """AdCPTestContext should have a from_headers classmethod for raw header dicts."""

    def test_from_headers_method_exists(self):
        """AdCPTestContext should have from_headers classmethod.

        Currently FAILS: Only from_context (takes FastMCP Context) exists.
        A2A needs from_headers (takes raw dict) for header extraction.
        """
        from src.core.testing_hooks import AdCPTestContext

        assert hasattr(AdCPTestContext, "from_headers"), (
            "AdCPTestContext needs a from_headers classmethod that extracts "
            "testing context from a raw headers dict (for A2A transport)."
        )

    def test_from_headers_extracts_dry_run(self):
        """from_headers should extract X-Dry-Run from raw headers dict."""
        from src.core.testing_hooks import AdCPTestContext

        if not hasattr(AdCPTestContext, "from_headers"):
            import pytest

            pytest.skip("from_headers not yet implemented")

        ctx = AdCPTestContext.from_headers({"x-dry-run": "true"})
        assert ctx.dry_run is True

    def test_from_headers_empty_dict_returns_none(self):
        """from_headers with empty dict should return None (no testing enabled)."""
        from src.core.testing_hooks import AdCPTestContext

        if not hasattr(AdCPTestContext, "from_headers"):
            import pytest

            pytest.skip("from_headers not yet implemented")

        ctx = AdCPTestContext.from_headers({})
        assert ctx is None, (
            "from_headers({}) should return None when no test headers present, "
            "to avoid creating a truthy AdCPTestContext that activates testing behavior."
        )
