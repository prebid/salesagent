# Testing Patterns

Reference patterns for writing tests. Read this when adding or modifying tests.

## Test Runner: tox + tox-uv

All test execution goes through **tox** for parallel execution and combined coverage.
Install: `uv tool install tox --with tox-uv`

```bash
# Quick
make quality                           # Format + lint + typecheck + unit tests
tox -e unit                            # Unit tests only (no Docker)

# Full suite (Docker + all 5 suites in parallel)
./run_all_tests.sh                     # One command: Docker up → tox -p → Docker down

# Manual Docker lifecycle (for iterating)
make test-stack-up                     # Start Docker, write .test-stack.env
source .test-stack.env && tox -p       # All suites in parallel
make test-stack-down                   # Tear down

# Targeted
tox -e integration -- -k test_name     # Pass pytest args after --
./run_all_tests.sh ci tests/integration/test_file.py -k test_name

# Coverage
make test-cov                          # Open htmlcov/index.html
```

## Test Organization
- **tests/unit/**: Fast, isolated (mock external deps only) — `tox -e unit`
- **tests/integration/**: Real PostgreSQL database — `tox -e integration`
- **tests/integration_v2/**: Real PostgreSQL database — `tox -e integration_v2`
- **tests/e2e/**: Full system tests (Docker stack) — `tox -e e2e`
- **tests/ui/**: Admin UI tests (Docker stack) — `tox -e ui`

## Database Fixtures
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

## Quality Rules
- Max 10 mocks per test file (pre-commit enforces)
- AdCP compliance test for all client-facing models
- Test YOUR code, not Python built-ins
- Never skip tests - fix the issue (`skip_ci` for rare exceptions only)
- Roundtrip test required for any operation using `apply_testing_hooks()`

## Testing Workflow (Before Commit)
```bash
# ALL changes
make quality

# Refactorings (shared impl, moving code, imports)
tox -e integration

# Critical changes (protocol, schema updates)
./run_all_tests.sh
```

**Pre-commit hooks can't catch import errors** - You must run tests for refactorings!

## Also See
- `.claude/rules/workflows/tdd-workflow.md` — Red-Green-Refactor cycle
- `.claude/rules/workflows/quality-gates.md` — Quality gate commands
