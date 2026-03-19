"""CircuitBreakerEnv — unit test environment for WebhookDeliveryService and CircuitBreaker.

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
    "logger"    -- module-level logger mock
    "post"      -- shortcut to httpx client.post mock
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from src.services.webhook_delivery_service import WebhookDeliveryService
from tests.harness._base import BaseTestEnv
from tests.harness._mixins import CircuitBreakerMixin


class CircuitBreakerEnv(CircuitBreakerMixin, BaseTestEnv):
    """Unit test environment for WebhookDeliveryService and CircuitBreaker.

    Fluent API (from CircuitBreakerMixin):
        get_service()                    -- return a WebhookDeliveryService instance
        get_breaker(**kwargs)            -- return a fresh CircuitBreaker instance
        set_http_response(status_code)   -- configure httpx Client mock response
        call_send(...)                   -- call service.send_delivery_webhook

    Unit-only API:
        set_db_webhooks(webhook_list)    -- configure mock DB results
        make_webhook_config(...)         -- create a mock webhook config object
    """

    MODULE = "src.services.webhook_delivery_service"
    EXTERNAL_PATCHES = {
        "client": f"{MODULE}.httpx.Client",
        "sleep": f"{MODULE}.time.sleep",
        "random": f"{MODULE}.random.uniform",
        "db": "src.core.database.database_session.get_db_session",
        "logger": f"{MODULE}.logger",
    }

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._service: WebhookDeliveryService | None = None
        self._db_session: MagicMock | None = None

    def _configure_mocks(self) -> None:
        # random.uniform: return 0.0 for deterministic tests
        self.mock["random"].return_value = 0.0

        # httpx.Client: 200 OK by default (from mixin)
        self.set_http_response(200)

        # Expose inner httpx post as mock["post"] so BDD steps can inspect call_args
        self.mock["post"] = self.mock["client"].return_value.__enter__.return_value.post

        # DB session: return a mock session with one active webhook config
        # (BDD Given steps store config in ctx dict; the unit env provides a default
        # so send_delivery_webhook finds at least one endpoint to deliver to)
        default_config = self.make_webhook_config(url="https://buyer.example.com/webhook")
        mock_session = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [default_config]
        mock_session.scalars.return_value = mock_scalars
        mock_ctx = MagicMock()
        mock_ctx.__enter__.return_value = mock_session
        mock_ctx.__exit__.return_value = None
        self.mock["db"].return_value = mock_ctx
        self._db_session = mock_session

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
