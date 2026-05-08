"""Detect inbound transport (MCP vs A2A) and stash it on a ContextVar.

The framework's :class:`RequestContext` doesn't carry the inbound transport
identifier — auth and tenant are exposed via ContextVars
(``adcp.server.auth.current_principal`` etc.) but not the request's
transport. Webhook payload shape selection is transport-dependent
(A2A buyers receive ``Task``/``TaskStatusUpdateEvent`` envelopes;
MCP buyers receive ``McpWebhookPayload``), so platform methods need
this signal.

This middleware mirrors the URL-path detection :class:`SpecDefaultsMiddleware`
already uses to dispatch wire-shape patches: ``/mcp`` or ``/mcp/*`` paths
are MCP, the host root (``/``, ``/.well-known/agent-card.json``) is A2A,
and admin paths are neither (don't fire). The detection runs early in
the ASGI chain so any downstream code (including ``_build_identity`` in
``core.platforms._delegate``) can read the transport via
:data:`current_transport.get()`.

ContextVars are reset in ``finally`` so a later task on the same loop
slot doesn't inherit the previous request's transport — same pattern
as the SDK's auth ContextVars.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Literal

from starlette.types import ASGIApp, Receive, Scope, Send

# Module-level ContextVar populated by the middleware, read by
# ``core.platforms._delegate._build_identity`` (and any other code that
# needs to know the inbound transport). ``None`` when no middleware ran
# (unit tests, lifespan events, admin paths) — callers MUST tolerate it.
current_transport: ContextVar[Literal["mcp", "a2a"] | None] = ContextVar("salesagent_current_transport", default=None)


# Admin / static / auth prefixes — don't carry buyer-protocol traffic.
# Mirrors the skip list in ``SpecDefaultsMiddleware._patch_body``.
_ADMIN_PREFIXES: tuple[str, ...] = (
    "/admin",
    "/static",
    "/auth",
    "/tenant",
    "/api",
    "/login",
    "/logout",
)


def _classify_path(path: str) -> Literal["mcp", "a2a"] | None:
    """Return the transport for a buyer-protocol path, or ``None`` for
    admin/static/auth paths that don't fire webhooks."""
    if path == "/mcp" or path.startswith("/mcp/"):
        return "mcp"
    if path.startswith(_ADMIN_PREFIXES):
        return None
    # Host root and agent-card discovery are A2A.
    return "a2a"


class TransportDetectMiddleware:
    """ASGI middleware that sets :data:`current_transport` based on the
    inbound URL path. Must run BEFORE the SDK's auth chain so downstream
    handlers (including platform-method dispatch) see the transport.

    No-op for non-HTTP scopes (lifespan, websocket).
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        transport = _classify_path(path)
        token = current_transport.set(transport)
        try:
            await self.app(scope, receive, send)
        finally:
            current_transport.reset(token)
