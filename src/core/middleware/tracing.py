"""ASGI middleware that creates a root span per HTTP request.

Reads W3C traceparent from incoming headers to continue a distributed trace.
Adds x-trace-id to response headers for log correlation.
No-op when tracing is disabled.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from opentelemetry import propagate, trace
from opentelemetry.semconv.trace import SpanAttributes

from src.core.telemetry import get_tracer, is_tracing_enabled

logger = logging.getLogger(__name__)

ASGIApp = Callable
_TRACER_NAME = "salesagent.http"


class TracingMiddleware:
    """Outermost ASGI middleware: one root span per HTTP request."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] != "http" or not is_tracing_enabled():
            await self._app(scope, receive, send)
            return

        headers_list: list[tuple[bytes, bytes]] = scope.get("headers", [])
        carrier = {k.decode(): v.decode() for k, v in headers_list}
        ctx = propagate.extract(carrier)

        tracer = get_tracer(_TRACER_NAME)
        method = scope.get("method", "")
        path = scope.get("path", "")
        span_name = f"{method} {path}".strip()

        with tracer.start_as_current_span(
            span_name,
            context=ctx,
            kind=trace.SpanKind.SERVER,
        ) as span:
            span.set_attribute(SpanAttributes.HTTP_METHOD, method)
            span.set_attribute(SpanAttributes.HTTP_TARGET, path)

            status_code: list[int] = []

            async def send_with_trace_header(message: dict) -> None:
                if message["type"] == "http.response.start":
                    code = message.get("status", 0)
                    status_code.append(code)
                    span.set_attribute(SpanAttributes.HTTP_STATUS_CODE, code)

                    trace_id = _format_trace_id(span)
                    if trace_id:
                        existing = list(message.get("headers", []))
                        existing.append((b"x-trace-id", trace_id.encode()))
                        message = {**message, "headers": existing}

                await send(message)

            try:
                await self._app(scope, receive, send_with_trace_header)
            except Exception as exc:
                span.record_exception(exc)
                span.set_status(trace.Status(trace.StatusCode.ERROR, str(exc)))
                raise


def _format_trace_id(span: trace.Span) -> str | None:
    ctx = span.get_span_context()
    if ctx and ctx.is_valid:
        return format(ctx.trace_id, "032x")
    return None
