"""Test harness package — shared test environments for obligation tests.

Two variants available:
- **Integration (default)**: Real database, only mocks external services.
  Requires ``integration_db`` fixture.
- **Unit**: Full mocking for fast unit tests (backward compat).

Multi-transport testing supported via ``Transport`` enum and ``call_via()``.

Usage (integration — preferred)::

    from tests.harness._base import IntegrationEnv

    class MyDomainEnv(IntegrationEnv):
        EXTERNAL_PATCHES = {"adapter": "src.adapters.get_adapter"}

        def _configure_mocks(self):
            self.mock["adapter"].return_value = MagicMock()

        def call_impl(self, **kwargs):
            req = MyRequest(**kwargs)
            return _my_impl(req, self.identity)

    @pytest.mark.requires_db
    def test_something(self, integration_db):
        with MyDomainEnv() as env:
            tenant = TenantFactory(tenant_id="t1")
            response = env.call_impl(...)

Usage (unit — backward compat)::

    from tests.harness._base import BaseTestEnv

    class MyDomainEnvUnit(BaseTestEnv):
        EXTERNAL_PATCHES = {"db": "src.core.database.database_session.get_db_session", ...}
        ...

    def test_something(self):
        with MyDomainEnvUnit() as env:
            ...
"""

from tests.harness._identity import make_identity
from tests.harness._mock_uow import make_mock_uow
from tests.harness.assertions import assert_envelope, assert_error_result, assert_payload_field

# Creative envs (multi-transport)
from tests.harness.creative_formats import CreativeFormatsEnv
from tests.harness.creative_list import CreativeListEnv
from tests.harness.creative_sync import CreativeSyncEnv

# Delivery envs (domain-specific)
from tests.harness.delivery_circuit_breaker import CircuitBreakerEnv
from tests.harness.delivery_poll import DeliveryPollEnv
from tests.harness.delivery_webhook import WebhookEnv

# Product env
from tests.harness.product import ProductEnv

# Transport helpers
from tests.harness.transport import Transport, TransportResult

__all__ = [
    # Helpers
    "make_identity",
    "make_mock_uow",
    "assert_envelope",
    "assert_error_result",
    "assert_payload_field",
    # Creative envs
    "CreativeFormatsEnv",
    "CreativeListEnv",
    "CreativeSyncEnv",
    # Delivery envs
    "CircuitBreakerEnv",
    "DeliveryPollEnv",
    "WebhookEnv",
    # Product env
    "ProductEnv",
    # Transport
    "Transport",
    "TransportResult",
]
