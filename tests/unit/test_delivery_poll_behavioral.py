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
from src.core.schemas.delivery import GetCreativeDeliveryResponse, GetMediaBuyDeliveryResponse
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

    def test_media_buy_ids_resolve_media_buys(self):
        """media_buy_ids resolve media buys (buyer_refs removed in adcp 3.12).

        Covers: UC-004-MAIN-02
        """
        from tests.harness.delivery_poll_unit import DeliveryPollEnv

        with DeliveryPollEnv() as env:
            env.add_buy(media_buy_id="mb_100")
            env.set_adapter_response("mb_100", impressions=5000, spend=250.0)

            response = env.call_impl(media_buy_ids=["mb_100"])

            assert len(response.media_buy_deliveries) == 1
            assert response.media_buy_deliveries[0].media_buy_id == "mb_100"

    def test_media_buy_ids_used_for_fetch(self):
        """media_buy_ids is the identifier for delivery requests (adcp 3.12).

        Covers: UC-004-MAIN-02
        """
        from tests.harness.delivery_poll_unit import DeliveryPollEnv

        with DeliveryPollEnv() as env:
            env.add_buy(media_buy_id="mb_300")
            env.set_adapter_response("mb_300", impressions=5000, spend=250.0)

            response = env.call_impl(media_buy_ids=["mb_300"])

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


# ---------------------------------------------------------------------------
# UC-004-DISPLAY-01 — Display messages for MCP response envelope
# ---------------------------------------------------------------------------


def _make_media_buy_delivery_response(
    media_buy_count: int = 0,
    *,
    notification_type: str | None = None,
) -> GetMediaBuyDeliveryResponse:
    """Build a minimal GetMediaBuyDeliveryResponse with *media_buy_count* entries."""
    from datetime import UTC, datetime

    from src.core.schemas.delivery import (
        AggregatedTotals,
        DeliveryTotals,
        MediaBuyDeliveryData,
    )

    rp = {"start": datetime(2025, 1, 1, tzinfo=UTC), "end": datetime(2025, 1, 31, tzinfo=UTC)}
    deliveries = [
        MediaBuyDeliveryData(
            media_buy_id=f"mb_{i:03d}",
            status="active",
            totals=DeliveryTotals(impressions=1000.0, spend=50.0),
            by_package=[],
        )
        for i in range(media_buy_count)
    ]
    kwargs: dict = {
        "reporting_period": rp,
        "currency": "USD",
        "aggregated_totals": AggregatedTotals(impressions=0.0, spend=0.0, media_buy_count=media_buy_count),
        "media_buy_deliveries": deliveries,
    }
    if notification_type is not None:
        kwargs["notification_type"] = notification_type
    return GetMediaBuyDeliveryResponse(**kwargs)


def _make_creative_delivery_response(
    creative_count: int = 0,
) -> GetCreativeDeliveryResponse:
    """Build a minimal GetCreativeDeliveryResponse with *creative_count* entries."""
    from datetime import UTC, datetime

    from src.core.schemas.delivery import CreativeDeliveryData

    rp = {"start": datetime(2025, 1, 1, tzinfo=UTC), "end": datetime(2025, 1, 31, tzinfo=UTC)}
    creatives = [CreativeDeliveryData(creative_id=f"cr_{i:03d}") for i in range(creative_count)]
    return GetCreativeDeliveryResponse(
        reporting_period=rp,
        currency="USD",
        creatives=creatives,
    )


class TestMediaBuyDeliveryResponseStr:
    """__str__ returns a human-readable summary for the MCP protocol envelope.

    Covers: UC-004-DISPLAY-01
    """

    def test_zero_deliveries(self):
        """Zero media buys produces 'No delivery data found' message.

        Covers: UC-004-DISPLAY-01
        """
        resp = _make_media_buy_delivery_response(0)
        assert str(resp) == "No delivery data found for the specified period."

    def test_one_delivery(self):
        """Single media buy produces singular message.

        Covers: UC-004-DISPLAY-01
        """
        resp = _make_media_buy_delivery_response(1)
        assert str(resp) == "Retrieved delivery data for 1 media buy."

    def test_many_deliveries(self):
        """Multiple media buys produces plural message with count.

        Covers: UC-004-DISPLAY-01
        """
        resp = _make_media_buy_delivery_response(5)
        assert str(resp) == "Retrieved delivery data for 5 media buys."


class TestCreativeDeliveryResponseStr:
    """__str__ returns a human-readable summary for creative delivery responses.

    Covers: UC-004-DISPLAY-01
    """

    def test_zero_creatives(self):
        """Zero creatives produces 'No creative delivery data found' message.

        Covers: UC-004-DISPLAY-01
        """
        resp = _make_creative_delivery_response(0)
        assert str(resp) == "No creative delivery data found for the specified period."

    def test_one_creative(self):
        """Single creative produces singular message.

        Covers: UC-004-DISPLAY-01
        """
        resp = _make_creative_delivery_response(1)
        assert str(resp) == "Retrieved delivery data for 1 creative."

    def test_many_creatives(self):
        """Multiple creatives produces plural message with count.

        Covers: UC-004-DISPLAY-01
        """
        resp = _make_creative_delivery_response(3)
        assert str(resp) == "Retrieved delivery data for 3 creatives."


# ---------------------------------------------------------------------------
# UC-004-SERIAL-01 — Serialization compliance for next_expected_at
# ---------------------------------------------------------------------------


class TestNextExpectedAtSerialization:
    """model_dump() forces next_expected_at=null when notification_type is set.

    The AdCP protocol requires next_expected_at to be explicitly present
    (as null) when notification_type is 'final', so consumers know no
    further reports are expected. The base model excludes None values,
    so the override on line 304 re-injects it.

    Covers: UC-004-SERIAL-01
    """

    def test_final_notification_includes_null_next_expected_at(self):
        """notification_type='final' forces next_expected_at=null in JSON output.

        Covers: UC-004-SERIAL-01
        """
        resp = _make_media_buy_delivery_response(0, notification_type="final")
        dumped = resp.model_dump(mode="json")
        assert "next_expected_at" in dumped
        assert dumped["next_expected_at"] is None

    def test_scheduled_notification_includes_null_next_expected_at(self):
        """Any notification_type (not just 'final') forces next_expected_at into JSON.

        Covers: UC-004-SERIAL-01
        """
        resp = _make_media_buy_delivery_response(0, notification_type="scheduled")
        dumped = resp.model_dump(mode="json")
        assert "next_expected_at" in dumped
        assert dumped["next_expected_at"] is None

    def test_no_notification_type_excludes_next_expected_at(self):
        """Without notification_type, next_expected_at is excluded from JSON (base behavior).

        Covers: UC-004-SERIAL-01
        """
        resp = _make_media_buy_delivery_response(0)
        dumped = resp.model_dump(mode="json")
        assert "next_expected_at" not in dumped


# ---------------------------------------------------------------------------
# Coverage gap fyl7: media_buy_delivery.py identity + helper edge cases
# ---------------------------------------------------------------------------


class TestMissingPrincipalIdReturnsError:
    """_get_media_buy_delivery_impl returns error when principal_id is missing.

    Covers lines 91-93 of media_buy_delivery.py.
    """

    def test_none_principal_id_returns_error_response(self):
        from src.core.resolved_identity import ResolvedIdentity

        identity = ResolvedIdentity(
            principal_id=None,
            tenant_id="t1",
            tenant=MagicMock(),
        )
        req = GetMediaBuyDeliveryRequest(media_buy_ids=["mb_001"])

        response = _get_media_buy_delivery_impl(req, identity)
        assert response.errors is not None
        assert any(e.code == "principal_id_missing" for e in response.errors)

    def test_empty_string_principal_id_returns_error_response(self):
        from src.core.resolved_identity import ResolvedIdentity

        identity = ResolvedIdentity(
            principal_id="",
            tenant_id="t1",
            tenant=MagicMock(),
        )
        req = GetMediaBuyDeliveryRequest(media_buy_ids=["mb_001"])

        response = _get_media_buy_delivery_impl(req, identity)
        assert response.errors is not None
        assert any(e.code == "principal_id_missing" for e in response.errors)


class TestMissingTenantRaisesAuthError:
    """_get_media_buy_delivery_impl raises AdCPAuthenticationError when tenant is None.

    Covers line 132 of media_buy_delivery.py.
    """

    def test_none_tenant_raises_auth_error(self):
        from src.core.exceptions import AdCPAuthenticationError
        from src.core.resolved_identity import ResolvedIdentity

        identity = ResolvedIdentity(
            principal_id="p1",
            tenant_id="t1",
            tenant=None,
        )
        req = GetMediaBuyDeliveryRequest(media_buy_ids=["mb_001"])

        with patch("src.core.tools.media_buy_delivery.get_principal_object", return_value=MagicMock()):
            with pytest.raises(AdCPAuthenticationError, match="No tenant context"):
                _get_media_buy_delivery_impl(req, identity)


class TestStatusFilterRawString:
    """_resolve_delivery_status_filter handles raw string status values.

    Covers line 694 (fallback path) of media_buy_delivery.py.
    """

    def test_raw_string_active_is_recognized(self):
        valid = {"active", "ready", "paused", "completed", "failed"}
        result = _resolve_delivery_status_filter("active", valid, lambda s: s.value)
        assert result == ["active"]

    def test_unknown_raw_string_defaults_to_active(self):
        valid = {"active", "ready", "paused", "completed", "failed"}
        result = _resolve_delivery_status_filter("nonexistent", valid, lambda s: s.value)
        assert result == ["active"]
