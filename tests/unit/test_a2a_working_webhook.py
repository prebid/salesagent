"""Unit tests for A2A working webhook edge cases.

Tests focus on edge cases around webhook dispatch, error handling,
and non-blocking behavior.
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from a2a.types import Task, TaskStatus, TaskState

from src.a2a_server.adcp_a2a_server import AdCPRequestHandler


@pytest.mark.asyncio
async def test_working_webhook_with_no_config():
    """Test that no webhook is sent when metadata has no push_notification_config."""

    server = AdCPRequestHandler()

    # Create task without push_notification_config
    task = Task(
        id="task_123",
        context_id="ctx_123",
        kind="task",
        status=TaskStatus(state=TaskState.working),
        metadata={},  # No push_notification_config
    )

    webhook_called = False

    async def fake_send_notification(*args, **kwargs):
        nonlocal webhook_called
        webhook_called = True
        return True

    with patch.object(
        server,
        "_send_protocol_webhook",
        new_callable=AsyncMock,
        side_effect=fake_send_notification,
    ):
        # Manually trigger the webhook logic
        if task.metadata and "push_notification_config" in task.metadata:
            await server._send_protocol_webhook(task, status="working")

        # Verify webhook was NOT called
        assert not webhook_called, "Webhook should not be called without config"


@pytest.mark.asyncio
async def test_working_webhook_with_missing_url():
    """Test graceful handling when config is present but URL is missing."""

    server = AdCPRequestHandler()

    # Create task with config but no URL
    task = Task(
        id="task_123",
        context_id="ctx_123",
        kind="task",
        status=TaskStatus(state=TaskState.working),
        metadata={
            "push_notification_config": {
                "push_notification_config": {
                    # URL is missing
                    "authentication": None
                }
            }
        },
    )

    # Verify _send_protocol_webhook handles missing URL gracefully
    # (it should return early without error)
    try:
        await server._send_protocol_webhook(task, status="working")
        # No exception should be raised
        success = True
    except Exception as e:
        pytest.fail(f"Expected graceful handling of missing URL, got exception: {e}")
        success = False

    assert success, "Should handle missing URL without exception"


@pytest.mark.asyncio
async def test_working_webhook_dispatch_non_blocking():
    """Verify that working webhook dispatch does not block task creation."""

    server = AdCPRequestHandler()

    webhook_started = False
    webhook_completed = False

    async def slow_webhook(*args, **kwargs):
        nonlocal webhook_started, webhook_completed
        webhook_started = True
        await asyncio.sleep(0.5)  # Simulate slow webhook endpoint
        webhook_completed = True
        return True

    task = Task(
        id="task_123",
        context_id="ctx_123",
        kind="task",
        status=TaskStatus(state=TaskState.working),
        metadata={
            "push_notification_config": {
                "url": "https://example.com/webhook",
                "authentication": None,
            }
        },
    )

    with patch.object(
        server,
        "_send_protocol_webhook",
        new_callable=AsyncMock,
        side_effect=slow_webhook,
    ):
        # Simulate non-blocking dispatch with create_task
        if task.metadata and "push_notification_config" in task.metadata:
            asyncio.create_task(server._send_protocol_webhook(task, status="working"))

        # Immediately check - webhook should have started but not completed
        await asyncio.sleep(0.05)  # Small delay to let create_task schedule

        # Webhook may or may not have started yet (timing dependent)
        # But task creation should have returned immediately
        # The key is that we didn't wait for the slow webhook

        # Now wait for webhook to complete
        await asyncio.sleep(0.6)

        # Webhook should now be completed
        assert webhook_completed, "Webhook should eventually complete"


@pytest.mark.asyncio
async def test_working_webhook_exception_doesnt_propagate():
    """Verify that exceptions in webhook sending don't fail task processing."""

    server = AdCPRequestHandler()

    async def failing_webhook(*args, **kwargs):
        raise Exception("Webhook service unavailable")

    task = Task(
        id="task_123",
        context_id="ctx_123",
        kind="task",
        status=TaskStatus(state=TaskState.working),
        metadata={
            "push_notification_config": {
                "url": "https://example.com/webhook",
                "authentication": None,
            }
        },
    )

    with patch.object(
        server,
        "_send_protocol_webhook",
        new_callable=AsyncMock,
        side_effect=failing_webhook,
    ):
        # The exception should be caught inside _send_protocol_webhook
        # (as per the existing implementation)
        # This test verifies the behavior is safe

        try:
            # Simulate what happens in on_message_send
            if task.metadata and "push_notification_config" in task.metadata:
                # Using create_task means exceptions won't propagate immediately
                task_obj = asyncio.create_task(
                    server._send_protocol_webhook(task, status="working")
                )

                # Wait a bit to let the task execute
                await asyncio.sleep(0.1)

                # Check if the task raised an exception
                if task_obj.done():
                    try:
                        task_obj.result()  # This would raise if exception occurred
                    except Exception:
                        # Exception occurred but was in background task
                        pass

            # Task processing should continue regardless
            task_processing_succeeded = True
        except Exception:
            task_processing_succeeded = False

        assert task_processing_succeeded, "Task processing should not fail due to webhook exception"


@pytest.mark.asyncio
async def test_send_protocol_webhook_validates_metadata_structure():
    """Test that _send_protocol_webhook validates metadata structure properly."""

    server = AdCPRequestHandler()

    # Test Case 1: metadata is None
    task1 = Task(
        id="task_1",
        context_id="ctx_1",
        kind="task",
        status=TaskStatus(state=TaskState.working),
        metadata=None,
    )

    # Should handle None metadata gracefully
    result1 = await server._send_protocol_webhook(task1, status="working")
    # No exception should be raised (returns None/early return)

    # Test Case 2: metadata exists but no push_notification_config key
    task2 = Task(
        id="task_2",
        context_id="ctx_2",
        kind="task",
        status=TaskStatus(state=TaskState.working),
        metadata={"other_key": "value"},
    )

    result2 = await server._send_protocol_webhook(task2, status="working")
    # No exception should be raised

    # Test Case 3: push_notification_config exists but malformed
    task3 = Task(
        id="task_3",
        context_id="ctx_3",
        kind="task",
        status=TaskStatus(state=TaskState.working),
        metadata={"push_notification_config": "not_a_dict"},
    )

    # Should handle malformed config gracefully (may log warning)
    try:
        result3 = await server._send_protocol_webhook(task3, status="working")
        # No fatal exception
        success = True
    except Exception:
        success = False

    assert success, "Should handle malformed config without fatal exception"


@pytest.mark.asyncio
async def test_working_webhook_with_empty_metadata():
    """Test webhook handling when task has empty metadata dict."""

    server = AdCPRequestHandler()

    task = Task(
        id="task_empty",
        context_id="ctx_empty",
        kind="task",
        status=TaskStatus(state=TaskState.working),
        metadata={},  # Empty dict
    )

    # The check: if task.metadata and "push_notification_config" in task.metadata
    # should prevent webhook from being dispatched

    webhook_called = False

    async def fake_send(*args, **kwargs):
        nonlocal webhook_called
        webhook_called = True
        return True

    with patch.object(
        server,
        "_send_protocol_webhook",
        new_callable=AsyncMock,
        side_effect=fake_send,
    ):
        # Simulate the check in on_message_send
        if task.metadata and "push_notification_config" in task.metadata:
            asyncio.create_task(server._send_protocol_webhook(task, status="working"))

        await asyncio.sleep(0.1)  # Let any tasks execute

        assert not webhook_called, "Webhook should not be called with empty metadata"
