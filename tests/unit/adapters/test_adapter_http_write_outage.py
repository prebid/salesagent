"""Contract tests for adapter HTTP-write outage recovery (PAT-01).

A transport outage on an adapter write must surface as ``AdCPAdapterError``
(wire ``SERVICE_UNAVAILABLE`` / recovery ``transient``) so a buyer agent retries.
A bare ``raise_for_status()`` escapes raw and normalizes at the boundary to the
base ``AdCPError`` -> ``INTERNAL_ERROR`` / recovery ``terminal``, telling the
agent to escalate to a human instead — opposite recovery for the same outage.

The original update-targeting test mocked ``raise_for_status`` to *succeed*, so
this contract was never exercised and the regression shipped. These tests pin
both the shared mapping (``wrap_request_errors``, the single source every write
routes through) and an end-to-end real-method path (Kevel update targeting).
The structural guard ``test_architecture_adapter_http_writes_wrapped`` proves
every other write site routes through the same mapping.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

import pytest
import requests

from src.adapters.kevel import Kevel
from src.adapters.utils import wrap_request_errors
from src.core.exceptions import AdCPAdapterError
from src.core.schemas import Principal, Targeting

pytestmark = pytest.mark.unit


def _kevel() -> Kevel:
    return Kevel(
        config={"network_id": "100", "api_key": "test-key"},
        principal=Principal(
            principal_id="p_test",
            name="Test Principal",
            platform_mappings={"kevel": {"advertiser_id": "kevel_adv_1"}},
        ),
        dry_run=False,
        tenant_id="t_test",
    )


def _raise_in_wrap(exc: BaseException) -> None:
    """Raise ``exc`` inside ``wrap_request_errors()`` (callable form keeps the raise
    out of a nested ``with`` so the mapping is asserted without CodeQL reading the
    follow-up asserts as unreachable)."""
    with wrap_request_errors():
        raise exc


class TestWrapRequestErrorsMapping:
    """The shared SSOT mapping every adapter write routes through."""

    def test_request_exception_maps_to_transient_adapter_error(self):
        exc_info = pytest.raises(AdCPAdapterError, _raise_in_wrap, requests.exceptions.ConnectionError("unreachable"))
        assert exc_info.value.error_code == "SERVICE_UNAVAILABLE"
        assert exc_info.value.recovery == "transient"

    def test_timeout_maps_to_transient_adapter_error(self):
        exc_info = pytest.raises(AdCPAdapterError, _raise_in_wrap, requests.exceptions.Timeout("read timed out"))
        assert exc_info.value.recovery == "transient"

    def test_non_request_exception_passes_through_unchanged(self):
        # Only transport failures are remapped; a programmer error must not be
        # disguised as a transient outage.
        pytest.raises(KeyError, _raise_in_wrap, KeyError("Id"))


class TestKevelUpdateTargetingOutage:
    """End-to-end: the real PR-new write method surfaces a transient error on outage."""

    def test_update_package_targeting_outage_is_transient(self):
        adapter = _kevel()
        # The flight Name->Id resolve GET succeeds; the targeting PUT is the outage.
        with patch("src.adapters.kevel.requests.get") as mock_get:
            mock_get.return_value.json.return_value = {"items": [{"Name": "flight_9", "Id": 555}]}
            with patch(
                "src.adapters.kevel.requests.put",
                side_effect=requests.exceptions.ConnectionError("flight PUT failed"),
            ):
                with pytest.raises(AdCPAdapterError) as exc_info:
                    adapter.update_package_targeting("kevel_42", "flight_9", Targeting(), datetime.now(UTC).date())
        assert exc_info.value.error_code == "SERVICE_UNAVAILABLE"
        assert exc_info.value.recovery == "transient"
