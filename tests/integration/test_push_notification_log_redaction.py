"""#1617: the buyer's webhook credential must never reach the logs.

Drives the real create_media_buy path with a push_notification_config carrying a
credential and asserts, via caplog, that the credential value appears in no log
record. Goes red if a log site is reverted to rendering the raw config.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from src.core.schemas import CreateMediaBuySuccess
from tests.integration.test_create_media_buy_behavioral import _env, _make_request

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

_SECRET = "buyer-webhook-bearer-SECRET-should-never-be-logged"


def test_create_media_buy_registration_log_redacts_webhook_credential(integration_db, caplog):
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

    with _env() as env:
        tenant, _principal = env.setup_default_data()
        env.setup_product_chain(tenant)
        env._commit_factory_data()
        identity = enrich_identity_with_account(env.identity, req.account)
        with caplog.at_level(logging.INFO):
            result = asyncio.run(_create_media_buy_impl(req=req, identity=identity, push_notification_config=pnc))

    assert isinstance(result.response, CreateMediaBuySuccess)
    logged = "\n".join(r.getMessage() for r in caplog.records)
    # The registration log site actually ran (so this test guards it), ...
    assert "***REDACTED***" in logged, "registration log did not run — the test would not guard the leak"
    # ... and the credential itself never appears anywhere in the logs.
    assert _SECRET not in logged, "buyer webhook credential leaked to the logs (#1617)"
