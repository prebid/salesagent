"""Get Media Buys tool implementation.

Returns media buy status, creative approval state, and optional delivery snapshots
for monitoring and reporting workflows.
"""

import logging
from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any, cast

from fastmcp.exceptions import ToolError
from fastmcp.server.context import Context
from fastmcp.tools.tool import ToolResult
from pydantic import ValidationError
from sqlalchemy import select

from src.core.tool_context import ToolContext

logger = logging.getLogger(__name__)

from adcp.types.generated_poc.core.context import ContextObject
from adcp.types.generated_poc.enums.media_buy_status import MediaBuyStatus

from src.core.auth import get_principal_object
from src.core.config_loader import get_current_tenant
from src.core.database.database_session import get_db_session
from src.core.database.models import Creative, CreativeAssignment, MediaBuy, MediaPackage
from src.core.helpers import get_principal_id_from_context
from src.core.helpers.adapter_helpers import get_adapter
from src.core.schemas import (
    ApprovalStatus,
    CreativeApproval,
    GetMediaBuysMediaBuy,
    GetMediaBuysPackage,
    GetMediaBuysRequest,
    GetMediaBuysResponse,
    Snapshot,
    SnapshotUnavailableReason,
)
from src.core.testing_hooks import get_testing_context
from src.core.validation_helpers import format_validation_error


def _get_media_buys_impl(req: GetMediaBuysRequest, ctx: Context | ToolContext | None) -> GetMediaBuysResponse:
    """Get media buys with status, creative approval state, and optional delivery snapshots."""
    if ctx is None:
        raise ToolError("Context is required")

    testing_ctx = get_testing_context(ctx)
    principal_id = get_principal_id_from_context(ctx)
    if not principal_id:
        return GetMediaBuysResponse(
            media_buys=[],
            errors=[{"code": "principal_id_missing", "message": "Principal ID not found in context"}],
        )

    principal = get_principal_object(principal_id)
    if not principal:
        return GetMediaBuysResponse(
            media_buys=[],
            errors=[{"code": "principal_not_found", "message": f"Principal {principal_id} not found"}],
        )

    tenant = get_current_tenant()
    today = datetime.now(UTC).date()

    # Resolve which media buys to return
    target_media_buys = _fetch_target_media_buys(req, principal_id, tenant, today)

    # Resolve creative approvals for all packages in one batch query
    all_media_buy_ids = [buy.media_buy_id for buy in target_media_buys]
    creative_approvals_by_package = _fetch_creative_approvals(all_media_buy_ids, tenant["tenant_id"])

    # Resolve package configs for all media buys in one batch query
    packages_by_media_buy = _fetch_packages(all_media_buy_ids)

    # Get snapshots from adapter if requested
    snapshot_data: dict[str, dict[str, Snapshot | None]] = {}  # media_buy_id -> package_id -> Snapshot
    unavailable_reason: SnapshotUnavailableReason | None = None

    if req.include_snapshot:
        adapter = get_adapter(
            principal,
            dry_run=testing_ctx.dry_run if testing_ctx else False,
            testing_context=testing_ctx,
        )
        if hasattr(adapter, "get_packages_snapshot"):
            # Build list of (media_buy_id, package_id, platform_line_item_id) for the adapter
            package_refs = []
            for buy in target_media_buys:
                for pkg in packages_by_media_buy.get(buy.media_buy_id, []):
                    line_item_id = (pkg.package_config or {}).get("platform_line_item_id")
                    package_refs.append((buy.media_buy_id, pkg.package_id, line_item_id))

            snapshot_data = adapter.get_packages_snapshot(package_refs)
        else:
            unavailable_reason = SnapshotUnavailableReason.SNAPSHOT_UNSUPPORTED

    # Build response
    response_media_buys = []
    for buy in target_media_buys:
        # Compute status from dates
        if buy.start_time:
            start_compare = buy.start_time.date()
        else:
            start_compare = cast(date, buy.start_date)

        if buy.end_time:
            end_compare = buy.end_time.date()
        else:
            end_compare = cast(date, buy.end_date)

        if today < start_compare:
            status = MediaBuyStatus.pending_activation
        elif today > end_compare:
            status = MediaBuyStatus.completed
        else:
            status = MediaBuyStatus.active

        # Build packages
        packages = packages_by_media_buy.get(buy.media_buy_id, [])
        response_packages = []
        buy_snapshots = snapshot_data.get(buy.media_buy_id, {})

        for pkg in packages:
            pkg_config = pkg.package_config or {}
            pkg_id = pkg.package_id

            # Get creative approvals for this package
            approvals = creative_approvals_by_package.get((buy.media_buy_id, pkg_id), [])

            # Get snapshot for this package
            snapshot = buy_snapshots.get(pkg_id)
            snapshot_unavailable = None
            if req.include_snapshot and snapshot is None:
                snapshot_unavailable = unavailable_reason or SnapshotUnavailableReason.SNAPSHOT_TEMPORARILY_UNAVAILABLE

            response_packages.append(
                GetMediaBuysPackage(
                    package_id=pkg_id,
                    buyer_ref=pkg_config.get("buyer_ref"),
                    budget=float(pkg.budget) if pkg.budget is not None else None,
                    bid_price=float(pkg.bid_price) if pkg.bid_price is not None else None,
                    product_id=pkg_config.get("product_id"),
                    start_time=pkg_config.get("start_time"),
                    end_time=pkg_config.get("end_time"),
                    paused=pkg_config.get("paused"),
                    creative_approvals=approvals if approvals else None,
                    snapshot=snapshot,
                    snapshot_unavailable_reason=snapshot_unavailable if req.include_snapshot else None,
                )
            )

        total_budget = float(buy.budget) if buy.budget else 0.0
        buyer_campaign_ref = (buy.raw_request or {}).get("buyer_campaign_ref")

        response_media_buys.append(
            GetMediaBuysMediaBuy(
                media_buy_id=buy.media_buy_id,
                buyer_ref=buy.buyer_ref,
                buyer_campaign_ref=buyer_campaign_ref,
                status=status,
                currency=buy.currency or "USD",
                total_budget=total_budget,
                packages=response_packages,
                created_at=buy.created_at,
                updated_at=buy.updated_at,
            )
        )

    return GetMediaBuysResponse(
        media_buys=response_media_buys,
        context=req.context,
    )


def get_media_buys(
    media_buy_ids: list[str] | None = None,
    buyer_refs: list[str] | None = None,
    status_filter: MediaBuyStatus | list[MediaBuyStatus] | None = None,
    include_snapshot: bool = False,
    account_id: str | None = None,
    context: ContextObject | None = None,
    ctx: Context | ToolContext | None = None,
):
    """Get media buys with status, creative approval state, and optional delivery snapshots.

    Returns a list of media buys matching the requested filters. When no filters are provided,
    returns all active media buys. Use include_snapshot=true for near-real-time delivery stats.

    Args:
        media_buy_ids: Array of publisher media buy IDs to retrieve (optional)
        buyer_refs: Array of buyer reference IDs to retrieve (optional)
        status_filter: Filter by status - single status or array of MediaBuyStatus values (optional)
        include_snapshot: When true, include near-real-time delivery stats per package (default: false)
        account_id: Filter to a specific account (optional)
        context: Application level context object (optional)
        ctx: FastMCP context (automatically provided)

    Returns:
        ToolResult with GetMediaBuysResponse data
    """
    try:
        req = GetMediaBuysRequest(
            media_buy_ids=media_buy_ids,
            buyer_refs=buyer_refs,
            status_filter=cast(MediaBuyStatus | list[MediaBuyStatus] | None, status_filter),
            include_snapshot=include_snapshot,
            account_id=account_id,
            context=cast(ContextObject | None, context),
        )
        response = _get_media_buys_impl(req, ctx)
        return ToolResult(content=str(response), structured_content=response)
    except ValidationError as e:
        raise ToolError(format_validation_error(e, context="get_media_buys request"))


def get_media_buys_raw(
    media_buy_ids: list[str] | None = None,
    buyer_refs: list[str] | None = None,
    status_filter: MediaBuyStatus | list[MediaBuyStatus] | None = None,
    include_snapshot: bool = False,
    account_id: str | None = None,
    context: ContextObject | None = None,
    ctx: Context | ToolContext | None = None,
):
    """Get media buys (raw function for A2A server use).

    Args:
        media_buy_ids: Array of publisher media buy IDs to retrieve (optional)
        buyer_refs: Array of buyer reference IDs to retrieve (optional)
        status_filter: Filter by status - single status or array of MediaBuyStatus values (optional)
        include_snapshot: When true, include near-real-time delivery stats per package (default: false)
        account_id: Filter to a specific account (optional)
        context: Application level context (optional)
        ctx: Context for authentication

    Returns:
        GetMediaBuysResponse
    """
    req = GetMediaBuysRequest(
        media_buy_ids=media_buy_ids,
        buyer_refs=buyer_refs,
        status_filter=cast(MediaBuyStatus | list[MediaBuyStatus] | None, status_filter),
        include_snapshot=include_snapshot,
        account_id=account_id,
        context=cast(ContextObject | None, context),
    )
    return _get_media_buys_impl(req, ctx)


# --- Helper functions ---


def _fetch_target_media_buys(
    req: GetMediaBuysRequest,
    principal_id: str,
    tenant: dict[str, Any],
    today: date,
) -> list[MediaBuy]:
    """Fetch media buys from database matching the request filters."""
    with get_db_session() as session:
        if req.media_buy_ids:
            stmt = select(MediaBuy).where(
                MediaBuy.tenant_id == tenant["tenant_id"],
                MediaBuy.principal_id == principal_id,
                MediaBuy.media_buy_id.in_(req.media_buy_ids),
            )
            return list(session.scalars(stmt).all())

        if req.buyer_refs:
            stmt = select(MediaBuy).where(
                MediaBuy.tenant_id == tenant["tenant_id"],
                MediaBuy.principal_id == principal_id,
                MediaBuy.buyer_ref.in_(req.buyer_refs),
            )
            return list(session.scalars(stmt).all())

        # No specific IDs — fetch all and apply status filter
        stmt = select(MediaBuy).where(
            MediaBuy.tenant_id == tenant["tenant_id"],
            MediaBuy.principal_id == principal_id,
        )
        all_buys = session.scalars(stmt).all()

        # Determine which statuses to include
        filter_statuses = _resolve_status_filter(req.status_filter)
        return [buy for buy in all_buys if _compute_status(buy, today) in filter_statuses]


def _resolve_status_filter(
    status_filter: MediaBuyStatus | Any | None,
) -> set[MediaBuyStatus]:
    """Resolve status_filter request field to a set of MediaBuyStatus values."""
    if status_filter is None:
        # Default: active only
        return {MediaBuyStatus.active}

    # Handle StatusFilter (RootModel wrapping a list) - adcp SDK types use .root
    if hasattr(status_filter, "root"):  # noqa: rootmodel
        return set(status_filter.root)

    if isinstance(status_filter, list):
        return set(status_filter)

    return {status_filter}


def _compute_status(buy: MediaBuy, today: date) -> MediaBuyStatus:
    """Compute the current AdCP status of a media buy based on its dates."""
    start = buy.start_time.date() if buy.start_time else cast(date, buy.start_date)
    end = buy.end_time.date() if buy.end_time else cast(date, buy.end_date)

    if today < start:
        return MediaBuyStatus.pending_activation
    if today > end:
        return MediaBuyStatus.completed
    return MediaBuyStatus.active


def _fetch_packages(media_buy_ids: list[str]) -> dict[str, list[MediaPackage]]:
    """Fetch all packages for the given media buy IDs, grouped by media_buy_id."""
    if not media_buy_ids:
        return {}
    with get_db_session() as session:
        stmt = select(MediaPackage).where(MediaPackage.media_buy_id.in_(media_buy_ids))
        packages = session.scalars(stmt).all()
        result: dict[str, list[MediaPackage]] = {}
        for pkg in packages:
            result.setdefault(pkg.media_buy_id, []).append(pkg)
        return result


def _fetch_creative_approvals(
    media_buy_ids: list[str],
    tenant_id: str,
) -> dict[tuple[str, str], list[CreativeApproval]]:
    """Fetch creative approvals for all packages, grouped by (media_buy_id, package_id)."""
    if not media_buy_ids:
        return {}

    with get_db_session() as session:
        # Get all creative assignments for these media buys
        assignment_stmt = select(CreativeAssignment).where(
            CreativeAssignment.tenant_id == tenant_id,
            CreativeAssignment.media_buy_id.in_(media_buy_ids),
        )
        assignments: Sequence[CreativeAssignment] = session.scalars(assignment_stmt).all()

        if not assignments:
            return {}

        # Fetch all referenced creatives in one query
        creative_ids = [a.creative_id for a in assignments]
        creative_stmt = select(Creative).where(Creative.creative_id.in_(creative_ids))
        creatives = {c.creative_id: c for c in session.scalars(creative_stmt).all()}

    # Build approval objects grouped by (media_buy_id, package_id)
    result: dict[tuple[str, str], list[CreativeApproval]] = {}
    for assignment in assignments:
        creative = creatives.get(assignment.creative_id)
        if creative is None:
            continue

        approval_status = _map_creative_status(creative.status)
        rejection_reason = None
        if approval_status == ApprovalStatus.rejected:
            rejection_reason = creative.data.get("rejection_reason") if creative.data else None

        key = (assignment.media_buy_id, assignment.package_id)
        result.setdefault(key, []).append(
            CreativeApproval(
                creative_id=assignment.creative_id,
                approval_status=approval_status,
                rejection_reason=rejection_reason,
            )
        )

    return result


def _map_creative_status(status: str) -> ApprovalStatus:
    """Map internal creative status to AdCP ApprovalStatus."""
    if status == "approved":
        return ApprovalStatus.approved
    if status == "rejected":
        return ApprovalStatus.rejected
    return ApprovalStatus.pending_review
