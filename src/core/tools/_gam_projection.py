"""Projection of GAM orders/line items into get_media_buys responses.

When an operator assigns a buyer agent to a ``GamAdvertiser`` (via the
buyer-routing UI), that agent's ``get_media_buys`` response should
include the advertiser's GAM orders alongside any native AdCP buys.

This module performs that projection in-memory at read time. Nothing is
written to ``media_buys`` until the buyer first calls ``update_media_buy``
on a projected ID — at that point a real row is materialized so packages,
push configs, and audit logs have somewhere to attach. See
``update_media_buy`` for the materialization path.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime
from decimal import Decimal

from adcp.types import MediaBuyStatus
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.core.database.models import GAMLineItem, GAMOrder, MediaBuy
from src.core.database.repositories.gam_sync import GAMSyncRepository
from src.core.exceptions import AdCPAuthorizationError, AdCPNotFoundError

# Map of GAM order status → AdCP MediaBuyStatus. The full GAM enum
# (src/adapters/gam/utils/constants.py:GAMOrderStatus) is exhaustive
# here so unknown statuses fail loud rather than silently date-deriving.
#
# Status semantics:
# - DRAFT / PENDING_APPROVAL: not yet activated by seller — return
#   pending_start regardless of flight dates.
# - APPROVED: seller has activated it; flight dates decide pending_start
#   / active / completed via the date logic below.
# - PAUSED: explicit pause.
# - DISAPPROVED: seller rejected it; AdCP MediaBuyStatus.rejected.
# - CANCELED / ARCHIVED: terminal, no longer delivers.
_ORDER_STATUS_MAP: dict[str, MediaBuyStatus] = {
    "DRAFT": MediaBuyStatus.pending_start,
    "PENDING_APPROVAL": MediaBuyStatus.pending_start,
    "DISAPPROVED": MediaBuyStatus.rejected,
    "PAUSED": MediaBuyStatus.paused,
    "CANCELED": MediaBuyStatus.canceled,
    "ARCHIVED": MediaBuyStatus.canceled,
}


def project_gam_status(order_status: str | None, start: date | None, end: date | None, today: date) -> MediaBuyStatus:
    """Map a GAM order's status + flight dates to an AdCP MediaBuyStatus.

    Non-APPROVED statuses short-circuit. APPROVED falls through to
    flight-date logic to decide pending_start / active / completed.
    """
    if order_status:
        upper = order_status.upper()
        if upper in _ORDER_STATUS_MAP:
            return _ORDER_STATUS_MAP[upper]
    if start is not None and today < start:
        return MediaBuyStatus.pending_start
    if end is not None and today > end:
        return MediaBuyStatus.completed
    return MediaBuyStatus.active


def projected_media_buy_id(order_id: str) -> str:
    """Stable, deterministic ID for a projected GAM order."""
    return f"gam_{order_id}"


def projected_package_id(line_item_id: str) -> str:
    """Stable, deterministic ID for a projected GAM line item."""
    return f"gam_li_{line_item_id}"


def fetch_materialized_external_ids(session: Session, tenant_id: str, order_ids: Iterable[str]) -> set[str]:
    """Return the GAM order ids that already have a materialized MediaBuy row.

    Materialized buys take precedence — we don't double-count them in the
    projection. Identified by ``source = 'gam_import'`` and ``external_id``
    matching the order id.
    """
    from src.core.database.repositories.media_buy import MediaBuyRepository

    return MediaBuyRepository(session, tenant_id).list_external_ids_for_source("gam_import", list(order_ids))


def project_orders_for_principal(
    session: Session,
    tenant_id: str,
    principal_id: str,
    media_buy_ids_filter: list[str] | None = None,
) -> tuple[list[GAMOrder], dict[str, list[GAMLineItem]]]:
    """Fetch GAM orders + line items the principal should see via projection.

    Returns the orders and a map of order_id -> [line items]. Filters out
    orders whose advertiser is not assigned to this principal. If
    ``media_buy_ids_filter`` is provided, only includes orders whose
    projected media_buy_id is in the filter.
    """
    repo = GAMSyncRepository(session, tenant_id)
    advertiser_ids = repo.list_advertiser_ids_assigned_to(principal_id)
    if not advertiser_ids:
        return [], {}

    orders = repo.list_orders_for_advertisers(advertiser_ids)

    if media_buy_ids_filter is not None:
        wanted_order_ids = {
            mid[len("gam_") :]
            for mid in media_buy_ids_filter
            if mid.startswith("gam_") and not mid.startswith("gam_li_")
        }
        orders = [o for o in orders if o.order_id in wanted_order_ids]

    if not orders:
        return [], {}

    materialized = fetch_materialized_external_ids(session, tenant_id, [o.order_id for o in orders])
    orders = [o for o in orders if o.order_id not in materialized]

    if not orders:
        return [], {}

    line_items = repo.list_line_items_for_orders([o.order_id for o in orders])
    line_items_by_order: dict[str, list[GAMLineItem]] = {}
    for li in line_items:
        line_items_by_order.setdefault(li.order_id, []).append(li)

    return orders, line_items_by_order


def order_to_media_buy_fields(order: GAMOrder) -> dict:
    """Extract _MediaBuyData-shaped fields from a GAMOrder.

    Keeping this as a dict (not building _MediaBuyData here) avoids a
    circular import with ``media_buy_list``.
    """
    start_date_val: date | None = order.start_date.date() if isinstance(order.start_date, datetime) else None
    end_date_val: date | None = order.end_date.date() if isinstance(order.end_date, datetime) else None

    return {
        "media_buy_id": projected_media_buy_id(order.order_id),
        "currency": order.currency_code,
        "budget": Decimal(str(order.total_budget)) if order.total_budget is not None else None,
        "start_date": start_date_val,
        "end_date": end_date_val,
        "start_time": order.start_date if isinstance(order.start_date, datetime) else None,
        "end_time": order.end_date if isinstance(order.end_date, datetime) else None,
        "raw_request": {
            "gam_order_id": order.order_id,
            "gam_advertiser_id": order.advertiser_id,
            "imported": True,
        },
        "created_at": order.created_at,
        "updated_at": order.last_modified_date or order.updated_at,
    }


def line_item_to_package_fields(line_item: GAMLineItem) -> dict:
    """Extract _PackageData-shaped fields from a GAMLineItem."""
    media_buy_id = projected_media_buy_id(line_item.order_id)
    package_id = projected_package_id(line_item.line_item_id)
    bid_price = Decimal(str(line_item.cost_per_unit)) if line_item.cost_per_unit is not None else None

    # ``gam_order_id`` is included so a flattened/cached package row can
    # be cross-referenced back to its parent GAM order without walking
    # the response tree (build_package_ext surfaces it on the wire).
    package_config: dict = {
        "platform_line_item_id": line_item.line_item_id,
        "imported": True,
        "gam_line_item_status": line_item.status,
        "gam_order_id": line_item.order_id,
    }

    return {
        "media_buy_id": media_buy_id,
        "package_id": package_id,
        "package_config": package_config,
        "budget": None,
        "bid_price": bid_price,
    }


def is_projected_media_buy_id(media_buy_id: str) -> bool:
    """True if ``media_buy_id`` follows the projected GAM order convention."""
    return media_buy_id.startswith("gam_") and not media_buy_id.startswith("gam_li_")


def build_buy_ext(raw_request: dict | None) -> dict | None:
    """Build the ``ext.gam`` payload for a projected/imported MediaBuy response.

    Returns ``None`` for native AdCP buys (so the field stays absent from
    the wire). Returns ``{"gam": {"imported": True, "order_id": ..., "advertiser_id": ...}}``
    for projected and materialized GAM imports — both store the GAM ids in
    ``raw_request`` under the ``gam_order_id`` / ``gam_advertiser_id`` keys.
    """
    if not raw_request or not raw_request.get("imported"):
        return None
    payload: dict = {"imported": True}
    order_id = raw_request.get("gam_order_id")
    if order_id is not None:
        payload["order_id"] = order_id
    advertiser_id = raw_request.get("gam_advertiser_id")
    if advertiser_id is not None:
        payload["advertiser_id"] = advertiser_id
    return {"gam": payload}


def build_package_ext(package_config: dict | None) -> dict | None:
    """Build the ``ext.gam`` payload for a projected/imported Package response.

    Returns ``None`` for native AdCP packages. Returns
    ``{"gam": {"imported": True, "line_item_id": ..., "order_id": ...}}`` for
    GAM-imported packages. ``order_id`` lets buyers cross-reference a flat
    package row back to its parent GAM order without walking the response
    tree.
    """
    if not package_config or not package_config.get("imported"):
        return None
    payload: dict = {"imported": True}
    line_item_id = package_config.get("platform_line_item_id")
    if line_item_id is not None:
        payload["line_item_id"] = line_item_id
    order_id = package_config.get("gam_order_id")
    if order_id is not None:
        payload["order_id"] = order_id
    status = package_config.get("gam_line_item_status")
    if status is not None:
        payload["line_item_status"] = status
    return {"gam": payload}


def order_id_from_projected(media_buy_id: str) -> str:
    """Extract the GAM order id from a projected media_buy_id."""
    if not is_projected_media_buy_id(media_buy_id):
        raise ValueError(f"{media_buy_id!r} is not a projected GAM media_buy_id")
    return media_buy_id[len("gam_") :]


def materialize_projected_buy(
    session: Session,
    tenant_id: str,
    principal_id: str,
    media_buy_id: str,
) -> MediaBuy:
    """Materialize a projected GAM order as a real MediaBuy + MediaPackages.

    Called the first time a buyer mutates an imported buy (update_media_buy
    on a ``gam_<order_id>`` ID). Creates real rows so packages, push
    configs, and audit logs have somewhere to attach. Subsequent reads
    of the same projected ID return the materialized row directly (the
    projection skips materialized order ids).

    Authorization: caller must be the principal currently assigned to
    the order's GAM advertiser. Both "unknown order" and "unassigned
    advertiser" raise the same ``AdCPAuthorizationError`` so an attacker
    cannot enumerate orders by id.

    Raises:
        AdCPAuthorizationError: order_id is not assigned to ``principal_id``
            (or no such order exists for this tenant — collapsed to the
            same error to avoid enumeration leaks).
        ValueError: ``media_buy_id`` is not a projected GAM id.
    """
    from src.core.database.repositories.media_buy import MediaBuyRepository

    if not is_projected_media_buy_id(media_buy_id):
        raise ValueError(f"{media_buy_id!r} is not a projected GAM media_buy_id")

    order_id = order_id_from_projected(media_buy_id)

    gam_repo = GAMSyncRepository(session, tenant_id)
    orders = gam_repo.list_orders_for_advertisers(gam_repo.list_advertiser_ids_assigned_to(principal_id))
    order = next((o for o in orders if o.order_id == order_id), None)
    if order is None:
        raise AdCPAuthorizationError(
            f"Order {order_id!r} is not assigned to principal {principal_id!r} (or does not exist)."
        )

    fields = order_to_media_buy_fields(order)
    advertiser = gam_repo.get_advertiser(order.advertiser_id) if order.advertiser_id else None
    advertiser_name = (
        (advertiser.name if advertiser else None) or order.advertiser_name or order.advertiser_id or "Unknown"
    )

    today = date.today()
    mb_repo = MediaBuyRepository(session, tenant_id)
    try:
        # Wrap the insert in a SAVEPOINT so a unique-index / PK collision
        # on a concurrent materialization rolls back ONLY our failed
        # insert — the caller's outer UoW transaction stays intact.
        # Without nested(), session.rollback() would discard everything
        # the caller has written before reaching this function.
        with session.begin_nested():
            media_buy = mb_repo.create_from_gam_import(
                media_buy_id=media_buy_id,
                principal_id=principal_id,
                order_name=order.name,
                advertiser_name=advertiser_name,
                budget=fields["budget"],
                currency=fields["currency"] or "USD",
                start_date=fields["start_date"] or today,
                end_date=fields["end_date"] or today,
                start_time=fields["start_time"],
                end_time=fields["end_time"],
                status=project_gam_status(order.status, fields["start_date"], fields["end_date"], today).value,
                external_id=order.order_id,
                raw_request=fields["raw_request"],
            )
    except IntegrityError:
        # Concurrent materialization won the race. The savepoint already
        # rolled back our failed insert; pick up the row the other
        # transaction wrote. Both the PK (media_buy_id) and the
        # ``(tenant_id, external_id) WHERE external_id IS NOT NULL``
        # unique index can fire this; either way the resolution is the
        # same — return the existing row, packages and all.
        existing = mb_repo.get_by_id(media_buy_id)
        if existing is None:
            # Extremely unlikely: the racing transaction was rolled back
            # AFTER we hit IntegrityError. Re-raise so the caller surfaces
            # a real failure rather than silently returning None.
            raise
        return existing

    for li in gam_repo.list_line_items_for_orders([order.order_id]):
        pkg_fields = line_item_to_package_fields(li)
        mb_repo.create_package(
            media_buy_id=media_buy_id,
            package_id=pkg_fields["package_id"],
            package_config=pkg_fields["package_config"],
            budget=pkg_fields["budget"],
            bid_price=pkg_fields["bid_price"],
        )

    return media_buy


def log_materialization_audit(
    *,
    tenant_id: str,
    principal_id: str,
    advertiser_name: str,
    advertiser_id: str | None,
    order_id: str,
    media_buy_id: str,
) -> None:
    """Emit an audit-log entry for an imported-buy materialization.

    Call this AFTER the parent UoW has committed. Audit logging opens its
    own DB session for the write; doing it inside the UoW that just
    flushed the new MediaBuy expires the freshly-added instance and
    breaks downstream session reads.

    Operators querying "when did agent X first claim our GAM order Y?"
    filter on ``operation = 'AdCP.materialize_imported_buy'`` plus
    ``principal_id`` and ``details->>'gam_order_id'``.
    """
    from src.core.audit_logger import get_audit_logger

    get_audit_logger("AdCP", tenant_id).log_operation(
        operation="materialize_imported_buy",
        principal_name=advertiser_name,
        principal_id=principal_id,
        adapter_id=advertiser_id or "",
        success=True,
        details={
            "media_buy_id": media_buy_id,
            "gam_order_id": order_id,
            "gam_advertiser_id": advertiser_id,
            "source": "gam_import",
        },
    )


def get_or_materialize_media_buy(
    session: Session,
    tenant_id: str,
    principal_id: str,
    media_buy_id: str,
) -> MediaBuy:
    """Return a real MediaBuy row, materializing on demand for projected IDs.

    For non-projected ids this is a plain ``get_by_id_or_external_id``
    against ``MediaBuyRepository``. For projected (``gam_<order_id>``)
    ids it materializes if no row exists yet.

    Raises:
        AdCPNotFoundError: id is unknown to this tenant.
        AdCPAuthorizationError: caller is not assigned to the advertiser
            (projected materialization path only).
    """
    from src.core.database.repositories.media_buy import MediaBuyRepository

    repo = MediaBuyRepository(session, tenant_id)
    existing = repo.get_by_id_or_external_id(media_buy_id)
    if existing is not None:
        return existing

    if is_projected_media_buy_id(media_buy_id):
        return materialize_projected_buy(session, tenant_id, principal_id, media_buy_id)

    raise AdCPNotFoundError(f"Media buy {media_buy_id!r} not found.")
