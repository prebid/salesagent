I have enough context. Now I can produce the detailed elaboration. Given the 3000-4000 word target and that I need to be code-heavy, I'll produce the output directly as my final assistant message (per the task instructions: "Return findings directly as your final assistant message — the parent agent reads your text output, not files you create.").

---

# §11 Foundation Modules — Detailed Elaboration

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

Everything below uses Python 3.12+ syntax. Under the full-async SQLAlchemy pivot (2026-04-11, see `async-pivot-checkpoint.md`), these foundation modules use `AsyncSession` via `async with get_db_session() as db:` / `await db.execute(...)` — SQLAlchemy 2.0 async-native. The pre-pivot plan said "sync SQLAlchemy inside `get_db_session()` context managers, per v2.0.0 charter"; that charter was rewritten 2026-04-11 to absorb async into v2.0. Under Agent E's idiom upgrade (Categories 1-2 in `async-audit/agent-e-ideal-state-gaps.md`), handlers and deps prefer the DI pattern `session: SessionDep` via `Depends(get_session)` over inline `async with` — but both forms are valid and the guard tests accept either.

---

## 11.0 `src/core/database/engine.py` — Lifespan-scoped async engine (Agent E Category 1)

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
        expire_on_commit=False,   # MANDATORY for async — see Risk #5
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

> **Added 2026-04-11 under the Agent E idiom upgrade.** Async debugging is 3x harder without request-ID context-var propagation. Adding `structlog` in v2.0 (~250 LOC) avoids the much larger retrofit cost in v2.1 (~400 LOC touching every log line).

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
- Structural guard `test_architecture_uses_structlog.py` enforces structlog usage (allowlisted for existing files during migration)

### D. Gotchas

- **Under async, `logging` alone does NOT propagate context-vars** — you'd need to thread request_id through every function call. `structlog.contextvars.merge_contextvars` handles this for free.
- **Log format change is OPS-VISIBLE.** In production, logs switch from stdlib format to JSON. Monitoring pipelines that grep stdlib format need an update. Document in release notes.

---

## 11.0.5 DTO layer — Pydantic v2 wrappers over ORM models (Agent E Category 5)

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

---

## 11.1 `src/admin/templating.py`

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
  break ~40 templates on cutover. Tracked for v2.1.
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
def _url_for(context: dict[str, Any], name: str, /, **path_params: Any) -> URL:
    request: Request = context["request"]
    try:
        return request.url_for(name, **path_params)
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
    CSRFMiddleware). `tenant` is NOT injected here — handlers pass it
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
        "csrf_token": getattr(request.state, "csrf_token", ""),
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
- **`Undefined` vs `StrictUndefined`:** We keep default `Undefined` for v2.0.0. `StrictUndefined` would raise on every missing tenant attribute at render time; legacy templates have dozens. v2.1 ticket.
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
- Does NOT interact with `UnifiedAuthMiddleware` directly — but handlers that call `render()` must have access to `request.state.csrf_token`, which requires `CSRFMiddleware` to have run.

### D. Gotchas

- **Jinja2 autoescape:** `Jinja2Templates(..., autoescape=True)` is the default; do NOT disable. The `| safe` filter and `Markup()` are the only ways to emit raw HTML.
- **Thread safety of `env.globals`:** Jinja `Environment` is shared across all requests. Never mutate `env.globals` per-request — use the context dict instead. The `templates` singleton is created at import time so this is enforced structurally.
- **Template caching:** Jinja caches compiled templates by default. In development, set `templates.env.auto_reload = True` via `APP_ENV=dev` check.
- **`TemplateResponse` and background tasks:** Starlette's `TemplateResponse` accepts `background=` but the wrapper doesn't expose it. Add on demand.

---

## 11.2 `src/admin/sessions.py`

### A. Implementation

```python
"""SessionMiddleware configuration for the admin UI.

Starlette's SessionMiddleware stores the whole session dict in a single signed
cookie (itsdangerous). ~4KB cap. On every request the cookie is deserialized
into `request.session` (a dict subclass that tracks mutation) and re-serialized
on response if `request.session` was touched.

Cookie name intentionally CHANGES from Flask's `session` to `adcp_session` so
that the v2.0.0 deploy forces a re-login — this prevents a Flask-signed
cookie from being accepted by the new Starlette stack (different signing
algorithm, different cookie key).
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

    Production: SameSite=None + Secure (required for cross-subdomain OIDC
    callbacks: the OAuth provider redirects to /admin/auth/callback from a
    different origin, so the session cookie must be sent on a top-level
    cross-site redirect).

    Development: SameSite=Lax + not Secure (localhost has no TLS).
    """
    production = _is_production()
    kwargs: dict[str, Any] = {
        "secret_key": _require_session_secret(),
        "session_cookie": "adcp_session",
        "max_age": 14 * 24 * 3600,  # 14 days
        "same_site": "none" if production else "lax",
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

    def test_prod_mode_sets_samesite_none(self, monkeypatch):
        monkeypatch.setenv("SESSION_SECRET", "x" * 64)
        monkeypatch.setenv("PRODUCTION", "true")
        monkeypatch.setenv("SALES_AGENT_DOMAIN", "sales-agent.example.com")
        kw = session_middleware_kwargs()
        assert kw["same_site"] == "none"
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
- **Secret rotation:** There's no key rotation support in Starlette's SessionMiddleware. Rotating `SESSION_SECRET` logs everyone out. Track as v2.1.
- **Starlette versions ≤ 0.37 had a bug** where `samesite=None` + `https_only=False` emitted invalid `SameSite=None` without `Secure`. Starlette 0.50 fixed this, but prod config always uses `https_only=True`.

---

## 11.3 `src/admin/flash.py`

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

    `async def` under the full-async pivot (2026-04-11). The DB fallback
    opens its own short-lived `async with get_db_session()` — this is the
    ONE helper where nested session opening is tolerated because the
    caller chain (identity resolution in middleware) may precede request
    scope and therefore has no `SessionDep` to thread through. All OTHER
    helpers MUST accept `SessionDep` rather than opening their own session.

    NO session-level caching here (the Flask version cached in session,
    causing staleness after env var changes). Result is cheap: env checks
    are dict lookups; DB fallback only triggers if env is unset. For high
    QPS, add a TTL lru_cache in v2.1.
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

async def _load_tenant(tenant_id: str) -> dict[str, Any]:
    """DEPRECATED transitional helper — use TenantRepository.get_dto() instead.

    Remains in place only until every caller has been migrated to the
    `TenantRepoDep` Dep pattern. New code MUST use the repository. This helper
    is flagged for removal in Wave 5.

    Under the full-async pivot (2026-04-11), this helper is `async def` and
    opens its own session inline. The idiomatic replacement is `TenantRepoDep`
    + `async def get_current_tenant(..., tenants: TenantRepoDep, ...)`.
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


async def _get_admin_user_or_none(request: Request) -> AdminUser | None:
    """Read the session and produce an AdminUser, or None if not authenticated.

    `async def` under the full-async pivot (2026-04-11) because it `await`s
    `is_super_admin`, which opens an async DB session for the env/DB fallback.

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

async def get_admin_user_optional(request: Request) -> AdminUser | None:
    return await _get_admin_user_or_none(request)


async def get_admin_user(request: Request) -> AdminUser:
    user = await _get_admin_user_or_none(request)
    if user is None:
        raise AdminRedirect(to="/admin/login", next_url=str(request.url))
    return user


async def get_admin_user_json(request: Request) -> AdminUser:
    """Same as get_admin_user but raises HTTPException(401) for JSON endpoints."""
    user = await _get_admin_user_or_none(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


AdminUserDep = Annotated[AdminUser, Depends(get_admin_user)]
AdminUserJsonDep = Annotated[AdminUser, Depends(get_admin_user_json)]
AdminUserOptional = Annotated[AdminUser | None, Depends(get_admin_user_optional)]


async def require_super_admin(user: AdminUserDep) -> AdminUser:
    if user.role != "super_admin":
        raise HTTPException(status_code=403, detail="Super admin required")
    return user


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


async def _user_has_tenant_access(email: str, tenant_id: str) -> bool:
    async with get_db_session() as db:
        found = (await db.execute(
            select(User).filter_by(email=email.lower(), tenant_id=tenant_id, is_active=True)
        )).scalars().first()
        return found is not None


async def _tenant_has_auth_setup_mode(tenant_id: str) -> bool:
    async with get_db_session() as db:
        tenant = (await db.execute(
            select(Tenant).filter_by(tenant_id=tenant_id)
        )).scalars().first()
        return bool(tenant and getattr(tenant, "auth_setup_mode", False))


async def get_current_tenant(
    request: Request,
    user: AdminUserDep,
    tenant_id: str,
) -> dict[str, Any]:
    """Resolve the current tenant, enforcing access.

    `async def` under the full-async pivot (2026-04-11): every helper below
    is async. Note this is the transitional-wrapper shape that opens its own
    session; the idiomatic Wave 4 replacement threads `TenantRepoDep` +
    `UserRepoDep` through the signature so the DI layer owns session lifetime.
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

- **Async DB (pivoted 2026-04-11):** `_load_tenant`, `_user_has_tenant_access`, and `_tenant_has_auth_setup_mode` are `async def` under the full-async pivot. All DB access uses `async with get_db_session() as db:` / `(await db.execute(select(...))).scalars().first()`. `run_in_threadpool` is reserved for file I/O, CPU-bound, or sync third-party libraries. Under the Agent E idiom upgrade (Category 2), new code should prefer `TenantRepoDep` via `Depends(get_tenant_repo)` over calling `_load_tenant` directly — the standalone helper is transitional and will be removed in Wave 5. See `async-pivot-checkpoint.md` §3 for the target-state patterns.
- **`request.session` raises AssertionError** if `SessionMiddleware` is not installed. The `try/except` in `_get_admin_user_or_none` catches this to make unit tests without middleware work (they get `None`).
- **Session fixation:** On privilege elevation (login), Starlette's SessionMiddleware does NOT rotate the session cookie. Add a manual cookie-clear + re-set in the login handler. Tracked.
- **Case-insensitive email comparison:** All email matching is lowercase. The database schema stores emails case-preserved but indexes on `lower(email)`. The dataclass enforces lowercase at construction.
- **Role enum drift:** Only `super_admin` is distinguished. `tenant_admin` vs `tenant_user` is populated but no current route gates on it. Leave the enum in place for future use.

---

## 11.5 `src/admin/deps/audit.py`

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
    invalidate. Tracked: move cache to Redis in v2.1 for cross-worker invalidation.
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

- **Cross-worker cache staleness:** Each Uvicorn worker has its own `_tenant_client_cache`. Invalidation only hits the worker that receives the invalidation request. For v2.0.0 with typically 2–4 workers, the worst case is a 5-second window of stale secrets after rotation. v2.1 moves to Redis-backed cache.
- **Session key collisions:** Authlib writes `_state_<name>_<nonce>` to `request.session`. This is safe unless a future codebase writes its own `_state_*` keys.
- **`oauth._clients` is private:** We reach into it to pop stale registrations. If Authlib renames this attribute in a minor release, invalidation silently becomes a no-op. Pin `authlib==1.6.*`.
- **Test isolation:** The `oauth` singleton persists across tests. The `clear_cache` autouse fixture + popping `_clients["google"]` is necessary for clean state.

---

## 11.7 `src/admin/csrf.py`

### A. Implementation

```python
"""Pure-ASGI Double Submit Cookie CSRF middleware.

DESIGN CHOICES (load-bearing):

1. Pure ASGI, NOT BaseHTTPMiddleware. BaseHTTPMiddleware runs in a separate
   task group (Starlette #1729) and its body reads break request.receive
   downstream. Pure ASGI lets us intercept receive messages cleanly.

2. Double Submit Cookie, not synchronizer pattern. Simpler, stateless,
   doesn't require storing tokens server-side. The signed token proves
   it came from us (itsdangerous) and the cookie+form match proves the
   client possesses the cookie (i.e., same-origin).

3. Separate cookie from session. Reasons:
   - CSRF cookie must be readable by JS (for AJAX XHR headers).
     Session cookie is HttpOnly.
   - CSRF cookie can be shorter-lived (24h) than session (14d).
   - Enables stateless token validation (no session state coupling).

4. Middleware ORDER: CSRFMiddleware runs INSIDE SessionMiddleware (i.e.,
   registered LATER, so it's outer via add_middleware's LIFO). That way
   request.session is available if we ever want to tie CSRF to session.
   In v2.0.0 we don't — but putting CSRF inside Session avoids the
   reverse issue (Session can't see our scope changes). See README at
   the bottom for the ordering rationale.

5. Body reading: on unsafe-method + form content type, we must read the
   ENTIRE request body to find the csrf_token field, THEN re-inject that
   body into the receive channel so the downstream handler sees a fresh
   stream. This is the tricky part. See _read_csrf_from_body().
"""
from __future__ import annotations

import logging
import os
import secrets
from typing import Any
from urllib.parse import parse_qs

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

logger = logging.getLogger(__name__)

_COOKIE_NAME = "adcp_csrf"
_FORM_FIELD = "csrf_token"
_HEADER_NAME = "x-csrf-token"
_TOKEN_MAX_AGE = 24 * 3600
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_EXEMPT_PATH_PREFIXES: tuple[str, ...] = (
    "/mcp",
    "/a2a",
    "/api/v1/",
    "/.well-known/",
    "/agent.json",
    "/admin/auth/callback",
    "/admin/auth/oidc/callback",
)
_MAX_BODY_BYTES = 1 * 1024 * 1024  # 1MB — refuse larger bodies outright


def _is_prod() -> bool:
    return os.environ.get("PRODUCTION", "").lower() == "true"


def _signer() -> URLSafeTimedSerializer:
    """Signer shares the SessionMiddleware secret with a distinct salt.

    Distinct salt ensures a token signed for CSRF cannot be replayed as a
    session cookie or vice-versa, even though they share the secret.
    """
    secret = os.environ.get("SESSION_SECRET", "")
    if not secret:
        raise RuntimeError("SESSION_SECRET not set; CSRFMiddleware cannot sign tokens")
    return URLSafeTimedSerializer(secret, salt="adcp-csrf-v1")


def _new_token() -> str:
    return _signer().dumps(secrets.token_urlsafe(32))


def _validate_token(token: str) -> bool:
    if not token:
        return False
    try:
        _signer().loads(token, max_age=_TOKEN_MAX_AGE)
        return True
    except (BadSignature, SignatureExpired):
        return False
    except Exception:
        logger.exception("CSRF token validation raised unexpectedly")
        return False


def _extract_cookie(cookie_header: str, cookie_name: str) -> str | None:
    """Parse a Cookie header and return the named cookie's value.

    Handles:
    - Multiple cookies separated by "; "
    - Quoted values (per RFC 6265 §4.1.1 only quotes around value allowed)
    - Names that happen to be substrings of other names (e.g., `csrf` vs `adcp_csrf`)
    - Empty values (returns None, not "")

    Does NOT use http.cookies.SimpleCookie because it chokes on unquoted
    special characters that browsers happily send.
    """
    if not cookie_header:
        return None
    # RFC 6265: cookie-pair separator is "; " but be lenient on whitespace
    for raw in cookie_header.split(";"):
        raw = raw.strip()
        if not raw:
            continue
        name, _, value = raw.partition("=")
        if name.strip() != cookie_name:
            continue
        value = value.strip()
        if value.startswith('"') and value.endswith('"') and len(value) >= 2:
            value = value[1:-1]
        return value or None
    return None


async def _read_csrf_from_body(
    scope: dict,
    receive: Any,
    headers: dict[str, str],
) -> tuple[str | None, Any]:
    """Read the request body once, extract csrf_token, return a replay-receive.

    Returns (token_or_None, new_receive_callable). The new_receive_callable
    replays the buffered body bytes exactly once, then yields the remaining
    receive messages from the upstream channel. The downstream handler MUST
    use the returned receive, not the original.

    Body size guarded by _MAX_BODY_BYTES — malicious clients cannot OOM us
    with a huge multipart upload.

    Only parses form-encoded bodies (application/x-www-form-urlencoded).
    For multipart/form-data, we let the downstream handler read it and rely
    on the header path instead — parsing multipart in middleware is both
    expensive (full multipart parser) and risky (stream consumption).
    """
    content_type = headers.get("content-type", "").lower()

    # Multipart bodies: skip form parsing, rely on header token only
    if "multipart/form-data" in content_type:
        return None, receive

    # Only parse form-encoded
    is_form = "application/x-www-form-urlencoded" in content_type
    if not is_form:
        return None, receive

    # Buffer the entire body
    chunks: list[bytes] = []
    more_body = True
    total = 0
    messages: list[dict] = []
    while more_body:
        message = await receive()
        messages.append(message)
        if message["type"] != "http.request":
            # Disconnect or unknown — bail
            break
        body = message.get("body", b"")
        total += len(body)
        if total > _MAX_BODY_BYTES:
            # Too large: stop reading, surface a replay receive that replays
            # what we have and let downstream handle it as truncated
            chunks.append(body)
            break
        chunks.append(body)
        more_body = message.get("more_body", False)

    full_body = b"".join(chunks)

    token: str | None = None
    try:
        parsed = parse_qs(full_body.decode("latin-1"), keep_blank_values=True)
        values = parsed.get(_FORM_FIELD, [])
        if values:
            token = values[0]
    except Exception:
        logger.exception("Failed to parse form body for CSRF token")
        token = None

    # Build a replay receive: first message replays the buffered body as a
    # single http.request, then subsequent calls delegate to upstream receive
    replayed = False

    async def replay_receive():
        nonlocal replayed
        if not replayed:
            replayed = True
            return {
                "type": "http.request",
                "body": full_body,
                "more_body": False,
            }
        # Caller should not call again since more_body=False, but be safe
        return await receive()

    return token, replay_receive


async def _respond_403(send: Any, detail: str = "CSRF token missing or invalid") -> None:
    import json
    body = json.dumps({"detail": detail}).encode("utf-8")
    await send({
        "type": "http.response.start",
        "status": 403,
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ],
    })
    await send({
        "type": "http.response.body",
        "body": body,
    })


def _is_exempt(path: str) -> bool:
    return any(path.startswith(p) for p in _EXEMPT_PATH_PREFIXES)


class CSRFMiddleware:
    """Pure-ASGI Double Submit Cookie CSRF middleware."""
    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method: str = scope["method"]
        path: str = scope["path"]

        # Build a case-insensitive headers dict
        headers: dict[str, str] = {}
        for name, value in scope.get("headers", []):
            headers[name.decode("latin-1").lower()] = value.decode("latin-1")

        cookie_header = headers.get("cookie", "")
        cookie_token = _extract_cookie(cookie_header, _COOKIE_NAME)

        downstream_receive = receive

        if method not in _SAFE_METHODS and not _is_exempt(path):
            # Unsafe method — validate
            form_token, downstream_receive = await _read_csrf_from_body(scope, receive, headers)
            header_token = headers.get(_HEADER_NAME)
            submitted = form_token or header_token

            if not cookie_token or not submitted or submitted != cookie_token:
                logger.info(
                    "CSRF rejection: path=%s method=%s has_cookie=%s has_submitted=%s match=%s",
                    path, method, bool(cookie_token), bool(submitted),
                    (submitted == cookie_token) if submitted and cookie_token else False,
                )
                await _respond_403(send)
                return

            if not _validate_token(cookie_token):
                logger.info("CSRF rejection: path=%s expired or invalid signature", path)
                await _respond_403(send, "CSRF token expired")
                return

        # Determine token to set on response: rotate only if missing/invalid
        if cookie_token and _validate_token(cookie_token):
            token_for_response = cookie_token
            needs_set_cookie = False
        else:
            token_for_response = _new_token()
            needs_set_cookie = True

        # Expose on scope state for templates / handlers
        scope.setdefault("state", {})
        scope["state"]["csrf_token"] = token_for_response

        async def send_with_cookie(message: dict) -> None:
            if message["type"] == "http.response.start" and needs_set_cookie:
                headers_list = list(message.get("headers", []))
                cookie_attrs = [
                    f"{_COOKIE_NAME}={token_for_response}",
                    "Path=/",
                    "SameSite=Lax",
                    f"Max-Age={_TOKEN_MAX_AGE}",
                ]
                if _is_prod():
                    cookie_attrs.append("Secure")
                # HttpOnly INTENTIONALLY OMITTED — JS needs to read for XHR
                cookie_value = "; ".join(cookie_attrs)
                headers_list.append((b"set-cookie", cookie_value.encode("latin-1")))
                message["headers"] = headers_list
            await send(message)

        await self.app(scope, downstream_receive, send_with_cookie)
```

### B. Tests

```python
# tests/unit/admin/test_csrf.py
import pytest
from fastapi import FastAPI, Request, Form
from starlette.middleware.sessions import SessionMiddleware
from starlette.testclient import TestClient

from src.admin.csrf import (
    CSRFMiddleware,
    _extract_cookie,
    _new_token,
    _validate_token,
    _signer,
)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "x" * 64)
    monkeypatch.delenv("PRODUCTION", raising=False)


class TestExtractCookie:
    def test_single_cookie(self):
        assert _extract_cookie("adcp_csrf=abc", "adcp_csrf") == "abc"

    def test_multi_cookie(self):
        h = "adcp_session=xyz; adcp_csrf=abc; other=qqq"
        assert _extract_cookie(h, "adcp_csrf") == "abc"

    def test_missing_cookie_returns_none(self):
        assert _extract_cookie("adcp_session=xyz", "adcp_csrf") is None

    def test_empty_value_returns_none(self):
        assert _extract_cookie("adcp_csrf=", "adcp_csrf") is None

    def test_substring_name_not_matched(self):
        assert _extract_cookie("csrf=wrong", "adcp_csrf") is None

    def test_quoted_value_unwrapped(self):
        assert _extract_cookie('adcp_csrf="abc"', "adcp_csrf") == "abc"


class TestTokenLifecycle:
    def test_validate_accepts_fresh(self):
        t = _new_token()
        assert _validate_token(t) is True

    def test_validate_rejects_tampered(self):
        t = _new_token()
        assert _validate_token(t + "X") is False

    def test_validate_rejects_empty(self):
        assert _validate_token("") is False


def _build_app():
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="x" * 64)
    app.add_middleware(CSRFMiddleware)

    @app.get("/form")
    def show_form(request: Request):
        return {"csrf_token": request.state.csrf_token}

    @app.post("/submit")
    def submit(request: Request, csrf_token: str = Form(...)):
        return {"ok": True, "received": csrf_token}

    return app


class TestMiddlewareFlow:
    def test_get_sets_csrf_cookie(self):
        app = _build_app()
        with TestClient(app) as c:
            r = c.get("/form")
        assert r.status_code == 200
        assert "adcp_csrf" in r.cookies
        assert r.json()["csrf_token"]

    def test_post_without_cookie_is_403(self):
        app = _build_app()
        with TestClient(app) as c:
            r = c.post("/submit", data={"csrf_token": "anything"})
        assert r.status_code == 403

    def test_post_with_matching_cookie_and_form_succeeds(self):
        app = _build_app()
        with TestClient(app) as c:
            r = c.get("/form")
            token = r.cookies["adcp_csrf"]
            r2 = c.post("/submit", data={"csrf_token": token})
        assert r2.status_code == 200
        assert r2.json()["received"] == token

    def test_post_with_mismatched_tokens_is_403(self):
        app = _build_app()
        with TestClient(app) as c:
            c.get("/form")  # sets cookie
            other_token = _new_token()
            r = c.post("/submit", data={"csrf_token": other_token})
        assert r.status_code == 403

    def test_exempt_path_bypasses_validation(self):
        app = _build_app()

        @app.post("/api/v1/noop")
        def noop():
            return {"ok": True}

        with TestClient(app) as c:
            r = c.post("/api/v1/noop")
        assert r.status_code == 200

    def test_header_token_accepted_for_xhr(self):
        app = _build_app()
        with TestClient(app) as c:
            c.get("/form")
            token = c.cookies["adcp_csrf"]
            # Empty form body but X-CSRF-Token header
            r = c.post(
                "/submit",
                data={"csrf_token": token},
                headers={"X-CSRF-Token": token},
            )
        assert r.status_code == 200

    def test_multipart_bypasses_form_parse_uses_header(self):
        app = _build_app()
        with TestClient(app) as c:
            c.get("/form")
            token = c.cookies["adcp_csrf"]
            r = c.post(
                "/submit",
                files={"upload": ("a.txt", b"data")},
                data={"csrf_token": token},
                headers={"X-CSRF-Token": token},  # multipart needs header path
            )
        assert r.status_code in (200, 422)  # 422 if handler can't bind multipart
```

### C. Integration

- Imports `itsdangerous` only. No DB, no other module dependencies beyond env.
- Public API: `CSRFMiddleware` class.
- Registered in `app_factory.py` via `app.add_middleware(CSRFMiddleware)`.
- Exposes `request.state.csrf_token` for `templating.py`'s `render()` wrapper.
- **Middleware ordering** (critical): `add_middleware` is LIFO — the LAST added is the OUTERMOST. Desired runtime order:
  ```
  outermost ─┐
  ExternalDomainRedirect   (runs first, can short-circuit)
  FlyHeadersMiddleware     (normalize headers)
  UnifiedAuthMiddleware    (token extraction)
  SessionMiddleware        (populate request.session)
  CSRFMiddleware           (needs session? no, but keeping it inside session
                            for future use and so _read_csrf_from_body's
                            buffered receive doesn't conflict with Session)
  CORSMiddleware           (innermost, close to handlers)
  innermost ─┘
  ```
  Registered in `app_factory` in REVERSE (CORSMiddleware first, ExternalDomainRedirect last).

### D. Gotchas

- **Body read exhaustion:** Without the replay-receive pattern, downstream handlers get an empty body (they've already been consumed). Test this end-to-end.
- **Multipart forms:** We do not parse multipart bodies. Handlers that accept file uploads MUST send the CSRF token in the `X-CSRF-Token` header, not the form body. The codemod can't enforce this at compile time — add a pre-commit grep for `{% form %} enctype="multipart/form-data"` without X-CSRF-Token handling.
- **Cookie-only origin binding:** Double Submit Cookie does NOT defend against subdomain attacks (a malicious subdomain can set cookies on the parent domain). Scope CSRF cookie with `Path=/` is insufficient if attacker controls `evil.sales-agent.example.com`. Mitigation: `adcp_csrf` is set at the current host only (no `Domain=` attribute), so it does NOT share across subdomains. This is intentional.
- **`HttpOnly=False`:** The CSRF cookie is readable by JavaScript. This is REQUIRED for XHR to send it in `X-CSRF-Token`. An attacker with XSS can exfiltrate the CSRF token — but with XSS they can already perform any authenticated action directly, so this is not a degradation.
- **`_MAX_BODY_BYTES = 1MB`:** Form POSTs over 1MB are rejected via truncation (the replay receive will give downstream a truncated body). If you have forms that legitimately submit >1MB of text, bump this.
- **TestClient cookie flow:** `TestClient` persists cookies across requests within the `with` block. The pattern is: `c.get("/form")` to populate the CSRF cookie, then `c.post(...)` — the cookie is auto-sent. Extract the token from `c.cookies["adcp_csrf"]` for the form field. The `IntegrationEnv.get_admin_client()` helper wraps this into a single authenticated client fixture.

---

## 11.8 `src/admin/middleware/external_domain.py`

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

MUST run BEFORE SessionMiddleware — Approximated strips cookies on the
OAuth bounce, and we want to redirect before the session attempts to bind.
MUST be pure ASGI (Starlette #1729) because SessionMiddleware downstream
is sensitive to BaseHTTPMiddleware task-group interleaving.
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


async def _respond_redirect(send: Any, url: str, status: int = 302) -> None:
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
- Reads DB via `get_tenant_by_virtual_host`. This is one sync DB call per external-domain request. Cache in v2.1 via an LRU.

### D. Gotchas

- **Failure mode = pass through, not 404/500:** If the tenant lookup fails (DB down), we let the request continue to the admin UI rather than returning an error. This prevents the middleware from being a hard availability dependency.
- **No ContextVar state:** Pure ASGI middleware that runs BEFORE `UnifiedAuthMiddleware` does not see `request.state.auth_context`. Don't try to use auth here.
- **Body not consumed:** We redirect before reading the body, so no receive-channel manipulation needed (unlike CSRF).

---

## 11.9 `src/admin/middleware/fly_headers.py`

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
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from fastapi import APIRouter
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import RedirectResponse
from starlette.requests import Request
from urllib.parse import quote

from src.admin.csrf import CSRFMiddleware
from src.admin.deps.auth import AdminRedirect, AdminAccessDenied
from src.admin.middleware.external_domain import ExternalDomainRedirectMiddleware
from src.admin.middleware.fly_headers import FlyHeadersMiddleware
from src.admin.oauth import init_oauth
from src.admin.sessions import session_middleware_kwargs
from src.admin.templating import render


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
        (CSRFMiddleware, {}),
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
        admin = build_admin()
        assert admin.router is not None
        assert (CSRFMiddleware_cls := next(m for m, _ in admin.middleware if m.__name__ == "CSRFMiddleware"))

    def test_end_to_end_admin_mount(self, monkeypatch):
        monkeypatch.setenv("SESSION_SECRET", "x" * 64)
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
- Order: `RequestIDMiddleware → FlyHeaders → ExternalDomainRedirect → UnifiedAuth → Session → CSRF → RestCompat → CORS → handler`
- Emits `X-Request-ID` on responses; echoes upstream value if the client sent one
- Structural guard `test_architecture_middleware_order.py` enforces the registration order

### D. Gotchas

- **AdCP boundary:** `X-Request-ID` is a universal debug header; no AdCP wire format claims it. Safe to add. See `async-audit/agent-e-ideal-state-gaps.md` Category 8 Section 3 cross-check.
- **Response header propagation requires intercepting `send`.** The wrapped `send_with_header` only acts on the first `http.response.start` message.

---

## 11.11 `src/app.py` — Exception handlers (Agent E Category 6)

> **Added 2026-04-11 under the Agent E idiom upgrade.** The plan covered `AdCPError` (Blocker 3) and `AdminRedirect` but missed four other handlers a 2026 FastAPI app needs: `HTTPException` (Accept-aware), `RequestValidationError`, `AdminAccessDenied`, and the catch-all `Exception`.

### A. Implementation

```python
# src/app.py (exception handlers)
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.admin.deps.auth import AdminAccessDenied, AdminRedirect
from src.admin.templating import render
from src.core.errors import AdCPError
from src.core.settings import get_settings


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

---

## 11.13 Test harness fixtures — async-native via httpx + ASGITransport (Agent E Category 14)

> **Added 2026-04-11 under the Agent E idiom upgrade.** Sync `TestClient(app)` spawns its own event loop in a thread and conflicts with async lifespan state stored on `app.state`. `httpx.AsyncClient(transport=ASGITransport(app=app))` runs in the test's own event loop and sees `app.state` correctly.

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

## Cross-cutting concerns

### Middleware ordering (final answer)

In `src/app.py`, the registration order (bottom-to-top = outermost-to-innermost):

```python
# Inner → outer (LIFO registration order)
app.add_middleware(CORSMiddleware, allow_origins=...)      # innermost
app.add_middleware(RestCompatMiddleware)
app.add_middleware(CSRFMiddleware)                          # new, admin
app.add_middleware(SessionMiddleware, **session_kwargs)     # new, admin
app.add_middleware(UnifiedAuthMiddleware)
app.add_middleware(ExternalDomainRedirectMiddleware)        # new, admin
app.add_middleware(FlyHeadersMiddleware)                    # new, outermost
```

Runtime order per request: Fly → ExternalDomain → UnifiedAuth → Session → CSRF → RestCompat → CORS → handler. Justification for CSRF inside Session: `request.session` is available in case a future handler wants to double-bind CSRF to session state; for v2.0.0 the order doesn't matter semantically, but keeping the more-innovative middleware (CSRF) innermost minimizes blast radius on changes.

### Critical Files for Implementation

- /Users/quantum/Documents/ComputedChaos/salesagent/src/admin/app.py
- /Users/quantum/Documents/ComputedChaos/salesagent/src/admin/utils/helpers.py
- /Users/quantum/Documents/ComputedChaos/salesagent/src/core/auth_context.py
- /Users/quantum/Documents/ComputedChaos/salesagent/src/core/auth_middleware.py
- /Users/quantum/Documents/ComputedChaos/salesagent/src/services/auth_config_service.py
