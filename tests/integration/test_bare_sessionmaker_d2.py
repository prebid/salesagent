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
def test_each_get_db_session_yields_fresh_instance(integration_db):
    """Two sequential get_db_session() blocks yield distinct Session objects.

    Under scoped_session (pre-L0-03), the registry would hand back the
    SAME Session instance on both calls (same thread -> same registry
    entry). Under bare sessionmaker (post-L0-03), each call constructs
    a fresh Session, so `id()` differs.
    """
    from src.core.database.database_session import get_db_session

    with get_db_session() as s1:
        id_s1 = id(s1)
    with get_db_session() as s2:
        id_s2 = id(s2)

    assert id_s1 != id_s2, (
        "Expected distinct Session instances from two sequential "
        "get_db_session() calls (bare sessionmaker D2). Same id suggests "
        "scoped_session registry is still active."
    )


@pytest.mark.requires_db
def test_concurrent_get_db_session_in_threads_yields_independent_sessions(integration_db):
    """Concurrent threadpool invocations get independent Session instances.

    FastAPI's AnyIO threadpool reuses threads across requests. Under
    scoped_session, two requests landing on the same reused thread
    would share a Session via the thread-local registry — a state-leak
    bug. Under bare sessionmaker, each `with get_db_session()` block
    constructs a fresh Session regardless of which thread it runs on.
    """
    from src.core.database.database_session import get_db_session

    ids: list[int] = []

    def capture():
        with get_db_session() as s:
            ids.append(id(s))

    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = [pool.submit(capture) for _ in range(5)]
        for f in futures:
            f.result()

    assert len(set(ids)) == 5, (
        f"Expected 5 distinct Session ids, got {len(set(ids))}: {ids}. "
        "Duplicate ids indicate a thread-local session registry is still "
        "active (scoped_session) or sessions are being reused across calls."
    )
