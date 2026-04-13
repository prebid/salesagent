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
- **tests/e2e/**: Full system tests (Docker stack) — `tox -e e2e`
- **tests/admin/**: Admin UI tests (Docker stack) — `tox -e admin`
- **tests/bdd/**: BDD behavioral tests — `tox -e bdd`

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
- Roundtrip test required for any operation using `apply_testing_hooks()`

## Test Integrity — ZERO TOLERANCE

**HARD STOP rules. No exceptions. No rationalizations.**

### Prohibited Actions
- **`--ignore`** — NEVER use to exclude test files
- **`-k "not test_name"`** — NEVER use to deselect failing tests
- **`--deselect`** — NEVER use to skip tests
- **`pytest.mark.skip` / `pytest.mark.xfail`** — NEVER add to bypass failures (stubs for unimplemented work are the only exception, managed by `/surface`)

### Prohibited Rationalizations
When a test fails, you must NOT say any of the following and continue:
- "This is a pre-existing failure"
- "This test needs a running server" (start the server)
- "This is an e2e test miscategorized in integration" (run it where it lives)
- "This was deselected in the full run" (irrelevant — it exists, it must pass)
- "This is an infrastructure issue" (fix the infrastructure or report it as a blocker)
- "Not a regression from our work" (irrelevant — all tests must pass)

### Required Action When Tests Fail
1. **Infrastructure missing?** → Start it. Use `./run_all_tests.sh` (starts everything), or `scripts/run-test.sh` (starts DB), or `make test-stack-up` (manual Docker lifecycle).
2. **Test bug?** → Fix the test (only if the test itself is wrong, not to match broken code).
3. **Code bug?** → Fix the code.
4. **Cannot fix?** → STOP. Report the failure to the user as a blocker. Do NOT skip it and report success.

### Test Infrastructure Decision Tree

| What you need | Command | What it starts |
|---------------|---------|----------------|
| Unit tests only | `make quality` | Nothing |
| One integration test | `scripts/run-test.sh tests/integration/test_foo.py -x` | Bare Postgres via agent-db |
| DB for worktree agent | `eval $(.claude/skills/agent-db/agent-db.sh up)` | Bare Postgres (unique port) |
| **Full suite (all 5 envs)** | **`./run_all_tests.sh`** | **Full Docker stack (auto-teardown)** |
| Full suite, targeted | `./run_all_tests.sh ci tests/path -k name` | Full Docker stack |
| Quick suite (no e2e/admin) | `./run_all_tests.sh quick` | Nothing (needs DATABASE_URL) |
| Entity-scoped | `make test-entity ENTITY=delivery` | Nothing (across all non-BDD suites) |

**Port conflicts are minimized** — port allocation checks match Docker's actual bind address, and ranges avoid the OS ephemeral port range. `test-stack.sh` and `agent-db.sh` scan 50000-60000; E2E conftest scans 20000-30000.

**When in doubt, use `./run_all_tests.sh`.** It starts Docker, runs all suites, saves JSON results, and tears down.

### Test Results Are Persistent

Results are saved as JSON in `test-results/<ddmmyy_HHmm>/`. Always check these after a run — background processes may crash and lose terminal output, but the JSON files persist. Use them to:
- Verify test counts match expectations
- Review failures without re-running
- Compare before/after counts

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
