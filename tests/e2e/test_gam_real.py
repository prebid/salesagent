"""
Real GAM E2E tests that verify actual Google Ad Manager API connectivity.

These tests require valid GAM credentials and connect to a real GAM test network.
They are gated behind @pytest.mark.requires_gam and skip when credentials are absent.

Test network: 23341594478 (XFP sandbox property)
Service account: salesagent-e2e@salesagenttest.iam.gserviceaccount.com

Run with:
    uv run pytest tests/e2e/test_gam_real.py -v
"""

from unittest.mock import MagicMock

import pytest

from tests.e2e.conftest import (
    GAM_TEST_AD_UNIT_IDS,
    GAM_TEST_ADVERTISER_ID,
    GAM_TEST_NETWORK_CODE,
)


@pytest.mark.requires_gam
class TestGAMConnection:
    """Verify basic GAM API connectivity and authentication."""

    def test_network_connection(self, gam_client_manager):
        """Service account can authenticate and retrieve network info."""
        from src.adapters.gam.utils.constants import GAM_API_VERSION

        client = gam_client_manager.get_client()
        network_service = client.GetService("NetworkService", version=GAM_API_VERSION)
        network = network_service.getCurrentNetwork()

        assert str(network["networkCode"]) == GAM_TEST_NETWORK_CODE
        assert network["displayName"] is not None

    def test_is_connected(self, gam_client_manager):
        """is_connected() returns True for valid credentials."""
        assert gam_client_manager.is_connected() is True

    def test_current_user(self, gam_client_manager):
        """Service account can query current user info."""
        from src.adapters.gam.utils.constants import GAM_API_VERSION

        client = gam_client_manager.get_client()
        user_service = client.GetService("UserService", version=GAM_API_VERSION)
        current_user = user_service.getCurrentUser()

        assert current_user is not None
        assert hasattr(current_user, "id")
        assert hasattr(current_user, "email")


@pytest.mark.requires_gam
class TestGAMInventory:
    """Verify GAM inventory (ad units) discovery works."""

    def test_list_ad_units(self, gam_client_manager):
        """Can list ad units from the test network."""
        from googleads import ad_manager

        from src.adapters.gam.utils.constants import GAM_API_VERSION

        inv_service = gam_client_manager.get_service("InventoryService")
        sb = ad_manager.StatementBuilder(version=GAM_API_VERSION)
        sb.limit = 100

        result = inv_service.getAdUnitsByStatement(sb.ToStatement())

        assert "results" in result
        assert len(result["results"]) > 0

        # Verify our test ad units exist
        ad_unit_ids = [str(au["id"]) for au in result["results"]]
        for expected_id in GAM_TEST_AD_UNIT_IDS:
            assert expected_id in ad_unit_ids, f"Test ad unit {expected_id} not found in network"

    def test_ad_unit_has_expected_structure(self, gam_client_manager):
        """Ad units have the fields the adapter expects."""
        from googleads import ad_manager

        from src.adapters.gam.utils.constants import GAM_API_VERSION

        inv_service = gam_client_manager.get_service("InventoryService")
        sb = ad_manager.StatementBuilder(version=GAM_API_VERSION)
        sb.Where("id = :id").WithBindVariable("id", int(GAM_TEST_AD_UNIT_IDS[0]))

        result = inv_service.getAdUnitsByStatement(sb.ToStatement())

        assert "results" in result
        au = result["results"][0]

        # Verify essential fields exist
        assert "id" in au
        assert "name" in au
        assert "adUnitCode" in au
        assert "status" in au


@pytest.mark.requires_gam
class TestGAMAdvertiser:
    """Verify GAM advertiser (company) operations work."""

    def test_list_advertisers(self, gam_client_manager):
        """Can list advertisers from the test network."""
        from googleads import ad_manager

        from src.adapters.gam.utils.constants import GAM_API_VERSION

        company_service = gam_client_manager.get_service("CompanyService")
        sb = ad_manager.StatementBuilder(version=GAM_API_VERSION)
        sb.Where("type = :type").WithBindVariable("type", "ADVERTISER")
        sb.limit = 100

        result = company_service.getCompaniesByStatement(sb.ToStatement())

        assert "results" in result
        assert len(result["results"]) > 0

        # Verify our test advertiser exists
        advertiser_ids = [str(co["id"]) for co in result["results"]]
        assert GAM_TEST_ADVERTISER_ID in advertiser_ids, "Test advertiser not found in network"

    def test_advertiser_has_expected_structure(self, gam_client_manager):
        """Advertisers have the fields the adapter expects."""
        from googleads import ad_manager

        from src.adapters.gam.utils.constants import GAM_API_VERSION

        company_service = gam_client_manager.get_service("CompanyService")
        sb = ad_manager.StatementBuilder(version=GAM_API_VERSION)
        sb.Where("id = :id").WithBindVariable("id", int(GAM_TEST_ADVERTISER_ID))

        result = company_service.getCompaniesByStatement(sb.ToStatement())

        assert "results" in result
        co = result["results"][0]

        assert co["id"] == int(GAM_TEST_ADVERTISER_ID)
        assert co["name"] == "E2E Test Advertiser"
        assert co["type"] == "ADVERTISER"


@pytest.mark.requires_gam
class TestGAMAdapter:
    """Test the GAM adapter wrapper with real API calls."""

    def _make_adapter(self, gam_service_account_json):
        """Create a GoogleAdManager adapter with test credentials."""
        from src.adapters.google_ad_manager import GoogleAdManager

        config = {
            "service_account_json": gam_service_account_json,
            "network_code": GAM_TEST_NETWORK_CODE,
            "trafficker_id": None,
        }

        # Principal is required but only used for advertiser mapping
        principal = MagicMock()
        principal.tenant_id = "e2e_test"
        principal.principal_id = "e2e_test_principal"
        principal.platform_mappings = {"gam_advertiser_id": GAM_TEST_ADVERTISER_ID}

        return GoogleAdManager(
            config=config,
            principal=principal,
            network_code=GAM_TEST_NETWORK_CODE,
            advertiser_id=GAM_TEST_ADVERTISER_ID,
            tenant_id="e2e_test",
        )

    def test_adapter_initialization(self, gam_service_account_json):
        """GoogleAdManager adapter initializes with service account credentials."""
        adapter = self._make_adapter(gam_service_account_json)

        assert adapter.client_manager is not None
        assert adapter.client_manager.is_connected()

    def test_adapter_get_advertisers(self, gam_service_account_json):
        """Adapter can fetch advertisers via its own API."""
        adapter = self._make_adapter(gam_service_account_json)

        companies = adapter.get_advertisers()
        assert len(companies) > 0

        # Find our test advertiser
        test_advertiser = None
        for co in companies:
            co_id = str(co.get("id", co.get("companyId", "")))
            if co_id == GAM_TEST_ADVERTISER_ID:
                test_advertiser = co
                break

        assert test_advertiser is not None, "Test advertiser not found via adapter.get_advertisers()"


@pytest.mark.requires_gam
class TestGAMHealthCheck:
    """Test GAM health check via client manager (not GAMHealthChecker directly).

    Note: GAMHealthChecker._init_client() only supports service_account_key_file,
    not service_account_json. Using client_manager.test_connection() instead.
    See salesagent-xxxx for the health checker bug.
    """

    def test_health_check_via_client_manager(self, gam_client_manager):
        """Client manager's test_connection verifies auth works."""
        from src.adapters.gam.utils.health_check import HealthStatus

        result = gam_client_manager.test_connection()

        assert result.status == HealthStatus.HEALTHY, f"Health check failed: {result.message}"

    def test_full_health_check(self, gam_client_manager):
        """Full health check returns healthy status."""
        from src.adapters.gam.utils.health_check import HealthStatus

        overall_status, results = gam_client_manager.check_health(
            advertiser_id=GAM_TEST_ADVERTISER_ID,
            ad_unit_ids=GAM_TEST_AD_UNIT_IDS,
        )

        # At minimum, the overall status should not be UNHEALTHY
        assert overall_status != HealthStatus.UNHEALTHY, "Health check returned UNHEALTHY. Results: " + "; ".join(
            f"{r.check_name}: {r.status.value} - {r.message}" for r in results
        )
