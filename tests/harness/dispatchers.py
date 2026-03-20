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

    Identity flows through kwargs to env.call_rest() → _run_rest_request(),
    which pops it and configures the FastAPI auth dep override per-request.
    """

    def dispatch(self, env: BaseTestEnv, **kwargs: Any) -> TransportResult:
        try:
            payload = env.call_rest(**kwargs)
        except Exception as exc:
            return TransportResult(error=exc)
        return TransportResult(payload=payload, envelope={"transport": "rest"})


class McpDispatcher:
    """Dispatch via mock Context → async MCP wrapper → _impl().

    Identity flows through kwargs to env.call_mcp() → _run_mcp_wrapper(),
    which pops it and configures the mock Context.
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
