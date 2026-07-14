"""P0 crash-recovery (#1637): approval finalization is exactly-once under crashes AND races.

Protocol under test (media_buy_completion.py + MediaBuyRepository lease seams):

- Phase 1 claims the buy ``pending_approval → finalizing`` WITH a durable expiring
  lease, committed before any external work.
- Phase 2 may run the adapter / publish ONLY while owning the lease; every mutation is
  a lease-CAS whose result is checked (lost ownership ⇒ NOT_CLAIMED, no side effects).
- A durable ``finalize_adapter_invoked_at`` marker committed immediately before the
  adapter splits the safe auto-retry window (marker absent ⇒ nothing remote happened)
  from the dangerous one (marker present ⇒ only full-replay adapters auto-resume;
  real adapters are flagged ``manual_required`` once — never a blind re-create, never
  a hot loop).

These tests model the reviewer's counterexamples: a worker blocked in the adapter past
a reconciler pass, a stale worker returning after ownership changed, crash windows on
both sides of the invoked marker, and the step-less creative-unblock path.
"""

import datetime
import threading
from datetime import UTC

import pytest

from src.adapters.base import AdapterIdempotencyUncertain
from src.admin.services.media_buy_completion import (
    FinalizeOutcome,
    finalize_media_buy_approval,
    resume_finalizing_media_buy,
)
from src.core.context_manager import ContextManager
from src.core.database.database_session import get_db_session
from src.core.database.models import MediaPackage
from src.core.database.repositories import MediaBuyRepository, MediaBuyUoW
from src.core.database.repositories.workflow import WorkflowRepository
from tests.integration.conftest import make_media_buy, seed_pending_buy_and_step


def _resume(session, tenant_id, media_buy_id, step_id, step_data, run_adapter, *, replayable=False):
    """Drive the reconciler entry with an explicit replay-capability disposition."""
    return resume_finalizing_media_buy(
        session,
        tenant_id,
        media_buy_id=media_buy_id,
        step_id=step_id,
        step_data=step_data,
        run_adapter=run_adapter,
        adapter_supports_replay=lambda: replayable,
    )


def _start_approval_worker(
    tenant_id: str, media_buy_id: str, step_id: str, step_data: dict, run_adapter
) -> tuple[threading.Thread, dict]:
    """Start a background thread driving the REAL approval finalizer on its own session.

    Shared by the ownership-race tests (blocked-worker / stale-worker / self-heal) so
    the finalize call site lives once (DRY). Returns ``(thread, result)`` where
    ``result`` receives ``outcome`` (or ``error``) when the worker finishes.
    """
    result: dict = {}

    def worker() -> None:
        try:
            with get_db_session() as session:
                result["outcome"] = finalize_media_buy_approval(
                    session,
                    tenant_id,
                    media_buy_id=media_buy_id,
                    step_id=step_id,
                    step_data=step_data,
                    compute_target=lambda _mb: "active",
                    run_adapter=run_adapter,
                    expected_status="pending_approval",
                    approved_by="admin",
                    approved_at=datetime.datetime.now(UTC),
                )[0]
        except BaseException as exc:  # noqa: BLE001 - surfaced to the main thread
            result["error"] = exc

    thread = threading.Thread(target=worker, name=f"approval-worker-{media_buy_id}")
    thread.start()
    return thread, result


def _expire_lease_now(tenant_id: str, media_buy_id: str) -> None:
    """Force the buy's phase-2 lease to read as expired (models a slow/stale owner)."""
    with get_db_session() as session:
        buy = MediaBuyRepository(session, tenant_id).get_by_id(media_buy_id)
        assert buy is not None
        buy.finalize_lease_expires_at = datetime.datetime.now(UTC) - datetime.timedelta(seconds=1)
        session.commit()


@pytest.mark.requires_db
class TestApprovalCrashRecovery:
    @pytest.fixture
    def context_manager(self):
        return ContextManager()

    def _seed_pending(self, context_manager, tenant_id, principal_id, media_buy_id) -> tuple[str, dict]:
        return seed_pending_buy_and_step(context_manager, tenant_id, principal_id, media_buy_id)

    def _claim_expired(self, tenant_id: str, media_buy_id: str, *, mark_invoked: bool) -> str:
        """Model a crashed worker: claimed ``finalizing`` with an already-expired lease,
        optionally past the adapter-invoked marker."""
        with get_db_session() as session:
            repo = MediaBuyRepository(session, tenant_id)
            # Model the real approve claim: phase 1 always stamps the approval instant.
            claim = repo.claim_finalizing(
                media_buy_id,
                expected_status="pending_approval",
                lease_ttl_seconds=3600,
                approved_at=datetime.datetime.now(UTC),
                approved_by="admin",
            )
            assert claim is not None
            buy, lease_id = claim
            if mark_invoked:
                assert repo.set_finalize_adapter_invoked(media_buy_id, lease_id)
            # The crash happened long ago: expire the lease in place.
            buy.finalize_lease_expires_at = datetime.datetime.now(UTC) - datetime.timedelta(seconds=1)
            session.commit()
        return lease_id

    # ── Crash windows ────────────────────────────────────────────────────

    def test_crash_before_marker_is_auto_recoverable_even_for_real_adapters(
        self, integration_db, sample_tenant, sample_principal, context_manager
    ):
        """A crash AFTER the claim but BEFORE the adapter-invoked marker means nothing
        remote happened — the reconciler safely re-attempts even for a non-replayable
        (real) adapter, and completes with exactly one adapter invocation."""
        tenant_id = sample_tenant["tenant_id"]
        step_id, step_data = self._seed_pending(context_manager, tenant_id, sample_principal["principal_id"], "mb_pre")
        self._claim_expired(tenant_id, "mb_pre", mark_invoked=False)

        adapter_calls: list[str] = []

        def adapter():
            adapter_calls.append("call")
            return True, None

        with get_db_session() as session:
            outcome, _ = _resume(session, tenant_id, "mb_pre", step_id, step_data, adapter, replayable=False)

        assert outcome is FinalizeOutcome.APPLIED
        assert adapter_calls == ["call"]
        with get_db_session() as session:
            buy = MediaBuyRepository(session, tenant_id).get_by_id("mb_pre")
            assert buy is not None and buy.status == "active"
            assert buy.confirmed_at is not None and buy.confirmed_at == buy.approved_at  # B4 preserved
            assert buy.finalize_lease_id is None and buy.finalize_adapter_invoked_at is None
            step = WorkflowRepository(session, tenant_id).get_by_step_id(step_id)
            assert step is not None and step.status == "completed" and step.response_data is not None

    def test_crash_after_marker_on_real_adapter_goes_manual_once_no_hot_loop(
        self, integration_db, sample_tenant, sample_principal, context_manager
    ):
        """Marker present + non-replayable adapter: the remote graph may be partial, so
        the reconciler NEVER re-invokes the adapter — it flags ``manual_required`` ONCE
        and every later pass skips the buy entirely (hot-loop pin). This holds even
        when ``platform_order_id`` was persisted (order ≠ complete graph)."""
        tenant_id = sample_tenant["tenant_id"]
        step_id, step_data = self._seed_pending(
            context_manager, tenant_id, sample_principal["principal_id"], "mb_manual"
        )
        self._claim_expired(tenant_id, "mb_manual", mark_invoked=True)
        with get_db_session() as session:
            # Even a persisted order id must not unlock auto-resume for a real adapter.
            session.add(
                MediaPackage(media_buy_id="mb_manual", package_id="pkg_1", package_config={"platform_order_id": "o1"})
            )
            session.commit()

        adapter_calls: list[str] = []

        def adapter():
            adapter_calls.append("call")
            return True, None

        with get_db_session() as session:
            outcome, msg = _resume(session, tenant_id, "mb_manual", step_id, step_data, adapter, replayable=False)
        assert outcome is FinalizeOutcome.RETRYING and "manual" in (msg or "")
        assert adapter_calls == []  # never re-invoked

        with get_db_session() as session:
            buy = MediaBuyRepository(session, tenant_id).get_by_id("mb_manual")
            assert buy is not None and buy.status == "finalizing"
            assert buy.finalize_recovery_mode == "manual_required"
            # The scan the scheduler uses excludes flagged buys — no hot loop.
            recoverable = MediaBuyRepository.get_finalizing_recoverable(session, datetime.datetime.now(UTC))
            assert all(b.media_buy_id != "mb_manual" for b in recoverable)

        # A second reconciler pass must not touch it (NOT_CLAIMED, still no adapter call).
        with get_db_session() as session:
            outcome2, _ = _resume(session, tenant_id, "mb_manual", step_id, step_data, adapter, replayable=False)
        assert outcome2 is FinalizeOutcome.NOT_CLAIMED
        assert adapter_calls == []

    def test_crash_after_marker_on_replay_capable_adapter_auto_resumes(
        self, integration_db, sample_tenant, sample_principal, context_manager
    ):
        """Marker present + full-replay adapter (mock): safe to re-run the whole create
        workflow — the reconciler resumes and completes."""
        tenant_id = sample_tenant["tenant_id"]
        step_id, step_data = self._seed_pending(
            context_manager, tenant_id, sample_principal["principal_id"], "mb_replay"
        )
        self._claim_expired(tenant_id, "mb_replay", mark_invoked=True)

        adapter_calls: list[str] = []

        def adapter():
            adapter_calls.append("call")
            return True, None

        with get_db_session() as session:
            outcome, _ = _resume(session, tenant_id, "mb_replay", step_id, step_data, adapter, replayable=True)
        assert outcome is FinalizeOutcome.APPLIED
        assert adapter_calls == ["call"]
        with get_db_session() as session:
            buy = MediaBuyRepository(session, tenant_id).get_by_id("mb_replay")
            assert buy is not None and buy.status == "active"

    # ── Ownership races (reviewer-required) ──────────────────────────────

    def test_reconciler_does_not_touch_a_live_workers_buy(
        self, integration_db, sample_tenant, sample_principal, context_manager
    ):
        """A worker blocked INSIDE the adapter past a reconciler pass: the reconciler
        must neither invoke the adapter nor flag manual (the lease is unexpired) —
        the adapter runs exactly once and the worker completes normally."""
        tenant_id = sample_tenant["tenant_id"]
        step_id, step_data = self._seed_pending(context_manager, tenant_id, sample_principal["principal_id"], "mb_live")

        adapter_entered = threading.Event()
        adapter_release = threading.Event()
        adapter_calls: list[str] = []

        def blocked_adapter():
            adapter_calls.append("worker")
            adapter_entered.set()
            assert adapter_release.wait(timeout=30), "test deadlock: adapter never released"
            return True, None

        t, worker_result = _start_approval_worker(tenant_id, "mb_live", step_id, step_data, blocked_adapter)
        try:
            assert adapter_entered.wait(timeout=30), "worker never reached the adapter"

            # A reconciler pass fires while the worker is mid-adapter.
            def reconciler_adapter():
                adapter_calls.append("reconciler")
                return True, None

            with get_db_session() as session:
                outcome, _ = _resume(
                    session, tenant_id, "mb_live", step_id, step_data, reconciler_adapter, replayable=True
                )
            assert outcome is FinalizeOutcome.NOT_CLAIMED  # unexpired lease → hands off
            with get_db_session() as session:
                buy = MediaBuyRepository(session, tenant_id).get_by_id("mb_live")
                assert buy is not None and buy.finalize_recovery_mode is None  # NOT flagged manual
        finally:
            adapter_release.set()
            t.join(timeout=60)

        assert not t.is_alive(), "worker hung"
        assert "error" not in worker_result, f"worker failed: {worker_result.get('error')}"
        assert worker_result["outcome"] is FinalizeOutcome.APPLIED
        assert adapter_calls == ["worker"]  # exactly once, by the worker
        with get_db_session() as session:
            buy = MediaBuyRepository(session, tenant_id).get_by_id("mb_live")
            assert buy is not None and buy.status == "active"
            step = WorkflowRepository(session, tenant_id).get_by_step_id(step_id)
            assert step is not None and step.status == "completed"

    def test_stale_worker_returning_after_takeover_does_nothing(
        self, integration_db, sample_tenant, sample_principal, context_manager
    ):
        """A worker's lease expires mid-adapter and a reconciler takes over and
        completes; when the stale worker's adapter finally returns, BOTH its failure
        transition and its success publish lose the lease CAS — it must not publish,
        mark failed, terminalize, or emit a second artifact."""
        tenant_id = sample_tenant["tenant_id"]
        step_id, step_data = self._seed_pending(
            context_manager, tenant_id, sample_principal["principal_id"], "mb_stale"
        )

        adapter_entered = threading.Event()
        adapter_release = threading.Event()

        def slow_adapter():
            adapter_entered.set()
            assert adapter_release.wait(timeout=30), "test deadlock: adapter never released"
            return True, None

        t, worker_result = _start_approval_worker(tenant_id, "mb_stale", step_id, step_data, slow_adapter)
        try:
            assert adapter_entered.wait(timeout=30)
            # Force the worker's lease to expire while it is inside the adapter.
            _expire_lease_now(tenant_id, "mb_stale")
            # Reconciler takes over (marker is set; use a replay-capable adapter) and completes.
            with get_db_session() as session:
                outcome, _ = _resume(
                    session, tenant_id, "mb_stale", step_id, step_data, lambda: (True, None), replayable=True
                )
            assert outcome is FinalizeOutcome.APPLIED
            with get_db_session() as session:
                step = WorkflowRepository(session, tenant_id).get_by_step_id(step_id)
                assert step is not None and step.status == "completed"
                artifact_after_takeover = step.response_data
                revision_after_takeover = MediaBuyRepository(session, tenant_id).get_by_id("mb_stale").revision
        finally:
            adapter_release.set()
            t.join(timeout=60)

        assert not t.is_alive(), "stale worker hung"
        assert "error" not in worker_result, f"stale worker failed: {worker_result.get('error')}"
        # The stale worker lost ownership → NOT_CLAIMED, and it changed NOTHING.
        assert worker_result["outcome"] is FinalizeOutcome.NOT_CLAIMED
        with get_db_session() as session:
            buy = MediaBuyRepository(session, tenant_id).get_by_id("mb_stale")
            assert buy is not None and buy.status == "active"
            assert buy.revision == revision_after_takeover  # no extra bump
            step = WorkflowRepository(session, tenant_id).get_by_step_id(step_id)
            assert step is not None and step.status == "completed"
            assert step.response_data == artifact_after_takeover  # artifact untouched (no re-emit path ran)

    # ── Uncertain-before-mutation + self-heal ────────────────────────────

    def test_adapter_uncertain_keeps_automatic_retry_path(
        self, integration_db, sample_tenant, sample_principal, context_manager
    ):
        """``AdapterIdempotencyUncertain`` (guaranteed no remote mutation): the approval
        returns RETRYING, the invoked marker is cleared and the lease released, so the
        next reconciler pass re-attempts automatically — for ANY adapter."""
        tenant_id = sample_tenant["tenant_id"]
        step_id, step_data = self._seed_pending(
            context_manager, tenant_id, sample_principal["principal_id"], "mb_uncertain"
        )

        def uncertain_adapter():
            raise AdapterIdempotencyUncertain("GAM lookup failed")

        with get_db_session() as session:
            outcome, msg = finalize_media_buy_approval(
                session,
                tenant_id,
                media_buy_id="mb_uncertain",
                step_id=step_id,
                step_data=step_data,
                compute_target=lambda _mb: "active",
                run_adapter=uncertain_adapter,
                expected_status="pending_approval",
                approved_by="admin",
                approved_at=datetime.datetime.now(UTC),
            )
        assert outcome is FinalizeOutcome.RETRYING and "lookup failed" in (msg or "")

        with get_db_session() as session:
            buy = MediaBuyRepository(session, tenant_id).get_by_id("mb_uncertain")
            assert buy is not None and buy.status == "finalizing"
            assert buy.finalize_adapter_invoked_at is None  # cleared: nothing remote happened
            assert buy.finalize_lease_id is None  # released: no TTL wait
            assert buy.finalize_recovery_mode is None  # AUTOMATIC path

        # Next reconciler pass re-attempts and completes — even for a real adapter.
        with get_db_session() as session:
            outcome2, _ = _resume(
                session, tenant_id, "mb_uncertain", step_id, step_data, lambda: (True, None), replayable=False
            )
        assert outcome2 is FinalizeOutcome.APPLIED

    def test_slow_worker_publish_self_heals_manual_flag(
        self, integration_db, sample_tenant, sample_principal, context_manager
    ):
        """A reconciler flags manual_required while a slow (lease-expired but alive)
        worker is still running; the worker's successful publish — its own lease was
        never stolen — clears the flag (self-heal)."""
        tenant_id = sample_tenant["tenant_id"]
        step_id, step_data = self._seed_pending(context_manager, tenant_id, sample_principal["principal_id"], "mb_heal")

        adapter_entered = threading.Event()
        adapter_release = threading.Event()

        def slow_adapter():
            adapter_entered.set()
            assert adapter_release.wait(timeout=30)
            return True, None

        t, worker_result = _start_approval_worker(tenant_id, "mb_heal", step_id, step_data, slow_adapter)
        try:
            assert adapter_entered.wait(timeout=30)
            _expire_lease_now(tenant_id, "mb_heal")
            # Reconciler (non-replayable adapter, marker set) flags manual — WITHOUT
            # stealing the worker's lease id.
            with get_db_session() as session:
                outcome, _ = _resume(
                    session, tenant_id, "mb_heal", step_id, step_data, lambda: (True, None), replayable=False
                )
            assert outcome is FinalizeOutcome.RETRYING
            with get_db_session() as session:
                buy = MediaBuyRepository(session, tenant_id).get_by_id("mb_heal")
                assert buy is not None and buy.finalize_recovery_mode == "manual_required"
        finally:
            adapter_release.set()
            t.join(timeout=60)

        assert not t.is_alive() and "error" not in worker_result
        assert worker_result["outcome"] is FinalizeOutcome.APPLIED  # its own lease survived
        with get_db_session() as session:
            buy = MediaBuyRepository(session, tenant_id).get_by_id("mb_heal")
            assert buy is not None and buy.status == "active"
            assert buy.finalize_recovery_mode is None  # self-healed on publish

    # ── Step-less creative-unblock path + happy path ─────────────────────

    def test_stepless_resume_completes_without_step(self, integration_db, sample_tenant, sample_principal):
        """The step-less path (creative-unblock with no async buyer task) recovers the
        same way: a marker-absent stranding resumes to the serving status."""
        tenant_id = sample_tenant["tenant_id"]
        with MediaBuyUoW(tenant_id) as uow:
            uow.media_buys.create(
                make_media_buy(tenant_id, sample_principal["principal_id"], "mb_nostep", status="pending_creatives")
            )
        with get_db_session() as session:
            repo = MediaBuyRepository(session, tenant_id)
            claim = repo.claim_finalizing(
                media_buy_id="mb_nostep", expected_status="pending_creatives", lease_ttl_seconds=3600
            )
            assert claim is not None
            claim[0].finalize_lease_expires_at = datetime.datetime.now(UTC) - datetime.timedelta(seconds=1)
            session.commit()

        with get_db_session() as session:
            outcome, _ = _resume(session, tenant_id, "mb_nostep", None, None, lambda: (True, None), replayable=False)
        assert outcome is FinalizeOutcome.APPLIED
        with get_db_session() as session:
            buy = MediaBuyRepository(session, tenant_id).get_by_id("mb_nostep")
            assert buy is not None and buy.status == "active" and buy.finalize_lease_id is None

    def test_happy_path_bumps_revision_once_and_stamps_confirmed_at(
        self, integration_db, sample_tenant, sample_principal, context_manager
    ):
        """No crash: pending_approval → finalizing → active in one call; adapter exactly
        once; revision advances exactly one (claim bumps; owned publish does not); all
        finalize state cleared."""
        tenant_id = sample_tenant["tenant_id"]
        step_id, step_data = self._seed_pending(
            context_manager, tenant_id, sample_principal["principal_id"], "mb_happy"
        )
        with get_db_session() as session:
            before = MediaBuyRepository(session, tenant_id).get_by_id("mb_happy").revision

        adapter_calls: list[str] = []

        def adapter():
            adapter_calls.append("call")
            return True, None

        with get_db_session() as session:
            outcome, _ = finalize_media_buy_approval(
                session,
                tenant_id,
                media_buy_id="mb_happy",
                step_id=step_id,
                step_data=step_data,
                compute_target=lambda _mb: "active",
                run_adapter=adapter,
                expected_status="pending_approval",
                approved_by="admin",
                approved_at=datetime.datetime.now(UTC),
            )

        assert outcome is FinalizeOutcome.APPLIED
        assert adapter_calls == ["call"]
        with get_db_session() as session:
            buy = MediaBuyRepository(session, tenant_id).get_by_id("mb_happy")
            assert buy is not None and buy.status == "active"
            assert buy.confirmed_at is not None
            assert buy.revision == before + 1  # exactly one advance for the whole approval
            assert buy.finalize_lease_id is None
            assert buy.finalize_adapter_invoked_at is None
            assert buy.finalize_recovery_mode is None
