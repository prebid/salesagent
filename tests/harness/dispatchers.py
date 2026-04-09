"""Dispatcher classes — one per transport.

Each dispatcher calls the env's transport-specific method and wraps the
result in a TransportResult. The env subclass provides the actual call logic;
the dispatcher only handles result wrapping and error capture.

Usage (internal — called by BaseTestEnv.call_via)::

    dispatcher = DISPATCHERS[Transport.A2A]
    result = dispatcher.dispatch(env, **kwargs)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tests.harness.transport import Transport, TransportResult

if TYPE_CHECKING:
    from tests.harness._base import BaseTestEnv


class ImplDispatcher:
    """Dispatch via direct _impl() call."""

    def dispatch(self, env: BaseTestEnv, **kwargs: Any) -> TransportResult:
        try:
            payload = env.call_impl(**kwargs)
        except Exception as exc:
            return TransportResult(error=exc)
        return TransportResult(payload=payload, envelope={"transport": "impl"})


class A2ADispatcher:
    """Dispatch via _raw() A2A wrapper."""

    def dispatch(self, env: BaseTestEnv, **kwargs: Any) -> TransportResult:
        try:
            payload = env.call_a2a(**kwargs)
        except Exception as exc:
            return TransportResult(error=exc)
        return TransportResult(payload=payload, envelope={"transport": "a2a"})


class RestDispatcher:
    """Dispatch via FastAPI TestClient → route → _raw() → _impl().

    Identity flows through kwargs to env._run_rest_request(), which pops it
    and configures the FastAPI auth dep override per-request.

    Unlike other dispatchers, REST includes HTTP metadata in the envelope
    (status_code, content_type) since tests may assert on these.
    """

    def dispatch(self, env: BaseTestEnv, **kwargs: Any) -> TransportResult:
        try:
            endpoint = env.REST_ENDPOINT  # type: ignore[attr-defined]
            response = env._run_rest_request(endpoint, **kwargs)

            envelope = {
                "transport": "rest",
                "status_code": response.status_code,
                "content_type": response.headers.get("content-type", ""),
            }

            if response.status_code >= 400:
                error = env.parse_rest_error(response.status_code, response.json())
                return TransportResult(error=error, envelope=envelope, raw_response=response)

            payload = env.parse_rest_response(response.json())
            return TransportResult(payload=payload, envelope=envelope, raw_response=response)
        except Exception as exc:
            return TransportResult(error=exc)


class McpDispatcher:
    """Dispatch via Client(mcp) — full FastMCP pipeline.

    Identity flows through kwargs to env.call_mcp() → _run_mcp_client(),
    which pops it and dispatches via FastMCP in-memory transport.
    """

    def dispatch(self, env: BaseTestEnv, **kwargs: Any) -> TransportResult:
        try:
            payload = env.call_mcp(**kwargs)
        except Exception as exc:
            return TransportResult(error=exc)
        return TransportResult(payload=payload, envelope={"transport": "mcp"})


DISPATCHERS: dict[Transport, ImplDispatcher | A2ADispatcher | RestDispatcher | McpDispatcher] = {
    Transport.IMPL: ImplDispatcher(),
    Transport.A2A: A2ADispatcher(),
    Transport.REST: RestDispatcher(),
    Transport.MCP: McpDispatcher(),
}
