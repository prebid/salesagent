#!/usr/bin/env python3
"""
Test complete lifecycle of workflows in the system.
"""

import uuid

import pytest
from sqlalchemy import delete, func, select

from src.core.context_manager import ContextManager
from src.core.database.database_session import get_db_session
from src.core.database.models import Context, Principal, Tenant, WorkflowStep


@pytest.mark.integration
@pytest.mark.requires_db
class TestWorkflowLifecycle:
    """Test complete workflow lifecycle scenarios."""

    @pytest.fixture(autouse=True)
    def setup(self, integration_db):
        """Set up test environment."""
        self.ctx_mgr = ContextManager()
        self.tenant_id = "test_tenant"
        self.principal_id = "test_principal"

        # Clean up any existing test data before each test
        with get_db_session() as session:
            # First delete workflow steps through context relationship
            contexts = session.scalars(select(Context).where(Context.tenant_id == self.tenant_id)).all()
            for ctx in contexts:
                session.execute(delete(WorkflowStep).where(WorkflowStep.context_id == ctx.context_id))
            # Then delete contexts
            session.execute(delete(Context).where(Context.tenant_id == self.tenant_id))
            # Delete principal
            session.execute(delete(Principal).where(Principal.tenant_id == self.tenant_id))
            # Delete tenant
            session.execute(delete(Tenant).where(Tenant.tenant_id == self.tenant_id))
            session.commit()

            # Create test tenant and principal for the tests
            tenant = Tenant(
                tenant_id=self.tenant_id, name="Test Tenant", subdomain="test", is_active=True, ad_server="mock"
            )
            session.add(tenant)

            principal = Principal(
                tenant_id=self.tenant_id,
                principal_id=self.principal_id,
                name="Test Principal",
                access_token="test_token",
                platform_mappings={"mock": {"advertiser_id": "test_advertiser"}},
            )
            session.add(principal)
            session.commit()

    def test_sync_operation_no_workflow(self):
        """Test that synchronous operations don't create workflows."""
        # Synchronous operations (like get_products) should not create workflow steps
        # They execute immediately and return results

        with get_db_session() as session:
            # Verify no workflow steps exist for this tenant
            # Need to check through context relationship
            contexts = session.scalars(select(Context).where(Context.tenant_id == self.tenant_id)).all()
            steps_count = 0
            for ctx in contexts:
                steps_count += session.scalar(
                    select(func.count()).select_from(WorkflowStep).where(WorkflowStep.context_id == ctx.context_id)
                )
            assert steps_count == 0

            # No context needed for sync operations
            context_count = session.scalar(
                select(func.count()).select_from(Context).where(Context.tenant_id == self.tenant_id)
            )
            assert context_count == 0

    def test_async_operation_creates_workflow(self):
        """Test that async operations create workflow steps."""
        # Create context for async operation
        context = self.ctx_mgr.create_context(tenant_id=self.tenant_id, principal_id=self.principal_id)

        # Create workflow step for async media buy creation
        step = self.ctx_mgr.create_workflow_step(
            context_id=context.context_id,
            step_type="tool_call",
            owner="system",
            status="active",
            tool_name="create_media_buy",
            request_data={"product_ids": ["prod_1"], "budget": 5000.0},
        )

        assert step is not None
        assert step.status == "active"
        assert step.step_type == "tool_call"

        # Verify step is persisted
        with get_db_session() as session:
            persisted_step = session.scalars(select(WorkflowStep).filter_by(step_id=step.step_id)).first()
            assert persisted_step is not None

    def test_manual_approval_workflow(self):
        """Test workflow requiring manual approval."""
        # Create context
        context = self.ctx_mgr.create_context(tenant_id=self.tenant_id, principal_id=self.principal_id)

        media_buy_id = f"mb_{uuid.uuid4().hex[:8]}"

        # Step 1: Create media buy (pending approval)
        step1 = self.ctx_mgr.create_workflow_step(
            context_id=context.context_id,
            step_type="approval",
            owner="publisher",
            status="requires_approval",
            tool_name="create_media_buy",
            request_data={
                "product_ids": ["prod_1"],
                "budget": 50000.0,  # High budget requiring approval
                "media_buy_id": media_buy_id,
            },
            object_mappings=[{"object_type": "media_buy", "object_id": media_buy_id, "action": "create_pending"}],
        )

        # Verify step requires approval
        assert step1.status == "requires_approval"
        assert step1.owner == "publisher"

        # Step 2: Add reviewer comment
        self.ctx_mgr.update_workflow_step(
            step_id=step1.step_id,
            add_comment={"user": "reviewer@publisher.com", "comment": "Reviewing budget allocation"},
        )

        # Step 3: Approve the workflow
        self.ctx_mgr.update_workflow_step(
            step_id=step1.step_id,
            status="completed",
            response_data={"approved": True, "approved_by": "reviewer@publisher.com", "media_buy_id": media_buy_id},
        )

        # Verify approval
        with get_db_session() as session:
            approved_step = session.scalars(select(WorkflowStep).filter_by(step_id=step1.step_id)).first()
            assert approved_step.status == "completed"
            assert approved_step.response_data["approved"] is True
            assert len(approved_step.comments) == 1
        assert approved_step.comments[0]["text"] == "Reviewing budget allocation"

    def test_workflow_failure_handling(self):
        """Test handling of failed workflow steps."""
        context = self.ctx_mgr.create_context(tenant_id=self.tenant_id, principal_id=self.principal_id)

        # Create a workflow step that will fail
        step = self.ctx_mgr.create_workflow_step(
            context_id=context.context_id,
            step_type="tool_call",
            owner="system",
            status="active",
            tool_name="update_media_buy",
            request_data={"media_buy_id": "non_existent_id", "budget": 1000.0},
        )

        # Simulate failure
        self.ctx_mgr.update_workflow_step(
            step_id=step.step_id,
            status="failed",
            error_message="Media buy not found",
            add_comment={"user": "system", "comment": "Failed to update non-existent media buy"},
        )

        # Verify failure is recorded
        with get_db_session() as session:
            failed_step = session.scalars(select(WorkflowStep).filter_by(step_id=step.step_id)).first()
            assert failed_step.status == "failed"
            assert "not found" in failed_step.error_message

    def test_object_lifecycle_tracking(self):
        """Test tracking object through multiple workflow steps."""
        context = self.ctx_mgr.create_context(tenant_id=self.tenant_id, principal_id=self.principal_id)

        media_buy_id = f"mb_{uuid.uuid4().hex[:8]}"

        # Create multiple workflow steps for same object
        steps = []

        # 1. Creation
        steps.append(
            self.ctx_mgr.create_workflow_step(
                context_id=context.context_id,
                step_type="tool_call",
                owner="system",
                status="completed",
                tool_name="create_media_buy",
                object_mappings=[{"object_type": "media_buy", "object_id": media_buy_id, "action": "create"}],
            )
        )

        # 2. Approval required
        steps.append(
            self.ctx_mgr.create_workflow_step(
                context_id=context.context_id,
                step_type="approval",
                owner="publisher",
                status="requires_approval",
                tool_name="approve_media_buy",
                object_mappings=[{"object_type": "media_buy", "object_id": media_buy_id, "action": "pending_approval"}],
            )
        )

        # 3. Approved
        self.ctx_mgr.update_workflow_step(step_id=steps[1].step_id, status="completed")

        # 4. Updated
        steps.append(
            self.ctx_mgr.create_workflow_step(
                context_id=context.context_id,
                step_type="tool_call",
                owner="system",
                status="completed",
                tool_name="update_media_buy",
                object_mappings=[{"object_type": "media_buy", "object_id": media_buy_id, "action": "update"}],
            )
        )

        # 5. Paused
        steps.append(
            self.ctx_mgr.create_workflow_step(
                context_id=context.context_id,
                step_type="tool_call",
                owner="system",
                status="completed",
                tool_name="pause_media_buy",
                object_mappings=[{"object_type": "media_buy", "object_id": media_buy_id, "action": "pause"}],
            )
        )

        # Get complete lifecycle
        lifecycle = self.ctx_mgr.get_object_lifecycle("media_buy", media_buy_id)

        # Verify all actions are tracked
        assert len(lifecycle) >= 4
        actions = [event["action"] for event in lifecycle]
        assert "create" in actions
        assert "pending_approval" in actions
        assert "update" in actions
        assert "pause" in actions

        # Verify chronological order
        timestamps = [event["created_at"] for event in lifecycle]
        assert timestamps == sorted(timestamps)

    def test_parallel_workflow_steps(self):
        """Test multiple workflow steps running in parallel."""
        context = self.ctx_mgr.create_context(tenant_id=self.tenant_id, principal_id=self.principal_id)

        # Create multiple active workflow steps
        steps = []
        for i in range(3):
            steps.append(
                self.ctx_mgr.create_workflow_step(
                    context_id=context.context_id,
                    step_type="tool_call",
                    owner="system",
                    status="active",
                    tool_name=f"operation_{i}",
                    request_data={"index": i},
                )
            )

        # Verify all are active
        with get_db_session() as session:
            active_count = session.scalar(
                select(func.count())
                .select_from(WorkflowStep)
                .where(WorkflowStep.context_id == context.context_id, WorkflowStep.status == "active")
            )
            assert active_count == 3

        # Complete them in different order
        self.ctx_mgr.update_workflow_step(steps[2].step_id, status="completed")
        self.ctx_mgr.update_workflow_step(steps[0].step_id, status="completed")
        self.ctx_mgr.update_workflow_step(steps[1].step_id, status="failed", error_message="Operation failed")

        # Verify final states
        status = self.ctx_mgr.get_context_status(context.context_id)
        assert status["counts"]["completed"] == 2
        assert status["counts"]["failed"] == 1
        assert status["counts"].get("active", 0) == 0  # 'active' status maps to 'in_progress'
        assert status["counts"].get("in_progress", 0) == 0

    def test_workflow_with_multiple_owners(self):
        """Test workflow steps with different owners."""
        context = self.ctx_mgr.create_context(tenant_id=self.tenant_id, principal_id=self.principal_id)

        # Create steps for different owners
        principal_step = self.ctx_mgr.create_workflow_step(
            context_id=context.context_id,
            step_type="tool_call",
            owner="principal",
            status="pending",
            tool_name="add_creative",
        )

        publisher_step = self.ctx_mgr.create_workflow_step(
            context_id=context.context_id,
            step_type="approval",
            owner="publisher",
            status="requires_approval",
            tool_name="approve_creative",
        )

        system_step = self.ctx_mgr.create_workflow_step(
            context_id=context.context_id,
            step_type="tool_call",
            owner="system",
            status="pending",
            tool_name="process_creative",
        )

        # Get pending steps by owner
        principal_pending = self.ctx_mgr.get_pending_steps(owner="principal")
        publisher_pending = self.ctx_mgr.get_pending_steps(owner="publisher")
        system_pending = self.ctx_mgr.get_pending_steps(owner="system")

        # Verify correct filtering
        assert len(principal_pending) == 1
        assert principal_pending[0].step_id == principal_step.step_id

        assert len(publisher_pending) == 1
        assert publisher_pending[0].step_id == publisher_step.step_id

        assert len(system_pending) == 1
        assert system_pending[0].step_id == system_step.step_id
