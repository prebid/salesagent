"""Dashboard service implementing single data source pattern.

This service centralizes all dashboard data access to prevent the reliability
issues caused by multiple overlapping data models. It uses ONLY the audit_logs
table for activity data, eliminating dependencies on workflow_steps, tasks, etc.
"""

import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any
from typing import cast as type_cast

from flask import url_for

from src.admin.services.business_activity_service import get_business_activities
from src.admin.services.media_buy_readiness_service import MediaBuyReadinessService
from src.core.database.database_session import get_db_session
from src.core.database.models import AuditLog, AuthorizedProperty, Creative, MediaBuy, Principal, Product, Tenant
from src.core.database.repositories import AuditLogRepository, MediaBuyRepository
from src.core.schemas import CreativeStatusEnum

logger = logging.getLogger(__name__)


class DashboardService:
    """Service for dashboard data with single data source pattern."""

    def __init__(self, tenant_id: str):
        self.tenant_id = tenant_id
        self._tenant: Tenant | None = None
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

    def get_dashboard_metrics(self) -> dict[str, Any]:
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
                repo = MediaBuyRepository(db_session, self.tenant_id)

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
                total_spend_buys = repo.list_by_statuses(["active", "completed"])
                total_spend_amount = float(sum(buy.budget or 0 for buy in total_spend_buys))

                # Revenue trend data (last 30 days)
                revenue_data = self._calculate_revenue_trend(db_session, repo=repo)

                # Calculate revenue change (last 7 vs previous 7 days)
                revenue_change = self._calculate_revenue_change(revenue_data)

                # Get recent BUSINESS activities (not raw audit logs)
                recent_activity = get_business_activities(self.tenant_id, limit=10)

                # Count creatives pending review
                pending_creatives_count = db_session.scalar(
                    select(func.count())
                    .select_from(Creative)
                    .where(Creative.tenant_id == self.tenant_id)
                    .where(Creative.status == CreativeStatusEnum.pending_review.value)
                )

                # Calculate needs attention count (includes pending creatives)
                needs_attention = (
                    readiness_summary.get("needs_creatives", 0)
                    + readiness_summary.get("needs_approval", 0)
                    + readiness_summary.get("failed", 0)
                    + (pending_creatives_count or 0)
                )

                return {
                    # Real business metrics with operational readiness
                    "total_revenue": total_spend_amount,
                    "live_buys": readiness_summary.get("live", 0),
                    "scheduled_buys": readiness_summary.get("scheduled", 0),
                    "needs_attention": needs_attention,
                    "needs_creatives": readiness_summary.get("needs_creatives", 0),
                    "needs_approval": readiness_summary.get("needs_approval", 0),
                    "pending_creatives": pending_creatives_count or 0,
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

    def get_recent_media_buys(self, limit: int = 10) -> list:
        """Get recent media buys with relationships loaded and readiness state."""
        try:
            with get_db_session() as db_session:
                repo = MediaBuyRepository(db_session, self.tenant_id)
                recent_buys = repo.list_recent(limit, eager_load_principal=True)

                # Transform for template consumption
                # Note: Adding dynamic attributes to MediaBuy instances for template rendering
                # Using setattr for dynamic attributes that mypy can't know about
                for media_buy in recent_buys:
                    # Calculate estimated spend based on flight duration and status
                    # Using object.__setattr__ for dynamic template attributes that aren't on the model
                    object.__setattr__(media_buy, "spend", self._calculate_estimated_spend(media_buy))

                    # Calculate relative time with proper timezone handling
                    object.__setattr__(
                        media_buy, "created_at_relative", self._format_relative_time(media_buy.created_at)
                    )

                    # Add advertiser name from eager-loaded principal
                    media_buy.advertiser_name = media_buy.principal.name if media_buy.principal else "Unknown"

                    # Add readiness state and details
                    readiness = MediaBuyReadinessService.get_readiness_state(
                        media_buy.media_buy_id, self.tenant_id, db_session
                    )
                    object.__setattr__(media_buy, "readiness_state", readiness["state"])
                    object.__setattr__(media_buy, "is_ready", readiness["is_ready_to_activate"])
                    object.__setattr__(media_buy, "readiness_details", readiness)

                return list(recent_buys)

        except (ValueError, TypeError) as e:
            logger.error(f"Data validation error getting media buys for {self.tenant_id}: {e}")
            return []
        except Exception as e:
            logger.error(f"Database error getting recent media buys for {self.tenant_id}: {e}", exc_info=True)
            return []

    def _calculate_revenue_trend(
        self, db_session, days: int = 30, *, repo: MediaBuyRepository | None = None
    ) -> list[dict[str, Any]]:
        """Calculate daily revenue for the last N days."""
        if repo is None:
            repo = MediaBuyRepository(db_session, self.tenant_id)
        today = datetime.now(UTC).date()
        revenue_data = []

        for i in range(days):
            day = today - timedelta(days=days - 1 - i)

            daily_buys = repo.list_in_flight_on_date(day, statuses=["active", "completed"])

            daily_revenue = 0.0
            for buy in daily_buys:
                # Skip buys with no GAM delivery confirmation — budget alone is unreliable
                # (order may not have been created, needs creative, line item missing, etc.)
                if buy.delivered_amount is None:
                    continue
                start_date = type_cast(date | None, buy.start_date)
                end_date = type_cast(date | None, buy.end_date)
                if start_date and end_date:
                    days_in_flight = (end_date - start_date).days + 1
                    if days_in_flight > 0:
                        daily_revenue += float(buy.delivered_amount) / days_in_flight

            revenue_data.append({"date": day.isoformat(), "revenue": round(daily_revenue, 2)})

        return revenue_data

    def _calculate_revenue_change(self, revenue_data: list[dict[str, Any]]) -> float:
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

    # ------------------------------------------------------------------
    # Ledger dashboard — Incoming / Running / Pipeline three-stage view.
    # See https://github.com/bokelley/salesagent/issues/22 for the
    # principal→account refactor that will simplify the buyer-identity
    # resolution below.
    # ------------------------------------------------------------------

    def get_ledger_dashboard(self) -> dict[str, Any]:
        """Aggregate everything the Ledger dashboard renders.

        One bundled call — the dashboard template should not have to
        reach back into the service for piecemeal data.
        """

        with get_db_session() as session:
            repo = MediaBuyRepository(session, self.tenant_id)
            tenant = self._load_tenant(session)
            return {
                "masthead": self._masthead(session, tenant),
                "incoming": self._incoming(repo),
                "running": self._running(repo),
                "pipeline": self._pipeline(session),
                "revenue_chart": self._revenue_chart(session, repo, days=30),
                "needs_attention": self._needs_attention(session, repo),
                "activity_ledger": self._activity_ledger(session, limit=8),
            }

    def _load_tenant(self, session) -> Tenant | None:
        from sqlalchemy import select

        stmt = select(Tenant).filter_by(tenant_id=self.tenant_id)
        return session.scalars(stmt).first()

    def _masthead(self, session, tenant: Tenant | None) -> dict[str, Any]:
        from sqlalchemy import func, select

        properties_count = (
            session.scalar(
                select(func.count())
                .select_from(AuthorizedProperty)
                .where(AuthorizedProperty.tenant_id == self.tenant_id)
            )
            or 0
        )
        products_count = (
            session.scalar(select(func.count()).select_from(Product).where(Product.tenant_id == self.tenant_id)) or 0
        )

        last_brief_at = session.scalar(
            select(func.max(AuditLog.timestamp))
            .where(AuditLog.tenant_id == self.tenant_id)
            .where(AuditLog.operation == "get_products")
        )
        last_offer_at = session.scalar(
            select(func.max(MediaBuy.approved_at)).where(MediaBuy.tenant_id == self.tenant_id)
        )

        # Net revenue uses the delivery snapshot when present; falls back
        # to budget pro-rata (the legacy estimate) when the snapshot has
        # not been written yet (no delivery polls have happened).
        now = datetime.now(UTC)
        net_30d = self._net_revenue_in_window(session, now - timedelta(days=30), now)
        net_prior_30 = self._net_revenue_in_window(session, now - timedelta(days=60), now - timedelta(days=30))
        delta_pct = 0.0
        if net_prior_30 > 0:
            delta_pct = ((net_30d - net_prior_30) / net_prior_30) * 100

        return {
            "publisher_name": tenant.name if tenant else self.tenant_id,
            "publisher_domain": (tenant.subdomain if tenant else None) or "",
            "property_count": properties_count,
            "product_count": products_count,
            "agent_count": 1,
            "last_brief_at": last_brief_at,
            "last_offer_at": last_offer_at,
            "last_brief_relative": self._format_relative_time(last_brief_at) if last_brief_at else None,
            "last_offer_relative": self._format_relative_time(last_offer_at) if last_offer_at else None,
            "net_revenue_30d": float(net_30d),
            "net_revenue_prior_30": float(net_prior_30),
            "revenue_delta_pct": round(delta_pct, 1),
            "today_label": now.strftime("%a, %b %-d"),
        }

    def _net_revenue_in_window(self, session, start: datetime, end: datetime) -> float:
        """Net revenue for media buys approved in [start, end].

        Only counts buys with confirmed GAM delivery data (delivered_amount IS NOT NULL).
        Buys that never ran in GAM (needs creative, line item not created, etc.) have no
        delivery snapshot and are excluded — budget alone is not a reliable revenue signal.
        """
        from sqlalchemy import select

        stmt = (
            select(MediaBuy.delivered_amount)
            .where(MediaBuy.tenant_id == self.tenant_id)
            .where(MediaBuy.approved_at != None)  # noqa: E711
            .where(MediaBuy.approved_at >= start)
            .where(MediaBuy.approved_at < end)
            .where(MediaBuy.status.in_(["active", "completed"]))
            .where(MediaBuy.delivered_amount != None)  # noqa: E711
        )
        return sum(float(row) for (row,) in session.execute(stmt))

    def _incoming(self, repo: MediaBuyRepository) -> dict[str, Any]:
        """Offers waiting on a yes / no / counter from the publisher."""
        pending = repo.list_by_statuses(["pending_approval", "pending_creative_approval"])
        pending.sort(key=lambda b: b.created_at or datetime.min.replace(tzinfo=UTC), reverse=True)

        now = datetime.now(UTC)
        rows: list[dict[str, Any]] = []
        urgent_threshold = timedelta(hours=4)
        for buy in pending[:6]:
            created = (
                buy.created_at
                if buy.created_at and buy.created_at.tzinfo
                else (buy.created_at.replace(tzinfo=UTC) if buy.created_at else now)
            )
            age = now - created
            rows.append(
                {
                    "advertiser_name": buy.advertiser_name or "Unknown",
                    "order_name": buy.order_name or "",
                    "budget": float(buy.budget or 0),
                    "currency": buy.currency or "USD",
                    "age_relative": self._format_relative_time(buy.created_at),
                    "urgent": age >= urgent_threshold,
                    "media_buy_id": buy.media_buy_id,
                }
            )

        urgent_count = sum(1 for r in rows if r["urgent"])
        total_value = sum(r["budget"] for r in rows)
        return {
            "count": len(pending),
            "total_value": total_value,
            "urgent_count": urgent_count,
            "rows": rows,
        }

    def _running(self, repo: MediaBuyRepository) -> dict[str, Any]:
        """Live deals delivering now, with linear-pacing classification."""
        active = repo.list_by_statuses(["active", "live"])
        now = datetime.now(UTC)
        running_now = [b for b in active if not getattr(b, "is_paused", False)]
        running_now.sort(key=lambda b: b.approved_at or b.created_at or datetime.min.replace(tzinfo=UTC), reverse=True)

        rows: list[dict[str, Any]] = [self._running_row(buy, now) for buy in running_now[:6]]

        total_committed = sum(float(b.budget or 0) for b in running_now)
        return {
            "count": len(running_now),
            "total_value": total_committed,
            "rows": rows,
        }

    def _running_row(self, buy: MediaBuy, now: datetime) -> dict[str, Any]:
        """Compute pacing for a single running buy using the linear assumption.

        Linear: at any point in the flight, expected_pct = elapsed / total.
        Compare against actual delivery_pct = delivered_amount / budget
        (preferred) or delivered_impressions / planned_impressions.
        Threshold ±10% — narrower bands felt noisy in practice.
        """
        budget = float(buy.budget or 0)
        delivered = float(buy.delivered_amount) if buy.delivered_amount is not None else 0.0
        delivery_pct = (delivered / budget) if budget > 0 else 0.0

        # Flight progress.
        # Cast SQLAlchemy `Mapped[Date]` → stdlib `date` so arithmetic
        # type-checks (existing pattern in src/core/tools/media_buy_delivery.py).
        flight_pct = 0.0
        if buy.start_date and buy.end_date:
            buy_start = type_cast(date, buy.start_date)
            buy_end = type_cast(date, buy.end_date)
            total = (buy_end - buy_start).days + 1
            if total > 0:
                today = now.date()
                if today < buy_start:
                    flight_pct = 0.0
                elif today > buy_end:
                    flight_pct = 1.0
                else:
                    elapsed = (today - buy_start).days + 1
                    flight_pct = elapsed / total

        # Pacing classification (linear)
        threshold = 0.10
        if buy.delivered_amount is None:
            pacing = "unknown"
        elif delivery_pct < flight_pct - threshold:
            pacing = "under"
        elif delivery_pct > flight_pct + threshold:
            pacing = "over"
        else:
            pacing = "on-pace"

        # Budget rate — derive a /wk number for editorial display
        weeks = 0
        if buy.start_date and buy.end_date:
            buy_start = type_cast(date, buy.start_date)
            buy_end = type_cast(date, buy.end_date)
            weeks = max(1, ((buy_end - buy_start).days + 1) // 7)
        rate_per_week = budget / weeks if weeks else budget

        return {
            "advertiser_name": buy.advertiser_name or "Unknown",
            "order_name": buy.order_name or "",
            "budget": budget,
            "currency": buy.currency or "USD",
            "rate_per_week": rate_per_week,
            "delivery_pct": round(delivery_pct, 3),
            "flight_pct": round(flight_pct, 3),
            "pacing": pacing,
            "delivery_synced_at": buy.delivery_synced_at,
            "delivery_synced_relative": (
                self._format_relative_time(buy.delivery_synced_at) if buy.delivery_synced_at else None
            ),
            "media_buy_id": buy.media_buy_id,
        }

    def _pipeline(self, session) -> dict[str, Any]:
        """Unique buyers in market this week, grouped by (operator, brand_domain).

        The audit_logs row written by get_products carries operator + brand_domain
        in `details` JSON. We group there and surface the top buyers by recency.
        """
        from sqlalchemy import select

        window_days = 7
        new_buyer_lookback_days = 30
        window_start = datetime.now(UTC) - timedelta(days=window_days)
        new_buyer_cutoff = datetime.now(UTC) - timedelta(days=new_buyer_lookback_days)

        stmt = (
            select(AuditLog.timestamp, AuditLog.principal_id, AuditLog.principal_name, AuditLog.details)
            .where(AuditLog.tenant_id == self.tenant_id)
            .where(AuditLog.operation == "get_products")
            .where(AuditLog.timestamp >= window_start)
            .order_by(AuditLog.timestamp.desc())
        )

        # Group by (operator, brand_domain) — fall back to principal_id when
        # the brand isn't on the request (anonymous discovery, etc.).
        groups: dict[tuple[str, str | None], dict[str, Any]] = {}
        for ts, principal_id, principal_name, details in session.execute(stmt):
            details = details or {}
            operator = details.get("operator") or principal_id or "anonymous"
            brand_domain = details.get("brand_domain")
            key = (operator, brand_domain)
            grp = groups.setdefault(
                key,
                {
                    "operator": operator,
                    "brand_domain": brand_domain,
                    "display_name": brand_domain or principal_name or operator,
                    "brief_count": 0,
                    "last_brief_at": ts,
                },
            )
            grp["brief_count"] += 1
            grp["last_brief_at"] = max(grp["last_brief_at"], ts)

        # NEW = no get_products row from this (operator, brand_domain) in the
        # 30→7d window before this one. One extra query, indexed by timestamp.
        rows = list(groups.values())
        rows.sort(key=lambda r: r["last_brief_at"], reverse=True)

        if rows:
            prior_stmt = (
                select(AuditLog.principal_id, AuditLog.details)
                .where(AuditLog.tenant_id == self.tenant_id)
                .where(AuditLog.operation == "get_products")
                .where(AuditLog.timestamp >= new_buyer_cutoff)
                .where(AuditLog.timestamp < window_start)
            )
            prior_keys: set[tuple[str, str | None]] = set()
            for principal_id, details in session.execute(prior_stmt):
                details = details or {}
                op = details.get("operator") or principal_id or "anonymous"
                bd = details.get("brand_domain")
                prior_keys.add((op, bd))
            for r in rows:
                r["is_new"] = (r["operator"], r["brand_domain"]) not in prior_keys
                r["last_brief_relative"] = self._format_relative_time(r["last_brief_at"])
        new_count = sum(1 for r in rows if r.get("is_new"))

        return {
            "count": len(rows),
            "new_count": new_count,
            "rows": rows[:6],
            "window_days": window_days,
        }

    def _revenue_chart(self, session, repo: MediaBuyRepository, days: int = 30) -> list[dict[str, Any]]:
        """Per-day delivered revenue series. Falls back to flat-pace
        budget allocation for buys with no snapshot."""
        return self._calculate_revenue_trend(session, days=days, repo=repo)

    def _needs_attention(self, session, repo: MediaBuyRepository) -> list[dict[str, Any]]:
        """Bullet-list items for the right-rail attention panel.

        Each item includes a ``url`` already rooted at the request's
        SCRIPT_NAME (built via ``url_for``) so the template can render it
        directly — the rail is the operator's entry point into the queue,
        so every row must lead somewhere.
        """
        from sqlalchemy import func, select

        items = []

        pending_creatives_count = (
            session.scalar(
                select(func.count())
                .select_from(Creative)
                .where(Creative.tenant_id == self.tenant_id)
                .where(Creative.status == CreativeStatusEnum.pending_review.value)
            )
            or 0
        )
        if pending_creatives_count:
            items.append(
                {
                    "level": "amber",
                    "title": f"{pending_creatives_count} creative{'s' if pending_creatives_count != 1 else ''} need approval",
                    "sub": "Creative review queue",
                    "url": url_for("creatives.review_creatives", tenant_id=self.tenant_id),
                }
            )

        # Deals expiring in next 24h (live, end_date today or tomorrow).
        # Cast Mapped[Date] → stdlib date for the comparison (mypy strictness).
        active = repo.list_by_statuses(["active", "live"])
        today = datetime.now(UTC).date()
        tomorrow = today + timedelta(days=1)
        expiring = [b for b in active if b.end_date and today <= type_cast(date, b.end_date) <= tomorrow]
        if expiring:
            # Single buy → deep-link to its detail; multiple → filtered list.
            expiring_url = (
                url_for(
                    "operations.media_buy_detail",
                    tenant_id=self.tenant_id,
                    media_buy_id=expiring[0].media_buy_id,
                )
                if len(expiring) == 1
                else url_for("tenants.media_buys_list", tenant_id=self.tenant_id, status="live")
            )
            items.append(
                {
                    "level": "amber" if len(expiring) > 1 else "neutral",
                    "title": f"{len(expiring)} deal{'s' if len(expiring) != 1 else ''} expiring in <24h",
                    "sub": ", ".join(b.advertiser_name or "Unknown" for b in expiring[:3]),
                    "url": expiring_url,
                }
            )

        # Pacing under: scan running buys
        now = datetime.now(UTC)
        under = []
        for b in active:
            if getattr(b, "is_paused", False):
                continue
            row = self._running_row(b, now)
            if row["pacing"] == "under":
                under.append((b.media_buy_id, b.advertiser_name or "Unknown", row["delivery_pct"], row["flight_pct"]))
        for buy_id, name, dpct, fpct in under[:2]:
            items.append(
                {
                    "level": "amber",
                    "title": f"{name} pacing under",
                    "sub": f"{int(dpct * 100)}% delivered · should be {int(fpct * 100)}%",
                    "url": url_for("operations.media_buy_detail", tenant_id=self.tenant_id, media_buy_id=buy_id),
                }
            )

        if not items:
            # No url — the empty-state row is informational, not a link.
            items.append(
                {
                    "level": "neutral",
                    "title": "Nothing needs your attention",
                    "sub": "Everything is on pace.",
                }
            )

        return items

    def _activity_ledger(self, session, limit: int = 8) -> list[dict[str, Any]]:
        """Recent audit-log activity rendered for the ledger table.

        Distinct from `recent_activity` (business-summary view); this is the
        raw audit feed shown editorial-style with date+time, actor, event,
        object, status. Bounded to the last 7 days to match the UI label.
        """
        now = datetime.now(UTC)
        window_start = now - timedelta(days=7)
        repo = AuditLogRepository(session, self.tenant_id)
        today = now.date()
        rows = []
        for log in repo.list_filtered(from_date=window_start, limit=limit):
            ts = log.timestamp
            if ts is None:
                time_label = ""
            elif ts.date() == today:
                time_label = ts.strftime("%H:%M")
            else:
                time_label = ts.strftime("%b %d %H:%M")
            rows.append(
                {
                    "time": time_label,
                    "actor": log.principal_name or "System",
                    "operation": log.operation,
                    "object": (log.details or {}).get("media_buy_id") or (log.details or {}).get("brand_domain") or "",
                    "status": "ok" if log.success else "error",
                    "success": log.success,
                }
            )
        return rows

    @staticmethod
    def health_check() -> dict[str, Any]:
        """Check dashboard service health."""
        try:
            # Test database connection
            from sqlalchemy import text

            with get_db_session() as db_session:
                db_session.execute(text("SELECT 1")).scalar()

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
