"""Shared integration_db fixture logic.

Eliminates duplication between tests/integration/conftest.py and
tests/integration_v2/conftest.py. Both conftest files delegate to
``make_integration_db()`` which handles:

- Per-test database creation (unique name via uuid)
- All model imports and Base.metadata.create_all()
- Monkeypatching the database_session module globals
- Teardown: engine dispose, env restore, database drop
"""

from __future__ import annotations

import os
import re
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from typing import NamedTuple

import psycopg2
import pytest
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
from sqlalchemy import create_engine
from sqlalchemy.orm import scoped_session, sessionmaker

_PG_URL_PATTERN = re.compile(r"postgresql://([^:]+):([^@]+)@([^:]+):(\d+)/(.+)")


class _PgConnInfo(NamedTuple):
    user: str
    password: str
    host: str
    port: int


def _parse_postgres_url(url: str) -> _PgConnInfo:
    match = _PG_URL_PATTERN.match(url)
    if not match:
        pytest.fail(f"Failed to parse DATABASE_URL: {url}\nExpected format: postgresql://user:pass@host:port/dbname")
    user, password, host, port_str, _ = match.groups()
    return _PgConnInfo(user=user, password=password, host=host, port=int(port_str))


def _import_all_models() -> None:
    """Import all ORM models so Base.metadata knows about every table.

    The module-level import triggers Python to execute the models module body,
    which defines all ORM classes and registers them with Base.metadata.
    No explicit per-model imports needed.
    """
    import src.core.database.models as _all_models  # noqa: F401


@contextmanager
def make_integration_db(
    *,
    json_serializer: bool = False,
) -> Generator[str, None, None]:
    """Context manager that provides an isolated PostgreSQL database.

    Yields the unique database name (e.g. ``test_a3f8d92c``).

    Parameters
    ----------
    json_serializer:
        If True, pass ``_pydantic_json_serializer`` to ``create_engine()``.
        The v1 integration suite uses this; v2 does not.
    """
    # ── Require PostgreSQL ──────────────────────────────────────────────
    postgres_url = os.environ.get("DATABASE_URL")
    if not postgres_url or not postgres_url.startswith("postgresql://"):
        pytest.skip(
            "Integration tests require PostgreSQL DATABASE_URL (e.g., postgresql://user:pass@localhost:5432/any_db)"
        )

    pg = _parse_postgres_url(postgres_url)

    # ── Save originals ──────────────────────────────────────────────────
    original_url = os.environ.get("DATABASE_URL")
    original_db_type = os.environ.get("DB_TYPE")

    # ── Create unique database ──────────────────────────────────────────
    unique_db_name = f"test_{uuid.uuid4().hex[:8]}"
    assert re.match(r"^test_[0-9a-f]{8}$", unique_db_name), f"Unexpected db name format: {unique_db_name}"
    conn_params = {
        "host": pg.host,
        "port": pg.port,
        "user": pg.user,
        "password": pg.password,
        "database": "postgres",
    }

    conn = psycopg2.connect(**conn_params)
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    cur = conn.cursor()
    try:
        cur.execute(f'CREATE DATABASE "{unique_db_name}"')
    finally:
        cur.close()
        conn.close()

    test_db_url = f"postgresql://{pg.user}:{pg.password}@{pg.host}:{pg.port}/{unique_db_name}"
    os.environ["DATABASE_URL"] = test_db_url
    os.environ["DB_TYPE"] = "postgresql"

    # ── Import models and create tables ─────────────────────────────────
    _import_all_models()
    from src.core.database.models import Base

    engine_kwargs: dict = {"echo": False}
    if json_serializer:
        from src.core.database.database_session import _pydantic_json_serializer

        engine_kwargs["json_serializer"] = _pydantic_json_serializer

    engine = create_engine(test_db_url, **engine_kwargs)

    from src.core.database.database_session import reset_engine

    reset_engine()

    Base.metadata.create_all(bind=engine, checkfirst=True)

    # ── Monkeypatch database_session globals ────────────────────────────
    import src.core.database.database_session as db_session_module

    db_session_module._engine = engine
    db_session_module._session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db_session_module._scoped_session = scoped_session(db_session_module._session_factory)

    # Reset context manager singleton
    import src.core.context_manager

    src.core.context_manager._context_manager_instance = None

    # ── Yield ───────────────────────────────────────────────────────────
    try:
        yield unique_db_name
    finally:
        # ── Teardown ────────────────────────────────────────────────────
        reset_engine()
        src.core.context_manager._context_manager_instance = None
        engine.dispose()

        # Restore environment
        if original_url is not None:
            os.environ["DATABASE_URL"] = original_url
        elif "DATABASE_URL" in os.environ:
            del os.environ["DATABASE_URL"]

        if original_db_type is not None:
            os.environ["DB_TYPE"] = original_db_type
        elif "DB_TYPE" in os.environ:
            del os.environ["DB_TYPE"]

        # Drop the test database
        try:
            conn = psycopg2.connect(**conn_params)
            conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
            cur = conn.cursor()
            cur.execute(
                """
                SELECT pg_terminate_backend(pg_stat_activity.pid)
                FROM pg_stat_activity
                WHERE pg_stat_activity.datname = %s
                AND pid <> pg_backend_pid()
                """,
                (unique_db_name,),
            )
            cur.execute(f'DROP DATABASE IF EXISTS "{unique_db_name}"')
            cur.close()
            conn.close()
        except Exception as exc:
            import warnings

            warnings.warn(
                f"Failed to drop test database {unique_db_name}: {exc.__class__.__name__}: {exc}. "
                "Orphaned test databases can be cleaned up with: "
                "SELECT 'DROP DATABASE \"' || datname || '\";' FROM pg_database WHERE datname LIKE 'test_%';",
                stacklevel=1,
            )
