# Getting Started

Set up the Prebid Sales Agent for local development.

## Prerequisites

| Tool | Version | Install |
|------|---------|---------|
| Python | 3.12+ | [python.org](https://www.python.org/downloads/) |
| Docker + Compose | Latest | [docker.com](https://docs.docker.com/get-docker/) |
| Git | Any | [git-scm.com](https://git-scm.com/downloads) |
| uv | Latest | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| ast-grep | Latest | `brew install ast-grep` (macOS) or [ast-grep.github.io](https://ast-grep.github.io/guide/quick-start.html) |

> **Note:** `ast-grep` is a Rust CLI tool used by the `ast-grep-bdd-guards` pre-commit hook for BDD
> step structural pattern detection. It cannot be installed via `uv` — it requires a separate system
> install. Without it, commits that touch `tests/bdd/steps/` will be blocked locally.

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

**Log in:** with Google, as one of the `SUPER_ADMIN_EMAILS` addresses. There is no password login.
Before you sign in, with either setup, complete the following steps:

1. Create the OAuth client and register its two redirect URIs as in
   [quickstart step 2](../quickstart.md#2-create-a-google-oauth-client).
2. In `.env`, set `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, and `SUPER_ADMIN_EMAILS`. The
   `OAUTH_*` variables configure another OIDC provider instead. After a manual setup, also set
   `ENCRYPTION_KEY`; `make setup` writes it for you.
3. Run `docker compose up -d --force-recreate` so that the server reads them.

## Manual Setup

If you prefer to run each step yourself:

```bash
# 1. Clone and enter the repository
git clone https://github.com/prebid/salesagent.git
cd salesagent

# 2. Install Python dependencies
uv sync

# 3. Create .env. Sign-in needs an OAuth client and SUPER_ADMIN_EMAILS in it:
#    see docs/quickstart.md steps 2-3
cp .env.template .env

# 4. Install pre-commit hooks
uvx pre-commit install

# 5. Start Docker services
docker compose up -d
```

Then wait for `adcp-server` to report `(healthy)` before the first request, as in
[quickstart step 4](../quickstart.md#4-start-the-stack).

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

Run the script in [quickstart step 7](../quickstart.md#7-connect-an-ai-agent).

### Type checking

```bash
uv run mypy src/core/your_file.py --config-file=mypy.ini
```

### Fix formatting and lint errors

```bash
make lint-fix
```

## Conductor Workspaces

Conductor is a Mac app that runs multiple development workspaces in
parallel, each in its own git worktree with an isolated Docker environment.
To set up a workspace, run the setup script from inside it:

```bash
./scripts/setup/setup_conductor_workspace.sh
```

The script copies `.env` from the Conductor root directory (create it there
first — see [Environment Configuration](#environment-configuration)),
assigns the workspace a unique port, and starts the Docker stack. When you
archive a workspace, release its Docker resources:

```bash
./scripts/setup/cleanup_conductor_workspace.sh
```

## Environment Configuration

The `.env` file is created from `.env.template` during setup. Key variables:

| Variable | Default | Purpose |
|----------|---------|---------|
| `CREATE_DEMO_TENANT` | `false` | No effect with `docker-compose.yml`; see [About `CREATE_DEMO_TENANT`](../quickstart.md#about-create_demo_tenant) |
| `ENVIRONMENT` | `development` | `development` = strict validation, `production` = lenient |
| `CONDUCTOR_PORT` | `8000` | Nginx proxy port |

For OAuth, GAM integration, and other production settings, see the comments in `.env.template` or [Environment Variables](../deployment/environment-variables.md).

## Next Steps

- [Architecture](architecture.md) — system design and component overview
- [Engineering standards](engineering-standards.md) — the standards every change is held to
- [Creating an ad server adapter](../adapters/creating-an-adapter.md) — the adapter base-class contract
- [Structural Guards](structural-guards.md) — automated architecture enforcement
- [Troubleshooting](troubleshooting.md) — common development issues
