"""approve_media_buy step selection is constrained to the CREATE-approval step (#1637).

``WorkflowRepository.list_actionable_steps_for_object`` (used by the approve/reject route's
``_select_actionable_create_step``) must return only steps of the given ``tool_name``, so the
route acts on the media-buy CREATION step (``create_media_buy``) — never an unrelated
actionable step (e.g. an ``update_media_buy`` step) mapped to the same buy. The route
finalizes a CREATE (it runs ``execute_approved_media_buy`` / the create-reject cascade), so
acting on a non-creation step would finalize the wrong workflow record; "newest actionable"
alone does not establish that the step is the creation.
"""

import pytest

from src.core.context_manager import ContextManager
from src.core.database.repositories import WorkflowUoW

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

_ACTIONABLE = ("requires_approval", "pending_approval", "in_progress")


def _make_mapped_step(cm, tenant_id, principal_id, media_buy_id, *, tool_name, status):
    """A workflow step mapped to ``media_buy_id`` (the shape the approve route queries)."""
    ctx = cm.create_context(tenant_id=tenant_id, principal_id=principal_id)
    return cm.create_workflow_step(
        context_id=ctx.context_id,
        step_type="media_buy_creation",
        owner="system",
        status=status,
        tool_name=tool_name,
        request_data={"media_buy_id": media_buy_id},
        object_mappings=[{"object_type": "media_buy", "object_id": media_buy_id, "action": "create"}],
    )


def _actionable_create_steps(tenant_id, media_buy_id):
    """Return ``[(step_id, tool_name), ...]`` newest-first — extracted INSIDE the UoW session
    so the assertions don't touch detached ORM instances after the session closes."""
    with WorkflowUoW(tenant_id) as uow:
        assert uow.workflows is not None
        steps = uow.workflows.list_actionable_steps_for_object(
            "media_buy", media_buy_id, tool_name="create_media_buy", statuses=_ACTIONABLE
        )
        return [(s.step_id, s.tool_name) for s in steps]


class TestApproveStepSelection:
    def test_non_creation_in_progress_step_is_not_selected(self, integration_db, sample_tenant, sample_principal):
        """A NEWER unrelated ``update_media_buy`` in_progress step mapped to the same buy is
        NOT returned — only the older ``create_media_buy`` step is, because the query is
        constrained to the creation tool_name (not merely 'newest actionable')."""
        tenant_id = sample_tenant["tenant_id"]
        principal_id = sample_principal["principal_id"]
        media_buy_id = "mb_sel_mixed"
        cm = ContextManager()

        # Create the create step FIRST, then a newer update step — so 'newest actionable'
        # alone would wrongly pick the update step.
        create_step = _make_mapped_step(
            cm, tenant_id, principal_id, media_buy_id, tool_name="create_media_buy", status="in_progress"
        )
        update_step = _make_mapped_step(
            cm, tenant_id, principal_id, media_buy_id, tool_name="update_media_buy", status="in_progress"
        )

        steps = _actionable_create_steps(tenant_id, media_buy_id)

        assert [step_id for step_id, _ in steps] == [create_step.step_id]
        assert all(tool_name == "create_media_buy" for _, tool_name in steps)
        assert update_step.step_id not in {step_id for step_id, _ in steps}

    def test_update_only_buy_selects_no_step(self, integration_db, sample_tenant, sample_principal):
        """A buy with ONLY a non-creation actionable step returns nothing — an update step is
        never mistaken for the creation the approve route finalizes."""
        tenant_id = sample_tenant["tenant_id"]
        principal_id = sample_principal["principal_id"]
        media_buy_id = "mb_sel_updateonly"
        cm = ContextManager()

        _make_mapped_step(cm, tenant_id, principal_id, media_buy_id, tool_name="update_media_buy", status="in_progress")

        assert _actionable_create_steps(tenant_id, media_buy_id) == []

    def test_newest_create_step_wins_among_multiple(self, integration_db, sample_tenant, sample_principal):
        """Among several actionable CREATE steps (a re-approval history), the newest is first —
        the secondary ordering is preserved after the tool_name constraint."""
        tenant_id = sample_tenant["tenant_id"]
        principal_id = sample_principal["principal_id"]
        media_buy_id = "mb_sel_multi"
        cm = ContextManager()

        _make_mapped_step(
            cm, tenant_id, principal_id, media_buy_id, tool_name="create_media_buy", status="requires_approval"
        )
        newest = _make_mapped_step(
            cm, tenant_id, principal_id, media_buy_id, tool_name="create_media_buy", status="in_progress"
        )

        steps = _actionable_create_steps(tenant_id, media_buy_id)

        assert len(steps) == 2
        assert steps[0][0] == newest.step_id  # newest first
