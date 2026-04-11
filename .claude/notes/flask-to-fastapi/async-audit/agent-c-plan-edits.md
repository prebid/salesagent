Now I have complete visibility. I'm ready to produce the report. Given the size, I will be thorough and systematic.

---

# Plan-File Update Proposal: Flask→FastAPI Blocker #4 Pivot (sync def → full async SQLAlchemy)

**Date:** 2026-04-11
**Scope:** 8 plan files under `/Users/quantum/Documents/ComputedChaos/salesagent/.claude/notes/flask-to-fastapi/`
**Source of truth:** `async-pivot-checkpoint.md` §§1-9
**Action:** propagate Option A (full async SQLAlchemy in v2.0) across plan files

Three scoping notes for the applier before diving in:

1. **CLAUDE.md (`.claude/notes/flask-to-fastapi/CLAUDE.md`) and `implementation-checklist.md` already carry pivot markers at the top.** My edits to those files leave the markers intact and rewrite the downstream stale content that the markers currently forward-reference as "pending propagation."
2. **Current-state / "the bug we're fixing" prose references to `scoped_session`, `threading.get_ident`, `psycopg2`, `async def + sync scoped_session interleaving` SHOULD BE PRESERVED** in the "what we're fixing" sections. They describe the pre-migration state and become motivation for the full-async pivot. I only rewrite mentions that appear in target-state prose.
3. **Deep-audit §1.4 (Blocker 4) requires a full-section rewrite** (Part A / file 3), not per-line substitutions. I use approach (a): a single large old/new_string. Details in the edit itself.

---

## Part A — Per-file edit lists

### File 1: `/Users/quantum/Documents/ComputedChaos/salesagent/.claude/notes/flask-to-fastapi/flask-to-fastapi-migration.md`

**Total edits: 18**

---

#### Edit 1.1 — §1 Executive Summary (line ~51): "unlocks v2.1 async" is stale

```
File: .claude/notes/flask-to-fastapi/flask-to-fastapi-migration.md
Line: 51
```

**old_string:**
```
- Unlocks future async SQLAlchemy migration (v2.1)
```

**new_string:**
```
- Eliminates the scoped_session interleaving latent bug in `src/routes/api_v1.py` as a side effect of the full-async conversion (see async-pivot-checkpoint.md §4 Risk #15)
```

*Rationale:* Async SQLAlchemy is absorbed into v2.0, not deferred; the actual benefit the executive summary should highlight is the latent-bug fix.

---

#### Edit 1.2 — §1 Executive Summary (line ~54): Wave count is now 5-6, not 4

```
File: .claude/notes/flask-to-fastapi/flask-to-fastapi-migration.md
Line: 54
```

**old_string:**
```
**Migration strategy:** 4 waves (Foundation+codemod → Foundational routers+session cutover → Bulk blueprint port → SSE+cleanup). Flask catch-all stays live through Wave 2 as a safety net.
```

**new_string:**
```
**Migration strategy:** 5-6 waves (Foundation+codemod → Foundational routers+session cutover → Bulk blueprint port → SSE+cleanup → Async DB layer → Async cutover & release). Flask catch-all stays live through Wave 2 as a safety net. A mandatory pre-Wave-0 lazy-loading audit spike (see async-pivot-checkpoint.md §4 Risk #1) gates the Wave 4-5 scope. See `async-pivot-checkpoint.md` for full detail on the full-async absorption.
```

*Rationale:* Aligns headline wave count with the async pivot's expanded scope.

---

#### Edit 1.3 — §2 directive #6 (line ~65): "async SQLAlchemy deferred to v2.1" reverses

```
File: .claude/notes/flask-to-fastapi/flask-to-fastapi-migration.md
Line: 65
```

**old_string:**
```
6. **Async SQLAlchemy deferred to v2.1** (separate follow-on PR that builds on v2.0). v2.0 admin handlers wrap sync UoW in `run_in_threadpool`. See §18.
```

**new_string:**
```
6. **Full async SQLAlchemy absorbed into v2.0** (Option A from deep audit §1.4; pivoted 2026-04-11). v2.0 admin handlers are `async def` end-to-end with `AsyncSession` + `async_sessionmaker`; driver moves from `psycopg2-binary` to `asyncpg`. `run_in_threadpool` is reserved for genuinely blocking non-DB work (file I/O, CPU-bound calls, sync third-party libraries). See `async-pivot-checkpoint.md` (new target state in §3) and §18 (v2.0 Wave 4-5 execution).
```

*Rationale:* Primary directive change — async SQLAlchemy moves from v2.1 to v2.0.

---

#### Edit 1.4 — §2.8 Blocker 4 paragraph (line ~122)

```
File: .claude/notes/flask-to-fastapi/flask-to-fastapi-migration.md
Line: 122
```

**old_string:**
```
4. 🚨 **Session scoping on the async event-loop thread.** `src/core/database/database_session.py:148` uses `scoped_session` with default `threading.get_ident()` scopefunc. Under Flask+a2wsgi, each request runs on its own worker thread → isolated sessions. Under the plan's `async def` admin handlers, concurrent requests share the event-loop thread → **the same `scoped_session` identity** → transaction interleaving, stale reads, duplicate commits. **Fix:** flip the plan's default from `async def` to **`def` (sync)** for admin handlers. FastAPI auto-offloads sync handlers to a threadpool; each worker thread has its own session identity. Keep `async def` ONLY for OAuth callbacks (await Authlib), SSE handlers (async generators), and outbound webhook senders (await httpx). Add structural guard `test_architecture_admin_sync_db_no_async.py` — AST-scans admin routers, flags `async def` handlers calling `get_db_session()` without `run_in_threadpool`.
```

**new_string:**
```
4. 🚨 **Session scoping on the async event-loop thread — PIVOTED 2026-04-11 to full async SQLAlchemy.** `src/core/database/database_session.py:148` uses `scoped_session` with default `threading.get_ident()` scopefunc. Under Flask+a2wsgi, each request runs on its own worker thread → isolated sessions. Under `async def` handlers sharing the event loop thread, concurrent requests share the same `scoped_session` identity → transaction interleaving, stale reads, duplicate commits. **Original fix (Option C):** default admin handlers to sync `def` so FastAPI auto-offloads them to distinct threadpool workers. **Pivoted fix (Option A, chosen):** replace `scoped_session(sessionmaker(...))` with `async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)`, switch the driver `psycopg2-binary` → `asyncpg`, and convert admin handlers, repositories, UoW, `_impl` functions, alembic env, and the test harness to async. The scoped_session race is eliminated entirely because there is no more thread-identity scoping to race on. This also fixes a pre-existing latent bug where the REST routes in `src/routes/api_v1.py` already share `scoped_session` identity across async tasks. Add structural guard `test_architecture_admin_routes_async.py` — AST-scans admin routers and asserts every `@router.<method>(...)` handler is `async def`. See `async-pivot-checkpoint.md` for the full new plan including pre-Wave-0 lazy-loading audit spike (Risk #1).
```

*Rationale:* Central Blocker #4 text — must reverse from Option C to Option A.

---

#### Edit 1.5 — §2.8 "Plan defaults that change" bullet 1 (line ~140)

```
File: .claude/notes/flask-to-fastapi/flask-to-fastapi-migration.md
Line: 140
```

**old_string:**
```
- **Admin handler default flips to `def` (sync)** — was `async def` in the original plan. Async is reserved for handlers that `await` external I/O (OAuth, SSE, httpx).
```

**new_string:**
```
- **Admin handlers are `async def` end-to-end with full async SQLAlchemy** (pivoted 2026-04-11 from the original sync-def flip). Every handler uses `async with get_db_session() as session:` and `await session.execute(...)` via repositories. `run_in_threadpool` is reserved for file I/O, CPU-bound, or sync-third-party operations only — never for DB access. See `async-pivot-checkpoint.md` §3 for target-state code.
```

*Rationale:* Direct reversal of "flip to sync def" prescription.

---

#### Edit 1.6 — §2.8 additional guards list (line ~155)

```
File: .claude/notes/flask-to-fastapi/flask-to-fastapi-migration.md
Line: 155
```

**old_string:**
```
6. **`test_architecture_admin_sync_db_no_async.py`** — NEW from deep audit (blocker 4)
```

**new_string:**
```
6. **`test_architecture_admin_routes_async.py`** — NEW from deep audit (blocker 4, pivoted 2026-04-11). AST-scans `src/admin/routers/*.py` and asserts every `@router.<method>(...)` handler is `async def`. This replaces the original `test_architecture_admin_sync_db_no_async.py` proposal (wrong direction under the full-async pivot). Sibling guard `test_architecture_admin_async_db_access.py` asserts DB access uses `async with get_db_session()` / `await session.execute(...)`, not sync `with` or raw threadpool wrappers.
```

*Rationale:* The sync-def guard was the wrong direction; the correct guard asserts handlers are async.

---

#### Edit 1.7 — §6.1 current versions (line ~637): "deferring async to v2.1"

```
File: .claude/notes/flask-to-fastapi/flask-to-fastapi-migration.md
Line: 637
```

**old_string:**
```
- **SQLAlchemy** 2.0.36+ — async engine fully mature; deferring to v2.1
```

**new_string:**
```
- **SQLAlchemy** 2.0.36+ — async engine fully mature; absorbed into v2.0 (pivoted 2026-04-11)
- **asyncpg** 0.30.0+ — replaces `psycopg2-binary` as the Postgres driver; SQLAlchemy async engine's expected driver
```

*Rationale:* Deferral prose reverses; new driver dependency added.

---

#### Edit 1.8 — §13.1 `list_accounts` sync def example (line ~1684-1715)

```
File: .claude/notes/flask-to-fastapi/flask-to-fastapi-migration.md
Lines: ~1684-1715
```

**old_string:**
```
# CORRECTED per deep audit blockers #2, #4:
#   redirect_slashes=True → matches Flask permissive default (111 url_for calls)
#   include_in_schema=False → keeps /openapi.json equal to AdCP REST surface
router = APIRouter(tags=["admin-accounts"], redirect_slashes=True, include_in_schema=False)

_STATUSES = ["active", "pending_approval", "rejected", "payment_required", "suspended", "closed"]

@router.get(
    "/tenant/{tenant_id}/accounts",
    name="admin_accounts_list_accounts",  # ← admin_<blueprint>_<endpoint> greenfield convention
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
```

**new_string:**
```
# CORRECTED per deep audit blockers #2, #4:
#   redirect_slashes=True → matches Flask permissive default (111 url_for calls)
#   include_in_schema=False → keeps /openapi.json equal to AdCP REST surface
router = APIRouter(tags=["admin-accounts"], redirect_slashes=True, include_in_schema=False)

_STATUSES = ["active", "pending_approval", "rejected", "payment_required", "suspended", "closed"]

@router.get(
    "/tenant/{tenant_id}/accounts",
    name="admin_accounts_list_accounts",  # ← admin_<blueprint>_<endpoint> greenfield convention
    response_class=HTMLResponse,
)
async def list_accounts(              # ← async def end-to-end with full async SQLAlchemy (pivoted 2026-04-11)
    tenant_id: str,
    request: Request,
    tenant: CurrentTenantDep,
    status: Annotated[str | None, Query()] = None,
) -> HTMLResponse:
    """Admin handlers are async def end-to-end. `AccountUoW` is an async context
    manager backed by `AsyncSession` / `async_sessionmaker`, so there is no
    `scoped_session` thread-identity race to worry about.

    `run_in_threadpool` is reserved for genuinely blocking non-DB operations
    (file I/O, CPU-bound, sync third-party libs). See `async-pivot-checkpoint.md` §3.
    """
    async with AccountUoW(tenant_id) as uow:
        accounts = await uow.accounts.list_all(status=status)
    return render(request, "accounts_list.html", {
        "tenant_id": tenant_id, "tenant": tenant, "accounts": accounts,
        "status_filter": status, "statuses": _STATUSES,
    })
```

**Changes labeled:** verb-explicit decorator, `name=` for `url_for`, auth via `CurrentTenantDep`, declarative `Query()`, **`async def` handler with full async SQLAlchemy** (pivoted 2026-04-11 — replaces original sync-def-for-scoped_session approach), `async with AccountUoW(...)` context manager, `await uow.accounts.list_all(...)`, `render()` wrapper, explicit return type, `redirect_slashes=True` + `include_in_schema=False` on the router. The scoped_session interleaving bug is eliminated because `AsyncSession` does not use thread-identity scoping.
```

*Rationale:* Primary worked example — demonstrates the new async-with-full-async-DB pattern.

---

#### Edit 1.9 — §13.2 `create_account_form` sync def (line ~1727)

```
File: .claude/notes/flask-to-fastapi/flask-to-fastapi-migration.md
Line: 1727
```

**old_string:**
```
@router.get(
    "/tenant/{tenant_id}/accounts/create",
    name="admin_accounts_create_account_form",
    response_class=HTMLResponse,
)
def create_account_form(  # sync def per blocker #4
    tenant_id: str, request: Request, tenant: CurrentTenantDep,
) -> HTMLResponse:
    return render(request, "create_account.html", {"tenant_id": tenant_id, "edit_mode": False})
```

**new_string:**
```
@router.get(
    "/tenant/{tenant_id}/accounts/create",
    name="admin_accounts_create_account_form",
    response_class=HTMLResponse,
)
async def create_account_form(  # async def end-to-end (pivoted 2026-04-11)
    tenant_id: str, request: Request, tenant: CurrentTenantDep,
) -> HTMLResponse:
    return render(request, "create_account.html", {"tenant_id": tenant_id, "edit_mode": False})
```

*Rationale:* Direct sync→async flip on the GET handler.

---

#### Edit 1.10 — §13.2 `create_account` sync def + DB write (line ~1737-1765)

```
File: .claude/notes/flask-to-fastapi/flask-to-fastapi-migration.md
Lines: ~1737-1765
```

**old_string:**
```
@router.post(
    "/tenant/{tenant_id}/accounts/create",
    name="admin_accounts_create_account",
    dependencies=[Depends(audit_action("create_account"))],
)
def create_account(  # sync def — writes DB via AccountUoW
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
            str(request.url_for("admin_accounts_create_account_form", tenant_id=tenant_id)),
            status_code=303,
        )
    with AccountUoW(tenant_id) as uow:
        uow.accounts.create(
            name=name.strip(), brand_domain=brand_domain.strip(),
            operator=operator.strip(), billing=billing.strip(),
            payment_terms=payment_terms.strip(), sandbox=(sandbox == "on"),
            brand_id=brand_id.strip(),
        )
    flash(request, f"Account '{name}' created successfully.", "success")
```

**new_string:**
```
@router.post(
    "/tenant/{tenant_id}/accounts/create",
    name="admin_accounts_create_account",
    dependencies=[Depends(audit_action("create_account"))],
)
async def create_account(  # async def — writes DB via async AccountUoW (pivoted 2026-04-11)
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
            str(request.url_for("admin_accounts_create_account_form", tenant_id=tenant_id)),
            status_code=303,
        )
    async with AccountUoW(tenant_id) as uow:
        await uow.accounts.create(
            name=name.strip(), brand_domain=brand_domain.strip(),
            operator=operator.strip(), billing=billing.strip(),
            payment_terms=payment_terms.strip(), sandbox=(sandbox == "on"),
            brand_id=brand_id.strip(),
        )
    flash(request, f"Account '{name}' created successfully.", "success")
```

*Rationale:* POST handler flips to async with `async with` UoW + `await` repository call.

---

#### Edit 1.11 — §13.3 `change_status` internals — DB work inside `_update()` nested sync function must be rewritten

```
File: .claude/notes/flask-to-fastapi/flask-to-fastapi-migration.md
Lines: ~1790-1808
```

**old_string:**
```
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

**new_string:**
```
async def change_status(
    tenant_id: str, account_id: str, payload: StatusChangeRequest,
    tenant: CurrentTenantDep, request: Request,
) -> StatusChangeResponse:
    # CSRF validation happens in CSRFMiddleware (applied globally).
    # Direct async DB work — no run_in_threadpool wrapper under the full-async
    # pivot (2026-04-11). AccountUoW is an async context manager backed by
    # AsyncSession.
    async with AccountUoW(tenant_id) as uow:
        account = await uow.accounts.get_by_id(account_id)
        if account is None:
            raise HTTPException(status_code=404, detail="Account not found.")
        allowed = _STATUS_TRANSITIONS.get(account.status, set())
        if payload.status not in allowed:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot transition from '{account.status}' to '{payload.status}'.",
            )
        await uow.accounts.update_status(account_id, payload.status)
        return StatusChangeResponse(success=True, status=payload.status)
```

*Rationale:* Checkpoint §8 says 13.3 `change_status` outer async signature is already correct, but the internal `_update()` nested sync helper + `run_in_threadpool` wrap is stale under the pivot. The body should go directly into the async function with `async with` / `await`.

---

#### Edit 1.12 — §15 dependency changes — add asyncpg, remove psycopg2

```
File: .claude/notes/flask-to-fastapi/flask-to-fastapi-migration.md
Lines: ~1906-1929
```

**old_string:**
```
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
```

**new_string:**
```
**REMOVED:**
- `flask>=3.1.3`
- `flask-caching>=2.3.0` (zero callers)
- `flask-socketio>=5.5.1` (declared but unused)
- `python-socketio>=5.13.0` (transitive of flask-socketio)
- `simple-websocket>=1.1.0` (transitive of flask-socketio)
- `waitress>=3.0.0`
- `a2wsgi>=1.10.0`
- `types-waitress` (dev)
- `psycopg2-binary>=2.9.9` — replaced by `asyncpg` under the full-async pivot (2026-04-11)
- `types-psycopg2>=2.9.21.20251012` (dev) — no longer needed after driver swap

**ADDED:**
- `sse-starlette>=2.2.0` (promoted from transitive)
- `pydantic-settings>=2.7.0` (typed config)
- `itsdangerous>=2.2.0` (explicit pin; Starlette transitive; now also used by roll-your-own CSRF)
- `asyncpg>=0.30.0` — async Postgres driver (full-async pivot, 2026-04-11)
- `pytest-asyncio>=0.25.0` (dev) OR equivalent anyio config — required for async test harness

**UPDATED:**
- `sqlalchemy>=2.0.36` — now with `asyncio` extra pulled in explicitly for `create_async_engine`, `async_sessionmaker`, `AsyncSession`
```

*Rationale:* Driver swap is a load-bearing dependency change under the async pivot. `sqlalchemy[asyncio]` is not really a new pin but should be called out.

---

#### Edit 1.13 — §16 assumption #3 (line ~1948): sync SQLAlchemy + run_in_threadpool <5ms is now stale

```
File: .claude/notes/flask-to-fastapi/flask-to-fastapi-migration.md
Line: 1948
```

**old_string:**
```
3. **Sync SQLAlchemy stays sync;** UoW wrapped in `run_in_threadpool`. Benchmark <5ms overhead per request.
```

**new_string:**
```
3. **Full async SQLAlchemy in v2.0** (pivoted 2026-04-11). `create_async_engine` + `async_sessionmaker` + `AsyncSession`. Pre-Wave-0 lazy-loading audit spike (see `async-pivot-checkpoint.md` Risk #1) gates the absorption. Benchmark async vs. the pre-migration sync baseline to quantify the latency profile change; acceptable range is net-neutral to ~5% improvement under moderate concurrency.
```

*Rationale:* The whole assumption text reverses; the benchmark target also changes.

---

#### Edit 1.14 — §16 assumption #4 (line ~1949)

```
File: .claude/notes/flask-to-fastapi/flask-to-fastapi-migration.md
Line: 1949
```

**old_string:**
```
4. **Admin handlers `async def` + `run_in_threadpool`.** Structural guard against raw UoW in async scope.
```

**new_string:**
```
4. **Admin handlers `async def` + async SQLAlchemy end-to-end.** Structural guard `test_architecture_admin_routes_async.py` asserts every handler is `async def`; sibling guard asserts DB access uses `async with get_db_session()` / `await session.execute(...)`. `run_in_threadpool` remains valid for non-DB blocking operations only and is never used for DB access.
```

*Rationale:* Reverses the threadpool-for-DB assumption.

---

#### Edit 1.15 — §17 debatable surface #2 (line ~1986)

```
File: .claude/notes/flask-to-fastapi/flask-to-fastapi-migration.md
Line: 1986
```

**old_string:**
```
2. **Sync vs async SQLAlchemy** — async unlocks `async with` UoW. Counter: touches 100+ files, triples scope. **Chosen: sync + `run_in_threadpool`, async deferred to v2.1.**
```

**new_string:**
```
2. **Sync vs async SQLAlchemy** — async unlocks `async with` UoW natively. Counter: touches 100+ files, triples scope. **Pivoted 2026-04-11: full async absorbed into v2.0.** Rationale: a greenfield FastAPI 2026 team writes fully async code end-to-end; the sync+`run_in_threadpool` compromise was a scope-reduction hack; going fully async eliminates the v2.1 async follow-on entirely and fixes the pre-existing `src/routes/api_v1.py` scoped_session latent bug as a side effect. See `async-pivot-checkpoint.md` §§1-5 for the full rationale, 2nd/3rd order risks, and revised scope (~30,000-35,000 LOC, 5-6 waves, pre-Wave-0 lazy-loading audit required).
```

*Rationale:* The debatable surface was "rejected." It's now chosen.

---

#### Edit 1.16 — §18 Future Work: v2.1 Async SQLAlchemy — DELETE the whole section

```
File: .claude/notes/flask-to-fastapi/flask-to-fastapi-migration.md
Lines: ~2003-2019
```

**old_string:**
```
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
```

**new_string:**
```
## 18. Async SQLAlchemy in v2.0 Waves 4-5 (absorbed, not deferred)

**Pivoted 2026-04-11** — what was originally a v2.1 follow-on is now absorbed into v2.0 as Waves 4-5. See `async-pivot-checkpoint.md` for the full target state, risk register, and revised scope estimate.

**Scope (per checkpoint §3):**
- Convert `create_engine` → `create_async_engine` in `src/core/database/database_session.py`
- Convert `Session` → `AsyncSession` via `async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)` — the `expire_on_commit=False` setting is critical for async because committed-object lazy loads otherwise trigger `MissingGreenlet`
- Convert all repository classes to `async def` methods; use `await session.execute(select(...))` + `.scalars()` rather than sync `session.scalars(...).first()`
- Convert UoW classes to `async def __aenter__` / `async def __aexit__`
- Driver swap: `psycopg2-binary` → `asyncpg` (~6 URL rewrite sites); alembic env.py async adapter
- Convert remaining sync `_impl` functions in `src/core/tools/*.py` to `async def` (several are already async)
- Convert `tests/harness/_base.py::IntegrationEnv` to `async def __aenter__` / `async def __aexit__`; mass-convert integration tests to `async def` + `@pytest.mark.asyncio`
- Adapt `factory_boy` — see checkpoint §3 "factory_boy" for the three options to evaluate
- Audit every `relationship()` access site for lazy-load out of session scope (Risk #1 — the single biggest v2.0 scope risk)
- Bump `pool_size` to match or exceed the pre-migration sync threadpool capacity (Risk #6)

**Why absorbed, not separable:** the original "separable" argument assumed a sync `def` admin-handler seam with explicit `run_in_threadpool` calls. Under the pivot, that seam never exists — admin handlers go straight to async, and extracting sync UoW call-sites would be a second refactor on the same files. Absorbing the work means one migration, one branch, one release.

**Pre-Wave-0 spike (MANDATORY):** run the lazy-loading audit (checkpoint §4 Risk #1) before committing to the Wave 4-5 scope. If the audit reveals the scope is untenable (hundreds of out-of-scope relationship accesses with no clean fix), fall back to Option C: v2.0 as originally planned with sync admin + v2.1 does async separately.

---
```

*Rationale:* Section 18's entire thesis was "async is separable, deferred to v2.1" — that is now dead. Rewriting rather than deleting preserves the cross-references (§14, §17, §6.1 all link here) and documents the new scope in-place.

---

#### Edit 1.17 — §14 Wave strategy (line ~1842): 4 waves → 5-6 waves

```
File: .claude/notes/flask-to-fastapi/flask-to-fastapi-migration.md
Line: 1842
```

**old_string:**
```
## 14. Migration Strategy — 4 Waves (not 8)

The "written today" framing pushes toward fewer, bigger, atomic PRs. Eight waves presume backward-compat matters — it doesn't here. But one giant PR (~18,000 LOC) is unreviewable. Four waves keep each PR at ~one week of work.

**Flask catch-all stays live until Wave 3 as the migration safety net.**
```

**new_string:**
```
## 14. Migration Strategy — 5-6 Waves (pivoted 2026-04-11 from 4)

The "written today" framing pushes toward fewer, bigger, atomic PRs. Eight waves presume backward-compat matters — it doesn't here. But one giant PR (~30,000-35,000 LOC post-pivot) is unreviewable. Five-to-six waves keep each PR at ~one week of work.

**Wave 0-3** ship the Flask removal + admin FastAPI rewrite (originally the whole scope).
**Wave 4-5** absorb the async SQLAlchemy migration that the original plan deferred to v2.1 (see §18 and `async-pivot-checkpoint.md`).
**Mandatory pre-Wave-0 lazy-loading audit spike** — see checkpoint §4 Risk #1. If the spike's outcome demands deferring async, fall back to the original 4-wave plan and push async to v2.1.

**Flask catch-all stays live until Wave 3 as the migration safety net.**
```

*Rationale:* Section header pins the wave count; the rewrite absorbs Waves 4-5.

---

#### Edit 1.18 — §14 add Waves 4-5 section stubs after Wave 3

```
File: .claude/notes/flask-to-fastapi/flask-to-fastapi-migration.md
Lines: ~1893 (after "Wave 3 — Activity stream SSE + cleanup cutover" block, before "### Why not 8 waves?")
```

**old_string:**
```
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
```

**new_string:**
```
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

### Wave 4 — Async database layer (~7,000-10,000 LOC, pivoted 2026-04-11)

- Driver swap: remove `psycopg2-binary` + `types-psycopg2`, add `asyncpg>=0.30.0`
- `src/core/database/database_session.py`: `create_engine` → `create_async_engine`, `scoped_session(sessionmaker(...))` → `async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)`
- `get_db_session()` becomes an `@asynccontextmanager` yielding `AsyncSession`
- `alembic/env.py`: async adapter (~30 LOC, standard pattern)
- All repositories become `async def` with `await session.execute(select(...))` + `.scalars()` pattern
- All UoW classes implement `async def __aenter__` / `async def __aexit__`
- `tests/harness/_base.py::IntegrationEnv` converts to `async def __aenter__` / `async def __aexit__`
- `factory_boy` adapter (decide between the three options in `async-pivot-checkpoint.md` §3)
- Integration tests mass-converted to `async def` + `@pytest.mark.asyncio` (scriptable via AST transform)
- Connection pool tuning (Risk #6)

**Entry gate:** Pre-Wave-0 lazy-loading audit spike (Risk #1) completed and approved — this Wave cannot begin until the audit confirms the scope is manageable.

### Wave 5 — Async cleanup + v2.0.0 release (~3,000-5,000 LOC)

- Convert remaining sync `_impl` functions in `src/core/tools/*.py` (most are already async)
- Benchmark async vs sync baseline (Risk #10) — must be net neutral or positive on hot admin routes
- Audit `created_at` / `updated_at` post-commit access sites (Risk #5 — `expire_on_commit=False` consequence)
- Startup log assertion: schedulers (delivery_webhook, media_buy_status) report "alive" on first tick
- v2.0.0 CHANGELOG: document breaking change from psycopg2 → asyncpg, `expire_on_commit=False` default, async handler signatures
- v2.0.0 tag + production deploy plan approval

### Why not 8 waves?
```

*Rationale:* Adds the new Wave 4 and Wave 5 outlines. The implementation-checklist and execution-details files both reference these waves.

---

### File 2: `/Users/quantum/Documents/ComputedChaos/salesagent/.claude/notes/flask-to-fastapi/flask-to-fastapi-deep-audit.md`

**Total edits: 4 (one large §1.4 rewrite + three small updates)**

**NOTE on §1.4 rewrite approach:** I considered approaches (a) single large old/new_string, (b) sequence of small edits, and (c) Write-based full-section rewrite. I chose **(a) single large old/new_string** because §1.4 is a coherent 45-line subsection and the edit is cleanly scoped — no risk of stranded prose. Approach (b) would splinter the section into micro-edits that risk the file becoming incoherent mid-edit. Approach (c) would force rewriting the surrounding file envelope.

---

#### Edit 2.1 — §1.4 Blocker 4 — FULL SUBSECTION REWRITE

```
File: .claude/notes/flask-to-fastapi/flask-to-fastapi-deep-audit.md
Lines: 181-224
```

**old_string:**
```
### 1.4 Session scoping on the async event-loop thread — plan must default to sync `def` admin handlers

**Severity:** 🚨 BLOCKER (architectural default change)

**The mechanism:** `src/core/database/database_session.py:148` uses:
```python
SessionLocal = scoped_session(sessionmaker(bind=_engine))
```

`scoped_session` with default scopefunc uses `threading.get_ident()` — one session per thread. Under Flask + `a2wsgi.WSGIMiddleware`, each request spins up a dedicated worker thread, so each request gets its own session identity. **Isolated.**

Under the proposed FastAPI migration with `async def` handlers:
- Multiple concurrent admin requests run on the **same event loop thread**
- Each request's `with get_db_session()` block gets the **same** scoped_session identity
- If request A commits mid-transaction and request B is still writing, they share a transaction
- Stale reads, duplicate commits, silently corrupted state

**Current state check (verified):** `rg 'run_in_threadpool' src/` returns 0 matches. Today's AdCP REST endpoints in `src/routes/api_v1.py` are already `async def` and call `_impl` directly without threadpool offload. **This is already a pre-existing latent bug** — if two concurrent AdCP REST requests touch the DB at the same time, they interleave. It hasn't bitten production because traffic is low and `scoped_session` happens to commit quickly.

**The migration plan would make this worse** by adding ~232 admin routes with DB access, all defaulting to `async def`.

**Fix options:**

**Option A (recommended): default admin handlers to `def` (sync).** FastAPI detects non-async handlers and automatically offloads them to a threadpool worker via `starlette.concurrency.run_in_threadpool`. Each threadpool worker has its own thread identity, so `scoped_session` isolates them. This matches today's Flask semantics exactly.

**Option B: every `async def` admin handler wraps sync DB calls in `run_in_threadpool(_sync_fetch)`.** Feasible but bug-prone — one forgotten wrap causes session interleaving.

**Option C: switch to async SQLAlchemy (already planned for v2.1).** Correct long-term, but v2.1 scope creep into v2.0.

**Recommendation:** **Option A as the default**, Option B only when a handler awaits on external I/O (OAuth calls, webhook POSTs, HTTP client calls) that warrant the handler being async.

**Plan changes required:**
1. Foundation modules (`flask-to-fastapi-foundation-modules.md`) — rewrite the example handlers to use `def` not `async def`
2. Worked examples (`flask-to-fastapi-worked-examples.md`) — same
3. Main overview §13 — update the three `accounts.py` examples to use `def`
4. Add a structural guard: `tests/unit/test_architecture_admin_sync_db_no_async.py` — AST-scans admin router files, flags any `async def` handler that calls `get_db_session()` directly without `run_in_threadpool`

**What to keep `async def`:**
- OAuth callbacks (await Authlib)
- SSE handlers (async generators)
- Outbound webhook senders (await httpx)

**Wave assignment:** Wave 0 (decide the default before any router ports).
```

**new_string:**
```
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
```

*Rationale:* §1.4 is the primary source of the sync-def prescription that cascaded through the rest of the plan files. A full rewrite preserves the historical context (how the bug was discovered, what the three options were) while flipping the chosen resolution. The other plan files all cross-reference this section.

---

#### Edit 2.2 — §7 Summary Table B4 row (line ~759)

```
File: .claude/notes/flask-to-fastapi/flask-to-fastapi-deep-audit.md
Line: 759
```

**old_string:**
```
| B4 | 🚨 BLOCKER | Async event loop session interleaving — flip to `def` default | `src/admin/routers/*.py` | Wave 0 decision |
```

**new_string:**
```
| B4 | 🚨 BLOCKER | Async event loop session interleaving — pivoted 2026-04-11 to full async SQLAlchemy (Option A) absorbed into v2.0 Waves 4-5 | `src/core/database/database_session.py` + `src/admin/routers/*.py` + alembic env + test harness | Wave 0 decision + Wave 4-5 execution |
```

*Rationale:* Summary table row must match the pivoted resolution.

---

#### Edit 2.3 — §5.1 structural guard rename

```
File: .claude/notes/flask-to-fastapi/flask-to-fastapi-deep-audit.md
Lines: 647-651
```

**old_string:**
```
### 5.1 `tests/unit/test_architecture_admin_sync_db_no_async.py` (NEW)

**Purpose:** prevent the async-event-loop session scoping bug (Blocker 1.4).

**Logic:** AST-scan `src/admin/routers/*.py`. For every `async def` handler, flag any call to `get_db_session()` that isn't wrapped in `run_in_threadpool()`. Allowlist empty at start.
```

**new_string:**
```
### 5.1 `tests/unit/test_architecture_admin_routes_async.py` (NEW — pivoted 2026-04-11)

**Purpose:** enforce the full-async admin handler invariant (Blocker 1.4 Option A resolution). Original plan was `test_architecture_admin_sync_db_no_async.py` (asserted async handlers must wrap DB work in `run_in_threadpool`) — that guard was the wrong direction under the full-async pivot and is DELETED.

**Logic:** AST-scan `src/admin/routers/*.py`. For every function decorated with `@router.get/post/put/delete/patch`, assert it is `async def`. Allowlist empty at start. Sibling guard `test_architecture_admin_async_db_access.py` asserts DB call-sites use `async with get_db_session()` + `await session.execute(...)` and NOT `run_in_threadpool(_sync_fetch)` wrappers (which would indicate a sync DB call that slipped through).
```

*Rationale:* Guard renamed and logic inverted.

---

#### Edit 2.4 — §6 plan revisions summary (lines 709-717)

```
File: .claude/notes/flask-to-fastapi/flask-to-fastapi-deep-audit.md
Lines: 709-717
```

**old_string:**
```
### Main overview (`flask-to-fastapi-migration.md`)

1. **Flip admin handler default from `async def` to `def`** — update §10, §11, §13 examples
2. **Swap middleware order** — Approximated BEFORE CSRF in §10.2
3. **Add §2.8 or §2.9** — deep audit revisions summary pointing at this file
4. **Update §11 foundation** — `render()` wrapper uses `url_for` exclusively (NO `admin_prefix`/`static_prefix`/`script_root` globals); `_url_for` safe-lookup override pre-registered on `templates.env.globals` before first `TemplateResponse`
5. **Update §12 codemod** — handle the `script_name` split, handle trailing slashes, handle 302→307
6. **Update §16 assumptions** — downgrade "admin handlers async def" from HIGH to MEDIUM; add new assumptions for the 6 blockers
7. **Update §21 verification** — add the 9 new guard tests
```

**new_string:**
```
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
```

*Rationale:* Plan revisions table itself has stale "flip to sync def" prescription.

---

### File 3: `/Users/quantum/Documents/ComputedChaos/salesagent/.claude/notes/flask-to-fastapi/flask-to-fastapi-adcp-safety.md`

**Total edits: 2**

---

#### Edit 3.1 — §9 audit DID NOT cover — benchmark bullet is stale

```
File: .claude/notes/flask-to-fastapi/flask-to-fastapi-adcp-safety.md
Line: 400
```

**old_string:**
```
- **Benchmark of `run_in_threadpool` overhead on hot admin routes:** covered in the execution-details doc but not runtime-verified yet. Recommend benchmarking a read-heavy listing route and a write-heavy form route in Wave 1 before the Wave 2 bulk port.
```

**new_string:**
```
- **Benchmark of async SQLAlchemy vs pre-migration sync baseline on hot admin routes:** covered in the execution-details doc but not runtime-verified yet. Recommend benchmarking a read-heavy listing route and a write-heavy form route in Wave 1 (pre-async-conversion) and again in Wave 4 (post-async-conversion) to quantify latency profile change. Acceptable range is net-neutral to ~5% improvement; significantly worse is a signal that `pool_size` tuning is needed (Risk #6 in `async-pivot-checkpoint.md` §4). Original "`run_in_threadpool` overhead benchmark" framing is stale under the full-async pivot.
```

*Rationale:* Benchmark target reverses under the pivot — measure async vs sync, not threadpool-overhead.

---

#### Edit 3.2 — §10.2 MCP scheduler invariant — mention async DB access

```
File: .claude/notes/flask-to-fastapi/flask-to-fastapi-adcp-safety.md
Line: 423
```

**old_string:**
```
### 10.2 MCP scheduler lifespan is chained via `combine_lifespans`

**File:** `src/app.py:68` — `lifespan=combine_lifespans(app_lifespan, mcp_app.lifespan)`. The FastMCP lifespan (`lifespan_context` at `src/core/main.py:82-103`) starts `delivery_webhook_scheduler` and `media_buy_status_scheduler`. These only run because `combine_lifespans` yields through both.
```

**new_string:**
```
### 10.2 MCP scheduler lifespan is chained via `combine_lifespans`

**File:** `src/app.py:68` — `lifespan=combine_lifespans(app_lifespan, mcp_app.lifespan)`. The FastMCP lifespan (`lifespan_context` at `src/core/main.py:82-103`) starts `delivery_webhook_scheduler` and `media_buy_status_scheduler`. These only run because `combine_lifespans` yields through both.

**Note (2026-04-11 pivot):** Under the full-async SQLAlchemy pivot, scheduler bodies' DB access becomes `async with get_db_session() as session:` / `await session.execute(...)`. No structural change to the lifespan composition itself — schedulers are already running inside an async context via `asyncio.create_task(...)`. Only the DB call-sites inside the scheduler loops change.
```

*Rationale:* Checkpoint §2 notes "MCP scheduler invariant still valid; mention that schedulers use async DB access now."

---

### File 4: `/Users/quantum/Documents/ComputedChaos/salesagent/.claude/notes/flask-to-fastapi/flask-to-fastapi-foundation-modules.md`

**Total edits: 3**

---

#### Edit 4.1 — §Preamble "None of these modules touch async DB" (line 27)

```
File: .claude/notes/flask-to-fastapi/flask-to-fastapi-foundation-modules.md
Line: 27
```

**old_string:**
```
Everything below uses Python 3.12+ syntax. None of these modules touch async DB — SQLAlchemy 2.0 sync inside `get_db_session()` context managers, per v2.0.0 charter.
```

**new_string:**
```
Everything below uses Python 3.12+ syntax. Under the full-async SQLAlchemy pivot (2026-04-11, see `async-pivot-checkpoint.md`), these foundation modules use `AsyncSession` via `async with get_db_session() as db:` / `await db.execute(...)` — SQLAlchemy 2.0 async-native. The pre-pivot plan said "sync SQLAlchemy inside `get_db_session()` context managers, per v2.0.0 charter"; that charter was rewritten 2026-04-11 to absorb async into v2.0.
```

*Rationale:* Preamble asserts sync DB as the policy — must reverse.

---

#### Edit 4.2 — §11.4 `_load_tenant` / `_user_has_tenant_access` — sync-to-async DB access patterns

```
File: .claude/notes/flask-to-fastapi/flask-to-fastapi-foundation-modules.md
Lines: 775-823
```

**old_string:**
```
    try:
        with get_db_session() as db:
            emails_cfg = db.scalars(
                select(TenantManagementConfig).filter_by(config_key="super_admin_emails")
            ).first()
            if emails_cfg and emails_cfg.config_value:
                db_emails = {
                    e.strip().lower() for e in emails_cfg.config_value.split(",") if e.strip()
                }
                if email_l in db_emails:
                    return True

            domains_cfg = db.scalars(
                select(TenantManagementConfig).filter_by(config_key="super_admin_domains")
            ).first()
            if domains_cfg and domains_cfg.config_value:
                db_domains = {
                    d.strip().lower() for d in domains_cfg.config_value.split(",") if d.strip()
                }
                if domain and domain in db_domains:
                    return True
    except Exception as e:
        # DB may not be reachable yet (startup probe). Never block the env path.
        logger.warning("is_super_admin DB check failed, env result used: %s", e)

    return False


def _load_tenant(tenant_id: str) -> dict[str, Any]:
    """Load tenant row as a dict.

    Why dict (not ORM object): the route handler must be able to pass the
    tenant into template contexts, which are serialized into cookies by
    SessionMiddleware if mis-stored. Returning a plain dict prevents
    accidental ORM object serialization attempts. The shape is DELIBERATELY
    minimal — add fields only when a handler needs them.

    Raises HTTPException(404) if tenant not found. The AdminRedirect exception
    is NOT raised here — that's for unauthenticated, not missing-resource.
    """
    with get_db_session() as db:
        tenant = db.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
        if tenant is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Tenant {tenant_id} not found",
            )
```

**new_string:**
```
    try:
        async with get_db_session() as db:
            emails_cfg = (await db.execute(
                select(TenantManagementConfig).filter_by(config_key="super_admin_emails")
            )).scalars().first()
            if emails_cfg and emails_cfg.config_value:
                db_emails = {
                    e.strip().lower() for e in emails_cfg.config_value.split(",") if e.strip()
                }
                if email_l in db_emails:
                    return True

            domains_cfg = (await db.execute(
                select(TenantManagementConfig).filter_by(config_key="super_admin_domains")
            )).scalars().first()
            if domains_cfg and domains_cfg.config_value:
                db_domains = {
                    d.strip().lower() for d in domains_cfg.config_value.split(",") if d.strip()
                }
                if domain and domain in db_domains:
                    return True
    except Exception as e:
        # DB may not be reachable yet (startup probe). Never block the env path.
        logger.warning("is_super_admin DB check failed, env result used: %s", e)

    return False


async def _load_tenant(tenant_id: str) -> dict[str, Any]:
    """Load tenant row as a dict.

    Why dict (not ORM object): the route handler must be able to pass the
    tenant into template contexts, which are serialized into cookies by
    SessionMiddleware if mis-stored. Returning a plain dict prevents
    accidental ORM object serialization attempts. The shape is DELIBERATELY
    minimal — add fields only when a handler needs them.

    Raises HTTPException(404) if tenant not found. The AdminRedirect exception
    is NOT raised here — that's for unauthenticated, not missing-resource.
    """
    async with get_db_session() as db:
        tenant = (await db.execute(
            select(Tenant).filter_by(tenant_id=tenant_id)
        )).scalars().first()
        if tenant is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Tenant {tenant_id} not found",
            )
```

*Rationale:* Full-async conversion: `is_super_admin` becomes async-compatible and `_load_tenant` signature changes to `async def`. Note the enclosing `is_super_admin` function would also need to become `async def` — callers cascade. The `def _user_has_tenant_access`, `def _tenant_has_auth_setup_mode`, and `def get_current_tenant` that follow in the file all need similar async conversion. The applier should consider a more comprehensive edit block that covers all four functions together; I'm showing the pattern here on the two most load-bearing sites.

---

#### Edit 4.3 — §11.4 Gotchas (line 1161) — "Sync DB in async routes" is dead

```
File: .claude/notes/flask-to-fastapi/flask-to-fastapi-foundation-modules.md
Line: 1161
```

**old_string:**
```
- **Sync DB in async routes:** `_load_tenant` is sync. Calling it from an async handler blocks the event loop. For v2.0.0 this is acceptable (existing REST routes do the same); v2.1 moves to async SQLAlchemy.
```

**new_string:**
```
- **Async DB (pivoted 2026-04-11):** `_load_tenant`, `_user_has_tenant_access`, and `_tenant_has_auth_setup_mode` are `async def` under the full-async pivot. All DB access uses `async with get_db_session() as db:` / `(await db.execute(select(...))).scalars().first()`. `run_in_threadpool` is reserved for file I/O, CPU-bound, or sync third-party libraries. See `async-pivot-checkpoint.md` §3 for the target-state patterns.
```

*Rationale:* The "sync DB blocks event loop" gotcha disappears under the pivot.

---

### File 5: `/Users/quantum/Documents/ComputedChaos/salesagent/.claude/notes/flask-to-fastapi/flask-to-fastapi-worked-examples.md`

**Total edits: 4**

---

#### Edit 5.1 — Conventions list line 15 — "all sync SQLAlchemy wrapped in run_in_threadpool"

```
File: .claude/notes/flask-to-fastapi/flask-to-fastapi-worked-examples.md
Line: 15
```

**old_string:**
```
- All sync SQLAlchemy wrapped in `starlette.concurrency.run_in_threadpool`
```

**new_string:**
```
- All SQLAlchemy is async (full-async pivot 2026-04-11): `async with get_db_session() as db:` / `await db.execute(...)` / `.scalars().first()`. `run_in_threadpool` is used only for genuinely blocking non-DB operations (file I/O, CPU-bound work, sync third-party libraries) — never for DB access. See `async-pivot-checkpoint.md` §3 for target-state patterns.
```

*Rationale:* The convention list anchors the rest of the document — reversing it cascades semantically.

---

#### Edit 5.2 — §4.3 Favicon upload `_update_tenant_favicon_url` wrapping in `run_in_threadpool`

```
File: .claude/notes/flask-to-fastapi/flask-to-fastapi-worked-examples.md
Lines: 1409-1439
```

**old_string:**
```
    try:
        await run_in_threadpool(_update_tenant_favicon_url, tenant_id, public_url)
    except Exception:
        logger.error("Favicon DB update failed after disk write for %s", tenant_id, exc_info=True)
        # Rollback: remove the file we just wrote to keep disk/DB consistent
        try:
            await run_in_threadpool(filepath.unlink, missing_ok=True)
        except Exception:
            logger.error("Cleanup after DB failure also failed", exc_info=True)
        flash(request, "Error uploading favicon. Please try again.", "error")
        return RedirectResponse(settings_url, status_code=303)

    flash(request, "Favicon uploaded successfully", "success")
    return RedirectResponse(settings_url, status_code=303)


def _remove_stale_favicons(tenant_dir: Path) -> None:
    for old_ext in ALLOWED_FAVICON_EXTENSIONS:
        old = tenant_dir / f"favicon.{old_ext}"
        if old.exists():
            old.unlink()


def _update_tenant_favicon_url(tenant_id: str, public_url: str) -> None:
    with get_db_session() as db:
        t = db.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
        if t is None:
            raise HTTPException(status_code=404, detail="Tenant not found")
        t.favicon_url = public_url
        t.updated_at = datetime.now(UTC)
        db.commit()
```

**new_string:**
```
    try:
        # Async DB update — no run_in_threadpool wrapper for DB work under the
        # full-async pivot (2026-04-11). run_in_threadpool remains valid for
        # file I/O (filepath.unlink cleanup below) because pathlib is sync.
        await _update_tenant_favicon_url(tenant_id, public_url)
    except Exception:
        logger.error("Favicon DB update failed after disk write for %s", tenant_id, exc_info=True)
        # Rollback: remove the file we just wrote to keep disk/DB consistent.
        # filepath.unlink is sync I/O — run_in_threadpool is correct here.
        try:
            await run_in_threadpool(filepath.unlink, missing_ok=True)
        except Exception:
            logger.error("Cleanup after DB failure also failed", exc_info=True)
        flash(request, "Error uploading favicon. Please try again.", "error")
        return RedirectResponse(settings_url, status_code=303)

    flash(request, "Favicon uploaded successfully", "success")
    return RedirectResponse(settings_url, status_code=303)


def _remove_stale_favicons(tenant_dir: Path) -> None:
    for old_ext in ALLOWED_FAVICON_EXTENSIONS:
        old = tenant_dir / f"favicon.{old_ext}"
        if old.exists():
            old.unlink()


async def _update_tenant_favicon_url(tenant_id: str, public_url: str) -> None:
    async with get_db_session() as db:
        t = (await db.execute(
            select(Tenant).filter_by(tenant_id=tenant_id)
        )).scalars().first()
        if t is None:
            raise HTTPException(status_code=404, detail="Tenant not found")
        t.favicon_url = public_url
        t.updated_at = datetime.now(UTC)
        await db.commit()
```

*Rationale:* Checkpoint §8 explicitly says: "`upload_favicon` outer signature — already async. BUT the internal `run_in_threadpool(_update_tenant_favicon_url, ...)` call for DB work should become `await async_uow.tenants.update_favicon_url(...)`." The filepath.unlink `run_in_threadpool` wrapping STAYS because pathlib is sync I/O (valid non-DB threadpool use).

---

#### Edit 5.3 — §4.3 "Every change labeled" table row — DB update via run_in_threadpool

```
File: .claude/notes/flask-to-fastapi/flask-to-fastapi-worked-examples.md
Line: 1452
```

**old_string:**
```
| Sync DB update inline | `await run_in_threadpool(_update_tenant_favicon_url, ...)` | §18 pattern. |
```

**new_string:**
```
| Sync DB update inline | `await _update_tenant_favicon_url(...)` (direct async call; `_update_tenant_favicon_url` is `async def` with `async with get_db_session()` under the full-async pivot) | Pivoted 2026-04-11 — async DB end-to-end. See §18 + `async-pivot-checkpoint.md` §3. |
```

*Rationale:* The label table now describes the pivoted pattern, not the threadpool wrap.

---

#### Edit 5.4 — §4.1 OAuth helpers — sync `_detect_tenant_from_host` / `_load_oidc_flags` / `_enumerate_tenants_for_user` / `_lookup_idp_logout_url`

```
File: .claude/notes/flask-to-fastapi/flask-to-fastapi-worked-examples.md
Lines: 272-277 and 303-326 and 456-492 and 502-516
```

**NOTE:** This is four separate sync helper functions that the pivoted pattern needs to convert. Rather than four old/new blocks, I propose a single large edit covering lines 272 to 516. The resulting edit is long but coherent. If the applier prefers four smaller edits, the pattern is the same for each: drop `run_in_threadpool(_sync_helper, ...)` wrapping, make the helper `async def`, convert `with get_db_session()` → `async with get_db_session()`, convert `db.scalars(...).first()` → `(await db.execute(...)).scalars().first()`. Showing one representative edit here:

**old_string:**
```
    tenant_context, tenant_name = await run_in_threadpool(_detect_tenant_from_host, request)
    oidc_enabled = False
    oidc_configured = False

    if tenant_context:
        oidc_configured, oidc_enabled = await run_in_threadpool(_load_oidc_flags, tenant_context)
```

**new_string:**
```
    tenant_context, tenant_name = await _detect_tenant_from_host(request)
    oidc_enabled = False
    oidc_configured = False

    if tenant_context:
        oidc_configured, oidc_enabled = await _load_oidc_flags(tenant_context)
```

**And the helpers themselves (lines ~303-326):**

**old_string:**
```
def _detect_tenant_from_host(request: Request) -> tuple[str | None, str | None]:
    """Mirror of Flask login()'s tenant-detection block. Sync — DB access."""
    host = request.headers.get("host", "")
    approximated = request.headers.get("apx-incoming-host")
    with get_db_session() as db:
        if approximated:
            tenant = db.scalars(select(Tenant).filter_by(virtual_host=approximated)).first()
            if tenant:
                return tenant.tenant_id, tenant.name
        if is_sales_agent_domain(host) and not host.startswith("admin."):
            subdomain = extract_subdomain_from_host(host)
            if subdomain:
                tenant = db.scalars(select(Tenant).filter_by(subdomain=subdomain)).first()
                if tenant:
                    return tenant.tenant_id, tenant.name
    return None, None


def _load_oidc_flags(tenant_id: str) -> tuple[bool, bool]:
    with get_db_session() as db:
        config = db.scalars(select(TenantAuthConfig).filter_by(tenant_id=tenant_id)).first()
        if config and config.oidc_client_id:
            return True, bool(config.oidc_enabled)
    return False, False
```

**new_string:**
```
async def _detect_tenant_from_host(request: Request) -> tuple[str | None, str | None]:
    """Mirror of Flask login()'s tenant-detection block. Async DB access (pivoted 2026-04-11)."""
    host = request.headers.get("host", "")
    approximated = request.headers.get("apx-incoming-host")
    async with get_db_session() as db:
        if approximated:
            tenant = (await db.execute(
                select(Tenant).filter_by(virtual_host=approximated)
            )).scalars().first()
            if tenant:
                return tenant.tenant_id, tenant.name
        if is_sales_agent_domain(host) and not host.startswith("admin."):
            subdomain = extract_subdomain_from_host(host)
            if subdomain:
                tenant = (await db.execute(
                    select(Tenant).filter_by(subdomain=subdomain)
                )).scalars().first()
                if tenant:
                    return tenant.tenant_id, tenant.name
    return None, None


async def _load_oidc_flags(tenant_id: str) -> tuple[bool, bool]:
    async with get_db_session() as db:
        config = (await db.execute(
            select(TenantAuthConfig).filter_by(tenant_id=tenant_id)
        )).scalars().first()
        if config and config.oidc_client_id:
            return True, bool(config.oidc_enabled)
    return False, False
```

*Rationale:* These helpers are called from the already-async `login` / `google_callback` / `logout` handlers; checkpoint §8 says OAuth handlers themselves are correct, but their internal `run_in_threadpool(_sync_helper, ...)` calls need conversion. The applier should perform the equivalent transformation on `_enumerate_tenants_for_user` (lines ~456-492) and `_lookup_idp_logout_url` (lines ~513-516) — same pattern, same rationale.

---

### File 6: `/Users/quantum/Documents/ComputedChaos/salesagent/.claude/notes/flask-to-fastapi/flask-to-fastapi-execution-details.md`

**Total edits: 6**

---

#### Edit 6.1 — Wave 0 criterion 6 (line 22): `async def` dep functions

```
File: .claude/notes/flask-to-fastapi/flask-to-fastapi-execution-details.md
Line: 22
```

**old_string:**
```
6. `/Users/quantum/Documents/ComputedChaos/salesagent/src/admin/deps/auth.py` exports `CurrentUserDep`, `RequireAdminDep`, `RequireSuperAdminDep` as `Annotated[...]` aliases with module-level `async def` dep functions.
```

**new_string:**
```
6. `/Users/quantum/Documents/ComputedChaos/salesagent/src/admin/deps/auth.py` exports `CurrentUserDep`, `RequireAdminDep`, `RequireSuperAdminDep` as `Annotated[...]` aliases with module-level `async def` dep functions (full-async pivot 2026-04-11 — dep functions use `async with get_db_session()` and `await db.execute(...)`).
```

*Rationale:* The original criterion already says `async def` (correctly) but needs to pin the async DB access pattern per the pivot. The checkpoint §2 flagged this line ("Wave 0 criterion 6 — module-level sync def dep functions — REVERSE to async"), suggesting an earlier draft had sync def. Current state is correct for handler signature but the pattern elaboration is what needs clarifying.

---

#### Edit 6.2 — Wave 0 risk assessment (line 296): `run_in_threadpool` overhead benchmark

```
File: .claude/notes/flask-to-fastapi/flask-to-fastapi-execution-details.md
Line: 296
```

**old_string:**
```
| `run_in_threadpool` overhead exceeds 5ms for a hot endpoint (e.g., `GET /admin/tenant/{id}/products`) | Medium | Medium | Benchmark in CI against Wave 1 baseline; acceptable range p99 <15ms overhead; above that, move specific routes to a hand-rolled async repo. |
```

**new_string:**
```
| Async SQLAlchemy latency profile regresses vs pre-migration sync baseline | Medium | Medium | Benchmark in CI async (Wave 4-5) vs pre-migration sync baseline (Wave 2); acceptable range is net-neutral to ~5% improvement under moderate concurrency; significantly worse signals `pool_size` tuning is needed (Risk #6 in `async-pivot-checkpoint.md` §4). Under low concurrency async has slightly higher per-request overhead; under high concurrency it wins big. |
```

*Rationale:* The benchmark target reverses under the pivot.

---

#### Edit 6.3 — Wave 3 risk assessment (line 431): activity_stream run_in_threadpool DB polling

```
File: .claude/notes/flask-to-fastapi/flask-to-fastapi-execution-details.md
Line: 431
```

**old_string:**
```
| `activity_stream.py` running via `run_in_threadpool` for DB polling starves the threadpool | Medium | Medium | Poll loop uses `asyncio.sleep` between queries; benchmark showed <2 concurrent DB queries per stream. |
```

**new_string:**
```
| `activity_stream.py` SSE poll loop under async SQLAlchemy holds an AsyncSession open across `asyncio.sleep` boundaries | Medium | Medium | Open a fresh `async with get_db_session()` inside each tick rather than holding one across sleeps; benchmark showed <2 concurrent DB queries per stream. Avoids connection-pool pressure. |
```

*Rationale:* The threadpool framing is dead under the pivot; the actual risk under async SQLAlchemy is holding sessions across `asyncio.sleep`.

---

#### Edit 6.4 — Wave 3 rollback window (line 456): v2.1 async SQLAlchemy PR

```
File: .claude/notes/flask-to-fastapi/flask-to-fastapi-execution-details.md
Line: 456
```

**old_string:**
```
**Rollback window:** open until v2.1 (the async SQLAlchemy PR) merges. After v2.1, rollback becomes effectively impossible because async deps have spread through the codebase.
```

**new_string:**
```
**Rollback window:** open until Wave 4 (the async SQLAlchemy conversion) merges. After Wave 4, rollback becomes effectively impossible because async deps have spread through the codebase and the driver has switched to asyncpg (pivoted 2026-04-11 — async SQLAlchemy absorbed into v2.0).
```

*Rationale:* v2.1 PR no longer exists; the rollback window boundary moves to Wave 4.

---

#### Edit 6.5 — Part 2 assumption #3 (line 510): sync SQLAlchemy + run_in_threadpool

```
File: .claude/notes/flask-to-fastapi/flask-to-fastapi-execution-details.md
Line: 510
```

**old_string:**
```
3. **Sync SQLAlchemy + `run_in_threadpool` <5ms.** Verify: benchmark per Part 3.D. When: after Wave 1 routers land, before Wave 2 bulk port. Failure: p99 >15ms on hot endpoints. Fallback: hand-roll async for hot routes in v2.0, defer rest to v2.1.
```

**new_string:**
```
3. **Full async SQLAlchemy in v2.0** (pivoted 2026-04-11). Verify: benchmark per Part 3.D compares async vs pre-migration sync baseline. When: Wave 2 baseline captured; Wave 4-5 comparison run. Failure: regression >10% on read-heavy hot endpoints (write-heavy regressions up to 15% acceptable). Fallback: tune `pool_size` (Risk #6) OR (last resort) hand-roll `selectinload` eager-loads on the worst offenders; if that's not enough, fall back to Option C and defer async to v2.1. Pre-Wave-0 lazy-loading audit spike (Risk #1) is the early-warning gate — if the audit reveals relationship-access scope is untenable, switch to Option C before starting Wave 0.
```

*Rationale:* Assumption reverses from "sync stays sync" to "full async."

---

#### Edit 6.6 — Part 2 assumption #4 (line 512)

```
File: .claude/notes/flask-to-fastapi/flask-to-fastapi-execution-details.md
Line: 512
```

**old_string:**
```
4. **Admin handlers `async def` + `run_in_threadpool`.** Verify: AST guard `test_architecture_admin_async_signatures.py` asserts every `src/admin/routers/*.py` handler is `async def`. When: Wave 1 entry. Failure: sync handler found. Fallback: rewrite that handler.
```

**new_string:**
```
4. **Admin handlers `async def` + full async SQLAlchemy end-to-end** (pivoted 2026-04-11). Verify: AST guard `test_architecture_admin_routes_async.py` (KEEP — this was the original correct-direction name) asserts every `src/admin/routers/*.py` handler is `async def`; sibling guard `test_architecture_admin_async_db_access.py` asserts DB access uses `async with get_db_session()` + `await db.execute(...)` rather than sync `with` or `run_in_threadpool(_sync_fetch)`. The stale `test_architecture_admin_sync_db_no_async.py` from the pre-pivot sync-def resolution is DELETED (wrong direction). When: Wave 1 entry (handler signature guard); Wave 4 entry (async DB access guard). Failure: sync handler or sync DB access found. Fallback: rewrite that handler.
```

*Rationale:* Per checkpoint §2: `test_architecture_admin_async_signatures.py` was the ORIGINAL (correct-direction) name and should be KEPT; the sync-def guard that the sync pivot introduced should be DELETED. The checkpoint notes "Guard description `test_architecture_admin_async_signatures.py` (line 752) — this was the ORIGINAL (pre-pivot to sync) guard. Its direction was correct for full async. It asserts every handler IS async def. KEEP this guard, DELETE the sync-def guard." The plan file uses `test_architecture_admin_async_signatures.py` as the guard name on line 752, so the rename to `test_architecture_admin_routes_async.py` is a harmonization pass.

---

#### Edit 6.7 — Part 3 §A guard description (line 752-754): rename guard

```
File: .claude/notes/flask-to-fastapi/flask-to-fastapi-execution-details.md
Lines: 752-754
```

**old_string:**
```
#### `tests/unit/test_architecture_admin_async_signatures.py`

Scans `src/admin/routers/*.py` and asserts every function decorated with `@router.get/post/put/delete/patch` is `async def`. Sibling to existing `test_architecture_repository_pattern.py`.
```

**new_string:**
```
#### `tests/unit/test_architecture_admin_routes_async.py`

Scans `src/admin/routers/*.py` and asserts every function decorated with `@router.get/post/put/delete/patch` is `async def`. Sibling to existing `test_architecture_repository_pattern.py`. **Pivoted 2026-04-11:** this guard was originally named `test_architecture_admin_async_signatures.py` under a pre-pivot draft; renamed for consistency with other `test_architecture_admin_*_async.py` guards in the full-async pivot. The stale `test_architecture_admin_sync_db_no_async.py` (which asserted async handlers must wrap DB in `run_in_threadpool`) is the wrong direction under the pivot and is DELETED; this guard replaces it.

#### `tests/unit/test_architecture_admin_async_db_access.py`

Scans `src/admin/routers/*.py` and asserts every DB access site uses `async with get_db_session()` + `await db.execute(...)` patterns, NOT sync `with get_db_session()` or `run_in_threadpool(_sync_fetch)` wrappers around DB work. The `run_in_threadpool` helper is still valid for file I/O, CPU-bound, and sync-third-party-library calls — the guard specifically flags calls where the wrapped function does DB work (identified by an inner `get_db_session()` call or a `Session`/`AsyncSession` parameter). Sibling guard added under the full-async pivot (2026-04-11).
```

*Rationale:* Per the checkpoint's renaming recommendation. Also adds the second new guard.

---

#### Edit 6.8 — Part 3 §D benchmark harness (lines 966-1005): rewrite from threadpool-overhead to async-vs-sync

```
File: .claude/notes/flask-to-fastapi/flask-to-fastapi-execution-details.md
Lines: 966-1005
```

**old_string:**
```
### D. Benchmark harness (assumption #3)

**Tool:** `pytest-benchmark` for deterministic microbenchmarks + `wrk` for macro load test.

**Routes benchmarked:**
1. **Read-heavy:** `GET /admin/tenant/t1/products` — lists 100 products via UoW. Measures `run_in_threadpool` wrapping `ProductRepo.list_for_tenant()`.
2. **Write-heavy:** `POST /admin/tenant/t1/accounts` — creates one account, redirects. Measures `run_in_threadpool` wrapping `AccountRepo.create()` + session flush.

**Harness file:** `/Users/quantum/Documents/ComputedChaos/salesagent/tests/benchmark/test_run_in_threadpool_overhead.py`

```python
import pytest
from fastapi.concurrency import run_in_threadpool


@pytest.mark.benchmark(group="threadpool-overhead")
def test_threadpool_overhead_sync_noop(benchmark):
    def noop():
        return 1
    result = benchmark(lambda: run_in_threadpool(noop))
    # p50 < 1ms, p99 < 5ms per assumption #3


@pytest.mark.benchmark(group="admin-routes")
def test_list_products_route(benchmark, integration_db):
    from tests.harness import IntegrationEnv
    with IntegrationEnv(tenant_id="t1", principal_id="p1") as env:
        ...
        client = env.get_admin_client()
        benchmark(lambda: client.get("/admin/tenant/t1/products"))
        # Acceptance: p50 < 50ms, p99 < 200ms
```

**Acceptance criteria:**
- `test_threadpool_overhead_sync_noop`: p50 < 1ms, p99 < 5ms.
- `test_list_products_route`: p50 < 50ms, p99 < 200ms.
- `test_create_account_route`: p50 < 80ms, p99 < 300ms.

**Storage:** `pytest-benchmark --benchmark-json=test-results/wave-N/benchmark.json` committed to repo per wave. `scripts/compare_benchmarks.py` asserts wave N doesn't regress >20% from wave N-1.
```

**new_string:**
```
### D. Benchmark harness (assumption #3 — pivoted 2026-04-11 to async-vs-sync comparison)

**Tool:** `pytest-benchmark` for deterministic microbenchmarks + `wrk` for macro load test.

**Routes benchmarked:**
1. **Read-heavy:** `GET /admin/tenant/t1/products` — lists 100 products via UoW. Measures async DB latency end-to-end vs pre-migration sync baseline.
2. **Write-heavy:** `POST /admin/tenant/t1/accounts` — creates one account, redirects. Measures async DB latency end-to-end vs pre-migration sync baseline.

**Harness file:** `/Users/quantum/Documents/ComputedChaos/salesagent/tests/benchmark/test_admin_routes_async_vs_sync.py`

```python
import pytest


@pytest.mark.benchmark(group="admin-routes-async")
def test_list_products_route(benchmark, integration_db):
    """Async route benchmark — compares against pre-migration sync baseline."""
    from tests.harness import IntegrationEnv
    async def _run():
        async with IntegrationEnv(tenant_id="t1", principal_id="p1") as env:
            ...
            client = env.get_admin_client()
            await client.get("/admin/tenant/t1/products")
    benchmark(lambda: asyncio.run(_run()))
    # Acceptance: async p50 ≤ sync baseline p50 + 5%, p99 ≤ sync baseline p99 + 10%


@pytest.mark.benchmark(group="admin-routes-async")
def test_create_account_route(benchmark, integration_db):
    """Async write-heavy — compares against pre-migration sync baseline."""
    ...
```

**Acceptance criteria:**
- `test_list_products_route`: async p50 ≤ sync baseline p50 + 5%, p99 ≤ sync baseline p99 + 10%
- `test_create_account_route`: async p50 ≤ sync baseline p50 + 5%, p99 ≤ sync baseline p99 + 15% (write-heavy tolerances wider)
- Under HIGH concurrency (load test with `wrk -c 100 -t 10 -d 30s`): async throughput ≥ sync baseline (should win decisively)

**Storage:** `pytest-benchmark --benchmark-json=test-results/wave-N/benchmark.json` committed to repo per wave. `scripts/compare_benchmarks.py` asserts wave N doesn't regress >20% from wave N-1. **Wave 2 captures the sync baseline; Wave 4 captures the post-async comparison.**

**Failure fallback:** if async regresses significantly under the benchmark, first tune `pool_size` (Risk #6 in `async-pivot-checkpoint.md` §4). If that doesn't close the gap, apply `selectinload` eager-loading to the worst offenders. If THAT doesn't close the gap, invoke the last-resort fallback: revert to Option C (sync `def` admin handlers) and defer async to v2.1.
```

*Rationale:* The entire benchmark's thesis was "threadpool overhead ≤5ms" — that framing is stale.

---

### File 7: `/Users/quantum/Documents/ComputedChaos/salesagent/.claude/notes/flask-to-fastapi/implementation-checklist.md`

**Total edits: 9** (the pivot marker at the top stays; the body edits below propagate the pivot)

---

#### Edit 7.1 — §1.2 architectural decisions first bullet (line 70)

```
File: .claude/notes/flask-to-fastapi/implementation-checklist.md
Line: 70
```

**old_string:**
```
- [ ] **Admin handler default: sync `def`** (not `async def`) — per deep audit blocker #4; documented in `flask-to-fastapi-migration.md` §2.8
```

**new_string:**
```
- [ ] **Admin handlers `async def` end-to-end with full async SQLAlchemy** — pivoted 2026-04-11 from Option C (sync def) to Option A (full async absorbed into v2.0 Waves 4-5) — per deep audit blocker #4 (rewritten); documented in `flask-to-fastapi-migration.md` §2.8, `async-pivot-checkpoint.md`, and full new scope in §18 of the migration doc
```

*Rationale:* The first architectural decision is the most load-bearing — must flip.

---

#### Edit 7.2 — §2 Blocker 4 section (lines 114-120)

```
File: .claude/notes/flask-to-fastapi/implementation-checklist.md
Lines: 114-120
```

**old_string:**
```
- [ ] **Blocker 4: Async event-loop session interleaving**
  - [ ] All admin router handlers default to **sync `def`** (NOT `async def`)
  - [ ] `async def` is used ONLY for: OAuth callbacks, SSE generators, outbound webhook senders, other handlers that `await` external I/O
  - [ ] `tests/unit/test_architecture_admin_sync_db_no_async.py` exists and is green — AST-scans `src/admin/routers/*.py`, flags any `async def` handler that calls `get_db_session()` directly without `run_in_threadpool`
  - [ ] Foundation module examples in `flask-to-fastapi-foundation-modules.md` updated to sync `def`
  - [ ] Worked examples in `flask-to-fastapi-worked-examples.md` updated to sync `def` (except OAuth/SSE)
  - [ ] Main overview §13 `accounts.py` examples updated to sync `def`
```

**new_string:**
```
- [ ] **Blocker 4: Async event-loop session interleaving — PIVOTED 2026-04-11 to full async SQLAlchemy (Option A absorbed into v2.0 Waves 4-5)**
  - [ ] Pre-Wave-0 lazy-loading audit spike completed and approved (Risk #1 in `async-pivot-checkpoint.md` §4) — enumerates every `relationship()` access site and classifies as safe / eager-loadable / requires-rewrite
  - [ ] `src/core/database/database_session.py` converted: `create_engine` → `create_async_engine`, `scoped_session(sessionmaker(...))` → `async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)`
  - [ ] `get_db_session()` is an `@asynccontextmanager` yielding `AsyncSession`
  - [ ] Driver swap: `psycopg2-binary` + `types-psycopg2` removed, `asyncpg>=0.30.0` added in `pyproject.toml`
  - [ ] `alembic/env.py` uses async adapter (standard `create_async_engine` + `run_sync` pattern)
  - [ ] All repository classes use `async def` methods with `(await session.execute(select(...))).scalars().first()` pattern
  - [ ] All UoW classes implement `async def __aenter__` / `async def __aexit__`
  - [ ] All admin router handlers are `async def` with `async with get_db_session()` / `await` DB calls
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
```

*Rationale:* Blocker 4's entire checklist section must be rewritten to the pivoted target state.

---

#### Edit 7.3 — §4 Wave 0 structural guards (lines 224-235)

```
File: .claude/notes/flask-to-fastapi/implementation-checklist.md
Line: 225
```

**old_string:**
```
- [ ] `tests/unit/test_architecture_admin_sync_db_no_async.py` — Blocker 4 guard
```

**new_string:**
```
- [ ] `tests/unit/test_architecture_admin_routes_async.py` — Blocker 4 guard (pivoted 2026-04-11). Asserts every admin router handler is `async def`. Replaces the wrong-direction `test_architecture_admin_sync_db_no_async.py` from the original plan.
- [ ] `tests/unit/test_architecture_admin_async_db_access.py` — Blocker 4 sibling guard. Asserts admin DB access uses `async with get_db_session()` + `await session.execute(...)`, not sync `with` or `run_in_threadpool(_sync_fetch)` wrappers for DB work.
```

*Rationale:* Replaces the stale guard name with the pivoted guard names.

---

#### Edit 7.4 — §4 Wave 0 "Blockers fixed in Wave 0" (line 247)

```
File: .claude/notes/flask-to-fastapi/implementation-checklist.md
Line: 247
```

**old_string:**
```
- [ ] Blocker 4 (async session interleaving) — via handler-default flip + AST guard
```

**new_string:**
```
- [ ] Blocker 4 (async session interleaving) — via full async SQLAlchemy pivot (Option A, 2026-04-11); Wave 0 adds the `test_architecture_admin_routes_async.py` guard + the lazy-loading audit spike. The full async conversion lands in Wave 4-5; the Wave 0 guard asserts the target-state handler signature is maintained from day one.
```

*Rationale:* Wave 0 fix mechanism changes.

---

#### Edit 7.5 — §4 Wave 3 rollback window mention (line 637)

```
File: .claude/notes/flask-to-fastapi/implementation-checklist.md
Line: 637
```

**old_string:**
```
- [ ] Rollback window is open until v2.1 (async SQLAlchemy) merges; after v2.1, rollback becomes effectively impossible
```

**new_string:**
```
- [ ] Rollback window is open until Wave 4 (the async SQLAlchemy conversion within v2.0) merges; after Wave 4, rollback becomes effectively impossible (driver has switched to asyncpg and async deps have spread through the codebase). Pivoted 2026-04-11 — async SQLAlchemy is no longer a separate v2.1 PR; it's absorbed as Wave 4-5 of v2.0.
```

*Rationale:* v2.1 async follow-on no longer exists.

---

#### Edit 7.6 — §6 post-migration verification (line 651): v2.1 async kickoff

```
File: .claude/notes/flask-to-fastapi/implementation-checklist.md
Line: 651
```

**old_string:**
```
- [ ] v2.1 async SQLAlchemy migration scoping kickoff scheduled
```

**new_string:**
```
- [ ] Async SQLAlchemy migration already merged as Wave 4-5 of v2.0 (pivoted 2026-04-11 — no separate v2.1 kickoff needed)
```

*Rationale:* v2.1 kickoff line is moot.

---

#### Edit 7.7 — §8 v2.1 scope first bullet (line 690): Async SQLAlchemy

```
File: .claude/notes/flask-to-fastapi/implementation-checklist.md
Line: 690
```

**old_string:**
```
- [ ] **Async SQLAlchemy** — convert `create_engine` → `create_async_engine`, `Session` → `AsyncSession`, all repositories to `async def`, delete every `run_in_threadpool(_sync_fn)` wrapper (~100+ files affected). Detail: `flask-to-fastapi-migration.md` §18.
```

**new_string:**
```
- [x] ~~**Async SQLAlchemy**~~ **MOVED TO v2.0 Waves 4-5** (pivoted 2026-04-11) — convert `create_engine` → `create_async_engine`, `Session` → `AsyncSession`, all repositories to `async def`, ~100+ files affected. Detail: `async-pivot-checkpoint.md` §3 for target state; `flask-to-fastapi-migration.md` §18 (rewritten) for wave execution; deep audit §1.4 (rewritten) for Option A rationale.
```

*Rationale:* Line moves from v2.1 scope to "already absorbed into v2.0."

---

#### Edit 7.8 — §1.1 prerequisites — add pre-Wave-0 lazy-loading audit gate

```
File: .claude/notes/flask-to-fastapi/implementation-checklist.md
Line: 66
```

**old_string:**
```
- [ ] Pre-Wave-0 `main` branch passes `make quality` + `tox -e integration` + `tox -e bdd`
- [ ] Pre-Wave-0 `main` has `a2wsgi` Flask mount still at `src/app.py:299-304` (safety net)
- [ ] v1.99.0 git tag plan documented (last-known-good Flask-era release, tagged before Wave 3 merges)
```

**new_string:**
```
- [ ] Pre-Wave-0 `main` branch passes `make quality` + `tox -e integration` + `tox -e bdd`
- [ ] Pre-Wave-0 `main` has `a2wsgi` Flask mount still at `src/app.py:299-304` (safety net)
- [ ] v1.99.0 git tag plan documented (last-known-good Flask-era release, tagged before Wave 3 merges)
- [ ] **Pre-Wave-0 lazy-loading audit spike completed and approved (async pivot 2026-04-11)** — enumerates every `relationship()` definition in `src/core/database/models/` and classifies every access site as safe (in-scope), fixable (eager-load), or requires-rewrite. This audit gates the Wave 4-5 async absorption scope. If the audit reveals the scope is untenable, fall back to Option C (sync def admin) and defer async to v2.1. See `async-pivot-checkpoint.md` §4 Risk #1 for the full audit procedure.
- [ ] **Pre-Wave-0 async driver compatibility spike completed (async pivot 2026-04-11)** — run the full test suite on a staging branch with `asyncpg` instead of `psycopg2-binary` to catch driver-compat surprises (JSONB codec, UUID/Interval types, LISTEN/NOTIFY API drift, COPY bulk imports). Estimated 1-2 days of debugging. See checkpoint §4 Risk #2.
```

*Rationale:* Two new pre-Wave-0 gates mandated by the checkpoint's §4 Risk register.

---

#### Edit 7.9 — §1.1 environment prerequisites — add `asyncpg` to infra

```
File: .claude/notes/flask-to-fastapi/implementation-checklist.md
Line: 46
```

**old_string:**
```
- [ ] `SESSION_SECRET` env var is set in staging secret store
- [ ] `SESSION_SECRET` env var is set in production secret store
- [ ] `SESSION_SECRET` env var is set in test/CI secret store
```

**new_string:**
```
- [ ] `SESSION_SECRET` env var is set in staging secret store
- [ ] `SESSION_SECRET` env var is set in production secret store
- [ ] `SESSION_SECRET` env var is set in test/CI secret store
- [ ] `DATABASE_URL` env var format compatible with asyncpg driver rewrite (staging + prod + test): `postgresql://...` gets rewritten to `postgresql+asyncpg://...` at engine construction — verified — pivoted 2026-04-11
```

*Rationale:* New infra prerequisite for driver swap.

---

### File 8: `/Users/quantum/Documents/ComputedChaos/salesagent/.claude/notes/flask-to-fastapi/CLAUDE.md`

**Total edits: 3** — the folder-level CLAUDE.md already has a pivot banner at the top (correct), and Critical Invariant #4 already says "PIVOTED" (correct). Three additional spots need harmonization.

---

#### Edit 8.1 — Migration conventions bullet 1: "PIVOTED: Admin handlers are async def"

Verify this bullet is complete. From reading the file, lines around "Migration conventions that differ from the rest of the codebase":

```
File: .claude/notes/flask-to-fastapi/CLAUDE.md
```

The CLAUDE.md file shown to me already has the correct pivoted text:

> - **⚠️ PIVOTED: Admin handlers are `async def` end-to-end with full async SQLAlchemy.** Consistent with the rest of the codebase. The scoped_session bug is eliminated by `AsyncSession` + `async_sessionmaker` (there is no more `threading.get_ident()` scoping to race on). `run_in_threadpool` is still used for genuinely blocking work (file I/O, CPU-bound calls, sync third-party libraries) but NOT for DB work — all DB access goes through `async with` UoW. See `async-pivot-checkpoint.md` for full detail.

**This bullet is already correct.** No edit needed.

And:
> - **⚠️ PIVOTED: Async admin handlers wrap DB access in `async with get_db_session() as session:` or `async with UoW() as uow:`**, NOT in `run_in_threadpool`. Under full async SQLAlchemy, `get_db_session()` is an async context manager yielding an `AsyncSession`; repositories return via `await session.execute(...)`. `run_in_threadpool` remains valid for non-DB blocking operations only.

**This bullet is also already correct.** No edit needed.

**Critical Invariant #4** on the folder CLAUDE.md:
> 4. **⚠️ PIVOTED to Option A (full async SQLAlchemy in v2.0).** ...

**Already correct.** No edit needed.

**v2.1 deferred items section** — the CLAUDE.md shown already lists:
> - ~~Async SQLAlchemy~~ **MOVED TO v2.0** per async-pivot-checkpoint.md — absorbed into v2.0 scope as Waves 4-5

**Already correct.** No edit needed.

**Conclusion for File 8:** The folder-level CLAUDE.md has been correctly pre-edited with the pivot marker + three corrected bullets. No additional edits needed. The 3 edits referenced in checkpoint §2 for this file have ALREADY been applied. This line in the checkpoint is STALE:

> ### Folder `CLAUDE.md`
> - Critical Invariant #4 — rewrite (pivot marker added alongside)
> - Migration conventions "sync def" bullet — reverse
> - "v2.1 deferred items" — remove async SQLAlchemy from the list

These three changes are already present in the file as shown to me. **No edits needed for File 8.**

*Rationale:* File 8 is the only file where the pivot propagation is already complete. Mark it as DONE and skip.

---

## Part B — Cross-file consistency check

After the edits above are applied, these phrases/terms MUST appear consistently across the 8 plan files:

### Required consistent phrases

| Phrase | Must match | Files that reference |
|---|---|---|
| "full async SQLAlchemy end-to-end" | Exact phrase or unambiguous variant | migration.md §2, §2.8, §13, §16, §17, §18; deep-audit §1.4; foundation-modules §preamble; worked-examples §conventions; execution-details §Wave 0/2/4/5; implementation-checklist §1.2, §2, §4, §8; CLAUDE.md |
| "Waves 4-5 absorb async" (or "v2.0 Waves 4-5") | Exact phrase | migration.md §14, §18; deep-audit §1.4; implementation-checklist §8; CLAUDE.md |
| "Option A resolution" (or "Option A (full async), chosen") | Consistent | deep-audit §1.4, §7; migration.md §2 directive #6, §2.8; implementation-checklist §1.2 |
| "pre-Wave-0 lazy-loading audit spike" | Exact phrase | migration.md §14, §16; deep-audit §1.4; implementation-checklist §1.1; adcp-safety.md §9 (optional) |
| "`async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)`" | Exact code | migration.md §18; deep-audit §1.4; implementation-checklist §2 Blocker 4; foundation-modules §preamble |
| "`psycopg2-binary` → `asyncpg`" | Consistent format | migration.md §15; implementation-checklist §1.1, §2; deep-audit §1.4 |
| "`test_architecture_admin_routes_async.py`" | Exact filename | migration.md §2.8; deep-audit §5.1; execution-details Part 3.A; implementation-checklist §2, §4 |

### Inconsistencies flagged for resolution

1. **Wave count — "5-6 waves" vs "4 waves"** — the migration.md §14 and implementation-checklist.md should match. Proposed: "5-6 waves" with a note that Wave 5 (async cleanup + release) may be merged into Wave 4 depending on scope after the lazy-loading audit. My edits use "5-6" consistently. Verify after apply.

2. **LOC estimate — "30,000-35,000 LOC" vs old "18,000 LOC"** — migration.md §1 executive summary doesn't currently state a total; my §14 edit bakes in "~30,000-35,000 LOC post-pivot". The implementation-checklist doesn't state a total LOC estimate. Action: verify after apply. Agent A's audit output will provide the authoritative LOC number — until then, use the checkpoint's estimate.

3. **Branch lifetime — "2 weeks original" vs "4-6 weeks pivoted"** — migration.md §14 doesn't state a branch lifetime; checkpoint §5 says "4-6 weeks." Action: add a branch lifetime statement to §14 in a follow-up edit after Agent A's audit completes.

4. **Guard name consistency** — checkpoint §2 flags two different guard names:
   - `test_architecture_admin_sync_db_no_async.py` — DELETE (wrong direction)
   - `test_architecture_admin_async_signatures.py` — KEEP (original correct-direction name)
   - `test_architecture_admin_routes_async.py` — my proposed new name for consistency with other `test_architecture_admin_*` guards

   The checkpoint says KEEP the original name. My edits rename to `test_architecture_admin_routes_async.py`. **Inconsistency with checkpoint §2 guidance** — the applier should decide: either (a) keep the original name `test_architecture_admin_async_signatures.py` per checkpoint guidance, or (b) rename to `test_architecture_admin_routes_async.py` for naming consistency with sibling guards. **I recommend (b)** for naming consistency, but the applier should confirm.

5. **`factory_boy` adapter** — checkpoint §3 lists three options; my edits don't pin a choice. Action: a separate followup edit pass on implementation-checklist.md and foundation-modules.md after Agent B's risk analysis chooses one.

6. **"sync SQLAlchemy stays sync" terminology** — check that no stale occurrence remains. After my edits, the migration.md §17 debatable-surface #2 should be the only remaining mention, and it's historical context (rejected).

---

## Part C — Guards to ADD / REMOVE / RENAME / KEEP

| Guard | Action | Rationale |
|---|---|---|
| `test_architecture_admin_sync_db_no_async.py` | **REMOVE** | Wrong direction under full-async pivot. Was the pre-pivot sync-def guard. Delete from plan files, do NOT implement. |
| `test_architecture_admin_async_signatures.py` | **RENAME** → `test_architecture_admin_routes_async.py` (or KEEP original name per checkpoint §2 guidance, applier's call) | Checkpoint §2 says "KEEP this guard, DELETE the sync-def guard." My edits rename for consistency with sibling guards, but applier may prefer the original name. |
| `test_architecture_admin_routes_async.py` | **ADD** | New name for the async handler guard; replaces sync-db guard. |
| `test_architecture_admin_async_db_access.py` | **ADD** | New sibling guard: asserts DB access uses `async with get_db_session()` + `await` pattern, not sync `with` or `run_in_threadpool(_sync_fetch)` for DB work. |
| `test_templates_url_for_resolves.py` | **KEEP** | Not affected by async pivot. |
| `test_templates_no_hardcoded_admin_paths.py` | **KEEP** | Not affected by async pivot. |
| `test_architecture_admin_routes_named.py` | **KEEP** | Not affected by async pivot. |
| `test_trailing_slash_tolerance.py` | **KEEP** | Not affected by async pivot. |
| `test_oauth_redirect_uris_immutable.py` | **KEEP** | Not affected by async pivot. |
| `test_oauth_callback_routes_exact_names.py` | **KEEP** | Not affected by async pivot. |
| `test_codemod_idempotent.py` | **KEEP** | Not affected by async pivot. |
| `test_architecture_no_flask_imports.py` | **KEEP** | Not affected by async pivot. |
| `test_architecture_csrf_exempt_covers_adcp.py` | **KEEP** | Not affected by async pivot. |
| `test_architecture_approximated_middleware_path_gated.py` | **KEEP** | Not affected by async pivot. |
| `test_architecture_admin_routes_excluded_from_openapi.py` | **KEEP** | Not affected by async pivot. |
| `test_architecture_single_worker_invariant.py` | **KEEP** | Not affected by async pivot. Scheduler singleton invariant unchanged. |
| `test_architecture_scheduler_lifespan_composition.py` | **KEEP** | Not affected by async pivot. Schedulers already run in async context. |
| `test_architecture_a2a_routes_grafted.py` | **KEEP** | Not affected by async pivot. |
| `test_architecture_harness_overrides_isolated.py` | **KEEP** | Not affected by async pivot. Test harness conversion doesn't change the override isolation invariant. |
| `test_foundation_modules_import.py` | **KEEP** | Not affected by async pivot. |
| `test_architecture_external_domain_post_redirects_before_csrf.py` | **KEEP** | Middleware ordering invariant unchanged. |
| `test_admin_html_accept_error_handler.py` | **KEEP** | HTML-Accept AdCPError handler invariant unchanged. |
| `test_integration_schemas_discovery_external_contract.py` | **KEEP** | External contract preservation unchanged. |

**Net change:** -1 guard removed (sync-db), +2 guards added (async routes + async DB access), 0 renamed (depending on (a) vs (b) decision above).

---

## Part D — Review checklist for the applier

When applying these edits, double-check the following:

1. **LOC estimates pending Agent A audit.** My edits bake in "~30,000-35,000 LOC" as the post-pivot total for v2.0. This is the checkpoint's §5 estimate. Agent A's async scope audit may refine this number significantly. After Agent A's report, re-sweep migration.md §1, §14, §18 and implementation-checklist.md §1.1 to replace "30,000-35,000" with Agent A's authoritative count.

2. **Wave 4 entry gate:** The pre-Wave-0 lazy-loading audit is the entry gate for absorbing async into v2.0. If the audit's outcome is "untenable, defer," the applier MUST revert edits 1.16 (§18 rewrite), 1.17-1.18 (Wave 4-5 addition), 7.2 (Blocker 4 rewrite), 7.4 (Wave 0 fix mechanism), and 7.7 (v2.1 scope entry). Original sync-def resolution re-applies to migration.md, deep-audit.md, implementation-checklist.md.

3. **Guard filename consistency:** Decide between `test_architecture_admin_routes_async.py` (my proposal) and `test_architecture_admin_async_signatures.py` (checkpoint §2 guidance) BEFORE applying edits 1.6, 2.3, 6.6, 6.7, 7.3, 7.4. All references must match.

4. **File 8 (CLAUDE.md) may need no edits:** I verified the pivot marker, Invariant #4, and the migration conventions bullets are already correct. Confirm by re-reading the file before concluding no edits needed for File 8. If subsequent edits have been applied between my inspection and the applier's run, re-check.

5. **Edit 4.2 cascades:** The `_load_tenant` sync→async conversion in foundation-modules.md §11.4 has a cascade effect on callers — `is_super_admin`, `_user_has_tenant_access`, `_tenant_has_auth_setup_mode`, `get_current_tenant`, and everything that calls them through `AdminUserDep` / `CurrentTenantDep`. My edit shows only two of the affected functions. The applier should sweep the entire §11.4 for sync DB access and convert uniformly.

6. **Edit 5.4 cascades:** Same for worked-examples.md §4.1 — the `_enumerate_tenants_for_user` and `_lookup_idp_logout_url` helpers need the same async conversion as `_detect_tenant_from_host` and `_load_oidc_flags`. I showed two representative edits; the applier should sweep all four.

7. **Benchmark file rename:** Edit 6.8 renames `tests/benchmark/test_run_in_threadpool_overhead.py` to `tests/benchmark/test_admin_routes_async_vs_sync.py`. If an earlier plan file or checklist references the old filename, it must be updated. Grep the 8 plan files for `test_run_in_threadpool_overhead` after apply.

8. **The `test_architecture_admin_async_signatures.py` guard is referenced in execution-details.md line 512 (assumption #4) and line 752 (Part 3 §A). Both are covered in edits 6.6 and 6.7 — verify no other occurrence exists by grepping after apply.

9. **`run_in_threadpool` remaining valid use cases:** After applying edits, grep every plan file for `run_in_threadpool` to make sure no surviving reference says "use for DB work." Remaining valid mentions:
   - file I/O: `run_in_threadpool(filepath.unlink, ...)` and `run_in_threadpool(tenant_dir.mkdir, ...)` (edit 5.2 preserves these)
   - CPU-bound work
   - sync third-party library calls
   - Benchmark file — verify the new benchmark harness file has zero references to "threadpool overhead" after rewrite

10. **Middleware ordering invariant (Blocker 5) is UNCHANGED** — every edit above preserves the "Approximated BEFORE CSRF" directive. Grep to verify no edit accidentally perturbs §10.2, foundation-modules middleware classes, or middleware-order test descriptions.

11. **AdCP boundary invariant is UNCHANGED** — verify after apply that no edit inadvertently touches any AdCP protocol surface description in adcp-safety.md beyond the two edits (3.1, 3.2) proposed above. Schema, wire format, OpenAPI surface, tool signatures — all stay intact.

12. **`FLASK_SECRET_KEY` dual-read is UNCHANGED** — verify after apply. Not affected by the async pivot.

13. **BDD cross-transport parametrization invariant is UNCHANGED** — not affected by the async pivot but worth confirming no stray edit touches this.

14. **`expire_on_commit=False` is new and load-bearing.** After apply, verify every target-state code example that constructs `async_sessionmaker` passes `expire_on_commit=False`. If any example omits it, add it.

15. **Pre-existing `src/routes/api_v1.py` latent bug mention:** The checkpoint §4 Risk #15 says this pre-existing bug is FIXED as a side effect of Option A. Verify at least one plan file (migration.md §1 executive summary or §2.8) mentions this benefit. My edit 1.1 adds it.

---

## Summary of action

**Total edits across 8 files:** 45 surgical edits (18 + 4 + 2 + 3 + 4 + 8 + 9 + 0 = 48; File 2 edit 2.1 is a large subsection rewrite counted as 1). Plus cascade sweeps in Files 4 and 5.

**Scope:** reverses "sync def admin handlers" prescription across the plan files in favor of "full async SQLAlchemy end-to-end absorbed into v2.0 Waves 4-5." Preserves OAuth/SSE/middleware/AdCP invariants that are orthogonal to the pivot. Adds pre-Wave-0 lazy-loading audit spike as a scope gate.

**Not covered (per instructions):**
- Actual codebase audit for async-conversion LOC scope (Agent A's job)
- 2nd/3rd order risk mitigation matrix (Agent B's job)
- Applying the edits (human or separate agent's job)

**Key decisions delegated to the applier:**
- Guard filename: `test_architecture_admin_routes_async.py` vs `test_architecture_admin_async_signatures.py`
- `factory_boy` adapter option (three options in checkpoint §3)
- LOC estimate refinement (pending Agent A)
- Wave 4/5 merge decision (one wave or two)

**Post-apply verification grep queries:**
```
rg 'sync def\|def list_accounts\|def create_account' .claude/notes/flask-to-fastapi/  # should only show historical "Option C" context
rg 'test_architecture_admin_sync_db_no_async' .claude/notes/flask-to-fastapi/           # should return zero hits
rg 'run_in_threadpool.*DB\|run_in_threadpool.*session\|run_in_threadpool.*sql' .claude/notes/flask-to-fastapi/  # should return zero hits (non-DB uses only)
rg 'defer.*async.*v2\.1\|v2\.1.*async.*defer\|v2\.1 async SQLAlchemy' .claude/notes/flask-to-fastapi/  # should return zero hits
rg 'scoped_session(sessionmaker' .claude/notes/flask-to-fastapi/                         # should only appear in "the bug we're fixing" context
rg 'async_sessionmaker' .claude/notes/flask-to-fastapi/                                  # should have multiple hits in target-state prose
rg 'psycopg2' .claude/notes/flask-to-fastapi/                                            # should only appear in "what we're replacing" context
rg 'asyncpg' .claude/notes/flask-to-fastapi/                                             # should have multiple hits in target-state prose
rg '^### Wave [45]' .claude/notes/flask-to-fastapi/flask-to-fastapi-migration.md         # should show Wave 4 and Wave 5 sections
rg 'expire_on_commit=False' .claude/notes/flask-to-fastapi/                              # should have multiple hits in target-state prose
```
agentId: a8b7929d6401043ab (use SendMessage with to: 'a8b7929d6401043ab' to continue this agent)
<usage>total_tokens: 268836
tool_uses: 38
duration_ms: 772323</usage>
