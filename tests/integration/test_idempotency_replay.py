"""Integration tests for verbatim SUCCESS replay at the transport boundary.

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

from tests.harness._base import DEFAULT_TEST_ACCOUNT_ID
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
        account_id=DEFAULT_TEST_ACCOUNT_ID,
    )


def _make_request(idempotency_key, *, po_number="REPLAY-1"):
    from src.core.schemas import CreateMediaBuyRequest

    return CreateMediaBuyRequest(
        # The account the seeds create. The boundary resolves this reference before probing,
        # and the cache scope is (agent, account, key), so naming an unseeded account would
        # fail resolution before any of these tests reached their subject.
        account={"account_id": DEFAULT_TEST_ACCOUNT_ID},
        brand={"domain": "replay-test.example.com"},
        packages=[{"product_id": "prod_1", "budget": 1000, "pricing_option_id": "po_1"}],
        start_time=datetime(2026, 6, 1, tzinfo=UTC),
        end_time=datetime(2026, 6, 30, tzinfo=UTC),
        po_number=po_number,
        idempotency_key=idempotency_key,
    )


def _headers(tenant_id, principal_id):
    """The headers the retry arrives with, not an identity.

    ``invoke_tool`` takes the request's HEADERS: the resolver is their one reader, and it
    resolves the principal, the tenant and the account the request names — including the
    grant that puts the cache probe in the same (agent, account, key) scope the seeded row
    sits in. A test that built its own identity and handed it over skipped that step. The
    credential is the one the factory principal answers to (``plaintext_token_for``).
    """
    from tests.factories.principal import plaintext_token_for
    from tests.helpers.credentials import credential_headers

    return credential_headers(token=plaintext_token_for(principal_id), tenant=tenant_id)


class TestImplReplaysCachedSuccess:
    """The boundary replays the cached success verbatim on key match."""

    async def test_cached_success_replayed_verbatim(self, integration_db):
        from src.core.idempotency_canonical import canonical_request_hash
        from src.core.resolved_identity import TransportProtocol
        from src.core.schemas._base import CreateMediaBuyResult, CreateMediaBuySuccess
        from src.core.tools._boundary import invoke_tool

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

        result = await invoke_tool(
            "create_media_buy",
            _make_request(idem_key),
            _headers(tenant_id, principal_id),
            TransportProtocol.MCP,
        )

        assert isinstance(result, CreateMediaBuyResult)
        assert isinstance(result, CreateMediaBuySuccess)
        assert result.media_buy_id == "mb_original_123"
        assert result.status == "completed"
        assert result.replayed is True  # top-level replay marker, injected at replay time

    async def test_different_payload_same_key_raises_conflict(self, integration_db):
        from src.core.exceptions import AdCPIdempotencyConflictError
        from src.core.resolved_identity import TransportProtocol
        from src.core.tools._boundary import invoke_tool
        from tests.helpers.envelope_assertions import raises_adcp

        idem_key = f"conflict-{uuid.uuid4().hex}"
        tenant_id = f"conflict_t_{uuid.uuid4().hex[:6]}"
        principal_id = f"p_{uuid.uuid4().hex[:8]}"

        seed_principal(tenant_id, principal_id)
        # Stored hash will NOT match the request's canonical hash → conflict.
        _seed_success(tenant_id, principal_id, idem_key, media_buy_id="mb_first", payload_hash="non-matching-hash")

        # The boundary answers a failure with a response and raises AdcpFailure carrying
        # it, so the caller grades the buyer-facing CODE rather than the typed exception
        # the raise site built (tests/CLAUDE.md § Error verification policy).
        with raises_adcp(AdCPIdempotencyConflictError):
            await invoke_tool(
                "create_media_buy",
                _make_request(idem_key),
                _headers(tenant_id, principal_id),
                TransportProtocol.MCP,
            )
        # Read-oracle defense: the conflict must not leak the cached payload/id.

    async def test_invalid_cached_envelope_treated_as_miss(self, integration_db):
        """A cache row that no longer validates is a MISS — the retry re-executes.

        Pins the schema-drift guard: a stored envelope from an older deploy that no
        longer validates must never surface as an internal error on a retry of a
        previously-successful call. The probe treats it as absent and re-executes
        (here the bare request then fails downstream on its own account — what matters is
        it is neither a replay, a conflict, nor a raw ValidationError).
        """
        from pydantic import ValidationError as PydanticValidationError

        from src.core.exceptions import AdcpFailure
        from src.core.idempotency_canonical import canonical_request_hash
        from src.core.resolved_identity import TransportProtocol
        from src.core.tools._boundary import invoke_tool
        from tests.helpers import LegacyCachedShape, seed_cached_success

        idem_key = f"drift-{uuid.uuid4().hex}"
        tenant_id = f"drift_t_{uuid.uuid4().hex[:6]}"
        principal_id = f"p_{uuid.uuid4().hex[:8]}"

        seed_principal(tenant_id, principal_id)

        seed_cached_success(
            tenant_id,
            principal_id,
            idem_key,
            response_model=LegacyCachedShape(),
            payload_hash=canonical_request_hash(_make_request(idem_key)),
            account_id=DEFAULT_TEST_ACCOUNT_ID,
        )

        with pytest.raises(AdcpFailure) as exc_info:
            await invoke_tool(
                "create_media_buy",
                _make_request(idem_key),
                _headers(tenant_id, principal_id),
                TransportProtocol.MCP,
            )

        failure = exc_info.value.response
        assert failure.adcp_error is not None
        # Re-executed, not replayed and not refused on the key: the failure is the fresh
        # call's own. A raw pydantic ValidationError escaping the drifted envelope is the
        # regression this pins, so the raise chain must not carry one either.
        assert failure.adcp_error.code != "IDEMPOTENCY_CONFLICT"
        assert not isinstance(exc_info.value.__cause__, PydanticValidationError)

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

        assert isinstance(result, CreateMediaBuySuccess)
        assert result.replayed is False, "A fresh key must execute fresh — never replay"
        assert result.media_buy_id != "mb_seeded_other"

        # The fresh success cached its own row under other_key (pins the store path).
        with MediaBuyUoW(tenant_id) as uow:
            assert uow.idempotency_attempts is not None
            cached = uow.idempotency_attempts.find_by_key(
                principal_id=principal_id,
                idempotency_key=other_key,
                account_id=DEFAULT_TEST_ACCOUNT_ID,
            )
            assert cached is not None, "A fresh successful create must cache its response"
            assert cached.payload_hash is not None


class TestOpportunisticEviction:
    """A successful keyed create probabilistically evicts expired cache rows.

    Eviction runs in its OWN transaction after the cache write commits (a
    DELETE deadlock can never roll back the just-cached success) and only on
    ``EVICTION_PROBABILITY`` of successes — the storage-growth bound for the
    cache without a scheduler (read-path TTL filtering already keeps replay
    correctness independent of eviction). The tests pin both sides: forced
    eviction deletes the row; suppressed eviction leaves it and the create
    is untouched.
    """

    def test_fresh_success_evicts_expired_rows(self, integration_db, monkeypatch):
        from datetime import timedelta

        from src.core.database.repositories import MediaBuyUoW
        from tests.helpers import make_active_cached_success, seed_cached_success

        monkeypatch.setattr("src.core.idempotency_replay.EVICTION_PROBABILITY", 1.0)

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
                account_id=DEFAULT_TEST_ACCOUNT_ID,
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
                account_id=DEFAULT_TEST_ACCOUNT_ID,
            )
            assert evicted is None, "the expired row must be deleted by the opportunistic eviction"
            # The fresh success's own row was written and survives.
            fresh = uow.idempotency_attempts.find_by_key(
                principal_id=principal_id,
                idempotency_key=fresh_key,
                account_id=DEFAULT_TEST_ACCOUNT_ID,
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
        result.assert_wire_error(
            "INVALID_REQUEST",
            recovery="correctable",
        )


class TestErrorsAreNeverCached:
    """An error path writes no IdempotencyAttempt row — a retry re-executes (spec)."""

    def test_adapter_rejection_not_cached(self, integration_db):
        from datetime import timedelta

        from src.core.database.repositories import MediaBuyUoW
        from src.core.exceptions import AdCPRateLimitError
        from tests.helpers.envelope_assertions import raises_adcp

        idem_key = f"err-{uuid.uuid4().hex}"

        with MediaBuyCreateEnv() as env:
            _tenant, _principal, product, _pricing = env.setup_media_buy_data()
            adapter = env.mock["adapter"].return_value
            # An adapter fails by RAISING. It cannot fail by returning an error: the method
            # is annotated ``-> AdapterCreateResult``, which is a plain success carrier
            # (``media_buy_id: str`` required, ``extra="forbid"``) with no error member, so
            # a returned ``CreateMediaBuyError`` is a shape no deployment can produce. This
            # used to inject exactly that, and production's success-path log line then read
            # ``.media_buy_id`` off it and raised AttributeError -- caught by the tool's
            # catch-all and reported as an adapter failure, so the test passed while grading
            # a defensive branch reacting to an impossible value.
            adapter.create_media_buy.side_effect = AdCPRateLimitError(retry_after=30)
            now = datetime.now(UTC)
            # ONE outcome, pinned. This was a try/except that accepted either a failed
            # result or a raised error as "both valid emission shapes" -- a When that cannot
            # fail, so it could not have told us the injection was impossible.
            with raises_adcp(AdCPRateLimitError):
                env.call_impl(
                    brand={"domain": "err-test.example.com"},
                    packages=[
                        {"product_id": product.product_id, "budget": 5000.0, "pricing_option_id": "cpm_usd_fixed"}
                    ],
                    start_time=(now + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    end_time=(now + timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    po_number="ERR-1",
                    idempotency_key=idem_key,
                )
            tenant_id = env._tenant_id
            principal_id = env._principal_id

        with MediaBuyUoW(tenant_id) as uow:
            assert uow.idempotency_attempts is not None
            cached = uow.idempotency_attempts.find_by_key(
                principal_id=principal_id,
                idempotency_key=idem_key,
                account_id=DEFAULT_TEST_ACCOUNT_ID,
            )
            assert cached is None, "Errors must never be cached — a retry must re-execute"

    def test_retry_after_error_re_executes_to_fresh_success(self, integration_db):
        """Storyboard rule 5 (security.mdx#idempotency): an error caches nothing,
        so a retry with the same key re-executes to a FRESH success (replayed is
        False) — not a replay, not IDEMPOTENCY_CONFLICT.

        What this pins: the error path RAISES and the same-key retry books a fresh
        buy. The rejection used to return a result carrying ``status="failed"``,
        which is why the boundary once inspected a returned status before caching;
        an adapter can only raise now -- its method is annotated
        ``-> AdapterCreateResult``, which has no error member -- so "an error caches
        nothing" holds because a raise never reaches the save. The complementary "no cache
        row is written on error" invariant is pinned directly by
        ``test_adapter_rejection_not_cached`` — that is the oracle for a
        cache-the-error regression; this is the fresh-re-execution half.
        """
        from datetime import timedelta

        from src.core.exceptions import AdCPRateLimitError
        from src.core.schemas._base import CreateMediaBuySuccess
        from tests.helpers.envelope_assertions import raises_adcp

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

            # First attempt: adapter rejects -> RAISES, nothing cached, no MediaBuy
            # backstop (the rejection raises before the persist).
            #
            # By raising, which is the only way an adapter can fail: the method is annotated
            # ``-> AdapterCreateResult``, a plain success carrier with no error member, so
            # the returned ``CreateMediaBuyError`` this used to inject was a shape no
            # deployment produces. ``AdCPAdapterError`` was then reached only because
            # production's success-path log line read ``.media_buy_id`` off it, raised
            # AttributeError, and the tool's catch-all relabelled that as an adapter fault.
            adapter.create_media_buy.side_effect = AdCPRateLimitError(retry_after=30)
            with raises_adcp(AdCPRateLimitError):
                env.call_impl(**dict(kwargs))

            # Restore the happy-path adapter and retry the SAME key + same payload.
            adapter.create_media_buy.side_effect = adapter._original_create_side_effect
            second = env.call_impl(**dict(kwargs))

        assert isinstance(second, CreateMediaBuySuccess), f"retry must re-execute, got {second}"
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

    monkeypatch.setattr("src.core.idempotency_replay.EVICTION_PROBABILITY", 0.0)
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
            account_id=DEFAULT_TEST_ACCOUNT_ID,
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
            principal_id=principal_id, idempotency_key=expired_key, now=seeded_at, account_id=DEFAULT_TEST_ACCOUNT_ID
        )
        assert kept is not None, "suppressed eviction must leave the expired row in place"
        fresh = uow.idempotency_attempts.find_by_key(
            principal_id=principal_id, idempotency_key=fresh_key, account_id=DEFAULT_TEST_ACCOUNT_ID
        )
        assert fresh is not None, "the fresh success caches regardless of eviction"
