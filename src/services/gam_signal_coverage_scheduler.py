"""Scheduled GAM signal coverage sync.

Runs a lightweight eligibility check hourly and dispatches a GAM Reporting
API coverage sync when a tenant's signal coverage data is older than the
daily freshness window. The sync itself persists to ``sync_jobs`` with
``sync_type='signal_coverage'``.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta

from src.core.database.database_session import get_db_session
from src.core.database.repositories.adapter_config import AdapterConfigAdminRepository
from src.core.database.repositories.sync_job import SyncJobAdminRepository
from src.services._scheduler_lifecycle import cancel_scheduler_task
from src.services.catalog_sync_helpers import run_catalog_sync_scheduler_cycle
from src.services.gam_signal_coverage_sync import KIND_SIGNAL_COVERAGE, run_gam_signal_coverage_sync
from src.services.gam_sync_applicability import tenant_has_custom_key_value_signals

logger = logging.getLogger(__name__)

# Check cheaply once an hour, but only run a tenant when the latest successful
# signal coverage sync is older than one day.
SLEEP_INTERVAL_SECONDS = int(os.getenv("GAM_SIGNAL_COVERAGE_SYNC_INTERVAL") or "3600")
SIGNAL_COVERAGE_STALE_AFTER = timedelta(seconds=int(os.getenv("GAM_SIGNAL_COVERAGE_STALE_AFTER_SECONDS") or "86400"))
MAX_CONCURRENT_TENANTS = int(os.getenv("GAM_SIGNAL_COVERAGE_MAX_CONCURRENT_TENANTS") or "3")


class GAMSignalCoverageScheduler:
    """Fixed-interval scheduler for GAM key-value signal coverage."""

    def __init__(self) -> None:
        self.is_running = False
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        async with self._lock:
            if self.is_running:
                logger.warning("GAM signal coverage scheduler is already running")
                return
            self.is_running = True
            self._task = asyncio.create_task(self._run())
            logger.info("GAM signal coverage scheduler started (interval=%ss)", SLEEP_INTERVAL_SECONDS)

    async def stop(self) -> None:
        async with self._lock:
            if not self.is_running:
                return
            self.is_running = False
            await cancel_scheduler_task(self._task)
            logger.info("GAM signal coverage scheduler stopped")

    async def _run(self) -> None:
        while self.is_running:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("GAM signal coverage scheduler iteration failed")
            await asyncio.sleep(SLEEP_INTERVAL_SECONDS)

    async def run_once(self, *, now: datetime | None = None) -> list[str]:
        return await run_catalog_sync_scheduler_cycle(
            now=now,
            list_eligible_tenants=_list_eligible_tenants,
            max_concurrent=MAX_CONCURRENT_TENANTS,
            sync_func=run_gam_signal_coverage_sync,
            triggered_by="scheduler_signal_coverage",
            logger=logger,
            crash_message="GAM signal coverage dispatch crashed for tenant=%s",
            failure_message="Scheduled GAM signal coverage sync failed: tenant=%s errors=%s",
            cycle_complete_message="GAM signal coverage cycle complete: dispatched=%d eligible=%d",
        )


def _list_eligible_tenants(now: datetime) -> list[str]:
    """Return GAM tenants with mapped key-value signals and stale coverage."""
    with get_db_session() as session:
        pairs = AdapterConfigAdminRepository(session).list_all()
        gam_tenant_ids = [p.tenant_id for p in pairs if p.adapter_type == "google_ad_manager"]
        signal_tenant_ids = [
            tenant_id for tenant_id in gam_tenant_ids if tenant_has_custom_key_value_signals(session, tenant_id)
        ]
        if not signal_tenant_ids:
            return []

        triples = [(tenant_id, "google_ad_manager", KIND_SIGNAL_COVERAGE) for tenant_id in signal_tenant_ids]
        latest = SyncJobAdminRepository(session).latest_for_triples(triples)

    eligible: list[str] = []
    for tenant_id in signal_tenant_ids:
        last = latest.get((tenant_id, "google_ad_manager", KIND_SIGNAL_COVERAGE))
        if last is None:
            eligible.append(tenant_id)
            continue
        if last.status in ("running", "queued"):
            continue
        if last.status == "completed" and last.completed_at is not None:
            if (now - last.completed_at) <= SIGNAL_COVERAGE_STALE_AFTER:
                continue
        eligible.append(tenant_id)
    return eligible


_scheduler: GAMSignalCoverageScheduler | None = None


def get_gam_signal_coverage_scheduler() -> GAMSignalCoverageScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = GAMSignalCoverageScheduler()
    return _scheduler


async def start_gam_signal_coverage_scheduler() -> None:
    await get_gam_signal_coverage_scheduler().start()


async def stop_gam_signal_coverage_scheduler() -> None:
    await get_gam_signal_coverage_scheduler().stop()
