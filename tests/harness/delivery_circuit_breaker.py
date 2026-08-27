"""CircuitBreakerEnv — integration test environment for WebhookDeliveryService.

Patches: the outbound webhook SOCKET, time.sleep, random.uniform (external/timing
concerns).
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
    "post"      -- the outbound webhook socket; called (url, headers=, content=)
    "sleep"     -- time.sleep mock
    "random"    -- random.uniform mock
    "ssrf"      -- send-time outbound SSRF gate (allows fixture hosts by default)
"""

from __future__ import annotations

import logging
from contextlib import ExitStack
from typing import Any

from sqlalchemy import select

from src.core.database.models import PushNotificationConfig
from src.services.webhook_delivery_service import WebhookDeliveryService
from tests.harness._base import IntegrationEnv
from tests.harness._mixins import SSRF_EXTERNAL_PATCH, CircuitBreakerMixin
from tests.helpers.log_capture import LogCaptureHandler


class CircuitBreakerEnv(CircuitBreakerMixin, IntegrationEnv):
    """Integration test environment for WebhookDeliveryService and CircuitBreaker.

    Only mocks external HTTP client, timing, and randomness.
    DB queries for PushNotificationConfig run against real database.

    Fluent API (from CircuitBreakerMixin):
        get_service()                    -- return a WebhookDeliveryService instance
        get_breaker(**kwargs)            -- return a fresh CircuitBreaker instance
        set_http_response(status_code)   -- answer every outbound webhook with status_code
        call_send(...)                   -- call service.send_delivery_webhook
        make_webhook_config(...)         -- create a PushNotificationConfig in DB
        set_db_webhooks(configs)         -- replace webhook configs in DB
    """

    MODULE = "src.services.webhook_delivery_service"

    EXTERNAL_PATCHES = {
        "sleep": "src.services.webhook_delivery_service.time.sleep",
        "random": "src.services.webhook_delivery_service.random.uniform",
        **SSRF_EXTERNAL_PATCH,
    }

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._service: WebhookDeliveryService | None = None
        self._log_handler: LogCaptureHandler | None = None
        self._wire: ExitStack | None = None
        self.captured_logs: list[str] = []

    def __enter__(self) -> CircuitBreakerEnv:
        result = super().__enter__()
        # Attach log capture to the webhook delivery service logger
        self._log_handler = LogCaptureHandler()
        webhook_logger = logging.getLogger("src.services.webhook_delivery_service")
        webhook_logger.addHandler(self._log_handler)
        self.captured_logs = self._log_handler.records
        return result  # type: ignore[return-value]

    def __exit__(self, *exc: object) -> bool:
        # Remove log capture handler
        if self._log_handler is not None:
            webhook_logger = logging.getLogger("src.services.webhook_delivery_service")
            webhook_logger.removeHandler(self._log_handler)
            self._log_handler = None
        self.close_webhook_wire()
        return super().__exit__(*exc)

    def _configure_mocks(self) -> None:
        # random.uniform: return 0.0 for deterministic tests
        self.mock["random"].return_value = 0.0

        self._configure_ssrf_default()

        # Installs mock["post"] (the outbound socket) answering 200 by default.
        self.install_webhook_wire()

    def make_webhook_config(
        self,
        url: str = "https://example.com/webhook",
        auth_type: str | None = None,
        auth_token: str | None = None,
        secret: str | None = None,
    ) -> PushNotificationConfig:
        """Create a PushNotificationConfig via factory and return the ORM instance."""
        from tests.factories import PushNotificationConfigFactory

        # Reuse existing tenant/principal from setup_default_data
        session = self._session
        from src.core.database.models import Principal, Tenant

        tenant = session.scalars(select(Tenant).filter_by(tenant_id=self._tenant_id)).first()
        principal = session.scalars(
            select(Principal).filter_by(tenant_id=self._tenant_id, principal_id=self._principal_id)
        ).first()

        auth_type, auth_token = self.webhook_auth_fields(auth_type, auth_token, secret)
        return PushNotificationConfigFactory(
            tenant=tenant,
            principal=principal,
            url=url,
            authentication_type=auth_type,
            authentication_token=auth_token,
            is_active=True,
        )

    def set_db_webhooks(self, webhook_list: list[PushNotificationConfig]) -> None:
        """Replace active webhook configs in DB with the given list.

        Deactivates all existing configs for this tenant/principal, then
        persists the new ones (already created by make_webhook_config).
        """
        session = self._session
        existing = session.scalars(
            select(PushNotificationConfig).filter_by(
                tenant_id=self._tenant_id,
                principal_id=self._principal_id,
                is_active=True,
            )
        ).all()
        for cfg in existing:
            if cfg not in webhook_list:
                cfg.is_active = False
        session.commit()
