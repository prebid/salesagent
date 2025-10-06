# AdCP Sales Agent Server - Development Guide

## 🚨 CRITICAL ARCHITECTURE PATTERNS

### MCP/A2A Shared Implementation Pattern
**🚨 MANDATORY**: All tools MUST use shared implementation to avoid code duplication.

```
CORRECT ARCHITECTURE:
  MCP Tool (main.py)  → _tool_name_impl() → [real implementation]
  A2A Raw (tools.py) → _tool_name_impl() → [real implementation]

WRONG - DO NOT DO THIS:
  MCP Tool → [implementation A]
  A2A Raw → [implementation B]  ❌ DUPLICATED CODE!
```

**Pattern for ALL tools:**
1. **Implementation** in `main.py`: `def _tool_name_impl(...) -> ResponseType`
   - Contains ALL business logic
   - No `@mcp.tool` decorator
   - Called by both MCP and A2A paths

2. **MCP Wrapper** in `main.py`: `@mcp.tool() def tool_name(...) -> ResponseType`
   - Thin wrapper with decorator
   - Just calls `_tool_name_impl()`

3. **A2A Raw Function** in `tools.py`: `def tool_name_raw(...) -> ResponseType`
   - Imports and calls `_tool_name_impl()` from main.py
   - Uses lazy import to avoid circular dependencies: `from src.core.main import _tool_name_impl`

**Example:**
```python
# src/core/main.py
def _create_media_buy_impl(promoted_offering: str, ...) -> CreateMediaBuyResponse:
    """Real implementation with all business logic."""
    # 600+ lines of actual implementation
    return response

@mcp.tool()
def create_media_buy(promoted_offering: str, ...) -> CreateMediaBuyResponse:
    """MCP tool wrapper."""
    return _create_media_buy_impl(promoted_offering, ...)

# src/core/tools.py
def create_media_buy_raw(promoted_offering: str, ...) -> CreateMediaBuyResponse:
    """A2A raw function."""
    from src.core.main import _create_media_buy_impl  # Lazy import
    return _create_media_buy_impl(promoted_offering, ...)
```

**Status of Current Tools:**
- ✅ `create_media_buy` - Uses shared `_create_media_buy_impl`
- ✅ `get_media_buy_delivery` - Uses shared `_get_media_buy_delivery_impl`
- ✅ `sync_creatives` - Uses shared `_sync_creatives_impl`
- ✅ `list_creatives` - Uses shared `_list_creatives_impl`
- ✅ `update_media_buy` - Uses shared `_update_media_buy_impl`
- ✅ `update_performance_index` - Uses shared `_update_performance_index_impl`
- ✅ `list_creative_formats` - Uses shared `_list_creative_formats_impl`
- ✅ `list_authorized_properties` - Uses shared `_list_authorized_properties_impl`

**All 8 AdCP tools now use shared implementations - NO code duplication!**

## 🚨 CRITICAL REMINDERS

### Deployment Architecture (Reference Implementation)
**NOTE**: This codebase can be hosted anywhere (Docker, Kubernetes, cloud providers, bare metal). The reference implementation uses:

**TWO SEPARATE ENVIRONMENTS:**
- **Local Dev**: `docker-compose up/down` → localhost:8001/8080/8091
- **Reference Production**: `fly deploy` → https://adcp-sales-agent.fly.dev
- ⚠️ **Docker and Fly.io are INDEPENDENT** - starting Docker does NOT affect production!

**Your Deployment**: You can host this anywhere that supports:
- Docker containers (recommended)
- Python 3.11+
- PostgreSQL (production) or SQLite (dev/testing)
- We'll support your deployment approach as best we can

### Git Workflow - MANDATORY (Reference Implementation)
**❌ NEVER PUSH DIRECTLY TO MAIN**

1. Work on feature branches: `git checkout -b feature/name`
2. Push branch and create PR: `gh pr create`
3. Wait for review and merge via GitHub UI
4. **Merging to main auto-deploys to reference production via Fly.io**

## Project Overview

Python-based AdCP V2.3 sales agent reference implementation with:
- **MCP Server** (8080): FastMCP-based tools for AI agents
- **Admin UI** (8001): Google OAuth secured web interface
- **A2A Server** (8091): Standard python-a2a agent-to-agent communication
- **Multi-Tenant**: Database-backed isolation with subdomain routing
- **Production Ready**: PostgreSQL, Docker deployment, health monitoring

## Key Architecture Principles

### 1. AdCP Protocol Compliance - MANDATORY
**🚨 CRITICAL**: All client-facing models MUST be AdCP spec-compliant and tested.

**Requirements:**
- Response models include ONLY AdCP spec-defined fields
- Use exact field names from AdCP schema
- Each model needs AdCP contract test in `tests/unit/test_adcp_contract.py`
- Use `model_dump()` for external responses, `model_dump_internal()` for database

**When Adding New Models:**
1. Check AdCP spec at https://adcontextprotocol.org/docs/
2. Add compliance test BEFORE implementing
3. Test with minimal and full field sets
4. Verify no internal fields leak

**🚨 NEVER ADD FIELDS TO REQUEST/RESPONSE SCHEMAS WITHOUT SPEC VERIFICATION:**
- **Before adding ANY field to a Request/Response model**, verify it exists in the AdCP spec
- **Do NOT bypass pre-commit hooks with `--no-verify`** unless absolutely necessary
- **If you must use `--no-verify`**, manually run schema validation checks:
  ```bash
  # Run schema validation manually
  .git/hooks/pre-commit
  # Or specific checks:
  pre-commit run verify-adcp-schema-sync --all-files
  pre-commit run audit-required-fields --all-files
  ```
- **Example of WRONG approach**: Adding `creative_ids` to `CreateMediaBuyRequest` top-level
  - ❌ NOT in AdCP spec - belongs in `Package.creative_ids`
  - ✅ Correct: Use existing spec-compliant `Package.creative_ids`

**Common mistakes:**
- Adding "convenience" fields that seem useful but aren't in spec
- Assuming client's usage pattern means we should add fields
- Using `--no-verify` and not checking schema compliance manually

See `docs/testing/adcp-compliance.md` for detailed test patterns.

### 2. Admin UI Route Architecture
**⚠️ DEBUGGING TIP**: Routes split between blueprints:
- `settings.py`: POST operations for tenant settings
- `tenants.py`: GET requests for tenant settings pages

Route mapping:
```
/admin/tenant/{id}/settings         → tenants.py::tenant_settings() (GET)
/admin/tenant/{id}/settings/adapter → settings.py::update_adapter() (POST)
```

### 3. Multi-Tenancy & Adapters
- **Tenant Isolation**: Each publisher is isolated with own data
- **Principal System**: Advertisers have unique tokens per tenant
- **Adapter Pattern**: Base class with GAM, Kevel, Triton, Mock implementations
- **GAM Modular**: 250-line orchestrator delegating to specialized managers

### 4. Unified Workflow System
- **Single Source**: All work tracking uses `WorkflowStep` tables
- **No Task Models**: Eliminated deprecated `Task`/`HumanTask` (caused schema conflicts)
- **Dashboard**: Shows workflow activity instead of generic tasks

### 5. Protocol Support
- **MCP**: FastMCP with header-based auth (`x-adcp-auth`)
- **A2A**: Standard `python-a2a` library (no custom protocol code)
- **Testing Backend**: Full AdCP testing spec implementation

## Core Components

```
src/core/
  ├── main.py          # FastMCP server + AdCP tools
  ├── schemas.py       # Pydantic models (AdCP-compliant)
  └── database/        # Multi-tenant schema

src/adapters/
  ├── base.py          # Abstract adapter interface
  ├── mock_ad_server.py
  └── gam/             # Modular GAM implementation

src/admin/           # Flask admin UI
src/a2a_server/      # python-a2a server implementation
```

## Configuration

### Secrets (.env.secrets file - REQUIRED)
**🔒 Security**: All secrets MUST be in `.env.secrets` file (no env vars).

```bash
# API Keys
GEMINI_API_KEY=your-key

# OAuth (Admin UI)
GOOGLE_CLIENT_ID=your-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-secret
SUPER_ADMIN_EMAILS=user@example.com

# GAM OAuth
GAM_OAUTH_CLIENT_ID=your-gam-id.apps.googleusercontent.com
GAM_OAUTH_CLIENT_SECRET=your-gam-secret
```

**Note**: Ports auto-configured by workspace setup script.

### Database Schema
```sql
-- Core multi-tenant tables
tenants, principals, products, media_buys, creatives, audit_logs

-- Workflow system (unified)
workflow_steps, object_workflow_mappings

-- Legacy (deprecated)
-- tasks, human_tasks (DO NOT USE)
```

## Common Operations

### Running Locally
```bash
# Start all services
docker-compose up -d

# View logs
docker-compose logs -f

# Stop
docker-compose down
```

### Testing
```bash
# Run all tests
uv run pytest

# By category
uv run pytest tests/unit/
uv run pytest tests/integration/
uv run pytest tests/e2e/

# With coverage
uv run pytest --cov=. --cov-report=html

# AdCP compliance (MANDATORY before commit)
uv run pytest tests/unit/test_adcp_contract.py -v
```

### Database Migrations
```bash
# Run migrations
uv run python migrate.py

# Create new migration
uv run alembic revision -m "description"
```

**⚠️ NEVER modify existing migrations after commit!**

## Development Best Practices

### Code Style
- Use `uv` for dependencies
- Run pre-commit: `pre-commit run --all-files`
- Use type hints
- **🚨 NO hardcoded external system IDs** - use config/database
- **🛡️ NO testing against production systems**

### Schema Design
**Field Requirement Analysis:**
- Can this have a sensible default?
- Would clients expect this optional?
- Good defaults: `brief: str = ""`, `platforms: str = "all"`

### Import Patterns
```python
# ✅ Always use absolute imports
from src.core.schemas import Principal
from src.core.database.database_session import get_db_session
from src.adapters import get_adapter
```

### Database Patterns
- Use context managers for sessions
- Explicit commits: `session.commit()`
- Use `attributes.flag_modified()` for JSONB updates

### ⛔ NO QUIET FAILURES
**CRITICAL**: NEVER silently skip requested features.

```python
# ❌ WRONG - Silent failure
if not self.supports_device_targeting:
    logger.warning("Skipping...")

# ✅ CORRECT - Explicit failure
if not self.supports_device_targeting and targeting.device_type_any_of:
    raise TargetingNotSupportedException("Cannot fulfill buyer contract.")
```

## Testing Guidelines

### Test Organization
```
tests/
├── unit/          # Fast, isolated (mock external deps only)
├── integration/   # Database + services (real DB, mock external APIs)
├── e2e/          # Full system tests
└── ui/           # Admin UI tests
```

### Quality Enforcement
**🚨 Pre-commit hooks enforce:**
- Max 10 mocks per test file
- AdCP compliance tests for all client-facing models
- MCP contract validation (minimal params work)
- No skipped tests (except `skip_ci`)
- No `.fn()` call patterns

### Critical Testing Patterns
1. **MCP Tool Roundtrip**: Test with real Pydantic objects, not mock dicts
2. **AdCP Compliance**: Every client model needs contract test
3. **Integration over Mocking**: Use real DB, mock only external services
4. **Test What You Import**: If imported, test it's callable
5. **Never Skip or Weaken Tests**: Fix the underlying issue, never bypass with `skip_ci` or `--no-verify`

**🚨 MANDATORY**: When CI tests fail, FIX THE TESTS PROPERLY. Skipping or weakening tests to make CI pass is NEVER acceptable. The tests exist to catch real issues - if they fail, there's a problem that needs fixing, not hiding.

### Testing Workflow - MANDATORY for Refactoring

**⚠️ CRITICAL**: Pre-commit hooks can't catch import errors or integration issues. You MUST run the full test suite for refactorings.

#### Before Committing Code Changes

**For ALL changes:**
```bash
# 1. Run unit tests (fast, always required)
uv run pytest tests/unit/ -x

# 2. Verify imports work (catches missing imports)
python -c "from src.core.tools import get_products_raw"  # Example
python -c "from src.core.main import _get_products_impl"  # For impl functions
```

**For refactorings (shared implementation, moving code, import changes):**
```bash
# 3. Run integration tests (REQUIRED - catches real bugs)
uv run pytest tests/integration/ -x

# 4. Run specific A2A/MCP tests if you changed those paths
uv run pytest tests/integration/test_a2a*.py -x
uv run pytest tests/integration/test_mcp*.py -x
```

**For critical changes (protocol changes, schema updates):**
```bash
# 5. Run full suite including e2e
uv run pytest tests/ -x
```

#### Why This Matters

**Unit tests alone are NOT enough because:**
- ✅ Unit tests pass with mocked imports (don't catch missing imports)
- ✅ Unit tests don't execute real code paths (don't catch integration bugs)
- ✅ Unit tests use fake data (don't catch validation issues)

**Real example from this codebase:**
```python
# Refactored get_products_raw to use GetProductsRequest
# Unit tests: PASSED ✓ (they mock everything)
# Integration tests: FAILED ✗ (import GetProductsRequest was missing)
# CI caught it after 2 failed runs
```

#### Pre-Push Validation

**⚠️ RECOMMENDED WORKFLOW:**
```bash
# 1. Before pushing - run CI mode (catches database issues)
./run_all_tests.sh ci        # ~3-5 min, exactly like GitHub Actions

# 2. Push - quick mode runs automatically
git push                      # ~1 min validation, blocks if tests fail
```

**Test Modes:**

**CI Mode (RECOMMENDED before pushing):**
- Starts PostgreSQL container automatically (postgres:15)
- Runs ALL tests including database-dependent tests
- Exactly matches GitHub Actions environment
- Catches issues before CI does
- Automatically cleans up container

**Quick Mode (runs automatically on push):**
- Fast validation: unit tests + integration tests (no database)
- Pre-push hook uses this mode
- Blocks push if tests fail (override with `git push --no-verify`)

**Full Mode (comprehensive, no Docker):**
- All tests with SQLite instead of PostgreSQL
- Good for development without Docker

**Command Reference:**
```bash
./run_all_tests.sh ci      # Like CI (PostgreSQL container) - USE THIS!
./run_all_tests.sh quick   # Fast pre-push validation (automatic)
./run_all_tests.sh full    # Full suite (SQLite, no Docker)
```

See `docs/testing/` for detailed patterns and case studies.

## Pre-Commit Hooks

```bash
# Run all checks
pre-commit run --all-files

# Specific checks
pre-commit run no-excessive-mocking --all-files
pre-commit run adcp-contract-tests --all-files
```

**When hooks fail**: Fix the issue, don't bypass with `--no-verify`.

## Deployment

### Hosting Options
This application can be hosted anywhere:
- **Docker** (recommended) - Works on any Docker-compatible platform
- **Kubernetes** - Full k8s manifests supported
- **Cloud Providers** - AWS, GCP, Azure, DigitalOcean, etc.
- **Bare Metal** - Direct Python deployment with systemd/supervisor
- **Platform Services** - Fly.io, Heroku, Railway, Render, etc.

See `docs/deployment.md` for platform-specific guides.

### Reference Implementation Deployment (Fly.io)
**🚨 For reference implementation maintainers: Fly.io auto-deploys from main branch**

```bash
# Check status
fly status --app adcp-sales-agent

# View logs
fly logs --app adcp-sales-agent

# Manual deploy (rarely needed)
fly deploy --app adcp-sales-agent

# Update secrets
fly secrets set GEMINI_API_KEY="key" --app adcp-sales-agent
```

### Deployment Checklist (General)
1. ✅ Create feature branch
2. ✅ Test locally: `docker-compose up`
3. ✅ Run tests: `uv run pytest`
4. ✅ Check migrations: `uv run python migrate.py`
5. ✅ Create Pull Request (if contributing to reference implementation)
6. ✅ Deploy to your environment
7. ✅ Monitor logs and health endpoints
8. ✅ Verify database migrations ran successfully

## Documentation

For detailed information, see:
- **Architecture**: `docs/ARCHITECTURE.md`
- **Setup**: `docs/SETUP.md`
- **Development**: `docs/DEVELOPMENT.md`
- **Testing**: `docs/testing/`
- **Troubleshooting**: `docs/TROUBLESHOOTING.md`
- **Security**: `docs/security.md`
- **A2A Implementation**: `docs/a2a-implementation-guide.md`
- **Deployment**: `docs/deployment.md`

## Quick Reference

### MCP Client Example
```python
from fastmcp.client import Client, StreamableHttpTransport

headers = {"x-adcp-auth": "token"}
transport = StreamableHttpTransport(url="http://localhost:8080/mcp/", headers=headers)
client = Client(transport=transport)

async with client:
    products = await client.tools.get_products(brief="video ads")
    result = await client.tools.create_media_buy(product_ids=["prod_1"], ...)
```

### A2A Server Pattern
```python
# ✅ ALWAYS use create_flask_app() for A2A servers
from python_a2a.server.http import create_flask_app
app = create_flask_app(agent)  # Provides all standard endpoints
```

### Admin UI Access
```bash
# Local: http://localhost:8001
# Reference Production: https://admin.sales-agent.scope3.com
# Your Production: Configure based on your hosting setup
```

## Support

- Check documentation in `/docs`
- Review test examples in `/tests`
- Consult adapter implementations in `/src/adapters`
