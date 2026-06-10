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

from tests.harness._base import IntegrationEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


class _FakeRequest:
    """Minimal request-like object for create_from_request that only needs model_dump and idempotency_key."""

    def __init__(self, idempotency_key: str | None = None):
        self.idempotency_key = idempotency_key

    def model_dump(self, **kwargs):
        return {"idempotency_key": self.idempotency_key, "packages": []}


class _RepoEnv(IntegrationEnv):
    """Bare integration env for repository tests — no external patches."""

    EXTERNAL_PATCHES: dict[str, str] = {}

    def get_session(self):
        """Expose session for direct repository construction."""
        self._commit_factory_data()
        return self._session


class TestIdempotencyRaceDbLevel:
    """DB-level: partial unique index enforces idempotency_key uniqueness."""

    def test_duplicate_idempotency_key_raises_integrity_error(self, integration_db):
        """Two media buys with same (tenant, principal, idempotency_key) — second raises IntegrityError."""
        from src.core.database.repositories import MediaBuyUoW
        from tests.factories import PrincipalFactory, TenantFactory

        idem_key = f"race-{uuid.uuid4().hex}"
        tenant_id = f"race_t_{uuid.uuid4().hex[:6]}"

        with _RepoEnv() as env:
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

        with _RepoEnv() as env:
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

        with _RepoEnv() as env:
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

            # This is exactly what _create_media_buy_impl does after catching:
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

        with _RepoEnv() as env:
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
        from adcp.server.helpers import valid_actions_for_status
        from adcp.types import MediaBuyStatus

        from src.core.exceptions import AdCPError
        from src.core.schemas import CreateMediaBuySuccess
        from src.core.tools.media_buy_create import _replay_after_race
        from tests.factories import PrincipalFactory, TenantFactory
        from tests.helpers import seed_cached_success

        idem_key = f"rconf-{uuid.uuid4().hex}"
        tenant_id = f"rconf_t_{uuid.uuid4().hex[:6]}"

        with _RepoEnv() as env:
            tenant = TenantFactory(tenant_id=tenant_id)
            principal = PrincipalFactory(tenant=tenant)
            principal_id = principal.principal_id
            env.get_session()

        winner_success = CreateMediaBuySuccess(
            media_buy_id="mb_race_winner",
            packages=[],
            status=MediaBuyStatus.active,
            valid_actions=valid_actions_for_status(MediaBuyStatus.active.value),
        )
        seed_cached_success(
            tenant_id, principal_id, idem_key, response_model=winner_success, payload_hash="winner-hash"
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

        with _RepoEnv() as env:
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
