#!/usr/bin/env python3
"""
Test A2A get_products brand parameter handling (adcp 3.6.0).

Unit tests to verify that the A2A server correctly uses brand (not brand_manifest)
when calling the core get_products tool.
"""

import logging
from unittest.mock import MagicMock, patch

import pytest

from src.a2a_server.adcp_a2a_server import AdCPRequestHandler

logger = logging.getLogger(__name__)


@pytest.mark.asyncio
async def test_handle_get_products_skill_passes_brand():
    """Test that _handle_get_products_skill passes brand parameter to core tool."""
    handler = AdCPRequestHandler()

    with (
        patch.object(handler, "_create_tool_context_from_a2a") as mock_create_context,
        patch("src.a2a_server.adcp_a2a_server.core_get_products_tool") as mock_core_tool,
        patch.object(handler, "_resolve_identity") as mock_resolve,
    ):
        mock_create_context.return_value = MagicMock(tenant_id="test_tenant", principal_id="test_principal")
        mock_resolve.return_value = MagicMock()

        mock_response = MagicMock()
        mock_response.model_dump.return_value = {"products": [], "message": "Test products"}
        mock_core_tool.return_value = mock_response

        parameters = {
            "brand": {"domain": "nike.com"},
            "brief": "Athletic footwear",
        }

        await handler._handle_get_products_skill(parameters, "test_token")

        mock_core_tool.assert_called_once()
        call_kwargs = mock_core_tool.call_args.kwargs

        assert "brand" in call_kwargs, "brand should be passed to core tool"
        assert call_kwargs["brand"] == {"domain": "nike.com"}
        assert call_kwargs["brief"] == "Athletic footwear"
        assert "brand_manifest" not in call_kwargs, "brand_manifest must not be passed"


@pytest.mark.asyncio
async def test_handle_get_products_skill_extracts_all_parameters():
    """Test that _handle_get_products_skill extracts all optional parameters."""
    handler = AdCPRequestHandler()

    with (
        patch.object(handler, "_create_tool_context_from_a2a") as mock_create_context,
        patch("src.a2a_server.adcp_a2a_server.core_get_products_tool") as mock_core_tool,
        patch.object(handler, "_resolve_identity") as mock_resolve,
    ):
        mock_create_context.return_value = MagicMock(tenant_id="test_tenant", principal_id="test_principal")
        mock_resolve.return_value = MagicMock()

        mock_response = MagicMock()
        mock_response.model_dump.return_value = {"products": [], "message": "Test products"}
        mock_core_tool.return_value = mock_response

        parameters = {
            "brand": {"domain": "nike.com"},
            "brief": "Athletic footwear",
            "filters": {"delivery_type": "guaranteed"},
            "min_exposures": 10000,
            "adcp_version": "3.6.0",
            "strategy_id": "test_strategy_123",
        }

        await handler._handle_get_products_skill(parameters, "test_token")

        mock_core_tool.assert_called_once()
        call_kwargs = mock_core_tool.call_args.kwargs

        assert call_kwargs["brand"] == {"domain": "nike.com"}
        assert call_kwargs["brief"] == "Athletic footwear"
        assert call_kwargs["filters"] == {"delivery_type": "guaranteed"}
        assert call_kwargs["min_exposures"] == 10000
        assert call_kwargs["strategy_id"] == "test_strategy_123"
        assert "adcp_version" not in call_kwargs
        assert "brand_manifest" not in call_kwargs


@pytest.mark.asyncio
async def test_handle_get_products_skill_brand_manifest_not_converted():
    """Test that brand_manifest is NOT silently converted — brand_manifest is ignored."""
    handler = AdCPRequestHandler()

    with (
        patch.object(handler, "_create_tool_context_from_a2a") as mock_create_context,
        patch("src.a2a_server.adcp_a2a_server.core_get_products_tool") as mock_core_tool,
        patch.object(handler, "_resolve_identity") as mock_resolve,
    ):
        mock_create_context.return_value = MagicMock(tenant_id="test_tenant", principal_id="test_principal")
        mock_resolve.return_value = MagicMock()

        mock_response = MagicMock()
        mock_response.model_dump.return_value = {"products": [], "message": "Test products"}
        mock_core_tool.return_value = mock_response

        # brand_manifest with brief — brief satisfies the "brief OR brand" requirement
        parameters = {
            "brand_manifest": {"name": "Nike Athletic Footwear"},
            "brief": "Display ads",
        }

        await handler._handle_get_products_skill(parameters, "test_token")

        mock_core_tool.assert_called_once()
        call_kwargs = mock_core_tool.call_args.kwargs

        # brand_manifest is ignored, brand is None
        assert call_kwargs["brand"] is None
        assert call_kwargs["brief"] == "Display ads"
        assert "brand_manifest" not in call_kwargs


@pytest.mark.asyncio
async def test_handle_get_products_skill_no_brief_no_brand_raises():
    """Test that empty parameters (no brief, no brand) raises an error."""
    handler = AdCPRequestHandler()

    with (
        patch.object(handler, "_create_tool_context_from_a2a") as mock_create_context,
        patch("src.a2a_server.adcp_a2a_server.core_get_products_tool") as mock_core_tool,
        patch.object(handler, "_resolve_identity") as mock_resolve,
    ):
        mock_create_context.return_value = MagicMock(tenant_id="test_tenant", principal_id="test_principal")
        mock_resolve.return_value = MagicMock()
        mock_core_tool.return_value = MagicMock()

        from a2a.utils.errors import ServerError

        with pytest.raises(ServerError):
            await handler._handle_get_products_skill({}, "test_token")
