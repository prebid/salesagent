"""Integration tests for MediaBuyRepository write methods.

Tests write operations against real PostgreSQL to verify:
- Roundtrip: write -> read back -> verify fields match
- Tenant isolation: writes scoped to repository's tenant
- Edge cases: duplicate creates, updates to nonexistent records, tenant mismatches

beads: salesagent-dyb6
"""

import threading
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import cast

import pytest

from src.core.database.database_session import get_db_session
from src.core.database.models import Principal, Tenant
from src.core.database.repositories import MediaBuyRepository, MediaBuyUoW
from tests.helpers.media_buy import read_back_media_buy
from tests.integration.conftest import cleanup_tenant, make_media_buy, make_package

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

_MISSING = object()


def _run_concurrently[T](
    workers: Sequence[Callable[[threading.Barrier], T]],
    *,
    thread_name_prefix: str,
    join_timeout: float = 60,
) -> list[T]:
    """Run synchronized workers and surface hangs/errors in the main thread."""
    barrier = threading.Barrier(len(workers), timeout=30)
    results: list[object] = [_MISSING] * len(workers)
    errors: list[BaseException] = []
    lock = threading.Lock()

    def run_worker(index: int, worker: Callable[[threading.Barrier], T]) -> None:
        try:
            results[index] = worker(barrier)
        except BaseException as exc:  # noqa: BLE001 - surfaced to the main thread below
            with lock:
                errors.append(exc)

    threads = [
        threading.Thread(target=run_worker, args=(index, worker), name=f"{thread_name_prefix}-{index}")
        for index, worker in enumerate(workers)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=join_timeout)

    assert not any(thread.is_alive() for thread in threads), f"{thread_name_prefix} thread hung (possible deadlock)"
    assert not errors, f"concurrent {thread_name_prefix} thread(s) failed: {errors}"
    assert all(result is not _MISSING for result in results), f"{thread_name_prefix} worker returned no result"
    return [cast(T, result) for result in results]


# ---------------------------------------------------------------------------
# Fixtures — tenant/principal setup with unique IDs for write tests
# ---------------------------------------------------------------------------


@pytest.fixture
def tenant_a(integration_db):
    """Create tenant A for write tests."""
    tenant_id = "write_test_tenant_a"
    with get_db_session() as session:
        tenant = Tenant(
            tenant_id=tenant_id, name="Write Tenant A", subdomain="write-a", is_active=True, ad_server="mock"
        )
        session.add(tenant)
        session.commit()
    yield tenant_id
    cleanup_tenant(tenant_id)


@pytest.fixture
def tenant_b(integration_db):
    """Create tenant B for cross-tenant isolation tests."""
    tenant_id = "write_test_tenant_b"
    with get_db_session() as session:
        tenant = Tenant(
            tenant_id=tenant_id, name="Write Tenant B", subdomain="write-b", is_active=True, ad_server="mock"
        )
        session.add(tenant)
        session.commit()
    yield tenant_id
    cleanup_tenant(tenant_id)


@pytest.fixture
def principal_a(tenant_a):
    """Create a principal in tenant A."""
    principal_id = "write_principal_a"
    with get_db_session() as session:
        principal = Principal(
            tenant_id=tenant_a,
            principal_id=principal_id,
            name="Write Advertiser A",
            access_token="write_token_a",
            platform_mappings={"mock": {"advertiser_id": "adv_write_a"}},
        )
        session.add(principal)
        session.commit()
    yield principal_id


@pytest.fixture
def principal_b(tenant_b):
    """Create a principal in tenant B."""
    principal_id = "write_principal_b"
    with get_db_session() as session:
        principal = Principal(
            tenant_id=tenant_b,
            principal_id=principal_id,
            name="Write Advertiser B",
            access_token="write_token_b",
            platform_mappings={"mock": {"advertiser_id": "adv_write_b"}},
        )
        session.add(principal)
        session.commit()
    yield principal_id


# ---------------------------------------------------------------------------
# MediaBuy.create — roundtrip and tenant isolation
# ---------------------------------------------------------------------------


class TestCreateMediaBuy:
    """Repository.create() persists a new media buy within the tenant."""

    def test_roundtrip_create_and_read_back(self, tenant_a, principal_a):
        """Create via repository, read back, verify all fields match."""
        with MediaBuyUoW(tenant_a) as uow:
            mb = make_media_buy(tenant_a, principal_a, "mb_create_1")
            result = uow.media_buys.create(mb)
            assert result is mb

        # Read back in a fresh session
        with get_db_session() as session:
            repo = MediaBuyRepository(session, tenant_a)
            fetched = repo.get_by_id("mb_create_1")
            assert fetched is not None
            assert fetched.media_buy_id == "mb_create_1"
            assert fetched.tenant_id == tenant_a
            assert fetched.principal_id == principal_a
            assert fetched.order_name == "Order mb_create_1"
            assert fetched.status == "draft"

    def test_tenant_mismatch_raises(self, tenant_a, tenant_b, principal_a):
        """Creating a media buy with wrong tenant_id raises ValueError."""
        with MediaBuyUoW(tenant_a) as uow:
            mb = make_media_buy(tenant_b, principal_a, "mb_wrong_tenant")
            with pytest.raises(ValueError, match="Tenant mismatch"):
                uow.media_buys.create(mb)

    def test_tenant_isolation_on_create(self, tenant_a, tenant_b, principal_a, principal_b):
        """Media buy created in tenant A is not visible to tenant B."""
        with MediaBuyUoW(tenant_a) as uow:
            mb = make_media_buy(tenant_a, principal_a, "mb_isolated")
            uow.media_buys.create(mb)

        with get_db_session() as session:
            repo_b = MediaBuyRepository(session, tenant_b)
            assert repo_b.get_by_id("mb_isolated") is None

    def test_uow_rollback_on_exception(self, tenant_a, principal_a):
        """If exception is raised inside UoW, the create is rolled back."""
        with pytest.raises(RuntimeError, match="intentional"):
            with MediaBuyUoW(tenant_a) as uow:
                mb = make_media_buy(tenant_a, principal_a, "mb_rollback_create")
                uow.media_buys.create(mb)
                raise RuntimeError("intentional")

        with get_db_session() as session:
            repo = MediaBuyRepository(session, tenant_a)
            assert repo.get_by_id("mb_rollback_create") is None


# ---------------------------------------------------------------------------
# MediaBuy.update_status — roundtrip and tenant isolation
# ---------------------------------------------------------------------------


class TestUpdateStatus:
    """Repository.update_status() changes status and optional approval fields."""

    def test_roundtrip_update_status(self, tenant_a, principal_a):
        """Update status and verify fields persisted."""
        # Seed a media buy
        with MediaBuyUoW(tenant_a) as uow:
            mb = make_media_buy(tenant_a, principal_a, "mb_status_1")
            uow.media_buys.create(mb)

        # Update status
        now = datetime.now(UTC)
        with MediaBuyUoW(tenant_a) as uow:
            result = uow.media_buys.update_status(
                "mb_status_1",
                "approved",
                approved_at=now,
                approved_by="admin@test.com",
            )
            assert result is not None
            assert result.status == "approved"

        # Read back
        with get_db_session() as session:
            repo = MediaBuyRepository(session, tenant_a)
            fetched = repo.get_by_id("mb_status_1")
            assert fetched is not None
            assert fetched.status == "approved"
            assert fetched.approved_by == "admin@test.com"
            assert fetched.approved_at is not None

    def test_update_status_nonexistent_returns_none(self, tenant_a, principal_a):
        """Updating status of nonexistent media buy returns None."""
        with MediaBuyUoW(tenant_a) as uow:
            result = uow.media_buys.update_status("nonexistent_mb", "active")
            assert result is None

    def test_update_status_other_tenant_returns_none(self, tenant_a, tenant_b, principal_a, principal_b):
        """Cannot update status of media buy in another tenant."""
        # Create in tenant A
        with MediaBuyUoW(tenant_a) as uow:
            mb = make_media_buy(tenant_a, principal_a, "mb_cross_status")
            uow.media_buys.create(mb)

        # Try to update from tenant B
        with MediaBuyUoW(tenant_b) as uow:
            result = uow.media_buys.update_status("mb_cross_status", "active")
            assert result is None

        # Verify original status unchanged
        with get_db_session() as session:
            repo = MediaBuyRepository(session, tenant_a)
            fetched = repo.get_by_id("mb_cross_status")
            assert fetched is not None
            assert fetched.status == "draft"

    def test_update_status_only_changes_status(self, tenant_a, principal_a):
        """update_status without approved_at/approved_by only changes status."""
        with MediaBuyUoW(tenant_a) as uow:
            mb = make_media_buy(tenant_a, principal_a, "mb_status_only")
            uow.media_buys.create(mb)

        with MediaBuyUoW(tenant_a) as uow:
            result = uow.media_buys.update_status("mb_status_only", "active")
            assert result is not None
            assert result.status == "active"
            assert result.approved_at is None
            assert result.approved_by is None


class TestRevisionBumpsOnStatusTransition:
    """Every status transition through the repository seam bumps the AdCP 3.1.1
    ``revision`` counter, and manual approval stamps the confirmation instant.

    Regression for #1544: the admin approve/reject routes, the flight-date
    scheduler, and creative-sync assignment previously mutated ``.status``
    directly, bypassing the bump — so ``revision`` never advanced on those
    seller-side state changes and ``confirmed_at`` reported the buyer's request
    time (``created_at``) instead of the approval moment.

    Pins IN-SESSION materialization: values are asserted on the row the seam
    returns within the mutating UoW (cross-session persistence is pinned
    separately by ``TestPersistedRevisionBump``).
    """

    def test_update_status_bumps_revision(self, tenant_a, principal_a):
        """A status change through update_status advances the persisted revision."""
        with MediaBuyUoW(tenant_a) as uow:
            mb = make_media_buy(tenant_a, principal_a, "mb_rev_status", status="pending_approval")
            uow.media_buys.create(mb)

        with MediaBuyUoW(tenant_a) as uow:
            result = uow.media_buys.update_status("mb_rev_status", "active")
            # Created at revision 1; a single transition bumps to exactly 2.
            assert result is not None
            assert result.revision == 2

    def test_update_status_or_raise_returns_row_and_raises_when_missing(self, tenant_a, principal_a):
        """The or-raise variant surfaces a vanished buy instead of reporting silent success.

        The admin approve/reject routes verify the buy exists, then transition it;
        a ``None`` from ``update_status`` at that point means the row disappeared
        mid-request — No-Quiet-Failures requires a raise, not a skipped write.
        """
        with MediaBuyUoW(tenant_a) as uow:
            uow.media_buys.create(make_media_buy(tenant_a, principal_a, "mb_rev_or_raise", status="pending_approval"))

        with MediaBuyUoW(tenant_a) as uow:
            result = uow.media_buys.update_status_or_raise("mb_rev_or_raise", "active")
            assert result.status == "active"

        with MediaBuyUoW(tenant_a) as uow:
            with pytest.raises(RuntimeError, match="mb_never_existed"):
                uow.media_buys.update_status_or_raise("mb_never_existed", "active")
            with pytest.raises(RuntimeError, match="mb_never_existed"):
                uow.media_buys.update_fields_or_raise("mb_never_existed", budget=Decimal("1.00"))
            with pytest.raises(RuntimeError, match="mb_never_existed"):
                uow.media_buys.bump_revision_or_raise("mb_never_existed")

    def test_apply_status_transition_bumps_revision(self, tenant_a, principal_a):
        """The cross-tenant seam (scheduler / creative-sync) bumps revision too."""
        with MediaBuyUoW(tenant_a) as uow:
            mb = make_media_buy(tenant_a, principal_a, "mb_rev_transition", status="pending_start")
            uow.media_buys.create(mb)

        with MediaBuyUoW(tenant_a) as uow:
            buy = uow.media_buys.get_by_id("mb_rev_transition")
            assert buy is not None
            returned = MediaBuyRepository.apply_status_transition(buy, "active")
            # CON-03: the seam returns the same (mutated) row, matching sibling mutators.
            assert returned is buy
            assert buy.status == "active"

        # The revision increment is a server-side SQL expression that only
        # materializes at flush/commit, so assert the value read back from the
        # database (a fresh UoW session), not the transient in-memory attribute.
        with MediaBuyUoW(tenant_a) as uow:
            assert uow.media_buys is not None
            persisted = uow.media_buys.get_by_id("mb_rev_transition")
            assert persisted is not None
            assert persisted.status == "active"
            assert persisted.revision == 2

    def test_manual_approval_stamps_confirmed_at_and_bumps_revision(self, tenant_a, principal_a):
        """create (pending_approval) → approve → get: confirmed_at is the approval
        instant (not created_at) and revision advanced past the create value."""
        from tests.factories.principal import PrincipalFactory

        with MediaBuyUoW(tenant_a) as uow:
            mb = make_media_buy(tenant_a, principal_a, "mb_approve_life", status="pending_approval")
            uow.media_buys.create(mb)

        # A buy awaiting approval is not yet confirmed — get reports None.
        identity = PrincipalFactory.make_identity(
            tenant_id=tenant_a, principal_id=principal_a, tenant={"tenant_id": tenant_a}
        )
        before = read_back_media_buy(identity, "mb_approve_life")
        assert before.confirmed_at is None
        assert before.revision == 1

        # Seller approves — the seam stamps approved_at and bumps revision.
        approve_time = datetime.now(UTC)
        with MediaBuyUoW(tenant_a) as uow:
            uow.media_buys.update_status(
                "mb_approve_life", "active", approved_at=approve_time, approved_by="admin@test.com"
            )

        after = read_back_media_buy(identity, "mb_approve_life")
        # confirmed_at is the approval instant, NOT the buyer's create time.
        assert after.confirmed_at == approve_time
        assert after.confirmed_at != after.created_at
        assert after.revision == 2

    def test_apply_status_transition_stamps_confirmed_at(self, tenant_a, principal_a):
        """draft → pending_creatives through the creative-sync/scheduler seam stamps
        the write-once confirmed_at, so get_media_buys reports it on the wire.

        Regression for #1544: apply_status_transition bumped revision but never
        stamped confirmed_at, so a buy that reached a seller-confirmed status via
        creative-sync reported confirmed_at=None forever. All three status seams now
        route through MediaBuyRepository._stamp_confirmation_if_needed.
        """
        from tests.factories.principal import PrincipalFactory

        with MediaBuyUoW(tenant_a) as uow:
            uow.media_buys.create(make_media_buy(tenant_a, principal_a, "mb_transition_confirm", status="draft"))

        with MediaBuyUoW(tenant_a) as uow:
            buy = uow.media_buys.get_by_id("mb_transition_confirm")
            assert buy is not None
            MediaBuyRepository.apply_status_transition(buy, "pending_creatives")

        identity = PrincipalFactory.make_identity(
            tenant_id=tenant_a, principal_id=principal_a, tenant={"tenant_id": tenant_a}
        )
        after = read_back_media_buy(identity, "mb_transition_confirm")
        # No manual approval, so confirmed_at falls back to the create instant — but
        # it IS set, which the pre-fix seam failed to do.
        assert after.confirmed_at is not None
        assert after.confirmed_at == after.created_at

    def test_apply_status_transition_never_clobbers_a_concurrent_confirmed_at(self, tenant_a, principal_a):
        """Write-once confirmed_at survives a stale unlocked transition.

        The cross-tenant sweep / creative-sync seam loads rows WITHOUT ``FOR
        UPDATE``, so its in-memory state can be stale while a concurrent approval
        commits a real stamp. Here the approval also advances the status
        (draft→active), so apply_status_transition locks, refreshes, sees the
        committed status changed under the stale read, and no-ops — which
        preserves the committed ``confirmed_at`` (the write-once instant) rather
        than clobbering it with this row's ``created_at``. Regression for #1544.
        """
        from sqlalchemy.orm import Session as SASession

        from src.core.database.database_session import get_engine

        with MediaBuyUoW(tenant_a) as uow:
            uow.media_buys.create(make_media_buy(tenant_a, principal_a, "mb_confirm_race", status="draft"))

        # A distinctive, fixed approval instant — unmistakably NOT this row's
        # created_at (~now), so a clobber-with-created_at is unambiguous and immune
        # to any client/server clock skew.
        approve_time = datetime(2020, 1, 1, tzinfo=UTC)
        engine = get_engine()
        # Two INDEPENDENT sessions (not the thread-local scoped session, which the
        # app's get_db_session shares — closing one would detach the other's rows).
        stale_session = SASession(engine)
        approve_session = SASession(engine)
        try:
            # Sweep-style unlocked load: in-memory status is 'draft', confirmed_at None.
            stale = MediaBuyRepository(stale_session, tenant_a).get_by_id("mb_confirm_race")
            assert stale is not None
            assert stale.confirmed_at is None

            # A concurrent approval commits status=active + confirmed_at=approve_time.
            MediaBuyRepository(approve_session, tenant_a).update_status(
                "mb_confirm_race", "active", approved_at=approve_time, approved_by="admin@test.com"
            )
            approve_session.commit()

            # The stale session applies its own transition on the row it loaded
            # BEFORE the approval — its status is still 'draft' in memory.
            MediaBuyRepository.apply_status_transition(stale, "active")
            stale_session.commit()
        finally:
            stale_session.close()
            approve_session.close()

        with MediaBuyUoW(tenant_a) as uow:
            assert uow.media_buys is not None
            persisted = uow.media_buys.get_by_id("mb_confirm_race")
            assert persisted is not None
            # The stale transition no-op'd (committed status advanced draft→active
            # under it), so revision stayed at the approval's bump (1→2), not 3.
            assert persisted.revision == 2
            assert persisted.status == "active"
            # The committed approval instant is preserved, not clobbered with created_at.
            assert persisted.confirmed_at == approve_time
            assert persisted.confirmed_at != persisted.created_at

    def test_apply_status_transition_skips_when_status_changed_underneath(self, tenant_a, principal_a):
        """A stale unlocked transition must not overwrite a concurrent terminal decision.

        Scheduler loads 'active'; an admin commits 'rejected'; the scheduler's stale
        active→completed transition must NO-OP under lock (the committed status
        changed), leaving 'rejected' intact instead of writing 'completed'.
        Regression for #1544: apply_status_transition previously refreshed only
        confirmed_at, never status, so it blindly applied the caller's stale target.
        """
        from sqlalchemy.orm import Session as SASession

        from src.core.database.database_session import get_engine

        with MediaBuyUoW(tenant_a) as uow:
            uow.media_buys.create(make_media_buy(tenant_a, principal_a, "mb_status_race", status="active"))

        engine = get_engine()
        stale_session = SASession(engine)
        admin_session = SASession(engine)
        try:
            # Sweep-style unlocked load: in-memory status is the soon-to-be-stale 'active'.
            stale = MediaBuyRepository(stale_session, tenant_a).get_by_id("mb_status_race")
            assert stale is not None
            assert stale.status == "active"

            # An admin rejects the buy in an independent transaction (terminal decision).
            MediaBuyRepository(admin_session, tenant_a).update_status("mb_status_race", "rejected")
            admin_session.commit()

            # The scheduler's stale active→completed transition must no-op under lock.
            MediaBuyRepository.apply_status_transition(stale, "completed")
            stale_session.commit()
        finally:
            stale_session.close()
            admin_session.close()

        with MediaBuyUoW(tenant_a) as uow:
            assert uow.media_buys is not None
            persisted = uow.media_buys.get_by_id("mb_status_race")
            assert persisted is not None
            # The terminal decision is preserved — NOT overwritten with 'completed'.
            assert persisted.status == "rejected"
            # Admin's write bumped 1→2; the skipped stale transition did NOT bump.
            assert persisted.revision == 2

    def test_lock_timeout_does_not_trip_the_db_circuit_breaker(self, tenant_a, principal_a):
        """Expected lock contention must not poison the process-wide DB circuit
        breaker. A lock_timeout raises OperationalError (SQLSTATE 55P03); a prior
        version marked that as a DB outage in get_db_session, failing-fast every
        unrelated request for 10s. It must re-raise WITHOUT flipping _is_healthy,
        so a subsequent session still works. #1544.

        The waiter/probe run through MediaBuyUoW, whose session is a real
        get_db_session under the hood — so the OperationalError still propagates
        through get_db_session's exit and exercises exactly the circuit-breaker
        code path this test pins (``_is_healthy`` must stay ``True``).
        """
        from sqlalchemy import select, text
        from sqlalchemy.exc import OperationalError
        from sqlalchemy.orm import Session as SASession

        import src.core.database.database_session as dbs
        from src.core.database.database_session import get_engine, reset_health_state
        from src.core.database.models import MediaBuy

        with MediaBuyUoW(tenant_a) as uow:
            uow.media_buys.create(make_media_buy(tenant_a, principal_a, "mb_lock_contended", status="active"))

        # Independent session holds the row lock so the waiter below times out.
        holder = SASession(get_engine())
        try:
            holder.execute(select(MediaBuy).filter_by(media_buy_id="mb_lock_contended").with_for_update()).first()

            with pytest.raises(OperationalError) as exc_info:
                with MediaBuyUoW(tenant_a) as waiter:
                    waiter.session.execute(text("SET LOCAL lock_timeout = '1s'"))
                    waiter.session.execute(
                        select(MediaBuy).filter_by(media_buy_id="mb_lock_contended").with_for_update()
                    ).first()
            assert getattr(exc_info.value.orig, "pgcode", None) == "55P03"  # lock_not_available

            # The breaker stayed closed: health intact and a fresh session works.
            assert dbs._is_healthy is True
            with MediaBuyUoW(tenant_a) as ok:
                assert ok.session.execute(text("SELECT 1")).scalar() == 1
        finally:
            holder.rollback()
            holder.close()
            reset_health_state()

    def test_get_by_id_lock_timeout_translates_contention_to_transient_conflict(self, tenant_a, principal_a):
        """Production-path lock coverage: get_by_id(lock_timeout=...) (used by
        _update_media_buy_impl) must arm its OWN lock_timeout and translate the
        expected 55P03 contention into a transient AdCPConflictError — NOT rely on
        a caller-installed timeout. A prior version put the SET LOCAL + SQLSTATE
        handling in the _impl; deleting it there stayed green because tests
        installed their own timeout. This drives the real repository seam under a
        held lock so a regression reddens. #1544.
        """
        from sqlalchemy import select
        from sqlalchemy.orm import Session as SASession

        from src.core.database.database_session import get_engine, reset_health_state
        from src.core.database.models import MediaBuy
        from src.core.exceptions import AdCPConflictError

        with MediaBuyUoW(tenant_a) as uow:
            uow.media_buys.create(make_media_buy(tenant_a, principal_a, "mb_lock_prod_path", status="active"))

        # Independent session holds the row lock so the repository waiter times out.
        holder = SASession(get_engine())
        try:
            holder.execute(select(MediaBuy).filter_by(media_buy_id="mb_lock_prod_path").with_for_update()).first()

            # The waiter runs its locked read through a MediaBuyUoW session (a real
            # get_db_session under the hood); the repository seam arms its OWN
            # lock_timeout and translates the 55P03 contention into AdCPConflictError,
            # which propagates out through the UoW's rollback-and-re-raise exit.
            with pytest.raises(AdCPConflictError) as exc_info:
                with MediaBuyUoW(tenant_a) as waiter:
                    assert waiter.media_buys is not None
                    waiter.media_buys.get_by_id(
                        "mb_lock_prod_path", for_update=True, populate_existing=True, lock_timeout="5s"
                    )
            # Buyer-facing recovery is transient (re-read and retry), not terminal.
            assert exc_info.value.recovery == "transient"
        finally:
            holder.rollback()
            holder.close()
            reset_health_state()

    def test_update_fields_staged_status_stamps_confirmed_at(self, tenant_a, principal_a):
        """A staged status change through update_fields (the update tool's approval
        path) also stamps confirmed_at — the third blessed seam. #1544."""
        from tests.factories.principal import PrincipalFactory

        with MediaBuyUoW(tenant_a) as uow:
            uow.media_buys.create(make_media_buy(tenant_a, principal_a, "mb_fields_confirm", status="draft"))

        with MediaBuyUoW(tenant_a) as uow:
            uow.media_buys.update_fields("mb_fields_confirm", status="pending_creatives")

        identity = PrincipalFactory.make_identity(
            tenant_id=tenant_a, principal_id=principal_a, tenant={"tenant_id": tenant_a}
        )
        after = read_back_media_buy(identity, "mb_fields_confirm")
        assert after.confirmed_at is not None
        assert after.confirmed_at == after.created_at


# ---------------------------------------------------------------------------
# MediaBuy.update_fields — generic field update
# ---------------------------------------------------------------------------


class TestUpdateFields:
    """Repository.update_fields() updates arbitrary attributes."""

    def test_roundtrip_update_fields(self, tenant_a, principal_a):
        """Update multiple fields and verify persistence."""
        with MediaBuyUoW(tenant_a) as uow:
            mb = make_media_buy(tenant_a, principal_a, "mb_fields_1")
            uow.media_buys.create(mb)

        with MediaBuyUoW(tenant_a) as uow:
            result = uow.media_buys.update_fields(
                "mb_fields_1",
                order_name="Updated Order Name",
                budget=Decimal("5000.00"),
                kpi_goal="maximize_reach",
            )
            assert result is not None
            assert result.order_name == "Updated Order Name"

        # Read back in fresh session
        with get_db_session() as session:
            repo = MediaBuyRepository(session, tenant_a)
            fetched = repo.get_by_id("mb_fields_1")
            assert fetched is not None
            assert fetched.order_name == "Updated Order Name"
            assert fetched.budget == Decimal("5000.00")
            assert fetched.kpi_goal == "maximize_reach"

    def test_update_fields_nonexistent_returns_none(self, tenant_a, principal_a):
        """Updating fields of nonexistent media buy returns None."""
        with MediaBuyUoW(tenant_a) as uow:
            result = uow.media_buys.update_fields("nonexistent_mb", order_name="x")
            assert result is None

    def test_update_fields_invalid_attribute_raises(self, tenant_a, principal_a):
        """Updating a nonexistent attribute raises ValueError."""
        with MediaBuyUoW(tenant_a) as uow:
            mb = make_media_buy(tenant_a, principal_a, "mb_invalid_field")
            uow.media_buys.create(mb)

        with pytest.raises(ValueError, match="has no attribute"):
            with MediaBuyUoW(tenant_a) as uow:
                uow.media_buys.update_fields("mb_invalid_field", nonexistent_field="value")

    def test_update_fields_tenant_isolation(self, tenant_a, tenant_b, principal_a, principal_b):
        """Cannot update fields of media buy in another tenant."""
        with MediaBuyUoW(tenant_a) as uow:
            mb = make_media_buy(tenant_a, principal_a, "mb_fields_iso")
            uow.media_buys.create(mb)

        with MediaBuyUoW(tenant_b) as uow:
            result = uow.media_buys.update_fields("mb_fields_iso", order_name="hacked")
            assert result is None

        with get_db_session() as session:
            repo = MediaBuyRepository(session, tenant_a)
            fetched = repo.get_by_id("mb_fields_iso")
            assert fetched is not None
            assert fetched.order_name == "Order mb_fields_iso"


# ---------------------------------------------------------------------------
# MediaPackage.create_package — roundtrip and tenant isolation
# ---------------------------------------------------------------------------


class TestCreatePackage:
    """Repository.create_package() creates a package for a tenant-scoped media buy."""

    def test_roundtrip_create_package(self, tenant_a, principal_a):
        """Create a package and read it back."""
        with MediaBuyUoW(tenant_a) as uow:
            mb = make_media_buy(tenant_a, principal_a, "mb_pkg_create")
            uow.media_buys.create(mb)

        with MediaBuyUoW(tenant_a) as uow:
            pkg = uow.media_buys.create_package(
                media_buy_id="mb_pkg_create",
                package_id="pkg_1",
                package_config={"name": "Test Package", "product_id": "prod_1"},
                budget=Decimal("1000.00"),
                bid_price=Decimal("5.50"),
                pacing="even",
            )
            assert pkg.package_id == "pkg_1"
            assert pkg.budget == Decimal("1000.00")

        # Read back
        with get_db_session() as session:
            repo = MediaBuyRepository(session, tenant_a)
            fetched = repo.get_package("mb_pkg_create", "pkg_1")
            assert fetched is not None
            assert fetched.package_id == "pkg_1"
            assert fetched.package_config == {"name": "Test Package", "product_id": "prod_1"}
            assert fetched.budget == Decimal("1000.00")
            assert fetched.bid_price == Decimal("5.50")
            assert fetched.pacing == "even"

    def test_create_package_nonexistent_media_buy_raises(self, tenant_a, principal_a):
        """Creating a package for a nonexistent media buy raises ValueError."""
        with pytest.raises(ValueError, match="not found"):
            with MediaBuyUoW(tenant_a) as uow:
                uow.media_buys.create_package(
                    media_buy_id="nonexistent_mb",
                    package_id="pkg_x",
                    package_config={"test": True},
                )

    def test_create_package_other_tenant_media_buy_raises(self, tenant_a, tenant_b, principal_a, principal_b):
        """Creating a package for another tenant's media buy raises ValueError."""
        # Create media buy in tenant B
        with MediaBuyUoW(tenant_b) as uow:
            mb = make_media_buy(tenant_b, principal_b, "mb_other_tenant_pkg")
            uow.media_buys.create(mb)

        # Try to create package from tenant A
        with pytest.raises(ValueError, match="not found"):
            with MediaBuyUoW(tenant_a) as uow:
                uow.media_buys.create_package(
                    media_buy_id="mb_other_tenant_pkg",
                    package_id="pkg_cross",
                    package_config={"test": True},
                )

    def test_create_package_with_no_optional_fields(self, tenant_a, principal_a):
        """Create a package with only required fields (no budget/bid_price/pacing)."""
        with MediaBuyUoW(tenant_a) as uow:
            mb = make_media_buy(tenant_a, principal_a, "mb_pkg_minimal")
            uow.media_buys.create(mb)

        with MediaBuyUoW(tenant_a) as uow:
            pkg = uow.media_buys.create_package(
                media_buy_id="mb_pkg_minimal",
                package_id="pkg_min",
                package_config={"name": "Minimal"},
            )
            assert pkg.budget is None
            assert pkg.bid_price is None
            assert pkg.pacing is None


# ---------------------------------------------------------------------------
# MediaPackage.update_package_config
# ---------------------------------------------------------------------------


class TestUpdatePackageConfig:
    """Repository.update_package_config() replaces the JSON config."""

    def test_roundtrip_update_config(self, tenant_a, principal_a):
        """Update package_config and read back."""
        with MediaBuyUoW(tenant_a) as uow:
            mb = make_media_buy(tenant_a, principal_a, "mb_upd_cfg")
            uow.media_buys.create(mb)

        with MediaBuyUoW(tenant_a) as uow:
            uow.media_buys.create_package(
                media_buy_id="mb_upd_cfg",
                package_id="pkg_cfg",
                package_config={"version": 1},
            )

        new_config = {"version": 2, "extra_field": "new"}
        with MediaBuyUoW(tenant_a) as uow:
            result = uow.media_buys.update_package_config("mb_upd_cfg", "pkg_cfg", new_config)
            assert result is not None
            assert result.package_config == new_config

        # Read back
        with get_db_session() as session:
            repo = MediaBuyRepository(session, tenant_a)
            fetched = repo.get_package("mb_upd_cfg", "pkg_cfg")
            assert fetched is not None
            assert fetched.package_config == new_config

    def test_update_config_nonexistent_returns_none(self, tenant_a, principal_a):
        """Updating config of nonexistent package returns None."""
        with MediaBuyUoW(tenant_a) as uow:
            mb = make_media_buy(tenant_a, principal_a, "mb_no_pkg_cfg")
            uow.media_buys.create(mb)

        with MediaBuyUoW(tenant_a) as uow:
            result = uow.media_buys.update_package_config("mb_no_pkg_cfg", "nonexistent", {})
            assert result is None

    def test_update_config_other_tenant_returns_none(self, tenant_a, tenant_b, principal_a, principal_b):
        """Cannot update package config via another tenant's repository."""
        with MediaBuyUoW(tenant_a) as uow:
            mb = make_media_buy(tenant_a, principal_a, "mb_cfg_iso")
            uow.media_buys.create(mb)

        with MediaBuyUoW(tenant_a) as uow:
            uow.media_buys.create_package(
                media_buy_id="mb_cfg_iso",
                package_id="pkg_cfg_iso",
                package_config={"original": True},
            )

        with MediaBuyUoW(tenant_b) as uow:
            result = uow.media_buys.update_package_config("mb_cfg_iso", "pkg_cfg_iso", {"hacked": True})
            assert result is None

        # Verify original unchanged
        with get_db_session() as session:
            repo = MediaBuyRepository(session, tenant_a)
            fetched = repo.get_package("mb_cfg_iso", "pkg_cfg_iso")
            assert fetched is not None
            assert fetched.package_config == {"original": True}


# ---------------------------------------------------------------------------
# MediaPackage.update_package_fields
# ---------------------------------------------------------------------------


class TestUpdatePackageFields:
    """Repository.update_package_fields() updates arbitrary package attributes."""

    def test_roundtrip_update_package_fields(self, tenant_a, principal_a):
        """Update package fields and verify persistence."""
        with MediaBuyUoW(tenant_a) as uow:
            mb = make_media_buy(tenant_a, principal_a, "mb_pkg_fields")
            uow.media_buys.create(mb)

        with MediaBuyUoW(tenant_a) as uow:
            uow.media_buys.create_package(
                media_buy_id="mb_pkg_fields",
                package_id="pkg_fld",
                package_config={"test": True},
                budget=Decimal("500.00"),
            )

        with MediaBuyUoW(tenant_a) as uow:
            result = uow.media_buys.update_package_fields(
                "mb_pkg_fields",
                "pkg_fld",
                budget=Decimal("1500.00"),
                pacing="asap",
            )
            assert result is not None
            assert result.budget == Decimal("1500.00")
            assert result.pacing == "asap"

        # Read back
        with get_db_session() as session:
            repo = MediaBuyRepository(session, tenant_a)
            fetched = repo.get_package("mb_pkg_fields", "pkg_fld")
            assert fetched is not None
            assert fetched.budget == Decimal("1500.00")
            assert fetched.pacing == "asap"

    def test_update_package_fields_nonexistent_returns_none(self, tenant_a, principal_a):
        """Updating fields of nonexistent package returns None."""
        with MediaBuyUoW(tenant_a) as uow:
            mb = make_media_buy(tenant_a, principal_a, "mb_no_pkg_fld")
            uow.media_buys.create(mb)

        with MediaBuyUoW(tenant_a) as uow:
            result = uow.media_buys.update_package_fields("mb_no_pkg_fld", "nope", budget=Decimal("1.00"))
            assert result is None

    def test_update_package_fields_invalid_attribute_raises(self, tenant_a, principal_a):
        """Updating a nonexistent package attribute raises ValueError."""
        with MediaBuyUoW(tenant_a) as uow:
            mb = make_media_buy(tenant_a, principal_a, "mb_pkg_bad_attr")
            uow.media_buys.create(mb)

        with MediaBuyUoW(tenant_a) as uow:
            uow.media_buys.create_package(
                media_buy_id="mb_pkg_bad_attr",
                package_id="pkg_bad",
                package_config={"test": True},
            )

        with pytest.raises(ValueError, match="has no attribute"):
            with MediaBuyUoW(tenant_a) as uow:
                uow.media_buys.update_package_fields("mb_pkg_bad_attr", "pkg_bad", fake_field="x")


# ---------------------------------------------------------------------------
# create_packages_bulk — batch creation
# ---------------------------------------------------------------------------


class TestCreatePackagesBulk:
    """Repository.create_packages_bulk() creates multiple packages atomically."""

    def test_roundtrip_bulk_create(self, tenant_a, principal_a):
        """Bulk create packages and read them all back."""
        with MediaBuyUoW(tenant_a) as uow:
            mb = make_media_buy(tenant_a, principal_a, "mb_bulk")
            uow.media_buys.create(mb)

        packages = [
            make_package("mb_bulk", "pkg_bulk_1", budget=Decimal("100.00")),
            make_package("mb_bulk", "pkg_bulk_2", budget=Decimal("200.00")),
            make_package("mb_bulk", "pkg_bulk_3"),
        ]

        with MediaBuyUoW(tenant_a) as uow:
            result = uow.media_buys.create_packages_bulk("mb_bulk", packages)
            assert len(result) == 3

        # Read back
        with get_db_session() as session:
            repo = MediaBuyRepository(session, tenant_a)
            fetched = repo.get_packages("mb_bulk")
            assert len(fetched) == 3
            pkg_ids = {p.package_id for p in fetched}
            assert pkg_ids == {"pkg_bulk_1", "pkg_bulk_2", "pkg_bulk_3"}

    def test_bulk_create_nonexistent_media_buy_raises(self, tenant_a, principal_a):
        """Bulk creating packages for nonexistent media buy raises ValueError."""
        packages = [make_package("nonexistent_mb", "pkg_x")]
        with pytest.raises(ValueError, match="not found"):
            with MediaBuyUoW(tenant_a) as uow:
                uow.media_buys.create_packages_bulk("nonexistent_mb", packages)

    def test_bulk_create_media_buy_id_mismatch_raises(self, tenant_a, principal_a):
        """Bulk creating with mismatched media_buy_id raises ValueError."""
        with MediaBuyUoW(tenant_a) as uow:
            mb = make_media_buy(tenant_a, principal_a, "mb_bulk_mismatch")
            uow.media_buys.create(mb)

        packages = [
            make_package("mb_bulk_mismatch", "pkg_ok"),
            make_package("wrong_mb_id", "pkg_bad"),
        ]

        with pytest.raises(ValueError, match="media_buy_id"):
            with MediaBuyUoW(tenant_a) as uow:
                uow.media_buys.create_packages_bulk("mb_bulk_mismatch", packages)

    def test_bulk_create_empty_list(self, tenant_a, principal_a):
        """Bulk creating with empty list succeeds and returns empty."""
        with MediaBuyUoW(tenant_a) as uow:
            mb = make_media_buy(tenant_a, principal_a, "mb_bulk_empty")
            uow.media_buys.create(mb)

        with MediaBuyUoW(tenant_a) as uow:
            result = uow.media_buys.create_packages_bulk("mb_bulk_empty", [])
            assert result == []

    def test_bulk_create_tenant_isolation(self, tenant_a, tenant_b, principal_a, principal_b):
        """Cannot bulk create packages via another tenant's repository."""
        with MediaBuyUoW(tenant_b) as uow:
            mb = make_media_buy(tenant_b, principal_b, "mb_bulk_iso")
            uow.media_buys.create(mb)

        packages = [make_package("mb_bulk_iso", "pkg_iso")]
        with pytest.raises(ValueError, match="not found"):
            with MediaBuyUoW(tenant_a) as uow:
                uow.media_buys.create_packages_bulk("mb_bulk_iso", packages)


# ---------------------------------------------------------------------------
# Concurrent revision bump — the counter must not collide under real contention
# ---------------------------------------------------------------------------


class TestConcurrentRevisionBump:
    """Two concurrent bumps on the SAME buy must land on distinct revisions —
    on BOTH the locked and the unlocked mutation seams.

    The two mutation seams are protected by different mechanisms, so each needs
    its own concurrent test:

    - **Locked seam** (``bump_revision``/``update_fields``/``update_status`` →
      ``_locked_mutate_and_bump``): the ``get_by_id(for_update=True,
      populate_existing=True)`` re-reads the committed counter under the row lock
      before the increment, so a stale identity-mapped read cannot survive. This
      seam stays correct even under a Python read-modify-write — the lock +
      ``populate_existing`` do the work, NOT the server-side increment.

    - **Unlocked seam** (``apply_status_transition``, used by the cross-tenant
      scheduler sweep and creative-sync): no ``FOR UPDATE``, no
      ``populate_existing`` re-read, so the server-side ``revision =
      coalesce(revision, 0) + 1`` is the SOLE protection. This is the seam that
      goes red if the increment regresses to a Python read-modify-write.

    The second test below is therefore the one that actually isolates the
    server-side increment (#1544).
    """

    def test_two_concurrent_bumps_yield_distinct_revisions(self, tenant_a, principal_a):
        with MediaBuyUoW(tenant_a) as uow:
            uow.media_buys.create(make_media_buy(tenant_a, principal_a, "mb_concurrent_rev"))

        # Starting revision after create.
        with MediaBuyUoW(tenant_a) as uow:
            start_rev = uow.media_buys.get_by_id("mb_concurrent_rev").revision
        assert start_rev == 1

        def bump_once(barrier: threading.Barrier) -> None:
            with MediaBuyUoW(tenant_a) as uow:
                # Preload the row into THIS transaction's identity map at the
                # current revision, before either thread bumps. This is the
                # stale-read setup that a naive Python increment would lose.
                preloaded = uow.media_buys.get_by_id("mb_concurrent_rev")
                assert preloaded is not None
                barrier.wait()
                updated = uow.media_buys.bump_revision("mb_concurrent_rev")
                assert updated is not None
                # UoW commit happens on clean exit.

        _run_concurrently([bump_once, bump_once], thread_name_prefix="bump")

        with MediaBuyUoW(tenant_a) as uow:
            final_rev = uow.media_buys.get_by_id("mb_concurrent_rev").revision
        # Two bumps from revision 1 → 3. A collision (lost update) would leave 2.
        assert final_rev == 3, f"expected two distinct bumps 1→2→3, got final revision {final_rev}"

    def test_two_concurrent_apply_status_transition_yield_distinct_revisions(self, tenant_a, principal_a):
        """The unlocked seam relies solely on the server-side increment.

        ``apply_status_transition`` (scheduler sweep, creative-sync) loads its row
        WITHOUT ``FOR UPDATE`` and WITHOUT ``populate_existing``, so nothing
        refreshes the stale identity-mapped counter before the bump — the
        server-side ``coalesce(revision, 0) + 1`` is the only thing standing
        between two concurrent transitions and a lost update. Unlike the locked
        bump test above, THIS one goes red if the increment regresses to a Python
        read-modify-write (both threads would write ``2``, leaving the final
        revision at 2 instead of 3). #1544.
        """
        # Start already 'active' so both threads transition active→active: the
        # source status is unchanged under lock, so both legitimately proceed and
        # bump. (A stale transition against a CHANGED status now no-ops — covered
        # by test_apply_status_transition_skips_when_status_changed_underneath.)
        with MediaBuyUoW(tenant_a) as uow:
            uow.media_buys.create(make_media_buy(tenant_a, principal_a, "mb_ast_concurrent", status="active"))

        def transition_once(barrier: threading.Barrier) -> None:
            with MediaBuyUoW(tenant_a) as uow:
                # Plain get_by_id: unlocked, no populate_existing — the stale
                # in-memory revision both threads hold before either commits.
                mb = uow.media_buys.get_by_id("mb_ast_concurrent")
                assert mb is not None
                barrier.wait()
                MediaBuyRepository.apply_status_transition(mb, "active")
                # UoW commit happens on clean exit.

        _run_concurrently([transition_once, transition_once], thread_name_prefix="apply-status-transition")

        with MediaBuyUoW(tenant_a) as uow:
            final_rev = uow.media_buys.get_by_id("mb_ast_concurrent").revision
        # Two transitions from revision 1 → 3. A Python read-modify-write loses one → 2.
        assert final_rev == 3, f"lost update on the unlocked seam: expected 3, got {final_rev}"


# ---------------------------------------------------------------------------
# Persisted revision bump — assert the value the DB produces, not a mock echo
# ---------------------------------------------------------------------------


class TestPersistedRevisionBump:
    """Each mutation path bumps the persisted counter, read back from the DB.

    These replace the transient-instance unit assertions that became meaningless
    once the bump moved server-side: ``_bump_revision`` now assigns a SQL
    expression (``coalesce(revision, 0) + 1``), so the value only materializes on
    flush — the number a buyer sees must be read back from PostgreSQL, never
    asserted on an in-memory ORM attribute (#1544 round-2 TQ-03).

    Pins CROSS-SESSION persistence: every value is re-read from the database in
    a fresh session (in-session materialization on the seam's returned row is
    pinned separately by ``TestRevisionBumpsOnStatusTransition``).
    """

    def _read_revision(self, tenant_id: str, media_buy_id: str) -> int:
        with MediaBuyUoW(tenant_id) as uow:
            assert uow.media_buys is not None
            buy = uow.media_buys.get_by_id(media_buy_id)
            assert buy is not None
            return buy.revision

    def test_update_status_bumps_persisted_revision(self, tenant_a, principal_a):
        with MediaBuyUoW(tenant_a) as uow:
            uow.media_buys.create(make_media_buy(tenant_a, principal_a, "mb_rev_status"))
        assert self._read_revision(tenant_a, "mb_rev_status") == 1

        with MediaBuyUoW(tenant_a) as uow:
            uow.media_buys.update_status("mb_rev_status", "paused")
        assert self._read_revision(tenant_a, "mb_rev_status") == 2

    def test_update_fields_bumps_persisted_revision(self, tenant_a, principal_a):
        with MediaBuyUoW(tenant_a) as uow:
            uow.media_buys.create(make_media_buy(tenant_a, principal_a, "mb_rev_fields"))
        assert self._read_revision(tenant_a, "mb_rev_fields") == 1

        with MediaBuyUoW(tenant_a) as uow:
            uow.media_buys.update_fields("mb_rev_fields", budget=Decimal("250.00"), currency="USD")
        assert self._read_revision(tenant_a, "mb_rev_fields") == 2

    def test_bump_revision_is_strictly_monotonic_across_consecutive_commits(self, tenant_a, principal_a):
        """Two sequential bumps yield 1 → 2 → 3 — no same-second collision.

        A time-derived formula returns the same value for bumps within one clock
        tick; the persisted counter advances by exactly one each time.
        """
        with MediaBuyUoW(tenant_a) as uow:
            uow.media_buys.create(make_media_buy(tenant_a, principal_a, "mb_rev_mono"))

        with MediaBuyUoW(tenant_a) as uow:
            uow.media_buys.bump_revision("mb_rev_mono")
        first = self._read_revision(tenant_a, "mb_rev_mono")

        with MediaBuyUoW(tenant_a) as uow:
            uow.media_buys.bump_revision("mb_rev_mono")
        second = self._read_revision(tenant_a, "mb_rev_mono")

        assert (first, second) == (2, 3)
        assert second > first


class TestExpectedRevisionUnderLock:
    """The optimistic-concurrency token is enforced UNDER the row lock at the
    mutation seam — the authoritative backstop, independent of the update tool's
    pre-adapter gate (which holds the same lock in the same UoW).

    AdCP 3.1.1 update-media-buy-request.json properties.revision MUST.
    The discriminating case: the mutating session already holds a STALE
    in-memory instance (identity map), another session bumps the row, and the
    seam must still CONFLICT — the locked SELECT re-populates the counter under
    the held lock (populate_existing) instead of trusting the stale attribute
    (#1544 round-7).
    """

    def test_stale_identity_map_instance_still_conflicts(self, tenant_a, principal_a):
        from src.core.exceptions import AdCPConflictError

        with MediaBuyUoW(tenant_a) as uow:
            uow.media_buys.create(make_media_buy(tenant_a, principal_a, "mb_lock_conflict"))

        with MediaBuyUoW(tenant_a) as uow:
            # Load the row unlocked into THIS session's identity map at revision 1.
            stale = uow.media_buys.get_by_id("mb_lock_conflict")
            assert stale is not None and (stale.revision or 1) == 1

            # A concurrent writer bumps the committed row to 2.
            with MediaBuyUoW(tenant_a) as other:
                other.media_buys.bump_revision_or_raise("mb_lock_conflict")

            # The seam must see the committed 2 under its lock and CONFLICT,
            # even though this session's instance still reads 1.
            with pytest.raises(AdCPConflictError) as exc_info:
                uow.media_buys.update_fields_or_raise("mb_lock_conflict", expected_revision=1, budget=Decimal("500.00"))
            assert exc_info.value.details["current_version"] == 2
            assert exc_info.value.details["expected_version"] == 1

    def test_matching_token_mutates_and_bumps(self, tenant_a, principal_a):
        with MediaBuyUoW(tenant_a) as uow:
            uow.media_buys.create(make_media_buy(tenant_a, principal_a, "mb_lock_match"))

        with MediaBuyUoW(tenant_a) as uow:
            row = uow.media_buys.update_fields_or_raise("mb_lock_match", expected_revision=1, budget=Decimal("750.00"))
            assert row is not None

        with MediaBuyUoW(tenant_a) as uow:
            assert uow.media_buys is not None
            persisted = uow.media_buys.get_by_id("mb_lock_match")
            assert persisted is not None
            assert persisted.revision == 2
            assert persisted.budget == Decimal("750.00")

    def test_two_concurrent_updates_same_token_one_wins_one_conflicts(self, tenant_a, principal_a):
        """Both writers pass the fast unlocked gate with the SAME token; the
        under-lock check must reject exactly one.

        Mirrors ``TestConcurrentRevisionBump``'s barrier setup: two independent
        transactions preload the row at revision 1 and both present
        ``expected_revision=1``. The locked check serializes on the row
        write-lock — the winner mutates and bumps to 2; the loser's
        refresh-under-lock then reads the committed 2 and raises CONFLICT. A
        gate that only checks the unlocked snapshot admits both writes
        (#1544 round-7): this test is red under gate-only enforcement.
        """
        from src.core.exceptions import AdCPConflictError

        with MediaBuyUoW(tenant_a) as uow:
            uow.media_buys.create(make_media_buy(tenant_a, principal_a, "mb_token_race"))

        def update_with_token(barrier: threading.Barrier, budget: str) -> str:
            with MediaBuyUoW(tenant_a) as uow:
                # Preload at revision 1 in THIS transaction — the unlocked
                # snapshot both writers' fast gates would see.
                preloaded = uow.media_buys.get_by_id("mb_token_race")
                assert preloaded is not None and (preloaded.revision or 1) == 1
                barrier.wait()
                try:
                    uow.media_buys.update_fields_or_raise("mb_token_race", expected_revision=1, budget=Decimal(budget))
                except AdCPConflictError:
                    return "conflict"
            return "applied"

        outcomes = _run_concurrently(
            [lambda barrier, budget=budget: update_with_token(barrier, budget) for budget in ("100.00", "200.00")],
            thread_name_prefix="update-with-token",
        )

        assert sorted(outcomes) == ["applied", "conflict"], (
            f"exactly one writer must win and one must CONFLICT, got outcomes: {outcomes}"
        )

        # Exactly one mutation landed: revision advanced by exactly one, and the
        # budget is whichever writer won (never a blend, never both).
        with MediaBuyUoW(tenant_a) as uow:
            assert uow.media_buys is not None
            persisted = uow.media_buys.get_by_id("mb_token_race")
            assert persisted is not None
            assert persisted.revision == 2
            assert persisted.budget in (Decimal("100.00"), Decimal("200.00"))
