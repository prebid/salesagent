"""WebhookEnv — unit test environment for deliver_webhook_with_retry.

Patches: requests.post, WebhookURLValidator.validate_webhook_url,
         time.sleep, get_db_session (all in src.core.webhook_delivery)

Usage::

    with WebhookEnv() as env:
        env.set_http_status(200)
        success, result = env.call_deliver(
            webhook_url="https://example.com/hook",
            payload={"event": "delivery.update"},
        )
        assert success is True
        assert result["status"] == "delivered"

Available mocks via env.mock:
    "post"      -- requests.post mock
    "validate"  -- WebhookURLValidator.validate_webhook_url mock
    "sleep"     -- time.sleep mock
    "db"        -- get_db_session mock
"""

from __future__ import annotations

from unittest.mock import MagicMock

from tests.harness._base import BaseTestEnv
from tests.harness._mixins import WebhookMixin


class WebhookEnv(WebhookMixin, BaseTestEnv):
    """Unit test environment for deliver_webhook_with_retry.

    Fluent API (from WebhookMixin):
        set_http_status(code, text)       -- configure single HTTP response
        set_http_sequence(responses)      -- configure sequence of responses
        set_http_error(exception)         -- make requests.post raise
        set_url_invalid(error_msg)        -- make URL validation fail
        call_deliver(...)                 -- call deliver_webhook_with_retry
    """

    MODULE = "src.core.webhook_delivery"
    EXTERNAL_PATCHES = {
        "post": f"{MODULE}.requests.post",
        "validate": f"{MODULE}.WebhookURLValidator.validate_webhook_url",
        "sleep": f"{MODULE}.time.sleep",
        "db": f"{MODULE}.get_db_session",
    }

    def _configure_mocks(self) -> None:
        # URL validation: valid by default
        self.mock["validate"].return_value = (True, None)

        # HTTP: 200 OK by default
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "OK"
        self.mock["post"].return_value = mock_response

        # DB session: no-op context manager
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=MagicMock())
        mock_ctx.__exit__ = MagicMock(return_value=False)
        self.mock["db"].return_value = mock_ctx
