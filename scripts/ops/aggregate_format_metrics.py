#!/usr/bin/env python3
"""
Aggregate format performance metrics for all GAM-enabled tenants.

This script queries GAM ReportService for historical data by COUNTRY_CODE + CREATIVE_SIZE,
calculates CPM percentiles, and stores results in format_performance_metrics table.

Intended to be run as a daily cron job:
  0 2 * * * /path/to/aggregate_format_metrics.py

Arguments:
  --period-days N  Number of days to aggregate (default: 30)
"""

import argparse
import logging
import os
import sys

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.services.format_metrics_service import aggregate_all_tenants

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def main():
    """Main entry point for format metrics aggregation."""
    parser = argparse.ArgumentParser(description="Aggregate format performance metrics from GAM")
    parser.add_argument(
        "--period-days",
        type=int,
        default=30,
        help="Number of days to aggregate (default: 30)",
    )

    args = parser.parse_args()

    logger.info(f"Starting format metrics aggregation (period_days={args.period_days})")

    try:
        summary = aggregate_all_tenants(period_days=args.period_days)
        logger.info(
            f"Aggregation complete: {summary['successful']} successful, "
            f"{summary['failed']} failed out of {summary['total_tenants']} tenants"
        )

        # Exit with error code if any failed
        if summary["failed"] > 0:
            sys.exit(1)

    except Exception as e:
        logger.error(f"Format metrics aggregation failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
