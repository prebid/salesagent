# Flask → FastAPI v2.0.0 Migration — Implementation Checklist

**Status:** SOURCE OF TRUTH for "am I ready to ship Wave N?"
**Target release:** salesagent v2.0.0
**Feature branch:** `feat/v2.0.0-flask-to-fastapi`
**Last updated:** 2026-04-14 (v2.0 includes everything under 8-layer strategy: L0-L4 sync, L5 async, L6-L7 polish and ship)

> **8-LAYER SCOPE (2026-04-14)**
>
> v2.0 ships L0-L4 with **sync `def` admin handlers** and sync SQLAlchemy, then L5 converts to async,
> then L6-L7 polish and tag v2.0.0. Async SQLAlchemy is Layer 5+ within v2.0 (not a separate release).
>
> **Sections in this file that still contain async language:**
> - §1.2 Decisions 1, 3, 7, 9 — describe async-era work; tagged `[L5+]`
> - §2 Blocker 4 — describes async conversion; entire section is **L5 scope**
> - §4 Wave 4 and Wave 5 — async conversion scope; **L5-L7 scope**
> - §3.5 items tagged `[L5+]` — async-only findings
>
> **For implementation, read `execution-plan.md` (8 layers: L0-L7).** This checklist
> is for verification tracking.

## How to use this file

> **Implementation agents: START with [`execution-plan.md`](execution-plan.md), NOT this file.**
> The execution plan has 8 layers in strict order (L0, L1a, L1b, L1c, L1d, L2, L3, L4, L5a, L5b, L5c, L5d1-L5d5, L5e, L6, L7), each a standalone briefing with
> everything you need (goal, prereqs, knowledge sources, work items, exit gate).
> This checklist is the **verification/tracking** document — tick boxes here AFTER
> completing work defined in the execution plan.

This checklist consolidates every action item from the companion migration documents into categorized sections. Each checkbox is a prerequisite, blocker fix, action item, wave acceptance criterion, rollback trigger, post-migration verification step, or deferred tech-debt tracking item.

## Wave ↔ Layer mapping

Legacy "Wave" section headings predate the 8-layer rename. Translation:

| Legacy Wave | Current Layer | Scope |
|-------------|---------------|-------|
| Wave 0 | L0 | Foundation + template codemod |
| Wave 1 | L1a + L1b | Middleware + public/core routers; auth + OIDC cutover |
| Wave 2 | L1c + L1d | Low-risk HTML routers; medium/high-risk + APIs |
| Wave 3 | L2 | Flask removal + cache migration + cleanup |
| Wave 4 | L3 + L4 + L5a-L5e | Test harness; sync refinement; async conversion |
| Wave 5 | L6 + L7 | Native refinements; polish & ship |

**Relationship between this file and `execution-plan.md`:**
- **`execution-plan.md`** = what to do, in what order (read BEFORE coding)
- **`implementation-checklist.md`** (this file) = verification tracking (tick AFTER completing)
- Items in this file are tagged with `[§X]` references; the execution plan pulls items from ALL sections into phase-ordered work lists

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
- [ ] **[L5+]** `DATABASE_URL` env var format compatible with asyncpg driver rewrite (staging + prod + test): `postgresql://...` gets rewritten to `postgresql+asyncpg://...` at engine construction — verified — pivoted 2026-04-11
- [ ] `SESSION_SECRET` documented in `.env.example`
- [ ] `SESSION_SECRET` documented in `docs/deployment/environment-variables.md`
- [ ] OAuth redirect URIs currently registered in Google Cloud Console enumerated and documented in a migration runbook — at minimum:
  - [ ] `https://<tenant>.scope3.com/admin/auth/google/callback`
  - [ ] `https://<tenant>.scope3.com/admin/auth/oidc/callback` (NO `{tenant_id}` in URL — tenant context comes from the session, verified at `src/admin/blueprints/oidc.py:209,215`)
  - [ ] `https://<tenant>.scope3.com/admin/auth/gam/callback` (WITH `/admin` prefix — route registered in `auth.py:959` under `auth_bp` which is mounted at `/admin` via nginx `SCRIPT_NAME`; verified at `src/admin/blueprints/auth.py:931`)
- [ ] External consumer contracts confirmed for Category-2 files:
  - [ ] `src/admin/tenant_management_api.py` (6 routes, `X-Tenant-Management-API-Key`)
  - [ ] `src/admin/sync_api.py` (9 routes, `X-API-Key`, duplicate mount at `/api/sync`)
  - [ ] `src/admin/blueprints/schemas.py` (`/schemas/adcp/v2.4/*`, external JSON Schema validators)
- [ ] Feature branch `feat/v2.0.0-flask-to-fastapi` created from green main
- [ ] Team announcement sent: `src/admin/` freeze will take effect during Wave 2 for ≤ 7 calendar days
- [ ] Rollback window documented for each wave (see Section 5)
- [ ] Staging environment matches production topology:
  - [ ] Fly.io proxy header behavior verified (`X-Forwarded-*` present)
  - [ ] nginx config unchanged during L0-L4 (nginx removal deferred to L5+)
  - [ ] `ADCP_TESTING=true` gating for `/_internal/reset-db-pool` confirmed
- [ ] Pre-Wave-0 `main` branch passes `make quality` + `tox -e integration` + `tox -e bdd`
- [ ] Pre-Wave-0 `main` has `a2wsgi` Flask mount still at `src/app.py:299-304` (safety net)
- [ ] v1.99.0 git tag plan documented (last-known-good Flask-era release, tagged before Wave 3 merges)
- [ ] **Spike 4.5 and Spike 5.5 are NOT pre-Wave-0 items.** Per CLAUDE.md canonical spike sequence (10 technical spikes + 1 decision gate): **Spike 4.5 runs at L4 entry** (ContextManager stateless refactor smoke test — validates Decision 7 before the L4 refactor PR lands); **Spike 5.5 runs at L5a entry** (two-engine coexistence — validates Decision 9 before async conversion starts). See L4 and L5a entry-criteria sections for full acceptance criteria. Verified by: neither spike gates §1.1 pre-Wave-0 entry; L4 entry-criteria lists Spike 4.5; L5a entry-criteria lists Spike 5.5. **Spikes 1 and 2 also run at L5a entry** (lazy-load audit + driver compat — they depend on L4's DTO boundary + SessionDep alias being in place). Neither blocks pre-Wave-0 or L0 entry.
- [ ] **Agent F pre-Wave-0 hard gate items completed (non-code surface inventory, corrected 2026-04-11):**
  - [ ] **`psycopg2-binary` RETAINED** — partial reversal of Agent F F1.1.1 per Decisions 1 (Path B sync factory) and 2 (pre-uvicorn health checks). (Pre-D3 plan also cited Decision 9 sync-bridge as a third retention rationale; under D3 2026-04-16 the sync-bridge is superseded by an async rearchitect, so D1+D2 alone are sufficient.) **[L5+ — asyncpg not in L0-L4]** `asyncpg>=0.30.0,<0.32` added alongside (not replacing). `types-psycopg2` also retained. **Fallback:** `psycopg[binary,pool]>=3.2.0` if Spike 2 (driver compat) fails — see `CLAUDE.md` pre-Wave-0 spike sequence.
  - [ ] **[L5+ — not needed for sync admin tests]** `[tool.pytest.ini_options]` added to `pyproject.toml` with `asyncio_mode = "auto"` (F1.7.1, F8.2.1)
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
  - [ ] Alembic `env.py` stays sync with psycopg2 (supersedes prior async-env.py rewrite plan — see async-pivot-checkpoint.md for history). Alembic gains nothing from running async — migrations are serial, single-connection operations. All 161 existing migrations use sync patterns that work under the greenlet bridge. Spike 6 scope is `render_item` hook for JSONType + advisory lock for multi-container safety (~0.5 day).
  - [ ] **L0 EXIT:** CI Postgres version aligned to 17 across all workflows (F2.4.1). `.github/workflows/test.yml` currently uses PG15 (line 135) and PG16 (line 196); local dev + Fly.io production run PG17. Captures golden-fingerprint parity before L1a starts. One-line YAML bump per workflow. Agent F originally scheduled this under the L5a driver spike; moved to L0 because PG version skew distorts golden-fingerprint fixtures at L1a.
  - [ ] Dead `test-migrations` pre-commit hook removed (F2.3.1)
  - [ ] 3 new structural guards added (F6.2.1, F6.2.5, F6.2.6)
  - [ ] New docs `async-debugging.md` + `async-cookbook.md` drafted (F5.3.1, F5.3.2)
  - [ ] **[L5+]** Full `asyncpg` wheel availability verified for glibc + macOS (F1.1.3)
  - [ ] Duplication baseline snapshotted at Wave 4 start (F7.4.1)

### 1.2 Architectural decisions recorded in the migration plan

- [ ] **[LAYERED 2026-04-14]** **Admin handlers use sync `def` at L0-L4, convert to `async def` with full async SQLAlchemy at L5** — per deep audit blocker #4 and the 8-layer (L0-L7) model. Canonical plan: `execution-plan.md` L5; decision history: `async-pivot-checkpoint.md`; invariants: `CLAUDE.md` critical invariant 1.
- [ ] **Middleware order: Approximated BEFORE CSRF** (corrected) — per deep audit blocker #5; documented in `flask-to-fastapi-migration.md` §2.8 and §10.2
- [ ] **Redirect status code: 307** (not 302) — preserves POST body per RFC 7231
- [ ] **`FLASK_SECRET_KEY` transition: hard-remove at L2** (v2.0 breaking change aligned with cookie rename; supersedes prior dual-read plan). Dev onboarding reads `SESSION_SECRET` only. Release notes call out the env-var rename. See §L2 work items.
- [ ] **Error-shape split decided:** Category 1 native `{"detail": ...}`, Category 2 legacy `{"success": false, "error": ...}` via scoped handler — documented in `flask-to-fastapi-migration.md` §2 directive #8
- [ ] **[L5+]** **Decision 1 (adapter Path B, 2026-04-11):** adapter methods stay sync `def`; 18 call sites in `src/core/tools/*.py` + 1 in `src/admin/blueprints/operations.py:252` wrap in `await run_in_threadpool(...)`. `src/core/database/database_session.py` exports `get_sync_db_session()` alongside async `get_db_session()` (dual session factory). `AuditLogger.log_operation` splits into `_log_operation_sync` (internal) + async public wrapper. `anyio.to_thread.current_default_thread_limiter().total_tokens = 80` at lifespan startup. Structural guard `test_architecture_adapter_calls_wrapped_in_threadpool.py`. Full implementation reference: `flask-to-fastapi-foundation-modules.md` §11.14. Full target state: `async-pivot-checkpoint.md` §3 "Adapters (Decision 1 Path B)".
- [ ] **[L5+]** **Decision 7 (ContextManager refactor, 2026-04-11):** delete `ContextManager` class + `DatabaseManager` entirely. 12 public methods become stateless `async def` module functions taking `session: AsyncSession` as first parameter. 7 production callers migrate (incl. dead `main.py:166` + module-load side effect in `mcp_context_wrapper.py:345` + `mock_ad_server.py threading.Thread → asyncio.create_task`). ~50 test patches, 20 collapsible via `tests/harness/media_buy_update.py::EXTERNAL_PATCHES` update. Validated by Spike 4.5. Structural guard `test_architecture_no_singleton_session.py`. Error-path composition gotcha: use SEPARATE `async with session_scope()` for error-logging writes (outer scope rolls back on raise). Full target state: `async-pivot-checkpoint.md` §3 "ContextManager refactor".
- [ ] **[L5+]** **Decision 9 / D3 (background_sync async rearchitect, 2026-04-16 supersedes 2026-04-11 Option B sync-bridge):** `src/services/background_sync_service.py` rearchitects to `asyncio.create_task` + checkpoint-per-GAM-page. Each GAM-page (~30s) opens its own short-lived `async with get_db_session()`, writes progress to a `sync_checkpoint` row, commits, closes. Resume logic reads checkpoint and continues from next cursor on next tick. `threading.Thread` workers become `asyncio.create_task(...)` in the lifespan, registered on `app.state.active_sync_tasks: dict[str, asyncio.Task]`, cancellable on shutdown. Session lifetime is always << `pool_recycle=3600`; no sync-bridge engine needed. `src/services/background_sync_db.py` is NOT created (never written). `psycopg2-binary` + `libpq5` + `libpq-dev` narrowing: retained ONLY for Decision 2 fork-safety (`db_config.py`) — NOT for this service. Wave 3 flask-caching correction still bundled: 3 consumer sites (inventory.py:874, :1133, background_sync_service.py:472), SimpleAppCache replacement required before deletion, closes `from flask import current_app` ImportError at line 472 (post-D3, site 472 runs inside an async task rather than a `threading.Thread`, but uses the module-global `get_app_cache()` helper unchanged). Validated by Spike 5.5 (checkpoint-session viability). Structural guard `test_architecture_sync_bridge_scope.py` is deleted from plan; replacement guard `test_architecture_no_threading_thread_for_db_work.py` (empty allowlist) introduced. Fallback on Spike 5.5 failure: revert to pre-D3 Option B sync-bridge. Full target state: `async-pivot-checkpoint.md` §3 "Background sync sync-bridge" (marked SUPERSEDED 2026-04-16).
- [ ] **[L5+]** **Decision 3 (factory-boy async shim, refined 2026-04-11):** custom `AsyncSQLAlchemyModelFactory` overrides `_save` (not `_create`), `sqlalchemy_session_persistence = None`, `session.add(instance)` directly (no `sync_session.add`), NO `flush()` call. 3 bugs fixed from Audit 06 recipe. Wave 4b-4c hard cliff: all 166 integration tests must flip async BEFORE factory base classes flip. New Spike 4.25 (0.5 day, soft blocker). 3 structural guards: `test_architecture_factory_inherits_async_base.py`, `test_architecture_factory_no_post_generation.py`, `test_architecture_factory_in_all_factories.py`. Full recipe: `foundation-modules.md` §11.13.1 (D).
- [ ] **Decision 4 (queries.py convert-and-prune, refined 2026-04-11):** 6 functions (not 7), zero production callers, 3 dead functions → delete + allowlist cleanup. 3 live functions → async conversion. Test file `test_creative_review_model.py` converts to async. Net: ~−100 LOC. Move to `CreativeRepository` deferred to L5+ within v2.0 (Option 4B).
- [ ] **Decision 5 (database_schema.py + product_pricing.py DELETE, refined 2026-04-11):** `database_schema.py` confirmed orphan → delete Wave 5. `product_pricing.py` has 1 caller already eager-loading, inspect-guard defeated by unconditional log at line 43 → DELETE entirely in Wave 4, inline conversion at single caller as `AdminPricingOptionView` Pydantic DTO. Supersedes Audit 06 SUBSTITUTE (RuntimeError prescription was technically ineffective).
- [ ] **Decision 6 (flask-caching → SimpleAppCache, refined 2026-04-11):** ~90 LOC module with `cachetools.TTLCache(maxsize=1024, ttl=300)` + `threading.RLock` + `_NullAppCache` fallback + `CacheBackend` Protocol. Both inventory sites rewritten to cache dicts not Flask Response objects. `cache_key`+`cache_time_key` folded into single 2-tuple entry. 12-step strict migration order in Wave 3 PR. 2 structural guards. Full recipe: `foundation-modules.md` §11.15.
- [ ] **Decision 8 (SSE DELETE, 2026-04-11):** `/tenant/{id}/events` SSE route is orphan code — template says "use polling", zero EventSource consumers. DELETE route + generator + rate-limit state + HEAD probe in Wave 4. Fix `api_mode=False → True` on surviving `/activity` JSON poll route. −170 LOC. (`sse_starlette` is NOT currently in `pyproject.toml` — the "-1 pip dep" framing from the original 2026-04-11 analysis was inflated; no dep to remove.) Structural guard `test_architecture_no_sse_handlers.py`.
- [ ] **Admin router OpenAPI: `include_in_schema=False`** — documented in `flask-to-fastapi-adcp-safety.md` §3
- [ ] **`gam_reporting_api.py` reclassified Category 2 → Category 1** (session-cookie authed = admin-UI-only) — documented
- [ ] **`tenant_management_api.py` route count fixed 19 → 6** in plan docs
- [ ] **Session cookie name: `session` → `adcp_session` with forced re-login at L1a cutover** (reaffirms user decision #7). SessionMiddleware is configured with `session_cookie="adcp_session"` only; legacy `session=...` cookies are silently ignored and users are redirected through OAuth on first post-cutover request. No dual-read middleware is built. Customer-communication plan is mandatory — see §L1a. Rationale: admin-only session surface (no external AdCP API users affected), zero custom crypto during security-critical layer, ~0 LOC vs. ~150 LOC + deletion PR.
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
  - [ ] Codemod runs successfully against all 74 templates (per `async-audit/frontend-deep-audit.md` FE-1 inventory; supersedes earlier 73-count estimate); stdout reports `"N templates processed, M rewrites applied"`
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
- [ ] **Blocker 4: L0-L4 use sync `def` admin handlers; async event-loop session interleaving is a L5+ concern (supersedes prior "async-first" plan — see async-pivot-checkpoint.md for history).** v2.0 L0-L4 use sync `def` handlers — scoped_session thread-local identity works correctly in FastAPI's threadpool. The entire Blocker 4 checklist below is L5+ scope.
  - [ ] Pre-Wave-0 lazy-loading audit spike completed and approved (Risk #1 in `async-pivot-checkpoint.md` §4) — enumerates every `relationship()` access site and classifies as safe / eager-loadable / requires-rewrite
  - [ ] `src/core/database/database_session.py` converted: `create_engine` → `create_async_engine`, `scoped_session(sessionmaker(...))` → `async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)`
  - [ ] `get_db_session()` is an `@asynccontextmanager` yielding `AsyncSession`
  - [ ] Driver addition: `asyncpg>=0.30.0,<0.32` added alongside (NOT replacing) `psycopg2-binary` + `types-psycopg2` in `pyproject.toml` — psycopg2 RETAINED per Decisions 1 (Path B sync adapter factory) and 2 (pre-fork orchestrator health check). (Pre-D3 plan also cited Decision 9 sync-bridge; under D3 2026-04-16 the sync-bridge is superseded by an async rearchitect, so D1+D2 alone are sufficient.) Removal deferred to L5+ within v2.0+.
  - [ ] `alembic/env.py` stays sync with psycopg2 (supersedes prior "async adapter" plan — see Section 1.1 Spike 6 correction and async-pivot-checkpoint.md for history). `postgresql+asyncpg://` URL rewriting is applied ONLY at async engine construction in `database_session.py`, NOT in Alembic.
  - [ ] All repository classes use `async def` methods with `(await session.execute(select(...))).scalars().first()` pattern
  - [ ] All UoW classes implement `async def __aenter__` / `async def __aexit__` — OR deleted entirely under the Agent E idiom upgrade (FastAPI DI request-scoped session IS the unit of work; see `async-pivot-checkpoint.md` §3)
  - [ ] **[L5+]** All admin router handlers are `async def` with `async with get_db_session()` / `await` DB calls (or, preferred, `session: SessionDep` via `Depends(get_session)`)
  - [ ] All `src/core/tools/*.py` `_impl` functions are `async def` (some already are)
  - [ ] `tests/harness/_base.py::IntegrationEnv` uses `async def __aenter__` / `async def __aexit__`
  - [ ] `factory_boy` adapter — one of the three options in `async-pivot-checkpoint.md` §3 chosen and implemented
  - [ ] All integration tests converted to `async def` + `@pytest.mark.asyncio` (or anyio equivalent)
  - [ ] `run_in_threadpool` used ONLY for file I/O, CPU-bound work, and sync third-party libraries — never for DB access
  - [ ] **[L5+]** `tests/unit/test_architecture_admin_handlers_async.py` exists and is green — AST-scans `src/admin/routers/*.py` and asserts every `@router.<method>(...)` handler is `async def`
  - [ ] `tests/unit/test_architecture_admin_async_db_access.py` exists and is green — AST-scans admin routers and asserts DB call-sites use `async with get_db_session()` + `await session.execute(...)` rather than sync `with` or `run_in_threadpool(_sync_fetch)`
  - [ ] The stale `tests/unit/test_architecture_admin_sync_db_no_async.py` is NOT created (wrong direction under the pivot)
  - [ ] **[L5+]** Foundation module examples in `flask-to-fastapi-foundation-modules.md` updated to `async def` + async DB patterns
  - [ ] **[L5+]** Worked examples in `flask-to-fastapi-worked-examples.md` updated to `async def` + async DB patterns (OAuth / favicon upload examples preserve their async outer signatures but drop `run_in_threadpool` wrappers around DB helper calls; JSON-poll `/activity` example replaces the prior SSE example — Decision 8 deletes SSE)
  - [ ] Main overview §13 `accounts.py` examples updated to `async def`
  - [ ] Connection pool `pool_size` bumped to match or exceed pre-migration sync threadpool capacity (Risk #6)
  - [ ] `created_at` / `updated_at` post-commit access audited (Risk #5 — `expire_on_commit=False` consequence)
  - [ ] Async vs pre-migration sync benchmark run on representative admin routes; latency profile net-neutral to ~5% improvement
- [ ] **Blocker 5: Middleware ordering — Approximated must run BEFORE CSRF**
  - [ ] `src/app.py` middleware stack in canonical runtime order (outermost → innermost):
    1. `FlyHeadersMiddleware`
    2. `ApproximatedExternalDomainMiddleware`
    3. `UnifiedAuthMiddleware`
    4. `SessionMiddleware`
    5. `CSRFOriginMiddleware`
    6. `RestCompatMiddleware`
    7. `CORSMiddleware`
  - [ ] Registration in `src/app.py` is in **REVERSE** order (LIFO — innermost added first): `CORS`, `RestCompat`, `CSRF`, `Session`, `UnifiedAuth`, `ApproximatedExternalDomain`, `FlyHeaders`
  - [ ] Hard invariant (notes/CLAUDE.md #2): `ApproximatedExternalDomainMiddleware` runs BEFORE `CSRFOriginMiddleware` — the canonical order satisfies this (ExternalDomain is outer of the pair)
  - [ ] `ApproximatedExternalDomainMiddleware` redirect status is **307** (not 302) to preserve POST body per RFC 7231 §6.4.7
  - [ ] `tests/integration/test_external_domain_post_redirects_before_csrf.py` exists and is green — POSTs to `/admin/tenant/t1/accounts/create` with `Apx-Incoming-Host: ads.example.com`, no Origin header, no session; asserts response is 307 (not 403)
- [ ] **Blocker 6: OAuth redirect URIs byte-identical**
  - [ ] `tests/unit/test_oauth_redirect_uris_immutable.py` exists and pins the EXACT set:
    - `/admin/auth/google/callback`
    - `/admin/auth/oidc/callback` (NO `{tenant_id}` — tenant context is in the session; corrected per FE-3 audit 2026-04-11, verified at `src/admin/blueprints/oidc.py:209`)
    - `/admin/auth/gam/callback` (WITH `/admin` prefix — corrected per FE-3 audit 2026-04-11, verified at `src/admin/blueprints/auth.py:931,959`)
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
- [ ] `/_internal/` added to `CSRFOriginMiddleware._EXEMPT_PATH_PREFIXES` in `src/admin/csrf.py`
- [ ] Three new structural guards exist and are green (from first-order audit):
  - [ ] `tests/unit/test_architecture_csrf_exempt_covers_adcp.py` — every non-GET route matching `/mcp`, `/a2a`, `/api/v1/`, `/a2a/` is covered by `_EXEMPT_PATH_PREFIXES`
  - [ ] `tests/unit/test_architecture_approximated_middleware_path_gated.py` — middleware short-circuits on any path not starting with `/admin`
  - [ ] `tests/unit/test_architecture_admin_routes_excluded_from_openapi.py` — `not any(p.startswith("/admin") for p in app.openapi()["paths"])`

---

## Section 3.5 — Verification audit findings (3-round Opus audit, 2026-04-12)

Three rounds of parallel Opus subagent verification (14 agents total) audited the plan against actual source code, derivative consequences, silent breaking bugs, code pattern consistency, CLAUDE.md compliance, and senior engineering practices. All findings below are scoped into specific waves.

### 3.5.1 Silent breaking bugs (pass all tests, break production)

- [ ] **[L5+]** **SB-1 [CRITICAL, Wave 4b]: Product `@property` methods trigger lazy loads on 3 relationships.** `src/core/database/models.py:341-489` — five `@property` methods (`effective_format_ids`, `effective_properties`, `effective_property_tags`, `effective_implementation_config`, `is_gam_tenant`) access `self.inventory_profile`, `self.tenant`, `self.adapter_config`. `ProductRepository.list_all()` eagerly loads `tenant` and `pricing_options` but NOT `inventory_profile`. Production paths raise `MissingGreenlet`. **Fix:** Add `selectinload(Product.inventory_profile)` to all Product repository methods whose callers access these properties. Add to Spike 1 acceptance criteria: every Product `@property` must be exercised against every repo method.
- [ ] **SB-2 [CRITICAL, Wave 2]: `request.form.getlist()` — 20+ call sites silently lose multi-value form data.** `src/admin/blueprints/products.py` has 12+ calls for `<select multiple>` fields (countries, channels, formats, principals, property_tags). FastAPI `Form()` returns only the LAST value, not a list. **Fix:** Use `List[str] = Form()` for multi-value fields. Add foundation-module pattern doc and structural guard `test_architecture_form_getlist_parity.py` in Wave 0.
- [ ] **SB-3 [HIGH, Wave 0]: `session.*` and `g.*` template variables silently render as empty.** `templates/base.html:145` uses `{% if g.test_mode %}` (test mode banner); lines 155-183 read `session.role`, `session.authenticated`, `session.email`. Starlette does NOT auto-inject `g` or `session` into Jinja context. **Fix:** `render()` wrapper must pass `test_mode`, `user_role`, `user_email`, `user_authenticated`, `username` as explicit context variables. Add `test_template_context_completeness.py` guard asserting every variable used in `base.html` is present in `render()` context dict.
- [ ] **[L0-L4: document risk only. L5+: mandatory fix.]** **SB-4 [HIGH, Wave 4a]: `onupdate=func.now()` columns permanently stale after UPDATE+commit.** Unlike `server_default` (INSERT-time, covered by Risk #5), `onupdate=func.now()` fires on UPDATE. With `expire_on_commit=False`, response returns OLD `updated_at`. Cannot be fixed with ORM-side `default=`. **Fix:** Set `obj.updated_at = func.now()` application-side before commit, or `await session.refresh(obj, ['updated_at'])` after commit. Add `test_onupdate_columns_refreshed.py` in Wave 4.
- [ ] **[L5+]** **SB-5 [HIGH, Wave 4b]: N+1 lazy load in `get_object_lifecycle` (`context_manager.py:430`).** Inside a loop, `mapping.workflow_step` triggers per-row lazy load → `MissingGreenlet` under async. **Fix:** Add `joinedload(ObjectWorkflowMapping.workflow_step)` to the query at line 414.

### 3.5.2 Loud but scoped breaks (caught by tests)

- [ ] **[L5+]** **LB-1 [HIGH, Spike 2 + Wave 4a]: `asyncpg` bypasses `json_serializer` parameter.** `database_session.py:114,130` passes `json_serializer=_pydantic_json_serializer` to `create_engine`. asyncpg uses its own native JSONB codec that ignores this. Pydantic types fail with `TypeError` on JSONB write. **Fix:** Register custom asyncpg JSONB codec via `asyncpg.Connection.set_type_codec()` at engine-level `connect` event. Add `test_jsonb_roundtrip_asyncpg.py` to Spike 2 criteria.
- [ ] **[L5+]** **LB-2 [HIGH, Wave 4a]: `statement_timeout` event listener crashes under asyncpg.** `database_session.py:139-144` uses `dbapi_conn.cursor()` which doesn't exist on asyncpg connections. **Fix:** Replace with `connect_args={"server_settings": {"statement_timeout": "30000"}}` on async engine. Already corrected in `async-pivot-checkpoint.md` code block (2026-04-12).
- [ ] **[L5+]** **LB-3 [MEDIUM, Wave 4b]: `bulk_insert_mappings` / `bulk_update_mappings` removed in `AsyncSession`.** `gam_inventory_service.py:98,104,734,739`. **Fix:** Rewrite to async Core `insert().values()` pattern (`await session.execute(insert(Table).values([...]))`). Post-D3 these sites are INSIDE the background_sync async rearchitect scope — they run inside the per-GAM-page `async with get_db_session()` block, use the async session, and commit per page.

### 3.5.3 Scope gaps (not previously in any wave — now added)

- [ ] **SG-1 [CRITICAL, Wave 2c + Wave 3]: `gam_inventory_service.py` has 8 Flask routes NOT in any wave scope.** `src/services/gam_inventory_service.py:1484-1680` — `create_inventory_endpoints(app)` registers 8 `@app.route()` decorators directly on the Flask app (called from `src/admin/app.py:391`). These live in `src/services/`, not `src/admin/blueprints/`, so they are missed by the Wave 2 "22 blueprints" scope. **Fix:** Port to `src/admin/routers/inventory_api.py` in Wave 2c. Delete `create_inventory_endpoints()` in Wave 3.
- [ ] **SG-2 [CRITICAL, Wave 3]: `atexit` handlers incompatible with async shutdown.** `src/services/webhook_delivery_service.py:185` and `src/services/delivery_simulator.py:45` register `atexit.register(self._shutdown)`. Under uvicorn, `atexit` fires AFTER the event loop is closed. **Fix:** Move to FastAPI lifespan post-yield block in Wave 3.
- [ ] **SG-3 [HIGH, Wave 2c]: `register_ui_routes(app: Flask)` adapter interface not addressed.** `src/adapters/base.py:481`, `google_ad_manager.py:1694`, `mock_ad_server.py:1346` take a Flask `app` object. **Fix:** Change interface to accept `APIRouter` in Wave 2c; re-home content into `src/admin/routers/adapters.py`.
- [ ] **[L0-L4: document risk only. L5+: mandatory fix.]** **SG-4 [HIGH, Wave 4]: GAM services have private `scoped_session` instances.** `gam_inventory_service.py` and `gam_orders_service.py` create their own module-level `scoped_session` bypassing `database_session.py`. **Fix:** post-D3, these services are reached from the async rearchitected `background_sync_service` — migrate to per-call `async with get_db_session()` (same pattern used by the rest of the async codebase) in Wave 4. Add structural guard `test_architecture_no_private_scoped_session.py`.
- [x] **SG-5 [HIGH, L1b]: RESOLVED pending L1b implementation — separate `oauth_transit` cookie handles form_post cross-origin POST.** `src/admin/app.py:117` currently sets `SESSION_COOKIE_SAMESITE = "None"`. OIDC providers using `response_mode=form_post` send cross-origin POSTs — `SameSite=Lax` on the session cookie blocks it. **Resolution:** per `foundation-modules.md §11.6.1`, a separate `oauth_transit` cookie is set with `SameSite=None; Secure; HttpOnly` and `path="/admin/auth/"`, 10-minute max_age, carrying `{state, nonce, code_verifier}` across the IdP round-trip. The admin session cookie stays `SameSite=Lax` (no CSRF posture weakening). Sub-items tracked here:
  - [ ] **Audit tenant OIDC configs** — list tenants whose `OIDCConfig.response_mode` is `form_post` vs `query` (most default to `query`; SG-5 affects only the `form_post` subset, but the fix ships for all tenants so any provider can be flipped without re-deploy).
  - [ ] **Implement `src/admin/oauth_transit.py`** — ~60 LOC per `foundation-modules.md §11.6.1` Section C. Signed + timed via `itsdangerous.URLSafeTimedSerializer`.
  - [ ] **Add `/admin/auth/oidc/callback` to `CSRFOriginMiddleware` exempt list** (§11.6.1 Section E) — the IdP's Origin cannot be pre-registered; state validation replaces Origin validation on this path.
  - [ ] **Integration tests** — `test_oidc_form_post_cross_origin_callback_validates` (green path) and `test_oidc_form_post_rejects_state_mismatch` (negative path), per §11.6.1 Section F.
  - [ ] **Callback handler wiring** — `src/admin/routers/oidc.py` calls `finish_oauth_flow()` and `delete_transit_cookie()` as shown in §11.6.1 Section D.
- [ ] **SG-6 [MEDIUM, Wave 2+3]: 7 files outside `src/admin/` import Flask.** `background_sync_service.py:472`, `gam_inventory_service.py` (8 sites), `gam_inventory_discovery.py:1074`, `gam_reporting_api.py:16,68`, `mock_ad_server.py:1349`, `google_ad_manager.py:25,1696`. **Fix:** Each must be migrated or removed per-wave. Track in `test_architecture_no_flask_imports.py` allowlist.

### 3.5.4 Async edge cases (from SQLAlchemy async specialist audit)

- [ ] **[L5+]** **AE-1 [CRITICAL, Spike 1]: Product `@property` lazy-load audit.** 5 properties across 3 relationships — see SB-1. Spike 1's `lazy="raise"` blanket must exercise these properties explicitly, not just run integration tests that happen to use eager-loading code paths.
- [ ] **[L5+]** **AE-2 [HIGH, Spike 2]: asyncpg JSONB codec incompatibility.** See LB-1. Add to Spike 2 pass criteria: Pydantic-typed JSONB roundtrip test.
- [ ] **[L5+]** **AE-3 [HIGH, Wave 4b]: `session.merge()` in `delivery.py:274` needs `await`.** Missing `await` returns a coroutine object, not the merged instance.
- [ ] **[L5+]** **AE-4 [MEDIUM, Wave 4b]: `inspect(product)` + lazy load in `product_pricing.py:38-43`.** Line 43 unconditionally accesses `product.pricing_options`. Under async → `MissingGreenlet`. File is deleted per Decision 5, so this is self-resolving.

### 3.5.5 Testing infrastructure additions (6 components, ~2,125 LOC)

- [ ] **TI-1 [Phase -1]: Response fingerprint system (~430 LOC).** Capture Flask response shapes as committed JSON fixtures before Wave 1; compare against FastAPI per-wave. Files: `tests/migration/fingerprint.py`, `tests/migration/conftest_fingerprint.py`, `tests/migration/test_response_fingerprints.py`, `tests/migration/fixtures/fingerprints/*.json`.
- [ ] **TI-2 [L1]: Dual-stack shadow test mode (~255 LOC).** During Waves 1-2 (both stacks coexist), shadow-test safe requests against both Flask and FastAPI, compare responses. Files: `tests/migration/dual_stack_client.py`, `tests/migration/conftest_dual_stack.py`.
- [ ] **[L5+]** **TI-3 [L5+]: Async correctness test harness (~410 LOC).** Concurrent session isolation, MissingGreenlet provocation, event loop blocking detection, connection pool stress. Files: `tests/migration/test_async_correctness.py`, `tests/migration/blocking_detector.py`.
- [ ] **TI-4 [Phase -1]: Structural guard meta-tests (~400 LOC).** Each new guard gets a "known violation" fixture proving it catches errors. Companion coverage test prevents guard rot. File: `tests/unit/test_architecture_guard_meta.py`.
- [ ] **TI-5 [L0 through L7]: Wave checkpoint tests (~300 LOC).** Per-wave invariant gates (route parity, import counts, schema match). File: `tests/migration/test_wave_checkpoints.py`.
- [ ] **TI-6 [L2+]: Production canary system (~330 LOC).** Post-deploy synthetic transactions, health check expansion (`/health/deep`), metric comparison, auto-rollback triggers. Files: `scripts/canary/production_canary.py`, `src/routes/health_deep.py`.

### 3.5.6 Engineering practice additions (from senior eng audit)

- [ ] **EP-1 [L0]: Feature flag for Flask/FastAPI routing toggle (~50 LOC).** `ADCP_USE_FASTAPI_ADMIN=true/false` routes `/admin/*` traffic between stacks. Enables instant rollback without container swaps. Eliminates Wave 2 code freeze. Removed in Wave 3.
- [ ] **EP-2 [L0]: `X-Served-By` response header (~20 LOC).** Middleware adds `X-Served-By: flask` or `X-Served-By: fastapi` during dual-mount phase. Makes "zero Flask traffic" assertion verifiable.
- [ ] **EP-3 [L0]: Shared `form_error_response()` helper.** DRY pattern for form-validation-error re-rendering across 25 router files. Prevents duplication caught by `check_code_duplication.py`.
- [ ] **EP-4 [All waves]: Golden-fixture characterization tests.** Before each router port, capture Flask response shapes as golden fixtures. After port, assert FastAPI matches. TDD adaptation for ports.
- [ ] **EP-5 [All waves]: FIXME comments at source for all new allowlist entries.** Per CLAUDE.md structural guard rules: "Every allowlisted violation has a `FIXME(salesagent-xxxx)` comment at the source location."
- [ ] **EP-6 [Wave 0]: Relationship count corrected to 68.** All doc references to "58 relationships" updated to 68 (verified by grep of `src/core/database/models.py`).
- [ ] **EP-7 [Wave 1b]: Customer communication plan for forced re-login.** Fortune 500 clients need 48-hour advance notice, not just a team announcement.

### 3.5.7 Code pattern corrections (from consistency audit)

- [ ] **CP-1 [Wave 0 design]: Repositories return ORM objects, NOT DTOs.** `list_dtos()` method removed from repository examples. DTO conversion happens in handler layer: `dtos = [AccountDTO.from_orm(a) for a in repo.list_all(...)]`. Corrected in `async-pivot-checkpoint.md` (2026-04-12).
- [ ] **CP-2 [Wave 2]: `request.form.getlist()` → `List[str] = Form()` migration pattern.** Document the FastAPI equivalent for multi-value form fields in foundation-modules worked examples.

---

## Section 3.6 — Allowlist Bootstrap Policy

Every structural guard starts with one of three allowlist strategies:

- **Empty + bootstrap**: no existing violations; guard is green from day 1; any new violation fails CI.
- **Captured baseline → shrink**: pre-existing violations captured as FIXME-commented allowlist; allowlist count monotonically decreases over migration; ratchets to zero by L7.
- **Frozen allowlist**: scope-limited exception (e.g., an architectural carve-out for a specific module or pattern); allowlist entries must stay fixed; net-new additions rejected.

| Guard | Strategy |
|-------|----------|
| `test_no_toolerror_in_impl.py` | Captured baseline → shrink |
| `test_transport_agnostic_impl.py` | Captured baseline → shrink |
| `test_impl_resolved_identity.py` | Empty + bootstrap |
| `test_architecture_schema_inheritance.py` | Captured baseline → shrink |
| `test_architecture_boundary_completeness.py` | Empty + bootstrap |
| `test_architecture_query_type_safety.py` | Captured baseline → shrink |
| `test_architecture_no_model_dump_in_impl.py` | Captured baseline → shrink |
| `test_architecture_repository_pattern.py` | Captured baseline → shrink |
| `test_architecture_migration_completeness.py` | Empty + bootstrap |
| `test_architecture_no_raw_media_package_select.py` | Empty + bootstrap |
| `test_architecture_no_raw_select.py` | Captured baseline → shrink |
| `test_architecture_obligation_coverage.py` | Captured baseline → shrink |
| `test_architecture_bdd_no_pass_steps.py` | Captured baseline → shrink |
| `test_architecture_bdd_no_trivial_assertions.py` | Captured baseline → shrink |
| `test_architecture_bdd_no_dict_registry.py` | Captured baseline → shrink |
| `test_architecture_bdd_no_duplicate_steps.py` | Captured baseline → shrink |
| `test_architecture_bdd_no_silent_env.py` | Empty + bootstrap |
| `test_architecture_obligation_test_quality.py` | Captured baseline → shrink |
| `check_code_duplication.py` (.duplication-baseline) | Captured baseline → shrink |
| `test_architecture_workflow_tenant_isolation.py` | Empty + bootstrap |
| `test_architecture_weak_mock_assertions.py` | Captured baseline → shrink |
| `test_architecture_single_migration_head.py` | Empty + bootstrap |
| `test_architecture_bdd_obligation_sync.py` | Captured baseline → shrink |
| `test_architecture_bdd_no_direct_call_impl.py` | Empty + bootstrap |
| `test_architecture_test_marker_coverage.py` | Captured baseline → shrink |
| **L0+ migration guards** | |
| `test_architecture_handlers_use_sync_def.py` (sunset L5b) | Empty + bootstrap, sunset L5b |
| `test_architecture_no_async_db_access.py` (sunset L5b) | Empty + bootstrap, sunset L5b |
| `test_architecture_adapter_calls_wrapped_in_threadpool.py` (L5d2+) | Empty + bootstrap |
| `test_architecture_admin_route_names_unique.py` (L2) | Empty + bootstrap |
| `test_architecture_admin_routes_named.py` (L2) | Empty + bootstrap |
| `test_templates_url_for_resolves.py` (L2) | Empty + bootstrap |
| `test_templates_no_hardcoded_admin_paths.py` (L2) | Captured baseline → shrink |
| `test_architecture_csrf_exempt_covers_adcp.py` (L1) | Empty + bootstrap |
| `test_architecture_no_runtime_psycopg2.py` (L1) | Captured allowlist (3 sites) → shrink |
| `test_architecture_get_db_connection_callers_allowlist.py` | Captured allowlist (1 file) → shrink |
| `test_architecture_no_sse_handlers.py` (activated L5a; enforced L5d4 per Decision 8) | Empty + bootstrap |
| `test_architecture_no_singleton_session.py` (L4, Decision 7) | Empty + bootstrap |
| `test_architecture_no_flask_caching_imports.py` (L3, Decision 6) | Empty + bootstrap |
| `test_architecture_no_threading_thread_for_db_work.py` (L5d1, D3 2026-04-16) | Empty + bootstrap (supersedes deleted `test_architecture_sync_bridge_scope.py`) |
| `test_architecture_no_module_scope_create_app.py` (L2) | Empty + bootstrap |
| `test_architecture_admin_handlers_async.py` (L5b+) | Captured full allowlist at L5b → monotonic drain through L5c–L5e → empty at L5e exit |
| `test_architecture_middleware_order.py` (L0) | Empty + bootstrap |
| `test_architecture_handlers_use_annotated.py` (L1/L2, §11.22) | Empty + bootstrap |
| `test_architecture_dto_config.py` (L4, §11.23) | Empty + bootstrap |
| `test_architecture_no_direct_os_environ.py` (L4, §11.19) | Captured baseline → shrink |

---

## Section 4 — Per-wave acceptance checklists

Full detail in `flask-to-fastapi-execution-details.md` Part 1.

## Prerequisite refactor PRs (before L0)

Pre-L0: three nested-session refactor PRs must land before B2's `get_db_session()` rewrite to bare `sessionmaker` (otherwise silent lost-update / detached-instance bugs surface in admin handlers under bare sessionmaker).

- [ ] **PRE-1: `src/admin/blueprints/oidc.py:173` — `test_initiate()` refactor.** Inline `get_or_create_auth_config` query against the outer `db_session` (mirror the existing fix at `callback()` line 229). Prevents detached-instance error under bare sessionmaker on `config.oidc_client_secret` getter.
- [ ] **PRE-2: `src/admin/blueprints/operations.py:304` — `approve_media_buy()` refactor.** Extract needed data to dict, close outer session before `execute_approved_media_buy`, open a fresh session for post-adapter status update (mirror `creatives.py:607-639` pattern). Prevents lost-update on `media_buy.status` under bare sessionmaker.
- [ ] **PRE-3: `src/admin/blueprints/workflows.py:158` — `approve_workflow_step()` refactor.** Same extract-dict + close-outer pattern as PRE-2.
- [ ] **PRE-4: `src/core/context_manager.py:26` — `ContextManager(DatabaseManager)` runtime compatibility check.** Add an integration test that exercises ContextManager under the new bare-sessionmaker `DatabaseManager.session` property. If identity-map assumptions break, refactor ContextManager to stateless (scope = Decision 7 / Spike 4.5 at L4).

These refactors are strict improvements under the CURRENT `scoped_session` world (reducing nested-session coupling); they ship independently and land before B2 rewrites `get_db_session()` at L0.

### Wave 0 / L0 — Foundation + template codemod (~2,500 LOC)

> **Knowledge sources for this wave:**
> - `flask-to-fastapi-foundation-modules.md` §11.1-11.15 — all 11 foundation module implementations with code
> - `flask-to-fastapi-migration.md` §11-12 — foundation module descriptions + template codemod details
> - `async-pivot-checkpoint.md` §3 — target async state (code blocks corrected 2026-04-12) **[L5+ reference only]**
> - `async-audit/agent-e-ideal-state-gaps.md` — 14 FastAPI-idiom upgrades (minimum apply: E1/E2/E3/E5/E6/E8)
> - `async-audit/frontend-deep-audit.md` — 7 critical blockers for templates/JS/OAuth
> - `flask-to-fastapi-deep-audit.md` §1 — 6 blockers (Blockers 1,2 fixed in Wave 0)
> - `implementation-checklist.md` §3.5 — 55 verification audit findings scoped per wave

**Entry criteria:**

- [ ] All of Section 1 (pre-migration prerequisites) complete
- [ ] `main` is green (`make quality` + `tox -e integration` + `tox -e bdd`)
- [ ] `src/app.py:299-304` still has `a2wsgi` Flask mount (safety net)
- [ ] Migration overview §§11, 12, 13 signed off
- [ ] `SESSION_SECRET` defined in `.env.example` and staging secret store
- [ ] All Ownership & Bus-Factor Policy roles assigned in `.claude/notes/flask-to-fastapi/CLAUDE.md` — primary lead, backup lead, security reviewer, incident commander. All `[TO BE ASSIGNED]` placeholders replaced with named engineers. Verified by: `rg -n 'TO BE ASSIGNED' .claude/notes/flask-to-fastapi/CLAUDE.md` returns zero matches.
- [ ] `[TBD]` placeholders in §6.5 on-call handoff table replaced with named engineers (or explicitly deferred to later layer entry with a filed follow-up issue). Verified by: `rg -n '\[TBD\]' .claude/notes/flask-to-fastapi/implementation-checklist.md` returns zero matches OR each remaining match has a follow-up issue linked in the L0 PR description.

**Files created — all 11 foundation modules plus supporting infra:**

- [ ] ~~`src/admin/templating.py`~~ **SUPERSEDED (D8 #4 §2.3):** do NOT create `src/admin/templating.py`. `Jinja2Templates(directory="src/admin/templates")` is bound to `app.state.templates` inside `src/app.py::lifespan` with the `_url_for` safe-lookup override and `from_json`/`markdown`/`tojson_safe` filters pre-registered before any `TemplateResponse` call. Handlers consume via `TemplatesDep` from `src/admin/deps/templates.py`. See `foundation-modules.md §D8-native` for the full design.
- [ ] `src/admin/deps/templates.py` (~30 LOC) — `TemplatesDep = Annotated[Jinja2Templates, Depends(get_templates)]` returning `request.app.state.templates`; `BaseCtxDep = Annotated[dict, Depends(get_base_context)]` returning `{messages, support_email, sales_agent_domain, user_email, user_authenticated, user_role, test_mode}` — replaces Flask's `inject_context()` processor; NO `csrf_token` (CSRFOriginMiddleware uses Origin-header validation); NO `tenant` (handlers load on-demand via `CurrentTenantDep` to avoid N+1).
- [ ] ~~`src/admin/flash.py`~~ **SUPERSEDED (D8 #4 §2.2):** do NOT create `src/admin/flash.py`. Message state uses `src/admin/deps/messages.py::MessagesDep` (`Annotated[Messages, Depends(get_messages)]`) with `Messages.info()` / `success()` / `warning()` / `error()` / `drain()` methods backed by `request.session["_messages"]` holding `list[FlashMessage]` (Pydantic-typed). Templates render via `{% for m in messages %}` where `messages` is supplied by `BaseCtxDep.drain()` (called exactly once per request via FastAPI dep-cache).
- [ ] `src/admin/deps/messages.py` (~100 LOC) — `FlashMessage` Pydantic model, `MessageLevel` Enum, `Messages` class, `get_messages(request)` factory, `MessagesDep` Annotated alias. Session-backed to survive Post/Redirect/Get.
- [ ] ~~`src/admin/sessions.py` (~40 LOC)~~ **SUPERSEDED (D8 #4 §2.3):** do NOT create `src/admin/sessions.py`. `SessionMiddleware` is registered inline in `src/app.py::build_middleware_stack()` at L1a via `app.add_middleware(SessionMiddleware, **session_middleware_kwargs())` where `session_middleware_kwargs()` is a helper function in `src/app.py` (or `src/core/settings.py` at L4). Kwargs: `secret_key` from `SESSION_SECRET` (≥32 chars, raises `SessionSecretMissingError` at startup if missing; no `FLASK_SECRET_KEY` dual-read per D6 L2), `session_cookie='adcp_session'`, `max_age=14*24*3600`, `same_site='lax'`, `https_only=True` in production, `path='/'`, `domain='.sales-agent.example.com'` in production non-single-tenant mode. Separate `oauth_transit` cookie for OIDC form_post (§11.6.1) — distinct name, distinct `SameSite=None`, path-scoped to `/admin/auth/oidc/`.
- [ ] `src/admin/oauth.py` (~60 LOC) — Authlib `starlette_client.OAuth` instance, Google client registered, `GOOGLE_CLIENT_NAME = "google"` constant, comment referencing OAuth URI immutability
- [ ] `src/admin/csrf.py` (~120 LOC) — `CSRFOriginMiddleware` (pure-ASGI Origin header validation, NOT Double Submit Cookie), `_EXEMPT_PATH_PREFIXES` includes `/mcp`, `/a2a`, `/api/v1/`, `/_internal/`, `/.well-known/`, `/agent.json`, `/admin/auth/google/callback`, `/admin/auth/oidc/callback`, `/admin/auth/gam/callback`. Zero JS changes, zero template changes.
- [ ] `src/admin/app_factory.py` (~80 LOC) — `build_admin_router()` returns `APIRouter(prefix="/admin", tags=["admin"], include_in_schema=False, redirect_slashes=True)`, empty in Wave 0
- [ ] `src/admin/deps/__init__.py` (2 LOC)
- [ ] `src/admin/deps/auth.py` (~260 LOC) — `CurrentUserDep`, `RequireAdminDep`, `RequireSuperAdminDep` as `Annotated[...]` aliases; **[CORRECTED 2026-04-12]** dep functions are `sync def` with `with get_db_session()` per execution-plan.md Layer 0 (not `async def` with `async with` as originally written during the async pivot)
- [ ] `src/admin/deps/tenant.py` (~90 LOC) — `CurrentTenantDep` filters `tenant.is_active=True` (fixes pre-existing latent bug)
- [ ] `src/admin/deps/audit.py` (~110 LOC) — FastAPI `Depends()`-based audit port (rewritten, not ported one-for-one); cached `AuditLogger` via `request.state`, not `flask.g`
- [ ] `src/admin/middleware/__init__.py` (2 LOC)
- [ ] `src/admin/middleware/external_domain.py` (~90 LOC) — pure-ASGI `ApproximatedExternalDomainMiddleware`, hard-gated on `/admin` path prefix, uses status 307 for redirects
- [ ] `src/admin/middleware/fly_headers.py` (~40 LOC) — pure-ASGI, may become unneeded if uvicorn `--proxy-headers` covers Fly.io (assumption #21)
- [ ] `src/admin/routers/__init__.py` (2 LOC)

**L0 lifespan — threadpool limiter bump (moved from L5+ per plan, 2026-04-14):**

- [ ] `src/app.py::lifespan` configures `anyio.to_thread.current_default_thread_limiter().total_tokens = int(os.environ.get("ADCP_THREADPOOL_TOKENS", "80"))` **before** any request is served. Default 40 is too low for sync-handler admin concurrency (OAuth callback bursts alone can hit ~30). Env var `ADCP_THREADPOOL_TOKENS` is canonical; older `ADCP_THREADPOOL_SIZE` references are deprecated and removed as the plan ratchets. Full code block: `flask-to-fastapi-foundation-modules.md` §11.14.F.

**Template codemod:**

> **Template codemod execution moved to L1a** (all 4 passes break Flask's `url_for` while Flask still serves traffic). L0 creates the codemod script but does NOT run it.

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
- [ ] Codemod runs to exit code 0 against all 74 templates in `/templates/` (per `async-audit/frontend-deep-audit.md` FE-1 inventory)
- [ ] Codemod stdout reports `"74 templates processed, N transformations applied"`
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
- [ ] `tests/unit/test_architecture_no_module_scope_create_app.py` — Wave 3 / Layer 2 guard. AST-scans `tests/` for top-level statements that call `create_app()` (assignment or naked expression). Empty allowlist — activated AFTER the 5-site sweep (`tests/integration/conftest.py:18`, `tests/integration/test_template_url_validation.py:16`, `tests/integration/test_product_deletion.py:15`, plus 2 deleted whole-files) is complete and BEFORE `src/admin/app.py` is deleted. Prevents the pytest-collection cascade where one broken module poisons the entire collection step. Implementation drafted in `flask-to-fastapi-foundation-modules.md`.
- [ ] `tests/unit/test_architecture_handlers_use_sync_def.py` — v2.0 sync invariant guard (async pivot reversed 2026-04-12). Asserts every admin router handler is sync `def`, NOT `async def`. Replaces the wrong-direction `test_architecture_admin_handlers_async.py`.
- [ ] `tests/unit/test_architecture_no_async_db_access.py` — v2.0 sync sibling guard. Asserts admin DB access uses sync `with get_db_session()`, NOT `async with` or `AsyncSession`.
- [ ] `tests/unit/test_architecture_handlers_use_annotated_depends.py` — Agent E idiom upgrade. AST-scans `src/admin/routers/*.py`; every `@router.<method>(...)` handler parameter must use `Annotated[T, ...]` form, not `x = Query(...)` default-value syntax.
- [ ] `tests/unit/test_architecture_templates_receive_dtos_not_orm.py` — Agent E idiom upgrade. Asserts every `render(request, "tpl", context)` call passes only primitives, Pydantic BaseModel instances, or the request object — never ORM model instances. Prevents lazy-load Risk #1 realization.
- [ ] `tests/unit/test_architecture_no_sync_session_usage.py` is **[L5+]** only (supersedes prior "Wave 0 Agent E idiom upgrade" plan — see async-pivot-checkpoint.md for history). v2.0 L0-L4 USES sync `Session` and `sessionmaker` — this guard would fail against the intended L0-L4 state.
- [ ] `tests/unit/test_architecture_no_module_level_engine.py` — Agent E idiom upgrade. Asserts no `create_async_engine` or `create_engine` at module scope — must be inside a function (lifespan factory). Prevents pytest-asyncio event-loop leak (Risk Interaction B).
- [ ] `tests/unit/test_architecture_no_direct_env_access.py` — Agent E idiom upgrade. Asserts no `os.environ.get` or `os.environ[` in `src/admin/` or `src/core/` except `src/core/config.py` (per H.5 / §11.0.2 — extend existing, do NOT create a new `src/core/settings.py`). `get_config()` from `src/core/config.py` is the only file that reads env directly via pydantic-settings.
- [ ] `tests/unit/test_architecture_uses_structlog.py` — Agent E idiom upgrade. Asserts new `src/admin/` and `src/core/` files use `from src.core.logging import get_logger`, not `logging.getLogger(`. Allowlisted for existing files during migration.
- [ ] `tests/unit/test_architecture_repository_eager_loads.py` — Agent E idiom upgrade. Asserts every repository method whose DTO has nested-attribute returns has an `.options(selectinload(...))` call matching the nested relationships.
- [ ] `tests/unit/test_architecture_middleware_order.py` — Agent E idiom upgrade. Asserts the exact middleware registration order in `src/app.py` matches the documented runtime order (e.g., Approximated BEFORE CSRF per Blocker 5). Prevents reshuffling.
- [ ] `tests/unit/test_architecture_tests_use_async_client.py` is **[L5+]** only (supersedes prior "Wave 0 Agent E idiom upgrade" plan — see async-pivot-checkpoint.md for history). v2.0 L0-L4 uses Starlette `TestClient` (sync) for admin tests.
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

**L0 derivative items (2026-04-15 sharpening):**

- [ ] `.pre-commit-hooks/check_hardcoded_urls.py` is rewritten to reject Jinja `scriptRoot`/`script_root`/`script_name`/`admin_prefix`/`static_prefix` references and accept only `url_for()` output. Verified by: hook runs on a fixture template containing `scriptRoot` and exits non-zero; runs on a fixture template containing only `url_for()` calls and exits zero; hook is listed in `.pre-commit-config.yaml` and fires in `pre-commit run --all-files`.
- [ ] Static JS URL strategy is decided and recorded in `docs/deployment/static-js-urls.md`. The file selects exactly one of: (a) inline JS into templates, (b) `window.AppURLs = { ... }` global set by `base.html`, (c) `data-*` attributes per call site; and applies that strategy to all 37 call sites across `static/js/tenant_settings.js` (30), `static/js/targeting_widget.js` (5), `static/js/format-template-picker.js` (2). Verified by: `docs/deployment/static-js-urls.md` exists; `rg -n 'scriptRoot|script_root' static/js/` returns zero.
- [ ] `.duplication-baseline` is temporarily relaxed at L0 entry to accommodate L0-L2 parallel old/new code, and re-tightened at L2 exit. Verified by: `check_code_duplication.py` green at every L0-L2 commit; L2 exit diff restores the baseline to ≤ L0-entry pre-relaxation value.
- [ ] `tests/unit/architecture/test_architecture_no_flask_imports.py` is authored at L0 with a FIXME-seeded allowlist (~40 files expected). Allowlist is tracked in `tests/unit/architecture/allowlists/no_flask_imports.txt` and shrinks to empty by L2 exit. Verified by: guard is present and green at L0; allowlist file exists with entries annotated `# FIXME(salesagent-xxxx)`; allowlist is empty at L2 exit.
- [ ] BDD feature files and step definitions are swept for Flask references (bare `flask` imports, `url_for` with blueprint dot-syntax, `script_root`). Verified by: `rg -n 'from flask|import flask|script_root|url_for\([^)]*\.' tests/bdd/` returns zero unexpected hits at L0 exit.

**Harness extension:**

- [ ] `tests/harness/_base.py::IntegrationEnv.get_admin_client()` exists, added as sibling to `get_rest_client()` near line 914
- [ ] `get_admin_client()` snapshots `app.dependency_overrides` on `__enter__` and restores on `__exit__` (prevents test leakage per §3.3 deep audit)
- [ ] Smoke test: `python -c "from tests.harness import IntegrationEnv; ..."` succeeds (TestClient construction does not error against empty router)

**Blockers fixed in Wave 0:**

- [ ] Blocker 1 (script_root) — via codemod + `render()` wrapper + guard test
- [ ] Blocker 2 (trailing slash) — via `APIRouter(redirect_slashes=True)` default in `build_admin_router()`
- [ ] Blocker 4 (async session interleaving) — L0-L4 use sync `def` handlers. L0 adds `test_architecture_handlers_use_sync_def.py` (supersedes prior "full async SQLAlchemy pivot Option A + `test_architecture_admin_handlers_async.py`" plan — see async-pivot-checkpoint.md for history). Blocker 4 async session interleaving is a L5+ concern within v2.0.

**What Wave 0 does NOT do (preserves mergeability):**

- [ ] `pyproject.toml` adds `pydantic-settings>=2.7.0` as an explicit dep (per execution-plan.md Layer 0). `itsdangerous` is NOT explicitly pinned — it stays a transitive dep of `SessionMiddleware` (Origin-based CSRF does not use it).
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
- [ ] **L0 native-ness guard:** `tests/unit/test_architecture_no_pydantic_v1_config.py` landed per §11.35 with EMPTY allowlist (codebase has 0 `class Config:` blocks today); guard is monotonic from day 1.
- [ ] Branch mergeable state verified
- [ ] Single squashed merge commit on `main`

### Wave 1 / L1a-L1b — Foundational routers + session cutover (~4,000 LOC)

> **Knowledge sources for this wave:**
> - `flask-to-fastapi-worked-examples.md` §4.1-4.3 — OAuth login/logout + favicon upload worked examples
> - `flask-to-fastapi-deep-audit.md` §1 — Blockers 3,5,6 fixed in Wave 1
> - `async-audit/frontend-deep-audit.md` §3 — OAuth + session + auth flow audit
> - `flask-to-fastapi-foundation-modules.md` §11.4 — deps/auth.py implementation
> - `flask_migration_critical_knowledge.md` items 2,4,5,6,17 — OIDC path, 307 default, CSRF unenforced, tojson, SameSite (file is in auto-memory, not in project tree)

**Entry criteria:**

- [ ] Wave 0 merged to `main`
- [ ] `SESSION_SECRET` live in staging secret store
- [ ] Playwright smoke run against the empty admin router confirms `get_admin_client()` infra is sound
- [ ] Authlib starlette_client happy-path spike completed on staging (assumption #8 verification from execution-details Part 2)
- [ ] **L1a single-PR requirement:** `SessionMiddleware` registration in `src/app.py` MUST co-commit with the port of `get_session_cookie_domain()` (today at `src/admin/app.py` and `src/core/domain_config.py`). If `SessionMiddleware(domain=None)` lands without the domain-calculation helper, every tenant subdomain gets its own isolated session cookie and SSO breaks across subdomains the moment Flask's cookie-domain calculation goes away. L1a PR is REJECTED at review if `grep -n 'session_cookie_domain' src/` does not return a hit in the same PR diff as `app.add_middleware(SessionMiddleware, ...)`.

**Routers ported:**

- [ ] `src/admin/routers/public.py` (~400 LOC) — signup, landing, no-auth pages
- [ ] `src/admin/routers/core.py` (~600 LOC) — `/`, `/health`, dashboard
- [ ] `src/admin/routers/auth.py` (~1,100 LOC) — Google OAuth login flow via Authlib `starlette_client`
- [ ] `src/admin/routers/oidc.py` (~500 LOC) — per-tenant OIDC dynamic client registration

**Middleware stack wired in `src/app.py` — canonical L1a runtime order (outermost → innermost, 7 middlewares):**

- [ ] 1. `FlyHeadersMiddleware` (new, outermost — header normalization)
- [ ] 2. `ApproximatedExternalDomainMiddleware` (new, BEFORE CSRF per Blocker 5)
- [ ] 3. `UnifiedAuthMiddleware` (already present — headers-only, no session dependency)
- [ ] 4. `SessionMiddleware` (new, kwargs built inline via `session_middleware_kwargs()` helper in `src/app.py::build_middleware_stack()` — D8 #4 §2.3; no `src/admin/sessions.py` module exists)
- [ ] 5. `CSRFOriginMiddleware` (new)
- [ ] 6. `RestCompatMiddleware` (already present)
- [ ] 7. `CORSMiddleware` (already present, innermost)
- [ ] Registration in `src/app.py` is in **REVERSE** order (LIFO — innermost added first): `CORS`, `RestCompat`, `CSRF`, `Session`, `UnifiedAuth`, `ApproximatedExternalDomain`, `FlyHeaders`
- [ ] `tests/integration/test_middleware_ordering.py` exists and is green — inspects `app.user_middleware` and asserts the sequence matches canonical order
- [ ] Note: `LegacyAdminRedirectMiddleware` lands at L1c (8-middleware stack, per D1 2026-04-16). `TrustedHostMiddleware` AND `SecurityHeadersMiddleware` land at L2 (10-middleware stack). `RequestIDMiddleware` lands at L4 (11-middleware stack). See `foundation-modules.md` §11.36 for the versioned `MIDDLEWARE_STACK_VERSION` assertion guard covering L1a=1 (7) → L1c=2 (8) → L2=3 (10) → L4+=4 (11).

**Middleware stack — canonical L1c runtime order (outermost → innermost, 8 middlewares, adds LegacyAdminRedirect INSIDE UnifiedAuth):**

> # WARNING: add_middleware calls in src/app.py are REVERSE of this runtime order (LIFO).

- [ ] 1. `FlyHeadersMiddleware`
- [ ] 2. `ApproximatedExternalDomainMiddleware`
- [ ] 3. `UnifiedAuthMiddleware`
- [ ] 4. `LegacyAdminRedirectMiddleware` (new at L1c per D1 — INSIDE UnifiedAuth so `request.state.identity.tenant_id` is populated; OUTSIDE SessionMiddleware so session is hydrated)
- [ ] 5. `SessionMiddleware`
- [ ] 6. `CSRFOriginMiddleware`
- [ ] 7. `RestCompatMiddleware`
- [ ] 8. `CORSMiddleware`
- [ ] Registration in `src/app.py` LIFO: `CORS`, `RestCompat`, `CSRF`, `Session`, `LegacyAdminRedirect`, `UnifiedAuth`, `ApproximatedExternalDomain`, `FlyHeaders`
- [ ] `src/app_constants.py::MIDDLEWARE_STACK_VERSION` bumped from 1 to 2 in the same PR; `tests/integration/test_architecture_middleware_order.py` asserts 8-middleware L1c sequence per §11.36

**Middleware stack — canonical L2 runtime order (outermost → innermost, 10 middlewares, adds TrustedHost + SecurityHeaders):**

> # WARNING: add_middleware calls in src/app.py are REVERSE of this runtime order (LIFO).

- [ ] 1. `FlyHeadersMiddleware`
- [ ] 2. `ApproximatedExternalDomainMiddleware`
- [ ] 3. `TrustedHostMiddleware` (new at L2 — reject non-allowed hosts before security-header injection)
- [ ] 4. `SecurityHeadersMiddleware` (new at L2 per §11.28 — inside TrustedHost, outside UnifiedAuth)
- [ ] 5. `UnifiedAuthMiddleware`
- [ ] 6. `LegacyAdminRedirectMiddleware`
- [ ] 7. `SessionMiddleware`
- [ ] 8. `CSRFOriginMiddleware`
- [ ] 9. `RestCompatMiddleware`
- [ ] 10. `CORSMiddleware`
- [ ] Registration in `src/app.py` is **REVERSE** (LIFO — innermost added first): `CORS`, `RestCompat`, `CSRF`, `Session`, `LegacyAdminRedirect`, `UnifiedAuth`, `SecurityHeaders`, `TrustedHost`, `ApproximatedExternalDomain`, `FlyHeaders`
- [ ] `src/app_constants.py::MIDDLEWARE_STACK_VERSION` bumped from 2 to 3 in the same PR; `tests/integration/test_architecture_middleware_order.py` asserts 10-middleware L2 sequence per §11.36

**Middleware stack — canonical L4+ runtime order (outermost → innermost, 11 middlewares, adds RequestID outermost):**

> # WARNING: add_middleware calls in src/app.py are REVERSE of this runtime order (LIFO). Each add_middleware line below is annotated with its runtime position.

- [ ] 1. `RequestIDMiddleware` (new at L4 — outermost for structured-log correlation; depends on L4 structlog wiring)
- [ ] 2. `FlyHeadersMiddleware`
- [ ] 3. `ApproximatedExternalDomainMiddleware`
- [ ] 4. `TrustedHostMiddleware`
- [ ] 5. `SecurityHeadersMiddleware`
- [ ] 6. `UnifiedAuthMiddleware`
- [ ] 7. `LegacyAdminRedirectMiddleware`
- [ ] 8. `SessionMiddleware`
- [ ] 9. `CSRFOriginMiddleware`
- [ ] 10. `RestCompatMiddleware`
- [ ] 11. `CORSMiddleware`
- [ ] Registration LIFO: `CORS`, `RestCompat`, `CSRF`, `Session`, `LegacyAdminRedirect`, `UnifiedAuth`, `SecurityHeaders`, `TrustedHost`, `ApproximatedExternalDomain`, `FlyHeaders`, `RequestID`
- [ ] `src/app_constants.py::MIDDLEWARE_STACK_VERSION` bumped from 3 to 4 in the same PR; `tests/integration/test_architecture_middleware_order.py` asserts 11-middleware L4+ sequence per §11.36

**Blockers fixed in Wave 1:**

- [ ] Blocker 3 (AdCPError HTML regression) — handler Accept-aware, `error.html` template exists, `test_admin_error_page.py` green
- [ ] Blocker 5 (middleware order) — swap applied, redirect is 307, `test_external_domain_post_redirects_before_csrf.py` green
- [ ] Blocker 6 (OAuth URI immutability) — guard test green AND a manual staging OAuth smoke test walked end-to-end against real Google with both OIDC tenants

**L1b — Staging OAuth callback URI pre-registration** (per-deployment runbook, not migration-plan work):

- [ ] For each deployment environment (staging, prod, dev), ensure these byte-immutable callback paths are registered in Google Cloud Console AND per-tenant OIDC providers BEFORE L1b smoke-tests:
  - `https://<hostname>/admin/auth/google/callback`
  - `https://<hostname>/admin/auth/oidc/callback`
  - `https://<hostname>/admin/auth/gam/callback`
- [ ] Hostnames are deployment-specific. Missing registrations cause smoke-test failures unrelated to the migration code. This step belongs in the per-environment deploy runbook, NOT in the migration PR itself.
- [ ] **L1b pre-cutover gate:** `scripts/verify_oauth_redirect_uris.py` (~40 LOC) exists and runs green in the deploy pipeline. Uses the Google Cloud Console API (`googleapiclient.discovery.build("oauth2", ...)`) to list registered redirect URIs for the project's OAuth client; asserts the set contains exactly `{"/admin/auth/google/callback", "/admin/auth/oidc/callback", "/admin/auth/gam/callback"}` prefixed by each deploy hostname. Script exits non-zero if any expected URI is missing or any extra URI exists. Called from `fly.toml` deploy hook: `[deploy] release_command = "python scripts/verify_oauth_redirect_uris.py"`. Non-zero exit blocks the deploy. Catches drift class where code-side guard passes but Google Cloud Console registration has diverged (redirect_uri_mismatch in production with no pre-deploy signal).

**L1b derivative items (2026-04-15 sharpening):**

- [ ] OAuth kwargs audit: every kwarg passed to `oauth.register(...)` in the legacy Flask integration (`src/admin/blueprints/auth.py`, `src/admin/blueprints/oidc.py`) is enumerated in `docs/migration/oauth-kwargs-inventory.md`, and each kwarg is verified to land identically in the Starlette `authlib.integrations.starlette_client.OAuth` wrapper. At minimum the following kwargs are enumerated: `client_kwargs.scope`, `server_metadata_url`, `access_type`, `prompt`, `hd`, `authorize_params`. Verified by: `docs/migration/oauth-kwargs-inventory.md` exists; a byte-for-byte diff of legacy-vs-new `register(...)` calls is recorded in the doc; `tests/integration/test_oauth_kwarg_parity.py` asserts the live registered client configuration matches the documented inventory.
- [ ] Mandatory security-reviewer sign-off on L1b PR (the OAuth cutover touches `src/admin/csrf.py`, `src/admin/sessions.py`, `src/admin/oauth.py`). Verified by: the L1b GitHub PR has an approving review from the named security-reviewer role per CLAUDE.md §Ownership; reviewer name and date are recorded in `docs/development/migration-handoffs.md`.

**Foundation runtime verifications:**

- [ ] `GET /admin/login` serves from FastAPI, not Flask (curl + integration test)
- [ ] `POST /admin/auth/callback` completes a full redirect chain ending at `/admin/` with a fresh `adcp_session` cookie set by `SessionMiddleware`
- [ ] `GET /admin/health` serves from FastAPI; old Flask `/admin/health` commented out
- [ ] CSRF Origin check: `POST /admin/auth/logout` with session cookie AND `Origin: https://evil.example.com` → 403; same POST with `Origin: https://admin.sales-agent.example.com` → 303. (No `csrf_token` form field; no `X-CSRF-Token` header; no `adcp_csrf` cookie. `itsdangerous` is NOT used for CSRF — it remains a transitive dep of `SessionMiddleware` only.)
- [ ] `Origin: null` on POST `/admin/*` → 403 (opaque-origin rejection)
- [ ] POST `/admin/*` with no Origin AND no Referer passes (SameSite=Lax on session cookie is the defense); documented in §11.7 E. gotchas
- [ ] Session cookie cutover announcement sent to users before deploy
- [ ] Stale `session=...` cookie returns fresh login page (not an error); Playwright `login_with_stale_flask_cookie` test green
- [ ] `test_templates_url_for_resolves.py` runs in `--strict` mode — every `url_for("name")` in templates referenced by Wave 1 routers resolves to an actual registered endpoint

**L1a derivative items (2026-04-15 sharpening):**

- [ ] Session cookie cutover: Starlette `SessionMiddleware` writes and reads `adcp_session` only. Verified by: `tests/integration/test_session_cookie_cutover.py` green — request carrying only legacy `session=...` receives an unauthenticated response (302 to login, not 500); request carrying only `adcp_session=...` is authenticated; `Set-Cookie: session=; Max-Age=0; Domain=<cookie-domain>; Path=/` is emitted on the login-redirect response to clear the stale legacy cookie from the browser.
- [ ] Customer-communication plan for forced re-login (mandatory at L1a deploy) is documented in `docs/migration/v2.0-customer-comms.md`. Verified by: file exists; product-owner sign-off block recorded inside the file with date and role.
- [ ] Feature-flag `ADCP_USE_FASTAPI_ADMIN`: name, default value (`false`), flip-commit SHA, and deletion-layer (L2 exit) are documented IN THIS CHECKLIST and in `src/core/config.py`. Verified by: `rg -n 'ADCP_USE_FASTAPI_ADMIN' src/core/config.py` returns ≥1 hit at L0 landing; flag deletion commit is part of the L2 PR diff.

**Architecture guards update:**

- [ ] `test_architecture_no_flask_imports.py` allowlist shrunk — `public.py/core.py/auth.py/oidc.py` removed (forbid re-introducing Flask in migrated files)

**Dependency changes:**

- [ ] `pyproject.toml` adds `pydantic-settings>=2.7.0`. `itsdangerous` is NOT explicitly pinned; it remains a transitive dep of `SessionMiddleware` (Origin-based CSRF does not use it). `sse-starlette` is NOT added — Decision 8 deletes the SSE route.

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

### Wave 2 / L1c-L1d — Bulk blueprint migration (~9,000 LOC)

> **Knowledge sources for this wave:**
> - `flask-to-fastapi-worked-examples.md` §4.4-4.5 — products + GAM adapter worked examples
> - `flask-to-fastapi-migration.md` §3 — current-state Flask inventory (route counts per blueprint)
> - `flask-to-fastapi-adcp-safety.md` §1-7 — AdCP boundary classification (Category 1 vs 2)
> - `async-audit/frontend-deep-audit.md` §1-2 — Jinja templates + JS fetch audit
> - `flask_migration_critical_knowledge.md` items 11,12,16 — GAM 8 direct routes, Flask imports outside admin, getlist (file is in auto-memory, not in project tree)

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

**Tenant-scoped admin routes use the canonical `/tenant/{tenant_id}/*` single mount + `/admin/*` 308 redirect (D1, 2026-04-16):**

- [ ] **L1c — Single canonical tenant-prefix mount (D1 breaking):** Each of the 14 feature routers (`accounts, products, principals, users, tenants, gam, inventory, inventory_profiles, creatives, creative_agents, operations, policy, settings, workflows`) is registered ONCE via `include_router(router, prefix="/tenant/{tenant_id}")`. `/admin/<feature>/<rest>` requests receive a 308 redirect to the canonical tenant-prefix URL via `LegacyAdminRedirectMiddleware` (uses `request.state.identity.tenant_id`). No dual-mount, no route-name collision. Enforced by `tests/unit/admin/test_architecture_admin_routes_single_mount.py` + `tests/integration/test_admin_legacy_redirect.py`.
- [ ] **Prerequisite — before L2 deletes the Flask catch-all.** Flask currently serves the feature routers at `/tenant/<tenant_id>/*` via the catch-all `app.mount("/", admin_wsgi)` at `src/app.py:44-45`. The canonical FastAPI mount at `/tenant/{tenant_id}` replaces it; `/admin/<feature>/*` bookmarks keep working via the L1c `LegacyAdminRedirectMiddleware` 308 redirect.
- [ ] **Verification:** `rg -l "register_blueprint.*url_prefix.*tenant" src/admin/app.py` enumerates the 14 blueprints for reference; each corresponds to one FastAPI router `include_router()`-ed once at `/tenant/{tenant_id}`.
- [ ] **Smoke test** `tests/integration/test_tenant_subdomain_routing.py` written BEFORE L2 merges (test-first discipline):
  - [ ] Request to `/tenant/default/dashboard` returns 200 (admin-authed)
  - [ ] Request to `/tenant/default/products` returns 200
  - [ ] Request to `/tenant/default/creatives` returns 200
  - [ ] Request to `/tenant/default/users` returns 200
  - [ ] Request to `/tenant/default/settings` returns 200
  - [ ] Verify post-L2 that no request reaches a deleted Flask catch-all.

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

- [ ] 24 blueprint files under `src/admin/blueprints/` (26 total minus `__init__.py` minus `activity_stream.py`)
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

### Wave 3 / L2 — Cache migration + Flask cleanup cutover (~2,500 LOC)

> **Knowledge sources for this wave:**
> - `flask-to-fastapi-foundation-modules.md` §11.15 — SimpleAppCache implementation (Decision 6)
> - `flask-to-fastapi-execution-details.md` §Wave 3 — rollback procedure + proxy-header smoke tests
> - `flask-to-fastapi-migration.md` §15 — dependency changes
> - `flask_migration_critical_knowledge.md` items 7,10 — psycopg2 retained, SSE orphan (file is in auto-memory, not in project tree)

Decision 8 eliminated the SSE port; the `/tenant/{id}/events` route and `sse_starlette` dependency are deleted in Wave 4 rather than ported in Wave 3. Wave 3 focuses on cache migration (Decision 6 SimpleAppCache) and Flask removal.

**Entry criteria:**

- [ ] Wave 2 / L1c-L1d merged to `main` and stable in staging ≥ 5 business days (canonical `/tenant/{tenant_id}/*` single-mount delivery for the 14 tenant-scoped routers was L1c work, with `LegacyAdminRedirectMiddleware` landing in the same PR per D1; L2 only verifies, does not own it). Verified by: `tests/integration/test_tenant_subdomain_routing.py` green in L1d CI; `tests/integration/test_admin_legacy_redirect.py` green in L1c CI; all 14 feature routers from §Wave 2 enumeration have matching `include_router(..., prefix="/tenant/{tenant_id}")` registrations (once each, not twice).
- [ ] Flask catch-all receives zero traffic in staging for 48h
- [ ] Datadog/dashboard audit confirms no external consumer references Flask-era endpoints
- [ ] `v1.99.0` git tag created and container image archived in registry (rollback fallback)

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

**Module-scope `create_app()` sweep (PREREQUISITE for `src/admin/app.py` deletion):**

- [ ] **Sweep all 5 module-scope `create_app()` call sites** — each breaks pytest collection the moment `src/admin/app.py` is deleted. Pytest collection is all-or-nothing: a single broken module poisons the entire collection step, and every test in the suite is reported as collection-error rather than just the offending file. Fix BEFORE the Flask deletion commit lands.
  - [ ] `tests/integration/conftest.py:18` — delete module-scope `admin_app = create_app()` assignment; fixture at `:644-646` already exists as the canonical pattern
  - [ ] `tests/integration/test_template_url_validation.py:16` — convert module-scope `admin_app = create_app()` to `@pytest.fixture(scope="module")` with lazy import
  - [ ] `tests/integration/test_product_deletion.py:15` — convert module-scope `app = create_app()` to fixture
  - [ ] `tests/admin/test_accounts_blueprint.py:17` — covered by whole-file deletion (see Wave 2 test-deletion list above)
  - [ ] `tests/admin/test_product_creation_integration.py:8` — covered by whole-file deletion (see Wave 2 test-deletion list above)
  - [ ] **Verification gate:** `rg -n '^[a-zA-Z_][a-zA-Z0-9_]* = create_app' tests/` returns zero matches
  - [ ] **Verification gate:** `rg -n '^from src\.admin\.app import' tests/` — every remaining match is inside a function/fixture body
  - [ ] **Structural guard activated:** `tests/unit/test_architecture_no_module_scope_create_app.py` runs in `make quality` with empty allowlist (see `flask-to-fastapi-foundation-modules.md` for guard implementation)

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

**L2 hardening modules (land in same PR as Flask removal):**

- [ ] `src/admin/middleware/security_headers.py` (~70 LOC) — `SecurityHeadersMiddleware` per §11.28. Position: INSIDE `TrustedHost`, OUTSIDE `UnifiedAuth`. Default CSP/HSTS/X-Frame/X-Content-Type-Options/Referrer-Policy/Permissions-Policy.
- [ ] `tests/unit/admin/test_security_headers.py` — 4 tests: `test_security_headers_on_200`, `test_security_headers_on_csrf_rejection`, `test_security_headers_on_500`, `test_hsts_disabled_when_https_only_false`.
- [ ] `tests/unit/architecture/test_architecture_security_headers_middleware_present.py` — structural guard (draft TODO) asserting SecurityHeaders registration position between UnifiedAuth and TrustedHost; empty allowlist. Add row to §5.5 guard table.
- [ ] `settings.https_only` boolean added to pydantic-settings — env var `ADCP_HTTPS_ONLY` (default `true` in prod, override `false` in staging with non-public domain).
- [ ] `src/routes/health.py` (5 endpoints + 2 byte-identical aliases) per §11.31 (REVISED):
    - [ ] `/healthz` (liveness, no DB, always 200)
    - [ ] `/readyz` (readiness: SELECT 1 + alembic head + scheduler tick; 503 on failure)
    - [ ] `/health/db` (diagnostic; at L2 reads module-level `get_engine()`, rewires to `request.app.state.db_engine` at L4)
    - [ ] `/health/pool` (diagnostic; anyio threadpool stats)
    - [ ] `/health` KEPT as direct 200 alias with byte-identical body `{"status": "healthy", "service": "mcp"}` — NOT 308. External uptime monitors reference `/health`; naive probes don't follow redirects.
    - [ ] `/admin/health` KEPT as direct 200 alias with same body — NOT 308. Avoids naive-probe breakage.
- [ ] `tests/integration/routes/test_health_split.py` — 4 tests per §11.31.E including `test_healthz_never_touches_db`.
- [ ] `tests/unit/architecture/test_architecture_health_endpoints_split.py` — structural guard per §11.31.F (empty allowlist).
- [ ] Update `fly.toml` http_checks to target `/readyz` instead of `/admin/health`.
- [ ] Update k8s manifests (if any) livenessProbe → `/healthz`, readinessProbe → `/readyz`.
- [ ] `src/admin/rate_limits.py` (~60 LOC) — SlowAPI limiter per §11.32. Memory backend for v2.0; Redis at v2.1. `_key_func` prefers auth token over IP.
- [ ] `slowapi>=0.1.9` added to `pyproject.toml` dependencies.
- [ ] `@limiter.limit(...)` decorators added to `POST /admin/login` (5/min), OAuth init endpoints (20/min), MCP mount (100/min per token).
- [ ] `tests/integration/admin/test_rate_limits.py` — `test_login_rate_limited_after_5_attempts`, `test_rate_limit_by_token_not_ip`.
- [ ] `tests/integration/test_session_cookie_size.py` — cookie-size budget guard per §11.33 with `MAX_COOKIE_BYTES = 3_584`. Two tests: `test_session_cookie_minimal_user`, `test_session_cookie_heavy_user`.
- [ ] `heavy_tenant_session_client` fixture added to `tests/integration/conftest.py` per §11.33.D.
- [ ] Add `Session cookie size budget | tests/integration/test_session_cookie_size.py` row to root `CLAUDE.md` structural-guards table.
- [ ] Cross-ref root `CLAUDE.md` Critical Invariant #2 updated to reference `MIDDLEWARE_STACK_VERSION` (foundation-modules §11.36): L1a=7 → L1c=8 (LegacyAdminRedirect per D1) → L2=10 (TrustedHost + SecurityHeaders) → L4+=11 (RequestID outermost). Note `SecurityHeadersMiddleware` position (INSIDE `TrustedHost`, OUTSIDE `UnifiedAuth`).

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
  - `FLASK_SECRET_KEY` → `SESSION_SECRET` (hard-removed at L2; dev onboarding reads `SESSION_SECRET` only)
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
- [ ] Production smoke test plan drafted: deploy → login → create tenant → create product → submit creative → JSON poll `/activity` returns recent events (Decision 8 deleted SSE) → logout

**Proxy-header smoke tests (Wave 3 pre-deploy — CRITICAL):**

The Flask removal also removes Flask's internal WSGI proxy-header stack (`CustomProxyFix`, `FlyHeadersMiddleware`, werkzeug `ProxyFix` at `src/admin/app.py:187-194`). These rewrote `Fly-Forwarded-Proto` → `X-Forwarded-Proto` and handled `X-Script-Name` for reverse-proxy deployments. Their replacement is `uvicorn --proxy-headers --forwarded-allow-ips='*'` (deep audit §R4 / §2.5). If this replacement fails, `request.url.scheme` returns `http` instead of `https` in production, which breaks OAuth by producing `redirect_uri=http%3A%2F%2F...` that Google Cloud Console rejects with `redirect_uri_mismatch` → **login is dead in production.**

- [ ] `scripts/run_server.py` launches uvicorn with `--proxy-headers --forwarded-allow-ips='*'` (verified in the file)
- [ ] Staging deploy of Wave 3 build
- [ ] `curl -sI https://staging-tenant.scope3.com/admin/login` — verify `Location:` header (if redirect) contains `https://`, not `http://`
- [ ] `curl -sI 'https://staging-tenant.scope3.com/admin/auth/google/initiate'` — verify the OAuth-initiation response's `Location:` header contains `redirect_uri=https%3A%2F%2F...` (URL-encoded `https://`). If it contains `redirect_uri=http%3A%2F%2F...`, `--proxy-headers` is not reading `X-Forwarded-Proto` correctly and OAuth will fail with `redirect_uri_mismatch`. **STOP deployment and fix before proceeding.**
- [ ] Manual browser test: click "Log in" on staging, verify the browser arrives at a real Google OAuth consent page (not an error page). Complete the OAuth flow; verify the callback lands on `https://staging-tenant.scope3.com/admin/...` and the admin UI loads.
- [ ] If `FlyHeadersMiddleware` was kept (assumption #21 deferred): verify Fly-specific header rewriting still works by checking `scope["headers"]` logs in staging for requests carrying `Apx-Incoming-Host`.

**L2 derivative items (2026-04-15 sharpening):**

- [ ] `FLASK_ENV: development` is removed from `docker-compose.yml:93`. Verified by: `rg -n -w 'FLASK_ENV' docker-compose.yml docker-compose.multi-tenant.yml docker-compose.e2e.yml` returns zero matches.
- [ ] `flask-socketio` is removed from `pyproject.toml:31` after verifying no direct or transitive usage. Verified by: `rg -n -w 'flask_socketio|flask-socketio|socketio' src/ tests/` returns zero matches.
- [ ] `a2wsgi` is removed from `pyproject.toml:9`; the mount lines at `src/app.py:33` and `:299-304` are deleted. Verified by: `rg -n -w 'a2wsgi|WSGIMiddleware' src/` returns zero matches.
- [ ] `werkzeug` is removed from `pyproject.toml:50`. Verified by: `rg -n -w 'werkzeug' src/ tests/` returns zero matches.
- [ ] `types-waitress` is removed from `.pre-commit-config.yaml` mypy `additional_dependencies`. Verified by: `rg -n -w 'types-waitress' .pre-commit-config.yaml` returns zero matches.
- [ ] Ruff overrides for Flask are removed: `pyproject.toml:169` E402 Flask comment and `:191` `"admin_ui.py" = ["E402", "E722"]` entry are both deleted. Verified by: `rg -n -w 'admin_ui.py|FLASK' pyproject.toml` returns zero matches.
- [ ] `mypy.ini:71 [mypy-flask.*]` stanza is removed. Verified by: `rg -n '\[mypy-flask' mypy.ini` returns zero matches.
- [ ] `test_architecture_no_raw_select.py` allowlist entries for `src/admin/app.py::create_app` and `inject_context` are removed IN THE SAME COMMIT as the deletion of `src/admin/app.py`. Verified by: the pre-L2-PR allowlist file contains the two entries; the post-L2-PR allowlist file does not; both deletions share the same commit SHA (`git log -1 --name-only <sha>` shows both files modified together).
- [ ] `uvicorn --proxy-headers` is confirmed enabled in `scripts/run_server.py` (or the equivalent entrypoint). Verified by: `rg -n 'proxy_headers|--proxy-headers' scripts/ src/` returns ≥1 hit in the uvicorn invocation.
- [ ] `request.remote_addr` → `request.client.host` + X-Forwarded-For audit is complete. At minimum `src/admin/blueprints/core.py:355` (the audit-log capture site) is migrated. `FlyHeadersMiddleware` unpacks X-Forwarded-For so handlers see the end-user IP. Verified by: `tests/integration/test_fly_headers_client_ip.py` green (POST with `X-Forwarded-For: 1.2.3.4` is logged with `client_ip=1.2.3.4`, not the proxy's IP).
- [ ] `TrustedHostMiddleware` allow-list matches `nginx-multi-tenant.conf` server_name patterns; wildcards cover `*.${SALES_AGENT_DOMAIN}`. Verified by: `tests/integration/test_trusted_host_wildcards.py` green (requests matching nginx server_name patterns are accepted; requests outside the pattern return 400).
- [ ] `src/core/domain_config.py::get_session_cookie_domain` caller is migrated from Flask `app.secret_key` config to Starlette `SessionMiddleware` config. Verified by: `rg -n 'secret_key' src/core/domain_config.py` returns zero matches.
- [ ] All `health*` endpoints are enumerated and their URL-stability status documented. At minimum `src/admin/blueprints/core.py:366` and `:378`, `src/routes/health.py:37`, `src/routes/api.py:26`, `src/admin/blueprints/schemas.py:150`, and `src/admin/tenant_management_api.py:40` are each labeled {retained, merged, deleted}. External-uptime check paths are confirmed stable. Verified by: `docs/migration/health-endpoints-inventory.md` exists with the full enumeration; any external-uptime check requiring a path change is updated before L2 merge.
- [ ] `templates/error.html` recursive-render protection is in place: either (a) the exception handler renders a minimal `error_minimal.html` that does NOT extend `base.html`, OR (b) `base.html` `url_for` calls use a safe `_url_for` helper that catches `NoMatchFound`. Verified by: `tests/integration/test_error_handler_no_recursion.py` green (forcing a render-time `NoMatchFound` inside `error.html` yields a 500 with a minimal HTML body, not an infinite recursion or stack overflow).
- [ ] CI `test.yml` integration-test timeout is bumped from `--timeout=60` to `--timeout=120` for the first 2 post-L2 runs (to capture cold threadpool warmup), then reverted. Verified by: two post-L2 CI runs complete without timeout-related failures; a follow-up commit reverts the timeout to 60 before L3 enters.
- [ ] Structural guard `tests/unit/architecture/test_architecture_mcp_no_flask_transitive.py` is added — AST-walks imports reachable from `src/core/main.py` and asserts zero transitive Flask dependency. Verified by: guard green on current `main`; a contamination-fixture test (a deliberately Flask-importing module placed in the import graph) proves the guard fails on violations.
- [ ] `.pre-commit-hooks/check_route_conflicts_fastapi.py` is rewritten (replacing the Flask-specific `check_route_conflicts.py`, which is deleted). Verified by: the rewritten hook fails on a fixture with two conflicting FastAPI routes; passes on current `src/admin/routers/`; the old hook file is absent from the tree.
- [ ] Smoke tests under `tests/smoke/` are swept for `from src.admin.app import create_app` and converted. Verified by: `rg -n 'from src\.admin\.app' tests/smoke/` returns zero matches.
- [ ] `scripts/deploy/fly-set-secrets.sh` references to `FLASK_SECRET_KEY` are **removed at L2** (v2.0 hard-removal, not dual-read). Verified by: `rg -n 'FLASK_SECRET_KEY' scripts/deploy/` returns zero matches; removal commit is part of the L2 PR diff.
- [ ] **L2 work item (D6 breaking change):** Hard-remove `FLASK_SECRET_KEY` dual-read. Delete the fallback read in SessionMiddleware registration (`src/admin/sessions.py`). Update `scripts/setup-dev.py:143` to write `SESSION_SECRET` only. Delete `tests/unit/test_setup_dev.py::test_flask_secret_key_*`. Add structural guard `tests/unit/test_architecture_no_flask_secret_key_reads.py` with EMPTY allowlist — AST-scans `src/**/*.py`, `scripts/**/*.py`, `tests/**/*.py` for `FLASK_SECRET_KEY` string literals and dict/env accesses. Update `docs/environment.md` and v2.0 release notes. Verified by: `rg -n 'FLASK_SECRET_KEY' src/ scripts/ tests/ docs/` returns zero matches at L2 exit.

**Exit criteria:**

- [ ] All 15 Wave-3 acceptance criteria in execution-details §Wave 3.A pass
- [ ] `rg -w flask .` from repo root returns zero hits
- [ ] `v2.0.0-rc1` pre-release tag applied at end of L2 (Flask removal complete, pre-release candidate); the **final `v2.0.0` tag is applied at L7 only**, after async conversion (L5) and native refinements (L6) ship. Async work continues as L5+ within v2.0 (supersedes prior "tag waits until Waves 4-5 land" plan — see async-pivot-checkpoint.md for history). Verified by: `git tag --list 'v2.0.0*'` at L2 exit shows only `v2.0.0-rc1`; at L7 exit shows both `v2.0.0-rc1` and `v2.0.0`.
- [ ] Staging canary runs 48h without incident
- [ ] Wave 3 / L2 merges to `main`; async work continues as L5+ within v2.0 (supersedes prior "Waves 4-5 continue the async DB layer absorption" plan — see async-pivot-checkpoint.md for history).

### Section 4 cross-cutting rules (2026-04-15 sharpening)

These rules apply to every layer's Entry and Exit subsections in §4, regardless of whether the layer is labelled by its legacy Wave header or its canonical L-name.

- **Mandatory security-reviewer sign-off**: any PR touching `src/admin/csrf.py`, `src/admin/sessions.py`, `src/admin/oauth.py`, `src/admin/rate_limits.py`, or `src/admin/middleware/security_headers.py` requires sign-off by the named security-reviewer role (CLAUDE.md §Ownership) regardless of layer. Verified by: GitHub PR review approval recorded against the named role.
- **Reviewer rotation**: no single reviewer approves more than 3 consecutive layer PRs. Verified by: `git log main --merges --pretty=format:'%H %s' --since=<L0-start>` cross-referenced against PR review records — 4th consecutive layer PR must show a different approver (exception: security reviewer on security-critical layers L1b, L2 security work).
- **Discovered items slot**: every layer includes a "Discovered items" list in its work-items section. Layer exit is blocked unless the list is empty OR each item has a filed follow-up issue linked in the layer PR description. Verified by: PR description contains either "Discovered items: none" or a bulleted list with issue links.
- **Rollback re-entry condition**: every layer rollback section specifies when the layer may be retried after a rollback. Verified by inspection of the rollback section; cross-referenced in §5 and §6.5 alert linkage.
- **Alert-name linkage in rollback triggers**: every rollback trigger in §5 explicitly names the §6.5 alert that fires it (e.g., `MigrationHigh5xx`, `FlaskCatchallHit`). Verified by: `rg -n 'alert|PAGE|NOTIFY' §5-rollback-text` cross-references §6.5 alert table entries.

### L3 — Test harness modernization (entry, work items, exit)

**Engineer-day estimate:** 3–4 (per CLAUDE.md §v2.0 Timeline Summary).

**L3 entry criteria:**

- [ ] L2 signed off by primary lead per §6.2 (48h bake passed; zero `FlaskCatchallHit` alerts).
- [ ] `main` is up to date and CI is green.
- [ ] Role assignments current per CLAUDE.md §Ownership; no named-role holder on scheduled leave during this layer.
- [ ] L2 48h bake completed: 5xx rate ≤ baseline + 0.1%; p99 latency within documented tolerance; zero `FlaskCatchallHit` alerts (see §6.5).

**L3 work items:**

- [ ] Idempotent codemod `scripts/codemod_flask_test_client.py` exists and converts `app.test_client()` / `test_request_context()` calls to FastAPI `TestClient`. Verified by: running the codemod twice produces zero diff on the second run.
- [ ] Every test file enumerated in `docs/migration/test-client-inventory.md` (the pre-L3 baseline, 29+ files) is converted. Verified by: `rg -n 'app\.test_client\(\)|test_request_context\(\)' tests/ src/` returns zero matches.
- [ ] `tests/harness/admin_accounts.py:135` is converted (no longer imports `create_app` from `src.admin.app`). Verified by: `rg -n 'from src\.admin\.app' tests/harness/admin_accounts.py` returns zero matches.
- [ ] `tests/integration/test_tenant_management_api_integration.py` is unified (the `app` and `sync_app` fixtures merged into a single FastAPI app + `TestClient`). Verified by: the file contains exactly one app fixture (grep finds a single `@pytest.fixture` decorator on an `app`-returning function).
- [ ] Discovered items list is empty OR each item has a filed follow-up issue linked in the L3 PR description.

**L3 exit criteria:**

- [ ] All work items green.
- [ ] All structural guards for this layer green with allowlists ≤ L2 exit values (monotonic).
- [ ] Layer-scope commit-lint green (no out-of-scope commits — see `foundation-modules.md` §11.27).
- [ ] 7-step Test-Before-Implement audit bash script (CLAUDE.md §Test-Before-Implement Discipline) run against the L3 feature branch and green.
- [ ] PR squash-merged; release-please changelog entry present.
- [ ] L4 entry prerequisites documented as satisfied in `docs/development/migration-handoffs.md`.

### L4 — FastAPI-native sync refinement (entry, work items, exit)

**Engineer-day estimate:** 6–8.

**L4 entry criteria:**

- [ ] L3 signed off by primary lead per §6.2.
- [ ] `main` is up to date and CI is green.
- [ ] Role assignments current; no named-role holder on leave during this layer.
- [ ] **Spike 4.5 — ContextManager stateless refactor smoke test** passed (Decision 7 validation, 0.5–1 day). Pass criteria: refactor LOC <400 AND files touched <15 AND test patches <50 AND error-path composition test proves outer `session_scope()` rollback does NOT wipe error-logging writes. Soft blocker: on fail, ContextManager refactor gets a dedicated L4 sub-phase PR. Verified by: `spike-4.5.md` committed in the repo with pass/fail table and measurement numbers.
- [ ] Discovered items slot (empty at entry).

**L4 work items:** (existing Wave 4 content below references these; see Wave 4 / L3+L4+L5a-L5e heading for detail)

- [ ] `test_architecture_uses_structlog.py` guard extended: blocks `print()` in `src/**/*.py` (allowlist: `scripts/`, `alembic/versions/`, `src/core/cli/`). Verified by: guard green; `rg "^\s*print\(" src/ | rg -v "scripts/|alembic/versions/|core/cli/"` returns zero.
- [ ] Structural guard `test_architecture_no_pydantic_v1_validators.py` added: forbids `from pydantic import validator` and `@validator(...)` usage in `src/`. Verified by: `rg "from pydantic import.*\bvalidator\b|@validator\(" src/` returns zero; guard green.

**L4 bake window:**

- [ ] Duration: 24h minimum post-deploy (L4 is internally visible — sync refinements only; no user-visible breaking changes).
- [ ] Zero PAGE-severity alerts on `admin-migration-health` dashboard (see §6.5).
- [ ] 5xx rate ≤ baseline + 0.1%.
- [ ] p99 latency within documented tolerance vs pre-L4 sync baseline.
- [ ] Auth success rate ≥ 99.5%.
- [ ] Sign-off by incident commander recorded in `docs/development/migration-handoffs.md`.

**L4 exit criteria:**

- [ ] All work items green (atomic verification).
- [ ] `baseline-sync.json` captured at L4 EXIT and committed to the repo — this is the Spike 3 deliverable and is the oracle for L5 async performance comparison. Verified by: `tests/migration/fixtures/baseline-sync.json` exists, contains latency numbers for 20 admin routes + 5 MCP tool calls; captured under the L4 production-shape config (including adapter `run_in_threadpool` wraps if Path-B is active).
- [ ] All structural guards for this layer green with allowlists ≤ L3 exit values (monotonic).
- [ ] **L4 native-ness: pydantic-settings centralization.** Extended existing `src/core/config.py` (NOT new `src/core/settings.py` — two settings modules is a Flask-era regression). All 89 `os.environ.get(...)` sites consolidated into typed `BaseSettings` subclasses with `SettingsConfigDict(env_nested_delimiter="__")`. Credentials use `pydantic.SecretStr`. Guard `tests/unit/test_architecture_no_direct_env_access.py` seeded with 89 sites, ratchets to 0 by L7.
- [ ] **L4 native-ness: structlog adoption.** `structlog>=24.4.0` in `pyproject.toml`. 121 `print(` sites in `src/` replaced with `log = structlog.get_logger()` + `log.info(...)`. Pipeline: `merge_contextvars` + `TimeStamper(fmt="iso")` + `EventRenamer("message")` + `JSONRenderer()` (prod) / `ConsoleRenderer()` (dev). `RequestIDMiddleware` binds `request_id` via `structlog.contextvars.bind_contextvars(...)`. Guard `tests/unit/test_architecture_uses_structlog.py` blocks new `print(` in `src/**` (allowlist: `scripts/`, `alembic/versions/`, `src/core/cli/`).
- [ ] **L4 native-ness: httpx lifespan-scoped AsyncClient.** `httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0), limits=httpx.Limits(max_connections=100, max_keepalive_connections=20), transport=httpx.AsyncHTTPTransport(retries=3))` attached to `app.state.http_client` in lifespan startup; closed in lifespan shutdown. Sync `app.state.http_client_sync = httpx.Client(...)` registered for adapter Path B (Decision 1). `tenacity`-based 5xx/read-timeout retry for webhook calls.
- [ ] **L4 native-ness: AsyncAttrs decision record.** Rationale committed: blanket `lazy="raise"` (Spike 1) chosen over `AsyncAttrs` mixin because (a) fails LOUDLY vs silent extra async `SELECT`, (b) all 68 relationship access sites cataloged in Spike 1's 9-pattern cookbook, (c) `AsyncAttrs` is additive — can layer on post-v2.0.
- [ ] Layer-scope commit-lint green.
- [ ] 7-step Test-Before-Implement audit green.
- [ ] PR squash-merged.
- [ ] L5a entry prerequisites documented as satisfied.

### L5a — Spike window + L5 go/no-go decision gate

**Engineer-day estimate:** 5–7.

**L5a entry criteria:**

- [ ] L4 signed off by primary lead per §6.2.
- [ ] `main` is up to date and CI is green.
- [ ] `baseline-sync.json` present in the repo (captured at L4 EXIT).
- [ ] Role assignments current.
- [ ] **Spike 5.5 — Checkpoint-session viability (D3 validation)** scheduled for this window. Pass criteria: 4 test cases green at `tests/driver_compat/test_checkpoint_session_viability.py` — (a) single 4-hour sync with per-page short-lived sessions completes, (b) 3 concurrent multi-tenant syncs share the async pool without contention, (c) cancellation via `task.cancel()` cleanly closes any in-flight session, (d) resume from a persisted `sync_checkpoint` row after container restart. Soft blocker: on fail, revert to pre-D3 Option B sync-bridge (retain psycopg2-binary; file v2.1 sunset ticket) — documented in `spike-decision.md`. Verified by: spike test file exists and runs as part of L5a gate.

**L5a work items (10 technical spikes + 1 decision gate):**

- [ ] Spike 1 (lazy-load audit, HARD gate); Spike 2 (driver compat, HARD gate); Spike 4 (test-harness 5-file conversion); Spike 4.25 (factory-boy async shim); Spike 5 (scheduler alive-tick); Spike 5.5 (checkpoint-session viability per D3, listed above); Spike 6 (Alembic async evaluation); Spike 7 (GAM adapter threadpool saturation) — see CLAUDE.md canonical spike table for per-spike pass criteria.
- [ ] **Spike 8 — L5 go/no-go decision gate (HARD)**: `spike-decision.md` committed at L5a EXIT with pass/fail per technical spike, `baseline-sync.json` comparison (if any L5 experiments were run on the spike branch), resolved status of the 9 open decisions, and the final go/no-go call. Go condition: Spike 1 PASSES AND no more than 2 soft spikes fail. No-go: narrow L5 scope OR ship L0-L4 only and defer async to v2.1 (L0-L4 ships regardless).
- [ ] Discovered items list is empty OR each item has a filed follow-up issue.

**L5a exit criteria:**

- [ ] `spike-decision.md` committed with go/no-go call.
- [ ] All technical spike acceptance criteria green (or explicit fallback invoked for soft-spike failures).
- [ ] Layer-scope commit-lint green.
- [ ] 7-step audit green.
- [ ] PR squash-merged.
- [ ] If GO: L5b entry prerequisites documented. If NO-GO: v2.1 async scope ticket filed; L6 entry prerequisites re-derived under "ship L0-L4 only" path.

### L5b — SessionDep alias flip + guard swap

**Engineer-day estimate:** 1–2.

**L5b entry criteria:**

- [ ] L5a Spike 8 decision is GO.
- [ ] `main` is up to date and CI is green.
- [ ] Role assignments current; security reviewer available (async engine config is security-relevant).
- [ ] Discovered items slot (empty at entry).

### L5b entry preflight — hardened prerequisites (addresses database-deep-audit critical blockers)

Before L5b (SessionDep alias flip) opens, ALL of the following must be verifiably closed with PR references:

- [ ] **C1: statement_timeout under asyncpg.** DB engine config at `src/core/database/database_session.py` uses `connect_args={"server_settings": {"statement_timeout": "30000"}}` for asyncpg (NOT `options="-c statement_timeout=..."` which is psycopg2-only syntax and would crash under asyncpg). Regression test `tests/integration/test_statement_timeout_async.py` asserts a deliberately-slow query raises `asyncpg.QueryCanceledError` within 30s.
- [ ] **C2: CreativeRepository.commit() atomicity.** The two internal `self._session.commit()` calls at `src/core/database/creative_repository.py:234,476` (bypassing outer UoW) are DELETED and redirected to the caller's UoW boundary. Under async concurrency these would create a partial-commit race. Regression test `tests/integration/test_creative_repository_atomicity.py` asserts no nested commits.
- [ ] **C3: 20+ `uow.session` direct-access sites migrated.** Enumerate via `rg -n "uow\.session" src/` at L5a entry; all call sites migrated to typed repository methods or exposed through a UoW protocol. Zero direct `uow.session.scalars(...)` / `uow.session.execute(...)` remain in business logic.
- [ ] **H1: Product @property lazy-load trap.** The 6 `@property` methods on `Product` that access relationships (`Product.inventory_profile`, `Product.tenant`, etc. at `src/core/database/models.py`) are addressed via eager-loading at every `select(Product)` site (repository returns `select(Product).options(selectinload(Product.inventory_profile), joinedload(Product.tenant))`). Verified by Spike 1 lazy-load audit.
- [ ] **Connection budget assertion at startup.** `src/app.py` lifespan startup asserts `sum(pool_size + max_overflow for each engine) * expected_container_count < postgres_max_connections * 0.8`. Prevents H4 (60 × 2 rolling containers = 120 > max_connections=100 scenario). H4 resolution (pick ONE of pool-reduction / PgBouncer / max_connections bump / drain-then-start deploy) documented in `docs/operations/deploy-connection-budget.md`.
- [ ] **Spike 7 `server_default` audit expected count revised.** Plan previously expected <30 columns; per database-deep-audit H7, actual is 45+ columns. Spike 7 acceptance criterion updated to "enumerate ≥45 `server_default` columns; convert to Python-side `default=` where post-commit read is load-bearing, OR add `await session.refresh(instance)` after INSERT for those columns". New regression test `tests/integration/test_expire_on_commit_fields_populated.py` lands BEFORE L5b to prove `server_default` columns are populated post-INSERT under async + `expire_on_commit=False`.
- [ ] **pg advisory lock for multi-container Alembic safety (database-deep-audit M2).** `alembic/env.py run_migrations_online` acquires `pg_advisory_lock(<migration_epic_id>)` before running migrations and releases at end. Prevents race during rolling deploy where 2 containers both try to apply the same migration. Small edit (~0.25 day); gates L5b entry.
- [ ] **NO-GO path naming.** If Spike 8 returns NO-GO (Spike 1 lazy-load audit fails OR >2 soft spikes fail), the release tag is `v1.99.0` or equivalent, NOT `v2.0.0`. v2.0.0 is reserved for full async shipment. L5a exit-gate checklist enforces.

**L5b work items:**

- [ ] One-line `SessionDep` re-alias from `Annotated[Session, Depends(get_session)]` to `Annotated[AsyncSession, Depends(get_session)]` in `src/admin/deps/db.py` (or equivalent).
- [ ] Lifespan-scoped async engine created in `database_lifespan(app)`; stored on `app.state.db_engine`.
- [ ] Guard swap per §Wave 4 "L5b guard sunset + replacement" (moved from L5c per 2026-04-15 correction): delete sync-def guard and no-async-db guard; add async-handlers guard with FULL allowlist seeded.
- [ ] Structural guard `test_architecture_relationships_explicit_loading.py` added; all 68 existing relationships have explicit `lazy=` strategies per Spike 1 outcome; allowlist empty. Verified by: guard green; `rg "relationship\(" src/core/database/models/ | rg -v "lazy=" ` returns zero.

**L5b bake window:**

- [ ] Duration: 48h minimum.
- [ ] Zero PAGE-severity alerts.
- [ ] 5xx rate ≤ baseline + 0.1% vs `baseline-sync.json`.
- [ ] p99 latency within ±5% vs `baseline-sync.json`.
- [ ] Zero `MissingGreenlet` errors in log aggregator.
- [ ] Incident-commander sign-off recorded.

**L5b exit criteria:**

- [ ] All work items green.
- [ ] Structural guards monotonic (async-handlers allowlist = full; all other allowlists ≤ L5a).
- [ ] Layer-scope commit-lint green.
- [ ] 7-step audit green.
- [ ] PR squash-merged.
- [ ] L5c entry prerequisites documented.

### L5c — 3-router async pilot

**Engineer-day estimate:** 3–5.

**L5c entry criteria:**

- [ ] L5b 48h bake completed green.
- [ ] `main` is up to date and CI is green.
- [ ] Role assignments current.
- [ ] Discovered items slot (empty at entry).

**L5c work items:** (detailed in existing §Wave 4 L5c block)

**L5c bake window:**

- [ ] Duration: 48h.
- [ ] p99 latency on the 3 pilot routers within ±5% vs `baseline-sync.json`.
- [ ] Zero `MissingGreenlet` on pilot routes.
- [ ] DB pool saturation < 0.7 sustained.
- [ ] Incident-commander sign-off recorded.

**L5c exit criteria:**

- [ ] All 3 pilot routers converted and green.
- [ ] async-handlers allowlist shrunk by exactly 3 router names.
- [ ] **L5c native-ness: AsyncClient test harness.** Pilot router integration/e2e tests migrated from Starlette `TestClient` (sync) to `httpx.AsyncClient(transport=ASGITransport(app=app))` (async). Guard `tests/unit/test_architecture_no_testclient_in_async_routers.py` asserts tests importing the 3 async pilot routers use `AsyncClient`, not `TestClient`. BDD tests (pytest-bdd) remain sync via `asyncio.run()` bridge; guard `tests/unit/test_architecture_bdd_no_pytest_asyncio.py` prevents `@pytest.mark.asyncio` in BDD step files (would deadlock per Risk #3 Interaction B).
- [ ] Layer-scope commit-lint green.
- [ ] 7-step audit green.
- [ ] PR squash-merged.
- [ ] L5d1 entry prerequisites documented.

### L5d1 — `background_sync_service` async rearchitect (D3 2026-04-16, supersedes Option B sync-bridge)

**Engineer-day estimate:** 2–3.

**L5d1 entry criteria:**

- [ ] L5c bake completed green.
- [ ] `main` green.
- [ ] D3 checkpoint-session viability validated (Spike 5.5 green at L5a).

**L5d1 exit criteria:**

- [ ] `src/services/background_sync_service.py` rearchitected to `asyncio.create_task` + checkpoint-per-GAM-page. Each GAM-page (~30s) opens its own `async with get_db_session() as session:`, writes progress to a `sync_checkpoint` row, commits, closes.
- [ ] `threading.Thread` workers converted to `asyncio.create_task(...)` registered on `app.state.active_sync_tasks: dict[str, asyncio.Task]`; cancellable on shutdown.
- [ ] `sync_checkpoint` table migration landed; resume logic reads checkpoint and continues from next cursor on next tick.
- [ ] `src/services/background_sync_db.py` is NOT created.
- [ ] `test_architecture_no_threading_thread_for_db_work.py` green with EMPTY allowlist (AST-scans `src/` for `threading.Thread(target=...)` whose target body contains `get_db_session` or `session.`).
- [ ] Flask-caching ImportError at `background_sync_service.py:472` closed (SimpleAppCache replacement; post-D3 site 472 is inside an async task, still reaches the cache via the module-global `get_app_cache()` helper).
- [ ] Layer-scope commit-lint green; 7-step audit green.
- [ ] PR squash-merged.

### L5d2 — Adapter Path-B threadpool wrap

**Engineer-day estimate:** 3–4.

**L5d2 entry criteria:**

- [ ] L5d1 merged green.
- [ ] Spike 7 (GAM adapter threadpool saturation) reviewed.

**L5d2 exit criteria:**

- [ ] 30 `adapter.` call sites across 7 files in `src/core/tools/*.py` (verified 2026-04-17) wrapped in `await run_in_threadpool(...)`. Post-L0 codemod path is `src/admin/routers/operations.py` (pre-codemod `src/admin/blueprints/operations.py`); re-verification on 2026-04-17 did NOT confirm the previously-claimed "+1 adapter call" in that file — the file is still in scope for codemod + router-pattern alignment even if no adapter wrap is needed.
- [ ] `test_architecture_adapter_calls_wrapped_in_threadpool.py` green with empty allowlist.
- [ ] `anyio.to_thread.current_default_thread_limiter().total_tokens` set to `int(os.environ.get("ADCP_THREADPOOL_TOKENS", "80"))` (already at L0 per 2026-04-14 move).
- [ ] Layer-scope commit-lint green; 7-step audit green.
- [ ] PR squash-merged.

### L5d3 — Bulk router + repository async conversion

**Engineer-day estimate:** 8–12 (largest L5 sub-PR).

**L5d3 entry criteria:**

- [ ] L5d2 merged green.
- [ ] `main` green; no scheduled leave during blackout window (CLAUDE.md §Time-off blackout).

**L5d3 bake window:**

- [ ] Duration: 48h.
- [ ] async-handlers allowlist drained monotonically across sub-PRs (L5d3.1 → L5d3.4).
- [ ] Zero `MissingGreenlet` at any point in the window.
- [ ] p99 latency within ±5% vs `baseline-sync.json`.

**L5d3 exit criteria:**

- [ ] ~300 repository methods + ~2,400 LOC converted.
- [ ] async-handlers allowlist shrunk to ≤ 5 entries (remaining for L5d5/L5e).
- [ ] Layer-scope commit-lint green; 7-step audit green.
- [ ] PR squash-merged.
- [ ] **L5d3 file roster** — before L5d3.1 PR opens, generate the concrete file list per sub-PR and commit it as `.claude/notes/flask-to-fastapi/L5d3-file-roster.md`. Use `rg 'def ' src/admin/routers/` + `rg 'class.*Repository' src/core/database/repositories/` to enumerate. Split into 4 sub-PRs of ~600 LOC each grouped by domain (accounts/principals, media_buys/creatives, products/inventory, workflows/operations).

### L5d4 — SSE deletion (Decision 8)

**Engineer-day estimate:** 1–2.

**L5d4 entry criteria:** L5d3 merged green; `main` green.

**L5d4 exit criteria:**

- [ ] `/tenant/{id}/events` route + generator + rate-limit state + HEAD probe deleted.
- [ ] Verify `sse_starlette` is NOT in `pyproject.toml` (it was never added — the planned dep from the original 2026-04-11 SSE port never landed; no removal action needed, just confirm absence).
- [ ] `api_mode=False → True` fix on `/activity` JSON poll route landed.
- [ ] `test_architecture_no_sse_handlers.py` green with empty allowlist.
- [ ] Layer-scope commit-lint green; 7-step audit green.
- [ ] PR squash-merged.

### L5d5 — Async mop-up (`_impl`/`tools.py`/`main.py`)

**Engineer-day estimate:** 2–4.

**L5d5 entry criteria:** L5d3 merged green; L5d4 merged green (if both ready).

**L5d5 exit criteria:**

- [ ] All remaining `_raw`/`_impl` call sites in `src/routes/api_v1.py` + `src/core/tools/capabilities.py` have missing `await` keywords added per Agent D M1/M2.
- [ ] `test_api_v1_routes_await_all_impls.py` green.
- [ ] Layer-scope commit-lint green; 7-step audit green.
- [ ] PR squash-merged.

### L5e — Final async sweep

**Engineer-day estimate:** 3–4.

**L5e entry criteria:** L5d1–L5d5 all merged green; `main` green. Per-route p99 budget ±10% vs baseline entry, aggregate p99 ±5% vs `baseline-sync.json`, p50 ±10%, throughput ±5%, >20% regression on any single route blocks exit even if aggregate passes; see `execution-plan.md` L4 EXIT work item "baseline-sync.json capture (Spike 3)" — Perf criteria block — for full threshold list.

**L5e bake window:**

- [ ] Duration: 48h.
- [ ] async-handlers allowlist empty at L5e exit.
- [ ] p99 latency within ±5% vs `baseline-sync.json` (release gate).
- [ ] DB pool saturation < 0.7 sustained.
- [ ] Incident-commander sign-off recorded.

**L5e exit criteria:**

- [ ] `lazy="raise"` flipped permanently on all relationships in `src/core/database/models.py`.
- [ ] `test_async_performance_parity.py` green vs `baseline-sync.json`.
- [ ] async-handlers allowlist empty.
- [ ] Layer-scope commit-lint green; 7-step audit green.
- [ ] PR squash-merged.

### L6 — Native refinements

**Engineer-day estimate:** 3–4.

**L6 entry criteria:** L5e bake completed green; `main` green; role assignments current.

**L6 exit criteria:**

- [ ] D8 #4 meta-guard `tests/unit/test_architecture_no_admin_wrapper_modules.py` green — no `src/admin/flash.py`, no `src/admin/sessions.py`, no `src/admin/templating.py`. `MessagesDep` (from `src/admin/deps/messages.py`) has been in use since L1a; L6 verifies absence of the wrapper modules rather than deleting them.
- [ ] `SimpleAppCache` migrated to `app.state` singleton.
- [ ] Router subdir reorg complete.
- [ ] `logfire` instrumentation landed (NOT `opentelemetry-sdk`).
- [ ] Layer-scope commit-lint green; 7-step audit green.
- [ ] PR squash-merged.
- [ ] L7 entry prerequisites documented.

### L7 — Polish and ship v2.0.0

**Engineer-day estimate:** 3–5.

**L7 entry criteria:**

- [ ] L6 merged green; `main` green.
- [ ] Role assignments current; no named-role holder on leave during blackout window.
- [ ] All prior layer bake windows signed off in `docs/development/migration-handoffs.md`.

**L7 work items (2026-04-15 sharpening):**

- [ ] `docs/migration/migration-guide-v2.0.md` is written and covers breaking changes for downstream consumers of the admin UI and OAuth callbacks. Verified by: file exists; reviewed and signed off by the product owner (signoff recorded inline).
- [ ] Post-deploy smoke verification: the login flow is walked end-to-end by 2 operators in production within 30 minutes of deploy. Verified by: signoff with operator names and timestamps recorded in `docs/development/migration-handoffs.md`.
- [ ] Rollback rehearsal in staging is completed before L7 deploy. Verified by: rehearsal log in `docs/development/migration-handoffs.md` with date, participants, and outcome.
- [ ] Stakeholder comms per the L1a customer-comms plan are sent. Verified by: send-receipt or message archive linked in `docs/development/migration-handoffs.md`.
- [ ] Post-release monitoring owner and end-date are named. Verified by: the incident-commander role confirms ownership of the 48h watch; end-date is recorded in `docs/development/migration-handoffs.md`.
- [ ] Formatter consolidation: `black` pre-commit hook removed; `ruff-format` hook (from `astral-sh/ruff-pre-commit`) added. `black` removed from `pyproject.toml` dev dependencies. Verified by: `rg -w "black" .pre-commit-config.yaml pyproject.toml` returns zero; `ruff format --check .` green across repo; single PR scope.

**L7 bake window:**

- [ ] Duration: 1 week (per CLAUDE.md §Calendar time).
- [ ] Zero PAGE-severity alerts.
- [ ] All `Captured→shrink` allowlists empty (meta-guard `test_architecture_allowlists_empty_at_L7` green).
- [ ] mypy strict ratcheting green.
- [ ] Incident-commander sign-off recorded.

**L7 exit criteria:**

- [ ] `v2.0.0` git tag applied (final release, after L5 async + L6 refinements shipped).
- [ ] `docs/ARCHITECTURE.md` refreshed.
- [ ] `FLASK_SECRET_KEY` hard-removal verified green (the hard-remove itself moved to L2; at L7 confirm zero reads remain anywhere in `src/`, `scripts/`, `docs/`, or tests).
- [ ] Layer-scope commit-lint green; 7-step audit green.
- [ ] PR squash-merged; release-please changelog entry present with all breaking changes.
- [ ] Post-release monitoring plan active.

### Wave 4 / L3+L4+L5a-L5e — Async database layer (test harness, sync refinement, async conversion)

> **This entire wave is L5+ scope.** L0-L4 ship with sync handlers and sync SQLAlchemy. Do not implement anything in this section until L2 is complete. The async-audit reports in `async-audit/` contain the research for when this work begins in L5+.

> **Knowledge sources for this wave:**
> - `async-pivot-checkpoint.md` §3 — full target state (corrected 2026-04-12: lifespan-scoped engine, autoflush=False, connect_args)
> - `async-audit/agent-a-scope-audit.md` — file-by-file async conversion inventory
> - `async-audit/agent-b-risk-matrix.md` — 33 risks with mitigations + lazy-load cookbook
> - `async-audit/agent-d-adcp-verification.md` — 10 missing await sites + M1-M9 mitigations
> - `async-audit/database-deep-audit.md` — 3 critical blockers (statement_timeout, commit atomicity, MissingGreenlet)
> - `async-audit/testing-strategy.md` — test harness conversion plan
> - `flask_migration_critical_knowledge.md` items 1,3,7,8,9,13,14,15 — fork-safety, factory shim, psycopg2 retained, Alembic sync, statement_timeout, Product lazy-load, asyncpg JSONB, onupdate staleness (file is in auto-memory, not in project tree)

**Entry criteria:**

- [ ] Wave 3 merged to `main` and stable in staging ≥ 3 business days
- [ ] Pre-Wave-0 lazy-loading audit spike outcome approved (see Section 1.1)
- [ ] Pre-Wave-0 async driver compatibility spike outcome approved (see Section 1.1)
- [ ] `v1.99.0` git tag remains as the Flask-era rollback fallback

**L5c — 3-router async pilot (chosen for low-coupling, diverse patterns, no outbound HTTP):**

- [ ] `src/admin/routers/format_search.py` — 320 LOC, 4 routes, all GET (read-only). Pure query conversion; zero write paths; no outbound HTTP.
- [ ] `src/admin/routers/accounts.py` — 189 LOC, 5 routes (list/create/detail/edit/status). CRUD with tenant scoping; no outbound HTTP.
- [ ] `src/admin/routers/inventory_profiles.py` OR `src/admin/routers/authorized_properties.py` — CRUD without outbound HTTP; pick whichever has simpler schema.
- [ ] **Dropped from pilot:** `src/admin/routers/signals_agents.py` — has `POST /test` endpoint that makes outbound HTTP calls, forcing adapter async pattern validation simultaneously. Move to L5d (broader async rollout).
- [ ] **Rationale:** diverse patterns (read-only GET, write-path CRUD, tenant-scoped CRUD) without the confound of outbound HTTP. If pilot succeeds, L5d1-5d5 scale.

**L5b guard sunset + replacement** (same atomic commit as `SessionDep` alias flip to `AsyncSession`):

- [ ] `tests/unit/test_architecture_handlers_use_sync_def.py` is deleted at L5b (not L5c). Verified by: `git log -1 --name-status <L5b-sha> | grep -E 'D.*handlers_use_sync_def'` returns the deletion; sync-def guard is absent from `main` at L5b exit.
- [ ] `tests/unit/test_architecture_no_async_db_access.py` is deleted at L5b. Verified by: file absent post-L5b.
- [ ] `tests/unit/test_architecture_admin_handlers_async.py` is added in the SAME L5b commit and asserts every admin APIRoute handler is `async def`. **Allowlist starts FULL at L5b** (every admin handler is on the allowlist because none have been converted yet — the alias flip is type-only), **drains monotonically through L5c → L5d1 → L5d2 → L5d3 → L5d4 → L5d5 → L5e**, and reaches EMPTY at L5e exit. Verified by: allowlist file exists at L5b with ~all admin handler names enumerated; meta-guard `test_structural_guard_allowlist_monotonic.py` green across L5c-L5e; allowlist file is empty at L5e exit.
- [ ] Rationale: the sync-def guard becomes wrong at the moment `SessionDep` re-aliases to `AsyncSession` (L5b), not when the first pilot handler flips to async (L5c). Atomic swap at L5b prevents a window where the sync-def guard contradicts the `SessionDep` alias; the full-allowlist-then-drain pattern allows L5c pilot handlers to be the first to leave the allowlist as they convert. Meta-test for the new guard uses the `write-guard` skill pattern. Full implementation in `flask-to-fastapi-foundation-modules.md` §11.16.

**L1 OAuth refresh token storage — audit outcome:**

- [x] **OAuth refresh token storage audit** (VERIFIED: refresh tokens live in `AdapterConfig.gam_refresh_token` DB column, not in session). Cookie rename in L1b is safe for refresh token persistence. No migration work required.

**Core DB layer conversion (Agent A scope inventory governs this list):**

- [ ] `src/core/database/database_session.py` rewrite:
  - [ ] `create_engine` → `create_async_engine` (with `postgresql://` → `postgresql+asyncpg://` URL rewriter)
  - [ ] `scoped_session(sessionmaker(...))` → `async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)`
  - [ ] `get_db_session()` becomes `@asynccontextmanager async def` yielding `AsyncSession`
  - [ ] Engine is lifespan-scoped (not module-level) per Agent E Category 1 guidance — created in `database_lifespan(app)` and stored on `app.state.db_engine`
  - [ ] `SessionDep = Annotated[AsyncSession, Depends(get_session)]` defined in `src/core/database/deps.py`
- [ ] `alembic/env.py` stays sync with psycopg2 (supersedes prior "env.py rewrite" plan — see Section 1.1 correction and async-pivot-checkpoint.md for history). Spike 6 reduced to `render_item` hook + advisory lock (~0.5 day).
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

### Wave 5 / L6+L7 — Native refinements, polish & ship v2.0.0

> **This entire wave is L5+ scope.** v2.0.0 initial release happens at the end of L2 (Flask removal). See `execution-plan.md`.

> **Knowledge sources for this wave:**
> - `async-audit/agent-e-ideal-state-gaps.md` — remaining idiom upgrades deferred to L5+ within v2.0
> - `async-audit/agent-f-nonsurface-inventory.md` — non-code action items (docs, CI, Dockerfile)
> - `implementation-checklist.md` §6-7 — post-migration verification + planning artifact cleanup

**Entry criteria:**

- [ ] Wave 4 merged to `main` and stable in staging ≥ 3 business days

**Async-vs-sync benchmark:**

- [ ] Async vs pre-migration sync baseline benchmark run on representative admin routes (read-heavy `GET /admin/tenant/t1/products` + write-heavy `POST /admin/tenant/t1/accounts`)
- [ ] Latency profile is net-neutral to ~5% improvement under moderate concurrency (Risk #10)
- [ ] If regression: tune `pool_size` (Risk #6) first; `selectinload` eager-loading second; last resort fallback is to revert async conversion

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

- [ ] `v2.0.0-rc1` CHANGELOG (L2 Flask-removal milestone) does NOT include async changes. The `v2.0.0` final CHANGELOG (L7) includes both the Flask removal and the async conversion breaking changes: `psycopg2-binary` → `asyncpg`, `expire_on_commit=False` default, async handler signatures, async repository methods, async test harness (supersedes prior "single v2.0.0 entry covers everything" plan — see async-pivot-checkpoint.md for history).
- [ ] `v2.0.0` git tag applied
- [ ] Production deploy plan approved
- [ ] Production deploy completes

---

## Section 4.5 — Per-Layer Commit Policy

Each layer (L0-L7) merges to main as a **single-squash PR**. This gives revert-as-a-whole semantics matching the layer boundary.

### Carve-out: structural-guard-addition commits

When a layer introduces new structural guards with meta-tests, those guards ship as their OWN single-commit PR immediately preceding the layer's squash PR. Rationale:

- Guards are isolated infrastructure — their correctness is independent of the refactor they protect.
- `git bisect` on guard-triggered regressions requires the guard's commit to be bisectable, which squashing into the layer PR would prevent.
- Reviewer attention on guard mechanics (AST-scanning logic, allowlist semantics, meta-test patterns) benefits from isolation.

### Sequencing per layer

1. Guard-addition PR lands first (single-commit, reviewable in isolation)
2. Layer squash PR lands second, now protected by the new guards

Both PRs target the same layer's exit-gate; the guard PR MUST be merged before the layer PR can be merged.

---

## Section 4.6 — Goals-Adherence Test Matrix (§8 in commit plan)

> **Numbering note:** the original Commit-5 plan named this section `§8 Goals-Adherence Test Matrix`, but Section 8 already exists ("Known tech debt deferred beyond L2"). The matrix is placed here as §4.6 because it is semantically a per-layer gate companion to §4.5 (Per-Layer Commit Policy). External references that use "§8 Goals-Adherence Matrix" resolve to this section.

Every stated v2.0 goal maps to a specific test that proves adherence. The matrix is the contract — if a goal has no row, it has no test, and it is not a v2.0 goal.

### Columns

- **Goal**: plain-English invariant
- **Test file**: path under `tests/`
- **Assertion type**: AST scan / runtime behavior / golden fingerprint / integration
- **Layer written**: when the test first enters the repo (failing-red initially)
- **Layer first enforced**: when the test's allowlist is bootstrapped (green under ratchet)
- **Layer last enforced**: when guard retires (most are permanent — "—")
- **Inverts to**: for polarity-flip guards (e.g., sync→async), the guard that replaces it

### Table

| # | Goal | Test file | Assertion | Written | Enforced | Last | Inverts to |
|---|------|-----------|-----------|---------|----------|------|------------|
| 1 | Zero Flask imports in `src/` | `tests/unit/test_architecture_no_flask_imports.py` | AST scan | L0 | L0 (captured allowlist, shrinks) | — | — |
| 2 | Admin handlers are sync `def` in L0-L4 | `tests/unit/test_architecture_handlers_use_sync_def.py` | AST scan | L0 | L0 | L5b (sunset at alias flip) | `test_architecture_admin_handlers_async.py` |
| 3 | Admin handlers are `async def` (sweep starts L5b alias flip, complete by L5e) | `tests/unit/test_architecture_admin_handlers_async.py` | AST scan | L5a | L5b (full allowlist seeded; drains L5c–L5e) | — | — |
| 4 | No async DB access in L0-L4 | `tests/unit/test_architecture_no_async_db_access.py` | AST scan | L0 | L0 | L5b (sunset) | — |
| 5 | All URL generation via `url_for` | `tests/unit/test_templates_no_hardcoded_admin_paths.py` + `tests/integration/test_templates_url_for_resolves.py` | AST scan + runtime | L0 | L1 | — | — |
| 6 | Every admin route has `name=` | `tests/unit/test_architecture_admin_routes_named.py` | runtime introspection | L0 | L1 | — | — |
| 7 | Admin route names are unique | `tests/unit/test_architecture_admin_route_names_unique.py` | runtime introspection | L0 | L1 | — | — |
| 8 | `pydantic-settings` for all config | `tests/unit/test_architecture_no_direct_os_environ.py` | AST scan (scope: `src/admin/`) | L0 | L4 (captured allowlist, shrinks) | — | — |
| 9 | No `BaseHTTPMiddleware` in `src/admin/middleware/` | `tests/unit/test_architecture_pure_asgi_middleware.py` | AST scan | L0 | L0 | — | — |
| 10 | OAuth callback URIs byte-immutable | `tests/unit/test_oauth_callback_routes_exact_names.py` | string equality pin | L0 | L0 | — | — |
| 11 | Middleware order: canonical 7-stack | `tests/unit/test_architecture_middleware_order.py` | runtime introspection of `app.user_middleware` | L0 | L0 | — | — |
| 12 | Accept-aware `AdCPError` handler (HTML for `/admin/*` browsers, JSON otherwise; HTMX/XHR bypass HTML) | `tests/integration/test_admin_error_html_vs_json.py` | integration behavior | L1b | L1b | — | — |
| 13 | Template codemod is idempotent | `tests/unit/test_codemod_idempotent.py` | run-twice-diff-empty | L0 | L0 | L2 (codemod deleted) | — |
| 14 | Trailing slash tolerance on all routers | `tests/unit/test_trailing_slash_tolerance.py` | runtime behavior | L0 | L1 | — | — |
| 15 | Category-1 error shapes native (FastAPI default) | `tests/unit/test_category1_native_error_shape.py` | golden fingerprint | L1d | L1d | — | — |
| 16 | Category-2 error shapes preserved byte-identical | `tests/unit/test_category2_compat_error_shape.py` | golden fingerprint | L1d | L1d | — | — |
| 17 | OpenAPI semantic stability (schema equivalence, not byte-equal) | `tests/unit/test_openapi_semantic_stability.py` | normalized JSON diff vs committed golden | L0 | L0 | — | — |
| 18 | MCP tool inventory frozen during migration window | `tests/unit/test_mcp_tool_inventory_migration_stable.py` | phase-gated snapshot | L0 | L0 | L7 (snapshot retired) | — |
| 19 | MCP tool schemas AdCP-compliant | `tests/unit/test_mcp_tool_schema_adcp_compliant.py` | schema validation | pre-v2.0 | — | — | — |
| 20 | A2A agent card snapshot stable | `tests/unit/test_a2a_agent_card_snapshot.py` | normalized JSON diff | L0 | L0 | — | — |
| 21 | Admin routes excluded from OpenAPI | `tests/unit/test_architecture_admin_routes_excluded_from_openapi.py` | runtime introspection | L0 | L0 | — | — |
| 22 | CSRF exempt covers AdCP surfaces (`/mcp`, `/a2a`, `/api/v1/`, `/_internal/`, `/.well-known/`, `/agent.json`, 3 OAuth callbacks) | `tests/unit/test_architecture_csrf_exempt_covers_adcp.py` | constant inspection | L0 | L0 | — | — |
| 23 | Approximated middleware is path-gated (only `/admin/*` paths trigger redirect) | `tests/unit/test_architecture_approximated_middleware_path_gated.py` | AST + runtime | L0 | L0 | — | — |
| 24 | External-domain POST redirects BEFORE CSRF | `tests/integration/test_external_domain_post_redirects_before_csrf.py` | integration behavior | L1a | L1a | — | — |
| 25 | Flask catch-all unreached post-L1d | `tests/integration/test_flask_catchall_unreached.py` | integration behavior (response header `X-Served-By`) | L1d | L1d | L2 (Flask deleted) | — |
| 26 | Proxy headers produce `https` scheme | `tests/integration/test_proxy_headers_produce_https_scheme.py` | integration behavior (Fly-shape headers) | L2 | L2 | — | — |
| 27 | `SimpleAppCache` thread-safe + null-fallback | `tests/unit/test_simple_app_cache.py` | 13 case matrix | L2 | L2 | — | — |
| 28 | No `flask-caching` imports | `tests/unit/test_architecture_no_flask_caching_imports.py` | AST scan | L2 | L2 | — | — |
| 29 | Factory-boy in all test data setup (no raw `session.add()`) | `tests/unit/test_architecture_repository_pattern.py` (ratchet) | AST scan | pre-v2.0 | L3 (captured allowlist, shrinks) | — | — |
| 30 | `SessionDep` is the sole session seam in admin handlers | `tests/unit/test_architecture_no_get_db_session_in_handler_body.py` | AST scan | L4 | L4 | — | — |
| 31 | DTOs at handler/template boundary (no ORM models in templates) | `tests/unit/test_architecture_dtos_at_boundary.py` | AST scan | L4 | L4 | — | — |
| 32 | DTO base classes correctly configured (`RequestDTO` permissive, `ResponseDTO` strict+frozen+from_attributes) | `tests/unit/test_architecture_dto_config.py` | AST scan | L4 | L4 | — | — |
| 33 | Handler signatures use `Annotated[..., Path/Query/...]` | `tests/unit/test_architecture_handlers_use_annotated.py` | AST scan | L1 | L2 | — | — |
| 34 | Module-scope `create_app()` forbidden in tests | `tests/unit/test_architecture_no_module_scope_create_app.py` | AST scan | L0 | L2 (empty allowlist after sweep) | — | — |
| 35 | Lazy-load failures <40 (HARD GATE for L5) | `tests/integration/test_lazy_load_raise_audit.py` (Spike 1) | runtime count | L5a | L5a | L5a (gate only) | **Spike 1 prep:** convert 5 `backref=` pairs (at `models.py:727,1900,1935,1971,2010`) to explicit `back_populates` BEFORE running the `lazy="raise"` sweep — backref-synthesized reverse sides are invisible to the textual sweep and would silently retain `lazy="select"`. Structural guard `test_architecture_no_backref_only_relationships.py` asserts 0 `backref=` in `models.py` from L5a onward. Decision matrix (joinedload vs selectinload) lives in `foundation-modules.md §11.29`. |
| 36 | Factory async shim handles 8 edge cases | `tests/unit/test_factory_async_shim.py` (Spike 4.25) | case matrix | L5a | L5a | — | — |
| 37 | Adapter calls wrapped in `run_in_threadpool` (Path-B) | `tests/unit/test_architecture_adapter_calls_wrapped_in_threadpool.py` | AST scan | L5a | L5d2 | — | — |
| 38 | No singleton `ContextManager` (stateless module functions per Decision 7) | `tests/unit/test_architecture_no_singleton_session.py` | AST scan | L4 | L4 | — | — |
| 39 | No `threading.Thread` for DB work in `src/` (D3) | `tests/unit/test_architecture_no_threading_thread_for_db_work.py` | AST scan (empty allowlist) | L5d1 | L5d1 | — | — |
| 40 | No SSE handlers (Decision 8 deletion) | `tests/unit/test_architecture_no_sse_handlers.py` | AST scan | L5a | L5d4 | — | — |
| 41 | Checkpoint-session viability (Decision 9 per D3 rearchitect 2026-04-16; supersedes two-engine coexistence) | `tests/integration/test_checkpoint_session_viability.py` (Spike 5.5 — 4 test cases per CLAUDE.md row 5.5) | integration behavior | L5a | L5a | — | — |
| 42 | Async performance within ±5% at 50 req/s vs `baseline-sync.json` | `tests/integration/test_async_performance_parity.py` | benchmark compare | L4 (baseline capture) | L5e | L5e (release gate) | — |
| 43 | No `render()` Flask-style wrapper post-L4 | `tests/unit/test_architecture_no_flask_style_render.py` | AST scan | L4 | L4 (after refactor to TemplateResponse) | — | — |
| 44 | `httpx.AsyncClient` is singleton on `app.state` (no per-request construction in `src/`) | `tests/unit/test_architecture_httpx_singleton_in_app_state.py` | AST scan | L5 | L5 | — | — |
| 45 | All lifespan resources registered via `combine_lifespans` | `tests/unit/test_architecture_lifespan_composition.py` | runtime introspection | L0 | L0 | — | — |
| 46 | `TrustedHostMiddleware` present with wildcard tenant support | `tests/integration/test_architecture_trusted_host_middleware.py` | integration behavior | L2 | L2 | — | — |
| 47 | Outbound HTTP wrapped in retry (tenacity/stamina) | `tests/unit/test_architecture_adapter_retries_wrapped.py` | AST scan | L5 | L5 | — | — |
| 48 | Meta: structural-guard allowlist counts monotonically decrease | `tests/unit/test_structural_guard_allowlist_monotonic.py` (meta) | per-guard baseline comparison | L0 | L0 | — | — |
| 49 | Folder CLAUDE.md invariants mirrored in root CLAUDE.md banner | `tests/unit/test_architecture_invariants_consistent.py` | text parse + substring match | L0 | L0 | — | — |
| 50 | OAuth URI strings across `.md` match canonical 3-URI set | `tests/unit/test_architecture_oauth_uris_consistent.py` | regex whitelist + NOT-context allowlist | L0 | L0 | — | — |
| 51 | CSRF double-submit vocabulary forbidden in planning docs (Option A Origin-middleware only) | `tests/unit/test_architecture_csrf_implementation_consistent.py` | token scan + NOT-context allowlist | L0 | L0 | — | — |
| 52 | Layer-timeline scope text consistent across CLAUDE.md, implementation-checklist, execution-plan | `tests/unit/test_architecture_layer_assignments_consistent.py` | table parse + fuzzy substring | L0 | L0 | — | — |
| 53 | Spike table (CLAUDE.md §v2.0 Spike Sequence) is single source of truth for spike layer assignments | `tests/unit/test_architecture_spike_table_consistent.py` | table parse + cross-doc conflict check | L0 | L0 | — | — |
| 54 | Production entrypoints contain `--proxy-headers` + `--forwarded-allow-ips='*'` | `tests/unit/test_architecture_proxy_headers_in_entrypoints.py` | string scan of Dockerfile/run_server.py/fly.toml | L0 | L2 | — | — |
| 55 | No Pydantic v1 `class Config:` blocks in BaseModel subclasses | `tests/unit/test_architecture_no_pydantic_v1_config.py` | AST scan (empty allowlist) | L0 | L0 | — | — |
| 56 | No direct `os.environ.get(...)` / `os.environ[...]` outside `src/core/config.py` | `tests/unit/test_architecture_no_direct_env_access.py` | AST scan (ratcheting from 89 sites) | L4 | L4 | L7 (zero allowlist) | — |
| 57 | No `import requests` / `from requests import` in `src/` | `tests/unit/test_architecture_no_requests_library.py` | AST scan (ratcheting from 17 sites) | L5 | L5 | — (sunset in v2.1 adapter rewrites) | — |
| 58 | No `print(` in `src/**` (structlog adoption) | `tests/unit/test_architecture_uses_structlog.py` | AST scan (ratcheting from 121 sites, allowlist: `scripts/`, `alembic/versions/`, `src/core/cli/`) | L4 | L4 | — | — |
| 59 | Async pilot routers use `httpx.AsyncClient` in tests (not `TestClient`) | `tests/unit/test_architecture_no_testclient_in_async_routers.py` | AST scan (ratcheting; allowlist seeded with pre-L5c tests that still use `TestClient`) | L5c | L5c | — | — |
| 60 | No `@pytest.mark.asyncio` in BDD step files (would deadlock under pytest-bdd + pytest-asyncio per Risk #3 Interaction B) | `tests/unit/test_architecture_bdd_no_pytest_asyncio.py` | AST scan | L5c | L5c | — | — |

### Matrix usage

- **Before adding a v2.0 goal**: it must enter as a new matrix row with a test name. If no row, no goal.
- **Before closing a layer**: every row with `Layer first enforced == <this-layer>` must have its test green under the declared allowlist strategy.
- **Before merging v2.0**: every row with `Layer last enforced == —` must be green at zero allowlist entries.
- **Meta-guard (row 48)**: runs on every `make quality`; any allowlist growth on any matrix-row guard fails CI. Row 48 implementation lives at `flask-to-fastapi-foundation-modules.md` §11.26.

### Gaps (goals without matrix rows)

The following v2.0 aspirations are NOT in the matrix yet and therefore NOT committed as goals. Propose in a separate PR to add:
- OpenTelemetry instrumentation active (deferred — logfire gating)
- Task queue migration from `threading.Thread` (deferred to v2.1)
- `docs/ARCHITECTURE.md` refresh (L7 polish, prose-only — uses `discipline: N/A - docs-only`)
- Rollback rehearsal in staging (runbook, not test)

### Cross-references

- The 7-step Test-Before-Implement cycle in `CLAUDE.md` (this folder) is how each matrix-row test enters the repo (Red commit → Green commit).
- §11.26 (`test_structural_guard_allowlist_monotonic.py`) in `flask-to-fastapi-foundation-modules.md` is the row-48 meta-guard implementation.
- §11.27 (layer-scope commit-lint) in `flask-to-fastapi-foundation-modules.md` prevents scope creep across matrix-row layer boundaries.

---

## Section 5 — Rollback triggers and procedures

Full detail in `flask-to-fastapi-execution-details.md` §D under each wave.

### Rollback triggers per wave

- [ ] **Wave 0**: any failure of `make quality` post-merge; any templates regression found in Wave 1 entry check
- [ ] **Wave 1**: OAuth login broken in staging/prod; session cookie causes auth loop; CSRF middleware blocks POST form flows; middleware ordering causes 403s on external-domain POSTs
- [ ] **Wave 2**: any migrated admin route returns 500 against production traffic; Datadog dashboard loss; category-2 error shape regression caught by external consumer; coverage parity check fails
- [ ] **Wave 3**: uvicorn `--proxy-headers` fails to produce correct `https` scheme in production; dependency lockfile resolution produces incompatible tree

### Wave 0 / L0 rollback procedure

Wave 0 is **pure addition** — nothing changes behavior. Single-commit revert.

- [ ] `git checkout main`
- [ ] `git revert -m 1 <wave-0-merge-sha>`
- [ ] `git push origin main`
- [ ] Verify `make quality` green on post-revert main
- [ ] No database state to restore, no env vars to roll back

### Wave 1 / L1a-L1b rollback procedure

Single-commit revert works. Users get one EXTRA forced re-login (in addition to the one Wave 1 already caused).

- [ ] `git checkout main`
- [ ] `git revert -m 1 <wave-1-merge-sha>`
- [ ] `git push origin main`
- [ ] Verify `register_blueprint` calls in `src/admin/app.py` auto-restored (they were commented out, not deleted)
- [ ] Verify `SESSION_SECRET` can remain set — Flask ignores it, no harm
- [ ] Document forced re-login in revert PR description
- [ ] No database state to restore

### Wave 2 / L1c-L1d rollback procedure

Single-commit revert; largest revert commit. Flask catch-all re-activates.

- [ ] `git checkout main`
- [ ] `git revert -m 1 <wave-2-merge-sha> --no-edit`
- [ ] `git diff HEAD~1 --stat | head -30` — verify 25+ files restored
- [ ] `git push origin main`
- [ ] Verify Flask catch-all at `src/app.py:299-304` is still live
- [ ] **Partial rollback option**: if only ONE router broke, revert just that file + its tests + re-add `register_blueprint(<bp>)` to `src/admin/app.py`, leaving the rest of Wave 2 intact
- [ ] Rollback window is open only until Wave 3 merges

### Wave 3 / L2 rollback procedure

**This is the dangerous cutover.** Wave 3 cannot roll back piecemeal.

- [ ] `git checkout main`
- [ ] `git revert -m 1 <wave-3-merge-sha> --no-edit`
- [ ] `cat pyproject.toml | grep -A 2 flask` — verify Flask deps restored
- [ ] `uv lock` — rebuild lockfile
- [ ] `docker build .` — rebuild image
- [ ] `grep -n "flask_admin_app\|admin_wsgi\|_install_admin_mounts" src/app.py` — verify Flask catch-all restored
- [ ] **Fallback option** (if git revert is too risky): redeploy the archived `v1.99.0` container image from the registry, accept downtime
- [ ] Rollback window closes when L2 ships. L5+ (async) is a separate rollback domain (supersedes prior "rollback window open until Wave 4 async merges" plan — see async-pivot-checkpoint.md for history).

### Layer 3 rollback procedure

**Test-harness only. Safe revert. No data.**

- [ ] **Trigger scenarios:** factory-boy base class introduces a flaky `_create` override; new integration test harness loses dependency-override teardown hygiene and tests leak state across files; ratcheting repository-pattern allowlist accidentally shrinks below a line that new tests rely on.
- [ ] **Procedure:**
  1. `git checkout main`
  2. `git revert -m 1 <L3-merge-sha> --no-edit`
  3. `tox -e integration` — verify suite green on the reverted tree (pre-L3 test patterns restored)
  4. `git diff HEAD~1 -- tests/factories/ tests/harness/ tests/integration/conftest*.py` — verify factory + harness files restored
  5. `git push origin main` (feature branch preserved per ephemeral-branch workflow — see session-completion.md)
- [ ] **Irreversible effects:** none. L3 touches test code only. Production, DB schema, admin routes, and FastAPI surface are all unaffected.
- [ ] **Recovery time estimate:** ~30 minutes (revert + integration suite run).
- [ ] **Data loss risk:** none.

### Layer 4 rollback procedure

**Sync `SessionDep` + DTOs + pydantic-settings + structlog + `render()` deletion + ContextManager refactor. ~45 minutes revert.**

- [ ] **Trigger scenarios:** `SessionDep = Annotated[Session, Depends(get_session)]` introduces a regression surfaced only in prod (e.g., `request.app.state.settings` access pattern breaks a caller); DTO boundary causes a silent serialization change caught post-deploy; structlog reconfiguration breaks log aggregation ingestion (schema change at `logfire`/Datadog shippers); `render()` deletion surfaces a missed `render_template` call; ContextManager module-function refactor causes a transaction-interleaving regression in production.
- [ ] **Procedure:**
  1. `git checkout main`
  2. `git revert -m 1 <L4-merge-sha> --no-edit`
  3. `grep -rn "from src.admin.templating import render" src/` — verify `render()` wrapper restored at all call sites (previously deleted)
  4. `grep -rn "app.state.settings" src/ scripts/` — **any scripts importing `app.state.settings` after revert will fail loudly**; either revert those scripts in the same revert commit or file follow-ups
  5. `grep -rn "from src.core.context_manager import" src/ tests/` — verify import sites match the pre-L4 `ContextManager` class pattern (not module-level functions)
  6. **Log schema coordination:** notify log aggregator owner that structlog `processors=[...]` pipeline has reverted — downstream dashboards and alerts keyed on the L4 log shape must be temporarily tolerant of pre-L4 shape or flip to dual-read
  7. `make quality && tox -e integration` — verify suite green on the reverted tree
  8. `git push origin main`
- [ ] **Irreversible effects:** production log-aggregator schema transition window (dashboards/alerts that adopted L4 structlog shape need a dual-read period); any consumer scripts that adopted `app.state.settings` pattern need revert coordination.
- [ ] **Recovery time estimate:** ~45 minutes (revert + log-aggregator coordination + integration suite run).
- [ ] **Data loss risk:** none — no DB schema change, no wire-format change, no cookie change.

### Layer 5a rollback procedure

**Spike artifacts only, no production code changes. ~10 minute revert.**

- [ ] **Trigger scenarios:** one of the 10 spikes (1, 2, 3, 4, 4.25, 4.5, 5, 5.5, 6, 7) exposes a blocker that cancels L5 entry; structural guard stubs added in L5a break `make quality` because they fire on pre-L5a code.
- [ ] **Procedure:**
  1. `git checkout main`
  2. `git revert -m 1 <L5a-merge-sha> --no-edit`
  3. `rm -f spike-decision.md tests/migration/fixtures/spike-*.json` if created locally
  4. `make quality` — verify green on the reverted tree
  5. `git push origin main`
- [ ] **Irreversible effects:** none. L5a is pure test and scaffolding. No production code, DB schema, or admin behavior changes.
- [ ] **Recovery time estimate:** ~10 minutes.
- [ ] **Data loss risk:** none. Spike findings in `spike-decision.md` should be preserved independently if the revert is triggered (copy to a notes file before reverting if the spike analysis is still valuable).

### Layer 5b rollback procedure

**SessionDep alias flip — CASCADE REVERT REQUIRED if L5c/L5d/L5e shipped. This is the single most destabilizing rollback in the v2.0 migration.**

- [ ] **Trigger scenarios:** async engine in `src/core/database/engine.py` misconfigured (pool sizing, asyncpg-specific `statement_timeout` crash surfaced in prod — see `database-deep-audit.md`); `AsyncSession` expiry-on-commit interaction with DTO boundary breaks a handler; perf regression beyond budget vs `baseline-sync.json`; `MissingGreenlet` errors at scale that did not appear in staging.
- [ ] **Procedure (if ONLY L5b shipped, L5c-L5e not yet merged):**
  1. `git checkout main`
  2. `git revert -m 1 <L5b-merge-sha> --no-edit`
  3. `grep -rn "from sqlalchemy.ext.asyncio import" src/admin/deps/db.py` — verify `SessionDep` reverted to `Annotated[Session, Depends(get_session)]`
  4. Swap structural guards back: restore `test_architecture_handlers_use_sync_def.py`, remove `test_architecture_admin_handlers_async.py`
  5. `make quality && ./run_all_tests.sh` — verify green
  6. `git push origin main`
- [ ] **Procedure (CASCADE if L5c/L5d/L5e partially or fully shipped):**
  ```bash
  # Reverse-order revert loop — topologically each async-consuming layer must revert before L5b
  for sha in $L5e_sha $L5d5_sha $L5d4_sha $L5d3_sha $L5d2_sha $L5d1_sha $L5c_sha $L5b_sha; do
    git revert -m 1 "$sha" --no-edit || { echo "Conflict at $sha — resolve manually before continuing"; break; }
  done
  git push origin main
  ```
  - [ ] After cascade: run `make quality && ./run_all_tests.sh` and verify structural guards swap back (sync-def guard active, async-routes guard removed)
  - [ ] Verify asyncpg driver removed from `pyproject.toml`; `psycopg2-binary` remains primary
  - [ ] Verify `AsyncSession`, `async_sessionmaker`, `create_async_engine` imports are zero in `src/`
- [ ] **Irreversible effects:**
  - Cascade revert is **high-risk** — any merge conflict during the reverse-order loop leaves the tree in a half-converted state. If conflicts appear, **STOP the loop, pair-resolve the conflict, and continue** — do NOT force-push over a half-reverted tree.
  - `baseline-sync.json` comparison oracle stays valid after cascade (it was captured at L4 EXIT, before any async work).
- [ ] **Recovery time estimate:**
  - L5b-only revert: ~1 hour (1-line flip reversal + guard swap + suite).
  - Full cascade (L5e → L5b): **1-3 hours** depending on conflict resolution during the reverse loop.
- [ ] **Data loss risk:** none — no schema change, no data change. However, in-flight requests during the deploy swap may see `MissingGreenlet` or connection-pool exhaustion errors transiently; expect a brief 5xx spike at the cutover moment.

#### Fast-path rollback (PRIMARY, ~2 min MTTR)

Redeploy `v2.0.0-rc.L5a` container image. No CI pass, no git revert, no merge-conflict risk:

```bash
# Pre-built image redeploy — ~2 min MTTR (beats 15-min target; far beats 1-3h git-revert path).
flyctl deploy --image registry.fly.io/salesagent:v2.0.0-rc.L5a --strategy rolling

# Verify:
flyctl status | grep "version"  # should show v2.0.0-rc.L5a
curl -fsSL https://app.fly.dev/healthz | jq .
```

**When to use:**
- Error-rate spike or p99 latency regression within 15 min of L5b deploy.
- `MissingGreenlet` exceptions in production logs.
- Connection-pool exhaustion signals.

**Prerequisites (enforced by L5a EXIT gate):**
- `v2.0.0-rc.L5a` git tag and container image created at L5a EXIT (post-Spike 8 go-decision, pre-L5b merge).
- Container image retention bumped to **90 days** (vs default 30) to cover L5b+L5c+L5d1-L5d5+L5e bake window.
- Zero schema changes between L5a and L5b — enforced by `tests/integration/test_l5b_no_schema_delta.py` (see Task 3).

**Recovery sequence:**
1. `flyctl deploy --image ...` (~2 min rolling deploy).
2. Confirm `/healthz` green across all instances.
3. During business hours, `git revert` L5b on main to close the feedback loop.
4. File incident writeup referencing the `v2.0.0-rc.L5a` tag.

#### Why no runtime kill-switch

A runtime `ADCP_USE_ASYNC_SESSIONDEP` feature flag is NOT provided because it is architecturally infeasible: `async def` handlers cannot consume a sync `Session` (shape mismatch at every `await session.execute(...)` site), and the Critical Invariant #1 (sync handlers + `scoped_session` safety) would be violated under `async def` handlers consuming sync Session. The pre-built image redeploy IS the 2026 SRE answer — it matches the pattern already proven at L2 and L4 rollbacks.

#### L5a EXIT checklist addition

- [ ] Tag `v2.0.0-rc.L5a` created: `git tag v2.0.0-rc.L5a <L5a-merge-sha> && git push origin v2.0.0-rc.L5a`
- [ ] Container image archived: `docker build -t registry.fly.io/salesagent:v2.0.0-rc.L5a . && docker push registry.fly.io/salesagent:v2.0.0-rc.L5a`
- [ ] Image retention set to 90 days: `flyctl image list | grep v2.0.0-rc.L5a` confirms
- [ ] Schema-freeze gate green: `tests/integration/test_l5b_no_schema_delta.py` passes (confirms zero migrations between L5a-EXIT and current branch)

### Layer 5c rollback procedure

**Bounded to 3 pilot routers. L5b alias remains. ~30 minute revert.**

- [ ] **Trigger scenarios:** one of the 3 pilot routers (`format_search.py`, `accounts.py`, `inventory_profiles.py` OR `authorized_properties.py`) has a subtle async bug (race, N+1 regression, missing `await`); factory-boy async shim fails under pilot test load; perf budget violation on pilot routes vs `baseline-sync.json`.
- [ ] **Procedure:**
  1. `git checkout main`
  2. `git revert -m 1 <L5c-merge-sha> --no-edit`
  3. Verify the 3 pilot routers return to sync `def` bodies (`git diff HEAD~1 -- src/admin/routers/format_search.py src/admin/routers/accounts.py`)
  4. Verify pilot tests revert to sync `TestClient` patterns
  5. **L5b SessionDep alias remains `AsyncSession`** — the 3 pilot routers are the only production consumers; post-revert they use `SessionDep` but consume it synchronously via a temporary `Session` factory adapter (document in revert PR)
  6. `make quality && ./run_all_tests.sh` — verify green
  7. `git push origin main`
- [ ] **Irreversible effects:** the `SessionDep` type alias still resolves to `AsyncSession` after revert. This is intentional — L5b is NOT reverted. Any new code written against `SessionDep` post-revert must either await it or use the sync adapter.
- [ ] **Recovery time estimate:** ~30 minutes (revert + integration suite).
- [ ] **Data loss risk:** none.

### Layer 5d rollback procedure — revert dependency graph

L5d ships as **5 separate sub-PRs** (L5d1-L5d5) for independent revertibility. The revert dependency graph:

```
L5d1 (background_sync async rearchitect)  — independent, revert alone
L5d2 (adapter Path-B)  — independent, revert alone
L5d3 (bulk routers)    — CASCADES; L5d3.1→L5d3.2→L5d3.3→L5d3.4 (topological within L5d3)
L5d4 (SSE deletion)    — independent, revert alone
L5d5 (mop-up)          — depends on L5d3; revert L5d5 BEFORE L5d3
```

- [ ] **L5d1 (background_sync async rearchitect):** `git revert -m 1 <sha>`; verify `src/services/background_sync_service.py` reverts to `threading.Thread` workers; `sync_checkpoint` migration reverts (downgrade); `app.state.active_sync_tasks` dict gone; `test_architecture_no_threading_thread_for_db_work.py` reverts alongside the source change. Note: revert does NOT require creating `src/services/background_sync_db.py` — that file was never written under D3. ~30 minutes.
- [ ] **L5d2 (adapter Path-B threadpool wrap):** `git revert -m 1 <sha>`; verify 30 adapter call sites across 7 files in `src/core/tools/*.py` (re-verified 2026-04-17) in `src/admin/routers/operations.py` (post-L0 codemod path) no longer wrapped in `await run_in_threadpool(...)`. Structural guard `test_architecture_adapter_calls_wrapped_in_threadpool.py` reverts in same commit. ~30 minutes.
- [ ] **L5d3 (bulk router conversion) — CASCADE revert:**
  ```bash
  # Topological: revert in reverse domain-group order
  for sha in $L5d3_4_sha $L5d3_3_sha $L5d3_2_sha $L5d3_1_sha; do
    git revert -m 1 "$sha" --no-edit || { echo "Conflict at $sha"; break; }
  done
  ```
  - Revert L5d5 first if it shipped (L5d5 depends on L5d3 router conversions being active).
  - ~2-4 hours depending on number of sub-PRs merged and conflict density.
- [ ] **L5d4 (SSE deletion):** `git revert -m 1 <sha>`; verify `/tenant/{id}/events` route restored in `src/admin/routers/activity_stream.py` (or `src/admin/blueprints/activity_stream.py` if pre-L1d), `sse_starlette` dependency restored in `pyproject.toml`, `api_mode=True` bug-fix on `/activity` reverted to `api_mode=False`. ~30 minutes.
- [ ] **L5d5 (mop-up):** `git revert -m 1 <sha>`; must revert BEFORE L5d3 if L5d3 is also reverting. ~1 hour.
- [ ] **Irreversible effects:** L5d4 SSE deletion revert restores dead code — acceptable transiently but schedule re-deletion as a follow-up. L5d3 cascade conflicts are the highest risk.
- [ ] **Recovery time estimate:** 30 min (single sub-PR revert) to 4 hours (full L5d cascade with L5d3 conflicts).
- [ ] **Data loss risk:** none across all L5d sub-PRs.

### Layer 5e rollback procedure

**Final async sweep. Revert alone, or as part of L5 cascade.**

- [ ] **Trigger scenarios:** `lazy="raise"` permanent flip surfaces a lazy-load that spike 1 missed; remaining `_impl` async conversions introduce an await-missing site caught in prod; perf regression beyond budget surfaced only after full-suite async is active.
- [ ] **Procedure:**
  1. `git checkout main`
  2. `git revert -m 1 <L5e-merge-sha> --no-edit`
  3. Verify `lazy="raise"` flipped back to pre-L5e state on affected relationships (spot-check `src/core/database/models.py`)
  4. `make quality && ./run_all_tests.sh` — verify green
  5. `git push origin main`
- [ ] **Irreversible effects:** none in isolation. If L5e revert is not sufficient and full L5 cascade is needed, see L5b cascade procedure.
- [ ] **Recovery time estimate:** ~45 minutes (revert + integration suite + quick perf spot-check).
- [ ] **Data loss risk:** none.

### Layer 6 rollback procedure

**Delete `flash.py`, `app.state` for `SimpleAppCache`, `logfire` instrumentation, router subdir reorg. ~30-60 minutes.**

- [ ] **Trigger scenarios:** `SimpleAppCache` migration to `app.state` breaks background-task access (post-D3, `background_sync_service.py:472` still reads cache via module-global `get_app_cache()` from inside its `asyncio.create_task` — migration to `app.state` must preserve the helper fallback or the async task crashes); `logfire` dashboard queries break when spans change shape; router subdirectory reorganization breaks an import path that CI missed. (Pre-D3 trigger list also included `app.state.flash_store` memory leak — under D8 #4 no flash_store migration happens at L6, so this trigger is removed.)
- [ ] **Procedure:**
  1. `git checkout main`
  2. `git revert -m 1 <L6-merge-sha> --no-edit`
  3. Verify `src/admin/flash.py` is restored
  4. Verify `SimpleAppCache` is back to module-global pattern (per Decision 6 recipe) — background threads use `get_app_cache()` again
  5. Verify router file paths restored to `src/admin/routers/<file>.py` (flat) if subdir reorg happened
  6. **logfire coordination:** `logfire` dashboards and alerts keyed on post-L6 span shape need rebuild — notify observability owner before reverting; plan a dashboard-rebuild follow-up task
  7. `make quality && ./run_all_tests.sh` — verify green
  8. `git push origin main`
- [ ] **Irreversible effects:** `logfire` dashboard queries need rebuild post-revert (dashboards referencing L6 span names/attributes break silently — rows appear empty until rebuilt). Router subdir reorg revert churns the import graph visibly in PRs, but is mechanically safe.
- [ ] **Recovery time estimate:** ~30-60 minutes (revert + logfire dashboard coordination + integration suite).
- [ ] **Data loss risk:** none — no DB or persistent-store interaction.

### Layer 7 rollback procedure

**Allowlist-to-zero ratchet, mypy strict, v2.0.0 tag. Tag delete is destructive and may require downstream consumer coordination.**

- [ ] **Trigger scenarios:** allowlist-to-zero ratchet fails at `make quality` post-merge because a shrinking allowlist exposed a violation that was missed in review; mypy strict ratcheting surfaces a latent typing bug that blocks CI; v2.0.0 tag applied to a commit with a production regression found in the 48h bake. (Note: `FLASK_SECRET_KEY` hard-removal moved to L2; its rollback lives in the L2 rollback procedure.)
- [ ] **Procedure:**
  1. `git checkout main`
  2. `git revert -m 1 <L7-merge-sha> --no-edit`
  3. Restore allowlist entries that were shrunk to zero (copy from `git show <L7-sha>:<allowlist-path>`)
  4. Restore mypy strict-mode relaxations (per-module ratchet rollback)
  5. **v2.0.0 tag deletion (DESTRUCTIVE — requires downstream coordination):**
     ```bash
     # Only if tag already pushed AND downstream consumers have not yet bumped
     git tag -d v2.0.0
     git push origin :refs/tags/v2.0.0  # delete remote tag
     ```
     - If downstream consumers (docker image registry, pypi if published, release-please, downstream repos) have already pinned to `v2.0.0`, **do NOT delete the tag**. Instead publish `v2.0.1` with the fix and keep `v2.0.0` deprecated-but-present.
  6. `make quality && ./run_all_tests.sh` — verify green
  7. `git push origin main`
  8. Coordinate with downstream consumers about the tag status (either deleted and to be re-tagged later, or deprecated-but-present pending `v2.0.1`)
- [ ] **Irreversible effects:** v2.0.0 tag deletion is cache-poisoning risk — any consumer that fetched the tag already has the commit pinned. Prefer `v2.0.1` forward-fix over tag deletion whenever possible. `CHANGELOG.md` v2.0.0 entry stays in the tree post-revert as historical record; update it with the revert note in the same commit.
- [ ] **Recovery time estimate:** ~1-2 hours (revert + allowlist restoration + tag coordination + production rollback deploy).
- [ ] **Data loss risk:** none in the repo. If production was deployed from the tagged image and the issue is a regression, standard rollback-to-previous-image applies (documented in `docs/deployment.md`).

---

## Section 5.5 — Structural Guards Inventory

This is the authoritative table of every AST-scanning / layer-scoped / meta-guard enforced on `make quality`. Pre-v2.0 guards (the 27 in root `CLAUDE.md` "Structural Guards" table) are represented as a single row for brevity; migration-specific guards each get their own row.

### Allowlist strategy legend

- **Empty** — guard is written with zero-entry allowlist; any violation fails immediately.
- **Captured→shrink** — guard is written with a frozen snapshot of current violations (the "capture"); baseline shrinks monotonically via the meta-guard; new violations fail immediately.
- **Frozen** — guard is written with a deliberately-fixed allowlist that is NOT expected to shrink (e.g., legitimate architectural carve-outs for a specific module or pattern). Any addition to the allowlist requires a paired FIXME and reviewer sign-off. (Pre-D3 examples included a `background_sync_service.py` carve-out for the `test_architecture_sync_bridge_scope.py` guard; post-D3 that guard is deleted and its carve-out is no longer relevant.)
- **Meta (no allowlist)** — guard enforces invariants over OTHER guards' allowlists; has no allowlist of its own.

### Meta-guards explanation

`test_structural_guard_allowlist_monotonic` (the meta-guard) enforces that every `Captured→shrink` allowlist in the repo is non-increasing across commits. It scans the diff of every `*_allowlist.py` / `allowlist.txt` / embedded `ALLOWED = {...}` set and fails if any set added entries. This is the mechanism that prevents allowlist drift. See row marked **meta** in the table.

### Guards table

| Guard name | Status | Introduced at | Test file | Allowlist strategy | Allowlist target | Inverts to |
|---|---|---|---|---|---|---|
| *(all 27 pre-v2.0 guards from root `CLAUDE.md`)* | Active pre-v2.0 | Various | Per root CLAUDE.md | Mostly `Captured→shrink` or `Empty` | Varies | N/A |
| `test_architecture_no_flask_imports` | New | L0 | `tests/unit/architecture/test_architecture_no_flask_imports.py` | Captured→shrink | L2 zero | N/A |
| `test_architecture_handlers_use_sync_def` | New | L0 | `tests/unit/architecture/test_architecture_handlers_use_sync_def.py` | Empty (with small carve-out for OAuth `async def` callbacks, ~3-4 entries Frozen) | N/A | Inverts to `test_architecture_admin_handlers_async` at **L5b** (same PR as `SessionDep` alias flip to `AsyncSession`) |
| `test_architecture_admin_handlers_async` | New | Written L5a / enforced L5b | `tests/unit/architecture/test_architecture_admin_handlers_async.py` | Captured full allowlist at L5b; drains monotonically through L5c–L5e; empty at L5e exit | N/A | Swaps in when `test_architecture_handlers_use_sync_def` swaps out |
| `test_architecture_no_async_db_access` | New | L0 | `tests/unit/architecture/test_architecture_no_async_db_access.py` | Empty | N/A | Removed at L5b |
| `test_architecture_middleware_order` | New | L0 | `tests/unit/architecture/test_architecture_middleware_order.py` | Empty | N/A | N/A |
| `test_architecture_exception_handlers_complete` | New | L0 | `tests/unit/architecture/test_architecture_exception_handlers_complete.py` | Empty | N/A | N/A |
| `test_architecture_csrf_exempt_covers_adcp` | New | L0 | `tests/unit/architecture/test_architecture_csrf_exempt_covers_adcp.py` | Empty | N/A | N/A |
| `test_architecture_approximated_middleware_path_gated` | New | L0 | `tests/unit/architecture/test_architecture_approximated_middleware_path_gated.py` | Empty | N/A | N/A |
| `test_architecture_admin_routes_excluded_from_openapi` | New | L0 | `tests/unit/architecture/test_architecture_admin_routes_excluded_from_openapi.py` | Empty | N/A | N/A |
| `test_architecture_admin_routes_named` | New | L0 | `tests/unit/architecture/test_architecture_admin_routes_named.py` | Empty | N/A | N/A |
| `test_architecture_admin_route_names_unique` | New | L0 | `tests/unit/architecture/test_architecture_admin_route_names_unique.py` | Empty | N/A | N/A |
| `test_architecture_no_module_scope_create_app` | New | L0 (guard written), enforced L2 (after Flask removal) | `tests/unit/architecture/test_architecture_no_module_scope_create_app.py` | Empty | N/A | N/A |
| `test_architecture_scheduler_lifespan_composition` | New | L0 | `tests/unit/architecture/test_architecture_scheduler_lifespan_composition.py` | Empty | N/A | N/A |
| `test_architecture_a2a_routes_grafted` | New | L0 | `tests/unit/architecture/test_architecture_a2a_routes_grafted.py` | Empty | N/A | N/A |
| `test_architecture_form_getlist_parity` | New | L0 | `tests/unit/architecture/test_architecture_form_getlist_parity.py` | Empty | N/A | Retired at L2 (Flask gone, pattern no longer applicable) |
| `test_architecture_no_module_level_engine` | New | L0 | `tests/unit/architecture/test_architecture_no_module_level_engine.py` | Empty | N/A | N/A |
| `test_architecture_no_direct_env_access` | New | L0 | `tests/unit/architecture/test_architecture_no_direct_env_access.py` | Captured→shrink | L7 zero (pydantic-settings used everywhere post-L4) | N/A |
| `test_templates_url_for_resolves` | New | L0 | `tests/unit/architecture/test_templates_url_for_resolves.py` | Empty | N/A | N/A |
| `test_templates_no_hardcoded_admin_paths` | New | L0 | `tests/unit/architecture/test_templates_no_hardcoded_admin_paths.py` | Empty | N/A | N/A |
| `test_codemod_idempotent` | New | L0 | `tests/migration/test_codemod_idempotent.py` | Empty | N/A | Retired after L1a codemod execution |
| `test_oauth_callback_routes_exact_names` | New | L0 | `tests/unit/architecture/test_oauth_callback_routes_exact_names.py` | Empty (pins 3 exact names) | N/A | N/A |
| `test_trailing_slash_tolerance` | New | L0 | `tests/unit/architecture/test_trailing_slash_tolerance.py` | Empty | N/A | N/A |
| `test_template_context_completeness` | New | L0 | `tests/unit/architecture/test_template_context_completeness.py` | Empty | N/A | N/A |
| `test_foundation_modules_import` | New | L0 | `tests/unit/architecture/test_foundation_modules_import.py` | Empty | N/A | N/A |
| `test_openapi_byte_stability` | New | L0 | `tests/migration/test_openapi_byte_stability.py` | Empty (byte-hash of OpenAPI snapshot) | N/A | N/A |
| `test_mcp_tool_inventory_frozen` | New | L0 | `tests/migration/test_mcp_tool_inventory_frozen.py` | Empty | N/A | N/A |
| `test_architecture_no_runtime_psycopg2` | New | L0 | `tests/unit/architecture/test_architecture_no_runtime_psycopg2.py` | Captured→shrink (3 import sites per Decision 2) | L7 shrinks only if background_sync_service.py sunsets | N/A |
| `test_architecture_get_db_connection_callers_allowlist` | New | L0 | `tests/unit/architecture/test_architecture_get_db_connection_callers_allowlist.py` | Frozen (1 file: `run_all_services.py`) | Frozen; changes require Decision 2 revisit | N/A |
| `test_architecture_dtos_at_boundary` | New | L4 | `tests/unit/architecture/test_architecture_dtos_at_boundary.py` | Captured→shrink | L7 zero | N/A |
| `test_architecture_no_new_sync_sites_post_l4` | New | L4 EXIT | `tests/unit/architecture/test_architecture_no_new_sync_sites_post_l4.py` | Frozen inventory (snapshot captured at L4 EXIT) | Frozen until L5e; retired at L5e exit | N/A |
| `test_architecture_factory_inherits_async_base` | New | L5a (Spike 4.25) | `tests/unit/architecture/test_architecture_factory_inherits_async_base.py` | Empty | N/A | N/A |
| `test_architecture_factory_no_post_generation` | New | L5a | `tests/unit/architecture/test_architecture_factory_no_post_generation.py` | Empty | N/A | N/A |
| `test_architecture_factory_in_all_factories` | New | L5a | `tests/unit/architecture/test_architecture_factory_in_all_factories.py` | Empty | N/A | N/A |
| `test_architecture_adapter_calls_wrapped_in_threadpool` | New | L5d2 | `tests/unit/architecture/test_architecture_adapter_calls_wrapped_in_threadpool.py` | Empty | N/A | N/A |
| `test_architecture_no_threading_thread_for_db_work` | New | L5d1 | `tests/unit/architecture/test_architecture_no_threading_thread_for_db_work.py` | Empty | N/A | N/A |
| `test_architecture_no_sse_handlers` | New | L5d4 | `tests/unit/architecture/test_architecture_no_sse_handlers.py` | Empty | N/A | N/A |
| `test_architecture_async_sessionmaker_expire_on_commit` | New | L5b | `tests/unit/architecture/test_architecture_async_sessionmaker_expire_on_commit.py` | Empty | N/A | N/A |
| `test_architecture_no_singleton_session` | New | L4 (ContextManager refactor) | `tests/unit/architecture/test_architecture_no_singleton_session.py` | Empty | N/A | N/A |
| `test_architecture_no_flask_caching_imports` | New | L0 (guard written), enforced L2 | `tests/unit/architecture/test_architecture_no_flask_caching_imports.py` | Empty post-L2 | N/A | N/A |
| `test_architecture_inventory_cache_uses_module_helpers` | New | L0 | `tests/unit/architecture/test_architecture_inventory_cache_uses_module_helpers.py` | Empty | N/A | Inverts to `test_architecture_inventory_cache_on_app_state` at L6 |
| `test_architecture_inventory_cache_on_app_state` | New | L6 | `tests/unit/architecture/test_architecture_inventory_cache_on_app_state.py` | Empty | N/A | Swaps in when helpers guard swaps out at L6 |
| `test_structural_guard_allowlist_monotonic` | New | L0 | `tests/unit/architecture/test_structural_guard_allowlist_monotonic.py` | Meta (no allowlist) | N/A | N/A |
| `test_architecture_exception_handlers_accept_aware` | New | L0 | `tests/unit/architecture/test_architecture_exception_handlers_accept_aware.py` | Empty | N/A | N/A |
| `test_architecture_flash_module_deleted` | New | L6 | `tests/unit/architecture/test_architecture_flash_module_deleted.py` | Empty | N/A | N/A |
| `test_architecture_no_logger_reconfig_outside_lifespan` | New | L4 (structlog wiring) | `tests/unit/architecture/test_architecture_no_logger_reconfig_outside_lifespan.py` | Empty | N/A | N/A |
| `test_architecture_no_asyncpg_in_alembic_env` | New | L5a (Spike 6) | `tests/unit/architecture/test_architecture_no_asyncpg_in_alembic_env.py` | Empty (per database-deep-audit recommendation: keep env.py sync) | N/A | N/A |
| `test_architecture_templates_no_script_root` | New | L0 | `tests/unit/architecture/test_architecture_templates_no_script_root.py` | Empty | N/A | N/A |
| `test_architecture_x_served_by_header_emitted` | New | L0 | `tests/unit/architecture/test_architecture_x_served_by_header_emitted.py` | Empty | N/A | Retired at L2 (feature flag gone) |
| `test_architecture_feature_flag_gate_active` | New | L0 | `tests/unit/architecture/test_architecture_feature_flag_gate_active.py` | Empty | N/A | Retired at L2 |
| `test_architecture_no_werkzeug_imports` | New | L0 | `tests/unit/architecture/test_architecture_no_werkzeug_imports.py` | Captured→shrink | L2 zero | N/A |
| `test_architecture_settings_from_pydantic_settings` | New | L4 | `tests/unit/architecture/test_architecture_settings_from_pydantic_settings.py` | Captured→shrink | L7 zero | N/A |
| `test_architecture_no_module_level_env_os_getenv` | New | L4 | `tests/unit/architecture/test_architecture_no_module_level_env_os_getenv.py` | Captured→shrink | L7 zero | N/A |
| `test_architecture_allowlist_fixme_coverage` | New | L0 | `tests/unit/architecture/test_architecture_allowlist_fixme_coverage.py` | Meta (no allowlist) | N/A | N/A |
| `test_architecture_logfire_configured_in_lifespan` | New | L6 | `tests/unit/architecture/test_architecture_logfire_configured_in_lifespan.py` | Empty | N/A | N/A |
| `test_architecture_no_opentelemetry_sdk_import` | New | L6 | `tests/unit/architecture/test_architecture_no_opentelemetry_sdk_import.py` | Empty | N/A | N/A |
| `test_architecture_flask_secret_key_dual_read_removed` | New | L7 | `tests/unit/architecture/test_architecture_flask_secret_key_dual_read_removed.py` | Empty (fires at L7) | N/A | N/A |
| `test_architecture_router_subdirs_canonical` | New | L6 | `tests/unit/architecture/test_architecture_router_subdirs_canonical.py` | Empty | N/A | N/A |
| `test_architecture_allowlists_empty_at_L7` | New | L7 | `tests/unit/architecture/test_architecture_allowlists_empty_at_L7.py` | Meta (no allowlist; asserts all `Captured→shrink` allowlists are empty) | N/A | N/A |

### Guard-lifecycle notes

- **Inverts to** — two guards encoding mutually-exclusive invariants across a migration layer boundary. The swap is atomic (same PR): e.g., `test_architecture_handlers_use_sync_def` is removed in the same L5b commit that adds `test_architecture_admin_handlers_async`. This pattern prevents a gap where neither guard is enforcing the invariant.
- **Retired** — guard is deleted (not just allowlist-emptied) because the invariant it enforced is no longer applicable post-layer (e.g., `test_architecture_form_getlist_parity` is Flask-specific and retires at L2).
- **Captured→shrink with L7 zero target** — the allowlist is initialized with current violations at introduction time, and the monotonic meta-guard enforces that the set only shrinks. At L7 the allowlist must be empty (`test_architecture_allowlists_empty_at_L7` meta-asserts this). Any FIXME(salesagent-xxxx) comment lingering in a `Captured→shrink` allowlist at L7 is a ship blocker.

### Cross-references

- Per-layer guard commit policy: §4.5 "Carve-out: structural-guard-addition commits"
- Goals-adherence matrix: §4.6 maps each guard row to the goal(s) it protects
- Structural-guard authoring skill: `.claude/skills/write-guard/SKILL.md`

---

## Section 6 — Post-migration verification (run after Wave 3 merges)

- [ ] Production traffic monitoring for 48 hours
- [ ] Error rate comparison vs pre-migration baseline (Datadog / logs)
- [ ] Admin UI latency p50 comparison vs pre-migration baseline
- [ ] Admin UI latency p99 comparison vs pre-migration baseline
- [ ] Docker image size delta reported to team (expected ~60-75 MB reduction — psycopg2 + libpq retained per D1/D2/D9)
- [ ] No 5xx spike in first 24h post-deploy
- [ ] Monitor JSON poll `/activity` response time (Decision 8 deleted the SSE activity stream).
- [ ] `SESSION_SECRET` cookie size observed < 3.5 KB across all real users
- [ ] Async SQLAlchemy is L5+ within v2.0; scoping kickoff needed after L2 ships (supersedes prior "separate v2.1 PR" and "already merged as Wave 4-5" plans — see async-pivot-checkpoint.md for history).
- [ ] **L4** REST routes `Annotated[T, Depends()]` ratchet scoped (part of FastAPI-native patterns)
- [ ] **L4** `require_tenant_access` `is_active` check scoped (breaking change OK on v2.0 branch)
- [ ] **L2** `FLASK_SECRET_KEY` hard-removal scoped (v2.0 breaking change aligned with cookie rename; moved from L7)
- [ ] **post-v2.0** nginx removal scoping kickoff scheduled (requires battle-testing)
- [ ] **post-v2.0** `Apx-Incoming-Host` IP allowlist (security hardening) ticket filed
- [ ] **post-v2.0** `/_internal/reset-db-pool` auth hardening ticket filed
- [ ] v2.2 multi-worker scheduler lease design ticket filed
- [ ] All 6 companion notes files archived to `.claude/notes/archive/flask-to-fastapi/` OR retained as historical reference (see Section 7)
- [ ] `feat/v2.0.0-flask-to-fastapi` branch deleted after successful merge + 1 week
- [ ] Auto-memory `flask-to-fastapi-migration-v2` entry marked complete

---

## Section 6.5 — Monitoring & Alert Runbook

> **Who reads this:** On-call engineers during migration cutovers (L1a flag flip, L2 Flask removal, L5b SessionDep alias flip). Ops staff setting up Datadog/Grafana dashboards. PR reviewers validating that each layer has appropriate observability before entry.
>
> **What this covers:** The `admin-migration-health` dashboard spec, 8 alert rules with severity + action, per-layer entry-gate observability checklist, on-call handoff table, post-incident procedure, and per-layer instrumentation work items.

### Dashboard: `admin-migration-health`

Single dashboard, tab-per-concern. Lives in Datadog (or equivalent). Owned by the platform team for duration of v2.0 migration; deleted at L7 exit + 30 days.

**Traffic panels:**
- Request rate (req/s) by route prefix: `/admin`, `/mcp`, `/a2a`, `/api/v1`, `/healthz`, `/readyz`
- 5xx rate by route prefix
- 429 rate (rate-limit rejections) by endpoint
- p50 latency by route prefix
- p99 latency by route prefix
- Request-size distribution (heatmap) — detect cookie-bloat before it trips the size guard

**Migration-specific panels:**
- Flask catch-all hits per minute — **must be 0 post-L2**. Alert if non-zero.
- `X-Served-By: fastapi` ratio — watches traffic split during L1 feature-flag rollouts; target 100% before L2 entry.
- `/readyz` probe traffic and fail rate
- 503 rate on `/readyz` — detects readiness flapping (multiple fail→pass cycles in 5min window)
- Alembic-head-mismatch count (from `/readyz` body parse) — detects deploy-without-migrate

**Capacity panels:**
- DB pool stats (from `/health/db`): size / checked_in / checked_out / overflow
- anyio threadpool stats (from `/health/pool`): total_tokens / borrowed_tokens
- Memory per container, p50/p99
- Container restart rate

**Auth panels:**
- OAuth init→callback p99 latency (login-flow SLA)
- Auth failure rate (401s from `UnifiedAuthMiddleware`)
- CSRF rejection rate (from `CSRFOriginMiddleware`)
- Session cookie size distribution — leading indicator for §11.33 budget breaches

### Alert rules

| Rule name | Condition | Severity | Action | Runbook |
|-----------|-----------|----------|--------|---------|
| `MigrationHigh5xx` | 5xx rate > 1% of requests over 5min on `/admin/*` | **PAGE** | Check recent deploy. If L1 flag-flip, flip back. If L2+, consider rollback. | §5 rollback |
| `MigrationHighP99` | p99 latency > 2× baseline-sync.json over 15min on `/admin/*` | NOTIFY | Capture `/health/pool` and `/health/db` snapshots. File regression ticket. | §6.5 post-incident |
| `FlaskCatchallHit` | Flask catch-all hit count > 0 over 1min (post-L2) | **PAGE** | URGENT: routes missing from FastAPI surface. Identify which path, add router. | §5 L2 rollback |
| `ReadinessFlapping` | `/readyz` returns 503 then 200 within 2min, ≥ 3 times in 15min | NOTIFY | Likely DB blip or migration check failure. Check DB status; verify alembic head match. | docs/deployment/health-checks.md |
| `DbPoolSaturated` | `checked_out / size` > 0.9 for 5min | NOTIFY | Investigate long-running queries. Check `/health/db`. Consider pool size bump. | docs/deployment/db-pool.md |
| `ThreadpoolSaturated` | `borrowed_tokens / total_tokens` > 0.9 for 5min | NOTIFY | Adapter threadpool wrap (Decision 1) exhausted. Check ADCP_THREADPOOL_TOKENS. | foundation-modules §11.14 |
| `OauthSlow` | OAuth callback p99 > 5s over 10min | **PAGE** | Login flow degraded. Check Google OAuth status. Check `Authlib` token-exchange latency. | §5 L1b rollback |
| `AuthFailureSurge` | 401 rate > 3× baseline over 5min | NOTIFY | Possible credential stuffing OR legit auth breakage. Check rate-limit 429 rate correlation. | foundation-modules §11.32 |

### Per-layer entry gate — observability checklist

Each layer entry gate requires the following observability artifacts in place BEFORE the layer PR lands:

**L0 entry:**
- [ ] `/metrics` endpoint scaffolded (Prometheus-format, no actual metrics yet — placeholder)
- [ ] `X-Served-By` header wired to emit `fastapi` or `flask` per request path
- [ ] Dashboard `admin-migration-health` created, at least Traffic tab populated

**L1a entry:**
- [ ] `X-Served-By` ratio panel live on dashboard
- [ ] Feature-flag dashboard panel showing rollout percentage

**L1b entry:**
- [ ] OAuth init→callback latency panel live
- [ ] Auth failure panel live
- [ ] Alert `OauthSlow` configured

**L1c / L1d entry:**
- [ ] Per-router 5xx and latency panels for newly-ported routers
- [ ] Response-fingerprint-mismatch panel (from `tests/migration/dual_stack_client.py`)

**L2 entry:**
- [ ] `flask_catchall_hits` counter exposed on `/metrics` (emitted by WSGIMiddleware wrapper before deletion)
- [ ] Alert `FlaskCatchallHit` configured (active post-L2)
- [ ] `/healthz` + `/readyz` panels live (per §11.31)
- [ ] Rate-limit 429 panels live (per §11.32)
- [ ] Session-cookie-size distribution panel live (per §11.33)

**L5a entry:**
- [ ] DB pool saturation panel live
- [ ] Threadpool saturation panel live
- [ ] Alert `DbPoolSaturated` + `ThreadpoolSaturated` configured

**L5d3 entry:**
- [ ] Per-repository async/sync mix panel live (detects mis-converted call sites)

### On-call handoff table

All values `[TBD]` pending assignment per §6 Ownership & Bus-Factor (see `.claude/notes/flask-to-fastapi/CLAUDE.md` Ownership section). Populate before L0 sprint kickoff.

| Layer | Primary on-call | Backup on-call | Pager policy | Notes |
|-------|----------------|----------------|--------------|-------|
| L0 | [TBD] | [TBD] | Business hours only | Pure-addition, low risk |
| L1a | [TBD] | [TBD] | 24/7 during bake | Middleware stack goes live |
| L1b | [TBD] | [TBD] | **24/7 + escalation to platform lead** | OAuth flip — user-visible |
| L1c | [TBD] | [TBD] | Business hours | Low-risk HTML |
| L1d | [TBD] | [TBD] | 24/7 during bake | High-risk HTML + APIs |
| L2 | [TBD] | [TBD] | **24/7 + 48h bake pager** | Flask removal — irreversible |
| L3 | [TBD] | [TBD] | Business hours | Test-harness modernization |
| L4 | [TBD] | [TBD] | Business hours | FastAPI-native refinement |
| L5a | [TBD] | [TBD] | Business hours | Spikes only |
| L5b | [TBD] | [TBD] | 24/7 during bake | Async alias flip |
| L5c | [TBD] | [TBD] | 24/7 during bake | 3-router async pilot |
| L5d1-d5 | [TBD] | [TBD] | 24/7 during each bake | Bulk async conversion sub-PRs |
| L5e | [TBD] | [TBD] | 24/7 during bake | Final async sweep |
| L6 | [TBD] | [TBD] | Business hours | Native refinements |
| L7 | [TBD] | [TBD] | **24/7 + escalation** | Release layer |

### Post-incident procedure

If an alert fires OR a post-deploy regression is detected:

1. **Correlate** — capture `/health/db`, `/health/pool`, `/readyz` snapshots plus recent request-log samples. Attach to incident ticket.
2. **Pause** — hold any in-flight layer rollouts (freeze the feature flag at current split; do NOT advance).
3. **Rollback or forward** — consult §5 per-wave rollback procedure. If the incident is deploy-related, revert the deploy. If it's a flag-rollout issue, flip the flag back. If it's a latent bug, file a hotfix.
4. **Postmortem** — write up within 48h using this template:
   - Summary (1 paragraph)
   - Timeline (detection → mitigation → resolution)
   - Root cause
   - Why existing guards didn't catch it (structural guards, tests, observability)
   - Action items (new guard? new test? new alert? plan update?)
5. **Plan update** — if action items affect the migration plan, file them as checklist updates against this file. PR both the postmortem and the plan update together.

### Instrumentation work items per layer

**L0:**
- [ ] `/metrics` placeholder endpoint (no real metrics, just scaffold for future rules)
- [ ] `X-Served-By` response header middleware
- [ ] Datadog (or equivalent) dashboard `admin-migration-health` created

**L2:**
- [ ] `flask_catchall_hits` counter — emitted by the WSGIMiddleware wrapper BEFORE it's deleted, so we have a zero-ratio assertion across the deletion window
- [ ] `/healthz` + `/readyz` + `/health/db` + `/health/pool` wired per §11.31
- [ ] Rate-limit 429 metric per §11.32
- [ ] Cookie-size distribution metric per §11.33 (from `SessionMiddleware` wrap)

**L5a:**
- [ ] anyio threadpool exporter (`total_tokens`, `borrowed_tokens` → Prometheus)
- [ ] **Spike 8 decision artifact** — `spike-decision.md` committed at L5a EXIT with pass/fail per technical spike (1, 2, 3, 4, 4.25, 4.5, 5, 5.5, 6, 7), `baseline-sync.json` reference, 9-open-decision resolutions, and final go/no-go call. Spike 8 IS the decision gate, not a separate technical spike. See `CLAUDE.md` §"v2.0 Spike Sequence" canonical table. 11 total pre-L5b work items = 10 technical spikes + 1 decision gate.

**L5d (any sub-PR):**
- [ ] DB pool exporter (`size`, `checked_in`, `checked_out`, `overflow` → Prometheus)

**L5e:**
- [ ] Per-router async/sync counter (detects mis-converted call sites during bulk conversion)

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

## Section 8 — Known tech debt deferred beyond L2

These items are **intentionally NOT in scope for L0-L4**. They are referenced here so nothing is forgotten. Full detail in `flask-to-fastapi-deep-audit.md` Section 7 table.

### L5+ scope (after L2 Flask removal stabilizes)

- [ ] **Async SQLAlchemy** — L5+ within v2.0 (supersedes prior "separate v2.1 PR" and "v2.0 Waves 4-5" plans — see async-pivot-checkpoint.md for history). Convert `create_engine` → `create_async_engine`, `Session` → `AsyncSession`, all repositories to `async def`, ~100+ files affected. Detail: `async-pivot-checkpoint.md` §3 for target state; `flask-to-fastapi-migration.md` §18 for wave execution; deep audit §1.4 for Option A rationale.
- [ ] **Drop nginx entirely** — ~30 MB image savings; uvicorn `--proxy-headers` + tiny Starlette middleware covers all nginx responsibilities. Detail: `flask-to-fastapi-deep-audit.md` §4.1.
- [ ] **Ratchet REST routes to `Annotated[T, Depends()]`** — 14 route signatures in `src/routes/api_v1.py` currently use `= resolve_auth` default-value style. Add guard `test_architecture_rest_uses_annotated.py`. Detail: `flask-to-fastapi-deep-audit.md` §4.2.
- [ ] ~~Remove `FLASK_SECRET_KEY` dual-read~~ — **moved to L2** per v2.0 breaking-change alignment with cookie rename. See Wave 3 / L2 work items for the hard-remove checkbox; this line is retained only as a historical pointer.
- [ ] **`/_internal/reset-db-pool` auth hardening** — pre-existing weakness; endpoint is only env-var gated (`ADCP_TESTING=true`). Detail: `flask-to-fastapi-deep-audit.md` §7 R9.
- [ ] **`require_tenant_access` `is_active` check** — pre-existing latent bug in Flask (Wave 0 `CurrentTenantDep` already filters `is_active=True`; L5+ cleans up the dead Flask helper)
- [ ] **Structured logging (structlog / logfire) swap-in** — clean integration now that Flask is gone; `logfire` already in deps. Detail: `flask-to-fastapi-deep-audit.md` §4 opportunity list.

### v2.2 scope (requires additional design)

- [ ] **Multi-worker scheduler lease design** — today's webhook and media-buy-status schedulers are single-worker singletons. Multi-worker requires Postgres advisory lock OR separate scheduler container. Detail: `flask-to-fastapi-deep-audit.md` §3.1.
- [ ] **`Apx-Incoming-Host` IP allowlist** — security hardening for the Approximated external-domain middleware. No client-side spoofing today because Fly.io terminates externally, but explicit allowlist is defensive. Detail: `flask-to-fastapi-deep-audit.md` §7 Y8.

### v2.1 deferred tech debt (filed during v2.0 pre-L0)

The following were considered for v2.0 and explicitly deferred to v2.1:

- **Drop nginx reverse proxy for multi-tenant routing.** `TenantSubdomainMiddleware` + Starlette Host matching can replace 574 LOC of nginx .conf templating. Scope ~4-5 days + 2 days bake. Deferred: needs battle-testing; would eat the `background_sync_service` async rearchitect budget at L5d1 (D3).

- **Adapter async rewrite.** `googleads==49.0.0` depends on `suds-py3` (sync SOAP); rewriting 4 adapters (GAM, Mock, Kevel, Triton) on async httpx is ~30 engineer-days of vendor-driven work. Decision 1 Path B (sync + threadpool wrap) is the v2.0 answer. File v2.1 ticket at L7.

- **DatabaseConnection + fork-safety path elimination.** `scripts/deploy/run_all_services.py` PID-1 orchestrator pattern is the reason raw psycopg2 is kept (Decision 2). Rewriting to single-process uvicorn entrypoint is out of v2.0 scope. File v2.1 ticket at L7.

- **Multi-worker scheduler lease design.** v2.0 ships single-worker per CLAUDE.md:133. DB-backed lease or advisory-lock scheme is a v2.1 feature (~5-8 days).

- **Redis backend for SimpleAppCache.** In-process TTLCache is the v2.0 answer; `CacheBackend` Protocol preserves swap path to Redis in v2.1 without API changes at call sites.

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
   - §18 — Async SQLAlchemy absorbed into v2.0 at L5-L7 (pointer section; canonical content lives in `execution-plan.md` L5 and `flask-to-fastapi-foundation-modules.md` §11.18–§11.27)
   - §19 — Natural flow changes
   - §21 — Verification strategy
2. **`flask-to-fastapi-execution-details.md`** (1,142 lines) — per-wave detail
   - Part 1 — per-wave execution with A (acceptance), B (files), C (risks), D (rollback), E (merge conflicts), F (time), G (entry/exit) for each wave
   - Part 2 — 28-assumption verification recipes
   - Part 3 — structural guard AST patterns, integration test templates, Playwright e2e test plan, benchmark harness, `scripts/check_coverage_parity.py` automation
3. **`flask-to-fastapi-foundation-modules.md`** (2,507 lines) — full code for 11 foundation modules with tests and gotchas
4. **`flask-to-fastapi-worked-examples.md`** (2,790 lines) — 5 real Flask-blueprint → FastAPI-router translations (OAuth, OIDC, file upload, JSON-poll `/activity` pattern (supersedes prior SSE example — Decision 8 deleted SSE), products form)
5. **`flask-to-fastapi-adcp-safety.md`** (412 lines) — first-order AdCP boundary audit, 8 action items, verdict CLEAR
6. **`flask-to-fastapi-deep-audit.md`** (787 lines) — deep 2nd/3rd-order audit, 6 BLOCKERS, 20 RISKS, 40+ OPPORTUNITIES

If a reader only reads this checklist, they should not miss anything critical — every blocker, every risk, every action item from the six companion documents is representable as a checkbox here.
