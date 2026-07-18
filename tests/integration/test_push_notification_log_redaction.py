"""#1617: the buyer's webhook credential must never reach the logs.

Drives the real create_media_buy path with a push_notification_config carrying a
credential and asserts the credential value appears in no log call. Goes red if a
log site is reverted to rendering the raw config.

Capture is taken by patching the ``media_buy_create`` logger object and reading
its ``info`` call args, NOT via caplog or a handler: a full-suite run can leave
``logging.disable()`` set (or propagation off, or the root level raised) by an
earlier test, which suppresses records BEFORE any handler sees them and leaves a
capture-based assertion reading an empty string. A MagicMock logger records the
call regardless of that global logging state, while the create path under test
still runs for real.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from src.core.schemas import CreateMediaBuySuccess
from tests.integration.test_create_media_buy_behavioral import _env, _make_request

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

_SECRET = "buyer-webhook-bearer-SECRET-should-never-be-logged"


def test_create_media_buy_registration_log_redacts_webhook_credential(integration_db):
    """A non-dry-run create with a credential-bearing push_notification_config
    reaches the registration log; that log must carry the redacted view, never the
    credential. Deletion oracle: reverting the site to log the raw config leaks
    ``_SECRET`` here.
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
    # Render every logger.info call (message template + args). A MagicMock records
    # the call even when logging.disable()/propagation/level would suppress the
    # record — so this observes the real registration log site, not a caplog buffer.
    logged = "\n".join(str(call.args) + str(call.kwargs) for call in mock_logger.info.call_args_list)
    # The registration log site ran (so this test guards it) and carries the
    # redacted view ...
    assert "***REDACTED***" in logged, "registration log did not run — the test would not guard the leak"
    # ... and the credential itself never appears in any log call.
    assert _SECRET not in logged, "buyer webhook credential leaked to the logs (#1617)"
