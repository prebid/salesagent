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
from typing import Any

from sqlalchemy import inspect, text

from src.core.http_utils import hostname_of
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
    # virtual_host is NOT NULL from revision 7f31c0ab94d2, which also adds virtual_host_name
    # (NOT NULL, UNIQUE). The host is derived from the tenant_id so it stays distinct across
    # seeded tenants, and the name is derived from the host by the function production uses.
    # Which columns exist depends on ``revision``, so the row states the ones the table has.
    vhost = vhost_for(tenant_id)
    row = {"tenant_id": tenant_id, "name": tenant_name, "subdomain": subdomain, "virtual_host": vhost}
    with engine.begin() as conn:
        if any(column["name"] == "virtual_host_name" for column in inspect(conn).get_columns("tenants")):
            row["virtual_host_name"] = hostname_of(vhost)
        # CASCADE, not a DELETE per known child table: every row a previous test seeded
        # belongs to a tenant (accounts, products, ...), and a DELETE list names only the
        # children its author knew of -- the placements tests' products outlived it and
        # every later reset failed on products_tenant_id_fkey.
        conn.execute(text("TRUNCATE TABLE tenants CASCADE"))
        conn.execute(
            text(
                f"INSERT INTO tenants ({', '.join(row)}, ad_server, is_active) "
                f"VALUES ({', '.join(f':{column}' for column in row)}, 'mock', true)"
            ),
            row,
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


def seed_product(
    engine, *, tenant_id: str, product_id: str, inventory_profile_id: int | None = None, **jsonb_columns: Any
) -> None:
    """Insert a products row directly -- a migration's subject is rows, not callers.

    Raw SQL for the same reason as :func:`seed_account`: a revision's subject is a row as an
    older schema holds it -- a channel string the column's validator now refuses, a NULL
    ``delivery_measurement`` -- so no factory can produce it. Each keyword names a JSONB
    column of the schema at the caller's revision and is written as given: None is SQL NULL,
    what production stores for "nothing declared", and a list keeps its non-string elements.
    A column not named takes its server default. ``format_ids`` defaults to ``[]``, which
    satisfies its validator (zero iterations), and ``targeting_template`` to ``{}``. The
    column names are interpolated, so they come from the calling test, never from data.
    ``inventory_profile_id`` is the one integer column a caller names, written only when given
    so a revision without the column is seeded as before.
    """
    columns = {"format_ids": [], "targeting_template": {}, **jsonb_columns}
    names = list(columns)
    values = [f"CAST(:{name} AS jsonb)" for name in columns]
    params: dict[str, Any] = {name: _json_or_null(value) for name, value in columns.items()}
    if inventory_profile_id is not None:
        names.append("inventory_profile_id")
        values.append(":inventory_profile_id")
        params["inventory_profile_id"] = inventory_profile_id
    with engine.begin() as conn:
        conn.execute(
            text(
                f"INSERT INTO products (tenant_id, product_id, name, description, delivery_type, {', '.join(names)}) "
                f"VALUES (:tid, :pid, :name, 'test', 'guaranteed', {', '.join(values)})"
            ),
            {"tid": tenant_id, "pid": product_id, "name": f"Product {product_id}", **params},
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


def _json_or_null(value: Any) -> str | None:
    return None if value is None else json.dumps(value)


def seed_inventory_profile(
    engine, *, tenant_id: str, profile_id: str, format_options: Any, publisher_properties: Any
) -> int:
    """Insert an inventory_profiles row with raw SQL, as the schema under test stores it; return its id."""
    with engine.begin() as conn:
        return conn.execute(
            text(
                "INSERT INTO inventory_profiles (tenant_id, profile_id, name, inventory_config, format_options, "
                "publisher_properties, created_at, updated_at) VALUES (:tid, :pid, :pid, '{}'::jsonb, "
                "CAST(:options AS jsonb), CAST(:properties AS jsonb), now(), now()) RETURNING id"
            ),
            {
                "tid": tenant_id,
                "pid": profile_id,
                "options": _json_or_null(format_options),
                "properties": _json_or_null(publisher_properties),
            },
        ).scalar_one()


class _JsonNull:
    def __repr__(self) -> str:
        return "JSON_NULL"


#: What :func:`stored_column` reads a JSONB ``null`` as. psycopg2 reads it as None, exactly as
#: it reads SQL NULL, and a revision that must store "nothing declared" as SQL NULL rather
#: than ``'null'::jsonb`` is graded by telling the two apart.
JSON_NULL = _JsonNull()


def stored_column(engine, table: str, column: str, **where: Any) -> Any:
    """*column* of the one *table* row matching *where*: None for SQL NULL, :data:`JSON_NULL` for JSONB null.

    *table*, *column* and the *where* names are interpolated, so they come from the calling
    test, never from data; the values stay binds.
    """
    condition = " AND ".join(f"{name} = :{name}" for name in where)
    with engine.begin() as conn:
        is_null, value = conn.execute(
            text(f"SELECT {column} IS NULL, {column} FROM {table} WHERE {condition}"), where
        ).one()
    return None if is_null else JSON_NULL if value is None else value
