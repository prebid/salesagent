"""HTTP adapters for the existing MCP session fixture, without restating its frames."""

import json

import httpx2

from tests.e2e._stdlib_json_http import mcp_capabilities_post


def mcp_handshake_response(frame, *, discover_status: int = 200):
    """Delegate session and discovery frames to the repository's existing peer."""
    if frame["method"] == "tools/call":
        return None
    status, payload = mcp_capabilities_post(json.dumps(frame).encode(), {"get_products": {}})
    if frame["method"] == "server/discover":
        status = discover_status
    return httpx2.Response(status, json=payload) if payload is not None else httpx2.Response(status)


def mock_mcp_http(monkeypatch, respond):
    """Replace only the SDK's external HTTP client, retaining its real MCP client."""
    real_client = httpx2.AsyncClient

    def client_factory(**kwargs):
        return real_client(transport=httpx2.MockTransport(respond), **kwargs)

    monkeypatch.setattr("mcp.shared._httpx_utils.httpx2.AsyncClient", client_factory)
