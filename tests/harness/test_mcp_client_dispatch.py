"""Regression test: MCP dispatch via Client(mcp) exercises the full pipeline.

Verifies that Client(mcp) goes through FastMCP's middleware chain and
TypeAdapter, not just calling the wrapper function directly.

Environment-aware tests verify that dev mode rejects unknown fields
(fail loudly) while production mode strips them (forward compatible).
"""

from __future__ import annotations

import asyncio
import os
from unittest.mock import patch

from src.core.resolved_identity import ResolvedIdentity
from tests.factories.principal import PrincipalFactory


def _make_identity() -> ResolvedIdentity:
    return PrincipalFactory.make_identity(protocol="mcp")


class TestMcpClientDispatch:
    """Verify that MCP dispatch uses Client(mcp) and exercises middleware."""

    def test_client_mcp_succeeds_through_pipeline(self):
        """Call get_adcp_capabilities via Client(mcp) — exercises full middleware chain."""
        from fastmcp import Client

        from src.core.main import mcp

        identity = _make_identity()

        async def _call():
            with patch(
                "src.core.mcp_auth_middleware.resolve_identity_from_context",
                return_value=identity,
            ):
                async with Client(mcp) as client:
                    result = await client.call_tool("get_adcp_capabilities", {})
                    assert not result.is_error, f"MCP call failed: {result.content}"
                    assert result.structured_content is not None
                    assert "adcp" in result.structured_content

        asyncio.run(_call())

    def test_dev_mode_rejects_unknown_fields(self):
        """In dev mode, unknown fields reach TypeAdapter and are rejected.

        This is the correct behavior — dev mode fails loudly so we detect
        fields the seller agent doesn't support.
        """
        from fastmcp import Client

        from src.core.main import mcp

        identity = _make_identity()

        async def _call():
            with patch(
                "src.core.mcp_auth_middleware.resolve_identity_from_context",
                return_value=identity,
            ):
                async with Client(mcp) as client:
                    result = await client.call_tool(
                        "get_adcp_capabilities",
                        {"unknown_field": "should_be_rejected"},
                        raise_on_error=False,
                    )
                    assert result.is_error, "Dev mode should reject unknown fields"

        asyncio.run(_call())

    def test_production_mode_strips_unknown_fields(self):
        """In production mode, unknown fields are stripped by middleware.

        The call succeeds because the middleware removes the unknown field
        before TypeAdapter validates.
        """
        from fastmcp import Client

        from src.core.main import mcp

        identity = _make_identity()

        async def _call():
            with (
                patch(
                    "src.core.mcp_auth_middleware.resolve_identity_from_context",
                    return_value=identity,
                ),
                patch.dict(os.environ, {"ENVIRONMENT": "production"}),
            ):
                async with Client(mcp) as client:
                    result = await client.call_tool(
                        "get_adcp_capabilities",
                        {"unknown_field": "should_be_stripped"},
                    )
                    assert not result.is_error, f"Production should strip unknown fields: {result.content}"
                    assert result.structured_content is not None

        asyncio.run(_call())
