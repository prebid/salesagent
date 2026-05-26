from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest


async def _call_middleware(middleware, scope, headers=None):
    """Helper: call middleware with a minimal ASGI scope."""
    headers = headers or []
    receive = AsyncMock()
    send_calls = []

    async def send(message):
        send_calls.append(message)

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/mcp",
        "query_string": b"",
        "headers": headers,
        **scope,
    }
    await middleware(scope, receive, send)
    return send_calls


@pytest.mark.asyncio
async def test_middleware_passes_through_when_tracing_disabled():
    with patch("src.core.middleware.tracing.is_tracing_enabled", return_value=False):
        from src.core.middleware.tracing import TracingMiddleware

        inner_called = {"n": 0}

        async def inner_app(scope, receive, send):
            inner_called["n"] += 1
            await send({"type": "http.response.start", "status": 200, "headers": []})

        middleware = TracingMiddleware(inner_app)
        await _call_middleware(middleware, {}, headers=[])
        assert inner_called["n"] == 1


@pytest.mark.asyncio
async def test_middleware_extracts_traceparent_header():
    with (
        patch("src.core.middleware.tracing.is_tracing_enabled", return_value=True),
        patch("src.core.middleware.tracing.get_tracer") as mock_get_tracer,
        patch("src.core.middleware.tracing.propagate") as mock_propagate,
    ):
        mock_span = MagicMock()
        mock_span.__enter__ = lambda s: s
        mock_span.__exit__ = MagicMock(return_value=False)
        mock_span.is_recording.return_value = True
        mock_span.get_span_context.return_value = MagicMock(
            trace_id=0xABCD1234ABCD1234ABCD1234ABCD1234,
            is_valid=True,
        )
        mock_tracer = MagicMock()
        mock_tracer.start_as_current_span.return_value = mock_span
        mock_get_tracer.return_value = mock_tracer
        mock_propagate.extract.return_value = {}

        from src.core.middleware.tracing import TracingMiddleware

        response_headers = []

        async def inner_app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})

        async def capturing_send(message):
            if message["type"] == "http.response.start":
                response_headers.extend(message.get("headers", []))

        middleware = TracingMiddleware(inner_app)
        traceparent = b"00-abcd1234abcd1234abcd1234abcd1234-0102030405060708-01"
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/mcp",
            "query_string": b"",
            "headers": [(b"traceparent", traceparent)],
        }
        await middleware(scope, AsyncMock(), capturing_send)

        mock_propagate.extract.assert_called_once_with(ANY)


@pytest.mark.asyncio
async def test_middleware_skips_non_http_scopes():
    with patch("src.core.middleware.tracing.is_tracing_enabled", return_value=True):
        from src.core.middleware.tracing import TracingMiddleware

        inner_called = {"n": 0}

        async def inner_app(scope, receive, send):
            inner_called["n"] += 1

        middleware = TracingMiddleware(inner_app)
        scope = {"type": "lifespan"}
        await middleware(scope, AsyncMock(), AsyncMock())
        assert inner_called["n"] == 1
