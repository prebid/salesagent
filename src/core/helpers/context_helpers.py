"""Context extraction helpers for MCP tools."""

from fastmcp.server.context import Context

from src.core.auth import get_principal_from_context
from src.core.config_loader import set_current_tenant
from src.core.tool_context import ToolContext


def get_principal_id_from_context(context: Context | ToolContext | None) -> str | None:
    """Extract principal ID from context.

    Handles both FastMCP Context (from MCP protocol) and ToolContext (from A2A protocol).
    Wrapper around get_principal_from_context that returns just the principal_id
    and sets the tenant context.

    Args:
        context: FastMCP Context or ToolContext

    Returns:
        Principal ID string, or None if not authenticated
    """
    # Handle ToolContext (from A2A server) - it already has principal_id and tenant_id
    if isinstance(context, ToolContext):
        # Set tenant context from ToolContext
        set_current_tenant({"tenant_id": context.tenant_id})
        return context.principal_id

    # Handle FastMCP Context (from MCP protocol)
    principal_id, tenant = get_principal_from_context(context)
    # Set tenant context if found
    if tenant:
        set_current_tenant(tenant)
    return principal_id
