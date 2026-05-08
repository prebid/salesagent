"""End-to-end ASGI test for per-leg bearer auth wiring.

Replaces the shim-era ``test_a2a_bearer_auth_translation.py`` after
:class:`BearerToAdcpAuthMiddleware` was removed (adcp 4.5.0 ships per-leg
config). The previous file verified a translation middleware sat outside
the SDK auth chain; this file verifies the SDK chain itself with the
production ``BearerTokenAuth`` config.

Why end-to-end ASGI: dataclass introspection of ``BearerTokenAuth``
proves the kwargs are accepted but doesn't catch wiring bugs — a future
change might pass the same config through a wrapper that rebinds the
header name, or a transport-specific layer might short-circuit auth on
the wrong path. This test runs requests through the actual SDK
middlewares wired the way ``adcp.server.serve._wrap_mcp_with_auth`` and
``_wrap_a2a_with_auth`` wire them, with the same kwargs
``core.main._serve_kwargs`` passes.

Coverage:
* MCP leg accepts ``x-adcp-auth: <raw>`` → 200.
* MCP leg rejects ``x-adcp-auth: <wrong>`` → 401.
* A2A leg accepts ``Authorization: Bearer <token>`` → 200 + Principal in scope.
* A2A leg rejects ``x-adcp-auth: <raw>`` → 401 (no fallback).
* A2A leg rejects ``Authorization: Bearer <wrong>`` → 401.
"""

from __future__ import annotations

from typing import Any

import pytest
from adcp.server.auth import (
    A2ABearerAuthMiddleware,
    BearerTokenAuth,
    BearerTokenAuthMiddleware,
    Principal,
)
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient


def _production_auth() -> BearerTokenAuth:
    """The exact ``BearerTokenAuth`` config ``core.main._serve_kwargs`` passes."""
    return BearerTokenAuth(
        validate_token=lambda t: Principal(caller_identity="buyer-1", tenant_id="acme") if t == "valid-token" else None,
        mcp_header_name="x-adcp-auth",
        mcp_bearer_prefix_required=False,
    )


def _build_mcp_app(auth: BearerTokenAuth) -> Starlette:
    """Mimic ``adcp.server.serve._wrap_mcp_with_auth`` — Starlette + middleware
    with the per-leg-resolved kwargs."""

    async def handler(request: Request) -> JSONResponse:
        # JSON-RPC body shape so the SDK's discovery-bypass peek doesn't
        # short-circuit auth (it skips ``initialize`` / ``tools/list`` /
        # ``notifications/initialized`` / ``get_adcp_capabilities``).
        return JSONResponse({"ok": True})

    app = Starlette(routes=[Route("/", handler, methods=["POST"])])
    app.add_middleware(
        BearerTokenAuthMiddleware,
        validate_token=auth.validate_token,
        unauthenticated_response=auth.unauthenticated_response,
        header_name=auth.resolved_mcp_header_name(),
        bearer_prefix_required=auth.resolved_mcp_bearer_prefix_required(),
    )
    return app


class _CapturingApp:
    """ASGI sink that records the scope it was called with — for A2A path."""

    def __init__(self) -> None:
        self.captured_scope: dict[str, Any] | None = None

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        self.captured_scope = scope
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"", "more_body": False})


def _post_scope(headers: list[tuple[bytes, bytes]], path: str = "/") -> dict[str, Any]:
    return {
        "type": "http",
        "method": "POST",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": headers,
    }


async def _drain(messages: list[dict[str, Any]], scope: dict[str, Any], app: Any) -> None:
    async def _receive() -> dict[str, Any]:
        return {"type": "http.disconnect"}

    async def _send(message: dict[str, Any]) -> None:
        messages.append(message)

    await app(scope, _receive, _send)


# ─── MCP leg ──────────────────────────────────────────────────────────────────


def test_mcp_accepts_x_adcp_auth_header():
    """Legacy MCP clients send raw token in ``x-adcp-auth`` (no Bearer prefix)."""
    auth = _production_auth()
    app = _build_mcp_app(auth)

    # Body must NOT match a discovery method or the SDK bypasses auth.
    body = {"jsonrpc": "2.0", "method": "tools/call", "params": {"name": "x"}, "id": 1}
    with TestClient(app) as client:
        r = client.post("/", json=body, headers={"x-adcp-auth": "valid-token"})
    assert r.status_code == 200, f"MCP rejected x-adcp-auth: <raw>; body={r.text}"


def test_mcp_rejects_unknown_token():
    """Unknown token on MCP → 401 (validator returns None)."""
    auth = _production_auth()
    app = _build_mcp_app(auth)
    body = {"jsonrpc": "2.0", "method": "tools/call", "params": {"name": "x"}, "id": 1}

    with TestClient(app) as client:
        r = client.post("/", json=body, headers={"x-adcp-auth": "wrong-token"})
    assert r.status_code == 401


def test_mcp_rejects_missing_header():
    """No ``x-adcp-auth`` on MCP → 401."""
    auth = _production_auth()
    app = _build_mcp_app(auth)
    body = {"jsonrpc": "2.0", "method": "tools/call", "params": {"name": "x"}, "id": 1}

    with TestClient(app) as client:
        r = client.post("/", json=body)
    assert r.status_code == 401


# ─── A2A leg ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a2a_accepts_authorization_bearer():
    """RFC 6750 ``Authorization: Bearer <token>`` is the canonical A2A carrier
    in adcp 4.5.0 — what off-the-shelf a2a-sdk clients emit."""
    inner = _CapturingApp()
    app = A2ABearerAuthMiddleware(inner, _production_auth())

    messages: list[dict[str, Any]] = []
    await _drain(messages, _post_scope([(b"authorization", b"Bearer valid-token")]), app)

    assert inner.captured_scope is not None, (
        "A2A middleware rejected Authorization: Bearer — per-leg auth wiring is broken."
    )
    starts = [m for m in messages if m.get("type") == "http.response.start"]
    assert starts and starts[0]["status"] == 200


@pytest.mark.asyncio
async def test_a2a_rejects_x_adcp_auth_header():
    """``x-adcp-auth`` must NOT authenticate on A2A — that fallback was the
    old shim's behavior. Removing it closes the legacy attack surface where
    a buyer could keep using the non-canonical header indefinitely."""
    inner = _CapturingApp()
    app = A2ABearerAuthMiddleware(inner, _production_auth())

    messages: list[dict[str, Any]] = []
    await _drain(messages, _post_scope([(b"x-adcp-auth", b"valid-token")]), app)

    assert inner.captured_scope is None, (
        "A2A middleware accepted x-adcp-auth — backward-compat fallback should be closed in adcp>=4.5.0."
    )
    starts = [m for m in messages if m.get("type") == "http.response.start"]
    assert starts and starts[0]["status"] == 401


@pytest.mark.asyncio
async def test_a2a_rejects_unknown_bearer_token():
    """Unknown token on A2A → 401 (validator returns None)."""
    inner = _CapturingApp()
    app = A2ABearerAuthMiddleware(inner, _production_auth())

    messages: list[dict[str, Any]] = []
    await _drain(messages, _post_scope([(b"authorization", b"Bearer wrong-token")]), app)

    assert inner.captured_scope is None
    starts = [m for m in messages if m.get("type") == "http.response.start"]
    assert starts and starts[0]["status"] == 401


@pytest.mark.asyncio
async def test_a2a_401_carries_www_authenticate_bearer_challenge():
    """RFC 6750 §3 + RFC 7235 §3.1 require ``WWW-Authenticate: Bearer`` on
    every 401 from a Bearer-protected resource. Without it, RFC-compliant
    clients (browsers, many HTTP libraries) treat the 401 as a generic
    error and never surface the auth challenge to the buyer.

    adcp 4.5.0 emits the header on every A2A 401 — this test pins that
    contract so a future SDK upgrade can't silently drop it.
    """
    inner = _CapturingApp()
    app = A2ABearerAuthMiddleware(inner, _production_auth())

    messages: list[dict[str, Any]] = []
    await _drain(messages, _post_scope([]), app)  # no auth header

    starts = [m for m in messages if m.get("type") == "http.response.start"]
    assert starts and starts[0]["status"] == 401, "Expected 401 on missing bearer"
    headers = {name.decode("latin-1").lower(): value.decode("latin-1") for name, value in starts[0]["headers"]}
    assert "www-authenticate" in headers, (
        f"401 must carry WWW-Authenticate per RFC 6750 §3; got headers: {list(headers)}"
    )
    challenge = headers["www-authenticate"]
    assert challenge.lower().startswith("bearer "), (
        f"WWW-Authenticate must start with 'Bearer'; got {challenge!r}"
    )
    assert "realm=" in challenge.lower(), (
        f"WWW-Authenticate Bearer challenge must include realm parameter; got {challenge!r}"
    )
