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
    """Dispatch via FastAPI TestClient → route → _raw() → _impl()."""

    def dispatch(self, env: BaseTestEnv, **kwargs: Any) -> TransportResult:
        try:
            client = env.get_rest_client()
            body = env.build_rest_body(**kwargs)
            endpoint = env.REST_ENDPOINT  # type: ignore[attr-defined]
            response = client.post(endpoint, json=body)

            if response.status_code >= 400:
                error = env.parse_rest_error(response.status_code, response.json())
                return TransportResult(
                    error=error,
                    envelope={
                        "transport": "rest",
                        "status_code": response.status_code,
                        "content_type": response.headers.get("content-type", ""),
                    },
                    raw_response=response,
                )

            payload = env.parse_rest_response(response.json())
            return TransportResult(
                payload=payload,
                envelope={
                    "transport": "rest",
                    "status_code": response.status_code,
                    "content_type": response.headers.get("content-type", ""),
                },
                raw_response=response,
            )
        except Exception as exc:
            return TransportResult(error=exc)


_MCP_SENTINEL = object()


class McpDispatcher:
    """Dispatch via mock Context → async MCP wrapper → _impl().

    The MCP wrapper is async, so this dispatcher uses asyncio.run()
    to bridge from the sync dispatch() interface. The env.call_mcp()
    method handles Context mocking and ToolResult extraction.
    """

    def dispatch(self, env: BaseTestEnv, **kwargs: Any) -> TransportResult:
        try:
            # MCP wrappers get identity from ctx.get_state(), not kwargs.
            # Extract identity and pass it as _mcp_identity so _run_mcp_wrapper
            # can set the mock Context's get_state to return this identity
            # instead of the default. This enables multi-agent and no-auth scenarios.
            identity = kwargs.pop("identity", _MCP_SENTINEL)
            if identity is not _MCP_SENTINEL:
                kwargs["_mcp_identity"] = identity
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
