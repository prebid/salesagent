"""Request-ID propagation middleware — STUB (L0-09 Red).

Canonical spec: ``flask-to-fastapi-foundation-modules.md`` §11.9.5.

At L0-09 this module exists as a stub that emits a **fixed** request_id
so the Red tests can fail for a **semantic** reason (two requests share
the same ID; generated ID is not a UUID hex) rather than ``ImportError``.

The Green commit replaces the body with real ``uuid.uuid4().hex``
generation, upstream-value reuse, and response-header propagation per
§11.9.5 A.
"""

from __future__ import annotations

from starlette.types import ASGIApp, Receive, Scope, Send

_STUB_REQUEST_ID = "stub0000000000000000000000000000"


class RequestIDMiddleware:
    """Stub: stamps a fixed request ID. Replaced in Green."""

    def __init__(self, app: ASGIApp, header_name: str = "x-request-id") -> None:
        self.app = app
        self.header_name = header_name.lower().encode("latin-1")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        state = dict(scope.get("state", {})) if isinstance(scope, dict) else {}
        state["request_id"] = _STUB_REQUEST_ID
        if isinstance(scope, dict):
            scope["state"] = state

        await self.app(scope, receive, send)
