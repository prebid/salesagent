"""Integration tests for MCP protocol implementation and flow."""

import json
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest
from fastmcp.client import Client
from fastmcp.client.transports import StreamableHttpTransport

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


class TestMCPProtocol:
    """Test the full MCP protocol implementation."""

    @pytest.fixture
    async def mcp_client(self, sample_principal):
        """Create an MCP client with test credentials."""
        headers = {"x-adcp-auth": sample_principal["access_token"]}
        transport = StreamableHttpTransport(url="http://localhost:8080/mcp/", headers=headers)
        client = Client(transport=transport)
        return client

    @pytest.mark.requires_server
    async def test_get_products_with_required_fields(self, mcp_client):
        """Test get_products with all required fields."""
        async with mcp_client as client:
            # Test with both required fields
            result = await client.call_tool(
                "get_products",
                {
                    "req": {
                        "brief": "Looking for display ads on news sites",
                        "brand_manifest": {"name": "AI analytics platform for businesses"},
                    }
                },
            )

            assert result is not None

            # FastMCP call_tool returns structured_content
            content = result.structured_content if hasattr(result, "structured_content") else result
            assert "products" in content

            products = content.get("products", [])

            assert isinstance(products, list)

            # Check product structure
            if len(products) > 0:
                product = products[0]
                assert "product_id" in product or hasattr(product, "product_id")
                assert "name" in product or hasattr(product, "name")
                assert "formats" in product or hasattr(product, "formats")

    @pytest.mark.requires_server
    async def test_get_products_missing_promoted_offering(self, mcp_client):
        """Test that get_products fails without promoted_offering."""
        async with mcp_client as client:
            with pytest.raises(Exception) as exc_info:
                await client.call_tool(
                    "get_products",
                    {
                        "req": {
                            "brief": "Looking for display ads"
                            # Missing promoted_offering
                        }
                    },
                )

            # Should get validation error
            assert "promoted_offering" in str(exc_info.value).lower() or "required" in str(exc_info.value).lower()

    @pytest.mark.requires_server
    async def test_full_media_buy_lifecycle(self, mcp_client):
        """Test the complete lifecycle of creating and managing a media buy."""
        async with mcp_client as client:
            # Step 1: Get available products
            products_result = await client.call_tool(
                "get_products",
                {
                    "req": {
                        "brief": "video ads for sports content",
                        "brand_manifest": {"name": "Sports betting app targeting NFL fans"},
                    }
                },
            )

            # Extract product IDs from FastMCP response
            content = (
                products_result.structured_content
                if hasattr(products_result, "structured_content")
                else products_result
            )
            products = content.get("products", [])

            assert len(products) > 0, "Should have at least one product available"

            product_ids = []
            for product in products:
                if isinstance(product, dict):
                    product_ids.append(product["product_id"])
                else:
                    product_ids.append(product.product_id)

            # Step 2: Create media buy with country targeting
            start_date = date.today() + timedelta(days=1)
            end_date = start_date + timedelta(days=30)

            create_result = await client.call_tool(
                "create_media_buy",
                {
                    "req": {
                        "product_ids": product_ids[:1],  # Use first product
                        "total_budget": 5000.0,
                        "flight_start_date": start_date.isoformat(),
                        "flight_end_date": end_date.isoformat(),
                        "targeting_overlay": {
                            "geo_country_any_of": ["US", "CA"],
                            "device_type_any_of": ["mobile", "desktop"],
                        },
                    }
                },
            )

            # Extract media buy ID and context ID
            if isinstance(create_result, dict):
                media_buy_id = create_result.get("media_buy_id")
                context_id = create_result.get("context_id")
            else:
                media_buy_id = create_result.media_buy_id if hasattr(create_result, "media_buy_id") else None
                context_id = create_result.context_id if hasattr(create_result, "context_id") else None

            assert media_buy_id is not None, "Should return a media_buy_id"
            assert context_id is not None, "Should return a context_id"

            # Step 3: Check status using context_id
            status_result = await client.call_tool("check_media_buy_status", {"req": {"context_id": context_id}})

            if isinstance(status_result, dict):
                status = status_result.get("status")
            else:
                status = status_result.status if hasattr(status_result, "status") else None

            assert status in [
                "pending_creative",
                "active",
                "paused",
            ], f"Unexpected status: {status}"

            # Step 4: Add creative assets
            creative_result = await client.call_tool(
                "add_creative_assets",
                {
                    "req": {
                        "media_buy_id": media_buy_id,
                        "creatives": [
                            {
                                "creative_id": "test_creative_001",
                                "format": "display_300x250",
                                "content": {
                                    "type": "url",
                                    "url": "https://example.com/creative.jpg",
                                },
                            }
                        ],
                    }
                },
            )

            assert creative_result is not None

            # Step 5: Update media buy with new targeting
            update_result = await client.call_tool(
                "update_media_buy",
                {
                    "req": {
                        "media_buy_id": media_buy_id,
                        "total_budget": 7500.0,  # Increase budget
                        "targeting_overlay": {
                            "geo_country_any_of": ["US", "CA", "GB"],  # Add GB
                            "device_type_any_of": [
                                "mobile",
                                "desktop",
                                "tablet",
                            ],  # Add tablet
                        },
                    }
                },
            )

            assert update_result is not None

            # Step 6: Get delivery stats
            delivery_result = await client.call_tool("get_media_buy_delivery", {"req": {"media_buy_id": media_buy_id}})

            assert delivery_result is not None

    @pytest.mark.requires_server
    @pytest.mark.requires_db  # Needs running MCP server - skip in quick mode
    async def test_auth_header_required(self):
        """Test that authentication via x-adcp-auth header is required."""
        # Create client without auth header
        transport = StreamableHttpTransport(url="http://localhost:8080/mcp/")
        client = Client(transport=transport)

        async with client:
            with pytest.raises(Exception) as exc_info:
                await client.call_tool(
                    "get_products",
                    {"req": {"brief": "test", "brand_manifest": {"name": "test"}}},
                )

            # Should get auth error
            assert "auth" in str(exc_info.value).lower() or "unauthorized" in str(exc_info.value).lower()

    @pytest.mark.requires_server
    async def test_country_targeting_validation(self, mcp_client):
        """Test that country codes are validated properly."""
        async with mcp_client as client:
            # Get a product first
            products_result = await client.call_tool(
                "get_products",
                {"req": {"brief": "display ads", "brand_manifest": {"name": "test product"}}},
            )

            content = (
                products_result.structured_content
                if hasattr(products_result, "structured_content")
                else products_result
            )
            products = content.get("products", [])

            assert len(products) > 0

            product_id = products[0]["product_id"] if isinstance(products[0], dict) else products[0].product_id

            # Test with valid ISO country codes
            start_date = date.today() + timedelta(days=1)
            end_date = start_date + timedelta(days=30)

            result = await client.call_tool(
                "create_media_buy",
                {
                    "req": {
                        "product_ids": [product_id],
                        "total_budget": 1000.0,
                        "flight_start_date": start_date.isoformat(),
                        "flight_end_date": end_date.isoformat(),
                        "targeting_overlay": {"geo_country_any_of": ["US", "GB", "FR", "DE", "JP"]},
                    }
                },
            )

            assert result is not None

            # Test with invalid country codes should still work (adapter validates)
            result2 = await client.call_tool(
                "create_media_buy",
                {
                    "req": {
                        "product_ids": [product_id],
                        "total_budget": 1000.0,
                        "flight_start_date": start_date.isoformat(),
                        "flight_end_date": end_date.isoformat(),
                        "targeting_overlay": {
                            "geo_country_any_of": [
                                "USA",
                                "United Kingdom",
                            ]  # Non-ISO codes
                        },
                    }
                },
            )

            # Should succeed but adapter may normalize or reject later
            assert result2 is not None


class TestMCPTestPage:
    """Test the MCP test page functionality."""

    @pytest.fixture
    def client(self, flask_app):
        """Get Flask test client."""
        return flask_app.test_client()

    def test_mcp_test_page_requires_auth(self, admin_client):
        """Test that MCP test page requires authentication."""
        response = admin_client.get("/mcp-test")
        assert response.status_code == 302  # Redirect to login

    def test_mcp_test_page_requires_super_admin(self, admin_client, integration_db):
        """Test that MCP test page requires super admin role."""
        with admin_client.session_transaction() as sess:
            sess["authenticated"] = True
            sess["email"] = "user@example.com"
            sess["role"] = "tenant_admin"
            sess["user"] = {"email": "user@example.com", "role": "tenant_admin"}

        response = admin_client.get("/mcp-test")
        assert response.status_code == 403  # Forbidden

    def test_mcp_test_page_loads_for_super_admin(self, authenticated_admin_session):
        """Test that MCP test page loads for super admin."""
        response = authenticated_admin_session.get("/mcp-test")
        assert response.status_code == 200
        assert b"MCP Protocol Test" in response.data
        assert b"get_products" in response.data
        assert b"create_media_buy" in response.data

    @pytest.mark.xfail(reason="Complex MCP mocking - actual server call happening")
    def test_mcp_test_api_endpoint(self, authenticated_admin_session, sample_principal):
        """Test the MCP test API endpoint."""
        # Mock the MCP client call - patch where it's imported
        with patch("fastmcp.client.Client") as MockClient:
            mock_client = MagicMock()
            MockClient.return_value = mock_client

            # Setup mock response - make it async compatible
            async def mock_call_tool(*args, **kwargs):
                return MagicMock(
                    model_dump=lambda: {
                        "products": [
                            {
                                "product_id": "prod_001",
                                "name": "Test Product",
                                "formats": ["display_300x250"],
                            }
                        ]
                    }
                )

            mock_client.call_tool = mock_call_tool

            # Make the context manager async compatible
            async def async_enter(self):
                return mock_client

            async def async_exit(self, *args):
                return None

            mock_client.__aenter__ = async_enter
            mock_client.__aexit__ = async_exit

            # Make API call
            response = authenticated_admin_session.post(
                "/api/mcp-test/call",
                json={
                    "server_url": "http://localhost:8080/mcp/",
                    "tool": "get_products",
                    "params": {"brief": "test", "brand_manifest": {"name": "test offering"}},
                    "access_token": sample_principal["access_token"],
                },
                headers={"Content-Type": "application/json"},
            )

            assert response.status_code == 200
            data = json.loads(response.data)
            assert data["success"] is True
            assert "result" in data

    def test_mcp_test_page_shows_principals(self, authenticated_admin_session, sample_tenant, sample_principal):
        """Test that MCP test page shows available principals."""
        # The page dynamically loads principals, so we just check the page loads
        response = authenticated_admin_session.get("/mcp-test")
        assert response.status_code == 200

        # Check that the principal select element exists
        assert b"principal_select" in response.data
        assert b"-- Select a Principal --" in response.data

    def test_mcp_test_response_parsing(self, authenticated_admin_session):
        """Test that the test page includes response parsing functionality."""
        response = authenticated_admin_session.get("/mcp-test")
        assert response.status_code == 200

        # Check for parsing functions in JavaScript
        assert b"parseResponseForNextCall" in response.data
        assert b"useParsedData" in response.data
        assert b"Parsed Data from Previous Response" in response.data

    def test_mcp_test_country_targeting_ui(self, authenticated_admin_session):
        """Test that country targeting is in the UI examples."""
        response = authenticated_admin_session.get("/mcp-test")
        assert response.status_code == 200

        # Check for country targeting in sample parameters
        assert b"geo_country_any_of" in response.data
        assert b'["US", "CA"]' in response.data or b'["US", "GB"]' in response.data
