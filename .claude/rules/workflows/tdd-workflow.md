# TDD Workflow

## Red-Green-Refactor Cycle

### 1. Red — Write Failing Test

Before writing implementation code:
1. Understand requirements from beads task + CLAUDE.md patterns
2. Write a test that describes the desired behavior
3. Run it and confirm it fails:

```bash
uv run pytest tests/unit/test_<area>.py::test_<name> -x -v
```

**Test organization:**
- `tests/unit/` — Fast, isolated (mock external deps only)
- `tests/integration/` — Real PostgreSQL database
- `tests/e2e/` — Full system tests

### 2. Green — Make It Pass

Write the minimum code to make the test pass:
- Follow CLAUDE.md critical patterns
- Use existing patterns from surrounding code
- Don't add extras not covered by tests

```bash
uv run pytest tests/unit/test_<area>.py::test_<name> -x -v
```

### 3. Refactor — Clean Up

With passing tests as safety net:
- Remove duplication
- Improve naming
- Simplify logic
- Ensure CLAUDE.md patterns are followed

```bash
make quality  # Full quality gate after refactoring
```

## Requirements Sources

For salesagent, requirements come from:
1. **Beads task description** — acceptance criteria
2. **CLAUDE.md** — 7 critical architecture patterns
3. **AdCP spec** — protocol compliance (`tests/unit/test_adcp_contract.py`)
4. **Existing test patterns** — conventions in `tests/unit/`

## Sacred Rule

**NEVER adjust tests to match code.**

If a test fails after implementation:
- The implementation is wrong, OR
- The test requirements were wrong (update requirements first, then test, then code)

Tests define the contract. Code fulfills it.

## Common Test Patterns

### Unit Test (Mock External Deps)
```python
def test_something():
    with patch('src.core.database.database_session.get_db_session') as mock_db:
        # Test with mocked database
        pass
```

### Integration Test (Real PostgreSQL)
```python
@pytest.mark.requires_db
def test_something(integration_db):
    with get_db_session() as session:
        # Test with real PostgreSQL
        pass
```

### AdCP Compliance
```python
# Run after any schema changes
uv run pytest tests/unit/test_adcp_contract.py -v
```

## Quality Rules
- Max 10 mocks per test file
- Test YOUR code, not Python built-ins
- Roundtrip test required for `apply_testing_hooks()`
- Never use `skip_ci` without explicit justification
