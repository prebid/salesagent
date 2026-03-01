"""Regression tests: token extraction must be consistent across all code paths.

Core invariant: All three transport boundaries (REST, MCP, A2A) must extract
the same token from the same set of headers, using the same priority and case
rules, regardless of which code path is taken.

These tests verify:
1. x-adcp-auth takes priority over Authorization: Bearer (AdCP convention)
2. Bearer prefix matching is case-insensitive (RFC 7235 Section 2.1)
3. Middleware and resolve_identity agree on extraction behavior

beads: salesagent-jirz
"""

import pytest


class TestMiddlewareTokenPriority:
    """UnifiedAuthMiddleware must prefer x-adcp-auth over Authorization: Bearer."""

    @pytest.mark.asyncio
    async def test_x_adcp_auth_wins_when_both_present(self):
        """When both headers present, x-adcp-auth token must be used."""
        from src.core.auth_middleware import UnifiedAuthMiddleware

        captured_state = {}

        async def mock_app(scope, receive, send):
            captured_state.update(scope.get("state", {}))

        middleware = UnifiedAuthMiddleware(mock_app)
        scope = {
            "type": "http",
            "headers": [
                (b"x-adcp-auth", b"adcp-token"),
                (b"authorization", b"Bearer bearer-token"),
                (b"host", b"test.example.com"),
            ],
        }

        await middleware(scope, None, None)
        auth_ctx = captured_state.get("auth_context")
        assert auth_ctx is not None
        assert auth_ctx.auth_token == "adcp-token", (
            f"Expected x-adcp-auth token 'adcp-token', got '{auth_ctx.auth_token}'. "
            "Middleware must prefer x-adcp-auth over Authorization: Bearer."
        )

    @pytest.mark.asyncio
    async def test_x_adcp_auth_wins_regardless_of_header_order(self):
        """x-adcp-auth must win even if Authorization comes first in headers."""
        from src.core.auth_middleware import UnifiedAuthMiddleware

        captured_state = {}

        async def mock_app(scope, receive, send):
            captured_state.update(scope.get("state", {}))

        middleware = UnifiedAuthMiddleware(mock_app)
        # Authorization comes FIRST in the header list
        scope = {
            "type": "http",
            "headers": [
                (b"authorization", b"Bearer bearer-token"),
                (b"x-adcp-auth", b"adcp-token"),
                (b"host", b"test.example.com"),
            ],
        }

        await middleware(scope, None, None)
        auth_ctx = captured_state.get("auth_context")
        assert auth_ctx is not None
        assert auth_ctx.auth_token == "adcp-token", (
            f"Expected x-adcp-auth token, got '{auth_ctx.auth_token}'. "
            "x-adcp-auth must take priority regardless of header ordering."
        )


class TestMiddlewareBearerCaseInsensitive:
    """UnifiedAuthMiddleware must accept Bearer prefix case-insensitively (RFC 7235)."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "bearer_prefix",
        ["Bearer", "bearer", "BEARER", "bEaReR"],
        ids=["capital", "lower", "upper", "mixed"],
    )
    async def test_bearer_case_insensitive(self, bearer_prefix):
        """Bearer prefix matching must be case-insensitive per RFC 7235 Section 2.1."""
        from src.core.auth_middleware import UnifiedAuthMiddleware

        captured_state = {}

        async def mock_app(scope, receive, send):
            captured_state.update(scope.get("state", {}))

        middleware = UnifiedAuthMiddleware(mock_app)
        scope = {
            "type": "http",
            "headers": [
                (b"authorization", f"{bearer_prefix} my-token".encode("latin-1")),
                (b"host", b"test.example.com"),
            ],
        }

        await middleware(scope, None, None)
        auth_ctx = captured_state.get("auth_context")
        assert auth_ctx is not None
        assert auth_ctx.auth_token == "my-token", (
            f"Expected token 'my-token' for '{bearer_prefix} my-token', "
            f"got '{auth_ctx.auth_token}'. Bearer prefix must be case-insensitive."
        )

    @pytest.mark.asyncio
    async def test_bearer_without_space_rejected(self):
        """Malformed 'Bearertoken' (no space) must not extract a token."""
        from src.core.auth_middleware import UnifiedAuthMiddleware

        captured_state = {}

        async def mock_app(scope, receive, send):
            captured_state.update(scope.get("state", {}))

        middleware = UnifiedAuthMiddleware(mock_app)
        scope = {
            "type": "http",
            "headers": [
                (b"authorization", b"Bearertoken123"),
                (b"host", b"test.example.com"),
            ],
        }

        await middleware(scope, None, None)
        auth_ctx = captured_state.get("auth_context")
        assert auth_ctx is not None
        assert auth_ctx.auth_token is None, "Malformed 'Bearertoken' must not match"

    @pytest.mark.asyncio
    async def test_empty_bearer_token_yields_none(self):
        """'Bearer ' with no token after prefix must yield None, not empty string."""
        from src.core.auth_middleware import UnifiedAuthMiddleware

        captured_state = {}

        async def mock_app(scope, receive, send):
            captured_state.update(scope.get("state", {}))

        middleware = UnifiedAuthMiddleware(mock_app)
        scope = {
            "type": "http",
            "headers": [
                (b"authorization", b"Bearer "),
                (b"host", b"test.example.com"),
            ],
        }

        await middleware(scope, None, None)
        auth_ctx = captured_state.get("auth_context")
        assert auth_ctx is not None
        assert auth_ctx.auth_token is None, "Empty Bearer token must yield None, not ''"


class TestMiddlewareAndResolveIdentityAgree:
    """Middleware extraction and resolve_identity must produce identical results."""

    @pytest.mark.asyncio
    async def test_both_paths_agree_on_x_adcp_auth_priority(self):
        """Both extraction paths must choose x-adcp-auth when both headers present."""

        from src.core.auth_middleware import UnifiedAuthMiddleware
        from src.core.resolved_identity import _extract_auth_token

        headers = {
            "x-adcp-auth": "adcp-token",
            "authorization": "Bearer bearer-token",
            "host": "test.example.com",
        }

        # Path 1: Middleware extraction
        captured_state = {}

        async def mock_app(scope, receive, send):
            captured_state.update(scope.get("state", {}))

        middleware = UnifiedAuthMiddleware(mock_app)
        scope = {
            "type": "http",
            "headers": [(k.encode(), v.encode()) for k, v in headers.items()],
        }
        await middleware(scope, None, None)
        middleware_token = captured_state["auth_context"].auth_token

        # Path 2: _extract_auth_token (used by resolve_identity when auth_token=None)
        extracted_token, source = _extract_auth_token(headers)

        assert middleware_token == "adcp-token", f"Middleware extracted '{middleware_token}', expected 'adcp-token'"
        assert extracted_token == middleware_token, (
            f"_extract_auth_token returned '{extracted_token}' but middleware used '{middleware_token}'. "
            "Both paths must agree on token extraction priority."
        )
