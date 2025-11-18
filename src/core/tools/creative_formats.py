"""AdCP tool implementation.

This module contains tool implementations following the MCP/A2A shared
implementation pattern from CLAUDE.md.
"""

import logging
import time

from fastmcp.exceptions import ToolError
from fastmcp.server.context import Context
from fastmcp.tools.tool import ToolResult
from pydantic import ValidationError

from src.core.tool_context import ToolContext

logger = logging.getLogger(__name__)

from src.core.audit_logger import get_audit_logger
from src.core.auth import get_principal_from_context
from src.core.config_loader import get_current_tenant, set_current_tenant
from src.core.schema_adapters import ListCreativeFormatsRequest, ListCreativeFormatsResponse
from src.core.validation_helpers import format_validation_error


def _list_creative_formats_impl(
    req: ListCreativeFormatsRequest | None, context: Context | ToolContext | None
) -> ListCreativeFormatsResponse:
    """List all available creative formats (AdCP spec endpoint).

    Returns formats from all registered creative agents (default + tenant-specific).
    Uses CreativeAgentRegistry for dynamic format discovery with caching.
    Supports optional filtering by type, standard_only, category, and format_ids.
    """
    start_time = time.time()

    # Use default request if none provided
    if req is None:
        req = ListCreativeFormatsRequest(type=None, standard_only=None, category=None, format_ids=None, context=None)

    # For discovery endpoints, authentication is optional
    # require_valid_token=False means invalid tokens are treated like missing tokens (discovery endpoint behavior)
    principal_id, tenant = get_principal_from_context(
        context, require_valid_token=False
    )  # Returns (None, tenant) if no/invalid auth

    # Set tenant context if returned
    if tenant:
        set_current_tenant(tenant)
    else:
        tenant = get_current_tenant()
    if not tenant:
        raise ToolError("No tenant context available")

    # Get formats from all registered creative agents via registry
    import asyncio

    from src.core.creative_agent_registry import get_creative_agent_registry

    registry = get_creative_agent_registry()

    # Run async operation - check if we're already in an async context
    try:
        # Check if there's already a running event loop
        loop = asyncio.get_running_loop()
        # We're in an async context, run in thread pool to avoid nested loop error
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(lambda: asyncio.run(registry.list_all_formats(tenant_id=tenant["tenant_id"])))
            formats = future.result()
    except RuntimeError:
        # No running loop, safe to create one
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            formats = loop.run_until_complete(registry.list_all_formats(tenant_id=tenant["tenant_id"]))
        finally:
            loop.close()

    # Apply filters from request
    if req.type:
        formats = [f for f in formats if f.type == req.type]

    if req.standard_only:
        formats = [f for f in formats if f.is_standard]

    if req.category:
        # Category maps to is_standard: "standard" -> True, "custom" -> False
        if req.category == "standard":
            formats = [f for f in formats if f.is_standard]
        elif req.category == "custom":
            formats = [f for f in formats if not f.is_standard]

    if req.format_ids:
        # Filter to only the specified format IDs
        # Extract the 'id' field from each FormatId object
        format_ids_set = {fmt.id for fmt in req.format_ids}
        # Compare format_id.id (handle both FormatId objects and strings)
        formats = [
            f for f in formats if (f.format_id.id if hasattr(f.format_id, "id") else f.format_id) in format_ids_set
        ]

    # Sort formats by type and name for consistent ordering
    # Use .value to convert enum to string for sorting (enums don't support < comparison)
    formats.sort(key=lambda f: (f.type.value, f.name))

    # Log the operation
    audit_logger = get_audit_logger("AdCP", tenant["tenant_id"])
    audit_logger.log_operation(
        operation="list_creative_formats",
        principal_name=principal_id or "anonymous",
        principal_id=principal_id or "anonymous",
        adapter_id="N/A",
        success=True,
        details={
            "format_count": len(formats),
            "standard_formats": len([f for f in formats if f.is_standard]),
            "custom_formats": len([f for f in formats if not f.is_standard]),
            "format_types": list({f.type for f in formats}),
        },
    )

    # Create response (no message/specification_version - not in adapter schema)
    response = ListCreativeFormatsResponse(formats=formats, creative_agents=None, errors=None, context=req.context)

    # Always return Pydantic model - MCP wrapper will handle serialization
    # Schema enhancement (if needed) should happen in the MCP wrapper, not here
    return response


def list_creative_formats(
    type: str | None = None,
    standard_only: bool | None = None,
    category: str | None = None,
    format_ids: list[str] | None = None,
    webhook_url: str | None = None,
    context: dict | None = None,  # Application level context per adcp spec
    ctx: Context | ToolContext | None = None,
):
    """List all available creative formats (AdCP spec endpoint).

    MCP tool wrapper that delegates to the shared implementation.

    Args:
        type: Filter by format type (audio, video, display)
        standard_only: Only return IAB standard formats
        category: Filter by format category (standard, custom)
        format_ids: Filter by specific format IDs
        webhook_url: URL for async task completion notifications (AdCP spec, optional)
        ctx: FastMCP context (automatically provided)

    Returns:
        ToolResult with ListCreativeFormatsResponse data
    """
    try:
        # Convert list[str] format_ids to list[FormatId] if provided
        from src.core.schemas import FormatId

        format_ids_objects = None
        if format_ids:
            # For MCP tools, format_ids are simple strings, but FormatId requires agent_url
            # Use empty string as placeholder since we'll filter by ID only
            format_ids_objects = [FormatId(id=fid, agent_url="") for fid in format_ids]  # type: ignore[arg-type]

        req = ListCreativeFormatsRequest(
            type=type,
            standard_only=standard_only,
            category=category,
            format_ids=format_ids_objects,
            context=context,
        )
    except ValidationError as e:
        raise ToolError(format_validation_error(e, context="list_creative_formats request")) from e

    response = _list_creative_formats_impl(req, ctx)
    return ToolResult(content=str(response), structured_content=response.model_dump())


def list_creative_formats_raw(
    req: ListCreativeFormatsRequest | None = None,
    ctx: Context | ToolContext | None = None,
) -> ListCreativeFormatsResponse:
    """List all available creative formats (raw function for A2A server use).

    Delegates to shared implementation.

    Args:
        req: Optional request with filter parameters
        ctx: FastMCP context

    Returns:
        ListCreativeFormatsResponse with all available formats
    """
    return _list_creative_formats_impl(req, ctx)
