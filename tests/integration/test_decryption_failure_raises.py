"""Regression test for H1: decryption failure must raise, not return None.

Three encrypted property getters (gemini_api_key, gam_service_account_json,
oidc_client_secret) previously caught ValueError and returned None. Callers
interpreted None as "not configured" when it meant "configuration is broken."

GH #1078 H1.
"""

import pytest

from src.core.database.models import AdapterConfig, TenantAuthConfig
from src.core.exceptions import AdCPConfigurationError
from tests.factories import TenantFactory
from tests.harness._base import IntegrationEnv

pytestmark = [pytest.mark.requires_db, pytest.mark.integration]


class _BareEnv(IntegrationEnv):
    """Minimal integration env — just session + factory binding."""

    EXTERNAL_PATCHES = {}


class TestDecryptionFailureRaises:
    """Encrypted property getters must raise AdCPConfigurationError on failure."""

    def test_tenant_gemini_key_raises_on_corrupt_data(self, integration_db):
        """Tenant.gemini_api_key raises AdCPConfigurationError for corrupt ciphertext."""
        with _BareEnv() as env:
            tenant = TenantFactory(tenant_id="t-decrypt")

            # Write corrupt encrypted data directly
            tenant._gemini_api_key = "not-valid-fernet-token"
            env._session.commit()

            with pytest.raises(AdCPConfigurationError, match="decrypt"):
                _ = tenant.gemini_api_key

    def test_adapter_config_gam_json_raises_on_corrupt_data(self, integration_db):
        """AdapterConfig.gam_service_account_json raises on corrupt ciphertext."""
        with _BareEnv() as env:
            tenant = TenantFactory(tenant_id="t-decrypt2")
            config = AdapterConfig(
                tenant_id=tenant.tenant_id,
                adapter_type="google_ad_manager",
                _gam_service_account_json="not-valid-fernet-token",
            )
            env._session.add(config)
            env._session.commit()

            with pytest.raises(AdCPConfigurationError, match="decrypt"):
                _ = config.gam_service_account_json

    def test_oidc_secret_raises_on_corrupt_data(self, integration_db):
        """TenantAuthConfig.oidc_client_secret raises on corrupt ciphertext."""
        with _BareEnv() as env:
            tenant = TenantFactory(tenant_id="t-decrypt3")
            auth_config = TenantAuthConfig(
                tenant_id=tenant.tenant_id,
                oidc_enabled=True,
                oidc_provider="google",
                oidc_client_id="test-client-id",
                oidc_client_secret_encrypted="not-valid-fernet-token",
            )
            env._session.add(auth_config)
            env._session.commit()

            with pytest.raises(AdCPConfigurationError, match="decrypt"):
                _ = auth_config.oidc_client_secret
