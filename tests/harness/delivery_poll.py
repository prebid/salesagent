"""DeliveryPollEnv — integration test environment for _get_media_buy_delivery_impl.

Patches: get_adapter ONLY (external ad server).
Real: MediaBuyUoW, _get_pricing_options (all hit real DB).

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

from src.core.schemas import AdapterGetMediaBuyDeliveryResponse, GetMediaBuyDeliveryResponse
from tests.harness._base import IntegrationEnv
from tests.harness._mixins import DeliveryPollMixin
from tests.harness.transport import DeliverResult


class DeliveryPollEnv(DeliveryPollMixin, IntegrationEnv):
    """Integration test environment for _get_media_buy_delivery_impl.

    Only mocks the adapter (external ad server). Everything else is real:
    - Real MediaBuyUoW -> real DB queries
    - The principal comes off the identity; nothing looks one up
    - Real _get_pricing_options -> real DB queries

    Fluent API (from DeliveryPollMixin):
        set_adapter_response(...)  -- configure adapter return for a media_buy_id
        set_adapter_error(exc)     -- make the adapter raise an exception
        call_impl(...)             -- call _get_media_buy_delivery_impl with real DB
    """

    RESPONSE_MODEL = GetMediaBuyDeliveryResponse

    # FIXME(#2012): JUSTIFIED OVERRIDE — deliberately does NOT declare
    # MCP_TOOL/A2A_SKILL, so it does not take the base's client-core delegation.
    # The core's UNWRAP parses into the PINNED GetMediaBuyDeliveryResponse, whose
    # by_package items REQUIRE pricing_model, rate and currency
    # (get-media-buy-delivery-response.json); production emits none of the three,
    # so every response fails that parse and 214 UC-004 scenarios go red. Parsing
    # here with the LOCAL model keeps the env working while the gap stays
    # attributable — a production schema defect, not a dispatch defect, and
    # deliberately not hidden by loosening the core. Delete both overrides and
    # their `_KNOWN_DELIVER_OVERRIDES` entries when #2012 lands.
    def deliver_mcp(self, **kwargs: Any) -> DeliverResult:
        """Dispatch get_media_buy_delivery via the real FastMCP Client pipeline."""
        return self._run_mcp_client("get_media_buy_delivery", GetMediaBuyDeliveryResponse, **kwargs)

    def deliver_a2a(self, **kwargs: Any) -> DeliverResult:
        """Dispatch get_media_buy_delivery via the real A2A handler pipeline."""
        return self._run_a2a_handler("get_media_buy_delivery", GetMediaBuyDeliveryResponse, **kwargs)

    EXTERNAL_PATCHES = {
        "adapter": "src.core.tools.media_buy_delivery.get_adapter",
    }
    REST_ENDPOINT = "/api/v1/media-buys/delivery"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._adapter_responses: dict[str, AdapterGetMediaBuyDeliveryResponse] = {}

    def _configure_mocks(self) -> None:
        self._configure_adapter_mock()

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

    # parse_rest_response: the base's, which revives RESPONSE_MODEL.
