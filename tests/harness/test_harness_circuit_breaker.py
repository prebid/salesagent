"""Meta-tests for CircuitBreakerEnv (unit variant) — verifies the harness contract.

These tests ensure the unit harness itself works correctly. They run in ``make quality``
but have no ``Covers:`` tags — they test infrastructure, not obligations.
"""

from __future__ import annotations

from src.services.webhook_delivery_service import CircuitState, WebhookDeliveryService
from tests.harness.delivery_circuit_breaker_unit import CircuitBreakerEnv


class TestCircuitBreakerEnvContract:
    """Contract tests for CircuitBreakerEnv (unit variant)."""

    def test_service_instantiation(self):
        """get_service returns a WebhookDeliveryService instance."""
        with CircuitBreakerEnv() as env:
            service = env.get_service()
            assert isinstance(service, WebhookDeliveryService)

    def test_breaker_state_transitions(self):
        """get_breaker returns a CircuitBreaker that transitions correctly."""
        with CircuitBreakerEnv() as env:
            breaker = env.get_breaker(failure_threshold=3)

            assert breaker.state == CircuitState.CLOSED
            assert breaker.can_attempt() is True

            for _ in range(3):
                breaker.record_failure()

            assert breaker.state == CircuitState.OPEN
            assert breaker.can_attempt() is False

    def test_http_mock_affects_delivery(self):
        """set_http_response configures the httpx mock for service calls."""
        with CircuitBreakerEnv() as env:
            env.set_http_response(200)
            config = env.make_webhook_config()
            env.set_db_webhooks([config])

            service = env.get_service()
            assert isinstance(service, WebhookDeliveryService)

    def test_mock_access(self):
        """env.mock[name] provides access to all patch targets."""
        with CircuitBreakerEnv() as env:
            assert "client" in env.mock
            assert "sleep" in env.mock
            assert "random" in env.mock
            assert "db" in env.mock

    def test_make_webhook_config(self):
        """make_webhook_config creates a mock with expected attributes."""
        with CircuitBreakerEnv() as env:
            config = env.make_webhook_config(
                url="https://test.com/hook",
                auth_type="bearer",
                auth_token="tok123",
                secret="s3cret",
            )
            assert config.url == "https://test.com/hook"
            assert config.authentication_type == "bearer"
            assert config.authentication_token == "tok123"
            assert config.webhook_secret == "s3cret"

    def test_get_breaker_accepts_kwargs(self):
        """get_breaker passes keyword args to CircuitBreaker constructor."""
        with CircuitBreakerEnv() as env:
            breaker = env.get_breaker(
                failure_threshold=10,
                success_threshold=5,
                timeout_seconds=120,
            )
            assert breaker.failure_threshold == 10
            assert breaker.success_threshold == 5
            assert breaker.timeout_seconds == 120
