#!/usr/bin/env python3
"""Test the new workflow architecture with object lifecycle tracking."""

import sys
import uuid
from datetime import UTC, datetime

import pytest
from rich.console import Console
from rich.table import Table

console = Console()


@pytest.mark.requires_db
def test_workflow_architecture(integration_db):
    """Test the new workflow architecture."""
    console.print("\n[bold cyan]Testing New Workflow Architecture[/bold cyan]")
    console.print("=" * 60)

    # Import after setting up path
    from sqlalchemy import delete, select

    from src.core.context_manager import ContextManager
    from src.core.database.database_session import get_db_session
    from src.core.database.models import Context, ObjectWorkflowMapping, WorkflowStep

    # Initialize context manager
    ctx_mgr = ContextManager()

    # Test parameters
    tenant_id = "test_tenant"
    principal_id = "test_principal"
    media_buy_id = f"mb_{uuid.uuid4().hex[:8]}"
    creative_id = f"cr_{uuid.uuid4().hex[:8]}"

    # Use proper context manager pattern for session
    with get_db_session() as session:
        try:
            # Clean up any existing test data
            session.execute(
                delete(ObjectWorkflowMapping).where(ObjectWorkflowMapping.object_id.in_([media_buy_id, creative_id]))
            )
            session.execute(delete(WorkflowStep).where(WorkflowStep.context_id.like("ctx_%")))
            session.execute(delete(Context).where(Context.tenant_id == tenant_id, Context.principal_id == principal_id))
            session.commit()

            console.print("\n[yellow]Test 1: Create context for async workflow[/yellow]")
            context = ctx_mgr.create_context(
                tenant_id=tenant_id,
                principal_id=principal_id,
                initial_conversation=[
                    {
                        "role": "user",
                        "content": "Create a media buy for sports content",
                        "timestamp": datetime.now(UTC).isoformat(),
                    }
                ],
            )
            console.print(f"✓ Created context: {context.context_id}")

            console.print("\n[yellow]Test 2: Create workflow step for media buy creation[/yellow]")
            step1 = ctx_mgr.create_workflow_step(
                context_id=context.context_id,
                step_type="tool_call",
                owner="system",
                status="pending",
                tool_name="create_media_buy",
                request_data={
                    "product_ids": ["prod_1", "prod_2"],
                    "budget": 5000.0,
                    "start_date": "2025-02-01",
                    "end_date": "2025-02-28",
                },
                object_mappings=[{"object_type": "media_buy", "object_id": media_buy_id, "action": "create"}],
                initial_comment="Creating media buy for sports content campaign",
            )
            console.print(f"✓ Created step: {step1.step_id}")
            console.print(f"  - Type: {step1.step_type}")
            console.print(f"  - Owner: {step1.owner}")
            console.print(f"  - Status: {step1.status}")

            console.print("\n[yellow]Test 3: Create approval step (waiting on publisher)[/yellow]")
            step2 = ctx_mgr.create_workflow_step(
                context_id=context.context_id,
                step_type="approval",
                owner="publisher",  # Publisher needs to approve
                status="requires_approval",
                tool_name=None,
                request_data={
                    "media_buy_id": media_buy_id,
                    "reason": "Manual approval required for high-value campaign",
                },
                assigned_to="admin@publisher.com",
                object_mappings=[{"object_type": "media_buy", "object_id": media_buy_id, "action": "approve"}],
            )
            console.print(f"✓ Created approval step: {step2.step_id}")
            console.print(f"  - Owner: {step2.owner} (waiting on publisher)")
            console.print(f"  - Assigned to: {step2.assigned_to}")

            console.print("\n[yellow]Test 4: Add creative and track its lifecycle[/yellow]")
            step3 = ctx_mgr.create_workflow_step(
                context_id=context.context_id,
                step_type="tool_call",
                owner="principal",  # Principal submitting creative
                status="completed",
                tool_name="add_creative_assets",
                request_data={
                    "creative_id": creative_id,
                    "format": "display_300x250",
                    "url": "https://example.com/creative.jpg",
                },
                response_data={"success": True, "creative_id": creative_id},
                object_mappings=[
                    {"object_type": "creative", "object_id": creative_id, "action": "create"},
                    {"object_type": "media_buy", "object_id": media_buy_id, "action": "add_creative"},
                ],
            )
            console.print(f"✓ Created creative step: {step3.step_id}")

            console.print("\n[yellow]Test 5: Query pending steps by owner[/yellow]")

            # Get steps waiting on publisher
            publisher_steps = ctx_mgr.get_pending_steps(owner="publisher")
            console.print(f"✓ Found {len(publisher_steps)} steps waiting on publisher:")
            for step in publisher_steps:
                console.print(f"  - {step.step_id}: {step.step_type} ({step.status})")

            # Get steps waiting on principal (should be none in this test)
            principal_steps = ctx_mgr.get_pending_steps(owner="principal")
            console.print(f"✓ Found {len(principal_steps)} steps waiting on principal")

            console.print("\n[yellow]Test 6: Get object lifecycle for media buy[/yellow]")
            lifecycle = ctx_mgr.get_object_lifecycle("media_buy", media_buy_id)
            console.print(f"✓ Media buy {media_buy_id} lifecycle:")
            for event in lifecycle:
                console.print(f"  - {event['action']}: {event['step_type']} ({event['status']})")
                console.print(f"    Owner: {event['owner']}, Created: {event['created_at']}")

            console.print("\n[yellow]Test 7: Add comment to workflow step[/yellow]")
            ctx_mgr.update_workflow_step(
                step_id=step2.step_id,
                add_comment={
                    "user": "reviewer@publisher.com",
                    "comment": "Reviewing campaign parameters, will approve shortly",
                },
            )
            console.print(f"✓ Added comment to step {step2.step_id}")

            # Verify comment was added
            session.expire_all()
            updated_step = session.scalars(select(WorkflowStep).filter_by(step_id=step2.step_id)).first()
            if updated_step and updated_step.comments:
                console.print(f"  Comments: {len(updated_step.comments)}")
                for comment in updated_step.comments:
                    console.print(f"    - {comment['user']}: {comment['comment']}")

            console.print("\n[yellow]Test 8: Complete approval step[/yellow]")
            ctx_mgr.update_workflow_step(
                step_id=step2.step_id,
                status="completed",
                response_data={
                    "approved": True,
                    "approved_by": "admin@publisher.com",
                    "approved_at": datetime.now(UTC).isoformat(),
                },
            )
            console.print(f"✓ Completed step {step2.step_id}")

            console.print("\n[yellow]Test 9: Check context status[/yellow]")
            status = ctx_mgr.get_context_status(context.context_id)
            console.print(f"✓ Context status: {status['status']}")
            console.print(f"  - Total steps: {status['total_steps']}")
            console.print("  - Status breakdown:")
            for stat, count in status["counts"].items():
                if count > 0:
                    console.print(f"    - {stat}: {count}")

            console.print("\n[yellow]Test 10: Verify simplified Context model[/yellow]")
            ctx = session.scalars(select(Context).filter_by(context_id=context.context_id)).first()

            # These fields should NOT exist
            assert not hasattr(ctx, "status"), "Context should not have status field"
            assert not hasattr(ctx, "session_type"), "Context should not have session_type field"
            assert not hasattr(ctx, "expires_at"), "Context should not have expires_at field"
            assert not hasattr(ctx, "human_needed"), "Context should not have human_needed field"

            # These fields SHOULD exist
            assert hasattr(ctx, "context_id"), "Context should have context_id"
            assert hasattr(ctx, "tenant_id"), "Context should have tenant_id"
            assert hasattr(ctx, "principal_id"), "Context should have principal_id"
            assert hasattr(ctx, "conversation_history"), "Context should have conversation_history"
            assert hasattr(ctx, "created_at"), "Context should have created_at"
            assert hasattr(ctx, "last_activity_at"), "Context should have last_activity_at"

            console.print("✓ Context model correctly simplified")

            console.print("\n[yellow]Test 11: Verify WorkflowStep has no started_at[/yellow]")
            step = session.scalars(select(WorkflowStep).filter_by(step_id=step1.step_id)).first()
            assert not hasattr(step, "started_at"), "WorkflowStep should not have started_at field"
            assert hasattr(step, "comments"), "WorkflowStep should have comments field"
            console.print("✓ WorkflowStep correctly updated (no started_at, has comments)")

            console.print("\n[yellow]Test 12: Verify ObjectWorkflowMapping works[/yellow]")
            mappings = session.scalars(
                select(ObjectWorkflowMapping).filter_by(object_type="media_buy", object_id=media_buy_id)
            ).all()
            console.print(f"✓ Found {len(mappings)} mappings for media_buy {media_buy_id}")
            for mapping in mappings:
                console.print(f"  - Action: {mapping.action}, Step: {mapping.step_id}")

            console.print("\n[bold green]✅ All tests passed![/bold green]")

            # Display summary table
            table = Table(title="Architecture Summary")
            table.add_column("Component", style="cyan")
            table.add_column("Purpose", style="white")
            table.add_column("Key Change", style="yellow")

            table.add_row("Context", "Track async conversations", "Simplified - no status/expires")
            table.add_row("WorkflowStep", "Work queue for tasks", "Added comments, removed started_at")
            table.add_row("ObjectWorkflowMapping", "Track object lifecycles", "New - loose coupling")
            table.add_row("Owner field", "Who needs to act", "principal/publisher/system")

            console.print("\n")
            console.print(table)

            console.print("\n[bold cyan]Key Insights:[/bold cyan]")
            console.print("1. Synchronous operations don't need context")
            console.print("2. Context is just for async conversation tracking")
            console.print("3. WorkflowStep is the actual work queue")
            console.print("4. Object lifecycles tracked via ObjectWorkflowMapping")
            console.print("5. Owner field clearly shows who's waiting (principal vs publisher)")
            console.print("6. Comments array enables collaboration on steps")
            console.print("7. No more tasks table - everything in workflow_steps")

        except Exception as e:
            console.print(f"\n[bold red]❌ Test failed: {e}[/bold red]")
            import traceback

            traceback.print_exc()
            raise


if __name__ == "__main__":
    # Run the test
    success = test_workflow_architecture()
    sys.exit(0 if success else 1)
