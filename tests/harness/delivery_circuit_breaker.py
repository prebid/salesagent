"""CircuitBreakerEnv — integration test environment for WebhookDeliveryService.

Patches: httpx.Client, time.sleep, random.uniform (external/timing concerns).
Real: get_db_session for PushNotificationConfig queries (real DB).

Requires: integration_db fixture (creates test PostgreSQL DB).

Usage::

    @pytest.mark.requires_db
    def test_something(self, integration_db):
        with CircuitBreakerEnv() as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant)
            PushNotificationConfigFactory(tenant=tenant, principal=principal)

            env.set_http_response(200)
            service = env.get_service()
            result = service.send_delivery_webhook(...)

Available mocks via env.mock:
    "client"    -- httpx.Client mock
    "sleep"     -- time.sleep mock
    "random"    -- random.uniform mock
"""

from __future__ import annotations

from typing import Any

from src.core.database.models import PushNotificationConfig
from src.services.webhook_delivery_service import WebhookDeliveryService
from tests.harness._base import IntegrationEnv
from tests.harness._mixins import CircuitBreakerMixin


class CircuitBreakerEnv(CircuitBreakerMixin, IntegrationEnv):
    """Integration test environment for WebhookDeliveryService and CircuitBreaker.

    Only mocks external HTTP client, timing, and randomness.
    DB queries for PushNotificationConfig run against real database.

    Fluent API (from CircuitBreakerMixin):
        get_service()                    -- return a WebhookDeliveryService instance
        get_breaker(**kwargs)            -- return a fresh CircuitBreaker instance
        set_http_response(status_code)   -- configure httpx Client mock response
        call_send(...)                   -- call service.send_delivery_webhook
    """

    MODULE = "src.services.webhook_delivery_service"

    EXTERNAL_PATCHES = {
        "client": "src.services.webhook_delivery_service.httpx.Client",
        "sleep": "src.services.webhook_delivery_service.time.sleep",
        "random": "src.services.webhook_delivery_service.random.uniform",
    }

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._service: WebhookDeliveryService | None = None

    def _configure_mocks(self) -> None:
        # random.uniform: return 0.0 for deterministic tests
        self.mock["random"].return_value = 0.0

        # httpx.Client: 200 OK by default
        self.set_http_response(200)

        # Expose inner httpx post as mock["post"] so BDD steps can inspect call_args
        self.mock["post"] = self.mock["client"].return_value.__enter__.return_value.post

    def make_webhook_config(
        self,
        url: str = "https://example.com/webhook",
        auth_type: str | None = None,
        auth_token: str | None = None,
        secret: str | None = None,
    ) -> PushNotificationConfig:
        """Create a real PushNotificationConfig via factory.

        Uses the env's tenant/principal (from setup_default_data) so the
        webhook config is discoverable by call_send which queries by
        self._tenant_id.
        """
        from tests.factories import PushNotificationConfigFactory

        return PushNotificationConfigFactory(
            tenant_id=self._tenant_id,
            principal_id=self._principal_id,
            url=url,
            authentication_type=auth_type,
            authentication_token=auth_token,
            webhook_secret=secret,
        )

    def set_db_webhooks(self, configs: list[PushNotificationConfig]) -> None:
        """Commit webhook configs to the real database."""
        self._commit_factory_data()
