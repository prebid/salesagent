# Code Review: Transport & Infrastructure Layer

## Summary

Review of the transport, auth, and infrastructure layer changes for the adcp 3.2.0 to 3.6.0 migration on branch `KonstantinMirin/adcp-v3-upgrade`. This layer covers the unified FastAPI entry point, A2A server overhaul, new REST API endpoints, health/debug routes, tenant context model, auth context, exception hierarchy, transport helpers, and Docker/nginx configuration. 14 files reviewed totaling approximately 2,800 lines of new or heavily modified code.

Three critical issues found (token leakage in error responses, misplaced debug endpoint, non-unique task IDs), three high issues (header dump in debug endpoints, missing middleware-level auth rejection, cross-tenant principal mismatch risk), two medium issues (debug log statements at info level, mutable dict in frozen dataclass), and one low issue (unbounded in-memory task store).

## Files Reviewed

| File | Type | Lines |
|------|------|-------|
| `src/app.py` | NEW | 411 |
| `src/a2a_server/adcp_a2a_server.py` | Modified | 610+ changed |
| `src/routes/api_v1.py` | NEW | 371 |
| `src/routes/health.py` | NEW | 304 |
| `src/core/tenant_context.py` | NEW | 245 |
| `src/core/auth.py` | Modified | 99 changed |
| `src/core/auth_context.py` | NEW | 49 |
| `src/core/exceptions.py` | NEW | 109 |
| `src/core/transport_helpers.py` | NEW | 98 |
| `src/core/resolved_identity.py` | NEW | 222 |
| `src/core/helpers/context_helpers.py` | Modified | 65 changed |
| `docker-compose.yml` | Modified | 44 changed |
| `docker-compose.e2e.yml` | Modified | 51 changed |
| `config/nginx/nginx-development.conf` | Modified | 167 changed |

## Critical Issues (Security Focus)

### C-1. Auth token leaked in A2A error response to clients

**File:** `src/a2a_server/adcp_a2a_server.py`, lines 254-258
**Confidence:** 100%

The `_create_tool_context_from_a2a` method includes the first 20 characters of the auth token in an error message that is sent as the JSON-RPC `message` field in the A2A response payload -- directly back to the caller:

```python
raise ServerError(
    InvalidRequestError(
        message=f"Invalid authentication token (not found in database). "
        f"Token: {auth_token[:20]}..., "          # <-- token prefix exposed in response
        f"Tenant: {requested_tenant_id or 'any'}, "
        f"Apx-Incoming-Host: {apx_host}"
    )
)
```

Any A2A client that submits an invalid or misrouted token will receive part of their raw bearer token string in the error response. While the prefix is limited to 20 characters, this is still a security disclosure: it confirms to an attacker exactly what portion of a token was received, aids in crafting/distinguishing valid from invalid tokens, and violates the principle that credentials must never appear in responses. The token prefix should be replaced with a fixed-length hash or omitted entirely. Internal correlation should use logging (already done at the `logger.debug` level in `auth_utils.py`).

**Fix:**
```python
raise ServerError(
    InvalidRequestError(
        message=f"Invalid authentication token for tenant '{requested_tenant_id or 'any'}'. "
        f"The token may be expired, revoked, or belong to a different tenant."
    )
)
```

---

### C-2. `/_internal/reset-db-pool` lives on the public `router`, not on `debug_router`

**File:** `src/routes/health.py`, lines 43-79
**Confidence:** 95%

The `debug_router` is properly guarded by `Depends(require_testing_mode)` which checks `ADCP_TESTING == "true"` and returns 404 otherwise. However, `/_internal/reset-db-pool` is registered on the unguarded `router` (line 43), not on `debug_router`. It has its own inline guard that returns a 403 with a descriptive message when `ADCP_TESTING != "true"`, but this is inconsistent with the pattern used for all other debug endpoints and causes two problems:

1. The endpoint is discoverable in production -- returning 403 with `"This endpoint is only available in testing mode"` confirms the endpoint exists and what it does, rather than being transparent (404).
2. The endpoint resets the database connection pool and clears the tenant context ContextVar -- exactly the kind of destructive internal action that should not be reachable at all in production environments.

```python
# Line 43 -- on public router, not debug_router
@router.post("/_internal/reset-db-pool")   # Should be @debug_router.post(...)
async def reset_db_pool(request: Request):
```

**Fix:** Move the `@router.post("/_internal/reset-db-pool")` decorator to `@debug_router.post("/_internal/reset-db-pool")` so it gets the same `Depends(require_testing_mode)` protection as all other debug endpoints, returning 404 in production.

---

### C-3. In-memory task store causes non-unique task IDs after concurrent requests

**File:** `src/a2a_server/adcp_a2a_server.py`, lines 135, 547
**Confidence:** 90%

The `AdCPRequestHandler` stores tasks in an instance-level dict and generates IDs using:

```python
self.tasks = {}  # In-memory task storage
...
task_id = f"task_{len(self.tasks) + 1}"   # line 547
```

`len(self.tasks) + 1` is not a safe unique ID. If any task is deleted (or if two concurrent requests arrive while the dict has the same size), two tasks will receive the same `task_id`. This is a correctness bug: the second task silently overwrites the first in `self.tasks[task_id] = task`. Given A2A clients use task IDs to poll status (`on_get_task`), a collision will return the wrong task state.

There is already a `uuid` import at the top of the file. The fix is straightforward.

**Fix:**
```python
task_id = f"task_{uuid.uuid4().hex[:16]}"
```

---

## High Issues

### H-1. `debug/root` endpoint dumps all request headers to clients

**File:** `src/routes/health.py`, lines 169-200
**Confidence:** 88%

`/debug/root` is on `debug_router` and correctly returns 404 in production. However, when `ADCP_TESTING=true`, it returns the full `request.headers` dict including any sensitive headers the caller or proxy injected:

```python
debug_info = {
    "all_headers": headers,   # includes Authorization, x-adcp-auth, x-real-ip, etc.
    ...
}
```

In e2e test environments, `ADCP_TESTING=true` is the default (`docker-compose.e2e.yml` line 40: `ADCP_TESTING: ${ADCP_TESTING:-true}`). A test environment is not a controlled single-user environment -- any test client that can reach the endpoint will receive all HTTP headers, which may include bearer tokens from internal service calls, internal IP addresses, and any other headers injected by nginx (`X-Real-IP`, `X-Forwarded-For`, `Authorization`). The endpoint serves its debugging purpose without needing to expose the raw header map. At minimum, the auth token headers should be redacted before inclusion in the response.

---

### H-2. A2A auth middleware does not reject unauthenticated requests -- discovery skill auth enforcement relies solely on business logic

**File:** `src/app.py`, lines 229-273
**Confidence:** 85%

The `a2a_auth_middleware` explicitly allows A2A requests with no token to pass through to the handler:

```python
if token:
    _request_auth_token.set(token)
else:
    logger.warning("A2A request ... missing authentication ...")
    _request_auth_token.set(None)   # no 401 raised here
```

Whether auth is enforced then depends on the handler checking `DISCOVERY_SKILLS` (lines 588-603 of the A2A server). The classification logic is:

```python
requires_auth = False
if skill_invocations:
    non_discovery_skills = requested_skills - DISCOVERY_SKILLS
    if non_discovery_skills:
        requires_auth = True
```

This means that if a natural-language path is taken (no `skill_invocations`), `requires_auth` stays `False` and the entire natural language routing block executes without authentication. Looking at the NL handlers (lines 732, 761, 825, etc.), they all call `self._create_tool_context_from_a2a(auth_token, ...)` which will raise `ServerError(InvalidRequestError("Missing authentication token"))` at that point -- so in practice the error is caught, but it is caught as a generic exception and returns `InternalError` rather than a proper 401 authentication error. The natural language `create_media_buy` path requires auth but gets a 500-class error for missing auth rather than a 401, which confuses clients.

This is a design concern rather than an exploitable bypass (the DB lookup will fail), but it violates the principle that auth should be checked at the protocol boundary before touching business logic.

---

### H-3. `api_v1.py` double-validates token with mismatched tenant scope

**File:** `src/routes/api_v1.py`, lines 57-85
**Confidence:** 83%

`_resolve_auth` performs two separate token lookups that may resolve against different tenant scopes:

```python
# Step 1: global lookup -- no tenant_id constraint
principal_id = get_principal_from_token(auth_ctx.auth_token)

# Step 2: resolve_identity with require_valid_token=False -- uses headers-detected tenant
identity = resolve_identity(
    headers=auth_ctx.headers,
    require_valid_token=False,
    protocol="rest",
)
```

Step 1 returns a `principal_id` from a **global** search across all tenants. Step 2 calls `resolve_identity` which re-runs token validation but scoped to the header-detected tenant (`require_valid_token=False` means a mismatch is silently ignored). If the header-detected tenant differs from the tenant where the token was issued, `resolve_identity` returns `principal_id=None`. The code then patches this with:

```python
if identity.principal_id != principal_id:
    identity = ResolvedIdentity(
        principal_id=principal_id,   # overrides with the globally-found principal
        tenant_id=identity.tenant_id,  # but keeps the header-detected tenant
        ...
    )
```

This can produce a `ResolvedIdentity` where `principal_id` belongs to **tenant A** but `tenant_id` is **tenant B**. Downstream `_impl` functions that query the DB with `(principal_id, tenant_id)` will find nothing and likely raise or return empty results. In the best case it silently fails; in the worst case it is a cross-tenant principal ID injection. The fix is to pass `tenant_id` from the header detection to `get_principal_from_token` in step 1 (as the A2A path does in `_create_tool_context_from_a2a`), or to rely entirely on `resolve_identity` for the token validation step.

---

## Medium Issues

### M-1. Left-behind `[DEBUG]` log statements at `logger.info` level in production code

**File:** `src/a2a_server/adcp_a2a_server.py`, lines 1148, 1153; and lines 1626-1629
**Confidence:** 87%

Three `logger.info` calls are tagged with `[DEBUG]` in their message strings, indicating they were temporary instrumentation that was not cleaned up:

```python
# line 1148
logger.info(f"[DEBUG] Received params type: {type(params)}, value: {params}")
# line 1153
logger.info(f"[DEBUG] task_id: {task_id}, push_config: {push_config}, type: {type(push_config)}")

# line 1626-1629 (sync_creatives handler)
logger.info(f"[A2A sync_creatives] Received parameters keys: {list(parameters.keys())}")
logger.info(f"[A2A sync_creatives] assignments param: {parameters.get('assignments')}")
logger.info(f"[A2A sync_creatives] creatives count: {len(parameters.get('creatives', []))}")
```

`logger.info` is always active regardless of environment. These emit on every request, including for the `on_set_task_push_notification_config` path where `{params}` may contain a webhook URL and authentication credentials. Convert to `logger.debug` or remove before release.

---

### M-2. `AuthContext.headers` is a mutable dict inside a `frozen=True` dataclass

**File:** `src/core/auth_context.py`, lines 15-26; `src/app.py`, lines 199-226
**Confidence:** 80%

`AuthContext` is declared `frozen=True` (via `@dataclass(frozen=True)`), but the `headers` field is `dict[str, str]` -- a mutable default. The middleware populates it with `dict(request.headers)`, which is safe at creation, but because `headers` is a plain `dict`, callers can still mutate the referenced dict object even though they cannot rebind the `headers` attribute. Since `AuthContext` is documented as "immutable per-request authentication context" and is stored on `request.state`, any code that receives the context object from a different request state (or caches it) and mutates `headers` would silently affect shared state. This is a low-risk issue in practice, but it violates the stated immutability guarantee. The field should be typed as `Mapping[str, str]` or the dict should be wrapped in `types.MappingProxyType`.

---

## Low Issues

### L-1. A2A task store has no upper bound -- unbounded memory growth

**File:** `src/a2a_server/adcp_a2a_server.py`, line 135
**Confidence:** 80%

`self.tasks = {}` grows without bound. Every `on_message_send` call adds an entry; nothing ever removes from it. In a long-running process under normal load this will cause memory to grow monotonically. A capped LRU dict (`functools.lru_cache` style) or TTL-based expiry is the standard fix. This is a known architectural issue (in-memory task storage is not production-ready anyway), but worth documenting explicitly.

---

## Security Analysis

### Auth bypass paths

There is no complete auth bypass found. The critical paths are:
- **MCP:** auth via `get_principal_from_context` -> token validated against DB
- **A2A:** auth via `_create_tool_context_from_a2a` -> token validated against DB
- **REST:** auth via `_require_auth` -> `get_principal_from_token` -> DB

The A2A natural-language path allows unauthenticated requests to reach the handler without an immediate 401, but the first DB call will fail and return `InternalError`. This is an error-classification defect, not an exploitable bypass.

### Debug endpoint exposure

`/debug/db-state`, `/debug/tenant`, `/debug/root`, `/debug/landing`, `/debug/root-logic` are properly on `debug_router` with the `require_testing_mode` dependency. The dependency returns 404 when `ADCP_TESTING != "true"`, which is the correct behavior. The only misplaced endpoint is `/_internal/reset-db-pool` (Issue C-2 above).

### Tenant isolation

The `TenantContext` model and `LazyTenantContext` wrapper look correct. Tenant is always resolved from headers before auth, and the token validation is scoped to the resolved tenant. The mismatch risk in `api_v1._resolve_auth` is documented in Issue H-3. No evidence of cross-tenant data access at the query level was found in the reviewed files -- DB queries consistently include `tenant_id` filters.

### Token leakage

The most significant finding is Issue C-1: raw token prefix in A2A error responses. Token prefix logging to the server log (at debug level) is also present in `auth_utils.py` but this is acceptable for server-side diagnostics. The response-level exposure is not.

### CORS

CORS is configured correctly: `allow_credentials=True` is paired with explicit origins from `ALLOWED_ORIGINS` env var (defaulting to `http://localhost:8000`), not wildcard. This is correct per the CORS spec which forbids `allow_origins=["*"]` with `allow_credentials=True`.

### Docker configuration security

- PostgreSQL in `docker-compose.yml` does not expose a host port -- access is only via Docker network. Correct.
- PostgreSQL in `docker-compose.e2e.yml` exposes port `${POSTGRES_PORT:-5435}` on the host for test access. Acceptable for test environments.
- Default password `secure_password_change_me` is used in both compose files. This is acceptable for local dev/test but should be documented as requiring change for any shared environment.
- `ENCRYPTION_KEY` has a hardcoded default in `docker-compose.e2e.yml` (line 44). Acceptable for test environments only.

---

## Architecture Assessment

### Flask to FastAPI migration quality

The unification into a single FastAPI process is well-structured. The lifespan combination pattern (`combine_lifespans(app_lifespan, mcp_app.lifespan)`) is correct and ensures both MCP schedulers and app-level hooks fire. Mounting the Flask admin via `WSGIMiddleware` is a practical transitional choice. The `_replace_routes()` function that patches SDK agent card routes with dynamic versions is unusual (mutating `app.router.routes` after construction) but functional and clearly commented.

### Middleware ordering

The comment at lines 186-195 of `app.py` correctly documents that `@app.middleware("http")` runs in reverse registration order in Starlette. The claimed order (messageId compat runs before auth) is achieved by registering auth first and messageId second. This is verified by reading the registration order in the source.

### ResolvedIdentity pattern

The introduction of `ResolvedIdentity` as a transport-agnostic identity type is architecturally sound and aligns with the shared-impl pattern (Critical Pattern #5 from CLAUDE.md). The `LazyTenantContext` design avoids N+1 DB queries for requests that only need `tenant_id`.

### Hostname validation

The `_is_valid_hostname` regex in `app.py` (lines 117-124) correctly guards against path traversal and injection in the header-driven agent card URL construction. This is a good security control.

### Duplicate tenant detection logic

Tenant detection strategy (4 ordered checks: virtual host, subdomain, x-adcp-tenant, apx-incoming-host, localhost fallback) is implemented independently in three places:
1. `src/core/auth.py` (lines 157-230)
2. `src/core/resolved_identity.py` (lines 78-140, `_detect_tenant`)
3. `src/a2a_server/adcp_a2a_server.py` (lines 186-245, `_create_tool_context_from_a2a`)

They are largely consistent but have minor ordering differences. For example, the A2A path checks subdomain before virtual host in step 1 (line 193: subdomain lookup first, then virtual host fallback), while the MCP/REST paths in `resolved_identity.py` check virtual host first (line 96: `get_tenant_by_virtual_host(host)` before subdomain extraction). The `resolve_identity()` function in `resolved_identity.py` is the intended single source of truth, but the A2A path bypasses it in `_create_tool_context_from_a2a` and reimplements detection inline. This is a maintenance risk that will cause drift over time.

### Nginx configuration

The simplified catch-all nginx pattern is clean and correct for the unified single-process architecture. WebSocket upgrade headers, auth headers, and standard proxy headers are all forwarded. The `resolver 127.0.0.11` directive uses Docker's internal DNS resolver for dynamic upstream resolution, which is correct.

---

## Recommendations

### Immediate (before merge)

1. Remove the token prefix from the A2A `InvalidRequestError` message (Issue C-1).
2. Move `/_internal/reset-db-pool` to `debug_router` (Issue C-2).
3. Replace sequential task ID generation with `uuid` (Issue C-3).

### Before release

4. Change the three `[DEBUG]` `logger.info` calls to `logger.debug` or remove (Issue M-1).
5. Audit `_handle_sync_creatives_skill` and `on_set_task_push_notification_config` parameter logging for credential content.
6. Redact auth-related headers (`Authorization`, `x-adcp-auth`) from the `/debug/root` response body (Issue H-1).

### Follow-up work

7. Consolidate the duplicate 4-strategy tenant detection into a single call to `resolve_identity()` from the A2A `_create_tool_context_from_a2a` path -- this is maintenance debt that will cause drift over time.
8. Fix the double-validation issue in `api_v1._resolve_auth` (Issue H-3) to prevent potential cross-tenant principal ID mismatches. Either pass `tenant_id` from header detection to `get_principal_from_token` in step 1, or rely entirely on `resolve_identity` for the full token validation.
9. Add middleware-level auth rejection for non-discovery A2A requests so that missing-token errors return a proper 401-class response rather than 500 (Issue H-2).
10. Cap the in-memory task store or add TTL-based expiry (Issue L-1).
