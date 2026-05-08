#!/usr/bin/env python3
"""
Sync all GAM-enabled tenants via the sync API.

Intended for cron (every 6h). Each invocation iterates GAM-configured
tenants, gates them through ``should_sync_tenant`` so tenants synced
within their per-tenant cadence window are skipped, and triggers a
fresh full sync via the sync API for the rest.

Sprint 1.8 §8 wires the cadence column (``Tenant.sync_cadence_minutes``)
into the cron loop. NULL = use ``DEFAULT_SYNC_CADENCE_MINUTES`` (6h).
"""

import logging
import os
import sys
from datetime import UTC, datetime, timedelta

import requests
from sqlalchemy import select

# Add parent directory to path to import modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.admin.sync_api import initialize_tenant_management_api_key
from src.core.database.database_session import get_db_session
from src.core.database.models import AdapterConfig, SyncJob, Tenant

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


# Default cadence when ``Tenant.sync_cadence_minutes`` is NULL.
# Matches the legacy crontab cadence so untouched tenants keep their
# previous behavior. Publishers tune via the management API.
DEFAULT_SYNC_CADENCE_MINUTES = 360


def should_sync_tenant(
    tenant: Tenant,
    latest_sync_completed_at: datetime | None,
    now: datetime,
) -> tuple[bool, int]:
    """Decide whether the cron should run a sync for ``tenant`` this tick.

    Returns ``(should_run, effective_cadence_minutes)``.

    Rules:
    - Tenant has never synced successfully → run (initial backfill is
      mandatory regardless of cadence).
    - ``Tenant.sync_cadence_minutes`` if non-NULL else
      ``DEFAULT_SYNC_CADENCE_MINUTES`` (360) is the cadence window.
    - Most-recent successful sync was within the cadence window → skip.
    - Otherwise → run.

    The decision is pure (no DB / clock side-effects) so it can be
    unit-tested without integration plumbing.
    """
    effective_cadence = tenant.sync_cadence_minutes or DEFAULT_SYNC_CADENCE_MINUTES

    if latest_sync_completed_at is None:
        return True, effective_cadence

    # Normalize naive datetimes to UTC — DB rows from Postgres come back
    # tz-aware, but synthesized test values may be naive.
    if latest_sync_completed_at.tzinfo is None:
        latest_sync_completed_at = latest_sync_completed_at.replace(tzinfo=UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)

    next_eligible = latest_sync_completed_at + timedelta(minutes=effective_cadence)
    return now >= next_eligible, effective_cadence


def _latest_successful_sync(session, tenant_id: str) -> datetime | None:
    """Return the most-recent ``completed_at`` across SuccessSyncJob rows
    for a tenant, or None if no sync ever succeeded.

    Custom-targeting bundles into the inventory worker (the inventory
    job's ``completed_at`` covers both rows), so the picker collapses
    inventory + custom_targeting + advertisers into a single MAX. Any
    successful sync resets the cadence window — partial coverage is
    fine; the next tick that exceeds cadence picks up wherever the
    last full sync left off.
    """
    row = session.execute(
        select(SyncJob.completed_at)
        .where(
            SyncJob.tenant_id == tenant_id,
            SyncJob.status == "completed",
            SyncJob.completed_at.is_not(None),
        )
        .order_by(SyncJob.completed_at.desc())
        .limit(1)
    ).first()
    return row[0] if row else None


def sync_all_gam_tenants():
    """Sync all tenants that have Google Ad Manager configured."""
    # Get API key
    api_key = initialize_tenant_management_api_key()

    now = datetime.now(UTC)

    # Get all GAM tenants from database using ORM
    with get_db_session() as session:
        tenants = session.execute(
            select(Tenant, AdapterConfig)
            .join(AdapterConfig, Tenant.tenant_id == AdapterConfig.tenant_id)
            .where(
                Tenant.ad_server == "google_ad_manager",
                Tenant.is_active.is_(True),
                AdapterConfig.gam_network_code.is_not(None),
                AdapterConfig.gam_refresh_token.is_not(None),
            )
        ).all()

        # Pull last-success per tenant inside the same session — avoids
        # opening a second session per tenant on the cadence-gating loop.
        cadence_decisions: list[tuple[Tenant, AdapterConfig, bool, int, datetime | None]] = []
        for tenant, adapter_config in tenants:
            latest_completed = _latest_successful_sync(session, tenant.tenant_id)
            should_run, effective_cadence = should_sync_tenant(tenant, latest_completed, now)
            cadence_decisions.append((tenant, adapter_config, should_run, effective_cadence, latest_completed))

    if not cadence_decisions:
        logger.info("No GAM tenants found to sync")
        return

    eligible = [d for d in cadence_decisions if d[2]]
    logger.info(f"Found {len(cadence_decisions)} GAM tenants; {len(eligible)} eligible this tick")

    # Sync each eligible tenant
    for tenant, _adapter_config, should_run, effective_cadence, latest_completed in cadence_decisions:
        tenant_id = tenant.tenant_id
        tenant_name = tenant.name

        if not should_run:
            next_eligible = latest_completed + timedelta(minutes=effective_cadence) if latest_completed else "n/a"
            logger.info(
                "Skipping tenant %s (%s): synced %s, cadence=%dm, next eligible %s",
                tenant_name,
                tenant_id,
                latest_completed.isoformat() if latest_completed else "never",
                effective_cadence,
                next_eligible,
            )
            continue

        logger.info(f"Syncing tenant: {tenant_name} ({tenant_id})")

        try:
            # Call sync API
            response = requests.post(
                f"http://localhost:{os.environ.get('ADCP_SALES_PORT', 8080)}/api/v1/sync/trigger/{tenant_id}",
                headers={"X-API-Key": api_key},
                json={"sync_type": "full"},
                timeout=300,  # 5 minute timeout per tenant
            )

            if response.status_code == 200:
                result = response.json()
                if result.get("status") == "completed":
                    logger.info(f"✓ Sync completed for {tenant_name}")
                    if "summary" in result:
                        summary = result["summary"]
                        logger.info(f"  - Ad units: {summary.get('ad_units', {}).get('total', 0)}")
                        logger.info(f"  - Targeting keys: {summary.get('custom_targeting', {}).get('total_keys', 0)}")
                else:
                    logger.warning(f"Sync status for {tenant_name}: {result.get('status')}")
            elif response.status_code == 409:
                logger.info(f"Sync already in progress for {tenant_name}")
            else:
                logger.error(f"Failed to sync {tenant_name}: HTTP {response.status_code}")

        except requests.exceptions.Timeout:
            logger.error(f"Sync timeout for {tenant_name}")
        except Exception as e:
            logger.error(f"Error syncing {tenant_name}: {e}")

    logger.info("Sync job completed")


if __name__ == "__main__":
    logger.info("Starting scheduled sync job")
    sync_all_gam_tenants()
