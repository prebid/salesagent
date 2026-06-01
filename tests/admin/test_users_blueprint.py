"""Integration tests for the users admin blueprint.

Tests user CRUD, role updates, authorized-domain management, and the
``disable-setup-mode`` / ``enable-setup-mode`` switches via Flask test client.
Requires PostgreSQL (integration_db fixture).

Uses factory-boy factories (``TenantFactory``, ``UserFactory``,
``TenantAuthConfigFactory``) per ``tests/CLAUDE.md`` — no inline
``session.add()`` in test bodies.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from src.admin.app import create_app
from src.core.database.models import Tenant, TenantAuthConfig, User
from tests.factories import TenantAuthConfigFactory, TenantFactory, UserFactory

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


def _auth_session(client, tenant_id: str, *, auth_method: str | None = None) -> None:
    """Populate a super-admin test-mode session.

    Pass ``auth_method='oidc'`` to exercise routes that gate on SSO login
    (e.g. ``disable-setup-mode``).
    """
    with client.session_transaction() as sess:
        sess["authenticated"] = True
        sess["user"] = {"email": "test@example.com", "is_super_admin": True}
        sess["email"] = "test@example.com"
        sess["tenant_id"] = tenant_id
        sess["test_user"] = "test@example.com"
        sess["test_user_role"] = "super_admin"
        sess["test_user_name"] = "Test User"
        sess["test_tenant_id"] = tenant_id
        if auth_method is not None:
            sess["auth_method"] = auth_method


@pytest.fixture(autouse=True)
def _enable_test_mode(monkeypatch):
    """Enable global test auth so require_tenant_access accepts the test session."""
    monkeypatch.setenv("ADCP_AUTH_TEST_MODE", "true")


class TestListUsersPage:
    """GET /tenant/<id>/users — render the users list page."""

    def test_list_page_returns_200(self, client, factory_session):
        tenant = TenantFactory()
        _auth_session(client, tenant.tenant_id)
        response = client.get(f"/tenant/{tenant.tenant_id}/users")
        assert response.status_code == 200

    def test_list_page_shows_existing_user(self, client, factory_session):
        tenant = TenantFactory()
        user = UserFactory(tenant=tenant, email="existing@example.com", name="Existing User")
        _auth_session(client, tenant.tenant_id)
        response = client.get(f"/tenant/{tenant.tenant_id}/users")
        assert user.email in response.data.decode()


class TestAddUser:
    """POST /tenant/<id>/users/add — create a new user."""

    def test_add_user_creates_row(self, client, factory_session):
        tenant = TenantFactory()
        _auth_session(client, tenant.tenant_id)

        response = client.post(
            f"/tenant/{tenant.tenant_id}/users/add",
            data={"email": "new@example.com", "name": "New User", "role": "manager"},
        )
        # All terminal paths of the add_user handler return redirect(url_for(...)).
        assert response.status_code == 302

        user = factory_session.scalars(
            select(User).filter_by(tenant_id=tenant.tenant_id, email="new@example.com")
        ).first()
        assert user is not None
        assert user.role == "manager"
        assert user.is_active is True

    def test_add_user_rejects_invalid_email(self, client, factory_session):
        tenant = TenantFactory()
        _auth_session(client, tenant.tenant_id)

        client.post(
            f"/tenant/{tenant.tenant_id}/users/add",
            data={"email": "not-an-email", "role": "viewer"},
        )

        # Invalid email must not create a row.
        user = factory_session.scalars(select(User).filter_by(tenant_id=tenant.tenant_id, email="not-an-email")).first()
        assert user is None


class TestToggleUser:
    """POST /tenant/<id>/users/<user_id>/toggle — flip is_active."""

    def test_toggle_deactivates_active_user(self, client, factory_session):
        tenant = TenantFactory()
        user = UserFactory(tenant=tenant, is_active=True)
        _auth_session(client, tenant.tenant_id)

        response = client.post(f"/tenant/{tenant.tenant_id}/users/{user.user_id}/toggle")
        assert response.status_code == 302

        factory_session.expire_all()
        refreshed = factory_session.get(User, user.user_id)
        assert refreshed.is_active is False


class TestUpdateRole:
    """POST /tenant/<id>/users/<user_id>/update_role — change role."""

    def test_update_role_to_admin(self, client, factory_session):
        tenant = TenantFactory()
        user = UserFactory(tenant=tenant, role="viewer")
        _auth_session(client, tenant.tenant_id)

        client.post(
            f"/tenant/{tenant.tenant_id}/users/{user.user_id}/update_role",
            data={"role": "admin"},
        )

        factory_session.expire_all()
        refreshed = factory_session.get(User, user.user_id)
        assert refreshed.role == "admin"

    def test_update_role_rejects_invalid_role(self, client, factory_session):
        tenant = TenantFactory()
        user = UserFactory(tenant=tenant, role="viewer")
        _auth_session(client, tenant.tenant_id)

        client.post(
            f"/tenant/{tenant.tenant_id}/users/{user.user_id}/update_role",
            data={"role": "superuser"},
        )

        factory_session.expire_all()
        refreshed = factory_session.get(User, user.user_id)
        # Role unchanged — endpoint rejects unknown roles.
        assert refreshed.role == "viewer"


class TestAuthorizedDomains:
    """POST/DELETE /tenant/<id>/users/domains — authorized domain list."""

    def test_add_domain_appends_to_list(self, client, factory_session):
        tenant = TenantFactory(authorized_domains=["example.com"])
        _auth_session(client, tenant.tenant_id)

        response = client.post(
            f"/tenant/{tenant.tenant_id}/users/domains",
            json={"domain": "new-domain.com"},
        )
        assert response.status_code == 200
        assert response.get_json()["success"] is True

        factory_session.expire_all()
        refreshed = factory_session.get(Tenant, tenant.tenant_id)
        assert "new-domain.com" in refreshed.authorized_domains

    def test_add_domain_rejects_duplicate(self, client, factory_session):
        tenant = TenantFactory(authorized_domains=["example.com"])
        _auth_session(client, tenant.tenant_id)

        response = client.post(
            f"/tenant/{tenant.tenant_id}/users/domains",
            json={"domain": "example.com"},
        )
        assert response.status_code == 400
        assert "already exists" in response.get_json()["error"].lower()


class TestDisableSetupModeAuth:
    """POST /tenant/<id>/users/disable-setup-mode — requires SSO login.

    These tests guard the F-02 lockout prevention: the endpoint must refuse
    to disable test-auth unless the caller is actually logged in via OIDC.
    """

    def test_blocks_when_not_sso_authenticated(self, client, factory_session):
        """403 when session lacks auth_method=oidc."""
        tenant = TenantFactory(auth_setup_mode=True)
        _auth_session(client, tenant.tenant_id)  # no auth_method set

        response = client.post(f"/tenant/{tenant.tenant_id}/users/disable-setup-mode")
        assert response.status_code == 403
        body = response.get_json()
        assert body["success"] is False
        assert "sso" in body["error"].lower()

    def test_rejects_when_sso_not_configured(self, client, factory_session):
        """400 when caller is SSO-authenticated but tenant has no oidc_enabled config."""
        tenant = TenantFactory(auth_setup_mode=True)
        _auth_session(client, tenant.tenant_id, auth_method="oidc")

        response = client.post(f"/tenant/{tenant.tenant_id}/users/disable-setup-mode")
        assert response.status_code == 400
        body = response.get_json()
        assert body["success"] is False
        assert "sso" in body["error"].lower()

        # Guardrail: setup mode must NOT be disabled when the precondition fails.
        factory_session.expire_all()
        refreshed = factory_session.get(Tenant, tenant.tenant_id)
        assert refreshed.auth_setup_mode is True

    def test_succeeds_with_sso_and_enabled_config(self, client, factory_session):
        """200 + auth_setup_mode flipped to False when all preconditions met."""
        tenant = TenantFactory(auth_setup_mode=True)
        TenantAuthConfigFactory(tenant=tenant, oidc_enabled=True)
        _auth_session(client, tenant.tenant_id, auth_method="oidc")

        response = client.post(f"/tenant/{tenant.tenant_id}/users/disable-setup-mode")
        assert response.status_code == 200
        assert response.get_json()["success"] is True

        factory_session.expire_all()
        refreshed = factory_session.get(Tenant, tenant.tenant_id)
        assert refreshed.auth_setup_mode is False


class TestEnableSetupMode:
    """POST /tenant/<id>/users/enable-setup-mode — re-enable test credentials."""

    def test_enables_setup_mode(self, client, factory_session):
        tenant = TenantFactory(auth_setup_mode=False)
        _auth_session(client, tenant.tenant_id)

        response = client.post(f"/tenant/{tenant.tenant_id}/users/enable-setup-mode")
        assert response.status_code == 200
        assert response.get_json()["success"] is True

        factory_session.expire_all()
        refreshed = factory_session.get(Tenant, tenant.tenant_id)
        assert refreshed.auth_setup_mode is True

    def test_returns_404_for_unknown_tenant(self, client, factory_session):
        # Auth the session against a real tenant (super-admin bypasses tenant scoping).
        tenant = TenantFactory()
        _auth_session(client, tenant.tenant_id)

        response = client.post("/tenant/nonexistent_tenant_id/users/enable-setup-mode")
        assert response.status_code == 404
        body = response.get_json()
        assert body["success"] is False


class TestCrossBlueprintGuardrails:
    """Integration: the TenantAuthConfig factory defaults keep oidc_enabled=False.

    This prevents accidental lockout-prevention bypass via a factory default —
    tests that need oidc_enabled=True must opt in explicitly.
    """

    def test_tenant_auth_config_factory_defaults_oidc_disabled(self, factory_session):
        tenant = TenantFactory()
        cfg = TenantAuthConfigFactory(tenant=tenant)
        factory_session.expire_all()
        refreshed = factory_session.scalars(select(TenantAuthConfig).filter_by(tenant_id=tenant.tenant_id)).first()
        assert refreshed is not None
        assert refreshed.oidc_enabled is False
        # Smoke check the factory-created client id is non-empty.
        assert refreshed.oidc_client_id
        assert cfg.oidc_client_id == refreshed.oidc_client_id
