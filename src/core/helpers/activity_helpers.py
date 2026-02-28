"""Activity logging helpers for MCP tool execution tracking."""

import logging
import time

from fastmcp.server.context import Context
from sqlalchemy import select

from src.core.config_loader import get_current_tenant, set_current_tenant
from src.core.database.database_session import get_db_session
from src.core.database.models import Principal as ModelPrincipal
from src.core.resolved_identity import ResolvedIdentity
from src.core.tool_context import ToolContext
from src.core.transport_helpers import resolve_identity_from_context
from src.services.activity_feed import activity_feed

logger = logging.getLogger(__name__)


def log_tool_activity(context: Context | ToolContext | ResolvedIdentity, tool_name: str, start_time: float = None):
    """Log tool activity to the activity feed.

    Args:
        context: FastMCP Context, ToolContext, or ResolvedIdentity with principal/tenant info
        tool_name: Name of the tool being executed
        start_time: Optional start time for calculating response time

    Logs to both:
    - Activity feed (for WebSocket real-time updates)
    - Audit logs (for persistent dashboard activity feed)
    """
    try:
        # Handle ResolvedIdentity (transport-agnostic)
        if isinstance(context, ResolvedIdentity):
            principal_id: str | None = context.principal_id
            tenant: dict | None = context.tenant
        # Handle ToolContext directly
        elif isinstance(context, ToolContext):
            principal_id = context.principal_id
            tenant = {"tenant_id": context.tenant_id}
        else:
            # Get principal and tenant context from FastMCP Context via unified path
            identity = resolve_identity_from_context(context, require_valid_token=False, protocol="mcp")
            principal_id = identity.principal_id if identity else None
            tenant = identity.tenant if identity and isinstance(identity.tenant, dict) else None

        # Set tenant context if returned
        if tenant:
            set_current_tenant(tenant)
        else:
            tenant = get_current_tenant()

        if not tenant:
            return
        principal_name = "Unknown"

        if principal_id:
            with get_db_session() as session:
                stmt = select(ModelPrincipal).filter_by(principal_id=principal_id, tenant_id=tenant["tenant_id"])
                principal = session.scalars(stmt).first()
                if principal:
                    principal_name = principal.name

        # Calculate response time if start_time provided
        response_time_ms: int | None = None
        if start_time:
            response_time_ms = int((time.time() - start_time) * 1000)

        # Log to activity feed (for WebSocket real-time updates)
        activity_feed.log_api_call(
            tenant_id=tenant["tenant_id"],
            principal_name=principal_name,
            method=tool_name,
            status_code=200,
            response_time_ms=response_time_ms,
        )

        # Also log to audit logs (for persistent dashboard activity feed)
        from typing import Any

        from src.core.audit_logger import get_audit_logger

        audit_logger = get_audit_logger("MCP", tenant["tenant_id"])
        details: dict[str, Any] = {"tool": tool_name, "status": "success"}
        if response_time_ms:
            details["response_time_ms"] = response_time_ms

        audit_logger.log_operation(
            operation=tool_name,
            principal_name=principal_name,
            principal_id=principal_id or "anonymous",
            adapter_id="mcp_server",
            success=True,
            details=details,
        )
    except Exception as e:
        logger.debug(f"Error logging tool activity: {e}")
