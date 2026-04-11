# Flask → FastAPI Migration: Complete Research & Design Reference

**Date:** 2026-04-11
**Status:** Design phase (not yet implemented)
**Release target:** salesagent v2.0.0 (major version, breaking changes allowed)
**Related plan file:** `/Users/quantum/.claude/plans/squishy-meandering-marshmallow.md`

> **How to use this document:** This is a self-contained research and design reference. A reader can start at the top with zero prior knowledge of the salesagent codebase and understand the full scope, trade-offs, assumptions, and decisions behind the Flask → FastAPI migration. All findings come from multiple rounds of codebase exploration (three Opus Explore subagents) and design (seven Opus Plan subagents in total) run on 2026-04-11, plus web research of current FastAPI patterns as of April 2026.

## Companion Documents

This overview covers every section at medium depth. Three companion files contain deep-dive elaborations with full implementations, real code translations, and specific execution details. Read the overview first; drop into a companion when you need implementation-level detail:

- **[flask-to-fastapi-foundation-modules.md](flask-to-fastapi-foundation-modules.md)** (~2,500 lines) — §11 deep dive. Full working implementations of all 11 foundation modules (`templating.py`, `sessions.py`, `flash.py`, `deps/auth.py`, `deps/tenant.py`, `deps/audit.py`, `oauth.py`, `csrf.py`, `middleware/external_domain.py`, `middleware/fly_headers.py`, `app_factory.py`) with their tests, integration notes, and known gotchas. Includes the full `is_super_admin()` four-tier fallback, the `_read_csrf_from_body` ASGI receive-channel re-injection pattern, and the per-tenant OIDC client cache with concurrency lock.

- **[flask-to-fastapi-worked-examples.md](flask-to-fastapi-worked-examples.md)** (~2,800 lines) — §13 deep dive. Five hard-case route translations with the real Flask source read from disk and faithful FastAPI translations: (1) Google OAuth login + callback flow from `src/admin/blueprints/auth.py`, (2) per-tenant OIDC dynamic client registration from `src/admin/blueprints/oidc.py`, (3) tenant favicon file upload from `src/admin/blueprints/tenants.py`, (4) SSE activity stream from `src/admin/blueprints/activity_stream.py`, (5) complex product-creation form from `src/admin/blueprints/products.py`. Each includes Flask source, FastAPI translation, change-by-change labels, edge cases, and integration test patterns.

- **[flask-to-fastapi-execution-details.md](flask-to-fastapi-execution-details.md)** (~1,150 lines) — §14 / §16 / §21 deep dive. Three parts: (1) per-wave execution details with acceptance criteria, file-level checklists, risk assessment tables, rollback procedures, merge-conflict resolution, time estimates, and entry/exit criteria for all 4 waves; (2) full verification recipes for all 28 assumptions, grouped by confidence with how/when/fallback for each; (3) concrete structural guard tests with AST scan patterns, 5 integration test templates, Playwright e2e test plan, benchmark harness for `run_in_threadpool` overhead, and the `scripts/check_coverage_parity.py` automation.

- **[flask-to-fastapi-adcp-safety.md](flask-to-fastapi-adcp-safety.md)** (~620 lines) — pre-implementation audit verifying the migration does NOT touch AdCP-protocol surfaces and does NOT require updates from external AdCP consumers. Contains: classification of every file as AdCP-protocol vs internal, scoped exception handler verification, OpenAPI surface impact analysis, middleware body-read interaction traces, `SessionMiddleware` `Set-Cookie` leak verification from Starlette 0.50 source, CSRF exempt list completeness check, `ApproximatedExternalDomainMiddleware` path-gating invariant (near-blocker if dropped), all 21 existing structural guards compatibility analysis, webhook payload preservation concerns for `creatives.py`/`operations.py`, and 8 prioritized action items that MUST land in the plan before implementation.

- **[flask-to-fastapi-deep-audit.md](flask-to-fastapi-deep-audit.md)** (~1,500 lines) — deep 2nd/3rd-order audit that surfaced **six previously unseen BLOCKERS**, twenty new RISKS, and forty-plus cleanup OPPORTUNITIES the first-order audit missed. Contains: detailed elaboration of the path-gating near-blocker with threat model, implicit Flask invariant audit (147 `script_root` references, 111 trailing-slash-dependent `url_for` calls, `@app.exception_handler(AdCPError)` HTML regression, session scoping on async event loop, middleware ordering bug — CSRF must run AFTER Approximated), shared infrastructure interaction analysis (scheduler singletons, SSE rate limits, `app.dependency_overrides` leakage), plan revisions required (flip admin default from `async def` to `def`, swap middleware order, add 9 new structural guards), and derivative opportunities enabled by Flask removal (drop nginx ~30MB, ratchet REST to Annotated, consolidate guards). **Every design decision in this overview should be cross-checked against the deep audit before implementation begins.**

**When to use which document:**
- Planning a wave? Start here (overview) then read the execution-details doc for that wave's checklist
- Writing a foundation module? Read this overview's §11, then the foundation-modules doc for full implementation
- Porting a blueprint? Find the closest worked example in the worked-examples doc
- Verifying an assumption? Look it up by number in the execution-details doc, Part 2
- Auditing the migration plan? Read this overview end-to-end (~1,700 lines); consult companions only for cited references

---

## 1. Executive Summary

The salesagent repo currently runs its Flask-based admin UI **inside** FastAPI via `a2wsgi.WSGIMiddleware`. This is a transitional architecture — single ASGI process, Flask mounted at `/admin` and `/` (root catch-all) — left over from when the app was multi-process (Flask + MCP + A2A behind nginx) and was consolidated into one ASGI process.

Flask contributes:
- **~21,340 LOC** across **30 blueprints** (~232 routes)
- **72 Jinja2 templates** (`/templates/`) with 134 `url_for(...)` calls, 171 `session.*`/`request.script_root`/`g.*` references
- **5 direct and transitive dependencies**: `flask`, `flask-caching`, `flask-socketio`, `waitress`, `a2wsgi`
- **~21 test files** coupled to Flask (17 integration + 3 admin + miscellaneous)
- **Flask-specific idioms** throughout: `@require_auth` decorators, `flask.g` writes, `session` thread-local, `url_for('blueprint.endpoint')` namespacing, custom `ProxyFix`/`FlyHeaders` WSGI middlewares, `@app.before_request`/`after_request`/`context_processor` lifecycle hooks

**The goal:** rewrite the admin as if Flask never existed, using current (April 2026) FastAPI-native patterns. Major version release (v2.0.0). Breaking changes allowed. No backward-compat shims. End state must read as "written today from scratch with FastAPI only."

**Why this is worth doing:**
- Eliminates ~11,000 LOC of boilerplate by using declarative FastAPI patterns
- Removes ~80 MB from Docker image (Flask + flask-caching + flask-socketio + waitress + a2wsgi)
- Unifies auth/session/middleware across all transports (MCP/A2A/REST/admin share one stack)
- Eliminates the WSGI↔ASGI bridge overhead
- Unlocks future async SQLAlchemy migration (v2.1)
- Simplifies testing (admin tests use the same `TestClient` + `dependency_overrides` pattern as REST tests)

**Migration strategy:** 4 waves (Foundation+codemod → Foundational routers+session cutover → Bulk blueprint port → SSE+cleanup). Flask catch-all stays live through Wave 2 as a safety net.

---

## 2. User-Confirmed Decisions (8 directives)

1. **Template strategy: Option B (FastAPI-native codemod).** Templates become `url_for('flat_route_name')`, native `flash()`, `request.url_for(...)`. No backward-compat shim retained in v2.0.
2. **Breaking changes welcome** provided code reads as modern FastAPI-native (Annotated deps, Pydantic v2, lifespan context managers, Starlette middleware patterns, declarative forms).
3. **Session cookie hard cutover:** one forced re-login at deploy is acceptable.
4. **URL prefix stays `/admin/`** (bookmarks, docs, runbooks all reference it; zero benefit to moving to root).
5. **`SESSION_SECRET` env var hard-required, `KeyError` at startup, no `secrets.token_hex()` fallback.** The old dev-mode fallback was a security smell anyway.
6. **Async SQLAlchemy deferred to v2.1** (separate follow-on PR that builds on v2.0). v2.0 admin handlers wrap sync UoW in `run_in_threadpool`. See §18.
7. **CSRF: roll-your-own Double Submit Cookie (~100 LOC, `src/admin/csrf.py`).** No external dep risk, bespoke to our form-post admin UI. See §11.6.
8. **Error-shape split** (refined post AdCP safety audit):
   - **Category 1** (internal admin UI AJAX endpoints called by our own JavaScript — e.g. `change_account_status`, `src/admin/blueprints/api.py` dashboard AJAX, `src/admin/blueprints/format_search.py` format picker, **and `src/adapters/gam_reporting_api.py`** which is admin-session-authed) → native FastAPI `{"detail": "..."}`. We update our own JS in the same PR.
   - **Category 2** (external non-AdCP JSON APIs: **`tenant_management_api` and `sync_api` only** — both use non-AdCP auth headers like `X-Tenant-Management-API-Key` and `X-API-Key` for external provisioning/sync tooling) → preserved legacy `{"success": false, "error": "..."}` via a scoped exception handler (~30 LOC, path-prefix match against `/api/v1/tenant-management`, `/api/v1/sync`, `/api/sync`). None of these are part of the AdCP spec; preserving the shape is a backward-compat concession to non-AdCP internal tooling, NOT an AdCP spec requirement.

---

## 2.7. AdCP Boundary Verification (post-design audit)

**Bottom line: the migration does NOT touch any AdCP-protocol surface. No AdCP spec update is required.** Three parallel Opus audits on 2026-04-11 verified this across file classification, OpenAPI surface impact, middleware body-read interactions, structural guard compatibility, and external consumer analysis. Full findings in **[flask-to-fastapi-adcp-safety.md](flask-to-fastapi-adcp-safety.md)**.

**Eight action items were surfaced by the audit and incorporated into this plan:**

1. 🚨 **NEAR-BLOCKER — `ApproximatedExternalDomainMiddleware` must preserve path-gating invariant.** The ASGI port must short-circuit to pass-through on any path not starting with `/admin`, mirroring `src/admin/app.py:226-230`. Without this, AdCP clients carrying an `Apx-Incoming-Host` header would get 302-redirected. Guard test `test_architecture_approximated_middleware_path_gated.py` lands in Wave 1. See §11.9 in this doc.

2. ⚠️ **FIXED** — stale route count: `tenant_management_api.py` is **6 routes, not 19** (corrected in §3.2 table above).

3. ⚠️ **FIXED** — `gam_reporting_api.py` reclassified from Category 2 → **Category 1** (session-cookie authed, admin-UI-only). Updated in §2.8 above.

4. ⚠️ **FLAGGED** — `src/admin/blueprints/schemas.py` is **externally consumed** via `/schemas/adcp/v2.4/*` URLs (external AdCP validators hit these for JSON Schema resolution). Wave 2 acceptance criterion: preserve URL shape, `$id` fields, and `/schemas/adcp/v2.4/index.json` payload byte-for-byte. Add contract test `tests/integration/test_schemas_discovery_external_contract.py` before porting.

5. 🟡 **YELLOW** — `src/admin/blueprints/creatives.py` and `operations.py` construct **outbound AdCP webhook payloads** via `create_a2a_webhook_payload` / `create_mcp_webhook_payload` and import `adcp.types.*`. Wave 2 code-review checklist: keep AdCP type imports scoped to webhook construction; **do NOT use AdCP types as `response_model=`** on admin FastAPI routes (would conflate admin AJAX responses with AdCP webhook shapes).

6. ✅ **APPLIED** — `build_admin_router()` returns `APIRouter(include_in_schema=False)` so all ~232 admin routes are invisible in `/openapi.json` and `/docs`, keeping the OpenAPI surface equal to the AdCP REST contract. Updated in §10.2 above.

7. ⚠️ **FIXED** — `/_internal/` added to CSRF exempt list. `/_internal/reset-db-pool` is a POST used by integration tests to reset DB pools; without the exempt entry, CSRF would block it. Updated in §11.6 above.

8. ✅ **ADDED** — three new structural guards (beyond the two already planned):
   - `tests/unit/test_architecture_csrf_exempt_covers_adcp.py` — runtime-introspects `app.routes`, asserts every non-GET route matching `/mcp`, `/a2a`, `/api/v1/`, or `/a2a/` is covered by `CSRFMiddleware._EXEMPT_PATH_PREFIXES`
   - `tests/unit/test_architecture_approximated_middleware_path_gated.py` — asserts the Approximated middleware short-circuits on non-`/admin` paths
   - `tests/unit/test_architecture_admin_routes_excluded_from_openapi.py` — asserts `not any(p.startswith("/admin") for p in app.openapi()["paths"])`

**Verified CLEAR (no action needed):**

- ✅ AdCP REST (`src/routes/api_v1.py`), MCP (`src/core/main.py`), A2A (`src/a2a_server/*`), `_impl()` layer (`src/core/tools/*`), schemas (`src/core/schemas/*`), and `AdCPError` hierarchy are all **explicitly out of migration scope**. Verified against `tests/unit/test_openapi_surface.py` inclusion assertions, `pyproject.toml:10` `adcp>=3.10.0` pin, and CLAUDE.md Pattern #1.
- ✅ **OpenAPI surface test `test_openapi_surface.py` uses inclusion-only assertions** — adding admin routes cannot break any assertion even without `include_in_schema=False`; the flag is added as best practice, not bug fix.
- ✅ **`SessionMiddleware` is safe on AdCP paths.** Verified from Starlette 0.50.0 source: `Set-Cookie` is only emitted when `scope["session"]` is non-empty. AdCP handlers never write to `request.session`, so `/api/v1/*`, `/mcp`, `/a2a` responses never get a `Set-Cookie` header. No leak.
- ✅ **`RestCompatMiddleware` body-read is safe under the new middleware stack.** Verified via trace: CSRF's `/api/v1/` exempt prefix prevents any body-read interference on AdCP routes. `RestCompatMiddleware` is a `BaseHTTPMiddleware` subclass — its body replay mechanism is compatible with downstream FastAPI handlers.
- ✅ **Sub-app middleware inheritance works correctly.** Verified from Starlette `Mount.matches()` source: every incoming ASGI scope traverses the parent FastAPI middleware stack before routing reaches the `/mcp` Mount that forwards to `mcp_app`. `UnifiedAuthMiddleware` continues to populate `scope["state"]["auth_context"]` for MCP sub-app consumption.
- ✅ **All 21 existing architecture guards are compatible.** Guards scoped to `src/core/tools/` (8 files), schemas/adapters/migrations (5 files), BDD steps (7 files), and test metadata (1 file) do not scan admin code. Only `test_architecture_no_raw_select.py` scans `src/**.py` broadly → design constraint: **new admin routers MUST use repository classes, no raw `select(OrmModel)`**.
- ✅ **Schema inheritance invariant (CLAUDE.md Pattern #1) is preserved.** 11 admin blueprints import from `src/core/schemas` but all are **consumers** (instance construction, enum/constant reads), never extenders.

---

## 2.8. Deep Audit — Six NEW Blockers Discovered by 2nd/3rd-Order Analysis

The first-order audit (§2.7) verified AdCP protocol safety. A subsequent **deep audit using Opus subagents** surfaced **six blockers the first-order pass missed** because they concern internal migration mechanics rather than external AdCP contract. Full details in **[flask-to-fastapi-deep-audit.md](flask-to-fastapi-deep-audit.md)**. The AdCP verdict still holds — no external consumer impact — but shipping the plan without these fixes would cause **silent production breakage**.

### The six new blockers

1. 🚨 **`script_root` / `script_name` silent template breakage — 147 refs across 45 templates.** Starlette's `include_router(prefix="/admin")` does NOT set `scope["root_path"]` (verified via runtime introspection); it stays empty. Flask's WSGIMiddleware mount currently sets it to `/admin`. Templates using `{{ script_name }}/logout`, `{{ script_name }}/tenant/...`, and JavaScript `fetch({{ script_name }}/...)` would render as `/logout`, `/tenant/...`, 404ing across the admin UI. **Fix:** split `script_name` into `admin_prefix="/admin"` and `static_prefix="/static"` Jinja globals in `render()`, extend the template codemod to handle the split, add guard test `test_templates_no_script_root.py`.

2. 🚨 **Trailing-slash handling differs between Flask and Starlette.** Flask's `strict_slashes=False` default matches both `/foo` and `/foo/`; Starlette does not. 111 `url_for()` calls across 30 templates are at risk of silent 404s. **Fix:** set `APIRouter(redirect_slashes=True, include_in_schema=False)` on every admin router, add guard test `test_trailing_slash_tolerance.py`.

3. 🚨 **`@app.exception_handler(AdCPError)` returns JSON to HTML admin browsers.** Today, an admin user clicking "Create Product" that fails with `AdCPValidationError` sees a Flask error page. Post-migration, the global FastAPI handler returns `{"error_code": "...", "message": "..."}` as JSON — the browser displays a raw JSON blob. **Fix:** make the handler Accept-aware — if `request.url.path.startswith("/admin")` AND `"text/html" in accept`, render `templates/error.html`; otherwise return JSON. Requires a new `error.html` template.

4. 🚨 **Session scoping on the async event-loop thread.** `src/core/database/database_session.py:148` uses `scoped_session` with default `threading.get_ident()` scopefunc. Under Flask+a2wsgi, each request runs on its own worker thread → isolated sessions. Under the plan's `async def` admin handlers, concurrent requests share the event-loop thread → **the same `scoped_session` identity** → transaction interleaving, stale reads, duplicate commits. **Fix:** flip the plan's default from `async def` to **`def` (sync)** for admin handlers. FastAPI auto-offloads sync handlers to a threadpool; each worker thread has its own session identity. Keep `async def` ONLY for OAuth callbacks (await Authlib), SSE handlers (async generators), and outbound webhook senders (await httpx). Add structural guard `test_architecture_admin_sync_db_no_async.py` — AST-scans admin routers, flags `async def` handlers calling `get_db_session()` without `run_in_threadpool`.

5. 🚨 **Middleware ordering bug — CSRF must run AFTER Approximated, not before.** The plan's proposed order puts `CSRFMiddleware` BEFORE `ApproximatedExternalDomainMiddleware`. Failure scenario: external-domain user POSTs to `/admin/tenant/t1/accounts/create` via Approximated. CSRF fires first, user has no session cookie (different domain), CSRF fails with 403, redirect never runs. The entire external-domain onboarding flow breaks. **Fix:** swap the order — `ApproximatedExternalDomainMiddleware` runs BEFORE `CSRFMiddleware`. Corrected stack:
   ```python
   # Outermost → innermost (add_middleware is LIFO):
   CORSMiddleware
   SessionMiddleware
   ApproximatedExternalDomainMiddleware   # ← MOVED UP
   CSRFMiddleware
   RestCompatMiddleware
   UnifiedAuthMiddleware
   ```
   Also: switch the Approximated redirect from 302 to **307** (preserves POST body per RFC 7231 §6.4.7).

6. 🚨 **OAuth redirect URIs must be byte-identical to Google Cloud Console registration.** `/admin/auth/google/callback`, `/admin/auth/oidc/{tenant_id}/callback`, `/auth/gam/callback` are all pre-registered with Google. If the FastAPI port changes a single character in the path, OAuth fails with `redirect_uri_mismatch` and **login is broken in production**. **Fix:** add pre-Wave-2 guard `test_oauth_redirect_uris_immutable.py` that pins the exact path set. Add a Wave-1 staging smoke test walking the actual OAuth flow before traffic cutover.

### Plan defaults that change as a result

- **Admin handler default flips to `def` (sync)** — was `async def` in the original plan. Async is reserved for handlers that `await` external I/O (OAuth, SSE, httpx).
- **Middleware order swaps Approximated and CSRF** — Approximated runs before CSRF, not after.
- **Redirect code changes from 302 to 307** — preserves POST body on external-domain redirect.
- **`render()` wrapper uses `admin_prefix`/`static_prefix`** — not `script_root`.
- **`APIRouter` construction includes `redirect_slashes=True`** — matches Flask permissive default.
- **Admin error handler renders `error.html`** — for HTML `Accept` on `/admin/*` paths.
- **`FLASK_SECRET_KEY` transition becomes dual-read for v2.0** (supersedes user directive #5) — plan originally said hard-required rename, but `scripts/setup-dev.py`, `docker-compose.yml`, `tests/unit/test_setup_dev.py` (9 occurrences), and two docs files all reference the old name. Hard-removing breaks dev workflow and 9 tests. Dual-read `SESSION_SECRET or FLASK_SECRET_KEY` in v2.0, hard-remove in v2.1.

### Additional structural guards (9 total, up from the original plan's 2)

1. `test_architecture_no_flask_imports.py` — already planned
2. `test_templates_url_for_resolves.py` — already planned
3. `test_architecture_csrf_exempt_covers_adcp.py` — from first-order audit
4. `test_architecture_approximated_middleware_path_gated.py` — from first-order audit
5. `test_architecture_admin_routes_excluded_from_openapi.py` — from first-order audit
6. **`test_architecture_admin_sync_db_no_async.py`** — NEW from deep audit (blocker 4)
7. **`test_templates_no_script_root.py`** — NEW from deep audit (blocker 1)
8. **`test_trailing_slash_tolerance.py`** — NEW from deep audit (blocker 2)
9. **`test_oauth_redirect_uris_immutable.py`** — NEW from deep audit (blocker 6)

Plus two derivative guards:
10. `test_architecture_single_worker_invariant.py` — prevent multi-worker regression (scheduler singleton)
11. `test_architecture_harness_overrides_isolated.py` — prevent `app.dependency_overrides` leakage across test envs

### Derivative opportunities enabled by the migration (deferred to v2.1)

- **Drop nginx entirely** — the container runs nginx + uvicorn for historical reasons; Fly.io terminates TLS externally and uvicorn has proxy-header support. Dropping nginx saves ~30 MB image size, simplifies the Dockerfile, and removes one restart-loop failure mode. Not in v2.0 scope to keep the migration focused.
- **Ratchet-migrate REST routes to `Annotated[T, Depends()]` pattern** — `src/routes/api_v1.py` currently uses old-style `= resolve_auth` defaults. Inconsistency with the new admin Annotated pattern breeds confusion. 14 route signatures.
- **Drop `a2wsgi`, `werkzeug`, `waitress`, `flask-caching`, `flask-socketio`, `python-socketio`, `simple-websocket`** — Wave 3 cleanup. All seven become unreferenced after Flask removal.
- **Structured logging (structlog/logfire) swap-in** — the `log_auth_cookies` debug handler and Flask logger bootstrap obscure current logging. Post-migration, a clean `logfire` integration (already in deps) can own the full request/response pipeline.

---

## 3. Current-State Inventory: Flask Surface

All paths relative to `/Users/quantum/Documents/ComputedChaos/salesagent/`.

### 3.1 Flask app entry points

| Location | Role |
|---|---|
| `src/admin/app.py:107` | Sole `Flask(__name__)` instantiation. `create_app()` factory builds the Flask app. 427 LOC. |
| `src/app.py:299-304` | FastAPI side: `flask_admin_app = create_app()`, `admin_wsgi = WSGIMiddleware(flask_admin_app)`. This is the Flask→FastAPI bridge. |
| `src/app.py:25-45` | `_install_admin_mounts()` — mounts Flask at `/admin` AND `/` (root catch-all). Called in lifespan startup. |
| `src/admin/server.py` | Legacy standalone admin entry point. No longer primary but still importable. |

**WSGI→ASGI bridge:** `a2wsgi>=1.10.0`. Flask is mounted TWICE — once at `/admin` and once at `/` — so Flask catches anything FastAPI doesn't handle. Landing routes use `app.router.routes.insert(0, Route("/", ...))` at `src/app.py:351-352` to beat the Flask catch-all.

**`/a2a/` trailing-slash redirect** at `src/app.py:127-135` exists only to prevent the root Flask mount from eating `/a2a/`.

### 3.2 Flask blueprints — 30 total, ~21,340 LOC

All in `src/admin/blueprints/` unless noted. ~232 routes total.

**HTML UI blueprints:**

| File | URL prefix | Routes | LOC | Notes |
|---|---|---|---|---|
| `public.py` | (root) | 5 | 316 | Signup, landing — no auth |
| `core.py` | (root) | 10 | 550 | `/`, `/health`, `/static/<path>`, dashboard |
| `auth.py` | (root) | 11 | 1,097 | Google OAuth login flow (Authlib Flask) |
| `oidc.py` | `/auth/oidc` | 7 | 431 | Per-tenant dynamic OIDC clients |
| `settings.py` | various | 19 | 1,446 | Combined tenant_management_settings + settings |
| `tenants.py` | `/tenant` | 13 | 906 | Tenant CRUD + tenant shell |
| `accounts.py` | `/tenant/<tenant_id>/accounts` | 5 | 189 | Canonical small CRUD (worked example in §13) |
| `products.py` | `/tenant/<tenant_id>/products` | 7 | **2,464** | Largest single file |
| `principals.py` | `/tenant/<tenant_id>` | 13 | 759 | |
| `users.py` | `/tenant/<tenant_id>/users` | 8 | 335 | |
| `gam.py` | `/tenant/<tenant_id>/gam` | 11 | 1,169 | Google Ad Manager UI |
| `operations.py` | `/tenant/<tenant_id>` | 9 | 709 | |
| `creatives.py` | `/tenant/<tenant_id>/creatives` | 8 | 1,308 | |
| `policy.py` | `/tenant/<tenant_id>/policy` | 4 | 297 | |
| `adapters.py` | various | 7 | 307 | |
| `authorized_properties.py` | `/tenant` | 11 | 1,003 | |
| `creative_agents.py` | `/tenant/<tenant_id>/creative-agents` | 5 | 303 | |
| `signals_agents.py` | `/tenant/<tenant_id>/signals-agents` | 5 | 325 | |
| `inventory.py` | internal | 16 | 1,352 | GAM inventory browser |
| `inventory_profiles.py` | `/tenant/<tenant_id>/inventory-profiles` | 7 | 720 | |
| `publisher_partners.py` | `/tenant` | 5 | 549 | |
| `workflows.py` | `/tenant` | 4 | 295 | |

**JSON API blueprints:**

| File | URL prefix | Routes | LOC | Category |
|---|---|---|---|---|
| `api.py` | `/api` | 7 | 448 | Internal AJAX (cat 1) |
| `format_search.py` | `/api/formats` | 4 | 320 | Internal AJAX (cat 1) |
| `schemas.py` | `/schemas` | 6 | 207 | JSON Schema validation |
| `activity_stream.py` | root | 3 | 390 | SSE (EventSource) |

**Top-level admin JSON APIs (category 2 — may have external consumers):**

| File | URL prefix | Routes | LOC | Auth | Category |
|---|---|---|---|---|---|
| `src/admin/tenant_management_api.py` | `/api/v1/tenant-management` | **6** (corrected from stale "19") | 529 | `X-Tenant-Management-API-Key` | Category 2 (external non-AdCP) |
| `src/admin/sync_api.py` | `/api/v1/sync` + `/api/sync` (duplicate mount) | 9 | 699 | `X-API-Key` | Category 2 (external non-AdCP) |
| `src/adapters/gam_reporting_api.py` | `/api/tenant/<tid>/gam/reporting*` | 6 | 650 | **admin session cookie** | **Category 1** (admin-UI-only per [adcp-safety audit](flask-to-fastapi-adcp-safety.md)) |

**Dynamic/inline routes (code smell — dependency inversion violation):**

- `src/services/gam_inventory_service.py::create_inventory_endpoints(app)` — **DEAD CODE** (early `return` at line 1469 before any registration). Delete during migration.
- `src/adapters/google_ad_manager.py::register_ui_routes(app)` — adapter mutates the Flask app.
- `src/adapters/mock_ad_server.py::register_ui_routes(app)` — same pattern.
- All called from `src/admin/app.py:391-427`.

### 3.3 Flask extensions in actual use

- **`flask-caching>=2.3.0`** — `src/admin/app.py:200-208` attaches `Cache(app)`. **Audit confirmed ZERO callers of `app.cache` / `current_app.cache` in any blueprint.** Deletable.
- **`authlib.integrations.flask_client.OAuth`** — real. `src/admin/blueprints/auth.py` registers Google client; `oidc.py` rebuilds per-tenant OIDC clients per-request.
- **`werkzeug.middleware.proxy_fix.ProxyFix`** — `src/admin/app.py:11, 187`. Handles `X-Forwarded-*`.
- **`flask-socketio>=5.5.1`** — declared in pyproject.toml but **ZERO imports in `src/`**. Completely unused. Plus transitive `python-socketio`, `simple-websocket`.

**NOT USED (absent):** Flask-Login, Flask-WTF (no CSRFProtect, `csrf_token()` usage in templates is defensive guard only), Flask-Session, Flask-Babel, Flask-Mail, Flask-CORS, Flask-Migrate, Flask-SQLAlchemy.

### 3.4 Templates (72 files under `templates/`)

**Custom Jinja filters** at `src/admin/app.py:154-155`:
- `from_json` — JSON decoder filter
- `markdown` — `markdown.markdown(text, extensions=["extra", "nl2br"])` via `markupsafe.Markup`

**Context processor** at `src/admin/app.py:298-330` (`inject_context`):
- Injects `script_name`, `support_email`, `sales_agent_domain`, and `tenant` (via DB lookup from `session["tenant_id"]`) into every template

**Template reference frequencies (grep-counted):**

| Reference | Count | Files |
|---|---|---|
| `url_for(...)` | 134 | 40 |
| `request.script_root` / `session.*` / `g.*` / `csrf_token` | 171 | 45 |
| `{{ csrf_token() }}` (actual use) | 0 (only defensive guard in base.html:6) | - |

**Real references seen in `templates/base.html` (layout extended by every other template):**
- `{{ script_name }}` at L10, 12, 18, 180, 181, 183, 238 (favicon href, static href, nav links)
- `{{ csrf_token() if csrf_token else '' }}` at L6 (defensive CSRF meta tag)
- `{{ tenant.name }}` at L9, 156, 168, 170, 172, 174, 176 (header/badges)
- `{% if session.authenticated %}` at L162
- `{{ session.email }}`, `{{ session.username }}` at L164
- `{% if session.role == 'super_admin' %}` at L165, 179 (and other roles 167-173)
- `{% if g.test_mode %}` at L145 (test-mode banner)
- `{% with messages = get_flashed_messages(with_categories=true) %}` at L189-200

**Templates with the most Flask-isms (codemod audit targets):**
- `add_product_gam.html` — 15 `url_for` calls inside JS template literals (`` fetch(`{{ url_for(...) }}`) ``)
- `tenant_settings.html` — 12 `url_for` + 12 `session.X` references
- `base.html` — 3 `session.X` in header gating, 1 `g.test_mode`, 1 `csrf_token()`
- `policy_*.html` (4 files) — 13 `session.X` references

### 3.5 Session & auth

**Session storage:** Flask built-in itsdangerous signed cookies. Settings in `src/admin/app.py:115-130`:
- `SESSION_COOKIE_SECURE`, `SESSION_COOKIE_HTTPONLY`, `SESSION_COOKIE_SAMESITE`, `SESSION_COOKIE_PATH`, `SESSION_COOKIE_DOMAIN`
- `SESSION_COOKIE_HTTPONLY=False` in production — historically to let EventSource read the cookie (cargo-culted; browsers send HttpOnly cookies on EventSource automatically)

**`flask.session` usage:** 159 occurrences across 16 files under `src/admin/`. Heaviest in `auth.py` (63), `public.py` (21), `oidc.py` (12), `core.py` (8).

**`flask.g` usage:** **Only 7 occurrences in 3 files:**
- `src/admin/utils/helpers.py` — 3 write sites (`g.user = ...`) at lines 260, 271, 321
- `src/admin/utils/audit_decorator.py`
- `tests/unit/test_utils.py`

**Auth decorators** in `src/admin/utils/helpers.py`:
- `require_auth(admin_only=False)` at L251 — reads `session["user"]`, sets `g.user`, redirects to `url_for("auth.login")`
- `require_tenant_access(api_mode=False)` at L291 — 80-LOC decorator: tenant membership check, super-admin bypass, test-mode shortcut. `api_mode=True` raises 401 JSON instead of redirecting.
- `is_super_admin` at L132 — reads `session["is_super_admin"]` / `session["admin_email"]` as a cache

**Test-mode bypass:** `ADCP_AUTH_TEST_MODE=true` + `session["test_user"]` + `session["test_tenant_id"]` + `session["test_user_role"]`.

**OAuth flows live entirely inside Flask today:**
- `auth.py` uses `authlib.integrations.flask_client.OAuth`, registers global Google client, redirects via `authorize_redirect`, receives callback via `authorize_access_token`, writes user info into `session["user"]`
- `oidc.py` (431 LOC, 7 routes) — rebuilds tenant-specific OIDC clients per request with dynamic discovery URLs

### 3.6 Lifecycle hooks

**`@app.before_request`** (`src/admin/app.py:211-269`, 58 LOC):
- `redirect_external_domain_admin` — Approximated proxy external-domain → tenant-subdomain redirect. Reads `Apx-Incoming-Host` header, looks up tenant via `get_tenant_by_virtual_host`, issues 302.

**`@app.after_request`** (`src/admin/app.py:272-295`, 24 LOC):
- `log_auth_cookies` — debug log for `Set-Cookie` headers. Gated on `/auth|/login|/admin` path prefixes. **Deletable** (debug noise).

**`@app.context_processor`** (`src/admin/app.py:298-330`, 32 LOC):
- `inject_context` — inject `script_name`, `support_email`, `sales_agent_domain`, `tenant` (DB lookup) into every template

**Error handlers:** Only `@schemas_bp.errorhandler(404)` / `500` at `src/admin/blueprints/schemas.py:176, 195`. No app-level Flask error handlers.

### 3.7 Middleware chain (`src/admin/app.py`)

Flask `app.wsgi_app` is wrapped in three layers:
1. `CustomProxyFix` — custom subclass of `werkzeug.ProxyFix`
2. `FlyHeadersMiddleware` — copies `Fly-Forwarded-Proto` → `X-Forwarded-Proto` (may be redundant now; Fly added standard `X-Forwarded-*` mid-2024)
3. Werkzeug `ProxyFix`

### 3.8 Coexistence with FastAPI today

**Current state = Flask mounted inside FastAPI via `a2wsgi.WSGIMiddleware`:**

- `src/app.py` is the sole ASGI entry point (`FastAPI(...)` at line 64)
- MCP mounted at `/mcp` (line 72)
- A2A routes added directly via `A2AStarletteApplication.add_routes_to_app(app, ...)` at lines 118-123 (adds `/a2a`, `/.well-known/agent-card.json`, `/agent.json`)
- Flask admin mounted LAST at `/admin` AND `/` (root catch-all) inside `_install_admin_mounts()` (lines 25-45)
- Lifespan re-sorts routes on startup so FastAPI-native routes (landing page at `/`, `/landing`) beat the Flask catch-all via explicit `routes.insert(0, ...)` / `insert(1, ...)` at lines 351-352
- `/a2a/` trailing-slash redirect (lines 127-135) prevents Flask from catching `/a2a/`
- **No shared middleware between Flask and FastAPI** — auth state lives in `scope["state"]["auth_context"]` for FastAPI but Flask uses its own `session["user"]`. The two auth systems are isolated.

---

## 4. Current-State Inventory: FastAPI Surface

### 4.1 FastAPI entry points

| Location | Role |
|---|---|
| `src/app.py:64` | `FastAPI(title="AdCP Sales Agent", lifespan=combine_lifespans(app_lifespan, mcp_app.lifespan))` |
| `src/app.py:48-54` | `app_lifespan` — runs `_install_admin_mounts()` on startup |
| `scripts/run_server.py:47` | `uvicorn.run("src.app:app", ...)` — only ASGI server |

### 4.2 FastMCP + A2A + REST routers

- **MCP:** `src/core/main.py:127` — `mcp = FastMCP(name="AdCPSalesAgent", ...)`. `mcp.http_app(path="/")` mounted at `/mcp`.
- **A2A:** `a2a-sdk[http-server]==0.3.22`. `A2AStarletteApplication` adds routes directly onto root app.
- **REST (`src/routes/api_v1.py:37`):** `APIRouter(prefix="/api/v1", tags=["api-v1"])` with 12 routes. The canonical pattern for new FastAPI code.
- **Health (`src/routes/health.py`):** `router = APIRouter()` + `debug_router = APIRouter(dependencies=[Depends(require_testing_mode)])`.

### 4.3 Existing FastAPI auth deps — THE pattern to reuse

**`src/core/auth_context.py`** exposes reusable auth primitives:

```python
@dataclass(frozen=True)
class AuthContext:
    auth_token: str | None = None
    headers: MappingProxyType[str, str] = ...

def _get_auth_context(request: Request) -> AuthContext: ...
def _resolve_auth_dep(...) -> ResolvedIdentity | None: ...   # optional auth
def _require_auth_dep(...) -> ResolvedIdentity: ...          # required auth

# Annotated aliases — THE pattern
ResolveAuth = Annotated[ResolvedIdentity | None, Depends(_resolve_auth_dep)]
RequireAuth = Annotated[ResolvedIdentity, Depends(_require_auth_dep)]
resolve_auth: Any = Depends(_resolve_auth_dep)
require_auth: Any = Depends(_require_auth_dep)
```

Wired by `UnifiedAuthMiddleware` at `src/core/auth_middleware.py:23` (pure ASGI, deliberately NOT `BaseHTTPMiddleware` to avoid Starlette #1729), which populates `request.state.auth_context` from `x-adcp-auth` / `Authorization: Bearer` headers.

**`ResolvedIdentity`** (`src/core/resolved_identity.py:24`) is the canonical auth object — a Pydantic `BaseModel`.

### 4.4 Existing middleware stack (`src/app.py:274-293`)

Registered via `add_middleware` (LIFO — last registered = outermost):
1. `CORSMiddleware` — origins from `ALLOWED_ORIGINS` env var
2. `RestCompatMiddleware` — normalizes deprecated REST body fields for `/api/v1/*` POSTs
3. `UnifiedAuthMiddleware` — pure ASGI, sets `scope["state"]["auth_context"]`
4. `@app.middleware("http")` `a2a_messageid_compatibility_middleware` — numeric → string messageId

**NO `SessionMiddleware` currently.** Sessions exist only within the Flask sub-app.

### 4.5 Exception handling

`@app.exception_handler(AdCPError)` at `src/app.py:82-88` translates typed `AdCPError` subclasses to JSON responses. This is the pattern for new exceptions.

### 4.6 Existing FastAPI patterns to reuse

- **`Depends()` identity pattern** — `ResolveAuth` / `RequireAuth` aliases
- **Router-level dependencies** — `APIRouter(dependencies=[Depends(...)])` for gating
- **Exception → HTTP translation** — `@app.exception_handler(AdCPError)`
- **Lifespan composition** — `combine_lifespans(app_lifespan, mcp_app.lifespan)`
- **Routes on root app (not sub-mount)** — A2A pattern, so `UnifiedAuthMiddleware` sees them

**Gaps that must be built fresh:**
- No form handling in any existing FastAPI route
- No file upload
- No OAuth flow
- No server-side sessions
- No HTML template rendering (landing pages use HTML strings)
- `python-multipart>=0.0.22` is pinned but unused

### 4.7 Dependencies (verified versions)

| Package | Declared | Locked |
|---|---|---|
| `fastapi` | >=0.100.0 | 0.128.0 |
| `starlette` | (transitive) | 0.50.0 |
| `uvicorn` | >=0.23.0 | 0.40.0 |
| `fastmcp` | >=3.2.0 | 3.2.0 |
| `a2a-sdk[http-server]` | >=0.3.19 | 0.3.22 |
| `jinja2` | >=3.1.0 | 3.1.6 |
| `a2wsgi` | >=1.10.0 | 1.10.10 |
| `authlib` | via extra | 1.6.7 |
| `flask` | >=3.1.3 | — |
| `flask-caching` | >=2.3.0 | — |
| `flask-socketio` | >=5.5.1 | — |
| `python-multipart` | >=0.0.22 | — (pinned, unused) |

---

## 5. Current-State Inventory: Test Surface

### 5.1 Test directory layout

| Directory | Total files | Test files | Purpose |
|---|---|---|---|
| `tests/unit/` | 312 | 303 | Unit (no DB, mocked) |
| `tests/integration/` | 182 | 175 | Real PostgreSQL |
| `tests/e2e/` | 28 | 18 | Docker stack via HTTP (transport-agnostic) |
| `tests/bdd/` | 12 (+features) | 7 | Gherkin scenarios |
| `tests/admin/` | 6 | 3 | Flask UI |
| `tests/harness/` | 35 | 8 | Test harness itself |
| `tests/factories/` | 14 | 1 | factory-boy |

### 5.2 Flask test-client touch surface

- **20 files** use `app.test_client()`
- **~21 distinct files** import `flask` / `from src.admin.app` / `create_app`
- **~17 integration files** build a Flask app to test admin routes
- **7 files** use `session_transaction()` (Flask session priming)
- **17 files** use `302` / `follow_redirects` patterns in assertions

### 5.3 Flask test fixtures (conftest.py files)

| Conftest | Line | Fixture |
|---|---|---|
| `tests/conftest.py` | 596-621 | `flask_app` |
| `tests/conftest.py` | 624-627 | `flask_client` |
| `tests/conftest.py` | 630-635 | `authenticated_client` |
| `tests/admin/conftest.py` | 48 | `ui_client` |
| `tests/admin/conftest.py` | 74 | `authenticated_ui_client` |
| `tests/integration/conftest.py` | 77 | `admin_client` |
| `tests/integration/conftest.py` | 641 | `test_admin_app` |
| `tests/integration/conftest.py` | 658 | `authenticated_admin_client` |
| `tests/harness/admin_accounts.py` | 134-162 | `_setup_flask_client` (dual-mode: integration uses Flask test_client, e2e uses requests.Session) |

### 5.4 Admin UI tests (`tests/admin/`)

- `test_accounts_blueprint.py` — 183 LOC, 7 tests. Canonical small-CRUD admin test.
- `test_product_creation_integration.py` — 359 LOC, 5 tests. Form POST + DB verification.
- `test_comprehensive_pages.py` — 253 LOC, 1 test. Uses `requests.Session` against running server (already transport-agnostic).

### 5.5 The FastAPI test pattern to extend (`tests/harness/_base.py:894-913`)

**This is the MODEL for the new `get_admin_client()` extension:**

```python
def get_rest_client(self) -> Any:
    """Return FastAPI TestClient with default auth dep override."""
    if self._rest_client is None:
        from starlette.testclient import TestClient
        from src.app import app
        from src.core.auth_context import _require_auth_dep, _resolve_auth_dep

        rest_identity = self.identity_for(Transport.REST)
        app.dependency_overrides[_require_auth_dep] = lambda: rest_identity
        app.dependency_overrides[_resolve_auth_dep] = lambda: rest_identity
        self._rest_client = TestClient(app)

    return self._rest_client
```

Teardown at `tests/harness/_base.py:827-832` clears `app.dependency_overrides` on `__exit__`.

### 5.6 BDD admin handling

- `tests/bdd/features/BR-ADMIN-ACCOUNTS.feature` — the only admin BDD feature
- `tests/bdd/steps/domain/admin_accounts.py` — admin step definitions
- `tests/bdd/conftest.py:534-561` — `_ADMIN_TAG_PREFIX = "T-ADMIN-"` excludes admin scenarios from cross-transport parametrization. Admin stays single-transport by design.

### 5.7 Quality gates touching Flask

- **`.pre-commit-hooks/check_route_conflicts.py`** (line 60-67 of `.pre-commit-config.yaml`) — scans Flask `@bp.route(...)` decorators. Needs Flask → FastAPI rewrite.
- **23 architecture guard tests** in `tests/unit/test_architecture_*.py` — **ZERO currently Flask-specific** (grep for `flask` returned 0 hits). No guards need updating.

### 5.8 Blast radius summary

| Metric | Count |
|---|---|
| Test files importing `flask` | ~21 |
| Test files using `app.test_client()` | 20 |
| `tests/admin/` total tests | 14 across 3 test files |
| Integration files building a Flask app | ~17 |
| Files using `session_transaction()` | 7 |
| Files using 302/follow_redirects assertions | 17 |
| Flask-client conftest fixtures | 8 |
| FastAPI TestClient files (pattern to copy) | 26 |

---

## 6. 2026 FastAPI Ecosystem Research (verified April 2026)

### 6.1 Current versions

- **FastAPI** 0.128.0 (locked) — `Annotated[T, Depends()]` is the idiomatic pattern
- **Starlette** 0.50.0 (locked) — `Jinja2Templates` API changed in FastAPI 0.108+ (`request` is now first kwarg)
- **Pydantic** v2.10+ — `ConfigDict(frozen=True, strict=True, extra="forbid")` is canonical
- **SQLAlchemy** 2.0.36+ — async engine fully mature; deferring to v2.1
- **uvicorn** 0.34.0+ — `--proxy-headers --forwarded-allow-ips='*'` replaces custom ProxyFix
- **Python** 3.12 / 3.13 — 3.12 is the safest "modern" floor

### 6.2 Canonical FastAPI patterns (2026)

**`Annotated[T, Depends()]` pattern:**
```python
# 2026 canonical
UserDep = Annotated[User, Depends(get_user)]
def handler(user: UserDep): ...
```

**`lifespan` context manager** (not deprecated `@app.on_event`):
```python
@asynccontextmanager
async def app_lifespan(app: FastAPI):
    yield

app = FastAPI(lifespan=app_lifespan)
```

**`Jinja2Templates` API (FastAPI 0.108+):**
```python
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="templates")
templates.env.filters["custom"] = my_filter
templates.env.globals["site_name"] = "AdCP"

return templates.TemplateResponse(
    request=request,       # NEW API (was inside context pre-0.108)
    name="item.html",
    context={"id": id}
)
```

**`url_for()` in templates:**
```jinja
{{ url_for('route_name', key=value) }}
{{ url_for('static', path='/app.js') }}
```

Route names come from the `name=` kwarg on the decorator. **Flat names, no dot namespace.**

### 6.3 Session management

**`starlette.middleware.sessions.SessionMiddleware`:**
```python
from starlette.middleware.sessions import SessionMiddleware

app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ["SESSION_SECRET"],
    session_cookie="adcp_session",
    max_age=14 * 24 * 3600,
    same_site="none" if production else "lax",
    https_only=production,
    path="/",
)
```

- Signed via `itsdangerous`
- Cookie format NOT interchangeable with Flask's → forced re-login at cutover
- ~4KB payload cap
- `request.session` is a plain dict

### 6.4 Authlib Starlette client (OAuth)

```python
from authlib.integrations.starlette_client import OAuth

oauth = OAuth()
oauth.register(
    name="google",
    client_id=GOOGLE_CLIENT_ID,
    client_secret=GOOGLE_CLIENT_SECRET,
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)

@router.get("/login")
async def login(request: Request):
    redirect_uri = request.url_for("auth_callback")
    return await oauth.google.authorize_redirect(request, str(redirect_uri))

@router.get("/callback", name="auth_callback")
async def callback(request: Request):
    token = await oauth.google.authorize_access_token(request)
    request.session["user"] = token.get("userinfo")
    return RedirectResponse(url="/admin/", status_code=303)
```

**Requires `SessionMiddleware`** — OAuth state lives on `request.session`. Near-identical to Flask's `authlib.integrations.flask_client.OAuth`.

### 6.5 CSRF protection options (2026)

Four library options evaluated:

| Library | Pros | Cons |
|---|---|---|
| `starlette-csrf` (frankie567) | Well-maintained, Double Submit Cookie | Header-only — incompatible with plain `<form>` POST |
| `fastapi-csrf-jinja` | Form-friendly, Jinja integration | **Last release May 2024**, upstream quiet |
| `csrf-starlette-fastapi` (gnat) | Minimal, `pydantic-settings` config | JSON-body focused |
| `fastapi-csrf-protect` | Actively maintained, Double Submit + form field | External dep, heavyweight |

**Roll-your-own Double Submit Cookie (~100 LOC):** zero external dep, full control. Uses `itsdangerous.URLSafeTimedSerializer` (already transitive of `SessionMiddleware`). **This is what the user chose.**

### 6.6 Other modern patterns

- **`pydantic-settings>=2.7.0`** — `BaseSettings` for typed config
- **`sse-starlette>=2.2.0`** — `EventSourceResponse` for SSE
- **`python-multipart>=0.0.22`** — activates on first `Form(...)` or `UploadFile` handler
- **Pure ASGI middleware preferred over `BaseHTTPMiddleware`** — Starlette #1729 has a known bug where `BaseHTTPMiddleware` doesn't propagate ContextVars correctly

---

## 7. Template Portability Reality Check

**Key insight:** "Flask and FastAPI both use Jinja2" is true but misleading. Zero templates would render unchanged under vanilla `Jinja2Templates(directory="templates")`. Flask auto-installs a ~50-LOC environment that FastAPI doesn't.

### 7.1 The six implicit Flask-Jinja globals

| Template reference (from real `base.html`) | Flask auto-provides via | FastAPI requires |
|---|---|---|
| `{{ script_name }}` | `inject_context` context processor | Per-request context dict OR Jinja global resolving `request.scope["root_path"]` |
| `{{ url_for('tenants.list') }}` | Flask's URL map with `<blueprint>.<endpoint>` dotted namespace | `request.url_for('flat_name')` — flat name, **returns absolute URL** |
| `{{ session.authenticated }}` | Flask's thread-local `session` proxy | `request.session` dict from `SessionMiddleware` — NOT in Jinja env by default |
| `{% for c, m in get_flashed_messages() %}` | Flask installs as Jinja global, pops from `session["_flashes"]` | **No equivalent.** Must be written from scratch |
| `{{ tenant.name }}` | Context processor DB lookup per request | Must be passed in context dict per-handler |
| `{{ g.test_mode }}` + `{{ csrf_token() }}` | Flask's `g` proxy; Flask-WTF | No `g`; no CSRF — **decision required** |

### 7.2 Verdict on portability

"Templates are portable" is **technically true** (same Jinja2 engine) but **practically misleading** (the implicit environment is non-trivial). Zero template files would render correctly without deliberate work to reproduce Flask's implicit environment on the FastAPI side.

**The work is not optional.** The only question is where you do it — in a shim (Option A) or a codemod (Option B).

---

## 8. Option A vs B vs C Trade-offs

### 8.1 Option A — Runtime Jinja shim (rejected)

**Concept:** Install `url_for`, `session`, `script_root`, `csrf_token`, `get_flashed_messages` as Jinja globals / per-request context values. A `LEGACY_ROUTE_MAP` dict maps `"bp.endpoint"` → `"flat_name"`.

**Pros:** Zero template edits, low merge-conflict surface, incremental-friendly, shim deletes cleanly at the end.

**Cons:** ~150-200 LOC shim lives for migration duration, parallel `LEGACY_ROUTE_MAP` hidden state, one extra indirection per `url_for` call, **templates retain Flask-flavored API**, risk of "temporary" becoming permanent, silent-failure mode.

### 8.2 Option B — AST/regex codemod (CHOSEN)

**Concept:** One mechanical pass over 72 templates, reviewed in a single PR. Transforms:
- `url_for('bp.endpoint', ...)` → `url_for('bp_endpoint', ...)`
- `url_for('static', filename='x.js')` → `{{ script_root ~ '/static/x.js' }}`
- `request.script_root` → `script_root`
- `csrf_token()` → `csrf_token`
- `get_flashed_messages(with_categories=true)` → `get_flashed_messages(request, with_categories=true)`
- `g.X` → inject via context dict

**Pros:** Templates immediately FastAPI-native, single source of truth (FastAPI route registry), boot-time verification possible, no shim to maintain, bounded reviewable diff.

**Cons:** Big cross-cutting diff (40 template files), merge conflicts during parallel waves, chicken-and-egg (codemod needs final route names), retroactive renames expensive, missing-name failures at render time.

### 8.3 Option C — Hybrid (rejected)

**Concept:** Option A during migration, Option B's codemod at the end to retire the shim.

**Rejected because:** the user chose "written today from scratch" framing. Hybrid preserves Flask flavor during migration, contradicting cleanroom end-state requirement.

### 8.4 Why Option B won

User quote: *"B or whatever is fastapi native and looks like we rewrote this repo today enitrely without flask and just FastAPI"*

The v2.0 codebase must read as if Flask never existed. Only Option B lands that end state cleanly.

---

## 9. Blueprint Translation Challenges (14 Flask-isms per route)

**Key insight #2:** Templates are only one axis. Blueprints have their own Flask semantics. Every one of the following requires translation:

| Flask | FastAPI |
|---|---|
| `@bp.route("/foo", methods=["GET"])` | `@router.get("/foo", name="bp_foo")` (name= load-bearing) |
| `url_prefix="/tenant/<tenant_id>"` | `APIRouter(prefix="/tenant/{tenant_id}")` |
| `request.args.get("status")` | `status: Annotated[str \| None, Query()] = None` |
| `request.form.get("name")` | `name: Annotated[str, Form()]` |
| `request.files["logo"]` | `logo: Annotated[UploadFile, File()]` |
| `render_template("foo.html", x=1)` | `render(request, "foo.html", {"x": 1})` |
| `redirect(url_for("foo"))` | `RedirectResponse(str(request.url_for("foo")), status_code=303)` |
| `jsonify({"ok": True})` | `return {"ok": True}` (auto-JSON) |
| `flash("Saved", "success")` | `flash(request, "Saved", "success")` |
| `abort(404)` | `raise HTTPException(status_code=404)` |
| `@bp.before_request` | `APIRouter(dependencies=[Depends(...)])` or middleware |
| `@bp.errorhandler(404)` | **No equivalent.** App-level `@app.exception_handler(...)` or try/except |
| `@require_auth` + `session["user"]` + `g.user` | `user: AdminUserDep` |
| `current_app.cache.get(...)` | Module-level singleton OR delete (zero callers) |
| `session["key"] = value` | `request.session["key"] = value` |

**Rough effort estimate:** 232 routes × 5-15 min each = **20-60 hours raw translation** before tests, reviews, auth edge cases. Template strategy is a minor lever compared to this.

**Also changes:**
- Flask auto-wraps string/tuple/dict returns → FastAPI requires `Response` subclass / Pydantic model / dict
- `request.form` is sync property → `await request.form()` or declarative `Form(...)`
- `flask.redirect()` defaults to 302 → `RedirectResponse()` defaults to 307; **use 303 explicitly for POST-redirect-GET**
- `flask.g` gone entirely — only 3 write sites at `src/admin/utils/helpers.py:260, 271, 321`

---

## 10. Target Architecture (End State)

### 10.1 Module layout

**Decision: keep `src/admin/` directory name, replace contents top-to-bottom.**

```
src/admin/
├── __init__.py                     # exports create_admin_router, admin_lifespan_hooks
├── app_factory.py                  # build_admin_router() -> APIRouter      ~80 LOC
├── templating.py                   # Jinja2Templates singleton + render()  ~120 LOC
├── flash.py                        # native flash() / get_flashed_messages ~70 LOC
├── csrf.py                         # Roll-your-own Double Submit Cookie   ~100 LOC
├── sessions.py                     # SessionMiddleware config helper       ~40 LOC
├── oauth.py                        # Authlib starlette_client.OAuth        ~60 LOC
├── middleware/
│   ├── external_domain.py          # Approximated redirect (pure ASGI)     ~90 LOC
│   └── fly_headers.py              # Fly-* → X-Forwarded-* (pure ASGI)     ~40 LOC
├── deps/
│   ├── auth.py                     # get_admin_user, require_super_admin   ~220 LOC
│   ├── tenant.py                   # get_current_tenant                    ~90 LOC
│   └── audit.py                    # audit_action(name) dep factory        ~110 LOC
├── schemas/                        # Pydantic forms per feature            ~500 LOC
├── routers/                        # one file per old blueprint            ~8000 LOC
│   ├── public.py, core.py, auth.py, oidc.py, tenants.py, accounts.py,
│   ├── products.py, principals.py, users.py, gam.py, inventory.py,
│   ├── inventory_profiles.py, creatives.py, creative_agents.py,
│   ├── signals_agents.py, operations.py, policy.py, settings.py,
│   ├── adapters.py, authorized_properties.py, publisher_partners.py,
│   ├── workflows.py, api.py, format_search.py, schemas.py, activity_stream.py
├── services/                       # extracted helpers                     ~200 LOC
├── templates/                      # moved from /templates (codemod applied)
└── static/                         # moved from /static
```

**Total new admin LOC: ~11,500** (vs 21,340 Flask LOC — ~45% reduction from removing boilerplate).

**Why `src/admin/` not `src/web/` or `src/routes/admin/`:**
- `src/routes/` is already claimed by the REST transport layer (mixing admin HTML there conflates concerns)
- `src/web/` is net-new for marginal clarity gain
- Keeping `src/admin/` minimizes import churn in `tests/admin/`, alembic, audit decorators, and ~200 files that reference `src.admin.*`

### 10.2 Final `src/app.py` shape

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.sessions import SessionMiddleware
from starlette.staticfiles import StaticFiles
from fastmcp.utilities.lifespan import combine_lifespans

from src.core.main import mcp
from src.core.auth_middleware import UnifiedAuthMiddleware
from src.core.exceptions import AdCPError
from src.admin.app_factory import build_admin_router, admin_lifespan_hooks
from src.admin.middleware.external_domain import ApproximatedExternalDomainMiddleware
from src.admin.csrf import CSRFMiddleware
from src.admin.sessions import session_middleware_kwargs
from src.admin.oauth import init_oauth
from src.routes.api_v1 import router as api_v1_router
from src.routes.health import router as health_router, debug_router as health_debug_router

mcp_app = mcp.http_app(path="/")

@asynccontextmanager
async def app_lifespan(app: FastAPI):
    init_oauth()
    await admin_lifespan_hooks.startup()
    yield
    await admin_lifespan_hooks.shutdown()

app = FastAPI(
    title="AdCP Sales Agent",
    version="2.0.0",
    lifespan=combine_lifespans(app_lifespan, mcp_app.lifespan),
)

@app.exception_handler(AdCPError)
async def adcp_error_handler(request, exc):
    return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

# Category-2 error-shape compat handler
from src.admin.routers._legacy_error_shape import legacy_error_shape_handler
from fastapi import HTTPException
app.add_exception_handler(HTTPException, legacy_error_shape_handler)

# Static
app.mount("/static", StaticFiles(directory="src/admin/static"), name="static")

# Routers
app.include_router(api_v1_router)
app.include_router(health_router)
app.include_router(health_debug_router)
# build_admin_router() returns APIRouter(include_in_schema=False) — see ADCP safety audit §3
# Admin routes are functional but invisible in /openapi.json and /docs,
# keeping the published OpenAPI surface equal to the AdCP REST contract.
app.include_router(build_admin_router(), prefix="/admin")

# A2A (unchanged)
a2a_app.add_routes_to_app(app, ...)
app.mount("/mcp", mcp_app)

# Middleware (add_middleware is LIFO; outermost registered last)
# CORRECTED ORDER per deep audit blocker #5:
# Approximated MUST run BEFORE CSRF so external-domain POST users
# get redirected (307) instead of CSRF-rejected (403).
app.add_middleware(UnifiedAuthMiddleware)
app.add_middleware(RestCompatMiddleware)
app.add_middleware(CSRFMiddleware)                         # ← now AFTER Approximated
app.add_middleware(ApproximatedExternalDomainMiddleware)   # ← MOVED UP
app.add_middleware(SessionMiddleware, **session_middleware_kwargs())
app.add_middleware(CORSMiddleware, ...)

# Root landing pages (FastAPI-native)
@app.get("/")
async def root(request: Request): ...

@app.get("/landing")
async def landing(request: Request): ...
```

**Deleted from old `src/app.py`:** `_install_admin_mounts()`, `a2wsgi`, `flask_admin_app`, `admin_wsgi`, `CustomProxyFix`, `routes.insert(0,...)` hack, `/a2a/` trailing-slash redirect.

---

## 11. Foundation Modules (with code)

### 11.1 `Jinja2Templates` singleton (`src/admin/templating.py`, ~120 LOC)

```python
from pathlib import Path
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
import json, markdown as md_lib

from src.admin.flash import get_flashed_messages
from src.core.domain_config import get_support_email, get_sales_agent_domain

_TEMPLATE_DIR = Path(__file__).parent / "templates"

def _from_json(s):
    if not s: return {}
    try: return json.loads(s) if isinstance(s, str) else s
    except (json.JSONDecodeError, TypeError): return {}

def _markdown(text):
    if not text: return Markup("")
    return Markup(md_lib.markdown(text, extensions=["extra", "nl2br"]))

templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))
templates.env.filters["from_json"] = _from_json
templates.env.filters["markdown"] = _markdown
templates.env.globals["get_flashed_messages"] = get_flashed_messages

def render(request, name, context=None, *, status_code=200):
    """One-call wrapper injecting admin_prefix, static_prefix, csrf_token, support_email,
    sales_agent_domain, and the current tenant. Replaces Flask's inject_context
    context processor.

    CORRECTED per deep audit blocker #1: admin_prefix and static_prefix replace the
    old script_name/script_root variable because include_router(prefix="/admin")
    does NOT set scope["root_path"] (verified via runtime introspection; see
    flask-to-fastapi-deep-audit.md §1.1). Templates use:
      {{ admin_prefix }}/tenant/...  for admin paths (renders /admin/tenant/...)
      {{ static_prefix }}/foo.css    for static assets (renders /static/foo.css)
    """
    base = {
        "request": request,
        "admin_prefix": "/admin",   # hard-coded; matches build_admin_router() prefix
        "static_prefix": "/static", # hard-coded; matches outer app StaticFiles mount
        "support_email": get_support_email(),
        "sales_agent_domain": get_sales_agent_domain() or "example.com",
        "csrf_token": getattr(request.state, "csrf_token", ""),
        "tenant": _load_current_tenant_dict(request),  # replaces Flask inject_context DB lookup
    }
    if context:
        base.update(context)
    return templates.TemplateResponse(name, base, status_code=status_code)


def _load_current_tenant_dict(request) -> dict | None:
    """Per-request tenant load (replaces inject_context at src/admin/app.py:298-330).
    Uses the repository pattern (no raw select) to respect CLAUDE.md Pattern #3."""
    tenant_id = request.session.get("tenant_id")
    if not tenant_id:
        return None
    from src.core.database.database_session import get_db_session
    from src.core.database.repositories.tenant import TenantRepository
    with get_db_session() as db:
        tenant = TenantRepository(db).get_by_id(tenant_id)
    return tenant.to_dict() if tenant else None
```

Every handler calls `render(request, "foo.html", {...})`. No context processor.

### 11.2 `SessionMiddleware` config (`src/admin/sessions.py`, ~40 LOC)

```python
import os
from src.core.config_loader import is_single_tenant_mode
from src.core.domain_config import get_session_cookie_domain

def session_middleware_kwargs() -> dict:
    production = os.environ.get("PRODUCTION", "").lower() == "true"
    kwargs = {
        "secret_key": os.environ["SESSION_SECRET"],   # HARD-REQUIRED
        "session_cookie": "adcp_session",
        "max_age": 14 * 24 * 3600,
        "same_site": "none" if production else "lax",
        "https_only": production,
        "path": "/",
    }
    if production and not is_single_tenant_mode():
        kwargs["domain"] = get_session_cookie_domain()
    return kwargs
```

- Starlette `SessionMiddleware` is signed-cookie (`itsdangerous`), ~4KB cap
- Cookie name `session` → `adcp_session` → forced re-login at cutover
- `SESSION_SECRET` replaces `FLASK_SECRET_KEY`, hard-required, no fallback

### 11.3 Native `flash()` (`src/admin/flash.py`, ~70 LOC)

```python
from typing import Literal
from starlette.requests import Request

Category = Literal["info", "success", "warning", "error", "danger"]
_SESSION_KEY = "_flashes"

def flash(request: Request, message: str, category: Category = "info") -> None:
    bucket = request.session.setdefault(_SESSION_KEY, [])
    bucket.append((category, message))

def get_flashed_messages(
    request: Request | None = None,
    *,
    with_categories: bool = False,
    category_filter: list[str] | None = None,
) -> list:
    if request is None:
        return []
    bucket = request.session.pop(_SESSION_KEY, [])
    if category_filter:
        bucket = [(c, m) for c, m in bucket if c in category_filter]
    return bucket if with_categories else [m for _, m in bucket]
```

Templates' existing `{% for c, m in get_flashed_messages(with_categories=true) %}` becomes `{% for c, m in get_flashed_messages(request, with_categories=true) %}`. Codemod handles this.

### 11.4 Admin auth deps (`src/admin/deps/auth.py`, ~220 LOC)

Replaces `@require_auth`, `@require_tenant_access`, all `flask.g` usage.

```python
from typing import Annotated, Literal
from dataclasses import dataclass
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import Tenant, User

@dataclass(frozen=True)
class AdminUser:
    email: str
    role: Literal["super_admin", "tenant_admin", "tenant_user", "test"]
    is_test_user: bool = False

class AdminRedirect(Exception):
    def __init__(self, to: str, next_url: str = ""):
        self.to = to
        self.next_url = next_url

def _get_admin_user_or_none(request: Request) -> AdminUser | None:
    session = request.session
    if "test_user" in session and os.environ.get("ADCP_AUTH_TEST_MODE") == "true":
        return AdminUser(
            email=_extract_email(session["test_user"]),
            role=session.get("test_user_role", "tenant_user"),
            is_test_user=True,
        )
    raw = session.get("user")
    if raw is None:
        return None
    email = _extract_email(raw)
    role = "super_admin" if is_super_admin(email) else "tenant_user"
    return AdminUser(email=email, role=role)

def get_admin_user_optional(request: Request) -> AdminUser | None:
    return _get_admin_user_or_none(request)

def get_admin_user(request: Request) -> AdminUser:
    user = _get_admin_user_or_none(request)
    if user is None:
        raise AdminRedirect(to="/admin/login", next_url=str(request.url))
    return user

def require_super_admin(user: "AdminUserDep") -> AdminUser:
    if user.role != "super_admin":
        raise HTTPException(status_code=403, detail="Super admin required")
    return user

def get_current_tenant(
    request: Request,
    user: "AdminUserDep",
    tenant_id: str,  # path param
) -> dict:
    if user.role == "super_admin" or (user.is_test_user and request.session.get("test_tenant_id") == tenant_id):
        return _load_tenant(tenant_id)
    with get_db_session() as db:
        found = db.scalars(
            select(User).filter_by(email=user.email.lower(), tenant_id=tenant_id, is_active=True)
        ).first()
        if not found:
            raise HTTPException(status_code=403, detail="Access denied")
    return _load_tenant(tenant_id)

# THE public surface — Annotated aliases used everywhere
AdminUserOptional = Annotated[AdminUser | None, Depends(get_admin_user_optional)]
AdminUserDep      = Annotated[AdminUser, Depends(get_admin_user)]
SuperAdminDep     = Annotated[AdminUser, Depends(require_super_admin)]
CurrentTenantDep  = Annotated[dict, Depends(get_current_tenant)]
```

**Exception handler for `AdminRedirect`:**
```python
@app.exception_handler(AdminRedirect)
async def admin_redirect_handler(request: Request, exc: AdminRedirect):
    return RedirectResponse(url=f"{exc.to}?next={exc.next_url}", status_code=303)
```

**Split decision:** Flask's `require_tenant_access(api_mode=True)` becomes **two separate deps**: `CurrentTenantDep` (HTML — raises `AdminRedirect` → 303) and `CurrentTenantJsonDep` (JSON — raises `HTTPException(401)`).

### 11.5 OAuth (`src/admin/oauth.py`, ~60 LOC)

```python
from authlib.integrations.starlette_client import OAuth

oauth = OAuth()
_tenant_client_cache: dict[str, Any] = {}

def init_oauth() -> None:
    import os
    oauth.register(
        name="google",
        client_id=os.environ["GOOGLE_CLIENT_ID"],
        client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )

def get_tenant_oidc_client(tenant_id: str):
    if tenant_id in _tenant_client_cache:
        return _tenant_client_cache[tenant_id]
    config = get_oidc_config_for_auth(tenant_id)
    if not config:
        return None
    name = f"tenant_{tenant_id}"
    oauth.register(
        name=name,
        client_id=config["client_id"],
        client_secret=config["client_secret"],
        server_metadata_url=config["discovery_url"],
        client_kwargs={"scope": config["scopes"]},
    )
    client = getattr(oauth, name)
    _tenant_client_cache[tenant_id] = client
    return client

def invalidate_tenant_oidc_client(tenant_id: str) -> None:
    _tenant_client_cache.pop(tenant_id, None)
```

OAuth state rides on `request.session` — same cookie as admin session.

### 11.6 CSRF: roll-your-own Double Submit Cookie (`src/admin/csrf.py`, ~100 LOC)

**Decision: bespoke, not a library.** Zero external dep risk, full control.

Mechanism:
1. On safe-method requests (GET/HEAD/OPTIONS), middleware generates a token via `itsdangerous.URLSafeTimedSerializer`, sets it as `adcp_csrf` cookie (SameSite=Lax, Secure in prod, HttpOnly=False for JS XHR), stashes on `request.state.csrf_token`
2. On unsafe-method requests, middleware validates form field `csrf_token` OR header `X-CSRF-Token` matches the cookie
3. Mismatch → 403 with `{"detail": "CSRF token missing or invalid"}`
4. Exemption list: OAuth callbacks, `/mcp`, `/a2a`, `/api/v1/*`, webhooks (authenticated via Bearer)

```python
# src/admin/csrf.py  (~100 LOC)
import os, secrets
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

_COOKIE_NAME = "adcp_csrf"
_FORM_FIELD = "csrf_token"
_HEADER_NAME = "X-CSRF-Token"
_TOKEN_MAX_AGE = 24 * 3600
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_EXEMPT_PATH_PREFIXES = (
    "/mcp", "/a2a", "/api/v1/", "/_internal/",
    "/admin/auth/callback", "/admin/auth/oidc/",
)
# /_internal/ added per ADCP safety audit §4 — /_internal/reset-db-pool is a POST
# used by the integration test harness to reset DB pools; it's gated by
# ADCP_TESTING=true env but must also be CSRF-exempt.

def _signer():
    return URLSafeTimedSerializer(os.environ["SESSION_SECRET"], salt="adcp-csrf")

def _new_token():
    return _signer().dumps(secrets.token_urlsafe(32))

def _validate(token):
    try:
        _signer().loads(token, max_age=_TOKEN_MAX_AGE)
        return True
    except (BadSignature, SignatureExpired):
        return False

class CSRFMiddleware:
    """Pure-ASGI Double Submit Cookie CSRF middleware."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        method = scope["method"]
        path = scope["path"]
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}

        cookie_header = headers.get("cookie", "")
        cookie_token = _extract_cookie(cookie_header, _COOKIE_NAME)

        is_exempt = any(path.startswith(p) for p in _EXEMPT_PATH_PREFIXES)
        if method not in _SAFE_METHODS and not is_exempt:
            form_token = await _read_csrf_from_body(scope, receive, headers)
            header_token = headers.get(_HEADER_NAME.lower())
            submitted = form_token or header_token
            if not cookie_token or not submitted or submitted != cookie_token or not _validate(cookie_token):
                await _respond_403(send)
                return

        token = cookie_token if cookie_token and _validate(cookie_token) else _new_token()
        scope.setdefault("state", {})["csrf_token"] = token

        async def send_with_cookie(message):
            if message["type"] == "http.response.start":
                message.setdefault("headers", [])
                message["headers"].append((
                    b"set-cookie",
                    f"{_COOKIE_NAME}={token}; Path=/; SameSite=Lax; "
                    f"{'Secure; ' if _is_prod() else ''}Max-Age={_TOKEN_MAX_AGE}".encode()
                ))
            await send(message)
        await self.app(scope, receive, send_with_cookie)
```

Jinja global `{{ csrf_token }}` reads from `request.state.csrf_token`. Templates: `<input type="hidden" name="csrf_token" value="{{ csrf_token }}">`. Codemod: `{{ csrf_token() }}` → `{{ csrf_token }}`.

**Testing:** dep override isn't possible (middleware, not Dep). `IntegrationEnv.get_admin_client()` fetches `/admin/` first, extracts CSRF cookie, includes in subsequent POSTs automatically.

### 11.7 Caching — DELETE `flask-caching` entirely

Audit confirmed **zero callers**. `Cache(app)` at `src/admin/app.py:208` is dead initialization. Drop the dep.

### 11.8 Proxy headers — uvicorn `--proxy-headers`

```bash
uvicorn src.app:app --proxy-headers --forwarded-allow-ips='*'
```

Replaces `CustomProxyFix` + werkzeug `ProxyFix`. `X-Script-Name` / `X-Forwarded-Prefix` vanishes because `include_router(prefix="/admin")` handles root path via `scope["root_path"]`.

`FlyHeadersMiddleware` stays as ~30 LOC pure ASGI **IF** Fly still sends only `Fly-*` headers at cutover. Verify first — Fly added standard `X-Forwarded-*` mid-2024 and this may already be redundant.

### 11.9 External-domain redirect (pure ASGI, `src/admin/middleware/external_domain.py`, ~90 LOC)

Replaces 58-LOC `@app.before_request redirect_external_domain_admin`. **Pure ASGI** (Starlette #1729). Must run BEFORE `SessionMiddleware`.

**🚨 CRITICAL INVARIANT (per [AdCP safety audit §4](flask-to-fastapi-adcp-safety.md)):** the middleware MUST preserve the `is_admin_request` path gate from `src/admin/app.py:226-230`. If the path does not start with `/admin`, the middleware MUST short-circuit to pass-through WITHOUT performing any tenant lookup or emitting any redirect. Otherwise, a proxy forwarding an `Apx-Incoming-Host` header to `/mcp`, `/a2a`, or `/api/v1/*` would 302 the AdCP client to a browser URL and break the call.

```python
class ApproximatedExternalDomainMiddleware:
    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        # ⚠️ CRITICAL: path gate — preserves src/admin/app.py:226-230 invariant
        # Non-admin paths are handed straight through; REMOVING THIS GATE
        # would cause AdCP clients carrying Apx-Incoming-Host to be 302-redirected.
        path = scope.get("path", "")
        if not path.startswith("/admin"):
            return await self.app(scope, receive, send)

        # ... rest of the external-domain redirect logic (Apx-Incoming-Host lookup,
        # tenant subdomain resolution, 302 via direct ASGI send)
```

**Guard test:** `tests/unit/test_architecture_approximated_middleware_path_gated.py` — structural test asserting short-circuit on any path not starting with `/admin`. MUST land in Wave 1 alongside the middleware port.

### 11.10 SSE via `sse-starlette.EventSourceResponse`

```python
from sse_starlette.sse import EventSourceResponse

@router.get("/tenant/{tenant_id}/activity/stream", name="activity_stream_events")
async def stream(tenant_id: str, tenant: CurrentTenantDep, request: Request):
    async def event_generator():
        last_check = datetime.now(UTC)
        for activity in reversed(get_recent_activities(tenant_id, limit=50)):
            yield {"data": json.dumps(activity)}
        while True:
            if await request.is_disconnected():
                break
            new = get_recent_activities(tenant_id, since=last_check - timedelta(seconds=1), limit=10)
            for a in reversed(new):
                yield {"data": json.dumps(a)}
            last_check = datetime.now(UTC)
            await asyncio.sleep(2)
    return EventSourceResponse(event_generator())
```

`sse-starlette` handles heartbeats, disconnect detection. Rate limit (`MAX_CONNECTIONS_PER_TENANT`) moves into a small dep.

**`SESSION_COOKIE_HTTPONLY=False` is unnecessary.** HttpOnly cookies are still sent on EventSource requests. The Flask setup was cargo-culted.

### 11.11 Static files

```python
app.mount("/static", StaticFiles(directory="src/admin/static"), name="static")
```

Move `/static` → `src/admin/static/`. Templates codemod `url_for('static', filename='x.js')` → `{{ script_root ~ '/static/x.js' }}`.

---

## 12. Template Codemod Details (Option B)

### 12.1 Mechanical transformations

| From (Flask Jinja) | To (FastAPI-native Jinja) |
|---|---|
| `{{ url_for('accounts.list_accounts', tenant_id=t) }}` | `{{ url_for('accounts_list_accounts', tenant_id=t) }}` |
| `{{ url_for('static', filename='app.js') }}` | `{{ script_root ~ '/static/app.js' }}` |
| `{{ request.script_root }}` | `{{ script_root }}` |
| `{{ session.authenticated }}`, `{{ session.role }}`, `{{ session.email }}` | **Unchanged** (Starlette `request.session` is dict; `request` in context) |
| `{{ g.test_mode }}` | `{{ test_mode }}` (inject via context dict) |
| `{{ csrf_token() }}` | `{{ csrf_token }}` (parens removed, now a variable) |
| `{% for c, m in get_flashed_messages(with_categories=true) %}` | `{% for c, m in get_flashed_messages(request, with_categories=true) %}` |
| `{{ tenant.name }}` | **Unchanged** (passed in per-handler context) |
| `{{ support_email }}` | **Unchanged** (`render()` injects globally) |

### 12.2 Flat route naming

Routes declared with `name="accounts_list_accounts"` (Flask's `accounts.list_accounts` flattened). Codemod is purely lexical.

```python
# Old Flask
@accounts_bp.route("/")  # endpoint "accounts.list_accounts"

# New FastAPI
@router.get("/tenant/{tenant_id}/accounts", name="accounts_list_accounts")
```

### 12.3 Codemod script sketch (`scripts/codemod_templates.py`)

Pure regex (Jinja isn't parseable with Python AST).

```python
import re, pathlib

URL_FOR = re.compile(r"""url_for\(\s*['"]([a-zA-Z_][a-zA-Z_0-9]*)\.([a-zA-Z_][a-zA-Z_0-9]*)['"]""")
STATIC_URL_FOR = re.compile(r"""url_for\(\s*['"]static['"]\s*,\s*filename\s*=\s*(['"][^'"]+['"])\s*\)""")
SCRIPT_ROOT = re.compile(r"\brequest\.script_root\b")
CSRF_CALL = re.compile(r"\bcsrf_token\(\)")
FLASH_CALL = re.compile(r"\bget_flashed_messages\(([^)]*)\)")
G_ATTR = re.compile(r"\bg\.([a-zA-Z_]\w*)")

def transform(src: str) -> str:
    src = URL_FOR.sub(lambda m: f"url_for('{m.group(1)}_{m.group(2)}'", src)
    src = STATIC_URL_FOR.sub(lambda m: f"script_root ~ '/static/' ~ {m.group(1)}", src)
    src = SCRIPT_ROOT.sub("script_root", src)
    src = CSRF_CALL.sub("csrf_token", src)
    src = FLASH_CALL.sub(
        lambda m: f"get_flashed_messages(request{', ' + m.group(1) if m.group(1).strip() else ''})",
        src,
    )
    src = G_ATTR.sub(lambda m: m.group(1), src)
    return src
```

Run against 72 files, manual diff audit per-file.

### 12.4 Validator guard (`tests/admin/test_templates_url_for_resolves.py`)

```python
def test_all_url_for_calls_resolve():
    from src.app import app
    route_names = {r.name for r in app.routes if hasattr(r, "name")}
    violations = []
    for tpl_path, calls in _walk_templates():
        for name in calls:
            if name not in route_names:
                violations.append((tpl_path, name))
    assert not violations, f"Unresolved url_for names: {violations}"
```

Runs under `make quality`. Catches silent template breakage at boot time.

### 12.5 Tricky files to audit manually

- **`add_product_gam.html`** — 15 `url_for` inside JavaScript template literals
- **`tenant_settings.html`** — 12 `url_for` + 12 `session.X` (biggest single file)
- **`base.html`** — 3 `session.X` in header gating, must keep visual behavior identical
- **`policy_*.html`** (4 files, 13 `session.X`) — confirm only reads, no writes

---

## 13. Three Worked Route Examples (from real `accounts.py`)

### 13.1 `list_accounts` — GET with query arg, UoW, template

**Before (Flask):**
```python
@accounts_bp.route("/")
@require_tenant_access()
def list_accounts(tenant_id):
    status_filter = request.args.get("status")
    with AccountUoW(tenant_id) as uow:
        accounts = uow.accounts.list_all(status=status_filter)
        return render_template("accounts_list.html", tenant_id=tenant_id, accounts=accounts, ...)
```

**After (FastAPI-native):**
```python
from typing import Annotated
from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from src.admin.templating import render
from src.admin.deps.auth import CurrentTenantDep
from src.core.database.repositories.uow import AccountUoW

# CORRECTED per deep audit blockers #2, #4:
#   redirect_slashes=True → matches Flask permissive default (111 url_for calls)
#   include_in_schema=False → keeps /openapi.json equal to AdCP REST surface
router = APIRouter(tags=["admin-accounts"], redirect_slashes=True, include_in_schema=False)

_STATUSES = ["active", "pending_approval", "rejected", "payment_required", "suspended", "closed"]

@router.get(
    "/tenant/{tenant_id}/accounts",
    name="accounts_list_accounts",
    response_class=HTMLResponse,
)
def list_accounts(                    # ← sync def (NOT async def) per deep audit blocker #4
    tenant_id: str,
    request: Request,
    tenant: CurrentTenantDep,
    status: Annotated[str | None, Query()] = None,
) -> HTMLResponse:
    """FastAPI auto-offloads sync handlers to a threadpool worker, so each request
    gets its own thread identity → scoped_session isolates correctly.

    Async is reserved for OAuth callbacks, SSE, and outbound httpx. See deep audit §1.4.
    """
    with AccountUoW(tenant_id) as uow:
        accounts = uow.accounts.list_all(status=status)
    return render(request, "accounts_list.html", {
        "tenant_id": tenant_id, "tenant": tenant, "accounts": accounts,
        "status_filter": status, "statuses": _STATUSES,
    })
```

**Changes labeled:** verb-explicit decorator, `name=` for `url_for`, auth via `CurrentTenantDep`, declarative `Query()`, **sync `def` handler (not `async def`)** — FastAPI auto-offloads to threadpool so each worker thread has its own `scoped_session` identity (prevents transaction interleaving per deep audit blocker #4), `render()` wrapper, explicit return type, `redirect_slashes=True` + `include_in_schema=False` on the router.

### 13.2 `create_account` — GET + POST split into two handlers

**After — two handlers (Flask conflation was an accident, not a design):**

```python
@router.get(
    "/tenant/{tenant_id}/accounts/create",
    name="accounts_create_account_form",
    response_class=HTMLResponse,
)
async def create_account_form(
    tenant_id: str, request: Request, tenant: CurrentTenantDep,
) -> HTMLResponse:
    return render(request, "create_account.html", {"tenant_id": tenant_id, "edit_mode": False})

@router.post(
    "/tenant/{tenant_id}/accounts/create",
    name="accounts_create_account",
    dependencies=[Depends(audit_action("create_account"))],
)
async def create_account(
    tenant_id: str, request: Request, tenant: CurrentTenantDep,
    name: Annotated[str, Form()],
    brand_domain: Annotated[str, Form()] = "",
    operator: Annotated[str, Form()] = "",
    billing: Annotated[str, Form()] = "",
    payment_terms: Annotated[str, Form()] = "",
    sandbox: Annotated[str, Form()] = "",
    brand_id: Annotated[str, Form()] = "",
) -> RedirectResponse:
    if not name.strip():
        flash(request, "Account name is required.", "error")
        return RedirectResponse(
            request.url_for("accounts_create_account_form", tenant_id=tenant_id),
            status_code=303,
        )
    # ... create account ...
    flash(request, f"Account '{name}' created successfully.", "success")
    return RedirectResponse(
        request.url_for("accounts_list_accounts", tenant_id=tenant_id),
        status_code=303,
    )
```

**Changes labeled:** GET+POST split, `Form()` parameters declarative, audit via `dependencies=[...]`, `flash(request, ...)` explicit, `RedirectResponse(..., status_code=303)` spec-correct.

### 13.3 `change_status` — POST JSON API (category 1, native error shape)

```python
from pydantic import BaseModel, ConfigDict

class StatusChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    status: str

class StatusChangeResponse(BaseModel):
    success: bool
    status: str

@router.post(
    "/tenant/{tenant_id}/accounts/{account_id}/status",
    name="accounts_change_status",
    response_model=StatusChangeResponse,
    dependencies=[Depends(audit_action("change_account_status"))],
)
async def change_status(
    tenant_id: str, account_id: str, payload: StatusChangeRequest,
    tenant: CurrentTenantDep, request: Request,
) -> StatusChangeResponse:
    # CSRF validation happens in CSRFMiddleware (applied globally)
    def _update():
        with AccountUoW(tenant_id) as uow:
            account = uow.accounts.get_by_id(account_id)
            if account is None:
                raise HTTPException(status_code=404, detail="Account not found.")
            allowed = _STATUS_TRANSITIONS.get(account.status, set())
            if payload.status not in allowed:
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot transition from '{account.status}' to '{payload.status}'.",
                )
            uow.accounts.update_status(account_id, payload.status)
            return StatusChangeResponse(success=True, status=payload.status)
    return await run_in_threadpool(_update)
```

**This is a category-1 endpoint** (internal admin AJAX). Native `{"detail": "..."}` error shape. Admin UI JS updated in same PR.

### 13.4 Category-2 error-shape compat handler

`tenant_management_api`, `sync_api`, `gam_reporting_api` may have external consumers. They preserve the legacy error shape via a scoped exception handler:

```python
# src/admin/routers/_legacy_error_shape.py  (~30 LOC)
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

_LEGACY_PATH_PREFIXES = (
    "/api/v1/tenant-management",
    "/api/v1/sync",
    "/api/sync",
)

async def legacy_error_shape_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Preserve pre-v2.0 {'success': false, 'error': '...'} shape for external JSON APIs."""
    if not any(request.url.path.startswith(p) for p in _LEGACY_PATH_PREFIXES):
        raise exc  # fall through to FastAPI default
    return JSONResponse(
        status_code=exc.status_code,
        content={"success": False, "error": exc.detail},
    )
```

Registered via `app.add_exception_handler(HTTPException, legacy_error_shape_handler)`.

---

## 14. Migration Strategy — 4 Waves (not 8)

The "written today" framing pushes toward fewer, bigger, atomic PRs. Eight waves presume backward-compat matters — it doesn't here. But one giant PR (~18,000 LOC) is unreviewable. Four waves keep each PR at ~one week of work.

**Flask catch-all stays live until Wave 3 as the migration safety net.**

### Wave 0 — Foundation + template codemod (~2,500 LOC)

- Add `src/admin/templating.py`, `flash.py`, `sessions.py`, `oauth.py`, `csrf.py`
- Add `src/admin/deps/auth.py` with Annotated aliases
- Add middleware modules — not wired yet
- Add `src/admin/app_factory.py` with empty `build_admin_router()`
- Write `scripts/codemod_templates.py`
- **Run the codemod across all 72 templates**
- Add `tests/admin/test_templates_url_for_resolves.py` (green against empty router)
- Extend `tests/harness/_base.py` with `IntegrationEnv.get_admin_client()`
- **Does NOT:** remove Flask, change pyproject.toml, modify `src/app.py` wiring

**Mergeability:** fully green, Flask still serving everything.

### Wave 1 — Foundational routers + session cutover (~4,000 LOC)

- Port `public.py`, `core.py`, `auth.py`, `oidc.py` → `src/admin/routers/`
- Wire `SessionMiddleware`, `CSRFMiddleware`, `ApproximatedExternalDomainMiddleware` in `src/app.py`
- Comment out `register_blueprint` calls in old `src/admin/app.py`
- Flask catch-all serves everything else
- **Cookie name `session` → `adcp_session`** → forced re-login

### Wave 2 — Bulk blueprint migration (~9,000 LOC)

Port every remaining blueprint in one PR:
- accounts, products, principals, users, tenants, gam, inventory, inventory_profiles
- creatives, creative_agents, signals_agents, operations, policy, settings
- adapters, authorized_properties, publisher_partners, workflows
- api, format_search, schemas, tenant_management_api, sync_api, gam_reporting_api

Delete Flask blueprint files. **Delete dead code:** `src/services/gam_inventory_service.py::create_inventory_endpoints`. **Delete adapter `register_ui_routes` hooks** — re-home into `src/admin/routers/adapters.py`.

**Branch lifetime target: 1 week.** Announce `src/admin/` freeze during the wave.

### Wave 3 — Activity stream SSE + cleanup cutover (~2,500 LOC)

- Port `activity_stream.py` to `sse-starlette.EventSourceResponse`
- Remove `flask`, `flask-caching`, `flask-socketio`, `python-socketio`, `simple-websocket`, `waitress`, `a2wsgi`, `types-waitress` from `pyproject.toml`
- Delete `src/admin/app.py` (old Flask factory)
- Delete `_install_admin_mounts`, `flask_admin_app`, `admin_wsgi`, `CustomProxyFix` from `src/app.py`
- Delete `/a2a/` trailing-slash redirect and `routes.insert(0,...)` hack
- Replace `.pre-commit-hooks/check_route_conflicts.py` with FastAPI-aware version
- Move `/templates/` → `src/admin/templates/` and `/static/` → `src/admin/static/`
- Add structural guard `tests/unit/test_architecture_no_flask_imports.py`
- Release notes + v2.0.0 CHANGELOG

### Why not 8 waves?

Eight waves imply safety via backward-compat seams — exactly what the user rejected. Three working seams (Wave 0 templates done early, Wave 1 session cutover, Wave 2 bulk with Flask catch-all as safety net) give 95% of the safety of eight waves without 5× coordination overhead.

### Why not one big PR?

~18,000 LOC is unreviewable. Wave 0 alone (templates + foundations) is ~2,500 LOC, already at the top of reviewable.

---

## 15. Dependency Changes (for v2.0.0 release notes)

**REMOVED:**
- `flask>=3.1.3`
- `flask-caching>=2.3.0` (zero callers)
- `flask-socketio>=5.5.1` (declared but unused)
- `python-socketio>=5.13.0` (transitive of flask-socketio)
- `simple-websocket>=1.1.0` (transitive of flask-socketio)
- `waitress>=3.0.0`
- `a2wsgi>=1.10.0`
- `types-waitress` (dev)

**ADDED:**
- `sse-starlette>=2.2.0` (promoted from transitive)
- `pydantic-settings>=2.7.0` (typed config)
- `itsdangerous>=2.2.0` (explicit pin; Starlette transitive; now also used by roll-your-own CSRF)

**NOT ADDED (explicit rejection):** `fastapi-csrf-protect`, `starlette-csrf`, `fastapi-csrf-jinja`, `csrf-starlette-fastapi` — CSRF is implemented in-tree.

**UPDATED (floor bumps, April 2026 stable):**
- `fastapi>=0.128.0` (already present)
- `starlette>=0.50.0` (already present)
- `pydantic>=2.10.0`
- `sqlalchemy>=2.0.36`
- `uvicorn>=0.34.0`
- `authlib>=1.6.7` (now used as `starlette_client`, not `flask_client`)

**UNCHANGED but newly load-bearing:**
- `python-multipart>=0.0.22` (activates on first `Form(...)`)
- `jinja2>=3.1.0`
- `markdown>=3.4.0`

**Runtime operational changes:**
- `uvicorn --proxy-headers --forwarded-allow-ips='*'` becomes **required** in production
- `SESSION_SECRET` env var replaces `FLASK_SECRET_KEY`, **hard-required**, no fallback

---

## 16. All 28 Assumptions (tagged by confidence)

### HIGH confidence (9) — proceed without spike

1. **FastAPI 0.128 / Starlette 0.50 ABI-stable** for migration duration. Pin exact versions during Wave 2.
2. **`Annotated[T, Depends()]` is canonical 2026 FastAPI idiom.**
3. **Sync SQLAlchemy stays sync;** UoW wrapped in `run_in_threadpool`. Benchmark <5ms overhead per request.
4. **Admin handlers `async def` + `run_in_threadpool`.** Structural guard against raw UoW in async scope.
5. **Starlette `SessionMiddleware` sufficient** (payloads <3.5KB).
6. **`SESSION_SECRET` set in every deploy.** Hard `KeyError` at startup.
7. **Admins tolerate one forced re-login** at cutover.
8. **`authlib.starlette_client.OAuth` feature-parity** with `flask_client` for Google OpenID.
9. **Route name translation `bp.endpoint` → `bp_endpoint` unique/stable.** Collision detection in validator.

### MEDIUM confidence (12) — verify before/during Wave 2

10. **Roll-your-own CSRF secure and correct.** Unit tests, Playwright test, security review of middleware body-read path.
11. **`sse-starlette` disconnect detection works** behind nginx + Fly. Backstop: `MAX_CONNECTIONS_PER_TENANT`.
12. **`uvicorn --proxy-headers --forwarded-allow-ips='*'` sufficient.** No custom ProxyFix.
13. **Test harness extension `get_admin_client()` lands in Wave 0.** Structural guard for migration.
14. **BDD admin scenarios stay excluded from cross-transport parametrization.**
15. **Codemod regex handles JS template literal `url_for`** (audit `add_product_gam.html`).
16. **No nginx config change needed.** Grep `config/nginx/*`.
17. **`/admin/` URL prefix stays** (bookmarks/docs/runbooks reference it).
18. **No external consumer depends on Flask-specific JSON error shape for category-1 endpoints.** Category 2 preserved via compat handler.
19. **`request.url_for()` resolves across `include_router(prefix=...)` nesting.**
20. **Super-admin flows fully expressible as `SuperAdminDep`.**
21. **`FlyHeadersMiddleware` may already be redundant** (Fly added standard `X-Forwarded-*` mid-2024).

### LOW confidence (7) — audit before cutover

22. **`SessionMiddleware` + SameSite=None prod tabs work.** Playwright test.
23. **No monitoring parses old `[SESSION_DEBUG]` log lines.** Grep deploy configs.
24. **`test_mode` global injectable via small dep** without leaking test surface.
25. **`tenant_management_api`, `sync_api`, `gam_reporting_api` are thin wrappers.** Manual read-through in Wave 2.
26. **`get_rest_client()` pattern extends cleanly to `get_admin_client()`.**
27. **3 `try/except ImportError` blocks in Flask factory are vestigial.** Unconditional imports work.
28. **Docker image shrinks ~80 MB** after Flask removals.

---

## 17. All 15 Debatable Surfaces (resolved + counterarguments)

1. **Module layout: `src/admin/` (chosen) vs `src/web/admin/` vs `src/routes/admin/`** — `src/web/admin/` signals presentation layer; `src/routes/admin/` mirrors REST. Counter: both cause import churn for marginal gain. **Chosen: keep `src/admin/`, rewrite contents.**
2. **Sync vs async SQLAlchemy** — async unlocks `async with` UoW. Counter: touches 100+ files, triples scope. **Chosen: sync + `run_in_threadpool`, async deferred to v2.1.**
3. ~~**CSRF library**~~ **RESOLVED → roll-your-own Double Submit Cookie (~100 LOC).** Zero external dep.
4. **`SessionMiddleware` cookie vs Redis server-side** — Redis if payloads grow. Counter: payloads stay under 4KB. **Chosen: signed cookies.**
5. **`BaseHTTPMiddleware` vs pure ASGI** — `BaseHTTPMiddleware` easier but Starlette #1729. **Chosen: pure ASGI.**
6. **Wave count: 4 (chosen) vs 2 vs 8** — 2 unreviewable, 8 too much coordination.
7. **Port SSE vs drop activity stream** — dropping saves ~400 LOC. Counter: user-visible. **Chosen: port.**
8. **Per-tenant OIDC complexity** — simplification drops ~150 LOC. Counter: multi-tenant is a product requirement. **Chosen: keep.**
9. **Dual-mode `require_tenant_access` split into two deps** — doubles dep count. **Chosen: split** (single-responsibility composes better).
10. **Keep `/admin/` prefix vs move to root** — every bookmark says `/admin/`. **Chosen: keep.**
11. **Audit decorator as Dep vs middleware** — Dep runs before handler, can't capture return. Counter: existing decorator already fires before return. **Chosen: Dep.**
12. **Flat route names `bp_endpoint` vs dotted `bp.endpoint`** — dotted matches Flask mental model. Counter: flat is FastAPI convention. **Chosen: flat.**
13. **Single admin router vs one per feature** — flat loses OpenAPI grouping. **Chosen: one per feature** with `tags=["admin-accounts"]`.
14. **Delete adapter `register_ui_routes` hooks vs port** — dependency inversion violation. **Chosen: delete**, re-home into `src/admin/routers/adapters.py`.
15. **`gam_reporting_api` port vs defer** — self-contained. **Chosen: bundle into Wave 2.**

---

## 18. Future Work: v2.1 Async SQLAlchemy (separate PR)

**v2.1 is a follow-on PR that depends on v2.0 being merged first.**

**Scope:**
- Convert `create_engine` → `create_async_engine` in `src/core/database/database_session.py`
- Convert `Session` → `AsyncSession` via `async_sessionmaker`
- Convert all repository classes to `async def` methods
- Convert UoW classes to `async with` context managers
- **Delete every `run_in_threadpool(_sync_fn)` wrapper** in admin routers
- Update ~100+ files that import `get_db_session`
- Pin floors: `sqlalchemy[asyncio]>=2.0.36`, `asyncpg>=0.30.0`

**Why separable:** v2.0 establishes clean seams by making every admin handler `async def` with explicit `run_in_threadpool(_sync_fn)` calls. v2.1 replaces each `run_in_threadpool` call-site one at a time — the handler signature stays identical, only the body changes.

**Why NOT in v2.0:** Going async touches 100+ files beyond admin. Bundling would triple v2.0's scope, extend branch lifetime to 3-4 weeks.

---

## 19. Natural Flow Changes

### Developers

| Flask | FastAPI |
|---|---|
| `@accounts_bp.route("/")` | `@router.get("/tenant/{tenant_id}/accounts", name="...", response_class=HTMLResponse)` |
| `request.args.get("x")` | `x: Annotated[str \| None, Query()] = None` |
| `request.form.get("x")` | `x: Annotated[str, Form()]` |
| `request.get_json()` | Typed Pydantic model parameter |
| `flash(msg)` | `flash(request, msg)` |
| `redirect(url_for("bp.ep"))` | `RedirectResponse(request.url_for("bp_ep"), status_code=303)` |
| `@require_tenant_access()` | `tenant: CurrentTenantDep` parameter |
| `g.user` | dep parameter |
| `flask.session["k"] = v` | `request.session["k"] = v` |
| `render_template("x.html", k=v)` | `render(request, "x.html", {"k": v})` |
| `@bp.before_request` | Middleware or `APIRouter(dependencies=[...])` |
| `@bp.errorhandler(404)` | `@app.exception_handler(...)` app-level |

### End users

- URLs unchanged (admin still at `/admin/`)
- One forced re-login at cutover
- CSRF tokens required on form POSTs
- Admin UI internal AJAX error shape changes to `{"detail": "..."}`
- External admin JSON APIs preserve `{"success": false, "error": "..."}`

### Operators

- Docker image shrinks ~80 MB
- `uvicorn --proxy-headers --forwarded-allow-ips='*'` required in production
- `SESSION_SECRET` env var hard-required
- No more `[SESSION_DEBUG]` log lines
- Single-process topology unchanged

### Testers

- `app.test_client()` → `IntegrationEnv.get_admin_client()` (new in Wave 0)
- Session priming via `session_transaction()` → `app.dependency_overrides`
- BDD admin scenarios stay excluded from 4-transport parametrization
- Redirect assertions: `302` → `303` audit
- `g.user` assertions disappear

### CI

- Drop `.pre-commit-hooks/check_route_conflicts.py` (Flask-aware) → FastAPI-aware rewrite
- Add `tests/admin/test_templates_url_for_resolves.py`
- Add `tests/unit/test_architecture_no_flask_imports.py` (ratchets per wave)

---

## 20. Critical Files to Modify

### Files created (Wave 0 foundation)
- `src/admin/templating.py` (~120 LOC)
- `src/admin/flash.py` (~70 LOC)
- `src/admin/csrf.py` (~100 LOC)
- `src/admin/sessions.py` (~40 LOC)
- `src/admin/oauth.py` (~60 LOC)
- `src/admin/app_factory.py` (~80 LOC)
- `src/admin/deps/auth.py` (~220 LOC)
- `src/admin/deps/tenant.py` (~90 LOC)
- `src/admin/deps/audit.py` (~110 LOC)
- `src/admin/middleware/external_domain.py` (~90 LOC)
- `src/admin/middleware/fly_headers.py` (~40 LOC)
- `scripts/codemod_templates.py` (~80 LOC)
- `tests/admin/test_templates_url_for_resolves.py`
- `tests/unit/test_architecture_no_flask_imports.py`
- `src/admin/routers/*.py` (25 files, ~8,000 LOC across Waves 1-3)

### Files modified
- `src/app.py` — middleware stack, router registration, Flask mount removal
- `src/core/auth_middleware.py` — ensure interop with new session middleware
- `tests/harness/_base.py` — add `IntegrationEnv.get_admin_client()` in Wave 0
- `pyproject.toml` — dep removal/addition (Waves 1 & 3)
- `.pre-commit-hooks/check_route_conflicts.py` — rewrite for FastAPI

### Files DELETED (Waves 2 & 3)
- `src/admin/app.py` (old Flask factory)
- `src/admin/blueprints/*.py` (25+ files)
- `src/admin/utils/helpers.py::require_auth`, `require_tenant_access`
- `src/services/gam_inventory_service.py::create_inventory_endpoints` (dead code)
- `src/adapters/google_ad_manager.py::register_ui_routes`
- `src/adapters/mock_ad_server.py::register_ui_routes`
- `/templates/` directory (moved to `src/admin/templates/`)
- `/static/` directory (moved to `src/admin/static/`)

---

## 21. Verification Strategy

### Per-wave gate

Every PR must satisfy:

- [ ] Old Flask test files for migrated routes **deleted** (not skipped/xfail'd per `tests/CLAUDE.md` zero-tolerance)
- [ ] New FastAPI test files with coverage ≥ deleted coverage
- [ ] New tests use `IntegrationEnv.get_admin_client()` — no direct `TestClient(app)`
- [ ] New tests use factory-boy, never raw `session.add(...)`
- [ ] `make quality` passes
- [ ] `tox -e integration` passes
- [ ] `tox -e bdd` passes
- [ ] `test_architecture_no_flask_imports.py` allowlist: migrated files removed
- [ ] `.duplication-baseline` regenerated
- [ ] `test_templates_url_for_resolves.py` passes
- [ ] Pre-commit `check-route-conflicts` passes

### End-to-end verification (Wave 3 cutover)

- `rg -w flask src/` returns zero hits
- `rg 'from flask' tests/` returns zero hits
- `make quality` + `./run_all_tests.sh` both green
- Playwright happy path: login → create account → create product → delete → logout → re-login
- CSRF happy path: POST with token → 200; POST without → 403
- Staging deploy: session invalidation forces re-login (expected)
- Request latency p50/p99 vs baseline
- Docker image size delta

### Coverage parity check (per wave)

1. Checkout PR base, `make test-cov`, record `coverage.json`
2. Record per-file percentages for deleted blueprint files
3. Checkout PR head, `make test-cov` again
4. Record per-file percentages for new router files
5. Assert new ≥ old − 1 (1-point fudge for noise)
6. Paste before/after table in PR description

---

## 22. Sources (verified April 2026)

- [FastAPI Features — Annotated Depends, lifespan](https://fastapi.tiangolo.com/features/)
- [FastAPI Templates](https://fastapi.tiangolo.com/advanced/templates/)
- [Authlib Starlette OAuth Client](https://docs.authlib.org/en/latest/client/starlette.html)
- [Pydantic v2 + FastAPI migration](https://github.com/fastapi/fastapi/discussions/9709)
- [FastAPI at Scale in 2026 — Pydantic v2, uvloop, HTTP/3](https://medium.com/@kaushalsinh73/fastapi-at-scale-in-2026-pydantic-v2-uvloop-http-3-which-knob-moves-latency-vs-throughput-cd0a601179de)
- [FastAPI Latest Version / Setup Guide 2026](https://www.zestminds.com/blog/fastapi-requirements-setup-guide-2025/)
- [fastapi-csrf-protect on PyPI](https://pypi.org/project/fastapi-csrf-protect/)
- [starlette-csrf on PyPI](https://pypi.org/project/starlette-csrf/)
- [SQLAlchemy 2.0 async + FastAPI (Medium)](https://medium.com/@tclaitken/setting-up-a-fastapi-app-with-async-sqlalchemy-2-0-pydantic-v2-e6c540be4308)
- [FastAPI best practices — zhanymkanov/fastapi-best-practices](https://github.com/zhanymkanov/fastapi-best-practices)
- [Starlette GitHub issue #1729 — BaseHTTPMiddleware ContextVar propagation](https://github.com/encode/starlette/issues/1729)

---

## Appendix A: How this document was produced

Research and design was gathered during a single Claude Code session on 2026-04-11:

- **3 parallel Opus Explore subagents** (Phase 1): Flask inventory, FastAPI inventory, test infrastructure inventory — each instructed with detailed search guidance and asked to produce structured ~600-1000 word reports with concrete file paths and line numbers.
- **3 parallel Opus Plan subagents** (Phase 2): foundation layer design, blueprint migration sequence, test migration design.
- **1 additional Opus Plan subagent** (Phase 3): side-by-side comparison of Option A (shim) vs Option B (codemod) trade-offs.
- **1 additional Opus Plan subagent** (Phase 4): full FastAPI-native 2026 redesign with user-confirmed directives.
- **Web research (Phase 4)**: 4 parallel `WebSearch` calls for FastAPI best practices 2026, Authlib Starlette, CSRF libraries, async SQLAlchemy. 2 parallel `WebFetch` calls for Authlib Starlette docs and FastAPI templates docs.
- **Direct file reading**: `src/app.py`, `src/admin/blueprints/accounts.py`, `src/routes/api_v1.py`, `src/core/auth_context.py`, `tests/harness/_base.py`, `templates/base.html`.

**User decisions gathered via 2 rounds of AskUserQuestion:**
- Template strategy (Option B), CSRF (roll-your-own), URL prefix (`/admin/`), secret handling (hard-required)
- Async DB (separate v2.1 PR), error-shape split (category 1 native / category 2 compat)

**This document is canonical for v2.0.0 Flask → FastAPI migration planning.** When a future session needs to revisit this work, start here, then consult the plan file at `/Users/quantum/.claude/plans/squishy-meandering-marshmallow.md` for the latest snapshot.
