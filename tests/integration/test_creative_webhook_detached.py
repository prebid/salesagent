"""Integration test: _call_webhook_for_creative_status must not crash with DetachedInstanceError.

salesagent-647: The function accesses WorkflowStep ORM attributes (tool_name,
step_id, request_data, context_id) after AdminCreativeUoW closes. With
expire_on_commit=True, this raises DetachedInstanceError.
"""

import asyncio
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from src.core.database.database_session import get_db_session
from src.core.database.models import (
    Context,
    Creative,
    ObjectWorkflowMapping,
    Principal,
    Tenant,
    WorkflowStep,
)

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


# FIXME(salesagent-dt8): No factories exist for Context, WorkflowStep,
# ObjectWorkflowMapping. Inline setup until workflow factories are created.
def _setup_webhook_test_data(session, tenant_id: str, creative_id: str) -> str:
    """Create full DB chain needed for _call_webhook_for_creative_status.

    Returns the step_id.
    """
    principal_id = f"principal_{uuid.uuid4().hex[:8]}"
    context_id = f"ctx_{uuid.uuid4().hex[:8]}"
    step_id = f"step_{uuid.uuid4().hex[:8]}"

    tenant = Tenant(
        tenant_id=tenant_id,
        name=f"Test Tenant {tenant_id}",
        subdomain=tenant_id[:20],
        is_active=True,
    )
    session.add(tenant)
    session.flush()

    principal = Principal(
        tenant_id=tenant_id,
        principal_id=principal_id,
        name="Test Principal",
        access_token=f"token_{uuid.uuid4().hex[:8]}",
        platform_mappings={"mock": {"id": "test"}},
    )
    session.add(principal)
    session.flush()

    context = Context(
        context_id=context_id,
        tenant_id=tenant_id,
        principal_id=principal_id,
        conversation_history=[],
    )
    session.add(context)
    session.flush()

    step = WorkflowStep(
        step_id=step_id,
        context_id=context_id,
        step_type="tool_call",
        tool_name="sync_creatives",
        request_data={
            "push_notification_config": {
                "url": "https://example.com/webhook",
                "authentication": {"schemes": ["bearer"], "credentials": "test-token"},
            },
            "protocol": "mcp",
        },
        status="completed",
        owner="principal",
    )
    session.add(step)
    session.flush()

    mapping = ObjectWorkflowMapping(
        object_type="creative",
        object_id=creative_id,
        step_id=step_id,
        action="sync_creatives",
    )
    session.add(mapping)
    session.flush()

    creative = Creative(
        creative_id=creative_id,
        tenant_id=tenant_id,
        principal_id=principal_id,
        name="Test Creative",
        format="display_300x250",
        status="approved",
        agent_url="https://test-agent.example.com",
        data={},
    )
    session.add(creative)
    session.commit()

    return step_id


class TestWebhookDetachedInstanceError:
    """salesagent-647: _call_webhook_for_creative_status must not crash post-UoW."""

    def test_webhook_accesses_step_attributes_after_uow_closes(self, integration_db):
        """Calling _call_webhook_for_creative_status must not raise DetachedInstanceError.

        The function loads a WorkflowStep inside AdminCreativeUoW, then accesses
        step.tool_name, step.step_id, step.request_data, step.context_id after
        the UoW closes. With expire_on_commit=True this crashes.
        """
        from src.admin.blueprints.creatives import _call_webhook_for_creative_status

        creative_id = f"creative_{uuid.uuid4().hex[:8]}"
        tenant_id = f"tenant_{uuid.uuid4().hex[:8]}"

        with get_db_session() as session:
            _setup_webhook_test_data(session, tenant_id, creative_id)

        mock_service = AsyncMock()
        mock_service.send_notification = AsyncMock(return_value=True)

        with patch(
            "src.admin.blueprints.creatives.get_protocol_webhook_service",
            return_value=mock_service,
        ):
            result = asyncio.run(
                _call_webhook_for_creative_status(
                    creative_id=creative_id,
                    tenant_id=tenant_id,
                )
            )

        assert result is True
        mock_service.send_notification.assert_called_once()
