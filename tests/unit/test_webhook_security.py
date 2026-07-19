"""Unit tests for webhook security features (SSRF protection and HMAC authentication)."""

import hashlib
import hmac
import json
import time

import pytest
from adcp.types import TaskType

from src.core.webhook_authenticator import WebhookAuthenticator
from src.core.webhook_validator import (
    WEBHOOK_TASK_TYPE_FALLBACK,
    WebhookURLValidator,
    validate_webhook_task_type,
)
from tests.helpers.protocol_webhook import assert_protocol_webhook_post


class TestValidateWebhookTaskType:
    """Coercion of untrusted action labels to SDK-accepted TaskType values."""

    @pytest.mark.parametrize("valid", [m.value for m in TaskType])
    def test_valid_tasktype_returned_unchanged(self, valid):
        """Every TaskType enum member passes through verbatim."""
        assert validate_webhook_task_type(valid) == valid

    @pytest.mark.parametrize(
        "invalid",
        # media_buy_delivery is now a valid TaskType member (adcp 6.6 / spec 3.1.1), so it no
        # longer coerces to the fallback — dropped from the invalid-label set.
        ["delivery_report", "unknown", "", "not_a_task"],
    )
    def test_non_tasktype_coerced_to_fallback(self, invalid):
        """Non-members are coerced to the default fallback."""
        assert validate_webhook_task_type(invalid) == WEBHOOK_TASK_TYPE_FALLBACK
        assert WEBHOOK_TASK_TYPE_FALLBACK == "update_media_buy"

    def test_custom_fallback_honored(self):
        """The fallback is overridable for callers with a different default."""
        assert validate_webhook_task_type("bogus", fallback="sync_creatives") == "sync_creatives"

    def test_fallback_must_be_valid_caller_choice(self):
        """A valid label ignores the fallback entirely."""
        assert validate_webhook_task_type("sync_creatives", fallback="update_media_buy") == "sync_creatives"


class TestWebhookURLValidator:
    """Test SSRF protection in webhook URL validation."""

    def test_valid_public_https_url(self):
        """Valid public HTTPS URLs should pass."""
        from unittest.mock import patch

        with patch("src.core.security.url_validator._resolve_ips", return_value=["93.184.216.34"]):
            is_valid, error = WebhookURLValidator.validate_webhook_url("https://example.com/webhook")
        assert is_valid
        assert error == ""

    def test_valid_public_http_url(self):
        """Valid public HTTP URLs should pass (for testing)."""
        from unittest.mock import patch

        with patch("src.core.security.url_validator._resolve_ips", return_value=["93.184.216.34"]):
            is_valid, error = WebhookURLValidator.validate_webhook_url("http://example.com/webhook")
        assert is_valid
        assert error == ""

    def test_blocks_localhost(self):
        """Should block localhost."""
        is_valid, error = WebhookURLValidator.validate_webhook_url("http://localhost:3000/webhook")
        assert not is_valid
        assert "blocked" in error.lower()

    def test_blocks_127_0_0_1(self):
        """Should block 127.0.0.1."""
        is_valid, error = WebhookURLValidator.validate_webhook_url("http://127.0.0.1:8080/webhook")
        assert not is_valid
        assert "loopback" in error.lower() or "private" in error.lower() or "internal" in error.lower()

    def test_blocks_private_network_10(self):
        """Should block 10.0.0.0/8 private network."""
        is_valid, error = WebhookURLValidator.validate_webhook_url("http://10.0.0.5/webhook")
        assert not is_valid
        assert "private" in error.lower() or "internal" in error.lower()

    def test_blocks_private_network_192(self):
        """Should block 192.168.0.0/16 private network."""
        is_valid, error = WebhookURLValidator.validate_webhook_url("http://192.168.1.1/webhook")
        assert not is_valid
        assert "private" in error.lower() or "internal" in error.lower()

    def test_blocks_private_network_172(self):
        """Should block 172.16.0.0/12 private network."""
        is_valid, error = WebhookURLValidator.validate_webhook_url("http://172.16.0.1/webhook")
        assert not is_valid
        assert "private" in error.lower() or "internal" in error.lower()

    def test_blocks_link_local(self):
        """Should block 169.254.0.0/16 link-local (AWS metadata service)."""
        is_valid, error = WebhookURLValidator.validate_webhook_url("http://169.254.169.254/latest/meta-data")
        assert not is_valid
        assert "link" in error.lower() or "private" in error.lower() or "blocked" in error.lower()

    def test_blocks_metadata_hostname(self):
        """Should block cloud metadata hostnames."""
        is_valid, error = WebhookURLValidator.validate_webhook_url("http://metadata.google.internal/webhook")
        assert not is_valid
        assert "blocked" in error.lower()

    def test_requires_http_or_https(self):
        """Should reject non-HTTP protocols."""
        is_valid, error = WebhookURLValidator.validate_webhook_url("ftp://example.com/webhook")
        assert not is_valid
        assert "http" in error.lower()

    def test_requires_hostname(self):
        """Should reject URLs without hostname."""
        is_valid, error = WebhookURLValidator.validate_webhook_url("http:///webhook")
        assert not is_valid
        assert "hostname" in error.lower()

    def test_callback_rejects_embedded_url_credentials_before_dns(self):
        """Callback auth must come from its config, never URL userinfo."""
        from unittest.mock import patch

        with patch("src.core.security.url_validator._resolve_ips") as resolve:
            is_valid, error = WebhookURLValidator.validate_callback_url(
                "https://url-user:url-password@buyer.example/webhook"
            )

        assert is_valid is False
        assert error == "URL failed SSRF validation"
        resolve.assert_not_called()

    def test_invalid_url_format(self):
        """Should reject malformed URLs."""
        is_valid, error = WebhookURLValidator.validate_webhook_url("not-a-url")
        assert not is_valid
        assert error != ""

    def test_validate_for_testing_allows_localhost(self):
        """Testing mode should allow localhost when enabled."""
        is_valid, error = WebhookURLValidator.validate_for_testing(
            "http://localhost:3001/webhook", allow_localhost=True
        )
        assert is_valid
        assert error == ""

    def test_validate_for_testing_blocks_private_networks(self):
        """Testing mode should still block private networks even with allow_localhost."""
        is_valid, error = WebhookURLValidator.validate_for_testing("http://192.168.1.1/webhook", allow_localhost=True)
        assert not is_valid


class TestWebhookAuthenticator:
    """Test HMAC-SHA256 webhook authentication."""

    def test_sign_payload(self):
        """Should generate signature with timestamp."""
        payload = {"event": "test", "data": "value"}
        secret = "test_secret_key"

        headers = WebhookAuthenticator.sign_payload(payload, secret)

        assert "X-Webhook-Signature" in headers
        assert "X-Webhook-Timestamp" in headers
        assert headers["X-Webhook-Signature"].startswith("sha256=")
        assert headers["X-Webhook-Timestamp"].isdigit()

    def test_sign_payload_deterministic(self):
        """Same payload and secret should generate different signatures (due to timestamp)."""
        payload = {"event": "test"}
        secret = "secret"

        headers1 = WebhookAuthenticator.sign_payload(payload, secret)
        time.sleep(1.1)  # Delay to ensure different timestamp (at least 1 second)
        headers2 = WebhookAuthenticator.sign_payload(payload, secret)

        # Timestamps should be different
        assert headers1["X-Webhook-Timestamp"] != headers2["X-Webhook-Timestamp"]
        # Signatures should be different (timestamp is part of signed message)
        assert headers1["X-Webhook-Signature"] != headers2["X-Webhook-Signature"]

    def test_sign_payload_with_different_secrets(self):
        """Different secrets should produce different signatures."""
        payload = {"event": "test"}

        headers1 = WebhookAuthenticator.sign_payload(payload, "secret1")
        headers2 = WebhookAuthenticator.sign_payload(payload, "secret2")

        assert headers1["X-Webhook-Signature"] != headers2["X-Webhook-Signature"]

    def test_verify_signature_valid(self):
        """Should verify valid signature."""
        payload = {"event": "test", "data": "value"}
        secret = "test_secret"

        # Create signature
        payload_str = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        timestamp = str(int(time.time()))
        signed_payload = f"{timestamp}.{payload_str}"
        signature = (
            "sha256=" + hmac.new(secret.encode("utf-8"), signed_payload.encode("utf-8"), hashlib.sha256).hexdigest()
        )

        # Verify
        is_valid = WebhookAuthenticator.verify_signature(payload_str, signature, timestamp, secret)
        assert is_valid

    def test_verify_signature_invalid_secret(self):
        """Should reject signature with wrong secret."""
        payload = {"event": "test"}
        payload_str = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        timestamp = str(int(time.time()))

        # Sign with one secret
        signed_payload = f"{timestamp}.{payload_str}"
        signature = "sha256=" + hmac.new(b"secret1", signed_payload.encode("utf-8"), hashlib.sha256).hexdigest()

        # Verify with different secret
        is_valid = WebhookAuthenticator.verify_signature(payload_str, signature, timestamp, "secret2")
        assert not is_valid

    def test_verify_signature_replay_protection(self):
        """Should reject old timestamps (replay attack prevention)."""
        payload = {"event": "test"}
        payload_str = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        secret = "test_secret"

        # Create signature with old timestamp (10 minutes ago)
        old_timestamp = str(int(time.time()) - 600)
        signed_payload = f"{old_timestamp}.{payload_str}"
        signature = (
            "sha256=" + hmac.new(secret.encode("utf-8"), signed_payload.encode("utf-8"), hashlib.sha256).hexdigest()
        )

        # Should reject (default tolerance is 300 seconds / 5 minutes)
        is_valid = WebhookAuthenticator.verify_signature(payload_str, signature, old_timestamp, secret)
        assert not is_valid

    def test_verify_signature_custom_tolerance(self):
        """Should accept old timestamps if tolerance allows."""
        payload = {"event": "test"}
        payload_str = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        secret = "test_secret"

        # Create signature with timestamp 10 minutes ago
        old_timestamp = str(int(time.time()) - 600)
        signed_payload = f"{old_timestamp}.{payload_str}"
        signature = (
            "sha256=" + hmac.new(secret.encode("utf-8"), signed_payload.encode("utf-8"), hashlib.sha256).hexdigest()
        )

        # Should accept with large tolerance
        is_valid = WebhookAuthenticator.verify_signature(
            payload_str, signature, old_timestamp, secret, tolerance_seconds=3600
        )
        assert is_valid

    def test_roundtrip_sign_and_verify(self):
        """Should successfully sign and verify."""
        payload = {"event": "creative_approved", "creative_id": "cr_123", "status": "active"}
        secret = "super_secret_key_12345"

        # Sign
        headers = WebhookAuthenticator.sign_payload(payload, secret)
        payload_str = json.dumps(payload, separators=(",", ":"), sort_keys=True)

        # Verify
        is_valid = WebhookAuthenticator.verify_signature(
            payload_str, headers["X-Webhook-Signature"], headers["X-Webhook-Timestamp"], secret
        )
        assert is_valid

    def test_signature_without_sha256_prefix(self):
        """Should handle signatures without sha256= prefix."""
        payload = {"event": "test"}
        payload_str = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        secret = "test_secret"
        timestamp = str(int(time.time()))

        # Create signature without prefix
        signed_payload = f"{timestamp}.{payload_str}"
        signature = hmac.new(secret.encode("utf-8"), signed_payload.encode("utf-8"), hashlib.sha256).hexdigest()

        # Should still verify
        is_valid = WebhookAuthenticator.verify_signature(payload_str, signature, timestamp, secret)
        assert is_valid

    def test_tampered_payload(self):
        """Should reject tampered payload."""
        payload = {"event": "test", "amount": 100}
        secret = "test_secret"

        # Sign original payload
        headers = WebhookAuthenticator.sign_payload(payload, secret)

        # Tamper with payload
        tampered_payload = {"event": "test", "amount": 999999}
        tampered_str = json.dumps(tampered_payload, separators=(",", ":"), sort_keys=True)

        # Should reject
        is_valid = WebhookAuthenticator.verify_signature(
            tampered_str, headers["X-Webhook-Signature"], headers["X-Webhook-Timestamp"], secret
        )
        assert not is_valid


class TestPinnedOutboundClient:
    """The outbound webhook POST is connection-pinned, redirect-disabled, 2xx-only (#1512 SSRF).

    Pinning lives in ``_PinningHTTPAdapter``, mounted on the service's long-lived pooled
    session. Tests exercise the real production seam (``get_connection_with_tls_context``),
    mocking only DNS resolution and pool creation.
    """

    def test_pinning_adapter_pins_socket_to_validated_ip_keeping_hostname_sni(self):
        """The socket connects to the validated IP while SNI + cert stay bound to the hostname."""
        from unittest.mock import MagicMock, patch

        import requests

        from src.core.security import webhook_http

        adapter = webhook_http.PinningHTTPAdapter()
        request = requests.Request("POST", "https://buyer.example.com:8443/webhook").prepare()

        captured: dict = {}

        def _fake_connection_from_host(**kwargs):
            captured.update(kwargs)
            return MagicMock()

        with (
            patch.object(webhook_http, "resolve_and_validate_target", return_value=("93.184.216.34", "")),
            patch.object(adapter.poolmanager, "connection_from_host", _fake_connection_from_host),
        ):
            adapter.get_connection_with_tls_context(request, verify=True)

        # Socket connects to the validated IP, not a hostname re-resolved at connect time...
        assert captured["host"] == "93.184.216.34"
        assert captured["port"] == 8443
        # ...while SNI and certificate verification stay bound to the ORIGINAL hostname.
        assert captured["pool_kwargs"]["server_hostname"] == "buyer.example.com"
        assert captured["pool_kwargs"]["assert_hostname"] == "buyer.example.com"

    def test_pinning_adapter_rejects_ssrf_url(self):
        """An SSRF-invalid target raises before any connection is created."""
        from unittest.mock import patch

        import requests

        from src.core.security import webhook_http

        adapter = webhook_http.PinningHTTPAdapter()
        request = requests.Request("POST", "https://evil.example.com/webhook").prepare()

        with patch.object(webhook_http, "resolve_and_validate_target", return_value=(None, "blocked internal target")):
            with pytest.raises(requests.RequestException, match="SSRF"):
                adapter.get_connection_with_tls_context(request, verify=True)

    def test_pinning_adapter_rejects_embedded_url_credentials_before_dns(self):
        """A stale persisted URL cannot replace configured Bearer auth with URL Basic auth."""
        from unittest.mock import patch

        import requests

        from src.core.security import webhook_http

        adapter = webhook_http.PinningHTTPAdapter()
        request = requests.Request(
            "POST",
            "https://url-user:url-password@buyer.example.com/webhook",
            headers={"Authorization": "Bearer configured-token"},
        ).prepare()

        # requests has already replaced the configured Bearer header with URL Basic
        # auth while preparing this request. The adapter must fail closed before DNS.
        assert request.headers["Authorization"].startswith("Basic ")
        with patch.object(webhook_http, "resolve_and_validate_target") as resolve:
            with pytest.raises(webhook_http.UnsafeWebhookTargetError, match="embedded credentials"):
                adapter.get_connection_with_tls_context(request, verify=True)
        resolve.assert_not_called()

    def test_session_ignores_environment_proxies_and_netrc(self):
        """trust_env=False: no HTTP(S)_PROXY / NO_PROXY / ~/.netrc injection on egress."""
        from src.services.protocol_webhook_service import ProtocolWebhookService

        service = ProtocolWebhookService()
        assert service._session.trust_env is False

    def test_pinning_adapter_refuses_proxied_target(self):
        """A configured proxy would defeat host-pinning — refuse to deliver, do not unpin."""
        from unittest.mock import patch

        import requests

        from src.core.security import webhook_http

        adapter = webhook_http.PinningHTTPAdapter()
        request = requests.Request("POST", "https://buyer.example.com/webhook").prepare()

        with (
            patch.object(webhook_http, "resolve_and_validate_target", return_value=("93.184.216.34", "")),
            patch.object(webhook_http, "select_proxy", return_value="http://proxy.internal:3128"),
        ):
            with pytest.raises(requests.RequestException, match="proxy"):
                adapter.get_connection_with_tls_context(request, verify=True)

    def test_post_streams_and_closes_response_body(self):
        """Only the status code is consumed: the POST streams and the body is closed."""
        import asyncio
        from unittest.mock import MagicMock, patch

        from src.core.database.models import PushNotificationConfig
        from src.services.protocol_webhook_service import ProtocolWebhookService

        config = PushNotificationConfig(
            id="pnc-stream",
            tenant_id="t",
            principal_id="p",
            url="https://buyer.example.com/webhook",
            authentication_type=None,
            authentication_token=None,
        )
        ok_resp = MagicMock()
        ok_resp.status_code = 200
        captured: dict = {}

        def _fake_post(self, url, **kwargs):  # noqa: ANN001 - test stub
            captured["kwargs"] = kwargs
            return ok_resp

        with patch("requests.sessions.Session.post", _fake_post):
            asyncio.run(
                ProtocolWebhookService().send_notification(
                    config, {"status": "completed"}, metadata={"task_type": "create_media_buy"}
                )
            )

        assert captured["kwargs"]["stream"] is True
        ok_resp.close.assert_called_once_with()

    def test_post_disables_redirects_and_preserves_host_header(self):
        """Delivery POSTs disable redirects and set Host to the original netloc (vhost routing)."""
        import asyncio
        from unittest.mock import MagicMock, patch

        from src.core.database.models import PushNotificationConfig
        from src.services.protocol_webhook_service import ProtocolWebhookService

        config = PushNotificationConfig(
            id="pnc-ok",
            tenant_id="t",
            principal_id="p",
            url="https://buyer.example.com/webhook",
            authentication_type=None,
            authentication_token=None,
        )
        ok_resp = MagicMock()
        ok_resp.status_code = 200
        captured: dict = {}

        def _fake_post(self, url, **kwargs):  # noqa: ANN001 - test stub
            captured["url"] = url
            captured["kwargs"] = kwargs
            return ok_resp

        with patch("requests.sessions.Session.post", _fake_post):
            asyncio.run(
                ProtocolWebhookService().send_notification(
                    config, {"status": "completed"}, metadata={"task_type": "create_media_buy"}
                )
            )

        assert captured["url"] == "https://buyer.example.com/webhook"
        assert captured["kwargs"]["allow_redirects"] is False
        assert captured["kwargs"]["headers"]["Host"] == "buyer.example.com"

    def test_send_notification_treats_3xx_as_failed_delivery(self):
        """A refused redirect is permanent and must be attempted exactly once."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock, patch

        from src.core.database.models import PushNotificationConfig
        from src.services.protocol_webhook_service import ProtocolWebhookService

        config = PushNotificationConfig(
            id="pnc-3xx",
            tenant_id="t",
            principal_id="p",
            url="https://buyer.example.com/webhook",
            authentication_type=None,
            authentication_token=None,
        )
        redirect_resp = MagicMock()
        redirect_resp.status_code = 302

        with (
            patch("requests.sessions.Session.post", return_value=redirect_resp) as mock_post,
            patch("src.services.protocol_webhook_service.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
        ):
            delivered = asyncio.run(
                ProtocolWebhookService().send_notification(
                    config, {"status": "completed"}, metadata={"task_type": "create_media_buy"}
                )
            )

        assert delivered is False, "a 3xx response must be treated as a failed delivery"
        assert_protocol_webhook_post(
            mock_post,
            url="https://buyer.example.com/webhook",
            body=b'{"status":"completed"}',
            host="buyer.example.com",
        )
        mock_sleep.assert_not_awaited()

    def test_send_notification_does_not_retry_unsafe_target(self):
        """An SSRF/pinning policy refusal is permanent, not a network retry."""
        import asyncio
        from unittest.mock import ANY, AsyncMock, patch

        from src.core.database.models import PushNotificationConfig
        from src.core.security.webhook_http import UnsafeWebhookTargetError
        from src.services.protocol_webhook_service import ProtocolWebhookService

        config = PushNotificationConfig(
            id="pnc-unsafe",
            tenant_id="t",
            principal_id="p",
            url="https://buyer.example.com/webhook",
            authentication_type=None,
            authentication_token=None,
        )

        with (
            patch(
                "src.services.protocol_webhook_service.post_webhook_status_async",
                new_callable=AsyncMock,
                side_effect=UnsafeWebhookTargetError("unsafe target"),
            ) as mock_post,
            patch("src.services.protocol_webhook_service.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
        ):
            delivered = asyncio.run(
                ProtocolWebhookService().send_notification(
                    config, {"status": "completed"}, metadata={"task_type": "create_media_buy"}
                )
            )

        assert delivered is False
        mock_post.assert_awaited_once_with(
            ANY,
            "https://buyer.example.com/webhook",
            body=b'{"status":"completed"}',
            headers={
                "Content-Type": "application/json",
                "User-Agent": "AdCP-Sales-Agent/1.0",
            },
            timeout=10.0,
        )
        mock_sleep.assert_not_awaited()
