"""Unit tests for L0-09 RequestIDMiddleware.

Canonical spec: ``flask-to-fastapi-foundation-modules.md`` §11.9.5.

Obligations:
  1. Each request gets a freshly generated 32-char lowercase hex UUID
     request_id when the incoming request carries no ``X-Request-ID``.
  2. Two distinct requests get distinct generated request_ids.
  3. An incoming ``X-Request-ID`` header value is reused (preserved).
  4. The response emits ``X-Request-ID`` echoing the active value.
  5. Non-HTTP scopes pass through untouched.
"""

from __future__ import annotations

import re
from unittest.mock import AsyncMock

import pytest

from src.admin.middleware.request_id import RequestIDMiddleware

_HEX32_RE = re.compile(r"^[a-f0-9]{32}$")


async def _receive() -> dict:
    return {"type": "http.request", "body": b"", "more_body": False}


async def _capture_send(messages: list[dict]):
    async def send(message: dict) -> None:
        messages.append(message)

    return send


def _make_http_scope(headers: list[tuple[bytes, bytes]] | None = None) -> dict:
    return {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": headers or [],
        "state": {},
    }


async def _run(mw: RequestIDMiddleware, scope: dict, inner):
    sends: list[dict] = []
    send = await _capture_send(sends)
    # Capture scope state as inner sees it
    seen_state: dict = {}

    async def tracking_inner(s: dict, r, sd) -> None:
        seen_state["state"] = dict(s.get("state", {}))
        await inner(s, r, sd)

    await mw(scope, _receive, send)  # type: ignore[arg-type]
    return sends, seen_state


@pytest.mark.asyncio
async def test_generates_32char_hex_request_id_when_absent():
    """Request without X-Request-ID gets a generated 32-char hex UUID."""
    seen: dict = {}

    async def inner(scope, receive, send) -> None:
        seen["state"] = dict(scope.get("state", {}))
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    mw = RequestIDMiddleware(inner)
    scope = _make_http_scope()
    await mw(scope, _receive, AsyncMock())  # type: ignore[arg-type]

    rid = seen["state"].get("request_id")
    assert rid is not None, "middleware did not populate scope state"
    assert isinstance(rid, str)
    assert _HEX32_RE.match(rid), f"request_id {rid!r} is not a 32-char lowercase hex string"


@pytest.mark.asyncio
async def test_two_requests_get_distinct_request_ids():
    """Two sequential requests MUST get different generated request_ids."""
    seen_ids: list[str] = []

    async def inner(scope, receive, send) -> None:
        seen_ids.append(scope["state"]["request_id"])
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    mw = RequestIDMiddleware(inner)
    await mw(_make_http_scope(), _receive, AsyncMock())  # type: ignore[arg-type]
    await mw(_make_http_scope(), _receive, AsyncMock())  # type: ignore[arg-type]

    assert len(seen_ids) == 2
    assert seen_ids[0] != seen_ids[1], "two requests shared the same request_id"


@pytest.mark.asyncio
async def test_reuses_upstream_request_id_header():
    """Inbound X-Request-ID is preserved verbatim."""
    seen: dict = {}

    async def inner(scope, receive, send) -> None:
        seen["state"] = dict(scope["state"])
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    mw = RequestIDMiddleware(inner)
    scope = _make_http_scope([(b"x-request-id", b"upstream-correlation-xyz")])
    await mw(scope, _receive, AsyncMock())  # type: ignore[arg-type]

    assert seen["state"]["request_id"] == "upstream-correlation-xyz"


@pytest.mark.asyncio
async def test_emits_x_request_id_response_header():
    """Response carries X-Request-ID echoing the active value."""
    captured: list[dict] = []

    async def inner(scope, receive, send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def send_capture(msg: dict) -> None:
        captured.append(msg)

    mw = RequestIDMiddleware(inner)
    scope = _make_http_scope([(b"x-request-id", b"deadbeef")])
    await mw(scope, _receive, send_capture)  # type: ignore[arg-type]

    start = next(m for m in captured if m["type"] == "http.response.start")
    header_values = [v for (n, v) in start["headers"] if n == b"x-request-id"]
    assert header_values == [b"deadbeef"]


@pytest.mark.asyncio
async def test_lifespan_passes_through():
    """Non-HTTP scopes skip ID generation."""
    called = False

    async def inner(scope, receive, send) -> None:
        nonlocal called
        called = True

    mw = RequestIDMiddleware(inner)
    await mw({"type": "lifespan"}, AsyncMock(), AsyncMock())  # type: ignore[arg-type]
    assert called is True
