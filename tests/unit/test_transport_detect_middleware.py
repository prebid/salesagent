"""Regression test: ``TransportDetectMiddleware`` populates the
``current_transport`` ContextVar based on the inbound URL path.

Without this signal, ``core.platforms._delegate._build_identity`` can't
distinguish A2A from MCP requests and stamps every workflow step with
``protocol="mcp"`` — which makes ``context_manager._send_push_notifications``
fire MCP-shaped webhooks at A2A buyers (#64 / #202).
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from core.middleware.transport_detect import (
    TransportDetectMiddleware,
    _classify_path,
    current_transport,
)


@pytest.mark.parametrize(
    "path,expected",
    [
        ("/mcp", "mcp"),
        ("/mcp/", "mcp"),
        ("/mcp/tools/call", "mcp"),
        ("/", "a2a"),
        ("/.well-known/agent-card.json", "a2a"),
        ("/admin", None),
        ("/admin/", None),
        ("/admin/tenants", None),
        ("/static/css/app.css", None),
        ("/auth/callback", None),
        ("/tenant/default", None),
        ("/api/v1/tenant-management", None),
        ("/login", None),
        ("/logout", None),
    ],
)
def test_classify_path(path: str, expected: str | None) -> None:
    """The classifier maps URL prefixes to transport labels. Admin /
    static / auth paths get None so admin requests don't poison the
    ContextVar with a buyer-protocol value."""
    assert _classify_path(path) == expected


def _run_middleware(path: str) -> tuple[str | None, str | None]:
    """Drive the middleware once with a synthetic ASGI scope and capture
    what ``current_transport`` looked like inside the inner app vs. after
    the request returned."""
    inside_value: list[str | None] = []

    async def inner_app(scope: Any, receive: Any, send: Any) -> None:
        inside_value.append(current_transport.get())

    middleware = TransportDetectMiddleware(inner_app)

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        return None

    scope: dict[str, Any] = {"type": "http", "method": "POST", "path": path}
    asyncio.run(middleware(scope, receive, send))

    after_value = current_transport.get()
    return inside_value[0], after_value


def test_middleware_sets_mcp_on_mcp_path() -> None:
    inside, after = _run_middleware("/mcp/tools/call")
    assert inside == "mcp"
    # ContextVar is reset in ``finally`` so the next request can't see
    # this one's transport.
    assert after is None


def test_middleware_sets_a2a_on_host_root() -> None:
    inside, after = _run_middleware("/")
    assert inside == "a2a"
    assert after is None


def test_middleware_clears_on_admin_path() -> None:
    inside, after = _run_middleware("/admin/tenants")
    # Admin paths get None — no buyer-protocol semantics on the wire,
    # so any downstream code defaults safely.
    assert inside is None
    assert after is None


def test_middleware_passes_through_non_http_scopes() -> None:
    """Lifespan / websocket scopes must not crash or set the contextvar."""
    seen: list[Any] = []

    async def inner_app(scope: Any, receive: Any, send: Any) -> None:
        seen.append(current_transport.get())

    middleware = TransportDetectMiddleware(inner_app)

    async def receive() -> dict[str, Any]:
        return {"type": "lifespan.startup"}

    async def send(message: dict[str, Any]) -> None:
        return None

    asyncio.run(middleware({"type": "lifespan"}, receive, send))
    assert seen == [None]
