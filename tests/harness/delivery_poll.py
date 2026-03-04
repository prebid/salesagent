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

from src.core.schemas import AdapterGetMediaBuyDeliveryResponse
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

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._adapter_responses: dict[str, AdapterGetMediaBuyDeliveryResponse] = {}

    def _configure_mocks(self) -> None:
        self._configure_adapter_mock()
