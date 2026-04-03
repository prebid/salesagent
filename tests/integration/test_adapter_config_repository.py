"""Integration tests for AdapterConfigRepository.

Verifies tenant-scoped access to adapter configuration for both OAuth
and service account GAM authentication methods.

Part of the AdapterConfigRepository introduction (salesagent-zj9 epic).
Redesigned in salesagent-9buv: fail-loud get_by_tenant, pure logic methods.
"""

import json
import os
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import delete

from src.core.database.database_session import get_db_session
from src.core.database.models import AdapterConfig
from src.core.database.models import Tenant as ModelTenant
from src.core.database.repositories.adapter_config import (
    AdapterConfigRepository,
    TenantNotConfiguredError,
)

# Test encryption key
_TEST_ENCRYPTION_KEY = Fernet.generate_key().decode()

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
    with patch.dict(os.environ, {"ENCRYPTION_KEY": _TEST_ENCRYPTION_KEY}):
        yield


@pytest.fixture
def _tenants(integration_db, _encryption_key):
    """Create OAuth, SA, and unconfigured tenants with AdapterConfig rows."""
    from tests.utils.database_helpers import create_tenant_with_timestamps

    with get_db_session() as session:
        # OAuth tenant
        session.add(
            create_tenant_with_timestamps(
                tenant_id="repo_test_oauth",
                name="OAuth",
                subdomain="oauth",
                ad_server="google_ad_manager",
                is_active=True,
            )
        )
        oauth_config = AdapterConfig(
            tenant_id="repo_test_oauth",
            adapter_type="google_ad_manager",
            gam_network_code="111222333",
            gam_refresh_token="test_refresh",
            gam_auth_method="oauth",
            gam_trafficker_id="100",
            gam_manual_approval_required=True,
            gam_order_name_template="Order: {buyer}",
            gam_line_item_name_template="LI: {product}",
        )
        oauth_config.axe_include_key = "hb_pb"
        oauth_config.axe_exclude_key = "hb_exclude"
        oauth_config.custom_targeting_keys = {"hb_pb": "123", "hb_source": "456"}
        session.add(oauth_config)

        # Service account tenant
        session.add(
            create_tenant_with_timestamps(
                tenant_id="repo_test_sa", name="SA", subdomain="sa", ad_server="google_ad_manager", is_active=True
            )
        )
        sa_config = AdapterConfig(
            tenant_id="repo_test_sa",
            adapter_type="google_ad_manager",
            gam_network_code="444555666",
            gam_refresh_token=None,
            gam_auth_method="service_account",
            gam_trafficker_id="200",
        )
        sa_config.gam_service_account_json = _TEST_SA_JSON
        session.add(sa_config)

        # Mock adapter tenant (not GAM)
        session.add(
            create_tenant_with_timestamps(
                tenant_id="repo_test_mock", name="Mock", subdomain="mock", ad_server="mock", is_active=True
            )
        )
        session.add(
            AdapterConfig(
                tenant_id="repo_test_mock",
                adapter_type="mock",
                mock_dry_run=True,
            )
        )

        # Unconfigured tenant (no AdapterConfig row)
        session.add(
            create_tenant_with_timestamps(
                tenant_id="repo_test_none", name="None", subdomain="none", ad_server=None, is_active=True
            )
        )

        session.commit()
        yield

        # Cleanup
        for tid in ["repo_test_oauth", "repo_test_sa", "repo_test_mock", "repo_test_none"]:
            session.execute(delete(AdapterConfig).where(AdapterConfig.tenant_id == tid))
            session.execute(delete(ModelTenant).where(ModelTenant.tenant_id == tid))
        session.commit()


@pytest.mark.integration
@pytest.mark.requires_db
class TestAdapterConfigRepositoryRead:
    """Test query methods of AdapterConfigRepository."""

    def test_get_by_tenant_returns_config(self, _tenants, _encryption_key):
        with get_db_session() as session:
            repo = AdapterConfigRepository(session, "repo_test_oauth")
            config = repo.get_by_tenant()
        assert config is not None
        assert config.adapter_type == "google_ad_manager"
        assert config.gam_network_code == "111222333"

    def test_get_by_tenant_raises_for_unconfigured(self, _tenants):
        """get_by_tenant raises TenantNotConfiguredError when row missing."""
        with get_db_session() as session:
            repo = AdapterConfigRepository(session, "repo_test_none")
            with pytest.raises(TenantNotConfiguredError, match="repo_test_none"):
                repo.get_by_tenant()

    def test_find_by_tenant_returns_config(self, _tenants, _encryption_key):
        with get_db_session() as session:
            repo = AdapterConfigRepository(session, "repo_test_oauth")
            config = repo.find_by_tenant()
        assert config is not None
        assert config.adapter_type == "google_ad_manager"

    def test_find_by_tenant_returns_none_for_unconfigured(self, _tenants):
        """find_by_tenant returns None when row missing (normal-absence case)."""
        with get_db_session() as session:
            repo = AdapterConfigRepository(session, "repo_test_none")
            config = repo.find_by_tenant()
        assert config is None

    def test_get_adapter_type_oauth(self, _tenants):
        with get_db_session() as session:
            repo = AdapterConfigRepository(session, "repo_test_oauth")
            assert repo.get_adapter_type() == "google_ad_manager"

    def test_get_adapter_type_unconfigured(self, _tenants):
        with get_db_session() as session:
            repo = AdapterConfigRepository(session, "repo_test_none")
            assert repo.get_adapter_type() is None


@pytest.mark.integration
@pytest.mark.requires_db
class TestAdapterConfigRepositoryLogicMethods:
    """Test pure logic methods accept pre-loaded config (no DB)."""

    def test_has_gam_credentials_oauth(self, _tenants):
        with get_db_session() as session:
            repo = AdapterConfigRepository(session, "repo_test_oauth")
            config = repo.find_by_tenant()
            assert config is not None
            assert repo.has_gam_credentials(config) is True

    def test_has_gam_credentials_service_account(self, _tenants, _encryption_key):
        with get_db_session() as session:
            repo = AdapterConfigRepository(session, "repo_test_sa")
            config = repo.find_by_tenant()
            assert config is not None
            assert repo.has_gam_credentials(config) is True

    def test_has_gam_credentials_false_for_mock(self, _tenants):
        with get_db_session() as session:
            repo = AdapterConfigRepository(session, "repo_test_mock")
            config = repo.find_by_tenant()
            assert config is not None
            assert repo.has_gam_credentials(config) is False

    def test_get_gam_config_oauth(self, _tenants, _encryption_key):
        with get_db_session() as session:
            repo = AdapterConfigRepository(session, "repo_test_oauth")
            config = repo.get_by_tenant()
            gam_config = repo.get_gam_config(config)
        assert gam_config["refresh_token"] == "test_refresh"
        assert "service_account_json" not in gam_config
        assert gam_config["network_code"] == "111222333"
        assert gam_config["manual_approval_required"] is True

    def test_get_gam_config_service_account(self, _tenants, _encryption_key):
        with get_db_session() as session:
            repo = AdapterConfigRepository(session, "repo_test_sa")
            config = repo.get_by_tenant()
            gam_config = repo.get_gam_config(config)
        assert "service_account_json" in gam_config
        parsed = json.loads(gam_config["service_account_json"])
        assert parsed["type"] == "service_account"
        assert "refresh_token" not in gam_config
        assert gam_config["network_code"] == "444555666"

    def test_get_gam_config_raises_for_non_gam(self, _tenants):
        with get_db_session() as session:
            repo = AdapterConfigRepository(session, "repo_test_mock")
            config = repo.get_by_tenant()
            with pytest.raises(ValueError, match="not a GAM adapter"):
                repo.get_gam_config(config)

    def test_get_gam_targeting_config(self, _tenants):
        with get_db_session() as session:
            repo = AdapterConfigRepository(session, "repo_test_oauth")
            config = repo.get_by_tenant()
            targeting = repo.get_gam_targeting_config(config)
        assert targeting["axe_include_key"] == "hb_pb"
        assert targeting["axe_exclude_key"] == "hb_exclude"
        assert targeting["custom_targeting_keys"]["hb_pb"] == "123"

    def test_get_gam_naming_templates(self, _tenants):
        with get_db_session() as session:
            repo = AdapterConfigRepository(session, "repo_test_oauth")
            config = repo.get_by_tenant()
            order_tmpl, li_tmpl = repo.get_gam_naming_templates(config)
        assert order_tmpl == "Order: {buyer}"
        assert li_tmpl == "LI: {product}"

    def test_get_gam_naming_templates_none_when_unset(self, _tenants, _encryption_key):
        with get_db_session() as session:
            repo = AdapterConfigRepository(session, "repo_test_sa")
            config = repo.get_by_tenant()
            order_tmpl, li_tmpl = repo.get_gam_naming_templates(config)
        assert order_tmpl is None
        assert li_tmpl is None


@pytest.mark.integration
@pytest.mark.requires_db
class TestAdapterConfigRepositoryWrite:
    """Test write methods raise on missing config."""

    def test_update_custom_targeting_keys_raises_when_missing(self, _tenants):
        """update_custom_targeting_keys raises TenantNotConfiguredError when row missing."""
        with get_db_session() as session:
            repo = AdapterConfigRepository(session, "repo_test_none")
            with pytest.raises(TenantNotConfiguredError, match="repo_test_none"):
                repo.update_custom_targeting_keys({"key": "value"})
