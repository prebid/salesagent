"""MediaBuyRepository persisted revision counter: bump semantics (unit level).

The AdCP 3.1.1 ``revision`` response field is a persisted monotonic
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
from pydantic import ValidationError

from src.core.database.models import MediaBuy
from src.core.database.repositories.media_buy import MediaBuyRepository
from src.core.schemas._base import CreateMediaBuySuccess, UpdateMediaBuySuccess


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
    (status/budget/dates). On the locked seams the lock — together with the
    ``populate_existing`` re-read — is itself what keeps the revision counter
    collision-free (proven by
    ``TestConcurrentRevisionBump.test_two_concurrent_bumps_yield_distinct_revisions``).

    ``apply_status_transition`` (scheduler sweep, creative-sync) ALSO takes a
    ``FOR UPDATE`` lock, but its locked refresh (``_LIFECYCLE_REFRESH_FIELDS``)
    deliberately EXCLUDES ``revision`` — the lifecycle inputs (status/window/
    confirmed_at) are reloaded, the counter is not — so the stale identity-mapped
    ``revision`` is never overwritten by the re-read, leaving the server-side
    ``coalesce(revision, 0) + 1`` increment as the SEPARATE, sole protection
    against a lost bump on that seam, proven by
    ``TestConcurrentRevisionBump.test_two_concurrent_apply_status_transition_yield_distinct_revisions``.
    These assertions only pin that the locking read is issued; they intentionally
    say nothing about the resulting value.
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


class TestLifecycleRefreshExcludesRevision:
    """``revision`` must stay OUT of ``_LIFECYCLE_REFRESH_FIELDS``.

    ``apply_status_transition`` locks + refreshes the lifecycle inputs before
    computing a target, but the revision counter is bumped by a server-side
    expression. If ``revision`` were added to the refresh set, the locked re-read
    would reload the committed counter and a Python read-modify-write bump would
    become collision-free too — masking a regression the concurrent integration
    test (``test_two_concurrent_apply_status_transition_yield_distinct_revisions``)
    is meant to catch. This guard pins the exclusion.
    """

    def test_revision_not_in_lifecycle_refresh_fields(self):
        assert "revision" not in MediaBuyRepository._LIFECYCLE_REFRESH_FIELDS, (
            "revision must not be in _LIFECYCLE_REFRESH_FIELDS — refreshing it under the lock "
            "would mask a lost-update regression in the server-side bump"
        )


class TestRevisionNumericStringValidationParity:
    """A numeric-string revision remains raw until the shared request boundary."""

    def test_a2a_raw_dict_rejects_numeric_string_revision(self):
        from pydantic import ValidationError

        from src.core.schemas import UpdateMediaBuyRequest

        with pytest.raises(ValidationError, match="revision"):
            UpdateMediaBuyRequest(media_buy_id="mb-1", revision="7")

    def test_rest_body_preserves_numeric_string_revision_for_shared_validation(self):
        # media_buy_id is a PATH parameter on PUT /media-buys/{id}, not a body field;
        # UpdateMediaBuyBody (SalesAgentBaseModel, extra="forbid" in dev/CI) carries
        # only the updatable fields.
        from src.routes.api_v1 import UpdateMediaBuyBody

        assert UpdateMediaBuyBody.model_validate({"revision": "7"}).revision == "7"

    def test_shared_boundary_rejects_numeric_string_as_invalid_request(self):
        from src.core.exceptions import AdCPInvalidRequestError
        from src.core.tools.media_buy_update import _build_update_request

        with pytest.raises(AdCPInvalidRequestError, match="revision"):
            _build_update_request(media_buy_id="mb-1", paused=True, revision="7")


class TestRevisionSuccessFieldConstraint:
    """The wire ``revision`` on the success envelopes keeps the 3.1.1 ``ge=1`` bound.

    AdCP 3.1.1 types ``revision`` as ``integer`` with ``minimum: 1`` (the pinned
    ``adcp`` 6.6 parents carry ``Ge(ge=1)``). Both ``CreateMediaBuySuccess`` and
    ``UpdateMediaBuySuccess`` override the field to default it to the spec-minimum
    initial revision 1; the override MUST re-declare the ``ge=1`` constraint so a
    plain ``revision: int = 1`` cannot silently widen the domain to accept 0 or
    negative counters. Regression for #1544 (the override had dropped the bound).
    """

    @pytest.mark.parametrize("bad_revision", [0, -1])
    def test_create_success_rejects_sub_minimum_revision(self, bad_revision):
        with pytest.raises(ValidationError, match="revision"):
            CreateMediaBuySuccess(
                media_buy_id="mb_1",
                packages=[],
                context={},
                confirmed_at=None,
                revision=bad_revision,
            )

    def test_create_success_accepts_minimum_revision(self):
        resp = CreateMediaBuySuccess(
            media_buy_id="mb_1",
            packages=[],
            context={},
            confirmed_at=None,
            revision=1,
        )
        assert resp.revision == 1

    def test_create_success_defaults_to_minimum_revision(self):
        # The dry-run/sandbox arm constructs without an explicit revision.
        resp = CreateMediaBuySuccess(
            media_buy_id="mb_1",
            packages=[],
            context={},
            confirmed_at=None,
        )
        assert resp.revision == 1

    @pytest.mark.parametrize("bad_revision", [0, -1])
    def test_update_success_rejects_sub_minimum_revision(self, bad_revision):
        with pytest.raises(ValidationError, match="revision"):
            UpdateMediaBuySuccess(
                media_buy_id="mb_1",
                status="completed",
                affected_packages=[],
                revision=bad_revision,
            )

    def test_update_success_accepts_minimum_revision(self):
        resp = UpdateMediaBuySuccess(
            media_buy_id="mb_1",
            status="completed",
            affected_packages=[],
            revision=1,
        )
        assert resp.revision == 1

    def test_update_success_defaults_to_minimum_revision(self):
        resp = UpdateMediaBuySuccess(
            media_buy_id="mb_1",
            status="completed",
            affected_packages=[],
        )
        assert resp.revision == 1
