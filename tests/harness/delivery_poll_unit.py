"""DeliveryPollEnv — unit test environment for _get_media_buy_delivery_impl.

Patches: MediaBuyUoW, get_principal_object, get_adapter, _get_pricing_options

Usage::

    with DeliveryPollEnv() as env:
        env.add_buy(media_buy_id="mb_001", start_date=date(2025, 1, 1))
        env.set_adapter_response("mb_001", impressions=5000, spend=250.0)
        response = env.call_impl(media_buy_ids=["mb_001"])
        assert response.aggregated_totals.impressions == 5000.0

Available mocks via env.mock:
    "uow"       -- MediaBuyUoW class mock
    "principal"  -- get_principal_object mock
    "adapter"    -- get_adapter mock
    "pricing"    -- _get_pricing_options mock
"""

from __future__ import annotations

from datetime import date
from typing import Any
from unittest.mock import MagicMock

from src.core.schemas import AdapterGetMediaBuyDeliveryResponse
from tests.harness._base import BaseTestEnv
from tests.harness._mixins import DeliveryPollMixin
from tests.harness._mock_uow import make_mock_uow


class DeliveryPollEnv(DeliveryPollMixin, BaseTestEnv):
    """Unit test environment for _get_media_buy_delivery_impl.

    Fluent API (from DeliveryPollMixin):
        set_adapter_response(...)  -- configure adapter return for a media_buy_id
        set_adapter_error(exc)     -- make the adapter raise an exception
        call_impl(...)             -- call _get_media_buy_delivery_impl

    Unit-only API:
        add_buy(...)               -- add a mock MediaBuy to the UoW repo
        set_pricing_options(map)   -- configure pricing option lookup results
    """

    MODULE = "src.core.tools.media_buy_delivery"
    EXTERNAL_PATCHES = {
        "uow": f"{MODULE}.MediaBuyUoW",
        "principal": f"{MODULE}.get_principal_object",
        "adapter": f"{MODULE}.get_adapter",
        "pricing": f"{MODULE}._get_pricing_options",
    }

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._buys: list[MagicMock] = []
        self._adapter_responses: dict[str, AdapterGetMediaBuyDeliveryResponse] = {}
        self._adapter_error: Exception | None = None
        self._uow_instance: MagicMock | None = None

    def _configure_mocks(self) -> None:
        # Principal: return a valid mock principal
        self.mock["principal"].return_value = MagicMock(
            principal_id=self._principal_id,
            name="Test Principal",
        )

        # UoW: replace mock class with make_mock_uow
        uow_cls, self._uow_instance = make_mock_uow()
        self.mock["uow"].return_value = self._uow_instance
        # Wire context manager protocol through the class mock
        self.mock["uow"].return_value.__enter__ = self._uow_instance.__enter__
        self.mock["uow"].return_value.__exit__ = self._uow_instance.__exit__

        # Adapter: default happy path (from mixin)
        self._configure_adapter_mock()

        # Pricing: default empty
        self.mock["pricing"].return_value = {}

    def add_buy(
        self,
        media_buy_id: str = "mb_001",
        buyer_ref: str | None = "ref_001",
        start_date: date = date(2025, 1, 1),
        end_date: date = date(2027, 12, 31),
        budget: float = 10000.0,
        currency: str = "USD",
        raw_request: dict[str, Any] | None = None,
    ) -> MagicMock:
        """Add a mock MediaBuy to the repository.

        Returns the mock buy for further customization if needed.
        """
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
        self._buys.append(buy)

        # Update repo mock
        if self._uow_instance is not None:
            self._uow_instance.media_buys.get_by_principal.return_value = list(self._buys)

        return buy

    def set_pricing_options(self, pricing_map: dict[str, Any]) -> None:
        """Configure pricing option lookup results."""
        self.mock["pricing"].return_value = pricing_map
