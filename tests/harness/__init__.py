"""Test harness package — shared test environments for obligation tests.

Two variants available:
- **Integration (default)**: Real database, only mocks external services.
  Requires ``integration_db`` fixture.
- **Unit**: Full mocking for fast unit tests (backward compat).

Usage (integration — preferred)::

    from tests.harness import DeliveryPollEnv

    @pytest.mark.requires_db
    def test_something(self, integration_db):
        with DeliveryPollEnv() as env:
            tenant = TenantFactory(tenant_id="t1")
            ...

Usage (unit — backward compat)::

    from tests.harness.delivery_poll_unit import DeliveryPollEnv as DeliveryPollEnvUnit

    def test_something(self):
        with DeliveryPollEnvUnit() as env:
            env.add_buy(media_buy_id="mb_001")
            ...
"""

from tests.harness._mock_uow import make_mock_uow

# Integration envs (default — real DB, only mock external services)
from tests.harness.delivery_circuit_breaker import CircuitBreakerEnv
from tests.harness.delivery_poll import DeliveryPollEnv
from tests.harness.delivery_webhook import WebhookEnv
from tests.harness.product import ProductEnv

__all__ = [
    "CircuitBreakerEnv",
    "DeliveryPollEnv",
    "ProductEnv",
    "WebhookEnv",
    "make_mock_uow",
]
