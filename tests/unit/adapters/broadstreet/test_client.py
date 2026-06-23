"""Unit tests for Broadstreet API client."""

from unittest.mock import MagicMock, patch

import pytest
import requests

from src.adapters.broadstreet.client import BroadstreetAPIError, BroadstreetClient
from src.core.exceptions import AdCPAdapterError, build_two_layer_error_envelope


class TestBroadstreetClient:
    """Tests for BroadstreetClient."""

    def test_init_with_required_params(self):
        """Test client initialization with required parameters."""
        client = BroadstreetClient(
            access_token="test_token",
            network_id="12345",
        )

        assert client.access_token == "test_token"
        assert client.network_id == "12345"
        assert client.base_url == "https://api.broadstreetads.com/api/0"
        assert client.timeout == 30

    def test_init_with_custom_base_url(self):
        """Test client initialization with custom base URL."""
        client = BroadstreetClient(
            access_token="test_token",
            network_id="12345",
            base_url="https://custom.api.com/v1/",
        )

        # Should strip trailing slash
        assert client.base_url == "https://custom.api.com/v1"

    def test_build_url_includes_access_token(self):
        """Test URL building includes access token."""
        client = BroadstreetClient(
            access_token="test_token",
            network_id="12345",
        )

        url = client._build_url("/networks/12345")

        assert "access_token=test_token" in url
        assert url.startswith("https://api.broadstreetads.com/api/0/networks/12345")

    def test_build_url_with_query_params(self):
        """Test URL building with additional query parameters."""
        client = BroadstreetClient(
            access_token="test_token",
            network_id="12345",
        )

        url = client._build_url("/test", query_params={"start_date": "2024-01-01"})

        assert "access_token=test_token" in url
        assert "start_date=2024-01-01" in url

    @patch("src.adapters.broadstreet.client.requests.request")
    def test_get_network(self, mock_request):
        """Test getting network details."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b'{"network": {"id": "12345", "name": "Test Network"}}'
        mock_response.json.return_value = {"network": {"id": "12345", "name": "Test Network"}}
        mock_request.return_value = mock_response

        client = BroadstreetClient(
            access_token="test_token",
            network_id="12345",
        )

        result = client.get_network()

        # Client unwraps the "network" key
        assert result == {"id": "12345", "name": "Test Network"}
        mock_request.assert_called_once()

    @patch("src.adapters.broadstreet.client.requests.request")
    def test_get_zones(self, mock_request):
        """Test getting zones for network."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b'{"zones": [{"id": "1", "name": "Banner"}]}'
        mock_response.json.return_value = {"zones": [{"id": "1", "name": "Banner"}]}
        mock_request.return_value = mock_response

        client = BroadstreetClient(
            access_token="test_token",
            network_id="12345",
        )

        result = client.get_zones()

        assert len(result) == 1
        assert result[0]["name"] == "Banner"

    @patch("src.adapters.broadstreet.client.requests.request")
    def test_create_campaign(self, mock_request):
        """Test creating a campaign."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b'{"campaign": {"id": "999", "name": "Test Campaign"}}'
        mock_response.json.return_value = {"campaign": {"id": "999", "name": "Test Campaign"}}
        mock_request.return_value = mock_response

        client = BroadstreetClient(
            access_token="test_token",
            network_id="12345",
        )

        result = client.create_campaign(
            advertiser_id="456",
            name="Test Campaign",
            start_date="2024-01-01",
            end_date="2024-12-31",
        )

        assert result["id"] == "999"
        assert result["name"] == "Test Campaign"

    @patch("src.adapters.broadstreet.client.requests.request")
    def test_handle_403_error(self, mock_request):
        """Test handling 403 authentication error."""
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.content = b'{"error": "Forbidden"}'
        mock_response.json.return_value = {"error": "Forbidden"}
        mock_request.return_value = mock_response

        client = BroadstreetClient(
            access_token="invalid_token",
            network_id="12345",
        )

        with pytest.raises(BroadstreetAPIError) as exc_info:
            client.get_network()

        # status_code is the mapped AdCP class default (uniform with wrap_request_errors),
        # not the upstream 403; the upstream 403 stays in the message ("(HTTP 403)").
        assert exc_info.value.status_code == 500
        assert exc_info.value.recovery == "terminal"
        assert "Auth Denied" in str(exc_info.value)

    @patch("src.adapters.broadstreet.client.requests.request")
    def test_handle_404_error(self, mock_request):
        """Test handling 404 not found error."""
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.content = b'{"error": "Not found"}'
        mock_response.json.return_value = {"error": "Not found"}
        mock_request.return_value = mock_response

        client = BroadstreetClient(
            access_token="test_token",
            network_id="12345",
        )

        with pytest.raises(BroadstreetAPIError) as exc_info:
            client.get_advertiser("nonexistent")

        # Mapped class default (uniform with wrap), not the upstream 404; the upstream
        # 404 stays in the message ("Resource not found (HTTP 404)").
        assert exc_info.value.status_code == 400
        assert exc_info.value.recovery == "correctable"
        assert "not found" in str(exc_info.value).lower()

    @patch("src.adapters.broadstreet.client.requests.request")
    def test_handle_500_error(self, mock_request):
        """Test handling 500 server error."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.content = b'{"error": "Internal server error"}'
        mock_response.json.return_value = {"error": "Internal server error"}
        mock_request.return_value = mock_response

        client = BroadstreetClient(
            access_token="test_token",
            network_id="12345",
        )

        with pytest.raises(BroadstreetAPIError) as exc_info:
            client.get_network()

        # An upstream 5xx maps to AdCPAdapterError's 502 (uniform with wrap); the
        # upstream 500 stays in the message ("server error (HTTP 500)").
        assert exc_info.value.status_code == 502
        assert exc_info.value.recovery == "transient"
        assert "server error" in str(exc_info.value)

    @patch("src.adapters.broadstreet.client.requests.request")
    def test_transport_failure_message_does_not_leak_access_token(self, mock_request):
        # A requests error stringifies the request URL, which carries the operator's
        # ?access_token=... query param. The buyer-facing message (which reaches the wire
        # via the typed-error boundary) MUST NOT contain it. Failing oracle: reverting the
        # client.py sanitization to ``f"Request failed: {e}"`` reddens this.
        secret = "SUPER_SECRET_TOKEN_xyz"  # noqa: S105 — test fixture, not a real credential
        mock_request.side_effect = requests.exceptions.ConnectionError(
            f"HTTPSConnectionPool(host='api.broadstreetads.com', port=443): Max retries exceeded "
            f"with url: /api/0/networks/42/advertisers?access_token={secret} (Caused by ...)"
        )
        client = BroadstreetClient(access_token=secret, network_id="42")

        with pytest.raises(BroadstreetAPIError) as exc_info:
            client.get_network()

        assert secret not in str(exc_info.value), "access_token leaked into the BroadstreetAPIError message"
        wire_message = build_two_layer_error_envelope(exc_info.value)["errors"][0]["message"]
        assert secret not in wire_message, "access_token leaked onto the buyer wire envelope"


class TestBroadstreetAPIErrorRecoveryTaxonomy:
    """BroadstreetAPIError must carry the AdCP recovery taxonomy (F01).

    Broadstreet uses manual ``response.status_code`` checks rather than
    ``raise_for_status()``, so it is invisible to the
    ``test_architecture_adapter_http_writes_wrapped`` AST guard. These tests are
    the regression net in its place: a Broadstreet write outage must surface a
    transient ``AdCPAdapterError`` (``SERVICE_UNAVAILABLE``) rather than
    normalizing to ``INTERNAL_ERROR``/``terminal`` (which tells a buyer agent to
    escalate to a human on a transient ad-server outage), and the per-status
    refinement must hold.
    """

    def test_is_adcp_adapter_error_subclass(self):
        # If this ever reverts to a plain Exception, every Broadstreet write
        # outage silently becomes INTERNAL_ERROR/terminal again.
        assert issubclass(BroadstreetAPIError, AdCPAdapterError)

    @pytest.mark.parametrize(
        ("status_code", "expected_code", "expected_recovery", "expected_status"),
        [
            (None, "SERVICE_UNAVAILABLE", "transient", 502),  # transport outage (RequestException)
            (500, "SERVICE_UNAVAILABLE", "transient", 502),
            (503, "SERVICE_UNAVAILABLE", "transient", 502),
            (429, "RATE_LIMITED", "transient", 429),  # rate limited — retry with backoff, not a client error
            (403, "CONFIGURATION_ERROR", "terminal", 500),  # operator access_token denied
            (404, "VALIDATION_ERROR", "correctable", 400),
            (400, "VALIDATION_ERROR", "correctable", 400),
        ],
    )
    def test_status_maps_to_recovery(self, status_code, expected_code, expected_recovery, expected_status):
        err = BroadstreetAPIError("boom", status_code=status_code)
        assert err.error_code == expected_code
        assert err.recovery == expected_recovery
        # status_code is the MAPPED AdCP class default — the SAME the wrap_request_errors
        # factory path yields for this status — NOT the upstream ad-server status. One
        # ad-server event yields one buyer-facing REST status regardless of adapter; the
        # upstream status stays in the message text / response_body.
        assert err.status_code == expected_status
