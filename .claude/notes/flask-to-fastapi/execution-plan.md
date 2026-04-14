# Flask → FastAPI v2.0.0 — Execution Plan

**Status:** Self-contained, phase-ordered implementation guide.
**Each phase is a standalone briefing. No cross-referencing required.**
**Last updated:** 2026-04-14 (v2.0 now includes full async, strategically layered after Flask removal)

> **Async SQLAlchemy is Phase 4+ (after Flask removal).** The async-audit reports in
> `async-audit/` contain comprehensive research for the Phase 4+ async migration.
> Phases 0-3 focus on Flask removal with sync admin handlers. No `asyncpg`, no
> `async_sessionmaker`, no `expire_on_commit=False`, no lazy-load audit, no `SessionDep`,
> no `AsyncSession` until Phase 4. Admin handlers use sync `def` with
> `with get_db_session() as session:` through Phase 3. FastAPI runs sync handlers in its
> default threadpool — no `run_in_threadpool` wrappers needed for DB access until Phase 4.
> Testing uses Starlette `TestClient` (sync) through Phase 3, then httpx `AsyncClient`
> in Phase 4+. Factory-boy works as-is with no async shim through Phase 3.

> **How to use this file:** Read ONE phase section. It contains everything you need —
> goal, prerequisites, knowledge sources, work items in order (tests first per TDD),
> files to touch, exit gate, and scope warnings. The `[§X-Y]` references point back
> to `implementation-checklist.md` for full detail when needed, but you should NOT
> need to open it during implementation.
>
> **Relationship to other docs:**
> - `implementation-checklist.md` — verification/tracking document (tick boxes after work is done)
> - `execution-plan.md` (this file) — **what to do, in what order** (read before coding)
> - Knowledge source files — deep reference (read when noted in "Knowledge to read")
> - `async-audit/` — **Phase 4+ reference**; do not implement anything from these reports during Phases 0-3

---

## Phase 0 — Foundation modules + codemod script (~2,500 LOC, ~5-7 days)

**Goal:** Land all foundation modules, write template codemod script (do NOT execute it — execution moves to Phase 1a), create structural guards. Flask still serves 100% of traffic. Pure addition — nothing changes behavior.

**Prerequisites:** `main` green (`make quality` + `tox -e integration` + `tox -e bdd`). Branch `feat/v2.0.0-flask-to-fastapi` exists.

**Knowledge to read:**
- `flask-to-fastapi-foundation-modules.md` §11.1-11.15 — module implementations with code (read with sync lens — ignore any `async def` signatures; all admin-facing functions become sync `def`)
- `flask-to-fastapi-migration.md` §11-12 — module descriptions + codemod details
- `async-audit/frontend-deep-audit.md` — 7 critical blockers for templates/JS/OAuth (still relevant for template and JS patterns; ignore async DB content)
- `flask-to-fastapi-deep-audit.md` §1 — blockers 1, 2

**Sync pattern for this phase:**

All foundation modules use sync patterns. The canonical admin handler shape is:

```python
# src/admin/routers/example.py
from fastapi import APIRouter, Request, Depends
from src.core.database.database_session import get_db_session
from src.admin.templating import render
from src.admin.deps.auth import require_tenant_access

router = APIRouter(redirect_slashes=True, include_in_schema=False)

@router.get("/accounts", name="admin_accounts_list")
def list_accounts(
    request: Request,
    tenant_context: dict = Depends(require_tenant_access),
):
    """Sync handler — FastAPI runs this in a threadpool automatically."""
    tenant_id = tenant_context["tenant_id"]
    with get_db_session() as session:
        repo = AccountRepository(session)
        accounts = repo.list_by_tenant(tenant_id)
    return render(request, "accounts/list.html", {"accounts": accounts})
```

Key rules:
- `def`, NOT `async def` — FastAPI auto-dispatches sync handlers to `anyio.to_thread`
- `with get_db_session() as session:` — sync context manager, threadpool-safe
- No `SessionDep`, no `Depends(get_session)` for DB — handler owns the session lifecycle
- Repository instantiated inside the `with` block, used, session closes on block exit
- `require_tenant_access` (or equivalent) as a `Depends()` for auth/tenant resolution

**Work items (in order):**

1. Write structural guard tests FIRST (TDD): `test_templates_url_for_resolves.py`, `test_templates_no_hardcoded_admin_paths.py`, `test_architecture_admin_routes_named.py`, `test_codemod_idempotent.py`, `test_oauth_callback_routes_exact_names.py` (pins: `/admin/auth/google/callback`, `/admin/auth/oidc/callback`, `/admin/auth/gam/callback`), `test_trailing_slash_tolerance.py`, `test_architecture_no_flask_imports.py` (full allowlist), `test_architecture_handlers_use_sync_def.py` (AST guard: admin handlers must be `def`, NOT `async def` — this is the Phases 0-3 sync invariant), `test_architecture_no_async_db_access.py` (no `async with get_db_session()` in admin code), `test_architecture_no_module_level_engine.py`, `test_architecture_no_direct_env_access.py`, `test_architecture_middleware_order.py`, `test_architecture_exception_handlers_complete.py`, `test_architecture_csrf_exempt_covers_adcp.py`, `test_architecture_approximated_middleware_path_gated.py`, `test_architecture_admin_routes_excluded_from_openapi.py`, `test_architecture_scheduler_lifespan_composition.py`, `test_architecture_a2a_routes_grafted.py`, `test_foundation_modules_import.py`, `test_template_context_completeness.py`, `test_architecture_form_getlist_parity.py` [§4 Wave 0, §3.5.1 SB-2/SB-3]. Every allowlist entry gets a `FIXME(salesagent-xxxx)` comment at its source location [§3.5.6 EP-5].
1a. Add 9 AdCP boundary protective tests: `test_openapi_byte_stability.py`, `test_mcp_tool_inventory_frozen.py`, A2A agent card snapshot, `test_architecture_approximated_middleware_path_gated.py`, `test_architecture_csrf_exempt_covers_adcp.py`, `test_architecture_admin_routes_excluded_from_openapi.py`, error shape contract test, schema discovery contract test, REST response wire test. These protect the AdCP surface from accidental breakage during the migration.
2. Create foundation modules (ALL sync `def`): `templating.py` (~150 LOC — `render()` wrapper passes `test_mode`, `user_role`, `user_email`, `user_authenticated`, `username`, `support_email` (from `get_support_email()`), `sales_agent_domain` (from `get_sales_agent_domain()`) as context — matching Flask's `inject_context()` at `src/admin/app.py:298-330`; registers `tojson` filter with HTML-escaping; registers `from_json` and `markdown` custom Jinja2 filters from `src/admin/app.py:154-155`), `flash.py` (~70), `sessions.py` (~40 — SameSite=Lax in ALL environments per CSRF decision), `oauth.py` (~60), `csrf.py` (~120 — `CSRFOriginMiddleware`, Origin header validation, SameSite=Lax strategy, NOT Double Submit Cookie), `app_factory.py` (~80), `deps/auth.py` (~260, sync `def` dependency functions), `deps/tenant.py` (~90, sync), `deps/audit.py` (~110, sync — port `src/admin/utils/audit_decorator.py` Flask `g`/`request`/`session` usage to FastAPI Depends pattern), `middleware/external_domain.py` (~90, status 307), `middleware/fly_headers.py` (~40) [§4 Wave 0, §2 Blockers 1-2, §3.5.1 SB-3].
3. Create `form_error_response()` shared helper for DRY form-validation re-rendering across 25 routers [§3.5.6 EP-3].
4. Create feature flag routing toggle `ADCP_USE_FASTAPI_ADMIN` (~50 LOC) [§3.5.6 EP-1].
5. Create `X-Served-By` header middleware (~20 LOC) [§3.5.6 EP-2].
6. Write `scripts/generate_route_name_map.py` (~50 LOC) — introspects Flask `url_map` [§2-B1].
7. Write `scripts/codemod_templates_greenfield.py` (~200 LOC) — Pass 0 (csrf, g.*, flash), Pass 1a (static), Pass 1b (hardcoded paths), Pass 2 (Flask-dotted names) [§2-B1].
8. **Write** `scripts/codemod_templates_greenfield.py` is complete but do **NOT run it** in Phase 0 — all 4 passes break Flask's `url_for` while Flask still serves traffic. Execution moves to Phase 1a. Manual audit of `add_product_gam.html`, `base.html`, `tenant_dashboard.html` deferred to Phase 1a [§2-B1].
9. Document `request.form.getlist()` → `List[str] = Form()` migration pattern in worked examples [§3.5.7 CP-2].
10. Write golden-fixture capture infrastructure: `tests/migration/fingerprint.py`, `tests/migration/conftest_fingerprint.py`, `tests/migration/test_response_fingerprints.py`, `tests/migration/fixtures/fingerprints/*.json` [§3.5.5 TI-1]. Uses Starlette `TestClient` (sync).
11. Add harness extension: `IntegrationEnv.get_admin_client()` returning Starlette `TestClient` with `dependency_overrides` snapshot/restore [§4 Wave 0].
12. Write `tests/integration/test_schemas_discovery_external_contract.py` [§3 audit action #4].
13. Complete §1.1 prerequisites: `SESSION_SECRET` in `.env.example` and secret stores, OAuth URI docs, external consumer contract confirmation [§1.1].

**Files to create:** Foundation modules under `src/admin/`, 2 scripts, 20+ test files, golden-fixture infrastructure.
**Files to modify:** `tests/harness/_base.py`, `pyproject.toml` (add `itsdangerous>=2.2.0`, `pydantic-settings>=2.7.0` as explicit deps — currently transitive via Flask/pydantic-ai).
**Note:** Templates are NOT modified in Phase 0 — codemod execution is Phase 1a.

**Exit gate:**
```bash
make quality && tox -e integration && tox -e bdd && ./run_all_tests.sh  # all green
python -c "import ast; ast.parse(open('scripts/codemod_templates_greenfield.py').read())"  # script parses
```
Note: Codemod idempotency check and url_for count check move to Phase 1a exit gate (after codemod runs).

**What NOT to do:** Do not modify `src/app.py` (no middleware, no router inclusion). Do not delete any Flask files. Flask serves 100% of `/admin/*` traffic. Do not use `async def` in any admin-facing handler or dependency. Do not add `asyncpg`, `async_sessionmaker`, or `SessionDep`. Do not implement anything from `async-audit/` reports — those are Phase 4+ scope.

---

## Phase 1a — Middleware stack + public/core routers (~1,800 LOC, ~3-4 days)

**Goal:** Wire middleware in correct order, port public + core routers, **run template codemod** (all 4 passes, atomically with FastAPI activation). FastAPI serves these routes; Flask catch-all handles everything else.

**Prerequisites:** Phase 0 merged. `SESSION_SECRET` live in staging.

**Knowledge to read:**
- `flask-to-fastapi-worked-examples.md` §4.1 — OAuth login worked example (adapt to sync)
- `flask-to-fastapi-deep-audit.md` §1 — Blocker 5 (middleware ordering)
- `flask-to-fastapi-foundation-modules.md` §11.4 — deps/auth.py (read with sync lens)

**Sync pattern for this phase:**

```python
# Public route — no auth, no DB
@router.get("/login", name="admin_public_login")
def login_page(request: Request):
    return render(request, "login.html", {})

# Core route — auth + DB
@router.get("/dashboard", name="admin_core_dashboard")
def dashboard(
    request: Request,
    tenant_context: dict = Depends(require_tenant_access),
):
    with get_db_session() as session:
        stats = DashboardRepository(session).get_stats(tenant_context["tenant_id"])
    return render(request, "dashboard.html", {"stats": stats})
```

**Work items (in order):**

1. Write tests first: `test_external_domain_post_redirects_before_csrf.py` (Blocker 5), `test_middleware_ordering.py`. Tests use Starlette `TestClient` [§2 Blocker 5].
2. Port `src/admin/routers/public.py` (~400 LOC) — sync `def` handlers, no DB access [§4 Wave 1].
3. Port `src/admin/routers/core.py` (~600 LOC) — sync `def` handlers with `with get_db_session()` [§4 Wave 1].
4. Wire middleware stack in `src/app.py`: CORS → Session → Approximated → CSRF → RestCompat → UnifiedAuth [§2-B5].
5. Wire admin router via feature flag (`ADCP_USE_FASTAPI_ADMIN`) [§3.5.6 EP-1].
5a. **Run template codemod** (`scripts/codemod_templates_greenfield.py`) against all 73 templates — all 4 passes (csrf/g.*/flash, static, hardcoded paths, Flask-dotted names). Must run atomically with items 4-5 (FastAPI router activation + feature flag flip). All 4 passes break Flask's `url_for`, so templates must be rewritten at the same moment FastAPI takes over serving them [§2-B1].
6. Activate dual-stack shadow testing (TI-2, ~255 LOC) using `TestClient` [§3.5.5].
7. Activate response fingerprint comparison [§3.5.5 TI-1].

**Files to create:** `src/admin/routers/public.py`, `src/admin/routers/core.py`, `tests/migration/dual_stack_client.py`, test files.
**Files to modify:** `src/app.py`, 73 templates (codemod — all 4 passes).

**Exit gate:**
```bash
make quality && tox -e integration && tox -e bdd  # green
python scripts/codemod_templates_greenfield.py --check templates/  # exit 0 (idempotent)
rg -n "url_for" templates/ | wc -l                                 # >= 134
curl -s http://localhost:8000/admin/login          # served by FastAPI (check X-Served-By header)
```

**What NOT to do:** Do not port auth/OIDC (Phase 1b). Do not change session cookie name. Do not delete Flask blueprints. Do not use `async def` in any handler.

---

## Phase 1b — Auth + OIDC routers + session cutover (~2,200 LOC, ~4-5 days)

**Goal:** Port Google OAuth and OIDC login flows. Cut session cookie to `adcp_session`. Validate SameSite with OIDC `form_post`. This is the highest-risk router work in the migration.

**Prerequisites:** Phase 1a merged. Middleware passing on staging. Authlib `starlette_client` tested (Authlib's Starlette integration works with sync handlers — the OAuth redirect/callback flow is HTTP-level, not DB-level).

**Knowledge to read:**
- `flask-to-fastapi-worked-examples.md` §4.1-4.2 — OAuth + OIDC worked examples (adapt to sync)
- `flask-to-fastapi-deep-audit.md` §1 — Blockers 3, 6
- `async-audit/frontend-deep-audit.md` §3 — OAuth + session audit (ignore async DB patterns)
- `flask_migration_critical_knowledge.md` items 2, 4, 5, 6, 17

**Sync pattern for auth handlers:**

```python
@router.get("/auth/google/callback", name="admin_auth_google_callback")
def google_callback(request: Request):
    """OAuth callback — sync. Authlib handles the token exchange internally."""
    token = oauth.google.authorize_access_token(request)
    userinfo = token.get("userinfo")
    # Store in session (sync operation on Starlette session middleware)
    request.session["user_email"] = userinfo["email"]
    with get_db_session() as session:
        repo = PrincipalRepository(session)
        principal = repo.get_or_create_by_email(userinfo["email"])
        session.commit()
    return RedirectResponse(url=request.url_for("admin_core_dashboard"))
```

Note: Authlib's Starlette OAuth client may require `async def` for `authorize_access_token`. If so, ONLY the OAuth callback handlers become `async def` — this is acceptable because they do not touch the DB inside the async context. The structural guard `test_architecture_handlers_use_sync_def.py` gets an allowlist for these specific callback routes.

**Work items (in order):**

1. Write tests first: `test_admin_error_page.py` (Blocker 3), `test_oauth_redirect_uris_immutable.py` (Blocker 6 — pins `/admin/auth/google/callback`, `/admin/auth/oidc/callback`, `/admin/auth/gam/callback`), `test_oidc_form_post_samesite.py`. Tests use Starlette `TestClient` [§2 Blockers 3, 6; §3.5.3 SG-5].
2. Port `src/admin/routers/auth.py` (~1,100 LOC) — Google OAuth via Authlib. Sync `def` handlers except where Authlib requires `async def` for token exchange [§4 Wave 1].
3. Port `src/admin/routers/oidc.py` (~500 LOC) — same pattern [§4 Wave 1].
4. Implement Accept-aware `AdCPError` handler; modify existing `templates/error.html` (it already exists) [§2-B3].
4a. Add `@app.exception_handler(RequestValidationError)` for admin routes — renders form with error messages instead of JSON 422.
4b. Add `@app.exception_handler(HTTPException)` for admin routes — renders `templates/error.html` for 404/500 instead of JSON.
5. If OIDC providers use `form_post`: adjust SameSite/CSRF for that callback path [§3.5.3 SG-5].
6. Enable `adcp_session` cookie name [§1.2].
7. Verify `pyproject.toml` deps `pydantic-settings>=2.7.0` and `itsdangerous>=2.2.0` were added in Phase 0 [§4 Wave 1].
8. Send 48-hour customer communication for forced re-login [§3.5.6 EP-7].
9. Write `test_stale_flask_cookie_returns_login.py` — old `session=` cookie returns login page, not 500 [§4 Wave 1].
10. Rollback procedure tested in staging [§4 Wave 1].
11. Manual staging OAuth smoke by 2 engineers [§4 Wave 1].
12. Update `test_architecture_no_flask_imports.py` allowlist (shrink) [§4 Wave 1].

**Files to create:** `src/admin/routers/auth.py`, `src/admin/routers/oidc.py`, `templates/error.html`, test files.
**Files to modify:** `src/app.py`, `pyproject.toml`.

**Exit gate:**
```bash
make quality && tox -e integration && tox -e bdd  # green
# Manual: walk real Google OAuth flow on staging end-to-end
# test_oidc_form_post_samesite.py green
# 2 engineers confirm staging login works
```

**What NOT to do:** Do not port any blueprint beyond auth/OIDC. Comment out `register_blueprint`, do not delete. Do not begin bulk migration. Do not convert any DB access to async.

---

## Phase 2a — Low-risk HTML routers (~3,000 LOC, ~4-5 days)

**Goal:** Port 8 low-risk HTML-rendering admin blueprints. Flask catch-all still wired as safety net.

**Prerequisites:** Phase 1b merged. Stable in staging >= 3 business days. Cookie size < 3.5KB confirmed.

**Knowledge to read:**
- `flask-to-fastapi-worked-examples.md` §4.4-4.5 — products + GAM worked examples (adapt to sync)
- `flask-to-fastapi-migration.md` §3 — Flask inventory (route counts)
- `flask_migration_critical_knowledge.md` items 16 — getlist

**Sync pattern for HTML routers:**

```python
@router.get("/products", name="admin_products_list")
def list_products(
    request: Request,
    tenant_context: dict = Depends(require_tenant_access),
):
    with get_db_session() as session:
        repo = ProductRepository(session)
        products = repo.list_by_tenant(tenant_context["tenant_id"])
    return render(request, "products/list.html", {"products": products})

@router.post("/products/create", name="admin_products_create")
def create_product(
    request: Request,
    tenant_context: dict = Depends(require_tenant_access),
    name: str = Form(...),
    format_ids: list[str] = Form([]),  # getlist equivalent
):
    with get_db_session() as session:
        repo = ProductRepository(session)
        product = repo.create(name=name, format_ids=format_ids, tenant_id=tenant_context["tenant_id"])
        session.commit()
    return RedirectResponse(url=request.url_for("admin_products_list"), status_code=303)
```

**Work items (in order):**

1. Capture golden-fixture response shapes from Flask for all routes being ported [§3.5.6 EP-4].
2. Port routers (each with golden-fixture comparison test): `accounts.py`, `principals.py`, `users.py`, `settings.py`, `authorized_properties.py`, `publisher_partners.py`, `format_search.py` (4 routes), `api.py` (7 routes — dashboard AJAX). All sync `def` handlers with `with get_db_session()` [§4 Wave 2].
3. Use `list[str] = Form([])` for every multi-value form field [§3.5.1 SB-2, §3.5.7 CP-2].
4. Every route decorator has `name="admin_<bp>_<endpoint>"` [§2-B1].
5. No `adcp.types.*` as `response_model=` [§3].

**Files to create:** 8 router files under `src/admin/routers/`.

**Exit gate:**
```bash
make quality && tox -e integration && tox -e bdd  # green
# Golden fixtures match for all ported routes
```

**What NOT to do:** Do not port high-risk routers (Phase 2b). Do not port APIs or external contracts (Phase 2b). Do not delete Flask files. Do not introduce `async def` handlers.

---

## Phase 2b — Medium/high-risk routers + APIs (~5,500 LOC, ~5-7 days)

**Goal:** Port remaining 14 HTML routers (including webhook-preserving ones), 4 JSON API files with Category-2 error shape preservation. Delete Flask blueprints.

**Prerequisites:** Phase 2a merged. Team freeze announced 48h prior.

**Knowledge to read:**
- `flask-to-fastapi-adcp-safety.md` §1-7 — Category 1 vs 2 classification
- `async-audit/frontend-deep-audit.md` §1-2 — templates + JS audit (ignore async DB patterns)
- `flask_migration_critical_knowledge.md` items 11, 12

**Sync pattern for API routes:**

```python
@router.get("/api/tenants/{tenant_id}/stats", name="admin_api_tenant_stats")
def get_tenant_stats(
    request: Request,
    tenant_id: str,
    auth: dict = Depends(require_tenant_access),
):
    with get_db_session() as session:
        repo = TenantRepository(session)
        stats = repo.get_stats(tenant_id)
    return JSONResponse(content=stats)  # Category-2: preserve exact error shape
```

**Work items (in order):**

1. Write `test_category1_native_error_shape.py` and `test_category2_compat_error_shape.py` FIRST [§4 Wave 2].
2. Port HTML routers: `products.py` (audit `getlist` — 12+ sites), `tenants.py`, `gam.py`, `inventory.py`, `inventory_profiles.py`, `creatives.py` (webhook audit), `creative_agents.py`, `signals_agents.py`, `operations.py` (webhook audit), `policy.py`, `workflows.py`. All sync `def` [§4 Wave 2].
3. Port JSON APIs: `schemas.py` (external contract — byte-identical), `tenant_management_api.py` (Cat-2), `sync_api.py` (Cat-2 + `/api/sync` mount), `gam_reporting_api.py` (Cat-1). All sync `def` [§4 Wave 2].
4. Implement Category-2 scoped exception handler [§4 Wave 2].
5. `datetime` serialization format audit [§4 Wave 2].
6. Port 8 GAM inventory routes from `src/services/gam_inventory_service.py` to `src/admin/routers/inventory_api.py` — these are NOT blueprints and would be missed otherwise. Sync `def` with `with get_db_session()` [§3.5.3 SG-1].
7. Change `register_ui_routes(app: Flask)` interface to accept `APIRouter`; re-home adapter routes into `src/admin/routers/adapters.py` [§3.5.3 SG-3].
8. Migrate Flask imports in `src/services/` and `src/adapters/` files [§3.5.3 SG-6].
9. Delete 24 blueprint files (26 total minus `__init__.py` minus `activity_stream.py`), legacy test files/fixtures, `src/admin/tests/` nested directory [§4 Wave 2].
10. Shrink Flask imports allowlist to 3 entries [§4 Wave 2].
11. Write `test_flask_catchall_unreached.py` [§4 Wave 2].
12. Coverage parity check via `scripts/check_coverage_parity.py` [§4 Wave 2].
13. Playwright staging flows: login, create account, create/delete product, logout [§4 Wave 2].
14. Fix `tests/integration/conftest.py:17` module-level `create_app()` — convert to lazy fixture or conditional import before Phase 3 deletes `src/admin/app.py`. This is a 4th-derivative cascade: ALL integration tests fail at pytest collection time if not fixed.
15. Move `src/adapters/google_ad_manager.py:25` top-level `from flask import Flask` inside `register_ui_routes()` method. This adapter is imported by non-admin code; the top-level import causes ImportError cascade beyond admin boundary.
16. Enumerate and port ALL adapter/service Flask routes (14 routes across 5 files outside `src/admin/`): `gam_inventory_service.py` (8 routes), `gam_inventory_discovery.py` (3 routes), `gam_reporting_api.py` (Blueprint), `mock_ad_server.py` (1 route), `gam/utils/health_check.py` (1 route), `adapters/base.py` (1 route).

**Files to create:** 14 HTML routers, 4 API routers, `inventory_api.py`, `adapters.py`, test files.
**Files to delete:** 24 blueprint files, `src/admin/tests/` (7 files), legacy tests, `src/admin/auth_helpers.py`.

**Test blast radius:** 25 files use Flask `test_client()`, 15 use `session_transaction()`. Two replacement patterns: (1) `dependency_overrides` for auth priming (80% of cases), (2) signed cookie injection via `itsdangerous` for session behavior tests. Port `src/admin/auth_helpers.py` `require_api_key_auth()` to FastAPI dependency. Remove Flask `request` import from `src/adapters/gam_inventory_discovery.py:1074`.

**Exit gate:**
```bash
make quality && ./run_all_tests.sh  # ALL 5 suites green
git grep -l "flask" src/admin/ | wc -l  # <= 2
# Category-2 error shape byte-identical, schemas contract green
# Playwright green, Flask catch-all 0 requests 24h staging
```

**What NOT to do:** Do not delete `src/admin/app.py` (Phase 3). Do not remove Flask from `pyproject.toml` (Phase 3). Do not introduce `async def` handlers.

---

## Phase 3 — Cache migration + Flask removal + RC1 (~2,500 LOC, ~5-7 days)

**Goal:** Delete Flask entirely. Migrate flask-caching to SimpleAppCache. Tag `v2.0.0-rc1` (Flask-free milestone) and deploy.

> **What "irreversible" means here:** This phase removes Flask from the codebase. A `git revert` of the merge IS technically possible, but it's expensive — you'd also need to `uv lock` (lockfile may drift), rebuild the Docker image, and either revert Phases 0-2 (templates were codemod'd to FastAPI `url_for` format) or accept broken templates. Users would also get force-logged-out again (cookie name reverts). In Phases 0-2, rollback is instant via the feature flag. After Phase 3, rollback means deploying the archived `v1.99.0` container (losing data written since) or a multi-commit revert. This is why Phase 3 has the strictest entry criteria.

**Prerequisites:** Phase 2b merged. Flask catch-all 0 traffic for 48h. `v1.99.0` tag created and container image archived in registry as break-glass fallback.

**Knowledge to read:**
- `flask-to-fastapi-foundation-modules.md` §11.15 — SimpleAppCache (Decision 6, 12-step order)
- `flask-to-fastapi-execution-details.md` §Wave 3 — rollback + proxy-header smoke tests
- `flask-to-fastapi-migration.md` §15 — dependency changes
- `flask_migration_critical_knowledge.md` items 7, 10

**Work items (in order):**

1. Implement `src/admin/cache.py::SimpleAppCache` (~90 LOC) — `cachetools.TTLCache` + `threading.RLock` (sync-safe for both threadpool handlers and background sync threads) [§1.2 Decision 6].
2. Migrate 3 cache consumer sites in strict 12-step order (a→l) [§1.2 Decision 6].
3. Fix `from flask import current_app` at `background_sync_service.py:472` → `SimpleAppCache` [§3.5.3 SG-6].
4. Move `atexit` handlers (`webhook_delivery_service.py:185`, `delivery_simulator.py:45`) to FastAPI lifespan post-yield [§3.5.3 SG-2].
5. Delete: `src/admin/app.py`, `activity_stream.py`, `blueprints/` dir, `server.py`, `scripts/run_admin_ui.py`, dead helpers [§4 Wave 3].
6. Modify `src/app.py`: delete Flask mount, `/a2a/` redirect shim, landing route hack, proxy refs, feature flag [§4 Wave 3].
7. `git mv templates/ src/admin/templates/` and `static/ src/admin/static/` [§4 Wave 3].
8. Add `--proxy-headers --forwarded-allow-ips='*'` to uvicorn in `scripts/run_server.py` [§4 Wave 3].
9. Remove Flask deps from `pyproject.toml` (`flask`, `flask-caching`, `flask-socketio`, `python-socketio`, `simple-websocket`, `waitress`, `a2wsgi`, `types-waitress`), run `uv lock` [§4 Wave 3].
10. Rewrite `.pre-commit-hooks/check_route_conflicts.py` — currently imports Flask `create_app()` and inspects `app.url_map`. Must be rewritten for FastAPI router introspection [§4 Wave 3].
11. Update `.pre-commit-hooks/check_hardcoded_urls.py` — currently enforces `scriptRoot` as correct pattern. Must enforce `url_for()` instead.
12. Two cache structural guards: `test_architecture_no_flask_caching_imports.py`, `test_architecture_inventory_cache_uses_module_helpers.py` [§1.2 Decision 6].
13. Flask imports allowlist: EMPTY [§4 Wave 3].
14. Write `CHANGELOG.md` v2.0.0-rc1 entry (breaking changes list) [§4 Wave 3].
15. **CRITICAL proxy-header smoke tests on staging:** verify `https://` in OAuth redirect URIs, manual browser OAuth flow [§4 Wave 3].
16. Migrate 6 critical invariants from planning docs to code comments in `src/app.py` and `src/admin/app_factory.py` [§senior eng audit].
17. Bump `pyproject.toml` version to `2.0.0-rc1` [§4 Wave 3].
18. Apply `v2.0.0-rc1` tag [§4 Wave 3].
19. Production deploy + 48h monitoring: error rates, latency p50/p99, Docker size, cookie size [§6].
20. Ratchet `.duplication-baseline` [§4 Wave 3].
21. Update auto-memory `flask_to_fastapi_migration_v2.md` to reflect Flask removal milestone.

**Files to delete:** `src/admin/app.py`, `src/admin/blueprints/`, `src/admin/server.py`, `scripts/run_admin_ui.py`.
**Files to modify:** `src/app.py`, `pyproject.toml`, `scripts/run_server.py`, `src/admin/templating.py` (template path).

**Exit gate:**
```bash
make quality && ./run_all_tests.sh  # green
rg -w flask .                       # zero hits (excluding planning docs and changelogs)
docker build .                      # succeeds
# Manual OAuth flow on staging with correct https scheme
# v2.0.0-rc1 tag applied, production deploy successful
# 48h monitoring: no 5xx spike, latency stable
```

**What NOT to do:** Do not start async conversion (that is Phase 4+). Do not remove `psycopg2-binary` (stays until Phase 4). Do not remove `FLASK_SECRET_KEY` dual-read (Phase 5). Do not drop nginx (post-v2.0). Do not design multi-worker scheduler (v2.2).

---

## Phase 4 — Async DB Layer + FastAPI-Native Patterns

**Goal:** Convert sync admin handlers to fully async with `AsyncSession`, introduce `SessionDep` dependency injection, Pydantic DTOs at the handler/template boundary, and structlog. This phase is gated by 4 mandatory spikes (lazy-load audit, driver compat, performance baseline, factory-boy async shim).

**Prerequisites:** Phase 3 complete. `v2.0.0-rc1` deployed and stable >= 1 week in production.

**Knowledge to read:**
- `async-audit/` — all reports (comprehensive research for async conversion)
- `async-audit/database-deep-audit.md` — 3 critical blockers, 8 high-severity issues
- `async-audit/agent-a-scope-audit.md` — file-by-file async conversion inventory, 9 open decisions
- `async-audit/agent-e-ideal-state-gaps.md` — 14 idiom upgrades (SessionDep, DTO, structlog)
- `implementation-checklist.md` §6-7 — post-migration verification + cleanup

---

### Phase 4a — Foundation: DI Pattern + DTOs (sync, ~2,500 LOC, ~4-5 days)

**Goal:** Introduce `SessionDep` dependency injection and DTO boundary while staying fully sync. All handlers convert from `with get_db_session() as session:` in body to `session: SessionDep` in signature. UoW removed from admin handlers.

**Work items:**

1. Create `src/admin/deps/db.py` with sync `SessionDep = Annotated[Session, Depends(get_session)]` — works with sync `Session` first.
2. Create DTO package `src/admin/dtos/` — Pydantic models for handler/template boundary.
3. Add `structlog` dependency and configure structured logging.
4. Add `pydantic-settings` for typed configuration.
5. Convert all admin handlers from `with get_db_session() as session:` in body to `session: SessionDep` in signature.
6. Remove UoW usage from admin handlers — repositories manage their own session lifecycle.
7. Create lifespan-scoped engine in `src/core/database/engine.py`.

**Exit gate:**
```bash
make quality && ./run_all_tests.sh  # green
# All handlers use SessionDep, zero get_db_session() in handler bodies
# DTOs used at handler/template boundary for ported routers
```

**What NOT to do:** Do not change any handler from `def` to `async def` in this sub-phase. Do not add `asyncpg` or `AsyncSession`. This phase introduces the DI pattern without changing the concurrency model.

---

### Phase 4b — Testing Infrastructure (~1,500 LOC, ~5-7 days)

**Goal:** Run the 4 mandatory spikes that gate async conversion. Establish `dependency_overrides` test patterns. Add 10+ new structural guards.

**Work items:**

1. **Spike 1 (HARD GATE):** Set `lazy="raise"` on all 68 relationships, run `tox -e integration`. Pass: <40 failures fixable in <2 days. **Fail = abandon async for v2.0, ship sync-only as final v2.0.0.**
2. **Spike 2:** Run tests under `asyncpg` driver. Fail = switch to `psycopg[binary,pool]>=3.2.0`.
3. **Spike 3:** Capture sync latency on 20 admin routes + 5 MCP tool calls as `baseline-sync.json` for Phase 4c/4d comparison.
4. **Spike 4.25:** Factory-boy async shim validation per `foundation-modules.md` §11.13.1(D) recipe. 8 edge-case tests. Pass: all green, no `MissingGreenlet`. **Fail = STOP Phase 4 and re-analyze.**
5. Establish `dependency_overrides` test patterns for async test infrastructure.
6. Add 10+ new structural guards: `test_architecture_factory_inherits_async_base.py`, `test_architecture_factory_no_post_generation.py`, `test_architecture_factory_in_all_factories.py`, `test_architecture_no_singleton_session.py`, `test_architecture_adapter_calls_wrapped_in_threadpool.py`, `test_architecture_sync_bridge_scope.py`, `test_architecture_no_sse_handlers.py`, and others.

**Exit gate:**
```bash
# All 4 spikes PASS (Spike 1 is HARD GATE)
make quality && ./run_all_tests.sh  # green
# baseline-sync.json captured
```

**What NOT to do:** Do not start converting handlers to `async def` before all spikes pass. Do not skip Spike 1 — it is a hard gate.

---

### Phase 4c — Async Pilot (~2,000 LOC, ~3-5 days)

**Goal:** Engine refactor to async, `SessionDep` re-aliased to `AsyncSession`, 3 pilot routers converted to `async def`.

**Work items:**

1. Refactor engine to async: `create_async_engine` in `src/core/database/engine.py`.
2. Re-alias `SessionDep` from `Session` to `AsyncSession` — 1-file change in `src/admin/deps/db.py`.
3. Convert 3 pilot routers to `async def` with async repository methods.
4. Convert corresponding repository classes to async methods.
5. Verify performance parity against `baseline-sync.json` for pilot routes.

**Exit gate:**
```bash
make quality && ./run_all_tests.sh  # green
# 3 pilot routers serving async, performance within 10% of baseline
```

---

### Phase 4d — Async Bulk (~5,000 LOC, ~7-10 days)

**Goal:** Bulk-convert remaining admin routers to async. Activate factory-boy async shim. Handle adapter wrapping, ContextManager refactor, and sync-bridge.

**Work items:**

1. Bulk-convert remaining admin routers to `async def`.
2. Activate factory-boy async shim across all integration tests.
3. MCP `_impl` functions + adapter `run_in_threadpool` wrapping (Decision 1 — Path B).
4. ContextManager refactor to stateless async module functions (Decision 7).
5. Sync-bridge for background services: `src/services/background_sync_db.py` with separate sync psycopg2 engine (Decision 9).
6. SSE route deletion (Decision 8 — orphan code, −170 LOC).

**Exit gate:**
```bash
make quality && ./run_all_tests.sh  # green
# All admin routers async, adapter calls wrapped in threadpool
# ContextManager singleton eliminated
# SSE routes deleted
```

---

### Phase 4e — Async Completion (~1,500 LOC, ~3-4 days)

**Goal:** Final sync-to-async conversion sites. `lazy="raise"` permanent on all relationships. All structural guards at 100%.

**Work items:**

1. Convert final sync-to-async sites (remaining `_impl` functions, utility modules).
2. Make `lazy="raise"` permanent on all ORM relationships.
3. Ratchet all structural guards to zero allowlist entries.
4. Performance benchmark: full suite comparison against `baseline-sync.json`.

**Gate:** All handlers `async def`, performance parity with sync baseline.

**Exit gate:**
```bash
make quality && ./run_all_tests.sh  # green
# All admin handlers async def
# Performance within 10% of baseline-sync.json
# All structural guard allowlists empty
```

---

## Phase 5 — Cleanup + v2.0.0 Final

**Goal:** Delete sync artifacts, remove dead code, finalize v2.0.0 release.

**Prerequisites:** Phase 4e complete. All handlers async, performance verified, structural guards at 100%.

**Work items (in order):**

1. Delete sync artifacts: `get_db_session` sync context manager, `scoped_session` usage, `DatabaseManager` class, UoW classes.
2. Delete dead code: `database_schema.py` (confirmed orphan), `product_pricing.py` (Decision 5), dead functions in `queries.py`.
3. Hard-remove `FLASK_SECRET_KEY` dual-read from session/config code.
4. Ratchet all structural guards to zero tolerance — no allowlist entries remain.
5. Archive `.claude/notes/flask-to-fastapi/` — promote critical patterns to `docs/`.
6. Remove migration breadcrumb from root `CLAUDE.md`.
7. Write `CHANGELOG.md` v2.0.0 final entry (full breaking changes list).
8. Bump `pyproject.toml` version to `2.0.0`.
9. Apply `v2.0.0` tag.
10. Production deploy + 48h monitoring: error rates, latency p50/p99, Docker size, cookie size.
11. Update auto-memory to mark migration complete.
12. Delete `feat/v2.0.0-flask-to-fastapi` branch after merge confirmation.

**Files to delete:** Sync artifacts (`scoped_session` wrappers, `DatabaseManager`, UoW classes), `database_schema.py`, `product_pricing.py`, dead `queries.py` functions.
**Files to modify:** `CLAUDE.md` (root), `pyproject.toml`, session/config modules (remove `FLASK_SECRET_KEY` dual-read), various doc files.
**Files to archive/delete:** `.claude/notes/flask-to-fastapi/` contents (after promoting anything worth keeping).

**Exit gate:**
```bash
make quality && ./run_all_tests.sh  # green
# v2.0.0 tag applied, production deploy successful
# 48h monitoring: no 5xx spike, latency stable
# Root CLAUDE.md no longer references active migration
# All planning artifacts archived or deleted
# Branch deleted
```

**What NOT to do:** Do not drop nginx (post-v2.0). Do not design multi-worker scheduler (v2.2). Do not rush — Phase 5 is the final release gate.
