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
        """Call get_media_buy_delivery_raw (A2A wrapper) with real DB."""
        from src.core.tools.media_buy_delivery import get_media_buy_delivery_raw

        self._commit_factory_data()
        kwargs.setdefault("identity", self.identity)
        return get_media_buy_delivery_raw(**kwargs)

    def call_mcp(self, **kwargs: Any) -> GetMediaBuyDeliveryResponse:
        """Call get_media_buy_delivery MCP wrapper with mock Context."""
        from src.core.tools.media_buy_delivery import get_media_buy_delivery

        return self._run_mcp_wrapper(get_media_buy_delivery, GetMediaBuyDeliveryResponse, **kwargs)

    def build_rest_body(self, **kwargs: Any) -> dict[str, Any]:
        """Convert kwargs to GetMediaBuyDeliveryBody shape for REST POST."""
        body: dict[str, Any] = {}
        if "media_buy_ids" in kwargs and kwargs["media_buy_ids"] is not None:
            body["media_buy_ids"] = kwargs["media_buy_ids"]
        if "buyer_refs" in kwargs and kwargs["buyer_refs"] is not None:
            body["buyer_refs"] = kwargs["buyer_refs"]
        if "start_date" in kwargs and kwargs["start_date"] is not None:
            body["start_date"] = kwargs["start_date"]
        if "end_date" in kwargs and kwargs["end_date"] is not None:
            body["end_date"] = kwargs["end_date"]
        return body

    def parse_rest_response(self, data: dict[str, Any]) -> GetMediaBuyDeliveryResponse:
        """Parse REST JSON into GetMediaBuyDeliveryResponse."""
        return GetMediaBuyDeliveryResponse(**data)
