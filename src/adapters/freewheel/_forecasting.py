"""FreeWheel Forecasting API v4 client.

The nightly forecast endpoint is placement-scoped and returns the latest
nightly delivery/pacing snapshot for that placement:

``GET /services/v4/placements/{placement_id}/forecasts?type=nightly``

Unlike the Query Reporting API, this is a lightweight read against the
Publisher API v4 surface. It is useful as a delivery fallback for accounts
that can read Forecasting API v4 but still lack ``/reporting/*`` scope.
"""

from __future__ import annotations

from src.adapters.freewheel._transport import FreeWheelTransport
from src.adapters.freewheel.entities import NightlyForecast


class FreeWheelForecastingClient:
    """Forecasting API v4 read client."""

    def __init__(self, transport: FreeWheelTransport):
        self._transport = transport

    def nightly_forecast(self, placement_id: int | str) -> NightlyForecast:
        """Return the latest nightly forecast for one FreeWheel placement."""
        body = self._transport.get_json(
            f"/services/v4/placements/{int(placement_id)}/forecasts",
            type="nightly",
        )
        return NightlyForecast.model_validate(body)
