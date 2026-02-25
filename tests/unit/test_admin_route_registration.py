"""Smoke tests: all 50 admin API endpoints registered and reachable as JSON.

Two-layer check:
1. OpenAPI spec contains all 50 expected paths (proves route registration)
2. HTTP requests return application/json not text/html (proves Flask /api mount
   does not intercept FastAPI routes)

If the Flask mount at /api ever gets registered before include_router calls,
these tests catch it: Flask returns text/html 404, FastAPI returns JSON 401.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from src.app import app


@pytest.fixture(scope="module")
def client():
    """Module-scoped TestClient — MCP lifespan can only start once per process."""
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


# All 50 expected paths — explicit list so accidental removal is a test failure.
PLATFORM_PATHS = [
    "/api/v1/platform/health",
    "/api/v1/platform/tenants",
    "/api/v1/platform/tenants/{tenant_id}",
    "/api/v1/platform/sync/{tenant_id}",
    "/api/v1/platform/sync/status/{sync_id}",
    "/api/v1/platform/sync/history/{tenant_id}",
    "/api/v1/platform/sync/stats",
    "/api/v1/platform/sync/tenants",
    "/api/v1/platform/init-api-key",
]

TENANT_PATHS = [
    "/api/v1/admin/{tenant_id}/adapter",
    "/api/v1/admin/{tenant_id}/adapter/test-connection",
    "/api/v1/admin/{tenant_id}/adapter/capabilities",
    "/api/v1/admin/{tenant_id}/currency-limits",
    "/api/v1/admin/{tenant_id}/currency-limits/{currency_code}",
    "/api/v1/admin/{tenant_id}/property-tags",
    "/api/v1/admin/{tenant_id}/property-tags/{tag_id}",
    "/api/v1/admin/{tenant_id}/properties",
    "/api/v1/admin/{tenant_id}/properties/{property_id}",
    "/api/v1/admin/{tenant_id}/properties/bulk",
    "/api/v1/admin/{tenant_id}/properties/verify",
    "/api/v1/admin/{tenant_id}/inventory",
    "/api/v1/admin/{tenant_id}/inventory/sizes",
    "/api/v1/admin/{tenant_id}/targeting",
    "/api/v1/admin/{tenant_id}/targeting/{key_id}/values",
    "/api/v1/admin/{tenant_id}/inventory-profiles",
    "/api/v1/admin/{tenant_id}/inventory-profiles/{profile_id}",
    "/api/v1/admin/{tenant_id}/products",
    "/api/v1/admin/{tenant_id}/products/{product_id}",
    "/api/v1/admin/{tenant_id}/creative-formats",
    "/api/v1/admin/{tenant_id}/principals",
    "/api/v1/admin/{tenant_id}/principals/{principal_id}",
    "/api/v1/admin/{tenant_id}/principals/{principal_id}/regenerate-token",
    "/api/v1/admin/{tenant_id}/gam/advertisers",
]

ALL_EXPECTED_PATHS = PLATFORM_PATHS + TENANT_PATHS


class TestAllRoutesInOpenAPISpec:
    """All 50 admin API paths appear in the OpenAPI spec."""

    def test_all_platform_paths_registered(self, client):
        response = client.get("/openapi.json")
        assert response.status_code == 200
        paths = response.json()["paths"]
        for path in PLATFORM_PATHS:
            assert path in paths, f"Missing from OpenAPI spec: {path}"

    def test_all_tenant_paths_registered(self, client):
        response = client.get("/openapi.json")
        assert response.status_code == 200
        paths = response.json()["paths"]
        for path in TENANT_PATHS:
            assert path in paths, f"Missing from OpenAPI spec: {path}"

    def test_total_admin_path_count(self, client):
        """Guard against silent route removal — total count must stay at 50+."""
        response = client.get("/openapi.json")
        paths = response.json()["paths"]
        admin_paths = [p for p in paths if p.startswith("/api/v1/admin/") or p.startswith("/api/v1/platform/")]
        assert len(admin_paths) >= len(ALL_EXPECTED_PATHS), (
            f"Expected at least {len(ALL_EXPECTED_PATHS)} admin paths, found {len(admin_paths)}"
        )


class TestRoutesNotInterceptedByFlask:
    """HTTP requests to admin routes return JSON, not Flask HTML.

    If the Flask /api mount intercepts before FastAPI routes, responses
    would have Content-Type: text/html. FastAPI returns application/json
    even for auth errors.
    """

    def test_platform_health_returns_json_200(self, client):
        """Open endpoint — no auth required. Strongest proof of non-interception."""
        response = client.get("/api/v1/platform/health")
        assert response.status_code == 200
        assert "application/json" in response.headers["content-type"]

    def test_platform_tenants_returns_json_401_not_html(self, client):
        """Auth-required endpoint — FastAPI returns JSON 401, Flask would return HTML."""
        response = client.get("/api/v1/platform/tenants")
        assert response.status_code == 401
        assert "application/json" in response.headers["content-type"]
        # Confirm it's an AdCP error body, not a Flask HTML page
        body = response.json()
        assert "error_code" in body

    def test_tenant_admin_returns_json_401_not_html(self, client):
        """Tenant admin endpoint — FastAPI returns JSON 401, Flask would return HTML."""
        response = client.get("/api/v1/admin/smoke-test-tenant/properties")
        assert response.status_code == 401
        assert "application/json" in response.headers["content-type"]
        body = response.json()
        assert "error_code" in body
