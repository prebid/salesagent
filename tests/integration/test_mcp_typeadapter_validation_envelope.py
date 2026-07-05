"""MCP TypeAdapter validation errors are emitted as AdCP wire envelopes.

These tests use the real in-memory FastMCP client and server:

    Client(mcp) -> middleware -> FastMCP TypeAdapter -> CallToolResult

The payloads fail before the tool body runs, so no database setup is required.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from fastmcp import Client

from src.core.main import mcp
from tests.factories.principal import PrincipalFactory
from tests.helpers import assert_envelope_shape

pytestmark = pytest.mark.integration


def _call_mcp_tool_capturing_envelope(tool_name: str, params: dict) -> tuple[bool, dict | None]:
    """Invoke a tool through the real in-memory MCP client and parse error content."""

    async def _call() -> tuple[bool, str | None]:
        identity = PrincipalFactory.make_identity(protocol="mcp")
        with (
            patch("src.core.mcp_auth_middleware.resolve_identity_from_context", return_value=identity),
            patch("src.services.delivery_webhook_scheduler.start_delivery_webhook_scheduler", AsyncMock()),
            patch("src.services.delivery_webhook_scheduler.stop_delivery_webhook_scheduler", AsyncMock()),
            patch("src.services.media_buy_status_scheduler.start_media_buy_status_scheduler", AsyncMock()),
            patch("src.services.media_buy_status_scheduler.stop_media_buy_status_scheduler", AsyncMock()),
        ):
            async with Client(mcp) as client:
                result = await client.call_tool(tool_name, params, raise_on_error=False)
                text = next((c.text for c in result.content if hasattr(c, "text")), None)
                return result.is_error, text

    is_error, envelope_text = asyncio.run(_call())
    return is_error, json.loads(envelope_text) if envelope_text else None


@pytest.mark.parametrize(
    ("tool_name", "params", "message_substr", "field_prefix"),
    [
        (
            "list_creatives",
            {"filters": {"concept_ids": []}},
            "filters.concept_ids",
            "filters.concept_ids",
        ),
        (
            "create_media_buy",
            {"brand": "wiretest.example", "packages": [{}]},
            "packages.0",
            "packages[0].",
        ),
    ],
)
def test_typeadapter_validation_error_emits_adcp_envelope_on_mcp_wire(tool_name, params, message_substr, field_prefix):
    is_error, envelope = _call_mcp_tool_capturing_envelope(tool_name, params)

    assert is_error, f"{tool_name}: invalid typed params must produce a tool error"
    assert envelope is not None, f"{tool_name}: no MCP wire error envelope captured"
    assert_envelope_shape(envelope, "VALIDATION_ERROR", recovery="correctable", message_substr=message_substr)
    assert envelope["errors"][0].get("suggestion"), f"{tool_name}: envelope must include a recovery suggestion"
    assert envelope["errors"][0].get("field", "").startswith(field_prefix)
