"""Shared helpers for migration integration tests.

Provides common utilities for tests that run Alembic migrations against
isolated PostgreSQL databases:
- parse_postgres_url(): Parse DATABASE_URL into connection components
- run_alembic_upgrade(): Run Alembic upgrade to a specific revision
- run_alembic_downgrade(): Run Alembic downgrade to a specific revision
- seed_account(): Insert an accounts row with raw SQL

The shared ``migration_db`` fixture lives in ``conftest.py`` (pytest auto-discovers
fixtures from conftest without explicit imports, avoiding F811 lint errors).
"""

from __future__ import annotations

import json
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


def reset_to_revision(
    migration_db,
    *,
    revision: str,
    tenant_id: str,
    tenant_name: str,
    subdomain: str,
):
    """Put the module-scoped database at ``revision`` with one empty tenant.

    ``migration_db`` is module-scoped and test order is randomized, so a test that
    assumed a starting revision or an empty table would pass or fail depending on
    what ran before it — and the abort/refusal tests in particular leave rows
    behind that would break any later upgrade.

    The upgrade creates the schema on first use; the downgrade is a no-op when the
    database is already at ``revision`` and rewinds it when a previous test left it
    higher.

    Returns ``(engine, db_url)``.
    """
    engine, db_url = migration_db
    run_alembic_upgrade(db_url, revision)
    run_alembic_downgrade(db_url, revision)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM accounts"))
        conn.execute(text("DELETE FROM tenants"))
        conn.execute(
            text(
                "INSERT INTO tenants (tenant_id, name, subdomain, ad_server, is_active) "
                "VALUES (:tid, :name, :subdomain, 'mock', true)"
            ),
            {"tid": tenant_id, "name": tenant_name, "subdomain": subdomain},
        )
    return engine, db_url


def seed_account(
    engine,
    *,
    tenant_id: str,
    account_id: str,
    domain: str | None,
    operator: str | None,
    brand_id: str | None = None,
    name: str | None = None,
) -> None:
    """Insert an accounts row directly — a migration's subject is rows, not callers.

    Deliberately raw SQL rather than a factory: these tests seed values the ORM
    refuses to produce. ``Account.brand`` is ``JSONType(model=BrandReference)``,
    which validates on read AND write, so the mangled ``brand_id`` that the
    repair migration exists to fix cannot be written through the model at all.

    The brand JSON is BOUND as a parameter and cast to ``jsonb`` rather than
    f-string interpolated, because the value under repair contains single quotes
    (``root='brand_one'``) that interpolation misquotes into a syntax error.

    ``domain=None`` writes SQL NULL, matching what production stores for a
    brand-less account (``JSONType`` uses ``JSONB(none_as_null=True)``).
    """
    brand: str | None = None
    if domain is not None:
        payload = {"domain": domain}
        if brand_id is not None:
            payload["brand_id"] = brand_id
        brand = json.dumps(payload)

    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO accounts (tenant_id, account_id, name, status, operator, brand) "
                "VALUES (:tid, :aid, :name, 'active', :operator, CAST(:brand AS jsonb))"
            ),
            {
                "tid": tenant_id,
                "aid": account_id,
                "name": account_id if name is None else name,
                "operator": operator,
                "brand": brand,
            },
        )
