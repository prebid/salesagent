"""Integration tests for MediaBuyStatusScheduler.

These tests verify that the scheduler correctly transitions media buy statuses
based on flight dates:
- pending_activation -> active (when start_time passed and creatives approved)
- scheduled -> active (when start_time passed)
- active -> completed (when end_time passed)

Uses real PostgreSQL database via integration_db fixture.
"""

from datetime import UTC, datetime, timedelta

import pytest

from src.core.database.database_session import get_db_session
from src.core.database.models import (
    Creative,
    CreativeAssignment,
    CurrencyLimit,
    MediaBuy,
    Principal,
    PropertyTag,
    Tenant,
)
from src.services.media_buy_status_scheduler import MediaBuyStatusScheduler


def _create_test_tenant(tenant_id: str = "test_tenant") -> str:
    """Create a test tenant with required setup data."""
    with get_db_session() as session:
        tenant = Tenant(
            tenant_id=tenant_id,
            name="Test Tenant",
            subdomain="test",
            ad_server="mock",
            is_active=True,
        )
        session.add(tenant)

        # Required: CurrencyLimit
        currency_limit = CurrencyLimit(
            tenant_id=tenant_id,
            currency_code="USD",
            min_package_budget=1.00,
            max_daily_package_spend=100000.00,
        )
        session.add(currency_limit)

        # Required: PropertyTag
        property_tag = PropertyTag(
            tenant_id=tenant_id,
            tag_id="all_inventory",
            name="All Inventory",
            description="All available inventory",
        )
        session.add(property_tag)

        session.commit()

    return tenant_id


def _create_test_principal(tenant_id: str, principal_id: str = "test_principal") -> str:
    """Create a test principal."""
    with get_db_session() as session:
        principal = Principal(
            tenant_id=tenant_id,
            principal_id=principal_id,
            name="Test Principal",
            access_token="test_token",
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
            agent_url="https://creative.adcontextprotocol.org",
            format="display_300x250",
            status=status,
            data={"type": "display", "width": 300, "height": 250},
        )
        session.add(creative)
        session.commit()

    return creative_id


def _create_creative_assignment(
    tenant_id: str,
    media_buy_id: str,
    creative_id: str,
) -> None:
    """Assign a creative to a media buy."""
    import uuid

    with get_db_session() as session:
        assignment = CreativeAssignment(
            assignment_id=f"assign_{uuid.uuid4().hex[:8]}",
            tenant_id=tenant_id,
            media_buy_id=media_buy_id,
            creative_id=creative_id,
            package_id="default_package",  # Required field
        )
        session.add(assignment)
        session.commit()


def _get_media_buy_status(tenant_id: str, media_buy_id: str) -> str:
    """Get the current status of a media buy."""
    with get_db_session() as session:
        from sqlalchemy import select

        stmt = select(MediaBuy).filter_by(tenant_id=tenant_id, media_buy_id=media_buy_id)
        media_buy = session.scalars(stmt).first()
        return media_buy.status if media_buy else None


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
    assert _get_media_buy_status(tenant_id, media_buy_id) == "scheduled"

    # Run scheduler
    scheduler = MediaBuyStatusScheduler()
    await scheduler._update_statuses()

    # Verify status changed to active
    assert _get_media_buy_status(tenant_id, media_buy_id) == "active"


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
    assert _get_media_buy_status(tenant_id, media_buy_id) == "scheduled"

    # Run scheduler
    scheduler = MediaBuyStatusScheduler()
    await scheduler._update_statuses()

    # Verify status unchanged
    assert _get_media_buy_status(tenant_id, media_buy_id) == "scheduled"


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
    assert _get_media_buy_status(tenant_id, media_buy_id) == "pending_activation"

    # Run scheduler
    scheduler = MediaBuyStatusScheduler()
    await scheduler._update_statuses()

    # Verify status changed to active
    assert _get_media_buy_status(tenant_id, media_buy_id) == "active"


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
    assert _get_media_buy_status(tenant_id, media_buy_id) == "pending_activation"

    # Run scheduler
    scheduler = MediaBuyStatusScheduler()
    await scheduler._update_statuses()

    # Verify status unchanged (creatives not approved)
    assert _get_media_buy_status(tenant_id, media_buy_id) == "pending_activation"


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
    assert _get_media_buy_status(tenant_id, media_buy_id) == "pending_activation"

    # Run scheduler
    scheduler = MediaBuyStatusScheduler()
    await scheduler._update_statuses()

    # Verify status changed to active (no creatives = nothing to block)
    assert _get_media_buy_status(tenant_id, media_buy_id) == "active"


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
    assert _get_media_buy_status(tenant_id, media_buy_id) == "pending_activation"

    # Run scheduler
    scheduler = MediaBuyStatusScheduler()
    await scheduler._update_statuses()

    # Verify status unchanged (start time not passed)
    assert _get_media_buy_status(tenant_id, media_buy_id) == "pending_activation"


# =============================================================================
# Test: active -> completed (when end time has passed)
# =============================================================================


@pytest.mark.requires_db
@pytest.mark.asyncio
async def test_active_transitions_to_completed_when_end_time_passed(integration_db):
    """Media buy in 'active' status should transition to 'completed' when end_time passes."""
    tenant_id = _create_test_tenant("tenant_active_completed")
    principal_id = _create_test_principal(tenant_id)

    # Create media buy with end_time in the past
    past_start = datetime.now(UTC) - timedelta(days=7)
    past_end = datetime.now(UTC) - timedelta(hours=1)

    media_buy_id = _create_media_buy(
        tenant_id=tenant_id,
        principal_id=principal_id,
        media_buy_id="mb_active_to_completed",
        status="active",
        start_time=past_start,
        end_time=past_end,
    )

    # Verify initial status
    assert _get_media_buy_status(tenant_id, media_buy_id) == "active"

    # Run scheduler
    scheduler = MediaBuyStatusScheduler()
    await scheduler._update_statuses()

    # Verify status changed to completed
    assert _get_media_buy_status(tenant_id, media_buy_id) == "completed"


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
    assert _get_media_buy_status(tenant_id, media_buy_id) == "active"

    # Run scheduler
    scheduler = MediaBuyStatusScheduler()
    await scheduler._update_statuses()

    # Verify status unchanged
    assert _get_media_buy_status(tenant_id, media_buy_id) == "active"


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
    assert _get_media_buy_status(tenant_id, "mb_multi_1") == "scheduled"
    assert _get_media_buy_status(tenant_id, "mb_multi_2") == "active"
    assert _get_media_buy_status(tenant_id, "mb_multi_3") == "scheduled"

    # Run scheduler
    scheduler = MediaBuyStatusScheduler()
    await scheduler._update_statuses()

    # Verify expected transitions
    assert _get_media_buy_status(tenant_id, "mb_multi_1") == "active"
    assert _get_media_buy_status(tenant_id, "mb_multi_2") == "completed"
    assert _get_media_buy_status(tenant_id, "mb_multi_3") == "scheduled"  # No change


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
    assert _get_media_buy_status(tenant_id, media_buy_id) == "scheduled"

    # Run scheduler - should use start_date for transition
    scheduler = MediaBuyStatusScheduler()
    await scheduler._update_statuses()

    # Verify status changed to active (using start_date fallback)
    assert _get_media_buy_status(tenant_id, media_buy_id) == "active"


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

    scheduler = MediaBuyStatusScheduler()

    # Run scheduler first time
    await scheduler._update_statuses()
    assert _get_media_buy_status(tenant_id, media_buy_id) == "active"

    # Run scheduler second time - should be no-op
    await scheduler._update_statuses()
    assert _get_media_buy_status(tenant_id, media_buy_id) == "active"

    # Run scheduler third time - still no-op
    await scheduler._update_statuses()
    assert _get_media_buy_status(tenant_id, media_buy_id) == "active"
