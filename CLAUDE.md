# AdCP Sales Agent - Development Guide

## 🤖 For Claude (AI Assistant)

This guide helps you work effectively with the AdCP sales agent codebase. Key principles:

### Working with This Codebase
1. **Always read before writing** - Use Read/Glob to understand existing patterns
2. **Test your changes** - Run `uv run pytest tests/unit/ -x` before committing
3. **Follow the patterns** - 7 critical patterns below are non-negotiable
4. **When stuck** - Check `/docs` for detailed explanations
5. **Pre-commit hooks are your friend** - They catch most issues automatically

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

---

## 🚨 Critical Architecture Patterns

### 1. AdCP Schema: Extend Library Schemas
**MANDATORY**: Use `adcp` library schemas via inheritance, never duplicate.

```python
from adcp.types import Product as LibraryProduct

class Product(LibraryProduct):
    """Extends library Product with internal-only fields."""
    implementation_config: dict[str, Any] | None = Field(default=None, exclude=True)
```

**Rules:**
- Extend library schemas for domain objects needing internal fields
- Mark internal fields with `exclude=True`
- Run `pytest tests/unit/test_adcp_contract.py` before commit
- Never bypass `--no-verify` without manual schema validation

### 2. Flask: Prevent Route Conflicts
**Pre-commit hook detects duplicate routes** - Run manually: `uv run python .pre-commit-hooks/check_route_conflicts.py`

When adding routes:
- Search existing: `grep -r "@.*route.*your/path"`
- Deprecate properly with early return, not comments

### 3. Database: PostgreSQL Only
**No SQLite support** - Production uses PostgreSQL exclusively.

- Use `JSONType` for all JSON columns (not plain `JSON`)
- Use SQLAlchemy 2.0 patterns: `select()` + `scalars()`, not `query()`
- All tests require PostgreSQL: `./run_all_tests.sh ci`

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

### 5. MCP/A2A: Shared Implementations
All tools use shared `_tool_name_impl()` function called by both MCP and A2A paths.

```python
# main.py
def _create_media_buy_impl(...) -> CreateMediaBuyResponse:
    # Real implementation
    return response

@mcp.tool()
def create_media_buy(...) -> CreateMediaBuyResponse:
    return _create_media_buy_impl(...)

# tools.py
def create_media_buy_raw(...) -> CreateMediaBuyResponse:
    from src.core.main import _create_media_buy_impl
    return _create_media_buy_impl(...)
```

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

---

## Project Overview

Python-based AdCP sales agent with:
- **MCP Server** (8080): FastMCP tools for AI agents
- **Admin UI** (8001): Google OAuth secured interface
- **A2A Server** (8091): python-a2a agent-to-agent communication
- **Multi-Tenant**: Database-backed isolation with subdomain routing
- **PostgreSQL**: Production-ready with Docker deployment

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
docker-compose up -d      # Start all services
docker-compose logs -f    # View logs
docker-compose down       # Stop
```

### Testing
```bash
./run_all_tests.sh ci     # Full suite with PostgreSQL (matches CI)
./run_all_tests.sh quick  # Fast iteration (skips database tests)

# Manual pytest
uv run pytest tests/unit/              # Unit tests only
uv run pytest tests/integration/       # Integration tests
uv run pytest tests/e2e/               # E2E tests

# AdCP compliance (MANDATORY before commit)
uv run pytest tests/unit/test_adcp_contract.py -v
```

### Database Migrations
```bash
uv run python migrate.py                    # Run migrations
uv run alembic revision -m "description"    # Create migration
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
- Never skip tests - fix the issue (`skip_ci` for rare exceptions only)
- Roundtrip test required for any operation using `apply_testing_hooks()`

### Testing Workflow (Before Commit)
```bash
# ALL changes
uv run pytest tests/unit/ -x
python -c "from src.core.tools import your_import"  # Verify imports

# Refactorings (shared impl, moving code, imports)
uv run pytest tests/integration/ -x

# Critical changes (protocol, schema updates)
uv run pytest tests/ -x
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

### Three Environments
- **Local Dev**: `docker-compose up` → localhost:8001/8080/8091
- **Reference Sales Agent**: Fly.io → https://adcp-sales-agent.fly.dev (auto-deploys from main)
- **Test Buyer**: https://testing.adcontextprotocol.org/ (OUR production tenant with mock adapter)

**All three are INDEPENDENT** - Docker doesn't affect production.

### Git Workflow (MANDATORY)
**Never push directly to main**

1. Work on feature branches: `git checkout -b feature/name`
2. Create PR: `gh pr create`
3. Merge via GitHub UI → auto-deploys to Fly.io

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
from fastmcp.client import Client, StreamableHttpTransport

headers = {"x-adcp-auth": "token"}
transport = StreamableHttpTransport(url="http://localhost:8080/mcp/", headers=headers)
client = Client(transport=transport)

async with client:
    products = await client.tools.get_products(brief="video ads")
    result = await client.tools.create_media_buy(product_ids=["prod_1"], ...)
```

### A2A Server
```python
from python_a2a.server.http import create_flask_app
app = create_flask_app(agent)  # Provides all standard endpoints
```

### Admin UI
- Local: http://localhost:8001
- Production: Configure based on your hosting

---

## Decision Tree for Claude

**User asks to add a new feature:**
1. Search existing code: `Glob` for similar features
2. Read relevant files to understand patterns
3. Design solution following critical patterns
4. Write tests first (TDD)
5. Implement feature
6. Run tests: `uv run pytest tests/unit/ -x`
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
3. Run tests after each change: `uv run pytest tests/unit/ -x`
4. For import changes, verify: `python -c "from module import thing"`
5. For shared implementations, run integration tests: `uv run pytest tests/integration/ -x`

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
