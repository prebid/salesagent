"""Integration tests for the idempotency insert ceiling (RATE_LIMITED).

Each fresh idempotency_key stores a cache row for the replay TTL, so the
per-(tenant, principal, account) scope is bounded
(``LimitSettings.idempotency_max_active_attempts_per_scope``): the probe rejects the excess as
``RATE_LIMITED`` with ``retry_after`` set to when the oldest active row
expires. Replays and conflicts insert nothing and are never rate-limited.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from src.services.idempotency_policy import enforce_insert_ceiling
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
    """Counting, retry_after derivation, and TTL interaction at the repository."""

    def test_full_scope_raises_rate_limited_with_retry_after(self, integration_db):
        """At the ceiling, the probe gate raises RATE_LIMITED; retry_after points at the oldest expiry."""
        from src.core.database.repositories import MediaBuyUoW
        from src.core.exceptions import AdCPSalesAgentError

        tenant_id = f"rl_t_{uuid.uuid4().hex[:6]}"
        principal_id = f"p_{uuid.uuid4().hex[:8]}"
        seed_principal(tenant_id, principal_id)

        now = datetime.now(UTC)
        _seed_scope_rows(tenant_id, principal_id, 2, ttl=timedelta(hours=1), now=now)

        with MediaBuyUoW(tenant_id) as uow:
            assert uow.idempotency_attempts is not None
            with pytest.raises(AdCPSalesAgentError) as exc_info:
                enforce_insert_ceiling(
                    uow.idempotency_attempts,
                    principal_id=principal_id,
                    ceiling=2,
                    now=now,
                )

        exc = exc_info.value
        assert exc.error_code == "RATE_LIMITED"
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
    """The spec's MUST: bound the INSERT RATE per scope, not just stored rows."""

    def test_burst_over_rate_ceiling_rejects_with_short_retry_after(self, integration_db):
        """Rows created inside the trailing window count against the rate ceiling.

        retry_after points at when the oldest in-window insert leaves the
        window — bounded by the window length, far shorter than any TTL.
        """
        from src.core.database.repositories import MediaBuyUoW
        from src.core.exceptions import AdCPSalesAgentError

        tenant_id = f"rlw_t_{uuid.uuid4().hex[:6]}"
        principal_id = f"p_{uuid.uuid4().hex[:8]}"
        seed_principal(tenant_id, principal_id)
        # Seeded rows are created NOW — inside the trailing window by construction.
        _seed_scope_rows(tenant_id, principal_id, 2, ttl=timedelta(hours=1), now=datetime.now(UTC))

        with MediaBuyUoW(tenant_id) as uow:
            assert uow.idempotency_attempts is not None
            with pytest.raises(AdCPSalesAgentError) as exc_info:
                enforce_insert_ceiling(
                    uow.idempotency_attempts,
                    principal_id=principal_id,
                    rate_ceiling=2,
                )

        exc = exc_info.value
        assert exc.error_code == "RATE_LIMITED"
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
        from src.core.exceptions import AdCPSalesAgentError

        tenant_id = f"rlc_t_{uuid.uuid4().hex[:6]}"
        principal_id = f"p_{uuid.uuid4().hex[:8]}"
        seed_principal(tenant_id, principal_id)
        now = datetime.now(UTC)
        _seed_scope_rows(tenant_id, principal_id, 1, ttl=timedelta(hours=24), now=now)

        with MediaBuyUoW(tenant_id) as uow:
            assert uow.idempotency_attempts is not None
            with pytest.raises(AdCPSalesAgentError) as exc_info:
                enforce_insert_ceiling(
                    uow.idempotency_attempts,
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

    @pytest.fixture
    def scope_ceiling(self, monkeypatch):
        """Set the storage-abuse ceiling for the duration of one test.

        ``MAX_ACTIVE_ATTEMPTS_PER_SCOPE`` was a module constant these tests monkeypatched
        by name. It is gone: ``enforce_insert_ceiling`` reads
        ``LimitSettings.idempotency_max_active_attempts_per_scope`` off the typed settings
        on EVERY call, so the name the patch targeted no longer existed and the patch
        raised AttributeError at setup -- the tests never reached their subject.

        The ENVIRONMENT is what gets pinned, then the settings are rebuilt from it.
        Pinning the settings object does not hold: a composition root rebuilds it from the
        environment whenever it starts, and the REST leg imports ``src.app``, which mounts
        the admin app, which calls ``load_settings()``. ``CreativeSyncEnv`` pins the Gemini
        key this same way and records the same reason.

        A fixture rather than a helper so the rebuild AFTER ``monkeypatch`` restores the
        variable is owned here: without it the pinned ceiling survives into whatever test
        next reads settings without rebuilding them.
        """
        from src.core.config import load_settings

        def _pin(value: int) -> None:
            monkeypatch.setenv("IDEMPOTENCY_MAX_ACTIVE_ATTEMPTS_PER_SCOPE", str(value))
            load_settings()

        yield _pin
        load_settings()

    def test_fresh_key_over_ceiling_rejects_rate_limited_on_wire(self, integration_db, scope_ceiling):
        """A fresh key in a full scope rejects with RATE_LIMITED + retry_after on the real wire."""
        from tests.harness.transport import Transport

        scope_ceiling(1)

        with MediaBuyCreateEnv() as env:
            _tenant, _principal, product, _pricing = env.setup_media_buy_data()
            first = env.call_impl(**self._create_kwargs(product, f"rlfill-{uuid.uuid4().hex}", po_number="RL-FILL"))
            assert first.status in {"completed", "submitted"}

            result = env.call_via(
                Transport.REST, **self._create_kwargs(product, f"rlfresh-{uuid.uuid4().hex}", po_number="RL-FRESH")
            )

        assert result.is_error, f"A fresh key in a full scope must reject, got: {result.payload}"
        result.assert_wire_error("RATE_LIMITED", recovery="transient")
        # The whole entry, graded against the pin. ``core/error.json`` declares
        # ``retry_after`` "type": "number" with minimum 1 and maximum 3600, so the
        # validator inside this call checks the type and both bounds -- derived from the
        # pin, so it cannot drift from it. Eleven lines here re-implemented exactly that by
        # hand, and got it wrong: they asserted ``isinstance(retry_after, int)``, which the
        # pin does not say, and a spec-valid 3600.0 failed.
        result.assert_wire_error_is_schema_conformant()
        # Presence is the one thing the schema does NOT settle -- its ``required`` is
        # ["code", "message"] -- and a transient rejection that never says when to retry is
        # the defect this test exists for. The value is not pinned: production derives it
        # from when the oldest row leaves the window and clamps it, so an exact expectation
        # would grade the test's timing.
        assert "retry_after" in (result.wire_error_object() or {}), (
            "a RATE_LIMITED rejection must tell the buyer when to retry"
        )

    def test_replay_is_never_rate_limited(self, integration_db, scope_ceiling):
        """Retrying a cached key replays verbatim even when the scope is at the ceiling."""
        from src.core.schemas._base import CreateMediaBuySuccess

        scope_ceiling(1)

        idem_key = f"rlreplay-{uuid.uuid4().hex}"
        with MediaBuyCreateEnv() as env:
            _tenant, _principal, product, _pricing = env.setup_media_buy_data()
            kwargs = self._create_kwargs(product, idem_key, po_number="RL-REPLAY")
            first = env.call_impl(**kwargs)
            assert isinstance(first, CreateMediaBuySuccess)

            second = env.call_impl(**kwargs)

        assert second.replayed is True, "a replay inserts nothing and must never be rate-limited"
        assert second.media_buy_id == first.media_buy_id
