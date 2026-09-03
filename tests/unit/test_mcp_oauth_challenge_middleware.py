import json

import pytest

from src.core.mcp_oauth_challenge_middleware import MCPOAuthChallengeMiddleware, _buffer_request_body


@pytest.mark.asyncio
async def test_mcp_oauth_challenge_returns_401_for_unauthenticated_protected_tool(monkeypatch):
    monkeypatch.setattr(
        "src.core.mcp_oauth_challenge_middleware.get_mcp_oauth_issuer",
        lambda: "https://agent.example.com",
    )
    sent_messages = []
    middleware = MCPOAuthChallengeMiddleware(_unexpected_app)

    async def send(message):
        sent_messages.append(message)

    await middleware(
        {"type": "http", "method": "POST", "headers": []},
        _receive_json_rpc_tool_call("create_media_buy"),
        send,
    )

    response_start = sent_messages[0]
    headers = {name.decode("latin-1"): value.decode("latin-1") for name, value in response_start["headers"]}
    assert response_start["status"] == 401
    assert headers["www-authenticate"] == (
        'Bearer resource_metadata="https://agent.example.com/.well-known/oauth-protected-resource"'
    )
    body = json.loads(sent_messages[1]["body"].decode("utf-8"))
    assert body["errors"][0]["code"] == "AUTH_REQUIRED"


@pytest.mark.asyncio
async def test_mcp_oauth_challenge_rejects_invalid_bearer_without_reading_body(monkeypatch):
    monkeypatch.setattr(
        "src.core.mcp_oauth_challenge_middleware.get_mcp_oauth_issuer",
        lambda: "https://agent.example.com",
    )
    monkeypatch.setattr("src.core.mcp_oauth_challenge_middleware.get_principal_from_token", lambda token: (None, None))
    sent_messages = []
    middleware = MCPOAuthChallengeMiddleware(_unexpected_app)

    async def receive():
        raise AssertionError("Invalid bearer tokens should be rejected before reading the MCP body")

    async def send(message):
        sent_messages.append(message)

    await middleware(
        {"type": "http", "method": "POST", "headers": [(b"authorization", b"Bearer forged.token.value")]},
        receive,
        send,
    )

    response_start = sent_messages[0]
    headers = {name.decode("latin-1"): value.decode("latin-1") for name, value in response_start["headers"]}
    assert response_start["status"] == 401
    assert 'error="invalid_token"' in headers["www-authenticate"]


@pytest.mark.asyncio
async def test_mcp_oauth_challenge_allows_discovery_tool_without_auth():
    called = False

    async def app(scope, receive, send):
        nonlocal called
        called = True

    middleware = MCPOAuthChallengeMiddleware(app)

    await middleware(
        {"type": "http", "method": "POST", "headers": []},
        _receive_json_rpc_tool_call("get_products"),
        _ignore_send,
    )

    assert called is True


@pytest.mark.asyncio
async def test_mcp_oauth_challenge_allows_valid_bearer_auth_without_reading_body(monkeypatch):
    monkeypatch.setattr(
        "src.core.mcp_oauth_challenge_middleware.get_principal_from_token",
        lambda token: ("principal_123", {"tenant_id": "tenant_123"}),
    )
    called = False

    async def app(scope, receive, send):
        nonlocal called
        called = True

    middleware = MCPOAuthChallengeMiddleware(app)

    async def receive():
        raise AssertionError("Valid bearer tokens should pass through without challenge body buffering")

    await middleware(
        {"type": "http", "method": "POST", "headers": [(b"authorization", b"Bearer valid.jwt.token")]},
        receive,
        _ignore_send,
    )

    assert called is True


@pytest.mark.asyncio
async def test_buffered_request_replay_delegates_after_body():
    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "get_products"}}
    messages = [
        {"type": "http.request", "body": json.dumps(payload).encode("utf-8"), "more_body": False},
        {"type": "http.disconnect"},
    ]

    async def receive():
        return messages.pop(0)

    body, replay_receive = await _buffer_request_body(receive)

    replayed_body = await replay_receive()
    terminal_message = await replay_receive()

    assert json.loads(body.decode("utf-8"))["params"]["name"] == "get_products"
    assert replayed_body["type"] == "http.request"
    assert replayed_body["more_body"] is False
    assert terminal_message == {"type": "http.disconnect"}


def _receive_json_rpc_tool_call(tool_name: str):
    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool_name, "arguments": {}}}
    messages = [{"type": "http.request", "body": json.dumps(payload).encode("utf-8"), "more_body": False}]

    async def receive():
        return messages.pop(0)

    return receive


async def _unexpected_app(scope, receive, send):
    raise AssertionError("Protected unauthenticated tool calls should be challenged before reaching FastMCP")


async def _ignore_send(message):
    return None
