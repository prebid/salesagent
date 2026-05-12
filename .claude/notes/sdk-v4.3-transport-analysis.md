# SDK v4.3 Transport Boundary Analysis (2026-05-01)

## Context
Research for prebid/salesagent#1183 (tool descriptions) and #1247 (storyboard compliance).
Analyzed adcp-client-python v4.3.0 (tag) vs salesagent current architecture.

## Owner Direction (2026-05-01)
- **Import, don't copy** — descriptions, annotations, error codes should come FROM the SDK
- **Lock-in is acceptable** — it's the only SDK, pin the version
- **Contribute upstream** — PR to make response builders return Pydantic models (grounded in the same `adcp.types.generated_poc` response types the SDK already defines)
- **comply_test_controller NEVER in production** — test/sandbox only
- **RootModel blocker is resolved** — was fixed earlier, worked in 3.12
- **`brand: Any` is our bug** — BrandReference existed in SDK, we failed to use it

## Current State (salesagent on adcp 3.12.0)
- 16 MCP tools registered via `@mcp.tool()`, 13 A2A raw wrappers
- **0/16 tools** have Field(description=...) on params
- **0/16 tools** have ToolAnnotations (v3.12 SDK has none; v4.3 adds them)
- **0/16 tool docstrings** are agent-facing
- `create_media_buy` leaks ~12 deprecated/internal params
- Error codes use wrong casing (snake_case, should be UPPER_SNAKE_CASE)

## SDK v4.3 Capabilities

### Types Layer (adcp.types.generated_poc/)
- ~90-95% Field(description=...) coverage, auto-generated from AdCP JSON Schema spec
- Our schemas inherit via `class Product(LibraryProduct)` — descriptions propagate
- Response types exist: `CreateMediaBuyResponse`, `GetProductsResponse`, etc.
- Request types exist: `CreateMediaBuyRequest`, `GetProductsRequest`, etc.

### Utilities (import directly)
- `adcp.server.helpers.adcp_error()` — structured error with recovery classification
- `adcp.server.helpers.STANDARD_ERROR_CODES` — 35 UPPERCASE codes
- `adcp.server.helpers.MEDIA_BUY_STATE_MACHINE` — status → valid_actions
- `adcp.server.helpers.valid_actions_for_status()` — pure function
- `adcp.server.mcp_tools.ADCP_TOOL_DEFINITIONS` — tool descriptions + ToolAnnotations
- `adcp.server.idempotency.canonical_json_sha256()` — RFC 8785 hashing

### Response Builders (need upstream PR)
- `media_buy_response()`, `products_response()`, etc. return raw **dicts**
- SDK already has Pydantic response types in `generated_poc/` but builders don't use them
- **Upstream PR**: builders should construct and return those Pydantic response types

### Server Framework (keep our architecture)
- ADCPHandler/serve()/ToolContext — alternative architecture, not needed
- Our _impl + transport wrapper + repository pattern is more capable
- SDK framework has no real DB, no multi-adapter, no admin UI, no multi-tenant

## How SDK Generates Tool Schemas
1. Maps tool names → Pydantic Request types
2. `model_json_schema()` on each → JSON Schema with Field descriptions
3. `_inline_refs()` flattens all `$ref` (MCP clients don't resolve them)
4. Registers via `tool.parameters = input_schema` on FastMCP Tool objects

## Implementation Plan

### Phase 1: Upgrade adcp 3.12 → 4.3
- RootModel blocker resolved
- Gets: ToolAnnotations, UPPERCASE error codes, state machine, response types
- Verify schema extension pattern still works

### Phase 2: Fix our MCP wrapper types (our debt)
- `brand: Any` → `BrandReference` (coerce string shorthand per #1247 item 2)
- `property_list: dict` → `PropertyListReference`
- Other `Any`/`dict` params → proper SDK types
- Scalar params: add `Annotated[..., Field(description=...)]`

### Phase 3: Import tool metadata from SDK
- Tool descriptions from `ADCP_TOOL_DEFINITIONS`
- ToolAnnotations from `ADCP_TOOL_DEFINITIONS`
- Error codes from `STANDARD_ERROR_CODES`
- State machine from `MEDIA_BUY_STATE_MACHINE`

### Phase 4: Storyboard compliance (#1247)
- P0: Accept unknown params, coerce string brand, uppercase error codes
- P1: sync_governance, cancellation, error on unknown IDs, status lifecycle
- P2: refine mode, property_list/collection_list, zero-inventory

### Phase 5: Upstream PR to adcp-client-python
- Response builders return Pydantic models from `adcp.types.generated_poc/`
- Same types the SDK defines, grounded in the AdCP schema
- Removes dict-vs-model tension for all implementors

## Compliance Notes
- Storyboards test through MCP protocol boundary
- `comply_test_controller` — sandbox/test only, NEVER production
- `TestControllerStore` interface is clean; can subclass for our DB
- `register_test_controller()` uses private FastMCP APIs — may need our own wiring

## Key Files
- SDK tools: `adcp/server/mcp_tools.py` (ADCP_TOOL_DEFINITIONS, schema generation)
- SDK helpers: `adcp/server/helpers.py` (error codes, state machine, adcp_error)
- SDK responses: `adcp/server/responses.py` (dict builders — target for upstream PR)
- SDK response types: `adcp/types/generated_poc/media_buy/create_media_buy_response.py` etc.
- Our wrappers: `src/core/tools/` (media_buy_create.py, products.py, etc.)
- Our schemas: `src/core/schemas/` (inherit from SDK types)
