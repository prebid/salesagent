# Getting Started

Set up the Prebid Sales Agent for local development.

## Prerequisites

| Tool | Version | Install |
|------|---------|---------|
| Python | 3.12+ | [python.org](https://www.python.org/downloads/) |
| Docker + Compose | Latest | [docker.com](https://docs.docker.com/get-docker/) |
| Git | Any | [git-scm.com](https://git-scm.com/downloads) |
| uv | Latest | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |

## One-Command Setup

```bash
git clone https://github.com/prebid/salesagent.git
cd salesagent
make setup
```

`make setup` runs `scripts/setup-dev.py`, which handles everything:

1. Verifies prerequisites (Python, Docker, uv, git)
2. Installs Python dependencies (`uv sync`)
3. Creates `.env` from `.env.template` (preserves existing values)
4. Installs pre-commit hooks
5. Checks for tox (optional, used by the test runner)
6. Starts Docker services (`docker compose up -d`)
7. Waits for database migrations to complete
8. Verifies health check at `http://localhost:8000/health`

When complete, services are running at **http://localhost:8000**:

| Service | URL |
|---------|-----|
| Admin UI | http://localhost:8000/admin/ |
| MCP Server | http://localhost:8000/mcp/ |
| A2A Server | http://localhost:8000/a2a |
| Health Check | http://localhost:8000/health |

**Test login:** Click "Log in to Dashboard" on the login page (password: `test123`).

## Manual Setup

If you prefer to run each step yourself:

```bash
# 1. Clone and enter the repository
git clone https://github.com/prebid/salesagent.git
cd salesagent

# 2. Install Python dependencies
uv sync

# 3. Create .env (optional — defaults work for development)
cp .env.template .env

# 4. Install pre-commit hooks
uvx pre-commit install

# 5. Start Docker services
docker compose up -d

# 6. Verify health
curl http://localhost:8000/health
```

Migrations run automatically on startup via the `db-init` container. Docker Compose builds from local source, so code changes are reflected immediately.

## Testing

### Quick quality check (before every commit)

```bash
make quality
```

Runs: formatting check, linting, type checking, and unit tests.

### Full test suite (before merge)

Install tox first (one-time):

```bash
uv tool install tox --with tox-uv
```

Then run all five test suites (unit, integration, integration_v2, e2e, ui) in parallel via Docker:

```bash
./run_all_tests.sh
```

This starts Docker, runs tox, tears down Docker, and saves JSON reports to `test-results/`. See [Testing Patterns](../../.claude/rules/patterns/testing-patterns.md) for the full reference.

### Targeted runs

```bash
tox -e unit                            # Unit tests only (no Docker)
tox -e integration -- -k test_name     # Specific integration test
./run_all_tests.sh quick               # No Docker: unit + integration + integration_v2
./run_all_tests.sh ci tests/integration/test_file.py -k test_name
```

## Common Operations

### View logs

```bash
docker compose logs -f          # All services
docker compose logs -f adcp-server  # Just the app server
```

### Stop and restart

```bash
docker compose down             # Stop services
docker compose up -d            # Start again
docker compose down -v          # Stop and reset database
```

### Rebuild after dependency changes

```bash
docker compose build && docker compose up -d
```

### Run database migrations manually

```bash
# Locally
uv run python scripts/ops/migrate.py

# Inside Docker
docker compose exec adcp-server python scripts/ops/migrate.py

# Create a new migration
uv run alembic revision -m "description"
```

### Test the MCP interface

```bash
uvx adcp http://localhost:8000/mcp/ --auth test-token list_tools
uvx adcp http://localhost:8000/mcp/ --auth test-token get_products '{"brief":"video"}'
```

### Type checking

```bash
uv run mypy src/core/your_file.py --config-file=mypy.ini
```

### Fix formatting and lint errors

```bash
make lint-fix
```

## Environment Configuration

The `.env` file is created from `.env.template` during setup. Key variables:

| Variable | Default | Purpose |
|----------|---------|---------|
| `ADCP_AUTH_TEST_MODE` | `true` | Enables test login (disable for production) |
| `CREATE_DEMO_TENANT` | `false` | Creates sample data on first startup |
| `ENVIRONMENT` | `development` | `development` = strict validation, `production` = lenient |
| `CONDUCTOR_PORT` | `8000` | Nginx proxy port |

For OAuth, GAM integration, and other production settings, see the comments in `.env.template` or [Environment Variables](../deployment/environment-variables.md).

## Next Steps

- [Architecture](architecture.md) — system design and component overview
- [Contributing](contributing.md) — code style, adapters, debugging
- [Structural Guards](structural-guards.md) — automated architecture enforcement
- [Troubleshooting](troubleshooting.md) — common development issues
