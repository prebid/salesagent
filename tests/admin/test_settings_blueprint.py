"""Integration tests for the settings admin blueprint.

Covers the security-sensitive slice of src/admin/blueprints/settings.py:
  - /domains/add, /domains/remove  — authorized_domains CRUD
  - /emails/add, /emails/remove    — authorized_emails CRUD
  - /approximated-token            — DNS widget token generation (external API)

Does NOT yet cover: /general, /adapter, /slack, /ai, /ai/test, /ai/models,
/business-rules, /approximated-domain-status|register|unregister. Those
routes mix tenant config saves + external API calls and warrant their
own test file with richer mocking.

Uses factory-boy factories per tests/CLAUDE.md.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.admin.app import create_app
from src.core.database.models import Tenant
from tests.factories import TenantFactory

app = create_app()

pytestmark = [pytest.mark.admin, pytest.mark.requires_db]


@pytest.fixture
def client():
    """Flask test client with CSRF disabled for POST testing."""
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["SESSION_COOKIE_PATH"] = "/"
    with app.test_client() as client:
        yield client


def _auth_session(client, tenant_id: str) -> None:
    """Populate a super-admin test-mode session."""
    with client.session_transaction() as sess:
        sess["authenticated"] = True
        sess["user"] = {"email": "test@example.com", "is_super_admin": True}
        sess["email"] = "test@example.com"
        sess["tenant_id"] = tenant_id
        sess["test_user"] = "test@example.com"
        sess["test_user_role"] = "super_admin"
        sess["test_user_name"] = "Test User"
        sess["test_tenant_id"] = tenant_id


@pytest.fixture(autouse=True)
def _enable_test_mode(monkeypatch):
    """Enable global test auth so require_tenant_access accepts the test session."""
    monkeypatch.setenv("ADCP_AUTH_TEST_MODE", "true")


class TestAuthorizedDomainsAdd:
    """POST /tenant/<id>/settings/domains/add — appends to authorized_domains."""

    def test_add_domain_appends_to_list(self, client, factory_session):
        tenant = TenantFactory(authorized_domains=["example.com"])
        factory_session.commit()
        _auth_session(client, tenant.tenant_id)

        response = client.post(
            f"/tenant/{tenant.tenant_id}/settings/domains/add",
            data={"domain": "new-domain.com"},
        )
        # Flash-based endpoint — always redirects back to tenant settings.
        assert response.status_code == 302

        factory_session.expire_all()
        refreshed = factory_session.get(Tenant, tenant.tenant_id)
        assert "new-domain.com" in refreshed.authorized_domains

    def test_add_domain_rejects_missing_field(self, client, factory_session):
        tenant = TenantFactory(authorized_domains=["example.com"])
        factory_session.commit()
        _auth_session(client, tenant.tenant_id)

        response = client.post(
            f"/tenant/{tenant.tenant_id}/settings/domains/add",
            data={},  # no 'domain' field
        )
        assert response.status_code == 302  # redirects with flash error

        # Guardrail: the authorized_domains list must NOT have grown.
        factory_session.expire_all()
        refreshed = factory_session.get(Tenant, tenant.tenant_id)
        assert refreshed.authorized_domains == ["example.com"]

    def test_add_domain_rejects_invalid_format(self, client, factory_session):
        """Missing '.' or containing '@' must be rejected."""
        tenant = TenantFactory(authorized_domains=["example.com"])
        factory_session.commit()
        _auth_session(client, tenant.tenant_id)

        response = client.post(
            f"/tenant/{tenant.tenant_id}/settings/domains/add",
            data={"domain": "not-a-domain"},  # no '.'
        )
        assert response.status_code == 302

        factory_session.expire_all()
        refreshed = factory_session.get(Tenant, tenant.tenant_id)
        assert "not-a-domain" not in refreshed.authorized_domains

    def test_add_domain_is_idempotent(self, client, factory_session):
        """Adding an already-present domain must not duplicate it."""
        tenant = TenantFactory(authorized_domains=["example.com"])
        factory_session.commit()
        _auth_session(client, tenant.tenant_id)

        client.post(
            f"/tenant/{tenant.tenant_id}/settings/domains/add",
            data={"domain": "example.com"},
        )

        factory_session.expire_all()
        refreshed = factory_session.get(Tenant, tenant.tenant_id)
        # Domain appears exactly once — no duplicate appended.
        assert refreshed.authorized_domains.count("example.com") == 1


class TestAuthorizedDomainsSuperAdminHijackGuard:
    """Security guard: refuse to add the super-admin domain to any tenant."""

    def test_refuses_to_add_super_admin_domain(self, client, factory_session, monkeypatch):
        monkeypatch.setenv("SUPER_ADMIN_DOMAIN", "admin-controlled.example")
        tenant = TenantFactory(authorized_domains=[])
        factory_session.commit()
        _auth_session(client, tenant.tenant_id)

        client.post(
            f"/tenant/{tenant.tenant_id}/settings/domains/add",
            data={"domain": "admin-controlled.example"},
        )

        factory_session.expire_all()
        refreshed = factory_session.get(Tenant, tenant.tenant_id)
        assert "admin-controlled.example" not in (refreshed.authorized_domains or [])


class TestAuthorizedDomainsRemove:
    """POST /tenant/<id>/settings/domains/remove — drops from authorized_domains."""

    def test_remove_existing_domain(self, client, factory_session):
        tenant = TenantFactory(authorized_domains=["example.com", "other.com"])
        factory_session.commit()
        _auth_session(client, tenant.tenant_id)

        response = client.post(
            f"/tenant/{tenant.tenant_id}/settings/domains/remove",
            data={"domain": "other.com"},
        )
        assert response.status_code == 302

        factory_session.expire_all()
        refreshed = factory_session.get(Tenant, tenant.tenant_id)
        assert "other.com" not in refreshed.authorized_domains
        assert "example.com" in refreshed.authorized_domains

    def test_remove_missing_field(self, client, factory_session):
        tenant = TenantFactory(authorized_domains=["example.com"])
        factory_session.commit()
        _auth_session(client, tenant.tenant_id)

        response = client.post(
            f"/tenant/{tenant.tenant_id}/settings/domains/remove",
            data={},
        )
        assert response.status_code == 302

        factory_session.expire_all()
        refreshed = factory_session.get(Tenant, tenant.tenant_id)
        # No change — list intact.
        assert refreshed.authorized_domains == ["example.com"]


class TestAuthorizedEmailsAdd:
    """POST /tenant/<id>/settings/emails/add — appends to authorized_emails."""

    def test_add_email_appends_to_list(self, client, factory_session):
        tenant = TenantFactory(authorized_emails=[])
        factory_session.commit()
        _auth_session(client, tenant.tenant_id)

        response = client.post(
            f"/tenant/{tenant.tenant_id}/settings/emails/add",
            data={"email": "new-user@example.com"},
        )
        assert response.status_code == 302

        factory_session.expire_all()
        refreshed = factory_session.get(Tenant, tenant.tenant_id)
        assert "new-user@example.com" in refreshed.authorized_emails

    def test_add_email_rejects_malformed(self, client, factory_session):
        tenant = TenantFactory(authorized_emails=[])
        factory_session.commit()
        _auth_session(client, tenant.tenant_id)

        response = client.post(
            f"/tenant/{tenant.tenant_id}/settings/emails/add",
            data={"email": "not-an-email"},
        )
        assert response.status_code == 302

        factory_session.expire_all()
        refreshed = factory_session.get(Tenant, tenant.tenant_id)
        assert "not-an-email" not in (refreshed.authorized_emails or [])


class TestAuthorizedEmailsRemove:
    """POST /tenant/<id>/settings/emails/remove — drops from authorized_emails."""

    def test_remove_existing_email(self, client, factory_session):
        tenant = TenantFactory(authorized_emails=["test@example.com", "other@example.com"])
        factory_session.commit()
        _auth_session(client, tenant.tenant_id)

        response = client.post(
            f"/tenant/{tenant.tenant_id}/settings/emails/remove",
            data={"email": "other@example.com"},
        )
        assert response.status_code == 302

        factory_session.expire_all()
        refreshed = factory_session.get(Tenant, tenant.tenant_id)
        assert "other@example.com" not in refreshed.authorized_emails
        assert "test@example.com" in refreshed.authorized_emails


class TestApproximatedToken:
    """POST /tenant/<id>/settings/approximated-token — DNS widget token.

    This endpoint handles API-key-backed external requests (Approximated
    DNS service). Tests exercise the gate behaviors without making real
    network calls.
    """

    def test_returns_500_when_api_key_missing(self, client, factory_session, monkeypatch):
        """Without APPROXIMATED_API_KEY in env, route returns 500 + error JSON."""
        monkeypatch.delenv("APPROXIMATED_API_KEY", raising=False)
        tenant = TenantFactory()
        factory_session.commit()
        _auth_session(client, tenant.tenant_id)

        response = client.post(f"/tenant/{tenant.tenant_id}/settings/approximated-token")
        assert response.status_code == 500
        body = response.get_json()
        assert body["success"] is False
        assert "not configured" in body["error"].lower()

    def test_returns_404_when_tenant_not_found(self, client, factory_session, monkeypatch):
        """Requires a real tenant to back the token request."""
        monkeypatch.setenv("APPROXIMATED_API_KEY", "fake-api-key")
        # Auth against a real tenant (super-admin bypasses tenant scoping).
        tenant = TenantFactory()
        factory_session.commit()
        _auth_session(client, tenant.tenant_id)

        response = client.post("/tenant/nonexistent_tenant/settings/approximated-token")
        assert response.status_code == 404
        body = response.get_json()
        assert body["success"] is False

    def test_returns_token_on_success(self, client, factory_session, monkeypatch):
        """Happy path: Approximated API returns 200 → endpoint forwards the token."""
        monkeypatch.setenv("APPROXIMATED_API_KEY", "fake-api-key")
        monkeypatch.setenv("APPROXIMATED_PROXY_IP", "10.0.0.99")

        tenant = TenantFactory()
        factory_session.commit()
        _auth_session(client, tenant.tenant_id)

        # Mock requests.get inside the settings module's import scope.
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"token": "opaque-widget-token-123"}

        with patch("requests.get", return_value=mock_response) as mock_get:
            response = client.post(f"/tenant/{tenant.tenant_id}/settings/approximated-token")

        assert response.status_code == 200
        body = response.get_json()
        assert body["success"] is True
        assert body["token"] == "opaque-widget-token-123"
        assert body["proxy_ip"] == "10.0.0.99"

        # Security: API key must be sent in the request header, not leaked in body.
        called_kwargs = mock_get.call_args.kwargs
        assert called_kwargs["headers"]["api-key"] == "fake-api-key"

    def test_propagates_upstream_error(self, client, factory_session, monkeypatch):
        """Non-200 from Approximated → endpoint surfaces the upstream status."""
        monkeypatch.setenv("APPROXIMATED_API_KEY", "fake-api-key")
        tenant = TenantFactory()
        factory_session.commit()
        _auth_session(client, tenant.tenant_id)

        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.text = "Unauthorized"

        with patch("requests.get", return_value=mock_response):
            response = client.post(f"/tenant/{tenant.tenant_id}/settings/approximated-token")

        assert response.status_code == 401
        body = response.get_json()
        assert body["success"] is False
