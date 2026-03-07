#!/usr/bin/env python3
"""
Unit tests for A2A auth-optional discovery endpoints.

Tests that discovery endpoints (list_creative_formats, list_authorized_properties, get_products)
properly handle both authenticated and unauthenticated requests according to AdCP spec.

After the identity-at-transport-boundary refactor (salesagent-anjp), handlers receive
a pre-resolved identity parameter rather than resolving auth internally.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from a2a.utils.errors import ServerError

from src.a2a_server.adcp_a2a_server import AdCPRequestHandler
from src.core.resolved_identity import ResolvedIdentity


class TestAuthOptionalSkills:
    """Test auth-optional skill handling in A2A server."""

    def setup_method(self):
        """Set up test fixtures."""
        self.handler = AdCPRequestHandler()
        self.mock_identity = ResolvedIdentity(
            principal_id="test_principal", tenant_id="default", tenant={"tenant_id": "default"}, protocol="a2a"
        )
        self.anon_identity = ResolvedIdentity(
            principal_id=None, tenant_id="default", tenant={"tenant_id": "default"}, protocol="a2a"
        )

    @pytest.mark.asyncio
    async def test_list_creative_formats_without_auth(self):
        """list_creative_formats should work with anonymous identity (no principal)."""
        with patch("src.a2a_server.adcp_a2a_server.core_list_creative_formats_tool") as mock_tool:
            mock_tool.return_value = {"formats": []}

            result = await self.handler._handle_list_creative_formats_skill(parameters={}, identity=self.anon_identity)

            assert result is not None
            assert "formats" in result
            mock_tool.assert_called_once()

    @pytest.mark.asyncio
    async def test_list_creative_formats_with_auth(self):
        """list_creative_formats should work with authenticated identity."""
        with patch("src.a2a_server.adcp_a2a_server.core_list_creative_formats_tool") as mock_tool:
            mock_tool.return_value = {"formats": []}

            result = await self.handler._handle_list_creative_formats_skill(parameters={}, identity=self.mock_identity)

            assert result is not None
            mock_tool.assert_called_once()

    @pytest.mark.asyncio
    async def test_list_authorized_properties_without_auth(self):
        """list_authorized_properties should work with anonymous identity."""
        with patch("src.a2a_server.adcp_a2a_server.core_list_authorized_properties_tool") as mock_tool:
            mock_tool.return_value = {"publisher_domains": []}

            result = await self.handler._handle_list_authorized_properties_skill(
                parameters={}, identity=self.anon_identity
            )

            assert result is not None
            mock_tool.assert_called_once()

    @pytest.mark.asyncio
    async def test_list_authorized_properties_with_auth(self):
        """list_authorized_properties should work with authenticated identity."""
        with patch("src.a2a_server.adcp_a2a_server.core_list_authorized_properties_tool") as mock_tool:
            mock_tool.return_value = {"publisher_domains": []}

            result = await self.handler._handle_list_authorized_properties_skill(
                parameters={}, identity=self.mock_identity
            )

            assert result is not None
            mock_tool.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_products_without_auth(self):
        """get_products should work with anonymous identity."""
        with patch("src.a2a_server.adcp_a2a_server.core_get_products_tool") as mock_tool:
            mock_tool.return_value = {"products": []}

            result = await self.handler._handle_get_products_skill(
                parameters={"brief": "test campaign"}, identity=self.anon_identity
            )

            assert result is not None
            mock_tool.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_products_with_auth(self):
        """get_products should work with authenticated identity."""
        with patch("src.a2a_server.adcp_a2a_server.core_get_products_tool") as mock_tool:
            mock_tool.return_value = {"products": []}

            result = await self.handler._handle_get_products_skill(
                parameters={"brief": "test campaign"}, identity=self.mock_identity
            )

            assert result is not None
            mock_tool.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_media_buy_requires_auth(self):
        """create_media_buy should reject None identity (not a discovery endpoint)."""
        with pytest.raises(ServerError) as exc_info:
            await self.handler._handle_explicit_skill(
                skill_name="create_media_buy", parameters={"product_ids": ["prod_1"]}, identity=None
            )

        assert "Authentication required" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_update_media_buy_requires_auth(self):
        """update_media_buy should reject None identity."""
        with pytest.raises(ServerError) as exc_info:
            await self.handler._handle_explicit_skill(
                skill_name="update_media_buy", parameters={"media_buy_id": "mb_1"}, identity=None
            )

        assert "Authentication required" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_discovery_skills_accept_anonymous_identity(self):
        """Discovery skills should accept anonymous identity (no principal_id)."""
        discovery_skills = {
            "list_creative_formats": "src.a2a_server.adcp_a2a_server.core_list_creative_formats_tool",
            "list_authorized_properties": "src.a2a_server.adcp_a2a_server.core_list_authorized_properties_tool",
            "get_products": "src.a2a_server.adcp_a2a_server.core_get_products_tool",
        }

        for skill_name, mock_path in discovery_skills.items():
            with patch(mock_path) as mock_tool:
                mock_tool.return_value = {}
                try:
                    await self.handler._handle_explicit_skill(
                        skill_name=skill_name,
                        parameters={"brief": "test"} if skill_name == "get_products" else {},
                        identity=self.anon_identity,
                    )
                except ServerError as e:
                    assert "Authentication required" not in str(e)

    @pytest.mark.asyncio
    async def test_natural_language_without_auth(self):
        """Natural language requests (empty skill_invocations) should not require auth.

        With the identity-at-transport-boundary refactor, on_message_send resolves
        identity at the transport boundary. NL requests with no auth get
        requires_auth=False, so identity resolution succeeds with anonymous identity.
        """
        # Mock the MessageSendParams with a text-only message (no explicit skill)
        params = MagicMock()
        params.message = MagicMock()
        params.message.message_id = "test_msg_1"
        params.message.context_id = "test_ctx_1"
        params.message.role = "user"

        # Create a mock part with text attribute that matches a natural language pattern
        text_part = MagicMock()
        text_part.text = "show me available products"
        text_part.data = None
        text_part.root = None
        params.message.parts = [text_part]
        params.configuration = None

        # Mock _get_auth_token to return None (no auth)
        with patch.object(self.handler, "_get_auth_token", return_value=None):
            # Mock _resolve_a2a_identity to return anonymous identity
            with patch.object(self.handler, "_resolve_a2a_identity", return_value=self.anon_identity):
                # Mock the _get_products method that would be called for natural language
                with patch.object(self.handler, "_get_products", new_callable=AsyncMock) as mock_products:
                    mock_products.return_value = {"products": []}

                    try:
                        result = await self.handler.on_message_send(params)
                        assert result is not None
                    except ServerError as e:
                        if "Authentication" in str(e) or "authentication" in str(e):
                            pytest.fail(f"Natural language request without auth should not require auth: {e}")
                        else:
                            raise
