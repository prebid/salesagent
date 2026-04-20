"""Unit tests for L0-08 FlyHeadersMiddleware.

Canonical spec: ``flask-to-fastapi-foundation-modules.md`` §11.9.B.

Obligation: when a request carries ``Fly-Forwarded-*`` headers, the
middleware copies them into their standard ``X-Forwarded-*`` forms so
uvicorn's ``--proxy-headers`` logic (downstream) sees the normalized
values. Pre-existing standard headers MUST win over the Fly-copied
value.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.admin.middleware.fly_headers import FlyHeadersMiddleware


def _make_scope(headers: list[tuple[bytes, bytes]]) -> dict:
    return {"type": "http", "headers": headers}


@pytest.mark.asyncio
async def test_copies_fly_forwarded_proto_to_x_forwarded_proto():
    """Fly-Forwarded-Proto → X-Forwarded-Proto when latter is absent."""
    captured: dict = {}

    async def inner(scope: dict, receive, send) -> None:
        captured["scope"] = scope

    mw = FlyHeadersMiddleware(inner)
    scope = _make_scope([(b"fly-forwarded-proto", b"https")])
    await mw(scope, AsyncMock(), AsyncMock())

    headers = dict(captured["scope"]["headers"])
    assert headers.get(b"x-forwarded-proto") == b"https"


@pytest.mark.asyncio
async def test_copies_fly_client_ip_to_x_forwarded_for():
    """Fly-Client-IP → X-Forwarded-For when latter is absent."""
    captured: dict = {}

    async def inner(scope: dict, receive, send) -> None:
        captured["scope"] = scope

    mw = FlyHeadersMiddleware(inner)
    scope = _make_scope([(b"fly-client-ip", b"203.0.113.42")])
    await mw(scope, AsyncMock(), AsyncMock())

    headers = dict(captured["scope"]["headers"])
    assert headers.get(b"x-forwarded-for") == b"203.0.113.42"


@pytest.mark.asyncio
async def test_preserves_existing_x_forwarded_proto():
    """Pre-existing X-Forwarded-Proto wins over the Fly variant."""
    captured: dict = {}

    async def inner(scope: dict, receive, send) -> None:
        captured["scope"] = scope

    mw = FlyHeadersMiddleware(inner)
    scope = _make_scope(
        [
            (b"fly-forwarded-proto", b"https"),
            (b"x-forwarded-proto", b"http"),
        ]
    )
    await mw(scope, AsyncMock(), AsyncMock())

    vals = [v for (n, v) in captured["scope"]["headers"] if n == b"x-forwarded-proto"]
    assert vals == [b"http"]


@pytest.mark.asyncio
async def test_lifespan_passes_through_untouched():
    """Non-HTTP scopes (lifespan/websocket) skip translation."""
    called = False

    async def inner(scope: dict, receive, send) -> None:
        nonlocal called
        called = True

    mw = FlyHeadersMiddleware(inner)
    await mw({"type": "lifespan"}, AsyncMock(), AsyncMock())
    assert called is True
