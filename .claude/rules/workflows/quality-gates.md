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
make quality-full
```

Runs everything above plus `./run_all_tests.sh ci` (integration + e2e with PostgreSQL).

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

**Important**: Pre-commit hooks can't catch import errors. After refactoring or moving code, always run `uv run pytest tests/unit/ -x` to verify.

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
