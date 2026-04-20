"""Security headers middleware — Red stub.

L0-27 Red state: intentional no-op (sets no response headers) so the
behavioral obligation tests in
``tests/unit/admin/middleware/test_security_headers.py`` fail at
assertion time. Green replacement arrives in the next commit.
"""

from __future__ import annotations

from starlette.types import ASGIApp, Receive, Scope, Send


class SecurityHeadersMiddleware:
    """Stub — passes through without modifying response headers."""

    def __init__(self, app: ASGIApp, *, https_only: bool = True) -> None:
        self.app = app
        self.https_only = https_only

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self.app(scope, receive, send)
