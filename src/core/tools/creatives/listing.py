"""List creatives implementation, MCP wrapper, and A2A raw function."""

import logging
import time
from datetime import UTC, datetime
from typing import Any, cast

from adcp import CreativeFilters
from adcp.types.generated_poc.core.context import ContextObject
from adcp.types.generated_poc.core.pagination_request import PaginationRequest
from adcp.types.generated_poc.creative.list_creatives_request import (
    Field1 as FieldModel,
)
from adcp.types.generated_poc.creative.list_creatives_request import (
    Sort,
)
from fastmcp.server.context import Context
from fastmcp.tools.tool import ToolResult
from pydantic import ValidationError

from src.core.audit_logger import get_audit_logger
from src.core.database.repositories.uow import CreativeUoW
from src.core.exceptions import AdCPAuthenticationError, AdCPValidationError
from src.core.helpers import log_tool_activity
from src.core.resolved_identity import ResolvedIdentity
from src.core.schema_helpers import to_context_object
from src.core.schemas import (
    Creative,
    ListCreativesResponse,
)
from src.core.tool_context import ToolContext
from src.core.validation_helpers import format_validation_error

logger = logging.getLogger(__name__)


def _list_creatives_impl(
    media_buy_id: str | None = None,
    media_buy_ids: list[str] | None = None,
    status: str | None = None,
    format: str | None = None,
    tags: list[str] | None = None,
    created_after: str | None = None,
    created_before: str | None = None,
    search: str | None = None,
    filters: CreativeFilters | None = None,
    fields: list[str] | None = None,
    include_performance: bool = False,
    include_assignments: bool = False,
    include_sub_assets: bool = False,
    page: int = 1,
    limit: int = 50,
    sort_by: str = "created_date",
    sort_order: str = "desc",
    context: ContextObject | None = None,  # Application level context per adcp spec
    identity: ResolvedIdentity | None = None,
) -> ListCreativesResponse:
    """List and search creative library (AdCP v2.5 spec endpoint).

    Advanced filtering and search endpoint for the centralized creative library.
    Supports pagination, sorting, and multiple filter criteria.

    Args:
        media_buy_id: Filter by single media buy ID (optional, backward compat)
        media_buy_ids: Filter by multiple media buy IDs (AdCP 2.5, optional)
        status: Filter by creative status (pending, approved, rejected) (optional)
        format: Filter by creative format (optional)
        tags: Filter by tags (optional)
        created_after: Filter by creation date (ISO string) (optional)
        created_before: Filter by creation date (ISO string) (optional)
        search: Search in creative names and descriptions (optional)
        filters: Advanced filtering options (CreativeFilters model, optional)
        fields: Specific fields to return (optional)
        include_performance: Include performance metrics (optional)
        include_assignments: Include package assignments (optional)
        include_sub_assets: Include sub-assets (optional)
        page: Page number for pagination (default: 1)
        limit: Number of results per page (default: 50, max: 1000)
        sort_by: Sort field (created_date, name, status) (default: created_date)
        sort_order: Sort order (asc, desc) (default: desc)
        context: Application level context per adcp spec
        identity: ResolvedIdentity with principal/tenant info (transport-agnostic)

    Returns:
        ListCreativesResponse with filtered creative assets and pagination info
    """
    from adcp.types import CreativeFilters as LibraryCreativeFilters
    from adcp.types.generated_poc.core.pagination_request import PaginationRequest as LibraryPagination
    from adcp.types.generated_poc.creative.list_creatives_request import Sort as LibrarySort

    from src.core.schemas import ListCreativesRequest

    # Parse datetime strings if provided
    created_after_dt = None
    created_before_dt = None
    if created_after:
        try:
            created_after_dt = datetime.fromisoformat(created_after.replace("Z", "+00:00"))
        except ValueError:
            raise AdCPValidationError(f"Invalid created_after date format: {created_after}")
    if created_before:
        try:
            created_before_dt = datetime.fromisoformat(created_before.replace("Z", "+00:00"))
        except ValueError:
            raise AdCPValidationError(f"Invalid created_before date format: {created_before}")

    # Validate sort_order is valid Literal
    from typing import Literal

    valid_sort_order: Literal["asc", "desc"] = cast(
        Literal["asc", "desc"], sort_order if sort_order in ["asc", "desc"] else "desc"
    )

    # Enforce max limit
    effective_limit = min(limit, 1000)

    # Build spec-compliant filters from flat parameters
    # Library CreativeFilters uses plural field names (statuses, formats)
    filters_dict: dict[str, Any] = {}
    if status:
        filters_dict["statuses"] = [status]
    # Note: flat 'format' param is handled by DB query directly (line ~213),
    # not via CreativeFilters. adcp 3.10 format_ids requires FormatId objects
    # which need agent_url — structured filters.format_ids handles this properly.
    if tags:
        filters_dict["tags"] = tags
    if created_after_dt:
        filters_dict["created_after"] = created_after_dt
    if created_before_dt:
        filters_dict["created_before"] = created_before_dt
    if search:
        filters_dict["name_contains"] = search

    # Build media_buy_ids filter array
    effective_media_buy_ids = list(media_buy_ids) if media_buy_ids else []
    if media_buy_id and media_buy_id not in effective_media_buy_ids:
        effective_media_buy_ids.append(media_buy_id)
    if effective_media_buy_ids:
        filters_dict["media_buy_ids"] = effective_media_buy_ids

    # Merge structured filters with flat params (flat params take precedence)
    if filters:
        filters_dict = {**filters.model_dump(exclude_none=True), **filters_dict}

    # Build structured objects
    structured_filters = LibraryCreativeFilters(**filters_dict) if filters_dict else None

    # Build pagination
    offset = (page - 1) * effective_limit
    # 3.6.0: PaginationRequest is cursor-based (max_results, cursor). DB query uses offset/limit internally.
    structured_pagination = LibraryPagination(max_results=effective_limit)

    # Build sort
    field_mapping = {
        "created_date": "created_date",
        "updated_date": "updated_date",
        "name": "name",
        "status": "status",
        "assignment_count": "assignment_count",
        "performance_score": "performance_score",
    }
    mapped_field = field_mapping.get(sort_by, "created_date")
    structured_sort = LibrarySort(field=mapped_field, direction=valid_sort_order)

    try:
        req = ListCreativesRequest(
            filters=structured_filters,
            pagination=structured_pagination,
            sort=structured_sort,
            fields=fields,
            include_assignments=include_assignments,
            context=context,
        )
    except ValidationError as e:
        raise AdCPValidationError(format_validation_error(e, context="list_creatives request")) from e

    start_time = time.time()

    # Authentication - REQUIRED (creatives contain sensitive data)
    # Unlike discovery endpoints (list_creative_formats), this returns actual creative assets
    # which are principal-specific and must be access-controlled
    principal_id = identity.principal_id if identity else None
    if not principal_id:
        raise AdCPAuthenticationError("Missing x-adcp-auth header")

    # Tenant is resolved at the transport boundary (resolve_identity_from_context)
    assert identity is not None, "identity is required for listing creatives"
    tenant = identity.tenant
    if not tenant:
        raise AdCPAuthenticationError("No tenant context available")

    creatives = []
    total_count = 0

    with CreativeUoW(tenant["tenant_id"]) as uow:
        assert uow.creatives is not None
        result = uow.creatives.get_by_principal(
            principal_id,
            status=status,
            format=format,
            tags=tags,
            created_after=created_after_dt,
            created_before=created_before_dt,
            search=search,
            media_buy_ids=effective_media_buy_ids or None,
            sort_by=sort_by,
            sort_order=valid_sort_order,
            offset=offset,
            limit=effective_limit,
        )
        db_creatives = result.creatives
        total_count = result.total_count

        # Convert to schema objects
        for db_creative in db_creatives:
            # Handle content_uri - required field even for snippet creatives
            # For snippet creatives, provide an HTML-looking URL to pass validation
            snippet = db_creative.data.get("snippet") if db_creative.data else None
            if snippet:
                content_uri = (
                    db_creative.data.get("url") or "<script>/* Snippet-based creative */</script>"
                    if db_creative.data
                    else "<script>/* Snippet-based creative */</script>"
                )
            else:
                content_uri = (
                    db_creative.data.get("url") or "https://placeholder.example.com/missing.jpg"
                    if db_creative.data
                    else "https://placeholder.example.com/missing.jpg"
                )

            # Build Creative directly with explicit types to satisfy mypy
            from src.core.schemas import FormatId, url

            # Build FormatId with optional parameters (AdCP 2.5 format templates)
            format_kwargs: dict[str, Any] = {
                "agent_url": url(db_creative.agent_url),
                "id": db_creative.format or "",
            }
            # Add format parameters if present
            if db_creative.format_parameters:
                params = db_creative.format_parameters
                if "width" in params:
                    format_kwargs["width"] = params["width"]
                if "height" in params:
                    format_kwargs["height"] = params["height"]
                if "duration_ms" in params:
                    format_kwargs["duration_ms"] = params["duration_ms"]

            format_obj = FormatId(**format_kwargs)

            # Ensure datetime fields are timezone-aware (database may store naive datetimes)
            if isinstance(db_creative.created_at, datetime):
                created_at_dt = (
                    db_creative.created_at.replace(tzinfo=UTC)
                    if db_creative.created_at.tzinfo is None
                    else db_creative.created_at
                )
            else:
                created_at_dt = datetime.now(UTC)

            if isinstance(db_creative.updated_at, datetime):
                updated_at_dt = (
                    db_creative.updated_at.replace(tzinfo=UTC)
                    if db_creative.updated_at.tzinfo is None
                    else db_creative.updated_at
                )
            else:
                updated_at_dt = datetime.now(UTC)

            # AdCP v1 spec compliant - only spec fields
            # Get assets dict from database (all production data uses AdCP v2.4 format)
            assets_dict = db_creative.data.get("assets", {}) if db_creative.data else {}

            # Convert string status to CreativeStatus enum
            from src.core.schemas import CreativeStatus

            try:
                status_enum = CreativeStatus(db_creative.status)
            except ValueError:
                # Default to pending_review if invalid status
                status_enum = CreativeStatus.pending_review

            creative = Creative(
                creative_id=db_creative.creative_id,
                name=db_creative.name,
                format_id=format_obj,
                assets=assets_dict,
                tags=db_creative.data.get("tags") if db_creative.data else None,
                # AdCP spec fields (listing Creative)
                status=status_enum,
                created_date=created_at_dt,
                updated_date=updated_at_dt,
                # Internal field (our extension)
                principal_id=db_creative.principal_id,
            )
            creatives.append(creative)

    # Calculate pagination info (page and limit have defaults from factory function)
    has_more = (page * limit) < total_count
    total_pages = (total_count + limit - 1) // limit if limit > 0 else 0

    # Build filters_applied list from structured filters (typed CreativeFilters model)
    filters_applied: list[str] = []
    if req.filters:
        if req.filters.media_buy_ids:
            filters_applied.append(f"media_buy_ids={','.join(req.filters.media_buy_ids)}")
        if req.filters.statuses:
            filters_applied.append(f"statuses={','.join(str(s) for s in req.filters.statuses)}")
        if req.filters.format_ids:
            filters_applied.append(f"format_ids={','.join(str(f) for f in req.filters.format_ids)}")
        if req.filters.tags:
            filters_applied.append(f"tags={','.join(req.filters.tags)}")
        if req.filters.created_after:
            filters_applied.append(f"created_after={req.filters.created_after.isoformat()}")
        if req.filters.created_before:
            filters_applied.append(f"created_before={req.filters.created_before.isoformat()}")
        if req.filters.name_contains:
            filters_applied.append(f"search={req.filters.name_contains}")

    # Build sort_applied dict from structured sort
    sort_applied = None
    if req.sort and req.sort.field and req.sort.direction:
        sort_applied = {"field": req.sort.field.value, "direction": req.sort.direction.value}

    # Audit logging
    audit_logger = get_audit_logger("AdCP", tenant["tenant_id"])
    audit_logger.log_operation(
        operation="list_creatives",
        principal_name=principal_id,
        principal_id=principal_id,
        adapter_id="N/A",
        success=True,
        details={
            "result_count": len(creatives),
            "total_count": total_count,
            "page": page,
            "filters_applied": filters_applied if filters_applied else None,
        },
    )

    # Log activity
    # Activity logging imported at module level
    if identity is not None:
        log_tool_activity(identity, "list_creatives", start_time)

    message = f"Found {len(creatives)} creatives"
    if total_count > len(creatives):
        message += f" (page {page} of {total_pages} total)"

    # Calculate offset for pagination
    offset_calc = (page - 1) * limit

    # Import required schema classes
    from src.core.schemas import Pagination as SchemaPagination
    from src.core.schemas import QuerySummary

    return ListCreativesResponse(
        query_summary=QuerySummary(
            total_matching=total_count,
            returned=len(creatives),
            filters_applied=filters_applied,
            sort_applied=sort_applied,
        ),
        pagination=SchemaPagination(
            has_more=has_more,
            total_count=total_count,
        ),
        creatives=creatives,
        format_summary=None,
        status_summary=None,
        context=req.context,
    )


async def list_creatives(
    media_buy_id: str = None,
    media_buy_ids: list[str] = None,
    status: str = None,
    format: str = None,
    tags: list[str] = None,
    created_after: str = None,
    created_before: str = None,
    search: str = None,
    filters: CreativeFilters | None = None,
    sort: Sort | None = None,
    pagination: PaginationRequest | None = None,
    fields: list[FieldModel | str] | None = None,
    include_performance: bool = False,
    include_assignments: bool = False,
    include_sub_assets: bool = False,
    page: int = 1,
    limit: int = 50,
    sort_by: str = "created_date",
    sort_order: str = "desc",
    webhook_url: str | None = None,
    context: ContextObject | None = None,  # Application level context per adcp spec
    ctx: Context | ToolContext | None = None,
):
    """List and filter creative assets from the centralized library (AdCP v2.5).

    MCP tool wrapper that delegates to the shared implementation.
    FastMCP automatically validates and coerces JSON inputs to Pydantic models.
    Supports both flat parameters (status, format, etc.) and nested objects (filters, sort, pagination)
    for maximum flexibility.

    Args:
        media_buy_id: Filter by single media buy ID (backward compat)
        media_buy_ids: Filter by multiple media buy IDs (AdCP 2.5)

    Returns:
        ToolResult with ListCreativesResponse data
    """
    identity = (await ctx.get_state("identity")) if isinstance(ctx, Context) else None

    # Pass typed Pydantic models directly (no model_dump conversion needed)
    fields_list = [f.value if isinstance(f, FieldModel) else f for f in fields] if fields else None

    response = _list_creatives_impl(
        media_buy_id=media_buy_id,
        media_buy_ids=media_buy_ids,
        status=status,
        format=format,
        tags=tags,
        created_after=created_after,
        created_before=created_before,
        search=search,
        filters=filters,
        fields=fields_list,
        include_performance=include_performance,
        include_assignments=include_assignments,
        include_sub_assets=include_sub_assets,
        page=page,
        limit=limit,
        sort_by=sort_by,
        sort_order=sort_order,
        context=context,
        identity=identity,
    )
    return ToolResult(content=str(response), structured_content=response)


def list_creatives_raw(
    media_buy_id: str = None,
    media_buy_ids: list[str] = None,
    status: str = None,
    format: str = None,
    tags: list[str] = None,
    created_after: str = None,
    created_before: str = None,
    search: str = None,
    filters: CreativeFilters | None = None,
    fields: list[str] | None = None,
    include_performance: bool = False,
    include_assignments: bool = False,
    include_sub_assets: bool = False,
    page: int = 1,
    limit: int = 50,
    sort_by: str = "created_date",
    sort_order: str = "desc",
    context: dict | None = None,  # Application level context per adcp spec
    ctx: Context | ToolContext | None = None,
    identity: ResolvedIdentity | None = None,
):
    """List creative assets with filtering and pagination (raw function for A2A server use, AdCP v2.5).

    Delegates to the shared implementation.

    Args:
        media_buy_id: Filter by single media buy ID (backward compat)
        media_buy_ids: Filter by multiple media buy IDs (AdCP 2.5)
        status: Filter by status (optional)
        format: Filter by creative format (optional)
        tags: Filter by creative group tags (optional)
        created_after: Filter creatives created after this date (ISO format) (optional)
        created_before: Filter creatives created before this date (ISO format) (optional)
        search: Search in creative name or description (optional)
        filters: Advanced filtering options (CreativeFilters model, optional)
        fields: Specific fields to return (optional)
        include_performance: Include performance metrics (optional)
        include_assignments: Include package assignments (optional)
        include_sub_assets: Include sub-assets (optional)
        page: Page number for pagination (default: 1)
        limit: Number of results per page (default: 50, max: 1000)
        sort_by: Sort field (default: created_date)
        sort_order: Sort order (default: desc)
        context: Application level context per adcp spec
        ctx: FastMCP context (automatically provided)
        identity: ResolvedIdentity (transport-agnostic, preferred over ctx)

    Returns:
        ListCreativesResponse with filtered creative assets and pagination info
    """
    if identity is None:
        from src.core.transport_helpers import resolve_identity_from_context

        identity = resolve_identity_from_context(ctx)

    return _list_creatives_impl(
        media_buy_id=media_buy_id,
        media_buy_ids=media_buy_ids,
        status=status,
        format=format,
        tags=tags,
        created_after=created_after,
        created_before=created_before,
        search=search,
        filters=filters,
        fields=fields,
        include_performance=include_performance,
        include_assignments=include_assignments,
        include_sub_assets=include_sub_assets,
        page=page,
        limit=limit,
        sort_by=sort_by,
        sort_order=sort_order,
        context=to_context_object(context),
        identity=identity,
    )
