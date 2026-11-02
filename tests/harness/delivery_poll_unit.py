"""DeliveryPollEnv — unit test environment for _get_media_buy_delivery_impl.

Patches: MediaBuyUoW, get_adapter, _get_pricing_options

Usage::

    with DeliveryPollEnv() as env:
        env.add_buy(media_buy_id="mb_001", start_date=date(2025, 1, 1))
        env.set_adapter_response("mb_001", impressions=5000, spend=250.0)
        response = env.call_impl(media_buy_ids=["mb_001"])
        assert response.aggregated_totals.impressions == 5000.0

Available mocks via env.mock:
    "uow"       -- MediaBuyUoW class mock
    "adapter"    -- get_adapter mock
    "pricing"    -- _get_pricing_options mock
"""

from __future__ import annotations

from datetime import date
from typing import Any
from unittest.mock import MagicMock

from src.core.schemas import AdapterGetMediaBuyDeliveryResponse
from tests.factories.media_buy import default_request_packages, pricing_options_for
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
        "adapter": f"{MODULE}.get_adapter",
        "pricing": f"{MODULE}._get_pricing_options",
        "circuit_open": f"{MODULE}._is_circuit_breaker_open",
    }

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._buys: list[MagicMock] = []
        self._adapter_responses: dict[str, AdapterGetMediaBuyDeliveryResponse] = {}
        self._adapter_error: Exception | None = None
        self._uow_instance: MagicMock | None = None

    def _configure_mocks(self) -> None:
        # UoW: replace mock class with make_mock_uow
        uow_cls, self._uow_instance = make_mock_uow()
        self.mock["uow"].return_value = self._uow_instance
        # Wire context manager protocol through the class mock
        self.mock["uow"].return_value.__enter__ = self._uow_instance.__enter__
        self.mock["uow"].return_value.__exit__ = self._uow_instance.__exit__

        # Adapter: default happy path (from mixin)
        self._configure_adapter_mock()

        # Pricing: answer about the ids production actually asked for, the way the real
        # lookup does. ``get-media-buy-delivery-response.json`` REQUIRES pricing_model,
        # rate and currency on every by_package entry, and a unit buy has no MediaPackage
        # row to carry them, so this mock is the buy's only pricing source.
        self.mock["pricing"].side_effect = lambda option_ids, **_: pricing_options_for(option_ids)

        # Packages: a mocked repo hands back a MagicMock, which reads as a dict with no
        # entries only by accident; say so.
        self._uow_instance.media_buys.get_packages_for_ids.return_value = {}

        # Circuit breaker: CLOSED. A bare MagicMock return value is TRUTHY, so leaving it
        # unset runs every test in this env with the breaker OPEN — which rewrites an
        # active buy's status to "reporting_delayed" before any assertion sees it.
        self.mock["circuit_open"].return_value = False

    def add_buy(
        self,
        media_buy_id: str = "mb_001",
        start_date: date = date(2025, 1, 1),
        end_date: date = date(2027, 12, 31),
        budget: float = 10000.0,
        currency: str = "USD",
        raw_request: dict[str, Any] | None = None,
        is_paused: bool = False,
        status: str = "active",
    ) -> MagicMock:
        """Add a mock MediaBuy to the repository.

        ``status`` is the persisted lifecycle status. It defaults to the
        generic "active" serving state, which ``_get_target_media_buys``
        refines against the flight window so date-only callers still resolve
        to ready/active/completed.

        Returns the mock buy for further customization if needed.
        """
        buy = MagicMock()
        buy.media_buy_id = media_buy_id
        buy.start_date = start_date
        buy.end_date = end_date
        buy.start_time = None
        buy.end_time = None
        buy.budget = budget
        buy.currency = currency
        buy.is_paused = is_paused
        buy.status = status
        # Packages name a pricing option, because ``package-request.json`` REQUIRES one on
        # every package — a buy without it is a shape ``create_media_buy`` cannot store.
        buy.raw_request = raw_request or {"packages": default_request_packages()}
        self._buys.append(buy)

        # Update repo mock
        if self._uow_instance is not None:
            self._uow_instance.media_buys.get_by_principal.return_value = list(self._buys)

        return buy

    def set_circuit_open(self, is_open: bool) -> None:
        """Open or close the tenant's reporting circuit breaker for this env.

        The default is CLOSED (``_configure_mocks``). A test about degraded reporting
        states the state it is about here, rather than depending on what an unconfigured
        mock happens to return.
        """
        self.mock["circuit_open"].return_value = is_open

    def set_pricing_options(self, pricing_map: dict[str, Any]) -> None:
        """Configure pricing option lookup results, replacing the derived default."""
        self.mock["pricing"].side_effect = None
        self.mock["pricing"].return_value = pricing_map
