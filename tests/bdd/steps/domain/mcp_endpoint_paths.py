"""Steps for the MCP endpoint paths feature. Transport-blind: the env owns how a path is reached."""

from __future__ import annotations

from pytest_bdd import parsers, then, when


@when(parsers.parse('a client posts an MCP initialize to "{path}"'))
def when_client_posts_initialize(ctx: dict, path: str) -> None:
    """The whole HTTP answer, with redirects not followed: the path is the subject."""
    ctx["mcp_path_response"] = ctx["env"].post_mcp_initialize(path)


@then("the answer is an MCP initialize result with no redirect")
def then_mcp_initialize_result(ctx: dict) -> None:
    """A 200 carrying the JSON-RPC result, which only the MCP app can produce."""
    response = ctx["mcp_path_response"]
    assert response.status_code == 200, f"{response.status_code}: {response.text[:200]}"
    assert response.json()["result"]["serverInfo"]["name"], f"not an initialize result: {response.text[:200]}"
