"""Regression test for the test-stack DB connect-retry helper (salesagent-qpst).

Targets the behaviour of ``tests.conftest_db._connect_with_retry`` directly:
the parallel-tox port-collision race is non-deterministic and cannot be
reproduced as a deterministic failing test, so we inject the failure mode
(``psycopg2.OperationalError``) and assert the helper's contract:

1. transient OperationalError -> retried, then the real connection is returned
2. persistent failure -> a CLEAR, bounded RuntimeError (never infinite, never a
   raw psycopg2 traceback surfacing on a random test)

This is the legitimate unit-test case: the unit under test *is* the retry/
backoff helper; an integration repro of the race would itself be flaky.
"""

from unittest.mock import patch

import psycopg2
import pytest

from tests.conftest_db import _connect_with_retry

_CONN_PARAMS = {"host": "localhost", "port": 54321, "user": "u", "password": "p", "database": "postgres"}


def test_retries_transient_operationalerror_then_succeeds():
    """A few transient OperationalErrors are tolerated; the real conn is returned."""
    sentinel_conn = object()
    attempts_seen = []

    def flaky_connect(**kwargs):
        attempts_seen.append(1)
        if len(attempts_seen) < 3:
            raise psycopg2.OperationalError("connection to server ... received invalid response to SSL negotiation")
        return sentinel_conn

    with patch("psycopg2.connect", side_effect=flaky_connect), patch("tests.conftest_db.time.sleep") as sleep:
        result = _connect_with_retry(_CONN_PARAMS, attempts=5, base_delay=0.01, max_delay=0.02)

    assert result is sentinel_conn, "helper must return the real connection once it succeeds"
    assert len(attempts_seen) == 3, "helper must retry the two transient failures, not give up early"
    assert sleep.call_count == 2, "helper must back off between retries (once per transient failure)"


def test_persistent_failure_raises_bounded_clear_error_not_psycopg2():
    """Never infinite, never a raw psycopg2 error: bounded RuntimeError with context."""
    calls = []

    def always_fail(**kwargs):
        calls.append(1)
        raise psycopg2.OperationalError("received invalid response to SSL negotiation")

    with patch("psycopg2.connect", side_effect=always_fail), patch("tests.conftest_db.time.sleep"):
        with pytest.raises(RuntimeError) as excinfo:
            _connect_with_retry(_CONN_PARAMS, attempts=4, base_delay=0.01, max_delay=0.02)

    # Bounded: exactly `attempts` tries, not infinite.
    assert len(calls) == 4, f"helper must stop after exactly `attempts` tries, got {len(calls)}"
    # Clear & actionable: not a bare psycopg2.OperationalError bubbling up.
    assert not isinstance(excinfo.value, psycopg2.OperationalError)
    msg = str(excinfo.value)
    assert "localhost:54321" in msg, "error must name the unreachable host:port"
    assert "salesagent-qpst" in msg, "error must point at the tracking issue/cause"
    # The original psycopg2 error is chained for debuggability.
    assert isinstance(excinfo.value.__cause__, psycopg2.OperationalError)
