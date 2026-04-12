# Flask → FastAPI v2.0.0 Migration — Mission Briefing

> ⚠️ **BLOCKER 4 HAS PIVOTED (2026-04-11) — READ `async-pivot-checkpoint.md` FIRST**
>
> User directive: go fully async in v2.0 (Option A from deep audit §1.4), absorbing the previously-deferred async SQLAlchemy migration. The "sync def admin handlers" resolution is **superseded**. Every reference in the plan files to "sync def", "admin handlers default to def", "defer async to v2.1", or `run_in_threadpool` for DB work is now STALE.
>
> Fresh sessions: read `async-pivot-checkpoint.md` in this folder before touching any other plan content or implementing any part of Blocker #4.

**Mission:** Migrate `src/admin/` (Flask blueprints + Jinja templates + session auth) to FastAPI with **fully async SQLAlchemy end-to-end**, without breaking the AdCP MCP/REST surface, OAuth callbacks, or the ~147 template refs that depend on `request.script_root`.

**Branch:** `feat/v2.0.0-flask-to-fastapi` — expanded to 5-6 waves (Flask removal + admin rewrite + full async SQLAlchemy), one PR per wave, merged to main. Pre-Wave-0 lazy-loading spike required.

---

## Read me first

This file is the **entry point** for any Claude Code session or engineer touching this migration. The companion docs are large (1k–3k lines each); this file is the map, not the territory. If you read nothing else, read the **Critical Invariants** section below — those are the six things that are easiest to forget and most destructive to miss.

The **source of truth** for "am I ready to ship Wave N?" is `implementation-checklist.md`. Everything else is context.

---

## Critical Invariants (the 6 deep-audit blockers)

These were surfaced by the 2nd/3rd-order audit. Every one of them has shipped-breaking potential. Do not touch admin code without understanding all six.

1. **`script_root` template breakage — use `url_for` everywhere (greenfield).** Starlette's `include_router(prefix="/admin")` does NOT populate `scope["root_path"]` the way Flask's blueprint mounting populated `request.script_root`. ~147 template references would break silently. **Fix:** every admin route has `name="admin_<blueprint>_<endpoint>"` on its decorator; `StaticFiles(..., name="static")` is mounted on the outer app; every URL in every template uses `{{ url_for('admin_...', **params) }}` or `{{ url_for('static', path='/...') }}`. NO `admin_prefix`/`static_prefix`/`script_root`/`script_name` Jinja globals exist — these are strictly forbidden and guarded by `test_templates_no_hardcoded_admin_paths.py`. Missing route names raise `NoMatchFound` at render time; `test_templates_url_for_resolves.py` catches this at CI time. See `flask-to-fastapi-deep-audit.md` §1 (blocker 1).

2. **Trailing slashes.** Flask's `strict_slashes=False` accepts both `/foo` and `/foo/`; Starlette does not by default. ~111 `url_for` call sites at risk. **Fix:** every admin router constructed as `APIRouter(redirect_slashes=True, include_in_schema=False)`. See `flask-to-fastapi-deep-audit.md` §1 (blocker 2).

3. **`@app.exception_handler(AdCPError)` HTML regression.** Admin user clicks a button, the handler returns a JSON blob to the browser, user sees raw JSON. **Fix:** Accept-aware handler — render `templates/error.html` when `Accept: text/html` and path starts with `/admin/`; JSON otherwise. This is intentionally different from the JSON-only handler currently at `src/app.py:82-88`. See `flask-to-fastapi-deep-audit.md` §1 (blocker 3).

4. **⚠️ PIVOTED to Option A (full async SQLAlchemy in v2.0).** The original Blocker #4 resolution was sync `def` admin handlers (Option C) because the event-loop `scoped_session` bug would otherwise cause transaction interleaving. That resolution is **superseded**. User chose to absorb async SQLAlchemy into v2.0: `create_async_engine`, `async_sessionmaker`, `AsyncSession`, async repositories, async UoW, driver change `psycopg2-binary` → `asyncpg`. Admin handlers become `async def` end-to-end matching the rest of the codebase. The scoped_session bug is eliminated entirely (no more thread-identity scoping). **See `async-pivot-checkpoint.md` in this folder for the new plan. The `flask-to-fastapi-deep-audit.md` §1.4 Option C text is stale.**

5. **Middleware ordering: Approximated BEFORE CSRF.** Counterintuitive but correct. If CSRF fires first, an external-domain POST user fails CSRF validation (403) before the Approximated redirect can fire (should be 307). Also switch the redirect from 302 → 307 to preserve the POST body. See `flask-to-fastapi-deep-audit.md` §1 (blocker 5).

6. **OAuth redirect URI byte-immutability.** The paths `/admin/auth/google/callback`, `/admin/auth/oidc/{tenant_id}/callback`, and `/auth/gam/callback` are registered in Google Cloud Console and per-tenant OIDC provider configs. Any path change — including trailing slash, case, or prefix drift — yields `redirect_uri_mismatch` and login is dead. See `flask-to-fastapi-deep-audit.md` §1 (blocker 6).

---

## Recommended reading order (fresh reader, ~2 hours)

1. **This file** — you are here. Mission, blockers, map.
2. **`flask-to-fastapi-migration.md` §1–§2.8** — overall context, Phase 1 vs Phase 2 framing, AdCP boundary verification, deep-audit summary. Skim the rest.
3. **`flask-to-fastapi-deep-audit.md` §1–§2** — read the 6 blockers and the risk register in full detail. This is the single most important read after the overview.
4. **`implementation-checklist.md`** — know what the per-wave acceptance criteria actually are. This is the "am I ready?" source of truth.
5. **`flask-to-fastapi-adcp-safety.md`** — confirm the AdCP boundary is clear; note the 8 first-order action items.
6. **`flask-to-fastapi-foundation-modules.md`** — reference only. Read the module you are about to implement; do not read end-to-end.
7. **`flask-to-fastapi-worked-examples.md`** — reference only. Read the example that matches the blueprint you are translating.
8. **`flask-to-fastapi-execution-details.md`** — reference only. Read the wave you are currently shipping.

---

## File index

| File | When to read | Detail level |
|---|---|---|
| `CLAUDE.md` (this file) | First, always | Entry point / map, 182 lines |
| `flask-to-fastapi-migration.md` | Context pass + before any wave | Overview, 2302 lines |
| `flask-to-fastapi-deep-audit.md` | **Before writing any admin code** | 6 blockers + 20 risks, 885 lines |
| `flask-to-fastapi-adcp-safety.md` | Before touching MCP/REST surface | 1st-order audit, 480 lines |
| `flask-to-fastapi-foundation-modules.md` | When implementing a foundation module | Full code + tests, 3337 lines |
| `flask-to-fastapi-worked-examples.md` | When translating a specific blueprint | 5 worked examples, 2843 lines |
| `flask-to-fastapi-execution-details.md` | When starting / shipping a wave | Per-wave acceptance + rollback, 1150 lines |
| `async-pivot-checkpoint.md` | **Before touching Blocker #4** (canonical pivot doc) | Absorbed-async v2.0 target state, 507 lines |
| `implementation-checklist.md` | **Before opening a PR** (source of truth) | Consolidated ready-to-ship checklist, 891 lines |

---

## Derivative audit reports (2026-04-11)

After the async pivot, six parallel opus agents (agents A-F) produced deep-audit reports on different facets of the absorbed-async v2.0 scope. All reports live in `async-audit/` and are committed at `3e0afa02` / `d8957931` on `feat/v2.0.0-flask-to-fastapi`. A fresh session should consult these before making scope or idiom decisions.

| Report | Lines | When to read |
|---|---|---|
| `async-audit/agent-a-scope-audit.md` | 765 | **Before estimating Wave 4-5 LOC or spike scope.** File-by-file async conversion inventory, lazy-load audit (~50 sites all mechanically fixable), refined scope estimate (~16-18k total LOC, **under** checkpoint's 30-35k upper bound), and **9 open decisions** (listed below) that need user input before Wave 4 can start. |
| `async-audit/agent-b-risk-matrix.md` | 2,392 | Before attempting any risk mitigation. 33 risks (15 checkpoint + 18 new), severity table, per-risk 4-part deep dive (root cause / detection / mitigation / fallback), **9-pattern lazy-load cookbook**, 7-spike pre-Wave-0 gate, driver fallback to `psycopg[binary,pool]>=3.2.0`. |
| `async-audit/agent-c-plan-edits.md` | 2,074 | Source record only — its 45 edits were applied in commit `d8957931`. Consult for traceability. |
| `async-audit/agent-d-adcp-verification.md` | 1,433 | **Before touching any AdCP surface.** 21 surfaces PASS with zero current Risk #5 hits. 9 mitigations M1-M9, including **10 missing `await` sites** identified at exact line numbers: `src/routes/api_v1.py` lines 200, 214, 252, 284, 305, 324, 342, 360 + `src/core/tools/capabilities.py` lines 265, 310 + parallel `src/a2a_server/adcp_a2a_server.py` lines 1558, 1587, 1774, 1798, 1842, 1892, 1961, 2000. These must land in the same PR that converts the corresponding `_raw`/`_impl` to async. |
| `async-audit/agent-e-ideal-state-gaps.md` | 2,849 | Before writing foundation code. Current plan graded B+; 14 idiom upgrades (SessionDep DI pattern, DTO boundary, lifespan-scoped engine, structlog, no-UoW repository pattern). Minimum apply set: E1/E2/E3/E5/E6/E8. |
| `async-audit/agent-f-nonsurface-inventory.md` | 1,782 | Before Dockerfile, CI, pre-commit, or deployment script changes. 105 non-code action items across 27 categories. **Hard blocker:** 3 sync-psycopg2 deployment paths in `scripts/deploy/entrypoint_admin.sh:9`, `scripts/deploy/run_all_services.py::check_database_health/check_schema_issues`, and `src/core/database/db_config.py::DatabaseConnection`. Also PG version skew (CI=15, local=17), missing `[tool.pytest.ini_options]` section, `DATABASE_URL` sslmode→ssl rewriter. |

### Open decisions blocking Wave 4 (from Agent A §7)

The 9 questions Agent A identified. **Decisions 1, 7, and 9 were resolved via ultrathink deep-think analysis on 2026-04-11** (3 parallel Opus subagents, each producing 1st/2nd/3rd-order derivative analysis). Decisions 2, 3, 5, 8 were resolved earlier by Audit 06 (see meta-audit round). Decisions 4 and 6 are mechanical Wave 4 work, not blockers. **Ledger closed.**

1. **Adapter base class async conversion strategy** — **RESOLVED: Path B (sync adapters + `run_in_threadpool` wrap).** Full async requires porting `googleads==49.0.0` off `suds-py3` and rewriting 4 `requests`-based adapters (~1500 LOC) for zero AdCP-visible benefit. Path B keeps adapters sync; the **18 adapter call sites** in `src/core/tools/*.py` (and 1 in `src/admin/blueprints/operations.py`) wrap in `await run_in_threadpool(...)`. Requires `get_sync_db_session()` factory in `src/core/database/database_session.py` alongside async path (adapters touch DB 40 times). `AuditLogger.log_operation` splits into `_log_operation_sync` (internal, used by adapters) + async public wrapper. Threadpool tune: `anyio.to_thread.current_default_thread_limiter().total_tokens = 80` in lifespan startup (default 40 is too low for burst adapter load), env-override via `ADCP_THREADPOOL_SIZE`. Structural guard `tests/unit/test_architecture_adapter_calls_wrapped_in_threadpool.py` prevents drift. See Wave 4a-pilot acceptance criteria in `flask-to-fastapi-execution-details.md`. **2026-04-11 decision.**
2. **Delete `DatabaseConnection` + `get_db_connection()` in `src/core/database/db_config.py`?** — **RESOLVED (Audit 06 OVERRULE): KEEP.** `scripts/deploy/run_all_services.py:84,135` calls `get_db_connection()` as pre-uvicorn health checks. Agent A's grep was incomplete. Instead: move `psycopg2-binary` to deploy-time scope AND keep it for Decision 9's sync-bridge + Path B's adapter sync factory. `DatabaseConnection` remains as the raw-psycopg2 pre-uvicorn path; `get_sync_db_session()` (Decision 1 factory) and `background_sync_db.get_sync_db_session()` (Decision 9 bridge) are ORM-level paths. All three coexist; each serves a different client. **2026-04-11 decision.**
3. **Factory-boy async strategy** — **RESOLVED: custom `AsyncSQLAlchemyModelFactory` shim** with `sqlalchemy_session_persistence = None` so `_create()` only calls `session.add()`; commits belong to fixtures. Full recipe in `foundation-modules.md` §11.13.1 (D). **2026-04-11 decision.** (Audit 06)
4. **`src/core/database/queries.py`** — mechanical Wave 4 work, not a blocker. Three transforms per function: `def → async def`, `session.scalars(stmt).first() → (await session.execute(stmt)).scalars().first()`, add `await` to commits. ~+50 LOC. Callers grep-enumerable. **Not a blocker.**
5. **`src/core/database/database_schema.py` + `product_pricing.py`** — **RESOLVED (Audit 06 SUBSTITUTE).** `database_schema.py` is **orphan** (its own docstring says "reference only; use Alembic migrations"); delete in Wave 5 cleanup. `product_pricing.py` has a **latent lazy-load hotspot** at lines 16-71 — `inspect(product).unloaded` silently early-returns when `pricing_options` isn't eager-loaded. Wave 4 fix: raise `RuntimeError` instead of silent early-return, and add all callers to Spike 1's explicit eager-load audit list. **2026-04-11 decision.** (Audit 06)
6. **Flask-caching in pyproject.toml** — **RESOLVED (Decision 9 correction): NOT zero consumers.** Grep shows 3 active consumer sites: `src/admin/blueprints/inventory.py:874`, `src/admin/blueprints/inventory.py:1133`, `src/services/background_sync_service.py:472` (the last is also the Wave 3 `from flask import current_app` ImportError blocker). The migration.md §11.7 "zero callers" claim is **factually incorrect** and is corrected in the same propagation commit that adds Decision 9. Wave 3 replaces `flask-caching` with `src/admin/cache.py::SimpleAppCache` (~40 LOC `cachetools.TTLCache` wrapper on `app.state.inventory_cache`). Three consumer sites migrate to the new cache BEFORE `flask-caching` is removed from `pyproject.toml`. **2026-04-11 decision.**
7. **`src/core/context_manager.py`** — **RESOLVED: refactor to stateless async module functions taking `session: AsyncSession`.** The `ContextManager(DatabaseManager)` inheritance caches `self._session` on a process-wide singleton; under `async_sessionmaker` on the single event-loop thread, every concurrent task shares the same cached session → transaction interleaving. `async_sessionmaker` does NOT fix this because the singleton sits above the session factory. **Refactor:** delete the class, delete `_context_manager_instance` + `get_context_manager()`, convert 12 public methods to module-level `async def` functions taking `session` as first positional parameter, delete `DatabaseManager` entirely (only ContextManager subclassed it). 7 production callers (incl. dead `main.py:166` + module-load side effect in `mcp_context_wrapper.py:345`). ~400 LOC across ~15 files; ~50 test patches, 20 collapsible via single `tests/harness/media_buy_update.py` update. `mock_ad_server.py` has a `threading.Thread` background task that becomes `asyncio.create_task` + `async with session_scope()`. Validated by pre-Wave-0 **Spike 4.5** (0.5-1 day, soft blocker). Structural guard `tests/unit/test_architecture_no_singleton_session.py` prevents regressions (3 test methods: no session-typed class attrs, no `_X_instance` singleton getters, no module-level `*Manager()` instantiations). Zero interaction with Decisions 1 and 9 (grep-verified). **2026-04-11 decision.**
8. **SSE session lifetime** — **RESOLVED (Audit 06 SUBSTITUTE): already correct in current code.** `get_recent_activities()` at `activity_stream.py:167` opens/closes per poll tick (~5ms). Real Wave 4 work: `time.sleep(2)` → `await asyncio.sleep(2)`, `def generate()` → `async def generate()`, `Response(generate(), ...)` → `StreamingResponse(generate(), ...)`, drop wildcard CORS. ~20 LOC. SSE is partially dead code anyway — templates poll, not EventSource. **2026-04-11 decision.** (Audit 06)
9. **`src/services/background_sync_service.py`** — **RESOLVED: Option B sync-bridge.** Service runs multi-hour GAM inventory sync jobs via `threading.Thread` workers, incompatible with async SQLAlchemy (asyncpg `pool_recycle=3600` rotates mid-session, identity map grows unbounded over hours, Fly.io TCP keepalives expire). **Fix:** new `src/services/background_sync_db.py` module with a separate sync psycopg2 engine and `get_sync_db_session()` factory. Background threads use the sync-bridge; async request path is untouched. `psycopg2-binary` + `types-psycopg2` + `libpq-dev` + `libpq5` all retained (partial reversal of Agent F F1.1.1/F1.2.1). Also fixes the Wave 3 `from flask import current_app` ImportError at line 472 (replaced with `SimpleAppCache` helper, see Decision 6). Scope guarded by `tests/unit/test_architecture_sync_bridge_scope.py` — a ratcheting allowlist containing ONLY `background_sync_service.py`. Validated by pre-Wave-0 **Spike 5.5** (0.5 day, soft blocker — fallback is Option A: asyncio task + single async session per sync, suboptimal but viable). Sunset target v2.1+ (phase-per-session async refactor). Docker image savings adjust from ~80MB to ~75MB (libpq stays). Other long-running services (`background_approval_service`, `order_approval_service`) have bounded durations <`pool_recycle=3600` and do NOT need the sync-bridge — they convert to async normally. **2026-04-11 decision.**

### Mandatory pre-Wave-0 spike sequence

Per Agent B §4 and Agent A §6, the 5.5-7.5 day spike sequence gates Wave 4-5 entry. **Expanded from 7 to 9 spikes on 2026-04-11** to add Spikes 4.5 and 5.5 validating Decisions 7 and 9 deep-think resolutions:

1. **Spike 1 — Lazy-load audit** (HARD GATE): set `lazy="raise"` on all 58 relationships, run `tox -e integration`. Pass: <40 failures fixable in <2 days. **Fail = abandon absorbed-async, revert to sync-def Option C + defer async to v2.1.**
2. **Spike 2 — Driver compat**: run tests under `asyncpg`. Fail = switch to `psycopg[binary,pool]>=3.2.0`.
3. **Spike 3 — Performance baseline**: capture sync latency on 20 admin routes + 5 MCP tool calls as `baseline-sync.json` for Wave 4 comparison. **Under Path B (Decision 1), the baseline includes adapter `run_in_threadpool` wraps** — Wave 5 benchmark parity measurements must NOT compare sync baseline vs "bare async" but vs "async + threadpool-wrapped adapters" since that is the v2.0 production shape.
4. **Spike 4 — Test harness**: convert `tests/harness/_base.py` + 5 representative tests; verify xdist + factory-boy work.
5. **Spike 4.5 — ContextManager refactor smoke test** (soft blocker, 0.5-1 day, Decision 7): rewrite `src/core/context_manager.py` as stateless async module functions, delete `DatabaseManager`, convert smallest caller (`src/core/tools/creatives/_workflow.py::_create_sync_workflow_steps`) end-to-end, update `tests/harness/media_buy_update.py` EXTERNAL_PATCHES, delete 18 lines of singleton-reset hacks in `conftest_db.py`/`integration_db.py`/`test_gam_lifecycle.py`. Pass: refactor size <400 LOC AND <15 files AND <50 test patches AND error-path composition test proves outer `session_scope()` rollback does NOT wipe error-logging writes (fail case: use separate `async with session_scope()` for error logs). Fail action (SOFT): refactor gets a dedicated Wave 4a sub-phase PR instead of being bundled into the pilot — not a gate failure on the pivot.
6. **Spike 5 — Scheduler alive-tick**: convert 2 scheduler tick bodies; observe container logs.
7. **Spike 5.5 — Two-engine coexistence** (soft blocker, 0.5 day, Decision 9): prove async asyncpg engine + sync psycopg2 engine coexist in one Python process. 4 test cases: (a) engine lazy-init + dispose cycle clean, (b) MVCC visibility bidirectional (async write → sync read, sync write → async read), (c) 5 concurrent async requests + 1 sync thread no deadlock, (d) post-dispose leaked connections ≤1 from baseline. Pass: all 4 green. Fail action (SOFT): revert to Option A (asyncio task + single async session per sync), suboptimal but viable — document in `spike-decision.md`.
8. **Spike 6 — Alembic async**: rewrite `alembic/env.py`; run upgrade/downgrade roundtrip. Fallback: keep env.py sync.
9. **Spike 7 — `server_default` audit**: grep + categorize columns; confirm <30 to rewrite.

---

## Apps loaded at runtime (4 before → 3 after)

The migration removes **one** of the four framework-level apps currently loaded by `src/app.py`. The MCP and A2A apps are AdCP-protocol surfaces and stay untouched.

| # | App | Where | Attached at | Disposition |
|---|---|---|---|---|
| 1 | **Root FastAPI `app`** | `src/app.py:64` | (is the root ASGI object) | **STAYS** — gains middleware + admin routers, loses the Flask mount |
| 2 | **`mcp_app` (Starlette from `mcp.http_app(path="/")`)** | `src/app.py:59` + `src/core/main.py:127` | `app.mount("/mcp", mcp_app)` at `src/app.py:72`; lifespan merged via `combine_lifespans` at `src/app.py:68` | **STAYS** — AdCP MCP protocol surface |
| 3 | **`a2a_app` (A2AStarletteApplication)** | `src/app.py:110` | **NOT mounted** — routes grafted onto root via `a2a_app.add_routes_to_app(app, ...)` at `src/app.py:118-123` | **STAYS** — AdCP A2A protocol surface |
| 4 | **`flask_admin_app` (Flask)** | `src/admin/app.py:107` via `create_app()` at `src/app.py:303` | `a2wsgi.WSGIMiddleware` wrapper, mounted at **both** `/admin` and `/` (root catch-all) via `_install_admin_mounts()` | **REMOVED Wave 3** — the whole point of the migration |

Plus orphan: `src/admin/server.py` (~103 LOC, standalone Flask runner via Waitress/Werkzeug/`asgiref.wsgi.WsgiToAsgi`) and `scripts/run_admin_ui.py` (38-line launcher) — not loaded by `src/app.py`, **removed in Wave 3 cleanup**.

**Subtleties a fresh reader MUST understand:**

- **A2A is grafted, not mounted.** `add_routes_to_app` at line 118 injects the SDK's Starlette `Route` objects directly into `app.router.routes`. So A2A handlers sit at the top level of the router tree, NOT inside a mounted sub-app. This is load-bearing for FastAPI middleware propagation (`UnifiedAuthMiddleware`, `CORSMiddleware`, `RestCompatMiddleware` all reach A2A handlers because they share the root scope). `_replace_routes()` at `src/app.py:192-215` also depends on this flat structure — it walks `app.routes` to swap the SDK's static agent-card routes for dynamic header-reading versions. **Any future refactor that mounts A2A as a sub-app would break middleware propagation AND `_replace_routes()`.**

- **MCP schedulers are lifespan-coupled.** `src/core/main.py:82-103` starts `delivery_webhook_scheduler` and `media_buy_status_scheduler` inside `lifespan_context`. That lifespan reaches uvicorn's event loop **only because of `combine_lifespans(app_lifespan, mcp_app.lifespan)` at `src/app.py:68`**. A future refactor that drops the MCP mount, rewires lifespans, or moves schedulers outside the MCP lifespan context will **silently stop the schedulers**. Not touched by v2.0 but document as a hard constraint and consider adding a startup-log assertion.

- **The `/a2a/` trailing-slash redirect shim at `src/app.py:127-135` exists ONLY because the Flask root catch-all (`app.mount("/", admin_wsgi)`) would otherwise eat the request.** When Flask is removed in Wave 3, this shim gets deleted — the causal chain is "no more Flask catch-all → no more route collision → no more shim needed."

- **`_install_admin_mounts()` is a lifespan hook at `src/app.py:25-45`** that re-filters and re-installs the `/admin` and `/` Flask mounts at the **tail** of `app.router.routes` on every startup. This ordering is load-bearing: landing routes (inserted at positions 0 and 1 via the `routes.insert(0, ...)` hack at lines 351-352) must win, A2A grafted routes must win, FastAPI-native REST routes must win, and the Flask catch-all must be last. The whole dance goes away in Wave 3 once Flask is gone.

- **Flask has its own internal WSGI middleware stack** at `src/admin/app.py:187-194` (`CustomProxyFix`, `FlyHeadersMiddleware`, werkzeug `ProxyFix`). These rewrite `Fly-Forwarded-Proto` → `X-Forwarded-Proto` and handle `X-Script-Name` for reverse-proxy deployments. **Wave 3 deletes Flask but the proxy-header handling must be reimplemented** via `uvicorn --proxy-headers --forwarded-allow-ips='*'` (already in the plan per deep-audit §R4). If this is missed, `request.url.scheme` returns `http` in production and OAuth redirect URIs fail with `redirect_uri_mismatch` on Fly.io.

---

## Migration conventions that differ from the rest of the codebase

These are the places where "copy what the rest of the repo does" is **wrong**. Admin is different.

- **⚠️ PIVOTED: Admin handlers are `async def` end-to-end with full async SQLAlchemy.** Consistent with the rest of the codebase. The scoped_session bug is eliminated by `AsyncSession` + `async_sessionmaker` (there is no more `threading.get_ident()` scoping to race on). `run_in_threadpool` is still used for genuinely blocking work (file I/O, CPU-bound calls, sync third-party libraries) but NOT for DB work — all DB access goes through `async with` UoW. See `async-pivot-checkpoint.md` for full detail.
- **Middleware order: Approximated BEFORE CSRF.** Counterintuitive relative to standard stacks where CSRF sits near the outside. Here, Approximated's external-domain redirect must fire before CSRF sees the form body. See blocker 5.
- **Templates use `{{ url_for('name', **params) }}` exclusively** — for admin routes AND static assets. No prefix variables, no Jinja globals holding URL strings, no `script_root`, no `admin_prefix`, no `static_prefix`. Every admin route has `name="admin_<blueprint>_<endpoint>"`; the static mount is `name="static"`. This is the FastAPI canonical pattern from the official docs, verified in `Jinja2Templates._setup_env_defaults` at `starlette/templating.py:118-129` (auto-registers `url_for` as a Jinja global that calls `request.url_for(...)` via `@pass_context`). `NoMatchFound` at render time on a missing name is caught pre-merge by `test_templates_url_for_resolves.py`.
- **`AdCPError` handler branches on `Accept`.** For admin HTML browser users, render `templates/error.html`. For JSON API callers, return JSON. Different from the plain JSON-only handler at `src/app.py:82-88` — do not copy that one.
- **⚠️ PIVOTED: Async admin handlers wrap DB access in `async with get_db_session() as session:` or `async with UoW() as uow:`**, NOT in `run_in_threadpool`. Under full async SQLAlchemy, `get_db_session()` is an async context manager yielding an `AsyncSession`; repositories return via `await session.execute(...)`. `run_in_threadpool` remains valid for non-DB blocking operations only.
- **`FLASK_SECRET_KEY` is dual-read alongside `SESSION_SECRET`** during v2.0 for dev ergonomics. It is hard-removed in v2.1. Do not rip it out in v2.0 — you will break every dev's local `.env`.

---

## First-order audit action items (quick reference)

Catalogued in `flask-to-fastapi-adcp-safety.md`; listed here so they are not lost:

- `tenant_management_api.py` route count in the main plan is **stale (19 → 6)** — re-verify before scoping.
- `gam_reporting_api.py` is **session-authed → Category 1**, not Category 2.
- `schemas.py` serves external AdCP JSON-Schema validators — preserve URLs **byte-for-byte**.
- `creatives.py` / `operations.py` construct outbound AdCP webhooks — **do not** use AdCP types as `response_model=`.
- Every admin router: `include_in_schema=False`.
- `/_internal/` must be added to the CSRF exempt list.
- Three new structural guards to add: `csrf_exempt_covers_adcp`, `approximated_path_gated`, `admin_excluded_from_openapi`.

---

## Branch and folder cleanup intent

- **Branch:** `feat/v2.0.0-flask-to-fastapi`. All migration work lives here.
- **Merge cadence:** one PR per wave, 5-6 waves total (Wave 0-3 Flask removal + admin FastAPI rewrite, Wave 4-5 async SQLAlchemy absorption per `async-pivot-checkpoint.md`), merged to `main` as each wave stabilizes.
- **Post-migration cleanup:** `.claude/notes/flask-to-fastapi/` is a planning-phase artifact. After v2.0.0 ships and stabilizes (~2 releases later), archive or delete this folder. Anything worth keeping long-term gets promoted to `docs/` or `CLAUDE.md` at the repo root.

---

## v2.1 deferred items (do NOT pull forward)

These are intentionally out of scope for v2.0.0. If you find yourself wanting to do them during the migration, stop and file an issue instead.

- ~~Async SQLAlchemy~~ **MOVED TO v2.0** per async-pivot-checkpoint.md — absorbed into v2.0 scope as Waves 4-5
- Drop nginx (cannot happen until admin is fully on FastAPI and external-domain handling is battle-tested)
- REST routes ratchet to `Annotated[...]` form
- `Apx-Incoming-Host` IP allowlist (currently trusted on header alone)
- `require_tenant_access` to check `is_active`
- `/_internal/` auth hardening (currently network-gated only)
- Hard-remove `FLASK_SECRET_KEY` dual-read
