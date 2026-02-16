"""List creatives implementation, MCP wrapper, and A2A raw function."""

import logging
import time
from datetime import UTC, datetime
from typing import Any, cast

from adcp import CreativeFilters
from adcp.types.generated_poc.core.context import ContextObject
from adcp.types.generated_poc.media_buy.list_creatives_request import (
    FieldModel,
    Pagination,
    Sort,
)
from fastmcp.exceptions import ToolError
from fastmcp.server.context import Context
from fastmcp.tools.tool import ToolResult
from pydantic import ValidationError
from sqlalchemy import select

from src.core.audit_logger import get_audit_logger
from src.core.config_loader import get_current_tenant
from src.core.database.database_session import get_db_session
from src.core.helpers import get_principal_id_from_context, log_tool_activity
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
    buyer_ref: str | None = None,
    buyer_refs: list[str] | None = None,
    status: str | None = None,
    format: str | None = None,
    tags: list[str] | None = None,
    created_after: str | None = None,
    created_before: str | None = None,
    search: str | None = None,
    filters: dict | None = None,
    sort: dict | None = None,
    pagination: dict | None = None,
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
) -> ListCreativesResponse:
    """List and search creative library (AdCP v2.5 spec endpoint).

    Advanced filtering and search endpoint for the centralized creative library.
    Supports pagination, sorting, and multiple filter criteria.

    Args:
        media_buy_id: Filter by single media buy ID (optional, backward compat)
        media_buy_ids: Filter by multiple media buy IDs (AdCP 2.5, optional)
        buyer_ref: Filter by single buyer reference (optional, backward compat)
        buyer_refs: Filter by multiple buyer references (AdCP 2.5, optional)
        status: Filter by creative status (pending, approved, rejected) (optional)
        format: Filter by creative format (optional)
        tags: Filter by tags (optional)
        created_after: Filter by creation date (ISO string) (optional)
        created_before: Filter by creation date (ISO string) (optional)
        search: Search in creative names and descriptions (optional)
        filters: Advanced filtering options (nested object, optional)
        sort: Sort configuration (nested object, optional)
        pagination: Pagination parameters (nested object, optional)
        fields: Specific fields to return (optional)
        include_performance: Include performance metrics (optional)
        include_assignments: Include package assignments (optional)
        include_sub_assets: Include sub-assets (optional)
        page: Page number for pagination (default: 1)
        limit: Number of results per page (default: 50, max: 1000)
        sort_by: Sort field (created_date, name, status) (default: created_date)
        sort_order: Sort order (asc, desc) (default: desc)
        context: Application level context per adcp spec
        ctx: FastMCP context (automatically provided)

    Returns:
        ListCreativesResponse with filtered creative assets and pagination info
    """
    from adcp.types import CreativeFilters as LibraryCreativeFilters
    from adcp.types import Sort as LibrarySort

    # V3: Request Pagination uses limit/offset, Response Pagination uses batch_number/total_batches
    from adcp.types.generated_poc.media_buy.list_creatives_request import Pagination as LibraryPagination

    from src.core.schemas import ListCreativesRequest

    # Parse datetime strings if provided
    created_after_dt = None
    created_before_dt = None
    if created_after:
        try:
            created_after_dt = datetime.fromisoformat(created_after.replace("Z", "+00:00"))
        except ValueError:
            raise ToolError(f"Invalid created_after date format: {created_after}")
    if created_before:
        try:
            created_before_dt = datetime.fromisoformat(created_before.replace("Z", "+00:00"))
        except ValueError:
            raise ToolError(f"Invalid created_before date format: {created_before}")

    # Validate sort_order is valid Literal
    from typing import Literal

    valid_sort_order: Literal["asc", "desc"] = cast(
        Literal["asc", "desc"], sort_order if sort_order in ["asc", "desc"] else "desc"
    )

    # Enforce max limit
    effective_limit = min(limit, 1000)

    # Build spec-compliant filters from flat parameters
    filters_dict: dict[str, Any] = {}
    if status:
        filters_dict["status"] = status
    if format:
        filters_dict["format"] = format
    if tags:
        filters_dict["tags"] = tags
    if created_after_dt:
        filters_dict["created_after"] = created_after_dt
    if created_before_dt:
        filters_dict["created_before"] = created_before_dt
    if search:
        filters_dict["name_contains"] = search

    # Build media_buy_ids and buyer_refs filter arrays
    effective_media_buy_ids = list(media_buy_ids) if media_buy_ids else []
    if media_buy_id and media_buy_id not in effective_media_buy_ids:
        effective_media_buy_ids.append(media_buy_id)
    if effective_media_buy_ids:
        filters_dict["media_buy_ids"] = effective_media_buy_ids

    effective_buyer_refs = list(buyer_refs) if buyer_refs else []
    if buyer_ref and buyer_ref not in effective_buyer_refs:
        effective_buyer_refs.append(buyer_ref)
    if effective_buyer_refs:
        filters_dict["buyer_refs"] = effective_buyer_refs

    # Merge with provided filters dict
    if filters:
        filters_dict = {**filters, **filters_dict}

    # Build structured objects
    structured_filters = LibraryCreativeFilters(**filters_dict) if filters_dict else None

    # Build pagination
    offset = (page - 1) * effective_limit
    structured_pagination = LibraryPagination(offset=offset, limit=effective_limit)

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
            include_performance=include_performance,
            include_assignments=include_assignments,
            include_sub_assets=include_sub_assets,
            context=to_context_object(context),
        )
    except ValidationError as e:
        raise ToolError(format_validation_error(e, context="list_creatives request")) from e

    start_time = time.time()

    # Authentication - REQUIRED (creatives contain sensitive data)
    # Unlike discovery endpoints (list_creative_formats), this returns actual creative assets
    # which are principal-specific and must be access-controlled
    principal_id = get_principal_id_from_context(ctx)
    if not principal_id:
        raise ToolError("Missing x-adcp-auth header")

    # Get tenant information
    tenant = get_current_tenant()
    if not tenant:
        raise ToolError("No tenant context available")

    creatives = []
    total_count = 0

    with get_db_session() as session:
        from src.core.database.models import Creative as DBCreative
        from src.core.database.models import CreativeAssignment as DBAssignment
        from src.core.database.models import MediaBuy

        # Build query - filter by tenant AND principal for security
        stmt = select(DBCreative).filter_by(tenant_id=tenant["tenant_id"], principal_id=principal_id)

        # Filter out creatives without valid assets (legacy data)
        # Using PostgreSQL JSONB ? operator to check if 'assets' key exists
        stmt = stmt.where(DBCreative.data["assets"].isnot(None))

        # Apply filters using local variables (already processed above)
        # AdCP 2.5: Support plural media_buy_ids and buyer_refs filters
        if effective_media_buy_ids:
            # Filter by media buy assignments (OR logic - matches any)
            stmt = stmt.join(DBAssignment, DBCreative.creative_id == DBAssignment.creative_id).where(
                DBAssignment.media_buy_id.in_(effective_media_buy_ids)
            )

        if effective_buyer_refs:
            # Filter by buyer_ref through media buy (OR logic - matches any)
            # Only join if not already joined for media_buy_ids
            if not effective_media_buy_ids:
                stmt = stmt.join(DBAssignment, DBCreative.creative_id == DBAssignment.creative_id)
            stmt = stmt.join(MediaBuy, DBAssignment.media_buy_id == MediaBuy.media_buy_id).where(
                MediaBuy.buyer_ref.in_(effective_buyer_refs)
            )

        if status:
            stmt = stmt.where(DBCreative.status == status)

        if format:
            stmt = stmt.where(DBCreative.format == format)

        if tags:
            # Simple tag filtering - in production, might use JSON operators
            for tag in tags:
                stmt = stmt.where(DBCreative.name.contains(tag))  # Simplified

        if created_after_dt:
            stmt = stmt.where(DBCreative.created_at >= created_after_dt)

        if created_before_dt:
            stmt = stmt.where(DBCreative.created_at <= created_before_dt)

        if search:
            # Search in name and description
            search_term = f"%{search}%"
            stmt = stmt.where(DBCreative.name.ilike(search_term))

        # Get total count before pagination
        from sqlalchemy import func
        from sqlalchemy.orm import InstrumentedAttribute

        total_count_result = session.scalar(select(func.count()).select_from(stmt.subquery()))
        total_count = int(total_count_result) if total_count_result is not None else 0

        # Apply sorting using local variables
        sort_column: InstrumentedAttribute
        if sort_by == "name":
            sort_column = DBCreative.name
        elif sort_by == "status":
            sort_column = DBCreative.status
        else:  # Default to created_date
            sort_column = DBCreative.created_at

        if valid_sort_order == "asc":
            stmt = stmt.order_by(sort_column.asc())
        else:
            stmt = stmt.order_by(sort_column.desc())

        # Apply pagination using local variables (already computed above)
        db_creatives = session.scalars(stmt.offset(offset).limit(effective_limit)).all()

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

            # Safety check: Skip creatives with empty assets (should be filtered by query, but defensive)
            if not assets_dict:
                logger.warning(
                    f"Creative {db_creative.creative_id} has empty assets dict - "
                    f"should have been filtered by query. Skipping.",
                    extra={"creative_id": db_creative.creative_id, "tenant_id": tenant["tenant_id"]},
                )
                continue

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
                # AdCP spec fields (library Creative)
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

    # Build filters_applied list from structured filters
    filters_applied: list[str] = []
    if req.filters:
        if hasattr(req.filters, "media_buy_ids") and req.filters.media_buy_ids:
            filters_applied.append(f"media_buy_ids={','.join(req.filters.media_buy_ids)}")
        if hasattr(req.filters, "buyer_refs") and req.filters.buyer_refs:
            filters_applied.append(f"buyer_refs={','.join(req.filters.buyer_refs)}")
        if hasattr(req.filters, "status") and req.filters.status:
            filters_applied.append(f"status={req.filters.status}")
        if hasattr(req.filters, "format") and req.filters.format:
            filters_applied.append(f"format={req.filters.format}")
        if hasattr(req.filters, "tags") and req.filters.tags:
            filters_applied.append(f"tags={','.join(req.filters.tags)}")
        if hasattr(req.filters, "created_after") and req.filters.created_after:
            filters_applied.append(f"created_after={req.filters.created_after.isoformat()}")
        if hasattr(req.filters, "created_before") and req.filters.created_before:
            filters_applied.append(f"created_before={req.filters.created_before.isoformat()}")
        if hasattr(req.filters, "name_contains") and req.filters.name_contains:
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
    if ctx is not None:
        log_tool_activity(ctx, "list_creatives", start_time)

    message = f"Found {len(creatives)} creatives"
    if total_count > len(creatives):
        message += f" (page {page} of {total_pages} total)"

    # Calculate offset for pagination
    offset_calc = (page - 1) * limit

    # Import required schema classes
    from src.core.schemas import Pagination as SchemaPagination
    from src.core.schemas import QuerySummary

    # Convert ContextObject to dict for response
    context_dict = req.context.model_dump() if req.context and hasattr(req.context, "model_dump") else None
    return ListCreativesResponse(
        query_summary=QuerySummary(
            total_matching=total_count,
            returned=len(creatives),
            filters_applied=filters_applied,
            sort_applied=sort_applied,
        ),
        pagination=SchemaPagination(
            limit=limit,
            offset=offset_calc,
            has_more=has_more,
            total_pages=total_pages,
            current_page=page,
        ),
        creatives=creatives,
        format_summary=None,
        status_summary=None,
        context=context_dict,
    )


async def list_creatives(
    media_buy_id: str = None,
    media_buy_ids: list[str] = None,
    buyer_ref: str = None,
    buyer_refs: list[str] = None,
    status: str = None,
    format: str = None,
    tags: list[str] = None,
    created_after: str = None,
    created_before: str = None,
    search: str = None,
    filters: CreativeFilters | None = None,
    sort: Sort | None = None,
    pagination: Pagination | None = None,
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
        buyer_ref: Filter by single buyer reference (backward compat)
        buyer_refs: Filter by multiple buyer references (AdCP 2.5)

    Returns:
        ToolResult with ListCreativesResponse data
    """
    # Convert typed Pydantic models to dicts for the impl
    # FastMCP already coerced JSON inputs to these types
    filters_dict = filters.model_dump(mode="json") if filters else None
    sort_dict = sort.model_dump(mode="json") if sort else None
    pagination_dict = pagination.model_dump(mode="json") if pagination else None
    fields_list = [f.value if isinstance(f, FieldModel) else f for f in fields] if fields else None
    context_dict = context.model_dump(mode="json") if context else None

    response = _list_creatives_impl(
        media_buy_id=media_buy_id,
        media_buy_ids=media_buy_ids,
        buyer_ref=buyer_ref,
        buyer_refs=buyer_refs,
        status=status,
        format=format,
        tags=tags,
        created_after=created_after,
        created_before=created_before,
        search=search,
        filters=filters_dict,
        sort=sort_dict,
        pagination=pagination_dict,
        fields=fields_list,
        include_performance=include_performance,
        include_assignments=include_assignments,
        include_sub_assets=include_sub_assets,
        page=page,
        limit=limit,
        sort_by=sort_by,
        sort_order=sort_order,
        context=context_dict,
        ctx=ctx,
    )
    return ToolResult(content=str(response), structured_content=response)


def list_creatives_raw(
    media_buy_id: str = None,
    media_buy_ids: list[str] = None,
    buyer_ref: str = None,
    buyer_refs: list[str] = None,
    status: str = None,
    format: str = None,
    tags: list[str] = None,
    created_after: str = None,
    created_before: str = None,
    search: str = None,
    page: int = 1,
    limit: int = 50,
    sort_by: str = "created_date",
    sort_order: str = "desc",
    context: dict | None = None,  # Application level context per adcp spec
    ctx: Context | ToolContext | None = None,
):
    """List creative assets with filtering and pagination (raw function for A2A server use, AdCP v2.5).

    Delegates to the shared implementation.

    Args:
        media_buy_id: Filter by single media buy ID (backward compat)
        media_buy_ids: Filter by multiple media buy IDs (AdCP 2.5)
        buyer_ref: Filter by single buyer reference (backward compat)
        buyer_refs: Filter by multiple buyer references (AdCP 2.5)
        status: Filter by status (optional)
        format: Filter by creative format (optional)
        tags: Filter by creative group tags (optional)
        created_after: Filter creatives created after this date (ISO format) (optional)
        created_before: Filter creatives created before this date (ISO format) (optional)
        search: Search in creative name or description (optional)
        page: Page number for pagination (default: 1)
        limit: Number of results per page (default: 50, max: 1000)
        sort_by: Sort field (default: created_date)
        sort_order: Sort order (default: desc)
        context: Application level context per adcp spec
        ctx: FastMCP context (automatically provided)

    Returns:
        ListCreativesResponse with filtered creative assets and pagination info
    """
    return _list_creatives_impl(
        media_buy_id=media_buy_id,
        media_buy_ids=media_buy_ids,
        buyer_ref=buyer_ref,
        buyer_refs=buyer_refs,
        status=status,
        format=format,
        tags=tags,
        created_after=created_after,
        created_before=created_before,
        search=search,
        page=page,
        limit=limit,
        sort_by=sort_by,
        sort_order=sort_order,
        context=context,
        ctx=ctx,
    )
