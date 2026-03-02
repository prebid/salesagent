"""Behavioral tests for UC-004: Deliver Media Buy Metrics.

Tests the delivery poll flow (_get_media_buy_delivery_impl), status filtering
(_resolve_delivery_status_filter, _get_target_media_buys), and webhook delivery
(deliver_webhook_with_retry) against per-obligation scenarios.

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
from src.core.webhook_delivery import WebhookDelivery, deliver_webhook_with_retry

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
) -> AdapterGetMediaBuyDeliveryResponse:
    return AdapterGetMediaBuyDeliveryResponse(
        media_buy_id=media_buy_id,
        reporting_period=ReportingPeriod(
            start=datetime(2025, 1, 1, tzinfo=UTC),
            end=datetime(2025, 12, 31, tzinfo=UTC),
        ),
        totals=DeliveryTotals(impressions=float(impressions), spend=spend),
        by_package=[AdapterPackageDelivery(package_id="pkg_001", impressions=impressions, spend=spend)],
        currency="USD",
    )


# ---------------------------------------------------------------------------
# UC-004-ALT-CUSTOM-DATE-RANGE-01
# ---------------------------------------------------------------------------


class TestCustomDateRangeBothProvided:
    """Custom date range with both start and end provided.

    Covers: UC-004-ALT-CUSTOM-DATE-RANGE-01
    """

    @patch(f"{_PATCH}.get_adapter")
    @patch(f"{_PATCH}.get_principal_object")
    @patch(f"{_PATCH}.MediaBuyUoW")
    def test_reporting_period_matches_requested_dates(self, mock_uow_cls, mock_get_principal, mock_get_adapter):
        """When start_date and end_date are provided, reporting_period matches them.

        Covers: UC-004-ALT-CUSTOM-DATE-RANGE-01
        """
        # Arrange
        mock_get_principal.return_value = MagicMock()
        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.return_value = _make_adapter_response()
        mock_get_adapter.return_value = mock_adapter

        buy = _make_buy(start_date=date(2026, 3, 1), end_date=date(2026, 3, 7))

        mock_repo = MagicMock()
        mock_repo.get_by_principal.return_value = [buy]
        mock_repo.get_packages.return_value = []
        mock_uow = MagicMock()
        mock_uow.media_buys = mock_repo
        mock_uow.__enter__ = MagicMock(return_value=mock_uow)
        mock_uow.__exit__ = MagicMock(return_value=False)
        mock_uow_cls.return_value = mock_uow

        req = GetMediaBuyDeliveryRequest(
            media_buy_ids=["mb_001"],
            start_date="2026-03-01",
            end_date="2026-03-07",
        )

        # Act
        response = _get_media_buy_delivery_impl(req, _make_identity())

        # Assert — reporting_period must match the requested dates
        assert response.reporting_period.start == datetime(2026, 3, 1, tzinfo=UTC)
        assert response.reporting_period.end == datetime(2026, 3, 7, tzinfo=UTC)


# ---------------------------------------------------------------------------
# UC-004-ALT-CUSTOM-DATE-RANGE-04
# ---------------------------------------------------------------------------


class TestCustomDateRangeOverridesDefault:
    """Custom date range overrides default 30-day window.

    Covers: UC-004-ALT-CUSTOM-DATE-RANGE-04
    """

    @patch(f"{_PATCH}.get_adapter")
    @patch(f"{_PATCH}.get_principal_object")
    @patch(f"{_PATCH}.MediaBuyUoW")
    def test_ninety_day_range_not_truncated(self, mock_uow_cls, mock_get_principal, mock_get_adapter):
        """A 90-day custom range is used in full — 30-day default NOT applied.

        Covers: UC-004-ALT-CUSTOM-DATE-RANGE-04
        """
        # Arrange
        mock_get_principal.return_value = MagicMock()
        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.return_value = _make_adapter_response()
        mock_get_adapter.return_value = mock_adapter

        buy = _make_buy(start_date=date(2025, 1, 1), end_date=date(2025, 12, 31))

        mock_repo = MagicMock()
        mock_repo.get_by_principal.return_value = [buy]
        mock_repo.get_packages.return_value = []
        mock_uow = MagicMock()
        mock_uow.media_buys = mock_repo
        mock_uow.__enter__ = MagicMock(return_value=mock_uow)
        mock_uow.__exit__ = MagicMock(return_value=False)
        mock_uow_cls.return_value = mock_uow

        req = GetMediaBuyDeliveryRequest(
            media_buy_ids=["mb_001"],
            start_date="2025-01-01",
            end_date="2025-04-01",  # 90 days
        )

        # Act
        response = _get_media_buy_delivery_impl(req, _make_identity())

        # Assert — reporting_period spans the FULL 90 days
        period_start = response.reporting_period.start
        period_end = response.reporting_period.end
        delta = period_end - period_start
        assert delta.days == 90, f"Expected 90-day range, got {delta.days} days"


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


class TestWebhookDeliveryHappyPath:
    """Scheduled webhook delivery happy path — POST with signed payload.

    Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-01
    """

    @patch("src.core.webhook_delivery.get_db_session")
    @patch("src.core.webhook_delivery.WebhookURLValidator.validate_webhook_url", return_value=(True, None))
    @patch("src.core.webhook_delivery.requests.post")
    def test_webhook_sends_signed_payload(self, mock_post, mock_validate, mock_db_session):
        """Webhook delivery sends POST to configured URL with HMAC-signed payload.

        Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-01
        """
        # Arrange
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response
        mock_db_session.return_value.__enter__ = MagicMock()
        mock_db_session.return_value.__exit__ = MagicMock(return_value=False)

        delivery = WebhookDelivery(
            webhook_url="https://buyer.example.com/webhook",
            payload={
                "media_buy_id": "mb_001",
                "impressions": 5000,
                "spend": 250.0,
                "notification_type": "scheduled",
            },
            headers={"Content-Type": "application/json"},
            signing_secret="test-secret-key",
            max_retries=1,
        )

        # Act
        success, result = deliver_webhook_with_retry(delivery)

        # Assert — delivery succeeded with signed payload
        assert success is True
        assert result["status"] == "delivered"

        # Verify POST was called with correct URL
        call_args = mock_post.call_args
        assert call_args.args[0] == "https://buyer.example.com/webhook"

        # Verify HMAC signature headers were added
        sent_headers = call_args.kwargs["headers"]
        assert "X-Webhook-Signature" in sent_headers
        assert "X-Webhook-Timestamp" in sent_headers

        # Verify payload was sent
        sent_payload = call_args.kwargs["json"]
        assert sent_payload["media_buy_id"] == "mb_001"
        assert sent_payload["notification_type"] == "scheduled"


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
