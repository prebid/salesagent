"""Integration tests for the idempotency insert ceiling (RATE_LIMITED).

Each fresh idempotency_key stores a cache row for the replay TTL, so the
per-(tenant, principal, account) scope is bounded
(``MAX_ACTIVE_ATTEMPTS_PER_SCOPE``): the probe rejects the excess as
``RATE_LIMITED`` with ``retry_after`` set to when the oldest active row
expires. Replays and conflicts insert nothing and are never rate-limited.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from tests.harness.media_buy_create import MediaBuyCreateEnv

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
    """Counting, retry_after derivation, and TTL interaction at the repository."""

    @staticmethod
    def _seed_principal(tenant_id, principal_id):
        from tests.factories import PrincipalFactory, TenantFactory
        from tests.harness._base import BareIntegrationEnv

        with BareIntegrationEnv() as env:
            tenant = TenantFactory(tenant_id=tenant_id)
            PrincipalFactory(tenant=tenant, principal_id=principal_id)
            env._commit_factory_data()

    def test_full_scope_raises_rate_limited_with_retry_after(self, integration_db):
        """At the ceiling, the probe gate raises RATE_LIMITED; retry_after points at the oldest expiry."""
        from src.core.database.repositories import MediaBuyUoW
        from src.core.exceptions import AdCPError

        tenant_id = f"rl_t_{uuid.uuid4().hex[:6]}"
        principal_id = f"p_{uuid.uuid4().hex[:8]}"
        self._seed_principal(tenant_id, principal_id)

        now = datetime.now(UTC)
        _seed_scope_rows(tenant_id, principal_id, 2, ttl=timedelta(hours=1), now=now)

        with MediaBuyUoW(tenant_id) as uow:
            assert uow.idempotency_attempts is not None
            with pytest.raises(AdCPError) as exc_info:
                uow.idempotency_attempts.enforce_insert_ceiling(
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
        self._seed_principal(tenant_id, principal_id)

        now = datetime.now(UTC)
        _seed_scope_rows(tenant_id, principal_id, 2, ttl=timedelta(hours=1), now=now)

        with MediaBuyUoW(tenant_id) as uow:
            assert uow.idempotency_attempts is not None
            uow.idempotency_attempts.enforce_insert_ceiling(
                principal_id=principal_id,
                ceiling=3,
                now=now,
            )

    def test_expired_rows_free_capacity(self, integration_db):
        """Only ACTIVE rows count — a scope full of expired rows is open."""
        from src.core.database.repositories import MediaBuyUoW

        tenant_id = f"rl_t_{uuid.uuid4().hex[:6]}"
        principal_id = f"p_{uuid.uuid4().hex[:8]}"
        self._seed_principal(tenant_id, principal_id)

        seeded_at = datetime(2020, 1, 1, tzinfo=UTC)
        _seed_scope_rows(tenant_id, principal_id, 3, ttl=timedelta(minutes=1), now=seeded_at)

        with MediaBuyUoW(tenant_id) as uow:
            assert uow.idempotency_attempts is not None
            uow.idempotency_attempts.enforce_insert_ceiling(
                principal_id=principal_id,
                ceiling=1,
            )


class TestInsertRateWindow:
    """The spec's MUST: bound the INSERT RATE per scope, not just stored rows."""

    @staticmethod
    def _seed_principal(tenant_id, principal_id):
        from tests.factories import PrincipalFactory, TenantFactory
        from tests.harness._base import BareIntegrationEnv

        with BareIntegrationEnv() as env:
            tenant = TenantFactory(tenant_id=tenant_id)
            PrincipalFactory(tenant=tenant, principal_id=principal_id)
            env._commit_factory_data()

    def test_burst_over_rate_ceiling_rejects_with_short_retry_after(self, integration_db):
        """Rows created inside the trailing window count against the rate ceiling.

        retry_after points at when the oldest in-window insert leaves the
        window — bounded by the window length, far shorter than any TTL.
        """
        from src.core.database.repositories import MediaBuyUoW
        from src.core.exceptions import AdCPError

        tenant_id = f"rlw_t_{uuid.uuid4().hex[:6]}"
        principal_id = f"p_{uuid.uuid4().hex[:8]}"
        self._seed_principal(tenant_id, principal_id)
        # Seeded rows are created NOW — inside the trailing window by construction.
        _seed_scope_rows(tenant_id, principal_id, 2, ttl=timedelta(hours=1), now=datetime.now(UTC))

        with MediaBuyUoW(tenant_id) as uow:
            assert uow.idempotency_attempts is not None
            with pytest.raises(AdCPError) as exc_info:
                uow.idempotency_attempts.enforce_insert_ceiling(
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
        self._seed_principal(tenant_id, principal_id)
        _seed_scope_rows(tenant_id, principal_id, 2, ttl=timedelta(hours=1), now=datetime.now(UTC))

        with MediaBuyUoW(tenant_id) as uow:
            assert uow.idempotency_attempts is not None
            # Probe from 11s in the future: both rows fall outside the 10s window.
            uow.idempotency_attempts.enforce_insert_ceiling(
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
        self._seed_principal(tenant_id, principal_id)
        now = datetime.now(UTC)
        _seed_scope_rows(tenant_id, principal_id, 1, ttl=timedelta(hours=24), now=now)

        with MediaBuyUoW(tenant_id) as uow:
            assert uow.idempotency_attempts is not None
            with pytest.raises(AdCPError) as exc_info:
                uow.idempotency_attempts.enforce_insert_ceiling(
                    principal_id=principal_id,
                    ceiling=1,
                    now=now + timedelta(seconds=11),
                )

        assert exc_info.value.retry_after == 3600, "retry_after must clamp to the Error model's upper bound"


class TestInsertCeilingThroughEntrypoint:
    """The probe gate end-to-end: fresh keys reject on the wire, replays never do."""

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

    def test_fresh_key_over_ceiling_rejects_rate_limited_on_wire(self, integration_db, monkeypatch):
        """A fresh key in a full scope rejects with RATE_LIMITED + retry_after on the real wire."""
        from tests.harness.transport import Transport
        from tests.helpers import assert_envelope_shape

        monkeypatch.setattr("src.core.database.repositories.idempotency_attempt.MAX_ACTIVE_ATTEMPTS_PER_SCOPE", 1)

        with MediaBuyCreateEnv() as env:
            _tenant, _principal, product, _pricing = env.setup_media_buy_data()
            first = env.call_impl(**self._create_kwargs(product, f"rlfill-{uuid.uuid4().hex}", po_number="RL-FILL"))
            assert first.status in {"completed", "submitted"}

            result = env.call_via(
                Transport.REST, **self._create_kwargs(product, f"rlfresh-{uuid.uuid4().hex}", po_number="RL-FRESH")
            )

        assert result.is_error, f"A fresh key in a full scope must reject, got: {result.payload}"
        assert_envelope_shape(result.wire_error_envelope, "RATE_LIMITED", recovery="transient")
        retry_after = result.wire_error_envelope["adcp_error"].get("retry_after")
        assert isinstance(retry_after, int) and retry_after >= 1, (
            f"RATE_LIMITED must carry integer retry_after >= 1, got {retry_after!r}"
        )

    def test_replay_is_never_rate_limited(self, integration_db, monkeypatch):
        """Retrying a cached key replays verbatim even when the scope is at the ceiling."""
        from src.core.schemas._base import CreateMediaBuySuccess

        monkeypatch.setattr("src.core.database.repositories.idempotency_attempt.MAX_ACTIVE_ATTEMPTS_PER_SCOPE", 1)

        idem_key = f"rlreplay-{uuid.uuid4().hex}"
        with MediaBuyCreateEnv() as env:
            _tenant, _principal, product, _pricing = env.setup_media_buy_data()
            kwargs = self._create_kwargs(product, idem_key, po_number="RL-REPLAY")
            first = env.call_impl(**kwargs)
            assert isinstance(first.response, CreateMediaBuySuccess)

            second = env.call_impl(**kwargs)

        assert second.replayed is True, "a replay inserts nothing and must never be rate-limited"
        assert second.response.media_buy_id == first.response.media_buy_id
