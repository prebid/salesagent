"""Shared AuthContext populated by UnifiedAuthMiddleware, consumed by handlers.

UnifiedAuthMiddleware extracts auth_token and headers BEFORE the handler runs.
Available via:
- request.state.auth_context (FastAPI routes, via scope["state"])
- get_current_auth_context() (anywhere, via ContextVar)
- get_auth_context FastAPI Depends (route signatures)

Identity resolution (principal, tenant) happens at handler level via
resolve_identity() — this is intentional to avoid DB calls on every request.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from fastapi import Depends, Request

# ContextVar set by UnifiedAuthMiddleware, cleaned up via try/finally.
_auth_context_var: ContextVar[AuthContext | None] = ContextVar("auth_context", default=None)


@dataclass(frozen=True)
class AuthContext:
    """Immutable per-request auth token + headers carrier.

    Populated by UnifiedAuthMiddleware (extracts token from headers).
    Identity resolution (principal_id, tenant_id) happens downstream
    via resolve_identity() at the handler level.
    """

    auth_token: str | None = None
    headers: dict[str, str] = field(default_factory=dict)

    @classmethod
    def unauthenticated(cls, *, headers: dict[str, str] | None = None) -> AuthContext:
        """Factory for unauthenticated request context."""
        return cls(headers=headers or {})


def get_current_auth_context() -> AuthContext | None:
    """Read AuthContext from ContextVar. Callable from anywhere without a Request object.

    Returns None when called outside a request scope.
    """
    return _auth_context_var.get()


def _get_auth_context(request: Request) -> AuthContext:
    """FastAPI dependency that reads AuthContext from request.state.

    The middleware must have already populated request.state.auth_context.
    If middleware hasn't run (e.g., websocket or internal route), returns unauthenticated.
    """
    return getattr(request.state, "auth_context", AuthContext.unauthenticated())


# Export as a FastAPI Depends for use in route signatures:
#   def my_route(auth_ctx: AuthContext = get_auth_context):
get_auth_context: Any = Depends(_get_auth_context)
