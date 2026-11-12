"""Delivery repository — tenant-scoped data access for webhook delivery tables.

Covers two ORM models:
- WebhookDeliveryRecord: webhook payload snapshots with retry tracking
- WebhookDeliveryLog: delivery report webhook sends with sequence tracking

Core invariant: every query includes tenant_id in the WHERE clause. The tenant_id
is set at construction time and injected into all queries automatically.

Write methods add objects to the session but never commit — the caller (or UoW)
handles commit/rollback at the boundary.

"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.core.database.models import MediaBuy, WebhookDeliveryLog, WebhookDeliveryRecord
from src.core.webhooks.delivery import WebhookDeliveryOutcome, WebhookTaskContext

# The ``task_type`` that marks a row as the delivery-poll sequence counter rather
# than a delivery. Named here because the repository owns what the row means; a
# caller spelling it is how it started looking like an outcome.
POLL_SEQUENCE_TASK_TYPE = "delivery_poll"
SCHEDULER_SEQUENCE_TASK_TYPE = "media_buy_delivery"

# ``webhook_delivery_log.webhook_url`` is NOT NULL and a sequence-counter row has
# no destination. The scheme is deliberately not http(s): nothing may ever mistake
# this for something that was, or could be, dialled.
_POLL_SEQUENCE_PLACEHOLDER_URL = "delivery_poll://internal"

# How each :class:`WebhookDeliveryOutcome` kind is written down. A kind absent
# from this map records NO row — today that is ``refused_auth`` alone, and the
# absence is the ruling, not an oversight (see :meth:`record_outcome`).
_OUTCOME_STATUS: dict[str, str] = {
    "delivered": "success",
    "refused_destination": "refused",
    "client_error": "failed",
    "exhausted": "failed",
}


def _outcome_fields(outcome: WebhookDeliveryOutcome, response_time_ms: int | None) -> dict[str, Any] | None:
    """The one projection from a delivery conclusion to persisted columns."""
    status = _OUTCOME_STATUS.get(outcome.kind)
    if status is None:
        return None
    return {
        "status": status,
        "attempt_count": outcome.attempts,
        "http_status_code": outcome.http_status,
        "error_message": outcome.detail,
        "payload_size_bytes": outcome.payload_size_bytes,
        "response_time_ms": response_time_ms,
        "completed_at": datetime.now(UTC),
    }


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

    def reserve_next_sequence(
        self,
        *,
        principal_id: str,
        media_buy_id: str,
        webhook_url: str,
        notification_type: str | None = None,
    ) -> WebhookDeliveryLog:
        """Reserve one scheduler event; the caller must commit before delivery.

        The transaction lock covers the tenant/buy/task stream, including an
        empty stream. Every status consumes a sequence. This does not reclaim
        outstanding reservations or coordinate HTTP delivery (#1638).
        """
        owned_buy = self._session.scalar(
            select(MediaBuy.media_buy_id).where(
                MediaBuy.tenant_id == self._tenant_id,
                MediaBuy.principal_id == principal_id,
                MediaBuy.media_buy_id == media_buy_id,
            )
        )
        if owned_buy is None:
            raise ValueError(f"Media buy {media_buy_id!r} is outside the reservation's tenant/principal scope")
        stream = json.dumps([self._tenant_id, media_buy_id, SCHEDULER_SEQUENCE_TASK_TYPE])
        lock_key = int.from_bytes(hashlib.sha256(stream.encode()).digest()[:8], byteorder="big", signed=True)
        self._session.execute(select(func.pg_advisory_xact_lock(lock_key)))
        sequence = self.get_max_sequence_number(media_buy_id, task_type=SCHEDULER_SEQUENCE_TASK_TYPE) + 1
        return self._create_log(
            log_id=str(uuid4()),
            principal_id=principal_id,
            media_buy_id=media_buy_id,
            webhook_url=webhook_url,
            task_type=SCHEDULER_SEQUENCE_TASK_TYPE,
            sequence_number=sequence,
            notification_type=notification_type,
            status="reserved",
            attempt_count=0,
            insert_only=True,
        )

    def mark_delivery_result(
        self,
        log_id: str,
        *,
        outcome: WebhookDeliveryOutcome,
        response_time_ms: int | None = None,
        next_retry_at: datetime | None = None,
    ) -> WebhookDeliveryLog:
        """Acknowledge an exact scheduler reservation without allocating again.

        ``outcome.attempts`` is cumulative for this event, not an increment.
        Success is terminal; lower attempt totals and refused_auth are no-ops.
        The retry date records caller intent only, without choosing a policy.
        Refresh under the row lock so a stale identity map cannot undo success.
        Flush only: the caller owns commit, rollback and persistence failures.
        """
        row = self._session.scalars(
            select(WebhookDeliveryLog)
            .where(
                WebhookDeliveryLog.tenant_id == self._tenant_id,
                WebhookDeliveryLog.id == log_id,
                WebhookDeliveryLog.task_type == SCHEDULER_SEQUENCE_TASK_TYPE,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        ).one_or_none()
        if row is None:
            raise ValueError(f"Scheduler reservation {log_id!r} not found for tenant {self._tenant_id!r}")
        fields = _outcome_fields(outcome, response_time_ms)
        if row.status == "success" or outcome.attempts < row.attempt_count or fields is None:
            return row
        if next_retry_at is not None:
            if fields["status"] != "failed":
                raise ValueError("Only a failed delivery outcome can carry retry intent")
            fields.update(status="retrying", completed_at=None)
        for name, value in fields.items():
            setattr(row, name, value)
        row.next_retry_at = next_retry_at
        self._session.flush()
        return row

    def record_outcome(
        self,
        *,
        ctx: WebhookTaskContext,
        log_id: str,
        webhook_url: str,
        outcome: WebhookDeliveryOutcome,
        response_time_ms: int | None = None,
    ) -> None:
        """Record an unreserved delivery through the shared outcome projection.

        Both senders conclude here. Before this existed, the only persisted
        recorder was a private fourteen-parameter method on ONE sender, reachable
        from nothing else, and it ran on failure paths only — so "did this webhook
        conclude, and how" was answerable for one sender, half the time. The other
        sender wrote nothing at all. Two senders each re-deriving the encoding is
        how a refusal came to be spelled the same as a delivery the buyer's
        endpoint actually rejected.

        The kind -> row mapping lives here, once:

        ============================  ==========  ===============================
        ``outcome.kind``              ``status``  ``attempt_count``
        ============================  ==========  ===============================
        ``delivered``                 success     ``outcome.attempts``
        ``refused_destination``       refused     ``outcome.attempts`` — always 0
        ``client_error``              failed      ``outcome.attempts``
        ``exhausted``                 failed      ``outcome.attempts``
        ``refused_auth``              *no row*    —
        ============================  ==========  ===============================

        ``attempt_count`` is the outcome's own count, never re-derived from the
        kind: a refusal arrives with ``attempts=0`` because nothing was attempted
        (``webhook_egress._outcome_for_outbound_error``), and a recorder that
        second-guessed that would be one more place the two could disagree.

        ``refused_destination`` gets its own status value rather than reusing
        ``"failed"``: a destination refused before a connection was opened is not
        a delivery that failed on the wire, and an operator who cannot tell those
        apart cannot tell a misconfigured URL from a flaky endpoint. It is a
        String column, so this is a new value and not a migration.

        ``refused_auth`` writes NOTHING, deliberately: the buyer registered an
        authentication this sender cannot produce, so no request was ever built.
        A row claiming an attempt would misreport a refusal as a delivery that
        failed on the wire. The buyer-actionable refusal already happened at
        registration time.

        Silently writes nothing when ``ctx`` is not log-eligible — that gate
        (:attr:`WebhookTaskContext.records_delivery_log`) used to be spelled once
        per branch at each sender.

        Does NOT commit, and does NOT open a session — the caller owns the
        transaction boundary, exactly one per conclusion.
        """
        fields = _outcome_fields(outcome, response_time_ms)
        if fields is None or not ctx.records_delivery_log:
            return

        # records_delivery_log already requires all three of these truthy; the
        # assert only narrows mypy's view from str | None to str (it cannot
        # narrow through a property call) and can never fire.
        assert ctx.tenant_id and ctx.principal_id and ctx.media_buy_id and ctx.task_type

        self._create_log(
            log_id=log_id,
            principal_id=ctx.principal_id,
            media_buy_id=ctx.media_buy_id,
            webhook_url=webhook_url,
            task_type=ctx.task_type,
            sequence_number=ctx.sequence_number,
            notification_type=ctx.notification_type,
            **fields,
        )

    def record_poll_sequence(
        self,
        *,
        principal_id: str,
        media_buy_id: str,
        sequence_number: int,
        notification_type: str | None,
    ) -> None:
        """Persist the per-media-buy delivery-poll sequence counter.

        This row is NOT a delivery outcome and never was: nothing was sent, no
        destination exists, and ``webhook_url`` is a placeholder. It exists only
        so the next poll can read ``get_max_sequence_number`` and continue the
        count. It is spelled as its own method — rather than smuggled through
        the outcome writer, or through a public ``create_log`` kept alive "for
        that one caller" — because a writer whose name does not say what the row
        IS is precisely how this row came to look like a delivery.

        WRITE-ONLY, and deliberately NOT atomic: the caller still reads
        :meth:`get_max_sequence_number` and passes ``max + 1``. Folding the read
        and the write together here would be a behavior change (a real
        reservation) that this method is not introducing.

        ``log_id`` is generated here because callers have nothing to say about
        it: :meth:`_create_log` upserts on ``id`` via ``merge()``, so a stable or
        derived id would silently turn insert-per-poll into update-in-place. A
        fresh uuid per call preserves today's behavior exactly.

        Does NOT commit — the caller handles that.
        """
        self._create_log(
            log_id=str(uuid4()),
            principal_id=principal_id,
            media_buy_id=media_buy_id,
            webhook_url=_POLL_SEQUENCE_PLACEHOLDER_URL,
            task_type=POLL_SEQUENCE_TASK_TYPE,
            status="success",
            sequence_number=sequence_number,
            notification_type=notification_type,
        )

    def _create_log(
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
        insert_only: bool = False,
    ) -> WebhookDeliveryLog:
        """Create or update a webhook delivery log entry.

        Uses session.merge() to handle upsert semantics (the protocol webhook
        service updates the same log entry across retry attempts).

        Does NOT commit — the caller handles that.

        Reservations use ``insert_only`` and return the persistent inserted row;
        they must never inherit the legacy upsert semantics.

        PRIVATE. It is the single construction site for ``WebhookDeliveryLog``,
        and it is private so that it stays that way: a public writer taking
        fifteen loose keyword arguments is what let a *sequence counter* and a
        *delivery outcome* be written as the same kind of thing by callers that
        each decided for themselves what ``status`` and ``task_type`` meant.
        Callers use :meth:`record_outcome`, :meth:`record_poll_sequence`, or
        :meth:`reserve_next_sequence`,
        which name what the row IS.
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
        if insert_only:
            self._session.add(log_entry)
        else:
            self._session.merge(log_entry)
        self._session.flush()
        return log_entry
