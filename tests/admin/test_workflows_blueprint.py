"""Integration tests for the workflows admin blueprint.

Tests workflow list, approval, and rejection via Flask test client.
Requires PostgreSQL (integration_db fixture).
"""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, select

from src.admin.app import create_app
from src.core.database.database_session import get_db_session
from src.core.database.models import Context, Principal, Tenant, WorkflowStep
from tests.utils.database_helpers import create_tenant_with_timestamps

app = create_app()

pytestmark = [pytest.mark.admin, pytest.mark.requires_db]

_TENANT_ID = "wf_test_tenant"


@pytest.fixture
def client():
    """Flask test client with test configuration."""
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["SESSION_COOKIE_PATH"] = "/"
    with app.test_client() as client:
        yield client


@pytest.fixture
def test_tenant(integration_db):
    """Create a test tenant with principal for workflow tests."""
    with get_db_session() as session:
        try:
            session.execute(
                delete(WorkflowStep).where(
                    WorkflowStep.context_id.in_(select(Context.context_id).where(Context.tenant_id == _TENANT_ID))
                )
            )
            session.execute(delete(Context).where(Context.tenant_id == _TENANT_ID))
            session.execute(delete(Principal).where(Principal.tenant_id == _TENANT_ID))
            session.execute(delete(Tenant).where(Tenant.tenant_id == _TENANT_ID))
            session.commit()
        except Exception:
            session.rollback()

        tenant = create_tenant_with_timestamps(
            tenant_id=_TENANT_ID,
            name="Workflow Test Tenant",
            subdomain="wf-test",
            ad_server="mock",
            is_active=True,
        )
        session.add(tenant)

        principal = Principal(
            tenant_id=_TENANT_ID,
            principal_id="wf_test_principal",
            name="Workflow Test Principal",
            platform_mappings={"mock": {"advertiser_id": "test_advertiser"}},
            access_token=f"wf-test-token-{uuid.uuid4().hex}",
        )
        session.add(principal)
        session.commit()

    return _TENANT_ID


def _auth_session(client, tenant_id):
    """Set up authenticated session for test client."""
    with client.session_transaction() as sess:
        sess["authenticated"] = True
        sess["user"] = {"email": "test@example.com", "is_super_admin": True}
        sess["email"] = "test@example.com"
        sess["tenant_id"] = tenant_id
        sess["test_user"] = "test@example.com"
        sess["test_user_role"] = "super_admin"
        sess["test_user_name"] = "Test User"
        sess["test_tenant_id"] = tenant_id


def _create_context_and_step(tenant_id: str, status: str = "pending_approval") -> tuple[str, str]:
    """Create a Context + WorkflowStep and return (context_id, step_id)."""
    context_id = f"ctx_{uuid.uuid4().hex[:12]}"
    step_id = f"step_{uuid.uuid4().hex[:12]}"
    now = datetime.now(UTC)
    with get_db_session() as session:
        context = Context(
            context_id=context_id,
            tenant_id=tenant_id,
            principal_id="wf_test_principal",
            conversation_history=[],
            created_at=now,
            last_activity_at=now,
        )
        session.add(context)
        step = WorkflowStep(
            step_id=step_id,
            context_id=context_id,
            step_type="approval",
            tool_name="create_media_buy",
            status=status,
            owner="principal",
            request_data={},
            created_at=now,
        )
        session.add(step)
        session.commit()
    return context_id, step_id


class TestWorkflowsList:
    """Test the workflows list page."""

    def test_list_returns_200(self, client, test_tenant):
        """GET /tenant/<tid>/workflows returns 200."""
        _auth_session(client, test_tenant)
        response = client.get(f"/tenant/{test_tenant}/workflows")
        assert response.status_code == 200

    def test_list_shows_pending_steps(self, client, test_tenant):
        """After creating a pending step, the list page shows it."""
        _auth_session(client, test_tenant)
        _create_context_and_step(test_tenant, status="pending_approval")

        response = client.get(f"/tenant/{test_tenant}/workflows")
        html = response.data.decode()
        assert "pending_approval" in html or "pending" in html.lower()


class TestWorkflowApproval:
    """Test workflow step approval."""

    def test_approve_step_sets_status_approved(self, client, test_tenant):
        """POST approve sets the step status to 'approved'."""
        _auth_session(client, test_tenant)
        context_id, step_id = _create_context_and_step(test_tenant, status="pending_approval")

        response = client.post(
            f"/tenant/{test_tenant}/workflows/{context_id}/steps/{step_id}/approve",
            content_type="application/json",
            json={},
        )
        assert response.status_code == 200
        data = response.get_json()
        assert data.get("success") is True

        with get_db_session() as session:
            step = session.get(WorkflowStep, step_id)
        assert step is not None
        assert step.status == "approved"

    def test_approve_nonexistent_step_returns_404(self, client, test_tenant):
        """POST approve for a nonexistent step returns 404."""
        _auth_session(client, test_tenant)
        response = client.post(
            f"/tenant/{test_tenant}/workflows/fake_ctx/steps/nonexistent_step/approve",
            content_type="application/json",
            json={},
        )
        assert response.status_code == 404

    def test_approve_stamps_confirmed_at_and_bumps_revision_through_the_route(
        self, client, test_tenant, factory_session
    ):
        """Driving the real approve route stamps approved_at/approved_by and bumps revision.

        This exercises the production surface the PR rewrote — the approve
        blueprint calling ``MediaBuyRepository.update_status(..., approved_at=)``
        — end to end through the Flask route, not the repository seam in
        isolation. Dropping the ``approved_at=`` kwarg (or the bump) would corrupt
        the buyer-visible ``confirmed_at``/``revision`` while every seam-level
        test stayed green; this is the test that goes red on that regression
        (#1544 round-2 blocker #3).

        The workflow step has an unapproved creative assignment, so the route
        takes the "await creatives" arm — it stamps the approval and returns
        BEFORE any adapter call, so no adapter mocking is needed.
        """
        from datetime import UTC, datetime

        from src.core.database.models import Principal, Tenant
        from src.core.database.repositories import MediaBuyRepository, WorkflowRepository
        from tests.factories import CreativeAssignmentFactory, CreativeFactory, MediaBuyFactory, PrincipalFactory
        from tests.helpers.media_buy import read_back_media_buy

        _auth_session(client, test_tenant)

        # Reuse the tenant/principal the test_tenant fixture committed.
        tenant_obj = factory_session.get(Tenant, test_tenant)
        principal_obj = factory_session.get(Principal, (test_tenant, "wf_test_principal"))

        # A media buy awaiting seller approval (revision starts at 1).
        buy = MediaBuyFactory(tenant=tenant_obj, principal=principal_obj, status="pending_approval")
        assert buy.revision == 1

        # An unapproved creative assigned to it → route stops before the adapter.
        creative = CreativeFactory(tenant=tenant_obj, principal=principal_obj, status="pending")
        CreativeAssignmentFactory(creative=creative, media_buy=buy)

        # The pending approval workflow step + a mapping tying it to the buy.
        context_id, step_id = _create_context_and_step(test_tenant, status="pending_approval")
        WorkflowRepository(factory_session, test_tenant).add_mapping(
            step_id=step_id, object_type="media_buy", object_id=buy.media_buy_id, action="create"
        )
        factory_session.commit()

        before_approval = datetime.now(UTC)
        response = client.post(
            f"/tenant/{test_tenant}/workflows/{context_id}/steps/{step_id}/approve",
            content_type="application/json",
            json={},
        )
        after_approval = datetime.now(UTC)
        assert response.status_code == 200, response.data
        assert response.get_json().get("success") is True

        # ORM read-back: the seam stamped approval and advanced the counter.
        factory_session.expire_all()
        stored = MediaBuyRepository(factory_session, test_tenant).get_by_id(buy.media_buy_id)
        assert stored is not None
        assert stored.status == "pending_creatives"
        assert stored.approved_by == "test@example.com"
        assert stored.approved_at is not None
        assert before_approval <= stored.approved_at <= after_approval

        # Protocol-model read-back: buyer-visible revision advanced (1 → 2) and confirmed_at
        # is the approval instant (== approved_at), NOT the buy's created_at.
        identity = PrincipalFactory.make_identity(tenant_id=test_tenant, principal_id="wf_test_principal")
        item = read_back_media_buy(identity, buy.media_buy_id)
        assert item.revision == 2
        assert item.confirmed_at == stored.approved_at
        assert item.confirmed_at != item.created_at

    def test_approve_with_zero_assignments_holds_at_pending_creatives(self, client, test_tenant, factory_session):
        """Approving a buy with NO creative assignments HOLDS at pending_creatives.

        Both admin approve routes decide finalize-vs-hold through the shared
        tenant-scoped gate (``creatives_ready_for_finalize``): per the AdCP
        media-buy-status.json enum, ``pending_creatives`` means "approved by the
        seller and has no creatives assigned — the buyer must attach creatives
        via sync_creatives", so creatives legitimately arrive after approval.
        This route previously FINALIZED a creative-less buy into the ad server
        while the operations route held — reverting the shared gate's
        empty-assignments policy turns this test red. #1544.
        """
        from src.core.database.repositories import MediaBuyRepository

        _auth_session(client, test_tenant)
        mbid, context_id, step_id = _setup_mapped_media_buy_step(factory_session, test_tenant, with_assignment=False)

        response = client.post(
            f"/tenant/{test_tenant}/workflows/{context_id}/steps/{step_id}/approve",
            content_type="application/json",
            json={},
        )
        assert response.status_code == 200, response.data
        assert response.get_json().get("success") is True

        factory_session.expire_all()
        stored = MediaBuyRepository(factory_session, test_tenant).get_by_id(mbid)
        assert stored is not None
        # HOLD, not finalize: the buy parks at pending_creatives with the approval
        # stamped; a finalize would have driven the adapter and left the buy at a
        # flight-derived status (scheduled/active) instead.
        assert stored.status == "pending_creatives"
        assert stored.approved_by == "test@example.com"
        assert stored.approved_at is not None


class TestWorkflowRejection:
    """Test workflow step rejection."""

    def test_reject_step_sets_status_rejected(self, client, test_tenant):
        """POST reject sets the step status to 'rejected'."""
        _auth_session(client, test_tenant)
        context_id, step_id = _create_context_and_step(test_tenant, status="pending_approval")

        response = client.post(
            f"/tenant/{test_tenant}/workflows/{context_id}/steps/{step_id}/reject",
            content_type="application/json",
            json={"reason": "Does not meet requirements"},
        )
        assert response.status_code == 200
        data = response.get_json()
        assert data.get("success") is True

        with get_db_session() as session:
            step = session.get(WorkflowStep, step_id)
        assert step is not None
        assert step.status == "rejected"
        assert step.error_message == "Does not meet requirements"

    def test_reject_step_without_reason_uses_default(self, client, test_tenant):
        """POST reject without a reason body still succeeds (uses default message)."""
        _auth_session(client, test_tenant)
        context_id, step_id = _create_context_and_step(test_tenant, status="pending_approval")

        response = client.post(
            f"/tenant/{test_tenant}/workflows/{context_id}/steps/{step_id}/reject",
            content_type="application/json",
            json={},
        )
        assert response.status_code == 200

        with get_db_session() as session:
            step = session.get(WorkflowStep, step_id)
        assert step.status == "rejected"

    def test_reject_nonexistent_step_returns_404(self, client, test_tenant):
        """POST reject for a nonexistent step returns 404."""
        _auth_session(client, test_tenant)
        response = client.post(
            f"/tenant/{test_tenant}/workflows/fake_ctx/steps/nonexistent_step/reject",
            content_type="application/json",
            json={"reason": "test"},
        )
        assert response.status_code == 404


def _setup_mapped_media_buy_step(
    factory_session,
    tenant_id,
    *,
    buy_status="pending_approval",
    step_status="pending_approval",
    external_task_id=None,
    with_assignment=True,
):
    """Create a media buy + a workflow step mapped to the buy (and, by default, an
    approved creative assignment). Returns (media_buy_id, context_id, step_id).

    ``with_assignment=False`` seeds a ZERO-assignment buy — the empty-readiness
    case both approve routes must HOLD on (#1544)."""
    from src.core.database.models import Principal as P
    from src.core.database.models import Tenant as T
    from src.core.database.repositories import WorkflowRepository
    from tests.factories import CreativeAssignmentFactory, CreativeFactory, MediaBuyFactory

    tenant_obj = factory_session.get(T, tenant_id)
    principal_obj = factory_session.get(P, (tenant_id, "wf_test_principal"))
    buy = MediaBuyFactory(tenant=tenant_obj, principal=principal_obj, status=buy_status)
    if with_assignment:
        creative = CreativeFactory(tenant=tenant_obj, principal=principal_obj, status="approved")
        CreativeAssignmentFactory(creative=creative, media_buy=buy)

    context_id = f"ctx_{uuid.uuid4().hex[:12]}"
    step_id = f"step_{uuid.uuid4().hex[:12]}"
    now = datetime.now(UTC)
    is_terminal = step_status in ("completed", "rejected", "failed")
    factory_session.add(
        Context(
            context_id=context_id,
            tenant_id=tenant_id,
            principal_id="wf_test_principal",
            conversation_history=[],
            created_at=now,
            last_activity_at=now,
        )
    )
    factory_session.add(
        WorkflowStep(
            step_id=step_id,
            context_id=context_id,
            step_type="approval",
            tool_name="create_media_buy",
            status=step_status,
            owner="principal",
            request_data={"external_task_id": external_task_id} if external_task_id else {},
            response_data={"media_buy_id": buy.media_buy_id, "revision": 2} if is_terminal else None,
            created_at=now,
        )
    )
    WorkflowRepository(factory_session, tenant_id).add_mapping(
        step_id=step_id, object_type="media_buy", object_id=buy.media_buy_id, action="create"
    )
    factory_session.commit()
    return buy.media_buy_id, context_id, step_id


def _buy_status(tenant_id, media_buy_id):
    from src.core.database.repositories import MediaBuyUoW

    with MediaBuyUoW(tenant_id) as uow:
        assert uow.media_buys is not None
        buy = uow.media_buys.get_by_id(media_buy_id)
        return buy.status if buy else None


def _step_status(tenant_id, step_id):
    from src.core.database.repositories import WorkflowUoW

    with WorkflowUoW(tenant_id) as uow:
        assert uow.workflows is not None
        step = uow.workflows.get_by_step_id(step_id)
        return step.status if step else None


class TestWorkflowDecisionOwnership:
    """The media-buy workflow decision is single-winner; a terminal step is immutable.

    Replays and ineligible actions return 409 WITHOUT reverting the step, so an
    active/decided buy is never paired with a stale/mismatched task. #1544.
    """

    def test_approve_on_terminal_step_returns_409_no_revert(self, client, test_tenant, factory_session):
        """Replaying approve on a completed step → 409; the step is NOT reverted to 'approved'."""
        _auth_session(client, test_tenant)
        _, context_id, step_id = _setup_mapped_media_buy_step(
            factory_session, test_tenant, buy_status="active", step_status="completed"
        )
        r = client.post(
            f"/tenant/{test_tenant}/workflows/{context_id}/steps/{step_id}/approve",
            content_type="application/json",
            json={},
        )
        assert r.status_code == 409
        assert _step_status(test_tenant, step_id) == "completed"

    def test_reject_on_terminal_step_returns_409(self, client, test_tenant, factory_session):
        """Replaying reject on a rejected step → 409; the step stays rejected."""
        _auth_session(client, test_tenant)
        _, context_id, step_id = _setup_mapped_media_buy_step(
            factory_session, test_tenant, buy_status="rejected", step_status="rejected"
        )
        r = client.post(
            f"/tenant/{test_tenant}/workflows/{context_id}/steps/{step_id}/reject",
            content_type="application/json",
            json={"reason": "again"},
        )
        assert r.status_code == 409
        assert _step_status(test_tenant, step_id) == "rejected"

    def test_reject_when_mapped_buy_active_returns_409_no_step_change(self, client, test_tenant, factory_session):
        """Reject with the mapped buy already active → 409; the step is NOT force-rejected
        (no active-buy + rejected-task mismatch)."""
        _auth_session(client, test_tenant)
        mbid, context_id, step_id = _setup_mapped_media_buy_step(
            factory_session, test_tenant, buy_status="active", step_status="in_progress"
        )
        r = client.post(
            f"/tenant/{test_tenant}/workflows/{context_id}/steps/{step_id}/reject",
            content_type="application/json",
            json={"reason": "late"},
        )
        assert r.status_code == 409
        assert _step_status(test_tenant, step_id) == "in_progress"
        assert _buy_status(test_tenant, mbid) == "active"

    def test_reject_of_held_buy_succeeds(self, client, test_tenant, factory_session):
        """A sequential reject of a genuinely held (pending_creatives) buy still succeeds."""
        _auth_session(client, test_tenant)
        mbid, context_id, step_id = _setup_mapped_media_buy_step(
            factory_session, test_tenant, buy_status="pending_creatives", step_status="in_progress"
        )
        r = client.post(
            f"/tenant/{test_tenant}/workflows/{context_id}/steps/{step_id}/reject",
            content_type="application/json",
            json={"reason": "changed mind"},
        )
        assert r.status_code == 200
        assert _step_status(test_tenant, step_id) == "rejected"
        assert _buy_status(test_tenant, mbid) == "rejected"

    def test_replay_approve_keeps_tasks_get_completed(self, client, test_tenant, factory_session):
        """After a replay-approve is rejected (409), durable tasks/get still reports
        COMPLETED — the step was never reverted to a WORKING-mapped status."""
        import asyncio
        from unittest.mock import patch

        from a2a.types import GetTaskRequest, TaskState

        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler
        from tests.factories import PrincipalFactory

        _auth_session(client, test_tenant)
        task_id = "task_replay_own"
        _, context_id, step_id = _setup_mapped_media_buy_step(
            factory_session, test_tenant, buy_status="active", step_status="completed", external_task_id=task_id
        )
        r = client.post(
            f"/tenant/{test_tenant}/workflows/{context_id}/steps/{step_id}/approve",
            content_type="application/json",
            json={},
        )
        assert r.status_code == 409

        handler = AdCPRequestHandler.__new__(AdCPRequestHandler)
        handler.tasks = {}
        identity = PrincipalFactory.make_identity(
            tenant_id=test_tenant, principal_id="wf_test_principal", protocol="a2a"
        )
        with (
            patch.object(handler, "_get_auth_token", return_value="tok"),
            patch.object(handler, "_resolve_a2a_identity", return_value=identity),
        ):
            task = asyncio.run(handler.on_get_task(GetTaskRequest(id=task_id), context=None))
        assert task is not None
        assert task.status.state == TaskState.TASK_STATE_COMPLETED


class TestOperationsDecisionOwnership:
    """The operations media-buy approve/reject route (form POST → 302) shares the invariant."""

    def test_operations_reject_when_buy_active_no_step_revert(self, client, test_tenant, factory_session):
        """Operations reject with the mapped buy already active → 302 conflict flash; the
        step is NOT force-rejected and the buy stays active."""
        _auth_session(client, test_tenant)
        mbid, _context_id, step_id = _setup_mapped_media_buy_step(
            factory_session, test_tenant, buy_status="active", step_status="pending_approval"
        )
        r = client.post(
            f"/tenant/{test_tenant}/media-buy/{mbid}/approve",
            data={"action": "reject", "reason": "late"},
        )
        assert r.status_code == 302
        assert _step_status(test_tenant, step_id) == "pending_approval"
        assert _buy_status(test_tenant, mbid) == "active"

    def test_operations_approve_on_completed_step_no_revert(self, client, test_tenant, factory_session):
        """Operations approve when the step is already completed → 302 (no pending step
        found); the completed step is not reverted."""
        _auth_session(client, test_tenant)
        mbid, _context_id, step_id = _setup_mapped_media_buy_step(
            factory_session, test_tenant, buy_status="active", step_status="completed"
        )
        r = client.post(
            f"/tenant/{test_tenant}/media-buy/{mbid}/approve",
            data={"action": "approve"},
        )
        assert r.status_code == 302
        assert _step_status(test_tenant, step_id) == "completed"

    def test_operations_approve_with_zero_assignments_holds_at_pending_creatives(
        self, client, test_tenant, factory_session
    ):
        """Parity pin with the workflow route: a zero-assignment buy HOLDS here too.

        Both routes call the shared ``creatives_ready_for_finalize`` gate, so the
        empty-assignments decision (hold at pending_creatives, never finalize a
        creative-less buy into the ad server) cannot drift between them. #1544.
        """
        _auth_session(client, test_tenant)
        mbid, _context_id, _step_id = _setup_mapped_media_buy_step(factory_session, test_tenant, with_assignment=False)
        r = client.post(
            f"/tenant/{test_tenant}/media-buy/{mbid}/approve",
            data={"action": "approve"},
        )
        assert r.status_code == 302
        assert _buy_status(test_tenant, mbid) == "pending_creatives"
