"""Deprecated testing headers are ignored consistently by A2A."""

from unittest.mock import patch

from src.a2a_server.adcp_a2a_server import AdCPRequestHandler
from tests.a2a_helpers import make_a2a_context
from tests.factories.principal import PrincipalFactory


class TestA2ATestingContextExtraction:
    """A2A transport must not derive behavior from deprecated X-* headers."""

    def test_dry_run_header_is_ignored(self):
        handler = AdCPRequestHandler()

        headers = {
            "authorization": "Bearer test-token",
            "x-adcp-tenant": "test-tenant",
            "x-dry-run": "true",
        }
        ctx = make_a2a_context(auth_token="test-token", headers=headers)

        mock_identity = PrincipalFactory.make_identity(
            principal_id="test_principal",
            tenant_id="test-tenant",
            tenant={"tenant_id": "test-tenant"},
            protocol="a2a",
        )

        with patch("src.core.resolved_identity.resolve_identity", return_value=mock_identity) as mock_resolve:
            handler._resolve_a2a_identity("test-token", require_valid_token=True, context=ctx)

        mock_resolve.assert_called_once_with(
            headers=headers,
            auth_token="test-token",
            require_valid_token=True,
            protocol="a2a",
            testing_context=None,
        )

    def test_test_session_id_header_is_ignored(self):
        handler = AdCPRequestHandler()

        headers = {
            "authorization": "Bearer test-token",
            "x-adcp-tenant": "test-tenant",
            "x-test-session-id": "session-abc-123",
        }
        ctx = make_a2a_context(auth_token="test-token", headers=headers)

        mock_identity = PrincipalFactory.make_identity(
            principal_id="test_principal",
            tenant_id="test-tenant",
            tenant={"tenant_id": "test-tenant"},
            protocol="a2a",
        )

        with patch("src.core.resolved_identity.resolve_identity", return_value=mock_identity) as mock_resolve:
            handler._resolve_a2a_identity("test-token", require_valid_token=True, context=ctx)

        call_kwargs = mock_resolve.call_args.kwargs
        assert call_kwargs.get("testing_context") is None

    def test_no_test_headers_passes_none_context(self):
        """When no test headers are present, testing_context=None should be passed."""
        handler = AdCPRequestHandler()

        headers = {
            "authorization": "Bearer test-token",
            "x-adcp-tenant": "test-tenant",
        }
        ctx = make_a2a_context(auth_token="test-token", headers=headers)

        mock_identity = PrincipalFactory.make_identity(
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

    def test_from_headers_ignores_dry_run(self):
        from src.core.testing_hooks import AdCPTestContext

        if not hasattr(AdCPTestContext, "from_headers"):
            import pytest

            pytest.skip("from_headers not yet implemented")

        assert AdCPTestContext.from_headers({"x-dry-run": "true"}) is None

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


class TestMockTimeIsAlwaysAware:
    """mock_time is UTC-aware no matter how the context is constructed.

    Regression (#1545 K1 follow-up review): X-Mock-Time was minted NAIVE by
    from_headers (rstrip("Z") + fromisoformat, and local-time fromtimestamp for
    the epoch form) while campaign flight datetimes are UTC-aware, so
    NextEventCalculator.calculate_next_event_time raised
    'TypeError: can't compare offset-naive and offset-aware datetimes' and
    failed the whole get_media_buy_delivery request. The clock is now
    normalized once at the AdCPTestContext construction boundary.
    """

    def test_from_headers_ignores_mock_time(self):
        from src.core.testing_hooks import AdCPTestContext

        assert AdCPTestContext.from_headers({"x-mock-time": "2025-06-01T00:00:00Z"}) is None

    def test_direct_construction_with_naive_datetime_is_coerced_to_utc(self):
        from datetime import UTC, datetime

        from src.core.testing_hooks import AdCPTestContext

        ctx = AdCPTestContext(mock_time=datetime(2025, 6, 1))
        assert ctx.mock_time == datetime(2025, 6, 1, tzinfo=UTC)
