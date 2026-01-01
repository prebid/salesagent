# Development Guide

Documentation for contributors to the AdCP Sales Agent codebase.

## Getting Started

1. Clone the repository
2. Copy `.env.template` to `.env`
3. Build and start the development environment:
   ```bash
   docker compose -f docker-compose.yml build
   docker compose -f docker-compose.yml up -d
   ```
4. Run database migrations:
   ```bash
   docker compose -f docker-compose.yml exec admin-ui python scripts/ops/migrate.py
   ```
5. Access Admin UI at http://localhost:8000/admin
   - Test login: `test_super_admin@example.com` / `test123`

**Why `docker-compose.yml`?** It builds from local source code (not pre-built images), enabling:
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

```bash
# Inside Docker (recommended for dev)
docker compose -f docker-compose.yml exec admin-ui python scripts/ops/migrate.py

# Or locally with uv
uv run python scripts/ops/migrate.py

# Create new migration
uv run alembic revision -m "description"
```
