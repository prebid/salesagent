"""AdCP tool implementation.

This module contains tool implementations following the MCP/A2A shared
implementation pattern from CLAUDE.md.
"""

import logging
from typing import Any

from adcp.types.generated_poc.core.context import ContextObject
from fastmcp.server.context import Context
from fastmcp.tools.tool import ToolResult
from pydantic import ValidationError

from src.core.exceptions import AdCPAuthenticationError, AdCPNotFoundError, AdCPValidationError
from src.core.tool_context import ToolContext

logger = logging.getLogger(__name__)

from src.core.audit_logger import get_audit_logger
from src.core.auth import get_principal_object
from src.core.database.repositories import MediaBuyUoW
from src.core.helpers.adapter_helpers import get_adapter
from src.core.resolved_identity import ResolvedIdentity
from src.core.schemas import PackagePerformance, UpdatePerformanceIndexRequest, UpdatePerformanceIndexResponse
from src.core.tools.media_buy_update import _verify_principal
from src.core.validation_helpers import format_validation_error


def _update_performance_index_impl(
    media_buy_id: str,
    performance_data: list[dict[str, Any]],
    context: ContextObject | None = None,
    identity: ResolvedIdentity | None = None,
) -> UpdatePerformanceIndexResponse:
    """Shared implementation for update_performance_index (used by both MCP and A2A).

    Args:
        media_buy_id: ID of the media buy to update
        performance_data: List of performance data objects
        context: Application level context per adcp spec
        identity: Resolved identity for authentication

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
        raise AdCPValidationError(format_validation_error(e, context="update_performance_index request")) from e

    if identity is None:
        raise ValueError("Identity is required for update_performance_index")

    # Tenant is resolved at the transport boundary (resolve_identity_from_context)
    tenant = identity.tenant
    if not tenant:
        raise AdCPAuthenticationError("No tenant context available")

    with MediaBuyUoW(tenant["tenant_id"]) as uow:
        assert uow.media_buys is not None
        _verify_principal(req.media_buy_id, identity, uow.media_buys)
    principal_id = identity.principal_id
    if principal_id is None:
        raise AdCPAuthenticationError("Principal ID not found in identity - authentication required")

    # Get the Principal object
    principal = get_principal_object(principal_id, tenant_id=identity.tenant_id)
    if not principal:
        raise AdCPNotFoundError(f"Principal {principal_id} not found")

    # Get the appropriate adapter (no dry_run support for performance updates)
    adapter = get_adapter(principal, dry_run=False, tenant=tenant)

    # Convert ProductPerformance to PackagePerformance for the adapter
    package_performance = [
        PackagePerformance(package_id=perf.product_id, performance_index=perf.performance_index)
        for perf in req.performance_data
    ]

    # Call the adapter's update method
    success = adapter.update_media_buy_performance_index(req.media_buy_id, package_performance)

    # Log the performance update
    logger.info("Performance Index Update for %s", req.media_buy_id)
    for perf in req.performance_data:
        logger.info(
            "  %s: %.2f (confidence: %s)",
            perf.product_id,
            perf.performance_index,
            perf.confidence_score or "N/A",
        )

    if any(p.performance_index < 0.8 for p in req.performance_data):
        logger.info("Low performance detected for %s - optimization recommended", req.media_buy_id)

    # Log the update_performance_index call
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


async def update_performance_index(
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
    identity = (await ctx.get_state("identity")) if isinstance(ctx, Context) else None
    response = _update_performance_index_impl(media_buy_id, performance_data, context, identity)
    return ToolResult(content=str(response), structured_content=response)


def update_performance_index_raw(
    media_buy_id: str,
    performance_data: list[dict[str, Any]],
    context: ContextObject | None = None,
    ctx: Context | ToolContext | None = None,
    identity: ResolvedIdentity | None = None,
):
    """Update performance data for a media buy (raw function for A2A server use).

    Delegates to the shared implementation.

    Args:
        media_buy_id: The ID of the media buy to update performance for
        performance_data: List of performance data objects
        ctx: Context for authentication
        identity: Pre-resolved identity (if available)

    Returns:
        UpdatePerformanceIndexResponse
    """
    if identity is None:
        from src.core.transport_helpers import resolve_identity_from_context

        identity = resolve_identity_from_context(ctx, require_valid_token=True)
    return _update_performance_index_impl(media_buy_id, performance_data, context, identity)


# --- Human-in-the-Loop Task Queue Tools ---
# DEPRECATED workflow functions moved to src/core/helpers/workflow_helpers.py and imported above

# Removed get_pending_workflows - replaced by admin dashboard workflow views

# Removed assign_task - assignment handled through admin UI workflow management

# Dry run logs are now handled by the adapters themselves
