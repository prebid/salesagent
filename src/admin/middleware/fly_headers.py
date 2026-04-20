"""Fly.io header normalizer: ``Fly-*`` → ``X-Forwarded-*``.

Canonical spec: ``flask-to-fastapi-foundation-modules.md`` §11.9.

Replaces the WSGI ``FlyHeadersMiddleware`` that lived at
``src/admin/app.py:172-189`` prior to Flask removal. Running BEFORE
uvicorn's ``--proxy-headers`` logic means uvicorn sees the normalized
headers and populates ``request.url.scheme`` + ``request.client.host``
correctly under Fly.io's proxy.

The middleware operates on the scope headers list **without mutating the
input list**: when a Fly-* variant is present and the equivalent
X-Forwarded-* variant is absent, the X-Forwarded-* header is appended to
a fresh list copy and a new scope dict is created before forwarding.

STATUS: Likely redundant as of Fly.io's 2024 platform update (Fly now
sends standard ``X-Forwarded-*`` headers). Kept as ~40 LOC insurance per
canonical spec §11.9 D; revisit deletion after verifying Fly's current
header emission against our Fly Machines pool.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Ordered pairs of (Fly-specific source, standard target). ASGI requires
# header names to be lowercased bytes — verified in Starlette's request
# parser at `starlette/requests.py`.
_MAPPINGS: tuple[tuple[bytes, bytes], ...] = (
    (b"fly-forwarded-proto", b"x-forwarded-proto"),
    (b"fly-client-ip", b"x-forwarded-for"),
    (b"fly-forwarded-host", b"x-forwarded-host"),
)


class FlyHeadersMiddleware:
    """Translate Fly.io proxy headers to standard ``X-Forwarded-*``.

    Pre-existing standard headers win — a client or upstream proxy that
    already set ``X-Forwarded-Proto`` is trusted over the Fly-supplied
    value. Non-HTTP scopes (``lifespan``, ``websocket``) pass through.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers: list[tuple[bytes, bytes]] = list(scope.get("headers", []))
        existing: set[bytes] = {name for name, _ in headers}

        new_headers = list(headers)
        for fly_name, standard_name in _MAPPINGS:
            if standard_name in existing:
                # Upstream or client set the canonical header directly;
                # trust it over the Fly variant.
                continue
            for name, value in headers:
                if name == fly_name:
                    new_headers.append((standard_name, value))
                    break

        if len(new_headers) != len(headers):
            # Never mutate the input scope in place — downstream code
            # may hold a reference.
            scope = {**scope, "headers": new_headers}
        await self.app(scope, receive, send)
