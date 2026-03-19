# Quality Gates

## When to Run

Run quality gates **before any commit** and **before closing a beads task**.

## Quick Check (Every Change)

```bash
make quality
```

This runs:
1. `ruff format --check .` — formatting
2. `ruff check .` — linting (includes C90 complexity and PLR refactor rules)
3. `mypy src/ --config-file=mypy.ini` — type checking
4. `pytest tests/unit/ -x` — unit tests (fail-fast)

## Full Check (Before Merge)

```bash
./run_all_tests.sh
```

Starts Docker, runs all 5 test suites in parallel via tox, combines coverage, tears down Docker. JSON reports saved to `test-results/<ddmmyy_HHmm>/` (last 10 runs kept).

Equivalent manual workflow:
```bash
make test-stack-up                     # Start Docker, write .test-stack.env
source .test-stack.env && tox -p       # All 5 suites in parallel + coverage combine
make test-stack-down                   # Tear down Docker
make test-cov                          # Open HTML coverage report
```

## Individual Suite Runs (via tox)

```bash
tox -e unit                            # Unit tests only (no Docker needed)
tox -e integration                     # Integration tests (needs Docker)
tox -e e2e                             # End-to-end (needs full Docker stack)
tox -e admin                           # Admin UI tests (needs full Docker stack)
tox -e bdd                             # BDD behavioral tests
tox -e coverage                        # Combine coverage + generate reports
tox -e integration -- -k test_name     # Pass pytest args after --
make test-entity ENTITY=delivery       # Entity-scoped (across unit+integration+e2e+admin)
```

## Test Integrity — HARD STOP Rules

See `.claude/rules/patterns/testing-patterns.md` "Test Integrity" section for the full policy. Summary:

- **NEVER** use `--ignore`, `-k "not ..."`, `--deselect` to skip failing tests
- **NEVER** rationalize failures as "pre-existing" or "infrastructure issue"
- If tests need Docker → start Docker (`./run_all_tests.sh` or `make test-stack-up`)
- If infrastructure is broken → STOP and report to user, do NOT skip tests
- Test results persist as JSON in `test-results/` — use them instead of re-running

## Coverage

Coverage is collected per-suite and combined automatically:
- **HTML report**: `htmlcov/index.html` (open with `make test-cov`)
- **JSON report**: `coverage.json`
- **Config**: `pyproject.toml` `[tool.coverage.*]` sections + `tox.ini` per-env `COVERAGE_FILE`

## Common Violations

### Ruff Rules to Watch
- **C901** (complexity > 10): Break function into smaller pieces
- **PLR0912** (branches > 12): Simplify conditional logic
- **PLR0913** (args > 5): Use dataclass/config object for parameters
- **PLR0915** (statements > 50): Extract helper functions

### Pre-commit Hooks (11 active)
The project has 11 pre-commit hooks that catch:
- Route conflicts
- SQLAlchemy 1.x patterns
- Star imports
- Excessive mocks in tests
- Documentation link breakage
- Import usage issues

Run manually: `pre-commit run --all-files`

**Important**: Pre-commit hooks can't catch import errors. After refactoring or moving code, always run `make quality` to verify.

## AdCP Contract Compliance

For any schema changes, run:
```bash
uv run pytest tests/unit/test_adcp_contract.py -v
```

## Fix Formatting/Linting Issues

```bash
make lint-fix
```

This runs `ruff format .` then `ruff check --fix .`.
