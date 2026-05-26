"""Dispatcher classes — one per transport.

Each dispatcher calls the env's transport-specific method and wraps the
result in a TransportResult. The env subclass provides the actual call logic;
the dispatcher only handles result wrapping and error capture.

Usage (internal — called by BaseTestEnv.call_via)::

    dispatcher = DISPATCHERS[Transport.MCP]
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


class McpDispatcher:
    """Dispatch via in-process MCP — httpx ASGITransport against build_app().

    Identity flows through kwargs to env.call_mcp() → _run_mcp_client(),
    which pops it and dispatches via httpx against the same Starlette
    app production runs.
    """

    def dispatch(self, env: BaseTestEnv, **kwargs: Any) -> TransportResult:
        try:
            payload = env.call_mcp(**kwargs)
        except Exception as exc:
            return TransportResult(error=exc)
        return TransportResult(payload=payload, envelope={"transport": "mcp"})


class A2aDispatcher:
    """Dispatch via in-process A2A JSON-RPC against build_app()."""

    def dispatch(self, env: BaseTestEnv, **kwargs: Any) -> TransportResult:
        try:
            payload = env.call_a2a(**kwargs)
        except Exception as exc:
            return TransportResult(error=exc)
        return TransportResult(payload=payload, envelope={"transport": "a2a"})


DISPATCHERS: dict[Transport, ImplDispatcher | McpDispatcher | A2aDispatcher] = {
    Transport.IMPL: ImplDispatcher(),
    Transport.MCP: McpDispatcher(),
    Transport.A2A: A2aDispatcher(),
}
