#!/usr/bin/env python3
"""Test script for simplified context persistence."""

import os
import sys
from datetime import UTC, datetime

from rich.console import Console

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.core.context_manager import ContextManager

console = Console()


def test_simplified_context(integration_db):
    """Test the simplified context system."""

    console.print("[bold blue]Testing Simplified Context Persistence[/bold blue]")
    console.print("=" * 50)

    # Initialize context manager (will use the integration_db fixture)
    ctx_manager = ContextManager()

    try:

        # Test 1: Create a simple context for async operation
        console.print("\n[yellow]Test 1: Creating context for async operation[/yellow]")
        ctx = ctx_manager.create_context(
            tenant_id="test_tenant",
            principal_id="test_principal",
            initial_conversation=[
                {
                    "timestamp": datetime.now(UTC).isoformat(),
                    "role": "user",
                    "content": "Create a media buy that needs approval",
                }
            ],
        )
        console.print(f"✓ Created context: {ctx.context_id}")
        console.print(f"  - Tenant: {ctx.tenant_id}")
        console.print(f"  - Principal: {ctx.principal_id}")
        console.print(f"  - Conversation entries: {len(ctx.conversation_history)}")

        # Test 2: Create workflow steps (the actual work queue)
        console.print("\n[yellow]Test 2: Creating workflow steps[/yellow]")

        # Step 1: Tool call that needs approval
        step1 = ctx_manager.create_workflow_step(
            context_id=ctx.context_id,
            step_type="tool_call",
            owner="principal",
            status="pending",
            tool_name="create_media_buy",
            request_data={"budget": 5000, "products": ["prod_1", "prod_2"]},
            object_mappings=[{"object_type": "media_buy", "object_id": "mb_12345", "action": "create"}],
        )
        console.print(f"✓ Created step: {step1.step_id}")
        console.print(f"  - Type: {step1.step_type}")
        console.print(f"  - Status: {step1.status}")
        console.print(f"  - Owner: {step1.owner}")

        # Step 2: Approval needed
        step2 = ctx_manager.create_workflow_step(
            context_id=ctx.context_id,
            step_type="approval",
            owner="publisher",
            status="requires_approval",
            assigned_to="admin@publisher.com",
            request_data={"approval_type": "manual_review", "reason": "High budget"},
            initial_comment="High budget media buy requires manual approval",
        )
        console.print(f"✓ Created step: {step2.step_id}")
        console.print(f"  - Type: {step2.step_type}")
        console.print(f"  - Status: {step2.status}")
        console.print(f"  - Assigned to: {step2.assigned_to}")
        console.print(f"  - Comments: {len(step2.comments)}")

        # Test 3: Query pending steps (work queue)
        console.print("\n[yellow]Test 3: Querying pending steps[/yellow]")
        pending_steps = ctx_manager.get_pending_steps(owner="publisher")
        console.print(f"✓ Found {len(pending_steps)} pending steps for publisher")
        for step in pending_steps:
            console.print(f"  - {step.step_id}: {step.step_type} ({step.status})")

        # Test 4: Get context status from workflow steps
        console.print("\n[yellow]Test 4: Getting context status from workflow steps[/yellow]")
        status = ctx_manager.get_context_status(ctx.context_id)
        console.print(f"✓ Context status: {status['status']}")
        console.print(f"  - Total steps: {status.get('total_steps', 0)}")
        console.print(f"  - Status counts: {status.get('counts', {})}")

        # Test 5: Update workflow step
        console.print("\n[yellow]Test 5: Updating workflow step[/yellow]")
        ctx_manager.update_workflow_step(
            step1.step_id,
            status="completed",
            response_data={"media_buy_id": "mb_12345", "status": "active"},
            add_comment={"user": "system", "comment": "Media buy created successfully"},
        )
        console.print(f"✓ Updated step {step1.step_id} to completed")

        # Test 6: Add conversation message
        console.print("\n[yellow]Test 6: Adding conversation message[/yellow]")
        ctx_manager.add_message(
            ctx.context_id, role="assistant", content="Media buy has been approved and created successfully."
        )
        console.print("✓ Added message to conversation")

        # Test 7: Get updated context
        console.print("\n[yellow]Test 7: Getting updated context[/yellow]")
        updated_ctx = ctx_manager.get_context(ctx.context_id)
        console.print(f"✓ Retrieved context: {updated_ctx.context_id}")
        console.print(f"  - Conversation entries: {len(updated_ctx.conversation_history)}")
        console.print(f"  - Last activity: {updated_ctx.last_activity_at}")

        # Test 8: Get object lifecycle
        console.print("\n[yellow]Test 8: Getting object lifecycle[/yellow]")
        lifecycle = ctx_manager.get_object_lifecycle("media_buy", "mb_12345")
        console.print(f"✓ Found {len(lifecycle)} lifecycle events for media_buy mb_12345")
        for event in lifecycle:
            console.print(f"  - {event['action']}: {event['step_type']} ({event['status']})")

        # Test 9: Verify no status/session_type fields
        console.print("\n[yellow]Test 9: Verifying simplified schema[/yellow]")
        assert not hasattr(updated_ctx, "status"), "Context should not have status field"
        assert not hasattr(updated_ctx, "session_type"), "Context should not have session_type field"
        assert not hasattr(updated_ctx, "expires_at"), "Context should not have expires_at field"
        assert not hasattr(updated_ctx, "human_needed"), "Context should not have human_needed field"
        console.print("✓ Context model correctly simplified")

        console.print("\n[bold green]✅ All tests passed![/bold green]")

    except Exception as e:
        console.print(f"\n[bold red]❌ Test failed: {e}[/bold red]")
        import traceback

        traceback.print_exc()
        raise

    finally:
        # Clean up context manager
        if "ctx_manager" in locals():
            ctx_manager.close()


if __name__ == "__main__":
    test_simplified_context()
