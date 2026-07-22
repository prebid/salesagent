"""Integration tests for verbatim SUCCESS replay through _create_media_buy_impl.

AdCP 3.0.1 idempotency: retrying with the same idempotency_key replays the
ORIGINAL success VERBATIM (top-level ``replayed: true``), never re-evaluating;
the same key carrying a *different* canonical payload raises
``IDEMPOTENCY_CONFLICT``; errors are NEVER cached, so a retry after an error
re-executes.

These pin the replay path through the production entrypoint — if the lookup were
deleted, the happy-path _impl tests would still pass green.
"""

import uuid
from datetime import UTC, datetime

import pytest

from tests.harness.media_buy_create import MediaBuyCreateEnv
from tests.helpers import seed_principal

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


def _seed_success(tenant_id, principal_id, idempotency_key, *, payload_hash, media_buy_id="mb_seeded"):
    """Seed a cached active-buy success (the verbatim cache).

    ``payload_hash`` must be the canonical hash of the request the test will
    retry (a hash match replays); pass a non-matching hash to exercise the
    IDEMPOTENCY_CONFLICT path.
    """
    from tests.helpers import make_active_cached_success, seed_cached_success

    seed_cached_success(
        tenant_id,
        principal_id,
        idempotency_key,
        response_model=make_active_cached_success(media_buy_id),
        payload_hash=payload_hash,
    )


def _make_request(idempotency_key, *, po_number="REPLAY-1"):
    from src.core.schemas import CreateMediaBuyRequest

    return CreateMediaBuyRequest(
        brand={"domain": "replay-test.example.com"},
        packages=[],
        start_time=datetime(2026, 6, 1, tzinfo=UTC),
        end_time=datetime(2026, 6, 30, tzinfo=UTC),
        po_number=po_number,
        idempotency_key=idempotency_key,
    )


def _identity(tenant_id, principal_id):
    from src.core.testing_hooks import AdCPTestContext
    from tests.factories import PrincipalFactory

    return PrincipalFactory.make_identity(
        principal_id=principal_id,
        tenant_id=tenant_id,
        testing_context=AdCPTestContext(test_session_id="replay_test"),
    )


class TestImplReplaysCachedSuccess:
    """_create_media_buy_impl replays the cached success verbatim on key match."""

    async def test_cached_success_replayed_verbatim(self, integration_db):
        from src.core.idempotency_canonical import canonical_request_hash
        from src.core.schemas._base import CreateMediaBuyResult, CreateMediaBuySuccess
        from src.core.tools.media_buy_create import _create_media_buy_impl

        idem_key = f"replay-{uuid.uuid4().hex}"
        tenant_id = f"replay_t_{uuid.uuid4().hex[:6]}"
        principal_id = f"p_{uuid.uuid4().hex[:8]}"

        seed_principal(tenant_id, principal_id)
        # Stored hash matches the retry's canonical hash → a true replay.
        _seed_success(
            tenant_id,
            principal_id,
            idem_key,
            payload_hash=canonical_request_hash(_make_request(idem_key)),
            media_buy_id="mb_original_123",
        )

        result = await _create_media_buy_impl(req=_make_request(idem_key), identity=_identity(tenant_id, principal_id))

        assert isinstance(result, CreateMediaBuyResult)
        assert isinstance(result.response, CreateMediaBuySuccess)
        assert result.response.media_buy_id == "mb_original_123"
        assert result.status == "completed"
        assert result.replayed is True  # top-level replay marker, injected at replay time

    async def test_different_payload_same_key_raises_conflict(self, integration_db):
        from src.core.exceptions import AdCPError
        from src.core.tools.media_buy_create import _create_media_buy_impl

        idem_key = f"conflict-{uuid.uuid4().hex}"
        tenant_id = f"conflict_t_{uuid.uuid4().hex[:6]}"
        principal_id = f"p_{uuid.uuid4().hex[:8]}"

        seed_principal(tenant_id, principal_id)
        # Stored hash will NOT match the request's canonical hash → conflict.
        _seed_success(tenant_id, principal_id, idem_key, media_buy_id="mb_first", payload_hash="non-matching-hash")

        with pytest.raises(AdCPError) as exc_info:
            await _create_media_buy_impl(req=_make_request(idem_key), identity=_identity(tenant_id, principal_id))

        exc = exc_info.value
        assert exc.error_code == "IDEMPOTENCY_CONFLICT"
        # Read-oracle defense: the conflict must not leak the cached payload/id.
        assert "mb_first" not in exc.message

    async def test_invalid_cached_envelope_treated_as_miss(self, integration_db):
        """A cache row that no longer validates is a MISS — the retry re-executes.

        Pins the schema-drift guard: a stored envelope from an older deploy that no
        longer validates must never surface as an internal error on a retry of a
        previously-successful call. The probe treats it as absent and re-executes
        (here the bare request then fails downstream as a typed AdCPError — what
        matters is it is neither a replay, a conflict, nor a raw ValidationError).
        """
        from pydantic import BaseModel

        from src.core.exceptions import AdCPError
        from src.core.idempotency_canonical import canonical_request_hash
        from src.core.tools.media_buy_create import _create_media_buy_impl
        from tests.helpers import seed_cached_success

        idem_key = f"drift-{uuid.uuid4().hex}"
        tenant_id = f"drift_t_{uuid.uuid4().hex[:6]}"
        principal_id = f"p_{uuid.uuid4().hex[:8]}"

        seed_principal(tenant_id, principal_id)

        class _LegacyShape(BaseModel):
            """A stored shape CreateMediaBuySuccess no longer validates (schema drift)."""

            legacy_field: str = "older-deploy"

        seed_cached_success(
            tenant_id,
            principal_id,
            idem_key,
            response_model=_LegacyShape(),
            payload_hash=canonical_request_hash(_make_request(idem_key)),
        )

        with pytest.raises(AdCPError) as exc_info:
            await _create_media_buy_impl(req=_make_request(idem_key), identity=_identity(tenant_id, principal_id))

        # A MISS means the probe RE-EXECUTED, so the request must fail the way a
        # fresh un-cached call fails: on the downstream setup this bare request
        # never satisfies. Pinning that specific code is what distinguishes a
        # miss from the fail-closed post-race path, which raises
        # SERVICE_UNAVAILABLE for this same input shape.
        #
        # The previous pair of assertions could not fail. `pytest.raises(AdCPError)`
        # already excludes PydanticValidationError (it is not in AdCPError's MRO),
        # and SERVICE_UNAVAILABLE satisfies "!= IDEMPOTENCY_CONFLICT" — so
        # inverting production from `return None` (miss) to a fail-closed raise
        # left this test, and all 42 in the replay/wire-matrix/race trio, green.
        assert exc_info.value.error_code == "BUDGET_TOO_LOW", (
            "a drifted cache row must be treated as a MISS and re-execute — the bare request then "
            "fails downstream in budget validation, which is the proof it ran. Got "
            f"{exc_info.value.error_code!r}: the probe took a different branch "
            "(SERVICE_UNAVAILABLE would mean it fail-closed instead of re-executing)"
        )

    def test_unrelated_key_does_not_replay(self, integration_db):
        """A different idempotency_key on the same principal executes fresh — and caches itself."""
        from datetime import timedelta

        from src.core.database.repositories import MediaBuyUoW
        from src.core.schemas._base import CreateMediaBuySuccess

        seeded_key = f"seeded-{uuid.uuid4().hex}"
        other_key = f"other-{uuid.uuid4().hex}"

        with MediaBuyCreateEnv() as env:
            _tenant, _principal, product, _pricing = env.setup_media_buy_data()
            env.seed_success(seeded_key, payload_hash="unrelated-hash", media_buy_id="mb_seeded_other")
            now = datetime.now(UTC)
            result = env.call_impl(
                brand={"domain": "miss-test.example.com"},
                packages=[{"product_id": product.product_id, "budget": 5000.0, "pricing_option_id": "cpm_usd_fixed"}],
                start_time=(now + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                end_time=(now + timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                po_number="MISS-1",
                idempotency_key=other_key,
            )
            tenant_id = env._tenant_id
            principal_id = env._principal_id

        assert isinstance(result.response, CreateMediaBuySuccess)
        assert result.replayed is False, "A fresh key must execute fresh — never replay"
        assert result.response.media_buy_id != "mb_seeded_other"

        # The fresh success cached its own row under other_key (pins the store path).
        with MediaBuyUoW(tenant_id) as uow:
            assert uow.idempotency_attempts is not None
            cached = uow.idempotency_attempts.find_by_key(
                principal_id=principal_id,
                idempotency_key=other_key,
            )
            assert cached is not None, "A fresh successful create must cache its response"
            assert cached.payload_hash is not None


class TestOpportunisticEviction:
    """A successful keyed create probabilistically evicts expired cache rows.

    Eviction runs in its OWN transaction after the cache write commits (a
    DELETE deadlock can never roll back the just-cached success) and only on
    ``_EVICTION_PROBABILITY`` of successes — the storage-growth bound for the
    cache without a scheduler (read-path TTL filtering already keeps replay
    correctness independent of eviction). The tests pin both sides: forced
    eviction deletes the row; suppressed eviction leaves it and the create
    is untouched.
    """

    def test_fresh_success_evicts_expired_rows(self, integration_db, monkeypatch):
        from datetime import timedelta

        from src.core.database.repositories import MediaBuyUoW
        from tests.helpers import make_active_cached_success, seed_cached_success

        monkeypatch.setattr("src.core.tools.media_buy_create._EVICTION_PROBABILITY", 1.0)

        expired_key = f"evict-{uuid.uuid4().hex}"
        fresh_key = f"fresh-{uuid.uuid4().hex}"
        seeded_at = datetime(2020, 1, 1, tzinfo=UTC)

        with MediaBuyCreateEnv() as env:
            _tenant, _principal, product, _pricing = env.setup_media_buy_data()

            seed_cached_success(
                env._tenant_id,
                env._principal_id,
                expired_key,
                response_model=make_active_cached_success("mb_expired_row"),
                payload_hash="expired-row-hash",
                ttl=timedelta(minutes=1),
                now=seeded_at,
            )

            now = datetime.now(UTC)
            result = env.call_impl(
                brand={"domain": "evict-test.example.com"},
                packages=[{"product_id": product.product_id, "budget": 5000.0, "pricing_option_id": "cpm_usd_fixed"}],
                start_time=(now + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                end_time=(now + timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                po_number="EVICT-1",
                idempotency_key=fresh_key,
            )
            assert result.status in {"completed", "submitted"}
            tenant_id = env._tenant_id
            principal_id = env._principal_id

        with MediaBuyUoW(tenant_id) as uow:
            assert uow.idempotency_attempts is not None
            # Physical-row probe: querying with a `now` from when the row was
            # still valid bypasses the read-path TTL filter, so None here means
            # the row was DELETED (evicted), not merely filtered.
            evicted = uow.idempotency_attempts.find_by_key(
                principal_id=principal_id,
                idempotency_key=expired_key,
                now=seeded_at,
            )
            assert evicted is None, "the expired row must be deleted by the opportunistic eviction"
            # The fresh success's own row was written and survives.
            fresh = uow.idempotency_attempts.find_by_key(
                principal_id=principal_id,
                idempotency_key=fresh_key,
            )
            assert fresh is not None


class TestMissingKeyRejectedAtWire:
    """Storyboard ``missing_key``: a create without idempotency_key rejects as VALIDATION_ERROR.

    The key is required at the schema boundary (AdCP 3.0.1) — the request never
    reaches ``_impl``, no buy is created, and the buyer sees the two-layer
    VALIDATION_ERROR envelope on the real wire.
    """

    def test_rest_missing_key_rejects_validation_error(self, integration_db):
        from datetime import timedelta

        from tests.harness.media_buy_create import OMIT_IDEMPOTENCY_KEY
        from tests.harness.transport import Transport
        from tests.helpers import assert_envelope_shape

        with MediaBuyCreateEnv() as env:
            _tenant, _principal, product, _pricing = env.setup_media_buy_data()
            now = datetime.now(UTC)
            result = env.call_via(
                Transport.REST,
                brand={"domain": "missing-key.example.com"},
                packages=[{"product_id": product.product_id, "budget": 5000.0, "pricing_option_id": "cpm_usd_fixed"}],
                start_time=(now + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                end_time=(now + timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                po_number="MISSING-KEY-1",
                idempotency_key=OMIT_IDEMPOTENCY_KEY,
            )

        assert result.is_error, f"Missing idempotency_key must reject, got success: {result.payload}"
        assert_envelope_shape(
            result.wire_error_envelope,
            "VALIDATION_ERROR",
            recovery="correctable",
            message_substr="idempotency_key",
        )


class TestErrorsAreNeverCached:
    """An error path writes no IdempotencyAttempt row — a retry re-executes (spec)."""

    def test_adapter_rejection_not_cached(self, integration_db):
        from datetime import timedelta

        from src.core.database.repositories import MediaBuyUoW
        from src.core.exceptions import AdCPError
        from src.core.schemas import CreateMediaBuyError, Error

        idem_key = f"err-{uuid.uuid4().hex}"

        with MediaBuyCreateEnv() as env:
            _tenant, _principal, product, _pricing = env.setup_media_buy_data()
            adapter = env.mock["adapter"].return_value
            adapter.create_media_buy.side_effect = None
            adapter.create_media_buy.return_value = CreateMediaBuyError(
                errors=[Error(code="ADAPTER_ERROR", message="adapter failure", recovery="terminal")],
                context=None,
            )
            now = datetime.now(UTC)
            # The adapter error surfaces as a failed result or a raised AdCPError —
            # either way the key must NOT be cached.
            try:
                result = env.call_impl(
                    brand={"domain": "err-test.example.com"},
                    packages=[
                        {"product_id": product.product_id, "budget": 5000.0, "pricing_option_id": "cpm_usd_fixed"}
                    ],
                    start_time=(now + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    end_time=(now + timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    po_number="ERR-1",
                    idempotency_key=idem_key,
                )
                assert result.status == "failed"
            except AdCPError:
                # The adapter failure may surface as a raised typed error rather
                # than a failed result — both are valid emission shapes here. The
                # assertion that matters is below: no cache row exists either way.
                pass
            tenant_id = env._tenant_id
            principal_id = env._principal_id

        with MediaBuyUoW(tenant_id) as uow:
            assert uow.idempotency_attempts is not None
            cached = uow.idempotency_attempts.find_by_key(
                principal_id=principal_id,
                idempotency_key=idem_key,
            )
            assert cached is None, "Errors must never be cached — a retry must re-execute"

    def test_retry_after_error_re_executes_to_fresh_success(self, integration_db):
        """Storyboard rule 5 (security.mdx#idempotency): an error caches nothing,
        so a retry with the same key re-executes to a FRESH success (replayed is
        False) — not a replay, not IDEMPOTENCY_CONFLICT.

        What this pins (mutation-verified): the error path returns ``failed`` and
        the same-key retry books a fresh buy; it reddens if an error result is
        ever routed through ``_cache_and_return`` (the fail-loud precondition
        fires). The complementary "no cache row is written on error" invariant is
        pinned directly by ``test_adapter_rejection_not_cached`` — that is the
        oracle for a cache-the-error regression; this is the fresh-re-execution half.
        """
        from datetime import timedelta

        from src.core.schemas import CreateMediaBuyError, Error
        from src.core.schemas._base import CreateMediaBuySuccess

        idem_key = f"err-retry-{uuid.uuid4().hex}"
        now = datetime.now(UTC)

        with MediaBuyCreateEnv() as env:
            _tenant, _principal, product, _pricing = env.setup_media_buy_data()
            kwargs = {
                "brand": {"domain": "err-retry.example.com"},
                "packages": [
                    {"product_id": product.product_id, "budget": 5000.0, "pricing_option_id": "cpm_usd_fixed"}
                ],
                "start_time": (now + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "end_time": (now + timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "po_number": "ERR-RETRY",
                "idempotency_key": idem_key,
            }
            adapter = env.mock["adapter"].return_value

            # First attempt: adapter rejects -> failed result, nothing cached, no
            # MediaBuy backstop (the rejection returns before the persist).
            adapter.create_media_buy.side_effect = None
            adapter.create_media_buy.return_value = CreateMediaBuyError(
                errors=[Error(code="ADAPTER_ERROR", message="adapter failure", recovery="terminal")],
                context=None,
            )
            first = env.call_impl(**dict(kwargs))
            assert first.status == "failed"

            # Restore the happy-path adapter and retry the SAME key + same payload.
            adapter.create_media_buy.return_value = None
            adapter.create_media_buy.side_effect = adapter._original_create_side_effect
            second = env.call_impl(**dict(kwargs))

        assert isinstance(second.response, CreateMediaBuySuccess), f"retry must re-execute, got {second}"
        assert second.status != "failed"
        assert second.replayed is False, "an error caches nothing — the retry is a fresh execution, not a replay"


@pytest.mark.requires_db
def test_suppressed_eviction_never_touches_the_create(integration_db, monkeypatch):
    """With eviction suppressed, the expired row survives and the buy is unaffected.

    Pins the decoupling: eviction is housekeeping OUTSIDE the cache-write
    transaction, so the create's outcome and its cached row are identical
    whether or not reclamation ran.
    """
    from datetime import timedelta

    from src.core.database.repositories import MediaBuyUoW
    from tests.harness.media_buy_create import MediaBuyCreateEnv
    from tests.helpers import make_active_cached_success, seed_cached_success

    monkeypatch.setattr("src.core.tools.media_buy_create._EVICTION_PROBABILITY", 0.0)
    expired_key = f"keep-{uuid.uuid4().hex}"
    fresh_key = f"fresh-{uuid.uuid4().hex}"
    seeded_at = datetime(2020, 1, 1, tzinfo=UTC)

    with MediaBuyCreateEnv() as env:
        _tenant, _principal, product, _pricing = env.setup_media_buy_data()
        seed_cached_success(
            env._tenant_id,
            env._principal_id,
            expired_key,
            response_model=make_active_cached_success("mb_kept_row"),
            payload_hash="kept-row-hash",
            ttl=timedelta(minutes=1),
            now=seeded_at,
        )
        now = datetime.now(UTC)
        result = env.call_impl(
            brand={"domain": "keep-test.example.com"},
            packages=[{"product_id": product.product_id, "budget": 5000.0, "pricing_option_id": "cpm_usd_fixed"}],
            start_time=(now + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            end_time=(now + timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            po_number="KEEP-1",
            idempotency_key=fresh_key,
        )
        assert result.status in {"completed", "submitted"}
        tenant_id = env._tenant_id
        principal_id = env._principal_id

    with MediaBuyUoW(tenant_id) as uow:
        assert uow.idempotency_attempts is not None
        kept = uow.idempotency_attempts.find_by_key(
            principal_id=principal_id, idempotency_key=expired_key, now=seeded_at
        )
        assert kept is not None, "suppressed eviction must leave the expired row in place"
        fresh = uow.idempotency_attempts.find_by_key(principal_id=principal_id, idempotency_key=fresh_key)
        assert fresh is not None, "the fresh success caches regardless of eviction"
