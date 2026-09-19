"""#1617: the buyer's webhook credential must never reach the logs.

One test per surviving push-notification log site, each driving the real code
path with a credential-bearing config and asserting on what that site actually
logged.

``send_notification`` is the redaction choke point: it routes its config through
``src.core.log_safety.redact_push_notification_config`` before logging.

The second site, media_buy_create, is NOT a choke point of that helper — it never
imports it. It withholds by construction instead, handing the logger only the
config id and ``webhook_url_for_log(url)``, so its case grades that inherited
withhold on upstream code rather than a redaction call. See
``_assert_registration_log_withholds_credential`` for why its assertion differs.

A third site used to exist — the admin creative-status webhook logged the stored
config it was about to send with. Upstream deleted that log line outright (the
whole debug preamble in ``_deliver_sync_creatives_webhook``), so there is nothing
left there to redact and its case was removed with it: withholding by DELETING
the log strictly dominates redacting it.

Capture is taken by patching each module's ``logger`` object and reading its
``info`` call args, NOT via caplog or a handler: a full-suite run can leave
``logging.disable()`` set (or propagation off, or the root level raised) by an
earlier test, which suppresses records BEFORE any handler sees them and leaves a
capture-based assertion reading an empty string. A MagicMock logger records the
call regardless of that global logging state, while the path under test still
runs for real.

Why the mask presence assert (not just secret-absence) is the load-bearing one at
the choke point: that site is handed the ``PushNotificationConfig`` ORM row,
whose ``__repr__`` already prints ``authentication_token='***'``. Log the raw row
and no secret appears — only the ``REDACTED`` sentinel disappears. That is
precisely why the sentinel is kept distinct from ``'***'``; see
src.core.log_safety.REDACTED and
tests/unit/test_log_safety.py::test_sentinel_is_distinct_from_model_repr_mask.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from src.core.log_safety import REDACTED
from src.core.schemas import CreateMediaBuySuccess
from tests.helpers.adcp_factories import wire_push_notification_config
from tests.integration.test_create_media_buy_behavioral import _env, _make_request

pytestmark = [pytest.mark.integration]

_SECRET = "buyer-webhook-bearer-SECRET-should-never-be-logged"


def _rendered(calls) -> str:
    """Render logger calls (message template + args + kwargs) into one string."""
    return "\n".join(str(call.args) + str(call.kwargs) for call in calls)


def _pnc_info_calls(mock_logger) -> list:
    """The ``info`` calls whose template is about the push-notification config.

    Matched by normalized template so it covers all three sites
    ("push_notification_config: %s" and "[MCP/A2A] Registering push notification
    config ...").
    """
    return [
        call
        for call in mock_logger.info.call_args_list
        if call.args and "push" in str(call.args[0]).lower() and "notif" in str(call.args[0]).lower()
    ]


def _assert_log_redacted(mock_logger) -> None:
    """Assert a patched logger's info calls carry the redacted view and no secret.

    Renders every ``logger.info`` call (message template + args + kwargs). A
    MagicMock records the call even when ``logging.disable()``/propagation/level
    would suppress the record, so this observes the real log site rather than a
    caplog buffer.
    """
    # The credential itself never appears in ANY log call (asserted first: it is the
    # security failure, and it is what a raw-wire-dict log site trips) ...
    logged = _rendered(mock_logger.info.call_args_list)
    assert _SECRET not in logged, "buyer webhook credential leaked to the logs (#1617)"
    # ... and the push-notification-config log line specifically ran through the
    # redactor. Scoped to that call so the presence assert can't pass on some
    # unrelated info line that happened to carry the sentinel. At the two DB-model
    # sites this is the assertion the deletion oracle reddens, because the model's
    # own repr masks the token.
    pnc_calls = _pnc_info_calls(mock_logger)
    assert pnc_calls, (
        "no push-notification-config log call was emitted — the log site did not run (test guards nothing)"
    )
    assert REDACTED in _rendered(pnc_calls), (
        "the push-notification log line did not run redacted — this test guards nothing"
    )


def _assert_registration_log_withholds_credential(mock_logger) -> None:
    """Assert the media_buy_create registration log ran and carried no credential.

    Deliberately NOT ``_assert_log_redacted``. The two DB-model sites are handed a
    ``PushNotificationConfig`` whose own repr masks the token, so only the
    ``REDACTED`` sentinel can tell a redacted log from a raw one — there, sentinel
    presence is the load-bearing assert. This site is handed the raw A2A wire dict
    and withholds by CONSTRUCTION instead: it passes the logger only the config id
    and ``webhook_url_for_log(url)``, never the dict or its ``authentication``
    blob. Nothing is redacted here because nothing sensitive is handed over, so
    what must be pinned is the absence — and with no masking repr in the way,
    absence is a real oracle.
    """
    logged = _rendered(mock_logger.info.call_args_list)
    assert _SECRET not in logged, "buyer webhook credential leaked to the logs (#1617)"
    pnc_calls = _pnc_info_calls(mock_logger)
    assert pnc_calls, (
        "no push-notification-config log call was emitted — the log site did not run (test guards nothing)"
    )
    # Hand the raw wire dict to the logger and all three of these appear together;
    # that is the revert this guards.
    rendered_pnc = _rendered(pnc_calls)
    assert "authentication" not in rendered_pnc, "the registration log handed the authentication blob to the logger"
    assert "credentials" not in rendered_pnc, "the registration log handed the credential field to the logger"


def _run_create_media_buy_with_pnc(pnc: dict):
    """Drive the real create-media-buy registration path, module logger patched."""
    from src.core.tools.media_buy_create import _create_media_buy_impl
    from src.core.transport_helpers import enrich_identity_with_account

    req = _make_request()
    with patch("src.core.tools.media_buy_create.logger") as mock_logger:
        with _env() as env:
            tenant, _principal = env.setup_default_data()
            env.setup_product_chain(tenant)
            env._commit_factory_data()
            identity = enrich_identity_with_account(env.identity, req.account)
            result = asyncio.run(_create_media_buy_impl(req=req, identity=identity, push_notification_config=pnc))
    return result, mock_logger


@pytest.mark.requires_db
def test_create_media_buy_registration_log_withholds_webhook_credential(integration_db):
    """A non-dry-run create with a credential-bearing push_notification_config
    reaches the registration log; that log must never carry the credential.

    This site withholds by construction rather than by redaction — it hands the
    logger only the config id and ``webhook_url_for_log(url)``. Deletion oracle:
    pass ``push_notification_config`` itself into that ``logger.info`` call and the
    assertions redden, because this site receives the A2A wire dict and no masking
    repr stands between the credential and the log.
    """
    pnc = wire_push_notification_config(id="pnc_redact", credentials=_SECRET)
    result, mock_logger = _run_create_media_buy_with_pnc(pnc)

    assert isinstance(result.response, CreateMediaBuySuccess)
    _assert_registration_log_withholds_credential(mock_logger)


def test_send_notification_log_redacts_webhook_credential():
    """The webhook service logs the config it is delivering with; that log must be
    the redacted view.

    No DB: the config is a real (unsaved) ``PushNotificationConfig`` model — not a
    MagicMock, which would make the secret-absence assert vacuously true — and the
    send goes to a real loopback receiver rather than a patched private method, so
    the production ``send_notification`` body runs end to end. The task context
    carries ``task_type="sync_creatives"`` and no ``media_buy_id``, so
    ``WebhookTaskContext.records_delivery_log`` is False and the tenant-scoped
    delivery-log write stays out of the path.

    Deletion oracle: restore the old hand-rolled ``safe_config`` block (or an
    f-string of the raw config). ``PushNotificationConfig.__repr__`` masks the
    token with ``'***'``, so secret-absence stays green and the ``REDACTED``-present
    assert is what reddens.
    """
    from adcp import create_mcp_webhook_payload
    from adcp.webhooks import GeneratedTaskStatus

    from src.core.webhooks.delivery import WebhookTaskContext
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
                    task=WebhookTaskContext(
                        task_id="task_pnc_redact",
                        task_type="sync_creatives",
                        tenant_id=None,
                        principal_id=None,
                        media_buy_id=None,
                        sequence_number=1,
                        notification_type=None,
                    ),
                )
            )
        assert info["received"], "receiver got no webhook — send_notification did not reach the wire"

    # Guard against a vacuous pass: the URL-missing early return skips the log site.
    assert sent is True, "send_notification did not deliver — the log assertion would be vacuous"
    _assert_log_redacted(mock_logger)
