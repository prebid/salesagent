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

from fastmcp.server.context import Context
from fastmcp.tools.tool import ToolResult
from pydantic import Field, RootModel
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
from adcp.types import ContextObject, MediaBuyStatus

from src.core.auth import get_principal_object, require_identity, require_tenant
from src.core.database.models import Creative, CreativeAssignment, MediaBuy
from src.core.database.repositories import MediaBuyUoW
from src.core.exceptions import AdCPCapabilityNotSupportedError
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
from src.core.validation_helpers import adcp_validation_boundary


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
    identity = require_identity(identity, context=req.context)

    if req.account is not None or req.account_id is not None:
        raise AdCPCapabilityNotSupportedError(
            "account filtering is not yet supported",
            suggestion="Omit account/account_id from the request; the seller infers the account from the auth token.",
        )

    testing_ctx = identity.testing_context
    principal_id = identity.principal_id
    if not principal_id:
        return GetMediaBuysResponse(
            media_buys=[],
            errors=[
                Error(  # structural-guard: advisory: get_media_buys degrades to empty list + error, not a raise
                    code="AUTH_REQUIRED", message="Principal ID not found in context"
                )
            ],
        )

    principal = get_principal_object(principal_id, tenant_id=identity.tenant_id)
    if not principal:
        return GetMediaBuysResponse(
            media_buys=[],
            errors=[
                Error(  # structural-guard: advisory: get_media_buys degrades to empty list + error, not a raise
                    code="AUTH_REQUIRED", message=f"Principal {principal_id} not found"
                )
            ],
        )

    # require_tenant raises the canonical auth envelope instead of a raw TypeError
    # if no tenant resolved (the principal advisories above take precedence).
    tenant = require_tenant(identity, context=req.context)
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
            tenant=tenant,
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
        status = _compute_status(buy)

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
                    # Seller-side data-integrity failure (the buyer can't fix it),
                    # surfaced with the standard ``SERVICE_UNAVAILABLE`` wire code —
                    # matching the sibling per-creative advisory in
                    # creatives/_processing.py — with the specific
                    # ``TARGETING_REHYDRATION_FAILED`` shape in the message so
                    # callers can grep/route on it.
                    hydration_errors.append(
                        Error(  # structural-guard: advisory per-package result in GetMediaBuysResponse.errors[]
                            code="SERVICE_UNAVAILABLE",
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


def _build_get_media_buys_request(
    media_buy_ids: list[str] | None,
    status_filter: MediaBuyStatus | list[MediaBuyStatus] | None,
    account: LibraryAccountReference | None,
    context: ContextObject | None,
) -> GetMediaBuysRequest:
    """Build a GetMediaBuysRequest from individual wire params.

    Shared by the MCP wrapper and the A2A/REST raw wrapper so request
    construction runs inside the ONE validation boundary — previously the raw
    wrapper built the request unprotected and REST leaked a raw pydantic
    ``ValidationError`` with no top-level suggestion (#1417).
    """
    with adcp_validation_boundary(context="get_media_buys request"):
        return GetMediaBuysRequest(
            media_buy_ids=media_buy_ids,
            status_filter=cast(MediaBuyStatus | list[MediaBuyStatus] | None, status_filter),
            account=account,
            context=cast(ContextObject | None, context),
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
    req = _build_get_media_buys_request(media_buy_ids, status_filter, account, context)
    # Read identity pre-resolved by MCPAuthMiddleware
    identity = (await ctx.get_state("identity")) if isinstance(ctx, Context) else None
    response = _get_media_buys_impl(req, identity=identity, include_snapshot=include_snapshot)
    return ToolResult(content=str(response), structured_content=response)


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

    req = _build_get_media_buys_request(media_buy_ids, status_filter, account, context)
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
    # Per AdCP spec: the default status filter (active-only) applies only when
    # media_buy_ids are omitted. When the caller specifies
    # explicit IDs, return all matching buys regardless of status.
    has_explicit_ids = bool(req.media_buy_ids)
    filter_statuses = _resolve_status_filter(req.status_filter, skip_default=has_explicit_ids)

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
        if filter_statuses is None or _compute_status(buy) in filter_statuses
    ]


def _resolve_status_filter(
    status_filter: MediaBuyStatus | Any | None,
    *,
    skip_default: bool = False,
) -> set[MediaBuyStatus] | None:
    """Resolve status_filter request field to a set of MediaBuyStatus values.

    Returns None when no filtering should be applied (explicit IDs with no filter).
    """
    if status_filter is None:
        if skip_default:
            return None  # No filtering — return all statuses
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
# the AdCP enum used by list_media_buys
# instead of the internal delivery filter vocabulary — the two output
# vocabularies are genuinely different (AdCP has no "failed"/"ready"/"draft"),
# so per the CLAUDE.md DRY guidance ("not about collapsing two genuinely
# different operations") the mapping is mirrored, not shared.
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
    # "scheduled" is set by admin approval pre-flight (operations.py / workflows.py);
    # like the other pre-serving states it maps to pending_start.
    "scheduled": MediaBuyStatus.pending_start,
    "pending_creatives": MediaBuyStatus.pending_creatives,
    "pending_start": MediaBuyStatus.pending_start,
}


def normalize_persisted_media_buy_status(status: str | None) -> MediaBuyStatus | None:
    """Map a persisted ``MediaBuy.status`` string to its canonical AdCP ``MediaBuyStatus``.

    Single source of truth for DB-status → AdCP-status coercion (``_PERSISTED_STATUS_TO_ADCP``),
    so the create/update dual-emit of ``media_buy_status`` cannot inject a non-enum DB
    value (e.g. legacy ``pending_approval``) into the typed response field. Returns
    ``None`` for an empty/unknown status so callers omit the field rather than emit a
    non-spec value.
    """
    if not status:
        return None
    return _PERSISTED_STATUS_TO_ADCP.get(status.lower())


def _compute_status(buy: MediaBuy | _MediaBuyData) -> MediaBuyStatus:
    """Resolve a media buy's AdCP status from its PERSISTED status column.

    Persisted-status-authoritative, per the AdCP lifecycle requirement "persist
    status, never recompute from dates" (dist/docs/<version>/media-buy/media-buys/
    index.mdx: status "MUST be
    stored as an explicit field and mutated only by protocol events ... date
    comparison sets the INITIAL status at create_media_buy time; after that, the
    state machine owns the field"). The ``media_buy_status_scheduler`` owns the
    date-driven transitions (pending_start -> active -> completed) on the status
    column, so read paths never recompute from the flight window. This keeps
    ``get_media_buys`` in agreement with the update-response status pair, which
    reads the same column via ``normalize_persisted_media_buy_status`` (#1417).

    A serving buy flagged ``is_paused`` reports ``paused`` — a command-driven
    flag, not a date. Terminal/explicit states come straight from the column.
    """
    mapped = normalize_persisted_media_buy_status(buy.status) or MediaBuyStatus.active
    if mapped == MediaBuyStatus.active and getattr(buy, "is_paused", False):
        return MediaBuyStatus.paused
    return mapped


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
