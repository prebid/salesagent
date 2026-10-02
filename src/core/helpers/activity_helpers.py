"""Activity logging helpers for MCP tool execution tracking."""

import logging
import time

from src.core.resolved_identity import PublicIdentity
from src.services.activity_feed import activity_feed

logger = logging.getLogger(__name__)


def log_tool_activity(identity: PublicIdentity, tool_name: str, start_time: float | None = None):
    """Log tool activity to the activity feed.

    Args:
        identity: the resolved caller, carrying principal and tenant
        tool_name: Name of the tool being executed
        start_time: Optional start time for calculating response time

    Logs to both:
    - Activity feed (for WebSocket real-time updates)
    - Audit logs (for persistent dashboard activity feed)
    """
    try:
        principal_id = identity.principal_id
        tenant = identity.tenant

        # The identity carries the principal the resolver loaded, name included; nothing
        # here loads a row.
        principal_name = identity.principal.name if identity.principal is not None else "Unknown"

        # Calculate response time if start_time provided
        response_time_ms: int | None = None
        if start_time:
            response_time_ms = int((time.time() - start_time) * 1000)

        # Log to activity feed (for WebSocket real-time updates)
        activity_feed.log_api_call(
            tenant_id=tenant.tenant_id,
            principal_name=principal_name,
            method=tool_name,
            status_code=200,
            response_time_ms=response_time_ms,
        )

        # Also log to audit logs (for persistent dashboard activity feed)
        from typing import Any

        from src.core.audit_logger import get_audit_logger

        audit_logger = get_audit_logger("MCP", tenant.tenant_id)
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
