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
- ``AdapterPostMutationIncomplete`` (remote mutations exist, workflow incomplete)
  parks the buy manual with the owner's lease RELEASED; re-approval of a manual buy
  is fenced against a possibly-still-alive prior owner (lease absent, or expired
  beyond the abandonment grace).

DB access is consolidated in the module helpers below (one guard-allowlist entry per
helper instead of one per test) — tests themselves never open sessions.
"""

import datetime
import threading
from datetime import UTC
from types import SimpleNamespace

import pytest

from src.adapters.base import AdapterIdempotencyUncertain, AdapterPostMutationIncomplete
from src.admin.services.media_buy_completion import (
    FinalizeOutcome,
    finalize_media_buy_approval,
    resume_finalizing_media_buy,
)
from src.core.context_manager import ContextManager
from src.core.database.database_session import get_db_session
from src.core.database.models import MEDIA_BUY_RECOVERY_MANUAL, MediaPackage
from src.core.database.repositories import MediaBuyRepository
from src.core.database.repositories.workflow import WorkflowRepository
from tests.integration.conftest import seed_pending_buy_and_step

# ── Session-owning module helpers (each carries ONE guard-allowlist entry) ──


def _resume(tenant_id, media_buy_id, step_id, step_data, run_adapter, *, replayable=False, replay_probe=None):
    """Drive the reconciler entry on a fresh session; returns (outcome, msg)."""
    with get_db_session() as session:
        return resume_finalizing_media_buy(
            session,
            tenant_id,
            media_buy_id=media_buy_id,
            step_id=step_id,
            step_data=step_data,
            run_adapter=run_adapter,
            adapter_supports_replay=replay_probe if replay_probe is not None else (lambda: replayable),
        )


def _finalize_approval(tenant_id, media_buy_id, step_id, step_data, run_adapter, *, expected_status="pending_approval"):
    """Drive the real approval finalizer on a fresh session; returns (outcome, msg)."""
    with get_db_session() as session:
        return finalize_media_buy_approval(
            session,
            tenant_id,
            media_buy_id=media_buy_id,
            step_id=step_id,
            step_data=step_data,
            compute_target=lambda _mb: "active",
            run_adapter=run_adapter,
            expected_status=expected_status,
            approved_by="admin",
            approved_at=datetime.datetime.now(UTC),
        )


def _buy_snapshot(tenant_id: str, media_buy_id: str) -> SimpleNamespace:
    """Committed-state snapshot of the fields these tests assert on."""
    with get_db_session() as session:
        buy = MediaBuyRepository(session, tenant_id).get_by_id(media_buy_id)
        assert buy is not None
        return SimpleNamespace(
            status=buy.status,
            revision=buy.revision,
            approved_at=buy.approved_at,
            confirmed_at=buy.confirmed_at,
            finalize_lease_id=buy.finalize_lease_id,
            finalize_lease_expires_at=buy.finalize_lease_expires_at,
            finalize_adapter_invoked_at=buy.finalize_adapter_invoked_at,
            finalize_recovery_mode=buy.finalize_recovery_mode,
        )


def _step_snapshot(tenant_id: str, step_id: str) -> SimpleNamespace:
    with get_db_session() as session:
        step = WorkflowRepository(session, tenant_id).get_by_step_id(step_id)
        assert step is not None
        return SimpleNamespace(status=step.status, response_data=step.response_data)


def _recoverable_ids() -> set[str]:
    """The media_buy_ids the scheduler's reconciler scan would pick up right now."""
    with get_db_session() as session:
        return {
            b.media_buy_id for b in MediaBuyRepository.get_finalizing_recoverable(session, datetime.datetime.now(UTC))
        }


def _claim_expired(
    tenant_id: str,
    media_buy_id: str,
    *,
    mark_invoked: bool,
    expired_by_seconds: int = 1,
    expected_status: str = "pending_approval",
) -> str:
    """Model a crashed worker: claimed ``finalizing`` with an already-expired lease,
    optionally past the adapter-invoked marker. ``expired_by_seconds`` controls HOW
    long ago the lease expired (the abandonment-grace fencing pins need old leases)."""
    with get_db_session() as session:
        repo = MediaBuyRepository(session, tenant_id)
        # Model the real approve claim: phase 1 always stamps the approval instant.
        claim = repo.claim_finalizing(
            media_buy_id,
            expected_status=expected_status,
            lease_ttl_seconds=3600,
            approved_at=datetime.datetime.now(UTC),
            approved_by="admin",
        )
        assert claim is not None
        buy, lease_id = claim
        if mark_invoked:
            assert repo.set_finalize_adapter_invoked(media_buy_id, lease_id)
        buy.finalize_lease_expires_at = datetime.datetime.now(UTC) - datetime.timedelta(seconds=expired_by_seconds)
        session.commit()
    return lease_id


def _expire_lease_now(tenant_id: str, media_buy_id: str, *, expired_by_seconds: int = 1) -> None:
    """Force the buy's phase-2 lease to read as expired (models a slow/stale owner)."""
    with get_db_session() as session:
        buy = MediaBuyRepository(session, tenant_id).get_by_id(media_buy_id)
        assert buy is not None
        buy.finalize_lease_expires_at = datetime.datetime.now(UTC) - datetime.timedelta(seconds=expired_by_seconds)
        session.commit()


def _clear_recovery_and_marker(tenant_id: str, media_buy_id: str) -> None:
    """The documented operator remediation: clear BOTH flags after remote reconciliation."""
    with get_db_session() as session:
        buy = MediaBuyRepository(session, tenant_id).get_by_id(media_buy_id)
        assert buy is not None
        buy.finalize_recovery_mode = None
        buy.finalize_adapter_invoked_at = None
        session.commit()


def _seed_platform_order_id(tenant_id: str, media_buy_id: str, package_id: str) -> None:
    """Persist a stale platform order/line-item id pair on a package (crash artifact)."""
    with get_db_session() as session:
        session.add(
            MediaPackage(
                media_buy_id=media_buy_id,
                package_id=package_id,
                package_config={"platform_order_id": "stale_order", "platform_line_item_id": "stale_li"},
            )
        )
        session.commit()


def _package_configs(tenant_id: str, media_buy_id: str) -> list[dict]:
    with get_db_session() as session:
        return [dict(p.package_config or {}) for p in MediaBuyRepository(session, tenant_id).get_packages(media_buy_id)]


def _try_reapproval_claim(tenant_id: str, media_buy_id: str) -> bool:
    """Attempt the widened re-approval claim shape (rolled back either way)."""
    with get_db_session() as session:
        claim = MediaBuyRepository(session, tenant_id).claim_finalizing(
            media_buy_id, expected_status=("pending_approval", "finalizing"), lease_ttl_seconds=3600
        )
        session.rollback()
        return claim is not None


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
            result["outcome"] = _finalize_approval(tenant_id, media_buy_id, step_id, step_data, run_adapter)[0]
        except BaseException as exc:  # noqa: BLE001 - surfaced to the main thread
            result["error"] = exc

    thread = threading.Thread(target=worker, name=f"approval-worker-{media_buy_id}")
    thread.start()
    return thread, result


def _seed_pending_creatives_buy(tenant_id: str, principal_id: str, media_buy_id: str) -> None:
    """Seed a buy held at pending_creatives (the step-less creative-unblock shape)."""
    from src.core.database.repositories import MediaBuyUoW
    from tests.integration.conftest import make_media_buy

    with MediaBuyUoW(tenant_id) as uow:
        uow.media_buys.create(make_media_buy(tenant_id, principal_id, media_buy_id, status="pending_creatives"))


@pytest.mark.requires_db
class TestApprovalCrashRecovery:
    @pytest.fixture
    def context_manager(self):
        return ContextManager()

    def _seed_pending(self, context_manager, tenant_id, principal_id, media_buy_id) -> tuple[str, dict]:
        return seed_pending_buy_and_step(context_manager, tenant_id, principal_id, media_buy_id)

    # ── Crash windows ────────────────────────────────────────────────────

    def test_crash_before_marker_is_auto_recoverable_even_for_real_adapters(
        self, integration_db, sample_tenant, sample_principal, context_manager
    ):
        """A crash AFTER the claim but BEFORE the adapter-invoked marker means nothing
        remote happened — the reconciler safely re-attempts even for a non-replayable
        (real) adapter, and completes with exactly one adapter invocation."""
        tenant_id = sample_tenant["tenant_id"]
        step_id, step_data = self._seed_pending(context_manager, tenant_id, sample_principal["principal_id"], "mb_pre")
        self._claim = _claim_expired(tenant_id, "mb_pre", mark_invoked=False)

        adapter_calls: list[str] = []

        def adapter():
            adapter_calls.append("call")
            return True, None

        outcome, _ = _resume(tenant_id, "mb_pre", step_id, step_data, adapter, replayable=False)

        assert outcome is FinalizeOutcome.APPLIED
        assert adapter_calls == ["call"]
        buy = _buy_snapshot(tenant_id, "mb_pre")
        assert buy.status == "active"
        assert buy.confirmed_at is not None and buy.confirmed_at == buy.approved_at  # B4 preserved
        assert buy.finalize_lease_id is None and buy.finalize_adapter_invoked_at is None
        step = _step_snapshot(tenant_id, step_id)
        assert step.status == "completed" and step.response_data is not None

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
        _claim_expired(tenant_id, "mb_manual", mark_invoked=True)
        # Even a persisted order id must not unlock auto-resume for a real adapter.
        _seed_platform_order_id(tenant_id, "mb_manual", "pkg_1")

        adapter_calls: list[str] = []

        def adapter():
            adapter_calls.append("call")
            return True, None

        outcome, msg = _resume(tenant_id, "mb_manual", step_id, step_data, adapter, replayable=False)
        assert outcome is FinalizeOutcome.RETRYING and "manual" in (msg or "")
        assert adapter_calls == []  # never re-invoked

        buy = _buy_snapshot(tenant_id, "mb_manual")
        assert buy.status == "finalizing"
        assert buy.finalize_recovery_mode == MEDIA_BUY_RECOVERY_MANUAL
        # The scan the scheduler uses excludes flagged buys — no hot loop.
        assert "mb_manual" not in _recoverable_ids()

        # A second reconciler pass must not touch it (NOT_CLAIMED, still no adapter call).
        outcome2, _ = _resume(tenant_id, "mb_manual", step_id, step_data, adapter, replayable=False)
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
        _claim_expired(tenant_id, "mb_replay", mark_invoked=True)

        adapter_calls: list[str] = []

        def adapter():
            adapter_calls.append("call")
            return True, None

        outcome, _ = _resume(tenant_id, "mb_replay", step_id, step_data, adapter, replayable=True)
        assert outcome is FinalizeOutcome.APPLIED
        assert adapter_calls == ["call"]
        assert _buy_snapshot(tenant_id, "mb_replay").status == "active"

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

            outcome, _ = _resume(tenant_id, "mb_live", step_id, step_data, reconciler_adapter, replayable=True)
            assert outcome is FinalizeOutcome.NOT_CLAIMED  # unexpired lease → hands off
            assert _buy_snapshot(tenant_id, "mb_live").finalize_recovery_mode is None  # NOT flagged manual
        finally:
            adapter_release.set()
            t.join(timeout=60)

        assert not t.is_alive(), "worker hung"
        assert "error" not in worker_result, f"worker failed: {worker_result.get('error')}"
        assert worker_result["outcome"] is FinalizeOutcome.APPLIED
        assert adapter_calls == ["worker"]  # exactly once, by the worker
        assert _buy_snapshot(tenant_id, "mb_live").status == "active"
        assert _step_snapshot(tenant_id, step_id).status == "completed"

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
            outcome, _ = _resume(tenant_id, "mb_stale", step_id, step_data, lambda: (True, None), replayable=True)
            assert outcome is FinalizeOutcome.APPLIED
            step_after = _step_snapshot(tenant_id, step_id)
            assert step_after.status == "completed"
            revision_after_takeover = _buy_snapshot(tenant_id, "mb_stale").revision
        finally:
            adapter_release.set()
            t.join(timeout=60)

        assert not t.is_alive(), "stale worker hung"
        assert "error" not in worker_result, f"stale worker failed: {worker_result.get('error')}"
        # The stale worker lost ownership → NOT_CLAIMED, and it changed NOTHING.
        assert worker_result["outcome"] is FinalizeOutcome.NOT_CLAIMED
        buy = _buy_snapshot(tenant_id, "mb_stale")
        assert buy.status == "active"
        assert buy.revision == revision_after_takeover  # no extra bump
        step_final = _step_snapshot(tenant_id, step_id)
        assert step_final.status == "completed"
        assert step_final.response_data == step_after.response_data  # artifact untouched

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

        outcome, msg = _finalize_approval(tenant_id, "mb_uncertain", step_id, step_data, uncertain_adapter)
        assert outcome is FinalizeOutcome.RETRYING and "lookup failed" in (msg or "")

        buy = _buy_snapshot(tenant_id, "mb_uncertain")
        assert buy.status == "finalizing"
        assert buy.finalize_adapter_invoked_at is None  # cleared: nothing remote happened
        assert buy.finalize_lease_id is None  # released: no TTL wait
        assert buy.finalize_recovery_mode is None  # AUTOMATIC path

        # Next reconciler pass re-attempts and completes — even for a real adapter.
        outcome2, _ = _resume(tenant_id, "mb_uncertain", step_id, step_data, lambda: (True, None), replayable=False)
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
            outcome, _ = _resume(tenant_id, "mb_heal", step_id, step_data, lambda: (True, None), replayable=False)
            assert outcome is FinalizeOutcome.RETRYING
            assert _buy_snapshot(tenant_id, "mb_heal").finalize_recovery_mode == MEDIA_BUY_RECOVERY_MANUAL
        finally:
            adapter_release.set()
            t.join(timeout=60)

        assert not t.is_alive() and "error" not in worker_result
        assert worker_result["outcome"] is FinalizeOutcome.APPLIED  # its own lease survived
        buy = _buy_snapshot(tenant_id, "mb_heal")
        assert buy.status == "active"
        assert buy.finalize_recovery_mode is None  # self-healed on publish

    def test_uncertain_clears_a_concurrent_manual_flag(
        self, integration_db, sample_tenant, sample_principal, context_manager
    ):
        """An expired-lease worker whose adapter raises ``AdapterIdempotencyUncertain``
        (guaranteed: nothing remote happened) must return the buy to the FULLY
        automatic state — including clearing a ``manual_required`` flag a reconciler
        set while the worker was in flight — and the next resume completes."""
        tenant_id = sample_tenant["tenant_id"]
        step_id, step_data = self._seed_pending(
            context_manager, tenant_id, sample_principal["principal_id"], "mb_unmanual"
        )

        adapter_entered = threading.Event()
        adapter_release = threading.Event()

        def uncertain_after_release():
            adapter_entered.set()
            assert adapter_release.wait(timeout=30)
            raise AdapterIdempotencyUncertain("lookup failed mid-flight")

        t, worker_result = _start_approval_worker(tenant_id, "mb_unmanual", step_id, step_data, uncertain_after_release)
        try:
            assert adapter_entered.wait(timeout=30)
            _expire_lease_now(tenant_id, "mb_unmanual")
            # Reconciler flags manual (marker set, real adapter, expired lease).
            outcome, _ = _resume(tenant_id, "mb_unmanual", step_id, step_data, lambda: (True, None), replayable=False)
            assert outcome is FinalizeOutcome.RETRYING
            assert _buy_snapshot(tenant_id, "mb_unmanual").finalize_recovery_mode == MEDIA_BUY_RECOVERY_MANUAL
        finally:
            adapter_release.set()
            t.join(timeout=60)

        assert not t.is_alive() and "error" not in worker_result
        assert worker_result["outcome"] is FinalizeOutcome.RETRYING  # the worker's Uncertain path
        buy = _buy_snapshot(tenant_id, "mb_unmanual")
        assert buy.status == "finalizing"
        # FULLY automatic again — Uncertain outlives the stale manual flag.
        assert buy.finalize_recovery_mode is None
        assert buy.finalize_adapter_invoked_at is None
        assert buy.finalize_lease_id is None

        # And the next reconciler pass completes it.
        outcome2, _ = _resume(tenant_id, "mb_unmanual", step_id, step_data, lambda: (True, None), replayable=False)
        assert outcome2 is FinalizeOutcome.APPLIED

    # ── Round N+6/N+7 pins: shortcut removal / remediation / probe / fencing ──

    def test_persisted_order_id_never_skips_the_adapter(
        self, integration_db, sample_tenant, sample_principal, context_manager
    ):
        """A persisted ``platform_order_id`` is NOT proof the remote workflow completed
        (it lands before creative upload and order approval). The re-approval shape —
        marker ABSENT, stale order id present — must RUN the adapter before publishing,
        never skip; and the stale per-package ids are reset for the fresh full create."""
        tenant_id = sample_tenant["tenant_id"]
        step_id, step_data = self._seed_pending(
            context_manager, tenant_id, sample_principal["principal_id"], "mb_orderid"
        )
        _claim_expired(tenant_id, "mb_orderid", mark_invoked=False)
        _seed_platform_order_id(tenant_id, "mb_orderid", "pkg_1")

        adapter_calls: list[str] = []

        def adapter():
            adapter_calls.append("call")
            # Pin the stale-id reset: by adapter time the partial identifiers are gone,
            # so a fresh full create cannot trip the persist mismatch guard.
            for config in _package_configs(tenant_id, "mb_orderid"):
                assert "platform_order_id" not in config
                assert "platform_line_item_id" not in config
            return True, None

        outcome, _ = _resume(tenant_id, "mb_orderid", step_id, step_data, adapter, replayable=False)

        assert outcome is FinalizeOutcome.APPLIED
        assert adapter_calls == ["call"]  # the adapter RAN — publish never rode a stale order id

    def test_documented_operator_remediation_recovers(
        self, integration_db, sample_tenant, sample_principal, context_manager
    ):
        """The documented remediation clears BOTH fields (recovery_mode AND the invoked
        marker) — after which the reconciler auto-recovers even a real adapter (the
        marker-absent window). Clearing only recovery_mode would immediately re-flag."""
        tenant_id = sample_tenant["tenant_id"]
        step_id, step_data = self._seed_pending(
            context_manager, tenant_id, sample_principal["principal_id"], "mb_operator"
        )
        _claim_expired(tenant_id, "mb_operator", mark_invoked=True)
        outcome, _ = _resume(tenant_id, "mb_operator", step_id, step_data, lambda: (True, None))
        assert outcome is FinalizeOutcome.RETRYING  # manual_required now set

        # The documented operator remediation: clear BOTH fields.
        _clear_recovery_and_marker(tenant_id, "mb_operator")

        adapter_calls: list[str] = []

        def adapter():
            adapter_calls.append("call")
            return True, None

        outcome2, _ = _resume(tenant_id, "mb_operator", step_id, step_data, adapter, replayable=False)
        assert outcome2 is FinalizeOutcome.APPLIED  # NOT re-flagged manual
        assert adapter_calls == ["call"]
        assert _buy_snapshot(tenant_id, "mb_operator").status == "active"

    def test_capability_probe_failure_goes_manual_once(
        self, integration_db, sample_tenant, sample_principal, context_manager
    ):
        """A capability-resolution exception must NOT escape to the scheduler (which
        would retry the same row every pass forever): it is treated conservatively as
        non-replayable → ``manual_required`` ONCE; the second pass skips entirely."""
        tenant_id = sample_tenant["tenant_id"]
        step_id, step_data = self._seed_pending(
            context_manager, tenant_id, sample_principal["principal_id"], "mb_probe"
        )
        _claim_expired(tenant_id, "mb_probe", mark_invoked=True)

        adapter_calls: list[str] = []

        def adapter():
            adapter_calls.append("call")
            return True, None

        def exploding_probe():
            raise RuntimeError("adapter resolution failed")

        outcome, msg = _resume(tenant_id, "mb_probe", step_id, step_data, adapter, replay_probe=exploding_probe)
        assert outcome is FinalizeOutcome.RETRYING and "manual" in (msg or "")  # no exception escaped
        assert adapter_calls == []
        assert _buy_snapshot(tenant_id, "mb_probe").finalize_recovery_mode == MEDIA_BUY_RECOVERY_MANUAL

        # Second pass: skipped entirely — no hot loop, probe not even consulted.
        outcome2, _ = _resume(tenant_id, "mb_probe", step_id, step_data, adapter, replay_probe=exploding_probe)
        assert outcome2 is FinalizeOutcome.NOT_CLAIMED
        assert adapter_calls == []

    def test_post_mutation_failure_preserves_reconciliation_signal(
        self, integration_db, sample_tenant, sample_principal, context_manager
    ):
        """A handled failure AFTER the remote order was created (creative upload /
        order approval — ``AdapterPostMutationIncomplete``) must NOT become a
        terminal ``failed`` that erases the finalization state: the buy stays
        ``finalizing`` with the invoked marker INTACT, flagged ``manual_required``
        (lease released), the step stays non-terminal, and the reconciler skips it."""
        tenant_id = sample_tenant["tenant_id"]
        step_id, step_data = self._seed_pending(
            context_manager, tenant_id, sample_principal["principal_id"], "mb_partial"
        )

        def post_mutation_failing_adapter():
            raise AdapterPostMutationIncomplete("order created; creative upload failed")

        outcome, msg = _finalize_approval(tenant_id, "mb_partial", step_id, step_data, post_mutation_failing_adapter)

        assert outcome is FinalizeOutcome.RETRYING and "manual" in (msg or "")
        buy = _buy_snapshot(tenant_id, "mb_partial")
        assert buy.status == "finalizing"  # NOT terminal failed
        assert buy.finalize_recovery_mode == MEDIA_BUY_RECOVERY_MANUAL
        assert buy.finalize_adapter_invoked_at is not None  # signal preserved
        assert buy.finalize_lease_id is None  # lease released by the owner itself
        assert _step_snapshot(tenant_id, step_id).status == "in_progress"  # not failed/terminal
        # Reconciler never re-touches it (manual_required excluded from the scan).
        assert "mb_partial" not in _recoverable_ids()

    def test_operator_reapproval_is_fenced_against_maybe_alive_workers(
        self, integration_db, sample_tenant, sample_principal, context_manager
    ):
        """Re-approving a ``manual_required`` buy is FENCED (#1637 round N+8):

        - A reconciler-flagged row keeps the expired lease of a possibly-still-alive
          worker → re-approval is REJECTED until the lease has been expired beyond
          the abandonment grace (no concurrent second adapter invocation).
        - An owner-flagged row (post-mutation failure released its own lease) is
          re-approvable immediately.
        - A plain in-flight ``finalizing`` buy is never re-claimable.
        """
        tenant_id = sample_tenant["tenant_id"]

        # (a) reconciler-flagged manual, lease expired only just now → FENCED.
        step_id, step_data = self._seed_pending(
            context_manager, tenant_id, sample_principal["principal_id"], "mb_fenced"
        )
        _claim_expired(tenant_id, "mb_fenced", mark_invoked=True, expired_by_seconds=1)
        outcome, _ = _resume(tenant_id, "mb_fenced", step_id, step_data, lambda: (True, None))
        assert outcome is FinalizeOutcome.RETRYING  # manual_required set, lease left in place
        assert not _try_reapproval_claim(tenant_id, "mb_fenced"), (
            "a freshly-expired lease may belong to a still-running worker — re-approval must be fenced"
        )

        # ...but once the lease has been expired beyond the abandonment grace → re-approvable.
        _expire_lease_now(tenant_id, "mb_fenced", expired_by_seconds=7200)  # > 3600s grace
        outcome_aged, _ = _finalize_approval(
            tenant_id,
            "mb_fenced",
            step_id,
            step_data,
            lambda: (True, None),
            expected_status=("pending_approval", "finalizing"),
        )
        assert outcome_aged is FinalizeOutcome.APPLIED
        assert _buy_snapshot(tenant_id, "mb_fenced").status == "active"

        # (b) owner-flagged manual (post-mutation failure; lease RELEASED) → immediate.
        step2_id, step2_data = self._seed_pending(
            context_manager, tenant_id, sample_principal["principal_id"], "mb_owner_manual"
        )

        def post_mutation_failing_adapter():
            raise AdapterPostMutationIncomplete("order created; approval failed")

        outcome_b, _ = _finalize_approval(
            tenant_id, "mb_owner_manual", step2_id, step2_data, post_mutation_failing_adapter
        )
        assert outcome_b is FinalizeOutcome.RETRYING
        assert _buy_snapshot(tenant_id, "mb_owner_manual").finalize_lease_id is None
        outcome_b2, _ = _finalize_approval(
            tenant_id,
            "mb_owner_manual",
            step2_id,
            step2_data,
            lambda: (True, None),
            expected_status=("pending_approval", "finalizing"),
        )
        assert outcome_b2 is FinalizeOutcome.APPLIED
        buy_b = _buy_snapshot(tenant_id, "mb_owner_manual")
        assert buy_b.status == "active"
        assert buy_b.finalize_recovery_mode is None and buy_b.finalize_adapter_invoked_at is None

        # (c) a plain in-flight finalizing buy (no manual flag) is never re-claimable.
        self._seed_pending(context_manager, tenant_id, sample_principal["principal_id"], "mb_inflight")
        _claim_expired(tenant_id, "mb_inflight", mark_invoked=False, expired_by_seconds=-3600)  # unexpired
        assert not _try_reapproval_claim(tenant_id, "mb_inflight")

    def test_stepless_resume_completes_without_step(self, integration_db, sample_tenant, sample_principal):
        """The step-less path (creative-unblock with no async buyer task) recovers the
        same way: a marker-absent stranding resumes to the serving status."""
        tenant_id = sample_tenant["tenant_id"]
        _seed_pending_creatives_buy(tenant_id, sample_principal["principal_id"], "mb_nostep")
        _claim_expired(tenant_id, "mb_nostep", mark_invoked=False, expected_status="pending_creatives")

        outcome, _ = _resume(tenant_id, "mb_nostep", None, None, lambda: (True, None), replayable=False)
        assert outcome is FinalizeOutcome.APPLIED
        buy = _buy_snapshot(tenant_id, "mb_nostep")
        assert buy.status == "active" and buy.finalize_lease_id is None

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
        before = _buy_snapshot(tenant_id, "mb_happy").revision

        adapter_calls: list[str] = []

        def adapter():
            adapter_calls.append("call")
            return True, None

        outcome, _ = _finalize_approval(tenant_id, "mb_happy", step_id, step_data, adapter)

        assert outcome is FinalizeOutcome.APPLIED
        assert adapter_calls == ["call"]
        buy = _buy_snapshot(tenant_id, "mb_happy")
        assert buy.status == "active"
        assert buy.confirmed_at is not None
        assert buy.revision == before + 1  # exactly one advance for the whole approval
        assert buy.finalize_lease_id is None
        assert buy.finalize_adapter_invoked_at is None
        assert buy.finalize_recovery_mode is None
