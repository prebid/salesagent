"""Regression tests for Kevel.add_creative_assets flight-fetch fold (B5).

The real-mode branch fetches the campaign's flights to map package names → flight
IDs. It was the third byte-identical copy of that GET /flight + Name→Id map; the
fold routes it through the shared ``_fetch_campaign_flights`` helper, which:

1. strips the ``kevel_`` media_buy_id prefix to match the campaignId convention
   every other flight-fetch path uses (it previously passed the prefixed id raw,
   a latent inconsistency), and
2. raises ``AdCPAdapterError`` on a transport outage (via ``wrap_request_errors``)
   instead of the bare ``RequestException`` the local copy raised — so the
   swallowing handler was widened to keep degrading gracefully.

These pin both behaviors so neither silently regresses.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
import requests

from src.adapters.kevel import Kevel
from src.core.schemas import Principal

pytestmark = pytest.mark.unit


def _kevel() -> Kevel:
    return Kevel(
        config={"network_id": "100", "api_key": "test-key"},
        principal=Principal(
            principal_id="p_test", name="Test", platform_mappings={"kevel": {"advertiser_id": "adv_1"}}
        ),
        dry_run=False,
        tenant_id="t_test",
    )


def _image_asset(creative_id: str = "cr_1", package_id: str = "pkg_1") -> dict:
    return {
        "creative_id": creative_id,
        "name": f"Creative {creative_id}",
        "format": "image",
        "media_url": "https://example.com/img.png",
        "click_url": "https://example.com/click",
        "package_assignments": [package_id],
    }


def _status_get_mock(status_code: int) -> MagicMock:
    """A ``requests.get`` replacement whose GET /flight response.raise_for_status() raises an
    HTTPError carrying ``status_code`` — exercising wrap_request_errors' status->AdCPError mapping
    (429->AdCPRateLimitError, 4xx->AdCPValidationError, 5xx->AdCPAdapterError)."""
    response = MagicMock(status_code=status_code)
    http_error = requests.exceptions.HTTPError(str(status_code))
    http_error.response = response
    response.raise_for_status = MagicMock(side_effect=http_error)
    return MagicMock(return_value=response)


class TestAddCreativeAssetsFlightFetchFold:
    """The fold preserves graceful degradation and corrects the campaignId prefix."""

    def test_flight_fetch_uses_kevel_stripped_campaign_id(self):
        # The GET /flight campaignId must be the ``kevel_``-stripped id, matching every other
        # flight-fetch path (update_package_targeting / update_media_buy). The pre-fold copy
        # passed the prefixed media_buy_id raw — folding through _fetch_campaign_flights fixes it.
        adapter = _kevel()
        flights = MagicMock()
        flights.raise_for_status = MagicMock()
        flights.json = MagicMock(return_value={"items": [{"Name": "pkg_1", "Id": 111}]})
        creative = MagicMock()
        creative.raise_for_status = MagicMock()
        creative.json = MagicMock(return_value={"Id": 222})
        ad = MagicMock()
        ad.raise_for_status = MagicMock()

        with (
            patch("src.adapters.kevel.requests.get", return_value=flights) as mock_get,
            patch("src.adapters.kevel.requests.post", side_effect=[creative, ad]),
        ):
            result = adapter.add_creative_assets("kevel_PO1", [_image_asset()], datetime(2026, 6, 1, tzinfo=UTC))

        _, kwargs = mock_get.call_args
        assert kwargs["params"] == {"campaignId": "PO1"}, "campaignId must strip the kevel_ prefix"
        assert [s.status for s in result] == ["approved"]

    @pytest.mark.parametrize(
        ("get_mock", "label"),
        [
            (MagicMock(side_effect=requests.exceptions.ConnectionError("kevel down")), "outage->AdCPAdapterError"),
            (_status_get_mock(429), "429->AdCPRateLimitError"),
            (_status_get_mock(403), "403->AdCPValidationError"),
            (_status_get_mock(503), "503->AdCPAdapterError"),
        ],
    )
    def test_flight_fetch_failure_always_degrades_gracefully(self, get_mock, label):
        # _fetch_campaign_flights' status-aware wrap maps each failure to a DIFFERENT AdCPError
        # subclass (429->RateLimit, 4xx->Validation, 5xx/outage->Adapter). All must degrade to
        # "failed", not escape add_creative_assets' per-asset status contract. A regression to
        # `except (RequestException, AdCPAdapterError)` reddens the 429 and 403 arms (neither class
        # is an AdCPAdapterError subclass) while leaving the outage/503 arms green.
        adapter = _kevel()
        with patch("src.adapters.kevel.requests.get", get_mock):
            result = adapter.add_creative_assets("kevel_PO1", [_image_asset()], datetime(2026, 6, 1, tzinfo=UTC))

        assert [s.status for s in result] == ["failed"], f"{label}: a flight-fetch failure must degrade, not escape"
