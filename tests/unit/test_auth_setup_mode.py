"""Tests for auth setup mode functionality.

Auth setup mode allows test credentials to work per-tenant:
- New tenants start with auth_setup_mode=True (test credentials work)
- Admin configures SSO, tests it, then disables setup mode
- Once disabled, only SSO works
"""

# --- Test Source-of-Truth Audit ---
# Audited: 2026-03-18
#
# DECISION_BACKED (7/17 tests):
#   test_auth_setup_mode_defaults_to_true_in_schema — product decision: "New tenants start
#       with auth_setup_mode=True" (file module docstring)
#   test_disable_setup_mode_requires_sso_enabled    — product decision: setup mode can only
#       be disabled after SSO is configured (module docstring + endpoint logic comment)
#   test_disable_setup_mode_allowed_with_sso        — same
#   test_test_auth_allowed_when_both_enabled        — F-02 fix: BOTH env var AND
#       auth_setup_mode=True required; documented in auth.py "# Require BOTH"
#   test_test_auth_blocked_when_env_var_only        — F-02 regression: env var alone was the
#       vulnerable case; documented in auth.py comment
#   test_test_auth_blocked_when_setup_mode_only     — F-02 fix: auth_setup_mode alone must
#       not grant access
#   test_test_auth_blocked_when_both_disabled       — F-02 fix: neither condition → blocked
#   test_migration_file_exists                      — deployment dependency: migration must
#       exist for auth_setup_mode column to be present in production DB
#
# CHARACTERIZATION (3/17 tests):
#   test_tenant_has_auth_setup_mode_field           — locks: Tenant ORM model has this
#       attribute; no external spec defines internal model shape
#   test_auth_setup_mode_is_boolean                 — locks: column python_type is bool;
#       internal schema detail
#   test_migration_has_correct_revision             — locks: revision ID and down_revision
#       chain; internal migration structure
#
# SUSPECT (0 tests — all replaced by endpoint tests per issue #1149)
# ---

import os
from unittest.mock import MagicMock, patch

from src.core.database.models import Tenant


class TestTenantAuthSetupMode:
    """Tests for the auth_setup_mode field on Tenant model."""

    def test_tenant_has_auth_setup_mode_field(self):
        """Tenant model should have auth_setup_mode field."""
        tenant = Tenant(
            tenant_id="test_tenant",
            name="Test Tenant",
            subdomain="test",
        )
        assert hasattr(tenant, "auth_setup_mode")

    def test_auth_setup_mode_defaults_to_true_in_schema(self):
        """The auth_setup_mode column should have server_default='true'."""
        from sqlalchemy import inspect

        mapper = inspect(Tenant)
        column = mapper.columns["auth_setup_mode"]
        assert column.server_default is not None
        assert "true" in str(column.server_default.arg).lower()

    def test_auth_setup_mode_is_boolean(self):
        """auth_setup_mode should be a boolean field."""
        from sqlalchemy import inspect

        mapper = inspect(Tenant)
        column = mapper.columns["auth_setup_mode"]
        assert column.type.python_type is bool


class TestDisableSetupModeEndpoint:
    """Endpoint-level tests for POST /disable-setup-mode.

    Replaces SUSPECT MagicMock-only tests that reconstructed the endpoint
    conditional in the test body. Uses make_users_test_client to call the
    real route so a broken endpoint would actually fail.
    """

    def test_disable_setup_mode_rejects_when_not_sso_logged_in(self, make_users_test_client):
        """POST disable-setup-mode returns 403 when session auth_method is not 'oidc'."""
        with make_users_test_client(auth_setup_mode=True, oidc_enabled=True) as (client, _):
            response = client.post("/tenant/default/users/disable-setup-mode")
        assert response.status_code == 403
        body = response.get_json()
        assert body["success"] is False
        assert "logged in via SSO" in body["error"]

    def test_disable_setup_mode_rejects_when_sso_not_enabled(self, make_users_test_client):
        """POST disable-setup-mode returns 400 when tenant auth_config has oidc_enabled=False."""
        with make_users_test_client(auth_setup_mode=True, oidc_enabled=False) as (client, _):
            with client.session_transaction() as sess:
                sess["auth_method"] = "oidc"
            response = client.post("/tenant/default/users/disable-setup-mode")
        assert response.status_code == 400
        body = response.get_json()
        assert body["success"] is False
        assert "SSO must be configured" in body["error"]

    def test_disable_setup_mode_succeeds_when_sso_enabled(self, make_users_test_client):
        """POST disable-setup-mode returns 200 and sets auth_setup_mode=False when SSO is enabled."""
        with make_users_test_client(auth_setup_mode=True, oidc_enabled=True) as (client, mock_session):
            with client.session_transaction() as sess:
                sess["auth_method"] = "oidc"
            response = client.post("/tenant/default/users/disable-setup-mode")
        assert response.status_code == 200
        assert response.get_json()["success"] is True
        # The endpoint must have persisted the state change.
        mock_session.commit.assert_called()


class TestTestAuthEndpoint:
    """Endpoint-level tests for the /test/auth gate.

    F-02 fix: test auth now requires BOTH ADCP_AUTH_TEST_MODE=true AND
    the tenant's auth_setup_mode=True. These tests exercise the actual
    Flask endpoint so a gate change in auth.py would cause a real failure.
    """

    def test_test_auth_allowed_when_both_enabled(self, make_auth_test_client):
        """POST /test/auth returns 302 when env var and tenant setup mode are both on."""
        with make_auth_test_client(auth_setup_mode=True) as (client, _):
            with patch.dict(os.environ, {"ADCP_AUTH_TEST_MODE": "true", "PRODUCTION": "", "ENVIRONMENT": ""}):
                response = client.post(
                    "/test/auth",
                    data={"email": "test_super_admin@example.com", "password": "test123", "tenant_id": "default"},
                )

        assert response.status_code == 302

    def test_test_auth_blocked_when_env_var_only(self, make_auth_test_client):
        """POST /test/auth returns 404 when env var is set but tenant has disabled setup mode.

        F-02 regression: this was the vulnerable case before the fix.
        """
        with make_auth_test_client(auth_setup_mode=False) as (client, _):
            with patch.dict(os.environ, {"ADCP_AUTH_TEST_MODE": "true", "PRODUCTION": "", "ENVIRONMENT": ""}):
                response = client.post(
                    "/test/auth",
                    data={"email": "test_super_admin@example.com", "password": "test123", "tenant_id": "default"},
                )

        assert response.status_code == 404

    def test_test_auth_blocked_when_setup_mode_only(self, make_auth_test_client):
        """POST /test/auth returns 404 when tenant is in setup mode but env var is not set."""
        with make_auth_test_client(auth_setup_mode=True) as (client, _):
            with patch.dict(os.environ, {"ADCP_AUTH_TEST_MODE": "", "PRODUCTION": "", "ENVIRONMENT": ""}):
                response = client.post(
                    "/test/auth",
                    data={"email": "test_super_admin@example.com", "password": "test123", "tenant_id": "default"},
                )

        assert response.status_code == 404

    def test_test_auth_blocked_when_both_disabled(self, make_auth_test_client):
        """POST /test/auth returns 404 when both env var and tenant setup mode are off."""
        with make_auth_test_client(auth_setup_mode=False) as (client, _):
            with patch.dict(os.environ, {"ADCP_AUTH_TEST_MODE": "", "PRODUCTION": "", "ENVIRONMENT": ""}):
                response = client.post(
                    "/test/auth",
                    data={"email": "test_super_admin@example.com", "password": "test123", "tenant_id": "default"},
                )

        assert response.status_code == 404


class TestMigration:
    """Tests for the auth_setup_mode migration."""

    def test_migration_file_exists(self):
        """Migration file for auth_setup_mode should exist."""
        import os

        migration_path = "alembic/versions/add_auth_setup_mode.py"
        assert os.path.exists(migration_path), f"Migration file not found: {migration_path}"

    def test_migration_has_correct_revision(self):
        """Migration should have correct revision chain."""
        import importlib.util

        migration_path = "alembic/versions/add_auth_setup_mode.py"
        spec = importlib.util.spec_from_file_location("migration", migration_path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)

        # Check revision chain
        assert migration.revision == "add_auth_setup_mode"
        assert migration.down_revision == "add_tenant_auth_config"
        assert callable(migration.upgrade)
        assert callable(migration.downgrade)


class TestEnableSetupModeEndpoint:
    """Endpoint-level tests for POST /enable-setup-mode.

    Replaces the SUSPECT MagicMock-only test that was unable to detect
    a broken endpoint. Uses make_users_test_client to call the real route.
    """

    def test_enable_setup_mode_returns_success(self, make_users_test_client):
        """POST enable-setup-mode returns {"success": True} regardless of current state."""
        with make_users_test_client(auth_setup_mode=False) as (client, _):
            response = client.post("/tenant/default/users/enable-setup-mode")
        assert response.status_code == 200
        assert response.get_json()["success"] is True


class TestTenantLoginEndpoint:
    """Endpoint-level tests for GET /tenant/<id>/login respecting setup mode.

    Replaces three SUSPECT tests that copied the tenant_login() conditional
    expression into the test body. Each test calls the real Flask route and
    asserts on the rendered HTML so a regression in auth.py causes a real failure.

    The 'Setup Mode' banner (templates/login.html) is the HTML marker: it is
    rendered when test_mode=True, absent when test_mode=False.
    """

    def test_login_shows_test_banner_when_setup_mode_enabled(self, make_auth_test_client):
        """GET /login renders the Setup Mode banner when auth_setup_mode=True."""
        with make_auth_test_client(auth_setup_mode=True) as (client, _):
            with (
                patch("src.admin.blueprints.auth.get_oauth_config", return_value=("", "", "", "")),
                patch("src.services.auth_config_service.get_oidc_config_for_auth", return_value=None),
                patch.dict(os.environ, {"ADCP_AUTH_TEST_MODE": ""}),
            ):
                response = client.get("/tenant/default/login")
        assert response.status_code == 200
        assert b"Setup Mode" in response.data

    def test_login_env_var_enables_test_banner_regardless_of_setup_mode(self, make_auth_test_client):
        """GET /login renders the Setup Mode banner when ADCP_AUTH_TEST_MODE=true,
        even if the tenant has disabled auth_setup_mode."""
        with make_auth_test_client(auth_setup_mode=False) as (client, _):
            with (
                patch("src.admin.blueprints.auth.get_oauth_config", return_value=("", "", "", "")),
                patch("src.services.auth_config_service.get_oidc_config_for_auth", return_value=None),
                patch.dict(os.environ, {"ADCP_AUTH_TEST_MODE": "true"}),
            ):
                response = client.get("/tenant/default/login")
        assert response.status_code == 200
        assert b"Setup Mode" in response.data

    def test_login_hides_test_banner_when_setup_mode_disabled(self, make_auth_test_client):
        """GET /login omits the Setup Mode banner when auth_setup_mode=False and no env override."""
        with make_auth_test_client(auth_setup_mode=False) as (client, _):
            with (
                patch("src.admin.blueprints.auth.get_oauth_config", return_value=("", "", "", "")),
                patch("src.services.auth_config_service.get_oidc_config_for_auth", return_value=None),
                patch.dict(os.environ, {"ADCP_AUTH_TEST_MODE": ""}),
            ):
                response = client.get("/tenant/default/login")
        assert response.status_code == 200
        assert b"Setup Mode" not in response.data


class TestListUsersEndpoint:
    """Endpoint-level tests for GET /tenant/<id>/users respecting setup mode flags.

    Replaces two SUSPECT tests that built the template context dict manually.
    Each test calls the real Flask route and asserts on rendered HTML so a
    regression in users.py (e.g., wrong kwarg name passed to render_template)
    causes a real failure.
    """

    def test_list_users_renders_setup_mode_active_banner(self, make_users_test_client):
        """GET /users renders 'Setup Mode Active' when auth_setup_mode=True."""
        with make_users_test_client(auth_setup_mode=True, oidc_enabled=True) as (client, _):
            response = client.get("/tenant/default/users")
        assert response.status_code == 200
        assert b"Setup Mode Active" in response.data

    def test_list_users_renders_production_mode_when_no_auth_config(self, make_users_test_client):
        """GET /users renders 'Production Mode' when auth_setup_mode=False and auth_config absent."""
        with make_users_test_client(auth_setup_mode=False, auth_config_exists=False) as (client, _):
            response = client.get("/tenant/default/users")
        assert response.status_code == 200
        assert b"Production Mode" in response.data
