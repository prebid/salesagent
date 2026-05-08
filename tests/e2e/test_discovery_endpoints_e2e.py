"""
E2E tests for AdCP discovery endpoints: list_creative_formats and list_authorized_properties.

These tests exercise the full MCP transport path (HTTP -> FastMCP -> tool wrapper -> _impl -> DB/registry)
with no mocking. They validate that structured_content contains correct AdCP response shapes
when called against a live Docker stack.

Both endpoints are "discovery" tools — they return catalog/inventory metadata without side effects.
"""

import pytest
from fastmcp.client import Client
from fastmcp.client.transports import StreamableHttpTransport

from tests.e2e.adcp_request_builder import parse_tool_result


class TestListCreativeFormatsE2E:
    """E2E tests for the list_creative_formats discovery endpoint."""

    @pytest.mark.asyncio
    async def test_list_creative_formats_returns_formats(self, docker_services_e2e, live_server, test_auth_token):
        """
        Calling list_creative_formats with no filters returns a non-empty formats list.

        The CI environment has a default creative agent registered, so at least
        one format should be available.
        """
        headers = {
            "x-adcp-auth": test_auth_token,
            "x-adcp-tenant": "ci-test",
        }
        transport = StreamableHttpTransport(url=f"{live_server['mcp']}/mcp/", headers=headers)

        async with Client(transport=transport) as client:
            result = await client.call_tool("list_creative_formats", {})
            data = parse_tool_result(result)

            assert "formats" in data, f"Response must contain 'formats' key, got: {sorted(data.keys())}"
            assert isinstance(data["formats"], list), "formats must be a list"
            assert len(data["formats"]) > 0, "Default creative agent should provide at least one format"

    @pytest.mark.asyncio
    async def test_list_creative_formats_response_structure(self, docker_services_e2e, live_server, test_auth_token):
        """
        Each format in the response has the required AdCP fields: format_id, name, type.

        Validates the serialization path from Pydantic model through ToolResult.structured_content
        to the final dict shape seen by MCP clients.
        """
        headers = {
            "x-adcp-auth": test_auth_token,
            "x-adcp-tenant": "ci-test",
        }
        transport = StreamableHttpTransport(url=f"{live_server['mcp']}/mcp/", headers=headers)

        async with Client(transport=transport) as client:
            result = await client.call_tool("list_creative_formats", {})
            data = parse_tool_result(result)

            formats = data["formats"]
            assert len(formats) > 0, "Need at least one format to validate structure"

            for fmt in formats:
                assert "format_id" in fmt, f"Format missing 'format_id': {sorted(fmt.keys())}"
                assert "name" in fmt, f"Format missing 'name': {sorted(fmt.keys())}"

                # format_id should be a dict with 'id' and 'agent_url' per AdCP spec
                fid = fmt["format_id"]
                assert isinstance(fid, dict), f"format_id should be a dict, got {type(fid).__name__}"
                assert "id" in fid, f"format_id missing 'id': {sorted(fid.keys())}"
                assert "agent_url" in fid, f"format_id missing 'agent_url': {sorted(fid.keys())}"

    @pytest.mark.asyncio
    async def test_list_creative_formats_context_echo(self, docker_services_e2e, live_server, test_auth_token):
        """
        Context passed in the request is echoed back in the response (AdCP spec requirement).
        """
        headers = {
            "x-adcp-auth": test_auth_token,
            "x-adcp-tenant": "ci-test",
        }
        transport = StreamableHttpTransport(url=f"{live_server['mcp']}/mcp/", headers=headers)

        async with Client(transport=transport) as client:
            test_context = {"e2e": "list_creative_formats", "session": "test-123"}
            result = await client.call_tool(
                "list_creative_formats",
                {"context": test_context},
            )
            data = parse_tool_result(result)

            assert "formats" in data, "Response must contain formats"
            assert (
                data.get("context") == test_context
            ), f"Context should be echoed back. Expected {test_context}, got {data.get('context')}"


# list_authorized_properties was removed from the AdCP spec in 4.4.x; the
# replacement surface is list_property_lists / get_property_list. Tests for
# the old tool are deleted rather than skipped — see #25.
