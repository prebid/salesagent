"""Tests for the FreeWheel Forecasting API v4 client."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

from src.adapters.freewheel._forecasting import FreeWheelForecastingClient


def test_nightly_forecast_uses_placement_scoped_endpoint():
    transport = MagicMock()
    transport.get_json.return_value = {
        "placement_id": 900003,
        "run_time": "2026-05-22T12:00:00Z",
        "on_schedule_indicator": 95,
        "delivered_impressions": 12345,
        "delivered_budget": "67.89",
        "forecast_to_be_delivered_impressions": 25000,
        "exchange_currency": "EUR",
    }

    client = FreeWheelForecastingClient(transport)
    result = client.nightly_forecast("900003")

    transport.get_json.assert_called_once_with(
        "/services/v4/placements/900003/forecasts",
        type="nightly",
    )
    assert result.placement_id == 900003
    assert result.delivered_impressions == 12345
    assert result.delivered_budget == Decimal("67.89")
    assert result.exchange_currency == "EUR"
