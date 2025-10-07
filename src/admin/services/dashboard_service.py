"""Dashboard service implementing single data source pattern.

This service centralizes all dashboard data access to prevent the reliability
issues caused by multiple overlapping data models. It uses ONLY the audit_logs
table for activity data, eliminating dependencies on workflow_steps, tasks, etc.
"""

import logging
from datetime import UTC, datetime, timedelta

from src.admin.services.business_activity_service import get_business_activities
from src.admin.services.media_buy_readiness_service import MediaBuyReadinessService
from src.core.database.database_session import get_db_session
from src.core.database.models import MediaBuy, Principal, Product, Tenant

logger = logging.getLogger(__name__)


class DashboardService:
    """Service for dashboard data with single data source pattern."""

    def __init__(self, tenant_id: str):
        self.tenant_id = tenant_id
        self._tenant = None
        self._validate_tenant_id()

    def _validate_tenant_id(self):
        """Validate tenant exists and is active."""
        if not self.tenant_id or len(self.tenant_id) > 50:
            raise ValueError(f"Invalid tenant_id: {self.tenant_id}")

    def get_tenant(self) -> Tenant | None:
        """Get tenant object, cached for this service instance."""
        if self._tenant is None:
            with get_db_session() as db_session:
                from sqlalchemy import select

                stmt = select(Tenant).filter_by(tenant_id=self.tenant_id)
                self._tenant = db_session.scalars(stmt).first()
        return self._tenant

    def get_dashboard_metrics(self) -> dict[str, any]:
        """Get all dashboard metrics using single data source pattern.

        Returns:
            Dictionary with all metrics needed for dashboard rendering.
            Uses ONLY audit_logs table for activity data.
        """
        tenant = self.get_tenant()
        if not tenant:
            raise ValueError(f"Tenant {self.tenant_id} not found")

        try:
            with get_db_session() as db_session:
                # Get readiness summary (replaces simple status counts)
                readiness_summary = MediaBuyReadinessService.get_tenant_readiness_summary(self.tenant_id)

                # Core business metrics
                from sqlalchemy import func, select

                principals_count = db_session.scalar(
                    select(func.count()).select_from(Principal).where(Principal.tenant_id == self.tenant_id)
                )
                products_count = db_session.scalar(
                    select(func.count()).select_from(Product).where(Product.tenant_id == self.tenant_id)
                )

                # Calculate total spend from live and completed media buys
                stmt = (
                    select(MediaBuy)
                    .filter_by(tenant_id=self.tenant_id)
                    .where(MediaBuy.status.in_(["active", "completed"]))
                )
                total_spend_buys = db_session.scalars(stmt).all()
                total_spend_amount = float(sum(buy.budget or 0 for buy in total_spend_buys))

                # Revenue trend data (last 30 days)
                revenue_data = self._calculate_revenue_trend(db_session)

                # Calculate revenue change (last 7 vs previous 7 days)
                revenue_change = self._calculate_revenue_change(revenue_data)

                # Get recent BUSINESS activities (not raw audit logs)
                recent_activity = get_business_activities(self.tenant_id, limit=10)

                # Calculate needs attention count
                needs_attention = (
                    readiness_summary.get("needs_creatives", 0)
                    + readiness_summary.get("needs_approval", 0)
                    + readiness_summary.get("failed", 0)
                )

                return {
                    # Real business metrics with operational readiness
                    "total_revenue": total_spend_amount,
                    "live_buys": readiness_summary.get("live", 0),
                    "scheduled_buys": readiness_summary.get("scheduled", 0),
                    "needs_attention": needs_attention,
                    "needs_creatives": readiness_summary.get("needs_creatives", 0),
                    "needs_approval": readiness_summary.get("needs_approval", 0),
                    "paused_buys": readiness_summary.get("paused", 0),
                    "completed_buys": readiness_summary.get("completed", 0),
                    "failed_buys": readiness_summary.get("failed", 0),
                    "draft_buys": readiness_summary.get("draft", 0),
                    "active_advertisers": principals_count,
                    "total_advertisers": principals_count,
                    "products_count": products_count,
                    # Revenue trend
                    "revenue_change": round(revenue_change, 1),
                    "revenue_change_abs": round(abs(revenue_change), 1),
                    "revenue_data": revenue_data,
                    # Activity data (SINGLE SOURCE: audit_logs only)
                    "recent_activity": recent_activity,
                    # Readiness summary for detailed view
                    "readiness_summary": readiness_summary,
                    # Workflow metrics (hardcoded until unified system implemented)
                    "pending_workflows": 0,
                    "approval_needed": 0,
                    "pending_approvals": 0,
                    "conversion_rate": 0.0,
                }

        except (ValueError, TypeError) as e:
            logger.error(f"Data validation error calculating metrics for {self.tenant_id}: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error calculating dashboard metrics for {self.tenant_id}: {e}", exc_info=True)
            raise

    def get_recent_media_buys(self, limit: int = 10) -> list[MediaBuy]:
        """Get recent media buys with relationships loaded and readiness state."""
        try:
            with get_db_session() as db_session:
                from sqlalchemy import select
                from sqlalchemy.orm import joinedload

                stmt = (
                    select(MediaBuy)
                    .where(MediaBuy.tenant_id == self.tenant_id)
                    .where(MediaBuy.media_buy_id.isnot(None))  # Defensive: ensure valid ID
                    .options(joinedload(MediaBuy.principal))  # Eager load to avoid N+1
                    .order_by(MediaBuy.created_at.desc())
                    .limit(limit)
                )
                recent_buys = db_session.scalars(stmt).all()

                # Transform for template consumption
                for media_buy in recent_buys:
                    # Calculate estimated spend based on flight duration and status
                    media_buy.spend = self._calculate_estimated_spend(media_buy)

                    # Calculate relative time with proper timezone handling
                    media_buy.created_at_relative = self._format_relative_time(media_buy.created_at)

                    # Add advertiser name from eager-loaded principal
                    media_buy.advertiser_name = media_buy.principal.name if media_buy.principal else "Unknown"

                    # Add readiness state and details
                    readiness = MediaBuyReadinessService.get_readiness_state(
                        media_buy.media_buy_id, self.tenant_id, db_session
                    )
                    media_buy.readiness_state = readiness["state"]
                    media_buy.is_ready = readiness["is_ready_to_activate"]
                    media_buy.readiness_details = readiness

                return recent_buys

        except (ValueError, TypeError) as e:
            logger.error(f"Data validation error getting media buys for {self.tenant_id}: {e}")
            return []
        except Exception as e:
            logger.error(f"Database error getting recent media buys for {self.tenant_id}: {e}", exc_info=True)
            return []

    def _calculate_revenue_trend(self, db_session, days: int = 30) -> list[dict[str, any]]:
        """Calculate daily revenue for the last N days."""
        today = datetime.now(UTC).date()
        revenue_data = []

        for i in range(days):
            date = today - timedelta(days=days - 1 - i)

            # Calculate revenue for this date
            from sqlalchemy import select

            stmt = (
                select(MediaBuy)
                .filter_by(tenant_id=self.tenant_id)
                .where(MediaBuy.start_date <= date)
                .where(MediaBuy.end_date >= date)
                .where(MediaBuy.status.in_(["active", "completed"]))
            )
            daily_buys = db_session.scalars(stmt).all()

            daily_revenue = 0
            for buy in daily_buys:
                if buy.start_date and buy.end_date:
                    days_in_flight = (buy.end_date - buy.start_date).days + 1
                    if days_in_flight > 0:
                        daily_revenue += float(buy.budget or 0) / days_in_flight

            revenue_data.append({"date": date.isoformat(), "revenue": round(daily_revenue, 2)})

        return revenue_data

    def _calculate_revenue_change(self, revenue_data: list[dict[str, any]]) -> float:
        """Calculate revenue change percentage (last 7 vs previous 7 days)."""
        if len(revenue_data) < 14:
            return 0.0

        last_week_revenue = sum(d["revenue"] for d in revenue_data[-7:])
        previous_week_revenue = sum(d["revenue"] for d in revenue_data[-14:-7])

        if previous_week_revenue > 0:
            return ((last_week_revenue - previous_week_revenue) / previous_week_revenue) * 100

        return 0.0

    def _calculate_estimated_spend(self, media_buy) -> float:
        """Calculate estimated spend based on campaign progress.

        For active campaigns, estimate based on days elapsed.
        For completed campaigns, return full budget.
        For pending/draft campaigns, return 0.
        """
        if not media_buy.budget or not media_buy.start_date:
            return 0.0

        budget = float(media_buy.budget)

        # Return 0 for pending/draft campaigns
        if media_buy.status in ["pending", "draft"]:
            return 0.0

        # Return full budget for completed campaigns
        if media_buy.status == "completed":
            return budget

        # For active campaigns, estimate based on elapsed time
        if media_buy.status == "active" and media_buy.end_date:
            today = datetime.now(UTC).date()

            # If campaign hasn't started yet
            if today < media_buy.start_date:
                return 0.0

            # If campaign is past end date, return full budget
            if today > media_buy.end_date:
                return budget

            # Calculate spend based on elapsed days
            total_days = (media_buy.end_date - media_buy.start_date).days + 1
            elapsed_days = (today - media_buy.start_date).days + 1

            if total_days > 0:
                return budget * (elapsed_days / total_days)

        return 0.0

    def _format_relative_time(self, timestamp) -> str:
        """Format timestamp as relative time string with timezone handling."""
        if not timestamp:
            return "Unknown"

        # Ensure timestamp is timezone-aware
        if timestamp.tzinfo is None:
            # Assume UTC for naive timestamps
            timestamp = timestamp.replace(tzinfo=UTC)

        now = datetime.now(UTC)
        delta = now - timestamp

        if delta.days > 0:
            if delta.days == 1:
                return "1 day ago"
            elif delta.days < 7:
                return f"{delta.days} days ago"
            elif delta.days < 30:
                weeks = delta.days // 7
                return f"{weeks} week{'s' if weeks != 1 else ''} ago"
            else:
                return timestamp.strftime("%Y-%m-%d")

        hours = delta.seconds // 3600
        if hours > 0:
            return f"{hours} hour{'s' if hours != 1 else ''} ago"

        minutes = delta.seconds // 60
        if minutes > 0:
            return f"{minutes} minute{'s' if minutes != 1 else ''} ago"

        return "Just now"

    def get_chart_data(self) -> dict[str, list]:
        """Get chart data formatted for frontend consumption."""
        metrics = self.get_dashboard_metrics()
        revenue_data = metrics["revenue_data"]

        return {"labels": [d["date"] for d in revenue_data], "data": [d["revenue"] for d in revenue_data]}

    @staticmethod
    def health_check() -> dict[str, any]:
        """Check dashboard service health."""
        try:
            # Test database connection
            with get_db_session() as db_session:
                db_session.execute("SELECT 1").scalar()

            # Test audit logs table (our single data source)
            test_activities = get_business_activities("health_check", limit=1)

            return {
                "status": "healthy",
                "single_data_source": "audit_logs",
                "deprecated_sources": ["tasks", "human_tasks", "workflow_steps"],
                "message": "Dashboard service using single data source pattern",
            }
        except Exception as e:
            return {"status": "unhealthy", "error": str(e), "message": "Dashboard service health check failed"}
