"""Database query helper functions for complex queries.

This module contains reusable query functions for common database operations
that are too complex for inline code or used across multiple modules.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.database.models import Creative, CreativeReview


def get_creative_reviews(
    session: Session,
    creative_id: str,
    order_by_newest: bool = True,
) -> list[CreativeReview]:
    """Get all reviews for a creative.

    Args:
        session: Database session
        creative_id: Creative ID to query
        order_by_newest: If True, newest first; if False, oldest first

    Returns:
        List of CreativeReview objects
    """
    stmt = select(CreativeReview).filter_by(creative_id=creative_id)

    if order_by_newest:
        stmt = stmt.order_by(CreativeReview.reviewed_at.desc())
    else:
        stmt = stmt.order_by(CreativeReview.reviewed_at.asc())

    return list(session.scalars(stmt).all())


def get_ai_review_stats(
    session: Session,
    tenant_id: str,
    days: int = 30,
) -> dict:
    """Get AI review statistics for analytics dashboard.

    Args:
        session: Database session
        tenant_id: Tenant ID to query
        days: Number of days to look back (default: 30)

    Returns:
        Dict with statistics:
        - total_reviews: Total AI reviews performed
        - auto_approved: Count of auto-approved creatives
        - auto_rejected: Count of auto-rejected creatives
        - required_human: Count requiring human review
        - human_overrides: Count of human overrides of AI decisions
        - override_rate: Percentage of AI decisions overridden by humans
        - avg_confidence: Average confidence score
        - approval_rate: Percentage of creatives approved by AI
        - policy_breakdown: Dict of policy_triggered -> count
    """
    cutoff_date = datetime.now(UTC) - timedelta(days=days)

    # Base query for AI reviews in time period
    base_stmt = select(CreativeReview).filter(
        CreativeReview.tenant_id == tenant_id,
        CreativeReview.review_type == "ai",
        CreativeReview.reviewed_at >= cutoff_date,
    )

    all_reviews = list(session.scalars(base_stmt).all())
    total_reviews = len(all_reviews)

    if total_reviews == 0:
        return {
            "total_reviews": 0,
            "auto_approved": 0,
            "auto_rejected": 0,
            "required_human": 0,
            "human_overrides": 0,
            "override_rate": 0.0,
            "avg_confidence": 0.0,
            "approval_rate": 0.0,
            "policy_breakdown": {},
        }

    # Calculate statistics
    auto_approved = sum(1 for r in all_reviews if r.final_decision == "approved" and not r.human_override)
    auto_rejected = sum(1 for r in all_reviews if r.final_decision == "rejected" and not r.human_override)
    required_human = sum(1 for r in all_reviews if r.final_decision == "pending")
    human_overrides = sum(1 for r in all_reviews if r.human_override)

    # Calculate averages
    confidence_scores = [r.confidence_score for r in all_reviews if r.confidence_score is not None]
    avg_confidence = sum(confidence_scores) / len(confidence_scores) if confidence_scores else 0.0

    approved_count = sum(1 for r in all_reviews if r.final_decision == "approved")
    approval_rate = (approved_count / total_reviews * 100) if total_reviews > 0 else 0.0

    override_rate = (human_overrides / total_reviews * 100) if total_reviews > 0 else 0.0

    # Policy breakdown
    policy_breakdown: dict[str, int] = {}
    for review in all_reviews:
        if review.policy_triggered:
            policy_breakdown[review.policy_triggered] = policy_breakdown.get(review.policy_triggered, 0) + 1

    return {
        "total_reviews": total_reviews,
        "auto_approved": auto_approved,
        "auto_rejected": auto_rejected,
        "required_human": required_human,
        "human_overrides": human_overrides,
        "override_rate": round(override_rate, 2),
        "avg_confidence": round(avg_confidence, 2),
        "approval_rate": round(approval_rate, 2),
        "policy_breakdown": policy_breakdown,
    }


def get_recent_reviews(
    session: Session,
    tenant_id: str,
    limit: int = 10,
    review_type: str | None = None,
) -> list[CreativeReview]:
    """Get most recent reviews for a tenant.

    Args:
        session: Database session
        tenant_id: Tenant ID to query
        limit: Maximum number of reviews to return
        review_type: Optional filter by review type ("ai" or "human")

    Returns:
        List of CreativeReview objects ordered by newest first
    """
    stmt = select(CreativeReview).filter_by(tenant_id=tenant_id)

    if review_type:
        stmt = stmt.filter_by(review_type=review_type)

    stmt = stmt.order_by(CreativeReview.reviewed_at.desc()).limit(limit)

    return list(session.scalars(stmt).all())


def get_creative_with_latest_review(
    session: Session,
    creative_id: str,
) -> tuple[Creative | None, CreativeReview | None]:
    """Get a creative and its most recent review.

    Args:
        session: Database session
        creative_id: Creative ID to query

    Returns:
        Tuple of (Creative, CreativeReview) or (Creative, None) or (None, None)
    """
    # Get creative
    stmt = select(Creative).filter_by(creative_id=creative_id)
    creative = session.scalars(stmt).first()

    if not creative:
        return None, None

    # Get latest review
    review_stmt = (
        select(CreativeReview).filter_by(creative_id=creative_id).order_by(CreativeReview.reviewed_at.desc()).limit(1)
    )

    latest_review = session.scalars(review_stmt).first()

    return creative, latest_review


def get_creatives_needing_human_review(
    session: Session,
    tenant_id: str,
    limit: int = 50,
) -> list[tuple[Creative, CreativeReview]]:
    """Get creatives that need human review along with their AI review.

    Args:
        session: Database session
        tenant_id: Tenant ID to query
        limit: Maximum number of creatives to return

    Returns:
        List of (Creative, CreativeReview) tuples for pending creatives
    """
    # Get pending creatives with their latest AI review
    stmt = (
        select(Creative, CreativeReview)
        .join(CreativeReview, Creative.creative_id == CreativeReview.creative_id)
        .filter(
            Creative.tenant_id == tenant_id,
            Creative.status == "pending",
            CreativeReview.review_type == "ai",
        )
        .order_by(CreativeReview.reviewed_at.desc())
        .limit(limit)
    )

    results = session.execute(stmt).all()
    return [(row[0], row[1]) for row in results]


def get_ai_accuracy_metrics(
    session: Session,
    tenant_id: str,
    days: int = 30,
) -> dict:
    """Calculate AI accuracy metrics where human reviews exist.

    This measures how often humans agree with AI decisions.

    Args:
        session: Database session
        tenant_id: Tenant ID to query
        days: Number of days to look back

    Returns:
        Dict with accuracy metrics:
        - total_ai_reviews: Total AI reviews with human decisions
        - human_agreed: Count where human agreed with AI
        - human_disagreed: Count where human disagreed with AI
        - agreement_rate: Percentage where human agreed
        - by_policy: Breakdown by policy_triggered
    """
    cutoff_date = datetime.now(UTC) - timedelta(days=days)

    # Get all AI reviews that have human overrides
    stmt = select(CreativeReview).filter(
        CreativeReview.tenant_id == tenant_id,
        CreativeReview.review_type == "ai",
        CreativeReview.reviewed_at >= cutoff_date,
        CreativeReview.human_override.is_(True),
    )

    reviews_with_overrides = list(session.scalars(stmt).all())
    total_with_human_decisions = len(reviews_with_overrides)

    if total_with_human_decisions == 0:
        return {
            "total_ai_reviews": 0,
            "human_agreed": 0,
            "human_disagreed": 0,
            "agreement_rate": 0.0,
            "by_policy": {},
        }

    # All these reviews have human_override=True, meaning human disagreed
    human_disagreed = total_with_human_decisions
    human_agreed = 0  # For now, we only track overrides

    # Breakdown by policy
    by_policy = {}
    for review in reviews_with_overrides:
        policy = review.policy_triggered or "unknown"
        if policy not in by_policy:
            by_policy[policy] = {"total": 0, "overrides": 0}
        by_policy[policy]["total"] += 1
        by_policy[policy]["overrides"] += 1

    return {
        "total_ai_reviews": total_with_human_decisions,
        "human_agreed": human_agreed,
        "human_disagreed": human_disagreed,
        "agreement_rate": 0.0,  # 0% since all reviews in query have human_override=True
        "by_policy": by_policy,
    }
