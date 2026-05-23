"""Unit tests for RequestCompatMiddleware (FastMCP on_call_tool).

Tests that the middleware calls normalize_request_params and replaces
the context message when translations are applied.
"""

from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest

from src.core.mcp_compat_middleware import ENVELOPE_FIELDS, RequestCompatMiddleware
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


class TestEnvelopeFieldStripping:
    """Envelope metadata fields (adcp_major_version etc.) are stripped at the MCP
    boundary in all environments so wrappers don't have to declare them.

    Closes #1308 — the @adcp/sdk storyboard runner injects adcp_major_version=3
    on every tool call as envelope metadata; FastMCP's TypeAdapter would
    otherwise raise unexpected_keyword_argument before the request reaches the
    tool. REST (api_v1.py Body schemas with Pydantic extra='ignore') and A2A
    (parameters.get() lookups) already silently ignore the field; this restores
    cross-transport symmetry by giving MCP the same behavior.
    """

    def test_envelope_fields_allowlist_includes_adcp_major_version(self):
        """The allowlist must include adcp_major_version (the SDK's envelope field).

        Pinning prevents accidental removal — if the allowlist shrinks, the next
        storyboard CI run regresses with the same unexpected_keyword_argument
        error documented in #1308.
        """
        assert "adcp_major_version" in ENVELOPE_FIELDS

    @pytest.mark.asyncio
    async def test_adcp_major_version_stripped_before_dispatch(self, middleware):
        """Envelope field is removed from arguments before call_next runs.

        Verifies the strip happens at the middleware layer — the tool wrapper
        never sees adcp_major_version, so its signature does not need to
        declare it (matching the project convention 'declare fields you use').
        """
        ctx = _make_context("get_products", {"brief": "ads", "adcp_major_version": 3})
        captured = {}

        async def capturing_call_next(context):
            captured["arguments"] = dict(context.message.arguments)
            return MagicMock()

        with patch("src.core.mcp_compat_middleware.normalize_request_params") as mock_norm:
            mock_norm.return_value = NormalizationResult(
                params={"brief": "ads", "adcp_major_version": 3},
                translations_applied=[],
            )
            await middleware.on_call_tool(ctx, capturing_call_next)

        assert "adcp_major_version" not in captured["arguments"], (
            f"adcp_major_version must be stripped at the MCP boundary, got {captured['arguments']!r}"
        )
        assert captured["arguments"] == {"brief": "ads"}, (
            f"non-envelope fields must be preserved, got {captured['arguments']!r}"
        )

    @pytest.mark.asyncio
    async def test_non_envelope_fields_preserved(self, middleware):
        """Unknown fields that are NOT envelope metadata still reach call_next.

        Confirms the strip is an allowlist (not a blanket unknown-strip) — the
        dev loud-fail signal for actual protocol drift stays intact, because
        only listed envelope fields are removed.
        """
        ctx = _make_context("get_products", {"brief": "ads", "unknown_drift_field": "x"})
        captured = {}

        async def capturing_call_next(context):
            captured["arguments"] = dict(context.message.arguments)
            return MagicMock()

        with patch("src.core.mcp_compat_middleware.normalize_request_params") as mock_norm:
            mock_norm.return_value = NormalizationResult(
                params={"brief": "ads", "unknown_drift_field": "x"},
                translations_applied=[],
            )
            await middleware.on_call_tool(ctx, capturing_call_next)

        # unknown_drift_field reaches call_next so TypeAdapter raises loudly in dev.
        assert captured["arguments"].get("unknown_drift_field") == "x"

    @pytest.mark.asyncio
    async def test_envelope_strip_fires_in_dev_environment(self, middleware):
        """Strip runs regardless of is_production() — envelope fields are
        environment-independent metadata, not "tolerated unknown drift".

        Distinct from the Step 2 production-gated unknown-stripping (which keeps
        the dev loud-fail signal). The envelope strip happens BEFORE step 2.
        """
        ctx = _make_context("get_products", {"brief": "ads", "adcp_major_version": 3})
        captured = {}

        async def capturing_call_next(context):
            captured["arguments"] = dict(context.message.arguments)
            return MagicMock()

        with (
            patch("src.core.mcp_compat_middleware.normalize_request_params") as mock_norm,
            patch("src.core.config.is_production", return_value=False),
        ):
            mock_norm.return_value = NormalizationResult(
                params={"brief": "ads", "adcp_major_version": 3},
                translations_applied=[],
            )
            await middleware.on_call_tool(ctx, capturing_call_next)

        assert "adcp_major_version" not in captured["arguments"]
