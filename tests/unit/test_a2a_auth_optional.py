#!/usr/bin/env python3
"""
Unit tests for A2A auth-optional discovery endpoints.

Tests that discovery endpoints (list_creative_formats, list_authorized_properties, get_products)
properly handle both authenticated and unauthenticated requests according to AdCP spec.
"""

from unittest.mock import MagicMock, patch

import pytest
from a2a.types import InvalidRequestError
from a2a.utils.errors import ServerError

from src.a2a_server.adcp_a2a_server import AdCPRequestHandler, MinimalContext


class TestAuthOptionalSkills:
    """Test auth-optional skill handling in A2A server."""

    def setup_method(self):
        """Set up test fixtures."""
        self.handler = AdCPRequestHandler()

    @pytest.mark.asyncio
    async def test_list_creative_formats_without_auth(self):
        """list_creative_formats should work without authentication."""
        with patch("src.a2a_server.adcp_a2a_server.core_list_creative_formats_tool") as mock_tool:
            mock_tool.return_value = {"formats": []}

            result = await self.handler._handle_list_creative_formats_skill(parameters={}, auth_token=None)

            assert result is not None
            assert "formats" in result
            mock_tool.assert_called_once()

    @pytest.mark.asyncio
    async def test_list_creative_formats_with_auth(self):
        """list_creative_formats should work with valid authentication."""
        with (
            patch("src.a2a_server.adcp_a2a_server.core_list_creative_formats_tool") as mock_tool,
            patch.object(self.handler, "_create_tool_context_from_a2a") as mock_create_context,
        ):
            mock_tool.return_value = {"formats": []}
            mock_create_context.return_value = MagicMock()

            result = await self.handler._handle_list_creative_formats_skill(parameters={}, auth_token="valid-token")

            assert result is not None
            mock_create_context.assert_called_once()
            mock_tool.assert_called_once()

    @pytest.mark.asyncio
    async def test_list_creative_formats_with_invalid_auth_fails(self):
        """list_creative_formats should fail with invalid token (not fall back to anonymous)."""
        with patch.object(self.handler, "_create_tool_context_from_a2a") as mock_create_context:
            # Simulate invalid token error
            mock_create_context.side_effect = ServerError(InvalidRequestError(message="Invalid authentication token"))

            with pytest.raises(ServerError) as exc_info:
                await self.handler._handle_list_creative_formats_skill(parameters={}, auth_token="invalid-token")

            assert "Invalid authentication token" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_list_authorized_properties_without_auth(self):
        """list_authorized_properties should work without authentication."""
        with patch("src.a2a_server.adcp_a2a_server.core_list_authorized_properties_tool") as mock_tool:
            mock_tool.return_value = {"publisher_domains": []}

            result = await self.handler._handle_list_authorized_properties_skill(parameters={}, auth_token=None)

            assert result is not None
            mock_tool.assert_called_once()

    @pytest.mark.asyncio
    async def test_list_authorized_properties_with_auth(self):
        """list_authorized_properties should work with valid authentication."""
        with (
            patch("src.a2a_server.adcp_a2a_server.core_list_authorized_properties_tool") as mock_tool,
            patch.object(self.handler, "_create_tool_context_from_a2a") as mock_create_context,
        ):
            mock_tool.return_value = {"publisher_domains": []}
            mock_create_context.return_value = MagicMock()

            result = await self.handler._handle_list_authorized_properties_skill(
                parameters={}, auth_token="valid-token"
            )

            assert result is not None
            mock_create_context.assert_called_once()
            mock_tool.assert_called_once()

    @pytest.mark.asyncio
    async def test_list_authorized_properties_with_invalid_auth_fails(self):
        """list_authorized_properties should fail with invalid token."""
        with patch.object(self.handler, "_create_tool_context_from_a2a") as mock_create_context:
            mock_create_context.side_effect = ServerError(InvalidRequestError(message="Invalid authentication token"))

            with pytest.raises(ServerError):
                await self.handler._handle_list_authorized_properties_skill(parameters={}, auth_token="invalid-token")

    @pytest.mark.asyncio
    async def test_get_products_without_auth(self):
        """get_products should work without authentication (depending on policy)."""
        with patch("src.a2a_server.adcp_a2a_server.core_get_products_tool") as mock_tool:
            mock_tool.return_value = {"products": []}

            result = await self.handler._handle_get_products_skill(
                parameters={"brief": "test campaign"}, auth_token=None
            )

            assert result is not None
            mock_tool.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_products_with_auth(self):
        """get_products should work with valid authentication."""
        with (
            patch("src.a2a_server.adcp_a2a_server.core_get_products_tool") as mock_tool,
            patch.object(self.handler, "_create_tool_context_from_a2a") as mock_create_context,
        ):
            mock_tool.return_value = {"products": []}
            mock_create_context.return_value = MagicMock()

            result = await self.handler._handle_get_products_skill(
                parameters={"brief": "test campaign"}, auth_token="valid-token"
            )

            assert result is not None
            mock_create_context.assert_called_once()
            mock_tool.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_products_with_invalid_auth_fails(self):
        """get_products should fail with invalid token (not fall back to anonymous)."""
        with patch.object(self.handler, "_create_tool_context_from_a2a") as mock_create_context:
            mock_create_context.side_effect = ServerError(InvalidRequestError(message="Invalid authentication token"))

            with pytest.raises(ServerError) as exc_info:
                await self.handler._handle_get_products_skill(
                    parameters={"brief": "test campaign"}, auth_token="invalid-token"
                )

            assert "Invalid authentication token" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_create_media_buy_requires_auth(self):
        """create_media_buy should reject None auth_token (not a discovery endpoint)."""
        with pytest.raises(ServerError) as exc_info:
            await self.handler._handle_explicit_skill(
                skill_name="create_media_buy", parameters={"product_ids": ["prod_1"]}, auth_token=None
            )

        assert "Authentication token required" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_update_media_buy_requires_auth(self):
        """update_media_buy should reject None auth_token."""
        with pytest.raises(ServerError) as exc_info:
            await self.handler._handle_explicit_skill(
                skill_name="update_media_buy", parameters={"media_buy_id": "mb_1"}, auth_token=None
            )

        assert "Authentication token required" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_discovery_skills_list(self):
        """Verify discovery_skills set includes only auth-optional endpoints."""
        # This test documents which skills are auth-optional
        # Call _handle_explicit_skill with None auth and verify behavior

        # Discovery skills should accept None auth
        discovery_skills = ["list_creative_formats", "list_authorized_properties", "get_products"]

        for skill_name in discovery_skills:
            # Should not raise auth error (may raise other errors)
            # We just verify that None auth_token doesn't immediately fail
            try:
                with patch(f"src.a2a_server.adcp_a2a_server.core_{skill_name}_tool") as mock_tool:
                    mock_tool.return_value = {}
                    await self.handler._handle_explicit_skill(
                        skill_name=skill_name,
                        parameters={"brief": "test"} if skill_name == "get_products" else {},
                        auth_token=None,
                    )
            except ServerError as e:
                # Should not be an auth error
                assert "Authentication token required" not in str(e)

    @pytest.mark.asyncio
    async def test_natural_language_without_auth(self):
        """Natural language requests (empty skill_invocations) should not require auth.

        With the fix, requires_auth defaults to False, so empty skill_invocations
        (natural language requests) should not trigger authentication requirement.
        """
        from unittest.mock import AsyncMock, MagicMock

        # Mock the MessageSendParams with a text-only message (no explicit skill)
        params = MagicMock()
        params.message = MagicMock()
        params.message.message_id = "test_msg_1"
        params.message.context_id = "test_ctx_1"
        params.message.role = "user"

        # Create a mock part with text attribute that matches a natural language pattern
        text_part = MagicMock()
        text_part.text = "show me available products"
        # Mock hasattr checks
        text_part.data = None
        text_part.root = None
        params.message.parts = [text_part]
        params.configuration = None

        # Mock _get_auth_token to return None (no auth)
        with patch.object(self.handler, "_get_auth_token", return_value=None):
            # Mock the _get_products method that would be called for natural language
            with patch.object(self.handler, "_get_products", new_callable=AsyncMock) as mock_products:
                mock_products.return_value = {"products": []}

                # This should NOT raise auth error for natural language
                # Before fix: would raise "Authentication token required"
                # After fix: should proceed to natural language handling
                try:
                    result = await self.handler.on_message_send(params)
                    # Should successfully return a result (even if empty)
                    assert result is not None
                except ServerError as e:
                    # Only auth errors are problematic - let other errors propagate
                    if "Authentication" in str(e) or "authentication" in str(e):
                        pytest.fail(f"Natural language request without auth should not require auth: {e}")
                    else:
                        # Re-raise non-auth errors to expose actual bugs
                        raise


class TestMinimalContext:
    """Test MinimalContext helper class."""

    def test_minimal_context_creation(self):
        """MinimalContext should initialize with headers."""
        headers = {"Host": "example.com", "x-adcp-tenant": "test"}
        context = MinimalContext(headers)

        assert context.headers == headers
        assert context.meta["headers"] == headers

    def test_minimal_context_from_request_context(self):
        """MinimalContext.from_request_context should use request headers."""
        with patch("src.a2a_server.adcp_a2a_server._request_headers") as mock_headers:
            mock_headers.get.return_value = {"Host": "test.com"}

            context = MinimalContext.from_request_context()

            assert context.headers == {"Host": "test.com"}
            assert context.meta["headers"] == {"Host": "test.com"}

    def test_minimal_context_from_request_context_no_headers(self):
        """MinimalContext.from_request_context should handle missing headers."""
        with patch("src.a2a_server.adcp_a2a_server._request_headers") as mock_headers:
            mock_headers.get.return_value = None

            context = MinimalContext.from_request_context()

            assert context.headers == {}
            assert context.meta["headers"] == {}
