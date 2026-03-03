"""CircuitBreakerEnv — test environment for WebhookDeliveryService and CircuitBreaker.

Patches: httpx.Client, time.sleep, random.uniform, get_db_session
         (all in src.services.webhook_delivery_service)

Usage::

    with CircuitBreakerEnv() as env:
        breaker = env.get_breaker(failure_threshold=3)
        for _ in range(3):
            breaker.record_failure()
        assert breaker.state == CircuitState.OPEN

    with CircuitBreakerEnv() as env:
        env.set_http_response(200)
        service = env.get_service()
        service.send_delivery_webhook(...)

Available mocks via env.mock:
    "client"    -- httpx.Client mock
    "sleep"     -- time.sleep mock
    "random"    -- random.uniform mock
    "db"        -- get_db_session mock
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

from src.services.webhook_delivery_service import (
    CircuitBreaker,
    WebhookDeliveryService,
)
from tests.harness._base_unit import ImplTestEnv


class CircuitBreakerEnv(ImplTestEnv):
    """Test environment for WebhookDeliveryService and CircuitBreaker.

    Fluent API:
        get_service()                    -- return a WebhookDeliveryService instance
        get_breaker(**kwargs)            -- return a fresh CircuitBreaker instance
        set_http_response(status_code)   -- configure httpx Client mock response
        set_db_webhooks(webhook_list)    -- configure mock DB results
        call_send(...)                   -- call service.send_delivery_webhook
    """

    MODULE = "src.services.webhook_delivery_service"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._service: WebhookDeliveryService | None = None

    def _patch_targets(self) -> dict[str, str]:
        return {
            "client": f"{self.MODULE}.httpx.Client",
            "sleep": f"{self.MODULE}.time.sleep",
            "random": f"{self.MODULE}.random.uniform",
            "db": "src.core.database.database_session.get_db_session",
        }

    def _configure_defaults(self) -> None:
        # random.uniform: return 0.0 for deterministic tests
        self.mock["random"].return_value = 0.0

        # httpx.Client: 200 OK by default
        mock_response = MagicMock()
        mock_response.status_code = 200
        self.mock["client"].return_value.__enter__.return_value.post.return_value = mock_response

        # DB session: return a mock session with no webhook configs
        mock_session = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_session.scalars.return_value = mock_scalars
        mock_ctx = MagicMock()
        mock_ctx.__enter__.return_value = mock_session
        mock_ctx.__exit__.return_value = None
        self.mock["db"].return_value = mock_ctx
        self._db_session = mock_session

    def get_service(self) -> WebhookDeliveryService:
        """Return a WebhookDeliveryService instance (cached per env)."""
        if self._service is None:
            self._service = WebhookDeliveryService()
        return self._service

    def get_breaker(self, **kwargs: Any) -> CircuitBreaker:
        """Return a fresh CircuitBreaker instance with the given params.

        Example::

            breaker = env.get_breaker(failure_threshold=3, timeout_seconds=30)
        """
        return CircuitBreaker(**kwargs)

    def set_http_response(self, status_code: int) -> None:
        """Configure the httpx Client mock to return the given status code."""
        mock_response = MagicMock()
        mock_response.status_code = status_code
        self.mock["client"].return_value.__enter__.return_value.post.return_value = mock_response

    def set_db_webhooks(self, webhook_list: list[MagicMock]) -> None:
        """Configure the mock DB to return the given webhook config list."""
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = webhook_list
        self._db_session.scalars.return_value = mock_scalars

    def make_webhook_config(
        self,
        url: str = "https://example.com/webhook",
        auth_type: str | None = None,
        auth_token: str | None = None,
        secret: str | None = None,
    ) -> MagicMock:
        """Create a mock webhook config object."""
        config = MagicMock()
        config.url = url
        config.authentication_type = auth_type
        config.authentication_token = auth_token
        config.webhook_secret = secret
        return config

    def call_send(
        self,
        media_buy_id: str = "mb_001",
        tenant_id: str | None = None,
        principal_id: str | None = None,
        reporting_period_start: datetime | None = None,
        reporting_period_end: datetime | None = None,
        impressions: float = 1000.0,
        spend: float = 100.0,
        **extra: Any,
    ) -> Any:
        """Call service.send_delivery_webhook with sensible defaults."""
        service = self.get_service()
        return service.send_delivery_webhook(
            media_buy_id=media_buy_id,
            tenant_id=tenant_id or self._tenant_id,
            principal_id=principal_id or self._principal_id,
            reporting_period_start=reporting_period_start or datetime(2025, 1, 1, tzinfo=UTC),
            reporting_period_end=reporting_period_end or datetime(2025, 12, 31, tzinfo=UTC),
            impressions=impressions,
            spend=spend,
            **extra,
        )

    def call_impl(self, **kwargs: Any) -> Any:
        """Alias for call_send to satisfy ImplTestEnv interface."""
        return self.call_send(**kwargs)
