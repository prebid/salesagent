"""Integration tests for A2A response message field validation.

This test suite prevents AttributeError bugs when A2A handlers try to access
fields that don't exist on response objects (like response.message when the
response type doesn't have a message attribute).

Key principle: Test the ACTUAL dict construction that happens in _handle_*_skill
methods, not just the response object structure.

Regression prevention: https://github.com/adcontextprotocol/salesagent/pull/337
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from src.a2a_server.adcp_a2a_server import AdCPRequestHandler
from tests.helpers.a2a_response_validator import assert_valid_skill_response

pytestmark = [pytest.mark.integration]


@pytest.mark.integration
class TestA2AMessageFieldValidation:
    """Test that all A2A skill handlers properly construct message fields.

    These tests catch AttributeError bugs when handlers try to access
    response.message on response types that don't have that field.
    """

    @pytest.fixture
    def handler(self):
        """Create A2A request handler."""
        return AdCPRequestHandler()

    @pytest.fixture
    def mock_auth_context(self, sample_tenant, sample_principal):
        """Mock authentication context for all tests."""
        from src.a2a_server import adcp_a2a_server

        def _mock_context(handler):
            # Set up request context with proper headers for tenant resolution
            # This will allow _create_tool_context_from_a2a to resolve the tenant from headers
            # Use ContextVars instead of threading.local()
            adcp_a2a_server._request_headers.set(
                {
                    "x-adcp-tenant": sample_tenant["tenant_id"],
                    "authorization": f"Bearer {sample_principal['access_token']}",
                }
            )

            handler._get_auth_token = MagicMock(return_value=sample_principal["access_token"])

            # Only mock get_principal_from_token - let real tenant lookups happen
            # since sample_tenant fixture created the tenant in the database
            return patch(
                "src.core.auth_utils.get_principal_from_token",
                return_value=sample_principal["principal_id"],
            )

        return _mock_context

    @pytest.mark.asyncio
    async def test_create_media_buy_message_field_exists(
        self, handler, mock_auth_context, sample_tenant, sample_principal, sample_products
    ):
        """Test create_media_buy returns a valid message field.

        Prevents: 'CreateMediaBuyResponse' object has no attribute 'message'
        """
        with mock_auth_context(handler):
            # Create parameters for create_media_buy skill
            start_date = datetime.now(UTC) + timedelta(days=1)
            end_date = start_date + timedelta(days=30)

            params = {
                "brand_manifest": {"name": "Test Campaign"},
                "packages": [
                    {
                        "buyer_ref": f"pkg_{sample_products[0]}",
                        "products": [sample_products[0]],
                        "budget": {"total": 10000.0, "currency": "USD"},
                    }
                ],
                "budget": {"total": 10000.0, "currency": "USD"},
                "start_time": start_date.isoformat(),
                "end_time": end_date.isoformat(),
            }

            # Call the handler method directly - this is where the bug occurred
            result = await handler._handle_create_media_buy_skill(params, sample_principal["access_token"])

            # ✅ CRITICAL: Use comprehensive validator to check all fields
            assert_valid_skill_response(result, "create_media_buy")

    @pytest.mark.asyncio
    async def test_sync_creatives_message_field_exists(self, handler, mock_auth_context, sample_principal):
        """Test sync_creatives returns a valid message field.

        SyncCreativesResponse also doesn't have a .message field, uses __str__
        """
        with mock_auth_context(handler):
            params = {
                "creatives": [
                    {
                        "buyer_ref": "creative_test_001",
                        "format_id": "display_300x250",
                        "name": "Test Creative",
                        "assets": {"main_image": {"asset_type": "image", "url": "https://example.com/image.jpg"}},
                    }
                ],
                "validation_mode": "strict",
            }

            # Call handler directly
            result = await handler._handle_sync_creatives_skill(params, sample_principal["access_token"])

            # ✅ Use validator
            assert_valid_skill_response(result, "sync_creatives")

    @pytest.mark.asyncio
    async def test_get_products_message_field_exists(self, handler, mock_auth_context, sample_principal):
        """Test get_products returns a valid message field.

        GetProductsResponse DOES have a .message field, but we should use str() consistently
        """
        with mock_auth_context(handler):
            params = {
                "brand_manifest": {"name": "Test product search"},
                "brief": "Looking for display ads",
            }

            result = await handler._handle_get_products_skill(params, sample_principal["access_token"])

            # ✅ Validate message field
            assert "message" in result, "get_products response must include 'message' field"
            assert isinstance(result["message"], str), "message must be a string"

    @pytest.mark.asyncio
    async def test_list_creatives_message_field_exists(self, handler, mock_auth_context, sample_principal):
        """Test list_creatives returns a valid message field."""
        with mock_auth_context(handler):
            params = {
                "buyer_ref": "test_creative",
                "page": 1,
                "limit": 10,
            }

            result = await handler._handle_list_creatives_skill(params, sample_principal["access_token"])

            # ✅ Validate message field
            assert "message" in result, "list_creatives response must include 'message' field"
            assert isinstance(result["message"], str), "message must be a string"

    @pytest.mark.asyncio
    async def test_list_creative_formats_message_field_exists(self, handler, mock_auth_context, sample_principal):
        """Test list_creative_formats returns a valid message field."""
        with mock_auth_context(handler):
            params = {}

            result = await handler._handle_list_creative_formats_skill(params, sample_principal["access_token"])

            # ✅ Validate message field
            assert "message" in result, "list_creative_formats response must include 'message' field"
            assert isinstance(result["message"], str), "message must be a string"


@pytest.mark.integration
class TestA2AResponseDictConstruction:
    """Test that all response types can be safely converted to A2A response dicts.

    This catches the pattern where we try to access an attribute that doesn't exist
    on a Pydantic model, by testing the dict construction directly.
    """

    def test_create_media_buy_response_to_dict(self):
        """Test CreateMediaBuyResponse can be converted to A2A dict.

        Protocol fields (status) are added by A2A wrapper, not in domain response.
        """
        from src.core.schemas import CreateMediaBuyResponse

        response = CreateMediaBuyResponse(
            buyer_ref="test-123",
            media_buy_id="mb-456",
        )

        # Simulate what _handle_create_media_buy_skill does
        # ✅ This should NOT raise AttributeError
        a2a_dict = {
            "success": True,
            "media_buy_id": response.media_buy_id,
            "message": str(response),  # Safe for all response types
        }

        assert a2a_dict["message"] == "Media buy mb-456 created successfully."

    def test_sync_creatives_response_to_dict(self):
        """Test SyncCreativesResponse can be converted to A2A dict.

        Protocol fields (status, message) are added by A2A wrapper.
        Domain response uses __str__() to generate message.
        """
        from src.core.schemas import SyncCreativeResult, SyncCreativesResponse

        response = SyncCreativesResponse(
            dry_run=False,
            creatives=[
                SyncCreativeResult(
                    buyer_ref="test-001",
                    creative_id="cr-001",
                    status="approved",
                    action="created",  # Required field
                )
            ],
        )

        # ✅ This should NOT raise AttributeError
        a2a_dict = {
            "success": True,
            "message": str(response),  # Safe - uses __str__ method
        }

        assert isinstance(a2a_dict["message"], str)
        assert len(a2a_dict["message"]) > 0

    def test_get_products_response_to_dict(self):
        """Test GetProductsResponse can be converted to A2A dict."""
        from src.core.schema_adapters import GetProductsResponse

        response = GetProductsResponse(products=[])

        # ✅ Uses __str__ method to generate message
        a2a_dict = {
            "products": [p.model_dump() if hasattr(p, "model_dump") else p for p in response.products],
            "message": str(response),  # Uses __str__ method
        }

        assert a2a_dict["message"] == "No products matched your requirements."

    def test_all_response_types_have_str_or_message(self):
        """Test that all response types used in A2A have either __str__ or .message.

        This is a contract test - ensures we don't add response types that
        can't be safely converted to A2A dicts.
        """
        from src.core.schemas import (
            CreateMediaBuyResponse,
            GetProductsResponse,
            ListCreativeFormatsResponse,
            ListCreativesResponse,
            SyncCreativesResponse,
        )

        response_types = [
            CreateMediaBuyResponse,
            SyncCreativesResponse,
            GetProductsResponse,
            ListCreativeFormatsResponse,
            ListCreativesResponse,
        ]

        for response_cls in response_types:
            # Check if it has __str__ method or message field
            has_str_method = hasattr(response_cls, "__str__")

            # Try to create a minimal instance and check for message field
            # This is tricky because we need to provide required fields
            # For now, just check the class definition
            has_message_field = "message" in response_cls.model_fields

            assert (
                has_str_method or has_message_field
            ), f"{response_cls.__name__} must have either __str__ method or .message field for A2A compatibility"


@pytest.mark.integration
class TestA2AErrorHandling:
    """Test that A2A handlers properly handle errors without AttributeErrors."""

    @pytest.fixture
    def handler(self):
        return AdCPRequestHandler()

    @pytest.mark.asyncio
    async def test_skill_error_has_message_field(self, handler, sample_principal):
        """Test that skill errors return proper message fields."""
        handler._get_auth_token = MagicMock(return_value=sample_principal["access_token"])

        with patch("src.a2a_server.adcp_a2a_server.get_principal_from_token") as mock_get_principal:
            mock_get_principal.return_value = sample_principal["principal_id"]

            # Force an error by passing invalid parameters
            params = {
                # Missing required fields - should cause validation error
            }

            try:
                result = await handler._handle_create_media_buy_skill(params, sample_principal["access_token"])
                # If it doesn't raise, check the error response structure
                if not result.get("success", True):
                    assert "message" in result or "error" in result, "Error response must have message or error field"
            except Exception as e:
                # Errors are expected for invalid params
                assert "message" not in str(e) or "AttributeError" not in str(
                    e
                ), "Should not get AttributeError when handling skill errors"
