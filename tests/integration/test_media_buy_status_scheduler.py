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

from src.core.database.database_session import get_db_session
from src.core.database.models import (
    Creative,
    CreativeAssignment,
    MediaBuy,
    PersistedMediaBuyStatus,
    Principal,
    Tenant,
)
from src.core.database.repositories import MediaBuyRepository
from src.services.media_buy_status_scheduler import (
    STATUS_BATCH_SUMMARY_PREFIX,
    MediaBuyStatusScheduler,
)
from tests.factories import stamp_format_identity
from tests.factories.principal import plaintext_token_for
from tests.helpers.media_buy_write_seam import (
    assert_status_move_carried_bookkeeping,
    read_media_buy_state,
)
from tests.helpers.metrics import counter_value
from tests.helpers.scheduler_isolation import (
    INVALIDATED_ESCAPE_ERROR_TYPES,
    assert_propagated_session_error,
    logged,
    run_status_tick,
    seed_active_expired_buys,
    statement_failure,
)
from tests.utils.database_helpers import add_product_prerequisites, vhost_for


def _create_test_tenant(tenant_id: str = "test_tenant") -> str:
    """Create a test tenant with required setup data."""
    with get_db_session() as session:
        tenant = Tenant(
            tenant_id=tenant_id,
            name="Test Tenant",
            subdomain="test",
            virtual_host=vhost_for(tenant_id),
            ad_server="mock",
            is_active=True,
        )
        session.add(tenant)

        # Required: the USD CurrencyLimit and the "all_inventory" PropertyTag.
        add_product_prerequisites(
            session,
            tenant_id,
            min_package_budget=1.00,
            max_daily_package_spend=100000.00,
            tag_description="All available inventory",
        )

        session.commit()

    return tenant_id


def _create_test_principal(tenant_id: str, principal_id: str = "test_principal") -> str:
    """Create a test principal."""
    with get_db_session() as session:
        principal = Principal.with_token(
            plaintext_token_for(principal_id),
            tenant_id=tenant_id,
            principal_id=principal_id,
            name="Test Principal",
            platform_mappings={"mock": {"advertiser_id": "mock_adv_123"}},
        )
        session.add(principal)
        session.commit()

    return principal_id


def _create_media_buy(
    tenant_id: str,
    principal_id: str,
    media_buy_id: str,
    status: str,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    start_date=None,
    end_date=None,
) -> str:
    """Create a media buy with specified status and flight dates.

    If start_date/end_date are not provided, they are derived from start_time/end_time.
    Pass explicit values to override this behavior.
    """
    # Derive start_date and end_date from start_time and end_time if not explicitly provided
    now = datetime.now(UTC)
    if start_date is None:
        start_date = start_time.date() if start_time else now.date()
    if end_date is None:
        end_date = end_time.date() if end_time else (now + timedelta(days=7)).date()

    with get_db_session() as session:
        media_buy = MediaBuy(
            tenant_id=tenant_id,
            principal_id=principal_id,
            media_buy_id=media_buy_id,
            status=status,
            start_date=start_date,
            end_date=end_date,
            start_time=start_time,
            end_time=end_time,
            order_name="Test Order",
            advertiser_name="Test Advertiser",
            raw_request={},  # Required field
        )
        session.add(media_buy)
        session.commit()

    return media_buy_id


def _create_creative(
    tenant_id: str,
    principal_id: str,
    creative_id: str,
    status: str = "approved",
) -> str:
    """Create a creative with specified status."""
    with get_db_session() as session:
        creative = Creative(
            tenant_id=tenant_id,
            principal_id=principal_id,
            creative_id=creative_id,
            name="Test Creative",
            status=status,
            data={"type": "display", "width": 300, "height": 250},
        )
        stamp_format_identity(creative, format_id="display_300x250")
        session.add(creative)
        session.commit()

    return creative_id


def _create_creative_assignment(
    tenant_id: str,
    media_buy_id: str,
    creative_id: str,
    principal_id: str = "test_principal",
) -> None:
    """Assign a creative to a media buy."""
    import uuid

    with get_db_session() as session:
        assignment = CreativeAssignment(
            assignment_id=f"assign_{uuid.uuid4().hex[:8]}",
            tenant_id=tenant_id,
            principal_id=principal_id,
            media_buy_id=media_buy_id,
            creative_id=creative_id,
            package_id="default_package",  # Required field
        )
        session.add(assignment)
        session.commit()


# =============================================================================
# Test: scheduled -> active (when start time has passed)
# =============================================================================


@pytest.mark.requires_db
@pytest.mark.asyncio
async def test_scheduled_transitions_to_active_when_start_time_passed(integration_db):
    """Media buy in 'scheduled' status should transition to 'active' when start_time passes."""
    tenant_id = _create_test_tenant("tenant_scheduled_active")
    principal_id = _create_test_principal(tenant_id)

    # Create media buy with start_time in the past
    past_start = datetime.now(UTC) - timedelta(hours=1)
    future_end = datetime.now(UTC) + timedelta(days=7)

    media_buy_id = _create_media_buy(
        tenant_id=tenant_id,
        principal_id=principal_id,
        media_buy_id="mb_scheduled_to_active",
        status="scheduled",
        start_time=past_start,
        end_time=future_end,
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
    tenant_id = _create_test_tenant("tenant_scheduled_stays")
    principal_id = _create_test_principal(tenant_id)

    # Create media buy with start_time in the future
    future_start = datetime.now(UTC) + timedelta(hours=1)
    future_end = datetime.now(UTC) + timedelta(days=7)

    media_buy_id = _create_media_buy(
        tenant_id=tenant_id,
        principal_id=principal_id,
        media_buy_id="mb_scheduled_stays",
        status="scheduled",
        start_time=future_start,
        end_time=future_end,
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
    tenant_id = _create_test_tenant("tenant_pending_active")
    principal_id = _create_test_principal(tenant_id)

    # Create media buy with start_time in the past
    past_start = datetime.now(UTC) - timedelta(hours=1)
    future_end = datetime.now(UTC) + timedelta(days=7)

    media_buy_id = _create_media_buy(
        tenant_id=tenant_id,
        principal_id=principal_id,
        media_buy_id="mb_pending_to_active",
        status="pending_activation",
        start_time=past_start,
        end_time=future_end,
    )

    # Create an approved creative and assign it to the media buy
    creative_id = _create_creative(
        tenant_id=tenant_id,
        principal_id=principal_id,
        creative_id="creative_approved",
        status="approved",
    )
    _create_creative_assignment(tenant_id, media_buy_id, creative_id)

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
    tenant_id = _create_test_tenant("tenant_pending_unapproved")
    principal_id = _create_test_principal(tenant_id)

    # Create media buy with start_time in the past
    past_start = datetime.now(UTC) - timedelta(hours=1)
    future_end = datetime.now(UTC) + timedelta(days=7)

    media_buy_id = _create_media_buy(
        tenant_id=tenant_id,
        principal_id=principal_id,
        media_buy_id="mb_pending_unapproved",
        status="pending_activation",
        start_time=past_start,
        end_time=future_end,
    )

    # Create a pending creative and assign it
    creative_id = _create_creative(
        tenant_id=tenant_id,
        principal_id=principal_id,
        creative_id="creative_pending",
        status="pending_approval",  # Not approved!
    )
    _create_creative_assignment(tenant_id, media_buy_id, creative_id)

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
    tenant_id = _create_test_tenant("tenant_pending_no_creatives")
    principal_id = _create_test_principal(tenant_id)

    # Create media buy with start_time in the past - NO creatives assigned
    past_start = datetime.now(UTC) - timedelta(hours=1)
    future_end = datetime.now(UTC) + timedelta(days=7)

    media_buy_id = _create_media_buy(
        tenant_id=tenant_id,
        principal_id=principal_id,
        media_buy_id="mb_pending_no_creatives",
        status="pending_activation",
        start_time=past_start,
        end_time=future_end,
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
    tenant_id = _create_test_tenant("tenant_pending_future")
    principal_id = _create_test_principal(tenant_id)

    # Create media buy with start_time in the future
    future_start = datetime.now(UTC) + timedelta(hours=1)
    future_end = datetime.now(UTC) + timedelta(days=7)

    media_buy_id = _create_media_buy(
        tenant_id=tenant_id,
        principal_id=principal_id,
        media_buy_id="mb_pending_future",
        status="pending_activation",
        start_time=future_start,
        end_time=future_end,
    )

    # Create approved creative
    creative_id = _create_creative(
        tenant_id=tenant_id,
        principal_id=principal_id,
        creative_id="creative_approved_future",
        status="approved",
    )
    _create_creative_assignment(tenant_id, media_buy_id, creative_id)

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
    tenant_id = _create_test_tenant("tenant_active_completed")
    principal_id = _create_test_principal(tenant_id)

    media_buy_id = "mb_active_to_completed"
    seed_active_expired_buys(
        _create_media_buy,
        tenant_id=tenant_id,
        principal_id=principal_id,
        buy_ids=[media_buy_id],
    )

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
    tenant_id = _create_test_tenant("tenant_active_stays")
    principal_id = _create_test_principal(tenant_id)

    # Create media buy with end_time in the future
    past_start = datetime.now(UTC) - timedelta(days=1)
    future_end = datetime.now(UTC) + timedelta(days=7)

    media_buy_id = _create_media_buy(
        tenant_id=tenant_id,
        principal_id=principal_id,
        media_buy_id="mb_active_stays",
        status="active",
        start_time=past_start,
        end_time=future_end,
    )

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
    tenant_id = _create_test_tenant("tenant_multi")
    principal_id = _create_test_principal(tenant_id)

    now = datetime.now(UTC)

    # Media buy 1: scheduled -> active
    _create_media_buy(
        tenant_id=tenant_id,
        principal_id=principal_id,
        media_buy_id="mb_multi_1",
        status="scheduled",
        start_time=now - timedelta(hours=1),
        end_time=now + timedelta(days=7),
    )

    # Media buy 2: active -> completed
    _create_media_buy(
        tenant_id=tenant_id,
        principal_id=principal_id,
        media_buy_id="mb_multi_2",
        status="active",
        start_time=now - timedelta(days=7),
        end_time=now - timedelta(hours=1),
    )

    # Media buy 3: scheduled but start_time in future (no change)
    _create_media_buy(
        tenant_id=tenant_id,
        principal_id=principal_id,
        media_buy_id="mb_multi_3",
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
    tenant_id = _create_test_tenant(f"iso_{fault[:3]}_{raiser_slot}")
    principal_id = _create_test_principal(tenant_id)

    buy_ids = [
        f"mb_{fault[:3]}_{raiser_slot}_a",
        f"mb_{fault[:3]}_{raiser_slot}_b",
        f"mb_{fault[:3]}_{raiser_slot}_c",
    ]
    bad_buy_id = buy_ids[raiser_slot]
    good_ids = [mid for mid in buy_ids if mid != bad_buy_id]

    seed_active_expired_buys(
        _create_media_buy,
        tenant_id=tenant_id,
        principal_id=principal_id,
        buy_ids=buy_ids,
    )

    for mid in buy_ids:
        assert read_media_buy_state(tenant_id, mid).status == "active"

    scheduler = MediaBuyStatusScheduler()
    real_compute = scheduler._compute_new_status
    processed: list[str] = []

    def _compute_with_raiser(media_buy, now_arg, session):
        processed.append(media_buy.media_buy_id)
        if media_buy.media_buy_id == bad_buy_id:
            if fault == "division":
                session.execute(text("SELECT 1/0"))
            else:
                raise statement_failure()
        return real_compute(media_buy, now_arg, session)

    metric_before = counter_value("media_buy_status", tenant_id, "db_error")
    records: list = []

    async def _run() -> None:
        with patch.object(scheduler, "_compute_new_status", side_effect=_compute_with_raiser):
            records.extend(await run_status_tick(scheduler))

    await assert_propagated_session_error(_run, trips_fail_fast=False)

    assert set(processed) == set(buy_ids)
    for mid in good_ids:
        assert read_media_buy_state(tenant_id, mid).status == "completed"
    assert read_media_buy_state(tenant_id, bad_buy_id).status == "active"

    errors = [record for record in records if record.levelname == "ERROR"]
    assert len(errors) == 1
    err_msg = errors[0].getMessage()
    assert f"tenant_id={tenant_id}" in err_msg
    assert f"principal_id={principal_id}" in err_msg
    assert f"media_buy_id={bad_buy_id}" in err_msg
    assert errors[0].exc_info

    assert logged(records, "INFO", STATUS_BATCH_SUMMARY_PREFIX) == [
        f"{STATUS_BATCH_SUMMARY_PREFIX}: 2 updated, 1 errors"
    ]
    assert logged(records, "WARNING", STATUS_BATCH_SUMMARY_PREFIX) == []

    assert counter_value("media_buy_status", tenant_id, "db_error") == metric_before + 1


@pytest.mark.requires_db
@pytest.mark.asyncio
async def test_all_failing_flips_emit_warning_summary(integration_db):
    """All-fail batch must WARNING the summary; INFO must not carry it."""
    tenant_id = _create_test_tenant("tenant_isolation_all_fail_1714")
    principal_id = _create_test_principal(tenant_id)

    buy_ids = ["mb_all_fail_a", "mb_all_fail_b", "mb_all_fail_c"]
    seed_active_expired_buys(
        _create_media_buy,
        tenant_id=tenant_id,
        principal_id=principal_id,
        buy_ids=buy_ids,
    )

    scheduler = MediaBuyStatusScheduler()

    def _always_fail(_media_buy, _now_arg, _session):
        raise ValueError("flip failed")

    metric_before = counter_value("media_buy_status", tenant_id, "other")

    with patch.object(scheduler, "_compute_new_status", side_effect=_always_fail):
        records = await run_status_tick(scheduler)

    warning_msgs = logged(records, "WARNING", STATUS_BATCH_SUMMARY_PREFIX)
    info_msgs = logged(records, "INFO", STATUS_BATCH_SUMMARY_PREFIX)
    assert warning_msgs == [f"{STATUS_BATCH_SUMMARY_PREFIX}: 0 updated, 3 errors"]
    assert info_msgs == []
    # Non-SQLAlchemyError → _classify_scheduler_error → "other"
    assert counter_value("media_buy_status", tenant_id, "other") == metric_before + 3


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

    tenant_id = _create_test_tenant("tenant_isolation_release_fail_1714")
    principal_id = _create_test_principal(tenant_id)

    buy_ids = ["mb_rel_a", "mb_rel_b", "mb_rel_c"]
    seed_active_expired_buys(
        _create_media_buy,
        tenant_id=tenant_id,
        principal_id=principal_id,
        buy_ids=buy_ids,
    )

    scheduler = MediaBuyStatusScheduler()
    real_update = MediaBuyRepository.update_status

    def _update_then_poison_order_name(self, media_buy_id, status, **kwargs):
        updated = real_update(self, media_buy_id, status, **kwargs)
        if updated is not None:
            # order_name is String(255); over-length dirties the unit so the
            # SAVEPOINT RELEASE flush fails after the on-enum status write.
            updated.order_name = "x" * 300
        return updated

    with (
        patch.object(scheduler, "_compute_new_status", return_value=PersistedMediaBuyStatus.COMPLETED),
        patch.object(MediaBuyRepository, "update_status", _update_then_poison_order_name),
    ):
        records = await run_status_tick(scheduler)

    for mid in buy_ids:
        assert read_media_buy_state(tenant_id, mid).status == "active"

    warning_msgs = logged(records, "WARNING", STATUS_BATCH_SUMMARY_PREFIX)
    assert warning_msgs == [f"{STATUS_BATCH_SUMMARY_PREFIX}: 0 updated, 3 errors"]
    assert logged(records, "INFO", STATUS_BATCH_SUMMARY_PREFIX) == []
    assert logged(records, "INFO", "Updated media buy ") == []


def _propagated_session_cases():
    for exc_type in INVALIDATED_ESCAPE_ERROR_TYPES:
        yield pytest.param(exc_type.__name__, False, id=exc_type.__name__)
    yield pytest.param("server_never_answered", True, id="server_never_answered")
    yield pytest.param("DisconnectionError", False, id="DisconnectionError")


def _propagated_session_error(kind: str):
    from tests.helpers.scheduler_isolation import (
        bare_disconnection_error,
        invalidated_dbapi_error,
        server_never_answered_error,
    )

    if kind == "server_never_answered":
        return server_never_answered_error()
    if kind == "DisconnectionError":
        return bare_disconnection_error()
    exc_type = next(exc_type for exc_type in INVALIDATED_ESCAPE_ERROR_TYPES if exc_type.__name__ == kind)
    return invalidated_dbapi_error(exc_type)


@pytest.mark.requires_db
@pytest.mark.asyncio
@pytest.mark.parametrize(("kind", "trips_fail_fast"), _propagated_session_cases())
async def test_propagated_session_error_stops_the_tick(integration_db, kind, trips_fail_fast):
    """A session-level database error stops the tick.

    A discarded connection and a dropped connection leave the fail-fast unarmed.
    An OperationalError with no SQLSTATE is a connect the server never answered,
    and that one trips it. Later buys in the tick do not run.
    """
    from tests.helpers.scheduler_isolation import assert_propagated_failure_stops_batch

    exc = _propagated_session_error(kind)
    tenant_id = _create_test_tenant(f"iso_{kind}_{int(trips_fail_fast)}")
    principal_id = _create_test_principal(tenant_id)
    buy_ids = [f"mb_{kind}_a", f"mb_{kind}_b"]

    seed_active_expired_buys(
        _create_media_buy,
        tenant_id=tenant_id,
        principal_id=principal_id,
        buy_ids=buy_ids,
    )

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
    tenant_id = _create_test_tenant("tenant_date_fallback")
    principal_id = _create_test_principal(tenant_id)

    # Create media buy with start_date in the past but no start_time
    past_date = (datetime.now(UTC) - timedelta(days=1)).date()
    future_date = (datetime.now(UTC) + timedelta(days=7)).date()

    media_buy_id = _create_media_buy(
        tenant_id=tenant_id,
        principal_id=principal_id,
        media_buy_id="mb_date_fallback",
        status="scheduled",
        start_time=None,  # No start_time
        end_time=None,  # No end_time
        start_date=past_date,  # But start_date is in the past
        end_date=future_date,
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
    tenant_id = _create_test_tenant("tenant_idempotent")
    principal_id = _create_test_principal(tenant_id)

    # Create media buy that should transition
    past_start = datetime.now(UTC) - timedelta(hours=1)
    future_end = datetime.now(UTC) + timedelta(days=7)

    media_buy_id = _create_media_buy(
        tenant_id=tenant_id,
        principal_id=principal_id,
        media_buy_id="mb_idempotent",
        status="scheduled",
        start_time=past_start,
        end_time=future_end,
    )

    before = read_media_buy_state(tenant_id, media_buy_id)

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
    tenant_id = _create_test_tenant("tenant_declined_write")
    principal_id = _create_test_principal(tenant_id)

    # A row the sweep WOULD transition: scheduled, start_time already passed.
    past_start = datetime.now(UTC) - timedelta(hours=1)
    future_end = datetime.now(UTC) + timedelta(days=7)

    media_buy_id = _create_media_buy(
        tenant_id=tenant_id,
        principal_id=principal_id,
        media_buy_id="mb_declined_write",
        status="scheduled",
        start_time=past_start,
        end_time=future_end,
    )

    before = read_media_buy_state(tenant_id, media_buy_id)
    assert before.status == "scheduled"

    scheduler = MediaBuyStatusScheduler()
    metric_before = counter_value("media_buy_status", tenant_id, "other")

    with patch.object(MediaBuyRepository, "update_status", return_value=None) as mock_update:
        records = await run_status_tick(scheduler)

    # The sweep did reach the write — otherwise the rest of this test is vacuous.
    # seller_committed=True is asserted, not tolerated: the pin forbids a null
    # confirmed_at on an "active" item, so a sweep that activated a row WITHOUT
    # claiming the commitment could put a schema-invalid document on the wire.
    mock_update.assert_called_once_with(media_buy_id, PersistedMediaBuyStatus.ACTIVE, seller_committed=True)

    assert logged(records, "INFO", "Updated media buy ") == []
    assert logged(records, "WARNING", STATUS_BATCH_SUMMARY_PREFIX) == [
        f"{STATUS_BATCH_SUMMARY_PREFIX}: 0 updated, 1 errors"
    ]
    assert counter_value("media_buy_status", tenant_id, "other") == metric_before + 1

    errors = [record for record in records if record.levelname == "ERROR"]
    assert len(errors) == 1
    err_msg = errors[0].getMessage()
    assert f"tenant_id={tenant_id}" in err_msg
    assert f"principal_id={principal_id}" in err_msg
    assert f"media_buy_id={media_buy_id}" in err_msg

    # (c) The row itself is untouched — status, and the bookkeeping a real move carries.
    after = read_media_buy_state(tenant_id, media_buy_id)
    assert after == before, f"a declined write must leave the row exactly as it was: {before} -> {after}"
