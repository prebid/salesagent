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

## Shared Implementation Pattern (Critical Pattern #5)
All tools use shared `_tool_name_impl()` called by both MCP and A2A paths:

```python
# main.py
def _create_media_buy_impl(...) -> CreateMediaBuyResponse:
    return response

@mcp.tool()
def create_media_buy(...) -> CreateMediaBuyResponse:
    return _create_media_buy_impl(...)

# tools.py
def create_media_buy_raw(...) -> CreateMediaBuyResponse:
    from src.core.main import _create_media_buy_impl
    return _create_media_buy_impl(...)
```

## Access Points (via nginx at http://localhost:8000)
- Admin UI: `/admin/` or `/tenant/default`
- MCP Server: `/mcp/`
- A2A Server: `/a2a`
