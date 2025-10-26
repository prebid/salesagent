"""Comprehensive integration tests for MCP endpoints.

This test file ensures all MCP tools work correctly with proper authentication
and data validation. It tests the actual server endpoints, not mocks.
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastmcp.client import Client
from fastmcp.client.transports import StreamableHttpTransport

from src.core.database.database_session import get_db_session
from src.core.database.models import Principal
from tests.integration_v2.conftest import create_test_product_with_pricing
from tests.utils.database_helpers import create_tenant_with_timestamps, get_utc_now


def safe_get_content(result):
    """Safely extract content from MCP result with proper error handling."""
    if result is None:
        return {}
    if hasattr(result, "structured_content") and result.structured_content is not None:
        return result.structured_content
    if hasattr(result, "content") and result.content is not None:
        return result.content
    return result if isinstance(result, dict) else {}


@pytest.mark.requires_db
class TestMCPEndpointsComprehensive:
    """Comprehensive tests for all MCP endpoints."""

    @pytest.fixture(autouse=True)
    def setup_test_data(self, integration_db):
        """Create test data for MCP tests.

        This runs BEFORE mcp_server starts (due to autouse=True and fixture ordering).
        """
        from tests.integration_v2.conftest import add_required_setup_data

        # Write test data to the database
        with get_db_session() as session:
            # Create test tenant
            tenant = create_tenant_with_timestamps(
                tenant_id="test_mcp",
                name="Test MCP Tenant",
                subdomain="test-mcp",
                is_active=True,
                ad_server="mock",
                enable_axe_signals=True,
                authorized_emails=["test@example.com"],  # Required for setup validation
                authorized_domains=[],
                auto_approve_formats=["display_300x250"],
                human_review_required=False,
                admin_token="test_admin_token",
            )
            session.add(tenant)
            session.flush()  # Ensure tenant is persisted before adding related data

            # Add required setup data (CurrencyLimit, AuthorizedProperty, PropertyTag, etc.)
            add_required_setup_data(session, "test_mcp")

            # Create test principal with proper platform_mappings
            principal = Principal(
                tenant_id="test_mcp",
                principal_id="test_principal",
                name="Test Principal",
                access_token="test_mcp_token_12345",
                platform_mappings={"mock": {"id": "test_advertiser"}},
                created_at=get_utc_now(),
            )
            session.add(principal)

            # Create test products with new pricing model
            product1 = create_test_product_with_pricing(
                session=session,
                tenant_id="test_mcp",
                product_id="display_news",
                name="Display Ads - News Sites",
                description="Premium display advertising on news websites",
                formats=[
                    {
                        "agent_url": "https://test.com",
                        "id": "display_300x250",
                    }
                ],
                targeting_template={"geo_country": {"values": ["US", "CA"], "required": False}},
                delivery_type="guaranteed",
                pricing_model="CPM",
                rate="10.0",
                is_fixed=True,
                currency="USD",
                countries=["US", "CA"],
                is_custom=False,
            )

            product2 = create_test_product_with_pricing(
                session=session,
                tenant_id="test_mcp",
                product_id="video_sports",
                name="Video Ads - Sports Content",
                description="In-stream video ads on sports content",
                formats=[
                    {
                        "agent_url": "https://test.com",
                        "id": "video_15s",
                    }
                ],
                targeting_template={"content_category": {"values": ["sports"], "required": True}},
                delivery_type="non_guaranteed",
                pricing_model="CPM",
                rate="1.0",  # Non-guaranteed typically has lower floor
                is_fixed=False,
                currency="USD",
                countries=["US"],
                is_custom=False,
                price_guidance={"floor": 1.0, "p50": 5.0, "p75": 8.0, "p90": 12.0},
            )

            session.commit()

            # Explicitly close connections to ensure data is flushed to disk
            session.close()

        # Store data for tests
        self.test_token = "test_mcp_token_12345"
        self.tenant_id = "test_mcp"
        self.principal_id = "test_principal"

    @pytest.fixture
    async def mcp_client(self, mcp_server):
        """Create MCP client with test authentication.

        Note: setup_test_data runs before this (autouse=True) to populate the database.
        """
        headers = {"x-adcp-auth": self.test_token}
        transport = StreamableHttpTransport(url=f"http://localhost:{mcp_server.port}/mcp/", headers=headers)
        client = Client(transport=transport)
        return client

    @pytest.mark.requires_server
    async def test_get_products_basic(self, mcp_client):
        """Test basic get_products functionality."""
        async with mcp_client as client:
            result = await client.call_tool(
                "get_products",
                {
                    "brief": "display ads for news content",
                    "brand_manifest": {"name": "Tech startup promoting AI analytics platform"},
                },
            )

            assert result is not None
            content = safe_get_content(result)
            assert "products" in content

            products = content["products"]
            assert isinstance(products, list)
            assert len(products) > 0

            # Verify product structure
            for product in products:
                assert "product_id" in product
                assert "name" in product
                assert "description" in product
                assert "format_ids" in product
                assert "delivery_type" in product
                assert product["delivery_type"] in ["guaranteed", "non_guaranteed"]
                # Pricing options should be included
                assert "pricing_options" in product
                assert len(product["pricing_options"]) > 0
                # Verify pricing option structure
                pricing = product["pricing_options"][0]
                assert "pricing_model" in pricing
                assert "is_fixed" in pricing

    @pytest.mark.requires_server
    async def test_get_products_filtering(self, mcp_client):
        """Test that get_products filters based on brief."""
        async with mcp_client as client:
            # Search for news content
            result = await client.call_tool(
                "get_products",
                {
                    "brief": "display advertising on news websites",
                    "brand_manifest": {"name": "B2B software company"},
                },
            )

            content = safe_get_content(result)
            products = content["products"]

            # Should find display_news product
            news_products = [p for p in products if "news" in p["name"].lower()]
            assert len(news_products) > 0

    @pytest.mark.requires_server
    async def test_get_products_missing_required_field(self, mcp_client):
        """Test that get_products fails without brand_manifest."""
        async with mcp_client as client:
            with pytest.raises(Exception) as exc_info:
                await client.call_tool(
                    "get_products",
                    {"brief": "display ads"},  # Missing brand_manifest
                )

            # Should fail with validation error mentioning brand_manifest (or BrandManifest in some error messages)
            error_msg = str(exc_info.value).lower()
            assert "brand" in error_msg or "manifest" in error_msg

    def test_schema_backward_compatibility(self):
        """Test that AdCP v2.4 schema maintains backward compatibility."""
        from datetime import date

        from src.core.schemas import Budget, CreateMediaBuyRequest, Package

        # Test 1: Legacy format should work
        legacy_request = CreateMediaBuyRequest(
            brand_manifest={"name": "Nike Air Jordan 2025 basketball shoes"},
            product_ids=["prod_1", "prod_2"],
            total_budget=5000.0,
            start_date=date.today(),
            end_date=date.today() + timedelta(days=30),
            po_number="PO-LEGACY-12345",  # Required per AdCP spec
            targeting_overlay={"geo_country_any_of": ["US"]},
        )

        # buyer_ref should NOT be auto-generated (it's the buyer's identifier)
        assert legacy_request.buyer_ref is None

        # Should auto-create budget from total_budget
        assert legacy_request.get_total_budget() == 5000.0
        assert legacy_request.budget.total == 5000.0
        assert legacy_request.budget.currency == "USD"

        # Should create packages from product_ids
        product_ids = legacy_request.get_product_ids()
        assert len(product_ids) == 2
        assert product_ids[0] == "prod_1"
        assert product_ids[1] == "prod_2"

        # Should have packages created
        assert len(legacy_request.packages) == 2

        # Test 2: New v2.4 format should work (per AdCP spec: product_id singular)
        new_request = CreateMediaBuyRequest(
            brand_manifest={"name": "Adidas UltraBoost 2025 running shoes"},
            buyer_ref="custom_ref_123",
            po_number="PO-V24-67890",  # Required per AdCP spec
            budget=Budget(total=10000.0, currency="EUR", pacing="asap"),
            packages=[
                Package(buyer_ref="pkg_1", product_id="prod_1", budget=Budget(total=6000.0, currency="EUR")),
                Package(buyer_ref="pkg_2", product_id="prod_2", budget=Budget(total=4000.0, currency="EUR")),
            ],
            start_time=datetime.now(UTC),
            end_time=datetime.now(UTC) + timedelta(days=30),
        )

        assert new_request.buyer_ref == "custom_ref_123"
        assert new_request.budget.currency == "EUR"
        assert new_request.budget.pacing == "asap"
        assert len(new_request.packages) == 2

        # Test 3: Mixed format should work (legacy with some new fields)
        mixed_request = CreateMediaBuyRequest(
            brand_manifest={"name": "Puma RS-X 2025 training shoes"},
            buyer_ref="mixed_ref",
            po_number="PO-MIXED-99999",  # Required per AdCP spec
            product_ids=["prod_1"],
            total_budget=3000.0,
            start_date=date.today(),
            end_date=date.today() + timedelta(days=15),
            budget=Budget(total=3000.0, currency="GBP"),  # Override currency
        )

        assert mixed_request.buyer_ref == "mixed_ref"
        assert mixed_request.budget.currency == "GBP"
        assert mixed_request.get_total_budget() == 3000.0

    @pytest.mark.requires_server
    async def test_invalid_auth(self, mcp_server):
        """Test that invalid authentication is rejected."""
        headers = {"x-adcp-auth": "invalid_token"}
        transport = StreamableHttpTransport(url=f"http://localhost:{mcp_server.port}/mcp/", headers=headers)
        client = Client(transport=transport)

        async with client:
            with pytest.raises(Exception) as exc_info:
                await client.call_tool(
                    "get_products",
                    {
                        "brief": "test",
                        "brand_manifest": {"name": "test"},
                    },
                )

            # Should get authentication error
            assert "auth" in str(exc_info.value).lower() or "invalid" in str(exc_info.value).lower()

    @pytest.mark.requires_server
    async def test_get_signals_optional(self, mcp_client):
        """Test the optional get_signals endpoint."""
        async with mcp_client as client:
            # get_signals is optional, so it might not exist
            try:
                result = await client.call_tool(
                    "get_signals",
                    {
                        "req": {
                            "signal_spec": "Audiences interested in sports and athletic products",
                            "deliver_to": {
                                "platforms": "all",
                                "countries": ["US"],
                            },
                            "max_results": 10,
                        }
                    },
                )

                content = safe_get_content(result)
                assert "signals" in content
                assert isinstance(content["signals"], list)
            except Exception as e:
                # If tool doesn't exist, that's ok (it's optional)
                if "unknown tool" not in str(e).lower():
                    raise

    @pytest.mark.requires_server
    async def test_full_workflow(self, mcp_client):
        """Test a complete workflow from discovery to media buy."""
        async with mcp_client as client:
            # 1. Discover products
            products_result = await client.call_tool(
                "get_products",
                {
                    "brief": "Looking for premium display advertising",
                    "brand_manifest": {"name": "Enterprise SaaS platform for data analytics"},
                },
            )

            products_content = safe_get_content(products_result)
            assert len(products_content["products"]) > 0

            # 2. Create media buy
            product = products_content["products"][0]
            start_time = (datetime.now(UTC) + timedelta(days=7)).isoformat()
            end_time = (datetime.now(UTC) + timedelta(days=37)).isoformat()

            buy_result = await client.call_tool(
                "create_media_buy",
                {
                    "brand_manifest": {"name": "Enterprise SaaS platform for data analytics"},
                    "buyer_ref": "test_workflow_buy_001",  # Required per AdCP spec
                    "packages": [
                        {
                            "buyer_ref": "pkg_001",
                            "product_id": product["product_id"],
                            "budget": {"total": 10000.0, "currency": "USD"},
                        }
                    ],
                    "start_time": start_time,
                    "end_time": end_time,
                    "budget": {"total": 10000.0, "currency": "USD"},
                    "po_number": "PO-TEST-12345",  # Required per AdCP spec
                },
            )

            buy_content = safe_get_content(buy_result)
            assert "media_buy_id" in buy_content
            media_buy_id = buy_content["media_buy_id"]

            # 3. Get media buy delivery status
            status_result = await client.call_tool(
                "get_media_buy_delivery",
                {"media_buy_ids": [media_buy_id]},
            )

            status_content = safe_get_content(status_result)
            # Response contains media_buy_deliveries array per AdCP spec
            assert "media_buy_deliveries" in status_content
            # Newly created media buy might not have delivery data yet
            assert isinstance(status_content["media_buy_deliveries"], list)
            # But should have aggregated totals
            assert "aggregated_totals" in status_content
            assert "currency" in status_content


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
