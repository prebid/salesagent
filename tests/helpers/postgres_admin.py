"""Administrative statements the test suite runs against a Postgres server."""

from __future__ import annotations

from typing import Any


def terminate_backends(cursor: Any, database: str) -> None:
    """Close every connection to *database* except the one *cursor* runs on.

    Run before ``DROP DATABASE``, which Postgres refuses while anything is connected, and
    by the fault harness to drop the seller's connections the way an outage does.
    """
    cursor.execute(
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s AND pid <> pg_backend_pid()",
        (database,),
    )


def drop_database(conn: Any, database: str) -> None:
    """Drop *database* through *conn*, a connection to another database on the server, then close *conn*."""
    conn.autocommit = True
    try:
        cur = conn.cursor()
        terminate_backends(cur, database)
        cur.execute(f'DROP DATABASE IF EXISTS "{database}"')
    finally:
        conn.close()
