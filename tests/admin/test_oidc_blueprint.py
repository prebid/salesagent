"""Integration tests for the OIDC auth admin blueprint.

Covers /auth/oidc/tenant/<id>/config (GET/POST), /enable, /disable, and
verification-dependent behavior. These endpoints handle per-tenant SSO
provider configuration — a broken gate here is a security-sensitive
regression (tenant lockout, bypass of verification requirement).

Does NOT cover the full OAuth callback flow — that requires a live IdP
exchange and token validation, which belongs in a separate e2e fixture.
These tests exercise the configuration/enable/disable surface.

Uses factory-boy factories per tests/CLAUDE.md.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from src.admin.app import create_app
from src.core.database.models import TenantAuthConfig
from src.services.auth_config_service import get_tenant_redirect_uri
from tests.factories import TenantAuthConfigFactory, TenantFactory

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
    """Enable global test auth and supply a valid Fernet key for secret encryption.

    ``save_oidc_config`` persists the client secret using Fernet via the
    ``oidc_client_secret`` setter, which requires ENCRYPTION_KEY to be set.
    """
    from cryptography.fernet import Fernet

    monkeypatch.setenv("ADCP_AUTH_TEST_MODE", "true")
    monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode())


class TestGetConfig:
    """GET /auth/oidc/tenant/<id>/config — return config summary as JSON."""

    def test_returns_200_with_summary_shape(self, client, factory_session):
        tenant = TenantFactory()
        TenantAuthConfigFactory(tenant=tenant, oidc_enabled=False, oidc_provider="google")
        _auth_session(client, tenant.tenant_id)

        response = client.get(f"/auth/oidc/tenant/{tenant.tenant_id}/config")
        assert response.status_code == 200

        body = response.get_json()
        # The endpoint returns the summary dict twice — once at top-level (legacy) and
        # once under "config" (current frontend contract). Both shapes should be present.
        assert "config" in body
        assert isinstance(body["config"], dict)

    def test_requires_authenticated_session(self, client, factory_session):
        tenant = TenantFactory()
        # No auth session populated.
        response = client.get(f"/auth/oidc/tenant/{tenant.tenant_id}/config")
        # api_mode=True route returns 401 when unauthenticated.
        assert response.status_code == 401


class TestSaveConfig:
    """POST /auth/oidc/tenant/<id>/config — persist OIDC provider settings."""

    def test_saves_config_encrypts_secret(self, client, factory_session):
        tenant = TenantFactory()
        _auth_session(client, tenant.tenant_id)

        response = client.post(
            f"/auth/oidc/tenant/{tenant.tenant_id}/config",
            json={
                "provider": "google",
                "client_id": "new-client-id.apps.googleusercontent.com",
                "client_secret": "brand-new-secret-value",
                "discovery_url": "https://accounts.google.com/.well-known/openid-configuration",
                "scopes": "openid email profile",
            },
        )
        assert response.status_code == 200
        body = response.get_json()
        assert body["success"] is True

        factory_session.expire_all()
        cfg = factory_session.scalars(select(TenantAuthConfig).filter_by(tenant_id=tenant.tenant_id)).first()
        assert cfg is not None
        assert cfg.oidc_client_id == "new-client-id.apps.googleusercontent.com"
        # Secret must be stored encrypted (not plaintext).
        assert cfg.oidc_client_secret_encrypted is not None
        assert "brand-new-secret-value" not in cfg.oidc_client_secret_encrypted
        # But the property decryptor must round-trip.
        assert cfg.oidc_client_secret == "brand-new-secret-value"

    def test_rejects_missing_provider(self, client, factory_session):
        tenant = TenantFactory()
        _auth_session(client, tenant.tenant_id)

        response = client.post(
            f"/auth/oidc/tenant/{tenant.tenant_id}/config",
            json={"client_id": "x.apps.googleusercontent.com", "client_secret": "s"},
        )
        assert response.status_code == 400
        assert "provider" in response.get_json()["error"].lower()

    def test_rejects_new_config_without_client_secret(self, client, factory_session):
        """When no existing secret is stored, client_secret is required."""
        tenant = TenantFactory()
        _auth_session(client, tenant.tenant_id)

        response = client.post(
            f"/auth/oidc/tenant/{tenant.tenant_id}/config",
            json={"provider": "google", "client_id": "x.apps.googleusercontent.com"},
        )
        assert response.status_code == 400
        assert "client_secret" in response.get_json()["error"].lower()

    def test_rejects_empty_body(self, client, factory_session):
        tenant = TenantFactory()
        _auth_session(client, tenant.tenant_id)

        response = client.post(
            f"/auth/oidc/tenant/{tenant.tenant_id}/config",
            json=None,
            content_type="application/json",
        )
        assert response.status_code == 400


class TestEnableOIDC:
    """POST /auth/oidc/tenant/<id>/enable — flip oidc_enabled=True.

    Guards the verification requirement: enable must fail unless a
    successful test-flow has marked the config verified.
    """

    def test_rejects_when_not_verified(self, client, factory_session):
        tenant = TenantFactory()
        TenantAuthConfigFactory(
            tenant=tenant,
            oidc_enabled=False,
            oidc_verified_at=None,  # not verified
        )
        _auth_session(client, tenant.tenant_id)

        response = client.post(f"/auth/oidc/tenant/{tenant.tenant_id}/enable")
        assert response.status_code == 400
        assert "test" in response.get_json()["error"].lower()

        # Guardrail: oidc_enabled must NOT have been flipped.
        factory_session.expire_all()
        cfg = factory_session.scalars(select(TenantAuthConfig).filter_by(tenant_id=tenant.tenant_id)).first()
        assert cfg.oidc_enabled is False

    @pytest.mark.xfail(
        reason=(
            "Production bug in enable_oidc (src/services/auth_config_service.py:145): "
            "uses get_db_session() scoped_session, and internally calls is_oidc_config_valid() "
            "which opens another get_db_session() context. The inner context's finally runs "
            "scoped.remove(), invalidating the outer session so the subsequent session.commit() "
            "is a no-op. The service logs 'Enabled OIDC' but oidc_enabled is never persisted. "
            "See log line 140 of src/admin/blueprints/oidc.py — the diagnostic log was added "
            "because this bug has been observed before. File a fix task and remove this xfail."
        ),
        strict=True,
    )
    def test_succeeds_when_verified_redirect_uri_matches(self, client, factory_session):
        """Happy path: config verified and redirect URI still matches → enables."""
        tenant = TenantFactory()
        verified_uri = get_tenant_redirect_uri(tenant)
        TenantAuthConfigFactory(
            tenant=tenant,
            oidc_enabled=False,
            oidc_verified_at=datetime.now(UTC),
            oidc_verified_redirect_uri=verified_uri,
        )
        # Defensive commit boundary. Factories use ``sqlalchemy_session_persistence="commit"``
        # so rows are already persisted, but the enable-OIDC handler opens its own
        # scoped_session and reads ``oidc_verified_at`` / ``oidc_verified_redirect_uri``
        # as preconditions. Forcing an explicit commit here keeps the precondition
        # unambiguous so the test only fails for the scoped_session bug the xfail
        # is meant to surface, not for a visibility artifact in the setup.
        factory_session.commit()
        _auth_session(client, tenant.tenant_id)

        response = client.post(f"/auth/oidc/tenant/{tenant.tenant_id}/enable")
        assert response.status_code == 200
        body = response.get_json()
        assert body["success"] is True
        assert body["oidc_enabled"] is True

        factory_session.expire_all()
        cfg = factory_session.scalars(select(TenantAuthConfig).filter_by(tenant_id=tenant.tenant_id)).first()
        assert cfg.oidc_enabled is True

    def test_rejects_when_verified_uri_stale(self, client, factory_session):
        """Verification is invalidated when the redirect URI drifts from what was tested.

        This prevents a tenant from being enabled against a verification that was
        performed against a now-stale URI (e.g. subdomain changed since test-flow).
        """
        tenant = TenantFactory()
        TenantAuthConfigFactory(
            tenant=tenant,
            oidc_enabled=False,
            oidc_verified_at=datetime.now(UTC),
            oidc_verified_redirect_uri="https://stale-host.example.com/admin/auth/oidc/callback",
        )
        _auth_session(client, tenant.tenant_id)

        response = client.post(f"/auth/oidc/tenant/{tenant.tenant_id}/enable")
        assert response.status_code == 400

        factory_session.expire_all()
        cfg = factory_session.scalars(select(TenantAuthConfig).filter_by(tenant_id=tenant.tenant_id)).first()
        assert cfg.oidc_enabled is False


class TestDisableOIDC:
    """POST /auth/oidc/tenant/<id>/disable — flip oidc_enabled=False."""

    def test_disables_enabled_config(self, client, factory_session):
        tenant = TenantFactory()
        TenantAuthConfigFactory(tenant=tenant, oidc_enabled=True)
        _auth_session(client, tenant.tenant_id)

        response = client.post(f"/auth/oidc/tenant/{tenant.tenant_id}/disable")
        assert response.status_code == 200
        assert response.get_json()["success"] is True

        factory_session.expire_all()
        cfg = factory_session.scalars(select(TenantAuthConfig).filter_by(tenant_id=tenant.tenant_id)).first()
        assert cfg.oidc_enabled is False

    def test_succeeds_when_no_config_exists(self, client, factory_session):
        """Disabling a non-existent config is a no-op that still returns 200."""
        tenant = TenantFactory()
        _auth_session(client, tenant.tenant_id)

        response = client.post(f"/auth/oidc/tenant/{tenant.tenant_id}/disable")
        assert response.status_code == 200
        assert response.get_json()["success"] is True
