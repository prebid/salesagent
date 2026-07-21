"""Tests for the dormant idempotency insert-ceiling primitive.

Direct repository/policy tests keep the future substrate correct. The final
entrypoint regression proves create_media_buy does not invoke that substrate
while the seller advertises ``idempotency.supported=false``.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from src.services.idempotency_policy import enforce_insert_ceiling
from tests.harness._idempotency import fresh_idempotency_key
from tests.harness.media_buy_create import MediaBuyCreateEnv
from tests.helpers import seed_principal

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


def _seed_scope_rows(tenant_id, principal_id, count, *, ttl, now):
    from tests.helpers import make_active_cached_success, seed_cached_success

    for i in range(count):
        seed_cached_success(
            tenant_id,
            principal_id,
            f"ceiling-{uuid.uuid4().hex}",
            response_model=make_active_cached_success(f"mb_ceiling_{i}"),
            payload_hash=f"hash-{i}",
            ttl=ttl,
            now=now,
        )


class TestInsertCeilingRepository:
    """Dormant primitive: counting, retry_after derivation, and TTL interaction."""

    def test_full_scope_raises_rate_limited_with_retry_after(self, integration_db):
        """The explicit policy call raises RATE_LIMITED at its configured ceiling."""
        from src.core.database.repositories import MediaBuyUoW
        from src.core.exceptions import AdCPError

        tenant_id = f"rl_t_{uuid.uuid4().hex[:6]}"
        principal_id = f"p_{uuid.uuid4().hex[:8]}"
        seed_principal(tenant_id, principal_id)

        now = datetime.now(UTC)
        _seed_scope_rows(tenant_id, principal_id, 2, ttl=timedelta(hours=1), now=now)

        with MediaBuyUoW(tenant_id) as uow:
            assert uow.idempotency_attempts is not None
            with pytest.raises(AdCPError) as exc_info:
                enforce_insert_ceiling(
                    uow.idempotency_attempts,
                    principal_id=principal_id,
                    ceiling=2,
                    now=now,
                )

        exc = exc_info.value
        assert exc.error_code == "RATE_LIMITED"
        assert exc.recovery == "transient"
        # Both rows were seeded with a 1h TTL from ``now`` — the oldest frees
        # capacity in exactly 3600s.
        assert exc.retry_after == 3600

    def test_under_ceiling_is_a_noop(self, integration_db):
        from src.core.database.repositories import MediaBuyUoW

        tenant_id = f"rl_t_{uuid.uuid4().hex[:6]}"
        principal_id = f"p_{uuid.uuid4().hex[:8]}"
        seed_principal(tenant_id, principal_id)

        now = datetime.now(UTC)
        _seed_scope_rows(tenant_id, principal_id, 2, ttl=timedelta(hours=1), now=now)

        with MediaBuyUoW(tenant_id) as uow:
            assert uow.idempotency_attempts is not None
            enforce_insert_ceiling(
                uow.idempotency_attempts,
                principal_id=principal_id,
                ceiling=3,
                now=now,
            )

    def test_expired_rows_free_capacity(self, integration_db):
        """Only ACTIVE rows count — a scope full of expired rows is open."""
        from src.core.database.repositories import MediaBuyUoW

        tenant_id = f"rl_t_{uuid.uuid4().hex[:6]}"
        principal_id = f"p_{uuid.uuid4().hex[:8]}"
        seed_principal(tenant_id, principal_id)

        seeded_at = datetime(2020, 1, 1, tzinfo=UTC)
        _seed_scope_rows(tenant_id, principal_id, 3, ttl=timedelta(minutes=1), now=seeded_at)

        with MediaBuyUoW(tenant_id) as uow:
            assert uow.idempotency_attempts is not None
            enforce_insert_ceiling(
                uow.idempotency_attempts,
                principal_id=principal_id,
                ceiling=1,
            )


class TestInsertRateWindow:
    """Dormant primitive: bound insert rate per scope, not just stored rows."""

    def test_burst_over_rate_ceiling_rejects_with_short_retry_after(self, integration_db):
        """Rows created inside the trailing window count against the rate ceiling.

        retry_after points at when the oldest in-window insert leaves the
        window — bounded by the window length, far shorter than any TTL.
        """
        from src.core.database.repositories import MediaBuyUoW
        from src.core.exceptions import AdCPError

        tenant_id = f"rlw_t_{uuid.uuid4().hex[:6]}"
        principal_id = f"p_{uuid.uuid4().hex[:8]}"
        seed_principal(tenant_id, principal_id)
        # Seeded rows are created NOW — inside the trailing window by construction.
        _seed_scope_rows(tenant_id, principal_id, 2, ttl=timedelta(hours=1), now=datetime.now(UTC))

        with MediaBuyUoW(tenant_id) as uow:
            assert uow.idempotency_attempts is not None
            with pytest.raises(AdCPError) as exc_info:
                enforce_insert_ceiling(
                    uow.idempotency_attempts,
                    principal_id=principal_id,
                    rate_ceiling=2,
                )

        exc = exc_info.value
        assert exc.error_code == "RATE_LIMITED"
        assert exc.recovery == "transient"
        assert 1 <= exc.retry_after <= 10, "rate-window retry_after is bounded by the window length"

    def test_rows_outside_window_do_not_count_toward_rate(self, integration_db):
        """The rate bound is a trailing window, not a lifetime count."""
        from datetime import timedelta as td

        from src.core.database.repositories import MediaBuyUoW

        tenant_id = f"rlw_t_{uuid.uuid4().hex[:6]}"
        principal_id = f"p_{uuid.uuid4().hex[:8]}"
        seed_principal(tenant_id, principal_id)
        _seed_scope_rows(tenant_id, principal_id, 2, ttl=timedelta(hours=1), now=datetime.now(UTC))

        with MediaBuyUoW(tenant_id) as uow:
            assert uow.idempotency_attempts is not None
            # Probe from 11s in the future: both rows fall outside the 10s window.
            enforce_insert_ceiling(
                uow.idempotency_attempts,
                principal_id=principal_id,
                rate_ceiling=2,
                now=datetime.now(UTC) + td(seconds=11),
            )

    def test_storage_bound_retry_after_clamps_to_spec_maximum(self, integration_db):
        """A 24h TTL would imply retry_after=86400; the spec Error model caps at 3600."""
        from src.core.database.repositories import MediaBuyUoW
        from src.core.exceptions import AdCPError

        tenant_id = f"rlc_t_{uuid.uuid4().hex[:6]}"
        principal_id = f"p_{uuid.uuid4().hex[:8]}"
        seed_principal(tenant_id, principal_id)
        now = datetime.now(UTC)
        _seed_scope_rows(tenant_id, principal_id, 1, ttl=timedelta(hours=24), now=now)

        with MediaBuyUoW(tenant_id) as uow:
            assert uow.idempotency_attempts is not None
            with pytest.raises(AdCPError) as exc_info:
                enforce_insert_ceiling(
                    uow.idempotency_attempts,
                    principal_id=principal_id,
                    ceiling=1,
                    now=now + timedelta(seconds=11),
                )

        assert exc_info.value.retry_after == 3600, "retry_after must clamp to the Error model's upper bound"


class TestInsertCeilingThroughEntrypoint:
    """Create never reaches the dormant ceiling while supported=false."""

    @staticmethod
    def _create_kwargs(product, idem_key, *, po_number="RL-1"):
        now = datetime.now(UTC)
        return {
            "brand": {"domain": "ratelimit-test.example.com"},
            "packages": [{"product_id": product.product_id, "budget": 5000.0, "pricing_option_id": "cpm_usd_fixed"}],
            "start_time": (now + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end_time": (now + timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "po_number": po_number,
            "idempotency_key": idem_key,
        }

    def test_zero_ceiling_does_not_rate_limit_create(self, integration_db, monkeypatch):
        """Even an impossible ceiling cannot affect a valid keyed create."""
        from tests.harness.transport import Transport

        monkeypatch.setattr("src.services.idempotency_policy.MAX_ACTIVE_ATTEMPTS_PER_SCOPE", 0)
        monkeypatch.setattr("src.services.idempotency_policy.MAX_INSERTS_PER_WINDOW", 0)

        with MediaBuyCreateEnv() as env:
            _tenant, _principal, product, _pricing = env.setup_media_buy_data()
            result = env.call_via(
                Transport.REST,
                **self._create_kwargs(product, fresh_idempotency_key("rl-noop"), po_number="RL-NOOP"),
            )

        assert result.is_success, result.error
        assert result.payload.replayed is False
        assert result.wire_error_envelope is None
