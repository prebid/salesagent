"""Integration tests for admin API authentication against a real PostgreSQL database.

Brian's unit tests (test_admin_auth.py) mock get_db_session — they prove the
control flow is correct but cannot catch bugs where the DB schema, ORM mapping,
or hmac comparison fails against real data. These tests don't use
dependency_overrides: they exercise the full Depends() chain.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from src.app import app
from src.core.database.database_session import get_db_session
from src.core.database.models import Tenant, TenantManagementConfig

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

ADMIN_TOKEN = "test-admin-token-for-auth-integration"
PLATFORM_KEY = "sk-test-platform-key-for-auth-integration"


@pytest.fixture(scope="module")
def client():
    """Shared TestClient for the full ASGI app.

    Module-scoped to avoid the StreamableHTTPSessionManager 'can only be called
    once per instance' error — the MCP lifespan cannot restart within a process.
    """
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
def tenant_with_token(integration_db):
    """Create a real Tenant row with a known admin_token."""
    from datetime import UTC, datetime

    tenant_id = "auth-test-tenant"
    with get_db_session() as session:
        tenant = Tenant(
            tenant_id=tenant_id,
            name="Auth Test Tenant",
            subdomain="auth-test",
            ad_server="mock",
            admin_token=ADMIN_TOKEN,
            is_active=True,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        session.add(tenant)
        session.commit()

    yield tenant_id


@pytest.fixture
def platform_api_key(integration_db):
    """Insert a real TenantManagementConfig row with a known platform API key."""
    from datetime import UTC, datetime

    with get_db_session() as session:
        config = TenantManagementConfig(
            config_key="tenant_management_api_key",
            config_value=PLATFORM_KEY,
            description="Integration test platform key",
            updated_at=datetime.now(UTC),
            updated_by="pytest",
        )
        session.add(config)
        session.commit()

    yield PLATFORM_KEY


@pytest.fixture
def platform_api_key_legacy_name(integration_db):
    """Insert TenantManagementConfig using legacy 'api_key' config_key name."""
    from datetime import UTC, datetime

    with get_db_session() as session:
        config = TenantManagementConfig(
            config_key="api_key",
            config_value=PLATFORM_KEY,
            description="Integration test platform key (legacy name)",
            updated_at=datetime.now(UTC),
            updated_by="pytest",
        )
        session.add(config)
        session.commit()

    yield PLATFORM_KEY


@pytest.fixture
def second_tenant(integration_db):
    """Create a second tenant to test cross-tenant rejection."""
    from datetime import UTC, datetime

    tenant_id = "auth-test-other-tenant"
    with get_db_session() as session:
        tenant = Tenant(
            tenant_id=tenant_id,
            name="Other Auth Test Tenant",
            subdomain="auth-test-other",
            ad_server="mock",
            admin_token="other-tenant-admin-token-xyz",
            is_active=True,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        session.add(tenant)
        session.commit()

    yield tenant_id


# ---------------------------------------------------------------------------
# Platform API Key Auth
# ---------------------------------------------------------------------------


class TestRequirePlatformApiKeyIntegration:
    """Integration tests for require_platform_api_key against real DB."""

    def test_missing_header_returns_401(self, client, integration_db):
        response = client.get("/api/v1/platform/tenants")
        assert response.status_code == 401

    def test_wrong_key_not_in_db_returns_401(self, client, platform_api_key):
        response = client.get(
            "/api/v1/platform/tenants",
            headers={"X-Tenant-Management-API-Key": "sk-totally-wrong-key"},
        )
        assert response.status_code == 401

    def test_valid_key_stored_as_tenant_management_api_key_returns_200(self, client, platform_api_key):
        response = client.get(
            "/api/v1/platform/tenants",
            headers={"X-Tenant-Management-API-Key": platform_api_key},
        )
        assert response.status_code == 200

    def test_valid_key_stored_under_legacy_api_key_name_returns_200(self, client, platform_api_key_legacy_name):
        """Backwards compat: key stored as config_key='api_key' should still authenticate."""
        response = client.get(
            "/api/v1/platform/tenants",
            headers={"X-Tenant-Management-API-Key": platform_api_key_legacy_name},
        )
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Tenant Admin Token Auth
# ---------------------------------------------------------------------------


class TestRequireTenantAdminIntegration:
    """Integration tests for require_tenant_admin against real DB."""

    def test_missing_bearer_header_returns_401(self, client, tenant_with_token):
        response = client.get(f"/api/v1/admin/{tenant_with_token}/properties")
        assert response.status_code == 401

    def test_wrong_token_returns_403(self, client, tenant_with_token):
        response = client.get(
            f"/api/v1/admin/{tenant_with_token}/properties",
            headers={"Authorization": "Bearer wrong-token-entirely"},
        )
        assert response.status_code == 403

    def test_nonexistent_tenant_returns_404(self, client, integration_db):
        response = client.get(
            "/api/v1/admin/tenant-does-not-exist/properties",
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        )
        assert response.status_code == 404

    def test_valid_token_returns_200(self, client, tenant_with_token):
        response = client.get(
            f"/api/v1/admin/{tenant_with_token}/properties",
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        )
        assert response.status_code == 200

    def test_valid_token_for_wrong_tenant_returns_403(self, client, tenant_with_token, second_tenant):
        """Token valid for tenant_A must be rejected when used with tenant_B's URL."""
        response = client.get(
            f"/api/v1/admin/{second_tenant}/properties",
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        )
        assert response.status_code == 403
