"""Admin approve/reject routes use the atomic terminal-transition primitive (round-15 B1).

operations.py (media-buy approve/reject) and policy.py (review_task) previously wrote
``step.status = "..."`` with raw ORM after a pending read, bypassing
``WorkflowRepository.transition_if_nonterminal`` — so a buyer cancel committing in between
was silently overwritten (and, for approve, irreversible adapter order creation ran anyway).
They now route through the atomic conditional UPDATE and treat a refused transition (None)
as a conflict: no overwrite, and no ``execute_approved_media_buy``.
"""

import uuid
from unittest.mock import ANY, patch

import pytest

from src.admin.app import create_app
from src.core.context_manager import ContextManager
from src.core.database.repositories import WorkflowUoW

app = create_app()

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


@pytest.fixture
def client():
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["SESSION_COOKIE_PATH"] = "/"
    with app.test_client() as c:
        yield c


def _auth(client, tenant_id):
    with client.session_transaction() as sess:
        sess["authenticated"] = True
        sess["user"] = {"email": "admin@example.com", "is_super_admin": True}
        sess["email"] = "admin@example.com"
        sess["tenant_id"] = tenant_id
        sess["test_user"] = "admin@example.com"
        sess["test_user_role"] = "super_admin"
        sess["test_tenant_id"] = tenant_id


def _make_step(tenant_id: str, principal_id: str, status: str, *, media_buy_id: str | None = None) -> str:
    """Create a Context + WorkflowStep (optionally mapped to a media_buy) via ContextManager
    (no raw session.add — factory/persistence-layer pattern). Returns the step id."""
    cm = ContextManager()
    ctx = cm.create_context(tenant_id=tenant_id, principal_id=principal_id)
    mappings = [{"object_type": "media_buy", "object_id": media_buy_id, "action": "approve"}] if media_buy_id else None
    step = cm.create_workflow_step(
        context_id=ctx.context_id,
        step_type="approval",
        owner="publisher",
        status=status,
        tool_name="create_media_buy",
        request_data={},
        object_mappings=mappings,
    )
    return step.step_id


def _status(tenant_id: str, step_id: str) -> str:
    with WorkflowUoW(tenant_id) as uow:
        assert uow.workflows is not None
        step = uow.workflows.get_by_step_id(step_id)
        return step.status if step else "missing"


class TestPolicyReviewAtomicity:
    def test_review_approve_of_canceled_step_is_conflict(self, client, sample_tenant, sample_principal):
        """[Round-15 B1] policy review of an already-canceled step is refused (409), not
        overwritten to completed."""
        tenant_id = sample_tenant["tenant_id"]
        _auth(client, tenant_id)
        step_id = _make_step(tenant_id, sample_principal["principal_id"], "canceled")

        resp = client.post(f"/tenant/{tenant_id}/policy/review/{step_id}", data={"action": "approve", "notes": "n"})
        assert resp.status_code == 409
        assert _status(tenant_id, step_id) == "canceled"

    def test_review_approve_of_pending_step_completes(self, client, sample_tenant, sample_principal):
        """[Round-15 B1] positive control: a pending review approves to completed."""
        tenant_id = sample_tenant["tenant_id"]
        _auth(client, tenant_id)
        step_id = _make_step(tenant_id, sample_principal["principal_id"], "requires_approval")

        resp = client.post(f"/tenant/{tenant_id}/policy/review/{step_id}", data={"action": "approve", "notes": "n"})
        assert resp.status_code in (200, 302)
        assert _status(tenant_id, step_id) == "completed"


class TestOperationsApproveAtomicity:
    def test_media_buy_approve_refused_transition_skips_adapter(self, client, sample_tenant, sample_principal):
        """[Round-15 B1] When the atomic transition is refused (a buyer cancel won the race),
        the media-buy approve route must NOT run the irreversible execute_approved_media_buy
        and must surface a conflict (redirect) — the step is not overwritten."""
        tenant_id = sample_tenant["tenant_id"]
        _auth(client, tenant_id)
        media_buy_id = f"mb_{uuid.uuid4().hex[:8]}"
        step_id = _make_step(
            tenant_id, sample_principal["principal_id"], "requires_approval", media_buy_id=media_buy_id
        )

        with (
            patch(
                "src.admin.blueprints.operations.WorkflowRepository.transition_if_nonterminal",
                return_value=None,
            ) as mock_transition,
            patch("src.core.tools.media_buy_create.execute_approved_media_buy") as mock_execute,
        ):
            resp = client.post(
                f"/tenant/{tenant_id}/media-buy/{media_buy_id}/approve",
                data={"action": "approve"},
                follow_redirects=False,
            )

        mock_transition.assert_called_once_with(ANY, status="approved")
        mock_execute.assert_not_called()
        assert _status(tenant_id, step_id) == "requires_approval", "refused transition must not overwrite the step"
        assert resp.status_code in (302, 303)
