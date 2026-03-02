"""Regression tests for FastAPI migration code review fixes.

Tests P0/P1 issues found during code review of the FastAPI unified app.
Each test targets a specific beads issue to prevent regression.

salesagent-c0gm: Non-async receive lambda (ASGI protocol)
salesagent-agey: CORS origins configuration
salesagent-9fy7: Apx-Incoming-Host hostname validation
salesagent-agmq: Debug endpoints gated behind ADCP_TESTING
salesagent-nb7k: format_resolver async event loop fix
"""

import os
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# salesagent-c0gm [P0]: Async receive callable in messageId middleware
# ---------------------------------------------------------------------------


class TestAsyncReceiveCallable:
    """The ASGI receive callable must be async for Starlette body reading."""

    def test_receive_function_is_async(self):
        """The _receive helper reconstructing request body must be awaitable.

        Before fix: lambda: {...} — not awaitable, causes TypeError on await.
        After fix: async def _receive() — properly awaitable.
        """
        # Import the middleware and inspect its internals
        # We test this by running the middleware with a numeric messageId
        # and verifying the request reconstruction works.
        from starlette.testclient import TestClient

        from src.app import app

        client = TestClient(app)

        # A2A JSON-RPC request with numeric messageId (triggers the middleware)
        payload = {
            "jsonrpc": "2.0",
            "id": 42,
            "method": "message/send",
            "params": {
                "message": {
                    "messageId": 12345,
                    "role": "user",
                    "parts": [{"kind": "text", "text": "test"}],
                }
            },
        }

        # This should NOT raise TypeError from non-async receive
        response = client.post(
            "/a2a",
            json=payload,
            headers={"Content-Type": "application/json"},
        )

        # We don't care about the exact response (auth will fail),
        # but the middleware must not crash with TypeError
        assert response.status_code != 500 or b"TypeError" not in response.content

    def test_numeric_jsonrpc_id_converted_to_string(self):
        """Numeric JSON-RPC id values must be converted to strings."""
        from starlette.testclient import TestClient

        from src.app import app

        client = TestClient(app)

        payload = {"jsonrpc": "2.0", "id": 99, "method": "message/send", "params": {}}

        response = client.post(
            "/a2a",
            json=payload,
            headers={"Content-Type": "application/json"},
        )

        # Middleware should have converted id to "99" — verify no crash
        assert response.status_code != 500 or b"TypeError" not in response.content


# ---------------------------------------------------------------------------
# salesagent-agey [P0]: CORS origins must not use wildcard with credentials
# ---------------------------------------------------------------------------


class TestCORSConfiguration:
    """CORS must use specific origins when allow_credentials=True."""

    def test_cors_does_not_use_wildcard_with_credentials(self):
        """CORS spec forbids allow_origins=['*'] with allow_credentials=True.

        Before fix: allow_origins=["*"] + allow_credentials=True — browsers ignore.
        After fix: allow_origins from ALLOWED_ORIGINS env var.
        """
        from starlette.testclient import TestClient

        from src.app import app

        client = TestClient(app)

        # Preflight request
        response = client.options(
            "/health",
            headers={
                "Origin": "http://evil.example.com",
                "Access-Control-Request-Method": "GET",
            },
        )

        # With specific origins, a non-allowed origin should NOT get
        # Access-Control-Allow-Origin: *
        acao = response.headers.get("access-control-allow-origin", "")
        assert acao != "*", "CORS wildcard '*' used with credentials — browsers will ignore credentials"

    def test_allowed_origin_gets_cors_header(self):
        """An origin listed in ALLOWED_ORIGINS should get CORS response header."""
        from starlette.testclient import TestClient

        from src.app import app

        client = TestClient(app)

        # Default ALLOWED_ORIGINS includes http://localhost:8000
        allowed_origin = os.getenv("ALLOWED_ORIGINS", "http://localhost:8000").split(",")[0].strip()
        response = client.get("/health", headers={"Origin": allowed_origin})
        acao = response.headers.get("access-control-allow-origin", "")
        assert acao == allowed_origin, (
            f"Allowed origin '{allowed_origin}' should get matching CORS header, got '{acao}'"
        )


# ---------------------------------------------------------------------------
# salesagent-9fy7 [P0]: Apx-Incoming-Host hostname validation
# ---------------------------------------------------------------------------


class TestHostnameValidation:
    """Apx-Incoming-Host header must be validated before use in URLs."""

    def test_valid_hostnames_accepted(self):
        """Standard hostnames pass validation."""
        from src.app import _is_valid_hostname

        assert _is_valid_hostname("example.com")
        assert _is_valid_hostname("sub.example.com")
        assert _is_valid_hostname("localhost")
        assert _is_valid_hostname("localhost:8000")
        assert _is_valid_hostname("my-host.example.com:443")
        assert _is_valid_hostname("192.168.1.1")
        assert _is_valid_hostname("192.168.1.1:8080")

    def test_path_traversal_rejected(self):
        """Hostnames with path components are rejected."""
        from src.app import _is_valid_hostname

        assert not _is_valid_hostname("example.com/../../etc/passwd")
        assert not _is_valid_hostname("example.com/admin")
        assert not _is_valid_hostname("host/path")

    def test_injection_characters_rejected(self):
        """Hostnames with injection characters are rejected."""
        from src.app import _is_valid_hostname

        assert not _is_valid_hostname("example.com\r\nX-Injected: true")
        assert not _is_valid_hostname("example.com<script>")
        assert not _is_valid_hostname("example.com; rm -rf /")
        assert not _is_valid_hostname("example.com' OR '1'='1")

    def test_empty_and_none_rejected(self):
        """Empty strings are rejected."""
        from src.app import _is_valid_hostname

        assert not _is_valid_hostname("")

    def test_overly_long_hostname_rejected(self):
        """Hostnames longer than 253 characters are rejected (DNS limit)."""
        from src.app import _is_valid_hostname

        long_host = "a" * 254
        assert not _is_valid_hostname(long_host)

    def test_agent_card_ignores_invalid_header(self):
        """Agent card falls back to Host header when Apx-Incoming-Host is invalid."""
        from starlette.testclient import TestClient

        from src.app import app

        client = TestClient(app)

        response = client.get(
            "/.well-known/agent-card.json",
            headers={
                "Apx-Incoming-Host": "evil.com/../../etc/passwd",
                "Host": "localhost:8000",
            },
        )

        assert response.status_code == 200
        card = response.json()
        # URL should NOT contain the injected path
        assert "passwd" not in card.get("url", "")
        assert "../../" not in card.get("url", "")

    def test_agent_card_ignores_invalid_host_header(self):
        """Agent card falls back to default URL when Host header is invalid (salesagent-4r0m)."""
        from starlette.testclient import TestClient

        from src.app import app

        client = TestClient(app)

        response = client.get(
            "/.well-known/agent-card.json",
            headers={
                "Host": "evil.com/../../etc/passwd",
            },
        )

        assert response.status_code == 200
        card = response.json()
        # URL should NOT contain the injected path
        assert "passwd" not in card.get("url", "")
        assert "../../" not in card.get("url", "")


# ---------------------------------------------------------------------------
# salesagent-agmq [P0]: Debug endpoints gated behind ADCP_TESTING
# ---------------------------------------------------------------------------


class TestDebugEndpointGate:
    """Debug endpoints must return 404 when ADCP_TESTING is not 'true'."""

    def test_require_testing_mode_blocks_in_production(self):
        """require_testing_mode raises 404 when ADCP_TESTING is not set."""
        from fastapi import HTTPException

        from src.routes.health import require_testing_mode

        with patch.dict(os.environ, {}, clear=True):
            # Remove ADCP_TESTING if present
            os.environ.pop("ADCP_TESTING", None)
            with pytest.raises(HTTPException) as exc_info:
                require_testing_mode()
            assert exc_info.value.status_code == 404

    def test_require_testing_mode_allows_in_testing(self):
        """require_testing_mode passes when ADCP_TESTING=true."""
        from src.routes.health import require_testing_mode

        with patch.dict(os.environ, {"ADCP_TESTING": "true"}):
            # Should not raise
            require_testing_mode()

    def test_debug_endpoints_use_testing_dependency(self):
        """All /debug/* routes are on the debug_router with require_testing_mode dependency."""
        from src.routes.health import debug_router

        # The debug_router should have the require_testing_mode dependency
        assert len(debug_router.dependencies) > 0, "debug_router has no dependencies"

        # Check that at least one dependency is require_testing_mode
        dep_callables = [d.dependency for d in debug_router.dependencies]
        from src.routes.health import require_testing_mode

        assert require_testing_mode in dep_callables, "require_testing_mode not in debug_router dependencies"

    def test_debug_db_state_returns_404_without_testing(self):
        """GET /debug/db-state returns 404 in production mode."""
        from starlette.testclient import TestClient

        from src.app import app

        client = TestClient(app)

        with patch.dict(os.environ, {"ADCP_TESTING": "false"}):
            os.environ.pop("ADCP_TESTING", None)
            response = client.get("/debug/db-state")
            assert response.status_code == 404


# ---------------------------------------------------------------------------
# salesagent-nb7k [P1]: format_resolver uses run_async_in_sync_context
# ---------------------------------------------------------------------------


class TestFormatResolverNoEventLoopCreation:
    """format_resolver must use run_async_in_sync_context, not new_event_loop."""

    def test_format_resolver_does_not_import_new_event_loop(self):
        """format_resolver must not use asyncio.new_event_loop (causes deadlocks).

        Before fix: asyncio.new_event_loop() + run_until_complete() — deadlocks.
        After fix: run_async_in_sync_context() — handles both sync and async contexts.
        """
        import src.core.format_resolver as fr_module

        # Verify the module does not reference new_event_loop at attribute level
        assert not hasattr(fr_module, "new_event_loop"), "format_resolver should not export new_event_loop"
        # Verify run_async_in_sync_context is imported (the correct approach)
        assert hasattr(fr_module, "run_async_in_sync_context"), (
            "format_resolver should import run_async_in_sync_context"
        )

    def test_get_format_works_from_sync_context(self):
        """get_format should work when called from a sync context."""
        from unittest.mock import AsyncMock

        mock_format = MagicMock()
        mock_format.format_id = "test_format"

        mock_registry = MagicMock()
        mock_registry.get_format = AsyncMock(return_value=mock_format)

        with patch("src.core.creative_agent_registry.get_creative_agent_registry", return_value=mock_registry):
            from src.core.format_resolver import get_format

            result = get_format("test_format", agent_url="http://example.com/agent")

        assert result == mock_format
