---
name: agent-db
description: >
  Start an isolated PostgreSQL container for integration testing. Each
  worktree gets its own container on a unique port — no mutex, no conflicts.
  Use this before running integration tests in a worktree agent.
---

# Agent Database

Starts a lightweight `postgres:17-alpine` container scoped to the current
worktree. The container name is deterministic (`agent-pg-<dirname>`), so
`up`/`down`/`status` work across calls without tracking state manually.

## When to use

- Before running integration tests in a worktree agent
- When you need a private Postgres that won't conflict with other agents
- Instead of `make test-stack-up` (which starts the full docker-compose stack)

## Setup

```bash
eval $(.claude/skills/agent-db/agent-db.sh up)
```

This exports:
- `DATABASE_URL` — points to your private Postgres
- `ADCP_TESTING=true`
- `ENCRYPTION_KEY`, `GEMINI_API_KEY` — test defaults

The `integration_db` pytest fixture handles the rest: creates per-test
databases, runs `Base.metadata.create_all()`, cleans up after each test.

## Running tests

```bash
# Integration tests (real Postgres)
uv run pytest tests/integration/ -x -q

# Targeted
uv run pytest tests/integration/test_specific.py -x -q -k "test_name"

# Unit tests (no DB needed)
uv run pytest tests/unit/ -x -q
```

## Commands

| Command | Effect |
|---------|--------|
| `eval $(.claude/skills/agent-db/agent-db.sh up)` | Start container, export env vars |
| `.claude/skills/agent-db/agent-db.sh down` | Stop and remove container |
| `.claude/skills/agent-db/agent-db.sh status` | Check if running |

## Idempotent

Running `up` when already running re-exports the same env vars (same port,
same container). Safe to call multiple times.

## Cleanup

The container is removed when you run `down` or when the worktree is deleted.
If the worktree is cleaned up without running `down`, the container stays
orphaned — run `docker rm -f agent-pg-<worktree-name>` to clean it up.
