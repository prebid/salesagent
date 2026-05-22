"""Get Media Buys tool implementation.

Returns media buy status, creative approval state, and optional delivery snapshots
for monitoring and reporting workflows.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Any, cast

from fastmcp.exceptions import ToolError
from fastmcp.server.context import Context
from fastmcp.tools.tool import ToolResult
from pydantic import Field, RootModel, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.resolved_identity import ResolvedIdentity
from src.core.tool_context import ToolContext

logger = logging.getLogger(__name__)


@dataclass
class _MediaBuyData:
    """Plain data extracted from a MediaBuy ORM row."""

    media_buy_id: str
    currency: str | None
    budget: Decimal | None
    start_date: date | None
    end_date: date | None
    start_time: datetime | None
    end_time: datetime | None
    raw_request: dict | None
    created_at: datetime | None
    updated_at: datetime | None
    status: str | None
    is_paused: bool


@dataclass
class _PackageData:
    """Plain data extracted from a MediaPackage ORM row."""

    media_buy_id: str
    package_id: str
    package_config: dict | None
    budget: Decimal | None
    bid_price: Decimal | None


from adcp.server.helpers import valid_actions_for_status
from adcp.types import AccountReference as LibraryAccountReference
from adcp.types.generated_poc.core.context import ContextObject
from adcp.types.generated_poc.enums.media_buy_status import MediaBuyStatus

from src.core.auth import get_principal_object
from src.core.database.models import Creative, CreativeAssignment, MediaBuy
from src.core.database.repositories import MediaBuyUoW
from src.core.exceptions import AdCPAuthenticationError, AdCPValidationError
from src.core.helpers.adapter_helpers import get_adapter
from src.core.schemas import (
    ApprovalStatus,
    CreativeApproval,
    Error,
    GetMediaBuysMediaBuy,
    GetMediaBuysPackage,
    GetMediaBuysRequest,
    GetMediaBuysResponse,
    Snapshot,
    SnapshotUnavailableReason,
    Targeting,
)
from src.core.validation_helpers import format_validation_error


def _get_media_buys_impl(
    req: GetMediaBuysRequest,
    identity: ResolvedIdentity | None = None,
    include_snapshot: bool = False,
) -> GetMediaBuysResponse:
    """Get media buys with status, creative approval state, and optional delivery snapshots.

    Args:
        req: Validated GetMediaBuysRequest with all protocol fields
        identity: ResolvedIdentity with principal/tenant info (transport-agnostic)
        include_snapshot: When True, include near-real-time delivery stats per package.
            This is an internal flag controlled by transport wrappers, not by the request object.

    Returns:
        GetMediaBuysResponse with matching media buys
    """
    if identity is None:
        raise AdCPAuthenticationError("Identity is required")

    if req.account is not None or req.account_id is not None:
        raise AdCPValidationError("account filtering is not yet supported", recovery="correctable")

    testing_ctx = identity.testing_context
    principal_id = identity.principal_id
    if not principal_id:
        return GetMediaBuysResponse(
            media_buys=[],
            errors=[Error(code="AUTH_REQUIRED", message="Principal ID not found in context")],
        )

    principal = get_principal_object(principal_id)
    if not principal:
        return GetMediaBuysResponse(
            media_buys=[],
            errors=[Error(code="AUTH_REQUIRED", message=f"Principal {principal_id} not found")],
        )

    tenant = identity.tenant
    today = datetime.now(UTC).date()
    tenant_id: str = tenant["tenant_id"]

    # Single DB session for all reads — ORM objects are converted to plain
    # dataclasses inside the UoW scope so nothing is accessed after session close.
    with MediaBuyUoW(tenant_id) as uow:
        assert uow.media_buys is not None
        # Resolve which media buys to return
        target_media_buys = _fetch_target_media_buys(req, principal_id, uow, today)

        # Resolve creative approvals for all packages in one batch query
        all_media_buy_ids = [buy.media_buy_id for buy in target_media_buys]
        # FIXME(salesagent-9f2): _fetch_creative_approvals should use a repository method
        assert uow.session is not None
        creative_approvals_by_package = _fetch_creative_approvals(all_media_buy_ids, tenant_id, uow.session)

        # Resolve package configs for all media buys in one batch query
        packages_by_media_buy = _fetch_packages(all_media_buy_ids, uow)

    # Get snapshots from adapter if requested
    snapshot_data: dict[str, dict[str, Snapshot | None]] = {}  # media_buy_id -> package_id -> Snapshot
    unavailable_reason: SnapshotUnavailableReason | None = None

    if include_snapshot:
        adapter = get_adapter(
            principal,
            dry_run=testing_ctx.dry_run if testing_ctx else False,
            testing_context=testing_ctx,
        )
        if adapter.capabilities.supports_realtime_reporting:
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
    # Accumulate non-fatal targeting-rehydration failures here; one row per
    # affected (media_buy_id, package_id). Surfaced on the response so the
    # buyer can reconcile out-of-band — beats silently coercing to
    # targeting_overlay=None which is indistinguishable from "no targeting".
    hydration_errors: list[Error] = []
    for buy in target_media_buys:
        status = _compute_status(buy, today)

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
            if include_snapshot and snapshot is None:
                snapshot_unavailable = unavailable_reason or SnapshotUnavailableReason.SNAPSHOT_TEMPORARILY_UNAVAILABLE

            # Materialize targeting_overlay from package_config so callers can verify
            # what was persisted. Tolerates the legacy "targeting" key for data written
            # before the targeting_overlay rename (see media_buy_create.py:638-642).
            # A single corrupted package_config row must not crash the whole tenant's
            # get_media_buys response — log the bad row, surface a non-fatal
            # TARGETING_REHYDRATION_FAILED on the response's errors channel, and
            # set this package's targeting_overlay=None so the rest of the buy
            # still renders.
            #
            # Narrow ``except`` to ``TypeError`` only: production
            # ``extra="ignore"`` already absorbs unknown-field drift, so
            # ``ValidationError`` here would only fire in dev/CI and we want
            # that canary to surface forgotten field declarations as a hard
            # test failure (CLAUDE.md "No Quiet Failures"). ``TypeError``
            # covers the real-corruption case (non-dict input from a bad row).
            targeting_raw = pkg_config.get("targeting_overlay") or pkg_config.get("targeting")
            targeting_overlay: Targeting | None
            if not targeting_raw:
                targeting_overlay = None
            else:
                try:
                    targeting_overlay = Targeting(**targeting_raw)
                except TypeError as exc:
                    logger.warning(
                        "Failed to rehydrate targeting_overlay for media_buy=%s package=%s; "
                        "returning targeting_overlay=None for this package. Error: %s",
                        buy.media_buy_id,
                        pkg_id,
                        exc,
                    )
                    # Use the spec-standard ``INTERNAL_ERROR`` code (data-integrity
                    # is a seller-side issue; buyer can't fix). The specific
                    # ``TARGETING_REHYDRATION_FAILED`` shape lives in the message
                    # so callers can grep/route on it without us adding a
                    # non-standard wire code.
                    hydration_errors.append(
                        Error(
                            code="INTERNAL_ERROR",
                            message=(
                                f"TARGETING_REHYDRATION_FAILED: targeting overlay for "
                                f"package '{pkg_id}' on media buy '{buy.media_buy_id}' "
                                f"could not be rehydrated; returning "
                                f"targeting_overlay=None for this package."
                            ),
                            field=f"media_buys[].packages[{pkg_id}].targeting_overlay",
                        )
                    )
                    targeting_overlay = None

            response_packages.append(
                GetMediaBuysPackage(
                    package_id=pkg_id,
                    budget=float(pkg.budget) if pkg.budget is not None else None,
                    bid_price=float(pkg.bid_price) if pkg.bid_price is not None else None,
                    product_id=pkg_config.get("product_id"),
                    start_time=pkg_config.get("start_time"),
                    end_time=pkg_config.get("end_time"),
                    paused=pkg_config.get("paused"),
                    targeting_overlay=targeting_overlay,
                    creative_approvals=approvals if approvals else None,
                    snapshot=snapshot,
                    snapshot_unavailable_reason=snapshot_unavailable if include_snapshot else None,
                )
            )

        total_budget = float(buy.budget) if buy.budget else 0.0
        buyer_campaign_ref = (buy.raw_request or {}).get("buyer_campaign_ref")

        response_media_buys.append(
            GetMediaBuysMediaBuy(
                media_buy_id=buy.media_buy_id,
                buyer_campaign_ref=buyer_campaign_ref,
                status=status,
                valid_actions=valid_actions_for_status(status.value),
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
        errors=hydration_errors or None,
    )


async def get_media_buys(
    media_buy_ids: list[str] | None = None,
    status_filter: MediaBuyStatus | list[MediaBuyStatus] | None = None,
    include_snapshot: Annotated[
        bool, Field(description="When true, include near-real-time delivery stats per package")
    ] = False,
    account: LibraryAccountReference | None = None,
    context: ContextObject | None = None,
    ctx: Context | ToolContext | None = None,
):
    """Get media buys with status, creative approval state, and optional delivery snapshots.

    MCP tool wrapper that resolves identity and delegates to the shared implementation.

    Args:
        media_buy_ids: Array of publisher media buy IDs to retrieve (optional)
        status_filter: Filter by status - single status or array of MediaBuyStatus values (optional)
        include_snapshot: When true, include near-real-time delivery stats per package (default: false)
        account: Account reference per AdCP 3.x (optional). Legacy account_id is normalized by middleware.
        context: Application level context object (optional)
        ctx: FastMCP context (automatically provided)

    Returns:
        ToolResult with GetMediaBuysResponse data
    """
    try:
        req = GetMediaBuysRequest(
            media_buy_ids=media_buy_ids,
            status_filter=cast(MediaBuyStatus | list[MediaBuyStatus] | None, status_filter),
            account=account,
            context=cast(ContextObject | None, context),
        )
        # Read identity pre-resolved by MCPAuthMiddleware
        identity = (await ctx.get_state("identity")) if isinstance(ctx, Context) else None
        response = _get_media_buys_impl(req, identity=identity, include_snapshot=include_snapshot)
        return ToolResult(content=str(response), structured_content=response)
    except ValidationError as e:
        raise ToolError(format_validation_error(e, context="get_media_buys request"))


def get_media_buys_raw(
    media_buy_ids: list[str] | None = None,
    status_filter: MediaBuyStatus | list[MediaBuyStatus] | None = None,
    include_snapshot: bool = False,
    account: LibraryAccountReference | None = None,
    context: ContextObject | None = None,
    ctx: Context | ToolContext | None = None,
    identity: ResolvedIdentity | None = None,
):
    """Get media buys (raw function for A2A server use).

    Args:
        media_buy_ids: Array of publisher media buy IDs to retrieve (optional)
        status_filter: Filter by status - single status or array of MediaBuyStatus values (optional)
        include_snapshot: When true, include near-real-time delivery stats per package (default: false)
        account: Account reference per AdCP 3.x (optional). Legacy account_id is normalized by middleware.
        context: Application level context (optional)
        ctx: Context for authentication (used if identity not pre-resolved)
        identity: Pre-resolved identity (preferred over ctx)

    Returns:
        GetMediaBuysResponse
    """
    if identity is None:
        from src.core.transport_helpers import resolve_identity_from_context

        identity = resolve_identity_from_context(ctx, require_valid_token=True, protocol="a2a")

    req = GetMediaBuysRequest(
        media_buy_ids=media_buy_ids,
        status_filter=cast(MediaBuyStatus | list[MediaBuyStatus] | None, status_filter),
        account=account,
        context=cast(ContextObject | None, context),
    )
    return _get_media_buys_impl(req, identity=identity, include_snapshot=include_snapshot)


# --- Helper functions ---


def _fetch_target_media_buys(
    req: GetMediaBuysRequest,
    principal_id: str,
    uow: MediaBuyUoW,
    today: date,
) -> list[_MediaBuyData]:
    """Fetch media buys from database matching the request filters."""
    assert uow.media_buys is not None
    filter_statuses = _resolve_status_filter(req.status_filter)

    buys = uow.media_buys.get_by_principal(
        principal_id,
        media_buy_ids=req.media_buy_ids,
    )

    return [
        _MediaBuyData(
            media_buy_id=buy.media_buy_id,
            currency=buy.currency,
            budget=buy.budget,
            start_date=cast(date, buy.start_date),
            end_date=cast(date, buy.end_date),
            start_time=buy.start_time,
            end_time=buy.end_time,
            raw_request=buy.raw_request,
            created_at=buy.created_at,
            updated_at=buy.updated_at,
            status=buy.status,
            is_paused=buy.is_paused,
        )
        for buy in buys
        if _compute_status(buy, today) in filter_statuses
    ]


def _resolve_status_filter(
    status_filter: MediaBuyStatus | Any | None,
) -> set[MediaBuyStatus]:
    """Resolve status_filter request field to a set of MediaBuyStatus values."""
    if status_filter is None:
        # Default: active only
        return {MediaBuyStatus.active}

    if isinstance(status_filter, RootModel):
        return set(status_filter.root)

    if isinstance(status_filter, list):
        return set(status_filter)

    return {status_filter}


# Persisted MediaBuy.status (written by media_buy_create.py and lifecycle
# transitions) maps onto the AdCP MediaBuyStatus wire vocabulary below.
# This mirrors media_buy_delivery._PERSISTED_STATUS_TO_INTERNAL but targets
# the AdCP enum used by list_media_buys instead of the internal delivery
# filter vocabulary — the two output vocabularies are genuinely different
# (AdCP has no "failed"/"ready"/"draft"), so per the CLAUDE.md DRY guidance
# ("not about collapsing two genuinely different operations") the mapping
# is mirrored, not shared.
#
# The persisted status is authoritative: terminal/explicit states
# (paused, completed, rejected, canceled) are lifecycle decisions that
# cannot be re-derived from flight dates. "failed" has no AdCP equivalent
# and is reported as the closest terminal state, "rejected". Pre-serving
# states (draft/pending/pending_approval) map to "pending_start"
# (consistent with media_buy_create._compute_initial_media_buy_status).
# Generic serving states (active/approved) are date-refined below.
_PERSISTED_STATUS_TO_ADCP: dict[str, MediaBuyStatus] = {
    "active": MediaBuyStatus.active,
    "approved": MediaBuyStatus.active,
    "paused": MediaBuyStatus.paused,
    "completed": MediaBuyStatus.completed,
    "rejected": MediaBuyStatus.rejected,
    "canceled": MediaBuyStatus.canceled,
    "failed": MediaBuyStatus.rejected,
    "draft": MediaBuyStatus.pending_start,
    "pending": MediaBuyStatus.pending_start,
    "pending_approval": MediaBuyStatus.pending_start,
    "pending_creatives": MediaBuyStatus.pending_creatives,
    "pending_start": MediaBuyStatus.pending_start,
}


def _compute_status(buy: MediaBuy | _MediaBuyData, today: date) -> MediaBuyStatus:
    """Resolve a media buy's AdCP status from its persisted status column.

    The persisted ``MediaBuy.status`` is the source of truth. Only when the
    buy is in a generic serving state ("active"/"approved") do we refine
    against the flight window — a serving buy whose flight has not started
    yet is "pending_start", one past its end date is "completed", and one
    flagged ``is_paused`` is "paused". Terminal/explicit lifecycle states
    (paused, completed, rejected, canceled) come straight from the column
    and cannot be re-derived from flight dates.
    """
    persisted = (buy.status or "").lower()
    mapped = _PERSISTED_STATUS_TO_ADCP.get(persisted, MediaBuyStatus.active)

    if mapped != MediaBuyStatus.active:
        return mapped

    # Generic serving state — refine against the flight window.
    if getattr(buy, "is_paused", False):
        return MediaBuyStatus.paused

    start = buy.start_time.date() if buy.start_time else cast(date, buy.start_date)
    end = buy.end_time.date() if buy.end_time else cast(date, buy.end_date)

    if today < start:
        return MediaBuyStatus.pending_start
    if today > end:
        return MediaBuyStatus.completed
    return MediaBuyStatus.active


def _fetch_packages(media_buy_ids: list[str], uow: MediaBuyUoW) -> dict[str, list[_PackageData]]:
    """Fetch all packages for the given media buy IDs, grouped by media_buy_id."""
    assert uow.media_buys is not None
    if not media_buy_ids:
        return {}

    packages_by_buy = uow.media_buys.get_packages_for_ids(media_buy_ids)

    result: dict[str, list[_PackageData]] = {}
    for media_buy_id, packages in packages_by_buy.items():
        result[media_buy_id] = [
            _PackageData(
                media_buy_id=pkg.media_buy_id,
                package_id=pkg.package_id,
                package_config=pkg.package_config,
                budget=pkg.budget,
                bid_price=pkg.bid_price,
            )
            for pkg in packages
        ]
    return result


def _fetch_creative_approvals(
    media_buy_ids: list[str],
    tenant_id: str,
    session: Session,
) -> dict[tuple[str, str], list[CreativeApproval]]:
    """Fetch creative approvals for all packages, grouped by (media_buy_id, package_id)."""
    if not media_buy_ids:
        return {}

    # Get all creative assignments for these media buys
    assignment_stmt = select(CreativeAssignment).where(
        CreativeAssignment.tenant_id == tenant_id,
        CreativeAssignment.media_buy_id.in_(media_buy_ids),
    )
    assignments: Sequence[CreativeAssignment] = session.scalars(assignment_stmt).all()

    if not assignments:
        return {}

    # Fetch all referenced creatives in one query (scoped to tenant)
    creative_ids = [a.creative_id for a in assignments]
    creative_stmt = select(Creative).where(
        Creative.tenant_id == tenant_id,
        Creative.creative_id.in_(creative_ids),
    )
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
