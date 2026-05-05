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
            platform_mappings={"mock": {}},
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
