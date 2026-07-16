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
    The server-side ``coalesce(revision, 0) + 1`` increment is the SEPARATE, sole
    protection for the UNLOCKED seam (``apply_status_transition``), proven by
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


class TestRevisionNumericStringCoercionDivergence:
    """#1582: a numeric-string revision like ``"7"`` diverges by transport.

    JSON Schema declares ``revision`` as ``type: integer``, so ``"7"`` is a
    wrong-TYPE value. The A2A raw-dict path (``UpdateMediaBuyRequest``) enforces
    that in a before-validator and rejects it; the REST body
    (``UpdateMediaBuyBody``) and the MCP typed param instead lax-coerce ``"7" ->
    7`` before the request model runs, so they accept it. This case has no single
    cross-transport outcome, which is why the BDD ``wrong_type`` partition uses
    ``"not-an-int"`` (rejected everywhere) — a Scenario Outline row grades one
    outcome per transport. This test is where the numeric-string coercion
    divergence itself stays exercised. Deferred, tracked in #1582.
    """

    def test_a2a_raw_dict_rejects_numeric_string_revision(self):
        from pydantic import ValidationError

        from src.core.schemas import UpdateMediaBuyRequest

        with pytest.raises(ValidationError, match="revision"):
            UpdateMediaBuyRequest(media_buy_id="mb-1", revision="7")

    def test_rest_body_lax_coerces_numeric_string_revision(self):
        # media_buy_id is a PATH parameter on PUT /media-buys/{id}, not a body field;
        # UpdateMediaBuyBody (SalesAgentBaseModel, extra="forbid" in dev/CI) carries
        # only the updatable fields. The body still LAX-coerces a numeric string "7" -> 7.
        from src.routes.api_v1 import UpdateMediaBuyBody

        assert UpdateMediaBuyBody.model_validate({"revision": "7"}).revision == 7


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
