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
from src.core.tool_context import ToolContext

logger = logging.getLogger(__name__)


def _load_full_tenant(tenant_id: str) -> "TenantContext":
    """Load tenant from DB and return as TenantContext model.

    Falls back to minimal TenantContext if DB is unavailable (e.g., unit tests).
    Also sets the ContextVar via set_current_tenant() for legacy code paths.
    """
    from sqlalchemy.exc import SQLAlchemyError

    from src.core.config_loader import get_tenant_by_id, set_current_tenant
    from src.core.tenant_context import TenantContext

    try:
        tenant_dict = get_tenant_by_id(tenant_id)
        if tenant_dict:
            set_current_tenant(tenant_dict)
            return TenantContext.from_dict(tenant_dict)
    except (SQLAlchemyError, RuntimeError) as e:
        logger.debug(f"Could not load tenant from database: {e}")

    # Fallback: minimal TenantContext (unit tests, DB unavailable)
    fallback = TenantContext(tenant_id=tenant_id)
    set_current_tenant(fallback)  # type: ignore[arg-type]  # TenantContext supports dict protocol
    return fallback


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
        # Load FULL tenant from DB — mirrors the side effect of the old
        # get_principal_id_from_context() that was lost in the #1050 migration.
        tenant = _load_full_tenant(ctx.tenant_id)
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
