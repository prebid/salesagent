"""Integration tests for GAM adapter authentication — OAuth and service account paths.

Verifies that get_adapter() correctly passes credentials from AdapterConfig (DB)
through to GoogleAdManager for both authentication methods.

This is a regression harness for #1163: get_adapter() drops service_account_json,
breaking all MCP/A2A tool operations for service-account tenants.

Test approach:
- Real PostgreSQL (integration_db fixture)
- Real AdapterConfig rows with both auth methods
- Mock the googleads client (no real GAM network in CI)
- Do NOT mock the config construction path — that's what we're testing
"""

import json
import os
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import delete, select

from src.adapters.gam import build_gam_config_from_adapter
from src.core.database.database_session import get_db_session
from src.core.database.models import AdapterConfig
from src.core.database.models import Principal as ModelPrincipal
from src.core.database.models import Tenant as ModelTenant
from src.core.helpers import get_adapter
from src.core.schemas import Principal

# Test encryption key (only for tests — Fernet requires a valid key)
_TEST_ENCRYPTION_KEY = Fernet.generate_key().decode()

# Minimal valid service account JSON (not a real key)
_TEST_SA_JSON = json.dumps(
    {
        "type": "service_account",
        "project_id": "test-project",
        "private_key_id": "key123",
        "private_key": "-----BEGIN PRIVATE KEY-----\nMIIEvgIBADANBg==\n-----END PRIVATE KEY-----\n",
        "client_email": "test@test-project.iam.gserviceaccount.com",
        "client_id": "123456789",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
)


@pytest.fixture
def _encryption_key():
    """Ensure ENCRYPTION_KEY is set for AdapterConfig SA JSON encryption."""
    with patch.dict(os.environ, {"ENCRYPTION_KEY": _TEST_ENCRYPTION_KEY}):
        yield


@pytest.fixture
def oauth_tenant(integration_db, _encryption_key):
    """Create a tenant with OAuth (refresh_token) GAM auth."""
    from tests.utils.database_helpers import (
        create_principal_with_platform_mappings,
        create_tenant_with_timestamps,
    )

    tenant_id = "test_gam_auth_oauth"
    with get_db_session() as session:
        tenant = create_tenant_with_timestamps(
            tenant_id=tenant_id,
            name="OAuth GAM Tenant",
            subdomain="oauth-gam",
            ad_server="google_ad_manager",
            is_active=True,
        )
        session.add(tenant)

        config = AdapterConfig(
            tenant_id=tenant_id,
            adapter_type="google_ad_manager",
            gam_network_code="123456789",
            gam_refresh_token="test_oauth_refresh_token",
            gam_auth_method="oauth",
            gam_trafficker_id="999",
        )
        session.add(config)

        principal = create_principal_with_platform_mappings(
            tenant_id=tenant_id,
            principal_id="oauth_principal",
            name="OAuth Principal",
            access_token="oauth_token",
            platform_mappings={"google_ad_manager": {"advertiser_id": "12345"}},
        )
        session.add(principal)
        session.commit()

        yield {
            "tenant_id": tenant_id,
            "principal_id": "oauth_principal",
            "config": config,
        }

        # Cleanup
        session.execute(delete(ModelPrincipal).where(ModelPrincipal.tenant_id == tenant_id))
        session.execute(delete(AdapterConfig).where(AdapterConfig.tenant_id == tenant_id))
        session.execute(delete(ModelTenant).where(ModelTenant.tenant_id == tenant_id))
        session.commit()


@pytest.fixture
def sa_tenant(integration_db, _encryption_key):
    """Create a tenant with service account GAM auth."""
    from tests.utils.database_helpers import (
        create_principal_with_platform_mappings,
        create_tenant_with_timestamps,
    )

    tenant_id = "test_gam_auth_sa"
    with get_db_session() as session:
        tenant = create_tenant_with_timestamps(
            tenant_id=tenant_id,
            name="SA GAM Tenant",
            subdomain="sa-gam",
            ad_server="google_ad_manager",
            is_active=True,
        )
        session.add(tenant)

        config = AdapterConfig(
            tenant_id=tenant_id,
            adapter_type="google_ad_manager",
            gam_network_code="987654321",
            gam_refresh_token=None,  # No OAuth token — SA auth only
            gam_auth_method="service_account",
            gam_trafficker_id="888",
        )
        # Set service account JSON via property (handles encryption)
        config.gam_service_account_json = _TEST_SA_JSON
        session.add(config)

        principal = create_principal_with_platform_mappings(
            tenant_id=tenant_id,
            principal_id="sa_principal",
            name="SA Principal",
            access_token="sa_token",
            platform_mappings={"google_ad_manager": {"advertiser_id": "67890"}},
        )
        session.add(principal)
        session.commit()

        yield {
            "tenant_id": tenant_id,
            "principal_id": "sa_principal",
            "config": config,
        }

        # Cleanup
        session.execute(delete(ModelPrincipal).where(ModelPrincipal.tenant_id == tenant_id))
        session.execute(delete(AdapterConfig).where(AdapterConfig.tenant_id == tenant_id))
        session.execute(delete(ModelTenant).where(ModelTenant.tenant_id == tenant_id))
        session.commit()


def _load_principal(tenant_id: str, principal_id: str) -> Principal:
    """Load principal from DB and convert to schema object."""
    with get_db_session() as session:
        db_principal = session.scalars(
            select(ModelPrincipal).filter_by(tenant_id=tenant_id, principal_id=principal_id)
        ).first()
        return Principal(
            principal_id=db_principal.principal_id,
            name=db_principal.name,
            platform_mappings=db_principal.platform_mappings or {},
        )


def _set_tenant_context(tenant_id: str):
    """Set the current tenant context for get_adapter()."""
    from src.core.config_loader import set_current_tenant

    with get_db_session() as session:
        db_tenant = session.scalars(select(ModelTenant).filter_by(tenant_id=tenant_id)).first()
        set_current_tenant(
            {
                "tenant_id": db_tenant.tenant_id,
                "name": db_tenant.name,
                "subdomain": db_tenant.subdomain,
                "ad_server": db_tenant.ad_server,
                "is_active": db_tenant.is_active,
            }
        )


@pytest.mark.integration
@pytest.mark.requires_db
class TestBuildGamConfigFromAdapter:
    """Test the reference implementation — build_gam_config_from_adapter().

    This function correctly handles both auth methods. These tests verify
    it stays correct as a baseline for the consolidation work.
    """

    def test_oauth_config_includes_refresh_token(self, oauth_tenant, _encryption_key):
        """OAuth tenant config must include refresh_token."""
        with get_db_session() as session:
            config_row = session.scalars(select(AdapterConfig).filter_by(tenant_id=oauth_tenant["tenant_id"])).first()
            config = build_gam_config_from_adapter(config_row)

        assert config["refresh_token"] == "test_oauth_refresh_token"
        assert "service_account_json" not in config
        assert config["network_code"] == "123456789"

    def test_sa_config_includes_service_account_json(self, sa_tenant, _encryption_key):
        """Service account tenant config must include service_account_json."""
        with get_db_session() as session:
            config_row = session.scalars(select(AdapterConfig).filter_by(tenant_id=sa_tenant["tenant_id"])).first()
            config = build_gam_config_from_adapter(config_row)

        assert "service_account_json" in config
        parsed = json.loads(config["service_account_json"])
        assert parsed["type"] == "service_account"
        assert "refresh_token" not in config
        assert config["network_code"] == "987654321"


@pytest.mark.integration
@pytest.mark.requires_db
class TestGetAdapterGAMAuth:
    """Test get_adapter() — the factory used by all MCP/A2A tool operations.

    This is where the bug lives: get_adapter() only sets refresh_token,
    so service account tenants fail with ValueError.
    """

    def test_oauth_tenant_creates_adapter_successfully(self, oauth_tenant):
        """get_adapter() with OAuth tenant must return a GoogleAdManager."""
        from src.adapters.google_ad_manager import GoogleAdManager

        _set_tenant_context(oauth_tenant["tenant_id"])
        principal = _load_principal(oauth_tenant["tenant_id"], oauth_tenant["principal_id"])

        adapter = get_adapter(principal, dry_run=True)

        assert isinstance(adapter, GoogleAdManager)
        assert adapter.refresh_token == "test_oauth_refresh_token"
        assert adapter.dry_run is True

    def test_sa_tenant_creates_adapter_successfully(self, sa_tenant, _encryption_key):
        """get_adapter() with service account tenant must return a GoogleAdManager.

        BUG (#1163): This currently FAILS because get_adapter() never includes
        service_account_json in the config dict. The GoogleAdManager constructor
        sees refresh_token="" and service_account_json=None, so all three auth
        fields are falsy and it raises ValueError.

        After the fix, this test should pass.
        """
        from src.adapters.google_ad_manager import GoogleAdManager

        _set_tenant_context(sa_tenant["tenant_id"])
        principal = _load_principal(sa_tenant["tenant_id"], sa_tenant["principal_id"])

        # This should NOT raise — but currently does (#1163)
        adapter = get_adapter(principal, dry_run=True)

        assert isinstance(adapter, GoogleAdManager)
        assert adapter.service_account_json is not None
        assert adapter.dry_run is True

    def test_sa_tenant_config_dict_has_correct_keys(self, sa_tenant, _encryption_key):
        """The config dict built by build_gam_config_from_adapter must contain service_account_json.

        Verifies that the centralized config builder (now used by get_adapter)
        produces a config dict with valid auth credentials for SA tenants.
        """
        with get_db_session() as session:
            config_row = session.scalars(select(AdapterConfig).filter_by(tenant_id=sa_tenant["tenant_id"])).first()
            adapter_config = build_gam_config_from_adapter(config_row)

        assert "service_account_json" in adapter_config, "SA tenant config must include service_account_json"
        assert "refresh_token" not in adapter_config, "SA tenant config must not include refresh_token"
        assert adapter_config["network_code"] == "987654321"
