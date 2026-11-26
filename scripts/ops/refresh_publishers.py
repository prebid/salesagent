#!/usr/bin/env python3
"""Refresh every publisher's adagents.json for every tenant: the scheduled ingest.

Run by supercronic every six hours (``crontab``). Each publisher is ingested in a unit of
work of its own (``src/services/publisher_ingest.py``); a failed fetch is recorded on that
publisher's row, keeps its stored document, and is retried on the next run.
"""

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.services.publisher_ingest import refresh_every_publisher  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def main() -> None:
    outcomes = refresh_every_publisher()
    failed = [outcome for outcome in outcomes if outcome.fetch_status != "ok"]
    logger.info(
        "Refreshed %d publishers: %d fetched, %d failed", len(outcomes), len(outcomes) - len(failed), len(failed)
    )
    for outcome in failed:
        logger.warning("%s: %s (%s)", outcome.domain, outcome.fetch_status, outcome.error)


if __name__ == "__main__":
    main()
