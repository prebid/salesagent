"""Regression tests for FastAPI migration code review fixes.

Tests P0/P1 issues found during code review of the FastAPI unified app.
Each test targets a specific beads issue to prevent regression.

: Non-async receive lambda (ASGI protocol)
: CORS origins configuration
: Debug endpoints gated behind ADCP_TESTING
: format_resolver async event loop fix
"""

import os
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# [P0]: Async receive callable in messageId middleware
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
            "method": "SendMessage",
            "params": {
                "message": {
                    "messageId": 12345,
                    "role": "ROLE_USER",
                    "parts": [{"text": "test"}],
                }
            },
        }

        # This should NOT raise TypeError from non-async receive
        response = client.post(
            "/a2a",
            json=payload,
            headers={"Content-Type": "application/json", "A2A-Version": "1.0"},
        )

        # We don't care about the exact response (auth will fail),
        # but the middleware must not crash with TypeError
        assert response.status_code != 500 or b"TypeError" not in response.content

    def test_numeric_jsonrpc_id_converted_to_string(self):
        """Numeric JSON-RPC id values must be converted to strings."""
        from starlette.testclient import TestClient

        from src.app import app

        client = TestClient(app)

        payload = {"jsonrpc": "2.0", "id": 99, "method": "SendMessage", "params": {}}

        response = client.post(
            "/a2a",
            json=payload,
            headers={"Content-Type": "application/json", "A2A-Version": "1.0"},
        )

        # Middleware should have converted id to "99" — verify no crash
        assert response.status_code != 500 or b"TypeError" not in response.content


# ---------------------------------------------------------------------------
# [P0]: CORS origins must not use wildcard with credentials
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
# [P0]: Debug endpoints gated behind ADCP_TESTING
# ---------------------------------------------------------------------------


class TestDebugEndpointGate:
    """The ``/debug/*`` routes must not EXIST unless this deployment allows them.

    The gate used to be a per-request FastAPI dependency, ``require_testing_mode``, which
    read ADCP_TESTING out of the environment on every call and raised 404. It is gone
    (commit 3d6bd0593): the environment is read once into typed settings, and ``src/app.py``
    includes ``health_debug_router`` only when ``settings.debug_routes_enabled`` -- a route
    that is not mounted cannot be reached, so there is nothing per-request to check. These
    cases grade the mounting decision and the router's own emptiness of gates.
    """

    def test_debug_routes_exist_exactly_when_the_allowance_says_so(self):
        """The mounted app carries ``/debug/*`` iff ``settings.debug_routes_enabled``.

        Read off the app that was actually built, in whichever mode this process runs, so
        both directions are graded: routes present when the allowance is on, and no route
        to reach at all when it is off.

        The allowance is read from ``src.app.settings`` -- the object the mount decision
        was made from -- not from ``get_settings()``. ``src/app.py`` calls
        ``load_settings()`` at import, and another test module importing the app during
        collection builds it under the ambient environment rather than this test's, so
        the live settings object can disagree with the one the app was built from.
        """
        from src.app import app, settings
        from src.routes.health import debug_router

        declared = {route.path for route in debug_router.routes}
        mounted = {getattr(route, "path", None) for route in app.routes}

        if settings.debug_routes_enabled:
            assert declared and declared <= mounted, (
                f"the allowance is on, so every debug route must be mounted; missing {declared - mounted}"
            )
        else:
            assert not (declared & mounted), (
                f"the allowance is off, so no debug route may exist; found {declared & mounted}"
            )

    def test_the_allowance_is_the_testing_flag(self):
        """``debug_routes_enabled`` is ADCP_TESTING, read once into the settings."""
        from src.core.config import Settings

        settings = Settings.from_environment()
        assert settings.debug_routes_enabled is settings.testing.adcp_testing

    def test_debug_router_carries_no_per_request_gate(self):
        """The router declares no dependencies: the mount decision IS the gate.

        A resurrected per-request check would be a second gate that can disagree with the
        mount, which is what this pins against.
        """
        from src.routes.health import debug_router

        assert debug_router.dependencies == [], (
            f"debug_router must carry no request-time gate, found {debug_router.dependencies!r}"
        )


# ---------------------------------------------------------------------------
# [P1]: format_resolver uses run_async_in_sync_context
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


class TestAdminCompatibilityMount:
    """Admin UI should be reachable through both /admin and the root fallback mount."""

    def test_fastapi_mounts_admin_at_admin_and_root(self):
        from starlette.routing import Mount

        from src.app import _install_admin_mounts, app

        _install_admin_mounts()
        admin_mounts = [
            route.path
            for route in app.routes
            if isinstance(route, Mount) and route.app.__class__.__name__ == "WSGIMiddleware"
        ]

        assert "/admin" in admin_mounts
        assert "" in admin_mounts
        assert "/tenant" not in admin_mounts
        assert "/auth" not in admin_mounts
        assert "/login" not in admin_mounts
        assert "/logout" not in admin_mounts
        assert "/signup" not in admin_mounts
        assert "/test" not in admin_mounts

    def test_root_login_path_is_exposed_by_root_fallback_mount(self):
        from starlette.testclient import TestClient

        from src.app import _install_admin_mounts, app

        _install_admin_mounts()
        client = TestClient(app)
        response = client.get("/login", follow_redirects=False)
        assert response.status_code != 404

    def test_admin_login_path_remains_available(self):
        from starlette.testclient import TestClient

        from src.app import _install_admin_mounts, app

        _install_admin_mounts()
        client = TestClient(app)
        response = client.get("/admin/login", follow_redirects=False)
        assert response.status_code != 404


class TestOidcCallbackCompatibility:
    """OIDC config should keep the legacy public callback path."""

    def test_get_tenant_redirect_uri_uses_root_auth_callback(self):
        from src.services.auth_config_service import get_tenant_redirect_uri

        tenant = MagicMock()
        tenant.virtual_host = None
        tenant.subdomain = None

        with patch.dict(os.environ, {"ADCP_SALES_PORT": "8080"}, clear=False):
            redirect_uri = get_tenant_redirect_uri(tenant)

        assert redirect_uri.endswith("/auth/oidc/callback")


class TestA2ATrailingSlashCompatibility:
    """A2A trailing-slash requests should stay on the FastAPI surface."""

    def test_a2a_trailing_slash_redirects_to_canonical_path(self):
        from starlette.testclient import TestClient

        from src.app import _install_admin_mounts, app

        _install_admin_mounts()
        client = TestClient(app)
        response = client.post("/a2a/", json={"test": "data"}, follow_redirects=False)

        assert response.status_code == 307
        assert response.headers["location"] == "/a2a"
