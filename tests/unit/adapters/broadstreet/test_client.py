"""Unit tests for Broadstreet API client."""

from unittest.mock import MagicMock, patch

import pytest

from src.adapters.broadstreet.client import BroadstreetAPIError, BroadstreetClient


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

        assert exc_info.value.status_code == 403
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

        assert exc_info.value.status_code == 404

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

        assert exc_info.value.status_code == 500
        assert "server error" in str(exc_info.value)
