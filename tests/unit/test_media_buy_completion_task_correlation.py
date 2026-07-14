"""B6 (#1544): the media-buy completion/rejection webhook correlates to the id the BUYER holds.

For A2A the buyer received an outer transport ``task_*`` id, persisted on the create
step's ``request_data.external_task_id``. The webhook must carry THAT id, not the internal
``step_id`` — otherwise the buyer cannot correlate the notification to their request. MCP/REST
have no outer id and fall back to ``step_id``.
"""

from unittest.mock import MagicMock, patch

from adcp.types import GeneratedTaskStatus as AdcpTaskStatus

import src.admin.services.media_buy_completion as mod
from src.core.schemas import CreateMediaBuySuccess


def _result() -> CreateMediaBuySuccess:
    return CreateMediaBuySuccess(media_buy_id="mb_1", packages=[], context={}, confirmed_at=None, revision=2)


def _emit_and_capture_task_id(step_data: dict) -> str:
    """Emit the webhook and return the ``task_id`` handed to the SDK payload builder."""
    captured: dict[str, str] = {}

    def _fake_a2a(*, task_id, status, result, context_id):
        captured["task_id"] = task_id
        return MagicMock()

    def _fake_mcp(*, task_id, task_type, result, status):
        captured["task_id"] = task_id
        return MagicMock()

    with (
        patch.object(mod, "create_a2a_webhook_payload", side_effect=_fake_a2a),
        patch.object(mod, "create_mcp_webhook_payload", side_effect=_fake_mcp),
        patch.object(mod, "get_protocol_webhook_service"),
    ):
        mod.emit_media_buy_webhook(step_data, MagicMock(), _result(), AdcpTaskStatus.completed)
    return captured["task_id"]


def _step_data(*, protocol: str, external_task_id: str | None = None) -> dict:
    request_data: dict = {"protocol": protocol}
    if external_task_id is not None:
        request_data["external_task_id"] = external_task_id
    return {
        "step_id": "step_internal",
        "context_id": "ctx_1",
        "tool_name": "create_media_buy",
        "request_data": request_data,
    }


def test_a2a_webhook_uses_external_task_id_not_step_id():
    """A2A: the webhook carries the persisted outer task id, not the internal step_id."""
    task_id = _emit_and_capture_task_id(_step_data(protocol="a2a", external_task_id="task_buyer_abc"))
    assert task_id == "task_buyer_abc"


def test_a2a_webhook_falls_back_to_step_id_without_external_task_id():
    """A2A step recorded before B6 (no stored external_task_id) still emits with step_id."""
    task_id = _emit_and_capture_task_id(_step_data(protocol="a2a"))
    assert task_id == "step_internal"


def test_mcp_webhook_uses_step_id():
    """MCP has no outer transport id — it correlates on step_id."""
    task_id = _emit_and_capture_task_id(_step_data(protocol="mcp"))
    assert task_id == "step_internal"
