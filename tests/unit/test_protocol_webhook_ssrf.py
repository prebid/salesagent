"""SSRF gate for protocol push / reporting webhook URLs (#1695 / #1578).

Pins that ProtocolWebhookService refuses unsafe URLs before any outbound POST,
mirroring application-level WebhookURLValidator usage in webhook_delivery.py.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.core.database.models import PushNotificationConfig
from src.core.exceptions import AdCPValidationError
from src.core.tools.media_buy_create import _reject_unsafe_webhook_url
from src.services.protocol_webhook_service import ProtocolWebhookService


def _config(url: str) -> PushNotificationConfig:
    return PushNotificationConfig(
        id="pnc-ssrf-test",
        tenant_id="t1",
        principal_id="p1",
        url=url,
        authentication_type=None,
        authentication_token=None,
        is_active=True,
    )


@pytest.mark.asyncio
async def test_send_notification_rejects_metadata_url_without_post() -> None:
    """Cloud metadata URL must fail closed before requests.Session.post (#1695)."""
    service = ProtocolWebhookService()
    with patch.object(service._session, "post", autospec=True) as mock_post:
        sent = await service.send_notification(
            _config("http://169.254.169.254/latest/meta-data/"),
            payload={"task_id": "t1", "status": "completed"},
            metadata={"task_type": "create_media_buy"},
        )
    assert sent is False
    mock_post.assert_not_called()


@pytest.mark.asyncio
async def test_send_notification_rejects_localhost_without_post() -> None:
    service = ProtocolWebhookService()
    with patch.object(service._session, "post", autospec=True) as mock_post:
        sent = await service.send_notification(
            _config("http://localhost:9999/webhook"),
            payload={"task_id": "t1", "status": "completed"},
            metadata={"task_type": "create_media_buy"},
        )
    assert sent is False
    mock_post.assert_not_called()


@pytest.mark.asyncio
async def test_send_notification_posts_when_url_is_public() -> None:
    """Safe public URL proceeds to POST (validator + session both exercised)."""
    service = ProtocolWebhookService()
    response = MagicMock()
    response.status_code = 200
    response.raise_for_status = MagicMock()

    with (
        patch(
            "src.services.protocol_webhook_service.WebhookURLValidator.validate_webhook_url",
            return_value=(True, ""),
        ),
        patch.object(service._session, "post", return_value=response) as mock_post,
        patch(
            "src.services.protocol_webhook_service.extract_webhook_result_data",
            return_value=None,
        ),
        patch(
            "src.services.protocol_webhook_service.get_audit_logger",
            return_value=MagicMock(),
        ),
    ):
        sent = await service.send_notification(
            _config("https://buyer.example.com/hooks/adcp"),
            payload={"task_id": "t1", "status": "completed"},
            metadata={"task_type": "create_media_buy", "tenant_id": "t1"},
        )

    assert sent is True
    mock_post.assert_called_once_with(
        "https://buyer.example.com/hooks/adcp",
        json={"task_id": "t1", "status": "completed"},
        headers={"Content-Type": "application/json", "User-Agent": "AdCP-Sales-Agent/1.0"},
        timeout=10.0,
    )


def test_reject_unsafe_webhook_url_raises_validation_error() -> None:
    with pytest.raises(AdCPValidationError) as exc_info:
        _reject_unsafe_webhook_url(
            "http://metadata.google.internal/computeMetadata/v1/",
            field="reporting_webhook.url",
        )
    assert exc_info.value.field == "reporting_webhook.url"
    assert "Invalid reporting_webhook.url" in exc_info.value.message


def test_reject_unsafe_webhook_url_allows_public() -> None:
    # example.com resolves publicly in CI; if DNS flakes, pin the validator.
    with patch(
        "src.core.webhook_validator.WebhookURLValidator.validate_webhook_url",
        return_value=(True, ""),
    ):
        _reject_unsafe_webhook_url("https://buyer.example.com/hook", field="push_notification_config.url")


def test_push_notification_config_repo_upsert_rejects_ssrf_url() -> None:
    """Repository upsert is a second registration gate (A2A set_push_notification_config)."""
    from unittest.mock import MagicMock

    from src.core.database.repositories.push_notification_config import PushNotificationConfigRepository

    repo = PushNotificationConfigRepository(MagicMock(), "t1")
    with pytest.raises(ValueError, match="Invalid webhook URL"):
        repo.upsert(
            config_id="pnc_bad",
            principal_id="p1",
            url="http://169.254.169.254/latest/meta-data/",
            authentication_type=None,
            authentication_token=None,
            validation_token=None,
        )
