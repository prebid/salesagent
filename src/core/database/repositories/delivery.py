"""Delivery repository — tenant-scoped data access for webhook delivery tables.

Covers two ORM models:
- WebhookDeliveryRecord: webhook payload snapshots with retry tracking
- WebhookDeliveryLog: delivery report webhook sends with sequence tracking

Core invariant: every query includes tenant_id in the WHERE clause. The tenant_id
is set at construction time and injected into all queries automatically.

Write methods add objects to the session but never commit — the caller (or UoW)
handles commit/rollback at the boundary.

beads: salesagent-7x3i
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.core.database.models import WebhookDeliveryLog, WebhookDeliveryRecord


class DeliveryRepository:
    """Tenant-scoped data access for WebhookDeliveryRecord and WebhookDeliveryLog.

    All queries filter by tenant_id automatically. Write methods add objects
    to the session but never commit — the Unit of Work handles that.

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
    # WebhookDeliveryRecord reads
    # ------------------------------------------------------------------

    def get_record_by_id(self, delivery_id: str) -> WebhookDeliveryRecord | None:
        """Get a delivery record by its ID within the tenant."""
        return self._session.scalars(
            select(WebhookDeliveryRecord).where(
                WebhookDeliveryRecord.tenant_id == self._tenant_id,
                WebhookDeliveryRecord.delivery_id == delivery_id,
            )
        ).first()

    def list_records_by_tenant(
        self,
        *,
        status: str | None = None,
        event_type: str | None = None,
        limit: int | None = None,
    ) -> list[WebhookDeliveryRecord]:
        """List delivery records for the tenant, with optional filters.

        Args:
            status: Filter by delivery status (e.g., "pending", "delivered", "failed").
            event_type: Filter by event type (e.g., "creative.status_changed").
            limit: Maximum number of records to return.
        """
        stmt = select(WebhookDeliveryRecord).where(
            WebhookDeliveryRecord.tenant_id == self._tenant_id,
        )
        if status is not None:
            stmt = stmt.where(WebhookDeliveryRecord.status == status)
        if event_type is not None:
            stmt = stmt.where(WebhookDeliveryRecord.event_type == event_type)
        stmt = stmt.order_by(WebhookDeliveryRecord.created_at.desc())
        if limit is not None:
            stmt = stmt.limit(limit)
        return list(self._session.scalars(stmt).all())

    # ------------------------------------------------------------------
    # WebhookDeliveryRecord writes
    # ------------------------------------------------------------------

    def create_record(
        self,
        *,
        delivery_id: str,
        webhook_url: str,
        payload: dict[str, Any],
        event_type: str,
        object_id: str | None = None,
        status: str = "pending",
        attempts: int = 0,
        created_at: datetime | None = None,
    ) -> WebhookDeliveryRecord:
        """Create a new webhook delivery record.

        Does NOT commit — the caller handles that.
        """
        record = WebhookDeliveryRecord(
            delivery_id=delivery_id,
            tenant_id=self._tenant_id,
            webhook_url=webhook_url,
            payload=payload,
            event_type=event_type,
            object_id=object_id,
            status=status,
            attempts=attempts,
        )
        if created_at is not None:
            record.created_at = created_at
        self._session.add(record)
        self._session.flush()
        return record

    def update_record(
        self,
        delivery_id: str,
        *,
        status: str | None = None,
        attempts: int | None = None,
        response_code: int | None = None,
        last_error: str | None = None,
        last_attempt_at: datetime | None = None,
        delivered_at: datetime | None = None,
    ) -> WebhookDeliveryRecord | None:
        """Update fields on a delivery record within this tenant.

        Returns the updated record, or None if not found.
        Does NOT commit — the caller handles that.
        """
        record = self.get_record_by_id(delivery_id)
        if record is None:
            return None
        if status is not None:
            record.status = status
        if attempts is not None:
            record.attempts = attempts
        if response_code is not None:
            record.response_code = response_code
        if last_error is not None:
            record.last_error = last_error
        if last_attempt_at is not None:
            record.last_attempt_at = last_attempt_at
        if delivered_at is not None:
            record.delivered_at = delivered_at
        self._session.flush()
        return record

    # ------------------------------------------------------------------
    # WebhookDeliveryLog reads
    # ------------------------------------------------------------------

    def get_logs_by_webhook_id(
        self,
        media_buy_id: str,
        *,
        task_type: str | None = None,
        status: str | None = None,
    ) -> list[WebhookDeliveryLog]:
        """Get delivery logs for a media buy within the tenant.

        Args:
            media_buy_id: The media buy to get logs for.
            task_type: Filter by task type (e.g., "media_buy_delivery").
            status: Filter by log status (e.g., "success", "failed").
        """
        stmt = select(WebhookDeliveryLog).where(
            WebhookDeliveryLog.tenant_id == self._tenant_id,
            WebhookDeliveryLog.media_buy_id == media_buy_id,
        )
        if task_type is not None:
            stmt = stmt.where(WebhookDeliveryLog.task_type == task_type)
        if status is not None:
            stmt = stmt.where(WebhookDeliveryLog.status == status)
        stmt = stmt.order_by(WebhookDeliveryLog.created_at.desc())
        return list(self._session.scalars(stmt).all())

    def get_recent_successful_log(
        self,
        media_buy_id: str,
        *,
        task_type: str,
        notification_type: str,
        since: datetime,
    ) -> WebhookDeliveryLog | None:
        """Find a recent successful log entry (for duplicate detection).

        Used by the scheduler to check if a report was already sent.
        """
        return self._session.scalars(
            select(WebhookDeliveryLog).where(
                WebhookDeliveryLog.tenant_id == self._tenant_id,
                WebhookDeliveryLog.media_buy_id == media_buy_id,
                WebhookDeliveryLog.task_type == task_type,
                WebhookDeliveryLog.notification_type == notification_type,
                WebhookDeliveryLog.status == "success",
                WebhookDeliveryLog.created_at > since,
            )
        ).first()

    def get_max_sequence_number(
        self,
        media_buy_id: str,
        *,
        task_type: str,
    ) -> int:
        """Get the maximum sequence number for a media buy's delivery logs.

        Returns 0 if no logs exist (caller should add 1 for the next sequence).
        """
        result = self._session.scalar(
            select(func.coalesce(func.max(WebhookDeliveryLog.sequence_number), 0)).where(
                WebhookDeliveryLog.tenant_id == self._tenant_id,
                WebhookDeliveryLog.media_buy_id == media_buy_id,
                WebhookDeliveryLog.task_type == task_type,
            )
        )
        return result or 0

    # ------------------------------------------------------------------
    # WebhookDeliveryLog writes
    # ------------------------------------------------------------------

    def create_log(
        self,
        *,
        log_id: str,
        principal_id: str,
        media_buy_id: str,
        webhook_url: str,
        task_type: str,
        status: str,
        attempt_count: int = 1,
        sequence_number: int = 1,
        notification_type: str | None = None,
        http_status_code: int | None = None,
        error_message: str | None = None,
        payload_size_bytes: int | None = None,
        response_time_ms: int | None = None,
        completed_at: datetime | None = None,
        next_retry_at: datetime | None = None,
    ) -> WebhookDeliveryLog:
        """Create or update a webhook delivery log entry.

        Uses session.merge() to handle upsert semantics (the protocol webhook
        service updates the same log entry across retry attempts).

        Does NOT commit — the caller handles that.
        """
        log_entry = WebhookDeliveryLog(
            id=log_id,
            tenant_id=self._tenant_id,
            principal_id=principal_id,
            media_buy_id=media_buy_id,
            webhook_url=webhook_url,
            task_type=task_type,
            status=status,
            attempt_count=attempt_count,
            sequence_number=sequence_number,
            notification_type=notification_type,
            http_status_code=http_status_code,
            error_message=error_message,
            payload_size_bytes=payload_size_bytes,
            response_time_ms=response_time_ms,
            completed_at=completed_at,
            next_retry_at=next_retry_at,
        )
        self._session.merge(log_entry)
        self._session.flush()
        return log_entry
