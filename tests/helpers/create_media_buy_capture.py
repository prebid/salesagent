"""Shared capture helpers for create_media_buy transport boundary tests.

Both MCP and A2A wrappers serialize PushNotificationConfig before forwarding
to _create_media_buy_impl. These helpers build the mock context, patch _impl
with a side_effect that records its kwargs, invoke the wrapper, and return the
forwarded push_notification_config dict — so individual tests only assert on
the returned value without duplicating scaffolding.

Used by:
  - tests/unit/test_create_media_buy_behavioral.py  (serialization obligations)
  - tests/unit/test_push_notification_forwarding.py  (forwarding parity)
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from tests.helpers.adcp_factories import create_test_media_buy_request_dict


def _make_mock_ctx() -> AsyncMock:
    """Build a minimal FastMCP Context mock with identity and context_id state."""
    mock_ctx = AsyncMock()
    mock_ctx.http = MagicMock()
    mock_ctx.http.headers = {}

    async def _get_state(key: str) -> Any:
        if key == "identity":
            return MagicMock()
        if key == "context_id":
            return "test-ctx-id"
        return None

    mock_ctx.get_state = _get_state
    return mock_ctx


async def capture_mcp_forwarded_pnc(pnc: Any) -> dict | None:
    """Invoke the MCP create_media_buy wrapper with *pnc* and return the
    push_notification_config dict that was forwarded to _create_media_buy_impl.

    The wrapper may raise after calling _impl (ToolResult serialization with a
    mock result); that exception is swallowed — only the captured kwarg matters.

    Args:
        pnc: A PushNotificationConfig model instance (or dict) to pass as
             push_notification_config to the MCP wrapper.

    Returns:
        The push_notification_config value received by _impl, or None if _impl
        was not called.
    """
    from src.core.schemas import CreateMediaBuyResult
    from src.core.tools.media_buy_create import create_media_buy

    req_dict = create_test_media_buy_request_dict()
    mock_result = MagicMock(spec=CreateMediaBuyResult)
    mock_result.__str__ = lambda self: "mock_result"
    mock_ctx = _make_mock_ctx()

    captured: dict[str, Any] = {}

    async def _capture(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return mock_result

    with patch(
        "src.core.tools.media_buy_create._create_media_buy_impl",
        side_effect=_capture,
    ):
        try:
            await create_media_buy(
                brand=req_dict["brand"],
                packages=req_dict["packages"],
                start_time=req_dict["start_time"],
                end_time=req_dict["end_time"],
                idempotency_key=req_dict["idempotency_key"],
                push_notification_config=pnc,
                ctx=mock_ctx,
            )
        except Exception:
            pass  # ToolResult serialization with mock may raise; only _impl args matter

    return captured.get("push_notification_config")


async def capture_a2a_forwarded_pnc(pnc: Any) -> dict | None:
    """Invoke the A2A create_media_buy_raw wrapper with *pnc* and return the
    push_notification_config dict that was forwarded to _create_media_buy_impl.

    Args:
        pnc: A PushNotificationConfig model instance or plain dict to pass as
             push_notification_config to the A2A wrapper.

    Returns:
        The push_notification_config value received by _impl, or None if _impl
        was not called.
    """
    from src.core.schemas import CreateMediaBuyResult
    from src.core.tools.media_buy_create import create_media_buy_raw

    req_dict = create_test_media_buy_request_dict()
    mock_result = MagicMock(spec=CreateMediaBuyResult)
    mock_result.__str__ = lambda self: "mock_result"
    mock_identity = MagicMock()

    captured: dict[str, Any] = {}

    async def _capture(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return mock_result

    with patch(
        "src.core.tools.media_buy_create._create_media_buy_impl",
        side_effect=_capture,
    ):
        await create_media_buy_raw(
            brand=req_dict["brand"],
            packages=req_dict["packages"],
            start_time=req_dict["start_time"],
            end_time=req_dict["end_time"],
            idempotency_key=req_dict["idempotency_key"],
            push_notification_config=pnc,
            identity=mock_identity,
        )

    return captured.get("push_notification_config")
