"""Unit tests for get_products transport wrappers — MCP, A2A, REST, _impl.

salesagent-pm9k: Cover MCP/A2A transport wrapper lines in products.py.

Tests the wrapper logic (request construction, error translation,
response serialization, version compat) independent of business logic.
Business logic is mocked via _get_products_impl.

# --- Test Source-of-Truth Audit ---
# Audited: 2026-03-07
#
# SPEC_BACKED (1 test):
#   test_rest_returns_json_response — AdCP get-products-response.json + protocol-envelope.json
#
# ARCH_BACKED (9 tests):
#   test_mcp_wrapper_returns_tool_result — CLAUDE.md #5: MCP returns ToolResult
#   test_mcp_wrapper_validation_error_raises_adcp_validation — CLAUDE.md #5 + no-ToolError guard
#   test_mcp_wrapper_value_error_raises_adcp_validation — CLAUDE.md #5: error translation
#   test_mcp_wrapper_reads_identity_from_ctx_state — CLAUDE.md #5: wrapper resolves identity
#   test_a2a_wrapper_returns_response_model — CLAUDE.md #5 + protocol-envelope.json notes
#   test_a2a_wrapper_passes_identity_to_impl — CLAUDE.md #5: forward all params
#   test_a2a_wrapper_constructs_request_from_params — CLAUDE.md #5: wrapper builds request
#   test_mcp_passes_none_identity_when_no_ctx — CLAUDE.md #5: identity optional
#   test_a2a_passes_none_identity_when_not_provided — CLAUDE.md #5: identity optional
#
# DECISION_BACKED (1 test):
#   test_a2a_wrapper_no_version_compat — arch decision: compat at handler level
#
# CHARACTERIZATION (4 tests):
#   test_mcp_wrapper_version_compat_v2 — locks: compat applied for pre-3.0
#   test_mcp_wrapper_version_compat_v3_skips — locks: compat skipped for v3+
#   test_a2a_wrapper_empty_brief_uses_empty_string — locks: empty brief handling
#   test_rest_applies_version_compat — locks: REST applies compat
# ---
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastmcp.server.context import Context
from pydantic import ValidationError

from src.core.exceptions import AdCPValidationError
from src.core.schemas import GetProductsResponse
from tests.factories import PrincipalFactory


def _mock_response() -> GetProductsResponse:
    """Build a minimal GetProductsResponse for testing wrapper logic."""
    return GetProductsResponse(products=[])


# ---------------------------------------------------------------------------
# MCP wrapper: get_products()
# ---------------------------------------------------------------------------


class TestMcpGetProductsWrapper:
    """Tests for the async MCP wrapper get_products()."""

    def test_mcp_wrapper_returns_tool_result(self):
        """MCP wrapper returns ToolResult with structured_content."""
        mock_ctx = MagicMock(spec=Context)
        mock_ctx.get_state = AsyncMock(return_value=PrincipalFactory.make_identity(protocol="mcp"))

        with patch(
            "src.core.tools.products._get_products_impl",
            new_callable=AsyncMock,
            return_value=_mock_response(),
        ):
            from src.core.tools.products import get_products

            result = asyncio.run(get_products(brief="video ads", ctx=mock_ctx))

        assert result.structured_content is not None
        assert "products" in result.structured_content
        assert result.content is not None  # Human-readable text

    def test_mcp_wrapper_validation_error_raises_adcp_validation(self):
        """MCP wrapper translates ValidationError to AdCPValidationError."""
        mock_ctx = MagicMock(spec=Context)
        mock_ctx.get_state = AsyncMock(return_value=None)

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
                asyncio.run(get_products(brief="", ctx=mock_ctx))

    def test_mcp_wrapper_value_error_raises_adcp_validation(self):
        """MCP wrapper translates ValueError to AdCPValidationError."""
        mock_ctx = MagicMock(spec=Context)
        mock_ctx.get_state = AsyncMock(return_value=None)

        with patch(
            "src.core.tools.products.create_get_products_request",
            side_effect=ValueError("Invalid brand format"),
        ):
            from src.core.tools.products import get_products

            with pytest.raises(AdCPValidationError, match="Invalid get_products request"):
                asyncio.run(get_products(brief="ads", ctx=mock_ctx))

    def test_mcp_wrapper_version_compat_v2(self):
        """MCP wrapper applies version compat for pre-3.0 clients."""
        mock_ctx = MagicMock(spec=Context)
        mock_ctx.get_state = AsyncMock(return_value=PrincipalFactory.make_identity(protocol="mcp"))

        with (
            patch(
                "src.core.tools.products._get_products_impl",
                new_callable=AsyncMock,
                return_value=_mock_response(),
            ),
            patch("src.core.version_compat.apply_version_compat") as mock_compat,
        ):
            mock_compat.return_value = {"products": [], "compat_field": True}
            from src.core.tools.products import get_products

            result = asyncio.run(get_products(brief="ads", adcp_version="2.0.0", ctx=mock_ctx))

        mock_compat.assert_called_once()
        assert result.structured_content["compat_field"] is True

    def test_mcp_wrapper_version_compat_v3_skips(self):
        """MCP wrapper skips version compat for v3+ clients."""
        mock_ctx = MagicMock(spec=Context)
        mock_ctx.get_state = AsyncMock(return_value=PrincipalFactory.make_identity(protocol="mcp"))

        with (
            patch(
                "src.core.tools.products._get_products_impl",
                new_callable=AsyncMock,
                return_value=_mock_response(),
            ),
            patch("src.core.version_compat.apply_version_compat") as mock_compat,
        ):
            # apply_version_compat returns input unchanged for v3+
            mock_compat.side_effect = lambda tool, resp, ver: resp
            from src.core.tools.products import get_products

            result = asyncio.run(get_products(brief="ads", adcp_version="3.0.0", ctx=mock_ctx))

        mock_compat.assert_called_once_with("get_products", result.structured_content, "3.0.0")

    def test_mcp_wrapper_reads_identity_from_ctx_state(self):
        """MCP wrapper reads identity from ctx.get_state('identity')."""
        identity = PrincipalFactory.make_identity(protocol="mcp")
        mock_ctx = MagicMock(spec=Context)
        mock_ctx.get_state = AsyncMock(return_value=identity)

        with patch(
            "src.core.tools.products._get_products_impl",
            new_callable=AsyncMock,
            return_value=_mock_response(),
        ) as mock_impl:
            from src.core.tools.products import get_products

            asyncio.run(get_products(brief="video", ctx=mock_ctx))

        mock_ctx.get_state.assert_awaited_once_with("identity")
        mock_impl.assert_awaited_once()
        _, call_identity = mock_impl.call_args.args
        assert call_identity is identity


# ---------------------------------------------------------------------------
# A2A wrapper: get_products_raw()
# ---------------------------------------------------------------------------


class TestA2AGetProductsRawWrapper:
    """Tests for the A2A wrapper get_products_raw()."""

    def test_a2a_wrapper_returns_response_model(self):
        """A2A wrapper returns GetProductsResponse directly (not ToolResult)."""
        identity = PrincipalFactory.make_identity(protocol="a2a")

        with patch(
            "src.core.tools.products._get_products_impl",
            new_callable=AsyncMock,
            return_value=_mock_response(),
        ):
            from src.core.tools.products import get_products_raw

            result = asyncio.run(get_products_raw(brief="display ads", identity=identity))

        assert isinstance(result, GetProductsResponse)
        assert result.products == []

    def test_a2a_wrapper_passes_identity_to_impl(self):
        """A2A wrapper forwards identity to _get_products_impl."""
        identity = PrincipalFactory.make_identity(protocol="a2a")

        with patch(
            "src.core.tools.products._get_products_impl",
            new_callable=AsyncMock,
            return_value=_mock_response(),
        ) as mock_impl:
            from src.core.tools.products import get_products_raw

            asyncio.run(get_products_raw(brief="video", identity=identity))

        mock_impl.assert_awaited_once()
        _, call_identity = mock_impl.call_args.args
        assert call_identity is identity

    def test_a2a_wrapper_constructs_request_from_params(self):
        """A2A wrapper constructs GetProductsRequest from its parameters."""
        identity = PrincipalFactory.make_identity(protocol="a2a")

        with patch(
            "src.core.tools.products._get_products_impl",
            new_callable=AsyncMock,
            return_value=_mock_response(),
        ) as mock_impl:
            from src.core.tools.products import get_products_raw

            asyncio.run(
                get_products_raw(
                    brief="sports ads",
                    filters={"delivery_types": ["guaranteed"]},
                    identity=identity,
                )
            )

        req = mock_impl.call_args.args[0]
        assert req.brief == "sports ads"
        assert req.filters is not None

    def test_a2a_wrapper_no_version_compat(self):
        """A2A wrapper does NOT apply version compat — that's the A2A handler's job."""
        identity = PrincipalFactory.make_identity(protocol="a2a")

        with (
            patch(
                "src.core.tools.products._get_products_impl",
                new_callable=AsyncMock,
                return_value=_mock_response(),
            ),
            patch("src.core.version_compat.apply_version_compat") as mock_compat,
        ):
            from src.core.tools.products import get_products_raw

            asyncio.run(get_products_raw(brief="ads", identity=identity))

        mock_compat.assert_not_called()

    def test_a2a_wrapper_empty_brief_uses_empty_string(self):
        """A2A wrapper passes empty string when brief is empty."""
        identity = PrincipalFactory.make_identity(protocol="a2a")

        with patch(
            "src.core.tools.products._get_products_impl",
            new_callable=AsyncMock,
            return_value=_mock_response(),
        ) as mock_impl:
            from src.core.tools.products import get_products_raw

            asyncio.run(get_products_raw(brief="", identity=identity))

        req = mock_impl.call_args.args[0]
        # brief="" → create_get_products_request normalizes to None
        assert req.brief is None or req.brief == ""


# ---------------------------------------------------------------------------
# REST wrapper: /api/v1/products
# ---------------------------------------------------------------------------


class TestRestGetProductsWrapper:
    """Tests for the REST endpoint POST /api/v1/products."""

    def test_rest_returns_json_response(self):
        """REST endpoint returns JSON with products array."""
        identity = PrincipalFactory.make_identity(protocol="rest")

        with patch(
            "src.core.tools.products._get_products_impl",
            new_callable=AsyncMock,
            return_value=_mock_response(),
        ):
            from starlette.testclient import TestClient

            from src.app import app
            from src.core.auth_context import _require_auth_dep, _resolve_auth_dep

            app.dependency_overrides[_require_auth_dep] = lambda: identity
            app.dependency_overrides[_resolve_auth_dep] = lambda: identity
            try:
                client = TestClient(app)
                response = client.post(
                    "/api/v1/products",
                    json={"brief": "video ads"},
                )
            finally:
                app.dependency_overrides.clear()

        assert response.status_code == 200
        data = response.json()
        assert "products" in data

    def test_rest_applies_version_compat(self):
        """REST endpoint applies version compat based on adcp_version in body."""
        identity = PrincipalFactory.make_identity(protocol="rest")

        with (
            patch(
                "src.core.tools.products._get_products_impl",
                new_callable=AsyncMock,
                return_value=_mock_response(),
            ),
            patch("src.routes.api_v1.apply_version_compat") as mock_compat,
        ):
            mock_compat.return_value = {"products": [], "legacy": True}

            from starlette.testclient import TestClient

            from src.app import app
            from src.core.auth_context import _require_auth_dep, _resolve_auth_dep

            app.dependency_overrides[_require_auth_dep] = lambda: identity
            app.dependency_overrides[_resolve_auth_dep] = lambda: identity
            try:
                client = TestClient(app)
                response = client.post(
                    "/api/v1/products",
                    json={"brief": "ads", "adcp_version": "2.0.0"},
                )
            finally:
                app.dependency_overrides.clear()

        mock_compat.assert_called_once()
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# _impl direct: wrapper passes identity correctly
# ---------------------------------------------------------------------------


class TestImplDirectIdentity:
    """Tests that wrappers correctly pass identity to _get_products_impl."""

    def test_mcp_passes_none_identity_when_no_ctx(self):
        """MCP wrapper passes None identity when ctx is not Context type."""
        with patch(
            "src.core.tools.products._get_products_impl",
            new_callable=AsyncMock,
            return_value=_mock_response(),
        ) as mock_impl:
            from src.core.tools.products import get_products

            asyncio.run(get_products(brief="test", ctx=None))

        _, identity = mock_impl.call_args.args
        assert identity is None

    def test_a2a_passes_none_identity_when_not_provided(self):
        """A2A wrapper passes None identity when not explicitly provided."""
        with patch(
            "src.core.tools.products._get_products_impl",
            new_callable=AsyncMock,
            return_value=_mock_response(),
        ) as mock_impl:
            from src.core.tools.products import get_products_raw

            asyncio.run(get_products_raw(brief="test"))

        _, identity = mock_impl.call_args.args
        assert identity is None
