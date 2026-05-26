"""
Real GAM E2E tests that verify actual Google Ad Manager API connectivity.

These tests require valid GAM credentials and connect to a real GAM test network.
They are gated behind @pytest.mark.requires_gam and skip when credentials are absent.

Test network: 23341594478 (XFP sandbox property)
Service account: salesagent-e2e@salesagenttest.iam.gserviceaccount.com

Run with:
    uv run pytest tests/e2e/test_gam_real.py -v
"""

import os
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from src.core.canonical_formats import DEFAULT_CREATIVE_AGENT_URL
from src.core.schemas import FormatId, MediaPackage
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

        client = gam_client_manager.get_client()
        network_service = client.GetService("NetworkService")
        network = network_service.getCurrentNetwork()

        assert str(network["networkCode"]) == GAM_TEST_NETWORK_CODE
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
        """Can list ad units from the test network."""
        from googleads import ad_manager

        inv_service = gam_client_manager.get_service("InventoryService")
        sb = ad_manager.StatementBuilder()
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

        inv_service = gam_client_manager.get_service("InventoryService")
        sb = ad_manager.StatementBuilder()
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

        company_service = gam_client_manager.get_service("CompanyService")
        sb = ad_manager.StatementBuilder()
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

        company_service = gam_client_manager.get_service("CompanyService")
        sb = ad_manager.StatementBuilder()
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
class TestGAMCanonicalCreativeTrafficking:
    """Smoke-test canonical display creatives against the real GAM test network.

    Gated by GAM_RUN_TRAFFICKING_TESTS=true because it creates real GAM orders,
    line items, creatives, and LICAs before archiving the order.
    """

    @pytest.fixture(autouse=True)
    def _require_trafficking_opt_in(self):
        if os.getenv("GAM_RUN_TRAFFICKING_TESTS", "").lower() != "true":
            pytest.skip("Set GAM_RUN_TRAFFICKING_TESTS=true to create real GAM test trafficking objects")

    def _make_adapter(self, gam_service_account_json):
        from src.adapters.google_ad_manager import GoogleAdManager

        principal = MagicMock()
        principal.tenant_id = "e2e_test"
        principal.principal_id = "e2e_test_principal"
        principal.platform_mappings = {"gam_advertiser_id": GAM_TEST_ADVERTISER_ID}

        return GoogleAdManager(
            config={"service_account_json": gam_service_account_json, "network_code": GAM_TEST_NETWORK_CODE},
            principal=principal,
            network_code=GAM_TEST_NETWORK_CODE,
            advertiser_id=GAM_TEST_ADVERTISER_ID,
            tenant_id="e2e_test",
            naming_templates=("TEST Canonical Creative {media_buy_id}", "{product_name}"),
        )

    def test_canonical_display_creatives_create_and_associate(self, gam_service_account_json):
        """Create line items from canonical formats, then create and associate creatives."""
        adapter = self._make_adapter(gam_service_account_json)
        suffix = uuid.uuid4().hex[:8]
        start = datetime.now(UTC) + timedelta(days=1)
        end = start + timedelta(days=3)

        order_id = adapter.orders_manager.create_order(
            order_name=f"TEST Canonical Creatives {suffix}",
            total_budget=3.00,
            start_time=start,
            end_time=end,
            currency="USD",
            po_number=f"CANON-{suffix}",
        )

        packages = [
            MediaPackage(
                package_id=f"pkg_prod_gamcanonimg_{suffix}_1",
                product_id="prod_gamcanonimg",
                name="Canonical Display Image",
                delivery_type="non_guaranteed",
                impressions=1000,
                budget=1.00,
                format_ids=[
                    FormatId(
                        agent_url=DEFAULT_CREATIVE_AGENT_URL,
                        id="display_image",
                        width=300,
                        height=250,
                    )
                ],
            ),
            MediaPackage(
                package_id=f"pkg_prod_gamcanonhtml_{suffix}_2",
                product_id="prod_gamcanonhtml",
                name="Canonical Display HTML",
                delivery_type="non_guaranteed",
                impressions=1000,
                budget=1.00,
                format_ids=[
                    FormatId(
                        agent_url=DEFAULT_CREATIVE_AGENT_URL,
                        id="display_html",
                        width=300,
                        height=250,
                    )
                ],
            ),
            MediaPackage(
                package_id=f"pkg_prod_gamcanonjs_{suffix}_3",
                product_id="prod_gamcanonjs",
                name="Canonical Display JS",
                delivery_type="non_guaranteed",
                impressions=1000,
                budget=1.00,
                format_ids=[
                    FormatId(
                        agent_url=DEFAULT_CREATIVE_AGENT_URL,
                        id="display_js",
                        width=300,
                        height=250,
                    )
                ],
            ),
        ]

        products_map = {
            pkg.package_id: {
                "product_id": pkg.product_id,
                "delivery_type": "non_guaranteed",
                "implementation_config": {
                    "targeted_ad_unit_ids": [GAM_TEST_AD_UNIT_IDS[0]],
                    "include_descendants": True,
                    "supported_format_types": ["display"],
                    "primary_goal_type": "LIFETIME",
                    "primary_goal_unit_type": "IMPRESSIONS",
                    "creative_rotation_type": "EVEN",
                    "delivery_rate_type": "EVENLY",
                },
            }
            for pkg in packages
        }
        package_pricing_info = {
            pkg.package_id: {
                "pricing_model": "cpm",
                "rate": 1.00,
                "currency": "USD",
                "is_fixed": True,
                "bid_price": None,
            }
            for pkg in packages
        }

        try:
            line_item_ids = adapter.orders_manager.create_line_items(
                order_id=order_id,
                packages=packages,
                start_time=start,
                end_time=end,
                products_map=products_map,
                tenant_id=None,
                order_name=f"TEST Canonical Creatives {suffix}",
                package_pricing_info=package_pricing_info,
                line_item_name_template="{product_name}",
            )
            assert len(line_item_ids) == len(packages)

            assets = [
                {
                    "creative_id": f"canon_img_{suffix}",
                    "name": "Canonical Image Creative",
                    "format": "display_image",
                    "url": "https://www.gstatic.com/webp/gallery/1.sm.jpg",
                    "click_url": "https://example.com/",
                    "width": 300,
                    "height": 250,
                    "package_assignments": [packages[0].package_id],
                },
                {
                    "creative_id": f"canon_html_{suffix}",
                    "name": "Canonical HTML Creative",
                    "format": "display_html",
                    "media_data": "<div style='width:300px;height:250px;background:#111;color:#fff'>Test</div>",
                    "width": 300,
                    "height": 250,
                    "package_assignments": [packages[1].package_id],
                },
                {
                    "creative_id": f"canon_js_{suffix}",
                    "name": "Canonical JS Creative",
                    "format": "display_js",
                    "snippet": "<script>document.write('<div style=\"width:300px;height:250px\">Test</div>')</script>",
                    "snippet_type": "javascript",
                    "width": 300,
                    "height": 250,
                    "package_assignments": [packages[2].package_id],
                },
            ]
            statuses = adapter.add_creative_assets(order_id, assets, datetime.now(UTC))

            assert [status.status for status in statuses] == ["approved", "approved", "approved"]
        finally:
            adapter.archive_order(order_id)


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
