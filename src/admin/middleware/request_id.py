"""Generate and propagate ``X-Request-ID`` on every request.

Canonical spec: ``flask-to-fastapi-foundation-modules.md`` §11.9.5.

Each HTTP request gets a 32-char lowercase-hex UUID stamped on
``scope["state"]["request_id"]`` and echoed in the response
``X-Request-ID`` header. If the inbound request already carries
``X-Request-ID``, that value is reused so upstream correlation IDs
survive the middleware boundary.

Registered as the **outermost** middleware at L4+ per the canonical
middleware ordering (§11.36). At L0 this module is scaffold-only — no
wiring into ``src/app.py`` yet.

The ``structlog`` integration (``bind_request_id`` context-var) lands at
L4 per the §11.9.5 layer banner — L0-L3 use stdlib ``logging`` and
request_id is carried only on ``request.state``.
"""

from __future__ import annotations

import uuid

from starlette.types import ASGIApp, Message, Receive, Scope, Send

_HEADER_NAME = b"x-request-id"


class RequestIDMiddleware:
    """Pure-ASGI middleware: generate or reuse an ``X-Request-ID``."""

    def __init__(self, app: ASGIApp, header_name: str = "x-request-id") -> None:
        self.app = app
        self.header_name = header_name.lower().encode("latin-1")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Reuse an upstream correlation ID if present; otherwise generate.
        incoming: str | None = None
        for name, value in scope["headers"]:
            if name == self.header_name:
                try:
                    incoming = value.decode("latin-1")
                except (UnicodeDecodeError, AttributeError):
                    incoming = None
                break

        request_id = incoming or uuid.uuid4().hex

        # Stash on scope state so handlers can read via request.state.request_id.
        # ASGI spec permits scope mutation within one request's lifetime.
        scope.setdefault("state", {})
        scope["state"]["request_id"] = request_id

        rid_bytes = request_id.encode("latin-1")
        header_name = self.header_name

        async def send_with_header(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                # Replace any existing X-Request-ID to keep a single source
                # of truth; we trust our own active value on the response.
                headers = [(n, v) for (n, v) in headers if n != header_name]
                headers.append((header_name, rid_bytes))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_header)
