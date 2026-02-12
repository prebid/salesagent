"""AdCP tool implementation.

This module contains tool implementations following the MCP/A2A shared
implementation pattern from CLAUDE.md.
"""

import logging
from typing import Any

from adcp.types.generated_poc.core.context import ContextObject
from fastmcp.exceptions import ToolError
from fastmcp.server.context import Context
from fastmcp.tools.tool import ToolResult
from pydantic import ValidationError
from rich.console import Console

from src.core.tool_context import ToolContext

logger = logging.getLogger(__name__)
console = Console()

from src.core.audit_logger import get_audit_logger
from src.core.auth import get_principal_object
from src.core.config_loader import get_current_tenant
from src.core.helpers.adapter_helpers import get_adapter
from src.core.helpers.context_helpers import get_principal_id_from_context as _get_principal_id_from_context
from src.core.schemas import PackagePerformance, UpdatePerformanceIndexRequest, UpdatePerformanceIndexResponse
from src.core.tools.media_buy_update import _verify_principal
from src.core.validation_helpers import format_validation_error


def _update_performance_index_impl(
    media_buy_id: str,
    performance_data: list[dict[str, Any]],
    context: dict | None = None,
    ctx: Context | ToolContext | None = None,
) -> UpdatePerformanceIndexResponse:
    """Shared implementation for update_performance_index (used by both MCP and A2A).

    Args:
        media_buy_id: ID of the media buy to update
        performance_data: List of performance data objects
        context: Application level context per adcp spec
        ctx: FastMCP context (automatically provided)

    Returns:
        UpdatePerformanceIndexResponse with update status
    """
    # Create request object from individual parameters (MCP-compliant)
    # Convert dict performance_data to ProductPerformance objects
    from src.core.schemas import ProductPerformance

    try:
        performance_objects = [ProductPerformance(**perf) for perf in performance_data]
        req = UpdatePerformanceIndexRequest(
            media_buy_id=media_buy_id, performance_data=performance_objects, context=context
        )
    except ValidationError as e:
        raise ToolError(format_validation_error(e, context="update_performance_index request")) from e

    if ctx is None:
        raise ValueError("Context is required for update_performance_index")

    _verify_principal(req.media_buy_id, ctx)
    principal_id = _get_principal_id_from_context(ctx)  # Already verified by _verify_principal
    if principal_id is None:
        raise ToolError("Principal ID not found in context - authentication required")

    # Get the Principal object
    principal = get_principal_object(principal_id)
    if not principal:
        raise ToolError(f"Principal {principal_id} not found")

    # Get the appropriate adapter (no dry_run support for performance updates)
    adapter = get_adapter(principal, dry_run=False)

    # Convert ProductPerformance to PackagePerformance for the adapter
    package_performance = [
        PackagePerformance(package_id=perf.product_id, performance_index=perf.performance_index)
        for perf in req.performance_data
    ]

    # Call the adapter's update method
    success = adapter.update_media_buy_performance_index(req.media_buy_id, package_performance)

    # Log the performance update
    console.print(f"[bold green]Performance Index Update for {req.media_buy_id}:[/bold green]")
    for perf in req.performance_data:
        status_emoji = "📈" if perf.performance_index > 1.0 else "📉" if perf.performance_index < 1.0 else "➡️"
        console.print(
            f"  {status_emoji} {perf.product_id}: {perf.performance_index:.2f} (confidence: {perf.confidence_score or 'N/A'})"
        )

    # Simulate optimization based on performance
    if any(p.performance_index < 0.8 for p in req.performance_data):
        console.print("  [yellow]⚠️  Low performance detected - optimization recommended[/yellow]")

    # Log the update_performance_index call
    tenant = get_current_tenant()
    audit_logger = get_audit_logger("AdCP", tenant["tenant_id"])
    audit_logger.log_operation(
        operation="update_performance_index",
        principal_name=principal_id or "anonymous",
        principal_id=principal_id or "anonymous",
        adapter_id="mcp_server",
        success=success,
        details={
            "media_buy_id": req.media_buy_id,
            "product_count": len(req.performance_data),
            "avg_performance_index": (
                sum(p.performance_index for p in req.performance_data) / len(req.performance_data)
                if req.performance_data
                else 0
            ),
        },
    )

    return UpdatePerformanceIndexResponse(
        status="success" if success else "failed",
        detail=f"Performance index updated for {len(req.performance_data)} products",
        context=req.context,
    )


def update_performance_index(
    media_buy_id: str,
    performance_data: list[dict[str, Any]],
    webhook_url: str | None = None,
    context: ContextObject | None = None,
    ctx: Context | ToolContext | None = None,
):
    """Update performance index data for a media buy.

    MCP tool wrapper that delegates to the shared implementation.
    FastMCP automatically validates and coerces JSON inputs to Pydantic models.

    Args:
        media_buy_id: ID of the media buy to update
        performance_data: List of performance data objects
        webhook_url: URL for async task completion notifications (AdCP spec, optional)
        ctx: FastMCP context (automatically provided)

    Returns:
        ToolResult with UpdatePerformanceIndexResponse data
    """
    # Convert typed Pydantic models to dicts for the impl
    # FastMCP already coerced JSON inputs to these types
    context_dict = context.model_dump(mode="json") if context else None
    response = _update_performance_index_impl(media_buy_id, performance_data, context_dict, ctx)
    return ToolResult(content=str(response), structured_content=response)


def update_performance_index_raw(
    media_buy_id: str,
    performance_data: list[dict[str, Any]],
    context: dict | None = None,
    ctx: Context | ToolContext | None = None,
):
    """Update performance data for a media buy (raw function for A2A server use).

    Delegates to the shared implementation.

    Args:
        media_buy_id: The ID of the media buy to update performance for
        performance_data: List of performance data objects
        ctx: Context for authentication

    Returns:
        UpdatePerformanceIndexResponse
    """
    return _update_performance_index_impl(media_buy_id, performance_data, context, ctx)


# --- Human-in-the-Loop Task Queue Tools ---
# DEPRECATED workflow functions moved to src/core/helpers/workflow_helpers.py and imported above

# Removed get_pending_workflows - replaced by admin dashboard workflow views

# Removed assign_task - assignment handled through admin UI workflow management

# Dry run logs are now handled by the adapters themselves
