"""Unit tests for RequestCompatMiddleware (FastMCP on_call_tool).

Tests that the middleware calls normalize_request_params and replaces
the context message when translations are applied.
"""

from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest

from src.core.mcp_compat_middleware import RequestCompatMiddleware
from src.core.request_compat import NormalizationResult


@pytest.fixture()
def middleware():
    return RequestCompatMiddleware()


def _make_context(tool_name: str, arguments: dict | None):
    """Build a mock MiddlewareContext with .message and .copy()."""
    message = MagicMock()
    message.name = tool_name
    message.arguments = arguments

    ctx = MagicMock()
    ctx.message = message
    # .copy() should return a new context with the replaced message
    ctx.copy = MagicMock(side_effect=lambda **kw: _make_copied_context(ctx, **kw))
    return ctx


def _make_copied_context(original, **kwargs):
    """Simulate MiddlewareContext.copy(message=...)."""
    copied = MagicMock()
    copied.message = kwargs.get("message", original.message)
    copied.copy = original.copy
    return copied


class TestMiddlewareCallsNormalizer:
    """Middleware delegates to normalize_request_params."""

    @pytest.mark.asyncio
    async def test_normalizer_called_with_tool_name_and_args(self, middleware):
        ctx = _make_context("get_products", {"brand_manifest": "https://acme.com/brand", "brief": "ads"})
        call_next = AsyncMock()

        with patch("src.core.mcp_compat_middleware.normalize_request_params") as mock_norm:
            mock_norm.return_value = NormalizationResult(
                params={"brand": {"domain": "acme.com"}, "brief": "ads"},
                translations_applied=["brand_manifest → brand"],
            )
            await middleware.on_call_tool(ctx, call_next)

            mock_norm.assert_called_once_with(
                "get_products", {"brand_manifest": "https://acme.com/brand", "brief": "ads"}
            )


class TestMiddlewareReplacesContext:
    """When translations applied, context.copy(message=...) creates new context."""

    @pytest.mark.asyncio
    async def test_context_replaced_when_translations_applied(self, middleware):
        ctx = _make_context("get_products", {"brand_manifest": "https://acme.com/brand"})
        captured_ctx = None

        async def capturing_call_next(context):
            nonlocal captured_ctx
            captured_ctx = context

        with patch("src.core.mcp_compat_middleware.normalize_request_params") as mock_norm:
            mock_norm.return_value = NormalizationResult(
                params={"brand": {"domain": "acme.com"}},
                translations_applied=["brand_manifest → brand"],
            )
            await middleware.on_call_tool(ctx, capturing_call_next)

            # context.copy was called with a new message
            ctx.copy.assert_called_once_with(message=ANY)
            # call_next received the copied context with normalized arguments
            assert captured_ctx is not None
            assert captured_ctx is not ctx
            assert captured_ctx.message.arguments == {"brand": {"domain": "acme.com"}}


class TestMiddlewarePassthrough:
    """When no translations, original context passes through unchanged."""

    @pytest.mark.asyncio
    async def test_no_translations_no_copy(self, middleware):
        ctx = _make_context("get_products", {"brand": {"domain": "acme.com"}, "brief": "ads"})
        call_next = AsyncMock()

        with patch("src.core.mcp_compat_middleware.normalize_request_params") as mock_norm:
            mock_norm.return_value = NormalizationResult(
                params={"brand": {"domain": "acme.com"}, "brief": "ads"},
                translations_applied=[],
            )
            await middleware.on_call_tool(ctx, call_next)

            ctx.copy.assert_not_called()
            call_next.assert_called_once_with(ctx)


class TestMiddlewareEdgeCases:
    """Edge cases: None arguments, empty arguments."""

    @pytest.mark.asyncio
    async def test_none_arguments_passthrough(self, middleware):
        ctx = _make_context("get_products", None)
        call_next = AsyncMock()

        await middleware.on_call_tool(ctx, call_next)
        call_next.assert_called_once_with(ctx)

    @pytest.mark.asyncio
    async def test_empty_arguments_passthrough(self, middleware):
        ctx = _make_context("get_products", {})
        call_next = AsyncMock()

        with patch("src.core.mcp_compat_middleware.normalize_request_params") as mock_norm:
            mock_norm.return_value = NormalizationResult(params={}, translations_applied=[])
            await middleware.on_call_tool(ctx, call_next)

            call_next.assert_called_once_with(ctx)
