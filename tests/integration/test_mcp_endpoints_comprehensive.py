"""Comprehensive integration tests for MCP endpoints.

This test file ensures all MCP tools work correctly with proper authentication
and data validation. It tests the actual server endpoints, not mocks.
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastmcp.client import Client
from fastmcp.client.transports import StreamableHttpTransport

from src.core.database.database_session import get_db_session
from src.core.database.models import Principal, Product
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


class TestMCPEndpointsComprehensive:
    """Comprehensive tests for all MCP endpoints."""

    @pytest.fixture(autouse=True)
    def setup_test_data(self, integration_db):
        """Create test data for MCP tests.

        This runs BEFORE mcp_server starts (due to autouse=True and fixture ordering).
        """
        # Write test data to the database
        with get_db_session() as session:
            # Create test tenant
            tenant = create_tenant_with_timestamps(
                tenant_id="test_mcp",
                name="Test MCP Tenant",
                subdomain="test-mcp",
                is_active=True,
                ad_server="mock",
                max_daily_budget=10000,
                enable_axe_signals=True,
                authorized_emails=[],
                authorized_domains=[],
                auto_approve_formats=["display_300x250"],
                human_review_required=False,
                admin_token="test_admin_token",
            )
            session.add(tenant)

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

            # Create test products with all required fields
            products = [
                Product(
                    tenant_id="test_mcp",
                    product_id="display_news",
                    name="Display Ads - News Sites",
                    description="Premium display advertising on news websites",
                    formats=[
                        {
                            "format_id": "display_300x250",
                            "name": "Medium Rectangle",
                            "type": "display",
                            "width": 300,
                            "height": 250,
                        }
                    ],
                    targeting_template={"geo_country": {"values": ["US", "CA"], "required": False}},
                    delivery_type="guaranteed",
                    is_fixed_price=True,
                    cpm=10.0,
                    is_custom=False,
                    countries=["US", "CA"],
                ),
                Product(
                    tenant_id="test_mcp",
                    product_id="video_sports",
                    name="Video Ads - Sports Content",
                    description="In-stream video ads on sports content",
                    formats=[
                        {
                            "format_id": "video_15s",
                            "name": "15 Second Video",
                            "type": "video",
                            "duration": 15,
                        }
                    ],
                    targeting_template={"content_category": {"values": ["sports"], "required": True}},
                    delivery_type="non_guaranteed",
                    is_fixed_price=False,
                    is_custom=False,
                    countries=["US"],
                ),
            ]
            for product in products:
                session.add(product)

            session.commit()

            # Explicitly close connections to ensure data is flushed to disk
            session.close()

        # Close the global session factory to ensure all connections are closed
        from src.core.database import database_session

        if database_session.db_session:
            database_session.db_session.remove()

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
                    "promoted_offering": "Tech startup promoting AI analytics platform",
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
                assert "formats" in product
                assert "delivery_type" in product
                assert product["delivery_type"] in ["guaranteed", "non_guaranteed"]
                assert "is_fixed_price" in product
                # cpm should be present for fixed-price products
                if product["is_fixed_price"]:
                    assert "cpm" in product

    @pytest.mark.requires_server
    async def test_get_products_filtering(self, mcp_client):
        """Test that get_products filters based on brief."""
        async with mcp_client as client:
            # Search for news content
            result = await client.call_tool(
                "get_products",
                {
                    "brief": "display advertising on news websites",
                    "promoted_offering": "B2B software company",
                },
            )

            content = safe_get_content(result)
            products = content["products"]

            # Should find display_news product
            news_products = [p for p in products if "news" in p["name"].lower()]
            assert len(news_products) > 0

    @pytest.mark.requires_server
    async def test_get_products_missing_required_field(self, mcp_client):
        """Test that get_products fails without promoted_offering."""
        async with mcp_client as client:
            with pytest.raises(Exception) as exc_info:
                await client.call_tool(
                    "get_products",
                    {"brief": "display ads"},  # Missing promoted_offering
                )

            # Should fail with validation error
            assert "promoted_offering" in str(exc_info.value).lower()

    def test_schema_backward_compatibility(self):
        """Test that AdCP v2.4 schema maintains backward compatibility."""
        from datetime import date, datetime, timedelta

        from src.core.schemas import Budget, CreateMediaBuyRequest, Package

        # Test 1: Legacy format should work
        legacy_request = CreateMediaBuyRequest(
            promoted_offering="Nike Air Jordan 2025 basketball shoes",
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

        # Test 2: New v2.4 format should work
        new_request = CreateMediaBuyRequest(
            promoted_offering="Adidas UltraBoost 2025 running shoes",
            buyer_ref="custom_ref_123",
            po_number="PO-V24-67890",  # Required per AdCP spec
            budget=Budget(total=10000.0, currency="EUR", pacing="asap"),
            packages=[
                Package(buyer_ref="pkg_1", products=["prod_1", "prod_3"], budget=Budget(total=6000.0, currency="EUR")),
                Package(buyer_ref="pkg_2", products=["prod_2"], budget=Budget(total=4000.0, currency="EUR")),
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
            promoted_offering="Puma RS-X 2025 training shoes",
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
                        "promoted_offering": "test",
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
                    "promoted_offering": "Enterprise SaaS platform for data analytics",
                },
            )

            products_content = safe_get_content(products_result)
            assert len(products_content["products"]) > 0

            # 2. Create media buy
            product = products_content["products"][0]
            start_date = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
            end_date = (datetime.now() + timedelta(days=37)).strftime("%Y-%m-%d")

            buy_result = await client.call_tool(
                "create_media_buy",
                {
                    "promoted_offering": "Enterprise SaaS platform for data analytics",
                    "po_number": "PO-TEST-12345",  # Required per AdCP spec
                    "product_ids": [product["product_id"]],
                    "total_budget": 10000.0,
                    "start_date": start_date,
                    "end_date": end_date,
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
            # Response contains deliveries array per AdCP spec
            assert "deliveries" in status_content
            # Newly created media buy might not have delivery data yet
            assert isinstance(status_content["deliveries"], list)
            # But should have aggregated totals
            assert "aggregated_totals" in status_content
            assert "currency" in status_content


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
