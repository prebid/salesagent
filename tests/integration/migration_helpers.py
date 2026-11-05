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

from tests.utils.database_helpers import vhost_for


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
    """Run an Alembic command against *db_url*, whatever the caller's context.

    THE ENV VAR ALONE IS NOT THE OVERRIDE. ``alembic/env.py`` resolves its URL at import
    time from ``DatabaseConfig.get_connection_string()``, which reads ``get_settings()`` --
    and that is a PROCESS-WIDE CACHE (``src.core.config._settings``, built on first use).
    Setting ``DATABASE_URL`` and hoping is therefore a coin flip decided by whether anything
    earlier in the process warmed that cache: measured, the identical override resolved to
    the ambient ``.../adcp_test`` when called from a module-scoped fixture and to the
    intended ``.../test_migration_<hex>`` when called from a test body, because a
    function-scoped conftest fixture resets the cache in between. The failure is silent --
    the upgrade runs against the wrong database, the intended one stays empty, and the next
    statement dies on a missing table.

    So the cache is dropped for the duration and rebuilt after, making the override mean
    what it says from any caller. Restored rather than left cleared: the ambient settings
    belong to whatever is running this, and a test that finds them missing afterwards would
    be paying for someone else's migration.
    """
    from alembic.config import Config

    from src.core import config as config_module

    old_url = os.environ.get("DATABASE_URL")
    old_settings = config_module._settings
    os.environ["DATABASE_URL"] = db_url
    config_module._settings = None
    try:
        alembic_cfg = Config("alembic.ini")
        command_fn(alembic_cfg, target_revision)
    finally:
        config_module._settings = old_settings
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
        # products.tenant_id has no ON DELETE rule in the Alembic-built schema
        # (initial_schema.py); the CASCADE lives on the mapped column, which only
        # Base.metadata.create_all reads. Clear the children first.
        conn.execute(text("DELETE FROM products"))
        conn.execute(text("DELETE FROM accounts"))
        conn.execute(text("DELETE FROM tenants"))
        tenant_columns = {
            row[0]
            for row in conn.execute(
                text("SELECT column_name FROM information_schema.columns WHERE table_name = 'tenants'")
            )
        }
        columns = ["tenant_id", "name", "subdomain", "virtual_host", "ad_server", "is_active"]
        values = [":tid", ":name", ":subdomain", ":vhost", "'mock'", "true"]
        # virtual_host is NOT NULL from revision 7f31c0ab94d2 and its index is UNIQUE, so
        # the value is derived from the tenant_id to stay distinct across seeded tenants.
        # The same revision adds virtual_host_name (NOT NULL); vhost_for() carries no
        # port, so the host IS the name. Older revisions have no such column.
        if "virtual_host_name" in tenant_columns:
            columns.append("virtual_host_name")
            values.append(":vhost")
        conn.execute(
            text(f"INSERT INTO tenants ({', '.join(columns)}) VALUES ({', '.join(values)})"),
            {
                "tid": tenant_id,
                "name": tenant_name,
                "subdomain": subdomain,
                "vhost": vhost_for(tenant_id),
            },
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
    billing: str | None = None,
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

    ``billing`` is likewise raw: the CHECK-widening migration's tests must be able
    to write a value the CURRENT constraint rejects, and to observe the database
    refusing it, which is exactly what the ORM would pre-empt.
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
                "INSERT INTO accounts (tenant_id, account_id, name, status, operator, brand, billing) "
                "VALUES (:tid, :aid, :name, 'active', :operator, CAST(:brand AS jsonb), :billing)"
            ),
            {
                "tid": tenant_id,
                "aid": account_id,
                "name": account_id if name is None else name,
                "operator": operator,
                "brand": brand,
                "billing": billing,
            },
        )


def seed_product(engine, *, tenant_id: str, product_id: str, channels: list[str] | None) -> None:
    """Insert a products row directly -- a migration's subject is rows, not callers.

    Raw SQL for the same reason as :func:`seed_account`: the channel-repair revision's
    subject is a string the admin picker could once write and the enum-typed read path
    refuses, so no factory can produce it. Column list as the delivery-measurement
    migration test seeds it; ``'[]'`` satisfies the format_ids validator (zero iterations),
    ``property_tags`` satisfies ``ck_product_authorization``, server defaults cover the rest.
    ``channels=None`` writes SQL NULL, what production stores for "no channels declared".
    """
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO products (tenant_id, product_id, name, description, delivery_type, "
                "format_ids, targeting_template, property_tags, channels) "
                "VALUES (:tid, :pid, :name, 'test', 'guaranteed', '[]'::jsonb, '{}'::jsonb, "
                "'[\"all_inventory\"]'::jsonb, CAST(:channels AS jsonb))"
            ),
            {
                "tid": tenant_id,
                "pid": product_id,
                "name": f"Product {product_id}",
                "channels": None if channels is None else json.dumps(channels),
            },
        )


def row_xmins(engine, *, table: str, key_column: str, tenant_id: str) -> dict[str, str]:
    """Per-row ``xmin`` -- the transaction that last wrote the row -- keyed by *key_column*.

    PostgreSQL bumps it on ANY update, including a rewrite of a row to itself, which is
    what makes this the only assertion that can tell "issued no statement" apart from
    "churned every row back to the same value". *table* and *key_column* are identifiers
    from a fixed call site, hence interpolated; the tenant stays a bind.
    """
    with engine.connect() as conn:
        rows = conn.execute(
            text(f"SELECT {key_column} AS key, xmin::text AS xmin FROM {table} WHERE tenant_id = :tid"),
            {"tid": tenant_id},
        ).all()
    return {row.key: row.xmin for row in rows}
