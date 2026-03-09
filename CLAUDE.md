# Prebid Sales Agent - Development Guide

## 🤖 For Claude (AI Assistant)

This guide helps you work effectively with the Prebid Sales Agent codebase maintained under Prebid.org. Key principles:

### Working with This Codebase
1. **Always read before writing** - Use Read/Glob to understand existing patterns
2. **Test your changes** - Run `make quality` before committing
3. **Follow the patterns** - 7 critical patterns below are non-negotiable
4. **When stuck** - Check `/docs` for detailed explanations
5. **Pre-commit hooks are your friend** - They catch most issues automatically
6. **Name your PRs correctly** - they need to pass .github/workflows/pr-title-check.yml

### Common Task Patterns
- **Adding a new AdCP tool**: Extend library schema → Add `_impl()` function → Add MCP wrapper → Add A2A raw function → Add tests
- **Fixing a route issue**: Check for conflicts with `grep -r "@.*route.*your/path"` → Use `url_for()` in Python, `scriptRoot` in JavaScript
- **Modifying schemas**: Verify against AdCP spec → Update Pydantic model → Run `pytest tests/unit/test_adcp_contract.py`
- **Database changes**: Use SQLAlchemy 2.0 `select()` → Use `JSONType` for JSON → Create migration with `alembic revision`

### Key Files to Know
- `src/core/main.py` - MCP tools and `_impl()` functions
- `src/core/tools.py` - A2A raw functions
- `src/core/schemas.py` - Pydantic models (AdCP-compliant)
- `src/adapters/base.py` - Adapter interface
- `src/adapters/gam/` - GAM implementation
- `tests/unit/test_adcp_contract.py` - Schema compliance tests

### What to Avoid
- ❌ Don't use `session.query()` (use `select()` + `scalars()`)
- ❌ Don't duplicate library schemas (extend with inheritance)
- ❌ Don't hardcode URLs in JavaScript (use `scriptRoot`)
- ❌ Don't bypass pre-commit hooks without good reason
- ❌ Don't skip tests to make CI pass (fix the underlying issue)

### Commit Messages & PR Titles
**Use Conventional Commits format** - release-please uses this to generate changelogs.

PR titles should use one of these prefixes:
- `feat: Add new feature` - New functionality (appears in "Features" section)
- `fix: Fix bug description` - Bug fixes (appears in "Bug Fixes" section)
- `docs: Update documentation` - Documentation changes
- `refactor: Restructure code` - Code refactoring (appears in "Code Refactoring" section)
- `perf: Improve performance` - Performance improvements
- `chore: Update dependencies` - Maintenance tasks (hidden from changelog)

**Without a prefix, commits won't appear in release notes!** The code will still be released, but the change won't be documented in the changelog.

### Structural Guards (Automated Architecture Enforcement)
Eleven AST-scanning tests enforce architecture invariants on every `make quality` run. New violations fail the build immediately. See [docs/development/structural-guards.md](docs/development/structural-guards.md) for full details.

| Guard | Enforces | Test File |
|-------|----------|-----------|
| No ToolError in _impl | `_impl` raises AdCPError, never ToolError | `test_no_toolerror_in_impl.py` |
| Transport-agnostic _impl | `_impl` has zero transport imports | `test_transport_agnostic_impl.py` |
| ResolvedIdentity in _impl | `_impl` accepts ResolvedIdentity, not Context | `test_impl_resolved_identity.py` |
| Schema inheritance | Schemas extend adcp library base types | `test_architecture_schema_inheritance.py` |
| Boundary completeness | MCP/A2A wrappers pass all _impl parameters | `test_architecture_boundary_completeness.py` |
| Query type safety | DB queries use types matching column definitions | `test_architecture_query_type_safety.py` |
| No model_dump in _impl | `_impl` returns model objects, never calls `.model_dump()` | `test_architecture_no_model_dump_in_impl.py` |
| Repository pattern | No `get_db_session()` in `_impl`; no inline `session.add()` in tests | `test_architecture_repository_pattern.py` |
| Migration completeness | Every migration has non-empty `upgrade()` and `downgrade()` | `test_architecture_migration_completeness.py` |
| No raw MediaPackage select | All MediaPackage access goes through repository, not raw `select()` | `test_architecture_no_raw_media_package_select.py` |
| Obligation coverage | Behavioral obligations in docs have matching test coverage | `test_architecture_obligation_coverage.py` |

**Rules for guards:**
- Allowlists can only shrink — never add new violations, fix them instead
- Every allowlisted violation has a `# FIXME(salesagent-xxxx)` comment at the source location
- When you fix a violation, remove it from the allowlist (the stale-entry test will remind you)

---

## 🚨 Critical Architecture Patterns

### 1. AdCP Schema: Extend Library Schemas
**MANDATORY**: Use `adcp` library schemas via inheritance, never duplicate.

```python
from adcp.types import Product as LibraryProduct  # Library* alias convention

class Product(LibraryProduct):
    """Extends library Product with internal-only fields."""
    implementation_config: dict[str, Any] | None = Field(default=None, exclude=True)
```

**Rules:**
- Import library types with `Library*` alias: `from adcp.types import X as LibraryX`
- Extend with inheritance — don't copy fields from the parent class
- Only redeclare parent fields when needed for nested serialization (Pattern #4)
- Mark internal-only fields with `exclude=True`
- Run `pytest tests/unit/test_adcp_contract.py` before commit
- **Enforced by:** `test_architecture_schema_inheritance.py`

### 2. Flask: Prevent Route Conflicts
**Pre-commit hook detects duplicate routes** - Run manually: `uv run python .pre-commit-hooks/check_route_conflicts.py`

When adding routes:
- Search existing: `grep -r "@.*route.*your/path"`
- Deprecate properly with early return, not comments

### 3. Database: Repository Pattern + ORM-First
**No SQLite support** - Production uses PostgreSQL exclusively.

**ORM-first access (MANDATORY):**
- All DB reads and writes go through SQLAlchemy ORM models via repository classes
- Never construct ORM models with raw kwargs scattered in `_impl` functions — use model factory methods or repository `create_from_*()` methods
- Never pass `json.dumps()` to `JSONType` columns — the column type handles serialization
- Use SQLAlchemy relationships and cascading — they exist to manage parent/child persistence atomically
- Use `JSONType` for all JSON columns (not plain `JSON`)
- Use SQLAlchemy 2.0 patterns: `select()` + `scalars()`, not `query()`
- Cast IDs at the boundary: JSON gives you strings, but Integer PK columns need `int` values. Write `int(x)` before passing to `.in_()` or `filter_by()`
- All tests require PostgreSQL: `./run_all_tests.sh` runs Docker + tox (JSON reports in `test-results/`)
- **Exception:** Bulk imports and complex reporting queries may use Core SQL/raw SQL for performance. Regular CRUD operations are never an exception.
- **Enforced by:** `test_architecture_query_type_safety.py`, `test_architecture_repository_pattern.py`

**Repository pattern:**
```python
# CORRECT: repository encapsulates data access
class MediaBuyRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_by_id(self, media_buy_id: str, tenant_id: str) -> MediaBuy | None:
        return self.session.scalars(
            select(MediaBuy).filter_by(media_buy_id=media_buy_id, tenant_id=tenant_id)
        ).first()

    def create_from_request(self, req: CreateMediaBuyRequest, identity: ResolvedIdentity) -> MediaBuy:
        media_buy = MediaBuy.from_request(req, identity)
        self.session.add(media_buy)
        return media_buy
```

```python
# WRONG: inline session management and raw model construction in business logic
def _create_media_buy_impl(req, identity):
    with get_db_session() as session:
        mb = MediaBuy(
            media_buy_id=generate_id(),
            buyer_ref=req.buyer_ref,      # manual field plucking
            tenant_id=tenant["tenant_id"], # from a dict, not even a model
            status="pending_approval",     # magic string
            ...
        )
        session.add(mb)
```

**`_impl` functions should not contain `get_db_session()` calls.** Data access belongs in the repository layer. `_impl` functions receive repositories (or use them via dependency injection) and call typed methods.

### 4. Pydantic: Explicit Nested Serialization
Parent models must override `model_dump()` to serialize nested children:

```python
class GetCreativesResponse(AdCPBaseModel):
    creatives: list[Creative]

    def model_dump(self, **kwargs):
        result = super().model_dump(**kwargs)
        if "creatives" in result and self.creatives:
            result["creatives"] = [c.model_dump(**kwargs) for c in self.creatives]
        return result
```

**Why**: Pydantic doesn't auto-call custom `model_dump()` on nested models.

### 5. Transport Boundary: Layer Separation
All tools have two layers: **transport wrappers** (MCP, A2A, REST) and **business logic** (`_impl` functions). The layers have strict responsibilities.

**`_impl` functions** (business logic layer):
```python
async def _create_media_buy_impl(
    req: CreateMediaBuyRequest,
    push_notification_config: dict | None = None,
    identity: ResolvedIdentity | None = None,    # NOT Context/ToolContext
) -> CreateMediaBuyResult:
    # Business logic only — no transport awareness
    ...
```

**Transport wrappers** (boundary layer):
```python
# MCP wrapper — resolves identity, forwards ALL params to _impl
@mcp.tool()
async def create_media_buy(ctx: Context, ...) -> CreateMediaBuyResponse:
    identity = resolve_identity(ctx.http.headers, protocol="mcp")
    return await _create_media_buy_impl(req=req, identity=identity, ...)

# A2A wrapper — same contract, different transport
async def create_media_buy_raw(...) -> CreateMediaBuyResponse:
    identity = resolve_identity(headers, protocol="a2a")
    return await _create_media_buy_impl(req=req, identity=identity, ...)
```

**Rules for `_impl` functions:**
- Accept `ResolvedIdentity`, never `Context`, `ToolContext`, or raw headers
- Raise `AdCPError` subclasses, never `ToolError` (that's transport-specific)
- Zero imports from `fastmcp`, `a2a`, `starlette`, or `fastapi`
- No auth extraction or tenant resolution — that's the wrapper's job

**Rules for transport wrappers:**
- Call `resolve_identity()` to create `ResolvedIdentity` before calling `_impl`
- Forward **every** `_impl` parameter — don't silently drop any
- Catch `AdCPError` and translate to transport-appropriate error format

**Enforced by:** `test_transport_agnostic_impl.py`, `test_impl_resolved_identity.py`, `test_no_toolerror_in_impl.py`, `test_architecture_boundary_completeness.py`

### 6. JavaScript: Use request.script_root
**All JS must support reverse proxy deployments:**

```javascript
const scriptRoot = '{{ request.script_root }}' || '';  // e.g., '/admin' or ''
const apiUrl = scriptRoot + '/api/endpoint';
fetch(apiUrl, { credentials: 'same-origin' });
```

Never hardcode `/api/endpoint` - breaks with nginx prefix.

### 7. Schema Validation: Environment-Based
- **Production**: `ENVIRONMENT=production` → `extra="ignore"` (forward compatible)
- **Development/CI**: Default → `extra="forbid"` (strict validation)

### 8. Test Fixtures: Factory-Based, Not Inline
**MANDATORY for new integration tests:** Use `factory-boy` factories for test data, not inline `session.add()` boilerplate.

```python
# CORRECT: factory creates ORM instance with sane defaults
from tests.factories import TenantFactory, MediaBuyFactory

@pytest.fixture
def sample_tenant(integration_db):
    return TenantFactory.create_sync()

@pytest.fixture
def sample_media_buy(sample_tenant, sample_principal):
    return MediaBuyFactory.create_sync(
        tenant_id=sample_tenant.tenant_id,
        principal_id=sample_principal.principal_id,
    )
```

```python
# WRONG: 20 lines of manual model construction in every test
with get_db_session() as session:
    tenant = Tenant(tenant_id="test", name="Test", subdomain="test", ...)
    session.add(tenant)
    currency = CurrencyLimit(tenant_id="test", ...)
    session.add(currency)
    prop = PropertyTag(tenant_id="test", ...)
    session.add(prop)
    session.commit()
```

**Rules:**
- Shared fixtures (tenant, principal, products) defined once in `conftest.py` using factories
- Test-specific data uses factory overrides, not copy-pasted setup blocks
- Factories live in `tests/factories/` — ORM factories and Pydantic schema factories
- Never `session.add()` in test bodies — use factories or fixtures that use factories

---

## Project Overview

Python-based Prebid Sales Agent with:
- **MCP Server**: FastMCP tools for AI agents (via nginx at `/mcp/`)
- **Admin UI**: Google OAuth secured interface (via nginx at `/admin/` or `/tenant/<name>`)
- **A2A Server**: python-a2a agent-to-agent communication (via nginx at `/a2a`)
- **Multi-Tenant**: Database-backed isolation with subdomain routing
- **PostgreSQL**: Production-ready with Docker deployment

All services are accessed through the nginx proxy at **http://localhost:8000**.

---

## Key Patterns

### SQLAlchemy 2.0 (MANDATORY for new code)
```python
from sqlalchemy import select

# Use this
stmt = select(Model).filter_by(field=value)
instance = session.scalars(stmt).first()

# Not this (deprecated)
instance = session.query(Model).filter_by(field=value).first()
```

### Database JSON Fields
```python
from src.core.database.json_type import JSONType

class MyModel(Base):
    config: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
```

### Import Patterns
```python
# Always use absolute imports
from src.core.schemas import Principal
from src.core.database.database_session import get_db_session
from src.adapters import get_adapter
```

### No Quiet Failures
```python
# ❌ WRONG - Silent failure
if not self.supports_feature:
    logger.warning("Skipping...")

# ✅ CORRECT - Explicit failure
if not self.supports_feature and feature_requested:
    raise FeatureNotSupportedException("Cannot fulfill contract")
```

---

## Common Operations

### Running Locally

```bash
# Clone and start
git clone https://github.com/prebid/salesagent.git
cd salesagent
docker compose up -d      # Build and start all services
docker compose logs -f    # View logs (Ctrl+C to exit)
docker compose down       # Stop

# Migrations run automatically on startup
```

**Access at http://localhost:8000:**
- Admin UI: `/admin/` or `/tenant/default`
- MCP Server: `/mcp/`
- A2A Server: `/a2a`

**Test login:** Click "Log in to Dashboard" button (password: `test123`)

**Test MCP interface:**
```bash
uvx adcp http://localhost:8000/mcp/ --auth test-token list_tools
```

**Note:** `docker compose` builds from local source. For a clean rebuild: `docker compose build --no-cache`

### Testing

Test orchestration uses **tox** (with tox-uv) for parallel execution and combined coverage.
Install: `uv tool install tox --with tox-uv`

```bash
# ─── Quick checks ───
make quality              # Format + lint + typecheck + unit tests (before every commit)
tox -e unit               # Unit tests only (fast, no Docker)

# ─── Full suite (Docker + all 5 suites in parallel via tox) ───
./run_all_tests.sh        # One command: starts Docker, runs tox -p, tears down
./run_all_tests.sh quick  # No Docker: unit + integration + integration_v2

# ─── Manual Docker lifecycle (for iterating) ───
make test-stack-up        # Start Docker stack, writes .test-stack.env
source .test-stack.env && tox -p   # Run all suites in parallel
make test-stack-down      # Tear down Docker

# ─── Coverage ───
make test-cov             # Open HTML coverage report (htmlcov/index.html)

# ─── Targeted runs ───
./run_all_tests.sh ci tests/integration/test_file.py -k test_name
tox -e integration -- -k test_name   # Pass pytest args after --

# Reports: test-results/<ddmmyy_HHmm>/*.json (last 10 runs kept)
# Coverage: htmlcov/index.html, coverage.json
```

### Database Migrations
```bash
uv run python scripts/ops/migrate.py            # Run migrations locally
uv run alembic revision -m "description"        # Create migration

# In Docker (migrations run automatically, but can be run manually):
docker compose exec admin-ui python scripts/ops/migrate.py
```

**Never modify existing migrations after commit!**

### Tenant Setup Dependencies
```
Tenant → CurrencyLimit (USD required for budget validation)
      → PropertyTag ("all_inventory" required for property_tags references)
      → Products (require BOTH)
```

---

## Testing Guidelines

### Test Organization
- **tests/unit/**: Fast, isolated (mock external deps only)
- **tests/integration/**: Real PostgreSQL database
- **tests/e2e/**: Full system tests
- **tests/ui/**: Admin UI tests

### Database Fixtures
```python
# Integration tests - use integration_db
@pytest.mark.requires_db
def test_something(integration_db):
    with get_db_session() as session:
        # Test with real PostgreSQL
        pass

# Unit tests - mock the database
def test_something():
    with patch('src.core.database.database_session.get_db_session') as mock_db:
        # Test with mocked database
        pass
```

### Quality Rules
- Max 10 mocks per test file (pre-commit enforces)
- AdCP compliance test for all client-facing models
- Test YOUR code, not Python built-ins
- Roundtrip test required for any operation using `apply_testing_hooks()`

### Test Integrity Policy — ZERO TOLERANCE

**This is non-negotiable. Every rule below is a HARD STOP.**

1. **NEVER skip, ignore, deselect, or exclude failing tests.** Do not use `--ignore`, `-k "not test_name"`, `--deselect`, `pytest.mark.skip`, or `pytest.mark.xfail` to work around failures.
2. **NEVER rationalize failures.** Do not classify failures as "pre-existing", "infrastructure issue", "misplaced test", "needs a running server", or "was deselected in the full run". A failing test is a failing test — fix it or report it to the user as a blocker.
3. **Start the right infrastructure.** If a test needs Docker (integration, e2e, ui), start Docker. The tooling exists — use it. See the infrastructure decision tree below.
4. **If infrastructure is broken, STOP.** Do not skip tests and report success. Tell the user the infrastructure is broken and either fix it or ask the user to fix it.
5. **Test results are saved as JSON** in `test-results/<ddmmyy_HHmm>/`. Review these instead of re-running the full suite. Background processes may crash and lose output — the JSON reports are the resilient record.

### Test Infrastructure Decision Tree

**Choose the right tool based on what you're testing:**

| What you need | Command | What it starts |
|---------------|---------|----------------|
| Unit tests only | `make quality` | Nothing (no Docker) |
| One integration test (iterating) | `scripts/run-test.sh tests/integration/test_foo.py -x` | Bare Postgres via agent-db (persists) |
| Integration DB for a worktree agent | `eval $(.claude/skills/agent-db/agent-db.sh up)` | Bare Postgres (unique port per worktree) |
| Full suite (all 5 envs) | `./run_all_tests.sh` | Full Docker stack (Postgres + app + nginx), auto-teardown |
| Full suite, targeted | `./run_all_tests.sh ci tests/integration/test_file.py -k test_name` | Full Docker stack |
| Quick suite (no e2e/ui) | `./run_all_tests.sh quick` | Nothing (needs pre-existing DATABASE_URL) |
| Manual Docker lifecycle | `make test-stack-up` → `source .test-stack.env && tox -p` → `make test-stack-down` | Full Docker stack (stays up between runs) |

**Port conflicts are not possible.** Both `test-stack.sh` and `agent-db.sh` scan for free ports in the 50000-60000 range. Multiple instances can run simultaneously.

**When in doubt, use `./run_all_tests.sh`.** It handles everything: Docker up, all suites, Docker down, JSON reports saved.

### Testing Workflow (Before Commit)
```bash
# ALL changes
make quality                           # Format + lint + typecheck + unit tests
uv run python -c "from src.core.tools import your_import"  # Verify imports

# Refactorings (shared impl, moving code, imports)
tox -e integration                     # Real PostgreSQL integration tests

# Critical changes (protocol, schema updates)
./run_all_tests.sh                     # Full suite: Docker + all 5 suites via tox
```

**Pre-commit hooks can't catch import errors** - You must run tests for refactorings!

---

## Development Best Practices

### Code Style
- Use `uv` for dependencies
- Run `pre-commit run --all-files`
- Use type hints
- No hardcoded external system IDs (use config/database)
- No testing against production systems

### Type Checking
```bash
uv run mypy src/core/your_file.py --config-file=mypy.ini
```

When modifying code:
1. Fix mypy errors in files you change
2. Use SQLAlchemy 2.0 `Mapped[]` annotations for new models
3. Use `| None` instead of `Optional[]` (Python 3.10+)

---

## Configuration

### Secrets (.env.secrets - REQUIRED)
```bash
GEMINI_API_KEY=your-key
GOOGLE_CLIENT_ID=your-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-secret
SUPER_ADMIN_EMAILS=user@example.com
GAM_OAUTH_CLIENT_ID=your-gam-id.apps.googleusercontent.com
GAM_OAUTH_CLIENT_SECRET=your-gam-secret
APPROXIMATED_API_KEY=your-approximated-api-key
```

### Database Schema
- **Core**: tenants, principals, products, media_buys, creatives, audit_logs
- **Workflow**: workflow_steps, object_workflow_mappings
- **Deprecated**: tasks, human_tasks (DO NOT USE)

---

## Adapter Support

### GAM Adapter
**Supported Pricing**: CPM, VCPM, CPC, FLAT_RATE

- Automatic line item type selection based on pricing + guarantees
- FLAT_RATE → SPONSORSHIP with CPD translation
- VCPM → STANDARD only (GAM requirement)
- See `docs/adapters/` for compatibility matrix

### Mock Adapter
**Supported**: All AdCP pricing models (CPM, VCPM, CPCV, CPP, CPC, CPV, FLAT_RATE)
- All currencies, simulates appropriate metrics
- Used for testing and development

---

## Deployment

### Environments
- **Local Dev**: `docker compose up -d` → http://localhost:8000 (builds from source)
- **Production**: Deploy to your preferred hosting platform

**Local Dev Notes:**
- Test mode enabled by default (`ADCP_AUTH_TEST_MODE=true`)
- Test credentials: Click "Log in to Dashboard" button (password: `test123`)

### Git Workflow (MANDATORY)
**Never push directly to main**

1. Work on feature branches: `git checkout -b feature/name`
2. Create PR: `gh pr create`
3. Merge via GitHub UI

### Hosting Options
This app can be hosted anywhere:
- Docker (recommended) - Any Docker-compatible platform
- Kubernetes - Full k8s manifests supported
- Cloud Providers - AWS, GCP, Azure, DigitalOcean
- Platform Services - Fly.io, Heroku, Railway, Render

See `docs/deployment.md` for platform-specific guides.

---

## Documentation

**Detailed docs in `/docs`:**
- `ARCHITECTURE.md` - System architecture
- `SETUP.md` - Initial setup guide
- `DEVELOPMENT.md` - Development workflow
- `testing/` - Testing patterns and case studies
- `TROUBLESHOOTING.md` - Common issues
- `security.md` - Security guidelines
- `deployment.md` - Deployment guides
- `adapters/` - Adapter-specific documentation

---

## Quick Reference

### MCP Client
```python
from fastmcp.client import Client
from fastmcp.client.transports import StreamableHttpTransport

headers = {"x-adcp-auth": "your_token"}
transport = StreamableHttpTransport(url="http://localhost:8000/mcp/", headers=headers)
client = Client(transport=transport)

async with client:
    products = await client.tools.get_products(brief="video ads")
    result = await client.tools.create_media_buy(product_ids=["prod_1"], ...)
```

### CLI Testing
```bash
# List available tools
uvx adcp http://localhost:8000/mcp/ --auth test-token list_tools

# Get a real token from Admin UI → Advertisers → API Token
uvx adcp http://localhost:8000/mcp/ --auth <real-token> get_products '{"brief":"video"}'
```

### Admin UI
- Local: http://localhost:8000/admin/ (or `/tenant/default`)
- Production: Configure based on your hosting

---

## Decision Tree for Claude

**User asks to add a new feature:**
1. Search existing code: `Glob` for similar features
2. Read relevant files to understand patterns
3. Design solution following critical patterns
4. Write tests first (TDD)
5. Implement feature
6. Run tests: `make quality`
7. Commit with clear message

**User reports a bug:**
1. Reproduce: Read the code path
2. Write failing test that demonstrates bug
3. Fix the code
4. Verify test passes
5. Check for similar issues in codebase
6. Commit fix with test

**User asks "how does X work?"**
1. Search for X: Use `Grep` to find relevant code
2. Read the implementation
3. Check tests for examples: `tests/unit/test_*X*.py`
4. Explain with code references (file:line)
5. Link to relevant docs if they exist

**User asks to refactor code:**
1. Verify tests exist and pass
2. Make small, incremental changes
3. Run tests after each change: `make quality`
4. For import changes, verify: `uv run python -c "from module import thing"`
5. For shared implementations, run integration tests: `tox -e integration`

**User asks about best practices:**
1. Check this CLAUDE.md for patterns
2. Check `/docs` for detailed guidelines
3. Look at recent code for current conventions
4. When in doubt, follow the 7 critical patterns above

---

## Support

- Documentation: `/docs` directory
- Test examples: `/tests` directory
- Adapter implementations: `/src/adapters` directory
- Issues: File on GitHub repository
