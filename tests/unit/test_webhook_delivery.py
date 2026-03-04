"""Unit tests for webhook delivery service with exponential backoff retry logic."""

from unittest.mock import Mock, call, patch

import requests

from src.core.webhook_delivery import WebhookDelivery, deliver_webhook_with_retry


@patch("src.core.webhook_delivery.time.sleep")
class TestWebhookDelivery:
    """Test cases for webhook delivery with exponential backoff retry."""

    def test_successful_delivery_first_attempt(self, mock_sleep):
        """Test successful delivery on first attempt (200 OK)."""
        delivery = WebhookDelivery(
            webhook_url="https://example.com/webhook",
            payload={"test": "data"},
            headers={"Content-Type": "application/json"},
            max_retries=3,
            timeout=10,
        )

        with patch("requests.post") as mock_post:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_post.return_value = mock_response

            success, result = deliver_webhook_with_retry(delivery)

            assert success is True
            assert result["status"] == "delivered"
            assert result["attempts"] == 1
            assert result["response_code"] == 200
            assert "delivery_id" in result
            assert mock_post.call_count == 1

    def test_successful_delivery_after_retry(self, mock_sleep):
        """Test successful delivery after 5xx error retry."""
        delivery = WebhookDelivery(
            webhook_url="https://example.com/webhook",
            payload={"test": "data"},
            headers={"Content-Type": "application/json"},
            max_retries=3,
            timeout=10,
        )

        with patch("requests.post") as mock_post:
            # First attempt: 503 Service Unavailable
            # Second attempt: 200 OK
            mock_response_503 = Mock()
            mock_response_503.status_code = 503
            mock_response_503.text = "Service temporarily unavailable"

            mock_response_200 = Mock()
            mock_response_200.status_code = 200

            mock_post.side_effect = [mock_response_503, mock_response_200]

            success, result = deliver_webhook_with_retry(delivery)

            assert success is True
            assert result["status"] == "delivered"
            assert result["attempts"] == 2
            assert result["response_code"] == 200
            assert mock_post.call_count == 2

            # Should have backed off 1 second between attempts (2^0 = 1)
            mock_sleep.assert_called_once_with(1)

    def test_retry_on_500_error(self, mock_sleep):
        """Test that 5xx errors trigger retry."""
        delivery = WebhookDelivery(
            webhook_url="https://example.com/webhook",
            payload={"test": "data"},
            headers={"Content-Type": "application/json"},
            max_retries=3,
            timeout=10,
        )

        with patch("requests.post") as mock_post:
            mock_response = Mock()
            mock_response.status_code = 500
            mock_response.text = "Internal Server Error"
            mock_post.return_value = mock_response

            success, result = deliver_webhook_with_retry(delivery)

            assert success is False
            assert result["status"] == "failed"
            assert result["attempts"] == 3  # All 3 attempts used
            assert result["response_code"] == 500
            assert "Internal Server Error" in result["error"]
            assert mock_post.call_count == 3

            # Should have exponential backoff: 1s + 2s (no sleep after last attempt)
            assert mock_sleep.call_count == 2
            mock_sleep.assert_has_calls([call(1), call(2)])

    def test_no_retry_on_400_error(self, mock_sleep):
        """Test that 4xx client errors do NOT trigger retry."""
        delivery = WebhookDelivery(
            webhook_url="https://example.com/webhook",
            payload={"test": "data"},
            headers={"Content-Type": "application/json"},
            max_retries=3,
            timeout=10,
        )

        with patch("requests.post") as mock_post:
            mock_response = Mock()
            mock_response.status_code = 400
            mock_response.text = "Bad Request"
            mock_post.return_value = mock_response

            success, result = deliver_webhook_with_retry(delivery)

            assert success is False
            assert result["status"] == "failed"
            assert result["attempts"] == 1  # No retries
            assert result["response_code"] == 400
            assert "Client error" in result["error"]
            assert "Bad Request" in result["error"]
            assert mock_post.call_count == 1  # Only 1 attempt

    def test_no_retry_on_404_error(self, mock_sleep):
        """Test that 404 Not Found does NOT trigger retry."""
        delivery = WebhookDelivery(
            webhook_url="https://example.com/webhook",
            payload={"test": "data"},
            headers={"Content-Type": "application/json"},
            max_retries=3,
            timeout=10,
        )

        with patch("requests.post") as mock_post:
            mock_response = Mock()
            mock_response.status_code = 404
            mock_response.text = "Not Found"
            mock_post.return_value = mock_response

            success, result = deliver_webhook_with_retry(delivery)

            assert success is False
            assert result["attempts"] == 1  # No retries for client error
            assert mock_post.call_count == 1

    def test_retry_on_timeout(self, mock_sleep):
        """Test that timeout errors trigger retry."""
        delivery = WebhookDelivery(
            webhook_url="https://example.com/webhook",
            payload={"test": "data"},
            headers={"Content-Type": "application/json"},
            max_retries=3,
            timeout=10,
        )

        with patch("requests.post") as mock_post:
            mock_post.side_effect = requests.exceptions.Timeout("Request timed out")

            success, result = deliver_webhook_with_retry(delivery)

            assert success is False
            assert result["status"] == "failed"
            assert result["attempts"] == 3
            assert "timeout" in result["error"].lower()
            assert mock_post.call_count == 3

            # Should have exponential backoff: 1s + 2s (no sleep after last attempt)
            assert mock_sleep.call_count == 2
            mock_sleep.assert_has_calls([call(1), call(2)])

    def test_retry_on_connection_error(self, mock_sleep):
        """Test that connection errors trigger retry."""
        delivery = WebhookDelivery(
            webhook_url="https://example.com/webhook",
            payload={"test": "data"},
            headers={"Content-Type": "application/json"},
            max_retries=3,
            timeout=10,
        )

        with patch("requests.post") as mock_post:
            mock_post.side_effect = requests.exceptions.ConnectionError("Connection refused")

            success, result = deliver_webhook_with_retry(delivery)

            assert success is False
            assert result["attempts"] == 3
            assert "Connection" in result["error"]
            assert mock_post.call_count == 3

    def test_exponential_backoff_timing(self, mock_sleep):
        """Test that exponential backoff follows 2^n pattern (1s, 2s, 4s)."""
        delivery = WebhookDelivery(
            webhook_url="https://example.com/webhook",
            payload={"test": "data"},
            headers={"Content-Type": "application/json"},
            max_retries=3,
            timeout=10,
        )

        with patch("requests.post") as mock_post:
            mock_response = Mock()
            mock_response.status_code = 503
            mock_response.text = "Service Unavailable"  # Add text attribute
            mock_post.return_value = mock_response

            deliver_webhook_with_retry(delivery)

            # Backoff: 2^0=1s after attempt 1, 2^1=2s after attempt 2 (no sleep after last)
            assert mock_sleep.call_count == 2
            mock_sleep.assert_has_calls([call(1), call(2)])

    def test_max_retries_exceeded(self, mock_sleep):
        """Test behavior when all retries are exhausted."""
        delivery = WebhookDelivery(
            webhook_url="https://example.com/webhook",
            payload={"test": "data"},
            headers={"Content-Type": "application/json"},
            max_retries=2,  # Only 2 retries
            timeout=10,
        )

        with patch("requests.post") as mock_post:
            mock_response = Mock()
            mock_response.status_code = 502
            mock_response.text = "Bad Gateway"  # Add text attribute
            mock_post.return_value = mock_response

            success, result = deliver_webhook_with_retry(delivery)

            assert success is False
            assert result["attempts"] == 2
            assert mock_post.call_count == 2

    def test_successful_delivery_with_202_accepted(self, mock_sleep):
        """Test that 202 Accepted is treated as success."""
        delivery = WebhookDelivery(
            webhook_url="https://example.com/webhook",
            payload={"test": "data"},
            headers={"Content-Type": "application/json"},
        )

        with patch("requests.post") as mock_post:
            mock_response = Mock()
            mock_response.status_code = 202
            mock_post.return_value = mock_response

            success, result = deliver_webhook_with_retry(delivery)

            assert success is True
            assert result["response_code"] == 202

    def test_successful_delivery_with_204_no_content(self, mock_sleep):
        """Test that 204 No Content is treated as success."""
        delivery = WebhookDelivery(
            webhook_url="https://example.com/webhook",
            payload={"test": "data"},
            headers={"Content-Type": "application/json"},
        )

        with patch("requests.post") as mock_post:
            mock_response = Mock()
            mock_response.status_code = 204
            mock_post.return_value = mock_response

            success, result = deliver_webhook_with_retry(delivery)

            assert success is True
            assert result["response_code"] == 204

    def test_hmac_signature_added(self, mock_sleep):
        """Test that HMAC signature is added when signing_secret provided."""
        delivery = WebhookDelivery(
            webhook_url="https://example.com/webhook",
            payload={"test": "data"},
            headers={"Content-Type": "application/json"},
            signing_secret="test-secret-key",
        )

        with patch("requests.post") as mock_post:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_post.return_value = mock_response

            success, result = deliver_webhook_with_retry(delivery)

            # Check that signature headers were added
            call_args = mock_post.call_args
            headers = call_args.kwargs["headers"]

            assert "X-Webhook-Signature" in headers or "X-Hub-Signature-256" in headers
            assert success is True

    def test_invalid_webhook_url_validation(self, mock_sleep):
        """Test that invalid webhook URLs are rejected."""
        delivery = WebhookDelivery(
            webhook_url="javascript:alert('xss')",  # Invalid scheme
            payload={"test": "data"},
            headers={"Content-Type": "application/json"},
        )

        with patch("requests.post") as mock_post:
            success, result = deliver_webhook_with_retry(delivery)

            assert success is False
            assert "Invalid webhook URL" in result["error"]
            assert mock_post.call_count == 0  # Should not attempt to call

    def test_localhost_webhook_url_rejected(self, mock_sleep):
        """Test that localhost URLs are rejected for SSRF protection."""
        delivery = WebhookDelivery(
            webhook_url="http://localhost:8080/webhook",
            payload={"test": "data"},
            headers={"Content-Type": "application/json"},
        )

        with patch("requests.post") as mock_post:
            success, result = deliver_webhook_with_retry(delivery)

            assert success is False
            assert "Invalid webhook URL" in result["error"]
            assert mock_post.call_count == 0

    @patch("src.core.webhook_delivery._create_delivery_record")
    @patch("src.core.webhook_delivery._update_delivery_record")
    def test_database_tracking_on_success(self, mock_update, mock_create, mock_sleep):
        """Test that successful delivery is tracked in database."""
        delivery = WebhookDelivery(
            webhook_url="https://example.com/webhook",
            payload={"test": "data"},
            headers={"Content-Type": "application/json"},
            event_type="test.event",
            tenant_id="tenant_1",
            object_id="obj_123",
        )

        with patch("requests.post") as mock_post:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_post.return_value = mock_response

            success, result = deliver_webhook_with_retry(delivery)

            assert success is True

            # Should create initial record
            assert mock_create.call_count == 1
            create_args = mock_create.call_args.kwargs
            assert create_args["tenant_id"] == "tenant_1"
            assert create_args["event_type"] == "test.event"
            assert create_args["object_id"] == "obj_123"

            # Should update record with success
            assert mock_update.call_count == 1
            update_args = mock_update.call_args.kwargs
            assert update_args["status"] == "delivered"
            assert update_args["attempts"] == 1
            assert update_args["response_code"] == 200

    @patch("src.core.webhook_delivery._create_delivery_record")
    @patch("src.core.webhook_delivery._update_delivery_record")
    def test_database_tracking_on_failure(self, mock_update, mock_create, mock_sleep):
        """Test that failed delivery is tracked in database."""
        delivery = WebhookDelivery(
            webhook_url="https://example.com/webhook",
            payload={"test": "data"},
            headers={"Content-Type": "application/json"},
            event_type="test.event",
            tenant_id="tenant_1",
        )

        with patch("requests.post") as mock_post:
            mock_response = Mock()
            mock_response.status_code = 400
            mock_response.text = "Bad Request"
            mock_post.return_value = mock_response

            success, result = deliver_webhook_with_retry(delivery)

            assert success is False

            # Should update record with failure
            assert mock_update.call_count == 1
            update_args = mock_update.call_args.kwargs
            assert update_args["status"] == "failed"
            assert update_args["response_code"] == 400
            assert "Bad Request" in update_args["last_error"]

    def test_custom_timeout(self, mock_sleep):
        """Test that custom timeout value is respected."""
        delivery = WebhookDelivery(
            webhook_url="https://example.com/webhook",
            payload={"test": "data"},
            headers={"Content-Type": "application/json"},
            timeout=5,  # Custom 5 second timeout
        )

        with patch("requests.post") as mock_post:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_post.return_value = mock_response

            deliver_webhook_with_retry(delivery)

            # Check that timeout was passed to requests.post
            call_args = mock_post.call_args
            assert call_args.kwargs["timeout"] == 5

    def test_result_contains_duration(self, mock_sleep):
        """Test that result includes duration metric."""
        delivery = WebhookDelivery(
            webhook_url="https://example.com/webhook",
            payload={"test": "data"},
            headers={"Content-Type": "application/json"},
        )

        with patch("requests.post") as mock_post:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_post.return_value = mock_response

            success, result = deliver_webhook_with_retry(delivery)

            assert "duration" in result
            assert isinstance(result["duration"], float)
            assert result["duration"] >= 0
