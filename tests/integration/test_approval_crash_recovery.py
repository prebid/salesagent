"""P0 crash-recovery (#1637): the approval finalizer is resumable after a mid-finalize crash.

The finalizer claims the buy into the transient ``finalizing`` status and commits BEFORE the
external adapter runs, so a crash (or an unexpected adapter exception) between that commit and
terminalization never leaves the buy serving with no guaranteed remote order. The status
scheduler's reconciliation pass (``resume_finalizing_media_buy``) re-drives a stranded buy
idempotently — the ``platform_order_id`` guard prevents a duplicate remote order.

These tests model the interruption directly: a ``run_adapter`` that raises leaves the buy in
``finalizing`` (the exact post-crash state), and a subsequent resume drives it to its correct
final state. This is the coverage the prior ``test_approval_finalizer_race.py`` lacked — it only
exercised a handled ``(False, message)`` adapter result, never process death mid-finalize.
"""

import datetime
from datetime import UTC

import pytest

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
from tests.integration.conftest import make_create_media_buy_step, make_media_buy


@pytest.mark.requires_db
class TestApprovalCrashRecovery:
    @pytest.fixture
    def context_manager(self):
        return ContextManager()

    def _seed_pending(self, context_manager, tenant_id, principal_id, media_buy_id) -> tuple[str, dict]:
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

    def test_crash_between_claim_and_terminalize_is_recoverable(
        self, integration_db, sample_tenant, sample_principal, context_manager
    ):
        """A worker death right after the claim (adapter raises) strands the buy in
        ``finalizing`` — NOT serving — and the reconciler resumes it to ``active`` with a
        terminal step artifact. The crashed attempt created no order, so the resume's adapter
        call is the one-and-only remote order."""
        tenant_id = sample_tenant["tenant_id"]
        step_id, step_data = self._seed_pending(context_manager, tenant_id, sample_principal["principal_id"], "mb_crash")

        adapter_calls: list[str] = []

        def crashing_adapter() -> tuple[bool, str | None]:
            adapter_calls.append("attempt")
            raise RuntimeError("worker died mid-finalize")

        def healthy_adapter() -> tuple[bool, str | None]:
            adapter_calls.append("attempt")
            return True, None

        # Approve; the adapter dies immediately after the buy is claimed ``finalizing``.
        with get_db_session() as session, pytest.raises(RuntimeError):
            finalize_media_buy_approval(
                session,
                tenant_id,
                media_buy_id="mb_crash",
                step_id=step_id,
                step_data=step_data,
                compute_target=lambda _mb: "active",
                run_adapter=crashing_adapter,
                expected_status="pending_approval",
                approved_by="admin",
                approved_at=datetime.datetime.now(UTC),
            )

        # Stranded, but NOT serving: finalizing, unconfirmed, approval instant captured, step open.
        with get_db_session() as session:
            buy = MediaBuyRepository(session, tenant_id).get_by_id("mb_crash")
            assert buy is not None and buy.status == "finalizing"
            assert buy.confirmed_at is None  # never confirmed while the order is unconfirmed
            assert buy.approved_at is not None  # the approval instant was captured at the claim
            step = WorkflowRepository(session, tenant_id).get_by_step_id(step_id)
            assert step is not None and step.status == "in_progress"

        # The scheduler's reconciliation pass resumes with a healthy adapter.
        with get_db_session() as session:
            outcome, _ = resume_finalizing_media_buy(
                session,
                tenant_id,
                media_buy_id="mb_crash",
                step_id=step_id,
                step_data=step_data,
                run_adapter=healthy_adapter,
            )
        assert outcome is FinalizeOutcome.APPLIED

        with get_db_session() as session:
            buy = MediaBuyRepository(session, tenant_id).get_by_id("mb_crash")
            assert buy is not None and buy.status == "active"
            # confirmed_at stamped at the serving transition, from the claim's approved_at (B4).
            assert buy.confirmed_at is not None and buy.confirmed_at == buy.approved_at
            step = WorkflowRepository(session, tenant_id).get_by_step_id(step_id)
            assert step is not None and step.status == "completed"
            assert step.response_data is not None  # terminal completion artifact for tasks/get

        assert adapter_calls == ["attempt", "attempt"]  # crash (no order) + resume (created it)

    def test_resume_skips_adapter_when_order_already_created(
        self, integration_db, sample_tenant, sample_principal, context_manager
    ):
        """Crash AFTER the adapter created the order (``platform_order_id`` persisted) but before
        the serving transition: the resume's idempotency guard skips the adapter entirely, so the
        remote order is never created twice."""
        tenant_id = sample_tenant["tenant_id"]
        step_id, step_data = self._seed_pending(
            context_manager, tenant_id, sample_principal["principal_id"], "mb_order_made"
        )

        # Model the post-order crash: buy claimed ``finalizing`` AND a package already carries
        # the remote order id (execute_approved_media_buy persisted it before the process died).
        with get_db_session() as session:
            MediaBuyRepository(session, tenant_id).update_status_computed(
                "mb_order_made",
                lambda _mb: "finalizing",
                expected_status="pending_approval",
                approved_at=datetime.datetime.now(UTC),
            )
            session.add(
                MediaPackage(
                    media_buy_id="mb_order_made",
                    package_id="pkg_1",
                    package_config={"platform_order_id": "gam_999"},
                )
            )
            session.commit()

        adapter_calls: list[str] = []

        def adapter() -> tuple[bool, str | None]:
            adapter_calls.append("attempt")
            return True, None

        with get_db_session() as session:
            outcome, _ = resume_finalizing_media_buy(
                session,
                tenant_id,
                media_buy_id="mb_order_made",
                step_id=step_id,
                step_data=step_data,
                run_adapter=adapter,
            )

        assert outcome is FinalizeOutcome.APPLIED
        assert adapter_calls == []  # the idempotency guard skipped the adapter — order already existed
        with get_db_session() as session:
            buy = MediaBuyRepository(session, tenant_id).get_by_id("mb_order_made")
            assert buy is not None and buy.status == "active"
            step = WorkflowRepository(session, tenant_id).get_by_step_id(step_id)
            assert step is not None and step.status == "completed"

    async def test_scheduler_reconciliation_pass_drives_stranded_buy_to_serving(
        self, integration_db, sample_tenant, sample_principal, context_manager
    ):
        """The status scheduler's reconciliation pass scans for buys stranded in ``finalizing``
        and resumes them. Here the order already exists (platform_order_id set), so the guard
        skips the adapter and the buy is driven to ``active`` without invoking the real adapter."""
        from src.services.media_buy_status_scheduler import MediaBuyStatusScheduler

        tenant_id = sample_tenant["tenant_id"]
        self._seed_pending(context_manager, tenant_id, sample_principal["principal_id"], "mb_sched")
        with get_db_session() as session:
            MediaBuyRepository(session, tenant_id).update_status_computed(
                "mb_sched",
                lambda _mb: "finalizing",
                expected_status="pending_approval",
                approved_at=datetime.datetime.now(UTC),
            )
            session.add(
                MediaPackage(
                    media_buy_id="mb_sched", package_id="pkg_1", package_config={"platform_order_id": "gam_1"}
                )
            )
            session.commit()

        await MediaBuyStatusScheduler()._reconcile_finalizing_buys()

        with get_db_session() as session:
            buy = MediaBuyRepository(session, tenant_id).get_by_id("mb_sched")
            assert buy is not None and buy.status == "active"  # no longer stranded

    def test_happy_path_bumps_revision_once_and_stamps_confirmed_at(
        self, integration_db, sample_tenant, sample_principal, context_manager
    ):
        """No crash: pending_approval → finalizing → active in one call; the adapter runs exactly
        once; the approval advances the revision by exactly one (the claim bumps, the deferred
        finalize does not)."""
        tenant_id = sample_tenant["tenant_id"]
        step_id, step_data = self._seed_pending(context_manager, tenant_id, sample_principal["principal_id"], "mb_happy")

        with get_db_session() as session:
            before = MediaBuyRepository(session, tenant_id).get_by_id("mb_happy").revision

        adapter_calls: list[str] = []

        def adapter() -> tuple[bool, str | None]:
            adapter_calls.append("attempt")
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
        assert adapter_calls == ["attempt"]
        with get_db_session() as session:
            buy = MediaBuyRepository(session, tenant_id).get_by_id("mb_happy")
            assert buy is not None and buy.status == "active"
            assert buy.confirmed_at is not None
            assert buy.revision == before + 1  # exactly one advance for the whole approval
