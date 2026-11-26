"""
Real GAM E2E tests that verify actual Google Ad Manager API connectivity.

These tests require valid GAM credentials and connect to a real GAM network the suite books against.
They are gated behind @pytest.mark.requires_gam. The network, advertiser, ad units and
service account are configuration (``ToolingSettings.gam_suite_network``); a test fails naming
any that is unset.

Run with:
    uv run pytest tests/e2e/test_gam_real.py -v
"""

from unittest.mock import MagicMock

import pytest


@pytest.mark.requires_gam
class TestGAMConnection:
    """Verify basic GAM API connectivity and authentication."""

    def test_network_connection(self, gam_client_manager, gam_suite_network):
        """Service account can authenticate and retrieve network info."""

        client = gam_client_manager.get_client()
        network_service = client.GetService("NetworkService")
        network = network_service.getCurrentNetwork()

        assert str(network["networkCode"]) == gam_suite_network.network_code
        assert network["displayName"] is not None

    def test_is_connected(self, gam_client_manager):
        """is_connected() returns True for valid credentials."""
        assert gam_client_manager.is_connected() is True

    def test_current_user(self, gam_client_manager):
        """Service account can query current user info."""

        client = gam_client_manager.get_client()
        user_service = client.GetService("UserService")
        current_user = user_service.getCurrentUser()

        assert current_user is not None
        assert hasattr(current_user, "id")
        assert hasattr(current_user, "email")


@pytest.mark.requires_gam
class TestGAMInventory:
    """Verify GAM inventory (ad units) discovery works."""

    def test_list_ad_units(self, gam_client_manager):
        """Can list ad units from the suite network."""
        from googleads import ad_manager

        inv_service = gam_client_manager.get_service("InventoryService")
        sb = ad_manager.StatementBuilder()
        sb.limit = 100

        result = inv_service.getAdUnitsByStatement(sb.ToStatement())

        assert "results" in result
        assert len(result["results"]) > 0

    def test_ad_unit_has_expected_structure(self, gam_client_manager, gam_suite_network):
        """Each configured ad unit exists and has the fields the adapter expects."""
        from googleads import ad_manager

        inv_service = gam_client_manager.get_service("InventoryService")
        for ad_unit_id in gam_suite_network.ad_unit_ids:
            sb = ad_manager.StatementBuilder()
            sb.Where("id = :id").WithBindVariable("id", int(ad_unit_id))

            result = inv_service.getAdUnitsByStatement(sb.ToStatement())

            assert "results" in result and result["results"], f"ad unit {ad_unit_id} not found in the network"
            au = result["results"][0]

            # Verify essential fields exist
            assert str(au["id"]) == ad_unit_id
            assert "name" in au
            assert "adUnitCode" in au
            assert "status" in au


@pytest.mark.requires_gam
class TestGAMAdvertiser:
    """Verify GAM advertiser (company) operations work."""

    def test_list_advertisers(self, gam_client_manager):
        """Can list advertisers from the suite network."""
        from googleads import ad_manager

        company_service = gam_client_manager.get_service("CompanyService")
        sb = ad_manager.StatementBuilder()
        sb.Where("type = :type").WithBindVariable("type", "ADVERTISER")
        sb.limit = 100

        result = company_service.getCompaniesByStatement(sb.ToStatement())

        assert "results" in result
        assert len(result["results"]) > 0

    def test_advertiser_has_expected_structure(self, gam_client_manager, gam_suite_network):
        """Advertisers have the fields the adapter expects."""
        from googleads import ad_manager

        company_service = gam_client_manager.get_service("CompanyService")
        sb = ad_manager.StatementBuilder()
        sb.Where("id = :id").WithBindVariable("id", int(gam_suite_network.advertiser_id))

        result = company_service.getCompaniesByStatement(sb.ToStatement())

        assert "results" in result
        co = result["results"][0]

        assert co["id"] == int(gam_suite_network.advertiser_id)
        assert isinstance(co["name"], str) and co["name"], "the advertiser has a name"
        assert co["type"] == "ADVERTISER"


@pytest.mark.requires_gam
class TestGAMAdapter:
    """Test the GAM adapter wrapper with real API calls."""

    def _make_adapter(self, gam_client_manager, gam_suite_network):
        """A GoogleAdManager adapter over the adapter config the server builds from the onboarded row."""
        from src.adapters.google_ad_manager import GoogleAdManager

        # Principal is required but only used for advertiser mapping
        principal = MagicMock()
        principal.tenant_id = "e2e_test"
        principal.principal_id = "e2e_test_principal"
        principal.platform_mappings = {"gam_advertiser_id": gam_suite_network.advertiser_id}

        return GoogleAdManager(
            config=gam_client_manager.config,
            principal=principal,
            network_code=gam_suite_network.network_code,
            advertiser_id=gam_suite_network.advertiser_id,
            tenant_id="e2e_test",
            tenant_ai_config=None,
        )

    def test_adapter_initialization(self, gam_client_manager, gam_suite_network):
        """GoogleAdManager adapter initializes with service account credentials."""
        adapter = self._make_adapter(gam_client_manager, gam_suite_network)

        assert adapter.client_manager is not None
        assert adapter.client_manager.is_connected()

    def test_adapter_get_advertisers(self, gam_client_manager, gam_suite_network):
        """Adapter can fetch advertisers via its own API."""
        adapter = self._make_adapter(gam_client_manager, gam_suite_network)

        companies = adapter.get_advertisers()
        assert len(companies) > 0


@pytest.mark.requires_gam
class TestGAMHealthCheck:
    """Test GAM health check via client manager (not GAMHealthChecker directly).

    Note: GAMHealthChecker._init_client() only supports service_account_key_file,
    not service_account_json. Using client_manager.test_connection() instead.
    See for the health checker bug.
    """

    def test_health_check_via_client_manager(self, gam_client_manager):
        """Client manager's test_connection verifies auth works."""
        from src.adapters.gam.utils.health_check import HealthStatus

        result = gam_client_manager.test_connection()

        assert result.status == HealthStatus.HEALTHY, f"Health check failed: {result.message}"

    def test_full_health_check(self, gam_client_manager, gam_suite_network):
        """Full health check returns healthy status."""
        from src.adapters.gam.utils.health_check import HealthStatus

        overall_status, results = gam_client_manager.check_health(
            advertiser_id=gam_suite_network.advertiser_id,
            ad_unit_ids=gam_suite_network.ad_unit_ids,
        )

        # At minimum, the overall status should not be UNHEALTHY
        assert overall_status != HealthStatus.UNHEALTHY, "Health check returned UNHEALTHY. Results: " + "; ".join(
            f"{r.check_name}: {r.status.value} - {r.message}" for r in results
        )
