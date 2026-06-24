#!/usr/bin/env python3
"""
Sync all GAM-enabled tenants via the sync API.

Intended for cron (every 6h). Each invocation iterates GAM-configured
tenants, gates them through ``should_sync_tenant`` so tenants synced
within their per-tenant cadence window are skipped, and triggers a
fresh full sync via the sync API for the rest.

Sprint 1.8 §8 wires the cadence column (``Tenant.sync_cadence_minutes``)
into the cron loop. NULL = use ``DEFAULT_SYNC_CADENCE_MINUTES`` (6h).

Memory note (important): this script runs as a SEPARATE process under
supercronic, in the SAME container as the server. It deliberately uses
SQLAlchemy Core with lightweight table definitions and reuses the app's
``get_db_session`` (connection/pool/config) — but it does NOT import the
ORM models in ``src.core.database.models``. Importing those models pulls in
the ~1 GB ``adcp`` dependency; as a second resident copy alongside the
server it pushed the container into memory pressure, which made the server
miss ELB health checks and get recycled every time the cron fired. Keeping
this process adcp-free (≈45 MB instead of ≈1.2 GB) removes that pressure.
The ``database_session`` layer is importable adcp-free because the
embedded-tenant guard registration was moved to models.py (see
src/core/database/__init__.py). Do NOT add ``src.core.database.models``
imports here.
"""

import logging
import os
import secrets
import sys
from datetime import UTC, datetime, timedelta
from typing import Protocol

import requests
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    select,
)

# Add parent directory to path to import modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import ONLY the session layer — it is adcp-free (the embedded-tenant guard
# registration lives in models.py, not the package __init__). Importing
# src.core.database.models here would re-introduce the ~1 GB adcp load.
from src.core.database.database_session import get_db_session

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


# Default cadence when ``Tenant.sync_cadence_minutes`` is NULL.
# Matches the legacy crontab cadence so untouched tenants keep their
# previous behavior. Publishers tune via the management API.
DEFAULT_SYNC_CADENCE_MINUTES = 360


# --- Lightweight SQLAlchemy Core table definitions -------------------------
# Only the columns this cron reads/writes. These mirror the ORM models in
# src/core/database/models.py (table + column names verified against the
# mappers) but are declared with Core so this process never imports the ORM
# (and thus never loads adcp). Keep these in sync with models.py if the
# corresponding columns are renamed.
_metadata = MetaData()

_superadmin_config = Table(
    "superadmin_config",
    _metadata,
    Column("config_key", String(100), primary_key=True),
    Column("config_value", Text),
    Column("description", Text),
    Column("updated_by", String(255)),
    Column("updated_at", DateTime(timezone=True)),
)

_tenants = Table(
    "tenants",
    _metadata,
    Column("tenant_id", String, primary_key=True),
    Column("name", String),
    Column("ad_server", String),
    Column("is_active", Boolean),
    Column("sync_cadence_minutes", Integer),
)

_adapter_config = Table(
    "adapter_config",
    _metadata,
    Column("tenant_id", String, primary_key=True),
    Column("gam_network_code", String),
    Column("gam_refresh_token", Text),
)

_sync_jobs = Table(
    "sync_jobs",
    _metadata,
    Column("sync_id", String, primary_key=True),
    Column("tenant_id", String),
    Column("status", String),
    Column("completed_at", DateTime(timezone=True)),
)


class _TenantCadence(Protocol):
    """Structural type for ``should_sync_tenant``'s tenant argument.

    Accepts anything exposing ``sync_cadence_minutes`` — the Core result row
    here, or a ``SimpleNamespace`` in unit tests — without importing the ORM
    ``Tenant`` model (which would pull in adcp; see module docstring).
    """

    sync_cadence_minutes: int | None


def initialize_tenant_management_api_key() -> str:
    """Return the tenant-management API key, creating it if absent.

    Uses Core SQL against ``superadmin_config`` rather than the ORM
    ``TenantManagementConfig`` model so this process stays adcp-free (see
    module docstring). Behaviour matches the ``src.admin.sync_api`` original.
    """
    with get_db_session() as session:
        row = session.execute(
            select(_superadmin_config.c.config_value).where(_superadmin_config.c.config_key == "api_key")
        ).first()
        if row and row[0]:
            return row[0]

        api_key = f"sk_{secrets.token_urlsafe(32)}"
        session.execute(
            _superadmin_config.insert().values(
                config_key="api_key",
                config_value=api_key,
                description="Tenant management API key for programmatic access",
                updated_by="system",
                updated_at=datetime.now(UTC),
            )
        )
        session.commit()
        logger.info("Generated new sync API key")
        return api_key


def should_sync_tenant(
    tenant: _TenantCadence,
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
    """Return the most-recent ``completed_at`` across successful sync_jobs
    rows for a tenant, or None if no sync ever succeeded.

    Custom-targeting bundles into the inventory worker (the inventory
    job's ``completed_at`` covers both rows), so the picker collapses
    inventory + custom_targeting + advertisers into a single MAX. Any
    successful sync resets the cadence window — partial coverage is
    fine; the next tick that exceeds cadence picks up wherever the
    last full sync left off.
    """
    row = session.execute(
        select(_sync_jobs.c.completed_at)
        .where(
            _sync_jobs.c.tenant_id == tenant_id,
            _sync_jobs.c.status == "completed",
            _sync_jobs.c.completed_at.is_not(None),
        )
        .order_by(_sync_jobs.c.completed_at.desc())
        .limit(1)
    ).first()
    return row[0] if row else None


def sync_all_gam_tenants():
    """Sync all tenants that have Google Ad Manager configured."""
    # Get API key
    api_key = initialize_tenant_management_api_key()

    now = datetime.now(UTC)

    # Get all GAM tenants from database (Core SQL — no ORM/adcp).
    with get_db_session() as session:
        tenant_rows = session.execute(
            select(
                _tenants.c.tenant_id,
                _tenants.c.name,
                _tenants.c.sync_cadence_minutes,
            )
            .select_from(_tenants)
            .join(_adapter_config, _adapter_config.c.tenant_id == _tenants.c.tenant_id)
            .where(
                _tenants.c.ad_server == "google_ad_manager",
                _tenants.c.is_active.is_(True),
                _adapter_config.c.gam_network_code.is_not(None),
                _adapter_config.c.gam_refresh_token.is_not(None),
            )
        ).all()

        # Pull last-success per tenant inside the same session — avoids
        # opening a second session per tenant on the cadence-gating loop.
        cadence_decisions: list[tuple[str, str, bool, int, datetime | None]] = []
        for row in tenant_rows:
            latest_completed = _latest_successful_sync(session, row.tenant_id)
            should_run, effective_cadence = should_sync_tenant(row, latest_completed, now)
            cadence_decisions.append((row.tenant_id, row.name, should_run, effective_cadence, latest_completed))

    if not cadence_decisions:
        logger.info("No GAM tenants found to sync")
        return

    eligible = [d for d in cadence_decisions if d[2]]
    logger.info(f"Found {len(cadence_decisions)} GAM tenants; {len(eligible)} eligible this tick")

    # Sync each eligible tenant
    for tenant_id, tenant_name, should_run, effective_cadence, latest_completed in cadence_decisions:
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
