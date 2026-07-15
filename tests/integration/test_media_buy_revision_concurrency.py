"""Integration tests for media-buy optimistic-concurrency revision integrity.

These pin the four correctness properties of the revision lock (AdCP 3.1.1
optimistic concurrency, PR #1546 round-2 blocker B2):

1. Two-session barrier — a ``SELECT ... FOR UPDATE`` in session A holds the row
   lock until A commits; session B blocks on the same row and, once it acquires,
   reads A's INCREMENTED revision. Proves both ``.populate_existing()`` (B has
   the row cached at the old revision and must still observe the fresh value)
   and that the lock is held to commit.
2. Regression — creating a workflow step between the lock and the increment must
   NOT release the lock. The WorkflowContextManager runs on an isolated session,
   so its commit/close cannot touch A's FOR UPDATE lock.
3. Out-of-band increment — an out-of-band status transition bumps the revision
   through the centralized ``apply_status_transition``, so a buyer holding a
   now-stale revision conflicts on its next update.
4. Read exposure — ``get_media_buys`` carries each buy's ``revision`` on the wire.

The barrier/regression tests spawn two threads; each ``MediaBuyUoW`` opens its
own thread-local session (distinct connection), so the FOR UPDATE block is real.
"""

import threading
import time
import uuid

import pytest

from src.core.database.repositories import MediaBuyUoW
from src.core.exceptions import AdCPConflictError

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

# The winner holds the lock this long before committing; the loser must observe
# a wait at least this comfortably-below fraction of it (thread scheduling jitter).
_HOLD_SECONDS = 0.5
_MIN_BLOCKED_SECONDS = 0.3


def _seed_buy(*, revision: int = 1, status: str = "active") -> tuple[str, str, str]:
    """Commit a tenant + principal + one MediaBuy; return (tenant, principal, buy) ids."""
    from tests.helpers import seed_media_buy

    tenant_id = f"rev_t_{uuid.uuid4().hex[:6]}"
    principal_id = f"rev_p_{uuid.uuid4().hex[:6]}"
    buy_id = f"mb_rev_{uuid.uuid4().hex[:8]}"
    seed_media_buy(tenant_id, principal_id, buy_id, status=status, revision=revision)
    return tenant_id, principal_id, buy_id


class TestRevisionTwoSessionBarrier:
    """A locks + increments and holds; B blocks then reads A's incremented revision."""

    def test_locker_blocks_second_session_until_commit(self, integration_db):
        tenant_id, _principal_id, buy_id = _seed_buy(revision=1)

        a_holding = threading.Event()
        b_attempting = threading.Event()
        state: dict[str, object] = {}

        def session_a() -> None:
            try:
                with MediaBuyUoW(tenant_id) as uow_a:
                    assert uow_a.media_buys is not None
                    mb = uow_a.media_buys.lock_for_revision_check(buy_id, expected_revision=1)
                    uow_a.media_buys.increment_revision(mb)  # revision -> 2, lock still held
                    a_holding.set()
                    b_attempting.wait(timeout=5)
                    time.sleep(_HOLD_SECONDS)  # keep the lock while B blocks in FOR UPDATE
                    # UoW __exit__ commits here, releasing the lock.
            except Exception as exc:  # surface to the assertions
                state["a_error"] = exc
                a_holding.set()

        def session_b() -> None:
            try:
                assert a_holding.wait(timeout=5), "session A never acquired the lock"
                with MediaBuyUoW(tenant_id) as uow_b:
                    assert uow_b.media_buys is not None
                    # Load the row into B's identity map at the OLD revision so a
                    # missing populate_existing() would return the stale value.
                    pre = uow_b.media_buys.get_by_id(buy_id)
                    assert pre is not None
                    state["b_pre_revision"] = pre.revision
                    b_attempting.set()
                    started = time.monotonic()
                    locked = uow_b.media_buys.lock_for_revision_check(buy_id, expected_revision=None)
                    state["b_wait_seconds"] = time.monotonic() - started
                    state["b_revision"] = locked.revision
            except Exception as exc:
                state["b_error"] = exc

        ta = threading.Thread(target=session_a)
        tb = threading.Thread(target=session_b)
        ta.start()
        tb.start()
        ta.join(timeout=15)
        tb.join(timeout=15)

        assert "a_error" not in state, f"session A failed: {state.get('a_error')!r}"
        assert "b_error" not in state, f"session B failed: {state.get('b_error')!r}"
        assert state["b_pre_revision"] == 1, "test setup: B should see the pre-increment revision"
        # B blocked in FOR UPDATE for roughly the hold window — proves the lock
        # was held to commit, not released early.
        assert state["b_wait_seconds"] >= _MIN_BLOCKED_SECONDS, (
            f"session B did not block on the FOR UPDATE lock (waited {state['b_wait_seconds']:.3f}s)"
        )
        # B observes A's committed increment despite caching the stale row first
        # — this is the populate_existing() pin.
        assert state["b_revision"] == 2, (
            f"session B read revision {state['b_revision']}, expected A's incremented 2 "
            "(stale identity-map value means populate_existing() was dropped)"
        )


class TestWorkflowStepDoesNotReleaseLock:
    """A workflow-step write between lock and increment must not release the lock."""

    def test_create_workflow_step_keeps_lock_held(self, integration_db):
        from src.core.context_manager import get_context_manager

        tenant_id, principal_id, buy_id = _seed_buy(revision=1)

        a_holding = threading.Event()
        b_attempting = threading.Event()
        state: dict[str, object] = {}

        def session_a() -> None:
            try:
                ctx_manager = get_context_manager()
                with MediaBuyUoW(tenant_id) as uow_a:
                    assert uow_a.media_buys is not None
                    mb = uow_a.media_buys.lock_for_revision_check(buy_id, expected_revision=1)
                    # Emulate the update path: a context + workflow step are written
                    # between the lock and the increment. These commit and close on
                    # ContextManager's ISOLATED session; if they touched A's scoped
                    # session they would release the FOR UPDATE lock below.
                    ctx = ctx_manager.create_context(tenant_id, principal_id)
                    ctx_manager.create_workflow_step(
                        context_id=ctx.context_id,
                        step_type="tool_call",
                        owner="principal",
                        status="in_progress",
                        tool_name="update_media_buy",
                    )
                    uow_a.media_buys.increment_revision(mb)  # revision -> 2, lock must still be held
                    a_holding.set()
                    b_attempting.wait(timeout=5)
                    time.sleep(_HOLD_SECONDS)
            except Exception as exc:
                state["a_error"] = exc
                a_holding.set()

        def session_b() -> None:
            try:
                assert a_holding.wait(timeout=5), "session A never acquired the lock"
                with MediaBuyUoW(tenant_id) as uow_b:
                    assert uow_b.media_buys is not None
                    b_attempting.set()
                    started = time.monotonic()
                    locked = uow_b.media_buys.lock_for_revision_check(buy_id, expected_revision=None)
                    state["b_wait_seconds"] = time.monotonic() - started
                    state["b_revision"] = locked.revision
            except Exception as exc:
                state["b_error"] = exc

        ta = threading.Thread(target=session_a)
        tb = threading.Thread(target=session_b)
        ta.start()
        tb.start()
        ta.join(timeout=15)
        tb.join(timeout=15)

        assert "a_error" not in state, f"session A failed: {state.get('a_error')!r}"
        assert "b_error" not in state, f"session B failed: {state.get('b_error')!r}"
        # The workflow-step commit ran on the isolated session, so A kept the lock:
        # B blocked until A's UoW committed and then read the committed increment.
        assert state["b_wait_seconds"] >= _MIN_BLOCKED_SECONDS, (
            f"the workflow-step write released A's lock early (B waited {state['b_wait_seconds']:.3f}s)"
        )
        assert state["b_revision"] == 2, (
            f"session B read revision {state['b_revision']}, expected 2 — the workflow-step "
            "commit must not have released A's lock before the increment committed"
        )


class TestOutOfBandTransitionBumpsRevision:
    """An out-of-band status transition bumps the revision, so a stale buyer conflicts."""

    def test_stale_revision_after_out_of_band_transition_conflicts(self, integration_db):
        tenant_id, _principal_id, buy_id = _seed_buy(revision=1, status="active")

        # Out-of-band writer (scheduler/admin) transitions status via the
        # centralized apply_status_transition, which bumps the revision.
        with MediaBuyUoW(tenant_id) as uow:
            assert uow.media_buys is not None
            mb = uow.media_buys.get_by_id(buy_id)
            assert mb is not None
            new_revision = uow.media_buys.apply_status_transition(mb, "paused")
            assert new_revision == 2, "apply_status_transition must increment the revision"

        # A buyer still holding revision=1 now conflicts on its next update.
        with pytest.raises(AdCPConflictError) as exc_info:
            with MediaBuyUoW(tenant_id) as uow2:
                assert uow2.media_buys is not None
                uow2.media_buys.lock_for_revision_check(buy_id, expected_revision=1)

        details = exc_info.value.details or {}
        assert details.get("current_version") == 2, (
            "the conflict must report the post-transition revision the buyer must re-read"
        )
        assert details.get("expected_version") == 1


class TestReadExposesRevision:
    """get_media_buys carries the buy's revision on the response."""

    def test_response_carries_revision(self, integration_db):
        from src.core.schemas import GetMediaBuysRequest
        from src.core.tools.media_buy_list import _get_media_buys_impl
        from tests.factories import MediaBuyFactory
        from tests.harness.media_buy_dual import MediaBuyDualEnv

        with MediaBuyDualEnv() as env:
            tenant, principal, _product, _ = env.setup_media_buy_data()
            # A non-default revision proves the read reflects the stored column,
            # not a hardcoded schema default.
            buy = MediaBuyFactory(
                tenant=tenant,
                principal=principal,
                status="active",
                revision=4,
            )
            env._commit_factory_data()

            get_req = GetMediaBuysRequest(media_buy_ids=[buy.media_buy_id])
            response = _get_media_buys_impl(get_req, identity=env.identity)

        assert len(response.media_buys) == 1, f"Expected the buy; errors: {response.errors}"
        assert response.media_buys[0].revision == 4, (
            "get_media_buys must expose the stored revision as the buyer's concurrency token"
        )
