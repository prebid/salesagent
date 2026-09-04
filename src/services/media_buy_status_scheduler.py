"""Media Buy Status Scheduler - Automatically transitions media buy statuses.

This scheduler runs in the background and updates media buy statuses based on
their flight dates:
- pending_activation -> active (when start_time has passed and creatives approved)
- scheduled -> active (when start_time has passed)
- active -> completed (when end_time has passed)

This ensures media buys don't get stuck in transitional states when approved
before their start date.
"""

import asyncio
import logging
import os
from datetime import UTC, datetime
from enum import Enum, auto

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from src.core.database.database_session import get_db_session, is_connection_dead
from src.core.database.models import Creative, CreativeAssignment, MediaBuy, PersistedMediaBuyStatus
from src.core.database.repositories import MediaBuyRepository
from src.core.metrics import record_scheduler_isolation_error
from src.core.tools._media_buy_transitions import resolve_flight_window_status

logger = logging.getLogger(__name__)

# Configurable via env var - default 60 seconds
STATUS_CHECK_INTERVAL_SECONDS = int(os.getenv("MEDIA_BUY_STATUS_CHECK_INTERVAL") or "60")

# Batch summary prefix — shared with tests via this constant (not a string literal).
STATUS_BATCH_SUMMARY_PREFIX = "Media buy status update complete"


class _BuyOutcome(Enum):
    """Closed outcome domain for one buy inside a status-scheduler tick."""

    FLIPPED = auto()
    ISOLATED = auto()
    NOOP = auto()


def _classify_scheduler_error(exc: Exception) -> str:
    """Bounded ``error_type`` for this scheduler's isolation metric.

    Owned here (services layer) rather than in ``src/core/metrics.py`` — the
    observability layer should not import ``sqlalchemy`` to interpret an
    exception population it doesn't own. Callers pass the already-bounded
    string to :func:`record_scheduler_isolation_error`.
    """
    return "db_error" if isinstance(exc, SQLAlchemyError) else "other"


_ACTIVATABLE_STATUSES = frozenset(
    {
        PersistedMediaBuyStatus.PENDING_START,
        PersistedMediaBuyStatus.PENDING_ACTIVATION,
        PersistedMediaBuyStatus.SCHEDULED,
    }
)


class MediaBuyStatusScheduler:
    """Scheduler for updating media buy statuses based on flight dates."""

    def __init__(self) -> None:
        self.is_running = False
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        """Start the scheduler background task."""
        async with self._lock:
            if self.is_running:
                logger.warning("Media buy status scheduler is already running")
                return

            self.is_running = True
            self._task = asyncio.create_task(self._run_scheduler())
            logger.info(f"Media buy status scheduler started (checking every {STATUS_CHECK_INTERVAL_SECONDS}s)")

    async def stop(self) -> None:
        """Stop the scheduler background task."""
        async with self._lock:
            if not self.is_running:
                return

            self.is_running = False
            if self._task:
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
            logger.info("Media buy status scheduler stopped")

    async def _run_scheduler(self) -> None:
        """Main scheduler loop - runs on a fixed cadence."""
        while self.is_running:
            try:
                await self._update_statuses()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in media buy status scheduler: {e}", exc_info=True)
            finally:
                # Wait before next check
                await asyncio.sleep(STATUS_CHECK_INTERVAL_SECONDS)

    def _process_one_media_buy(self, media_buy: MediaBuy, now: datetime, session: Session) -> _BuyOutcome:
        """Run one buy's status check inside its own SAVEPOINT; isolate DB errors.

        Ids are captured into plain locals *inside* the per-buy ``try`` and
        *before* the SAVEPOINT opens — under production ``autoflush=True`` a
        flush failure expires ORM attributes, so reading ``media_buy.tenant_id``
        after the fact can raise ``PendingRollbackError`` and hide the original
        exception. Capturing inside the ``try`` also keeps a lazy-id failure
        from aborting the rest of the tick (the #1714 containment unit is one
        iteration, not only the SAVEPOINT body). A release-time flush failure
        counts as an error, not a success. Dead-connection errors
        (``connection_invalidated`` / ``DisconnectionError``, see
        :func:`is_connection_dead`) are *not* isolated — they re-raise so
        ``get_db_session`` can trip the process-global circuit breaker when the
        exception class is in :data:`CONNECTION_ERROR_TYPES`.

        Status writes go through ``MediaBuyRepository.update_status`` (tenant-scoped
        seam + revision / ``confirmed_at`` bookkeeping from upstream), not a raw
        ``media_buy.status`` assignment. Self-transitions are already refused by
        ``_compute_new_status`` (``if target == current: return None``), so this
        method does not re-check ``new_status != media_buy.status``.

        A flush failure at SAVEPOINT release (for example a ``DataError`` /
        ``IntegrityError`` raised while releasing after an on-enum write) counts
        as an isolated error, not a flip — the same path as a body-time DB error.

        Returns :class:`_BuyOutcome` — ``FLIPPED`` / ``ISOLATED`` / ``NOOP``.
        """
        tenant_id = ""
        principal_id = ""
        media_buy_id = ""
        new_status: PersistedMediaBuyStatus | None = None
        old_status: str | None = None

        try:
            tenant_id = media_buy.tenant_id
            principal_id = media_buy.principal_id
            media_buy_id = media_buy.media_buy_id
            with session.begin_nested():
                new_status = self._compute_new_status(media_buy, now, session)
                if new_status:
                    old_status = media_buy.status
                    # The sweep is deliberately cross-tenant, but the repository is
                    # tenant-scoped, so build it from this row's own tenant. That
                    # keeps every write inside the isolation the class enforces
                    # rather than widening it with a cross-tenant write method.
                    updated = MediaBuyRepository(session, tenant_id).update_status(
                        media_buy_id,
                        new_status,
                        # The sweep does not itself commit anything -- commitment
                        # happened earlier, at the synchronous create or at approval,
                        # and confirmed_at is write-once so a stamped row is untouched.
                        # ACTIVE is passed as committing anyway, and the PIN is the
                        # reason: create-media-buy-response.json @ 3.1.1 constrains
                        # confirmed_at in exactly one direction -- a null value forbids
                        # status "active". This sweep is the last writer before a buyer
                        # can observe that combination, so it must not be able to
                        # produce it. Any row reaching ACTIVE unstamped is already a
                        # defect upstream; stamping here keeps the defect from becoming
                        # a schema-invalid document on the wire.
                        seller_committed=new_status == PersistedMediaBuyStatus.ACTIVE,
                    )
                    if updated is None:
                        # Unreachable: media_buy_id is the sole primary key and the
                        # row is already loaded in this transaction, so the
                        # tenant-filtered re-fetch cannot miss. Never fall through
                        # silently — a sweep must not report an update it did not make.
                        logger.error(
                            f"Media buy {media_buy_id} vanished from its own tenant "
                            f"{tenant_id!r} mid-sweep; status left at {old_status}"
                        )
                        old_status = None
        except Exception as exc:
            if is_connection_dead(exc):
                raise
            try:
                logger.error(
                    f"Error updating media buy status "
                    f"(tenant_id={tenant_id}, principal_id={principal_id}, "
                    f"media_buy_id={media_buy_id}): {exc}",
                    exc_info=True,
                )
                record_scheduler_isolation_error(
                    scheduler="media_buy_status",
                    tenant_id=tenant_id,
                    error_type=_classify_scheduler_error(exc),
                )
            except Exception as handler_exc:
                logger.error(
                    f"Status isolation error handler failed (media_buy_id={media_buy_id}): {handler_exc}",
                    exc_info=True,
                )
            return _BuyOutcome.ISOLATED

        if not new_status or old_status is None:
            return _BuyOutcome.NOOP
        logger.info(f"Updated media buy {media_buy_id} status: {old_status} -> {new_status}")
        return _BuyOutcome.FLIPPED

    async def _update_statuses(self) -> None:
        """Check and update media buy statuses based on flight dates.

        Per-buy work runs inside a status-local ``with session.begin_nested():``
        SAVEPOINT (precedent: ``src/core/tools/creatives/_sync.py``) so a DB
        error on one buy rolls back only that buy; siblings still reach the
        terminal ``session.commit()`` — see :meth:`_process_one_media_buy` for
        the pre-capture / isolation / dead-connection contract. The
        ``updated_count``/``errors`` tally is applied only *after* the SAVEPOINT
        releases cleanly. Batch summary runs in an outer ``finally`` so a
        dead-connection re-raise mid-loop still emits the tally, and the
        summary keys severity on whether the loop ``reached_end`` (successful
        terminal path), not on a derived ``committed`` flag.
        """
        now = datetime.now(UTC)
        updated_count = 0
        errors = 0
        seen = 0
        reached_end = False

        try:
            with get_db_session() as session:
                # Find media buys that need status updates (cross-tenant scheduler query)
                # 1. pending_start (or legacy pending_activation/scheduled) -> active if start_time passed
                # 2. active -> should become completed if end_time passed
                media_buys = MediaBuyRepository.get_all_by_statuses(
                    session, _ACTIVATABLE_STATUSES | {PersistedMediaBuyStatus.ACTIVE}
                )

                for media_buy in media_buys:
                    seen += 1
                    result = self._process_one_media_buy(media_buy, now, session)
                    if result is _BuyOutcome.FLIPPED:
                        updated_count += 1
                    elif result is _BuyOutcome.ISOLATED:
                        errors += 1

                if updated_count > 0:
                    session.commit()
                reached_end = True

        except Exception as e:
            logger.error(f"Failed to update media buy statuses: {e}", exc_info=True)
        finally:
            self._log_batch_summary(
                seen=seen,
                updated_count=updated_count,
                errors=errors,
                reached_end=reached_end,
            )

    def _log_batch_summary(self, *, seen: int, updated_count: int, errors: int, reached_end: bool = True) -> None:
        """Suppress all-quiet ticks (60s cadence); WARNING on total-loss or mid-loop abort.

        Legitimate no-op flips do not count as failures, so the error tally
        stays visible without paging on a normal quiet tick. Called from an
        outer ``finally`` so the tally survives both a failed terminal commit
        and a dead-connection re-raise mid-loop. When the loop did not reach
        the end and pending flips exist, do not claim them as "updated" —
        report pending flips lost instead. ``reached_end`` is false on escape
        or commit failure paths so a zero-flip worst tick (every visited buy
        failed, including an escaping dead connection) still WARNING rather
        than INFO.
        """
        if not reached_end and updated_count:
            summary = (
                f"{STATUS_BATCH_SUMMARY_PREFIX}: 0 persisted ({updated_count} pending flips lost), {errors} errors"
            )
            logger.warning(summary)
            return
        if not (updated_count or errors):
            return
        summary = f"{STATUS_BATCH_SUMMARY_PREFIX}: {updated_count} updated, {errors} errors"
        if errors == seen or not reached_end:
            logger.warning(summary)
        else:
            logger.info(summary)

    def _compute_new_status(self, media_buy: MediaBuy, now: datetime, session) -> PersistedMediaBuyStatus | None:
        """The status this sweep should write, or ``None`` to leave the row alone.

        The flight-window rule itself lives in
        ``src.core.tools._media_buy_transitions.resolve_flight_window_status`` — one
        domain owner shared with the two admin approval paths. What stays here is the
        part that is genuinely the SCHEDULER's: this runs unattended over every buy,
        so it moves only buys that are waiting to start, and never writes a status the
        row already has.
        """
        target = resolve_flight_window_status(
            media_buy,
            now=now,
            creatives_approved=self._are_creatives_approved(media_buy, session),
        )
        if target is None:
            return None  # No flight window — this sweep has no opinion.

        current = media_buy.status
        if target == current:
            return None

        if target == PersistedMediaBuyStatus.COMPLETED:
            return target

        # Activation only, and only out of a pre-serving state. An unattended sweep
        # must not resurrect a buy a seller deliberately paused, rejected or canceled,
        # and it must not push a buy BACK to scheduled once it is serving.
        if target == PersistedMediaBuyStatus.ACTIVE and current in _ACTIVATABLE_STATUSES:
            return target

        return None

    def _are_creatives_approved(self, media_buy: MediaBuy, session) -> bool:
        """Check if all creatives for a media buy are approved.

        Returns:
            True if no creatives assigned OR all creatives are approved.
        """
        # Get creative assignments for this media buy
        stmt = select(CreativeAssignment).filter_by(tenant_id=media_buy.tenant_id, media_buy_id=media_buy.media_buy_id)
        assignments = session.scalars(stmt).all()

        if not assignments:
            # No creatives assigned - can activate (some campaigns run without creatives initially)
            return True

        # Get all creative IDs
        creative_ids = list({a.creative_id for a in assignments})

        # Check creative statuses
        creative_stmt = select(Creative).where(
            Creative.tenant_id == media_buy.tenant_id,
            Creative.creative_id.in_(creative_ids),
        )
        creatives = session.scalars(creative_stmt).all()

        # All creatives must be approved
        for creative in creatives:
            if creative.status != "approved":
                return False

        return True


# Global singleton instance
_scheduler: MediaBuyStatusScheduler | None = None


def get_media_buy_status_scheduler() -> MediaBuyStatusScheduler:
    """Get or create the global media buy status scheduler instance."""
    global _scheduler
    if _scheduler is None:
        _scheduler = MediaBuyStatusScheduler()
    return _scheduler


async def start_media_buy_status_scheduler() -> None:
    """Start the global media buy status scheduler."""
    scheduler = get_media_buy_status_scheduler()
    await scheduler.start()


async def stop_media_buy_status_scheduler() -> None:
    """Stop the global media buy status scheduler."""
    scheduler = get_media_buy_status_scheduler()
    await scheduler.stop()
