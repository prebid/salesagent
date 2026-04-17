# Flask → FastAPI v2.0.0 — Execution Plan

**Status:** Self-contained, layer-ordered implementation guide.
**Each layer is a standalone briefing. No cross-referencing required.**
**Last updated:** 2026-04-14 (v2.0 includes full async, strategically layered after Flask removal: L0-L4 sync, L5 async, L6-L7 polish and ship)

> **Async SQLAlchemy is Layer 5+ within v2.0 (after Flask removal L2 and FastAPI-native pattern refinement L4).** The async-audit reports in `async-audit/` contain comprehensive research for the L5-L7 async migration.
> L0-L4 focus on Flask removal + test harness modernization + sync FastAPI-native refinement. No `asyncpg`, no `async_sessionmaker`, no `expire_on_commit=False`, no lazy-load audit, no `AsyncSession` until L5. Admin handlers use sync `def` with `with get_db_session() as session:` through L3; L4 introduces sync `SessionDep = Annotated[Session, Depends(get_session)]`; L5b re-aliases `SessionDep` to `AsyncSession` as a one-line flip. FastAPI runs sync handlers in its default threadpool — no `run_in_threadpool` wrappers needed for DB access until L5.
> Testing uses Starlette `TestClient` (sync) through L4, then httpx `AsyncClient` in L5c+. Factory-boy works as-is with no async shim through L4; the `AsyncSQLAlchemyModelFactory` shim lands at L5c.

> **How to use this file:** Read ONE layer section. It contains everything you need —
> goal, prerequisites, knowledge sources, work items in order (tests first per TDD),
> files to touch, exit gate, and scope warnings. The `[§X-Y]` references point back
> to `implementation-checklist.md` for full detail when needed, but you should NOT
> need to open it during implementation.
>
> **Relationship to other docs:**
> - `implementation-checklist.md` — verification/tracking document (tick boxes after work is done)
> - `execution-plan.md` (this file) — **what to do, in what order** (read before coding)
> - Knowledge source files — deep reference (read when noted in "Knowledge to read")
> - `async-audit/` — **L5-L7 reference**; do not implement anything from these reports during L0-L4

---

## Layer 0 — Spike & Foundation: foundation modules + codemod script (~2,500 LOC, ~5-7 days)

**Thesis:** Pure addition. Flask serves 100% of traffic.

**Goal:** Land all foundation modules, write template codemod script (do NOT execute it — execution moves to L1a), create structural guards (including the `ADCP_USE_FASTAPI_ADMIN` feature flag and `X-Served-By` response header for instant rollback and verifiable traffic split). Pure addition — nothing changes behavior.

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

**L0 day-1 codemod (single commit, pre-foundation-modules, D8 #6 breaking):** `git mv src/admin/blueprints src/admin/routers`. Codemod all `from src.admin.blueprints.` imports to `from src.admin.routers.`. Verify Flask still imports cleanly. Zero behavioral change, ~5-line diff per importer (~40 importers expected). Structural guard `tests/unit/test_architecture_no_blueprints_dir.py` asserts the directory no longer exists and no imports reference it. Eliminates mixed directory state through L1c/L1d.

1. Write structural guard tests FIRST (TDD): `test_templates_url_for_resolves.py`, `test_templates_no_hardcoded_admin_paths.py`, `test_architecture_admin_routes_named.py`, `test_codemod_idempotent.py`, `test_oauth_callback_routes_exact_names.py` (pins: `/admin/auth/google/callback`, `/admin/auth/oidc/callback`, `/admin/auth/gam/callback`), `test_trailing_slash_tolerance.py`, `test_architecture_no_flask_imports.py` (full allowlist), `test_architecture_handlers_use_sync_def.py` (AST guard: admin handlers must be `def`, NOT `async def` — this is the L0-L4 sync invariant; the guard has a small allowlist for the 3-4 OAuth callback handlers that require `async def` for Authlib and is swapped out for `test_architecture_admin_routes_async.py` atomically at L5b), `test_architecture_no_async_db_access.py` (no `async with get_db_session()` in admin code), `test_architecture_no_module_level_engine.py`, `test_architecture_no_direct_env_access.py`, `test_architecture_middleware_order.py`, `test_architecture_exception_handlers_complete.py`, `test_architecture_csrf_exempt_covers_adcp.py`, `test_architecture_approximated_middleware_path_gated.py`, `test_architecture_admin_routes_excluded_from_openapi.py`, `test_architecture_scheduler_lifespan_composition.py`, `test_architecture_a2a_routes_grafted.py`, `test_foundation_modules_import.py`, `test_template_context_completeness.py`, `test_architecture_form_getlist_parity.py` [§4 Wave 0, §3.5.1 SB-2/SB-3]. Every allowlist entry gets a `FIXME(salesagent-xxxx)` comment at its source location [§3.5.6 EP-5].
1a. Add 9 AdCP boundary protective tests: `test_openapi_byte_stability.py`, `test_mcp_tool_inventory_frozen.py`, A2A agent card snapshot, `test_architecture_approximated_middleware_path_gated.py`, `test_architecture_csrf_exempt_covers_adcp.py`, `test_architecture_admin_routes_excluded_from_openapi.py`, error shape contract test, schema discovery contract test, REST response wire test. These protect the AdCP surface from accidental breakage during the migration.
2. Create foundation modules (ALL sync `def`) — decomposed into 11 sub-items below [§4 Wave 0, §2 Blockers 1-2, §3.5.1 SB-3].

    **2.1** `src/admin/templates.py` (~150 LOC) — Jinja2Templates + `render()` wrapper. Passes `test_mode`, `user_role`, `user_email`, `user_authenticated`, `username`, `support_email` (from `get_support_email()`), `sales_agent_domain` (from `get_sales_agent_domain()`) as context matching Flask's `inject_context()` at `src/admin/app.py:298-330`; registers `tojson` filter with HTML-escaping; registers `from_json` and `markdown` custom Jinja2 filters from `src/admin/app.py:154-155`. See `foundation-modules.md` §11.1.
    **Done =** `from src.admin.templates import render, templates` imports cleanly; `templates.env.filters["tojson"]`, `["from_json"]`, `["markdown"]` all callable; `test_template_context_completeness.py` passes (context dict contains all 7 Flask keys).

    **2.2** **SUPERSEDED (D8 #4 breaking):** ~~`src/admin/flash.py`~~ **Inline FlashMessage (D8 #4 breaking, replaces src/admin/flash.py):** Do NOT create `src/admin/flash.py`. Add `src/admin/deps/messages.py` providing `MessagesDep = Annotated[MessagesHelper, Depends(get_messages)]` where `MessagesHelper` reads/writes a `list[FlashMessage]` (Pydantic-typed) on `request.session["flash"]`. Codemod ~366 Flask `flash(...)` call sites to `messages.add(...)`. Structural guard extends `tests/unit/test_architecture_no_admin_wrapper_modules.py` to cover flash.py.
    **Done =** API surface matches Flask's `flask.flash`/`get_flashed_messages` for all 366 call sites identified in frontend-deep-audit; unit test with 3 categories (success/error/info) passes round-trip.

    **2.3** **SUPERSEDED (D8 #4 breaking):** ~~`src/admin/sessions.py`~~ **Inline SessionMiddleware + templates (D8 #4 breaking, replaces foundation-modules sub-items for sessions.py and templating.py):** Do NOT create `src/admin/sessions.py` or `src/admin/templating.py` as standalone modules. Register `SessionMiddleware` inline in `src/app.py::build_middleware_stack()` at L1a. Attach `Jinja2Templates(directory="src/admin/templates")` to `app.state.templates` in the lifespan startup. Handlers access via `request.app.state.templates.TemplateResponse(...)`. Structural guard `tests/unit/test_architecture_no_admin_wrapper_modules.py` asserts these modules do NOT exist. `SameSite=Lax` in ALL environments. `HttpOnly=True`. `secure=True` when scheme is https (detected post-proxy-headers). `secret_key` from `SESSION_SECRET` only (no `FLASK_SECRET_KEY` dual-read per D6).
    **Done =** middleware installed inline; `request.session` dict-like in handler; round-trip test sets/reads a key; cookie flags verified via TestClient `Set-Cookie` header parse (Lax + HttpOnly + Path=/); `tests/unit/test_architecture_no_admin_wrapper_modules.py` green.

    **2.4** `src/admin/csrf.py` (~120 LOC) — `CSRFOriginMiddleware` pure-ASGI Origin header validator. Allowlist seeded from `ALLOWED_ORIGINS` env var + `SALES_AGENT_DOMAIN`. Exempts `/mcp/*`, `/a2a`, `/a2a/*`, `/_internal/*`, `/static/*`. 307 on mismatch with a clear error body. NOT Double Submit Cookie — zero JavaScript or template changes. See `foundation-modules.md` §11.7 (integration code).
    **Done =** 7 Origin-scenario pytest cases green (missing Origin, matching, scheme-mismatch, port-mismatch, subdomain, null, evil); MCP/A2A paths exempted by path prefix; `test_architecture_csrf_exempt_covers_adcp.py` passes.

    **2.5** `src/admin/external_domain.py` (~90 LOC) — `ApproximatedExternalDomainMiddleware`. Reads `Apx-Incoming-Host`; if present and differs from configured `SALES_AGENT_DOMAIN`, issues a **307** redirect (preserves POST body) to the tenant subdomain. Path-gated — never fires on `/mcp`, `/a2a`, `/_internal`. See `foundation-modules.md` §11.8.
    **Done =** 307 emitted for cross-domain POST request in unit test; no 302 anywhere in the code (grep-verified); `test_architecture_approximated_middleware_path_gated.py` passes.

    **2.6** `src/admin/proxy_headers.py` (~40 LOC) — `FlyHeadersMiddleware`. Rewrites `Fly-Forwarded-Proto` → `X-Forwarded-Proto` when `Fly-Forwarded-Proto` present and `X-Forwarded-Proto` absent. Complements uvicorn's `--proxy-headers` flag (which handles standard `X-Forwarded-*` but not `Fly-*`). See `foundation-modules.md` §11.9.
    **Done =** request with `Fly-Forwarded-Proto: https` results in `request.url.scheme == "https"` inside handler; unit test verifies via TestClient with header injection.

    **2.7** `src/admin/request_id.py` (~30 LOC) — `RequestIDMiddleware`. Generates a UUID per request, stashes on `request.state.request_id`, echoes back as `X-Request-ID` response header; accepts inbound `X-Request-ID` if present. Used by structlog binding in L4 for trace correlation. See `foundation-modules.md` §11.9.5.
    **Done =** `X-Request-ID` echoed in response; re-request with same ID preserves it; unit test asserts both paths.

    **2.8** `src/admin/unified_auth.py` (~250 LOC) — Pure-ASGI auth middleware. Replaces Flask's `require_auth` decorator. Loads session, resolves `ResolvedIdentity`, stashes on `request.state.identity`. Path-gated (public routes bypass). Returns 401 JSON or HTML 302-to-login based on Accept header. Registered in the root `src/app.py` middleware stack per `foundation-modules.md` §11.36 `MIDDLEWARE_STACK_VERSION` table; the admin-side dep that consumes `request.state.identity` is documented in `foundation-modules.md` §11.4 `src/admin/deps/auth.py`.
    **Done =** authenticated request populates `request.state.identity`; unauthenticated `/admin/*` request redirects to login (HTML) or 401 (JSON); `test_architecture_no_werkzeug_imports.py` guard shrinks by this module's former Flask dependency.

    **2.9** `src/admin/oauth.py` (~60 LOC) — Authlib `starlette_client.OAuth()` registration for Google and per-tenant OIDC providers. Callback URI constants pinned: `/admin/auth/google/callback`, `/admin/auth/oidc/callback`, `/admin/auth/gam/callback` (byte-immutable with Google Cloud Console). See `foundation-modules.md` §11.6.
    **Done =** `oauth.google.authorize_redirect(...)` returns a valid `RedirectResponse`; callback paths match `test_oauth_callback_routes_exact_names.py` assertions exactly; routes reachable via `request.url_for("admin_auth_google_callback")`.

    **2.10** `src/admin/cache.py` (~90 LOC) — `SimpleAppCache` wrapping `cachetools.TTLCache(maxsize=1024, ttl=300)` with `threading.RLock` (NOT `asyncio.Lock` — Decision 6 / Site 3 background-thread requirement). `install_app_cache(app)` lifespan hook + `get_app_cache()` module global with `_NullAppCache` fallback for the startup race window. `CacheBackend` Protocol for v2.2 Redis swap. Env vars `ADCP_INVENTORY_CACHE_MAXSIZE` + `ADCP_INVENTORY_CACHE_TTL` override defaults. Admin handlers use `request.app.state.inventory_cache`; background threads use `get_app_cache()`. L6 later migrates storage to `app.state.inventory_cache` cleanly. See `foundation-modules.md` §11.15.
    **Done =** round-trip set/get test passes; TTL expiry verified at 0.5s resolution; RLock contention test with 4 threads passes; `test_architecture_inventory_cache_uses_module_helpers.py` guard passes.

    **2.11** `src/admin/content_negotiation.py` (~50 LOC) — `_wants_html(request)` helper that inspects `Accept` header (prefers `text/html` only if request path starts with `/admin/` AND `Accept` includes `text/html` AND does NOT include `application/json` with equal or higher q-value) + Accept-aware `AdCPError` exception handler (renders `templates/error.html` for HTML, returns JSON otherwise) registered on the root app.
    **Done =** unit test: `/admin/x` with `Accept: text/html` routes to HTML path; `/admin/x` with `Accept: application/json` routes to JSON path; `/mcp/x` always JSON regardless of Accept; `test_architecture_exception_handlers_accept_aware.py` guard passes.

    **Exit gate for Work Item 2 (all 11 sub-items):**
    - [ ] All 11 modules merged (may land as 1-3 PRs; within-PR ordering is irrelevant, ordering of PRs against L1 is what matters)
    - [ ] `test_foundation_modules_import.py` green (imports all 11 modules without ImportError)
    - [ ] Golden-fingerprint capture via `capture-fixtures` skill runs successfully against the pre-L1 Flask admin — this proves the fingerprint infrastructure (Work Item 10) is operational
    - [ ] **No cross-module import cycles** — verify via `python -c "import src.admin.templates, src.admin.flash, src.admin.sessions, src.admin.csrf, src.admin.external_domain, src.admin.proxy_headers, src.admin.request_id, src.admin.unified_auth, src.admin.oauth, src.admin.cache, src.admin.content_negotiation"` runs without error in under 1 second (slow = likely circular)
    - [ ] Each module's `Done =` criteria above verified
3. Create `form_error_response()` shared helper for DRY form-validation re-rendering across 25 routers [§3.5.6 EP-3].
4. Create feature flag routing toggle `ADCP_USE_FASTAPI_ADMIN` (~50 LOC) [§3.5.6 EP-1].
5. Create `X-Served-By` header middleware (~20 LOC) [§3.5.6 EP-2].
6. Write `scripts/generate_route_name_map.py` (~50 LOC) — introspects Flask `url_map` [§2-B1].
7. Write `scripts/codemod_templates_greenfield.py` (~200 LOC) — Pass 0 (csrf, g.*, flash), Pass 1a (static), Pass 1b (hardcoded paths), Pass 2 (Flask-dotted names) [§2-B1].
8. **Write** `scripts/codemod_templates_greenfield.py` is complete but do **NOT run it** in L0 — all 4 passes break Flask's `url_for` while Flask still serves traffic. Execution moves to L1a. Manual audit of `add_product_gam.html`, `base.html`, `tenant_dashboard.html` deferred to L1a [§2-B1].
9. Document `request.form.getlist()` → `List[str] = Form()` migration pattern in worked examples [§3.5.7 CP-2].
10. Write golden-fixture capture infrastructure: `tests/migration/fingerprint.py`, `tests/migration/conftest_fingerprint.py`, `tests/migration/test_response_fingerprints.py`, `tests/migration/fixtures/fingerprints/*.json` [§3.5.5 TI-1]. Uses Starlette `TestClient` (sync).
11. Add harness extension: `IntegrationEnv.get_admin_client()` returning Starlette `TestClient` with `dependency_overrides` snapshot/restore [§4 Wave 0 / L0].
12. Write `tests/integration/test_schemas_discovery_external_contract.py` [§3 audit action #4].
13. Complete §1.1 prerequisites: `SESSION_SECRET` in `.env.example` and secret stores, OAuth URI docs, external consumer contract confirmation [§1.1].

**L0 work item — Pydantic v2 guard (native-ness, empty allowlist):**
Add `tests/unit/test_architecture_no_pydantic_v1_config.py` per §11.35. Allowlist EMPTY at introduction (current codebase has 0 `class Config:` blocks; guard is monotonic from day 1 to prevent L1/L2/L4 regression).

**Files to create:** Foundation modules under `src/admin/`, 2 scripts, 20+ test files, golden-fixture infrastructure.
**Files to modify:** `tests/harness/_base.py`, `pyproject.toml` (add `itsdangerous>=2.2.0`, `pydantic-settings>=2.7.0` as explicit deps — currently transitive via Flask/pydantic-ai).
**Note:** Templates are NOT modified in L0 — codemod execution is L1a.

**Exit gate:**
```bash
make quality && tox -e integration && tox -e bdd && ./run_all_tests.sh  # all green
python -c "import ast; ast.parse(open('scripts/codemod_templates_greenfield.py').read())"  # script parses
```
Note: Codemod idempotency check and url_for count check move to L1a exit gate (after codemod runs).

**What NOT to do:** Do not modify `src/app.py` (no middleware, no router inclusion). Do not delete any Flask files. Flask serves 100% of `/admin/*` traffic. Do not use `async def` in any admin-facing handler or dependency. Do not add `asyncpg`, `async_sessionmaker`, or `SessionDep`. Do not implement anything from `async-audit/` reports — those are L5+ scope.

---

## Layer 1a — Flask Parity (sync): middleware stack + public/core routers (~1,800 LOC, ~3-4 days)

**Thesis:** Feature-flag-gated byte-identical port (sub-PR 1 of 4). This is the first L1 sub-PR; OAuth is deferred to L1b so this PR's handlers are all sync `def` with no async-def exceptions.

**Goal:** Wire middleware in correct order, port public + core routers, **run template codemod** (all 4 passes, atomically with FastAPI activation). FastAPI serves these routes under `ADCP_USE_FASTAPI_ADMIN=true`; Flask catch-all handles everything else. `X-Served-By` response header makes the split verifiable.

**Prerequisites:** L0 merged. `SESSION_SECRET` live in staging.

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
2. Port `src/admin/routers/public.py` (~400 LOC) — sync `def` handlers, no DB access [§4 Wave 1 / L1a-L1b].
3. Port `src/admin/routers/core.py` (~600 LOC) — sync `def` handlers with `with get_db_session()` [§4 Wave 1 / L1a-L1b].
4. Wire middleware stack in `src/app.py` (L1a shape, 7 middlewares, outermost → innermost): `Fly → ExternalDomain → UnifiedAuth → Session → CSRF → RestCompat → CORS`. `TrustedHost` AND `SecurityHeaders` are added at L2 (9 middlewares), `RequestID` at L4/L6 (10 middlewares) — see `flask-to-fastapi-foundation-modules.md` §cross-cutting/Middleware ordering for the canonical L1a/L2/L4-L6 progression [§2-B5]. Replace single `/admin/health` with `/healthz` (liveness) + `/readyz` (readiness) + `/health/db` + `/health/pool` per §11.31; legacy `/admin/health` becomes 308 → `/healthz`.
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

**What NOT to do:** Do not port auth/OIDC (L1b). Do not change session cookie name. Do not delete Flask blueprints. Do not use `async def` in any handler (L1b is where the OAuth async-def allowlist carveout lands).

---

## Layer 1b — Flask Parity (sync): auth + OIDC routers + session cutover (~2,200 LOC, ~4-5 days)

**Thesis:** Feature-flag-gated byte-identical port (sub-PR 2 of 4). This sub-PR introduces the **narrow `async def` allowlist** for OAuth callback handlers where Authlib's Starlette integration requires async (the callback flow is HTTP-level, not DB-level, so the handler body uses `await authlib.oauth.google.authorize_access_token(request)` but any DB access still uses sync `with get_db_session() as session:`). These 3-4 handlers are the only async-def admin handlers permitted through L0-L4; the sync-def guard's allowlist covers exactly them.

**Goal:** Port Google OAuth and OIDC login flows. Cut session cookie to `adcp_session`. Validate SameSite with OIDC `form_post`. This is the highest-risk router work in the migration.

**Prerequisites:** L1a merged. Middleware passing on staging. Authlib `starlette_client` tested (Authlib's Starlette integration requires `async def` for the OAuth redirect/callback handlers — the OAuth flow is HTTP-level, not DB-level, so the handler is `async def` but DB access inside it still uses sync `with get_db_session() as session:`).

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
2. Port `src/admin/routers/auth.py` (~1,100 LOC) — Google OAuth via Authlib. Sync `def` handlers except where Authlib requires `async def` for token exchange [§4 Wave 1 / L1a-L1b].
3. Port `src/admin/routers/oidc.py` (~500 LOC) — same pattern [§4 Wave 1 / L1a-L1b].
4. Implement Accept-aware `AdCPError` handler; modify existing `templates/error.html` (it already exists) [§2-B3].
4a. Add `@app.exception_handler(RequestValidationError)` for admin routes — renders form with error messages instead of JSON 422.
4b. Add `@app.exception_handler(HTTPException)` for admin routes — renders `templates/error.html` for 404/500 instead of JSON.
5. If OIDC providers use `form_post`: adjust SameSite/CSRF for that callback path [§3.5.3 SG-5].
6. Enable `adcp_session` cookie name [§1.2].
7. Verify `pyproject.toml` deps `pydantic-settings>=2.7.0` and `itsdangerous>=2.2.0` were added in L0 [§4 Wave 1 / L1a-L1b].
8. Send 48-hour customer communication for forced re-login [§3.5.6 EP-7].
9. Write `test_stale_flask_cookie_returns_login.py` — old `session=` cookie returns login page, not 500 [§4 Wave 1 / L1a-L1b].
10. Rollback procedure tested in staging [§4 Wave 1 / L1a-L1b].
11. Manual staging OAuth smoke by 2 engineers [§4 Wave 1 / L1a-L1b].
12. Update `test_architecture_no_flask_imports.py` allowlist (shrink) [§4 Wave 1 / L1a-L1b].

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

## Layer 1c — Flask Parity (sync): low-risk HTML routers (~3,000 LOC, ~4-5 days)

**Thesis:** Feature-flag-gated byte-identical port (sub-PR 3 of 4). All handlers are sync `def` (the OAuth async-def allowlist from L1b is NOT extended here).

**Goal:** Port 8 low-risk HTML-rendering admin blueprints. Flask catch-all still wired as safety net.

**Prerequisites:** L1b merged. Stable in staging >= 3 business days. Cookie size < 3.5KB confirmed.

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
2. Port routers (each with golden-fixture comparison test): `accounts.py`, `principals.py`, `users.py`, `settings.py`, `authorized_properties.py`, `publisher_partners.py`, `format_search.py` (4 routes), `api.py` (7 routes — dashboard AJAX). All sync `def` handlers with `with get_db_session()` [§4 Wave 2 / L1c-L1d].
3. Use `list[str] = Form([])` for every multi-value form field [§3.5.1 SB-2, §3.5.7 CP-2].
4. Every route decorator has `name="admin_<bp>_<endpoint>"` [§2-B1].
5. No `adcp.types.*` as `response_model=` [§3].

**L1c — Canonical tenant-prefix routing (D1 breaking):**
1. Mount each feature router once at `/tenant/{tenant_id}/<feature>` via `include_router(router, prefix="/tenant/{tenant_id}")`. Do NOT dual-mount.
2. Add `src/admin/middleware/legacy_admin_redirect.py` (~60 LOC, pure-ASGI). Reads `request.state.identity.tenant_id` (populated by `UnifiedAuthMiddleware`); on `/admin/<feature>/<rest>` where `<feature>` is in the feature-router set, emits 308 with `Location: /tenant/<tenant_id>/<feature>/<rest>`. Exempts `/admin/auth/*`, `/admin/login`, `/admin/logout`, `/admin/public/*`, `/admin/static/*`.
3. Register `LegacyAdminRedirectMiddleware` in `src/app.py::build_middleware_stack()` INSIDE `UnifiedAuthMiddleware` (so `request.state.identity.tenant_id` is available) but OUTSIDE `SessionMiddleware` (so session is hydrated). This makes L1c+ stack 8 middlewares (L1a had 7; L2 will add TrustedHost + SecurityHeaders → 10).
4. Structural guard `tests/unit/admin/test_architecture_admin_routes_single_mount.py`: AST-scans `src/app.py` and `src/admin/app_factory.py`; asserts each of the 14 feature routers is passed to `include_router()` exactly once AND the prefix is `/tenant/{tenant_id}`. Allowlist: `/admin/auth`, `/admin/public`, `/admin/static`.
5. Integration test `tests/integration/test_admin_legacy_redirect.py`: for each feature router, assert `GET /admin/<feature>` with session → 308 Location header matches `/tenant/<tenant_id>/<feature>`.

**Files to create:** 8 router files under `src/admin/routers/`.

**Exit gate:**
```bash
make quality && tox -e integration && tox -e bdd  # green
# Golden fixtures match for all ported routes
```

**What NOT to do:** Do not port high-risk routers (L1d). Do not port APIs or external contracts (L1d). Do not delete Flask files. Do not introduce `async def` handlers — the OAuth async-def allowlist from L1b does not extend here.

---

## Layer 1d — Flask Parity (sync): medium/high-risk routers + APIs (~5,500 LOC, ~5-7 days)

**Thesis:** Feature-flag-gated byte-identical port (sub-PR 4 of 4). Completes the Flask→FastAPI admin surface; after this PR merges and bakes, L2 deletes Flask. All handlers are sync `def`.

**Goal:** Port remaining 14 HTML routers (including webhook-preserving ones), 4 JSON API files with Category-2 error shape preservation. Delete Flask blueprints.

**Prerequisites:** L1c merged. Team freeze announced 48h prior.

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

1. Write `test_category1_native_error_shape.py` and `test_category2_compat_error_shape.py` FIRST [§4 Wave 2 / L1c-L1d].
2. Port HTML routers: `products.py` (audit `getlist` — 12+ sites), `tenants.py`, `gam.py`, `inventory.py`, `inventory_profiles.py`, `creatives.py` (webhook audit), `creative_agents.py`, `signals_agents.py`, `operations.py` (webhook audit), `policy.py`, `workflows.py`. All sync `def` [§4 Wave 2 / L1c-L1d]. **Each tenant-scoped HTML router is registered ONCE at the canonical `/tenant/{tenant_id}/*` prefix per D1 (2026-04-16); `/admin/*` access is served by the `LegacyAdminRedirectMiddleware` 308 redirect landed at L1c.** The existing `/tenant/<tenant_id>/*` URLs Flask serves via the catch-all continue to resolve because the canonical mount IS that prefix. Write `tests/integration/test_tenant_subdomain_routing.py` in this PR — smoke-tests `/tenant/default/dashboard`, `/tenant/default/products`, `/tenant/default/creatives`, `/tenant/default/users`, `/tenant/default/settings` for 200 BEFORE the L2 catch-all deletion. See `implementation-checklist.md` Wave 2 "Tenant-scoped admin routes" block and the L1c briefing for the single-mount + legacy-redirect architecture.
3. Port JSON APIs: `schemas.py` (external contract — byte-identical), `tenant_management_api.py` (Cat-2), `sync_api.py` (Cat-2 + `/api/sync` mount), `gam_reporting_api.py` (Cat-1). All sync `def` [§4 Wave 2 / L1c-L1d].
4. Implement Category-2 scoped exception handler [§4 Wave 2 / L1c-L1d].
5. `datetime` serialization format audit [§4 Wave 2 / L1c-L1d].
6. Port 8 GAM inventory routes from `src/services/gam_inventory_service.py` to `src/admin/routers/inventory_api.py` — these are NOT blueprints and would be missed otherwise. Sync `def` with `with get_db_session()` [§3.5.3 SG-1].
7. Change `register_ui_routes(app: Flask)` interface to accept `APIRouter`; re-home adapter routes into `src/admin/routers/adapters.py` [§3.5.3 SG-3].
8. Migrate Flask imports in `src/services/` and `src/adapters/` files [§3.5.3 SG-6].
9. Delete 24 blueprint files (26 total minus `__init__.py` minus `activity_stream.py`), legacy test files/fixtures, `src/admin/tests/` nested directory [§4 Wave 2 / L1c-L1d].
10. Shrink Flask imports allowlist to 3 entries [§4 Wave 2 / L1c-L1d].
11. Write `test_flask_catchall_unreached.py` [§4 Wave 2 / L1c-L1d].
12. Coverage parity check via `scripts/check_coverage_parity.py` [§4 Wave 2 / L1c-L1d].
13. Playwright staging flows: login, create account, create/delete product, logout [§4 Wave 2 / L1c-L1d].
14. Fix `tests/integration/conftest.py:17` module-level `create_app()` — convert to lazy fixture or conditional import before L2 deletes `src/admin/app.py`. This is a 4th-derivative cascade: ALL integration tests fail at pytest collection time if not fixed.
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

**What NOT to do:** Do not delete `src/admin/app.py` (L2). Do not remove Flask from `pyproject.toml` (L2). Do not introduce `async def` handlers.

---

## Layer 2 — Flask Removal + pre-commit rewrites + proxy-headers + TrustedHost + RC1 (~2,500 LOC, ~5-7 days)

**Thesis:** Delete Flask. `rg -w flask src/` must return 0 at exit. This is the single irreversible cut in v2.0; everything after L2 is pure FastAPI.

**Goal:** Delete Flask entirely. Migrate flask-caching to SimpleAppCache. Rewrite pre-commit hooks (`check_route_conflicts.py`, `check_hardcoded_urls.py`) for FastAPI AST inspection. Add `uvicorn --proxy-headers --forwarded-allow-ips='*'` and `TrustedHostMiddleware` to replace Flask's WSGI-era proxy handling. Tag `v2.0.0-rc1` (Flask-free milestone) and deploy.

> **What "irreversible" means here:** This layer removes Flask from the codebase. A `git revert` of the merge IS technically possible, but it's expensive — you'd also need to `uv lock` (lockfile may drift), rebuild the Docker image, and either revert L0/L1 (templates were codemod'd to FastAPI `url_for` format) or accept broken templates. Users would also get force-logged-out again (cookie name reverts). Through L1, rollback is instant via the `ADCP_USE_FASTAPI_ADMIN` feature flag + `X-Served-By` verification. After L2, rollback means deploying the archived `v1.99.0` container (losing data written since) or a multi-commit revert. This is why L2 has the strictest entry criteria.

**Prerequisites:** L1d merged. Flask catch-all 0 traffic for 48h (verified via `X-Served-By: fastapi` ratio = 100%). `v1.99.0` tag created and container image archived in registry as break-glass fallback.

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
4a. **PREREQUISITE VERIFICATION — dual-prefix tenant routing.** Dual-prefix registration of the 14 tenant-scoped routers (tenants, accounts, creatives, users, settings, operations, products, inventory, authorized_properties, signals_agents, activity, workflows, audit_logs, gam) is performed in **L1d work item 2** (where each router is ported). The L2 prereq is verification only: re-run `tests/integration/test_tenant_subdomain_routing.py` (added in L1d) and confirm `/tenant/default/{dashboard,products,creatives,users,settings}` all return 200 against the FastAPI router stack BEFORE deleting the Flask catch-all in this layer. See `implementation-checklist.md` Wave 2 "Tenant-scoped admin routes" block.
5. Delete: `src/admin/app.py`, `activity_stream.py`, `blueprints/` dir, `server.py`, `scripts/run_admin_ui.py`, dead helpers [§4 Wave 3].
6. Modify `src/app.py`: delete Flask mount, `/a2a/` redirect shim, landing route hack, proxy refs, feature flag [§4 Wave 3].
7. `git mv templates/ src/admin/templates/` and `static/ src/admin/static/` [§4 Wave 3].
8. Add `proxy_headers=True, forwarded_allow_ips='*'` kwargs to `uvicorn.run(...)` in `scripts/run_server.py` (the single canonical entrypoint per §11.8 — NOT in Dockerfile CMD). `scripts/deploy/run_all_services.py` inherits via `subprocess.Popen([sys.executable, "scripts/run_server.py", ...])`; `fly.toml` process entry runs the same script. Verify with `rg -n 'proxy_headers' scripts/ | wc -l` at L2 exit — expected 1 hit in `scripts/run_server.py`. Add `TrustedHostMiddleware` to `src/app.py` with the production host allowlist to replace Flask's WSGI-era `CustomProxyFix`/`FlyHeadersMiddleware` stack. Also add `SecurityHeadersMiddleware` (§11.28, ~70 LOC) positioned INSIDE `TrustedHost` and OUTSIDE `UnifiedAuth`. Per §11.36 `MIDDLEWARE_STACK_VERSION`, L2 bumps stack from version 2 (L1c, 8 middlewares) to version 3 (10 middlewares including the D1 `LegacyAdminRedirectMiddleware`).
8a. Implement `src/routes/health.py` per §11.31: `/healthz` (liveness, never DB), `/readyz` (readiness, DB + alembic + scheduler), `/health/db` + `/health/pool` (diagnostic), `/admin/health` 308-redirect to `/healthz`. Update `fly.toml` http_checks → `/readyz`.
8b. Implement `src/admin/rate_limits.py` per §11.32 (SlowAPI, memory backend, key-function prefers auth token over IP). Add `slowapi>=0.1.9` to `pyproject.toml`. Decorate `POST /admin/login` (5/min), OAuth init endpoints (20/min), MCP mount (100/min per token).
8c. Add `tests/integration/test_session_cookie_size.py` cookie-size budget guard per §11.33 (`MAX_COOKIE_BYTES = 3_584`). Add `heavy_tenant_session_client` fixture to `tests/integration/conftest.py`.
9. Remove Flask deps from `pyproject.toml` (`flask`, `flask-caching`, `flask-socketio`, `python-socketio`, `simple-websocket`, `waitress`, `a2wsgi`, `types-waitress`), run `uv lock` [§4 Wave 3].
10. Rewrite `.pre-commit-hooks/check_route_conflicts.py` — currently imports Flask `create_app()` and inspects `app.url_map`. Must be rewritten for FastAPI router introspection [§4 Wave 3].
11. Update `.pre-commit-hooks/check_hardcoded_urls.py` — currently enforces `scriptRoot` as correct pattern. Must enforce `url_for()` instead.
11a. **Pre-commit glob update (L2, same PR as Flask removal):** `.pre-commit-config.yaml:33` regex `^(templates/.*\.html|static/.*\.js)$` becomes a no-op after the L2 `git mv templates/ → src/admin/templates/` and `git mv static/ → src/admin/static/`. Update the regex in the SAME PR to `^src/admin/(templates/.*\.html|static/.*\.js)$`. During the L0→L2 window, a transitional regex `^(templates/.*\.html|static/.*\.js|src/admin/templates/.*\.html|src/admin/static/.*\.js)$` covers both old and new paths so the hook stays effective.
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
22. **L2 work item (D6 breaking change):** Hard-remove `FLASK_SECRET_KEY` dual-read. Delete the fallback read in SessionMiddleware registration. Update `scripts/setup-dev.py:143` to write `SESSION_SECRET` only. Delete `tests/unit/test_setup_dev.py::test_flask_secret_key_*`. Add structural guard `tests/unit/test_architecture_no_flask_secret_key_reads.py` with EMPTY allowlist. Update `docs/environment.md` and v2.0 release notes.
23. **L2 work item (D8 #7 breaking):** Convert `/_internal/*` routes to require `X-Internal-API-Key` header matching `INTERNAL_API_KEY` env var. Delete `ADCP_TESTING == 'true'` gate at `src/routes/health.py:30,51`. Update test harness to inject the header. Add `INTERNAL_API_KEY` to `.env.example`. Structural guard `tests/unit/test_architecture_internal_routes_api_key_authed.py` asserts every `/_internal/*` route depends on `require_internal_api_key`.
24. **L2 work item — explicit lifespan adoption (2026-native baseline):** Grep `src/` for `@app.on_event` (legacy pre-lifespan decorator). Replace with `@asynccontextmanager lifespan(app): yield`. Register via `FastAPI(lifespan=lifespan)`. Inside the lifespan context: initialize `app.state.db_engine`, `app.state.sessionmaker`, `app.state.templates`, `app.state.http_client` (async) + `app.state.http_client_sync`, `app.state.oauth`, `app.state.inventory_cache`, `app.state.active_sync_tasks`, plus structlog processor setup and scheduler starts. Shutdown: close engine, http clients, cancel all active async tasks, stop schedulers. Structural guard `tests/unit/test_architecture_no_on_event_handlers.py` with EMPTY allowlist prevents regression to `@app.on_event`. CLAUDE.md §2026 FastAPI-native baseline mandates the lifespan context manager.

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

**What NOT to do:** Do not start test-harness modernization (L3), pattern refinement (L4), or async conversion (L5+). Do not remove `psycopg2-binary` (stays until post-v2.0 when Path B adapters and sync-bridge sunset). Do not drop nginx (post-v2.0). Do not design multi-worker scheduler (v2.2). Do not delete `render()` wrapper (deletion is L4). Do not introduce `SessionDep` (that is L4).

---

## Layer 3 — Test Harness Modernization (~1,200 LOC, ~3-4 days)

**Thesis:** Factories + `dependency_overrides` + `TestClient` become the mandated pattern; pre-existing inline-`session.add()` debt goes into a ratcheting allowlist.

**Goal:** Consolidate factories in `tests/factories/`, adopt `app.dependency_overrides[get_db_session] = lambda: session` pattern in all new tests, retire inline `session.add()` in test bodies. All tests stay sync. Structural guard `test_architecture_repository_pattern.py` gains a ratcheting allowlist of pre-existing debt.

**Prerequisites:** L2 merged. `v2.0.0-rc1` deployed and stable in staging >= 3 business days (L3 is test-harness modernization only; no production-visible surface change, so the bake is staging-only and shorter than L1b/L2 production bakes).

**Knowledge to read:**
- `async-audit/testing-strategy.md` — multi-tier testing strategy, ~6,000 tests safety net
- `implementation-checklist.md` §6 — post-migration verification

**Work items (in order):**

1. Consolidate ORM factories under `tests/factories/` with `factory-boy` (sync); ensure every ORM model has a factory and every factory is registered in `ALL_FACTORIES`.
2. Write Pydantic schema factories (for request/response DTOs) under `tests/factories/schemas/`.
3. Add ratcheting allowlist to `test_architecture_repository_pattern.py` for pre-existing `get_db_session()`/`session.add()` in test bodies; every entry gets a `FIXME(salesagent-xxxx)` comment at the source location.
4. Document the dependency-override teardown rule (`.pop(dep, None)`, NOT `.clear()`) in `tests/admin/README.md` and in the `test-router` skill.
5. Establish the `TestClient` pattern: `with TestClient(app) as tc:` wrapped as a pytest fixture in `conftest.py`, with `app.dependency_overrides[get_db_session] = lambda: integration_db` for DB override.
6. Migrate 3-5 exemplar integration test files to the new pattern; leave the rest on the ratcheting allowlist.

**Exit gate:**
```bash
make quality && ./run_all_tests.sh  # green
# All new integration tests use factories + dependency_overrides
# Allowlist in test_architecture_repository_pattern.py frozen (or shrunk)
```

**What NOT to do:** Do not introduce `async def` tests. Do not introduce `httpx.AsyncClient`. Do not introduce the factory-boy async shim (that is L5c). Do not start FastAPI-native pattern refinement (L4) in the same PR — L3 is test-harness only.

---

## Layer 4 — Pattern Refinement (sync): DI + DTOs + structlog + pydantic-settings + render() deletion + ContextManager refactor + perf baseline (~2,500 LOC, ~5-7 days)

**Thesis:** FastAPI-native idioms without async risk. SessionDep lands here but remains sync — so L5 becomes a 1-file alias flip plus mechanical await conversion, not a structural rewrite.

**Goal:** Introduce `SessionDep = Annotated[Session, Depends(get_session)]` (still sync `Session`), DTO boundary at the repo layer, `structlog` wiring, pydantic-settings extension, `app.state` singletons, ContextManager refactor (Decision 7). **Delete the `render()` wrapper** in favor of `Jinja2Templates` via dependency. At EXIT, capture `baseline-sync.json` (Spike 3 deliverable) as the comparison oracle for L5.

**Prerequisites:** L3 merged.

**Knowledge to read:**
- `async-audit/agent-e-ideal-state-gaps.md` — 14 idiom upgrades (SessionDep, DTO, structlog)
- `foundation-modules.md` §11.0.1 (deps.py/SessionDep), §11.4 (structlog), §11.6 (pydantic-settings), §11.14 (DTOs)
- `implementation-checklist.md` §7 — pattern-refinement verification

**Work items (in order):**

1. Create `src/admin/deps/db.py` with sync `SessionDep = Annotated[Session, Depends(get_session)]` — works with sync `Session` first. The module exports only `SessionDep`; L5b edits this one file to re-alias to `AsyncSession`.
2. Create DTO package `src/admin/dtos/` — Pydantic models for handler/template boundary. Add structural guard that handlers receive DTOs (not ORM instances) at the template boundary for ported routers.
3. Add `structlog` dependency, configure structured logging with request-ID ContextVar propagation (Agent E idiom upgrade — avoids larger retrofit cost at L5 when async debugging becomes harder).
3a. Register `RequestIDMiddleware` (from `src/admin/request_id.py`, created in L0) as the outermost middleware in `src/app.py`, extending the canonical stack from 9 to 10 middlewares. Runtime order becomes `RequestID → Fly → ExternalDomain → TrustedHost → SecurityHeaders → UnifiedAuth → Session → CSRF → RestCompat → CORS`; registration is LIFO so `app.add_middleware(RequestIDMiddleware)` is the LAST `add_middleware` call in `src/app.py`. Honors inbound `X-Request-ID` (trust-upstream-if-present per 2026 Starlette practice — nginx may already set one), else generates a UUID4 hex; binds via `bind_request_id()` (work item 3) so every structlog line emitted during the request carries `request_id`; echoes `X-Request-ID` on the response. **Dependency:** must land AFTER work item 3 (structlog/bind_request_id), and BEFORE work item 13 (Spike 3 baseline capture) so `baseline-sync.json` reflects the 10-middleware stack. Update `tests/integration/test_architecture_middleware_order.py` to assert the 10-item L4+ order (replace the 9-item L2 assertion via layer-gated parametrization). See `foundation-modules.md §11.9.5` + `§cross-cutting/Middleware ordering`.
4. Extend `pydantic-settings` for typed configuration (replaces scattered `os.environ.get()` reads).
5. Adopt `app.state` for per-app singletons that were previously module-level globals; no async yet.
6. **Convert admin handlers to repository-dep injection (NOT raw SessionDep).** Admin handlers depend on `*RepoDep` (e.g., `accounts: AccountRepoDep = Depends(get_account_repo)`), NOT on `SessionDep` directly. Enforced by `tests/unit/test_architecture_no_session_in_admin_handlers.py` (foundation-modules §11.4/§11.0.3). `SessionDep` is used internally by the `*RepoDep` factory functions and by REST/`_impl` wrappers at `src/routes/api_v1.py` and `src/core/tools/*.py`, but NOT by admin handlers. Rationale: the project's repository pattern (project-root CLAUDE.md §3) treats `_impl` and admin handlers as consumers of typed repository interfaces; direct session access in handlers bypasses the repository layer's tenant-scoping and model factory guarantees. Convert each of ~40 admin handlers from `with get_db_session() as session:` in body to `*RepoDep` parameter in signature; the repo factory owns the `get_db_session()` call.
7. Remove UoW usage from admin handlers — repositories manage their own session lifecycle.
8. Create lifespan-scoped engine in `src/core/database/engine.py`.
9. **Delete `src/admin/templating.py::render()` wrapper** — handlers use `templates.TemplateResponse(request, "name.html", ctx)` via `Jinja2Templates` dependency instead.
10. **ContextManager refactor (Decision 7):** delete `ContextManager(DatabaseManager)` singleton, delete `DatabaseManager`, convert 12 public methods to module-level functions (sync at L4, will be `async def` at L5c). Validated by Spike 4.5 at L5a — at L4 this is the sync version. Structural guard `test_architecture_no_singleton_session.py` enforces the pattern.
11. Ratchet REST routes to `Annotated[T, Depends()]` form (14 signatures).
12. Update `require_tenant_access` to check `is_active` (small pre-existing Flask bug fix; breaking change OK on v2.0 branch).
13. **At EXIT: capture `baseline-sync.json` (Spike 3 deliverable).** Measure p50/p99 latency on 20 admin routes + 5 MCP tool calls under this final sync shape (with SessionDep, DTOs, structlog). Commit the file. L5 MUST compare against this baseline, not against pre-L4 sync.

**L4 EXIT work item — baseline-sync.json capture (Spike 3):**
Capture p50/p95/p99 latency + throughput for 20 admin routes + 5 MCP tool calls under sync admin handlers + sync adapters, 3 concurrency levels (10/50/200). Output: `tests/migration/fixtures/baseline-sync.json`. This is the FLOOR baseline — pre-async, pre-threadpool-wrap.

**Baseline shape disclaimer (important):** `baseline-sync.json` is captured at L4 EXIT under the sync stack with adapters still raw sync-`def`-in-sync-handler. L5e compares production shape (async handlers + adapters wrapped in `run_in_threadpool` per Decision 1 Path B, see L5d2), which is a different shape. To split the L4→L5e delta into its components:

- **L4 EXIT** captures `baseline-sync.json` (sync/sync) — establishes the floor.
- **L5d2 EXIT** captures `baseline-l5d2.json` (async handlers + threadpool-wrapped sync adapters; post-Path-B-wrap, pre-handler-flip if phased). **NEW requirement.**
- **L5e EXIT** captures `baseline-l5e.json` (final async shape) and compares against BOTH baselines:
  - vs `baseline-sync.json` — total async migration delta (what production saw)
  - vs `baseline-l5d2.json` — pure async-handler delta (isolates the SessionDep flip cost from the threadpool-wrap cost)

Perf criteria (lands at L5e entry):
- **p99 budget:** ±5% aggregate vs `baseline-sync.json`
- **p50 budget:** ±10% aggregate vs `baseline-sync.json`
- **Throughput:** ±5% aggregate vs `baseline-sync.json`
- **Per-route budget:** each admin route individually ±10% p99 vs its baseline entry
- **Escalation:** any single route regressing >20% p99 blocks L5e even if aggregate passes
- Benchmarked at 3 concurrency levels: 10, 50, 200 req/s

Write as `tests/integration/test_async_performance_parity.py` with thresholds in code (not prose). Guard fails L5e exit gate if threshold is violated.

**L4 work item — pydantic-settings centralization (native-ness):**
Extend existing `src/core/config.py` (do NOT create a new `src/core/settings.py` — per native-ness audit, two settings modules is a Flask-era regression). Consolidate the 89 `os.environ.get(...)` sites across `src/` into typed `BaseSettings` subclasses with `SettingsConfigDict(env_file=..., env_prefix=..., env_nested_delimiter="__")`. Credentials use `pydantic.SecretStr` (OAuth client_secret, DB passwords). Ratcheting guard `tests/unit/test_architecture_no_direct_env_access.py` (§11.35) seeded with current 89 sites; ratchets to 0 by L7.

**L4 work item — structlog adoption (native-ness):**
Add `structlog>=24.4.0` to `pyproject.toml`. Replace the 121 `print(` sites across 15 files in `src/` with `log = structlog.get_logger()` + `log.info(...)` calls. Register processors: `structlog.contextvars.merge_contextvars`, `structlog.processors.TimeStamper(fmt="iso")`, `structlog.processors.EventRenamer("message")`, `structlog.processors.JSONRenderer()` (prod) / `ConsoleRenderer()` (dev). `RequestIDMiddleware` (land at L4 per middleware stack version 4) binds `request_id` via `structlog.contextvars.bind_contextvars(...)`. Structural guard `tests/unit/test_architecture_uses_structlog.py` blocks new `print(` in `src/**` with allowlist for `scripts/`, `alembic/versions/`, `src/core/cli/`.

**L4 work item — httpx lifespan-scoped client (native-ness):**
Attach `httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0), limits=httpx.Limits(max_connections=100, max_keepalive_connections=20), transport=httpx.AsyncHTTPTransport(retries=3))` to `app.state.http_client` in the lifespan startup; close it in lifespan shutdown. `request.app.state.http_client` is used for all outbound HTTP calls (webhooks, JWKS discovery, agent-card fetches). For the adapter Path B sync call sites (Decision 1), `app.state.http_client_sync = httpx.Client(...)` is also registered. `tenacity`-based 5xx/read-timeout retry for webhook calls (the `AsyncHTTPTransport(retries=...)` option is connection-retry only).

**L4 work item — AsyncAttrs decision (L5+ lazy-load safety):**
Document rationale for choosing blanket `lazy="raise"` (Spike 1) over SQLAlchemy's `AsyncAttrs` mixin for async lazy-load safety. Both are valid 2026-native idioms; blanket `lazy="raise"` was selected because: (a) it fails LOUDLY at query time rather than silently issuing an extra async `SELECT` via `awaitable_attrs`, (b) all 68 existing relationship access sites have been cataloged in Spike 1's 9-pattern cookbook; (c) `AsyncAttrs` is additive and can be layered on top post-v2.0 if specific access patterns warrant it.

**L4 work item — transport-boundary preservation for admin→`_impl` calls:**
Admin handlers that call `_impl` functions MUST construct `ResolvedIdentity` via `src/admin/deps/identity.py::AdminIdentityDep` (resolves from session cookie + tenant resolution), NOT via `resolve_identity(ctx.http.headers)` which is the MCP/A2A-side helper and would import `fastmcp` types into admin code. Structural guard `tests/unit/test_architecture_admin_impl_calls_use_admin_identity.py` AST-scans admin router files for `resolve_identity(...)` calls and asserts the source is `AdminIdentityDep`, not `ctx.http.headers`. Preserves project-root `CLAUDE.md` §5 transport-boundary invariants (zero `fastmcp`/`a2a`/`starlette`/`fastapi` imports in `_impl`).

**Exit gate:**
```bash
make quality && ./run_all_tests.sh  # green
# All admin handlers use *RepoDep (NOT SessionDep), zero get_db_session() in handler bodies; SessionDep is confined to RepoDep factories and REST/_impl wrappers
# DTOs used at handler/template boundary for ported routers
# render() wrapper deleted
# ContextManager singleton eliminated
# baseline-sync.json committed
```

**What NOT to do:** Do not change any handler from `def` to `async def` in this layer (OAuth callback handlers from L1b remain async per their allowlist). Do not add `asyncpg` or `AsyncSession`. Do not start the pre-L5 spike sequence beyond Spike 3 baseline capture (Spikes 1/2/4/4.25/4.5/5.5 run at L5a entry). This layer introduces the DI pattern without changing the concurrency model.

---

## Layer 5 — Async Conversion: SessionDep alias flip + mechanical await conversion + adapter Path-B wrap + sync-bridge + SSE deletion

**Thesis:** `SessionDep` re-aliased from `Session` to `AsyncSession` — a one-line flip at L5b — then mechanical `await` conversion of ~60 commit sites and ~200 `scalars`/`execute` call sites across the router surface. The layer is split into sub-PRs for review tractability.

**Prerequisites:** L4 merged. `baseline-sync.json` captured and committed.

**Knowledge to read:**
- `async-audit/` — all reports (comprehensive research for async conversion)
- `async-audit/database-deep-audit.md` — 3 critical blockers, 8 high-severity issues
- `async-audit/agent-a-scope-audit.md` — file-by-file async conversion inventory, 9 open decisions
- `implementation-checklist.md` §6-7 — post-migration verification + cleanup

---

### L5 Pre-Flight Sync Inventory

Before L5a opens, enumerate every sync DB call site in the repository and classify it into one of four buckets. The output is the authoritative work surface for L5 — every row is either converted, wrapped, bridged, or deleted by L5e.

**Enumeration script** (run at L4 EXIT, output committed as `tests/migration/fixtures/l5-sync-inventory.txt`):

```bash
#!/bin/bash
# Enumerate every sync DB call site in src/ and classify.
# Buckets:
#   convert       — sync code that becomes async in L5c-L5e
#   threadpool    — sync code that stays sync, wrapped in await run_in_threadpool(...) at L5d2
#   sync-bridge   — sync code that stays sync with its own engine (Decision 9 — background_sync_service.py only)
#   delete        — sync code that is being deleted (e.g., SSE /events route per Decision 8)

OUT=tests/migration/fixtures/l5-sync-inventory.txt
mkdir -p tests/migration/fixtures
{
  echo "# L5 sync-site inventory — captured at L4 EXIT"
  echo "# Format: <bucket> <file>:<line> <snippet>"
  echo ""

  # Bucket: sync-bridge — Decision 9
  rg -n 'get_db_session\(\)' src/services/background_sync_service.py \
    | sed 's/^/sync-bridge /'

  # Bucket: delete — Decision 8 (SSE route)
  rg -n 'EventSourceResponse|StreamingResponse.*text/event-stream' src/admin/blueprints/activity_stream.py \
    | sed 's/^/delete /'

  # Bucket: threadpool — Path B adapter call sites (Decision 1)
  # 18 call sites in src/core/tools/*.py + 1 in src/admin/blueprints/operations.py
  rg -n '\.(create_media_buy|update_media_buy|pause|resume|update_creatives|upload_creative_assets)\(' \
    src/core/tools/ src/admin/blueprints/operations.py \
    --type py \
    | sed 's/^/threadpool /'

  # Bucket: convert — everything else (the bulk of L5c-L5e work)
  # Any remaining sync DB access in src/ not already classified
  rg -n 'with get_db_session\(\)|session\.scalars\(|session\.execute\(|session\.commit\(\)|session\.flush\(\)' \
    src/ \
    --glob '!src/services/background_sync_service.py' \
    --glob '!src/admin/blueprints/activity_stream.py' \
    --glob '!src/core/tools/' \
    --glob '!src/admin/blueprints/operations.py' \
    --type py \
    | sed 's/^/convert /'
} > "$OUT"

echo "Inventory written to $OUT"
wc -l "$OUT"
```

**Layer target per bucket:**

| Bucket | Layer | Outcome |
|---|---|---|
| `convert` | L5c (pilot 3 routers) + L5d3 (bulk routers + repositories) + L5d5 (mop-up `_impl`/`tools.py`/`main.py`) + L5e (final sweep) | Site becomes `async def` + `await` |
| `threadpool` | L5d2 | Site becomes `await run_in_threadpool(sync_fn, ...)` — adapters stay sync |
| `async-rearchitect` | L5d1 | Site converts to `asyncio.create_task` + short-lived `async with get_db_session()` per GAM-page batch with checkpoint resume (D3 supersedes 2026-04-11 sync-bridge) |
| `delete` | L5d4 | Site is deleted along with dead-code SSE route |

**Important path corrections (from verification audit):**

- **`background_sync_service.py`** lives at `src/services/background_sync_service.py`, **NOT** `src/core/services/`. The script path above is correct.
- **SSE route** lives at `src/admin/blueprints/activity_stream.py:226-364`, **NOT** `src/admin/routers/activity_stream.py` (it is still a Flask blueprint at L4 EXIT because activity_stream ports to FastAPI in L1d; the SSE route is deleted at L5d4 after the L1d port). If by L5d4 the blueprint has been ported, the SSE deletion targets `src/admin/routers/activity_stream.py` — adjust script accordingly.
- **Repositories** live at `src/core/database/repositories/`, **NOT** `src/core/repositories/`. The `convert` bucket's bulk is in this directory.

**Post-Commit-E amendments (precision pass):**

1. **`SYNC_BRIDGE_FILES` constant (if the script is ever ported to a Python guard, see §6 below).** The allowlist is a singleton: `SYNC_BRIDGE_FILES = {"src/services/background_sync_service.py"}`. Note the path is `src/services/`, NOT `src/core/services/` — an earlier plan revision incorrectly wrote `src/core/`. The bash script above already uses the correct path; this amendment is for the Python-guard rewrite below.

2. **`SSE_FILES` covers both pre-port and post-port paths.** The SSE route lives at the Flask blueprint path (`src/admin/blueprints/activity_stream.py`) through L1d, then at the FastAPI router path (`src/admin/routers/activity_stream.py`) after L1d merges, then is deleted at L5d4. The classifier therefore matches BOTH paths so an inventory captured at either point in the layer sequence produces consistent output:

   ```python
   SSE_FILES = {
       "src/admin/blueprints/activity_stream.py",  # pre-L1d port (Flask blueprint)
       "src/admin/routers/activity_stream.py",     # post-L1d port (FastAPI router)
   }
   # Explanatory comment: the SSE route name migrates as the blueprint→router port
   # happens in L1d. L5d4 deletes whichever path exists at that point. The inventory
   # guard runs at L4 EXIT (so `blueprints/` still exists) and again informally during
   # L5d (either path could exist). Including both guards against false negatives on
   # either end of the port.
   ```

3. **`repositories/` classification path correction.** The `rel.startswith(...)` check for the `convert` bucket must use `src/core/database/repositories/`, NOT `src/core/repositories/`. The latter does not exist. The bash script above uses `src/` without narrowing to the repository directory, so this correction is a no-op for the script but a REQUIRED precision for any Python-guard port (a wrong prefix would silently miss every repository method).

4. **`_enumerate_sync_sites()` helper — Python-guard port of the bash script.** To be added to `tests/unit/architecture/test_architecture_no_new_sync_sites_post_l4.py` when the structural guard is created at L4 EXIT. Draft shape (docs-only; final lands in the guard file at L4 EXIT):

   ```python
   # tests/unit/architecture/test_architecture_no_new_sync_sites_post_l4.py (draft)
   """Guard: no new sync DB sites added after L4 EXIT.

   Reads `tests/migration/fixtures/l5-sync-inventory.txt` as the baseline captured at L4 EXIT.
   Any sync DB site present in `src/` that is NOT in the baseline fails the test.

   Retired at L5e exit (the `convert` bucket becomes empty).
   """
   from pathlib import Path
   import re
   import subprocess

   SYNC_BRIDGE_FILES = {"src/services/background_sync_service.py"}
   SSE_FILES = {
       "src/admin/blueprints/activity_stream.py",
       "src/admin/routers/activity_stream.py",
   }
   REPOSITORY_PREFIX = "src/core/database/repositories/"  # NOT src/core/repositories/
   ADAPTER_CALLER_FILES = {
       # 18 sites in src/core/tools/ — enumerated at L4 EXIT
       # 1 site in src/admin/blueprints/operations.py
   }
   SYNC_PATTERN = re.compile(
       r"with get_db_session\(\)|session\.scalars\(|session\.execute\(|"
       r"session\.commit\(\)|session\.flush\(\)"
   )

   def _enumerate_sync_sites() -> set[tuple[str, int]]:
       """Walk src/ and return the set of (relative_path, line_number) pairs
       where a sync DB call site appears. Classification into buckets is done
       separately via the path-prefix checks above."""
       root = Path(__file__).resolve().parents[3]
       src = root / "src"
       sites: set[tuple[str, int]] = set()
       for py in src.rglob("*.py"):
           rel = str(py.relative_to(root))
           try:
               for lineno, line in enumerate(py.read_text().splitlines(), start=1):
                   if SYNC_PATTERN.search(line):
                       sites.add((rel, lineno))
           except (UnicodeDecodeError, OSError):
               continue
       return sites

   def test_no_new_sync_sites_post_l4():
       baseline_file = Path(__file__).resolve().parents[3] / "tests/migration/fixtures/l5-sync-inventory.txt"
       if not baseline_file.exists():
           # Guard is retired; allow the test to pass until L5e cleanup deletes it.
           return
       baseline = _parse_baseline(baseline_file)
       current = _enumerate_sync_sites()
       new_sites = current - baseline
       assert not new_sites, f"New sync DB sites added after L4 EXIT: {sorted(new_sites)}"
   ```

   This helper replicates the bash script's enumeration in Python so the guard can run as part of `make quality` without requiring a shell script. The classification (into convert/threadpool/sync-bridge/delete buckets) remains bash-only — the guard only asserts non-growth of the `convert` bucket + no-new-site invariant.

5. **Expected order-of-magnitude.** An earlier draft included an illustrative breakdown ("~280 repository sites, ~68 admin handler sites..."). That breakdown was synthetic and would mislead anyone reading it as an exact count. Replace with: **"Expected order-of-magnitude: ~200-400 sync sites. Exact count captured at L4 EXIT and written as the first line of `l5-sync-inventory.txt`."** The exact count IS the artifact; do not pre-commit a number in the plan.

**New structural guard** (added at L4 EXIT, retired at L5e exit):

`test_architecture_no_new_sync_sites_post_l4` — **Frozen inventory guard.** Reads `tests/migration/fixtures/l5-sync-inventory.txt` as the baseline. Any sync DB site that appears in `src/` after L4 EXIT and is NOT in the baseline file fails CI. This prevents new sync code from being added while L5 is in progress. The guard retires when L5e closes and all `convert`-bucket sites have become async.

**L5 Exit gate (end of L5e):**

- [ ] Every `convert` bucket entry is now `async def` + `await`, verified by mypy strict
- [ ] Every `threadpool` bucket entry is wrapped in `await run_in_threadpool(...)`
- [ ] Every `sync-bridge` entry imports `get_sync_db_session` from the dedicated module
- [ ] Every `delete` entry is no longer in the tree
- [ ] `test_architecture_no_new_sync_sites_post_l4.py` retires (deleted, not allowlist-emptied)
- [ ] `l5-sync-inventory.txt` deleted from `tests/migration/fixtures/` (no longer needed)

---

### Layer 5a — Spike sequence (~1,500 LOC, ~5-7 days)

**Goal:** Run the 7 mandatory spikes that gate async conversion. Establish async `dependency_overrides` test patterns. Add 10+ new structural guards.

**Work items:**

1. **Spike 1 (HARD GATE):** Set `lazy="raise"` on all 68 relationships, run `tox -e integration`. Pass: <40 failures fixable in <2 days. **Fail = narrow L5 scope or defer residual async to a v2.1 epic; L0-L4 already shipped and are not affected.**
2. **Spike 2:** Run tests under `asyncpg` driver. Fail = switch to `psycopg[binary,pool]>=3.2.0`.
3. **Spike 3 (already captured at L4 EXIT):** Re-run perf measurement against `baseline-sync.json` to validate the capture pipeline is reproducible before mid-L5 comparisons.
4. **Spike 4:** Convert `tests/harness/_base.py` + 5 representative tests; verify xdist + factory-boy work.
5. **Spike 4.25:** Factory-boy async shim validation per `foundation-modules.md` §11.13.1(D) recipe. 8 edge-case tests. Pass: all green, no `MissingGreenlet`. **Fail = STOP L5 and re-analyze.**
6. ~~Spike 4.5~~ **Ran at L4 ENTRY per CLAUDE.md §v2.0 Spike Sequence; spike-decision.md references the L4-entry record.** L5a still enumerates Spike 4.5 status to confirm no regression was introduced by the sync-baseline capture at L4 EXIT, but this is a recap, not an execution item.
7. **Spike 5:** Scheduler alive-tick — convert 2 scheduler tick bodies; observe container logs.
8. **Spike 5.5:** Two-engine coexistence — prove async asyncpg + sync psycopg2 engines coexist in one process (Decision 9 validation).
9. **Spike 6:** Alembic async — rewrite `alembic/env.py`; run upgrade/downgrade roundtrip. Fallback: keep env.py sync.
10. **Spike 7:** `server_default` audit — grep + categorize columns; confirm <30 to rewrite.
11. Establish async `dependency_overrides` test patterns (async generator overrides, `.pop()` teardown, scope alignment).
12. Add 10+ new structural guards: `test_architecture_factory_inherits_async_base.py`, `test_architecture_factory_no_post_generation.py`, `test_architecture_factory_in_all_factories.py`, `test_architecture_adapter_calls_wrapped_in_threadpool.py`, `test_architecture_sync_bridge_scope.py`, `test_architecture_no_sse_handlers.py`, and others.

**Exit gate:**
```bash
# All 10 technical spikes + 1 decision gate (11 items) PASS per CLAUDE.md §v2.0 Spike Sequence (Spike 1 is HARD GATE)
make quality && ./run_all_tests.sh  # green
# Spike gate decisions recorded in spike-decision.md
```

**What NOT to do:** Do not start converting handlers to `async def` before all spikes pass. Do not skip Spike 1 — it is a hard gate.

---

### Layer 5b — SessionDep alias flip (~50 LOC, ~0.5 day)

**Goal:** One-line change: `SessionDep = Annotated[AsyncSession, Depends(get_async_session)]` in `src/admin/deps/db.py`. The entire admin surface's type checker now sees `AsyncSession` — mypy flags every call site that needs `await`.

**Work items:**

1. Refactor engine to async: `create_async_engine` in `src/core/database/engine.py`.
2. Re-alias `SessionDep` from `Session` to `AsyncSession` — the 1-line change.
3. Atomically swap the structural guards: remove `test_architecture_handlers_use_sync_def.py`, add `test_architecture_admin_handlers_async.py`. The two guards are mutually exclusive. (Per finding-13 resolution: L5b is the correct placement — the swap happens when the `AsyncSession` alias flips, so new `async def` pilot code can compile cleanly; the new guard's allowlist starts full and drains through L5c-L5d3.)
4. Fix the pilot commit by `await`ing the handful of call sites in the engine module.

**Exit gate:**
```bash
make quality  # mypy will error on every un-await'd call site — this is expected
# The list of mypy errors is the exact work surface for L5c-L5e
```

**What NOT to do:** Do not attempt to make the whole suite pass in this PR — that is L5c+. The PR's test suite will have failures scoped to the converted engine module only.

---

### Layer 5c — 3-router async pilot + async test harness adoption (~2,000 LOC, ~3-5 days)

**Goal:** Convert 3 pilot routers (chosen for simplicity and low coupling) to `async def` with mechanical await conversion. Convert corresponding test files via `/convert-tests` skill. Activate factory-boy async shim for these tests.

**Pilot router selection (final):**

- `src/admin/routers/format_search.py` — 320 LOC, 4 routes, all GET (read-only). Pure query conversion; zero write paths; no outbound HTTP.
- `src/admin/routers/accounts.py` — 189 LOC, 5 routes (list/create/detail/edit/status). CRUD with tenant scoping; no outbound HTTP.
- `src/admin/routers/inventory_profiles.py` OR `src/admin/routers/authorized_properties.py` — CRUD without outbound HTTP; pick whichever has simpler schema.

**Dropped from pilot:** `src/admin/routers/signals_agents.py` — has `POST /test` endpoint that makes outbound HTTP calls, forcing adapter async pattern validation simultaneously. Move to L5d (broader async rollout).

**Rationale:** diverse patterns (read-only GET, write-path CRUD, tenant-scoped CRUD) without the confound of outbound HTTP. If pilot succeeds, L5d1-5d5 scale.

**Work items:**

1. Convert 3 pilot routers to `async def` with mechanical `await` conversion (`await session.scalars(stmt)`, `await session.commit()`, etc.).
2. Convert corresponding repository classes to async methods.
3. Convert the pilot router tests to async via `/convert-tests` skill (lifecycle: layer-5c).
4. Activate factory-boy async shim for the pilot test files.
5. Run `ContextManager` async conversion for modules touched by pilot routers (the sync refactor landed at L4; L5c flips to `async def`).
6. Verify performance parity against `baseline-sync.json` for the 3 pilot routes (within budget).

**L5c work item — httpx.AsyncClient test harness (native-ness):**
Migrate integration/e2e test fixtures from Starlette `TestClient` (sync) to `httpx.AsyncClient(transport=ASGITransport(app=app))` (async). `TestClient` remains for L0-L4 sync-def handlers; `AsyncClient` is required for L5c+ async-def handlers. Ratcheting guard `tests/unit/test_architecture_no_testclient_in_async_routers.py` (L5c exit) asserts tests importing routers flipped to async use `AsyncClient`, not `TestClient`. BDD tests (pytest-bdd) keep sync via `asyncio.run()` bridge; new guard `tests/unit/test_architecture_bdd_no_pytest_asyncio.py` prevents `@pytest.mark.asyncio` in BDD step files (would deadlock under Risk #3 Interaction B).

**Exit gate:**
```bash
make quality && ./run_all_tests.sh  # green
# 3 pilot routers serving async, performance within budget vs baseline-sync.json
```

---

### Layer 5d — Bulk conversion, split into 5 sub-PRs

**Goal:** Complete the async conversion surface. Split by risk / failure domain — each sub-PR is independently revertible.

#### L5d1 — `background_sync_service` async rearchitect (D3 2026-04-16, supersedes Option B sync-bridge)

Rearchitect `src/services/background_sync_service.py` as `asyncio.create_task` + checkpoint-per-GAM-page. Each GAM-page (~30s) opens its own short-lived `async with get_db_session() as session:`, writes progress to a `sync_checkpoint` row, commits, closes. Resume logic reads checkpoint and continues from next cursor on next tick. `threading.Thread` workers become `asyncio.create_task(...)` in the lifespan, registered on `app.state.active_sync_tasks: dict[str, asyncio.Task]`, cancellable on shutdown. Session lifetime is always << `pool_recycle=3600`; no sync-bridge needed. `src/services/background_sync_db.py` is NOT created (never written). New structural guard `test_architecture_no_threading_thread_for_db_work.py` (empty allowlist) — AST-scans `src/` for `threading.Thread(target=...)` whose body touches `get_db_session` or `session.`. Validated by Spike 5.5 at L5a entry (checkpoint-session viability — 4 test cases: 4-hour sync, concurrent tenants, cancellation, resume from checkpoint). If Spike 5.5 fails (SOFT), revert to pre-D3 Option B sync-bridge and file v2.1 sunset ticket.

#### L5d2 — Adapter Path-B threadpool wrap (Decision 1)

All 18 adapter call sites in `src/core/tools/*.py` (and 1 in `src/admin/blueprints/operations.py`) wrap in `await run_in_threadpool(adapter.method, ...)`. Structural guard `test_architecture_adapter_calls_wrapped_in_threadpool.py` enforces.

#### L5d3 — Bulk router conversion

Convert all remaining admin routers to `async def`. Each router's test file goes through `/convert-tests` in the same PR.

#### L5d4 — SSE route deletion (Decision 8)

Delete the orphan `/tenant/{id}/events` SSE route (−170 LOC; the JS layer already uses JSON polling, the SSE route is dead code). Delete the `sse_starlette` dependency. Fix the `api_mode=False → api_mode=True` bug on the surviving `/activity` JSON poll route.

#### L5d5 — Mop-up

Convert remaining utility modules and `_impl` functions that mypy still flags. Fix any `await`-missing sites surfaced by spike 3 re-runs.

---

### L5d3 Repository-Method Inventory

L5d3 is the largest sub-PR in L5 and the highest-uncertainty item on the L5 schedule (8-12 engineer-days with 15-day upper band per the verification audit). Before starting L5d3, inventory every repository method that needs async conversion so the scope is visible up front, not discovered mid-PR.

**Inventory script** (run at L5c exit / L5d3 entry):

```bash
# Count repository method definitions
rg -n '^\s{4}def\s+\w+' src/core/database/repositories/ --type py | wc -l   # expect ~300

# List every method grouped by repository
rg -n '^\s{4}def\s+(\w+)' src/core/database/repositories/ -r '$1' --type py \
  | sort -t: -k1,1 -u > tests/migration/fixtures/l5d3-repo-methods.txt

# Approximate LOC under conversion
rg -A 20 '^\s{4}def\s+\w+' src/core/database/repositories/ --type py | wc -l  # expect ~2400

# Find commit sites (sync → need await)
rg -n 'session\.(commit|flush|refresh|execute|scalars|scalar|get)\(' src/core/database/repositories/ --type py > tests/migration/fixtures/l5d3-call-sites.txt
```

**Expected scale** (per verification audit):

- **~300 repository methods** across `src/core/database/repositories/` (NOT `src/core/`; the repositories live at `src/core/database/repositories/`)
- **~2,400 LOC** of repository body that needs conversion
- **~60 commit sites** (`session.commit()`, `session.flush()`) — each requires `await`
- **~200 query sites** (`session.scalars(stmt)`, `session.execute(stmt)`, `session.get(Model, pk)`) — each requires `await` + pattern rewrite (`(await session.execute(stmt)).scalars().all()` idiom)

**Per-repository 4-step work breakdown:**

For each repository class (e.g., `MediaBuyRepository`, `CreativeRepository`, `ProductRepository`):

1. **Flip method signatures:** `def X(self, ...) -> T:` → `async def X(self, ...) -> T:` for every method that touches `self.session`.
2. **Await DB call sites:** every `self.session.scalars(...)` / `.execute(...)` / `.get(...)` / `.commit()` / `.flush()` gains `await`.
3. **Rewrite query idiom:** `self.session.scalars(stmt).first()` → `(await self.session.execute(stmt)).scalar_one_or_none()` (or `.scalars().first()` for multi-row helpers).
4. **Update every caller:** admin router handlers that invoke the repository methods gain `await` at the call site. mypy surfaces these exhaustively after L5b.

**L5d3 sub-PR grouping (domain-topological):**

Split L5d3 into 4 sub-PRs by domain to keep review tractable and preserve revertibility:

- **L5d3.1 — Media/creative domain** (highest-traffic, largest repos): `MediaBuyRepository`, `CreativeRepository`, `CreativeAssignmentRepository`, `CreativeReviewRepository`. ~100 methods, ~800 LOC.
- **L5d3.2 — Product/pricing domain:** `ProductRepository`, `PricingOptionRepository`, `FormatRepository`. ~60 methods, ~450 LOC.
- **L5d3.3 — Tenant/identity domain:** `TenantRepository`, `PrincipalRepository`, `AccountRepository`, `CurrencyLimitRepository`, `PropertyTagRepository`. ~80 methods, ~650 LOC.
- **L5d3.4 — Workflow/audit domain:** `WorkflowRepository`, `AuditLogRepository`, `ObjectWorkflowMappingRepository`. ~60 methods, ~500 LOC.

**Per-sub-PR exit gate:**

Each of L5d3.1-L5d3.4 must pass independently:

- [ ] All methods in the covered repositories are `async def`
- [ ] All callers (admin routers, `_impl` functions) gained `await` at call sites — verified by `mypy src/ --strict` producing zero new errors from this domain
- [ ] `tox -e integration` passes for the domain's test files
- [ ] No `MissingGreenlet` exceptions anywhere in the suite
- [ ] Perf measurement on 3 representative endpoints within budget vs `baseline-sync.json`
- [ ] Structural guard allowlists shrink (methods removed from `test_architecture_no_raw_select.py` pre-existing-debt allowlist where applicable)

**If actual method count exceeds ~300,** split L5d3.1 or L5d3.3 further rather than bundling overflow into L5d3.4 — a cleaner domain boundary is always preferable to a catch-all PR.

---

### Layer 5e — Final async sweep (~1,500 LOC, ~3-4 days)

**Goal:** Final sync-to-async conversion sites. `lazy="raise"` permanent on all relationships. Full perf validation against `baseline-sync.json` captured at L4 EXIT.

**Work items:**

1. Convert final sync-to-async sites (remaining `_impl` functions, utility modules).
2. Make `lazy="raise"` permanent on all ORM relationships.
3. Performance benchmark: full suite comparison against `baseline-sync.json`. Regression beyond budget = block L5 exit (not L7) — measure here so L6/L7 do not ship regressions.
4. Update the `AdCPError` exception handler and any remaining middleware to be `async`-aware where applicable.

**Exit gate:**
```bash
make quality && ./run_all_tests.sh  # green
# All admin handlers async def (except L1b OAuth-async-def allowlist, which stays async — it was the direction of travel)
# Performance within budget of baseline-sync.json (captured at L4 EXIT)
```

---

## Layer 6 — Native Refinements: app.state singletons, router subdir reorg, logfire (~1,500 LOC, ~3-4 days)

**Thesis:** Post-async cleanup that was not safe to do before L5. `SimpleAppCache` migrates to `app.state`; router subdirectory reorganization for navigability; `logfire` instrumentation lands (NOT `opentelemetry-sdk` — `logfire` is the chosen observability stack). (Note: `flash.py` deletion moved to L0 per D8 #4 — the wrapper module was never created.)

**Prerequisites:** L5e merged. All handlers async, perf within budget.

**Work items (in order):**

1. ~~Delete `src/admin/flash.py`~~ — **superseded by D8 #4:** the flash wrapper module was never created at L0; `MessagesDep` on `request.session["flash"]` has been the canonical path since L1a. Verify no `src/admin/flash.py` exists and no code imports from it.
2. Migrate `SimpleAppCache` from module globals to `app.state.inventory_cache` (set at lifespan startup).
3. **L6 work item — router subdir reorganization (canonical target structure):** Current post-L2 structure is flat: `src/admin/routers/<feature>.py` (per D8 #6 codemod). L6 reorganizes to domain-grouped subdirectories:

    ```
    src/admin/routers/
      auth/
        __init__.py          # re-exports `router = APIRouter(...)`
        google.py            # GET/POST /auth/google, /auth/google/callback
        oidc.py              # GET /auth/oidc, /auth/oidc/callback, /.well-known/oidc-*
        gam.py               # GET /auth/gam, /auth/gam/callback
        logout.py            # POST /logout
      tenant/                # /tenant/{tenant_id}/* canonical prefix per D1
        __init__.py
        accounts.py
        products.py
        principals.py
        users.py
        creatives.py
        creative_agents.py
        inventory.py
        inventory_profiles.py
        operations.py
        policy.py
        settings.py
        workflows.py
        gam.py
      tenants.py             # /tenant list/create (not tenant-scoped)
      public.py              # /login, /logout, /public/*, /about
    ```

    Each subdir `__init__.py` re-exports its `router` symbol. `src/app.py` includes each via `app.include_router(router, prefix="/tenant/{tenant_id}")` or the canonical prefix per D1. Migration is mechanical: ~30 `git mv` operations + 1 import-line edit per moved file (~50 line edits total). Structural guard `tests/unit/test_architecture_router_subdirs_canonical.py` asserts every file in `src/admin/routers/` matches this structure; allowlist EMPTY after L6 exit.
4. Add `logfire` instrumentation: wire `logfire.configure()` in lifespan, add FastAPI auto-instrumentation, integrate with the L4 `structlog` pipeline. **Do NOT install `opentelemetry-sdk`** — `logfire` bundles its own OTLP exporter.
5. Ratchet structural guard allowlists — shrink the pre-existing-debt allowlists now that async conversion is done.

**Exit gate:**
```bash
make quality && ./run_all_tests.sh  # green
# No src/admin/flash.py
# SimpleAppCache lives on app.state
# logfire dashboards show request spans + DB spans + MCP tool spans
```

**What NOT to do:** Do not start the v2.0.0 tag / release yet (that is L7). Do not install `opentelemetry-sdk` — `logfire` replaces it.

---

## Layer 7 — Polish & Ship: allowlists → zero, mypy strict, docs refresh, v2.0.0 tag

**Thesis:** Release readiness. Ratchet all structural-guard allowlists to zero, validate perf baseline, refresh `docs/ARCHITECTURE.md`, tag v2.0.0. (Note: `FLASK_SECRET_KEY` dual-read hard-removal moved to L2 per v2.0 breaking-change alignment.)

**Prerequisites:** L6 merged.

**Work items (in order):**

1. Delete sync artifacts: `get_db_session` sync context manager, `scoped_session` usage (except the sync-bridge scope), residual UoW classes.
2. Delete dead code: `database_schema.py` (confirmed orphan), `product_pricing.py` (Decision 5), dead functions in `queries.py`.
3. ~~Hard-remove `FLASK_SECRET_KEY` dual-read~~ **moved to L2** per v2.0 breaking-change alignment with cookie rename. Verify at L7 that no `FLASK_SECRET_KEY` reads remain anywhere in `src/`, `scripts/`, `docs/`, or tests; structural guard `test_architecture_no_flask_secret_key_reads.py` (added at L2) is green with empty allowlist.
4. Ratchet all structural guard allowlists to zero — no allowlist entries remain.
5. Ratchet mypy strict-mode flags (per-module ratcheting baseline) — target is zero new strict errors.
6. Final performance baseline comparison vs `baseline-sync.json` captured at L4 EXIT. No regression beyond budget.
7. Refresh `docs/ARCHITECTURE.md` with the post-v2.0 architecture (admin FastAPI, MCP, A2A, async DB, sync-bridge).
8. Archive `.claude/notes/flask-to-fastapi/` — promote critical patterns to `docs/`.
9. Remove migration breadcrumb from root `CLAUDE.md`.
10. Write `CHANGELOG.md` v2.0.0 final entry (full breaking changes list — includes async handler signatures and driver change from L5).
11. Bump `pyproject.toml` version to `2.0.0`.
12. Apply `v2.0.0` tag.
13. Production deploy + 48h monitoring: error rates, latency p50/p99, Docker size, cookie size.
14. Update auto-memory to mark migration complete.
15. Delete `feat/v2.0.0-flask-to-fastapi` branch after merge confirmation.
16. **L7 work item — pure-ASGI middleware discipline guard:** Add `tests/unit/test_architecture_middleware_pure_asgi.py` asserting zero `BaseHTTPMiddleware` subclasses in `src/`. Allowlist EMPTY (no genuine streaming-mutation middleware exists in v2.0). Guard monotonic from L7 forward. Prevents a future maintainer from silently regressing to `BaseHTTPMiddleware` for a logging shim and losing the pure-ASGI invariant per `CLAUDE.md` §2026-native baseline.
17. **L7 work item — psycopg2 retention paper trail (3 locations):** Post-v2.0, `.claude/notes/flask-to-fastapi/` will be archived. The rationale for keeping `psycopg2-binary` (Decision 2 fork-safety + Decision 1 Path-B adapter wrap; Decision 9 sync-bridge eliminated by D3) must live in-code so future maintainers don't PR its removal. Land three artifacts:
    - **(a) ADR document:** `docs/adr/007-retain-psycopg2-binary.md` — explains fork-safety + Path-B rationale, lists the 3 allowlisted import sites (`src/core/database/db_config.py`, `src/core/database/database_session.py` if explicit, `src/adapters/*` Path-B wrap chain), references Decisions 1/2, and states the v2.1 sunset plan for adapter async rewrite.
    - **(b) `pyproject.toml` comment:** inline `# Retained: fork-safety (D2) + adapter Path-B (D1) — see docs/adr/007-retain-psycopg2-binary.md` next to `psycopg2-binary` in deps.
    - **(c) Module docstring at `src/core/database/db_config.py` top:** "Retained for fork-safety per Audit 06 Decision 2 OVERRULE 2026-04-11; ONLY callers are `scripts/deploy/run_all_services.py` (PID-1 orchestrator) and `examples/upstream_quickstart.py`. Enforced by `tests/unit/test_architecture_get_db_connection_callers_allowlist.py`. Do NOT replace with `get_db_session()` — the SQLAlchemy engine's inherited connection pool FDs will cause PG socket corruption across the `Popen` fork."
18. **L7 work item — NO-GO release-tag naming verification:** Pre-release checklist confirms the tag is `v2.0.0` (full async shipped) OR `v1.99.0` (Spike 8 NO-GO path, L5+ deferred to v2.1). Mismatch between Spike 8 decision and release-tag name blocks the tag. See folder `CLAUDE.md` §v2.0 Spike Sequence Spike 8 NO-GO naming rule.

**Files to delete:** Sync artifacts (`scoped_session` wrappers, residual UoW classes), `database_schema.py`, `product_pricing.py`, dead `queries.py` functions.
**Files to modify:** `CLAUDE.md` (root), `pyproject.toml`, `docs/ARCHITECTURE.md`, various doc files. (`FLASK_SECRET_KEY` dual-read removal moved to L2.)
**Files to archive/delete:** `.claude/notes/flask-to-fastapi/` contents (after promoting anything worth keeping).

**Exit gate:**
```bash
make quality && ./run_all_tests.sh  # green
# v2.0.0 tag applied, production deploy successful
# 48h monitoring: no 5xx spike, latency stable
# Root CLAUDE.md no longer references active migration
# All planning artifacts archived or deleted
# All structural guard allowlists empty
# Branch deleted
```

**What NOT to do:** Do not drop nginx (post-v2.0). Do not design multi-worker scheduler (v2.2). Do not rush — L7 is the final release gate.
