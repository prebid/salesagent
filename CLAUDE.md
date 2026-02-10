# Prebid Sales Agent - Development Guide

## Rules

1. **Always read before writing** - Use Read/Glob to understand existing patterns
2. **Test your changes** - Run `make quality` before committing
3. **Follow the 7 patterns below** - They are non-negotiable
4. **Doc-first** - Search docs via MCP servers (Ref, DeepWiki) before relying on training data for external libraries (FastMCP, SQLAlchemy, adcp, Flask, Pydantic, etc.)
5. **Pre-commit hooks are your friend** - They catch most issues automatically
6. **Conventional commits** - PRs need prefixes (`feat:`, `fix:`, `docs:`, `refactor:`, `perf:`, `chore:`) to pass `.github/workflows/pr-title-check.yml` and appear in release notes

### What to Avoid
- Don't use `session.query()` (use `select()` + `scalars()`)
- Don't duplicate library schemas (extend with inheritance)
- Don't hardcode URLs in JavaScript (use `scriptRoot`)
- Don't bypass pre-commit hooks without good reason
- Don't skip tests to make CI pass (fix the underlying issue)
- Never add `# noqa` comments without explaining why in a code comment

### Key Files
- `src/core/main.py` - MCP tools and `_impl()` functions
- `src/core/tools.py` - A2A raw functions
- `src/core/schemas.py` - Pydantic models (AdCP-compliant)
- `src/adapters/base.py` - Adapter interface
- `src/adapters/gam/` - GAM implementation
- `tests/unit/test_adcp_contract.py` - Schema compliance tests

### Common Task Patterns
- **Adding a new AdCP tool**: Extend library schema -> Add `_impl()` function -> Add MCP wrapper -> Add A2A raw function -> Add tests
- **Fixing a route issue**: Check for conflicts with `grep -r "@.*route.*your/path"` -> Use `url_for()` in Python, `scriptRoot` in JavaScript
- **Modifying schemas**: Verify against AdCP spec -> Update Pydantic model -> Run `pytest tests/unit/test_adcp_contract.py`
- **Database changes**: Use SQLAlchemy 2.0 `select()` -> Use `JSONType` for JSON -> Create migration with `alembic revision`

---

## Critical Architecture Patterns

### 1. AdCP Schema: Extend Library Schemas
**MANDATORY**: Use `adcp` library schemas via inheritance, never duplicate.

```python
from adcp.types import Product as LibraryProduct

class Product(LibraryProduct):
    """Extends library Product with internal-only fields."""
    implementation_config: dict[str, Any] | None = Field(default=None, exclude=True)
```

Rules: Extend library schemas for domain objects needing internal fields. Mark internal fields with `exclude=True`. Run `pytest tests/unit/test_adcp_contract.py` before commit.

### 2. Flask: Prevent Route Conflicts
Pre-commit hook detects duplicate routes. Run manually: `uv run python .pre-commit-hooks/check_route_conflicts.py`

### 3. Database: PostgreSQL Only
No SQLite support. Use `JSONType` for all JSON columns (not plain `JSON`). Use SQLAlchemy 2.0 patterns: `select()` + `scalars()`, not `query()`.

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

Why: Pydantic doesn't auto-call custom `model_dump()` on nested models.

### 5. MCP/A2A: Shared Implementations
All tools use shared `_tool_name_impl()` function called by both MCP and A2A paths. See `.claude/rules/patterns/mcp-patterns.md` for full example.

### 6. JavaScript: Use request.script_root
```javascript
const scriptRoot = '{{ request.script_root }}' || '';
const apiUrl = scriptRoot + '/api/endpoint';
```
Never hardcode `/api/endpoint` - breaks with nginx prefix.

### 7. Schema Validation: Environment-Based
- **Production**: `ENVIRONMENT=production` -> `extra="ignore"` (forward compatible)
- **Development/CI**: Default -> `extra="forbid"` (strict validation)

---

## Commands

```bash
make quality              # Format + lint + mypy + unit tests (before every commit)
make quality-full         # Above + integration/e2e with PostgreSQL
make lint-fix             # Auto-fix formatting and lint issues
make test-fast            # Unit tests only (fail-fast)
```

### Git Workflow
Never push directly to main. Work on feature branches, create PR, merge via GitHub UI.

### Database Migrations
```bash
uv run alembic revision -m "description"        # Create migration
uv run python scripts/ops/migrate.py            # Run migrations locally
```
Never modify existing migrations after commit.

---

## Decision Tree

**Adding a feature**: Search existing code -> Read patterns -> Design with critical patterns -> TDD (`.claude/rules/workflows/tdd-workflow.md`) -> `make quality` -> Commit

**Fixing a bug**: Read code path -> Write failing test (`.claude/rules/workflows/bug-reporting.md`) -> Fix -> `make quality` -> Commit

**Refactoring**: Verify tests pass -> Small incremental changes -> `make quality` after each -> For imports: `python -c "from module import thing"` -> For shared impl: `uv run pytest tests/integration/ -x`

**"How does X work?"**: `Grep` for code -> Read implementation -> Check `tests/unit/test_*X*.py` -> Explain with file:line references

---

## Self-Improvement

When something goes wrong (test failure you caused, pattern violation, rework):
1. Analyze what happened and why
2. Check if a CLAUDE.md rule or pattern would have prevented it
3. If yes, suggest the addition (do not modify CLAUDE.md without permission)

---

## Reference Docs

**Load on demand** — read these when working in the relevant area:

| When working on... | Read |
|---|---|
| Writing new code | `.claude/rules/patterns/code-patterns.md` |
| Writing tests | `.claude/rules/patterns/testing-patterns.md` |
| MCP/A2A tools | `.claude/rules/patterns/mcp-patterns.md` |
| Quality gates | `.claude/rules/workflows/quality-gates.md` |
| TDD workflow | `.claude/rules/workflows/tdd-workflow.md` |
| Bug fixes | `.claude/rules/workflows/bug-reporting.md` |
| Research | `.claude/rules/workflows/research-workflow.md` |
| Subagents | `.claude/rules/workflows/subagent-implementation-guide.md` |
| Adapters | `docs/adapters/` |
| Deployment | `docs/deployment/` |
| Architecture | `docs/development/architecture.md` |
| Setup | `docs/quickstart.md` |
| Troubleshooting | `docs/development/troubleshooting.md` |
