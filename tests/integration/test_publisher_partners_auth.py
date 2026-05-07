"""Auth gate on the publisher_partners blueprint.

Closes #65: the 5 routes accept GET/POST/DELETE under tenant scope and
**must** require authentication. Pre-fix: anonymous callers could
enumerate partners, create rogue records, delete by sequential ID, or
trigger outbound sync (SSRF amplification). Each route now wears
``@require_tenant_access(api_mode=True)``.

The structural guard
``tests/unit/test_architecture_tenant_routes_decorated.py`` enforces the
decorator's *presence*; this file locks in the *behavior*. If the
decorator's enforcement ever loosens, we want to know here too — not
just at AST level.

The test uses an arbitrary tenant_id — anonymous callers hit the auth
check *before* the route handler runs, so the tenant doesn't need to
exist in the DB. Keeps the test cheap and avoids re-deriving a
factory-binding fixture.
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


@pytest.fixture
def app(integration_db, monkeypatch):
    """Bare admin app, MANAGED_INSTANCE off so embedded auth doesn't
    intervene — the test exercises the OAuth/test-mode code path."""
    monkeypatch.delenv("MANAGED_INSTANCE", raising=False)
    monkeypatch.setenv("ADCP_AUTH_TEST_MODE", "false")

    from src.admin.app import create_app

    application = create_app()
    application.config["TESTING"] = True
    return application


@pytest.fixture
def client(app):
    return app.test_client()


# Each row is (HTTP method, URL, JSON body or None) covering all 5 routes
# the PR added the decorator to. Parameterized so a single test run locks
# in the auth gate across the whole surface — if any future change loosens
# enforcement on any one route, this catches it.
ROUTES = [
    ("GET", "/tenant/any-tenant/publisher-partners", None),
    ("POST", "/tenant/any-tenant/publisher-partners", {"publisher_domain": "example.com"}),
    ("DELETE", "/tenant/any-tenant/publisher-partners/1", None),
    ("POST", "/tenant/any-tenant/publisher-partners/sync", None),
    ("GET", "/tenant/any-tenant/publisher-partners/1/properties", None),
]


class TestPublisherPartnersAuth:
    """Anonymous callers must hit the auth gate, not the route handler.

    api_mode=True returns 401 JSON for anonymous (so JS clients can branch
    on status without parsing HTML).
    """

    @pytest.mark.parametrize("method,url,body", ROUTES)
    def test_anonymous_returns_401(self, client, method, url, body):
        if body is None:
            resp = client.open(url, method=method)
        else:
            resp = client.open(url, method=method, json=body)
        assert (
            resp.status_code == 401
        ), f"{method} {url} should require auth (got {resp.status_code}): {resp.get_data(as_text=True)[:200]}"
        body_json = resp.get_json()
        assert body_json is not None, "expected JSON envelope, not HTML"
        assert "Authentication required" in body_json.get("error", ""), body_json
