"""Shared helpers for migration integration tests.

Provides common utilities for tests that run Alembic migrations against
isolated PostgreSQL databases:
- parse_postgres_url(): Parse DATABASE_URL into connection components
- run_alembic_upgrade(): Run Alembic upgrade to a specific revision
- run_alembic_downgrade(): Run Alembic downgrade to a specific revision

The shared ``migration_db`` fixture lives in ``conftest.py`` (pytest auto-discovers
fixtures from conftest without explicit imports, avoiding F811 lint errors).
"""

from __future__ import annotations

import os
import re

from sqlalchemy import text


def parse_postgres_url() -> tuple[str, str, str, int] | None:
    """Parse DATABASE_URL into connection components.

    Returns (user, password, host, port) or None if DATABASE_URL is not set
    or does not match the expected PostgreSQL format.
    """
    postgres_url = os.environ.get("DATABASE_URL", "")
    match = re.match(r"postgresql://([^:]+):([^@]+)@([^:]+):(\d+)/(.+)", postgres_url)
    if not match:
        return None
    user, password, host, port_str, _ = match.groups()
    return user, password, host, int(port_str)


def _run_alembic_command(db_url: str, command_fn, target_revision: str) -> None:
    """Run an Alembic command with temporary DATABASE_URL override.

    Temporarily sets DATABASE_URL for alembic/env.py which reads from
    DatabaseConfig.get_connection_string().
    """
    from alembic.config import Config

    old_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = db_url
    try:
        alembic_cfg = Config("alembic.ini")
        command_fn(alembic_cfg, target_revision)
    finally:
        if old_url:
            os.environ["DATABASE_URL"] = old_url
        elif "DATABASE_URL" in os.environ:
            del os.environ["DATABASE_URL"]


def run_alembic_upgrade(db_url: str, target_revision: str) -> None:
    """Run Alembic upgrade to a specific revision."""
    from alembic import command

    _run_alembic_command(db_url, command.upgrade, target_revision)


def run_alembic_downgrade(db_url: str, target_revision: str) -> None:
    """Run Alembic downgrade to a specific revision."""
    from alembic import command

    _run_alembic_command(db_url, command.downgrade, target_revision)


def column_exists(engine, table: str, column: str) -> bool:
    """Whether ``table.column`` exists (information_schema probe).

    Shared by migration tests that assert a column is added/dropped, replacing
    per-test hand-rolled copies of the same query.
    """
    return get_column_info(engine, table, column) is not None


def get_column_info(engine, table: str, column: str) -> tuple[str, str | None] | None:
    """``(is_nullable, column_default)`` for ``table.column``, or None if absent."""
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT is_nullable, column_default FROM information_schema.columns "
                "WHERE table_name = :t AND column_name = :c"
            ),
            {"t": table, "c": column},
        ).first()
    return (row[0], row[1]) if row else None


def seed_tenant(engine, tenant_id: str, *, subdomain: str) -> None:
    """Insert a minimal tenant row on a migration-managed schema.

    The tenant INSERT is identical across migration tests; centralizing it here
    keeps new migration tests from re-copying it (each test still seeds its own
    domain-specific child rows).
    """
    with engine.connect() as conn:
        conn.execute(
            text(
                "INSERT INTO tenants (tenant_id, name, subdomain, created_at, updated_at) "
                "VALUES (:tid, :name, :sub, NOW(), NOW())"
            ),
            {"tid": tenant_id, "name": f"{tenant_id} tenant", "sub": subdomain},
        )
        conn.commit()
