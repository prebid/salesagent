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

from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import Creative, CreativeAssignment, MediaBuy
from src.core.database.repositories import MediaBuyRepository
from src.core.database.repositories.workflow import WorkflowRepository
from src.core.media_buy_flight import resolve_flight_window_utc

logger = logging.getLogger(__name__)

# Configurable via env var - default 60 seconds
STATUS_CHECK_INTERVAL_SECONDS = int(os.getenv("MEDIA_BUY_STATUS_CHECK_INTERVAL") or "60")


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
                # Resume any approval stranded mid-finalize by a crash (#1637). Runs
                # after the flight-window sweep; its own errors never abort the loop.
                await self._reconcile_finalizing_buys()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in media buy status scheduler: {e}", exc_info=True)
            finally:
                # Wait before next check
                await asyncio.sleep(STATUS_CHECK_INTERVAL_SECONDS)

    async def _update_statuses(self) -> None:
        """Check and update media buy statuses based on flight dates."""
        now = datetime.now(UTC)
        updated_count = 0

        try:
            with get_db_session() as session:
                # Find media buys that need status updates (cross-tenant scheduler query)
                # 1. pending_start (or legacy pending_activation/scheduled) -> active if start_time passed
                # 2. active -> should become completed if end_time passed
                media_buys = MediaBuyRepository.get_all_by_statuses(
                    session, ["pending_start", "pending_activation", "scheduled", "active"]
                )

                for media_buy in media_buys:
                    old_status = media_buy.status
                    # Compute the target UNDER the row lock. The rows above were
                    # loaded WITHOUT a lock, so their flight window / status may be
                    # stale: apply_computed_status_transition takes FOR UPDATE,
                    # refreshes every lifecycle input, and only THEN runs this
                    # callback on the committed row. That closes the lost-update
                    # race where a concurrent end_time extension (status still
                    # active) would let a pre-lock "completed" decision win. The
                    # seam bumps the AdCP 3.1.0-beta.3 revision + stamps
                    # confirmed_at on any real transition. #1544.
                    MediaBuyRepository.apply_computed_status_transition(
                        media_buy, lambda mb: self._compute_new_status(mb, now, session)
                    )
                    if media_buy.status != old_status:
                        updated_count += 1
                        logger.info(
                            f"Updated media buy {media_buy.media_buy_id} status: {old_status} -> {media_buy.status}"
                        )

                if updated_count > 0:
                    session.commit()
                    logger.info(f"Updated {updated_count} media buy status(es)")

        except Exception as e:
            logger.error(f"Failed to update media buy statuses: {e}", exc_info=True)

    async def _reconcile_finalizing_buys(self) -> None:
        """Re-drive media buys stranded in ``finalizing`` by a mid-finalize crash.

        The approval finalizer claims a buy into ``finalizing`` (with a phase-2 lease)
        and commits BEFORE the external adapter runs (#1637). If the process dies (or
        the adapter raises unexpectedly) before the serving-status transition + step
        terminalization, the buy is left in ``finalizing``. This pass scans for
        RECOVERABLE strandings only — lease absent/expired (an unexpired lease means
        a live worker owns phase 2) and ``finalize_recovery_mode IS NULL`` (buys
        flagged ``manual_required`` are never re-touched: no hot loop) — and hands
        each to ``resume_finalizing_media_buy``, whose lease CAS is the authoritative
        single-winner gate and whose disposition check fail-closes non-replayable
        adapters. Each buy is reconciled in its own transaction so one failure never
        blocks the others.
        """
        from functools import partial

        from src.admin.services.media_buy_completion import resume_finalizing_media_buy
        from src.core.tools.media_buy_create import adapter_supports_full_create_replay, execute_approved_media_buy

        try:
            with get_db_session() as session:
                stranded = [
                    (mb.tenant_id, mb.media_buy_id)
                    for mb in MediaBuyRepository.get_finalizing_recoverable(session, datetime.now(UTC))
                ]
        except Exception as e:
            logger.error(f"Failed to scan for stranded finalizing media buys: {e}", exc_info=True)
            return

        for tenant_id, media_buy_id in stranded:
            try:
                with get_db_session() as session:
                    wf_repo = WorkflowRepository(session, tenant_id)
                    mapping = wf_repo.get_latest_mapping_for_object("media_buy", media_buy_id)
                    step = wf_repo.get_by_step_id(mapping.step_id) if mapping else None
                    step_id = step.step_id if step else None
                    step_data = (
                        {
                            "step_id": step.step_id,
                            "context_id": step.context_id,
                            "tool_name": step.tool_name,
                            "request_data": step.request_data or {},
                        }
                        if step
                        else None
                    )
                    outcome, _ = resume_finalizing_media_buy(
                        session,
                        tenant_id,
                        media_buy_id=media_buy_id,
                        step_id=step_id,
                        step_data=step_data,
                        run_adapter=partial(execute_approved_media_buy, media_buy_id, tenant_id),
                        adapter_supports_replay=partial(adapter_supports_full_create_replay, media_buy_id, tenant_id),
                    )
                logger.info(f"Reconciled stranded finalizing media buy {media_buy_id}: {outcome}")
            except Exception as e:
                logger.error(f"Failed to reconcile finalizing media buy {media_buy_id}: {e}", exc_info=True)

    def _compute_new_status(self, media_buy: MediaBuy, now: datetime, session) -> str | None:
        """Compute the new status for a media buy based on flight dates.

        Returns:
            New status string if change needed, None otherwise.
        """
        # Resolve the effective UTC flight window (shared with the admin approve
        # route and creative-review path — see resolve_flight_window_utc / #1544).
        start_time, end_time = resolve_flight_window_utc(media_buy)

        if start_time is None:
            return None  # No start time defined
        if end_time is None:
            return None  # No end time defined

        current_status = media_buy.status

        # Check if campaign has ended
        if now > end_time:
            if current_status != "completed":
                return "completed"
            return None

        # Check if campaign should be active
        if now >= start_time:
            if current_status in ["pending_start", "pending_activation", "scheduled"]:
                # Before activating, verify creatives are approved (for pending_start/pending_activation)
                if current_status in ["pending_start", "pending_activation"]:
                    if self._are_creatives_approved(media_buy, session):
                        return "active"
                    # Creatives not approved yet - stay pending
                    return None
                else:
                    # scheduled -> active (no creative check needed, already validated)
                    return "active"

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
