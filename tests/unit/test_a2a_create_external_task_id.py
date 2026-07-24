"""B6 (#1544): the A2A create_media_buy skill forwards the outer task id to core.

on_message_send mints the outer ``task_*`` id and threads it down so
``_create_media_buy_impl`` can persist it on the workflow step. This pins the A2A-specific
hop — ``_handle_create_media_buy_skill(a2a_task_id=...)`` must forward it to core as
``external_task_id`` — so a refactor that drops it turns this test red.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from tests.factories import PrincipalFactory

_MOCK_IDENTITY = PrincipalFactory.make_identity(principal_id="principal_123", tenant_id="tenant_123", protocol="a2a")

_VALID_PARAMS = {
    "brand": {"domain": "example.com"},
    "packages": [{"package_id": "pkg_1", "products": ["prod_1"], "budget": {"total": 1000, "currency": "USD"}}],
    "start_time": "2026-01-01T00:00:00Z",
    "end_time": "2026-02-01T00:00:00Z",
}


def _external_task_id_forwarded_to_core(a2a_task_id: str | None) -> object:
    """Invoke the real create skill handler and return the ``external_task_id`` handed to core."""
    from src.a2a_server.adcp_a2a_server import AdCPRequestHandler

    handler = AdCPRequestHandler.__new__(AdCPRequestHandler)
    captured: dict[str, object] = {}

    async def _fake_core(**kwargs: object) -> MagicMock:
        captured["external_task_id"] = kwargs.get("external_task_id")
        result = MagicMock()
        result.model_dump.return_value = {}
        return result

    # Bypass request-body validation — the unit under test is the external_task_id
    # forwarding, and core (which consumes the params) is mocked.
    with (
        patch.object(handler, "_make_tool_context", return_value=MagicMock()),
        patch("src.core.schemas.CreateMediaBuyRequest.model_validate", return_value=MagicMock(po_number=None)),
        patch("src.a2a_server.adcp_a2a_server.core_create_media_buy_tool", new=AsyncMock(side_effect=_fake_core)),
    ):
        asyncio.run(
            handler._handle_create_media_buy_skill(
                parameters={**_VALID_PARAMS},
                identity=_MOCK_IDENTITY,
                a2a_task_id=a2a_task_id,
            )
        )
    return captured["external_task_id"]


class TestA2ACreateExternalTaskId:
    def test_outer_task_id_forwarded_as_external_task_id(self):
        """The A2A skill hands its outer task id to core as external_task_id."""
        assert _external_task_id_forwarded_to_core("task_outer_xyz") == "task_outer_xyz"

    def test_absent_task_id_forwards_none(self):
        """A direct handler call without an outer task id forwards None (MCP/REST parity)."""
        assert _external_task_id_forwarded_to_core(None) is None
