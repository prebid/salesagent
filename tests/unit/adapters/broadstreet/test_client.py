"""Unit tests for Broadstreet API client."""

from unittest.mock import MagicMock, patch

import pytest

from src.adapters.broadstreet.client import BroadstreetClient
from src.core.exceptions import (
    AdCPAdapterResourceNotFoundError,
    AdCPAuthorizationError,
    AdCPServiceUnavailableError,
)
from src.core.security.outbound_http import OutboundDeliveryFailed


def _seam_result(*, content: bytes, body):
    """A stand-in for the seam's OutboundResult on a successful call.

    The client reads ``result.content`` to decide whether there is a body at
    all, then ``result.json()`` — the same two touchpoints the real result
    has (salesagent-tbrk.5 closed OutboundResult over content/text/headers
    directly -- no more ``.response`` reach-through).
    """
    result = MagicMock()
    result.content = content
    result.json.return_value = body
    return result


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

    @patch("src.adapters.vendor_http.send")
    def test_get_network(self, mock_send):
        """Test getting network details."""
        mock_send.return_value = _seam_result(
            content=b'{"network": {"id": "12345", "name": "Test Network"}}',
            body={"network": {"id": "12345", "name": "Test Network"}},
        )

        client = BroadstreetClient(
            access_token="test_token",
            network_id="12345",
        )

        result = client.get_network()

        # Client unwraps the "network" key
        assert result == {"id": "12345", "name": "Test Network"}
        # The credential rides the query string, so it is a kwarg of the seam call,
        # not part of the URL the client assembles. A bare assert_called_once() here
        # graded the hit count and nothing about what was sent.
        mock_send.assert_called_once_with(
            "https://api.broadstreetads.com/api/0/networks/12345",
            method="GET",
            headers={},
            json=None,
            params={"access_token": "test_token"},
            timeout=30.0,
            max_attempts=1,
        )

    @patch("src.adapters.vendor_http.send")
    def test_get_zones(self, mock_send):
        """Test getting zones for network."""
        mock_send.return_value = _seam_result(
            content=b'{"zones": [{"id": "1", "name": "Banner"}]}', body={"zones": [{"id": "1", "name": "Banner"}]}
        )

        client = BroadstreetClient(
            access_token="test_token",
            network_id="12345",
        )

        result = client.get_zones()

        assert len(result) == 1
        assert result[0]["name"] == "Banner"

    @patch("src.adapters.vendor_http.send")
    def test_create_campaign(self, mock_send):
        """Test creating a campaign."""
        mock_send.return_value = _seam_result(
            content=b'{"campaign": {"id": "999", "name": "Test Campaign"}}',
            body={"campaign": {"id": "999", "name": "Test Campaign"}},
        )

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

    @patch("src.adapters.vendor_http.send")
    def test_handle_403_error(self, mock_send):
        """Test handling 403 authentication error."""
        # The seam raises on a non-2xx rather than returning it, and discards the
        # response — so the client rebuilds its status-specific error from the typed
        # failure. response_body is None now: a counterparty's error body is exactly
        # what the seam declines to carry back.
        mock_send.side_effect = OutboundDeliveryFailed(attempts=1, http_status=403)

        client = BroadstreetClient(
            access_token="invalid_token",
            network_id="12345",
        )

        with pytest.raises(AdCPAuthorizationError) as exc_info:
            client.get_network()

        # Asserting the CLASS, not the upstream status: the class is what decides
        # the code the buyer reads. HTTP 403 -> PERMISSION_DENIED.
        assert exc_info.value.error_code == "PERMISSION_DENIED"

    @patch("src.adapters.vendor_http.send")
    def test_handle_404_error(self, mock_send):
        """Test handling 404 not found error."""
        # The seam raises on a non-2xx rather than returning it, and discards the
        # response — so the client rebuilds its status-specific error from the typed
        # failure. response_body is None now: a counterparty's error body is exactly
        # what the seam declines to carry back.
        mock_send.side_effect = OutboundDeliveryFailed(attempts=1, http_status=404)

        client = BroadstreetClient(
            access_token="test_token",
            network_id="12345",
        )

        with pytest.raises(AdCPAdapterResourceNotFoundError) as exc_info:
            client.get_advertiser("nonexistent")

        assert exc_info.value.error_code == "REFERENCE_NOT_FOUND"

    @patch("src.adapters.vendor_http.send")
    def test_handle_500_error(self, mock_send):
        """Test handling 500 server error."""
        # The seam raises on a non-2xx rather than returning it, and discards the
        # response — so the client rebuilds its status-specific error from the typed
        # failure. response_body is None now: a counterparty's error body is exactly
        # what the seam declines to carry back.
        mock_send.side_effect = OutboundDeliveryFailed(attempts=1, http_status=500)

        client = BroadstreetClient(
            access_token="test_token",
            network_id="12345",
        )

        with pytest.raises(AdCPServiceUnavailableError) as exc_info:
            client.get_network()

        # Asserting the CLASS, not the upstream status: the class is what decides
        # the code the buyer reads. HTTP 500 -> SERVICE_UNAVAILABLE.
        assert exc_info.value.error_code == "SERVICE_UNAVAILABLE"
