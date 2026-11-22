"""Integration tests for MediaBuyStatusScheduler.

These tests verify that the scheduler correctly transitions media buy statuses
based on flight dates:
- pending_activation -> active (when start_time passed and creatives approved)
- scheduled -> active (when start_time passed)
- active -> completed (when end_time passed)

Uses real PostgreSQL database via integration_db fixture.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import text

from src.core.database.models import MediaBuy, PersistedMediaBuyStatus, Principal, Tenant
from src.core.database.repositories import MediaBuyRepository
from src.services.media_buy_status_scheduler import MediaBuyStatusScheduler
from tests.factories import CreativeAssignmentFactory, CreativeFactory
from tests.helpers.media_buy_write_seam import (
    assert_status_move_carried_bookkeeping,
    read_media_buy_state,
)
from tests.helpers.metrics import assert_isolation_delta
from tests.helpers.scheduler_isolation import (
    INVALIDATED_ESCAPE_ERROR_TYPES,
    assert_isolated_buy_logged,
    assert_propagated_session_error,
    assert_tick_summary,
    bare_disconnection_error,
    fail_one,
    flip_lines,
    invalidated_dbapi_error,
    run_status_tick,
    seed_active_expired_buys,
    seed_status_buys,
    server_never_answered_error,
    statement_failure,
    status_party,
)


def _assign_creative(tenant: Tenant, principal: Principal, media_buy: MediaBuy, *, status: str) -> None:
    """Assign one creative in *status* to *media_buy*."""
    creative = CreativeFactory(tenant=tenant, principal=principal, status=status)
    CreativeAssignmentFactory(creative=creative, media_buy=media_buy)


# =============================================================================
# Test: scheduled -> active (when start time has passed)
# =============================================================================


@pytest.mark.requires_db
@pytest.mark.asyncio
async def test_scheduled_transitions_to_active_when_start_time_passed(integration_db):
    """Media buy in 'scheduled' status should transition to 'active' when start_time passes."""
    tenant_id = "tenant_scheduled_active"

    # Create media buy with start_time in the past
    past_start = datetime.now(UTC) - timedelta(hours=1)
    future_end = datetime.now(UTC) + timedelta(days=7)

    media_buy_id = "mb_scheduled_to_active"
    with status_party(tenant_id) as (tenant, principal):
        # confirmed_at=None: this test grades the repository's own confirmation stamp.
        seed_status_buys(
            tenant,
            principal,
            [media_buy_id],
            status="scheduled",
            start_time=past_start,
            end_time=future_end,
            confirmed_at=None,
        )

    # Verify initial status
    before = read_media_buy_state(tenant_id, media_buy_id)
    assert before.status == "scheduled"
    assert before.confirmed_at is None, "fixture must start with an unstamped confirmation instant"

    # Run scheduler
    scheduler = MediaBuyStatusScheduler()
    await scheduler._update_statuses()

    # Verify status changed to active
    after = read_media_buy_state(tenant_id, media_buy_id)
    assert after.status == "active"

    # The sweep is a mutation of the buy, so it must carry the buy's mutation
    # bookkeeping with it: MediaBuyRepository.update_status bumps `revision` (the
    # buyer's optimistic-concurrency token, which MUST strictly increase on every
    # mutation) and stamps `confirmed_at` on the first committed status. A sweep
    # that writes media_buy.status directly moves the buy without either.
    # "active" is a seller-confirmed status, so this transition is the instant the buy
    # reads as committed — hence confirms=True.
    assert_status_move_carried_bookkeeping(
        before, after, expected_status="active", confirms=True, subject=f"scheduler sweep of {media_buy_id}"
    )


@pytest.mark.requires_db
@pytest.mark.asyncio
async def test_scheduled_stays_scheduled_when_start_time_not_passed(integration_db):
    """Media buy in 'scheduled' status should stay 'scheduled' if start_time is in the future."""
    tenant_id = "tenant_scheduled_stays"

    # Create media buy with start_time in the future
    future_start = datetime.now(UTC) + timedelta(hours=1)
    future_end = datetime.now(UTC) + timedelta(days=7)

    media_buy_id = "mb_scheduled_stays"
    with status_party(tenant_id) as (tenant, principal):
        seed_status_buys(
            tenant, principal, [media_buy_id], status="scheduled", start_time=future_start, end_time=future_end
        )

    # Verify initial status
    assert read_media_buy_state(tenant_id, media_buy_id).status == "scheduled"

    # Run scheduler
    scheduler = MediaBuyStatusScheduler()
    await scheduler._update_statuses()

    # Verify status unchanged
    assert read_media_buy_state(tenant_id, media_buy_id).status == "scheduled"


# =============================================================================
# Test: pending_activation -> active (when start time passed AND creatives approved)
# =============================================================================


@pytest.mark.requires_db
@pytest.mark.asyncio
async def test_pending_activation_transitions_to_active_with_approved_creatives(integration_db):
    """Media buy in 'pending_activation' should transition to 'active' when start_time passes and creatives approved."""
    tenant_id = "tenant_pending_active"

    # Create media buy with start_time in the past
    past_start = datetime.now(UTC) - timedelta(hours=1)
    future_end = datetime.now(UTC) + timedelta(days=7)

    media_buy_id = "mb_pending_to_active"
    with status_party(tenant_id) as (tenant, principal):
        (media_buy,) = seed_status_buys(
            tenant, principal, [media_buy_id], status="pending_activation", start_time=past_start, end_time=future_end
        )
        # Create an approved creative and assign it to the media buy
        _assign_creative(tenant, principal, media_buy, status="approved")

    # Verify initial status
    assert read_media_buy_state(tenant_id, media_buy_id).status == "pending_activation"

    # Run scheduler
    scheduler = MediaBuyStatusScheduler()
    await scheduler._update_statuses()

    # Verify status changed to active
    assert read_media_buy_state(tenant_id, media_buy_id).status == "active"


@pytest.mark.requires_db
@pytest.mark.asyncio
async def test_pending_activation_stays_pending_with_unapproved_creatives(integration_db):
    """Media buy in 'pending_activation' should stay pending if creatives are not approved."""
    tenant_id = "tenant_pending_unapproved"

    # Create media buy with start_time in the past
    past_start = datetime.now(UTC) - timedelta(hours=1)
    future_end = datetime.now(UTC) + timedelta(days=7)

    media_buy_id = "mb_pending_unapproved"
    with status_party(tenant_id) as (tenant, principal):
        (media_buy,) = seed_status_buys(
            tenant, principal, [media_buy_id], status="pending_activation", start_time=past_start, end_time=future_end
        )
        # Create a pending creative and assign it
        _assign_creative(tenant, principal, media_buy, status="pending_review")

    # Verify initial status
    assert read_media_buy_state(tenant_id, media_buy_id).status == "pending_activation"

    # Run scheduler
    scheduler = MediaBuyStatusScheduler()
    await scheduler._update_statuses()

    # Verify status unchanged (creatives not approved)
    assert read_media_buy_state(tenant_id, media_buy_id).status == "pending_activation"


@pytest.mark.requires_db
@pytest.mark.asyncio
async def test_pending_activation_activates_without_creatives(integration_db):
    """Media buy in 'pending_activation' with no creatives should transition to 'active'."""
    tenant_id = "tenant_pending_no_creatives"

    # Create media buy with start_time in the past - NO creatives assigned
    past_start = datetime.now(UTC) - timedelta(hours=1)
    future_end = datetime.now(UTC) + timedelta(days=7)

    media_buy_id = "mb_pending_no_creatives"
    with status_party(tenant_id) as (tenant, principal):
        seed_status_buys(
            tenant, principal, [media_buy_id], status="pending_activation", start_time=past_start, end_time=future_end
        )

    # Verify initial status
    assert read_media_buy_state(tenant_id, media_buy_id).status == "pending_activation"

    # Run scheduler
    scheduler = MediaBuyStatusScheduler()
    await scheduler._update_statuses()

    # Verify status changed to active (no creatives = nothing to block)
    assert read_media_buy_state(tenant_id, media_buy_id).status == "active"


@pytest.mark.requires_db
@pytest.mark.asyncio
async def test_pending_activation_stays_pending_when_start_time_not_passed(integration_db):
    """Media buy in 'pending_activation' should stay pending if start_time is in the future."""
    tenant_id = "tenant_pending_future"

    # Create media buy with start_time in the future
    future_start = datetime.now(UTC) + timedelta(hours=1)
    future_end = datetime.now(UTC) + timedelta(days=7)

    media_buy_id = "mb_pending_future"
    with status_party(tenant_id) as (tenant, principal):
        (media_buy,) = seed_status_buys(
            tenant, principal, [media_buy_id], status="pending_activation", start_time=future_start, end_time=future_end
        )
        # Create approved creative
        _assign_creative(tenant, principal, media_buy, status="approved")

    # Verify initial status
    assert read_media_buy_state(tenant_id, media_buy_id).status == "pending_activation"

    # Run scheduler
    scheduler = MediaBuyStatusScheduler()
    await scheduler._update_statuses()

    # Verify status unchanged (start time not passed)
    assert read_media_buy_state(tenant_id, media_buy_id).status == "pending_activation"


# =============================================================================
# Test: active -> completed (when end time has passed)
# =============================================================================


@pytest.mark.requires_db
@pytest.mark.asyncio
async def test_active_transitions_to_completed_when_end_time_passed(integration_db):
    """Media buy in 'active' status should transition to 'completed' when end_time passes."""
    tenant_id = "tenant_active_completed"

    media_buy_id = "mb_active_to_completed"
    seed_active_expired_buys(tenant_id, [media_buy_id])

    # Verify initial status
    assert read_media_buy_state(tenant_id, media_buy_id).status == "active"

    # Run scheduler
    scheduler = MediaBuyStatusScheduler()
    await scheduler._update_statuses()

    # Verify status changed to completed
    assert read_media_buy_state(tenant_id, media_buy_id).status == "completed"


@pytest.mark.requires_db
@pytest.mark.asyncio
async def test_active_stays_active_when_end_time_not_passed(integration_db):
    """Media buy in 'active' status should stay 'active' if end_time is in the future."""
    tenant_id = "tenant_active_stays"

    # Create media buy with end_time in the future
    past_start = datetime.now(UTC) - timedelta(days=1)
    future_end = datetime.now(UTC) + timedelta(days=7)

    media_buy_id = "mb_active_stays"
    with status_party(tenant_id) as (tenant, principal):
        seed_status_buys(tenant, principal, [media_buy_id], status="active", start_time=past_start, end_time=future_end)

    # Verify initial status
    assert read_media_buy_state(tenant_id, media_buy_id).status == "active"

    # Run scheduler
    scheduler = MediaBuyStatusScheduler()
    await scheduler._update_statuses()

    # Verify status unchanged
    assert read_media_buy_state(tenant_id, media_buy_id).status == "active"


# =============================================================================
# Test: Multiple media buys in single run
# =============================================================================


@pytest.mark.requires_db
@pytest.mark.asyncio
async def test_scheduler_updates_multiple_media_buys(integration_db):
    """Scheduler should update multiple media buys in a single run."""
    tenant_id = "tenant_multi"

    now = datetime.now(UTC)

    with status_party(tenant_id) as (tenant, principal):
        # Media buy 1: scheduled -> active
        seed_status_buys(
            tenant,
            principal,
            ["mb_multi_1"],
            status="scheduled",
            start_time=now - timedelta(hours=1),
            end_time=now + timedelta(days=7),
        )
        # Media buy 2: active -> completed
        seed_status_buys(
            tenant,
            principal,
            ["mb_multi_2"],
            status="active",
            start_time=now - timedelta(days=7),
            end_time=now - timedelta(hours=1),
        )
        # Media buy 3: scheduled but start_time in future (no change)
        seed_status_buys(
            tenant,
            principal,
            ["mb_multi_3"],
            status="scheduled",
            start_time=now + timedelta(hours=1),
            end_time=now + timedelta(days=7),
        )

    # Verify initial statuses
    assert read_media_buy_state(tenant_id, "mb_multi_1").status == "scheduled"
    assert read_media_buy_state(tenant_id, "mb_multi_2").status == "active"
    assert read_media_buy_state(tenant_id, "mb_multi_3").status == "scheduled"

    # Run scheduler
    scheduler = MediaBuyStatusScheduler()
    await scheduler._update_statuses()

    # Verify expected transitions
    assert read_media_buy_state(tenant_id, "mb_multi_1").status == "active"
    assert read_media_buy_state(tenant_id, "mb_multi_2").status == "completed"
    assert read_media_buy_state(tenant_id, "mb_multi_3").status == "scheduled"  # No change


# =============================================================================
# Test: Per-buy failure isolation (#1714)
# =============================================================================


@pytest.mark.requires_db
@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["division", "statement_timeout"])
@pytest.mark.parametrize("raiser_slot", [0, 1, 2])
async def test_raising_buy_does_not_abort_remaining_status_flips(integration_db, fault, raiser_slot):
    """A DB error on one buy must not discard sibling status flips.

    Uses a SAVEPOINT-backed per-buy body. Division by zero and a statement
    timeout (SQLSTATE 57014) are both answers the server gave, so the buy is
    isolated, its siblings still flip, and the fail-fast stays unarmed.
    """
    tenant_id = f"iso_{fault[:3]}_{raiser_slot}"

    buy_ids = [
        f"mb_{fault[:3]}_{raiser_slot}_a",
        f"mb_{fault[:3]}_{raiser_slot}_b",
        f"mb_{fault[:3]}_{raiser_slot}_c",
    ]
    bad_buy_id = buy_ids[raiser_slot]
    good_ids = [mid for mid in buy_ids if mid != bad_buy_id]

    principal_id = seed_active_expired_buys(tenant_id, buy_ids)

    for mid in buy_ids:
        assert read_media_buy_state(tenant_id, mid).status == "active"

    scheduler = MediaBuyStatusScheduler()
    processed: list[str] = []
    raiser = (lambda session: session.execute(text("SELECT 1/0"))) if fault == "division" else statement_failure()
    compute = fail_one(bad_buy_id, raiser, otherwise=scheduler._compute_new_status)

    with (
        assert_isolation_delta(1, tenant_id=tenant_id, error_type="db_error"),
        assert_propagated_session_error(trips_fail_fast=False),
    ):
        records = await run_status_tick(scheduler, compute=compute, visited=processed)

    assert set(processed) == set(buy_ids)
    for mid in good_ids:
        assert read_media_buy_state(tenant_id, mid).status == "completed"
    assert read_media_buy_state(tenant_id, bad_buy_id).status == "active"

    assert_isolated_buy_logged(records, tenant_id=tenant_id, principal_id=principal_id, media_buy_id=bad_buy_id)
    assert_tick_summary(records, "INFO", "2 updated, 1 errors")


@pytest.mark.requires_db
@pytest.mark.asyncio
async def test_savepoint_release_failure_not_counted_processed_and_warns(integration_db):
    """BLOCKER oracle: on-enum write + flush failure at SAVEPOINT release.

    ``PersistedMediaBuyStatus.parse`` refuses off-enum values before any flush, so
    an over-length status string no longer reaches release. Keep the injected
    status inside the pinned enum, let ``update_status`` succeed, then dirty a
    non-status ``String(255)`` column so the RELEASE flush raises
    ``DataError`` / truncation — grading the release path, not parse.
    """
    from src.core.database.models import PersistedMediaBuyStatus
    from src.core.database.repositories.media_buy import MediaBuyRepository

    tenant_id = "tenant_isolation_release_fail_1714"

    buy_ids = ["mb_rel_a", "mb_rel_b", "mb_rel_c"]
    seed_active_expired_buys(tenant_id, buy_ids)

    scheduler = MediaBuyStatusScheduler()
    real_update = MediaBuyRepository.update_status

    def _update_then_poison_order_name(self, media_buy_id, status, **kwargs):
        updated = real_update(self, media_buy_id, status, **kwargs)
        if updated is not None:
            # order_name is String(255); over-length dirties the unit so the
            # SAVEPOINT RELEASE flush fails after the on-enum status write.
            updated.order_name = "x" * 300
        return updated

    with patch.object(MediaBuyRepository, "update_status", _update_then_poison_order_name):
        records = await run_status_tick(scheduler, compute=PersistedMediaBuyStatus.COMPLETED)

    for mid in buy_ids:
        assert read_media_buy_state(tenant_id, mid).status == "active"

    assert_tick_summary(records, "WARNING", "0 updated, 3 errors")
    assert flip_lines(records) == []


_PROPAGATED_SESSION_CASES = [
    *((exc_type.__name__, invalidated_dbapi_error(exc_type), False) for exc_type in INVALIDATED_ESCAPE_ERROR_TYPES),
    ("server_never_answered", server_never_answered_error(), True),
    ("DisconnectionError", bare_disconnection_error(), False),
]


@pytest.mark.requires_db
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "exc", "trips_fail_fast"), [pytest.param(*case, id=case[0]) for case in _PROPAGATED_SESSION_CASES]
)
async def test_propagated_session_error_stops_the_tick(integration_db, kind, exc, trips_fail_fast):
    """A session-level database error stops the tick.

    A discarded connection and a dropped connection leave the fail-fast unarmed.
    An OperationalError with no SQLSTATE is a connect the server never answered,
    and that one trips it. Later buys in the tick do not run.
    """
    from tests.helpers.scheduler_isolation import assert_propagated_failure_stops_batch

    tenant_id = f"iso_{kind}_{int(trips_fail_fast)}"
    buy_ids = [f"mb_{kind}_a", f"mb_{kind}_b"]

    seed_active_expired_buys(tenant_id, buy_ids)

    await assert_propagated_failure_stops_batch(
        MediaBuyStatusScheduler(),
        exc,
        tenant_id=tenant_id,
        trips_fail_fast=trips_fail_fast,
        buy_count=len(buy_ids),
    )


# =============================================================================
# Test: Edge cases
# =============================================================================


@pytest.mark.requires_db
@pytest.mark.asyncio
async def test_scheduler_uses_start_date_when_start_time_not_set(integration_db):
    """Scheduler should fall back to start_date/end_date when start_time/end_time are not set."""
    tenant_id = "tenant_date_fallback"

    # Create media buy with start_date in the past but no start_time
    past_date = (datetime.now(UTC) - timedelta(days=1)).date()
    future_date = (datetime.now(UTC) + timedelta(days=7)).date()

    media_buy_id = "mb_date_fallback"
    with status_party(tenant_id) as (tenant, principal):
        seed_status_buys(
            tenant, principal, [media_buy_id], status="scheduled", start_date=past_date, end_date=future_date
        )

    # Verify initial status
    assert read_media_buy_state(tenant_id, media_buy_id).status == "scheduled"

    # Run scheduler - should use start_date for transition
    scheduler = MediaBuyStatusScheduler()
    await scheduler._update_statuses()

    # Verify status changed to active (using start_date fallback)
    assert read_media_buy_state(tenant_id, media_buy_id).status == "active"


@pytest.mark.requires_db
@pytest.mark.asyncio
async def test_scheduler_idempotent(integration_db):
    """Running scheduler multiple times should be idempotent."""
    tenant_id = "tenant_idempotent"

    # Create media buy that should transition
    past_start = datetime.now(UTC) - timedelta(hours=1)
    future_end = datetime.now(UTC) + timedelta(days=7)

    media_buy_id = "mb_idempotent"
    with status_party(tenant_id) as (tenant, principal):
        # confirmed_at=None: this test grades the repository's own confirmation stamp.
        seed_status_buys(
            tenant,
            principal,
            [media_buy_id],
            status="scheduled",
            start_time=past_start,
            end_time=future_end,
            confirmed_at=None,
        )

    before = read_media_buy_state(tenant_id, media_buy_id)
    assert before.confirmed_at is None, "fixture must start with an unstamped confirmation instant"

    scheduler = MediaBuyStatusScheduler()

    # Run scheduler first time
    await scheduler._update_statuses()
    after_first = read_media_buy_state(tenant_id, media_buy_id)
    assert after_first.status == "active"
    # The one sweep that DID transition carries the mutation bookkeeping.
    assert after_first.revision == before.revision + 1, (
        f"the transitioning sweep left revision at {after_first.revision} "
        f"(was {before.revision}); a status move must bump it by exactly 1"
    )
    assert after_first.confirmed_at is not None, (
        "the transitioning sweep moved the buy to the seller-confirmed status 'active' without stamping confirmed_at"
    )

    # Run scheduler second time - should be no-op
    await scheduler._update_statuses()
    assert read_media_buy_state(tenant_id, media_buy_id).status == "active"

    # Run scheduler third time - still no-op
    await scheduler._update_statuses()
    after_third = read_media_buy_state(tenant_id, media_buy_id)
    assert after_third.status == "active"

    # Idempotent means idempotent on the whole row, not just on status: two sweeps
    # that transition nothing must leave revision and confirmed_at exactly where the
    # first sweep left them. A `revision` that ticks on every sweep would invalidate
    # the buyer's concurrency token without anything having changed, and a re-stamped
    # confirmed_at would move the seller-commitment instant forward forever.
    assert after_third.revision == after_first.revision, (
        f"sweeps that transitioned nothing bumped revision {after_first.revision} -> "
        f"{after_third.revision}; only a real status move may bump it"
    )
    assert after_third.confirmed_at == after_first.confirmed_at, (
        f"sweeps that transitioned nothing moved confirmed_at {after_first.confirmed_at} -> "
        f"{after_third.confirmed_at}; the seller-commitment instant is written once"
    )


# =============================================================================
# Test: a write the repository did not make is never counted as one
# =============================================================================


@pytest.mark.requires_db
@pytest.mark.asyncio
async def test_sweep_does_not_count_a_write_the_repository_declined(integration_db):
    """A ``None`` from ``update_status`` is an isolated error, not a flip.

    ``MediaBuyRepository.update_status`` returns ``None`` when the row is not
    found within the repository's tenant. The sweep raises inside the savepoint
    so the buy rolls back, the batch summary reports ``0 updated, 1 errors``,
    and the isolation counter increments with ``error_type`` ``other``.
    """
    tenant_id = "tenant_declined_write"

    # A row the sweep WOULD transition: scheduled, start_time already passed.
    past_start = datetime.now(UTC) - timedelta(hours=1)
    future_end = datetime.now(UTC) + timedelta(days=7)

    media_buy_id = "mb_declined_write"
    with status_party(tenant_id) as (tenant, principal):
        principal_id = principal.principal_id
        seed_status_buys(
            tenant, principal, [media_buy_id], status="scheduled", start_time=past_start, end_time=future_end
        )

    before = read_media_buy_state(tenant_id, media_buy_id)
    assert before.status == "scheduled"

    scheduler = MediaBuyStatusScheduler()
    with (
        assert_isolation_delta(1, tenant_id=tenant_id, error_type="other"),
        patch.object(MediaBuyRepository, "update_status", return_value=None) as mock_update,
    ):
        records = await run_status_tick(scheduler)

    # The sweep did reach the write — otherwise the rest of this test is vacuous.
    # seller_committed=True is asserted, not tolerated: the pin forbids a null
    # confirmed_at on an "active" item, so a sweep that activated a row WITHOUT
    # claiming the commitment could put a schema-invalid document on the wire.
    mock_update.assert_called_once_with(media_buy_id, PersistedMediaBuyStatus.ACTIVE, seller_committed=True)

    assert flip_lines(records) == []
    assert_tick_summary(records, "WARNING", "0 updated, 1 errors")
    assert_isolated_buy_logged(records, tenant_id=tenant_id, principal_id=principal_id, media_buy_id=media_buy_id)

    # (c) The row itself is untouched — status, and the bookkeeping a real move carries.
    after = read_media_buy_state(tenant_id, media_buy_id)
    assert after == before, f"a declined write must leave the row exactly as it was: {before} -> {after}"
