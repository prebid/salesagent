# Flask → FastAPI Migration: AdCP Safety Audit

**Date:** 2026-04-11
**Status:** Pre-implementation audit
**Purpose:** Verify that the v2.0.0 Flask → FastAPI migration plan does NOT impact the AdCP protocol surface and does NOT make assumptions that would require updates from external AdCP consumers

> **Companion to:** [flask-to-fastapi-migration.md](flask-to-fastapi-migration.md). Read that first for the migration plan itself. This file is the audit findings produced by three parallel Opus Explore subagents on 2026-04-11.

---

## Bottom line

**✅ The migration does NOT touch any AdCP-protocol surface.** No AdCP spec update is required. No external AdCP consumer will see any behavior change.

**⚠️ Eight specific action items** were found — all internal, all fixable in the plan itself:

1. ⚠️ **RISK** — Stale route count for `tenant_management_api.py` in the plan (plan says 19, actual is 6) — cosmetic fix
2. ⚠️ **RISK** — `gam_reporting_api.py` not in `_LEGACY_PATH_PREFIXES` tuple but named in plan text — reclassify as Category 1 (session-cookie authed = admin-UI-only)
3. ⚠️ **RISK** — `src/admin/blueprints/schemas.py` is externally consumed (`/schemas/adcp/v2.4/*` URLs) — needs contract test before porting
4. 🟡 **YELLOW** — `src/admin/blueprints/creatives.py` and `operations.py` emit outbound AdCP webhooks via `create_a2a_webhook_payload` / `create_mcp_webhook_payload` — port must preserve webhook shapes, and AdCP library types must not be used as `response_model` on admin routes
5. ✅ **ACTION** — Set `include_in_schema=False` on the admin router to prevent OpenAPI pollution (one-line fix)
6. ⚠️ **RISK** — `/_internal/reset-db-pool` POST is not in the CSRF exempt list — add `/_internal/` prefix
7. 🚨 **NEAR-BLOCKER** — `ApproximatedExternalDomainMiddleware` must preserve the `is_admin_request` path gate from `src/admin/app.py:226-230`, otherwise it will 302-redirect AdCP callers
8. ✅ **ACTION** — Add two new structural guards: `test_csrf_exempt_covers_all_adcp_surface.py` and `test_approximated_middleware_path_gated.py`

---

## 1. AdCP vs Internal Classification

Verified by reading each file's imports, docstring, route decorators, and cross-references with `schemas/`, `docs/`, and CLAUDE.md.

### AdCP-protocol surfaces (OUT of migration scope)

| File | Role | Notes |
|---|---|---|
| `src/routes/api_v1.py` | AdCP REST transport, 12 routes | Each route accepts `adcp_version` and calls `apply_version_compat`; delegates to `src/core/tools/*._impl`/`_raw`. Pinned by `tests/unit/test_openapi_surface.py`. |
| `src/core/main.py` | MCP tool registrations (lines 300-315) | 16 AdCP tools registered with FastMCP |
| `src/a2a_server/adcp_a2a_server.py` | A2A protocol layer | Imports from `adcp.types`, publishes Agent Card at `/.well-known/agent-card.json` |
| `src/core/tools/*.py` | `_impl()` business logic | Transport-agnostic, produces AdCP-compliant response models |
| `src/core/schemas/*.py` | Schema extensions | Extend `adcp` library types per CLAUDE.md Pattern #1 |
| `src/core/resolved_identity.py` | `ResolvedIdentity` canonical auth object | Used by all transports |
| `src/core/exceptions.py` | `AdCPError` hierarchy | Used by `@app.exception_handler(AdCPError)` |
| `src/core/auth_context.py` | `ResolveAuth`/`RequireAuth` Annotated aliases | Used by REST routes |
| `src/core/auth_middleware.py` | `UnifiedAuthMiddleware` (pure ASGI) | Reads Bearer token, populates `scope["state"]["auth_context"]` |
| `src/routes/rest_compat_middleware.py` | REST version compat | Reads/rewrites JSON body for `/api/v1/*` POSTs |

**Verified invariant:** The migration plan explicitly states "Phase 2 does NOT touch" all of these files. The invariant holds.

### Internal admin surfaces (IN migration scope)

All under `src/admin/` — 30 blueprints, ~21,340 LOC, ~232 routes. Classification:

| File/Group | Category | Notes |
|---|---|---|
| Admin HTML UI blueprints (26 files) | **Internal** — rewrite as FastAPI routers | Consumed only by the admin UI browser |
| `src/admin/blueprints/api.py` (`/api`, 7 routes) | **Internal** — Category 1 (native error shape) | Admin dashboard AJAX (revenue chart, oauth status, product listing for UI, GAM advertiser lookup). Uses admin session cookie. No AdCP consumers. |
| `src/admin/blueprints/format_search.py` (`/api/formats`, 4 routes) | **Internal** — Category 1 | Admin UI format picker. Consumes creative-agent registry. Not the AdCP `list_creative_formats` surface (that's in `src/routes/api_v1.py`). |
| `src/admin/blueprints/schemas.py` (`/schemas`, 6 routes) | ⚠️ **External (AdCP discovery)** — special handling | Serves `/schemas/adcp/v2.4/*` JSON Schemas used by external validators. **Needs contract test before porting.** |
| `src/admin/tenant_management_api.py` (`/api/v1/tenant-management`, 6 routes) | **External (not AdCP)** — Category 2 | Auth is `X-Tenant-Management-API-Key` (not AdCP's `x-adcp-auth`). External consumers: CI/provisioning scripts, tenant lifecycle automation. Preserve legacy error shape. **Plan's route count of 19 is stale — actual is 6.** |
| `src/admin/sync_api.py` (`/api/v1/sync` + `/api/sync`, 9 routes) | **External (not AdCP)** — Category 2 | Auth is `X-API-Key`. GAM-specific operations (full/inventory/targeting/selective sync). Note: the `/api/sync` prefix is a **duplicate registration** of the same blueprint at `src/admin/app.py:375` (same handlers, backward-compat mount). |
| `src/adapters/gam_reporting_api.py` (`/api/tenant/<tid>/gam/reporting*`, 6 routes) | **Internal** — Category 1 (session-authed) | Uses admin session cookie for auth; therefore only callable from the admin UI. ⚠️ **Plan currently names it in Category 2 but omits its path from `_LEGACY_PATH_PREFIXES`.** Reclassify as Category 1. |

### AdCP spec artifacts in the repo

- `schemas/v1/` — 314 cached AdCP JSON schemas with `.meta` sidecars (`downloaded_at: 2026-03-13`). **Read-only cache**, not source of truth.
- `schemas/v1/index.json` — cached AdCP schema registry (`"adcp_version": "latest"`, `"lastUpdated": "2026-03-29"`)
- `pyproject.toml:10` — `adcp>=3.10.0` library pin. This is the canonical version pin.
- Contract tests:
  - `tests/unit/test_adcp_contract.py` (validates schemas match library)
  - `tests/unit/test_adcp_schema_compatibility.py`
  - `tests/unit/test_adcp_25_creative_management.py`
  - `tests/unit/test_adcp_36_schema_upgrade.py`
  - `tests/unit/test_adcp_exceptions.py`
  - `tests/unit/test_adcp_json_serialization.py`
  - `tests/e2e/adcp_schema_validator.py`
  - `tests/e2e/test_a2a_protocol_compliance.py`
- **No separate OpenAPI YAML file** — the contract is the FastAPI-generated `/openapi.json` pinned by `tests/unit/test_openapi_surface.py`

**Verified:** the `adcp` library pin in `pyproject.toml` is the single source of truth. No `.adcp-version` file or separate spec artifact. Migration does not need to bump or modify this pin.

---

## 2. Error Shape Classification Verification

### Category 1 — internal admin UI AJAX (native `{"detail": "..."}`)

Safe to switch to FastAPI-native shape. Our admin UI JS is the only consumer; we update both in the same PR.

Confirmed in-scope:
- `change_account_status` at `/admin/tenant/<tid>/accounts/<aid>/status` — admin UI only (verified via `src/admin/blueprints/accounts.py:161`, session-cookie authed)
- `src/admin/blueprints/api.py` — admin dashboard AJAX (7 routes)
- `src/admin/blueprints/format_search.py` — format picker (4 routes)
- `src/adapters/gam_reporting_api.py` — session-authed, admin-UI-only despite its path prefix

### Category 2 — external non-AdCP JSON APIs (preserve legacy `{"success": false, "error": "..."}`)

Safe to preserve because these are **external but NOT AdCP** — they have non-AdCP auth (`X-Tenant-Management-API-Key`, `X-API-Key`) and are called by internal provisioning scripts / GAM sync tools.

- `src/admin/tenant_management_api.py` at `/api/v1/tenant-management` (6 routes, not 19 — plan has stale count)
- `src/admin/sync_api.py` at `/api/v1/sync` AND `/api/sync` (same handlers, duplicate mount)

**Key finding:** None of the Category-2 prefixes are part of the AdCP spec. Changing them would NOT require an AdCP spec update — the legacy `{"success": false, "error": "..."}` shape is an **internal-legacy** format maintained for backward compatibility with non-AdCP operational tooling.

### Scoped exception handler path prefix verification

The plan's proposed `_LEGACY_PATH_PREFIXES` tuple:
```python
_LEGACY_PATH_PREFIXES = (
    "/api/v1/tenant-management",
    "/api/v1/sync",
    "/api/sync",
)
```

✅ **Safe:** these prefixes do NOT overlap with AdCP REST tool paths (`/api/v1/products`, `/api/v1/media-buys`, `/api/v1/accounts`, etc.). `startswith()` matching means `/api/v1/products` will never match `/api/v1/tenant-management` or `/api/v1/sync`. The legacy-shape handler will only fire for the three internal endpoints, and AdCP REST routes will continue to emit native `{"detail": "..."}` errors.

⚠️ **Missing:** `gam_reporting_api.py` at `/api/tenant/<tid>/gam/reporting*` is named in plan §2.8 as a Category-2 endpoint but the tuple doesn't include its prefix. **Decision:** since `gam_reporting_api` uses admin session cookies (verified), it's only callable from the admin UI and should be **reclassified as Category 1**. No prefix addition needed.

---

## 3. OpenAPI Surface Impact

### Verified facts

- **`tests/unit/test_openapi_surface.py` uses inclusion-only assertions** — adding ~232 admin routes will NOT break any assertion
- **Current OpenAPI surface** (introspected live via `uv run python`): 31 total routes, 27 in schema. Includes:
  - 12 AdCP REST routes (`/api/v1/*`)
  - A2A routes (`/a2a`, `/.well-known/agent-card.json`, etc.)
  - Landing (`/`, `/landing`)
  - Health + debug (`/health`, `/_internal/*`, `/debug/*`)
  - MCP sub-app at `/mcp` (sub-app mount; not expanded in OpenAPI)
- **No published AdCP OpenAPI artifact** — `/openapi.json` is purely Swagger UI fuel. No external consumer reads it as a contract. No committed YAML spec file.
- **Pre-existing pollution:** `/debug/*` and `/_internal/reset-db-pool` are currently in the OpenAPI surface (unrelated to this migration but worth flagging as cleanup)

### Recommended mitigation — `include_in_schema=False` on the admin router

One-line fix in `build_admin_router()`:

```python
# src/admin/app_factory.py
def build_admin_router() -> APIRouter:
    router = APIRouter(include_in_schema=False)  # ← ADD THIS
    router.include_router(public_router)
    router.include_router(auth_router)
    # ... etc
    return router
```

All ~232 admin routes become invisible in `/openapi.json` and `/docs`, fully functional otherwise. Zero per-route annotation. Keeps `/openapi.json` equal to the AdCP REST surface.

### Optional guard test

Add to `tests/unit/test_openapi_surface.py`:
```python
def test_no_admin_paths_in_openapi():
    schema = app.openapi()
    assert not any(p.startswith("/admin") for p in schema["paths"]), \
        "Admin routes leaked into OpenAPI — use APIRouter(include_in_schema=False)"
```

---

## 4. Middleware Verification

### Body-read interaction — CSRFMiddleware vs RestCompatMiddleware

**Verified from the actual `src/routes/rest_compat_middleware.py` source:**
- `RestCompatMiddleware` is a `BaseHTTPMiddleware` subclass (line 29)
- It returns immediately on `request.method != "POST" or not request.url.path.startswith("/api/v1/")` (line 38)
- Also returns on non-JSON content type (line 47)
- When it modifies the body, it writes `request._body = normalized_bytes` (line 61) — safe because `BaseHTTPMiddleware`'s replay mechanism hands the replayed stream to downstream handlers

**Trace for `POST /api/v1/products`:**
1. `CORSMiddleware` — headers only ✅
2. `SessionMiddleware` — pure scope manipulation, no body read ✅
3. `CSRFMiddleware` — `/api/v1/` is exempt, short-circuits before body read ✅
4. `ApproximatedExternalDomainMiddleware` — headers only ✅
5. `RestCompatMiddleware` — reads+rewrites JSON body (safe via BaseHTTPMiddleware replay) ✅
6. `UnifiedAuthMiddleware` — headers only ✅
7. Handler — FastAPI re-reads replayed body ✅

**Verdict:** CLEAR, provided `CSRFMiddleware` is implemented with the exempt-prefix check BEFORE any `await receive()` call. The foundation modules companion file enforces this.

### SessionMiddleware side effects on AdCP paths

**Verified from Starlette 0.50.0 `SessionMiddleware` source** (`starlette/middleware/sessions.py`):
- Cookie read cost: O(dict-lookup) when no session cookie present
- Attacker-forged cookie: `BadSignature` silently sets `scope["session"] = {}` — no error leaks
- `Set-Cookie` on response: **only emitted when `scope["session"]` is non-empty** (lines 57-82)
- AdCP handlers never write to `request.session` → `scope["session"]` stays `{}` → **no `Set-Cookie` leaks on AdCP responses**

**Verdict:** CLEAR. SessionMiddleware is safe to run for every request including `/api/v1/*`, `/mcp`, `/a2a`.

### MCP sub-app middleware inheritance

**Verified from Starlette 0.50.0 `Mount.matches()` implementation:**
- Parent FastAPI `app.add_middleware()` wraps the entire `app.__call__`
- Every incoming ASGI scope traverses the FastAPI middleware stack before routing reaches the `Mount` that forwards to `mcp_app`
- `UnifiedAuthMiddleware` continues to populate `scope["state"]["auth_context"]` for MCP sub-app consumption

**Verdict:** CLEAR. Sub-apps inherit parent middleware.

### CSRF exempt list completeness

Checked the full registered route table:

| Path | Methods | Exempt? | Notes |
|---|---|---|---|
| `/mcp` (mount) | ANY | ✅ YES (`/mcp` prefix) | |
| `/a2a`, `/a2a/` | POST | ✅ YES (`/a2a` prefix) | |
| `/.well-known/agent-card.json`, `/agent.json` | GET | ✅ N/A (safe method) | |
| `/api/v1/*` | POST/PUT | ✅ YES (`/api/v1/` prefix) | |
| `/health`, `/health/config` | GET | ✅ N/A | |
| `/debug/*` | GET | ✅ N/A | |
| **`/_internal/reset-db-pool`** | **POST** | **⚠️ NO** | Integration tests POST to this endpoint to reset DB pools. Gated by `ADCP_TESTING=true` env. |
| `/openapi.json`, `/docs`, `/redoc` | GET | ✅ N/A | |
| `/`, `/landing` | GET | ✅ N/A | |

**Action required:** Add `/_internal/` to `_EXEMPT_PATH_PREFIXES` in `src/admin/csrf.py`:

```python
_EXEMPT_PATH_PREFIXES = (
    "/mcp", "/a2a", "/api/v1/", "/_internal/",   # ← ADD /_internal/
    "/admin/auth/callback", "/admin/auth/oidc/",
)
```

### ApproximatedExternalDomainMiddleware path gating — NEAR-BLOCKER

**Source of truth:** `src/admin/app.py:211-269` (`redirect_external_domain_admin` Flask handler).

**Critical invariant:** the Flask version is hard-gated on `is_admin_request` (line 226-230). If the path isn't `/admin*`, it returns `None` immediately and performs **no tenant lookup, no redirect.** This gating is **non-negotiable** for AdCP routes — without it, a proxy forwarding an `Apx-Incoming-Host` header to `/mcp`, `/a2a`, or `/api/v1/*` would 302 the AdCP client to a browser URL and break the call.

**Required action in the FastAPI port:**
```python
# src/admin/middleware/external_domain.py
class ApproximatedExternalDomainMiddleware:
    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        # ⚠️ CRITICAL: path gate — preserves src/admin/app.py:226-230 invariant
        path = scope.get("path", "")
        if not path.startswith("/admin"):
            return await self.app(scope, receive, send)

        # ... rest of the external-domain redirect logic
```

**Guard test:** `test_architecture_approximated_middleware_path_gated.py` — structural test asserting that the middleware short-circuits on any path not starting with `/admin`.

**If this gate is dropped → BLOCKER:** AdCP calls with any `Apx-Incoming-Host` header would get 302-redirected. The guard test must land in Wave 1 alongside the middleware port.

---

## 5. Structural Guard Compatibility

Scanned all 21 files matching `tests/unit/test_architecture_*.py` + `test_impl_resolved_identity.py`, `test_no_toolerror_in_impl.py`, `test_transport_agnostic_impl.py`.

### Guards scoped to `src/core/tools/` only — CLEAR for admin

- `test_architecture_boundary_completeness.py` — explicit `IMPL_REGISTRY` of 13 `_impl` functions
- `test_architecture_no_model_dump_in_impl.py` — `TOOLS_DIR = src/core/tools`
- `test_architecture_query_type_safety.py` — explicit `QUERY_FILES` list
- `test_impl_resolved_identity.py` — hardcoded list of `_impl` imports
- `test_no_toolerror_in_impl.py` — `SIMPLE_MODULE_FILES` explicit list
- `test_transport_agnostic_impl.py` — `TOOLS_DIR = src/core/tools` with function-name-ends-with-`_impl` matching

**Verdict:** Admin route handlers are NOT `_impl` functions and live outside `src/core/tools/`. Zero impact.

### Guards scoped to schemas/adapters/migrations — CLEAR for admin

- `test_architecture_schema_inheritance.py` — `src/core/schemas` scope; admin doesn't touch
- `test_architecture_migration_completeness.py` — `alembic/versions/*.py` only
- `test_architecture_single_migration_head.py` — alembic graph only
- `test_architecture_no_raw_media_package_select.py` — explicit `MEDIA_PACKAGE_MODELS` set
- `test_architecture_workflow_tenant_isolation.py` — `WORKFLOW_REPO_FILE` only

### Guards scanning all `src/**.py` — POTENTIAL IMPACT

- **`test_architecture_no_raw_select.py`** — scans `src_dir.rglob("*.py")`. Existing Flask admin blueprints are allowlisted function-by-function. **New FastAPI admin files MUST use repository classes** — no raw `select(OrmModel)`. The allowlist is "shrink-only" per CLAUDE.md. **Action:** all admin handlers use repositories; document as a Wave 2 design constraint.
- `test_architecture_repository_pattern.py` — Invariant 1 scans explicit `IMPL_FILES` list (admin not included except `src/admin/blueprints/creatives.py`); Invariant 2 scans integration tests. No impact on new admin FastAPI files.

### BDD guards — CLEAR

All `test_architecture_bdd_*` (7 files) scoped to `tests/bdd/steps/**` and `docs/test-obligations/**`. Admin scenarios are excluded from cross-transport parametrization via `_ADMIN_TAG_PREFIX = "T-ADMIN-"` — no impact.

### Recommended new guards (beyond the two already in the plan)

1. **`tests/unit/test_architecture_no_flask_imports.py`** — already planned. Ratchets per wave.
2. **`tests/admin/test_templates_url_for_resolves.py`** — already planned. Validates template `url_for` names against live route registry.
3. **`tests/unit/test_architecture_csrf_exempt_covers_adcp.py`** — **NEW.** Runtime-introspects `app.routes`, finds every non-GET route whose path matches `/mcp`, `/a2a`, `/api/v1/`, or `/a2a/`, asserts each is covered by `CSRFMiddleware._EXEMPT_PATH_PREFIXES`. Catches regressions where someone adds a new AdCP POST route.
4. **`tests/unit/test_architecture_approximated_middleware_path_gated.py`** — **NEW.** Asserts `ApproximatedExternalDomainMiddleware` short-circuits on any path not starting with `/admin`. Prevents the near-blocker from §4.
5. **`tests/unit/test_architecture_admin_routes_excluded_from_openapi.py`** — **NEW.** Asserts `not any(p.startswith("/admin") for p in app.openapi()["paths"])`. Guards the `include_in_schema=False` invariant.
6. *(Optional, lower-ROI)* `tests/unit/test_architecture_admin_no_raw_select.py` — scans `src/admin/routers/*.py` for raw `select(OrmModel)` calls (enforcing "use repositories"). Duplicates part of `test_architecture_no_raw_select.py` but scoped narrowly so it's easier to reason about.

---

## 6. Webhook Payload Preservation — YELLOW

**Finding:** `src/admin/blueprints/creatives.py` and `src/admin/blueprints/operations.py` import AdCP library types directly and use them to construct **outbound webhooks** to AdCP callers:

- `src/admin/blueprints/creatives.py:13-16`:
  ```python
  from adcp import create_a2a_webhook_payload, create_mcp_webhook_payload
  from adcp.types import CreativeAction, McpWebhookPayload, SyncCreativeResult, SyncCreativesSuccessResponse
  from adcp.types.generated_poc.core.context import ContextObject
  from adcp.webhooks import GeneratedTaskStatus
  ```
- `src/admin/blueprints/operations.py:6-8`:
  ```python
  from adcp import create_a2a_webhook_payload, create_mcp_webhook_payload
  from adcp.types import CreateMediaBuySuccessResponse, Package
  from adcp.types import GeneratedTaskStatus as AdcpTaskStatus
  ```

These are webhook producers: when a human-in-the-loop approval completes in the admin UI, the handler POSTs an AdCP-shaped webhook payload to an external AdCP caller URL. **The shape of the webhook payload is AdCP-governed.**

**Risk:** if the FastAPI port uses these AdCP library types as `response_model=` on an admin-UI-facing endpoint, it would conflate admin-AJAX responses with AdCP webhook shapes. A developer reading the code might assume the admin endpoint IS an AdCP endpoint.

**Required action:**
- Keep `from adcp.*` imports scoped to webhook-payload construction functions (`create_a2a_webhook_payload`, `create_mcp_webhook_payload`)
- **Do NOT use `SyncCreativesSuccessResponse`, `McpWebhookPayload`, `CreateMediaBuySuccessResponse`, or any other `adcp.types.*` as `response_model` on admin FastAPI routes**
- The outbound webhook POST code path is unaffected by the Flask → FastAPI migration (webhooks are outbound HTTP calls, not response objects), so no functional change is needed
- Add a code review checklist item to Wave 2: "Verify admin routes don't expose AdCP types as `response_model`"

**Verdict:** YELLOW — not a blocker, worth a manual review during Wave 2.

---

## 7. Action Items Summary

Prioritized by risk:

### 🚨 NEAR-BLOCKER (must fix before Wave 1)

1. **Preserve `ApproximatedExternalDomainMiddleware` path gate.** The ASGI port must short-circuit to pass-through on any path not starting with `/admin`. Add guard test `test_architecture_approximated_middleware_path_gated.py` to Wave 1 acceptance criteria.

### ⚠️ RISK (must fix before Wave 2)

2. **Fix stale route count in plan:** `tenant_management_api.py` is **6 routes, not 19**. Update §3.2 of the main overview.
3. **Reclassify `gam_reporting_api.py` as Category 1** (session-cookie authed = admin-UI-only). Update the plan's Category 2 list to exclude it, and update the scoped exception handler comment.
4. **Flag `src/admin/blueprints/schemas.py` as externally consumed.** Add a Wave 2 acceptance criterion: "`/schemas/adcp/v2.4/*` URLs, JSON shapes, and `$id` fields preserved byte-for-byte." Add a contract test `tests/integration/test_schemas_discovery_external_contract.py` that hits `/schemas/adcp/v2.4/index.json` and asserts the payload shape.
5. **Add `/_internal/` to CSRF exempt list** in `src/admin/csrf.py::_EXEMPT_PATH_PREFIXES`. Update the foundation modules companion doc.
6. **Add `include_in_schema=False`** to the admin router in `build_admin_router()`. One-line change.

### 🟡 YELLOW (worth a manual review during Wave 2)

7. **Webhook payload preservation:** ensure `creatives.py` and `operations.py` keep their `adcp.types.*` imports scoped to outbound webhook construction, not `response_model=`. Add to Wave 2 code-review checklist.

### ✅ ACTION (new structural guards to add)

8. **Add three new structural guards** (beyond the two already planned):
   - `tests/unit/test_architecture_csrf_exempt_covers_adcp.py` — Wave 1
   - `tests/unit/test_architecture_approximated_middleware_path_gated.py` — Wave 1
   - `tests/unit/test_architecture_admin_routes_excluded_from_openapi.py` — Wave 1

---

## 8. Summary Table

| Concern | Status | Required action |
|---|---|---|
| AdCP REST routes (`src/routes/api_v1.py`) untouched | ✅ CLEAR | None — explicitly out of migration scope |
| AdCP MCP tool catalog (`src/core/main.py`) untouched | ✅ CLEAR | None — out of scope |
| AdCP A2A server (`src/a2a_server/*`) untouched | ✅ CLEAR | None — out of scope |
| Category-1 error-shape change safe for admin AJAX | ✅ CLEAR | None — `change_account_status` has no AdCP overlap with `sync_accounts` |
| Category-2 prefixes correctly identify external non-AdCP tooling | ✅ CLEAR | None — but see #2, #3 below for inline fixes |
| `tenant_management_api.py` route count in plan | ⚠️ RISK | Fix stale count 19→6 |
| `gam_reporting_api` classification | ⚠️ RISK | Reclassify as Category 1 (session-authed) |
| `schemas.py` external consumer contract | ⚠️ RISK | Add Wave 2 contract test before porting |
| Webhook payload preservation (`creatives.py`, `operations.py`) | 🟡 YELLOW | Manual review during Wave 2; keep AdCP types scoped to webhook construction |
| OpenAPI surface pollution | ✅ CLEAR (with action) | Set `include_in_schema=False` on admin router |
| `test_openapi_surface.py` breakage | ✅ CLEAR | Inclusion-only assertions — safe |
| Middleware body-read interaction with `RestCompatMiddleware` | ✅ CLEAR | CSRF exempt list prevents interference |
| `SessionMiddleware` leakage onto AdCP responses | ✅ CLEAR | Verified: no `Set-Cookie` emitted when `scope["session"]` empty |
| Sub-app middleware inheritance (`/mcp`) | ✅ CLEAR | Verified from Starlette source |
| CSRF exempt list completeness | ⚠️ RISK | Add `/_internal/` prefix |
| `ApproximatedExternalDomainMiddleware` path gating | 🚨 NEAR-BLOCKER | Preserve `/admin`-prefix gate in ASGI port + guard test |
| Existing structural guards (21 files) | ✅ CLEAR | Admin handlers outside `_impl`-scoped guards |
| `test_architecture_no_raw_select.py` impact | ⚠️ DESIGN CONSTRAINT | New admin code must use repositories, no raw `select(OrmModel)` |
| New structural guards needed | ✅ ACTION | Add 3 new guards (CSRF exempt, approximated path gating, admin OpenAPI exclusion) |
| Schema inheritance invariant (CLAUDE.md Pattern #1) | ✅ CLEAR | Admin blueprints only consume schemas, never extend |
| AdCP spec version pinning | ✅ CLEAR | `adcp>=3.10.0` in `pyproject.toml`, no changes needed |

---

## 9. What this audit DID NOT cover

For transparency:

- **Runtime behavior diffs in category-1 endpoints:** the audit verified the endpoints are admin-only, but did not exercise every endpoint against the Flask and FastAPI implementations to confirm bit-identical response bodies on non-error paths. Recommend a Wave 2 smoke-test suite that hits each migrated endpoint in staging and diffs responses against the Flask baseline.

- **Adapter-level GAM payload shape:** `gam_reporting_api.py` returns GAM-specific data. The audit confirmed it's session-authed, but did not verify the exact response shape. If downstream consumers of GAM reporting exist (BI dashboards? publisher tooling?), they need a shape check.

- **External consumer discovery for `schemas.py`:** the audit identified that `/schemas/adcp/v2.4/*` is externally consumed, but did not enumerate the actual consumers. Recommend a 48-hour shadow-trace of `/schemas/adcp/v2.4/*` access logs to identify all external clients before Wave 2.

- **Playwright end-to-end OAuth flow:** the audit trusted the Authlib Starlette client documentation. A Wave 1 staging smoke test should exercise the full Google OAuth flow against the new `auth.py` router before any traffic cutover.

- **Benchmark of async SQLAlchemy vs pre-migration sync baseline on hot admin routes:** covered in the execution-details doc but not runtime-verified yet. Recommend benchmarking a read-heavy listing route and a write-heavy form route in Wave 1 (pre-async-conversion) and again in Wave 4 (post-async-conversion) to quantify latency profile change. Acceptable range is net-neutral to ~5% improvement; significantly worse is a signal that `pool_size` tuning is needed (Risk #6 in `async-pivot-checkpoint.md` §4). Original "`run_in_threadpool` overhead benchmark" framing is stale under the full-async pivot.

### Non-code surface AdCP impact (Agent F confirmation, pivoted 2026-04-11)

All 105 action items in Agent F's non-code surface inventory (`async-audit/agent-f-nonsurface-inventory.md`) have been verified AdCP-safe:

| Surface | AdCP impact |
|---|---|
| Dep swap (psycopg2 → asyncpg) | NONE — wire format unchanged |
| Pre-commit hooks + structural guards | NONE — dev-only |
| CI workflows + tox envs | NONE — testing only |
| Docker Dockerfile / compose | NONE — runtime env unchanged |
| Deployment entrypoints | NONE — health check paths unchanged |
| New `/health/pool` + `/health/schedulers` endpoints | ADDITIVE — new paths, no existing path changes |
| `/metrics` endpoint (new) | ADDITIVE — new path, no existing path changes |
| DB pool Prometheus metrics | NONE — operational telemetry only |
| `contextvars` request-ID | NONE — internal propagation, log field only |
| `DATABASE_URL` rewriter | INTERNAL — rewrites at engine construction, env var unchanged |
| CLAUDE.md / docs updates | NONE |
| Alembic env.py async rewrite | NONE — wire format stable under sync or async migration |
| New env vars (`DB_POOL_SIZE`, `DB_POOL_MAX_OVERFLOW`, etc.) | ADDITIVE — defaults preserve current behavior |
| Nginx tuning (worker_connections, proxy timeouts) | NONE — proxy behavior identical |

**Verdict:** no non-code surface change touches AdCP protocol wire format. Side-effect of `/metrics` and `/health/pool` being additive endpoints means OpenAPI spec gains entries but does not remove or alter existing ones. AdCP contract preserved.

---

## 10. Architectural Constraints Preserved (do NOT change these in a future refactor)

The v2.0 migration preserves two non-obvious architectural facts that are load-bearing for AdCP protocol correctness. They are documented here so a hypothetical future Wave-N refactor doesn't accidentally "improve" them and silently break AdCP handlers.

### 10.1 A2A routes are grafted onto the root app, NOT mounted as a sub-app

**File:** `src/app.py:118-123` calls `a2a_app.add_routes_to_app(app, ...)`. This injects the SDK's Starlette `Route` objects directly into `app.router.routes` at the top level. It is **not** `app.mount("/a2a", a2a_starlette_app)`.

**Why this is load-bearing for AdCP protocol correctness:**
- A2A handlers sit inside the same ASGI scope as FastAPI routes, so `UnifiedAuthMiddleware` (`src/core/auth_middleware.py:23`) populates `scope["state"]["auth_context"]` for them. A sub-app mount would have an isolated middleware stack and lose this propagation.
- `RestCompatMiddleware` (`src/routes/rest_compat_middleware.py:29`) only fires for `/api/v1/*` and does not touch A2A — but if A2A were mounted, any future cross-transport body rewrite in `RestCompatMiddleware` would silently skip the A2A path.
- `_replace_routes()` at `src/app.py:192-215` walks `app.routes` to find the SDK's static agent-card paths and swaps them for dynamic header-reading versions. **It depends on the A2A routes being visible at the top level of `app.routes`.** A sub-app mount would hide the SDK's routes inside a `Mount.app` attribute and break the swap — agent cards would silently return hard-coded default URLs instead of tenant-aware URLs.
- The `AdCPCallContextBuilder` at `src/a2a_server/context_builder.py` reads `request.state.auth_context` set by `UnifiedAuthMiddleware`. A sub-app mount would break this read.

**What a future refactor must NOT do:**
- Change `a2a_app.add_routes_to_app(app, ...)` to `app.mount("/a2a", a2a_app.build_starlette_app())` or `app.include_router(...)` without simultaneously reimplementing middleware propagation, `_replace_routes()`, and the context-builder handshake.

**Guard (proposed for Wave 0 as part of deep-audit §3.8 action items):** `tests/unit/test_architecture_a2a_routes_grafted.py` — asserts that `/a2a`, `/.well-known/agent-card.json`, `/agent.json` all appear as top-level `Route` objects in `app.routes` (NOT nested inside a `Mount`). Walks `app.routes`, filters `Mount` objects, looks up the three paths by exact match.

### 10.2 MCP scheduler lifespan is chained via `combine_lifespans`

**File:** `src/app.py:68` — `lifespan=combine_lifespans(app_lifespan, mcp_app.lifespan)`. The FastMCP lifespan (`lifespan_context` at `src/core/main.py:82-103`) starts `delivery_webhook_scheduler` and `media_buy_status_scheduler`. These only run because `combine_lifespans` yields through both.

**Note (2026-04-11 pivot):** Under the full-async SQLAlchemy pivot, scheduler bodies' DB access becomes `async with get_db_session() as session:` / `await session.execute(...)`. No structural change to the lifespan composition itself — schedulers are already running inside an async context via `asyncio.create_task(...)`. Only the DB call-sites inside the scheduler loops change.

**Why this is load-bearing for AdCP protocol correctness:**
- `delivery_webhook_scheduler` is what fires outbound AdCP webhooks to subscribers (creative approvals, media buy state changes, etc.) via `create_a2a_webhook_payload` / `create_mcp_webhook_payload`. If it stops, every human-in-the-loop approval silently stops notifying AdCP callers.
- `media_buy_status_scheduler` polls adapter status and updates the database. If it stops, `get_media_buy_delivery` MCP/REST tool calls return stale `pending` status forever.

**What a future refactor must NOT do:**
- Drop the `/mcp` mount → schedulers stop.
- Replace `combine_lifespans(app_lifespan, mcp_app.lifespan)` with `lifespan=app_lifespan` alone → schedulers stop.
- Move schedulers out of `lifespan_context` into `app_lifespan` without also verifying they're reached by uvicorn's lifespan protocol → may stop.
- Set `workers > 1` in uvicorn → schedulers start N× per tick (loud failure, separately tracked in deep-audit §3.1).

**Guards (proposed for Wave 0):**
1. `tests/unit/test_architecture_scheduler_lifespan_composition.py` — parses `src/app.py`, asserts the `FastAPI(...)` constructor's `lifespan=` kwarg literally contains `combine_lifespans(app_lifespan, mcp_app.lifespan)`.
2. Startup log assertion: at first scheduler tick, emit `"delivery_webhook_scheduler alive"` / `"media_buy_status_scheduler alive"` so missing log lines in production dashboards surface the failure within 60 seconds.

### Summary: these are Wave-0 structural guards, NOT Wave-3 cleanup

Both guards land in Wave 0 alongside the other migration guards. They cost ~40 lines of Python each and prevent a whole class of silent-failure refactors in v2.1+ work. Added to the plan via `flask-to-fastapi-migration.md` §4.8 "Apps loaded at runtime inventory" and `flask-to-fastapi-deep-audit.md` §3.7 + §3.8.

---

## Appendix A: Verification methodology

Produced by three parallel Opus Explore subagents on 2026-04-11:

1. **Agent 1 — AdCP boundary** — read every candidate file, searched for `apply_version_compat` / `adcp_version` / `from adcp.*` across `src/admin/`, verified classification against `schemas/v1/index.json`, enumerated external consumers of `/schemas/adcp/v2.4/*`, checked webhook payload construction paths
2. **Agent 2 — OpenAPI surface** — read `tests/unit/test_openapi_surface.py`, introspected the live FastAPI app via `uv run python -c "from src.app import app; ..."`, compared recommended mitigation options, searched for any committed OpenAPI artifacts
3. **Agent 3 — Middleware + structural guards** — read `src/routes/rest_compat_middleware.py` source, read Starlette 0.50.0 `SessionMiddleware` source from `.venv`, traced body-read interactions for `POST /api/v1/products`, audited all 21 `test_architecture_*.py` files for scope and allowlist gating, introspected runtime routes for CSRF exempt list completeness

All three agents' full reports are preserved in the conversation context. This document is the synthesized action-item summary.
