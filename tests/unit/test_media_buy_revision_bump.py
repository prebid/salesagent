"""MediaBuyRepository persisted revision counter: bump semantics.

The AdCP GA ``revision`` response field is a persisted monotonic
optimistic-concurrency counter (``media_buys.revision``), NOT a value derived
from timestamps — any formula based on ``created_at``/``updated_at`` collides
when two updates land within the clock resolution.

These tests exercise the REAL repository bump logic against a mocked
SQLAlchemy session holding a real ``MediaBuy`` ORM instance: the assertions
are on the ORM object's ``revision`` attribute after production code ran,
never on mock return values.
"""

from unittest.mock import MagicMock

import pytest

from src.core.database.models import MediaBuy
from src.core.database.repositories.media_buy import MediaBuyRepository


def _repo_with_media_buy(media_buy: MediaBuy | None) -> MediaBuyRepository:
    """Repository whose ``get_by_id`` resolves to *media_buy* (or misses)."""
    session = MagicMock()
    session.scalars.return_value.first.return_value = media_buy
    return MediaBuyRepository(session, "tenant-1")


def _transient_media_buy(revision: int = 1) -> MediaBuy:
    """Real (transient) MediaBuy ORM instance — attribute mutation is real logic."""
    return MediaBuy(
        media_buy_id="mb-rev-1",
        tenant_id="tenant-1",
        principal_id="principal-1",
        status="active",
        revision=revision,
    )


class TestBumpRevision:
    def test_bump_revision_increments_by_one(self):
        mb = _transient_media_buy(revision=1)
        repo = _repo_with_media_buy(mb)

        result = repo.bump_revision("mb-rev-1")

        assert result is mb
        assert mb.revision == 2

    def test_bump_revision_is_strictly_monotonic_across_consecutive_bumps(self):
        """Two immediate bumps yield strictly increasing values — the exact
        scenario a time-derived formula gets wrong (same-second collision)."""
        mb = _transient_media_buy(revision=1)
        repo = _repo_with_media_buy(mb)

        repo.bump_revision("mb-rev-1")
        first = mb.revision
        repo.bump_revision("mb-rev-1")
        second = mb.revision

        assert (first, second) == (2, 3)
        assert second > first

    def test_bump_revision_tolerates_legacy_null(self):
        """A pre-backfill row (revision unset) bumps to 1, never crashes."""
        mb = _transient_media_buy(revision=1)
        mb.revision = None  # type: ignore[assignment]  # simulate legacy row
        repo = _repo_with_media_buy(mb)

        repo.bump_revision("mb-rev-1")

        assert mb.revision == 1

    def test_bump_revision_missing_buy_returns_none(self):
        repo = _repo_with_media_buy(None)
        assert repo.bump_revision("mb-missing") is None


class TestMutationPathsBump:
    def test_update_status_bumps_revision(self):
        mb = _transient_media_buy(revision=3)
        repo = _repo_with_media_buy(mb)

        result = repo.update_status("mb-rev-1", "paused")

        assert result is mb
        assert mb.status == "paused"
        assert mb.revision == 4

    def test_update_fields_bumps_revision(self):
        mb = _transient_media_buy(revision=1)
        repo = _repo_with_media_buy(mb)

        result = repo.update_fields("mb-rev-1", budget=250.0, currency="USD")

        assert result is mb
        assert float(mb.budget) == 250.0
        assert mb.revision == 2

    def test_update_fields_rejects_direct_revision_write(self):
        """revision is repository-managed: callers may never set it directly."""
        mb = _transient_media_buy(revision=5)
        repo = _repo_with_media_buy(mb)

        with pytest.raises(ValueError, match="revision"):
            repo.update_fields("mb-rev-1", revision=99)

        assert mb.revision == 5  # untouched
