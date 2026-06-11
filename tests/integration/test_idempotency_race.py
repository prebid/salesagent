"""Integration tests for idempotency_key race condition (TOCTOU).

Verifies that when two concurrent requests with the same idempotency_key
both pass the initial lookup and attempt to commit, the loser catches
IntegrityError and resolves to the winner — replaying the winner's verbatim
cached success when visible, enforcing the payload-hash conflict rule even
after the race, and degrading to MediaBuy re-derivation only when the cache
row is missing or unusable.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from tests.harness._base import BareIntegrationEnv

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


class TestBuildIdempotencyHitResult:
    """_build_idempotency_hit_result re-queries the winner and returns correct result."""

    def test_returns_winner_after_race(self, integration_db):
        """After IntegrityError, the helper finds the winner and builds a response."""
        from src.core.schemas import CreateMediaBuyResult, CreateMediaBuySuccess
        from src.core.tools.media_buy_create import _build_idempotency_hit_result
        from tests.factories import MediaBuyFactory, MediaPackageFactory, PrincipalFactory, TenantFactory

        idem_key = f"hit-{uuid.uuid4().hex}"
        tenant_id = f"hit_t_{uuid.uuid4().hex[:6]}"

        with BareIntegrationEnv() as env:
            tenant = TenantFactory(tenant_id=tenant_id)
            principal = PrincipalFactory(tenant=tenant)
            principal_id = principal.principal_id

            # Create a media buy with the idempotency_key (simulates the winner)
            buy = MediaBuyFactory(
                tenant=tenant,
                principal=principal,
                idempotency_key=idem_key,
                status="active",
            )
            buy_id = buy.media_buy_id
            MediaPackageFactory(media_buy=buy, package_id="pkg_winner_1")
            env.get_session()  # commit factory data

        # Now call the helper — it opens its own UoW
        result = _build_idempotency_hit_result(
            tenant_id=tenant_id,
            idempotency_key=idem_key,
            principal_id=principal_id,
            context=None,
        )

        assert isinstance(result, CreateMediaBuyResult)
        assert isinstance(result.response, CreateMediaBuySuccess)
        assert result.response.media_buy_id == buy_id
        assert len(result.response.packages) == 1
        assert result.response.packages[0].package_id == "pkg_winner_1"
        assert result.status == "completed"


class TestIdempotencyRaceRecovery:
    """Integration test: IntegrityError catch + _build_idempotency_hit_result recovery.

    Simulates the race condition by:
    1. Creating a media buy with idempotency_key (the winner)
    2. Attempting to create a second with the same key via UoW (triggers IntegrityError)
    3. Catching the error and verifying _build_idempotency_hit_result recovers correctly
    """

    def test_integrity_error_recovery_returns_winner(self, integration_db):
        """IntegrityError on duplicate idempotency_key is caught and returns the winner."""
        from src.core.database.repositories import MediaBuyUoW
        from src.core.schemas import CreateMediaBuyResult, CreateMediaBuySuccess
        from src.core.tools.media_buy_create import _build_idempotency_hit_result
        from tests.factories import MediaBuyFactory, MediaPackageFactory, PrincipalFactory, TenantFactory

        idem_key = f"recovery-{uuid.uuid4().hex}"
        tenant_id = f"recov_t_{uuid.uuid4().hex[:6]}"

        with BareIntegrationEnv() as env:
            tenant = TenantFactory(tenant_id=tenant_id)
            principal = PrincipalFactory(tenant=tenant)
            principal_id = principal.principal_id

            # Create the "winner" media buy with idempotency_key
            winner = MediaBuyFactory(
                tenant=tenant,
                principal=principal,
                idempotency_key=idem_key,
                status="active",
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
            assert "idempotency_key" in str(exc.orig)
            caught = True

            # This is the degraded fallback `_replay_after_race` lands on when
            # no cache row is usable:
            result = _build_idempotency_hit_result(
                tenant_id=tenant_id,
                idempotency_key=idem_key,
                principal_id=principal_id,
                context=None,
            )

            assert isinstance(result, CreateMediaBuyResult)
            assert isinstance(result.response, CreateMediaBuySuccess)
            assert result.response.media_buy_id == winner_id
            assert len(result.response.packages) == 1
            assert result.response.packages[0].package_id == "pkg_race_1"
            assert result.status == "completed"

        assert caught, "IntegrityError should have been raised by the duplicate idempotency_key"

        # Verify only ONE media buy exists for this key
        with MediaBuyUoW(tenant_id) as verify_uow:
            assert verify_uow.media_buys is not None
            existing = verify_uow.media_buys.find_by_idempotency_key(idem_key, principal_id)
            assert existing is not None
            assert existing.media_buy_id == winner_id


class TestDegradedFallbackStatus:
    """The degraded re-derivation reports an awaiting-approval buy as still in flight."""

    def test_pending_approval_buy_reports_submitted(self, integration_db):
        """A degraded re-derivation of an awaiting-approval buy must not claim completed."""
        from adcp.types import MediaBuyStatus

        from src.core.schemas import CreateMediaBuyResult, CreateMediaBuySuccess
        from src.core.tools.media_buy_create import _build_idempotency_hit_result
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

        result = _build_idempotency_hit_result(
            tenant_id=tenant_id,
            idempotency_key=idem_key,
            principal_id=principal_id,
            context=None,
        )

        assert isinstance(result, CreateMediaBuyResult)
        assert isinstance(result.response, CreateMediaBuySuccess)
        # The protocol task is still in flight — the original response said "submitted".
        assert result.status == "submitted"
        # The internal awaiting-approval state maps to the nearest spec status.
        assert result.response.status == MediaBuyStatus.pending_start


class TestRaceLoserPayloadRules:
    """_replay_after_race enforces the same payload rules as the probe."""

    def test_different_payload_after_race_conflicts(self, integration_db):
        """A race loser whose payload differs gets IDEMPOTENCY_CONFLICT, never the winner's response."""
        from src.core.exceptions import AdCPError
        from src.core.tools.media_buy_create import _replay_after_race
        from tests.factories import PrincipalFactory, TenantFactory
        from tests.helpers import make_active_cached_success, seed_cached_success

        idem_key = f"rconf-{uuid.uuid4().hex}"
        tenant_id = f"rconf_t_{uuid.uuid4().hex[:6]}"

        with BareIntegrationEnv() as env:
            tenant = TenantFactory(tenant_id=tenant_id)
            principal = PrincipalFactory(tenant=tenant)
            principal_id = principal.principal_id
            env.get_session()

        seed_cached_success(
            tenant_id,
            principal_id,
            idem_key,
            response_model=make_active_cached_success("mb_race_winner"),
            payload_hash="winner-hash",
        )

        with pytest.raises(AdCPError) as exc_info:
            _replay_after_race(
                tenant_id,
                idempotency_key=idem_key,
                principal_id=principal_id,
                account_id=None,
                context=None,
                request_hash="loser-different-hash",
            )

        assert exc_info.value.error_code == "IDEMPOTENCY_CONFLICT"
        # Read-oracle defense: the conflict must not leak the winner's response.
        assert "mb_race_winner" not in exc_info.value.message

    def test_invalid_cached_envelope_falls_back_to_rederivation(self, integration_db):
        """An unusable cache row degrades to MediaBuy re-derivation — never an internal error."""
        from pydantic import BaseModel

        from src.core.schemas import CreateMediaBuyResult, CreateMediaBuySuccess
        from src.core.tools.media_buy_create import _replay_after_race
        from tests.factories import MediaBuyFactory, MediaPackageFactory, PrincipalFactory, TenantFactory
        from tests.helpers import seed_cached_success

        idem_key = f"rinv-{uuid.uuid4().hex}"
        tenant_id = f"rinv_t_{uuid.uuid4().hex[:6]}"

        with BareIntegrationEnv() as env:
            tenant = TenantFactory(tenant_id=tenant_id)
            principal = PrincipalFactory(tenant=tenant)
            principal_id = principal.principal_id
            winner = MediaBuyFactory(
                tenant=tenant,
                principal=principal,
                idempotency_key=idem_key,
                status="active",
            )
            winner_id = winner.media_buy_id
            MediaPackageFactory(media_buy=winner, package_id="pkg_rinv_1")
            env.get_session()

        class _LegacyShape(BaseModel):
            """A stored shape CreateMediaBuySuccess no longer validates (schema drift)."""

            legacy_field: str = "older-deploy"

        seed_cached_success(tenant_id, principal_id, idem_key, response_model=_LegacyShape(), payload_hash="same-hash")

        result = _replay_after_race(
            tenant_id,
            idempotency_key=idem_key,
            principal_id=principal_id,
            account_id=None,
            context=None,
            request_hash="same-hash",
        )

        assert isinstance(result, CreateMediaBuyResult)
        assert isinstance(result.response, CreateMediaBuySuccess)
        assert result.response.media_buy_id == winner_id
        assert result.replayed is False, "A re-derived fallback is not a verbatim replay"
        # Frozen advisory: the degraded path must not rebuild the property_list
        # advisory from current capability state — it omits it.
        assert result.response.errors is None


class TestRaceSeamThroughEntrypoint:
    """The impl's except-IntegrityError → ``_replay_after_race`` seam, end-to-end.

    The components on either side of the seam are pinned individually; this
    drives the junction through the production entrypoint. A lost/expired
    cache row with a surviving MediaBuy is exactly the state a race loser
    observes before the winner's cache write commits: the probe misses, the
    buy re-executes, the ``MediaBuy.idempotency_key`` backstop fires, and the
    impl's except-branch must recover the winner — a regression in that
    wiring (constraint-string drift, argument mis-pass) surfaces here, not
    only in the direct ``_replay_after_race`` tests.
    """

    def test_retry_after_lost_cache_row_recovers_winner_via_backstop(self, integration_db):
        from datetime import timedelta

        from src.core.database.repositories import MediaBuyUoW
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
            assert isinstance(first.response, CreateMediaBuySuccess)
            winner_id = first.response.media_buy_id

            # Lose the cache row (TTL expiry / lost write) while the MediaBuy
            # survives — the race-loser state. expire_old with a far-future
            # ``now`` deletes the row through the production repository.
            with MediaBuyUoW(env._tenant_id) as uow:
                assert uow.idempotency_attempts is not None
                deleted = uow.idempotency_attempts.expire_old(now=datetime(2099, 1, 1, tzinfo=UTC))
            assert deleted >= 1, "test setup: the first call must have cached a row to lose"

            adapter_mock = env.mock["adapter"].return_value.create_media_buy
            calls_before = adapter_mock.call_count
            second = env.call_impl(**call_kwargs)
            calls_after = adapter_mock.call_count
            tenant_id = env._tenant_id
            principal_id = env._principal_id

        # The probe missed (row gone) → full re-execution → backstop fired →
        # the except-branch recovered the winner via the degraded fallback.
        assert calls_after == calls_before + 1, "the retry must re-execute (probe miss), not replay"
        assert isinstance(second.response, CreateMediaBuySuccess)
        assert second.response.media_buy_id == winner_id
        assert second.status == "completed"
        assert second.replayed is False, "the degraded re-derivation is reconstructed, never a verbatim replay"

        # Exactly one booking exists for the key — the backstop held.
        with MediaBuyUoW(tenant_id) as verify_uow:
            assert verify_uow.media_buys is not None
            existing = verify_uow.media_buys.find_by_idempotency_key(idem_key, principal_id)
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
        from src.core.exceptions import AdCPError
        from src.core.schemas._base import CreateMediaBuySuccess
        from tests.harness.media_buy_create import MediaBuyCreateEnv

        idem_key = f"degconf-{uuid.uuid4().hex}"

        with MediaBuyCreateEnv() as env:
            _tenant, _principal, product, _pricing = env.setup_media_buy_data()
            first = env.call_impl(**self._create_kwargs(product, idem_key, po_number="DEG-1"))
            assert isinstance(first.response, CreateMediaBuySuccess)
            winner_id = first.response.media_buy_id

            with MediaBuyUoW(env._tenant_id) as uow:
                assert uow.idempotency_attempts is not None
                assert uow.idempotency_attempts.expire_old(now=datetime(2099, 1, 1, tzinfo=UTC)) >= 1

            with pytest.raises(AdCPError) as exc_info:
                env.call_impl(**self._create_kwargs(product, idem_key, po_number="DEG-2-MUTATED"))

        exc = exc_info.value
        assert exc.error_code == "IDEMPOTENCY_CONFLICT"
        # Read-oracle defense: the conflict must not leak the winner's id.
        assert winner_id not in exc.message

    def test_post_ttl_retry_rejects_idempotency_expired(self, integration_db):
        """A key whose buy outlived the replay TTL rejects instead of re-deriving.

        security.mdx#idempotency rule 6: a request arriving after eviction with
        a key the seller has seen SHOULD reject with IDEMPOTENCY_EXPIRED rather
        than be silently treated as new or answered with a reconstruction the
        buyer cannot distinguish from a faithful replay.
        """
        from datetime import timedelta

        from src.core.exceptions import AdCPError
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

            with pytest.raises(AdCPError) as exc_info:
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
        from tests.harness.media_buy_create import MediaBuyCreateEnv

        idem_key = f"degacct-{uuid.uuid4().hex}"

        with MediaBuyCreateEnv() as env:
            tenant, _principal, product, _pricing = env.setup_media_buy_data()
            AccountFactory(tenant=tenant, account_id="acct_a")
            AccountFactory(tenant=tenant, account_id="acct_b")

            kwargs = self._create_kwargs(product, idem_key, po_number="ACCT-1")
            first = env.call_impl(identity=env.identity.model_copy(update={"account_id": "acct_a"}), **kwargs)
            second = env.call_impl(identity=env.identity.model_copy(update={"account_id": "acct_b"}), **kwargs)

        assert isinstance(first.response, CreateMediaBuySuccess)
        assert isinstance(second.response, CreateMediaBuySuccess)
        assert second.response.media_buy_id != first.response.media_buy_id
        assert second.replayed is False, "a different account is an independent request, never a replay"
