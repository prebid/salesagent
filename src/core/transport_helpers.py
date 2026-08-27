"""Transport boundary helpers for creating ResolvedIdentity from transport-specific types.

These functions bridge transport-specific types (FastMCP Context, ToolContext,
A2A headers) to the transport-agnostic ResolvedIdentity used by _impl functions.

Each transport boundary calls one of these helpers before invoking _impl.
"""

from __future__ import annotations

import enum
import logging
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from adcp.types import AccountReference

from fastmcp.server.context import Context
from fastmcp.server.dependencies import get_http_headers

from src.core.resolved_identity import ResolvedIdentity, resolve_identity
from src.core.tenant_context import LazyTenantContext
from src.core.tool_context import ToolContext

logger = logging.getLogger(__name__)


class _IdentitySentinel(enum.Enum):
    """Typed sentinel distinguishing 'identity omitted' from 'identity=None'.

    ``identity: ResolvedIdentity | None`` cannot tell "caller explicitly
    passed ``identity=None``" (exercise the anonymous/no-tenant path) apart
    from "caller omitted the argument" (resolve identity from ambient
    transport context) — both look like ``None`` inside the function body.
    ``*_raw`` transport wrappers (A2A/REST) default ``identity`` to
    :data:`NOT_PROVIDED` instead of ``None`` so :func:`resolve_identity_if_not_provided`
    can tell the two apart. See salesagent-tb8c.

    A bare ``object()`` sentinel (as used by ``tests/harness/_base.py``'s
    untyped ``_NO_OVERRIDE``) fails ``mypy src/`` as the default for a
    ``ResolvedIdentity | None``-typed parameter — this Enum member is a
    ``Literal``-compatible type that can be folded into the parameter's
    union annotation.
    """

    NOT_PROVIDED = enum.auto()


#: Default value for ``*_raw`` wrapper ``identity`` parameters — means "not passed by caller".
NOT_PROVIDED = _IdentitySentinel.NOT_PROVIDED

#: Type alias for ``*_raw`` wrapper ``identity`` parameters: a resolved identity, an
#: explicit "anonymous" (``None``), or the :data:`NOT_PROVIDED` sentinel.
IdentityOrNotProvided = ResolvedIdentity | None | Literal[_IdentitySentinel.NOT_PROVIDED]


def _make_lazy_tenant(tenant_id: str) -> LazyTenantContext:
    """Create a lazy-loading tenant context for the given tenant_id.

    The DB query is deferred until a non-tenant_id field is first accessed.
    This avoids hitting the database for requests that only need tenant_id
    (the common case) or that fail auth before reaching tenant-dependent logic.
    """
    return LazyTenantContext(tenant_id)


def resolve_identity_from_context(
    ctx: Context | ToolContext | None,
    require_valid_token: bool = True,
    protocol: Literal["mcp", "a2a", "rest"] = "mcp",
) -> ResolvedIdentity | None:
    """Create ResolvedIdentity from a FastMCP Context or ToolContext.

    This is the primary bridge for MCP tool wrappers and A2A raw functions.

    Args:
        ctx: FastMCP Context or ToolContext (or None for unauthenticated)
        require_valid_token: Whether to raise on invalid tokens
        protocol: Transport protocol ("mcp", "a2a", "rest")

    Returns:
        ResolvedIdentity, or None if ctx is None and no headers available
    """
    # Handle ToolContext directly (already has resolved identity info)
    if isinstance(ctx, ToolContext):
        # Create lazy tenant — DB query deferred until a field beyond
        # tenant_id is accessed. Most _impl paths only need tenant_id
        # for DB queries, so the full load often never happens.
        tenant = _make_lazy_tenant(ctx.tenant_id)
        return ResolvedIdentity(
            principal_id=ctx.principal_id,
            tenant_id=ctx.tenant_id,
            tenant=tenant,
            protocol=protocol,
            testing_context=ctx.testing_context,
        )

    # Handle FastMCP Context — extract headers and resolve
    headers = None
    try:
        headers = get_http_headers(include_all=True)
    except Exception:
        logger.debug("get_http_headers() unavailable, trying fallback", exc_info=True)

    # Fallback to context.meta if available
    if not headers and ctx is not None:
        if hasattr(ctx, "meta") and ctx.meta and "headers" in ctx.meta:
            headers = ctx.meta["headers"]
        elif hasattr(ctx, "headers"):
            headers = ctx.headers

    if not headers:
        if ctx is None:
            return None
        # No headers available — return minimal identity
        return ResolvedIdentity(protocol=protocol)

    # Extract testing context from headers if present
    testing_context = None
    try:
        from src.core.testing_hooks import TestContext

        if ctx is not None:
            testing_context = TestContext.from_context(ctx)
    except Exception:
        logger.debug("Could not extract testing context", exc_info=True)

    return resolve_identity(
        headers=headers,
        require_valid_token=require_valid_token,
        protocol=protocol,
        testing_context=testing_context,
    )


def resolve_identity_if_not_provided(
    identity: IdentityOrNotProvided,
    ctx: Context | ToolContext | None,
    require_valid_token: bool = True,
    protocol: Literal["mcp", "a2a", "rest"] = "mcp",
) -> ResolvedIdentity | None:
    """Resolve identity from context ONLY when the caller omitted the argument.

    Shared guard for the 15 ``*_raw`` transport wrappers (A2A/REST). Callers
    default ``identity`` to :data:`NOT_PROVIDED`; an explicit ``identity=None``
    (meaning: exercise the anonymous/no-tenant path) is returned unchanged
    instead of being silently upgraded via ambient-context re-resolution.
    See salesagent-tb8c.

    Args:
        identity: The wrapper's ``identity`` parameter — a resolved identity,
            an explicit ``None``, or :data:`NOT_PROVIDED`.
        ctx: FastMCP Context or ToolContext to resolve from, if omitted.
        require_valid_token: Whether to raise on invalid tokens (only used on re-resolution).
        protocol: Transport protocol (only used on re-resolution).

    Returns:
        ``identity`` unchanged if explicitly provided (including ``None``),
        otherwise the result of :func:`resolve_identity_from_context`.
    """
    if identity is NOT_PROVIDED:
        return resolve_identity_from_context(ctx, require_valid_token=require_valid_token, protocol=protocol)
    return identity


def enrich_identity_with_account(
    identity: ResolvedIdentity | None,
    account_ref: AccountReference | None = None,
) -> ResolvedIdentity | None:
    """Enrich a ResolvedIdentity with a resolved account_id.

    Called at the transport boundary after resolve_identity(), when the request
    payload contains an AccountReference. Opens an AccountUoW, resolves the
    reference to a validated account_id, and returns an enriched identity.

    If account_ref is None or identity is None, returns identity unchanged.

    Args:
        identity: Base ResolvedIdentity from resolve_identity().
        account_ref: AccountReference from the request body (optional).

    Returns:
        ResolvedIdentity with account_id populated, or original identity if no account.
    """
    if identity is None or account_ref is None:
        return identity

    # Require an authenticated principal BEFORE resolving the account (#1417).
    # Account resolution runs at the transport boundary ahead of the _impl auth gate;
    # without this guard an unauthenticated caller (tenant resolved, principal_id=None)
    # reaches natural-key resolution, which skips the access-scope join and discloses the
    # tenant-wide match count via ACCOUNT_AMBIGUOUS. require_principal_id raises
    # AUTH_REQUIRED first, uniformly across every transport that funnels through here.
    from src.core.auth import require_principal_id

    require_principal_id(identity)

    if identity.tenant_id is None:
        return identity

    from src.core.database.repositories.uow import AccountUoW
    from src.core.helpers.account_helpers import resolve_account

    with AccountUoW(identity.tenant_id) as uow:
        assert uow.accounts is not None
        account_id = resolve_account(account_ref, identity, uow.accounts)

    return identity.model_copy(update={"account_id": account_id})
