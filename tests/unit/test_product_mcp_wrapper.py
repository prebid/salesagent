"""Unit tests for get_products MCP transport wrapper.

Covers invalid-request translation and ToolResult construction. The shared
create_get_products_request helper owns the ValidationError -> AdCPInvalidRequestError
translation, and the MCP wrapper applies outbound v2-compat for pre-v3 buyers (who
reach the seller over MCP).

These test the transport boundary layer, NOT business logic.
_get_products_impl is always mocked.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.exceptions import AdCPInvalidRequestError
from src.core.schema_helpers import GetProductsRequestBuild


class TestGetProductsMCPWrapper:
    """Test the MCP get_products() wrapper function."""

    @pytest.mark.asyncio
    async def test_invalid_request_raises_adcp_invalid_request_error(self):
        """The helper owns the schema-validation translation, so a cross-mode violation
        surfaces at the wrapper as AdCPInvalidRequestError (INVALID_REQUEST) with no
        wrapper-level try/except. brief mode without a brief rejects before _impl."""
        from src.core.tools.products import get_products

        with pytest.raises(AdCPInvalidRequestError):
            await get_products(brief="", buying_mode="brief", ctx=None)

    @pytest.mark.asyncio
    async def test_returns_tool_result_with_structured_content(self):
        """Happy path: returns ToolResult with structured_content from the response."""
        mock_response = MagicMock()
        mock_response.model_dump.return_value = {"products": [], "metadata": {}}
        mock_response.__str__ = lambda self: "0 products found"

        mock_req = MagicMock()

        with (
            patch(
                "src.core.tools.products.create_get_products_request",
                return_value=GetProductsRequestBuild(mock_req, False),
            ),
            patch(
                "src.core.tools.products._get_products_impl",
                new_callable=AsyncMock,
                return_value=mock_response,
            ),
        ):
            from src.core.tools.products import get_products

            # adcp_version='3.0.0' → v2-compat is a no-op, so structured == model_dump().
            result = await get_products(brief="video ads", adcp_version="3.0.0", ctx=None)

        assert result.structured_content == {"products": [], "metadata": {}}
        assert "0 products found" in str(result.content)

    @pytest.mark.asyncio
    async def test_wrapper_applies_version_compat_for_pre_v3(self):
        """The MCP wrapper applies outbound v2-compat for pre-v3 buyers — the transform
        runs here because pre-v3 clients reach the seller over MCP."""
        mock_response = MagicMock()
        mock_response.__str__ = lambda self: "result"

        mock_req = MagicMock()

        with (
            patch(
                "src.core.tools.products.create_get_products_request",
                return_value=GetProductsRequestBuild(mock_req, False),
            ),
            patch(
                "src.core.tools.products._get_products_impl",
                new_callable=AsyncMock,
                return_value=mock_response,
            ),
            patch("src.core.tools.products.apply_version_compat", return_value={"products": []}) as mock_compat,
        ):
            from src.core.tools.products import get_products

            result = await get_products(brief="test", adcp_version="1.0.0", ctx=None)

        mock_compat.assert_called_once_with("get_products", mock_response, "1.0.0")
        assert result.structured_content == {"products": []}
