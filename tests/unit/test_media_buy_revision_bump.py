"""MediaBuyRepository persisted revision counter: bump semantics (unit level).

The AdCP 3.1.0-beta.3 ``revision`` response field is a persisted monotonic
optimistic-concurrency counter (``media_buys.revision``), NOT a value derived
from timestamps — any formula based on ``created_at``/``updated_at`` collides
when two updates land within the clock resolution.

Scope of THIS file: the parts of the bump contract that need no database — the
not-found short-circuit and the immutable-field guard. The bump itself is a
**server-side** SQL expression (``coalesce(revision, 0) + 1``, see
``_bump_revision``) that only materializes on flush, so the resulting value can
only be asserted against a real database. Those value assertions live in
``tests/integration/test_media_buy_repository_writes.py``
(``TestPersistedRevisionBump`` / ``TestConcurrentRevisionBump``) and
``tests/integration/test_media_buy_revision.py`` — never on a transient ORM
attribute here, which would only hold the unflushed SQL expression object.
"""

from unittest.mock import MagicMock

import pytest

from src.core.database.models import MediaBuy
from src.core.database.repositories.media_buy import MediaBuyRepository


def _session_returning(media_buy: MediaBuy | None) -> MagicMock:
    """Mock SQLAlchemy session whose ``scalars(...).first()`` yields *media_buy*."""
    session = MagicMock()
    session.scalars.return_value.first.return_value = media_buy
    return session


def _repo_with_media_buy(media_buy: MediaBuy | None) -> MediaBuyRepository:
    """Repository whose ``get_by_id`` resolves to *media_buy* (or misses)."""
    return MediaBuyRepository(_session_returning(media_buy), "tenant-1")


def _transient_media_buy(revision: int = 1) -> MediaBuy:
    """Real (transient) MediaBuy ORM instance for the no-bump code paths."""
    return MediaBuy(
        media_buy_id="mb-rev-1",
        tenant_id="tenant-1",
        principal_id="principal-1",
        status="active",
        revision=revision,
    )


class TestBumpRevisionShortCircuits:
    """Paths that return before any bump runs — no DB needed."""

    def test_bump_revision_missing_buy_returns_none(self):
        assert _repo_with_media_buy(None).bump_revision("mb-missing") is None

    def test_update_status_missing_buy_returns_none(self):
        assert _repo_with_media_buy(None).update_status("mb-missing", "paused") is None

    def test_update_fields_rejects_direct_revision_write(self):
        """revision is repository-managed: callers may never set it directly.

        The guard raises BEFORE the row is even loaded, so no bump/flush occurs —
        this is asserted at unit level precisely because it is DB-independent.
        """
        repo = _repo_with_media_buy(_transient_media_buy(revision=5))

        with pytest.raises(ValueError, match="revision"):
            repo.update_fields("mb-rev-1", revision=99)


class TestMutationLoadsAreRowLocked:
    """The mutation load takes a row lock (SELECT ... FOR UPDATE).

    The lock serializes concurrent writers to the same buy's mutable columns
    (status/budget/dates). It is COMPLEMENTARY to — not the source of — the
    revision counter's collision-freedom: that is guaranteed by the server-side
    ``coalesce(revision, 0) + 1`` increment, proven under real contention by
    ``TestConcurrentRevisionBump``. These assertions only pin that the locking
    read is issued; they intentionally say nothing about the resulting value.
    """

    @staticmethod
    def _first_stmt_for(call):
        mb = _transient_media_buy(revision=1)
        session = _session_returning(mb)
        repo = MediaBuyRepository(session, "tenant-1")
        call(repo)
        return str(session.scalars.call_args_list[0].args[0])

    def test_update_status_load_is_for_update(self):
        assert "FOR UPDATE" in self._first_stmt_for(lambda r: r.update_status("mb-rev-1", "paused"))

    def test_update_fields_load_is_for_update(self):
        assert "FOR UPDATE" in self._first_stmt_for(lambda r: r.update_fields("mb-rev-1", status="paused"))

    def test_bump_revision_load_is_for_update(self):
        assert "FOR UPDATE" in self._first_stmt_for(lambda r: r.bump_revision("mb-rev-1"))

    def test_plain_get_by_id_is_not_locked(self):
        # A read-only lookup must NOT hold a row lock.
        assert "FOR UPDATE" not in self._first_stmt_for(lambda r: r.get_by_id("mb-rev-1"))
