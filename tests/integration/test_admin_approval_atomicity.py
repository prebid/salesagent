"""Admin approve/reject routes use the atomic terminal-transition primitive (round-15 B1).

operations.py (media-buy approve/reject) and policy.py (review_task) previously wrote
``step.status = "..."`` with raw ORM after a pending read, bypassing
``WorkflowRepository.transition_if_nonterminal`` — so a buyer cancel committing in between
was silently overwritten (and, for approve, irreversible adapter order creation ran anyway).
They now route through the atomic conditional UPDATE and treat a refused transition (None)
as a conflict: no overwrite, and no ``execute_approved_media_buy``.
"""

import uuid
from unittest.mock import patch

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

    def test_review_unknown_action_is_bad_request_not_conflict(self, client, sample_tenant, sample_principal):
        """An unknown/missing action is a client error (400 Bad Request), distinct from the 409
        finalized-task conflict, and must not transition a still-pending step. Regression guard:
        the else branch previously fell through with ``transitioned = None`` and mislabeled the
        bad request as a 409 conflict."""
        tenant_id = sample_tenant["tenant_id"]
        _auth(client, tenant_id)
        step_id = _make_step(tenant_id, sample_principal["principal_id"], "requires_approval")

        resp = client.post(f"/tenant/{tenant_id}/policy/review/{step_id}", data={"action": "frobnicate", "notes": "n"})
        assert resp.status_code == 400
        assert _status(tenant_id, step_id) == "requires_approval", "an unknown action must not transition the step"


class TestOperationsApproveAtomicity:
    def test_media_buy_approve_refused_claim_skips_adapter(self, client, sample_tenant, sample_principal):
        """[Round-19] When the approval claim is refused (a concurrent approve/cancel won the
        race), the media-buy approve route must NOT run the irreversible execute_approved_media_buy
        and must surface a conflict (redirect) — the step is not overwritten. The route uses the
        source-state-guarded ``claim_approval`` (NOT the broad transition_if_nonterminal, which
        would let approved→approved slip through)."""
        tenant_id = sample_tenant["tenant_id"]
        _auth(client, tenant_id)
        media_buy_id = f"mb_{uuid.uuid4().hex[:8]}"
        step_id = _make_step(
            tenant_id, sample_principal["principal_id"], "requires_approval", media_buy_id=media_buy_id
        )

        with (
            patch(
                "src.admin.blueprints.operations.WorkflowRepository.claim_approval",
                return_value=None,
            ) as mock_claim,
            patch("src.core.tools.media_buy_create.execute_approved_media_buy") as mock_execute,
        ):
            resp = client.post(
                f"/tenant/{tenant_id}/media-buy/{media_buy_id}/approve",
                data={"action": "approve", "workflow_step_id": step_id},
                follow_redirects=False,
            )

        mock_claim.assert_called_once_with(step_id)
        mock_execute.assert_not_called()
        assert _status(tenant_id, step_id) == "requires_approval", "refused claim must not overwrite the step"
        assert resp.status_code in (302, 303)

    def test_media_buy_detail_approves_legacy_approval_status_step(self, client, sample_tenant, sample_principal):
        """[Round-21] The media-buy detail approve route finds and approves a legacy ``approval``
        step — its lookup previously prefiltered on {requires_approval, pending_approval} only and
        returned 'No pending approval found' before reaching the canonical claim."""
        tenant_id = sample_tenant["tenant_id"]
        _auth(client, tenant_id)
        media_buy_id = f"mb_{uuid.uuid4().hex[:8]}"
        step_id = _make_step(tenant_id, sample_principal["principal_id"], "approval", media_buy_id=media_buy_id)

        resp = client.post(
            f"/tenant/{tenant_id}/media-buy/{media_buy_id}/approve",
            data={"action": "approve", "workflow_step_id": step_id},
            follow_redirects=False,
        )

        assert resp.status_code in (200, 302, 303)
        assert _status(tenant_id, step_id) == "approved", (
            "a legacy approval step must be approvable via the detail route"
        )

    def test_media_buy_detail_rejects_legacy_approval_status_step(self, client, sample_tenant, sample_principal):
        """[Round-21] The media-buy detail reject action (same route, action=reject) rejects a
        legacy ``approval`` step through the canonical reject_if_approvable."""
        tenant_id = sample_tenant["tenant_id"]
        _auth(client, tenant_id)
        media_buy_id = f"mb_{uuid.uuid4().hex[:8]}"
        step_id = _make_step(tenant_id, sample_principal["principal_id"], "approval", media_buy_id=media_buy_id)

        resp = client.post(
            f"/tenant/{tenant_id}/media-buy/{media_buy_id}/approve",
            data={"action": "reject", "reason": "no", "workflow_step_id": step_id},
            follow_redirects=False,
        )

        assert resp.status_code in (200, 302, 303)
        assert _status(tenant_id, step_id) == "rejected", (
            "a legacy approval step must be rejectable via the detail route"
        )


class TestApprovalClaimCompareAndSet:
    """[Round-19] claim_approval / reject_if_approvable are source-state-guarded compare-and-sets.

    Because ``approved`` is (deliberately) non-terminal, the broad ``transition_if_nonterminal``
    guard would admit an ``approved → approved`` no-op — a second concurrent approver that also
    runs execute_approved_media_buy (duplicate order). These pin the narrower guard.
    """

    def test_claim_approval_admits_exactly_one_approver(self, sample_tenant, sample_principal):
        tenant_id = sample_tenant["tenant_id"]
        step_id = _make_step(tenant_id, sample_principal["principal_id"], "requires_approval")

        with WorkflowUoW(tenant_id) as uow:
            first = uow.workflows.claim_approval(step_id)
            assert first is not None and first.status == "approved"
        with WorkflowUoW(tenant_id) as uow:
            second = uow.workflows.claim_approval(step_id)
            assert second is None, "a second approver must not re-claim an already-approved step"
        assert _status(tenant_id, step_id) == "approved"

    def test_claim_approval_refuses_non_approvable_statuses(self, sample_tenant, sample_principal):
        tenant_id = sample_tenant["tenant_id"]
        principal_id = sample_principal["principal_id"]
        for status in ("pending", "in_progress", "approved", "completed", "rejected", "canceled"):
            step_id = _make_step(tenant_id, principal_id, status)
            with WorkflowUoW(tenant_id) as uow:
                assert uow.workflows.claim_approval(step_id) is None, f"{status} must not be claimable"
            assert _status(tenant_id, step_id) == status, f"{status} must be left unchanged"
        # Positive control: pending_approval is claimable.
        ok = _make_step(tenant_id, principal_id, "pending_approval")
        with WorkflowUoW(tenant_id) as uow:
            assert uow.workflows.claim_approval(ok) is not None
        assert _status(tenant_id, ok) == "approved"

    def test_reject_if_approvable_refuses_approved_step(self, sample_tenant, sample_principal):
        tenant_id = sample_tenant["tenant_id"]
        principal_id = sample_principal["principal_id"]
        approved = _make_step(tenant_id, principal_id, "approved")
        with WorkflowUoW(tenant_id) as uow:
            assert uow.workflows.reject_if_approvable(approved, error_message="no") is None
        assert _status(tenant_id, approved) == "approved", "an approved step must not be rejectable"
        # Positive control: a step still awaiting a decision rejects.
        pending = _make_step(tenant_id, principal_id, "requires_approval")
        with WorkflowUoW(tenant_id) as uow:
            assert uow.workflows.reject_if_approvable(pending, error_message="bad") is not None
        assert _status(tenant_id, pending) == "rejected"

    def test_legacy_approval_status_is_claimable_and_rejectable(self, sample_tenant, sample_principal):
        """[Round-20] Regression guard: the legacy adapter-emitted ``approval`` status (GAM /
        Broadstreet / base_workflow default) is awaiting-decision and MUST be approvable and
        rejectable — the round-19 guard wrongly excluded it, 409-ing live human workflows."""
        tenant_id = sample_tenant["tenant_id"]
        principal_id = sample_principal["principal_id"]

        to_approve = _make_step(tenant_id, principal_id, "approval")
        with WorkflowUoW(tenant_id) as uow:
            assert uow.workflows.claim_approval(to_approve) is not None
        assert _status(tenant_id, to_approve) == "approved"

        to_reject = _make_step(tenant_id, principal_id, "approval")
        with WorkflowUoW(tenant_id) as uow:
            assert uow.workflows.reject_if_approvable(to_reject, error_message="no") is not None
        assert _status(tenant_id, to_reject) == "rejected"

    def test_get_approvable_step_for_object_uses_canonical_set(self, sample_tenant, sample_principal):
        """[Round-21] get_approvable_step_for_object finds a mapped step in ANY canonical
        approvable status — including the legacy ``approval`` alias — and returns None once the
        step is no longer approvable (so the admin media-buy detail lookup matches the CAS guard)."""
        tenant_id = sample_tenant["tenant_id"]
        principal_id = sample_principal["principal_id"]
        for status in ("requires_approval", "pending_approval", "approval"):
            mb = f"mb_{uuid.uuid4().hex[:8]}"
            step_id = _make_step(tenant_id, principal_id, status, media_buy_id=mb)
            with WorkflowUoW(tenant_id) as uow:
                found = uow.workflows.get_approvable_step_for_object("media_buy", mb)
                # Read attributes INSIDE the session (the ORM object detaches on exit).
                assert found is not None and found.step_id == step_id, f"a {status} step must be found"
        # A step that is no longer awaiting a decision is not returned.
        mb_done = f"mb_{uuid.uuid4().hex[:8]}"
        _make_step(tenant_id, principal_id, "approved", media_buy_id=mb_done)
        with WorkflowUoW(tenant_id) as uow:
            assert uow.workflows.get_approvable_step_for_object("media_buy", mb_done) is None

    def test_get_approvable_step_for_object_selects_exact_rendered_step(self, sample_tenant, sample_principal):
        """Multiple approval operations may map to one media buy; POST must revalidate the
        exact step rendered by GET instead of selecting an arbitrary sibling."""
        tenant_id = sample_tenant["tenant_id"]
        principal_id = sample_principal["principal_id"]
        media_buy_id = f"mb_{uuid.uuid4().hex[:8]}"
        first_id = _make_step(tenant_id, principal_id, "approval", media_buy_id=media_buy_id)
        second_id = _make_step(tenant_id, principal_id, "requires_approval", media_buy_id=media_buy_id)

        with WorkflowUoW(tenant_id) as uow:
            default = uow.workflows.get_approvable_step_for_object("media_buy", media_buy_id)
            exact = uow.workflows.get_approvable_step_for_object("media_buy", media_buy_id, step_id=second_id)
            assert default is not None and default.step_id == first_id
            assert exact is not None and exact.step_id == second_id

    def test_media_buy_detail_post_actions_only_selected_step(self, client, sample_tenant, sample_principal):
        """A stale/multi-step form actions its explicit step and leaves siblings untouched."""
        tenant_id = sample_tenant["tenant_id"]
        principal_id = sample_principal["principal_id"]
        _auth(client, tenant_id)
        media_buy_id = f"mb_{uuid.uuid4().hex[:8]}"
        first_id = _make_step(tenant_id, principal_id, "approval", media_buy_id=media_buy_id)
        second_id = _make_step(tenant_id, principal_id, "requires_approval", media_buy_id=media_buy_id)

        response = client.post(
            f"/tenant/{tenant_id}/media-buy/{media_buy_id}/approve",
            data={"action": "reject", "reason": "selected", "workflow_step_id": second_id},
            follow_redirects=False,
        )

        assert response.status_code in (302, 303)
        assert _status(tenant_id, first_id) == "approval"
        assert _status(tenant_id, second_id) == "rejected"

    def test_media_buy_detail_refuses_step_mapped_to_different_buy(self, client, sample_tenant, sample_principal):
        """A hidden step id is only a selector: POST must re-authorize its URL object mapping."""
        tenant_id = sample_tenant["tenant_id"]
        principal_id = sample_principal["principal_id"]
        _auth(client, tenant_id)
        requested_buy_id = f"mb_{uuid.uuid4().hex[:8]}"
        other_buy_id = f"mb_{uuid.uuid4().hex[:8]}"
        requested_step_id = _make_step(tenant_id, principal_id, "requires_approval", media_buy_id=requested_buy_id)
        other_step_id = _make_step(tenant_id, principal_id, "requires_approval", media_buy_id=other_buy_id)

        with (
            patch("src.admin.blueprints.operations.WorkflowRepository.claim_approval") as mock_claim,
            patch("src.core.tools.media_buy_create.execute_approved_media_buy") as mock_execute,
        ):
            response = client.post(
                f"/tenant/{tenant_id}/media-buy/{requested_buy_id}/approve",
                data={"action": "approve", "workflow_step_id": other_step_id},
                follow_redirects=False,
            )

        assert response.status_code in (302, 303)
        mock_claim.assert_not_called()
        mock_execute.assert_not_called()
        assert _status(tenant_id, requested_step_id) == "requires_approval"
        assert _status(tenant_id, other_step_id) == "requires_approval"


class TestWorkflowsRouteConflict:
    """[Round-19] The generic workflow approve/reject JSON route distinguishes a genuine
    concurrency conflict (409) from a missing step (404), and never double-executes."""

    def test_approve_of_already_approved_step_returns_409(self, client, sample_tenant, sample_principal):
        tenant_id = sample_tenant["tenant_id"]
        _auth(client, tenant_id)
        step_id = _make_step(tenant_id, sample_principal["principal_id"], "approved")

        with patch("src.core.tools.media_buy_create.execute_approved_media_buy") as mock_execute:
            resp = client.post(f"/tenant/{tenant_id}/workflows/wf_x/steps/{step_id}/approve")

        assert resp.status_code == 409, "a second approve of an approved step is a conflict, not 404"
        mock_execute.assert_not_called()
        assert _status(tenant_id, step_id) == "approved"

    def test_approve_of_nonexistent_step_returns_404(self, client, sample_tenant):
        tenant_id = sample_tenant["tenant_id"]
        _auth(client, tenant_id)
        resp = client.post(f"/tenant/{tenant_id}/workflows/wf_x/steps/step_missing/approve")
        assert resp.status_code == 404

    def test_reject_of_approved_step_returns_409(self, client, sample_tenant, sample_principal):
        tenant_id = sample_tenant["tenant_id"]
        _auth(client, tenant_id)
        step_id = _make_step(tenant_id, sample_principal["principal_id"], "approved")

        resp = client.post(f"/tenant/{tenant_id}/workflows/wf_x/steps/{step_id}/reject", json={"reason": "x"})

        assert resp.status_code == 409, "rejecting an approved step is a conflict — no rejecting a live order"
        assert _status(tenant_id, step_id) == "approved"

    def test_route_approves_legacy_approval_status_step(self, client, sample_tenant, sample_principal):
        """[Round-20] The generic approve route actions a legacy ``approval``-status step (200),
        not a spurious 409 — the round-19 regression."""
        tenant_id = sample_tenant["tenant_id"]
        _auth(client, tenant_id)
        step_id = _make_step(tenant_id, sample_principal["principal_id"], "approval")

        resp = client.post(f"/tenant/{tenant_id}/workflows/wf_x/steps/{step_id}/approve")

        assert resp.status_code == 200, "a legacy approval-status step must be approvable, not 409"
        assert _status(tenant_id, step_id) == "approved"


class TestMediaBuyDetailApprovalUI:
    """[Round-22] The media-buy detail PAGE (GET) must RENDER the approve/reject controls for a
    legacy ``approval``-status step. Round-21's tests exercised the repository lookup and the POST
    approve/reject routes, but nothing rendered the GET page — so reverting only the GET-side
    canonical lookup (hiding the UI again) would leave all tests green. This closes that gap."""

    def test_detail_page_renders_approval_controls_for_legacy_approval_step(self, client, factory_session):
        from tests.factories import MediaBuyFactory

        # A persisted media buy with its own linked tenant/principal (super-admin auth below
        # bypasses tenant scoping), plus a mapped legacy ``approval`` workflow step. Unique
        # SubFactory ids avoid the factory Sequence colliding with the persistent agent-db.
        suffix = uuid.uuid4().hex[:8]
        media_buy = MediaBuyFactory(
            tenant__tenant_id=f"t_{suffix}",
            tenant__subdomain=f"sub-{suffix}",
            principal__principal_id=f"p_{suffix}",
            principal__access_token=f"tok_{suffix}",
            media_buy_id=f"mb_{suffix}",
            status="pending_approval",
        )
        tenant_id = media_buy.tenant_id
        principal_id = media_buy.principal_id
        media_buy_id = media_buy.media_buy_id

        _auth(client, tenant_id)
        step_id = _make_step(tenant_id, principal_id, "approval", media_buy_id=media_buy_id)

        resp = client.get(f"/tenant/{tenant_id}/media-buy/{media_buy_id}")

        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        # The approval alert + approve/reject controls render ONLY inside {% if pending_approval_step %}.
        assert "Manual Approval Required" in html, "detail page must show the approval alert for a legacy approval step"
        assert "Approve" in html and "Reject" in html, "approve/reject controls must render"
        # And the approve control posts to the media-buy approve route for THIS buy.
        assert f"/media-buy/{media_buy_id}/approve" in html
        assert f'name="workflow_step_id" value="{step_id}"' in html
