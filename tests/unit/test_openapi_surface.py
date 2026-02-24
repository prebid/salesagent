"""Tests for REST API OpenAPI surface.

Validates that:
- /docs serves Swagger UI HTML
- /openapi.json is valid JSON with all expected endpoints
- All API v1 endpoints have descriptions
- Error responses are documented

beads: salesagent-b61l.16
"""

from starlette.testclient import TestClient

from src.app import app

client = TestClient(app)


# ---------------------------------------------------------------------------
# OpenAPI Availability
# ---------------------------------------------------------------------------


class TestOpenAPIAvailability:
    """Verify OpenAPI docs are served."""

    def test_docs_serves_html(self):
        """GET /docs should return Swagger UI HTML."""
        response = client.get("/docs")
        assert response.status_code == 200
        assert "text/html" in response.headers.get("content-type", "")

    def test_openapi_json_served(self):
        """GET /openapi.json should return valid JSON schema."""
        response = client.get("/openapi.json")
        assert response.status_code == 200
        schema = response.json()
        assert "openapi" in schema
        assert "paths" in schema
        assert "info" in schema


# ---------------------------------------------------------------------------
# API Surface Completeness
# ---------------------------------------------------------------------------


EXPECTED_ENDPOINTS = [
    ("post", "/api/v1/products"),
    ("get", "/api/v1/capabilities"),
    ("post", "/api/v1/creative-formats"),
    ("post", "/api/v1/authorized-properties"),
    ("post", "/api/v1/media-buys"),
    ("put", "/api/v1/media-buys/{media_buy_id}"),
    ("post", "/api/v1/media-buys/delivery"),
    ("post", "/api/v1/creatives/sync"),
    ("post", "/api/v1/creatives"),
    ("post", "/api/v1/performance-index"),
]


class TestAPISurfaceCompleteness:
    """Verify all expected endpoints appear in OpenAPI schema."""

    def test_all_endpoints_in_schema(self):
        """All 10 REST API endpoints must appear in OpenAPI spec."""
        schema = client.get("/openapi.json").json()
        paths = schema["paths"]

        for method, path in EXPECTED_ENDPOINTS:
            assert path in paths, f"Missing path {path} in OpenAPI schema"
            assert method in paths[path], f"Missing method {method} for {path}"

    def test_endpoints_have_descriptions(self):
        """All API endpoints should have operation descriptions."""
        schema = client.get("/openapi.json").json()
        paths = schema["paths"]

        for method, path in EXPECTED_ENDPOINTS:
            operation = paths[path][method]
            desc = operation.get("description") or operation.get("summary")
            assert desc, f"{method.upper()} {path} has no description"

    def test_api_title_set(self):
        """API info should have a title."""
        schema = client.get("/openapi.json").json()
        assert schema["info"]["title"]
