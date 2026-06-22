# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Working Style

- Make **minimal changes** for every task. Ask before making major/structural changes.
- Do not modify test files unless explicitly asked.

## Commands

```bash
make quality        # format + lint + typecheck + unit tests (run before every commit)
make lint-fix       # auto-fix formatting/linting
make compose-up     # rebuild Docker stack (use after uv.lock changes, not docker compose build)
make openapi        # regenerate OpenAPI spec — run after any endpoint/schema change in tenant_management_api.py
tox -e unit         # unit tests only (no Docker)
./run_all_tests.sh  # full suite: Docker + all 5 test envs
```

## Architecture

Single Starlette binary at `scripts/run_server.py → core.main.main()` serves three surfaces via nginx at `http://localhost:8000`:

| Surface | Path | Source |
|---------|------|--------|
| Admin UI | `/admin/`, `/tenant/<name>` | `src/admin/` (Flask, Google OAuth) |
| MCP Server | `/mcp/` | `src/core/tools/` |
| A2A Server | `/a2a` | `src/core/tools/` via `core/platforms/_delegate.py` |

`core/platforms/_delegate.py` bridges the new `core/` framework to existing `src/core/tools/*` `_impl` functions. All new platform code delegates into `src/`.

## Critical Patterns (Non-Negotiable)

### Transport Boundary
Every tool has two layers. `_impl` functions (business logic) must:
- Accept `ResolvedIdentity`, never `Context`/`ToolContext`
- Raise `AdCPError`, never `ToolError`
- Have zero imports from `fastmcp`, `a2a`, `starlette`, `fastapi`

MCP/A2A wrappers call `resolve_identity()` first, then forward **all** `_impl` params.

### Repository Pattern
No `get_db_session()` or raw ORM construction in `_impl` functions. All DB access goes through `src/core/database/repositories/`. Use SQLAlchemy 2.0: `select()` + `scalars()`, never `session.query()`.

### Schema Inheritance
Extend `adcp` library types, never copy them:
```python
from adcp.types import Product as LibraryProduct
class Product(LibraryProduct):
    internal_field: dict | None = Field(default=None, exclude=True)
```

### Pydantic Nested Serialization
Parent models must override `model_dump()` to call it on nested children — Pydantic doesn't do this automatically.

### JavaScript Routing
Use `scriptRoot = '{{ request.script_root }}' || ''` for all API URLs — never hardcode paths (breaks behind nginx).

## Structural Guards

AST tests in `tests/unit/` enforce architecture on every `make quality` run. Violations fail the build. Allowlists only shrink — never add new violations.

Key guards: transport-agnostic `_impl`, `ResolvedIdentity` in `_impl`, schema inheritance, boundary completeness (all params forwarded), repository pattern (no raw DB in `_impl`).

## Commit Messages

Use Conventional Commits — `feat:`, `fix:`, `refactor:`, `docs:`, `chore:`. PR titles must pass `.github/workflows/pr-title-check.yml`.

## Tenant Dependencies

```
Tenant → CurrencyLimit (USD required) → PropertyTag ("all_inventory" required) → Products
```
Products require both CurrencyLimit and PropertyTag to exist first.