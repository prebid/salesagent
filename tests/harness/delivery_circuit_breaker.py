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

import logging
from typing import Any

from sqlalchemy import select

from src.core.database.models import PushNotificationConfig
from src.services.webhook_delivery_service import WebhookDeliveryService
from tests.harness._base import IntegrationEnv
from tests.harness._mixins import OUTBOUND_SSRF_VALIDATE_TARGET, CircuitBreakerMixin


class _LogCaptureHandler(logging.Handler):
    """Captures formatted log records into a list for assertion in tests."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.records: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(self.format(record))


class CircuitBreakerEnv(CircuitBreakerMixin, IntegrationEnv):
    """Integration test environment for WebhookDeliveryService and CircuitBreaker.

    Only mocks external HTTP client, timing, and randomness.
    DB queries for PushNotificationConfig run against real database.

    Fluent API (from CircuitBreakerMixin):
        get_service()                    -- return a WebhookDeliveryService instance
        get_breaker(**kwargs)            -- return a fresh CircuitBreaker instance
        set_http_response(status_code)   -- configure httpx Client mock response
        call_send(...)                   -- call service.send_delivery_webhook
        make_webhook_config(...)         -- create a PushNotificationConfig in DB
        set_db_webhooks(configs)         -- replace webhook configs in DB
    """

    MODULE = "src.services.webhook_delivery_service"

    EXTERNAL_PATCHES = {
        "client": "src.services.webhook_delivery_service.httpx.Client",
        "sleep": "src.services.webhook_delivery_service.time.sleep",
        "random": "src.services.webhook_delivery_service.random.uniform",
        # Fixture hostnames (hmac.example.com, etc.) are intentionally
        # unresolvable; send-time SSRF DNS is covered by dedicated unit tests.
        "ssrf": OUTBOUND_SSRF_VALIDATE_TARGET,
    }

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._service: WebhookDeliveryService | None = None
        self._log_handler: _LogCaptureHandler | None = None
        self.captured_logs: list[str] = []

    def __enter__(self) -> CircuitBreakerEnv:
        result = super().__enter__()
        # Attach log capture to the webhook delivery service logger
        self._log_handler = _LogCaptureHandler()
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
        return super().__exit__(*exc)

    def _configure_mocks(self) -> None:
        # random.uniform: return 0.0 for deterministic tests
        self.mock["random"].return_value = 0.0

        # Default: allow fixture hostnames through send-time SSRF (DNS covered
        # elsewhere). Scenarios that grade the reject branch call set_url_invalid().
        self.set_url_valid()

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
        """Create a PushNotificationConfig via factory and return the ORM instance."""
        from tests.factories import PushNotificationConfigFactory

        # Reuse existing tenant/principal from setup_default_data
        session = self._session
        from src.core.database.models import Principal, Tenant

        tenant = session.scalars(select(Tenant).filter_by(tenant_id=self._tenant_id)).first()
        principal = session.scalars(
            select(Principal).filter_by(tenant_id=self._tenant_id, principal_id=self._principal_id)
        ).first()

        return PushNotificationConfigFactory(
            tenant=tenant,
            principal=principal,
            url=url,
            authentication_type=auth_type,
            authentication_token=auth_token,
            webhook_secret=secret,
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
