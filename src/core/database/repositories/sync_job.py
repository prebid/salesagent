"""SyncJob repository — tenant-scoped data access for background sync jobs.

Centralizes the JSONB-merge guard (``flag_modified``), terminal-status writes,
the duplicate-order scan, and the staleness reaper for the order-approval
polling flow.

Core invariant: every query includes ``tenant_id`` in the WHERE clause. The
``flag_modified`` guard for ``SyncJob.progress`` writes lives in
``merge_progress`` — callers cannot forget it, because they never touch the
JSONB column directly.

Design rules:
  - Write methods add to or mutate session-attached rows but never commit;
    the Unit of Work (``SyncJobUoW``) owns the transaction boundary.
  - Read methods return ``None`` when the row is not found within the
    tenant (no cross-tenant fallback).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from src.core.database.models import SyncJob


class SyncJobRepository:
    """Tenant-scoped data access for ``SyncJob``.

    All queries filter by ``tenant_id`` automatically. Callers cannot bypass
    tenant isolation. Write methods do not commit — the Unit of Work
    (``SyncJobUoW``) handles commit/rollback at the boundary.

    Args:
        session: SQLAlchemy session (caller manages lifecycle).
        tenant_id: Tenant scope for all queries.
    """

    def __init__(self, session: Session, tenant_id: str) -> None:
        self._session = session
        self._tenant_id = tenant_id

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    # ------------------------------------------------------------------
    # Read methods
    # ------------------------------------------------------------------

    def get(self, sync_id: str) -> SyncJob | None:
        """Get a ``SyncJob`` by ``sync_id`` within the tenant, or ``None``."""
        stmt = select(SyncJob).where(
            SyncJob.tenant_id == self._tenant_id,
            SyncJob.sync_id == sync_id,
        )
        return self._session.scalars(stmt).first()

    def find_running_for_order(
        self,
        order_id: str,
        *,
        sync_type: str = "order_approval",
    ) -> SyncJob | None:
        """Return the live ``running`` ``SyncJob`` for an order, or ``None``.

        Walks the tenant's ``running`` SyncJobs of the given ``sync_type``
        and matches on ``progress.order_id``. Callers that need
        liveness should call ``reap_stale()`` first, otherwise this will
        also surface rows belonging to dead worker processes.
        """
        stmt = select(SyncJob).where(
            SyncJob.tenant_id == self._tenant_id,
            SyncJob.sync_type == sync_type,
            SyncJob.status == "running",
        )
        for row in self._session.scalars(stmt):
            if row.progress and row.progress.get("order_id") == order_id:
                return row
        return None

    # ------------------------------------------------------------------
    # Write methods (do not commit — UoW handles transaction boundary)
    # ------------------------------------------------------------------

    def merge_progress(self, sync_id: str, data: dict[str, Any]) -> SyncJob | None:
        """Merge ``data`` into the ``SyncJob.progress`` JSONB column.

        Calls ``flag_modified`` on the column so SQLAlchemy's dirty
        tracker detects the in-place mutation. If the row's progress
        is currently ``NULL`` the column is seeded with a fresh copy
        of ``data`` instead of merging.

        Returns the updated ``SyncJob``, or ``None`` if no row matches
        within the tenant.
        """
        row = self.get(sync_id)
        if row is None:
            return None
        if row.progress:
            row.progress.update(data)
            flag_modified(row, "progress")
        else:
            row.progress = dict(data)
        return row

    def mark_terminal(
        self,
        sync_id: str,
        *,
        status: str,
        completed_at: datetime,
        summary: dict[str, Any] | str | None = None,
        error_message: str | None = None,
    ) -> SyncJob | None:
        """Flip a ``SyncJob`` to a terminal status (``completed`` / ``failed``).

        ``summary`` may be a ``dict`` (JSON-serialized for the ``Text``
        column) or a string. Returns the updated ``SyncJob``, or ``None``
        if no row matches within the tenant.
        """
        row = self.get(sync_id)
        if row is None:
            return None
        row.status = status
        row.completed_at = completed_at
        if isinstance(summary, dict):
            row.summary = json.dumps(summary)
        else:
            row.summary = summary
        if error_message is not None:
            row.error_message = error_message
        return row

    def reap_stale(
        self,
        threshold: timedelta,
        *,
        sync_type: str = "order_approval",
        now: datetime | None = None,
        stale_error_message: str | None = None,
    ) -> list[str]:
        """Flip stale ``running`` SyncJobs to ``failed`` for the tenant.

        A ``SyncJob`` is stale if its ``started_at`` is older than
        ``threshold`` — the worker process is presumed dead and the
        row is blocking re-entry for its ``order_id``.

        Returns the list of reaped ``sync_id`` values so callers can
        log them.
        """
        if now is None:
            now = datetime.now(UTC)
        if stale_error_message is None:
            stale_error_message = (
                f"Worker thread presumed dead (no progress for {threshold}); "
                "marked as failed to allow fresh re-entry. Downstream state "
                "is unchanged — the next live attempt will advance it."
            )
        stmt = select(SyncJob).where(
            SyncJob.tenant_id == self._tenant_id,
            SyncJob.sync_type == sync_type,
            SyncJob.status == "running",
        )
        reaped: list[str] = []
        for row in self._session.scalars(stmt):
            started = row.started_at
            if started.tzinfo is None:
                started = started.replace(tzinfo=UTC)
            if now - started > threshold:
                row.status = "failed"
                row.completed_at = now
                row.error_message = stale_error_message
                reaped.append(row.sync_id)
        return reaped

    def create_for_order(
        self,
        *,
        sync_id: str,
        adapter_type: str,
        order_id: str,
        media_buy_id: str,
        principal_id: str,
        webhook_url: str | None,
        started_at: datetime,
        max_attempts: int,
        triggered_by: str = "order_creation",
        sync_type: str = "order_approval",
        extra_progress: dict[str, Any] | None = None,
    ) -> SyncJob:
        """Insert a ``SyncJob`` row for a fresh order-approval poll.

        Builds the canonical ``progress`` dict, attaches the row to the
        session, and returns it. Does not commit — ``SyncJobUoW`` handles
        the transaction boundary.
        """
        progress: dict[str, Any] = {
            "order_id": order_id,
            "media_buy_id": media_buy_id,
            "principal_id": principal_id,
            "webhook_url": webhook_url,
            "attempts": 0,
            "max_attempts": max_attempts,
            "phase": "Starting approval polling",
        }
        if extra_progress:
            progress.update(extra_progress)
        row = SyncJob(
            sync_id=sync_id,
            tenant_id=self._tenant_id,
            adapter_type=adapter_type,
            sync_type=sync_type,
            status="running",
            started_at=started_at,
            triggered_by=triggered_by,
            triggered_by_id=media_buy_id,
            progress=progress,
        )
        self._session.add(row)
        return row
