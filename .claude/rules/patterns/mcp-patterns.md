# MCP & A2A Patterns

Reference patterns for working with MCP tools and A2A integration. Read this when adding or modifying tools.

## MCP Client Usage
```python
from fastmcp.client import Client
from fastmcp.client.transports import StreamableHttpTransport

headers = {"x-adcp-auth": "your_token"}
transport = StreamableHttpTransport(url="http://localhost:8000/mcp/", headers=headers)
client = Client(transport=transport)

async with client:
    products = await client.tools.get_products(brief="video ads")
    result = await client.tools.create_media_buy(product_ids=["prod_1"], ...)
```

## CLI Testing
```bash
# List available tools
uvx adcp http://localhost:8000/mcp/ --auth test-token list_tools

# Get a real token from Admin UI -> Advertisers -> API Token
uvx adcp http://localhost:8000/mcp/ --auth <real-token> get_products '{"brief":"video"}'
```

## Transport Boundary: Layer Separation (Critical Pattern #5)

All tools have two layers with strict responsibilities:

**`_impl` functions** (business logic — transport-agnostic):
```python
async def _create_media_buy_impl(
    req: CreateMediaBuyRequest,
    push_notification_config: dict | None = None,
    identity: ResolvedIdentity | None = None,    # NOT Context/ToolContext
) -> CreateMediaBuyResult:
    # Business logic only — no transport awareness
    ...
```

**Transport wrappers** (boundary — resolves identity, forwards all params):
```python
@mcp.tool()
async def create_media_buy(ctx: Context, ...) -> CreateMediaBuyResponse:
    identity = resolve_identity(ctx.http.headers, protocol="mcp")
    return await _create_media_buy_impl(req=req, identity=identity, ...)

async def create_media_buy_raw(...) -> CreateMediaBuyResponse:
    identity = resolve_identity(headers, protocol="a2a")
    return await _create_media_buy_impl(req=req, identity=identity, ...)
```

**`_impl` rules:** Accept `ResolvedIdentity` (not Context). Raise `AdCPError` (not ToolError). Zero imports from fastmcp/a2a/starlette/fastapi.

**Wrapper rules:** Call `resolve_identity()` first. Forward every `_impl` parameter. Translate `AdCPError` to transport-specific format.

**Enforced by 4 structural guards** — see `docs/development/structural-guards.md`.

## Access Points (via nginx at http://localhost:8000)
- Admin UI: `/admin/` or `/tenant/default`
- MCP Server: `/mcp/`
- A2A Server: `/a2a`
