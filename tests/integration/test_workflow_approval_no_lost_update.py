"""Regression test for the nested-session lost-update bug in approve_workflow_step.

Background
==========
Before this refactor, ``approve_workflow_step()`` opened an outer ``get_db_session()``
context, then called ``execute_approved_media_buy()`` — which opens its own inner UoW
session that writes ``media_buy.status = "active"``. When control returned to the
outer handler, it then wrote ``media_buy.status = "scheduled"`` on the outer-session
ORM instance. Under ``scoped_session`` (current default) both "sessions" share the
same thread-local, which masks the bug. Under bare ``sessionmaker`` (the B2 change
in the Flask→FastAPI migration), the outer write is a LOST UPDATE that clobbers the
adapter-set ``"active"`` status.

The fix: close the outer session BEFORE calling ``execute_approved_media_buy()``,
then use a fresh session for approval-audit fields — and stop mutating
``media_buy.status`` in the outer handler at all.

This test drives the Flask route end-to-end (via ``Flask.test_client``). The
adapter call is mocked to behave like the real ``execute_approved_media_buy``:
it opens a fresh ``get_db_session()`` and writes ``status = "active"``. If the
refactor regresses and the outer handler again writes to ``media_buy.status``
after the adapter call, this test will fail because the final persisted status
would not be ``"active"``.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import select

from src.admin.app import create_app
from src.core.database.database_session import get_db_session
from src.core.database.models import (
    MediaBuy,
    WorkflowStep,
)
from tests.integration._approval_helpers import (
    assert_media_buy_status_active,
    authenticated,  # noqa: F401  pytest fixture re-exported; consumed via fixture-injection below
    build_approval_scenario,
    make_csrf_disabled_client,
)
from tests.integration._approval_helpers import (
    simulate_adapter_sets_active as _simulate_adapter_sets_active,
)

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

_WORKFLOWS_APP = create_app()


@pytest.fixture
def client():
    """Flask test client bound to the workflows-test app instance."""
    with make_csrf_disabled_client(_WORKFLOWS_APP) as c:
        yield c


_WORKFLOW_ID_FOR_URLS = "wf_lost_update_regression"


@pytest.fixture
def approval_fixture(integration_db, sample_tenant, sample_principal):
    """Scenario fixture for workflows.py::approve_workflow_step regression.

    Augments the shared scenario with a workflow_id that the workflows route
    includes in its URL (unlike the operations route which addresses by step_id).
    """
    scenario = build_approval_scenario(
        tenant_id=sample_tenant["tenant_id"],
        principal_id=sample_principal["principal_id"],
        id_prefix="lost_update_regression",
    )
    scenario["workflow_id"] = _WORKFLOW_ID_FOR_URLS
    return scenario


class TestApproveWorkflowStepNoLostUpdate:
    """Regression coverage for the outer-session lost-update bug."""

    def test_adapter_active_status_survives_outer_handler(
        self,
        client,
        authenticated,  # noqa: F811 — fixture re-import per pytest idiom
        approval_fixture,
    ):
        """After approving the workflow step, the media buy must end up with the
        adapter-set status "active" — NOT "scheduled" from a post-hoc outer-session
        mutation.
        """
        tenant_id = approval_fixture["tenant_id"]
        media_buy_id = approval_fixture["media_buy_id"]
        step_id = approval_fixture["step_id"]
        workflow_id = approval_fixture["workflow_id"]

        # Patch the adapter to simulate its real session-isolated behavior.
        with patch(
            "src.core.tools.media_buy_create.execute_approved_media_buy",
            side_effect=_simulate_adapter_sets_active,
        ) as mock_adapter:
            response = client.post(
                f"/tenant/{tenant_id}/workflows/{workflow_id}/steps/{step_id}/approve",
            )

        # The adapter MUST have been invoked — otherwise the test would pass for
        # the wrong reason (status never leaves "pending_approval").
        mock_adapter.assert_called_once_with(media_buy_id, tenant_id)

        assert (
            response.status_code == 200
        ), f"approve_workflow_step returned {response.status_code}: {response.get_data(as_text=True)}"
        payload = response.get_json()
        assert payload.get("success") is True, f"approve_workflow_step did not succeed: {payload}"

        # The invariant: adapter's "active" status must persist. Under the buggy
        # pattern, the outer session would have written "scheduled" back after
        # the adapter's inner UoW closed.
        assert_media_buy_status_active(tenant_id, media_buy_id)
        with get_db_session() as session:
            mb = session.scalars(select(MediaBuy).filter_by(tenant_id=tenant_id, media_buy_id=media_buy_id)).first()
            assert mb is not None
            # Approval audit fields must be populated by Phase 2 (fresh session).
            assert mb.approved_at is not None, "approved_at must be set by the fresh Phase 2 session"
            assert mb.approved_by == "admin@example.com"

            # Workflow step itself must have completed approval.
            step = session.scalars(select(WorkflowStep).filter_by(step_id=step_id)).first()
            assert step is not None
            assert step.status == "approved"
