"""Unit tests for RequestCompatMiddleware (FastMCP on_call_tool).

Tests that the middleware calls normalize_request_params and replaces
the context message when translations are applied.
"""

from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest

from src.core.mcp_compat_middleware import RequestCompatMiddleware
from src.core.request_compat import NormalizationResult
from src.core.tool_error_logging import AdCPToolError
from tests.helpers import assert_envelope_shape


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
    ctx.fastmcp_context = None
    # .copy() should return a new context with the replaced message
    ctx.copy = MagicMock(side_effect=lambda **kw: _make_copied_context(ctx, **kw))
    return ctx


def _make_copied_context(original, **kwargs):
    """Simulate MiddlewareContext.copy(message=...)."""
    copied = MagicMock()
    copied.message = kwargs.get("message", original.message)
    copied.copy = original.copy
    return copied


def _typeadapter_validation_error(tool_name: str, line_error: dict):
    """Build the same pydantic ValidationError shape FastMCP TypeAdapter raises."""
    from pydantic import ValidationError

    return ValidationError.from_exception_data(
        title=f"call[{tool_name}]",
        line_errors=[line_error],
    )


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


class TestTypeAdapterValidationEnvelope:
    """FastMCP TypeAdapter validation errors become AdCP wire envelopes."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("tool_name", "arguments", "line_error", "field", "message"),
        [
            (
                "list_creatives",
                {"filters": {"statuses": []}},
                {
                    "type": "too_short",
                    "loc": ("filters", "statuses"),
                    "input": [],
                    "ctx": {"field_type": "List", "min_length": 1, "actual_length": 0},
                },
                "filters.statuses",
                "List should have at least 1 item",
            ),
            (
                "create_media_buy",
                {"packages": [{}]},
                {
                    "type": "missing",
                    "loc": ("packages", 0, "product_id"),
                    "input": {},
                },
                "packages[0].product_id",
                "Field required",
            ),
        ],
    )
    async def test_typeadapter_validation_errors_are_adcp_tool_errors(
        self, middleware, tool_name, arguments, line_error, field, message
    ):
        ctx = _make_context(tool_name, arguments)
        validation_error = _typeadapter_validation_error(tool_name, line_error)
        call_next = AsyncMock(side_effect=validation_error)

        with patch("src.core.config.is_production", return_value=False):
            with pytest.raises(AdCPToolError) as exc_info:
                await middleware.on_call_tool(ctx, call_next)

        assert_envelope_shape(
            exc_info.value,
            "VALIDATION_ERROR",
            recovery="correctable",
            message_substr=message,
            check_mcp_tool_error=True,
        )
        assert exc_info.value.envelope["errors"][0]["field"] == field
        message = exc_info.value.envelope["errors"][0]["message"]
        assert "input_value" not in message
        assert "errors.pydantic.dev" not in message

    @pytest.mark.asyncio
    async def test_typeadapter_validation_errors_are_recorded_at_mcp_boundary(self, middleware):
        ctx = _make_context("list_creatives", {"filters": {"statuses": []}})
        identity = MagicMock(tenant_id="tenant-1", principal_id="buyer-1")
        ctx.fastmcp_context = MagicMock()
        ctx.fastmcp_context.get_state = AsyncMock(return_value=identity)
        validation_error = _typeadapter_validation_error(
            "list_creatives",
            {
                "type": "too_short",
                "loc": ("filters", "statuses"),
                "input": [],
                "ctx": {"field_type": "List", "min_length": 1, "actual_length": 0},
            },
        )

        with (
            patch("src.core.config.is_production", return_value=False),
            patch("src.core.mcp_compat_middleware.record_boundary_error") as record_error,
            pytest.raises(AdCPToolError),
        ):
            await middleware.on_call_tool(ctx, AsyncMock(side_effect=validation_error))

        record_error.assert_called_once_with(
            "mcp",
            "list_creatives",
            ANY,
            tenant_id="tenant-1",
            principal_id="buyer-1",
        )


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
