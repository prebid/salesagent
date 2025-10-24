"""Media Buy Readiness Service - Computes operational readiness state.

This service determines the actual operational state of media buys by checking:
- Package configuration completeness
- Creative assignments
- Creative approval status
- Flight timing
- Blocking issues

No database schema changes required - computes state from existing data.
"""

import logging
from datetime import UTC, datetime
from typing import TypedDict

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.database.database_session import get_db_session
from src.core.database.models import Creative, CreativeAssignment, GAMLineItem, GAMOrder, MediaBuy, Tenant

logger = logging.getLogger(__name__)


class ReadinessDetails(TypedDict):
    """Detailed readiness information for a media buy."""

    state: str  # "draft", "needs_creatives", "needs_approval", "ready", "live", "paused", "completed", "failed"
    is_ready_to_activate: bool
    packages_total: int
    packages_with_creatives: int
    creatives_total: int
    creatives_approved: int
    creatives_pending: int
    creatives_rejected: int
    blocking_issues: list[str]
    warnings: list[str]
    gam_order_status: str | None  # GAM order status if using GAM adapter
    gam_line_items_total: int  # Number of line items in GAM
    gam_line_items_ready: int  # Number of approved/active line items


class MediaBuyReadinessService:
    """Service to compute operational readiness of media buys."""

    @staticmethod
    def get_readiness_state(media_buy_id: str, tenant_id: str, session: Session | None = None) -> ReadinessDetails:
        """Compute the operational readiness state for a media buy.

        Args:
            media_buy_id: Media buy identifier
            tenant_id: Tenant identifier
            session: Optional SQLAlchemy session (creates one if not provided)

        Returns:
            ReadinessDetails dict with complete readiness information
        """
        should_close = session is None
        if session is None:
            session = get_db_session().__enter__()

        try:
            # Get media buy
            stmt = select(MediaBuy).filter_by(tenant_id=tenant_id, media_buy_id=media_buy_id)
            media_buy = session.scalars(stmt).first()

            if not media_buy:
                return {
                    "state": "failed",
                    "is_ready_to_activate": False,
                    "packages_total": 0,
                    "packages_with_creatives": 0,
                    "creatives_total": 0,
                    "creatives_approved": 0,
                    "creatives_pending": 0,
                    "creatives_rejected": 0,
                    "blocking_issues": ["Media buy not found"],
                    "warnings": [],
                }

            # Check if already failed
            if media_buy.status == "failed":
                return {
                    "state": "failed",
                    "is_ready_to_activate": False,
                    "packages_total": 0,
                    "packages_with_creatives": 0,
                    "creatives_total": 0,
                    "creatives_approved": 0,
                    "creatives_pending": 0,
                    "creatives_rejected": 0,
                    "blocking_issues": ["Media buy creation failed"],
                    "warnings": [],
                }

            # Extract packages from raw_request
            raw_request = media_buy.raw_request or {}
            packages = raw_request.get("packages", [])
            packages_total = len(packages)

            # Get creative assignments for this media buy
            stmt = select(CreativeAssignment).filter_by(tenant_id=tenant_id, media_buy_id=media_buy_id)
            assignments = session.scalars(stmt).all()

            # Get unique package IDs that have creative assignments
            packages_with_assignments = {a.package_id for a in assignments}
            packages_with_creatives = len(packages_with_assignments)

            # Get all creative IDs
            creative_ids = list({a.creative_id for a in assignments})
            creatives_total = len(creative_ids)

            # Get creative statuses
            creatives = []
            if creative_ids:
                stmt = select(Creative).filter(Creative.tenant_id == tenant_id, Creative.creative_id.in_(creative_ids))
                creatives = session.scalars(stmt).all()

            creatives_approved = sum(1 for c in creatives if c.status == "approved")
            creatives_pending = sum(1 for c in creatives if c.status == "pending")
            creatives_rejected = sum(1 for c in creatives if c.status == "rejected")

            # Build blocking issues and warnings
            blocking_issues = []
            warnings = []

            # Check for packages without creatives
            if packages_total > 0 and packages_with_creatives < packages_total:
                missing_count = packages_total - packages_with_creatives
                blocking_issues.append(f"{missing_count} package(s) missing creative assignments")

            # Check for rejected creatives
            if creatives_rejected > 0:
                blocking_issues.append(f"{creatives_rejected} creative(s) rejected and need replacement")

            # Check for pending creatives
            if creatives_pending > 0:
                warnings.append(f"{creatives_pending} creative(s) pending approval")

            # Check if missing creatives entirely
            if creatives_total == 0 and packages_total > 0:
                blocking_issues.append("No creatives uploaded")

            # Check GAM status if using GAM adapter
            gam_order_status = None
            gam_line_items_total = 0
            gam_line_items_ready = 0

            # Determine if tenant uses GAM
            stmt = select(Tenant).filter_by(tenant_id=tenant_id)
            tenant = session.scalars(stmt).first()
            if tenant and tenant.ad_server == "google_ad_manager":
                # Check if we have GAM order data for this media buy
                stmt = select(GAMOrder).filter_by(tenant_id=tenant_id, order_id=media_buy_id)
                gam_order = session.scalars(stmt).first()

                if gam_order:
                    gam_order_status = gam_order.status
                    # Check for blocking GAM statuses
                    if gam_order.status in ["DRAFT", "PENDING_APPROVAL"]:
                        warnings.append(f"GAM order is {gam_order.status.replace('_', ' ').lower()}")
                    elif gam_order.status in ["CANCELED", "DELETED"]:
                        blocking_issues.append(f"GAM order is {gam_order.status.lower()}")

                    # Get line item statuses
                    stmt = select(GAMLineItem).filter_by(tenant_id=tenant_id, order_id=media_buy_id)
                    line_items = session.scalars(stmt).all()
                    gam_line_items_total = len(line_items)
                    gam_line_items_ready = sum(
                        1 for li in line_items if li.status in ["APPROVED", "DELIVERING", "READY"]
                    )

                    if gam_line_items_total > 0 and gam_line_items_ready < gam_line_items_total:
                        pending_count = gam_line_items_total - gam_line_items_ready
                        warnings.append(f"{pending_count} GAM line item(s) need approval")

            # Compute operational state
            now = datetime.now(UTC)
            state = MediaBuyReadinessService._compute_state(
                media_buy=media_buy,
                now=now,
                packages_total=packages_total,
                packages_with_creatives=packages_with_creatives,
                creatives_total=creatives_total,
                creatives_approved=creatives_approved,
                creatives_pending=creatives_pending,
                creatives_rejected=creatives_rejected,
                blocking_issues=blocking_issues,
            )

            # Determine if ready to activate
            # Note: "live" campaigns are already activated, but we consider them "ready"
            is_ready_to_activate = (
                len(blocking_issues) == 0
                and packages_total > 0
                and packages_with_creatives == packages_total
                and creatives_approved == creatives_total
                and state in ["scheduled", "live"]
            )

            return {
                "state": state,
                "is_ready_to_activate": is_ready_to_activate,
                "packages_total": packages_total,
                "packages_with_creatives": packages_with_creatives,
                "creatives_total": creatives_total,
                "creatives_approved": creatives_approved,
                "creatives_pending": creatives_pending,
                "creatives_rejected": creatives_rejected,
                "blocking_issues": blocking_issues,
                "warnings": warnings,
                "gam_order_status": gam_order_status,
                "gam_line_items_total": gam_line_items_total,
                "gam_line_items_ready": gam_line_items_ready,
            }

        finally:
            if should_close:
                session.close()

    @staticmethod
    def _compute_state(
        media_buy: MediaBuy,
        now: datetime,
        packages_total: int,
        packages_with_creatives: int,
        creatives_total: int,
        creatives_approved: int,
        creatives_pending: int,
        creatives_rejected: int,
        blocking_issues: list[str],
    ) -> str:
        """Compute the operational state based on media buy data.

        State hierarchy (in priority order):
        1. failed - Media buy creation failed
        2. paused - Explicitly paused
        3. needs_approval - Media buy itself awaiting manual approval (NOT creative approval)
        4. completed - Flight ended
        5. live - Currently serving (in flight, all creatives approved, no blockers)
        6. scheduled - Ready and waiting for start date
        7. needs_creatives - Creatives need action (missing, pending approval, or rejected)
        8. draft - Initial state, not configured
        """
        # Check explicit status first
        if media_buy.status == "failed":
            return "failed"

        if media_buy.status == "paused":
            return "paused"

        # Check if awaiting manual approval (highest priority - bypasses creative checks)
        if media_buy.status == "pending_approval":
            return "needs_approval"

        # Check flight timing - ensure timezone-aware datetimes
        if media_buy.start_time:
            start_time = (
                media_buy.start_time if media_buy.start_time.tzinfo else media_buy.start_time.replace(tzinfo=UTC)
            )
        else:
            start_time = datetime.combine(media_buy.start_date, datetime.min.time()).replace(tzinfo=UTC)

        if media_buy.end_time:
            end_time = media_buy.end_time if media_buy.end_time.tzinfo else media_buy.end_time.replace(tzinfo=UTC)
        else:
            end_time = datetime.combine(media_buy.end_date, datetime.max.time()).replace(tzinfo=UTC)

        # Completed if past end date
        if now > end_time:
            return "completed"

        # Check for blocking issues
        has_blockers = len(blocking_issues) > 0

        # Live: in flight, all creatives approved, no blockers
        if now >= start_time and now <= end_time and not has_blockers and creatives_approved == creatives_total:
            return "live"

        # Scheduled: ready but before start date
        if now < start_time and not has_blockers and creatives_approved == creatives_total and creatives_total > 0:
            return "scheduled"

        # Draft: initial state (no packages configured)
        if packages_total == 0:
            return "draft"

        # Needs creatives: ANY creative action needed (missing, pending, or rejected)
        # This includes creatives pending approval - they are still in "needs creatives" state
        if (
            packages_total > packages_with_creatives  # Missing creative assignments
            or creatives_pending > 0  # Creatives pending approval
            or creatives_rejected > 0  # Rejected creatives need replacement
            or creatives_total == 0  # No creatives at all
        ):
            return "needs_creatives"

        # Fallback (shouldn't reach here if logic is complete)
        return "draft"

    @staticmethod
    def get_tenant_readiness_summary(tenant_id: str) -> dict[str, int]:
        """Get counts of media buys by readiness state for a tenant.

        Returns:
            Dict mapping state names to counts, e.g.:
            {
                "live": 5,
                "scheduled": 2,
                "needs_creatives": 3,
                "needs_approval": 1,
                "paused": 1,
                "completed": 12,
                "failed": 0,
                "draft": 0
            }
        """
        with get_db_session() as session:
            # Get all media buys for tenant
            stmt = select(MediaBuy).filter_by(tenant_id=tenant_id)
            media_buys = session.scalars(stmt).all()

            # Initialize counts
            summary = {
                "live": 0,
                "scheduled": 0,
                "needs_creatives": 0,
                "needs_approval": 0,
                "paused": 0,
                "completed": 0,
                "failed": 0,
                "draft": 0,
            }

            # Compute state for each media buy
            for media_buy in media_buys:
                readiness = MediaBuyReadinessService.get_readiness_state(media_buy.media_buy_id, tenant_id, session)
                state = readiness["state"]
                summary[state] = summary.get(state, 0) + 1

            return summary
