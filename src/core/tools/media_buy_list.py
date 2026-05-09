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
from typing import Any, cast

from pydantic import RootModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.resolved_identity import ResolvedIdentity

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
    # Persisted MediaBuy.status from the DB. Honored by ``_compute_status``
    # for blocker / terminal states (pending_creatives, paused, rejected,
    # canceled) that no clock can resolve.
    status: str | None = None
    # Pre-computed status for projected GAM buys (whose state comes from
    # GAM, not just flight dates). None means use the date-derived status.
    projected_status: object | None = None


@dataclass
class _PackageData:
    """Plain data extracted from a MediaPackage ORM row."""

    media_buy_id: str
    package_id: str
    package_config: dict | None
    budget: Decimal | None
    bid_price: Decimal | None


from adcp.types import MediaBuyStatus

from src.core.auth import get_principal_object
from src.core.database.models import Creative, CreativeAssignment, MediaBuy
from src.core.database.repositories import MediaBuyUoW
from src.core.exceptions import AdCPAuthenticationError
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
    Targeting,
)
from src.core.tools._gam_projection import (
    build_buy_ext,
    build_package_ext,
    line_item_to_package_fields,
    order_to_media_buy_fields,
    project_gam_status,
    project_orders_for_principal,
)


def _get_media_buys_impl(
    req: GetMediaBuysRequest,
    identity: ResolvedIdentity | None = None,
) -> GetMediaBuysResponse:
    """Get media buys with status, creative approval state, and optional delivery snapshots.

    Args:
        req: Validated GetMediaBuysRequest with all protocol fields, including
            ``include_snapshot`` (per AdCP spec) which when true causes each
            package to carry a near-real-time delivery snapshot.
        identity: ResolvedIdentity with principal/tenant info (transport-agnostic)

    Returns:
        GetMediaBuysResponse with matching media buys
    """
    include_snapshot = bool(req.include_snapshot)
    if identity is None:
        raise AdCPAuthenticationError("Identity is required")

    # ``req.account`` is an optional spec-defined scoping hint. The principal
    # already scopes to a tenant, so we tolerate the field rather than reject
    # the request — buyer agents (and storyboards) routinely pass it for
    # routing context, not as a hostile filter.

    testing_ctx = identity.testing_context
    principal_id = identity.principal_id
    if not principal_id:
        return GetMediaBuysResponse(
            media_buys=[],
            errors=[{"code": "principal_id_missing", "message": "Principal ID not found in context"}],
        )

    principal = get_principal_object(principal_id, tenant_id=identity.tenant_id)
    if not principal:
        return GetMediaBuysResponse(
            media_buys=[],
            errors=[{"code": "principal_not_found", "message": f"Principal {principal_id} not found"}],
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

        # Project GAM orders + line items for advertisers assigned to this
        # principal. Projected buys appear alongside native ones; they have
        # no creative approvals and their snapshots are looked up by
        # platform_line_item_id like any other adapter package.
        projected_buys, projected_packages = _project_gam_buys(
            uow.session,
            tenant_id,
            principal_id,
            req,
            today,
        )
        target_media_buys.extend(projected_buys)
        for media_buy_id, packages in projected_packages.items():
            packages_by_media_buy[media_buy_id] = packages

        # Webhook activity opt-in (#101). When ``ext.psa.include_webhook_activity``
        # is true, fetch recent webhook_delivery_log rows for the
        # returned buys (scoped to the calling principal so a buyer
        # only sees its own deliveries even if multiple agents share
        # visibility into the same buy).
        webhook_activity_by_buy = _fetch_webhook_activity(
            req,
            uow.session,
            tenant_id,
            principal_id,
            [b.media_buy_id for b in target_media_buys],
        )

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

            response_packages.append(
                GetMediaBuysPackage(
                    package_id=pkg_id,
                    budget=float(pkg.budget) if pkg.budget is not None else None,
                    bid_price=float(pkg.bid_price) if pkg.bid_price is not None else None,
                    product_id=pkg_config.get("product_id"),
                    start_time=pkg_config.get("start_time"),
                    end_time=pkg_config.get("end_time"),
                    paused=pkg_config.get("paused"),
                    targeting_overlay=_build_targeting_overlay(pkg_config),
                    creative_approvals=approvals if approvals else None,
                    snapshot=snapshot,
                    snapshot_unavailable_reason=snapshot_unavailable if include_snapshot else None,
                    ext=build_package_ext(pkg_config),
                )
            )

        total_budget = float(buy.budget) if buy.budget else 0.0
        buyer_campaign_ref = (buy.raw_request or {}).get("buyer_campaign_ref")

        # Build the response ``ext`` field. ``ext.gam`` carries import
        # provenance for projected/materialized GAM buys; ``ext.psa``
        # carries publisher-side activity (webhook deliveries, future
        # PSA-specific surfaces). Both vendors coexist under the same dict.
        buy_ext = build_buy_ext(buy.raw_request)
        webhook_deliveries = webhook_activity_by_buy.get(buy.media_buy_id)
        if webhook_deliveries is not None:
            buy_ext = (buy_ext or {}) | {"psa": {"webhook_deliveries": webhook_deliveries}}

        response_media_buys.append(
            GetMediaBuysMediaBuy(
                media_buy_id=buy.media_buy_id,
                buyer_campaign_ref=buyer_campaign_ref,
                status=status,
                currency=buy.currency or "USD",
                total_budget=total_budget,
                packages=response_packages,
                created_at=buy.created_at,
                updated_at=buy.updated_at,
                ext=buy_ext,
            )
        )

    return GetMediaBuysResponse(
        media_buys=response_media_buys,
        context=req.context,
    )


_WEBHOOK_ACTIVITY_DEFAULT_LIMIT = 50
_WEBHOOK_ACTIVITY_MAX_LIMIT = 200


def _fetch_webhook_activity(
    req: GetMediaBuysRequest,
    session: Session,
    tenant_id: str,
    principal_id: str,
    media_buy_ids: list[str],
) -> dict[str, list[dict]]:
    """Build the per-buy webhook delivery list when ``ext.psa`` opted in.

    Returns a map of ``media_buy_id -> [delivery_dicts]``. Empty dict
    when the request didn't opt in (so the caller can skip the merge
    cheaply). Deliveries are scoped to the calling principal so a
    buyer agent only sees its own webhook history even when multiple
    agents share access to the same media buy.
    """
    ext = req.ext or {}
    psa = ext.get("psa") or {}
    if not psa.get("include_webhook_activity"):
        return {}
    if not media_buy_ids:
        return {}

    raw_limit = psa.get("webhook_activity_limit", _WEBHOOK_ACTIVITY_DEFAULT_LIMIT)
    try:
        limit = int(raw_limit)
    except (TypeError, ValueError):
        limit = _WEBHOOK_ACTIVITY_DEFAULT_LIMIT
    limit = max(1, min(_WEBHOOK_ACTIVITY_MAX_LIMIT, limit))

    from src.core.database.repositories.delivery import DeliveryRepository

    repo = DeliveryRepository(session, tenant_id)
    activity: dict[str, list[dict]] = {}
    for media_buy_id in media_buy_ids:
        rows = repo.list_logs_for_buyer(media_buy_id, principal_id, limit=limit)
        activity[media_buy_id] = [
            {
                "delivery_id": row.id,
                "fired_at": row.created_at.isoformat() if row.created_at else None,
                "completed_at": row.completed_at.isoformat() if row.completed_at else None,
                "task_type": row.task_type,
                "notification_type": row.notification_type,
                "sequence_number": row.sequence_number,
                "attempt": row.attempt_count,
                "status": row.status,
                "url": _redact_url_query(row.webhook_url),
                "http_status_code": row.http_status_code,
                "response_time_ms": row.response_time_ms,
                "payload_size_bytes": row.payload_size_bytes,
                "error_message": row.error_message,
                # Bodies are pre-truncated at insert time (DeliveryRepository
                # caps at 64KB). Surface as-is — buyers wanting full payload
                # debug get what we stored.
                "request_payload": row.request_payload,
                "response_body": row.response_body,
            }
            for row in rows
        ]
    return activity


def _redact_url_query(url: str | None) -> str | None:
    """Strip query string from a webhook URL before echoing to buyers.

    Why: buyer-configured webhook URLs commonly carry bearer tokens or
    signed-URL parameters in the query string. Even though the buyer
    sent the URL to us originally, surfacing it back in API responses
    risks accidental disclosure (screenshots, logs, third-party agents
    in the buyer pipeline). Path is preserved for debug value.
    """
    if not url:
        return url
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


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


# Statuses no clock can resolve: blockers awaiting a buyer / operator action,
# and terminal explicit states. ``_compute_status`` returns these verbatim from
# the persisted ``MediaBuy.status`` rather than overwriting them with a
# date-derived value.
_BLOCKER_STATUSES: frozenset[str] = frozenset(
    {
        MediaBuyStatus.pending_creatives.value,
        MediaBuyStatus.paused.value,
        MediaBuyStatus.rejected.value,
        MediaBuyStatus.canceled.value,
    }
)


def _compute_status(buy: MediaBuy | _MediaBuyData, today: date) -> MediaBuyStatus:
    """Compute the current AdCP status of a media buy.

    Precedence:
    1. Projected GAM status (set by ``_project_gam_buys``) wins — GAM is the
       source of truth for adapter-managed buys.
    2. Persisted blocker / terminal statuses (``pending_creatives``, ``paused``,
       ``rejected``, ``canceled``) win over date math — no clock can resolve a
       missing creative or an explicit operator action.
    3. Otherwise derive from flight dates: ``pending_start`` / ``active`` /
       ``completed``.
    """
    if isinstance(buy, _MediaBuyData) and buy.projected_status is not None:
        return cast(MediaBuyStatus, buy.projected_status)

    persisted = (buy.status or "").lower()
    if persisted in _BLOCKER_STATUSES:
        return MediaBuyStatus(persisted)

    start = buy.start_time.date() if buy.start_time else cast(date, buy.start_date)
    end = buy.end_time.date() if buy.end_time else cast(date, buy.end_date)

    if today < start:
        return MediaBuyStatus.pending_start
    if today > end:
        return MediaBuyStatus.completed
    return MediaBuyStatus.active


def _project_gam_buys(
    session: Session,
    tenant_id: str,
    principal_id: str,
    req: GetMediaBuysRequest,
    today: date,
) -> tuple[list[_MediaBuyData], dict[str, list[_PackageData]]]:
    """Project GAM orders into _MediaBuyData / _PackageData for the response.

    Applies the same status_filter and media_buy_ids filter as native
    buys. Status is derived from the GAM order status (PAUSED / CANCELED /
    DELETED short-circuit) combined with flight dates for non-terminal
    states.
    """
    media_buy_ids_filter = req.media_buy_ids if req.media_buy_ids else None
    orders, line_items_by_order = project_orders_for_principal(session, tenant_id, principal_id, media_buy_ids_filter)
    if not orders:
        return [], {}

    filter_statuses = _resolve_status_filter(req.status_filter)

    projected_buys: list[_MediaBuyData] = []
    projected_packages: dict[str, list[_PackageData]] = {}
    for order in orders:
        fields = order_to_media_buy_fields(order)
        status = project_gam_status(order.status, fields["start_date"], fields["end_date"], today)
        if status not in filter_statuses:
            continue
        buy_data = _MediaBuyData(**fields, projected_status=status)
        projected_buys.append(buy_data)

        packages = [
            _PackageData(**line_item_to_package_fields(li)) for li in line_items_by_order.get(order.order_id, [])
        ]
        projected_packages[buy_data.media_buy_id] = packages

    return projected_buys, projected_packages


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


def _build_targeting_overlay(pkg_config: dict) -> Targeting | None:
    """Hydrate the persisted targeting_overlay from package_config.

    Per AdCP spec (``Package.targeting_overlay`` on get_media_buys), sellers
    must echo the persisted targeting back so buyers can verify what was
    stored. For sellers claiming the property-lists / collection-lists
    specialisms, this includes the ``PropertyListReference`` and
    ``CollectionListReference`` provided on create / update.

    Falls back to the legacy ``targeting`` key for media buys written before
    the storage migration to ``targeting_overlay``.
    """
    raw = pkg_config.get("targeting_overlay") or pkg_config.get("targeting")
    if not raw:
        return None
    if isinstance(raw, Targeting):
        return raw
    return Targeting.model_validate(raw)
