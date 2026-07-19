"""Unit tests for RequestCompatMiddleware (FastMCP on_call_tool).

Tests that the middleware calls normalize_request_params and replaces
the context message when translations are applied.
"""

from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest

from src.core.exceptions import AdCPValidationError
from src.core.mcp_compat_middleware import RequestCompatMiddleware
from src.core.request_compat import STANDARD_ADCP_READ_TOOLS, NormalizationResult
from src.core.tool_error_logging import AdCPToolError
from tests.helpers import assert_envelope_shape, assert_no_raw_validation_leak


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


class _ValidationErrorRecord:
    """Matcher that pins the typed boundary error passed to the recorder."""

    def __eq__(self, other: object) -> bool:
        return isinstance(other, AdCPValidationError) and other.error_code == "VALIDATION_ERROR"

    def __repr__(self) -> str:
        return "AdCPValidationError(error_code='VALIDATION_ERROR')"


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
        from src.core.tool_error_logging import AdCPToolError
        from tests.helpers.envelope_assertions import assert_envelope_shape

        ctx = _make_context("get_products", {"brief": "ads", "adcp_major_version": 99})
        call_next = AsyncMock()

        # Middleware translates the AdCPError to the wire envelope (VERSION_UNSUPPORTED),
        # the same shape the tool wrapper emits — not a bare AdCPError. Pin the
        # full two-layer wire shape per the Error Verification Policy.
        with pytest.raises(AdCPToolError) as exc:
            await middleware.on_call_tool(ctx, call_next)
        assert_envelope_shape(exc.value, "VERSION_UNSUPPORTED", recovery="correctable", check_mcp_tool_error=True)
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
    async def test_context_stripped_when_tool_does_not_declare_it(self, middleware, caplog):
        ctx = _make_context("get_adcp_capabilities", {"context": {"correlation_id": "c1"}})
        captured_ctx = None

        async def capturing_call_next(context):
            nonlocal captured_ctx
            captured_ctx = context

        # get_adcp_capabilities declares only `protocols` — not the AdCP `context` field.
        with (
            patch.object(middleware, "_get_known_params", AsyncMock(return_value={"protocols"})),
            patch("src.core.config.is_production", return_value=False),
            caplog.at_level("DEBUG", logger="src.core.request_compat"),
        ):
            await middleware.on_call_tool(ctx, capturing_call_next)

        assert captured_ctx is not None
        assert "context" not in captured_ctx.message.arguments
        assert caplog.messages == ["Dropped undeclared AdCP envelope fields from get_adcp_capabilities: context"]

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


class TestMiddlewareReadIdempotencyEnvelope:
    """Every registered standard read consumes one validated inert key."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("tool_name", sorted(STANDARD_ADCP_READ_TOOLS))
    async def test_valid_key_is_consumed_before_dispatch_for_every_read(self, middleware, tool_name):
        ctx = _make_context(tool_name, {"idempotency_key": "valid-read-key-0001"})
        captured_ctx = None

        async def capturing_call_next(context):
            nonlocal captured_ctx
            captured_ctx = context

        with (
            patch.object(middleware, "_get_known_params", AsyncMock(return_value=set())),
            patch("src.core.config.is_production", return_value=False),
        ):
            await middleware.on_call_tool(ctx, capturing_call_next)

        assert captured_ctx is not None
        assert captured_ctx.message.arguments == {}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("tool_name", sorted(STANDARD_ADCP_READ_TOOLS))
    async def test_explicit_null_rejects_before_envelope_strip_for_every_read(self, middleware, tool_name):
        ctx = _make_context(tool_name, {"idempotency_key": None})
        call_next = AsyncMock()

        with pytest.raises(AdCPToolError) as exc_info:
            await middleware.on_call_tool(ctx, call_next)

        assert_envelope_shape(
            exc_info.value,
            "VALIDATION_ERROR",
            recovery="correctable",
            message_substr="idempotency_key must be a string",
            check_mcp_tool_error=True,
        )
        call_next.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_version_error_precedes_malformed_read_key(self, middleware):
        ctx = _make_context(
            "get_products",
            {"adcp_version": "4.0", "idempotency_key": None},
        )
        call_next = AsyncMock()

        with pytest.raises(AdCPToolError) as exc_info:
            await middleware.on_call_tool(ctx, call_next)

        assert_envelope_shape(
            exc_info.value,
            "VERSION_UNSUPPORTED",
            recovery="correctable",
            check_mcp_tool_error=True,
        )
        call_next.assert_not_awaited()


class TestMiddlewareDropsNegotiationFields:
    """Middleware strips AdCP version-negotiation envelope fields in all envs (#1512).

    The AdCP SDK client injects adcp_version / adcp_major_version on every
    request. No tool wrapper declares them, so without stripping, FastMCP's
    strict per-tool arg-validation rejects conformant clients.
    """

    @pytest.mark.asyncio
    async def test_negotiation_fields_removed_before_dispatch(self, middleware, caplog):
        from src.core.adcp_version import adcp_major_version, supported_adcp_versions

        ctx = _make_context(
            "get_products",
            {
                "brief": "ads",
                "adcp_version": supported_adcp_versions()[0],
                "adcp_major_version": adcp_major_version(),
            },
        )
        captured_ctx = None

        async def capturing_call_next(context):
            nonlocal captured_ctx
            captured_ctx = context

        # Not production, and no deprecated-field translations — proves the drop
        # is independent of both the env gate and normalize_request_params.
        with (
            patch("src.core.config.is_production", return_value=False),
            caplog.at_level("DEBUG", logger="src.core.request_compat"),
        ):
            await middleware.on_call_tool(ctx, capturing_call_next)

        assert captured_ctx is not None
        assert captured_ctx.message.arguments == {"brief": "ads"}
        assert "adcp_version" not in captured_ctx.message.arguments
        assert "adcp_major_version" not in captured_ctx.message.arguments
        assert caplog.messages == [
            "Dropped AdCP negotiation fields from get_products: adcp_major_version, adcp_version"
        ]

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
                "list_creatives",
                {"filters": {"format_ids": [{}]}},
                {
                    "type": "missing",
                    "loc": ("filters", "format_ids", 0, "agent_url"),
                    "input": {},
                },
                "filters.format_ids[0].agent_url",
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
        wire_message = exc_info.value.envelope["errors"][0]["message"]
        assert_no_raw_validation_leak(wire_message)

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

        ctx.fastmcp_context.get_state.assert_awaited_once_with("identity")
        record_error.assert_called_once_with(
            "mcp",
            "list_creatives",
            _ValidationErrorRecord(),
            tenant_id="tenant-1",
            principal_id="buyer-1",
        )


class TestMiddlewareEdgeCases:
    """Argument-less calls still cross the validation-envelope boundary."""

    @pytest.mark.asyncio
    async def test_none_arguments_are_normalized_before_dispatch(self, middleware):
        ctx = _make_context("get_products", None)
        call_next = AsyncMock()

        with patch("src.core.mcp_compat_middleware.normalize_request_params") as mock_norm:
            mock_norm.return_value = NormalizationResult(params={}, translations_applied=[])
            await middleware.on_call_tool(ctx, call_next)

        mock_norm.assert_called_once_with("get_products", {})
        call_next.assert_awaited_once_with(ctx)

    @pytest.mark.asyncio
    async def test_empty_arguments_are_normalized_before_dispatch(self, middleware):
        ctx = _make_context("get_products", {})
        call_next = AsyncMock()

        with patch("src.core.mcp_compat_middleware.normalize_request_params") as mock_norm:
            mock_norm.return_value = NormalizationResult(params={}, translations_applied=[])
            await middleware.on_call_tool(ctx, call_next)

        mock_norm.assert_called_once_with("get_products", {})
        call_next.assert_awaited_once_with(ctx)
