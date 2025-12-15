"""Integration tests for A2A protocol-level webhooks.

Tests verify that protocol-level push notifications (operation status updates)
fire for ALL status transitions per AdCP PR #268 requirements.
"""

import pytest
from unittest.mock import AsyncMock, patch

from a2a.types import (
    Message,
    MessageSendConfiguration,
    MessageSendParams,
    Part,
    PushNotificationAuthenticationInfo,
    PushNotificationConfig,
    TaskIdParams,
    TaskState,
)
from a2a.types import DataPart

from src.a2a_server.adcp_a2a_server import AdCPRequestHandler
from src.services.protocol_webhook_service import ProtocolWebhookService


@pytest.mark.asyncio
async def test_working_webhook_sent_on_task_creation():
    """Verify 'working' webhook sent when A2A task is created."""

    webhook_calls = []

    async def fake_send_notification(*args, **kwargs):
        webhook_calls.append({
            "status": kwargs.get("status"),
            "task_id": kwargs.get("task_id"),
            "task_type": kwargs.get("task_type"),
            "result": kwargs.get("result"),
            "error": kwargs.get("error"),
        })
        return True

    server = AdCPRequestHandler()

    # Mock the authentication to avoid needing real credentials
    with patch("src.a2a_server.adcp_a2a_server.get_principal_from_token") as mock_auth:
        mock_auth.return_value = ("test_tenant", "test_principal")

        with patch.object(
            ProtocolWebhookService,
            "send_notification",
            new_callable=AsyncMock,
            side_effect=fake_send_notification,
        ):
            params = MessageSendParams(
                message=Message(
                    parts=[
                        Part(
                            root=DataPart(
                                data={"skill": "get_products", "input": {"brief": "test products"}}
                            )
                        )
                    ]
                ),
                configuration=MessageSendConfiguration(
                    pushNotificationConfig=PushNotificationConfig(
                        url="https://buyer.example.com/webhook",
                        authentication=PushNotificationAuthenticationInfo(
                            schemes=["HMAC-SHA256"], credentials="test_secret_32_chars_minimum!!"
                        ),
                    )
                ),
            )

            # Set auth token in context
            server._context_auth_token.set("test_token")

            task = await server.on_message_send(params)

            # Verify webhooks sent in correct order
            # Note: Because working webhook uses asyncio.create_task(), it may complete
            # after the completed webhook. We check that both were called.
            assert len(webhook_calls) >= 2, f"Expected at least 2 webhooks, got {len(webhook_calls)}"

            # Extract statuses
            statuses = [call["status"] for call in webhook_calls]
            assert "working" in statuses, "Expected 'working' webhook to be sent"
            assert "completed" in statuses, "Expected 'completed' webhook to be sent"

            # Verify task reached completed state
            assert task.status.state == TaskState.completed


@pytest.mark.asyncio
async def test_working_webhook_not_sent_without_config():
    """Verify NO working webhook sent when pushNotificationConfig is absent."""

    webhook_calls = []

    async def fake_send_notification(*args, **kwargs):
        webhook_calls.append(kwargs.get("status"))
        return True

    server = AdCPRequestHandler()

    with patch("src.a2a_server.adcp_a2a_server.get_principal_from_token") as mock_auth:
        mock_auth.return_value = ("test_tenant", "test_principal")

        with patch.object(
            ProtocolWebhookService,
            "send_notification",
            new_callable=AsyncMock,
            side_effect=fake_send_notification,
        ):
            params = MessageSendParams(
                message=Message(
                    parts=[
                        Part(
                            root=DataPart(
                                data={"skill": "get_products", "input": {"brief": "test products"}}
                            )
                        )
                    ]
                ),
                # NO pushNotificationConfig provided
            )

            server._context_auth_token.set("test_token")

            task = await server.on_message_send(params)

            # Verify NO webhooks sent
            assert len(webhook_calls) == 0, f"Expected no webhooks, got {len(webhook_calls)}"

            # Verify task still processes successfully
            assert task.status.state == TaskState.completed


@pytest.mark.asyncio
async def test_working_webhook_failure_doesnt_block_task():
    """Verify that webhook failures don't prevent task processing."""

    server = AdCPRequestHandler()

    async def failing_webhook(*args, **kwargs):
        raise Exception("Webhook endpoint unreachable")

    with patch("src.a2a_server.adcp_a2a_server.get_principal_from_token") as mock_auth:
        mock_auth.return_value = ("test_tenant", "test_principal")

        with patch.object(
            ProtocolWebhookService,
            "send_notification",
            new_callable=AsyncMock,
            side_effect=failing_webhook,
        ):
            params = MessageSendParams(
                message=Message(
                    parts=[
                        Part(
                            root=DataPart(
                                data={"skill": "get_products", "input": {"brief": "test products"}}
                            )
                        )
                    ]
                ),
                configuration=MessageSendConfiguration(
                    pushNotificationConfig=PushNotificationConfig(
                        url="https://buyer.example.com/webhook",
                        authentication=PushNotificationAuthenticationInfo(
                            schemes=["None"], credentials=None
                        ),
                    )
                ),
            )

            server._context_auth_token.set("test_token")

            # Task should complete successfully despite webhook failures
            task = await server.on_message_send(params)

            # Verify task reached completed state
            assert task.status.state == TaskState.completed


@pytest.mark.asyncio
async def test_webhook_sequence_working_to_completed():
    """Verify webhook sequence and payload structure for successful operation."""

    webhook_calls = []

    async def fake_send_notification(*args, **kwargs):
        webhook_calls.append({
            "status": kwargs.get("status"),
            "task_id": kwargs.get("task_id"),
            "task_type": kwargs.get("task_type"),
            "result": kwargs.get("result"),
            "error": kwargs.get("error"),
        })
        return True

    server = AdCPRequestHandler()

    with patch("src.a2a_server.adcp_a2a_server.get_principal_from_token") as mock_auth:
        mock_auth.return_value = ("test_tenant", "test_principal")

        with patch.object(
            ProtocolWebhookService,
            "send_notification",
            new_callable=AsyncMock,
            side_effect=fake_send_notification,
        ):
            params = MessageSendParams(
                message=Message(
                    parts=[
                        Part(
                            root=DataPart(
                                data={"skill": "get_products", "input": {"brief": "test products"}}
                            )
                        )
                    ]
                ),
                configuration=MessageSendConfiguration(
                    pushNotificationConfig=PushNotificationConfig(
                        url="https://buyer.example.com/webhook",
                        authentication=PushNotificationAuthenticationInfo(
                            schemes=["HMAC-SHA256"], credentials="test_secret_32_chars_minimum!!"
                        ),
                    )
                ),
            )

            server._context_auth_token.set("test_token")

            task = await server.on_message_send(params)

            # Verify at least 2 webhooks sent
            assert len(webhook_calls) >= 2

            # Find working and completed webhooks
            working_webhook = next((w for w in webhook_calls if w["status"] == "working"), None)
            completed_webhook = next((w for w in webhook_calls if w["status"] == "completed"), None)

            assert working_webhook is not None, "Expected 'working' webhook"
            assert completed_webhook is not None, "Expected 'completed' webhook"

            # Verify working webhook structure
            assert working_webhook["task_id"] == task.id
            assert working_webhook["result"] is None  # No result yet
            assert working_webhook["error"] is None

            # Verify completed webhook structure
            assert completed_webhook["task_id"] == task.id
            assert completed_webhook["result"] is not None  # Should have result
            assert completed_webhook["error"] is None


@pytest.mark.asyncio
async def test_webhook_sequence_working_to_failed():
    """Verify webhook sequence for failed operation."""

    webhook_calls = []

    async def fake_send_notification(*args, **kwargs):
        webhook_calls.append({
            "status": kwargs.get("status"),
            "error": kwargs.get("error"),
        })
        return True

    server = AdCPRequestHandler()

    with patch("src.a2a_server.adcp_a2a_server.get_principal_from_token") as mock_auth:
        # Simulate auth failure to trigger failed status
        mock_auth.side_effect = Exception("Authentication failed")

        with patch.object(
            ProtocolWebhookService,
            "send_notification",
            new_callable=AsyncMock,
            side_effect=fake_send_notification,
        ):
            params = MessageSendParams(
                message=Message(
                    parts=[
                        Part(
                            root=DataPart(
                                data={"skill": "create_media_buy", "input": {"promoted_offering": "Test"}}
                            )
                        )
                    ]
                ),
                configuration=MessageSendConfiguration(
                    pushNotificationConfig=PushNotificationConfig(
                        url="https://buyer.example.com/webhook",
                        authentication=PushNotificationAuthenticationInfo(
                            schemes=["HMAC-SHA256"], credentials="test_secret_32_chars_minimum!!"
                        ),
                    )
                ),
            )

            server._context_auth_token.set("invalid_token")

            # Task should fail but webhooks should still be sent
            try:
                task = await server.on_message_send(params)
                # Check if task failed
                assert task.status.state == TaskState.failed
            except Exception:
                pass  # Exception expected

            # Verify webhooks called (working + failed)
            assert len(webhook_calls) >= 2

            # Extract statuses
            statuses = [call["status"] for call in webhook_calls]
            assert "working" in statuses, "Expected 'working' webhook"
            assert "failed" in statuses, "Expected 'failed' webhook"

            # Verify failed webhook has error message
            failed_webhook = next((w for w in webhook_calls if w["status"] == "failed"), None)
            assert failed_webhook is not None
            assert failed_webhook["error"] is not None


@pytest.mark.asyncio
async def test_canceled_webhook_sent():
    """Verify 'canceled' webhook sent when task is canceled."""

    webhook_calls = []

    async def fake_send_notification(*args, **kwargs):
        webhook_calls.append(kwargs.get("status"))
        return True

    server = AdCPRequestHandler()

    with patch("src.a2a_server.adcp_a2a_server.get_principal_from_token") as mock_auth:
        mock_auth.return_value = ("test_tenant", "test_principal")

        with patch.object(
            ProtocolWebhookService,
            "send_notification",
            new_callable=AsyncMock,
            side_effect=fake_send_notification,
        ):
            # First create a task
            params = MessageSendParams(
                message=Message(
                    parts=[
                        Part(
                            root=DataPart(
                                data={"skill": "get_products", "input": {"brief": "test products"}}
                            )
                        )
                    ]
                ),
                configuration=MessageSendConfiguration(
                    pushNotificationConfig=PushNotificationConfig(
                        url="https://buyer.example.com/webhook",
                        authentication=PushNotificationAuthenticationInfo(
                            schemes=["HMAC-SHA256"], credentials="test_secret_32_chars_minimum!!"
                        ),
                    )
                ),
            )

            server._context_auth_token.set("test_token")

            task = await server.on_message_send(params)
            initial_webhook_count = len(webhook_calls)

            # Now cancel the task
            cancel_params = TaskIdParams(id=task.id)
            canceled_task = await server.on_cancel_task(cancel_params)

            # Verify canceled webhook was sent
            assert len(webhook_calls) > initial_webhook_count
            assert "canceled" in webhook_calls, "Expected 'canceled' webhook"
            assert canceled_task.status.state == TaskState.canceled
