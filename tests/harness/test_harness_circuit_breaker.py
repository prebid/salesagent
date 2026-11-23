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

    # "The service delivers to the real local origin the config points at" STOOD HERE and
    # is deleted. It bound a real socket, which no in-process test can legitimately do:
    # the only address available is loopback, production's egress gate refuses loopback,
    # and the case therefore ran only under ADCP_OUTBOUND_ALLOW_PRIVATE —
    # docker-compose.e2e.yml:988 records that hatch as "considered and rejected" for this
    # purpose because it "opens 127.0.0.1, host.docker.internal and all of RFC1918 for
    # whatever sets it".
    #
    # It also was not a harness contract. The rest of this class grades the ENV (its mock
    # set, its breaker wiring); that case graded production's delivery path, which belongs
    # where an origin is admitted on its own terms:
    #
    #   tests/bdd/features/BR-UC-004-deliver-media-buy-metrics.feature:412, :434, :445
    #     the circuit breaker opening, probing half-open, and closing after recovery.

    def test_mock_access(self):
        """env.mock[name] provides access to all patch targets — timing only, no transport.

        The set is pinned rather than sampled: a patch target added here is a
        thing production no longer decides for itself, and that deserves to be
        noticed. ``ssrf`` was in this set until gh-#1589 — pointed at a send-side
        validator production had already stopped calling, so it intercepted
        nothing while making the harness look like it could drive a refusal.
        Egress policy is now graded by naming a destination the real gate
        refuses, not by programming a verdict.
        """
        with CircuitBreakerEnv() as env:
            # No "post" either: #1291 C1 made the outbound socket the only webhook
            # transport, and #1802 then replaced that socket with a REAL local
            # origin. Delivery is read off the origin, never off a patch target.
            assert set(env.mock) == {"sleep", "random", "db", "logger"}

    def test_make_webhook_config(self):
        """make_webhook_config creates a mock carrying only columns production reads.

        The spec-cased scheme is deliberate: ``AuthenticationScheme =
        ["Bearer", "HMAC-SHA256"]`` @ pinned AdCP 3.1.1 is what every writer in
        ``src/`` persists, so it is the row this mock should stand in for.
        """
        with CircuitBreakerEnv() as env:
            config = env.make_webhook_config(
                auth_type="Bearer",
                auth_token="tok123",
            )
            assert config.url == env.webhook_url
            assert config.authentication_type == "Bearer"
            assert config.authentication_token == "tok123"

    def test_make_webhook_config_puts_the_hmac_secret_on_the_spec_selector(self):
        """``secret=`` seeds ``authentication``, not the retired webhook_secret column.

        security.mdx @ v3.1.1 :1424 defines ONE selector for how a webhook is
        authenticated; #1291 C1 collapsed the delivery service's second one
        (``webhook_secret``, which production never wrote) onto it. The fold lives
        in :meth:`CircuitBreakerMixin.webhook_auth_fields`, so this also pins the
        ``secret=`` convenience the mixin still accepts.

        The explicit ``url=`` is the other half: the default is the running local
        origin (see the test above), so an override is the only thing that proves
        a caller can still address an endpoint the origin does not serve.
        """
        with CircuitBreakerEnv() as env:
            config = env.make_webhook_config(url="https://test.com/hook", secret="s3cret")

            assert config.url == "https://test.com/hook"
            assert config.authentication_type == "HMAC-SHA256"
            assert config.authentication_token == "s3cret"

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
