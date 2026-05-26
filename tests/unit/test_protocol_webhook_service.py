import json
import time

import pytest
from adcp import create_a2a_webhook_payload
from adcp.webhooks import GeneratedTaskStatus, LegacyWebhookHmacOptions, verify_webhook_hmac

from src.core.database.models import PushNotificationConfig
from src.services import protocol_webhook_service
from src.services.protocol_webhook_service import ProtocolWebhookService


class _Response:
    status_code = 200

    def raise_for_status(self) -> None:
        return None


class _Session:
    def __init__(self) -> None:
        self.calls = []

    def post(self, url, *, data, headers, timeout):
        self.calls.append({"url": url, "data": data, "headers": headers, "timeout": timeout})
        return _Response()


def test_redact_webhook_url_removes_secret_bearing_parts() -> None:
    assert (
        protocol_webhook_service._redact_webhook_url("https://user:pass@example.com:8443/hook?sig=secret#frag")
        == "https://example.com:8443/[redacted]"
    )


@pytest.mark.asyncio
async def test_protocol_webhook_service_signs_rfc9421_payload_with_exact_body(monkeypatch) -> None:
    loaded_credential = object()
    captured = {}

    def fake_load_active_signing_credential(*, tenant_id, signing_mode):
        captured["load"] = {"tenant_id": tenant_id, "signing_mode": signing_mode}
        return loaded_credential

    def fake_build_auth_headers(**kwargs):
        captured["sign"] = kwargs
        headers = dict(kwargs["base_headers"])
        headers["Signature"] = "sig1=:signed:"
        headers["Signature-Input"] = 'sig1=("@method" "@target-uri" "content-digest" "content-type")'
        headers["Content-Digest"] = "sha-256=:digest:"
        return headers

    monkeypatch.setattr(protocol_webhook_service, "load_active_signing_credential", fake_load_active_signing_credential)
    monkeypatch.setattr(protocol_webhook_service, "build_auth_headers", fake_build_auth_headers)

    service = ProtocolWebhookService()
    session = _Session()
    service._session = session
    payload = {"notification_type": "signal.updated", "task_id": "catalog_1"}
    config = PushNotificationConfig(
        id="pnc_1",
        tenant_id="tenant_1",
        principal_id="agent_1",
        url="https://example.com/webhooks",
        signing_mode="rfc9421",
        is_active=True,
    )

    ok = await service.send_notification(config, payload, {"task_type": "signal.updated", "tenant_id": "tenant_1"})

    assert ok is True
    assert captured["load"] == {"tenant_id": "tenant_1", "signing_mode": "rfc9421"}
    assert captured["sign"]["body"] == session.calls[0]["data"]
    assert json.loads(session.calls[0]["data"]) == payload
    assert session.calls[0]["headers"]["Signature"] == "sig1=:signed:"
    assert session.calls[0]["headers"]["Content-Type"] == "application/json"


@pytest.mark.asyncio
async def test_protocol_webhook_service_legacy_hmac_body_verifies() -> None:
    service = ProtocolWebhookService()
    session = _Session()
    service._session = session
    secret = "x" * 32
    payload = {"notification_type": "signal.updated", "task_id": "catalog_1"}
    config = PushNotificationConfig(
        id="pnc_1",
        tenant_id="tenant_1",
        principal_id="agent_1",
        url="https://example.com/webhooks",
        authentication_type="HMAC-SHA256",
        authentication_token=secret,
        signing_mode="hmac",
        is_active=True,
    )

    ok = await service.send_notification(config, payload, {"task_type": "signal.updated", "tenant_id": "tenant_1"})

    assert ok is True
    call = session.calls[0]
    assert json.loads(call["data"]) == payload
    verify_webhook_hmac(
        headers=call["headers"],
        body=call["data"],
        options=LegacyWebhookHmacOptions(secret=secret.encode(), sender_identity="agent_1", now=time.time()),
    )


@pytest.mark.asyncio
async def test_protocol_webhook_service_adds_idempotency_key_to_a2a_payload() -> None:
    service = ProtocolWebhookService()
    session = _Session()
    service._session = session
    payload = create_a2a_webhook_payload(
        task_id="task_1",
        status=GeneratedTaskStatus.completed,
        context_id="ctx_1",
        result={"media_buy_id": "mb_1"},
    )
    config = PushNotificationConfig(
        id="pnc_1",
        tenant_id="tenant_1",
        principal_id="agent_1",
        url="https://example.com/webhooks",
        signing_mode="hmac",
        is_active=True,
    )

    ok = await service.send_notification(config, payload, {"task_type": "create_media_buy", "tenant_id": "tenant_1"})

    assert ok is True
    body = json.loads(session.calls[0]["data"])
    assert body["idempotency_key"]
    assert body["id"] == "task_1"
    assert body["contextId"] == "ctx_1"


@pytest.mark.asyncio
async def test_protocol_webhook_service_refuses_public_http_url(monkeypatch) -> None:
    monkeypatch.delenv("ADCP_AUTH_TEST_MODE", raising=False)
    monkeypatch.delenv("WEBHOOK_ALLOW_PRIVATE_IPS", raising=False)

    service = ProtocolWebhookService()
    session = _Session()
    service._session = session
    config = PushNotificationConfig(
        id="pnc_1",
        tenant_id="tenant_1",
        principal_id="agent_1",
        url="http://example.com/webhooks",
        signing_mode="hmac",
        is_active=True,
    )

    ok = await service.send_notification(config, {"task_id": "catalog_1"}, {"task_type": "signal.updated"})

    assert ok is False
    assert session.calls == []


@pytest.mark.asyncio
async def test_protocol_webhook_service_refuses_metadata_url(monkeypatch) -> None:
    monkeypatch.delenv("ADCP_AUTH_TEST_MODE", raising=False)
    monkeypatch.delenv("WEBHOOK_ALLOW_PRIVATE_IPS", raising=False)

    service = ProtocolWebhookService()
    session = _Session()
    service._session = session
    config = PushNotificationConfig(
        id="pnc_1",
        tenant_id="tenant_1",
        principal_id="agent_1",
        url="https://169.254.169.254/latest/meta-data",
        signing_mode="hmac",
        is_active=True,
    )

    ok = await service.send_notification(config, {"task_id": "catalog_1"}, {"task_type": "signal.updated"})

    assert ok is False
    assert session.calls == []
