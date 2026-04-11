# Agent D — AdCP Wire-Format Verification for Full-Async v2.0.0 Pivot

**Date:** 2026-04-11
**Agent:** D (AdCP wire-format verifier)
**Scope:** Hard verification that the Flask → FastAPI v2.0 migration + full async SQLAlchemy absorption does NOT change AdCP wire format (headers, bodies, status codes, schemas, URLs, tool signatures, webhook payloads, or discovery URLs)
**Method:** file-level code inspection of every AdCP-touching surface; cross-check against Agent B risk matrix (`agent-b-risk-matrix.md`), the async pivot checkpoint (`async-pivot-checkpoint.md`), and the 1st-order AdCP boundary audit (`flask-to-fastapi-adcp-safety.md`)
**Hard requirement from user:** "as long as we don't break adcp schema and can't see how we would" — this report proves or refutes that claim per-surface.

---

## Section 0 — Executive Verdict

**Overall verdict: PASS with two (minor) mitigation items and one (trivial) code fix.**

The async pivot (Blocker #4 Option A absorbed into v2.0) does NOT change the AdCP wire format. Every AdCP-facing surface has been verified against the source code. There is ONE class of latent bug that must be fixed during Wave 4 (the 8 missing `await` calls in `src/routes/api_v1.py`) but this fix is independently correct even under the old sync plan — it is what Agent A flagged as "Risk #15 — pre-existing REST latent bug FIXED by pivot." The wire format of the REST surface is preserved.

### Category breakdown

| Category | Count | Surfaces |
|---|---|---|
| **PASS — zero risk** | 18 | MCP tool registration, A2A handlers, A2A SDK integration, agent card, `/openapi.json`, `schemas.py` URLs, `ResolvedIdentity`, `AdCPError`, OAuth paths, session cookies, CORS, content-types, error body shapes, auth header parsing, webhook delivery semantics, rate-limit headers (N/A), pagination, long-lived operations. |
| **PASS with mitigation** | 2 | (a) 8 missing `await` sites in `src/routes/api_v1.py` — trivial fix, must land in Wave 4 alongside `_raw → async def _raw` conversions. (b) Risk #5 `server_default=func.now()` datetime columns — verified that NO AdCP wire response is sourced from an ORM instance post-INSERT where `server_default` would leave the column stale. Mitigation is preventive: guard test + repository policy. |
| **FAIL** | 0 | — |

### Top 3 risks (all mitigable pre-merge)

1. **The 8 missing `await` calls in `src/routes/api_v1.py`** — LATENT TODAY, MUST FIX WHEN `_raw` BECOMES ASYNC. Without the fix, `await`-less calls to async functions return coroutines, which would crash `response.model_dump(mode="json")` on the next line and emit 500 errors. This is load-bearing: the AdCP REST surface goes 500 across 8 routes on the first request post-deploy. Trivial fix (add 8 `await` keywords); non-negotiable.

2. **Risk #5 — `expire_on_commit=False` + `server_default` datetime columns** — verified NO current wire-format AdCP field reads a server_default column post-INSERT without explicit client-side default or an explicit refresh. The risk remains latent for future code: a new `_impl` function could easily introduce a regression by reading `obj.created_at` on a fresh instance. Recommend `test_architecture_no_server_default_without_refresh.py` guard plus the Step 2 migration in Agent B §Risk #5 (convert `server_default=func.now()` → `default=datetime.utcnow`).

3. **`GetMediaBuysMediaBuy.created_at` / `updated_at` are declared `datetime | None`** (i.e., nullable) in the AdCP response schema. This is a GET path (reads existing rows, not post-INSERT), so the risk is structurally impossible today. But the nullable-ness is a SAFETY NET — if the field were `datetime` (required), a stale None post-INSERT would crash Pydantic validation at response serialization. The nullable declaration is load-bearing. A guard test should assert this field stays nullable.

### Go/no-go

**GO.** The "zero AdCP impact" hard requirement is MET, conditional on:
- The 8 missing `await` calls being fixed as part of Wave 4 `_raw → async` conversion (not a separate task)
- A Wave 4 entry criterion: every new `_impl` function that reads `obj.created_at` / `obj.updated_at` post-commit uses `datetime.now(UTC)` at construction or explicitly refreshes
- Adding the 2 recommended guard tests (Section 4)

---

## Section 1 — Per-Surface Analysis

### Surface 1 — MCP tool registration (FastMCP)

**Verdict: PASS**

**Current state verified at `src/core/main.py:300-315`:**
```python
mcp.tool()(with_error_logging(list_accounts))       # 300
mcp.tool()(with_error_logging(sync_accounts))        # 301
mcp.tool()(with_error_logging(get_adcp_capabilities))# 302
mcp.tool()(with_error_logging(get_products))         # 303
mcp.tool()(with_error_logging(list_creative_formats))# 304
mcp.tool()(with_error_logging(sync_creatives))       # 305
mcp.tool()(with_error_logging(list_creatives))       # 306
mcp.tool()(with_error_logging(list_authorized_properties)) # 307
mcp.tool()(with_error_logging(create_media_buy))     # 308
mcp.tool()(with_error_logging(update_media_buy))     # 309
mcp.tool()(with_error_logging(get_media_buy_delivery))# 310
mcp.tool()(with_error_logging(get_media_buys))       # 311
mcp.tool()(with_error_logging(update_performance_index)) # 312
mcp.tool()(with_error_logging(list_tasks))           # 313
mcp.tool()(with_error_logging(get_task))             # 314
mcp.tool()(with_error_logging(complete_task))        # 315
```

16 tools registered via the FastMCP decorator pattern. Imports are from `src.core.tools.*` at lines 284-295. Each tool's public function is verified:

| Tool | File | Signature line | Current state |
|---|---|---|---|
| `list_accounts` | `src/core/tools/accounts.py:178` | `async def list_accounts(...)` | ALREADY ASYNC |
| `sync_accounts` | `src/core/tools/accounts.py:651` | `async def sync_accounts(...)` | ALREADY ASYNC |
| `get_adcp_capabilities` | `src/core/tools/capabilities.py:244` | `async def get_adcp_capabilities(...)` | ALREADY ASYNC |
| `get_products` | `src/core/tools/products.py:809` | `async def get_products(...)` | ALREADY ASYNC |
| `list_creative_formats` | `src/core/tools/creative_formats.py:444` | `async def list_creative_formats(...)` | ALREADY ASYNC |
| `sync_creatives` | `src/core/tools/creatives/sync_wrappers.py:17` | `async def sync_creatives(...)` | ALREADY ASYNC |
| `list_creatives` | `src/core/tools/creatives/listing.py:389` | `async def list_creatives(...)` | ALREADY ASYNC |
| `list_authorized_properties` | `src/core/tools/properties.py:200` | `async def list_authorized_properties(...)` | ALREADY ASYNC |
| `create_media_buy` | `src/core/tools/media_buy_create.py:3689` | `async def create_media_buy(...)` | ALREADY ASYNC |
| `update_media_buy` | `src/core/tools/media_buy_update.py:1387` | `async def update_media_buy(...)` | ALREADY ASYNC |
| `get_media_buy_delivery` | `src/core/tools/media_buy_delivery.py:587` | `async def get_media_buy_delivery(...)` | ALREADY ASYNC |
| `get_media_buys` | `src/core/tools/media_buy_list.py:219` | `async def get_media_buys(...)` | ALREADY ASYNC |
| `update_performance_index` | `src/core/tools/performance.py:130` | `async def update_performance_index(...)` | ALREADY ASYNC |
| `list_tasks` | `src/core/tools/task_management.py:24` | `async def list_tasks(...)` | ALREADY ASYNC |
| `get_task` | `src/core/tools/task_management.py:124` | `async def get_task(...)` | ALREADY ASYNC |
| `complete_task` | `src/core/tools/task_management.py:188` | `async def complete_task(...)` | ALREADY ASYNC |

**Conclusion: every MCP tool is already `async def` in the public wrapper. The full async pivot does NOT change the tool wrapper signatures. The internal `_impl` and `_raw` functions change, but the MCP tool surface (what `list_tools` enumerates and what the client sees) is invariant.**

**FastMCP introspection verification** (`.venv/lib/python3.12/site-packages/fastmcp/tools/function_tool.py:244-287`):
```python
async def run(self, arguments: dict[str, Any]) -> ToolResult:
    wrapper_fn = without_injected_parameters(self.fn)
    type_adapter = get_cached_typeadapter(wrapper_fn)
    ...
    if is_coroutine_function(wrapper_fn):
        result = await type_adapter.validate_python(arguments)
    else:
        result = await call_sync_fn_in_threadpool(type_adapter.validate_python, arguments)
        if inspect.isawaitable(result):
            result = await result
```

FastMCP branches on `is_coroutine_function(wrapper_fn)`. Both sync and async are supported natively; the tool schema is derived from `inspect.signature(fn)` which is INVARIANT between sync and async (verified empirically: `inspect.signature(async_fn).return_annotation` == `inspect.signature(sync_fn).return_annotation`). The `ParsedFunction.from_function` path at `function_parsing.py:117-200` derives schema from the type adapter, which uses `typing.get_type_hints(fn)` — entirely async-agnostic.

**Evidence of invariance:**
```python
>>> import inspect
>>> async def foo() -> dict: pass
>>> def bar() -> dict: pass
>>> inspect.signature(foo).return_annotation is inspect.signature(bar).return_annotation
True
```

**Wire output for `list_tools` MCP protocol call:** unchanged. Tool names, descriptions, JSON schemas, input types, output types, and arg defaults all derive from signature introspection on the public wrappers. The `inspect.signature()` of an `async def` function is identical to the `inspect.signature()` of the corresponding `def` function for schema purposes. **PASS.**

---

### Surface 2 — A2A protocol handlers

**Verdict: PASS**

**File:** `src/a2a_server/adcp_a2a_server.py` (2284 LOC)
**A2A class:** `AdCPRequestHandler` (extends SDK `RequestHandler`), registered at `src/app.py:108`

**All A2A handler methods are ALREADY `async def`.** Verified by enumerating all methods in the class (file grep):

```
142:    def __init__(self):           # non-handler (sync init)
147:    def _get_auth_token(...):     # sync helper
158:    def _resolve_a2a_identity(...): # sync helper
228:    def _make_tool_context(...):  # sync helper
258:    def _log_a2a_operation(...):  # sync helper
286:    async def _send_protocol_webhook(...)
368:    def _reconstruct_response_object(...) # sync helper
431:    async def on_message_send(...)
894:    async def on_message_send_stream(...)
917:    async def on_get_task(...)
934:    async def on_cancel_task(...)
955:    async def on_resubscribe_to_task(...)
968:    async def on_get_task_push_notification_config(...)
1025:   async def on_set_task_push_notification_config(...)
1144:   async def on_list_task_push_notification_config(...)
1198:   async def on_delete_task_push_notification_config(...)
1257:   def _serialize_for_a2a(response) # sync static helper
1291:   async def _handle_explicit_skill(...)
1391:   async def _handle_get_products_skill(...)
1439:   async def _handle_create_media_buy_skill(...)
...
```

Every `on_*` protocol entry point (the A2A SDK's handler contract) is async. Every `_handle_*_skill` helper is async. Every `_send_protocol_webhook` is async. No handler conversion is required.

**A2A SDK async contract** (`.venv/lib/python3.12/site-packages/a2a/server/apps/jsonrpc/jsonrpc_app.py`):
```
282:    async def _handle_requests(self, request: Request) -> Response:
396:    async def _process_streaming_request(...)
428:    async def _process_non_streaming_request(...)
560:    async def _handle_get_agent_card(self, request: Request) -> JSONResponse:
588:    async def _handle_get_authenticated_extended_agent_card(...)
```

The A2A SDK (`a2a-sdk[http-server]>=0.3.19`) is built on Starlette and is already async-native. `AdCPRequestHandler` correctly implements the async contract.

**`add_routes_to_app` pattern** (`.venv/lib/python3.12/site-packages/a2a/server/apps/jsonrpc/starlette_app.py:154-174`):
```python
def add_routes_to_app(
    self,
    app: Starlette,
    agent_card_url: str = AGENT_CARD_WELL_KNOWN_PATH,
    rpc_url: str = DEFAULT_RPC_URL,
    extended_agent_card_url: str = EXTENDED_AGENT_CARD_PATH,
) -> None:
    routes = self.routes(...)
    app.routes.extend(routes)
```

Pure `app.routes.extend(routes)` — the SDK injects `Route` objects directly into the FastAPI app's router. These Route objects carry async endpoints built by the SDK. Middleware propagation (verified in first-order audit §10.1) works because A2A routes share the root scope with FastAPI-native routes.

**`_replace_routes()` swap mechanics** (`src/app.py:192-215`):
```python
def _replace_routes():
    async def dynamic_agent_card(request: Request):
        card = _create_dynamic_agent_card(request)
        return JSONResponse(card.model_dump(mode="json"))

    for route in app.routes:
        path = getattr(route, "path", None)
        if path in _AGENT_CARD_PATHS:
            new_routes.append(Route(path, dynamic_agent_card, methods=["GET", "OPTIONS"]))
        ...
    app.router.routes = new_routes
```

`dynamic_agent_card` is already `async def`. The agent card payload comes from `_create_dynamic_agent_card(request)` (sync — but only does header parsing, no DB) + `agent_card.model_copy()` + `.model_dump(mode="json")`. ZERO database access, so no async-pivot impact on the agent card. Wire-byte-identical.

**`/.well-known/agent-card.json` and `/agent.json` and `/a2a` paths** — byte-immutable. The paths are string literals in `src/app.py:118-123`:
```python
a2a_app.add_routes_to_app(
    app,
    agent_card_url="/.well-known/agent-card.json",
    rpc_url="/a2a",
    extended_agent_card_url="/agent.json",
)
```
Unchanged by pivot. AdCP clients see identical routes.

**`/a2a/` trailing-slash redirect shim** (`src/app.py:127-135`): returns `307` redirect to `/a2a`. Not DB-touching. Wire-identical.

**Verdict: PASS.** Every A2A JSON-RPC response body, agent card JSON, and `/a2a` HTTP status code is byte-identical pre- and post-pivot.

---

### Surface 3 — REST endpoint bodies (`src/routes/api_v1.py`)

**Verdict: PASS WITH MITIGATION**

**File:** `src/routes/api_v1.py` (379 LOC)
**Routes:** 12 FastAPI routes under `/api/v1/*`

**Every REST route is currently `async def`** (verified by grep). They delegate to `_raw` / `_impl` functions in `src/core/tools/*`. The **latent bug** is that 8 of these `_raw` functions are currently sync `def` — when the async pivot converts them to `async def`, each call site must gain an `await` keyword, or the route will return a coroutine instead of a response, crashing on the next line's `response.model_dump(...)` call.

**Exhaustive list of latent `await` sites** (verified by cross-referencing `src/routes/api_v1.py` route bodies with `src/core/tools/*.py` `_raw` signatures):

| Line | Current state | Target state (Wave 4) |
|---|---|---|
| 175 | `response = await products_module._get_products_impl(req, identity)` | ALREADY CORRECT (already awaits) |
| 188 | `response = await capabilities_module.get_adcp_capabilities_raw(identity=identity)` | ALREADY CORRECT |
| 200 | `response = creative_formats_module.list_creative_formats_raw(identity=identity)` | **MUST ADD `await`** |
| 214 | `response = properties_module.list_authorized_properties_raw(identity=identity)` | **MUST ADD `await`** |
| 230 | `response = await media_buy_create_module.create_media_buy_raw(...)` | ALREADY CORRECT |
| 252 | `response = media_buy_update_module.update_media_buy_raw(...)` | **MUST ADD `await`** |
| 284 | `response = media_buy_delivery_module.get_media_buy_delivery_raw(...)` | **MUST ADD `await`** |
| 305 | `response = creatives_sync_module.sync_creatives_raw(...)` | **MUST ADD `await`** |
| 324 | `response = creatives_listing_module.list_creatives_raw(...)` | **MUST ADD `await`** |
| 342 | `response = performance_module.update_performance_index_raw(...)` | **MUST ADD `await`** |
| 360 | `response = accounts_module.list_accounts_raw(req=req, identity=identity)` | **MUST ADD `await`** |
| 374 | `response = await accounts_module.sync_accounts_raw(req=req, identity=identity)` | ALREADY CORRECT |

**Count: 8 missing `await` calls.** This matches Agent A's count.

**Signatures currently sync (verified):**
- `src/core/tools/creative_formats.py:505` `def list_creative_formats_raw(...)`
- `src/core/tools/properties.py:233` `def list_authorized_properties_raw(...)`
- `src/core/tools/media_buy_update.py:1464` `def update_media_buy_raw(...)`
- `src/core/tools/media_buy_delivery.py:652` `def get_media_buy_delivery_raw(...)`
- `src/core/tools/creatives/sync_wrappers.py:72` `def sync_creatives_raw(...)`
- `src/core/tools/creatives/listing.py:462` `def list_creatives_raw(...)`
- `src/core/tools/performance.py:156` `def update_performance_index_raw(...)`
- `src/core/tools/accounts.py:218` `def list_accounts_raw(...)`

**Additional latent sites found during this audit (NOT in Agent A's count):**
- `src/core/tools/capabilities.py:310` `return _get_adcp_capabilities_impl(req, identity)` — returns sync call result; when `_impl` becomes async, must become `return await _get_adcp_capabilities_impl(req, identity)`. Also line 265 (`get_adcp_capabilities` MCP wrapper, same pattern).

**Wire format impact:** NONE if fix is applied. The status codes, response body shapes, `Content-Type: application/json` headers, and `apply_version_compat()` results are all DETERMINED BY THE `_raw` / `_impl` RETURN VALUE, which is identical whether the call is `await`ed or sync. The fix is a 1-line edit per site (insert `await` keyword); it does NOT change return value or schema.

**Concurrent-request interleaving:** when `_impl` is sync and holds a `scoped_session` keyed by `threading.get_ident()`, multiple requests sharing a thread can interleave transactions. This is Risk #15 in the checkpoint. Under full async with `AsyncSession`, each request has its own session from `async_sessionmaker()` and there is no thread-identity sharing. The interleaving bug is eliminated. Wire format is unaffected either way (interleaving produces either a correct response or a 500; under the pivot, the 500 case is eliminated → strictly wire-preserving).

**Response model `.model_dump(mode="json")`** is invoked at line 179, 192, 204, 218, 245, 266, 298, 317, 335, 350, 364, 378. Under the pivot these stay unchanged — `model_dump` is a pure Pydantic method, not async-sensitive.

**`apply_version_compat()`** at line 180 is sync, pure function, operates on the dict post `model_dump`. Not affected.

**`_handle_tool_error`** at lines 45-58 produces `JSONResponse` with status `500` and body shape `{"error_code", "message", "recovery", "details"}`. Unchanged.

**Status codes:** 200 (success) / 500 (tool error) / 401 (require_auth fails). Unchanged.

**MITIGATION (MUST LAND IN WAVE 4):**
1. Convert each sync `_raw` function to `async def` (Agent A's scope).
2. Add `await` to each of the 8 sites listed above.
3. Add `await` to the 2 additional sites in `src/core/tools/capabilities.py:265, 310`.
4. Test: `tox -e integration -- -k api_v1` + `curl /api/v1/products` smoke test.
5. Guard: `tests/unit/test_api_v1_routes_await_all_impls.py` — AST-walks `src/routes/api_v1.py`, finds every `_raw` or `_impl` call, asserts that if the target is async def, the call site is `await`-prefixed.

**Verdict: PASS WITH MITIGATION.** Wire format is preserved IF the 8 missing awaits are added simultaneously with the sync→async conversion.

---

### Surface 4 — OpenAPI spec output

**Verdict: PASS**

**`/openapi.json` generation**: FastAPI auto-generates from route metadata. Under the pivot, the metadata is unchanged — routes, paths, methods, Pydantic request/response models, status codes, and tags are all untouched.

**Test pin** at `tests/unit/test_openapi_surface.py` asserts specific paths and methods (verified by grep):
```
49:    ("post", "/api/v1/products"),
50:    ("get", "/api/v1/capabilities"),
51:    ("post", "/api/v1/creative-formats"),
52:    ("post", "/api/v1/authorized-properties"),
53:    ("post", "/api/v1/media-buys"),
54:    ("put", "/api/v1/media-buys/{media_buy_id}"),
55:    ("post", "/api/v1/media-buys/delivery"),
56:    ("post", "/api/v1/creatives/sync"),
57:    ("post", "/api/v1/creatives"),
58:    ("post", "/api/v1/performance-index"),
```

These are byte-immutable. The test uses inclusion-only assertions (per first-order audit §3). Async pivot produces the same OpenAPI spec.

**Admin router `include_in_schema=False`** — planned for Wave 1 (per `flask-to-fastapi-adcp-safety.md` §3). Does not affect the AdCP REST surface. Orthogonal to the async pivot.

**`/docs`, `/redoc`** — unchanged; purely derived from `/openapi.json`.

**Recommended guard (already planned per first-order audit):** `tests/unit/test_architecture_admin_routes_excluded_from_openapi.py` asserts `not any(p.startswith("/admin") for p in app.openapi()["paths"])`. Orthogonal to async pivot.

**NEW recommendation:** `tests/unit/test_openapi_byte_stability.py` — compare `app.openapi()` against a committed snapshot JSON. Detect any unintended schema drift from the async pivot. Snapshot is committed pre-Wave 4, test asserts post-Wave-4 schema matches. Non-blocking (not a merge gate), but catches silent regressions.

**Verdict: PASS.** OpenAPI output is byte-stable across the pivot.

---

### Surface 5 — Schema URLs (`src/admin/blueprints/schemas.py`)

**Verdict: PASS**

**File:** `src/admin/blueprints/schemas.py` (208 LOC)
**Routes:** 6 Flask routes (Wave 3 migration target)

| Route | Method | Purpose |
|---|---|---|
| `/schemas/adcp/v2.4/<schema_name>.json` | GET | Serve named JSON schema |
| `/schemas/adcp/v2.4/` | GET | List schemas index |
| `/schemas/adcp/v2.4/index.json` | GET | Same as above (duplicate) |
| `/schemas/adcp/` | GET | List AdCP schema versions |
| `/schemas/` | GET | Root schemas endpoint |
| `/schemas/health` | GET | Schema service health |

**No DB access anywhere in this file.** All schemas come from `create_schema_registry()` which introspects Pydantic models. No `get_db_session()` call; no ORM touch; no async conversion needed in the data layer. Wave 3's Flask→FastAPI port is a mechanical translation (Flask blueprint → FastAPI router) with zero data-layer changes.

**`$id` URL stability** (lines 42-49):
```python
base_url = get_sales_agent_url()
schema_with_meta = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": f"{base_url}/schemas/adcp/v2.4/{schema_name}.json",
    ...
}
```

`get_sales_agent_url()` comes from `src/core/domain_config.py` — pure config lookup. Byte-stable.

**Content-Type:** Flask's `jsonify()` returns `application/json` (not `application/schema+json` despite serving JSON Schema). Under FastAPI port, `JSONResponse(schema_with_meta)` produces the same `application/json` Content-Type. Byte-stable.

**Wave 3 port MUST preserve:**
1. Exact URL paths (trailing slash presence/absence matters — `/schemas/adcp/v2.4/` has a trailing slash, `/schemas/adcp/v2.4/<name>.json` does not).
2. Exact `$id` format: `{base_url}/schemas/adcp/v2.4/{schema_name}.json`
3. Exact response body shape (`$schema`, `$id`, `title`, `description`, then spread of inner schema)
4. Exact HTTP status codes (200 for success, 404 for not found)
5. `create_schema_registry()` return value (i.e., which schemas are available)

**First-order audit action item #3** (from `flask-to-fastapi-adcp-safety.md` §7): Add Wave 2/3 contract test `tests/integration/test_schemas_discovery_external_contract.py` that hits `/schemas/adcp/v2.4/index.json` and asserts the payload shape.

**Verdict: PASS.** Schema URLs and content are wire-byte-preserved across the pivot. No async-SQLAlchemy impact because no DB is touched.

---

### Surface 6 — Webhook payload construction

**Verdict: PASS**

**Functions under audit:**
- `create_a2a_webhook_payload` (from `adcp` library, used by A2A handlers)
- `create_mcp_webhook_payload` (from `adcp` library, used by delivery scheduler)

**Call sites audited:**

**`src/a2a_server/adcp_a2a_server.py:348` — A2A webhook construction:**
```python
payload = create_a2a_webhook_payload(
    task_id=task.id,
    status=status_enum,
    context_id=task.context_id or "",
    result=result_data,
)
```

- `task.id` — from in-memory A2A `Task` object, no DB access
- `status_enum` — from local string → `GeneratedTaskStatus` enum conversion
- `task.context_id` — from in-memory Task
- `result_data` — from parameter passed in, constructed at `_send_protocol_webhook` caller sites (adapter response dicts)

**No `server_default` datetime column is read in the payload construction.** Wire-safe.

**`src/services/delivery_webhook_scheduler.py:322` — MCP webhook construction:**
```python
mcp_payload_dict = create_mcp_webhook_payload(
    task_id=media_buy.media_buy_id,
    task_type="media_buy_delivery",
    result=delivery_response,
    status=AdcpTaskStatus.completed,
)
```

- `media_buy.media_buy_id` — PK string, not a datetime
- `task_type` — literal
- `delivery_response` — `GetMediaBuyDeliveryResponse`, constructed from adapter reports (NOT from `server_default` fields)
- `status` — literal

**`delivery_response.next_expected_at`** is set at line 267 to `datetime.combine(next_day, datetime.min.time(), tzinfo=UTC)` — a CLIENT-SIDE computed value, NOT from a `server_default` column. Safe.

**`WebhookDeliveryLog.created_at`** — is a `server_default=func.now()` column (line 2125 of `models.py`). Used only in `ORDER BY` and `WHERE` SQL expressions (verified at `src/core/database/repositories/delivery.py:80, 181, 203` — these are SQL-level, not attribute access on a committed instance). The `_write_delivery_log` method writes a NEW row via `repo.create_log(...)` at `src/services/protocol_webhook_service.py:163` — the method signature for `create_log` does not return the created log object for further attribute access (returns None). Safe.

**`WebhookDeliveryRecord.created_at`** — similar pattern. Used only in `ORDER BY` at `src/core/database/repositories/delivery.py:80`. Not read post-INSERT.

**Webhook delivery ordering semantics** — `sequence_number` is calculated via `func.coalesce(func.max(WebhookDeliveryLog.sequence_number), 0)) + 1` at `src/services/delivery_webhook_scheduler.py:251-256`. Under async, this SQL expression is still correct — async doesn't change aggregate semantics. No ordering violation.

**Retry / backoff timing** — computed from `time.time()` in `src/services/protocol_webhook_service.py` (sync), not async-sensitive.

**Verdict: PASS.** All webhook payload construction paths use in-memory data or explicit client-side datetimes. No `server_default` column is read on a fresh-INSERT instance. Wire-byte-identical.

---

### Surface 7 — `ResolvedIdentity` structure

**Verdict: PASS**

**File:** `src/core/resolved_identity.py` (205 LOC)
**Class:** `ResolvedIdentity(BaseModel, frozen=True)`

**Fields (all Pydantic-typed, no ORM):**
```python
principal_id: str | None = None
tenant_id: str | None = None
tenant: Any = None           # dict[str, Any] transitional
auth_token: str | None = None
protocol: Literal["mcp", "a2a", "rest"] = "mcp"
testing_context: AdCPTestContext | None = None
account_id: str | None = None
supported_billing: list[str] | None = None
account_approval_mode: str | None = None
```

**This object is NEVER serialized to the wire.** It is the internal auth-identity carrier passed to `_impl` functions. Not wire-visible.

**`resolve_identity()`** calls `get_tenant_by_id` / `get_tenant_by_virtual_host` / `get_tenant_by_subdomain` which touch the DB. Under the async pivot, these become async — `resolve_identity` itself becomes `async def`. Callers (`_resolve_auth_dep`, `_require_auth_dep` in `src/core/auth_context.py:69, 99`) must be updated to `async def` + `await`. This is a mechanical change inside the FastAPI dependency layer.

**Wire impact:** ZERO. The returned `ResolvedIdentity` object is the same, and it's internal.

**Verdict: PASS.**

---

### Surface 8 — `AdCPError` exception hierarchy

**Verdict: PASS**

**File:** `src/core/exceptions.py` (168 LOC)

**Every exception class extends `AdCPError`** and declares 3 class-level attributes:
- `status_code: int`
- `error_code: str`
- `recovery: RecoveryHint`

**`to_dict()`** at lines 46-57:
```python
def to_dict(self) -> dict[str, Any]:
    result: dict[str, Any] = {
        "error_code": self.error_code,
        "message": self.message,
        "recovery": self.recovery,
    }
    if self.details is not None:
        result["details"] = self.details
    else:
        result["details"] = None
    return result
```

Pure sync function. No DB. No async.

**Exception handler** at `src/app.py:82-88`:
```python
@app.exception_handler(AdCPError)
async def adcp_error_handler(request: Request, exc: AdCPError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.to_dict(),
    )
```

`async def` handler (required by FastAPI). `to_dict()` is sync. Wire body shape:
```json
{
  "error_code": "VALIDATION_ERROR",
  "message": "...",
  "recovery": "correctable",
  "details": null
}
```

**Under Blocker 3 (HTML-aware handler, Wave 1):** the handler becomes Accept-aware:
- For `/admin/*` paths with `Accept: text/html` → render `templates/error.html` (new path)
- For all other paths → JSON response as today (PRESERVED)

**Wire impact on AdCP surface:** ZERO. The HTML branch only fires for admin browser paths, which are not AdCP-visible. For AdCP clients (MCP/REST/A2A), the JSON response shape is byte-identical.

**Status codes by exception:**
| Exception | `status_code` | Used by |
|---|---|---|
| `AdCPValidationError` | 400 | Input validation failures |
| `AdCPAuthenticationError` | 401 | Missing/invalid token |
| `AdCPAccountPaymentRequiredError` | 402 | Billing issues |
| `AdCPAuthorizationError` | 403 | RBAC failures |
| `AdCPAccountSuspendedError` | 403 | Suspended accounts |
| `AdCPNotFoundError` | 404 | Resource not found |
| `AdCPAccountNotFoundError` | 404 | Account ID not found |
| `AdCPConflictError` | 409 | Duplicate key |
| `AdCPAccountAmbiguousError` | 409 | Multiple account matches |
| `AdCPGoneError` | 410 | Deleted resource |
| `AdCPAccountSetupRequiredError` | 422 | Setup incomplete |
| `AdCPBudgetExhaustedError` | 422 | Budget limit |
| `AdCPRateLimitError` | 429 | Throttling |
| `AdCPError` (base) | 500 | Unclassified |
| `AdCPAdapterError` | 502 | Upstream adapter failure |
| `AdCPServiceUnavailableError` | 503 | Temp unavailable |

All preserved byte-for-byte. No async impact.

**Verdict: PASS.**

---

### Surface 9 — OAuth redirect URIs

**Verdict: PASS (critical invariant — must be enforced)**

**Paths (byte-immutable):**
- `/admin/auth/google/callback`
- `/admin/auth/oidc/{tenant_id}/callback`
- `/auth/gam/callback`

**Source of truth:** Google Cloud Console + per-tenant OIDC provider config. These paths are registered externally — changing them by one character causes `redirect_uri_mismatch` and breaks login.

**Current state in codebase:** these routes are served by Flask in `src/admin/blueprints/auth.py` today. Wave 3 (not Wave 4) ports them to FastAPI. The async pivot (Wave 4) does NOT change these routes. They remain Flask handlers until Wave 3.

**Wave 3 requirement:** exact path preservation. This is Blocker 6 in the migration plan. Not an async concern.

**Async pivot impact:** zero. OAuth callbacks are not async-sensitive; they issue sync redirects.

**Guard already planned:** `tests/unit/test_architecture_oauth_paths_frozen.py` asserts the OAuth paths are exactly as listed above (Wave 1 landing).

**Verdict: PASS.**

---

### Surface 10 — Session cookies

**Verdict: PASS**

**`SessionMiddleware`** — Starlette's `starlette.middleware.sessions.SessionMiddleware`. Pure ASGI middleware, async-native.

**First-order audit verification** (from `flask-to-fastapi-adcp-safety.md` §4): `SessionMiddleware` only emits `Set-Cookie` when `scope["session"]` is non-empty. AdCP REST/MCP/A2A handlers never write to `request.session`, so no cookie leaks onto AdCP responses.

**Under the pivot:** `SessionMiddleware` is unchanged. Starlette's `SessionMiddleware` handles sync and async identically (it's already pure ASGI).

**`FLASK_SECRET_KEY` → `SESSION_SECRET` dual-read:** preserved in v2.0 per folder `CLAUDE.md`. Not an async concern.

**Verdict: PASS.**

---

### Surface 11 — CORS behavior

**Verdict: PASS**

**`CORSMiddleware`** configured at `src/app.py:287-293`:
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _cors_origins],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

`CORSMiddleware` is pure ASGI, async-agnostic. No DB touch. No async impact.

**Allowed origins** — derived from `ALLOWED_ORIGINS` env var. Byte-stable.

**Verdict: PASS.**

---

### Surface 12 — ⚠️ Datetime fields from `server_default` (Risk #5 — THE ONE KNOWN RISK)

**Verdict: PASS** (with preventive guard recommendation)

This is the hardest part of the audit. I enumerated every `server_default` datetime column in `src/core/database/models.py`, then traced each to determine (a) whether it's ever read on a post-INSERT ORM instance, (b) whether that read feeds an AdCP wire response.

**Full inventory of `server_default` datetime columns:**

| # | Line | Model | Column | Nullable | onupdate? |
|---|---|---|---|---|---|
| 1 | 49 | `Tenant` | `created_at` | NO | — |
| 2 | 51 | `Tenant` | `updated_at` | NO | `func.now()` |
| 3 | 560 | `CurrencyLimit` | `created_at` | NO | — |
| 4 | 562 | `CurrencyLimit` | `updated_at` | NO | `func.now()` |
| 5 | 586 | `Principal` | `created_at` | (default) | — |
| 6 | 588 | `Principal` | `updated_at` | NO | `func.now()` |
| 7 | 628 | `User` | `created_at` | (default) | — |
| 8 | 668 | `TenantAuthConfig` | `created_at` | NO | — |
| 9 | 669 | `TenantAuthConfig` | `updated_at` | YES | `func.now()` (no server_default) |
| 10 | 719 | `Creative` | `created_at` | YES | — |
| 11 | 760 | `CreativeReview` | `reviewed_at` | NO | — |
| 12 | 816 | `CreativeAssignment` | `created_at` | NO | — |
| 13 | 873 | `Account` | `created_at` | (default) | — |
| 14 | 875 | `Account` | `updated_at` | (default) | `func.now()` |
| 15 | 915 | `AgentAccountAccess` | `granted_at` | (default) | — |
| 16 | 952 | `MediaBuy` | `created_at` | (default) | — |
| 17 | 954 | `MediaBuy` | `updated_at` | (default) | `func.now()` |
| 18 | 1078 | `AuditLog` | `timestamp` | (default) | — |
| 19 | 1110 | `TenantManagementConfig` | `updated_at` | (default) | `func.now()` |
| 20 | 1213 | `AdapterConfig` | `created_at` | (default) | — |
| 21 | 1215 | `AdapterConfig` | `updated_at` | (default) | `func.now()` |
| 22 | 1271 | `CreativeAgent` | `created_at` | (default) | — |
| 23 | 1273 | `CreativeAgent` | `updated_at` | (default) | `func.now()` |
| 24 | 1308 | `SignalsAgent` | `created_at` | (default) | — |
| 25 | 1310 | `SignalsAgent` | `updated_at` | (default) | `func.now()` |
| 26 | 1694 | `Context` | `created_at` | NO | — |
| 27 | 1696 | `Context` | `last_activity_at` | NO | — |
| 28 | 1746 | `WorkflowStep` | `created_at` | (default) | — |
| 29 | 1789 | `ObjectWorkflowMapping` | `created_at` | NO | — |
| 30 | 1820 | `Strategy` | `created_at` | NO | — |
| 31 | 1822 | `Strategy` | `updated_at` | NO | `func.now()` |
| 32 | 1863 | `StrategyState` | `updated_at` | NO | `func.now()` |
| 33 | 1894 | `AuthorizedProperty` | `created_at` | NO | — |
| 34 | 1896 | `AuthorizedProperty` | `updated_at` | NO | `func.now()` |
| 35 | 1929 | `PropertyTag` | `created_at` | NO | — |
| 36 | 1931 | `PropertyTag` | `updated_at` | NO | `func.now()` |
| 37 | 1965 | `PublisherPartner` | `created_at` | NO | — |
| 38 | 1967 | `PublisherPartner` | `updated_at` | NO | `func.now()` |
| 39 | 2002 | `PushNotificationConfig` | `created_at` | NO | — |
| 40 | 2004 | `PushNotificationConfig` | `updated_at` | NO | `func.now()` |
| 41 | 2075 | `WebhookDeliveryRecord` | `created_at` | NO | — |
| 42 | 2125 | `WebhookDeliveryLog` | `created_at` | NO | — |

**`server_default` but NOT datetime (verified safe — Boolean/JSONB/string):**
- Line 76, 77: `Tenant.creative_auto_approve_threshold/reject_threshold` (Float)
- Line 97, 100: `Tenant.order_name_template`, `line_item_name_template` (String)
- Line 109, 113: `Tenant.brand_manifest_policy`, `auth_setup_mode` (String/Bool)
- Line 276: `Principal.platform_mappings` default JSONB
- Line 960: `MediaBuy.is_paused` (Bool)
- Line 1146: `AdapterConfig.gam_auth_method` (String)
- Line 1189, 1209: `AdapterConfig.custom_targeting_keys`, `config_json` (JSONB)
- Line 1408: `InventoryProfile.gam_preset_sync_enabled` (Bool)
- Line 2109, 2115: `WebhookDeliveryLog.sequence_number`, `attempt_count` (Integer)

These are all non-datetime. No Risk #5 impact — the instance-level read of a non-datetime `server_default` value via `expire_on_commit=False` is also affected, but it's a different concern (integer/bool defaults don't cause wire format validation errors for AdCP types).

**Also tracked — `default=func.now()`** (client-side via SQLAlchemy, NOT server_default):
- Line 1335, 1336, 1337: `GAMInventory.last_synced`, `created_at`, `updated_at`
- Line 1437: `ProductInventoryMapping.created_at`
- Line 1492, 1493: `FormatPerformanceMetrics.last_updated`, `created_at`
- Line 1545, 1546, 1547: `GAMOrder.last_synced`, `created_at`, `updated_at`
- Lines 1608, 1609, 1610: `GAMLineItem.last_synced`, `created_at`, `updated_at` (inferred from pattern)

**`default=func.now()`** is ORM-side — SQLAlchemy invokes the function at `session.add()` time and binds the value to the instance BEFORE flush. Post-commit, the attribute is already set. These are SAFE under `expire_on_commit=False` because the value was never lazy-fetched.

---

**AdCP wire-visible datetime fields (cross-mapped from schemas/):**

| Wire field | File:Line | Type | Sourced from? |
|---|---|---|---|
| `GetMediaBuysMediaBuy.created_at` | `src/core/schemas/_base.py:2350` | `datetime | None` (nullable) | `MediaBuy.created_at` (server_default) via `media_buy_list.py:208, 330` |
| `GetMediaBuysMediaBuy.updated_at` | `src/core/schemas/_base.py:2351` | `datetime | None` (nullable) | `MediaBuy.updated_at` (server_default) via `media_buy_list.py:209, 331` |
| `Creative.created_date` | `src/core/schemas/creative.py:153` | `datetime` (required, but with `default_factory`) | `Creative.created_at` via `listing.py:303` with `datetime.now(UTC)` fallback |
| `Creative.updated_date` | `src/core/schemas/creative.py:154` | `datetime` (required, with `default_factory`) | `Creative.updated_at` via `listing.py:304` with `datetime.now(UTC)` fallback |
| `Product.expires_at` | `adcp.types.Product.expires_at` | `AwareDatetime | None` | Not sourced from a DB column; computed per-product |
| `Snapshot.as_of` | `src/core/schemas/_base.py:2304` | `datetime` (required) | From adapter response data, not DB |
| `GetMediaBuyDeliveryResponse.next_expected_at` | adcp library | `AwareDatetime | None` | `datetime.combine(next_day, datetime.min.time(), tzinfo=UTC)` (client-side) |
| `SyncCreativeResult.expires_at` | adcp library | `AwareDatetime | None` | Optional, not sourced from server_default |
| `CreativeReview.reviewed_at` | Not in wire response | N/A | — |
| `HumanTask.created_at` | `src/core/schemas/_base.py:1779` | `datetime` (required, NO default) | **NOT USED** — `task_management.py` builds untyped dicts directly, NOT via `HumanTask` Pydantic model |
| `HumanTask.updated_at` | `src/core/schemas/_base.py:1780` | `datetime` (required) | **NOT USED** — same reason |

**Fields that are AdCP-visible but NOT sourced from server_default columns:**
- `Product.expires_at` — computed per-product, not DB
- `Snapshot.as_of` — from adapter response
- `GetMediaBuyDeliveryResponse.next_expected_at` — client-side `datetime.combine()`
- `SyncCreativeResult.expires_at` — adapter response
- Agent card `protocol_version` / `version` — static strings

**Fields marked `exclude=True` (NOT wire-visible):**
- `Targeting.created_at`, `Targeting.updated_at` (`src/core/schemas/_base.py:911, 912`, excluded in `model_dump()` at lines 975-977)
- `PackageRequest.created_at`, `PackageRequest.updated_at` (`src/core/schemas/_base.py:1312, 1313`, `Field(exclude=True)`)
- `Package.created_at`, `Package.updated_at` (`src/core/schemas/_base.py:1386, 1387`, `Field(exclude=True)`)
- `Signal.created_at`, `Signal.updated_at` (`src/core/schemas/_base.py:1973, 1974`, `Field(exclude=True)`)

All `Field(exclude=True)` fields are stripped from AdCP responses automatically by Pydantic.

---

**Critical data-flow trace for the ONE wire-visible server_default-sourced field: `GetMediaBuysMediaBuy.created_at`**

**Code path** (verified):
1. AdCP client calls `get_media_buys` MCP tool or POST `/api/v1/media-buys/delivery` REST (actually, `get_media_buys` is its own REST route that doesn't exist yet in api_v1.py — it's only MCP/A2A).
2. Route calls `_get_media_buys_impl(req, identity, ...)` or equivalent in `src/core/tools/media_buy_list.py`.
3. `_impl` opens `MediaBuyUoW`, calls `uow.media_buys.get_by_principal(...)` — **a SELECT, not an INSERT**.
4. Each returned `buy: MediaBuy` is an ORM instance populated from DB rows (not fresh-INSERT).
5. `_impl` constructs `GetMediaBuysMediaBuy(created_at=buy.created_at, updated_at=buy.updated_at, ...)` at lines 208-209 and 330-331.
6. `buy.created_at` was populated BY THE SELECT, not from `server_default`. Even under `expire_on_commit=False`, the value is live.

**Critical observation:** the SELECT path is immune to Risk #5. Risk #5 only bites when code constructs a NEW ORM instance, calls `session.add()`, commits, and THEN reads `.created_at` — the post-commit read is what triggers the problem. GET/LIST paths don't have this problem.

**INSERT path for MediaBuy** (`src/core/tools/media_buy_create.py`):
- Line 2070-2085: `create_from_request(...)` called WITH `created_at=datetime.now(UTC)` (line 2084). Explicit client-side timestamp. Safe.
- Line 2937-2949: `create_from_request(...)` called WITHOUT `created_at=`. Would rely on `server_default`. BUT the return value of `create_from_request` is NOT used — the function is called as a side-effect-only write. The response object passed to the AdCP client comes from `response` (adapter response), not from the ORM instance.

**Verified:** no AdCP wire response in the codebase reads `media_buy.created_at` on a post-INSERT ORM instance. The only wire-consuming access is via SELECT paths (`_fetch_target_media_buys` at line 303, `_resolve_status_filter` etc.).

**Similarly for Creative.created_at (line 719):**
- The column is `Mapped[datetime | None]` (nullable) with `server_default=func.current_timestamp()`.
- Read at `src/core/tools/creatives/listing.py:264-271`:
```python
if isinstance(db_creative.created_at, datetime):
    created_at_dt = (
        db_creative.created_at.replace(tzinfo=UTC)
        if db_creative.created_at.tzinfo is None
        else db_creative.created_at
    )
else:
    created_at_dt = datetime.now(UTC)
```
- **DEFENSIVE FALLBACK:** if `db_creative.created_at` is NOT a datetime (e.g., None because of a cold fresh-INSERT post-commit), the code falls back to `datetime.now(UTC)`. This is a wire-safe escape hatch.
- Insert paths in `src/core/database/repositories/creative.py:212, 418` use `created_at=datetime.now(UTC)` explicitly. Safe.

**For all other wire-visible datetime sources:** either `default_factory`, explicit `datetime.now(UTC)` at construction, adapter response data, or client-side computation. NONE depend on the `server_default` column being refreshed post-INSERT.

---

**Per-hit risk classification:**

| AdCP Wire Field | ORM column | `server_default`? | Path | Hit? |
|---|---|---|---|---|
| `GetMediaBuysMediaBuy.created_at` | `MediaBuy.created_at` | YES (`func.now()`) | SELECT only (read existing rows) | **NO** |
| `GetMediaBuysMediaBuy.updated_at` | `MediaBuy.updated_at` | YES (`func.now()`, `onupdate=func.now()`) | SELECT only | **NO** |
| `Creative.created_date` | `Creative.created_at` | YES (`func.current_timestamp()`, nullable) | SELECT + defensive fallback | **NO** (defended) |
| `Creative.updated_date` | `Creative.updated_at` | NO (plain `DateTime(nullable=True)`) | Explicit `datetime.now(UTC)` in `_processing.py:66` | **NO** |
| Account fields (AdCP 3.10) | `Account.created_at`, `Account.updated_at` | YES (server_default) | SELECT only (list_accounts) | **NO** (no wire datetime field exists) |
| Tasks (`list_tasks`) | `WorkflowStep.created_at` | YES (server_default) | SELECT only, defensive `hasattr` check | **NO** (defended; also sourced to untyped dict not HumanTask Pydantic) |

**Conclusion: ZERO Risk #5 hits in the current codebase.** The "potential AdCP impact" Agent B flagged is theoretically possible but is NOT exercised by any current code path. All wire-sourcing paths are either:
1. SELECT-only (safe — values loaded from DB)
2. Explicit `datetime.now(UTC)` at construction (safe — client-side default)
3. Defensive fallback (`isinstance` check + fallback to `datetime.now(UTC)`)
4. `exclude=True` (not wire-visible)
5. Not sourced from server_default (adapter data or computed)

---

**Mitigation (preventive, NOT remedial):**

**Step 1 — Migration from `server_default=func.now()` to `default=datetime.utcnow`** (Agent B's preferred fix). This eliminates the class of risk entirely. Forces the value at `session.add()` time, so it's always visible before and after commit regardless of `expire_on_commit`. Verify with migration + re-insert existing rows or accept ORM-side drift (the existing DEFAULT in SQL is preserved; future inserts use ORM-side).

**Step 2 — Guard test** `tests/unit/test_architecture_no_server_default_without_refresh.py` (Agent B Step 4). AST-parse `models.py`, find every `server_default=`, fail if there's no `# NOQA: server-default-refreshed` comment attesting that callers handle the refresh.

**Step 3 — Specific wire-contract test** `tests/integration/test_get_media_buys_wire_datetime_present.py`:
```python
@pytest.mark.requires_db
@pytest.mark.asyncio
async def test_get_media_buys_wire_created_at_is_never_missing():
    # Create a media buy
    async with MediaBuyUoW(tenant_id) as uow:
        mb = await uow.media_buys.create_from_request(...)  # NO explicit created_at
    # Fetch via get_media_buys
    response = await _get_media_buys_impl(...)
    wire = response.model_dump(mode="json")
    for mb_json in wire["media_buys"]:
        assert mb_json["created_at"] is not None, (
            "MediaBuy.created_at is wire-visible via GetMediaBuysMediaBuy. "
            "Under async SQLAlchemy expire_on_commit=False, server_default=func.now() "
            "columns may be stale post-commit. Use default=datetime.utcnow or explicit refresh."
        )
```

**Step 4 — CI assertion that `GetMediaBuysMediaBuy.created_at` stays nullable:**
```python
def test_get_media_buys_datetime_nullability_invariant():
    """Risk #5 safety net: AdCP spec for get_media_buys specifies created_at as nullable.
    Do NOT change to datetime (required) without also migrating ORM column to default=datetime.utcnow.
    """
    field = GetMediaBuysMediaBuy.model_fields["created_at"]
    assert field.default is None, "created_at must be nullable"
```

**Verdict: PASS.** Risk #5 has zero current hits. Preventive mitigation is recommended to lock in the property.

---

### Surface 13 — Response content-type headers

**Verdict: PASS**

| Route type | Content-Type | Source |
|---|---|---|
| Admin HTML | `text/html; charset=utf-8` | Flask → Wave 3 FastAPI port preserves via `HTMLResponse` |
| REST JSON (`/api/v1/*`) | `application/json` | FastAPI `JSONResponse` + default `application/json` |
| MCP (`/mcp/*`) | per MCP protocol (SSE or JSON) | FastMCP native |
| A2A (`/a2a`) | `application/json` | a2a-sdk native |
| Schema URLs (`/schemas/adcp/*`) | `application/json` | Flask `jsonify()` → Wave 3 `JSONResponse` |
| `.well-known/agent-card.json` | `application/json` | `JSONResponse(card.model_dump(mode="json"))` at `src/app.py:197` |
| SSE / activity stream | `text/event-stream` | Admin only, not AdCP surface |
| Health/debug | `application/json` | `JSONResponse` at `src/routes/health.py` |
| Error responses (AdCPError) | `application/json` | `JSONResponse` at `src/app.py:85` |

**All Content-Types are produced by FastAPI/Starlette response classes that are async-native.** No pivot impact.

**Verdict: PASS.**

---

### Surface 14 — Error body shapes beyond AdCPError

**Verdict: PASS**

| Exception | HTTP status | Body shape | Source |
|---|---|---|---|
| `AdCPError` (registered handler) | 400-503 | `{"error_code", "message", "recovery", "details"}` | `exc.to_dict()` |
| `RequestValidationError` (Pydantic) | 422 | `{"detail": [{"loc", "msg", "type"}...]}` | FastAPI default |
| `HTTPException` | varies | `{"detail": "..."}` | FastAPI default |
| 404 (no route match) | 404 | `{"detail": "Not Found"}` | Starlette default |
| 401 (require_auth fails) | 401 | Raised as `AdCPAuthenticationError` → AdCPError handler | |
| 500 (unhandled) | 500 | `{"detail": "Internal Server Error"}` | Starlette default |
| `ToolError` (MCP) | 500 | `{"error_code", "message", "recovery", "details"}` | `_handle_tool_error` in `api_v1.py:45-58` |

**All error body shapes are produced by sync code or pure ASGI response classes.** No async-pivot impact.

**Verdict: PASS.**

---

### Surface 15 — Auth header parsing (MCP + A2A + REST)

**Verdict: PASS**

**`UnifiedAuthMiddleware`** at `src/core/auth_middleware.py` — pure ASGI async class, already written in async form.

```python
class UnifiedAuthMiddleware:
    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers: dict[str, str] = {}
        for raw_name, raw_value in scope.get("headers", []):
            ...

        token: str | None = None
        x_adcp = headers.get("x-adcp-auth", "").strip()
        if x_adcp:
            token = x_adcp
        else:
            auth_header = headers.get("authorization", "").strip()
            if auth_header.lower().startswith("bearer "):
                ...

        auth_ctx = AuthContext(auth_token=token, headers=MappingProxyType(headers))
        scope.setdefault("state", {})
        scope["state"][AUTH_CONTEXT_STATE_KEY] = auth_ctx
        await self.app(scope, receive, send)
```

Token extraction logic:
1. `x-adcp-auth` header (AdCP convention)
2. `Authorization: Bearer <token>` (RFC 7235)

**Under the pivot:** unchanged. This middleware has ZERO database access — token validation happens later in `resolve_identity()`. The middleware is pure header parsing.

**Middleware ordering** in `src/app.py:282-293`:
```python
app.add_middleware(UnifiedAuthMiddleware)     # inner
app.add_middleware(RestCompatMiddleware)      # middle
app.add_middleware(CORSMiddleware, ...)       # outer
```

Registration order is reverse of execution order. Execution: CORS → RestCompat → UnifiedAuth → handler. All three are async-native ASGI middleware. Unchanged.

**Verdict: PASS.**

---

### Surface 16 — Webhook delivery semantics

**Verdict: PASS**

**`delivery_webhook_scheduler.py` dispatch flow:**
1. Scheduler tick runs (every N seconds via `asyncio.create_task(...)`)
2. Queries media buys needing delivery reports via SELECT
3. For each, computes `sequence_number = max(existing) + 1`
4. Fetches delivery data from adapter
5. Constructs `delivery_response`
6. Calls `create_mcp_webhook_payload(...)` → `McpWebhookPayload`
7. Calls `self.webhook_service.send_notification(...)` → HTTP POST

**Under the pivot:**
- Step 1: `asyncio.create_task` unchanged (already async)
- Step 2: DB query becomes `await session.execute(...)` — async
- Step 3: aggregate query semantics unchanged (SQL-level)
- Step 4: adapter call unchanged (sync or async per adapter)
- Step 5: Pydantic construction unchanged
- Step 6: library helper unchanged
- Step 7: `httpx.AsyncClient.post(...)` or `requests.post(...)` unchanged

**Delivery ordering:** enforced by `sequence_number` at the DB level (monotonically increasing per-tenant, per-media-buy). Async doesn't reorder.

**Retry behavior:** `protocol_webhook_service._send_with_retry_and_logging` uses exponential backoff. Retry count and timing are client-side. No async impact.

**AdCP consumer view:** same sequence numbers, same ordering, same retry pattern. Wire-stable.

**Verdict: PASS.**

---

### Surface 17 — Rate limit / retry headers

**Verdict: PASS (N/A)**

**Current state:** no AdCP-visible rate-limit headers (`X-RateLimit-*`, `Retry-After`) are emitted by any route in `src/routes/api_v1.py`. No middleware adds them.

**`AdCPRateLimitError`** exists in the exception hierarchy (`src/core/exceptions.py:146`) with `status_code=429`, but no code path raises it today (grep confirms zero usages in production code).

**Under the pivot:** no change possible — there's nothing to change.

**Verdict: PASS (N/A).**

---

### Surface 18 — Pagination / cursor patterns

**Verdict: PASS**

**AdCP-visible pagination:**
- `ListAccountsResponse.pagination` (AdCP spec)
- `ListCreativesResponse` has `page`, `limit`, `total`, `has_more`
- `list_tasks` has `total`, `offset`, `limit`, `has_more` in its untyped dict response

**Cursor format:** not opaque — these are integer offsets + limits, not server-signed cursors. No encoding/decoding. No async sensitivity.

**Under the pivot:** identical. Pagination math is sync Python arithmetic.

**Verdict: PASS.**

---

### Surface 19 — Long-lived operations (async within AdCP surface)

**Verdict: PASS**

**`create_media_buy`** may spawn background work for push notifications. Under current code, the push notification config is WRITTEN to the DB, and webhook delivery happens on the scheduler's next tick. The `create_media_buy` response is synchronous — returns `CreateMediaBuySuccess` immediately with the media_buy_id.

Background task dispatch: `delivery_webhook_scheduler` runs in the `lifespan_context` (via `asyncio.create_task`). The HTTP request handler does not `await` the webhook delivery — it writes to DB, returns, and the scheduler picks it up later.

**Under the pivot:** unchanged. `asyncio.create_task` is already the async-native pattern. FastAPI's `BackgroundTasks` is not currently used in AdCP paths (verified by grep — no `BackgroundTasks` import in `src/core/tools/` or `src/routes/`).

**Verdict: PASS.**

---

### Surface 20 — MCP protocol version + capabilities

**Verdict: PASS**

**MCP protocol version** is negotiated by FastMCP's `http_app` during the streamable HTTP handshake. Version identifier is library-provided (`fastmcp.__version__` or spec-defined). Under the pivot, FastMCP version is unchanged — `fastmcp>=3.2.0` pin. Protocol negotiation is unchanged.

**AdCP extension on agent card:**
```python
adcp_extension = AgentExtension(
    uri=f"https://adcontextprotocol.org/schemas/{protocol_version}/protocols/adcp-extension.json",
    params={"adcp_version": protocol_version, "protocols_supported": ["media_buy"]},
)
```
Version string from `adcp.get_adcp_version()`. Byte-stable.

**Verdict: PASS.**

---

### Surface 21 — FastMCP lifespan

**Verdict: PASS (load-bearing, must be preserved)**

**Current state** (`src/app.py:68`):
```python
app = FastAPI(
    ...
    lifespan=combine_lifespans(app_lifespan, mcp_app.lifespan),
)
```

**`mcp_app.lifespan`** wraps FastMCP's `lifespan_context` (`src/core/main.py:82-125`), which:
1. Starts `delivery_webhook_scheduler`
2. Starts `media_buy_status_scheduler`
3. Yields (app running)
4. Stops both schedulers on shutdown

**Both schedulers access the DB.** Under the async pivot, scheduler bodies become:
```python
async def _scheduler_tick():
    async with get_db_session() as session:
        result = await session.execute(select(...))
        ...
```

**Critical invariants preserved:**
- `lifespan=combine_lifespans(app_lifespan, mcp_app.lifespan)` literal must stay in the FastAPI constructor.
- Schedulers must run inside the FastMCP lifespan context (which runs under uvicorn's lifespan protocol).
- Single-worker invariant (`workers=1`) prevents N× tick multiplication.

**Guard already planned** (`flask-to-fastapi-adcp-safety.md` §10.2): `tests/unit/test_architecture_scheduler_lifespan_composition.py` — parses `src/app.py`, asserts `FastAPI(lifespan=combine_lifespans(app_lifespan, mcp_app.lifespan))` is the literal text.

**MCP tool list** comes from the MCP app's protocol surface — unchanged by scheduler internals. Only schedulers write DB; the tool list response is generated once at startup from `@mcp.tool()` registrations.

**Async impact on schedulers:**
1. Tick timing: `asyncio.create_task(loop.run_forever)` → `asyncio.create_task(periodic_tick)` — same pattern.
2. DB access inside tick: `with get_db_session()` → `async with get_db_session()`. Mechanical change.
3. Session handling: per-tick `AsyncSession` vs current per-tick `Session`. Same pattern.

**Wire impact:** zero. Schedulers emit AdCP webhook payloads, which are Pydantic-serialized — independent of sync/async session handling.

**Verdict: PASS.**

---

## Section 2 — Risk #5 Datetime Field Audit (the heavy-lift section)

This section consolidates Section 1.12 with a finer-grained trace.

### Part A — All ORM columns with `server_default=func.now()` or similar datetime defaults

Verified by `grep server_default src/core/database/models.py`. 42 datetime columns total (see Section 1.12 table above).

### Part B — All Pydantic response models with required or optional datetime fields

| Schema | File:Line | Field | Type | Source |
|---|---|---|---|---|
| `GetMediaBuysMediaBuy` | `_base.py:2350` | `created_at` | `datetime \| None` | `MediaBuy.created_at` (server_default) |
| `GetMediaBuysMediaBuy` | `_base.py:2351` | `updated_at` | `datetime \| None` | `MediaBuy.updated_at` (server_default + onupdate) |
| `Snapshot` | `_base.py:2304` | `as_of` | `datetime` (required) | Adapter data |
| `HumanTask` | `_base.py:1779` | `created_at` | `datetime` (required) | UNUSED IN PRODUCTION (task_management returns untyped dicts) |
| `HumanTask` | `_base.py:1780` | `updated_at` | `datetime` (required) | UNUSED |
| `HumanTask` | `_base.py:1782` | `completed_at` | `datetime \| None` | UNUSED |
| `Creative` (listing) | `creative.py:153` | `created_date` | `datetime` (required, `default_factory`) | `Creative.created_at` via listing.py with defensive fallback |
| `Creative` (listing) | `creative.py:154` | `updated_date` | `datetime` (required, `default_factory`) | `Creative.updated_at` via listing.py |
| `Targeting` | `_base.py:911, 912` | `created_at`, `updated_at` | `datetime \| None` | Internal, `exclude`d from `model_dump()` |
| `PackageRequest` | `_base.py:1312, 1313` | `created_at`, `updated_at` | `datetime \| None` | Internal, `Field(exclude=True)` |
| `Package` | `_base.py:1386, 1387` | `created_at`, `updated_at` | `datetime \| None` | Internal, `Field(exclude=True)` |
| `Signal` | `_base.py:1973, 1974` | `created_at`, `updated_at` | `datetime \| None` | Internal, `Field(exclude=True)` |

### Part C — AdCP library types (from `adcp>=3.10.0`) with datetime fields

Verified by introspection:
| Lib type | Field | Type | Required? |
|---|---|---|---|
| `CreateMediaBuySuccessResponse` | `creative_deadline` | `AwareDatetime \| None` | No |
| `Product` | `expires_at` | `AwareDatetime \| None` | No |
| `GetMediaBuyDeliveryResponse` | `next_expected_at` | `AwareDatetime \| None` | No |
| `SyncCreativeResult` | `expires_at` | `AwareDatetime \| None` | No |
| `Creative` (library) | — | no datetime fields | — |
| `CreativeAssignment` (library) | — | no datetime fields | — |
| `Account` (library) | — | no datetime fields | — |

### Part D — Cross-product: every potential wire-format hit

| # | Wire field | ORM column | Path | Hit? | Why? |
|---|---|---|---|---|---|
| 1 | `GetMediaBuysMediaBuy.created_at` | `MediaBuy.created_at` (server_default) | GET (SELECT only) | **NO** | SELECT populates `created_at` from DB on load; attribute is live. Also field is `datetime \| None`, so `None` would be wire-legal anyway. |
| 2 | `GetMediaBuysMediaBuy.updated_at` | `MediaBuy.updated_at` (server_default + onupdate) | GET (SELECT only) | **NO** | Same rationale. |
| 3 | `Creative.created_date` | `Creative.created_at` (server_default, nullable) | GET with defensive fallback | **NO** | Fallback to `datetime.now(UTC)` in `listing.py:271, 280` |
| 4 | `Creative.updated_date` | `Creative.updated_at` (plain, NOT server_default) | Explicit client-side write | **NO** | `_processing.py:66` sets `updated_at = datetime.now(UTC)` before commit |
| 5 | `Snapshot.as_of` | (not DB) | Adapter data | **NO** | Not DB-sourced |
| 6 | `HumanTask.created_at` | `WorkflowStep.created_at` (server_default) | Not used | **NO** | Task management returns untyped dicts with `hasattr` check, not via HumanTask Pydantic |
| 7 | `CreateMediaBuySuccessResponse.creative_deadline` | (not DB) | Adapter-provided | **NO** | Optional, adapter data |
| 8 | `Product.expires_at` | (not DB) | Computed per-product | **NO** | Not DB-sourced |
| 9 | `GetMediaBuyDeliveryResponse.next_expected_at` | (not DB) | `datetime.combine(...)` client-side | **NO** | Not DB-sourced |
| 10 | `SyncCreativeResult.expires_at` | (not DB) | Adapter data | **NO** | Optional, adapter data |

**Total hits: 0.**

### Part E — Per-hit mitigation

None required — there are zero hits. Preventive mitigations recommended in Section 4.

---

## Section 3 — Consolidated Mitigation List

### Urgent (Wave 4 entry criterion — MUST land in the same PR as `_raw → async`)

**M1. Add 8 missing `await` keywords in `src/routes/api_v1.py`** (Section 1.3):
- Line 200: `list_creative_formats_raw`
- Line 214: `list_authorized_properties_raw`
- Line 252: `update_media_buy_raw`
- Line 284: `get_media_buy_delivery_raw`
- Line 305: `sync_creatives_raw`
- Line 324: `list_creatives_raw`
- Line 342: `update_performance_index_raw`
- Line 360: `list_accounts_raw`

Each is a 1-character insertion. Total diff: 8 `await ` insertions.

**M2. Add 2 missing `await` keywords in `src/core/tools/capabilities.py`** (Section 1.3, additional sites):
- Line 265: `response = await _get_adcp_capabilities_impl(req, identity)`
- Line 310: `return await _get_adcp_capabilities_impl(req, identity)`

**M3. Convert sync `_raw` functions to `async def`** (Wave 4, Agent A's scope): 8 function signatures plus ~20 `_impl` functions.

### High (Wave 4 guard tests)

**M4. `tests/unit/test_api_v1_routes_await_all_impls.py`** — AST-walks `src/routes/api_v1.py`, finds every `_raw` or `_impl` call in a route body, asserts that if the target is async def, the call site is `await`-prefixed. Prevents regression.

**M5. `tests/integration/test_get_media_buys_wire_datetime_present.py`** — Creates a media buy without explicit `created_at`, fetches via `get_media_buys`, asserts `created_at` is not None in the wire response. Guards against the MediaBuy INSERT path ever adopting a pattern that relies on post-commit server_default read.

### Medium (Wave 4 preventive mitigation for Risk #5)

**M6. `tests/unit/test_architecture_no_server_default_without_refresh.py`** (Agent B §Risk #5 Step 4) — AST-parse `models.py`, find every `server_default=`, fail if there's no `# NOQA: server-default-refreshed` comment attesting callers handle refresh.

**M7. Migrate `server_default=func.now()` → `default=datetime.utcnow`** for columns whose instances are read post-INSERT (Agent B §Risk #5 Step 2). Concrete candidates based on this audit:
- `Creative.created_at` (line 719) — defended by `listing.py:264-271` fallback, but cleaner to fix at the source
- `MediaBuy.created_at` (line 952) — `create_from_request` sometimes passes explicit, sometimes doesn't; safer to migrate
- `Creative.updated_at`, `MediaBuy.updated_at` — also affected by `onupdate=func.now()`. The `onupdate` semantics are SQL-level (applied on UPDATE statements); these stay on the column but need ORM `default` alongside.

### Low (preventive guards for AdCP wire stability)

**M8. `tests/unit/test_architecture_adcp_datetime_nullability.py`** — asserts `GetMediaBuysMediaBuy.created_at` and `updated_at` remain `datetime | None`. Prevents an accidental schema tightening that would turn a no-op Risk #5 case into a real crash.

**M9. `tests/unit/test_openapi_byte_stability.py`** — snapshots `app.openapi()` to a committed JSON; CI asserts the OpenAPI spec matches. Catches unintended schema drift.

### File paths & line numbers for direct-edit

| M# | File | Line(s) | Change |
|---|---|---|---|
| M1a | `src/routes/api_v1.py` | 200 | insert `await` before `creative_formats_module.list_creative_formats_raw(...)` |
| M1b | `src/routes/api_v1.py` | 214 | insert `await` before `properties_module.list_authorized_properties_raw(...)` |
| M1c | `src/routes/api_v1.py` | 252 | insert `await` before `media_buy_update_module.update_media_buy_raw(...)` |
| M1d | `src/routes/api_v1.py` | 284 | insert `await` before `media_buy_delivery_module.get_media_buy_delivery_raw(...)` |
| M1e | `src/routes/api_v1.py` | 305 | insert `await` before `creatives_sync_module.sync_creatives_raw(...)` |
| M1f | `src/routes/api_v1.py` | 324 | insert `await` before `creatives_listing_module.list_creatives_raw(...)` |
| M1g | `src/routes/api_v1.py` | 342 | insert `await` before `performance_module.update_performance_index_raw(...)` |
| M1h | `src/routes/api_v1.py` | 360 | insert `await` before `accounts_module.list_accounts_raw(...)` |
| M2a | `src/core/tools/capabilities.py` | 265 | insert `await` before `_get_adcp_capabilities_impl(req, identity)` |
| M2b | `src/core/tools/capabilities.py` | 310 | change to `return await _get_adcp_capabilities_impl(req, identity)` |
| M3 | multiple `src/core/tools/*.py` | many | convert `def *_raw` → `async def *_raw`, convert `def _*_impl` → `async def _*_impl`, add `await` to internal DB calls |
| M4 | new file `tests/unit/test_api_v1_routes_await_all_impls.py` | — | AST guard |
| M5 | new file `tests/integration/test_get_media_buys_wire_datetime_present.py` | — | regression test |
| M6 | new file `tests/unit/test_architecture_no_server_default_without_refresh.py` | — | AST guard |
| M7 | `src/core/database/models.py` | 719, 952 (and maybe others) | migrate `server_default=func.now()` to `default=datetime.utcnow` |
| M8 | new file `tests/unit/test_architecture_adcp_datetime_nullability.py` | — | schema nullability guard |
| M9 | new file `tests/unit/test_openapi_byte_stability.py` + snapshot JSON | — | OpenAPI snapshot guard |

### Wave assignment

| Mitigation | Wave | Rationale |
|---|---|---|
| M1, M2, M3 (latent `await` fixes) | Wave 4 (same PR as `_raw → async`) | Non-negotiable: without these, wire format goes 500 on first request |
| M4, M5 (guard tests for M1-M3) | Wave 4 | Must land alongside the fix |
| M6 (structural guard) | Wave 4 | Locks in Risk #5 prevention |
| M7 (ORM migration) | Wave 4 (recommended) or Wave 5 (acceptable) | Preventive; not a current hit |
| M8, M9 (schema stability guards) | Wave 4 | Cheap, catches regressions |

---

## Section 4 — Verification Test Suite

The following tests should exist pre-merge of Wave 4 to LOCK the AdCP boundary.

### Test 1 — `tests/unit/test_api_v1_routes_await_all_impls.py`

**What it asserts:** every call to a `_raw` or `_impl` function in `src/routes/api_v1.py` is either sync (target function is sync) or `await`ed.

**How it works:** AST-parse `src/routes/api_v1.py`, find each `FunctionDef` that is a route handler (decorated with `@router.<method>`), walk the body for `Call` nodes matching `*_raw` or `*_impl`, check if the enclosing node is `Await`.

**How it fails:** prints the offending line number and tool name.

**Why it matters:** prevents regression of the 8 missing `await` bugs after Wave 4.

```python
import ast
from pathlib import Path

def test_every_impl_call_in_api_v1_is_awaited_or_target_is_sync():
    source = Path("src/routes/api_v1.py").read_text()
    tree = ast.parse(source)
    offenders = []
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.AsyncFunctionDef):
            continue
        # Only examine route handlers (decorated with @router.*)
        if not any(
            isinstance(d, ast.Call)
            and isinstance(d.func, ast.Attribute)
            and isinstance(d.func.value, ast.Name)
            and d.func.value.id == "router"
            for d in fn.decorator_list
        ):
            continue
        for node in ast.walk(fn):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                name = node.func.attr
                if name.endswith("_raw") or name.endswith("_impl"):
                    # Check if enclosed by Await
                    # (Walk parents — AST doesn't track parents natively; use a helper.)
                    ...  # pseudo-code; real impl uses parent tracking
```

### Test 2 — `tests/integration/test_get_media_buys_wire_datetime_present.py`

**What it asserts:** creating a media buy without an explicit `created_at` value produces a wire response where `created_at` is NOT None.

**How it works:**
1. `MediaBuyFactory` creates a media buy (factory may or may not pass `created_at`)
2. `_get_media_buys_impl` fetches it
3. Assert the wire JSON has `media_buys[0].created_at` populated (a timestamp string, not null)

**How it fails:** triggers on a regression where the INSERT path stops setting `created_at` client-side and relies on a stale `server_default`.

### Test 3 — `tests/unit/test_architecture_no_server_default_without_refresh.py`

**What it asserts:** every `server_default=` in `src/core/database/models.py` has an accompanying `# NOQA: server-default-refreshed` comment OR the column has a parallel ORM-side `default=`.

### Test 4 — `tests/unit/test_architecture_adcp_datetime_nullability.py`

**What it asserts:** `GetMediaBuysMediaBuy.created_at` and `updated_at` (and any other wire field that tracks a `server_default` ORM column) stay nullable.

### Test 5 — `tests/unit/test_openapi_byte_stability.py`

**What it asserts:** `app.openapi()` matches a committed JSON snapshot (canonical JSON sorted keys).

**How it fails:** any change to route signatures, Pydantic models, or route metadata that alters the OpenAPI schema.

**Maintenance:** update the snapshot intentionally when the AdCP spec changes (version bump). The snapshot itself is a committed artifact under `tests/unit/fixtures/openapi_snapshot.json`.

### Test 6 — `tests/integration/test_api_v1_all_routes_smoke.py`

**What it asserts:** every route in `src/routes/api_v1.py` returns a non-500 response for a valid authenticated request.

**How it works:** for each route, build a minimal valid request body, POST/PUT/GET, assert `response.status_code < 500`.

**Why it matters:** catches the missing-`await` regression on the first request post-deploy.

### Test 7 — `tests/unit/test_adcp_tool_schema_invariance.py`

**What it asserts:** `list_tools` MCP protocol response is byte-stable pre/post async pivot.

**How it works:** capture the full tool list response from the MCP handshake, compare against a committed snapshot.

**Why it matters:** catches the theoretical case where async conversion changes a signature (e.g., dropping a default value) and silently alters the schema.

---

## Section 5 — Certification Statement

**I, Agent D, certify with source-file-level evidence that the Flask → FastAPI v2.0.0 migration + full async SQLAlchemy absorption does NOT change the AdCP wire format, subject to the conditions below.**

### Sign-off per surface

| # | Surface | Verdict | Conditions |
|---|---|---|---|
| 1 | MCP tool registration (`@mcp.tool()`) | **PASS** | None |
| 2 | A2A protocol handlers | **PASS** | None |
| 3 | REST endpoint bodies (`api_v1.py`) | **PASS WITH MITIGATION** | M1-M3 must land in same Wave-4 PR as `_raw → async` conversion |
| 4 | OpenAPI spec output | **PASS** | M9 recommended |
| 5 | Schema URLs (`schemas.py`) | **PASS** | Wave 3 port must preserve paths byte-for-byte (not an async concern) |
| 6 | Webhook payload construction | **PASS** | None |
| 7 | `ResolvedIdentity` structure | **PASS** | None (internal object, not wire-visible) |
| 8 | `AdCPError` exception hierarchy | **PASS** | Blocker 3 HTML-aware handler must preserve JSON body shape for AdCP paths |
| 9 | OAuth redirect URIs | **PASS** | Wave 3 port must preserve paths byte-for-byte (not an async concern) |
| 10 | Session cookies | **PASS** | None |
| 11 | CORS behavior | **PASS** | None |
| 12 | Datetime fields from `server_default` (Risk #5) | **PASS** | Zero current hits; M6, M7, M8 preventive mitigation recommended |
| 13 | Response content-type headers | **PASS** | None |
| 14 | Error body shapes beyond AdCPError | **PASS** | None |
| 15 | Auth header parsing | **PASS** | None |
| 16 | Webhook delivery semantics | **PASS** | None |
| 17 | Rate limit / retry headers | **PASS (N/A)** | — |
| 18 | Pagination / cursor patterns | **PASS** | None |
| 19 | Long-lived operations | **PASS** | None |
| 20 | MCP protocol version + capabilities | **PASS** | None |
| 21 | FastMCP lifespan | **PASS** | `combine_lifespans(app_lifespan, mcp_app.lifespan)` literal must be preserved (already guarded) |

### Conditional go/no-go

**GO** subject to:
1. **Mandatory (Wave 4 entry criterion):** M1 + M2 + M3 land in the same PR as the `_raw → async` conversion. These are load-bearing wire-format fixes; without them, the REST surface goes 500 on 8 routes.
2. **Strongly recommended (Wave 4):** M4 + M5 + M6 + M8 + M9 land in Wave 4 as guard tests. These prevent regression and catch unintended drift.
3. **Recommended (Wave 4 or 5):** M7 migrates `server_default=func.now()` → `default=datetime.utcnow` for at least the three high-exposure columns (`MediaBuy.created_at`, `MediaBuy.updated_at`, `Creative.created_at`).

**If M1-M3 are NOT landed in the same PR as `_raw → async`:** NO-GO. Fix is trivial; skipping it = 500 errors across 8 AdCP REST routes on first post-deploy request.

**If M7 is deferred to v2.1:** ACCEPTABLE. Current code has zero Risk #5 hits, so the preventive migration can wait. However, any NEW `_impl` code landing in the v2.0 migration waves MUST NOT introduce a fresh-INSERT + post-commit attribute-read pattern without explicit client-side `datetime.now(UTC)`.

### Final verdict

**The AdCP wire format is preserved across the full-async v2.0.0 pivot.** Every surface has been audited to the file-level. The ONE potential risk (Risk #5) has zero current hits. The 8 missing `await` calls are a latent bug that becomes manifest upon `_raw → async` conversion; the fix is 8 single-character insertions and is non-negotiable.

**Recommendation to the user: proceed with full-async absorption into v2.0.0, with M1-M3 as hard Wave-4 entry criteria.**

---

## Appendix A — Audit methodology

**Files read in full or with substantial excerpts:**
- `.claude/notes/flask-to-fastapi/async-pivot-checkpoint.md` (484 lines)
- `.claude/notes/flask-to-fastapi/CLAUDE.md` (via system reminder)
- `.claude/notes/flask-to-fastapi/flask-to-fastapi-adcp-safety.md` (455 lines)
- `.claude/notes/flask-to-fastapi/async-audit/agent-b-risk-matrix.md` (2392 lines; Risk #5 section in full)
- `src/app.py` (355 lines)
- `src/routes/api_v1.py` (379 lines)
- `src/core/main.py` (lines 1-300)
- `src/a2a_server/adcp_a2a_server.py` (greps + targeted reads of `create_agent_card` and `_send_protocol_webhook`)
- `src/core/exceptions.py` (168 lines)
- `src/core/resolved_identity.py` (205 lines)
- `src/core/auth_middleware.py` (64 lines)
- `src/core/auth_context.py` (144 lines)
- `src/routes/rest_compat_middleware.py` (73 lines)
- `src/routes/health.py` (305 lines)
- `src/admin/blueprints/schemas.py` (208 lines)
- `src/core/tools/task_management.py` (275 lines)
- `src/core/tools/media_buy_list.py` (selected ranges)
- `src/core/tools/media_buy_create.py` (selected ranges around MediaBuy writes)
- `src/core/tools/capabilities.py` (selected ranges)
- `src/core/tools/creatives/sync_wrappers.py` (125 lines)
- `src/core/tools/creatives/listing.py` (selected ranges)
- `src/core/tools/creatives/_processing.py` (selected ranges)
- `src/core/schemas/_base.py` (selected ranges: datetime fields)
- `src/core/schemas/creative.py` (selected ranges)
- `src/core/schemas/account.py` (104 lines)
- `src/core/database/models.py` (selected ranges: all `server_default` datetime columns)
- `src/core/database/repositories/media_buy.py` (selected ranges: `create_from_request`)
- `src/core/database/repositories/delivery.py`, `creative.py`, `workflow.py` (greps for `.created_at`)
- `src/services/delivery_webhook_scheduler.py` (selected ranges)
- `src/services/protocol_webhook_service.py` (selected ranges)
- `.venv/lib/python3.12/site-packages/fastmcp/tools/function_tool.py` (lines 244-287: tool `run()`)
- `.venv/lib/python3.12/site-packages/fastmcp/tools/function_parsing.py` (lines 117-200: schema introspection)
- `.venv/lib/python3.12/site-packages/a2a/server/apps/jsonrpc/starlette_app.py` (lines 154-200: `add_routes_to_app`)
- `.venv/lib/python3.12/site-packages/a2a/server/apps/jsonrpc/jsonrpc_app.py` (grep for async handlers)

**Library introspection (via `uv run python`):**
- `adcp.types.CreateMediaBuySuccessResponse`
- `adcp.types.UpdateMediaBuyResponse`
- `adcp.types.SyncCreativesResponse`
- `adcp.types.ListCreativesResponse`
- `adcp.types.GetProductsResponse`
- `adcp.types.ListCreativeFormatsResponse`
- `adcp.types.ListAuthorizedPropertiesResponse`
- `adcp.types.GetMediaBuyDeliveryResponse`
- `adcp.types.generated_poc.core.product.Product`
- `adcp.types.generated_poc.core.account.Account`
- `adcp.types.Creative`
- `adcp.types.CreativeAssignment`
- `src.core.schemas.SyncCreativeResult`

**Empirical Python verification:**
- `inspect.signature(async_fn).return_annotation == inspect.signature(sync_fn).return_annotation` → True (confirmed tool schema invariance under sync→async)

**Assumptions flagged:**
- **ASSUMED:** `factory-boy` factories will be updated to be async-compatible in Wave 4 (Agent B's concern, not mine — out of scope for wire format).
- **ASSUMED:** `asyncpg` handles JSONB type coercion identically to `psycopg2` (per Agent B Risk #2). If asyncpg returns `dict` vs `str` for JSONB, the `JSONType` TypeDecorator's `process_result_value` handles either form. Verified by reading `src/core/database/json_type.py` briefly — the code uses `isinstance(value, (str, bytes))` checks before parsing.
- **ASSUMED:** `Creative.data` and `MediaBuy.raw_request` JSONB fields will deserialize identically under asyncpg. These are dict fields, not datetime; not in the Risk #5 scope.
- **ASSUMED:** I did not verify every admin HTML route (~232 routes) because none of them are AdCP-visible surfaces. First-order audit confirmed the admin routers are not in the AdCP boundary.
- **ASSUMED:** The `tests/unit/test_openapi_surface.py` inclusion-only assertion pattern remains unchanged by the pivot. I verified its current state but did not re-run the test.

**Not audited (out of scope):**
- Runtime benchmarking of async vs sync latency (Risk #10 in Agent B's matrix)
- Connection pool saturation under concurrent load (Risk #6)
- `asyncpg` driver compatibility for Interval/UUID/Array types (Risk #2, driver spike territory)
- Factory-boy async adapter implementation (Risk #3)
- Alembic async env.py rewrite (Risk #4)
- Lazy loading `MissingGreenlet` audit (Risk #1 — Agent A's scope)

**Cross-checks performed:**
- Agent B Risk #5 §D ("THIS IS A POTENTIAL AdCP IMPACT") — verified zero current hits via exhaustive wire-field → ORM column trace
- First-order audit §1 (AdCP-facing surfaces table) — confirmed the pivot touches NO file in the audit's "OUT of migration scope" list
- First-order audit §10.1 (A2A graft invariant) — confirmed preserved
- First-order audit §10.2 (MCP lifespan composition) — confirmed preserved
- Checkpoint §9 ("AdCP protocol safety re-verification" table) — my findings are consistent: same per-surface verdict

---

## Appendix B — Signal on async risk for downstream agents

For the fresh-session agents applying the pivot edits:

**Pattern to PRESERVE across Wave 4:**
```python
# Async def wrapper explicitly awaits async _impl
async def get_products_raw(..., identity: ResolvedIdentity | None = None):
    return await _get_products_impl(req, identity)

# Async def route explicitly awaits async _raw
@router.post("/products")
async def get_products(body: GetProductsBody, identity: ResolveAuth):
    req = products_module.create_get_products_request(...)
    response = await products_module.get_products_raw(identity=identity, ...)
    return response.model_dump(mode="json")
```

**Anti-pattern to AVOID:**
```python
# BUG: returning coroutine instead of awaiting
async def get_products_raw(...):
    return _get_products_impl(req, identity)  # MISSING await!

# BUG: async route without await on async _raw
@router.post("/products")
async def get_products(...):
    response = products_module.get_products_raw(...)  # MISSING await!
    return response.model_dump(mode="json")  # crashes: coroutine has no model_dump
```

**How to verify after edit:**
```bash
# For each edited file, verify every _raw/_impl call is either sync-sync or async-await
uv run python -c "
import ast
tree = ast.parse(open('src/routes/api_v1.py').read())
for node in ast.walk(tree):
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        if node.func.attr.endswith('_raw') or node.func.attr.endswith('_impl'):
            print(f'line {node.lineno}: {ast.unparse(node)[:80]}')
"
```
Then manually inspect each line for `await` presence (or use the AST guard from M4).

---

*End of Agent D AdCP wire-format verification report.*
