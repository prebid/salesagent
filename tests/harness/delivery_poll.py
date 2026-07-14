"""DeliveryPollEnv — integration test environment for _get_media_buy_delivery_impl.

Patches: get_adapter ONLY (external ad server).
Real: MediaBuyUoW, get_principal_object, _get_pricing_options (all hit real DB).

Requires: integration_db fixture (creates test PostgreSQL DB).

Usage::

    @pytest.mark.requires_db
    def test_something(self, integration_db):
        with DeliveryPollEnv() as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            buy = MediaBuyFactory(tenant=tenant, principal=principal)
            env.set_adapter_response(buy.media_buy_id, impressions=5000)

            response = env.call_impl(media_buy_ids=[buy.media_buy_id])
            assert response.aggregated_totals.impressions == 5000.0

Available mocks via env.mock:
    "adapter"    -- get_adapter mock (only external mock)
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from src.core.schemas import AdapterGetMediaBuyDeliveryResponse, GetMediaBuyDeliveryResponse
from tests.harness._base import IntegrationEnv
from tests.harness._mixins import DeliveryPollMixin


class DeliveryPollEnv(DeliveryPollMixin, IntegrationEnv):
    """Integration test environment for _get_media_buy_delivery_impl.

    Only mocks the adapter (external ad server). Everything else is real:
    - Real MediaBuyUoW -> real DB queries
    - Real get_principal_object -> real DB queries
    - Real _get_pricing_options -> real DB queries

    Fluent API (from DeliveryPollMixin):
        set_adapter_response(...)  -- configure adapter return for a media_buy_id
        set_adapter_error(exc)     -- make the adapter raise an exception
        call_impl(...)             -- call _get_media_buy_delivery_impl with real DB
    """

    EXTERNAL_PATCHES = {
        "adapter": "src.core.tools.media_buy_delivery.get_adapter",
    }
    REST_ENDPOINT = "/api/v1/media-buys/delivery"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._adapter_responses: dict[str, AdapterGetMediaBuyDeliveryResponse] = {}

    def _configure_mocks(self) -> None:
        self._configure_adapter_mock()

    def call_a2a(self, **kwargs: Any) -> GetMediaBuyDeliveryResponse:
        """Call get_media_buy_delivery via real AdCPRequestHandler — full A2A pipeline."""
        return self._run_a2a_handler("get_media_buy_delivery", GetMediaBuyDeliveryResponse, **kwargs)

    def call_mcp(self, **kwargs: Any) -> GetMediaBuyDeliveryResponse:
        """Call get_media_buy_delivery via Client(mcp) — full pipeline dispatch."""
        return self._run_mcp_client("get_media_buy_delivery", GetMediaBuyDeliveryResponse, **kwargs)

    def build_rest_body(self, **kwargs: Any) -> dict[str, Any]:
        """Convert kwargs to GetMediaBuyDeliveryBody shape for REST POST."""
        # Forward all request fields that the REST body accepts
        _BODY_FIELDS = (
            "media_buy_ids",
            "status_filter",
            "start_date",
            "end_date",
            "reporting_dimensions",
            "attribution_window",
            "include_package_daily_breakdown",
            "account",
        )
        return {k: kwargs[k] for k in _BODY_FIELDS if k in kwargs and kwargs[k] is not None}

    def parse_rest_response(self, data: dict[str, Any]) -> GetMediaBuyDeliveryResponse:
        """Parse REST JSON into GetMediaBuyDeliveryResponse."""
        return GetMediaBuyDeliveryResponse(**data)

    async def send_delivery_webhook(self, buy: Any) -> dict[str, Any]:
        """Force one delivery-webhook scheduler send for ``buy``; return the wire payload.

        Drives the REAL scheduler path (``_send_report_for_media_buy`` with
        ``force=True``) — delivery impl, sequence computation from
        WebhookDeliveryLog, payload serialization — mocking only the outbound
        HTTP POST. Returns the JSON body the buyer's webhook would receive
        (the webhook-only fields notification_type / sequence_number /
        next_expected_at live under ``result``; #1570).

        ``buy.raw_request`` must contain a ``reporting_webhook`` config.
        """
        from src.services.delivery_webhook_scheduler import DeliveryWebhookScheduler

        scheduler = DeliveryWebhookScheduler()
        mock_response = MagicMock(status_code=200, text="OK")
        mock_response.raise_for_status.return_value = None
        # Stub only the outbound HTTP client — everything above it (delivery
        # impl, derivation, sequence, serialization) runs for real. This reaches
        # into webhook_service._session (private) because that is the only seam
        # where the serialized body is observable; if the service ever swaps its
        # HTTP client this AttributeErrors loudly rather than silently no-op'ing.
        with patch.object(scheduler.webhook_service._session, "post", return_value=mock_response) as mock_post:
            await scheduler._send_report_for_media_buy(
                buy, buy.raw_request["reporting_webhook"], self.get_session(), force=True
            )
        assert mock_post.call_count == 1, "scheduler must send exactly one webhook"
        return mock_post.call_args.kwargs["json"]
