"""Database faults provoked on the database itself, for any env that grades how the seller survives them.

The seller is never patched. Each method makes Postgres do to the seller what a real
incident does, against whichever database the env talks to: the per-test database
in process, the live server's database over e2e. The seller's own ``get_db_session``
then handles exactly the driver error it would handle in production.

- :meth:`DatabaseFaultMixin.interrupt_next_statement` holds an ACCESS EXCLUSIVE lock on a
  table from a harness connection, so the seller's next statement on it waits. A harness
  thread watches ``pg_stat_activity`` for a backend blocked by that lock and cancels it
  (SQLSTATE 57014 on a live connection, what a statement_timeout or an operator's
  ``pg_cancel_backend`` produces) or terminates it (a dropped connection), then releases
  the lock. ``products`` (the default) is read only by the tool's own query: the credential
  path reads tenants and principals first. ``principals`` is read first by the credential
  lookup, which runs under ``execute_with_retry``; there the victim must also send a given
  text (the presented token's hash), because on the live server anything else that reads
  ``principals`` while the lock is held (a server-initiated job resolving a stored owner
  through ``get_principal_by_id``) would otherwise be the backend interrupted.
- :meth:`DatabaseFaultMixin.refuse_new_connections` stops the database accepting
  connections and terminates its open ones, so the seller's next checkout fails its
  pre-ping (the per-test engine pre-pings, as production's does) and its reconnect is
  refused before the server answers any SQL: the outage the process-wide fail-fast exists
  for. ``ALLOW_CONNECTIONS false`` rather than ``CONNECTION LIMIT 0``, because a superuser
  (the test role) ignores the limit, and it is set from the ``postgres`` database because
  Postgres refuses it on the current one.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

import psycopg2
from sqlalchemy.engine import URL

from src.core.database.database_session import FAIL_FAST_WINDOW_S, reset_health_state
from tests.harness._realize import realize_e2e
from tests.helpers.postgres_admin import terminate_backends

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

_WAIT_FOR_WAITER_S = 15.0
#: How long the harness waits for its own table lock before failing the scenario. A lock it
#: cannot take means a transaction holds that table, and an unbounded LOCK would hang the test.
_LOCK_TIMEOUT = "5s"


def _connect(url: URL, *, autocommit: bool = False):
    conn = psycopg2.connect(url.set(drivername="postgresql").render_as_string(hide_password=False))
    conn.autocommit = autocommit
    return conn


class _NextStatementInterrupter:
    """Cancel or terminate the first backend blocked by the lock this object holds.

    With *sending*, only a blocked backend whose current statement contains that text.
    """

    def __init__(self, url: URL, *, table: str, terminate: bool, sending: str = "") -> None:
        self._signal = "pg_terminate_backend" if terminate else "pg_cancel_backend"
        self._sending = f"%{sending}%"
        self._lock_conn = _connect(url)
        cur = self._lock_conn.cursor()
        cur.execute(f"SET lock_timeout = '{_LOCK_TIMEOUT}'")
        cur.execute(f'LOCK TABLE "{table}" IN ACCESS EXCLUSIVE MODE')
        self._lock_pid = self._lock_conn.get_backend_pid()
        self._admin = _connect(url, autocommit=True)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _blocked_pids(self, cur) -> set[int]:
        cur.execute(
            "SELECT pid FROM pg_stat_activity WHERE datname = current_database() "
            "AND %s = ANY(pg_blocking_pids(pid)) AND query LIKE %s",
            (self._lock_pid, self._sending),
        )
        return {row[0] for row in cur.fetchall()}

    def _run(self) -> None:
        cur = self._admin.cursor()
        deadline = time.monotonic() + _WAIT_FOR_WAITER_S
        try:
            while time.monotonic() < deadline:
                victims = self._blocked_pids(cur)
                if victims:
                    for pid in victims:
                        cur.execute(f"SELECT {self._signal}(%s)", (pid,))
                    # Hold the lock until the signal has landed, or the statement simply
                    # completes once the lock goes away and nothing was provoked.
                    while victims & self._blocked_pids(cur) and time.monotonic() < deadline:
                        time.sleep(0.01)
                    return
                time.sleep(0.01)
        finally:
            self._lock_conn.rollback()

    def close(self) -> None:
        self._thread.join(timeout=_WAIT_FOR_WAITER_S + 5)
        self._lock_conn.close()
        self._admin.close()


def _wait_out_fail_fast(self: DatabaseFaultMixin) -> None:
    """E2E realization of :meth:`DatabaseFaultMixin._clear_seller_fail_fast`.

    The tripped fail-fast lives in the live server's process, which this test cannot
    reset, so the scenario waits out the window from the moment the database accepted
    connections again: nothing could trip it after that.
    """
    remaining = self._connections_restored_at + FAIL_FAST_WINDOW_S + 1 - time.monotonic()
    if remaining > 0:
        time.sleep(remaining)


class DatabaseFaultMixin:
    """Mixes database fault injection into a real-database env.

    Host env must be an ``IntegrationEnv`` (relies on ``_guard`` for cleanup on exit and on
    ``get_session`` for the database the env talks to).
    """

    _connections_restored_at: float = 0.0

    if TYPE_CHECKING:

        def _guard(self, label: str, cleanup: Callable[[], None]) -> None: ...

        def get_session(self) -> Session: ...

        def _commit_factory_data(self) -> None: ...

    def _fault_target_url(self) -> URL:
        return self.get_session().get_bind().url

    def interrupt_next_statement(self, *, terminate: bool, table: str = "products", sending: str = "") -> None:
        """Make the database cancel (or, with *terminate*, drop) the seller's next statement on *table*.

        The env's own seeding is committed first: an open transaction on the env session holds
        a lock on every table it read, which the harness's ACCESS EXCLUSIVE lock would wait on.
        """
        self._commit_factory_data()
        interrupter = _NextStatementInterrupter(
            self._fault_target_url(), table=table, terminate=terminate, sending=sending
        )
        self._guard("db_fault:interrupter", interrupter.close)

    def refuse_new_connections(self) -> None:
        """Stop the database accepting connections and close every connection it has.

        The env's own seeding is committed first, so the harness holds no transaction on a
        connection about to be closed; the pool replaces that connection when next used.
        """
        self._commit_factory_data()
        self._guard("db_fault:seller_fail_fast", self._clear_seller_fail_fast)
        self._guard("db_fault:connections", self.accept_new_connections)
        self._set_allow_connections(False)

    def accept_new_connections(self) -> None:
        """Let the database accept connections again. Idempotent; also the refusal's cleanup."""
        self._set_allow_connections(True)
        if not self._connections_restored_at:
            self._connections_restored_at = time.monotonic()

    def _set_allow_connections(self, allow: bool) -> None:
        url = self._fault_target_url()
        admin = _connect(url.set(database="postgres"), autocommit=True)
        try:
            cur = admin.cursor()
            cur.execute(f'ALTER DATABASE "{url.database}" WITH ALLOW_CONNECTIONS {str(allow).lower()}')
            if not allow:
                terminate_backends(cur, url.database)
        finally:
            admin.close()

    @realize_e2e(_wait_out_fail_fast)
    def _clear_seller_fail_fast(self) -> None:
        """In process the fail-fast is a module global; a tripped one would refuse the next scenario."""
        reset_health_state()
