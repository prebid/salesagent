# ⚠️ BLOCKER 4 RESOLUTION HAS PIVOTED — READ THIS FIRST

> **ASYNC DEFERRED TO PHASE 4+ WITHIN v2.0 (2026-04-12).** The async pivot described in this document has been reversed for Phases 0-3. v2.0 ships with sync admin handlers for Phases 0-3 (the original Option C from the deep audit). This document is preserved as the Phase 4+ async migration roadmap within v2.0. Do NOT implement any content from this file during Phases 0-3 — it will be the starting point for Phase 4+.

**Date:** 2026-04-11
**Author:** Claude + user directive
**Status:** Checkpoint before context compaction

## 1. The pivot

**User directive (2026-04-11):** go fully async in v2.0.0. No separate follow-on for async SQLAlchemy — absorb it into v2.0 as Phase 4+ within v2.0. Verification that this does not impact the AdCP schema is a hard requirement; the user explicitly said "as long as we don't break adcp schema and can't see how we would."

**One-sentence rationale:** a greenfield FastAPI team in 2026 writes fully async code end-to-end; the sync `def` compromise from deep-audit Blocker #4 Option C was a scope-reduction hack, and going fully async (Option A) produces a cleaner end state, matches modern FastAPI idiom, fixes a pre-existing latent bug in `src/routes/api_v1.py` as a side effect, and eliminates the entire async-migration follow-on from the roadmap.

**AdCP boundary (verified — hard requirement from user):**
- Schema / data shape: **unchanged** (sync vs async is code-style, not wire format)
- Response model structure: **unchanged**
- MCP tool signatures: **unchanged** (FastMCP supports both sync and async tools; converting is internal)
- A2A protocol messages: **unchanged**
- REST endpoint bodies: **unchanged**
- OpenAPI surface: **unchanged**
- Auth context (`ResolvedIdentity`, session cookies): **unchanged**
- `AdCPError` exception hierarchy: **unchanged**
- Webhook payload shapes (`create_a2a_webhook_payload`, `create_mcp_webhook_payload`): **unchanged**
- Protocol-level concurrency guarantees: **improved** (scoped_session latent bug fixed)

The pivot is a purely internal implementation change. No external AdCP consumer sees any wire-format difference. Verified against the AdCP safety audit's file classification table in `flask-to-fastapi-adcp-safety.md` §1.

## 2. What is NOW stale in the plan files (do NOT implement this language)

Each line below is content that the fresh session's opus agents must rewrite from "sync def" to "async def with full async SQLAlchemy":

### `flask-to-fastapi-migration.md`

- §2.8 Blocker 4 paragraph (line ~122) — says "flip the plan's default from async def to def (sync)" — REVERSE
- §2.8 "Plan defaults that change" bullet #1 — says "Admin handler default flips to def (sync)" — REVERSE
- §11.1 `render()` wrapper example — replace `with get_db_session()` with `async with get_db_session()` pattern
- §13.1 `list_accounts` worked example — shows sync `def`, should be `async def` calling `await uow.accounts.list_all(status=status)`
- §13.2 `create_account_form` and `create_account` — same
- §13.3 `change_status` — should be `async def` (already is, leave it)
- §16 assumption #4 — says "Admin handlers async def + run_in_threadpool" — should say "Admin handlers async def + async SQLAlchemy; run_in_threadpool only for truly blocking work"
- §17 debatable surface #2 — says "Sync SQLAlchemy stays sync" — REVERSE: "Full async SQLAlchemy in v2.0"
- §18 "Future Work: Async SQLAlchemy" — DELETE this section; the work is now in Phase 4+ within v2.0

### `flask-to-fastapi-foundation-modules.md`

- §11.1 `templating.py` render() wrapper — examples are framework-agnostic but any `with get_db_session()` in doc prose needs updating
- §11.4 `deps/auth.py` — all dep functions (`get_admin_user`, `get_current_tenant`, `require_super_admin`) should be `async def` with `async with get_db_session()` bodies
- Tests shown in §11.1 Tests section — update fixtures to async

### `flask-to-fastapi-worked-examples.md`

- Conventions list at line ~15 — says "All sync SQLAlchemy wrapped in run_in_threadpool" — REVERSE: "Full async SQLAlchemy; no run_in_threadpool for DB work"
- §4.1 `login`, `logout` — may need async def (check awaits)
- §4.3 `upload_favicon` — already async def (required by UploadFile.read()); internal `run_in_threadpool` for DB calls becomes `await async_uow.*`
- §4.5 `add_product_form`, `add_product`, `_rerender_with_error` — async def with async DB

### `flask-to-fastapi-execution-details.md`

- Wave 0 criterion 6 (line 22) — "module-level sync def dep functions" — REVERSE to async
- Group 1 assumption #4 (line 512) — same
- Guard description `test_architecture_admin_async_signatures.py` (line 752) — this was the ORIGINAL (pre-pivot to sync) guard. Its direction was correct for full async. It asserts every handler IS async def. KEEP this guard, DELETE the sync-def guard that the sync pivot introduced.
- Wave 1/2/3 entry/exit criteria — any mention of `run_in_threadpool`, `sync def`, or deferred async needs reversing
- Part 2 verification recipes — assumption #3 (sync SQLAlchemy overhead) is no longer applicable
- Benchmark harness description — measure async vs old-sync, not threadpool overhead

### `flask-to-fastapi-deep-audit.md`

- §1.4 Blocker 4 — **major rewrite**. Option A is chosen, not Option C. The Option C "sync def default" text is superseded. Option A becomes the primary resolution. The "why defer" justification is deleted (not deferring — it's Phase 4+ within v2.0).
- §3.7 MCP scheduler lifespan-composition — still valid but now with async DB access inside `lifespan_context`
- §7 Summary Table B4 row — change from "sync def default" to "full async SQLAlchemy + async handlers"

### `flask-to-fastapi-adcp-safety.md`

- §10.2 MCP scheduler invariant — still valid; mention that schedulers use async DB access now
- Any mention of async as deferred — REVERSE to "absorbed into Phase 4+ within v2.0"

### `implementation-checklist.md`

- Section 1.2 "Architectural decisions" — first bullet is "Admin handler default: sync def (not async def)" — REVERSE
- Section 2 Blocker 4 — every bullet about sync def is superseded
- Section 4 Wave 0 — any sync def-related criterion
- Section 4 Wave 2 — any deferred async
- Section 8 — async SQLAlchemy item moves into Phase 4+ within v2.0
- Wave 0 structural guards — `test_architecture_admin_sync_db_no_async.py` is WRONG direction, delete it; the correct guard is `test_architecture_admin_routes_async.py` (asserts every admin handler IS async def)

### Folder `CLAUDE.md`

- Critical Invariant #4 — rewrite (pivot marker added alongside)
- Migration conventions "sync def" bullet — reverse
- "deferred items" — move async SQLAlchemy into Phase 4+ within v2.0

## 3. New target state

### Database layer (`src/core/database/database_session.py`)

> **⚠️ CORRECTED 2026-04-12 (3-round verification audit):** The code block below was stale — showed module-level engine creation. The correct pattern is lifespan-scoped engine per Agent E Category 1, with `autoflush=False` added per async best practice.

```python
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from contextlib import asynccontextmanager
from typing import AsyncIterator

# Engine is created in lifespan, NOT at module level — prevents pytest-asyncio
# event-loop leak (Risk Interaction B) and ensures proper shutdown via dispose().
async def database_lifespan(app):
    """Create engine at startup, dispose at shutdown."""
    url = connection_string.replace("postgresql://", "postgresql+asyncpg://")
    engine = create_async_engine(
        url,
        echo=False,
        pool_size=20,
        max_overflow=10,
        pool_pre_ping=True,
        pool_recycle=3600,
        # statement_timeout via connect_args (NOT event listener — asyncpg has no
        # dbapi_conn.cursor(), so the psycopg2-era event listener crashes).
        connect_args={"server_settings": {"statement_timeout": "30000"}},
    )
    app.state.db_engine = engine
    app.state.session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False, autoflush=False,
    )
    yield
    await engine.dispose()

@asynccontextmanager
async def get_db_session() -> AsyncIterator[AsyncSession]:
    """Request-scoped session. Used by the SessionDep DI factory."""
    session = _get_session_factory()()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()
```

Key design decisions in this code block:
- **`expire_on_commit=False`** — critical for async sessions; prevents post-commit auto-expire that would trigger `MissingGreenlet` on attribute access.
- **`autoflush=False`** — prevents surprise writes under the async model; flushes are explicit via `await session.flush()`.
- **Lifespan-scoped engine** (not module-level) — asyncpg's connection pool binds to the event loop at creation time; module-level creation causes `RuntimeError: Event loop is closed` in pytest-asyncio with function-scoped loops.
- **`connect_args` for `statement_timeout`** — replaces the psycopg2-era `@event.listens_for("connect")` handler which calls `dbapi_conn.cursor()` (doesn't exist on asyncpg connections; would crash on every new connection).

### Repository pattern

All repositories become async:

```python
class AccountRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, account_id: str, tenant_id: str) -> Account | None:
        result = await self.session.execute(
            select(Account).filter_by(account_id=account_id, tenant_id=tenant_id)
        )
        return result.scalars().first()

    async def list_all(self, tenant_id: str, status: str | None = None) -> list[Account]:
        stmt = select(Account).filter_by(tenant_id=tenant_id)
        if status:
            stmt = stmt.filter_by(status=status)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
```

### Repository pattern (no UoW) — pivoted 2026-04-11 per Agent E Category 3

FastAPI's request-scoped session IS the unit of work. Repositories take
`session: AsyncSession` in `__init__`. Multiple repositories in the same
request share one session via `Depends(get_session)` caching. Transactions
commit on normal handler return, roll back on exception — all handled by
the `get_session` DI factory.

**No `async with UoW()` anywhere in handlers. No `AccountUoW` class.** The
UoW abstraction was a Flask-era clean-architecture pattern that re-implements
functionality FastAPI already provides for free. Under the pivot, a 2026
greenfield FastAPI team does not write UoW classes — they write repositories
that take an injected session.

> **⚠️ CORRECTED 2026-04-12 (pattern consistency audit):** Repositories return **ORM model objects**, NOT DTOs. This is consistent with the existing codebase where all repos (`AccountRepository`, `MediaBuyRepository`, etc.) return ORM objects. DTO conversion happens in the **handler layer**, not the repository — this keeps repositories as pure data-access and avoids mixing concerns. See `CLAUDE.md` Critical Pattern #3.

```python
# src/core/database/repositories/accounts.py
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from src.core.database.models import Account

class AccountRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_all(self, tenant_id: str, *, status: str | None = None) -> list[Account]:
        stmt = select(Account).filter_by(tenant_id=tenant_id)
        if status:
            stmt = stmt.filter_by(status=status)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
```

### Handler + Dep chain pattern

```python
# src/admin/routers/accounts.py
async def get_account_repo(session: SessionDep) -> AccountRepository:
    return AccountRepository(session)

AccountRepoDep = Annotated[AccountRepository, Depends(get_account_repo)]


@router.get("/tenant/{tenant_id}/accounts", name="admin_accounts_list_accounts")
async def list_accounts(
    tenant_id: str,
    request: Request,
    tenant: CurrentTenantDep,
    repo: AccountRepoDep,    # ← repository Dep, NOT UoW context manager
    status: Annotated[str | None, Query()] = None,
) -> HTMLResponse:
    accounts = await repo.list_all(tenant_id, status=status)
    # DTO conversion happens HERE in the handler, NOT in the repository.
    # Repos return ORM objects (consistent with existing codebase pattern).
    dtos = [AccountDTO.from_orm(a) for a in accounts]
    return render(request, "accounts_list.html", {
        "tenant_id": tenant_id, "tenant": tenant, "accounts": dtos,
    })
```

Handler body is 100% business logic. No session management. No `async with`. No `await uow.*`. The session is injected via `Depends(get_session)`, committed automatically by the DI factory on normal return, and rolled back on exception. Multiple repositories in the same handler share ONE session (FastAPI caches dep results per-request), so cross-repository transactions are implicit.

### Test harness (updated per Agent E Category 14)

All tests use `httpx.AsyncClient(transport=ASGITransport(app=app))` with `app.dependency_overrides[get_session] = lambda: session`. `TestClient` (sync) is deprecated for integration tests under async lifespan — it spawns its own event loop in a thread and conflicts with async lifespan state stored on `app.state`.

### `_impl` functions

All `src/core/tools/*.py` `_impl` functions become `async def`. Some already are (e.g. `_get_products_impl`, `create_media_buy_raw`); a handful are still sync (`list_creative_formats_raw`, `list_accounts_raw`). All become async.

### Driver change

> **⚠️ CORRECTED 2026-04-11 (Decisions 1/2/9):** `psycopg2-binary` is **RETAINED** alongside `asyncpg`, NOT removed. Three sync paths require it: Decision 1 Path B sync session factory, Decision 2 pre-fork orchestrator health check, Decision 9 sync-bridge. `types-psycopg2` also RETAINED. Removal deferred to Phase 4+ within v2.0.

`pyproject.toml`:
- **KEEP** `psycopg2-binary>=2.9.9` (retained per D1/D2/D9)
- **ADD** `asyncpg>=0.30.0` (alongside, not replacing)
  - **Fallback:** `psycopg[binary,pool]>=3.2.0` if Spike 2 (driver compat) fails.
- **KEEP** `types-psycopg2>=2.9.21.20251012` (retained per D1/D2/D9)
- (Keep `sqlalchemy>=2.0.0`)

`DATABASE_URL` env var translation:
- `postgresql://user:pass@host/db` → rewritten at engine construction to `postgresql+asyncpg://user:pass@host/db`

### Alembic migrations

> **⚠️ CORRECTED 2026-04-11 (Database deep-audit DB-4):** Keep `alembic/env.py` **sync with psycopg2**. Do NOT rewrite to async. Alembic gains nothing from running async — migrations are serial, single-connection operations. All 161 existing migrations use sync patterns (`op.*`, `connection.execute()`, `sa.inspect()`) that work under the `run_sync` greenlet bridge, but rewriting env.py adds risk for zero benefit. psycopg2 is already retained for Decisions 1/2/9. **This eliminates Spike 6 scope** — the spike becomes "add `render_item` hook for JSONType + advisory lock for multi-container safety" (~0.5 day) instead of "full async env.py rewrite + validation" (~1 day).

The async adapter pattern below is preserved for reference ONLY — do NOT implement:

```python
# STALE — kept for reference. Use sync env.py with psycopg2 per DB-4.
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine

async def run_migrations_online():
    connectable = create_async_engine(_ASYNC_URL)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()

def run_migrations():
    asyncio.run(run_migrations_online())
```

Standard SQLAlchemy async alembic pattern. Well-documented.

### factory_boy

Needs async adapter. Options:
1. `factory-boy` has no native async support as of 2026-04-11. Workaround:
   - Keep ORM factories synchronous (they build Python objects)
   - `session.add()` + `await session.commit()` happens in test fixtures explicitly
2. OR use a thin custom `AsyncSQLAlchemyModelFactory` wrapper
3. OR move factory `.create()` into `await session.run_sync(...)` calls

The next session's opus agents should evaluate all three and pick. Preserves CLAUDE.md Pattern #8 (factory-boy ORM-first).

### Test harness (`tests/harness/_base.py`)

`IntegrationEnv.__enter__` currently binds sync sessions to factories. Needs async equivalent. Conversion:

```python
async def __aenter__(self):
    self._session = SessionLocal()
    for f in ALL_FACTORIES:
        f._meta.sqlalchemy_session = self._session
    return self
```

All tests using `IntegrationEnv` need `async with env:` instead of `with env:`.

All integration tests need `@pytest.mark.asyncio` (or the equivalent anyio marker).

### Schedulers (`src/core/main.py::lifespan_context`)

Already in an async context. Just update the scheduler bodies' DB calls to `async with get_db_session()`. No structural change to the lifespan composition.

### Adapters (Decision 1 Path B, 2026-04-11 resolution)

**Adapters stay sync under v2.0.** `src/adapters/base.py::AdServerAdapter` is UNCHANGED — methods remain sync `def`. Full async would require porting `googleads==49.0.0` off `suds-py3` (hard-sync SOAP, no async variant) and rewriting 4 `requests`-based adapters (~1500 LOC) for zero AdCP-visible benefit. Path B wraps adapter calls at the `_impl` boundary instead.

**Dual session factory.** `src/core/database/database_session.py` exports BOTH:
- `get_db_session()` — async, yields `AsyncSession`. Used by admin handlers, `_impl` functions, `_raw` wrappers, schedulers.
- `get_sync_db_session()` — sync, yields sync `Session`. Used by adapter code running inside `run_in_threadpool` worker threads, plus `AuditLogger._log_operation_sync` (internal). Pool sizing: `pool_size=5, max_overflow=10, pool_pre_ping=True, pool_recycle=3600`, statement_timeout=30s.

**Wrap pattern** at the 18 `_impl` adapter call sites:
```python
from starlette.concurrency import run_in_threadpool
# Adapter is sync; _impl is async; the wrap is at the boundary.
result = await run_in_threadpool(adapter.create_media_buy, request, packages, ...)
```

**`AuditLogger` split:**
- `_log_operation_sync(...)` — sync internal, uses `get_sync_db_session()`. Called by adapters from inside worker threads.
- `async def log_operation(...)` — async public wrapper, calls `await run_in_threadpool(self._log_operation_sync, ...)`. Called by `_impl` functions.
- 30 `_impl` call sites update to `await audit_logger.log_operation(...)`; adapter call sites use `self.audit_logger._log_operation_sync(...)`.

**Threadpool tune:** `anyio.to_thread.current_default_thread_limiter().total_tokens = 80` at lifespan startup (default 40 is too low for burst adapter load). Env-override via `ADCP_THREADPOOL_SIZE`.

**Structural guard:** `tests/unit/test_architecture_adapter_calls_wrapped_in_threadpool.py` — AST-walks `src/core/tools/`, `src/admin/blueprints/`, `src/admin/routers/`, `src/core/helpers/` for adapter method calls inside `async def` bodies. Each must be the first argument to a `run_in_threadpool(...)` call. `src/services/background_sync_service.py` is allowlisted (already in sync-bridge thread).

### ContextManager refactor (Decision 7, 2026-04-11 resolution)

**`src/core/context_manager.py` loses the class.** `ContextManager(DatabaseManager)` caches `self._session` on a process-wide singleton. Under `async_sessionmaker` on the single event-loop thread, every concurrent task shares the cached session → transaction interleaving. **`async_sessionmaker` does NOT fix this** because the singleton sits above the session factory. This is the scoped_session bug in singleton form and must be fixed explicitly by a refactor.

**Refactor shape:**
- Delete `class ContextManager`, delete `_context_manager_instance`, delete `get_context_manager()`, delete no-op `set_tool_state`
- Convert 12 public methods to module-level `async def` functions taking `session: AsyncSession` as first positional parameter
- Delete `DatabaseManager` entirely (only ContextManager subclassed it; 2 test-only subclasses in `test_session_json_validation.py` also deleted)
- `_send_push_notifications` fork at lines 727-755 collapses to a single `await service.send_notification(...)`

**7 production callers migrate:** `main.py:164-166` (delete dead variable), `mcp_context_wrapper.py` (delete module-load `_wrapper = MCPContextWrapper()`, open `session_scope()` via `_wrap_async_tool`), `media_buy_create.py` (3 calls), `media_buy_update.py` (**17 calls** — largest), `_workflow.py` (2 calls), `mock_ad_server.py` (2 sites, including `threading.Thread complete_after_delay` → `asyncio.create_task(_complete_after_delay)` with `async with session_scope()`), `operations.py` (1 site, admin conversion).

**Error-path gotcha:** raising an exception after `await update_workflow_step(session, step_id, status="failed", ...)` inside an outer `async with session_scope()` rolls back the error-status write too. Fix: use a SEPARATE `async with session_scope() as error_session:` inside the `except` block for error logging. Documented in §11.0.6 Gotchas.

**Structural guard:** `tests/unit/test_architecture_no_singleton_session.py` — 3 AST-scanning methods: (a) no session-typed class attributes outside `src/core/database/`, (b) no `_X_instance = None` + `get_X()` singleton-getter patterns, (c) no module-level `X = SomeManager()` instantiations.

**Validated by:** pre-Wave-0 Spike 4.5 (0.5-1 day, soft blocker). Fail action: refactor becomes dedicated Wave 4a sub-phase PR, not a gate failure.

### Background sync sync-bridge (Decision 9, 2026-04-11 resolution)

**New module: `src/services/background_sync_db.py` (~200 LOC).** Runs the multi-hour GAM inventory sync jobs via a SEPARATE sync psycopg2 engine, kept distinct from the dual engines in `database_session.py`. The service keeps its current `threading.Thread` shape; converting to `asyncio.create_task` would pin async-pool connections for hours, triggering `pool_recycle=3600` mid-session and Fly.io TCP keepalive expiry.

**Separate engine:**
- `application_name='adcp-salesagent-sync-bridge'` (distinguishable in `pg_stat_activity`)
- `pool_size=2, max_overflow=3, pool_pre_ping=True, pool_recycle=3600`
- `statement_timeout=600s` (10-minute budget for long GAM writes)
- Thread-safe lazy-init via `threading.Lock`
- `atexit` hook registered at engine construction time (NOT module load — lazy)
- `dispose_sync_bridge()` function for lifespan shutdown integration

**Total sync/async engine math:**
- Async engine (request path): `pool_size=15, max_overflow=25` = 40 peak connections
- Sync engine in `database_session.py` (Path B adapters + audit): `pool_size=5, max_overflow=10` = 15 peak
- Sync-bridge engine in `background_sync_db.py`: `pool_size=2, max_overflow=3` = 5 peak
- **Total: 60 peak connections** (within default `max_connections=100` with headroom)

**Shutdown ordering** in `src/core/main.py::lifespan_context.__aexit__`:
1. `await request_shutdown()` — signal background_sync threads via `_shutdown_event`
2. `await wait_for_shutdown(30.0)` — wait up to 30s for threads to drain
3. `dispose_sync_bridge()` — dispose sync-bridge engine
4. `await engine.dispose()` — dispose async engine
5. `engine.dispose()` on the Path-B sync session factory

**Scope guard:** `tests/unit/test_architecture_sync_bridge_scope.py` — ratcheting frozenset allowlist containing ONLY `src/services/background_sync_service.py`. Any PR adding a new importer requires explicit CLAUDE.md exception. 4 test methods enforce allowlist non-growth, file-exists check, importer-still-uses check, and "if allowlist empty then module must be deleted" sunset check.

**Sunset target Phase 4+ within v2.0.** When the phase-per-session async refactor lands (per `foundation-modules.md` §11.0.6 §G), the sync-bridge gets deleted alongside `psycopg2-binary` + `libpq-dev` + `libpq5`. Tracking issue: `salesagent-sync-bridge-sunset`.

**Validated by:** pre-Wave-0 Spike 5.5 (0.5 day, soft blocker). 4 test cases: (a) engine lifecycle, (b) MVCC bidirectional visibility, (c) 5 concurrent async requests + 1 sync thread no deadlock, (d) post-dispose connection leaks ≤1. Fail action: revert to Option A (asyncio task + single async session per sync), suboptimal but viable.

**Wave 3 flask-caching correction (bundled with Decision 9):** the "zero callers" claim was WRONG. 3 consumer sites exist at `inventory.py:874, 1133, background_sync_service.py:472`. The last is also the `from flask import current_app` ImportError blocker. Wave 3 replaces `flask-caching` with `src/admin/cache.py::SimpleAppCache` (cachetools.TTLCache wrapper on `app.state.inventory_cache`) BEFORE deletion. Deleting flask-caching outright would crash inventory pages + break background sync.

### Pre-fork orchestrator sync-DB path (Decision 2 deep-think 2026-04-11)

`scripts/deploy/run_all_services.py` is **PID 1** in the container. It runs a sync Python orchestrator that:
1. Validates env vars (line ~343)
2. Health-checks the DB via raw psycopg2: `get_db_connection()` at `:84` (`check_database_health`)
3. Runs migrations via `subprocess.run([sys.executable, "scripts/ops/migrate.py"], ...)` at `:207`
4. Schema-checks DB via raw psycopg2: `get_db_connection()` at `:135` (`check_schema_issues`)
5. Calls `init_db()` at `:175` — **⚠️ Risk #34, see §4 below**
6. Forks uvicorn into a CHILD subprocess via `subprocess.Popen([sys.executable, "scripts/run_server.py"])` at `:231` (run inside `threading.Thread(target=run_mcp_server, daemon=True).start()` at `:361`)
7. Parent loops on `time.sleep(1)` forever, monitoring stdout

**Critical: uvicorn's asyncio event loop lives in the CHILD interpreter, NOT the parent.** Audit 06's "loop collision" rationale for keeping `DatabaseConnection` was technically false — there is no shared loop to collide with. The deep-think analysis traced this via the actual `Popen` call at `run_all_services.py:231` and the `scripts/run_server.py:47` `uvicorn.run("src.app:app", ...)` invocation.

**The REAL reason `DatabaseConnection` (raw psycopg2) stays:** **fork safety.**
- The parent process MUST NOT initialize the **async SQLAlchemy engine** (Decision 1 main path) — if it did, asyncpg connection pool state would duplicate into the child fork via FD inheritance, corrupting two processes sharing the same PG socket.
- The parent process MUST NOT initialize the **Path B sync SQLAlchemy engine** (Decision 1 sync factory) — same problem with psycopg2 pooled connections.
- The parent process MUST NOT initialize the **sync-bridge engine** (Decision 9) — same problem.
- The parent process MAY open **transient raw psycopg2 connections** that are fully closed (FD released) before `Popen`. `DatabaseConnection.connect()` + `.close()` is the sanctioned shape for this.

**Therefore `src/core/database/db_config.py::DatabaseConnection` is KEPT for v2.0** as the one fork-safe pre-uvicorn DB path. Allowlisted in two structural guards:
- `tests/unit/test_architecture_no_runtime_psycopg2.py` — AST-walks every `src/**/*.py` for `import psycopg2`/`from psycopg2`, allowlist of 3 files (`db_config.py`, `database_session.py` IF it explicitly imports psycopg2 — verify during implementation, may not be needed since SQLAlchemy auto-detects from URL string, `background_sync_db.py`)
- `tests/unit/test_architecture_get_db_connection_callers_allowlist.py` — AST-walks every `src/**/*.py` and `scripts/**/*.py` for `Call` nodes invoking `get_db_connection`, allowlist of 1 file (`scripts/deploy/run_all_services.py`). Catches the failure mode where someone adds `DatabaseConnection` inside the runtime process.

**Wave 4 scope additions** (bundled with the structural guard PR):
- **Delete `scripts/deploy/entrypoint_admin.sh`** — dead shell code, unreferenced by Dockerfile/compose/fly.toml, still shell-imports psycopg2 in a subshell, calls non-existent `migrate.py`, imports `src.admin.server` (Wave 3 deletion target).
- **Migrate `examples/upstream_quickstart.py:137`** to `get_db_session()` — example is standalone async-capable. Leaves `DatabaseConnection` with exactly 2 callers in `run_all_services.py`.
- **Harden `DatabaseConnection.connect()`**: add `connect_timeout=10` (env-overridable via `DATABASE_CONNECT_TIMEOUT`) + `options="-c statement_timeout=5000"`. Prevents hanging DB from bricking container startup.

**Adjacent fork-safety concern (Risk #34, see §4):** `run_all_services.py:175` calls `src.core.database.database.init_db()` which under async pivot opens the SQLAlchemy async engine in the parent process. This is the **same bug class** as Decision 2 but at a different code path. Mitigation: either `init_db()` calls `await reset_engine()` in `finally`, OR `run_all_services.py:175` runs init via `subprocess.run([sys.executable, "-m", "scripts.setup.init_database"])` like migrations already do at `:207`. **Strongly prefer the subprocess pattern** — matches the existing migration pattern, no in-process state leak risk.

### SSE / long-lived connections — Decision 8 DELETE (2026-04-11 deep-think)

The codebase has exactly **one SSE endpoint**: `src/admin/blueprints/activity_stream.py:226-364`. Decision 8 deep-think confirmed it is **orphan code**:
- `templates/tenant_dashboard.html:972` literally says `// Use simple polling instead of EventSource for reliability`
- Template fetch-polls `/tenant/{id}/activity` (JSON) at 5s intervals (line 978 `setInterval(pollActivities, 5000)`)
- Zero `new EventSource(` exists anywhere in `templates/` or `static/`
- Only `/events` references: one integration smoke test probe (`test_admin_ui_routes_comprehensive.py:367-370`) and one docs line (`troubleshooting.md:74`)

**Wave 4 action:** DELETE the SSE route + generator + rate-limit state (`MAX_CONNECTIONS_PER_TENANT`, `connection_counts`, `connection_timestamps`, lines 22-24) + HEAD probe. Net: **−170 LOC, −3 unwritten test files, −1 pip dep (`sse_starlette`)**. Also delete the smoke test and docs reference.

**Two surviving routes** (`/activity` JSON poll + `/activities` REST API) convert mechanically: `def → async def`, `with get_db_session → async with get_db_session`, `db_session.scalars(stmt).all() → (await db_session.execute(stmt)).scalars().all()`. Additionally fix `api_mode=False → api_mode=True` on the `/activity` JSON poll route (pre-existing bug — JS `fetch` sees HTML 302 redirect on auth failure, never gets the 401 the template expects).

**Post-deletion:** the codebase has **zero** long-lived connection handlers, so Risk #29 (WebSocket/SSE holding pool connections) reduces to "no surface area." Structural guard `tests/unit/test_architecture_no_sse_handlers.py` asserts no function in `src/admin/routers/**` uses `EventSourceResponse` or returns `StreamingResponse` with `mimetype="text/event-stream"`, ratchet to zero tolerance after Wave 4.

**`get_recent_activities` retains per-call session lifetime** — each invocation opens its own `async with get_db_session()` block, formats `AuditLog` column data into dicts (no relationship access), and returns. Under `lazy="raise"` (Spike 1), the formatter accesses only columns (`audit_log.log_id`, `audit_log.timestamp`, `audit_log.details`, etc.), not relationships — safe.

**If SSE is ever needed again** for sub-5s activity latency, the correct shape is **WebSocket** (bidirectional, lower framing overhead), not SSE. Document this guidance in the surviving `activity_stream.py` file header.

## 4. 2nd and 3rd order risks — the fresh session MUST investigate these

**(#1 is the biggest unknown — do this audit FIRST before committing to v2.0 scope.)**

### Risk #1 — Lazy loading (BIGGEST UNKNOWN)

SQLAlchemy's `relationship()` attributes lazy-load by default. Under `AsyncSession`, any lazy load outside the async context raises `sqlalchemy.exc.MissingGreenlet` ("greenlet_spawn has not been called; can't call await_only() here"). This is a HARD FAILURE, not a warning.

**Action required (pre-Wave-0 spike):**
- `grep -rn 'relationship(' src/core/database/models/` — enumerate every relationship definition
- For each relationship, audit every access site in the codebase: `grep -rn '\.{relationship_name}' src/`
- Classify each access site as: (a) inside session scope → safe, (b) outside session scope → MUST convert to eager loading (`joinedload`, `selectinload`) or explicit refresh

**Mitigation options if audit reveals many out-of-scope accesses:**
- Set `expire_on_commit=False` (done in the session factory above) — handles post-commit access within the same task
- Use `selectinload(...)` in repository queries to eager-load relationships
- Rewrite the worst offenders to pass the session explicitly
- As a last resort: use `session.run_sync(lambda s: obj.relationship)` to force a sync eager load

**Estimated effort:** 1-3 days of audit + fix. This is the single biggest risk to the v2.0 scope absorbing async.

### Risk #2 — Driver change (`psycopg2` → `asyncpg`)

Different behaviors that could bite:
- **JSONB type codec**: asyncpg returns `dict`/`list` by default (like psycopg2 with custom codec). Verify `JSONType` custom column type still works.
- **UUID type**: asyncpg returns `uuid.UUID`, psycopg2 often returns `str`. Audit `.uuid` columns for type-sensitive code.
- **Interval type**: different timedelta handling.
- **Array types**: asyncpg returns `list`, psycopg2 behavior depends on codec config.
- **Transaction isolation**: default isolation level (READ COMMITTED) is the same but interactions with SAVEPOINTs differ.
- **Connection pool**: asyncpg has its own pool separate from SQLAlchemy's; pool size tuning may need adjustment.
- **LISTEN/NOTIFY**: if any code uses Postgres pub/sub, asyncpg has a different API.
- **COPY**: bulk import paths (if any) use different syntax.

**Action required:** test matrix — run the full test suite on a staging branch with `asyncpg` to catch driver-compat surprises. Probably 1-2 days of debugging.

### Risk #3 — pytest-asyncio / test infrastructure

`pytest-asyncio` (or anyio plugin) needs to be added to dev deps. Every test function that touches the DB becomes `async def`. Every fixture that produces a session becomes async. Impact:
- `tests/harness/_base.py` — `__enter__`/`__exit__` → `__aenter__`/`__aexit__`
- Every integration test — `def test_foo(...)` → `async def test_foo(...)`
- Many unit tests that currently use `integration_db` fixture
- `conftest.py` files — event loop scoping (function / session / module)
- `pytest-xdist` parallel execution — must use `worker_id`-scoped databases (already does, but verify)

**Estimated effort:** ~2 days for harness refactor + mechanical test-by-test async conversion. The mechanical part can be scripted (`ast.NodeTransformer` walking test files).

### Risk #4 — Alembic async

Standard pattern, but first time for the team. `alembic/env.py` rewrite is ~30 LOC. Migration scripts themselves DON'T need to be async (they run inside `do_run_migrations` which is sync). Main risk: migrations that use raw `op.execute(...)` with parameters — these work the same.

**Estimated effort:** 1 day.

### Risk #5 — `expire_on_commit=False` behavior change

Today's sync session has `expire_on_commit=True` (default), so accessing an object after commit triggers a refresh. Under async with `expire_on_commit=True`, the refresh would lazy-load → `MissingGreenlet` → crash. We MUST set `expire_on_commit=False` for async.

**Consequence:** some code may rely on post-commit refresh to pick up DB defaults (e.g. `created_at` columns). After the pivot, these fields are whatever the ORM set them to at insert time, not what Postgres computed. Audit needed.

**Action:** `grep -rn 'created_at\|updated_at' src/` and verify no code reads these fields after commit without explicit refresh.

### Risk #6 — Connection pool tuning under load

Async connection pools behave differently than sync pools. Under high concurrency:
- Sync: limited by threadpool size (default 40 in AnyIO)
- Async: limited by SQLAlchemy pool size (default 5 + 10 overflow = 15)

Default async pool may be SMALLER than threadpool capacity. Need to bump `pool_size` for production load.

**Action:** benchmark under realistic concurrency, tune `pool_size` and `max_overflow`.

### Risk #7 — MCP scheduler async DB

`src/core/main.py::lifespan_context` starts `delivery_webhook_scheduler` and `media_buy_status_scheduler`. Both touch DB. The lifespan context is already async. Schedulers use `asyncio.create_task()` to run periodically. DB access inside the scheduler loop becomes:

```python
async def _scheduler_tick():
    async with get_db_session() as session:
        # async queries
```

No structural change. Just update DB calls.

### Risk #8 — A2A handler async

`src/a2a_server/adcp_a2a_server.py` — verify the A2A SDK expects async handlers. It almost certainly does (it's built on Starlette). If any handler currently does sync DB work, it needs async conversion.

### Risk #9 — Middleware state propagation

`UnifiedAuthMiddleware`, `RestCompatMiddleware`, `CSRFMiddleware` (Wave 1), `ApproximatedExternalDomainMiddleware` (Wave 1) — all are pure ASGI. No change required; they're already async.

### Risk #10 — Performance characteristics

Under LOW concurrency, async has slightly higher per-request overhead (event loop scheduling, await overhead, context switch). Under HIGH concurrency, async wins big (no threadpool contention).

For salesagent's current traffic profile (low-to-medium admin concurrency, bursty MCP tool calls), the net performance impact is probably neutral-to-positive. **Benchmark before and after Wave 0 to confirm.**

### Risk #11 — Debugging complexity

Async stack traces are harder to read. Error messages with `MissingGreenlet` are confusing for developers who haven't seen them. Docs should include a troubleshooting section.

### Risk #12 — SessionContextVar propagation

If any code relies on a `ContextVar`-scoped session (e.g., for audit logging), the propagation semantics change. `asyncio.current_task()` is per-task, not per-thread. Audit needed.

### Risk #13 — FastMCP tool functions

FastMCP supports both sync and async tool functions. Converting is mechanical but the tool registration in `src/core/main.py:300-315` may need verification.

### Risk #14 — Scheduler worker multiplication under multi-worker

Still applies (deep audit §3.1). Async doesn't fix this. Still v2.0 single-worker invariant.

### Risk #15 — Pre-existing REST latent bug FIXED

`src/routes/api_v1.py` routes are already `async def` but call sync `_impl` functions. This has the scoped_session interleaving bug but hasn't bitten production. Full async fixes this as a side effect — the bug is gone once `_impl` functions become async.

**This is a WIN, not a risk.** It's the biggest reason the user's pivot makes sense.

### Risk #20 — ContextManager singleton session cache (added 2026-04-11, resolved by Decision 7)

**Severity: HIGH (bug by construction, not by accident).** `src/core/context_manager.py::ContextManager` inherits from `DatabaseManager` and caches `self._session` on a process-wide singleton via `_context_manager_instance`. Under sync SQLAlchemy, `scoped_session` with default scopefunc keys on `threading.get_ident()` — each worker thread gets its own session, and the singleton's `self._session` was "effectively thread-local" via a cached-first-wins accident. **Under `async_sessionmaker` on the event-loop thread, every concurrent task returns the same `threading.get_ident()` value → every task gets the SAME cached session → transaction interleaving.** The `async_sessionmaker` swap does NOT fix this because the singleton sits above the session factory.

**Detection:** Audit 06 deep-think 2026-04-11, confirmed by the Decision 7 Opus subagent grep-verifying 7 production callers and finding the module-load side effect at `mcp_context_wrapper.py:345`.

**Mitigation:** Decision 7 refactor (stateless module functions taking `session: AsyncSession`, delete `DatabaseManager`). See §3 "ContextManager refactor" for the full shape.

**Validated by:** Spike 4.5 (pre-Wave-0, 0.5-1 day soft blocker).

### Risk #7.5 — Background sync service long session (added 2026-04-11, resolved by Decision 9)

**Severity: HIGH (hours-long session incompatible with async pool).** `src/services/background_sync_service.py::_run_sync_thread` spawns `threading.Thread(daemon=True)` workers that open `get_db_session()` and hold it across multi-hour wall-clock GAM inventory syncs. Under `async_sessionmaker`:
- `pool_recycle=3600` rotates connections after 1 hour → open session hits `DisconnectionError`
- asyncpg idle time blows through Fly.io TCP keepalives → random mid-job failures
- Identity map grows unbounded over hours → memory pressure
- Converting to `asyncio.create_task` instead of `threading.Thread` doesn't help — the task's session still pins the pool for hours

**Also:** line 472 imports `from flask import current_app` for cache invalidation. This fails with `ImportError` the moment Flask is removed in Wave 3. **Wave 3 blocker.**

**Detection:** Decision 9 Opus subagent 2026-04-11, grep-verified the 9 `get_db_session()` sites + the Flask import + the 3-site flask-caching consumer list.

**Mitigation:** Decision 9 sync-bridge (Option B): new `src/services/background_sync_db.py` module with separate sync psycopg2 engine. Service stays sync. Wave 3 flask-caching replacement via `SimpleAppCache`. See §3 "Background sync sync-bridge" for the full shape.

**Validated by:** Spike 5.5 (pre-Wave-0, 0.5 day soft blocker). Fallback: Option A (asyncio task + single async session, suboptimal but viable).

**Sunset target Phase 4+ within v2.0** — phase-per-session async refactor at `foundation-modules.md §11.0.6 §G`.

### Risk #34 — `init_db()` opens engine in pre-fork orchestrator parent (added 2026-04-11, surfaced by Decision 2 deep-think)

**Severity: HIGH (latent fork-safety bug under async pivot).** `scripts/deploy/run_all_services.py:175` imports `from src.core.database.database import init_db` and calls `init_db()` BEFORE `:231` `subprocess.Popen([sys.executable, "scripts/run_server.py"])` forks uvicorn into a child process. Under sync SQLAlchemy this was fine (psycopg2 connections in the parent's pool got forked but were not actively used in the child, eventually timed out). **Under async SQLAlchemy + asyncpg, the parent eagerly initializes the async engine with ~10 pooled asyncpg connections** that then get duplicated into the child's interpreter via `Popen`'s file-descriptor inheritance. Two processes holding the same PG socket = canonical SQLAlchemy fork-safety bug. Even when `Popen`'s default `close_fds=True` saves the FD inheritance, the parent itself **continues to hold the open PG sockets** for the entire container lifetime (`while True: time.sleep(1)` at `:403`), wasting connection slots from the PG `max_connections=100` budget.

**Detection:** Decision 2 deep-think 2026-04-11 (Opus subagent traced the `Popen` chain at `run_all_services.py:231` → `scripts/run_server.py:47` `uvicorn.run("src.app:app", ...)`).

**Mitigation:** two options, prefer (b):
- (a) `init_db()` calls `await reset_engine()` in a `finally` block, ensuring the engine is disposed before return. Risk: any code path between `init_db()` return and `Popen` that accidentally re-touches the engine re-creates it.
- (b) **`run_all_services.py:175` runs init via `subprocess.run([sys.executable, "-m", "scripts.setup.init_database"])`** like migrations already do at `:207`. Init runs in a fresh child process, fully isolated from PID 1, terminates cleanly, no in-process state leak. Matches the existing migration pattern.

**Validated by:** Spike 5.5 additional check (verify `pg_stat_activity` shows zero parent-PID-1 connections after `init_database()` returns and before `threading.Thread(target=run_mcp_server).start()`).

**Sunset:** when `run_all_services.py` is replaced with a proper process supervisor (s6-overlay/supervisord) in v2.2+, the entire orchestrator-Python-process role disappears.

### Risk #35 — Wave 4c factory-base-class hard cliff (added 2026-04-11, Decision 3 deep-think)

**Severity: HIGH (166 tests break simultaneously).** When factory base classes flip from `SQLAlchemyModelFactory` to `AsyncSQLAlchemyModelFactory` in Wave 4c, ALL 166 consuming sync integration tests break simultaneously in a single commit — no incremental rollback possible. If any test remains sync, it gets an `AsyncSession` it cannot use.

**Detection:** pre-PR diff-scope gate asserting Wave 4c contains ONLY edits under `tests/factories/`.

**Mitigation:** strict ordering — all 166 tests flip to async in Wave 4b BEFORE factory base classes flip in Wave 4c. Structural guard `test_architecture_factory_inherits_async_base.py` prevents sync base class usage after Wave 4c.

**Validated by:** Spike 4.25 (factory async-shim validation, 0.5 day soft blocker).

### Risk #40 — dynamic_pricing_service.py ambiguous type iteration (added 2026-04-11, Decision 5 deep-think)

**Severity: MEDIUM (potential `MissingGreenlet` if ORM instances passed).** `src/services/dynamic_pricing_service.py:230,282` iterates `product.pricing_options` with a type hint that says `Product` — ambiguous between `src.core.schemas.Product` (Pydantic) and `src.core.database.models.Product` (ORM). If callers pass ORM instances under async + `lazy="raise"`, the iteration crashes with `MissingGreenlet`.

**Detection:** Spike 1's blanket `lazy="raise"` sweep covers this automatically. Additionally, a targeted type-audit of the function's callers should clarify whether Pydantic or ORM instances are passed.

**Mitigation:** verify whether the parameter is already an eagerly-loaded list or a Pydantic DTO; add explicit type annotation if ambiguous.

### Risk #41 — Response-object caching foot-gun (added 2026-04-11, Decision 6 deep-think)

**Severity: HIGH (cache hit returns Flask Response to FastAPI, crashes at runtime).** Both inventory cache sites (`inventory.py:874`, `:1133`) cache `jsonify(...)` which returns a Flask `Response` object. Under FastAPI, a cached Flask Response cannot be served — the framework expects a dict, Pydantic model, or `JSONResponse`. A naive port that preserves the cached object type will WORK on cache-miss (fresh DB query) but CRASH on cache-hit (stale Flask Response served to FastAPI).

**Detection:** integration test that hits the inventory endpoint twice (second request is a cache hit).

**Mitigation:** Wave 3 consumer migration rewrites both sites to cache the payload dict and reconstruct `JSONResponse(dict)` on hit. Documented in `foundation-modules.md` §11.15 consumer migration pattern.

### Risk #36 — AccountFactory._create override MRO breakage (Decision 3 deep-think, MEDIUM)

`tests/factories/account.py:28-30` overrides `_create` to pop a `tenant` kwarg before calling `super()._create`. If the async shim ALSO overrides `_create`, AccountFactory's override chains to the shim's override instead of factory-boy's default, silently skipping the tenant-kwarg processing. **Mitigated by:** the corrected recipe overrides `_save` instead of `_create`, preserving factory-boy's MRO chain. Validated by Spike 4.25 test case (c).

### Risk #37 — factory_session module-global state under xdist parallelism (Decision 3, LOW)

`_meta.sqlalchemy_session` is set on the factory class (module-global). Each xdist worker is a separate process (safe), but if `pytest-parallelism` or `--dist loadscope --parallel` is enabled, tests within the same process run concurrently sharing module state — factory binding would race. **Mitigated by:** current config uses `loadfile` (serial within process). Document the constraint in the recipe gotchas.

### Risk #38 — TenantFactory.currency_usd RelatedFactory identity-map pollution (Decision 3, LOW)

`TenantFactory` uses `RelatedFactory(CurrencyLimitFactory, ...)` which runs AFTER `_save` returns. Under the async shim with `autoflush=False`, the related row exists in the identity map but is NOT flushed. If the test body queries `Tenant.currency_limits` relationship before an explicit `await async_db.flush()`, no results are returned. **Mitigated by:** documenting that tests must `await async_db.flush()` before querying relationships that depend on RelatedFactory writes. Validated by Spike 4.25 test case (b).

### Risk #39 — `get_or_create`/`get_or_404` sync signatures in database_session.py (agent-b #20 renumbered, LOW)

`database_session.py:342-388` has sync helper functions. Under async, callers break with `TypeError` on `.scalars()`. **Note:** this risk was originally numbered #20 in agent-b's risk register (a numbering collision with checkpoint's #20 = ContextManager singleton). Renumbered to #39 to avoid confusion. Mechanical async conversion, ~0.25 day. Validated by CI test suite.

### Risk #42 — Site 2 inventory_list has no cache invalidation (Decision 6 deep-think, LOW — pre-existing)

The `inventory_list` endpoint cache at `inventory.py:1133` has NO invalidation path — data can be stale up to 5 minutes after a GAM sync. Background sync invalidation at `background_sync_service.py:477` deletes only `inventory_tree:v2:{tenant_id}`, NOT `inventory_list:*`. **Pre-existing gap** (same behavior in Flask). Accept as-is for Phases 0-3; add invalidation in Phase 4+.

### Risk #43 — install_app_cache lifespan startup race window (Decision 6, MEDIUM)

If `install_app_cache(app)` runs inside the lifespan AFTER `yield`, the cache is not available during request handling. If a background thread starts before lifespan completes setup, `get_app_cache()` returns `_NullAppCache` (silent no-op). **Mitigated by:** `install_app_cache` executes BEFORE `yield` in the lifespan function; `_NullAppCache` fallback prevents crashes. Validated by integration test verifying cache is populated on first inventory hit.

### Risk #44 — api_mode=False on /activity JSON poll route (Decision 8 deep-think, LOW — pre-existing bug)

JS `fetch` to `/tenant/{id}/activity` gets HTML 302 redirect on auth failure instead of JSON 401. Template's `if (response.status === 401)` branch never fires. **Pre-existing bug.** Fix `api_mode=False → True` during Wave 4 SSE deletion PR. Validated by test asserting unauthenticated fetch receives 401 JSON.

### Risk #45 — format_activity_from_audit_log fragile column-only assumption (Decision 8, MEDIUM)

`format_activity_from_audit_log` in `activity_stream.py` accesses only AuditLog columns today (safe under `lazy="raise"`). But if a future refactor adds relationship access (e.g., `audit_log.tenant.name`), it becomes a MissingGreenlet crash site. **Mitigated by:** adding to the Spike 1 eager-load audit list. Add a code comment documenting the column-only constraint.

## 5. Revised v2.0 scope (my rough estimate)

**Original v2.0:** ~18,000 LOC (Flask removal + admin FastAPI rewrite + cleanup)
**Plus async absorption:** +10,000-15,000 LOC (database layer + repositories + UoW + `_impl` conversion + alembic + test harness + factory_boy + driver change)

~~**Total v2.0:** ~30,000-35,000 LOC~~ **Refined by Agent A scope audit (`async-audit/agent-a-scope-audit.md:347`) to ~16,600-18,000 LOC total** — file-by-file inventory landed the upper bound *below* the checkpoint's first-pass estimate, primarily because the lazy-load audit surfaced ~50 mechanically fixable sites rather than a long tail of rewrites.
**Waves:** 6 (numbered 0-5; was 4 waves numbered 0-3 pre-pivot). May collapse to 5 if Wave 5 merges back into Wave 4 as scope allows.
**Branch lifetime:** 4-6 weeks with a mandatory pre-Wave-0 spike for Risk #1 (lazy loading audit)

## 6. Recommended next-session workflow

Fresh session should:

1. **Read this checkpoint** (already happening if you're here)
2. **Read folder `CLAUDE.md`** (already has a pivot marker pointing here)
3. **Read `flask-to-fastapi-deep-audit.md` §1.4 Blocker 4** with awareness that Option C is stale and Option A is chosen
4. **Launch 3 parallel opus plan agents:**
   - **Agent A — Async scope audit:** produce a definitive inventory of every file, every function, every test that needs async conversion. Includes the lazy loading audit (Risk #1). Output: file-by-file action list with LOC estimates.
   - **Agent B — 2nd/3rd order deep dive:** for each of the 15 risks above, produce mitigation steps, verification tests, and fallback plans. Output: risk mitigation matrix with pre-Wave-0 spike items flagged.
   - **Agent C — Plan file updates:** produce exact `old_string`/`new_string` edits for every stale "sync def" or "defer async" reference across the 8 plan files. Output: list of Edit operations to apply.
5. **Apply the plan-file updates** (~50-100 surgical edits)
6. **Run a pre-Wave-0 spike** on Risk #1 (lazy loading audit) before committing to the absorbed-async v2.0 scope. If the spike reveals the scope is too big, fall back to: Phases 0-3 stay as planned (sync admin) + Phase 4+ does async separately.
7. **Commit the plan updates** and **open Wave 0** with the new scope

## 7. What NOT to do in the next session

- Don't propagate the `sync def` pivot. That pivot is dead.
- Don't implement `test_architecture_admin_sync_db_no_async.py`. That guard is the wrong direction.
- Don't preserve `run_in_threadpool` wrappers in admin handler bodies for DB work. Once DB is async, they're dead code.
- Don't keep deep-audit §1.4 Option C text verbatim — Option A is the chosen resolution.
- Don't treat async SQLAlchemy as a separate release follow-on — it's Phase 4+ within v2.0.
- Don't delete `run_in_threadpool` imports entirely — they're still needed for truly blocking work (file I/O, CPU-bound calls, third-party sync libraries).
- Don't forget the pre-Wave-0 lazy loading spike. It's the single most important risk.
- Don't ship v2.0 without benchmarking async-vs-sync performance on representative admin routes (Risk #10).
- Don't forget to bump connection pool size (Risk #6).

## 8. Files that are still correct and DO NOT need editing

Per the previous audit pass, these were already in the "correct direction" and remain so under the async pivot:
- `flask-to-fastapi-migration.md` §13.3 `change_status` (already async def)
- `flask-to-fastapi-worked-examples.md` §4.1 `google_auth`, `google_callback` (correctly async)
- `flask-to-fastapi-worked-examples.md` §4.2 `oidc_login`, `oidc_callback` (correctly async)
- `flask-to-fastapi-worked-examples.md` §4.3 `upload_favicon` (already async due to `UploadFile.read()`)
- `flask-to-fastapi-worked-examples.md` §4.4 `activity_events` SSE (already async)
- Folder `CLAUDE.md` OAuth / SSE / upload conventions
- `foundation-modules.md` middleware classes (`CSRFMiddleware`, `ApproximatedExternalDomainMiddleware`, `FlyHeadersMiddleware`) — already pure ASGI async
- All exception handlers — already async (FastAPI requirement)

These files won't need editing BUT the DB access inside them (e.g., `upload_favicon` `run_in_threadpool(_update_tenant_favicon_url, ...)`) becomes `await uow.tenants.update_favicon_url(...)`.

## 9. AdCP protocol safety re-verification

Verified by cross-reference with `flask-to-fastapi-adcp-safety.md`:

| Surface | Impact of full async pivot |
|---|---|
| `/api/v1/*` REST routes (AdCP) | Signatures already async; only `_impl` internals change |
| `/mcp` MCP protocol | FastMCP handles async tools natively; tool functions convert mechanically |
| `/a2a` A2A protocol | A2A SDK is already async (built on Starlette); handler signatures may already be async |
| `_impl` layer (`src/core/tools/*.py`) | Several already async; remaining ones convert mechanically |
| `src/core/schemas/*.py` | **UNTOUCHED** — data shape is independent of sync/async |
| `ResolvedIdentity` | **UNTOUCHED** — Pydantic model, data shape independent |
| `AdCPError` hierarchy | **UNTOUCHED** — exception classes |
| Webhook payload construction | Uses Pydantic `.model_dump()` — sync, no change |
| OAuth state storage (session cookie) | **UNTOUCHED** — Starlette `SessionMiddleware` handles async/sync identically |
| CSRF middleware | Already pure ASGI async |
| Middleware stack | All already async |
| JSON response serialization | Uses `jsonable_encoder` — sync, no change |
| Error body shape (`{"detail": "..."}`) | **UNTOUCHED** |
| OpenAPI spec | **UNTOUCHED** |

**Verdict: NO AdCP protocol impact.** The pivot is purely internal. External consumers see no wire-format change.

One subtle consequence worth noting: latency profile under concurrent load improves (no more threadpool contention), which is a small improvement for AdCP clients but not a compatibility break.

---

## Appendix A: One-line summary for the next session's opus agents

> Deep-audit Blocker #4 pivoted from Option C (sync def handlers) to Option A (full async SQLAlchemy absorbed into v2.0). All plan-file "sync def" language is superseded. Verify no AdCP schema impact (confirmed), then propagate the pivot across 8 plan files, run a pre-Wave-0 lazy-loading spike (Risk #1 in §4), and update the structural guards to assert async-def rather than sync-def.

## Appendix B: Where to look for the previous audit findings

The previous audit that found 17 sync-def inconsistencies is in my running context at the time of this checkpoint, not yet committed. That audit is NOW superseded — the fresh session should NOT try to apply those "fix to sync def" edits. Instead, the fresh session's Agent C (plan file updates) should identify every stale "sync def" reference and rewrite to async def + async SQLAlchemy.

The file locations of the stale content are listed in §2 of this checkpoint.

## Appendix C: Context compaction rationale

This checkpoint is being written immediately before a planned context compaction. The user's reasoning: the async migration work ahead involves 3+ opus subagent invocations that will produce ~100-200KB of output each, plus 50+ surgical plan-file edits. Fresh context produces cleaner agent outputs and avoids hitting context window limits mid-migration.

Everything the fresh session needs is on disk:
- This checkpoint file
- 8 existing plan files in `.claude/notes/flask-to-fastapi/`
- Folder `CLAUDE.md` (points at this checkpoint first)
- `implementation-checklist.md` (updated with superseded marker)
- Git branch `feat/v2.0.0-flask-to-fastapi` (3 commits)
- Project `CLAUDE.md` breadcrumb
- Auto-memory reference entry

The fresh session's first action: read this file.

---

## 5. Verification audit findings (2026-04-12)

Three rounds of parallel Opus subagent verification (14 agents total) audited this checkpoint against the actual source code, traced derivative consequences through 4 orders, hunted for silent breaking bugs, and checked compliance with the project's CLAUDE.md rules.

### 5.1 Code block corrections applied to this file

1. **Database layer code block (Section 3):** Corrected from module-level engine to lifespan-scoped engine per Agent E Category 1. Added `autoflush=False`. Replaced `statement_timeout` event listener with `connect_args={"server_settings": ...}` (asyncpg has no `dbapi_conn.cursor()`).
2. **Repository pattern code block (Section 3):** Removed `list_dtos()` from repository example. Repositories return ORM objects (consistent with existing codebase). DTO conversion belongs in handlers.
3. **Handler code block (Section 3):** Updated to show DTO conversion in handler: `dtos = [AccountDTO.from_orm(a) for a in repo.list_all(...)]`.

### 5.2 Ground truth corrections (from forensic source code audit)

| Fact | Plan Said | Code Says | Impact |
|---|---|---|---|
| OIDC callback path | `{tenant_id}` in URL | NO tenant_id — `/auth/oidc/callback` (`oidc.py:209`) | Guard test would pin wrong path |
| GAM callback path | `/auth/gam/callback` NOT under `/admin` | Gets `/admin` prefix from nginx SCRIPT_NAME (`auth.py:931`) | Guard test would pin wrong path |
| Relationship count | 58 | **68** (verified grep) | Spike 1 lazy-load audit undersized |
| Template count | 74 (FE audit) | **72** (verified glob) | Codemod criteria already correct |
| Engine initialization | Module-level | **Lazy singleton** (`get_engine()` with `if _engine is None:`) | Migration is a pattern change |
| ContextVar count | Multiple implied | **1** (`current_tenant` in `config_loader.py:57`) | Simpler than expected |

### 5.3 Derivative consequences requiring new mitigation

1. **`onupdate=func.now()` columns stale post-commit** (Risk #5 variant). `updated_at` columns with `onupdate=func.now()` are DB-side UPDATE triggers. With `expire_on_commit=False`, the ORM instance retains the OLD value after commit. Fix: application-side `obj.updated_at = func.now()` before commit, or explicit `await session.refresh(obj, ['updated_at'])`.
2. **GAM services have private `scoped_session` instances.** `gam_inventory_service.py` and `gam_orders_service.py` bypass `database_session.py` with their own module-level `scoped_session`. Must migrate to centralized `get_sync_db_session()` (Decision 9 sync-bridge).
3. **Dual-engine connection pool budget.** psycopg2 pool (10+20=30) + asyncpg pool (20+10=30) = 60 peak connections against `max_connections=100`. Under burst load, both pools saturating simultaneously would hit the limit. Fix: tune combined `pool_size` to stay under `max_connections - 15` (headroom for superuser/replication).
4. **Template relationship access needs DTO audit.** Every template that accesses ORM relationship attributes (`product.pricing_options`, `creative.media_buys`) must have the corresponding DTO include that data. Without this, `AttributeError` on Pydantic models or `MissingGreenlet` on ORM objects.

### 5.4 Pattern corrections

1. **Repositories return ORM objects, not DTOs.** The `list_dtos()` pattern in the original checkpoint was inconsistent with the existing codebase. All current repositories (`AccountRepository`, `MediaBuyRepository`, etc.) return ORM model objects. DTO conversion belongs in the handler layer.
2. **Feature flag recommended for Waves 1-2.** `ADCP_USE_FASTAPI_ADMIN=true/false` enables instant rollback during the dual-mount phase without container swaps or data loss. Eliminates Wave 2 code freeze. ~50 LOC.
3. **`form_error_response()` shared helper.** DRY invariant requires extracting form-validation-error re-rendering pattern before 25 router files are written. Prevents duplication caught by `check_code_duplication.py`.

### 5.5 Full findings reference

The complete findings (55 items across 7 categories) are documented in `implementation-checklist.md` Section 3.5. This checkpoint section provides the summary; the checklist is the source of truth for tracking.
