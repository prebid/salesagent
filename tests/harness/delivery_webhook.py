"""WebhookEnv — integration test environment for deliver_webhook_with_retry.

Patches: requests.post, WebhookURLValidator.validate_webhook_url, time.sleep
         (all external/timing concerns).
Real: get_db_session for delivery record tracking (requires integration_db fixture).

Requires: integration_db fixture (creates test PostgreSQL DB).

Usage::

    @pytest.mark.requires_db
    def test_something(self, integration_db):
        with WebhookEnv() as env:
            env.set_http_status(200)
            success, result = env.call_deliver(
                webhook_url="https://example.com/hook",
                payload={"event": "delivery.update"},
            )
            assert success is True

Available mocks via env.mock:
    "post"      -- requests.post mock
    "validate"  -- WebhookURLValidator.validate_webhook_url mock
    "sleep"     -- time.sleep mock
"""

from __future__ import annotations

from unittest.mock import MagicMock

from tests.harness._base import IntegrationEnv
from tests.harness._mixins import WebhookMixin


class WebhookEnv(WebhookMixin, IntegrationEnv):
    """Integration test environment for deliver_webhook_with_retry.

    Only mocks external HTTP calls, URL validation, and time.sleep.
    DB operations for delivery tracking go through the real database.

    Fluent API (from WebhookMixin):
        set_http_status(code, text)       -- configure single HTTP response
        set_http_sequence(responses)      -- configure sequence of responses
        set_http_error(exception)         -- make requests.post raise
        set_url_invalid(error_msg)        -- make URL validation fail
        call_deliver(...)                 -- call deliver_webhook_with_retry
    """

    MODULE = "src.core.webhook_delivery"

    EXTERNAL_PATCHES = {
        "post": "src.core.webhook_delivery.requests.post",
        "validate": "src.core.webhook_delivery.WebhookURLValidator.validate_webhook_url",
        "sleep": "src.core.webhook_delivery.time.sleep",
    }

    def _configure_mocks(self) -> None:
        # URL validation: valid by default
        self.mock["validate"].return_value = (True, None)

        # HTTP: 200 OK by default
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "OK"
        self.mock["post"].return_value = mock_response
