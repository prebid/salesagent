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

from datetime import UTC, date, datetime
from unittest.mock import MagicMock, patch

import pytest
from adcp.types import MediaBuyStatus

from src.core.exceptions import AdCPValidationError
from src.core.resolved_identity import ResolvedIdentity
from src.core.schemas import (
    AdapterGetMediaBuyDeliveryResponse,
    AdapterPackageDelivery,
    DeliveryTotals,
    GetMediaBuyDeliveryRequest,
    GetMediaBuyDeliveryResponse,
    ReportingPeriod,
)
from src.core.testing_hooks import AdCPTestContext
from src.core.tools.media_buy_delivery import (
    _get_media_buy_delivery_impl,
    _get_target_media_buys,
    _resolve_delivery_status_filter,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PATCH = "src.core.tools.media_buy_delivery"


def _make_identity(
    principal_id: str = "test_principal",
    tenant_id: str = "test_tenant",
) -> ResolvedIdentity:
    return ResolvedIdentity(
        principal_id=principal_id,
        tenant_id=tenant_id,
        tenant={"tenant_id": tenant_id, "name": "Test Tenant"},
        protocol="mcp",
        testing_context=AdCPTestContext(
            dry_run=False,
            mock_time=None,
            jump_to_event=None,
            test_session_id=None,
        ),
    )


def _make_buy(
    media_buy_id: str = "mb_001",
    buyer_ref: str | None = "ref_001",
    start_date: date = date(2025, 1, 1),
    end_date: date = date(2025, 12, 31),
    budget: float = 10000.0,
    currency: str = "USD",
    raw_request: dict | None = None,
) -> MagicMock:
    """Create a mock MediaBuy ORM object."""
    buy = MagicMock()
    buy.media_buy_id = media_buy_id
    buy.buyer_ref = buyer_ref
    buy.start_date = start_date
    buy.end_date = end_date
    buy.start_time = None
    buy.end_time = None
    buy.budget = budget
    buy.currency = currency
    buy.raw_request = raw_request or {
        "buyer_ref": buyer_ref,
        "packages": [{"package_id": "pkg_001", "product_id": "prod_001"}],
    }
    return buy


def _make_adapter_response(
    media_buy_id: str = "mb_001",
    impressions: int = 5000,
    spend: float = 250.0,
    package_id: str = "pkg_001",
) -> AdapterGetMediaBuyDeliveryResponse:
    return AdapterGetMediaBuyDeliveryResponse(
        media_buy_id=media_buy_id,
        reporting_period=ReportingPeriod(
            start=datetime(2025, 1, 1, tzinfo=UTC),
            end=datetime(2025, 12, 31, tzinfo=UTC),
        ),
        totals=DeliveryTotals(impressions=float(impressions), spend=spend),
        by_package=[AdapterPackageDelivery(package_id=package_id, impressions=impressions, spend=spend)],
        currency="USD",
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
        # Arrange — 3 buys: one completed, one active, one ready
        today = date(2026, 3, 1)

        completed_buy = _make_buy(
            media_buy_id="mb_completed",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 6, 30),
        )
        active_buy = _make_buy(
            media_buy_id="mb_active",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
        )
        ready_buy = _make_buy(
            media_buy_id="mb_ready",
            start_date=date(2026, 6, 1),
            end_date=date(2026, 12, 31),
        )

        mock_repo = MagicMock()
        mock_repo.get_by_principal.return_value = [completed_buy, active_buy, ready_buy]

        req = GetMediaBuyDeliveryRequest(status_filter="completed")

        # Act
        result = _get_target_media_buys(req, "test_principal", mock_repo, today)

        # Assert — only the completed buy is returned
        returned_ids = [mb_id for mb_id, _ in result]
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

    @pytest.mark.xfail(
        reason="Production code derives status from dates only (ready/active/completed). "
        "'paused' is accepted as a filter value but never produced by date logic. "
        "Needs model-level paused flag."
    )
    def test_paused_buys_returned(self):
        """status_filter='paused' includes only paused media buys.

        Covers: UC-004-ALT-STATUS-FILTERED-DELIVERY-03
        """
        # Arrange — create a buy that should be "paused"
        # Note: production code has no mechanism to set paused from dates
        today = date(2026, 3, 1)

        paused_buy = _make_buy(
            media_buy_id="mb_paused",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
        )

        mock_repo = MagicMock()
        mock_repo.get_by_principal.return_value = [paused_buy]

        req = GetMediaBuyDeliveryRequest(status_filter="paused")

        # Act
        result = _get_target_media_buys(req, "test_principal", mock_repo, today)

        # Assert — paused buy should be included
        returned_ids = [mb_id for mb_id, _ in result]
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
        valid_internal = {"active", "ready", "paused", "completed", "failed"}

        def _to_internal(status: MediaBuyStatus) -> str:
            if status == MediaBuyStatus.pending_activation:
                return "ready"
            return status.value

        # Act — must not raise
        result = _resolve_delivery_status_filter(status_input, valid_internal, _to_internal)

        # Assert — returns a non-empty list of valid internal statuses
        assert len(result) > 0
        assert all(s in valid_internal for s in result)

    def test_special_all_value_returns_all_statuses(self):
        """The 'all' value returns all valid internal statuses.

        Covers: UC-004-ALT-STATUS-FILTERED-DELIVERY-07
        """
        valid_internal = {"active", "ready", "paused", "completed", "failed"}

        def _to_internal(status: MediaBuyStatus) -> str:
            return status.value

        # Use a mock with .value = "all" to simulate the "all" special case
        mock_status = MagicMock()
        mock_status.value = "all"

        # Act
        result = _resolve_delivery_status_filter(mock_status, valid_internal, _to_internal)

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
        from src.core.schemas import AggregatedTotals

        # Act — construct response with notification_type
        response = GetMediaBuyDeliveryResponse(
            reporting_period={
                "start": datetime(2026, 1, 1, tzinfo=UTC),
                "end": datetime(2026, 1, 31, tzinfo=UTC),
            },
            currency="USD",
            aggregated_totals=AggregatedTotals(
                impressions=0.0,
                spend=0.0,
                clicks=None,
                video_completions=None,
                media_buy_count=0,
            ),
            media_buy_deliveries=[],
            notification_type=notification_type,
        )

        # Assert — notification_type is preserved in the response
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

    @pytest.mark.xfail(
        reason="No webhook-specific payload assembly exists. GetMediaBuyDeliveryResponse.model_dump() "
        "always includes aggregated_totals (required field). Webhook payload filtering not implemented."
    )
    def test_aggregated_totals_excluded_from_webhook_payload(self):
        """Webhook delivery payload should NOT contain aggregated_totals (polling only).

        Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-09
        """
        from src.core.schemas import AggregatedTotals

        # Arrange — construct standard delivery response
        response = GetMediaBuyDeliveryResponse(
            reporting_period={
                "start": datetime(2026, 1, 1, tzinfo=UTC),
                "end": datetime(2026, 1, 31, tzinfo=UTC),
            },
            currency="USD",
            aggregated_totals=AggregatedTotals(
                impressions=5000.0,
                spend=250.0,
                clicks=None,
                video_completions=None,
                media_buy_count=1,
            ),
            media_buy_deliveries=[],
        )

        # Act — dump as webhook payload
        payload = response.model_dump(mode="json")

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

    @pytest.mark.xfail(
        reason="requested_metrics field does not exist on GetMediaBuyDeliveryResponse or request schemas. "
        "Metric filtering for webhook payloads not yet implemented."
    )
    def test_only_requested_metrics_in_payload(self):
        """Webhook payload should only include metrics specified in requested_metrics.

        Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-10
        """
        from src.core.schemas import AggregatedTotals

        # Arrange — response with all metric types populated
        response = GetMediaBuyDeliveryResponse(
            reporting_period={
                "start": datetime(2026, 1, 1, tzinfo=UTC),
                "end": datetime(2026, 1, 31, tzinfo=UTC),
            },
            currency="USD",
            aggregated_totals=AggregatedTotals(
                impressions=5000.0,
                spend=250.0,
                clicks=100.0,
                video_completions=50.0,
                media_buy_count=1,
            ),
            media_buy_deliveries=[],
        )

        # Act — dump payload (simulating filtering to [impressions, clicks])
        payload = response.model_dump(mode="json")
        totals = payload["aggregated_totals"]

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
        req = GetMediaBuyDeliveryRequest(
            media_buy_ids=["mb_001"],
            buyer_refs=None,
            status_filter=None,
            start_date=None,
            end_date=None,
            context=None,
        )

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

    def test_get_target_media_buys_uses_buyer_refs_when_no_media_buy_ids(self):
        """_get_target_media_buys passes buyer_refs to repo when media_buy_ids absent.

        Covers: UC-004-MAIN-02
        """
        buy = _make_buy(media_buy_id="mb_100", buyer_ref="my_campaign_1")
        repo = MagicMock()
        repo.get_by_principal.return_value = [buy]

        req = GetMediaBuyDeliveryRequest(
            media_buy_ids=None,
            buyer_refs=["my_campaign_1"],
        )

        reference_date = date(2025, 6, 15)

        result = _get_target_media_buys(req, "test_principal", repo, reference_date)

        repo.get_by_principal.assert_called_once_with("test_principal", buyer_refs=["my_campaign_1"])

        assert len(result) == 1
        assert result[0][0] == "mb_100"
        assert result[0][1] is buy

    # test_full_impl_returns_delivery_via_buyer_ref — migrated to integration

    def test_buyer_refs_ignored_when_media_buy_ids_present(self):
        """media_buy_ids takes precedence over buyer_refs per INV-2 rule.

        Covers: UC-004-MAIN-02
        """
        buy = _make_buy(media_buy_id="mb_300", buyer_ref="my_campaign_1")
        repo = MagicMock()
        repo.get_by_principal.return_value = [buy]

        req = GetMediaBuyDeliveryRequest(
            media_buy_ids=["mb_300"],
            buyer_refs=["my_campaign_1"],
        )

        reference_date = date(2025, 6, 15)
        result = _get_target_media_buys(req, "test_principal", repo, reference_date)

        repo.get_by_principal.assert_called_once_with("test_principal", media_buy_ids=["mb_300"])
        assert len(result) == 1


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
    """

    @staticmethod
    def _stub_delivery_response():
        """Build a minimal GetMediaBuyDeliveryResponse for wrapper testing."""
        from src.core.schemas import AggregatedTotals

        return GetMediaBuyDeliveryResponse(
            reporting_period={
                "start": datetime(2025, 1, 1, tzinfo=UTC),
                "end": datetime(2025, 12, 31, tzinfo=UTC),
            },
            currency="USD",
            aggregated_totals=AggregatedTotals(impressions=5000.0, spend=250.0, media_buy_count=1),
            media_buy_deliveries=[],
        )

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
