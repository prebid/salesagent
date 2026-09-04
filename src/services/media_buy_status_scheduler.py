"""Media Buy Status Scheduler - Automatically transitions media buy statuses.

This scheduler runs in the background and updates media buy statuses based on
their flight dates:
- pending_activation -> active (when start_time has passed and creatives approved)
- scheduled -> active (when start_time has passed)
- active -> completed (when end_time has passed)

This ensures media buys don't get stuck in transitional states when approved
before their start date.
"""

import logging
from datetime import UTC, datetime

from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import Creative, CreativeAssignment, MediaBuy, PersistedMediaBuyStatus
from src.core.database.repositories import MediaBuyRepository
from src.core.tools._media_buy_transitions import resolve_flight_window_status
from src.services._scheduler_base import IntervalScheduler, make_singleton, parse_interval_env

logger = logging.getLogger(__name__)

# Configurable via env var - default 60 seconds
STATUS_CHECK_INTERVAL_SECONDS: int = parse_interval_env("MEDIA_BUY_STATUS_CHECK_INTERVAL", 60)


_ACTIVATABLE_STATUSES = frozenset(
    {
        PersistedMediaBuyStatus.PENDING_START,
        PersistedMediaBuyStatus.PENDING_ACTIVATION,
        PersistedMediaBuyStatus.SCHEDULED,
    }
)


class MediaBuyStatusScheduler(IntervalScheduler):
    """Scheduler for updating media buy statuses based on flight dates."""

    def __init__(self) -> None:
        super().__init__(interval_seconds=STATUS_CHECK_INTERVAL_SECONDS, name="media buy status")

    async def tick(self) -> None:
        """Check and update media buy statuses based on flight dates."""
        await self._update_statuses()

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
                    session, _ACTIVATABLE_STATUSES | {PersistedMediaBuyStatus.ACTIVE}
                )

                for media_buy in media_buys:
                    new_status = self._compute_new_status(media_buy, now, session)

                    if new_status and new_status != media_buy.status:
                        old_status = media_buy.status
                        # The sweep is deliberately cross-tenant, but the repository is
                        # tenant-scoped, so build it from this row's own tenant. That
                        # keeps every write inside the isolation the class enforces
                        # rather than widening it with a cross-tenant write method.
                        updated = MediaBuyRepository(session, media_buy.tenant_id).update_status(
                            media_buy.media_buy_id,
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
                                f"Media buy {media_buy.media_buy_id} vanished from its own tenant "
                                f"{media_buy.tenant_id!r} mid-sweep; status left at {old_status}"
                            )
                            continue
                        updated_count += 1
                        logger.info(f"Updated media buy {media_buy.media_buy_id} status: {old_status} -> {new_status}")

                if updated_count > 0:
                    session.commit()
                    logger.info(f"Updated {updated_count} media buy status(es)")

        except Exception as e:
            logger.error(f"Failed to update media buy statuses: {e}", exc_info=True)

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


# ---------------------------------------------------------------------------
# Global singleton — derived from the shared factory, not hand-rolled.
# make_singleton also registers the start/stop pair under the display name
# below, which is what the app entry point iterates (#1197 review).
# ---------------------------------------------------------------------------

(
    get_media_buy_status_scheduler,
    start_media_buy_status_scheduler,
    stop_media_buy_status_scheduler,
) = make_singleton(MediaBuyStatusScheduler, name="media buy status")
