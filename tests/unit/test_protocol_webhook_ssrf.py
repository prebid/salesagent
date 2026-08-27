"""SSRF gate for protocol push / reporting webhook URLs.

Pins that ProtocolWebhookService refuses unsafe URLs before any outbound POST,
mirrors application-level WebhookURLValidator usage in webhook_delivery, and
covers registration wiring: create_media_buy, sync_creatives, A2A message/send,
and A2A set_push_notification_config handler.

Wire-level VALIDATION_ERROR / recovery=correctable + suggestion for
create_media_buy and sync_creatives is graded by transport-blind BDD scenarios
(BR-UC-002-ext-webhook-ssrf, BR-UC-006-ext-webhook-ssrf). A2A-native push-config
endpoints translate the same registration gate to InvalidParamsError with the
AdCP VALIDATION_ERROR envelope in ``data`` — pinned below.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from unittest.mock import MagicMock, patch

import pytest
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
from src.core.webhook_validator import (
    WEBHOOK_SSRF_SUGGESTION_DEV,
    WebhookURLValidator,
    reject_unsafe_webhook_registration_url,
)
from src.services.protocol_webhook_service import ProtocolWebhookService
from tests.factories.principal import PrincipalFactory
from tests.helpers import assert_envelope_shape
from tests.helpers.adcp_factories import create_test_media_buy_request_dict, valid_reporting_webhook
from tests.helpers.webhook_wire import capture_outbound_webhooks, constructed_http_clients

_METADATA_URL = "http://169.254.169.254/latest/meta-data/"
_PUBLIC_URL = "https://buyer.example.com/hooks/adcp"


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
    return ReportingWebhook.model_validate(valid_reporting_webhook(url))


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
    data = create_test_media_buy_request_dict(
        product_ids=["prod_1"],
        total_budget=5000.0,
        pricing_option_id="cpm_usd_fixed",
        idempotency_key="unit-ssrf-create-key-0001",
        **overrides,
    )
    return CreateMediaBuyRequest(**data)


@asynccontextmanager
async def _running_service() -> AsyncIterator[ProtocolWebhookService]:
    """The service, constructed INSIDE the caller's wire capture and closed after.

    Construction is what binds this service's long-lived ``httpx.AsyncClient`` —
    the one the RFC 9421 signing boundary borrows (#1291 C1) — to the stubbed
    socket. A service built outside the capture block would POST to the real
    network and record nothing, which is how a "nothing was sent" assertion goes
    silently vacuous.
    """
    service = ProtocolWebhookService()
    try:
        yield service
    finally:
        await service.close()


async def _send(service: ProtocolWebhookService, url: str) -> bool:
    return await service.send_notification(
        _config(url),
        payload={"task_id": "t1", "status": "completed"},
        metadata={"task_type": "create_media_buy"},
    )


@pytest.mark.asyncio
async def test_send_notification_rejects_metadata_url_without_post() -> None:
    """Cloud metadata URL must fail closed before any byte leaves the process.

    Graded on the WIRE rather than on a client mock: delivery moved to
    ``deliver_adcp_webhook`` (#1291 C1) and the SDK skips its OWN SSRF check on
    the operator-supplied-client path (``WebhookSender._send_bytes``), so
    ``reject_unsafe_outbound_webhook_url`` is the only gate standing between this
    URL and the socket. Deleting it produces a real recorded POST here.
    """
    with capture_outbound_webhooks() as captured:
        async with _running_service() as service:
            sent = await _send(service, _METADATA_URL)

    # Wire leg first: "no webhook was sent" is the obligation, and the return
    # value is only its report.
    assert captured == []
    assert sent is False


@pytest.mark.asyncio
async def test_send_notification_rejects_localhost_without_post(monkeypatch: pytest.MonkeyPatch) -> None:
    """Production send path must reject localhost (ADCP_TESTING off)."""
    monkeypatch.delenv("ADCP_TESTING", raising=False)

    with capture_outbound_webhooks() as captured:
        async with _running_service() as service:
            sent = await _send(service, "http://localhost:9999/webhook")

    assert captured == []
    assert sent is False


@pytest.mark.asyncio
async def test_send_notification_posts_when_url_is_public() -> None:
    """Safe public URL proceeds to a real POST — the gate is not a blanket refusal.

    The real ``validate_outbound_webhook_url`` runs (DNS answers a public address
    via the capture's resolver stub), so this grades the ACCEPT arm of the same
    gate the two tests above grade the reject arm of, and the bytes recorded are
    the ones the receiving socket would have seen.
    """
    with capture_outbound_webhooks() as captured, constructed_http_clients() as clients:
        async with _running_service() as service:
            sent = await _send(service, _PUBLIC_URL)

    assert sent is True
    assert len(captured) == 1
    assert captured[0].url == _PUBLIC_URL
    assert captured[0].headers["content-type"] == "application/json"
    assert captured[0].headers["user-agent"] == "AdCP-Sales-Agent/1.0"
    posted = captured[0].payload
    assert posted["task_id"] == "t1"
    assert posted["status"] == "completed"
    # Timeout moved from a per-call kwarg onto the client the service owns (#1291 C1).
    assert clients and all(client.timeout.connect == 10.0 for client in clients)


@pytest.mark.asyncio
async def test_send_notification_does_not_follow_redirect_to_metadata() -> None:
    """A 3xx must not be chased — a followed 302 to metadata bypasses the pre-POST gate.

    The property now lives on the CLIENT (``httpx.AsyncClient(follow_redirects=False)``
    in ``ProtocolWebhookService.__init__``), not on a per-call ``allow_redirects``
    kwarg, because delivery POSTs through ``deliver_adcp_webhook``. It is therefore
    asserted on every client the delivery path actually constructs — the shape
    ``constructed_http_clients`` exists for — and flipping it to ``True`` in
    production turns this red. The wire leg additionally pins that the redirect
    status never produced a POST anywhere but the configured URL.
    """
    with (
        capture_outbound_webhooks(status_codes=(302,)) as captured,
        constructed_http_clients() as clients,
        patch("src.services.protocol_webhook_service.asyncio.sleep", return_value=None),
    ):
        async with _running_service() as service:
            sent = await _send(service, _PUBLIC_URL)

    assert sent is False
    assert clients and all(client.follow_redirects is False for client in clients)
    assert captured
    assert {c.url for c in captured} == {_PUBLIC_URL}


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


@pytest.mark.parametrize("blank", [None, "", "   "])
def test_reject_unsafe_webhook_registration_url_noop_on_blank(blank: str | None) -> None:
    """Blank / missing URL is not a rejection — callers extract-then-call unconditionally."""
    reject_unsafe_webhook_registration_url(blank, field="push_notification_config.url")


def test_sanitize_webhook_url_for_log_strips_credentials_query_and_fragment() -> None:
    from src.core.webhook_validator import (
        UNPARSEABLE_WEBHOOK_URL_FOR_LOG,
        sanitize_webhook_url_for_log,
        webhook_url_for_log,
    )

    dirty = "https://user:pass@buyer.example.com:8443/hook?token=abc#frag"
    assert sanitize_webhook_url_for_log(dirty) == "https://buyer.example.com/hook"
    assert webhook_url_for_log(dirty) == "https://buyer.example.com/hook"
    assert sanitize_webhook_url_for_log(None) is None
    assert sanitize_webhook_url_for_log("not-a-url") is None
    assert webhook_url_for_log(None) == UNPARSEABLE_WEBHOOK_URL_FOR_LOG
    assert webhook_url_for_log("not-a-url") == UNPARSEABLE_WEBHOOK_URL_FOR_LOG


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
    """A2A registration helper maps SSRF to InvalidParamsError + AdCP envelope in data."""
    with pytest.raises(InvalidParamsError, match="Invalid push_notification_config.url") as exc_info:
        _reject_unsafe_a2a_webhook_url(_METADATA_URL)
    assert_envelope_shape(exc_info.value.data, "VALIDATION_ERROR", recovery="correctable")
    assert exc_info.value.data["errors"][0].get("suggestion")


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

    with pytest.raises(InvalidParamsError, match="Invalid push_notification_config.url") as exc_info:
        await handler.on_message_send(params, context=MagicMock())

    assert_envelope_shape(exc_info.value.data, "VALIDATION_ERROR", recovery="correctable")
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
        pytest.raises(InvalidParamsError, match="Invalid push_notification_config.url") as exc_info,
    ):
        await handler.on_create_task_push_notification_config(params, context=MagicMock())

    assert_envelope_shape(exc_info.value.data, "VALIDATION_ERROR", recovery="correctable")
    mock_uow.assert_not_called()


@pytest.mark.asyncio
async def test_the_url_the_gate_judged_is_the_url_that_is_dialled(monkeypatch: pytest.MonkeyPatch) -> None:
    """The destination must not be rewritten into something the gate refuses.

    ``send_notification`` used to gate ``push_notification_config.url`` and then
    rewrite ``localhost`` to ``host.docker.internal`` before dialling — a host that
    is itself in ``BLOCKED_HOSTNAMES``, so the URL the gate approved and the URL
    that reached the socket were different, and the second was one the gate exists
    to refuse. A gate whose verdict does not describe the dialled destination is
    advisory. The rewrite is gone; this pins that it stays gone.

    Graded on the DIALLED url captured at the wire, not on the return value:
    ``sent is True`` is equally true whether the destination was legitimate or
    rewritten into a blocked one, so it cannot tell the two apart.

    Runs under ADCP_TESTING because that is the only configuration in which a
    ``localhost`` registration passes the gate at all; in production it is refused
    outright. That is also why the original defect was a dev/e2e integrity problem
    rather than a reachable production SSRF hole — but it was load-bearing for
    signing, since the RFC 9421 ``@target-uri`` covers whatever URL is finally
    dialled.
    """
    monkeypatch.setenv("ADCP_TESTING", "true")
    configured = "http://localhost:9999/webhook"

    with capture_outbound_webhooks() as captured:
        async with _running_service() as service:
            await _send(service, configured)

    assert len(captured) == 1, "expected exactly one delivery to grade"
    dialled = captured[0].url

    assert dialled == configured, (
        f"the gate judged {configured!r} but the process dialled {dialled!r} — a destination the gate never saw"
    )

    # And the dialled URL must satisfy the SAME gate that admitted the configured
    # one. Deliberately not a bare check_url_ssrf: that is a DIFFERENT gate (no
    # ADCP_TESTING localhost allowance), and asserting against it would fail an
    # honest delivery while passing a rewrite into another allowed-but-unjudged
    # host. The invariant is about the gate that actually ran.
    is_safe, error = WebhookURLValidator.validate_outbound_webhook_url(dialled)
    assert is_safe, f"the dialled URL {dialled!r} does not pass the gate that admitted it: {error}"
