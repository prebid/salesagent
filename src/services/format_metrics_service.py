"""
Format Performance Metrics Aggregation Service

Aggregates GAM reporting data by country + creative format for dynamic pricing.
Queries GAM ReportService with COUNTRY_CODE + CREATIVE_SIZE dimensions.
Stores results in format_performance_metrics table for fast lookup.

Used by DynamicPricingService to calculate floor_cpm, recommended_cpm, estimated_exposures.
"""

import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from src.adapters.gam_reporting_service import GAMReportingService
from src.core.database.database_session import get_db_session
from src.core.database.models import FormatPerformanceMetrics, Tenant

logger = logging.getLogger(__name__)


class FormatMetricsAggregationService:
    """Service for aggregating GAM reporting data by country + format."""

    def __init__(self, db_session: Session):
        self.db = db_session

    def aggregate_metrics_for_tenant(self, tenant_id: str, gam_client, period_days: int = 30) -> dict[str, Any]:
        """
        Aggregate format metrics for a tenant from GAM reporting.

        Args:
            tenant_id: Tenant ID
            gam_client: Initialized GAM client
            period_days: Number of days to aggregate (default 30)

        Returns:
            Summary of aggregation (rows processed, formats found, etc.)
        """
        logger.info(f"Starting format metrics aggregation for tenant {tenant_id}")

        # Initialize GAM reporting service
        reporting_service = GAMReportingService(gam_client)

        # Calculate date range
        end_date = datetime.now()
        start_date = end_date - timedelta(days=period_days)

        # Query GAM with COUNTRY_CODE + CREATIVE_SIZE dimensions
        # Note: We'll use the existing reporting service but need to extend it
        # For now, let's use a direct query approach
        logger.info(f"Querying GAM for date range: {start_date.date()} to {end_date.date()}")

        try:
            # Build custom report query for format metrics
            report_data = self._query_format_metrics(reporting_service, start_date, end_date)

            # Process and store in database
            summary = self._process_and_store_metrics(tenant_id, report_data, start_date, end_date)

            logger.info(f"Completed format metrics aggregation for tenant {tenant_id}: {summary}")
            return summary

        except Exception as e:
            logger.error(
                f"Failed to aggregate format metrics for tenant {tenant_id}: {e}",
                exc_info=True,
            )
            raise

    def _query_format_metrics(
        self, reporting_service: GAMReportingService, start_date: datetime, end_date: datetime
    ) -> list[dict[str, Any]]:
        """
        Query GAM for format metrics using ReportService.

        Returns list of rows with: country_code, creative_size, impressions, revenue, clicks
        """
        # Build the report query with COUNTRY_CODE + CREATIVE_SIZE dimensions
        report_job = {
            "reportQuery": {
                "dimensions": ["COUNTRY_CODE", "CREATIVE_SIZE"],
                "columns": [
                    "AD_SERVER_IMPRESSIONS",
                    "AD_SERVER_CLICKS",
                    "AD_SERVER_CPM_AND_CPC_REVENUE",
                ],
                "dateRangeType": "CUSTOM_DATE",
                "startDate": {
                    "year": start_date.year,
                    "month": start_date.month,
                    "day": start_date.day,
                },
                "endDate": {
                    "year": end_date.year,
                    "month": end_date.month,
                    "day": end_date.day,
                },
            }
        }

        # Run the report
        report_data = reporting_service._run_report(report_job)

        # Process results
        processed_data = []
        for row in report_data:
            # GAM returns data with dimension/column prefixes
            country_code = row.get("Dimension.COUNTRY_CODE")
            creative_size = row.get("Dimension.CREATIVE_SIZE")
            impressions = int(row.get("Column.AD_SERVER_IMPRESSIONS", 0) or 0)
            clicks = int(row.get("Column.AD_SERVER_CLICKS", 0) or 0)
            revenue_micros = float(row.get("Column.AD_SERVER_CPM_AND_CPC_REVENUE", 0) or 0)

            # Skip rows with no impressions
            if impressions == 0:
                continue

            processed_data.append(
                {
                    "country_code": country_code,
                    "creative_size": creative_size,
                    "impressions": impressions,
                    "clicks": clicks,
                    "revenue_micros": revenue_micros,
                }
            )

        logger.info(f"Processed {len(processed_data)} format metric rows from GAM")
        return processed_data

    def _process_and_store_metrics(
        self,
        tenant_id: str,
        report_data: list[dict[str, Any]],
        start_date: datetime,
        end_date: datetime,
    ) -> dict[str, Any]:
        """
        Process report data and store in format_performance_metrics table.

        Calculates CPM percentiles and aggregates by country + format.
        """
        # Group by country + creative_size
        metrics_by_format = {}
        for row in report_data:
            key = (row["country_code"], row["creative_size"])
            if key not in metrics_by_format:
                metrics_by_format[key] = {
                    "impressions": 0,
                    "clicks": 0,
                    "revenue_micros": 0,
                    "line_items": [],
                }

            metrics_by_format[key]["impressions"] += row["impressions"]
            metrics_by_format[key]["clicks"] += row["clicks"]
            metrics_by_format[key]["revenue_micros"] += row["revenue_micros"]
            # Track individual line item CPMs for percentile calculation
            # Revenue is in micros (1/1,000,000 dollar), so convert to dollars first
            if row["impressions"] > 0:
                cpm = (row["revenue_micros"] / 1_000_000 / row["impressions"]) * 1000
                metrics_by_format[key]["line_items"].append(cpm)

        # Store or update metrics in database
        rows_created = 0
        rows_updated = 0

        for (country_code, creative_size), data in metrics_by_format.items():
            # Calculate CPM metrics
            total_impressions = data["impressions"]
            total_revenue_micros = data["revenue_micros"]
            # Revenue is in micros (1/1,000,000 dollar), convert to dollars then to CPM
            average_cpm = (
                (total_revenue_micros / 1_000_000 / total_impressions) * 1000 if total_impressions > 0 else None
            )

            # Calculate percentiles from line item CPMs
            line_item_cpms = sorted(data["line_items"])
            median_cpm = self._calculate_percentile(line_item_cpms, 50)
            p75_cpm = self._calculate_percentile(line_item_cpms, 75)
            p90_cpm = self._calculate_percentile(line_item_cpms, 90)

            # Upsert to database
            stmt = select(FormatPerformanceMetrics).where(
                and_(
                    FormatPerformanceMetrics.tenant_id == tenant_id,
                    FormatPerformanceMetrics.country_code == country_code,
                    FormatPerformanceMetrics.creative_size == creative_size,
                    FormatPerformanceMetrics.period_start == start_date.date(),
                    FormatPerformanceMetrics.period_end == end_date.date(),
                )
            )
            existing = self.db.scalars(stmt).first()

            if existing:
                # Update existing record
                existing.total_impressions = total_impressions
                existing.total_clicks = data["clicks"]
                existing.total_revenue_micros = int(total_revenue_micros)
                existing.average_cpm = average_cpm
                existing.median_cpm = median_cpm
                existing.p75_cpm = p75_cpm
                existing.p90_cpm = p90_cpm
                existing.line_item_count = len(line_item_cpms)
                existing.last_updated = datetime.now()
                rows_updated += 1
            else:
                # Create new record
                metric = FormatPerformanceMetrics(
                    tenant_id=tenant_id,
                    country_code=country_code,
                    creative_size=creative_size,
                    period_start=start_date.date(),
                    period_end=end_date.date(),
                    total_impressions=total_impressions,
                    total_clicks=data["clicks"],
                    total_revenue_micros=int(total_revenue_micros),
                    average_cpm=average_cpm,
                    median_cpm=median_cpm,
                    p75_cpm=p75_cpm,
                    p90_cpm=p90_cpm,
                    line_item_count=len(line_item_cpms),
                )
                self.db.add(metric)
                rows_created += 1

        self.db.commit()

        return {
            "rows_created": rows_created,
            "rows_updated": rows_updated,
            "formats_processed": len(metrics_by_format),
            "total_impressions": sum(m["impressions"] for m in metrics_by_format.values()),
        }

    def _calculate_percentile(self, sorted_values: list[float], percentile: int) -> float | None:
        """Calculate percentile from sorted list of values."""
        if not sorted_values:
            return None

        if len(sorted_values) == 1:
            return sorted_values[0]

        # Use linear interpolation
        index = (percentile / 100) * (len(sorted_values) - 1)
        lower_index = int(index)
        upper_index = min(lower_index + 1, len(sorted_values) - 1)
        weight = index - lower_index

        return sorted_values[lower_index] * (1 - weight) + sorted_values[upper_index] * weight


def aggregate_all_tenants(period_days: int = 30) -> dict[str, Any]:
    """
    Aggregate format metrics for all active tenants with GAM configured.

    Args:
        period_days: Number of days to aggregate

    Returns:
        Summary of aggregation across all tenants
    """
    from src.adapters.gam.auth import GAMAuthManager
    from src.adapters.gam.client import GAMClientManager
    from src.core.database.models import AdapterConfig

    logger.info("Starting format metrics aggregation for all tenants")

    with get_db_session() as db_session:
        # Get all active tenants with GAM configured
        stmt = (
            select(Tenant, AdapterConfig)
            .join(AdapterConfig, Tenant.tenant_id == AdapterConfig.tenant_id)
            .where(
                Tenant.ad_server == "google_ad_manager",
                Tenant.is_active,
                AdapterConfig.gam_network_code.isnot(None),
                AdapterConfig.gam_refresh_token.isnot(None),
            )
        )
        tenants = db_session.execute(stmt).all()

        summary = {"total_tenants": len(tenants), "successful": 0, "failed": 0, "details": []}

        for tenant, adapter_config in tenants:
            tenant_id = tenant.tenant_id
            logger.info(f"Processing tenant: {tenant.name} ({tenant_id})")

            try:
                # Initialize GAM client
                auth_manager = GAMAuthManager(
                    network_code=adapter_config.gam_network_code,
                    refresh_token=adapter_config.gam_refresh_token,
                )
                client_manager = GAMClientManager(auth_manager)
                gam_client = client_manager.get_client()

                # Aggregate metrics
                service = FormatMetricsAggregationService(db_session)
                tenant_summary = service.aggregate_metrics_for_tenant(tenant_id, gam_client, period_days)

                summary["successful"] += 1
                summary["details"].append(
                    {
                        "tenant_id": tenant_id,
                        "tenant_name": tenant.name,
                        "status": "success",
                        "summary": tenant_summary,
                    }
                )

            except Exception as e:
                logger.error(
                    f"Failed to aggregate metrics for tenant {tenant_id}: {e}",
                    exc_info=True,
                )
                summary["failed"] += 1
                summary["details"].append(
                    {
                        "tenant_id": tenant_id,
                        "tenant_name": tenant.name,
                        "status": "failed",
                        "error": str(e),
                    }
                )

        logger.info(
            f"Completed format metrics aggregation: {summary['successful']} successful, "
            f"{summary['failed']} failed out of {summary['total_tenants']} tenants"
        )
        return summary
