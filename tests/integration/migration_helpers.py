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

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


def parse_postgres_url() -> tuple[str, str, str, int] | None:
    """Parse DATABASE_URL into connection components.

    Returns (user, password, host, port) or None if DATABASE_URL is not set
    or is not a valid PostgreSQL URL.
    """
    postgres_url = os.environ.get("DATABASE_URL", "")
    if not postgres_url:
        return None
    try:
        url = make_url(postgres_url)
    except ArgumentError:
        return None
    if not url.drivername.startswith(("postgresql", "postgres")):
        return None
    return (
        url.username or "",
        str(url.password) if url.password else "",
        url.host or "",
        url.port or 5432,
    )


def _run_alembic_command(db_url: str, command_fn, target_revision: str) -> None:
    """Run an Alembic command with temporary DATABASE_URL override.

    Temporarily sets DATABASE_URL for alembic/env.py which reads from
    os.environ["DATABASE_URL"].
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
