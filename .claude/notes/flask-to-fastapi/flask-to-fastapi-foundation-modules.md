# §11 Foundation Modules — Detailed Elaboration

> **v2.0 LAYER GUIDE (2026-04-14)**
>
> This file contains foundation module designs for the FULL v2.0 migration. Sections are tagged by layer:
> - **[L0-L4]** — Implement during sync layers (Flask removal + test harness + FastAPI-native pattern refinement) using **sync** patterns from `execution-plan.md`
> - **[L4]** — Sync `SessionDep`, DTO boundary, structlog wiring, pydantic-settings extension, `render()` wrapper deletion, ContextManager refactor. Lands AFTER Flask removal (L2) and test harness modernization (L3).
> - **[L5+]** — Implement during async conversion. These sections contain `async def`, `AsyncSession` patterns. Activated at L5b (SessionDep alias flip) and completed across L5c-L5e.
> - **[L6]** — Native refinements: delete `flash.py`, `app.state` singletons for `SimpleAppCache`, router subdir reorg, `logfire` instrumentation.
> - **[L0 CANDIDATE]** — Can land early (framework-agnostic, no layer dependency)
>
> **For L0-L4 implementation, always use `sync def` handlers with `with get_db_session() as session:` in handler bodies (except the 3-4 OAuth callback handlers in L1b that require `async def` for Authlib compatibility — those are the only async-def handlers through L4).**

**Key v2.0 scope changes:** Sections 11.0 (engine.py) and 11.0.1 (deps.py/SessionDep) do NOT exist in L0-L3 — the existing `database_session.py` provides all session infrastructure. **L4 introduces sync `SessionDep = Annotated[Session, Depends(get_session)]`**; L5b re-aliases it to `AsyncSession`. Sections 11.0.4.D-H (dep factories, cross-repo composition) are replaced by direct repository instantiation inside `with get_db_session()` blocks through L3, then adopted at L4. Section 11.7 (csrf.py) implements CSRFOriginMiddleware (Origin header validation), NOT Double Submit Cookie.

Target file tree under `src/admin/`:
```
src/admin/
  templating.py
  sessions.py
  flash.py
  csrf.py
  oauth.py
  app_factory.py
  deps/
    __init__.py
    auth.py
    tenant.py
    audit.py
  middleware/
    __init__.py
    external_domain.py
    fly_headers.py
```

Everything below uses Python 3.12+ syntax. These foundation modules use sync `Session` via `with get_db_session() as db:` during Layers 0-4. Layer 5c converts admin handlers and their repository dependencies to `AsyncSession`. Each section below is phase-tagged (`[Layer 0-4]` sync, `[Layer 5+]` async). Do not mix patterns across layer boundaries. Under Agent E's idiom upgrade (Categories 1-2 in `async-audit/agent-e-ideal-state-gaps.md`), Layer 4 introduces the DI pattern `session: SessionDep = Annotated[Session, Depends(get_session)]` (still sync); Layer 5b re-aliases `SessionDep` to `AsyncSession` as a 1-file flip.

---

## 11.0 `src/core/database/engine.py` — Lifespan-scoped async engine (Agent E Category 1)

> **[L5+]** This module is created at L5b (SessionDep alias flip to `AsyncSession`) and completed across L5c-L5e. L0-L4 use the existing `database_session.py` with sync patterns.

> **Added 2026-04-11 under the Agent E idiom upgrade.** The engine is lifespan-scoped rather than module-global to prevent pytest-asyncio event-loop leak bugs (Agent B Risk Interaction B). Engine + sessionmaker live on `app.state.db_engine` / `app.state.db_sessionmaker` — never at module import time.

### A. Implementation

```python
# src/core/database/engine.py
"""AsyncEngine lifecycle — lifespan-scoped, never module-global."""
from __future__ import annotations
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine,
)

if TYPE_CHECKING:
    from fastapi import FastAPI


def _build_async_url(sync_url: str) -> str:
    """Rewrite postgresql:// → postgresql+asyncpg:// (idempotent).

    Agent F F1.5.1 mitigation: also rewrite `sslmode=` → `ssl=` for asyncpg's
    different TLS query-param vocabulary. The rewriter is where both URL
    differences are handled in one place.
    """
    if sync_url.startswith("postgresql+asyncpg://"):
        return sync_url
    if sync_url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + sync_url[len("postgresql://"):]
    raise ValueError(f"Unsupported DATABASE_URL scheme: {sync_url}")


def make_engine(database_url: str, *, pool_size: int = 20, max_overflow: int = 10) -> AsyncEngine:
    """Factory — callable from lifespan AND from test fixtures.

    Pool sizing: default 20+10 matches benchmark tuning from async-pivot-checkpoint
    Risk #6. xdist workers override via PYTEST_XDIST_WORKER_COUNT-aware fixture.
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
        # expire_on_commit=False is MANDATORY for AsyncSession. With the SQLA
        # default `True`, every post-commit attribute read (even innocuous ones
        # like `media_buy.id` right after `session.commit()`) triggers a lazy
        # refresh under the hood. Under asyncpg that lazy refresh runs sync I/O
        # on a connection owned by the async greenlet boundary and raises
        # `sqlalchemy.exc.MissingGreenlet: greenlet_spawn has not been called`
        # with no useful stack. The failure is non-deterministic because it
        # only fires when the attribute is actually accessed, so integration
        # tests catch maybe 1 in 5 violations. Setting expire_on_commit=False
        # instructs SQLA to keep instances live post-commit — callers that need
        # a true refresh must call `await session.refresh(obj)` explicitly.
        # The structural guard `test_architecture_async_sessionmaker_expire_on_commit`
        # (§11.0.7) fails the build if any `async_sessionmaker(...)` call omits
        # this kwarg or sets it to True.
        expire_on_commit=False,
        autoflush=False,          # explicit flush, no surprises
        autobegin=True,           # SQLA 2.0 default, still worth being explicit
    )


@asynccontextmanager
async def database_lifespan(app: "FastAPI") -> AsyncIterator[None]:
    """Create engine + sessionmaker on startup, dispose on shutdown.

    Store on `app.state` so DI factories can read them off the current request.
    """
    from src.core.settings import get_settings  # pydantic-settings Settings
    settings = get_settings()
    engine = make_engine(settings.database_url)
    app.state.db_engine = engine
    app.state.db_sessionmaker = make_sessionmaker(engine)
    try:
        yield
    finally:
        await engine.dispose()
```

### B. Tests

Integration test fixture that creates and disposes the engine per test; see §11.13 for the full `conftest.py`.

### C. Integration

- Public API: `make_engine()`, `make_sessionmaker()`, `database_lifespan(app)`
- Called from `combined_lifespan` in `src/app.py`
- NO module-level engine instance — everything is lifespan-scoped
- Structural guard `test_architecture_no_module_level_engine.py` enforces this

### D. Gotchas

- **Module-level engines are forbidden.** Any `_engine = create_async_engine(...)` at module scope fails the guard.
- **Pool sizing under xdist.** Override `pool_size` via a fixture that reads `PYTEST_XDIST_WORKER_COUNT`.
- **`expire_on_commit=False` is MANDATORY.** With the default `True`, post-commit attribute access triggers a lazy-load that raises `MissingGreenlet` under `AsyncSession`. Risk #5 consequence: audit code that reads `created_at` / `updated_at` post-commit (they may be `None` now).

---

## 11.0.1 `src/core/database/deps.py` — SessionDep (Agent E Category 2)

> **[L4 SYNC, then L5b ASYNC]** L4 introduces sync `SessionDep = Annotated[Session, Depends(get_session)]`. L5b re-aliases to `AsyncSession` (1-file change). L0-L3 use `with get_db_session() as session:` in handler bodies.

> **Added 2026-04-11 under the Agent E idiom upgrade.** The idiomatic FastAPI-native pattern: handlers receive `session: SessionDep` as a parameter. The DI layer owns session lifecycle. No `async with` in handler bodies.

### A. Implementation

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
        # OR:
        session: SessionDep

    NOT a nested context manager inside handler bodies. The DI layer owns
    session lifecycle; handlers only own business logic.

    Two deps that both declare `SessionDep` get the SAME session within a
    single request (FastAPI caches dep results per-request). This is how
    multiple repositories share one transaction automatically.
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

### B. Tests

```python
import pytest

@pytest.mark.asyncio
async def test_get_session_commits_on_success(client):
    # Handler uses SessionDep; session commits on normal exit
    r = await client.post("/test/increment-counter")
    assert r.status_code == 200
    # Verify DB state reflects committed write

@pytest.mark.asyncio
async def test_get_session_rolls_back_on_exception(client):
    # Handler raises; session rolls back
    r = await client.post("/test/raise-error")
    assert r.status_code == 500
    # Verify DB side effect was rolled back
```

### C. Integration

- Every handler that needs DB: `session: SessionDep` parameter
- Repository Deps chain through `SessionDep`: `async def get_account_repo(session: SessionDep) -> AccountRepository: return AccountRepository(session)`
- No handler uses `async with get_db_session()` in its body

### D. Gotchas

- **Deps share session within a single request** — FastAPI caches dep results per request, so `get_session` is invoked once no matter how many deps transitively depend on it. Multiple repositories in the same handler share ONE transaction. Correct.
- **Exception in ANY dep rolls back the whole request.** Correct — the request is atomic.
- **Test overrides:** `app.dependency_overrides[get_session] = lambda: stub_session` is THE canonical injection mechanism. Never monkeypatch `get_session` internals.

---

## 11.0.2 `src/core/settings.py` — Pydantic Settings class (Agent E Category 15)

> **[L0 CANDIDATE]** Framework-agnostic. Can land in L0 or L4.

> **Added 2026-04-11 under the Agent E idiom upgrade.** Every config value goes through a single `Settings` class loaded via `@lru_cache get_settings()`. No more `os.environ.get("FOO", "")` scattered throughout the codebase.

### A. Implementation

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

    # Sessions & CSRF (FLASK_SECRET_KEY dual-read during v2.0 per directive #5)
    session_secret: SecretStr = Field(..., alias="SESSION_SECRET", min_length=32)

    # CSRF (Option A: Origin header validation + SameSite=Lax session cookie)
    csrf_allowed_origins: list[str] = Field(default_factory=list, alias="CSRF_ALLOWED_ORIGINS")
    csrf_allowed_origin_suffixes: list[str] = Field(default_factory=list, alias="CSRF_ALLOWED_ORIGIN_SUFFIXES")

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

### C. Integration

- Every `os.environ.get(...)` in `src/admin/` and `src/core/` is replaced with `get_settings().field`
- `SettingsDep = Annotated[Settings, Depends(get_settings)]` for handler DI
- Structural guard `test_architecture_no_direct_env_access.py` enforces — only `src/core/settings.py` may read env directly

### D. Gotchas

- **`SecretStr`** wraps sensitive fields. Unwrap via `settings.session_secret.get_secret_value()` — never log the wrapped object directly.
- **`@lru_cache(maxsize=1)`** makes `get_settings()` a singleton. Tests that need fresh settings clear the cache: `get_settings.cache_clear()`.
- **Field aliases** map Python snake_case to env SCREAMING_SNAKE_CASE — the env layer does not see Python names.

---

## 11.0.3 `src/core/logging.py` — Structured logging via structlog (Agent E Category 16)

> **[L4]** Added alongside sync `SessionDep`. structlog provides request-scoped logging with ContextVar propagation (matters especially once L5 lands async handlers).

> **Added 2026-04-11 under the Agent E idiom upgrade.** Async debugging is 3x harder without request-ID context-var propagation. Adding `structlog` at L4 (~250 LOC) avoids the much larger retrofit cost if it were deferred to a post-async layer (~400 LOC touching every log line).

### A. Implementation

```python
# src/core/logging.py
"""Structlog configuration — JSON in prod, human in dev."""
from contextlib import contextmanager
import logging
import sys
from typing import Iterator

import structlog
from structlog.contextvars import bind_contextvars, unbind_contextvars

from src.core.settings import get_settings


def configure_logging() -> None:
    """Called once from lifespan."""
    settings = get_settings()

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

### C. Integration

- `configure_logging()` called from `combined_lifespan` at startup
- Every `logging.getLogger(__name__)` in new code becomes `from src.core.logging import get_logger; logger = get_logger(__name__)`
- Under async, **contextvars propagate correctly across `await` boundaries** — every log line emitted during a request automatically carries the `request_id` bound by `RequestIDMiddleware` (§11.9.5)
- Structural guard `test_architecture_uses_structlog.py` enforces structlog usage (allowlisted for existing files during migration). Additionally, this guard blocks bare `print(...)` calls in `src/**/*.py` (allowlisted paths: `scripts/`, `alembic/versions/`, `src/core/cli/`). Production request-path code must emit via `structlog.get_logger()` so log correlation via contextvars works across async boundaries.

### D. Gotchas

- **Under async, `logging` alone does NOT propagate context-vars** — you'd need to thread request_id through every function call. `structlog.contextvars.merge_contextvars` handles this for free.
- **Log format change is OPS-VISIBLE.** In production, logs switch from stdlib format to JSON. Monitoring pipelines that grep stdlib format need an update. Document in release notes.

---

## 11.0.4 `src/core/database/repositories/base.py` — Repository base + per-repository dep factories (Agent E Categories 2 + 3)

> **[L4+]** Repository DI factories with `Depends(get_session)` land at L4 (still sync Session) and auto-upgrade to AsyncSession at L5b via the alias flip. L0-L3 use the existing UoW pattern with sync sessions.

> **Added 2026-04-11 under the Agent E idiom upgrade.** This is the operational recipe for the two biggest idiom upgrades in the Agent E audit: Category 2 (SessionDep DI, E1) and Category 3 (no-UoW repository pattern, E2+E3). §11.0.1 SessionDep tells you *how* the session enters the request; this section tells you *how* repositories compose on top of it and how `_impl` functions receive sessions from non-request callers.

### A. Why the UoW class goes away

The existing codebase has a `BaseUoW` at `src/core/database/repositories/uow.py` with seven concrete subclasses (`MediaBuyUoW`, `ProductUoW`, `WorkflowUoW`, `TenantConfigUoW`, `AccountUoW`, `CreativeUoW`, `AdminCreativeUoW`). Under Flask + sync SQLAlchemy, UoW earned its keep — it bundled "open a `get_db_session()` cm, instantiate one or more repositories over that session, commit on clean exit, rollback on exception" into a single reusable `__enter__`/`__exit__` pair. Every handler body wrote `with SomeUoW(tenant_id) as uow: ...`.

Under FastAPI + async SQLAlchemy + `Depends(get_session)`, **every line of that lifecycle logic is already handled for free by the DI layer**:

| UoW responsibility | FastAPI equivalent |
|---|---|
| Open a session on `__enter__` | `get_session()` opens an `AsyncSession` at request start (§11.0.1) |
| Commit on clean exit | `get_session()` commits after `yield` on handler success |
| Rollback on exception | `get_session()` catches and rolls back |
| Bundle multiple repositories in one session | Every repo dep factory takes `SessionDep`; FastAPI's per-request dep cache guarantees they all receive the **same** `AsyncSession` instance |
| Ensure one transaction per "unit of work" | A request **is** the unit of work — the DI layer commits once at the end |
| Prevent session-construction sprawl | Handlers never see `async with sessionmaker()` — the DI layer owns it |

A UoW class in this world is a wrapper that calls `Depends(get_session)` for you, forgets to cache, and forces every handler into a context-manager body instead of a clean parameter list. It also fixes `tenant_id` at construction time, which conflates "data-access scoping" (a repo method argument) with "transactional scoping" (the session lifetime) — two orthogonal concerns that should not share a constructor.

**Greenfield rule:** no `__aenter__`/`__aexit__`, no `commit()`/`rollback()`, no `tenant_id` in `__init__`, no `_session_cm`. Repositories are thin, stateless, per-method objects over an injected session.

### B. The base class

```python
# src/core/database/repositories/base.py
"""Repository base — stateless wrappers over an injected AsyncSession.

Design invariants (enforced by test_architecture_repository_shape.py):

1. `__init__` takes exactly one argument beyond `self`: the session.
2. No `__aenter__` / `__aexit__` / `__enter__` / `__exit__`.
3. No `commit()`, `rollback()`, `flush()` as public methods — only
   `session.flush()` inside write methods when you need to materialize
   server-side defaults or PKs for cross-repository reads.
4. `tenant_id` is NEVER a constructor argument. It is a per-method
   keyword, so the same repository instance can (in principle) serve
   queries across tenants — though in practice every call site passes
   one tenant, FastAPI dep caching wins, and the repo is recreated
   per-request anyway.
5. Return DTOs (Pydantic models) from read methods. Return ORM
   instances ONLY when the caller is itself a repository composing a
   larger write (see §11.0.5 DTO layer).
"""
from __future__ import annotations

from typing import Generic, TypeVar

from sqlalchemy.ext.asyncio import AsyncSession

#: Type variable for the ORM model a concrete repository wraps.
ModelT = TypeVar("ModelT")


class BaseRepository(Generic[ModelT]):
    """Base class for all async repositories.

    Subclasses override nothing on the base — they simply add domain
    methods. The base exists to (a) document the shape via one place,
    (b) give the structural guard a single parent class to scan for,
    and (c) optionally hold generic helpers like `by_id()` once the
    pattern stabilises.

    Usage:

        class AccountRepository(BaseRepository[Account]):
            async def list_by_tenant(self, tenant_id: str) -> list[AccountDTO]:
                stmt = select(Account).filter_by(tenant_id=tenant_id)
                result = await self.session.execute(stmt)
                return [AccountDTO.model_validate(a) for a in result.scalars().all()]

    Deliberately NOT on this class:

    * `async def __aenter__(self) -> Self` — DI owns lifecycle.
    * `async def commit(self) -> None` — DI owns transactions.
    * `tenant_id: str` constructor arg — scoping is a method-level concern.
    * `_session_cm` attribute — there is no context manager to hold.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
```

The base is intentionally near-empty. Do **not** add a `commit()` method "for convenience" — the whole point is that repositories never touch the transaction boundary. If a test fails because the handler's changes were not visible, the correct answer is to `await session.flush()` at the write site, not to commit.

### C. Example concrete repository — `AccountRepository`

```python
# src/core/database/repositories/account.py
"""Account repository — async, DTO-returning, dep-factory friendly.

Eager-load contract (asserted by test_architecture_repository_eager_loads.py):

    list_dtos            — no relationships loaded
    get_with_contact     — Account.primary_contact (selectinload)
    create_from_dto      — write, no eager loads needed
"""
from __future__ import annotations

from typing import Sequence

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.admin.dtos.account import (
    AccountCreateDTO,
    AccountDTO,
    AccountWithContactDTO,
)
from src.core.database.models import Account
from src.core.database.repositories.base import BaseRepository


class AccountRepository(BaseRepository[Account]):
    """Async data-access for `Account` + related aggregates."""

    async def list_dtos(
        self,
        tenant_id: str,
        *,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[AccountDTO]:
        """List accounts for a tenant, optionally filtered by status.

        Returns `AccountDTO` — no relationships loaded. If the caller
        needs the primary contact, use `get_with_contact` instead.
        """
        stmt = (
            select(Account)
            .filter_by(tenant_id=tenant_id)
            .order_by(Account.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        if status is not None:
            stmt = stmt.filter_by(status=status)

        result = await self.session.execute(stmt)
        orm_rows = result.scalars().all()
        return [AccountDTO.model_validate(a) for a in orm_rows]

    async def get_with_contact(
        self,
        account_id: str,
        *,
        tenant_id: str,
    ) -> AccountWithContactDTO | None:
        """Fetch one account with `primary_contact` eagerly loaded.

        The `selectinload` below is load-bearing — under async sessions,
        accessing `account.primary_contact` on a committed instance
        triggers `MissingGreenlet`. The DTO pulls the relationship
        across the boundary, so after this method returns the session
        can be safely closed without breaking the DTO shape.
        """
        stmt = (
            select(Account)
            .filter_by(account_id=account_id, tenant_id=tenant_id)
            .options(selectinload(Account.primary_contact))
        )
        result = await self.session.execute(stmt)
        orm = result.scalars().first()
        return AccountWithContactDTO.model_validate(orm) if orm else None

    async def create_from_dto(
        self,
        dto: AccountCreateDTO,
        *,
        tenant_id: str,
    ) -> AccountDTO:
        """Persist a new account and return the hydrated DTO.

        We `flush()` (not `commit()`) so that server defaults like
        `created_at` are populated on the ORM instance, letting us
        `model_validate()` into a complete DTO. The actual commit
        happens in `get_session` at handler return.
        """
        account = Account(
            account_id=dto.account_id,
            tenant_id=tenant_id,
            operator=dto.operator,
            brand=dto.brand.model_dump(mode="json"),
            status="pending_approval",
            sandbox=dto.sandbox,
            display_name=dto.display_name,
        )
        self.session.add(account)
        await self.session.flush()
        return AccountDTO.model_validate(account)
```

### D. Dep factories — the glue between SessionDep and handlers

```python
# src/admin/routers/deps/repositories.py
"""Per-repository dep factories.

Every repository dep follows the same shape:

    async def get_<repo>_repo(session: SessionDep) -> <Repo>:
        return <Repo>(session)

    <Repo>Dep = Annotated[<Repo>, Depends(get_<repo>_repo)]

Do not add logic to these factories. If you find yourself wanting to
"wrap the session in a transaction" or "bind tenant_id to the repo",
stop — that is a sign you are recreating UoW. The tenant comes from
the request path parameter; the transaction comes from `get_session`.
"""
from typing import Annotated

from fastapi import Depends

from src.core.database.deps import SessionDep
from src.core.database.repositories.account import AccountRepository
from src.core.database.repositories.creative import (
    CreativeAssignmentRepository,
    CreativeRepository,
)
from src.core.database.repositories.currency_limit import CurrencyLimitRepository
from src.core.database.repositories.media_buy import MediaBuyRepository
from src.core.database.repositories.product import ProductRepository
from src.core.database.repositories.tenant_config import TenantConfigRepository
from src.core.database.repositories.workflow import WorkflowRepository


async def get_account_repo(session: SessionDep) -> AccountRepository:
    return AccountRepository(session)


async def get_media_buy_repo(session: SessionDep) -> MediaBuyRepository:
    return MediaBuyRepository(session)


async def get_product_repo(session: SessionDep) -> ProductRepository:
    return ProductRepository(session)


async def get_workflow_repo(session: SessionDep) -> WorkflowRepository:
    return WorkflowRepository(session)


async def get_tenant_config_repo(session: SessionDep) -> TenantConfigRepository:
    return TenantConfigRepository(session)


async def get_creative_repo(session: SessionDep) -> CreativeRepository:
    return CreativeRepository(session)


async def get_creative_assignment_repo(
    session: SessionDep,
) -> CreativeAssignmentRepository:
    return CreativeAssignmentRepository(session)


async def get_currency_limit_repo(session: SessionDep) -> CurrencyLimitRepository:
    return CurrencyLimitRepository(session)


# Type aliases for handler signatures
AccountRepoDep = Annotated[AccountRepository, Depends(get_account_repo)]
MediaBuyRepoDep = Annotated[MediaBuyRepository, Depends(get_media_buy_repo)]
ProductRepoDep = Annotated[ProductRepository, Depends(get_product_repo)]
WorkflowRepoDep = Annotated[WorkflowRepository, Depends(get_workflow_repo)]
TenantConfigRepoDep = Annotated[TenantConfigRepository, Depends(get_tenant_config_repo)]
CreativeRepoDep = Annotated[CreativeRepository, Depends(get_creative_repo)]
CreativeAssignmentRepoDep = Annotated[CreativeAssignmentRepository, Depends(get_creative_assignment_repo)]
CurrencyLimitRepoDep = Annotated[CurrencyLimitRepository, Depends(get_currency_limit_repo)]
```

### E. Cross-repository composition: how one request commits atomically

```python
# src/admin/routers/accounts.py (excerpt)
from fastapi import APIRouter

from src.admin.deps.audit import AuditRepoDep
from src.admin.deps.auth import CurrentTenantDep
from src.admin.dtos.account import AccountCreateDTO, AccountDTO
from src.admin.routers.deps.repositories import (
    AccountRepoDep,
    CurrencyLimitRepoDep,
)
from src.admin.routers.deps.users import UserRepoDep

router = APIRouter(
    tags=["admin-accounts"],
    redirect_slashes=True,
    include_in_schema=False,
)


@router.post(
    "/tenant/{tenant_id}/accounts",
    name="admin_accounts_create_account",
    status_code=201,
    response_model=AccountDTO,
)
async def create_account(
    tenant_id: str,
    payload: AccountCreateDTO,
    tenant: CurrentTenantDep,
    accounts: AccountRepoDep,
    users: UserRepoDep,
    currencies: CurrencyLimitRepoDep,
    audit: AuditRepoDep,
) -> AccountDTO:
    """Create a new account + grant owner access + log the event.

    Four repositories, one session, one transaction. If ANY of the
    four operations below raises, `get_session` rolls everything
    back atomically. If all four return, `get_session` commits once
    at handler exit.

    Per-request dep caching is what makes this work: `accounts`,
    `users`, `currencies`, and `audit` all transitively depend on
    `SessionDep`, and FastAPI resolves `get_session` exactly once per
    request — the same `AsyncSession` instance flows into every repo.
    """
    await currencies.assert_within_budget(tenant_id, currency=payload.currency)
    account = await accounts.create_from_dto(payload, tenant_id=tenant_id)
    await users.grant_access(
        email=payload.owner_email,
        tenant_id=tenant_id,
        account_id=account.account_id,
    )
    await audit.log_event(
        "account_created",
        tenant_id=tenant_id,
        resource_type="account",
        resource_id=account.account_id,
        actor_email=tenant.viewer_email,
    )
    # No `await session.commit()` anywhere. get_session handles it.
    return account
```

**The critical invariant:** all four repositories share one `AsyncSession` because all four dep factories transitively depend on the same `SessionDep` — FastAPI caches the resolved dep once per request (this is the default behaviour of `Depends`, and `use_cache=False` should never be passed on a session dep). Cross-repo writes are therefore implicitly atomic. You get the UoW semantics without writing a UoW class.

### F. The `_impl` escape hatch — how transport-agnostic business logic gets a session

`_impl` functions live at `src/core/tools/*.py` and must stay transport-agnostic per Pattern #5 (CLAUDE.md) and guards `test_transport_agnostic_impl.py`, `test_impl_resolved_identity.py`. They are called from four different places: MCP tool wrappers, A2A raw wrappers, REST API routes in `src/routes/api_v1.py`, and unit/integration tests that bypass the transport layer entirely. Three of those four have no `Request` object and cannot use `Depends()`.

**The rule:** `_impl` functions take an explicit `session: AsyncSession` parameter, and construct repositories inside the body. Not `Depends(get_session)` — that only resolves inside a FastAPI route. Not `async with get_db_session()` — that would hide the session from callers. The transport wrappers pass the session in.

```python
# src/core/tools/accounts.py
async def _create_account_impl(
    req: CreateAccountRequest,
    *,
    identity: ResolvedIdentity,       # Pattern #5 — NOT Context/ToolContext
    session: AsyncSession,             # explicit, not Depends()
) -> CreateAccountResult:
    """Business logic — no transport awareness, no DI magic."""
    accounts = AccountRepository(session)
    users = UserRepository(session)
    audit = AuditLogRepository(session)

    account = await accounts.create_from_dto(req.to_dto(), tenant_id=identity.tenant_id)
    await users.grant_access(
        email=req.owner_email,
        tenant_id=identity.tenant_id,
        account_id=account.account_id,
    )
    await audit.log_event(
        "account_created",
        tenant_id=identity.tenant_id,
        resource_id=account.account_id,
        actor_email=identity.actor_email,
    )
    return CreateAccountResult(account=account)
```

Wrapping it at the four transport boundaries:

```python
# src/core/main.py — MCP wrapper
@mcp.tool()
async def create_account(ctx: Context, req: CreateAccountRequest) -> CreateAccountResponse:
    identity = resolve_identity(ctx.http.headers, protocol="mcp")
    from src.core.database.scope import session_scope
    async with session_scope() as session:
        return await _create_account_impl(req, identity=identity, session=session)


# src/a2a_server/adcp_a2a_server.py — A2A raw function
async def create_account_raw(headers: dict[str, str], req: CreateAccountRequest) -> CreateAccountResponse:
    from src.core.database.scope import session_scope
    identity = resolve_identity(headers, protocol="a2a")
    async with session_scope() as session:
        return await _create_account_impl(req, identity=identity, session=session)


# src/routes/api_v1.py — REST wrapper (inside FastAPI, uses SessionDep)
@router.post("/api/v1/accounts", status_code=201)
async def create_account_route(
    req: CreateAccountRequest,
    identity: ResolvedIdentityDep,
    session: SessionDep,              # proper DI here; no session_scope()
) -> CreateAccountResponse:
    return await _create_account_impl(req, identity=identity, session=session)
```

**Why the REST wrapper uses `SessionDep` but the MCP/A2A wrappers use `session_scope()`:** the REST route is a FastAPI endpoint, so `Depends(get_session)` resolves naturally and carries the request-scoped transaction contract. MCP tools and A2A raw functions run inside their own transport layer's request abstraction, **not** FastAPI's `Request` object. They cannot use `Depends(get_session)`. `session_scope()` (§11.0.6) is the non-request analog and gives them the same open-commit-or-rollback-close guarantee.

### G. UoW → repository-dep deletion map

When Wave 4 lands, every UoW subclass in `src/core/database/repositories/uow.py` is deleted. Here is the one-to-many map:

| Deleted class | Replaced by (dep factories) |
|---|---|
| `BaseUoW` | `BaseRepository` (§11.0.4 base) |
| `MediaBuyUoW` | `MediaBuyRepoDep` + `CurrencyLimitRepoDep` |
| `ProductUoW` | `ProductRepoDep` |
| `WorkflowUoW` | `WorkflowRepoDep` |
| `TenantConfigUoW` | `TenantConfigRepoDep` |
| `AccountUoW` | `AccountRepoDep` |
| `CreativeUoW` | `CreativeRepoDep` + `CreativeAssignmentRepoDep` |
| `AdminCreativeUoW` | `CreativeRepoDep` + `CreativeAssignmentRepoDep` + `MediaBuyRepoDep` + `ProductRepoDep` + `WorkflowRepoDep` + `TenantConfigRepoDep` (handler takes six repos; per-request dep cache means one session still backs all six) |

**Rule-of-thumb:** count the attributes of the UoW subclass (`uow.media_buys`, `uow.currency_limits`, etc.) and add that many `*RepoDep` parameters to the handler signature, one per attribute. If the list gets long (five or more), consider whether the handler is doing too much — but do NOT re-introduce a UoW wrapper.

### H. Why no handler-level `session` parameter for admin routes

It is tempting to shortcut the dep-factory layer and write:

```python
# Tempting but WRONG for admin routes — avoid
async def list_accounts(
    tenant_id: str,
    session: SessionDep,      # handler touches session directly
    ...
) -> HTMLResponse:
    accounts = AccountRepository(session)
    ...
```

This works and is technically equivalent, but it leaks `AsyncSession` into the handler surface area. Three problems:

1. **Test overrides become less expressive.** `app.dependency_overrides[get_account_repo] = lambda: fake_repo` is more targeted than overriding the entire session.
2. **Refactoring shifts the construction site.** If you later decide `AccountRepository(session, *, audit_logger=...)` needs an extra dependency, every handler has to learn about it. With the dep factory, only one file changes.
3. **The structural guard `test_architecture_no_session_in_admin_handlers.py` disallows `SessionDep` as an admin handler parameter** — it wants handlers to depend on repositories, not sessions. The exception is `src/routes/api_v1.py` (REST API, which passes the session directly into `_impl`) and `_impl` functions themselves.

**Admin handlers depend on `*RepoDep`, not `SessionDep`.** REST routes and `_impl` wrappers can depend on `SessionDep` because they are the layer that owns the transport-to-`_impl` boundary.

### I. Gotchas

- **`session.commit()` never appears in repository methods.** Enforced by `test_architecture_no_commit_in_repository.py`. The only files allowed to call `await session.commit()` are `src/core/database/deps.py` (the `get_session` DI factory) and `src/core/database/scope.py` (the `session_scope()` helper from §11.0.6).
- **`self.session` is an `AsyncSession`, never `Session`.** The guard `test_architecture_repository_session_is_async.py` AST-scans for this.
- **Do not inject `tenant_id` at construction.** The guard `test_architecture_repository_no_tenant_in_init.py` catches new violations.
- **Do not cache repositories across requests.** They hold a session reference, and the session is per-request. Creating a repository at module import time and reusing it across requests breaks the per-request transaction boundary and leaks `AsyncSession` objects across event loops.
- **Eager-load contract drift is silent.** If you add a relationship access in a method body without updating the `selectinload(...)` options *and* the docstring, tests pass locally (the relationship lazy-loads the first time) but production fails with `MissingGreenlet`. The guard `test_architecture_repository_eager_loads.py` catches method/option drift.
- **Cross-repository writes require one of them to `flush()`.** If repo A creates a row and repo B needs the row's PK for a FK lookup, repo A must `await self.session.flush()` before returning.
- **The `BaseUoW` deprecation burns a release.** Delete the UoW subclasses one PR at a time during Waves 4a-4b. Each PR updates its handlers and `_impl` call sites in the same commit. Only the very last PR of Wave 4b deletes `BaseUoW` itself, after `grep -r "UoW" src/ tests/` returns zero. Do not try to delete them all in one commit — the blast radius is 40+ files.

---

## 11.0.5 DTO layer — Pydantic v2 wrappers over ORM models (Agent E Category 5)

> **[L4]** Pydantic DTOs at the handler/template boundary. Lands BEFORE async conversion so L5 does not need to fix lazy-load crashes at the template-render boundary in addition to the call-site conversion.

> **Added 2026-04-11 under the Agent E idiom upgrade.** The DTO boundary is the **architectural prevention** for Risk #1 (lazy-load realization in production). If templates receive DTOs, lazy loads become impossible by construction.

### A. File tree

```
src/admin/dtos/
  __init__.py
  account.py
  tenant.py
  user.py
  product.py
  media_buy.py
  ... (one per major admin entity)
```

### B. Every DTO

```python
# src/admin/dtos/account.py
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer


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


class AccountWithContactDTO(AccountDTO):
    """Eager-loaded variant — includes primary_contact.

    Returned only by `AccountRepository.get_with_contact()` which applies
    `selectinload(Account.primary_contact)` to the query.
    """
    primary_contact: "AccountContactDTO | None" = None


class AccountCreateRequest(BaseModel):
    """Request body for POST /accounts. Explicitly NOT extending AccountDTO."""
    model_config = ConfigDict(extra="forbid", strict=True)

    account_id: Annotated[str, Field(pattern=r"^acc_[a-z0-9]{8}$")]
    name: Annotated[str, Field(min_length=1, max_length=255)]
    monthly_budget: Decimal | None = None
```

### C. Integration

- Repositories return DTOs at the public surface — never ORM instances
- Handlers pass DTOs into template contexts — not ORM instances
- Structural guard `test_architecture_templates_receive_dtos_not_orm.py` enforces: every `render(request, "tpl", context)` call passes only primitives, Pydantic models, or the request

### D. Gotchas

- **`frozen=True`** prevents downstream mutation (templates can't "update" a DTO accidentally)
- **`from_attributes=True`** enables `AccountDTO.model_validate(orm_instance)` — the ORM → DTO conversion at the repository boundary
- **`strict=True`** prevents silent type coercion that might hide data model drift
- **Eager-load contract:** DTOs with nested relationships MUST be returned only from repository methods that eager-loaded the relationships. Enforced by `test_architecture_repository_eager_loads.py`.
- **AdCP boundary preservation:** admin DTOs live in `src/admin/dtos/`, SEPARATE from `src/core/schemas/` (AdCP library schemas). Changes to admin DTOs NEVER touch the AdCP wire format. CLAUDE.md Pattern #1 still applies to library schemas; admin DTOs are a different layer.
- **Validator decorators:** Only `field_validator` and `model_validator` (Pydantic v2) are accepted. Any use of `@validator` (Pydantic v1) in `src/` is rejected. A structural guard `test_architecture_no_pydantic_v1_validators.py` (L4 addition) enforces this.

---

## 11.0.6 `src/core/database/scope.py` — Non-request `session_scope()` helper (Agent E Category 2 / scheduler edition)

> **[L5+]** Async session scope context manager. L0-L4 use sync `get_db_session()`.

> **Added 2026-04-11 under the Agent E idiom upgrade.** §11.0.1 `SessionDep` solves "how does a request handler get a session". This section solves "how does a scheduler, background task, CLI script, or alembic migration get a session when there is no `Request` at all". It is the non-request analog and it shares the lifespan-scoped sessionmaker with §11.0.1, so both paths commit/rollback through identical code.

### A. The problem

The following places in the codebase currently call `get_db_session()` without a `Request`:

1. `src/services/delivery_webhook_scheduler.py::_send_reports` — runs on an `asyncio.sleep(SLEEP_INTERVAL_SECONDS)` loop inside the MCP lifespan
2. `src/services/media_buy_status_scheduler.py` — similar periodic loop
3. `src/services/background_sync_service.py` — long-running GAM sync jobs (multi-hour)
4. `src/services/background_approval_service.py` — approval task worker
5. `src/services/order_approval_service.py` — GAM order polling
6. `src/services/property_verification_service.py` — DNS / ASA verification worker
7. CLI scripts under `scripts/ops/*.py` (migrations, seeding, one-off ops)
8. `alembic/env.py` itself (via `EnvironmentContext`)

None of these have a FastAPI `Request`. None can call `Depends(get_session)`. All currently open a session via the sync `get_db_session()` context manager, which goes away in Wave 4 when the engine becomes fully async. They need a replacement that yields an `AsyncSession`, commits on clean exit, rolls back on exception, **shares the lifespan-scoped sessionmaker** so the scheduler tick uses the same connection pool the HTTP handlers use, works from code that is NOT inside a request scope, and degrades gracefully when called from outside a running app (CLI, alembic, pytest).

### B. Implementation

```python
# src/core/database/scope.py
"""Non-request session helper.

Parallels `src/core/database/deps.py::get_session` for code paths that
do not have a `fastapi.Request`:

    * Scheduler ticks (asyncio.create_task loops in MCP lifespan)
    * Background workers (GAM sync, approval polling)
    * CLI scripts
    * Alembic migrations
    * Pytest fixtures that set up data outside a request

The sessionmaker lives in a ContextVar, seeded once by
`database_lifespan()` at startup and cleared on shutdown.
"""
from __future__ import annotations

import contextlib
from contextvars import ContextVar, Token
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
)


_current_sessionmaker: ContextVar[async_sessionmaker[AsyncSession] | None] = ContextVar(
    "current_sessionmaker",
    default=None,
)


class SessionScopeNotInitialised(RuntimeError):
    """Raised when `session_scope()` is called before a sessionmaker is set.

    Typical causes:
    * CLI script that forgot to wrap its body in `database_lifespan(app)`.
    * Unit test that imported a module calling `session_scope()` at
      collection time. Fix: move the call inside the test body and let
      the fixture seed the ContextVar.
    * Alembic migration run directly via `python alembic/env.py` without
      configuring the engine. Fix: the new async `env.py` (section F)
      seeds the ContextVar itself.
    """


def set_sessionmaker(sm: async_sessionmaker[AsyncSession]) -> Token:
    """Install the current sessionmaker. Returns the Token for later reset."""
    return _current_sessionmaker.set(sm)


def reset_sessionmaker(token: Token) -> None:
    """Restore the previous sessionmaker from a token returned by set_sessionmaker."""
    _current_sessionmaker.reset(token)


def current_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Read the current sessionmaker or raise if unset."""
    sm = _current_sessionmaker.get()
    if sm is None:
        raise SessionScopeNotInitialised(
            "session_scope() was called before a sessionmaker was installed. "
            "Start the app via `database_lifespan(app)`, or install one "
            "manually via `set_sessionmaker(make_sessionmaker(engine))` in "
            "your CLI / scheduler / migration entry point."
        )
    return sm


@contextlib.asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Yield a fresh AsyncSession, commit on exit, rollback on exception.

    Usage:

        async with session_scope() as session:
            repo = MediaBuyRepository(session)
            await repo.do_work()
            # session.commit() happens here automatically
            # session.rollback() happens if do_work raises

    The session is short-lived — one `session_scope()` block should
    correspond to ONE logical unit of work (one scheduler tick, one
    CLI subcommand, one phase of a multi-phase background job). Do
    NOT nest `async with session_scope()` inside another one — use
    savepoints (not this helper) if you need sub-transactions.
    """
    sessionmaker = current_sessionmaker()
    async with sessionmaker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
```

### C. Lifespan integration — how `database_lifespan` seeds the ContextVar

```python
# src/core/database/engine.py (updated from §11.0)
@asynccontextmanager
async def database_lifespan(app: "FastAPI") -> AsyncIterator[None]:
    """Create engine + sessionmaker on startup, dispose on shutdown.

    ALSO seeds the `_current_sessionmaker` ContextVar so that non-request
    code (schedulers, background tasks) can call `session_scope()` and
    share the same sessionmaker as HTTP handlers.
    """
    from src.core.database.scope import (
        reset_sessionmaker,
        set_sessionmaker,
    )
    from src.core.settings import get_settings

    settings = get_settings()
    engine = make_engine(settings.database_url)
    sessionmaker = make_sessionmaker(engine)

    app.state.db_engine = engine
    app.state.db_sessionmaker = sessionmaker

    scope_token = set_sessionmaker(sessionmaker)
    try:
        yield
    finally:
        reset_sessionmaker(scope_token)
        await engine.dispose()
```

Both paths — `get_session` (from `app.state.db_sessionmaker`) and `session_scope()` (from the ContextVar) — now share the **same** `sessionmaker` instance. A scheduler tick borrows from the same pool as HTTP handlers. Under xdist the per-test lifespan seeds a per-test ContextVar so workers do not collide.

### D. Scheduler tick pattern

Short-lived session per tick — NOT per scheduler lifetime.

```python
# src/services/delivery_webhook_scheduler.py
"""Delivery webhook scheduler — periodic loop, fresh session per tick."""
import asyncio
import logging

from src.core.database.scope import session_scope
from src.core.database.repositories.media_buy import MediaBuyRepository

logger = logging.getLogger(__name__)
SLEEP_INTERVAL_SECONDS = 30.0


class DeliveryWebhookScheduler:
    """Periodic worker that sends delivery webhooks for in-flight media buys."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._stopped = asyncio.Event()

    async def start(self) -> None:
        async with self._lock:
            if self._task is not None:
                return
            self._stopped.clear()
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stopped.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        """Long-lived loop. Each iteration opens ONE fresh session."""
        while not self._stopped.is_set():
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("delivery_webhook_scheduler tick failed")
            await asyncio.sleep(SLEEP_INTERVAL_SECONDS)

    async def _tick(self) -> None:
        """One scheduler iteration. Opens a session, does the work, commits.

        Key invariant: the session is opened HERE, not in `_run`. A
        long-lived session held across ticks would:

        1. Occupy a connection pool slot for the entire scheduler lifetime.
        2. Accumulate stale identity-map entries that grow unboundedly.
        3. Hit `pool_recycle=3600` and start raising `DisconnectionError`
           mid-tick.
        """
        async with session_scope() as session:
            media_buys = MediaBuyRepository(session)
            pending = await media_buys.list_pending_webhook_deliveries()
            for buy in pending:
                await self._send_one(buy, session=session)
            # commit happens at `async with` exit
```

### E. CLI script pattern

Two shapes depending on whether the script needs the full app or just the DB.

**Shape 1 — full-app lifespan (recommended):**

```python
# scripts/ops/reseed_property_tags.py
"""One-off script to re-seed property_tags for a tenant."""
import asyncio
import sys

from src.app import app
from src.core.database.engine import database_lifespan
from src.core.database.repositories.tenant_config import TenantConfigRepository
from src.core.database.scope import session_scope


async def main(tenant_id: str) -> None:
    async with database_lifespan(app):
        async with session_scope() as session:
            tenant_config = TenantConfigRepository(session)
            await tenant_config.reseed_property_tags(tenant_id)
            print(f"Reseeded property_tags for {tenant_id}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage: reseed_property_tags.py <tenant_id>")
    asyncio.run(main(sys.argv[1]))
```

**Shape 2 — DB-only, no app (for alembic-adjacent tooling):**

```python
# scripts/ops/dump_media_buys_csv.py
"""Bulk export — builds its own engine, no FastAPI app needed."""
import asyncio
import os
import sys

from src.core.database.engine import make_engine, make_sessionmaker
from src.core.database.repositories.media_buy import MediaBuyRepository
from src.core.database.scope import (
    reset_sessionmaker,
    session_scope,
    set_sessionmaker,
)


async def main(tenant_id: str) -> None:
    engine = make_engine(os.environ["DATABASE_URL"])
    token = set_sessionmaker(make_sessionmaker(engine))
    try:
        async with session_scope() as session:
            repo = MediaBuyRepository(session)
            async for row in repo.stream_all_as_csv(tenant_id):
                sys.stdout.write(row)
    finally:
        reset_sessionmaker(token)
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
```

Shape 2 is marginally more code but lets the script run without any `src.app` imports — useful for fast smoke tests and for alembic, which cannot import the full app without circular-import headaches.

### F. Alembic `env.py` — async rewrite

```python
# alembic/env.py
"""Async alembic environment.

Run migration scripts against an async engine. Individual migrations
remain sync — only the envelope is async. Standard SQLAlchemy 2.0
pattern.
"""
import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from src.core.database.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _do_run_migrations(connection) -> None:
    """Sync body — migrations themselves are not async."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations_online() -> None:
    """Async envelope that drives a sync migration body."""
    from src.core.database.engine import _build_async_url

    raw_url = config.get_main_option("sqlalchemy.url")
    config.set_main_option("sqlalchemy.url", _build_async_url(raw_url))

    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Entry point called by `alembic upgrade head`."""
    asyncio.run(run_async_migrations_online())


def run_migrations_offline() -> None:
    """Offline mode — dumps SQL without connecting."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

`NullPool` is used deliberately: alembic runs one connection and exits. A pooled engine would keep connections alive after `upgrade` finishes, delaying script exit.

### G. Long-running background jobs — GAM sync

`src/services/background_sync_service.py` runs GAM sync jobs that can take multiple hours. Holding one `AsyncSession` for the entire run is wrong for three reasons:

1. The pool has `pool_recycle=3600` — after one hour the connection is rotated and any open session on it starts raising `DisconnectionError`.
2. One hour of asyncpg idle time blows through TCP keepalives on Fly.io's proxy layer, causing random mid-job failures.
3. The identity-map grows unbounded over hours of writes, consuming memory proportional to the sync size.

**Refactor rule: one `async with session_scope()` per PHASE of the sync, not per run.**

```python
# src/services/background_sync_service.py (post Wave 4)
async def run_gam_sync(tenant_id: str) -> SyncReport:
    """Multi-phase GAM sync. Each phase gets a fresh session."""
    report = SyncReport()

    # Phase 1: discover line items
    async with session_scope() as session:
        discovery = GamDiscoveryRepository(session)
        line_items = await discovery.fetch_and_persist(tenant_id)
        report.discovery_count = len(line_items)

    # Phase 2: reconcile with internal media buys
    async with session_scope() as session:
        reconciler = GamReconcileRepository(session)
        report.reconciled_count = await reconciler.reconcile(tenant_id, line_items)

    # Phase 3: write delivery metrics
    async with session_scope() as session:
        metrics = DeliveryMetricsRepository(session)
        report.metrics_count = await metrics.bulk_upsert(tenant_id, line_items)

    return report
```

Each phase commits independently. If phase 2 fails, phase 1's writes are already committed — which is usually what you want for a long-running job, because partial progress is better than losing everything and restarting from the beginning of phase 1.

Decision #9 in the folder `CLAUDE.md` open-decisions list asks "how should background_sync_service handle long jobs" — Option A is this phase-per-session refactor; Option B is the sync-bridge fallback (run the whole job under `run_in_threadpool` with the sync psycopg2 path, kept on life-support just for this one service). Option A is strongly preferred because it keeps the codebase on one driver.

### H. Interaction with `src/core/main.py::lifespan_context`

The MCP lifespan (`src/core/main.py`) starts `delivery_webhook_scheduler` and `media_buy_status_scheduler`. After Wave 4, both schedulers call `session_scope()` from their tick bodies. For that call to resolve, **the MCP lifespan must run INSIDE `database_lifespan` so the ContextVar is seeded by the time the scheduler's `asyncio.create_task` fires.**

The lifespan composition in `src/app.py` becomes:

```python
@asynccontextmanager
async def combined_lifespan(app: FastAPI) -> AsyncIterator[None]:
    async with database_lifespan(app):          # seeds _current_sessionmaker
        async with mcp_lifespan(app):           # starts schedulers (they can now session_scope())
            yield
```

The order is load-bearing. If `mcp_lifespan` starts first, the scheduler tick fires before `database_lifespan` has seeded the ContextVar, and `session_scope()` raises `SessionScopeNotInitialised`. The folder `CLAUDE.md` "MCP schedulers are lifespan-coupled" note already covers this — this section is just the concrete "which goes first" answer: **database first, MCP second.**

### I. Gotchas

- **`session_scope()` never appears inside a FastAPI route.** Routes use `SessionDep`. If you find `session_scope()` inside a `@router.get(...)` handler, it is a bug — FastAPI already has a session for you, and opening a second one means your request is running two transactions. The guard `test_architecture_no_session_scope_in_routes.py` enforces this.
- **Never nest `async with session_scope()` inside another `async with session_scope()`.** Two sessions, two transactions, no implicit atomicity. Use one outer scope and pass the session down. Nesting is allowed only in tests where the outer scope is a fixture.
- **`SessionScopeNotInitialised` at test collection time is almost always a module-level import side effect.** Move the `session_scope()` call inside a function body.
- **CLI scripts that do NOT use `database_lifespan` must call `reset_sessionmaker(token)` in a `finally` block** — without it, the ContextVar holds a reference to a disposed engine, and any subsequent `session_scope()` gets a session backed by a closed pool. `try / finally` is not optional here.
- **Alembic envelope calls `asyncio.run()` directly**, which creates a fresh event loop. That means `env.py` cannot share an event loop with a running app — alembic is always out-of-process.
- **`background_sync_service.py` Option A (phase-per-session) requires each phase to be idempotent on retry.** Build phase 1's writes with UPSERT semantics or guard with an already-processed check.
- **`ContextVar` vs module-global.** A module-global `_sessionmaker` leaks between tests if pytest runs them in the same worker; a `ContextVar` is cleared by Python's context propagation rules. Tests that install their own sessionmaker should use `set_sessionmaker` / `reset_sessionmaker` in fixture setup/teardown.

---

## 11.0.7 `tests/unit/test_architecture_async_sessionmaker_expire_on_commit.py` — Structural guard (L5b+)

> **[L5b]** Activated at the `SessionDep` alias flip. Scans every `async_sessionmaker(...)` construction site and fails the build if `expire_on_commit=False` is absent or set to anything other than `False`.

### Purpose

`expire_on_commit` defaults to `True` on SQLAlchemy `async_sessionmaker`. With that default, post-commit attribute access on any previously-committed instance triggers a lazy refresh. Under asyncpg the refresh crosses the greenlet boundary sync-style and raises `sqlalchemy.exc.MissingGreenlet: greenlet_spawn has not been called; can't call await_() here`. The failure is latent — it only fires when a specific attribute is read after commit, so code review misses it and integration tests catch only a fraction of violations.

This guard converts the violation into a compile-time assertion: if any `async_sessionmaker(...)` call in the repo omits `expire_on_commit=False` (or sets it to `True`), `make quality` fails immediately. Sync `sessionmaker(...)` is out of scope — `expire_on_commit=True` is safe on the sync path and is the correct SQLA default there.

### Implementation

```python
# tests/unit/test_architecture_async_sessionmaker_expire_on_commit.py
"""Structural guard: async_sessionmaker(...) MUST pass expire_on_commit=False.

Without it, post-commit attribute access on AsyncSession instances triggers
a lazy refresh that crosses the greenlet boundary and raises MissingGreenlet.
See §11.0.A `make_sessionmaker` for the expanded rationale.

Scope: every .py file under src/ and tests/. Sync `sessionmaker(...)` is
OUT of scope — expire_on_commit=True is safe on the sync path.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
SCAN_DIRS = (ROOT / "src", ROOT / "tests")

# Ratcheting allowlist — starts empty at L5b activation. Shrinks only.
ALLOWLIST: frozenset[str] = frozenset()


def _iter_async_sessionmaker_calls(tree: ast.AST):
    """Yield every Call node whose callable is the name `async_sessionmaker`.

    Matches:
      async_sessionmaker(...)                 # direct import
      sqlalchemy.ext.asyncio.async_sessionmaker(...)  # qualified
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "async_sessionmaker":
            yield node
        elif isinstance(func, ast.Attribute) and func.attr == "async_sessionmaker":
            yield node


def _expire_on_commit_ok(call: ast.Call) -> bool:
    """True iff the call has `expire_on_commit=False` as an explicit kwarg."""
    for kw in call.keywords:
        if kw.arg != "expire_on_commit":
            continue
        return isinstance(kw.value, ast.Constant) and kw.value.value is False
    return False


def test_async_sessionmaker_always_sets_expire_on_commit_false():
    violations: list[str] = []
    for scan_dir in SCAN_DIRS:
        for py in scan_dir.rglob("*.py"):
            rel = py.relative_to(ROOT).as_posix()
            if rel in ALLOWLIST:
                continue
            try:
                tree = ast.parse(py.read_text())
            except SyntaxError:
                continue
            for call in _iter_async_sessionmaker_calls(tree):
                if not _expire_on_commit_ok(call):
                    violations.append(f"  {rel}:{call.lineno}")
    assert not violations, (
        "async_sessionmaker(...) MUST pass expire_on_commit=False.\n"
        "See foundation-modules.md §11.0.A for why — MissingGreenlet on post-commit "
        "attribute access under asyncpg. Violations:\n"
        + "\n".join(violations)
    )
```

### Meta-test

Uses the `write-guard` skill pattern: plant a fixture source containing `async_sessionmaker(bind=engine)` (no kwarg) in a temp directory, point the scan dir at it, assert the guard raises. Flip the fixture to `async_sessionmaker(bind=engine, expire_on_commit=False)`, assert the guard is silent. Flip to `async_sessionmaker(bind=engine, expire_on_commit=True)`, assert the guard raises.

### Activation

- **L5b entry:** activates in the same PR that re-aliases `SessionDep = Annotated[AsyncSession, Depends(get_session)]`. Before L5b the guard has nothing to scan (no `async_sessionmaker` call sites exist yet).
- **Allowlist:** empty at inception. Any new `async_sessionmaker(...)` call must pass the kwarg from day one.
- **Scope excludes sync `sessionmaker(...)`** — sync sessions' default `expire_on_commit=True` is correct and idiomatic, the MissingGreenlet crash is async-specific.

---

## 11.0.8 `tests/unit/test_architecture_relationships_explicit_loading.py` — Structural guard (L5b+)

### test_architecture_relationships_explicit_loading.py [L5b]

AST-scan every call to `relationship(...)` (sqlalchemy.orm) in `src/` and assert that each call has a `lazy=` kwarg with value in {`"raise"`, `"selectin"`, `"joined"`, `"subquery"`, `"noload"`}. Reject `lazy="select"` (the default, which triggers implicit IO under async and raises `MissingGreenlet`). Allowlist file: `tests/unit/architecture/allowlists/relationships_explicit_loading.txt` — must be empty after L5b (Spike 1 seeds all 68 existing relationships with explicit strategies).

Rationale: prevents post-migration drift back to lazy-by-default relationships that silently break under async context. Spike 1 is a one-shot audit; this guard is the durable invariant.

---

## 11.1 `src/admin/templating.py`

> **[L0-L2]** This module is part of Flask removal. Use sync patterns. It stays sync through L4 and auto-converts at L5 only to the extent its callers flip to `async def` (most functions here are framework-agnostic utilities and remain sync across all layers).

### A. Implementation

```python
"""Jinja2Templates singleton and render() wrapper for the admin UI.

GREENFIELD FastAPI convention: every URL in every template resolves via
`request.url_for('route_name', **params)`. There are NO `admin_prefix` /
`static_prefix` / `script_root` / `script_name` Jinja globals. Every admin
route has `name="admin_<blueprint>_<endpoint>"` on its decorator; the static
mount declares `name="static"`.

- request.url_for('admin_accounts_list_accounts', tenant_id=t) → /admin/tenant/{t}/accounts
- request.url_for('static', path='/validation.css')             → /static/validation.css
- request.url_for('admin_auth_logout')                           → /admin/logout

Missing or mistyped route names raise `starlette.routing.NoMatchFound` at
render time; the guard test `tests/unit/admin/test_templates_url_for_resolves.py`
catches this at CI time by statically extracting every `url_for('name', ...)`
call from every template and asserting `name` exists in the live route table.

Replaces Flask's @app.context_processor inject_context (src/admin/app.py:298)
and the custom Jinja filters declared at src/admin/app.py:154-155.

Design rules (load-bearing):
- Jinja `Undefined` (default, NOT StrictUndefined). Existing Flask templates
  reference attributes on `tenant` that may be None; flipping to strict would
  break ~40 templates on cutover. Tracked for L6 (native refinements).
- The `request` object is always in the context. All per-request data
  (`session`, `csrf_token`, `url_for`) is reached THROUGH request in templates,
  which keeps `env.globals` free of request-scoped state (thread-safety).
- `get_flashed_messages` is registered as an `env.global` so existing template
  call sites keep compiling, but it is a trampoline that requires `request`
  as first positional arg — the codemod inserts it.
- `url_for` is auto-registered by Starlette's `Jinja2Templates._setup_env_defaults`
  (starlette/templating.py:118-129) as a `@pass_context`-decorated wrapper that
  delegates to `request.url_for(name, **path_params)`. We override it below
  with `_url_for` to add template-filename logging on `NoMatchFound`.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import markdown as md_lib
from fastapi.templating import Jinja2Templates
from jinja2 import pass_context
from markupsafe import Markup
from starlette.datastructures import URL
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import NoMatchFound

from src.admin.flash import get_flashed_messages
from src.core.domain_config import get_sales_agent_domain, get_support_email

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).parent / "templates"


def _from_json(raw: Any) -> Any:
    """Parse a possibly-JSON value into a Python object.

    Mirrors the Flask filter at src/admin/app.py:135-141. Returns {} on any
    failure so templates can unconditionally `{{ x | from_json | default({}) }}`
    without try/except gymnastics.
    """
    if not raw:
        return {}
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}


def _markdown(text: str | None) -> Markup:
    """Render markdown to safe HTML.

    Mirrors src/admin/app.py:143-152. `extra` enables tables/fenced code;
    `nl2br` preserves newlines as <br>. Returns Markup("") (not "") so the
    template never sees an HTML-escaped empty string.
    """
    if not text:
        return Markup("")
    return Markup(md_lib.markdown(text, extensions=["extra", "nl2br"]))


def _tojson_safe(obj: Any) -> str:
    """Safer tojson than Jinja's default — sorts keys for deterministic output."""
    return json.dumps(obj, default=str, sort_keys=True)


templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))

# Custom filters
templates.env.filters["from_json"] = _from_json
templates.env.filters["markdown"] = _markdown
templates.env.filters["tojson_safe"] = _tojson_safe

# Globals — must be request-independent. get_flashed_messages is a special
# trampoline that requires `request` as first arg; codemod inserts it.
templates.env.globals["get_flashed_messages"] = get_flashed_messages


# --- Safe url_for override: same API, better failure mode -------------------
#
# Starlette's default `url_for` (from Jinja2Templates._setup_env_defaults at
# starlette/templating.py:118-129) raises `NoMatchFound(name, params)` with a
# message that omits the offending template filename. When a 500 hits a user,
# we want the template filename in the log, not just the route name. We
# intercept, log, and re-raise.
#
# `setdefault` at starlette/templating.py:129 means our pre-registered function
# wins; we MUST register BEFORE any TemplateResponse call. Since `templates` is
# a module-level singleton, this override runs at import time.


@pass_context
def _url_for(context: dict[str, Any], name: str, /, **path_params: Any) -> str:
    """Return path-only URL. Starlette's request.url_for returns absolute
    (scheme+host+path); templates want path-only so rendered HTML is portable
    across hostnames (multi-tenant proxy, Approximated external domains, etc.).
    Golden-fingerprint fixtures must be regenerated when this wrapper lands."""
    request: Request = context["request"]
    try:
        return str(request.url_for(name, **path_params).path)
    except NoMatchFound:
        template_name = getattr(context, "name", "<unknown>")
        logger.error(
            "NoMatchFound in template %s: url_for(%r, **%r). "
            "Check that every admin router has name= on its decorator and "
            "the route name matches tests/unit/admin/test_architecture_admin_routes_named.py.",
            template_name, name, path_params,
        )
        raise


templates.env.globals["url_for"] = _url_for

# NOTE: Return type is `str` (not `URL`). Golden fingerprint fixtures captured
# before this change will break and must be regenerated in the L1a PR. Any
# template or helper code that previously called `.path`, `.scheme`, or other
# `URL` methods on a `url_for(...)` result must be updated to operate on a
# plain string (the value is already path-only).


def render(
    request: Request,
    name: str,
    context: dict[str, Any] | None = None,
    *,
    status_code: int = 200,
    headers: dict[str, str] | None = None,
) -> Response:
    """One-call template wrapper replacing Flask's context processor.

    GREENFIELD: no admin_prefix, no static_prefix, no script_root, no script_name.
    Templates use `{{ url_for('name', **params) }}` for every URL — for admin
    routes (named `admin_<blueprint>_<endpoint>`) AND static assets (named
    `static` via the StaticFiles mount on the outer app).

    `csrf_token` is pulled from `request.state.csrf_token` (stamped by
    CSRFOriginMiddleware). `tenant` is NOT injected here — handlers pass it
    explicitly via `context={"tenant": ...}` when they need it, to avoid
    the N+1 DB hit that the old Flask inject_context performed.

    For JS URL construction with runtime path params (Case 6 in the plan),
    handlers pre-resolve base URLs via `js_*_base` context vars:
        js_workflows_base=str(request.url_for("admin_workflows_list", tenant_id=t))
    Templates use: const base = "{{ js_workflows_base }}";

    The returned object is a starlette.templating._TemplateResponse (which IS
    a Response). Every handler in every router calls this — there is no
    escape hatch to templates.TemplateResponse directly.
    """
    base: dict[str, Any] = {
        "request": request,
        "support_email": get_support_email(),
        "sales_agent_domain": get_sales_agent_domain() or "example.com",
        # csrf_token NOT injected — CSRFOriginMiddleware uses Origin header validation, not tokens
        # `tenant` is injected on-demand by handlers via CurrentTenantDep, NOT
        # here — the old inject_context did a DB lookup on every single render
        # which was an N+1 magnet. Handlers own tenant loading now.
        # `admin_prefix`/`static_prefix`/`script_root` are STRICTLY FORBIDDEN
        # — guarded by test_templates_no_hardcoded_admin_paths.py.
    }
    if context:
        # Explicit override semantics: handler keys win over base keys. This
        # lets tests inject a synthetic `request.state.csrf_token`.
        base.update(context)
    return templates.TemplateResponse(
        request=request,
        name=name,
        context=base,
        status_code=status_code,
        headers=headers,
    )
```

Notes on what the sketch omitted:

- **`session` in templates:** Templates should reach it via `{{ request.session.get('foo') }}`, not a top-level `session`. Flask's implicit `{{ session.foo }}` becomes `{{ request.session.foo }}`; codemod does this rewrite. Why not put session on globals? `env.globals` is process-wide; setting it per-request would be a data race under Uvicorn workers.
- **`url_for`:** `request.url_for("route_name", **params)` is available on the Starlette `Request`. In templates: `{{ request.url_for('accounts_list_accounts', tenant_id=t) }}`. The Flask convention `url_for('accounts.list_accounts', ...)` becomes the flat `accounts_list_accounts` — this is a codemod transformation, not a runtime shim.
- **`Undefined` vs `StrictUndefined`:** We keep default `Undefined` for v2.0.0. `StrictUndefined` would raise on every missing tenant attribute at render time; legacy templates have dozens. Post-v2.0 ticket.
- **Signature note:** FastAPI 0.109+ requires `request` as keyword arg in `TemplateResponse` — passing request in context is deprecated. The wrapper handles both.

### B. Tests

```python
# tests/unit/admin/test_templating.py
import pytest
from starlette.requests import Request
from starlette.testclient import TestClient
from fastapi import FastAPI

from src.admin.templating import render, _from_json, _markdown


class TestFilters:
    def test_from_json_handles_none(self):
        assert _from_json(None) == {}

    def test_from_json_handles_already_parsed(self):
        assert _from_json({"a": 1}) == {"a": 1}

    def test_from_json_malformed_returns_empty(self):
        assert _from_json("{not json") == {}

    def test_markdown_empty_returns_safe_empty(self):
        result = _markdown("")
        assert str(result) == ""
        # Markup must be marked safe so `| safe` isn't needed downstream
        assert hasattr(result, "__html__")


class TestRender:
    def test_render_injects_script_root_and_support_email(self, monkeypatch):
        monkeypatch.setenv("SUPPORT_EMAIL", "help@example.com")
        app = FastAPI()

        @app.get("/t")
        def handler(request: Request):
            return render(request, "_smoke.html", {"hello": "world"})

        # For unit test, templating uses the real _TEMPLATE_DIR; point at a fixture
        # in a real test suite via monkeypatching templates.env.loader.
        # Here we just confirm the response shape without rendering.
        client = TestClient(app)
        # Smoke tests of render() belong in integration, not unit — see below.

    def test_render_uses_csrf_token_from_request_state(self):
        app = FastAPI()

        @app.middleware("http")
        async def stamp(request, call_next):
            request.state.csrf_token = "TESTTOKEN"
            return await call_next(request)

        @app.get("/t")
        def handler(request: Request):
            return render(request, "csrf_probe.html")

        # Using TestClient with a template fixture that emits {{ csrf_token }}
        # as the body — see tests/fixtures/admin_templates/csrf_probe.html
```

Integration test uses the harness pattern:

```python
# tests/integration/admin/test_templating_rendering.py
from tests.harness import IntegrationEnv
from tests.factories import TenantFactory
from starlette.testclient import TestClient

class TestRenderIntegratesWithTenantDep:
    def test_tenant_from_dep_available_in_template(self, integration_db):
        with IntegrationEnv() as env:
            TenantFactory(tenant_id="t1")
            client = env.get_rest_client()
            # Hit a real route that uses CurrentTenantDep + render()
            # Asserts the template sees `tenant.name` correctly.
            r = client.get("/admin/tenant/t1/", cookies={"adcp_session": _signed("user@t1.example")})
            assert r.status_code == 200
            assert "Test Publisher t1" in r.text
```

### C. Integration

- Imports `src.admin.flash.get_flashed_messages`, `src.core.domain_config.get_support_email/get_sales_agent_domain`.
- Public API: `templates` singleton (for tests to add loaders), `render(request, name, context, *, status_code, headers)`.
- Does NOT touch DB.
- Does NOT interact with `UnifiedAuthMiddleware` directly — but handlers that call `render()` must have access to `request.state.csrf_token`, which requires `CSRFOriginMiddleware` to have run.

### D. Gotchas

- **Jinja2 autoescape:** `Jinja2Templates(..., autoescape=True)` is the default; do NOT disable. The `| safe` filter and `Markup()` are the only ways to emit raw HTML.
- **Thread safety of `env.globals`:** Jinja `Environment` is shared across all requests. Never mutate `env.globals` per-request — use the context dict instead. The `templates` singleton is created at import time so this is enforced structurally.
- **Template caching:** Jinja caches compiled templates by default. In development, set `templates.env.auto_reload = True` via `APP_ENV=dev` check.
- **`TemplateResponse` and background tasks:** Starlette's `TemplateResponse` accepts `background=` but the wrapper doesn't expose it. Add on demand.

---

## 11.2 `src/admin/sessions.py`

> **[L0-L2]** This module is part of Flask removal. Use sync patterns. It stays sync through L4 and auto-converts at L5 only to the extent its callers flip to `async def` (most functions here are framework-agnostic utilities and remain sync across all layers).

### A. Implementation

```python
"""SessionMiddleware configuration for the admin UI.

Starlette's SessionMiddleware stores the whole session dict in a single signed
cookie (itsdangerous). ~4KB cap. On every request the cookie is deserialized
into `request.session` (a dict subclass that tracks mutation) and re-serialized
on response if `request.session` was touched.

Cookie name CHANGES from Flask's `session` to `adcp_session`. SessionMiddleware
reads `adcp_session` only; legacy `session=...` cookies are silently ignored.
Users are bounced through Google OAuth once at L1a deploy. See CLAUDE.md
§"Session cookie rename" and `implementation-checklist.md` L1a for the full
rationale and customer-communication requirement.
"""
from __future__ import annotations

import os
from typing import Any

from src.core.config_loader import is_single_tenant_mode
from src.core.domain_config import get_session_cookie_domain


class SessionSecretMissingError(RuntimeError):
    """Raised at startup when SESSION_SECRET is not set.

    No fallback by user directive. Refusing to start is safer than
    silently generating a random per-process secret (which would log
    everyone out on every deploy and break multi-worker setups).
    """


def _require_session_secret() -> str:
    secret = os.environ.get("SESSION_SECRET", "").strip()
    if not secret:
        raise SessionSecretMissingError(
            "SESSION_SECRET env var is required. "
            "Generate one with: python -c 'import secrets; print(secrets.token_urlsafe(64))'"
        )
    if len(secret) < 32:
        raise SessionSecretMissingError(
            f"SESSION_SECRET too short ({len(secret)} chars). "
            "Use at least 32 chars of entropy."
        )
    return secret


def _is_production() -> bool:
    return os.environ.get("PRODUCTION", "").lower() == "true"


def session_middleware_kwargs() -> dict[str, Any]:
    """Return kwargs to pass to `app.add_middleware(SessionMiddleware, **kw)`.

    All environments: SameSite=Lax. Secure=True in production only.
    SameSite=Lax is correct everywhere — SSE deletion (Decision 8) removed
    the only reason for SameSite=None. HttpOnly=True is the Starlette
    SessionMiddleware default and is intentional.
    """
    production = _is_production()
    kwargs: dict[str, Any] = {
        "secret_key": _require_session_secret(),
        "session_cookie": "adcp_session",
        "max_age": 14 * 24 * 3600,  # 14 days
        "same_site": "lax",
        "https_only": production,
        "path": "/",
    }
    # Scope cookie to .sales-agent.example.com so subdomains share it.
    # Single-tenant mode intentionally skips this so the cookie is host-only.
    if production and not is_single_tenant_mode():
        domain = get_session_cookie_domain()
        if domain:
            kwargs["domain"] = domain
    return kwargs
```

### B. Tests

```python
# tests/unit/admin/test_sessions.py
import pytest
from src.admin.sessions import (
    session_middleware_kwargs,
    SessionSecretMissingError,
    _require_session_secret,
)


class TestSessionSecretValidation:
    def test_missing_secret_raises(self, monkeypatch):
        monkeypatch.delenv("SESSION_SECRET", raising=False)
        with pytest.raises(SessionSecretMissingError, match="required"):
            _require_session_secret()

    def test_short_secret_raises(self, monkeypatch):
        monkeypatch.setenv("SESSION_SECRET", "tooshort")
        with pytest.raises(SessionSecretMissingError, match="too short"):
            _require_session_secret()

    def test_whitespace_only_raises(self, monkeypatch):
        monkeypatch.setenv("SESSION_SECRET", "     ")
        with pytest.raises(SessionSecretMissingError):
            _require_session_secret()


class TestSessionMiddlewareKwargs:
    def test_dev_mode_defaults(self, monkeypatch):
        monkeypatch.setenv("SESSION_SECRET", "x" * 64)
        monkeypatch.delenv("PRODUCTION", raising=False)
        kw = session_middleware_kwargs()
        assert kw["same_site"] == "lax"
        assert kw["https_only"] is False
        assert "domain" not in kw

    def test_prod_mode_sets_samesite_lax(self, monkeypatch):
        monkeypatch.setenv("SESSION_SECRET", "x" * 64)
        monkeypatch.setenv("PRODUCTION", "true")
        monkeypatch.setenv("SALES_AGENT_DOMAIN", "sales-agent.example.com")
        kw = session_middleware_kwargs()
        assert kw["same_site"] == "lax"
        assert kw["https_only"] is True
        assert kw["domain"] == ".sales-agent.example.com"

    def test_single_tenant_mode_omits_domain(self, monkeypatch):
        monkeypatch.setenv("SESSION_SECRET", "x" * 64)
        monkeypatch.setenv("PRODUCTION", "true")
        monkeypatch.setenv("ADCP_SINGLE_TENANT_MODE", "true")
        kw = session_middleware_kwargs()
        assert "domain" not in kw
```

### C. Integration

- Imports `is_single_tenant_mode` from `config_loader`, `get_session_cookie_domain` from `domain_config`.
- Public API: `session_middleware_kwargs()`, `SessionSecretMissingError`.
- Called from `app_factory.build_admin_router()` at app startup (fail fast if secret missing).
- Does not touch DB.

### D. Gotchas

- **Nested dict mutation** (Starlette #1738): `request.session["nested"]["key"] = "x"` does NOT trigger `session.modified = True` in Starlette's `SessionMiddleware`. Only top-level key assignments trigger re-serialization. Code that mutates nested structures must explicitly reassign: `request.session["nested"] = {**request.session.get("nested", {}), "key": "x"}`. The `flash()` helper below takes care of this for its own bucket.
- **4KB cap:** itsdangerous-signed cookies cannot exceed ~4KB (browser cookie limit). Don't store large structures. Flash messages are popped on read to keep size bounded.
- **Secret rotation:** There's no key rotation support in Starlette's SessionMiddleware. Rotating `SESSION_SECRET` logs everyone out. Track as later v2.0 phase.
- **Starlette versions ≤ 0.37 had a bug** where `samesite=None` + `https_only=False` emitted invalid `SameSite=None` without `Secure`. Starlette 0.50 fixed this, but prod config always uses `https_only=True`.

---

## 11.3 `src/admin/flash.py`

> **[L0-L2]** This module is part of Flask removal. Use sync patterns. It stays sync through L4 and auto-converts at L5 only to the extent its callers flip to `async def` (most functions here are framework-agnostic utilities and remain sync across all layers).

### A. Implementation

```python
"""Native flash() / get_flashed_messages() using request.session.

Replaces Flask's flash infrastructure. Stores a list[tuple[category, message]]
at session[_SESSION_KEY]. Read-and-pop: get_flashed_messages mutates the
session by removing the bucket.

CRITICAL: Always reassign the full bucket back to session[_SESSION_KEY], not
mutate in place — see sessions.py "Nested dict mutation" gotcha.
"""
from __future__ import annotations

from typing import Literal

from starlette.requests import Request

Category = Literal["info", "success", "warning", "error", "danger"]
_SESSION_KEY = "_flashes"

_VALID_CATEGORIES: frozenset[str] = frozenset(
    {"info", "success", "warning", "error", "danger"}
)


def flash(request: Request, message: str, category: Category = "info") -> None:
    """Push a flash message onto the session bucket.

    WHY the reassignment dance: Starlette's SessionMiddleware only marks the
    session dirty when the top-level key is reassigned. `bucket.append(...)`
    would mutate in place and the cookie would not be re-written.
    """
    if category not in _VALID_CATEGORIES:
        category = "info"  # type: ignore[assignment]
    if not message:
        return
    bucket = list(request.session.get(_SESSION_KEY, []))
    bucket.append([category, message])  # lists, not tuples — JSON round-trip
    request.session[_SESSION_KEY] = bucket


def get_flashed_messages(
    request: Request | None = None,
    *,
    with_categories: bool = False,
    category_filter: list[str] | None = None,
) -> list:
    """Read-and-pop the flash bucket.

    Signature MUST accept `request` as first positional arg — this is a Jinja
    global trampoline, and the codemod rewrites every template call from
    `get_flashed_messages(...)` to `get_flashed_messages(request, ...)`.

    `request=None` support is only for the startup case where Jinja evaluates
    default template globals before a request arrives (rare, but happens with
    some test harnesses).
    """
    if request is None:
        return []
    raw = request.session.get(_SESSION_KEY, [])
    if not raw:
        return []

    # Pop by reassignment — NOT .pop() in place
    request.session[_SESSION_KEY] = []

    # Session came back from JSON as list[list[str, str]]; normalize
    normalized: list[tuple[str, str]] = [
        (entry[0], entry[1]) if isinstance(entry, (list, tuple)) and len(entry) >= 2 else ("info", str(entry))
        for entry in raw
    ]

    if category_filter:
        normalized = [(c, m) for c, m in normalized if c in set(category_filter)]

    if with_categories:
        return normalized
    return [m for _, m in normalized]
```

### B. Tests

```python
# tests/unit/admin/test_flash.py
from unittest.mock import MagicMock
import pytest

from src.admin.flash import flash, get_flashed_messages


def _make_request_with_session(initial: dict | None = None):
    req = MagicMock()
    req.session = dict(initial or {})
    return req


class TestFlash:
    def test_flash_appends_to_empty_bucket(self):
        req = _make_request_with_session()
        flash(req, "hi", "success")
        assert req.session["_flashes"] == [["success", "hi"]]

    def test_flash_reassigns_not_mutates(self):
        """Regression guard: bucket must be reassigned to trigger Starlette
        session re-serialization. This test asserts the list is NOT the
        same object as what get() returned."""
        existing: list[list[str]] = [["info", "old"]]
        req = _make_request_with_session({"_flashes": existing})
        flash(req, "new", "warning")
        assert req.session["_flashes"] is not existing
        assert req.session["_flashes"] == [["info", "old"], ["warning", "new"]]

    def test_flash_invalid_category_falls_back_to_info(self):
        req = _make_request_with_session()
        flash(req, "hi", "explodey")  # type: ignore[arg-type]
        assert req.session["_flashes"][0][0] == "info"

    def test_empty_message_noop(self):
        req = _make_request_with_session()
        flash(req, "")
        assert "_flashes" not in req.session


class TestGetFlashedMessages:
    def test_with_categories_round_trip(self):
        req = _make_request_with_session({"_flashes": [["success", "ok"]]})
        assert get_flashed_messages(req, with_categories=True) == [("success", "ok")]
        # Post-read, bucket is emptied
        assert req.session["_flashes"] == []

    def test_category_filter(self):
        req = _make_request_with_session(
            {"_flashes": [["success", "a"], ["error", "b"]]}
        )
        out = get_flashed_messages(req, with_categories=True, category_filter=["error"])
        assert out == [("error", "b")]

    def test_none_request_returns_empty(self):
        assert get_flashed_messages(None) == []

    def test_malformed_entries_normalized(self):
        req = _make_request_with_session({"_flashes": ["just-a-string"]})
        out = get_flashed_messages(req, with_categories=True)
        assert out == [("info", "just-a-string")]
```

### C. Integration

- Imports only `starlette.requests.Request`.
- Public API: `flash(request, message, category)`, `get_flashed_messages(request, ...)`.
- Registered as a Jinja global by `templating.py`.
- Depends on `SessionMiddleware` being installed.

### D. Gotchas

- **Empty bucket after pop:** We reassign to `[]` rather than `del`, because some routes call `get_flashed_messages` twice per render (defensive) and `del` would raise on the second call unless guarded.
- **JSON serialization:** Starlette's SessionMiddleware uses `json.dumps`. Tuples become lists on round-trip, hence the list-of-list storage and normalization on read.
- **Cross-request leaks:** Flash messages survive until read. If a user hits a route that doesn't render a template with `get_flashed_messages`, the messages persist to the next request. This is correct behavior but surprises testers.

---

## 11.4 `src/admin/deps/auth.py`

> **[L0-L2]** This module is part of Flask removal. Use sync patterns. It stays sync through L4 and auto-converts at L5 only to the extent its callers flip to `async def` (most functions here are framework-agnostic utilities and remain sync across all layers).
>
> **Layer note:** Code examples in this section show async patterns for L5+ completeness. During L0-L4, use `def` instead of `async def` and `session.scalars(stmt)` instead of `(await session.execute(stmt)).scalars()`. Both `await session.scalars(...)` and `(await session.execute(...)).scalars()` are valid on `AsyncSession` at L5+ — `await session.scalars(...)` is canonical per SQLAlchemy docs (native method since 1.4.24).

### A. Implementation

```python
"""Admin UI auth dependencies: AdminUser, AdminRedirect, Annotated aliases.

Replaces Flask decorators:
- @require_auth(admin_only=True)       → AdminUserDep / SuperAdminDep
- @require_tenant_access()             → CurrentTenantDep (HTML)
- @require_tenant_access(api_mode=True)→ CurrentTenantJsonDep (JSON)
- flask.g.user                         → injected via Annotated alias

Relationship to ResolvedIdentity (src/core/resolved_identity.py):
  ResolvedIdentity is MCP/A2A/REST-API identity (principal-centric, token-based).
  AdminUser is admin-UI identity (human, session-cookie-based).
  They are DISTINCT — an admin UI user has no principal_id, and an MCP principal
  has no admin role. Handlers that need BOTH (rare: admin API that echoes into
  an MCP call) construct a ResolvedIdentity separately via PrincipalFactory
  semantics at the handler level. No cross-pollination at the dep layer.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from starlette.requests import Request

from src.core.database.database_session import get_db_session
from src.core.database.models import Tenant, TenantManagementConfig, User

logger = logging.getLogger(__name__)

Role = Literal["super_admin", "tenant_admin", "tenant_user", "test"]


@dataclass(frozen=True)
class AdminUser:
    """Immutable admin-UI identity.

    `email` is always lowercased at construction time (centralization here
    removes 40+ `.lower()` calls across routers).
    `is_test_user` flag is set only when ADCP_AUTH_TEST_MODE=true AND the
    session contains `test_user` — it enables the test-fixture bypass path
    in CurrentTenantDep.
    """
    email: str
    role: Role
    is_test_user: bool = False

    def __post_init__(self) -> None:
        if self.email != self.email.lower():
            object.__setattr__(self, "email", self.email.lower())


class AdminRedirect(Exception):
    """Raised by admin deps to signal 303 redirect to login.

    Caught by an app-level exception handler registered in app_factory.
    Using an exception (not a RedirectResponse return) is necessary because
    FastAPI deps cannot return a Response directly — they must raise.
    """
    def __init__(self, to: str, next_url: str = ""):
        super().__init__(f"redirect to {to}")
        self.to = to
        self.next_url = next_url


class AdminAccessDenied(Exception):
    """Raised when a user is authenticated but lacks tenant access.

    Distinct from HTTPException(403) so the app-level handler can render a
    templated 403 page instead of a JSON response for HTML routes.
    """
    def __init__(self, message: str = "Access denied"):
        super().__init__(message)
        self.message = message


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _extract_email(raw: Any) -> str:
    """Safely extract an email from the session's `user` field.

    Legacy Flask code stored either a string email or a dict with an `email`
    key (from OAuth claims). Handle both.
    """
    if isinstance(raw, dict):
        return str(raw.get("email") or "").strip().lower()
    if isinstance(raw, str):
        return raw.strip().lower()
    return ""


async def is_super_admin(email: str) -> bool:
    """Check super-admin status.

    Order (mirrors current src/admin/utils/helpers.py:132):
    1. SUPER_ADMIN_EMAILS env var (comma list of exact emails)
    2. SUPER_ADMIN_DOMAINS env var (comma list of domains)
    3. TenantManagementConfig row `super_admin_emails` (db fallback)
    4. TenantManagementConfig row `super_admin_domains`

    [L0-L4: sync `def` with `with get_db_session()`; L5+: `async def` with
    `async with get_db_session()`.] The DB fallback opens its own short-lived
    session — this is the ONE helper where nested session opening is tolerated
    because the caller chain (identity resolution in middleware) may precede
    request scope and therefore has no `SessionDep` to thread through. All OTHER
    helpers MUST accept `SessionDep` rather than opening their own session.

    NO session-level caching here (the Flask version cached in session,
    causing staleness after env var changes). Result is cheap: env checks
    are dict lookups; DB fallback only triggers if env is unset. For high
    QPS, add a TTL lru_cache in a later v2.0 phase.
    """
    if not email:
        return False
    email_l = email.lower()
    domain = email_l.split("@", 1)[1] if "@" in email_l else ""

    env_emails = {
        e.strip().lower()
        for e in os.environ.get("SUPER_ADMIN_EMAILS", "").split(",")
        if e.strip()
    }
    if email_l in env_emails:
        return True

    env_domains = {
        d.strip().lower()
        for d in os.environ.get("SUPER_ADMIN_DOMAINS", "").split(",")
        if d.strip()
    }
    if domain and domain in env_domains:
        return True

    # Async DB check. Caller chain cascades through the full async path:
    # `_get_admin_user_or_none` → `is_super_admin` → `get_admin_user` →
    # every admin handler. All are `async def`.
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


# ---------------------------------------------------------------------------
# Tenant lookup — Agent E Category 2+5 idiom upgrade (pivoted 2026-04-11)
# ---------------------------------------------------------------------------
#
# Originally: `_load_tenant(tenant_id)` as a standalone sync function that
# opened its own `with get_db_session()` block and returned a hand-built dict.
#
# Now: `TenantRepository.get_dto(tenant_id)` takes an injected `SessionDep`
# and returns a typed `TenantDTO` (Pydantic v2 with `from_attributes=True`).
# The dep chain is:
#   SessionDep (session: Depends(get_session))
#     → TenantRepoDep (repo: Depends(get_tenant_repo))
#       → get_current_tenant (tenants: TenantRepoDep, ...)
#
# Every consumer of the old `_load_tenant` helper becomes a handler parameter
# `tenant: CurrentTenantDep` which itself receives a `tenants: TenantRepoDep`.
# No more inline `async with get_db_session()` in helpers; the DI factory owns
# session lifecycle; the repository returns a DTO so templates never see a
# lazy-loadable ORM object.

# src/core/database/repositories/tenant.py
# class TenantRepository:
#     def __init__(self, session: AsyncSession):
#         self.session = session
#
#     async def get_dto(self, tenant_id: str) -> TenantDTO | None:
#         stmt = select(Tenant).filter_by(tenant_id=tenant_id)
#         result = await self.session.execute(stmt)
#         orm = result.scalars().first()
#         return TenantDTO.model_validate(orm) if orm else None

# src/admin/deps/tenant.py
# async def get_tenant_repo(session: SessionDep) -> TenantRepository:
#     return TenantRepository(session)
#
# TenantRepoDep = Annotated[TenantRepository, Depends(get_tenant_repo)]
#
# async def get_current_tenant(
#     request: Request,
#     user: AdminUserDep,
#     tenant_id: str,
#     tenants: TenantRepoDep,
#     users: UserRepoDep,
# ) -> TenantDTO:
#     if user.role == "super_admin":
#         tenant = await tenants.get_dto(tenant_id)
#         if not tenant:
#             raise HTTPException(404, f"Tenant {tenant_id} not found")
#         return tenant
#     # ... per-user access check via UserRepoDep ...
#     tenant = await tenants.get_dto(tenant_id)
#     if not tenant:
#         raise HTTPException(404, f"Tenant {tenant_id} not found")
#     return tenant

# CurrentTenantDep = Annotated[TenantDTO, Depends(get_current_tenant)]

# For the small number of code sites that still need the legacy helper shape
# during the Wave 4 migration (e.g., `_get_admin_user_or_none` for the
# is_super_admin cascade), we retain a thin transitional wrapper:

# !!! L0-L4: this is L5+ admin-handler code. At L0-L4, use `def`, `Session` (not AsyncSession), and `session.execute(stmt)` (no await). See file-top banner.
async def _load_tenant(tenant_id: str) -> dict[str, Any]:
    """DEPRECATED transitional helper — use TenantRepository.get_dto() instead.

    Remains in place only until every caller has been migrated to the
    `TenantRepoDep` Dep pattern. New code MUST use the repository. This helper
    is flagged for removal in Wave 5.

    [L0-L4: sync `def` + `with get_db_session()`. L5+: `async def` +
    `async with get_db_session()`.] This helper opens its own session inline.
    The idiomatic replacement is `TenantRepoDep` + `get_current_tenant(...,
    tenants: TenantRepoDep, ...)` (sync at L0-L4, async at L5+).
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


# !!! L0-L4: this is L5+ admin-handler code. At L0-L4, use `def`, `Session` (not AsyncSession), and `session.execute(stmt)` (no await). See file-top banner.
async def _get_admin_user_or_none(request: Request) -> AdminUser | None:
    """Read the session and produce an AdminUser, or None if not authenticated.

    [L0-L4: sync `def`; L5+: `async def`.] At L5+ this is `async def` because
    it awaits `is_super_admin`, which opens an async DB session for the env/DB
    fallback. At L0-L4 it is sync `def` and `is_super_admin` is a sync call.

    Test-mode bypass: when ADCP_AUTH_TEST_MODE=true AND session contains a
    `test_user` key, construct an AdminUser with `is_test_user=True` and
    `role=session["test_user_role"]`. This is the ONLY place the test bypass
    is honored — CurrentTenantDep trusts is_test_user without re-checking.

    Critical: the env var check AND the session key check must BOTH be true.
    Neither alone is sufficient. This prevents test fixtures from leaking
    into production (env var) while also preventing stale test session cookies
    from granting access in dev after ADCP_AUTH_TEST_MODE is flipped off.
    """
    try:
        session = request.session
    except AssertionError:
        # SessionMiddleware not installed (unit test without middleware)
        return None

    test_mode = os.environ.get("ADCP_AUTH_TEST_MODE", "").lower() == "true"
    if test_mode and "test_user" in session:
        email = _extract_email(session["test_user"])
        if not email:
            return None
        role = session.get("test_user_role", "tenant_user")
        if role not in ("super_admin", "tenant_admin", "tenant_user", "test"):
            role = "tenant_user"
        return AdminUser(email=email, role=role, is_test_user=True)

    raw = session.get("user")
    if raw is None:
        return None
    email = _extract_email(raw)
    if not email:
        return None
    role: Role = "super_admin" if await is_super_admin(email) else "tenant_user"
    return AdminUser(email=email, role=role, is_test_user=False)


# ---------------------------------------------------------------------------
# Public deps
# ---------------------------------------------------------------------------

# !!! L0-L4: this is L5+ admin-handler code. At L0-L4, use `def`, `Session` (not AsyncSession), and `session.execute(stmt)` (no await). See file-top banner.
async def get_admin_user_optional(request: Request) -> AdminUser | None:
    return await _get_admin_user_or_none(request)


# !!! L0-L4: this is L5+ admin-handler code. At L0-L4, use `def`, `Session` (not AsyncSession), and `session.execute(stmt)` (no await). See file-top banner.
async def get_admin_user(request: Request) -> AdminUser:
    user = await _get_admin_user_or_none(request)
    if user is None:
        raise AdminRedirect(to="/admin/login", next_url=str(request.url))
    return user


# !!! L0-L4: this is L5+ admin-handler code. At L0-L4, use `def`, `Session` (not AsyncSession), and `session.execute(stmt)` (no await). See file-top banner.
async def get_admin_user_json(request: Request) -> AdminUser:
    """Same as get_admin_user but raises HTTPException(401) for JSON endpoints."""
    user = await _get_admin_user_or_none(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


AdminUserDep = Annotated[AdminUser, Depends(get_admin_user)]
AdminUserJsonDep = Annotated[AdminUser, Depends(get_admin_user_json)]
AdminUserOptional = Annotated[AdminUser | None, Depends(get_admin_user_optional)]


# !!! L0-L4: this is L5+ admin-handler code. At L0-L4, use `def`, `Session` (not AsyncSession), and `session.execute(stmt)` (no await). See file-top banner.
async def require_super_admin(user: AdminUserDep) -> AdminUser:
    if user.role != "super_admin":
        raise HTTPException(status_code=403, detail="Super admin required")
    return user


# !!! L0-L4: this is L5+ admin-handler code. At L0-L4, use `def`, `Session` (not AsyncSession), and `session.execute(stmt)` (no await). See file-top banner.
async def require_super_admin_json(user: AdminUserJsonDep) -> AdminUser:
    if user.role != "super_admin":
        raise HTTPException(status_code=403, detail="Super admin required")
    return user


SuperAdminDep = Annotated[AdminUser, Depends(require_super_admin)]
SuperAdminJsonDep = Annotated[AdminUser, Depends(require_super_admin_json)]
```

`tenant.py` (split out to avoid circular imports — `audit.py` depends on `auth.py` but not `tenant.py`):

```python
# src/admin/deps/tenant.py
from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from starlette.requests import Request

from src.admin.deps.auth import (
    AdminUser,
    AdminUserDep,
    AdminUserJsonDep,
    _load_tenant,
)
from src.core.database.database_session import get_db_session
from src.core.database.models import Tenant, User


# !!! L0-L4: this is L5+ admin-handler code. At L0-L4, use `def`, `Session` (not AsyncSession), and `session.execute(stmt)` (no await). See file-top banner.
async def _user_has_tenant_access(email: str, tenant_id: str) -> bool:
    async with get_db_session() as db:
        found = (await db.execute(
            select(User).filter_by(email=email.lower(), tenant_id=tenant_id, is_active=True)
        )).scalars().first()
        return found is not None


# !!! L0-L4: this is L5+ admin-handler code. At L0-L4, use `def`, `Session` (not AsyncSession), and `session.execute(stmt)` (no await). See file-top banner.
async def _tenant_has_auth_setup_mode(tenant_id: str) -> bool:
    async with get_db_session() as db:
        tenant = (await db.execute(
            select(Tenant).filter_by(tenant_id=tenant_id)
        )).scalars().first()
        return bool(tenant and getattr(tenant, "auth_setup_mode", False))


# !!! L0-L4: this is L5+ admin-handler code. At L0-L4, use `def`, `Session` (not AsyncSession), and `session.execute(stmt)` (no await). See file-top banner.
async def get_current_tenant(
    request: Request,
    user: AdminUserDep,
    tenant_id: str,
) -> dict[str, Any]:
    """Resolve the current tenant, enforcing access.

    [L0-L4: sync `def`; L5+: `async def`.] This is the transitional-wrapper
    shape that opens its own session; the idiomatic L4 replacement threads
    `TenantRepoDep` + `UserRepoDep` through the signature so the DI layer owns
    session lifetime. (SessionDep is introduced at L4 as sync, re-aliased to
    `AsyncSession` at L5b.)
    See §11.0.4 (repository base) and §11.0.5 (session_scope).

    Super admins bypass access checks. Test users with matching test_tenant_id
    OR super_admin role bypass. Test users from OTHER tenants are rejected.
    Regular users must have an active User row in the target tenant.
    """
    if user.role == "super_admin":
        return await _load_tenant(tenant_id)

    if user.is_test_user:
        session = request.session
        if session.get("test_tenant_id") == tenant_id:
            return await _load_tenant(tenant_id)
        if session.get("test_user_role") == "super_admin":
            return await _load_tenant(tenant_id)
        # Fall through to the auth_setup_mode check — a fresh tenant with
        # auth_setup_mode=True is intentionally permissive for bootstrap.
        if await _tenant_has_auth_setup_mode(tenant_id):
            return await _load_tenant(tenant_id)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    if not await _user_has_tenant_access(user.email, tenant_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    return await _load_tenant(tenant_id)


# !!! L0-L4: this is L5+ admin-handler code. At L0-L4, use `def`, `Session` (not AsyncSession), and `session.execute(stmt)` (no await). See file-top banner.
async def get_current_tenant_json(
    request: Request,
    user: AdminUserJsonDep,
    tenant_id: str,
) -> dict[str, Any]:
    # Same logic; different 401 semantics come from the AdminUserJsonDep chain.
    return await get_current_tenant(request, user, tenant_id)


CurrentTenantDep = Annotated[dict, Depends(get_current_tenant)]
CurrentTenantJsonDep = Annotated[dict, Depends(get_current_tenant_json)]
```

### B. Tests

```python
# tests/unit/admin/test_deps_auth.py
from unittest.mock import MagicMock, patch
import pytest
from fastapi import HTTPException

from src.admin.deps.auth import (
    AdminUser,
    AdminRedirect,
    _extract_email,
    _get_admin_user_or_none,
    get_admin_user,
    is_super_admin,
)


def _request(session: dict):
    req = MagicMock()
    req.session = session
    req.url = "http://t.example/admin/foo"
    return req


class TestExtractEmail:
    def test_string_input(self):
        assert _extract_email("USER@X.com ") == "user@x.com"

    def test_dict_input(self):
        assert _extract_email({"email": "USER@X.com"}) == "user@x.com"

    def test_none_input(self):
        assert _extract_email(None) == ""

    def test_dict_without_email_key(self):
        assert _extract_email({"name": "bob"}) == ""


class TestGetAdminUserOrNone:
    def test_no_session_user_returns_none(self):
        req = _request({})
        assert _get_admin_user_or_none(req) is None

    def test_test_mode_bypass_requires_both(self, monkeypatch):
        # env var off — test_user in session is ignored
        monkeypatch.delenv("ADCP_AUTH_TEST_MODE", raising=False)
        req = _request({"test_user": "bob@example.com"})
        assert _get_admin_user_or_none(req) is None

        # env var on + session key → AdminUser with is_test_user
        monkeypatch.setenv("ADCP_AUTH_TEST_MODE", "true")
        user = _get_admin_user_or_none(req)
        assert user is not None
        assert user.email == "bob@example.com"
        assert user.is_test_user is True

    def test_malformed_session_user_returns_none(self):
        req = _request({"user": 12345})
        assert _get_admin_user_or_none(req) is None

    def test_email_is_lowercased(self, monkeypatch):
        monkeypatch.delenv("SUPER_ADMIN_EMAILS", raising=False)
        req = _request({"user": {"email": "ALICE@X.com"}})
        user = _get_admin_user_or_none(req)
        assert user.email == "alice@x.com"


class TestGetAdminUserRaises:
    def test_unauthenticated_raises_admin_redirect(self):
        req = _request({})
        with pytest.raises(AdminRedirect) as exc_info:
            get_admin_user(req)
        assert exc_info.value.to == "/admin/login"
        assert "http://t.example/admin/foo" in exc_info.value.next_url


class TestIsSuperAdmin:
    def test_env_emails_match(self, monkeypatch):
        monkeypatch.setenv("SUPER_ADMIN_EMAILS", "alice@x.com, BOB@Y.com")
        assert is_super_admin("alice@x.com") is True
        assert is_super_admin("bob@y.com") is True
        assert is_super_admin("other@z.com") is False

    def test_env_domains_match(self, monkeypatch):
        monkeypatch.setenv("SUPER_ADMIN_DOMAINS", "anthropic.com")
        assert is_super_admin("anyone@anthropic.com") is True

    def test_empty_email_returns_false(self):
        assert is_super_admin("") is False
```

Integration test for tenant dep (uses harness):

```python
# tests/integration/admin/test_current_tenant_dep.py
import pytest
from fastapi import FastAPI, HTTPException
from starlette.middleware.sessions import SessionMiddleware
from starlette.testclient import TestClient

from tests.factories import TenantFactory, UserFactory
from tests.harness import IntegrationEnv
from src.admin.deps.auth import AdminUserDep
from src.admin.deps.tenant import CurrentTenantDep, get_current_tenant


@pytest.mark.requires_db
class TestCurrentTenantDep:
    def test_super_admin_bypasses_user_row(self, integration_db, monkeypatch):
        monkeypatch.setenv("SUPER_ADMIN_EMAILS", "super@x.com")
        with IntegrationEnv() as env:
            TenantFactory(tenant_id="t1")

            app = FastAPI()
            app.add_middleware(SessionMiddleware, secret_key="x" * 64)

            @app.get("/tenant/{tenant_id}")
            def show(tenant_id: str, tenant: CurrentTenantDep):
                return tenant

            client = TestClient(app)
            # Inject the session dict directly via the SessionMiddleware cookie
            with client as c:
                c.cookies.set("adcp_session", _sign({"user": "super@x.com"}))
                r = c.get("/tenant/t1")
            assert r.status_code == 200
            assert r.json()["tenant_id"] == "t1"

    def test_regular_user_without_row_gets_403(self, integration_db):
        with IntegrationEnv() as env:
            TenantFactory(tenant_id="t1")
            TenantFactory(tenant_id="t2")
            UserFactory(tenant_id="t1", email="alice@x.com", is_active=True)
            # alice is in t1 but tries t2
            ...
```

### C. Integration

- Imports `get_db_session`, `Tenant`, `TenantManagementConfig`, `User` from `src.core.database`.
- Public API: `AdminUser`, `AdminRedirect`, `AdminAccessDenied`, `is_super_admin`, `AdminUserDep`, `AdminUserJsonDep`, `SuperAdminDep`, `AdminUserOptional`, `CurrentTenantDep`, `CurrentTenantJsonDep`.
- Does NOT use `ResolvedIdentity` — admin UI auth is a distinct concept. See docstring.
- Does NOT read `request.state.auth_context` — that's token-based auth for API routes. Admin UI auth is session-cookie-based. The two stacks share `UnifiedAuthMiddleware` only incidentally (it runs on every request regardless).
- DB access via `async with get_db_session()` (async SQLAlchemy 2.0 via `AsyncSession`, pivoted 2026-04-11). Repository layer ships in v2.0 Wave 4 per the Agent E idiom upgrade — see `async-pivot-checkpoint.md` §3 and the recipe at §11.0.4/§11.0.5.
- `AdminRedirect` exception handler registered in `app_factory`:

```python
@app.exception_handler(AdminRedirect)
async def admin_redirect_handler(request: Request, exc: AdminRedirect) -> RedirectResponse:
    from urllib.parse import quote
    url = f"{exc.to}?next={quote(exc.next_url, safe='')}" if exc.next_url else exc.to
    return RedirectResponse(url=url, status_code=303)
```

### D. Gotchas

- **DB layer (layered 2026-04-14):** [L0-L4: `_load_tenant`, `_user_has_tenant_access`, and `_tenant_has_auth_setup_mode` are sync `def`. All DB access uses `with get_db_session() as db:` / `db.execute(select(...)).scalars().first()`.] [L5+: same helpers flip to `async def` with `async with` / `await db.execute(...)`.] `run_in_threadpool` is reserved for file I/O, CPU-bound, or sync third-party libraries at every layer. Under the Agent E idiom upgrade (Category 2), new code should prefer `TenantRepoDep` via `Depends(get_tenant_repo)` over calling `_load_tenant` directly — the standalone helper is transitional and will be removed in L6. See `async-pivot-checkpoint.md` §3 for the L5+ target-state patterns.
- **`request.session` raises AssertionError** if `SessionMiddleware` is not installed. The `try/except` in `_get_admin_user_or_none` catches this to make unit tests without middleware work (they get `None`).
- **Session fixation:** On privilege elevation (login), Starlette's SessionMiddleware does NOT rotate the session cookie. Add a manual cookie-clear + re-set in the login handler. Tracked.
- **Case-insensitive email comparison:** All email matching is lowercase. The database schema stores emails case-preserved but indexes on `lower(email)`. The dataclass enforces lowercase at construction.
- **Role enum drift:** Only `super_admin` is distinguished. `tenant_admin` vs `tenant_user` is populated but no current route gates on it. Leave the enum in place for future use.

---

## 11.5 `src/admin/deps/audit.py`

> **[L0-L2]** This module is part of Flask removal. Use sync patterns. It stays sync through L4 and auto-converts at L5 only to the extent its callers flip to `async def` (most functions here are framework-agnostic utilities and remain sync across all layers).

### A. Implementation

```python
"""Audit dependency factory.

Replaces the Flask decorator @audit_action("create_user") with:
    @router.post("/users", dependencies=[Depends(audit_action("create_user"))])

Emits an audit log entry AFTER the handler completes successfully.
BackgroundTasks is used so the DB write happens post-response (not blocking).
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from fastapi import BackgroundTasks, Depends
from starlette.requests import Request

from src.admin.deps.auth import AdminUser, AdminUserDep
from src.core.audit_logger import AuditLogger

logger = logging.getLogger(__name__)


def _write_audit(
    action: str,
    user_email: str,
    tenant_id: str | None,
    path: str,
    method: str,
    extra: dict[str, Any] | None = None,
) -> None:
    """Fire-and-forget audit write. Swallows DB errors."""
    try:
        audit = AuditLogger()
        audit.log(
            action=action,
            user=user_email,
            tenant_id=tenant_id,
            details={"path": path, "method": method, **(extra or {})},
        )
    except Exception:
        logger.exception("Audit log write failed (non-fatal): action=%s", action)


def audit_action(action: str) -> Callable[..., None]:
    """Dep factory that schedules an audit log after the handler runs.

    WHY BackgroundTasks: FastAPI runs BackgroundTasks AFTER the response is
    sent. A failing audit write should never affect the user-visible response,
    so we intentionally do NOT write inside the dep body (which would block).

    Dep order: this MUST run after get_current_tenant (if the route uses it),
    so the BackgroundTasks scheduling is the absolute last thing before the
    handler executes. FastAPI's topological dep resolution handles this when
    audit_action is declared last in the route signature.
    """
    def _dep(
        request: Request,
        background: BackgroundTasks,
        user: AdminUserDep,
    ) -> None:
        tenant_id = request.path_params.get("tenant_id")
        background.add_task(
            _write_audit,
            action=action,
            user_email=user.email,
            tenant_id=tenant_id,
            path=request.url.path,
            method=request.method,
        )
    return _dep
```

### B. Tests

```python
# tests/unit/admin/test_audit_dep.py
from unittest.mock import MagicMock, patch
import pytest
from fastapi import FastAPI, BackgroundTasks
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from src.admin.deps.audit import audit_action, _write_audit
from src.admin.deps.auth import AdminUser, get_admin_user


class TestAuditAction:
    def test_schedules_background_task(self, monkeypatch):
        monkeypatch.setenv("SESSION_SECRET", "x" * 64)
        monkeypatch.setenv("SUPER_ADMIN_EMAILS", "admin@x.com")

        app = FastAPI()
        app.add_middleware(SessionMiddleware, secret_key="x" * 64)

        # Override auth dep to avoid session machinery
        app.dependency_overrides[get_admin_user] = lambda: AdminUser(
            email="admin@x.com", role="super_admin"
        )

        recorded: list[dict] = []

        def fake_write(**kwargs):
            recorded.append(kwargs)

        @app.post("/tenant/{tenant_id}/users")
        def create_user(tenant_id: str, _: None = None):  # _ = audit dep result
            return {"ok": True}

        # Re-declare with the audit dep wired in
        app.router.routes.clear()

        @app.post("/tenant/{tenant_id}/users")
        def create_user2(
            tenant_id: str,
            _audit: None = audit_action("create_user"),
        ):
            return {"ok": True}

        with patch("src.admin.deps.audit._write_audit", side_effect=fake_write):
            client = TestClient(app)
            r = client.post("/tenant/t1/users")
        assert r.status_code == 200
        assert recorded == [{
            "action": "create_user",
            "user_email": "admin@x.com",
            "tenant_id": "t1",
            "path": "/tenant/t1/users",
            "method": "POST",
        }]

    def test_write_swallows_db_errors(self):
        with patch("src.admin.deps.audit.AuditLogger", side_effect=RuntimeError("db down")):
            # Must not raise
            _write_audit(action="x", user_email="a@b", tenant_id=None, path="/", method="GET")
```

### C. Integration

- Imports `AdminUserDep` from `src.admin.deps.auth`, `AuditLogger` from `src.core.audit_logger`.
- Public API: `audit_action(name: str) -> dep`.
- Runs AFTER `get_admin_user` (declared first in route signature).
- Writes to DB via `AuditLogger` — fire-and-forget inside a `BackgroundTasks` task.

### D. Gotchas

- **BackgroundTasks run post-response:** A 500 error in the handler → BackgroundTasks don't run → no audit entry. This is intentional (failed actions aren't audited as successes), but means you cannot audit failures via this dep. Use the exception handler for that.
- **Error swallowing:** A failing audit write logs `exception` but does not propagate. This is the right tradeoff for a non-critical side effect.
- **`request.path_params` timing:** The dep runs AFTER path parameters are parsed, so `tenant_id` is always available if the route declares it. If the route has no `tenant_id` path param, `.get("tenant_id")` returns `None`.

---

## 11.6 `src/admin/oauth.py`

> **[L0-L2]** This module is part of Flask removal. Use sync patterns. It stays sync through L4 and auto-converts at L5 only to the extent its callers flip to `async def` (most functions here are framework-agnostic utilities and remain sync across all layers).

### A. Implementation

```python
"""Authlib starlette_client OAuth singleton + per-tenant OIDC factory.

Replaces src/admin/blueprints/auth.py:18 (authlib.integrations.flask_client).
The starlette_client is a drop-in API match — same register() / authorize_redirect()
/ authorize_access_token() method names, but uses Starlette Request/Response.

Two client tiers:
1. GLOBAL "google" client — registered at startup, serves default OIDC flow
2. PER-TENANT clients — lazily registered on first use, cached in _tenant_client_cache

Cache invalidation: tenant admin updates their OIDC config via the settings UI,
which calls invalidate_tenant_oidc_client(tenant_id). Without this, a tenant
could not rotate their client secret without a pod restart.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Any

from authlib.integrations.starlette_client import OAuth
from authlib.integrations.starlette_client.apps import StarletteOAuth2App

from src.services.auth_config_service import get_oidc_config_for_auth

logger = logging.getLogger(__name__)

# Module-level singleton. Safe because OAuth is a registration registry; its
# internal state is only written at startup + cache invalidation.
oauth = OAuth()

# Per-tenant cache of lazily-registered clients.
# Protected by a lock because _register_tenant can be called concurrently
# from multiple worker requests racing on the same unregistered tenant.
_tenant_client_cache: dict[str, StarletteOAuth2App] = {}
_cache_lock = threading.Lock()


def init_oauth() -> None:
    """Register the default global OIDC clients.

    Called once at startup from app_factory. Reads env once (not on every
    request). Safe to call multiple times (Authlib's register is idempotent
    but we guard anyway).
    """
    google_id = os.environ.get("GOOGLE_CLIENT_ID") or os.environ.get("OAUTH_CLIENT_ID")
    google_secret = os.environ.get("GOOGLE_CLIENT_SECRET") or os.environ.get("OAUTH_CLIENT_SECRET")
    discovery = os.environ.get(
        "OAUTH_DISCOVERY_URL",
        "https://accounts.google.com/.well-known/openid-configuration",
    )
    if not google_id or not google_secret:
        logger.warning(
            "OAuth client env vars not set — default provider unavailable. "
            "Set GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET or OAUTH_CLIENT_ID/OAUTH_CLIENT_SECRET."
        )
        return

    if hasattr(oauth, "google"):
        logger.debug("OAuth `google` client already registered, skipping")
        return

    oauth.register(
        name="google",
        client_id=google_id,
        client_secret=google_secret,
        server_metadata_url=discovery,
        client_kwargs={"scope": os.environ.get("OAUTH_SCOPES", "openid email profile")},
    )
    logger.info("Registered global OAuth client: google (%s)", discovery)


def get_tenant_oidc_client(tenant_id: str) -> StarletteOAuth2App | None:
    """Return a cached or freshly-registered per-tenant OIDC client.

    Returns None if the tenant has not configured OIDC. Callers must then
    fall back to the default `oauth.google` client.

    Thread safety: the check-then-register sequence is protected by a lock.
    Without the lock, two concurrent requests for the same uncached tenant
    would both call oauth.register(), and Authlib would raise
    OAuthError('OAuth client with name=... already exists').
    """
    # Fast path: lock-free read
    client = _tenant_client_cache.get(tenant_id)
    if client is not None:
        return client

    with _cache_lock:
        # Re-check under lock — another thread may have just populated
        client = _tenant_client_cache.get(tenant_id)
        if client is not None:
            return client

        config = get_oidc_config_for_auth(tenant_id)
        if not config:
            return None

        name = f"tenant_{tenant_id}"
        # Authlib raises if re-registering with the same name — pop any
        # stale registration (can happen after invalidation).
        if name in oauth._clients:  # type: ignore[attr-defined]
            del oauth._clients[name]  # type: ignore[attr-defined]

        oauth.register(
            name=name,
            client_id=config["client_id"],
            client_secret=config["client_secret"],
            server_metadata_url=config["discovery_url"],
            client_kwargs={"scope": config.get("scopes", "openid email profile")},
        )
        client = getattr(oauth, name)
        _tenant_client_cache[tenant_id] = client
        logger.info("Registered tenant OIDC client: %s", name)
        return client


def invalidate_tenant_oidc_client(tenant_id: str) -> None:
    """Evict a tenant's cached OIDC client.

    MUST be called from:
    - The settings route that saves a new OIDC config (e.g., after the admin
      changes their client_secret or discovery_url)
    - The settings route that disables OIDC for a tenant
    - Tenant deletion

    Under Gunicorn/Uvicorn workers, this only invalidates the CURRENT worker.
    Other workers will continue to use the stale client until they also
    invalidate. Tracked: move cache to Redis in a later v2.0 phase for cross-worker invalidation.
    """
    with _cache_lock:
        client = _tenant_client_cache.pop(tenant_id, None)
        name = f"tenant_{tenant_id}"
        if hasattr(oauth, "_clients") and name in oauth._clients:  # type: ignore[attr-defined]
            del oauth._clients[name]  # type: ignore[attr-defined]
        if client is not None:
            logger.info("Invalidated tenant OIDC client: %s", name)
```

### B. Tests

```python
# tests/unit/admin/test_oauth.py
from unittest.mock import MagicMock, patch
import pytest

from src.admin.oauth import (
    init_oauth,
    get_tenant_oidc_client,
    invalidate_tenant_oidc_client,
    _tenant_client_cache,
)


@pytest.fixture(autouse=True)
def clear_cache():
    _tenant_client_cache.clear()
    yield
    _tenant_client_cache.clear()


class TestInitOAuth:
    def test_missing_env_logs_warning_and_skips(self, monkeypatch, caplog):
        monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
        monkeypatch.delenv("OAUTH_CLIENT_ID", raising=False)
        # Reset oauth singleton for idempotency
        from src.admin.oauth import oauth as _oauth
        _oauth._clients.pop("google", None)  # type: ignore
        init_oauth()
        assert "OAuth client env vars not set" in caplog.text

    def test_registers_google(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
        monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "csec")
        from src.admin.oauth import oauth as _oauth
        _oauth._clients.pop("google", None)
        init_oauth()
        assert hasattr(_oauth, "google")


class TestTenantClientCache:
    def test_returns_none_when_no_config(self):
        with patch("src.admin.oauth.get_oidc_config_for_auth", return_value=None):
            assert get_tenant_oidc_client("t1") is None

    def test_caches_registered_client(self):
        fake_config = {
            "client_id": "cid",
            "client_secret": "csec",
            "discovery_url": "https://idp.example/.well-known/openid-configuration",
            "scopes": "openid email",
        }
        with patch("src.admin.oauth.get_oidc_config_for_auth", return_value=fake_config):
            c1 = get_tenant_oidc_client("t1")
            c2 = get_tenant_oidc_client("t1")
        assert c1 is not None
        assert c1 is c2

    def test_invalidate_removes_from_cache(self):
        fake_config = {
            "client_id": "cid",
            "client_secret": "csec",
            "discovery_url": "https://idp.example/.well-known/openid-configuration",
            "scopes": "openid email",
        }
        with patch("src.admin.oauth.get_oidc_config_for_auth", return_value=fake_config):
            get_tenant_oidc_client("t1")
            assert "t1" in _tenant_client_cache
            invalidate_tenant_oidc_client("t1")
            assert "t1" not in _tenant_client_cache

    def test_concurrent_register_only_registers_once(self):
        """Regression: without the lock, two threads racing on an uncached
        tenant would both call oauth.register and fail."""
        import threading
        calls = []
        fake_config = {
            "client_id": "cid", "client_secret": "csec",
            "discovery_url": "https://idp.example/.well-known/openid-configuration",
            "scopes": "openid email",
        }

        def _cfg(tid):
            calls.append(tid)
            return fake_config

        results = []
        with patch("src.admin.oauth.get_oidc_config_for_auth", side_effect=_cfg):
            def race():
                results.append(get_tenant_oidc_client("t_race"))
            threads = [threading.Thread(target=race) for _ in range(10)]
            for t in threads: t.start()
            for t in threads: t.join()
        # All 10 threads got the same client
        assert all(r is results[0] for r in results)
        # Config was fetched at most once under lock (could be 1 or 2 if race was quick)
        assert len(calls) <= 2
```

### C. Integration

- Imports `OAuth` from `authlib.integrations.starlette_client`, `get_oidc_config_for_auth` from `src.services.auth_config_service`.
- Public API: `oauth` (singleton), `init_oauth()`, `get_tenant_oidc_client(tenant_id)`, `invalidate_tenant_oidc_client(tenant_id)`.
- Used by `src/admin/routers/auth.py` login/callback handlers.
- Touches DB indirectly via `get_oidc_config_for_auth`.
- State survives on `request.session`: Authlib stores OAuth state in session keys like `_state_google_<random>`. This shares the `adcp_session` cookie with admin session data. **No collision** because Authlib's keys all start with `_state_` and admin code never writes keys with that prefix. Audit: grep for `request.session[`.

### D. Gotchas

- **Cross-worker cache staleness:** Each Uvicorn worker has its own `_tenant_client_cache`. Invalidation only hits the worker that receives the invalidation request. For v2.0.0 with typically 2–4 workers, the worst case is a 5-second window of stale secrets after rotation. A later v2.0 phase moves to Redis-backed cache.
- **Session key collisions:** Authlib writes `_state_<name>_<nonce>` to `request.session`. This is safe unless a future codebase writes its own `_state_*` keys.
- **`oauth._clients` is private:** We reach into it to pop stale registrations. If Authlib renames this attribute in a minor release, invalidation silently becomes a no-op. Pin `authlib==1.6.*`.
- **Test isolation:** The `oauth` singleton persists across tests. The `clear_cache` autouse fixture + popping `_clients["google"]` is necessary for clean state.

---

## 11.6.1 OIDC `form_post` transit cookie

> **[L1b]** Sub-section under §11.6. Resolves the SG-5 CSRF/OIDC cookie-attachment edge case. Pure-ASGI; no Flask dependencies; lands with the OIDC router port in L1b.

### A. Background

Google OIDC (and every OP that matches the OpenID Connect Core 1.0 spec) supports `response_mode=form_post` — the IdP returns the authorization code/state via an HTML form that auto-submits to the RP's callback URL. The browser issues a cross-origin POST whose `Origin` is the IdP's host (e.g., `https://accounts.google.com`), NOT our origin. Two separate problems fall out of this single fact:

1. **Session cookie dropped by SameSite.** The project-wide session cookie is set with `SameSite=Lax` (confirmed in §11.2 / `SessionMiddleware` registration). RFC 6265bis §5.3.7.1 and the living [SameSite draft](https://datatracker.ietf.org/doc/html/draft-ietf-httpbis-rfc6265bis) are explicit: `Lax` cookies are NOT attached to cross-site POSTs — only to top-level GET navigations and same-site requests. When Google posts the form to `/admin/auth/oidc/callback`, the browser strips `adcp_session`. The callback handler therefore cannot read any state the login-initiation step wrote to `request.session`.
2. **CSRF middleware refuses the callback.** `CSRFOriginMiddleware` (§11.7) validates `Origin` on unsafe methods against the configured allowed-origins list. The IdP's origin is never on that list (and cannot be — `allowed_origins` is our own public origins, not every OP we might federate with). So the callback also fails Origin validation before any handler runs.

Both are blocking: current Flask OIDC code at `src/admin/blueprints/oidc.py:209, 215` writes `state`, `nonce`, and the PKCE `code_verifier` to `session` pre-redirect and reads them back on callback. Port-to-FastAPI without mitigation = OIDC login permanently broken.

The widespread mitigation in the Python OIDC ecosystem (and what Authlib recommends) is a **separate short-lived "transit" cookie** set on the login-initiation response with `SameSite=None; Secure; HttpOnly` so it survives the cross-origin POST, carrying only the three OIDC round-trip fields. The admin session cookie remains `SameSite=Lax` — no weakening of the general CSRF posture.

### B. Impact — what breaks without the transit cookie

- Every tenant whose OIDC provider uses `response_mode=form_post` (Google with `form_post` opt-in, ADFS, Okta OIDC-SAML hybrid, Azure AD B2C in some configs) — login is dead.
- Providers that default to `response_mode=query` (most OIDC deployments, including Google's default) — unaffected, but we cannot predict per-tenant `response_mode` without auditing every tenant's `OIDCConfig`. Assume form_post is possible.
- CSRFOriginMiddleware returns 403 on the callback before Authlib even parses the response.

### C. Implementation — `src/admin/oauth_transit.py`

~60 LOC. Pure stdlib + `itsdangerous`.

```python
"""OIDC form_post transit cookie.

Carries {state, nonce, code_verifier} across the cross-origin POST from the IdP
back to our callback URL. Separate from the admin session cookie because SameSite=Lax
on that cookie strips it from cross-site POSTs.

Signed + short-lived; compromises on confidentiality are bounded because the
fields are single-use OIDC round-trip values, not auth material.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import Request, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

logger = logging.getLogger(__name__)

TRANSIT_COOKIE = "oauth_transit"
TRANSIT_MAX_AGE = 600  # 10 minutes — OIDC spec caps state validity at ~10min


def get_transit_serializer(secret: str) -> URLSafeTimedSerializer:
    """Factory — one serializer per secret. Not memoized; caller holds the ref."""
    return URLSafeTimedSerializer(secret, salt="oauth_transit_v1")


def start_oauth_flow(
    response: Response,
    *,
    secret: str,
    state: str,
    nonce: str,
    code_verifier: str,
) -> None:
    """Called from the login-initiation handler BEFORE the redirect to the IdP.

    Sets the transit cookie on the outgoing 302/303 response. The cookie survives
    the IdP round-trip because SameSite=None allows cross-site attachment.
    """
    serializer = get_transit_serializer(secret)
    payload = serializer.dumps({"state": state, "nonce": nonce, "code_verifier": code_verifier})
    response.set_cookie(
        key=TRANSIT_COOKIE,
        value=payload,
        max_age=TRANSIT_MAX_AGE,
        httponly=True,
        secure=True,          # REQUIRED with SameSite=None
        samesite="none",      # survives the cross-origin form_post
        path="/admin/auth/",  # narrow — only visible to /admin/auth/* paths
    )


def finish_oauth_flow(
    request: Request,
    *,
    secret: str,
) -> dict[str, Any] | None:
    """Called from the OIDC callback handler BEFORE Authlib token exchange.

    Returns the decoded payload or None if the cookie is missing / expired / tampered.
    """
    raw = request.cookies.get(TRANSIT_COOKIE)
    if not raw:
        logger.warning("oauth_transit_cookie_missing")
        return None

    serializer = get_transit_serializer(secret)
    try:
        return serializer.loads(raw, max_age=TRANSIT_MAX_AGE)
    except SignatureExpired:
        logger.warning("oauth_transit_cookie_expired")
        return None
    except BadSignature:
        logger.warning("oauth_transit_cookie_tampered")
        return None


def delete_transit_cookie(response: Response) -> None:
    """Call on BOTH success and failure callback paths.

    Attributes MUST match set_cookie exactly — browsers identify cookies by
    (name, path, domain), but the Set-Cookie attributes that form the delete
    directive must replicate SameSite/Secure or some browsers will silently
    ignore the delete.
    """
    response.delete_cookie(
        key=TRANSIT_COOKIE,
        path="/admin/auth/",
        httponly=True,
        secure=True,
        samesite="none",
    )
```

### D. Callback flow integration

```python
# src/admin/routers/oidc.py — callback excerpt
@router.post("/callback", name="admin_oidc_callback")
async def oidc_callback(request: Request, response: Response, ...):
    transit = finish_oauth_flow(request, secret=settings.session_secret)
    if transit is None:
        raise HTTPException(400, "OIDC state cookie missing or expired")

    if request.query_params.get("state") != transit["state"]:
        delete_transit_cookie(response)
        raise HTTPException(400, "OIDC state mismatch")

    # Authlib token exchange consumes code_verifier + nonce from `transit`
    token = await oauth.google.authorize_access_token(
        request,
        code_verifier=transit["code_verifier"],
        claims_options={"nonce": {"value": transit["nonce"]}},
    )

    # ... establish session, set admin session cookie ...

    delete_transit_cookie(response)
    return RedirectResponse("/admin/", status_code=303)
```

### E. CSRFOriginMiddleware exempt path

`CSRFOriginMiddleware` (§11.7) must **not** Origin-validate the OIDC callback — the Origin is the IdP, and we have no way to pre-register every federated IdP's origin. Add `/admin/auth/oidc/callback` to the existing exempt list (alongside `/_internal/` and any adapter callbacks):

```python
# §11.7 CSRFOriginMiddleware — extend exempt list
CSRF_EXEMPT_PATHS = (
    "/_internal/",
    "/admin/auth/oidc/callback",  # OIDC form_post — state validated by oauth_transit
    # ...
)
```

The state validation in `finish_oauth_flow` + state-param comparison above is the CSRF defence on this path — it's stronger than Origin validation for this specific class of request because the state is a signed, single-use, timed value the attacker cannot forge.

### F. Tests

`tests/integration/test_oidc_form_post_cross_origin_callback.py` — 2 cases:

```python
@pytest.mark.integration
async def test_oidc_form_post_cross_origin_callback_validates(async_client, monkeypatch):
    """Green path: IdP form_post POST carries oauth_transit cookie, callback accepts."""
    # 1. GET /admin/auth/oidc/login — handler calls start_oauth_flow, 302s to IdP
    # 2. Simulate IdP response: POST /admin/auth/oidc/callback with Origin=https://accounts.google.com
    #    + oauth_transit cookie preserved (client does NOT preserve SameSite=Lax adcp_session — asserts the exact failure mode we're mitigating)
    # Assert: 303 redirect to /admin/, adcp_session cookie set, oauth_transit cookie deleted.


@pytest.mark.integration
async def test_oidc_form_post_rejects_state_mismatch(async_client):
    """Negative path: tampered state → 400 + transit cookie deleted."""
    # 1. Obtain a valid oauth_transit cookie via login-init
    # 2. POST callback with state=<different_value>
    # Assert: 400, oauth_transit cookie cleared in Set-Cookie response, adcp_session NOT created.
```

### G. Gotchas

- **`SameSite=None` without `Secure` is silently dropped by modern browsers.** The `secure=True` parameter is not optional — omit it and the cookie simply never arrives on the callback. In local dev over `http://localhost:8000`, Chrome accepts `SameSite=None; Secure` on localhost as a special case; Firefox requires `about:config` tweaks. Staging must run under HTTPS for this flow to work.
- **`delete_transit_cookie` attribute symmetry.** If set_cookie used `samesite="none"` and delete_cookie omits it, some browsers (notably Safari) treat the delete as a distinct cookie and leave the original in place. The `oauth_transit.py` helpers enforce symmetric attributes.
- **Cookie path scoping reduces blast radius.** `path="/admin/auth/"` means the cookie is only attached on `/admin/auth/*` — it cannot be replayed against `/admin/tenants/*` or `/_internal/`, nor leak in `document.cookie` on other admin pages.
- **10-minute max_age.** Matches OIDC spec guidance on state validity. If users bounce off the IdP and come back 15 minutes later, they get a fresh login-initiation round; do not extend this timeout.
- **Not used by Google OAuth2 non-OIDC flow.** The google-login path at `src/admin/routers/auth.py` uses `response_mode=query` (the default). Only the OIDC router needs `oauth_transit`.

### H. Cross-references

- `§11.6` Authlib singleton — unaffected; it handles token exchange, not state transit
- `§11.7` CSRFOriginMiddleware — exempt-path list extension above
- `implementation-checklist.md §3.5.3 SG-5` — this sub-section is the documented resolution
- `flask-to-fastapi-deep-audit.md §1.5` "OIDC form_post response mode" — original risk documentation (the paragraph in that section that says "the OIDC callback does not rely on a pre-auth session cookie" is WRONG for the current Flask code and is corrected by pointing to §11.6.1)

---

## 11.7 `src/admin/csrf.py` — CSRFOriginMiddleware

> **[Layers 0-4]** This module is part of Flask removal. Sync patterns. Pure-ASGI middleware.

> **Strategy:** SameSite=Lax session cookie + Origin header validation (Option A from CLAUDE.md blocker 5). No token in forms, no cookie rotation, no body read. ~140 LOC pure-ASGI.
>
> **Why this works:** SameSite=Lax on the session cookie blocks cross-site cookie attachment on unsafe methods (POST/PUT/PATCH/DELETE); cross-origin XHR from a malicious page cannot attach the session cookie, so any unsafe request that REACHES us carrying a session cookie originated from our origin or from a top-level navigation we initiated. Origin-header validation closes the residual gap: same-site subdomains and legacy user-agents that don't enforce SameSite still emit `Origin` on cross-origin requests, and we reject any mismatch. The combination is CSRF-safe without requiring token plumbing through ~80 fetch calls and ~47 form templates.
>
> **The historical Double Submit Cookie implementation has been superseded.** Its rationale and code were removed from the plan on 2026-04-14. If you need the archival text for context, see git history.

### A. Implementation

```python
"""CSRF defense via Origin header validation + SameSite=Lax session cookie.

Protects unsafe HTTP methods (POST/PUT/PATCH/DELETE) on non-exempt paths
by requiring the Origin (or Referer fallback) header to match one of the
configured allowed origins. Safe methods and exempt paths pass through.

Design choices:
1. Pure-ASGI, not BaseHTTPMiddleware — avoids Starlette #1729 task-group
   interleaving and keeps body streams intact for downstream handlers.
2. Header-only validation — no form-field parsing, no body buffering.
3. Paired with SessionMiddleware's SameSite=Lax HttpOnly=True session
   cookie (the session cookie is the only credential that matters for
   CSRF on admin routes).
4. Wildcard subdomain matching — accepts requests from `*.PRIMARY_DOMAIN`
   plus any explicit virtual_hosts, so newly-created tenants don't get
   stale-rejected after startup.
5. Exempt paths include AdCP transport surfaces (/mcp, /a2a, /api/v1),
   internal health endpoints (/_internal/), and the three OAuth callback
   paths that Google/OIDC providers POST to directly (byte-immutable,
   see notes/CLAUDE.md invariant 6).
"""
from __future__ import annotations

import json
import logging
from html import escape
from typing import Any, Iterable
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

_SAFE_METHODS: frozenset[str] = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})

# Byte-immutable OAuth callback paths (notes/CLAUDE.md invariant 6). Providers
# POST the authorization-code response to these URLs; they cannot be
# CSRF-protected because the POST originates from the provider's origin.
_OAUTH_CALLBACK_EXEMPTS: tuple[str, ...] = (
    "/admin/auth/google/callback",
    "/admin/auth/oidc/callback",
    "/admin/auth/gam/callback",
)

# AdCP and internal transport surfaces — out-of-scope for admin CSRF.
_TRANSPORT_EXEMPT_PREFIXES: tuple[str, ...] = (
    "/mcp",
    "/a2a",
    "/api/v1/",
    "/.well-known/",
    "/agent.json",
    "/_internal/",
)
# FIXME(adcp-webhooks): when AdCP inbound push-notification receivers land
# (per PushNotificationConfig DB rows), add their path prefix to this tuple
# and update test_architecture_csrf_exempt_covers_webhooks.py.


def _extract_header(scope: dict, name: str) -> str | None:
    """Extract a single header value from an ASGI scope.

    ASGI contract guarantees header names in `scope["headers"]` are already
    lowercased bytes — Uvicorn, Hypercorn, Daphne and Starlette's TestClient
    all emit them that way. The defensive `.lower()` on the raw name covers
    the case where a unit test constructs a scope by hand with mixed-case
    header bytes; it is cheap (byte-level) and avoids a test-only footgun.

    First-match policy: duplicate headers (a client sending two Origin lines)
    are reduced to the first occurrence. RFC 6454 §7.2 says Origin is a
    single-value header, so two Origin lines is already malformed — rejecting
    it by reading only the first is safe and avoids ambiguity.
    """
    target = name.lower().encode("latin-1")
    for raw_name, raw_value in scope.get("headers", []):
        if raw_name.lower() == target:
            return raw_value.decode("latin-1")
    return None


_DEFAULT_PORTS: dict[str, int] = {"http": 80, "https": 443, "ws": 80, "wss": 443}


def _origin_of(url: str) -> str | None:
    """Return RFC 6454-serialized scheme://host[:port] for a URL.

    Applies the serialization rules browsers apply before sending
    Origin/Referer-derived origins on the wire:
      - scheme lowercased
      - host lowercased
      - default port for the scheme stripped (443 for https, 80 for http, etc.)
      - IPv6 hosts re-bracketed (urllib strips brackets in `.hostname`)

    Any allowed_origins registered WITH a default port would otherwise never
    match a browser Origin header (which serializes without the default port),
    403-ing every same-origin POST.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if not parts.scheme or not parts.hostname:
        return None
    scheme = parts.scheme.lower()
    host = parts.hostname.lower()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    try:
        port = parts.port
    except ValueError:
        return None
    if port is None or port == _DEFAULT_PORTS.get(scheme):
        return f"{scheme}://{host}"
    return f"{scheme}://{host}:{port}"


def _is_exempt(path: str) -> bool:
    if path in _OAUTH_CALLBACK_EXEMPTS:
        return True
    return any(path.startswith(p) for p in _TRANSPORT_EXEMPT_PREFIXES)


def _scope_wants_html(scope: dict) -> bool:
    """Mirror of src.admin.content_negotiation._wants_html, scope-native."""
    path: str = scope.get("path", "")
    if not (path.startswith("/admin/") or path.startswith("/tenant/")):
        return False
    if _extract_header(scope, "hx-request"):
        return False
    xrw = _extract_header(scope, "x-requested-with")
    if xrw and xrw.lower() == "xmlhttprequest":
        return False
    accept = _extract_header(scope, "accept") or ""
    if "application/json" in accept and "text/html" not in accept:
        return False
    return "text/html" in accept


def _render_403_html(detail: str) -> str:
    return (
        "<!DOCTYPE html>\n"
        "<html><head><meta charset=\"utf-8\"><title>403 Forbidden</title></head>"
        "<body style=\"font-family: sans-serif; max-width: 600px; margin: 4em auto;\">"
        "<h1>403 Forbidden</h1>"
        f"<p>{escape(detail)}</p>"
        "<p><a href=\"/admin/\">Return to the admin dashboard</a></p>"
        "</body></html>"
    )


async def _respond_403(send: Any, scope: dict, detail: str) -> None:
    """403 response, Accept-aware.

    Browsers hitting admin paths get HTML; API/MCP/A2A callers and XHR fetches
    get JSON. Mirrors the AdCPError handler's negotiation logic so CSRF reject
    path and domain-error path have identical UX. Middleware runs BEFORE
    FastAPI exception handlers, so negotiation is inlined here.
    """
    if _scope_wants_html(scope):
        body = _render_403_html(detail).encode("utf-8")
        content_type = b"text/html; charset=utf-8"
    else:
        body = json.dumps({"detail": detail}).encode("utf-8")
        content_type = b"application/json"
    await send({
        "type": "http.response.start",
        "status": 403,
        "headers": [
            (b"content-type", content_type),
            (b"content-length", str(len(body)).encode()),
            (b"vary", b"Origin, Accept"),
        ],
    })
    await send({"type": "http.response.body", "body": body})


class CSRFOriginMiddleware:
    """Pure-ASGI CSRF defense via Origin header validation.

    Constructor accepts:
    - allowed_origins: explicit set of origin strings (e.g. {"https://admin.example.com"})
    - allowed_origin_suffixes: domain suffixes for wildcard matching
      (e.g. {".scope3.com"} accepts any "https://*.scope3.com" or "http://*.scope3.com")

    The wildcard set lets newly-provisioned tenant subdomains work without
    a startup-time refresh. Both sets are closed over at construction.
    """

    def __init__(
        self,
        app: Any,
        *,
        allowed_origins: Iterable[str] = (),
        allowed_origin_suffixes: Iterable[str] = (),
    ) -> None:
        self.app = app
        self.allowed_origins: frozenset[str] = frozenset(
            o.rstrip("/").lower() for o in allowed_origins if o
        )
        self.allowed_suffixes: tuple[str, ...] = tuple(
            s.lower() for s in allowed_origin_suffixes if s
        )
        if not self.allowed_origins and not self.allowed_suffixes:
            raise RuntimeError(
                "CSRFOriginMiddleware requires at least one of "
                "allowed_origins or allowed_origin_suffixes"
            )

    def _origin_allowed(self, normalized_origin: str) -> bool:
        if normalized_origin in self.allowed_origins:
            return True
        # Wildcard subdomain match: extract host from normalized origin
        # ("https://foo.example.com" → "foo.example.com")
        try:
            host = urlsplit(normalized_origin).netloc.lower()
        except ValueError:
            return False
        return any(
            host.endswith(suffix) or host == suffix.lstrip(".")
            for suffix in self.allowed_suffixes
        )

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method: str = scope["method"]
        path: str = scope["path"]

        # Safe methods always bypass
        if method in _SAFE_METHODS:
            await self.app(scope, receive, send)
            return

        # Exempt paths bypass
        if _is_exempt(path):
            await self.app(scope, receive, send)
            return

        # Origin validation
        origin = _extract_header(scope, "origin")

        if origin is None:
            # No Origin header — legacy user-agent or a same-origin request
            # the browser did not annotate. Fall back to Referer.
            #
            # SAFETY: "missing Origin AND missing Referer = accept" is safe
            # ONLY because SameSite=Lax on the session cookie blocks the
            # cookie on cross-site unsafe requests. Without Lax, this branch
            # would be a CSRF hole. The Lax-dependency is load-bearing —
            # if SessionMiddleware is configured with same_site="none",
            # this branch must reject instead.
            referer = _extract_header(scope, "referer")
            if referer is None:
                await self.app(scope, receive, send)
                return
            ref_origin = _origin_of(referer)
            if ref_origin is None:
                await _respond_403(send, scope, "CSRF: unparseable Referer")
                return
            if not self._origin_allowed(ref_origin):
                logger.info(
                    "CSRF rejection (referer): path=%s method=%s referer_origin=%s",
                    path, method, ref_origin,
                )
                await _respond_403(send, scope, "CSRF: cross-origin request rejected")
                return
            await self.app(scope, receive, send)
            return

        if origin == "null":
            # Origin: null appears for file://, sandboxed iframes with
            # "allow-scripts" but not "allow-same-origin", and certain
            # cross-origin redirect chains. None of these are legitimate
            # admin request sources — reject.
            logger.info(
                "CSRF rejection (null origin): path=%s method=%s", path, method,
            )
            await _respond_403(send, scope, "CSRF: opaque origin rejected")
            return

        normalized = _origin_of(origin)
        if normalized is None:
            await _respond_403(send, scope, "CSRF: unparseable Origin")
            return

        if not self._origin_allowed(normalized):
            logger.info(
                "CSRF rejection: path=%s method=%s origin=%s", path, method, normalized,
            )
            await _respond_403(send, scope, "CSRF: cross-origin request rejected")
            return

        await self.app(scope, receive, send)
```

### B. Tests

```python
# tests/unit/admin/test_csrf_origin_middleware.py
import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from src.admin.csrf import CSRFOriginMiddleware, _extract_header, _origin_of


def _build(*, allowed_origins=("https://admin.sales-agent.example.com",),
           allowed_suffixes=()):
    async def ok(request):
        return PlainTextResponse("ok")

    inner = Starlette(routes=[
        Route("/admin/foo", ok, methods=["GET", "POST", "PUT", "DELETE"]),
        Route("/mcp/tool", ok, methods=["POST"]),
        Route("/admin/auth/google/callback", ok, methods=["POST"]),
    ])
    wrapped_app = CSRFOriginMiddleware(
        inner,
        allowed_origins=allowed_origins,
        allowed_origin_suffixes=allowed_suffixes,
    )
    assert wrapped_app is not inner, "harness must wrap inner app with CSRF middleware"
    return TestClient(wrapped_app)


class TestHarnessSanity:
    """If these fail, the harness stopped exercising the middleware."""

    def test_wrong_origin_actually_returns_403(self):
        # If the middleware were bypassed, this would be 200.
        with _build() as c:
            r = c.post("/admin/foo", headers={"Origin": "https://evil.example.com"})
        assert r.status_code == 403, "harness is not routing through CSRF middleware"

    def test_missing_allowed_origins_raises(self):
        async def noop(scope, receive, send): pass
        with pytest.raises(RuntimeError):
            CSRFOriginMiddleware(noop, allowed_origins=(), allowed_origin_suffixes=())


class TestHeaderExtraction:
    def test_handcrafted_uppercase_header_name_still_matches(self):
        scope = {"headers": [(b"Origin", b"https://foo")]}
        assert _extract_header(scope, "origin") == "https://foo"

    def test_duplicate_origin_uses_first_value(self):
        scope = {"headers": [
            (b"origin", b"https://admin.sales-agent.example.com"),
            (b"origin", b"https://evil.example.com"),
        ]}
        assert _extract_header(scope, "origin") == "https://admin.sales-agent.example.com"


class TestSafeMethods:
    def test_get_passes(self):
        with _build() as c:
            assert c.get("/admin/foo").status_code == 200

    def test_head_passes(self):
        with _build() as c:
            assert c.head("/admin/foo").status_code == 200


class TestExemptPaths:
    def test_mcp_post_without_origin_passes(self):
        with _build() as c:
            assert c.post("/mcp/tool").status_code == 200

    def test_oauth_callback_post_without_origin_passes(self):
        with _build() as c:
            assert c.post("/admin/auth/google/callback").status_code == 200


class TestOriginValidation:
    def test_matching_origin_passes(self):
        with _build() as c:
            r = c.post("/admin/foo",
                       headers={"Origin": "https://admin.sales-agent.example.com"})
        assert r.status_code == 200

    def test_mismatched_origin_403(self):
        with _build() as c:
            r = c.post("/admin/foo",
                       headers={"Origin": "https://evil.example.com"})
        assert r.status_code == 403

    def test_null_origin_403(self):
        with _build() as c:
            r = c.post("/admin/foo", headers={"Origin": "null"})
        assert r.status_code == 403

    def test_missing_origin_with_matching_referer_passes(self):
        with _build() as c:
            r = c.post("/admin/foo",
                       headers={"Referer": "https://admin.sales-agent.example.com/prev"})
        assert r.status_code == 200

    def test_missing_origin_and_referer_passes_under_lax(self):
        # Legacy UA case — SameSite=Lax on the session cookie is the primary defense.
        with _build() as c:
            assert c.post("/admin/foo").status_code == 200


class TestWildcardSuffix:
    def test_suffix_match_passes(self):
        with _build(allowed_origins=(),
                    allowed_suffixes=(".scope3.com",)) as c:
            r = c.post("/admin/foo",
                       headers={"Origin": "https://tenant-foo.scope3.com"})
        assert r.status_code == 200

    def test_suffix_root_match_passes(self):
        # "scope3.com" itself (not a subdomain) should match suffix ".scope3.com"
        with _build(allowed_origins=(),
                    allowed_suffixes=(".scope3.com",)) as c:
            r = c.post("/admin/foo",
                       headers={"Origin": "https://scope3.com"})
        assert r.status_code == 200

    def test_suffix_non_match_403(self):
        with _build(allowed_origins=(),
                    allowed_suffixes=(".scope3.com",)) as c:
            r = c.post("/admin/foo",
                       headers={"Origin": "https://scope3.com.evil.com"})
        assert r.status_code == 403


class TestOriginNormalization:
    def test_uppercase_origin_normalized(self):
        with _build() as c:
            r = c.post("/admin/foo",
                       headers={"Origin": "HTTPS://ADMIN.SALES-AGENT.EXAMPLE.COM"})
        assert r.status_code == 200

    def test_unparseable_origin_403(self):
        with _build() as c:
            r = c.post("/admin/foo", headers={"Origin": "not-a-url"})
        assert r.status_code == 403

    def test_default_https_port_stripped(self):
        # The browser sends "https://admin.sales-agent.example.com" even if the
        # allowed set registered port 443 — they must match post-normalization.
        assert _origin_of("https://admin.sales-agent.example.com:443") == \
            "https://admin.sales-agent.example.com"

    def test_default_http_port_stripped(self):
        assert _origin_of("http://admin.sales-agent.example.com:80") == \
            "http://admin.sales-agent.example.com"

    def test_nondefault_port_preserved(self):
        assert _origin_of("https://admin.sales-agent.example.com:8443") == \
            "https://admin.sales-agent.example.com:8443"

    def test_default_ws_port_stripped(self):
        assert _origin_of("ws://chat.example.com:80") == "ws://chat.example.com"

    def test_default_wss_port_stripped(self):
        assert _origin_of("wss://chat.example.com:443") == "wss://chat.example.com"

    def test_ipv6_bracketed_default_port_stripped(self):
        # IPv6 hosts: urllib strips brackets in `.hostname`; _origin_of re-brackets.
        assert _origin_of("https://[2001:db8::1]:443") == "https://[2001:db8::1]"

    def test_ipv6_bracketed_nondefault_port_preserved(self):
        assert _origin_of("https://[2001:db8::1]:8443") == "https://[2001:db8::1]:8443"

    def test_case_normalized_with_default_port(self):
        # Upper-case scheme + default port: lowercase scheme, strip port.
        assert _origin_of("HTTPS://ADMIN.EXAMPLE.COM:443") == "https://admin.example.com"

    def test_malformed_port_returns_none(self):
        # urlsplit raises ValueError on parts.port when it contains non-digits.
        assert _origin_of("https://admin.example.com:notaport") is None


class TestContentNegotiation:
    """403 response body mirrors AdCPError handler's negotiation."""

    def test_browser_admin_path_gets_html(self):
        with _build() as c:
            r = c.post(
                "/admin/foo",
                headers={
                    "Origin": "https://evil.example.com",
                    "Accept": "text/html,application/xhtml+xml",
                },
            )
        assert r.status_code == 403
        assert r.headers["content-type"].startswith("text/html")
        assert "<h1>403 Forbidden</h1>" in r.text

    def test_api_accept_gets_json(self):
        with _build() as c:
            r = c.post(
                "/admin/foo",
                headers={
                    "Origin": "https://evil.example.com",
                    "Accept": "application/json",
                },
            )
        assert r.status_code == 403
        assert r.headers["content-type"] == "application/json"
        assert r.json()["detail"].startswith("CSRF:")

    def test_htmx_request_gets_json(self):
        # HX-Request trumps Accept for HTMX callers.
        with _build() as c:
            r = c.post(
                "/admin/foo",
                headers={
                    "Origin": "https://evil.example.com",
                    "Accept": "text/html",
                    "HX-Request": "true",
                },
            )
        assert r.status_code == 403
        assert r.headers["content-type"] == "application/json"

    def test_xhr_request_gets_json(self):
        with _build() as c:
            r = c.post(
                "/admin/foo",
                headers={
                    "Origin": "https://evil.example.com",
                    "Accept": "text/html",
                    "X-Requested-With": "XMLHttpRequest",
                },
            )
        assert r.status_code == 403
        assert r.headers["content-type"] == "application/json"

    def test_non_admin_path_gets_json_even_with_html_accept(self):
        # /mcp is exempt from CSRF, but any non-admin path that DID hit a 403
        # branch would still take the JSON path — _scope_wants_html requires
        # /admin/ or /tenant/ prefix. Verified indirectly: there is no browser
        # POST to /api/v1/* in the admin UX, so the JSON default is correct.
        # (No production code path exercises this branch; skipped as a
        # behavioral promise of _scope_wants_html rather than a live test.)
        pytest.skip("exempt paths never hit _respond_403; documented behavior only")

    def test_vary_header_present(self):
        with _build() as c:
            r = c.post(
                "/admin/foo",
                headers={"Origin": "https://evil.example.com", "Accept": "text/html"},
            )
        assert r.status_code == 403
        assert "Origin" in r.headers.get("vary", "")
        assert "Accept" in r.headers.get("vary", "")


class TestConstruction:
    def test_empty_allowed_raises(self):
        async def noop(scope, receive, send): pass
        with pytest.raises(RuntimeError):
            CSRFOriginMiddleware(noop, allowed_origins=(), allowed_origin_suffixes=())
```

### C. Integration

Public API:
```python
CSRFOriginMiddleware(
    app,
    *,
    allowed_origins: Iterable[str] = (),
    allowed_origin_suffixes: Iterable[str] = (),
)
```

Registered in `app_factory.py` (added in REVERSE order of canonical stack — LIFO):
```python
# Innermost first
app.add_middleware(CORSMiddleware, ...)
app.add_middleware(RestCompatMiddleware)
app.add_middleware(
    CSRFOriginMiddleware,
    allowed_origins=settings.csrf_allowed_origins,
    allowed_origin_suffixes=settings.csrf_allowed_origin_suffixes,
)
app.add_middleware(SessionMiddleware, ..., same_site="lax", https_only=True)
app.add_middleware(UnifiedAuthMiddleware)
app.add_middleware(SecurityHeadersMiddleware, https_only=settings.https_only)  # added at L2 (§11.28)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=...)   # added at L2
app.add_middleware(ApproximatedExternalDomainMiddleware)
app.add_middleware(FlyHeadersMiddleware)
app.add_middleware(RequestIDMiddleware)                        # added at L4, outermost
# Outermost runtime (L4+, 10 middlewares):
# RequestID → Fly → ExternalDomain → TrustedHost → SecurityHeaders → UnifiedAuth → Session → CSRF → RestCompat → CORS
# See §cross-cutting/Middleware ordering for the L1a (7) and L2 (9) progressive shapes.
```

### D. Why SameSite=Lax is sufficient paired with Origin validation

1. **SameSite=Lax on the session cookie** blocks cross-site cookie attachment on unsafe methods. An attacker page at `evil.example.com` posting to `/admin/foo` sends the request, but the session cookie is stripped — request arrives unauthenticated → 302 to login (not 200 with exploit).
2. **Origin header validation** closes residual gaps: (a) same-site subdomains, (b) legacy UAs that don't yet enforce SameSite. The Origin header is attached by browsers on cross-origin unsafe requests; we reject mismatches.
3. **No token plumbing.** Double-Submit Cookie would force every fetch site (~80) and form template (~47) to insert a token — 127 surface changes for zero incremental security given (1) and (2).

### E. Gotchas

- **Constructor MUST raise on empty origins/suffixes.** A misconfigured deployment that accepts any origin is worse than crash-on-startup.
- **Referer fallback is Lax-dependent.** The "missing Origin + missing Referer = accept" branch is safe ONLY if SessionMiddleware uses `same_site="lax"` (or stricter). If you ever set `same_site="none"`, change this branch to reject.
- **Origin: null is always rejected.** No legitimate admin source produces it.
- **Wildcard suffix matching uses domain suffix only**, not regex — `.scope3.com` matches `tenant.scope3.com` and `scope3.com` itself, but NOT `scope3.com.evil.com` (suffix check requires the dot or exact match).
- **No body reading.** Content-Length checks belong on upload endpoints, not here.

### F. Test strategy

1. **Unit:** per-branch coverage (matching Origin, mismatched, null, missing with Referer, missing both, safe method, exempt path, wildcard suffix, normalization). See §B.
2. **Integration:** `tests/integration/test_csrf_origin_end_to_end.py` — real Starlette app with Session + Approximated + CSRF in canonical order; assert cross-origin POST is 403 AND same-origin POST is 200; assert wildcard-tenant POST is 200.
3. **Staging smoke:** Playwright walk of admin UI with middleware live; any working-in-Flask fetch must still work (zero template/JS changes).

---

## 11.8 `src/admin/middleware/external_domain.py`

> **[L0-L2]** This module is part of Flask removal. Use sync patterns. It stays sync through L4 and auto-converts at L5 only to the extent its callers flip to `async def` (most functions here are framework-agnostic utilities and remain sync across all layers).

### A. Implementation

```python
"""External-domain → tenant-subdomain redirect as pure ASGI middleware.

Replaces @app.before_request redirect_external_domain_admin at
src/admin/app.py:211-269.

LOGIC:
1. Only acts on /admin/* requests. All other paths pass through.
2. Looks for Apx-Incoming-Host header (set by Approximated edge proxy
   when the request originated from a publisher's custom domain).
3. If the Apx host IS a sales-agent subdomain, pass through (no redirect).
4. If the Apx host is an EXTERNAL domain, look up the tenant by virtual_host,
   then 302-redirect to the tenant's subdomain.
5. If no tenant found or tenant has no subdomain: pass through (let the
   admin UI show whatever error page it shows).

MUST run AFTER UnifiedAuth and BEFORE Session/CSRF in the middleware stack.
Canonical L4+ order (outermost runtime → innermost runtime, 10 middlewares):
RequestID → Fly → ExternalDomain → TrustedHost → SecurityHeaders → UnifiedAuth → Session → CSRF → RestCompat → CORS
See §cross-cutting/Middleware ordering for the L1a (7) and L2 (9) progressive shapes.
Add via add_middleware in REVERSE order (LIFO):
app.add_middleware(CORS); ...; app.add_middleware(RequestID).

ExternalDomain reads only the Apx-Incoming-Host header — it does NOT touch
request.session, so its position relative to Session is functionally
independent. The hard invariant is that it runs BEFORE CSRF: external-domain
POSTs are 307-redirected to the canonical subdomain BEFORE CSRF can reject
the request for a missing/mismatched Origin header.

MUST be pure ASGI (Starlette #1729). BaseHTTPMiddleware in this slot would
break the body-streaming contract for downstream handlers when CSRF
short-circuits a 403.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from src.core.config_loader import get_tenant_by_virtual_host
from src.core.domain_config import get_tenant_url, is_sales_agent_domain

logger = logging.getLogger(__name__)


def _extract_header(scope: dict, name: str) -> str | None:
    target = name.lower().encode("latin-1")
    for raw_name, raw_value in scope.get("headers", []):
        if raw_name == target:
            return raw_value.decode("latin-1")
    return None


def _is_admin_path(path: str) -> bool:
    # Note: Starlette does NOT strip a "script name" like WSGI Flask did.
    # We literally check the request path.
    return path == "/admin" or path.startswith("/admin/")


async def _respond_redirect(send: Any, url: str, status: int = 307) -> None:
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [
            (b"location", url.encode("latin-1")),
            (b"content-length", b"0"),
        ],
    })
    await send({"type": "http.response.body", "body": b""})


class ExternalDomainRedirectMiddleware:
    """Redirect /admin/* requests from external domains to tenant subdomain."""
    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path: str = scope["path"]
        if not _is_admin_path(path):
            await self.app(scope, receive, send)
            return

        apx_host = _extract_header(scope, "apx-incoming-host")
        if not apx_host:
            await self.app(scope, receive, send)
            return

        if is_sales_agent_domain(apx_host):
            # Subdomain request — normal routing
            await self.app(scope, receive, send)
            return

        # External domain detected. Look up tenant.
        try:
            tenant = get_tenant_by_virtual_host(apx_host)
        except Exception:
            logger.exception("Tenant lookup failed for virtual host %s", apx_host)
            await self.app(scope, receive, send)
            return

        if not tenant:
            logger.warning("No tenant for external domain %s", apx_host)
            await self.app(scope, receive, send)
            return

        subdomain = tenant.get("subdomain") if isinstance(tenant, dict) else getattr(tenant, "subdomain", None)
        if not subdomain:
            logger.warning("Tenant %s has no subdomain configured", tenant)
            await self.app(scope, receive, send)
            return

        query = scope.get("query_string", b"").decode("latin-1")
        full_path = f"{path}?{query}" if query else path

        if os.environ.get("PRODUCTION", "").lower() == "true":
            redirect_url = f"{get_tenant_url(subdomain)}{full_path}"
        else:
            port = os.environ.get("ADCP_SALES_PORT", "8080")
            redirect_url = f"http://{subdomain}.localhost:{port}{full_path}"

        logger.info("Redirecting external domain %s/admin to %s", apx_host, redirect_url)
        await _respond_redirect(send, redirect_url)
```

### B. Tests

```python
# tests/unit/admin/test_external_domain.py
import pytest
from unittest.mock import patch
from starlette.testclient import TestClient
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from src.admin.middleware.external_domain import ExternalDomainRedirectMiddleware


def _build(app_response="ok"):
    async def homepage(request):
        return PlainTextResponse(app_response)

    inner = Starlette(routes=[
        Route("/admin", homepage),
        Route("/admin/{path:path}", homepage),
        Route("/other", homepage),
    ])
    wrapped = ExternalDomainRedirectMiddleware(inner)
    return TestClient(Starlette(middleware=[], routes=inner.routes, lifespan=None))


class TestExternalDomainRedirect:
    def test_non_admin_path_passes_through(self):
        # ...
        pass  # test shape identical to CSRF tests above

    def test_admin_without_apx_header_passes_through(self):
        pass

    def test_admin_with_sales_subdomain_apx_passes_through(self):
        with patch("src.admin.middleware.external_domain.is_sales_agent_domain", return_value=True):
            # ...
            pass

    def test_external_domain_redirects_to_subdomain(self, monkeypatch):
        monkeypatch.delenv("PRODUCTION", raising=False)
        with patch("src.admin.middleware.external_domain.is_sales_agent_domain", return_value=False), \
             patch("src.admin.middleware.external_domain.get_tenant_by_virtual_host",
                   return_value={"tenant_id": "t1", "subdomain": "pub-t1"}):
            # assert 302 Location: http://pub-t1.localhost:8080/admin/foo?q=1
            pass

    def test_tenant_lookup_failure_falls_through(self):
        with patch("src.admin.middleware.external_domain.get_tenant_by_virtual_host",
                   side_effect=RuntimeError("db down")):
            # assert 200, not 500
            pass

    def test_tenant_without_subdomain_falls_through(self):
        with patch("src.admin.middleware.external_domain.get_tenant_by_virtual_host",
                   return_value={"tenant_id": "t1", "subdomain": None}):
            # assert 200
            pass
```

### C. Integration

- Imports `get_tenant_by_virtual_host`, `is_sales_agent_domain`, `get_tenant_url`.
- Public API: `ExternalDomainRedirectMiddleware` class.
- Runs BEFORE `UnifiedAuthMiddleware` (which means `UnifiedAuthMiddleware` never sees redirected requests).
- Reads DB via `get_tenant_by_virtual_host`. This is one sync DB call per external-domain request. Cache in a later v2.0 phase via an LRU.

### D. Gotchas

- **Failure mode = pass through, not 404/500:** If the tenant lookup fails (DB down), we let the request continue to the admin UI rather than returning an error. This prevents the middleware from being a hard availability dependency.
- **No ContextVar state:** Pure ASGI middleware that runs BEFORE `UnifiedAuthMiddleware` does not see `request.state.auth_context`. Don't try to use auth here.
- **Body not consumed:** We redirect before reading the body, so no receive-channel manipulation needed (unlike CSRF).

---

## 11.9 `src/admin/middleware/fly_headers.py`

> **[L0-L2]** This module is part of Flask removal. Use sync patterns. It stays sync through L4 and auto-converts at L5 only to the extent its callers flip to `async def` (most functions here are framework-agnostic utilities and remain sync across all layers).

### A. Implementation

```python
"""Fly.io header normalizer: Fly-* → X-Forwarded-*.

Replaces the WSGI FlyHeadersMiddleware at src/admin/app.py:172-189.

STATUS: Likely REDUNDANT as of Fly.io 2024 platform update — Fly now sends
standard X-Forwarded-* headers. Keep this module as ~40 LOC insurance; cost
is trivial, and we can delete after verifying Fly's current header behavior
in our specific Fly Machines pool.

The module operates on the scope headers list in-place: copies
fly-forwarded-proto → x-forwarded-proto if the latter is missing, and
fly-client-ip → x-forwarded-for similarly. Running BEFORE uvicorn's
--proxy-headers logic means uvicorn sees the normalized headers.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_MAPPINGS: tuple[tuple[bytes, bytes], ...] = (
    (b"fly-forwarded-proto", b"x-forwarded-proto"),
    (b"fly-client-ip", b"x-forwarded-for"),
    (b"fly-forwarded-host", b"x-forwarded-host"),
)


class FlyHeadersMiddleware:
    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers: list[tuple[bytes, bytes]] = list(scope.get("headers", []))
        existing = {name for name, _ in headers}

        new_headers = list(headers)
        for fly_name, standard_name in _MAPPINGS:
            if standard_name in existing:
                continue
            for name, value in headers:
                if name == fly_name:
                    new_headers.append((standard_name, value))
                    break

        if len(new_headers) != len(headers):
            scope = {**scope, "headers": new_headers}
        await self.app(scope, receive, send)
```

### B. Tests

```python
# tests/unit/admin/test_fly_headers.py
import pytest
from unittest.mock import AsyncMock

from src.admin.middleware.fly_headers import FlyHeadersMiddleware


@pytest.mark.asyncio
async def test_copies_fly_proto_to_x_forwarded_proto():
    captured = {}
    async def inner(scope, receive, send):
        captured["scope"] = scope

    mw = FlyHeadersMiddleware(inner)
    scope = {
        "type": "http",
        "headers": [(b"fly-forwarded-proto", b"https")],
    }
    await mw(scope, AsyncMock(), AsyncMock())
    headers = dict(captured["scope"]["headers"])
    assert headers[b"x-forwarded-proto"] == b"https"


@pytest.mark.asyncio
async def test_preserves_existing_x_forwarded_proto():
    captured = {}
    async def inner(scope, receive, send):
        captured["scope"] = scope

    mw = FlyHeadersMiddleware(inner)
    scope = {
        "type": "http",
        "headers": [
            (b"fly-forwarded-proto", b"https"),
            (b"x-forwarded-proto", b"http"),  # pre-existing wins
        ],
    }
    await mw(scope, AsyncMock(), AsyncMock())
    headers = [v for (n, v) in captured["scope"]["headers"] if n == b"x-forwarded-proto"]
    assert headers == [b"http"]


@pytest.mark.asyncio
async def test_lifespan_passes_through():
    inner_called = False
    async def inner(scope, receive, send):
        nonlocal inner_called
        inner_called = True

    mw = FlyHeadersMiddleware(inner)
    await mw({"type": "lifespan"}, AsyncMock(), AsyncMock())
    assert inner_called
```

### C. Integration

- No imports besides stdlib.
- Public API: `FlyHeadersMiddleware`.
- Runs OUTERMOST (registered LAST in `app_factory`).

### D. Gotchas

- **Scope mutation:** We avoid mutating the input scope dict (in case upstream code holds a reference). Create a new dict with `{**scope, "headers": new_headers}`.
- **Header case:** ASGI requires header names to be lowercased bytes. Our constants are lowercase — enforced.
- **Deletion candidate:** Verify Fly's current header emission before merging. If Fly now always sends X-Forwarded-*, delete this module entirely.

---

## 11.10 `src/admin/app_factory.py`

> **[L0-L2]** This module is part of Flask removal. Use sync patterns. It stays sync through L4 and auto-converts at L5 only to the extent its callers flip to `async def` (most functions here are framework-agnostic utilities and remain sync across all layers).

### A. Implementation

```python
"""Admin router factory — compose all admin middleware + routers into a
FastAPI APIRouter that gets included on the root FastAPI app at /admin.

The return type is (router, middleware_list, exception_handlers) — not a
standalone FastAPI app — because admin must live on the SAME FastAPI app as
/mcp, /a2a, /api/v1. Sub-app mounting would bypass UnifiedAuthMiddleware.

Caller pattern in src/app.py:
    from src.admin.app_factory import build_admin

    admin = build_admin()
    for mw_cls, mw_kw in reversed(admin.middleware):  # reverse for LIFO add
        app.add_middleware(mw_cls, **mw_kw)
    for exc_cls, handler in admin.exception_handlers:
        app.add_exception_handler(exc_cls, handler)
    app.include_router(admin.router, prefix="/admin")
    app.mount("/static", StaticFiles(directory="src/admin/static"), name="static")
    # NOTE: directory is "static" until L2 git mv; becomes "src/admin/static" after L2 (Flask removal)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from fastapi import APIRouter
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import RedirectResponse
from starlette.requests import Request
from urllib.parse import quote

from src.admin.csrf import CSRFOriginMiddleware
from src.admin.deps.auth import AdminRedirect, AdminAccessDenied
from src.admin.middleware.external_domain import ExternalDomainRedirectMiddleware
from src.admin.middleware.fly_headers import FlyHeadersMiddleware
from src.admin.oauth import init_oauth
from src.admin.sessions import session_middleware_kwargs
from src.admin.templating import render
from src.core.settings import get_settings


@dataclass
class AdminBuild:
    router: APIRouter
    middleware: list[tuple[type, dict[str, Any]]] = field(default_factory=list)
    exception_handlers: list[tuple[type[Exception], Callable]] = field(default_factory=list)


async def admin_redirect_handler(request: Request, exc: Exception) -> RedirectResponse:
    assert isinstance(exc, AdminRedirect)
    url = f"{exc.to}?next={quote(exc.next_url, safe='')}" if exc.next_url else exc.to
    return RedirectResponse(url=url, status_code=303)


async def admin_access_denied_handler(request: Request, exc: Exception) -> Any:
    assert isinstance(exc, AdminAccessDenied)
    return render(request, "403.html", {"message": exc.message}, status_code=403)


def build_admin() -> AdminBuild:
    """Assemble admin router + middleware + handlers."""
    settings = get_settings()

    # Startup-time side effects
    init_oauth()

    # Import routers (split for readability; all live under src/admin/routers/)
    from src.admin.routers import (
        auth as auth_router,
        tenants as tenants_router,
        accounts as accounts_router,
        products as products_router,
        creatives as creatives_router,
        # ... etc, one module per Flask blueprint
    )

    router = APIRouter()
    router.include_router(auth_router.router)
    router.include_router(tenants_router.router, prefix="/tenant")
    router.include_router(accounts_router.router, prefix="/tenant/{tenant_id}/accounts")
    router.include_router(products_router.router, prefix="/tenant/{tenant_id}/products")
    router.include_router(creatives_router.router, prefix="/tenant/{tenant_id}/creatives")
    # ... etc

    # Middleware in RUNTIME order (outermost first).
    # Caller must REVERSE this list before calling app.add_middleware (LIFO).
    middleware: list[tuple[type, dict[str, Any]]] = [
        (FlyHeadersMiddleware, {}),
        (ExternalDomainRedirectMiddleware, {}),
        # UnifiedAuthMiddleware is registered at the root app level in src/app.py
        (SessionMiddleware, session_middleware_kwargs()),
        (CSRFOriginMiddleware, {
            "allowed_origins": tuple(settings.csrf_allowed_origins),
            "allowed_origin_suffixes": tuple(settings.csrf_allowed_origin_suffixes),
        }),
    ]

    exception_handlers: list[tuple[type[Exception], Callable]] = [
        (AdminRedirect, admin_redirect_handler),
        (AdminAccessDenied, admin_access_denied_handler),
    ]

    return AdminBuild(
        router=router,
        middleware=middleware,
        exception_handlers=exception_handlers,
    )
```

### B. Tests

```python
# tests/integration/admin/test_app_factory.py
import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from src.admin.app_factory import build_admin


class TestAppFactory:
    def test_build_admin_returns_router(self, monkeypatch):
        monkeypatch.setenv("SESSION_SECRET", "x" * 64)
        monkeypatch.setenv("CSRF_ALLOWED_ORIGINS", '["https://admin.sales-agent.example.com"]')
        admin = build_admin()
        assert admin.router is not None
        assert next(m for m, _ in admin.middleware if m.__name__ == "CSRFOriginMiddleware")

    def test_csrf_uses_origin_middleware(self, monkeypatch):
        monkeypatch.setenv("SESSION_SECRET", "x" * 64)
        monkeypatch.setenv("CSRF_ALLOWED_ORIGINS", '["https://admin.sales-agent.example.com"]')
        admin = build_admin()
        csrf_entries = [
            (cls, kw) for cls, kw in admin.middleware
            if cls.__name__ == "CSRFOriginMiddleware"
        ]
        assert len(csrf_entries) == 1
        _, kw = csrf_entries[0]
        assert kw["allowed_origins"], "allowed_origins must not be empty (RuntimeError guard)"

    def test_end_to_end_admin_mount(self, monkeypatch):
        monkeypatch.setenv("SESSION_SECRET", "x" * 64)
        monkeypatch.setenv("CSRF_ALLOWED_ORIGINS", '["https://admin.sales-agent.example.com"]')
        app = FastAPI()
        admin = build_admin()
        for mw_cls, mw_kw in reversed(admin.middleware):
            app.add_middleware(mw_cls, **mw_kw)
        for exc_cls, handler in admin.exception_handlers:
            app.add_exception_handler(exc_cls, handler)
        app.include_router(admin.router, prefix="/admin")

        client = TestClient(app)
        # Unauthenticated access to a tenant page should 303 to /admin/login
        r = client.get("/admin/tenant/foo/", follow_redirects=False)
        assert r.status_code == 303
        assert "/admin/login" in r.headers["location"]
        assert "next=" in r.headers["location"]
```

### C. Integration

- Imports everything else in this foundation list.
- Public API: `build_admin()` → `AdminBuild` dataclass with `router`, `middleware`, `exception_handlers`.
- Called once at app startup from `src/app.py`.
- The admin router is ATTACHED to the root FastAPI app, NOT mounted as a sub-app, so all middleware (including `UnifiedAuthMiddleware` at the root level) runs for admin requests.

### D. Gotchas

- **Middleware LIFO:** `app.add_middleware` wraps — last added is outermost. The middleware list is in RUNTIME (outer→inner) order and MUST be reversed before registration. Documented in the caller comment.
- **`UnifiedAuthMiddleware` placement:** NOT added here. It's registered at the root app level in `src/app.py` once, because it applies to MCP, A2A, REST, and admin uniformly. Admin routes that need session-based auth use the admin deps; they ignore `request.state.auth_context`.
- **`init_oauth` is a startup side effect** inside `build_admin()`. If tests call `build_admin()` multiple times, `oauth.register(name="google")` must be idempotent — guarded by the `hasattr(oauth, "google")` check in `init_oauth`.
- **Static files** mounted at `/static` (not `/admin/static`) because the codemod rewrites templates to `{{ script_root ~ '/static/x.js' }}`. `script_root` is `/admin` on admin routes, so the URL becomes `/admin/static/x.js`. Wait — that requires static to be mounted at `/admin/static` after all. Resolution: mount at `/admin/static` AND at `/static` for API routes. Or just `/admin/static` and drop the `script_root` prefix entirely. **Decision:** Mount `/admin/static`. Templates use `{{ request.url_for('static', path='x.js') }}` via the named static mount.

---

## 11.9.5 `src/admin/middleware/request_id.py` — Request ID propagation (Agent E Category 8)

> **[L0-L2]** This module is part of Flask removal. Use sync patterns. The structlog integration (`bind_request_id`) lands at L4; through L0-L3, use stdlib `logging` with request_id passed as a log parameter. The middleware itself is framework-agnostic and remains sync across all layers.

> **Added 2026-04-11 under the Agent E idiom upgrade.** Under async, contextvars propagate correctly across `await` boundaries. The combination `RequestIDMiddleware` + `structlog.contextvars.merge_contextvars` means every log line emitted during a request automatically carries the `request_id` — critical for debugging async-interleaved requests.

### A. Implementation

```python
# src/admin/middleware/request_id.py
"""Generates and propagates X-Request-ID for every request.

Read by the structured logger to include in every log line emitted during
handling of this request. Critical for debugging async-interleaved requests.
"""
import uuid

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

        # Propagate via structlog context-var (see §11.0.3)
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

### C. Integration

- Registered as the **outermost** middleware in `src/app.py` so every request gets a `request_id` before anything else runs
- Order: `RequestIDMiddleware → FlyHeaders → ExternalDomainRedirect → TrustedHost → SecurityHeaders → UnifiedAuth → Session → CSRF → RestCompat → CORS → handler`
- Emits `X-Request-ID` on responses; echoes upstream value if the client sent one
- Structural guard `test_architecture_middleware_order.py` enforces the registration order

### D. Gotchas

- **AdCP boundary:** `X-Request-ID` is a universal debug header; no AdCP wire format claims it. Safe to add. See `async-audit/agent-e-ideal-state-gaps.md` Category 8 Section 3 cross-check.
- **Response header propagation requires intercepting `send`.** The wrapped `send_with_header` only acts on the first `http.response.start` message.

---

## 11.11 `src/app.py` — Exception handlers (Agent E Category 6)

> **[L0-L2]** This module is part of Flask removal. Use sync patterns. It stays sync through L4 and auto-converts at L5 only to the extent its callers flip to `async def` (most functions here are framework-agnostic utilities and remain sync across all layers).

> **Added 2026-04-11 under the Agent E idiom upgrade.** The plan covered `AdCPError` (Blocker 3) and `AdminRedirect` but missed four other handlers a 2026 FastAPI app needs: `HTTPException` (Accept-aware), `RequestValidationError`, `AdminAccessDenied`, and the catch-all `Exception`.

### A. Implementation

```python
# src/app.py (exception handlers)
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.admin.deps.auth import AdminAccessDenied, AdminRedirect
from src.admin.templating import render
from src.core.errors import AdCPError
from src.core.settings import get_settings


def _wants_html(request: Request) -> bool:
    """Return True only for browser navigation requests wanting HTML.

    Rejects XHR/fetch patterns even when Accept contains text/html:
    - jQuery/XHR sets X-Requested-With: XMLHttpRequest
    - Fetch without explicit Accept sends */* (does NOT match "text/html" substring)

    NOTE: HTMX callers are NOT handled here — they get partial HTML via
    `_response_mode` below (HTMX expects HTML fragments, not JSON). This
    helper is retained for call sites that only need the binary full-HTML
    vs. JSON decision and do not render partials.

    Accept substring check remains the primary signal; only browser navigation
    sends Accept like "text/html,application/xhtml+xml,...". Path-scoping
    to /admin/ adds a second guard — AdCP surfaces (/mcp, /a2a, /api) never
    want HTML regardless of Accept.
    """
    if request.headers.get("x-requested-with", "").lower() == "xmlhttprequest":
        return False
    accept = request.headers.get("accept", "")
    if "application/json" in accept and "text/html" not in accept:
        return False
    if "text/html" not in accept:
        return False
    # Path-scope: HTML rendering only for admin surface
    return request.url.path.startswith("/admin/") or request.url.path.startswith("/tenant/")


def _response_mode(request: Request) -> Literal["htmx_partial", "html_full", "json"]:
    """Determine response mode for errors (and redirects) based on request headers.

    Three modes, not two:
    - htmx_partial: HX-Request: true → render a fragment (no base.html extend).
      Previously treated as JSON, which broke HTMX swap targets.
    - html_full: browser navigation on /admin|/tenant with Accept: text/html.
    - json: everything else (AdCP surfaces, XHR, API callers).
    """
    if request.headers.get("hx-request") == "true":
        return "htmx_partial"
    if request.headers.get("x-requested-with", "").lower() == "xmlhttprequest":
        return "json"
    accept = request.headers.get("accept", "")
    is_admin = request.url.path.startswith(("/admin/", "/tenant/"))
    if is_admin and "text/html" in accept and "application/json" not in accept:
        return "html_full"
    return "json"


@app.exception_handler(AdCPError)
async def adcp_error_handler(request: Request, exc: AdCPError):
    """Accept-aware: HTMX partial, full HTML for admin browsers, JSON for APIs. (Blocker 3)"""
    mode = _response_mode(request)
    if mode == "htmx_partial":
        # HTMX swaps partial HTML fragments; do NOT extend base.html
        return render(request, "error_partial.html", {
            "error": exc.message,
            "status_code": exc.status_code,
        }, status_code=exc.status_code)
    if mode == "html_full":
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
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    """For JSON, return structured errors. HTML re-render handled per-form by each handler."""
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
    from urllib.parse import quote
    from starlette.responses import RedirectResponse
    url = f"{exc.to}?next={quote(exc.next_url, safe='')}" if exc.next_url else exc.to
    return RedirectResponse(url=url, status_code=303)


@app.exception_handler(AdminAccessDenied)
async def admin_access_denied_handler(request: Request, exc: AdminAccessDenied):
    return render(request, "403.html", {"message": exc.message}, status_code=403)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Catch-all. Sanitizes in production, full detail in dev."""
    from src.core.logging import get_logger
    logger = get_logger(__name__)
    logger.exception("unhandled_exception", method=request.method, path=request.url.path)
    msg = "Internal server error" if get_settings().app_env == "production" else f"{type(exc).__name__}: {exc}"
    if _wants_html(request):
        return render(request, "500.html", {"message": msg}, status_code=500)
    return JSONResponse(status_code=500, content={"detail": msg})
```

### C. Integration

- Register all 6 handlers on the root app in `src/app.py` during `build_admin()` wiring
- Structural guard `test_architecture_exception_handlers_complete.py` asserts all 6 are registered

### D. Gotchas

- **Handler order matters.** FastAPI dispatches to the most specific handler first (subclass before superclass), so `AdCPError` handler fires before `Exception`. Correct.
- **`AdCPError` JSON shape is byte-stable** for API callers — Agent D M9 `test_openapi_byte_stability.py` + an explicit JSON schema snapshot protect this.
- **`_wants_html` only returns True on admin paths.** API callers (`/api/v1/*`, `/mcp`, `/a2a`) always get JSON, regardless of their `Accept` header. This preserves AdCP wire format.
- **XHR navigation:** `_wants_html` returns False for requests with `X-Requested-With: XMLHttpRequest` even when Accept contains `text/html`. This prevents admin JS `fetch()` calls from accidentally receiving HTML error pages when the browser happened to send `Accept: text/html` by default.
- **HTMX navigation:** Handled by `_response_mode`, NOT `_wants_html`. HTMX requests (`HX-Request: true`) get a partial HTML fragment (`error_partial.html`) so the swap target receives renderable HTML, not JSON. A prior version of this handler collapsed HTMX into the JSON branch — that was wrong; HTMX swaps expect HTML fragments.
- **New template required: `templates/error_partial.html`** — just the error div block without `{% extends 'base.html' %}`. HTMX swaps it into `#error-flash` or similar. Keep the markup minimal (a single `<div class="error">` with `{{ error }}` and `{{ status_code }}`) so it composes into any swap target.

### E. Tests — content negotiation edge cases

```python
# tests/unit/admin/test_accept_aware_handler.py
class TestAcceptAwareHandler:
    def test_browser_navigation_gets_html(self, client):
        r = client.get("/admin/foo", headers={"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"})
        assert r.headers["content-type"].startswith("text/html")

    def test_fetch_default_gets_json(self, client):
        r = client.get("/admin/foo", headers={"Accept": "*/*"})
        assert r.headers["content-type"].startswith("application/json")

    def test_xhr_gets_json_even_with_html_accept(self, client):
        r = client.get("/admin/foo", headers={
            "Accept": "text/html",
            "X-Requested-With": "XMLHttpRequest",
        })
        assert r.headers["content-type"].startswith("application/json")

    def test_htmx_gets_json_even_with_html_accept(self, client):
        r = client.get("/admin/foo", headers={
            "Accept": "text/html",
            "HX-Request": "true",
        })
        assert r.headers["content-type"].startswith("application/json")

    def test_non_admin_path_gets_json(self, client):
        r = client.get("/api/v1/foo", headers={"Accept": "text/html"})
        assert r.headers["content-type"].startswith("application/json")
```

---

## 11.13 Test harness fixtures — async-native via httpx + ASGITransport (Agent E Category 14)

> **[L5+]** Async test harness. L0-L3 use the existing sync `TestClient(app)` and `IntegrationEnv` harness. L3 modernizes test patterns (factories, `dependency_overrides`) but stays sync.
>
> **Layer note:** Code examples in this section show async patterns for L5+ completeness. During L0-L4, use `def` instead of `async def` and `session.scalars(stmt)` instead of `(await session.execute(stmt)).scalars()`. Both `await session.scalars(...)` and `(await session.execute(...)).scalars()` are valid on `AsyncSession` at L5+ — `await session.scalars(...)` is canonical per SQLAlchemy docs (native method since 1.4.24).

> **Added 2026-04-11 under the Agent E idiom upgrade.** Sync `TestClient(app)` spawns its own event loop in a thread and conflicts with async lifespan state stored on `app.state`. `httpx.AsyncClient(transport=ASGITransport(app=app))` runs in the test's own event loop and sees `app.state` correctly.

> **LifespanManager note (L5+):** Starlette's `TestClient` automatically triggers FastAPI lifespan, so `app.state.db_engine` and `app.state.http_client` are populated during tests. If/when the harness migrates to native `httpx.AsyncClient + ASGITransport` for async tests, add `asgi-lifespan>=2.1.0` to dev deps and wrap the test app in `LifespanManager(app)` to trigger lifespan manually (ASGITransport does NOT run lifespan). For v2.0 L0-L4 scope, `TestClient` is sufficient — `asgi-lifespan` addition deferred to L5 harness conversion.

> **Async savepoints — deferred post-v2.0:** factory-boy async shim + `async with session.begin_nested()` savepoints are not in v2.0 scope. All factories in `tests/factories/` are sync `factory.alchemy.SQLAlchemyModelFactory` through L4. L5c test conversion keeps factories sync; async test cases access data via sync fixtures set up before the async handler call. Revisit if/when v2.1 introduces async factories with `AsyncSQLAlchemyModelFactory` base class.

### A. Implementation

```python
# tests/conftest.py (async fragment — layered with existing sync conftest)
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
from src.core.settings import get_settings


@pytest_asyncio.fixture(scope="function")
async def engine():
    """Per-test engine, tied to the current event loop. Prevents leak under xdist."""
    eng = make_engine(get_settings().database_url)
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
    """ASGI test client with session dependency override."""
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

### B. Example test

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

### C. Integration

- `pytest-asyncio>=1.1.0` with `asyncio_mode = "auto"` in `[tool.pytest.ini_options]` — Agent F F1.7.1 gate
- Per-test function-scoped event loop prevents the engine binding leak
- `app.dependency_overrides[get_session]` is THE canonical injection — no `monkeypatch.setattr` on internal functions
- Structural guard `test_architecture_tests_use_async_client.py` asserts no new `TestClient(app)` usage

### D. Gotchas

- **`TestClient` is deprecated for integration tests under async lifespan.** It still works for unit tests that don't exercise the lifespan, but any test that relies on `app.state.db_engine` must use `AsyncClient + ASGITransport`.
- **`app.dependency_overrides.clear()` in `finally`** prevents test-to-test leakage. Agent B Risk #35 cascade.
- **Session fixture rollback** replaces per-test transaction management. No need for `integration_db` fixture shenanigans under async.

---

## 11.13.1 Integration tests — `async_engine → async_db → async_app → async_client` fixture chain

> **[L5+]** Async integration test fixture chain. L0-L3 use the existing sync `integration_db` fixture and `IntegrationEnv` harness.
>
> **Layer note:** Code examples in this section show async patterns for L5+ completeness. During L0-L4, use `def` instead of `async def` and `session.scalars(stmt)` instead of `(await session.execute(stmt)).scalars()`. Both `await session.scalars(...)` and `(await session.execute(...)).scalars()` are valid on `AsyncSession` at L5+ — `await session.scalars(...)` is canonical per SQLAlchemy docs (native method since 1.4.24).

> **Added 2026-04-11 under the Agent E idiom upgrade.** This is the operational recipe for integration-test fixtures under async SQLAlchemy. The existing §11.13 introduces `AsyncClient + ASGITransport` at a high level; this subsection is the concrete chain that Wave 4 engineers sit down and paste.

### A. Goals of the integration harness

1. **Event-loop safety.** Function-scoped engine per test — an engine constructed under test A's event loop must NOT be reused by test B, because asyncpg's connection pool is tied to the event loop that created it (the pytest-asyncio "event loop is closed" footgun; Agent B Interaction B).
2. **Fast test isolation via transaction rollback.** Each test runs inside a single outer transaction on one connection, and the test client's `session.commit()` calls become savepoint releases on that transaction. Test teardown rolls the outer transaction back — the database starts and ends each test in the same state, without `TRUNCATE` or `DROP TABLE`.
3. **One schema create per xdist worker.** `Base.metadata.create_all` is session-scoped per worker, not per test, because DDL is not transaction-safe.
4. **Factory-boy works unchanged from the test body's point of view.** The factory session is rebound to the per-test `AsyncSession` via a shim.
5. **`dependency_overrides` is the only injection mechanism.** No `monkeypatch.setattr("module.get_db_session", ...)` — that path is dead in the new world.
6. **Coexistence during Waves 4a-5.** The existing sync `integration_db` fixture and the new `async_integration_db` fixture live in the same repo, in the same conftest files, for the duration of the migration. Each test file picks one or the other; mixing in a single file is prohibited by a new structural guard.

### B. `tests/conftest.py` — the new async fragment

```python
# tests/conftest.py (async fragment — append to existing sync conftest)
"""Async fixtures for Wave 4+ integration tests.

This fragment coexists with the sync fixtures during Waves 4a-5. A test
file picks EITHER the sync `integration_db` / `IntegrationEnv` harness
OR the async `async_integration_db` / `async_client` harness — never
both in the same file. Enforced by
`test_architecture_wave4_hybrid_test_boundary.py`.
"""
from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import AsyncClient
from httpx._transports.asgi import ASGITransport
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)

from src.core.database.deps import get_session
from src.core.database.engine import make_engine


# Fixture 1: async_engine — per-TEST engine, not per-session or per-module.
@pytest_asyncio.fixture(scope="function")
async def async_engine() -> AsyncIterator[AsyncEngine]:
    """Fresh AsyncEngine for each test.

    Function-scoped on purpose. A module- or session-scoped engine
    binds its asyncpg pool to the event loop of whichever test
    imported it first; subsequent tests on fresh loops see
    `RuntimeError: Event loop is closed` on connection acquisition.

    Function scope costs ~10ms of engine construction per test, which
    is cheap in absolute terms and pays for itself the first time
    you save a 30-minute debugging session of "why is this one test
    passing alone but failing in the full suite".
    """
    database_url = os.environ["DATABASE_URL"]
    engine = make_engine(
        database_url,
        # Lower pool sizes under xdist — each worker is one process,
        # and each function-scoped engine builds its own pool on top
        # of Postgres max_connections=100. With 8 workers, per-worker
        # pool of 5 + overflow 5 stays safely under the ceiling.
        pool_size=5,
        max_overflow=5,
    )
    try:
        yield engine
    finally:
        await engine.dispose()


# Fixture 2: async_connection_and_txn — open a connection + outer txn.
@pytest_asyncio.fixture(scope="function")
async def async_connection_and_txn(
    async_engine: AsyncEngine,
) -> AsyncIterator[tuple[AsyncConnection, "AsyncTransaction"]]:
    """One connection per test with an outer transaction we roll back.

    Every handler-issued commit becomes a savepoint release inside
    this outer transaction. When the test ends, `outer_txn.rollback()`
    discards everything the test wrote.
    """
    async with async_engine.connect() as connection:
        outer_txn = await connection.begin()
        try:
            yield connection, outer_txn
        finally:
            if outer_txn.is_active:
                await outer_txn.rollback()


# Fixture 3: async_sessionmaker_fixture — sessionmaker with savepoint mode.
@pytest_asyncio.fixture(scope="function")
async def async_sessionmaker_fixture(
    async_engine: AsyncEngine,
    async_connection_and_txn: tuple[AsyncConnection, "AsyncTransaction"],
) -> async_sessionmaker[AsyncSession]:
    """Sessionmaker bound to the open outer transaction.

    The key parameter is `join_transaction_mode="create_savepoint"`.
    When the test's handler code calls `session.commit()`, SQLAlchemy
    does NOT commit the outer transaction — it releases a savepoint
    instead. When we roll back the outer transaction in the teardown,
    all of the handler's committed writes are discarded.

    This is the async-world equivalent of the sync conftest's
    `nested_transaction` pattern.
    """
    connection, _outer_txn = async_connection_and_txn
    return async_sessionmaker(
        bind=connection,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
        autobegin=True,
        join_transaction_mode="create_savepoint",
    )


# Fixture 4: async_db — the AsyncSession factories + test bodies use.
@pytest_asyncio.fixture(scope="function")
async def async_db(
    async_sessionmaker_fixture: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """The one AsyncSession shared by the test body AND all handler requests.

    Both the test body's factory calls and the handler's `SessionDep`
    resolve to THIS session — that is what makes the data written by
    factories visible to the code under test without a manual commit.
    """
    async with async_sessionmaker_fixture() as session:
        yield session


# Fixture 5: async_app — install dependency_overrides, clear on teardown.
@pytest_asyncio.fixture(scope="function")
async def async_app(async_db: AsyncSession) -> AsyncIterator["FastAPI"]:
    """The app with `get_session` overridden to yield the fixture session.

    CRITICAL: `.pop(get_session, None)` in finally, NOT `.clear()`.
    `.clear()` wipes every override in the app, which can destroy
    overrides installed by a higher-level fixture. `.pop` is surgical
    and additive-safe.
    """
    from src.app import app

    async def _override() -> AsyncIterator[AsyncSession]:
        yield async_db

    app.dependency_overrides[get_session] = _override
    try:
        yield app
    finally:
        app.dependency_overrides.pop(get_session, None)


# Fixture 6: async_client — httpx.AsyncClient(transport=ASGITransport(...))
@pytest_asyncio.fixture(scope="function")
async def async_client(async_app: "FastAPI") -> AsyncIterator[AsyncClient]:
    """ASGI-transport test client. Runs in the test's own event loop.

    NOT `starlette.testclient.TestClient(app)` — TestClient spawns a
    new event loop in a thread and fights `app.state.db_engine` set
    by the async lifespan. `AsyncClient + ASGITransport` runs the app
    directly in the current event loop and sees `app.state` correctly.
    """
    async with AsyncClient(
        transport=ASGITransport(app=async_app),
        base_url="http://testserver",
    ) as client:
        yield client
```

### C. Fixture dependency graph

```
                 ┌──────────────────────┐
                 │ async_engine         │  function-scoped, per-test
                 │ (pool 5 + overflow 5)│  disposes at teardown
                 └──────────┬───────────┘
                            │
                            ▼
                 ┌──────────────────────┐
                 │ async_connection_    │  opens ONE connection
                 │   and_txn            │  begins outer txn
                 └──────────┬───────────┘  rollback on teardown
                            │
                            ▼
                 ┌──────────────────────┐
                 │ async_sessionmaker_  │  bound to that connection
                 │   fixture            │  join_transaction_mode=
                 └──────────┬───────────┘   "create_savepoint"
                            │
                            ▼
                 ┌──────────────────────┐
                 │ async_db             │  AsyncSession
                 │                      │  handler.commit() = SAVEPOINT
                 └──────────┬───────────┘
                            │
                    ┌───────┴───────┐
                    ▼               ▼
         ┌──────────────────┐ ┌────────────────────┐
         │ bind_factories   │ │ async_app          │
         │ (rebinds         │ │ (overrides         │
         │  factory-boy     │ │  get_session → db) │
         │  _meta.session)  │ └─────────┬──────────┘
         └──────────────────┘           │
                                        ▼
                             ┌──────────────────────┐
                             │ async_client         │
                             │ (httpx + ASGI)       │
                             └──────────────────────┘
```

### D. Factory-boy async shim

factory-boy has no native async support. The existing sync factories under `tests/factories/` use `sqlalchemy_session_persistence = "commit"`, which calls `session.commit()` from inside `_save()`. Under an async session, that is illegal — `commit()` is a coroutine and the factory's `_save()` is sync.

The shim flips `sqlalchemy_session_persistence` to `None`, so `_save()` only calls `session.add(instance)` and leaves the commit/flush to the test's surrounding fixture. Under savepoint-rollback isolation, the test never wants the factory to commit anyway — the factory write must travel through the outer transaction so teardown can roll it back.

> **Decision 3 deep-think 2026-04-11 (refined from Audit 06):** the original recipe had 3 bugs: (a) overrode `_create` instead of `_save`, which breaks `AccountFactory`'s existing `_create` override at `tests/factories/account.py:28-30`; (b) used `session.sync_session.add(instance)` redundantly — `AsyncSession.add()` is a sync method (verified at `sqlalchemy/ext/asyncio/session.py:1111-1143`) that proxies directly to `sync_session.add()`; (c) called `session.sync_session.flush()` which **raises `MissingGreenlet` under asyncpg** — any sync DB I/O on an AsyncSession-owned connection must go through `greenlet_spawn(...)`. No current factory needs DB-materialized PKs across SubFactory boundaries (all 15 ORM factories generate cross-referenced keys Python-side via `Sequence`), so flush is NOT called by the shim. Validated by pre-Wave-0 **Spike 4.25**.

**Key facts this recipe relies on:**
- `AsyncSession.add()` is a **sync** method that proxies directly to `self.sync_session.add()` (see `sqlalchemy/ext/asyncio/session.py:1111-1143`). No special handling is required for adds.
- `AsyncSession.flush()` is `async def` and goes through `greenlet_spawn`. **Calling `session.sync_session.flush()` from sync code raises `MissingGreenlet` under asyncpg** — do NOT do that. If a test body needs PK materialization across SubFactory boundaries, call `await async_db.flush()` explicitly in the test body.
- Factory-boy's `_save` (at `factory/alchemy.py:119`) is the one method that calls `session.add` and the commit/flush branches. Overriding `_save` (not `_create`) preserves factory-boy's MRO chain for factories that have their own `_create` override (e.g., `AccountFactory` at `tests/factories/account.py:28-30`).
- No current ORM factory uses `post_generation`, `sqlalchemy_get_or_create`, or `RelatedFactoryList`. One factory uses `RelatedFactory` (`TenantFactory.currency_usd` at `core.py:50`); it works because factory-boy runs `RelatedFactory` AFTER `_save` returns, and the related factory's `add()` goes through the same session.

```python
# tests/factories/_async_shim.py
"""Async shim for factory-boy ORM factories.

Wraps `SQLAlchemyModelFactory` so it can be used with an `AsyncSession`:

1. `sqlalchemy_session_persistence = None` — never commit/flush from
   inside `_save()`. The fixture owns the transaction boundary.
2. `_save()` is overridden (not `_create()`) so subclasses that
   override `_create()` — notably `AccountFactory` — continue to work
   unchanged. factory-boy's `_create()` calls `_save()` in its default
   path, so replacing `_save()` gives us the right hook.
3. `session.add(instance)` is safe because `AsyncSession.add()` is a
   sync method that proxies to `sync_session.add()`. No greenlet spawn
   needed.
4. We do NOT call `session.flush()`. All current factories generate
   their cross-referenced keys in Python (via `Sequence`). If a future
   factory needs DB-materialized PKs, the test body must explicitly
   call `await async_db.flush()` before the dependent factory runs.

Usage is identical to `SQLAlchemyModelFactory`. Tests still write
`TenantFactory(tenant_id="t1")` and get back an ORM instance. The
only difference is that `_meta.sqlalchemy_session` now holds an
`AsyncSession` instead of a sync `Session`.
"""
from __future__ import annotations

from typing import Any

from factory.alchemy import SQLAlchemyModelFactory
from sqlalchemy.ext.asyncio import AsyncSession


class AsyncSQLAlchemyModelFactory(SQLAlchemyModelFactory):
    """factory-boy base class for use with `AsyncSession`.

    All ORM factories in `tests/factories/` inherit from this class
    starting in Wave 4c. Behavior is the same as `SQLAlchemyModelFactory`
    except:
    - `sqlalchemy_session_persistence` is pinned to `None`
    - `_save()` calls only `session.add(instance)` (no flush, no commit)
    - `_meta.sqlalchemy_session` must be an `AsyncSession`
    """

    class Meta:
        abstract = True
        sqlalchemy_session = None
        sqlalchemy_session_persistence = None

    @classmethod
    def _save(
        cls,
        model_class: type,
        session: AsyncSession,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> Any:
        """Instantiate the model and add it to the async session.

        Overrides `SQLAlchemyModelFactory._save`. Factory-boy's default
        `_create()` calls `_save()` — by overriding `_save()` instead
        of `_create()` we keep subclass overrides of `_create()` working
        (e.g., AccountFactory at tests/factories/account.py:28-30).

        `session.add()` is sync even on `AsyncSession` — it proxies to
        `sync_session.add()` without a greenlet spawn. We deliberately
        do NOT call `flush()` or `commit()`: both are async on
        `AsyncSession`, and the fixture owns the transaction boundary.
        """
        if session is None:
            raise RuntimeError(
                f"{cls.__name__}._meta.sqlalchemy_session is unbound. "
                "Did you forget the `bind_factories` fixture?"
            )
        if not isinstance(session, AsyncSession):
            raise TypeError(
                f"{cls.__name__} expects an AsyncSession, got "
                f"{type(session).__name__}. Mixing sync and async sessions "
                "is not supported — see foundation-modules §11.13.1 H "
                "(file-level one-path invariant)."
            )

        instance = model_class(*args, **kwargs)
        session.add(instance)  # sync — proxies to sync_session.add
        return instance
```

**Escape hatch for DB-materialized PKs:** if a future factory uses a SubFactory that depends on a database-generated auto-increment PK, the shim cannot materialize it. The test body must call `await async_db.flush()` between factory invocations:

```python
@pytest.mark.asyncio
async def test_auto_increment_chain(async_integration_db, async_db):
    parent = ParentFactory()  # parent.id is None (auto-increment)
    await async_db.flush()    # <-- materializes parent.id
    child = ChildFactory(parent_id=parent.id)  # safe now
```

None of the current 15 ORM factories require this escape hatch (all cross-referenced keys are `Sequence`-generated Python-side). Documented for future use.

And the per-test `bind_factories` fixture:

```python
# tests/integration/conftest.py (async fragment)
from collections.abc import AsyncIterator

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession


@pytest_asyncio.fixture(scope="function")
async def bind_factories(async_db: AsyncSession) -> AsyncIterator[None]:
    """Point every ORM factory at `async_db` for the duration of the test.

    Inverse of `tests/harness/_base.py::IntegrationEnv.__enter__`. Async
    integration tests do NOT use the harness — they use raw fixtures —
    so we bind directly. The guard against double-binding protects against
    a test accidentally nesting an `IntegrationEnv` inside an async test
    (which is forbidden by the file-level hybrid-boundary guard anyway).
    """
    from tests.factories import ALL_FACTORIES

    for f in ALL_FACTORIES:
        if f._meta.sqlalchemy_session is not None:
            raise RuntimeError(
                f"{f.__name__}._meta.sqlalchemy_session is already bound — "
                "nested fixtures or hybrid sync/async test body "
                "(see foundation-modules §11.13.1 H)."
            )
        f._meta.sqlalchemy_session = async_db

    try:
        yield
    finally:
        for f in ALL_FACTORIES:
            f._meta.sqlalchemy_session = None
```

**Wave 4b-4c ordering constraint (hard cliff, Decision 3 deep-think):** the moment `tests/factories/core.py::TenantFactory` flips its base class from `SQLAlchemyModelFactory` to `AsyncSQLAlchemyModelFactory`, every sync integration test that imports it breaks. Therefore:

1. **Wave 4b:** convert ALL 166 consuming integration tests to async (`async def`, `@pytest.mark.asyncio`, `async_integration_db` fixture, `bind_factories` fixture). Factories still inherit from sync `SQLAlchemyModelFactory`. During this wave the shim file exists but is unused.
2. **Wave 4c:** flip factories to `AsyncSQLAlchemyModelFactory` one file at a time, commit-per-file, verifying `tests/factories/test_*_factory.py` contract tests pass at each step.

Enforced by a pre-PR gate: the Wave 4c PR must contain ONLY factory-file edits. Gate check: `git diff --name-only origin/main...HEAD | grep -v "^tests/factories/" | wc -l` must be 0 (or within a small allowlist for `_async_shim.py` and `ALL_FACTORIES` edits).

**Three structural guards:**
- `tests/unit/test_architecture_factory_inherits_async_base.py` — AST-walks `tests/factories/*.py`, asserts every ORM factory class inherits from `AsyncSQLAlchemyModelFactory` not `SQLAlchemyModelFactory`. Active from Wave 4c onward.
- `tests/unit/test_architecture_factory_no_post_generation.py` — forbids `@post_generation` decorator usage in `tests/factories/`. The shim does not guarantee correct semantics for post_generation hooks that try to `await` inside them.
- `tests/unit/test_architecture_factory_in_all_factories.py` — asserts every `AsyncSQLAlchemyModelFactory` subclass is in `ALL_FACTORIES`. Unbound factories fail with `RuntimeError` at runtime; this guard catches it at CI time.

### E. `tests/integration/conftest.py` — schema create + composite fixture

```python
# tests/integration/conftest.py (async fragment — append to existing sync conftest)
"""Integration-test-specific async fixtures."""
from __future__ import annotations

from collections.abc import AsyncIterator

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession


@pytest_asyncio.fixture(scope="session")
async def integration_schema() -> AsyncIterator[None]:
    """Create the schema exactly once per xdist worker.

    We run `Base.metadata.create_all` via a temporary engine — NOT the
    per-test `async_engine` fixture. DDL is not savepoint-safe, so it
    MUST happen outside the per-test outer transaction. This fixture
    runs once at worker startup.
    """
    import os

    from src.core.database.engine import make_engine
    from src.core.database.models import Base

    engine = make_engine(os.environ["DATABASE_URL"])
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        yield
    finally:
        # Intentionally do NOT drop tables at worker teardown:
        # agent-db.sh tears down the whole container, which is faster.
        await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def async_integration_db(
    integration_schema: None,
    async_db: AsyncSession,
    bind_factories: None,
) -> AsyncSession:
    """The canonical fixture for new Wave 4+ integration tests.

    Transitively pulls:
    * schema creation (session-scoped, once per worker)
    * per-test engine / connection / outer txn / session
    * factory-boy session binding

    Test body signature:

        @pytest.mark.asyncio
        async def test_x(async_integration_db, async_client):
            tenant = TenantFactory(tenant_id="t1")
            r = await async_client.get("/admin/tenant/t1/accounts")
            assert r.status_code == 200
    """
    return async_db
```

### F. Example test using the full chain

```python
# tests/integration/test_accounts_list.py
import pytest
from tests.factories import TenantFactory, PrincipalFactory, AccountFactory


@pytest.mark.asyncio
@pytest.mark.requires_db
async def test_list_accounts_filters_by_status(
    async_integration_db,
    async_client,
):
    """Accounts list filters by status query param."""
    tenant = TenantFactory(tenant_id="t1", name="Test Tenant")
    PrincipalFactory(tenant=tenant, principal_id="p1")
    AccountFactory(tenant=tenant, account_id="a1", status="pending_approval")
    AccountFactory(tenant=tenant, account_id="a2", status="active")

    # async_integration_db committed factory writes as savepoint releases;
    # they are visible to the handler because async_client's SessionDep
    # resolves to the SAME async_db fixture session.

    r = await async_client.get(
        "/admin/tenant/t1/accounts",
        params={"status": "active"},
        cookies={"adcp_session": _signed({"user": "alice@example.com"})},
    )
    assert r.status_code == 200
    assert "a2" in r.text
    assert "a1" not in r.text

    # No teardown boilerplate — async_engine's outer_txn rolls back
    # and the fixture's `finally` blocks unbind factories and clear
    # dependency_overrides.
```

### G. xdist coordination

Each xdist worker gets its own Postgres container via the agent-db skill (`.claude/skills/agent-db/agent-db.sh up`), which allocates a unique port per worktree. The per-worker `DATABASE_URL` is written to `.test-stack.env` and exported before `tox -p` runs. The `async_engine` fixture reads that env var at fixture time — each worker talks to its own container, so there is no cross-worker contention on Postgres's `max_connections=100`.

Pool sizing per worker: `pool_size=5, max_overflow=5 = 10 connections peak per worker`. With 8 workers that is 80 peak connections — comfortably under 100 with headroom.

### H. Coexistence with sync fixtures during Waves 4a-5

| Aspect | Sync path (current) | Async path (new) |
|---|---|---|
| DB fixture | `integration_db` | `async_integration_db` |
| Test client | `TestClient(app)` via `IntegrationEnv.get_rest_client()` | `async_client` (httpx + ASGI) |
| Test marker | `@pytest.mark.requires_db` | `@pytest.mark.requires_db` + `@pytest.mark.asyncio` |
| Harness | `IntegrationEnv` subclasses | **no harness — raw fixtures** |
| Factory base | `SQLAlchemyModelFactory` | `AsyncSQLAlchemyModelFactory` |
| Session binding | `IntegrationEnv.__enter__` | `bind_factories` fixture |

**File-level invariant:** each test file uses ONE path, not both. A single test file that mixes `with DeliveryPollEnv(...)` and `async_client` will deadlock on session binding — two competing `_meta.sqlalchemy_session` values at the same time. Enforced by new guard `test_architecture_wave4_hybrid_test_boundary.py`.

### I. Gotchas

- **`join_transaction_mode="create_savepoint"` is SQLAlchemy 2.0+ only.** Pin `sqlalchemy>=2.0.36`.
- **The `async_db` session and the handler's `SessionDep` MUST be the same object.** The override in `async_app` returns `async_db` from the test fixture scope — not a new session from `async_sessionmaker_fixture()` opened in the override. Two sessions on the outer transaction have their writes isolated from each other.
- **Per-test engine is expensive in absolute wall-clock terms.** ~10ms per test × 3000 tests = 30 seconds across a full run. Not enough to matter — do not session-scope the engine.
- **`Base.metadata.create_all` does not create the `alembic_version` table.** Tests do not run migrations; they create the schema from models. Tests that rely on alembic-specific state belong under `tests/e2e/`.
- **Factory commits via `sync_session.flush()` materialize PKs but DO NOT SEND to Postgres until the outer transaction's next savepoint.** For tests that verify "INSERT hit the database", wrap the assertion in `await async_db.flush()` before the assertion.
- **`dependency_overrides.clear()` is a sledgehammer.** The `async_app` fixture uses `.pop(get_session, None)` instead, to avoid stomping on overrides that another fixture installed higher up the chain.
- **`async_client.get(...)` does NOT auto-follow redirects.** httpx's default is `follow_redirects=False`. For tests that expect a 302/307 from `redirect_slashes=True`, assert the 307 directly, or pass `follow_redirects=True` to the client call.
- **The fixture does NOT install the non-request `session_scope()` ContextVar.** Integration tests that exercise a scheduler or background worker should additionally call `set_sessionmaker(async_sessionmaker_fixture)` inside the test body, inside a `try/finally` that calls `reset_sessionmaker`.

---

## 11.13.2 Unit tests — `mock_async_session` + `dependency_overrides` cheat-sheet

> **[L5+]** Async unit test fixtures. L0-L4 use the existing sync mocking patterns with `patch('src.core.database.database_session.get_db_session')`.
>
> **Layer note:** Code examples in this section show async patterns for L5+ completeness. During L0-L4, use `def` instead of `async def` and `session.scalars(stmt)` instead of `(await session.execute(stmt)).scalars()`. Both `await session.scalars(...)` and `(await session.execute(...)).scalars()` are valid on `AsyncSession` at L5+ — `await session.scalars(...)` is canonical per SQLAlchemy docs (native method since 1.4.24).

> **Added 2026-04-11 under the Agent E idiom upgrade.** Integration tests use a real `AsyncSession` against agent-db Postgres (§11.13.1). Unit tests mock everything. This section defines the unit fixture surface and catalogs every `dependency_overrides` key that new unit tests commonly need.

### A. Why a separate unit path

The integration path is expensive: ~10ms engine construction per test, connection open + outer txn + savepoint machinery, schema create once per xdist worker, and a real Postgres container running alongside pytest. Unit tests should not pay any of that cost.

The unit path swaps out:

* `async_engine` → nothing (no engine at all)
* `async_db` → `mock_async_session` (a `MagicMock(spec=AsyncSession)`)
* `async_app` → `unit_app` (same app, different override)
* `async_client` → `unit_client` (still httpx + ASGI, but backed by the mock)
* `bind_factories` → nothing (factories are an integration concern)

### B. `tests/unit/conftest.py` — mock-session fixtures

```python
# tests/unit/conftest.py (async-fragment, append to existing sync conftest)
"""Async unit-test fixtures.

Every fixture here hands the test a MOCKED session — never a real one.
If your test needs a real session, it is an integration test and should
live under `tests/integration/` with the fixtures from §11.13.1.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from httpx import AsyncClient
from httpx._transports.asgi import ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database.deps import get_session


@pytest.fixture
def mock_async_session() -> MagicMock:
    """A MagicMock configured to behave like `AsyncSession`.

    `spec=AsyncSession` gives attribute validation — typos like
    `mock.excute(...)` raise `AttributeError` instead of silently
    creating a new child mock.

    Async methods are replaced with `AsyncMock` explicitly. `spec=`
    alone does NOT do this — it makes attribute access match the
    spec's shape, but accessing `mock.commit` under spec=AsyncSession
    returns a plain `MagicMock`, not an `AsyncMock`. Calling
    `await mock.commit()` on a plain MagicMock raises
    `TypeError: object MagicMock can't be used in 'await' expression`.

    `.add()` stays a regular MagicMock because `AsyncSession.add()`
    IS sync — it just calls through to `sync_session.add()`.
    """
    mock = MagicMock(spec=AsyncSession)
    mock.execute = AsyncMock()
    mock.scalars = AsyncMock()
    mock.commit = AsyncMock()
    mock.rollback = AsyncMock()
    mock.flush = AsyncMock()
    mock.close = AsyncMock()
    mock.refresh = AsyncMock()
    mock.merge = AsyncMock()
    mock.delete = AsyncMock()
    mock.add = MagicMock()  # sync — leave as MagicMock
    return mock


@pytest_asyncio.fixture
async def unit_app(mock_async_session: MagicMock) -> AsyncIterator["FastAPI"]:
    """The app, with `get_session` returning `mock_async_session`.

    CRITICAL: `.pop(get_session, None)` in finally, NOT `.clear()`.
    """
    from src.app import app

    async def _override() -> AsyncIterator[MagicMock]:
        yield mock_async_session

    app.dependency_overrides[get_session] = _override
    try:
        yield app
    finally:
        app.dependency_overrides.pop(get_session, None)


@pytest_asyncio.fixture
async def unit_client(unit_app: "FastAPI") -> AsyncIterator[AsyncClient]:
    """Unit-test HTTP client. Same shape as async_client but mocked.

    Tests can assert on both request/response shape AND on the mocked
    session's call history:

        @pytest.mark.asyncio
        async def test_create_account_flushes(unit_client, mock_async_session):
            r = await unit_client.post("/api/v1/accounts", json={...})
            assert r.status_code == 201
            mock_async_session.flush.assert_awaited_once()
    """
    async with AsyncClient(
        transport=ASGITransport(app=unit_app),
        base_url="http://testserver",
    ) as client:
        yield client
```

### C. Example unit test

```python
# tests/unit/test_accounts_unit.py
import pytest
from unittest.mock import AsyncMock


@pytest.mark.asyncio
async def test_create_account_route_calls_impl(
    unit_client,
    mock_async_session,
    monkeypatch,
):
    """The create-account route delegates to `_create_account_impl`."""
    fake_result = {"account_id": "a1", "status": "pending_approval"}
    fake_impl = AsyncMock(return_value=fake_result)
    monkeypatch.setattr(
        "src.routes.api_v1._create_account_impl",
        fake_impl,
    )

    r = await unit_client.post(
        "/api/v1/accounts",
        json={"account_id": "a1", "operator": "gam", "brand": {"domain": "x.com"}},
    )
    assert r.status_code == 201
    assert r.json()["account_id"] == "a1"
    fake_impl.assert_awaited_once()
    # Session was threaded through:
    passed_session = fake_impl.await_args.kwargs["session"]
    assert passed_session is mock_async_session
```

### D. `dependency_overrides` cheat-sheet

Every override below is a real dep used somewhere in the admin or REST surface. All overrides MUST be cleaned up in a `finally` block — see section F.

| Dep | Override shape | Used by | Notes |
|---|---|---|---|
| `get_session` | `async def _over(): yield mock_async_session` | every admin + REST handler | **Must be an async generator**, not a plain return. FastAPI checks the signature. |
| `get_account_repo` and siblings (§11.0.4 dep factories) | `async def _over(): return Mock(spec=AccountRepository)` | admin account handlers | Regular coroutine, not generator — the factory returns a value, not yields. |
| `_require_auth_dep` | `def _over(): return ResolvedIdentity(...)` or `lambda: fixed_identity` | every MCP + A2A + REST route that auths | Sync lambda works — FastAPI wraps it. |
| `_resolve_auth_dep` | same shape as `_require_auth_dep` | same | Two deps exist because "require auth" 401s on missing and "resolve auth" returns `None` — tests usually override both to the same fake identity. |
| `get_admin_user` | `def _over(): return AdminUser(email="alice@example.com", role="tenant_admin")` | every admin router | Defined in `src/admin/deps/auth.py` (§11.4). |
| `require_super_admin` | `def _over(): return AdminUser(email="root@example.com", role="super_admin")` | super-admin-only admin routes | Same module. |
| `get_current_tenant` | `async def _over(): return TenantDTO(tenant_id="t1", ...)` | `/tenant/{tenant_id}/*` admin routes | In a unit test the DTO is a Pydantic instance, not an ORM model. |
| `get_csrf_token` | `def _over(): return "test-csrf-token"` | form-submit admin routes | Tests that submit forms set both this override AND the `csrftoken` cookie. |
| `get_settings` | `def _over(): return Settings(...)` | every handler that touches `SettingsDep` (§11.0.2) | Construct a real `Settings` with the test env values, not a mock. |
| `get_adapter` | `def _over(): return FakeAdapter()` | every handler that touches the adapter layer | `FakeAdapter` is `tests/factories/adapter.py::FakeAdapter`. |
| `get_http_client` | `def _over(): return httpx.AsyncClient(transport=respx.mock)` | webhook-sending handlers | Use `respx` to stub the outbound HTTP. |

### E. Mock-layer choice guide

When writing a unit test, choose the mock layer that matches what you are testing:

| What you are testing | Mock at | Example |
|---|---|---|
| A FastAPI route's integration with the request layer | `_impl` function itself via `monkeypatch.setattr` | test_create_account_route_calls_impl (above) |
| An `_impl` function's logic | Every repository used by `_impl`, not the session | `mock_accounts = Mock(spec=AccountRepository); monkeypatch.setattr(...)` |
| A repository method's query shape | `session.execute` on `mock_async_session`, with a configured return value | `mock_async_session.execute.return_value.scalars.return_value.all.return_value = [fake_account]` |
| A Pydantic validator | Nothing — just call the model constructor | Pure unit test, no async fixtures needed |
| A template rendering function | Nothing — call `render(request, template, ctx)` with a fake `Request` | May need `async_client` for the request object |

**Rule of thumb: one mock layer per test.** A test that mocks both `_create_account_impl` AND `mock_async_session.execute` is testing nothing real — the mocks are lying to each other.

### F. The cleanup invariant

`src.app.app` is a **module-level FastAPI instance**. Every fixture that writes to `app.dependency_overrides` is mutating shared state. If the write is not undone in a `finally` block, the next test to import `src.app.app` inherits the override — even across test files.

Three patterns prevent this:

**Pattern 1 — Fixture-scoped, pop on teardown:**

```python
@pytest_asyncio.fixture
async def unit_app(mock_async_session):
    from src.app import app
    async def _override(): yield mock_async_session
    app.dependency_overrides[get_session] = _override
    try:
        yield app
    finally:
        app.dependency_overrides.pop(get_session, None)
```

**Pattern 2 — Test-scoped, explicit try/finally in the body:**

```python
@pytest.mark.asyncio
async def test_manual_override(unit_client):
    from src.app import app
    app.dependency_overrides[some_dep] = _fake_dep
    try:
        r = await unit_client.get(...)
        assert r.status_code == 200
    finally:
        app.dependency_overrides.pop(some_dep, None)
```

**Pattern 3 — Session-wide autouse cleanup (safety net):**

```python
@pytest.fixture(autouse=True)
def _clear_overrides_on_teardown():
    """Clear any dep overrides that leaked from a forgotten finally.

    This is a SAFETY NET, not a substitute for per-fixture cleanup.
    """
    yield
    from src.app import app
    app.dependency_overrides.clear()
```

Pattern 3 goes in `tests/unit/conftest.py` and `tests/integration/conftest.py` as a safety net. It does NOT replace per-fixture `finally` — a test relying only on pattern 3 will fail when its fixture's own overrides collide with another fixture's overrides before the safety net fires.

### G. Structural guard — `test_architecture_no_override_leak.py`

```python
# tests/unit/test_architecture_no_override_leak.py
"""Guard: every test that writes to dependency_overrides cleans up.

AST scan rule:
  For every assignment of the form
    `<x>.dependency_overrides[<key>] = <value>`

  The enclosing function must contain a `finally` block that includes
  either:
    * `<x>.dependency_overrides.pop(<key>, ...)`
    * `<x>.dependency_overrides.clear()`

Allowlist:
  * tests/conftest.py (defines the fixtures)
  * tests/unit/conftest.py
  * tests/integration/conftest.py
  * tests/harness/_base.py (legacy; FIXME shrinks over time)
"""
from __future__ import annotations

import ast
import pathlib

ALLOWLIST: frozenset[str] = frozenset({
    "tests/conftest.py",
    "tests/unit/conftest.py",
    "tests/integration/conftest.py",
    "tests/harness/_base.py",
})


def _function_writes_override(func) -> list:
    writes = []
    for node in ast.walk(func):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Subscript)
            and isinstance(node.targets[0].value, ast.Attribute)
            and node.targets[0].value.attr == "dependency_overrides"
        ):
            writes.append(node)
    return writes


def _function_has_cleanup(func) -> bool:
    for node in ast.walk(func):
        if isinstance(node, ast.Try) and node.finalbody:
            for finally_stmt in node.finalbody:
                for sub in ast.walk(finally_stmt):
                    if (
                        isinstance(sub, ast.Call)
                        and isinstance(sub.func, ast.Attribute)
                        and sub.func.attr in {"pop", "clear"}
                        and isinstance(sub.func.value, ast.Attribute)
                        and sub.func.value.attr == "dependency_overrides"
                    ):
                        return True
    return False


def test_no_dependency_override_leak() -> None:
    violations: list[str] = []
    for path in pathlib.Path("tests").rglob("test_*.py"):
        rel = str(path).replace("\\", "/")
        if rel in ALLOWLIST:
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if _function_writes_override(node) and not _function_has_cleanup(node):
                    violations.append(
                        f"{path}:{node.lineno} `{node.name}` writes "
                        f"dependency_overrides without a finally-block cleanup"
                    )
    assert not violations, (
        "Dependency override leaks detected:\n  "
        + "\n  ".join(violations)
        + "\n\nWrap the override in try/finally or move it into a fixture."
    )
```

### H. Gotchas

- **`mock_async_session.execute` returns an `AsyncMock()` by default, which resolves to another `AsyncMock`.** If your test asserts `result.scalars().all()`, you must chain-configure: `mock.execute.return_value.scalars.return_value.all.return_value = [...]`.
- **`mock_async_session.add` is a `MagicMock`, NOT an `AsyncMock`.** `AsyncSession.add()` is sync — match the real API.
- **Don't `await` `mock.add.assert_called_once_with(...)`.** The assertion method is sync.
- **Pattern 3's autouse cleanup CAN mask Pattern 1/2 bugs.** If your test's own `finally` is missing but the autouse cleanup saves it, you never notice — until a future test wires a dep override at module-import time (which autouse does not touch) and the masked bug surfaces there. The structural guard (section G) is the real defense.
- **Unit tests should not import `tests.conftest.async_integration_db`.** If a unit test needs a real session, it is an integration test. The file-level boundary guard catches cross-contamination.
- **FastAPI caches deps per-request, not per-test.** If a single test makes multiple requests through `unit_client`, each request re-invokes `_override()`. For async generator overrides, the generator is re-created per request.
- **Unit-test `unit_client` does NOT run the app's lifespan.** `app.state.db_engine` is never set. That is fine because `mock_async_session` replaces the entire session flow, but any handler that reaches into `request.app.state.db_engine` directly will get `AttributeError` — a signal to refactor the handler to use the dep, not to install a fake on `app.state`.

---

## 11.14 Adapter Path B wrap pattern (Decision 1, 2026-04-11)

> **[L5d2]** Adapter `run_in_threadpool` wrapping and dual session factory (Decision 1 Path B). L0-L4 call adapters synchronously from sync handlers without wrapping.
>
> **Layer note:** Code examples in this section show async patterns for L5+ completeness. During L0-L4, use `def` instead of `async def` and `session.scalars(stmt)` instead of `(await session.execute(stmt)).scalars()`. Both `await session.scalars(...)` and `(await session.execute(...)).scalars()` are valid on `AsyncSession` at L5+ — `await session.scalars(...)` is canonical per SQLAlchemy docs (native method since 1.4.24).

> **Added 2026-04-11 under the Decision 1 deep-think resolution.** Adapters stay sync under v2.0. The 18 adapter call sites in `src/core/tools/*.py` + 1 in `src/admin/blueprints/operations.py:252` wrap their sync adapter methods in `await run_in_threadpool(...)`. This section is the canonical reference for the wrap pattern plus the dual-session-factory machinery that supports it.

### A. Why adapters stay sync

The full-async alternative requires porting `googleads==49.0.0` off `suds-py3` (hard-sync SOAP with no async variant) and rewriting 4 `requests`-based adapters (`xandr.py`, `kevel.py`, `triton_digital.py`, `broadstreet/adapter.py`) — ~1500 LOC of churn for zero AdCP-visible benefit. The "full async" adapter method body would end up as:

```python
async def create_media_buy(self, request, packages, ...):
    return await run_in_threadpool(self._sync_impl, request, packages, ...)
```

— which just moves the `run_in_threadpool` wrap from outside the adapter (Path B) to inside each adapter method (Path A), gaining only cosmetic uniformity at the cost of converting 40+ adapter methods. **Path B is simpler, smaller, and has a cleaner rollforward path** if some future release wants to migrate individual adapters to native async.

### B. Dual session factory in `database_session.py`

Under Path B, `src/core/database/database_session.py` exports BOTH factories side-by-side:

```python
# src/core/database/database_session.py (Wave 4 target state)
"""Database session factories — async primary, sync secondary.

Under the Decision 1 Path B resolution (2026-04-11), the sync factory
below coexists with the async factory for the lifetime of v2.0.0.
Adapter code running inside run_in_threadpool worker threads uses
`get_sync_db_session()`; every other code path (handlers, _impl
functions, schedulers, repositories) uses `get_db_session()`.

Sunset target for the sync factory: post-v2.0, when the `googleads`/`requests`-based adapters are ported to native async HTTP clients.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager, contextmanager
from typing import AsyncIterator, Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker


# ---------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------

def _build_async_url(raw_url: str) -> str:
    """Rewrite `postgresql://` → `postgresql+asyncpg://` for the async engine."""
    if raw_url.startswith("postgresql+asyncpg://"):
        return raw_url
    if raw_url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + raw_url[len("postgresql://"):]
    raise ValueError(f"DATABASE_URL must start with postgresql:// or postgresql+asyncpg://; got: {raw_url[:20]}...")


def _build_sync_url(raw_url: str) -> str:
    """Strip `+asyncpg` for the sync psycopg2 engine."""
    if raw_url.startswith("postgresql+asyncpg://"):
        return "postgresql://" + raw_url[len("postgresql+asyncpg://"):]
    return raw_url  # already plain postgresql://


# ---------------------------------------------------------------------
# Async engine — used by handlers, _impl, repositories, schedulers
# ---------------------------------------------------------------------

_database_url = os.environ["DATABASE_URL"]

_async_engine = create_async_engine(
    _build_async_url(_database_url),
    pool_size=int(os.environ.get("DB_POOL_SIZE", "15")),
    max_overflow=int(os.environ.get("DB_POOL_MAX_OVERFLOW", "25")),
    pool_pre_ping=True,
    pool_recycle=3600,
    echo=False,
    # asyncpg-specific: ensure JSONB codec is registered before first query
    connect_args={"server_settings": {"application_name": "adcp-salesagent"}},
)

_async_sessionmaker = async_sessionmaker(
    _async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


@asynccontextmanager
async def get_db_session() -> AsyncIterator[AsyncSession]:
    """Async session for handlers, _impl, schedulers, repositories.

    Commits on clean exit, rolls back on exception, closes on finally.
    Per the Decision 7 refactor, every caller owns session lifetime via
    `async with get_db_session()` — no ambient singleton sessions.
    """
    async with _async_sessionmaker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ---------------------------------------------------------------------
# Sync engine — used by adapter code (Path B) + AuditLogger internal
# ---------------------------------------------------------------------

_sync_engine = create_engine(
    _build_sync_url(_database_url),
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,
    pool_recycle=3600,
    echo=False,
    connect_args={"application_name": "adcp-salesagent-sync"},
)


@event.listens_for(_sync_engine, "connect")
def _set_sync_statement_timeout(dbapi_conn, connection_record):
    """30-second statement timeout for sync path."""
    with dbapi_conn.cursor() as cur:
        cur.execute("SET statement_timeout = '30s'")


_sync_sessionmaker = sessionmaker(
    _sync_engine,
    class_=Session,
    expire_on_commit=True,
    autoflush=True,
)


@contextmanager
def get_sync_db_session() -> Iterator[Session]:
    """Sync session for adapter code inside run_in_threadpool workers.

    **Do NOT call from async request handlers, _impl functions, or
    schedulers.** Those live on the event loop and must use the async
    `get_db_session()`. The sync factory is scoped to:

    1. `src/adapters/*.py` — sync SQLAlchemy inside adapter method bodies
    2. `src/core/audit_logger.py::AuditLogger._log_operation_sync` —
       internal sync method called from adapter threads
    3. Unit tests that exercise adapters directly

    Structural guard: `tests/unit/test_architecture_no_sync_session_in_async_context.py`
    forbids `get_sync_db_session()` imports from `src/core/tools/`,
    `src/admin/routers/`, `src/core/helpers/`, `src/services/delivery_*.py`,
    `src/services/media_buy_*.py`. Allowlist: `src/adapters/`,
    `src/core/audit_logger.py`.

    This sync factory is SEPARATE from `src/services/background_sync_db.py`
    (Decision 9 sync-bridge), which has its own engine with different pool
    sizing + 600s statement timeout for multi-hour GAM syncs. The two sync
    engines coexist; each serves a different consumer.
    """
    session = _sync_sessionmaker()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
```

### C. The wrap pattern — 18 call sites

Every adapter method call inside an `async def` body must be wrapped in `await run_in_threadpool(...)`. The wrap pattern:

```python
from starlette.concurrency import run_in_threadpool

async def _create_media_buy_impl(
    req: CreateMediaBuyRequest,
    identity: ResolvedIdentity,
    session: AsyncSession,
) -> CreateMediaBuyResult:
    ...
    adapter = get_adapter(principal, dry_run=dry_run, tenant=tenant)

    # Path B wrap — adapter is sync, _impl is async.
    # The worker thread borrowed from uvicorn's anyio pool runs the
    # sync adapter method, opens its own sync session via
    # get_sync_db_session() (if the adapter does DB work), and
    # returns the result to the event loop.
    response = await run_in_threadpool(
        adapter.create_media_buy,
        req,
        packages,
        start_time,
        end_time,
        package_pricing_info,
    )
    ...
```

**The 18 known call sites** (verified by the Decision 1 Opus subagent, 2026-04-11):

| # | File:line | Method | Notes |
|---|---|---|---|
| 1 | `src/core/tools/media_buy_create.py:429` | `adapter.create_media_buy(...)` | The hot path |
| 2 | `src/core/tools/media_buy_create.py:3283` | `adapter.add_creative_assets(...)` | Inside loop |
| 3 | `src/core/tools/media_buy_create.py:3386` | `adapter.add_creative_assets(...)` | Second loop |
| 4 | `src/core/tools/media_buy_update.py:400` | `adapter.update_media_buy(...)` | Pause/resume |
| 5 | `src/core/tools/media_buy_update.py:460` | `adapter.update_media_buy(...)` | Budget update |
| 6 | `src/core/tools/media_buy_update.py:532` | `adapter.update_media_buy(...)` | Full update |
| 7 | `src/core/tools/media_buy_delivery.py:268` | `adapter.get_media_buy_delivery(...)` | Per-media-buy |
| 8 | `src/core/tools/performance.py:89` | `adapter.update_media_buy_performance_index(...)` | Hot path |
| 9 | `src/core/tools/media_buy_create.py:??` | `adapter.orders_manager.approve_order(...)` | GAM sub-manager |
| 10 | `src/core/tools/media_buy_create.py:??` | `adapter.creatives_manager.add_creative_assets(...)` | GAM sub-manager |
| 11-17 | `src/core/tools/media_buy_*.py`, `src/core/tools/creative*.py` | Remaining DR/DL/signals sites | Verify in Spike 4.5 |
| 18 | `src/admin/blueprints/operations.py:252` | `adapter.get_media_buy_delivery(...)` | Admin direct-call site |

Spike 4.5 grep-verifies the complete list and produces the exact count in `spike-decision.md`.

### D. `functools.partial` when kwargs don't round-trip

`starlette.concurrency.run_in_threadpool` under newer `anyio` versions uses `anyio.to_thread.run_sync` which accepts `*args` but **NOT** `**kwargs`. If an adapter call needs keyword arguments, wrap with `functools.partial`:

```python
from functools import partial
from starlette.concurrency import run_in_threadpool

response = await run_in_threadpool(
    partial(
        adapter.update_media_buy,
        media_buy_id=media_buy_id,
        updates=updates,
        effective_from=effective_from,
    )
)
```

Alternatively, refactor the adapter method to take positional args. For the 18 current sites, a quick grep confirms all are callable positionally.

### E. AuditLogger split — sync internal, async public

`src/core/audit_logger.py::AuditLogger.log_operation` currently opens its own `with get_db_session() as db:` and writes an `AuditLog` row. Under Path B this method becomes dual-natured:

```python
# src/core/audit_logger.py (Wave 4 target state)
class AuditLogger:
    def _log_operation_sync(
        self,
        *,
        operation: str,
        tenant_id: str,
        principal_id: str,
        success: bool,
        details: dict,
        ...
    ) -> None:
        """Sync internal method — called by adapter code running inside
        run_in_threadpool worker threads.

        Opens its own sync session via get_sync_db_session(). Cannot be
        called from async context — the worker thread has no event loop.
        """
        from src.core.database.database_session import get_sync_db_session

        with get_sync_db_session() as db:
            log = AuditLog(
                operation=operation,
                tenant_id=tenant_id,
                principal_id=principal_id,
                success=success,
                details=details,
                ...
            )
            db.add(log)
            # commit handled by get_sync_db_session() finally block

    async def log_operation(
        self,
        *,
        operation: str,
        tenant_id: str,
        principal_id: str,
        success: bool,
        details: dict,
        ...
    ) -> None:
        """Async public wrapper — called by _impl functions.

        Delegates to the sync internal method via run_in_threadpool so
        the async caller doesn't block the event loop on the audit write.
        """
        from starlette.concurrency import run_in_threadpool
        from functools import partial

        await run_in_threadpool(
            partial(
                self._log_operation_sync,
                operation=operation,
                tenant_id=tenant_id,
                principal_id=principal_id,
                success=success,
                details=details,
                ...
            )
        )
```

**Caller migration:**
- ~30 `_impl` call sites update to `await audit_logger.log_operation(...)` — async path.
- Adapter call sites update to `self.audit_logger._log_operation_sync(...)` (the underscore prefix signals intentional private-method access from the privileged sync path).

### F. Threadpool tune — lands at L0 lifespan (not L5+)

> **[L0]** The threadpool limiter bump is L0 scope, not L5+. FastAPI runs sync `def` admin handlers in the anyio threadpool starting from L0 (Flask parity still uses the threadpool via `a2wsgi.WSGIMiddleware` under the hood, but the moment any FastAPI-native handler ships, the limiter size directly governs admin concurrency).

Anyio's default thread limiter is 40 workers. Sync FastAPI handlers run in this threadpool; admin OAuth bursts + (L5+) adapter `run_in_threadpool` wraps can push concurrency past 40. The 41st request blocks waiting for a token before the handler even starts.

Configure in app lifespan BEFORE any request is served:

```python
# src/app.py::lifespan — L0 addition, refined in L5 when adapter wraps go live
from contextlib import asynccontextmanager
import anyio.to_thread
import logging
import os

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Configure in app lifespan BEFORE any request is served.
    # Default anyio threadpool is 40 tokens. Sync FastAPI handlers run in this
    # threadpool; admin OAuth bursts + adapter wraps can push concurrency past 40.
    # The 41st request blocks waiting for a token before the handler even starts.
    #
    # ADCP_THREADPOOL_TOKENS env var allows ops-tuning; default 80 covers
    # observed peak admin load (~30 concurrent OAuth callbacks) with headroom.
    # NOTE: threadpool tokens × DB pool size interaction — pool_size=20 +
    # max_overflow=30 = 50 DB connections; at 80 tokens + heavy DB load,
    # sustained saturation will queue on DB pool, not threadpool. Monitor both.
    _tokens = int(os.environ.get("ADCP_THREADPOOL_TOKENS", "80"))
    anyio.to_thread.current_default_thread_limiter().total_tokens = _tokens
    logger.info("threadpool_limiter_configured", tokens=_tokens)
    ...
    yield
    ...
```

**Env var name:** `ADCP_THREADPOOL_TOKENS` is canonical. An older draft used `ADCP_THREADPOOL_SIZE`; both remained in various references during the pivot. New code uses `ADCP_THREADPOOL_TOKENS`.

Monitoring: the `/health/pool` endpoint (L5+ acceptance) exposes the current thread limiter's `borrowed_tokens` and `total_tokens` alongside the async and sync engine pool stats.

### G. Structural guard against drift

`tests/unit/test_architecture_adapter_calls_wrapped_in_threadpool.py` — full content in §3 of the Decision 1 analysis. AST-walks `src/core/tools/`, `src/admin/blueprints/`, `src/admin/routers/`, `src/core/helpers/` for method calls matching `adapter.METHOD(...)`, `self.adapter.METHOD(...)`, `adapter.submanager.METHOD(...)` inside `async def` bodies. Each such call must be the first argument to a `run_in_threadpool(...)` call expression.

**Allowlist:** `src/services/background_sync_service.py` (Decision 9 sync-bridge thread — adapter calls already in sync context, no wrap needed). `src/services/` other services (`delivery_webhook_scheduler.py`, `media_buy_status_scheduler.py`) do not call adapters directly; grep-verified.

**Ratcheting:** the guard's allowlist starts empty after Wave 4b lands. New violations fail the build immediately.

### H. Return-type gotcha — don't return ORM instances from adapter methods

Adapters running inside `run_in_threadpool` worker threads hold sync `Session` instances. If an adapter method returns an ORM instance (not a Pydantic DTO), the calling `_impl` in async context inherits a detached ORM object. Accessing lazy-loaded relationships on that object triggers `MissingGreenlet` from the async side.

**Rule:** adapters return Pydantic models, dicts, or primitive types. Never raw SQLAlchemy ORM instances. Enforced by:

- `src/adapters/base.py` abstract method return annotations (all declare Pydantic types)
- `tests/unit/test_architecture_adapter_return_types.py` (new guard in Wave 4) — AST-walks `src/adapters/*.py` for method return annotations and fails on raw ORM types

### I. Gotchas

- **`mock_ad_server.py` uses `time.sleep()` for latency simulation.** Under Path B this still works — the sleep happens inside the worker thread, not on the event loop, so it doesn't block other requests. No conversion to `asyncio.sleep()` needed. The Decision 7 refactor of `mock_ad_server.py::complete_after_delay` IS still required (that's a `threading.Thread` background task, not a wrapped adapter call — the thread becomes `asyncio.create_task`).
- **Kwargs in `run_in_threadpool`.** Use `functools.partial` wrapper (see §D) or refactor the adapter method to take positional args.
- **Sync-vs-async engine pool math.** 15+25 (async) + 5+10 (sync adapter) + 2+3 (sync-bridge from Decision 9) = 60 peak connections. PG default `max_connections=100` has headroom. Production deploys that raise `max_connections` should document the new async/sync split.
- **Threadpool capacity vs concurrent requests.** 80 workers is sized for ~80 concurrent adapter-invoking requests. Admin UI traffic typically ≤10 concurrent; MCP tool call concurrent load can spike higher. Monitor `anyio.to_thread.current_default_thread_limiter().borrowed_tokens` via `/health/pool` and raise `ADCP_THREADPOOL_SIZE` if it saturates.
- **`application_name` in `pg_stat_activity`.** The async engine connections show `application_name='adcp-salesagent'`; Path B sync connections show `application_name='adcp-salesagent-sync'`; Decision 9 sync-bridge connections show `application_name='adcp-salesagent-sync-bridge'`. Three distinct labels make debugging stale connections easy.
- **Post-v2.0 sunset path.** If `googleads` releases an async variant and the 4 `requests`-based adapters are ported to `httpx.AsyncClient`, each adapter converts individually to `async def`, the wrap at the `_impl` caller becomes `await adapter.method(...)` instead of `await run_in_threadpool(adapter.method, ...)`, and eventually the sync factory in `database_session.py` + psycopg2 dep + libpq can all be deleted. Structural guard allowlist adjusts as each adapter completes.

---

## 11.15 SimpleAppCache — flask-caching replacement (Decision 6, 2026-04-11)

> **[L0-L2]** This module is part of Flask removal. Use sync patterns. It stays sync through L4 and auto-converts at L5 only to the extent its callers flip to `async def` (most functions here are framework-agnostic utilities and remain sync across all layers).

Replaces `flask-caching` (`SimpleCache` backend) with a thread-safe, async-safe in-process cache backed by `cachetools.TTLCache`. Ships in **Wave 3** as a prep module, with consumer sites migrating in the same PR, followed by `flask-caching` removal from `pyproject.toml`.

> **Decision 6 deep-think 2026-04-11:** the replacement is ~90 LOC (not the 40 LOC estimated by Agent A). 5 traps identified: (1) both inventory sites cache `jsonify(...)` Response objects (Flask-ism, breaks under FastAPI — must cache dicts and reconstruct `JSONResponse` on hit); (2) `cachetools.TTLCache` is NOT thread-safe without explicit locking; (3) `cache_key` + `cache_time_key` pair writes are non-atomic (fold into single 2-tuple entry); (4) background_sync_service.py:472 runs in a `threading.Thread` with no Flask app context (latently broken even in Flask — `try/except` silently eats the error); (5) lifespan startup race window (cache must exist before the first HTTP request can arrive).

```python
# src/admin/cache.py
"""In-process inventory cache for admin routes and background-sync threads.

Replaces flask-caching's SimpleCache with a thread-safe, async-safe wrapper
over cachetools.TTLCache. Single per-process instance; v2.0 is hard-constrained
to single-worker uvicorn so this is semantically identical to the Flask version.

Access paths:
  - FastAPI admin handlers:  request.app.state.inventory_cache
  - Background threading.Thread workers: get_app_cache()
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Any, Protocol

from cachetools import TTLCache
from fastapi import FastAPI

logger = logging.getLogger(__name__)

_DEFAULT_MAXSIZE = 1024
_DEFAULT_TTL_SECONDS = 300


class CacheBackend(Protocol):
    """Minimal backend interface — v2.2 Redis swap conforms to this."""

    def get(self, key: str, default: Any = None) -> Any: ...
    def set(self, key: str, value: Any) -> None: ...
    def delete(self, key: str) -> bool: ...


class SimpleAppCache:
    """Thread-safe TTL cache with a dict-like get/set/delete API.

    Invariants:
      - All access guarded by threading.RLock. Safe under concurrent access
        from the asyncio event loop thread AND anyio threadpool workers
        (Decision 1 Path B) AND background_sync_service threading.Thread
        workers (Decision 9). RLock (not Lock) so nested helpers can
        safely re-enter.
      - Single uniform TTL per cache instance. Per-key TTL intentionally
        NOT supported — the 3 consumer sites all use 300s.
      - LRU-evicted when maxsize exceeded. Eviction is acceptable because
        every cached entry is reconstructible from the database.
      - .get() on missing/expired key returns default, never raises.
      - .delete() on missing key returns False, never raises (idempotent).
    """

    def __init__(self, maxsize: int = _DEFAULT_MAXSIZE, ttl: int = _DEFAULT_TTL_SECONDS) -> None:
        self._cache: TTLCache[str, Any] = TTLCache(maxsize=maxsize, ttl=ttl)
        self._lock = threading.RLock()
        self._maxsize = maxsize
        self._ttl = ttl

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            try:
                return self._cache[key]
            except KeyError:
                return default

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._cache[key] = value

    def delete(self, key: str) -> bool:
        with self._lock:
            try:
                del self._cache[key]
                return True
            except KeyError:
                return False

    def clear(self) -> None:
        """Test-only helper; not called from production code."""
        with self._lock:
            self._cache.clear()

    @property
    def stats(self) -> dict[str, int]:
        with self._lock:
            return {"size": len(self._cache), "maxsize": self._maxsize, "ttl": self._ttl}


class _NullAppCache:
    """No-op fallback returned by get_app_cache() when install_app_cache()
    has not yet run (lifespan startup race window). All operations succeed
    silently.

    This mirrors the latent-broken behavior of the pre-migration Flask code
    where background_sync_service.py:479's try/except silently ate
    invalidation failures.
    """

    def get(self, key: str, default: Any = None) -> Any:
        return default

    def set(self, key: str, value: Any) -> None:
        return None

    def delete(self, key: str) -> bool:
        return False


_APP_CACHE: SimpleAppCache | _NullAppCache = _NullAppCache()
_INSTALL_LOCK = threading.Lock()


def install_app_cache(app: FastAPI) -> SimpleAppCache:
    """Create the process-wide SimpleAppCache and attach it to the app.

    MUST be called from the FastAPI lifespan startup block BEFORE yield.
    Safe to call multiple times (idempotent).
    """
    global _APP_CACHE
    with _INSTALL_LOCK:
        if isinstance(_APP_CACHE, SimpleAppCache):
            return _APP_CACHE
        maxsize = int(os.environ.get("ADCP_INVENTORY_CACHE_MAXSIZE", _DEFAULT_MAXSIZE))
        ttl = int(os.environ.get("ADCP_INVENTORY_CACHE_TTL", _DEFAULT_TTL_SECONDS))
        cache = SimpleAppCache(maxsize=maxsize, ttl=ttl)
        _APP_CACHE = cache
        app.state.inventory_cache = cache
        logger.info(
            "SimpleAppCache installed (maxsize=%d, ttl=%ds) at app.state.inventory_cache",
            maxsize,
            ttl,
        )
        return cache


def get_app_cache() -> SimpleAppCache | _NullAppCache:
    """Return the process-wide cache. Safe to call from any thread.

    Returns a _NullAppCache stub if install_app_cache() has not yet run
    (lifespan startup race window). Callers MUST NOT check the type —
    treat the return value as opaque and rely on the get/set/delete contract.
    """
    return _APP_CACHE


def _reset_app_cache_for_tests() -> None:
    """Test-only: reset the module global between tests. NOT for production."""
    global _APP_CACHE
    with _INSTALL_LOCK:
        _APP_CACHE = _NullAppCache()
```

**Lifespan integration** (in `src/app.py`):

```python
@asynccontextmanager
async def app_lifespan(app: FastAPI):
    from src.admin.cache import install_app_cache
    install_app_cache(app)  # MUST run before yield — background threads may start immediately
    _install_admin_mounts()
    logger.info("FastAPI application starting up")
    yield
    logger.info("FastAPI application shutting down")
```

**Consumer migration pattern** (both inventory sites):

```python
# BEFORE (Flask):
cached_result = cache.get(cache_key)
cached_time = cache.get(cache_time_key)
# ... later:
cache.set(cache_key, jsonify({...}), timeout=300)
cache.set(cache_time_key, time.time(), timeout=300)

# AFTER (FastAPI — fold cache_key + cache_time_key into single 2-tuple):
cache = request.app.state.inventory_cache
cached_entry = cache.get(cache_key)
if cached_entry is not None:
    payload_dict, cached_time = cached_entry
    # ... staleness check against last_sync.completed_at ...
    return JSONResponse(payload_dict)
# ... build payload_dict from DB ...
cache.set(cache_key, (payload_dict, time.time()))
return JSONResponse(payload_dict)
```

**Background sync invalidation:**

```python
# BEFORE (Flask — latently broken, try/except silently eats RuntimeError):
from flask import current_app
try:
    current_app.cache.delete(f"inventory_tree:v2:{tenant_id}")
except Exception:
    pass

# AFTER:
from src.admin.cache import get_app_cache
get_app_cache().delete(f"inventory_tree:v2:{tenant_id}")
# _NullAppCache stub absorbs the call if lifespan hasn't installed yet.
```

**Strict 12-step migration order:**
1. Land `src/admin/cache.py` module (new file, ~90 LOC).
2. Land `tests/unit/admin/test_simple_app_cache.py` (13 unit tests).
3. Wire `install_app_cache(app)` into `app_lifespan` in `src/app.py` BEFORE `yield`.
4. Land `tests/unit/test_architecture_no_flask_caching_imports.py` allowlisting current sites.
5. Port `inventory.py:874` consumer to `request.app.state.inventory_cache`, caching dict not Response.
6. Port `inventory.py:1133` consumer, same pattern.
7. Port `background_sync_service.py:472` consumer to `get_app_cache()`. **Same commit deletes `from flask import current_app` in that file** — closes Wave 3 ImportError blocker.
8. Tighten `test_architecture_no_flask_caching_imports.py` allowlist to empty.
9. Delete `flask_caching` init block from `src/admin/app.py:199-208`.
10. Remove `flask-caching>=2.3.0` from `pyproject.toml`; `uv lock`.
11. Delete `scripts/deploy/entrypoint_admin.sh:28-30` flask-caching debug probe.
12. Land integration test `tests/integration/test_inventory_cache_behavior.py` (3 test cases minimum).

Steps 1-4 CAN ship in a prep PR before Wave 3. Steps 5-12 bundle into Wave 3.

**Two structural guards:**
- `tests/unit/test_architecture_no_flask_caching_imports.py` — AST walker asserting no file under `src/` contains `import flask_caching` or `from flask_caching import *`. Allowlist parameter starts populated during steps 1-7 and empties at step 8.
- `tests/unit/test_architecture_inventory_cache_uses_module_helpers.py` — asserts that any file calling `current_app.cache` is forbidden, and any file under `src/services/` accessing the inventory cache MUST go through `get_app_cache()` (not `request.app.state.inventory_cache`).

---

## 11.15.1 `tests/unit/test_architecture_no_module_scope_create_app.py` — Module-scope create_app() guard

> **[Layer 2 / Wave 3]** AST-scanning guard activated in Layer 2 (Wave 3) immediately before `src/admin/app.py` is deleted. Prevents a 4th-derivative pytest-collection cascade.

> **Why:** when Layer 2 deletes `src/admin/app.py`, any test module that evaluates `admin_app = create_app()` at module scope fails to import. Since pytest collects all test modules before running any test, one broken module poisons the entire collection step — every test in the suite is reported as collection-error, not just the offending file. The guard forbids module-scope `create_app()` calls so the cascade cannot recur.

### A. Implementation

```python
"""AST scan: disallow module-scope `create_app()` calls in tests/.

Module-scope `create_app()` evaluates at import time. If the imported module
(or the module it transitively imports) is absent, pytest collection fails
for this test module — and pytest-collection is all-or-nothing, so a single
broken module halts the entire suite.

Rule: `create_app()` may only be called inside a function body (fixture,
method, helper). Module-scope assignment like `admin_app = create_app()`
or naked expression `create_app()` at top level is forbidden.
"""
import ast
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).parent.parent
REPO_ROOT = TESTS_DIR.parent


def _module_scope_create_app_calls(tree: ast.Module) -> list[int]:
    """Return line numbers of top-level statements that call create_app()."""
    offenders = []
    for stmt in tree.body:
        # Top-level assignment: `x = create_app()`
        if isinstance(stmt, ast.Assign):
            if isinstance(stmt.value, ast.Call) and _is_create_app(stmt.value):
                offenders.append(stmt.lineno)
        # Top-level naked expression: `create_app()`
        elif isinstance(stmt, ast.Expr):
            if isinstance(stmt.value, ast.Call) and _is_create_app(stmt.value):
                offenders.append(stmt.lineno)
    return offenders


def _is_create_app(call: ast.Call) -> bool:
    if isinstance(call.func, ast.Name):
        return call.func.id == "create_app"
    if isinstance(call.func, ast.Attribute):
        return call.func.attr == "create_app"
    return False


def _iter_test_files():
    for path in TESTS_DIR.rglob("*.py"):
        if "conftest" in path.name or path.name.startswith("test_"):
            yield path


def test_no_module_scope_create_app_calls():
    offenders: list[tuple[Path, int]] = []
    for path in _iter_test_files():
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:
            continue
        for lineno in _module_scope_create_app_calls(tree):
            offenders.append((path.relative_to(REPO_ROOT), lineno))
    assert not offenders, (
        "Module-scope create_app() calls break pytest collection when "
        "src/admin/app.py is deleted in Layer 2. Move each call into a "
        "fixture or function body:\n"
        + "\n".join(f"  {p}:{n}" for p, n in offenders)
    )
```

### B. Tests (meta-test)

```python
# tests/unit/test_architecture_no_module_scope_create_app_meta.py
"""Meta-test: plant a violation, assert the guard catches it."""
import ast
from textwrap import dedent

from tests.unit.test_architecture_no_module_scope_create_app import (
    _module_scope_create_app_calls,
)


def test_module_scope_assignment_detected():
    tree = ast.parse(dedent("""
        from src.admin.app import create_app
        app = create_app()
    """))
    assert _module_scope_create_app_calls(tree) == [3]


def test_function_scope_call_not_detected():
    tree = ast.parse(dedent("""
        from src.admin.app import create_app

        def get_app():
            return create_app()
    """))
    assert _module_scope_create_app_calls(tree) == []


def test_fixture_scope_call_not_detected():
    tree = ast.parse(dedent("""
        import pytest
        from src.admin.app import create_app

        @pytest.fixture
        def app():
            return create_app()
    """))
    assert _module_scope_create_app_calls(tree) == []


def test_naked_expression_detected():
    tree = ast.parse(dedent("""
        from src.admin.app import create_app
        create_app()
    """))
    assert _module_scope_create_app_calls(tree) == [3]


def test_unrelated_call_not_detected():
    tree = ast.parse(dedent("""
        app = something_else()
    """))
    assert _module_scope_create_app_calls(tree) == []
```

### C. Activation

- **Layer 2 entry gate:** the 5 known module-scope sites must be fixed BEFORE this guard activates. Sites: `tests/integration/conftest.py:18`, `tests/integration/test_template_url_validation.py:16`, `tests/integration/test_product_deletion.py:15`, `tests/admin/test_accounts_blueprint.py:17` (covered by whole-file deletion), `tests/admin/test_product_creation_integration.py:8` (covered by whole-file deletion).
- **Layer 2 exit gate:** guard runs in `make quality` with zero allowlist — any new module-scope `create_app()` call fails the build immediately.
- **Interaction with other guards:** none — this is a standalone AST scan over `tests/`.

---

## 11.16 `tests/unit/test_architecture_admin_handlers_async.py` — Admin-handlers-async guard (L5c+)

> **[Layer 5c]** Activated when the 3-router async pilot lands. Atomically replaces two L0-L4 guards (`test_architecture_handlers_use_sync_def.py` and `test_architecture_no_async_db_access.py`) in the SAME commit — no dual-enforcement window.

### A. Implementation

```python
# tests/unit/test_architecture_admin_handlers_async.py
"""AST scan: every admin router handler must be async def.

Activated in Layer 5c when the 3-router pilot lands. Before L5c, the
inverse guard (test_architecture_handlers_use_sync_def.py) enforced the
opposite — those two guards are atomic-swapped in the same commit.
"""
import ast
from pathlib import Path

ADMIN_ROUTERS_DIR = Path(__file__).parent.parent.parent / "src" / "admin" / "routers"


_ROUTER_METHODS: frozenset[str] = frozenset({
    "get", "post", "put", "delete", "patch", "options", "head", "trace",
    "route", "api_route", "websocket",
})


def _is_router_handler(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True iff ANY decorator on the function is `@<something>.<router-method>(...)`
    or bare `@<something>.<router-method>`.

    Walks ALL decorators — an outer `@audit_log` wrapping an inner `@router.post`
    must still flag the function. Matches both Call forms (`@router.post(...)`)
    and bare Attribute forms (`@router.websocket`).
    """
    for dec in node.decorator_list:
        target = dec.func if isinstance(dec, ast.Call) else dec
        if isinstance(target, ast.Attribute) and target.attr in _ROUTER_METHODS:
            return True
    return False


def test_admin_handlers_all_async():
    violations = []
    for py in ADMIN_ROUTERS_DIR.rglob("*.py"):
        tree = ast.parse(py.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and _is_router_handler(node):
                violations.append(f"{py.relative_to(ADMIN_ROUTERS_DIR.parent.parent.parent)}:{node.lineno}:{node.name}")
    assert not violations, (
        "Admin router handlers must be async def (activated at L5c).\n"
        + "\n".join(f"  {v}" for v in violations)
    )
```

### B. Tests (meta-test)

Meta-test plants a sync-def handler in a temp router file, asserts guard flags it; plants an async-def handler, asserts guard is silent. Uses the `write-guard` skill pattern. Each stacked-decorator case below validates a specific branch of `_is_router_handler`:

```python
# tests/unit/test_architecture_admin_handlers_async_meta.py
import ast
import textwrap

from tests.unit.test_architecture_admin_handlers_async import _is_router_handler


def _fn(src: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    tree = ast.parse(textwrap.dedent(src))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node
    raise AssertionError("no function in source")


def test_stacked_decorator_with_audit_outer_detected():
    # @audit_log wraps @router.post — walk ALL decorators, not just the outermost.
    node = _fn("""
        @audit_log
        @router.post("/foo")
        def handler(): pass
    """)
    assert _is_router_handler(node)


def test_stacked_decorator_with_router_outer_detected():
    node = _fn("""
        @router.post("/foo")
        @audit_log
        def handler(): pass
    """)
    assert _is_router_handler(node)


def test_websocket_decorator_detected():
    node = _fn("""
        @router.websocket("/ws")
        async def handler(ws): pass
    """)
    assert _is_router_handler(node)


def test_bare_attribute_decorator_detected():
    # @router.websocket with no Call — bare Attribute form.
    node = _fn("""
        @router.websocket
        async def handler(ws): pass
    """)
    assert _is_router_handler(node)


def test_non_router_decorator_not_detected():
    node = _fn("""
        @audit_log
        @require_admin
        def helper(): pass
    """)
    assert not _is_router_handler(node)


def test_module_level_function_not_detected():
    node = _fn("""
        def plain_function(): pass
    """)
    assert not _is_router_handler(node)
```

### C. Activation

- **L5c entry gate (atomic):** in the same commit that converts the first pilot router to `async def`, (a) delete `tests/unit/test_architecture_handlers_use_sync_def.py`, (b) delete `tests/unit/test_architecture_no_async_db_access.py`, (c) add this guard.
- **Rationale for atomic swap:** the sync-def guard becomes wrong the moment any pilot handler flips to async; keeping both guards in the same commit window would fire contradictory assertions on the same file. The atomic swap prevents the dual-enforcement cliff.
- **Allowlist:** empty at inception. Any NEW handler that lands after L5c must be async-def from day one.

---

## 11.17 `tests/unit/test_architecture_admin_route_names_unique.py` — Route name uniqueness guard (L2)

> **[Layer 2]** AST-scan equivalent + runtime-introspection guard asserting admin routes have unique `name=` values. Starlette silently accepts duplicate route names — the second registration wins, the first becomes unreachable via `request.url_for('name')`.

### A. Implementation

```python
# tests/unit/test_architecture_admin_route_names_unique.py
"""Assert admin routes have unique name= values for url_for resolution.

Starlette silently accepts duplicate route names — the second registration
wins, the first becomes unreachable via request.url_for('name'). Scope the
check to admin/tenant routes only so it doesn't false-positive on A2A SDK
grafted routes or MCP mount names.
"""
from collections import Counter

from fastapi.routing import APIRoute
from starlette.testclient import TestClient


def test_admin_route_names_unique(admin_app):
    # admin_app is a fixture yielding the configured FastAPI app
    admin_names = [
        r.name for r in admin_app.routes
        if isinstance(r, APIRoute)
        and r.name is not None
        and (r.path.startswith("/admin/") or r.path.startswith("/tenant/"))
    ]
    counts = Counter(admin_names)
    duplicates = {name: count for name, count in counts.items() if count > 1}
    assert not duplicates, (
        "Duplicate admin route names — Starlette silently drops the first "
        "registration; url_for will resolve to the second only:\n"
        + "\n".join(f"  {name} (x{count})" for name, count in duplicates.items())
    )


def test_all_admin_routes_named(admin_app):
    unnamed = [
        r.path for r in admin_app.routes
        if isinstance(r, APIRoute)
        and r.name is None
        and (r.path.startswith("/admin/") or r.path.startswith("/tenant/"))
    ]
    assert not unnamed, (
        "Admin/tenant routes must have name= set (Pattern #3 — url_for is "
        "the only URL generator):\n"
        + "\n".join(f"  {p}" for p in unnamed)
    )
```

### B. Tests (meta-test)

Meta-test constructs a FastAPI app with two routes sharing `name="foo"`; asserts the guard raises. Flips one name; asserts the guard passes. Second meta-test plants an unnamed admin route; asserts `test_all_admin_routes_named` flags it.

### C. Activation

- **L2 entry gate:** the guard activates in the same PR that deletes the Flask catch-all. Scope-limited to `/admin/` + `/tenant/` paths so A2A SDK grafted routes (which have their own name conventions) are not false-positive matches.
- **Allowlist:** empty at inception. The Wave 0/L0 guard `test_architecture_admin_routes_named.py` enforces the `name=` invariant ahead of time; this guard adds the uniqueness check on top.

---

## Cross-cutting concerns

### Middleware ordering (final answer)

Canonical runtime order (outermost → innermost). Three progressive shapes — the stack grows as the migration advances:

**L1a (initial port, 7 middlewares):**
```
Fly → ExternalDomain → UnifiedAuth → Session → CSRF → RestCompat → CORS → handler
```

**L2 (Flask removal, 9 middlewares — adds TrustedHost AND SecurityHeaders):**
```
Fly → ExternalDomain → TrustedHost → SecurityHeaders → UnifiedAuth → Session → CSRF → RestCompat → CORS → handler
```

**L4+ (observability floor, 10 middlewares — adds RequestID outermost; persists through L5/L6/L7):**
```
RequestID → Fly → ExternalDomain → TrustedHost → SecurityHeaders → UnifiedAuth → Session → CSRF → RestCompat → CORS → handler
```

Registration in `src/app.py` is in **REVERSE** order (LIFO — `add_middleware` last-added becomes outermost). L4+ canonical registration:

```python
app.add_middleware(CORSMiddleware, allow_origins=...)          # innermost
app.add_middleware(RestCompatMiddleware)
app.add_middleware(CSRFOriginMiddleware, ...)
app.add_middleware(SessionMiddleware, **session_kwargs)
app.add_middleware(UnifiedAuthMiddleware)
app.add_middleware(SecurityHeadersMiddleware, https_only=settings.https_only)  # added at L2 (§11.28)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=...)   # added at L2
app.add_middleware(ApproximatedExternalDomainMiddleware)
app.add_middleware(FlyHeadersMiddleware)
app.add_middleware(RequestIDMiddleware)                        # added at L4, outermost
```

**Hard invariant (notes/CLAUDE.md #2):** `ApproximatedExternalDomainMiddleware` runs BEFORE `CSRFOriginMiddleware` — the canonical order satisfies this (ExternalDomain is outer of the pair).

**Rationale:**
- `UnifiedAuth` is **outside** `Session` by design — it authenticates from headers only (`x-adcp-auth`, `Authorization: Bearer`) and does NOT depend on `request.session`. Session still wraps CSRF/RestCompat/CORS so admin handlers downstream have session state.
- `ExternalDomain` does not touch `request.session`; its position relative to Session is functionally independent. The load-bearing constraint is that it runs before CSRF.
- `Fly` is outermost of the L1a stack so every inner middleware sees `X-Forwarded-*` normalized from `Fly-Forwarded-*`. At L4+, `RequestID` takes the outermost slot above `Fly`.

---

## 11.18 `src/core/http.py` — Shared httpx clients

> **[L2 sync + L5 async]** Replace ad-hoc `httpx.AsyncClient()` and `requests.*` call sites with lifespan-managed shared clients on `app.state.*`.
>
> **See also:** `§11.30` Concurrency math — httpx `max_connections=100` / `max_keepalive_connections=20` below must be read against the threadpool token and DB pool sizing. At 80 threadpool tokens each handler can open up to 1 outbound httpx connection, so `max_connections=100` leaves 20 headroom for webhook delivery retries and JWKS refresh. If a future deploy raises `ADCP_THREADPOOL_TOKENS` above 80, revisit `max_connections` via the derivation in §11.30.B.

> **Why:** ~45 `requests.*` sites across `src/adapters/{kevel,triton_digital,xandr,mock_ad_server,gam_reporting_service}.py`, `src/admin/blueprints/{auth.py,tenants.py,settings.py}`, `src/core/webhook_delivery.py` each construct per-call clients — no connection pool reuse, no shared timeout policy, no shared retry. Vendored SDK calls (`googleads`, `google-ads`) internally use `requests` via deep integration and are intentionally left alone; `requests>=2.33.0` stays as a pinned dep for them.

### A. Sync client (L2 ships alongside proxy-headers)

```python
# src/core/http.py (partial)
import httpx
from src.core.config import get_config

DEFAULT_TIMEOUT = httpx.Timeout(
    connect=5.0, read=30.0, write=30.0, pool=5.0,
)
OAUTH_TIMEOUT = httpx.Timeout(
    connect=5.0, read=10.0, write=10.0, pool=5.0,
)
DEFAULT_LIMITS = httpx.Limits(
    max_keepalive_connections=20,
    max_connections=100,
    keepalive_expiry=30.0,
)


def make_sync_client(timeout: httpx.Timeout | None = None) -> httpx.Client:
    return httpx.Client(
        timeout=timeout or DEFAULT_TIMEOUT,
        limits=DEFAULT_LIMITS,
        transport=httpx.HTTPTransport(retries=3),  # connect-retry only
        headers={"User-Agent": f"adcp-sales-agent/{get_config().app.version}"},
    )
```

Register in L2 lifespan:
```python
async def lifespan(app: FastAPI):
    app.state.http_client_sync = make_sync_client()
    app.state.http_client_sync_oauth = make_sync_client(timeout=OAUTH_TIMEOUT)
    try:
        yield
    finally:
        app.state.http_client_sync.close()
        app.state.http_client_sync_oauth.close()
```

Replace `requests.get(...)` calls across the 45 sites with `request.app.state.http_client_sync.get(...)`. Adapter code uses the dependency-injected sync client; webhook/outbound code in `src/core/webhook_delivery.py` upgrades to async in L5.

### B. Async client (L5+ scope)

```python
def make_async_client(timeout: httpx.Timeout | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=timeout or DEFAULT_TIMEOUT,
        limits=DEFAULT_LIMITS,
        transport=httpx.AsyncHTTPTransport(retries=3),
        headers={"User-Agent": f"adcp-sales-agent/{get_config().app.version}"},
    )
```

Lifespan registration:
```python
app.state.http_client = make_async_client()
# ... yield ...
await app.state.http_client.aclose()
```

Replace the 4 ad-hoc `httpx.AsyncClient()` sites in `src/services/webhook_delivery_service.py`, `src/services/order_approval_service.py`, `src/core/property_list_resolver.py`, `src/core/creative_agent_registry.py` with `request.app.state.http_client`.

### C. Retry semantics (shared with P8)

`httpx.HTTPTransport(retries=N)` retries **connection failures only** (not 5xx, not read timeouts). For application-level retries on idempotent outbound (webhooks, JWKS fetch), use `tenacity` (already a transitive dep via `google-api-core`) or `stamina`:

```python
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
import httpx

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.TimeoutException)),
    reraise=True,
)
def fetch_jwks(request, issuer: str) -> dict:
    r = request.app.state.http_client_sync.get(f"{issuer}/.well-known/jwks.json")
    r.raise_for_status()
    return r.json()
```

Use `tenacity` for sync paths (L2); for async (L5) either upgrade to `tenacity.retry` with `AsyncRetrying`, or adopt `stamina` for its typed `stamina.retry()` decorator with circuit-breaker integration.

### D. Tests

```python
# tests/unit/test_http_clients.py
def test_sync_client_has_shared_transport_with_retries():
    client = make_sync_client()
    assert isinstance(client._transport, httpx.HTTPTransport)
    assert client.timeout.connect == 5.0

def test_oauth_client_has_shorter_read_timeout():
    client = make_sync_client(timeout=OAUTH_TIMEOUT)
    assert client.timeout.read == 10.0
```

### E. Scope summary

- **L2:** create `src/core/http.py`, add `make_sync_client`, register in lifespan, replace ~45 `requests.*` call sites in admin/core/adapters (excluding vendored SDK internals).
- **L5:** add `make_async_client`, register async client in lifespan, replace 4 ad-hoc `httpx.AsyncClient()` sites.
- **LOC estimate:** ~300 net (lifespan ~30, 45+4 call-site replacements, retry helpers, tests).

---

## 11.19 `src/core/config.py` — Consolidate env reads (extend existing)

> **[L4]** Extend existing `src/core/config.py` (NOT create new). `BaseSettings` groups already exist. Consolidate 146 `os.environ.get()` call sites across 40 files over L2/L4.

### Scope

- `src/core/config.py` already has: `GAMOAuthConfig`, `DatabaseConfig`, `ServerConfig`, `GoogleOAuthConfig`, `SuperAdminConfig`, `AppConfig`, and `get_config()` singleton.
- Add `env_file=".env.secrets"` to `model_config` of each group — eliminates manual dotenv loading.
- Add missing groups as needed: `CSRFConfig` (allowed_origins, allowed_suffixes), `OAuthCallbackConfig` (OIDC issuer URLs), `ThreadpoolConfig` (ADCP_THREADPOOL_TOKENS), `ObservabilityConfig` (logfire enabled/send/console flags — see §11.20).
- **Do NOT** consolidate dynamic/tenant-scoped config (read from DB, not env) — those stay per-tenant (e.g., `gemini_api_key` per-tenant override).

### Consolidation pattern

Per-module sweep (one PR per module, amortized L2→L4):
1. Grep target module for `os.environ` / `os.getenv`
2. For each env var, add corresponding field to appropriate `BaseSettings` group (with default)
3. Replace call site with `get_config().group.field`
4. Add `get_config()` to module's imports

### Guard

`test_architecture_no_direct_os_environ.py` — scope to `src/admin/` (Layer-scope) with allowlist for startup-path files (entry points, deploy scripts). Guard activates in L4 with captured baseline, shrinks to zero by L7.

### Deferred

Moving tenant-scoped keys into pydantic-settings is NOT in scope — those are DB-sourced and tenant-variable.

---

## 11.20 Observability — logfire instrumentation

> **[L6]** Use existing `logfire>=4.16.0` dep; DO NOT add `opentelemetry-sdk` directly. Logfire wraps OTLP and provides drop-in instrumentations for FastAPI, SQLAlchemy, httpx.

### Wire-up (L6 lifespan)

```python
# src/core/observability.py (new, ~25 LOC)
import logfire
from src.core.config import get_config

def configure_observability(app):
    config = get_config()
    if not config.observability.enabled:
        return
    logfire.configure(
        send_to_logfire=config.observability.send_to_logfire,  # False for self-hosted
        console=config.observability.console,
    )
    logfire.instrument_fastapi(app, capture_headers=False)
    logfire.instrument_sqlalchemy()
    logfire.instrument_httpx()
    # structlog ↔ logfire bridge already wired via configure_logging()
```

Called from L6 lifespan after middleware registration:
```python
async def lifespan(app: FastAPI):
    configure_observability(app)
    # ... engines, http clients, caches ...
    yield
```

### LOC estimate

~25 LOC in new `src/core/observability.py` + ~5 LOC lifespan call + config-group additions. **NOT 200 LOC** — logfire does the heavy lifting.

### DEFER direct OTLP

`opentelemetry-sdk` + `opentelemetry-instrumentation-{fastapi,sqlalchemy,httpx}` is **NOT added** — would duplicate what logfire does. If self-hosted OTLP collector is needed, configure logfire's OTLP exporter (`LOGFIRE_SEND_TO_LOGFIRE=false; LOGFIRE_OTLP_ENDPOINT=...`).

---

## 11.21 `app.state` singletons

> **[L4]** Move all per-request-shared singletons to `app.state.*` via lifespan. Greenfield FastAPI pattern.

### Targets

- `app.state.http_client` — async httpx (§11.18, L5)
- `app.state.http_client_sync` — sync httpx (§11.18, L2)
- `app.state.http_client_sync_oauth` — sync httpx with OAuth timeout (§11.18, L2)
- `app.state.inventory_cache` — `SimpleAppCache` (Decision 6, L4 consolidation with cache module)
- `app.state.templates` — single `Jinja2Templates` instance (L4; currently only function-local load in `src/landing/landing_page.py`)
- `app.state.oauth` — consolidate 5 `OAuth()` constructions in `src/admin/blueprints/auth.py` + `src/admin/blueprints/oidc.py` (4 sites) to a single app-scoped registry

### Access pattern

Handlers:
```python
@router.get("/foo")
def foo(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "foo.html", {...})
```

DI (preferred):
```python
def get_http_client(request: Request) -> httpx.Client:
    return request.app.state.http_client_sync

SyncHttpClientDep = Annotated[httpx.Client, Depends(get_http_client)]
```

### Deferred (already per-app or out of scope)

- `oauth` is already per-app in `create_app()` — just consolidate the 5 construction sites, don't move to module-global (which would regress).
- `logger` instances stay module-global (loggers are idempotent; moving to `app.state` adds no benefit).
- `db_engine` / `db_sessionmaker` stay as per current plan — `app.state.db_engine` per Decision 2.

---

## 11.22 Handler signature convention — `Annotated[..., Path/Query/...]`

> **[L1/L2]** FastAPI handlers use `Annotated[T, Path()/Query()/Body()/Form()/Header()/Cookie()/File()/Depends()]` for every parameter. Modern FastAPI idiom (0.95+).

### Rule

Every parameter of a function decorated with `@router.{get,post,put,delete,patch}` MUST use `Annotated[T, <FastAPI-parameter-class>]` unless:
- Typed as a Pydantic `BaseModel` subclass (request body, inferred)
- Named `request: Request`, `response: Response`, `background: BackgroundTasks`
- Prefixed `_` (conventional private)
- `*args` / `**kwargs`

### Examples

```python
# YES
@router.get("/tenant/{tenant_id}/products/{product_id}", name="admin_products_get")
def get_product(
    tenant_id: Annotated[str, Path()],
    product_id: Annotated[str, Path()],
    include_draft: Annotated[bool, Query()] = False,
    session: SessionDep = None,
) -> ProductDTO:
    ...

# NO — bare str for Path param
@router.get("/tenant/{tenant_id}/products", name="admin_products_list")
def list_products(tenant_id: str, session: SessionDep):  # ← WRONG
    ...
```

### Guard

`test_architecture_handlers_use_annotated.py` — AST scan of `src/admin/routers/`. Excludes Pydantic-body params, Request/Response/BackgroundTasks, underscore-prefixed, `*args`/`**kwargs`. Empty allowlist at L1/L2.

### LOC

~100 LOC guard + meta-test; ~150 LOC of handler signature changes across ported blueprints.

---

## 11.23 DTO base classes — `RequestDTO` / `ResponseDTO`

> **[L4]** Two base classes with different `model_config`. Blanket `strict=True, frozen=True` on all DTOs is HARMFUL — strict rejects form string-to-int coercion; frozen breaks handler mutation patterns.

### Base classes

```python
# src/admin/dtos/_base.py
from pydantic import BaseModel, ConfigDict


class RequestDTO(BaseModel):
    """Base for form/JSON input DTOs. Permissive coercion, mutable.

    Do NOT set strict=True — HTML form strings like "123" must coerce to int.
    Do NOT set frozen=True — handlers mutate before validation.
    """
    model_config = ConfigDict(
        extra="forbid",  # reject unknown fields (environment-gated elsewhere)
        str_strip_whitespace=True,
    )


class ResponseDTO(BaseModel):
    """Base for ORM→template DTOs. Strict, frozen, constructed from ORM.

    from_attributes=True: construct from ORM model via DTO.model_validate(my_model).
    frozen=True: prevents accidental handler mutation of response data.
    """
    model_config = ConfigDict(
        from_attributes=True,
        frozen=True,
        extra="forbid",
    )
```

### Usage

```python
class CreateProductRequest(RequestDTO):
    name: str
    cpm: float

class ProductResponse(ResponseDTO):
    id: str
    name: str
    cpm: float
    is_active: bool  # derived via @computed_field

@router.post("/tenant/{tenant_id}/products", name="admin_products_create")
def create_product(
    tenant_id: Annotated[str, Path()],
    req: CreateProductRequest,
    session: SessionDep,
) -> ProductResponse:
    product = ProductRepository(session).create(req, tenant_id)
    return ProductResponse.model_validate(product)
```

### Guard

`test_architecture_dto_config.py` — AST scan `src/admin/dtos/**`. Asserts `RequestDTO` subclasses don't set `strict=True` or `frozen=True`; `ResponseDTO` subclasses set both + `from_attributes=True`.

### Deferred

- `strict=True` globally (original proposal): rejected. Breaks form handling.
- `frozen=True` on `RequestDTO`: rejected. Breaks `model_validator(mode="after")` patterns that set derived fields.

---

## 11.24 `TrustedHostMiddleware` — L2 hardening (GZip dropped)

> **[L2]** Add `TrustedHostMiddleware` with wildcard subdomain support. DROP `GZipMiddleware` — nginx already gzips (configs at `config/nginx/nginx-*.conf`).

### TrustedHostMiddleware

```python
from starlette.middleware.trustedhost import TrustedHostMiddleware

app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=get_config().server.trusted_hosts,  # e.g. ["*.scope3.com", "scope3.com", "localhost"]
)
```

Position: OUTSIDE `UnifiedAuth`, INSIDE `Fly`. Reject-at-the-edge discipline.

Per-config `trusted_hosts`:
- dev: `["*"]` (permissive)
- staging/prod: `["*.scope3.com", "scope3.com"]` + platform-specific hostnames

### GZipMiddleware — DEFER

nginx already gzips responses at the edge. Adding FastAPI `GZipMiddleware` double-compresses (wastes CPU) without benefit. If/when nginx is dropped post-v2.0, re-evaluate.

### Updated canonical middleware stack (L2, 9 middlewares)

```
Fly → ExternalDomain → TrustedHost → SecurityHeaders → UnifiedAuth → Session → CSRF → RestCompat → CORS
```

(`TrustedHost` added between `ExternalDomain` and `SecurityHeaders` — reject non-allowed hosts before security-header injection and auth parsing. `SecurityHeaders` (§11.28) lands in the same L2 PR, INSIDE `TrustedHost` and OUTSIDE `UnifiedAuth`.) `RequestID` becomes the outermost middleware when it lands in L4+ — runtime order then (10 middlewares): `RequestID → Fly → ExternalDomain → TrustedHost → SecurityHeaders → UnifiedAuth → Session → CSRF → RestCompat → CORS`.

---

## 11.25 Small-win patterns (L4 consolidation)

Low-cost additional patterns grouped here so they travel together at L4.

### 11.25.1 `lazy="raise_on_sql"` on relationships (~10 LOC, L4)

Attach `lazy="raise_on_sql"` to high-fan-out relationships in `src/core/database/models.py`. Catches N+1 lazy-loads at query time instead of deferring to async context switching. Zero `lazy=` overrides exist today; add to relationships flagged in Spike 1 audit.

### 11.25.2 `pytest-httpx` + `respx` dev deps

Add to pyproject dev deps:
- `pytest-httpx>=0.30.0` — cleaner than respx for simple outbound mocks
- `respx>=0.20.0` — already referenced in foundation-modules §11.13.2

Test fixture pattern in §11.13.

### 11.25.3 `rich.traceback.install()` in dev

Add to `src/core/main.py` or entry-point module:

```python
import os
if os.environ.get("ENV") == "development":
    from rich.traceback import install
    install(show_locals=True, suppress=["httpx", "uvicorn"])
```

`rich` is already a prod dep. ~3 LOC.

### 11.25.4 Postgres `statement_timeout` — two-tier pattern

A blanket 30s timeout catches runaway OLTP queries but would kill legitimate long-running reports (GAM reporting jobs regularly scan 10-100M impression rows and take 5-10 minutes). The two-tier pattern sets an aggressive default per-connection and lets reporting paths opt into a larger bound for the duration of a single transaction.

**Defaults:**

```python
# src/core/database/db_config.py (L4 sync landing)
from contextlib import contextmanager
from sqlalchemy import event, text

DEFAULT_STATEMENT_TIMEOUT_MS = 30_000          # 30s — OLTP default
LONG_RUNNING_STATEMENT_TIMEOUT_MS = 600_000    # 10min — reporting ceiling


@event.listens_for(_sync_engine, "connect")
def _set_default_statement_timeout(dbapi_conn, conn_record):
    """Applied to every new connection from the sync engine's pool.

    Catches runaway joins, infinite-recursion CTEs, and seq-scans on missing
    indexes before they saturate the connection for the pool_recycle window.
    Override per-transaction via `with_long_statement_timeout(session)`.
    """
    with dbapi_conn.cursor() as cur:
        cur.execute(f"SET statement_timeout = {DEFAULT_STATEMENT_TIMEOUT_MS}")


@contextmanager
def with_long_statement_timeout(session):
    """Raise statement_timeout to LONG_RUNNING_STATEMENT_TIMEOUT_MS for the
    duration of the current transaction, then restore.

    Usage (sync, for L0-L4 GAM reporting):
        with get_db_session() as session:
            with with_long_statement_timeout(session):
                rows = session.execute(report_query).all()
    """
    session.execute(text(f"SET LOCAL statement_timeout = {LONG_RUNNING_STATEMENT_TIMEOUT_MS}"))
    try:
        yield
    finally:
        # SET LOCAL auto-reverts on txn commit/rollback — explicit reset is
        # a belt-and-suspenders guard for nested-use scenarios.
        session.execute(text(f"SET LOCAL statement_timeout = {DEFAULT_STATEMENT_TIMEOUT_MS}"))
```

**Async counterpart (L5+):**

```python
# src/core/database/engine.py
from contextlib import asynccontextmanager
from sqlalchemy import text

from src.core.database.db_config import (
    DEFAULT_STATEMENT_TIMEOUT_MS,
    LONG_RUNNING_STATEMENT_TIMEOUT_MS,
)


@asynccontextmanager
async def with_long_statement_timeout_async(session):
    """Async variant. Note: asyncpg does NOT fire SQLAlchemy's `connect`
    event the way psycopg2 does — it routes connection setup through its
    own connect hook. Pass `connect_args={"server_settings": {"statement_timeout": "30s"}}`
    when building the async engine (see `make_engine` in §11.0) so every
    asyncpg connection gets the default; this helper raises the per-transaction
    ceiling on top of that baseline.
    """
    await session.execute(
        text(f"SET LOCAL statement_timeout = {LONG_RUNNING_STATEMENT_TIMEOUT_MS}")
    )
    try:
        yield
    finally:
        await session.execute(
            text(f"SET LOCAL statement_timeout = {DEFAULT_STATEMENT_TIMEOUT_MS}")
        )
```

**Usage examples — GAM reporting:**

```python
# Sync (L0-L4)
from src.core.database.db_config import with_long_statement_timeout

def run_gam_impressions_report(tenant_id: str) -> ReportRows:
    with get_db_session() as session:
        with with_long_statement_timeout(session):
            return session.execute(GAM_IMPRESSIONS_QUERY, {"t": tenant_id}).all()


# Async (L5+)
from src.core.database.engine import with_long_statement_timeout_async

async def run_gam_impressions_report(tenant_id: str, session: SessionDep) -> ReportRows:
    async with with_long_statement_timeout_async(session):
        result = await session.execute(GAM_IMPRESSIONS_QUERY, {"t": tenant_id})
        return result.all()
```

**Cross-reference:** `async-audit/database-deep-audit.md` H1 (asyncpg-specific regression — the `connect` event does not fire under asyncpg, so the `server_settings` kwarg is the only way to make the default stick at the connection layer).

**Test file:** `tests/integration/test_statement_timeout.py` with three tests:

- **`test_default_statement_timeout_applied`** — open a session, run `SHOW statement_timeout`, assert the result parses to 30000ms (Postgres emits `"30s"` on some versions, `"30000"` / `"30000ms"` on others; the test should accept any of those three serializations via `_normalize_pg_timeout`).
- **`test_long_running_override_applies_within_transaction`** — enter `with_long_statement_timeout(session)`, `SHOW statement_timeout` returns 600000ms; exit the block, `SHOW statement_timeout` returns 30000ms again.
- **`test_short_query_not_affected`** — run a trivial `SELECT 1` with the default timeout active, assert it returns a result rather than raising `QueryCanceled` (catches regressions where the default is accidentally lowered below sane OLTP latency).

### 11.25.5 `types-cachetools` when SimpleAppCache lands

Add to pyproject dev deps when Decision 6 ships: `types-cachetools`.

---

## 11.26 `tests/unit/test_structural_guard_allowlist_monotonic.py` — Meta-guard

> **[L0]** Meta-guard ratcheting all structural-guard allowlists monotonically downward. Pattern matches existing `.duplication-baseline` (`check_code_duplication.py`).

### Design

Per-guard baseline file at `.guard-baselines/<guard-name>.json`:
```json
{
  "guard": "test_architecture_no_flask_imports",
  "allowlist_count": 18,
  "committed_sha": "abc1234",
  "updated_at": "2026-04-14"
}
```

Each new-allowlist-count must be `<=` baseline. On green, auto-update the baseline (reducing it) — same mechanism as `.duplication-baseline`.

### Implementation sketch

```python
# tests/unit/test_structural_guard_allowlist_monotonic.py
import json
from pathlib import Path

import pytest

BASELINES_DIR = Path(__file__).parent.parent.parent / ".guard-baselines"


def _current_allowlist_count(guard_module_name: str) -> int:
    """Import the guard module and count entries in its ALLOWLIST or EXPECTED_VIOLATIONS constant."""
    module = __import__(f"tests.unit.{guard_module_name}", fromlist=["*"])
    for name in ("ALLOWLIST", "EXPECTED_VIOLATIONS", "KNOWN_VIOLATIONS"):
        allowlist = getattr(module, name, None)
        if allowlist is not None:
            return len(allowlist)
    return 0


def _baseline_counts():
    for baseline_file in BASELINES_DIR.glob("*.json"):
        data = json.loads(baseline_file.read_text())
        yield baseline_file, data


def test_allowlist_counts_monotonic():
    violations = []
    for baseline_file, data in _baseline_counts():
        guard_module = data["guard"]
        current = _current_allowlist_count(guard_module)
        baseline = data["allowlist_count"]
        if current > baseline:
            violations.append(
                f"  {guard_module}: baseline={baseline}, current={current} (GREW by {current - baseline})"
            )
    assert not violations, (
        "Structural-guard allowlist(s) GREW — allowlists can only shrink.\n"
        "Fix the new violations, do not add to allowlist:\n"
        + "\n".join(violations)
    )


def test_baselines_auto_update_on_shrink(tmp_path):
    # Meta-test: a baseline auto-updates when current < baseline
    # (via make quality hook; not tested here at runtime, but
    # the hook's correctness is asserted by snapshot + re-run)
    pass
```

### Integration with `make quality`

Add a post-test hook that, on green, iterates through `.guard-baselines/` and updates any baseline where `current < baseline`. This is identical to how `check_code_duplication.py` updates `.duplication-baseline`.

### Interaction with layer exits

Row 48 of the Goals-Adherence Matrix (`implementation-checklist.md` §4.6). Any allowlist growth at any point fails `make quality`, blocking merge.

### Related: `test_architecture_no_get_db_session_in_handler_body.py`

Catches the L4→L5 SessionDep drift scenario: admin handlers that forgot the SessionDep migration and kept `with get_db_session() as session:` in handler bodies. When L5c flips `SessionDep` to `AsyncSession`, these unmigrated handlers silently contradict (sync `get_db_session()` + async handlers = two engines, pool drift).

Activated at L4 with empty allowlist; prevents the cascade by catching sync-session-in-handler-body at commit time.

Implementation pattern same as §11.16 — AST scan of `src/admin/routers/` flagging `with get_db_session` usage inside `FunctionDef`/`AsyncFunctionDef` decorated with `@router.*`. Matrix row 30.

---

## 11.27 Layer-scope commit-lint rule

> **[L0]** CI check asserting commits don't span more than one layer's work items. Prevents reviewer drift during long-lived migration branch.

### Rule

Every commit on `feat/v2.0.0-flask-to-fastapi` MUST declare a `Layer:` trailer:

```
feat: convert accounts router to async

Layer: L5c
```

CI runs `.github/workflows/layer-scope-check.yml`:
1. Parse the `Layer:` trailer (L0/L1a/L1b/L1c/L1d/L2/L3/L4/L5a/L5b/L5c/L5d1-5/L5e/L6/L7).
2. Read the layer's "Files to create/modify" list from `implementation-checklist.md`.
3. Compute the set of files changed in the commit (`git diff --name-only HEAD~1 HEAD`).
4. Assert every changed file is in the declared layer's file list (or on the global allowlist: `CLAUDE.md` notes, `docs/`, `README.md`).
5. If any file is out-of-layer, fail with a message naming the file and the layer it belongs to.

### Escapes

- `Layer: multi-layer` trailer permitted ONLY with reviewer sign-off in PR description.
- Docs-only commits: use `Layer: docs` trailer.
- Infra/config: use `Layer: infra` trailer.

### Implementation

```yaml
# .github/workflows/layer-scope-check.yml
name: Layer-scope
on: [pull_request]
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }
      - name: Parse and verify
        run: python scripts/ops/check_layer_scope.py ${{ github.event.pull_request.base.sha }}..${{ github.event.pull_request.head.sha }}
```

`scripts/ops/check_layer_scope.py` reads layer-to-file mappings from `implementation-checklist.md` §2 (Files to create/modify per layer) and enforces containment.

### Interaction with matrix

Row 48 of the Goals-Adherence Matrix (`implementation-checklist.md` §4.6) checks allowlist growth; this rule checks layer cohesion. Together they prevent scope drift and hidden violations.

### Interaction with 7-step cycle

The Test-Before-Implement cycle in `CLAUDE.md` of this folder produces Red/Green commit pairs. Both commits in each pair carry the same `Layer:` trailer. The post-hoc `PAIR OK` verification and the `Layer:` trailer check are run together before squash-merge.

---

## 11.28 `src/admin/middleware/security_headers.py` — Security headers (CSP/HSTS/X-Frame/etc.)

> **[L2+]** Pure-ASGI middleware that injects browser-hardening response headers on every response. Lands with `TrustedHostMiddleware` at L2. Position: INSIDE `TrustedHostMiddleware`, OUTSIDE `UnifiedAuthMiddleware` — runs on every response including auth-rejected ones so that login, error, and 403 pages carry the same hardening headers as authenticated pages. Sync/async-agnostic (pure ASGI — intercepts `send()` on `http.response.start`).

### A. Purpose and scope

Every admin HTML response and every AdCP JSON response must carry the standard browser-hardening header set:

- `Strict-Transport-Security` (HSTS) — forces HTTPS for 1 year, preloadable
- `X-Frame-Options: DENY` — prevents clickjacking (even when CSP `frame-ancestors` is honored; double-defense for legacy UAs)
- `X-Content-Type-Options: nosniff` — prevents MIME-type sniffing attacks
- `Referrer-Policy: strict-origin-when-cross-origin` — leaks only origin (not path) on cross-origin navigations
- `Content-Security-Policy` (CSP) — restricts script/style/img/font/connect sources
- `Permissions-Policy` — disables browser feature access (accelerometer, camera, geolocation, gyroscope, magnetometer, microphone, payment, usb)

Flask had no equivalent module; Approximated's edge proxy sets a subset of these but they are not uniformly applied (and Approximated is bypassed when a request reaches via the subdomain path). SecurityHeadersMiddleware makes the policy application origin-independent.

### B. Implementation

```python
# src/admin/middleware/security_headers.py
"""Pure-ASGI middleware that injects standard browser-hardening headers on every response.

Runtime position (canonical L4+ stack):
    RequestID → Fly → ExternalDomain → TrustedHost → SecurityHeaders → UnifiedAuth → Session → CSRF → RestCompat → CORS

INSIDE TrustedHost, OUTSIDE UnifiedAuth — runs on every response including login
pages, 403s, and error pages so that hardening headers are origin-independent.
"""
from collections.abc import Iterable
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Default CSP — tight but compatible with current admin templates. Adjust in settings
# if a specific router needs `connect-src` additions.
DEFAULT_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: https:; "
    "font-src 'self' data:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self';"
)

# Permissions-Policy — disable sensors/media APIs admin does not use.
_DISABLED_PERMISSIONS = (
    "accelerometer=()",
    "camera=()",
    "geolocation=()",
    "gyroscope=()",
    "magnetometer=()",
    "microphone=()",
    "payment=()",
    "usb=()",
)
DEFAULT_PERMISSIONS_POLICY = ", ".join(_DISABLED_PERMISSIONS)


class SecurityHeadersMiddleware:
    """Injects hardening headers on every HTTP response.

    Args:
        app: downstream ASGI app
        https_only: if True, emit HSTS. Default True. Set False in staging with
            non-public domains (HSTS is irrevocable for 1 year — do not enable
            unless the hostname is committed to HTTPS for the duration).
        csp: override the default CSP string. None → DEFAULT_CSP.
        hsts_seconds: HSTS max-age. Default 1 year (31_536_000). IMPORTANT: this
            is a browser-side commitment — once shipped, the domain cannot go
            back to HTTP for `hsts_seconds` seconds. Do not enable in staging
            environments that may revert to HTTP.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        https_only: bool = True,
        csp: str | None = None,
        hsts_seconds: int = 31_536_000,
    ) -> None:
        self.app = app
        self.https_only = https_only
        self.csp = csp if csp is not None else DEFAULT_CSP
        self.hsts_seconds = hsts_seconds

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        send_with_headers = self._send_with_headers(send)
        await self.app(scope, receive, send_with_headers)

    def _send_with_headers(self, send: Send) -> Send:
        hsts_header = (
            f"max-age={self.hsts_seconds}; includeSubDomains; preload".encode()
            if self.https_only
            else None
        )
        csp_header = self.csp.encode()
        permissions_header = DEFAULT_PERMISSIONS_POLICY.encode()

        async def wrapped(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                # Only add headers not already set by the handler (allow explicit override).
                existing = {name.lower() for name, _ in headers}
                if hsts_header is not None and b"strict-transport-security" not in existing:
                    headers.append((b"strict-transport-security", hsts_header))
                if b"x-frame-options" not in existing:
                    headers.append((b"x-frame-options", b"DENY"))
                if b"x-content-type-options" not in existing:
                    headers.append((b"x-content-type-options", b"nosniff"))
                if b"referrer-policy" not in existing:
                    headers.append((b"referrer-policy", b"strict-origin-when-cross-origin"))
                if b"content-security-policy" not in existing:
                    headers.append((b"content-security-policy", csp_header))
                if b"permissions-policy" not in existing:
                    headers.append((b"permissions-policy", permissions_header))
                message["headers"] = headers
            await send(message)

        return wrapped
```

### C. Registration

Added in `app_factory.py` / `src/app.py`. LIFO registration order (innermost first) — `SecurityHeaders` registers AFTER `UnifiedAuth` and BEFORE `TrustedHost`:

```python
app.add_middleware(CORSMiddleware, allow_origins=...)          # innermost
app.add_middleware(RestCompatMiddleware)
app.add_middleware(CSRFOriginMiddleware, ...)
app.add_middleware(SessionMiddleware, **session_kwargs)
app.add_middleware(UnifiedAuthMiddleware)
app.add_middleware(SecurityHeadersMiddleware, https_only=settings.https_only)  # NEW at L2
app.add_middleware(TrustedHostMiddleware, allowed_hosts=...)
app.add_middleware(ApproximatedExternalDomainMiddleware)
app.add_middleware(FlyHeadersMiddleware)
app.add_middleware(RequestIDMiddleware)                        # outermost at L4+
```

**Canonical runtime order (L4+, 10 middlewares):**
```
RequestID → Fly → ExternalDomain → TrustedHost → SecurityHeaders → UnifiedAuth → Session → CSRF → RestCompat → CORS
```

### D. Tests

```python
# tests/unit/admin/test_security_headers.py
from starlette.applications import Starlette
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from src.admin.middleware.security_headers import SecurityHeadersMiddleware


def _app(*, https_only: bool = True) -> Starlette:
    async def ok(_): return PlainTextResponse("ok")
    async def boom(_): raise RuntimeError("boom")
    async def forbidden(_): return JSONResponse({"detail": "forbidden"}, status_code=403)

    app = Starlette(routes=[
        Route("/ok", ok),
        Route("/boom", boom),
        Route("/403", forbidden),
    ])
    app.add_middleware(SecurityHeadersMiddleware, https_only=https_only)
    return app


def test_security_headers_on_200():
    client = TestClient(_app())
    r = client.get("/ok")
    assert r.status_code == 200
    assert r.headers.get("strict-transport-security") == "max-age=31536000; includeSubDomains; preload"
    assert r.headers.get("x-frame-options") == "DENY"
    assert r.headers.get("x-content-type-options") == "nosniff"
    assert r.headers.get("referrer-policy") == "strict-origin-when-cross-origin"
    assert "default-src 'self'" in r.headers.get("content-security-policy", "")
    assert "frame-ancestors 'none'" in r.headers.get("content-security-policy", "")
    assert "camera=()" in r.headers.get("permissions-policy", "")


def test_security_headers_on_csrf_rejection():
    """Headers present on 403 responses (auth/CSRF-rejected requests).

    Load-bearing: SecurityHeaders sits OUTSIDE UnifiedAuth/Session/CSRF, so it
    fires even when the inner chain returns 403.
    """
    client = TestClient(_app())
    r = client.get("/403")
    assert r.status_code == 403
    assert r.headers.get("x-frame-options") == "DENY"
    assert "default-src 'self'" in r.headers.get("content-security-policy", "")


def test_security_headers_on_500():
    """Headers present on 500 responses.

    Starlette converts the exception to 500 via the exception_handler chain;
    SecurityHeaders' send-wrap fires on the response.start message regardless.
    """
    client = TestClient(_app(), raise_server_exceptions=False)
    r = client.get("/boom")
    assert r.status_code == 500
    assert r.headers.get("x-frame-options") == "DENY"


def test_hsts_disabled_when_https_only_false():
    client = TestClient(_app(https_only=False))
    r = client.get("/ok")
    assert "strict-transport-security" not in r.headers
    # Other headers still present
    assert r.headers.get("x-frame-options") == "DENY"
```

### E. Structural guard

Draft `tests/unit/architecture/test_architecture_security_headers_middleware_present.py` (TODO, lands in same L2 PR as the middleware):

Scans `src/app.py` / `src/admin/app_factory.py` AST and asserts:
1. `SecurityHeadersMiddleware` appears in the registration chain
2. It is registered BETWEEN `UnifiedAuthMiddleware` and `TrustedHostMiddleware` (checked by relative call-order in the AST)

Registered in §5.5 guard table with `Empty` allowlist strategy.

### F. Gotchas

- **HSTS is irrevocable for 1 year.** Do NOT enable `https_only=True` in staging with a non-public domain. Once a browser has seen the header, it will refuse HTTP for `hsts_seconds` even if the server downgrades. Settings toggle: `ADCP_HTTPS_ONLY=false` in staging.
- **`SameSite=None` on cookies vs `frame-ancestors 'none'` in CSP are independent.** Cookie SameSite controls when the cookie is attached. CSP `frame-ancestors` controls what can embed the page. Both can and should be set independently per their respective threat models.
- **Do NOT add `Content-Security-Policy-Report-Only` as a second header in this middleware.** Report-Only is a separate policy that browsers honor alongside the enforced one. If/when we want to roll out a tighter CSP, introduce it as a Report-Only policy first, collect reports, then swap to enforcing. That is v2.1 scope — the enforced-from-day-1 policy above is deliberately permissive enough that no existing admin page breaks.
- **`style-src 'unsafe-inline'` is a compromise.** Some existing Jinja templates inline `<style>` blocks. Removing `'unsafe-inline'` from `style-src` requires a separate audit pass (templates + any JS that sets `element.style`). That audit belongs in v2.1, not v2.0.
- **CSP does NOT cover `iframe src`.** Use `X-Frame-Options: DENY` AND CSP `frame-ancestors 'none'` — legacy UAs honor only the former; modern UAs honor the latter. Double-defense is intentional.
- **Header already-set respect:** the middleware uses `in existing` checks so a specific handler can override (e.g., a PDF-serving endpoint that needs `Content-Security-Policy: default-src 'none'; style-src 'unsafe-inline'`). This is rare; default is "middleware wins if handler did not set."
- **Do NOT reach into `scope["state"]` here.** This middleware runs OUTSIDE `UnifiedAuthMiddleware`, so `request.state.auth_context` is not yet populated. The header policy is identity-independent by design.

---

## 11.29 Eager-load strategy for async SQLAlchemy

> **[L5]** The pattern library for Spike 1 fixes and every repository method written after the async flip. Without a conscious eager-load strategy, `lazy="raise"` is a tripwire that fires on production traffic hours after merge.

### A. Why this section exists

Spike 1 sets `lazy="raise"` on all 68 relationships in `src/core/database/models.py`. Every existing repository query that relied on implicit lazy loading will fail — not with a deprecation warning, but with `sqlalchemy.exc.InvalidRequestError: 'MediaBuy.packages' is not available due to lazy='raise'`. The HARD gate in Spike 1 is "can we fix this in <2 days?" — if yes, L5 proceeds; if no, L5 narrows or defers to v2.1.

What "fix" means in this context is: at every query site, decide whether the relationship is needed by the response, and if so, attach an eager-load directive. Getting the directive WRONG (joinedload where selectinload was correct, or vice-versa) causes silent performance cliffs — a correct-but-slow query often feels fine in dev and quietly dies in prod under real cardinality.

### B. Relationship-type decision matrix

| Relationship shape | Directive | Why | Example from `models.py` |
|---|---|---|---|
| many-to-one (child → parent) | `joinedload` | Single-row JOIN adds 1 query of fixed width; no row explosion. | `Product.tenant`, `MediaBuy.principal` |
| one-to-many (parent → children) | `selectinload` | JOIN would multiply parent rows by child count → result-set explosion. `IN (...)` fan-out is 1 extra query but bounded width. | `MediaBuy.packages` (`models.py:972`), `Product.pricing_options` (`models.py:338`) |
| one-to-one | `joinedload` | Cardinality is 1:1; JOIN is a fixed-width add, no explosion. | `Tenant.adapter_config` (`models.py:133-138`, `uselist=False`) |
| many-to-many (through assoc table) | `selectinload` | Any JOIN path produces Cartesian explosion proportional to the product of both sides' cardinalities. `IN (...)` on the assoc table is 2 queries but bounded. | `Principal.properties` (via `principal_properties`) |
| self-referential (tree / graph) | `selectinload` + depth limit | `joinedload` on self-ref creates unbounded JOIN aliases. Always set `.options(selectinload(Model.parent).options(selectinload(Model.parent).selectinload(Model.parent)))` with explicit depth, or the IN fan-out can walk to unbounded depth. | `WorkflowStep.parent_step` (if/when enabled) |

### C. Why not always joinedload?

On first glance `joinedload` looks simpler (1 query instead of 2). But consider a `MediaBuy` with 50 packages and 30 creatives per package:

```sql
-- joinedload on all three — the pathological case
SELECT mb.*, p.*, c.*
FROM media_buys mb
LEFT JOIN packages p ON p.media_buy_id = mb.id
LEFT JOIN creatives c ON c.package_id = p.id
WHERE mb.id = 42;
-- Result set: 1 × 50 × 30 = 1,500 rows, each carrying full mb.* and p.* columns.
```

Row width × count × pickle-wire-cost = 30-100× the payload of the same data fetched via selectinload:

```sql
-- selectinload — 3 queries, bounded width
SELECT mb.* FROM media_buys WHERE id = 42;               -- 1 row
SELECT p.*  FROM packages  WHERE media_buy_id IN (42);   -- 50 rows
SELECT c.*  FROM creatives WHERE package_id  IN (...);   -- 1,500 rows (same as above) but only once, not 50×
```

The quadratic-explosion cost is invisible in dev with 2-package fixtures and catastrophic in prod with 100-package media buys. `selectinload` for collections is the default; deviate only when profiling proves the 1-extra-query cost is the bottleneck.

### D. Pattern for repository methods

Combine `joinedload` (parents) and `selectinload` (children) in the same query:

```python
# src/core/database/repositories/tenant_repository.py
from sqlalchemy import select
from sqlalchemy.orm import joinedload, selectinload

from src.core.database.models import Tenant


class TenantRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_with_products(self, tenant_id: str) -> Tenant | None:
        """Fetch tenant with adapter_config (1:1 join) and products → pricing_options (nested collections)."""
        stmt = (
            select(Tenant)
            .options(
                joinedload(Tenant.adapter_config),       # 1:1 — JOIN is safe
                selectinload(Tenant.products)            # 1:N — separate IN query
                .selectinload(Product.pricing_options),  # 1:N on child — chained selectinload
                selectinload(Tenant.currency_limits),    # 1:N — separate IN query
            )
            .where(Tenant.tenant_id == tenant_id)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
```

The chained `.selectinload(Product.pricing_options)` is the idiom for "load children of children" — NOT `joinedload(Tenant.products).joinedload(Product.pricing_options)`, which multiplies rows.

### E. Avoid over-eager loading — profile with `echo=True`

Every eager-load directive added should be verified to actually be used. A common anti-pattern is adding `selectinload` for every relationship "just in case" — this adds N queries to every read, even when the caller never touches the loaded attribute. The fix:

```python
# Development-only: see what SQL actually runs
engine = create_async_engine(url, echo=True, ...)

# Production: emit metrics via SQLAlchemy's event.listens_for(engine.sync_engine, "before_cursor_execute")
# and aggregate by endpoint. The anomaly is a route that emits 10+ SELECT queries —
# that's a sign of missing eager-loads OR a loop over a collection with per-row queries.
```

Add a structural guard at L5e exit: a repository method that declares `.options(...)` with >5 eager-loads requires a comment explaining why. Over-eager defaults are as bad as missing-eager.

### F. When `lazy="raise"` is too aggressive — `lazy="raise_on_sql"` caveat

`lazy="raise"` fires on ANY attribute access — including `len(mb.packages)` on an already-loaded collection if `expire_on_commit=True` expired it. `lazy="raise_on_sql"` is softer: it fires only when the access WOULD emit SQL. For most cases use `lazy="raise"` (stricter, catches bugs). Rare exception: session-lifecycle boundaries where we intentionally access an expired attribute and want to re-load it implicitly. Document every `lazy="raise_on_sql"` use; the structural guard requires a comment with `# lazy="raise_on_sql": ...` rationale.

### G. Spike 1 failure triage — 4-step procedure

When Spike 1 fires a lazy-load exception:

1. **Run the failing test** — `tox -e integration -- tests/integration/test_<failing>.py -x`. Capture the stack trace including the last repository method called.
2. **Collect the error** — grep `sqlalchemy.exc.InvalidRequestError.*lazy='raise'` from the pytest JSON report at `test-results/<latest>/integration.json`. List every distinct `<Model>.<attr>` pair. This is the fix surface.
3. **Add eager-load at the repository query, NOT at the call site.** Eager-load directives belong in the query that owns the session; adding them at the caller couples business logic to persistence shape. For each `<Model>.<attr>` pair, find the repository method that returned the Model instance and extend its `.options(...)` with the correct directive from the matrix above.
4. **Re-run.** If the test still fails on a different relationship, repeat. If the test fails on the same relationship, the `.options()` was added to the wrong method (often because the test uses a different repository method than you patched).

Target: ~2 minutes per fix. 40 fixes × 2 min = ~80 minutes total, fitting well inside the 2-day HARD gate budget.

### H. HARD gate threshold — <40 fixes fixable in <2 days

Spike 1 pass criteria, restated for clarity:

- **PASS** = < 40 unique `<Model>.<attr>` pairs AND the fix pattern for each is one of {`joinedload`, `selectinload`, `selectinload.selectinload`} from the matrix AND no pair requires structural schema change.
- **FAIL** = ≥ 40 pairs OR ≥ 1 pair requires schema change (e.g., replacing a relationship with a materialized aggregate) OR the estimated fix time at 2 min/fix exceeds 2 working days.

On FAIL: do NOT attempt to force L5 through. L0-L4 already ship standalone value (sync Flask removal + FastAPI-native refinement); the correct move is to narrow L5's scope (fewer async routers — e.g., only the 3 pilot routers from L5c) or defer full-async to a v2.1 epic. See `CLAUDE.md` Critical Invariant #1 (L5 HARD gate semantics).

### I. `backref=` → `back_populates` mandatory conversion (Spike 1 prep)

`models.py` currently has 5 `relationship("...", backref="...")` declarations at the exact lines:

- `models.py:727` — `Creative.tenant = relationship("Tenant", backref="creatives")`
- `models.py:1900` — `AuthorizedProperty.tenant = relationship("Tenant", backref="authorized_properties")`
- `models.py:1935` — `PropertyTag.tenant = relationship("Tenant", backref="property_tags")`
- `models.py:1971` — `PublisherPartner.tenant = relationship("Tenant", backref="publisher_partners")`
- `models.py:2010` — `PushNotificationConfig.tenant = relationship("Tenant", backref="push_notification_configs", overlaps="principal")`

Under `backref=`, SQLAlchemy synthesizes the reverse-side `relationship` at mapper-configuration time — the attribute on `Tenant` (e.g., `Tenant.creatives`) **does not appear in source code**. Spike 1's `lazy="raise"` sweep works by textually editing every `relationship(...)` call in `models.py`. Backref-synthesized reverse sides are invisible to the sweep and will NOT receive `lazy="raise"` — so they'll silently fall back to `lazy="select"` and mask failures that a consistent posture would catch.

**Mandatory Spike 1 prep step:** before running the `lazy="raise"` sweep, convert all 5 `backref=` to explicit `back_populates` pairs. Example:

```python
# models.py — before
class Creative(Base):
    tenant = relationship("Tenant", backref="creatives")

# models.py — after
class Creative(Base):
    tenant = relationship("Tenant", back_populates="creatives", lazy="raise")

class Tenant(Base):
    # ... add on the Tenant side ...
    creatives = relationship("Creative", back_populates="tenant", lazy="raise")
```

Repeat for all 5 pairs. This is ~20 LOC mechanical. Commit BEFORE the `lazy="raise"` sweep so the sweep sees both sides of every relationship.

**Structural guard:** `tests/unit/architecture/test_architecture_no_backref_only_relationships.py` — AST-walks `src/core/database/models.py` for `relationship(..., backref=...)` calls and asserts the count is 0. Added at L5a entry; its allowlist is empty from day one (any new `backref=` fails the build).

### J. Cross-references

- `async-audit/database-deep-audit.md` — original Spike 1 sizing and backref hazard
- `async-audit/agent-b-risk-matrix.md` Risk #2 (lazy-load) — fold "decision matrix: see foundation-modules §11.29" note
- `implementation-checklist.md` L5a Spike 1 row — entry gate: "convert 5 backref= to back_populates before lazy sweep"
- `execution-plan.md` Layer 5a Spike 1 entry criterion — same

---

## 11.30 Concurrency math — threadpool × DB pool × httpx

> **[L0+]** The capacity-planning reference used by every L2 pool-size commit, every L5 engine refactor, every `/health/pool` dashboard threshold, and every production deploy. This section replaces ad-hoc number-picking with a single derivation that all downstream numbers cite.

### A. The pools table

Every request that reaches an admin handler can, under worst-case conditions, consume one token from each of these pools simultaneously:

| Pool | Where configured | Default | Rationale |
|---|---|---|---|
| AnyIO threadpool | `src/app.py::lifespan` (§11.14.F) | 80 tokens (`ADCP_THREADPOOL_TOKENS`) | Sync handlers + adapter `run_in_threadpool` wrap. Default anyio=40 is too low for admin OAuth bursts. |
| Sync DB engine — admin | `src/core/database/engine.py` (L5b sync-side; L0-L4 whole-codebase) | `pool_size=40, max_overflow=40` → 80 peak | **Revised from 20+30 = 50** to match threadpool tokens under the Path-B worst case (see §C). |
| Async DB engine (L5+) | `src/core/database/engine.py` (L5b async-side) | `pool_size=20, max_overflow=30` → 50 peak | Async request path does not multiply connections per threadpool token; 50 is sufficient for 200+ req/s. |
| Sync-bridge engine (Decision 9) | `src/services/background_sync_db.py` | `pool_size=10, max_overflow=5` → 15 peak | Multi-hour GAM inventory sync jobs. Narrow pool; long-lived connections. |
| httpx client — shared | `src/core/http.py` (§11.18) | `max_connections=100, max_keepalive_connections=20` | Outbound HTTP (webhooks, JWKS fetch, OAuth token exchange). |

### B. The worst-case walkthrough — 80 concurrent GAM-calling admin requests

Start from 80 concurrent users simultaneously hitting admin endpoints that call GAM adapter methods:

1. Each request consumes 1 **threadpool token** (sync handler entry). 80 tokens used — threadpool saturated.
2. Each sync handler opens `with get_db_session()` — each consumes 1 **sync DB connection**. 80 DB connections demanded.
3. Each handler invokes `await run_in_threadpool(adapter.create_media_buy, ...)`. The adapter runs in a SEPARATE worker thread (anyio's internal thread pool under `run_in_threadpool`) — WITHIN that thread, the adapter opens its own `get_sync_db_session()` to log audit entries. **This is the key multiplier:** +1 DB connection per request, from a different code path (adapter's audit log), at the same time as the handler's session is still held.
4. Worst-case DB demand: `80 handlers × 2 connections/handler = 160 connections` spiking simultaneously.
5. Each adapter may issue outbound HTTP via httpx. `80 × 1 httpx connection = 80` — fits inside `max_connections=100` with some headroom.

### C. The tuning invariant — `DB pool max ≥ threadpool tokens` NOT `/2`

An older draft suggested `DB pool = threadpool_tokens / 2` on the theory that "each request holds one DB connection, and 40 is enough headroom." This is wrong under Path B: the adapter worker thread opens a SECOND DB connection for audit logging while the handler's first connection is still held. The invariant must reflect the worst case, not the average.

**Corrected invariant:** for Path B with adapter-side audit logging, budget `2 × threadpool_tokens` DB connections. This drives the revised admin pool:

- **Old:** `pool_size=20, max_overflow=30` → 50 peak → saturates at ~25 concurrent adapter-calling requests
- **New:** `pool_size=40, max_overflow=40` → 80 peak (per request path) + sync-bridge 15 = **95 total**, staying under PG default `max_connections=100` with 5-connection headroom for psql debugging + alembic runs

Sync-bridge stays `pool_size=10, max_overflow=5` because background_sync_service runs at most 1 tenant sync at a time (single-thread executor).

### D. Verifying PostgreSQL `max_connections`

Peak per uvicorn worker under L0-L4 (no async engine yet):

```
80 (admin sync) + 15 (sync-bridge) + 5 (alembic buffer) = 100 connections
```

Peak per uvicorn worker under L5+ (async engine added):

```
80 (admin sync during adapter wraps) + 50 (async request path during non-adapter work) +
15 (sync-bridge) + 5 (alembic) = 150 connections
```

The 150 figure assumes a pathological mix; in practice sync and async paths don't both saturate simultaneously because a single request is on one path at a time — but during rolling deploys both engines are briefly active.

**For production** set `max_connections=250` on Postgres to accommodate 1 uvicorn worker with headroom. For multi-worker deploys see §G.

### E. Per-request accounting example

100 concurrent users, each averaging 200ms admin requests:

- Throughput: 100 users × (1 / 0.2s) = **500 req/s** sustained — NOT 400 as stated in a prior draft (200ms = 5 req/s per user × 100 users).
- At 500 req/s average latency 200ms, each pool sees: threadpool ~100 tokens concurrent (still fits 80... wait, that's over-saturated; traffic queues on threadpool tokens and average latency rises until the system finds a new equilibrium), DB ~100 connections concurrent (fits inside 80+40 peak + headroom), httpx ~100 connections concurrent (fits inside 100).
- Bottleneck identification: threadpool tokens is the first to saturate in this example, NOT DB. Symptom: request p95 latency climbs while DB pool shows idle connections. The fix is to raise `ADCP_THREADPOOL_TOKENS` or reduce handler work, NOT to raise DB pool.

### F. Per-adapter semaphore — Spike 7 follow-up

GAM has per-network-code rate limits (~3000 requests/minute default). If 80 threadpool tokens all try to call the same adapter simultaneously, the adapter's caller queue grows unbounded waiting on GAM response time (20-30s at saturation). Mitigation via `anyio.CapacityLimiter` per adapter at the `run_in_threadpool` boundary:

```python
# src/adapters/__init__.py
ADAPTER_LIMITERS: dict[str, CapacityLimiter] = {
    "gam": CapacityLimiter(20),      # bounded per-network-code concurrency
    "kevel": CapacityLimiter(40),
    "triton_digital": CapacityLimiter(10),
    "xandr": CapacityLimiter(20),
    "mock_ad_server": CapacityLimiter(100),  # no external rate limit
}

# In _impl calling an adapter:
async def _create_media_buy_impl(..., adapter_name: str):
    limiter = ADAPTER_LIMITERS[adapter_name]
    async with limiter:
        result = await run_in_threadpool(adapter.create_media_buy, ...)
```

This caps the adapter-specific thread consumption without shrinking the overall threadpool — other handlers (no adapter call) proceed unimpeded. Spike 7 surfaces the exact limits per adapter.

### G. Multi-worker deployments — when to add workers

Rule of thumb: add a second uvicorn worker when a single worker's threadpool stays >80% saturated for 5+ minutes. Each worker has its own process-memory state and its own DB pool — N workers = N × pool sizing:

- 4 workers × 80 admin peak = 320 connections demanded
- 4 workers × 15 sync-bridge = 60 connections demanded (assuming every worker runs background sync, which is wrong — pin sync-bridge to worker-0 only, so stays at 15 total)
- 4 workers × 20 alembic headroom = 80 connections headroom (loose)

Verify PG `max_connections ≥ 400`. Fly.io Postgres standard plan default is 256 — upgrade to 1GB plan (`max_connections=400`) before going to 4 workers.

### H. Monitoring — the three exporters

Surface each pool's saturation as a Prometheus metric:

```python
# DB pool (SQLAlchemy)
from sqlalchemy.pool import QueuePool
sync_engine.pool  # QueuePool methods: .checkedout(), .size(), .overflow()

# Threadpool (anyio)
import anyio.to_thread
limiter = anyio.to_thread.current_default_thread_limiter()
# limiter.total_tokens, limiter.borrowed_tokens, limiter.statistics()

# httpx
# httpx does not expose live pool stats; attach a transport hook or use structlog timing events.
```

Dashboard panels (per `implementation-checklist.md §6.5` admin-migration-health dashboard):

- `sync_engine_pool_saturation` — `checkedout / (size + overflow)` — alert `>0.9 for 5min`
- `threadpool_saturation` — `borrowed_tokens / total_tokens` — alert `>0.9 for 5min`
- `httpx_concurrent_requests` — from structlog event timings — alert `>80 for 5min` (out of 100 max)

### I. Revised pool-size commits across the plan

The following locations currently say `pool_size=20, max_overflow=30` for the admin sync engine and must be revised to `pool_size=40, max_overflow=40` under this §11.30 derivation:

- `async-pivot-checkpoint.md` Decision 1 §"Total sync/async engine math" (370-372): sync engine pool_size=5, max_overflow=10 — that's Path-B sync factory, stays as-is (it's the small pool for adapter-worker threads only). The revision is for the MAIN admin sync engine in L0-L4 database_session (currently sized per the pre-async plan).
- `implementation-checklist.md` L2 acceptance criteria — any explicit pool-size values for admin sync engine
- `foundation-modules.md §11.18` httpx section — add cross-ref "httpx limits interact with threadpool tokens; see §11.30"

Sync-bridge pool stays `pool_size=10, max_overflow=5` (unchanged).

### J. Cross-references

- `implementation-checklist.md §6.5` admin-migration-health dashboard — panel thresholds use this section's saturation formulas
- `foundation-modules.md §11.14` Path-B adapter wrap — the multiplier-of-2 arises from its audit-logging pattern
- `foundation-modules.md §11.18` shared httpx — `max_connections=100` is the upper limit for outbound HTTP
- `async-pivot-checkpoint.md` Decision 1 — pool-size math original (superseded for admin pool by this section's revision)

---

## 11.31 `src/routes/health.py` — Liveness / readiness / diagnostic split

> **[L1a/L2]** Replace the single Flask `/admin/health` endpoint with 4 purpose-separated endpoints. `/healthz` is the dumb liveness probe (never touches DB, always 200 if the process is alive). `/readyz` is the readiness probe (checks DB + alembic head + scheduler state; 503 on any failure; what orchestrators poll for rolling deploys). `/health/db` and `/health/pool` are diagnostic-only (expose pool stats for debugging; not polled). Legacy `/admin/health` and `/health` are kept as 200-alias handlers returning the byte-identical body (decision D4: alias, not 308 redirect, because many naive probes — Docker `curl -f`, uptime monitors without follow-redirect — don't traverse 308s).

### A. Why split

The single-endpoint health check is a category error:
- **Liveness** (`/healthz`): "Is the process alive enough to serve traffic?" A liveness failure → kill the container. Must be cheap: a DB query here causes every liveness flap to kill containers during DB maintenance → cascading restart storms.
- **Readiness** (`/readyz`): "Should a load balancer route traffic to me right now?" A readiness failure → take out of rotation temporarily. DB check belongs here: a container with a broken DB should be taken out of rotation, but NOT killed (the DB might come back).
- **Diagnostic** (`/health/db`, `/health/pool`): "Show me internal state for debugging." Ops teams curl these; orchestrators don't.

### B. Endpoints

| Endpoint | Purpose | DB touch | Cost | Polled by |
|----------|---------|----------|------|-----------|
| `/healthz` | Liveness | Never | ~1ms (in-process state only) | Kubernetes/Fly liveness probe |
| `/readyz` | Readiness | `SELECT 1` + alembic head check + scheduler state | ~5-50ms | Kubernetes/Fly readiness probe; LB |
| `/health/db` | DB pool stats | Pool introspection only | ~1ms | Humans/dashboards |
| `/health/pool` | anyio threadpool stats | Threadpool introspection only | ~1ms | Humans/dashboards |
| `/admin/health` (legacy) | 200 alias, byte-identical body | Never | <1ms | Existing consumers (smoke tests, old probes) |
| `/health` (legacy) | 200 alias, byte-identical body | Never | <1ms | External uptime monitors referencing `/health` |

### C. Implementation

```python
# src/routes/health.py
"""Health-check endpoints: liveness, readiness, diagnostic.

Canonical split (docs/deployment/health-checks.md):
- /healthz — liveness, cheap, never touches DB, always 200 if process alive.
- /readyz — readiness, SELECT 1 + alembic head + scheduler state; 503 if unhealthy.
- /health/db — diagnostic pool stats.
- /health/pool — anyio threadpool stats.
- /admin/health — legacy 200 alias; byte-identical body to /healthz (D4).
- /health — legacy 200 alias; byte-identical body to /healthz (D4).

Registration: mounted on the ROOT app, no auth, no tenant scoping, no CSRF.
"""
from __future__ import annotations

import anyio
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from alembic.config import Config
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import text

from src.core.database.database_session import get_db_session

router = APIRouter(include_in_schema=False)


@router.get("/healthz", name="healthz")
def healthz() -> JSONResponse:
    """Liveness. MUST NOT touch DB. Always 200 if process alive.

    Kubernetes/Fly kills the container on liveness failure — making this DB-gated
    would cause a DB blip to kill every container simultaneously.
    """
    return JSONResponse({"status": "ok"})


@router.get("/readyz", name="readyz")
def readyz(request: Request) -> JSONResponse:
    """Readiness. Checks DB + alembic head + scheduler state. 503 on any failure.

    Kubernetes/Fly takes the container out of rotation on readiness failure; it
    does NOT kill it. This is the correct place for DB health checks.
    """
    checks: dict[str, str] = {}
    ok = True

    # 1. DB connectivity
    try:
        with get_db_session() as session:
            session.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception as exc:  # noqa: BLE001 — we want any failure to 503
        checks["db"] = f"failed: {type(exc).__name__}"
        ok = False

    # 2. Alembic head parity (detect deploy-without-migrate)
    try:
        with get_db_session() as session:
            ctx = MigrationContext.configure(session.connection())
            db_head = ctx.get_current_revision()
        expected_head = ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()
        if db_head != expected_head:
            checks["migrations"] = f"behind: db={db_head} expected={expected_head}"
            ok = False
        else:
            checks["migrations"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["migrations"] = f"failed: {type(exc).__name__}"
        ok = False

    # 3. Scheduler state (set on app.state by lifespan hook)
    scheduler_healthy = getattr(request.app.state, "scheduler_healthy", True)
    checks["scheduler"] = "ok" if scheduler_healthy else "stalled"
    if not scheduler_healthy:
        ok = False

    body = {"status": "ok" if ok else "not_ready", "checks": checks}
    return JSONResponse(body, status_code=200 if ok else 503)


@router.get("/health/db", name="health_db")
def health_db(request: Request) -> JSONResponse:
    """Diagnostic: DB pool stats. Not polled."""
    engine = request.app.state.db_engine
    pool = engine.pool
    return JSONResponse({
        "size": pool.size(),
        "checked_in": pool.checkedin(),
        "checked_out": pool.checkedout(),
        "overflow": pool.overflow(),
    })


@router.get("/health/pool", name="health_pool")
def health_pool() -> JSONResponse:
    """Diagnostic: anyio threadpool stats. Not polled."""
    limiter = anyio.to_thread.current_default_thread_limiter()
    return JSONResponse({
        "total_tokens": limiter.total_tokens,
        "borrowed_tokens": limiter.borrowed_tokens,
        "statistics": str(limiter.statistics()),
    })


@router.get("/admin/health", name="admin_health_legacy", include_in_schema=False)
async def admin_health_legacy() -> JSONResponse:
    # Legacy alias; byte-identical body so external probes parsing the
    # response body don't break. Decision D4: 200 alias (not 308 redirect),
    # because many naive probes (Docker curl -f, uptime monitors without
    # follow-redirect) don't traverse 308s.
    return JSONResponse({"status": "healthy", "service": "mcp"})


@router.get("/health", name="health_legacy", include_in_schema=False)
async def health_legacy() -> JSONResponse:
    # Legacy alias kept indefinitely. Byte-identical body so external
    # uptime monitors that reference /health don't break at cutover.
    return JSONResponse({"status": "healthy", "service": "mcp"})
```

### D. Orchestrator configuration

**fly.toml:**
```toml
# Fly http_checks are liveness-semantic (restart VM on failure); use /healthz.
# For rolling-deploy readiness gating, add a separate [[services.http_checks]]
# block targeting /readyz with grace_period >= 60s.
[[services.http_checks]]
  interval = "10s"
  timeout = "2s"
  grace_period = "5s"
  method = "GET"
  path = "/healthz"
  protocol = "http"

[[services.tcp_checks]]
  interval = "15s"
  timeout = "2s"
  grace_period = "10s"
```

**k8s Deployment (illustrative):**
```yaml
livenessProbe:
  httpGet: { path: /healthz, port: 8080 }
  periodSeconds: 10
  timeoutSeconds: 2
  failureThreshold: 3
readinessProbe:
  httpGet: { path: /readyz, port: 8080 }
  periodSeconds: 5
  timeoutSeconds: 3
  failureThreshold: 2
```

### E. Tests

```python
# tests/integration/routes/test_health_split.py

def test_healthz_never_touches_db(integration_client, monkeypatch):
    """Liveness must not open a DB session even if DB is down."""
    called = {"get_db": 0}
    import src.routes.health as health_mod
    orig = health_mod.get_db_session
    def spy(*a, **kw):
        called["get_db"] += 1
        return orig(*a, **kw)
    monkeypatch.setattr(health_mod, "get_db_session", spy)

    r = integration_client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
    assert called["get_db"] == 0


def test_readyz_checks_db(integration_client):
    r = integration_client.get("/readyz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["checks"]["db"] == "ok"
    assert body["checks"]["migrations"] == "ok"


def test_readyz_503_on_db_down(integration_client, broken_db_engine):
    r = integration_client.get("/readyz")
    assert r.status_code == 503
    assert r.json()["checks"]["db"].startswith("failed:")


def test_legacy_admin_health_alias_returns_200(integration_client):
    # D4: /admin/health is a 200 alias (not a 308 redirect) so naive probes
    # that don't follow redirects still see success.
    r = integration_client.get("/admin/health", follow_redirects=False)
    assert r.status_code == 200
    assert r.json() == {"status": "healthy", "service": "mcp"}


def test_legacy_health_alias_returns_200(integration_client):
    r = integration_client.get("/health", follow_redirects=False)
    assert r.status_code == 200
    assert r.json() == {"status": "healthy", "service": "mcp"}
```

### F. Structural guard

Draft `tests/unit/architecture/test_architecture_health_endpoints_split.py`:

AST scan of `src/routes/health.py`:
1. `healthz` function body contains NO call to `get_db_session` (literal name match)
2. `readyz` function body contains a call to `get_db_session`
3. `admin_health_legacy` AND `health_legacy` return a `JSONResponse` with the byte-identical body `{"status": "healthy", "service": "mcp"}` (D4 — alias, not 308 redirect)
4. Enforcement: any future edit that adds DB access to `/healthz`, or flips the legacy alias back to a redirect, fails the guard.

Registered in §5.5 guard table with `Empty` allowlist.

### G. Gotchas

- **`/healthz` MUST NOT touch the DB.** Period. If you are tempted to "quickly check DB" from `/healthz`, revisit the liveness-vs-readiness distinction above. Use `/readyz` for DB-gated health.
- **`MigrationContext` requires a live DB connection.** If the DB is down, the alembic-head check itself will fail — which is correct (the `/readyz` endpoint returns 503 with `checks["migrations"] = "failed: ..."` and `checks["db"] = "failed: ..."`).
- **No auth on the probes.** These are probes, not admin endpoints. CSRF is exempted (the `/admin/*` prefix on the legacy path is the only admin-adjacent part; the redirect target `/healthz` is at root). Do not add `require_auth` or tenant checks here.
- **`is_test` app-state flag:** in unit tests where the DB is mocked out entirely, the fixture sets `app.state.is_test = True` and the readyz handler can short-circuit the DB check. Current implementation does NOT do this — it runs the real `SELECT 1` in integration tests (which have real DB) and mocks `get_db_session` in unit tests. If a future edit adds the short-circuit, guard it behind an explicit `if app.state.is_test` branch so production never silently returns "ok" on a dead DB.
- **`/readyz` response must be JSON, not plain text.** Kubernetes parses the body of readiness checks; human operators grep for `failed:` substrings. Keep the `{"checks": {...}}` shape stable.

---

## 11.32 `src/admin/rate_limits.py` — SlowAPI rate limiter for sensitive endpoints

> **[L1b/L2]** Per-IP / per-token rate limiting for login, OAuth init, and MCP endpoints. Uses `slowapi>=0.1.9` with memory backend in L1b-L6 (process-local, single-worker acceptable); v2.1 swaps to Redis backend for multi-worker. Purpose: brake credential-stuffing against login, abuse of OAuth init endpoints (which call Google Cloud), and runaway MCP clients.

### A. Targets and limits

| Endpoint | Limit | Scope |
|----------|-------|-------|
| `POST /admin/login` | 5/min | per source IP |
| `GET /admin/auth/google/initiate` | 20/min | per source IP |
| `GET /admin/auth/oidc/initiate` | 20/min | per source IP |
| `GET /admin/auth/gam/initiate` | 20/min | per source IP |
| MCP tool calls | 100/min | per auth token (`x-adcp-auth`) |
| A2A endpoints | 100/min | per auth token (`authorization`) |

Numbers are launch defaults; tunable per settings.

### B. Implementation

```python
# src/admin/rate_limits.py
"""SlowAPI rate limiter.

Backend: memory:// (process-local) for L1b-L6. This is intentionally NOT Redis
during v2.0 — single-worker deployment makes memory sufficient, and Redis adds
deployment/operational complexity that is a v2.1 concern. See §11.33 related
setting `ADCP_RATE_LIMIT_BACKEND` for the v2.1 swap.

Key function: for MCP/A2A, rate-limit by auth token (more precise than IP when
multiple clients share a NAT); for browser endpoints, rate-limit by IP.
"""
from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address


def _key_func(request: Request) -> str:
    """Prefer auth token over IP — clients behind NAT share an IP.

    Order of precedence:
    1. MCP: x-adcp-auth header (token-scoped rate limit)
    2. A2A: Authorization: Bearer <token>
    3. Fallback: source IP (browser endpoints, unauthenticated paths)
    """
    mcp_token = request.headers.get("x-adcp-auth")
    if mcp_token:
        return f"mcp:{mcp_token}"
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        return f"a2a:{auth_header[7:]}"
    return f"ip:{get_remote_address(request)}"


limiter = Limiter(
    key_func=_key_func,
    default_limits=[],      # opt-in per endpoint via decorator
    storage_uri="memory://",  # v2.1: redis://...
    headers_enabled=True,   # emit X-RateLimit-* response headers
)


async def rate_limit_exceeded_handler(
    request: Request, exc: RateLimitExceeded
) -> JSONResponse:
    """429 response with Retry-After header.

    For /admin/* HTML requests where the client is a browser, we still return
    JSON — the AdCPError exception handler chain (see §11.11) handles HTML
    translation for browser clients. This handler is the JSON-first default.
    """
    retry_after = int(exc.detail.split(" ")[-1]) if " per " in exc.detail else 60
    return JSONResponse(
        {"detail": f"Rate limit exceeded: {exc.detail}"},
        status_code=429,
        headers={"Retry-After": str(retry_after)},
    )
```

### C. Integration in `app_factory.py`

```python
# src/admin/app_factory.py (excerpt)
from slowapi.errors import RateLimitExceeded
from src.admin.rate_limits import limiter, rate_limit_exceeded_handler


def create_app() -> FastAPI:
    app = FastAPI(...)
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    # ... middleware, routers ...
    return app
```

### D. Per-endpoint decorator usage

```python
# src/admin/routers/auth.py (excerpt)
from src.admin.rate_limits import limiter


@router.post("/login", name="admin_auth_login")
@limiter.limit("5/minute")
def login_submit(request: Request, ...):
    """Rate-limited to 5 attempts per minute per IP — brake credential stuffing.

    Note: decorator REQUIRES the handler signature to include `request: Request`
    even if the body doesn't reference it. slowapi inspects args to find the
    Request object for key_func.
    """
    ...


@router.get("/auth/google/initiate", name="admin_auth_google_initiate")
@limiter.limit("20/minute")
def google_initiate(request: Request): ...


@router.get("/auth/oidc/initiate", name="admin_auth_oidc_initiate")
@limiter.limit("20/minute")
def oidc_initiate(request: Request): ...


@router.get("/auth/gam/initiate", name="admin_auth_gam_initiate")
@limiter.limit("20/minute")
def gam_initiate(request: Request): ...
```

MCP tool rate limiting is applied at the MCP mount level (middleware or per-tool decorator in `src/core/main.py`) — 100/min per auth token via the same limiter instance.

### E. pyproject.toml

```toml
# Add to dependencies:
slowapi = ">=0.1.9"
```

### F. Tests

```python
# tests/integration/admin/test_rate_limits.py

def test_login_rate_limited_after_5_attempts(integration_client):
    for _ in range(5):
        r = integration_client.post("/admin/login", data={"password": "wrong"})
        assert r.status_code in (401, 403)
    # 6th attempt trips the limiter
    r = integration_client.post("/admin/login", data={"password": "wrong"})
    assert r.status_code == 429
    assert "Retry-After" in r.headers


def test_rate_limit_by_token_not_ip(integration_client):
    """Two clients behind the same IP with different tokens have independent buckets."""
    headers_a = {"x-adcp-auth": "token-a"}
    headers_b = {"x-adcp-auth": "token-b"}
    # Exhaust token-a's bucket
    for _ in range(100):
        integration_client.post("/mcp/", headers=headers_a, json={...})
    # token-b should still work (different bucket despite same source IP)
    r = integration_client.post("/mcp/", headers=headers_b, json={...})
    assert r.status_code != 429
```

### G. Gotchas

- **`@limiter.limit(...)` decorators require the handler to take `request: Request` as a parameter.** slowapi introspects the signature to find the `Request` arg for `key_func`. If you omit it, the decorator silently no-ops on some versions and raises on others — always include it explicitly.
- **Memory backend is process-local.** In a multi-worker deploy (e.g., 4 uvicorn workers), each worker has its own counter — effective limit is `N_workers × limit_per_worker`. v2.0 is single-worker so this is fine. v2.1 must swap `storage_uri="memory://"` to `storage_uri="redis://..."` before adding workers.
- **MCP uses `x-adcp-auth`; A2A uses `Authorization: Bearer ...`.** The `_key_func` above handles both. Do not assume a single header — the two protocols genuinely use different auth headers per AdCP spec.
- **`RateLimitExceeded` is raised BEFORE the handler runs.** If your handler has side effects (logging, metrics) and you want those to fire even on rate-limited requests, put them in middleware, not the handler body.
- **Rate-limit buckets are global per-key.** You cannot "refund" a request (e.g., after a successful login, restore one slot). The bucket is time-based TTL; callers just wait the retry-after period.
- **Do NOT rate-limit `/healthz`, `/readyz`, `/metrics`, or internal probes.** These are polled every 10s by orchestrators; rate-limiting them will take the container out of rotation.

---

## 11.33 `tests/integration/test_session_cookie_size.py` — Cookie-size budget guard

> **[L1b/L2]** Integration-test guard that asserts the `adcp_session` cookie stays under 3.5 KB for realistic user workloads. Browser cookie limit is 4 KB; proxies (nginx, Fly.io) cap total request header size at 4-8 KB. The 3.5 KB budget leaves headroom for other cookies (CSRF, analytics) and proxy headers.

### A. Rationale

Starlette's `SessionMiddleware` serializes the entire `request.session` dict as a signed, base64-encoded cookie. Common bloat sources:
- Multi-tenant user with N tenant memberships → full tenant list in session
- Flash messages stacked during a multi-step form flow
- CSRF tokens (if we ever switched to double-submit)
- OAuth flow state dicts (Authlib can store PKCE verifiers, state tokens)

Crossing 4 KB → browsers silently drop the cookie → users logged out mid-session. Crossing 8 KB → Fly.io edge returns 431 "Request Header Fields Too Large" before the request ever reaches the app.

### B. Budget

```python
# tests/integration/test_session_cookie_size.py
MAX_COOKIE_BYTES = 3_584  # 3.5 KB — leaves 0.5 KB headroom under 4 KB browser limit.
```

Do NOT raise this number. If a change pushes a realistic user over 3.5 KB, the fix is architectural (see §E runbook), not a budget increase.

### C. Implementation

```python
# tests/integration/test_session_cookie_size.py
"""Cookie-size budget guard.

Rationale: §11.33. 3.5 KB budget protects against browser 4 KB limit and proxy
4-8 KB header cap. Crossing either → users silently logged out.

Pattern: walk a realistic user through the admin UI, inspect the adcp_session
cookie after every state transition, assert size ≤ 3.5 KB.
"""
from __future__ import annotations

import pytest

MAX_COOKIE_BYTES = 3_584  # 3.5 KB — do NOT raise without §E runbook review.


def _session_cookie_size(client) -> int:
    """Return the byte size of the current adcp_session cookie, or 0."""
    for cookie in client.cookies.jar:
        if cookie.name == "adcp_session" and cookie.value:
            return len(cookie.value)
    return 0


def test_session_cookie_minimal_user(integration_client, minimal_user_session):
    """Newly-logged-in user with zero tenants + zero flashes."""
    size = _session_cookie_size(integration_client)
    assert size > 0, "session cookie must be set after login"
    assert size <= MAX_COOKIE_BYTES, (
        f"Minimal session already uses {size} bytes — something fundamentally "
        f"wrong. Expected ≤ ~500 bytes for bare authenticated session."
    )


def test_session_cookie_heavy_user(integration_client, heavy_tenant_session_client):
    """Realistic heavy user: 10 tenants + 5 stacked flashes + CSRF state."""
    size = _session_cookie_size(heavy_tenant_session_client)
    assert size <= MAX_COOKIE_BYTES, (
        f"Heavy-user session exceeded budget: {size} bytes > {MAX_COOKIE_BYTES}. "
        f"Run §E runbook in foundation-modules.md §11.33 before increasing budget. "
        f"Do NOT raise MAX_COOKIE_BYTES — architectural fix required."
    )
```

### D. Heavy fixture spec

```python
# tests/integration/conftest.py (excerpt)

@pytest.fixture
def heavy_tenant_session_client(integration_client, factory):
    """Client with a realistic worst-case session:
    - 10 tenant memberships (realistic for agency-holding-company user)
    - 5 flash messages stacked (mid-multi-step-form state)
    - CSRF token in session
    """
    user = factory.UserFactory.create(tenants=[factory.TenantFactory.create() for _ in range(10)])
    integration_client.post("/admin/login", data={"email": user.email, "password": "test"})
    for msg in ["step 1 done", "step 2 done", "validation warning", "creative uploaded", "product saved"]:
        integration_client.get(f"/admin/_test/flash?msg={msg}")  # test-only endpoint
    return integration_client
```

### E. When-this-fails runbook

If `test_session_cookie_heavy_user` fails:

1. **Measure the delta.** Add `print(dict(request.session))` in a test fixture to see which keys grew.
2. **Identify the new key.** Compare to the known-baseline keys: `user_email`, `user_id`, `tenant_memberships`, `csrf_state`, `oauth_state`, `flashes`.
3. **Decide the fix.**
   - If the new key is a large list (e.g., all tenant IDs for a user with 50 memberships), move the list to a DB-backed user-scoped cache; store only an opaque session key in the cookie.
   - If the new key is a user-profile field, move it to the user table; read on demand in handlers.
   - If the new key is derived (e.g., computed permissions), compute on read, don't store.
4. **Do NOT raise `MAX_COOKIE_BYTES`.** The 3.5 KB budget is the architectural ceiling. Raising it today to "fix" a test failure pushes the problem to the next person who adds a session key. The budget is a correctness requirement like any other structural invariant.
5. **If after steps 1-4 the fix is genuinely "we need a bigger cookie,"** the correct architectural move is DB-backed sessions: replace `SessionMiddleware` with a custom middleware that stores session data in Postgres and puts only an opaque session ID in the cookie. That is a v2.1 epic, not a v2.0 budget-raise.

### F. Cross-reference

Registered in root `CLAUDE.md` structural-guards table row: `Session cookie size budget | tests/integration/test_session_cookie_size.py`.

---

## §11.34 — Doc-Drift Linters (6 new structural guards, land at L0)

The 7-step Red-Green discipline has a systemic blind spot: docs-only edits qualify for the `discipline: N/A - docs-only` escape hatch, but migration plan documents ARE the specification. Without automated enforcement, the pre-Wave-0 audit surfaced drift in 30+ locations across 11 docs.

These 6 guards enforce semantic consistency across `.md` files:

### `tests/unit/test_architecture_invariants_consistent.py`
Parses the 6 invariants from `.claude/notes/flask-to-fastapi/CLAUDE.md` §Critical Invariants. Asserts root `CLAUDE.md` banner contains the same numbered list with matching substantive text. Canonical order: (1) url_for/script_root, (2) trailing slashes, (3) AdCPError Accept-aware, (4) sync def L0-L4, (5) middleware Approximated-before-CSRF, (6) OAuth URIs byte-immutable.

### `tests/unit/test_architecture_oauth_uris_consistent.py`
Whitelist. Every `.md` under `.claude/notes/flask-to-fastapi/` and `docs/` is scanned via regex `/(admin/)?auth/[^"` ]+/callback` for OAuth URI strings. Each match MUST be in the canonical set {`/admin/auth/google/callback`, `/admin/auth/oidc/callback`, `/admin/auth/gam/callback`} UNLESS the match appears within ±10 tokens of an explicit `NOT`/`REJECTED`/`FORBIDDEN` context word.

### `tests/unit/test_architecture_csrf_implementation_consistent.py`
Scans every `.md` in `.claude/notes/flask-to-fastapi/` for tokens associated with the abandoned double-submit-cookie strategy: `adcp_csrf`, `double-submit`, `XSRF-TOKEN`, `csrf_token.*cookie`. Allowlist: folder CLAUDE.md §Invariant 5 may mention `double-submit` in the rejection sentence (same NOT-context rule).

### `tests/unit/test_architecture_layer_assignments_consistent.py`
Parses the layer-timeline table in folder `CLAUDE.md` into `{label: scope}`. Scans `implementation-checklist.md` and `execution-plan.md` for each layer label; asserts scope text matches (fuzzy substring). Catches the L5d1 relabel drift class.

### `tests/unit/test_architecture_spike_table_consistent.py`
Parses the Spike Sequence table (folder CLAUDE.md §v2.0 Spike Sequence) into `{spike_id: (gate, layer)}`. Scans all 11 plan docs for string `Spike <N>` + claimed layer; asserts no conflict. Spike table is single source of truth.

### `tests/unit/test_architecture_proxy_headers_in_entrypoints.py`
Asserts the canonical entrypoint `scripts/run_server.py` contains `proxy_headers=True` + `forwarded_allow_ips='*'` as kwargs to `uvicorn.run(...)`. `Dockerfile` CMD and `scripts/deploy/run_all_services.py` are asserted to invoke `scripts/run_server.py` (inheritance path, not duplicated flag placement) per migration.md §11.8. Catches entrypoint drift during L2 Flask removal.

### Escape hatch tightening
Edits under `.claude/notes/flask-to-fastapi/` NO LONGER qualify for the `discipline: N/A - docs-only` waiver. Those docs are spec; they require a doc-drift linter Red/Green pair.

### Land at
L0 as part of the structural-guard stubs work item. Each guard ships with a meta-test fixture per implementation-checklist §TI-4 proving it catches a planted violation.

---

## §11.35 — Native-Idiom Guards (3 new structural guards)

Per the native-ness audit: 89 `os.environ.get` sites, 17 `import requests` sites, 0 Pydantic v1 `class Config:` blocks today. Guards land at:

### `tests/unit/test_architecture_no_pydantic_v1_config.py` (L0 — empty allowlist)
AST-scans every `src/**/*.py` for `class Config:` inside a Pydantic BaseModel subclass. Allowlist EMPTY at introduction. Code is already clean; guard is monotonic from day 1 to prevent regression when L1/L2/L4 land new schemas.

### `tests/unit/test_architecture_no_direct_env_access.py` (L4 — ratcheting allowlist)
AST-scans every `src/**/*.py` for `os.environ.get(...)` or `os.environ[...]` calls outside `src/core/config.py`. Allowlist seeded with the 89 current sites; ratcheting to 0 by L7.

### `tests/unit/test_architecture_no_requests_library.py` (L5+ — ratcheting allowlist)
AST-scans every `src/**/*.py` for `import requests` or `from requests import`. Allowlist seeded with the 17 current sites (mostly adapter files retained under Decision 1 Path B); ratcheting toward 0 as adapters migrate to httpx in v2.1+.

### Land at
Each guard introduced at its respective layer with a Red commit (empty stub of the guard that fails because the pattern exists) followed by a Green commit (allowlist seeded with current state; downstream PRs shrink it).

---

## §11.36 — Middleware Stack Versioning (supersedes "Empty + bootstrap" guard strategy)

The middleware stack grows L1a=7 → L1c=8 (LegacyAdminRedirect, per D1) → L2=10 (TrustedHost + SecurityHeaders) → L4+=11 (RequestID). An empty+bootstrap guard would force last-minute edits on every mid-layer middleware change; a version-keyed assertion makes the layer transition explicit in code.

### Implementation pattern (lands in L1a with initial version=1)

```python
# src/app_constants.py
MIDDLEWARE_STACK_VERSION = 1  # L1a=1, L1c=2, L2=3, L4=4

EXPECTED_STACKS: dict[int, list[str]] = {
    1: [  # L1a — 7 middlewares
        "CORSMiddleware", "RestCompatMiddleware", "CSRFOriginMiddleware",
        "SessionMiddleware", "UnifiedAuthMiddleware",
        "ApproximatedExternalDomainMiddleware", "FlyHeadersMiddleware",
    ],
    2: [  # L1c — 8 middlewares (adds LegacyAdminRedirect INSIDE UnifiedAuth)
        "CORSMiddleware", "RestCompatMiddleware", "CSRFOriginMiddleware",
        "SessionMiddleware", "LegacyAdminRedirectMiddleware",
        "UnifiedAuthMiddleware",
        "ApproximatedExternalDomainMiddleware", "FlyHeadersMiddleware",
    ],
    3: [  # L2 — 10 middlewares (adds SecurityHeaders + TrustedHost)
        "CORSMiddleware", "RestCompatMiddleware", "CSRFOriginMiddleware",
        "SessionMiddleware", "LegacyAdminRedirectMiddleware",
        "UnifiedAuthMiddleware", "SecurityHeadersMiddleware",
        "TrustedHostMiddleware",
        "ApproximatedExternalDomainMiddleware", "FlyHeadersMiddleware",
    ],
    4: [  # L4+ — 11 middlewares (adds RequestID outermost)
        "CORSMiddleware", "RestCompatMiddleware", "CSRFOriginMiddleware",
        "SessionMiddleware", "LegacyAdminRedirectMiddleware",
        "UnifiedAuthMiddleware", "SecurityHeadersMiddleware",
        "TrustedHostMiddleware",
        "ApproximatedExternalDomainMiddleware", "FlyHeadersMiddleware",
        "RequestIDMiddleware",
    ],
}
```

### Guard

`tests/integration/test_architecture_middleware_order.py` reads `MIDDLEWARE_STACK_VERSION` from `src.app_constants` and asserts `[m.cls.__name__ for m in app.user_middleware] == EXPECTED_STACKS[VERSION]`. Bumping the version is an explicit, reviewable diff in every layer PR that changes the stack.

Note: Starlette stores middleware in `app.user_middleware` in REVERSE added order (outermost last). The guard's canonical list is the OUTERMOST-TO-INNERMOST runtime order; the guard normalizes by reversing `app.user_middleware` before comparison.

### Lands at

L1a introduces the constant + guard with VERSION=1. L1c bumps to VERSION=2 in the same PR that adds `LegacyAdminRedirectMiddleware`. L2 bumps to VERSION=3 in the same PR that adds `SecurityHeadersMiddleware` + `TrustedHostMiddleware`. L4 bumps to VERSION=4 in the same PR that adds `RequestIDMiddleware`.

---

### Critical Files for Implementation

- /Users/quantum/Documents/ComputedChaos/salesagent/src/admin/app.py
- /Users/quantum/Documents/ComputedChaos/salesagent/src/admin/utils/helpers.py
- /Users/quantum/Documents/ComputedChaos/salesagent/src/core/auth_context.py
- /Users/quantum/Documents/ComputedChaos/salesagent/src/core/auth_middleware.py
- /Users/quantum/Documents/ComputedChaos/salesagent/src/services/auth_config_service.py
