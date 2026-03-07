#!/usr/bin/env python3
"""
Test A2A get_products brand parameter handling (adcp 3.6.0).

Unit tests to verify that the A2A server correctly uses brand (not brand_manifest)
when calling the core get_products tool.

After the identity-at-transport-boundary refactor, handlers
receive a pre-resolved identity parameter.
"""

import logging
from unittest.mock import MagicMock, patch

import pytest

from src.a2a_server.adcp_a2a_server import AdCPRequestHandler
from src.core.resolved_identity import ResolvedIdentity

logger = logging.getLogger(__name__)

_MOCK_IDENTITY = ResolvedIdentity(
    principal_id="test_principal", tenant_id="test_tenant", tenant={"tenant_id": "test_tenant"}, protocol="a2a"
)


@pytest.mark.asyncio
async def test_handle_get_products_skill_passes_brand():
    """Test that _handle_get_products_skill passes brand parameter to core tool."""
    handler = AdCPRequestHandler()

    with patch("src.a2a_server.adcp_a2a_server.core_get_products_tool") as mock_core_tool:
        mock_response = MagicMock()
        mock_response.model_dump.return_value = {"products": [], "message": "Test products"}
        mock_core_tool.return_value = mock_response

        parameters = {
            "brand": {"domain": "nike.com"},
            "brief": "Athletic footwear",
        }

        await handler._handle_get_products_skill(parameters, _MOCK_IDENTITY)

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

    with patch("src.a2a_server.adcp_a2a_server.core_get_products_tool") as mock_core_tool:
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

        await handler._handle_get_products_skill(parameters, _MOCK_IDENTITY)

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
async def test_handle_get_products_skill_forwards_property_list():
    """Test that _handle_get_products_skill forwards property_list to core tool.

    Regression test for salesagent-bosc: A2A handler was silently dropping
    property_list while MCP and get_products_raw both forwarded it correctly.
    """
    handler = AdCPRequestHandler()

    with patch("src.a2a_server.adcp_a2a_server.core_get_products_tool") as mock_core_tool:
        mock_response = MagicMock()
        mock_response.model_dump.return_value = {"products": [], "message": "Test products"}
        mock_core_tool.return_value = mock_response

        parameters = {
            "brief": "Video ads",
            "property_list": {"agent_url": "https://buyer.example.com/properties"},
        }

        await handler._handle_get_products_skill(parameters, _MOCK_IDENTITY)

        mock_core_tool.assert_called_once()
        call_kwargs = mock_core_tool.call_args.kwargs

        assert "property_list" in call_kwargs, "property_list should be forwarded to core tool"
        assert call_kwargs["property_list"] == {"agent_url": "https://buyer.example.com/properties"}


@pytest.mark.asyncio
async def test_handle_get_products_skill_brand_manifest_not_converted():
    """Test that brand_manifest is NOT silently converted — brand_manifest is ignored."""
    handler = AdCPRequestHandler()

    with patch("src.a2a_server.adcp_a2a_server.core_get_products_tool") as mock_core_tool:
        mock_response = MagicMock()
        mock_response.model_dump.return_value = {"products": [], "message": "Test products"}
        mock_core_tool.return_value = mock_response

        # brand_manifest with brief — brief satisfies the "brief OR brand" requirement
        parameters = {
            "brand_manifest": {"name": "Nike Athletic Footwear"},
            "brief": "Display ads",
        }

        await handler._handle_get_products_skill(parameters, _MOCK_IDENTITY)

        mock_core_tool.assert_called_once()
        call_kwargs = mock_core_tool.call_args.kwargs

        # brand_manifest is ignored, brand is None
        assert call_kwargs["brand"] is None
        assert call_kwargs["brief"] == "Display ads"
        assert "brand_manifest" not in call_kwargs


@pytest.mark.asyncio
async def test_handle_get_products_skill_no_brief_no_brand_raises():
    """Test that AdCPValidationError from _impl propagates through the handler."""
    handler = AdCPRequestHandler()

    with patch("src.a2a_server.adcp_a2a_server.core_get_products_tool") as mock_core_tool:
        from src.core.exceptions import AdCPValidationError

        mock_core_tool.side_effect = AdCPValidationError("At least one of 'brief', 'brand', or 'filters' is required")

        # AdCPError propagates via 'except AdCPError: raise' to outer handler
        with pytest.raises(AdCPValidationError):
            await handler._handle_get_products_skill({}, _MOCK_IDENTITY)
