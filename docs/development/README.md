# Development Guide

Documentation for contributors to the Prebid Sales Agent codebase, maintained under Prebid.org.

## Getting Started

```bash
git clone https://github.com/prebid/salesagent.git
cd salesagent
make setup
```

See [Getting Started](GETTING_STARTED.md) for prerequisites, manual setup, testing, and common operations.

## Documentation

- **[Architecture](architecture.md)** - System design and component overview
- **[Contributing](contributing.md)** - Development workflows, testing, code style
- **[Structural Guards](structural-guards.md)** - Automated architecture enforcement tests
- **[Troubleshooting](troubleshooting.md)** - Common development issues

## Key Resources

- **[CLAUDE.md](../../CLAUDE.md)** - Detailed development patterns and conventions
- **[Tests](../../tests/)** - Test suite and examples
- **[Source](../../src/)** - Application source code

## Quick Reference

### Running Tests

```bash
./run_all_tests.sh ci     # Full suite: Docker + all 5 suites (DEFAULT)
./run_all_tests.sh quick  # No Docker: unit + integration + integration_v2
# Both modes produce JSON reports in test-results/<ddmmyy_HHmm>/

# Manual pytest
uv run pytest tests/unit/ -x
uv run pytest tests/integration/ -x
```

### Code Quality

```bash
# Pre-commit hooks
pre-commit run --all-files

# Type checking
uv run mypy src/core/your_file.py --config-file=mypy.ini
```

### Database Migrations

Migrations run automatically on startup. To run manually:

```bash
# Inside Docker
docker compose exec adcp-server python scripts/ops/migrate.py

# Or locally with uv
uv run python scripts/ops/migrate.py

# Create new migration
uv run alembic revision -m "description"
```
