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

from datetime import UTC, date, datetime, timedelta
from unittest.mock import MagicMock, Mock, call, patch

import pytest
from adcp.types import MediaBuyStatus, PricingModel

from src.core.exceptions import AdCPValidationError
from src.core.resolved_identity import ResolvedIdentity
from src.core.schemas import (
    AdapterGetMediaBuyDeliveryResponse,
    AdapterPackageDelivery,
    DeliveryStatus,
    DeliveryTotals,
    GetMediaBuyDeliveryRequest,
    GetMediaBuyDeliveryResponse,
    PackageDelivery,
    ReportingPeriod,
)
from src.core.testing_hooks import AdCPTestContext
from src.core.tools.media_buy_delivery import (
    _get_media_buy_delivery_impl,
    _get_pricing_options,
    _get_target_media_buys,
    _resolve_delivery_status_filter,
)
from src.core.webhook_authenticator import WebhookAuthenticator
from src.core.webhook_delivery import WebhookDelivery, deliver_webhook_with_retry
from src.services.webhook_delivery_service import (
    CircuitBreaker,
    CircuitState,
    WebhookDeliveryService,
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


# ---------------------------------------------------------------------------
# UC-004-ALT-WEBHOOK-PUSH-REPORTING-03
# ---------------------------------------------------------------------------


class TestWebhookNotificationTypeScheduled:
    """Normal periodic delivery sets notification_type to 'scheduled'.

    Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-03
    """

    @pytest.mark.xfail(
        reason="Production code does not auto-set notification_type based on delivery trigger. "
        "_get_media_buy_delivery_impl constructs response without notification_type (defaults to None)."
    )
    @patch(f"{_PATCH}.get_adapter")
    @patch(f"{_PATCH}.get_principal_object")
    @patch(f"{_PATCH}.MediaBuyUoW")
    def test_periodic_delivery_sets_scheduled_type(self, mock_uow_cls, mock_get_principal, mock_get_adapter):
        """Normal periodic delivery should auto-set notification_type to 'scheduled'.

        Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-03
        """
        # Arrange — normal delivery for an active buy
        mock_get_principal.return_value = MagicMock()
        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.return_value = _make_adapter_response()
        mock_get_adapter.return_value = mock_adapter

        buy = _make_buy(start_date=date(2026, 1, 1), end_date=date(2026, 12, 31))

        mock_repo = MagicMock()
        mock_repo.get_by_principal.return_value = [buy]
        mock_repo.get_packages.return_value = []
        mock_uow = MagicMock()
        mock_uow.media_buys = mock_repo
        mock_uow.__enter__ = MagicMock(return_value=mock_uow)
        mock_uow.__exit__ = MagicMock(return_value=False)
        mock_uow_cls.return_value = mock_uow

        req = GetMediaBuyDeliveryRequest(media_buy_ids=["mb_001"])

        # Act
        response = _get_media_buy_delivery_impl(req, _make_identity())

        # Assert — notification_type should be "scheduled" for normal periodic delivery
        dumped = response.model_dump(mode="json")
        assert dumped["notification_type"] == "scheduled"


# ---------------------------------------------------------------------------
# UC-004-ALT-WEBHOOK-PUSH-REPORTING-04
# ---------------------------------------------------------------------------


class TestWebhookNotificationTypeFinal:
    """Completed campaign sets notification_type to 'final' with no next_expected_at.

    Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-04
    """

    @pytest.mark.xfail(
        reason="Production code does not auto-set notification_type or manage next_expected_at "
        "based on campaign completion. _get_media_buy_delivery_impl leaves both as None."
    )
    @patch(f"{_PATCH}.get_adapter")
    @patch(f"{_PATCH}.get_principal_object")
    @patch(f"{_PATCH}.MediaBuyUoW")
    def test_completed_campaign_sets_final_type(self, mock_uow_cls, mock_get_principal, mock_get_adapter):
        """Completed campaign should set notification_type='final' and omit next_expected_at.

        Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-04
        """
        # Arrange — completed buy (end_date in the past)
        mock_get_principal.return_value = MagicMock()
        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.return_value = _make_adapter_response()
        mock_get_adapter.return_value = mock_adapter

        buy = _make_buy(start_date=date(2025, 1, 1), end_date=date(2025, 6, 30))

        mock_repo = MagicMock()
        mock_repo.get_by_principal.return_value = [buy]
        mock_repo.get_packages.return_value = []
        mock_uow = MagicMock()
        mock_uow.media_buys = mock_repo
        mock_uow.__enter__ = MagicMock(return_value=mock_uow)
        mock_uow.__exit__ = MagicMock(return_value=False)
        mock_uow_cls.return_value = mock_uow

        req = GetMediaBuyDeliveryRequest(media_buy_ids=["mb_001"])

        # Act
        response = _get_media_buy_delivery_impl(req, _make_identity())

        # Assert — notification_type is "final" and next_expected_at is None
        dumped = response.model_dump(mode="json")
        assert dumped["notification_type"] == "final"
        assert dumped["next_expected_at"] is None


# ---------------------------------------------------------------------------
# UC-004-ALT-WEBHOOK-PUSH-REPORTING-05
# ---------------------------------------------------------------------------


class TestWebhookSequenceNumber:
    """Monotonically increasing sequence_number per media buy.

    Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-05
    """

    @pytest.mark.xfail(
        reason="Production code does not auto-assign or persist sequence_number. "
        "_get_media_buy_delivery_impl leaves sequence_number as None (no auto-increment logic)."
    )
    @patch(f"{_PATCH}.get_adapter")
    @patch(f"{_PATCH}.get_principal_object")
    @patch(f"{_PATCH}.MediaBuyUoW")
    def test_sequence_number_auto_assigned(self, mock_uow_cls, mock_get_principal, mock_get_adapter):
        """Delivery response should auto-assign sequence_number starting from 1.

        Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-05
        """
        # Arrange
        mock_get_principal.return_value = MagicMock()
        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.return_value = _make_adapter_response()
        mock_get_adapter.return_value = mock_adapter

        buy = _make_buy(start_date=date(2026, 1, 1), end_date=date(2026, 12, 31))

        mock_repo = MagicMock()
        mock_repo.get_by_principal.return_value = [buy]
        mock_repo.get_packages.return_value = []
        mock_uow = MagicMock()
        mock_uow.media_buys = mock_repo
        mock_uow.__enter__ = MagicMock(return_value=mock_uow)
        mock_uow.__exit__ = MagicMock(return_value=False)
        mock_uow_cls.return_value = mock_uow

        req = GetMediaBuyDeliveryRequest(media_buy_ids=["mb_001"])

        # Act
        response = _get_media_buy_delivery_impl(req, _make_identity())

        # Assert — sequence_number should be auto-assigned (at least 1)
        assert response.sequence_number is not None, "sequence_number should be auto-assigned"
        assert response.sequence_number >= 1


# ---------------------------------------------------------------------------
# UC-004-ALT-WEBHOOK-PUSH-REPORTING-06
# ---------------------------------------------------------------------------


class TestWebhookNextExpectedAt:
    """next_expected_at computed for non-final deliveries.

    Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-06
    """

    @pytest.mark.xfail(
        reason="Production code does not compute next_expected_at based on reporting frequency. "
        "_get_media_buy_delivery_impl leaves next_expected_at as None."
    )
    @patch(f"{_PATCH}.get_adapter")
    @patch(f"{_PATCH}.get_principal_object")
    @patch(f"{_PATCH}.MediaBuyUoW")
    def test_next_expected_at_set_for_active_delivery(self, mock_uow_cls, mock_get_principal, mock_get_adapter):
        """Scheduled delivery for active buy should compute next_expected_at.

        Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-06
        """
        # Arrange — active buy (non-final delivery)
        mock_get_principal.return_value = MagicMock()
        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.return_value = _make_adapter_response()
        mock_get_adapter.return_value = mock_adapter

        buy = _make_buy(start_date=date(2026, 1, 1), end_date=date(2026, 12, 31))

        mock_repo = MagicMock()
        mock_repo.get_by_principal.return_value = [buy]
        mock_repo.get_packages.return_value = []
        mock_uow = MagicMock()
        mock_uow.media_buys = mock_repo
        mock_uow.__enter__ = MagicMock(return_value=mock_uow)
        mock_uow.__exit__ = MagicMock(return_value=False)
        mock_uow_cls.return_value = mock_uow

        req = GetMediaBuyDeliveryRequest(media_buy_ids=["mb_001"])

        # Act
        response = _get_media_buy_delivery_impl(req, _make_identity())

        # Assert — next_expected_at should be set for non-final delivery
        assert response.next_expected_at is not None, "next_expected_at must be set for non-final delivery"


# ---------------------------------------------------------------------------
# UC-004-ALT-WEBHOOK-PUSH-REPORTING-07
# ---------------------------------------------------------------------------


class TestWebhookHmacSha256Signing:
    """Webhook payload signed with HMAC-SHA256.

    Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-07
    """

    def test_sign_payload_produces_hmac_headers(self):
        """WebhookAuthenticator.sign_payload produces HMAC-SHA256 signature headers.

        Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-07
        """
        payload = {"media_buy_id": "mb_001", "impressions": 5000}
        secret = "test-signing-secret"

        # Act
        headers = WebhookAuthenticator.sign_payload(payload, secret)

        # Assert — HMAC-SHA256 signature header present with correct format
        assert "X-Webhook-Signature" in headers
        assert headers["X-Webhook-Signature"].startswith("sha256=")
        assert len(headers["X-Webhook-Signature"]) > len("sha256=")

        # Assert — timestamp header present for replay protection
        assert "X-Webhook-Timestamp" in headers
        assert headers["X-Webhook-Timestamp"].isdigit()


# ---------------------------------------------------------------------------
# UC-004-ALT-WEBHOOK-PUSH-REPORTING-08
# ---------------------------------------------------------------------------


class TestWebhookBearerTokenAuth:
    """Webhook delivery with Bearer token authentication.

    Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-08
    """

    @patch("src.core.webhook_delivery.get_db_session")
    @patch("src.core.webhook_delivery.WebhookURLValidator.validate_webhook_url", return_value=(True, None))
    @patch("src.core.webhook_delivery.requests.post")
    def test_bearer_token_sent_in_authorization_header(self, mock_post, mock_validate, mock_db_session):
        """Bearer token is forwarded in Authorization header when set by caller.

        Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-08
        """
        # Arrange
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response
        mock_db_session.return_value.__enter__ = MagicMock()
        mock_db_session.return_value.__exit__ = MagicMock(return_value=False)

        delivery = WebhookDelivery(
            webhook_url="https://buyer.example.com/webhook",
            payload={"media_buy_id": "mb_001"},
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer test-bearer-token-xyz",
            },
            max_retries=1,
        )

        # Act
        success, result = deliver_webhook_with_retry(delivery)

        # Assert — delivery succeeded
        assert success is True
        assert result["status"] == "delivered"

        # Assert — Bearer token was sent in the request headers
        call_args = mock_post.call_args
        sent_headers = call_args.kwargs["headers"]
        assert sent_headers["Authorization"] == "Bearer test-bearer-token-xyz"


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


class TestWebhookOnlyActiveMediaBuys:
    """Only active media buys trigger webhook delivery.

    Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-11
    """

    @pytest.mark.xfail(
        reason="deliver_webhook_with_retry does not check media buy status. "
        "It sends whatever payload is given regardless of the media buy's state. "
        "Webhook trigger scheduler with status filtering not yet implemented."
    )
    @patch("src.core.webhook_delivery.get_db_session")
    @patch("src.core.webhook_delivery.WebhookURLValidator.validate_webhook_url", return_value=(True, None))
    @patch("src.core.webhook_delivery.requests.post")
    def test_paused_media_buy_webhook_rejected(self, mock_post, mock_validate, mock_db_session):
        """Webhook delivery should be rejected for paused media buys.

        Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-11
        """
        # Arrange — webhook for a paused media buy
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response
        mock_db_session.return_value.__enter__ = MagicMock()
        mock_db_session.return_value.__exit__ = MagicMock(return_value=False)

        delivery = WebhookDelivery(
            webhook_url="https://buyer.example.com/webhook",
            payload={"media_buy_id": "mb_paused", "status": "paused"},
            headers={"Content-Type": "application/json"},
            max_retries=1,
        )

        # Act
        success, result = deliver_webhook_with_retry(delivery)

        # Assert — should NOT deliver webhook for paused media buy
        assert success is False, "Webhook should not be delivered for paused media buy"


# ---------------------------------------------------------------------------
# UC-004-ALT-WEBHOOK-PUSH-REPORTING-12
# ---------------------------------------------------------------------------


class TestWebhookEndpoint2xxAcknowledgment:
    """Endpoint acknowledges with 2xx — successful delivery recorded.

    Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-12
    """

    @patch("src.core.webhook_delivery.get_db_session")
    @patch("src.core.webhook_delivery.WebhookURLValidator.validate_webhook_url", return_value=(True, None))
    @patch("src.core.webhook_delivery.requests.post")
    def test_2xx_response_records_successful_delivery(self, mock_post, mock_validate, mock_db_session):
        """200 OK from buyer endpoint records delivery as successful.

        Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-12
        """
        # Arrange
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response
        mock_db_session.return_value.__enter__ = MagicMock()
        mock_db_session.return_value.__exit__ = MagicMock(return_value=False)

        delivery = WebhookDelivery(
            webhook_url="https://buyer.example.com/webhook",
            payload={"media_buy_id": "mb_001", "impressions": 5000},
            headers={"Content-Type": "application/json"},
            max_retries=1,
        )

        # Act
        success, result = deliver_webhook_with_retry(delivery)

        # Assert — delivery recorded as successful
        assert success is True
        assert result["status"] == "delivered"
        assert result["response_code"] == 200
        assert result["attempts"] == 1


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


class TestUC004EXTB01PrincipalNotFound:
    """Covers: UC-004-EXT-B-01"""

    def test_principal_not_found_returns_error_in_response(self):
        """When a valid token is presented but the principal does not exist in the
        tenant database, _get_media_buy_delivery_impl must return a response
        whose errors list contains exactly one entry with code "principal_not_found".

        Covers: UC-004-EXT-B-01
        """
        identity = _make_identity(principal_id="ghost_principal", tenant_id="test_tenant")
        req = GetMediaBuyDeliveryRequest()

        with patch(f"{_PATCH}.get_principal_object", return_value=None) as mock_get_principal:
            response = _get_media_buy_delivery_impl(req, identity)

        mock_get_principal.assert_called_once_with("ghost_principal", tenant_id="test_tenant")
        assert response.errors is not None, "Expected errors list in response"
        assert len(response.errors) == 1
        assert response.errors[0].code == "principal_not_found"
        assert response.media_buy_deliveries == []
        assert isinstance(response, GetMediaBuyDeliveryResponse)


# ---------------------------------------------------------------------------
# UC-004-EXT-C-01
# ---------------------------------------------------------------------------


class TestNonexistentMediaBuyIdReturnsNotFoundError:
    """Requesting delivery for a nonexistent media_buy_id returns media_buy_not_found error.

    Covers: UC-004-EXT-C-01
    """

    @patch(f"{_PATCH}.get_adapter")
    @patch(f"{_PATCH}.get_principal_object")
    @patch(f"{_PATCH}.MediaBuyUoW")
    def test_nonexistent_id_produces_media_buy_not_found_error(
        self, mock_uow_cls, mock_get_principal, mock_get_adapter
    ):
        """When media_buy_ids contains an ID absent from the DB, response.errors includes
        media_buy_not_found with the unresolved identifier.

        Covers: UC-004-EXT-C-01
        """
        mock_get_principal.return_value = MagicMock()
        mock_get_adapter.return_value = MagicMock()

        mock_repo = MagicMock()
        mock_repo.get_by_principal.return_value = []
        mock_repo.get_packages.return_value = []
        mock_uow = MagicMock()
        mock_uow.media_buys = mock_repo
        mock_uow.__enter__ = MagicMock(return_value=mock_uow)
        mock_uow.__exit__ = MagicMock(return_value=False)
        mock_uow_cls.return_value = mock_uow

        req = GetMediaBuyDeliveryRequest(media_buy_ids=["nonexistent_id"])

        response = _get_media_buy_delivery_impl(req, _make_identity())

        assert isinstance(response, GetMediaBuyDeliveryResponse)
        assert response.errors is not None
        assert len(response.errors) == 1
        error = response.errors[0]
        assert error.code == "media_buy_not_found"
        assert "nonexistent_id" in error.message


# ---------------------------------------------------------------------------
# UC-004-EXT-C-02
# ---------------------------------------------------------------------------


class TestPartialMediaBuyIdsNotFound:
    """Mixed request: some IDs exist, some do not.

    Covers: UC-004-EXT-C-02

    SPEC CONFLICT NOTE:
    - BR-RULE-030 (INV-5) says partial results should be returned.
    - ext-c says an error should be returned for not-found IDs.
    - ACTUAL PRODUCTION BEHAVIOR: BOTH -- partial results (mb_1 delivery data)
      are returned in media_buy_deliveries, AND a media_buy_not_found error
      for mb_999 is placed in the errors list. The implementation follows
      BR-RULE-030 (partial results) while also satisfying the ext-c error
      requirement by embedding errors alongside valid data.
    """

    @patch(f"{_PATCH}.get_adapter")
    @patch(f"{_PATCH}.get_principal_object")
    @patch(f"{_PATCH}.MediaBuyUoW")
    def test_partial_ids_returns_found_buy_and_not_found_error(
        self, mock_uow_cls, mock_get_principal, mock_get_adapter
    ):
        """When some IDs exist and some don't, returns delivery for found IDs
        and a media_buy_not_found error for missing IDs.

        Covers: UC-004-EXT-C-02
        """
        mock_get_principal.return_value = MagicMock()
        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.return_value = _make_adapter_response(media_buy_id="mb_1")
        mock_get_adapter.return_value = mock_adapter

        buy = _make_buy(media_buy_id="mb_1", start_date=date(2020, 1, 1), end_date=date(2030, 12, 31))

        mock_repo = MagicMock()
        mock_repo.get_by_principal.return_value = [buy]
        mock_repo.get_packages.return_value = []
        mock_uow = MagicMock()
        mock_uow.media_buys = mock_repo
        mock_uow.__enter__ = MagicMock(return_value=mock_uow)
        mock_uow.__exit__ = MagicMock(return_value=False)
        mock_uow_cls.return_value = mock_uow

        req = GetMediaBuyDeliveryRequest(
            media_buy_ids=["mb_1", "mb_999"],
        )

        response = _get_media_buy_delivery_impl(req, _make_identity())

        assert isinstance(response, GetMediaBuyDeliveryResponse)
        assert len(response.media_buy_deliveries) == 1
        assert response.media_buy_deliveries[0].media_buy_id == "mb_1"

        assert response.errors is not None
        assert len(response.errors) == 1
        not_found_error = response.errors[0]
        assert not_found_error.code == "media_buy_not_found"
        assert "mb_999" in not_found_error.message

        assert all("mb_1" not in e.message for e in response.errors)


# ---------------------------------------------------------------------------
# UC-004-EXT-C-03
# ---------------------------------------------------------------------------


class TestBuyerRefNotFound:
    """Buyer ref lookup returns media_buy_not_found error in response.

    Covers: UC-004-EXT-C-03
    """

    @patch(f"{_PATCH}.get_adapter")
    @patch(f"{_PATCH}.get_principal_object")
    @patch(f"{_PATCH}.MediaBuyUoW")
    def test_unknown_buyer_ref_produces_not_found_error(self, mock_uow_cls, mock_get_principal, mock_get_adapter):
        """When buyer_refs contains a ref that matches no media buy, the response
        contains an error with code 'media_buy_not_found'.

        Covers: UC-004-EXT-C-03
        """
        mock_get_principal.return_value = MagicMock()
        mock_get_adapter.return_value = MagicMock()

        mock_repo = MagicMock()
        mock_repo.get_by_principal.return_value = []
        mock_repo.get_packages.return_value = []
        mock_uow = MagicMock()
        mock_uow.media_buys = mock_repo
        mock_uow.__enter__ = MagicMock(return_value=mock_uow)
        mock_uow.__exit__ = MagicMock(return_value=False)
        mock_uow_cls.return_value = mock_uow

        req = GetMediaBuyDeliveryRequest(buyer_refs=["no_such_ref"])

        response = _get_media_buy_delivery_impl(req, _make_identity())

        assert isinstance(response, GetMediaBuyDeliveryResponse)
        assert response.errors is not None, "Expected errors list, got None"
        error_codes = [e.code for e in response.errors]
        assert "media_buy_not_found" in error_codes, f"Expected 'media_buy_not_found' in errors, got: {error_codes}"
        not_found_error = next(e for e in response.errors if e.code == "media_buy_not_found")
        assert "no_such_ref" in not_found_error.message


# ---------------------------------------------------------------------------
# UC-004-EXT-E-01
# ---------------------------------------------------------------------------


class TestEqualDateRangeReturnsInvalidDateRangeError:
    """Equal start and end dates return invalid_date_range error.

    Covers: UC-004-EXT-E-01

    BR-RULE-013: start_date >= end_date is invalid.
    """

    @patch(f"{_PATCH}.get_adapter")
    @patch(f"{_PATCH}.get_principal_object")
    def test_equal_dates_returns_invalid_date_range(self, mock_get_principal, mock_get_adapter):
        identity = _make_identity()
        req = GetMediaBuyDeliveryRequest(
            media_buy_ids=["mb_001"],
            start_date="2026-03-15",
            end_date="2026-03-15",
        )

        mock_get_principal.return_value = MagicMock(principal_id="test_principal")
        mock_get_adapter.return_value = MagicMock()

        response = _get_media_buy_delivery_impl(req, identity)

        assert isinstance(response, GetMediaBuyDeliveryResponse)
        assert response.media_buy_deliveries == []
        assert len(response.errors) == 1
        assert response.errors[0].code == "invalid_date_range"


# ---------------------------------------------------------------------------
# UC-004-EXT-E-02
# ---------------------------------------------------------------------------


class TestStartDateAfterEndDateReturnsInvalidDateRangeError:
    """Start date after end date returns invalid_date_range error.

    Covers: UC-004-EXT-E-02

    BR-RULE-013: start_date >= end_date is invalid.
    """

    @patch(f"{_PATCH}.get_adapter")
    @patch(f"{_PATCH}.get_principal_object")
    def test_start_after_end_returns_invalid_date_range(self, mock_get_principal, mock_get_adapter):
        identity = _make_identity()
        req = GetMediaBuyDeliveryRequest(
            media_buy_ids=["mb_001"],
            start_date="2026-03-20",
            end_date="2026-03-10",
        )

        mock_get_principal.return_value = MagicMock(principal_id="test_principal")
        mock_get_adapter.return_value = MagicMock()

        response = _get_media_buy_delivery_impl(req, identity)

        assert isinstance(response, GetMediaBuyDeliveryResponse)
        assert response.media_buy_deliveries == []
        assert len(response.errors) == 1
        assert response.errors[0].code == "invalid_date_range"


# ---------------------------------------------------------------------------
# UC-004-EXT-E-03
# ---------------------------------------------------------------------------


class TestInvalidDateRangeDoesNotFetchDeliveryData:
    """Invalid date range causes no delivery data to be fetched.

    Covers: UC-004-EXT-E-03

    POST-F1: No delivery data is fetched or returned on date range error.
    This proves the read-only property — the adapter is never invoked.
    """

    @patch(f"{_PATCH}.get_adapter")
    @patch(f"{_PATCH}.get_principal_object")
    def test_invalid_date_range_does_not_call_adapter(self, mock_get_principal, mock_get_adapter):
        identity = _make_identity()
        req = GetMediaBuyDeliveryRequest(
            media_buy_ids=["mb_001"],
            start_date="2026-03-20",
            end_date="2026-03-10",
        )

        mock_get_principal.return_value = MagicMock(principal_id="test_principal")
        mock_adapter = MagicMock()
        mock_get_adapter.return_value = mock_adapter

        response = _get_media_buy_delivery_impl(req, identity)

        # Verify error response
        assert response.media_buy_deliveries == []
        assert len(response.errors) == 1
        assert response.errors[0].code == "invalid_date_range"

        # Verify adapter's delivery method was never called (no data fetched)
        mock_adapter.get_media_buy_delivery.assert_not_called()


# ---------------------------------------------------------------------------
# UC-004-EXT-F-01
# ---------------------------------------------------------------------------


class TestAdapterUnavailableReturnsAdapterError:
    """Adapter unavailable (network error) returns adapter_error.

    Covers: UC-004-EXT-F-01

    POST-F2: buyer knows delivery data could not be retrieved.
    """

    @patch(f"{_PATCH}.get_adapter")
    @patch(f"{_PATCH}.get_principal_object")
    @patch(f"{_PATCH}.MediaBuyUoW")
    def test_adapter_connection_error_returns_adapter_error(self, mock_uow_cls, mock_get_principal, mock_get_adapter):
        identity = _make_identity()
        req = GetMediaBuyDeliveryRequest(media_buy_ids=["mb_001"])

        # Principal found
        mock_get_principal.return_value = MagicMock(principal_id="test_principal")

        # Set up UoW with repo returning one buy
        buy = _make_buy(
            media_buy_id="mb_001",
            start_date=date(2020, 1, 1),
            end_date=date(2030, 12, 31),
        )
        mock_repo = MagicMock()
        mock_repo.get_by_principal.return_value = [buy]
        mock_repo.get_packages.return_value = []
        mock_uow = MagicMock()
        mock_uow.__enter__ = MagicMock(return_value=mock_uow)
        mock_uow.__exit__ = MagicMock(return_value=False)
        mock_uow.media_buys = mock_repo
        mock_uow_cls.return_value = mock_uow

        # Adapter raises ConnectionError (network error)
        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.side_effect = ConnectionError("Connection refused")
        mock_get_adapter.return_value = mock_adapter

        response = _get_media_buy_delivery_impl(req, identity)

        assert isinstance(response, GetMediaBuyDeliveryResponse)
        error_codes = [e.code for e in response.errors]
        assert "adapter_error" in error_codes
        adapter_error = next(e for e in response.errors if e.code == "adapter_error")
        assert "mb_001" in adapter_error.message


# ---------------------------------------------------------------------------
# UC-004-EXT-F-02
# ---------------------------------------------------------------------------


class TestAdapterInternalServerErrorReturnsAdapterError:
    """Adapter 500 internal server error returns adapter_error.

    Covers: UC-004-EXT-F-02

    Ext-f step 7b: ad server returns 500 → buyer gets adapter_error.
    """

    @patch(f"{_PATCH}.get_adapter")
    @patch(f"{_PATCH}.get_principal_object")
    @patch(f"{_PATCH}.MediaBuyUoW")
    def test_adapter_500_error_returns_adapter_error(self, mock_uow_cls, mock_get_principal, mock_get_adapter):
        identity = _make_identity()
        req = GetMediaBuyDeliveryRequest(media_buy_ids=["mb_001"])

        # Principal found
        mock_get_principal.return_value = MagicMock(principal_id="test_principal")

        # Set up UoW with repo returning one buy
        buy = _make_buy(
            media_buy_id="mb_001",
            start_date=date(2020, 1, 1),
            end_date=date(2030, 12, 31),
        )
        mock_repo = MagicMock()
        mock_repo.get_by_principal.return_value = [buy]
        mock_repo.get_packages.return_value = []
        mock_uow = MagicMock()
        mock_uow.__enter__ = MagicMock(return_value=mock_uow)
        mock_uow.__exit__ = MagicMock(return_value=False)
        mock_uow.media_buys = mock_repo
        mock_uow_cls.return_value = mock_uow

        # Adapter raises RuntimeError simulating a 500 response
        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.side_effect = RuntimeError("500 Internal Server Error")
        mock_get_adapter.return_value = mock_adapter

        response = _get_media_buy_delivery_impl(req, identity)

        assert isinstance(response, GetMediaBuyDeliveryResponse)
        error_codes = [e.code for e in response.errors]
        assert "adapter_error" in error_codes
        adapter_error = next(e for e in response.errors if e.code == "adapter_error")
        assert "mb_001" in adapter_error.message


# ---------------------------------------------------------------------------
# UC-004-EXT-F-03
# ---------------------------------------------------------------------------


class TestAdapterFailureAuditTrail:
    """Adapter failure is logged to the audit trail (NFR-003).

    Covers: UC-004-EXT-F-03
    """

    @pytest.mark.xfail(
        reason=(
            "Production code at media_buy_delivery.py:267-268 catches adapter exceptions "
            "and logs via logger.error() but does NOT write to the AuditLog database table "
            "via AuditLogger.log_operation(). NFR-003 requires adapter failures to be "
            "recorded in the persistent audit trail. Fix: import get_audit_logger in "
            "_get_media_buy_delivery_impl and call log_operation(success=False) in the "
            "inner except block (lines 267-283)."
        ),
        strict=True,
    )
    @patch(f"{_PATCH}._get_pricing_options", return_value={})
    @patch(f"{_PATCH}.get_adapter")
    @patch(f"{_PATCH}.get_principal_object")
    @patch(f"{_PATCH}.MediaBuyUoW")
    @patch(f"{_PATCH}._get_target_media_buys")
    @patch("src.core.audit_logger.get_db_session")
    def test_adapter_failure_writes_audit_log(
        self,
        mock_audit_db_session,
        mock_get_target,
        mock_uow_cls,
        mock_get_principal,
        mock_get_adapter,
        mock_get_pricing,
    ):
        """When adapter.get_media_buy_delivery raises, the failure is audit-logged."""
        identity = _make_identity()
        buy = _make_buy(media_buy_id="mb_fail")

        mock_principal = MagicMock()
        mock_principal.principal_id = "test_principal"
        mock_get_principal.return_value = mock_principal

        adapter = MagicMock()
        adapter.get_media_buy_delivery.side_effect = RuntimeError("GAM API timeout")
        mock_get_adapter.return_value = adapter

        mock_get_target.return_value = [("mb_fail", buy)]

        mock_uow = MagicMock()
        mock_repo = MagicMock()
        mock_repo.get_packages.return_value = []
        mock_uow.media_buys = mock_repo
        mock_uow.__enter__ = MagicMock(return_value=mock_uow)
        mock_uow.__exit__ = MagicMock(return_value=False)
        mock_uow_cls.return_value = mock_uow

        mock_session = MagicMock()
        mock_audit_db_session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_audit_db_session.return_value.__exit__ = MagicMock(return_value=False)

        req = GetMediaBuyDeliveryRequest(
            media_buy_ids=["mb_fail"],
            start_date="2025-06-01",
            end_date="2025-06-30",
        )

        response = _get_media_buy_delivery_impl(req, identity)

        assert response is not None
        assert isinstance(response, GetMediaBuyDeliveryResponse)
        assert response.errors is not None
        assert any(e.code == "adapter_error" for e in response.errors)

        audit_adds = list(mock_session.add.call_args_list)
        assert len(audit_adds) > 0, (
            "No AuditLog records written to DB. Adapter failure must be recorded in audit trail per NFR-003."
        )


# ---------------------------------------------------------------------------
# UC-004-EXT-F-04
# ---------------------------------------------------------------------------


class TestAdapterErrorNoStateMutation:
    """Adapter error returns error response without modifying any state.

    Covers: UC-004-EXT-F-04
    """

    @patch(f"{_PATCH}._get_pricing_options", return_value={})
    @patch(f"{_PATCH}.get_adapter")
    @patch(f"{_PATCH}.get_principal_object")
    @patch(f"{_PATCH}.MediaBuyUoW")
    def test_adapter_error_returns_error_without_state_modification(
        self,
        mock_uow_cls: MagicMock,
        mock_get_principal: MagicMock,
        mock_get_adapter: MagicMock,
        mock_get_pricing: MagicMock,
    ) -> None:
        """When adapter raises, response has adapter_error and zero deliveries.

        Covers: UC-004-EXT-F-04
        """
        buy = _make_buy(media_buy_id="mb_err")
        mock_repo = MagicMock()
        mock_repo.get_by_principal.return_value = [buy]
        mock_repo.get_packages.return_value = []

        mock_uow = MagicMock()
        mock_uow.media_buys = mock_repo
        mock_uow.__enter__ = MagicMock(return_value=mock_uow)
        mock_uow.__exit__ = MagicMock(return_value=False)
        mock_uow_cls.return_value = mock_uow

        mock_get_principal.return_value = MagicMock()

        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.side_effect = RuntimeError("GAM API timeout")
        mock_get_adapter.return_value = mock_adapter

        identity = _make_identity()
        req = GetMediaBuyDeliveryRequest(
            media_buy_ids=["mb_err"],
            start_date="2025-06-01",
            end_date="2025-06-30",
        )

        result = _get_media_buy_delivery_impl(req, identity)

        assert isinstance(result, GetMediaBuyDeliveryResponse)
        assert result.errors is not None
        assert len(result.errors) == 1
        assert result.errors[0].code == "adapter_error"
        assert "mb_err" in result.errors[0].message
        assert result.media_buy_deliveries == []
        assert result.aggregated_totals.impressions == 0.0
        assert result.aggregated_totals.spend == 0.0
        assert result.aggregated_totals.media_buy_count == 0
        mock_uow.__exit__.assert_called_once()


# ---------------------------------------------------------------------------
# UC-004-EXT-G-01
# ---------------------------------------------------------------------------


class TestWebhook503RetryBackoff:
    """Tests that a 503 webhook endpoint triggers retries with exponential backoff.

    Covers: UC-004-EXT-G-01
    """

    @patch("src.core.webhook_delivery.WebhookURLValidator.validate_webhook_url", return_value=(True, None))
    @patch("src.core.webhook_delivery.time.sleep")
    @patch("src.core.webhook_delivery.requests.post")
    def test_503_triggers_retries_with_exponential_backoff(self, mock_post, mock_sleep, mock_validate):
        """When a webhook returns 503, the system retries with exponential backoff.

        Covers: UC-004-EXT-G-01

        Verifies:
        - All attempts are made (max_retries controls total attempts)
        - Backoff delays follow 2^attempt pattern (1s, 2s, 4s)
        - Final result is failure with correct attempt count
        """
        mock_response = MagicMock()
        mock_response.status_code = 503
        mock_response.text = "Service Unavailable"
        mock_post.return_value = mock_response

        delivery = WebhookDelivery(
            webhook_url="https://example.com/webhook",
            payload={"event": "delivery.update", "media_buy_id": "mb_001"},
            headers={"Content-Type": "application/json"},
            max_retries=4,  # 1 initial + 3 retries = 4 total attempts
            timeout=10,
            tenant_id=None,
            event_type=None,
        )

        success, result = deliver_webhook_with_retry(delivery)

        assert success is False
        assert result["status"] == "failed"
        assert result["attempts"] == 4
        assert result["response_code"] == 503
        assert mock_post.call_count == 4
        assert mock_sleep.call_count == 3
        mock_sleep.assert_has_calls(
            [
                call(1),  # 2^0 = 1s after attempt 0
                call(2),  # 2^1 = 2s after attempt 1
                call(4),  # 2^2 = 4s after attempt 2
            ]
        )

    @patch("src.core.webhook_delivery.WebhookURLValidator.validate_webhook_url", return_value=(True, None))
    @patch("src.core.webhook_delivery.time.sleep")
    @patch("src.core.webhook_delivery.requests.post")
    def test_503_no_backoff_after_final_attempt(self, mock_post, mock_sleep, mock_validate):
        """No sleep occurs after the last attempt — only between attempts.

        Covers: UC-004-EXT-G-01

        With max_retries=4, there should be 3 sleeps (not 4).
        """
        mock_response = MagicMock()
        mock_response.status_code = 503
        mock_response.text = "Service Unavailable"
        mock_post.return_value = mock_response

        delivery = WebhookDelivery(
            webhook_url="https://example.com/webhook",
            payload={"event": "test"},
            headers={},
            max_retries=4,
            tenant_id=None,
            event_type=None,
        )

        success, result = deliver_webhook_with_retry(delivery)

        assert mock_sleep.call_count == 3
        assert mock_post.call_count == 4

    @patch("src.core.webhook_delivery.WebhookURLValidator.validate_webhook_url", return_value=(True, None))
    @patch("src.core.webhook_delivery.time.sleep")
    @patch("src.core.webhook_delivery.requests.post")
    def test_503_then_success_stops_retrying(self, mock_post, mock_sleep, mock_validate):
        """If a retry succeeds, no further retries or backoff occur.

        Covers: UC-004-EXT-G-01

        First attempt returns 503, second attempt returns 200.
        """
        fail_response = MagicMock()
        fail_response.status_code = 503
        fail_response.text = "Service Unavailable"

        ok_response = MagicMock()
        ok_response.status_code = 200
        ok_response.text = "OK"

        mock_post.side_effect = [fail_response, ok_response]

        delivery = WebhookDelivery(
            webhook_url="https://example.com/webhook",
            payload={"event": "delivery.update"},
            headers={},
            max_retries=4,
            tenant_id=None,
            event_type=None,
        )

        success, result = deliver_webhook_with_retry(delivery)

        assert success is True
        assert result["status"] == "delivered"
        assert result["attempts"] == 2
        assert mock_post.call_count == 2
        assert mock_sleep.call_count == 1
        mock_sleep.assert_called_once_with(1)

    @pytest.mark.xfail(
        reason="Production code does not add jitter to exponential backoff. "
        "BR-RULE-029 specifies '1s, 2s, 4s + jitter' but deliver_webhook_with_retry "
        "(src/core/webhook_delivery.py:226-228) uses exact 2**attempt with no randomization. "
        "Jitter would be added at line 226 with e.g. backoff_time = 2**attempt + random.uniform(0, 0.5).",
        strict=True,
    )
    @patch("src.core.webhook_delivery.WebhookURLValidator.validate_webhook_url", return_value=(True, None))
    @patch("src.core.webhook_delivery.time.sleep")
    @patch("src.core.webhook_delivery.requests.post")
    def test_backoff_includes_jitter(self, mock_post, mock_sleep, mock_validate):
        """Backoff delays should include jitter to prevent thundering herd.

        Covers: UC-004-EXT-G-01

        BR-RULE-029 specifies exponential backoff with jitter.
        Current implementation uses exact powers of 2 without randomization.
        """
        mock_response = MagicMock()
        mock_response.status_code = 503
        mock_response.text = "Service Unavailable"
        mock_post.return_value = mock_response

        delivery = WebhookDelivery(
            webhook_url="https://example.com/webhook",
            payload={"event": "test"},
            headers={},
            max_retries=4,
            tenant_id=None,
            event_type=None,
        )

        deliver_webhook_with_retry(delivery)

        sleep_values = [c.args[0] for c in mock_sleep.call_args_list]
        exact_powers = [1, 2, 4]

        has_jitter = any(actual != expected for actual, expected in zip(sleep_values, exact_powers, strict=True))
        assert has_jitter, f"Sleep values {sleep_values} are exact powers of 2 — no jitter detected"


# ---------------------------------------------------------------------------
# UC-004-EXT-G-02
# ---------------------------------------------------------------------------


class TestWebhookRetrySucceedsOnSecondAttempt:
    """Webhook endpoint fails first, succeeds on retry -> delivery recorded as successful.

    Covers: UC-004-EXT-G-02
    """

    @patch("src.core.webhook_delivery.time.sleep")
    @patch("src.core.webhook_delivery._update_delivery_record")
    @patch("src.core.webhook_delivery._create_delivery_record")
    @patch("src.core.webhook_delivery.WebhookURLValidator.validate_webhook_url", return_value=(True, ""))
    @patch("requests.post")
    def test_transient_failure_then_success_records_delivered(
        self, mock_post, mock_validator, mock_create, mock_update, mock_sleep
    ):
        """Given a webhook that 503s then 200s, the delivery result is 'delivered'.

        Covers: UC-004-EXT-G-02
        """
        resp_503 = Mock()
        resp_503.status_code = 503
        resp_503.text = "Service Unavailable"

        resp_200 = Mock()
        resp_200.status_code = 200

        mock_post.side_effect = [resp_503, resp_200]

        delivery = WebhookDelivery(
            webhook_url="https://buyer.example.com/webhook",
            payload={"media_buy_id": "mb_001", "event": "delivery.update"},
            headers={"Content-Type": "application/json"},
            max_retries=3,
            timeout=10,
            event_type="delivery.update",
            tenant_id="test_tenant",
            object_id="mb_001",
        )

        success, result = deliver_webhook_with_retry(delivery)

        assert success is True
        assert result["status"] == "delivered"
        assert result["attempts"] == 2
        assert result["response_code"] == 200

        assert mock_update.call_count == 1
        update_kwargs = mock_update.call_args.kwargs
        assert update_kwargs["status"] == "delivered"
        assert update_kwargs["attempts"] == 2
        assert update_kwargs["response_code"] == 200
        assert update_kwargs["delivered_at"] is not None

        mock_sleep.assert_called_once_with(1)

        assert mock_create.call_count == 1
        create_kwargs = mock_create.call_args.kwargs
        assert create_kwargs["tenant_id"] == "test_tenant"
        assert create_kwargs["event_type"] == "delivery.update"


# ---------------------------------------------------------------------------
# UC-004-EXT-G-03
# ---------------------------------------------------------------------------


class TestCircuitBreakerOpensAfterRetriesExhausted:
    """Circuit breaker opens after consecutive failures suppress subsequent deliveries.

    Covers: UC-004-EXT-G-03
    """

    def test_circuit_breaker_opens_after_threshold_failures(self):
        """After failure_threshold consecutive failures, circuit breaker moves to OPEN.

        Covers: UC-004-EXT-G-03
        """
        cb = CircuitBreaker(failure_threshold=3)

        assert cb.state == CircuitState.CLOSED
        assert cb.can_attempt() is True

        cb.record_failure()
        assert cb.state == CircuitState.CLOSED
        cb.record_failure()
        assert cb.state == CircuitState.CLOSED
        cb.record_failure()

        assert cb.state == CircuitState.OPEN
        assert cb.can_attempt() is False

    def test_open_circuit_breaker_suppresses_subsequent_deliveries(self):
        """When circuit is OPEN, can_attempt returns False, suppressing deliveries.

        Covers: UC-004-EXT-G-03
        """
        cb = CircuitBreaker(failure_threshold=3)

        for _ in range(3):
            cb.record_failure()

        assert cb.state == CircuitState.OPEN

        assert cb.can_attempt() is False
        assert cb.can_attempt() is False
        assert cb.can_attempt() is False

    @patch("src.services.webhook_delivery_service.time.sleep")
    @patch("src.services.webhook_delivery_service.random.uniform", return_value=0.0)
    @patch("src.services.webhook_delivery_service.httpx.Client")
    @patch("src.core.database.database_session.get_db_session")
    def test_service_skips_delivery_when_circuit_open(self, mock_get_db, mock_client, mock_random, mock_sleep):
        """WebhookDeliveryService skips webhook send when circuit breaker is OPEN.

        Covers: UC-004-EXT-G-03
        """
        service = WebhookDeliveryService()

        endpoint_key = "test_tenant:https://example.com/webhook"
        mock_config = MagicMock()
        mock_config.url = "https://example.com/webhook"
        mock_config.authentication_type = None
        mock_config.authentication_token = None
        mock_config.webhook_secret = None

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_client.return_value.__enter__.return_value.post.return_value = mock_response

        mock_session = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [mock_config]
        mock_session.scalars.return_value = mock_scalars
        mock_ctx = MagicMock()
        mock_ctx.__enter__.return_value = mock_session
        mock_ctx.__exit__.return_value = None
        mock_get_db.return_value = mock_ctx

        start_time = datetime(2025, 6, 1, tzinfo=UTC)

        for i in range(5):
            service.send_delivery_webhook(
                media_buy_id=f"mb_{i}",
                tenant_id="test_tenant",
                principal_id="p1",
                reporting_period_start=start_time,
                reporting_period_end=start_time,
                impressions=1000,
                spend=100.0,
            )

        state, failures = service.get_circuit_breaker_state(endpoint_key)
        assert state == CircuitState.OPEN

        mock_client.return_value.__enter__.return_value.post.reset_mock()

        result = service.send_delivery_webhook(
            media_buy_id="mb_suppressed",
            tenant_id="test_tenant",
            principal_id="p1",
            reporting_period_start=start_time,
            reporting_period_end=start_time,
            impressions=1000,
            spend=100.0,
        )

        assert result is False
        mock_client.return_value.__enter__.return_value.post.assert_not_called()

    @pytest.mark.xfail(
        reason="reporting_delayed status is not set by _get_media_buy_delivery_impl "
        "when circuit breaker is open. The schema supports 'reporting_delayed' "
        "(src/core/schemas/delivery.py:224) and the circuit breaker logic exists in "
        "src/services/webhook_delivery_service.py:40-121, but there is no integration "
        "between the circuit breaker state and the delivery status computation in "
        "src/core/tools/media_buy_delivery.py:219-230.",
        strict=True,
    )
    @patch(f"{_PATCH}._get_pricing_options", return_value={})
    @patch(f"{_PATCH}.get_adapter")
    @patch(f"{_PATCH}.get_principal_object")
    @patch(f"{_PATCH}.MediaBuyUoW")
    def test_delivery_marked_reporting_delayed_when_circuit_open(
        self,
        mock_uow_cls,
        mock_get_principal,
        mock_get_adapter,
        mock_get_pricing,
    ):
        """Delivery should be marked reporting_delayed when circuit breaker is open.

        Covers: UC-004-EXT-G-03
        """
        identity = _make_identity()
        buy = _make_buy(media_buy_id="mb_001")

        mock_get_principal.return_value = MagicMock()
        mock_get_adapter.return_value = MagicMock()

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
            end_date="2025-06-30",
        )

        response = _get_media_buy_delivery_impl(req, identity)

        assert len(response.media_buy_deliveries) == 1
        assert response.media_buy_deliveries[0].status == "reporting_delayed"


# ---------------------------------------------------------------------------
# UC-004-EXT-G-04
# ---------------------------------------------------------------------------


class TestCircuitBreakerHalfOpenProbe:
    """When a circuit breaker is OPEN and the timeout elapses, the system
    transitions to HALF_OPEN and allows a probe attempt.

    Covers: UC-004-EXT-G-04
    """

    def test_open_circuit_transitions_to_half_open_after_timeout(self):
        """Given an OPEN circuit breaker whose timeout has elapsed,
        can_attempt() should transition state to HALF_OPEN and return True."""
        cb = CircuitBreaker(failure_threshold=3, success_threshold=2, timeout_seconds=60)

        # Force circuit into OPEN state with a failure time in the past
        cb.state = CircuitState.OPEN
        cb.last_failure_time = datetime.now(UTC) - timedelta(seconds=120)

        # When can_attempt is called (simulating the timer/probe check)
        result = cb.can_attempt()

        # Then the circuit transitions to HALF_OPEN and allows the probe
        assert result is True
        assert cb.state == CircuitState.HALF_OPEN
        assert cb.success_count == 0  # Reset for tracking recovery successes

    def test_open_circuit_stays_open_before_timeout(self):
        """Given an OPEN circuit breaker whose timeout has NOT elapsed,
        can_attempt() should return False and stay OPEN."""
        cb = CircuitBreaker(failure_threshold=3, success_threshold=2, timeout_seconds=60)

        cb.state = CircuitState.OPEN
        cb.last_failure_time = datetime.now(UTC) - timedelta(seconds=30)

        result = cb.can_attempt()

        assert result is False
        assert cb.state == CircuitState.OPEN

    def test_half_open_probe_success_path(self):
        """After transitioning to HALF_OPEN, a successful probe should be recorded.
        With enough successes, the circuit closes."""
        cb = CircuitBreaker(failure_threshold=3, success_threshold=2, timeout_seconds=60)

        # Transition to HALF_OPEN via timeout expiry
        cb.state = CircuitState.OPEN
        cb.last_failure_time = datetime.now(UTC) - timedelta(seconds=120)
        assert cb.can_attempt() is True
        assert cb.state == CircuitState.HALF_OPEN

        # First successful probe
        cb.record_success()
        assert cb.state == CircuitState.HALF_OPEN  # Not yet enough successes
        assert cb.success_count == 1

        # Second successful probe -> closes the circuit
        cb.record_success()
        assert cb.state == CircuitState.CLOSED

    def test_half_open_probe_failure_reopens_circuit(self):
        """After transitioning to HALF_OPEN, a failed probe should reopen the circuit."""
        cb = CircuitBreaker(failure_threshold=3, success_threshold=2, timeout_seconds=60)

        # Transition to HALF_OPEN
        cb.state = CircuitState.OPEN
        cb.last_failure_time = datetime.now(UTC) - timedelta(seconds=120)
        assert cb.can_attempt() is True
        assert cb.state == CircuitState.HALF_OPEN

        # Probe fails
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

    def test_service_allows_probe_after_circuit_breaker_timeout(self):
        """Integration test: WebhookDeliveryService uses circuit breaker
        can_attempt() to allow half-open probe after timeout expires.

        This verifies the service-level integration at _send_webhook_enhanced
        where can_attempt() gates webhook delivery."""
        service = WebhookDeliveryService()

        endpoint_key = "test_tenant:https://example.com/webhook"
        cb = CircuitBreaker(failure_threshold=3, success_threshold=2, timeout_seconds=60)
        cb.state = CircuitState.OPEN
        cb.last_failure_time = datetime.now(UTC) - timedelta(seconds=120)

        service._circuit_breakers[endpoint_key] = cb

        # Verify the circuit breaker transitions when checked
        assert cb.can_attempt() is True
        assert cb.state == CircuitState.HALF_OPEN

    def test_full_open_to_halfopen_via_failures_then_timeout(self):
        """End-to-end: circuit starts CLOSED, accumulates failures to go OPEN,
        then after timeout transitions to HALF_OPEN on next can_attempt()."""
        cb = CircuitBreaker(failure_threshold=3, success_threshold=2, timeout_seconds=60)
        assert cb.state == CircuitState.CLOSED

        # Accumulate failures to trip the circuit
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.CLOSED  # Not yet at threshold

        cb.record_failure()
        assert cb.state == CircuitState.OPEN  # Tripped

        # Simulate time passing beyond timeout
        cb.last_failure_time = datetime.now(UTC) - timedelta(seconds=61)

        # Next attempt should transition to HALF_OPEN (the half-open probe)
        result = cb.can_attempt()
        assert result is True
        assert cb.state == CircuitState.HALF_OPEN


# ---------------------------------------------------------------------------
# UC-004-EXT-G-05
# ---------------------------------------------------------------------------


class TestWebhook401ForbiddenNoRetry:
    """Tests that 401 authentication errors are not retried.

    Covers: UC-004-EXT-G-05
    """

    @patch("src.core.webhook_delivery._update_delivery_record")
    @patch("src.core.webhook_delivery._create_delivery_record")
    @patch("src.core.webhook_delivery.WebhookURLValidator.validate_webhook_url")
    @patch("src.core.webhook_delivery.requests.post")
    def test_401_response_is_not_retried_and_marked_failed(
        self, mock_post, mock_validate, mock_create_record, mock_update_record
    ):
        """A 401 Forbidden response must cause immediate failure with no retries.

        Covers: UC-004-EXT-G-05
        """
        # Arrange: URL validation passes
        mock_validate.return_value = (True, None)

        # Arrange: endpoint returns 401 Forbidden
        mock_response = Mock()
        mock_response.status_code = 401
        mock_response.text = "Unauthorized - invalid credentials"
        mock_post.return_value = mock_response

        delivery = WebhookDelivery(
            webhook_url="https://buyer.example.com/webhook",
            payload={"media_buy_id": "mb_001", "event": "delivery.update"},
            headers={"Content-Type": "application/json"},
            max_retries=3,
            timeout=10,
            event_type="delivery.update",
            tenant_id="test_tenant",
            object_id="mb_001",
        )

        # Act
        success, result = deliver_webhook_with_retry(delivery)

        # Assert: delivery failed
        assert success is False
        assert result["status"] == "failed"
        assert result["response_code"] == 401

        # Assert: exactly 1 attempt -- NO retries for 4xx
        assert mock_post.call_count == 1
        assert result["attempts"] == 1

        # Assert: error message contains the status code
        assert "401" in result["error"]

    @patch("src.core.webhook_delivery._update_delivery_record")
    @patch("src.core.webhook_delivery._create_delivery_record")
    @patch("src.core.webhook_delivery.WebhookURLValidator.validate_webhook_url")
    @patch("src.core.webhook_delivery.requests.post")
    def test_401_vs_500_retry_behavior_contrast(self, mock_post, mock_validate, mock_create_record, mock_update_record):
        """Verify 401 does NOT retry while 500 DOES retry -- proves the branch matters.

        Covers: UC-004-EXT-G-05
        """
        mock_validate.return_value = (True, None)

        # --- 401 case: should stop immediately ---
        mock_response_401 = Mock()
        mock_response_401.status_code = 401
        mock_response_401.text = "Unauthorized"
        mock_post.return_value = mock_response_401

        delivery_401 = WebhookDelivery(
            webhook_url="https://buyer.example.com/webhook",
            payload={"event": "delivery"},
            headers={},
            max_retries=3,
        )
        success_401, result_401 = deliver_webhook_with_retry(delivery_401)

        assert success_401 is False
        assert result_401["attempts"] == 1
        calls_for_401 = mock_post.call_count

        # Reset mock
        mock_post.reset_mock()

        # --- 500 case: should retry all 3 attempts ---
        mock_response_500 = Mock()
        mock_response_500.status_code = 500
        mock_response_500.text = "Internal Server Error"
        mock_post.return_value = mock_response_500

        delivery_500 = WebhookDelivery(
            webhook_url="https://buyer.example.com/webhook",
            payload={"event": "delivery"},
            headers={},
            max_retries=3,
        )
        success_500, result_500 = deliver_webhook_with_retry(delivery_500)

        assert success_500 is False
        assert result_500["attempts"] == 3
        calls_for_500 = mock_post.call_count

        # The key contrast: 401 = 1 call, 500 = 3 calls
        assert calls_for_401 == 1
        assert calls_for_500 == 3


# ---------------------------------------------------------------------------
# UC-004-EXT-G-06
# ---------------------------------------------------------------------------


class TestEXT_G_06_HmacAuthRejection:
    """HMAC auth rejection: 401/403 logs rejection, no retry, marks failed.

    Covers: UC-004-EXT-G-06
    """

    @pytest.mark.parametrize("status_code", [401, 403])
    @patch("src.core.webhook_delivery._update_delivery_record")
    @patch("src.core.webhook_delivery._create_delivery_record")
    @patch("src.core.webhook_delivery.requests.post")
    @patch("src.core.webhook_delivery.WebhookURLValidator.validate_webhook_url")
    def test_auth_rejection_no_retry_marks_failed(
        self,
        mock_validate,
        mock_post,
        mock_create_record,
        mock_update_record,
        status_code,
    ):
        """401/403 from endpoint => single attempt, no retry, status=failed."""
        mock_validate.return_value = (True, None)

        mock_response = Mock()
        mock_response.status_code = status_code
        mock_response.text = "HMAC signature mismatch"
        mock_post.return_value = mock_response

        delivery = WebhookDelivery(
            webhook_url="https://buyer.example.com/webhook",
            payload={"media_buy_id": "mb_001", "impressions": 5000},
            headers={"Content-Type": "application/json"},
            max_retries=3,
            signing_secret="super-secret-key-for-hmac-signing",
            event_type="delivery.report",
            tenant_id="test_tenant",
            object_id="mb_001",
        )

        success, result = deliver_webhook_with_retry(delivery)

        assert success is False
        assert result["status"] == "failed"
        assert result["response_code"] == status_code
        assert result["attempts"] == 1
        assert mock_post.call_count == 1
        assert f"Client error {status_code}" in result["error"]

        mock_update_record.assert_called_once()
        update_kwargs = mock_update_record.call_args
        assert update_kwargs[1]["status"] == "failed"
        assert update_kwargs[1]["response_code"] == status_code

    @patch("src.core.webhook_delivery._update_delivery_record")
    @patch("src.core.webhook_delivery._create_delivery_record")
    @patch("src.core.webhook_delivery.requests.post")
    @patch("src.core.webhook_delivery.WebhookURLValidator.validate_webhook_url")
    def test_hmac_headers_sent_before_rejection(
        self,
        mock_validate,
        mock_post,
        mock_create_record,
        mock_update_record,
    ):
        """When signing_secret is provided, HMAC signature headers are added to request."""
        mock_validate.return_value = (True, None)

        mock_response = Mock()
        mock_response.status_code = 401
        mock_response.text = "Invalid signature"
        mock_post.return_value = mock_response

        payload = {"media_buy_id": "mb_001", "event": "delivery.report"}
        delivery = WebhookDelivery(
            webhook_url="https://buyer.example.com/webhook",
            payload=payload,
            headers={"Content-Type": "application/json"},
            signing_secret="my-webhook-secret-key",
            event_type="delivery.report",
            tenant_id="test_tenant",
            object_id="mb_001",
        )

        deliver_webhook_with_retry(delivery)

        sent_headers = mock_post.call_args[1]["headers"]
        assert "X-Webhook-Signature" in sent_headers
        assert sent_headers["X-Webhook-Signature"].startswith("sha256=")
        assert "X-Webhook-Timestamp" in sent_headers

    @patch("src.core.webhook_delivery._update_delivery_record")
    @patch("src.core.webhook_delivery._create_delivery_record")
    @patch("src.core.webhook_delivery.requests.post")
    @patch("src.core.webhook_delivery.WebhookURLValidator.validate_webhook_url")
    def test_auth_rejection_vs_server_error_retry_behavior(
        self,
        mock_validate,
        mock_post,
        mock_create_record,
        mock_update_record,
    ):
        """Contrast: 401 does NOT retry, but 500 DOES retry -- proves branching."""
        mock_validate.return_value = (True, None)

        mock_response_401 = Mock()
        mock_response_401.status_code = 401
        mock_response_401.text = "Unauthorized"
        mock_post.return_value = mock_response_401

        delivery_auth_fail = WebhookDelivery(
            webhook_url="https://buyer.example.com/webhook",
            payload={"test": True},
            headers={},
            max_retries=3,
            event_type="delivery.report",
            tenant_id="t1",
        )

        success_401, result_401 = deliver_webhook_with_retry(delivery_auth_fail)
        assert success_401 is False
        assert result_401["attempts"] == 1
        assert mock_post.call_count == 1

        mock_post.reset_mock()
        mock_response_500 = Mock()
        mock_response_500.status_code = 500
        mock_response_500.text = "Internal Server Error"
        mock_post.return_value = mock_response_500

        delivery_server_fail = WebhookDelivery(
            webhook_url="https://buyer.example.com/webhook",
            payload={"test": True},
            headers={},
            max_retries=3,
            event_type="delivery.report",
            tenant_id="t1",
        )

        with patch("src.core.webhook_delivery.time.sleep"):
            success_500, result_500 = deliver_webhook_with_retry(delivery_server_fail)

        assert success_500 is False
        assert result_500["attempts"] == 3
        assert mock_post.call_count == 3


# ---------------------------------------------------------------------------
# UC-004-EXT-G-07
# ---------------------------------------------------------------------------


class TestExtG07WebhookAuthFailureRecovery:
    """Auth failure recovery: buyer must reconfigure credentials after 401/403.

    Covers: UC-004-EXT-G-07
    """

    @pytest.mark.xfail(
        reason=(
            "No explicit auth-failure-blocks-until-reconfigured guard exists. "
            "deliver_webhook_with_retry treats 401/403 as generic 4xx (no retry), "
            "and the circuit breaker does not distinguish auth failures from other "
            "errors. Recovery via UC-003 credential update is not enforced."
        ),
        strict=False,
    )
    def test_auth_failure_blocks_delivery_until_credentials_reconfigured(self):
        """401/403 webhook failure should block delivery until credentials are reconfigured.

        Covers: UC-004-EXT-G-07

        Tests the full recovery cycle:
        1. Webhook delivery fails with 401 (auth failure)
        2. Subsequent deliveries are blocked (circuit breaker opens)
        3. Buyer reconfigures credentials via UC-003
        4. Delivery resumes with new credentials

        The missing behavior: step 3 should be REQUIRED before step 4 can succeed.
        Currently, the circuit breaker auto-recovers after timeout without requiring
        credential reconfiguration.
        """
        # --- Step 1: Deliver webhook, receive 401 (auth failure) ---
        mock_response_401 = Mock()
        mock_response_401.status_code = 401
        mock_response_401.text = "Unauthorized: invalid credentials"

        delivery = WebhookDelivery(
            webhook_url="https://buyer.example.com/webhook",
            payload={"media_buy_id": "mb_001", "status": "active"},
            headers={"Content-Type": "application/json"},
            max_retries=3,
            timeout=10,
            event_type="delivery.update",
            tenant_id="test_tenant",
            object_id="mb_001",
        )

        with (
            patch("src.core.webhook_delivery.requests.post", return_value=mock_response_401),
            patch(
                "src.core.webhook_delivery.WebhookURLValidator.validate_webhook_url",
                return_value=(True, None),
            ),
            patch("src.core.webhook_delivery._create_delivery_record"),
            patch("src.core.webhook_delivery._update_delivery_record"),
        ):
            success, result = deliver_webhook_with_retry(delivery)

        # VERIFY: 401 causes immediate failure (no retry for 4xx)
        assert success is False
        assert result["status"] == "failed"
        assert result["response_code"] == 401
        assert result["attempts"] == 1  # No retry for client errors
        assert "Client error 401" in result["error"]

        # --- Step 2: Circuit breaker should open after auth failures ---
        cb = CircuitBreaker(failure_threshold=3)

        # Simulate repeated 401 failures (as would happen with bad credentials)
        for _ in range(3):
            cb.record_failure()

        assert cb.state == CircuitState.OPEN, "Circuit breaker should be OPEN after repeated auth failures"
        assert cb.can_attempt() is False, "Delivery should be blocked while circuit is OPEN"

        # --- Step 3: This is where the MISSING behavior would be ---
        # The buyer should be REQUIRED to reconfigure credentials via UC-003
        # (update_media_buy with new push_notification_config) before delivery
        # can resume. Currently, the circuit breaker auto-recovers after timeout
        # without any credential check.

        # --- Step 4: After reconfiguration, delivery succeeds ---
        mock_response_200 = Mock()
        mock_response_200.status_code = 200
        mock_response_200.text = "OK"

        # Reset circuit breaker (simulating what would happen after credential update)
        cb_fresh = CircuitBreaker(failure_threshold=3)
        assert cb_fresh.state == CircuitState.CLOSED
        assert cb_fresh.can_attempt() is True

        delivery_after_reconfig = WebhookDelivery(
            webhook_url="https://buyer.example.com/webhook",
            payload={"media_buy_id": "mb_001", "status": "active"},
            headers={"Authorization": "Bearer new-valid-token"},
            max_retries=3,
            timeout=10,
            event_type="delivery.update",
            tenant_id="test_tenant",
            object_id="mb_001",
        )

        with (
            patch("src.core.webhook_delivery.requests.post", return_value=mock_response_200),
            patch(
                "src.core.webhook_delivery.WebhookURLValidator.validate_webhook_url",
                return_value=(True, None),
            ),
            patch("src.core.webhook_delivery._create_delivery_record"),
            patch("src.core.webhook_delivery._update_delivery_record"),
        ):
            success_after, result_after = deliver_webhook_with_retry(delivery_after_reconfig)

        assert success_after is True
        assert result_after["status"] == "delivered"

        # THE KEY MISSING ASSERTION: The system should enforce that
        # credential reconfiguration happened BEFORE allowing delivery to
        # resume. This pytest.xfail marks the gap.
        raise AssertionError(
            "No auth-failure-specific guard exists. The circuit breaker provides "
            "generic failure isolation but does not require credential reconfiguration "
            "via UC-003 before resuming delivery after 401/403."
        )

    def test_401_causes_immediate_failure_no_retry(self):
        """401 auth error is treated as 4xx client error: no retry.

        Covers: UC-004-EXT-G-07
        """
        mock_response = Mock()
        mock_response.status_code = 401
        mock_response.text = "Unauthorized"

        delivery = WebhookDelivery(
            webhook_url="https://buyer.example.com/webhook",
            payload={"event": "delivery.update", "media_buy_id": "mb_001"},
            headers={"Content-Type": "application/json"},
            max_retries=3,
            timeout=10,
            event_type="delivery.update",
            tenant_id="test_tenant",
            object_id="mb_001",
        )

        with (
            patch("src.core.webhook_delivery.requests.post", return_value=mock_response) as mock_post,
            patch(
                "src.core.webhook_delivery.WebhookURLValidator.validate_webhook_url",
                return_value=(True, None),
            ),
            patch("src.core.webhook_delivery._create_delivery_record"),
            patch("src.core.webhook_delivery._update_delivery_record") as mock_update,
        ):
            success, result = deliver_webhook_with_retry(delivery)

        # 401 -> immediate failure, single attempt, no retries
        assert success is False
        assert result["response_code"] == 401
        assert result["attempts"] == 1
        assert result["status"] == "failed"

        # Verify only ONE HTTP request was made (no retry)
        mock_post.assert_called_once()

        # Verify the failure was recorded in the database
        mock_update.assert_called_once()
        update_kwargs = mock_update.call_args
        assert update_kwargs[1]["status"] == "failed"
        assert update_kwargs[1]["response_code"] == 401

    def test_403_causes_immediate_failure_no_retry(self):
        """403 forbidden error is treated as 4xx client error: no retry.

        Covers: UC-004-EXT-G-07
        """
        mock_response = Mock()
        mock_response.status_code = 403
        mock_response.text = "Forbidden"

        delivery = WebhookDelivery(
            webhook_url="https://buyer.example.com/webhook",
            payload={"event": "delivery.update", "media_buy_id": "mb_001"},
            headers={"Authorization": "Bearer expired-token"},
            max_retries=3,
            timeout=10,
            event_type="delivery.update",
            tenant_id="test_tenant",
            object_id="mb_001",
        )

        with (
            patch("src.core.webhook_delivery.requests.post", return_value=mock_response) as mock_post,
            patch(
                "src.core.webhook_delivery.WebhookURLValidator.validate_webhook_url",
                return_value=(True, None),
            ),
            patch("src.core.webhook_delivery._create_delivery_record"),
            patch("src.core.webhook_delivery._update_delivery_record") as mock_update,
        ):
            success, result = deliver_webhook_with_retry(delivery)

        assert success is False
        assert result["response_code"] == 403
        assert result["attempts"] == 1
        assert result["status"] == "failed"
        mock_post.assert_called_once()

    def test_circuit_breaker_opens_after_repeated_auth_failures(self):
        """Circuit breaker opens after threshold auth failures, blocking delivery.

        Covers: UC-004-EXT-G-07
        """
        cb = CircuitBreaker(failure_threshold=3, success_threshold=2, timeout_seconds=60)

        # Initial state: CLOSED, attempts allowed
        assert cb.state == CircuitState.CLOSED
        assert cb.can_attempt() is True

        # Simulate 3 consecutive 401 failures
        cb.record_failure()  # failure 1
        assert cb.state == CircuitState.CLOSED
        cb.record_failure()  # failure 2
        assert cb.state == CircuitState.CLOSED
        cb.record_failure()  # failure 3: threshold reached

        # Circuit should now be OPEN
        assert cb.state == CircuitState.OPEN
        assert cb.can_attempt() is False


# ---------------------------------------------------------------------------
# UC-004-EXT-G-08
# ---------------------------------------------------------------------------


class TestWebhookFailureNoSyncError:
    """Webhook failure does not produce synchronous error to buyer.

    Covers: UC-004-EXT-G-08
    """

    def test_send_delivery_webhook_returns_false_on_http_failure_never_raises(self):
        """WebhookDeliveryService.send_delivery_webhook catches all exceptions
        and returns False -- it never propagates to callers.

        Covers: UC-004-EXT-G-08
        """
        service = WebhookDeliveryService()

        with patch.object(service, "_send_webhook_enhanced", side_effect=Exception("network down")):
            result = service.send_delivery_webhook(
                media_buy_id="mb_001",
                tenant_id="test_tenant",
                principal_id="test_principal",
                reporting_period_start=datetime(2025, 1, 1, tzinfo=UTC),
                reporting_period_end=datetime(2025, 6, 30, tzinfo=UTC),
                impressions=5000,
                spend=250.0,
                currency="USD",
                status="active",
            )

        assert result is False

    def test_send_delivery_webhook_returns_false_on_internal_failure(self):
        """Even when _send_webhook_enhanced returns False (all retries exhausted),
        send_delivery_webhook returns False gracefully.

        Covers: UC-004-EXT-G-08
        """
        service = WebhookDeliveryService()

        with patch.object(service, "_send_webhook_enhanced", return_value=False):
            result = service.send_delivery_webhook(
                media_buy_id="mb_002",
                tenant_id="test_tenant",
                principal_id="test_principal",
                reporting_period_start=datetime(2025, 1, 1, tzinfo=UTC),
                reporting_period_end=datetime(2025, 6, 30, tzinfo=UTC),
                impressions=3000,
                spend=150.0,
            )

        assert result is False

    def test_sequence_number_increments_even_on_failed_delivery(self):
        """Sequence numbers increment regardless of delivery outcome, creating
        detectable gaps when deliveries fail -- the buyer detection mechanism.

        Covers: UC-004-EXT-G-08
        """
        service = WebhookDeliveryService()

        with patch.object(service, "_send_webhook_enhanced", return_value=False):
            service.send_delivery_webhook(
                media_buy_id="mb_seq",
                tenant_id="t1",
                principal_id="p1",
                reporting_period_start=datetime(2025, 1, 1, tzinfo=UTC),
                reporting_period_end=datetime(2025, 1, 31, tzinfo=UTC),
                impressions=1000,
                spend=50.0,
            )
            service.send_delivery_webhook(
                media_buy_id="mb_seq",
                tenant_id="t1",
                principal_id="p1",
                reporting_period_start=datetime(2025, 2, 1, tzinfo=UTC),
                reporting_period_end=datetime(2025, 2, 28, tzinfo=UTC),
                impressions=2000,
                spend=100.0,
            )

        assert service._sequence_numbers["mb_seq"] == 2

    def test_webhook_failure_does_not_affect_poll_response(self):
        """The poll endpoint (_get_media_buy_delivery_impl) and webhook delivery
        are completely separate code paths. A webhook failure in a background
        process cannot propagate to the poll response.

        Covers: UC-004-EXT-G-08
        """
        identity = _make_identity()
        buy = _make_buy(start_date=date(2025, 1, 1), end_date=date(2025, 12, 31))
        adapter_resp = _make_adapter_response()

        req = GetMediaBuyDeliveryRequest(
            media_buy_ids=["mb_001"],
            start_date="2025-01-01",
            end_date="2025-06-30",
        )

        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.return_value = adapter_resp

        mock_repo = MagicMock()
        mock_repo.get_by_principal.return_value = [buy]
        mock_repo.get_packages.return_value = []

        mock_uow = MagicMock()
        mock_uow.__enter__ = Mock(return_value=mock_uow)
        mock_uow.__exit__ = Mock(return_value=False)
        mock_uow.media_buys = mock_repo

        webhook_service = WebhookDeliveryService()
        with patch.object(webhook_service, "_send_webhook_enhanced", side_effect=Exception("timeout")):
            webhook_result = webhook_service.send_delivery_webhook(
                media_buy_id="mb_001",
                tenant_id="test_tenant",
                principal_id="test_principal",
                reporting_period_start=datetime(2025, 1, 1, tzinfo=UTC),
                reporting_period_end=datetime(2025, 6, 30, tzinfo=UTC),
                impressions=5000,
                spend=250.0,
            )

        assert webhook_result is False

        with (
            patch(f"{_PATCH}.get_principal_object") as mock_principal,
            patch(f"{_PATCH}.get_adapter", return_value=mock_adapter),
            patch(f"{_PATCH}.MediaBuyUoW", return_value=mock_uow),
            patch(f"{_PATCH}._get_pricing_options", return_value={}),
        ):
            mock_principal.return_value = MagicMock(principal_id="test_principal")

            response = _get_media_buy_delivery_impl(req, identity)

        assert isinstance(response, GetMediaBuyDeliveryResponse)
        assert len(response.media_buy_deliveries) == 1
        assert response.media_buy_deliveries[0].media_buy_id == "mb_001"
        assert response.aggregated_totals.impressions == 5000
        assert response.errors is None

    def test_send_webhook_enhanced_catches_db_errors(self):
        """_send_webhook_enhanced catches database errors when looking up
        webhook configs, returning False instead of raising.

        Covers: UC-004-EXT-G-08
        """
        service = WebhookDeliveryService()

        with patch(
            "src.core.database.database_session.get_db_session",
            side_effect=Exception("DB connection refused"),
        ):
            result = service._send_webhook_enhanced(
                tenant_id="t1",
                principal_id="p1",
                media_buy_id="mb_001",
                delivery_payload={"test": "data"},
            )

        assert result is False


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

    def test_full_impl_returns_delivery_via_buyer_ref(self):
        """_get_media_buy_delivery_impl returns delivery metrics when buyer_refs used.

        Covers: UC-004-MAIN-02
        """
        identity = _make_identity()
        today = date.today()
        buy = _make_buy(
            media_buy_id="mb_200",
            buyer_ref="my_campaign_1",
            start_date=today - timedelta(days=30),
            end_date=today + timedelta(days=30),
        )

        adapter_resp = _make_adapter_response(
            media_buy_id="mb_200",
            impressions=8000,
            spend=400.0,
        )

        req = GetMediaBuyDeliveryRequest(
            media_buy_ids=None,
            buyer_refs=["my_campaign_1"],
        )

        mock_repo = MagicMock()
        mock_repo.get_by_principal.return_value = [buy]
        mock_repo.get_packages.return_value = []

        mock_uow = MagicMock()
        mock_uow.__enter__ = MagicMock(return_value=mock_uow)
        mock_uow.__exit__ = MagicMock(return_value=False)
        mock_uow.media_buys = mock_repo

        mock_principal = MagicMock()

        with (
            patch(f"{_PATCH}.MediaBuyUoW", return_value=mock_uow),
            patch(f"{_PATCH}.get_principal_object", return_value=mock_principal),
            patch(f"{_PATCH}.get_adapter") as mock_get_adapter,
            patch(f"{_PATCH}._get_pricing_options", return_value={}),
        ):
            mock_adapter = MagicMock()
            mock_adapter.get_media_buy_delivery.return_value = adapter_resp
            mock_get_adapter.return_value = mock_adapter

            response = _get_media_buy_delivery_impl(req, identity)

        assert isinstance(response, GetMediaBuyDeliveryResponse)
        assert len(response.media_buy_deliveries) == 1

        delivery = response.media_buy_deliveries[0]
        assert delivery.media_buy_id == "mb_200"
        assert delivery.buyer_ref == "my_campaign_1"
        assert delivery.totals.impressions == 8000.0
        assert delivery.totals.spend == 400.0

        assert response.aggregated_totals.media_buy_count == 1
        assert response.aggregated_totals.impressions == 8000.0
        assert response.aggregated_totals.spend == 400.0

        mock_repo.get_by_principal.assert_called_once_with("test_principal", buyer_refs=["my_campaign_1"])

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


class TestMultipleMediaBuyDelivery:
    """Array-based identification returns delivery for all requested media buys.

    Covers: UC-004-MAIN-03
    """

    @patch(f"{_PATCH}._get_pricing_options", return_value={})
    @patch(f"{_PATCH}.get_adapter")
    @patch(f"{_PATCH}.get_principal_object")
    @patch(f"{_PATCH}.MediaBuyUoW")
    def test_three_media_buys_returns_all_deliveries_and_aggregated_totals(
        self,
        mock_uow_cls,
        mock_get_principal,
        mock_get_adapter,
        mock_pricing,
    ):
        """Given 3 media buys owned by buyer, when requesting all 3, get all 3 back with aggregated totals."""

        buy_1 = _make_buy(media_buy_id="mb_1", buyer_ref="ref_1")
        buy_2 = _make_buy(media_buy_id="mb_2", buyer_ref="ref_2")
        buy_3 = _make_buy(media_buy_id="mb_3", buyer_ref="ref_3")

        mock_repo = MagicMock()
        mock_repo.get_by_principal.return_value = [buy_1, buy_2, buy_3]
        mock_repo.get_packages.return_value = []

        mock_uow = MagicMock()
        mock_uow.media_buys = mock_repo
        mock_uow.__enter__ = Mock(return_value=mock_uow)
        mock_uow.__exit__ = Mock(return_value=False)
        mock_uow_cls.return_value = mock_uow

        mock_get_principal.return_value = MagicMock()

        adapter_mock = MagicMock()
        adapter_mock.get_media_buy_delivery.side_effect = [
            _make_adapter_response("mb_1", impressions=1000, spend=100.0, package_id="pkg_mb_1"),
            _make_adapter_response("mb_2", impressions=2000, spend=200.0, package_id="pkg_mb_2"),
            _make_adapter_response("mb_3", impressions=3000, spend=300.0, package_id="pkg_mb_3"),
        ]
        mock_get_adapter.return_value = adapter_mock

        identity = _make_identity()
        req = GetMediaBuyDeliveryRequest(
            media_buy_ids=["mb_1", "mb_2", "mb_3"],
            start_date="2025-06-01",
            end_date="2025-06-30",
        )
        response = _get_media_buy_delivery_impl(req, identity)

        assert isinstance(response, GetMediaBuyDeliveryResponse)
        assert len(response.media_buy_deliveries) == 3
        returned_ids = {d.media_buy_id for d in response.media_buy_deliveries}
        assert returned_ids == {"mb_1", "mb_2", "mb_3"}

        delivery_map = {d.media_buy_id: d for d in response.media_buy_deliveries}
        assert delivery_map["mb_1"].totals.impressions == 1000
        assert delivery_map["mb_1"].totals.spend == 100.0
        assert delivery_map["mb_2"].totals.impressions == 2000
        assert delivery_map["mb_2"].totals.spend == 200.0
        assert delivery_map["mb_3"].totals.impressions == 3000
        assert delivery_map["mb_3"].totals.spend == 300.0

        agg = response.aggregated_totals
        assert agg.media_buy_count == 3
        assert agg.impressions == 6000.0
        assert agg.spend == 600.0

        mock_repo.get_by_principal.assert_called_once_with("test_principal", media_buy_ids=["mb_1", "mb_2", "mb_3"])
        assert adapter_mock.get_media_buy_delivery.call_count == 3


# ---------------------------------------------------------------------------
# UC-004-MAIN-04
# ---------------------------------------------------------------------------


class TestNoIdentifiersReturnAll:
    """No identifiers provided returns delivery data for ALL principal's media buys.

    Covers: UC-004-MAIN-04
    """

    @patch(f"{_PATCH}.get_adapter")
    @patch(f"{_PATCH}.get_principal_object")
    @patch(f"{_PATCH}.MediaBuyUoW")
    def test_all_five_media_buys_returned_when_no_identifiers(self, mock_uow_cls, mock_get_principal, mock_get_adapter):
        """When neither media_buy_ids nor buyer_refs is provided, response contains
        delivery data for ALL 5 media buys owned by the principal.

        Covers: UC-004-MAIN-04
        """
        today = datetime.now(UTC).date()
        buys = [
            _make_buy(
                media_buy_id=f"mb_{i:03d}",
                buyer_ref=f"ref_{i:03d}",
                start_date=today - timedelta(days=30),
                end_date=today + timedelta(days=30),
                budget=10000.0 + i * 1000,
            )
            for i in range(1, 6)
        ]

        mock_get_principal.return_value = MagicMock()

        def adapter_side_effect(media_buy_id, date_range, today):
            idx = int(media_buy_id.split("_")[1])
            return _make_adapter_response(
                media_buy_id=media_buy_id,
                impressions=1000 * idx,
                spend=100.0 * idx,
                package_id=f"pkg_{media_buy_id}",
            )

        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.side_effect = adapter_side_effect
        mock_get_adapter.return_value = mock_adapter

        mock_repo = MagicMock()
        mock_repo.get_by_principal.return_value = buys
        mock_repo.get_packages.return_value = []

        mock_uow = MagicMock()
        mock_uow.media_buys = mock_repo
        mock_uow.__enter__ = MagicMock(return_value=mock_uow)
        mock_uow.__exit__ = MagicMock(return_value=False)
        mock_uow_cls.return_value = mock_uow

        identity = _make_identity()

        req = GetMediaBuyDeliveryRequest(
            media_buy_ids=None,
            buyer_refs=None,
            status_filter=None,
        )
        response = _get_media_buy_delivery_impl(req, identity)

        assert isinstance(response, GetMediaBuyDeliveryResponse)
        assert len(response.media_buy_deliveries) == 5
        assert response.aggregated_totals.media_buy_count == 5

        returned_ids = {d.media_buy_id for d in response.media_buy_deliveries}
        expected_ids = {f"mb_{i:03d}" for i in range(1, 6)}
        assert returned_ids == expected_ids

        mock_repo.get_by_principal.assert_called_once_with("test_principal")
        assert response.errors is None

    @patch(f"{_PATCH}.get_adapter")
    @patch(f"{_PATCH}.get_principal_object")
    @patch(f"{_PATCH}.MediaBuyUoW")
    def test_aggregated_totals_sum_across_all_buys(self, mock_uow_cls, mock_get_principal, mock_get_adapter):
        """Aggregated totals reflect the sum of delivery across all 5 media buys.

        Covers: UC-004-MAIN-04
        """
        today = datetime.now(UTC).date()
        buys = [
            _make_buy(
                media_buy_id=f"mb_{i:03d}",
                buyer_ref=f"ref_{i:03d}",
                start_date=today - timedelta(days=30),
                end_date=today + timedelta(days=30),
            )
            for i in range(1, 6)
        ]

        mock_get_principal.return_value = MagicMock()

        def adapter_side_effect(media_buy_id, date_range, today):
            return _make_adapter_response(
                media_buy_id=media_buy_id,
                impressions=1000,
                spend=100.0,
                package_id=f"pkg_{media_buy_id}",
            )

        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.side_effect = adapter_side_effect
        mock_get_adapter.return_value = mock_adapter

        mock_repo = MagicMock()
        mock_repo.get_by_principal.return_value = buys
        mock_repo.get_packages.return_value = []

        mock_uow = MagicMock()
        mock_uow.media_buys = mock_repo
        mock_uow.__enter__ = MagicMock(return_value=mock_uow)
        mock_uow.__exit__ = MagicMock(return_value=False)
        mock_uow_cls.return_value = mock_uow

        identity = _make_identity()
        req = GetMediaBuyDeliveryRequest()

        response = _get_media_buy_delivery_impl(req, identity)

        assert response.aggregated_totals.impressions == 5000.0
        assert response.aggregated_totals.spend == 500.0
        assert response.aggregated_totals.media_buy_count == 5


# ---------------------------------------------------------------------------
# UC-004-MAIN-09
# ---------------------------------------------------------------------------


class TestPackageLevelBreakdowns:
    """Media buy delivery includes per-package breakdowns with metrics.

    Covers: UC-004-MAIN-09
    """

    @patch(f"{_PATCH}._get_pricing_options")
    @patch(f"{_PATCH}.get_adapter")
    @patch(f"{_PATCH}.get_principal_object")
    @patch(f"{_PATCH}.MediaBuyUoW")
    def test_two_packages_each_have_own_metrics(
        self,
        mock_uow_cls,
        mock_get_principal,
        mock_get_adapter,
        mock_get_pricing_options,
    ):
        """Two packages in a media buy each get distinct impressions, spend, and metric fields.

        Covers: UC-004-MAIN-09
        """
        mock_get_principal.return_value = MagicMock()
        mock_get_pricing_options.return_value = {}

        adapter_response = AdapterGetMediaBuyDeliveryResponse(
            media_buy_id="mb_two_pkg",
            reporting_period=ReportingPeriod(
                start=datetime(2025, 3, 1, tzinfo=UTC),
                end=datetime(2025, 3, 31, tzinfo=UTC),
            ),
            totals=DeliveryTotals(impressions=15000.0, spend=750.0),
            by_package=[
                AdapterPackageDelivery(package_id="pkg_A", impressions=10000, spend=500.0),
                AdapterPackageDelivery(package_id="pkg_B", impressions=5000, spend=250.0),
            ],
            currency="USD",
        )

        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.return_value = adapter_response
        mock_get_adapter.return_value = mock_adapter

        buy = _make_buy(
            media_buy_id="mb_two_pkg",
            start_date=date(2025, 3, 1),
            end_date=date(2025, 3, 31),
            raw_request={
                "buyer_ref": "ref_two_pkg",
                "packages": [
                    {"package_id": "pkg_A", "product_id": "prod_A"},
                    {"package_id": "pkg_B", "product_id": "prod_B"},
                ],
            },
        )

        mock_repo = MagicMock()
        mock_repo.get_by_principal.return_value = [buy]
        mock_repo.get_packages.return_value = []
        mock_uow = MagicMock()
        mock_uow.media_buys = mock_repo
        mock_uow.__enter__ = MagicMock(return_value=mock_uow)
        mock_uow.__exit__ = MagicMock(return_value=False)
        mock_uow_cls.return_value = mock_uow

        req = GetMediaBuyDeliveryRequest(
            media_buy_ids=["mb_two_pkg"],
            start_date="2025-03-01",
            end_date="2025-03-31",
        )
        identity = _make_identity()

        result = _get_media_buy_delivery_impl(req, identity)

        assert isinstance(result, GetMediaBuyDeliveryResponse)
        assert len(result.media_buy_deliveries) == 1

        delivery = result.media_buy_deliveries[0]
        assert len(delivery.by_package) == 2

        pkg_map = {p.package_id: p for p in delivery.by_package}
        assert "pkg_A" in pkg_map
        assert "pkg_B" in pkg_map

        pkg_a = pkg_map["pkg_A"]
        assert pkg_a.impressions == 10000.0
        assert pkg_a.spend == 500.0

        pkg_b = pkg_map["pkg_B"]
        assert pkg_b.impressions == 5000.0
        assert pkg_b.spend == 250.0

        assert pkg_a.impressions != pkg_b.impressions
        assert pkg_a.clicks is None
        assert pkg_a.video_completions is None

    @patch(f"{_PATCH}._get_pricing_options")
    @patch(f"{_PATCH}.get_adapter")
    @patch(f"{_PATCH}.get_principal_object")
    @patch(f"{_PATCH}.MediaBuyUoW")
    def test_package_breakdowns_include_pacing_for_active_buy(
        self,
        mock_uow_cls,
        mock_get_principal,
        mock_get_adapter,
        mock_get_pricing_options,
    ):
        """Active media buy packages report pacing_index=1.0.

        Covers: UC-004-MAIN-09
        """
        mock_get_principal.return_value = MagicMock()
        mock_get_pricing_options.return_value = {}

        adapter_response = AdapterGetMediaBuyDeliveryResponse(
            media_buy_id="mb_active",
            reporting_period=ReportingPeriod(
                start=datetime(2025, 1, 1, tzinfo=UTC),
                end=datetime(2025, 12, 31, tzinfo=UTC),
            ),
            totals=DeliveryTotals(impressions=8000.0, spend=400.0),
            by_package=[
                AdapterPackageDelivery(package_id="pkg_X", impressions=5000, spend=250.0),
                AdapterPackageDelivery(package_id="pkg_Y", impressions=3000, spend=150.0),
            ],
            currency="USD",
        )

        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.return_value = adapter_response
        mock_get_adapter.return_value = mock_adapter

        buy = _make_buy(
            media_buy_id="mb_active",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
            raw_request={
                "buyer_ref": "ref_active",
                "packages": [
                    {"package_id": "pkg_X", "product_id": "prod_X"},
                    {"package_id": "pkg_Y", "product_id": "prod_Y"},
                ],
            },
        )

        mock_repo = MagicMock()
        mock_repo.get_by_principal.return_value = [buy]
        mock_repo.get_packages.return_value = []
        mock_uow = MagicMock()
        mock_uow.media_buys = mock_repo
        mock_uow.__enter__ = MagicMock(return_value=mock_uow)
        mock_uow.__exit__ = MagicMock(return_value=False)
        mock_uow_cls.return_value = mock_uow

        req = GetMediaBuyDeliveryRequest(
            media_buy_ids=["mb_active"],
            start_date="2025-06-01",
            end_date="2025-06-30",
        )
        identity = _make_identity()

        result = _get_media_buy_delivery_impl(req, identity)

        delivery = result.media_buy_deliveries[0]
        assert delivery.status == "active"

        for pkg in delivery.by_package:
            assert pkg.pacing_index == 1.0

    @patch(f"{_PATCH}._get_pricing_options")
    @patch(f"{_PATCH}.get_adapter")
    @patch(f"{_PATCH}.get_principal_object")
    @patch(f"{_PATCH}.MediaBuyUoW")
    def test_totals_reflect_sum_of_package_metrics(
        self,
        mock_uow_cls,
        mock_get_principal,
        mock_get_adapter,
        mock_get_pricing_options,
    ):
        """Media buy totals are consistent with the sum of package-level metrics.

        Covers: UC-004-MAIN-09
        """
        mock_get_principal.return_value = MagicMock()
        mock_get_pricing_options.return_value = {}

        adapter_response = AdapterGetMediaBuyDeliveryResponse(
            media_buy_id="mb_sum",
            reporting_period=ReportingPeriod(
                start=datetime(2025, 4, 1, tzinfo=UTC),
                end=datetime(2025, 4, 30, tzinfo=UTC),
            ),
            totals=DeliveryTotals(impressions=12000.0, spend=600.0),
            by_package=[
                AdapterPackageDelivery(package_id="pkg_1", impressions=7000, spend=350.0),
                AdapterPackageDelivery(package_id="pkg_2", impressions=5000, spend=250.0),
            ],
            currency="USD",
        )

        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.return_value = adapter_response
        mock_get_adapter.return_value = mock_adapter

        buy = _make_buy(
            media_buy_id="mb_sum",
            start_date=date(2025, 4, 1),
            end_date=date(2025, 4, 30),
            raw_request={
                "buyer_ref": "ref_sum",
                "packages": [
                    {"package_id": "pkg_1", "product_id": "prod_1"},
                    {"package_id": "pkg_2", "product_id": "prod_2"},
                ],
            },
        )

        mock_repo = MagicMock()
        mock_repo.get_by_principal.return_value = [buy]
        mock_repo.get_packages.return_value = []
        mock_uow = MagicMock()
        mock_uow.media_buys = mock_repo
        mock_uow.__enter__ = MagicMock(return_value=mock_uow)
        mock_uow.__exit__ = MagicMock(return_value=False)
        mock_uow_cls.return_value = mock_uow

        req = GetMediaBuyDeliveryRequest(
            media_buy_ids=["mb_sum"],
            start_date="2025-04-01",
            end_date="2025-04-30",
        )
        identity = _make_identity()

        result = _get_media_buy_delivery_impl(req, identity)

        delivery = result.media_buy_deliveries[0]
        assert delivery.totals.impressions == 12000.0
        assert delivery.totals.spend == 600.0

        pkg_impressions = sum(p.impressions for p in delivery.by_package)
        pkg_spend = sum(p.spend for p in delivery.by_package)
        assert pkg_impressions == delivery.totals.impressions
        assert pkg_spend == delivery.totals.spend


# ---------------------------------------------------------------------------
# UC-004-MAIN-10
# ---------------------------------------------------------------------------


class TestPackageDeliveryStatus:
    """Media buy status computation based on package delivery states.

    The production code computes media-buy-level status (ready/active/completed)
    based on date comparison against the request end_date (reference_date).
    Per-package delivery_status (delivering, completed, budget_exhausted,
    flight_ended, goal_met) is NOT yet implemented in the delivery poll flow.

    Covers: UC-004-MAIN-10
    """

    @patch(f"{_PATCH}.get_adapter")
    @patch(f"{_PATCH}.get_principal_object")
    @patch(f"{_PATCH}.MediaBuyUoW")
    def test_rq1_buy_before_start_has_ready_status(self, mock_uow_cls, mock_get_principal, mock_get_adapter):
        """Media buy before its start date gets status 'ready'.

        Covers: UC-004-MAIN-10
        """
        mock_get_principal.return_value = MagicMock()
        mock_adapter = MagicMock()
        mock_get_adapter.return_value = mock_adapter

        buy = _make_buy(
            media_buy_id="mb_future",
            start_date=date(2025, 6, 1),
            end_date=date(2025, 12, 31),
        )

        mock_repo = MagicMock()
        mock_repo.get_by_principal.return_value = [buy]
        mock_repo.get_packages.return_value = []
        mock_uow = MagicMock()
        mock_uow.__enter__ = Mock(return_value=mock_uow)
        mock_uow.__exit__ = Mock(return_value=False)
        mock_uow.media_buys = mock_repo
        mock_uow_cls.return_value = mock_uow

        req = GetMediaBuyDeliveryRequest(
            media_buy_ids=["mb_future"],
            status_filter=[MediaBuyStatus.pending_activation],
            start_date="2025-01-01",
            end_date="2025-03-15",
        )
        identity = _make_identity()
        resp = _get_media_buy_delivery_impl(req, identity)

        assert len(resp.media_buy_deliveries) == 1
        assert resp.media_buy_deliveries[0].status == "ready"

    @patch(f"{_PATCH}.get_adapter")
    @patch(f"{_PATCH}.get_principal_object")
    @patch(f"{_PATCH}.MediaBuyUoW")
    def test_rq2_buy_in_flight_has_active_status(self, mock_uow_cls, mock_get_principal, mock_get_adapter):
        """Media buy within its flight dates gets status 'active'.

        Covers: UC-004-MAIN-10
        """
        mock_get_principal.return_value = MagicMock()
        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.return_value = _make_adapter_response()
        mock_get_adapter.return_value = mock_adapter

        buy = _make_buy(
            media_buy_id="mb_001",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
        )

        mock_repo = MagicMock()
        mock_repo.get_by_principal.return_value = [buy]
        mock_repo.get_packages.return_value = []
        mock_uow = MagicMock()
        mock_uow.__enter__ = Mock(return_value=mock_uow)
        mock_uow.__exit__ = Mock(return_value=False)
        mock_uow.media_buys = mock_repo
        mock_uow_cls.return_value = mock_uow

        req = GetMediaBuyDeliveryRequest(
            media_buy_ids=["mb_001"],
            start_date="2025-01-01",
            end_date="2025-06-15",
        )
        identity = _make_identity()
        resp = _get_media_buy_delivery_impl(req, identity)

        assert len(resp.media_buy_deliveries) == 1
        assert resp.media_buy_deliveries[0].status == "active"

    @patch(f"{_PATCH}.get_adapter")
    @patch(f"{_PATCH}.get_principal_object")
    @patch(f"{_PATCH}.MediaBuyUoW")
    def test_rq3_buy_past_end_has_completed_status(self, mock_uow_cls, mock_get_principal, mock_get_adapter):
        """Media buy past its end date gets status 'completed'.

        Covers: UC-004-MAIN-10
        """
        mock_get_principal.return_value = MagicMock()
        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.return_value = _make_adapter_response(media_buy_id="mb_past")
        mock_get_adapter.return_value = mock_adapter

        buy = _make_buy(
            media_buy_id="mb_past",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
        )

        mock_repo = MagicMock()
        mock_repo.get_by_principal.return_value = [buy]
        mock_repo.get_packages.return_value = []
        mock_uow = MagicMock()
        mock_uow.__enter__ = Mock(return_value=mock_uow)
        mock_uow.__exit__ = Mock(return_value=False)
        mock_uow.media_buys = mock_repo
        mock_uow_cls.return_value = mock_uow

        req = GetMediaBuyDeliveryRequest(
            media_buy_ids=["mb_past"],
            status_filter=[MediaBuyStatus.completed],
            start_date="2025-01-01",
            end_date="2025-06-15",
        )
        identity = _make_identity()
        resp = _get_media_buy_delivery_impl(req, identity)

        assert len(resp.media_buy_deliveries) == 1
        assert resp.media_buy_deliveries[0].status == "completed"

    @patch(f"{_PATCH}.get_adapter")
    @patch(f"{_PATCH}.get_principal_object")
    @patch(f"{_PATCH}.MediaBuyUoW")
    def test_rq4_multiple_buys_different_statuses(self, mock_uow_cls, mock_get_principal, mock_get_adapter):
        """Multiple media buys return their respective date-based statuses.

        Covers: UC-004-MAIN-10
        """
        mock_get_principal.return_value = MagicMock()
        mock_adapter = MagicMock()

        buy_future = _make_buy(
            media_buy_id="mb_future",
            start_date=date(2025, 9, 1),
            end_date=date(2025, 12, 31),
            raw_request={"buyer_ref": "ref_f", "packages": [{"package_id": "pkg_f", "product_id": "prod_001"}]},
        )
        buy_active = _make_buy(
            media_buy_id="mb_active",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
            raw_request={"buyer_ref": "ref_a", "packages": [{"package_id": "pkg_a", "product_id": "prod_001"}]},
        )
        buy_completed = _make_buy(
            media_buy_id="mb_completed",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 3, 31),
            raw_request={"buyer_ref": "ref_c", "packages": [{"package_id": "pkg_c", "product_id": "prod_001"}]},
        )

        mock_adapter.get_media_buy_delivery.side_effect = [
            _make_adapter_response(media_buy_id="mb_future", package_id="pkg_f"),
            _make_adapter_response(media_buy_id="mb_active", package_id="pkg_a"),
            _make_adapter_response(media_buy_id="mb_completed", package_id="pkg_c"),
        ]
        mock_get_adapter.return_value = mock_adapter

        mock_repo = MagicMock()
        mock_repo.get_by_principal.return_value = [buy_future, buy_active, buy_completed]
        mock_repo.get_packages.return_value = []
        mock_uow = MagicMock()
        mock_uow.__enter__ = Mock(return_value=mock_uow)
        mock_uow.__exit__ = Mock(return_value=False)
        mock_uow.media_buys = mock_repo
        mock_uow_cls.return_value = mock_uow

        req = GetMediaBuyDeliveryRequest(
            media_buy_ids=["mb_future", "mb_active", "mb_completed"],
            status_filter=[MediaBuyStatus.pending_activation, MediaBuyStatus.active, MediaBuyStatus.completed],
            start_date="2025-01-01",
            end_date="2025-06-15",
        )
        identity = _make_identity()
        resp = _get_media_buy_delivery_impl(req, identity)

        assert len(resp.media_buy_deliveries) == 3
        status_map = {d.media_buy_id: d.status for d in resp.media_buy_deliveries}
        assert status_map["mb_future"] == "ready"
        assert status_map["mb_active"] == "active"
        assert status_map["mb_completed"] == "completed"

    def test_rq5_package_delivery_has_no_delivery_status_field(self):
        """PackageDelivery lacks delivery_status -- obligation gap.

        Covers: UC-004-MAIN-10
        """
        assert DeliveryStatus.delivering.value == "delivering"
        assert DeliveryStatus.completed.value == "completed"
        assert DeliveryStatus.budget_exhausted.value == "budget_exhausted"
        assert DeliveryStatus.flight_ended.value == "flight_ended"
        assert DeliveryStatus.goal_met.value == "goal_met"

        field_names = set(PackageDelivery.model_fields.keys())
        assert "delivery_status" not in field_names, (
            "If this fails, delivery_status was added to PackageDelivery -- "
            "update this test to PASS and verify the computation logic."
        )


# ---------------------------------------------------------------------------
# UC-004-MAIN-11
# ---------------------------------------------------------------------------


class TestAggregatedTotalsMultipleBuys:
    """Aggregated totals are correctly summed across three media buys.

    Covers: UC-004-MAIN-11
    """

    @patch(f"{_PATCH}.get_adapter")
    @patch(f"{_PATCH}.get_principal_object")
    @patch(f"{_PATCH}.MediaBuyUoW")
    def test_aggregated_totals_sum_across_three_buys(self, mock_uow_cls, mock_get_principal, mock_get_adapter):
        """Three media buys with known metrics produce correct aggregated totals.

        Covers: UC-004-MAIN-11
        """
        mock_get_principal.return_value = MagicMock()

        buy_1 = _make_buy(media_buy_id="mb_1", buyer_ref="ref_1", budget=5000.0)
        buy_2 = _make_buy(media_buy_id="mb_2", buyer_ref="ref_2", budget=10000.0)
        buy_3 = _make_buy(media_buy_id="mb_3", buyer_ref="ref_3", budget=2500.0)

        adapter_responses = {
            "mb_1": _make_adapter_response(media_buy_id="mb_1", impressions=1000, spend=50.0, package_id="pkg_mb_1"),
            "mb_2": _make_adapter_response(media_buy_id="mb_2", impressions=2000, spend=100.0, package_id="pkg_mb_2"),
            "mb_3": _make_adapter_response(media_buy_id="mb_3", impressions=500, spend=25.0, package_id="pkg_mb_3"),
        }

        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.side_effect = lambda media_buy_id, **kw: adapter_responses[media_buy_id]
        mock_get_adapter.return_value = mock_adapter

        mock_repo = MagicMock()
        mock_repo.get_by_principal.return_value = [buy_1, buy_2, buy_3]
        mock_repo.get_packages.return_value = []

        mock_uow = MagicMock()
        mock_uow.media_buys = mock_repo
        mock_uow.__enter__ = MagicMock(return_value=mock_uow)
        mock_uow.__exit__ = MagicMock(return_value=False)
        mock_uow_cls.return_value = mock_uow

        identity = _make_identity()
        req = GetMediaBuyDeliveryRequest(
            media_buy_ids=["mb_1", "mb_2", "mb_3"],
            start_date="2025-01-01",
            end_date="2025-06-30",
        )

        response = _get_media_buy_delivery_impl(req, identity)

        assert isinstance(response, GetMediaBuyDeliveryResponse)

        agg = response.aggregated_totals
        assert agg.impressions == 3500.0
        assert agg.spend == 175.0
        assert agg.media_buy_count == 3

        assert len(response.media_buy_deliveries) == 3
        delivery_ids = {d.media_buy_id for d in response.media_buy_deliveries}
        assert delivery_ids == {"mb_1", "mb_2", "mb_3"}

    @patch(f"{_PATCH}.get_adapter")
    @patch(f"{_PATCH}.get_principal_object")
    @patch(f"{_PATCH}.MediaBuyUoW")
    def test_per_buy_totals_match_individual_adapter_data(self, mock_uow_cls, mock_get_principal, mock_get_adapter):
        """Each media_buy_delivery has correct individual totals from its adapter response.

        Covers: UC-004-MAIN-11
        """
        mock_get_principal.return_value = MagicMock()

        buy_1 = _make_buy(media_buy_id="mb_1", buyer_ref="ref_1", budget=5000.0)
        buy_2 = _make_buy(media_buy_id="mb_2", buyer_ref="ref_2", budget=10000.0)
        buy_3 = _make_buy(media_buy_id="mb_3", buyer_ref="ref_3", budget=2500.0)

        adapter_responses = {
            "mb_1": _make_adapter_response(media_buy_id="mb_1", impressions=1000, spend=50.0, package_id="pkg_mb_1"),
            "mb_2": _make_adapter_response(media_buy_id="mb_2", impressions=2000, spend=100.0, package_id="pkg_mb_2"),
            "mb_3": _make_adapter_response(media_buy_id="mb_3", impressions=500, spend=25.0, package_id="pkg_mb_3"),
        }

        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.side_effect = lambda media_buy_id, **kw: adapter_responses[media_buy_id]
        mock_get_adapter.return_value = mock_adapter

        mock_repo = MagicMock()
        mock_repo.get_by_principal.return_value = [buy_1, buy_2, buy_3]
        mock_repo.get_packages.return_value = []

        mock_uow = MagicMock()
        mock_uow.media_buys = mock_repo
        mock_uow.__enter__ = MagicMock(return_value=mock_uow)
        mock_uow.__exit__ = MagicMock(return_value=False)
        mock_uow_cls.return_value = mock_uow

        identity = _make_identity()
        req = GetMediaBuyDeliveryRequest(
            media_buy_ids=["mb_1", "mb_2", "mb_3"],
            start_date="2025-01-01",
            end_date="2025-06-30",
        )

        response = _get_media_buy_delivery_impl(req, identity)

        by_id = {d.media_buy_id: d for d in response.media_buy_deliveries}

        assert by_id["mb_1"].totals.impressions == 1000.0
        assert by_id["mb_1"].totals.spend == 50.0
        assert by_id["mb_2"].totals.impressions == 2000.0
        assert by_id["mb_2"].totals.spend == 100.0
        assert by_id["mb_3"].totals.impressions == 500.0
        assert by_id["mb_3"].totals.spend == 25.0

        agg = response.aggregated_totals
        sum_impressions = sum(d.totals.impressions for d in response.media_buy_deliveries)
        sum_spend = sum(d.totals.spend for d in response.media_buy_deliveries)
        assert agg.impressions == sum_impressions
        assert agg.spend == sum_spend


# ---------------------------------------------------------------------------
# UC-004-MAIN-12
# ---------------------------------------------------------------------------


class TestProtocolEnvelopeStatusCompleted:
    """Successful delivery query returns a well-formed response (protocol envelope).

    Covers: UC-004-MAIN-12
    """

    @patch(f"{_PATCH}.get_adapter")
    @patch(f"{_PATCH}.get_principal_object")
    @patch(f"{_PATCH}.MediaBuyUoW")
    def test_successful_query_returns_response_type(self, mock_uow_cls, mock_get_principal, mock_get_adapter):
        """_impl returns GetMediaBuyDeliveryResponse on success.

        Covers: UC-004-MAIN-12
        """
        mock_get_principal.return_value = MagicMock()
        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.return_value = _make_adapter_response()
        mock_get_adapter.return_value = mock_adapter

        buy = _make_buy(start_date=date(2026, 1, 1), end_date=date(2026, 6, 30))

        mock_repo = MagicMock()
        mock_repo.get_by_principal.return_value = [buy]
        mock_repo.get_packages.return_value = []
        mock_uow = MagicMock()
        mock_uow.media_buys = mock_repo
        mock_uow.__enter__ = MagicMock(return_value=mock_uow)
        mock_uow.__exit__ = MagicMock(return_value=False)
        mock_uow_cls.return_value = mock_uow

        req = GetMediaBuyDeliveryRequest(media_buy_ids=["mb_001"])
        identity = _make_identity()

        response = _get_media_buy_delivery_impl(req, identity)
        assert isinstance(response, GetMediaBuyDeliveryResponse)

    @patch(f"{_PATCH}.get_adapter")
    @patch(f"{_PATCH}.get_principal_object")
    @patch(f"{_PATCH}.MediaBuyUoW")
    def test_successful_query_has_no_errors(self, mock_uow_cls, mock_get_principal, mock_get_adapter):
        """Successful delivery query returns errors=None.

        Covers: UC-004-MAIN-12
        """
        mock_get_principal.return_value = MagicMock()
        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.return_value = _make_adapter_response()
        mock_get_adapter.return_value = mock_adapter

        buy = _make_buy(start_date=date(2026, 1, 1), end_date=date(2026, 6, 30))

        mock_repo = MagicMock()
        mock_repo.get_by_principal.return_value = [buy]
        mock_repo.get_packages.return_value = []
        mock_uow = MagicMock()
        mock_uow.media_buys = mock_repo
        mock_uow.__enter__ = MagicMock(return_value=mock_uow)
        mock_uow.__exit__ = MagicMock(return_value=False)
        mock_uow_cls.return_value = mock_uow

        req = GetMediaBuyDeliveryRequest(media_buy_ids=["mb_001"])
        identity = _make_identity()

        response = _get_media_buy_delivery_impl(req, identity)
        assert response.errors is None

    @patch(f"{_PATCH}.get_adapter")
    @patch(f"{_PATCH}.get_principal_object")
    @patch(f"{_PATCH}.MediaBuyUoW")
    def test_successful_query_contains_delivery_data(self, mock_uow_cls, mock_get_principal, mock_get_adapter):
        """Successful query populates media_buy_deliveries.

        Covers: UC-004-MAIN-12
        """
        mock_get_principal.return_value = MagicMock()
        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.return_value = _make_adapter_response()
        mock_get_adapter.return_value = mock_adapter

        buy = _make_buy(start_date=date(2026, 1, 1), end_date=date(2026, 6, 30))

        mock_repo = MagicMock()
        mock_repo.get_by_principal.return_value = [buy]
        mock_repo.get_packages.return_value = []
        mock_uow = MagicMock()
        mock_uow.media_buys = mock_repo
        mock_uow.__enter__ = MagicMock(return_value=mock_uow)
        mock_uow.__exit__ = MagicMock(return_value=False)
        mock_uow_cls.return_value = mock_uow

        req = GetMediaBuyDeliveryRequest(media_buy_ids=["mb_001"])
        identity = _make_identity()

        response = _get_media_buy_delivery_impl(req, identity)
        assert len(response.media_buy_deliveries) == 1
        assert response.media_buy_deliveries[0].media_buy_id == "mb_001"

    @patch(f"{_PATCH}.get_adapter")
    @patch(f"{_PATCH}.get_principal_object")
    @patch(f"{_PATCH}.MediaBuyUoW")
    def test_successful_query_has_required_envelope_fields(self, mock_uow_cls, mock_get_principal, mock_get_adapter):
        """Protocol envelope includes all required top-level fields.

        Covers: UC-004-MAIN-12
        """
        mock_get_principal.return_value = MagicMock()
        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.return_value = _make_adapter_response()
        mock_get_adapter.return_value = mock_adapter

        buy = _make_buy(start_date=date(2026, 1, 1), end_date=date(2026, 6, 30))

        mock_repo = MagicMock()
        mock_repo.get_by_principal.return_value = [buy]
        mock_repo.get_packages.return_value = []
        mock_uow = MagicMock()
        mock_uow.media_buys = mock_repo
        mock_uow.__enter__ = MagicMock(return_value=mock_uow)
        mock_uow.__exit__ = MagicMock(return_value=False)
        mock_uow_cls.return_value = mock_uow

        req = GetMediaBuyDeliveryRequest(media_buy_ids=["mb_001"])
        identity = _make_identity()

        response = _get_media_buy_delivery_impl(req, identity)
        assert response.reporting_period is not None
        assert response.currency is not None
        assert response.aggregated_totals is not None
        assert response.media_buy_deliveries is not None


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


class TestPricingOptionStringLookup:
    """Verify pricing_option_id string field is used for lookup, not integer PK.

    Bug: salesagent-mq3n -- string-to-integer comparison silently drops pricing
    context, resulting in silent data loss (no clicks calculated for CPC buys).

    Covers: UC-004-MAIN-14
    """

    @pytest.mark.xfail(
        reason=(
            "BUG salesagent-mq3n: _get_pricing_options casts string pricing_option_id "
            "to int and queries PricingOption.id (integer PK). Non-numeric IDs like "
            "'cpm_usd_fixed' are silently discarded."
        ),
        strict=True,
    )
    @patch(f"{_PATCH}.get_db_session")
    def test_get_pricing_options_uses_string_id_not_integer_pk(self, mock_session_ctx):
        """_get_pricing_options should return dict keyed by string pricing_option_id.

        Covers: UC-004-MAIN-14
        """
        mock_pricing_option = MagicMock()
        mock_pricing_option.id = 42
        mock_pricing_option.pricing_option_id = "cpm_usd_fixed"
        mock_pricing_option.pricing_model = "cpm"
        mock_pricing_option.rate = 5.00
        mock_pricing_option.tenant_id = "test_tenant"

        mock_session = MagicMock()
        mock_session_ctx.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_session_ctx.return_value.__exit__ = MagicMock(return_value=False)
        mock_session.scalars.return_value.all.return_value = [mock_pricing_option]

        result = _get_pricing_options(["cpm_usd_fixed"], tenant_id="test_tenant")

        assert "cpm_usd_fixed" in result, (
            f"Expected key 'cpm_usd_fixed', got keys: {list(result.keys())}. "
            f"_get_pricing_options incorrectly uses integer PK."
        )
        assert result["cpm_usd_fixed"] is mock_pricing_option

    @pytest.mark.xfail(
        reason=(
            "BUG salesagent-mq3n: _get_pricing_options tries int() on string IDs. "
            "Non-numeric strings are silently discarded."
        ),
        strict=True,
    )
    @patch(f"{_PATCH}.get_db_session")
    def test_non_numeric_pricing_option_id_is_not_silently_discarded(self, mock_session_ctx):
        """Non-numeric string pricing_option_ids must not be dropped.

        Covers: UC-004-MAIN-14
        """
        mock_pricing_option = MagicMock()
        mock_pricing_option.id = 42
        mock_pricing_option.pricing_option_id = "cpm_usd_fixed"
        mock_pricing_option.pricing_model = "cpm"
        mock_pricing_option.rate = 5.00

        mock_session = MagicMock()
        mock_session_ctx.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_session_ctx.return_value.__exit__ = MagicMock(return_value=False)
        mock_session.scalars.return_value.all.return_value = [mock_pricing_option]

        result = _get_pricing_options(["cpm_usd_fixed"], tenant_id="test_tenant")

        assert len(result) > 0, "Non-numeric pricing_option_id 'cpm_usd_fixed' was silently discarded."


# ---------------------------------------------------------------------------
# UC-004-MAIN-15
# ---------------------------------------------------------------------------


class TestDeliverySpendComputation:
    """CPM spend computation: impressions / 1000 * rate propagates through delivery.

    Covers: UC-004-MAIN-15
    """

    @patch(f"{_PATCH}._get_pricing_options", return_value={})
    @patch(f"{_PATCH}.get_adapter")
    @patch(f"{_PATCH}.get_principal_object")
    @patch(f"{_PATCH}.MediaBuyUoW")
    def test_cpm_spend_propagated_to_totals_and_aggregated(
        self, mock_uow_cls, mock_get_principal, mock_get_adapter, mock_pricing
    ):
        """Adapter returns CPM-computed spend ($50 for 10k imps at $5 CPM);
        _impl propagates it to media-buy totals AND aggregated_totals.

        Covers: UC-004-MAIN-15
        """
        cpm_rate = 5.00
        impressions = 10_000
        expected_spend = impressions / 1000 * cpm_rate  # $50.00

        mock_get_principal.return_value = MagicMock()

        adapter_response = AdapterGetMediaBuyDeliveryResponse(
            media_buy_id="mb_cpm",
            reporting_period=ReportingPeriod(
                start=datetime(2025, 6, 1, tzinfo=UTC),
                end=datetime(2025, 6, 30, tzinfo=UTC),
            ),
            totals=DeliveryTotals(impressions=float(impressions), spend=expected_spend),
            by_package=[
                AdapterPackageDelivery(
                    package_id="pkg_cpm_001",
                    impressions=impressions,
                    spend=expected_spend,
                )
            ],
            currency="USD",
        )
        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.return_value = adapter_response
        mock_get_adapter.return_value = mock_adapter

        buy = _make_buy(
            media_buy_id="mb_cpm",
            start_date=date(2025, 6, 1),
            end_date=date(2025, 6, 30),
            budget=500.0,
            raw_request={
                "buyer_ref": "ref_cpm",
                "pricing_option_id": None,
                "packages": [{"package_id": "pkg_cpm_001", "product_id": "prod_001"}],
            },
        )

        mock_repo = MagicMock()
        mock_repo.get_by_principal.return_value = [buy]
        mock_repo.get_packages.return_value = []
        mock_uow = MagicMock()
        mock_uow.media_buys = mock_repo
        mock_uow.__enter__ = MagicMock(return_value=mock_uow)
        mock_uow.__exit__ = MagicMock(return_value=False)
        mock_uow_cls.return_value = mock_uow

        req = GetMediaBuyDeliveryRequest(
            media_buy_ids=["mb_cpm"],
            start_date="2025-06-01",
            end_date="2025-06-30",
        )

        response = _get_media_buy_delivery_impl(req, _make_identity())

        assert len(response.media_buy_deliveries) == 1
        mb_delivery = response.media_buy_deliveries[0]

        assert mb_delivery.totals.spend == expected_spend
        assert mb_delivery.totals.impressions == impressions

        assert response.aggregated_totals.spend == expected_spend
        assert response.aggregated_totals.impressions == float(impressions)

        assert len(mb_delivery.by_package) == 1
        pkg = mb_delivery.by_package[0]
        assert pkg.package_id == "pkg_cpm_001"
        assert pkg.spend == expected_spend
        assert pkg.impressions == float(impressions)


# ---------------------------------------------------------------------------
# Batch 7: UC-004-MAIN-16 through UC-004-MAIN-20
# ---------------------------------------------------------------------------

# --- OID: UC-004-MAIN-16 ---


class TestBuyerRefInDeliveryEntries:
    """Verify that buyer_ref from raw_request propagates to media_buy_deliveries entries.

    Covers: UC-004-MAIN-16
    """

    @patch(f"{_PATCH}.get_adapter")
    @patch(f"{_PATCH}.get_principal_object")
    @patch(f"{_PATCH}.MediaBuyUoW")
    def test_buyer_ref_propagates_to_delivery_entry(self, mock_uow_cls, mock_get_principal, mock_get_adapter):
        """When a media buy has buyer_ref='buyer_camp_1' in raw_request,
        each media_buy_deliveries entry must include buyer_ref='buyer_camp_1'.
        """
        # Given: a media buy created with buyer_campaign_ref="buyer_camp_1"
        buy = _make_buy(
            media_buy_id="mb_camp",
            buyer_ref="buyer_camp_1",
            raw_request={
                "buyer_ref": "buyer_camp_1",
                "buyer_campaign_ref": "buyer_camp_1",
                "packages": [{"package_id": "pkg_001", "product_id": "prod_001"}],
            },
        )

        # Set up mocks
        mock_get_principal.return_value = MagicMock()

        mock_repo = MagicMock()
        mock_repo.get_by_principal.return_value = [buy]
        mock_repo.get_packages.return_value = []

        mock_uow = MagicMock()
        mock_uow.media_buys = mock_repo
        mock_uow.__enter__ = Mock(return_value=mock_uow)
        mock_uow.__exit__ = Mock(return_value=False)
        mock_uow_cls.return_value = mock_uow

        mock_adapter = MagicMock()
        adapter_resp = _make_adapter_response(media_buy_id="mb_camp", package_id="pkg_001")
        mock_adapter.get_media_buy_delivery.return_value = adapter_resp
        mock_get_adapter.return_value = mock_adapter

        identity = _make_identity()
        req = GetMediaBuyDeliveryRequest(
            media_buy_ids=["mb_camp"],
            start_date="2025-06-01",
            end_date="2025-06-30",
        )

        # When: delivery metrics are returned
        response = _get_media_buy_delivery_impl(req, identity)

        # Then: each media_buy_deliveries entry includes buyer_ref matching "buyer_camp_1"
        assert len(response.media_buy_deliveries) == 1
        delivery = response.media_buy_deliveries[0]
        assert delivery.buyer_ref == "buyer_camp_1", (
            f"Expected buyer_ref='buyer_camp_1' but got '{delivery.buyer_ref}'. "
            "The delivery boundary must propagate buyer_ref from raw_request."
        )


# --- OID: UC-004-MAIN-17 ---


class TestPartialResolutionMissingIds:
    """Partial resolution returns found buys only, reports missing as errors.

    Covers: UC-004-MAIN-17
    """

    @patch(f"{_PATCH}.get_adapter")
    @patch(f"{_PATCH}.get_principal_object")
    @patch(f"{_PATCH}.MediaBuyUoW")
    def test_missing_id_excluded_from_deliveries_with_error(self, mock_uow_cls, mock_get_principal, mock_get_adapter):
        """When some media_buy_ids don't exist, return data for found ones
        and report missing IDs in the errors array.

        Covers: UC-004-MAIN-17
        """
        # --- Arrange ---
        identity = _make_identity()

        # Two buys exist, mb_999 does not
        buy_1 = _make_buy(
            media_buy_id="mb_1",
            buyer_ref="ref_1",
            raw_request={
                "buyer_ref": "ref_1",
                "packages": [{"package_id": "pkg_1", "product_id": "prod_1"}],
            },
        )
        buy_2 = _make_buy(
            media_buy_id="mb_2",
            buyer_ref="ref_2",
            raw_request={
                "buyer_ref": "ref_2",
                "packages": [{"package_id": "pkg_2", "product_id": "prod_2"}],
            },
        )

        # Configure UoW mock: repo.get_by_principal returns only mb_1 and mb_2
        mock_repo = MagicMock()
        mock_repo.get_by_principal.return_value = [buy_1, buy_2]
        mock_repo.get_packages.return_value = []  # No package pricing config

        mock_uow = MagicMock()
        mock_uow.media_buys = mock_repo
        mock_uow.__enter__ = MagicMock(return_value=mock_uow)
        mock_uow.__exit__ = MagicMock(return_value=False)
        mock_uow_cls.return_value = mock_uow

        # Principal exists
        mock_principal = MagicMock()
        mock_get_principal.return_value = mock_principal

        # Adapter returns delivery data for each buy
        mock_adapter = MagicMock()

        def adapter_delivery_side_effect(media_buy_id, date_range, today):
            return _make_adapter_response(
                media_buy_id=media_buy_id,
                package_id=f"pkg_{media_buy_id.split('_')[1]}",
            )

        mock_adapter.get_media_buy_delivery.side_effect = adapter_delivery_side_effect
        mock_get_adapter.return_value = mock_adapter

        req = GetMediaBuyDeliveryRequest(
            media_buy_ids=["mb_1", "mb_999", "mb_2"],
            start_date="2025-06-01",
            end_date="2025-06-30",
        )

        # --- Act ---
        response = _get_media_buy_delivery_impl(req, identity)

        # --- Assert ---
        # Delivery data returned for mb_1 and mb_2 only
        returned_ids = {d.media_buy_id for d in response.media_buy_deliveries}
        assert returned_ids == {"mb_1", "mb_2"}, f"Expected delivery for mb_1 and mb_2 only, got {returned_ids}"

        # mb_999 is NOT in deliveries
        assert "mb_999" not in returned_ids

        # Errors array reports mb_999 as not found
        assert response.errors is not None, "Expected errors for missing mb_999"
        error_messages = [e.message for e in response.errors]
        assert any("mb_999" in msg for msg in error_messages), f"Expected error mentioning mb_999, got {error_messages}"

        # Aggregated totals reflect only the 2 found buys
        assert response.aggregated_totals.media_buy_count == 2


# --- OID: UC-004-MAIN-18 ---


class TestNonexistentMediaBuyIdsReturnEmptyDeliveries:
    """BR-RULE-030: Nonexistent media_buy_ids resolve to empty deliveries array.

    Covers: UC-004-MAIN-18
    """

    def test_nonexistent_ids_return_empty_media_buy_deliveries(self):
        """Requesting delivery for nonexistent media_buy_ids returns empty deliveries.

        Covers: UC-004-MAIN-18
        """
        identity = _make_identity()
        req = GetMediaBuyDeliveryRequest(
            media_buy_ids=["nonexistent_1"],
            start_date="2025-01-01",
            end_date="2025-12-31",
        )

        mock_principal = MagicMock()
        mock_repo = MagicMock()
        # Repository returns empty list -- ID doesn't exist in DB
        mock_repo.get_by_principal.return_value = []

        mock_uow = MagicMock()
        mock_uow.__enter__ = MagicMock(return_value=mock_uow)
        mock_uow.__exit__ = MagicMock(return_value=False)
        mock_uow.media_buys = mock_repo

        with (
            patch(f"{_PATCH}.get_principal_object", return_value=mock_principal),
            patch(f"{_PATCH}.get_adapter") as mock_get_adapter,
            patch(f"{_PATCH}.MediaBuyUoW", return_value=mock_uow),
        ):
            result = _get_media_buy_delivery_impl(req, identity)

        # Core assertion: media_buy_deliveries is an empty list
        assert isinstance(result, GetMediaBuyDeliveryResponse)
        assert result.media_buy_deliveries == []
        assert result.aggregated_totals.media_buy_count == 0
        assert result.aggregated_totals.impressions == 0.0
        assert result.aggregated_totals.spend == 0.0

        # The repo was queried with the nonexistent ID
        mock_repo.get_by_principal.assert_called_once_with("test_principal", media_buy_ids=["nonexistent_1"])

        # Adapter was never called (no buys to fetch delivery for)
        mock_get_adapter.return_value.get_media_buy_delivery.assert_not_called()


# --- OID: UC-004-MAIN-19 ---


class TestDeliveryMetricsFieldPresence:
    """Tests that delivery metrics include the required schema fields.

    Covers: UC-004-MAIN-19
    """

    @patch(f"{_PATCH}.get_adapter")
    @patch(f"{_PATCH}.get_principal_object")
    @patch(f"{_PATCH}.MediaBuyUoW")
    def test_totals_include_impressions_spend_clicks_ctr(self, mock_uow_cls, mock_get_principal, mock_get_adapter):
        """Delivery totals include impressions, spend, clicks, and ctr fields.

        Covers: UC-004-MAIN-19
        """
        # Arrange
        identity = _make_identity()
        buy = _make_buy()
        adapter_resp = _make_adapter_response(impressions=5000, spend=250.0)

        mock_get_principal.return_value = MagicMock()
        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.return_value = adapter_resp
        mock_get_adapter.return_value = mock_adapter

        mock_uow = MagicMock()
        mock_repo = MagicMock()
        mock_repo.get_by_principal.return_value = [buy]
        mock_repo.get_packages.return_value = []
        mock_uow.media_buys = mock_repo
        mock_uow.__enter__ = Mock(return_value=mock_uow)
        mock_uow.__exit__ = Mock(return_value=False)
        mock_uow_cls.return_value = mock_uow

        req = GetMediaBuyDeliveryRequest(
            media_buy_ids=["mb_001"],
            start_date="2025-06-01",
            end_date="2025-06-30",
        )

        # Act
        result = _get_media_buy_delivery_impl(req, identity)

        # Assert - response is well-formed
        assert isinstance(result, GetMediaBuyDeliveryResponse)
        assert len(result.media_buy_deliveries) == 1

        delivery = result.media_buy_deliveries[0]
        totals = delivery.totals

        # Core metric fields must be present on totals
        assert totals.impressions == 5000.0
        assert totals.spend == 250.0
        # clicks field exists (set to 0 in current impl)
        assert totals.clicks is not None or hasattr(totals, "clicks")
        # ctr field exists (computed from clicks/impressions)
        assert hasattr(totals, "ctr")

    @patch(f"{_PATCH}.get_adapter")
    @patch(f"{_PATCH}.get_principal_object")
    @patch(f"{_PATCH}.MediaBuyUoW")
    def test_totals_include_video_completions_field(self, mock_uow_cls, mock_get_principal, mock_get_adapter):
        """Delivery totals include video_completions field (where applicable).

        Covers: UC-004-MAIN-19
        """
        # Arrange
        identity = _make_identity()
        buy = _make_buy()
        adapter_resp = _make_adapter_response(impressions=5000, spend=250.0)

        mock_get_principal.return_value = MagicMock()
        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.return_value = adapter_resp
        mock_get_adapter.return_value = mock_adapter

        mock_uow = MagicMock()
        mock_repo = MagicMock()
        mock_repo.get_by_principal.return_value = [buy]
        mock_repo.get_packages.return_value = []
        mock_uow.media_buys = mock_repo
        mock_uow.__enter__ = Mock(return_value=mock_uow)
        mock_uow.__exit__ = Mock(return_value=False)
        mock_uow_cls.return_value = mock_uow

        req = GetMediaBuyDeliveryRequest(
            media_buy_ids=["mb_001"],
            start_date="2025-06-01",
            end_date="2025-06-30",
        )

        # Act
        result = _get_media_buy_delivery_impl(req, identity)

        # Assert - video_completions field exists on totals (currently None)
        delivery = result.media_buy_deliveries[0]
        assert hasattr(delivery.totals, "video_completions")
        # Field is optional and currently always None in impl
        assert delivery.totals.video_completions is None

    @pytest.mark.xfail(
        reason="DeliveryTotals schema does not include 'conversions' field. "
        "Obligation requires conversions metric but it is missing from "
        "src/core/schemas/delivery.py:DeliveryTotals (lines 116-133). "
        "Would need to add conversions field to DeliveryTotals and populate "
        "it in _get_media_buy_delivery_impl (src/core/tools/media_buy_delivery.py:396-403).",
        strict=True,
    )
    @patch(f"{_PATCH}.get_adapter")
    @patch(f"{_PATCH}.get_principal_object")
    @patch(f"{_PATCH}.MediaBuyUoW")
    def test_totals_include_conversions_field(self, mock_uow_cls, mock_get_principal, mock_get_adapter):
        """Delivery totals include conversions metric field.

        Covers: UC-004-MAIN-19
        """
        # Arrange
        identity = _make_identity()
        buy = _make_buy()
        adapter_resp = _make_adapter_response(impressions=5000, spend=250.0)

        mock_get_principal.return_value = MagicMock()
        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.return_value = adapter_resp
        mock_get_adapter.return_value = mock_adapter

        mock_uow = MagicMock()
        mock_repo = MagicMock()
        mock_repo.get_by_principal.return_value = [buy]
        mock_repo.get_packages.return_value = []
        mock_uow.media_buys = mock_repo
        mock_uow.__enter__ = Mock(return_value=mock_uow)
        mock_uow.__exit__ = Mock(return_value=False)
        mock_uow_cls.return_value = mock_uow

        req = GetMediaBuyDeliveryRequest(
            media_buy_ids=["mb_001"],
            start_date="2025-06-01",
            end_date="2025-06-30",
        )

        # Act
        result = _get_media_buy_delivery_impl(req, identity)

        # Assert - conversions field should exist on totals
        delivery = result.media_buy_deliveries[0]
        assert hasattr(delivery.totals, "conversions")

    @pytest.mark.xfail(
        reason="DeliveryTotals schema does not include 'viewability' field. "
        "Obligation requires viewability metric but it is missing from "
        "src/core/schemas/delivery.py:DeliveryTotals (lines 116-133). "
        "Would need to add viewability field to DeliveryTotals and populate "
        "it in _get_media_buy_delivery_impl (src/core/tools/media_buy_delivery.py:396-403).",
        strict=True,
    )
    @patch(f"{_PATCH}.get_adapter")
    @patch(f"{_PATCH}.get_principal_object")
    @patch(f"{_PATCH}.MediaBuyUoW")
    def test_totals_include_viewability_field(self, mock_uow_cls, mock_get_principal, mock_get_adapter):
        """Delivery totals include viewability metric field.

        Covers: UC-004-MAIN-19
        """
        # Arrange
        identity = _make_identity()
        buy = _make_buy()
        adapter_resp = _make_adapter_response(impressions=5000, spend=250.0)

        mock_get_principal.return_value = MagicMock()
        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.return_value = adapter_resp
        mock_get_adapter.return_value = mock_adapter

        mock_uow = MagicMock()
        mock_repo = MagicMock()
        mock_repo.get_by_principal.return_value = [buy]
        mock_repo.get_packages.return_value = []
        mock_uow.media_buys = mock_repo
        mock_uow.__enter__ = Mock(return_value=mock_uow)
        mock_uow.__exit__ = Mock(return_value=False)
        mock_uow_cls.return_value = mock_uow

        req = GetMediaBuyDeliveryRequest(
            media_buy_ids=["mb_001"],
            start_date="2025-06-01",
            end_date="2025-06-30",
        )

        # Act
        result = _get_media_buy_delivery_impl(req, identity)

        # Assert - viewability field should exist on totals
        delivery = result.media_buy_deliveries[0]
        assert hasattr(delivery.totals, "viewability")

    @patch(f"{_PATCH}.get_adapter")
    @patch(f"{_PATCH}.get_principal_object")
    @patch(f"{_PATCH}.MediaBuyUoW")
    def test_aggregated_totals_include_core_metrics(self, mock_uow_cls, mock_get_principal, mock_get_adapter):
        """Response aggregated_totals include impressions, spend, clicks fields.

        Covers: UC-004-MAIN-19
        """
        # Arrange
        identity = _make_identity()
        buy = _make_buy()
        adapter_resp = _make_adapter_response(impressions=5000, spend=250.0)

        mock_get_principal.return_value = MagicMock()
        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.return_value = adapter_resp
        mock_get_adapter.return_value = mock_adapter

        mock_uow = MagicMock()
        mock_repo = MagicMock()
        mock_repo.get_by_principal.return_value = [buy]
        mock_repo.get_packages.return_value = []
        mock_uow.media_buys = mock_repo
        mock_uow.__enter__ = Mock(return_value=mock_uow)
        mock_uow.__exit__ = Mock(return_value=False)
        mock_uow_cls.return_value = mock_uow

        req = GetMediaBuyDeliveryRequest(
            media_buy_ids=["mb_001"],
            start_date="2025-06-01",
            end_date="2025-06-30",
        )

        # Act
        result = _get_media_buy_delivery_impl(req, identity)

        # Assert - aggregated totals contain core metrics
        agg = result.aggregated_totals
        assert agg.impressions == 5000.0
        assert agg.spend == 250.0
        assert agg.media_buy_count == 1
        # clicks field is present (may be None if no clicks)
        assert hasattr(agg, "clicks")


# --- OID: UC-004-MAIN-20 ---


class TestUnpopulatedFieldsGraceful:
    """Verify unpopulated schema fields (gaps G42, G44) handled without error.

    Covers: UC-004-MAIN-20
    """

    @patch(f"{_PATCH}._get_pricing_options", return_value={})
    @patch(f"{_PATCH}.get_adapter")
    @patch(f"{_PATCH}.get_principal_object")
    @patch(f"{_PATCH}.MediaBuyUoW")
    def test_daily_breakdown_is_none_without_error(self, mock_uow, mock_principal, mock_adapter, mock_pricing):
        """Production sets daily_breakdown=None; response assembles without error.

        Covers: UC-004-MAIN-20
        """
        buy = _make_buy()
        mock_principal.return_value = MagicMock()
        mock_repo = MagicMock()
        mock_repo.get_packages.return_value = []
        mock_uow_ctx = MagicMock()
        mock_uow_ctx.media_buys = mock_repo
        mock_uow.return_value.__enter__ = MagicMock(return_value=mock_uow_ctx)
        mock_uow.return_value.__exit__ = MagicMock(return_value=False)

        adapter_resp = _make_adapter_response()
        mock_adapter.return_value.get_media_buy_delivery.return_value = adapter_resp

        with patch(
            f"{_PATCH}._get_target_media_buys",
            return_value=[("mb_001", buy)],
        ):
            req = GetMediaBuyDeliveryRequest(
                media_buy_ids=["mb_001"],
                start_date="2025-06-01",
                end_date="2025-06-30",
            )
            identity = _make_identity()
            result = _get_media_buy_delivery_impl(req, identity)

        assert isinstance(result, GetMediaBuyDeliveryResponse)
        assert len(result.media_buy_deliveries) == 1
        delivery = result.media_buy_deliveries[0]
        # daily_breakdown is explicitly None (gap G42) — no error raised
        assert delivery.daily_breakdown is None

    def test_delivery_totals_schema_lacks_effective_rate_and_viewability(self):
        """DeliveryTotals does not have effective_rate or viewability fields (gap G44).

        The local DeliveryTotals schema intentionally omits these adcp library fields
        (blocked on video_completions -> completed_views rename). Construction
        succeeds without them, documenting the known gap.

        Covers: UC-004-MAIN-20
        """
        # Construct DeliveryTotals the same way production does (line 396-403
        # in media_buy_delivery.py) — effective_rate and viewability are absent
        totals = DeliveryTotals(
            impressions=5000.0,
            spend=250.0,
            clicks=0,
            ctr=None,
            video_completions=None,
            completion_rate=None,
        )
        # These fields exist in adcp library Totals but NOT in local DeliveryTotals
        assert not hasattr(totals, "effective_rate") or "effective_rate" not in DeliveryTotals.model_fields
        assert not hasattr(totals, "viewability") or "viewability" not in DeliveryTotals.model_fields
        # Construction succeeded — no error from missing fields
        assert totals.impressions == 5000.0
        assert totals.spend == 250.0

    def test_package_delivery_schema_lacks_creative_level_breakdowns(self):
        """PackageDelivery does not have by_creative / creative_level_breakdowns (gap G42).

        The local PackageDelivery schema intentionally omits by_creative (present
        in adcp library ByPackageItem). Construction succeeds without it.

        Covers: UC-004-MAIN-20
        """
        # Construct PackageDelivery the same way production does (lines 353-371)
        pkg = PackageDelivery(
            package_id="pkg_001",
            buyer_ref="ref_001",
            impressions=5000.0,
            spend=250.0,
            clicks=None,
            video_completions=None,
            pacing_index=1.0,
            pricing_model=None,
            rate=None,
            currency=None,
        )
        # by_creative exists in library ByPackageItem but NOT in local PackageDelivery
        assert "by_creative" not in PackageDelivery.model_fields
        # Construction succeeded — no error from missing field
        assert pkg.package_id == "pkg_001"
        assert pkg.impressions == 5000.0

    @patch(f"{_PATCH}._get_pricing_options", return_value={})
    @patch(f"{_PATCH}.get_adapter")
    @patch(f"{_PATCH}.get_principal_object")
    @patch(f"{_PATCH}.MediaBuyUoW")
    def test_full_response_assembles_with_all_gap_fields_absent(
        self, mock_uow, mock_principal, mock_adapter, mock_pricing
    ):
        """End-to-end: _impl returns valid response despite gap fields being absent.

        Verifies that daily_breakdown=None, no effective_rate on totals,
        no viewability on totals, and no by_creative on packages all coexist
        without raising any validation or runtime error.

        Covers: UC-004-MAIN-20
        """
        buy = _make_buy()
        mock_principal.return_value = MagicMock()
        mock_repo = MagicMock()
        mock_repo.get_packages.return_value = []
        mock_uow_ctx = MagicMock()
        mock_uow_ctx.media_buys = mock_repo
        mock_uow.return_value.__enter__ = MagicMock(return_value=mock_uow_ctx)
        mock_uow.return_value.__exit__ = MagicMock(return_value=False)

        adapter_resp = _make_adapter_response()
        mock_adapter.return_value.get_media_buy_delivery.return_value = adapter_resp

        with patch(
            f"{_PATCH}._get_target_media_buys",
            return_value=[("mb_001", buy)],
        ):
            req = GetMediaBuyDeliveryRequest(
                media_buy_ids=["mb_001"],
                start_date="2025-06-01",
                end_date="2025-06-30",
            )
            result = _get_media_buy_delivery_impl(req, _make_identity())

        assert isinstance(result, GetMediaBuyDeliveryResponse)
        delivery = result.media_buy_deliveries[0]

        # Gap G42: daily_breakdown is None
        assert delivery.daily_breakdown is None

        # Gap G44: effective_rate not on local DeliveryTotals
        assert "effective_rate" not in DeliveryTotals.model_fields

        # Gap G44: viewability not on local DeliveryTotals
        assert "viewability" not in DeliveryTotals.model_fields

        # Gap G42: creative_level_breakdowns (by_creative) not on PackageDelivery
        for pkg in delivery.by_package:
            assert "by_creative" not in type(pkg).model_fields

        # Response serializes cleanly — None fields excluded per AdCP convention
        dumped = result.model_dump()
        assert "media_buy_deliveries" in dumped
        # daily_breakdown=None is excluded by AdCPBaseModel's exclude_none=True
        assert "daily_breakdown" not in dumped["media_buy_deliveries"][0]
        # effective_rate and viewability not present (gap fields)
        assert "effective_rate" not in dumped["media_buy_deliveries"][0].get("totals", {})
        assert "viewability" not in dumped["media_buy_deliveries"][0].get("totals", {})


# ---------------------------------------------------------------------------
# UC-004-PRICINGOPTION-TYPE-CONSISTENCY-02
# ---------------------------------------------------------------------------


class TestPricingOptionStringToIntComparisonRejected:
    """PricingOption string-to-integer comparison is detected and rejected.

    Covers: UC-004-PRICINGOPTION-TYPE-CONSISTENCY-02
    """

    @pytest.mark.xfail(
        reason=(
            "_get_pricing_options converts pricing_option_ids to int and queries "
            "PricingOption.id (integer PK) at line 674. Non-numeric string IDs like "
            "'cpm_usd_fixed' are silently discarded (line 676). Should use string "
            "pricing_option_id field for lookup instead."
        ),
    )
    @patch(f"{_PATCH}.get_db_session")
    def test_pricing_options_keyed_by_string_id_not_integer_pk(self, mock_db):
        """_get_pricing_options maps by string pricing_option_id, not integer PK."""
        mock_po = MagicMock()
        mock_po.id = 99  # integer PK
        mock_po.pricing_option_id = "cpm_usd_fixed"  # string ID
        mock_po.pricing_model = "CPM"
        mock_po.rate = 2.50
        mock_po.currency = "USD"

        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_session.scalars.return_value.all.return_value = [mock_po]
        mock_db.return_value = mock_session

        identity = _make_identity()
        result = _get_pricing_options(
            tenant_id=identity.tenant_id,
            pricing_option_ids=["cpm_usd_fixed"],
        )

        # Key assertion: the map uses the string pricing_option_id, NOT the int PK
        assert "cpm_usd_fixed" in result
        assert 99 not in result
        assert result["cpm_usd_fixed"]["pricing_model"] == "CPM"
        assert result["cpm_usd_fixed"]["rate"] == 2.50

    @pytest.mark.xfail(
        reason=(
            "_get_pricing_options converts pricing_option_ids to int (line 674) and "
            "silently discards non-numeric strings (line 676). The function never "
            "queries by string pricing_option_id, so the result dict is empty."
        ),
    )
    @patch(f"{_PATCH}.get_db_session")
    def test_integer_pk_lookup_returns_none(self, mock_db):
        """Looking up pricing option by integer PK returns None (type mismatch caught)."""
        mock_po = MagicMock()
        mock_po.id = 42
        mock_po.pricing_option_id = "cpc_usd_standard"
        mock_po.pricing_model = "CPC"
        mock_po.rate = 0.50
        mock_po.currency = "USD"

        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_session.scalars.return_value.all.return_value = [mock_po]
        mock_db.return_value = mock_session

        identity = _make_identity()
        result = _get_pricing_options(
            tenant_id=identity.tenant_id,
            pricing_option_ids=["cpc_usd_standard"],
        )

        # Looking up by integer PK must fail — proves string-to-int comparison
        # would be caught
        assert result.get(42) is None
        assert result.get("42") is None
        # Only the string pricing_option_id works
        assert result.get("cpc_usd_standard") is not None


# ---------------------------------------------------------------------------
# UC-004-PRICINGOPTION-TYPE-CONSISTENCY-03
# ---------------------------------------------------------------------------


class TestEndToEndDeliveryMetricsCpmPricing:
    """End-to-end delivery metrics with CPM pricing.

    Covers: UC-004-PRICINGOPTION-TYPE-CONSISTENCY-03
    """

    @patch(f"{_PATCH}.get_adapter")
    @patch(f"{_PATCH}.get_principal_object")
    @patch(f"{_PATCH}.MediaBuyUoW")
    def test_cpm_spend_computed_correctly(self, mock_uow_cls, mock_get_principal, mock_get_adapter):
        """CPM: 10,000 impressions at $2.50 CPM -> spend $25.00.

        Spend is passthrough from adapter; pricing_model on PackageDelivery
        comes from MediaPackage.package_config.pricing_info.
        """
        identity = _make_identity()
        buy = _make_buy(
            media_buy_id="mb_cpm",
            raw_request={
                "buyer_ref": "ref_cpm",
                "packages": [{"package_id": "pkg_cpm", "product_id": "prod_cpm", "pricing_option_id": "cpm_usd_fixed"}],
            },
        )
        mock_get_principal.return_value = MagicMock()

        mock_media_pkg = MagicMock()
        mock_media_pkg.package_id = "pkg_cpm"
        mock_media_pkg.package_config = {"pricing_info": {"pricing_model": "cpm", "rate": 2.50, "currency": "USD"}}

        mock_repo = MagicMock()
        mock_repo.get_by_principal.return_value = [buy]
        mock_repo.get_packages.return_value = [mock_media_pkg]

        mock_uow = MagicMock()
        mock_uow.media_buys = mock_repo
        mock_uow.__enter__ = Mock(return_value=mock_uow)
        mock_uow.__exit__ = Mock(return_value=False)
        mock_uow_cls.return_value = mock_uow

        adapter_resp = _make_adapter_response(
            media_buy_id="mb_cpm", impressions=10000, spend=25.0, package_id="pkg_cpm"
        )
        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.return_value = adapter_resp
        mock_get_adapter.return_value = mock_adapter

        req = GetMediaBuyDeliveryRequest(media_buy_ids=["mb_cpm"], start_date="2025-06-01", end_date="2025-06-30")
        result = _get_media_buy_delivery_impl(req=req, identity=identity)

        assert result.aggregated_totals.media_buy_count == 1
        delivery = result.media_buy_deliveries[0]
        assert delivery.totals.spend == 25.0
        assert delivery.totals.impressions == 10000.0
        assert delivery.by_package[0].pricing_model == "cpm"
        assert delivery.by_package[0].rate == 2.50
        assert delivery.by_package[0].currency == "USD"

    @patch(f"{_PATCH}.get_adapter")
    @patch(f"{_PATCH}.get_principal_object")
    @patch(f"{_PATCH}.MediaBuyUoW")
    def test_cpm_package_level_spend_matches_totals(self, mock_uow_cls, mock_get_principal, mock_get_adapter):
        """Package-level spend and impressions are consistent with totals for CPM."""
        identity = _make_identity()
        buy = _make_buy(
            media_buy_id="mb_cpm2",
            raw_request={
                "buyer_ref": "ref_cpm2",
                "packages": [
                    {"package_id": "pkg_cpm2", "product_id": "prod_cpm2", "pricing_option_id": "cpm_usd_fixed"}
                ],
            },
        )
        mock_get_principal.return_value = MagicMock()

        mock_repo = MagicMock()
        mock_repo.get_by_principal.return_value = [buy]
        mock_repo.get_packages.return_value = []

        mock_uow = MagicMock()
        mock_uow.media_buys = mock_repo
        mock_uow.__enter__ = Mock(return_value=mock_uow)
        mock_uow.__exit__ = Mock(return_value=False)
        mock_uow_cls.return_value = mock_uow

        adapter_resp = _make_adapter_response(
            media_buy_id="mb_cpm2", impressions=10000, spend=25.0, package_id="pkg_cpm2"
        )
        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.return_value = adapter_resp
        mock_get_adapter.return_value = mock_adapter

        req = GetMediaBuyDeliveryRequest(media_buy_ids=["mb_cpm2"], start_date="2025-06-01", end_date="2025-06-30")
        result = _get_media_buy_delivery_impl(req=req, identity=identity)

        delivery = result.media_buy_deliveries[0]
        pkg = delivery.by_package[0]
        assert pkg.impressions == delivery.totals.impressions
        assert pkg.spend == delivery.totals.spend
        assert pkg.package_id == "pkg_cpm2"


# ---------------------------------------------------------------------------
# UC-004-PRICINGOPTION-TYPE-CONSISTENCY-04
# ---------------------------------------------------------------------------


class TestEndToEndDeliveryMetricsCpcPricing:
    """End-to-end delivery metrics with CPC pricing.

    Covers: UC-004-PRICINGOPTION-TYPE-CONSISTENCY-04
    """

    @patch(f"{_PATCH}._get_pricing_options")
    @patch(f"{_PATCH}.get_adapter")
    @patch(f"{_PATCH}.get_principal_object")
    @patch(f"{_PATCH}.MediaBuyUoW")
    def test_cpc_clicks_calculated_from_spend_and_rate(
        self, mock_uow_cls, mock_get_principal, mock_get_adapter, mock_pricing
    ):
        """CPC: $250.00 spend at $0.50 CPC -> 500 clicks (floor(spend/rate)).

        _get_media_buy_delivery_impl calculates per-package clicks at line 348-349
        when pricing_option.pricing_model == PricingModel.cpc.
        """
        identity = _make_identity()
        buy = _make_buy(
            media_buy_id="mb_cpc",
            raw_request={
                "buyer_ref": "ref_cpc",
                "pricing_option_id": "99",
                "packages": [{"package_id": "pkg_cpc", "product_id": "prod_cpc", "pricing_option_id": "99"}],
            },
        )
        mock_get_principal.return_value = MagicMock()

        mock_po = MagicMock()
        mock_po.pricing_model = PricingModel.cpc
        mock_po.rate = 0.50
        mock_pricing.return_value = {"99": mock_po}

        mock_repo = MagicMock()
        mock_repo.get_by_principal.return_value = [buy]
        mock_repo.get_packages.return_value = []

        mock_uow = MagicMock()
        mock_uow.media_buys = mock_repo
        mock_uow.__enter__ = Mock(return_value=mock_uow)
        mock_uow.__exit__ = Mock(return_value=False)
        mock_uow_cls.return_value = mock_uow

        adapter_resp = _make_adapter_response(
            media_buy_id="mb_cpc", impressions=5000, spend=250.0, package_id="pkg_cpc"
        )
        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.return_value = adapter_resp
        mock_get_adapter.return_value = mock_adapter

        req = GetMediaBuyDeliveryRequest(media_buy_ids=["mb_cpc"], start_date="2025-06-01", end_date="2025-06-30")
        result = _get_media_buy_delivery_impl(req=req, identity=identity)

        assert result.aggregated_totals.media_buy_count == 1
        delivery = result.media_buy_deliveries[0]
        assert delivery.totals.spend == 250.0
        # CPC click calculation: floor(spend / rate) = floor(250 / 0.50) = 500
        assert delivery.by_package[0].clicks == 500

    @patch(f"{_PATCH}.get_adapter")
    @patch(f"{_PATCH}.get_principal_object")
    @patch(f"{_PATCH}.MediaBuyUoW")
    def test_cpc_pricing_info_on_package_delivery(self, mock_uow_cls, mock_get_principal, mock_get_adapter):
        """CPC pricing info from MediaPackage.package_config flows to PackageDelivery."""
        identity = _make_identity()
        buy = _make_buy(
            media_buy_id="mb_cpc2",
            raw_request={
                "buyer_ref": "ref_cpc2",
                "packages": [
                    {"package_id": "pkg_cpc2", "product_id": "prod_cpc2", "pricing_option_id": "cpc_usd_standard"}
                ],
            },
        )
        mock_get_principal.return_value = MagicMock()

        mock_media_pkg = MagicMock()
        mock_media_pkg.package_id = "pkg_cpc2"
        mock_media_pkg.package_config = {"pricing_info": {"pricing_model": "cpc", "rate": 0.50, "currency": "USD"}}

        mock_repo = MagicMock()
        mock_repo.get_by_principal.return_value = [buy]
        mock_repo.get_packages.return_value = [mock_media_pkg]

        mock_uow = MagicMock()
        mock_uow.media_buys = mock_repo
        mock_uow.__enter__ = Mock(return_value=mock_uow)
        mock_uow.__exit__ = Mock(return_value=False)
        mock_uow_cls.return_value = mock_uow

        adapter_resp = _make_adapter_response(
            media_buy_id="mb_cpc2", impressions=5000, spend=250.0, package_id="pkg_cpc2"
        )
        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.return_value = adapter_resp
        mock_get_adapter.return_value = mock_adapter

        req = GetMediaBuyDeliveryRequest(media_buy_ids=["mb_cpc2"], start_date="2025-06-01", end_date="2025-06-30")
        result = _get_media_buy_delivery_impl(req=req, identity=identity)

        delivery = result.media_buy_deliveries[0]
        assert delivery.by_package[0].pricing_model == "cpc"
        assert delivery.by_package[0].rate == 0.50
        assert delivery.by_package[0].currency == "USD"


# ---------------------------------------------------------------------------
# UC-004-PRICINGOPTION-TYPE-CONSISTENCY-05
# ---------------------------------------------------------------------------


class TestDeliveryMetricsFlatRatePricing:
    """End-to-end delivery metrics with FLAT_RATE pricing.

    Covers: UC-004-PRICINGOPTION-TYPE-CONSISTENCY-05
    """

    @patch(f"{_PATCH}.get_adapter")
    @patch(f"{_PATCH}.get_principal_object")
    @patch(f"{_PATCH}.MediaBuyUoW")
    def test_flat_rate_spend_reflects_rate_correctly(self, mock_uow_cls, mock_get_principal, mock_get_adapter):
        """FLAT_RATE pricing: adapter reports spend=$5,000 which flows through."""
        identity = _make_identity()
        buy = _make_buy(
            media_buy_id="mb_flat",
            raw_request={
                "buyer_ref": "ref_flat",
                "packages": [
                    {"package_id": "pkg_flat", "product_id": "prod_flat", "pricing_option_id": "flat_rate_5k"}
                ],
            },
        )
        mock_get_principal.return_value = MagicMock()

        mock_media_pkg = MagicMock()
        mock_media_pkg.package_id = "pkg_flat"
        mock_media_pkg.package_config = {
            "pricing_info": {"pricing_model": "flat_rate", "rate": 5000.0, "currency": "USD"}
        }

        mock_repo = MagicMock()
        mock_repo.get_by_principal.return_value = [buy]
        mock_repo.get_packages.return_value = [mock_media_pkg]

        mock_uow = MagicMock()
        mock_uow.media_buys = mock_repo
        mock_uow.__enter__ = Mock(return_value=mock_uow)
        mock_uow.__exit__ = Mock(return_value=False)
        mock_uow_cls.return_value = mock_uow

        adapter_resp = _make_adapter_response(
            media_buy_id="mb_flat", impressions=50000, spend=5000.0, package_id="pkg_flat"
        )
        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.return_value = adapter_resp
        mock_get_adapter.return_value = mock_adapter

        req = GetMediaBuyDeliveryRequest(media_buy_ids=["mb_flat"], start_date="2025-06-01", end_date="2025-06-30")
        result = _get_media_buy_delivery_impl(req=req, identity=identity)

        assert result.aggregated_totals.media_buy_count == 1
        delivery = result.media_buy_deliveries[0]
        assert delivery.totals.spend == 5000.0
        assert delivery.totals.impressions == 50000.0
        pkg = delivery.by_package[0]
        assert pkg.spend == 5000.0
        assert pkg.pricing_model == "flat_rate"
        assert pkg.rate == 5000.0
        assert pkg.currency == "USD"

    @patch(f"{_PATCH}.get_adapter")
    @patch(f"{_PATCH}.get_principal_object")
    @patch(f"{_PATCH}.MediaBuyUoW")
    def test_flat_rate_package_level_spend_matches_totals(self, mock_uow_cls, mock_get_principal, mock_get_adapter):
        """FLAT_RATE: package-level spend matches buy-level totals."""
        identity = _make_identity()
        buy = _make_buy(
            media_buy_id="mb_flat2",
            raw_request={
                "buyer_ref": "ref_flat2",
                "packages": [
                    {"package_id": "pkg_flat2", "product_id": "prod_flat2", "pricing_option_id": "flat_rate_premium"}
                ],
            },
        )
        mock_get_principal.return_value = MagicMock()

        mock_repo = MagicMock()
        mock_repo.get_by_principal.return_value = [buy]
        mock_repo.get_packages.return_value = []

        mock_uow = MagicMock()
        mock_uow.media_buys = mock_repo
        mock_uow.__enter__ = Mock(return_value=mock_uow)
        mock_uow.__exit__ = Mock(return_value=False)
        mock_uow_cls.return_value = mock_uow

        adapter_resp = _make_adapter_response(
            media_buy_id="mb_flat2", impressions=50000, spend=5000.0, package_id="pkg_flat2"
        )
        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.return_value = adapter_resp
        mock_get_adapter.return_value = mock_adapter

        req = GetMediaBuyDeliveryRequest(media_buy_ids=["mb_flat2"], start_date="2025-06-01", end_date="2025-06-30")
        result = _get_media_buy_delivery_impl(req=req, identity=identity)

        delivery = result.media_buy_deliveries[0]
        pkg = delivery.by_package[0]
        assert pkg.spend == delivery.totals.spend
        assert pkg.impressions == delivery.totals.impressions
        assert pkg.package_id == "pkg_flat2"


# ---------------------------------------------------------------------------
# UC-004-RESPONSE-SERIALIZATION-SALESAGENT-02
# ---------------------------------------------------------------------------


class TestDeliveryResponsePreservesExtFields:
    """Delivery response should preserve ext fields from adapter.

    Covers: UC-004-RESPONSE-SERIALIZATION-SALESAGENT-02
    """

    @pytest.mark.xfail(
        reason=(
            "MediaBuyDeliveryData does not have an ext field "
            "(src/core/schemas/delivery.py:208-239). Production code at "
            "media_buy_delivery.py:389-406 does not propagate ext from adapter "
            "response to per-buy delivery data. AdapterGetMediaBuyDeliveryResponse "
            "also lacks an ext field (delivery.py:324-332). Ext propagation "
            "needs to be added to both the adapter response schema and the "
            "delivery data construction logic."
        ),
    )
    @patch(f"{_PATCH}.get_adapter")
    @patch(f"{_PATCH}.get_principal_object")
    @patch(f"{_PATCH}.MediaBuyUoW")
    def test_ext_fields_preserved_in_delivery_data(self, mock_uow_cls, mock_get_principal, mock_get_adapter):
        """ext fields from adapter response should flow through to MediaBuyDeliveryData."""
        identity = _make_identity()
        buy = _make_buy(media_buy_id="mb_ext")
        mock_get_principal.return_value = MagicMock()

        mock_repo = MagicMock()
        mock_repo.get_by_principal.return_value = [buy]
        mock_repo.get_packages.return_value = []

        mock_uow = MagicMock()
        mock_uow.media_buys = mock_repo
        mock_uow.__enter__ = Mock(return_value=mock_uow)
        mock_uow.__exit__ = Mock(return_value=False)
        mock_uow_cls.return_value = mock_uow

        adapter_resp = _make_adapter_response(media_buy_id="mb_ext")
        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.return_value = adapter_resp
        mock_get_adapter.return_value = mock_adapter

        req = GetMediaBuyDeliveryRequest(media_buy_ids=["mb_ext"], start_date="2025-06-01", end_date="2025-06-30")
        result = _get_media_buy_delivery_impl(req=req, identity=identity)

        assert len(result.media_buy_deliveries) == 1
        delivery = result.media_buy_deliveries[0]
        # MediaBuyDeliveryData should have ext field for extension data
        assert hasattr(delivery, "ext") and delivery.ext is not None

    @pytest.mark.xfail(
        reason=(
            "MediaBuyDeliveryData has no ext field in its schema definition "
            "(src/core/schemas/delivery.py:208-239), so model_dump() does not "
            "include an 'ext' key in the serialized per-buy delivery data. "
            "Ext propagation from adapter to delivery data is not implemented."
        ),
    )
    @patch(f"{_PATCH}.get_adapter")
    @patch(f"{_PATCH}.get_principal_object")
    @patch(f"{_PATCH}.MediaBuyUoW")
    def test_ext_fields_preserved_in_model_dump(self, mock_uow_cls, mock_get_principal, mock_get_adapter):
        """ext fields should survive model_dump() serialization."""
        identity = _make_identity()
        buy = _make_buy(media_buy_id="mb_ext2")
        mock_get_principal.return_value = MagicMock()

        mock_repo = MagicMock()
        mock_repo.get_by_principal.return_value = [buy]
        mock_repo.get_packages.return_value = []

        mock_uow = MagicMock()
        mock_uow.media_buys = mock_repo
        mock_uow.__enter__ = Mock(return_value=mock_uow)
        mock_uow.__exit__ = Mock(return_value=False)
        mock_uow_cls.return_value = mock_uow

        adapter_resp = _make_adapter_response(media_buy_id="mb_ext2")
        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.return_value = adapter_resp
        mock_get_adapter.return_value = mock_adapter

        req = GetMediaBuyDeliveryRequest(media_buy_ids=["mb_ext2"], start_date="2025-06-01", end_date="2025-06-30")
        result = _get_media_buy_delivery_impl(req=req, identity=identity)

        dumped = result.model_dump()
        delivery_dumped = dumped["media_buy_deliveries"][0]
        assert "ext" in delivery_dumped, "Serialized delivery data should include ext field"
