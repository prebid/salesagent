"""Behavioral tests for UC-004 delivery polling (_get_media_buy_delivery_impl).

Tests the delivery poll flow, status filtering, date range reporting,
and pricing option lookup against per-obligation scenarios.

Split from test_delivery_behavioral.py — see also:
- test_delivery_webhook_behavioral.py (deliver_webhook_with_retry)
- test_delivery_service_behavioral.py (WebhookDeliveryService, CircuitBreaker)

Each test targets exactly one obligation ID and follows the 6 hard rules:
1. MUST import from src.
2. MUST call production function
3. MUST assert production output
4. MUST have Covers: tag
5. MUST use factories where applicable (helpers here — no ORM factories for unit)
6. MUST NOT be mock-echo only
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest
from adcp.types import MediaBuyStatus

from src.core.exceptions import AdCPValidationError
from src.core.schemas import GetMediaBuyDeliveryRequest
from src.core.tools.media_buy_delivery import (
    _get_media_buy_delivery_impl,
    _resolve_delivery_status_filter,
)

# UC-004-ALT-STATUS-FILTERED-DELIVERY-02
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# UC-004-ALT-STATUS-FILTERED-DELIVERY-02
# ---------------------------------------------------------------------------


class TestStatusFilterCompleted:
    """Filter by status 'completed' returns only completed media buys.

    Covers: UC-004-ALT-STATUS-FILTERED-DELIVERY-02
    """

    def test_only_completed_buys_returned(self):
        """status_filter='completed' includes only media buys past their end_date.

        Covers: UC-004-ALT-STATUS-FILTERED-DELIVERY-02
        """
        from tests.harness.delivery_poll_unit import DeliveryPollEnv

        with DeliveryPollEnv() as env:
            # 3 buys: completed (past), active (current), ready (future)
            env.add_buy(media_buy_id="mb_completed", start_date=date(2025, 1, 1), end_date=date(2025, 6, 30))
            env.add_buy(media_buy_id="mb_active", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31))
            env.add_buy(media_buy_id="mb_ready", start_date=date(2027, 6, 1), end_date=date(2027, 12, 31))
            env.set_adapter_response("mb_completed", impressions=5000, spend=250.0)

            response = env.call_impl(status_filter="completed")

            returned_ids = [d.media_buy_id for d in response.media_buy_deliveries]
            assert returned_ids == ["mb_completed"]


# ---------------------------------------------------------------------------
# UC-004-ALT-STATUS-FILTERED-DELIVERY-03
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# UC-004-ALT-STATUS-FILTERED-DELIVERY-03
# ---------------------------------------------------------------------------


# UC-004-ALT-STATUS-FILTERED-DELIVERY-03
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# UC-004-ALT-STATUS-FILTERED-DELIVERY-03
# ---------------------------------------------------------------------------


class TestStatusFilterPaused:
    """Filter by status 'paused' returns only paused media buys.

    Covers: UC-004-ALT-STATUS-FILTERED-DELIVERY-03
    """

    def test_paused_buys_returned(self):
        """status_filter='paused' includes only paused media buys.

        Covers: UC-004-ALT-STATUS-FILTERED-DELIVERY-03
        """
        from tests.harness.delivery_poll_unit import DeliveryPollEnv

        with DeliveryPollEnv() as env:
            # Buy with current dates and is_paused=True
            env.add_buy(
                media_buy_id="mb_paused", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31), is_paused=True
            )
            env.set_adapter_response("mb_paused", impressions=1000, spend=50.0)

            response = env.call_impl(status_filter="paused")

            returned_ids = [d.media_buy_id for d in response.media_buy_deliveries]
            assert "mb_paused" in returned_ids


# ---------------------------------------------------------------------------
# UC-004-ALT-STATUS-FILTERED-DELIVERY-07
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# UC-004-ALT-STATUS-FILTERED-DELIVERY-07
# ---------------------------------------------------------------------------


# UC-004-ALT-STATUS-FILTERED-DELIVERY-07
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# UC-004-ALT-STATUS-FILTERED-DELIVERY-07
# ---------------------------------------------------------------------------


class TestValidStatusValuesAccepted:
    """All valid status values are accepted by status filter without error.

    Covers: UC-004-ALT-STATUS-FILTERED-DELIVERY-07
    """

    @pytest.mark.parametrize(
        "status_input",
        [
            MediaBuyStatus.active,
            MediaBuyStatus.pending_activation,
            MediaBuyStatus.paused,
            MediaBuyStatus.completed,
        ],
    )
    def test_adcp_status_values_accepted(self, status_input):
        """Each AdCP MediaBuyStatus enum value is processed without error.

        Covers: UC-004-ALT-STATUS-FILTERED-DELIVERY-07
        """
        from tests.harness.delivery_poll_unit import DeliveryPollEnv

        with DeliveryPollEnv() as env:
            env.add_buy(media_buy_id="mb_001")
            env.set_adapter_response("mb_001", impressions=1000, spend=50.0)

            # Act — must not raise
            response = env.call_impl(status_filter=status_input)

            # Assert — response is valid (no error raised)
            assert response is not None

    def test_special_all_value_returns_all_statuses(self):
        """The 'all' value returns all valid internal statuses.

        Covers: UC-004-ALT-STATUS-FILTERED-DELIVERY-07
        """
        valid_internal = {"active", "ready", "paused", "completed", "failed"}

        # Use a mock with .value = "all" to simulate the "all" special case
        mock_status = MagicMock()
        mock_status.value = "all"

        # Act — pure function test, harness not applicable
        result = _resolve_delivery_status_filter(mock_status, valid_internal, lambda s: s.value)

        # Assert — all valid statuses returned
        assert set(result) == valid_internal


# ---------------------------------------------------------------------------
# UC-004-ALT-WEBHOOK-PUSH-REPORTING-01
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# UC-004-ALT-WEBHOOK-PUSH-REPORTING-02
# ---------------------------------------------------------------------------


# UC-004-ALT-WEBHOOK-PUSH-REPORTING-01
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# UC-004-ALT-WEBHOOK-PUSH-REPORTING-02
# ---------------------------------------------------------------------------


class TestWebhookPayloadNotificationType:
    """Webhook payload includes notification_type field.

    Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-02
    """

    @pytest.mark.parametrize(
        "notification_type",
        ["scheduled", "final", "delayed", "adjusted"],
    )
    def test_response_accepts_notification_type(self, notification_type):
        """GetMediaBuyDeliveryResponse accepts and serializes notification_type values.

        Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-02
        """
        from tests.harness.delivery_poll_unit import DeliveryPollEnv

        with DeliveryPollEnv() as env:
            env.add_buy(media_buy_id="mb_001")
            env.set_adapter_response("mb_001", impressions=1000, spend=50.0)

            response = env.call_impl(media_buy_ids=["mb_001"])

            # Manually set notification_type (this is set by the caller, not _impl)
            response.notification_type = notification_type

            dumped = response.model_dump(mode="json")
            assert dumped["notification_type"] == notification_type


# ---------------------------------------------------------------------------
# UC-004-ALT-WEBHOOK-PUSH-REPORTING-03
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# UC-004-ALT-WEBHOOK-PUSH-REPORTING-03
# ---------------------------------------------------------------------------


# UC-004-ALT-WEBHOOK-PUSH-REPORTING-07
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# UC-004-ALT-WEBHOOK-PUSH-REPORTING-09
# ---------------------------------------------------------------------------


class TestWebhookExcludesAggregatedTotals:
    """Webhook payload does NOT include aggregated_totals.

    Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-09
    """

    def test_aggregated_totals_excluded_from_webhook_payload(self):
        """Webhook delivery payload should NOT contain aggregated_totals (polling only).

        Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-09
        """
        from tests.harness.delivery_poll_unit import DeliveryPollEnv

        with DeliveryPollEnv() as env:
            env.add_buy(media_buy_id="mb_001")
            env.set_adapter_response("mb_001", impressions=5000, spend=250.0)

            response = env.call_impl(media_buy_ids=["mb_001"])

            # Act — dump as webhook payload
            payload = response.webhook_payload()

            # Assert — aggregated_totals should NOT be in webhook payload
            assert "aggregated_totals" not in payload


# ---------------------------------------------------------------------------
# UC-004-ALT-WEBHOOK-PUSH-REPORTING-10
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# UC-004-ALT-WEBHOOK-PUSH-REPORTING-10
# ---------------------------------------------------------------------------


# UC-004-ALT-WEBHOOK-PUSH-REPORTING-10
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# UC-004-ALT-WEBHOOK-PUSH-REPORTING-10
# ---------------------------------------------------------------------------


class TestWebhookRequestedMetricsFiltering:
    """Webhook filters to requested_metrics.

    Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-10
    """

    def test_only_requested_metrics_in_payload(self):
        """Webhook payload should only include metrics specified in requested_metrics.

        Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-10
        """
        from tests.harness.delivery_poll_unit import DeliveryPollEnv

        with DeliveryPollEnv() as env:
            env.add_buy(media_buy_id="mb_001")
            env.set_adapter_response("mb_001", impressions=5000, spend=250.0, clicks=100)

            response = env.call_impl(media_buy_ids=["mb_001"])

            # Act — dump payload filtering to [impressions, clicks]
            payload = response.webhook_payload(requested_metrics=["impressions", "clicks"])
            totals = payload["media_buy_deliveries"][0]["totals"]

            # Assert — only requested metrics should be present (spend excluded)
            assert "spend" not in totals, "spend should be excluded when not in requested_metrics"


# ---------------------------------------------------------------------------
# UC-004-ALT-WEBHOOK-PUSH-REPORTING-11
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# UC-004-EXT-A-02
# ---------------------------------------------------------------------------


# UC-004-ALT-WEBHOOK-PUSH-REPORTING-11
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# UC-004-EXT-A-02
# ---------------------------------------------------------------------------


class TestUC004EXTA02AuthenticationFailure:
    """Authentication failure returns no data and no state modification.

    Covers: UC-004-EXT-A-02

    Given: an authentication failure (identity=None)
    When: _get_media_buy_delivery_impl is called
    Then: AdCPValidationError is raised, no delivery data is returned,
          and no state is modified (read-only operation).
    """

    def test_none_identity_raises_validation_error(self) -> None:
        """No delivery data returned on auth failure.

        Covers: UC-004-EXT-A-02
        """
        from tests.harness.delivery_poll_unit import DeliveryPollEnv

        with DeliveryPollEnv() as env:
            env.add_buy(media_buy_id="mb_001")

            # Call _impl directly with identity=None (bypassing env.call_impl which provides identity)
            req = GetMediaBuyDeliveryRequest(media_buy_ids=["mb_001"])

            with pytest.raises(AdCPValidationError) as exc_info:
                _get_media_buy_delivery_impl(req, identity=None)

            assert exc_info.value.message == "Context is required"


# ---------------------------------------------------------------------------
# UC-004-EXT-B-01
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# UC-004-EXT-B-01
# ---------------------------------------------------------------------------


# UC-004-EXT-G-01
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# UC-004-MAIN-02
# ---------------------------------------------------------------------------


class TestBuyerRefResolution:
    """Verify that buyer_refs resolve media buys when media_buy_ids is absent.

    Covers: UC-004-MAIN-02
    """

    def test_buyer_refs_resolve_media_buys(self):
        """buyer_refs resolve media buys when media_buy_ids absent.

        Covers: UC-004-MAIN-02
        """
        from tests.harness.delivery_poll_unit import DeliveryPollEnv

        with DeliveryPollEnv() as env:
            env.add_buy(media_buy_id="mb_100", buyer_ref="my_campaign_1")
            env.set_adapter_response("mb_100", impressions=5000, spend=250.0)

            response = env.call_impl(buyer_refs=["my_campaign_1"])

            assert len(response.media_buy_deliveries) == 1
            assert response.media_buy_deliveries[0].media_buy_id == "mb_100"

            # Verify repo was called with buyer_refs
            uow_instance = env.mock["uow"].return_value
            uow_instance.media_buys.get_by_principal.assert_called_once_with(
                "test_principal", buyer_refs=["my_campaign_1"]
            )

    # test_full_impl_returns_delivery_via_buyer_ref — migrated to integration

    def test_buyer_refs_ignored_when_media_buy_ids_present(self):
        """media_buy_ids takes precedence over buyer_refs per INV-2 rule.

        Covers: UC-004-MAIN-02
        """
        from tests.harness.delivery_poll_unit import DeliveryPollEnv

        with DeliveryPollEnv() as env:
            env.add_buy(media_buy_id="mb_300", buyer_ref="my_campaign_1")
            env.set_adapter_response("mb_300", impressions=5000, spend=250.0)

            response = env.call_impl(media_buy_ids=["mb_300"], buyer_refs=["my_campaign_1"])

            # media_buy_ids takes precedence — repo called with media_buy_ids, not buyer_refs
            uow_instance = env.mock["uow"].return_value
            uow_instance.media_buys.get_by_principal.assert_called_once_with("test_principal", media_buy_ids=["mb_300"])
            assert len(response.media_buy_deliveries) == 1


# ---------------------------------------------------------------------------
# UC-004-MAIN-03
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# UC-004-MAIN-03
# ---------------------------------------------------------------------------


# UC-004-MAIN-13
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# UC-004-MAIN-13
# ---------------------------------------------------------------------------


class TestMCPToolResultContent:
    """MCP wrapper returns ToolResult with both content and structured_content.

    Covers: UC-004-MAIN-13

    Note: These test the MCP transport wrapper, not _impl. The harness is used
    to build a realistic response via call_impl(), then the MCP wrapper is tested
    with that response as the _impl return value.
    """

    @staticmethod
    def _stub_delivery_response():
        """Build a realistic GetMediaBuyDeliveryResponse via harness."""
        from tests.harness.delivery_poll_unit import DeliveryPollEnv

        with DeliveryPollEnv() as env:
            env.add_buy(media_buy_id="mb_001")
            env.set_adapter_response("mb_001", impressions=5000, spend=250.0)
            return env.call_impl(media_buy_ids=["mb_001"])

    async def test_tool_result_has_content_and_structured_content(self):
        """MCP wrapper wraps _impl response in ToolResult with both fields.

        Covers: UC-004-MAIN-13
        """
        from unittest.mock import AsyncMock

        from fastmcp.server.context import Context
        from fastmcp.tools.tool import ToolResult

        from src.core.tools.media_buy_delivery import get_media_buy_delivery

        stub_response = self._stub_delivery_response()

        mock_ctx = MagicMock(spec=Context)
        mock_ctx.get_state = AsyncMock(return_value=None)

        with patch("src.core.tools.media_buy_delivery._get_media_buy_delivery_impl") as mock_impl:
            mock_impl.return_value = stub_response

            result = await get_media_buy_delivery(
                media_buy_ids=["mb_001"],
                ctx=mock_ctx,
            )

            assert isinstance(result, ToolResult)
            assert result.content is not None
            assert len(result.content) > 0
            assert result.structured_content is not None
            assert isinstance(result.structured_content, dict)
            assert result.structured_content["currency"] == "USD"

    async def test_structured_content_contains_response_fields(self):
        """structured_content dict contains all top-level response fields.

        Covers: UC-004-MAIN-13
        """
        from unittest.mock import AsyncMock

        from fastmcp.server.context import Context

        from src.core.tools.media_buy_delivery import get_media_buy_delivery

        stub_response = self._stub_delivery_response()

        mock_ctx = MagicMock(spec=Context)
        mock_ctx.get_state = AsyncMock(return_value=None)

        with patch("src.core.tools.media_buy_delivery._get_media_buy_delivery_impl") as mock_impl:
            mock_impl.return_value = stub_response

            result = await get_media_buy_delivery(
                media_buy_ids=["mb_001"],
                ctx=mock_ctx,
            )

            sc = result.structured_content
            assert "reporting_period" in sc
            assert "currency" in sc
            assert "aggregated_totals" in sc
            assert "media_buy_deliveries" in sc

    async def test_content_is_string_representation(self):
        """content field contains a human-readable string form of the response.

        Covers: UC-004-MAIN-13
        """
        from unittest.mock import AsyncMock

        from fastmcp.server.context import Context

        from src.core.tools.media_buy_delivery import get_media_buy_delivery

        stub_response = self._stub_delivery_response()

        mock_ctx = MagicMock(spec=Context)
        mock_ctx.get_state = AsyncMock(return_value=None)

        with patch("src.core.tools.media_buy_delivery._get_media_buy_delivery_impl") as mock_impl:
            mock_impl.return_value = stub_response

            result = await get_media_buy_delivery(
                media_buy_ids=["mb_001"],
                ctx=mock_ctx,
            )

            content_text = result.content[0].text if hasattr(result.content[0], "text") else str(result.content[0])
            assert len(content_text) > 0
            assert "No delivery data found" in content_text or "delivery" in content_text.lower()


# ---------------------------------------------------------------------------
# UC-004-MAIN-14
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# UC-004-MAIN-14
# ---------------------------------------------------------------------------
