"""Shared AuthContext populated by middleware, consumed by handlers via FastAPI dependency.

Middleware resolves auth + tenant BEFORE the handler runs. Handlers receive
AuthContext as a resolved dependency — zero auth logic in handlers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fastapi import Depends, Request


@dataclass(frozen=True)
class AuthContext:
    """Immutable per-request authentication context.

    Populated by auth_context_middleware before handlers run.
    Read by handlers via the get_auth_context() dependency.
    """

    tenant_id: str | None = None
    principal_id: str | None = None
    auth_token: str | None = None
    headers: dict[str, str] = field(default_factory=dict)

    def is_authenticated(self) -> bool:
        """True if a valid principal was resolved."""
        return self.principal_id is not None

    @classmethod
    def unauthenticated(cls, *, headers: dict[str, str] | None = None) -> AuthContext:
        """Factory for unauthenticated request context."""
        return cls(headers=headers or {})


def _get_auth_context(request: Request) -> AuthContext:
    """FastAPI dependency that reads AuthContext from request.state.

    The middleware must have already populated request.state.auth_context.
    If middleware hasn't run (e.g., websocket or internal route), returns unauthenticated.
    """
    return getattr(request.state, "auth_context", AuthContext.unauthenticated())


# Export as a FastAPI Depends for use in route signatures:
#   def my_route(auth_ctx: AuthContext = get_auth_context):
get_auth_context: Any = Depends(_get_auth_context)
