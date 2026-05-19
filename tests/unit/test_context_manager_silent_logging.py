"""Behavioral coverage for context_manager silent-skip logging (Issue #1231).

CLAUDE.md: No Quiet Failures pattern. When notification dispatch fails, the failure
must reach structured logging (logger.exception) so log aggregation/alerting picks
it up — not stdout via console.print.

This test pins the behavior at the two fix sites in src/core/context_manager.py:

- Site 2 (line 743): asyncio.Task done-callback when service.send_notification fails
- Site 3 (line 766): outer except when an unexpected error escapes inner handlers

Acceptance criterion #4 of #1231: "Unit coverage added for each fixed site verifying
the failure mode now propagates (mock the dependency to raise; assert ... that logger
captured the right record)."

Pattern follows tests/unit/test_creative_formats_behavioral.py:827
(test_referral_error_logs_warning).
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def mock_step():
    """A workflow step with valid push_notification_config."""
    step = MagicMock()
    step.step_id = "step1"
    step.context_id = "ctx1"
    step.tool_name = "create_media_buy"
    step.request_data = {
        "push_notification_config": {
            "url": "https://example.com/webhook",
            "authentication": {"credentials": "x" * 32, "schemes": ["Bearer"]},
        },
        "protocol": "mcp",
    }
    step.response_data = {}
    step.context = MagicMock(tenant_id="t1", principal_id="p1")
    return step


@pytest.fixture
def session_with_one_mapping_one_webhook():
    """A session whose 3 scalars() calls return: mappings, context, webhooks (in order)."""
    session = MagicMock()
    mapping = MagicMock(object_type="media_buy", object_id="mb1", action="created")
    context = MagicMock(tenant_id="t1", principal_id="p1")
    webhook = MagicMock(spec=[])

    mappings_result = MagicMock()
    mappings_result.all.return_value = [mapping]
    context_result = MagicMock()
    context_result.first.return_value = context
    webhooks_result = MagicMock()
    webhooks_result.all.return_value = [webhook]

    session.scalars.side_effect = [mappings_result, context_result, webhooks_result]
    return session


class TestSendPushNotificationsLogsOnFailure:
    """Notification dispatch failures must route via logger.exception (Issue #1231).

    CLAUDE.md No Quiet Failures: stdout-Rich output never reaches log aggregation;
    structured logging does. The two fix sites are deliberately silent-skip (the
    workflow is already committed when notifications run, and asyncio Task
    done-callbacks can't propagate), so the assertion is on logger capture, not
    on exception propagation.
    """

    async def test_done_callback_logs_when_webhook_task_fails(
        self, caplog, mock_step, session_with_one_mapping_one_webhook
    ):
        """Site 2: when service.send_notification raises inside the asyncio Task,
        the done-callback closure logs via logger.exception with the webhook URL."""
        from src.core.context_manager import ContextManager

        mock_service = MagicMock()
        mock_service.send_notification = AsyncMock(side_effect=RuntimeError("connection refused"))

        cm = ContextManager.__new__(ContextManager)

        with (
            patch(
                "src.core.context_manager.get_protocol_webhook_service",
                return_value=mock_service,
            ),
            caplog.at_level(logging.ERROR, logger="src.core.context_manager"),
        ):
            cm._send_push_notifications(mock_step, "completed", session_with_one_mapping_one_webhook)
            # Yield repeatedly so the scheduled Task runs and the done-callback fires.
            for _ in range(5):
                await asyncio.sleep(0)

        error_msgs = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
        assert any(
            "Webhook failed for https://example.com/webhook" in m for m in error_msgs
        ), f"Expected logger.exception with webhook URL, got: {error_msgs}"

    def test_outer_except_logs_when_unexpected_error_escapes(self, caplog, mock_step):
        """Site 3: when an unexpected error escapes inner handlers, the outer
        except logs via logger.exception (must not re-raise — best-effort contract)."""
        from src.core.context_manager import ContextManager

        mock_session = MagicMock()
        mock_session.scalars.side_effect = RuntimeError("DB connection lost")

        cm = ContextManager.__new__(ContextManager)

        with caplog.at_level(logging.ERROR, logger="src.core.context_manager"):
            cm._send_push_notifications(mock_step, "completed", mock_session)

        error_msgs = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
        assert any(
            "Error sending push notifications" in m for m in error_msgs
        ), f"Expected outer except to log via logger.exception, got: {error_msgs}"
