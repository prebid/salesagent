"""Transport boundary helpers for creating ResolvedIdentity from transport-specific types.

These functions bridge transport-specific types (FastMCP Context, ToolContext,
A2A headers) to the transport-agnostic ResolvedIdentity used by _impl functions.

Each transport boundary calls one of these helpers before invoking _impl.
"""

from __future__ import annotations

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
    # Handle ToolContext directly (already has resolved identity info).
    # Skip when both ``tenant_id`` and ``principal_id`` are ``None`` —
    # this happens on discovery-tool calls where ``BearerTokenAuth``
    # bypasses validation at the transport gate, so ``request.state``
    # carries no principal even when the buyer sent a valid token.
    # Fall through to header-based resolution in that case.
    if isinstance(ctx, ToolContext) and (ctx.tenant_id is not None or ctx.principal_id is not None):
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

    identity = resolve_identity(
        headers=headers,
        require_valid_token=require_valid_token,
        protocol=protocol,
        testing_context=testing_context,
    )

    # If the SigningVerifyMiddleware verified an inbound signature on this
    # request, fold the verified agent_url/key into the identity so downstream
    # _impl functions and audit-log writers can record it.
    from src.core.signing.verified_state import get_verified_state

    verified = get_verified_state()
    if verified is not None:
        identity = identity.model_copy(
            update={
                "verified_agent_url": verified.agent_url,
                "verified_key_id": verified.key_id,
            }
        )

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

    if identity.tenant_id is None:
        return identity

    if isinstance(account_ref, dict):
        from adcp.types import AccountReference

        account_ref = AccountReference.model_validate(account_ref)

    from src.core.database.repositories.uow import AccountUoW
    from src.core.helpers.account_helpers import resolve_account

    with AccountUoW(identity.tenant_id) as uow:
        assert uow.accounts is not None
        account_id = resolve_account(account_ref, identity, uow.accounts)

    return identity.model_copy(update={"account_id": account_id})
