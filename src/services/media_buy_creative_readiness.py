"""Domain creative finalize-readiness predicate for media-buy approve paths.

Shared by admin workflows / operations / creatives so zero-assignment and
unapproved-creative hold decisions share one policy (issue #1696). Neutral
module (not Flask-aware) — admin flash/commit lives in the admin facade.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Literal, cast

from sqlalchemy.orm import Session

from src.core.database.database_session import get_db_session
from src.core.database.models import MediaBuy, PersistedMediaBuyStatus
from src.core.database.repositories.creative import (
    CreativeAssignmentRepository,
    CreativeRepository,
)
from src.core.database.repositories.media_buy import MediaBuyRepository
from src.core.schemas.creative import FINALIZE_READY_CREATIVE_STATUSES
from src.core.utils import utc_flight_end, utc_flight_start

logger = logging.getLogger(__name__)

HoldReason = Literal["no_assignments", "unapproved_creatives"]
FinalizeKind = Literal["held", "adapter_failed", "finalized"]

_HOLD_MSG_NO_ASSIGNMENTS = (
    "Media buy approved! Waiting for creatives to be assigned and approved before creating in GAM."
)


@dataclass(frozen=True)
class CreativeFinalizeReadiness:
    """Result of evaluating whether a media buy may proceed to adapter finalize."""

    ready: bool
    """True iff ≥1 assignment AND every linked creative is in the allowlist."""

    unapproved_creative_ids: list[str]
    hold_reason: HoldReason | None
    hold_message: str | None = None


@dataclass(frozen=True)
class FinalizeOutcome:
    """Typed result of the shared approve → hold/ready → execute sequence."""

    kind: FinalizeKind
    hold_message: str | None = None
    hold_reason: HoldReason | None = None
    error_msg: str | None = None
    webhook_media_buy_status: str | None = None
    # Carried from ApprovalResult after adapter success so webhook producers do
    # not re-read a detached row for confirmed_at / revision (main writer contract).
    confirmed_at: datetime | None = None
    revision: int | None = None


def _hold_message_for(reason: HoldReason, unapproved_count: int) -> str:
    if reason == "no_assignments":
        return _HOLD_MSG_NO_ASSIGNMENTS
    return f"Media buy approved! Waiting for {unapproved_count} creative(s) to be approved before creating in GAM."


def evaluate_creative_finalize_readiness(
    assignments_repo: CreativeAssignmentRepository,
    creatives_repo: CreativeRepository,
    *,
    media_buy_id: str,
) -> CreativeFinalizeReadiness:
    """Evaluate whether creatives are ready for media-buy finalize / adapter create.

    Locked Hold semantics (#1696): zero CreativeAssignment rows ⇒ not ready
    (``hold_reason="no_assignments"``). Repositories are tenant-scoped; creative
    loads use the composite key via ``get_by_ids(..., principal_id)``.
    """
    assignments = assignments_repo.get_by_media_buy(media_buy_id)

    if not assignments:
        return CreativeFinalizeReadiness(
            ready=False,
            unapproved_creative_ids=[],
            hold_reason="no_assignments",
            hold_message=_hold_message_for("no_assignments", 0),
        )

    # Group by principal so each get_by_ids call matches the composite PK.
    by_principal: dict[str, list[str]] = {}
    for assignment in assignments:
        by_principal.setdefault(assignment.principal_id, []).append(assignment.creative_id)

    creatives = []
    for principal_id, creative_ids in by_principal.items():
        creatives.extend(creatives_repo.get_by_ids(creative_ids, principal_id))

    # dict preserves first-seen order; membership is O(1) (list `in` was O(n)).
    unapproved_ids: dict[str, None] = {
        c.creative_id: None for c in creatives if c.status not in FINALIZE_READY_CREATIVE_STATUSES
    }
    # Missing creative rows (assignment points at deleted/missing) count as not ready.
    found_ids = {c.creative_id for c in creatives}
    for cid in (a.creative_id for a in assignments):
        if cid not in found_ids:
            unapproved_ids[cid] = None
    unapproved_creative_ids = list(unapproved_ids)

    if unapproved_creative_ids:
        return CreativeFinalizeReadiness(
            ready=False,
            unapproved_creative_ids=unapproved_creative_ids,
            hold_reason="unapproved_creatives",
            hold_message=_hold_message_for("unapproved_creatives", len(unapproved_creative_ids)),
        )

    return CreativeFinalizeReadiness(
        ready=True,
        unapproved_creative_ids=[],
        hold_reason=None,
        hold_message=None,
    )


def evaluate_creative_finalize_readiness_for_session(
    session: Session,
    tenant_id: str,
    *,
    media_buy_id: str,
) -> CreativeFinalizeReadiness:
    """Session-level entry: construct tenant-scoped repos and evaluate readiness."""
    return evaluate_creative_finalize_readiness(
        CreativeAssignmentRepository(session, tenant_id),
        CreativeRepository(session, tenant_id),
        media_buy_id=media_buy_id,
    )


def log_creative_finalize_hold(
    media_buy_id: str,
    readiness: CreativeFinalizeReadiness,
    *,
    context_tag: str = "[APPROVAL]",
) -> None:
    """Log a finalize hold with an approval-trail tag and stable event key."""
    logger.info(
        "%s Creative finalize hold for media buy %s hold_reason=%s unapproved=%s event=creative_finalize_hold",
        context_tag,
        media_buy_id,
        readiness.hold_reason,
        readiness.unapproved_creative_ids,
    )


def stamp_media_buy_approval(media_buy: MediaBuy, *, approved_by: str) -> None:
    """Stamp approval provenance once — shared by hold and ready arms."""
    media_buy.approved_at = datetime.now(UTC)
    media_buy.approved_by = approved_by


def apply_creative_finalize_hold(
    media_buy: MediaBuy,
    readiness: CreativeFinalizeReadiness,
    *,
    approved_by: str,
) -> None:
    """Apply hold outcome: provenance + pending_creatives + single info log."""
    stamp_media_buy_approval(media_buy, approved_by=approved_by)
    media_buy.status = PersistedMediaBuyStatus.PENDING_CREATIVES
    log_creative_finalize_hold(media_buy.media_buy_id, readiness)


def apply_creative_finalize_ready(media_buy: MediaBuy, *, approved_by: str) -> None:
    """Apply ready outcome: provenance + flight-window status (mirror of hold)."""
    stamp_media_buy_approval(media_buy, approved_by=approved_by)
    media_buy.status = PersistedMediaBuyStatus.parse(
        compute_media_buy_status_from_flight_dates(media_buy),
        media_buy_id=media_buy.media_buy_id,
    )


def mark_media_buy_adapter_failed(
    media_buy_id: str,
    tenant_id: str,
    *,
    error_msg: str | None = None,
    status: PersistedMediaBuyStatus = PersistedMediaBuyStatus.FAILED,
    approved_at: datetime | None = None,
    approved_by: str | None = None,
) -> None:
    """Persist adapter-failure status and log with a single ``[APPROVAL]`` trail.

    Ready-arm approve routes (operations / workflows) commit an optimistic
    flight-window status before execute; pass the default ``FAILED`` to roll
    that back. The creatives unblock path executes before stamping and should
    stay recoverable — pass ``PENDING_CREATIVES`` there.
    """
    from src.core.logging_config import log_safe

    logger.error(
        "[APPROVAL] Adapter creation failed for %s: %s",
        log_safe(media_buy_id),
        log_safe(error_msg),
    )
    with get_db_session() as session:
        repo = MediaBuyRepository(session, tenant_id)
        if approved_at is not None or approved_by is not None:
            updated = repo.update_status(
                media_buy_id,
                status,
                approved_at=approved_at,
                approved_by=approved_by,
            )
        else:
            updated = repo.update_status(media_buy_id, status)
        if updated:
            session.commit()


def _execute_media_buy_adapter(
    media_buy_id: str,
    tenant_id: str,
    *,
    failure_status: PersistedMediaBuyStatus,
    approved_by: str | None = None,
    approved_at: datetime | None = None,
):
    """Execute adapter creation and single-home its persisted failure outcome."""
    from src.core.tools.media_buy_create import ApprovalOutcome, ApprovalResult, execute_approved_media_buy

    approval: ApprovalResult = execute_approved_media_buy(
        media_buy_id,
        tenant_id,
        approved_by=approved_by,
        approved_at=approved_at,
    )
    # Creative-hold is owned by evaluate_* above the call; a late HELD from the
    # writer is a race / double-gate miss — surface it, do not stamp FAILED over it.
    if approval.outcome is ApprovalOutcome.HELD_PENDING_CREATIVES:
        return approval
    if not approval.ok:
        if failure_status == PersistedMediaBuyStatus.FAILED:
            # Ready-arm (operations/workflows): operator approved the buy; record provenance
            # even when the adapter fails. Creatives unblock keeps recoverable pending_creatives
            # without stamping approval on the row (sole writer owns success path).
            mark_media_buy_adapter_failed(
                media_buy_id,
                tenant_id,
                error_msg=approval.error_msg,
                status=failure_status,
                approved_at=approved_at,
                approved_by=approved_by,
            )
        else:
            mark_media_buy_adapter_failed(
                media_buy_id,
                tenant_id,
                error_msg=approval.error_msg,
                status=failure_status,
            )
    return approval


def finalize_media_buy_approval(
    session: Session,
    tenant_id: str,
    media_buy: MediaBuy,
    *,
    approved_by: str,
) -> FinalizeOutcome:
    """Own the approve finalize sequence once for operations + workflows.

    evaluate readiness → hold (commit via repository) | ready (delegate to
    ``execute_approved_media_buy`` as the sole post-adapter writer). Callers
    only handle transport (flash / redirect / jsonify / webhook) from the
    typed outcome.

    Ready-arm does **not** pre-stamp flight-window status: that write belongs
    to the sole writer. Wire ``media_buy_status`` is still captured from the
    session-bound row for the webhook envelope (canonical date refine).
    """
    from src.core.media_buy_status import resolve_canonical_status
    from src.core.tools.media_buy_create import ApprovalOutcome

    media_buy_id = media_buy.media_buy_id
    readiness = evaluate_creative_finalize_readiness_for_session(session, tenant_id, media_buy_id=media_buy_id)
    approved_at = datetime.now(UTC)
    if not readiness.ready:
        # Repository write so revision bumps (direct ORM status assign does not).
        MediaBuyRepository(session, tenant_id).update_status(
            media_buy_id,
            PersistedMediaBuyStatus.PENDING_CREATIVES,
            approved_at=approved_at,
            approved_by=approved_by,
        )
        log_creative_finalize_hold(media_buy_id, readiness)
        session.commit()
        return FinalizeOutcome(
            kind="held",
            hold_message=readiness.hold_message,
            hold_reason=readiness.hold_reason,
        )

    logger.info("[APPROVAL] Executing adapter creation for approved media buy %s", media_buy_id)
    approval = _execute_media_buy_adapter(
        media_buy_id,
        tenant_id,
        failure_status=PersistedMediaBuyStatus.FAILED,
        approved_by=approved_by,
        approved_at=approved_at,
    )
    if approval.outcome is ApprovalOutcome.HELD_PENDING_CREATIVES:
        return FinalizeOutcome(
            kind="held",
            hold_message=approval.error_msg,
            hold_reason="unapproved_creatives",
        )
    if not approval.ok:
        return FinalizeOutcome(
            kind="adapter_failed",
            error_msg=approval.error_msg,
        )

    # Wire status must reflect what execute persisted (get_media_buys parity).
    session.expire_all()
    fresh = MediaBuyRepository(session, tenant_id).get_by_id(media_buy_id)
    webhook_media_buy_status = resolve_canonical_status(fresh, approved_at.date()) if fresh is not None else None

    logger.info("[APPROVAL] Adapter creation succeeded for %s", media_buy_id)
    return FinalizeOutcome(
        kind="finalized",
        webhook_media_buy_status=webhook_media_buy_status,
        confirmed_at=approval.confirmed_at,
        revision=approval.revision,
    )


def finalize_media_buy_after_creative_approval(
    media_buy_id: str,
    tenant_id: str,
    *,
    approved_by: str,
) -> FinalizeOutcome:
    """Own the creatives unblock sequence via the sole post-adapter writer.

    ``execute_approved_media_buy`` persists flight-window status on success.
    Adapter failure stays recoverable at ``pending_creatives`` so a later
    creative approval can retry (execute may have written FAILED; we restore).
    """
    from src.core.tools.media_buy_create import ApprovalOutcome

    logger.info("[CREATIVE APPROVAL] Executing adapter creation for unblocked media buy %s", media_buy_id)
    approved_at = datetime.now(UTC)
    approval = _execute_media_buy_adapter(
        media_buy_id,
        tenant_id,
        failure_status=PersistedMediaBuyStatus.PENDING_CREATIVES,
        approved_by=approved_by,
        approved_at=approved_at,
    )
    if approval.outcome is ApprovalOutcome.HELD_PENDING_CREATIVES or not approval.ok:
        return FinalizeOutcome(kind="adapter_failed", error_msg=approval.error_msg)

    logger.info("[CREATIVE APPROVAL] Media buy %s successfully created in adapter", media_buy_id)
    return FinalizeOutcome(
        kind="finalized",
        confirmed_at=approval.confirmed_at,
        revision=approval.revision,
    )


def _coerce_flight_boundary(
    dt: datetime | None,
    date_value: date | None,
    *,
    end_of_day: bool,
) -> datetime | None:
    """Normalize a start/end boundary from aware/naive datetime or date column."""
    if dt:
        return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)
    if date_value:
        return utc_flight_end(date_value) if end_of_day else utc_flight_start(date_value)
    return None


def compute_media_buy_status_from_flight_dates(media_buy: MediaBuy) -> str:
    """Compute post-approve *persisted* status from the flight window.

    Returns AdCP 3.1.1 lifecycle tokens that ``MEDIA_BUY_STATE_MACHINE`` /
    ``valid_actions_for_status`` understand: ``pending_start`` before flight,
    ``active`` in flight (including past end-of-flight).

    Past-end stays ``active`` here so the wire can refine to ``completed`` via
    ``resolve_canonical_status``. Persisting ``completed`` would hit the
    update-path terminal gate (``INVALID_STATE``); that is the opposite of
    legacy ``scheduled``, which the update path still accepts. Spec
    ``index.mdx @ 3.1.1`` L325 initial-status set also omits ``completed`` /
    ``scheduled`` as the approve-arm write.

    End-of-flight is owned by the wire refine / scheduler completed arm — this
    stamp only compares ``start_time`` (or ``start_date``) against now.
    """
    now = datetime.now(UTC)

    # MediaBuy annotates start_date/end_date as Mapped[Date] (SQLAlchemy type), not
    # Mapped[date]; runtime values are datetime.date. Cast bridges the model typo.
    start_time = _coerce_flight_boundary(
        media_buy.start_time,
        cast(date | None, media_buy.start_date),
        end_of_day=False,
    )

    if start_time and now < start_time:
        return "pending_start"
    return "active"
