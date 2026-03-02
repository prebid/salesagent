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
from src.core.webhook_authenticator import WebhookAuthenticator
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
