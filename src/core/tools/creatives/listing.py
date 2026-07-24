"""List creatives implementation, MCP wrapper, and A2A raw function."""

import logging
import time
from datetime import UTC, datetime
from typing import Annotated, Any, cast

from adcp import CreativeFilters
from adcp.types import ContextObject, PaginationRequest
from adcp.types.generated_poc.creative.list_creatives_request import (
    Field1 as FieldModel,
)
from adcp.types.generated_poc.creative.list_creatives_request import (
    Sort,
)
from fastmcp.server.context import Context
from fastmcp.tools.tool import ToolResult
from pydantic import Field as PydanticField

from src.core.audit_logger import get_audit_logger
from src.core.auth import require_identity, require_principal_id, require_tenant
from src.core.database.repositories.uow import CreativeUoW
from src.core.exceptions import AdCPValidationError
from src.core.helpers import enum_value, log_tool_activity
from src.core.resolved_identity import ResolvedIdentity
from src.core.schema_helpers import to_context_object
from src.core.schemas import (
    Creative,
    ListCreativesRequest,
    ListCreativesResponse,
)
from src.core.tool_context import ToolContext
from src.core.validation_helpers import adcp_validation_boundary

logger = logging.getLogger(__name__)


def _coerce_concept_value(value: Any) -> str | None:
    """Coerce an untyped concept blob value to the spec's string type.

    ``concept_id``/``concept_name`` are strings per the AdCP response schema but
    live in the untyped JSON ``data`` blob, where an out-of-band producer may write
    a non-string scalar (e.g. a numeric CM360 group id). Scalars are stringified.
    A non-scalar (list/dict) is corrupt for a string field, so it is dropped with a
    warning — surfaced in logs (No Quiet Failures) rather than projected as a Python
    repr — instead of crashing the whole listing on one bad row.
    """
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, (int, float)):  # bool is an int subclass; str(True)="True" is acceptable
        return str(value)
    logger.warning("Dropping non-scalar concept value of type %s from creative listing", type(value).__name__)
    return None


def _merge_structured_filters(filters: "CreativeFilters | None", flat_params: dict) -> dict:
    """Merge a structured CreativeFilters model into flat params (flat take precedence).

    The model->dict conversion lives in this helper rather than inside the _impl
    because it is internal request normalization, not the wire serialization that
    the no-model_dump-in-_impl guard targets.
    """
    if filters:
        return {**filters.model_dump(exclude_none=True), **flat_params}
    return flat_params


def _build_list_creatives_request(
    media_buy_id: str | None = None,
    media_buy_ids: list[str] | None = None,
    status: str | None = None,
    tags: list[str] | None = None,
    created_after: str | None = None,
    created_before: str | None = None,
    search: str | None = None,
    filters: "CreativeFilters | None" = None,
    fields: list[str] | None = None,
    include_assignments: bool = False,
    limit: int = 50,
    sort_by: str = "created_date",
    sort_order: str = "desc",
    context: ContextObject | None = None,
) -> "ListCreativesRequest":
    """Build a ListCreativesRequest from individual wire params.

    Folds the flat filter/sort/pagination params (status, tags, search, dates,
    media_buy_ids, sort_by/sort_order, limit) into the spec-compliant structured
    request, merges any structured ``filters`` (flat take precedence), and
    translates Pydantic/parse errors into AdCPValidationError. Shared by both
    transport wrappers so this construction lives in one place.

    Note: ``format``, ``page``, ``include_performance`` and ``include_sub_assets``
    are NOT representable on ListCreativesRequest and stay as out-of-band _impl
    kwargs.
    """
    from adcp.types import CreativeFilters as LibraryCreativeFilters
    from adcp.types import PaginationRequest as LibraryPagination
    from adcp.types.generated_poc.creative.list_creatives_request import Sort as LibrarySort

    # Parse datetime strings if provided
    created_after_dt = None
    created_before_dt = None
    if created_after:
        try:
            created_after_dt = datetime.fromisoformat(created_after.replace("Z", "+00:00"))
        except ValueError:
            raise AdCPValidationError(
                f"Invalid created_after date format: {created_after}",
                field="created_after",
                suggestion="Provide 'created_after' as an ISO 8601 datetime (e.g. 2026-01-01T00:00:00Z) and resend.",
            )
    if created_before:
        try:
            created_before_dt = datetime.fromisoformat(created_before.replace("Z", "+00:00"))
        except ValueError:
            raise AdCPValidationError(
                f"Invalid created_before date format: {created_before}",
                field="created_before",
                suggestion="Provide 'created_before' as an ISO 8601 datetime (e.g. 2026-01-01T00:00:00Z) and resend.",
            )

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
    # Note: flat 'format' param is handled by DB query directly in _impl,
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
    filters_dict = _merge_structured_filters(filters, filters_dict)

    # Build structured objects
    structured_filters = LibraryCreativeFilters(**filters_dict) if filters_dict else None

    # Build pagination
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

    with adcp_validation_boundary(context="list_creatives request"):
        return ListCreativesRequest(
            filters=structured_filters,
            pagination=structured_pagination,
            sort=structured_sort,
            fields=fields,
            include_assignments=include_assignments,
            context=context,
        )


def _list_creatives_impl(
    req: "ListCreativesRequest",
    format: str | None = None,
    include_performance: bool = False,
    include_sub_assets: bool = False,
    page: int = 1,
    identity: ResolvedIdentity | None = None,
) -> ListCreativesResponse:
    """List and search creative library (AdCP v2.5 spec endpoint).

    Advanced filtering and search endpoint for the centralized creative library.
    Supports pagination, sorting, and multiple filter criteria.

    Args:
        req: Typed list-creatives request (filters, sort, pagination, fields, context)
        format: Filter by creative format — out-of-band (not a request field)
        include_performance: Include performance metrics — out-of-band (not a request field)
        include_sub_assets: Include sub-assets — out-of-band (not a request field)
        page: Page number for pagination (default: 1) — out-of-band (pagination is cursor-based)
        identity: ResolvedIdentity with principal/tenant info (transport-agnostic)

    Returns:
        ListCreativesResponse with filtered creative assets and pagination info
    """
    from typing import Literal

    # Derive flat DB-query params from the structured request.
    req_filters = req.filters
    # statuses filter (CreativeFilters.statuses in core/creative-filters.json, "match any
    # of these"): thread the full structured list into the DB query, not just the first
    # status — otherwise a buyer's multi-status filter is silently narrowed and the
    # response misrepresents what shaped the result set. The flat `status` param is already
    # folded into req_filters.statuses (flat wins) by _build_list_creatives_request, so this
    # merged list is the single source of truth — filters_applied reports it verbatim below.
    # CreativeStatus enums -> string values to match the String status column (mirrors
    # MediaBuyRepository.get_by_principal's list[str]).
    effective_statuses = [enum_value(s) for s in req_filters.statuses] if req_filters and req_filters.statuses else None
    tags = req_filters.tags if req_filters else None
    created_after_dt = req_filters.created_after if req_filters else None
    created_before_dt = req_filters.created_before if req_filters else None
    search = req_filters.name_contains if req_filters else None
    effective_media_buy_ids = list(req_filters.media_buy_ids) if req_filters and req_filters.media_buy_ids else []
    # v3.1 concept_ids filter has no flat equivalent — it arrives only via the structured
    # filters object and must be threaded into the DB query (not merely reported in
    # filters_applied), or it would be silently dropped. (#1493)
    effective_concept_ids = req_filters.concept_ids if req_filters else None

    sort_by = enum_value(req.sort.field) if req.sort and req.sort.field else "created_date"
    valid_sort_order: Literal["asc", "desc"] = cast(
        Literal["asc", "desc"],
        enum_value(req.sort.direction) if req.sort and req.sort.direction else "desc",
    )

    effective_limit = min(req.pagination.max_results, 1000) if req.pagination and req.pagination.max_results else 50
    # Page is out-of-band (cursor-based pagination has no page index); preserve offset math.
    limit = effective_limit
    offset = (page - 1) * effective_limit

    start_time = time.time()

    # Authentication - REQUIRED (creatives contain sensitive data)
    # Unlike discovery endpoints (list_creative_formats), this returns actual creative assets
    # which are principal-specific and must be access-controlled
    # require_principal_id first so the canonical auth message surfaces for missing/anonymous auth;
    # require_identity narrows the type for the tenant lookup below.
    principal_id = require_principal_id(identity, context=req.context)
    identity = require_identity(identity, context=req.context)
    tenant = require_tenant(identity, context=req.context)

    creatives = []
    total_count = 0

    with CreativeUoW(tenant["tenant_id"]) as uow:
        assert uow.creatives is not None
        result = uow.creatives.get_by_principal(
            principal_id,
            statuses=effective_statuses,
            format=format,
            tags=tags,
            created_after=created_after_dt,
            created_before=created_before_dt,
            search=search,
            media_buy_ids=effective_media_buy_ids or None,
            concept_ids=effective_concept_ids,
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

            # v3.1 concept grouping. AdCP exposes concept_id/concept_name on the
            # list_creatives RESPONSE (a creative's concept membership, sourced from
            # the buyer's creative-management platform — Flashtalking/Celtra/CM360),
            # but standardizes no concept INPUT on sync_creatives, so the field is
            # populated out-of-band into the data blob. (A seller-side mapping of GAM
            # creative groups -> these fields is a separate enrichment/fallback
            # follow-up (#1506), not the authoritative buyer-side concept.) The blob is
            # untyped and an external producer may write numeric group ids, so coerce
            # to the spec's string type via _coerce_concept_value rather than letting a
            # non-string value fail Creative validation and crash the whole listing.
            concept_data = db_creative.data or {}

            creative = Creative(
                creative_id=db_creative.creative_id,
                name=db_creative.name,
                format_id=format_obj,
                assets=assets_dict,
                # FIXME(#1508): raw untyped blob into typed list[str] — a malformed
                # tags value (bare string, or [1, 2]) crashes the whole listing, the
                # same hazard _coerce_concept_value handles for concept fields.
                tags=db_creative.data.get("tags") if db_creative.data else None,
                # AdCP spec fields (listing Creative)
                status=status_enum,
                created_date=created_at_dt,
                updated_date=updated_at_dt,
                concept_id=_coerce_concept_value(concept_data.get("concept_id")),
                concept_name=_coerce_concept_value(concept_data.get("concept_name")),
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
        if effective_statuses:
            # Report the value actually applied to the query (see above) so filters_applied
            # can't drift from what scoped the result set. effective_statuses is already
            # enum_value-coerced to "approved", not the "CreativeStatus.approved" str(enum) emits.
            filters_applied.append(f"statuses={','.join(effective_statuses)}")
        if req.filters.format_ids:
            filters_applied.append(f"format_ids={','.join(str(f) for f in req.filters.format_ids)}")
        if req.filters.tags:
            filters_applied.append(f"tags={','.join(req.filters.tags)}")
        if req.filters.concept_ids:
            filters_applied.append(f"concept_ids={','.join(req.filters.concept_ids)}")
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
    media_buy_id: Annotated[str | None, PydanticField(description="Filter creatives by a single media buy ID")] = None,
    media_buy_ids: list[str] = None,
    status: Annotated[
        str | None, PydanticField(description="Filter by creative status (e.g. 'approved', 'pending', 'rejected')")
    ] = None,
    format: Annotated[str | None, PydanticField(description="Filter by creative format ID")] = None,
    tags: list[str] = None,
    created_after: Annotated[
        str, PydanticField(description="Filter creatives created after this ISO 8601 datetime")
    ] = None,
    created_before: Annotated[
        str, PydanticField(description="Filter creatives created before this ISO 8601 datetime")
    ] = None,
    search: Annotated[
        str | None, PydanticField(description="Free-text search across creative name and metadata")
    ] = None,
    filters: CreativeFilters | None = None,
    sort: Sort | None = None,
    pagination: PaginationRequest | None = None,
    fields: list[FieldModel | str] | None = None,
    include_performance: Annotated[
        bool, PydanticField(description="Include performance metrics for each creative")
    ] = False,
    include_assignments: Annotated[
        bool, PydanticField(description="Include package assignment details for each creative")
    ] = False,
    include_sub_assets: Annotated[
        bool, PydanticField(description="Include sub-assets (e.g. individual sizes in a responsive creative)")
    ] = False,
    page: Annotated[int, PydanticField(description="Page number for pagination (1-based)")] = 1,
    limit: Annotated[int, PydanticField(description="Maximum number of creatives per page")] = 50,
    sort_by: Annotated[
        str, PydanticField(description="Field to sort by (e.g. 'created_date', 'name')")
    ] = "created_date",
    sort_order: Annotated[str, PydanticField(description="Sort direction: 'asc' or 'desc'")] = "desc",
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
    fields_list = [enum_value(f) for f in fields] if fields else None

    # Structured sort and pagination are AdCP spec params; _impl is built around flat
    # equivalents (sort_by/sort_order, page/limit). Coerce structured forms to flat
    # at the boundary so spec-compliant payloads are honored instead of silently dropped.
    if sort is not None:
        if sort.field is not None:
            sort_by = enum_value(sort.field)
        if sort.direction is not None:
            sort_order = enum_value(sort.direction)
    if pagination is not None and pagination.max_results is not None:
        limit = pagination.max_results

    req = _build_list_creatives_request(
        media_buy_id=media_buy_id,
        media_buy_ids=media_buy_ids,
        status=status,
        tags=tags,
        created_after=created_after,
        created_before=created_before,
        search=search,
        filters=filters,
        fields=fields_list,
        include_assignments=include_assignments,
        limit=limit,
        sort_by=sort_by,
        sort_order=sort_order,
        context=context,
    )
    response = _list_creatives_impl(
        req=req,
        format=format,
        include_performance=include_performance,
        include_sub_assets=include_sub_assets,
        page=page,
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
    context: ContextObject | None = None,  # Application level context per adcp spec
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

    req = _build_list_creatives_request(
        media_buy_id=media_buy_id,
        media_buy_ids=media_buy_ids,
        status=status,
        tags=tags,
        created_after=created_after,
        created_before=created_before,
        search=search,
        filters=filters,
        fields=fields,
        include_assignments=include_assignments,
        limit=limit,
        sort_by=sort_by,
        sort_order=sort_order,
        context=to_context_object(context),
    )
    return _list_creatives_impl(
        req=req,
        format=format,
        include_performance=include_performance,
        include_sub_assets=include_sub_assets,
        page=page,
        identity=identity,
    )
