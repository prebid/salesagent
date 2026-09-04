"""Integration tests for the workflows admin blueprint.

Tests workflow list, approval, and rejection via Flask test client.
Requires PostgreSQL (integration_db fixture).
"""

import uuid
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from sqlalchemy import delete, select

from src.admin.app import create_app
from src.core.database.database_session import get_db_session
from src.core.database.models import Context, Principal, Tenant, WorkflowStep
from tests.helpers.media_buy_approval import (
    ADAPTER_BOUNDARY,
    adapter_success,
    attach_approved_creative,
    login_as,
    seed_pending_buy,
)
from tests.helpers.media_buy_write_seam import (
    MediaBuyState,
    assert_status_move_carried_bookkeeping,
    read_media_buy_state,
)
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
    """Set up authenticated super-admin session for test client."""
    login_as(client, tenant_id=tenant_id)


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


class TestWorkflowApprovalMovesMediaBuy:
    """Approving a media-buy workflow step moves the buy — with its mutation bookkeeping.

    The status write these tests grade lives in ``execute_approved_media_buy``, which is
    the SOLE post-adapter writer: it owns ``revision`` (the buyer's optimistic-concurrency
    token, which must strictly increase on every mutation) and ``confirmed_at`` (the instant
    the seller committed, stamped once on the first committed status). The route no longer
    touches the row at all.

    So the callee is exactly what must NOT be mocked here — patching it removes the only
    writer, and every assertion below would then be grading a mock. The one seam these
    tests stub is the AD-SERVER boundary. That was the original defect: these tests patched
    ``execute_approved_media_buy`` and asserted the route's own write, which is how the
    callee and the three routes came to disagree about the final status without anything
    going red.
    """

    def test_approve_waiting_on_creatives_bumps_revision(self, client, factory_session):
        """The pending_creatives arm: the buy moved, and the ad server was never contacted."""
        from tests.factories import CreativeAssignmentFactory, CreativeFactory

        seeded = seed_pending_buy(starts_in_days=7)
        _auth_session(client, seeded.tenant_id)

        before = read_media_buy_state(seeded.tenant_id, seeded.media_buy_id, session=factory_session)
        assert before.confirmed_at is None, "fixture must start with an unstamped confirmation instant"

        creative = CreativeFactory(tenant=seeded.tenant, principal=seeded.principal, status="pending")
        CreativeAssignmentFactory(creative=creative, media_buy=seeded.media_buy, package_id="pkg_wf_1")

        with patch(ADAPTER_BOUNDARY) as adapter_boundary:
            response = client.post(
                f"/tenant/{seeded.tenant_id}/workflows/{seeded.context_id}/steps/{seeded.step_id}/approve",
                content_type="application/json",
                json={},
            )
        assert response.status_code == 200, response.data

        # The whole point of holding a buy is that nothing is created downstream. An
        # order in the ad server for a buy whose creatives are unapproved is the failure
        # this arm exists to prevent, and only the boundary can testify to it.
        adapter_boundary.assert_not_called()

        after = read_media_buy_state(seeded.tenant_id, seeded.media_buy_id, session=factory_session)
        assert_status_move_carried_bookkeeping(
            MediaBuyState(status="pending_approval", revision=before.revision, confirmed_at=None),
            after,
            expected_status="pending_creatives",
            # confirms=False: pending_creatives is a HOLD, not a commitment. The buy is
            # waiting on creative approval and the ad server has not been contacted, so
            # there is nothing to record. Was confirms=True, which graded the defect Chris
            # reproduced in round 6 (B3): the hold stamped a write-once, buyer-visible
            # confirmed_at, and a buy that later failed ended `failed` still carrying it.
            # The pin decides it -- create-media-buy-response.json @ 3.1.1 says null "in
            # deferred or manual-approval flows until seller commitment occurs".
            confirms=False,
            subject="approve with an unapproved creative",
        )
        # confirmed_at is NOT asserted again here. The helper above already grades it
        # under confirms=False, with a stronger message than a hand-rolled copy carries.
        # This used to be a second, inverted assertion claiming pending_creatives was
        # seller-committed -- a duplicate oracle that had to be found and flipped
        # separately when the membership was corrected.

    def test_approve_schedules_buy_and_bumps_revision(self, client, factory_session):
        """The pre-flight arm: a buy approved BEFORE its flight window opens.

        The status this write persists is the flight-window rule's answer, not a
        constant. The sibling test below grades the inside-window answer, and the two
        together pin that the rule is consulted at all: replace the resolved status with
        a bare ``PersistedMediaBuyStatus.PENDING_START`` and this test stays green while its
        sibling reddens.
        """
        seeded = seed_pending_buy(starts_in_days=7)
        attach_approved_creative(seeded)
        _auth_session(client, seeded.tenant_id)

        before = read_media_buy_state(seeded.tenant_id, seeded.media_buy_id, session=factory_session)
        assert before.confirmed_at is None, "fixture must start with an unstamped confirmation instant"

        with patch(ADAPTER_BOUNDARY, side_effect=adapter_success):
            response = client.post(
                f"/tenant/{seeded.tenant_id}/workflows/{seeded.context_id}/steps/{seeded.step_id}/approve",
                content_type="application/json",
                json={},
            )
        assert response.status_code == 200, response.data

        after = read_media_buy_state(seeded.tenant_id, seeded.media_buy_id, session=factory_session)
        assert after.approved_by == "test@example.com"
        assert after.approved_at is not None
        assert_status_move_carried_bookkeeping(
            MediaBuyState(status="pending_approval", revision=before.revision, confirmed_at=None),
            after,
            expected_status="pending_start",
            confirms=True,
            subject="approving the media-buy workflow step",
        )
        # This is the manual-approval path: before this write the buy had no confirmation
        # instant at all, and 'pending_start' is a seller-confirmed status.
        assert after.confirmed_at is not None, (
            "an admin-approved buy must carry the instant the seller committed; "
            "confirmed_at is still NULL after approval"
        )

    def test_approve_inside_the_flight_window_activates_rather_than_schedules(self, client, factory_session):
        """The active arm: a buy approved INSIDE its window is serving, not scheduled.

        This is the case the route got wrong when it wrote ``scheduled`` unconditionally.
        The wire projection and the sweep corrected it downstream, which is why nothing
        caught it — but the column disagreed with the calendar.
        """
        seeded = seed_pending_buy(starts_in_days=-1)
        attach_approved_creative(seeded)
        _auth_session(client, seeded.tenant_id)

        before = read_media_buy_state(seeded.tenant_id, seeded.media_buy_id, session=factory_session)

        with patch(ADAPTER_BOUNDARY, side_effect=adapter_success):
            response = client.post(
                f"/tenant/{seeded.tenant_id}/workflows/{seeded.context_id}/steps/{seeded.step_id}/approve",
                content_type="application/json",
                json={},
            )
        assert response.status_code == 200, response.data

        after = read_media_buy_state(seeded.tenant_id, seeded.media_buy_id, session=factory_session)
        assert_status_move_carried_bookkeeping(
            MediaBuyState(status="pending_approval", revision=before.revision, confirmed_at=None),
            after,
            expected_status="active",
            confirms=True,
            subject="approving a buy inside its flight window",
        )
        assert after.status == "active", (
            "a buy approved inside its flight window is serving; persisting 'scheduled' "
            "makes the column disagree with the calendar"
        )

    def test_approval_from_another_tenants_session_is_refused(self, client, factory_session):
        """@require_tenant_access() on approve_workflow_step is graded here.

        Nothing graded it: deleting the decorator left the admin suite green, because
        every other test in this file authenticates as a super_admin, who is allowed
        across tenants by design. Only a tenant-SCOPED session can tell the decorator
        apart from its absence.

        The assertion is the media buy's state, not the status code. A route that
        redirects to a login page still returns 200 for the redirect target, so
        "did the write happen" is the question that cannot be answered two ways.
        """
        seeded = seed_pending_buy(starts_in_days=7)

        # A session scoped to a DIFFERENT tenant, and not a super admin.
        login_as(client, tenant_id="some_other_tenant", email="outsider@example.com", super_admin=False)

        before = read_media_buy_state(seeded.tenant_id, seeded.media_buy_id, session=factory_session)
        with patch(ADAPTER_BOUNDARY, side_effect=adapter_success):
            client.post(
                f"/tenant/{seeded.tenant_id}/workflows/{seeded.context_id}/steps/{seeded.step_id}/approve",
                content_type="application/json",
                json={},
            )

        after = read_media_buy_state(seeded.tenant_id, seeded.media_buy_id, session=factory_session)
        assert after.status == before.status, (
            f"a session scoped to another tenant moved this buy from {before.status!r} to "
            f"{after.status!r}; require_tenant_access() is not holding"
        )
        assert after.confirmed_at is None, "an outsider's request stamped the seller-commitment instant"
