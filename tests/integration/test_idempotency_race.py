"""Integration tests for idempotency_key race condition (TOCTOU).

Verifies that when two concurrent requests with the same idempotency_key
both pass the initial lookup and attempt to commit, the loser catches
IntegrityError and resolves to the winner — replaying the winner's verbatim
cached success when visible, enforcing the payload-hash conflict rule even
after the race, and FAILING CLOSED (transient SERVICE_UNAVAILABLE) when the
cache row is missing or unusable: verbatim replay is byte-for-byte or
nothing, never a fabricated body.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from tests.harness._base import DEFAULT_TEST_ACCOUNT_ID, BareIntegrationEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


class _FakeRequest:
    """Minimal request-like object for create_from_request that only needs model_dump and idempotency_key."""

    def __init__(self, idempotency_key: str | None = None):
        self.idempotency_key = idempotency_key

    def model_dump(self, **kwargs):
        return {"idempotency_key": self.idempotency_key, "packages": []}


class TestIdempotencyRaceDbLevel:
    """DB-level: partial unique index enforces idempotency_key uniqueness."""

    def test_duplicate_idempotency_key_raises_integrity_error(self, integration_db):
        """Two media buys with same (tenant, principal, idempotency_key) — second raises IntegrityError."""
        from src.core.database.repositories import MediaBuyUoW
        from tests.factories import PrincipalFactory, TenantFactory

        idem_key = f"race-{uuid.uuid4().hex}"
        tenant_id = f"race_t_{uuid.uuid4().hex[:6]}"

        with BareIntegrationEnv() as env:
            tenant = TenantFactory(tenant_id=tenant_id)
            principal = PrincipalFactory(tenant=tenant)
            principal_id = principal.principal_id
            principal_name = principal.name
            env.get_session()  # commit factory data

        # Use separate UoW instances (like production code) to test the constraint
        with MediaBuyUoW(tenant_id) as uow1:
            assert uow1.media_buys is not None
            uow1.media_buys.create_from_request(
                media_buy_id=f"mb_winner_{uuid.uuid4().hex[:8]}",
                req=_FakeRequest(idempotency_key=idem_key),
                principal_id=principal_id,
                advertiser_name=principal_name,
                budget=Decimal("5000.00"),
                currency="USD",
                start_time=datetime(2026, 1, 1, tzinfo=UTC),
                end_time=datetime(2026, 12, 31, tzinfo=UTC),
                status="active",
            )
            # UoW commits on exit

        with pytest.raises(IntegrityError, match="idempotency_key"):
            with MediaBuyUoW(tenant_id) as uow2:
                assert uow2.media_buys is not None
                uow2.media_buys.create_from_request(
                    media_buy_id=f"mb_loser_{uuid.uuid4().hex[:8]}",
                    req=_FakeRequest(idempotency_key=idem_key),
                    principal_id=principal_id,
                    advertiser_name=principal_name,
                    budget=Decimal("5000.00"),
                    currency="USD",
                    start_time=datetime(2026, 1, 1, tzinfo=UTC),
                    end_time=datetime(2026, 12, 31, tzinfo=UTC),
                    status="active",
                )


class TestDegradedReplayFailsClosed:
    """No usable cache row ⇒ transient rejection, never a fabricated body.

    Verbatim replay is byte-for-byte or nothing (the spec's fail-closed rule):
    a reconstruction the buyer cannot distinguish from a faithful replay is the
    named failure mode. The buyer's retry replays verbatim once the winner's
    cache write lands.
    """

    def test_missing_cache_row_rejects_transient(self, integration_db):
        """A same-key buy with no cache row rejects SERVICE_UNAVAILABLE + retry_after."""
        from src.core.exceptions import AdCPSalesAgentError
        from src.core.tools.media_buy_create import _raise_degraded_replay_outcome
        from tests.factories import MediaBuyFactory, MediaPackageFactory, PrincipalFactory, TenantFactory

        idem_key = f"hit-{uuid.uuid4().hex}"
        tenant_id = f"hit_t_{uuid.uuid4().hex[:6]}"

        with BareIntegrationEnv() as env:
            tenant = TenantFactory(tenant_id=tenant_id)
            principal = PrincipalFactory(tenant=tenant)
            principal_id = principal.principal_id

            # The committed winner whose cache write has not landed.
            buy = MediaBuyFactory(
                tenant=tenant,
                principal=principal,
                idempotency_key=idem_key,
                status="active",
            )
            MediaPackageFactory(media_buy=buy, package_id="pkg_winner_1")
            env.get_session()  # commit factory data

        with pytest.raises(AdCPSalesAgentError) as exc_info:
            _raise_degraded_replay_outcome(
                tenant_id,
                idem_key,
                principal_id,
                account_id=DEFAULT_TEST_ACCOUNT_ID,
            )

        exc = exc_info.value
        assert exc.error_code == "SERVICE_UNAVAILABLE"
        assert exc.retry_after >= 1


class TestDegradedReplayImpossibleState:
    """No same-key buy at all ⇒ terminal, and distinguishably so.

    This is the guard the sibling class does NOT cover: there, a same-key buy
    exists and only its cache row is missing, which is genuinely transient (the
    winner's write is in flight). Here there is no such buy, so the race
    "resolved" into a state that cannot resolve itself, and a retry re-runs the
    same impossible lookup forever.

    The two outcomes must therefore not collapse onto one wire answer. That is
    what pins the CLASS: the verdict is expressed by choosing
    ``CONFIGURATION_ERROR``, whose pinned enumMetadata recovery IS ``terminal``
    ("surface to a human at the seller ... MUST NOT auto-retry"), rather than by
    hand-typing ``recovery="terminal"`` onto a code the pin calls correctable —
    which is what this site used to do.
    """

    def test_no_same_key_buy_rejects_terminal(self, integration_db):
        """No buy carries the key ⇒ CONFIGURATION_ERROR + terminal, not the transient sibling."""
        from src.core.exceptions import AdCPSalesAgentError
        from src.core.tools.media_buy_create import _raise_degraded_replay_outcome
        from tests.factories import PrincipalFactory, TenantFactory

        idem_key = f"missing-{uuid.uuid4().hex}"
        tenant_id = f"miss_t_{uuid.uuid4().hex[:6]}"

        with BareIntegrationEnv() as env:
            tenant = TenantFactory(tenant_id=tenant_id)
            principal = PrincipalFactory(tenant=tenant)
            principal_id = principal.principal_id
            # Deliberately NO MediaBuy carrying idem_key.
            env.get_session()  # commit factory data

        with pytest.raises(AdCPSalesAgentError) as exc_info:
            _raise_degraded_replay_outcome(
                tenant_id,
                idem_key,
                principal_id,
                account_id=DEFAULT_TEST_ACCOUNT_ID,
            )

        exc = exc_info.value
        assert exc.error_code == "CONFIGURATION_ERROR", (
            f"an impossible post-race state is terminal, but got {exc.error_code!r} — if this is now "
            "SERVICE_UNAVAILABLE the guard has collapsed onto its transient sibling and the buyer is "
            "being told to retry a state that cannot resolve"
        )
        assert exc.recovery == "terminal"


class TestIdempotencyRaceRecovery:
    """Integration test: IntegrityError catch + fail-closed degraded outcome.

    Simulates the race condition by:
    1. Creating a media buy with idempotency_key (the winner)
    2. Attempting to create a second with the same key via UoW (triggers IntegrityError)
    3. Catching the error and verifying the loser fails closed (no cache row yet)
       while exactly one booking survives
    """

    def test_integrity_error_loser_fails_closed_single_booking(self, integration_db):
        """The loser gets a transient rejection; the winner's booking is untouched."""
        from src.core.database.repositories import MediaBuyUoW
        from src.core.exceptions import AdCPSalesAgentError
        from src.core.tools.media_buy_create import _raise_degraded_replay_outcome
        from tests.factories import MediaBuyFactory, MediaPackageFactory, PrincipalFactory, TenantFactory

        idem_key = f"recovery-{uuid.uuid4().hex}"
        tenant_id = f"recov_t_{uuid.uuid4().hex[:6]}"

        with BareIntegrationEnv() as env:
            tenant = TenantFactory(tenant_id=tenant_id)
            principal = PrincipalFactory(tenant=tenant)
            principal_id = principal.principal_id

            # Create the "winner" media buy with idempotency_key.
            # account_id=None DELIBERATELY, against the factory's default: this test grades
            # the DB unique constraint on the key, and the loser below is written by
            # ``create_from_request``, which carries no account. Two rows in two different
            # account scopes do not collide, so the duplicate would simply insert and the
            # IntegrityError this test exists for would never fire.
            winner = MediaBuyFactory(
                tenant=tenant,
                principal=principal,
                idempotency_key=idem_key,
                status="active",
                account_id=None,
            )
            winner_id = winner.media_buy_id
            MediaPackageFactory(media_buy=winner, package_id="pkg_race_1")
            env.get_session()  # commit

        # Now simulate the loser: attempt to create a duplicate via UoW
        caught = False
        try:
            with MediaBuyUoW(tenant_id) as uow:
                assert uow.media_buys is not None
                uow.media_buys.create_from_request(
                    media_buy_id=f"mb_loser_{uuid.uuid4().hex[:8]}",
                    req=_FakeRequest(idempotency_key=idem_key),
                    principal_id=principal_id,
                    advertiser_name="Loser",
                    budget=Decimal("5000.00"),
                    currency="USD",
                    start_time=datetime(2026, 1, 1, tzinfo=UTC),
                    end_time=datetime(2026, 12, 31, tzinfo=UTC),
                    status="active",
                )
                # UoW __exit__ calls commit — IntegrityError fires here
        except IntegrityError as exc:
            caught = True

            # This is the degraded outcome `_replay_after_race` lands on when
            # no cache row is usable — fail closed, never a fabricated body:
            with pytest.raises(AdCPSalesAgentError) as exc_info:
                _raise_degraded_replay_outcome(
                    tenant_id,
                    idem_key,
                    principal_id,
                    # Matches the winner's scope above, which is deliberately account-less.
                    account_id=None,
                )
            assert exc_info.value.error_code == "SERVICE_UNAVAILABLE"

        assert caught, "IntegrityError should have been raised by the duplicate idempotency_key"

        # Verify only ONE media buy exists for this key
        with MediaBuyUoW(tenant_id) as verify_uow:
            assert verify_uow.media_buys is not None
            # None, matching the deliberately account-less winner seeded above.
            existing = verify_uow.media_buys.find_by_idempotency_key(idem_key, principal_id, account_id=None)
            assert existing is not None
            assert existing.media_buy_id == winner_id


class TestDegradedFallbackStatus:
    """An awaiting-approval buy also fails closed — no fabricated submitted body."""

    def test_pending_approval_buy_fails_closed_too(self, integration_db):
        """The original submitted response (task_id and all) is unrecoverable here —
        fabricating one would hand the buyer an envelope that never existed."""
        from src.core.exceptions import AdCPSalesAgentError
        from src.core.tools.media_buy_create import _raise_degraded_replay_outcome
        from tests.factories import MediaBuyFactory, PrincipalFactory, TenantFactory

        idem_key = f"pend-{uuid.uuid4().hex}"
        tenant_id = f"pend_t_{uuid.uuid4().hex[:6]}"

        with BareIntegrationEnv() as env:
            tenant = TenantFactory(tenant_id=tenant_id)
            principal = PrincipalFactory(tenant=tenant)
            principal_id = principal.principal_id
            MediaBuyFactory(
                tenant=tenant,
                principal=principal,
                idempotency_key=idem_key,
                status="pending_approval",
            )
            env.get_session()

        with pytest.raises(AdCPSalesAgentError) as exc_info:
            _raise_degraded_replay_outcome(
                tenant_id,
                idem_key,
                principal_id,
                account_id=DEFAULT_TEST_ACCOUNT_ID,
            )

        assert exc_info.value.error_code == "SERVICE_UNAVAILABLE"


class TestRaceLoserPayloadRules:
    """Reusing a key with a different payload is refused, at the boundary that owns the rule.

    This used to drive ``_replay_after_race``, the create-path helper that resolved a race
    loser to the winner's cached response and applied the payload rule a second time on the
    way. Replay moved to ``src/core/tools/_boundary.py``, which probes BEFORE any transport
    reaches the implementation, so the conflict is decided there for every tool at once and
    the loser of an actual commit race simply retries into it.
    """

    def test_different_payload_under_a_stored_key_conflicts(self, integration_db):
        """A create carrying a seen key and a different payload never sees the stored response."""
        from datetime import timedelta

        from src.core.exceptions import AdCPSalesAgentError
        from tests.harness.media_buy_create import MediaBuyCreateEnv
        from tests.helpers import make_active_cached_success, seed_cached_success

        idem_key = f"rconf-{uuid.uuid4().hex[:12]}00000000"

        with MediaBuyCreateEnv() as env:
            _tenant, _principal, product, _pricing = env.setup_media_buy_data()
            seed_cached_success(
                env._tenant_id,
                env._principal_id,
                idem_key,
                response_model=make_active_cached_success("mb_race_winner"),
                # A hash no request canonicalises to, so whatever the create below produces
                # differs from it -- which is the condition under test.
                payload_hash="winner-hash",
                account_id=DEFAULT_TEST_ACCOUNT_ID,
            )

            now = datetime.now(UTC)
            with pytest.raises(AdCPSalesAgentError) as exc_info:
                env.call_impl(
                    brand={"domain": "conflict-test.example.com"},
                    packages=[
                        {"product_id": product.product_id, "budget": 5000.0, "pricing_option_id": "cpm_usd_fixed"}
                    ],
                    start_time=(now + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    end_time=(now + timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    idempotency_key=idem_key,
                )

        assert exc_info.value.error_code == "IDEMPOTENCY_CONFLICT"
        # Read-oracle defense: the conflict must not leak the winner's response.
        assert "mb_race_winner" not in str(exc_info.value)


class TestRaceSeamThroughEntrypoint:
    """The impl's except-IntegrityError → ``_replay_after_race`` seam, end-to-end.

    The components on either side of the seam are pinned individually; this
    drives the junction through the production entrypoint. A lost/expired
    cache row with a surviving MediaBuy is exactly the state a race loser
    observes before the winner's cache write commits: the probe misses, the
    buy re-executes, the ``MediaBuy.idempotency_key`` backstop fires, and the
    impl's except-branch must fail closed (transient) — a regression in that
    wiring (constraint-string drift, argument mis-pass) surfaces here, not
    only in the direct ``_replay_after_race`` tests.
    """

    def test_retry_after_lost_cache_row_fails_closed_via_backstop(self, integration_db):
        from datetime import timedelta

        from src.core.database.repositories import MediaBuyUoW
        from src.core.exceptions import AdCPSalesAgentError
        from src.core.schemas._base import CreateMediaBuySuccess
        from tests.harness.media_buy_create import MediaBuyCreateEnv

        idem_key = f"seam-{uuid.uuid4().hex}"

        with MediaBuyCreateEnv() as env:
            _tenant, _principal, product, _pricing = env.setup_media_buy_data()
            now = datetime.now(UTC)
            call_kwargs = {
                "brand": {"domain": "seam-test.example.com"},
                "packages": [
                    {"product_id": product.product_id, "budget": 5000.0, "pricing_option_id": "cpm_usd_fixed"}
                ],
                "start_time": (now + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "end_time": (now + timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "po_number": "SEAM-1",
                "idempotency_key": idem_key,
            }
            first = env.call_impl(**call_kwargs)
            assert isinstance(first, CreateMediaBuySuccess)
            winner_id = first.media_buy_id

            # Lose the cache row (TTL expiry / lost write) while the MediaBuy
            # survives — the race-loser state. expire_old with a far-future
            # ``now`` deletes the row through the production repository.
            with MediaBuyUoW(env._tenant_id) as uow:
                assert uow.idempotency_attempts is not None
                deleted = uow.idempotency_attempts.expire_old(now=datetime(2099, 1, 1, tzinfo=UTC))
            assert deleted >= 1, "test setup: the first call must have cached a row to lose"

            adapter_mock = env.mock["adapter"].return_value.create_media_buy
            calls_before = adapter_mock.call_count
            with pytest.raises(AdCPSalesAgentError) as exc_info:
                env.call_impl(**call_kwargs)
            calls_after = adapter_mock.call_count
            tenant_id = env._tenant_id
            principal_id = env._principal_id

        # The probe missed (row gone) → full re-execution → backstop fired →
        # the except-branch failed closed instead of fabricating a body.
        assert calls_after == calls_before + 1, "the retry must re-execute (probe miss), not replay"
        assert exc_info.value.error_code == "SERVICE_UNAVAILABLE"

        # Exactly one booking exists for the key — the backstop held.
        with MediaBuyUoW(tenant_id) as verify_uow:
            assert verify_uow.media_buys is not None
            existing = verify_uow.media_buys.find_by_idempotency_key(
                idem_key, principal_id, account_id=DEFAULT_TEST_ACCOUNT_ID
            )
            assert existing is not None
            assert existing.media_buy_id == winner_id


class TestDegradedFallbackScopeRules:
    """Account scoping, payload-conflict, and TTL-expiry rules on the degraded fallback.

    The verbatim cache is the authoritative replay path; these pin what happens
    when it has no usable row and the ``MediaBuy.idempotency_key`` backstop is
    the only signal left. Deterministic recipes — no concurrency: a missing
    cache row plus a surviving buy IS the race-loser state.
    """

    @staticmethod
    def _create_kwargs(product, idem_key, *, po_number):
        from datetime import timedelta

        now = datetime.now(UTC)
        return {
            "brand": {"domain": "degraded-test.example.com"},
            "packages": [{"product_id": product.product_id, "budget": 5000.0, "pricing_option_id": "cpm_usd_fixed"}],
            "start_time": (now + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end_time": (now + timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "po_number": po_number,
            "idempotency_key": idem_key,
        }

    def test_degraded_path_conflicts_on_mutated_payload(self, integration_db):
        """Same key + different canonical payload conflicts even with the cache row gone.

        The buy's stored ``payload_hash`` (written at create time) carries the
        conflict signal the evicted cache row can no longer provide — the retry
        must never be resolved to a buy describing a different request.
        """
        from src.core.database.repositories import MediaBuyUoW
        from src.core.exceptions import AdCPSalesAgentError
        from src.core.schemas._base import CreateMediaBuySuccess
        from tests.harness.media_buy_create import MediaBuyCreateEnv

        idem_key = f"degconf-{uuid.uuid4().hex}"

        with MediaBuyCreateEnv() as env:
            _tenant, _principal, product, _pricing = env.setup_media_buy_data()
            first = env.call_impl(**self._create_kwargs(product, idem_key, po_number="DEG-1"))
            assert isinstance(first, CreateMediaBuySuccess)
            winner_id = first.media_buy_id

            with MediaBuyUoW(env._tenant_id) as uow:
                assert uow.idempotency_attempts is not None
                assert uow.idempotency_attempts.expire_old(now=datetime(2099, 1, 1, tzinfo=UTC)) >= 1

            with pytest.raises(AdCPSalesAgentError) as exc_info:
                env.call_impl(**self._create_kwargs(product, idem_key, po_number="DEG-2-MUTATED"))

        exc = exc_info.value
        assert exc.error_code == "IDEMPOTENCY_CONFLICT"
        # No leak assert on the message: it is a read-only CODE_TABLE constant,
        # so the winner's id (or any request data) has no route into it.

    def test_post_ttl_retry_rejects_idempotency_expired(self, integration_db):
        """A key whose buy outlived the replay TTL rejects instead of re-deriving.

        security.mdx#idempotency rule 6: a request arriving after eviction with
        a key the seller has seen SHOULD reject with IDEMPOTENCY_EXPIRED rather
        than be silently treated as new or answered with a reconstruction the
        buyer cannot distinguish from a faithful replay.
        """
        from datetime import timedelta

        from src.core.exceptions import AdCPSalesAgentError
        from tests.factories import MediaBuyFactory
        from tests.harness.media_buy_create import MediaBuyCreateEnv

        idem_key = f"degexp-{uuid.uuid4().hex}"

        with MediaBuyCreateEnv() as env:
            tenant, principal, product, _pricing = env.setup_media_buy_data()
            # The buy that outlived the advertised TTL; its cache row is long evicted.
            MediaBuyFactory(
                tenant=tenant,
                principal=principal,
                idempotency_key=idem_key,
                status="active",
                created_at=datetime.now(UTC) - timedelta(days=2),
            )

            with pytest.raises(AdCPSalesAgentError) as exc_info:
                env.call_impl(**self._create_kwargs(product, idem_key, po_number="EXP-1"))

        assert exc_info.value.error_code == "IDEMPOTENCY_EXPIRED"

    def test_same_key_different_account_books_independently(self, integration_db):
        """The idempotency scope is (agent, account, key): accounts never collide.

        Pins the widened backstop index end-to-end — before account_id joined
        the unique tuple, the second account's create raised IntegrityError and
        could be resolved to the first account's buy.
        """
        from src.core.schemas._base import CreateMediaBuySuccess
        from tests.factories import AccountFactory
        from tests.factories.account import AgentAccountAccessFactory
        from tests.harness.media_buy_create import MediaBuyCreateEnv

        idem_key = f"degacct-{uuid.uuid4().hex}"

        with MediaBuyCreateEnv() as env:
            tenant, _principal, product, _pricing = env.setup_media_buy_data()
            # Access GRANTED, not just the rows created: enrich_identity_with_account
            # resolves through the access scope, so an account the agent cannot reach comes
            # back as AdCPAuthorizationError instead of the independent booking under test.
            # It did not matter while the request named no account and resolution never ran.
            for acct_id in ("acct_a", "acct_b"):
                acct = AccountFactory(tenant=tenant, account_id=acct_id)
                AgentAccountAccessFactory(tenant_id=tenant.tenant_id, principal=_principal, account=acct)

            # Named on the REQUEST, not patched onto the identity. call_impl runs the same
            # enrich_identity_with_account the transport boundary runs, so it RESOLVES
            # req.account onto the identity -- a hand-set identity.account_id beside it is
            # overwritten, and both calls would land in one scope and replay. Setting it
            # where a buyer sets it is also what makes this test grade the real path.
            kwargs = self._create_kwargs(product, idem_key, po_number="ACCT-1")
            first = env.call_impl(account={"account_id": "acct_a"}, **kwargs)
            second = env.call_impl(account={"account_id": "acct_b"}, **kwargs)

        assert isinstance(first, CreateMediaBuySuccess)
        assert isinstance(second, CreateMediaBuySuccess)
        assert second.media_buy_id != first.media_buy_id
        assert second.replayed is False, "a different account is an independent request, never a replay"


class TestDegradedExpiryAnchoring:
    """The degraded expiry boundary is the cache row's STORED expires_at, not MediaBuy.created_at."""

    def test_uses_stored_expires_at_not_media_buy_created_at(self, integration_db):
        """A present-but-expired cache row drives IDEMPOTENCY_EXPIRED even when the backstop is fresh.

        The cache row and the MediaBuy backstop carry independent timestamps.
        Recomputing the window from MediaBuy.created_at (just created here) would
        call the closed window OPEN; anchoring on the stored expires_at — the
        same authority the probe filters on — rejects correctly.
        """
        from datetime import timedelta

        from src.core.exceptions import AdCPSalesAgentError
        from src.core.tools.media_buy_create import _raise_degraded_replay_outcome
        from tests.helpers import make_active_cached_success, seed_cached_success, seed_media_buy

        idem_key = f"degstore-{uuid.uuid4().hex}"
        tenant_id = f"degstore_t_{uuid.uuid4().hex[:6]}"
        principal_id = f"p_{uuid.uuid4().hex[:8]}"

        # Backstop buy created NOW — a MediaBuy.created_at recompute calls the window OPEN.
        seed_media_buy(tenant_id, principal_id, "mb_degstore_backstop", idempotency_key=idem_key)

        # ...but the cache row's stored expires_at is already in the past.
        seed_cached_success(
            tenant_id,
            principal_id,
            idem_key,
            response_model=make_active_cached_success("mb_degstore"),
            payload_hash="store-hash",
            ttl=timedelta(hours=1),
            now=datetime.now(UTC) - timedelta(hours=2),
            account_id=DEFAULT_TEST_ACCOUNT_ID,
        )

        with pytest.raises(AdCPSalesAgentError) as exc_info:
            _raise_degraded_replay_outcome(
                tenant_id,
                idem_key,
                principal_id,
                account_id=DEFAULT_TEST_ACCOUNT_ID,
            )

        assert exc_info.value.error_code == "IDEMPOTENCY_EXPIRED"
