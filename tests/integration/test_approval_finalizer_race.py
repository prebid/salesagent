"""P1 (#1544): the approval finalizer is single-winner under concurrency.

Two admin requests that both preload a ``pending_approval`` buy must NOT both invoke the
adapter (duplicate remote orders) and must NOT overwrite each other's decision. The
finalizer CLAIMS the decision via a status compare-and-swap under the row lock
(``expected_status``); only the winner runs the adapter and terminalizes the step.

Harness mirrors ``test_two_concurrent_updates_same_token_one_wins_one_conflicts`` in
``test_media_buy_repository_writes.py``: a ``threading.Barrier`` aligns two threads, each
with its own session; shared ``outcomes``/``errors`` under a ``threading.Lock``.
"""

import threading
from datetime import UTC, datetime

import pytest

from src.admin.services.media_buy_completion import (
    FinalizeOutcome,
    finalize_media_buy_approval,
    finalize_media_buy_rejection,
)
from src.core.context_manager import ContextManager
from src.core.database.database_session import get_db_session
from src.core.database.repositories import MediaBuyRepository, MediaBuyUoW
from src.core.database.repositories.workflow import WorkflowRepository
from tests.integration.conftest import make_create_media_buy_step, make_media_buy


@pytest.mark.requires_db
class TestApprovalFinalizerRace:
    @pytest.fixture
    def context_manager(self):
        return ContextManager()

    def _seed_pending_buy_and_step(self, context_manager, tenant_id, principal_id, media_buy_id) -> tuple[str, dict]:
        with MediaBuyUoW(tenant_id) as uow:
            uow.media_buys.create(make_media_buy(tenant_id, principal_id, media_buy_id, status="pending_approval"))
        step = make_create_media_buy_step(
            context_manager, tenant_id, principal_id, media_buy_id=media_buy_id, status="in_progress"
        )
        step_data = {
            "step_id": step.step_id,
            "context_id": step.context_id,
            "tool_name": "create_media_buy",
            "request_data": {},
        }
        return step.step_id, step_data

    def test_two_concurrent_approvals_run_adapter_once(
        self, integration_db, sample_tenant, sample_principal, context_manager
    ):
        """Two concurrent approvals of the same pending buy → exactly one wins and invokes
        the adapter once; the other is NOT_CLAIMED. One terminal step, one artifact."""
        tenant_id = sample_tenant["tenant_id"]
        step_id, step_data = self._seed_pending_buy_and_step(
            context_manager, tenant_id, sample_principal["principal_id"], "mb_race"
        )

        both_ready = threading.Barrier(2, timeout=30)
        adapter_calls: list[int] = []
        adapter_lock = threading.Lock()
        outcomes: list[FinalizeOutcome] = []
        errors: list[BaseException] = []
        results_lock = threading.Lock()

        def counting_adapter() -> tuple[bool, str | None]:
            with adapter_lock:
                adapter_calls.append(1)
            return True, None

        def approve_once() -> None:
            try:
                with get_db_session() as session:
                    both_ready.wait()  # both threads race into the claim at once
                    outcome, _ = finalize_media_buy_approval(
                        session,
                        tenant_id,
                        media_buy_id="mb_race",
                        step_id=step_id,
                        step_data=step_data,
                        compute_target=lambda _mb: "active",
                        run_adapter=counting_adapter,
                        expected_status="pending_approval",
                        approved_by="admin",
                        approved_at=datetime.now(UTC),
                    )
                with results_lock:
                    outcomes.append(outcome)
            except BaseException as exc:  # noqa: BLE001 - surfaced to the main thread
                with results_lock:
                    errors.append(exc)

        threads = [threading.Thread(target=approve_once, name=f"appr-{i}") for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        assert not any(t.is_alive() for t in threads), "an approval thread hung (possible deadlock)"
        assert not errors, f"concurrent approval thread(s) failed: {errors}"
        assert len(adapter_calls) == 1, f"adapter must run EXACTLY once, ran {len(adapter_calls)}"
        assert sorted(outcomes) == sorted([FinalizeOutcome.APPLIED, FinalizeOutcome.NOT_CLAIMED])

        with get_db_session() as session:
            step = WorkflowRepository(session, tenant_id).get_by_step_id(step_id)
            assert step is not None and step.status == "completed"
            assert step.response_data is not None  # exactly one terminal artifact
            buy = MediaBuyRepository(session, tenant_id).get_by_id("mb_race")
            assert buy is not None and buy.status == "active"

    def test_concurrent_approve_and_reject_single_winner(
        self, integration_db, sample_tenant, sample_principal, context_manager
    ):
        """A concurrent approve and reject of the same pending buy → exactly one wins; the
        buy lands in a single terminal decision (active XOR rejected), never both."""
        tenant_id = sample_tenant["tenant_id"]
        step_id, step_data = self._seed_pending_buy_and_step(
            context_manager, tenant_id, sample_principal["principal_id"], "mb_race2"
        )

        both_ready = threading.Barrier(2, timeout=30)
        outcomes: list[FinalizeOutcome] = []
        errors: list[BaseException] = []
        results_lock = threading.Lock()

        def do_approve() -> None:
            try:
                with get_db_session() as session:
                    both_ready.wait()
                    outcome, _ = finalize_media_buy_approval(
                        session,
                        tenant_id,
                        media_buy_id="mb_race2",
                        step_id=step_id,
                        step_data=step_data,
                        compute_target=lambda _mb: "active",
                        run_adapter=lambda: (True, None),
                        expected_status="pending_approval",
                        approved_by="admin",
                        approved_at=datetime.now(UTC),
                    )
                with results_lock:
                    outcomes.append(outcome)
            except BaseException as exc:  # noqa: BLE001
                with results_lock:
                    errors.append(exc)

        def do_reject() -> None:
            try:
                with get_db_session() as session:
                    both_ready.wait()
                    outcome = finalize_media_buy_rejection(
                        session,
                        tenant_id,
                        media_buy_id="mb_race2",
                        step_id=step_id,
                        step_data=step_data,
                        reason="rejected in race",
                    )
                with results_lock:
                    outcomes.append(outcome)
            except BaseException as exc:  # noqa: BLE001
                with results_lock:
                    errors.append(exc)

        threads = [threading.Thread(target=do_approve, name="appr"), threading.Thread(target=do_reject, name="rej")]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        assert not any(t.is_alive() for t in threads), "a decision thread hung (possible deadlock)"
        assert not errors, f"decision thread(s) failed: {errors}"
        # Exactly one decision won; the other lost the claim.
        assert sorted(outcomes) == sorted([FinalizeOutcome.APPLIED, FinalizeOutcome.NOT_CLAIMED])

        with get_db_session() as session:
            buy = MediaBuyRepository(session, tenant_id).get_by_id("mb_race2")
            assert buy is not None
            assert buy.status in ("active", "rejected")  # a single terminal decision, never both
