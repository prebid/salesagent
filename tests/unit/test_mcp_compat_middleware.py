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


class TestMiddlewareRejectsUnsupportedMajor:
    """Unsupported adcp_major_version is rejected before dispatch (#1512 Tier 2)."""

    @pytest.mark.asyncio
    async def test_unsupported_major_raises_version_unsupported_envelope(self, middleware):
        import json

        from src.core.tool_error_logging import AdCPToolError

        ctx = _make_context("get_products", {"brief": "ads", "adcp_major_version": 99})
        call_next = AsyncMock()

        # Middleware translates the AdCPError to the wire envelope (VERSION_UNSUPPORTED),
        # the same shape the tool wrapper emits — not a bare AdCPError.
        with pytest.raises(AdCPToolError) as exc:
            await middleware.on_call_tool(ctx, call_next)
        assert "VERSION_UNSUPPORTED" in json.dumps(exc.value.envelope)
        call_next.assert_not_called()

    @pytest.mark.asyncio
    async def test_supported_major_dispatches(self, middleware):
        from src.core.adcp_version import adcp_major_version

        ctx = _make_context("get_products", {"brief": "ads", "adcp_major_version": adcp_major_version()})
        captured_ctx = None

        async def capturing_call_next(context):
            nonlocal captured_ctx
            captured_ctx = context

        await middleware.on_call_tool(ctx, capturing_call_next)
        # dispatched, and the negotiation field was stripped on the way through
        assert captured_ctx is not None
        assert "adcp_major_version" not in captured_ctx.message.arguments


class TestMiddlewareDropsUndeclaredEnvelopeFields:
    """context/ext/push_notification_config are stripped when the tool doesn't
    declare them, so a conformant client's envelope doesn't trip validation (#1512)."""

    @pytest.mark.asyncio
    async def test_context_stripped_when_tool_does_not_declare_it(self, middleware):
        ctx = _make_context("get_adcp_capabilities", {"context": {"correlation_id": "c1"}})
        captured_ctx = None

        async def capturing_call_next(context):
            nonlocal captured_ctx
            captured_ctx = context

        # get_adcp_capabilities declares only `protocols` — not the AdCP `context` field.
        with patch.object(middleware, "_get_known_params", AsyncMock(return_value={"protocols"})):
            with patch("src.core.config.is_production", return_value=False):
                await middleware.on_call_tool(ctx, capturing_call_next)

        assert captured_ctx is not None
        assert "context" not in captured_ctx.message.arguments

    @pytest.mark.asyncio
    async def test_context_kept_when_tool_declares_it(self, middleware):
        ctx = _make_context("get_products", {"brief": "ads", "context": {"correlation_id": "c1"}})
        captured_ctx = None

        async def capturing_call_next(context):
            nonlocal captured_ctx
            captured_ctx = context

        # get_products declares `context` — it must reach the handler untouched.
        with patch.object(middleware, "_get_known_params", AsyncMock(return_value={"brief", "context"})):
            with patch("src.core.config.is_production", return_value=False):
                await middleware.on_call_tool(ctx, capturing_call_next)

        # no envelope strip happened → original context passes through unchanged
        call_args = captured_ctx.message.arguments if captured_ctx is not None else ctx.message.arguments
        assert call_args.get("context") == {"correlation_id": "c1"}


class TestMiddlewareDropsNegotiationFields:
    """Middleware strips AdCP version-negotiation envelope fields in all envs (#1512).

    The AdCP SDK client injects adcp_version / adcp_major_version on every
    request. No tool wrapper declares them, so without stripping, FastMCP's
    strict per-tool arg-validation rejects conformant clients.
    """

    @pytest.mark.asyncio
    async def test_negotiation_fields_removed_before_dispatch(self, middleware):
        ctx = _make_context(
            "get_products",
            {"brief": "ads", "adcp_version": "3.1", "adcp_major_version": 3},
        )
        captured_ctx = None

        async def capturing_call_next(context):
            nonlocal captured_ctx
            captured_ctx = context

        # Not production, and no deprecated-field translations — proves the drop
        # is independent of both the env gate and normalize_request_params.
        with patch("src.core.config.is_production", return_value=False):
            await middleware.on_call_tool(ctx, capturing_call_next)

        assert captured_ctx is not None
        assert captured_ctx.message.arguments == {"brief": "ads"}
        assert "adcp_version" not in captured_ctx.message.arguments
        assert "adcp_major_version" not in captured_ctx.message.arguments

    @pytest.mark.asyncio
    async def test_no_negotiation_fields_leaves_args_untouched(self, middleware):
        ctx = _make_context("get_products", {"brief": "ads"})
        call_next = AsyncMock()

        with patch("src.core.config.is_production", return_value=False):
            await middleware.on_call_tool(ctx, call_next)

        ctx.copy.assert_not_called()
        call_next.assert_called_once_with(ctx)


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


class TestShouldRetry:
    """_should_retry only catches TypeAdapter structural errors, not business logic."""

    def test_typeadapter_validation_error_retries_in_production(self, middleware):
        """TypeAdapter errors (title starts with 'call[') should trigger retry."""
        from pydantic import ValidationError

        # Simulate TypeAdapter error: "validation error for call[create_media_buy]"
        exc = ValidationError.from_exception_data(
            title="call[create_media_buy]",
            line_errors=[],
        )
        with patch("src.core.config.is_production", return_value=True):
            assert middleware._should_retry(exc) is True

    def test_business_logic_validation_error_does_not_retry(self, middleware):
        """Model validation errors (e.g. CreateMediaBuyRequest) must NOT retry."""
        from pydantic import ValidationError

        # Simulate business logic error: "validation error for CreateMediaBuyRequest"
        exc = ValidationError.from_exception_data(
            title="CreateMediaBuyRequest",
            line_errors=[],
        )
        with patch("src.core.config.is_production", return_value=True):
            assert middleware._should_retry(exc) is False

    def test_non_production_never_retries(self, middleware):
        """No retry in dev mode, even for TypeAdapter errors."""
        from pydantic import ValidationError

        exc = ValidationError.from_exception_data(
            title="call[get_products]",
            line_errors=[],
        )
        with patch("src.core.config.is_production", return_value=False):
            assert middleware._should_retry(exc) is False

    def test_tool_error_does_not_retry(self, middleware):
        """ToolError is never retried — TypeAdapter raises ValidationError, not ToolError."""
        from fastmcp.exceptions import ToolError

        exc = ToolError("1 validation error for call[get_products]\ncount\n  Field required [type=missing]")
        with patch("src.core.config.is_production", return_value=True):
            assert middleware._should_retry(exc) is False

    def test_unrelated_exception_does_not_retry(self, middleware):
        """Non-ValidationError exceptions never retry."""
        exc = RuntimeError("unexpected")
        with patch("src.core.config.is_production", return_value=True):
            assert middleware._should_retry(exc) is False


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
