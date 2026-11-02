# MCP and A2A patterns

These are the reference patterns for MCP tools and A2A integration. Read them before you add
or modify a tool.

## MCP client usage
```python
from fastmcp.client import Client
from fastmcp.client.transports import StreamableHttpTransport

headers = {"Authorization": "Bearer your_token"}
transport = StreamableHttpTransport(url="http://localhost:8000/mcp/", headers=headers)
client = Client(transport=transport)

async with client:
    products = await client.tools.get_products(brief="video ads")
    result = await client.tools.create_media_buy(product_ids=["prod_1"], ...)
```

## CLI testing
```bash
# List available tools
uvx adcp http://localhost:8000/mcp/ --auth test-token list_tools

# A real token is shown once, when the advertiser is created (or rotated) in Admin UI -> Advertisers
uvx adcp http://localhost:8000/mcp/ --auth <real-token> get_products '{"brief":"video"}'
```

## Transport boundary: one path to every implementation (critical pattern #5)

A transport parses a request and writes a response. Between it and the business logic sits
ONE seam, `src/core/tools/_boundary.py`. There are no per-tool wrappers.

**`_impl` functions** (transport-agnostic):
```python
async def _create_media_buy_impl(
    req: CreateMediaBuyRequest,
    identity: ResolvedIdentity,    # never Context, headers or a token
) -> CreateMediaBuyResult:
    ...
```

**Every transport** names the tool and hands over the raw payload and the request headers:
```python
response = await serve("create_media_buy", payload, headers, TransportProtocol.A2A)
```

`serve` validates the payload into the registry row's DTO, resolves the identity once (the
resolver is private to the boundary), and resolves the account the request names. It honors
the request's `idempotency_key` and stamps the buyer's `context` onto the response. Each of
those happens once, for every transport.

**`_impl` rules:** Accept `ResolvedIdentity` (protected tool: principal and tenant are not
optional, read them directly), `AccountIdentity` (protected, and the DTO requires `account`:
the account is there too), or `PublicIdentity` (public tool: branch on
`identity.principal is None`), never a Context. Raise `AdCPSalesAgentError` (not ToolError).
Import nothing from fastmcp, a2a, starlette, or fastapi. No account resolution, no
idempotency, no context echo. Declare exactly `(req: <DTO>, identity: <one of those three>)`.
The registry derives the tool's credential policy from that annotation, and refuses a row
whose annotation disagrees with its DTO.

**Transport rules:** Hand over the headers, call `serve`, catch `AdcpFailure`, serialize its
response with `to_wire`, and add only the transport's own failure marker.

**Substituting an implementation in a test** patches the registry ROW — `TOOLS` holds the
function object, so patching a module attribute renames something nothing consults. Use
`stub_impl` / `registry_impl` from `tests/helpers/capture_wrapper_req.py`.

**Enforced by 4 structural guards** — see `docs/development/structural-guards.md`.

## Access points (through nginx at `http://localhost:8000`)
- Admin UI: `/admin/` or `/tenant/default`
- MCP server: `/mcp/`
- A2A server: `/a2a`
