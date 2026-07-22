"""SSRF gate for protocol push / reporting webhook URLs.

Pins that ProtocolWebhookService refuses unsafe URLs before any outbound POST,
mirrors application-level WebhookURLValidator usage in webhook_delivery, and
covers registration wiring: create_media_buy, sync_creatives, A2A message/send,
and A2A set_push_notification_config handler.

Wire-level VALIDATION_ERROR / recovery=correctable + suggestion for
create_media_buy and sync_creatives is graded by transport-blind BDD scenarios
(BR-UC-002-ext-webhook-ssrf, BR-UC-006-ext-webhook-ssrf). A2A-native push-config
endpoints translate the same registration gate to InvalidParamsError (not the
AdCP VALIDATION_ERROR envelope) — pinned below.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests
from a2a.types import (
    InvalidParamsError,
    Message,
    Part,
    Role,
    SendMessageConfiguration,
    SendMessageRequest,
    TaskPushNotificationConfig,
)
from adcp.types import ReportingWebhook

from src.a2a_server.adcp_a2a_server import AdCPRequestHandler, _reject_unsafe_a2a_webhook_url
from src.core.database.models import PushNotificationConfig
from src.core.exceptions import AdCPValidationError
from src.core.resolved_identity import ResolvedIdentity
from src.core.schemas import CreateMediaBuyRequest
from src.core.testing_hooks import AdCPTestContext
from src.core.tools.creatives._sync import _sync_creatives_impl
from src.core.tools.media_buy_create import _create_media_buy_impl
from src.core.webhook_validator import WEBHOOK_SSRF_SUGGESTION_DEV, reject_unsafe_webhook_registration_url
from src.services.protocol_webhook_service import ProtocolWebhookService
from tests.factories.principal import PrincipalFactory

_METADATA_URL = "http://169.254.169.254/latest/meta-data/"
_AUTH_CREDS = "x" * 32


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


def _reporting_webhook(url: str) -> ReportingWebhook:
    return ReportingWebhook.model_validate(
        {
            "url": url,
            "authentication": {"schemes": ["Bearer"], "credentials": _AUTH_CREDS},
            "reporting_frequency": "daily",
        }
    )


def _identity() -> ResolvedIdentity:
    return PrincipalFactory.make_identity(
        principal_id="principal_1",
        tenant_id="test_tenant",
        auth_token="test-token",
        protocol="mcp",
        tenant={"tenant_id": "test_tenant", "human_review_required": False, "auto_create_media_buys": True},
        testing_context=AdCPTestContext(dry_run=False, test_session_id="test-session"),
    )


def _minimal_create_request(**overrides):
    from datetime import UTC, datetime, timedelta

    start = datetime.now(UTC) + timedelta(days=1)
    end = start + timedelta(days=7)
    defaults = {
        "brand": {"domain": "testbrand.com"},
        "start_time": start.isoformat(),
        "end_time": end.isoformat(),
        "packages": [{"product_id": "prod_1", "budget": 5000.0, "pricing_option_id": "cpm_usd_fixed"}],
        "idempotency_key": "unit-ssrf-create-key-0001",
    }
    defaults.update(overrides)
    return CreateMediaBuyRequest(**defaults)


@pytest.mark.asyncio
async def test_send_notification_rejects_metadata_url_without_post() -> None:
    """Cloud metadata URL must fail closed before requests.Session.post."""
    service = ProtocolWebhookService()
    with patch.object(service._session, "post", autospec=True) as mock_post:
        sent = await service.send_notification(
            _config(_METADATA_URL),
            payload={"task_id": "t1", "status": "completed"},
            metadata={"task_type": "create_media_buy"},
        )
    assert sent is False
    mock_post.assert_not_called()


@pytest.mark.asyncio
async def test_send_notification_rejects_localhost_without_post(monkeypatch: pytest.MonkeyPatch) -> None:
    """Production send path must reject localhost (ADCP_TESTING off)."""
    monkeypatch.delenv("ADCP_TESTING", raising=False)
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
            "src.core.webhook_validator.WebhookURLValidator.validate_outbound_webhook_url",
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
        allow_redirects=False,
    )


@pytest.mark.asyncio
async def test_send_notification_does_not_follow_redirect_to_metadata() -> None:
    """302 to link-local metadata must not be followed (open-redirect SSRF)."""
    service = ProtocolWebhookService()
    redirect = MagicMock()
    redirect.status_code = 302
    redirect.headers = {"Location": _METADATA_URL}
    redirect.raise_for_status.side_effect = requests.HTTPError("302 redirect")

    with (
        patch(
            "src.core.webhook_validator.WebhookURLValidator.validate_outbound_webhook_url",
            return_value=(True, ""),
        ),
        patch.object(service._session, "post", return_value=redirect) as mock_post,
        patch(
            "src.services.protocol_webhook_service.extract_webhook_result_data",
            return_value=None,
        ),
        patch(
            "src.services.protocol_webhook_service.get_audit_logger",
            return_value=MagicMock(),
        ),
        patch("src.services.protocol_webhook_service.asyncio.sleep", return_value=None),
    ):
        sent = await service.send_notification(
            _config("https://buyer.example.com/hooks/adcp"),
            payload={"task_id": "t1", "status": "completed"},
            metadata={"task_type": "create_media_buy", "tenant_id": "t1"},
        )

    assert sent is False
    assert mock_post.call_count >= 1
    for call in mock_post.call_args_list:
        assert call.kwargs.get("allow_redirects") is False
        assert call.args[0] == "https://buyer.example.com/hooks/adcp"


def test_reject_unsafe_webhook_registration_url_raises_validation_error() -> None:
    with pytest.raises(AdCPValidationError) as exc_info:
        reject_unsafe_webhook_registration_url(
            "http://metadata.google.internal/computeMetadata/v1/",
            field="reporting_webhook.url",
        )
    assert exc_info.value.field == "reporting_webhook.url"
    assert "Invalid reporting_webhook.url" in exc_info.value.message
    assert exc_info.value.suggestion == WEBHOOK_SSRF_SUGGESTION_DEV
    assert exc_info.value.recovery == "correctable"


def test_reject_unsafe_webhook_registration_url_allows_public() -> None:
    # Registration skips DNS — fixture hostnames must not NXDOMAIN-fail.
    reject_unsafe_webhook_registration_url("https://buyer.example.com/hook", field="push_notification_config.url")


def test_reject_unsafe_webhook_registration_url_allows_unresolvable_public_hostname() -> None:
    """Registration gate must not require DNS (BDD fixture hosts)."""
    reject_unsafe_webhook_registration_url(
        "https://nonexistent-buyer-ssrf-fixture.invalid/hook",
        field="reporting_webhook.url",
    )


def test_push_notification_config_repo_upsert_rejects_ssrf_url() -> None:
    """Repository upsert is a second registration gate (A2A set_push_notification_config)."""
    from src.core.database.repositories.push_notification_config import PushNotificationConfigRepository

    repo = PushNotificationConfigRepository(MagicMock(), "t1")
    with pytest.raises(ValueError, match="Invalid webhook URL"):
        repo.upsert(
            config_id="pnc_bad",
            principal_id="p1",
            url=_METADATA_URL,
            authentication_type=None,
            authentication_token=None,
            validation_token=None,
        )


@pytest.mark.asyncio
async def test_create_media_buy_rejects_reporting_webhook_anyurl() -> None:
    """Registration gate must run for real ReportingWebhook.url (AnyUrl, not str)."""
    req = _minimal_create_request(reporting_webhook=_reporting_webhook(_METADATA_URL))
    with pytest.raises(AdCPValidationError) as exc_info:
        await _create_media_buy_impl(req, identity=_identity())
    assert exc_info.value.field == "reporting_webhook.url"
    assert "Invalid reporting_webhook.url" in exc_info.value.message


@pytest.mark.asyncio
async def test_create_media_buy_rejects_push_config_before_workflow() -> None:
    """PNC SSRF must run before workflow metadata write (wiring + ordering)."""
    req = _minimal_create_request()
    mock_ctx = MagicMock()
    with (
        patch("src.core.tools.media_buy_create.get_context_manager", return_value=mock_ctx),
        pytest.raises(AdCPValidationError) as exc_info,
    ):
        await _create_media_buy_impl(
            req,
            push_notification_config={"url": _METADATA_URL},
            identity=_identity(),
        )
    assert exc_info.value.field == "push_notification_config.url"
    mock_ctx.create_workflow_step.assert_not_called()
    mock_ctx.create_context.assert_not_called()


def test_sync_creatives_rejects_unsafe_push_config_url() -> None:
    """sync_creatives must reject metadata URL at registration before DB work."""
    with pytest.raises(AdCPValidationError) as exc_info:
        _sync_creatives_impl(
            creatives=[],
            push_notification_config={"url": _METADATA_URL},
            identity=_identity(),
        )
    assert exc_info.value.field == "push_notification_config.url"


def test_reject_unsafe_a2a_webhook_url_rejects_metadata() -> None:
    """A2A registration helper maps SSRF to InvalidParamsError (not VALIDATION_ERROR)."""
    with pytest.raises(InvalidParamsError, match="Invalid webhook URL"):
        _reject_unsafe_a2a_webhook_url(_METADATA_URL)


@pytest.mark.asyncio
async def test_a2a_message_send_rejects_unsafe_push_config_url() -> None:
    """message/send must reject metadata URL before stash."""
    handler = AdCPRequestHandler()
    text_part = Part()
    text_part.text = "list products"
    message = Message(message_id="m-ssrf", role=Role.ROLE_USER, parts=[text_part])
    push = TaskPushNotificationConfig(url=_METADATA_URL)
    params = SendMessageRequest(
        message=message,
        configuration=SendMessageConfiguration(task_push_notification_config=push),
    )

    with pytest.raises(InvalidParamsError, match="Invalid webhook URL"):
        await handler.on_message_send(params, context=MagicMock())

    assert handler._task_push_configs == {}


@pytest.mark.asyncio
async def test_a2a_set_push_handler_rejects_metadata_url() -> None:
    """Handler on_create_task_push_notification_config must reject before upsert."""
    handler = AdCPRequestHandler()
    identity = _identity()
    tool_context = MagicMock()
    tool_context.tenant_id = identity.tenant_id
    tool_context.principal_id = identity.principal_id
    params = TaskPushNotificationConfig(url=_METADATA_URL, task_id="task-1", id="pnc-1")

    with (
        patch.object(handler, "_get_auth_token", return_value="tok"),
        patch.object(handler, "_resolve_a2a_identity", return_value=identity),
        patch.object(handler, "_make_tool_context", return_value=tool_context),
        patch("src.a2a_server.adcp_a2a_server.PushNotificationConfigUoW") as mock_uow,
        pytest.raises(InvalidParamsError, match="Invalid webhook URL"),
    ):
        await handler.on_create_task_push_notification_config(params, context=MagicMock())

    mock_uow.assert_not_called()
