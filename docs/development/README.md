# Development Guide

Documentation for contributors to the Prebid Sales Agent codebase, maintained under Prebid.org.

## Getting Started

1. Clone the repository
2. Copy `.env.template` to `.env` (optional - defaults work for development)
3. Build and start the development environment:
   ```bash
   docker compose build
   docker compose up -d
   ```
4. Access Admin UI at http://localhost:8000
   - Click "Log in to Dashboard" button (password: `test123`)

Migrations run automatically on startup. A demo tenant with sample data is created by default.

**Why `docker compose`?** It builds from local source code (not pre-built images), enabling:
- Hot-reload for code changes
- All dependencies including newly added packages
- Source code mounted for live development

See [Contributing](contributing.md) for detailed development workflows.

## Documentation

- **[Architecture](architecture.md)** - System design and component overview
- **[Contributing](contributing.md)** - Development workflows, testing, code style
- **[Troubleshooting](troubleshooting.md)** - Common development issues

## Key Resources

- **[CLAUDE.md](../../CLAUDE.md)** - Detailed development patterns and conventions
- **[Tests](../../tests/)** - Test suite and examples
- **[Source](../../src/)** - Application source code

## Quick Reference

### Running Tests

```bash
./run_all_tests.sh ci     # Full suite with PostgreSQL
./run_all_tests.sh quick  # Fast iteration

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
docker compose exec admin-ui python scripts/ops/migrate.py

# Or locally with uv
uv run python scripts/ops/migrate.py

# Create new migration
uv run alembic revision -m "description"
```
