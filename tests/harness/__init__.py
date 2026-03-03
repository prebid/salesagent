"""Test harness package — shared test environments for obligation tests.

Each domain env is a context-manager class that sets up mocks for a specific
_impl function. Tests import and use these instead of inline @patch decorators.

Usage::

    from tests.harness import DeliveryPollEnv

    class TestSomething:
        def test_it(self):
            with DeliveryPollEnv() as env:
                env.add_buy(media_buy_id="mb_001")
                env.set_adapter_response("mb_001", impressions=5000)
                response = env.call_impl(media_buy_ids=["mb_001"])
                assert response.aggregated_totals.impressions == 5000.0
"""

from tests.harness._mock_uow import make_mock_uow
from tests.harness.delivery_circuit_breaker import CircuitBreakerEnv
from tests.harness.delivery_poll import DeliveryPollEnv
from tests.harness.delivery_webhook import WebhookEnv

__all__ = [
    "CircuitBreakerEnv",
    "DeliveryPollEnv",
    "WebhookEnv",
    "make_mock_uow",
]
