"""
Google Ad Manager Reporting Service

Provides comprehensive reporting data from GAM including:
- Spend and impression numbers by advertiser, order, and line item
- Three date range options: lifetime by day, this month by day, today by hour
- Timezone handling and data freshness timestamps
"""

import csv
import gzip
import io
import json
import logging
import math
import time
from calendar import monthrange
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import pytz
import requests

from src.core.signal_ids import adcp_safe_signal_id
from src.core.statistics import percentile

logger = logging.getLogger(__name__)


def _parse_report_float(value: Any) -> float:
    """Parse GAM CSV numeric values, accepting blanks and comma separators."""
    if value is None:
        return 0.0
    if isinstance(value, int | float):
        return float(value)
    text = str(value).replace(",", "").strip()
    if not text:
        return 0.0
    return float(text)


def _parse_report_int(value: Any) -> int:
    return int(_parse_report_float(value))


def _cpm_from_micros(revenue_micros: float, impressions: int) -> float:
    if impressions <= 0:
        return 0.0
    return revenue_micros / 1_000_000 / impressions * 1000


def _weighted_percentile(cpm_weights: list[tuple[float, int]], percentile: int) -> float | None:
    weighted = sorted((cpm, weight) for cpm, weight in cpm_weights if weight > 0)
    total_weight = sum(weight for _, weight in weighted)
    if total_weight <= 0:
        return None

    threshold = total_weight * (percentile / 100)
    cumulative = 0
    for cpm, weight in weighted:
        cumulative += weight
        if cumulative >= threshold:
            return cpm
    return weighted[-1][0]


def _rounded_guidance(guidance: dict[str, float | None]) -> dict[str, float | None]:
    return {key: (round(value, 2) if value is not None else None) for key, value in guidance.items()}


def _price_guidance_dimensions(*, include_placements: bool, include_ad_units: bool) -> list[str]:
    dimensions: list[str] = []
    if include_placements:
        dimensions.extend(["PLACEMENT_ID", "PLACEMENT_NAME"])
    if include_ad_units:
        dimensions.extend(["AD_UNIT_ID", "AD_UNIT_NAME"])
    dimensions.extend(["COUNTRY_CODE", "COUNTRY_NAME", "LINE_ITEM_ID", "LINE_ITEM_NAME", "LINE_ITEM_TYPE"])
    return dimensions


def _price_guidance_inventory_target(row: dict[str, Any]) -> dict[str, str]:
    placement_id = str(row.get("placement_id") or "").strip()
    if placement_id:
        return {
            "kind": "placement",
            "id": placement_id,
            "name": str(row.get("placement_name") or placement_id),
        }
    ad_unit_id = str(row.get("ad_unit_id") or "").strip()
    return {
        "kind": "ad_unit",
        "id": ad_unit_id,
        "name": str(row.get("ad_unit_name") or ad_unit_id),
    }


def _total_result_set_size(page: Any) -> int:
    if page is None:
        return 0
    if isinstance(page, dict):
        return int(page.get("totalResultSetSize") or 0)
    return int(getattr(page, "totalResultSetSize", 0) or 0)


def _object_field(obj: Any, field: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(field)
    return getattr(obj, field, None)


class ReportingConfig:
    """Configuration constants for GAM reporting operations."""

    # Security settings
    ALLOWED_DOMAINS = [".google.com", ".googleapis.com"]

    # Memory management
    MAX_ROWS_PER_REPORT = 100000  # Prevent OOM from large reports
    MAX_CSV_SIZE_BYTES = 10 * 1024 * 1024  # 10MB limit for CSV data

    # Network and timing
    REPORT_TIMEOUT_SECONDS = 600  # 10 minutes maximum for report completion
    POLL_INTERVAL_SECONDS = 5  # Check report status every 5 seconds
    HTTP_CONNECT_TIMEOUT = 30  # 30 seconds for connection establishment
    HTTP_READ_TIMEOUT = 300  # 5 minutes for data transfer

    # User agent for HTTP requests
    USER_AGENT = "AdCP-Sales-Agent/1.0"


@dataclass
class ReportingData:
    """Container for reporting data with metadata"""

    data: list[dict[str, Any]]
    start_date: datetime
    end_date: datetime
    requested_timezone: str
    data_timezone: str
    data_valid_until: datetime
    query_type: str
    dimensions: list[str]
    metrics: dict[str, Any]


class GAMReportingService:
    """Service for getting comprehensive reporting data from Google Ad Manager"""

    def __init__(self, gam_client, network_timezone: str = None):
        """
        Initialize the reporting service

        Args:
            gam_client: Initialized Google Ad Manager client
            network_timezone: The timezone of the GAM network (will be auto-detected if not provided)
        """
        self.client = gam_client
        self.report_service = self.client.GetService("ReportService")

        # Get network timezone from GAM if not provided
        if network_timezone:
            self.network_timezone = network_timezone
        else:
            try:
                network_service = self.client.GetService("NetworkService")
                network = network_service.getCurrentNetwork()
                self.network_timezone = network.timeZone
            except Exception:
                # Fallback to Eastern Time if we can't get network timezone
                self.network_timezone = "America/New_York"

    def get_reporting_data(
        self,
        date_range: Literal["lifetime", "this_month", "today"],
        advertiser_id: str | None = None,
        order_id: str | None = None,
        line_item_id: str | None = None,
        requested_timezone: str = "America/New_York",
        include_country: bool = False,
        include_ad_unit: bool = False,
    ) -> ReportingData:
        """
        Get reporting data for specified date range and filters

        Args:
            date_range: One of "lifetime", "this_month", or "today"
            advertiser_id: Optional advertiser/company ID filter
            order_id: Optional order ID filter
            line_item_id: Optional line item ID filter
            requested_timezone: Timezone for the request (data will be converted if different)
            include_country: Include country dimension in the report
            include_ad_unit: Include ad unit dimension in the report

        Returns:
            ReportingData object containing results and metadata
        """
        # Determine the appropriate dimensions and date range
        dimensions, start_date, end_date, granularity = self._get_report_config(
            date_range, requested_timezone, include_country, include_ad_unit
        )

        # Build the report query
        report_job = self._build_report_query(dimensions, start_date, end_date, advertiser_id, order_id, line_item_id)

        # Run the report
        report_data = self._run_report(report_job)

        # Calculate data freshness
        data_valid_until = self._calculate_data_validity(date_range, requested_timezone)

        # Process and aggregate the data
        processed_data = self._process_report_data(report_data, granularity, requested_timezone)

        # Calculate summary metrics
        metrics = self._calculate_metrics(processed_data)

        return ReportingData(
            data=processed_data,
            start_date=start_date,
            end_date=end_date,
            requested_timezone=requested_timezone,
            data_timezone=self.network_timezone if self.network_timezone != requested_timezone else requested_timezone,
            data_valid_until=data_valid_until,
            query_type=date_range,
            dimensions=dimensions,
            metrics=metrics,
        )

    def _get_report_config(
        self,
        date_range: str,
        requested_tz: str,
        include_country: bool = False,
        include_ad_unit: bool = False,
        include_date: bool = True,
    ) -> tuple:
        """Get the appropriate dimensions and date range for the report type

        Args:
            date_range: Time period for the report
            requested_tz: Timezone for the report
            include_country: Whether to include country dimension
            include_ad_unit: Whether to include ad unit dimensions
            include_date: Whether to include DATE dimension (False for aggregated queries)
        """
        tz = pytz.timezone(requested_tz)
        now = datetime.now(tz)

        # Base dimensions for all reports
        # For aggregated reports (no DATE), we can include names
        # For time-series reports (with DATE), we only include IDs to reduce data volume
        if not include_date:
            # Aggregated query - include names for readability
            base_dimensions = [
                "ADVERTISER_ID",
                "ADVERTISER_NAME",
                "ORDER_ID",
                "ORDER_NAME",
                "LINE_ITEM_ID",
                "LINE_ITEM_NAME",
            ]
        else:
            # Time-series query - only IDs to minimize data
            base_dimensions = ["ADVERTISER_ID", "ORDER_ID", "LINE_ITEM_ID"]

        # Add optional dimensions
        if include_country:
            base_dimensions.append("COUNTRY_NAME")
        if include_ad_unit:
            base_dimensions.extend(["AD_UNIT_ID", "AD_UNIT_NAME"])

        # For aggregated queries (e.g., country/ad unit breakdowns), skip DATE dimension
        # This reduces data from millions of rows to thousands
        if not include_date:
            dimensions = base_dimensions
            # Still set date range for filtering, but no DATE in dimensions means GAM aggregates for us
            if date_range == "today":
                start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
                end_date = now
                granularity = "total"
            elif date_range == "this_month":
                start_date = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                end_date = now
                granularity = "total"
            else:  # lifetime
                # For aggregated queries, we can use longer date ranges since we get one row per entity
                start_date = (now - timedelta(days=90)).replace(hour=0, minute=0, second=0, microsecond=0)
                end_date = now
                granularity = "total"
        # Include DATE dimension for time-series data
        elif date_range == "today":
            # Today by hour - need both DATE and HOUR dimensions for hourly reporting
            dimensions = ["DATE", "HOUR"] + base_dimensions
            start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
            end_date = now
            granularity = "hourly"
        elif date_range == "this_month":
            # This month by day
            dimensions = ["DATE"] + base_dimensions
            start_date = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            end_date = now
            granularity = "daily"
        else:  # lifetime
            # Lifetime by day - limit based on whether we're getting detailed dimensions
            dimensions = ["DATE"] + base_dimensions
            # Reduce to 30 days if we have ad unit or country dimensions to avoid timeouts
            if include_country or include_ad_unit:
                start_date = (now - timedelta(days=30)).replace(hour=0, minute=0, second=0, microsecond=0)
            else:
                start_date = (now - timedelta(days=90)).replace(hour=0, minute=0, second=0, microsecond=0)
            end_date = now
            granularity = "daily"

        return dimensions, start_date, end_date, granularity

    def _build_report_query(
        self,
        dimensions: list[str],
        start_date: datetime,
        end_date: datetime,
        advertiser_id: str | None = None,
        order_id: str | None = None,
        line_item_id: str | None = None,
    ) -> dict[str, Any]:
        """Build the GAM report query"""

        # Build the WHERE clause and bind variables for ReportQuery
        # Note: We don't use StatementBuilder here because it adds LIMIT which is not supported in ReportService
        where_clauses = []
        bind_variables = []

        if advertiser_id:
            # Validate numeric ID
            try:
                advertiser_id_int = int(advertiser_id)
                where_clauses.append("ADVERTISER_ID = :advertiserId")
                bind_variables.append(
                    {"key": "advertiserId", "value": {"value": str(advertiser_id_int), "xsi_type": "NumberValue"}}
                )
            except (ValueError, TypeError):
                logger.warning(f"Invalid advertiser_id format: {advertiser_id}")

        if order_id:
            # Validate numeric ID
            try:
                order_id_int = int(order_id)
                where_clauses.append("ORDER_ID = :orderId")
                bind_variables.append(
                    {"key": "orderId", "value": {"value": str(order_id_int), "xsi_type": "NumberValue"}}
                )
            except (ValueError, TypeError):
                logger.warning(f"Invalid order_id format: {order_id}")

        if line_item_id:
            # Validate numeric ID
            try:
                line_item_id_int = int(line_item_id)
                where_clauses.append("LINE_ITEM_ID = :lineItemId")
                bind_variables.append(
                    {"key": "lineItemId", "value": {"value": str(line_item_id_int), "xsi_type": "NumberValue"}}
                )
            except (ValueError, TypeError):
                logger.warning(f"Invalid line_item_id format: {line_item_id}")

        # Add minimum impressions filter for aggregated queries to reduce noise
        # NOTE: AD_SERVER_IMPRESSIONS is not filterable in WHERE clause, but we can
        # filter during processing. For aggregated queries, this happens server-side.

        report_job = {
            "reportQuery": {
                "dimensions": dimensions,
                "columns": [
                    "AD_SERVER_IMPRESSIONS",
                    "AD_SERVER_CLICKS",
                    "AD_SERVER_CPM_AND_CPC_REVENUE",  # Revenue/spend - this is always available
                    # Video VAST events. Returns real values for in-stream
                    # VIDEO_PLAYER line items; returns zero for outstream
                    # since VAST events don't fire there. The full classifier-
                    # plus-merge pattern that handles outstream correctly is
                    # tracked in #225's Phase 2 (needs a new gam_line_items
                    # repository). This phase closes the in-stream gap, the
                    # most common case.
                    "AD_SERVER_VIDEO_COMPLETIONS",
                ],
                "dateRangeType": "CUSTOM_DATE",
                "startDate": {"year": start_date.year, "month": start_date.month, "day": start_date.day},
                "endDate": {"year": end_date.year, "month": end_date.month, "day": end_date.day},
                "timeZoneType": "PUBLISHER",  # Use publisher's timezone
                "statement": (
                    {
                        "query": "WHERE " + " AND ".join(where_clauses),
                        "values": bind_variables if bind_variables else None,
                    }
                    if where_clauses
                    else None
                ),
            }
        }

        return report_job

    def _run_report(self, report_job: dict[str, Any]) -> list[dict[str, Any]]:
        """Run the report and return the data"""
        try:
            # Start the report job - returns a ReportJob object with an 'id' field
            report_job_response = self.report_service.runReportJob(report_job)

            # Extract the report job ID from the response
            if hasattr(report_job_response, "id"):
                report_job_id = report_job_response.id
            elif isinstance(report_job_response, dict) and "id" in report_job_response:
                report_job_id = report_job_response["id"]
            else:
                # If it's already just the ID
                report_job_id = report_job_response

            logger.info(f"Started GAM report job with ID: {report_job_id}")

            # Wait for completion - longer timeout for reports with multiple dimensions
            max_wait = ReportingConfig.REPORT_TIMEOUT_SECONDS
            wait_time = 0
            poll_interval = ReportingConfig.POLL_INTERVAL_SECONDS

            while wait_time < max_wait:
                status = self.report_service.getReportJobStatus(report_job_id)
                if status == "COMPLETED":
                    break
                elif status == "FAILED":
                    raise Exception("GAM report job failed")

                # Log progress for long-running reports
                if wait_time > 0 and wait_time % 30 == 0:
                    logger.info(f"Still waiting for GAM report {report_job_id} - {wait_time}s elapsed")

                time.sleep(poll_interval)
                wait_time += poll_interval

            if self.report_service.getReportJobStatus(report_job_id) != "COMPLETED":
                raise Exception(f"GAM report job timed out after {max_wait} seconds")

            # Use modern ReportService method instead of deprecated GetDataDownloader
            try:
                download_url = self.report_service.getReportDownloadURL(report_job_id, "CSV_DUMP")
            except Exception as e:
                raise Exception(f"Failed to get GAM report download URL: {str(e)}") from e

            # Validate URL is from Google for security
            parsed_url = urlparse(download_url)
            if not parsed_url.hostname or not any(
                parsed_url.hostname.endswith(domain) for domain in ReportingConfig.ALLOWED_DOMAINS
            ):
                raise Exception(f"Invalid download URL: not from Google domain ({parsed_url.hostname})")

            # Download the report using requests with proper timeout and error handling
            try:
                response = requests.get(
                    download_url,
                    timeout=(ReportingConfig.HTTP_CONNECT_TIMEOUT, ReportingConfig.HTTP_READ_TIMEOUT),
                    headers={"User-Agent": ReportingConfig.USER_AGENT},
                    stream=True,  # For better memory handling of large files
                )
                response.raise_for_status()
            except requests.exceptions.Timeout as e:
                raise Exception(f"GAM report download timed out: {str(e)}") from e
            except requests.exceptions.RequestException as e:
                raise Exception(f"Failed to download GAM report: {str(e)}") from e

            # Parse the CSV data directly from the response with memory limits
            try:
                data = []

                with gzip.open(io.BytesIO(response.content), "rt") as gz_file:
                    csv_reader = csv.DictReader(gz_file)
                    for i, row in enumerate(csv_reader):
                        if i >= ReportingConfig.MAX_ROWS_PER_REPORT:
                            logger.warning(
                                f"GAM report truncated at {ReportingConfig.MAX_ROWS_PER_REPORT} rows to prevent memory issues"
                            )
                            break
                        data.append(row)
            except Exception as e:
                raise Exception(f"Failed to parse GAM report CSV data: {str(e)}") from e

            # Debug: Log the first row to see column names
            if data:
                logger.info(f"CSV columns: {list(data[0].keys())}")
                logger.info(f"First row sample: {data[0]}")
                logger.info(f"Total rows in report: {len(data)}")
            else:
                logger.warning("GAM report returned no data rows")

            return data

        except Exception as e:
            raise Exception(f"Error running GAM report: {str(e)}") from e

    def _process_report_data(
        self, raw_data: list[dict[str, Any]], granularity: str, requested_tz: str
    ) -> list[dict[str, Any]]:
        """Process and aggregate the raw report data"""

        # Map possible CSV column names to our field names
        # GAM CSV might use different names than the API constants
        column_mappings = {
            # Dimensions - including both IDs and names
            "Dimension.ADVERTISER_ID": "ADVERTISER_ID",
            "Dimension.ADVERTISER_NAME": "ADVERTISER_NAME",
            "Dimension.ORDER_ID": "ORDER_ID",
            "Dimension.ORDER_NAME": "ORDER_NAME",
            "Dimension.LINE_ITEM_ID": "LINE_ITEM_ID",
            "Dimension.LINE_ITEM_NAME": "LINE_ITEM_NAME",
            "Dimension.DATE": "DATE",
            "Dimension.HOUR": "HOUR",
            "Dimension.COUNTRY_NAME": "COUNTRY_NAME",
            "Dimension.COUNTRY_CODE": "COUNTRY_CODE",
            "Dimension.AD_UNIT_ID": "AD_UNIT_ID",
            "Dimension.AD_UNIT_NAME": "AD_UNIT_NAME",
            "Dimension.PLACEMENT_ID": "PLACEMENT_ID",
            "Dimension.PLACEMENT_NAME": "PLACEMENT_NAME",
            "Dimension.LINE_ITEM_TYPE": "LINE_ITEM_TYPE",
            # Metrics - only including the ones we're actually requesting
            "Column.AD_SERVER_IMPRESSIONS": "AD_SERVER_IMPRESSIONS",
            "Column.AD_SERVER_CLICKS": "AD_SERVER_CLICKS",
            "Column.AD_SERVER_CPM_AND_CPC_REVENUE": "AD_SERVER_CPM_AND_CPC_REVENUE",
            "Column.AD_SERVER_VIDEO_COMPLETIONS": "AD_SERVER_VIDEO_COMPLETIONS",
            "Column.AD_SERVER_ACTIVE_VIEW_VIEWABLE_IMPRESSIONS": "AD_SERVER_ACTIVE_VIEW_VIEWABLE_IMPRESSIONS",
            "Column.AD_SERVER_ACTIVE_VIEW_MEASURABLE_IMPRESSIONS": "AD_SERVER_ACTIVE_VIEW_MEASURABLE_IMPRESSIONS",
        }

        # Dictionary to store aggregated data
        # Key will be a tuple of dimension values
        aggregated_data = {}

        for row in raw_data:
            # Normalize column names
            normalized_row = {}
            for key, value in row.items():
                # Check if it's a GAM CSV column name
                if key in column_mappings:
                    normalized_row[column_mappings[key]] = value
                else:
                    # Use as-is
                    normalized_row[key] = value

            # Skip rows where ALL metrics are zero to reduce data volume.
            # Do NOT skip zero-impression rows that have clicks or revenue —
            # FLAT_RATE/SPONSORSHIP line items accrue spend without impressions.
            # Video completions also count as a non-zero signal (in-stream
            # VAST inventory may have completions on rows where the
            # impression count is non-zero anyway, but be defensive).
            impressions = int(normalized_row.get("AD_SERVER_IMPRESSIONS", 0) or 0)
            clicks = int(normalized_row.get("AD_SERVER_CLICKS", 0) or 0)
            revenue = float(normalized_row.get("AD_SERVER_CPM_AND_CPC_REVENUE", 0) or 0)
            video_completions = int(normalized_row.get("AD_SERVER_VIDEO_COMPLETIONS", 0) or 0)
            if impressions == 0 and clicks == 0 and revenue == 0 and video_completions == 0:
                continue

            # Build aggregation key from dimensions
            # Include timestamp for time-based aggregation
            timestamp = self._parse_timestamp(normalized_row, granularity)

            agg_key = (
                timestamp,
                normalized_row.get("ADVERTISER_ID", ""),
                normalized_row.get("ORDER_ID", ""),
                normalized_row.get("LINE_ITEM_ID", ""),
                normalized_row.get("COUNTRY_NAME", ""),
                normalized_row.get("AD_UNIT_ID", ""),
            )

            # Initialize or update aggregated metrics
            if agg_key not in aggregated_data:
                aggregated_data[agg_key] = {
                    "timestamp": timestamp,
                    "advertiser_id": normalized_row.get("ADVERTISER_ID", ""),
                    "advertiser_name": normalized_row.get("ADVERTISER_NAME", ""),
                    "order_id": normalized_row.get("ORDER_ID", ""),
                    "order_name": normalized_row.get("ORDER_NAME", ""),
                    "line_item_id": normalized_row.get("LINE_ITEM_ID", ""),
                    "line_item_name": normalized_row.get("LINE_ITEM_NAME", ""),
                    "country": normalized_row.get("COUNTRY_NAME", ""),
                    "ad_unit_id": normalized_row.get("AD_UNIT_ID", ""),
                    "ad_unit_name": normalized_row.get("AD_UNIT_NAME", ""),
                    "impressions": 0,
                    "clicks": 0,
                    "revenue_micros": 0,  # Keep in micros for accurate summing
                    "video_completions": 0,
                    "row_count": 0,  # Track number of rows aggregated
                }

            # Aggregate metrics
            agg = aggregated_data[agg_key]
            agg["impressions"] += int(normalized_row.get("AD_SERVER_IMPRESSIONS", 0) or 0)
            agg["clicks"] += int(normalized_row.get("AD_SERVER_CLICKS", 0) or 0)
            agg["revenue_micros"] += float(normalized_row.get("AD_SERVER_CPM_AND_CPC_REVENUE", 0) or 0)
            agg["video_completions"] += int(normalized_row.get("AD_SERVER_VIDEO_COMPLETIONS", 0) or 0)
            agg["row_count"] += 1

        # Convert aggregated data to list and calculate derived metrics
        processed = []
        for agg_data in aggregated_data.values():
            # Convert revenue from micros to dollars
            spend = agg_data["revenue_micros"] / 1_000_000

            # Calculate derived metrics
            impressions = agg_data["impressions"]
            clicks = agg_data["clicks"]

            # Calculate CTR (clicks/impressions as percentage)
            ctr = (clicks / impressions * 100) if impressions > 0 else 0.0

            # Calculate CPM (cost per thousand impressions)
            cpm = (spend / impressions * 1000) if impressions > 0 else 0.0

            processed_row = {
                "timestamp": agg_data["timestamp"],
                "advertiser_id": agg_data["advertiser_id"],
                "advertiser_name": agg_data.get("advertiser_name", ""),
                "order_id": agg_data["order_id"],
                "order_name": agg_data.get("order_name", ""),
                "line_item_id": agg_data["line_item_id"],
                "line_item_name": agg_data.get("line_item_name", ""),
                "country": agg_data.get("country", ""),
                "ad_unit_id": agg_data.get("ad_unit_id", ""),
                "ad_unit_name": agg_data.get("ad_unit_name", ""),
                "impressions": impressions,
                "clicks": clicks,
                "ctr": round(ctr, 4),
                "spend": round(spend, 2),
                "cpm": round(cpm, 2),  # Changed from ecpm to cpm for clarity
                "video_completions": agg_data.get("video_completions", 0),
                "aggregated_rows": agg_data["row_count"],  # Useful for debugging
            }

            processed.append(processed_row)

        # Sort by timestamp and then by spend (descending)
        processed.sort(key=lambda x: (x["timestamp"], -x["spend"]))

        # Log aggregation results
        logger.info(f"Aggregated {len(raw_data)} raw rows into {len(processed)} aggregated rows")

        return processed

    def get_placement_country_price_guidance(
        self,
        date_range: Literal["lifetime", "this_month", "today"],
        *,
        placement_ids: list[str] | None = None,
        ad_unit_ids: list[str] | None = None,
        countries: list[str] | None = None,
        line_item_types: list[str] | None = None,
        min_group_impressions: int = 10_000,
        min_line_item_impressions: int = 1_000,
        min_package_budget: float | None = None,
        bookability_safety_factor: float = 1.0,
        include_zero_revenue: bool = False,
        include_eligible_line_items: bool = False,
        currency: str = "USD",
        publisher_domain: str | None = None,
        viewability_standard: str = "mrc",
        requested_timezone: str = "America/New_York",
    ) -> dict[str, Any]:
        """Calculate placement-country price guidance from line-item delivery.

        GAM does not provide CPM quartiles directly. This report asks GAM for
        placement x country x line-item rows, then computes p25/p50/p75/p90
        locally from each line item's realized CPM. The primary guidance is
        impression-weighted so tiny high-CPM line items do not dominate.
        """
        dimensions, start_date, end_date, granularity = self._get_report_config(
            date_range=date_range,
            requested_tz=requested_timezone,
            include_date=False,
        )
        include_placements = bool(placement_ids) or not ad_unit_ids
        dimensions = _price_guidance_dimensions(
            include_placements=include_placements, include_ad_units=bool(ad_unit_ids)
        )

        effective_line_item_types = ["PRICE_PRIORITY"] if line_item_types is None else line_item_types

        report_query = self._build_price_guidance_report_query(
            dimensions=dimensions,
            start_date=start_date,
            end_date=end_date,
            placement_ids=placement_ids,
            ad_unit_ids=ad_unit_ids,
            countries=countries,
            line_item_types=effective_line_item_types,
        )
        raw_rows = self._run_report(report_query)
        line_item_rows = self._price_guidance_line_item_rows(
            raw_rows,
            line_item_types=effective_line_item_types,
            min_line_item_impressions=min_line_item_impressions,
            include_zero_revenue=include_zero_revenue,
        )

        groups = self._aggregate_price_guidance_groups(
            line_item_rows,
            min_group_impressions=min_group_impressions,
            min_package_budget=min_package_budget,
            bookability_safety_factor=bookability_safety_factor,
            currency=currency,
            publisher_domain=publisher_domain,
            viewability_standard=viewability_standard,
        )

        result = {
            "date_range": date_range,
            "window_start": start_date.date().isoformat(),
            "window_end": end_date.date().isoformat(),
            "timezone": requested_timezone,
            "data_valid_until": self._calculate_data_validity(date_range, requested_timezone).isoformat(),
            "dimensions": dimensions,
            "columns": [
                "AD_SERVER_IMPRESSIONS",
                "AD_SERVER_CLICKS",
                "AD_SERVER_CPM_AND_CPC_REVENUE",
                "AD_SERVER_VIDEO_COMPLETIONS",
                "AD_SERVER_ACTIVE_VIEW_VIEWABLE_IMPRESSIONS",
                "AD_SERVER_ACTIVE_VIEW_MEASURABLE_IMPRESSIONS",
            ],
            "thresholds": {
                "min_group_impressions": min_group_impressions,
                "min_line_item_impressions": min_line_item_impressions,
                "min_package_budget": min_package_budget,
                "bookability_safety_factor": bookability_safety_factor,
                "include_zero_revenue": include_zero_revenue,
            },
            "filters": {
                "placement_ids": placement_ids or [],
                "ad_unit_ids": ad_unit_ids or [],
                "countries": countries or [],
                "line_item_types": effective_line_item_types,
            },
            "raw_rows": len(raw_rows),
            "possibly_truncated": len(raw_rows) >= ReportingConfig.MAX_ROWS_PER_REPORT,
            "eligible_line_item_rows": len(line_item_rows),
            "groups": groups,
            "group_count": len(groups),
            "bookable_group_count": sum(1 for group in groups if group["bookable"]),
            "forecast": self._forecast_from_groups(groups, currency=currency),
            "granularity": granularity,
        }
        if include_eligible_line_items:
            result["line_item_rows"] = line_item_rows
        return result

    def get_line_item_capacity_guidance(
        self,
        date_range: Literal["this_month", "today"] = "this_month",
        *,
        max_network_line_items: int,
        monthly_line_item_space_fraction: float = 0.01,
        estimated_line_items_per_package: int = 1,
        requested_timezone: str = "America/New_York",
    ) -> dict[str, Any]:
        """Estimate minimum package size from GAM revenue and line-item capacity.

        GAM documents line-item limit errors, but does not expose the network's
        hard line-item cap as a reporting metric. Callers provide the assumed
        cap, and this method computes the minimum package budget that would keep
        Sales Agent-originated monthly line-item creation within the configured
        fraction of that cap.
        """
        if max_network_line_items <= 0:
            raise ValueError("max_network_line_items must be positive")
        if not 0 < monthly_line_item_space_fraction <= 1:
            raise ValueError("monthly_line_item_space_fraction must be greater than 0 and at most 1")
        if estimated_line_items_per_package <= 0:
            raise ValueError("estimated_line_items_per_package must be positive")

        _dimensions, start_date, end_date, granularity = self._get_report_config(
            date_range=date_range,
            requested_tz=requested_timezone,
            include_date=False,
        )
        report_query = self._build_network_revenue_report_query(start_date=start_date, end_date=end_date)
        raw_rows = self._run_report(report_query)
        monthly_revenue_to_date = self._sum_report_revenue(raw_rows)
        projected_monthly_revenue = self._project_monthly_revenue(
            monthly_revenue_to_date,
            start_date=start_date,
            end_date=end_date,
            requested_timezone=requested_timezone,
        )

        monthly_line_item_budget = max(1, math.floor(max_network_line_items * monthly_line_item_space_fraction))
        total_line_items = self._line_item_count()
        line_items_created_in_window = self._line_item_count(start_date=start_date, end_date=end_date)
        remaining_total_line_item_capacity = max(0, max_network_line_items - total_line_items)
        effective_monthly_line_item_budget = min(monthly_line_item_budget, remaining_total_line_item_capacity)
        minimum_package_budget = (
            projected_monthly_revenue / effective_monthly_line_item_budget * estimated_line_items_per_package
            if effective_monthly_line_item_budget > 0
            else None
        )

        return {
            "date_range": date_range,
            "window_start": start_date.date().isoformat(),
            "window_end": end_date.date().isoformat(),
            "timezone": requested_timezone,
            "granularity": granularity,
            "network_revenue": {
                "to_date": round(monthly_revenue_to_date, 2),
                "projected_monthly": round(projected_monthly_revenue, 2),
            },
            "line_item_capacity": {
                "max_network_line_items": max_network_line_items,
                "monthly_space_fraction": monthly_line_item_space_fraction,
                "monthly_line_item_budget": monthly_line_item_budget,
                "effective_monthly_line_item_budget": effective_monthly_line_item_budget,
                "estimated_line_items_per_package": estimated_line_items_per_package,
                "total_line_items": total_line_items,
                "total_line_item_utilization_pct": round(total_line_items / max_network_line_items * 100, 2),
                "remaining_total_line_item_capacity": remaining_total_line_item_capacity,
                "created_in_window": line_items_created_in_window,
                "created_in_window_pct_of_monthly_budget": round(
                    line_items_created_in_window / monthly_line_item_budget * 100, 2
                ),
                "remaining_monthly_line_item_budget": max(
                    0, effective_monthly_line_item_budget - line_items_created_in_window
                ),
            },
            "minimum_package_budget": round(minimum_package_budget, 2) if minimum_package_budget is not None else None,
            "notes": [
                "GAM exposes errors when line-item limits are reached, but this calculation requires the network cap as an input.",
                "The minimum package budget is based on projected monthly network revenue divided by the allowed monthly line-item budget.",
            ],
        }

    def get_custom_targeting_value_coverage(
        self,
        date_range: Literal["lifetime", "this_month", "today"],
        *,
        key_name: str | None = None,
        key_id: str | int | None = None,
        value_names: list[str] | None = None,
        line_item_types: list[str] | None = None,
        min_value_impressions: int = 1,
        requested_timezone: str = "America/New_York",
    ) -> dict[str, Any]:
        """Measure delivered inventory share for one GAM custom targeting key.

        This is historical coverage: it answers "what share of matching GAM
        delivery carried each key-value?" It is not a forecast that applies new
        targeting to unsold future capacity.
        """
        if not key_name and key_id is None:
            raise ValueError("Either key_name or key_id is required")

        start_date, end_date, granularity = self._signal_coverage_window(date_range, requested_timezone)
        effective_line_item_types = ["PRICE_PRIORITY"] if line_item_types is None else line_item_types
        key = self._custom_targeting_key(key_name=key_name, key_id=key_id)
        values = self._custom_targeting_values(key_id=int(key["id"]), value_names=value_names)
        value_ids = [str(value["id"]) for value in values]

        total_impressions, total_revenue = self._signal_coverage_total_inventory(
            start_date=start_date,
            end_date=end_date,
            line_item_types=effective_line_item_types,
        )
        aggregated_values = self._signal_coverage_values_for_ids(
            value_ids=value_ids,
            values_by_id={str(value["id"]): value for value in values},
            start_date=start_date,
            end_date=end_date,
            line_item_types=effective_line_item_types,
        )
        filtered_values = [value for value in aggregated_values if value["impressions"] >= min_value_impressions]
        present_impressions = sum(value["impressions"] for value in filtered_values)
        not_present_impressions = max(0, total_impressions - present_impressions)
        denominator = total_impressions or 1
        for value in filtered_values:
            value["share_of_inventory"] = round(value["impressions"] / denominator, 6)

        multi_value_overlap = present_impressions > total_impressions
        not_present_bucket = {
            "label": "not present",
            "impressions": not_present_impressions,
            "share_of_inventory": round(not_present_impressions / denominator, 6),
        }
        return {
            "date_range": date_range,
            "window_start": start_date.date().isoformat(),
            "window_end": end_date.date().isoformat(),
            "timezone": requested_timezone,
            "data_valid_until": self._calculate_data_validity(date_range, requested_timezone).isoformat(),
            "granularity": granularity,
            "key": key,
            "filters": {
                "line_item_types": effective_line_item_types,
                "value_names": value_names or [],
                "min_value_impressions": min_value_impressions,
            },
            "total_inventory": {
                "impressions": total_impressions,
                "revenue": round(total_revenue, 2),
                "average_cpm": round(total_revenue / total_impressions * 1000, 2) if total_impressions > 0 else None,
            },
            "values": filtered_values,
            "not_present": not_present_bucket,
            "coverage": {
                "present_impressions": present_impressions,
                "present_share_of_inventory": round(min(present_impressions, total_impressions) / denominator, 6),
                "value_count": len(filtered_values),
                "registered_value_count": len(values),
                "multi_value_overlap": multi_value_overlap,
            },
            "coverage_forecast": self._signal_coverage_forecast(
                key=key,
                values=filtered_values,
                not_present=not_present_bucket,
                total_impressions=total_impressions,
                total_revenue=total_revenue,
                line_item_types=effective_line_item_types,
                window_start=start_date.date().isoformat(),
                window_end=end_date.date().isoformat(),
                multi_value_overlap=multi_value_overlap,
            ),
            "notes": [
                "Coverage is based on delivered historical impressions for the selected line item types.",
                "The not_present bucket is total matching delivery minus reported impressions for the selected values.",
                "If a request can carry multiple values for this key, value shares may sum above 100%; multi_value_overlap flags that case.",
                "Freeform key-values only appear when registered with GAM CustomTargetingService.",
            ],
        }

    def get_custom_targeting_value_coverage_for_value_ids(
        self,
        date_range: Literal["lifetime", "this_month", "today"],
        *,
        value_ids: list[str],
        values_by_id: dict[str, dict[str, Any]] | None = None,
        line_item_types: list[str] | None = None,
        min_value_impressions: int = 1,
        requested_timezone: str = "America/New_York",
    ) -> dict[str, Any]:
        """Measure delivered inventory share for many mapped custom targeting values.

        Unlike ``get_custom_targeting_value_coverage()``, this does not resolve
        one key at a time through CustomTargetingService. Callers that already
        have mapped GAM value IDs can use this to run one baseline report and
        then chunk value-ID filters across the whole mapped catalog.
        """
        start_date, end_date, granularity = self._signal_coverage_window(date_range, requested_timezone)
        effective_line_item_types = ["PRICE_PRIORITY"] if line_item_types is None else line_item_types
        normalized_value_ids = sorted({str(int(value_id)) for value_id in value_ids if str(value_id).strip()})
        value_metadata = {value_id: {"id": value_id, "name": ""} for value_id in normalized_value_ids}
        for value_id, metadata in (values_by_id or {}).items():
            normalized_id = str(int(value_id))
            if normalized_id in value_metadata:
                value_metadata[normalized_id] = {"id": normalized_id, **metadata}

        total_impressions, total_revenue = self._signal_coverage_total_inventory(
            start_date=start_date,
            end_date=end_date,
            line_item_types=effective_line_item_types,
        )
        aggregated_values = self._signal_coverage_values_for_ids(
            value_ids=normalized_value_ids,
            values_by_id=value_metadata,
            start_date=start_date,
            end_date=end_date,
            line_item_types=effective_line_item_types,
        )
        filtered_values = [value for value in aggregated_values if value["impressions"] >= min_value_impressions]
        denominator = total_impressions or 1
        for value in filtered_values:
            value["share_of_inventory"] = round(value["impressions"] / denominator, 6)

        return {
            "date_range": date_range,
            "window_start": start_date.date().isoformat(),
            "window_end": end_date.date().isoformat(),
            "timezone": requested_timezone,
            "data_valid_until": self._calculate_data_validity(date_range, requested_timezone).isoformat(),
            "granularity": granularity,
            "filters": {
                "line_item_types": effective_line_item_types,
                "value_ids": normalized_value_ids,
                "min_value_impressions": min_value_impressions,
            },
            "total_inventory": {
                "impressions": total_impressions,
                "revenue": round(total_revenue, 2),
                "average_cpm": round(total_revenue / total_impressions * 1000, 2) if total_impressions > 0 else None,
            },
            "values": filtered_values,
            "coverage": {
                "present_impressions": sum(value["impressions"] for value in filtered_values),
                "value_count": len(filtered_values),
                "requested_value_count": len(normalized_value_ids),
                "report_value_chunk_count": len(self._chunks(normalized_value_ids, 200)),
            },
            "notes": [
                "Coverage is based on delivered historical impressions for the selected line item types.",
                "This bulk result is keyed by GAM custom targeting value ID and may include values from multiple keys.",
            ],
        }

    def _signal_coverage_window(
        self,
        date_range: Literal["lifetime", "this_month", "today"],
        requested_timezone: str,
    ) -> tuple[datetime, datetime, str]:
        _dimensions, start_date, end_date, granularity = self._get_report_config(
            date_range=date_range,
            requested_tz=requested_timezone,
            include_date=False,
        )
        return start_date, end_date, granularity

    def _signal_coverage_total_inventory(
        self,
        *,
        start_date: datetime,
        end_date: datetime,
        line_item_types: list[str] | None,
    ) -> tuple[int, float]:
        baseline_query = self._build_signal_coverage_baseline_query(
            start_date=start_date,
            end_date=end_date,
            line_item_types=line_item_types,
        )
        baseline_rows = self._run_report(baseline_query)
        total_impressions = sum(
            _parse_report_int(self._normalize_report_row(row).get("AD_SERVER_IMPRESSIONS")) for row in baseline_rows
        )
        total_revenue = (
            sum(
                _parse_report_float(self._normalize_report_row(row).get("AD_SERVER_CPM_AND_CPC_REVENUE"))
                for row in baseline_rows
            )
            / 1_000_000
        )
        return total_impressions, total_revenue

    def _signal_coverage_values_for_ids(
        self,
        *,
        value_ids: list[str],
        values_by_id: dict[str, dict[str, Any]],
        start_date: datetime,
        end_date: datetime,
        line_item_types: list[str] | None,
    ) -> list[dict[str, Any]]:
        value_rows = []
        for value_id_chunk in self._chunks(value_ids, 200):
            report_query = self._build_signal_coverage_value_query(
                start_date=start_date,
                end_date=end_date,
                value_ids=value_id_chunk,
                line_item_types=line_item_types,
            )
            value_rows.extend(self._run_report(report_query))
        return self._aggregate_signal_value_rows(value_rows, values_by_id)

    @staticmethod
    def _signal_coverage_forecast(
        *,
        key: dict[str, Any],
        values: list[dict[str, Any]],
        not_present: dict[str, Any],
        total_impressions: int,
        total_revenue: float,
        line_item_types: list[str] | None,
        window_start: str,
        window_end: str,
        multi_value_overlap: bool,
    ) -> dict[str, Any]:
        signal_id = adcp_safe_signal_id(str(key.get("name") or key.get("id") or "signal"))
        signal_name = str(key.get("display_name") or key.get("name") or signal_id)
        scope_label = f"{', '.join(line_item_types)} inventory" if line_item_types else "all line item type inventory"
        return {
            "forecast_range_unit": "availability",
            "method": "estimate",
            "scope": {
                "kind": "inventory",
                "label": scope_label,
                "line_item_types": line_item_types or None,
                "date_range": {"start": window_start, "end": window_end},
                "ad_server": "google_ad_manager",
                "custom_targeting_key_id": str(key.get("id") or ""),
                "custom_targeting_key_name": key.get("name"),
            },
            "bucket_semantics": "overlapping" if multi_value_overlap else "exclusive",
            "bucket_completeness": "partial",
            "points": [
                *[
                    GAMReportingService._signal_coverage_value_point(
                        signal_id=signal_id,
                        signal_name=signal_name,
                        value=value,
                        total_impressions=total_impressions,
                    )
                    for value in values
                ],
                GAMReportingService._signal_coverage_absent_point(
                    signal_id=signal_id,
                    signal_name=signal_name,
                    not_present=not_present,
                    total_impressions=total_impressions,
                ),
            ],
            "ext": {
                "total_inventory": {
                    "impressions": total_impressions,
                    "revenue": round(total_revenue, 2),
                }
            },
        }

    @staticmethod
    def _signal_coverage_value_point(
        *,
        signal_id: str,
        signal_name: str,
        value: dict[str, Any],
        total_impressions: int,
    ) -> dict[str, Any]:
        signal_value_name = value.get("display_name") or value["value"]
        return {
            "label": signal_value_name,
            "dimensions": [
                {
                    "kind": "signal",
                    "signal_id": signal_id,
                    "signal_name": signal_name,
                    "signal_value": value["value"],
                    "signal_value_name": signal_value_name,
                    "presence": "present",
                }
            ],
            "metrics": {
                "impressions": {"mid": value["impressions"]},
                "spend": {"mid": value["revenue"]},
                "coverage_rate": {"mid": GAMReportingService._coverage_share(value["impressions"], total_impressions)},
            },
        }

    @staticmethod
    def _signal_coverage_absent_point(
        *,
        signal_id: str,
        signal_name: str,
        not_present: dict[str, Any],
        total_impressions: int,
    ) -> dict[str, Any]:
        return {
            "label": not_present["label"],
            "dimensions": [
                {
                    "kind": "signal",
                    "signal_id": signal_id,
                    "signal_name": signal_name,
                    "signal_value": None,
                    "presence": "absent",
                }
            ],
            "metrics": {
                "impressions": {"mid": not_present["impressions"]},
                "coverage_rate": {
                    "mid": GAMReportingService._coverage_share(not_present["impressions"], total_impressions)
                },
            },
        }

    @staticmethod
    def _coverage_share(impressions: int, total_impressions: int) -> float:
        if total_impressions <= 0:
            return 0.0
        return round(impressions / total_impressions, 6)

    def _build_price_guidance_report_query(
        self,
        *,
        dimensions: list[str],
        start_date: datetime,
        end_date: datetime,
        placement_ids: list[str] | None,
        ad_unit_ids: list[str] | None,
        countries: list[str] | None,
        line_item_types: list[str] | None,
    ) -> dict[str, Any]:
        where_clauses: list[str] = []
        inventory_filter = self._inventory_target_filter(placement_ids=placement_ids, ad_unit_ids=ad_unit_ids)
        if inventory_filter:
            where_clauses.append(inventory_filter)
        country_filter = self._country_filter(countries)
        if country_filter:
            where_clauses.append(country_filter)
        line_item_type_filter = self._string_in_filter("LINE_ITEM_TYPE", line_item_types)
        if line_item_type_filter:
            where_clauses.append(line_item_type_filter)

        report_query = {
            "dimensions": dimensions,
            "columns": [
                "AD_SERVER_IMPRESSIONS",
                "AD_SERVER_CLICKS",
                "AD_SERVER_CPM_AND_CPC_REVENUE",
                "AD_SERVER_VIDEO_COMPLETIONS",
                "AD_SERVER_ACTIVE_VIEW_VIEWABLE_IMPRESSIONS",
                "AD_SERVER_ACTIVE_VIEW_MEASURABLE_IMPRESSIONS",
            ],
            "dateRangeType": "CUSTOM_DATE",
            "startDate": {"year": start_date.year, "month": start_date.month, "day": start_date.day},
            "endDate": {"year": end_date.year, "month": end_date.month, "day": end_date.day},
            "timeZoneType": "PUBLISHER",
        }
        if where_clauses:
            report_query["statement"] = {"query": "WHERE " + " AND ".join(where_clauses)}
        return {"reportQuery": report_query}

    def _build_signal_coverage_baseline_query(
        self,
        *,
        start_date: datetime,
        end_date: datetime,
        line_item_types: list[str] | None,
    ) -> dict[str, Any]:
        where_clauses = []
        line_item_type_filter = self._string_in_filter("LINE_ITEM_TYPE", line_item_types)
        if line_item_type_filter:
            where_clauses.append(line_item_type_filter)
        report_query = {
            "dimensions": ["LINE_ITEM_TYPE"],
            "columns": [
                "AD_SERVER_IMPRESSIONS",
                "AD_SERVER_CPM_AND_CPC_REVENUE",
            ],
            "dateRangeType": "CUSTOM_DATE",
            "startDate": {"year": start_date.year, "month": start_date.month, "day": start_date.day},
            "endDate": {"year": end_date.year, "month": end_date.month, "day": end_date.day},
            "timeZoneType": "PUBLISHER",
        }
        if where_clauses:
            report_query["statement"] = {"query": "WHERE " + " AND ".join(where_clauses)}
        return {"reportQuery": report_query}

    def _build_signal_coverage_value_query(
        self,
        *,
        start_date: datetime,
        end_date: datetime,
        value_ids: list[str],
        line_item_types: list[str] | None,
    ) -> dict[str, Any]:
        where_clauses = []
        line_item_type_filter = self._string_in_filter("LINE_ITEM_TYPE", line_item_types)
        if line_item_type_filter:
            where_clauses.append(line_item_type_filter)
        value_filter = self._numeric_in_filter("CUSTOM_TARGETING_VALUE_ID", value_ids)
        if value_filter:
            where_clauses.append(value_filter)
        report_query = {
            "dimensions": [
                "CUSTOM_TARGETING_VALUE_ID",
                "CUSTOM_CRITERIA",
                "LINE_ITEM_TYPE",
            ],
            "columns": [
                "AD_SERVER_IMPRESSIONS",
                "AD_SERVER_CPM_AND_CPC_REVENUE",
            ],
            "dateRangeType": "CUSTOM_DATE",
            "startDate": {"year": start_date.year, "month": start_date.month, "day": start_date.day},
            "endDate": {"year": end_date.year, "month": end_date.month, "day": end_date.day},
            "timeZoneType": "PUBLISHER",
        }
        if where_clauses:
            report_query["statement"] = {"query": "WHERE " + " AND ".join(where_clauses)}
        return {"reportQuery": report_query}

    @staticmethod
    def _build_network_revenue_report_query(*, start_date: datetime, end_date: datetime) -> dict[str, Any]:
        return {
            "reportQuery": {
                "dimensions": ["DATE"],
                "columns": [
                    "AD_SERVER_IMPRESSIONS",
                    "AD_SERVER_CPM_AND_CPC_REVENUE",
                ],
                "dateRangeType": "CUSTOM_DATE",
                "startDate": {"year": start_date.year, "month": start_date.month, "day": start_date.day},
                "endDate": {"year": end_date.year, "month": end_date.month, "day": end_date.day},
                "timeZoneType": "PUBLISHER",
            }
        }

    def _line_item_count(self, *, start_date: datetime | None = None, end_date: datetime | None = None) -> int:
        from googleads import ad_manager

        line_item_service = self.client.GetService("LineItemService")
        statement_builder = ad_manager.StatementBuilder().Limit(1)
        if start_date is not None and end_date is not None:
            statement_builder = (
                statement_builder.Where("creationDateTime >= :start AND creationDateTime <= :end")
                .WithBindVariable("start", start_date)
                .WithBindVariable("end", end_date)
            )
        page = line_item_service.getLineItemsByStatement(statement_builder.ToStatement())
        return _total_result_set_size(page)

    @staticmethod
    def _sum_report_revenue(raw_rows: list[dict[str, Any]]) -> float:
        total_revenue_micros = 0.0
        for raw in raw_rows:
            normalized = GAMReportingService._normalize_report_row(raw)
            total_revenue_micros += _parse_report_float(normalized.get("AD_SERVER_CPM_AND_CPC_REVENUE"))
        return total_revenue_micros / 1_000_000

    @staticmethod
    def _project_monthly_revenue(
        revenue_to_date: float,
        *,
        start_date: datetime,
        end_date: datetime,
        requested_timezone: str,
    ) -> float:
        if revenue_to_date <= 0:
            return 0.0

        timezone = pytz.timezone(requested_timezone)
        local_end = end_date.astimezone(timezone) if end_date.tzinfo else timezone.localize(end_date)
        if start_date.month != local_end.month or start_date.year != local_end.year:
            return revenue_to_date

        days_in_month = monthrange(local_end.year, local_end.month)[1]
        elapsed_days = max(1, local_end.day)
        return revenue_to_date / elapsed_days * days_in_month

    @staticmethod
    def _numeric_in_filter(field: str, values: list[str] | None) -> str | None:
        if not values:
            return None
        numeric_values = sorted({str(int(value)) for value in values})
        if not numeric_values:
            return None
        return f"{field} IN ({', '.join(numeric_values)})"

    @staticmethod
    def _inventory_target_filter(*, placement_ids: list[str] | None, ad_unit_ids: list[str] | None) -> str | None:
        filters = [
            value
            for value in (
                GAMReportingService._numeric_in_filter("PLACEMENT_ID", placement_ids),
                GAMReportingService._numeric_in_filter("AD_UNIT_ID", ad_unit_ids),
            )
            if value
        ]
        if not filters:
            return None
        if len(filters) == 1:
            return filters[0]
        return "(" + " OR ".join(filters) + ")"

    @staticmethod
    def _string_in_filter(field: str, values: list[str] | None) -> str | None:
        if not values:
            return None
        escaped = sorted({str(value).replace("'", "''") for value in values if str(value).strip()})
        if not escaped:
            return None
        quoted = ", ".join(f"'{value}'" for value in escaped)
        return f"{field} IN ({quoted})"

    @staticmethod
    def _country_filter(countries: list[str] | None) -> str | None:
        if not countries:
            return None
        country_criteria_ids: list[str] = []
        country_names: list[str] = []
        country_map = GAMReportingService._country_criteria_id_map()
        for value in countries:
            country = str(value).strip()
            if not country:
                continue
            if len(country) == 2 and country.isalpha():
                criteria_id = country_map.get(country.upper())
                if criteria_id:
                    country_criteria_ids.append(criteria_id)
                else:
                    country_names.append(country)
            else:
                country_names.append(country)

        code_filter = GAMReportingService._numeric_in_filter("COUNTRY_CRITERIA_ID", country_criteria_ids)
        name_filter = GAMReportingService._string_in_filter("COUNTRY_NAME", country_names)
        if code_filter and name_filter:
            return f"({code_filter} OR {name_filter})"
        return code_filter or name_filter

    @staticmethod
    def _country_criteria_id_map() -> dict[str, str]:
        mapping_file = Path(__file__).resolve().parent / "gam_geo_mappings.json"
        try:
            with mapping_file.open() as f:
                data = json.load(f)
        except OSError:
            return {}
        countries = data.get("countries", {})
        return {str(code).upper(): str(criteria_id) for code, criteria_id in countries.items()}

    def _custom_targeting_key(self, *, key_name: str | None, key_id: str | int | None) -> dict[str, Any]:
        from googleads import ad_manager

        service = self.client.GetService("CustomTargetingService")
        builder = ad_manager.StatementBuilder().Limit(2)
        if key_id is not None:
            builder = builder.Where("id = :keyId").WithBindVariable("keyId", int(key_id))
        elif key_name is not None:
            escaped_name = key_name.replace("'", "\\'")
            builder = builder.Where(f"name = '{escaped_name}'")

        page = service.getCustomTargetingKeysByStatement(builder.ToStatement())
        results = list(getattr(page, "results", []) or [])
        if not results:
            key_ref = key_id if key_id is not None else key_name
            raise ValueError(f"GAM custom targeting key not found: {key_ref}")
        if len(results) > 1:
            raise ValueError(f"GAM custom targeting key lookup was ambiguous: {key_name}")
        key = results[0]
        return {
            "id": str(_object_field(key, "id")),
            "name": str(_object_field(key, "name") or ""),
            "display_name": _object_field(key, "displayName"),
            "type": _object_field(key, "type"),
            "reportable_type": _object_field(key, "reportableType"),
            "status": _object_field(key, "status"),
        }

    def _custom_targeting_values(self, *, key_id: int, value_names: list[str] | None) -> list[dict[str, Any]]:
        from googleads import ad_manager

        service = self.client.GetService("CustomTargetingService")
        values: list[dict[str, Any]] = []
        offset = 0
        limit = 500
        while True:
            builder = (
                ad_manager.StatementBuilder()
                .Where("customTargetingKeyId = :keyId")
                .WithBindVariable("keyId", key_id)
                .Limit(limit)
                .Offset(offset)
            )
            page = service.getCustomTargetingValuesByStatement(builder.ToStatement())
            results = list(getattr(page, "results", []) or [])
            for value in results:
                values.append(
                    {
                        "id": str(_object_field(value, "id")),
                        "name": str(_object_field(value, "name") or ""),
                        "display_name": _object_field(value, "displayName"),
                        "match_type": _object_field(value, "matchType"),
                        "status": _object_field(value, "status"),
                    }
                )
            total_results = _total_result_set_size(page)
            if offset + limit >= total_results or not results:
                break
            offset += limit

        if value_names:
            requested = {name.lower() for name in value_names}
            values = [value for value in values if value["name"].lower() in requested]
            found = {value["name"].lower() for value in values}
            missing = sorted(requested - found)
            if missing:
                raise ValueError(f"GAM custom targeting values not found for key {key_id}: {', '.join(missing)}")

        return values

    @staticmethod
    def _aggregate_signal_value_rows(
        raw_rows: list[dict[str, Any]], values_by_id: dict[str, dict[str, Any]]
    ) -> list[dict[str, Any]]:
        aggregated: dict[str, dict[str, Any]] = {}
        for raw in raw_rows:
            normalized = GAMReportingService._normalize_report_row(raw)
            value_id = str(normalized.get("CUSTOM_TARGETING_VALUE_ID") or "")
            if not value_id:
                continue
            value = values_by_id.get(value_id, {"id": value_id, "name": ""})
            bucket = aggregated.setdefault(
                value_id,
                {
                    "value_id": value_id,
                    "value": value.get("name") or "",
                    "display_name": value.get("display_name"),
                    "custom_criteria": normalized.get("CUSTOM_CRITERIA") or "",
                    "impressions": 0,
                    "revenue": 0.0,
                },
            )
            bucket["impressions"] += _parse_report_int(normalized.get("AD_SERVER_IMPRESSIONS"))
            bucket["revenue"] += _parse_report_float(normalized.get("AD_SERVER_CPM_AND_CPC_REVENUE")) / 1_000_000

        results = []
        for bucket in aggregated.values():
            impressions = bucket["impressions"]
            revenue = bucket["revenue"]
            bucket["revenue"] = round(revenue, 2)
            bucket["average_cpm"] = round(revenue / impressions * 1000, 2) if impressions > 0 else None
            results.append(bucket)
        results.sort(key=lambda row: row["impressions"], reverse=True)
        return results

    @staticmethod
    def _chunks(values: list[str], size: int) -> list[list[str]]:
        return [values[index : index + size] for index in range(0, len(values), size)]

    def _price_guidance_line_item_rows(
        self,
        raw_rows: list[dict[str, Any]],
        *,
        line_item_types: list[str] | None,
        min_line_item_impressions: int,
        include_zero_revenue: bool,
    ) -> list[dict[str, Any]]:
        allowed_line_item_types = {value.upper() for value in line_item_types or []}
        rows: list[dict[str, Any]] = []
        for raw in raw_rows:
            normalized = self._normalize_report_row(raw)
            line_item_type = str(normalized.get("LINE_ITEM_TYPE") or "").strip()
            if allowed_line_item_types and line_item_type.upper() not in allowed_line_item_types:
                continue

            impressions = _parse_report_int(normalized.get("AD_SERVER_IMPRESSIONS"))
            if impressions < min_line_item_impressions:
                continue

            revenue_micros = _parse_report_float(normalized.get("AD_SERVER_CPM_AND_CPC_REVENUE"))
            if revenue_micros <= 0 and not include_zero_revenue:
                continue
            clicks = _parse_report_int(normalized.get("AD_SERVER_CLICKS"))
            completed_views = _parse_report_int(normalized.get("AD_SERVER_VIDEO_COMPLETIONS"))
            viewable_impressions = _parse_report_int(normalized.get("AD_SERVER_ACTIVE_VIEW_VIEWABLE_IMPRESSIONS"))
            measurable_impressions = _parse_report_int(normalized.get("AD_SERVER_ACTIVE_VIEW_MEASURABLE_IMPRESSIONS"))

            placement_id = str(normalized.get("PLACEMENT_ID") or "").strip()
            ad_unit_id = str(normalized.get("AD_UNIT_ID") or "").strip()
            country = str(normalized.get("COUNTRY_NAME") or "").strip()
            line_item_id = str(normalized.get("LINE_ITEM_ID") or "").strip()
            if not (placement_id or ad_unit_id) or not country or not line_item_id:
                continue

            rows.append(
                {
                    "placement_id": placement_id,
                    "placement_name": str(normalized.get("PLACEMENT_NAME") or ""),
                    "ad_unit_id": ad_unit_id,
                    "ad_unit_name": str(normalized.get("AD_UNIT_NAME") or ""),
                    "country_code": str(normalized.get("COUNTRY_CODE") or "").strip().upper(),
                    "country": country,
                    "line_item_id": line_item_id,
                    "line_item_name": str(normalized.get("LINE_ITEM_NAME") or ""),
                    "line_item_type": line_item_type,
                    "impressions": impressions,
                    "viewable_impressions": viewable_impressions,
                    "measurable_impressions": measurable_impressions,
                    "clicks": clicks,
                    "completed_views": completed_views,
                    "revenue": round(revenue_micros / 1_000_000, 2),
                    "cpm": round(_cpm_from_micros(revenue_micros, impressions), 4),
                    "vcpm": round(_cpm_from_micros(revenue_micros, viewable_impressions), 4)
                    if viewable_impressions > 0
                    else None,
                    "cpc": round(revenue_micros / 1_000_000 / clicks, 4) if clicks > 0 else None,
                    "cpcv": round(revenue_micros / 1_000_000 / completed_views, 4) if completed_views > 0 else None,
                }
            )
        return rows

    @staticmethod
    def _normalize_report_row(row: dict[str, Any]) -> dict[str, Any]:
        normalized: dict[str, Any] = {}
        for key, value in row.items():
            if "." in key:
                normalized[key.split(".")[-1]] = value
            else:
                normalized[key] = value
        return normalized

    @staticmethod
    def _aggregate_price_guidance_groups(
        line_item_rows: list[dict[str, Any]],
        *,
        min_group_impressions: int,
        min_package_budget: float | None,
        bookability_safety_factor: float,
        currency: str,
        publisher_domain: str | None,
        viewability_standard: str,
    ) -> list[dict[str, Any]]:
        grouped: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        for row in line_item_rows:
            target = _price_guidance_inventory_target(row)
            key = (target["kind"], target["id"], row["country_code"], row["country"])
            group = grouped.setdefault(
                key,
                {
                    "inventory_target_kind": target["kind"],
                    "inventory_target_id": target["id"],
                    "inventory_target_name": target["name"],
                    "placement_id": row.get("placement_id") or "",
                    "placement_name": row.get("placement_name") or "",
                    "ad_unit_id": row.get("ad_unit_id") or "",
                    "ad_unit_name": row.get("ad_unit_name") or "",
                    "country_code": row["country_code"],
                    "country": row["country"],
                    "line_items": [],
                },
            )
            group["line_items"].append(row)

        results: list[dict[str, Any]] = []
        for group in grouped.values():
            line_items = group["line_items"]
            total_impressions = sum(row["impressions"] for row in line_items)
            if total_impressions < min_group_impressions:
                continue

            total_revenue = sum(row["revenue"] for row in line_items)
            total_clicks = sum(row["clicks"] for row in line_items)
            total_completed_views = sum(row["completed_views"] for row in line_items)
            total_viewable_impressions = sum(row["viewable_impressions"] for row in line_items)
            total_measurable_impressions = sum(row["measurable_impressions"] for row in line_items)
            weighted_inputs = [(row["cpm"], row["impressions"]) for row in line_items]
            unweighted_inputs = sorted(row["cpm"] for row in line_items)
            price_guidance = _rounded_guidance(
                {
                    "p25": _weighted_percentile(weighted_inputs, 25),
                    "p50": _weighted_percentile(weighted_inputs, 50),
                    "p75": _weighted_percentile(weighted_inputs, 75),
                    "p90": _weighted_percentile(weighted_inputs, 90),
                }
            )
            unweighted_line_item_guidance = _rounded_guidance(
                {
                    "p25": percentile(unweighted_inputs, 25),
                    "p50": percentile(unweighted_inputs, 50),
                    "p75": percentile(unweighted_inputs, 75),
                    "p90": percentile(unweighted_inputs, 90),
                }
            )
            pricing_guidance_by_model = {
                "cpm": price_guidance,
                "vcpm": GAMReportingService._billable_metric_guidance(
                    line_items,
                    price_key="vcpm",
                    weight_key="viewable_impressions",
                    min_billable_units=min_group_impressions,
                ),
                "cpc": GAMReportingService._billable_metric_guidance(
                    line_items,
                    price_key="cpc",
                    weight_key="clicks",
                    min_billable_units=100,
                ),
                "cpcv": GAMReportingService._billable_metric_guidance(
                    line_items,
                    price_key="cpcv",
                    weight_key="completed_views",
                    min_billable_units=100,
                ),
            }
            label = f"{group['inventory_target_name']} / {group['country']}"
            delivery_guidance = GAMReportingService._delivery_guidance(
                label=label,
                impressions=total_impressions,
                viewable_impressions=total_viewable_impressions,
                measurable_impressions=total_measurable_impressions,
                clicks=total_clicks,
                completed_views=total_completed_views,
            )
            bookability = GAMReportingService._bookability(
                available_units=total_impressions,
                price_guidance=price_guidance,
                min_package_budget=min_package_budget,
                safety_factor=bookability_safety_factor,
            )
            forecast_point = GAMReportingService._forecast_point(
                placement_id=group["placement_id"],
                placement_name=group["placement_name"],
                ad_unit_id=group["ad_unit_id"],
                ad_unit_name=group["ad_unit_name"],
                country_code=group["country_code"],
                country=group["country"],
                impressions=total_impressions,
                viewable_impressions=total_viewable_impressions,
                measurable_impressions=total_measurable_impressions,
                clicks=total_clicks,
                completed_views=total_completed_views,
                spend=total_revenue,
                publisher_domain=publisher_domain,
                viewability_standard=viewability_standard,
            )
            results.append(
                {
                    "inventory_target_kind": group["inventory_target_kind"],
                    "inventory_target_id": group["inventory_target_id"],
                    "inventory_target_name": group["inventory_target_name"],
                    "placement_id": group["placement_id"],
                    "placement_name": group["placement_name"],
                    "ad_unit_id": group["ad_unit_id"],
                    "ad_unit_name": group["ad_unit_name"],
                    "country_code": group["country_code"],
                    "country": group["country"],
                    "bookable": bookability["bookable"],
                    "bookability": bookability,
                    "total_impressions": total_impressions,
                    "total_viewable_impressions": total_viewable_impressions,
                    "total_measurable_impressions": total_measurable_impressions,
                    "total_clicks": total_clicks,
                    "total_completed_views": total_completed_views,
                    "total_revenue": round(total_revenue, 2),
                    "average_cpm": round((total_revenue / total_impressions * 1000), 2),
                    "average_vcpm": round((total_revenue / total_viewable_impressions * 1000), 2)
                    if total_viewable_impressions > 0
                    else None,
                    "average_cpc": round((total_revenue / total_clicks), 2) if total_clicks > 0 else None,
                    "average_cpcv": round((total_revenue / total_completed_views), 2)
                    if total_completed_views > 0
                    else None,
                    "ctr": round(total_clicks / total_impressions, 6) if total_impressions > 0 else None,
                    "viewability_rate": round(total_viewable_impressions / total_measurable_impressions, 6)
                    if total_measurable_impressions > 0
                    else None,
                    "completion_rate": round(total_completed_views / total_impressions, 6)
                    if total_impressions > 0
                    else None,
                    "line_item_count": len(line_items),
                    "price_guidance": price_guidance,
                    "pricing_guidance_by_model": pricing_guidance_by_model,
                    "forecast_point": forecast_point,
                    "delivery_guidance": delivery_guidance,
                    "unweighted_line_item_guidance": unweighted_line_item_guidance,
                    "line_items": sorted(line_items, key=lambda row: row["impressions"], reverse=True)[:10],
                }
            )

        results.sort(key=lambda row: row["total_impressions"], reverse=True)
        return results

    @staticmethod
    def _forecast_from_groups(groups: list[dict[str, Any]], *, currency: str) -> dict[str, Any]:
        return {
            "method": "estimate",
            "currency": currency,
            "forecast_range_unit": "availability",
            "points": [group["forecast_point"] for group in groups if group["bookable"]],
        }

    @staticmethod
    def _bookability(
        *,
        available_units: int,
        price_guidance: dict[str, float | None],
        min_package_budget: float | None,
        safety_factor: float,
    ) -> dict[str, Any]:
        p25 = price_guidance.get("p25")
        if min_package_budget is None:
            return {
                "bookable": True,
                "reason": "no_min_package_budget",
                "minimum_package_budget": None,
                "price_basis": "cpm_p25",
                "required_units": None,
                "available_units": available_units,
                "safety_factor": safety_factor,
            }
        if p25 is None or p25 <= 0:
            return {
                "bookable": False,
                "reason": "missing_conservative_price_guidance",
                "minimum_package_budget": min_package_budget,
                "price_basis": "cpm_p25",
                "required_units": None,
                "available_units": available_units,
                "safety_factor": safety_factor,
            }

        required_units = math.ceil((min_package_budget / p25) * 1000 * safety_factor)
        return {
            "bookable": available_units >= required_units,
            "reason": "capacity_meets_minimum_budget" if available_units >= required_units else "insufficient_capacity",
            "minimum_package_budget": round(min_package_budget, 2),
            "price_basis": "cpm_p25",
            "price": p25,
            "required_units": required_units,
            "available_units": available_units,
            "safety_factor": safety_factor,
        }

    @staticmethod
    def _forecast_point(
        *,
        placement_id: str | None,
        placement_name: str | None,
        ad_unit_id: str | None,
        ad_unit_name: str | None,
        country_code: str,
        country: str,
        impressions: int,
        viewable_impressions: int,
        measurable_impressions: int,
        clicks: int,
        completed_views: int,
        spend: float,
        publisher_domain: str | None,
        viewability_standard: str,
    ) -> dict[str, Any]:
        target_name = placement_name or ad_unit_name or "Inventory"
        placement_ref: dict[str, str] = {}
        if placement_id:
            placement_ref["placement_id"] = placement_id
        if ad_unit_id:
            placement_ref["ad_unit_id"] = ad_unit_id
        if publisher_domain:
            placement_ref["publisher_domain"] = publisher_domain

        dimensions: list[dict[str, Any]] = [
            {
                "kind": "placement",
                "placement_ref": placement_ref,
                "placement_name": target_name,
            }
        ]
        if country_code:
            dimensions.append(
                {
                    "kind": "geo",
                    "geo_level": "country",
                    "geo_code": country_code,
                    "geo_name": country,
                }
            )

        metrics: dict[str, dict[str, float]] = {
            "impressions": {"mid": float(impressions)},
            "spend": {"mid": round(float(spend), 2)},
        }
        if clicks > 0:
            metrics["clicks"] = {"mid": float(clicks)}
        if completed_views > 0:
            metrics["completed_views"] = {"mid": float(completed_views)}

        point: dict[str, Any] = {
            "label": f"{target_name} / {country}",
            "dimensions": dimensions,
            "metrics": metrics,
        }
        if measurable_impressions > 0 or viewable_impressions > 0:
            viewability: dict[str, Any] = {
                "vendor": {"domain": "googleadmanager.com"},
                "standard": viewability_standard,
            }
            if measurable_impressions > 0:
                viewability["measurable_impressions"] = {"mid": float(measurable_impressions)}
            if viewable_impressions > 0:
                viewability["viewable_impressions"] = {"mid": float(viewable_impressions)}
            if measurable_impressions > 0 and viewable_impressions > 0:
                viewability["viewable_rate"] = {"mid": round(viewable_impressions / measurable_impressions, 6)}
            point["viewability"] = viewability

        return point

    @staticmethod
    def _billable_metric_guidance(
        line_items: list[dict[str, Any]],
        *,
        price_key: str,
        weight_key: str,
        min_billable_units: int,
    ) -> dict[str, float | None]:
        weighted_inputs = [
            (row[price_key], row[weight_key])
            for row in line_items
            if row.get(price_key) is not None and int(row.get(weight_key) or 0) > 0
        ]
        if sum(weight for _, weight in weighted_inputs) < min_billable_units:
            return {"p25": None, "p50": None, "p75": None, "p90": None}
        return _rounded_guidance(
            {
                "p25": _weighted_percentile(weighted_inputs, 25),
                "p50": _weighted_percentile(weighted_inputs, 50),
                "p75": _weighted_percentile(weighted_inputs, 75),
                "p90": _weighted_percentile(weighted_inputs, 90),
            }
        )

    @staticmethod
    def _delivery_guidance(
        *,
        label: str,
        impressions: int,
        viewable_impressions: int,
        measurable_impressions: int,
        clicks: int,
        completed_views: int,
    ) -> dict[str, Any]:
        metrics: dict[str, dict[str, float]] = {
            "impressions": {"mid": float(impressions)},
        }
        if viewable_impressions > 0:
            metrics["viewable_impressions"] = {"mid": float(viewable_impressions)}
        if clicks > 0:
            metrics["clicks"] = {"mid": float(clicks)}
        if completed_views > 0:
            metrics["completed_views"] = {"mid": float(completed_views)}
        if impressions > 0 and clicks > 0:
            metrics["ctr"] = {"mid": round(clicks / impressions, 6)}
        if measurable_impressions > 0 and viewable_impressions > 0:
            metrics["viewability_rate"] = {"mid": round(viewable_impressions / measurable_impressions, 6)}
        if impressions > 0 and completed_views > 0:
            metrics["completion_rate"] = {"mid": round(completed_views / impressions, 6)}

        return {
            "method": "historical",
            "points": [
                {
                    "label": label,
                    "metrics": metrics,
                }
            ],
        }

    def _parse_timestamp(self, row: dict[str, Any], granularity: str) -> str:
        """Parse timestamp from row based on granularity"""
        if granularity == "hourly":
            # HOUR dimension returns values 0-23 according to documentation
            # Combined with DATE for full timestamp
            date = row.get("DATE", "")
            hour = row.get("HOUR", "0")
            if date:
                # Combine DATE (YYYY-MM-DD) with HOUR (0-23)
                try:
                    hour_val = int(hour)
                    dt = datetime.strptime(date, "%Y-%m-%d")
                    dt = dt.replace(hour=hour_val)
                    return dt.isoformat()
                except (ValueError, TypeError):
                    # Fallback for unexpected format
                    return f"{date}T{hour:02d}:00:00"
        else:  # daily
            # DATE dimension uses ISO 8601 format 'YYYY-MM-DD'
            date = row.get("DATE", "")
            if date:
                return f"{date}T00:00:00"

        return ""

    def _calculate_data_validity(self, date_range: str, requested_tz: str = "America/New_York") -> datetime:
        """
        Calculate when the data is valid until based on GAM's reporting delays

        According to Google documentation:
        - Most data is available within 4 hours
        - Previous month's data is frozen after 3 AM Pacific Time on the first day of every month
        """
        tz = pytz.timezone(requested_tz)
        now = datetime.now(tz)

        # GAM data typically has a 4-hour delay
        four_hours_ago = now - timedelta(hours=4)

        if date_range == "today":
            # For hourly data, be conservative and assume 4-hour delay
            # Round down to the last completed hour
            data_valid_until = four_hours_ago.replace(minute=0, second=0, microsecond=0)
        elif date_range == "this_month":
            # Daily data has the same 4-hour delay
            # If we're early in the day, yesterday's data might not be complete
            if now.hour < 7:  # Account for 4-hour delay + 3 AM PT freeze time
                # Data is valid through 2 days ago
                data_valid_until = (now - timedelta(days=2)).replace(hour=23, minute=59, second=59)
            else:
                # Yesterday's data should be complete
                data_valid_until = (now - timedelta(days=1)).replace(hour=23, minute=59, second=59)
        # Same as this_month for the most recent data
        elif now.hour < 7:
            data_valid_until = (now - timedelta(days=2)).replace(hour=23, minute=59, second=59)
        else:
            data_valid_until = (now - timedelta(days=1)).replace(hour=23, minute=59, second=59)

        return data_valid_until

    def _calculate_metrics(self, data: list[dict[str, Any]]) -> dict[str, Any]:
        """Calculate summary metrics from the processed data"""
        if not data:
            return {
                "total_impressions": 0,
                "total_clicks": 0,
                "total_spend": 0.0,
                "average_ctr": 0.0,
                "average_ecpm": 0.0,
                "total_video_completions": 0,
                "unique_advertisers": 0,
                "unique_orders": 0,
                "unique_line_items": 0,
            }

        total_impressions = sum(row["impressions"] for row in data)
        total_clicks = sum(row["clicks"] for row in data)
        total_spend = sum(row["spend"] for row in data)
        total_video_completions = sum(row.get("video_completions", 0) for row in data)

        # Calculate averages
        avg_ctr = (total_clicks / total_impressions * 100) if total_impressions > 0 else 0.0
        avg_ecpm = (total_spend / total_impressions * 1000) if total_impressions > 0 else 0.0

        # Count unique entities
        unique_advertisers = len({row["advertiser_id"] for row in data if row["advertiser_id"]})
        unique_orders = len({row["order_id"] for row in data if row["order_id"]})
        unique_line_items = len({row["line_item_id"] for row in data if row["line_item_id"]})

        return {
            "total_impressions": total_impressions,
            "total_clicks": total_clicks,
            "total_spend": round(total_spend, 2),
            "average_ctr": round(avg_ctr, 4),
            "average_ecpm": round(avg_ecpm, 2),
            "total_video_completions": total_video_completions,
            "unique_advertisers": unique_advertisers,
            "unique_orders": unique_orders,
            "unique_line_items": unique_line_items,
        }

    def get_country_breakdown(
        self,
        date_range: Literal["lifetime", "this_month", "today"],
        advertiser_id: str | None = None,
        order_id: str | None = None,
        line_item_id: str | None = None,
        requested_timezone: str = "America/New_York",
    ) -> dict[str, Any]:
        """
        Get reporting data broken down by country (aggregated, no DATE dimension)

        Returns:
            Dictionary with country-level metrics for pricing recommendations
        """
        # Get dimensions without DATE for aggregated query
        dimensions, start_date, end_date, granularity = self._get_report_config(
            date_range=date_range,
            requested_tz=requested_timezone,
            include_country=True,
            include_ad_unit=False,
            include_date=False,  # No DATE dimension for aggregated results
        )

        # Build and run the report
        report_query = self._build_report_query(
            dimensions=dimensions,
            start_date=start_date,
            end_date=end_date,
            advertiser_id=advertiser_id,
            order_id=order_id,
            line_item_id=line_item_id,
        )

        raw_data = self._run_report(report_query)

        logger.info(f"Country breakdown report returned {len(raw_data)} rows (aggregated, no DATE dimension)")

        # Process the aggregated data
        processed_data = self._process_report_data(raw_data, granularity, requested_timezone)

        # Aggregate by country
        country_summary = {}
        advertiser_names = {}  # Map advertiser_id to advertiser_name

        for row in processed_data:
            country = row.get("country", "Unknown")
            if not country:
                country = "Unknown"

            if country not in country_summary:
                country_summary[country] = {
                    "country": country,
                    "impressions": 0,
                    "clicks": 0,
                    "spend": 0.0,
                    "unique_advertisers": set(),
                    "unique_orders": set(),
                    "unique_line_items": set(),
                }

            country_summary[country]["impressions"] += row["impressions"]
            country_summary[country]["clicks"] += row["clicks"]
            country_summary[country]["spend"] += row["spend"]

            # Collect advertiser names
            if row["advertiser_id"] and row.get("advertiser_name"):
                advertiser_names[row["advertiser_id"]] = row["advertiser_name"]

            if row["advertiser_id"]:
                country_summary[country]["unique_advertisers"].add(row["advertiser_id"])
            if row["order_id"]:
                country_summary[country]["unique_orders"].add(row["order_id"])
            if row["line_item_id"]:
                country_summary[country]["unique_line_items"].add(row["line_item_id"])

        # Convert sets to counts and calculate metrics
        for country_data in country_summary.values():
            impressions = country_data["impressions"]
            clicks = country_data["clicks"]
            spend = country_data["spend"]

            country_data["ctr"] = round((clicks / impressions * 100) if impressions > 0 else 0, 4)
            country_data["avg_cpm"] = round((spend / impressions * 1000) if impressions > 0 else 0, 2)
            country_data["unique_advertisers"] = len(country_data["unique_advertisers"])
            country_data["unique_orders"] = len(country_data["unique_orders"])
            country_data["unique_line_items"] = len(country_data["unique_line_items"])

        # Sort by spend descending
        sorted_countries = sorted(country_summary.values(), key=lambda x: x["spend"], reverse=True)

        # Calculate data validity and metrics
        data_valid_until = self._calculate_data_validity(date_range)
        metrics = self._calculate_metrics(processed_data)

        return {
            "date_range": date_range,
            "data_valid_until": data_valid_until.isoformat(),
            "timezone": requested_timezone,
            "metrics": metrics,
            "countries": sorted_countries,
            "advertisers": advertiser_names,  # Include advertiser name mapping
            "raw_data": processed_data,  # Include full data for filters
            "total_countries": len(sorted_countries),
            "total_rows_processed": len(raw_data),  # Show how many rows GAM returned
        }

    def get_ad_unit_breakdown(
        self,
        date_range: Literal["lifetime", "this_month", "today"],
        advertiser_id: str | None = None,
        order_id: str | None = None,
        line_item_id: str | None = None,
        country: str | None = None,
        requested_timezone: str = "America/New_York",
    ) -> dict[str, Any]:
        """
        Get reporting data broken down by ad unit (aggregated, no DATE dimension)

        Returns:
            Dictionary with ad unit-level metrics including country breakdown
        """
        # For ad unit breakdown, don't include country dimension initially to avoid timeout
        # We'll only include country if specifically filtering by it
        include_country = country is not None

        # Get dimensions without DATE for aggregated query
        dimensions, start_date, end_date, granularity = self._get_report_config(
            date_range=date_range,
            requested_tz=requested_timezone,
            include_country=include_country,  # Only include if filtering by country
            include_ad_unit=True,
            include_date=False,  # No DATE dimension for aggregated results
        )

        # Build the report query with country filter if specified
        report_query = self._build_report_query(
            dimensions=dimensions,
            start_date=start_date,
            end_date=end_date,
            advertiser_id=advertiser_id,
            order_id=order_id,
            line_item_id=line_item_id,
        )

        # Add country filter to WHERE clause if specified
        if country and report_query.get("reportQuery", {}).get("statement"):
            if report_query["reportQuery"]["statement"]["query"]:
                report_query["reportQuery"]["statement"]["query"] += f" AND COUNTRY_NAME = '{country}'"
            else:
                report_query["reportQuery"]["statement"] = {"query": f"WHERE COUNTRY_NAME = '{country}'"}

        raw_data = self._run_report(report_query)

        logger.info(f"Ad unit breakdown report returned {len(raw_data)} rows (aggregated, no DATE dimension)")

        # Process the aggregated data
        processed_data = self._process_report_data(raw_data, granularity, requested_timezone)

        # Filter by country if specified (in case it wasn't in WHERE clause)
        filtered_data = processed_data
        if country and include_country:
            filtered_data = [row for row in processed_data if row.get("country") == country]

        # Aggregate by ad unit
        ad_unit_summary = {}
        advertiser_names = {}  # Map advertiser_id to advertiser_name
        all_countries = set()  # Track all countries in the data

        for row in filtered_data:
            ad_unit_id = row.get("ad_unit_id", "Unknown")
            if not ad_unit_id:
                ad_unit_id = "Unknown"

            if ad_unit_id not in ad_unit_summary:
                ad_unit_summary[ad_unit_id] = {
                    "ad_unit_id": ad_unit_id,
                    "ad_unit_name": row.get("ad_unit_name", ""),
                    "impressions": 0,
                    "clicks": 0,
                    "spend": 0.0,
                    "countries": {},  # Track metrics by country
                    "unique_advertisers": set(),
                    "unique_orders": set(),
                    "unique_line_items": set(),
                }

            # Aggregate overall metrics
            ad_unit_summary[ad_unit_id]["impressions"] += row["impressions"]
            ad_unit_summary[ad_unit_id]["clicks"] += row["clicks"]
            ad_unit_summary[ad_unit_id]["spend"] += row["spend"]

            # Collect advertiser names
            if row["advertiser_id"] and row.get("advertiser_name"):
                advertiser_names[row["advertiser_id"]] = row["advertiser_name"]

            # Track by country only if country data is available
            if include_country:
                country_name = row.get("country", "Unknown")
                all_countries.add(country_name)
                if country_name not in ad_unit_summary[ad_unit_id]["countries"]:
                    ad_unit_summary[ad_unit_id]["countries"][country_name] = {
                        "impressions": 0,
                        "clicks": 0,
                        "spend": 0.0,
                    }

                ad_unit_summary[ad_unit_id]["countries"][country_name]["impressions"] += row["impressions"]
                ad_unit_summary[ad_unit_id]["countries"][country_name]["clicks"] += row["clicks"]
                ad_unit_summary[ad_unit_id]["countries"][country_name]["spend"] += row["spend"]

            if row["advertiser_id"]:
                ad_unit_summary[ad_unit_id]["unique_advertisers"].add(row["advertiser_id"])
            if row["order_id"]:
                ad_unit_summary[ad_unit_id]["unique_orders"].add(row["order_id"])
            if row["line_item_id"]:
                ad_unit_summary[ad_unit_id]["unique_line_items"].add(row["line_item_id"])

        # Convert sets to counts and calculate metrics
        for ad_unit_data in ad_unit_summary.values():
            impressions = ad_unit_data["impressions"]
            clicks = ad_unit_data["clicks"]
            spend = ad_unit_data["spend"]

            ad_unit_data["ctr"] = round((clicks / impressions * 100) if impressions > 0 else 0, 4)
            ad_unit_data["avg_cpm"] = round((spend / impressions * 1000) if impressions > 0 else 0, 2)
            ad_unit_data["unique_advertisers"] = len(ad_unit_data["unique_advertisers"])
            ad_unit_data["unique_orders"] = len(ad_unit_data["unique_orders"])
            ad_unit_data["unique_line_items"] = len(ad_unit_data["unique_line_items"])

            # Calculate CPM for each country
            for country_data in ad_unit_data["countries"].values():
                c_impressions = country_data["impressions"]
                c_spend = country_data["spend"]
                country_data["cpm"] = round((c_spend / c_impressions * 1000) if c_impressions > 0 else 0, 2)

        # Sort by spend descending
        sorted_ad_units = sorted(ad_unit_summary.values(), key=lambda x: x["spend"], reverse=True)

        # Calculate data validity and metrics
        data_valid_until = self._calculate_data_validity(date_range)
        metrics = self._calculate_metrics(filtered_data)

        return {
            "date_range": date_range,
            "data_valid_until": data_valid_until.isoformat(),
            "timezone": requested_timezone,
            "metrics": metrics,
            "ad_units": sorted_ad_units,
            "advertisers": advertiser_names,  # Include advertiser name mapping
            "countries": sorted(all_countries),  # Include all countries for filter
            "raw_data": filtered_data,  # Include full data for filters
            "total_ad_units": len(sorted_ad_units),
            "filtered_by_country": country,
            "total_rows_processed": len(raw_data),  # Show how many rows GAM returned
        }

    def get_advertiser_summary(
        self,
        advertiser_id: str,
        date_range: Literal["lifetime", "this_month", "today"],
        requested_timezone: str = "America/New_York",
    ) -> dict[str, Any]:
        """
        Get a summary of all orders and line items for an advertiser

        Returns aggregated data by order and line item
        """
        report_data = self.get_reporting_data(
            date_range=date_range, advertiser_id=advertiser_id, requested_timezone=requested_timezone
        )

        # Aggregate by order and line item
        order_summary = {}
        line_item_summary = {}

        for row in report_data.data:
            order_id = row["order_id"]
            line_item_id = row["line_item_id"]

            # Aggregate by order
            if order_id not in order_summary:
                order_summary[order_id] = {
                    "order_id": order_id,
                    "order_name": row["order_name"],
                    "impressions": 0,
                    "clicks": 0,
                    "spend": 0.0,
                    "line_items": set(),
                }

            order_summary[order_id]["impressions"] += row["impressions"]
            order_summary[order_id]["clicks"] += row["clicks"]
            order_summary[order_id]["spend"] += row["spend"]
            order_summary[order_id]["line_items"].add(line_item_id)

            # Aggregate by line item
            if line_item_id not in line_item_summary:
                line_item_summary[line_item_id] = {
                    "line_item_id": line_item_id,
                    "line_item_name": row["line_item_name"],
                    "order_id": order_id,
                    "order_name": row["order_name"],
                    "impressions": 0,
                    "clicks": 0,
                    "spend": 0.0,
                }

            line_item_summary[line_item_id]["impressions"] += row["impressions"]
            line_item_summary[line_item_id]["clicks"] += row["clicks"]
            line_item_summary[line_item_id]["spend"] += row["spend"]

        # Convert sets to counts
        for order in order_summary.values():
            order["line_item_count"] = len(order["line_items"])
            del order["line_items"]

        return {
            "advertiser_id": advertiser_id,
            "date_range": date_range,
            "data_valid_until": report_data.data_valid_until.isoformat(),
            "timezone": report_data.data_timezone,
            "metrics": report_data.metrics,
            "orders": list(order_summary.values()),
            "line_items": list(line_item_summary.values()),
        }
