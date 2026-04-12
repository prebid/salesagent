# Flask → FastAPI v2.0.0 Migration — Implementation Checklist

**Status:** SOURCE OF TRUTH for "am I ready to ship Wave N?"
**Target release:** salesagent v2.0.0
**Feature branch:** `feat/v2.0.0-flask-to-fastapi`
**Last updated:** 2026-04-11 (pivoted to full async; propagation wave applied)

> **BLOCKER 4 PIVOTED TO FULL ASYNC (2026-04-11)**
>
> User directive: go fully async in v2.0 (deep-audit Option A), absorbing the
> previously-deferred async SQLAlchemy migration. The "sync def admin handlers"
> resolution is superseded; §2 Blocker 4 describes the pivoted target state.
>
> **Canonical references for the pivoted plan:**
> - [`async-pivot-checkpoint.md`](async-pivot-checkpoint.md) — full new plan
> - [`async-audit/`](async-audit/) — six derivative audit reports on the
>   absorbed-async v2.0 scope (Agent A-F)
> - `CLAUDE.md` §"Critical Invariants" — 6 invariants and 9 open decisions
>
> §1.2 ("Architectural decisions"), §2 ("Blocker 4"), and §4 ("Wave 0 deliverables")
> in this file have been rewritten to reflect the pivot. §4 Wave 4 and Wave 5
> sections enumerate the async conversion scope.

## How to use this file

This checklist consolidates every action item from the six companion migration documents into a single gate-by-gate sequence. Each checkbox is either a prerequisite, a blocker fix, an action item, a wave acceptance criterion, a rollback trigger, a post-migration verification step, or a deferred tech-debt tracking item. If you only read ONE file before shipping a wave, read this one.

Every item references the companion doc where full detail lives. Tick every box for a given section before declaring that section "done." The deep audit's six BLOCKERS (Section 2) MUST be fixed in Wave 0 — shipping Wave 1 without them will cause silent production breakage.

**Companion docs (all under `.claude/notes/flask-to-fastapi/`):**
- `flask-to-fastapi-migration.md` — main overview (§14 waves, §2.8 blockers)
- `flask-to-fastapi-execution-details.md` — per-wave execution with rollback
- `flask-to-fastapi-foundation-modules.md` — 11 foundation module implementations
- `flask-to-fastapi-worked-examples.md` — 5 real Flask→FastAPI translations
- `flask-to-fastapi-adcp-safety.md` — first-order AdCP boundary audit
- `flask-to-fastapi-deep-audit.md` — 2nd/3rd-order audit (6 blockers + 20 risks)

---

## Section 1 — Pre-migration prerequisites (run once, before Wave 0)

### 1.1 Environment and infrastructure prerequisites

- [ ] `SESSION_SECRET` env var is set in staging secret store
- [ ] `SESSION_SECRET` env var is set in production secret store
- [ ] `SESSION_SECRET` env var is set in test/CI secret store
- [ ] `DATABASE_URL` env var format compatible with asyncpg driver rewrite (staging + prod + test): `postgresql://...` gets rewritten to `postgresql+asyncpg://...` at engine construction — verified — pivoted 2026-04-11
- [ ] `SESSION_SECRET` documented in `.env.example`
- [ ] `SESSION_SECRET` documented in `docs/deployment/environment-variables.md`
- [ ] OAuth redirect URIs currently registered in Google Cloud Console enumerated and documented in a migration runbook — at minimum:
  - [ ] `https://<tenant>.scope3.com/admin/auth/google/callback`
  - [ ] `https://<tenant>.scope3.com/admin/auth/oidc/{tenant_id}/callback`
  - [ ] `https://<tenant>.scope3.com/auth/gam/callback` (NOT under `/admin`)
- [ ] External consumer contracts confirmed for Category-2 files:
  - [ ] `src/admin/tenant_management_api.py` (6 routes, `X-Tenant-Management-API-Key`)
  - [ ] `src/admin/sync_api.py` (9 routes, `X-API-Key`, duplicate mount at `/api/sync`)
  - [ ] `src/admin/blueprints/schemas.py` (`/schemas/adcp/v2.4/*`, external JSON Schema validators)
- [ ] Feature branch `feat/v2.0.0-flask-to-fastapi` created from green main
- [ ] Team announcement sent: `src/admin/` freeze will take effect during Wave 2 for ≤ 7 calendar days
- [ ] Rollback window documented for each wave (see Section 5)
- [ ] Staging environment matches production topology:
  - [ ] Fly.io proxy header behavior verified (`X-Forwarded-*` present)
  - [ ] nginx config unchanged during v2.0 (nginx removal deferred to v2.1)
  - [ ] `ADCP_TESTING=true` gating for `/_internal/reset-db-pool` confirmed
- [ ] Pre-Wave-0 `main` branch passes `make quality` + `tox -e integration` + `tox -e bdd`
- [ ] Pre-Wave-0 `main` has `a2wsgi` Flask mount still at `src/app.py:299-304` (safety net)
- [ ] v1.99.0 git tag plan documented (last-known-good Flask-era release, tagged before Wave 3 merges)
- [ ] **Pre-Wave-0 lazy-loading audit spike completed and approved (async pivot 2026-04-11)** — enumerates every `relationship()` definition in `src/core/database/models/` and classifies every access site as safe (in-scope), fixable (eager-load), or requires-rewrite. This audit gates the Wave 4-5 async absorption scope. If the audit reveals the scope is untenable, fall back to Option C (sync def admin) and defer async to v2.1. See `async-pivot-checkpoint.md` §4 Risk #1 for the full audit procedure.
- [ ] **Pre-Wave-0 async driver compatibility spike completed (async pivot 2026-04-11)** — run the full test suite on a staging branch with `asyncpg` instead of `psycopg2-binary` to catch driver-compat surprises (JSONB codec, UUID/Interval types, LISTEN/NOTIFY API drift, COPY bulk imports). Estimated 1-2 days of debugging. See checkpoint §4 Risk #2.
- [ ] **Pre-Wave-0 Spike 4.5 — ContextManager refactor smoke test (Decision 7, 2026-04-11)** — rewrite `src/core/context_manager.py` to stateless async module functions, delete `DatabaseManager` from `database_session.py`, convert the smallest caller (`src/core/tools/creatives/_workflow.py::_create_sync_workflow_steps`) end-to-end, update `tests/harness/media_buy_update.py::EXTERNAL_PATCHES`, delete 18 lines of singleton-reset hacks in `conftest_db.py` + `integration_db.py` + `test_gam_lifecycle.py`. Pass: LOC <400 AND files <15 AND test patches <50 AND error-path composition test passes. Soft blocker (fallback: dedicated Wave 4a sub-phase). See `async-pivot-checkpoint.md` §4 Risk #20 + `CLAUDE.md` Decision 7.
- [ ] **Pre-Wave-0 Spike 5.5 — Two-engine coexistence (Decision 9, 2026-04-11)** — create MVP `src/services/background_sync_db.py` (~200 LOC separate sync psycopg2 engine) and run 4 test cases at `tests/driver_compat/test_sync_bridge_coexistence.py`: (a) lazy-init + dispose, (b) MVCC bidirectional visibility, (c) 5 concurrent async requests + 1 sync thread no deadlock, (d) post-dispose connection leaks <=1. Also validates the Wave 3 flask-caching correction (3 consumer sites at inventory.py:874, :1133, background_sync_service.py:472). Soft blocker (fallback: Option A asyncio task). See `async-pivot-checkpoint.md` §4 Risk #7.5 + `CLAUDE.md` Decision 9.
- [ ] **Agent F pre-Wave-0 hard gate items completed (non-code surface inventory, corrected 2026-04-11):**
  - [ ] **`psycopg2-binary` RETAINED** — partial reversal of Agent F F1.1.1 per Decisions 1 (Path B sync factory), 2 (pre-uvicorn health checks), 9 (sync-bridge). `asyncpg>=0.30.0,<0.32` added alongside (not replacing). `types-psycopg2` also retained. **Fallback:** `psycopg[binary,pool]>=3.2.0` if Spike 2 (driver compat) fails — see `CLAUDE.md` pre-Wave-0 spike sequence.
  - [ ] `[tool.pytest.ini_options]` added to `pyproject.toml` with `asyncio_mode = "auto"` (F1.7.1, F8.2.1)
  - [ ] `DATABASE_URL` rewriter (`sslmode` → `ssl`) landed (F1.5.1)
  - [ ] **`DatabaseConnection` KEPT** (partial reversal of F1.4.1) per Audit 06 Decision 2 OVERRULE, REFINED 2026-04-11. **Real rationale: fork safety, NOT loop collision.** `run_all_services.py` is PID 1 sync orchestrator that forks uvicorn into a child subprocess via `subprocess.Popen` at `:231`, so parent/child have independent Python interpreters. Using SQLAlchemy `get_sync_db_session()` here would duplicate pooled asyncpg/psycopg connections into the child fork → PG socket corruption (canonical SQLAlchemy fork-safety bug). Raw `psycopg2.connect()` + close-before-fork is the only safe shape.
  - [ ] **Decision 2 corrected caller list (2026-04-11):** `scripts/deploy/run_all_services.py:84,135` + `examples/upstream_quickstart.py:137`. **NOT** `init_database.py`/`init_database_ci.py` — those use SQLAlchemy `get_db_session()`, were misattributed in original Audit 06 ledger and Agent F §1.4.
  - [ ] **Delete `scripts/deploy/entrypoint_admin.sh`** in same PR — dead shell code, unreferenced by Dockerfile/compose/fly.toml, still shell-imports psycopg2 in a subshell, calls non-existent `migrate.py`, imports `src.admin.server` (scheduled for Wave 3 deletion).
  - [ ] **Migrate `examples/upstream_quickstart.py:137`** to `get_db_session()` (async-capable, standalone-safe). Leaves `DatabaseConnection` with exactly 2 callers in `run_all_services.py`.
  - [ ] **Harden `DatabaseConnection.connect()`**: add `connect_timeout=10` (env-overridable via `DATABASE_CONNECT_TIMEOUT`) and `options="-c statement_timeout=5000"` to the `psycopg2.connect(...)` call. Prevents hanging DB from bricking container startup.
  - [ ] **Structural guard #1** `tests/unit/test_architecture_no_runtime_psycopg2.py` — AST-walks every `src/**/*.py`, allowlists exactly 3 files for `import psycopg2`/`from psycopg2`: `src/core/database/db_config.py` (Decision 2 raw pre-fork path), `src/core/database/database_session.py` (Decision 1 Path B sync factory IF it explicitly imports — verify during implementation; SQLAlchemy auto-detects psycopg2 driver from URL string without an explicit import), `src/services/background_sync_db.py` (Decision 9 sync-bridge). Includes a "stale-entry" test that fails if any allowlisted file no longer imports psycopg2.
  - [ ] **Structural guard #2** `tests/unit/test_architecture_get_db_connection_callers_allowlist.py` — AST-walks every `src/**/*.py` and `scripts/**/*.py` for `Call` nodes invoking `get_db_connection`, allowlists exactly 1 file: `scripts/deploy/run_all_services.py`. Catches the failure mode where someone adds `DatabaseConnection` inside the runtime process (fork-unsafe AND pool-uncoordinated). Excludes `db_config.py` (definition file) and `examples/`/`tests/` by directory scope.
  - [ ] **Risk #34 (NEW, HIGH)**: `run_all_services.py:175` imports `src.core.database.database.init_db` which under async pivot opens the SQLAlchemy async engine in the parent process. Then `:231` `Popen`s uvicorn — duplicate pooled connection FDs leak into the child. Either (a) `init_db()` calls `await reset_engine()` in `finally`, OR (b) `run_all_services.py` runs init via `subprocess.run([sys.executable, "-m", "scripts.setup.init_database"])` like migrations already do at `:207`. **Strongly prefer (b)** — matches the existing migration pattern, no in-process state leak risk. Add to async-pivot-checkpoint.md §4 risk register.
  - [ ] **Spike 5.5 additional check**: verify `run_all_services.py`'s init flow does NOT eagerly hold any PG sockets in the parent process after `init_database()` returns and before `threading.Thread(target=run_mcp_server).start()`. Either grep `/proc/<pid>/fd/` for PG sockets (Linux) or run `pg_stat_activity` query and confirm zero connections from PID 1 outside of the transient `DatabaseConnection` window.
  - [ ] Alembic `env.py` async rewrite validated via Spike 6 (F2.5.1)
  - [ ] CI Postgres version aligned to 17 across all workflows (F2.4.1)
  - [ ] Dead `test-migrations` pre-commit hook removed (F2.3.1)
  - [ ] 3 new structural guards added (F6.2.1, F6.2.5, F6.2.6)
  - [ ] New docs `async-debugging.md` + `async-cookbook.md` drafted (F5.3.1, F5.3.2)
  - [ ] Full `asyncpg` wheel availability verified for glibc + macOS (F1.1.3)
  - [ ] Duplication baseline snapshotted at Wave 4 start (F7.4.1)

### 1.2 Architectural decisions recorded in the migration plan

- [ ] **Admin handlers `async def` end-to-end with full async SQLAlchemy** — pivoted 2026-04-11 from Option C (sync def) to Option A (full async absorbed into v2.0 Waves 4-5) — per deep audit blocker #4 (rewritten); documented in `flask-to-fastapi-migration.md` §2.8, `async-pivot-checkpoint.md`, and full new scope in §18 of the migration doc
- [ ] **Middleware order: Approximated BEFORE CSRF** (corrected) — per deep audit blocker #5; documented in `flask-to-fastapi-migration.md` §2.8 and §10.2
- [ ] **Redirect status code: 307** (not 302) — preserves POST body per RFC 7231
- [ ] **`FLASK_SECRET_KEY` transition: dual-read during v2.0, hard-remove in v2.1** (supersedes original user directive #5) — documented in `flask-to-fastapi-deep-audit.md` §3.4
- [ ] **Error-shape split decided:** Category 1 native `{"detail": ...}`, Category 2 legacy `{"success": false, "error": ...}` via scoped handler — documented in `flask-to-fastapi-migration.md` §2 directive #8
- [ ] **Decision 1 (adapter Path B, 2026-04-11):** adapter methods stay sync `def`; 18 call sites in `src/core/tools/*.py` + 1 in `src/admin/blueprints/operations.py:252` wrap in `await run_in_threadpool(...)`. `src/core/database/database_session.py` exports `get_sync_db_session()` alongside async `get_db_session()` (dual session factory). `AuditLogger.log_operation` splits into `_log_operation_sync` (internal) + async public wrapper. `anyio.to_thread.current_default_thread_limiter().total_tokens = 80` at lifespan startup. Structural guard `test_architecture_adapter_calls_wrapped_in_threadpool.py`. Full implementation reference: `flask-to-fastapi-foundation-modules.md` §11.14. Full target state: `async-pivot-checkpoint.md` §3 "Adapters (Decision 1 Path B)".
- [ ] **Decision 7 (ContextManager refactor, 2026-04-11):** delete `ContextManager` class + `DatabaseManager` entirely. 12 public methods become stateless `async def` module functions taking `session: AsyncSession` as first parameter. 7 production callers migrate (incl. dead `main.py:166` + module-load side effect in `mcp_context_wrapper.py:345` + `mock_ad_server.py threading.Thread → asyncio.create_task`). ~50 test patches, 20 collapsible via `tests/harness/media_buy_update.py::EXTERNAL_PATCHES` update. Validated by Spike 4.5. Structural guard `test_architecture_no_singleton_session.py`. Error-path composition gotcha: use SEPARATE `async with session_scope()` for error-logging writes (outer scope rolls back on raise). Full target state: `async-pivot-checkpoint.md` §3 "ContextManager refactor".
- [ ] **Decision 9 (background_sync sync-bridge, 2026-04-11):** new `src/services/background_sync_db.py` module with separate sync psycopg2 engine (pool 2+3, statement_timeout=600s, `application_name='adcp-salesagent-sync-bridge'`). Background threads stay sync. `psycopg2-binary` + `libpq5` + `libpq-dev` all retained in `pyproject.toml` + `Dockerfile` (partial reversal of Agent F F1.1.1/F1.2.1). Wave 3 flask-caching correction bundled: 3 consumer sites (inventory.py:874, :1133, background_sync_service.py:472), SimpleAppCache replacement required before deletion, closes `from flask import current_app` ImportError at line 472. Validated by Spike 5.5. Structural guard `test_architecture_sync_bridge_scope.py` with ratcheting allowlist. Sunset v2.1+. Full target state: `async-pivot-checkpoint.md` §3 "Background sync sync-bridge".
- [ ] **Decision 3 (factory-boy async shim, refined 2026-04-11):** custom `AsyncSQLAlchemyModelFactory` overrides `_save` (not `_create`), `sqlalchemy_session_persistence = None`, `session.add(instance)` directly (no `sync_session.add`), NO `flush()` call. 3 bugs fixed from Audit 06 recipe. Wave 4b-4c hard cliff: all 166 integration tests must flip async BEFORE factory base classes flip. New Spike 4.25 (0.5 day, soft blocker). 3 structural guards: `test_architecture_factory_inherits_async_base.py`, `test_architecture_factory_no_post_generation.py`, `test_architecture_factory_in_all_factories.py`. Full recipe: `foundation-modules.md` §11.13.1 (D).
- [ ] **Decision 4 (queries.py convert-and-prune, refined 2026-04-11):** 6 functions (not 7), zero production callers, 3 dead functions → delete + allowlist cleanup. 3 live functions → async conversion. Test file `test_creative_review_model.py` converts to async. Net: ~−100 LOC. Move to `CreativeRepository` deferred to v2.1 (Option 4B).
- [ ] **Decision 5 (database_schema.py + product_pricing.py DELETE, refined 2026-04-11):** `database_schema.py` confirmed orphan → delete Wave 5. `product_pricing.py` has 1 caller already eager-loading, inspect-guard defeated by unconditional log at line 43 → DELETE entirely in Wave 4, inline conversion at single caller as `AdminPricingOptionView` Pydantic DTO. Supersedes Audit 06 SUBSTITUTE (RuntimeError prescription was technically ineffective).
- [ ] **Decision 6 (flask-caching → SimpleAppCache, refined 2026-04-11):** ~90 LOC module with `cachetools.TTLCache(maxsize=1024, ttl=300)` + `threading.RLock` + `_NullAppCache` fallback + `CacheBackend` Protocol. Both inventory sites rewritten to cache dicts not Flask Response objects. `cache_key`+`cache_time_key` folded into single 2-tuple entry. 12-step strict migration order in Wave 3 PR. 2 structural guards. Full recipe: `foundation-modules.md` §11.15.
- [ ] **Decision 8 (SSE DELETE, 2026-04-11):** `/tenant/{id}/events` SSE route is orphan code — template says "use polling", zero EventSource consumers. DELETE route + generator + rate-limit state + HEAD probe in Wave 4. Fix `api_mode=False → True` on surviving `/activity` JSON poll route. −170 LOC, −1 pip dep (`sse_starlette`). Structural guard `test_architecture_no_sse_handlers.py`.
- [ ] **Admin router OpenAPI: `include_in_schema=False`** — documented in `flask-to-fastapi-adcp-safety.md` §3
- [ ] **`gam_reporting_api.py` reclassified Category 2 → Category 1** (session-cookie authed = admin-UI-only) — documented
- [ ] **`tenant_management_api.py` route count fixed 19 → 6** in plan docs
- [ ] **Session cookie name: `session` → `adcp_session`** — one forced re-login at cutover is acceptable (user decision #7)
- [ ] **Scheduler stays single-worker in v2.0** — documented as a hard constraint; multi-worker deferred to v2.2 (requires scheduler lease design)

---

## Section 2 — Six deep-audit blockers (must all be fixed in Wave 0 or Wave 1)

Full detail in `flask-to-fastapi-deep-audit.md` §1.

- [ ] **Blocker 1: `script_root` / `script_name` template breakage — GREENFIELD: full `url_for` adoption**
  - [ ] `src/admin/templating.py::render()` wrapper has NO `admin_prefix`/`static_prefix`/`script_root`/`script_name` in its context dict
  - [ ] `src/admin/templating.py` pre-registers `_url_for` safe-lookup override on `templates.env.globals` BEFORE any `TemplateResponse` call (catches `NoMatchFound`, logs template filename + route name + params, re-raises)
  - [ ] `app.mount("/static", StaticFiles(directory="src/admin/static"), name="static")` on the outer FastAPI app — `name="static"` is load-bearing for `url_for('static', path=...)` resolution via `Mount.url_path_for` at `starlette/routing.py:434-459`
  - [ ] Every admin route has `name="admin_<blueprint>_<endpoint>"` on its decorator (e.g., `@router.get("/tenant/{tenant_id}/accounts", name="admin_accounts_list_accounts")`)
  - [ ] `scripts/codemod_templates_greenfield.py` exists and implements a two-pass regex rewrite:
    - [ ] Pass 1a: `{{ script_name }}/static/foo.css` → `{{ url_for('static', path='/foo.css') }}`
    - [ ] Pass 1b: `{{ script_name }}/tenant/{{ tenant_id }}/settings` → `{{ url_for('admin_tenants_settings', tenant_id=tenant_id) }}` via `HARDCODED_PATH_TO_ROUTE` map
    - [ ] Pass 2: `{{ url_for('bp.endpoint', ...) }}` Flask-dotted → `{{ url_for('admin_bp_endpoint', ...) }}` via `FLASK_TO_FASTAPI_NAME` map
  - [ ] `scripts/generate_route_name_map.py` exists and produces `FLASK_TO_FASTAPI_NAME` and `HARDCODED_PATH_TO_ROUTE` from `src/admin/app.py::create_app().url_map.iter_rules()` introspection
  - [ ] Codemod runs successfully against all 72 templates; stdout reports `"N templates processed, M rewrites applied"`
  - [ ] Codemod is idempotent — re-running on post-codemod templates yields zero diff (`tests/unit/admin/test_codemod_idempotent.py` green)
  - [ ] Manual audit of `add_product_gam.html` for JS-literal edge cases (15 `url_for` calls in JS template literals) — verify the `JS_TEMPLATE_LITERAL_RE` pre-pass flags them for manual review
  - [ ] Manual audit of `base.html` (7 `{{ script_name }}` references — highest-fanout template)
  - [ ] Manual audit of `tenant_dashboard.html` (21 `script_name` references — highest-complexity template)
  - [ ] `tests/unit/admin/test_templates_no_hardcoded_admin_paths.py` green — asserts zero matches for `script_name|script_root|admin_prefix|static_prefix` AND zero bare `"/admin/"` / `"/static/"` string literals
  - [ ] `tests/unit/admin/test_templates_url_for_resolves.py` green — AST-extracts every `url_for('name', ...)` and asserts `name` is in `{r.name for r in app.routes}` (catches `NoMatchFound` footgun at CI time)
  - [ ] `tests/unit/admin/test_architecture_admin_routes_named.py` green — AST-scans `src/admin/routers/*.py` and asserts every `@router.<method>(...)` has `name=` kwarg
  - [ ] For JS URL construction with runtime path params: handlers pre-resolve base URLs via `js_*_base` context vars (e.g., `js_workflows_base=str(request.url_for("admin_workflows_list_workflows", tenant_id=tenant_id))`); templates use `const base = "{{ js_workflows_base }}";`
- [ ] **Blocker 2: Trailing-slash 404 divergence (111 `url_for` calls at risk)**
  - [ ] Every admin router constructed with `APIRouter(redirect_slashes=True, include_in_schema=False)`
  - [ ] OR: the aggregated admin router in `build_admin_router()` sets `redirect_slashes=True` and nested sub-routers inherit cleanly (verified)
  - [ ] `tests/admin/test_trailing_slash_tolerance.py` exists and is green — iterates every registered admin route, hits both `path` and `path + "/"`, asserts neither returns 404
- [ ] **Blocker 3: `AdCPError` JSON-to-HTML browser regression**
  - [ ] `src/app.py::adcp_error_handler` is Accept-aware — if `request.url.path.startswith("/admin")` AND `"text/html" in accept`, render `error.html` via `src/admin/templating.templates`
  - [ ] `templates/error.html` (or `src/admin/templates/error.html` after Wave 3 move) exists, extends `base.html`, renders error message + back link
  - [ ] `tests/integration/test_admin_error_page.py` exists — forces `AdCPValidationError` from inside an admin route, asserts HTML response (not JSON), asserts body contains the error message
- [ ] **Blocker 4: Async event-loop session interleaving — PIVOTED 2026-04-11 to full async SQLAlchemy (Option A absorbed into v2.0 Waves 4-5)**
  - [ ] Pre-Wave-0 lazy-loading audit spike completed and approved (Risk #1 in `async-pivot-checkpoint.md` §4) — enumerates every `relationship()` access site and classifies as safe / eager-loadable / requires-rewrite
  - [ ] `src/core/database/database_session.py` converted: `create_engine` → `create_async_engine`, `scoped_session(sessionmaker(...))` → `async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)`
  - [ ] `get_db_session()` is an `@asynccontextmanager` yielding `AsyncSession`
  - [ ] Driver swap: `psycopg2-binary` + `types-psycopg2` removed, `asyncpg>=0.30.0` added in `pyproject.toml`
  - [ ] `alembic/env.py` uses async adapter (standard `create_async_engine` + `run_sync` pattern)
  - [ ] All repository classes use `async def` methods with `(await session.execute(select(...))).scalars().first()` pattern
  - [ ] All UoW classes implement `async def __aenter__` / `async def __aexit__` — OR deleted entirely under the Agent E idiom upgrade (FastAPI DI request-scoped session IS the unit of work; see `async-pivot-checkpoint.md` §3)
  - [ ] All admin router handlers are `async def` with `async with get_db_session()` / `await` DB calls (or, preferred, `session: SessionDep` via `Depends(get_session)`)
  - [ ] All `src/core/tools/*.py` `_impl` functions are `async def` (some already are)
  - [ ] `tests/harness/_base.py::IntegrationEnv` uses `async def __aenter__` / `async def __aexit__`
  - [ ] `factory_boy` adapter — one of the three options in `async-pivot-checkpoint.md` §3 chosen and implemented
  - [ ] All integration tests converted to `async def` + `@pytest.mark.asyncio` (or anyio equivalent)
  - [ ] `run_in_threadpool` used ONLY for file I/O, CPU-bound work, and sync third-party libraries — never for DB access
  - [ ] `tests/unit/test_architecture_admin_routes_async.py` exists and is green — AST-scans `src/admin/routers/*.py` and asserts every `@router.<method>(...)` handler is `async def`
  - [ ] `tests/unit/test_architecture_admin_async_db_access.py` exists and is green — AST-scans admin routers and asserts DB call-sites use `async with get_db_session()` + `await session.execute(...)` rather than sync `with` or `run_in_threadpool(_sync_fetch)`
  - [ ] The stale `tests/unit/test_architecture_admin_sync_db_no_async.py` is NOT created (wrong direction under the pivot)
  - [ ] Foundation module examples in `flask-to-fastapi-foundation-modules.md` updated to `async def` + async DB patterns
  - [ ] Worked examples in `flask-to-fastapi-worked-examples.md` updated to `async def` + async DB patterns (OAuth / SSE / favicon upload examples preserve their async outer signatures but drop `run_in_threadpool` wrappers around DB helper calls)
  - [ ] Main overview §13 `accounts.py` examples updated to `async def`
  - [ ] Connection pool `pool_size` bumped to match or exceed pre-migration sync threadpool capacity (Risk #6)
  - [ ] `created_at` / `updated_at` post-commit access audited (Risk #5 — `expire_on_commit=False` consequence)
  - [ ] Async vs pre-migration sync benchmark run on representative admin routes; latency profile net-neutral to ~5% improvement
- [ ] **Blocker 5: Middleware ordering — Approximated must run BEFORE CSRF**
  - [ ] `src/app.py` middleware stack registered in this order (outermost → innermost):
    1. `CORSMiddleware`
    2. `SessionMiddleware`
    3. `ApproximatedExternalDomainMiddleware`  ← MOVED UP from below CSRF
    4. `CSRFMiddleware`
    5. `RestCompatMiddleware`
    6. `UnifiedAuthMiddleware`
  - [ ] `ApproximatedExternalDomainMiddleware` redirect status is **307** (not 302) to preserve POST body per RFC 7231 §6.4.7
  - [ ] `tests/integration/test_external_domain_post_redirects_before_csrf.py` exists and is green — POSTs to `/admin/tenant/t1/accounts/create` with `Apx-Incoming-Host: ads.example.com`, no CSRF token, no session; asserts response is 307 (not 403)
- [ ] **Blocker 6: OAuth redirect URIs byte-identical**
  - [ ] `tests/unit/test_oauth_redirect_uris_immutable.py` exists and pins the EXACT set:
    - `/admin/auth/google/callback`
    - `/admin/auth/oidc/{tenant_id}/callback`
    - `/auth/gam/callback` (note: NOT under `/admin`)
  - [ ] Guard test asserts each expected route is in `{r.path for r in app.routes if hasattr(r, "path")}`
  - [ ] `src/admin/oauth.py` carries a comment referencing the byte-identity requirement
  - [ ] Wave 1 staging smoke test walks the REAL Google OAuth flow end-to-end (documented and executed before Wave 2 begins)

---

## Section 3 — First-order audit action items (from adcp-safety.md §7)

Full detail in `flask-to-fastapi-adcp-safety.md` §7.

- [ ] **Near-blocker:** `ApproximatedExternalDomainMiddleware` preserves the `is_admin_request` path gate from `src/admin/app.py:226-230` — ASGI port short-circuits on any path not starting with `/admin` (distinct test from Blocker 5 — this one guards the gate itself, not the ordering)
- [ ] Fix stale `tenant_management_api.py` route count **19 → 6** in `flask-to-fastapi-migration.md` §3.2
- [ ] `gam_reporting_api.py` reclassified **Category 2 → Category 1** (session-cookie authed); removed from `_LEGACY_PATH_PREFIXES` tuple; documented in main overview §2.8
- [ ] `/schemas/adcp/v2.4/*` external contract preserved — contract test `tests/integration/test_schemas_discovery_external_contract.py` exists and is green, pinning JSON shape and 404/500 body shape byte-for-byte
- [ ] Webhook payload preservation manual Wave 2 code review for:
  - [ ] `src/admin/blueprints/creatives.py` — `create_a2a_webhook_payload`, `create_mcp_webhook_payload`, `adcp.types.*` scoped to outbound webhook construction only
  - [ ] `src/admin/blueprints/operations.py` — same
  - [ ] **No `adcp.types.*` used as `response_model=`** on admin FastAPI routes
- [ ] `include_in_schema=False` on `build_admin_router()` — one-line applied
- [ ] `/_internal/` added to `CSRFMiddleware._EXEMPT_PATH_PREFIXES` in `src/admin/csrf.py`
- [ ] Three new structural guards exist and are green (from first-order audit):
  - [ ] `tests/unit/test_architecture_csrf_exempt_covers_adcp.py` — every non-GET route matching `/mcp`, `/a2a`, `/api/v1/`, `/a2a/` is covered by `_EXEMPT_PATH_PREFIXES`
  - [ ] `tests/unit/test_architecture_approximated_middleware_path_gated.py` — middleware short-circuits on any path not starting with `/admin`
  - [ ] `tests/unit/test_architecture_admin_routes_excluded_from_openapi.py` — `not any(p.startswith("/admin") for p in app.openapi()["paths"])`

---

## Section 4 — Per-wave acceptance checklists

Full detail in `flask-to-fastapi-execution-details.md` Part 1.

### Wave 0 — Foundation + template codemod (~2,500 LOC)

**Entry criteria:**

- [ ] All of Section 1 (pre-migration prerequisites) complete
- [ ] `main` is green (`make quality` + `tox -e integration` + `tox -e bdd`)
- [ ] `src/app.py:299-304` still has `a2wsgi` Flask mount (safety net)
- [ ] Migration overview §§11, 12, 13 signed off
- [ ] `SESSION_SECRET` defined in `.env.example` and staging secret store

**Files created — all 11 foundation modules plus supporting infra:**

- [ ] `src/admin/templating.py` (~150 LOC) — `Jinja2Templates` singleton, `_url_for` safe-lookup override pre-registered on `templates.env.globals`, `render()` wrapper with greenfield context (NO `admin_prefix`/`static_prefix`/`script_root`; only `request`, `support_email`, `sales_agent_domain`, `csrf_token`, plus handler-provided context keys)
- [ ] `src/admin/flash.py` (~70 LOC) — `flash(request, msg)` / `get_flashed_messages(request, with_categories=False)`
- [ ] `src/admin/sessions.py` (~40 LOC) — `build_session_middleware_kwargs()` returning `secret_key` from `SESSION_SECRET` (with dual-read of `FLASK_SECRET_KEY` for v2.0), `session_cookie='adcp_session'`, `same_site='lax'`, `https_only=True` in production
- [ ] `src/admin/oauth.py` (~60 LOC) — Authlib `starlette_client.OAuth` instance, Google client registered, `GOOGLE_CLIENT_NAME = "google"` constant, comment referencing OAuth URI immutability
- [ ] `src/admin/csrf.py` (~100 LOC) — pure-ASGI `CSRFMiddleware`, header-only read (never `await receive()`), `_EXEMPT_PATH_PREFIXES` includes `/mcp`, `/a2a`, `/api/v1/`, `/_internal/`, `/admin/auth/callback`, `/admin/auth/oidc/`, plus `csrf_token(request)` Jinja helper
- [ ] `src/admin/app_factory.py` (~80 LOC) — `build_admin_router()` returns `APIRouter(prefix="/admin", tags=["admin"], include_in_schema=False, redirect_slashes=True)`, empty in Wave 0
- [ ] `src/admin/deps/__init__.py` (2 LOC)
- [ ] `src/admin/deps/auth.py` (~260 LOC) — `CurrentUserDep`, `RequireAdminDep`, `RequireSuperAdminDep` as `Annotated[...]` aliases; dep functions are `async def` with `async with get_db_session()` / `await db.execute(...)` per the full-async pivot (2026-04-11)
- [ ] `src/admin/deps/tenant.py` (~90 LOC) — `CurrentTenantDep` filters `tenant.is_active=True` (fixes pre-existing latent bug)
- [ ] `src/admin/deps/audit.py` (~110 LOC) — FastAPI `Depends()`-based audit port (rewritten, not ported one-for-one); cached `AuditLogger` via `request.state`, not `flask.g`
- [ ] `src/admin/middleware/__init__.py` (2 LOC)
- [ ] `src/admin/middleware/external_domain.py` (~90 LOC) — pure-ASGI `ApproximatedExternalDomainMiddleware`, hard-gated on `/admin` path prefix, uses status 307 for redirects
- [ ] `src/admin/middleware/fly_headers.py` (~40 LOC) — pure-ASGI, may become unneeded if uvicorn `--proxy-headers` covers Fly.io (assumption #21)
- [ ] `src/admin/routers/__init__.py` (2 LOC)

**Template codemod:**

- [ ] `scripts/codemod_templates_greenfield.py` (~200 LOC) exists — two-pass regex rewrite
- [ ] `scripts/generate_route_name_map.py` (~50 LOC) exists — imports `src.admin.app.create_app()` and produces `FLASK_TO_FASTAPI_NAME` + `HARDCODED_PATH_TO_ROUTE` maps from `url_map.iter_rules()` introspection
- [ ] Codemod handles all greenfield transformations:
  - [ ] `{{ url_for('bp.endpoint', **kw) }}` → `{{ url_for('admin_bp_endpoint', **kw) }}` (Flask-dotted → flat admin-prefixed) — Pass 2
  - [ ] `{{ script_name }}/static/foo.css` → `{{ url_for('static', path='/foo.css') }}` — Pass 1a
  - [ ] `{{ script_name }}/tenant/{{ tenant_id }}/settings` → `{{ url_for('admin_tenants_settings', tenant_id=tenant_id) }}` via `HARDCODED_PATH_TO_ROUTE` — Pass 1b
  - [ ] `{{ script_name }}/logout` → `{{ url_for('admin_auth_logout') }}` — Pass 1b
  - [ ] `request.script_root` / `request.script_name` / `script_root` / `script_name` → **DELETED** (never appears in greenfield templates)
  - [ ] `csrf_token()` → `csrf_token` (Jinja variable, codemod Pass 0)
  - [ ] `get_flashed_messages(...)` → `get_flashed_messages(request, ...)` (add `request` first arg, codemod Pass 0)
  - [ ] `g.test_mode` → `test_mode` (drop `g.` prefix, codemod Pass 0)
  - [ ] JS template literals with `{{ script_name }}` inside backticks → flagged for manual review via `JS_TEMPLATE_LITERAL_RE` pre-pass
  - [ ] Bare `"/admin/..."` / `"/static/..."` string literals in quotes → flagged for manual review via `BARE_ADMIN_RE` post-pass
- [ ] Codemod runs to exit code 0 against all 72 templates in `/templates/`
- [ ] Codemod stdout reports `"72 templates processed, N transformations applied"`
- [ ] Codemod is idempotent: re-running on post-codemod templates yields zero diff
- [ ] `git diff --stat templates/` shows changes in ≥ 40 files
- [ ] `rg -n "url_for" templates/ | wc -l` ≥ 134 (did not drop references)
- [ ] Manual audit of tricky files — `add_product_gam.html` (15 `url_for` literals inside JS string literals), plus any other `§12.5` flagged files

**Tests created (Wave 0 additions):**

- [ ] `tests/unit/admin/test_templates_url_for_resolves.py` — AST-extracts every `url_for('name', ...)` from templates; asserts `name` in `{r.name for r in app.routes}`. Blocker 1 runtime safety net.
- [ ] `tests/unit/admin/test_templates_no_hardcoded_admin_paths.py` — Blocker 1 GREENFIELD guard. Forbids `script_name`/`script_root`/`admin_prefix`/`static_prefix` Jinja references AND bare `"/admin/"` / `"/static/"` string literals in quotes.
- [ ] `tests/unit/admin/test_architecture_admin_routes_named.py` — GREENFIELD: AST-scans `src/admin/routers/*.py`; every `@router.<method>(...)` decorator must have `name=` kwarg. Prerequisite for `url_for` coverage.
- [ ] `tests/unit/admin/test_codemod_idempotent.py` — GREENFIELD: running the template codemod twice produces no additional changes.
- [ ] `tests/unit/admin/test_oauth_callback_routes_exact_names.py` — Blocker 6 GREENFIELD enhancement: byte-pins OAuth callback route names AND paths together. Changing `/admin/auth/google/callback` name or path fails the test.
- [ ] `tests/unit/admin/test_trailing_slash_tolerance.py` — Blocker 2 guard
- [ ] `tests/unit/test_architecture_no_flask_imports.py` — empty allowlist check, ratchets per wave
- [ ] `tests/unit/test_architecture_admin_routes_async.py` — Blocker 4 guard (pivoted 2026-04-11). Asserts every admin router handler is `async def`. Replaces the wrong-direction `test_architecture_admin_sync_db_no_async.py` from the original plan.
- [ ] `tests/unit/test_architecture_admin_async_db_access.py` — Blocker 4 sibling guard. Asserts admin DB access uses `async with get_db_session()` + `await session.execute(...)`, not sync `with` or `run_in_threadpool(_sync_fetch)` wrappers for DB work.
- [ ] `tests/unit/test_architecture_handlers_use_annotated_depends.py` — Agent E idiom upgrade. AST-scans `src/admin/routers/*.py`; every `@router.<method>(...)` handler parameter must use `Annotated[T, ...]` form, not `x = Query(...)` default-value syntax.
- [ ] `tests/unit/test_architecture_templates_receive_dtos_not_orm.py` — Agent E idiom upgrade. Asserts every `render(request, "tpl", context)` call passes only primitives, Pydantic BaseModel instances, or the request object — never ORM model instances. Prevents lazy-load Risk #1 realization.
- [ ] `tests/unit/test_architecture_no_sync_session_usage.py` — Agent E idiom upgrade. Asserts no `Session(...)` or `sessionmaker(...)` imports outside `src/core/database/engine.py`. Only `AsyncSession` and `async_sessionmaker` allowed post-pivot.
- [ ] `tests/unit/test_architecture_no_module_level_engine.py` — Agent E idiom upgrade. Asserts no `create_async_engine` or `create_engine` at module scope — must be inside a function (lifespan factory). Prevents pytest-asyncio event-loop leak (Risk Interaction B).
- [ ] `tests/unit/test_architecture_no_direct_env_access.py` — Agent E idiom upgrade. Asserts no `os.environ.get` or `os.environ[` in `src/admin/` or `src/core/` except `src/core/settings.py` (the only file that reads env directly via pydantic-settings).
- [ ] `tests/unit/test_architecture_uses_structlog.py` — Agent E idiom upgrade. Asserts new `src/admin/` and `src/core/` files use `from src.core.logging import get_logger`, not `logging.getLogger(`. Allowlisted for existing files during migration.
- [ ] `tests/unit/test_architecture_repository_eager_loads.py` — Agent E idiom upgrade. Asserts every repository method whose DTO has nested-attribute returns has an `.options(selectinload(...))` call matching the nested relationships.
- [ ] `tests/unit/test_architecture_middleware_order.py` — Agent E idiom upgrade. Asserts the exact middleware registration order in `src/app.py` matches the documented runtime order (e.g., Approximated BEFORE CSRF per Blocker 5). Prevents reshuffling.
- [ ] `tests/unit/test_architecture_tests_use_async_client.py` — Agent E idiom upgrade. Scans `tests/integration/` and `tests/admin/` for `TestClient(app)` imports; asserts `httpx.AsyncClient(transport=ASGITransport(app=app))` is used instead. Allowlisted during migration.
- [ ] `tests/unit/test_architecture_exception_handlers_complete.py` — Agent E idiom upgrade. Scans `src/app.py` for `@app.exception_handler` decorators; asserts all 6 are registered (AdCPError, HTTPException, RequestValidationError, AdminRedirect, AdminAccessDenied, Exception).
- [ ] `tests/unit/test_architecture_csrf_exempt_covers_adcp.py` — first-order audit action #8a
- [ ] `tests/unit/test_architecture_approximated_middleware_path_gated.py` — first-order audit action #8b (also satisfies near-blocker #1)
- [ ] `tests/unit/test_architecture_admin_routes_excluded_from_openapi.py` — first-order audit action #8c
- [ ] `tests/unit/test_architecture_single_worker_invariant.py` — derivative guard (scheduler singleton protection)
- [ ] `tests/unit/test_architecture_harness_overrides_isolated.py` — derivative guard (`app.dependency_overrides` leakage protection)
- [ ] `tests/unit/test_architecture_scheduler_lifespan_composition.py` — NEW from apps inventory. AST-parses `src/app.py`, asserts `FastAPI(...)` has `lifespan=combine_lifespans(app_lifespan, mcp_app.lifespan)`. Prevents silent scheduler stop if MCP mount is dropped.
- [ ] `tests/unit/test_architecture_a2a_routes_grafted.py` — NEW from apps inventory. Walks `app.routes` and asserts `/a2a`, `/.well-known/agent-card.json`, `/agent.json` are top-level `Route` objects (NOT inside a `Mount`). Prevents future refactor from mounting A2A as a sub-app and breaking middleware propagation + `_replace_routes()`.
- [ ] `tests/unit/test_foundation_modules_import.py` — smoke test that every foundation module imports cleanly
- [ ] `tests/integration/test_schemas_discovery_external_contract.py` — contract test for `/schemas/adcp/v2.4/*` (first-order audit action #4)
- [ ] **(total Wave 0 structural guards = 16)**

**Harness extension:**

- [ ] `tests/harness/_base.py::IntegrationEnv.get_admin_client()` exists, added as sibling to `get_rest_client()` near line 914
- [ ] `get_admin_client()` snapshots `app.dependency_overrides` on `__enter__` and restores on `__exit__` (prevents test leakage per §3.3 deep audit)
- [ ] Smoke test: `python -c "from tests.harness import IntegrationEnv; ..."` succeeds (TestClient construction does not error against empty router)

**Blockers fixed in Wave 0:**

- [ ] Blocker 1 (script_root) — via codemod + `render()` wrapper + guard test
- [ ] Blocker 2 (trailing slash) — via `APIRouter(redirect_slashes=True)` default in `build_admin_router()`
- [ ] Blocker 4 (async session interleaving) — via full async SQLAlchemy pivot (Option A, 2026-04-11); Wave 0 adds the `test_architecture_admin_routes_async.py` guard + the lazy-loading audit spike. The full async conversion lands in Wave 4-5; the Wave 0 guard asserts the target-state handler signature is maintained from day one.

**What Wave 0 does NOT do (preserves mergeability):**

- [ ] `pyproject.toml` is unchanged
- [ ] `src/app.py` is unchanged (no middleware added, no router included)
- [ ] No Flask files deleted
- [ ] Flask catch-all still serving 100% of `/admin/*` traffic

**Exit criteria:**

- [ ] All 15 Wave-0 acceptance criteria in execution-details §Wave 0.A pass
- [ ] `make quality` green
- [ ] `tox -e integration` green
- [ ] `tox -e bdd` green
- [ ] `./run_all_tests.sh` green
- [ ] `python scripts/codemod_templates_greenfield.py --check templates/` returns exit 0 (idempotent re-run yields no diff) — enforced by `test_codemod_idempotent.py`
- [ ] Branch mergeable state verified
- [ ] Single squashed merge commit on `main`

### Wave 1 — Foundational routers + session cutover (~4,000 LOC)

**Entry criteria:**

- [ ] Wave 0 merged to `main`
- [ ] `SESSION_SECRET` live in staging secret store
- [ ] Playwright smoke run against the empty admin router confirms `get_admin_client()` infra is sound
- [ ] Authlib starlette_client happy-path spike completed on staging (assumption #8 verification from execution-details Part 2)

**Routers ported:**

- [ ] `src/admin/routers/public.py` (~400 LOC) — signup, landing, no-auth pages
- [ ] `src/admin/routers/core.py` (~600 LOC) — `/`, `/health`, dashboard
- [ ] `src/admin/routers/auth.py` (~1,100 LOC) — Google OAuth login flow via Authlib `starlette_client`
- [ ] `src/admin/routers/oidc.py` (~500 LOC) — per-tenant OIDC dynamic client registration

**Middleware stack wired in `src/app.py` in CORRECTED order (outermost → innermost):**

- [ ] 1. `CORSMiddleware` (already present)
- [ ] 2. `SessionMiddleware` (new, from `src/admin/sessions.py`)
- [ ] 3. `ApproximatedExternalDomainMiddleware` (new, BEFORE CSRF per Blocker 5)
- [ ] 4. `CSRFMiddleware` (new)
- [ ] 5. `RestCompatMiddleware` (already present)
- [ ] 6. `UnifiedAuthMiddleware` (already present)
- [ ] `tests/integration/test_middleware_ordering.py` exists and is green — inspects `app.user_middleware` and asserts the sequence

**Blockers fixed in Wave 1:**

- [ ] Blocker 3 (AdCPError HTML regression) — handler Accept-aware, `error.html` template exists, `test_admin_error_page.py` green
- [ ] Blocker 5 (middleware order) — swap applied, redirect is 307, `test_external_domain_post_redirects_before_csrf.py` green
- [ ] Blocker 6 (OAuth URI immutability) — guard test green AND a manual staging OAuth smoke test walked end-to-end against real Google with both OIDC tenants

**Foundation runtime verifications:**

- [ ] `GET /admin/login` serves from FastAPI, not Flask (curl + integration test)
- [ ] `POST /admin/auth/callback` completes a full redirect chain ending at `/admin/` with a fresh `adcp_session` cookie set by `SessionMiddleware`
- [ ] `GET /admin/health` serves from FastAPI; old Flask `/admin/health` commented out
- [ ] CSRF double-submit: `POST /admin/auth/logout` with valid session but no CSRF header returns 403; with valid session + matching cookie + header returns 303
- [ ] CSRF cookie generated on first GET of a page with a form
- [ ] `{{ csrf_token(request) }}` helper emits a token in hidden form field
- [ ] Session cookie cutover announcement sent to users before deploy
- [ ] Stale `session=...` cookie returns fresh login page (not an error); Playwright `login_with_stale_flask_cookie` test green
- [ ] `test_templates_url_for_resolves.py` runs in `--strict` mode — every `url_for("name")` in templates referenced by Wave 1 routers resolves to an actual registered endpoint

**Architecture guards update:**

- [ ] `test_architecture_no_flask_imports.py` allowlist shrunk — `public.py/core.py/auth.py/oidc.py` removed (forbid re-introducing Flask in migrated files)

**Dependency changes:**

- [ ] `pyproject.toml` adds `sse-starlette>=2.2.0`, `pydantic-settings>=2.7.0`, `itsdangerous>=2.2.0`

**Playwright smoke coverage (staging):**

- [ ] `tests/e2e/test_admin_login_flow.py` green — login → dashboard
- [ ] `tests/e2e/test_admin_csrf_enforcement.py` green

**Rollback infrastructure:**

- [ ] Rollback procedure tested in staging: revert commit, verify users forced through re-login, verify Flask catch-all re-serves all 4 migrated routes (because `register_blueprint` calls were commented out, not deleted)

**Exit criteria:**

- [ ] All 15 Wave-1 acceptance criteria in execution-details §Wave 1.A pass
- [ ] 4 new routers together have ≥ 90% branch coverage
- [ ] Zero Flask imports in `src/admin/routers/**`
- [ ] Staging deploy completes
- [ ] Manual login smoke test performed by 2 engineers against staging
- [ ] `make quality` + `tox -e integration` + `tox -e bdd` green
- [ ] Redirect assertion audit: pre-existing integration tests that asserted `response.status_code == 302` for login redirects updated to `303` (FastAPI `RedirectResponse` convention)
- [ ] Branch mergeable state verified

### Wave 2 — Bulk blueprint migration (~9,000 LOC)

**Entry criteria:**

- [ ] Wave 1 merged to `main` and running in staging ≥ 3 business days
- [ ] Wave 1 Playwright suite passing on staging nightly
- [ ] `scripts/check_coverage_parity.py` tested on Wave 1 and green
- [ ] `tests/integration/test_route_parity.py` baseline captured from Wave 1 staging (JSON map of URL+method → status)
- [ ] Platform team confirms no external consumer depends on Flask-specific category-1 JSON shapes (assumption #18 verification)
- [ ] `SESSION_SECRET` cookie-size instrumented in Wave 1 and confirmed < 3.5KB over 24h of staging traffic (assumption #5 verification)
- [ ] All 22 blueprints have designated owner reviewers
- [ ] Team `src/admin/` freeze announcement sent 48h before PR opens
- [ ] Freeze scope confirmed: entire `src/admin/**` except `activity_stream.py`; whole `tests/integration/**` for anything touching deleted fixtures
- [ ] Branch-lifetime budget confirmed: ≤ 7 calendar days

**Routers ported — 22 HTML/JSON blueprints plus 3 top-level APIs (25 target files):**

- [ ] `src/admin/routers/accounts.py`
- [ ] `src/admin/routers/products.py` (2,464 LOC source — audit for surprises)
- [ ] `src/admin/routers/principals.py`
- [ ] `src/admin/routers/users.py`
- [ ] `src/admin/routers/tenants.py`
- [ ] `src/admin/routers/gam.py`
- [ ] `src/admin/routers/inventory.py`
- [ ] `src/admin/routers/inventory_profiles.py`
- [ ] `src/admin/routers/creatives.py` — webhook payload preservation audit
- [ ] `src/admin/routers/creative_agents.py`
- [ ] `src/admin/routers/signals_agents.py`
- [ ] `src/admin/routers/operations.py` — webhook payload preservation audit
- [ ] `src/admin/routers/policy.py`
- [ ] `src/admin/routers/settings.py`
- [ ] `src/admin/routers/adapters.py` (re-homes deleted `register_ui_routes` content)
- [ ] `src/admin/routers/authorized_properties.py`
- [ ] `src/admin/routers/publisher_partners.py`
- [ ] `src/admin/routers/workflows.py`
- [ ] `src/admin/routers/api.py` (7 routes — dashboard AJAX)
- [ ] `src/admin/routers/format_search.py` (4 routes)
- [ ] `src/admin/routers/schemas.py` (6 routes — EXTERNAL contract preserved)
- [ ] `src/admin/routers/tenant_management_api.py` (6 routes — Category 2)
- [ ] `src/admin/routers/sync_api.py` (9 routes, duplicate mount at `/api/sync` preserved)
- [ ] `src/admin/routers/gam_reporting_api.py` (6 routes — Category 1 session-authed)

**Dead code deleted:**

- [ ] `src/services/gam_inventory_service.py::create_inventory_endpoints` function body (early-return dead code at line 1469)
- [ ] `src/adapters/google_ad_manager.py::register_ui_routes` hook — content re-homed into `src/admin/routers/adapters.py`
- [ ] `src/adapters/mock_ad_server.py::register_ui_routes` hook — same

**Flask files deleted:**

- [ ] 21 blueprint files under `src/admin/blueprints/` (every file EXCEPT `activity_stream.py`)
- [ ] `src/admin/tenant_management_api.py`
- [ ] `src/admin/sync_api.py`
- [ ] `src/adapters/gam_reporting_api.py`

**Test files deleted:**

- [ ] 17 integration test files building Flask test apps (§5.8 blast radius)
- [ ] `tests/admin/test_accounts_blueprint.py`
- [ ] `tests/admin/test_product_creation_integration.py`
- [ ] `tests/admin/conftest.py::ui_client` and `authenticated_ui_client` fixtures
- [ ] `tests/integration/conftest.py::flask_client`, `authenticated_client`, `admin_client`, `test_admin_app`, `authenticated_admin_client` fixtures
- [ ] `tests/conftest.py::flask_app`, `flask_client`, `authenticated_client` fixtures (lines 596-635 per §5.3)

**Error-shape classification tests:**

- [ ] `tests/integration/test_category1_native_error_shape.py` — asserts `POST /admin/api/*` endpoints return `{"detail": "..."}` on 4xx
- [ ] `tests/integration/test_category2_compat_error_shape.py` — asserts `POST /api/v1/tenant-management/*` and `POST /api/v1/sync/*` return `{"success": false, "error": "..."}` on 4xx (byte-for-byte vs Wave 1 golden fixture)

**Category-1 error-shape classification covers:**

- [ ] `src/admin/routers/api.py` (7 routes) — native shape
- [ ] `src/admin/routers/format_search.py` (4 routes) — native shape
- [ ] `src/admin/routers/gam_reporting_api.py` (6 routes) — native shape (reclassified)
- [ ] `change_account_status` at `/admin/tenant/<tid>/accounts/<aid>/status` — native shape

**Category-2 scoped exception handler verified:**

- [ ] `_LEGACY_PATH_PREFIXES = ("/api/v1/tenant-management", "/api/v1/sync", "/api/sync")`
- [ ] Does NOT include `/api/v1/products`, `/api/v1/media-buys`, or any AdCP REST path
- [ ] Does NOT include `gam_reporting_api` (now Category 1)

**Wave 2 audit tasks:**

- [ ] `schemas.py` external contract test green (byte-identical shape, 404/500 body shape preserved)
- [ ] Webhook payload preservation manual code review performed — no `adcp.types.*` used as `response_model=` on any admin route
- [ ] `datetime` serialization format audit — every `jsonify({...})` call in `src/admin/routers/gam.py`, `inventory.py`, etc. explicitly `.isoformat()`s datetime values before serialization
- [ ] `scripts/check_coverage_parity.py` per-wave gate passed — new routers ≥ (old coverage − 1)

**Playwright admin flows green on staging:**

- [ ] Login → dashboard
- [ ] Create account
- [ ] Create product
- [ ] Delete product
- [ ] Logout
- [ ] Re-login

**Structural guards update:**

- [ ] `test_architecture_no_flask_imports.py` allowlist shrunk to 3 entries:
  - `src/admin/app.py`
  - `src/app.py`
  - `src/admin/blueprints/activity_stream.py`
- [ ] `src/admin/blueprints/` directory contains only `activity_stream.py`
- [ ] `git grep -l "flask" src/admin/` returns only `app.py` and `blueprints/activity_stream.py`
- [ ] `test_architecture_no_raw_select.py` allowlist naturally shrinks (admin files use repositories by design)

**Flask catch-all status:**

- [ ] Flask catch-all still wired at `src/app.py:299-304` as a safety net
- [ ] `tests/integration/test_flask_catchall_unreached.py` — asserts no request routes to the Flask mount during `./run_all_tests.sh`
- [ ] Flask catch-all receives zero requests in 24h of staging traffic (monitored)

**Operational verifications:**

- [ ] Datadog dashboards confirmed green by platform team
- [ ] No external consumer references Flask-era endpoints

**Exit criteria:**

- [ ] All 15 Wave-2 acceptance criteria in execution-details §Wave 2.A pass
- [ ] Branch lifetime ≤ 7 calendar days from open to merge (daily rebase)
- [ ] `make quality` + `tox -e integration` + `tox -e bdd` + `./run_all_tests.sh` green
- [ ] PR description includes blueprint-by-blueprint diff summary
- [ ] 3 reviewers assigned per area (HTML UI / JSON API / adapters)

### Wave 3 — Activity stream SSE + cleanup cutover (~2,500 LOC)

**Entry criteria:**

- [ ] Wave 2 merged to `main` and stable in staging ≥ 5 business days
- [ ] Flask catch-all receives zero traffic in staging for 48h
- [ ] Datadog/dashboard audit confirms no external consumer references Flask-era endpoints
- [ ] `v1.99.0` git tag created and container image archived in registry (rollback fallback)
- [ ] SSE spike completed — disconnect detection validated behind Fly.io + nginx

**Activity stream SSE port:**

- [ ] `src/admin/routers/activity_stream.py` (~400 LOC) exists using `sse_starlette.EventSourceResponse`
- [ ] `GET /admin/tenant/{tenant_id}/activity-stream` opens SSE, emits events within 500ms of tenant activity logged
- [ ] Client disconnect detection works — server stops producing events within 2s of client close (verified by test + manual staging check)
- [ ] `MAX_CONNECTIONS_PER_TENANT` backstop enforced: 11th concurrent connection returns 429
- [ ] `X-Accel-Buffering: no` header set on SSE responses
- [ ] `tests/integration/test_activity_stream_sse.py` green
- [ ] `tests/integration/test_activity_stream_disconnect.py` green
- [ ] `tests/integration/test_activity_stream_backpressure.py` green

**Dependency removals from `pyproject.toml`:**

- [ ] `flask>=3.1.3`
- [ ] `flask-caching>=2.3.0`
- [ ] `flask-socketio>=5.5.1`
- [ ] `python-socketio>=5.13.0`
- [ ] `simple-websocket>=1.1.0`
- [ ] `waitress>=3.0.0`
- [ ] `a2wsgi>=1.10.0`
- [ ] `types-waitress` (dev)
- [ ] `werkzeug` (if still pinned)
- [ ] `uv lock` or `poetry lock --check` succeeds post-removal

**Files deleted:**

- [ ] `src/admin/app.py` (427 LOC — old Flask factory)
- [ ] `src/admin/blueprints/activity_stream.py` (390 LOC)
- [ ] `src/admin/blueprints/` directory (now empty)
- [ ] `src/admin/server.py` (103 LOC — orphan standalone Flask server)
- [ ] `scripts/run_admin_ui.py` (references deleted `src/admin/server.py`)
- [ ] `src/admin/utils/helpers.py::require_auth` (dead after all callers migrated)
- [ ] `src/admin/utils/helpers.py::require_tenant_access` (dead)
- [ ] `tests/admin/conftest.py` (legacy fixtures)

**Files modified in `src/app.py`:**

- [ ] `_install_admin_mounts()` function deleted (lines 25-45)
- [ ] `flask_admin_app = create_app()` / `admin_wsgi = WSGIMiddleware(...)` deleted
- [ ] Flask mount at `src/app.py:299-304` deleted
- [ ] `/a2a/` trailing-slash redirect deleted (src/app.py:127-135)
- [ ] `app.router.routes.insert(0, Route("/", ...))` landing hack deleted (src/app.py:351-352)
- [ ] `CustomProxyFix` references removed
- [ ] 17 `noqa: E402` carve-outs cleaned up
- [ ] Uvicorn invocation in `scripts/run_server.py` uses `--proxy-headers --forwarded-allow-ips='*'`
- [ ] `FlyHeadersMiddleware` retained OR deleted per assumption #21 verification

**Pre-commit + CI:**

- [ ] `.pre-commit-hooks/check_route_conflicts.py` rewritten for FastAPI — scans `app.routes` introspection, not Flask URL map
- [ ] Rewritten hook has unit test against known-conflicting fixture
- [ ] Rewritten hook passes on current main

**Template and static file moves (`git mv` preserves history):**

- [ ] `/templates/` → `src/admin/templates/`
- [ ] `/static/` → `src/admin/static/`
- [ ] `Jinja2Templates(directory=...)` singleton in `src/admin/templating.py` points to new path
- [ ] `StaticFiles` mount at `src/app.py` points to `src/admin/static/`

**Architecture guards final state:**

- [ ] `test_architecture_no_flask_imports.py` allowlist is EMPTY
- [ ] `rg -w flask src/` returns zero hits
- [ ] `rg 'from flask' tests/` returns zero hits
- [ ] `rg 'a2wsgi\|werkzeug\|waitress\|flask_caching\|flask_socketio' src/` returns zero hits

**Release engineering:**

- [ ] `CHANGELOG.md` v2.0.0 entry written with breaking changes:
  - Dependency removal list
  - `FLASK_SECRET_KEY` → `SESSION_SECRET` (dual-read in v2.0)
  - Session cookie rename `session` → `adcp_session` (forced re-login)
  - Error-shape split (Category 1 native / Category 2 compat)
  - CSRF required on form POSTs
  - Admin router not exposed in `/openapi.json`
  - Redirect status changes 302 → 307 for external-domain redirect
- [ ] `CHANGELOG.md` references `flask-to-fastapi-migration.md` §15 (deps) and §19 (flow changes)
- [ ] Docker image build completes
- [ ] Docker image size delta measured — target ≥ 60 MB reduction vs Wave 2
- [ ] Playwright full regression suite green against staging v2.0.0 build
- [ ] Production deploy plan approved
- [ ] Production smoke test plan drafted: deploy → login → create tenant → create product → submit creative → SSE activity stream visible → logout

**Proxy-header smoke tests (Wave 3 pre-deploy — CRITICAL):**

The Flask removal also removes Flask's internal WSGI proxy-header stack (`CustomProxyFix`, `FlyHeadersMiddleware`, werkzeug `ProxyFix` at `src/admin/app.py:187-194`). These rewrote `Fly-Forwarded-Proto` → `X-Forwarded-Proto` and handled `X-Script-Name` for reverse-proxy deployments. Their replacement is `uvicorn --proxy-headers --forwarded-allow-ips='*'` (deep audit §R4 / §2.5). If this replacement fails, `request.url.scheme` returns `http` instead of `https` in production, which breaks OAuth by producing `redirect_uri=http%3A%2F%2F...` that Google Cloud Console rejects with `redirect_uri_mismatch` → **login is dead in production.**

- [ ] `scripts/run_server.py` launches uvicorn with `--proxy-headers --forwarded-allow-ips='*'` (verified in the file)
- [ ] Staging deploy of Wave 3 build
- [ ] `curl -sI https://staging-tenant.scope3.com/admin/login` — verify `Location:` header (if redirect) contains `https://`, not `http://`
- [ ] `curl -sI 'https://staging-tenant.scope3.com/admin/auth/google/initiate'` — verify the OAuth-initiation response's `Location:` header contains `redirect_uri=https%3A%2F%2F...` (URL-encoded `https://`). If it contains `redirect_uri=http%3A%2F%2F...`, `--proxy-headers` is not reading `X-Forwarded-Proto` correctly and OAuth will fail with `redirect_uri_mismatch`. **STOP deployment and fix before proceeding.**
- [ ] Manual browser test: click "Log in" on staging, verify the browser arrives at a real Google OAuth consent page (not an error page). Complete the OAuth flow; verify the callback lands on `https://staging-tenant.scope3.com/admin/...` and the admin UI loads.
- [ ] If `FlyHeadersMiddleware` was kept (assumption #21 deferred): verify Fly-specific header rewriting still works by checking `scope["headers"]` logs in staging for requests carrying `Apx-Incoming-Host`.

**Exit criteria:**

- [ ] All 15 Wave-3 acceptance criteria in execution-details §Wave 3.A pass
- [ ] `rg -w flask .` from repo root returns zero hits
- [ ] ~~`v2.0.0` git tag applied~~ **(MOVED TO Wave 5 under the async pivot 2026-04-11; Wave 3 merges but v2.0.0 tag waits until Waves 4-5 land)**
- [ ] Staging canary runs 48h without incident
- [ ] Wave 3 merges to `main`; Waves 4-5 continue the async DB layer absorption

### Wave 4 — Async database layer (~7,000-10,000 LOC, pivoted 2026-04-11)

**Entry criteria:**

- [ ] Wave 3 merged to `main` and stable in staging ≥ 3 business days
- [ ] Pre-Wave-0 lazy-loading audit spike outcome approved (see Section 1.1)
- [ ] Pre-Wave-0 async driver compatibility spike outcome approved (see Section 1.1)
- [ ] `v1.99.0` git tag remains as the Flask-era rollback fallback

**Core DB layer conversion (Agent A scope inventory governs this list):**

- [ ] `src/core/database/database_session.py` rewrite:
  - [ ] `create_engine` → `create_async_engine` (with `postgresql://` → `postgresql+asyncpg://` URL rewriter)
  - [ ] `scoped_session(sessionmaker(...))` → `async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)`
  - [ ] `get_db_session()` becomes `@asynccontextmanager async def` yielding `AsyncSession`
  - [ ] Engine is lifespan-scoped (not module-level) per Agent E Category 1 guidance — created in `database_lifespan(app)` and stored on `app.state.db_engine`
  - [ ] `SessionDep = Annotated[AsyncSession, Depends(get_session)]` defined in `src/core/database/deps.py`
- [ ] `alembic/env.py` rewrite — standard async adapter pattern (~30 LOC, `create_async_engine` + `connection.run_sync(do_run_migrations)`)
- [ ] All repository classes converted to `async def` methods with `(await session.execute(select(...))).scalars().first()` pattern
- [ ] UoW classes either converted to `async def __aenter__` / `async def __aexit__` OR deleted in favor of `SessionDep` + per-repository Dep factories (Agent E preferred: FastAPI's request-scoped session IS the unit of work)
- [ ] All `src/core/tools/*.py` `_impl` functions converted to `async def` (several already are per Agent D Section 1.3 inventory)
- [ ] All `*_raw` wrapper functions in `src/core/tools/*.py` converted to `async def`
- [ ] Remaining sync `with get_db_session()` call sites in `src/admin/` and `src/core/` converted to `async with`

**Agent D mitigations (AdCP wire-safety — MUST land in Wave 4):**

- [ ] **M1.** Add 8 missing `await` keywords in `src/routes/api_v1.py` (lines 200, 214, 252, 284, 305, 324, 342, 360) — each is a 1-character insertion
- [ ] **M2.** Add 2 missing `await` keywords in `src/core/tools/capabilities.py` (lines 265, 310)
- [ ] **M3.** Convert sync `_raw` functions to `async def` (same as bullet above; Agent D M3 is a cross-ref to Agent A scope)
- [ ] **M4.** `tests/unit/test_api_v1_routes_await_all_impls.py` — AST-walks `src/routes/api_v1.py`, asserts every `_raw`/`_impl` call site is `await`-prefixed when the target is async def. Prevents M1/M2 regression.
- [ ] **M5.** `tests/integration/test_get_media_buys_wire_datetime_present.py` — creates a media buy without explicit `created_at`, fetches via `get_media_buys`, asserts `created_at` is not None in the wire response. Guards against the INSERT path relying on post-commit server_default read.
- [ ] **M6.** `tests/unit/test_architecture_no_server_default_without_refresh.py` — AST-parses `src/core/database/models.py`, finds every `server_default=`, fails if there's no `# NOQA: server-default-refreshed` comment or parallel ORM `default=`.
- [ ] **M7.** Migrate `server_default=func.now()` → `default=datetime.utcnow` for columns whose instances are read post-INSERT (`Creative.created_at`, `Creative.updated_at`, `MediaBuy.created_at`, `MediaBuy.updated_at`).
- [ ] **M8.** `tests/unit/test_architecture_adcp_datetime_nullability.py` — asserts `GetMediaBuysMediaBuy.created_at` and `updated_at` remain `datetime | None`. Prevents schema tightening.
- [ ] **M9.** `tests/unit/test_openapi_byte_stability.py` — snapshots `app.openapi()` to a committed JSON under `tests/unit/fixtures/openapi_snapshot.json`; CI asserts the OpenAPI spec matches. Catches unintended schema drift.

**Test harness conversion:**

- [ ] `tests/harness/_base.py::IntegrationEnv` converts to `async def __aenter__` / `async def __aexit__`
- [ ] `factory_boy` adapter chosen from the three options in `async-pivot-checkpoint.md` §3 (Agent E recommendation: option B — custom `AsyncSQLAlchemyModelFactory` wrapper)
- [ ] Integration tests mass-converted to `async def` + `@pytest.mark.asyncio` (scriptable via AST transform)
- [ ] Test harness switches from sync `TestClient(app)` to `httpx.AsyncClient(transport=ASGITransport(app=app))` with `app.dependency_overrides[get_session]` pattern (Agent E Category 14)
- [ ] Per-test engine fixture (function-scoped) + per-test session fixture to prevent event-loop leak

**Pool + connection tuning:**

- [ ] Connection pool `pool_size` bumped to match or exceed pre-migration sync threadpool capacity (Risk #6)
- [ ] `pool_pre_ping=True` and `pool_recycle=3600` set to handle Fly.io network blips and 2h idle kill
- [ ] `DB_POOL_SIZE` + `DB_POOL_MAX_OVERFLOW` env vars documented with production-safe defaults

**Wave 4 — Tooling gate (Agent F findings):**

In addition to the code conversion work:

- [ ] `check_database_health()` in `scripts/deploy/run_all_services.py` rewritten or deleted (F3.5.1)
- [ ] `check_schema_issues()` in `scripts/deploy/run_all_services.py` rewritten (F3.5.2)
- [ ] `init_database()` in `scripts/deploy/run_all_services.py` audited for async safety (F3.5.3)
- [ ] `scripts/deploy/entrypoint_admin.sh` psycopg2 probe rewritten or script deleted (F3.6.1)
- [ ] `Dockerfile` `libpq-dev` / `libpq5` removal (F1.2.1, F3.1.1)
- [ ] `docker-compose*.yml` DATABASE_URL compatibility verified (F3.2.1, F3.3.1, F3.4.1)
- [ ] New structural guards for admin routes async (F6.2.2), async DB access (F6.2.3), templates no ORM (F6.2.4) landed
- [ ] `tox -e driver-compat` env added and runs in CI (F2.1.2, F2.4.5)
- [ ] `/health/pool` + `/metrics` endpoints added (F4.1.1, F4.1.3)
- [ ] DB pool Prometheus gauges added (F4.1.4)
- [ ] `contextvars` request-ID propagation landed (F4.3.1)
- [ ] CLAUDE.md + `/docs` async updates complete (F5.1.*, F5.2.*)
- [ ] All 5 scripts with top-level `database_session` imports audited for Risk #33 (F7.3.1)

**Wave 4 exit criteria:**

- [ ] All tests converted and green: `tox -e unit`, `tox -e integration`, `tox -e bdd`, `tox -e e2e`, `tox -e admin`
- [ ] Agent D M1-M9 mitigations all landed and green
- [ ] OpenAPI snapshot matches (M9 guard)
- [ ] `/health/db` and `/health/schedulers` endpoints return healthy
- [ ] Staging deploy completes with zero 500s on hot admin routes for 24h

### Wave 5 — Async cleanup + v2.0.0 release (~3,000-5,000 LOC)

**Entry criteria:**

- [ ] Wave 4 merged to `main` and stable in staging ≥ 3 business days

**Async-vs-sync benchmark:**

- [ ] Async vs pre-migration sync baseline benchmark run on representative admin routes (read-heavy `GET /admin/tenant/t1/products` + write-heavy `POST /admin/tenant/t1/accounts`)
- [ ] Latency profile is net-neutral to ~5% improvement under moderate concurrency (Risk #10)
- [ ] If regression: tune `pool_size` (Risk #6) first; `selectinload` eager-loading second; last resort fallback is to revert and defer async to v2.1

**Startup + observability:**

- [ ] Startup log assertion: schedulers (`delivery_webhook`, `media_buy_status`) report "alive" on first tick
- [ ] `/health/pool` exposes SQLAlchemy AsyncEngine pool stats (size, checked_in, checked_out, overflow)
- [ ] `/health/schedulers` returns alive-tick timestamps for each scheduler task

**Consequence audit (Risk #5):**

- [ ] `created_at` / `updated_at` post-commit access sites audited — `expire_on_commit=False` means these fields are not refreshed after commit; any code reading them post-commit without explicit `await session.refresh(obj)` must be fixed

**Wave 5 — Tooling polish (Agent F findings):**

- [ ] `.duplication-baseline` ratcheted back to ≤ Wave 4 start level (F7.4.2)
- [ ] Benchmark comparison CI job passing (F2.4.6, F8.4.1)
- [ ] `pyproject.toml` version bumped to 2.0.0 (F8.4.2)
- [ ] FIXME comments for async landmines closed (F7.5.2)
- [ ] Auto-memory `flask_to_fastapi_migration_v2.md` updated to reflect pivot (F7.6.1)
- [ ] Release notes include: driver swap, new env vars, new endpoints, new guards

**v2.0.0 release:**

- [ ] `CHANGELOG.md` v2.0.0 entry includes async pivot breaking changes: `psycopg2-binary` → `asyncpg`, `expire_on_commit=False` default, async handler signatures, async repository methods, async test harness
- [ ] `v2.0.0` git tag applied
- [ ] Production deploy plan approved
- [ ] Production deploy completes

---

## Section 5 — Rollback triggers and procedures

Full detail in `flask-to-fastapi-execution-details.md` §D under each wave.

### Rollback triggers per wave

- [ ] **Wave 0**: any failure of `make quality` post-merge; any templates regression found in Wave 1 entry check
- [ ] **Wave 1**: OAuth login broken in staging/prod; session cookie causes auth loop; CSRF middleware blocks POST form flows; middleware ordering causes 403s on external-domain POSTs
- [ ] **Wave 2**: any migrated admin route returns 500 against production traffic; Datadog dashboard loss; category-2 error shape regression caught by external consumer; coverage parity check fails
- [ ] **Wave 3**: uvicorn `--proxy-headers` fails to produce correct `https` scheme in production; SSE disconnect detection fails in production; dependency lockfile resolution produces incompatible tree

### Wave 0 rollback procedure

Wave 0 is **pure addition** — nothing changes behavior. Single-commit revert.

- [ ] `git checkout main`
- [ ] `git revert -m 1 <wave-0-merge-sha>`
- [ ] `git push origin main`
- [ ] Verify `make quality` green on post-revert main
- [ ] No database state to restore, no env vars to roll back

### Wave 1 rollback procedure

Single-commit revert works. Users get one EXTRA forced re-login (in addition to the one Wave 1 already caused).

- [ ] `git checkout main`
- [ ] `git revert -m 1 <wave-1-merge-sha>`
- [ ] `git push origin main`
- [ ] Verify `register_blueprint` calls in `src/admin/app.py` auto-restored (they were commented out, not deleted)
- [ ] Verify `SESSION_SECRET` can remain set — Flask ignores it, no harm
- [ ] Document forced re-login in revert PR description
- [ ] No database state to restore

### Wave 2 rollback procedure

Single-commit revert; largest revert commit. Flask catch-all re-activates.

- [ ] `git checkout main`
- [ ] `git revert -m 1 <wave-2-merge-sha> --no-edit`
- [ ] `git diff HEAD~1 --stat | head -30` — verify 25+ files restored
- [ ] `git push origin main`
- [ ] Verify Flask catch-all at `src/app.py:299-304` is still live
- [ ] **Partial rollback option**: if only ONE router broke, revert just that file + its tests + re-add `register_blueprint(<bp>)` to `src/admin/app.py`, leaving the rest of Wave 2 intact
- [ ] Rollback window is open only until Wave 3 merges

### Wave 3 rollback procedure

**This is the dangerous cutover.** Wave 3 cannot roll back piecemeal.

- [ ] `git checkout main`
- [ ] `git revert -m 1 <wave-3-merge-sha> --no-edit`
- [ ] `cat pyproject.toml | grep -A 2 flask` — verify Flask deps restored
- [ ] `uv lock` — rebuild lockfile
- [ ] `docker build .` — rebuild image
- [ ] `grep -n "flask_admin_app\|admin_wsgi\|_install_admin_mounts" src/app.py` — verify Flask catch-all restored
- [ ] **Fallback option** (if git revert is too risky): redeploy the archived `v1.99.0` container image from the registry, accept downtime
- [ ] Rollback window is open until Wave 4 (the async SQLAlchemy conversion within v2.0) merges; after Wave 4, rollback becomes effectively impossible (driver has switched to asyncpg and async deps have spread through the codebase). Pivoted 2026-04-11 — async SQLAlchemy is no longer a separate v2.1 PR; it's absorbed as Wave 4-5 of v2.0.

---

## Section 6 — Post-migration verification (run after Wave 3 merges)

- [ ] Production traffic monitoring for 48 hours
- [ ] Error rate comparison vs pre-migration baseline (Datadog / logs)
- [ ] Admin UI latency p50 comparison vs pre-migration baseline
- [ ] Admin UI latency p99 comparison vs pre-migration baseline
- [ ] Docker image size delta reported to team (expected ~60-80 MB reduction)
- [ ] No 5xx spike in first 24h post-deploy
- [ ] SSE activity stream connection count stable (no leaks)
- [ ] `SESSION_SECRET` cookie size observed < 3.5 KB across all real users
- [x] ~~v2.1 async SQLAlchemy migration scoping kickoff scheduled~~ — Async SQLAlchemy already merged as Wave 4-5 of v2.0 (pivoted 2026-04-11 — no separate v2.1 kickoff needed)
- [ ] v2.1 nginx removal scoping kickoff scheduled
- [ ] v2.1 REST routes `Annotated[T, Depends()]` ratchet scoping kickoff scheduled
- [ ] v2.1 `FLASK_SECRET_KEY` dual-read removal scoping kickoff scheduled
- [ ] v2.1 `Apx-Incoming-Host` IP allowlist (security hardening) ticket filed
- [ ] v2.1 `require_tenant_access` `is_active` pre-existing-bug fix confirmed (Wave 0 tenant dep already fixed it; v2.1 deletes the dead Flask helper)
- [ ] v2.1 `/_internal/reset-db-pool` auth hardening ticket filed
- [ ] v2.2 multi-worker scheduler lease design ticket filed
- [ ] All 6 companion notes files archived to `.claude/notes/archive/flask-to-fastapi/` OR retained as historical reference (see Section 7)
- [ ] `feat/v2.0.0-flask-to-fastapi` branch deleted after successful merge + 1 week
- [ ] Auto-memory `flask-to-fastapi-migration-v2` entry marked complete

---

## Section 7 — Planning artifact cleanup after migration complete

Run this section only after v2.0.0 has been stable in production for ≥ 2 weeks.

- [ ] `.claude/notes/flask-to-fastapi/flask-to-fastapi-migration.md` — decide: archive to `.claude/notes/archive/flask-to-fastapi/` OR delete
- [ ] `.claude/notes/flask-to-fastapi/flask-to-fastapi-execution-details.md` — archive or delete
- [ ] `.claude/notes/flask-to-fastapi/flask-to-fastapi-foundation-modules.md` — archive or delete
- [ ] `.claude/notes/flask-to-fastapi/flask-to-fastapi-worked-examples.md` — archive or delete
- [ ] `.claude/notes/flask-to-fastapi/flask-to-fastapi-adcp-safety.md` — archive or delete
- [ ] `.claude/notes/flask-to-fastapi/flask-to-fastapi-deep-audit.md` — archive or delete
- [ ] `.claude/notes/flask-to-fastapi/implementation-checklist.md` (this file) — archive or delete
- [ ] Auto-memory entry `flask-to-fastapi-migration-v2` — remove
- [ ] Project `CLAUDE.md` "active migration" breadcrumb — remove
- [ ] `feat/v2.0.0-flask-to-fastapi` branch — delete after merge + stability window
- [ ] `v1.99.0` container image in registry — retention window 30 days, then prune
- [ ] `.duplication-baseline` — regenerate against new admin LOC footprint

---

## Section 8 — Known tech debt explicitly deferred to v2.1+

These items are **intentionally NOT in scope for v2.0**. They are referenced here so nothing is forgotten. Full detail in `flask-to-fastapi-deep-audit.md` Section 7 table.

### v2.1 scope (follow-on PR after v2.0 stabilizes)

- [x] ~~**Async SQLAlchemy**~~ **MOVED TO v2.0 Waves 4-5** (pivoted 2026-04-11) — convert `create_engine` → `create_async_engine`, `Session` → `AsyncSession`, all repositories to `async def`, ~100+ files affected. Detail: `async-pivot-checkpoint.md` §3 for target state; `flask-to-fastapi-migration.md` §18 (rewritten) for wave execution; deep audit §1.4 (rewritten) for Option A rationale.
- [ ] **Drop nginx entirely** — ~30 MB image savings; uvicorn `--proxy-headers` + tiny Starlette middleware covers all nginx responsibilities. Detail: `flask-to-fastapi-deep-audit.md` §4.1.
- [ ] **Ratchet REST routes to `Annotated[T, Depends()]`** — 14 route signatures in `src/routes/api_v1.py` currently use `= resolve_auth` default-value style. Add guard `test_architecture_rest_uses_annotated.py`. Detail: `flask-to-fastapi-deep-audit.md` §4.2.
- [ ] **Remove `FLASK_SECRET_KEY` dual-read** — remove fallback from `src/admin/sessions.py`, remove from `scripts/setup-dev.py`, `docker-compose.yml`, `docs/deployment/environment-variables.md`, `docs/development/troubleshooting.md`, update `tests/unit/test_setup_dev.py` (9 occurrences)
- [ ] **`/_internal/reset-db-pool` auth hardening** — pre-existing weakness; endpoint is only env-var gated (`ADCP_TESTING=true`). Detail: `flask-to-fastapi-deep-audit.md` §7 R9.
- [ ] **`require_tenant_access` `is_active` check** — pre-existing latent bug in Flask (Wave 0 `CurrentTenantDep` already filters `is_active=True`; v2.1 cleans up the dead Flask helper)
- [ ] **Structured logging (structlog / logfire) swap-in** — clean integration now that Flask is gone; `logfire` already in deps. Detail: `flask-to-fastapi-deep-audit.md` §4 opportunity list.

### v2.2 scope (requires additional design)

- [ ] **Multi-worker scheduler lease design** — today's webhook and media-buy-status schedulers are single-worker singletons. Multi-worker requires Postgres advisory lock OR separate scheduler container. Detail: `flask-to-fastapi-deep-audit.md` §3.1.
- [ ] **SSE per-tenant rate limit moved to Redis** — today's `connection_counts` dict is per-process; multi-worker effective limit becomes `10 × workers`. Redis-backed replacement is a multi-worker prerequisite. Detail: `flask-to-fastapi-deep-audit.md` §3.2.
- [ ] **`Apx-Incoming-Host` IP allowlist** — security hardening for the Approximated external-domain middleware. No client-side spoofing today because Fly.io terminates externally, but explicit allowlist is defensive. Detail: `flask-to-fastapi-deep-audit.md` §7 Y8.

---

## Cross-reference: companion documents

All six companion files live under `.claude/notes/flask-to-fastapi/`:

1. **`flask-to-fastapi-migration.md`** (1,878 lines) — main overview
   - §2 — User-confirmed decisions (8 directives)
   - §2.7 — AdCP boundary verification (first-order audit summary)
   - §2.8 — Deep-audit blockers summary (cross-reference to Section 2 of this file)
   - §3 — Current-state Flask inventory
   - §4 — Current-state FastAPI inventory
   - §10 — Target architecture (module layout, `src/app.py` shape)
   - §11 — Foundation module descriptions
   - §12 — Template codemod details
   - §13 — Three worked route examples (simple cases)
   - §14 — 5-6 wave strategy (pivoted 2026-04-11; cross-reference to Section 4 of this file)
   - §15 — Dependency changes
   - §16 — 28 assumptions
   - §18 — v2.0 Waves 4-5 async SQLAlchemy absorption (pivoted 2026-04-11; was v2.1 follow-on pre-pivot)
   - §19 — Natural flow changes
   - §21 — Verification strategy
2. **`flask-to-fastapi-execution-details.md`** (1,142 lines) — per-wave detail
   - Part 1 — per-wave execution with A (acceptance), B (files), C (risks), D (rollback), E (merge conflicts), F (time), G (entry/exit) for each wave
   - Part 2 — 28-assumption verification recipes
   - Part 3 — structural guard AST patterns, integration test templates, Playwright e2e test plan, benchmark harness, `scripts/check_coverage_parity.py` automation
3. **`flask-to-fastapi-foundation-modules.md`** (2,507 lines) — full code for 11 foundation modules with tests and gotchas
4. **`flask-to-fastapi-worked-examples.md`** (2,790 lines) — 5 real Flask-blueprint → FastAPI-router translations (OAuth, OIDC, file upload, SSE, products form)
5. **`flask-to-fastapi-adcp-safety.md`** (412 lines) — first-order AdCP boundary audit, 8 action items, verdict CLEAR
6. **`flask-to-fastapi-deep-audit.md`** (787 lines) — deep 2nd/3rd-order audit, 6 BLOCKERS, 20 RISKS, 40+ OPPORTUNITIES

If a reader only reads this checklist, they should not miss anything critical — every blocker, every risk, every action item from the six companion documents is representable as a checkbox here.
