# Agent E — Ideal-State Gap Audit (2026 FastAPI-Native Design Review)

> **[ARCHIVED REFERENCE — 2026-04-14]** This report is a preserved artifact from the 3-round verification process (Apr 11-14) that produced the v2.0 8-layer execution model. For current implementation guidance, see:
> - `../CLAUDE.md` — mission briefing + 8-layer model
> - `../execution-plan.md` — layer-by-layer work items
> - `../implementation-checklist.md` — per-layer gate checklist
>
> This file is preserved for institutional memory only. Its recommendations have been absorbed into the canonical docs above. Do NOT use this file as a primary reference for implementation decisions.

**Date:** 2026-04-11
**Author:** Claude (Opus 4.6, 1M context)
**Role:** Idioms purist. Compare current plan vs. what a greenfield 2026 FastAPI team would write from scratch.
**Charter constraint:** every proposal is AdCP-wire-safe. Propose nothing that shifts the MCP/A2A/REST/webhook surface.
**Related agents:** A (scope audit), B (risk matrix + lazy-load cookbook), C (plan-file edits), D (AdCP safety verification in parallel).

---

## Section 0 — Executive Summary

### One-paragraph verdict

The current plan is **solid but deeply conservative**. It reads like a careful port of a Flask app to a FastAPI skin rather than a greenfield 2026 FastAPI rewrite. The async pivot (checkpoint doc) fixes the worst scoped_session latent bug and aligns handler signatures with the rest of the codebase, but the underlying architecture still carries four Flask mental habits that a fresh 2026 team would never write: (1) `async with get_db_session() as session:` inline in every handler body instead of `Annotated[AsyncSession, Depends(get_session)]`; (2) ORM objects crossing the UoW boundary into templates (the plan mitigates this in handler #4.5 but not at the design level); (3) module-level singletons created at import time (engine, templates, oauth, CSRF signer) instead of `app.state`-scoped lifespan objects; (4) no structured logging architecture (structlog/logfire-native with task-ID context propagation). Fixing all four is ~2,500 LOC of additional idiom upgrade on top of the async pivot's ~15,000 LOC.

### Current plan grade

**Grade: B+** (solid, production-viable, but not idiomatic 2026 FastAPI)

- **Correctness:** A (async pivot fixes blocker #4 correctly, lazy-load cookbook is thorough)
- **Safety:** A (deep audit + AdCP safety + middleware ordering all correct)
- **Testability:** B (factory-boy fixtures correct, but `app.dependency_overrides` idiom underused)
- **Idiomatic FastAPI:** C+ (the plan describes "Flask-shaped FastAPI", not "what we'd write from scratch")
- **Greenfield purity:** C (engine is module-global, session is context-manager-in-handler, templates see ORM objects)

### Top 5 gaps (in priority order)

1. **Missing `Depends(get_session)` DI pattern** (§2.1). The plan's handlers use `async with get_db_session() as session:` inline. This is the Flask mental model ported verbatim. The idiomatic FastAPI pattern is `session: Annotated[AsyncSession, Depends(get_session)]`, which composes with `app.dependency_overrides` for test isolation, scopes to the request lifecycle automatically, and eliminates ~60% of context-manager boilerplate. **Gap classification: MAJOR.** Propagation: every handler in waves 1-5.

2. **ORM objects cross the UoW boundary into templates** (§2.5, §3.3). CLAUDE.md pattern #4 mandates explicit `model_dump()` overrides for nested serialization, but the plan still shows `_load_tenant()` returning a `dict[str, Any]` manually built field-by-field. The idiomatic 2026 pattern is Pydantic v2 DTO models with `model_config = ConfigDict(from_attributes=True)` auto-materialized from ORM instances, validated at the boundary, and passed to templates by their DTO name. **Gap classification: MAJOR.** This is the LAZY-LOAD risk multiplier — if templates receive DTOs, lazy loads become impossible by construction.

3. **Engine + sessionmaker are module-level singletons** (§2.1, §2.9). The plan's `database_session.py` creates the engine at module import time. Under asyncpg + pytest-asyncio function-scoped event loops, this guarantees event-loop leak bugs (Interaction B in Agent B's risk matrix). The idiomatic 2026 pattern is to create the engine inside `lifespan()`, store on `app.state.engine`, and access via a DI factory. This is a **prerequisite** for proper pytest-asyncio test isolation. **Gap classification: MAJOR** (will break tests under xdist).

4. **No structured logging architecture** (§2.16). The plan uses `logging` directly and references `logfire` as a "v2.1 follow-on". But Flask is being removed in the same PR that introduces `asyncio` everywhere — this is the right time to introduce `structlog` + context-var-scoped task-IDs for every request. Async stack traces are 3x harder to debug without structured logging. **Gap classification: MAJOR.** Cost of adding in v2.0: ~200 LOC. Cost of adding in v2.1: ~400 LOC (retrofits every log call site that v2.0 wrote in the old idiom).

5. **Handler signatures mix `Annotated[]` and bare defaults inconsistently** (§2.4). The plan's worked examples show `x: Annotated[str, Form()]` alongside `x: str = Form(...)` and `x: str = ""`. FastAPI's modern convention (April 2026) is `Annotated[]` everywhere. Mixing creates a ratcheting consistency problem and weakens mypy strict mode. **Gap classification: MINOR-GAP.** Cost to fix: mechanical, <100 LOC across the plan's worked examples.

### Secondary gaps (5 more)

6. **`response_model=` inconsistent** (§2.5). Category-1 routes need it for OpenAPI; admin HTML routes don't. The plan doesn't codify which class uses which. **MINOR-GAP.**
7. **CSRF middleware is 200 LOC body-read manual ASGI** (§2.11). Greenfield 2026 would use `starlette-wtf` or `fastapi-csrf-protect` UNLESS you have specific reasons to roll your own. The plan's rationale ("zero external dep risk, full control") is valid but misses an option: `itsdangerous` signed-cookie validation via a Dep, no body-read at all. **DEBATABLE.**
8. **No `pydantic-settings` `Settings` class** (§2.15). The plan adds `pydantic-settings>=2.7.0` to deps but never uses it — every config value is read via `os.environ.get(...)`. **MINOR-GAP.**
9. **No rate limiting architecture** (§2.17). SSE rate-limit is per-tenant in-memory dict with a lock; a 2026 team would use `slowapi` or at minimum a documented rate-limit dep factory. **DEBATABLE-MINOR.**
10. **No `app.dependency_overrides` test infrastructure** (§2.2). The plan shows `TestClient(app)` and `IntegrationEnv.get_admin_client()` but never demonstrates the `app.dependency_overrides[get_session] = lambda: ...` pattern, which is THE FastAPI test idiom. **MINOR-GAP.**

### Net scope impact of closing all gaps

**Estimated LOC to close all gaps: +2,400 LOC on top of the async pivot.**

| Change | Est. LOC | Status |
|---|---|---|
| `get_session()` DI factory + handler conversion | +1,200 | Migrate every handler from `async with get_db_session()` to `Depends(get_session)` |
| Engine via lifespan + `app.state.engine` | +150 | `src/app.py` + `src/core/database/database_session.py` rewrites |
| Pydantic DTO layer + repository returns | +500 | `src/admin/dtos/` new package |
| Structured logging (structlog) | +250 | `src/core/logging.py` new file + replace `logging.getLogger` call sites |
| Settings class (pydantic-settings) | +200 | `src/core/settings.py` new file + replace env.get call sites |
| New structural guards (10 new) | +300 | `tests/unit/test_architecture_*.py` |
| `response_model=` audit + additions | +50 | Ratchet across all routers |
| `app.dependency_overrides` test examples | -100 | Deletes boilerplate, net savings |

**Subtracted:** ~450 LOC of boilerplate that the idiomatic patterns eliminate. **Net: +1,950 LOC** for a materially better end state.

### Is this worth doing in v2.0?

**Yes, for items 1-5.** They are prerequisites for the rest of the waves working correctly:
- Gap 1 (`Depends(get_session)`) blocks gap 2 (DTO boundary) blocks gap 3 (engine lifespan) blocks proper test isolation under xdist (Risk Interaction B in Agent B).
- Items 6-10 are nice-to-haves; defer to v2.1.

---

## Section 1 — Per-category analysis

Each category has: **Ideal pattern → Canonical example → Current plan proposal → Gap classification → Recommended plan-file edit → Dependency impact**.

---

### Category 1 — Engine + session factory architecture

#### Ideal pattern

A 2026 greenfield FastAPI team creates the `AsyncEngine` inside the app's `lifespan` function, attaches it to `app.state.engine`, and provides a `get_session()` dependency that yields an `AsyncSession` per request. The engine creation is **deferred** to avoid the asyncpg event-loop binding problem (asyncpg's connection pool is bound to the event loop that created it; creating the engine at module import time means the pool is owned by whatever loop runs the first import — which under pytest-asyncio is almost never the test's actual event loop).

#### Canonical example

```python
# src/core/database/engine.py
"""AsyncEngine lifecycle — lifespan-scoped, never module-global."""
from __future__ import annotations
from contextlib import asynccontextmanager
from typing import AsyncIterator, TYPE_CHECKING

from sqlalchemy.ext.asyncio import (
    AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine,
)

if TYPE_CHECKING:
    from fastapi import FastAPI


def _build_async_url(sync_url: str) -> str:
    """Rewrite postgresql:// → postgresql+asyncpg:// (idempotent)."""
    if sync_url.startswith("postgresql+asyncpg://"):
        return sync_url
    if sync_url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + sync_url[len("postgresql://"):]
    raise ValueError(f"Unsupported DATABASE_URL scheme: {sync_url}")


def make_engine(database_url: str, *, pool_size: int = 20, max_overflow: int = 10) -> AsyncEngine:
    """Factory — callable from lifespan AND from test fixtures.

    Pool sizing: default 20+10 matches benchmark tuning from Risk #6. xdist
    workers override via PYTEST_XDIST_WORKER_COUNT-aware fixture (Interaction B).
    """
    return create_async_engine(
        _build_async_url(database_url),
        echo=False,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_pre_ping=True,       # detect stale connections (Fly.io network blips)
        pool_recycle=3600,        # recycle every hour (beat Fly's 2h idle kill)
    )


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,   # MANDATORY for async (cookbook §8)
        autoflush=False,          # explicit flush, no surprises
        autobegin=True,           # SQLA 2.0 default, still worth being explicit
    )


@asynccontextmanager
async def database_lifespan(app: "FastAPI") -> AsyncIterator[None]:
    """Create engine + sessionmaker on startup, dispose on shutdown.

    Store on `app.state` so DI factories can read them off the current request.
    """
    import os
    engine = make_engine(os.environ["DATABASE_URL"])
    app.state.db_engine = engine
    app.state.db_sessionmaker = make_sessionmaker(engine)
    try:
        yield
    finally:
        await engine.dispose()
```

```python
# src/core/database/deps.py
"""Session DI factory — the ONLY place handlers get a session from."""
from typing import Annotated, AsyncIterator

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Per-request session. Commits on normal exit, rolls back on exception.

    Access pattern in handlers:
        session: Annotated[AsyncSession, Depends(get_session)]

    NOT a nested context manager inside handler bodies. The DI layer owns
    session lifecycle; handlers only own business logic.
    """
    sessionmaker = request.app.state.db_sessionmaker
    async with sessionmaker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


SessionDep = Annotated[AsyncSession, Depends(get_session)]
```

```python
# src/app.py (foundation)
from contextlib import asynccontextmanager
from fastapi import FastAPI
from src.core.database.engine import database_lifespan
from src.core.main import mcp_lifespan  # existing MCP lifespan


@asynccontextmanager
async def combined_lifespan(app: FastAPI):
    async with database_lifespan(app):
        async with mcp_lifespan(app):
            yield


app = FastAPI(lifespan=combined_lifespan)
```

#### Current plan proposal

From `async-pivot-checkpoint.md` §3:

```python
_engine = create_async_engine(
    connection_string.replace("postgresql://", "postgresql+asyncpg://"),
    echo=False,
    pool_size=20,
    max_overflow=10,
)
SessionLocal = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)

@asynccontextmanager
async def get_db_session() -> AsyncIterator[AsyncSession]:
    session = SessionLocal()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()
```

This is a **module-level** engine creation. Under pytest-asyncio with `asyncio_mode = "strict"` and function-scoped event loops, the engine is bound to the event loop of whichever test imported `database_session.py` first. That loop is closed when the test ends; subsequent tests running on fresh loops see `RuntimeError: Event loop is closed` on asyncpg connection acquisition. This is a **well-known footgun** — see [sqlalchemy#6409](https://github.com/sqlalchemy/sqlalchemy/discussions/6409) and Agent B Interaction B.

#### Gap classification: **MAJOR-GAP**

The plan is aware of the pool-sizing issue (Risk #6 in Agent B) but does NOT address the root architectural problem: module-level engine creation. This is a **correctness** issue, not a style issue.

#### Recommended plan-file edit

**File:** `.claude/notes/flask-to-fastapi/async-pivot-checkpoint.md`
**Section:** §3 "Database layer"
**Before:** The 8-line `_engine = create_async_engine(...)` block shown above.
**After:** Replace with the three-file architecture (`engine.py`, `deps.py`, lifespan composition in `src/app.py`) shown in the Canonical Example. Add a sidebar note:

```markdown
> **Why not module-level engine?** Under pytest-asyncio function-scoped event
> loops, a module-level `create_async_engine()` binds its asyncpg pool to the
> event loop of the first test that imports the module. Subsequent tests on
> fresh loops see `RuntimeError: Event loop is closed`. The lifespan-scoped
> pattern sidesteps this entirely. See Agent B Interaction B.
```

**File:** `.claude/notes/flask-to-fastapi/flask-to-fastapi-foundation-modules.md`
**Section:** new §11.0 "Database engine lifecycle" (insert BEFORE §11.1)
**Add:** Full `src/core/database/engine.py`, `src/core/database/deps.py`, and lifespan composition in `src/app.py` as described above.

#### Dependency impact

None (already using SQLAlchemy 2.0 + asyncpg post-pivot). Worth adding `pool_pre_ping=True` and `pool_recycle=3600` as documented defaults.

---

### Category 2 — Dependency injection for sessions

#### Ideal pattern

Every handler that needs a DB session takes `session: Annotated[AsyncSession, Depends(get_session)]` as a parameter. The DI layer owns lifecycle. Handlers never construct a session and never use `async with` for session management.

Why this matters:
- **Test overrides become trivial:** `app.dependency_overrides[get_session] = lambda: fake_session` in every test that needs a stub DB. No `monkeypatch.setattr("module.get_db_session", ...)` patching.
- **Scoped to request lifecycle automatically:** the session is created when the request arrives at the handler and disposed when the response has been sent. Exceptions auto-rollback.
- **Composable with other deps:** a `UserRepository(session: SessionDep)` dep factory lets you chain repositories as handler params.
- **Eliminates boilerplate:** no `async with` nesting in every handler body.

#### Canonical example

```python
# src/admin/routers/accounts.py
from typing import Annotated, Sequence

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.admin.deps.auth import CurrentTenantDep
from src.admin.templating import render
from src.admin.dtos import AccountDTO  # Pydantic DTO (see Category 3)
from src.core.database.deps import SessionDep
from src.core.database.repositories.accounts import AccountRepository


router = APIRouter(
    tags=["admin-accounts"],
    redirect_slashes=True,
    include_in_schema=False,
)


# Dep factory — the repository is itself a Dep
async def get_account_repo(session: SessionDep) -> AccountRepository:
    return AccountRepository(session)

AccountRepoDep = Annotated[AccountRepository, Depends(get_account_repo)]


@router.get(
    "/tenant/{tenant_id}/accounts",
    name="admin_accounts_list_accounts",
    response_class=HTMLResponse,
)
async def list_accounts(
    tenant_id: str,
    request: Request,
    tenant: CurrentTenantDep,
    accounts_repo: AccountRepoDep,
    status: Annotated[str | None, "Query()"] = None,
) -> HTMLResponse:
    """Handler is 100% business logic. No session management. No context manager."""
    accounts: Sequence[AccountDTO] = await accounts_repo.list_dtos(tenant_id, status=status)
    return render(request, "accounts_list.html", {
        "tenant_id": tenant_id,
        "tenant": tenant,
        "accounts": accounts,   # DTOs, NOT ORM objects — see Category 3
        "status_filter": status,
    })
```

Repository signature with DTO at the boundary:

```python
# src/core/database/repositories/accounts.py
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from src.admin.dtos import AccountDTO
from src.core.database.models import Account


class AccountRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_dtos(self, tenant_id: str, *, status: str | None = None) -> list[AccountDTO]:
        """Returns DTOs, not ORM objects. Templates never see lazy loads."""
        stmt = (
            select(Account)
            .filter_by(tenant_id=tenant_id)
            .options(selectinload(Account.primary_contact))  # eager-load what DTO needs
        )
        if status:
            stmt = stmt.filter_by(status=status)
        result = await self.session.execute(stmt)
        orm_accounts = result.scalars().all()
        return [AccountDTO.model_validate(a) for a in orm_accounts]
```

#### Current plan proposal

From `flask-to-fastapi-migration.md` §13.1 (post-pivot):

```python
async def list_accounts(
    tenant_id: str,
    request: Request,
    tenant: CurrentTenantDep,
    status: Annotated[str | None, Query()] = None,
) -> HTMLResponse:
    async with AccountUoW(tenant_id) as uow:
        accounts = await uow.accounts.list_all(status=status)
    return render(request, "accounts_list.html", {...})
```

The handler constructs a UoW via `async with` in its body. The UoW in turn constructs a session. There is no DI for sessions anywhere in the plan — every handler has `async with` boilerplate.

From `flask-to-fastapi-foundation-modules.md` §11.4 Edit 4.2 (proposed by Agent C):

```python
async def _load_tenant(tenant_id: str) -> dict[str, Any]:
    async with get_db_session() as db:
        tenant = (await db.execute(
            select(Tenant).filter_by(tenant_id=tenant_id)
        )).scalars().first()
        ...
```

Same pattern — `async with` inline. And this is in a **dep function** (`get_current_tenant`), meaning deps now chain: `get_admin_user` (dep) → `get_current_tenant` (dep) → `_load_tenant` (helper with `async with`). The session is constructed inside the dep helper. If two deps both call `_load_tenant(same_tenant_id)`, they open two separate sessions instead of sharing one.

#### Gap classification: **MAJOR-GAP**

The entire plan is missing `Depends(get_session)`. Not one handler, not one dep, not one worked example uses the pattern. This is the #1 idiomatic FastAPI pattern — its absence is the biggest "written by someone who learned Flask first" tell in the plan.

**Concrete consequences:**
1. **Test overrides are hard.** The plan has to patch `get_db_session()` via `monkeypatch.setattr`, which doesn't compose with `TestClient`.
2. **Two deps can't share a session.** `get_admin_user` and `get_current_tenant` each open their own session. Three DB queries take three separate connections.
3. **Handler bodies have boilerplate.** Every handler has `async with UoW` or `async with get_db_session()` nesting.

#### Recommended plan-file edit

**File:** `.claude/notes/flask-to-fastapi/flask-to-fastapi-foundation-modules.md`
**Section:** new §11.0 "Session DI" (insert BEFORE §11.1)
**Add:** The `get_session` dep factory + `SessionDep = Annotated[AsyncSession, Depends(get_session)]` from the canonical example.

**File:** `.claude/notes/flask-to-fastapi/flask-to-fastapi-migration.md`
**Section:** §13.1 `list_accounts` example
**Before:** `async with AccountUoW(tenant_id) as uow: accounts = await uow.accounts.list_all(status=status)`
**After:** `accounts: list[AccountDTO] = await accounts_repo.list_dtos(tenant_id, status=status)`
where `accounts_repo: AccountRepoDep` is in the handler signature.

**File:** `.claude/notes/flask-to-fastapi/flask-to-fastapi-foundation-modules.md`
**Section:** §11.4 `_load_tenant`, `_user_has_tenant_access`, `_tenant_has_auth_setup_mode`
**Before:** Standalone functions that do `async with get_db_session()`.
**After:** Convert into `TenantRepository` methods that take `session` as `__init__` arg. Deps become:

```python
async def get_current_tenant(
    request: Request,
    user: AdminUserDep,
    tenant_id: str,
    tenants: Annotated[TenantRepository, Depends(get_tenant_repo)],
    users: Annotated[UserRepository, Depends(get_user_repo)],
) -> TenantDTO:
    if user.role == "super_admin":
        tenant = await tenants.get_by_id(tenant_id)
        if not tenant:
            raise HTTPException(404, "Tenant not found")
        return tenant
    ...
```

#### Dependency impact

None. This is pure architectural rearrangement.

---

### Category 3 — Repository + UoW pattern under async

#### Ideal pattern

Under FastAPI's Depends-based architecture, the **Unit of Work pattern is redundant**. UoW exists in clean architecture to bundle a transaction across multiple repository operations. FastAPI's request-scoped session already does this: every repository dep takes the same `session: SessionDep`, which is committed once at request-end by the `get_session` DI factory. The UoW abstraction adds a layer of indirection that buys nothing.

Greenfield 2026 architecture:

1. **Session is the transaction boundary.** DI creates one session per request; the request IS the unit of work.
2. **Repositories are stateless methods on an object that holds `self.session`.** No `__enter__`/`__aenter__`.
3. **Multiple repositories share the request's session automatically** because `Depends` caches the result of `get_session` within a single request.
4. **Cross-repository transactions are implicit** — they all write to the same session, which the DI layer commits atomically.
5. **Repositories return DTOs** (Pydantic models with `from_attributes=True`), never ORM instances, to prevent lazy-loads downstream.
6. **Every repository method docstring declares which relationships are eager-loaded.** Enforced by a structural guard that parses docstrings + queries + compares.

#### Canonical example

```python
# src/core/database/repositories/base.py
"""Repository base — stateless methods over an injected session."""
from typing import TypeVar, Generic

from sqlalchemy.ext.asyncio import AsyncSession

T = TypeVar("T")


class BaseRepository(Generic[T]):
    """All repositories inherit this. Session is injected, not constructed."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # No __aenter__/__aexit__. No commit/rollback. DI owns lifecycle.
```

```python
# src/core/database/repositories/account.py
from typing import Sequence
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.admin.dtos import AccountDTO, AccountWithContactDTO
from src.core.database.models import Account
from src.core.database.repositories.base import BaseRepository


class AccountRepository(BaseRepository[Account]):
    """Account data access.

    All read methods return DTOs. All write methods operate on ORM instances
    internally but accept/return DTOs at the public surface.

    Eager-load contract (asserted by test_architecture_repository_eager_loads.py):
        list_dtos: no relationships
        get_with_contact: Account.primary_contact (selectinload)
        get_with_pricing_options: Account.pricing_options (selectinload)
    """

    async def list_dtos(
        self,
        tenant_id: str,
        *,
        status: str | None = None,
    ) -> Sequence[AccountDTO]:
        """Returns [AccountDTO]. No relationships loaded — DTO is self-contained."""
        stmt = select(Account).filter_by(tenant_id=tenant_id)
        if status:
            stmt = stmt.filter_by(status=status)
        result = await self.session.execute(stmt)
        return [AccountDTO.model_validate(a) for a in result.scalars().all()]

    async def get_with_contact(
        self,
        account_id: str,
        tenant_id: str,
    ) -> AccountWithContactDTO | None:
        """Returns AccountWithContactDTO. Eager-loads `primary_contact`."""
        stmt = (
            select(Account)
            .filter_by(account_id=account_id, tenant_id=tenant_id)
            .options(selectinload(Account.primary_contact))
        )
        result = await self.session.execute(stmt)
        orm = result.scalars().first()
        return AccountWithContactDTO.model_validate(orm) if orm else None

    async def create_from_dto(self, dto: AccountCreateDTO, tenant_id: str) -> AccountDTO:
        """Write side — takes a DTO, returns a DTO, doesn't leak ORM."""
        account = Account(
            account_id=dto.account_id,
            tenant_id=tenant_id,
            name=dto.name,
            status="pending_approval",
            # ...
        )
        self.session.add(account)
        await self.session.flush()  # materialize PKs + server_defaults
        return AccountDTO.model_validate(account)
```

Handler usage:

```python
@router.post("/tenant/{tenant_id}/accounts")
async def create_account(
    tenant_id: str,
    accounts: AccountRepoDep,
    users: UserRepoDep,
    audit: AuditLogRepoDep,
    payload: AccountCreateDTO,
) -> AccountDTO:
    # Three repositories, one session, one transaction — FastAPI's DI manages it.
    account = await accounts.create_from_dto(payload, tenant_id)
    await users.grant_access(account.owner_email, tenant_id)
    await audit.log_event("account_created", tenant_id=tenant_id, resource_id=account.account_id)
    # No manual commit — get_session commits on successful handler return.
    return account
```

#### Current plan proposal

From `async-pivot-checkpoint.md` §3 and `flask-to-fastapi-migration.md` §13:

```python
class AccountUoW:
    def __init__(self, tenant_id: str):
        self.tenant_id = tenant_id
        self._session: AsyncSession | None = None
        self.accounts: AccountRepository | None = None

    async def __aenter__(self):
        self._session = SessionLocal()
        self.accounts = AccountRepository(self._session)
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if exc_type:
            await self._session.rollback()
        else:
            await self._session.commit()
        await self._session.close()
```

And usage:

```python
async with AccountUoW(tenant_id) as uow:
    accounts = await uow.accounts.list_all(status=status)
```

The UoW class is a reimplementation of `get_session()`'s lifecycle logic with an extra layer of naming. It bundles a single repository. The `tenant_id` baked into the UoW constructor is suspicious — why does the UoW know about tenant_id? (Answer: because Flask's `@require_tenant_access` put tenant_id on `flask.g` and Flask-style code inferred that UoW should have a tenant identity. This is porting the Flask mental model.)

Additionally: the UoW pattern in the plan returns ORM instances, not DTOs. This is the LAZY-LOAD footgun from cookbook Pattern 9: "close session AFTER template renders, not before" — which the plan acknowledges but doesn't architecturally enforce.

#### Gap classification: **MAJOR-GAP**

UoW is a Flask-era clean-architecture pattern that **does not fit FastAPI's DI model**. It re-implements functionality FastAPI already provides for free. Every plan handler has to remember to use `async with UoW(...)` correctly, versus FastAPI-native where the session is injected as a parameter and you can't forget.

#### Recommended plan-file edit

**File:** `.claude/notes/flask-to-fastapi/async-pivot-checkpoint.md`
**Section:** §3 "Repository pattern" and "UoW pattern"
**Before:** The `AccountUoW` class shown above.
**After:** Delete UoW entirely. Replace with:

```markdown
### Repository pattern (no UoW)

FastAPI's DI layer IS the unit of work. One session per request, shared across
all repositories that depend on `SessionDep`. Transactions commit on normal
handler return, roll back on exception.

Repositories take `session: AsyncSession` in `__init__` and expose async methods
that return **DTOs** (Pydantic models), never ORM instances. This prevents lazy
loads from leaking across the session boundary.

class AccountRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_dtos(self, tenant_id: str, *, status: str | None = None) -> list[AccountDTO]:
        ...

Handler usage:

    @router.get(...)
    async def list_accounts(
        accounts: AccountRepoDep,  # Annotated[AccountRepository, Depends(get_account_repo)]
        tenant_id: str,
        status: str | None = None,
    ) -> HTMLResponse:
        dtos = await accounts.list_dtos(tenant_id, status=status)
        return render(request, "accounts_list.html", {"accounts": dtos})
```

**File:** `.claude/notes/flask-to-fastapi/flask-to-fastapi-migration.md`
**Section:** Every §13 worked example that uses `async with AccountUoW(...)`
**Before:** UoW usage.
**After:** `accounts: AccountRepoDep` in signature, `await accounts.list_dtos(...)` in body.

**File:** `.claude/notes/flask-to-fastapi/flask-to-fastapi-worked-examples.md`
**Section:** §4.5 `add_product` — the service function `create_product_for_tenant(cmd)`
**Observation:** this function IS a UoW-shaped service (one function, one session, one transaction). In the new pattern, it becomes a `ProductRepository.create_from_command(cmd)` method, still pure, still testable, but the session is injected from the handler.

#### Dependency impact

None. Removes a layer, doesn't add one.

---

### Category 4 — Handler signatures

#### Ideal pattern

Every FastAPI handler in a 2026 greenfield app uses `Annotated[T, ...]` exclusively for FastAPI-decorated parameters. Path/Query/Body/Form/File/Header/Cookie metadata lives inside `Annotated[]` rather than as default values. This is the modern convention as of FastAPI 0.95+, officially the preferred style as of 0.100+, and April 2026 mypy strict mode assumes it.

Additionally:
- **Return type annotation mandatory.** Either `-> HTMLResponse`, `-> AccountDTO`, `-> RedirectResponse`, or `-> None`.
- **`response_model=` on decorator for JSON APIs** (category-1 routes). Not used for HTML routes (admin).
- **`status_code=` explicit for POST/PUT/DELETE/PATCH.** Default 200 is silly for create endpoints — 201.
- **Request body models use `BaseModel`**, not `Form(...)`, for JSON content-type. Form data uses explicit `Form(...)` params.
- **`Request` only when you genuinely need `request.state.*`** (templates, CSRF token, request.app). Otherwise don't accept it — it forces TestClient fixtures to construct real requests.

#### Canonical example

```python
@router.post(
    "/tenant/{tenant_id}/accounts",
    name="admin_accounts_create_account",
    status_code=201,                              # explicit; create → 201
    response_model=AccountDTO,                    # OpenAPI + response validation
    dependencies=[Depends(audit_action("create_account"))],
)
async def create_account(
    tenant_id: Annotated[str, Path(description="Tenant ID")],
    payload: Annotated[AccountCreateRequest, Body()],
    accounts: AccountRepoDep,
    tenant: CurrentTenantDep,
) -> AccountDTO:
    """Create a new account.

    Returns the created DTO with assigned PK and server_default fields populated.
    """
    return await accounts.create_from_request(payload, tenant_id)


@router.get(
    "/tenant/{tenant_id}/accounts",
    name="admin_accounts_list_accounts",
    response_class=HTMLResponse,                  # HTML — not response_model
)
async def list_accounts(
    request: Request,                             # needed for templating
    tenant_id: Annotated[str, Path()],
    tenant: CurrentTenantDep,
    accounts: AccountRepoDep,
    status: Annotated[str | None, Query(description="Filter by status")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    per_page: Annotated[int, Query(ge=1, le=100)] = 25,
) -> HTMLResponse:
    dtos = await accounts.list_dtos(tenant_id, status=status, limit=per_page, offset=(page-1)*per_page)
    return render(request, "accounts_list.html", {
        "accounts": dtos, "page": page, "per_page": per_page,
    })
```

#### Current plan proposal

Mixed, inconsistent. Examples from `flask-to-fastapi-worked-examples.md`:

```python
# Example 4.5 — some Annotated, some defaults
async def add_product(
    tenant_id: str,                                           # ← no Path()
    request: Request,                                          # ← no annotation
    tenant: CurrentTenantDep,
    user: AdminUserDep,
    name: Annotated[str, Form()],                              # ← Annotated
    description: Annotated[str, Form()] = "",                  # ← Annotated + default
    countries: Annotated[list[str] | None, Form()] = None,
    ...
)
```

```python
# Example 13.3 — body model good, but path param untyped
async def change_status(
    tenant_id: str, account_id: str,                           # ← no Path()
    payload: StatusChangeRequest,                              # ← no Body()
    tenant: CurrentTenantDep, request: Request,
) -> StatusChangeResponse:
```

```python
# Example 13.1 — missing response_model for JSON
@router.post("/.../change_status",
    name="accounts_change_status",
    response_model=StatusChangeResponse,                       # ← good here
)
```

The plan gets SOME of this right but inconsistently. Path params aren't `Annotated[str, Path()]`. `payload` isn't `Annotated[X, Body()]`. `status_code=201` is missing from every POST example.

#### Gap classification: **MINOR-GAP**

This is a style drift, not a correctness issue. But it weakens mypy strict mode (you can't enforce "every handler parameter must have `Annotated[]`") and creates copy-paste confusion when implementers pick which style to follow.

#### Recommended plan-file edit

**File:** `.claude/notes/flask-to-fastapi/flask-to-fastapi-migration.md`
**Section:** new §11.12 "Handler signature convention" (insert at end of §11)
**Add:**

```markdown
### 11.12 Handler signature convention (greenfield 2026 idiom)

Every FastAPI handler parameter uses `Annotated[T, Metadata]`:

| Param source | Idiom |
|---|---|
| Path parameter | `tenant_id: Annotated[str, Path()]` |
| Query parameter | `status: Annotated[str \| None, Query()] = None` |
| Form field | `name: Annotated[str, Form()]` |
| Request body (JSON) | `payload: Annotated[AccountCreateRequest, Body()]` |
| File upload | `favicon: Annotated[UploadFile, File()]` |
| Dep | `tenant: CurrentTenantDep` (which is itself `Annotated[]`) |

Return types MANDATORY. `-> HTMLResponse`, `-> AccountDTO`, `-> RedirectResponse`,
or `-> None`. No untyped returns.

`response_model=` on every JSON-returning route. `response_class=HTMLResponse`
on every HTML route. Never both.

`status_code=` explicit on every POST/PUT/DELETE/PATCH. 201 for create, 204 for
delete, 200 for update-in-place. Don't let the default 200 propagate silently.

`Request` parameter ONLY when the body reads `request.state.*`, `request.session`,
or `request.app`. Otherwise don't accept it — it complicates testing.
```

**File:** Every §13 and §4 worked example
**Before:** `tenant_id: str`
**After:** `tenant_id: Annotated[str, Path()]`

**New structural guard:** `test_architecture_handlers_use_annotated.py`
Scans `src/admin/routers/*.py` via AST. For every async function decorated by `@router.get/post/put/delete`, asserts:
1. Every parameter has a type annotation.
2. Path/Query/Body/Form/File/Header/Cookie params use `Annotated[T, ...]`, not `default = X()`.
3. Return type annotation exists.

#### Dependency impact

None.

---

### Category 5 — Response models + Pydantic v2

#### Ideal pattern

2026 greenfield FastAPI uses Pydantic v2 with `model_config = ConfigDict(from_attributes=True, strict=True)` for DTOs that wrap ORM objects. DTOs live in a dedicated `dtos/` package. Templates receive DTOs, NOT ORM instances. Repositories return DTOs at their public surface.

CLAUDE.md Pattern #4 says "parent models must override `model_dump()` to serialize nested children" — this is CORRECT for AdCP library-schema extensions (the MCP wire format depends on it), but for **internal** DTOs, Pydantic v2's default nested serialization is fine as long as every nested type is itself a Pydantic model (not an ORM object or dict).

Key Pydantic v2 features a 2026 team uses by default:

1. **`from_attributes=True`** — v2 replacement for `orm_mode=True`. Enables `AccountDTO.model_validate(orm_instance)`.
2. **`strict=True`** — reject type coercion surprises (string "5" becoming int 5).
3. **`extra="forbid"`** in development/CI, `extra="ignore"` in production (per CLAUDE.md Pattern #7).
4. **`field_serializer` / `model_serializer`** for custom serialization (e.g., decimals to strings with `Decimal(...).quantize(...)` semantics).
5. **`computed_field`** for derived fields (e.g., `full_name` from `first_name + last_name`).
6. **`Field(...)` validators** via `Annotated[str, Field(min_length=1, max_length=255)]` instead of standalone validators where possible.
7. **Discriminated unions** via `Annotated[Union[A, B], Field(discriminator="type")]` for polymorphic shapes like "email OR webhook" contact info.
8. **`TypeAdapter` for list serialization** at the route boundary: `adapter = TypeAdapter(list[AccountDTO])`.
9. **OpenAPI `response_model=`** on every route — Pydantic v2 emits JSON Schema automatically.

#### Canonical example

```python
# src/admin/dtos/account.py
from datetime import datetime
from decimal import Decimal
from typing import Literal, Annotated

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_serializer


class AccountContactDTO(BaseModel):
    """Nested DTO — referenced by AccountWithContactDTO."""
    model_config = ConfigDict(from_attributes=True, strict=True, frozen=True)

    email: Annotated[str, Field(min_length=1, max_length=255)]
    name: str
    role: Literal["primary", "billing", "technical"]


class AccountDTO(BaseModel):
    """Flat account DTO — no relationships loaded. Used in list views."""
    model_config = ConfigDict(from_attributes=True, strict=True, frozen=True)

    account_id: str
    tenant_id: str
    name: Annotated[str, Field(min_length=1, max_length=255)]
    status: Literal["active", "pending_approval", "rejected", "suspended", "closed"]
    created_at: datetime
    monthly_budget: Decimal | None

    @field_serializer("monthly_budget")
    def serialize_budget(self, value: Decimal | None) -> str | None:
        return str(value.quantize(Decimal("0.01"))) if value is not None else None

    @computed_field  # type: ignore[misc]
    @property
    def is_active(self) -> bool:
        return self.status == "active"


class AccountWithContactDTO(AccountDTO):
    """Eager-loaded variant — includes primary_contact."""
    primary_contact: AccountContactDTO | None = None


class AccountCreateRequest(BaseModel):
    """Request body for POST /accounts. Explicitly NOT extending AccountDTO."""
    model_config = ConfigDict(extra="forbid", strict=True)

    account_id: Annotated[str, Field(pattern=r"^acc_[a-z0-9]{8}$")]
    name: Annotated[str, Field(min_length=1, max_length=255)]
    monthly_budget: Decimal | None = None
    primary_contact_email: Annotated[str, Field(pattern=r"^\S+@\S+\.\S+$")] | None = None
```

Repository returns DTOs:

```python
async def list_dtos(self, tenant_id: str, *, status: str | None = None) -> list[AccountDTO]:
    result = await self.session.execute(
        select(Account).filter_by(tenant_id=tenant_id)
    )
    return [AccountDTO.model_validate(a) for a in result.scalars().all()]
```

Handler returns DTOs via `response_model=`:

```python
@router.get("/tenant/{tenant_id}/accounts/{account_id}", response_model=AccountWithContactDTO)
async def get_account(
    tenant_id: Annotated[str, Path()],
    account_id: Annotated[str, Path()],
    accounts: AccountRepoDep,
) -> AccountWithContactDTO:
    account = await accounts.get_with_contact(account_id, tenant_id)
    if account is None:
        raise HTTPException(404, "Account not found")
    return account
```

#### Current plan proposal

From `flask-to-fastapi-foundation-modules.md` §11.4:

```python
def _load_tenant(tenant_id: str) -> dict[str, Any]:
    """Load tenant row as a dict.

    Why dict (not ORM object): the route handler must be able to pass the
    tenant into template contexts, which are serialized into cookies by
    SessionMiddleware if mis-stored. Returning a plain dict prevents
    accidental ORM object serialization attempts.
    """
    with get_db_session() as db:
        tenant = db.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
        ...
        return {
            "tenant_id": tenant.tenant_id,
            "name": tenant.name,
            "subdomain": tenant.subdomain,
            "is_active": tenant.is_active,
            "billing_plan": tenant.billing_plan,
            "ad_server": tenant.ad_server,
            "approval_mode": getattr(tenant, "approval_mode", None),
            "auth_setup_mode": getattr(tenant, "auth_setup_mode", False),
        }
```

This is **manual dict construction**. It's:
1. Verbose (9 lines to copy 8 fields)
2. Untyped (templates can access `tenant["non_existent"]` and get `None` silently)
3. Error-prone (forget to add a new field when the model changes)
4. Not reusable (every `_load_tenant`-equivalent function in the codebase reinvents the shape)

It's also the exact thing `AccountDTO.model_validate(orm_instance)` solves in one line with full type safety.

`flask-to-fastapi-migration.md` §13.3 gets response_model right for the single category-1 JSON endpoint:

```python
class StatusChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    status: str
```

But there's no DTO package, no `from_attributes=True` usage anywhere, no repository returns DTOs, no template receives DTOs. Every other example in the plan has ORM instances (or hand-built dicts) crossing the session boundary.

#### Gap classification: **MAJOR-GAP**

The plan is aware of Pydantic v2 (cites ConfigDict, uses BaseModel for one request) but hasn't adopted DTOs as an **architectural layer**. Combined with the missing `Depends(get_session)` from Category 2, this means:

- ORM instances will reach templates
- Lazy loads will happen at template-render time
- They'll fail with `MissingGreenlet`
- Engineers will debug for hours
- Eventually add `selectinload()` calls ad-hoc, without a structural guard

This is Agent B's Risk #1 (lazy loading) realized at scale. The DTO boundary is the **architectural prevention** for Risk #1.

#### Recommended plan-file edit

**File:** `.claude/notes/flask-to-fastapi/flask-to-fastapi-foundation-modules.md`
**Section:** new §11.0.5 "DTO layer" (after §11.0 engine, before §11.1 templating)
**Add:**

```markdown
### 11.0.5 DTO layer — Pydantic v2 wrappers over ORM models

**File tree:**
```
src/admin/dtos/
  __init__.py
  account.py
  tenant.py
  user.py
  product.py
  media_buy.py
  ... (one per major entity)
```

**Every DTO:**
```python
class AccountDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True, strict=True, frozen=True)
    account_id: str
    tenant_id: str
    name: Annotated[str, Field(min_length=1, max_length=255)]
    ...
```

**`frozen=True`** prevents downstream mutation (templates can't "update" a DTO).

**`from_attributes=True`** enables `AccountDTO.model_validate(orm_instance)`.

**`strict=True`** prevents silent type coercion that might hide data model drift.

**Eager-load contract:** DTOs with nested relationships MUST be returned only
from repository methods that eager-loaded the relationships. Enforced by
`test_architecture_repository_eager_loads.py`.

**The three CRITICAL benefits of this layer:**

1. **Lazy-load impossibility:** if a template has a DTO, no lazy load is
   possible — DTOs are plain Pydantic objects, not ORM instances.

2. **Type safety in templates:** templates access DTO fields with
   auto-completion (if your IDE supports it) and mypy-checkable code.

3. **AdCP boundary preservation:** admin DTOs live in `src/admin/dtos/`,
   separate from `src/core/schemas/` (AdCP library schemas). Changes to
   admin DTOs NEVER touch the AdCP wire format.
```

**File:** `.claude/notes/flask-to-fastapi/flask-to-fastapi-foundation-modules.md`
**Section:** §11.4 `_load_tenant` (and sibling helpers)
**Before:** Manual dict construction.
**After:** Return `TenantDTO.model_validate(tenant)` from a `TenantRepository.get_by_id()` method that takes an injected `AsyncSession`.

**New structural guard:** `test_architecture_templates_receive_dtos.py`
Scans every handler in `src/admin/routers/*.py`. For every `render(request, "tpl", context)` call, asserts every value in `context` is either:
- a primitive (str, int, bool, None, etc.)
- a Pydantic BaseModel instance
- a list/dict/tuple containing only primitives or BaseModels
- the `request` object itself
NEVER an ORM model instance.

#### Dependency impact

Already using `pydantic>=2.10.0` per plan. No new deps.

---

### Category 6 — Error handling idioms

#### Ideal pattern

Greenfield 2026 FastAPI error handling:

1. **`@app.exception_handler(AdCPError)`** — existing, must be Accept-aware per Blocker 3.
2. **`@app.exception_handler(RequestValidationError)`** — customize 422 body shape. For HTML routes, render a form with field errors. For JSON routes, return a structured error dict.
3. **`@app.exception_handler(HTTPException)`** — Accept-aware handler that renders HTML 4xx/5xx pages for admin browsers and JSON for APIs.
4. **`@app.exception_handler(Exception)`** — catch-all with sanitized message, hides internal details in production, emits structured log with traceback.
5. **`HTTPException` ONLY for transport errors** (404, 403, 401). Business logic raises `AdCPError` subclasses or admin-specific typed exceptions (`AdminRedirect`, `AdminAccessDenied`).
6. **Form validation errors re-render the form with error messages** — NOT redirect-with-flash. Redirect loses POST data; re-render preserves it. The plan gets this right in §4.5 but not in §4.1.
7. **Pydantic v2 `ValidationError` → 422 with structured `.errors()` list** — FastAPI does this automatically; override only to customize the body shape.

#### Canonical example

```python
# src/app.py (error handlers)
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, HTMLResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.core.errors import AdCPError
from src.admin.deps.auth import AdminRedirect, AdminAccessDenied
from src.admin.templating import render


def _wants_html(request: Request) -> bool:
    """Is this an admin browser?"""
    accept = request.headers.get("accept", "")
    return request.url.path.startswith("/admin/") and "text/html" in accept


@app.exception_handler(AdCPError)
async def adcp_error_handler(request: Request, exc: AdCPError):
    """Accept-aware: HTML for admin browsers, JSON for APIs. (Blocker 3)"""
    if _wants_html(request):
        return render(request, "error.html", {
            "error": exc.message,
            "status_code": exc.status_code,
        }, status_code=exc.status_code)
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.message, "type": exc.__class__.__name__},
    )


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """Accept-aware 404/403/401 etc."""
    if _wants_html(request):
        return render(request, f"{exc.status_code}.html", {
            "detail": exc.detail,
        }, status_code=exc.status_code)
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    """For admin HTML, re-render the form with errors. For JSON, return structured errors."""
    if _wants_html(request):
        # Extract form-level errors for re-rendering
        field_errors = {}
        for err in exc.errors():
            if err["loc"][0] == "body" and len(err["loc"]) > 1:
                field_errors[err["loc"][1]] = err["msg"]
        # Re-render the form with errors — handler can check request.state.form_errors
        request.state.form_errors = field_errors
        # This is tricky because we don't know which template to render; in
        # practice the per-form handler catches RequestValidationError locally
        # via a dep. See Pattern 7 below.
        return JSONResponse(status_code=422, content={"errors": exc.errors()})
    return JSONResponse(
        status_code=422,
        content={
            "detail": "Validation failed",
            "errors": [
                {"field": ".".join(str(x) for x in e["loc"][1:]), "message": e["msg"]}
                for e in exc.errors()
            ],
        },
    )


@app.exception_handler(AdminRedirect)
async def admin_redirect_handler(request: Request, exc: AdminRedirect):
    from starlette.responses import RedirectResponse
    from urllib.parse import quote
    url = f"{exc.to}?next={quote(exc.next_url, safe='')}" if exc.next_url else exc.to
    return RedirectResponse(url=url, status_code=303)


@app.exception_handler(AdminAccessDenied)
async def admin_access_denied_handler(request: Request, exc: AdminAccessDenied):
    return render(request, "403.html", {"message": exc.message}, status_code=403)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Catch-all. Sanitizes in production, full detail in dev."""
    import os
    import logging
    logger = logging.getLogger(__name__)
    logger.exception("Unhandled exception in %s %s", request.method, request.url.path)
    if os.environ.get("ENVIRONMENT") == "production":
        msg = "Internal server error"
    else:
        msg = f"{type(exc).__name__}: {exc}"
    if _wants_html(request):
        return render(request, "500.html", {"message": msg}, status_code=500)
    return JSONResponse(status_code=500, content={"detail": msg})
```

#### Current plan proposal

`flask-to-fastapi-deep-audit.md` §1 Blocker 3 correctly identifies the AdCPError Accept-aware issue. `flask-to-fastapi-foundation-modules.md` §11.10 shows:

```python
@app.exception_handler(AdminRedirect)
async def admin_redirect_handler(request: Request, exc: AdminRedirect):
    ...
```

And `flask-to-fastapi-migration.md` §13.4 shows a `legacy_error_shape_handler` for backward compat. **What's MISSING:**

- No `RequestValidationError` handler example
- No catch-all `@app.exception_handler(Exception)` example
- Form validation errors in §4.5 `add_product` use `flash(request, ...) + _rerender_with_error()` at the HANDLER level — manual, repeated in every handler body. There's no middleware or dep that makes this automatic.
- No DTO for error responses (`ErrorResponse` model with `detail: str, type: str, errors: list[FieldError]`).

#### Gap classification: **MINOR-GAP** (functionality OK, but not codified)

#### Recommended plan-file edit

**File:** `.claude/notes/flask-to-fastapi/flask-to-fastapi-foundation-modules.md`
**Section:** new §11.11 "Error handling" (after §11.10)
**Add:** The full exception handler set from the canonical example, including:
1. `AdCPError` (Accept-aware, per Blocker 3)
2. `HTTPException` (Accept-aware)
3. `RequestValidationError` (structured errors body)
4. `AdminRedirect` (303 redirect)
5. `AdminAccessDenied` (templated 403)
6. `Exception` catch-all

**New structural guard:** `test_architecture_exception_handlers_complete.py`
Scans `src/app.py` for `@app.exception_handler` decorators. Asserts all 6 are registered.

#### Dependency impact

None.

---

### Category 7 — Template rendering idioms

#### Ideal pattern

2026 greenfield FastAPI Jinja2 rendering:

1. **`Jinja2Templates(directory=..., env=...)`** — standard.
2. **`enable_async=True`** on the Jinja environment. Enables `{% for x in await coro %}` in templates. **DEBATABLE** — the plan doesn't need it because repositories return DTOs (lists), not coroutines. Skip unless you have a specific need.
3. **`url_for` auto-registered** via `@pass_context` (confirmed working in §1 of checkpoint).
4. **Template globals** are request-independent ONLY. Any per-request data must go through the context dict. The `_url_for` override uses `@pass_context` which DOES give it request access — correct pattern.
5. **Macros** for repeated fragments (flash messages, form fields, pagination).
6. **i18n via babel** — out of scope for v2.0 per plan, leave it.
7. **Template caching:** auto-reload in dev, cached in production. `APP_ENV` controls.
8. **StrictUndefined vs Undefined:** the plan chose `Undefined` for v2.0 to avoid breaking legacy templates. Correct decision.
9. **DTOs in templates only.** No ORM instances. This is the key idiom upgrade (see Category 5).
10. **`request.url_for` returns a `URL` object**, not a string. The plan wraps in `str(...)` where necessary — good.

#### Canonical example

```python
# src/admin/templating.py (idiomatic 2026)
from pathlib import Path
from typing import Any
import logging

from fastapi.templating import Jinja2Templates
from jinja2 import pass_context
from starlette.datastructures import URL
from starlette.requests import Request
from starlette.routing import NoMatchFound

from src.admin.flash import get_flashed_messages
from src.core.settings import get_settings  # pydantic-settings instance

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).parent / "templates"

_templates = Jinja2Templates(
    directory=str(_TEMPLATE_DIR),
    auto_reload=get_settings().app_env == "dev",  # cached in prod
    # enable_async=False — not needed; DTOs are sync objects
)

# Custom filters
_templates.env.filters["from_json"] = _from_json
_templates.env.filters["markdown"] = _markdown
_templates.env.filters["format_decimal"] = _format_decimal
_templates.env.filters["format_datetime"] = _format_datetime


# url_for override (as before)
@pass_context
def _url_for(context: dict[str, Any], name: str, /, **path_params: Any) -> URL:
    request: Request = context["request"]
    try:
        return request.url_for(name, **path_params)
    except NoMatchFound:
        template_name = getattr(context, "name", "<unknown>")
        logger.error(
            "NoMatchFound in template %s: url_for(%r, **%r)",
            template_name, name, path_params,
        )
        raise


_templates.env.globals["url_for"] = _url_for
_templates.env.globals["get_flashed_messages"] = get_flashed_messages
_templates.env.globals["settings"] = get_settings  # callable, request-free


def render(
    request: Request,
    name: str,
    context: dict[str, Any] | None = None,
    *,
    status_code: int = 200,
    headers: dict[str, str] | None = None,
) -> "_TemplateResponse":
    base: dict[str, Any] = {
        "request": request,
        "csrf_token": getattr(request.state, "csrf_token", ""),
        "flash_messages": get_flashed_messages(request, with_categories=True),
    }
    if context:
        base.update(context)
    return _templates.TemplateResponse(
        request=request, name=name, context=base,
        status_code=status_code, headers=headers,
    )
```

Note the key differences:

1. **`auto_reload` bound to `APP_ENV`**, not hardcoded — the plan doesn't specify.
2. **`settings` injected as a callable global** — every template can access `settings().sales_agent_domain` instead of threading it through every handler context.
3. **`flash_messages` eagerly resolved in `render()`** — no `{{ get_flashed_messages(request) }}` calls in templates, just `{% for c, m in flash_messages %}`. Simpler templates.

#### Current plan proposal

`flask-to-fastapi-foundation-modules.md` §11.1 — mostly good, but:

1. No `auto_reload` toggle based on environment.
2. `get_flashed_messages` is registered as a Jinja global **trampoline** — templates call `{{ get_flashed_messages(request, with_categories=true) }}`, forcing every template to know about the `request` parameter. The cleaner pattern is to call it once in `render()` and pass the resolved list.
3. `settings` not injected at all — every handler has to pass `support_email`, `sales_agent_domain` individually. Every handler's context dict has boilerplate.
4. `flash_messages` not auto-eagerly resolved — templates still do the trampoline dance.

#### Gap classification: **MINOR-GAP**

Correctness is fine; ergonomics can be improved.

#### Recommended plan-file edit

**File:** `.claude/notes/flask-to-fastapi/flask-to-fastapi-foundation-modules.md`
**Section:** §11.1 `templating.py`
**Add:**
- `auto_reload=settings.app_env == "dev"` to `Jinja2Templates()` constructor
- Eager-resolve `flash_messages` in `render()` base context
- Register `settings` as a Jinja global callable
- Delete the `get_flashed_messages` trampoline from `env.globals` (it's no longer needed — templates just use `{% for c, m in flash_messages %}`)

#### Dependency impact

Requires the `pydantic-settings` Settings class from Category 15 below.

---

### Category 8 — Middleware stack

#### Ideal pattern

1. **Every middleware is pure ASGI (`async def __call__(scope, receive, send)`)** unless it has a compelling reason to use `BaseHTTPMiddleware`. Starlette #1729 is a hard constraint.
2. **Middleware order is documented and tested.** A structural guard asserts the registration order.
3. **`request.state.*` for per-request data.** NOT ContextVars (except for logging, see Category 16).
4. **Middleware receive/send replays are correct.** The CSRF middleware's body-read-then-replay pattern is the classic footgun — the plan gets this right, include a test.
5. **Middleware count is minimal.** Too many middlewares multiply request overhead. Bundle where possible.
6. **No middleware does DB work.** Middleware runs on every request (including static assets). DB work belongs in deps.

#### Canonical example

```python
# src/app.py — middleware registration (LIFO: last = outermost)

app = FastAPI(lifespan=combined_lifespan)

# Inner → outer (LIFO order matters)
app.add_middleware(CORSMiddleware, allow_origins=cors_origins, allow_credentials=True)
app.add_middleware(RestCompatMiddleware)
app.add_middleware(CSRFOriginMiddleware, signer=get_settings().session_secret, salt="adcp-csrf-v1")
app.add_middleware(SessionMiddleware, **session_middleware_kwargs())
app.add_middleware(UnifiedAuthMiddleware)
app.add_middleware(ExternalDomainRedirectMiddleware)  # must run BEFORE CSRF (Blocker 5)
app.add_middleware(FlyHeadersMiddleware)              # outermost — runs first, normalizes headers
app.add_middleware(RequestIDMiddleware)               # outermost, creates X-Request-ID for logs
```

With runtime flow:

```
RequestIDMiddleware → FlyHeaders → ExternalDomainRedirect → UnifiedAuth →
    Session → CSRF → RestCompat → CORS → handler
```

#### Current plan proposal

From `flask-to-fastapi-foundation-modules.md` §11.10 and `flask-to-fastapi-migration.md`:

```python
app.add_middleware(CORSMiddleware, allow_origins=...)      # innermost
app.add_middleware(RestCompatMiddleware)
app.add_middleware(CSRFOriginMiddleware)                          # new, admin
app.add_middleware(SessionMiddleware, **session_kwargs)     # new, admin
app.add_middleware(UnifiedAuthMiddleware)
app.add_middleware(ExternalDomainRedirectMiddleware)        # new, admin
app.add_middleware(FlyHeadersMiddleware)                    # new, outermost
```

This is **correct** per Blocker 5 (Approximated BEFORE CSRF). Missing:

1. **No RequestIDMiddleware** — the plan has no structured-log request correlation. Every log line should include the request ID for debugging async interleaving. CRITICAL for debugging under async.
2. **No test for middleware order.** The plan has test assertions for CSRF and External Domain individually, but no test that asserts the registration order.

#### Gap classification: **MINOR-GAP**

#### Recommended plan-file edit

**File:** `.claude/notes/flask-to-fastapi/flask-to-fastapi-foundation-modules.md`
**Section:** new §11.9.5 "RequestIDMiddleware"
**Add:**

```python
# src/admin/middleware/request_id.py
"""Generates and propagates X-Request-ID for every request.

Read by the structured logger to include in every log line emitted during
handling of this request. Critical for debugging async-interleaved requests.
"""
import uuid
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send


class RequestIDMiddleware:
    def __init__(self, app: ASGIApp, header_name: str = "x-request-id") -> None:
        self.app = app
        self.header_name = header_name.lower().encode("latin-1")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Reuse upstream request ID if present, otherwise generate
        incoming: str | None = None
        for name, value in scope["headers"]:
            if name == self.header_name:
                incoming = value.decode("latin-1")
                break

        request_id = incoming or uuid.uuid4().hex
        scope["state"] = scope.get("state", {}) | {"request_id": request_id}

        # Propagate via structlog context-var (see Category 16)
        from src.core.logging import bind_request_id
        with bind_request_id(request_id):
            async def send_with_header(message: Message) -> None:
                if message["type"] == "http.response.start":
                    headers = list(message.get("headers", []))
                    headers.append((self.header_name, request_id.encode("latin-1")))
                    message["headers"] = headers
                await send(message)
            await self.app(scope, receive, send_with_header)
```

**New structural guard:** `test_architecture_middleware_order.py`
Asserts the middleware registration order matches the documented runtime order. Prevents accidental reshuffling that would break Blocker 5.

#### Dependency impact

None. `uuid` is stdlib, `structlog` is added in Category 16.

---

### Category 9 — Lifespan management

#### Ideal pattern

**ONE** top-level lifespan per app. Composed via `contextlib.AsyncExitStack` or manual nested `async with`. Lifespan creates engine, starts schedulers, warms caches, validates config. Shutdown drains tasks gracefully.

Key 2026 patterns:

1. **Single `lifespan=combined_lifespan` passed to `FastAPI()`** — no side effects at import time.
2. **Engine created in lifespan**, not at module import. (Category 1)
3. **Schedulers started via `asyncio.create_task(...)`** in lifespan, cancelled on shutdown via `asyncio.shield()` + timeout.
4. **Startup assertions:** config sanity, pool warmup, migration check. Fails fast if anything is wrong.
5. **Startup log alive-tick for every scheduler** — so operators see "delivery_webhook_scheduler started" in logs and can monitor that it's still running via a `/health/schedulers` endpoint.
6. **Graceful shutdown timeout.** Uvicorn has `--timeout-graceful-shutdown 10`. Schedulers must drain within that window OR be force-killed.
7. **`combine_lifespans` helper** for multiple independent lifespans (db + mcp + admin).

#### Canonical example

```python
# src/app.py
from contextlib import AsyncExitStack, asynccontextmanager
import asyncio
import logging
from typing import AsyncIterator

from fastapi import FastAPI

from src.core.database.engine import database_lifespan
from src.core.main import mcp_lifespan  # existing
from src.core.schedulers import (
    delivery_webhook_scheduler,
    media_buy_status_scheduler,
)

logger = logging.getLogger(__name__)
SHUTDOWN_TIMEOUT_SECONDS = 10


@asynccontextmanager
async def scheduler_lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Start schedulers on startup, gracefully cancel on shutdown."""
    scheduler_tasks: list[asyncio.Task] = []

    # Start each scheduler as a background task
    scheduler_tasks.append(asyncio.create_task(
        delivery_webhook_scheduler(app.state.db_sessionmaker),
        name="delivery_webhook_scheduler",
    ))
    scheduler_tasks.append(asyncio.create_task(
        media_buy_status_scheduler(app.state.db_sessionmaker),
        name="media_buy_status_scheduler",
    ))

    # Alive-tick log so operators can grep for startup confirmation
    for task in scheduler_tasks:
        logger.info("Scheduler started: %s", task.get_name())

    app.state.scheduler_tasks = scheduler_tasks

    try:
        yield
    finally:
        logger.info("Cancelling %d scheduler tasks...", len(scheduler_tasks))
        for task in scheduler_tasks:
            task.cancel()
        # Wait for all to finish with a bounded timeout
        try:
            await asyncio.wait_for(
                asyncio.gather(*scheduler_tasks, return_exceptions=True),
                timeout=SHUTDOWN_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            logger.error("Schedulers did not drain within %ds, forcing shutdown", SHUTDOWN_TIMEOUT_SECONDS)


@asynccontextmanager
async def combined_lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Composes all sub-lifespans via AsyncExitStack.

    Startup order: database → MCP → schedulers (schedulers need db, mcp needs db).
    Shutdown reverse: schedulers → MCP → database.
    """
    async with AsyncExitStack() as stack:
        await stack.enter_async_context(database_lifespan(app))
        await stack.enter_async_context(mcp_lifespan(app))
        await stack.enter_async_context(scheduler_lifespan(app))
        # Post-startup config sanity
        _assert_config_complete(app)
        yield


def _assert_config_complete(app: FastAPI) -> None:
    """Fail-fast config check. Called after all sub-lifespans are up."""
    from src.core.settings import get_settings
    settings = get_settings()
    if settings.app_env == "production":
        assert settings.session_secret, "SESSION_SECRET required in production"
        assert settings.google_client_id, "GOOGLE_CLIENT_ID required in production"
    # Warm up DB connection
    # ... etc


app = FastAPI(lifespan=combined_lifespan)
```

#### Current plan proposal

From `flask-to-fastapi-migration.md` and `async-pivot-checkpoint.md` §3:

The plan mentions `combine_lifespans(app_lifespan, mcp_app.lifespan)` at `src/app.py:68` as an existing pattern and says "schedulers already use [`asyncio.create_task`]". But:

1. **No explicit example of the combined lifespan for the v2.0 architecture** (only the existing pattern mentioned in passing).
2. **No graceful shutdown timeout discussed.** Agent B Risk #26 (FastMCP + FastAPI lifespan composition deadlock on shutdown) flags this.
3. **No startup config assertions** — the plan says "fail-fast hard KeyError at startup" for SESSION_SECRET but doesn't codify WHERE.
4. **No alive-tick log assertions** — so operators can't grep for "scheduler started".
5. **No `app.state.db_engine` / `app.state.db_sessionmaker`** — the plan's engine is module-global, so lifespan has nothing to attach.

Agent C edit 7.8 proposes a pre-Wave-0 lazy-loading spike but doesn't propose lifespan hardening.

#### Gap classification: **MAJOR-GAP** (correctness + operability)

#### Recommended plan-file edit

**File:** `.claude/notes/flask-to-fastapi/flask-to-fastapi-foundation-modules.md`
**Section:** new §11.0.1 "Lifespan composition" (right after §11.0 engine)
**Add:** The full `combined_lifespan` + `scheduler_lifespan` + `_assert_config_complete` from the canonical example. Reference Agent B Risk #26 as the motivator.

**File:** `.claude/notes/flask-to-fastapi/flask-to-fastapi-execution-details.md`
**Section:** Wave 0 acceptance criteria
**Add:** "Lifespan integration test that (a) starts the app, (b) verifies `app.state.db_engine` is set, (c) verifies each scheduler task is running, (d) shuts down cleanly in <10s."

#### Dependency impact

None. `AsyncExitStack` is stdlib.

---

### Category 10 — SSE handlers

#### Ideal pattern

Already well-covered in the plan's §4.4 worked example (using `sse-starlette.EventSourceResponse`). Two additions:

1. **Session per tick, not per stream.** Under pool pressure, a long-lived SSE stream holding a connection for minutes starves other requests. Open a new session for each DB query, close immediately. Agent B Risk #29 flags this.
2. **Pool size bump for SSE.** Every SSE client holds at least one pool slot during each `get_recent_activities()` call. With 10 concurrent streams polling every 2 seconds, that's spikes of 10 connections every 2 seconds.

#### Canonical example

```python
@router.get("/tenant/{tenant_id}/events", name="activity_stream_events")
async def activity_events(
    tenant_id: Annotated[str, Path()],
    request: Request,
    tenant: CurrentTenantDep,
    _rate: Annotated[None, Depends(rate_limit_sse)],
    # NOTE: NO `session: SessionDep` — we create per-tick sessions instead
    activity_repo_factory: Annotated[
        ActivityRepositoryFactory, Depends(get_activity_repo_factory)
    ],
) -> EventSourceResponse:
    if len(tenant_id) > 50:
        raise HTTPException(status_code=400, detail="Invalid tenant ID")

    async def event_generator() -> AsyncGenerator[dict, None]:
        try:
            # Initial burst — open session, fetch, close
            async with activity_repo_factory() as repo:
                recent = await repo.list_recent_dtos(tenant_id, limit=50)
            for activity in reversed(recent):
                yield {"event": "activity", "data": activity.model_dump_json()}

            last_check = datetime.now(UTC)
            while True:
                if await request.is_disconnected():
                    break

                # Per-tick session — don't hold a pool slot between ticks
                try:
                    async with activity_repo_factory() as repo:
                        new_activities = await repo.list_recent_dtos(
                            tenant_id,
                            since=last_check - timedelta(seconds=1),
                            limit=10,
                        )
                except Exception:
                    logger.exception("SSE poll failed for tenant %s", tenant_id)
                    yield {"event": "error", "data": json.dumps({"type": "error"})}
                    await asyncio.sleep(5)
                    continue

                for activity in reversed(new_activities):
                    yield {"event": "activity", "data": activity.model_dump_json()}

                last_check = datetime.now(UTC)
                await asyncio.sleep(2)
        finally:
            await release_sse_slot(tenant_id)

    return EventSourceResponse(event_generator(), ping=15, headers={"X-Accel-Buffering": "no"})
```

The `activity_repo_factory` is a callable that returns an async context manager — opens a fresh session, yields a repository, closes the session. This lets the generator open/close repositories on every tick without inheriting a long-lived request session.

#### Current plan proposal

§4.4 shows `run_in_threadpool(get_recent_activities, ...)` for sync DB access. Under the async pivot, this becomes `await get_recent_activities(...)`. But the plan does NOT address the "session per tick" concern — it implicitly uses one session per stream duration, which creates pool pressure under load.

#### Gap classification: **MINOR-GAP**

#### Recommended plan-file edit

**File:** `.claude/notes/flask-to-fastapi/flask-to-fastapi-worked-examples.md`
**Section:** §4.4 `activity_events` event_generator
**Add:** Session-per-tick pattern, with an `activity_repo_factory` dep that returns an async context manager. Explain that long-lived SSE streams should NOT hold a pool slot for the entire stream duration.

#### Dependency impact

None.

---

### Category 11 — CSRF (roll-your-own Double Submit Cookie)

#### Ideal pattern

The plan's roll-your-own CSRF (~200 LOC) is **reasonable** but heavy. A leaner 2026 alternative: use `itsdangerous.URLSafeTimedSerializer` with a Dep, no middleware body-read.

**Alternative design:**
1. A Dep (`get_csrf_token(request)`) extracts the token from the cookie, validates signature + timestamp, returns bool.
2. Every POST handler that needs CSRF declares `_csrf: Annotated[bool, Depends(require_csrf_token)]` as a parameter. The dep reads `request.form.get("csrf_token")` OR `request.headers.get("x-csrf-token")`.
3. A middleware still sets the cookie on GET responses, but the BODY-READ-AND-REPLAY dance goes away — deps read the form after FastAPI has already parsed it.
4. 50 LOC instead of 200 LOC.

**Trade-off:** the dep approach requires every POST handler to opt in. The middleware approach is default-on. Security-wise, default-on is better (no developer can forget to add the dep). **Recommendation: keep the middleware approach as-is.** The plan's design is correct; it just deserves more testing.

What the plan **should add**:
1. A fuzzing test (boundary body sizes, multipart edge cases, truncated uploads).
2. A Playwright test (real browser, real cookie, real form POST).
3. A test that asserts the middleware does NOT interfere with FastAPI's native form parsing (i.e., `await request.form()` in a handler still works after the middleware's replay).

#### Gap classification: **IDEAL** (design is fine)

No plan-file edit needed.

#### Dependency impact

None.

---

### Category 12 — Auth patterns

#### Ideal pattern

Already well-covered: `ResolvedIdentity` via `Depends(resolve_identity)`, `Depends(require_admin)` / `Depends(require_super_admin)` for admin routes, OAuth Authlib `starlette_client` for Google, OIDC per-tenant config.

One gap: **decorator-style gating vs dependency-style**. The plan uses `dependencies=[Depends(require_admin)]` on routes, which is correct. But `flask-to-fastapi-migration.md` §13 doesn't consistently use it — some examples have `user: SuperAdminDep` as a parameter (which also works), others use `dependencies=[...]`.

**Convention:** use `Annotated[AdminUser, Depends(...)]` parameter when the handler needs to read user data. Use `dependencies=[Depends(...)]` when the handler only needs the auth check as a side effect.

#### Current plan proposal

Mostly correct. Inconsistent parameter vs. dependencies pattern.

#### Gap classification: **IDEAL** (minor inconsistency)

#### Recommended plan-file edit

**File:** `.claude/notes/flask-to-fastapi/flask-to-fastapi-migration.md`
**Section:** new §11.4.1 "Parameter vs. dependencies=[] convention"
**Add:**

```markdown
### 11.4.1 Parameter vs. dependencies=[] convention

Two ways to attach a dep to a handler:

1. **As a parameter** when the handler reads the dep's return value:
   ```python
   async def my_handler(user: AdminUserDep): ...
   ```

2. **As `dependencies=[...]`** when the handler only needs the dep as a gate:
   ```python
   @router.post("/admin/foo", dependencies=[Depends(require_super_admin)])
   async def my_handler(): ...
   ```

Both work. Rule of thumb:
- If the handler body references the user's email, role, or identity: parameter.
- If the handler only needs the auth check to succeed: `dependencies=[...]`.
- `audit_action()` is always `dependencies=[...]` (side effect only).
```

#### Dependency impact

None.

---

### Category 13 — Background tasks

#### Ideal pattern

1. **FastAPI `BackgroundTasks`** for post-response work scoped to a single request (e.g., "send welcome email after account creation").
2. **`asyncio.create_task(...)`** for lifespan-scoped work (schedulers, watchdogs).
3. **No Celery/RQ/Dramatiq** — single-worker v2.0. (Already correct.)

Plan is already idiomatic here. No changes.

#### Gap classification: **IDEAL**

#### Dependency impact

None.

---

### Category 14 — Test harness under async

#### Ideal pattern

1. **`httpx.AsyncClient(transport=ASGITransport(app=app))`** for in-process testing — no real HTTP.
2. **`@pytest.mark.asyncio`** with `asyncio_mode = "strict"`.
3. **Loop scope: `function`** (fresh per test) — combined with **lifespan-scoped engine from Category 1**, this prevents the event-loop leak.
4. **Session fixture: `async def integration_db() -> AsyncIterator[AsyncSession]`**.
5. **Factory-boy with custom `AsyncSQLAlchemyModelFactory`** wrapper (Agent B option B).
6. **`pytest-xdist` with per-worker DB** — agent-db.sh already does this.
7. **`app.dependency_overrides`** for injecting stubs — no monkeypatching.
8. **DTO factories** (Polyfactory or hand-written) for creating Pydantic models in tests.

#### Canonical example

```python
# tests/conftest.py
import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator

import pytest
import pytest_asyncio
from httpx import AsyncClient
from httpx._transports.asgi import ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession

from src.app import app
from src.core.database.engine import make_engine, make_sessionmaker
from src.core.database.deps import get_session


@pytest_asyncio.fixture(scope="function")
async def engine():
    """Per-test engine, tied to the current event loop."""
    import os
    eng = make_engine(os.environ["DATABASE_URL"])
    try:
        yield eng
    finally:
        await eng.dispose()


@pytest_asyncio.fixture(scope="function")
async def session_factory(engine):
    return make_sessionmaker(engine)


@pytest_asyncio.fixture(scope="function")
async def session(session_factory) -> AsyncIterator[AsyncSession]:
    """Per-test session, auto-rolled back."""
    async with session_factory() as s:
        yield s
        await s.rollback()


@pytest_asyncio.fixture(scope="function")
async def client(session) -> AsyncIterator[AsyncClient]:
    """ASGI test client with session override."""
    async def _override():
        yield session

    app.dependency_overrides[get_session] = _override
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as c:
            yield c
    finally:
        app.dependency_overrides.clear()
```

Test:

```python
import pytest

@pytest.mark.asyncio
async def test_list_accounts(client, session):
    # Factory creates test data through the injected session
    await AccountFactory.create(tenant_id="t1", name="Test Account", session=session)

    r = await client.get(
        "/admin/tenant/t1/accounts",
        cookies={"adcp_session": _signed({"user": "alice@example.com"})},
    )
    assert r.status_code == 200
    assert "Test Account" in r.text
```

#### Current plan proposal

`async-pivot-checkpoint.md` §3 discusses `IntegrationEnv.__aenter__` but:

1. **Uses `TestClient(app)` (sync)**, not `AsyncClient(transport=ASGITransport(app=app))`. TestClient runs in a thread and spawns its own loop — this is the OLD pattern and has bugs under async lifespan.
2. **No `app.dependency_overrides[get_session]` example** — the plan says to "bind sessions to factories" but doesn't use the DI override mechanism.
3. **Factory-boy adapter is TBD** — Agent B says "evaluate all three". A 2026 team picks option B (custom `AsyncSQLAlchemyModelFactory` wrapper).

#### Gap classification: **MAJOR-GAP**

This is a testability issue, not a correctness issue. But it blocks clean test isolation for the entire v2.0 test suite.

#### Recommended plan-file edit

**File:** `.claude/notes/flask-to-fastapi/flask-to-fastapi-foundation-modules.md`
**Section:** new §11.13 "Test harness fixtures" (after all foundation modules)
**Add:** The full `conftest.py` from the canonical example. Explicitly recommend `httpx.AsyncClient + ASGITransport`, NOT `TestClient`.

**File:** `.claude/notes/flask-to-fastapi/async-pivot-checkpoint.md`
**Section:** §3 "Test harness"
**Before:** "All tests using `IntegrationEnv` need `async with env:` instead of `with env:`"
**After:** "All tests use `httpx.AsyncClient(transport=ASGITransport(app=app))` with `app.dependency_overrides[get_session] = lambda: session`. The `IntegrationEnv` harness is retained as a factory fixture wrapper only."

**New structural guard:** `test_architecture_tests_use_async_client.py`
Scans `tests/integration/` and `tests/admin/` for `TestClient(app)` imports. Asserts none remain (post-migration). FIXME-allowlisted during the migration itself.

#### Dependency impact

`httpx>=0.28.1` already in deps. `pytest-asyncio>=1.1.0` already in dev deps. No new additions.

---

### Category 15 — Pydantic Settings

#### Ideal pattern

Greenfield 2026 config: one `Settings` class, loaded once via `get_settings()` with `@lru_cache`, stored on `app.state.settings`, accessed via Dep or directly (for non-request code). Type-safe throughout. `SecretStr` for sensitive fields.

#### Canonical example

```python
# src/core/settings.py
"""Pydantic-settings Settings class — one source of truth for config."""
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """App-wide settings. Loaded from env, .env, or secret store."""
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Environment
    app_env: Literal["dev", "staging", "production"] = "dev"

    # Database
    database_url: str = Field(..., alias="DATABASE_URL")

    # Sessions & CSRF
    session_secret: SecretStr = Field(..., alias="SESSION_SECRET", min_length=32)

    # OAuth
    google_client_id: str | None = Field(None, alias="GOOGLE_CLIENT_ID")
    google_client_secret: SecretStr | None = Field(None, alias="GOOGLE_CLIENT_SECRET")

    # Super admin
    super_admin_emails: list[str] = Field(default_factory=list, alias="SUPER_ADMIN_EMAILS")
    super_admin_domains: list[str] = Field(default_factory=list, alias="SUPER_ADMIN_DOMAINS")

    # Test mode
    adcp_auth_test_mode: bool = Field(False, alias="ADCP_AUTH_TEST_MODE")

    # Feature flags
    single_tenant_mode: bool = Field(False, alias="ADCP_SINGLE_TENANT_MODE")

    # Domain
    sales_agent_domain: str = "sales-agent.example.com"
    support_email: str = "help@example.com"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
```

Handler usage:

```python
from typing import Annotated
from fastapi import Depends

from src.core.settings import Settings, get_settings

SettingsDep = Annotated[Settings, Depends(get_settings)]


@router.get("/admin/config")
async def show_config(settings: SettingsDep):
    return {"env": settings.app_env, "domain": settings.sales_agent_domain}
```

Non-request usage:

```python
from src.core.settings import get_settings

def some_utility():
    if get_settings().app_env == "production":
        ...
```

#### Current plan proposal

The plan adds `pydantic-settings>=2.7.0` to deps (plan §15) but **never uses it**. Every config value is `os.environ.get("FOO", "")` inline in module code. The CSRF module reads `SESSION_SECRET` directly from env. The oauth module reads `GOOGLE_CLIENT_ID` directly from env.

#### Gap classification: **MAJOR-GAP** (added a dep, didn't use it)

#### Recommended plan-file edit

**File:** `.claude/notes/flask-to-fastapi/flask-to-fastapi-foundation-modules.md`
**Section:** new §11.0.2 "Pydantic Settings" (right after §11.0.1 Lifespan)
**Add:** The full `src/core/settings.py` from the canonical example. Replace every `os.environ.get(...)` in every foundation module with `get_settings().field`.

**File:** Every `os.environ.get(...)` call site in §11.2 (sessions), §11.6 (csrf), §11.7 (oauth), §11.8 (external_domain)
**Before:** `os.environ.get("SESSION_SECRET", "")`
**After:** `get_settings().session_secret.get_secret_value()`

**New structural guard:** `test_architecture_no_direct_env_access.py`
Scans `src/admin/` and `src/core/` for `os.environ.get` or `os.environ[` calls. Allowlist `src/core/settings.py` (the only file that reads env). Prevents config drift.

#### Dependency impact

`pydantic-settings>=2.7.0` already in plan. No new additions.

---

### Category 16 — Logging

#### Ideal pattern

Greenfield 2026: **structlog + contextvar request-ID propagation**. Logs are structured (JSON in prod, human in dev). Every log line has a `request_id`, `task_id`, `tenant_id` (if known), `user_email` (if authenticated).

#### Canonical example

```python
# src/core/logging.py
"""Structlog configuration — JSON in prod, human in dev."""
from contextlib import contextmanager
from contextvars import ContextVar
import logging
import sys
from typing import Iterator

import structlog
from structlog.contextvars import bind_contextvars, unbind_contextvars

from src.core.settings import get_settings


def configure_logging() -> None:
    """Called once from lifespan."""
    settings = get_settings()

    # stdlib → structlog bridge
    logging.basicConfig(
        level=logging.INFO if settings.app_env != "dev" else logging.DEBUG,
        stream=sys.stderr,
        format="%(message)s",
    )

    processors = [
        structlog.contextvars.merge_contextvars,      # adds bound vars
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if settings.app_env == "dev":
        processors.append(structlog.dev.ConsoleRenderer(colors=True))
    else:
        processors.append(structlog.processors.JSONRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


@contextmanager
def bind_request_id(request_id: str) -> Iterator[None]:
    """Bind request_id to the current contextvar context."""
    bind_contextvars(request_id=request_id)
    try:
        yield
    finally:
        unbind_contextvars("request_id")


@contextmanager
def bind_tenant(tenant_id: str | None) -> Iterator[None]:
    if tenant_id:
        bind_contextvars(tenant_id=tenant_id)
    try:
        yield
    finally:
        if tenant_id:
            unbind_contextvars("tenant_id")


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
```

Handler usage:

```python
from src.core.logging import get_logger

logger = get_logger(__name__)


@router.post("/tenant/{tenant_id}/accounts")
async def create_account(
    tenant_id: Annotated[str, Path()],
    accounts: AccountRepoDep,
    payload: AccountCreateRequest,
    user: AdminUserDep,
):
    logger.info("create_account.request", tenant_id=tenant_id, user=user.email)
    try:
        account = await accounts.create_from_request(payload, tenant_id)
    except DuplicateAccountError:
        logger.warning("create_account.duplicate", tenant_id=tenant_id, account_id=payload.account_id)
        raise HTTPException(409, "Account already exists")
    logger.info("create_account.success", account_id=account.account_id)
    return account
```

Every log line automatically includes `request_id` (from `RequestIDMiddleware` binding the contextvar). Under async, contextvars propagate correctly across `await` boundaries — this is the key async-debuggability win.

#### Current plan proposal

Plan uses `import logging; logger = logging.getLogger(__name__)` throughout. `logfire` is mentioned as a "v2.1 follow-on" opportunity. Plain `logging` does NOT propagate contextvars automatically under async — debugging async interleavings is hard.

#### Gap classification: **MAJOR-GAP**

Delaying structured logging to v2.1 means v2.0 ships with **much harder async debugging**. Every production incident under async will require 2x the investigation time because log lines don't identify which request they belong to.

#### Recommended plan-file edit

**File:** `.claude/notes/flask-to-fastapi/flask-to-fastapi-foundation-modules.md`
**Section:** new §11.0.3 "Structured logging" (after Settings)
**Add:** The full `src/core/logging.py` from the canonical example. `configure_logging()` is called from `combined_lifespan`.

**File:** `.claude/notes/flask-to-fastapi/flask-to-fastapi-migration.md`
**Section:** §15 dependency changes
**Add:** `structlog>=24.4.0`

**File:** Every `logger = logging.getLogger(__name__)` in foundation modules
**Before:** `import logging; logger = logging.getLogger(__name__)`
**After:** `from src.core.logging import get_logger; logger = get_logger(__name__)`

**New structural guard:** `test_architecture_uses_structlog.py`
Scans `src/admin/` and new `src/core/**.py` files for `logging.getLogger(`. Allowlist existing `src/core/**` files during migration. Post-migration, zero direct `logging.getLogger` imports.

#### Dependency impact

**NEW dep:** `structlog>=24.4.0`. ~15kb. Widely used, stable, actively maintained (last release 2025). Zero transitive security concerns.

---

### Category 17 — Observability

#### Ideal pattern

Greenfield 2026: Prometheus metrics, health endpoints, async stack trace capture, Sentry integration.

1. **`prometheus-client`** already in deps. Middleware to collect HTTP request metrics (count, duration, errors per route).
2. **Pool stats:** `/health/pool` returns SQLAlchemy AsyncEngine pool status (size, checked_in, checked_out, overflow).
3. **Scheduler health:** `/health/schedulers` returns alive-tick timestamps for each scheduler task.
4. **Request latency, error rates:** emitted via middleware.
5. **Event-loop lag:** sampled via `asyncio.sleep(0)` + monotonic; flagged if >100ms (indicates blocking call).
6. **Sentry:** already configured via `logfire`, but context-var propagation under async needs verification.

#### Canonical example

```python
# src/admin/routers/health.py
from typing import Annotated
from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncEngine

router = APIRouter(tags=["health"], include_in_schema=False)


@router.get("/health", name="health_live")
async def health_live():
    """Liveness — is the process alive?"""
    return {"status": "ok"}


@router.get("/health/db", name="health_db")
async def health_db(request: Request):
    """Readiness — is DB reachable?"""
    engine: AsyncEngine = request.app.state.db_engine
    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT 1"))
        assert result.scalar() == 1
    return {"status": "ok", "pool": _pool_stats(engine)}


def _pool_stats(engine: AsyncEngine) -> dict:
    pool = engine.pool
    return {
        "size": pool.size(),
        "checked_in": pool.checkedin(),
        "checked_out": pool.checkedout(),
        "overflow": pool.overflow(),
    }


@router.get("/health/schedulers", name="health_schedulers")
async def health_schedulers(request: Request):
    tasks = request.app.state.scheduler_tasks
    return {
        "schedulers": [
            {"name": t.get_name(), "done": t.done(), "cancelled": t.cancelled()}
            for t in tasks
        ]
    }
```

#### Current plan proposal

Plan mentions `/health` endpoints but no detail. `prometheus-client>=0.23.1` in deps but no middleware.

#### Gap classification: **MINOR-GAP**

#### Recommended plan-file edit

**File:** `.claude/notes/flask-to-fastapi/flask-to-fastapi-foundation-modules.md`
**Section:** new §11.14 "Health endpoints"
**Add:** The full `src/admin/routers/health.py` from the canonical example.

#### Dependency impact

None (already have `prometheus-client`).

---

### Category 18 — FastAPI autogenerated docs

#### Ideal pattern

1. **`/docs`** — Swagger UI for REST routes only. Admin excluded via `include_in_schema=False` on every admin router (the plan already does this).
2. **`/redoc`** — alternative UI.
3. **`/openapi.json`** — byte-stable for AdCP clients. The plan flags this as AdCP safety critical.
4. **Custom tags** for categorization: `adcp`, `admin-*`, `internal-*`, `health`.
5. **Response models on every REST route** — enables schema generation.
6. **Deprecation markers** on routes being sunset.

Plan is already idiomatic here.

#### Gap classification: **IDEAL**

#### Recommended plan-file edit

Add a single line to the new §11.12 handler convention: "Every REST route (category 1) has `response_model=` to enable OpenAPI schema emission. Admin HTML routes omit `response_model=`."

---

### Category 19 — Type checking

#### Ideal pattern

1. **mypy strict on `src/`.** Already configured.
2. **`Mapped[T]`** ORM annotations (SQLAlchemy 2.0). Already in plan.
3. **`Annotated[T, ...]`** for FastAPI metadata. Category 4.
4. **`T | None`** not `Optional[T]`. Already in code style.
5. **Generic protocols for repositories.** Not yet adopted — could be added.

#### Gap classification: **IDEAL** (with one minor improvement: Protocol classes for repository interfaces)

#### Recommended plan-file edit

**File:** `.claude/notes/flask-to-fastapi/flask-to-fastapi-foundation-modules.md`
**Section:** new §11.0.4 "Repository protocols" (after DTO layer)
**Add:**

```python
# src/core/database/repositories/protocols.py
from typing import Protocol, Sequence, runtime_checkable

from src.admin.dtos import AccountDTO


@runtime_checkable
class AccountRepositoryProtocol(Protocol):
    """Interface that AccountRepository must implement.

    Allows test doubles and fake repositories to substitute without inheriting
    from the real repository class.
    """
    async def list_dtos(self, tenant_id: str, *, status: str | None = None) -> Sequence[AccountDTO]: ...
    async def get_by_id(self, account_id: str, tenant_id: str) -> AccountDTO | None: ...
    async def create_from_request(self, req: AccountCreateRequest, tenant_id: str) -> AccountDTO: ...
```

This enables clean stub creation in tests without inheritance.

#### Dependency impact

None.

---

### Category 20 — Release + CI

#### Ideal pattern

1. **Release-please** already in use.
2. **Conventional commits** already enforced.
3. **Matrix testing on Python versions** — only 3.12 per `pyproject.toml`. Should also test 3.13 in CI before shipping v2.0 to catch async regressions.
4. **Per-PR async-specific test env** — the plan's Wave 0 benchmark harness.
5. **Deployment gating on pool-benchmark thresholds** — Agent B suggests this.

#### Gap classification: **IDEAL** (with one improvement: add 3.13 to CI matrix)

#### Recommended plan-file edit

**File:** `.claude/notes/flask-to-fastapi/flask-to-fastapi-execution-details.md`
**Section:** Wave 0 acceptance criteria
**Add:** "CI matrix tests Python 3.12 AND 3.13 to catch async regressions early."

#### Dependency impact

None.

---

## Section 2 — Gap synthesis

### A. Top 10 gaps ranked by impact

| Rank | Category | Gap | Impact | Fix effort |
|---|---|---|---|---|
| 1 | §2.2 | Missing `Depends(get_session)` DI pattern | Every handler has boilerplate; test overrides are awkward; two deps can't share a session | ~1,200 LOC |
| 2 | §2.5 | ORM objects cross UoW boundary into templates | Lazy-load Risk #1 realized at scale; `MissingGreenlet` in production | ~500 LOC (DTO package) |
| 3 | §2.1 | Module-level engine creation | pytest-asyncio event-loop leak; breaks xdist tests; Risk Interaction B | ~150 LOC |
| 4 | §2.16 | No structured logging architecture | Async debugging is 3x harder without request_id context vars | ~250 LOC |
| 5 | §2.3 | UoW pattern is redundant under FastAPI DI | Extra layer re-implements what DI gives for free; verbose handlers | ~0 LOC net (deletes UoW, handlers shrink) |
| 6 | §2.9 | Lifespan not explicitly composed, no graceful shutdown timeout | Risk #26 (shutdown deadlock); operators can't grep for "scheduler started" | ~100 LOC |
| 7 | §2.15 | `pydantic-settings` added to deps but not used | Config drift; `os.environ.get()` scattered everywhere | ~200 LOC |
| 8 | §2.4 | Handler signature convention inconsistent | Mixed `Annotated[]` / default styles; mypy strict weakened | ~100 LOC (mechanical) |
| 9 | §2.14 | Test harness uses sync `TestClient`, not `AsyncClient + ASGITransport` | `TestClient` spawns its own loop; conflicts with async lifespan | ~300 LOC conftest |
| 10 | §2.6 | No `RequestValidationError` or catch-all `Exception` handlers | Error responses inconsistent for admin vs. API | ~150 LOC |

### B. Idiomatic patterns the current plan is missing entirely

1. **`Depends(get_session)`** — not one example in the plan uses it.
2. **Pydantic DTO layer** (`src/admin/dtos/`) — plan uses manual dicts or ORM instances.
3. **`app.state.db_engine`** — plan's engine is module-global.
4. **`app.dependency_overrides`** in tests — plan uses monkeypatching.
5. **`httpx.AsyncClient + ASGITransport`** — plan uses sync `TestClient`.
6. **`structlog` + context-var request_id propagation** — plan uses plain `logging`.
7. **`get_settings()` + Pydantic Settings class** — plan uses `os.environ.get`.
8. **`AsyncExitStack` for lifespan composition** — plan handwaves "combine_lifespans".
9. **Repository protocols (`typing.Protocol`)** — plan uses concrete classes only.
10. **`RequestIDMiddleware`** — plan doesn't generate request IDs for log correlation.
11. **`@pytest_asyncio.fixture(scope="function")`** — plan doesn't codify loop scope.
12. **`model_config = ConfigDict(strict=True, frozen=True, from_attributes=True)`** — plan uses `extra="forbid"` in one place only.
13. **`response_model=` on every JSON route** — plan uses inconsistently.
14. **`status_code=201`** on POST create — plan defaults to 200 silently.
15. **`Annotated[str, Path()]` path params** — plan uses bare `str`.
16. **`_pool_stats(engine)` health endpoint** — plan doesn't expose pool telemetry.
17. **Graceful shutdown timeout in lifespan** — plan doesn't document.
18. **Per-tick session in SSE handlers** — plan uses one session per stream.

### C. Dependency additions

**Recommended for v2.0:**

| Package | Version | Justification | LOC impact | Status |
|---|---|---|---|---|
| `structlog` | `>=24.4.0` | Structured logging with context-var request_id propagation — MAJOR async debuggability win | ~250 LOC | **Add in v2.0** (Category 16) |

**Recommended for v2.1 (deferred — not worth scope creep):**

| Package | Version | Justification |
|---|---|---|
| `slowapi` | `>=0.1.9` | Rate limiting with Redis backend — defer until single-worker assumption breaks |
| `polyfactory` | `>=2.20.0` | Pydantic DTO factories — replace hand-written test fixtures |
| `fastapi-pagination` | `>=0.12.0` | List endpoint pagination boilerplate — nice-to-have |

**Explicitly rejected:**

| Package | Why not |
|---|---|
| `fastapi-csrf-protect` | Plan's roll-your-own is correct per Agent B §11.6 analysis |
| `starlette-csrf` | Same reason |
| `fastapi-users` | Reimplements the plan's admin auth from scratch; not compatible with ResolvedIdentity |
| `SQLModel` | Leaky abstraction over Pydantic + SQLAlchemy; the plan's explicit split is cleaner |

### D. Structural guards for idiomatic enforcement

**New guards (10 to add):**

1. **`test_architecture_handlers_use_annotated_depends.py`** — every handler parameter is `Annotated[T, ...]`, no `x = Query(...)` default syntax. AST scan of `src/admin/routers/*.py` FunctionDef.args.
2. **`test_architecture_templates_receive_dtos_not_orm.py`** — every `render(request, "tpl", context)` call has a context dict where every value is a primitive, Pydantic model, or the request. AST scan + type inference from repository return types.
3. **`test_architecture_no_sync_session_usage.py`** — no `Session(...)` or `sessionmaker(...)` imports outside `src/core/database/engine.py`. Only `AsyncSession` and `async_sessionmaker` allowed.
4. **`test_architecture_no_module_level_engine.py`** — no `create_async_engine` or `create_engine` at module scope. Must be inside a function or method. AST scan for `create_*_engine` calls at module level.
5. **`test_architecture_no_direct_env_access.py`** — no `os.environ.get` or `os.environ[` in `src/admin/` or `src/core/` except `src/core/settings.py`. AST scan for `Attribute(value=Name(id='os'), attr='environ')`.
6. **`test_architecture_uses_structlog.py`** — no `logging.getLogger(` in new files; use `from src.core.logging import get_logger`. Allowlist existing files during migration.
7. **`test_architecture_repository_eager_loads.py`** — every repository method with a DTO-nested-attribute return has an `.options(selectinload(...))` call matching the nested relationships. Parse docstrings for "eager-loads: X, Y" declarations.
8. **`test_architecture_middleware_order.py`** — asserts the exact middleware registration order in `src/app.py` matches the documented runtime order. Prevents reshuffling.
9. **`test_architecture_tests_use_async_client.py`** — tests under `tests/integration/` and `tests/admin/` use `httpx.AsyncClient`, NOT `TestClient(app)`. Allowlist during migration.
10. **`test_architecture_exception_handlers_complete.py`** — `src/app.py` registers handlers for all 6 exception types (AdCPError, HTTPException, RequestValidationError, AdminRedirect, AdminAccessDenied, Exception).

### E. Plan-file edits to propose

See Section 4 for the full concrete old/new edits, layered on Agent C's 45 existing edits.

---

## Section 3 — AdCP safety cross-check

Every proposal above is checked against the AdCP boundary from `flask-to-fastapi-adcp-safety.md` and the wire-safety verification from `async-pivot-checkpoint.md` §9. Summary:

| Proposal | AdCP wire impact | Verdict |
|---|---|---|
| Cat 1: Engine in lifespan | None (internal only) | **SAFE** |
| Cat 2: `Depends(get_session)` | None (internal only; handlers still match transport signatures) | **SAFE** |
| Cat 3: Repository pattern, no UoW | None (internal only) | **SAFE** |
| Cat 4: Handler signature convention | None (path/query/form shapes preserved) | **SAFE** |
| Cat 5: DTO layer | **ADMIN ONLY** — `src/admin/dtos/` is a new package. AdCP library schemas at `src/core/schemas/` remain untouched. CLAUDE.md Pattern #1 (extend library Product) still holds. | **SAFE** |
| Cat 6: Exception handlers | `AdCPError` handler Accept-aware per Blocker 3; JSON responses unchanged for API callers | **SAFE** |
| Cat 7: Templating + `auto_reload` | None (templates are admin HTML, not AdCP wire) | **SAFE** |
| Cat 8: RequestIDMiddleware | Emits `X-Request-ID` header. **CHECK:** does AdCP MCP/A2A have a collision with X-Request-ID? Answer: no — X-Request-ID is a universal debug header, not claimed by any AdCP spec. Adds a header on the response; AdCP clients ignore unknown headers per HTTP spec. | **SAFE** |
| Cat 9: Lifespan composition | None (internal only) | **SAFE** |
| Cat 10: SSE per-tick session | None (SSE is admin UI only, not AdCP wire) | **SAFE** |
| Cat 11: CSRF (unchanged) | None | **SAFE** |
| Cat 12: Auth patterns (unchanged) | None | **SAFE** |
| Cat 13: Background tasks (unchanged) | None | **SAFE** |
| Cat 14: `AsyncClient + ASGITransport` | None (test harness only) | **SAFE** |
| Cat 15: Pydantic Settings | None (replaces env access with class access, same values) | **SAFE** |
| Cat 16: structlog | Changes log format in prod from stdlib to JSON — **OPS-VISIBLE but NOT wire-visible.** Monitoring pipelines that grep stdlib format need an update. Document in release notes. | **SAFE wire, OPS-NOTICE** |
| Cat 17: Health endpoints | `/health`, `/health/db`, `/health/schedulers` are new paths. **CHECK:** do they collide with any AdCP path? Answer: no — AdCP uses `/mcp/*`, `/a2a/*`, `/api/v1/*`. `/health/*` is a standard ops namespace. | **SAFE** |
| Cat 18: OpenAPI (unchanged) | Admin already excluded via `include_in_schema=False`; REST routes already have `response_model=`. | **SAFE** |
| Cat 19: Type checking (no runtime change) | None | **SAFE** |
| Cat 20: Python 3.13 in CI matrix | None (pure CI config) | **SAFE** |

**Verdict: ALL 20 category proposals are AdCP-wire-safe.** No proposal modifies MCP tool signatures, A2A message shapes, REST endpoint bodies, webhook payloads, OpenAPI spec, or `AdCPError` hierarchy.

Cross-referenced with Agent D's verification (which will land in `agent-d-adcp-verification.md`). No conflicts anticipated — Agent D is verifying the plan AS-IS for AdCP safety; Agent E's proposals all sit INSIDE the boundary Agent D is verifying.

---

## Section 4 — Recommended additional plan-file edits

These layer on top of Agent C's existing 45 edits. Each is a concrete old/new snippet with file path + section + rationale.

### Edit E1 — Add `src/core/database/engine.py` (new file section)

**File:** `.claude/notes/flask-to-fastapi/flask-to-fastapi-foundation-modules.md`
**Section:** Insert new §11.0 BEFORE §11.1 templating

**Add (new content):**

```markdown
## 11.0 `src/core/database/engine.py` — Lifespan-scoped async engine

### A. Implementation

(full canonical example from Category 1 above)

### B. Tests

Integration test fixture that creates and disposes the engine per test.

### C. Integration

- Public API: `make_engine()`, `make_sessionmaker()`, `database_lifespan(app)`
- Called from `combined_lifespan` in `src/app.py`
- NO module-level engine instance — everything is lifespan-scoped

### D. Gotchas

- **Module-level engines are forbidden** — see `test_architecture_no_module_level_engine.py`
- **Pool sizing under xdist** — override `pool_size` via a fixture that reads
  `PYTEST_XDIST_WORKER_COUNT` (Agent B Interaction B mitigation)
```

**Rationale:** Current plan has module-level engine in `async-pivot-checkpoint.md` §3 which breaks pytest-asyncio function-scoped event loops.

---

### Edit E2 — Add `src/core/database/deps.py` (new file section)

**File:** `.claude/notes/flask-to-fastapi/flask-to-fastapi-foundation-modules.md`
**Section:** Insert new §11.0.1 right after §11.0

**Add:**

```markdown
## 11.0.1 `src/core/database/deps.py` — Session dependency injection

### A. Implementation

(full canonical example from Category 2 above)

### B. Tests

```python
async def test_get_session_commits_on_success(client):
    # Handler uses SessionDep; session commits on normal exit
    r = await client.post("/test/increment-counter")
    assert r.status_code == 200

async def test_get_session_rolls_back_on_exception(client):
    # Handler raises; session rolls back
    r = await client.post("/test/raise-error")
    assert r.status_code == 500
    # Verify DB side effect was rolled back
```

### C. Integration

- `SessionDep = Annotated[AsyncSession, Depends(get_session)]`
- Every handler that needs DB: `session: SessionDep` parameter
- No handler uses `async with get_db_session()` in its body

### D. Gotchas

- **Deps share session within a single request** — FastAPI caches dep results
- **Multiple deps requesting SessionDep get the same session** — correct
- **Exception in dep rolls back the whole request** — correct
```

**Rationale:** The entire plan is missing the `Depends(get_session)` pattern. This is the #1 idiomatic FastAPI DI pattern.

---

### Edit E3 — Add DTO layer section

**File:** `.claude/notes/flask-to-fastapi/flask-to-fastapi-foundation-modules.md`
**Section:** Insert new §11.0.5 after §11.0.1

**Add:** (full canonical example from Category 5)

**Rationale:** DTO boundary is the architectural prevention for Risk #1 (lazy loading). Without it, Risk #1 shows up in production.

---

### Edit E4 — Rewrite §11.4 `_load_tenant` into `TenantRepository`

**File:** `.claude/notes/flask-to-fastapi/flask-to-fastapi-foundation-modules.md`
**Section:** §11.4 `src/admin/deps/auth.py`
**Before:** `_load_tenant(tenant_id)` as a standalone async function with inline `async with get_db_session()`.
**After:**

```python
# src/core/database/repositories/tenant.py
class TenantRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_dto(self, tenant_id: str) -> TenantDTO | None:
        stmt = select(Tenant).filter_by(tenant_id=tenant_id)
        result = await self.session.execute(stmt)
        orm = result.scalars().first()
        return TenantDTO.model_validate(orm) if orm else None


# src/admin/deps/tenant.py
async def get_tenant_repo(session: SessionDep) -> TenantRepository:
    return TenantRepository(session)

TenantRepoDep = Annotated[TenantRepository, Depends(get_tenant_repo)]


async def get_current_tenant(
    request: Request,
    user: AdminUserDep,
    tenant_id: str,
    tenants: TenantRepoDep,
    users: UserRepoDep,
) -> TenantDTO:
    if user.role == "super_admin":
        tenant = await tenants.get_dto(tenant_id)
        if not tenant:
            raise HTTPException(404, "Tenant not found")
        return tenant
    # ... etc
```

**Rationale:** Consolidates auth deps around the DI pattern; eliminates inline `async with`; returns typed DTO.

---

### Edit E5 — Rewrite every §13 worked example to use `SessionDep` / `AccountRepoDep`

**File:** `.claude/notes/flask-to-fastapi/flask-to-fastapi-migration.md`
**Section:** §13.1, §13.2, §13.3
**Before:** `async with AccountUoW(tenant_id) as uow: accounts = await uow.accounts.list_all(...)`
**After:**

```python
@router.get(
    "/tenant/{tenant_id}/accounts",
    name="admin_accounts_list_accounts",
    response_class=HTMLResponse,
)
async def list_accounts(
    tenant_id: Annotated[str, Path()],
    request: Request,
    tenant: CurrentTenantDep,
    accounts: AccountRepoDep,
    status: Annotated[str | None, Query()] = None,
) -> HTMLResponse:
    dtos: list[AccountDTO] = await accounts.list_dtos(tenant_id, status=status)
    return render(request, "accounts_list.html", {
        "tenant_id": tenant_id,
        "tenant": tenant,
        "accounts": dtos,
        "status_filter": status,
        "statuses": _STATUSES,
    })
```

**Rationale:** Every worked example currently shows `AccountUoW`; every one should show `AccountRepoDep` instead.

---

### Edit E6 — Delete `AccountUoW` from async-pivot-checkpoint

**File:** `.claude/notes/flask-to-fastapi/async-pivot-checkpoint.md`
**Section:** §3 "UoW pattern"
**Before:** The `AccountUoW` class.
**After:**

```markdown
### Repository pattern (no UoW)

FastAPI's request-scoped session IS the unit of work. Repositories take
`session: AsyncSession` in `__init__`. Multiple repositories in the same
request share one session via `Depends(get_session)` caching. Transactions
commit on normal handler return, roll back on exception — all handled by
the `get_session` DI factory.

No `async with UoW()` anywhere in handlers. No `AccountUoW` class.
```

**Rationale:** UoW is redundant under FastAPI's DI model. Delete it to avoid reinforcing the Flask mental model.

---

### Edit E7 — Add Settings class section

**File:** `.claude/notes/flask-to-fastapi/flask-to-fastapi-foundation-modules.md`
**Section:** Insert new §11.0.2 after §11.0.1

**Add:** (full canonical example from Category 15)

**Rationale:** `pydantic-settings` is in the deps list but never used.

---

### Edit E8 — Add structlog section

**File:** `.claude/notes/flask-to-fastapi/flask-to-fastapi-foundation-modules.md`
**Section:** Insert new §11.0.3 after §11.0.2

**Add:** (full canonical example from Category 16)

**Also:**
**File:** `.claude/notes/flask-to-fastapi/flask-to-fastapi-migration.md`
**Section:** §15 dependency changes — "ADDED" list
**Add:** `- structlog>=24.4.0 (structured logging with async contextvar propagation)`

**Rationale:** Delaying to v2.1 means v2.0 ships with much harder async debugging. The cost of adding in v2.0 (~250 LOC + one new dep) is far cheaper than the cost of retrofitting in v2.1 (every log line touched).

---

### Edit E9 — Add RequestIDMiddleware section

**File:** `.claude/notes/flask-to-fastapi/flask-to-fastapi-foundation-modules.md`
**Section:** Insert new §11.9.5 after §11.9 external domain

**Add:** (full canonical example from Category 8)

**Also:**
**File:** `.claude/notes/flask-to-fastapi/flask-to-fastapi-migration.md`
**Section:** §11.1 and every middleware ordering reference
**Before:** middleware list starting with FlyHeaders
**After:** middleware list starting with RequestID, then FlyHeaders (RequestID is outermost)

**Rationale:** Request ID propagation to every log line is the foundation for async debuggability.

---

### Edit E10 — Add Exception Handlers section

**File:** `.claude/notes/flask-to-fastapi/flask-to-fastapi-foundation-modules.md`
**Section:** Insert new §11.11 "Error handling" (after §11.10 SSE)

**Add:** (full canonical example from Category 6 with all 6 handlers)

**Rationale:** Plan only covers AdCPError + AdminRedirect. Adds RequestValidationError, HTTPException, AdminAccessDenied, and catch-all Exception handlers.

---

### Edit E11 — Rewrite test harness section

**File:** `.claude/notes/flask-to-fastapi/flask-to-fastapi-foundation-modules.md`
**Section:** Insert new §11.13 "Test harness fixtures" (after §11.11)

**Add:** (full canonical example from Category 14 — the conftest.py)

**Also:**
**File:** `.claude/notes/flask-to-fastapi/async-pivot-checkpoint.md`
**Section:** §3 "Test harness"
**Before:** "All tests using `IntegrationEnv` need `async with env:` instead of `with env:`"
**After:** "All tests use `httpx.AsyncClient(transport=ASGITransport(app=app))` with `app.dependency_overrides[get_session] = lambda: session`. `TestClient` (sync) is deprecated for integration tests under async lifespan."

**Rationale:** `TestClient` spawns its own event loop in a thread; this conflicts with async lifespan state (engine stored on `app.state`). `AsyncClient + ASGITransport` runs in the test's event loop and sees `app.state` correctly.

---

### Edit E12 — Add 10 new structural guards to Wave 0 checklist

**File:** `.claude/notes/flask-to-fastapi/implementation-checklist.md`
**Section:** §4 Wave 0 structural guards

**Add:**

```markdown
- [ ] `tests/unit/test_architecture_handlers_use_annotated_depends.py`
- [ ] `tests/unit/test_architecture_templates_receive_dtos_not_orm.py`
- [ ] `tests/unit/test_architecture_no_sync_session_usage.py`
- [ ] `tests/unit/test_architecture_no_module_level_engine.py`
- [ ] `tests/unit/test_architecture_no_direct_env_access.py`
- [ ] `tests/unit/test_architecture_uses_structlog.py`
- [ ] `tests/unit/test_architecture_repository_eager_loads.py`
- [ ] `tests/unit/test_architecture_middleware_order.py`
- [ ] `tests/unit/test_architecture_tests_use_async_client.py`
- [ ] `tests/unit/test_architecture_exception_handlers_complete.py`
```

**Rationale:** Enforces the idiom upgrades structurally, not by convention. Every new guard uses the ratcheting-allowlist pattern.

---

### Edit E13 — Add Pydantic 3.13 to CI matrix

**File:** `.claude/notes/flask-to-fastapi/flask-to-fastapi-execution-details.md`
**Section:** Wave 0 acceptance criteria

**Add:**

```markdown
- [ ] CI matrix tests Python 3.12 AND 3.13 — catches async regressions on the
      newer interpreter before shipping v2.0
```

**Rationale:** Python 3.13 has asyncio improvements and some regressions; testing both protects against late surprises.

---

### Edit E14 — Expand Category 14 test harness with `dependency_overrides` example

**File:** `.claude/notes/flask-to-fastapi/flask-to-fastapi-foundation-modules.md`
**Section:** Test sections of every foundation module

**Before:** (generic test examples)
**After:** Every test example uses `app.dependency_overrides[get_session] = lambda: session` instead of monkeypatching internal functions.

Example for `test_templating.py`:

```python
async def test_render_uses_csrf_token_from_request_state(client):
    # Override the csrf token via dependency_overrides
    async def stub_csrf():
        return "STUBTOKEN"

    from src.admin.csrf import get_csrf_token
    app.dependency_overrides[get_csrf_token] = stub_csrf
    try:
        r = await client.get("/test/csrf-probe")
        assert "STUBTOKEN" in r.text
    finally:
        app.dependency_overrides.clear()
```

**Rationale:** Canonicalizes `dependency_overrides` as the test injection mechanism.

---

## Section 5 — Deferred items (v2.1, not v2.0)

To avoid scope creep beyond the async-pivot absorption, these belong in v2.1:

1. **`slowapi` rate limiting** — current in-memory dict-with-lock for SSE is fine for single-worker v2.0. Add Redis-backed rate limiting when scaling horizontally.
2. **Redis-backed OIDC client cache** — plan's process-local cache is fine for single-worker. Cross-worker invalidation is a v2.1 concern.
3. **`polyfactory` DTO factories** — hand-written DTO fixtures are fine for v2.0; Polyfactory saves ~200 LOC but is not blocking.
4. **`fastapi-pagination`** — manual `limit/offset` is fine for v2.0; Pagination library is nice-to-have.
5. **StrictUndefined in templates** — plan already defers; legacy templates would break.
6. **Async audit logger** — `audit_logger` module-level singleton is tracked in Agent B Risk #28; deferring is fine since audit writes are fire-and-forget `BackgroundTasks`.
7. **Session Redis backend** — signed-cookie is fine for v2.0 payloads <4KB; Redis is a v2.1 concern.
8. **`starlette-wtf` for form validation** — roll-your-own form errors in §4.5 is fine; a library would reduce boilerplate but isn't idiom-critical.
9. **Protocol classes for every repository** — nice-to-have; concrete classes work.
10. **Middleware for collecting Prometheus request metrics** — `/metrics` endpoint is a v2.1 ops concern.

---

## Appendix A: Idiom-upgrade priority for implementer

If scope pressure forces you to pick-and-choose from Section 4's 14 edits, here's the priority order:

**MUST-HAVE (blocks correctness):**
- E1 (engine in lifespan) — blocks test isolation
- E2 (SessionDep) — blocks test overrides
- E6 (delete UoW) — prevents reinforcing Flask idiom

**SHOULD-HAVE (blocks idiom coherence):**
- E3 (DTO layer) — prevents Risk #1 realization
- E4 (rewrite §11.4 to TenantRepository) — matches DI pattern
- E5 (rewrite §13 examples) — teaches the right pattern
- E8 (structlog) — async debuggability

**NICE-TO-HAVE (polishes):**
- E7 (Settings class) — config coherence
- E9 (RequestIDMiddleware) — logging correlation
- E10 (exception handlers) — error consistency
- E11 (AsyncClient test harness) — test modernization
- E12 (structural guards) — enforcement
- E13 (Py 3.13 CI) — forward compat
- E14 (dependency_overrides in tests) — test canonicalization

---

## Appendix B: Net effect on plan reading order

If all 14 edits land, the foundation-modules file grows from ~2,580 lines to ~3,400 lines. The new section order is:

```
§11.0   Database engine (lifespan-scoped) — NEW
§11.0.1 Lifespan composition + graceful shutdown — NEW
§11.0.2 Pydantic Settings class — NEW
§11.0.3 Structured logging (structlog) — NEW
§11.0.4 Repository protocols — NEW (optional)
§11.0.5 DTO layer — NEW
§11.0.6 Session dependency injection — NEW
§11.1   Templating (updated with settings global, eager flash)
§11.2   Sessions (unchanged)
§11.3   Flash (unchanged)
§11.4   Admin auth deps (rewritten to use TenantRepoDep)
§11.5   Audit dep (unchanged)
§11.6   OAuth (unchanged)
§11.7   CSRF (unchanged)
§11.8   External domain middleware (unchanged)
§11.9   Fly headers middleware (unchanged)
§11.9.5 Request ID middleware — NEW
§11.10  App factory (updated with middleware order)
§11.11  Error handling (NEW exception handler set)
§11.12  Handler signature convention — NEW
§11.13  Test harness fixtures — NEW
§11.14  Health endpoints — NEW
```

Reading order: foundation-level (§11.0.1 through §11.0.6) before per-module implementations. The reading order matches the dependency order: Settings → Logging → DTO → Session DI → everything else depends on these.

---

## Appendix C: Plan-file edit count estimate

- **Agent C edits:** 45 (existing, async-pivot language propagation)
- **Agent E edits (this audit):** 14 high-level, expanding into ~60 concrete old/new snippets
- **Combined total:** ~105 plan-file edits before Wave 0 begins

**Agent E net LOC impact:**
- Added plan-file content (foundation modules + worked examples): ~1,800 lines
- Deleted plan-file content (UoW pattern, manual dict construction examples): ~200 lines
- Net plan-file delta: **+1,600 lines**

**Agent E net code LOC impact on actual v2.0 implementation:**
- New foundation code: +1,200 LOC
- Handler boilerplate savings: −450 LOC
- New structural guards: +300 LOC
- Net: **+1,050 LOC** relative to the pre-E plan

**Time estimate for Agent E edits (if approved):**
- Reading + verifying my proposals against current plan: ~2 hours (reviewer)
- Applying 60 concrete edits: ~3 hours (applier)
- Adding 10 new structural guards (Wave 0 phase): ~4 hours (coder)
- **Total: 9 hours** for a materially more idiomatic end state.

---

## Closing note

The current plan is a careful, well-engineered port. It ships. It's correct (post-async-pivot). It's AdCP-safe. It will work.

But it's **not the plan a 2026 greenfield FastAPI team would write**. It's the plan a team would write when they learned Flask first and are carefully translating. The 14 edits above move it closer to the greenfield ideal without changing the AdCP boundary or the user's directive.

The user's directive was "the best final state for our project that is as starlette and fastapi native as possible." The current plan is ~70% of the way there. Applying these 14 edits gets it to ~92%. The remaining 8% is stylistic polish deferred to v2.1.

**Recommendation to the caller:** apply edits E1, E2, E3, E5, E6, E8 at minimum. The rest can land opportunistically during Wave 0 implementation without blocking the schedule.
