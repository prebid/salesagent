#!/usr/bin/env python3
"""
Test workflow approval system for manual approvals.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, select

from src.core.context_manager import ContextManager
from src.core.database.database_session import get_db_session
from src.core.database.models import Context, ObjectWorkflowMapping, WorkflowStep
from tests.utils.database_helpers import get_utc_now


@pytest.mark.integration
class TestWorkflowApproval:
    """Test the workflow approval system."""

    @pytest.fixture
    def context_manager(self):
        """Create a context manager instance."""
        return ContextManager()

    def test_create_approval_workflow(self, integration_db, context_manager):
        """Test creating a workflow that requires approval."""
        tenant_id = "test_tenant"
        principal_id = "test_principal"
        media_buy_id = "mb_test_123"

        with get_db_session() as db_session:
            # Clean up any existing test data
            # First delete workflow steps through context relationship
            contexts = db_session.scalars(select(Context).where(Context.tenant_id == tenant_id)).all()
            for ctx in contexts:
                db_session.execute(delete(WorkflowStep).where(WorkflowStep.context_id == ctx.context_id))
            # Then delete contexts
            db_session.execute(delete(Context).where(Context.tenant_id == tenant_id))
            db_session.commit()

        # Create context for async workflow
        context = context_manager.create_context(
            tenant_id=tenant_id,
            principal_id=principal_id,
            initial_conversation=[
                {"role": "user", "content": "Create a media buy", "timestamp": get_utc_now().isoformat()}
            ],
        )

        # Create approval workflow step
        step = context_manager.create_workflow_step(
            context_id=context.context_id,
            step_type="approval",
            owner="publisher",
            status="requires_approval",
            tool_name="create_media_buy",
            request_data={"product_ids": ["prod_1"], "budget": 5000.0, "media_buy_id": media_buy_id},
            object_mappings=[{"object_type": "media_buy", "object_id": media_buy_id, "action": "approve"}],
            initial_comment="Manual approval required for media buy",
        )

        assert step.status == "requires_approval"
        assert step.owner == "publisher"
        assert step.step_type == "approval"

        # Verify object mapping was created
        with get_db_session() as db_session:
            mapping = db_session.scalars(
                select(ObjectWorkflowMapping).filter_by(object_type="media_buy", object_id=media_buy_id)
            ).first()
            assert mapping is not None
            assert mapping.action == "approve"

    def test_approve_workflow_step(self, integration_db, context_manager):
        """Test approving a workflow step."""
        tenant_id = "test_tenant"
        principal_id = "test_principal"

        # Create context and approval step
        context = context_manager.create_context(tenant_id=tenant_id, principal_id=principal_id)

        step = context_manager.create_workflow_step(
            context_id=context.context_id,
            step_type="approval",
            owner="publisher",
            status="requires_approval",
            tool_name="update_media_buy",
            request_data={"media_buy_id": "mb_123", "budget": 10000},
        )

        # Approve the step
        context_manager.update_workflow_step(
            step_id=step.step_id,
            status="completed",
            response_data={
                "approved": True,
                "approved_by": "admin@publisher.com",
                "approved_at": datetime.now(UTC).isoformat(),
            },
            add_comment={"user": "admin@publisher.com", "comment": "Approved after review"},
        )

        # Verify the update
        with get_db_session() as db_session:
            updated_step = db_session.scalars(select(WorkflowStep).filter_by(step_id=step.step_id)).first()

            assert updated_step.status == "completed"
            assert updated_step.response_data["approved"] is True
            assert len(updated_step.comments) == 1
            assert updated_step.comments[0]["text"] == "Approved after review"

    def test_reject_workflow_step(self, integration_db, context_manager):
        """Test rejecting a workflow step."""
        tenant_id = "test_tenant"
        principal_id = "test_principal"

        # Create context and approval step
        context = context_manager.create_context(tenant_id=tenant_id, principal_id=principal_id)

        step = context_manager.create_workflow_step(
            context_id=context.context_id,
            step_type="approval",
            owner="publisher",
            status="requires_approval",
            tool_name="create_media_buy",
            request_data={"budget": 100000},  # High budget requiring approval
        )

        # Reject the step
        context_manager.update_workflow_step(
            step_id=step.step_id,
            status="failed",
            error_message="Budget exceeds approval limit",
            add_comment={"user": "admin@publisher.com", "comment": "Budget too high for this campaign type"},
        )

        # Verify the rejection
        with get_db_session() as db_session:
            updated_step = db_session.scalars(select(WorkflowStep).filter_by(step_id=step.step_id)).first()

            assert updated_step.status == "failed"
            assert "Budget exceeds" in updated_step.error_message

    def test_get_pending_approvals(self, integration_db, context_manager):
        """Test getting pending approval steps."""
        tenant_id = "test_tenant"

        with get_db_session() as db_session:
            # Clean up existing data
            # First delete workflow steps through context relationship
            contexts = db_session.scalars(select(Context).where(Context.tenant_id == tenant_id)).all()
            for ctx in contexts:
                db_session.execute(delete(WorkflowStep).where(WorkflowStep.context_id == ctx.context_id))
            db_session.commit()

        # Create multiple workflow steps with different statuses
        context = context_manager.create_context(tenant_id=tenant_id, principal_id="test_principal")

        # Create pending approval step
        step1 = context_manager.create_workflow_step(
            context_id=context.context_id,
            step_type="approval",
            owner="publisher",
            status="requires_approval",
            tool_name="create_media_buy",
        )

        # Create completed step
        step2 = context_manager.create_workflow_step(
            context_id=context.context_id,
            step_type="tool_call",
            owner="system",
            status="completed",
            tool_name="get_products",
        )

        # Create another pending approval
        step3 = context_manager.create_workflow_step(
            context_id=context.context_id,
            step_type="approval",
            owner="publisher",
            status="requires_approval",
            tool_name="update_media_buy",
        )

        # Get pending approvals
        pending_steps = context_manager.get_pending_steps(owner="publisher")

        assert len(pending_steps) == 2
        pending_ids = [s.step_id for s in pending_steps]
        assert step1.step_id in pending_ids
        assert step3.step_id in pending_ids
        assert step2.step_id not in pending_ids

    def test_workflow_lifecycle_tracking(self, integration_db, context_manager):
        """Test tracking the complete lifecycle of an object through workflows."""
        tenant_id = "test_tenant"
        media_buy_id = "mb_lifecycle_123"

        with get_db_session() as db_session:
            # Clean up
            db_session.execute(delete(ObjectWorkflowMapping).where(ObjectWorkflowMapping.object_id == media_buy_id))
            db_session.commit()

        context = context_manager.create_context(tenant_id=tenant_id, principal_id="test_principal")

        # Step 1: Create
        step1 = context_manager.create_workflow_step(
            context_id=context.context_id,
            step_type="tool_call",
            owner="system",
            status="completed",
            tool_name="create_media_buy",
            object_mappings=[{"object_type": "media_buy", "object_id": media_buy_id, "action": "create"}],
        )

        # Step 2: Approve
        step2 = context_manager.create_workflow_step(
            context_id=context.context_id,
            step_type="approval",
            owner="publisher",
            status="requires_approval",
            tool_name="approve_media_buy",
            object_mappings=[{"object_type": "media_buy", "object_id": media_buy_id, "action": "approve"}],
        )

        # Step 3: Update
        step3 = context_manager.create_workflow_step(
            context_id=context.context_id,
            step_type="tool_call",
            owner="system",
            status="completed",
            tool_name="update_media_buy",
            object_mappings=[{"object_type": "media_buy", "object_id": media_buy_id, "action": "update"}],
        )

        # Get lifecycle
        lifecycle = context_manager.get_object_lifecycle("media_buy", media_buy_id)

        assert len(lifecycle) == 3
        actions = [event["action"] for event in lifecycle]
        assert "create" in actions
        assert "approve" in actions
        assert "update" in actions

        # Verify chronological order
        for i in range(len(lifecycle) - 1):
            assert lifecycle[i]["created_at"] <= lifecycle[i + 1]["created_at"]
