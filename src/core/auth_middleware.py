"""Pure ASGI middleware for unified authentication token extraction.

Replaces the fragile 3-middleware chain (auth_context_middleware +
a2a_auth_middleware + ordering dependency) with a single middleware that:
- Extracts token from Authorization: Bearer or x-adcp-auth headers
- Writes to scope["state"] (backs request.state)

This is a pure ASGI class, NOT BaseHTTPMiddleware, avoiding ContextVar
propagation bugs (Starlette issue #1729).
"""

from __future__ import annotations

import logging
from typing import Any

from src.core.auth_context import AuthContext

logger = logging.getLogger(__name__)


class UnifiedAuthMiddleware:
    """Pure ASGI middleware that extracts auth token and populates AuthContext.

    Sets AuthContext in scope["state"]["auth_context"], which backs
    request.state for FastAPI routes and is read by AdCPCallContextBuilder
    for A2A.
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

        scope.setdefault("state", {})
        scope["state"]["auth_context"] = auth_ctx

        await self.app(scope, receive, send)
