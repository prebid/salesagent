"""D2 bare-sessionmaker semantic identity assertions (L0-03 Red/Green pair).

Decision D2 (2026-04-16) retires scoped_session from
src/core/database/database_session.py. Under bare sessionmaker, each
`with get_db_session()` block must yield a *fresh* Session instance
(distinct object identity) — never a thread-local registry-shared
instance. This property is what makes FastAPI's AnyIO threadpool safe
for sync admin handlers: no thread-local state can leak between
requests because there is no registry at all.

These tests assert the semantic contract. Under the pre-L0-03
scoped_session implementation, two `with` blocks on the same thread
return the *same* registered Session instance, so the first assertion
fails. Under bare sessionmaker, each block yields a distinct instance
and both assertions pass.

Per CLAUDE.md Critical Invariant #4 and
.claude/notes/flask-to-fastapi/L0-implementation-plan-v2.md §L0-03.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest


@pytest.mark.requires_db
def test_module_no_longer_exposes_scoped_session_accessor(integration_db):
    """Post-L0-03, src.core.database.database_session has no
    `get_scoped_session()` accessor.

    The most direct semantic of D2 is the absence of the thread-local
    registry API. Pre-L0-03 the module exposed `get_scoped_session()`
    which returned a `scoped_session` registry; callers invoked
    `registry.remove()` for thread-local session lifecycle. Post-L0-03
    the accessor is deleted and only the bare `_session_factory` remains.

    Note: We assert on the public ACCESSOR, not on the `_scoped_session`
    module global — test fixtures in tests/conftest_db.py and
    tests/fixtures/integration_db.py still write to the global during
    engine override (harmless dead attribute), so hasattr on the global
    is not a reliable discriminator under the test harness.
    """
    import src.core.database.database_session as db_module

    # Force lazy initialization so the full object graph is materialized.
    db_module.get_engine()

    assert not hasattr(db_module, "get_scoped_session"), (
        "get_scoped_session() must be deleted under Decision D2. "
        "Its continued existence signals a scoped_session registry "
        "still lurks in the module."
    )

    # The bare session factory must be in place and callable.
    assert db_module._session_factory is not None
    from sqlalchemy.orm import Session

    s = db_module._session_factory()
    try:
        assert isinstance(s, Session)
    finally:
        s.close()


@pytest.mark.requires_db
def test_nested_get_db_session_yields_distinct_instances(integration_db):
    """Nested `with get_db_session()` blocks on the same thread yield distinct
    Session instances.

    This is THE bug scoped_session could introduce in a nested-session
    call site (the PRE-1/2/3 refactors eliminated all known nested call
    sites, but the language-level guarantee was only established by D2).
    Under scoped_session with the thread-local registry still active,
    the inner `with` block would receive the SAME Session instance as
    the outer — so `id(inner) == id(outer)`. Under bare sessionmaker,
    each block gets a fresh Session with a distinct id.

    Both sessions are held open simultaneously, so object-id reuse from
    garbage collection cannot confound the identity check.
    """
    from src.core.database.database_session import get_db_session

    with get_db_session() as outer:
        outer_id = id(outer)
        with get_db_session() as inner:
            inner_id = id(inner)
            # Both sessions are alive simultaneously here.
            assert outer_id != inner_id, (
                "Nested get_db_session() blocks yielded the SAME Session "
                "instance — scoped_session's thread-local registry is "
                f"still active (id(outer)={outer_id}, id(inner)={inner_id}). "
                "Under Decision D2 bare sessionmaker, each block must "
                "construct a fresh Session."
            )


@pytest.mark.requires_db
def test_concurrent_get_db_session_in_threads_yields_independent_sessions(integration_db):
    """Concurrent threadpool invocations get independent Session instances.

    FastAPI's AnyIO threadpool reuses threads across requests. Under
    bare sessionmaker, each `with get_db_session()` block constructs a
    fresh Session regardless of which thread it runs on. We hold each
    Session open for the duration of a barrier-rendezvous so that all
    workers' Sessions are alive simultaneously — no object-id reuse
    from GC can confound the identity check.
    """
    import threading

    from src.core.database.database_session import get_db_session

    n_workers = 5
    barrier = threading.Barrier(n_workers)
    ids: list[int] = []
    ids_lock = threading.Lock()

    def capture():
        with get_db_session() as s:
            barrier.wait(timeout=10)
            with ids_lock:
                ids.append(id(s))
            barrier.wait(timeout=10)

    with ThreadPoolExecutor(max_workers=n_workers) as pool:
        futures = [pool.submit(capture) for _ in range(n_workers)]
        for f in futures:
            f.result()

    assert len(set(ids)) == n_workers, (
        f"Expected {n_workers} distinct Session ids, got {len(set(ids))}: {ids}. "
        "Duplicate ids (with all sessions alive simultaneously) indicate "
        "sessions are being shared across concurrent workers."
    )
