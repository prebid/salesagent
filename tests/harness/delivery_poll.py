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

from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

from src.core.schemas import (
    AdapterGetMediaBuyDeliveryResponse,
    AdapterPackageDelivery,
    DeliveryTotals,
    GetMediaBuyDeliveryRequest,
    GetMediaBuyDeliveryResponse,
    ReportingPeriod,
)
from src.core.tools.media_buy_delivery import _get_media_buy_delivery_impl
from tests.harness._base import IntegrationEnv


class DeliveryPollEnv(IntegrationEnv):
    """Integration test environment for _get_media_buy_delivery_impl.

    Only mocks the adapter (external ad server). Everything else is real:
    - Real MediaBuyUoW → real DB queries
    - Real get_principal_object → real DB queries
    - Real _get_pricing_options → real DB queries

    Fluent API:
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
        # Adapter: default happy path with side_effect lookup
        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.side_effect = self._adapter_lookup
        self.mock["adapter"].return_value = mock_adapter

    def _adapter_lookup(self, *args: Any, **kwargs: Any) -> AdapterGetMediaBuyDeliveryResponse:
        """Look up configured adapter response by media_buy_id."""
        mb_id = kwargs.get("media_buy_id") or (args[0] if args else None)
        if mb_id and mb_id in self._adapter_responses:
            return self._adapter_responses[mb_id]
        if self._adapter_responses:
            return next(iter(self._adapter_responses.values()))
        return self._make_default_adapter_response()

    def set_adapter_response(
        self,
        media_buy_id: str = "mb_001",
        impressions: int = 5000,
        spend: float = 250.0,
        package_id: str = "pkg_001",
        clicks: int | None = None,
    ) -> None:
        """Configure adapter to return specific delivery data for a media buy."""
        totals = DeliveryTotals(
            impressions=float(impressions),
            spend=spend,
        )
        if clicks is not None:
            totals.clicks = float(clicks)

        by_package = [
            AdapterPackageDelivery(
                package_id=package_id,
                impressions=impressions,
                spend=spend,
            )
        ]

        self._adapter_responses[media_buy_id] = AdapterGetMediaBuyDeliveryResponse(
            media_buy_id=media_buy_id,
            reporting_period=ReportingPeriod(
                start=datetime(2025, 1, 1, tzinfo=UTC),
                end=datetime(2025, 12, 31, tzinfo=UTC),
            ),
            totals=totals,
            by_package=by_package,
            currency="USD",
        )

    def set_adapter_error(self, exception: Exception) -> None:
        """Make the adapter raise the given exception on get_media_buy_delivery."""
        self.mock["adapter"].return_value.get_media_buy_delivery.side_effect = exception

    def call_impl(
        self,
        media_buy_ids: list[str] | None = None,
        buyer_refs: list[str] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        status_filter: list[str] | None = None,
        **extra: Any,
    ) -> GetMediaBuyDeliveryResponse:
        """Call _get_media_buy_delivery_impl with real DB.

        Commits all factory-created data before calling production code.
        """
        self._commit_factory_data()

        kwargs: dict[str, Any] = {}
        if media_buy_ids is not None:
            kwargs["media_buy_ids"] = media_buy_ids
        if buyer_refs is not None:
            kwargs["buyer_refs"] = buyer_refs
        if start_date is not None:
            kwargs["start_date"] = start_date
        if end_date is not None:
            kwargs["end_date"] = end_date
        if status_filter is not None:
            kwargs["status_filter"] = status_filter
        kwargs.update(extra)

        req = GetMediaBuyDeliveryRequest(**kwargs)
        return _get_media_buy_delivery_impl(req, self.identity)

    @staticmethod
    def _make_default_adapter_response() -> AdapterGetMediaBuyDeliveryResponse:
        return AdapterGetMediaBuyDeliveryResponse(
            media_buy_id="mb_001",
            reporting_period=ReportingPeriod(
                start=datetime(2025, 1, 1, tzinfo=UTC),
                end=datetime(2025, 12, 31, tzinfo=UTC),
            ),
            totals=DeliveryTotals(impressions=5000.0, spend=250.0),
            by_package=[AdapterPackageDelivery(package_id="pkg_001", impressions=5000, spend=250.0)],
            currency="USD",
        )
