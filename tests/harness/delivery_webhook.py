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

from src.core.database.models import PushNotificationConfig
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

    def make_webhook_config(
        self,
        url: str = "https://example.com/webhook",
        auth_type: str | None = None,
        auth_token: str | None = None,
        secret: str | None = None,
    ) -> PushNotificationConfig:
        """Create a real PushNotificationConfig via factory."""
        from sqlalchemy import select

        from src.core.database.models import Principal, Tenant
        from tests.factories import PushNotificationConfigFactory

        tenant = self._session.scalars(select(Tenant).filter_by(tenant_id=self._tenant_id)).one()
        principal = self._session.scalars(
            select(Principal).filter_by(tenant_id=self._tenant_id, principal_id=self._principal_id)
        ).one()

        return PushNotificationConfigFactory(
            tenant=tenant,
            principal=principal,
            url=url,
            authentication_type=auth_type,
            authentication_token=auth_token,
            webhook_secret=secret,
        )

    def set_db_webhooks(self, configs: list[PushNotificationConfig]) -> None:
        """Commit webhook configs to the real database."""
        self._commit_factory_data()
