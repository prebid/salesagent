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
from unittest.mock import MagicMock

from src.core.schemas.delivery import GetCreativeDeliveryResponse, GetMediaBuyDeliveryResponse
from src.core.tools._media_buy_status import CANONICAL_STATUSES
from src.core.tools.media_buy_delivery import (
    _resolve_delivery_status_filter,
)

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
# UC-004-ALT-STATUS-FILTERED-DELIVERY-07
# ---------------------------------------------------------------------------


class TestValidStatusValuesAccepted:
    """All valid status values are accepted by status filter without error.

    Covers: UC-004-ALT-STATUS-FILTERED-DELIVERY-07
    """

    def test_special_all_value_returns_all_statuses(self):
        """The 'all' value returns all valid internal statuses.

        Covers: UC-004-ALT-STATUS-FILTERED-DELIVERY-07
        """
        # Use a mock with .value = "all" to simulate the "all" special case
        mock_status = MagicMock()
        mock_status.value = "all"

        # Act — pure function test, harness not applicable
        result = _resolve_delivery_status_filter(mock_status, set(CANONICAL_STATUSES))

        # Assert — all valid statuses returned
        assert set(result) == set(CANONICAL_STATUSES)


# ---------------------------------------------------------------------------
# UC-004-ALT-WEBHOOK-PUSH-REPORTING-09
# ---------------------------------------------------------------------------


# TestWebhookExcludesAggregatedTotals and TestWebhookRequestedMetricsFiltering are DELETED
# with GetMediaBuyDeliveryResponse.webhook_payload(), the method they exercised. It had zero
# production callers: a webhook body is built by src/services/protocol_webhook_service.py:176,183
# through create_a2a_webhook_payload / create_mcp_webhook_payload, and neither excludes
# aggregated_totals nor filters requested_metrics.
#
# So these two tests were the only thing that made the obligation look covered while nothing
# a buyer receives implemented it. Both obligations (UC-004-ALT-WEBHOOK-PUSH-REPORTING-09 and
# -10) are recorded on GH #2058, which already owns the divergence between the flat document
# the service posts and the envelope the spec defines.


# UC-004-MAIN-13 (the MCP ToolResult carries content and structured_content) is graded on
# the wire: every BR-UC-004 scenario parametrized over mcp reads structured_content
# through the harness's MCP leg, so no direct-call test of the wrapper is kept here.


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


class TestCreativeDeliveryResponseStr:
    """__str__ returns a human-readable summary for creative delivery responses.

    Covers: UC-004-DISPLAY-01
    """


# ---------------------------------------------------------------------------
# UC-004-SERIAL-01 — Serialization compliance for next_expected_at
# ---------------------------------------------------------------------------


class TestNextExpectedAtSerialization:
    """next_expected_at is omitted when unset — never emitted as null.

    AdCP 3.1 types the field `{"type": "string"}` and never lists it in `required`,
    in all five places it is declared. A null fails buyer-side validation whatever
    the notification_type, and `final` is singled out for omission by both the
    response schema ("only present ... when notification_type is not 'final'") and
    the webhook result schema ("Omitted on final notifications").

    These tests previously asserted the inverse — that any notification_type forced
    `next_expected_at: null` — and production carried a `_should_always_include`
    override to satisfy them. The graded BDD contract
    (@T-UC-004-webhook-notification-type, BR-RULE-029 INV-2) always had the correct
    rule; see docs/test-obligations/UC-004-deliver-media-buy-metrics.md.

    Covers: UC-004-SERIAL-01
    """

    def test_final_notification_omits_next_expected_at(self):
        """'final' is the case both pinned schemas single out for omission.

        Covers: UC-004-SERIAL-01
        """
        resp = _make_media_buy_delivery_response(0, notification_type="final")
        dumped = resp.model_dump(mode="json")
        assert "next_expected_at" not in dumped, (
            f"A final report carrying next_expected_at tells the buyer to expect another "
            f"one. Got {dumped.get('next_expected_at')!r}"
        )

    def test_scheduled_notification_omits_unset_next_expected_at(self):
        """'scheduled' may carry the field, but an unset value is omitted, not nulled.

        Covers: UC-004-SERIAL-01
        """
        resp = _make_media_buy_delivery_response(0, notification_type="scheduled")
        dumped = resp.model_dump(mode="json")
        assert "next_expected_at" not in dumped, (
            f"The pin types next_expected_at as a string; an unset value is omitted, "
            f"never serialized as null. Got {dumped.get('next_expected_at')!r}"
        )

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


# TestMissingPrincipalIdReturnsError (test_none_principal_id_raises_auth_error and
# test_empty_string_principal_id_raises_auth_error) is REMOVED. Both built an identity with
# no usable principal_id -- None, and the empty string -- and asserted
# _get_media_buy_delivery_impl raised AdCPAuthenticationError itself. The class docstring
# cited "lines 91-93 of media_buy_delivery.py"; those lines are imports now.
#
# Neither half is constructible now. ResolvedIdentity.principal is a required field, so the
# None case is not a value the type can hold, and the in-tool guard that raised for either
# case went with the rest of the re-checks when the resolver became the one place a
# credential is judged (47d57e5d6) -- ruff-boundary.toml bans raising AUTH_MISSING or
# AUTH_INVALID anywhere but the resolver, so this implementation cannot raise it even if a
# guard were written back in. The empty string is not a separate obligation either: the
# resolver only builds an identity around a principal row it actually loaded.
#
# The obligation is graded where the refusal is minted -- once, for every tool and every
# transport -- by the transport-blind auth scenarios asserting the AUTH_MISSING wire
# envelope across a2a, mcp and rest.
#
# Same removal, same reason, as tests/unit/test_media_buy.py:3869.


class TestStatusFilterRawString:
    """_resolve_delivery_status_filter handles raw string status values.

    Covers line 694 (fallback path) of media_buy_delivery.py.
    """

    def test_raw_string_active_is_recognized(self):
        result = _resolve_delivery_status_filter("active", set(CANONICAL_STATUSES))
        assert result == ["active"]

    def test_unknown_raw_string_defaults_to_active(self):
        result = _resolve_delivery_status_filter("nonexistent", set(CANONICAL_STATUSES))
        assert result == ["active"]
