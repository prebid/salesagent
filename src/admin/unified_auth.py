"""Admin-UI UnifiedAuthMiddleware — STUB (L0-10 Red).

Canonical spec: ``flask-to-fastapi-foundation-modules.md`` §11.4 +
§11.36 (Middleware Stack Versioning).

Resolves an admin ``Principal`` from the session cookie and stashes it on
``request.state.principal``. For unauthenticated requests on
``/admin/*``:

  - HTML/browser clients get a 302 redirect to ``/admin/login``.
  - JSON/API clients get a 401 response.

Public paths (``/admin/login``, ``/admin/auth/*``, ``/admin/public/*``,
``/admin/static/*``) bypass the gate.

At L0-10 this module exists as a **stub** that leaves
``request.state.principal = None`` and never redirects / 401s, so the
Red tests fail for **semantic** reasons (principal not populated,
unauthenticated request on ``/admin/*`` not redirected). The Green
commit replaces the body with session-backed resolution.

Distinct from the token-based MCP/A2A/REST
``src/core/auth_middleware.UnifiedAuthMiddleware`` which handles
principal-centric tokens. Admin-UI auth is session-cookie-based.
"""

from __future__ import annotations

from typing import Any


class UnifiedAuthMiddleware:
    """Stub: writes ``None`` to ``request.state.principal``. Replaced in Green."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        scope.setdefault("state", {})
        scope["state"]["principal"] = None
        await self.app(scope, receive, send)
