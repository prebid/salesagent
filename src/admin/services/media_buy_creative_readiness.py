"""Shared creative finalize-readiness predicate for admin media-buy approve paths.

Used by workflows / operations / creatives blueprints so zero-assignment and
unapproved-creative hold decisions share one policy (issue #1696).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.database.models import Creative, CreativeAssignment

# Finalize gate: creative must be in this set. ``active`` is retained for legacy
# rows / parity with media_buy_create + former workflows/creatives checks even
# though CreativeStatusEnum no longer lists it.
FINALIZE_READY_CREATIVE_STATUSES: frozenset[str] = frozenset({"approved", "active"})


@dataclass(frozen=True)
class CreativeFinalizeReadiness:
    """Result of evaluating whether a media buy may proceed to adapter finalize."""

    ready: bool
    """True iff ≥1 assignment AND every linked creative is in the allowlist."""

    assignment_count: int
    unapproved_creative_ids: list[str]
    hold_reason: str | None  # "no_assignments" | "unapproved_creatives"


def evaluate_creative_finalize_readiness(
    session: Session,
    *,
    tenant_id: str,
    media_buy_id: str,
) -> CreativeFinalizeReadiness:
    """Evaluate whether creatives are ready for media-buy finalize / adapter create.

    Locked Hold semantics (#1696): zero CreativeAssignment rows ⇒ not ready
    (``hold_reason="no_assignments"``). Tenant-scoped assignment + creative queries.
    """
    assignments = session.scalars(
        select(CreativeAssignment).filter_by(tenant_id=tenant_id, media_buy_id=media_buy_id)
    ).all()
    assignment_count = len(assignments)

    if assignment_count == 0:
        return CreativeFinalizeReadiness(
            ready=False,
            assignment_count=0,
            unapproved_creative_ids=[],
            hold_reason="no_assignments",
        )

    creative_ids = [a.creative_id for a in assignments]
    creatives = session.scalars(
        select(Creative).filter(Creative.tenant_id == tenant_id, Creative.creative_id.in_(creative_ids))
    ).all()

    unapproved_creative_ids = [c.creative_id for c in creatives if c.status not in FINALIZE_READY_CREATIVE_STATUSES]
    # Missing creative rows (assignment points at deleted/missing) count as not ready.
    found_ids = {c.creative_id for c in creatives}
    for cid in creative_ids:
        if cid not in found_ids and cid not in unapproved_creative_ids:
            unapproved_creative_ids.append(cid)

    if unapproved_creative_ids:
        return CreativeFinalizeReadiness(
            ready=False,
            assignment_count=assignment_count,
            unapproved_creative_ids=unapproved_creative_ids,
            hold_reason="unapproved_creatives",
        )

    return CreativeFinalizeReadiness(
        ready=True,
        assignment_count=assignment_count,
        unapproved_creative_ids=[],
        hold_reason=None,
    )


def should_hold_media_buy_for_creatives(
    session: Session,
    *,
    tenant_id: str,
    media_buy_id: str,
) -> bool:
    """True when approve must park the buy (not call execute_approved_media_buy)."""
    return not evaluate_creative_finalize_readiness(session, tenant_id=tenant_id, media_buy_id=media_buy_id).ready
