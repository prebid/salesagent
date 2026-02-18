# Architecture Proposal: FastAPI as Unified Application Framework

## Status: PROPOSAL (not approved)

## Motivation

The project serves three transports — MCP (AI agent tools), A2A (agent-to-agent JSON-RPC), and Admin UI (Flask). The business logic is shared via `_impl()` functions, but everything between "request arrives" and "call `_impl()`" is reimplemented per transport:

- **MCP**: FastMCP handles routing, validation, serialization, errors, dependency injection. Handlers are clean.
- **A2A**: No framework. 500+ lines of hand-rolled routing, auth, validation, serialization, error handling in `adcp_a2a_server.py`. Handlers mix framework concerns with business logic.
- **Admin UI**: Flask handles its own routes. Separate process.

This causes real problems:

1. **Handlers do framework work.** Every A2A handler does its own auth extraction, parameter validation, error formatting, and response serialization. Adding a new skill means copying 30 lines of boilerplate.

2. **Errors are dicts, not exceptions.** Six A2A handlers return `{"success": False, "message": "..."}` dicts instead of throwing typed exceptions. The dispatch can't distinguish error responses from success responses without `isinstance(result, dict)`.

3. **Three serialization paths for the same data.** `get_products` has three divergent serialization flows (MCP, A2A explicit, A2A natural language) that produce slightly different output for the same business logic call.

4. **Version compat is tangled into handlers.** V2 backward-compatibility (adding `is_fixed`, `rate`, `price_guidance.floor` to pricing options for pre-3.0 clients) is applied differently in each path — some unconditionally, some gated on version, some not at all.

5. **No REST API.** AdCP promises REST support. Adding it today means building a fourth path with a fourth reimplementation of auth/validation/serialization.

The root cause: there is no shared application framework. FastMCP is a framework for MCP. A2A has no framework. Each transport reinvents the same infrastructure.

## Current Architecture

### Deployment

```
nginx (:8000)
  /mcp/*    ->  adcp-server (:8080)   [FastMCP, mcp.run()]
  /a2a/*    ->  adcp-server (:8091)   [Starlette via A2A SDK]
  /admin/*  ->  admin-ui (:5000)      [Flask]
```

Two Python processes for MCP and A2A. Three frameworks.

### Framework Capabilities by Transport

| Capability              | MCP (FastMCP)  | A2A               | Admin (Flask)  |
|-------------------------|----------------|--------------------|----------------|
| Routing                 | @mcp.tool      | Hand-rolled        | @app.route     |
| Request validation      | TypeAdapter    | Manual per-handler | Manual         |
| Response serialization  | to_jsonable_py | model_dump() everywhere | jsonify    |
| Error handling          | ToolError exc  | Dict returns + catch-all | abort()   |
| Auth/DI                 | Context inject | Manual per-handler | decorators     |
| Middleware              | Middleware API | None               | before_request |

### A2A Request Flow (current)

```
on_message_send(params)
  |
  |-- Parse text/data parts (manual, 50 lines)
  |-- Extract auth token (manual)
  |-- Route: keyword match for NL, skill name for explicit (manual, 80 lines)
  |
  |-- _handle_explicit_skill(skill_name, parameters, auth_token)
  |     |-- Map skill_name to handler (dict lookup)
  |     |-- Call handler
  |     |-- _serialize_for_a2a(result) -- adds protocol fields
  |     |-- Build Task/Artifact/DataPart (manual, 60 lines)
  |
  |-- Individual handlers (15 of them):
        |-- Auth extraction (repeated in every handler)
        |-- Parameter validation (repeated, returns error dicts)
        |-- Call core _raw() function
        |-- Serialization (inconsistent per handler)
        |-- Return dict or model (inconsistent)
```

### MCP Request Flow (current)

```
FastMCP receives JSON-RPC call
  |-- Validates args against function signature (automatic)
  |-- Injects Context (automatic)
  |-- Calls @mcp.tool function
  |     |-- Build request model
  |     |-- Call _impl()
  |     |-- response.model_dump()      <-- handler does serialization (wrong)
  |     |-- add_v2_compat()            <-- handler does version compat (wrong)
  |     |-- Return ToolResult(dict)
  |-- FastMCP sends response (automatic)
```

Even in MCP where the framework is good, handlers reach into the serialization layer.

## Proposed Architecture

### Use FastAPI as the unified application framework.

FastAPI is built on Starlette (already a dependency). It provides routing, Pydantic validation, automatic serialization, dependency injection, exception handlers, middleware, OpenAPI docs, and streaming — everything we're hand-rolling or missing.

The A2A SDK already ships `A2AFastAPIApplication` — a FastAPI-native variant of the Starlette integration. FastMCP's `http_app()` returns a Starlette app that mounts onto FastAPI.

### Target Architecture

```
FastAPI (:8080)
  |
  |-- Middleware pipeline (shared):
  |     |-- CORS
  |     |-- Auth extraction
  |     |-- Request logging
  |     |-- Version compat (post-response transform)
  |
  |-- /mcp/*     ->  FastMCP mounted as Starlette sub-app
  |-- /a2a/*     ->  A2A SDK mounted via A2AFastAPIApplication
  |-- /api/v1/*  ->  REST routes (FastAPI native)
  |
  |-- Exception handlers (shared):
  |     |-- AdCPValidationError -> 400 + AdCP error format
  |     |-- AuthenticationError -> 401
  |     |-- ToolError -> mapped to transport-specific error
  |
  |-- Dependency injection (shared):
        |-- get_auth_context() -> AuthContext
        |-- get_adcp_version() -> str | None
```

### Handler Pattern (target)

Handlers become pure async functions. No auth. No validation. No serialization. No error dicts.

```python
# Before (A2A handler, current):
async def _handle_get_products_skill(self, parameters: dict, auth_token: str | None) -> Any:
    try:
        tool_context: ToolContext | MinimalContext
        if auth_token:
            tool_context = self._create_tool_context_from_a2a(auth_token=auth_token, tool_name="get_products")
        else:
            tool_context = MinimalContext.from_request_context()

        brand_manifest = parameters.get("brand_manifest")
        if isinstance(brand_manifest, str):
            brand_manifest = {"url": brand_manifest}
        elif brand_manifest is not None and not isinstance(brand_manifest, dict):
            raise ServerError(InvalidParamsError(...))

        brief = parameters.get("brief", "")
        if not brief and not brand_manifest:
            raise ServerError(InvalidParamsError(...))

        if isinstance(tool_context, ToolContext):
            mcp_ctx = self._tool_context_to_mcp_context(tool_context)
        else:
            mcp_ctx = cast(ToolContext, tool_context)

        response = await core_get_products_tool(...)

        adcp_version = parameters.get("adcp_version")
        if needs_v2_compat(adcp_version):
            if isinstance(response, dict):
                response_data = response
            else:
                response_data = response.model_dump(mode="json")
            if "products" in response_data:
                response_data["products"] = add_v2_compat_to_products(response_data["products"])
            return response_data
        return response

    except Exception as e:
        raise ServerError(InternalError(message=f"Unable to retrieve products: {str(e)}"))


# After (pure handler):
async def get_products(req: GetProductsRequest, ctx: AuthContext) -> GetProductsResponse:
    return await _get_products_impl(req, ctx)
```

Auth, validation, serialization, version compat, error handling — all handled by the framework.

### Version Compat as Middleware

```python
# Version compat is a response transform, not handler logic.
# Registered per-tool (only tools that need it).

_version_transforms: dict[str, Callable] = {}

def register_version_transform(tool_name: str):
    def decorator(fn):
        _version_transforms[tool_name] = fn
        return fn
    return decorator

@register_version_transform("get_products")
def get_products_v2_compat(response_data: dict, adcp_version: str | None) -> dict:
    if needs_v2_compat(adcp_version) and "products" in response_data:
        response_data["products"] = add_v2_compat_to_products(response_data["products"])
    return response_data
```

The middleware applies the registered transform after serialization, before sending the response. Same transform runs regardless of transport (MCP, A2A, REST).

### Error Handling as Exceptions

```python
# Before: dict returns mixed with model returns
if missing_params:
    return {
        "success": False,
        "message": f"Missing required AdCP parameters: {missing_params}",
        "errors": [{"code": "validation_error", "message": f"Missing: {missing_params}"}],
    }

# After: typed exceptions caught by framework
if missing_params:
    raise AdCPValidationError(
        message=f"Missing required AdCP parameters: {missing_params}",
        code="validation_error",
    )

# Framework exception handler (registered once):
@app.exception_handler(AdCPValidationError)
async def handle_adcp_validation(request, exc):
    return JSONResponse(status_code=400, content={
        "success": False,
        "message": str(exc),
        "errors": [{"code": exc.code, "message": str(exc)}],
    })
```

### A2A Integration

The A2A SDK's `on_message_send` still handles the A2A JSON-RPC protocol (parsing message parts, building Task/Artifact responses). But skill dispatch calls the same handlers as REST:

```python
async def on_message_send(self, params, context):
    # A2A protocol parsing (SDK concern)
    skill_invocations = parse_skill_invocations(params.message)

    for invocation in skill_invocations:
        # Call the shared handler (same as REST endpoint)
        response = await dispatch_skill(invocation.skill, invocation.parameters, auth_context)
        # Version compat applied by shared pipeline
        # Serialization applied by shared pipeline

    # A2A envelope (SDK concern)
    return build_task_with_artifacts(results)
```

The `dispatch_skill` function uses the same routing, validation, and serialization as REST. A2A's `on_message_send` is just a protocol translator.

### MCP Integration

FastMCP tools can delegate to the shared handlers:

```python
@mcp.tool
async def get_products(brand_manifest: BrandManifest | None = None, brief: str = "", ..., ctx: Context) -> GetProductsResponse:
    req = create_get_products_request(brief=brief, brand_manifest=brand_manifest, ...)
    return await _get_products_impl(req, ctx)
    # FastMCP serializes the response via to_jsonable_python()
    # Version compat applied by FastMCP middleware (on_call_tool hook)
```

FastMCP's `Middleware.on_call_tool` hook applies the version compat transform after the tool returns.

## Key Properties

1. **One handler, three transports.** Business logic is written once. MCP, A2A, and REST are transport adapters.
2. **Framework handles boilerplate.** Auth, validation, serialization, error handling are FastAPI's job.
3. **Errors are exceptions.** Handlers never return error dicts. The framework catches exceptions and formats error responses per transport.
4. **Version compat is a registered post-response transform.** Not handler logic. Applied uniformly regardless of transport.
5. **REST API for free.** Adding a REST endpoint is `@app.post("/api/v1/endpoint")` pointing at the same handler.

## What This Requires

### New dependency

- `fastapi` (built on Starlette, which is already installed)

### Structural changes

- Create a shared handler layer (plain async functions with typed params)
- Move auth to FastAPI dependency injection
- Move validation errors from dict returns to exceptions
- Create FastAPI exception handlers for AdCP error types
- Mount FastMCP and A2A SDK on the FastAPI app
- Version compat as middleware/post-response hook

### Migration path

This can be done incrementally, one handler at a time:
1. Add FastAPI, create the main app, mount existing MCP and A2A sub-apps (no behavior change)
2. Extract one handler (e.g., get_products) to the shared pattern
3. Wire it through all three transports
4. Repeat for remaining handlers
5. Delete the hand-rolled A2A dispatch code as handlers migrate

### What stays the same

- `_impl()` functions (business logic core) — unchanged
- Database layer — unchanged
- Adapter layer (GAM, Mock) — unchanged
- Admin UI (Flask) — separate concern, can stay as-is or migrate later
- A2A SDK's `RequestHandler.on_message_send` — still handles A2A protocol, just delegates to shared handlers
- FastMCP's `@mcp.tool` — still registers MCP tools, just delegates to shared handlers

## Open Questions

1. **Single process or keep separate?** Currently MCP and A2A are in separate processes. FastAPI can host both, but merging processes changes the deployment topology.

2. **Admin UI migration?** Flask admin UI is a separate concern (HTML templates, OAuth, etc.). Could stay as Flask mounted on FastAPI via WSGIMiddleware, or migrate later.

3. **FastMCP middleware vs FastAPI middleware for MCP tools?** FastMCP has its own middleware pipeline (`Middleware.on_call_tool`). Version compat for MCP could use either FastMCP middleware or be applied in the tool function. FastMCP middleware only runs through the MCP protocol path — it doesn't run when calling `ToolManager.call_tool` directly.

4. **A2A natural language path?** NL queries currently use keyword matching to route to handlers. This is independent of the framework question — it's application-level routing that stays in `on_message_send`. The fix is simpler: NL routes through the same explicit handler (pricing queries already do this).

5. **Gradual vs big-bang migration?** The incremental path (one handler at a time) is safer but means a period where both patterns coexist.
