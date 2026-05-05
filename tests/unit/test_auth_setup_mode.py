"""Tests for auth setup mode functionality.

Auth setup mode allows test credentials to work per-tenant:
- New tenants start with auth_setup_mode=True (test credentials work)
- Admin configures SSO, tests it, then disables setup mode
- Once disabled, only SSO works
"""

import os
from unittest.mock import patch

from src.core.database.models import Tenant


class TestTenantAuthSetupMode:
    """Characterization tests for the auth_setup_mode field on the Tenant ORM model."""

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


class TestTestAuthEndpoint:
    """Endpoint-level tests for the /test/auth gate.

    F-02 fix: test auth requires BOTH ADCP_AUTH_TEST_MODE=true AND
    the tenant's auth_setup_mode=True. These tests exercise the actual
    Flask endpoint so a gate change in auth.py causes a real failure.

    (Previously removed in e1dbe47d and replaced with tests that
    re-implemented the endpoint conditional inline — restored here.)
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
        migration_path = "alembic/versions/add_auth_setup_mode.py"
        assert os.path.exists(migration_path), f"Migration file not found: {migration_path}"

    def test_migration_has_correct_revision(self):
        """Migration should have correct revision chain."""
        import importlib.util

        migration_path = "alembic/versions/add_auth_setup_mode.py"
        spec = importlib.util.spec_from_file_location("migration", migration_path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)

        assert migration.revision == "add_auth_setup_mode"
        assert migration.down_revision == "add_tenant_auth_config"
        assert callable(migration.upgrade)
        assert callable(migration.downgrade)
