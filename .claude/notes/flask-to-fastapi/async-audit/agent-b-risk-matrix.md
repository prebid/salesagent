I have enough grounding. Now let me produce the risk analysis report. The body of the report is what the caller expects.

---

# Risk Analysis Report: Full Async SQLAlchemy Absorbed into Flask→FastAPI v2.0.0

**Date:** 2026-04-11
**Agent:** Risk Analyst (Agent B in checkpoint workflow)
**Scope:** 15 risks enumerated in `async-pivot-checkpoint.md` §4, plus interactions and newly-discovered 2nd/3rd-order risks
**AdCP safety floor:** Every mitigation in this report has been cross-checked against `flask-to-fastapi-adcp-safety.md`. Any mitigation that would perturb wire format, schema URL paths, webhook payload shape, or MCP tool signatures is flagged loudly.
**Grounding:** `src/core/database/database_session.py` (scoped_session + psycopg2), `src/core/database/models.py` (68 `relationship(...)` definitions, zero `lazy=`/`selectinload`/`joinedload`/`raiseload` annotations — all relationships default to `lazy="select"`), `src/core/database/json_type.py` (JSONB TypeDecorator with Pydantic coercion, comment at line 91 says "PostgreSQL JSONB is already deserialized by psycopg2 driver" — this is driver-specific), 12 files in `src/` with selectinload/joinedload/options usage already, `tests/factories/*.py` uses `factory.alchemy.SQLAlchemyModelFactory` which has no async support.

---

## Section 1 — Risk Mitigation Matrix

| # | Name | Severity | Detectable pre-merge? | Mitigation effort | Fallback viable? |
|---|---|---|---|---|---|
| 1 | Lazy-load `MissingGreenlet` on `relationship()` access | **H** | Yes, with new `raiseload` guard + targeted greenlet probe | 3–5 days (audit + fix + cookbook adoption) | **No** — full rollback to sync is the only escape; partial rollback impossible because one lazy load poisons the whole surface |
| 2 | `psycopg2` → `asyncpg` driver-behavior delta (JSONB/UUID/Interval/Array/isolation) | **H** | Yes, with driver-spike `tox -e driver-compat` | 1–2 days (spike) + 1–2 days (fixes) | Yes — pin `psycopg` v3 (`psycopg[binary,pool]`) async instead, avoid asyncpg codec differences entirely |
| 3 | `pytest-asyncio` event-loop scoping, session-scope conflicts, xdist interaction | **M** | Yes, smoke-suite runs in parallel | 2–3 days | Yes — serialize xdist workers per DB; slower but works |
| 4 | Alembic `env.py` async rewrite + CI migration execution | **M** | Yes (CI migration job runs every PR) | 0.5–1 day | Yes — keep `alembic env.py` sync; async-URL rewriter at boot time still possible |
| 5 | `expire_on_commit=False` — DB-default fields (`created_at`, `server_default`) stale post-commit | **M** | Partial — needs regression tests on insert flows | 0.5–1 day | N/A — must set `expire_on_commit=False` for async, not optional |
| 6 | Connection pool saturation under load (asyncpg pool vs SQLAlchemy pool, max_size tuning) | **M** | Yes, benchmark harness | 1 day benchmark + tune | Yes — grow pool; if not enough, fall back to PgBouncer transaction pooling |
| 7 | MCP scheduler ticks wired to async DB under `combine_lifespans` | **M** | Yes, lifespan alive-tick test + existing scheduler-composition guard | 0.5 day | Partial — schedulers can live in `run_in_threadpool` block; reduces concurrency but works |
| **7.5** | **Background sync long-session multi-hour failure (Decision 9, 2026-04-11)** | **H** | Yes, Spike 5.5 (4 test cases: lazy-init/dispose, MVCC bidirectional, 5-async + 1-sync no-deadlock, post-dispose leaks ≤1) | 0.5 day Spike + ~200 LOC sync-bridge module | **Soft** — fallback Option A (asyncio task + per-page session checkpoint), suboptimal but viable. Sunset target v2.1+. See deep dive after Risk #7. |
| 8 | A2A handler async-DB conversion + SDK async contract | **L** | Yes (A2A TestClient test suite) | 0.5 day | Yes — per-handler sync escape via `asyncio.to_thread` |
| 9 | Middleware state propagation (CSRF, Approximated, ProxyHeaders, Unified auth) | **L** | Already enforced by middleware ordering tests | 0 days (already async) | N/A — no change |
| 10 | Performance regression under low concurrency / warm-path latency | **M** | Yes, benchmark harness (pre/post) | 1 day benchmark | Partial — tune `pool_size` + reduce `await` fan-out; full rollback fallback exists |
| 11 | Debugging complexity (greenlet/async stack traces) | **L** | No (training issue) | Ongoing — doc + runbook | N/A — mitigation is education |
| 12 | `ContextVar` / `flask.g` replacement propagation (audit logger, tenant context) | **M** | Yes, audit-log regression test | 1 day | Yes — per-request dict attached to `request.state` |
| 13 | FastMCP tool function registration under async (tool signature introspection) | **L** | Yes (list_tools smoke test) | 0.25 day | Yes — FastMCP supports both |
| 14 | Scheduler singleton under `workers > 1` (unchanged from sync plan) | **L** | Already guarded by `test_architecture_single_worker_invariant.py` | 0 days | N/A — pre-existing invariant |
| 15 | **Pre-existing `api_v1.py` interleaving bug — FIXED by pivot** | — (WIN) | N/A | N/A — side-effect gain | N/A |

**NEW risks discovered during analysis** (Section 6):

| # | Name | Severity | Detectable pre-merge? | Mitigation effort | Fallback viable? |
|---|---|---|---|---|---|
| 16 | `session.execute` return type change — `result.scalars().first()` already works, but `.query()` paths that slipped past allowlist break | **M** | Yes, mypy + unit test run | 0.5 day | N/A |
| 17 | `TypeDecorator.process_result_value` assumes psycopg2 JSONB pre-decoding — asyncpg codec path differs | **H** | Yes, `tox -e driver-compat` JSONType round-trip test | 1 day | Yes — add JSON codec registration on asyncpg connect |
| 18 | `@event.listens_for(_engine, "connect")` — sync event listener executing `cursor.execute("SET statement_timeout=...")` doesn't fire on async engine | **H** | Yes, integration test observing `statement_timeout` via `SHOW` | 0.5 day | Yes — use asyncpg `setup` callback |
| 19 | `DatabaseManager` class (`src/core/database/database_session.py:287-338`) with sync `__enter__`/`__exit__` — any caller breaks | **M** | Yes, grep + mypy | 0.5 day | N/A |
| 20 | `get_or_create`, `get_or_404` convenience helpers (lines 342-388) — sync signatures | **L** | Yes, grep | 0.25 day | N/A |
| 21 | Cross-schema `execute_with_retry` helper — sync wrapper around `get_db_session()` | **M** | Yes, grep + CI test | 0.5 day | Partial |
| 22 | `_pydantic_json_serializer` passed as `json_serializer=` to `create_engine()` — engine-level hook may not fire for asyncpg | **H** | Yes, integration test writing Pydantic models to JSONB | 0.5 day | Yes — register asyncpg JSON codec directly |
| 23 | `check_database_health` circuit breaker uses module-level mutable state — `_is_healthy` races between tasks | **M** | Hard — concurrency race, only shows under load | 0.5 day (add `asyncio.Lock`) | N/A |
| 24 | `get_pool_status()` — SQLAlchemy async pool has `AsyncAdaptedQueuePool`, pool.size() etc. return the same API but the `.pool` attribute is `QueuePool.AsyncAdaptedQueuePool` — possible AttributeError at runtime | **L** | Yes, health endpoint smoke | 0.25 day | N/A |
| 25 | `reset_engine` in test fixtures — `_engine.dispose()` is sync; async engine needs `await _engine.dispose()` | **M** | Yes, test harness breaks immediately | 0.25 day | N/A |
| 26 | FastMCP `lifespan_context` — bridging FastMCP lifespan + FastAPI lifespan + async DB inside schedulers; deadlock risk on shutdown | **M** | Hard — only shows on shutdown under load | 1 day (graceful shutdown semantics) | Partial — forceful process kill works but loses in-flight work |
| 27 | DB-field read after commit triggers implicit `SELECT` (`expire_on_commit=True` default) — interacts with Risk #1 | **H** | Yes, with greenlet probe in tests | (covered by #5 and #1) | N/A |
| 28 | `audit_logger` module-level singleton writes via `get_db_session()` — becomes async-only; every caller breaks | **M** | Yes, grep + unit test | 1 day | Partial |
| 29 | WebSocket / SSE handlers with long-lived DB sessions pin pool connections | **M** | Yes, pool-exhaustion test | 0.5 day | Yes — close session between SSE ticks |
| 30 | Pydantic `model_validate_async` nonexistent — model validation during read happens in sync context; `JSONType.process_result_value` calls `model_validate` inside the async DB result processing path | **L** | Yes, integration round-trip test | 0 days (already sync) | N/A |
| 31 | Existing `tests/unit/test_architecture_no_raw_select.py` allowlist entries (historical sync `select()`) become landmines during conversion | **L** | Yes (structural guard already enforces) | 0 days | N/A — fall within existing guard's scope |
| 32 | `asyncio.get_event_loop()` + `asyncio.new_event_loop()` patterns in test fixtures — DeprecationWarning → RuntimeError in Python 3.14 | **L** | Yes (pytest warnings) | 0.25 day | N/A |
| 33 | SLL (shared library loader) modules that do `import src.core.database.database_session` at module load time may trigger eager engine creation → asyncpg event loop binding at wrong time | **M** | Yes, import-order test | 0.5 day | N/A |

---

## Section 2 — Per-Risk Deep Dives

### Risk #1 — Lazy loading `MissingGreenlet` [SEVERITY: H — biggest unknown]

#### A. Root cause

SQLAlchemy's default `relationship()` loader strategy is `lazy="select"`: when code accesses `obj.related`, SQLAlchemy emits a SELECT on first access using the ORM session bound to the instance. Under sync `Session`, the SELECT runs synchronously in the current thread using the underlying DBAPI connection. Under `AsyncSession`, the ORM can't drive a blocking SELECT because the underlying connection is an `asyncpg` coroutine — it must `await` to execute. SQLAlchemy's async layer uses **greenlets** (`sqlalchemy.util.greenlet_spawn`) to bridge this: when the ORM emits I/O, the greenlet parks, the event loop runs the coroutine, and the greenlet resumes.

The problem: **greenlet context is only set up inside `await session.execute(...)` calls and other `await`-entered paths.** When you access `obj.related` OUTSIDE a SQLAlchemy async-I/O boundary — for example, in a Jinja template after the session has closed, or in response-serialization code after `await session.commit()`, or in a callback that runs after the handler returns — there is no greenlet context. The ORM tries to spawn its greenlet adapter, finds no outer greenlet stack, and raises `sqlalchemy.exc.MissingGreenlet: greenlet_spawn has not been called; can't call await_only() here`. This is a HARD failure, not a warning: the entire request dies.

Concrete audit of `src/core/database/models.py`: **68 `relationship()` definitions, zero `lazy=`/`selectinload`/`joinedload`/`raiseload` annotations.** Every relationship defaults to `lazy="select"`. The repo already uses `selectinload`/`joinedload` at ~12 call sites (verified via grep in the preamble), so the team has some fluency, but the default-lazy posture across ~68 relationships means the surface area is large.

Further: `src/core/database/database_session.py` currently uses `sessionmaker(bind=_engine)` — no explicit `expire_on_commit=False`, so it defaults to `True`. Under sync SQLAlchemy this triggers a lazy-refresh on post-commit attribute access; under async this becomes `MissingGreenlet`. Risk #5 deals with the config flip; Risk #1 is about the structural code pattern.

#### B. Detection

**Failure signatures:**
```
sqlalchemy.exc.MissingGreenlet: greenlet_spawn has not been called; can't call await_only() here.
Was IO attempted in an unexpected place? (Background on this error at: https://sqlalche.me/e/20/xd2s)
```

**Log pattern to grep for in CI output:**
```bash
grep -E "MissingGreenlet|greenlet_spawn has not been called|await_only" test-results/*/pytest-stdout.log
```

**Test that catches this structurally (add during Wave 0):**
```python
# tests/unit/test_architecture_no_default_lazy_relationship.py
"""
Every relationship() in src/core/database/models.py MUST specify an explicit
loader strategy: lazy="raise", lazy="selectin", lazy="joined", or a marker
"# NOQA: default-lazy" comment with an explanation.

Rationale: under AsyncSession, default lazy="select" triggers MissingGreenlet
when the relationship is accessed outside the session's async context.
"""
import ast
from pathlib import Path

ALLOWED_LAZY = {"raise", "selectin", "joined", "noload", "immediate"}
MODELS_FILE = Path("src/core/database/models.py")

def test_no_default_lazy_relationships():
    tree = ast.parse(MODELS_FILE.read_text())
    offenders = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "relationship"):
            continue
        kwargs = {kw.arg: kw.value for kw in node.keywords}
        if "lazy" not in kwargs:
            offenders.append(f"{MODELS_FILE}:{node.lineno}")
            continue
        lazy_val = kwargs["lazy"]
        if not (isinstance(lazy_val, ast.Constant) and lazy_val.value in ALLOWED_LAZY):
            offenders.append(f"{MODELS_FILE}:{node.lineno} (lazy={ast.unparse(lazy_val)})")
    assert not offenders, f"Relationships with implicit or disallowed lazy strategy: {offenders}"
```

**Monitoring metric** (for post-deploy detection):
- Instrument logs to emit a structured field `exception_type` on error. Dashboard query:
  ```
  exception_type:"MissingGreenlet" count() per route
  ```
  Any nonzero count in prod is a P0. Greenlet errors do not recover — the request dies.

**Targeted greenlet probe test (catches handlers that accidentally lazy-load):**
```python
# tests/integration/test_no_greenlet_spawns_after_handler_return.py
@pytest.mark.requires_db
@pytest.mark.asyncio
async def test_accounts_list_handler_no_lazy_loads(admin_client):
    # Instrument: set every relationship to lazy="raise" via runtime patching
    # for the duration of this test; any handler that lazy-loads blows up loudly.
    with _patch_lazy_to_raise():
        resp = await admin_client.get("/admin/tenant/default/accounts")
        assert resp.status_code == 200
```

#### C. Mitigation — Step-by-step

**Step 1 — Structural guard (half-day).** Add `test_architecture_no_default_lazy_relationship.py` (code above). Baseline allowlist = 68 (all current relationships). Wave 0 blocks on baseline creation. Wave 1+ ratchets down. Zero new violations may be added.

**Step 2 — Repository-level eager loading (2 days).** In every repository method that returns an object whose relationships are accessed by the caller, add `.options(selectinload(Model.relationship))` or `.options(joinedload(Model.relationship))`. See the Lazy-Load Cookbook (Section 3) for the decision tree.

**Step 3 — Set `lazy="raise"` as the default (half-day).** Once Step 2 is complete, flip all 68 relationships to `lazy="raise"`. This is the **key enforcement move**: any lazy load now fails loudly at runtime, even in a test or dev environment, instead of silently working in sync mode and breaking under async.

```python
# src/core/database/models.py — before
products = relationship("Product", back_populates="tenant", cascade="all, delete-orphan")

# after
products = relationship(
    "Product",
    back_populates="tenant",
    cascade="all, delete-orphan",
    lazy="raise",  # Async invariant: caller must use selectinload/joinedload
)
```

**Step 4 — Promote `lazy="raise"` → constructor default (half-day).** Optional but recommended — wrap `sqlalchemy.orm.relationship` with a project-local factory:

```python
# src/core/database/orm_helpers.py
from sqlalchemy.orm import relationship as _sa_relationship

def relationship(*args, lazy="raise", **kwargs):
    """Project-local relationship() with lazy='raise' default.

    Any relationship that needs eager loading must specify lazy="selectin",
    "joined", or "immediate" explicitly. This prevents accidental lazy loads
    under AsyncSession which would raise MissingGreenlet.
    """
    return _sa_relationship(*args, lazy=lazy, **kwargs)
```

Import this in `models.py` instead of `from sqlalchemy.orm import relationship`. Add a second structural guard: `test_models_uses_project_relationship.py`.

**Step 5 — Cookbook adoption audit (1 day).** Walk every `_impl` function and every admin handler. For each DB-backed route, confirm:
- Repository method `selectinload`s the relationships the handler reads
- Handler does not access relationships outside the UoW `async with` block

Build the audit inventory as part of Agent A's deliverable. Spot-check: run the integration test suite with `lazy="raise"` globally enabled via a pytest conftest override. Every failing test pinpoints a lazy-load site.

**Step 6 — Pre-commit hook (half-day).** AST scanner: any call to `relationship(` in `src/core/database/models.py` that does not specify `lazy=` fails pre-commit. Prevents regression once Step 4 is in place.

#### D. Fallback plan

If the lazy-load audit reveals that more than ~30% of handler code paths need non-trivial repository rewrites (not just adding `.options(selectinload(...))` but restructuring queries, adding new relationship shapes, or rewriting template bodies), **the v2.0 async scope is too aggressive**. Escape hatches:

1. **Full rollback to sync def admin (Option C from deep-audit §1.4).** Preserves the scoped_session + sync session model. Admin stays sync. `api_v1.py` latent bug stays latent (documented, not fixed). AdCP surface unchanged. No schema impact. **This is the clean fallback — it matches the previous plan.**

2. **Split rollback — keep MCP/A2A tools async, revert admin to sync.** Technically possible but MESSY: it requires two session factories (one async for `_impl`, one sync for admin handlers), duplicate repository classes (sync + async variants), and a shared DB engine with both sync and async connections. Rejected: this creates more complexity than it solves. Pick option 1.

3. **Partial: async only for new admin routes.** Also rejected — the plan is to rewrite Flask admin to FastAPI; "new admin routes" IS all admin routes.

**AdCP compliance of rollback:** Option 1 rollback is AdCP-safe (`api_v1.py` sync-call-from-async-route latent bug is pre-existing and hasn't bitten production per deep-audit §R11). The rollback does NOT break AdCP compliance.

**Point of no return:** Wave 4 (async SQLAlchemy) entry. Once Wave 4 ships to main with `create_async_engine`, `async_sessionmaker`, and `asyncpg` in `pyproject.toml`, rolling back means reverting the driver, re-editing 68 relationships, and un-converting every repository. Estimated rollback cost post-Wave-4: 3–5 days. Pre-Wave-4: 0 days. **Decision gate: the pre-Wave-0 spike (Section 4) MUST pass before starting Wave 4.**

---

### Risk #2 — Driver change: `psycopg2` → `asyncpg`

#### A. Root cause

`asyncpg` is a ground-up re-implementation of the Postgres wire protocol in Python, written by Yury Selivanov specifically for async use. It is NOT a drop-in replacement for `psycopg2`; it differs in type codecs, transaction semantics, prepared-statement caching, and connection pooling. SQLAlchemy's `postgresql+asyncpg://` dialect bridges the two APIs but does NOT hide all of the differences. The most-likely-to-bite differences:

- **JSONB codec**: `asyncpg` returns JSONB as `str` by default unless you register a custom codec. `psycopg2` returns `dict`/`list`. Our `JSONType.process_result_value` (file `src/core/database/json_type.py:86-114`) asserts "PostgreSQL JSONB is already deserialized by psycopg2 driver" and raises `TypeError` if the incoming value isn't `dict | list`. **This will break immediately on asyncpg.**

- **UUID codec**: `asyncpg` returns `uuid.UUID` objects; `psycopg2` with default codec returns `uuid.UUID` only if `psycopg2.extras.register_uuid()` was called (usually is, transitively). Likely aligned but verify.

- **Interval/timestamp**: asyncpg returns `datetime.datetime` for `timestamptz`, which is the same as psycopg2. Aligned.

- **Array types**: asyncpg returns `list`, psycopg2 returns `list`. Aligned.

- **Server-side `SET` statements**: `@event.listens_for(_engine, "connect")` hooks in `database_session.py:139-143` run `cursor.execute("SET statement_timeout = ...")`. Under asyncpg, the sync `dbapi_conn` is a `asyncpg.Connection` wrapped by SQLAlchemy's sync adaptor — the event still fires but the cursor API differs. See Risk #18 for the full breakdown.

- **JSON engine serializer**: `create_engine(..., json_serializer=_pydantic_json_serializer)` hooks SQLAlchemy's sync serializer path. Under asyncpg, SQLAlchemy passes the serializer through but asyncpg's JSONB codec is registered at the asyncpg-driver level, not the SQLAlchemy level. The engine-level `json_serializer` may not fire. See Risk #22.

- **Prepared-statement cache collision**: asyncpg caches prepared statements per-connection. Connections returned to the pool have stale prepared statements. If a DDL changes the column shape, cached statements become invalid and raise `InvalidCachedStatementError`. Not common in production but can bite during alembic migration runs against a live pool.

- **PgBouncer incompatibility**: the existing code already handles PgBouncer (`_is_pgbouncer_connection` at line 44). Under asyncpg, **transaction-pooling PgBouncer is broken** because asyncpg's prepared-statement cache assumes connection affinity, which transaction pooling violates. Workaround: pass `statement_cache_size=0` and `prepared_statement_cache_size=0` to the asyncpg driver via `connect_args`.

- **`search_path`, `SET ROLE`, session-level state**: session-level config applied via `SET` does not survive PgBouncer transaction pooling under asyncpg. Same issue as above.

- **Error classes**: `asyncpg.exceptions.*` vs `psycopg2.OperationalError`. SQLAlchemy normalizes these into `sqlalchemy.exc.OperationalError`, but any code that catches the driver-specific class breaks. Grep: `grep -rn "psycopg2" src/` — any hit outside `pyproject.toml` is a potential landmine.

- **`COPY`**: not used by this repo, skip.

- **`LISTEN/NOTIFY`**: grep confirms no usage. Skip.

- **`cursor.execute("BEGIN")` / raw transactional SQL**: not used outside migrations. Skip.

#### B. Detection

**Failure signatures:**
```
asyncpg.exceptions.InvalidCachedStatementError: cached statement plan is invalid due to a database schema or configuration change
sqlalchemy.exc.InterfaceError: (asyncpg.exceptions.InvalidCachedStatementError) ...
TypeError: Unexpected type in JSONB column: str
asyncpg.exceptions.InterfaceError: cannot perform operation: another operation is in progress
```

The last one is particularly nasty: it signals a session being used concurrently by two tasks (Risk #33) — one task has a pending I/O and another tries to start a new one on the same connection.

**Log patterns:**
```bash
grep -E "asyncpg\.exceptions|InvalidCachedStatementError|cannot perform operation" test-results/
grep -E "TypeError: Unexpected type in JSONB" test-results/
```

**Pre-merge detection test — driver compat spike (runs as `tox -e driver-compat`):**
```python
# tests/integration/test_driver_compat_asyncpg.py
@pytest.mark.requires_db
@pytest.mark.asyncio
async def test_jsonb_dict_roundtrip():
    async with get_db_session() as session:
        tenant = TenantFactory.build(config={"foo": "bar", "nested": {"n": 1}})
        session.add(tenant)
        await session.commit()
        result = await session.execute(select(Tenant).where(Tenant.tenant_id == tenant.tenant_id))
        loaded = result.scalars().first()
        assert loaded.config == {"foo": "bar", "nested": {"n": 1}}
        assert isinstance(loaded.config, dict), f"Got {type(loaded.config)}"

@pytest.mark.requires_db
@pytest.mark.asyncio
async def test_jsonb_pydantic_model_roundtrip():
    # Verify JSONType(model=BrandReference) still coerces on read/write
    ...

@pytest.mark.requires_db
@pytest.mark.asyncio
async def test_uuid_column_roundtrip():
    ...

@pytest.mark.requires_db
@pytest.mark.asyncio
async def test_statement_timeout_applied():
    async with get_db_session() as session:
        result = await session.execute(text("SHOW statement_timeout"))
        timeout = result.scalar()
        assert timeout in ("30000ms", "30s")  # Matches query_timeout config
```

#### C. Mitigation

**Step 1 — Pre-Wave-0 driver spike (1 day).** Create a throwaway branch `spike/asyncpg-driver-compat`. Do the minimum to get the test suite running against asyncpg:
1. Add `asyncpg` to `pyproject.toml` (keep psycopg2 alongside)
2. Conditionally rewrite `DATABASE_URL` → `postgresql+asyncpg://` when `ASYNC_DB=1`
3. Create a minimal async session factory
4. Run `tox -e unit` and `tox -e integration` under `ASYNC_DB=1`
5. **Record every failure.** Count failures by root cause (JSONB codec, greenlet, prepared statement cache, event listener, etc.). Triage into "fixable with 1-line patch" vs "fixable with new infra" vs "rewrites repository call sites".

**Step 2 — Register JSONB codec at asyncpg level (1 hour).** This is the critical fix for the TypeDecorator:

```python
# src/core/database/database_session.py — async version
import json
import asyncpg
from sqlalchemy.ext.asyncio import create_async_engine

async def _asyncpg_connect_init(conn: asyncpg.Connection):
    """Register custom codecs on every new asyncpg connection.

    Without this, JSONB columns return str instead of dict, breaking
    JSONType.process_result_value's dict/list assertion.
    """
    # JSONB → dict/list roundtrip (matches psycopg2 default behavior)
    await conn.set_type_codec(
        "jsonb",
        encoder=lambda v: json.dumps(v, default=str),
        decoder=json.loads,
        schema="pg_catalog",
    )
    # JSON → dict/list roundtrip
    await conn.set_type_codec(
        "json",
        encoder=lambda v: json.dumps(v, default=str),
        decoder=json.loads,
        schema="pg_catalog",
    )
    # Session-level statement timeout (replaces the sync event listener)
    await conn.execute(f"SET statement_timeout = '{query_timeout * 1000}'")

_engine = create_async_engine(
    connection_string.replace("postgresql://", "postgresql+asyncpg://"),
    echo=False,
    pool_size=20,
    max_overflow=10,
    connect_args={
        "server_settings": {"statement_timeout": f"{query_timeout * 1000}"},
        "command_timeout": query_timeout,
        "prepared_statement_cache_size": 0,  # Defeat PgBouncer conflicts
    },
)

# Hook the asyncpg-level connect callback via SQLAlchemy's event API
from sqlalchemy import event

@event.listens_for(_engine.sync_engine, "connect")
def _on_connect(dbapi_conn, connection_record):
    # dbapi_conn is an AsyncAdapt_asyncpg_connection — call _connection to get
    # the raw asyncpg.Connection, then schedule the async init on the loop.
    asyncpg_conn = dbapi_conn._connection
    asyncio.run_coroutine_threadsafe(
        _asyncpg_connect_init(asyncpg_conn), asyncpg_conn._loop
    ).result(timeout=5)
```

**Note:** the `run_coroutine_threadsafe` dance is needed because SQLAlchemy's `connect` event fires in sync context even for async engines. A cleaner alternative is to use `asyncpg.create_pool(init=...)` directly, but SQLAlchemy's async dialect owns the pool.

**Cleaner alternative** — use SQLAlchemy's own JSON serializer path, which it threads through to asyncpg if correctly configured:

```python
_engine = create_async_engine(
    url,
    json_serializer=_pydantic_json_serializer,
    json_deserializer=json.loads,  # explicit
)
```

SQLAlchemy's asyncpg dialect should register the JSON/JSONB codec on the asyncpg connection using these. **Verify this experimentally in the driver spike** — if it works, drop the manual codec registration. If it doesn't, fall back to the manual `set_type_codec` approach.

**Step 3 — Add `TypeDecorator.process_result_value` defensive path (1 hour).** Make `JSONType` tolerant of `str` input (parse it) so the TypeDecorator doesn't rely on driver-specific pre-decoding:

```python
# src/core/database/json_type.py — updated process_result_value
def process_result_value(self, value: Any, dialect: Dialect) -> Any:
    if value is None:
        return None

    # Driver-agnostic: some drivers return str for JSONB, some return dict/list
    if isinstance(value, str):
        value = json.loads(value)

    if not isinstance(value, dict | list):
        raise TypeError(...)
    ...
```

This is defensible — the TypeDecorator becomes driver-agnostic. Add a regression test that feeds both `str` and `dict` values and verifies the output matches.

**Step 4 — Audit for psycopg2-specific imports (1 hour):**
```bash
grep -rn "import psycopg2\|from psycopg2" src/ tests/
```
Fix any hits — usually `except psycopg2.OperationalError` → `except sqlalchemy.exc.OperationalError`.

**Step 5 — Driver-compat tox env in CI (1 hour):** add `tox -e driver-compat` that runs a subset of integration tests under asyncpg. Use in CI only until the full Wave 4 flip.

**Step 6 — PgBouncer compat (0.5 day if used, 0 days if not).** Verify whether production uses PgBouncer. If yes, explicit `prepared_statement_cache_size=0` in `connect_args`. Add a startup log assertion.

#### D. Fallback plan

If asyncpg compat cost exceeds 2 days of the spike's time budget, **switch to `psycopg` v3** (the modern successor to `psycopg2`, with first-class async support):

```toml
# pyproject.toml — fallback
# asyncpg>=0.30.0           # OUT
psycopg[binary,pool]>=3.2.0  # IN
```

```python
url = "postgresql+psycopg://user:pass@host/db"  # NOT "psycopg2"
```

**Why psycopg v3 is a reasonable fallback:**
- Same codec behavior as psycopg2 (JSONB → dict, etc.)
- Same `cursor.execute` semantics
- Supports both sync and async via the same package (dual-API)
- SQLAlchemy 2.0 fully supports `postgresql+psycopg://` with async
- No PgBouncer compatibility issues (no client-side prepared-statement cache by default)

**Cost of the fallback:** ~4 hours — update `pyproject.toml`, update URL rewriter, re-run driver spike. Fewer type-codec surprises but slightly slower than asyncpg.

**AdCP compliance:** no impact — driver is purely internal. Either `asyncpg` or `psycopg` v3 is AdCP-safe.

**Point of no return:** Wave 4 merge. If driver-compat bugs show up in production after Wave 4 ships, fall back to psycopg v3 is a patch release (`v2.0.1`). Full rollback to sync is a major rollback (`v2.0.x` → `v1.x`).

---

### Risk #3 — pytest-asyncio / test infrastructure

#### A. Root cause

Async tests require `pytest-asyncio` or `anyio`. Both work, but they differ in event-loop scoping:
- `pytest-asyncio` (the common choice): each test gets a fresh event loop by default (`function` scope). Fixtures that create connections at `session` scope bind to an event loop that is torn down after the first test, leading to `RuntimeError: Event loop is closed` for the second test.
- `anyio`: uses a single loop per test run, friendlier for session fixtures, but requires rewriting test markers.

The current test harness (`tests/harness/_base.py:894-913`) uses sync `__enter__`/`__exit__`. Conversion to `__aenter__`/`__aexit__` is mechanical but affects every integration test. Additionally, `pytest-xdist` parallel execution scopes databases per-worker (already does) but event-loop scoping interacts with xdist: each worker is a separate process, so event loops don't collide, but the session-scoped DB fixture must be re-initialized per-worker-process.

Another subtle issue: `pytest-asyncio` has `asyncio_mode = "strict"` vs `"auto"`. In "strict" mode, every test needs `@pytest.mark.asyncio` explicitly; in "auto" mode, any `async def` test is automatically asyncio. The project should use `"strict"` to avoid accidental async-on-sync-fixture bugs.

#### B. Detection

**Failure signatures:**
```
RuntimeError: Event loop is closed
RuntimeError: There is no current event loop in thread 'MainThread'
pytest_asyncio.plugin.Error: Asynchronous fixtures and test functions need to be marked with "@pytest.mark.asyncio"
anyio.BrokenResourceError: ...
```

**Log pattern:**
```bash
grep -E "Event loop is closed|no current event loop|asyncio.plugin.Error" test-results/
```

**Pre-merge test** — first run of `tox -e integration` with the converted harness will surface every failure at once. No incremental detection needed.

**Monitoring metric:** N/A — test-time only.

#### C. Mitigation

**Step 1 — Add pytest-asyncio to dev deps (5 min):**
```toml
# pyproject.toml
[project.optional-dependencies]
dev = [
    "pytest-asyncio>=0.23.0",
    ...
]
```

**Step 2 — Configure `pytest-asyncio` mode (5 min):**
```toml
# pyproject.toml or pytest.ini
[tool.pytest.ini_options]
asyncio_mode = "strict"
asyncio_default_fixture_loop_scope = "function"
```

Explicit per-test marker. Fresh loop per test. Forces clarity.

**Step 3 — Convert harness `__enter__` → `__aenter__` (2 hours):**
```python
# tests/harness/_base.py
class IntegrationEnv:
    async def __aenter__(self):
        self._session = SessionLocal()
        for f in ALL_FACTORIES:
            f._meta.sqlalchemy_session = self._session
        # Snapshot app.dependency_overrides (Risk #13 / deep-audit §3.3)
        self._override_snapshot = dict(app.dependency_overrides)
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self._session.close()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(self._override_snapshot)
```

**Step 4 — AST codemod for test files (4 hours).** Walk every `tests/integration/*.py` and `tests/e2e/*.py`, add `@pytest.mark.asyncio` to every test that uses `integration_db` or `IntegrationEnv`, convert `def` → `async def`, convert `with env:` → `async with env:`.

```python
# scripts/codemod_tests_to_async.py (throwaway)
import ast, pathlib

def convert(file: pathlib.Path):
    tree = ast.parse(file.read_text())
    # Walk FunctionDef nodes, check if they use integration_db, convert to AsyncFunctionDef.
    # Add @pytest.mark.asyncio decorator.
    # Rewrite `with IntegrationEnv()` → `async with IntegrationEnv()`.
    ...
```

**Step 5 — Factory-boy async adapter (4 hours).** `factory-boy`'s `SQLAlchemyModelFactory` has no async support as of 2026-04. The `core.py` and `principal.py` factories currently use `factory.alchemy.SQLAlchemyModelFactory` and set `sqlalchemy_session` via `_meta`. Options:

A. **Wrapper approach (lowest-risk):** factories build detached instances; test fixtures explicitly `session.add(obj)` and `await session.commit()`.

B. **Custom async factory base:**
```python
# tests/factories/_async.py
import factory
from sqlalchemy.ext.asyncio import AsyncSession

class AsyncSQLAlchemyModelFactory(factory.alchemy.SQLAlchemyModelFactory):
    @classmethod
    async def _create_async(cls, **kwargs):
        instance = cls.build(**kwargs)
        session: AsyncSession = cls._meta.sqlalchemy_session
        session.add(instance)
        await session.flush()
        return instance

    @classmethod
    def create_sync(cls, **kwargs):
        raise NotImplementedError("Use async create: await Factory.create_async_(**kwargs)")
```

C. **`session.run_sync(lambda s: Factory.create_sync(...))`:** dispatches the sync factory under SQLAlchemy's sync adaptor. Works but introduces a greenlet hop per factory call — measurable overhead on large test files.

**Recommendation:** Option B. Wrapping is explicit, no hidden greenlet hops, and factories still get their kwargs-resolver behavior. Requires updating every test call site from `Factory.create_sync(...)` → `await Factory.create_async_(...)`.

**Step 6 — Xdist scoping (0 days).** pytest-xdist already uses per-worker databases (`worker_id`-scoped). Each worker is a separate Python process with its own event loop. No change needed. Verify with: `tox -e integration -- -n 4`.

#### D. Fallback plan

If pytest-asyncio conversion proves too disruptive:
1. **Keep sync tests but wrap DB calls in `asyncio.run(...)`:** every test function wraps its async DB calls in `asyncio.run(async_helper())`. Ugly but works. ~0.5 day to implement as a sed-level rewrite.
2. **Use anyio instead of pytest-asyncio:** anyio has broader scope and plays better with session-scoped fixtures, but requires `@pytest.mark.anyio` markers and a different runner config.

**AdCP compliance:** N/A — tests don't touch the protocol surface.

**Point of no return:** Wave 4 — once factories are converted, reverting is a full rebuild.

---

### Risk #4 — Alembic async

#### A. Root cause

`alembic/env.py` currently uses a sync `engine_from_config` + `context.configure(connection=connection)`. Under async, the canonical pattern is:

```python
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine

async def run_async_migrations():
    connectable = create_async_engine(url)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()

def run_migrations_online():
    asyncio.run(run_async_migrations())
```

Migration scripts themselves stay sync (they run inside the `do_run_migrations` callback, which is called via `run_sync`). `op.execute(...)` and raw SQL work identically.

The risks:
- **First-time adopters misunderstand `run_sync`**: they add `await` to migration scripts and break them
- **`alembic upgrade head` during container boot** (`scripts/deploy/run_all_services.py` runs this) — if the async env.py isn't event-loop-safe in that context, boot fails
- **CI migration tests that use alembic programmatically** — e.g., `tests/integration/test_migrations.py` (if it exists) — may need adjustment

#### B. Detection

**Failure signatures:**
```
RuntimeError: asyncio.run() cannot be called from a running event loop
sqlalchemy.exc.InvalidRequestError: The asyncio extension requires an async driver
alembic.util.exc.CommandError: ...
```

**Pre-merge detection:** CI migration step. Every PR runs `alembic upgrade head` against a fresh DB as part of the integration test bootstrap. Any env.py breakage fails CI immediately.

**Log patterns:**
```bash
grep -E "asyncio.run.*cannot be called|async driver|alembic.*CommandError" test-results/
```

#### C. Mitigation

**Step 1 — Rewrite `alembic/env.py` (0.5 day):** use the canonical SQLAlchemy async alembic template (documented at https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html#using-asyncio-with-asyncpg). Copy-paste + tweak for project URL.

**Step 2 — Keep migration scripts sync (0 days):** Existing `alembic/versions/*.py` files don't need changes. The `do_run_migrations` callback runs them synchronously via `run_sync`.

**Step 3 — Add migration CI test (0.5 day):** `tests/integration/test_alembic_async_env.py`:
```python
@pytest.mark.requires_db
@pytest.mark.asyncio
async def test_alembic_upgrade_head_runs_clean(integration_db):
    from alembic.config import Config
    from alembic import command
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", integration_db_url)
    command.upgrade(cfg, "head")
    # Verify a known table exists
    async with integration_db.session() as s:
        result = await s.execute(text("SELECT 1 FROM tenants LIMIT 1"))
```

**Step 4 — Verify container-boot path (0.5 day):** Edit `scripts/deploy/run_all_services.py` / the Docker entrypoint to verify `alembic upgrade head` runs cleanly in an Alpine-like environment. Test locally with `docker compose build --no-cache && docker compose up -d`.

#### D. Fallback plan

If async env.py has issues:
- **Keep env.py sync, use sync engine only for migrations:** alembic can use a sync engine while the runtime uses an async engine. Both connect to the same database. Sync migrations work identically to today. **Recommended fallback — cleaner than fighting async alembic.**
  ```python
  # alembic/env.py — fallback
  def run_migrations_online():
      # Use sync URL even under async runtime
      sync_url = url.replace("postgresql+asyncpg://", "postgresql://")
      engine = create_engine(sync_url)
      with engine.connect() as connection:
          context.configure(connection=connection, target_metadata=target_metadata)
          with context.begin_transaction():
              context.run_migrations()
  ```
  This requires `psycopg2` to remain a build-time dependency even under async runtime — not a clean split. Acceptable for the fallback.

**AdCP compliance:** N/A.

**Point of no return:** Wave 4 — migrations run on every container boot. If this breaks post-deploy, rollback means reverting Wave 4.

---

### Risk #5 — `expire_on_commit=False` behavior change

#### A. Root cause

SQLAlchemy's `sessionmaker(expire_on_commit=True)` (the default) marks every attribute on every instance as "expired" after a commit. The next access re-fetches via SELECT. Under sync sessions, this is transparent. Under `AsyncSession`, the re-fetch would lazy-load → `MissingGreenlet` → crash. Setting `expire_on_commit=False` disables the expire.

**The side effect:** DB-computed default values (e.g., `created_at` columns with `server_default=func.now()`) are NOT populated on the ORM instance after `INSERT + COMMIT` unless you explicitly refresh or re-select. With `expire_on_commit=True` (sync default), the attribute auto-refreshes on access. With `expire_on_commit=False` (async requirement), it stays `None` (or the last-set client value).

This bites when code does:
```python
media_buy = MediaBuy(...)  # created_at not set (relies on server_default)
session.add(media_buy)
await session.commit()
logger.info(f"Created media buy at {media_buy.created_at}")  # None!
```

Or when serializing to JSON:
```python
return {"id": mb.media_buy_id, "created_at": mb.created_at.isoformat()}
# AttributeError: 'NoneType' object has no attribute 'isoformat'
```

#### B. Detection

**Failure signatures:**
```
AttributeError: 'NoneType' object has no attribute 'isoformat'
TypeError: strftime() argument 1 must be str, not NoneType
```

**Log pattern:** not a specific pattern, but any `AttributeError: NoneType` near `created_at`/`updated_at`/`inserted_at` is a smoking gun.

**Pre-merge test** — regression test on every insert path:
```python
# tests/integration/test_expire_on_commit_fields_populated.py
@pytest.mark.requires_db
@pytest.mark.asyncio
async def test_media_buy_created_at_populated_after_insert(sample_tenant):
    async with MediaBuyUoW(sample_tenant.tenant_id) as uow:
        mb = await uow.media_buys.create_from_request(req, identity)
    # After UoW exit (commit happened), created_at must be set
    assert mb.created_at is not None
```

**Detection strategy:** structural — grep for any attribute access on a model that has `server_default`:
```bash
grep -rn "created_at\|updated_at\|inserted_at" src/
```
Review each site for post-commit access.

#### C. Mitigation

**Step 1 — Set `expire_on_commit=False` explicitly (5 min):** already in the checkpoint plan. `async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)`.

**Step 2 — Audit server-default columns (2 hours).** Walk `src/core/database/models.py`, find every `mapped_column(... server_default=...)`. Tag each column as "SERVER_DEFAULT" in a comment; each one must be either:
- Explicitly set at instance-construction time (not relying on the server default for ORM access)
- Or explicitly refreshed post-commit: `await session.refresh(obj, attribute_names=["created_at"])`
- Or always set via `default=` instead of `server_default=` (client-side default executed on INSERT)

**Preferred fix:** replace `server_default=func.now()` with `default=datetime.utcnow` in column definitions. This moves the default to the ORM layer; the value is set at `session.add()` time, so it's visible before `commit()`. Side effect: the Postgres `DEFAULT` clause is dropped (check migration history — existing rows keep their values).

```python
# Before
created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

# After — ORM-side default
created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
```

**Step 3 — Add `session.refresh` in UoW where needed (0.5 day):** For rows that need DB-computed fields (e.g., auto-increment PKs, server-generated UUIDs), explicitly refresh:
```python
# In repository.create_from_request
async def create_from_request(self, req, identity):
    obj = Model(...)
    self.session.add(obj)
    await self.session.flush()  # Send INSERT, populate server-generated fields
    # flush() triggers a SELECT for RETURNING-based PKs; autoincrement PKs are populated
    return obj
```

For most CRUD paths, `flush()` (not `refresh()`) is the right fix — `flush()` sends the INSERT and populates server-generated PKs without expiring the instance. Refresh is only needed when the code reads a column that wasn't returned by RETURNING.

**Step 4 — Structural guard (0.25 day):** `test_architecture_no_server_default_without_refresh.py` — parses `models.py`, finds every `server_default=`, and fails if there's no `# NOQA: server-default-refreshed` comment attesting that callers handle the refresh. Lightweight; prevents regressions.

#### D. Fallback plan

There is no fallback from `expire_on_commit=False` — it's mandatory for async. If this creates too many breakages, the fallback is full rollback (Risk #1 fallback).

**AdCP compliance:** potential impact if `created_at` fields appear in wire format. Audit `src/core/schemas/*.py` for any Pydantic field that comes from a DB column with `server_default`. Spot check: the AdCP spec uses `created_at` / `updated_at` liberally in response bodies. If any of those come from DB columns with `server_default`, a missing field = `null` in the wire response = AdCP schema violation (field is typed as `datetime`, not `datetime | None`).

**THIS IS A POTENTIAL AdCP IMPACT** — call out to the team:
- Check `src/core/schemas/media_buy.py`, `creative.py`, etc. — do any response models have `created_at: datetime` (non-nullable)?
- If yes, the `expire_on_commit=False` change could inject `None` into a non-nullable AdCP field → Pydantic validation failure → 500 error to AdCP clients.
- Mitigation: the `default=datetime.utcnow` approach avoids this entirely.

**Point of no return:** Wave 4 entry.

---

### Risk #6 — Connection pool tuning under load

#### A. Root cause

Current sync config (`database_session.py:122-134`):
- `pool_size=10`
- `max_overflow=20`
- Total = 30 connections per process
- pool_timeout=30s

Under sync mode, the threadpool size (`anyio.to_thread.run_sync`'s default limiter is 40 threads) bounds how many sync queries can run concurrently. So the effective concurrency ceiling is min(30 DB connections, 40 threads) = 30.

Under async mode with `asyncpg`, every handler runs on the event loop. A single Python process can hold thousands of pending coroutines. If each coroutine calls `await session.execute(...)`, the DB connection pool becomes the ceiling. At `pool_size=10, max_overflow=20`, after 30 concurrent queries, the 31st waits up to `pool_timeout=30s` for a connection. Under traffic bursts, requests queue → latency spikes → `TimeoutError: QueuePool limit of size 10 overflow 20 reached`.

Additionally, `asyncpg` itself has a connection pool at the asyncpg level (separate from SQLAlchemy's). Under SQLAlchemy async, the SQLAlchemy pool wraps the asyncpg connection. The asyncpg pool is not exposed — `pool_size` is the SQLAlchemy pool size, which maps 1-to-1 to asyncpg connections.

Long-lived sessions (SSE endpoints at `src/admin/blueprints/activity_stream.py`) are particularly dangerous: an SSE handler that holds a session for 60s pins one connection for 60s. 10 simultaneous SSE viewers = 10 connections pinned = the rest of the app has only `overflow=20` capacity.

#### B. Detection

**Failure signatures:**
```
sqlalchemy.exc.TimeoutError: QueuePool limit of size 10 overflow 20 reached, connection timed out, timeout 30.00
asyncpg.exceptions.TooManyConnectionsError: sorry, too many clients already
```

**Log pattern:**
```bash
grep -E "QueuePool limit|TooManyConnectionsError|connection timed out" production-logs/
```

**Monitoring metric:**
- Expose `get_pool_status()` (already exists at `database_session.py:430`) via `/health` endpoint
- Alert on `checked_out > 0.8 * (size + overflow)` sustained for >30s
- Track `connection_wait_time_ms` histogram

**Pre-merge detection — load test harness:**
```python
# tests/performance/test_async_pool_saturation.py
@pytest.mark.performance
@pytest.mark.asyncio
async def test_admin_list_accounts_under_100_concurrent(admin_client):
    async def one_request():
        resp = await admin_client.get("/admin/tenant/default/accounts")
        assert resp.status_code == 200

    # Fire 100 concurrent requests
    await asyncio.gather(*[one_request() for _ in range(100)])

    pool = get_pool_status()
    assert pool["checked_out"] == 0, "Connections not returned to pool"
```

#### C. Mitigation

**Step 1 — Baseline benchmark (1 day).** Run a pre-Wave-0 benchmark on the sync stack to establish baseline numbers:
- Throughput (requests/sec at 10, 50, 100 concurrent)
- p50/p95/p99 latency
- Pool saturation point (concurrency level where 95% of requests wait >100ms for a connection)
- Connection count at peak

**Step 2 — Set async pool config (0.25 day):**
```python
_engine = create_async_engine(
    url,
    pool_size=20,       # Up from 10 — async doesn't have threadpool contention
    max_overflow=30,    # Up from 20
    pool_timeout=30,
    pool_pre_ping=True,
    pool_recycle=3600,
)
```

**Rationale:** async wins from higher concurrency, so pool must be bigger. Target `pool_size + max_overflow = 50` as a safe starting point.

**Step 3 — Document Postgres-side connection limit (0.25 day):** Postgres `max_connections` defaults to 100. At `pool_size=50` per worker × 1 worker = 50 connections → 50% of `max_connections`. Add a runtime assertion at startup:
```python
# At engine init time
max_pg_connections = await session.execute(text("SHOW max_connections")).scalar()
assert _engine.pool.size() + _engine.pool._max_overflow < int(max_pg_connections) * 0.8
```

**Step 4 — SSE/long-lived handler session-lifetime audit (0.5 day):** identify every handler that holds a session for >1s. Grep:
```bash
grep -rln "EventSourceResponse\|stream\|StreamingResponse" src/admin/
```

For each, verify the session is released between ticks:
```python
# Bad — pin a connection
async def sse_stream():
    async with get_db_session() as session:
        while True:
            events = await session.execute(...)
            yield format_event(events)
            await asyncio.sleep(1)

# Good — release between ticks
async def sse_stream():
    while True:
        async with get_db_session() as session:
            events = await session.execute(...)
        yield format_event(events)
        await asyncio.sleep(1)
```

Apply pattern: no `async with get_db_session()` around `while True` loops.

**Step 5 — Post-deploy pool monitoring (1 hour):** add `/health/pool` endpoint that returns `get_pool_status()` as JSON. Datadog/Prometheus scraper ingests; alert on high checked_out.

**Step 6 — PgBouncer evaluation (defer to v2.1):** if prod load exceeds `max_connections`, PgBouncer is the next step. NOT required for v2.0.

#### D. Fallback plan

If pool saturation becomes a P0 in production:
1. **Grow pool size on-the-fly** — bump `pool_size`/`max_overflow` via env vars, rolling restart. No code change.
2. **Add PgBouncer** — deploy PgBouncer in front of Postgres. Requires `prepared_statement_cache_size=0` in asyncpg config. ~0.5 day in production.
3. **Per-request session lifetime reduction** — shorter-lived sessions, more acquire/release overhead but better pool fairness.

**AdCP compliance:** no impact (pool config is internal).

**Point of no return:** N/A — pool config is hot-tunable.

---

### Risk #7 — MCP scheduler async DB

#### A. Root cause

`src/core/main.py::lifespan_context` starts `delivery_webhook_scheduler` and `media_buy_status_scheduler` as background asyncio tasks. These tasks loop, ticking every N seconds, and touch the DB. Under sync mode, each tick called sync DB helpers inside `run_in_threadpool` (or sync-in-async, which has the scoped_session interleaving bug — deep-audit §R11 hints at this). Under async mode, each tick must use `async with get_db_session() as s:` and `await s.execute(...)`.

The lifespan composition at `src/app.py:68` (`combine_lifespans(app_lifespan, mcp_app.lifespan)`) is already async. Schedulers already run inside it. The change is internal — replace sync DB calls with async DB calls inside the scheduler tick bodies.

**But** there are subtle risks:
- **Scheduler DB calls consume connections from the same pool as request handlers.** If a scheduler hogs connections (e.g., long-running status-poll queries), request latency spikes.
- **Scheduler exception handling:** under sync mode, a raised exception in a tick handler that uses scoped_session leaves the session in a bad state but the scoped_session's `.remove()` cleanup on request boundary helps. Under async, no scoped_session → exceptions in ticks must be caught and the session rolled back manually. A tick that fails mid-commit leaves a session with pending operations.
- **Shutdown ordering:** on container shutdown (`SIGTERM`), the lifespan exit cancels the scheduler tasks. If a scheduler is mid-`await session.commit()`, the cancel injects a `CancelledError` into the commit — the connection may be left in an inconsistent state. The engine's `dispose()` call during lifespan exit may hang waiting for the connection.

#### B. Detection

**Failure signatures:**
```
asyncio.CancelledError during session.commit()
sqlalchemy.exc.InvalidRequestError: Session already closed
RuntimeError: Task was destroyed but it is pending!
```

**Log pattern (scheduler-specific):**
```bash
grep -E "scheduler.*error|scheduler.*CancelledError" production-logs/
```

**Pre-merge test — scheduler tick test:**
```python
# tests/integration/test_scheduler_async_db.py
@pytest.mark.requires_db
@pytest.mark.asyncio
async def test_delivery_webhook_scheduler_tick(sample_media_buy):
    from src.core.webhook_scheduler import delivery_webhook_scheduler_tick
    await delivery_webhook_scheduler_tick()
    # Verify webhook was dispatched or status updated
    ...

@pytest.mark.asyncio
async def test_scheduler_shutdown_clean(integration_db):
    task = asyncio.create_task(_scheduler_loop())
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # Verify no leaked connections
    pool = get_pool_status()
    assert pool["checked_out"] == 0
```

#### C. Mitigation

**Step 1 — Convert scheduler tick bodies (1 hour):**
```python
# src/core/webhook_scheduler.py (or wherever)
async def _tick_once():
    try:
        async with get_db_session() as session:
            result = await session.execute(
                select(MediaBuy).filter_by(status="pending_webhook")
            )
            for mb in result.scalars().all():
                await _dispatch_webhook(mb)
    except Exception as e:
        logger.exception("Scheduler tick failed: %s", e)
        # Don't re-raise — let the loop continue
```

Key: each tick is its own `async with` block. Exceptions caught and logged. No shared session across ticks.

**Step 2 — Graceful cancellation (0.5 day):**
```python
async def _scheduler_loop():
    while True:
        try:
            await _tick_once()
            await asyncio.sleep(TICK_INTERVAL)
        except asyncio.CancelledError:
            logger.info("Scheduler cancelled, exiting loop")
            return
```

Cancellation propagates from the loop wait (`asyncio.sleep`), not from the tick body. Between ticks, cancellation is clean.

**Step 3 — Alive-tick log (0.25 day):** on first successful tick, emit `logger.info("delivery_webhook_scheduler alive")` so production dashboards can detect silent failures (per deep-audit §3.7).

**Step 4 — Lifespan-composition guard already in plan (§3.7):** `test_architecture_scheduler_lifespan_composition.py` asserts `combine_lifespans` is still in `src/app.py`. No additional guard needed for async conversion.

#### D. Fallback plan

If scheduler async conversion creates connection leaks or shutdown hangs:
1. **Run scheduler bodies via `run_in_threadpool`** — scheduler tick stays sync internally, uses sync session factory. Works but reintroduces the cross-boundary complexity. Available as an escape hatch.
2. **Move scheduler to a separate process:** out of scope for v2.0.

**AdCP compliance:** RISK — if schedulers silently stop, webhooks stop firing, AdCP callers stop receiving creative-approval notifications. The alive-tick log (Step 3) is the main defense.

**Point of no return:** Wave 4 — scheduler bodies converted. Rollback means reverting scheduler conversion.

---

### Risk #7.5 — Background sync long-session multi-hour failure

**Severity: HIGH** | **Added 2026-04-11 (Decision 9 deep-think)** | **Resolved by sync-bridge**

#### A. Root cause

`src/services/background_sync_service.py` runs **multi-hour GAM inventory sync jobs** via `threading.Thread` workers. Each worker holds a single session for the lifetime of the sync (potentially 2-6 hours for large publishers). Under absorbed-async with `AsyncSession` + `asyncpg`, this pattern is **incompatible by construction** with three distinct failure modes that compound:

1. **`pool_recycle=3600` rotates connections under the session.** asyncpg/SQLAlchemy's pool recycles physical connections after 1 hour to defend against stale sockets and DB-side `idle_in_transaction` timers. A 4-hour sync that holds an `AsyncSession` across recycle boundaries gets `OperationalError: connection is closed in the middle of a transaction`. The session has no awareness of the rotation — it discovers it on the next `await session.execute()` and the entire sync rolls back.

2. **Identity map grows unbounded.** `Session.identity_map` accumulates every loaded ORM instance. A multi-hour sync that pages through 500k inventory items per tenant accumulates ~2-4 GB of `Inventory` objects in memory. Async sessions have NO automatic eviction.

3. **Fly.io TCP keepalive expiry.** Fly.io's edge proxies time out idle TCP connections after ~5 minutes. A long sync that processes a single GAM API page over a 6-minute interval (slow rate-limited reporting endpoint) without round-tripping to the DB loses the asyncpg connection mid-sync.

This is **not theoretical**. The Wave 3 `from flask import current_app` ImportError at line 472 is symptomatic of the service being lifted-and-shifted from Flask without correctly handling its threading model — the root cause is that the service was never designed for the FastAPI runtime.

#### B. Detection

**Pre-Wave-0 (Spike 5.5):** prove async asyncpg engine + sync psycopg2 engine coexist in one Python process. 4 test cases:
- (a) engine lazy-init + dispose cycle clean (no leaked tasks, no leaked file descriptors via `lsof`)
- (b) MVCC visibility bidirectional (async write commits → sync `SELECT` sees the row; sync write commits → async `await session.execute(SELECT)` sees the row, both within `<1s` to confirm there's no read-isolation pinning)
- (c) 5 concurrent async requests + 1 sync background thread issuing 100 statements over 60s — no deadlock, no `TimeoutError`, no `pool overflow exceeded`
- (d) post-`dispose_sync_bridge()` + post-`engine.dispose()` connection leaks ≤1 from baseline (pgs `pg_stat_activity`)

**In production:** add `application_name='adcp-salesagent-sync-bridge'` to the sync-bridge engine so `pg_stat_activity` distinguishes between three engines (async-main, sync-pathb, sync-bridge). Add an alert on `pg_stat_activity.state_change` >1 hour for the sync-bridge connection — long-running sessions are EXPECTED, but rotated/zombie connections must alert.

#### C. Mitigation (the resolution)

**Decision 9: Option B sync-bridge.** New module `src/services/background_sync_db.py` (~200 LOC) exposes:
- `get_sync_engine()` — lazy-init psycopg2 engine, pool 2+3, `statement_timeout=600s`, `application_name='adcp-salesagent-sync-bridge'`, **no** `pool_recycle` (the worker explicitly rotates sessions per GAM API page)
- `get_sync_db_session()` — sync `Session` factory bound to the bridge engine
- `dispose_sync_bridge()` — explicit dispose called from the lifespan shutdown after `wait_for_shutdown(30s)`

The 9 `get_db_session()` call sites in `background_sync_service.py` migrate to `get_sync_db_session()`. Background `threading.Thread` workers stay sync (intentionally — Decision 1 confirms threadpool capacity at 80 has headroom). The async request path is untouched.

**Pool math across all three engines (within PG `max_connections=100`):**
- async asyncpg engine: pool 15 + max_overflow 25 = 40 peak
- sync Path-B engine (Decision 1): pool 5 + max_overflow 10 = 15 peak
- sync sync-bridge engine (Decision 9): pool 2 + max_overflow 3 = 5 peak
- **Total: 60 peak connections**, leaving 40 headroom for admin connections + monitoring + Alembic.

**Shutdown ordering** (in `lifespan` shutdown phase):
1. `request_shutdown()` — set the global stop flag
2. `await wait_for_shutdown(30s)` — give running syncs time to finish their current GAM page
3. `await dispose_sync_bridge()` — dispose the sync engine
4. `await async_engine.dispose()` — dispose the async engine

#### D. Fallback

**Soft blocker, fallback to Option A** (asyncio task + single async session per sync, suboptimal but viable):
- Replace `threading.Thread` with `asyncio.create_task` running an `async def sync_worker(...)` coroutine
- Each sync gets its own `async with session_scope()` for the entire duration
- Mitigates failure modes 1 (pool_recycle) and 3 (Fly.io TCP keepalive) by checkpointing state to the DB after every GAM page (~30s) — the session never sits idle long enough to be rotated or timed out
- Failure mode 2 (identity map growth) is mitigated via explicit `session.expire_all()` after each page commit

Option A is suboptimal because it puts multi-hour CPU work on the event loop's threadpool budget (every `await asyncio.sleep(0)` cooperates back, but rate-limit waits between GAM pages dominate). Spike 5.5 fail action: document the choice in `spike-decision.md` and proceed with Option A.

**Sunset target v2.1+:** even Option B is a stopgap. The right long-term fix is a phase-per-session async refactor (each GAM page = one short-lived session, checkpointed via Alembic-tracked job state in `sync_jobs` table). This is intentionally deferred — the sync-bridge gives v2.0 a shippable shape without forcing the larger refactor.

#### E. AdCP compliance

LOW RISK — `background_sync_service` is internal-only. It does not expose an AdCP surface. The only AdCP-visible failure mode is "creative approval webhooks delayed because sync didn't finish on schedule" which is bounded by the existing scheduler ticks, not by the sync-bridge.

#### F. Point of no return

Wave 4 — once `background_sync_db.py` is shipped and `psycopg2-binary` is reinstated in `pyproject.toml`, rolling back means re-deleting the module + reverting the partial Agent F F1.1.1/F1.2.1 reversal + accepting that the multi-hour-sync failure will surface in production. No clean rollback after Wave 4 ships.

---

### Risk #8 — A2A handler async

#### A. Root cause

`src/a2a_server/adcp_a2a_server.py` uses the `python-a2a` SDK (now `a2a-sdk`). The SDK is built on Starlette (verified in deep-audit §3.8), so handlers are natively async. If any A2A handler currently uses sync DB calls inline (not via a sync-to-async bridge), it has the same interleaving bug as `api_v1.py`.

Grep for `_impl` usage in A2A handlers — every `_impl` is already async (per the checkpoint's note that the pivot FIXES `api_v1.py`, which implies the `_impl` layer is already async-aware). A2A conversion should be mechanical.

#### B. Detection

**Failure signatures:** same as Risk #1 (MissingGreenlet) if a lazy load happens outside a session scope.

**Pre-merge test — A2A tool smoke test:**
```python
# tests/integration/test_a2a_async_handlers.py
@pytest.mark.requires_db
@pytest.mark.asyncio
async def test_a2a_create_media_buy_smoke(a2a_client, sample_tenant):
    req = CreateMediaBuyRequest(...)
    resp = await a2a_client.call_tool("create_media_buy", **req.model_dump())
    assert resp.status == "success"
```

#### C. Mitigation

**Step 1 — Convert A2A handler bodies (2 hours):** for each handler in `adcp_a2a_server.py`, convert internal DB calls to `await async_repo.method(...)`. Since `_impl` functions are already async (per the pivot), A2A handlers just `await _impl(...)` as before.

**Step 2 — Verify dynamic agent-card swap still works (0.25 day):** `_replace_routes()` at `src/app.py:192-215` handles dynamic agent cards. It walks `app.routes` and replaces three SDK routes. Under async, the swap mechanics are unchanged — routes are still Starlette `Route` objects. Verify via existing `test_architecture_a2a_routes_grafted.py` guard.

#### D. Fallback plan

If A2A SDK interop has issues:
- **Per-handler `await anyio.to_thread.run_sync(sync_helper)`**: dispatch DB calls to threadpool. Works but reintroduces the threadpool contention, which is what we're trying to avoid.

**AdCP compliance:** A2A is an AdCP protocol surface — any handler failure breaks AdCP callers. High priority to test.

**Point of no return:** Wave 4.

---

### Risk #9 — Middleware state propagation

#### A. Root cause

All middleware classes are already pure ASGI async (per deep-audit §3.9 and checkpoint §2 ordering notes). `UnifiedAuthMiddleware`, `RestCompatMiddleware`, `CSRFMiddleware`, `ApproximatedExternalDomainMiddleware` — no conversion needed.

**The one subtle risk:** middleware state-sharing via `scope["state"]`. Starlette's `scope["state"]` is a shared dict for middleware-to-handler communication. Under async, `scope` is per-request (not global), so there's no cross-request state leak. But: if any middleware stashes a DB session in `scope["state"]["db_session"]`, that session must be released in middleware teardown (after the response is sent). Sync sessions can be cleaned up on the main thread; async sessions must be `await`-cleaned.

Grep:
```bash
grep -rn "scope\[\"state\"\]\|scope.state" src/
```

#### B. Detection

**Pre-merge test — middleware state smoke:**
```python
# tests/integration/test_middleware_state_propagation.py
@pytest.mark.asyncio
async def test_auth_middleware_scope_state(admin_client):
    resp = await admin_client.get("/admin/tenant/default/dashboard")
    # Middleware should have attached auth_context to scope["state"]
    # Test-side verification via response headers or debug endpoint
```

#### C. Mitigation

**Step 1 — Grep for middleware session stash (0.25 day):** verify no middleware stashes a DB session.

**Step 2 — Verify middleware ordering unchanged (0 days):** the critical invariant (Approximated BEFORE CSRF per deep-audit blocker 5) is unchanged by the async pivot.

#### D. Fallback plan

N/A — middleware is already async.

---

### Risk #10 — Performance characteristics

#### A. Root cause

Async has different performance profile than sync:
- **Low concurrency (1-10 req/s):** async slightly slower due to event loop overhead (~5-10% latency increase)
- **Medium concurrency (10-100 req/s):** async roughly equivalent
- **High concurrency (100+ req/s):** async significantly better (no threadpool contention)

salesagent's traffic profile: admin UI is low concurrency (a few admins clicking buttons). MCP tool calls are bursty (agentic workflows may fire 10-50 concurrent requests). A2A is similar to MCP. So the mix is low admin + bursty tool calls.

**Net expectation:** neutral to positive. Admin routes slightly slower (imperceptible); tool calls much faster under load.

**Risks:**
- **Warm-path latency regression** on admin UI dashboard — user clicks a button, the response takes 200ms instead of 180ms. Small absolute delta but perceptible. Likely because of the extra `async with get_db_session()` overhead vs inline scoped_session.
- **Cold-start latency:** first request after process start allocates the asyncpg pool, which is slower than the sync pool. Single-worker deployments may see a 1-2s cold-start delay.

#### B. Detection

**Monitoring metric:**
- p50/p95/p99 request latency per-route, dashboarded
- Alert on `p95 > 2x baseline` for any admin route

**Pre-merge benchmark harness:**
```python
# tests/performance/bench_admin_routes.py
@pytest.mark.performance
def test_bench_admin_accounts_list(admin_client, benchmark):
    def _run():
        resp = admin_client.get("/admin/tenant/default/accounts")
        assert resp.status_code == 200
    benchmark(_run)
```

Run pre- and post-pivot. Compare via `pytest-benchmark`'s output. Flag any route with >20% latency regression.

#### C. Mitigation

**Step 1 — Baseline benchmark (1 day pre-Wave-0):** capture latency for 20 representative admin routes and 5 representative MCP tool calls on the sync stack. Store as `tests/performance/baseline.json`.

**Step 2 — Post-Wave-4 benchmark (0.5 day):** re-run the same suite on async. Compare. Flag regressions.

**Step 3 — Connection pool warmup (0.25 day):** at startup, acquire N connections and release them, to pre-populate the pool. Reduces cold-start latency:
```python
async def _warm_pool():
    conns = []
    for _ in range(pool_size):
        conn = await _engine.connect()
        conns.append(conn)
    for conn in conns:
        await conn.close()
```

**Step 4 — Route-level tuning (0.5 day if regressions found):** for routes that regress, inspect query count (use SQLAlchemy echo), add eager loading.

#### D. Fallback plan

If performance regresses >20% on any route:
1. Profile with `py-spy` or `austin`. Usually root cause is a lazy-load fix that converted 1 query into N queries (N+1 problem).
2. Fix the specific query (eager loading) — keeps async.
3. If unfixable, partial rollback of that route is impossible (sync def handler would reintroduce the scoped_session bug). Full rollback is the only option.

**AdCP compliance:** AdCP has no latency SLOs in the spec, but sustained >200ms p95 regressions on MCP tool calls would degrade user experience of AdCP clients.

**Point of no return:** Wave 5 merge to main. Post-merge, performance regressions require a hotfix or a point release.

---

### Risk #11 — Debugging complexity

#### A. Root cause

Async stack traces have "greenlet hops" — the trace shows sync frames (normal code), greenlet frames (SQLAlchemy's async-to-sync bridge), and async frames (FastAPI/asyncio). New engineers find this confusing.

Common traps:
- Forgetting `await` — silently returns a coroutine object instead of calling the function. `result.scalars()` on a coroutine raises `AttributeError: 'coroutine' object has no attribute 'scalars'` which is confusing.
- Catching `CancelledError` and swallowing it — breaks graceful shutdown.
- Using `asyncio.run(...)` inside an already-running loop.
- Mixing sync and async contexts (e.g., calling `run_in_threadpool` from inside an async context that already holds a session → deadlock).

#### B. Detection

N/A — not runtime detectable. Training issue.

#### C. Mitigation

**Step 1 — Write a troubleshooting runbook (2 hours):** `docs/development/async-debugging.md` with:
- How to read `MissingGreenlet` stack traces
- How to interpret `'coroutine' object has no attribute...` (missing await)
- How to debug `cannot perform operation: another operation is in progress`
- When to use `run_in_threadpool` (never for DB, only for file I/O / CPU / sync libs)
- How to test async code (pytest-asyncio examples)

**Step 2 — Enable warnings (0.25 day):** `asyncio.set_debug(True)` in dev/test, which:
- Logs slow callbacks (`>100ms` sync work blocking the loop)
- Logs never-awaited coroutines
- Enables stricter type checks

```python
# src/app.py
if os.environ.get("ENVIRONMENT") != "production":
    import asyncio
    asyncio.get_event_loop().set_debug(True)
```

**Step 3 — Lint for common async bugs (0.5 day):** add pylint or ruff rules for:
- `B902` — never use `asyncio.run()` in lib code
- Custom rule: `async def` functions must `await` at least one coroutine or yield control (detects sync work disguised as async)

#### D. Fallback plan

N/A — education.

---

### Risk #12 — SessionContextVar / per-request state propagation

#### A. Root cause

Flask uses `flask.g` (a thread-local) for per-request state. The deep audit (§2.1) says there's 1 write site at `src/admin/utils/audit_decorator.py:18`. Starlette's equivalent is `request.state` (a per-request attribute, not a `ContextVar`). Under async, `request.state` is per-request and doesn't leak across tasks.

**But** if any code uses Python's `contextvars.ContextVar` directly (e.g., for a "current tenant" that's not passed through function args), the propagation semantics differ:
- Under sync + `scoped_session(scopefunc=get_ident)`, the ContextVar is thread-local. Each request runs on a thread, so each gets its own value.
- Under async + `scoped_session(scopefunc=asyncio.current_task)`, the ContextVar is task-local. Each request is a task, so each gets its own value.
- Under async without scoped_session (our target), ContextVars propagate through `asyncio.create_task(...)` via the `copy_context()` mechanism. A fire-and-forget background task inherits the parent's ContextVar state. **Most of the time this is correct**, but if the background task outlives the request, the ContextVar may reference stale data.

Grep:
```bash
grep -rn "ContextVar\|contextvars\." src/
grep -rn "flask.g\|from flask import g" src/
```

#### B. Detection

**Failure signature:** subtle — audit logs attributed to the wrong user, or missing audit logs entirely if the ContextVar isn't set in async context.

**Pre-merge test — audit logger regression:**
```python
# tests/integration/test_audit_logger_async_context.py
@pytest.mark.requires_db
@pytest.mark.asyncio
async def test_audit_log_written_for_account_create(admin_client):
    resp = await admin_client.post("/admin/tenant/default/accounts", json={...})
    assert resp.status_code == 200
    # Verify audit log was written with the correct user
    async with get_db_session() as s:
        log = await s.execute(select(AuditLog).order_by(AuditLog.id.desc()).limit(1))
        log = log.scalars().first()
        assert log.user_email == "test@example.com"
```

#### C. Mitigation

**Step 1 — Grep for ContextVar usage (0.25 day):** confirm the scope. If only 1 or 2 sites, manual fix. If many, design a shared helper.

**Step 2 — Replace `flask.g` with `request.state` (deep-audit §R2 already plans this):** rewrite `audit_decorator` to store state in `request.state.audit_logger_<tid>`.

**Step 3 — Background task handling (0.25 day):** for `asyncio.create_task(...)` calls that spawn background work, the task inherits the current ContextVar. If the task should NOT see the caller's context (e.g., a shared scheduler tick), wrap the task:
```python
# Background work that should NOT inherit caller context
async def _background(args):
    with contextvars.copy_context() as ctx:
        # Reset context to default
        ctx.run(...)
```

#### D. Fallback plan

N/A — ContextVar propagation is fundamentally a language feature.

**AdCP compliance:** audit logs are NOT AdCP-protocol (internal). No AdCP impact.

**Point of no return:** Wave 4.

---

### Risk #13 — FastMCP tool function registration

#### A. Root cause

FastMCP (`src/core/main.py:300-315` area) registers tool functions via `@mcp.tool()` decorator. FastMCP supports both sync and async tool functions — the decorator inspects the function signature and dispatches accordingly. Under sync, the tool runs in a threadpool. Under async, the tool runs on the event loop. Conversion is mechanical: change `def` → `async def` and add `await` to internal calls.

**Subtle issue:** if a tool function is already `async def` but its body is sync code (no `await`), FastMCP dispatches it to the event loop — which is fine, just wasted async overhead. No bug.

**Bigger issue:** FastMCP's tool signature introspection uses `inspect.signature()`. If we decorate a method that's `@functools.wraps`'d by an async-to-sync adapter, the signature may be corrupted. Low risk but check.

#### B. Detection

**Pre-merge test — list_tools smoke:**
```bash
uvx adcp http://localhost:8000/mcp/ --auth test-token list_tools
```
Should return the full tool catalog with correct signatures. Any mismatch fails obviously.

**Failure signature:**
```
fastmcp.exceptions.ToolError: Tool 'create_media_buy' signature mismatch
TypeError: Unexpected keyword argument
```

#### C. Mitigation

**Step 1 — Audit all `@mcp.tool()` registrations (0.25 day):** grep in `src/core/main.py`, verify every registered tool is `async def` post-conversion.

**Step 2 — Manual smoke test in Wave 4 (0.25 day):** run `uvx adcp ... list_tools` after conversion. Compare against pre-conversion output.

#### D. Fallback plan

Per-tool sync escape: if any tool function doesn't convert cleanly, leave it `def` — FastMCP handles both. No conflict. AdCP-safe.

---

### Risk #14 — Scheduler singleton under multi-worker

Unchanged from sync plan. Already guarded by `test_architecture_single_worker_invariant.py` per deep-audit §5.8. Async pivot does not affect this.

**Point of no return:** N/A — invariant is enforced by structural guard.

---

### Risk #15 — Pre-existing `api_v1.py` interleaving bug (FIXED)

This is a WIN, not a risk. Under the pivot, `src/routes/api_v1.py` routes (already `async def`) call `_impl` functions (now `async def`), eliminating the sync-call-from-async interleaving via scoped_session.

**No action required.** Add a regression test verifying concurrent `api_v1` requests don't interleave:
```python
# tests/integration/test_api_v1_no_interleaving.py
@pytest.mark.requires_db
@pytest.mark.asyncio
async def test_concurrent_get_products_no_cross_tenant_leak(rest_client):
    async def call_for_tenant(tid):
        return await rest_client.post(
            f"/api/v1/get_products",
            headers={"x-adcp-auth": tokens[tid]},
            json={"brief": "..."}
        )
    results = await asyncio.gather(*[call_for_tenant(t) for t in range(20)])
    # Each result must be for its own tenant
    for tid, resp in enumerate(results):
        assert resp.json()["tenant_id"] == f"tenant_{tid}"
```

---

## Section 3 — Lazy-Load Cookbook

Goal: every admin handler and `_impl` function can pick the right pattern in <30 seconds. Decision tree + 7 concrete patterns.

### Decision tree

```
Am I loading a relationship?
├── Yes, and I'll access it in the same async function before commit?
│   → Pattern 1: selectinload / joinedload in repository query
├── Yes, and the relationship is a many-to-one (parent/FK)?
│   → Pattern 2: joinedload (single OUTER JOIN)
├── Yes, and the relationship is a has-many (collection)?
│   → Pattern 3: selectinload (1 extra query per collection)
├── Yes, but the access happens in a template after handler returns?
│   → Pattern 1 + force load in handler body before return
├── No, I want to explicitly forbid lazy loads site-wide?
│   → Pattern 4: raiseload() in query or lazy="raise" in model
├── I need to refresh a specific attribute post-commit (server_default)?
│   → Pattern 5: await session.refresh(obj, attribute_names=[...])
├── I have legacy sync code that I can't convert yet?
│   → Pattern 6: session.run_sync(lambda s: obj.rel) — escape hatch
└── Want to disable auto-expire entirely?
    → Pattern 7: expire_on_commit=False in sessionmaker — already in plan
```

### Pattern 1 — `selectinload(...)` for has-many collections

**When to use:** you have a parent with a collection relationship (`tenant.products`, `media_buy.packages`). Loads parent in 1 query, then fires ONE additional query to fetch all children via `WHERE parent_id IN (...)`. O(2) queries regardless of collection size.

```python
# src/core/database/repositories/media_buy.py
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

class MediaBuyRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_with_packages(self, media_buy_id: str, tenant_id: str) -> MediaBuy | None:
        stmt = (
            select(MediaBuy)
            .filter_by(media_buy_id=media_buy_id, tenant_id=tenant_id)
            .options(selectinload(MediaBuy.packages))  # <-- eager-load collection
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()
```

**Usage:**
```python
async with MediaBuyUoW(tenant_id) as uow:
    mb = await uow.media_buys.get_with_packages("mb_123", tenant_id)
    # mb.packages is now populated — no lazy load, no greenlet error
    for pkg in mb.packages:
        print(pkg.package_id)
```

**Failure mode to watch:** forgetting to match `.options()` to the exact relationships the caller uses. If the handler reads `mb.packages` but the repo only loaded `mb.principal`, you get `MissingGreenlet` at `mb.packages` access.

**Rule:** each repository method declares which relationships it eager-loads in its docstring. Tests call the method, access every loaded relationship, and verify no greenlet error.

---

### Pattern 2 — `joinedload(...)` for many-to-one / one-to-one

**When to use:** you have a child with a parent relationship (`media_buy.principal`, `package.media_buy`). Loads both in a single `SELECT ... LEFT OUTER JOIN`. Best for parent FK relationships where the parent is small and always fetched.

```python
async def get_with_principal(self, media_buy_id: str, tenant_id: str) -> MediaBuy | None:
    stmt = (
        select(MediaBuy)
        .filter_by(media_buy_id=media_buy_id, tenant_id=tenant_id)
        .options(joinedload(MediaBuy.principal))  # <-- single JOIN
    )
    result = await self.session.execute(stmt)
    return result.scalars().first()
```

**When NOT to use:** deeply nested joinedloads (`joinedload(A.b).joinedload(B.c).joinedload(C.d)`) produce cartesian explosion. Use `selectinload` for the nested layers instead:

```python
# Mixed — OK
.options(joinedload(MediaBuy.principal), selectinload(MediaBuy.packages))
```

**Failure mode:** joinedload on a has-many collection causes row duplication (each row repeats the parent columns). SQLAlchemy deduplicates by PK, but you can get "collection not unique" warnings. Use `selectinload` for collections.

**Rule:** joinedload for many-to-one (`foreign_key_id -> parent`). Selectinload for one-to-many (`parent -> [children]`).

---

### Pattern 3 — `selectinload(...)` chained

**When to use:** multi-level nested relationships.

```python
async def get_tenant_with_all_products_and_pricing(self, tenant_id: str):
    stmt = (
        select(Tenant)
        .filter_by(tenant_id=tenant_id)
        .options(
            selectinload(Tenant.products).selectinload(Product.pricing_options),
            selectinload(Tenant.adapter_config),
        )
    )
    result = await self.session.execute(stmt)
    return result.scalars().first()
```

Three queries total: tenant, products, pricing_options. All children loaded eagerly, no lazy fallback.

**Rule:** each nesting level needs its own `.selectinload(...)` chain.

---

### Pattern 4 — `raiseload(...)` for explicit failure

**When to use:** you want to guarantee a specific relationship is NEVER lazy-loaded, as a defensive measure. Raises `InvalidRequestError` if accessed without eager loading.

```python
from sqlalchemy.orm import raiseload

async def get_tenant_minimal(self, tenant_id: str):
    """Returns a Tenant with NO relationships loaded. Accessing any relationship
    raises an error — prevents accidental lazy loads in downstream code."""
    stmt = (
        select(Tenant)
        .filter_by(tenant_id=tenant_id)
        .options(raiseload("*"))  # <-- any relationship access raises
    )
    result = await self.session.execute(stmt)
    return result.scalars().first()
```

**When to use at query level:** you're returning a slim DTO where the caller doesn't need relationships. Raiseload prevents future maintainers from "accidentally" adding a lazy load.

**Failure mode:** `InvalidRequestError: 'X.y' is not available due to lazy='raise'`. This is a LOUD failure, which is the point.

**Rule:** use `raiseload("*")` on query methods that return minimal objects. Use `lazy="raise"` on the relationship definition itself for stronger guarantees.

---

### Pattern 5 — `lazy="raise"` on the relationship definition

**When to use:** static enforcement at the model level. Every access to this relationship — regardless of query — must go through explicit eager loading.

```python
# src/core/database/models.py
class Tenant(Base):
    products: Mapped[list["Product"]] = relationship(
        "Product",
        back_populates="tenant",
        cascade="all, delete-orphan",
        lazy="raise",  # <-- relationship-level guard
    )
```

Now `tenant.products` raises unless the query explicitly used `selectinload(Tenant.products)`.

**Why:** this is the strongest guarantee — you can't forget `raiseload`, and structural guards can assert every relationship has an explicit `lazy=` kwarg.

**Step-3 from Risk #1 mitigation** applies this pattern to all 68 relationships after Step 2 eager-loading is in place.

**Failure mode:** existing sync code that relied on lazy loading breaks. This is a GOOD failure — it surfaces the lazy load at conversion time, not in production.

**Rule:** after full conversion, every relationship in `models.py` should be `lazy="raise"` OR an explicit eager strategy (`"selectin"`, `"joined"`, `"immediate"`).

---

### Pattern 6 — `AsyncSession.refresh(instance, attribute_names=[...])` — manual post-insert refresh

**When to use:** after an INSERT, you need a DB-computed column (server_default) that wasn't populated on the instance.

```python
from datetime import datetime

async def create_media_buy(self, req):
    mb = MediaBuy(
        media_buy_id=generate_id(),
        tenant_id=self.tenant_id,
        status="pending",
        # created_at has server_default=func.now() — not set on instance
    )
    self.session.add(mb)
    await self.session.flush()  # Send INSERT, populate auto-PKs
    await self.session.refresh(mb, attribute_names=["created_at"])  # Reload specific columns
    return mb
```

`refresh` triggers a SELECT for only the specified columns. It does NOT trigger a lazy load on relationships (those need explicit reload).

**Prefer:** replace `server_default` with ORM-side `default` (Risk #5 Step 2 mitigation), avoiding the need for refresh entirely.

**Failure mode:** `refresh` after commit under `expire_on_commit=False` works fine. Under `expire_on_commit=True`, commit already triggered an auto-refresh, so manual refresh is redundant.

**Rule:** only use for columns with `server_default` or RETURNING values that aren't populated by `flush()`.

---

### Pattern 7 — `session.run_sync(lambda s: obj.relationship)` — escape hatch

**When to use:** you have legacy sync code that lazy-loads a relationship and you can't rewrite it right now. `run_sync` runs the lambda in a sync context on the same connection.

```python
# Legacy code path that accesses obj.related outside a sync context
value = await session.run_sync(lambda s: obj.related)
```

**Why it works:** `run_sync` dispatches the callable via SQLAlchemy's greenlet bridge, giving the sync code a greenlet context in which lazy loads succeed.

**Cost:** greenlet hop has overhead (~0.1ms per call). At scale, this is measurable.

**When to use:** bridging legacy code during migration. Mark with a FIXME comment and a ticket to rewrite properly.

```python
# FIXME(salesagent-XXXX): convert to selectinload in repository
value = await session.run_sync(lambda s: obj.legacy_relationship)
```

**Failure mode:** `run_sync` closures must be pickleable if using `run_in_executor`. For `AsyncSession.run_sync`, no pickling; closures work fine.

**Rule:** escape hatch only. Every usage gets a FIXME and a ticket.

---

### Pattern 8 — `expire_on_commit=False` — disable auto-expire globally

**When to use:** MANDATORY for async. Already in the checkpoint plan.

```python
SessionLocal = async_sessionmaker(
    _engine,
    class_=AsyncSession,
    expire_on_commit=False,  # <-- mandatory for async
)
```

**How it interacts with the patterns above:**

- **With `selectinload/joinedload` (Patterns 1-3):** relationships stay loaded after commit. Safe to access post-commit as long as session is still open.
- **With `lazy="raise"` (Pattern 5):** relationships that were NOT eagerly loaded raise on access, regardless of commit state.
- **With `refresh` (Pattern 6):** refresh works after commit because the session isn't closed. After session close, refresh raises.
- **With DB-computed columns:** columns aren't auto-refreshed. Use `default=` or explicit `refresh`.

**Failure mode:** engineer assumes post-commit refresh works (as in sync mode) → reads a stale attribute value. Mitigation: structural guard flagging any code that reads a `server_default` column post-commit without an explicit refresh call.

**Rule:** set once at session factory. Never override per-session.

---

### Pattern 9 — In-handler force load before return

**When to use:** the handler returns an ORM object to a template. The template accesses a relationship. The repository didn't eager-load it.

**Correct fix:** repository eager-loads (Pattern 1-3). **Anti-pattern:** force-load in handler via `session.run_sync`.

**If you MUST:** keep the session alive through template rendering.

```python
@router.get("/admin/tenant/{tid}/dashboard", name="admin_core_dashboard")
async def dashboard(tid: str, request: Request):
    async with TenantUoW(tid) as uow:
        tenant = await uow.tenants.get_with_full_config(tid)
        # Render WHILE session is still open — any lazy loads happen in-context
        return render(request, "dashboard.html", {"tenant": tenant})
    # <-- Session closed here. Post-return access would raise.
```

**Note:** Jinja template rendering in the `render()` wrapper must complete synchronously OR be `async`-capable (Jinja 3+ has `enable_async=True`). If the session closes before the template renders, lazy loads fail. The rule: **close session AFTER template renders**, not before.

**In the planned architecture:** `render()` converts the tenant to a dict or DTO inside the UoW block, so the template receives plain-old-data and no lazy loads are possible:

```python
async with TenantUoW(tid) as uow:
    tenant = await uow.tenants.get_with_full_config(tid)
    tenant_data = tenant.to_dict()  # Snapshot into plain dict
return render(request, "dashboard.html", {"tenant": tenant_data})
```

**Rule:** templates receive dicts/DTOs, not ORM objects. Handler serializes inside the UoW block. No ORM objects cross the UoW boundary.

---

### Summary cookbook table

| Pattern | Use case | Query count | Effort | Detection |
|---|---|---|---|---|
| 1. selectinload (collections) | parent.children | O(2) | Repository | Test eager-loads every relationship |
| 2. joinedload (FKs) | child.parent | O(1) | Repository | Test eager-loads every relationship |
| 3. Nested selectinload | parent.children.grandchildren | O(N+1) | Repository | Test + spot-check query count |
| 4. raiseload("*") | explicit minimal DTOs | O(1) | Repository | Raises on access |
| 5. lazy="raise" on model | blanket model-level guard | varies | One-time model edit | Structural guard |
| 6. refresh(attr_names=[...]) | server_default post-insert | +1 query | Repository | Regression test |
| 7. run_sync escape | legacy bridge | greenlet hop | FIXME comment | Grep for `run_sync` |
| 8. expire_on_commit=False | global async requirement | 0 | Session factory | N/A — config |
| 9. dict/DTO serialization | template boundary | 0 | Handler pattern | Structural guard: no ORM in templates |

---

## Section 4 — Pre-Wave-0 Spike Checklist

Goal: run these experiments BEFORE committing to absorbed-async v2.0 scope. Each has clear pass/fail criteria. Total time budget: 3 days on a spike branch.

### Spike 1 — Lazy-load audit (Risk #1) — **CRITICAL**

**What to do:**
1. Checkout branch `spike/async-lazy-load-audit`
2. Rewrite `src/core/database/database_session.py` to use `create_async_engine` + `async_sessionmaker(expire_on_commit=False)`
3. Add `postgresql+asyncpg://` URL rewriter
4. Add `asyncpg` to `pyproject.toml`
5. In `src/core/database/models.py`, set `lazy="raise"` on EVERY relationship (all 68)
6. Run `tox -e integration -- -x -v 2>&1 | tee spike-results.log`

**What "pass" looks like:**
- Fewer than 40 unique `InvalidRequestError: 'X.y' is not available due to lazy='raise'` failures across the test suite
- Each failure can be classified as a "missing selectinload in repository X" fix (cost: <30 min per fix)
- **Total estimated fix cost < 2 days**

**What "fail" means:**
- More than 100 unique lazy-load failures
- Failures in critical paths where the fix is non-obvious (cross-boundary lazy loads from MCP tool `_impl` into admin serialization logic)
- **Total estimated fix cost > 5 days**

**Fail consequence:** **ABANDON absorbed-async v2.0 scope.** Fall back to the sync def Option C plan. Schedule async SQLAlchemy for v2.1 as a separate migration.

---

### Spike 2 — Driver compat (Risk #2, #17, #18, #22)

**What to do:**
1. On spike branch, run: `tox -e unit` under `DATABASE_URL=postgresql+asyncpg://...`
2. Run: `tox -e integration` (as many tests as pass)
3. Log every `TypeError`, `asyncpg.exceptions.*`, `InterfaceError`
4. Count by category:
   - JSONB codec issues (Risk #17)
   - Event listener / statement_timeout issues (Risk #18)
   - Engine `json_serializer` issues (Risk #22)
   - Prepared statement cache issues (PgBouncer)
   - UUID / Interval / Array codec issues
5. Write minimal fixes for each category

**What "pass" looks like:**
- All integration tests pass OR fail only with lazy-load errors (not driver errors)
- JSONB roundtrip works via `JSONType(model=X)` for both typed and untyped columns
- `SHOW statement_timeout` returns the configured value
- Pydantic model `json_serializer` emits correct JSON for JSONB writes

**What "fail" means:**
- Multiple codec issues requiring >4 hours of manual fixes
- Statement timeout not applied (timeouts inherit from server default, which is 0 = unlimited)
- JSONType coercion broken for typed columns (Pydantic models not round-tripping)

**Fail consequence:** **Switch to `psycopg[binary,pool]>=3.2.0` fallback.** Rerun spike 2 with psycopg v3. If psycopg v3 passes, continue with it instead of asyncpg. Impact on rest of the plan: minimal (URL scheme changes from `+asyncpg` to `+psycopg`, no other code changes).

---

### Spike 3 — Performance baseline benchmark (Risk #10)

**What to do:**
1. On `main` branch (sync stack), run:
   ```bash
   pytest tests/performance/bench_admin_routes.py --benchmark-json=baseline-sync.json
   pytest tests/performance/bench_mcp_tools.py --benchmark-json=baseline-sync-mcp.json
   ```
2. On spike branch (async stack, with minimal lazy-load fixes from Spike 1), run:
   ```bash
   pytest tests/performance/bench_admin_routes.py --benchmark-json=spike-async.json
   pytest tests/performance/bench_mcp_tools.py --benchmark-json=spike-async-mcp.json
   ```
3. Compare: `pytest-benchmark compare baseline-sync.json spike-async.json`
4. Benchmark harness uses `locust` or `wrk` to simulate 10, 50, 100, 500 concurrent requests

**What "pass" looks like:**
- Low concurrency (1 req/s): async within 20% of sync latency
- Medium concurrency (50 req/s): async within 10% of sync latency
- High concurrency (200+ req/s): async outperforms sync by 20%+ (or sync saturates while async doesn't)

**What "fail" means:**
- Low concurrency: async >40% slower than sync (unacceptable for admin UI)
- Pool saturates at <100 concurrent
- Connection wait time spikes above 500ms at moderate load

**Fail consequence:** investigate root cause (usually query count explosion from N+1 lazy-load fixes that materialized as N eager queries). If unfixable, fall back to sync stack.

---

### Spike 4 — Test infrastructure conversion (Risk #3)

**What to do:**
1. Convert `tests/harness/_base.py` to async (~40 LOC rewrite)
2. Pick 5 representative test files, convert to `@pytest.mark.asyncio`
3. Run `tox -e integration -- tests/integration/test_<picked>.py -n 4` (xdist 4 workers)
4. Time the run; measure parallelism

**What "pass" looks like:**
- All 5 converted tests pass
- Xdist parallelism still works (each worker gets its own DB, no conflicts)
- Factory-boy factories work via the async wrapper
- No `RuntimeError: Event loop is closed`

**What "fail" means:**
- Xdist workers collide on event loop state
- Factory-boy can't bind sessions correctly
- Session fixture teardown hangs

**Fail consequence:** investigate; most issues are configurational. Unlikely to fail entirely — pytest-asyncio is well-supported.

---

### Spike 5 — MCP scheduler lifespan under async DB (Risk #7)

**What to do:**
1. On spike branch, convert `delivery_webhook_scheduler` and `media_buy_status_scheduler` tick bodies to async DB
2. Start the container, let it run for 5 minutes
3. Observe `docker logs` for:
   - `"delivery_webhook_scheduler alive"`
   - `"media_buy_status_scheduler alive"`
4. Trigger a webhook (e.g., create a media buy with a push notification config)
5. Verify webhook fires

**What "pass" looks like:**
- Both scheduler alive-tick log lines appear within 60 seconds of container start
- Webhook delivery succeeds
- No `CancelledError` or `InvalidRequestError` in logs

**What "fail" means:**
- Alive-tick log lines missing
- Scheduler crashes on first tick
- Webhook payload construction fails with `MissingGreenlet`

**Fail consequence:** Wave 4 scope increases (additional lazy-load fixes in scheduler paths). If scope creep is too large, scheduler stays sync via `run_in_threadpool` wrapper (Risk #7 fallback).

---

### Spike 6 — Alembic migration run (Risk #4)

**What to do:**
1. Rewrite `alembic/env.py` using the async template
2. Drop and recreate the test DB: `docker compose down postgres && docker compose up postgres`
3. Run: `alembic upgrade head`
4. Verify all tables exist
5. Run: `alembic downgrade -1` then `alembic upgrade +1`
6. Repeat with a migration that uses `op.execute(text("..."))`

**What "pass" looks like:**
- `alembic upgrade head` completes cleanly
- `alembic downgrade` + `upgrade` roundtrip works
- `op.execute` works

**What "fail" means:**
- `RuntimeError: asyncio.run() cannot be called from a running event loop` (environment issue)
- Migration scripts error out inside `run_sync` callback

**Fail consequence:** keep `alembic/env.py` sync (fallback in Risk #4 D). Minimal rework.

---

### Spike 7 — `expire_on_commit` server-default audit (Risk #5)

**What to do:**
1. Grep `src/core/database/models.py` for `server_default=`:
   ```bash
   grep -n "server_default" src/core/database/models.py
   ```
2. For each column, trace callers: `grep -rn "<column_name>" src/core/`
3. Identify call sites that read the column after commit but don't explicitly refresh
4. Count: how many rewrites needed?

**What "pass" looks like:**
- <10 `server_default=` columns
- All can be converted to `default=datetime.utcnow` or equivalent client-side default
- Zero callers rely on post-commit refresh

**What "fail" means:**
- >30 `server_default=` columns (many candidates to rewrite)
- Some columns (e.g., auto-generated UUIDs via `server_default=gen_random_uuid()`) cannot be trivially converted

**Fail consequence:** expand scope — add explicit `await session.refresh(...)` calls in every affected repository. +0.5-1 day of work. Not a scope-killer.

---

### Spike 8 — Go/no-go decision

After completing spikes 1-7 (budget: 3 days), convene a decision meeting. Use this checklist:

| Spike | Pass? | Blocker? |
|---|---|---|
| 1. Lazy-load audit | Pass if <40 failures, fixable in <2d | YES (hard blocker) |
| 2. Driver compat | Pass if <4h fix cost | Soft (can switch to psycopg3) |
| 3. Performance | Pass if async within 20% sync at low conc. | Soft |
| 4. Test infra | Pass if xdist + factories work | Soft |
| 5. MCP scheduler | Pass if alive-tick visible | Soft |
| 6. Alembic | Pass if upgrade/downgrade works | Soft (fallback exists) |
| 7. server_default | Pass if <30 columns to rewrite | Soft |

**Go condition:** Spike 1 PASSES and no more than 2 soft spikes fail. Proceed to Wave 4 planning.

**No-go condition:** Spike 1 FAILS (lazy-load scope too big) OR more than 2 soft spikes fail. Fall back to Option C sync def admin, defer async SQLAlchemy to v2.1.

---

## Section 5 — Risk Interaction Analysis (Compound Failures)

### Interaction A: Risk #1 (lazy load) + Risk #5 (expire_on_commit)

**Compound failure mode:** a handler fetches an object, commits, then the handler (or a template downstream) reads a column that was set via `server_default`. Under `expire_on_commit=True`, sync behavior auto-refreshes the column — developer never notices. Switch to async `expire_on_commit=False` (mandatory) and the column reads as `None` (or last-set client value, if any). Handler serializes the `None` into JSON, violates Pydantic schema, returns 500.

**Diagnostic difficulty:** moderate. The failure is not `MissingGreenlet` — it's `AttributeError: 'NoneType' object has no attribute 'isoformat'` or Pydantic validation error. Engineers look at the wrong thing.

**Compound mitigation:**
- Convert all `server_default=` columns to client-side `default=` (Risk #5 Step 2)
- Structural guard: reject any new `server_default=` in models.py without explicit refresh handling
- Include both columns AND relationships in the Wave 1 "async migration audit" — they're the same category of problem

### Interaction B: Risk #3 (pytest-asyncio) + Risk #6 (connection pool)

**Compound failure mode:** `pytest-xdist -n 4` runs 4 workers in parallel, each with its own event loop and session factory. Each worker opens a pool of 50 connections. 4 × 50 = 200 connections. Postgres `max_connections = 100`. **Test suite hangs waiting for connections.**

**Diagnostic difficulty:** high. Only shows up in parallel xdist runs. Sequential passes. Intermittent failures.

**Compound mitigation:**
- Set `pool_size=10, max_overflow=5` in test-mode (`tox -e integration` uses a reduced pool)
- Add `DATABASE_URL` detection: if `TEST_MODE=1`, use smaller pool
- Add a conftest fixture that asserts `pool_size * xdist_workers < max_connections * 0.8`

```python
# tests/conftest.py
@pytest.fixture(scope="session", autouse=True)
def _assert_test_pool_fits():
    pool_size = int(os.environ.get("DB_POOL_SIZE", "10"))
    xdist_workers = int(os.environ.get("PYTEST_XDIST_WORKER_COUNT", "1"))
    # Leave headroom for alembic, health checks, etc.
    assert pool_size * xdist_workers < 80, (
        f"Test pool {pool_size} × workers {xdist_workers} = "
        f"{pool_size * xdist_workers} > 80 (Postgres max_connections=100 - safety)"
    )
```

### Interaction C: Risk #7 (scheduler) + Risk #12 (ContextVar propagation)

**Compound failure mode:** `delivery_webhook_scheduler` fires a webhook from a scheduler tick. The webhook dispatch writes an audit log entry via the `audit_logger`. `audit_logger` reads the current user from `request.state.user` — but there's no request in a scheduler tick. Under sync mode, `flask.g.user` is also empty in scheduler context → audit log says "user: None". Under async mode with ContextVar-based audit logger, same thing, but now if a handler spawned a child task that runs in a scheduler context, the ContextVar inherited from the parent task is stale → audit log attributes the webhook to the WRONG user.

**Diagnostic difficulty:** very high. Subtle race condition. Only shows up in production.

**Compound mitigation:**
- Scheduler ticks use a dedicated "system" user identity, never rely on request-derived context
- `audit_logger` raises if `current_user` is not explicitly provided (no implicit context lookup)
- Structural guard: scheduler code must pass `user=SYSTEM_USER` explicitly to audit logger

```python
# src/core/audit_logger.py
async def log_action(action: str, user: str, tenant_id: str, details: dict):
    # REQUIRED user parameter — no ContextVar lookup
    ...

# Scheduler tick
async def _tick_once():
    async with get_db_session() as s:
        ...
        await log_action("scheduler_tick", user="system", tenant_id=tid, details=...)
```

### Interaction D: Risk #2 (driver) + Risk #17 (JSONType decoder)

**Compound failure mode:** the driver switch to asyncpg returns JSONB as `str`. `JSONType.process_result_value` (line 86 of `json_type.py`) raises `TypeError: Unexpected type in JSONB column: str. Expected dict or list from PostgreSQL JSONB. This may indicate a database schema issue.`

**Diagnostic difficulty:** low — the error message is explicit. But: the error is raised on EVERY query that loads a JSONB column, which is most tables. Every integration test fails with the same error. Hard to triage because 200 tests fail simultaneously with the same error.

**Compound mitigation:** (already in Risk #2 and Risk #17)
- Register JSONB codec on asyncpg connect (`set_type_codec`)
- Make `JSONType.process_result_value` tolerant of `str` input (parse if needed) — defensive driver-agnostic path
- Driver-compat test in Spike 2

### Interaction E: Risk #1 (lazy) + Risk #3 (pytest-asyncio) + test harness

**Compound failure mode:** test harness creates a `TestClient` for admin routes. Test calls `client.get("/admin/...")`. Handler runs, returns a Pydantic response. Test asserts on the response body. But inside the handler, a template rendered with `tenant.products` — which is a lazy relationship. Session closed before template rendered. `MissingGreenlet` raised. Response returns 500 with the error message.

Test sees 500 and says "handler broken" — but the actual root cause is the handler pattern (close session after template render, not before). This is Pattern 9 in the cookbook.

**Diagnostic difficulty:** moderate. Error message is clear (`MissingGreenlet`), but novice engineer fixes the symptom (add `selectinload`) without understanding the handler pattern is wrong.

**Compound mitigation:**
- Cookbook section 9 explains the pattern explicitly
- Structural guard: admin handlers must not return ORM objects to templates. Templates receive dicts/DTOs only.
- Integration test that exercises every admin route with `lazy="raise"` globally enabled — any lazy-load slips through the cookbook fails the test

```python
# tests/unit/test_architecture_templates_no_orm_objects.py
"""
Admin template contexts must not contain SQLAlchemy ORM objects.
Reason: lazy loads fire outside the session context, raising MissingGreenlet.

AST-scan every admin handler, find all `render(request, template, {context})`
calls, verify context dict values are not ORM instances or lists of ORM instances.
"""
```

### Interaction F: Risk #10 (performance) + Risk #6 (pool) + Risk #29 (SSE pin connections)

**Compound failure mode:** admin UI has SSE (server-sent events) for activity streams. Async handler for SSE holds the session open across the lifetime of the connection (60s+). Under 10 concurrent SSE clients, 10 connections are pinned. Remaining pool = `pool_size - 10 + overflow`. During an admin bulk-operation burst that needs 40 connections, pool saturates. Handler latency spikes to 30s (`pool_timeout`). User experiences "site down".

**Diagnostic difficulty:** high. Only shows at a specific traffic pattern. Grafana shows `checked_out` rising, but SSE sessions aren't counted the same as request sessions.

**Compound mitigation:**
- Rule (cookbook section): SSE/long-lived handlers MUST release session between ticks
- Pool-monitoring alert thresholds set conservatively
- Structural guard: grep for `async with get_db_session()` inside `while True:` / `async for ... in stream:` loops

---

## Section 6 — Newly-Discovered Risks (Updated Risk Register)

All risks below were surfaced during the analysis and are NOT in the checkpoint's 15. Each gets a full A/B/C/D breakdown.

### Risk #16 — `session.query()` stragglers

#### A. Root cause
Structural guards enforce `session.query()` is deprecated, but some files are allowlisted. Under async, `AsyncSession` has NO `.query()` method — every allowlisted query call raises `AttributeError`.

#### B. Detection
`AttributeError: 'AsyncSession' object has no attribute 'query'` — immediate failure on any call.

**Grep:** `grep -rn "session\.query\|\.query(" src/ tests/`

#### C. Mitigation
1. Grep the allowlist, produce exact file list
2. Convert each to `await session.execute(select(...))`
3. Delete allowlist entries after conversion
4. Existing structural guard (`test_architecture_no_raw_select.py`) stays — catches regressions

#### D. Fallback
N/A — conversion is mechanical.

---

### Risk #17 — `JSONType.process_result_value` assumes psycopg2 pre-decoding

See Interaction D above. Same mitigation.

#### A. Root cause
The comment at `src/core/database/json_type.py:91` is explicit: "PostgreSQL JSONB is already deserialized by psycopg2 driver". asyncpg does NOT pre-decode JSONB to `dict` by default. Result: every JSONB read raises `TypeError`.

#### B. Detection
Every integration test that loads a row with a JSONB column fails with:
```
TypeError: Unexpected type in JSONB column: str. Expected dict or list from PostgreSQL JSONB.
```

#### C. Mitigation
1. Register JSONB codec on every asyncpg connection (see Risk #2 Step 2)
2. Make `JSONType` driver-agnostic by parsing `str` input:
   ```python
   def process_result_value(self, value, dialect):
       if value is None:
           return None
       if isinstance(value, str):
           value = json.loads(value)
       if not isinstance(value, (dict, list)):
           raise TypeError(...)
       ...
   ```
3. Add test covering both `str` and `dict` inputs

#### D. Fallback
Use psycopg v3 (decodes JSONB to dict natively, matching psycopg2 behavior).

---

### Risk #18 — `@event.listens_for(_engine, "connect")` statement_timeout event

#### A. Root cause
`database_session.py:139-144` registers a sync event listener on the engine's `connect` event. The handler executes `cursor.execute("SET statement_timeout = ...")`. Under async engine, the event fires with `dbapi_conn` = `AsyncAdapt_asyncpg_connection` (SQLAlchemy's sync adaptor wrapping asyncpg). The `.cursor()` method on this wrapper is sync but internally schedules coroutines — using it from the event listener (sync context) can deadlock or raise `InterfaceError: cannot perform operation: another operation is in progress`.

**Result:** statement_timeout is not set, which means long queries run unbounded. A single runaway query hangs the connection indefinitely. Pool fills up. App dies.

#### B. Detection

**Pre-merge test:**
```python
@pytest.mark.requires_db
@pytest.mark.asyncio
async def test_statement_timeout_applied():
    async with get_db_session() as s:
        result = await s.execute(text("SHOW statement_timeout"))
        timeout = result.scalar()
        assert timeout != "0" and timeout != "0ms", \
            f"statement_timeout not applied — expected 30s, got {timeout}"
```

This test MUST be in the Wave 4 entry criteria.

**Production detection:** slow queries, pool saturation, no timeout errors.

#### C. Mitigation

Option A — Use `connect_args` + `server_settings`:
```python
_engine = create_async_engine(
    url,
    connect_args={
        "server_settings": {
            "statement_timeout": f"{query_timeout * 1000}",  # milliseconds
            "application_name": "salesagent",
        },
        "command_timeout": query_timeout,  # asyncpg client-side timeout
    },
)
```

`server_settings` pass through to `asyncpg.connect(server_settings=...)`, which sends them as startup parameters. Works for direct Postgres connections.

Option B — if PgBouncer is in use, startup parameters are stripped. Instead, use asyncpg's `init` callback:
```python
async def _init_conn(conn):
    await conn.execute("SET statement_timeout = 30000")

_engine = create_async_engine(
    url,
    connect_args={"init": _init_conn},  # asyncpg-specific
)
```

The SQLAlchemy dialect threads `init` through to `asyncpg.create_pool(init=...)`.

**Recommended:** Option A for non-PgBouncer environments, Option B for PgBouncer. Detect at runtime.

#### D. Fallback
If neither option works, set statement_timeout at the Postgres server level (config file). Less granular but reliable.

**AdCP compliance:** no direct impact, but unbounded queries can exhaust pool → request timeouts → AdCP callers see 503s. Indirect compliance risk.

---

### Risk #19 — `DatabaseManager` class sync `__enter__`/`__exit__`

> **RESOLVED 2026-04-11 by Decision 7 (ContextManager refactor):** `DatabaseManager` is **deleted entirely**. Only `ContextManager` subclassed it, and Decision 7's refactor flattens `ContextManager` into stateless module-level `async def` functions taking `session: AsyncSession` as the first parameter. With no subclasses left, `DatabaseManager` has no remaining callers and the entire base class disappears in Wave 4. The risk below is preserved for traceability but the mitigation column in Section 1 is now "delete (Decision 7)" instead of "convert to async". See `async-pivot-checkpoint.md` §3 "ContextManager refactor" for the full target state.

#### A. Root cause
`database_session.py:287-338` defines a `DatabaseManager` class with sync context manager methods. Any caller breaks under async:
```python
with DatabaseManager() as db:
    db.session.execute(...)
```

#### B. Detection
Grep: `grep -rn "DatabaseManager" src/ tests/`

#### C. Mitigation (Decision 7 resolution)
1. Grep for all callers — confirmed: only `ContextManager` subclasses `DatabaseManager` (no production callers use it directly)
2. Delete `class DatabaseManager` from `database_session.py` (~50 LOC)
3. Delete `class ContextManager(DatabaseManager)` from `context_manager.py`
4. Convert 12 `ContextManager` public methods to module-level `async def` functions taking `session: AsyncSession` as first parameter
5. Update 7 production callers to acquire their own session via `async with session_scope():` and pass it explicitly

#### D. Fallback
Hard-coded: deletion is the only resolution. If Decision 7 is reversed (Spike 4.5 fails), `DatabaseManager` gets `__aenter__`/`__aexit__` added and the original "convert to async" plan applies — but Decision 7's deep-think analysis confirmed the singleton-cached-session bug is unfixable without the refactor.

---

### Risk #20 — `get_or_create`, `get_or_404` helpers

Minor. Both functions take `session` as first arg, so conversion is mechanical: `async def get_or_404(session: AsyncSession, ...)` and `session.scalars(stmt).first()` → `(await session.execute(stmt)).scalars().first()`.

---

### Risk #21 — `execute_with_retry` helper (database_session.py:244)

#### A. Root cause
Sync retry wrapper that calls `get_db_session()` context manager. Under async, the entire helper must become async. Callers break.

#### B. Detection
Grep: `grep -rn "execute_with_retry" src/ tests/`

#### C. Mitigation
Convert to `async def execute_with_retry(func, ...)` where `func` is an async callable. All callers become async. Straightforward.

---

### Risk #22 — `json_serializer` engine hook may not fire for asyncpg

#### A. Root cause
`create_engine(..., json_serializer=_pydantic_json_serializer)` hooks the engine's JSON serialization. Under asyncpg dialect, SQLAlchemy passes the serializer to asyncpg's JSONB codec registration — IF the dialect honors it. The SQLAlchemy asyncpg dialect DOES support `json_serializer` / `json_deserializer` kwargs.

**The catch:** asyncpg's codec system is per-connection, so the serializer must be registered on EVERY connection. SQLAlchemy's asyncpg dialect registers the codec in its `on_connect` hook. Verify this experimentally — if it doesn't fire, Pydantic models don't round-trip.

#### B. Detection
**Pre-merge test:**
```python
@pytest.mark.requires_db
@pytest.mark.asyncio
async def test_pydantic_model_to_jsonb_roundtrip():
    tenant = TenantFactory.build(
        brand=BrandReference(name="Acme", website="https://acme.example")
    )
    async with get_db_session() as s:
        s.add(tenant)
        await s.commit()
        result = await s.execute(select(Tenant).where(Tenant.tenant_id == tenant.tenant_id))
        loaded = result.scalars().first()
        assert isinstance(loaded.brand, BrandReference)
        assert loaded.brand.name == "Acme"
```

#### C. Mitigation
1. Verify in spike 2 that `json_serializer=_pydantic_json_serializer` threads through
2. If it doesn't, register the codec manually in asyncpg connect hook (Risk #17 Option C approach)
3. Add the roundtrip test to permanent test suite

#### D. Fallback
Manual codec registration.

---

### Risk #23 — `check_database_health` global state race

#### A. Root cause
`database_session.py:391-427` — uses module-level `_is_healthy`, `_last_health_check` globals. Under async, two tasks can read-modify-write concurrently → race condition. Not a crash risk, but inconsistent circuit-breaker state.

#### B. Detection
Hard to detect. Only shows under concurrent load with DB errors.

#### C. Mitigation
Wrap in `asyncio.Lock`:
```python
_health_lock = asyncio.Lock()

async def check_database_health(force=False):
    async with _health_lock:
        # Existing logic
        ...
```

#### D. Fallback
N/A — race is benign (causes extra health checks, not crashes).

---

### Risk #24 — `get_pool_status()` attribute access on async pool

#### A. Root cause
`database_session.py:430-464` calls `pool.size()`, `pool.checkedin()`, etc. on `engine.pool`. Async engine's pool is `AsyncAdaptedQueuePool`, which exposes the same `.size()` / `.checkedin()` / `.checkedout()` / `.overflow()` methods. Should work, but verify.

#### B. Detection
Call `get_pool_status()` in a test and assert dict shape.

#### C. Mitigation
No code change expected. If there's an API mismatch, patch the method names.

---

### Risk #25 — `reset_engine()` sync `dispose()`

#### A. Root cause
`database_session.py:153-165` calls `_engine.dispose()` — sync method. Under async engine, `dispose()` is `async`. Sync call returns a coroutine (never awaited) — engine is NOT disposed. Pool leaks in tests.

#### B. Detection
Immediate — test fixture that calls `reset_engine()` fails with "coroutine was never awaited" warning, and subsequent tests find leaked connections.

#### C. Mitigation
Make `reset_engine()` async:
```python
async def reset_engine():
    global _engine, _session_factory, _scoped_session
    if _scoped_session is not None:
        _scoped_session.remove()  # if still exists in scoped wrapper
        _scoped_session = None
    if _engine is not None:
        await _engine.dispose()
        _engine = None
    _session_factory = None
```

All callers become `await reset_engine()`.

#### D. Fallback
N/A — immediate break.

---

### Risk #26 — FastMCP + FastAPI lifespan composition deadlock on shutdown

#### A. Root cause
`combine_lifespans(app_lifespan, mcp_app.lifespan)` yields through both. On SIGTERM, the outer lifespan cancels background tasks. If a scheduler is mid-`await session.commit()` and the commit hasn't returned, the cancel injects `CancelledError`. The connection may be in a "busy" state. Engine's `dispose()` waits for connections to return. If the connection never returns (async `dispose()` has a timeout), shutdown hangs for 10-30s.

#### B. Detection
Shutdown logs show `"waiting for X connections"` / `timed out waiting for connection`.

#### C. Mitigation
1. Scheduler tick uses `asyncio.shield(...)` around commit to complete the commit before cancel
2. Lifespan exit has a `timeout` — after N seconds, forcefully cancel all pending tasks
3. Disposal uses `engine.dispose(close=True)` to force connection close

```python
async def _tick_once():
    try:
        async with get_db_session() as session:
            ...
            await asyncio.shield(session.commit())  # Don't cancel mid-commit
    except asyncio.CancelledError:
        logger.info("Tick cancelled, exiting")
        raise
```

#### D. Fallback
Force kill on shutdown (SIGKILL after 30s). Fly.io's default graceful-shutdown is 30s; raising the timeout helps.

**AdCP compliance:** in-flight webhooks may be lost on shutdown. Document the invariant: "webhook dispatches are idempotent (via `webhook_id` dedup key), safe to retry on next tick."

---

### Risk #27 — Covered by Risk #1 + #5 (removed for brevity)

---

### Risk #28 — `audit_logger` module-level singleton

> **PARTIALLY RESOLVED 2026-04-11 by Decision 1 (Path B):** the simple "convert all callers to async" plan is **wrong** because adapter call paths are sync threads under Path B and cannot `await`. Resolution is a **split**: `AuditLogger.log_operation` becomes a thin async wrapper that delegates to `AuditLogger._log_operation_sync` via `run_in_threadpool`. Adapter code (running inside `run_in_threadpool`) calls `_log_operation_sync` directly using `get_sync_db_session()`. Async code (request handlers, schedulers, `_impl` functions) calls the public `await audit_logger.log_operation(...)` which forwards into the thread. Both paths write to the same `audit_logs` table; the only difference is which session factory they use. See `flask-to-fastapi-foundation-modules.md` §11.14 (E) for the complete code.

#### A. Root cause
`src/core/audit_logger.py` uses `get_db_session()` in sync mode. **Naive plan would convert all callers to async** — but adapter code runs inside `run_in_threadpool` (Decision 1 Path B) and **cannot `await` the audit logger**. Fanout via grep: `grep -rn "audit_logger\|AuditLogger\|log_action" src/`.

#### B. Detection
After conversion, every sync caller breaks with `coroutine was never awaited` warning and `TypeError`. Plus: any adapter call site inside `run_in_threadpool` that tries to `await audit_logger.log_operation(...)` raises `RuntimeError: cannot use 'await' in a non-async function`.

#### C. Mitigation (Decision 1 Path B resolution)
Split into sync internal + async public wrapper:

```python
# src/core/audit_logger.py — AFTER (Decision 1 Path B)

class AuditLogger:
    def _log_operation_sync(
        self,
        principal_name: str,
        operation: str,
        details: dict,
        success: bool = True,
    ) -> None:
        """Internal sync implementation. Used by adapter code running in run_in_threadpool.
        Uses get_sync_db_session() from the dual session factory."""
        from src.core.database.database_session import get_sync_db_session
        with get_sync_db_session() as session:
            session.add(AuditLog(
                principal_name=principal_name,
                operation=operation,
                details=details,
                success=success,
                timestamp=datetime.utcnow(),
            ))
            session.commit()

    async def log_operation(
        self,
        principal_name: str,
        operation: str,
        details: dict,
        success: bool = True,
    ) -> None:
        """Async public wrapper. Used by request handlers, schedulers, _impl functions."""
        from starlette.concurrency import run_in_threadpool
        await run_in_threadpool(
            self._log_operation_sync,
            principal_name=principal_name,
            operation=operation,
            details=details,
            success=success,
        )
```

**Adapter code calls `_log_operation_sync` directly** (no threadpool wrap — already on a worker thread). **Async code calls `await log_operation(...)`** which forwards into the threadpool. Both paths write to the same `audit_logs` table via different session factories.

#### D. Fallback
N/A — the split is the only resolution that supports both sync (Path B adapter) and async (request handler) callers without duplicating the `INSERT INTO audit_logs` SQL across two implementations.

This also aligns with the repository pattern: audit is a repository method, not a free function.

---

### Risk #29 — SSE / long-lived handler pins connections

See Interaction F above.

---

### Risk #30 — N/A (removed — same as #17)

---

### Risk #31 — `test_architecture_no_raw_select.py` allowlist interactions

Existing allowlist contains historical raw `select()` call sites. Conversion to async doesn't affect the allowlist directly, but each entry needs conversion to `await session.execute(select(...))`. Pre-existing allowlist entries become conversion targets during Wave 4.

**Mitigation:** before Wave 4, freeze the allowlist. During Wave 4, convert entries one-by-one and remove from allowlist. By Wave 5, allowlist is empty.

---

### Risk #32 — `asyncio.get_event_loop()` deprecation

#### A. Root cause
Python 3.12+ deprecates `asyncio.get_event_loop()` without a running loop. In 3.14 it becomes `DeprecationWarning` → `RuntimeError`. Test fixtures that call it break.

#### B. Detection
Pytest warnings `DeprecationWarning: There is no current event loop`.

#### C. Mitigation
Replace with `asyncio.new_event_loop()` or use `pytest-asyncio`'s loop fixture.

#### D. Fallback
N/A.

---

### Risk #33 — Module-import-time engine creation

#### A. Root cause
`database_session.py:70-150` uses lazy init — `_engine` is `None` until `get_engine()` is first called. Under async, the engine must be created inside an event loop (asyncpg binds the loop). If a module imports `database_session` and calls `get_engine()` at module load time (before `asyncio.run(...)` starts), asyncpg's loop binding fails.

**Grep:** `grep -rn "^from src.core.database.database_session import.*get_engine" src/` — any hit at top-level is a risk.

#### B. Detection
`RuntimeError: There is no current event loop` on module import.

#### C. Mitigation
1. Ensure engine creation happens inside an async context
2. Use `@asynccontextmanager` lifespan to create engine in `app.state.engine`
3. No module-level `get_engine()` calls

```python
# src/app.py lifespan
@asynccontextmanager
async def app_lifespan(app: FastAPI):
    app.state.engine = create_async_engine(...)
    yield
    await app.state.engine.dispose()
```

Update `get_db_session()` to read `app.state.engine` (or a module-level singleton set lazily from the lifespan).

#### D. Fallback
Create engine at first `get_db_session()` call (current pattern), but ensure that call is inside an async function, never at module load.

---

## Section 7 — Recommendations

### MUST do before Wave 0 (hard gate)

1. **Run the lazy-load spike (Spike 1).** 3 days max. Scope-killer: if >40 lazy-load failures, abandon absorbed-async and revert to Option C sync def admin plan. THIS IS THE CRITICAL DECISION GATE.

2. **Run the driver-compat spike (Spike 2).** 1 day. If asyncpg has >4 hours of fixes, pivot to `psycopg[binary,pool]>=3.2.0` as the driver. Driver decision locked before Wave 0.

3. **Baseline performance benchmark.** Sync latency on 20 admin routes + 5 MCP tool calls. Stored as `tests/performance/baseline-sync.json`. Wave 4 compares against this.

4. **Audit `server_default` columns.** Produce a list of columns requiring post-commit refresh or migration to `default=`. If >30 columns, plan scope is larger.

5. **Audit `session.query()` and raw `select()` allowlist entries.** Count + categorize. This is the Wave 4 conversion inventory.

6. **Verify `test_architecture_no_default_lazy_relationship.py` guard** (new) compiles and captures the baseline of 68 relationships. Baseline is the allowlist; Wave 1+ ratchets down.

7. **Write the async debugging runbook** (`docs/development/async-debugging.md`). Engineers need it before they touch async code.

8. **Decide driver + confirm the AdCP protocol boundary unchanged.** Wave 0 entry criterion.

### SHOULD do during Wave 0 (same wave as foundation modules)

9. **Add `test_architecture_no_default_lazy_relationship.py` structural guard.** All 68 relationships baselined.

10. **Add driver-compat test env (`tox -e driver-compat`).** Runs a slim integration test against asyncpg. Catches regressions between waves.

11. **Add `test_expire_on_commit_fields_populated.py`** — regression test for `server_default` column fix.

12. **Add `test_statement_timeout_applied.py`** — prevents Risk #18.

13. **Add `test_pydantic_model_to_jsonb_roundtrip.py`** — prevents Risk #22.

14. **Rewrite `JSONType.process_result_value` to be driver-agnostic** (tolerant of `str` input). 30 minutes.

15. **Rewrite `alembic/env.py` to async** or stick with sync env (fallback decision). 0.5-1 day.

16. **Convert `database_session.py` to async.** `create_async_engine`, `async_sessionmaker`, `expire_on_commit=False`. Do this early in Wave 4 (foundation of everything else).

17. **Build the async Test Harness.** Convert `tests/harness/_base.py` to async. Convert `tests/factories/_async.py` for factory-boy async wrapper. Convert 5 representative test files end-to-end as proof of concept.

18. **Set `lazy="raise"` on all 68 relationships in `models.py`.** AFTER Wave 4 repository conversion is in place. Final enforcement.

19. **Add `test_architecture_templates_no_orm_objects.py`** — enforces the handler → dict/DTO → template pattern.

### CAN defer to Wave 4 (async implementation) or Wave 5 (release polish)

20. **Performance benchmarking + tuning.** Bench against `baseline-sync.json`. Tune `pool_size`.

21. **Convert scheduler ticks to async DB.** Wave 4 (Risk #7). Add alive-tick log (deep-audit §3.7).

22. **Convert admin handlers to `async def` with async UoW.** Wave 4 core work.

23. **Convert `_impl` functions to `async def`** (those not already async).

24. **Docs:** update `CLAUDE.md` to reflect async-first patterns, update the migration rubric.

25. **Remove psycopg2 from `pyproject.toml`.** Only after Wave 4 ships to main and is stable for ~1 release.

26. **Remove `scoped_session`-related code** from `database_session.py`.

27. **PgBouncer evaluation.** Defer to v2.1 unless Spike 2 reveals an immediate need.

28. **Consolidate structural guards into one file** (deep-audit O3). Wave 5 polish.

29. **Update CI JSON test reports to distinguish "async" failures from others.** Wave 5 observability polish.

30. **Document all 9 cookbook patterns in `docs/development/async-cookbook.md`.** Wave 5.

---

## Closing Notes — Go/No-Go Summary

**The single most important decision is the lazy-load spike (Spike 1).** If it passes, everything else is manageable incremental work. If it fails, the team must revert to Option C sync def admin, defer async SQLAlchemy to v2.1, and accept that `api_v1.py`'s latent bug remains latent (documented) for another release cycle.

**AdCP safety re-verification:** all mitigation patterns in this report have been cross-checked against `flask-to-fastapi-adcp-safety.md`. The only potential AdCP impact is **Risk #5 Interaction with server_default datetime fields** — if a non-nullable `datetime` field in an AdCP response body becomes `None` because of `expire_on_commit=False` behavior change, Pydantic validation fails and AdCP clients see 500s. Mitigation: move defaults to client-side via `default=datetime.utcnow`. Audit required before Wave 4 entry.

**Driver recommendation: prepare for either asyncpg or psycopg v3.** The spike decides. Psycopg v3 has fewer codec gotchas (matches psycopg2 defaults) but is slightly slower. Asyncpg is faster but has more driver-specific behavior. Either is AdCP-safe.

**Pre-Wave-4 points of no return:**
- Spike 1 fail → revert scope (no async)
- Spike 2 fail → switch driver (no scope change, just driver)
- Spikes 3-7 fail → scope creep, acceptable

**Post-Wave-4 points of no return:**
- Wave 4 merged to main → rollback is 3-5 days + risk
- Wave 5 merged to main → rollback requires point release

**Total new risks discovered: 18** (Risks #16-33 in Section 6). Most are low-to-medium severity with clear mitigations. The ones the team should pay closest attention to:
- **#17** (JSONType codec) — high severity, breaks every integration test if not addressed
- **#18** (statement_timeout event listener) — high severity, silent failure in prod
- **#22** (json_serializer codec) — high severity, similar to #17
- **#26** (FastMCP lifespan shutdown deadlock) — medium severity, hard to diagnose
- **#33** (module-import engine creation) — medium severity, easy to introduce accidentally

**Final recommendation:** proceed with the pre-Wave-0 spike sequence. Budget 3 days. If Spike 1 (lazy-load) passes, commit to the absorbed-async v2.0 scope. If it fails, the fallback plan is clean and well-understood.

**End of report.**
agentId: a6e114b25df0e2aba (use SendMessage with to: 'a6e114b25df0e2aba' to continue this agent)
<usage>total_tokens: 147605
tool_uses: 16
duration_ms: 784444</usage>
