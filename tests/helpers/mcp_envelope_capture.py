"""Shared real-wire MCP error-envelope capture helper."""

from __future__ import annotations

import asyncio
import json
from contextlib import ExitStack
from typing import Any
from unittest.mock import AsyncMock, patch

from fastmcp import Client

from src.core.main import mcp
from tests.factories.principal import PrincipalFactory

_AUTO_IDENTITY = object()


def call_mcp_tool_capturing_envelope(
    tool_name: str,
    params: dict[str, Any],
    identity: Any = _AUTO_IDENTITY,
    *,
    stub_lifecycle_schedulers: bool = False,
) -> tuple[bool, dict[str, Any] | None]:
    """Invoke an MCP tool and return its error flag and parsed wire envelope."""
    resolved_identity = PrincipalFactory.make_identity(protocol="mcp") if identity is _AUTO_IDENTITY else identity

    async def _call() -> tuple[bool, str | None]:
        with ExitStack() as stack:
            stack.enter_context(
                patch(
                    "src.core.mcp_auth_middleware.resolve_identity_from_context",
                    return_value=resolved_identity,
                )
            )
            if stub_lifecycle_schedulers:
                for target in (
                    "src.services.delivery_webhook_scheduler.start_delivery_webhook_scheduler",
                    "src.services.delivery_webhook_scheduler.stop_delivery_webhook_scheduler",
                    "src.services.media_buy_status_scheduler.start_media_buy_status_scheduler",
                    "src.services.media_buy_status_scheduler.stop_media_buy_status_scheduler",
                ):
                    stack.enter_context(patch(target, AsyncMock()))
            async with Client(mcp) as client:
                result = await client.call_tool(tool_name, params, raise_on_error=False)
                if not result.content:
                    return result.is_error, None
                text = next((item.text for item in result.content if hasattr(item, "text")), None)
                return result.is_error, text

    is_error, envelope_text = asyncio.run(_call())
    return is_error, json.loads(envelope_text) if envelope_text is not None else None
