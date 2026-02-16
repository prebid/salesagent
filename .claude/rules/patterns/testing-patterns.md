# Testing Patterns

Reference patterns for writing tests. Read this when adding or modifying tests.

## Test Organization
- **tests/unit/**: Fast, isolated (mock external deps only)
- **tests/integration/**: Real PostgreSQL database
- **tests/e2e/**: Full system tests
- **tests/ui/**: Admin UI tests

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
uv run pytest tests/integration/ -x

# Critical changes (protocol, schema updates)
uv run pytest tests/ -x
```

**Pre-commit hooks can't catch import errors** - You must run tests for refactorings!

## Also See
- `.claude/rules/workflows/tdd-workflow.md` — Red-Green-Refactor cycle
- `.claude/rules/workflows/quality-gates.md` — Quality gate commands
