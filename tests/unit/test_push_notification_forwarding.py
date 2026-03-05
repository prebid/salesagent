"""
Regression test for salesagent-c1xl: MCP create_media_buy wrapper must forward
push_notification_config to _create_media_buy_impl.

Both MCP and A2A wrappers must forward push_notification_config identically
(transport parity invariant). The _impl function accepts dict|None, so:
- MCP wrapper (receives PushNotificationConfig model from FastMCP) must serialize to dict
- A2A wrapper (receives dict from JSON) passes through directly
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.schemas import CreateMediaBuyResult
from tests.helpers.adcp_factories import create_test_media_buy_request_dict


def _make_push_notification_config_dict() -> dict[str, Any]:
    """Create push_notification_config as a raw dict (A2A transport format)."""
    return {
        "url": "https://example.com/webhook",
        "authentication": {"credentials": "a" * 32, "schemes": ["Bearer"]},
    }


def _mock_create_result() -> CreateMediaBuyResult:
    """Create a minimal mock result for _create_media_buy_impl."""
    result = MagicMock(spec=CreateMediaBuyResult)
    result.__str__ = lambda self: "mock_result"
    return result


class TestMCPForwardsPushNotificationConfig:
    """MCP wrapper must forward push_notification_config to _impl."""

    @pytest.mark.asyncio
    async def test_mcp_wrapper_forwards_push_notification_config(self):
        """When push_notification_config is provided, MCP wrapper forwards it to _impl as a dict."""
        from adcp import PushNotificationConfig

        from src.core.tools.media_buy_create import create_media_buy

        pnc = PushNotificationConfig(
            url="https://example.com/webhook",
            authentication={"credentials": "a" * 32, "schemes": ["Bearer"]},
        )
        mock_result = _mock_create_result()

        # Build valid request kwargs from factory
        req_dict = create_test_media_buy_request_dict()

        # Mock Context with get_state returning identity and context_id
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

        with patch(
            "src.core.tools.media_buy_create._create_media_buy_impl",
            new_callable=AsyncMock,
            return_value=mock_result,
        ) as mock_impl:
            # The wrapper may fail at ToolResult serialization with a mock;
            # we only care that _impl received push_notification_config.
            try:
                await create_media_buy(
                    buyer_ref=req_dict["buyer_ref"],
                    brand=req_dict["brand"],
                    packages=req_dict["packages"],
                    start_time=req_dict["start_time"],
                    end_time=req_dict["end_time"],
                    push_notification_config=pnc,
                    ctx=mock_ctx,
                )
            except (TypeError, Exception):
                pass  # ToolResult serialization error with mock is expected

            # _impl must have been called with push_notification_config
            mock_impl.assert_called_once()
            call_kwargs = mock_impl.call_args.kwargs
            assert "push_notification_config" in call_kwargs, (
                "MCP wrapper does not forward push_notification_config to _impl. "
                "This is a transport parity violation (salesagent-c1xl)."
            )
            forwarded = call_kwargs["push_notification_config"]
            assert forwarded is not None, "push_notification_config was forwarded as None despite being provided"
            # _impl expects dict, not PushNotificationConfig model
            assert isinstance(forwarded, dict), (
                f"push_notification_config must be forwarded as dict, got {type(forwarded).__name__}"
            )


class TestA2AForwardsPushNotificationConfig:
    """A2A wrapper must forward push_notification_config to _impl (parity check)."""

    @pytest.mark.asyncio
    async def test_a2a_wrapper_forwards_push_notification_config(self):
        """When push_notification_config is provided, A2A wrapper forwards it to _impl."""
        from src.core.tools.media_buy_create import create_media_buy_raw

        pnc_dict = _make_push_notification_config_dict()
        mock_result = _mock_create_result()
        mock_identity = MagicMock()

        req_dict = create_test_media_buy_request_dict()

        with patch(
            "src.core.tools.media_buy_create._create_media_buy_impl",
            new_callable=AsyncMock,
            return_value=mock_result,
        ) as mock_impl:
            await create_media_buy_raw(
                buyer_ref=req_dict["buyer_ref"],
                brand=req_dict["brand"],
                packages=req_dict["packages"],
                start_time=req_dict["start_time"],
                end_time=req_dict["end_time"],
                push_notification_config=pnc_dict,
                identity=mock_identity,
            )

            mock_impl.assert_called_once()
            call_kwargs = mock_impl.call_args.kwargs
            assert "push_notification_config" in call_kwargs, (
                "A2A wrapper does not forward push_notification_config to _impl"
            )
            forwarded = call_kwargs["push_notification_config"]
            assert forwarded is not None
            assert isinstance(forwarded, dict)
            assert forwarded["url"] == "https://example.com/webhook"
