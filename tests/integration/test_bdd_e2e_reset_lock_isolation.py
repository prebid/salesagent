"""The bdd_e2e per-scenario DB reset must not contend with a concurrent reader.

Regression cover for #2048. Over the e2e_rest transport the test
runner and the live server share ONE database, so ``_reset_e2e_db`` runs
concurrently with the server's background scheduler sweeps
(``delivery_webhook_scheduler`` every 5s under run_all_tests.sh,
``media_buy_status_scheduler`` every 60s). Each sweep reads ``media_buys`` first
and a second table later in the SAME transaction; the reset used to TRUNCATE,
taking AccessExclusiveLock on the same relations in ``pg_tables`` order. Opposite
orders over conflicting lock modes is an ABBA cycle, and Postgres broke it by
killing a party -- one rotating ``DeadlockDetected`` in scenario setup per full
in-network run.

Grading the deadlock itself would mean racing a nondeterministic interleaving.
The property that makes the deadlock impossible is deterministic, and is what
these tests assert: **the reset never waits on a plain reader.** A transaction
that cannot wait cannot be half of a cycle. Under the old TRUNCATE the first test
blocks until the reader commits (so it times out); under DELETE's
RowExclusiveLock -- which does not conflict with AccessShareLock -- it returns
immediately.
"""

from __future__ import annotations

import os
import threading
from types import SimpleNamespace

import psycopg2
import pytest
from sqlalchemy import text

from tests.factories import MediaBuyFactory, PrincipalFactory, TenantFactory

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

# Generous enough that a loaded CI box never trips it, small enough that a reset
# genuinely blocked on the reader's AccessShareLock cannot sneak under it: the
# reader holds its lock for the whole window and only releases at teardown.
_RESET_DEADLINE_SECONDS = 20.0


@pytest.fixture
def e2e_config(integration_db) -> SimpleNamespace:
    """The ``_reset_e2e_db`` argument shape: anything with a ``postgres_url``."""
    return SimpleNamespace(postgres_url=os.environ["DATABASE_URL"])


class _HeldReader:
    """A transaction holding an AccessShareLock on ``media_buys``, until released.

    ``release()`` exists because a FAILING run must still tear down. If the reset
    is blocked on this lock, nothing here ever returns and ``integration_db``'s
    DROP DATABASE waits on the blocked backend forever -- a red test that hangs
    the suite instead of reporting. Every test releases before it asserts.
    """

    def __init__(self, url: str) -> None:
        self._conn = psycopg2.connect(url)
        self._conn.autocommit = False
        self.cursor = self._conn.cursor()
        self.cursor.execute("SELECT count(*) FROM media_buys")
        self.cursor.fetchall()

    def release(self) -> None:
        if self._conn.closed:
            return
        self._conn.rollback()
        self._conn.close()


@pytest.fixture
def reader(integration_db):
    """The server-side scheduler sweep reduced to the part that matters.

    It has read one relation and has not committed, so it still holds that
    relation's lock while the reset runs.
    """
    held = _HeldReader(os.environ["DATABASE_URL"])
    try:
        yield held
    finally:
        held.release()


def _reset_in_thread(e2e_config) -> tuple[threading.Thread, dict]:
    from tests.bdd.conftest import _reset_e2e_db

    outcome: dict = {}

    def run() -> None:
        try:
            _reset_e2e_db(e2e_config)
            outcome["ok"] = True
        except BaseException as exc:  # noqa: BLE001 - reported, never swallowed
            outcome["error"] = f"{type(exc).__name__}: {exc}"

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, outcome


@pytest.mark.timeout(120)
def test_reset_completes_and_empties_while_a_reader_holds_media_buys(e2e_config, reader, factory_session):
    """The reset finishes without waiting for the concurrent reader to commit.

    The seeded rows span an FK chain (tenant -> principal -> media buy), so this
    also grades that emptying every table stays order-independent now that the
    ``CASCADE`` which used to supply that property is gone.
    """
    tenant = TenantFactory(tenant_id="prkv48_reader_race")
    principal = PrincipalFactory(tenant=tenant)
    MediaBuyFactory(tenant=tenant, principal=principal)
    factory_session.commit()

    thread, outcome = _reset_in_thread(e2e_config)
    thread.join(_RESET_DEADLINE_SECONDS)
    blocked = thread.is_alive()

    # Unblock before asserting: see _HeldReader.release.
    reader.release()
    thread.join(_RESET_DEADLINE_SECONDS)

    assert not blocked, (
        f"_reset_e2e_db was still running after {_RESET_DEADLINE_SECONDS}s while a plain reader held an "
        "AccessShareLock on media_buys. It is taking a lock mode that conflicts with readers "
        "(AccessExclusiveLock, i.e. TRUNCATE/DDL) — that is the #2048 deadlock cycle."
    )
    assert outcome == {"ok": True}, outcome

    for table in ("media_buys", "principals", "tenants"):
        remaining = factory_session.execute(text(f"SELECT count(*) FROM {table}")).scalar()
        assert remaining == 0, f"{table} still has {remaining} row(s) after the reset"


@pytest.mark.timeout(120)
def test_reset_restarts_identity_sequences(e2e_config, factory_session):
    """Emptying by DELETE must still restart sequences, as RESTART IDENTITY did.

    Without this the DELETE rewrite would silently drop half of what the old
    TRUNCATE guaranteed, and the scenarios that depend on a fresh serial id would
    fail far from here.
    """
    from tests.bdd.conftest import _reset_e2e_db

    TenantFactory(tenant_id="prkv48_identity")
    factory_session.execute(
        text(
            "INSERT INTO audit_logs (tenant_id, timestamp, operation, principal_name, principal_id, success) "
            "VALUES ('prkv48_identity', now(), 'op', 'P', 'p', true)"
        )
    )
    first_id = factory_session.execute(text("SELECT log_id FROM audit_logs")).scalar()
    assert first_id is not None
    # Leave no transaction open: this test grades the sequence, not lock contention,
    # and an idle-in-transaction reader here would just re-stage the other test.
    factory_session.commit()

    _reset_e2e_db(e2e_config)

    next_id = factory_session.execute(text("SELECT nextval(pg_get_serial_sequence('audit_logs', 'log_id'))")).scalar()
    assert next_id == 1, f"sequence not restarted: nextval returned {next_id}"
