"""Smoke tests for ``core.idempotency.get_idempotency_store``.

Verifies:
- ``CORE_IDEMPOTENCY_BACKEND=memory`` returns MemoryBackend (test default).
- Missing DATABASE_URL falls back to MemoryBackend (tooling/import path).
- DATABASE_URL set → PgBackend is wired and the ``adcp_idempotency`` table
  exists after first call.
- The store survives across repeated calls (process singleton).
"""

from __future__ import annotations

import asyncio
import os

import psycopg2
import pytest
from adcp.server.idempotency import IdempotencyStore, MemoryBackend, PgBackend

import core.idempotency as idem

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _reset_store():
    """Drop the cached store between tests so env flips take effect."""
    idem.reset_for_tests()
    yield
    idem.reset_for_tests()


def test_memory_backend_when_env_says_memory(monkeypatch):
    monkeypatch.setenv("CORE_IDEMPOTENCY_BACKEND", "memory")
    store = idem.get_idempotency_store()
    assert isinstance(store, IdempotencyStore)
    assert isinstance(store.backend, MemoryBackend)


def test_memory_backend_when_no_database_url(monkeypatch):
    monkeypatch.delenv("CORE_IDEMPOTENCY_BACKEND", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    store = idem.get_idempotency_store()
    assert isinstance(store.backend, MemoryBackend)


@pytest.mark.requires_db
def test_pg_backend_when_database_url_set(monkeypatch, integration_db):
    monkeypatch.setenv("CORE_IDEMPOTENCY_BACKEND", "pg")
    store = idem.get_idempotency_store()
    assert isinstance(store.backend, PgBackend)

    # The pool and schema are bootstrapped lazily on first async backend use.
    assert asyncio.run(store.backend.get("scope", "missing-key")) is None

    # adcp_idempotency table should exist after the first backend call.
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    try:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM information_schema.tables WHERE table_name = 'adcp_idempotency'")
        assert cur.fetchone() is not None, "adcp_idempotency table missing after PgBackend init"
    finally:
        conn.close()


def test_store_is_process_singleton(monkeypatch):
    """Repeated calls return the same store object — no rebuilds."""
    monkeypatch.setenv("CORE_IDEMPOTENCY_BACKEND", "memory")
    first = idem.get_idempotency_store()
    second = idem.get_idempotency_store()
    assert first is second
