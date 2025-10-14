# AdCP Sales Agent Server - Development Guide

## 🚨 CRITICAL ARCHITECTURE PATTERNS

### AdCP Schema Source of Truth
**🚨 MANDATORY**: The official AdCP specification at https://adcontextprotocol.org/schemas/v1/ is the **SINGLE SOURCE OF TRUTH** for all API schemas.

**Schema Hierarchy:**
1. **Official Spec** (https://adcontextprotocol.org/schemas/v1/) - Primary source of truth
2. **Cached Schemas** (`tests/e2e/schemas/v1/`) - Checked into git for offline validation
3. **Pydantic Schemas** (`src/core/schemas.py`) - MUST match official spec exactly

**Rules:**
- ✅ Always verify against official AdCP spec when adding/modifying schemas
- ✅ Use `tests/e2e/adcp_schema_validator.py` to validate responses
- ✅ Run `pytest tests/unit/test_adcp_contract.py` to check Pydantic schema compliance
- ❌ NEVER add fields not in the official spec
- ❌ NEVER make required fields optional (or vice versa) without spec verification
- ❌ NEVER bypass AdCP contract tests with `--no-verify`

**When schemas don't match:**
1. Check official spec: `https://adcontextprotocol.org/schemas/v1/media-buy/[operation].json`
2. Update Pydantic schema in `src/core/schemas.py` to match
3. Update cached schemas if official spec changed: Re-run schema validator
4. If spec is wrong, file issue with AdCP maintainers, don't work around it locally

**Schema Update Process:**
```bash
# Check official schemas (they auto-download and cache)
pytest tests/e2e/test_adcp_compliance.py -v

# Validate all Pydantic schemas match spec
pytest tests/unit/test_adcp_contract.py -v

# If schemas are out of date, cached files are auto-updated on next run
# Commit any schema file changes that appear in tests/e2e/schemas/v1/
```

**Current Schema Version:**
- AdCP Version: v2.4
- Schema Version: v1
- Last Verified: 2025-09-02
- Source: https://adcontextprotocol.org/schemas/v1/index.json

---

### PostgreSQL-Only Architecture
**🚨 DECISION**: This codebase uses **PostgreSQL exclusively**. We do NOT support SQLite.

**Why:**
- Production uses PostgreSQL exclusively
- SQLite hides bugs (different JSONB behavior, no connection pooling, single-threaded)
- "No fallbacks - if it's in our control, make it work" (core principle)
- One database. One source of truth. No hidden bugs.

**What this means:**
- ✅ All database code assumes PostgreSQL (JSONB, connection pooling, etc.)
- ✅ All tests require PostgreSQL container (run via `./run_all_tests.sh ci`)
- ✅ Alembic migrations use PostgreSQL-specific syntax
- ❌ NO SQLite support - don't add it, don't test for it
- ❌ NO cross-database compatibility code - keep it simple

**If you see SQLite references:**
- Test files (`tests/smoke/`, `tests/unit/test_json_type.py`) - Legacy tests, ignore or delete
- Simulation tools (`tools/simulations/`) - Uses temp PostgreSQL, not SQLite
- Documentation - Outdated, needs removal

---

### Schema Validation Modes (Environment-Based)
**🚨 CRITICAL**: Schema validation strictness changes based on `ENVIRONMENT` variable.

**The Problem:**
Strict schema validation (`extra="forbid"`) makes production fragile:
- Clients using newer schema versions get rejected
- Rolling updates require perfect coordination
- Forward compatibility breaks
- Production failures from harmless extra fields

**The Solution: Environment-Based Validation**
```bash
# .env
ENVIRONMENT=production  # Lenient: extra="ignore" (forward compatible)
ENVIRONMENT=development  # Strict: extra="forbid" (catches bugs early)
ENVIRONMENT=staging     # Strict: extra="forbid" (catches bugs early)
```

**Behavior:**
- **Production** (`ENVIRONMENT=production`):
  - `extra="ignore"` - Unknown fields are silently dropped
  - Clients can send future schema fields (forward compatible)
  - Graceful degradation, no production failures
  - Example: Client sends `adcp_version="1.8.0"` from v1.8 schema → accepted

- **Development/Staging** (default):
  - `extra="forbid"` - Unknown fields raise validation errors
  - Catches typos and bugs during development
  - Enforces schema compliance in tests
  - Example: Client sends `unknown_field="test"` → validation error

**Implementation:**
All AdCP request/response models inherit from `AdCPBaseModel`:

```python
from src.core.schemas import AdCPBaseModel

class CreateMediaBuyRequest(AdCPBaseModel):  # Uses environment-aware validation
    buyer_ref: str
    packages: list[Package]
    # ... fields
```

**Testing:**
```bash
# Test validation modes
uv run pytest tests/unit/test_schema_validation_modes.py -v

# Production mode accepts extra fields
ENVIRONMENT=production pytest tests/unit/test_create_media_buy.py

# Development mode rejects extra fields (default)
pytest tests/unit/test_create_media_buy.py
```

**When to Use Each Mode:**
- ✅ **Production**: Always use `ENVIRONMENT=production` (forward compatible)
- ✅ **Staging**: Use `ENVIRONMENT=staging` (catch issues before prod)
- ✅ **Development**: Default (no env var) (strict validation)
- ✅ **CI**: Default (strict validation catches bugs)

**Why This Matters:**
1. **Forward Compatibility**: Clients using newer schemas don't break production
2. **Rolling Updates**: Can deploy server/client independently
3. **Development Safety**: Strict validation catches bugs in tests
4. **Production Stability**: No failures from harmless extra fields

---

### Database JSON Fields Pattern (SQLAlchemy 2.0)
**🚨 MANDATORY**: All JSON columns MUST use `JSONType` for PostgreSQL JSONB handling.

**The Solution: JSONType**
We have a custom `JSONType` TypeDecorator for PostgreSQL JSONB:

```python
# ✅ CORRECT - Use JSONType for ALL JSON columns
from src.core.database.json_type import JSONType

class MyModel(Base):
    __tablename__ = "my_table"

    # Use JSONType, not JSON
    config = Column(JSONType, nullable=False, default=dict)
    tags = Column(JSONType, nullable=True)
    metadata = Column(JSONType)
```

**❌ WRONG - Never use plain JSON type:**
```python
from sqlalchemy import JSON

class MyModel(Base):
    config = Column(JSON)  # ❌ Will cause bugs!
```

**In Application Code:**
```python
# ✅ CORRECT - JSONType handles everything automatically
with get_db_session() as session:
    model = session.query(MyModel).first()

    # Always receive dict/list, never string
    assert isinstance(model.config, dict)
    assert isinstance(model.tags, (list, type(None)))

    # No manual json.loads() needed!
    config_value = model.config.get("key")

# ❌ WRONG - Manual JSON parsing (old pattern)
import json
config = json.loads(model.config) if isinstance(model.config, str) else model.config
```

**Migration Strategy:**
1. **New code**: ALWAYS use `JSONType` for new JSON columns
2. **Existing code**: Convert to `JSONType` when you touch it
3. **No manual parsing**: Remove any `json.loads()` calls when converting

**mypy + SQLAlchemy Plugin Compatibility:**
JSONType is fully compatible with mypy's SQLAlchemy plugin:

```python
from typing import Optional
from sqlalchemy.orm import Mapped, mapped_column

class MyModel(Base):
    __tablename__ = "my_table"

    # mypy-compatible syntax (SQLAlchemy 2.0 style)
    config: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    tags: Mapped[Optional[list]] = mapped_column(JSONType, nullable=True)
```

**Error Handling:**
JSONType uses fail-fast error handling:
- **Invalid JSON in database** → Raises `ValueError` (data corruption alert)
- **Non-dict/list types being stored** → Converts to `{}` with warning
- **Unexpected database types** → Raises `TypeError`

This ensures data corruption is detected immediately, not hidden.

**Current Status:**
- ✅ All 45 JSON columns in models.py use JSONType
- ✅ 30 comprehensive unit tests
- ✅ PostgreSQL fast-path optimization (~20% performance improvement)
- ✅ Production-ready with robust error handling

**References:**
- Implementation: `src/core/database/json_type.py`
- Tests: `tests/unit/test_json_type.py`
- SQLAlchemy docs: [TypeDecorator](https://docs.sqlalchemy.org/en/20/core/custom_types.html#typedecorator)

---

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

**THREE SEPARATE ENVIRONMENTS:**
- **Local Dev**: `docker-compose up/down` → localhost:8001/8080/8091
- **Reference Sales Agent**: `fly deploy` → https://adcp-sales-agent.fly.dev
  - This is OUR sales agent (publisher side)
  - Hosted on Fly.io, auto-deploys from main branch
- **Test Buyer Agent**: https://test-agent.adcontextprotocol.org
  - This is OUR test advertiser agent (buyer side)
  - Also hosted on Fly.io by us
  - Used for E2E testing of the complete AdCP flow
  - When this is down, it affects our integration tests
  - Check logs: `fly logs --app test-agent` (exact app name may vary)

⚠️ **All three are INDEPENDENT** - starting Docker does NOT affect production!

**Your Deployment**: You can host this anywhere that supports:
- Docker containers (recommended)
- Python 3.11+
- PostgreSQL (production and testing)
- We'll support your deployment approach as best we can

**When Test Agent is Down:**
- Check Fly.io logs first: `fly logs --app <test-agent-app-name>`
- Check Fly.io status: `fly status --app <test-agent-app-name>`
- Don't assume external infrastructure issue - we control both sides
- Check application logs before assuming network/external issues

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

### 1. SQLAlchemy 2.0 Query Patterns - MANDATORY
**🚨 CRITICAL**: Use SQLAlchemy 2.0 patterns for all database queries. Legacy 1.x patterns are deprecated.

**Migration Status:**
- ✅ Stage 1 & 2 Complete: Infrastructure and helper functions migrated
- 🔄 In Progress: ~114 files with legacy patterns remain
- 🎯 Goal: All new code uses 2.0 patterns; convert legacy code as touched

**Correct Pattern (SQLAlchemy 2.0):**
```python
from sqlalchemy import select

# Single result
stmt = select(Model).filter_by(field=value)
instance = session.scalars(stmt).first()

# Multiple results
stmt = select(Model).filter_by(field=value)
results = session.scalars(stmt).all()

# With filter() instead of filter_by()
stmt = select(Model).where(Model.field == value)
instance = session.scalars(stmt).first()

# Complex queries with joins
stmt = select(Model).join(Other).where(Model.field == value)
results = session.scalars(stmt).all()
```

**❌ WRONG - Legacy Pattern (SQLAlchemy 1.x):**
```python
# DO NOT USE - Deprecated in 2.0
instance = session.query(Model).filter_by(field=value).first()
results = session.query(Model).filter_by(field=value).all()
```

**When to Migrate:**
1. **Always use 2.0 patterns for new code** - No exceptions
2. **Convert legacy code when touching it** - If you're editing a function with `session.query()`, convert it to `select()` + `scalars()`
3. **Helper functions available**: Use `get_or_404()` and `get_or_create()` from `database_session.py` (already migrated)

**Common Conversions:**
```python
# Pattern 1: Simple filter_by
- session.query(Model).filter_by(x=y).first()
+ stmt = select(Model).filter_by(x=y)
+ session.scalars(stmt).first()

# Pattern 2: filter() with conditions
- session.query(Model).filter(Model.x == y).first()
+ stmt = select(Model).where(Model.x == y)
+ session.scalars(stmt).first()

# Pattern 3: All results
- session.query(Model).filter_by(x=y).all()
+ stmt = select(Model).filter_by(x=y)
+ session.scalars(stmt).all()

# Pattern 4: Count
- session.query(Model).filter_by(x=y).count()
+ from sqlalchemy import func, select
+ stmt = select(func.count()).select_from(Model).where(Model.x == y)
+ session.scalar(stmt)
```

**See Also:**
- PR #307 - Stage 1 & 2 migration examples
- [SQLAlchemy 2.0 Docs](https://docs.sqlalchemy.org/en/20/changelog/migration_20.html)

### 2. AdCP Protocol Compliance - MANDATORY
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

### 6. Push Notification (Webhook) Registration
**Both MCP and A2A support webhook registration:**

- **MCP**: Pass `push_notification_config` parameter to `create_media_buy`
- **A2A**: Use `set_task_push_notification_config` endpoint (pushNotifications capability enabled)

**Implementation Notes:**
- Parameter extraction follows A2A spec (snake_case attributes via Pydantic)
- Authentication format: `schemes` array + `credentials` string (HMAC-SHA256)
- Database persistence in `push_notification_configs` table
- Tenant setup script creates default USD currency limit (required for media buy creation)

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

### Admin UI Settings Structure

**Tenant Settings** (http://localhost:8001/tenant/{id}/settings):

Settings are organized around clear conceptual buckets:

**🏢 Account** (Who You Are)
- Organization name and subdomain
- Virtual host configuration
- Domain/email access control

**🖥️ Ad Server** (Infrastructure)
- Adapter selection (GAM, Mock, Kevel)
- OAuth authentication
- Network codes and IDs
- Connection testing

**📋 Policies & Workflows** (How You Operate) ⭐ **NEW**
- **Budget Controls**: Max daily budget
- **Naming Conventions**: Order/line item templates with live preview
  - Works across all adapters (GAM, Mock, Kevel)
  - Quick-start presets (Simple, Campaign-First, Detailed)
  - Variable reference and validation
- **Approval Workflow**: Manual approval, human review
- **Features**: AXE signals, feature flags

**📊 Inventory** (Ad Server Data)
- Sync inventory from ad server
- Browse ad units and placements

**📦 Products** (Advertising Products)
- Product management and configuration

**👥 Advertisers** (Principals)
- Advertiser/principal management
- Platform mappings

**🔗 Integrations** (External Services)
- Slack webhooks
- Signals discovery agent
- API tokens

**⚠️ Danger Zone** (Destructive Operations)
- Delete tenant, reset data

**Key Changes (October 2025):**
- Naming templates moved from Ad Server to Policies & Workflows
- Budget controls moved from Account to Policies & Workflows
- Feature flags moved from Account to Policies & Workflows
- Settings reduced from 11 to 8 sections
- Naming templates now work across all adapters

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
# Recommended: Full test suite with PostgreSQL (matches CI/production)
./run_all_tests.sh ci

# Fast iteration during development (skips database tests)
./run_all_tests.sh quick

# Manual pytest commands
uv run pytest tests/unit/           # Unit tests only
uv run pytest tests/integration/    # Integration tests (needs database)
uv run pytest tests/e2e/            # E2E tests (needs database)

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

### Database Initialization Dependencies
**🚨 CRITICAL**: Products have implicit dependencies that MUST be satisfied before creation.

**Dependency Chain:**
```
Tenant → CurrencyLimit (required for budget validation)
      → PropertyTag (required for property_tags array references)
      → Products (require BOTH CurrencyLimit AND PropertyTag)
```

**Why These Dependencies Exist:**
1. **CurrencyLimit** (`currency_code="USD"`): Required for media buy budget validation
   - Products are used in media buys which validate budgets against currency limits
   - Missing currency limits cause "Must have at least one product" errors in E2E tests
   - **Fix**: Always create at least one CurrencyLimit when creating a tenant

2. **PropertyTag** (`tag_id="all_inventory"`): Required for property_tags array references
   - Per AdCP v2.4 spec, products MUST have either `properties` OR `property_tags` (oneOf constraint)
   - Most products use `property_tags=["all_inventory"]` as default
   - Missing property tags cause referential integrity errors
   - **Fix**: Always create at least one PropertyTag when creating a tenant

**Validation in Init Scripts:**
The `scripts/setup/init_database_ci.py` script now validates these prerequisites:
```python
# 1. Check CurrencyLimit exists
stmt_currency = select(CurrencyLimit).filter_by(tenant_id=tenant_id, currency_code="USD")
currency_limit = session.scalars(stmt_currency).first()
if not currency_limit:
    raise ValueError("Cannot create products: CurrencyLimit (USD) not found...")

# 2. Check PropertyTag exists
stmt_tag = select(PropertyTag).filter_by(tenant_id=tenant_id, tag_id="all_inventory")
property_tag = session.scalars(stmt_tag).first()
if not property_tag:
    raise ValueError("Cannot create products: PropertyTag 'all_inventory' not found...")
```

**When Creating New Tenants:**
1. Create Tenant
2. Create CurrencyLimit (at least USD)
3. Create PropertyTag (at least "all_inventory")
4. Create Products (can now safely reference currency and tags)

**Failure Symptoms:**
- ❌ "Must have at least one product" in E2E tests
- ❌ Products created but budget validation fails
- ❌ Referential integrity errors on property_tags array

## Development Best Practices

### Code Style
- Use `uv` for dependencies
- Run pre-commit: `pre-commit run --all-files`
- Use type hints
- **🚨 NO hardcoded external system IDs** - use config/database
- **🛡️ NO testing against production systems**

### Type Checking with mypy
**🚨 MANDATORY**: When touching files, fix mypy errors in the code you modify.

**Run mypy manually:**
```bash
# Check specific file
uv run mypy src/core/your_file.py --config-file=mypy.ini

# Check entire directory
uv run mypy src/core/ --config-file=mypy.ini

# Check all source files
uv run mypy src/ --config-file=mypy.ini
```

**When modifying code:**
1. **Run mypy on files you change** - Fix errors introduced by your changes
2. **Fix nearby errors if easy** - Opportunistically improve type safety
3. **Use SQLAlchemy 2.0 Mapped[] annotations** for new ORM models:
   ```python
   from sqlalchemy.orm import Mapped, mapped_column

   class MyModel(Base):
       # ✅ CORRECT - New SQLAlchemy 2.0 style
       id: Mapped[int] = mapped_column(primary_key=True)
       name: Mapped[str] = mapped_column(String(100))
       optional_field: Mapped[str | None] = mapped_column(nullable=True)
   ```

**Common Fixes:**
- Add type hints to function signatures
- Use `| None` instead of `Optional[]` (Python 3.10+)
- Fix `Sequence[Model]` vs `list[Model]` return types
- Add missing imports

**Current Status:**
- ✅ mypy installed with SQLAlchemy plugin
- ✅ Configuration in `mypy.ini` (lenient for incremental adoption)
- ⚠️ ~1313 errors remaining (down from 2644)
- 🎯 Goal: Fix errors as we touch files, gradually improve type safety

**Note**: Pre-commit hook disabled until error count is manageable. Run manually during development.

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

### What Makes a Good Unit Test

**✅ Good unit tests:**
- Test YOUR code's logic and behavior
- Use minimal mocking (only for external dependencies)
- Have clear, specific assertions about actual behavior
- Would catch real bugs if the implementation changed
- Test edge cases and error conditions
- Are fast (<100ms per test)

**❌ Bad unit tests (DELETE THESE):**
- Test Python's built-in behavior (`assert True`, `assert 1+1==2`)
- Test data structures without testing any code (`assert dict["key"] == "value"`)
- Define functions inside tests that aren't used (`def validate_x(): ...`)
- Have generic names like `test_basic_functionality()`
- Just check that imports work without testing behavior

**Examples:**

```python
# ❌ BAD - Testing Python itself
def test_basic_functionality():
    assert True

def test_list_operations():
    test_list = [1, 2, 3]
    assert len(test_list) == 3  # This tests Python, not our code!

# ✅ GOOD - Testing our code's behavior
def test_1x1_placeholder_accepts_any_creative_size():
    """1x1 placeholder should accept any creative size (wildcard behavior)."""
    manager = GAMCreativesManager(mock_client, "advertiser_123", dry_run=True)

    asset = {"creative_id": "c1", "width": 728, "height": 90}
    placeholders = {"pkg1": [{"size": {"width": 1, "height": 1}}]}

    errors = manager._validate_creative_size_against_placeholders(asset, placeholders)
    assert errors == []  # This tests OUR business logic!
```

### Critical Testing Patterns
1. **MCP Tool Roundtrip**: Test with real Pydantic objects, not mock dicts
2. **AdCP Compliance**: Every client model needs contract test
3. **Integration over Mocking**: Use real DB, mock only external services
4. **Test What You Import**: If imported, test it's callable
5. **Test YOUR Code**: Don't test Python, test your implementation
6. **Never Skip or Weaken Tests**: Fix the underlying issue, never bypass with `skip_ci` or `--no-verify`
7. **Roundtrip Tests for Testing Hooks**: Every operation using `apply_testing_hooks` MUST have a roundtrip test

**🚨 MANDATORY**: When CI tests fail, FIX THE TESTS PROPERLY. Skipping or weakening tests to make CI pass is NEVER acceptable. The tests exist to catch real issues - if they fail, there's a problem that needs fixing, not hiding.

### Testing Hooks Roundtrip Pattern (MANDATORY)

**Rule**: If an operation uses `apply_testing_hooks()`, it MUST have a roundtrip test.

**Why**: Testing hooks add extra fields (`is_test`, `dry_run`, `test_session_id`, `response_headers`) that can break response reconstruction.

**Pattern**:
```python
def test_{operation}_with_testing_hooks_roundtrip():
    """Test {Operation}Response survives apply_testing_hooks roundtrip."""
    # 1. Create valid response
    response = {Operation}Response(field1="value", field2="value")

    # 2. Convert to dict
    response_data = response.model_dump_internal()

    # 3. Apply testing hooks (adds extra fields)
    testing_ctx = TestingContext(dry_run=True, test_session_id="test")
    campaign_info = {"start_date": datetime.now(), "end_date": datetime.now(), "total_budget": 1000}
    modified_data = apply_testing_hooks(response_data, testing_ctx, "{operation}", campaign_info)

    # 4. Filter out testing hook fields
    valid_fields = {"field1", "field2", "field3"}  # Only schema fields
    filtered_data = {k: v for k, v in modified_data.items() if k in valid_fields}

    # 5. Reconstruct response - this MUST not raise validation error
    reconstructed = {Operation}Response(**filtered_data)

    # 6. Verify reconstruction
    assert reconstructed.field1 == "value"
```

**Examples**:
- `tests/integration/test_create_media_buy_roundtrip.py`
- `test_create_media_buy_response_survives_testing_hooks_roundtrip()`
- `test_get_media_buy_delivery_survives_testing_hooks_roundtrip()`

**Enforcement**: Pre-commit hook `check-roundtrip-tests` verifies all operations using `apply_testing_hooks` have roundtrip tests.

**When Adding Testing Hooks**:
1. Add `apply_testing_hooks` to your operation
2. Immediately write roundtrip test (before committing)
3. Pre-commit hook will fail if test is missing

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

**✅ AUTOMATIC CI MODE:**
The pre-push hook now automatically runs CI mode tests (with PostgreSQL) before every push.
This catches database-specific issues before they hit GitHub Actions.

```bash
# Pre-push hook automatically runs:
./run_all_tests.sh ci        # ~3-5 min, exactly like GitHub Actions

# To skip (not recommended):
git push --no-verify
```

**🔧 Setup Pre-Push Hook:**
If the hook isn't installed or you want to update it:
```bash
./scripts/setup/setup_hooks.sh
```

**Test Modes:**

**CI Mode (DEFAULT - runs automatically on push):**
- Starts PostgreSQL container automatically (postgres:15)
- Runs ALL tests including database-dependent tests
- Exactly matches GitHub Actions and production environment
- Catches database issues before CI does
- Automatically cleans up container
- ~3-5 minutes

**Quick Mode (for fast development iteration):**
- Fast validation: unit tests + integration tests (no database)
- Skips database-dependent tests (marked with `@pytest.mark.requires_db`)
- Good for rapid testing during development
- ~1 minute

**Command Reference:**
```bash
./run_all_tests.sh         # CI mode (default) - PostgreSQL container
./run_all_tests.sh ci      # CI mode (explicit) - USE THIS before pushing!
./run_all_tests.sh quick   # Quick mode - fast iteration
```

**Why PostgreSQL-only?**
- Production uses PostgreSQL exclusively
- SQLite hides bugs (different JSONB behavior, no connection pooling, single-threaded)
- "No fallbacks - if it's in our control, make it work" (core principle)
- One database. One source of truth. No hidden bugs.

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

## Adapter Pricing Model Support

### GAM Adapter
**Supported**: CPM only (both fixed and auction)
- Fixed CPM → GAM guaranteed orders
- Auction CPM → GAM non-guaranteed orders

**Not supported**: CPCV, CPP, CPC, CPV, flat_rate
- GAM supports these internally but adapter doesn't implement them yet
- Products with non-CPM pricing_options will work (validation passes) but adapter uses CPM

### Mock Adapter
**Supported**: All pricing models (CPM, CPCV, CPP, CPC, CPV, flat_rate)
- Both fixed and auction pricing
- All currencies
- Simulates appropriate metrics per pricing model

**Note**: Adapter UIs should allow creating pricing_options for products. Currently only legacy pricing fields are shown.

---

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
