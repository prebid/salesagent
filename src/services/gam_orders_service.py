"""
Service layer for GAM orders and line items management.

This service:
- Syncs orders and line items from GAM to database
- Provides browsing and search for orders and line items
- Tracks delivery stats and performance
- Handles updates and caching
"""

import logging
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import create_engine, or_, select
from sqlalchemy.orm import Session, joinedload, scoped_session, sessionmaker

from src.adapters.gam_orders_discovery import GAMOrdersDiscovery, LineItem, Order
from src.core.database.db_config import DatabaseConfig
from src.core.database.models import GAMLineItem, GAMOrder

# Create database session factory
engine = create_engine(DatabaseConfig.get_connection_string())
SessionLocal = sessionmaker(bind=engine)
# Use scoped_session for thread-local sessions
db_session = scoped_session(SessionLocal)

logger = logging.getLogger(__name__)


class GAMOrdersService:
    """Service for managing GAM orders and line items data."""

    def __init__(self, db_session: Session):
        self.db = db_session

    def sync_tenant_orders(self, tenant_id: str, gam_client) -> dict[str, Any]:
        """
        Sync all orders and line items for a tenant from GAM to database.

        Args:
            tenant_id: Tenant ID
            gam_client: Initialized GAM client

        Returns:
            Sync summary with counts and timing
        """
        logger.info(f"Starting orders sync for tenant {tenant_id}")

        # Create discovery instance
        discovery = GAMOrdersDiscovery(gam_client, tenant_id)

        # Perform discovery
        sync_summary = discovery.sync_all()

        # Save to database
        self._save_orders_to_db(tenant_id, discovery)

        return sync_summary

    def _save_orders_to_db(self, tenant_id: str, discovery: GAMOrdersDiscovery):
        """Save discovered orders and line items to database."""
        sync_time = datetime.now(UTC)

        # Process orders
        for order in discovery.orders.values():
            self._upsert_order(tenant_id, order, sync_time)

        # Process line items
        for line_item in discovery.line_items.values():
            self._upsert_line_item(tenant_id, line_item, sync_time)

        # Commit all changes
        self.db.commit()
        logger.info(f"Saved {len(discovery.orders)} orders and {len(discovery.line_items)} line items to database")

    def _upsert_order(self, tenant_id: str, order: Order, sync_time: datetime):
        """Insert or update an order."""
        stmt = select(GAMOrder).filter_by(tenant_id=tenant_id, order_id=order.order_id)
        existing = self.db.scalars(stmt).first()

        if existing:
            # Update existing order
            existing.name = order.name
            existing.advertiser_id = order.advertiser_id
            existing.advertiser_name = order.advertiser_name
            existing.agency_id = order.agency_id
            existing.agency_name = order.agency_name
            existing.trafficker_id = order.trafficker_id
            existing.trafficker_name = order.trafficker_name
            existing.salesperson_id = order.salesperson_id
            existing.salesperson_name = order.salesperson_name
            existing.status = order.status.value
            existing.start_date = order.start_date
            existing.end_date = order.end_date
            existing.unlimited_end_date = order.unlimited_end_date
            existing.total_budget = order.total_budget
            existing.currency_code = order.currency_code
            existing.external_order_id = order.external_order_id
            existing.po_number = order.po_number
            existing.notes = order.notes
            existing.last_modified_date = order.last_modified_date
            existing.is_programmatic = order.is_programmatic
            existing.applied_labels = order.applied_labels
            existing.effective_applied_labels = order.effective_applied_labels
            existing.custom_field_values = order.custom_field_values
            existing.order_metadata = order.order_metadata
            existing.last_synced = sync_time
        else:
            # Create new order
            new_order = GAMOrder(
                tenant_id=tenant_id,
                order_id=order.order_id,
                name=order.name,
                advertiser_id=order.advertiser_id,
                advertiser_name=order.advertiser_name,
                agency_id=order.agency_id,
                agency_name=order.agency_name,
                trafficker_id=order.trafficker_id,
                trafficker_name=order.trafficker_name,
                salesperson_id=order.salesperson_id,
                salesperson_name=order.salesperson_name,
                status=order.status.value,
                start_date=order.start_date,
                end_date=order.end_date,
                unlimited_end_date=order.unlimited_end_date,
                total_budget=order.total_budget,
                currency_code=order.currency_code,
                external_order_id=order.external_order_id,
                po_number=order.po_number,
                notes=order.notes,
                last_modified_date=order.last_modified_date,
                is_programmatic=order.is_programmatic,
                applied_labels=order.applied_labels,
                effective_applied_labels=order.effective_applied_labels,
                custom_field_values=order.custom_field_values,
                order_metadata=order.order_metadata,
                last_synced=sync_time,
            )
            self.db.add(new_order)

    def _upsert_line_item(self, tenant_id: str, line_item: LineItem, sync_time: datetime):
        """Insert or update a line item."""
        stmt = select(GAMLineItem).filter_by(tenant_id=tenant_id, line_item_id=line_item.line_item_id)
        existing = self.db.scalars(stmt).first()

        if existing:
            # Update existing line item
            existing.order_id = line_item.order_id
            existing.name = line_item.name
            existing.status = line_item.status.value
            existing.line_item_type = line_item.line_item_type
            existing.priority = line_item.priority
            existing.start_date = line_item.start_date
            existing.end_date = line_item.end_date
            existing.unlimited_end_date = line_item.unlimited_end_date
            existing.auto_extension_days = line_item.auto_extension_days
            existing.cost_type = line_item.cost_type
            existing.cost_per_unit = line_item.cost_per_unit
            existing.discount_type = line_item.discount_type
            existing.discount = line_item.discount
            existing.contracted_units_bought = line_item.contracted_units_bought
            existing.delivery_rate_type = line_item.delivery_rate_type
            existing.goal_type = line_item.goal_type
            existing.primary_goal_type = line_item.primary_goal_type
            existing.primary_goal_units = line_item.primary_goal_units
            existing.impression_limit = line_item.impression_limit
            existing.click_limit = line_item.click_limit
            existing.target_platform = line_item.target_platform
            existing.environment_type = line_item.environment_type
            existing.allow_overbook = line_item.allow_overbook
            existing.skip_inventory_check = line_item.skip_inventory_check
            existing.reserve_at_creation = line_item.reserve_at_creation

            # Update stats if available
            if line_item.stats:
                existing.stats_impressions = line_item.stats.get("impressions")
                existing.stats_clicks = line_item.stats.get("clicks")
                existing.stats_ctr = line_item.stats.get("ctr")
                existing.stats_video_completions = line_item.stats.get("video_completions")
                existing.stats_video_starts = line_item.stats.get("video_starts")
                existing.stats_viewable_impressions = line_item.stats.get("viewable_impressions")

            existing.delivery_indicator_type = line_item.delivery_indicator_type
            existing.delivery_data = line_item.delivery_data
            existing.targeting = line_item.targeting
            existing.creative_placeholders = line_item.creative_placeholders
            existing.frequency_caps = line_item.frequency_caps
            existing.applied_labels = line_item.applied_labels
            existing.effective_applied_labels = line_item.effective_applied_labels
            existing.custom_field_values = line_item.custom_field_values
            existing.third_party_measurement_settings = line_item.third_party_measurement_settings
            existing.video_max_duration = line_item.video_max_duration
            existing.line_item_metadata = line_item.line_item_metadata
            existing.last_modified_date = line_item.last_modified_date
            existing.creation_date = line_item.creation_date
            existing.external_id = line_item.external_id
            existing.last_synced = sync_time
        else:
            # Create new line item
            new_line_item = GAMLineItem(
                tenant_id=tenant_id,
                line_item_id=line_item.line_item_id,
                order_id=line_item.order_id,
                name=line_item.name,
                status=line_item.status.value,
                line_item_type=line_item.line_item_type,
                priority=line_item.priority,
                start_date=line_item.start_date,
                end_date=line_item.end_date,
                unlimited_end_date=line_item.unlimited_end_date,
                auto_extension_days=line_item.auto_extension_days,
                cost_type=line_item.cost_type,
                cost_per_unit=line_item.cost_per_unit,
                discount_type=line_item.discount_type,
                discount=line_item.discount,
                contracted_units_bought=line_item.contracted_units_bought,
                delivery_rate_type=line_item.delivery_rate_type,
                goal_type=line_item.goal_type,
                primary_goal_type=line_item.primary_goal_type,
                primary_goal_units=line_item.primary_goal_units,
                impression_limit=line_item.impression_limit,
                click_limit=line_item.click_limit,
                target_platform=line_item.target_platform,
                environment_type=line_item.environment_type,
                allow_overbook=line_item.allow_overbook,
                skip_inventory_check=line_item.skip_inventory_check,
                reserve_at_creation=line_item.reserve_at_creation,
                stats_impressions=line_item.stats.get("impressions") if line_item.stats else None,
                stats_clicks=line_item.stats.get("clicks") if line_item.stats else None,
                stats_ctr=line_item.stats.get("ctr") if line_item.stats else None,
                stats_video_completions=line_item.stats.get("video_completions") if line_item.stats else None,
                stats_video_starts=line_item.stats.get("video_starts") if line_item.stats else None,
                stats_viewable_impressions=line_item.stats.get("viewable_impressions") if line_item.stats else None,
                delivery_indicator_type=line_item.delivery_indicator_type,
                delivery_data=line_item.delivery_data,
                targeting=line_item.targeting,
                creative_placeholders=line_item.creative_placeholders,
                frequency_caps=line_item.frequency_caps,
                applied_labels=line_item.applied_labels,
                effective_applied_labels=line_item.effective_applied_labels,
                custom_field_values=line_item.custom_field_values,
                third_party_measurement_settings=line_item.third_party_measurement_settings,
                video_max_duration=line_item.video_max_duration,
                line_item_metadata=line_item.line_item_metadata,
                last_modified_date=line_item.last_modified_date,
                creation_date=line_item.creation_date,
                external_id=line_item.external_id,
                last_synced=sync_time,
            )
            self.db.add(new_line_item)

    def get_orders(self, tenant_id: str, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """
        Get orders for a tenant with optional filtering.

        Args:
            tenant_id: Tenant ID
            filters: Optional filters (status, advertiser_id, date_range, has_line_items, etc.)

        Returns:
            List of orders as dictionaries
        """
        # Use eager loading to avoid N+1 queries
        stmt = select(GAMOrder).options(joinedload(GAMOrder.line_items)).filter_by(tenant_id=tenant_id)

        if filters:
            if "status" in filters:
                stmt = stmt.where(GAMOrder.status == filters["status"])
            if "advertiser_id" in filters:
                stmt = stmt.where(GAMOrder.advertiser_id == filters["advertiser_id"])
            if "search" in filters:
                search_term = f"%{filters['search']}%"
                stmt = stmt.where(
                    or_(
                        GAMOrder.name.ilike(search_term),
                        GAMOrder.po_number.ilike(search_term),
                        GAMOrder.external_order_id.ilike(search_term),
                        GAMOrder.advertiser_name.ilike(search_term),
                    )
                )
            if "start_date" in filters:
                stmt = stmt.where(GAMOrder.start_date >= filters["start_date"])
            if "end_date" in filters:
                stmt = stmt.where(GAMOrder.end_date <= filters["end_date"])

        stmt = stmt.order_by(GAMOrder.last_modified_date.desc())
        orders = self.db.scalars(stmt).all()

        # Apply has_line_items filter after fetching (requires checking line items)
        result = []
        for order in orders:
            order_dict = self._order_to_dict(order)
            if filters and "has_line_items" in filters:
                filter_value = filters["has_line_items"]
                if filter_value == "true" and not order_dict["has_line_items"]:
                    continue
                elif filter_value == "false" and order_dict["has_line_items"]:
                    continue
            result.append(order_dict)

        return result

    def get_line_items(
        self, tenant_id: str, order_id: str | None = None, filters: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """
        Get line items for a tenant with optional filtering.

        Args:
            tenant_id: Tenant ID
            order_id: Optional order ID to filter by
            filters: Optional filters (status, type, etc.)

        Returns:
            List of line items as dictionaries
        """
        stmt = select(GAMLineItem).filter_by(tenant_id=tenant_id)

        if order_id:
            stmt = stmt.filter_by(order_id=order_id)

        if filters:
            if "status" in filters:
                stmt = stmt.where(GAMLineItem.status == filters["status"])
            if "line_item_type" in filters:
                stmt = stmt.where(GAMLineItem.line_item_type == filters["line_item_type"])
            if "search" in filters:
                search_term = f"%{filters['search']}%"
                stmt = stmt.where(GAMLineItem.name.ilike(search_term))
            if "priority" in filters:
                stmt = stmt.where(GAMLineItem.priority == filters["priority"])

        stmt = stmt.order_by(GAMLineItem.last_modified_date.desc())
        line_items = self.db.scalars(stmt).all()

        return [self._line_item_to_dict(li) for li in line_items]

    def get_order_details(self, tenant_id: str, order_id: str) -> dict[str, Any] | None:
        """
        Get detailed information about a specific order.

        Args:
            tenant_id: Tenant ID
            order_id: GAM order ID

        Returns:
            Order details with associated line items
        """
        stmt = select(GAMOrder).filter_by(tenant_id=tenant_id, order_id=order_id)
        order = self.db.scalars(stmt).first()

        if not order:
            return None

        # Get associated line items
        stmt = select(GAMLineItem).filter_by(tenant_id=tenant_id, order_id=order_id)
        line_items = self.db.scalars(stmt).all()

        result = self._order_to_dict(order)
        result["line_items"] = [self._line_item_to_dict(li) for li in line_items]

        # Calculate aggregated stats
        result["stats"] = {
            "total_line_items": len(line_items),
            "active_line_items": len([li for li in line_items if li.status == "APPROVED"]),
            "total_impressions": sum(li.stats_impressions or 0 for li in line_items),
            "total_clicks": sum(li.stats_clicks or 0 for li in line_items),
            "total_spend": sum(
                (li.cost_per_unit or 0) * (li.stats_impressions or 0) / 1000
                for li in line_items
                if li.cost_type == "CPM"
            ),
        }

        return result

    def _order_to_dict(self, order: GAMOrder) -> dict[str, Any]:
        """Convert order model to dictionary with delivery metrics."""
        # Use pre-loaded line items if available (from eager loading), otherwise query
        if hasattr(order, "line_items") and order.line_items is not None:
            line_items = order.line_items
        else:
            # Fallback to query if not eager loaded
            stmt = select(GAMLineItem).filter_by(tenant_id=order.tenant_id, order_id=order.order_id)
            line_items = self.db.scalars(stmt).all()

        # Calculate delivery status and metrics
        delivery_status = self._calculate_delivery_status(line_items)
        delivery_metrics = self._calculate_delivery_metrics(line_items)

        return {
            "order_id": order.order_id,
            "name": order.name,
            "advertiser_id": order.advertiser_id,
            "advertiser_name": order.advertiser_name,
            "agency_id": order.agency_id,
            "agency_name": order.agency_name,
            "trafficker_id": order.trafficker_id,
            "trafficker_name": order.trafficker_name,
            "salesperson_id": order.salesperson_id,
            "salesperson_name": order.salesperson_name,
            "status": order.status,
            "start_date": order.start_date.isoformat() if order.start_date else None,
            "end_date": order.end_date.isoformat() if order.end_date else None,
            "unlimited_end_date": order.unlimited_end_date,
            "total_budget": order.total_budget,
            "currency_code": order.currency_code,
            "external_order_id": order.external_order_id,
            "po_number": order.po_number,
            "notes": order.notes,
            "last_modified_date": order.last_modified_date.isoformat() if order.last_modified_date else None,
            "is_programmatic": order.is_programmatic,
            "applied_labels": order.applied_labels,
            "custom_field_values": order.custom_field_values,
            "last_synced": order.last_synced.isoformat() if order.last_synced else None,
            # Add delivery status and metrics
            "delivery_status": delivery_status,
            "delivery_metrics": delivery_metrics,
            "line_item_count": len(line_items),
            "has_line_items": len(line_items) > 0,
        }

    def _calculate_delivery_status(self, line_items: list[GAMLineItem]) -> str:
        """
        Calculate overall delivery status based on line item statuses.

        Returns:
            'DELIVERING' if any line items are actively delivering
            'READY' if any line items are ready to deliver
            'COMPLETED' if all line items are completed
            'PAUSED' if all line items are paused
            'DRAFT' if all line items are draft
            'NO_LINE_ITEMS' if no line items exist
            Order status as fallback
        """
        if not line_items:
            return "NO_LINE_ITEMS"

        statuses = [li.status for li in line_items]

        # Priority order: check for active delivery first
        if "DELIVERING" in statuses:
            return "DELIVERING"
        elif "READY" in statuses:
            return "READY"
        elif all(s == "COMPLETED" for s in statuses):
            return "COMPLETED"
        elif all(s == "PAUSED" for s in statuses):
            return "PAUSED"
        elif all(s == "DRAFT" for s in statuses):
            return "DRAFT"
        elif "APPROVED" in statuses:
            # APPROVED could mean various things, check dates
            now = datetime.now(UTC)
            for li in line_items:
                if li.status == "APPROVED":
                    if li.start_date and li.end_date:
                        # Convert dates to datetime for comparison if needed
                        import datetime as dt

                        start_dt = (
                            dt.datetime.combine(li.start_date, dt.datetime.min.time(), dt.UTC)
                            if isinstance(li.start_date, date)
                            else li.start_date
                        )
                        end_dt = (
                            dt.datetime.combine(li.end_date, dt.datetime.max.time(), dt.UTC)
                            if isinstance(li.end_date, date)
                            else li.end_date
                        )

                        if start_dt <= now <= end_dt:
                            return "DELIVERING"
                        elif now < start_dt:
                            return "READY"
                        elif now > end_dt:
                            return "COMPLETED"
            return "APPROVED"
        else:
            # Return most common status
            from collections import Counter

            status_counts = Counter(statuses)
            return status_counts.most_common(1)[0][0] if status_counts else "UNKNOWN"

    def _calculate_delivery_metrics(self, line_items: list[GAMLineItem]) -> dict[str, Any]:
        """
        Calculate delivery metrics for line items.

        Returns dict with:
            - total_impressions_delivered
            - total_impressions_goal
            - delivery_percentage
            - total_clicks
            - average_ctr
            - estimated_spend
        """
        metrics = {
            "total_impressions_delivered": 0,
            "total_impressions_goal": 0,
            "delivery_percentage": 0.0,
            "total_clicks": 0,
            "average_ctr": 0.0,
            "estimated_spend": 0.0,
        }

        if not line_items:
            return metrics

        total_impressions = 0
        total_clicks = 0
        total_goal = 0
        total_spend = 0.0

        for li in line_items:
            # Aggregate delivered stats
            if li.stats_impressions:
                total_impressions += li.stats_impressions
            if li.stats_clicks:
                total_clicks += li.stats_clicks

            # Aggregate goals
            if li.primary_goal_type == "IMPRESSIONS" and li.primary_goal_units:
                total_goal += li.primary_goal_units
            elif li.impression_limit:
                total_goal += li.impression_limit

            # Calculate spend (assuming CPM for now, could be enhanced)
            if li.cost_type == "CPM" and li.cost_per_unit and li.stats_impressions:
                # CPM = cost per thousand impressions
                spend = (li.stats_impressions / 1000.0) * li.cost_per_unit
                total_spend += spend
            elif li.cost_type == "CPC" and li.cost_per_unit and li.stats_clicks:
                # CPC = cost per click
                spend = li.stats_clicks * li.cost_per_unit
                total_spend += spend
            elif li.cost_type == "CPD" and li.cost_per_unit:
                # CPD = cost per day, calculate based on days elapsed
                if li.start_date and li.end_date:
                    now = datetime.now(UTC).date()
                    # Ensure dates are date objects for comparison
                    start_date = li.start_date.date() if isinstance(li.start_date, datetime) else li.start_date
                    end_date = li.end_date.date() if isinstance(li.end_date, datetime) else li.end_date
                    if start_date <= now:
                        days_elapsed = (min(now, end_date) - start_date).days + 1
                        spend = days_elapsed * li.cost_per_unit
                        total_spend += spend

        metrics["total_impressions_delivered"] = total_impressions
        metrics["total_impressions_goal"] = total_goal
        metrics["total_clicks"] = total_clicks
        metrics["estimated_spend"] = round(total_spend, 2)

        # Calculate delivery percentage
        if total_goal > 0:
            metrics["delivery_percentage"] = round((total_impressions / total_goal) * 100, 2)

        # Calculate average CTR
        if total_impressions > 0:
            metrics["average_ctr"] = round((total_clicks / total_impressions) * 100, 4)

        return metrics

    def _line_item_to_dict(self, line_item: GAMLineItem) -> dict[str, Any]:
        """Convert line item model to dictionary."""
        # Calculate delivery percentage for individual line item
        delivery_percentage = 0.0
        if (
            line_item.primary_goal_type == "IMPRESSIONS"
            and line_item.primary_goal_units
            and line_item.stats_impressions
        ):
            delivery_percentage = round((line_item.stats_impressions / line_item.primary_goal_units) * 100, 2)

        return {
            "line_item_id": line_item.line_item_id,
            "order_id": line_item.order_id,
            "name": line_item.name,
            "status": line_item.status,
            "line_item_type": line_item.line_item_type,
            "priority": line_item.priority,
            "start_date": line_item.start_date.isoformat() if line_item.start_date else None,
            "end_date": line_item.end_date.isoformat() if line_item.end_date else None,
            "unlimited_end_date": line_item.unlimited_end_date,
            "cost_type": line_item.cost_type,
            "cost_per_unit": line_item.cost_per_unit,
            "discount_type": line_item.discount_type,
            "discount": line_item.discount,
            "delivery_rate_type": line_item.delivery_rate_type,
            "primary_goal_type": line_item.primary_goal_type,
            "primary_goal_units": line_item.primary_goal_units,
            "environment_type": line_item.environment_type,
            "stats_impressions": line_item.stats_impressions,
            "stats_clicks": line_item.stats_clicks,
            "stats_ctr": line_item.stats_ctr,
            "delivery_indicator_type": line_item.delivery_indicator_type,
            "delivery_percentage": delivery_percentage,
            "targeting": line_item.targeting,
            "creative_placeholders": line_item.creative_placeholders,
            "last_modified_date": line_item.last_modified_date.isoformat() if line_item.last_modified_date else None,
            "creation_date": line_item.creation_date.isoformat() if line_item.creation_date else None,
            "last_synced": line_item.last_synced.isoformat() if line_item.last_synced else None,
        }
