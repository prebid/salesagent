"""Integration tests for delivery webhook scheduler."""

import pytest

from src.services.delivery_webhook_scheduler import DeliveryWebhookScheduler
from tests.harness.sandbox_lifecycle import SchedulerLifecycleEnv

# TODO: @yusuf - We actually need to:
# - Test the scheduler is calling sending webhooks correctly in every correct intervals


@pytest.fixture
def scheduler():
    """Create a delivery webhook scheduler for testing."""
    return DeliveryWebhookScheduler()


@pytest.mark.asyncio
async def test_scheduler_start_stop(scheduler):
    """Test starting and stopping the scheduler."""
    # Start scheduler
    await scheduler.start()
    assert scheduler.is_running is True

    assert scheduler._task is not None

    # Stop scheduler
    await scheduler.stop()
    assert scheduler.is_running is False


@pytest.mark.asyncio
async def test_scheduler_start_when_already_running(scheduler):
    """Test that starting an already running scheduler is handled gracefully."""
    await scheduler.start()
    assert scheduler.is_running is True

    # TODO: @yusuf - Either remove this test or fix it to actually test the warning logging when the scheduler is already running
    # Try to start again - should log warning but not fail
    await scheduler.start()
    assert scheduler.is_running is True

    # Cleanup
    await scheduler.stop()


@pytest.mark.requires_db
@pytest.mark.asyncio
async def test_send_reports_no_webhooks(integration_db, scheduler):
    """A real buy without reporting configuration produces no outbound report."""
    with SchedulerLifecycleEnv() as env:
        env.seed_lifecycle()
        env.seed_owned_buy("sandbox", status="active")

        await scheduler._send_reports()

        assert env.delivery_attempts == 0


@pytest.mark.requires_db
@pytest.mark.asyncio
async def test_send_reports_with_media_buys(integration_db, scheduler):
    """Only the two configured buys in the batch produce outbound reports."""
    with SchedulerLifecycleEnv() as env:
        env.seed_lifecycle()
        env.seed_reporting_buy("sandbox", "mb_1")
        env.seed_owned_buy("sandbox", media_buy_id="mb_2", status="active")
        env.seed_reporting_buy("sandbox", "mb_3")

        await scheduler._send_reports()

        assert env.delivery_attempts == 2
