"""``WWWAuthenticateMiddleware`` injects ``WWW-Authenticate: Bearer`` on 401
responses missing the header.

RFC 6750 §3 requires every 401 from a Bearer-protected resource to advertise
the auth scheme on ``WWW-Authenticate``. The MCP-leg
``BearerTokenAuthMiddleware`` upstream returns 401 without it on missing/
invalid tokens; this middleware closes the gap and is asserted by the
``security_baseline/probe_unauth`` storyboard.
"""

from __future__ import annotations

import pytest

from core.middleware.www_authenticate import WWWAuthenticateMiddleware


class _CaptureSend:
    """Collect every ASGI ``send`` message for later inspection."""

    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def __call__(self, message: dict) -> None:
        self.messages.append(message)


def _start(messages: list[dict]) -> dict:
    return next(m for m in messages if m.get("type") == "http.response.start")


def _headers(start_message: dict) -> list[tuple[bytes, bytes]]:
    return list(start_message.get("headers") or [])


def _has_www_authenticate(headers: list[tuple[bytes, bytes]]) -> bool:
    return any(name.lower() == b"www-authenticate" for name, _ in headers)


def _get_www_authenticate(headers: list[tuple[bytes, bytes]]) -> bytes | None:
    for name, value in headers:
        if name.lower() == b"www-authenticate":
            return value
    return None


def _make_app(status: int, headers: list[tuple[bytes, bytes]] | None = None):
    """Build a minimal ASGI app that returns ``status`` with optional headers."""

    async def app(scope, receive, send):
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": headers or [],
            }
        )
        await send({"type": "http.response.body", "body": b""})

    return app


@pytest.mark.asyncio
async def test_injects_bearer_challenge_on_401_when_missing() -> None:
    """The canonical case: an inner middleware returned 401 without
    ``WWW-Authenticate``. The middleware must add the bare Bearer challenge."""
    app = _make_app(401, headers=[(b"content-type", b"application/json")])
    capture = _CaptureSend()
    await WWWAuthenticateMiddleware(app)({"type": "http", "method": "POST", "path": "/mcp"}, lambda: None, capture)
    start = _start(capture.messages)
    headers = _headers(start)
    assert _has_www_authenticate(headers), (
        f"401 response must carry WWW-Authenticate header (RFC 6750 §3); got headers={headers!r}"
    )
    assert _get_www_authenticate(headers) == b"Bearer"


@pytest.mark.asyncio
async def test_does_not_overwrite_existing_www_authenticate() -> None:
    """When an inner middleware (A2A leg, SigningVerifyMiddleware) already
    set a richer challenge — e.g. with ``error="invalid_token"`` — the
    middleware must NOT replace it. The richer diagnostic wins."""
    inner_challenge = b'Bearer realm="a2a", error="invalid_token"'
    app = _make_app(401, headers=[(b"www-authenticate", inner_challenge)])
    capture = _CaptureSend()
    await WWWAuthenticateMiddleware(app)({"type": "http", "method": "POST", "path": "/"}, lambda: None, capture)
    headers = _headers(_start(capture.messages))
    assert _get_www_authenticate(headers) == inner_challenge


@pytest.mark.asyncio
async def test_case_insensitive_header_lookup() -> None:
    """Upstream sometimes capitalises the header name. The presence check
    must be case-insensitive so we don't double-inject."""
    inner_challenge = b'Bearer realm="x"'
    app = _make_app(401, headers=[(b"WWW-Authenticate", inner_challenge)])
    capture = _CaptureSend()
    await WWWAuthenticateMiddleware(app)({"type": "http", "method": "GET", "path": "/"}, lambda: None, capture)
    headers = _headers(_start(capture.messages))
    matches = [v for name, v in headers if name.lower() == b"www-authenticate"]
    assert matches == [inner_challenge], (
        f"Case-insensitive presence check must prevent duplicate header injection; got {matches!r}"
    )


@pytest.mark.parametrize("status", [200, 201, 204, 301, 302, 400, 403, 404, 500, 502, 503])
@pytest.mark.asyncio
async def test_non_401_responses_pass_through_unchanged(status: int) -> None:
    """Only 401 triggers injection. 2xx / 3xx / 4xx-other / 5xx must be
    untouched — the Bearer challenge on a 200 OK or a 403 Forbidden would
    confuse buyer agents about why their request was rejected."""
    app = _make_app(status, headers=[(b"content-type", b"application/json")])
    capture = _CaptureSend()
    await WWWAuthenticateMiddleware(app)({"type": "http", "method": "POST", "path": "/mcp"}, lambda: None, capture)
    headers = _headers(_start(capture.messages))
    assert not _has_www_authenticate(headers), (
        f"Status {status} response must NOT carry an injected WWW-Authenticate; got headers={headers!r}"
    )


@pytest.mark.asyncio
async def test_websocket_and_lifespan_scopes_pass_through() -> None:
    """The middleware only inspects ``http`` scope types — ``websocket``
    and ``lifespan`` must reach the inner app untouched. A lifespan
    startup that tried to send a ``http.response.start`` would be the
    only edge case, but lifespan messages never carry that type."""
    seen: list[dict] = []

    async def inner_app(scope, receive, send):
        seen.append(scope)
        await send({"type": "lifespan.startup.complete"})

    capture = _CaptureSend()
    await WWWAuthenticateMiddleware(inner_app)({"type": "lifespan"}, lambda: None, capture)
    assert seen == [{"type": "lifespan"}]
    assert capture.messages == [{"type": "lifespan.startup.complete"}]
