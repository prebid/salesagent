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
from types import MappingProxyType
from typing import Any

from src.core.auth_context import AUTH_CONTEXT_STATE_KEY, AuthContext

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
        for raw_name, raw_value in scope.get("headers", []):
            name = raw_name.decode("latin-1").lower()
            value = raw_value.decode("latin-1")
            headers[name] = value

        # Token extraction: x-adcp-auth takes priority (AdCP convention),
        # then Authorization: Bearer (case-insensitive per RFC 7235 §2.1).
        token: str | None = None
        x_adcp = headers.get("x-adcp-auth", "").strip()
        if x_adcp:
            token = x_adcp
        else:
            auth_header = headers.get("authorization", "").strip()
            if auth_header.lower().startswith("bearer "):
                potential = auth_header[7:].strip()
                token = potential or None

        auth_ctx = AuthContext(auth_token=token, headers=MappingProxyType(headers))

        scope.setdefault("state", {})
        scope["state"][AUTH_CONTEXT_STATE_KEY] = auth_ctx

        await self.app(scope, receive, send)
