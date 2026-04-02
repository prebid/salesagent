"""Dispatcher classes — one per transport.

Each dispatcher calls the env's transport-specific method and wraps the
result in a TransportResult. The env subclass provides the actual call logic;
the dispatcher only handles result wrapping and error capture.

Usage (internal — called by BaseTestEnv.call_via)::

    dispatcher = DISPATCHERS[Transport.A2A]
    result = dispatcher.dispatch(env, **kwargs)
"""

from __future__ import annotations

import os
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


class RestE2EDispatcher:
    """Dispatch via real HTTP through nginx to the Docker stack.

    Uses httpx to send POST requests to the live server, exercising the full
    stack: nginx -> UnifiedAuthMiddleware -> resolve_identity() ->
    get_principal_from_token() DB lookup -> route handler -> _impl().

    Environment variables:
        E2E_BASE_URL: Docker stack URL (default http://localhost:8092)
        E2E_AUTH_TOKEN: auth token (default ci-test-token)
        E2E_TENANT: tenant subdomain (default ci-test)
    """

    def dispatch(self, env: BaseTestEnv, **kwargs: Any) -> TransportResult:
        import httpx

        base_url = os.environ.get("E2E_BASE_URL", "http://localhost:8092")
        auth_token = os.environ.get("E2E_AUTH_TOKEN", "ci-test-token")
        tenant = os.environ.get("E2E_TENANT", "ci-test")

        headers = {
            "x-adcp-auth": auth_token,
            "x-adcp-tenant": tenant,
            "Content-Type": "application/json",
        }

        body = env.build_rest_body(**kwargs)
        endpoint = env.REST_ENDPOINT  # type: ignore[attr-defined]

        with httpx.Client(base_url=base_url, timeout=30) as client:
            method = getattr(env, "REST_METHOD", "post")
            response = getattr(client, method)(endpoint, json=body, headers=headers)

        envelope = {
            "transport": "e2e_rest",
            "status_code": response.status_code,
            "content_type": response.headers.get("content-type", ""),
        }

        if response.status_code >= 400:
            try:
                error = env.parse_rest_error(response.status_code, response.json())
            except Exception as exc:
                error = exc
            return TransportResult(payload=None, envelope=envelope, error=error, raw_response=response)

        try:
            payload = env.parse_rest_response(response.json())
        except Exception as exc:
            return TransportResult(payload=None, envelope=envelope, error=exc, raw_response=response)

        return TransportResult(
            payload=payload,
            envelope=envelope,
            error=None,
            raw_response=response,
        )


class McpE2EDispatcher:
    """Placeholder for real MCP E2E dispatch (not yet implemented)."""

    def dispatch(self, env: BaseTestEnv, **kwargs: Any) -> TransportResult:
        raise NotImplementedError(
            "E2E_MCP dispatcher is not yet implemented. Use Transport.MCP for in-process MCP dispatch."
        )


class A2AE2EDispatcher:
    """Placeholder for real A2A E2E dispatch (not yet implemented)."""

    def dispatch(self, env: BaseTestEnv, **kwargs: Any) -> TransportResult:
        raise NotImplementedError(
            "E2E_A2A dispatcher is not yet implemented. Use Transport.A2A for in-process A2A dispatch."
        )


_DispatcherType = (
    ImplDispatcher
    | A2ADispatcher
    | RestDispatcher
    | McpDispatcher
    | RestE2EDispatcher
    | McpE2EDispatcher
    | A2AE2EDispatcher
)

DISPATCHERS: dict[Transport, _DispatcherType] = {
    Transport.IMPL: ImplDispatcher(),
    Transport.A2A: A2ADispatcher(),
    Transport.REST: RestDispatcher(),
    Transport.MCP: McpDispatcher(),
    Transport.E2E_REST: RestE2EDispatcher(),
    Transport.E2E_MCP: McpE2EDispatcher(),
    Transport.E2E_A2A: A2AE2EDispatcher(),
}
