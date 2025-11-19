"""
GAM Data Freshness Validation

Provides utilities to determine if GAM reporting data is fresh enough for sending webhooks.
Since GAM doesn't provide a real-time freshness API, we use heuristics based on:
1. Report metadata (date ranges in response)
2. Known GAM processing delays (4 hours)
3. Documented freeze times (3 AM PT for monthly data)
"""

import logging
from datetime import UTC, datetime, timedelta
from src.adapters.gam_reporting_service import ReportingData

import pytz

logger = logging.getLogger(__name__)


class GAMDataFreshnessValidator:
    """Validates whether GAM reporting data is fresh enough for webhook delivery."""

    # Known GAM processing delays
    STANDARD_DELAY_HOURS = 4
    DAILY_DATA_READY_HOUR_ET = 7  # 7 AM ET = 4 AM PT (1 hour after freeze)
    MONTHLY_FREEZE_HOUR_PT = 3  # 3 AM PT on 1st of month

    @classmethod
    def is_data_fresh_for_webhook(
        cls,
        reporting_data,
        target_date: datetime | None = None,
        timezone: str = "America/New_York",
    ) -> tuple[bool, str]:
        """Determine if reporting data is fresh enough to send via webhook.

        Args:
            reporting_data: ReportingData from GAMReportingService
            target_date: Date we want data for (defaults to yesterday)
            timezone: Timezone for calculations

        Returns:
            Tuple of (is_fresh: bool, reason: str)
        """
        tz = pytz.timezone(timezone)
        now = datetime.now(tz)

        # Default to yesterday's data
        if target_date is None:
            target_date = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)

        # Check 1: Is it too early in the day for yesterday's data?
        if now.hour < cls.DAILY_DATA_READY_HOUR_ET:
            message = f"Too early for fresh data (current hour: {now.hour}, need {cls.DAILY_DATA_READY_HOUR_ET}+)"
            logger.error(message)

            return (
                False,
                message
            )

        # Check 2: Does the report actually include our target date?
        report_end = reporting_data.end_date
        if report_end.date() < target_date.date():
            message = f"Report ends {report_end.date()}, but we need data through {target_date.date()}"
            logger.error(message)
            
            return (
                False,
                message
            )

        # Check 3: Is the data valid until after our target date?
        data_valid_until = reporting_data.data_valid_until
        if data_valid_until.date() < target_date.date():
            message = f"Data only valid until {data_valid_until.date()}, need {target_date.date()}"
            logger.error(message)
            
            return (
                False,
                message
            )

        # Check 4: Is this month-end data that might still be processing?
        if target_date.day == 1 and target_date.month != (target_date - timedelta(days=1)).month:
            # First day of new month - previous month might still be processing
            pt_tz = pytz.timezone("America/Los_Angeles")
            now_pt = datetime.now(pt_tz)
            if now_pt.hour < cls.MONTHLY_FREEZE_HOUR_PT:
                return (
                    False,
                    f"Month-end data still processing (PT hour: {now_pt.hour}, freeze at {cls.MONTHLY_FREEZE_HOUR_PT})",
                )

        return (True, "Data is fresh")

    @classmethod
    def get_freshness_timestamp(cls, reporting_data) -> dict:
        """Get metadata about data freshness for webhook payload.

        Returns:
            Dict with freshness metadata to include in webhook
        """
        return {
            "data_valid_until": reporting_data.data_valid_until.isoformat(),
            "data_timezone": reporting_data.data_timezone,
            "report_end_date": reporting_data.end_date.isoformat(),
            "query_type": reporting_data.query_type,
            "freshness_checked_at": datetime.now(UTC).isoformat(),
        }

    @classmethod
    def should_retry_later(
        cls,
        reporting_data,
        target_date: datetime,
        timezone: str = "America/New_York",
    ) -> tuple[bool, datetime | None]:
        """Determine if we should retry fetching data later.

        Args:
            reporting_data: ReportingData from GAMReportingService
            target_date: Date we want data for
            timezone: Timezone for calculations

        Returns:
            Tuple of (should_retry: bool, retry_at: datetime | None)
        """
        is_fresh, reason = cls.is_data_fresh_for_webhook(reporting_data, target_date, timezone)

        if is_fresh:
            return (False, None)

        # Calculate when to retry based on the reason
        tz = pytz.timezone(timezone)
        now = datetime.now(tz)

        if "Too early" in reason:
            # Retry at the daily data ready hour
            retry_at = now.replace(hour=cls.DAILY_DATA_READY_HOUR_ET, minute=5, second=0, microsecond=0)
            if retry_at < now:
                retry_at += timedelta(days=1)
            return (True, retry_at)

        if "Month-end" in reason:
            # Retry 1 hour after monthly freeze time (in PT)
            pt_tz = pytz.timezone("America/Los_Angeles")
            retry_at_pt = datetime.now(pt_tz).replace(
                hour=cls.MONTHLY_FREEZE_HOUR_PT + 1,
                minute=5,
                second=0,
                microsecond=0,
            )
            # Convert back to requested timezone
            retry_at = retry_at_pt.astimezone(tz)
            if retry_at < now:
                retry_at += timedelta(days=1)
            return (True, retry_at)

        # For other reasons, retry in 1 hour
        return (True, now + timedelta(hours=1))


def validate_and_log_freshness(
    reporting_data,
    media_buy_id: str,
    target_date: datetime | None = None,
) -> bool:
    """Validate data freshness and log the result.

    Convenience function for use in webhook scheduler.

    Args:
        reporting_data: ReportingData from GAMReportingService
        media_buy_id: Media buy ID for logging context
        target_date: Date we want data for

    Returns:
        True if data is fresh enough to send
    """
    validator = GAMDataFreshnessValidator()
    is_fresh, reason = validator.is_data_fresh_for_webhook(reporting_data, target_date)

    if is_fresh:
        logger.info(f"Data is fresh for media buy {media_buy_id}: {reason}")
        return True
    else:
        logger.warning(f"Data not fresh for media buy {media_buy_id}: {reason}")

        # Check if we should retry
        should_retry, retry_at = validator.should_retry_later(reporting_data, target_date or datetime.now())
        if should_retry and retry_at:
            logger.info(f"Will retry media buy {media_buy_id} at {retry_at}")

        return False
