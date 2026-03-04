"""Shared AuthContext populated by UnifiedAuthMiddleware, consumed by handlers.

UnifiedAuthMiddleware extracts auth_token and headers BEFORE the handler runs.
Available via:
- request.state.auth_context (FastAPI routes, via scope["state"])
- get_auth_context FastAPI Depends (route signatures)
- ServerCallContext.state["auth_context"] (A2A, via AdCPCallContextBuilder)

Identity resolution (principal, tenant) happens at handler level via
resolve_identity() — this is intentional to avoid DB calls on every request.
"""

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Annotated, Any

from fastapi import Depends, Request

# Shared state key for auth context in scope["state"] and ServerCallContext.state.
# All producers/consumers must use this constant instead of a string literal.
AUTH_CONTEXT_STATE_KEY = "auth_context"


@dataclass(frozen=True)
class AuthContext:
    """Immutable per-request auth token + headers carrier.

    Populated by UnifiedAuthMiddleware (extracts token from headers).
    Identity resolution (principal_id, tenant_id) happens downstream
    via resolve_identity() at the handler level.
    """

    auth_token: str | None = None
    headers: MappingProxyType[str, str] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        # Wrap mutable dicts passed to __init__ so headers is always immutable.
        if isinstance(self.headers, dict):
            object.__setattr__(self, "headers", MappingProxyType(self.headers))

    @classmethod
    def unauthenticated(cls, *, headers: "dict[str, str] | None" = None) -> "AuthContext":
        """Factory for unauthenticated request context."""
        return cls(headers=MappingProxyType(headers or {}))


def _get_auth_context(request: Request) -> AuthContext:
    """FastAPI dependency that reads AuthContext from request.state.

    The middleware must have already populated request.state.auth_context.
    If middleware hasn't run (e.g., websocket or internal route), returns unauthenticated.
    """
    return getattr(request.state, AUTH_CONTEXT_STATE_KEY, AuthContext.unauthenticated())


# Annotated type aliases for route signatures (modern FastAPI pattern):
#   def my_route(auth_ctx: GetAuthContext):
GetAuthContext = Annotated[AuthContext, Depends(_get_auth_context)]

# Backward-compatible Depends instance (for dependency chaining):
get_auth_context: Any = Depends(_get_auth_context)


# ---------------------------------------------------------------------------
# Identity resolution dependencies (REST routes)
# ---------------------------------------------------------------------------


def _resolve_auth_dep(auth_ctx: AuthContext = get_auth_context) -> "ResolvedIdentity | None":
    """FastAPI dependency: resolve identity (auth-optional, for discovery endpoints).

    Returns ResolvedIdentity if a valid token is present, None otherwise.
    Does not raise on missing or invalid tokens.
    """
    if not auth_ctx.auth_token:
        return None

    from src.core.resolved_identity import resolve_identity

    identity = resolve_identity(
        headers=dict(auth_ctx.headers),
        auth_token=auth_ctx.auth_token,
        require_valid_token=False,
        protocol="rest",
    )

    if not identity.principal_id:
        return None

    # Set tenant ContextVar at the REST transport boundary
    if identity.tenant:
        from src.core.config_loader import set_current_tenant

        set_current_tenant(identity.tenant)

    return identity


def _require_auth_dep(auth_ctx: AuthContext = get_auth_context) -> "ResolvedIdentity":
    """FastAPI dependency: resolve identity (auth-required, raises 401 if missing).

    Returns ResolvedIdentity on success. Raises AdCPAuthenticationError if
    no token is present or the token is invalid.
    """
    from src.core.exceptions import AdCPAuthenticationError

    if not auth_ctx.auth_token:
        raise AdCPAuthenticationError("Authentication required")

    from src.core.resolved_identity import resolve_identity

    identity = resolve_identity(
        headers=dict(auth_ctx.headers),
        auth_token=auth_ctx.auth_token,
        require_valid_token=True,
        protocol="rest",
    )

    if not identity.principal_id:
        raise AdCPAuthenticationError("Authentication required")

    # Set tenant ContextVar at the REST transport boundary
    if identity.tenant:
        from src.core.config_loader import set_current_tenant

        set_current_tenant(identity.tenant)

    return identity


# Annotated type aliases for route signatures (modern FastAPI pattern):
#   def my_route(identity: ResolveAuth):
#   def my_route(identity: RequireAuth):
# Import at module level for Annotated (cannot be deferred — Annotated
# needs the real type at alias definition time).
from src.core.resolved_identity import ResolvedIdentity  # noqa: E402

ResolveAuth = Annotated[ResolvedIdentity | None, Depends(_resolve_auth_dep)]
RequireAuth = Annotated[ResolvedIdentity, Depends(_require_auth_dep)]

# Backward-compatible Depends instances (for dependency chaining):
resolve_auth: Any = Depends(_resolve_auth_dep)
require_auth: Any = Depends(_require_auth_dep)
