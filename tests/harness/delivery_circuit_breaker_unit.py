"""CircuitBreakerEnv — unit test environment for WebhookDeliveryService and CircuitBreaker.

Patches: time.sleep, random.uniform, get_db_session, and the module logger.
Real: a local HTTP origin that actually serves the delivery attempts — the
outbound transport is NOT patched (see ``LocalOriginMixin``).

Egress policy is NOT mocked. The ``ssrf`` control that used to live here pointed
at a send-side validator production no longer called, so it intercepted nothing
and every assertion behind it was vacuous (gh-#1589). A refusal is now driven by
naming a destination the real gate refuses, and whether delivery happened is
read off the real origin (``delivery_attempts``), never off a transport mock.

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
        assert env.delivery_attempts == 1

Available mocks via env.mock:
    "sleep"     -- time.sleep mock
    "random"    -- random.uniform mock
    "db"        -- get_db_session mock
    "logger"    -- module-level logger mock
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from src.services.webhook_delivery_service import WebhookDeliveryService
from tests.harness._base import BaseTestEnv
from tests.harness._mixins import CircuitBreakerMixin


class CircuitBreakerEnv(CircuitBreakerMixin, BaseTestEnv):
    """Unit test environment for WebhookDeliveryService and CircuitBreaker.

    Fluent API (from CircuitBreakerMixin / LocalOriginMixin):
        webhook_url                      -- the running origin's URL
        endpoint_key(tenant_id)          -- production's per-endpoint breaker key
        get_service()                    -- return a WebhookDeliveryService instance
        get_breaker(**kwargs)            -- return a fresh CircuitBreaker instance
        set_http_response(status_code)   -- answer every attempt with one status
        call_send(...)                   -- call service.send_delivery_webhook
        delivery_attempts / last_delivery -- what the endpoint actually received

    Unit-only API:
        set_db_webhooks(webhook_list)    -- configure mock DB results
        make_webhook_config(...)         -- create a mock webhook config object
    """

    MODULE = "src.services.webhook_delivery_service"
    EXTERNAL_PATCHES = {
        "sleep": "src.core.security.outbound_http.time.sleep",
        "random": "src.core.security.egress.attempts.random.uniform",
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

        # The origin answers 200 OK unless a test programs otherwise.
        self.set_http_response(200)

        # DB session: return a mock session with one active webhook config
        # (BDD Given steps store config in ctx dict; the unit env provides a default
        # so send_delivery_webhook finds at least one endpoint to deliver to)
        default_config = self.make_webhook_config()
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
        url: str | None = None,
        auth_type: str | None = None,
        auth_token: str | None = None,
    ) -> MagicMock:
        """Create a mock webhook config object.

        ``url`` defaults to the running origin, so the configured endpoint is one
        that really answers.

        No ``secret=`` and no ``webhook_secret`` attribute, mirroring the
        integration twin (GH #1802, GH #1894). A MagicMock answers every
        attribute, so leaving it set would let this mock keep feeding a column
        production no longer reads -- the failure mode a mock-based harness is
        worst at surfacing.
        """
        config = MagicMock()
        config.url = url if url is not None else self.webhook_url
        config.authentication_type = auth_type
        config.authentication_token = auth_token
        return config
