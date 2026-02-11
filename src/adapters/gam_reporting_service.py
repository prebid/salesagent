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
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal
from urllib.parse import urlparse

import pytz
import requests

logger = logging.getLogger(__name__)


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
        else:
            # Include DATE dimension for time-series data
            if date_range == "today":
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
            raise Exception(f"Error running GAM report: {str(e)}")

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
            "Dimension.AD_UNIT_ID": "AD_UNIT_ID",
            "Dimension.AD_UNIT_NAME": "AD_UNIT_NAME",
            # Metrics - only including the ones we're actually requesting
            "Column.AD_SERVER_IMPRESSIONS": "AD_SERVER_IMPRESSIONS",
            "Column.AD_SERVER_CLICKS": "AD_SERVER_CLICKS",
            "Column.AD_SERVER_CPM_AND_CPC_REVENUE": "AD_SERVER_CPM_AND_CPC_REVENUE",
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

            # Skip rows with zero impressions to reduce data volume
            impressions = int(normalized_row.get("AD_SERVER_IMPRESSIONS", 0) or 0)
            if impressions == 0:
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
                    "row_count": 0,  # Track number of rows aggregated
                }

            # Aggregate metrics
            agg = aggregated_data[agg_key]
            agg["impressions"] += int(normalized_row.get("AD_SERVER_IMPRESSIONS", 0) or 0)
            agg["clicks"] += int(normalized_row.get("AD_SERVER_CLICKS", 0) or 0)
            agg["revenue_micros"] += float(normalized_row.get("AD_SERVER_CPM_AND_CPC_REVENUE", 0) or 0)
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
                "aggregated_rows": agg_data["row_count"],  # Useful for debugging
            }

            processed.append(processed_row)

        # Sort by timestamp and then by spend (descending)
        processed.sort(key=lambda x: (x["timestamp"], -x["spend"]))

        # Log aggregation results
        logger.info(f"Aggregated {len(raw_data)} raw rows into {len(processed)} aggregated rows")

        return processed

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
        else:  # lifetime
            # Same as this_month for the most recent data
            if now.hour < 7:
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
