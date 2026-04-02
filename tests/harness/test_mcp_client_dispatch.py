"""Regression test: MCP dispatch via Client(mcp) exercises the full pipeline.

Verifies that Client(mcp) goes through FastMCP's middleware chain and
TypeAdapter, not just calling the wrapper function directly.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

from src.core.resolved_identity import ResolvedIdentity


def _make_identity() -> ResolvedIdentity:
    return ResolvedIdentity(
        principal_id="test_principal",
        tenant_id="test_tenant",
        tenant={"tenant_id": "test_tenant", "name": "Test"},
        protocol="mcp",
    )


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

    def test_unknown_field_rejected_without_middleware_stripping(self):
        """Without the schema-aware strip, unknown fields cause TypeAdapter error.

        This test documents the current behavior: FastMCP's TypeAdapter rejects
        unknown kwargs. After salesagent-xd73 evolves the middleware to strip
        unknowns, this test should be updated to expect success instead.
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
                    # Currently: TypeAdapter rejects unknown kwargs
                    # After salesagent-xd73: middleware strips it, call succeeds
                    assert result.is_error, "Expected error for unknown field (pre-strip middleware)"

        asyncio.run(_call())
