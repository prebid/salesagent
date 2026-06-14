"""Raise-site coverage for the typed errors xandr/triton emit on adapter failures (F24).

``test_architecture_adapter_raise_site_coverage`` checks each *explicit* adapter
``raise AdCP*Error`` has a test; these are that test for the two sites this PR's
batch added, pinning the deliberate recovery semantics the code comments call out:

- Xandr ``_authenticate`` rejects a bad credential as a TERMINAL
  ``AdCPConfigurationError`` (the operator must fix server config), distinct from
  a transport outage which ``wrap_request_errors`` maps to a transient
  ``AdCPAdapterError``. A regression swapping the raise to a bare re-raise or
  ``AdCPAdapterError`` would flip recovery to transient and send buyers into a
  retry loop on an unfixable credential — caught here.
- Triton ``get_media_buy_delivery`` wraps a transport ``RequestException`` as a
  transient ``AdCPAdapterError`` instead of letting the raw requests error escape
  to INTERNAL_ERROR/terminal.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, Mock, patch

import pytest
import requests

from src.adapters.triton_digital import TritonDigital
from src.adapters.xandr import XandrAdapter
from src.core.exceptions import AdCPAdapterError, AdCPConfigurationError


def _xandr_adapter() -> XandrAdapter:
    principal = Mock()
    principal.name = "test_principal"
    principal.principal_id = "principal_123"
    # Real dict: XandrAdapter.__init__ does ``"xandr" in principal.platform_mappings``.
    principal.platform_mappings = {"xandr": {"advertiser_id": "789"}}
    config = {
        "api_endpoint": "https://api.appnexus.test",
        "username": "u",
        "password": "p",
        "member_id": "123",
    }
    with patch.multiple("src.adapters.xandr.XandrAdapter", __abstractmethods__=set()):
        return XandrAdapter(config=config, principal=principal, tenant_id="test_tenant")


class TestXandrAuthRejectionRaiseSite:
    """A rejected Xandr credential is terminal, not a transient retry."""

    # _authenticate is @api_retry (retries on Exception); skip the backoff sleeps.
    @patch("src.core.retry_utils.time.sleep", lambda *_a, **_k: None)
    @patch("src.adapters.xandr.requests.post")
    def test_rejected_credential_is_terminal_configuration_error(self, mock_post):
        # 200 OK whose body reports the credential was rejected (NOT a transport
        # outage — raise_for_status is a no-op, so wrap_request_errors does not fire).
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = Mock()
        resp.json.return_value = {"response": {"status": "ERROR", "error": "UNAUTH"}}
        mock_post.return_value = resp

        adapter = _xandr_adapter()
        with pytest.raises(AdCPConfigurationError) as exc_info:
            adapter._authenticate()
        # Terminal: the buyer has no lever — must NOT degrade to transient retry.
        assert exc_info.value.recovery == "terminal"
        assert exc_info.value.error_code == "CONFIGURATION_ERROR"


class TestTritonDeliveryReportRaiseSite:
    """A transport outage fetching the Triton delivery report is transient, not terminal."""

    @patch("src.adapters.triton_digital.requests.post")
    def test_request_exception_wrapped_as_transient_adapter_error(self, mock_post):
        mock_post.side_effect = requests.exceptions.RequestException("connection reset")

        principal = Mock()
        principal.name = "test_principal"
        adapter = TritonDigital(
            config={"api_key": "k", "base_url": "https://api.tritondigital.test", "auth_token": "t"},
            principal=principal,
            dry_run=False,  # live path so the real requests.post is reached
            tenant_id="tenant_123",
        )

        date_range = MagicMock()
        date_range.start = datetime(2026, 1, 1, tzinfo=UTC)
        date_range.end = datetime(2026, 1, 31, tzinfo=UTC)

        with pytest.raises(AdCPAdapterError) as exc_info:
            adapter.get_media_buy_delivery("mb_1", date_range, today=datetime.now(UTC) + timedelta(days=5))
        # Transient by inheritance: a delivery-report outage is retryable.
        assert exc_info.value.recovery == "transient"
        assert exc_info.value.error_code == "SERVICE_UNAVAILABLE"
