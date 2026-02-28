"""Pure ASGI middleware for unified authentication token extraction.

Replaces the fragile 3-middleware chain (auth_context_middleware +
a2a_auth_middleware + ordering dependency) with a single middleware that:
- Extracts token from Authorization: Bearer or x-adcp-auth headers
- Dual-writes to scope["state"] (backs request.state) and ContextVar
- Guarantees cleanup via try/finally with ContextVar.reset()

This is a pure ASGI class, NOT BaseHTTPMiddleware, avoiding ContextVar
propagation bugs (Starlette issue #1729).
"""

from __future__ import annotations

import logging
from typing import Any

from src.core.auth_context import AuthContext, _auth_context_var

logger = logging.getLogger(__name__)


class UnifiedAuthMiddleware:
    """Pure ASGI middleware that extracts auth token and populates AuthContext.

    Sets AuthContext in both:
    - scope["state"]["auth_context"] — backs request.state for FastAPI routes
    - _auth_context_var ContextVar — accessible anywhere via get_current_auth_context()
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Extract headers from ASGI scope
        headers: dict[str, str] = {}
        token: str | None = None
        for raw_name, raw_value in scope.get("headers", []):
            name = raw_name.decode("latin-1").lower()
            value = raw_value.decode("latin-1")
            headers[name] = value

            if name == "authorization":
                auth_header = value.strip()
                if auth_header.startswith("Bearer "):
                    token = auth_header[7:]
            elif name == "x-adcp-auth" and token is None:
                token = value.strip()

        auth_ctx = AuthContext(auth_token=token, headers=headers)

        # Dual-write: scope["state"] (backs request.state) + ContextVar
        scope.setdefault("state", {})
        scope["state"]["auth_context"] = auth_ctx
        cv_token = _auth_context_var.set(auth_ctx)

        try:
            await self.app(scope, receive, send)
        finally:
            _auth_context_var.reset(cv_token)
