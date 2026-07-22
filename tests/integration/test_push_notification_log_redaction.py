"""#1617: the buyer's webhook credential must never reach the logs.

One test per push-notification log site — media_buy_create, the admin
creative-status webhook, and the protocol webhook service — each driving the real
code path with a credential-bearing config and asserting on what that site
actually logged. A single site test would leave the other two choke points
unguarded: a revert there would still be green.

Capture is taken by patching each module's ``logger`` object and reading its
``info`` call args, NOT via caplog or a handler: a full-suite run can leave
``logging.disable()`` set (or propagation off, or the root level raised) by an
earlier test, which suppresses records BEFORE any handler sees them and leaves a
capture-based assertion reading an empty string. A MagicMock logger records the
call regardless of that global logging state, while the path under test still
runs for real.

Why the mask presence assert (not just secret-absence) is the load-bearing one at
two of the three sites: those sites are handed the ``PushNotificationConfig`` DB
model, whose ``__repr__`` already prints ``authentication_token='***'``. Log the
raw model and no secret appears — only the ``REDACTED`` sentinel disappears. That
is precisely why the sentinel is kept distinct from ``'***'``; see
src.core.log_safety.REDACTED and
tests/unit/test_log_safety.py::test_sentinel_is_distinct_from_model_repr_mask.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.log_safety import REDACTED
from src.core.schemas import CreateMediaBuySuccess
from tests.integration.test_create_media_buy_behavioral import _env, _make_request

pytestmark = [pytest.mark.integration]

_SECRET = "buyer-webhook-bearer-SECRET-should-never-be-logged"


def _assert_log_redacted(mock_logger) -> None:
    """Assert a patched logger's info calls carry the redacted view and no secret.

    Renders every ``logger.info`` call (message template + args + kwargs). A
    MagicMock records the call even when ``logging.disable()``/propagation/level
    would suppress the record, so this observes the real log site rather than a
    caplog buffer.
    """
    logged = "\n".join(str(call.args) + str(call.kwargs) for call in mock_logger.info.call_args_list)
    # The credential itself never appears in any log call (asserted first: it is the
    # security failure, and it is what a raw-wire-dict log site trips) ...
    assert _SECRET not in logged, "buyer webhook credential leaked to the logs (#1617)"
    # ... and the site ran and routed through the redactor. See the module docstring:
    # at the two DB-model sites this is the assertion the deletion oracle reddens,
    # because the model's own repr masks the token.
    assert REDACTED in logged, "the push-notification log site did not run redacted — this test guards nothing"


@pytest.mark.requires_db
def test_create_media_buy_registration_log_redacts_webhook_credential(integration_db):
    """A non-dry-run create with a credential-bearing push_notification_config
    reaches the registration log; that log must carry the redacted view, never the
    credential. Deletion oracle: reverting the site to log the raw config leaks
    ``_SECRET`` here (this site receives the A2A wire dict, which has no masking
    repr, so both assertions bite).
    """
    from src.core.tools.media_buy_create import _create_media_buy_impl
    from src.core.transport_helpers import enrich_identity_with_account

    pnc = {
        "id": "pnc_redact",
        "url": "https://buyer.example/webhook",
        "authentication": {"schemes": ["Bearer"], "credentials": _SECRET},
    }
    req = _make_request()

    with patch("src.core.tools.media_buy_create.logger") as mock_logger:
        with _env() as env:
            tenant, _principal = env.setup_default_data()
            env.setup_product_chain(tenant)
            env._commit_factory_data()
            identity = enrich_identity_with_account(env.identity, req.account)
            result = asyncio.run(_create_media_buy_impl(req=req, identity=identity, push_notification_config=pnc))

    assert isinstance(result.response, CreateMediaBuySuccess)
    _assert_log_redacted(mock_logger)


@pytest.fixture
def reviewed_creative_with_webhook_step(integration_db):
    """A fully-reviewed creative whose sync_creatives workflow step carries a
    credential-bearing push_notification_config.

    That ``request_data`` is exactly what
    ``src/admin/blueprints/creatives.py::_call_webhook_for_creative_status``
    turns into the ``DBPushNotificationConfig`` it logs, so the credential
    reaches the log site for real. Built with the factories + ContextManager
    production APIs (same shape as
    tests/integration/test_admin_media_buy_reject_webhook.py::make_pending_media_buy),
    committed so the blueprint's own session sees the rows.
    """
    from sqlalchemy.orm import Session as SASession

    from src.core.context_manager import ContextManager
    from src.core.database.database_session import get_engine
    from tests.factories import ALL_FACTORIES, CreativeFactory, PrincipalFactory, TenantFactory

    session = SASession(bind=get_engine())
    for f in ALL_FACTORIES:
        f._meta.sqlalchemy_session = session
    try:
        tenant = TenantFactory(tenant_id="pnc_redact_tenant")
        principal = PrincipalFactory(tenant=tenant, principal_id="pnc_redact_principal")
        creative = CreativeFactory(
            tenant=tenant,
            principal=principal,
            creative_id="creative_pnc_redact",
            status="approved",
        )
        cm = ContextManager()
        context = cm.create_context(tenant_id=tenant.tenant_id, principal_id=principal.principal_id)
        cm.create_workflow_step(
            context_id=context.context_id,
            step_type="approval",
            owner="publisher",
            status="requires_approval",
            tool_name="sync_creatives",
            request_data={
                "protocol": "mcp",
                "push_notification_config": {
                    "id": "pnc_cr",
                    "url": "https://buyer.example/webhook",
                    "authentication": {"schemes": ["Bearer"], "credentials": _SECRET},
                },
            },
            object_mappings=[
                {"object_type": "creative", "object_id": creative.creative_id, "action": "approve"},
            ],
        )
        yield {"tenant_id": tenant.tenant_id, "creative_id": creative.creative_id}
    finally:
        for f in ALL_FACTORIES:
            f._meta.sqlalchemy_session = None
        session.close()


@pytest.mark.requires_db
def test_creative_status_webhook_log_redacts_webhook_credential(reviewed_creative_with_webhook_step):
    """The admin creative-status webhook logs the stored config it is about to
    send with; that log must be the redacted view.

    Deletion oracle: revert the site to ``logger.info(f"push_notification_config:
    {push_notification_config}")``. The model's ``__repr__`` masks the token with
    ``'***'``, so the secret-absence assert stays green and ONLY the
    ``REDACTED``-present assert reddens — that one is the oracle here.
    """
    from src.admin.blueprints.creatives import _call_webhook_for_creative_status

    mock_service = MagicMock()
    mock_service.send_notification = AsyncMock(return_value=True)

    with (
        patch("src.admin.blueprints.creatives.logger") as mock_logger,
        patch("src.admin.blueprints.creatives.get_protocol_webhook_service", return_value=mock_service),
    ):
        delivered = asyncio.run(
            _call_webhook_for_creative_status(
                creative_id=reviewed_creative_with_webhook_step["creative_id"],
                tenant_id=reviewed_creative_with_webhook_step["tenant_id"],
            )
        )

    # Guard against a vacuous pass: every early return in this function is a
    # `return False` that never reaches the log site.
    assert delivered is True, "webhook path did not run to completion — the log assertion would be vacuous"
    mock_service.send_notification.assert_awaited_once()
    _assert_log_redacted(mock_logger)


def test_send_notification_log_redacts_webhook_credential():
    """The webhook service logs the config it is delivering with; that log must be
    the redacted view.

    No DB: the config is a real (unsaved) ``PushNotificationConfig`` model — not a
    MagicMock, which would make the secret-absence assert vacuously true — and the
    send goes to a real loopback receiver rather than a patched private method, so
    the production ``send_notification`` body runs end to end. ``metadata`` carries
    only ``task_type``, which keeps the delivery-log/audit writes (tenant-scoped)
    out of the path.

    Deletion oracle: restore the old hand-rolled ``safe_config`` block (or an
    f-string of the raw config). ``PushNotificationConfig.__repr__`` masks the
    token with ``'***'``, so secret-absence stays green and the ``REDACTED``-present
    assert is what reddens.
    """
    from adcp import create_mcp_webhook_payload
    from adcp.webhooks import GeneratedTaskStatus

    from src.services.protocol_webhook_service import ProtocolWebhookService
    from tests.e2e._webhook_capture import WebhookCaptureHandler, run_webhook_capture_server
    from tests.factories import PushNotificationConfigFactory

    class _Receiver(WebhookCaptureHandler):
        received_webhooks: list = []

    payload = create_mcp_webhook_payload(
        task_id="task_pnc_redact",
        status=GeneratedTaskStatus.completed,
        task_type="sync_creatives",
        result={},
    )

    with run_webhook_capture_server(_Receiver, _Receiver.received_webhooks, host="127.0.0.1") as info:
        config = PushNotificationConfigFactory.build(
            url=info["url"],
            authentication_type="Bearer",
            authentication_token=_SECRET,
        )
        with patch("src.services.protocol_webhook_service.logger") as mock_logger:
            sent = asyncio.run(
                ProtocolWebhookService().send_notification(
                    push_notification_config=config,
                    payload=payload,
                    metadata={"task_type": "sync_creatives"},
                )
            )
        assert info["received"], "receiver got no webhook — send_notification did not reach the wire"

    # Guard against a vacuous pass: the URL-missing early return skips the log site.
    assert sent is True, "send_notification did not deliver — the log assertion would be vacuous"
    _assert_log_redacted(mock_logger)
