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
        from src.core.schemas._base import GetMediaBuysRequest
        from src.core.tools.media_buy_list import _get_media_buys_impl
        from tests.factories import CreativeAssignmentFactory, CreativeFactory, MediaBuyFactory, PrincipalFactory

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

        # Wire read-back: buyer-visible revision advanced (1 → 2) and confirmed_at
        # is the approval instant (== approved_at), NOT the buy's created_at.
        identity = PrincipalFactory.make_identity(tenant_id=test_tenant, principal_id="wf_test_principal")
        listed = _get_media_buys_impl(
            req=GetMediaBuysRequest(media_buy_ids=[buy.media_buy_id]),
            identity=identity,
            include_snapshot=False,
        )
        assert len(listed.media_buys) == 1
        item = listed.media_buys[0]
        assert item.revision == 2
        assert item.confirmed_at == stored.approved_at
        assert item.confirmed_at != item.created_at


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
