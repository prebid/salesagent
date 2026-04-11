# Flask → FastAPI Migration: Deep Audit Findings (2nd/3rd order + derivative)

**Date:** 2026-04-11
**Status:** Pre-implementation audit — plan revisions required before Wave 0 begins
**Supersedes:** `flask-to-fastapi-adcp-safety.md` §4 (first-order audit) with deeper analysis

> **Companion to:** [flask-to-fastapi-migration.md](flask-to-fastapi-migration.md) (main overview) and [flask-to-fastapi-adcp-safety.md](flask-to-fastapi-adcp-safety.md) (first-order AdCP boundary audit). This file captures the 2nd-order, 3rd-order, and derivative-thinking findings produced by two parallel Opus plan subagents on 2026-04-11. It identifies **six previously unseen blockers, twenty new risks, and forty-plus cleanup opportunities** that must be reflected in the migration plan before Wave 0 begins.

---

## Bottom line — what changed vs the first-order audit

The first-order audit (adcp-safety.md) identified 8 action items, all internal, and gave the migration a "structurally safe against AdCP spec impact" verdict. **That verdict still holds.** No external AdCP consumer is affected.

But the deeper audit found **six critical blockers** the first-order pass missed because they concern internal migration mechanics rather than AdCP protocol surface. Shipping the plan without fixing these would cause **silent production breakage** on cutover day:

1. 🚨 **`script_root` / `script_name` silent template breakage** — 147 occurrences across 45 templates break because Starlette's `include_router(prefix="/admin")` does NOT set `scope["root_path"]`, but Flask's WSGIMiddleware mount did
2. 🚨 **Trailing-slash handling differs** — Starlette doesn't redirect `/foo` to `/foo/` by default; Flask does. 111 template `url_for()` calls are at risk of 404s
3. 🚨 **`@app.exception_handler(AdCPError)` returns JSON to HTML admin browser users** — human clicks a button, sees raw JSON instead of a friendly error page. UX regression across every admin action path
4. 🚨 **Session scoping on the async event-loop thread** — if admin handlers are `async def` and call sync `get_db_session()`, two concurrent admin requests share the same `scoped_session` identity on the event loop thread, causing transaction interleaving. **The plan's default of `async def` for admin handlers must flip to `def`**
5. 🚨 **Middleware ordering bug: CSRF must run AFTER Approximated, not before** — POSTing to `/admin/*` from an external domain currently produces a 403 (CSRF missing) before the redirect ever fires. Fix is a one-line reorder
6. 🚨 **OAuth redirect URIs are immutable contracts with Google Cloud Console** — if the FastAPI router changes the path even by one character, OAuth fails with `redirect_uri_mismatch`

Plus twenty RISK-level findings (pre-existing bugs surfaced, new bugs introduced by migration mechanics) and forty-plus cleanup OPPORTUNITIES enabled by Flask removal.

---

## Section 1 — The Six New Blockers

### 1.1 `script_root` / `script_name` silent template breakage

**Severity:** 🚨 BLOCKER

**What I verified via runtime introspection:**

```python
app = FastAPI()
admin = APIRouter()

@admin.get('/foo', name='admin_foo')
async def foo(request: Request):
    return {
        'path': request.url.path,            # '/admin/foo'
        'root_path': request.scope.get('root_path', ''),  # '' (empty!)
        'url_for_self': str(request.url_for('admin_foo')),  # 'http://testserver/admin/foo'
    }

app.include_router(admin, prefix='/admin')
```

`include_router(prefix="/admin")` only prepends the prefix to the route's `path`. `scope["root_path"]` stays empty.

**What Flask currently does:** `CustomProxyFix` at `src/admin/app.py:54-102` reads `X-Script-Name`/`X-Forwarded-Prefix` headers and sets `environ["SCRIPT_NAME"]`. Flask's `request.script_root` then returns `/admin`. The `inject_context` context processor at `src/admin/app.py:310` writes `context["script_name"] = request.script_root` into every Jinja render.

**Template impact (greps verified):**
- **147 total occurrences** of `script_name` / `script_root` / `request.script_root` / `request.script_name` across **45 templates**
- **9 occurrences for static paths:** `{{ script_name }}/static/...` in `base.html`, `add_product.html`, `add_product_gam.html`, `add_product_mock.html`, `edit_product.html`, `edit_product_mock.html`, `tenant_settings.html`
- **135 occurrences for admin paths:** `{{ script_name }}/logout`, `{{ script_name }}/tenant/{{ tenant_id }}/settings`, JavaScript `fetch({{ script_name }}/...)`, href links in navigation, etc.
- **4 edge cases** with non-path suffixes

**Post-migration silent failures without a fix:**
1. `{{ script_name }}/logout` → renders `/logout` → 404 (should be `/admin/logout`)
2. `{{ script_name }}/tenant/t1/settings` → renders `/tenant/t1/settings` → 404
3. JavaScript `fetch({{ script_name }}/tenant/.../approve)` → POST to `/tenant/.../approve` → 404 → silent JS failure, no form submission, user sees nothing
4. `{{ script_name }}/static/validation.css` → renders `/static/validation.css` → works by accident if `StaticFiles` is mounted at `/static` on the outer app (which the plan already specifies)

**Root cause:** Flask overloaded `script_name` as both the admin URL prefix AND the static asset prefix, because both coincidentally shared `/admin/` via the WSGIMiddleware mount. Post-migration they diverge (`/admin/*` for routes, `/static/*` for assets).

**Required fix (GREENFIELD — full `url_for` adoption, per user directive upgrading the original Pattern D compromise):**

Every admin route gets `name="admin_<blueprint>_<endpoint>"` on its decorator. `StaticFiles(..., name="static")` is mounted on the outer app. Every URL in every template uses `{{ url_for('admin_...', **params) }}` for admin paths and `{{ url_for('static', path='/...') }}` for static assets. **NO `admin_prefix`/`static_prefix` Jinja globals exist** — they are strictly forbidden and guarded.

This is the canonical FastAPI docs pattern, verified in the live venv:
- `starlette/templating.py:118-129` (`Jinja2Templates._setup_env_defaults`) — auto-registers `url_for` as a `@pass_context`-decorated Jinja global that calls `request.url_for(name, **path_params)`. The `setdefault` at line 129 means a pre-registered override wins, which is how the foundation module's `_url_for` safe-lookup hook installs.
- `starlette/routing.py:434-459` (`Mount.url_path_for`) — handles `url_for('static', path='/foo.css')` via the `name == self.name and "path" in path_params` branch when the mount declares `name="static"`.
- `fastapi/routing.py:1395` — `include_router(prefix="/admin")` passes `name=route.name` through verbatim, so admin route names do NOT get an automatic prefix; the `admin_` prefix must be explicit on the decorator.

**Two-pass codemod** (`scripts/codemod_templates_greenfield.py`):
- Pass 1: `{{ script_name }}/static/foo.css` → `{{ url_for('static', path='/foo.css') }}`
- Pass 1: `{{ script_name }}/tenant/{{ tenant_id }}/settings` → `{{ url_for('admin_tenants_settings', tenant_id=tenant_id) }}` (via `HARDCODED_PATH_TO_ROUTE` map)
- Pass 2: `{{ url_for('accounts.list_accounts', ...) }}` → `{{ url_for('admin_accounts_list_accounts', ...) }}` (via `FLASK_TO_FASTAPI_NAME` map)
- JS template literals with runtime-param URLs → flagged for manual review; handlers pre-resolve base URLs via `js_*_base` context vars set via `str(request.url_for('admin_...', ...))`.
- Idempotent: re-running is a no-op because all patterns key off pre-migration syntax.

**Guard tests (greenfield — replaces Pattern D guards):**
1. `tests/unit/admin/test_templates_no_hardcoded_admin_paths.py` — regex-scans templates for `script_name`, `script_root`, `request.script_root`, `admin_prefix`, `static_prefix`, AND bare `"/admin/..."` / `"/static/..."` string literals inside quotes. Asserts zero matches. Ratchets prevent regression.
2. `tests/unit/admin/test_templates_url_for_resolves.py` — AST-extracts every `url_for('name', ...)` call from every template and asserts `name` exists in `{r.name for r in app.routes}`. Catches `NoMatchFound` footgun at CI time. ~0.5s runtime.
3. `tests/unit/admin/test_architecture_admin_routes_named.py` — AST-scans `src/admin/routers/*.py` and asserts every `@router.<method>(...)` decorator has `name=` kwarg. Required because unnamed routes cannot be targets of `url_for`.
4. `tests/unit/admin/test_oauth_callback_routes_exact_names.py` — byte-pins OAuth callback route names AND paths together (blocker #6 cross-reference). Changing `/admin/auth/google/callback` name or path fails the test.
5. `tests/unit/admin/test_codemod_idempotent.py` — running the codemod twice on the same template produces no additional changes.

**Runtime safety net:** `_url_for` safe-lookup override in `src/admin/templating.py` (pre-registered on `templates.env.globals` before any `TemplateResponse` call) catches `NoMatchFound`, logs the template filename + route name + params, then re-raises. Converts silent 500s into grep-able log lines for production debugging.

**Route naming convention:** `admin_<blueprint>_<endpoint>` (flat, prefixed). Example: `accounts.list_accounts` → `admin_accounts_list_accounts`. The `admin_` prefix is explicit because `include_router(prefix="/admin")` prefixes paths only, not names — and dropping the prefix risks future collisions with `/api/v1/*` protocol route names (e.g., `list_products` at protocol level vs `products_list_products` at admin level).

**Wave assignment:** Wave 0 (foundation + codemod). Cannot ship without this fix.

---

### 1.2 Trailing-slash handling differs between Flask and Starlette

**Severity:** 🚨 BLOCKER

**The mechanism:** Flask's `strict_slashes=False` (default on most routes) means `/foo` and `/foo/` both resolve. Starlette routes do NOT — a route registered at `/foo` returns 404 for `/foo/` by default.

**Impact:** 111 `url_for()` calls across 30 templates. Any mismatch between how the template writes the URL and how the new FastAPI router registers the path = 404. Admin UI breaks invisibly.

**Concrete example:**
- Template: `<a href="{{ url_for('tenants.list') }}">Tenants</a>`
- Old Flask route: `@tenants_bp.route("/tenant")` (no trailing slash, matches both)
- New FastAPI route: `@router.get("/tenant", name="tenants_list")` (matches only `/tenant`, 404 on `/tenant/`)
- If a user has a bookmark at `/admin/tenant/` (with slash), they hit 404 post-migration

**Fix:** FastAPI has a `redirect_slashes=True` default on the main `APIRouter`, BUT it's **not inherited by sub-routers** added via `include_router`. The plan must explicitly set `redirect_slashes=True` on every admin `APIRouter()` construction:

```python
# In each src/admin/routers/*.py
router = APIRouter(redirect_slashes=True, include_in_schema=False)
```

OR set on the aggregated admin router in `build_admin_router()`:
```python
def build_admin_router() -> APIRouter:
    router = APIRouter(redirect_slashes=True, include_in_schema=False)
    router.include_router(tenants_router)
    # ... etc
    return router
```

**Guard test:** `tests/admin/test_trailing_slash_tolerance.py` — iterates every registered admin route, for each one hits both `path` and `path + "/"` via TestClient, asserts neither returns 404 (they may return the same 200 OR a 307 redirect, both acceptable).

**Wave assignment:** Wave 0 (foundation). Easy one-line fix. Critical.

---

### 1.3 `@app.exception_handler(AdCPError)` returns JSON to HTML admin requests

**Severity:** 🚨 BLOCKER

**Current behavior:** `src/app.py:82-88`:
```python
@app.exception_handler(AdCPError)
async def adcp_error_handler(request: Request, exc: AdCPError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.to_dict())
```

**Post-migration problem:** Admin handlers call into shared repositories and helpers (`UoW`, service layer) that can raise `AdCPError` subclasses (`AdCPNotFoundError`, `AdCPValidationError`, etc.). Today's Flask admin catches these at the blueprint level or renders a Flask default 500 error page. Post-migration, the top-level FastAPI handler catches `AdCPError` and returns **JSON to a human's browser**.

**Concrete scenario:**
1. Admin clicks "Create Product" in the browser
2. Form POST → admin router → repository call → `_impl` helper → raises `AdCPValidationError("sku is required")`
3. FastAPI `@app.exception_handler(AdCPError)` catches it
4. Returns `{"error_code": "validation_error", "message": "sku is required", ...}` as JSON
5. Browser displays raw JSON blob — terrible UX regression

**Fix:** Make the handler Accept-aware. If the request is under `/admin/*` AND the `Accept` header contains `text/html`, render a Jinja `error.html` template. Otherwise return JSON.

```python
@app.exception_handler(AdCPError)
async def adcp_error_handler(request: Request, exc: AdCPError):
    accept = request.headers.get("accept", "")
    is_html_admin = request.url.path.startswith("/admin") and "text/html" in accept
    if is_html_admin:
        from src.admin.templating import templates
        return templates.TemplateResponse(
            "error.html",
            {"request": request, "error": exc.to_dict(), "status_code": exc.status_code},
            status_code=exc.status_code,
        )
    return JSONResponse(status_code=exc.status_code, content=exc.to_dict())
```

**Also needs:** `templates/error.html` — a simple Jinja template that extends `base.html` and renders the error message + a back link. Currently there is no such template.

**Guard test:** `tests/integration/test_admin_error_page.py` — force a `AdCPValidationError` to raise from within an admin route, assert the response is HTML not JSON, assert the HTML contains the error message.

**Wave assignment:** Wave 1 (foundation routers land). Add the error page template in Wave 0.

---

### 1.4 Session scoping on the async event-loop thread — PIVOTED 2026-04-11 to full async SQLAlchemy (Option A, absorbed into v2.0)

**Severity:** 🚨 BLOCKER (architectural default change)

**Status (2026-04-11):** This blocker's resolution has PIVOTED. The original analysis proposed sync `def` admin handlers (Option C in the list below) as a scope-reduction compromise to defer async SQLAlchemy to v2.1. User directive on 2026-04-11 reversed this: v2.0 absorbs full async SQLAlchemy (Option A from the list below), eliminating the `scoped_session` race entirely rather than working around it. **The "sync def handler" resolution text below is historical context — the new plan is Option A. See `async-pivot-checkpoint.md` for the full new target state.**

**The mechanism (unchanged — this is still what's broken today):** `src/core/database/database_session.py:148` uses:
```python
SessionLocal = scoped_session(sessionmaker(bind=_engine))
```

`scoped_session` with default scopefunc uses `threading.get_ident()` — one session per thread. Under Flask + `a2wsgi.WSGIMiddleware`, each request spins up a dedicated worker thread, so each request gets its own session identity. **Isolated.**

Under the proposed FastAPI migration with `async def` handlers and unchanged sync SQLAlchemy:
- Multiple concurrent admin requests run on the **same event loop thread**
- Each request's `with get_db_session()` block gets the **same** scoped_session identity
- If request A commits mid-transaction and request B is still writing, they share a transaction
- Stale reads, duplicate commits, silently corrupted state

**Current state check (verified):** `rg 'run_in_threadpool' src/` returns 0 matches. Today's AdCP REST endpoints in `src/routes/api_v1.py` are already `async def` and call `_impl` directly without threadpool offload. **This is already a pre-existing latent bug** — if two concurrent AdCP REST requests touch the DB at the same time, they interleave. It hasn't bitten production because traffic is low and `scoped_session` happens to commit quickly.

**The migration plan would make this worse** by adding ~232 admin routes with DB access.

**Fix options (historical, for context):**

- **Option A: Switch to async SQLAlchemy end-to-end.** Replace `scoped_session(sessionmaker(...))` with `async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)`. Admin handlers become `async def` with `async with get_db_session()` / `await session.execute(...)`. The scoped_session thread-identity race is eliminated entirely because `AsyncSession` does not use thread-identity scoping. Driver moves from `psycopg2-binary` to `asyncpg`. Correct long-term, touches 100+ files, requires careful lazy-loading audit, but fixes the pre-existing `src/routes/api_v1.py` latent bug as a side effect.
- **Option B: Every `async def` admin handler wraps sync DB calls in `run_in_threadpool(_sync_fetch)`.** Feasible but bug-prone — one forgotten wrap causes session interleaving. Not chosen.
- **Option C: Default admin handlers to sync `def`.** FastAPI auto-offloads to threadpool workers; each worker thread has its own session identity so `scoped_session` isolates correctly. Matches today's Flask semantics. Minimal v2.0 scope. Does NOT fix the pre-existing REST latent bug (which is on `async def` handlers). Was the pre-pivot choice.

**Resolution: Option A (chosen 2026-04-11).** The user directive absorbed async SQLAlchemy into v2.0 as Waves 4-5. Rationale:

1. **Greenfield 2026 FastAPI codebases write fully async code.** Sync `def` + threadpool is a scope-reduction hack, not the end state.
2. **Fixes a pre-existing latent bug as a side effect.** `src/routes/api_v1.py` already has the scoped_session race on async tasks. Option A eliminates it; Option C leaves it intact.
3. **Eliminates the v2.1 async follow-on from the roadmap.** One migration, one branch, one release.
4. **AdCP schema impact: zero.** Verified — wire format, MCP tool signatures, A2A protocol, REST endpoint bodies, OpenAPI surface, auth context, `AdCPError` hierarchy, webhook payloads — all unchanged. The pivot is purely an internal implementation-language change. Full verification in `async-pivot-checkpoint.md` §9.

**Scope implication:** v2.0 grows from ~18,000 LOC (original estimate) to ~30,000-35,000 LOC; wave count grows from 4 to 5-6 (adding Wave 4 = async DB layer, Wave 5 = async cleanup + release).

**Pre-Wave-0 lazy-loading audit spike (MANDATORY before committing to Option A scope):** `relationship()` access sites in SQLAlchemy lazily load under AsyncSession only within an active async session scope — out-of-scope access raises `sqlalchemy.exc.MissingGreenlet` (a HARD FAILURE). The audit enumerates every `relationship()` definition in `src/core/database/models/` and classifies every access site as safe (in-scope), fixable (eager-load via `selectinload`/`joinedload`), or requiring rewrite. If the audit reveals the scope is untenable, fall back to Option C and defer async to v2.1. Estimated effort: 1-3 days. See `async-pivot-checkpoint.md` §4 Risk #1 for the full audit procedure.

**Plan changes required (under Option A):**
1. Foundation modules (`flask-to-fastapi-foundation-modules.md`) — rewrite `get_db_session` call sites to `async with`; rewrite repository examples to `await session.execute(...)`; rewrite UoW classes to `async def __aenter__` / `async def __aexit__`
2. Worked examples (`flask-to-fastapi-worked-examples.md`) — every handler is `async def`; every DB call-site is `async with` / `await`
3. Main overview §13 — already updated to `async def` examples
4. Replace the original structural guard `test_architecture_admin_sync_db_no_async.py` (wrong direction under Option A) with `test_architecture_admin_routes_async.py` (AST-scans admin routers and asserts every `@router.<method>(...)` handler is `async def`). Sibling guard `test_architecture_admin_async_db_access.py` asserts DB access uses `async with get_db_session()` + `await session.execute(...)`, not sync `with` or `run_in_threadpool` wrappers
5. Dependency changes: remove `psycopg2-binary`, `types-psycopg2`; add `asyncpg>=0.30.0`; add `pytest-asyncio` (or equivalent); explicit `sqlalchemy[asyncio]` extra
6. `tests/harness/_base.py::IntegrationEnv` becomes `async def __aenter__` / `async def __aexit__`; integration tests mass-convert to `async def` + `@pytest.mark.asyncio`
7. `alembic/env.py` async adapter (standard SQLAlchemy pattern, ~30 LOC)
8. `factory_boy` adapter (evaluate three options in checkpoint §3)
9. Benchmark harness compares async vs pre-migration sync baseline, not threadpool-overhead

**What stays `async def` for different reasons** (unchanged under the pivot — these were already correctly async):
- OAuth callbacks (await Authlib)
- SSE handlers (async generators + await `request.is_disconnected()`)
- Outbound webhook senders (await httpx)

**What `run_in_threadpool` is still used for** (non-DB blocking operations only):
- File I/O (favicon upload, image writes)
- CPU-bound synchronous work (image processing, sync cryptography libs)
- Third-party sync libraries that cannot be made async

**What `run_in_threadpool` is NEVER used for under Option A:** DB access. That path is always `async with get_db_session()` / `await session.execute(...)`.

**Wave assignment:** Wave 0 (decide the default before any router ports) AND Wave 4-5 (absorb async SQLAlchemy migration). The Wave 4-5 entry gate is the pre-Wave-0 lazy-loading audit spike outcome.

---

### 1.5 Middleware ordering bug — CSRF must run AFTER Approximated redirect

**Severity:** 🚨 BLOCKER (one-line fix in the plan)

**The problem:** The plan's proposed middleware order is:
```python
# outermost → innermost
CORSMiddleware
SessionMiddleware
CSRFMiddleware                         # ← BEFORE approximated
ApproximatedExternalDomainMiddleware   # ← AFTER CSRF
RestCompatMiddleware
UnifiedAuthMiddleware
```

**Failure scenario:** A user on `ads.example.com` (external domain proxied by Approximated) POSTs a form to `/admin/tenant/t1/accounts/create`. The request carries `Apx-Incoming-Host: ads.example.com`.

1. CORS → pass
2. Session → reads cookies. Browser has no `.scope3.com` session cookie (different domain). `scope["session"] = {}`
3. **CSRF → POST to `/admin/*`, not exempt. Checks for CSRF token. User has NO session, so NO CSRF cookie exists. Returns 403 Forbidden.**
4. *Approximated redirect never fires.* User sees "CSRF token missing" instead of being bounced to the canonical subdomain.

The entire external-domain onboarding flow is broken by the middleware order.

**Fix:** Swap. Approximated runs BEFORE CSRF, so users with no canonical-subdomain session get redirected cleanly:

```python
# Corrected order (outermost → innermost)
CORSMiddleware
SessionMiddleware
ApproximatedExternalDomainMiddleware   # ← MOVED UP
CSRFMiddleware
RestCompatMiddleware
UnifiedAuthMiddleware
```

Justification: the Approximated middleware doesn't need session state; it only reads `Apx-Incoming-Host` and looks up a tenant by virtual_host. Moving it earlier in the stack is safe and correct.

**Guard test:** `tests/integration/test_external_domain_post_redirects_before_csrf.py` — simulates a POST to `/admin/tenant/t1/accounts/create` with `Apx-Incoming-Host: ads.example.com`, no CSRF token, no session. Asserts the response is a 307 redirect (not a 403).

**Related plan correction:** switch the redirect code from 302 to **307** (method + body preserving). RFC 7231 §6.4.3 says 302 does NOT preserve POST body; 307 does. Current Flask issues 302 — this is a latent bug that the migration should fix while rewriting.

**Wave assignment:** Wave 1 (when middleware stack is wired).

---

### 1.6 OAuth redirect URI byte-identity requirement

**Severity:** 🚨 BLOCKER

**The constraint:** `src/admin/blueprints/auth.py:386-406` constructs OAuth redirect URIs referenced in Google Cloud Console configuration:
```python
redirect_uri = os.environ.get("GOOGLE_OAUTH_REDIRECT_URI")
# ...
redirect_uri = base_url.replace("/auth/google/callback", "/admin/auth/google/callback")
```

Google Cloud Console has these URIs pre-registered:
- `https://<tenant>.scope3.com/admin/auth/google/callback`
- `https://<tenant>.scope3.com/admin/auth/oidc/{tenant_id}/callback`
- `https://<tenant>.scope3.com/auth/gam/callback` (GAM OAuth flow)

**The risk:** during the migration, it's easy to "clean up" these paths by moving them into the admin router (`/admin/auth/...`). If the new router is mounted at `prefix="/admin"` and the route is registered as `@router.get("/auth/google/callback", ...)`, the effective path is `/admin/auth/google/callback` — **this matches**.

BUT if the route is accidentally registered as `@router.get("/admin/auth/google/callback", ...)` (forgetting the prefix is applied automatically), the effective path becomes `/admin/admin/auth/google/callback` — broken. Google returns `redirect_uri_mismatch` and **login is broken in production**.

**Required guard:** a pre-Wave-2 test that asserts the registered callback paths match **exactly**:

```python
# tests/unit/test_oauth_redirect_uris_immutable.py
EXPECTED_CALLBACK_ROUTES = {
    "/admin/auth/google/callback",
    "/admin/auth/oidc/{tenant_id}/callback",
    "/auth/gam/callback",   # note: NOT under /admin prefix
}

def test_oauth_callback_routes_registered():
    registered = {r.path for r in app.routes if hasattr(r, "path")}
    missing = EXPECTED_CALLBACK_ROUTES - registered
    assert not missing, f"OAuth callback routes missing from app.routes: {missing}"
```

Plus a **Wave 1 staging smoke test**: actually walk the Google OAuth flow against staging, verify the callback resolves without `redirect_uri_mismatch`.

**Wave assignment:** Wave 1 (guard test), Wave 1-end (staging smoke). Do not advance to Wave 2 without staging OAuth verification.

---

## Section 2 — Implicit Flask Invariants That Would Silently Break

Beyond the six blockers, several Flask-specific behaviors are implicit in the current code and must be preserved in the port.

### 2.1 `require_tenant_access` writes to `flask.g` (1 site, not 3)

**Finding:** The original audit said "3 `g.user` write sites". Actual count via grep: **1 write site** at `src/admin/utils/audit_decorator.py:18` (imports `g`) using it at line 136-139 to cache an `AuditLogger` instance per request.

**Pre-existing bug in Flask:** `require_tenant_access` at `src/admin/utils/helpers.py:291-372` **does NOT check `tenant.is_active`**. Line 313 queries `Tenant` for `auth_setup_mode` but doesn't filter by active status. Inactive tenants are still accessible to users with valid sessions. This is a latent bug worth fixing during migration.

**Action:** the new `CurrentTenantDep` in FastAPI MUST filter `is_active=True` on the Tenant query. This is a behavior change vs Flask, but it's a bug fix, not a regression.

### 2.2 `audit_decorator` is Flask-specific and needs async rewrite

**File:** `src/admin/utils/audit_decorator.py`

**Findings:**
1. Reads `session["user"]` directly — FastAPI equivalent: `request.session["user"]` via Starlette SessionMiddleware
2. Reads `request.form`, `request.is_json`, `request.get_json()` — all Flask-specific. FastAPI async equivalents: `await request.form()`, `await request.json()`. **The decorator becomes async.**
3. **Body consumption hazard:** `request.get_json()` reads the body. In FastAPI, reading the body consumes the stream. The decorator currently calls `f()` first then reads body in the `finally` block — but in FastAPI, by that time the body is already consumed by the handler. Must cache in `request.state` early.
4. Uses `flask.g` for per-request audit logger cache — FastAPI equivalent: `request.state.audit_logger_<tid>`. Straightforward.
5. Extracts `tenant_id` from `kwargs.get("tenant_id")` — FastAPI path params come through the handler signature, not kwargs. Decorator needs per-route adaptation.

**Required port:** rewrite as a FastAPI `Depends()` factory:
```python
def audit_action(action_name: str):
    async def dep(request: Request, user: AdminUserDep):
        # Runs before handler. Capture intent (action_name, user, path).
        # Inject a callback into request.state to log on success.
        log = AuditLog(action=action_name, user=user.email, ...)
        request.state.audit_log = log
        yield
        # Runs after handler. Commit the log.
        log.status = "success"
        log.commit()
    return Depends(dep)
```

But this pattern has its own pitfalls — dep-based audit can't see the response body or exception. For admin UI, "logged before returning" is good enough. For finer-grained audit, a middleware is better.

**Severity:** ⚠️ RISK. The decorator must be re-written, not ported one-for-one.

### 2.3 `log_auth_cookies` after_request reads `flask.session.modified`

**File:** `src/admin/app.py:272-295` (24 lines)

Flask tracks `session.modified = True` on any mutation. Starlette's `SessionMiddleware` has no public equivalent — it tracks internally. The debug-log message "NO Set-Cookie on {path} (session.modified=True)" cannot be ported faithfully.

**Action:** drop the handler entirely. Any replacement would be debug noise.

### 2.4 `inject_context` runs a DB lookup per template render

**File:** `src/admin/app.py:298-330`

Every Jinja render hits the `Tenant` table if `session["tenant_id"]` is set. Swallows exceptions. Uses raw `select(Tenant).filter_by(tenant_id=tenant_id)` — not via a repository.

**Pattern #1 violation:** raw `select(OrmModel)` outside repositories. The existing `test_architecture_no_raw_select.py` allowlist permits this. If the port moves the logic to a new module, the allowlist needs an entry — but CLAUDE.md says "new files are never added to allowlists." **Required fix: move the tenant load to a repository method, or inline the call into the `render()` wrapper and add the file to the allowlist only if the guard test permits.**

**Performance:** every template render = one DB query. For a dashboard page that renders 1 template, that's 1 query. For a tenant settings page that renders a template with nested partials, still 1 query (Jinja context is per-response, not per-partial). Acceptable.

**Port:** the `render()` wrapper in `src/admin/templating.py` calls a helper:
```python
def _load_current_tenant(request: Request) -> dict | None:
    tenant_id = request.session.get("tenant_id")
    if not tenant_id:
        return None
    with get_db_session() as db:
        tenant = TenantRepository(db).get_by_id(tenant_id)
    return tenant.to_dict() if tenant else None
```

Uses a repository, not raw select. Structural guard happy.

### 2.5 `CustomProxyFix` / `FlyHeadersMiddleware` / `werkzeug.ProxyFix` triple stack

**File:** `src/admin/app.py:186-191`

Three WSGI middlewares stacked on `app.wsgi_app`:
1. `WerkzeugProxyFix(x_for=1, x_proto=1, x_host=1, x_prefix=0)` — handles standard `X-Forwarded-*` headers
2. `FlyHeadersMiddleware` — copies `Fly-Forwarded-Proto` → `X-Forwarded-Proto`
3. `CustomProxyFix` — handles `X-Script-Name` / `X-Forwarded-Prefix` for `SCRIPT_NAME` injection

**FastAPI replacement:**
- `WerkzeugProxyFix` → `uvicorn --proxy-headers --forwarded-allow-ips='*'` (built-in)
- `FlyHeadersMiddleware` → tiny Starlette `ProxyHeadersMiddleware` or verify Fly now sends standard `X-Forwarded-*` (they started mid-2024)
- `CustomProxyFix` → **NOT NEEDED** post-migration. `SCRIPT_NAME` juggling was only because Flask was mounted via `a2wsgi.WSGIMiddleware`. In native FastAPI there's no mount, so no need to inject `SCRIPT_NAME`.

**Action:** delete all three. Add `--proxy-headers --forwarded-allow-ips='*'` to uvicorn invocation in `scripts/run_server.py`. Verify Fly proxy behavior in staging. If Fly still sends only `Fly-*` headers, add a ~30-LOC pure-ASGI middleware that copies them.

**Severity:** ⚠️ RISK — forgetting the uvicorn flags breaks `request.url.scheme` in production (returns `http` instead of `https`), which breaks OAuth redirect URI construction, which breaks login.

### 2.6 `SESSION_COOKIE_HTTPONLY=False` in production

**File:** `src/admin/app.py:114-131`

Flask explicitly sets `SESSION_COOKIE_HTTPONLY=False` in production, with a comment saying "for EventSource compatibility". **This is cargo-culted and wrong** — browsers send HttpOnly cookies on EventSource requests automatically. HttpOnly only prevents JavaScript from READING the cookie via `document.cookie`.

**Starlette's `SessionMiddleware` does NOT support `httponly=False`.** It always sets HttpOnly. This is actually the MORE SECURE default.

**Risk:** if any admin JavaScript currently reads the session cookie via `document.cookie` (it shouldn't, but verify), that code breaks.

**Action:** grep `src/admin/static/*.js` and `templates/**/*.html` for `document.cookie`. If zero hits, drop the `HTTPONLY=False` override. If hits exist, fix them to not read the cookie.

### 2.7 `/static/*` is currently served by a Flask blueprint route

**File:** `src/admin/blueprints/core.py:506-509`
```python
@core_bp.route("/static/<path:path>")
def static_files(path):
    return send_from_directory("static", path)
```

Relative directory path — assumes CWD is the project root. Containers run from `/app`, so `static` resolves to `/app/static`. This is a fallback — Flask's built-in `static_folder` also serves `/static`.

**Post-migration:** the outer FastAPI app mounts `StaticFiles(directory="src/admin/static")` at `/static` (per the plan's move of `/static/` → `src/admin/static/`). The blueprint route goes away.

**Risk:** if the CWD assumption differs in any test or deployment, static file serving breaks. The Dockerfile sets `WORKDIR /app`, and `src/admin/static` resolves correctly. Verify in staging.

### 2.8 `@schemas_bp.errorhandler(404/500)` is externally visible

**File:** `src/admin/blueprints/schemas.py:176, 195`

The schemas blueprint serves `/schemas/adcp/v2.4/*` — externally consumed by AdCP JSON-Schema validators. Its blueprint-level 404/500 handlers return:
```json
{"error": "Schema not found", "available_endpoints": [...]}
```

Not FastAPI's default `{"detail": "Not Found"}`. **Any external validator that tests 404/500 paths sees this shape.** If the migration naively converts `abort(404)` to `raise HTTPException(404)`, the response body shape changes.

**Required:** Wave 2 contract test pins the 404/500 body shape and headers for `/schemas/adcp/v2.4/*` paths. The port must register custom scoped exception handlers OR include explicit fallback response construction in the handler.

**Severity:** ⚠️ RISK (external contract).

---

### 2.9 Deployment entrypoint has THREE sync-psycopg2 paths (Agent F finding — pivoted 2026-04-11)

**Severity:** 🚨 BLOCKER (hard-fails Wave 4 container startup)

Deep audit did not catch `scripts/deploy/run_all_services.py` as a sync-DB path because it uses the `DatabaseConnection` class from `src/core/database/db_config.py` instead of `get_db_session()`. Under the async pivot (when `psycopg2-binary` is removed from `pyproject.toml`), three paths break simultaneously:

1. **`scripts/deploy/entrypoint_admin.sh:9`** does `python -c "import psycopg2; psycopg2.connect('${DATABASE_URL}')"`. This is a shell probe that runs before any Python app starts. When `psycopg2` is removed from `pyproject.toml`, this line fails and the container refuses to start. **Hard blocker for Wave 4 merge.**
2. **`scripts/deploy/run_all_services.py:65-125`** (`check_database_health()`) calls `get_db_connection()` → `psycopg2.connect(...)` for a startup health check. Same issue.
3. **`scripts/deploy/run_all_services.py:128-164`** (`check_schema_issues()`) calls `get_db_connection()` → `psycopg2.connect(...)` for a schema audit. Same issue.
4. **`src/core/database/db_config.py:105-172`** `DatabaseConnection` class is a sync-psycopg2 wrapper independent from SQLAlchemy. Used only by the three call sites above.

**Mitigation plan (Option D, recommended):**
- Delete `DatabaseConnection` class and `get_db_connection()` helper
- Replace with a 5-line `asyncio.run(asyncpg.connect(...).fetchval(...))` utility in `scripts/deploy/run_all_services.py`
- Delete `entrypoint_admin.sh` in Wave 3 cleanup (it references `flask_caching` which is also going away, and is not wired up in the current `Dockerfile` entrypoint — orphan)

**Why this matters:** deep audit Blocker #4 focused on SQLAlchemy scoped_session. This is a parallel sync-DB path that deep audit did not surface because it's in deployment scripts, not application code. All three paths must be rewritten or deleted in Wave 4 alongside the `pyproject.toml` driver swap, or the container will fail `docker build` / startup probes.

**Wave assignment:** Wave 4 (same PR as the `psycopg2-binary` removal).

---

## Section 3 — Shared Infrastructure Interactions (2nd order)

### 3.1 Scheduler singleton risk under multi-worker

**Current state:** `src/core/main.py:82-103` starts webhook and media-buy-status schedulers as process-local singletons in the lifespan context. `scripts/run_server.py:47` starts uvicorn without `workers=`, so single worker, no issue.

**Post-migration risk:** Flask removal makes the process leaner. It's tempting to add `workers=4` for throughput. The moment that happens:
- Webhooks fire 4× per tick
- Media-buy status polled 4× per tick
- Slack notifications fan out 4× in `src/core/audit_logger.py:232`

**Action:** add a structural guard `tests/unit/test_architecture_single_worker_invariant.py` that asserts `scripts/run_server.py` does not pass `workers > 1`. Document in the plan: "v2.0 MUST remain single-worker; multi-worker is a v2.2 follow-up that requires scheduler leasing (Postgres advisory lock or a separate scheduler container)."

### 3.2 SSE per-tenant rate limit is per-process state

**File:** `src/admin/blueprints/activity_stream.py:22-24`

Module-level `connection_counts: dict[str, int]`. Per-process. Under multi-worker this becomes per-worker, so the effective limit is `10 × workers`. Not a security issue at 1 worker, but tied to §3.1.

**Action:** FIXME comment referencing the single-worker invariant. Document the Redis-backed replacement as a v2.2 prerequisite.

### 3.3 Harness `app.dependency_overrides` leakage

**File:** `tests/harness/_base.py:894-913`

The `IntegrationEnv.get_rest_client()` sets `app.dependency_overrides[_require_auth_dep] = lambda: rest_identity`. Teardown at lines 827-832 calls `app.dependency_overrides.clear()`.

**Risk:** the plan adds `get_admin_client()` as a sibling method that sets `app.dependency_overrides[get_admin_user] = ...`. Both methods share the same `app` instance (global FastAPI `src.app.app`). If two `IntegrationEnv` instances are active concurrently (pytest-xdist parallel tests, or nested context managers), their overrides clobber each other.

**Action:** the new `get_admin_client()` must snapshot `app.dependency_overrides` on `__enter__`, restore on `__exit__`. Add a regression test: two `IntegrationEnv` instances simultaneously, verify overrides don't leak.

### 3.4 `FLASK_SECRET_KEY` → `SESSION_SECRET` rename is under-propagated

**Grep results:**
- `src/admin/app.py:110` reads `FLASK_SECRET_KEY`
- `scripts/setup-dev.py:143-144` writes `FLASK_SECRET_KEY`
- `tests/unit/test_setup_dev.py` — 9 occurrences across lines 166-430
- `docs/deployment/environment-variables.md:172, 215` documents it
- `docs/development/troubleshooting.md:440-457` references it
- `scripts/run_admin_ui.py:8-9` sets `FLASK_ENV`/`FLASK_DEBUG`
- `src/admin/server.py:83, 89-90` reads `FLASK_DEBUG`/`FLASK_ENV`
- `docker-compose.yml:93` sets `FLASK_ENV: development`

Plan says "hard-required rename, no fallback". Shipping v2.0 with the rename **breaks `scripts/setup-dev.py` for every new contributor** and fails `test_setup_dev.py`.

**Revised plan directive (supersedes user-confirmed decision #5):** keep a transition period where both names are accepted for v2.0:
```python
SESSION_SECRET = os.environ.get("SESSION_SECRET") or os.environ.get("FLASK_SECRET_KEY")
if not SESSION_SECRET:
    raise RuntimeError("SESSION_SECRET env var required (FLASK_SECRET_KEY deprecated)")
```

Atomic cleanup in v2.1: remove the fallback, remove `FLASK_SECRET_KEY` from `setup-dev.py`, `docker-compose.yml`, `docs`, and `test_setup_dev.py`.

**Reason to revise:** the user's "hard-required" directive was made before verifying the propagation cost. The fallback is 3 lines and preserves dev ergonomics without any security compromise.

### 3.5 `jsonify(datetime)` format difference Flask vs FastAPI

**Flask** `jsonify({"x": datetime(2024, 1, 1)})` → `{"x": "Mon, 01 Jan 2024 00:00:00 GMT"}` (HTTP date format)
**FastAPI** via `JSONResponse` → `{"x": "2024-01-01T00:00:00"}` (ISO 8601)

**Impact:** admin UI JavaScript parses dates from AJAX responses. If the server switches format, existing JS `new Date(response.timestamp)` may break for the HTTP-date format fallback path.

**Action:** audit every `jsonify({...})` call in admin blueprints for raw `datetime` values in the dict. If any are found, ensure they're explicitly converted to `.isoformat()` before serialization. Spot check: `src/admin/blueprints/inventory.py` has 13 `.isoformat()` calls (good pattern), but `gam.py` has only 6 (needs broader check).

**Severity:** 🟡 YELLOW — likely already fine, but verify.

### 3.6 CORS applies globally to admin too

**File:** `src/app.py:285-293`

`CORSMiddleware` is registered on the app before any admin mount. This means admin routes also get CORS headers. With `allow_credentials=True`, a cross-origin browser context could submit requests to admin endpoints if it's in the `ALLOWED_ORIGINS` list.

**Action:** scope CORS away from `/admin/*`, OR ensure `ALLOWED_ORIGINS` only contains trusted-admin origins. Document the constraint in `pyproject.toml` or a runtime assertion.

### 3.7 MCP scheduler lifespan-composition dependency (silent-failure footgun)

**Source of truth:** `src/core/main.py:82-103` starts `delivery_webhook_scheduler` and `media_buy_status_scheduler` inside `lifespan_context` (the FastMCP lifespan). Those schedulers reach uvicorn's event loop **only because** `src/app.py:68` composes lifespans via `combine_lifespans(app_lifespan, mcp_app.lifespan)`.

**Silent-failure modes a future refactor could trigger:**
- Dropping the MCP mount → schedulers stop (no yield in lifespan chain)
- Rewiring lifespans to run `app_lifespan` alone without composing `mcp_app.lifespan` → schedulers stop
- Moving schedulers out of `lifespan_context` into something not reached by the uvicorn ASGI lifespan protocol → schedulers stop
- Setting uvicorn `workers > 1` → schedulers start 4× per tick (not silent — loud and correlated with traffic spikes, already documented in §3.1)

**Severity:** ⚠️ RISK (future-refactor footgun; v2.0 does not touch this). Deletion of the MCP mount during a hypothetical "drop MCP and integrate tools directly into FastAPI" refactor would silently stop webhook delivery and media-buy status polling, with no error and no log line — the only visible symptom is that webhooks stop firing and status fields go stale.

**Required action (document as hard constraint, add guard):**
1. Add a Wave-0 structural guard `tests/unit/test_architecture_scheduler_lifespan_composition.py` that parses `src/app.py`, finds the `FastAPI(...)` constructor call, and asserts the `lifespan=` kwarg literally contains `combine_lifespans(app_lifespan, mcp_app.lifespan)`. Refactors that change the composition without updating this test fail at CI time.
2. Add a startup log line at the first scheduler tick: `"delivery_webhook_scheduler alive"` / `"media_buy_status_scheduler alive"`. Missing log lines in production surface the failure within 60 seconds instead of hours.
3. Document the lifespan-composition invariant in `src/app.py` as a load-bearing comment next to the `combine_lifespans` call.

### 3.8 A2A is grafted onto the root app, not mounted as a sub-app

**Source of truth:** `src/app.py:118-123` calls `a2a_app.add_routes_to_app(app, ...)` which injects the SDK's Starlette `Route` objects directly into `app.router.routes` at the top level. This is NOT `app.mount("/a2a", a2a_app)` — it's a different mechanism with different consequences.

**Why this matters:**
- A2A handlers sit at the top level of the FastAPI router tree, inside the same ASGI scope as everything else
- FastAPI middleware (`UnifiedAuthMiddleware`, `RestCompatMiddleware`, `CORSMiddleware`, plus the future `SessionMiddleware`/`CSRFMiddleware`/`ApproximatedExternalDomainMiddleware` from Wave 1) all reach A2A handlers because they share the root scope
- `scope["state"]["auth_context"]` propagates cleanly into A2A handlers
- `_replace_routes()` at `src/app.py:192-215` walks `app.routes` to find the SDK's three static agent-card paths (`/.well-known/agent-card.json`, `/.well-known/agent.json`, `/agent.json`) and swaps them for dynamic `Route(path, dynamic_agent_card, methods=[...])` objects that read `Apx-Incoming-Host`/`Host` headers and emit tenant-aware agent cards. This swap depends on the A2A routes being visible at the top level of `app.routes`.

**Silent-failure modes a future refactor could trigger:**
- Switching to `app.mount("/a2a", a2a_starlette_app)` "to be consistent with MCP" → breaks middleware propagation (sub-apps have isolated middleware stacks). `scope["state"]["auth_context"]` would not reach A2A handlers. Auth might still work by coincidence if A2A sets its own via a context builder, but CSRF/CORS/RestCompat silently drop out.
- Mounting as a sub-app also breaks `_replace_routes()` — the sub-app's internal routes would not be visible to `app.routes` iteration, and the dynamic agent-card swap would silently skip the SDK routes. Agent cards would then return the SDK's hard-coded default URLs instead of tenant-aware URLs read from `Apx-Incoming-Host`.
- A future `include_router(a2a_router)` refactor would have the same problem if `a2a_router` is an `APIRouter` — unless it's explicitly included with `prefix=""`, route-name conflicts could arise.

**Severity:** 🟡 YELLOW (future-refactor footgun; v2.0 does not touch this). The v2.0 migration preserves the grafted-routes pattern; this section documents the constraint so Wave-N refactors don't accidentally "improve" A2A integration by mounting it as a sub-app.

**Required action (document as architectural constraint):**
1. Add a code comment in `src/app.py` at line 118 explaining that `add_routes_to_app` (not `mount`) is deliberate and why.
2. Add a Wave-0 structural guard `tests/unit/test_architecture_a2a_routes_grafted.py` that asserts the A2A routes (`/a2a`, `/.well-known/agent-card.json`, `/agent.json`) appear directly in `app.routes` (not inside a `Mount` subtree). Walks `app.routes` and checks for `Route` objects at those paths without a containing `Mount`.
3. Document the grafted-vs-mounted distinction in `flask-to-fastapi-migration.md` §4.8 (the apps inventory section).

---

## Section 4 — Derivative Opportunities Enabled by the Migration

### 4.1 Drop nginx entirely (~30 MB image savings)

**File:** `config/nginx/nginx-single-tenant.conf` (98 lines)

What nginx does today:
1. TLS termination — but Fly.io does this at the edge externally, so unused inside the container
2. Header forwarding (`Fly-Forwarded-Proto` → `X-Forwarded-Proto`) — can be done by uvicorn `--proxy-headers` + tiny Starlette middleware
3. Gzip compression — uvicorn supports `GZipMiddleware`
4. WebSocket upgrade proxy — uvicorn handles natively

**Post-migration state:** nginx is pure overhead. The container runs nginx + uvicorn + supercronic + the app all via `scripts/deploy/run_all_services.py`. Dropping nginx would:
- ~30 MB image savings (nginx + dependencies)
- Simpler Dockerfile (no `apt-get install nginx`, no `/var/log/nginx`, no `/var/run` permission setup)
- Let uvicorn bind directly to port 8000 (Fly.io expects this)
- Remove one restart-loop failure mode

**Recommendation:** schedule as a **v2.1 follow-on PR** after v2.0 Flask removal stabilizes. Not in scope for v2.0 to keep the migration focused.

### 4.2 Ratchet-migrate REST routes to the `Annotated[T, Depends()]` pattern

**File:** `src/routes/api_v1.py:166-368`

Current style:
```python
async def get_products(body: GetProductsBody, identity: ResolvedIdentity | None = resolve_auth):
```

Admin will use:
```python
async def list_accounts(tenant_id: str, user: AdminUserDep, request: Request, ...):
```

Inconsistency breeds confusion for new engineers. **Ratchet-migrate the 14 REST route signatures to Annotated pattern in a v2.1 follow-on PR.** Guard: `tests/unit/test_architecture_rest_uses_annotated.py` regexes for `= resolve_auth|= require_auth` default values and fails.

### 4.3 Consolidate migration structural guards into one file

The plan adds 5 new guards:
- `test_architecture_no_flask_imports.py`
- `test_templates_url_for_resolves.py`
- `test_architecture_csrf_exempt_covers_adcp.py`
- `test_architecture_approximated_middleware_path_gated.py`
- `test_architecture_admin_routes_excluded_from_openapi.py`

Each is a separate file with its own AST parsing. Consolidation opportunity: one `tests/unit/test_architecture_fastapi_migration_invariants.py` with named sub-tests. Same enforcement, one file to grok.

**Action:** consolidate during Wave 0 to prevent fragmentation. Low effort.

### 4.4 Cleanup deps: `a2wsgi`, `werkzeug`, `waitress`, `flask-caching`, `flask-socketio`, `python-socketio`, `simple-websocket`

**Verified removable** (zero usage in `src/` after Flask is gone):
- `a2wsgi` — only used by `src/app.py:33,299` for mounting Flask
- `werkzeug` — only used by `src/admin/app.py:11` for `ProxyFix`
- `waitress` — only used by `src/admin/server.py` (orphan standalone server)
- `flask-caching` — only used by `src/admin/app.py:200-208` for `Cache(app)`, zero consumer reads of `app.cache`
- `flask-socketio` — zero imports in `src/` (verified via grep)
- `python-socketio` — transitive of flask-socketio
- `simple-websocket` — transitive of flask-socketio

**Image savings:** ~40 MB across all removed deps + transitive.

**Wave assignment:** Wave 3 cleanup PR.

### 4.5 Delete `src/admin/server.py` — orphan standalone server

**File:** `src/admin/server.py` (103 lines)

References:
- Referenced in `scripts/run_admin_ui.py:8-9` (which sets `FLASK_DEBUG`/`FLASK_ENV`)
- NOT referenced in `scripts/run_server.py` (the actual entrypoint)
- NOT referenced in `scripts/deploy/run_all_services.py` (the Docker entrypoint)

**Verdict:** dead code. Delete both files (`src/admin/server.py` and `scripts/run_admin_ui.py`) in Wave 3.

---

## Section 5 — Structural Guard Additions

The plan should add these guards. Some overlap with the original audit's proposals but with more detail:

### 5.1 `tests/unit/test_architecture_admin_routes_async.py` (NEW — pivoted 2026-04-11)

**Purpose:** enforce the full-async admin handler invariant (Blocker 1.4 Option A resolution). Original plan was `test_architecture_admin_sync_db_no_async.py` (asserted async handlers must wrap DB work in `run_in_threadpool`) — that guard was the wrong direction under the full-async pivot and is DELETED.

**Logic:** AST-scan `src/admin/routers/*.py`. For every function decorated with `@router.get/post/put/delete/patch`, assert it is `async def`. Allowlist empty at start. Sibling guard `test_architecture_admin_async_db_access.py` asserts DB call-sites use `async with get_db_session()` + `await session.execute(...)` and NOT `run_in_threadpool(_sync_fetch)` wrappers (which would indicate a sync DB call that slipped through).

### 5.2 `tests/admin/test_templates_no_script_root.py` (NEW)

**Purpose:** prevent `script_root` / `script_name` regressions after codemod (Blocker 1.1).

**Logic:** grep all templates for `script_name|script_root|request\.script_root|request\.script_name`. Assert zero matches. Ratchets prevent regression after the codemod has run.

### 5.3 `tests/admin/test_trailing_slash_tolerance.py` (NEW)

**Purpose:** verify Starlette trailing-slash handling matches Flask's permissive default (Blocker 1.2).

**Logic:** iterate every registered admin route, hit both `path` and `path + "/"` via TestClient, assert neither returns 404.

### 5.4 `tests/unit/test_architecture_admin_html_accept_error_handler.py` (NEW)

**Purpose:** verify the HTML-aware `AdCPError` exception handler renders templates for admin browser users (Blocker 1.3).

**Logic:** monkey-patch an admin route to raise `AdCPError`, set `Accept: text/html` header, assert response `Content-Type: text/html` and body contains the error message.

### 5.5 `tests/unit/test_oauth_redirect_uris_immutable.py` (NEW)

**Purpose:** pin OAuth callback URLs to prevent byte-level drift (Blocker 1.6).

**Logic:** hard-coded `EXPECTED_CALLBACK_ROUTES` set, assert subset of `{r.path for r in app.routes}`.

### 5.6 `tests/integration/test_external_domain_post_redirects_before_csrf.py` (NEW)

**Purpose:** verify middleware ordering — Approximated runs before CSRF (Blocker 1.5).

**Logic:** POST to `/admin/tenant/t1/accounts/create` with `Apx-Incoming-Host: ads.example.com` and no CSRF token. Assert response is 307 (redirect), not 403 (CSRF missing).

### 5.7 `tests/integration/test_schemas_discovery_external_contract.py` (NEW — from first-order audit)

**Purpose:** pin `/schemas/adcp/v2.4/*` external contract (Section 2.8, already identified in first-order audit).

**Logic:** hit `/schemas/adcp/v2.4/index.json`, assert payload keys match; hit an invalid schema name, assert 404 body shape matches Flask's `{"error": "Schema not found", "available_endpoints": [...]}`.

### 5.8 `tests/unit/test_architecture_single_worker_invariant.py` (NEW)

**Purpose:** prevent regression to multi-worker without scheduler redesign (§3.1).

**Logic:** parse `scripts/run_server.py`, assert `workers=` kwarg is absent or `1`.

### 5.9 `tests/unit/test_architecture_harness_overrides_isolated.py` (NEW)

**Purpose:** prevent `app.dependency_overrides` leakage across test env instances (§3.3).

**Logic:** construct two `IntegrationEnv` instances in a nested `with`, verify overrides in inner don't leak out to outer on teardown.

**Total new guards:** 9 (five from the first-order audit section, plus four from the deep audit — 1.4, 1.1, 1.2, 1.3).

---

## Section 6 — Plan Revisions Required (Summary)

The following updates must land in the plan before Wave 0 begins:

### Main overview (`flask-to-fastapi-migration.md`)

1. **Admin handlers are `async def` end-to-end with full async SQLAlchemy** (pivoted 2026-04-11) — update §10, §11, §13 examples; §18 converted from v2.1 deferral to v2.0 Wave 4-5 absorption
2. **Swap middleware order** — Approximated BEFORE CSRF in §10.2
3. **Add §2.8 or §2.9** — deep audit revisions summary pointing at this file
4. **Update §11 foundation** — `render()` wrapper uses `url_for` exclusively (NO `admin_prefix`/`static_prefix`/`script_root` globals); `_url_for` safe-lookup override pre-registered on `templates.env.globals` before first `TemplateResponse`
5. **Update §12 codemod** — handle the `script_name` split, handle trailing slashes, handle 302→307
6. **Update §16 assumptions** — rewrite "admin handlers async def + run_in_threadpool" to "admin handlers async def + full async SQLAlchemy"; rewrite "sync SQLAlchemy stays sync" to "full async SQLAlchemy absorbed into v2.0"
7. **Update §21 verification** — add the 9 new guard tests; rename the sync-db guard to `test_architecture_admin_routes_async.py`
8. **Update §15 dependencies** — remove `psycopg2-binary` + `types-psycopg2`; add `asyncpg>=0.30.0`; explicit `sqlalchemy[asyncio]` extra
9. **Expand §14 wave count from 4 to 5-6** — add Wave 4 (async DB layer) and Wave 5 (async cleanup + release)

### Foundation modules (`flask-to-fastapi-foundation-modules.md`)

1. **`templating.py`** — greenfield: NO `admin_prefix`/`static_prefix`/`script_root` in `render()`; pre-register `_url_for` safe-lookup override on `templates.env.globals` before first `TemplateResponse`; templates use `{{ url_for('admin_<bp>_<endpoint>', **params) }}` for admin paths and `{{ url_for('static', path='/...') }}` for static assets
2. **`csrf.py`** — add `/_internal/` exempt; add `SessionMiddleware` before CSRF ordering note
3. **`deps/auth.py`** — switch example handlers to `def`, note that async is for OAuth/SSE/httpx callers only; fix `AdminRedirect` handler to URL-encode `next_url`
4. **`middleware/external_domain.py`** — add explicit path-gate check; change to 307 instead of 302; add body-preservation test
5. **`app_factory.py`** — `APIRouter(redirect_slashes=True, include_in_schema=False)`; add scoped `AdCPError` exception handler that renders `error.html` for `text/html` Accept on `/admin/*`
6. **`oauth.py`** — add comment referencing OAuth callback URI immutability guard

### Worked examples (`flask-to-fastapi-worked-examples.md`)

1. **All examples** — switch `async def` to `def` where DB access is synchronous (accounts list/create, favicon upload, products form); keep `async def` for OAuth callbacks and SSE
2. **Example 4.1 (Google OAuth)** — document the byte-identical callback URL requirement
3. **Example 4.3 (favicon upload)** — sync `def` handler since it writes files + DB

### Execution details (`flask-to-fastapi-execution-details.md`)

1. **Wave 0 entry criteria** — add decision: sync `def` admin handlers default
2. **Wave 0 acceptance criteria** — add all 9 guard tests green
3. **Wave 1 acceptance criteria** — add OAuth staging smoke test, middleware order verification
4. **Wave 2 acceptance criteria** — add `schemas.py` external contract test, datetime format audit
5. **Wave 3 acceptance criteria** — add nginx removal decision (defer to v2.1)
6. **Part 2 assumptions** — add verification recipes for the 6 new blockers
7. **Part 3 verification** — add the 9 new guard test AST patterns

### ADCP safety audit (`flask-to-fastapi-adcp-safety.md`)

Add a header link pointing to this deeper audit for anything beyond first-order analysis.

---

## Section 7 — Summary Table (all findings)

Sorted by severity:

| # | Severity | Finding | Location | Fix wave |
|---|---|---|---|---|
| B1 | 🚨 BLOCKER | `script_root`/`script_name` silent template break (147 refs, 45 files) — fixed greenfield: `url_for` everywhere (admin routes named `admin_<bp>_<endpoint>`, static named `"static"`), NO `admin_prefix`/`static_prefix` globals | `templates/**/*.html` + `render()` wrapper + `scripts/codemod_templates_greenfield.py` | Wave 0 |
| B2 | 🚨 BLOCKER | Trailing-slash 404 divergence Flask vs Starlette (111 `url_for`) | admin routers + `base.html` | Wave 0 |
| B3 | 🚨 BLOCKER | `@app.exception_handler(AdCPError)` returns JSON to HTML browsers | `src/app.py:82-88` + new `error.html` | Wave 1 |
| B4 | 🚨 BLOCKER | Async event loop session interleaving — pivoted 2026-04-11 to full async SQLAlchemy (Option A) absorbed into v2.0 Waves 4-5 | `src/core/database/database_session.py` + `src/admin/routers/*.py` + alembic env + test harness | Wave 0 decision + Wave 4-5 execution |
| B5 | 🚨 BLOCKER | Middleware order — Approximated must run BEFORE CSRF | `src/app.py` middleware stack | Wave 1 |
| B6 | 🚨 BLOCKER | OAuth redirect URIs must be byte-identical to Google Cloud Console | `src/admin/routers/auth.py` | Wave 1 + staging smoke |
| R1 | ⚠️ RISK | `require_tenant_access` doesn't check `tenant.is_active` (pre-existing) | `src/admin/utils/helpers.py:291-372` | Wave 2 |
| R2 | ⚠️ RISK | `audit_decorator` needs full async rewrite | `src/admin/utils/audit_decorator.py` | Wave 0 foundation |
| R3 | ⚠️ RISK | `inject_context` DB lookup per render — use repository | `src/admin/app.py:298-330` | Wave 0 |
| R4 | ⚠️ RISK | Triple proxy middleware stack — must preserve via uvicorn flags | `src/admin/app.py:186-191` | Wave 1 |
| R5 | ⚠️ RISK | `SESSION_COOKIE_HTTPONLY=False` — verify no JS reads cookie | `src/admin/app.py:114-131` | Wave 0 audit |
| R6 | ⚠️ RISK | `/static/*` serving must mount at outer FastAPI app | `src/admin/blueprints/core.py:506-509` | Wave 1 |
| R7 | ⚠️ RISK | `schemas.py` 404/500 external contract must be preserved | `src/admin/blueprints/schemas.py:176-207` | Wave 2 |
| R8 | ⚠️ RISK | `FLASK_SECRET_KEY` rename breaks setup-dev + 9 tests — add dual-read | `scripts/setup-dev.py`, `tests/unit/test_setup_dev.py` | Wave 1 |
| R9 | ⚠️ RISK | `/_internal/reset-db-pool` has no auth beyond env var gate | `src/routes/health.py:43-79` | Wave 1 |
| R10 | ⚠️ RISK | `src/admin/server.py` + `scripts/run_admin_ui.py` orphan files | delete | Wave 3 |
| R11 | ⚠️ RISK | Workflow step read-after-write consistency (derivative of B4) | `src/admin/blueprints/operations.py` | Wave 2 |
| R12 | ⚠️ RISK | Scheduler singleton under `workers>1` — add single-worker guard | `src/core/main.py:82-103` | Wave 0 guard |
| R13 | ⚠️ RISK | Harness `app.dependency_overrides` leakage | `tests/harness/_base.py` | Wave 0 |
| R14 | ⚠️ RISK | Webhook payload Pydantic serialization (verified safe but audit continues) | `src/admin/blueprints/creatives.py`, `operations.py` | Wave 2 review |
| R15 | ⚠️ RISK | `flask.g` usage is 1 site, not 3 (already in plan but count was wrong) | `src/admin/utils/audit_decorator.py:18` | Wave 0 |
| Y1 | 🟡 YELLOW | Super-admin session cache preservation | `src/admin/utils/helpers.py:132-168` | Wave 1 |
| Y2 | 🟡 YELLOW | SSE rate limit per-process tied to single-worker | `src/admin/blueprints/activity_stream.py:22-24` | Wave 3 + FIXME |
| Y3 | 🟡 YELLOW | CORS applies globally incl. admin | `src/app.py:285-293` | Wave 1 |
| Y4 | 🟡 YELLOW | `/docs` and `/redoc` exposed in prod | `src/app.py:64-69` | Wave 3 |
| Y5 | 🟡 YELLOW | `jsonify(datetime)` format diff Flask vs FastAPI | `src/admin/blueprints/gam.py`, `inventory.py` | Wave 2 audit |
| Y6 | 🟡 YELLOW | `health/config` hardcodes `"service": "mcp"` | `src/routes/health.py:286-304` | Wave 3 cleanup |
| Y7 | 🟡 YELLOW | `.duplication-baseline` refresh after ~11.5k new LOC | `.duplication-baseline` | Wave 3 |
| Y8 | 🟡 YELLOW | `Apx-Incoming-Host` spoofing (no IP allowlist) — pre-existing | `src/admin/app.py:211-269` | File ticket, defer |
| O1 | ✨ OPPORTUNITY | Drop nginx entirely (~30MB image) | `config/nginx/*` + Dockerfile | v2.1 follow-on |
| O2 | ✨ OPPORTUNITY | Ratchet REST routes to `Annotated[T, Depends()]` | `src/routes/api_v1.py` | v2.1 follow-on |
| O3 | ✨ OPPORTUNITY | Consolidate 9 migration guards into one file | `tests/unit/test_architecture_*.py` | Wave 0 |
| O4 | ✨ OPPORTUNITY | Drop `a2wsgi`, `werkzeug`, `waitress`, `flask-caching`, `flask-socketio`, `python-socketio`, `simple-websocket` | `pyproject.toml` | Wave 3 |
| O5 | ✨ OPPORTUNITY | `log_auth_cookies` delete (24 lines debug spam) | `src/admin/app.py:272-295` | Wave 0 |
| O6 | ✨ OPPORTUNITY | Duplicate `sync_api` mount `/api/sync` + `/api/v1/sync` | `src/admin/app.py:372-377` | Wave 3 audit |
| O7 | ✨ OPPORTUNITY | `from flask import Flask` in `google_ad_manager.py` — dead `gam_config.html` | `src/adapters/google_ad_manager.py:25, 1694` | Wave 2 |
| O8 | ✨ OPPORTUNITY | `mock_ad_server.register_ui_routes` possibly dead | `src/adapters/mock_ad_server.py` | Wave 2 audit |
| O9 | ✨ OPPORTUNITY | Flask test client consolidation (58 call sites) | `tests/**/*.py` | Waves 3-4 |
| O10 | ✨ OPPORTUNITY | Template CDN asset bloat (Bootstrap 5 + FA 6) | `templates/base.html:15-17` | Wave 3 optional |
| O11 | ✨ OPPORTUNITY | 17 `noqa: E402` carve-outs in `src/app.py` | `src/app.py` | Wave 3 cleanup |
| O12 | ✨ OPPORTUNITY | `test_architecture_no_raw_select.py` allowlist auto-shrinks | `tests/unit/test_architecture_no_raw_select.py` | Wave 2 natural |
| O13 | ✨ OPPORTUNITY | Structural logging (structlog/logfire) swap-in | `src/**` | v2.1 follow-on |
| C1 | ✅ CLEAN | Tenant deactivation propagation works | `src/core/auth_utils.py:38` | — |
| C2 | ✅ CLEAN | Webhook payloads Pydantic-serialized, not Flask | `src/admin/blueprints/creatives.py:231` | — |
| C3 | ✅ CLEAN | Admin tests no `pytest.skip` violations | `tests/admin/` | — |
| C4 | ✅ CLEAN | Admin prod code no `session.query()` | `src/admin/blueprints/` | — |
| C5 | ✅ CLEAN | `tests/fixtures/` not in `tests/admin/` | `tests/admin/` | — |
| C6 | ✅ CLEAN | No `robots.txt` / `sitemap.xml` | — | — |

---

## Section 8 — Key Takeaways

The **biggest unmentioned risks** are **B4 (session scoping)** and **B2 (trailing slashes)** — both are blockers that could silently break production after a traffic cutover. Neither was in the first-order audit.

The **plan default to `async def` admin handlers was architecturally wrong** — the migration must flip to sync `def` as the default, matching today's Flask+a2wsgi semantics. Only OAuth callbacks, SSE handlers, and outbound webhook senders stay `async def`.

The **middleware order as proposed was buggy** — Approximated must run BEFORE CSRF, not after. This is a one-line fix in the plan but missing it would break external-domain onboarding.

The **largest derivative win** is **dropping nginx entirely** in v2.1 (~30 MB image, simpler Dockerfile, no proxy header copying). The migration unlocks this but the plan doesn't call it out as a follow-on.

The **largest cleanup wins per hour of effort** are:
- **O4 (drop 7 Flask deps)** — zero-risk, zero-test-impact, image savings
- **O5 (delete log_auth_cookies)** — 24 lines of debug spam
- **O3 (consolidate 9 guards into 1 file)** — prevents guard-file fragmentation

---

## Appendix A: Verification methodology

Two parallel Opus plan subagents on 2026-04-11:

1. **Agent 1 — Near-blocker deep dive + implicit Flask invariants** — read `src/admin/app.py:211-269`, traced middleware interactions through scenarios A-E, audited `src/admin/utils/helpers.py`, `audit_decorator.py`, `auth_helpers.py`, `auth_utils.py`, `app.py:54-102, 114-131, 186-191, 272-295, 298-330` for hidden invariants
2. **Agent 2 — Ecosystem interactions + cleanup** — grep'd 147 `script_root` occurrences, verified 111 `url_for` calls, checked `scoped_session` scopefunc behavior, enumerated shared infrastructure (schedulers, SSE counters, error handlers), classified 45+ cleanup opportunities

Both agents produced full reports preserved in the conversation context. This file is the synthesized, prioritized action plan.

Runtime verification via Bash:
```python
# Confirmed: include_router(prefix="/admin") does NOT set scope["root_path"]
# Result: root_path='' but request.url.path='/admin/foo'
```

Plus file reads of `src/routes/rest_compat_middleware.py`, Starlette 0.50.0 `SessionMiddleware` and `Mount` source from `.venv/lib/python3.12/site-packages/starlette/`.
