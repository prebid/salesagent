"""Transport boundary helpers for creating ResolvedIdentity from transport-specific types.

These functions bridge transport-specific types (FastMCP Context, ToolContext,
A2A headers) to the transport-agnostic ResolvedIdentity used by _impl functions.

Each transport boundary calls one of these helpers before invoking _impl.
"""

import logging
from typing import Literal

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
        pass

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
        pass

    return resolve_identity(
        headers=headers,
        require_valid_token=require_valid_token,
        protocol=protocol,
        testing_context=testing_context,
    )
