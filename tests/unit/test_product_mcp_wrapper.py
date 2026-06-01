"""Unit tests for get_products MCP transport wrapper.

Covers ValidationError/ValueError handling and ToolResult construction.
Version compat lives at the transport handler level (parity with A2A).

These test the transport boundary layer, NOT business logic.
_get_products_impl is always mocked.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from src.core.exceptions import AdCPValidationError


class TestGetProductsMCPWrapper:
    """Test the MCP get_products() wrapper function."""

    @pytest.mark.asyncio
    async def test_validation_error_raises_adcp_validation_error(self):
        """ValidationError from create_get_products_request → AdCPValidationError."""
        with patch(
            "src.core.tools.products.create_get_products_request",
            side_effect=ValidationError.from_exception_data(
                title="GetProductsRequest",
                line_errors=[
                    {
                        "type": "missing",
                        "loc": ("brief",),
                        "msg": "Field required",
                        "input": {},
                    }
                ],
            ),
        ):
            from src.core.tools.products import get_products

            with pytest.raises(AdCPValidationError):
                await get_products(brief="test", ctx=None)

    @pytest.mark.asyncio
    async def test_value_error_raises_adcp_validation_error(self):
        """ValueError from create_get_products_request → AdCPValidationError."""
        with patch(
            "src.core.tools.products.create_get_products_request",
            side_effect=ValueError("invalid filter combination"),
        ):
            from src.core.tools.products import get_products

            with pytest.raises(AdCPValidationError, match="Invalid get_products request"):
                await get_products(brief="test", ctx=None)

    @pytest.mark.asyncio
    async def test_returns_tool_result_with_structured_content(self):
        """Happy path: returns ToolResult with structured_content from response."""
        mock_response = MagicMock()
        mock_response.model_dump.return_value = {"products": [], "metadata": {}}
        mock_response.__str__ = lambda self: "0 products found"

        mock_req = MagicMock()

        with (
            patch("src.core.tools.products.create_get_products_request", return_value=mock_req),
            patch(
                "src.core.tools.products._get_products_impl",
                new_callable=AsyncMock,
                return_value=mock_response,
            ),
        ):
            from src.core.tools.products import get_products

            result = await get_products(brief="video ads", ctx=None)

        assert result.structured_content == {"products": [], "metadata": {}}
        assert "0 products found" in str(result.content)

    @pytest.mark.asyncio
    async def test_wrapper_does_not_apply_version_compat(self):
        """Wrapper does NOT apply version compat — that's the transport handler's job."""
        mock_response = MagicMock()
        mock_response.model_dump.return_value = {"products": []}
        mock_response.__str__ = lambda self: "result"

        mock_req = MagicMock()

        with (
            patch("src.core.tools.products.create_get_products_request", return_value=mock_req),
            patch(
                "src.core.tools.products._get_products_impl",
                new_callable=AsyncMock,
                return_value=mock_response,
            ),
            patch("src.core.version_compat.apply_version_compat") as mock_compat,
        ):
            from src.core.tools.products import get_products

            await get_products(brief="test", ctx=None)

        mock_compat.assert_not_called()
