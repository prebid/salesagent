"""CircuitBreakerEnv — unit test environment for WebhookDeliveryService and CircuitBreaker.

Patches: time.sleep, random.uniform, get_db_session, and the module logger.
Real: a local HTTP origin that actually serves the delivery attempts — the
outbound transport is NOT patched (see ``LocalOriginMixin``).

Egress policy is NOT mocked. The ``ssrf`` control that used to live here pointed
at a send-side validator production no longer called, so it intercepted nothing
and every assertion behind it was vacuous (gh-#1589). A refusal is now driven by
naming a destination the real gate refuses, and whether delivery happened is
read off the real origin (``delivery_attempts``), never off a transport mock.

The origin is a loopback listener on an ephemeral port, so this env still needs
neither PostgreSQL nor the network. It also carries the RFC 9421 signing arm for
free: a signed delivery arrives at the origin with its real ``Signature`` /
``Signature-Input`` headers, readable off ``last_delivery()`` rather than off a
socket mock's ``call_args``.

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

There is deliberately no "post" and no "ssrf" control here: the transport and the
egress gate are both real (see above).
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
        # Timing and jitter moved out of this module with the delivery seam
        # (#1802): ``webhook_delivery_service`` no longer imports ``random`` at
        # all, so the old ``{MODULE}.random.uniform`` target would now raise on
        # patch — and ``{MODULE}.time.sleep`` would patch a name the retry
        # ladder has stopped consulting, i.e. observe nothing. Patch where the
        # schedule is actually decided.
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

        # The origin answers 200 OK unless a test programs otherwise. No socket
        # is installed and no SSRF default is configured: both controls this env
        # used to own are now real (see the module docstring).
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
        secret: str | None = None,
        operation_id: str | None = "op_harness_0001",
        token: str | None = None,
    ) -> MagicMock:
        """Create a mock webhook config object.

        ``url`` defaults to the running origin, so the configured endpoint is one
        that really answers.

        No ``webhook_secret`` attribute is ever set, mirroring the integration twin
        (GH #1894). A MagicMock answers every attribute, so leaving that column set
        would let this mock keep feeding a column production no longer reads -- the
        failure mode a mock-based harness is worst at surfacing.

        ``secret=`` survives as a CONVENIENCE, not as a second selector: it is folded
        onto the spec's one ``authentication`` selector by
        :meth:`CircuitBreakerMixin.webhook_auth_fields` (security.mdx @ v3.1.1 :1424),
        so asking for an HMAC secret yields ``("HMAC-SHA256", secret)`` -- exactly the
        row ``media_buy_create`` and the A2A push-config handler persist -- and never
        the retired column.

        ``operation_id`` and ``token`` are set for the mirror-image reason: the sender
        echoes both into the payload, and a MagicMock would hand it a Mock object that
        the pinned envelope refuses -- so the delivery would fail for a reason no
        production registration can produce. ``operation_id`` defaults to a value
        because a conformant registration carries one and the envelope REQUIRES it;
        pass ``None`` to model a buyer that sent none.
        """
        auth_type, auth_token = self.webhook_auth_fields(auth_type, auth_token, secret)
        config = MagicMock()
        config.url = url if url is not None else self.webhook_url
        config.authentication_type = auth_type
        config.authentication_token = auth_token
        config.operation_id = operation_id
        config.token = token
        return config
