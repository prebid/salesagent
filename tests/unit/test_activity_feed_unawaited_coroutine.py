"""Tests for ActivityFeed sync context behavior.

Bug fix: salesagent-3laa
The sync log_* methods now store activity data synchronously via _store_activity(),
then attempt async WebSocket broadcast as best-effort. This ensures data is never
lost when called from sync contexts (no event loop running).

Previously, log_* methods called asyncio.create_task(self.broadcast_activity(...))
which leaked the coroutine when no event loop was running, silently losing all data.
"""

import gc
import warnings

from src.services.activity_feed import ActivityFeed


class TestActivityFeedSyncStorage:
    """Verify that ActivityFeed.log_* methods store data synchronously without warnings."""

    def test_log_api_call_stores_data_without_warning(self):
        """log_api_call should store activity data even without an event loop."""
        feed = ActivityFeed()

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            feed.log_api_call(
                tenant_id="test-tenant",
                principal_name="test-user",
                method="get_products",
                status_code=200,
                response_time_ms=50,
            )
            gc.collect()

        unawaited = [w for w in caught if issubclass(w.category, RuntimeWarning) and "never awaited" in str(w.message)]
        assert len(unawaited) == 0, (
            f"Should not emit unawaited coroutine warning, got: {[str(w.message) for w in unawaited]}"
        )

        # Data must be stored
        activities = feed.recent_activities.get("test-tenant")
        assert activities is not None and len(activities) == 1
        assert activities[0]["type"] == "api-call"

    def test_log_media_buy_stores_data_without_warning(self):
        """log_media_buy should store activity data even without an event loop."""
        feed = ActivityFeed()

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            feed.log_media_buy(
                tenant_id="test-tenant",
                principal_name="test-user",
                media_buy_id="mb-123",
                budget=5000.0,
                duration_days=30,
                action="created",
            )
            gc.collect()

        unawaited = [w for w in caught if issubclass(w.category, RuntimeWarning) and "never awaited" in str(w.message)]
        assert len(unawaited) == 0

        activities = feed.recent_activities.get("test-tenant")
        assert activities is not None and len(activities) == 1
        assert activities[0]["type"] == "media-buy"

    def test_log_creative_stores_data_without_warning(self):
        """log_creative should store activity data even without an event loop."""
        feed = ActivityFeed()

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            feed.log_creative(
                tenant_id="test-tenant",
                principal_name="test-user",
                creative_id="cr-456",
                format_name="Banner 300x250",
                status="uploaded",
            )
            gc.collect()

        unawaited = [w for w in caught if issubclass(w.category, RuntimeWarning) and "never awaited" in str(w.message)]
        assert len(unawaited) == 0

        activities = feed.recent_activities.get("test-tenant")
        assert activities is not None and len(activities) == 1
        assert activities[0]["type"] == "creative"

    def test_log_error_stores_data_without_warning(self):
        """log_error should store activity data even without an event loop."""
        feed = ActivityFeed()

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            feed.log_error(
                tenant_id="test-tenant",
                principal_name="test-user",
                error_message="Something broke",
                error_code="500",
            )
            gc.collect()

        unawaited = [w for w in caught if issubclass(w.category, RuntimeWarning) and "never awaited" in str(w.message)]
        assert len(unawaited) == 0

        activities = feed.recent_activities.get("test-tenant")
        assert activities is not None and len(activities) == 1
        assert activities[0]["type"] == "error"

    def test_activity_data_stored_in_sync_context(self):
        """Activity data must be stored synchronously regardless of event loop state."""
        feed = ActivityFeed()

        feed.log_api_call(
            tenant_id="test-tenant",
            principal_name="test-user",
            method="get_products",
            status_code=200,
        )

        activities = feed.recent_activities.get("test-tenant")
        assert activities is not None, "Activity data must be stored in sync context"
        assert len(activities) == 1
        assert activities[0]["action"] == "Called get_products"
        assert "timestamp" in activities[0]
        assert "time_relative" in activities[0]
