"""Integration test: the repository maintains revision and confirmed_at.

These two fields are only meaningful as PERSISTED state, so they are graded here
against a real Postgres rather than in a unit test: what the assertions read back
is the row, not a Python attribute a mock happened to receive. That is enforced,
not merely intended — every assertion goes through ``_reread``, which expires the
identity map and re-SELECTs with autoflush suppressed, so a value set on the
object but never written to the database reddens instead of passing.

What this file grades, precisely:
  - every repository write path that mutates a media buy moves the revision
    counter, and the counter is strictly increasing across successive writes;
  - both repository create paths seed it at 1;
  - confirmed_at is stamped at the seller-commitment instant, once, and is
    immutable to callers;
  - the commitment lookup fails CLOSED — a status the vocabulary does not contain
    stamps nothing, and does not raise out of the write path either;
  - MediaBuyFactory, which persists without the repository, seeds the same
    confirmed_at the repository would have written (the fixture seam).

  - the bump is emitted as a SQL expression (``revision = revision + 1``) and not
    as a Python read-modify-write. That form is the whole reason two CONCURRENT
    bumps serialize in the database, and it used to be ungraded: swapping
    ``_bump_revision`` to a read-modify-write left every other test in this file
    green, so the protection could be deleted silently. ``TestConcurrentBumps``
    below discriminates the two implementations against two real connections.

Semantics adopted verbatim from PR #1544 (GH #1928 requires reconciling with it
rather than deciding independently):
  - revision is a monotonic optimistic-concurrency token, bumped on EVERY successful
    mutation, repository-managed and immutable to callers.
  - confirmed_at is the seller-commitment instant, written ONCE and stable across all
    later transitions.
"""

import datetime
from decimal import Decimal

import pytest

from src.core.database.models import MediaBuy, PersistedMediaBuyStatus, is_media_buy_seller_confirmed
from src.core.database.repositories.media_buy import MediaBuyRepository
from src.core.exceptions import AdCPPersistedStateError
from src.core.schemas import CreateMediaBuyRequest
from src.core.tools._media_buy_status import PERSISTED_STATUS_TO_CANONICAL

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

# Every timestamp the buy already carries is pinned here, far outside any window a
# test measures. That is what makes the stamp assertions discriminating: a stamp
# that copied created_at or approved_at instead of reading the clock would land in
# 2020 and fail the window, where a presence-only assertion would not notice.
_PAST = datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC)

# A value no writer in this tree produces and the commitment vocabulary therefore
# does not contain. Short enough for the varchar(20) status column.
_UNRECOGNISED_STATUS = "some_new_status"


@pytest.fixture
def repo_env(integration_db, bound_factory_session):
    """A pending_approval media buy plus a repository scoped to its tenant.

    Session and factory binding come from the shared ``bound_factory_session``
    fixture rather than a local bind-then-None: it restores whatever binding was
    there before instead of nulling, which is the only version that is correct when
    something outside has already bound.
    """
    from tests.factories import MediaBuyFactory

    media_buy = MediaBuyFactory(status="pending_approval", created_at=_PAST, approved_at=_PAST)
    yield MediaBuyRepository(bound_factory_session, media_buy.tenant_id), media_buy


def _reread(repo: MediaBuyRepository, media_buy_id: str) -> MediaBuy:
    """Re-read the ROW, not the session's identity map.

    ``repo.get_by_id`` on its own hands back the instance already living in the
    session, so an assertion on it passes for a value that was set on the Python
    object but never written to the database — a stamp applied after the last
    flush, a write on a detached instance, an attribute set via
    ``set_committed_value``. That is precisely the regression class this file
    exists to catch, so every oracle here expires first and reads with autoflush
    suppressed: expiring forces a real SELECT, and suppressing autoflush stops
    the read from persisting a pending value on the writer's behalf and then
    congratulating it. Every repository write path flushes before returning, so
    nothing legitimate is hidden by that suppression.
    """
    session = repo._session  # noqa: SLF001 — the oracle must read the row this repo wrote
    session.expire_all()
    with session.no_autoflush:
        media_buy = repo.get_by_id(media_buy_id)
    assert media_buy is not None, f"media buy {media_buy_id!r} is not in the database"
    return media_buy


def _revision(repo: MediaBuyRepository, media_buy_id: str) -> int:
    """The persisted counter."""
    return _reread(repo, media_buy_id).revision


def _confirmed_at(repo: MediaBuyRepository, media_buy_id: str) -> datetime.datetime | None:
    """The persisted seller-commitment instant."""
    return _reread(repo, media_buy_id).confirmed_at


def _assert_stamped_between(
    stamped: datetime.datetime | None,
    t0: datetime.datetime,
    t1: datetime.datetime,
) -> None:
    """The stamp must be a fresh clock reading taken during the write, not a copied field."""
    assert stamped is not None, "confirmed_at was never stamped"
    assert t0 <= stamped <= t1, (
        f"confirmed_at={stamped.isoformat()} is outside the write window "
        f"[{t0.isoformat()}, {t1.isoformat()}] — it was copied from another column "
        f"(created_at/approved_at are pinned at {_PAST.isoformat()}) rather than read from the clock"
    )


def _make_request(idempotency_key: str) -> CreateMediaBuyRequest:
    return CreateMediaBuyRequest(
        account={"account_id": "acct_test"},
        brand={"domain": "testbrand.com"},
        packages=[{"product_id": "prod_1", "budget": 5000.0, "pricing_option_id": "po_1"}],
        start_time=(datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=1)).isoformat(),
        end_time=(datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=8)).isoformat(),
        idempotency_key=idempotency_key,
    )


def _create_from_request(repo: MediaBuyRepository, media_buy: MediaBuy) -> MediaBuy:
    """The async/create_media_buy path: the repository builds the row from the request model."""
    return repo.create_from_request(
        seller_committed=True,
        media_buy_id="mb_create_from_request",
        req=_make_request("revision-confirmation-create-from-request"),
        principal_id=media_buy.principal_id,
        advertiser_name="Test Advertiser",
        budget=Decimal("5000.00"),
        currency="USD",
        start_time=datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=1),
        end_time=datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=8),
        status="active",
        created_at=_PAST,
    )


def _create_prebuilt(repo: MediaBuyRepository, media_buy: MediaBuy) -> MediaBuy:
    """The sync path: the caller hands over a fully-built row in its final status."""
    from tests.factories import MediaBuyFactory

    return repo.create(
        seller_committed=True,
        media_buy=MediaBuyFactory.build(
            tenant=media_buy.tenant,
            principal=media_buy.principal,
            media_buy_id="mb_create_prebuilt",
            status="active",
            created_at=_PAST,
            approved_at=_PAST,
            # The factory stamps confirmed_at for committed statuses (it persists
            # without the repository elsewhere, so it has to). Here the repository
            # IS under test: a pre-stamped row short-circuits
            # _stamp_confirmation_if_needed, and the create-path stamp assertion
            # would then grade the FACTORY's value and stay green even with
            # create()'s stamp deleted. Opt out so the oracle keeps its subject.
            confirmed_at=None,
        ),
    )


# Both repository entry points that persist a brand-new media buy. Each must seed
# revision at 1 and stamp confirmed_at when the row is born already committed.
_CREATE_PATHS = {
    "create_from_request": _create_from_request,
    "create": _create_prebuilt,
}


def _write_package_config(repo, media_buy, package):
    return repo.update_package_config(
        media_buy.media_buy_id,
        package.package_id,
        {"package_id": package.package_id, "product_id": "prod_001", "budget": 7500.0},
    )


def _write_package_fields(repo, media_buy, package):
    return repo.update_package_fields(media_buy.media_buy_id, package.package_id, budget=Decimal("7500.00"))


def _write_new_package(repo, media_buy, package):
    return repo.create_package(
        media_buy.media_buy_id,
        "pkg_created",
        {"package_id": "pkg_created", "product_id": "prod_001"},
    )


def _write_packages_bulk(repo, media_buy, package):
    from tests.factories import MediaPackageFactory

    return repo.create_packages_bulk(
        media_buy.media_buy_id,
        # Pass the real parent, not media_buy=None. Setting the relationship to None
        # while hand-setting media_buy_id builds a contradictory row: the child is
        # explicitly disassociated from the parent, yet carries the FK that forms half
        # of its own composite PK. That state is inert only while the parent stays
        # clean — once the parent is legitimately dirty (which is exactly what this
        # test grades), SQLAlchemy's dependency processor honors the disassociation
        # and tries to blank the PK column. No production caller builds it that way.
        [MediaPackageFactory.build(media_buy=media_buy, package_id="pkg_bulk")],
    )


# Every public repository method that persists a package. A package write changes
# what the buyer sees on the parent media buy, so each one moves the parent's
# concurrency token — and each is graded, so folding the bump into one of them
# cannot be mistaken for covering all four.
_PACKAGE_WRITERS = {
    "create_package": _write_new_package,
    "update_package_config": _write_package_config,
    "update_package_fields": _write_package_fields,
    "create_packages_bulk": _write_packages_bulk,
}


class TestTheOracleFailsClosed:
    """The oracles every assertion in this file goes through refuse a missing row.

    ``_reread`` re-SELECTs, so a media buy that is not in the database comes back as
    ``None``. Without the guard, ``_revision`` would then raise
    ``AttributeError: 'NoneType' object has no attribute 'revision'`` — a crash that
    reads like a harness bug rather than the finding it is, and ``_confirmed_at``
    would be worse: it is typed ``| None``, so a caller comparing it to ``None``
    would read "this buy was never confirmed" for a buy that does not exist. Every
    oracle in this file is only as trustworthy as this guard, so it is graded rather
    than assumed.
    """

    def test_revision_of_a_row_that_is_not_there_names_the_missing_id(self, repo_env):
        repo, _media_buy = repo_env
        with pytest.raises(AssertionError, match="mb_never_written"):
            _revision(repo, "mb_never_written")

    def test_confirmed_at_of_a_row_that_is_not_there_refuses_rather_than_reading_none(self, repo_env):
        repo, _media_buy = repo_env
        with pytest.raises(AssertionError, match="mb_never_written"):
            _confirmed_at(repo, "mb_never_written")


class TestRevisionCounter:
    def test_new_media_buy_starts_at_revision_one(self, repo_env):
        repo, media_buy = repo_env
        assert _revision(repo, media_buy.media_buy_id) == 1

    @pytest.mark.parametrize("path_name", sorted(_CREATE_PATHS))
    def test_repository_create_paths_seed_revision_at_one(self, repo_env, path_name):
        """Both create paths persist the counter's floor, not a NULL the reader has to guess."""
        repo, media_buy = repo_env

        created = _CREATE_PATHS[path_name](repo, media_buy)

        assert _revision(repo, created.media_buy_id) == 1

    def test_back_to_back_updates_yield_strictly_increasing_revisions(self, repo_env):
        """Two updates in the same clock tick must still produce 2 then 3.

        This is the scenario that rules out deriving revision from timestamps, and
        the one PR #1544's own integration test pins.
        """
        repo, media_buy = repo_env

        repo.update_fields(media_buy.media_buy_id, budget=20000)
        first = _revision(repo, media_buy.media_buy_id)
        repo.update_fields(media_buy.media_buy_id, budget=30000)
        second = _revision(repo, media_buy.media_buy_id)

        assert (first, second) == (2, 3)

    def test_status_transition_bumps_revision(self, repo_env):
        """Seller-side transitions move the token too, not just buyer updates."""
        repo, media_buy = repo_env

        repo.update_status(media_buy.media_buy_id, "active")

        assert _revision(repo, media_buy.media_buy_id) == 2

    @pytest.mark.parametrize("writer_name", sorted(_PACKAGE_WRITERS))
    def test_package_level_write_bumps_parent_revision(self, repo_env, writer_name):
        """A package write persists outside update_*, but still moves the parent's token.

        The bump belongs INSIDE each package writer: an external "now also bump"
        call is a step a future writer forgets, which is exactly how the counter
        went stale before.
        """
        from tests.factories import MediaPackageFactory

        repo, media_buy = repo_env
        package = MediaPackageFactory(media_buy=media_buy)
        assert _revision(repo, media_buy.media_buy_id) == 1, "factory setup must not move the counter"

        _PACKAGE_WRITERS[writer_name](repo, media_buy, package)

        assert _revision(repo, media_buy.media_buy_id) == 2

    def test_revision_is_immutable_to_callers(self, repo_env):
        """It is repository-managed: a caller writing it would break monotonicity."""
        repo, media_buy = repo_env

        with pytest.raises(ValueError, match="immutable field"):
            repo.update_fields(media_buy.media_buy_id, revision=99)

        assert _revision(repo, media_buy.media_buy_id) == 1


class TestConfirmedAtStamp:
    def test_unconfirmed_status_leaves_confirmed_at_null(self, repo_env):
        """pending_approval is not a seller commitment, so nothing is stamped."""
        repo, media_buy = repo_env

        repo.update_fields(media_buy.media_buy_id, budget=20000)

        assert _confirmed_at(repo, media_buy.media_buy_id) is None

    def test_reaching_a_committed_status_stamps_the_transition_instant(self, repo_env):
        """The stamp is the clock reading taken during the transition, not a nearby column."""
        repo, media_buy = repo_env

        t0 = datetime.datetime.now(datetime.UTC)
        repo.update_status(media_buy.media_buy_id, "active", seller_committed=True)
        t1 = datetime.datetime.now(datetime.UTC)

        _assert_stamped_between(_confirmed_at(repo, media_buy.media_buy_id), t0, t1)

    @pytest.mark.parametrize("path_name", sorted(_CREATE_PATHS))
    def test_create_in_a_committed_status_stamps_the_create_instant(self, repo_env, path_name):
        """A buy born already committed is confirmed at creation — the sync auto-approve path.

        Neither create path can leave confirmed_at NULL here: the buy is `active`,
        and the pinned get-media-buys-response schema forbids an active item with a
        null confirmed_at.
        """
        repo, media_buy = repo_env

        t0 = datetime.datetime.now(datetime.UTC)
        created = _CREATE_PATHS[path_name](repo, media_buy)
        t1 = datetime.datetime.now(datetime.UTC)

        _assert_stamped_between(_confirmed_at(repo, created.media_buy_id), t0, t1)

    def test_confirmed_at_is_written_once_and_survives_later_transitions(self, repo_env):
        """The commitment instant must not track the most recent transition."""
        repo, media_buy = repo_env

        repo.update_status(media_buy.media_buy_id, "active", seller_committed=True)
        stamped = _confirmed_at(repo, media_buy.media_buy_id)
        # Pin that the first transition actually persisted a stamp. Without this the
        # equality below is satisfied by None == None, i.e. by a stamp that never
        # reached the row at all.
        assert stamped is not None, "the committing transition did not persist a stamp"

        # Deliberately WITHOUT seller_committed: a later transition is not a new
        # commitment, and must not move the instant even if it were passed one.
        repo.update_status(media_buy.media_buy_id, "completed")

        assert _confirmed_at(repo, media_buy.media_buy_id) == stamped

    def test_rejected_never_stamps(self, repo_env):
        """A rejected buy was never committed to, so it has no commitment instant."""
        repo, media_buy = repo_env

        repo.update_status(media_buy.media_buy_id, "rejected")

        assert _confirmed_at(repo, media_buy.media_buy_id) is None

    def test_an_unrecognised_status_is_refused_at_the_write_boundary(self, repo_env):
        """A status outside the vocabulary cannot be persisted at all.

        The obligation is unchanged — an undefined status must never mint a
        seller-commitment instant — but it is met by REFUSING the write rather than
        by reading the value charitably. That is the stronger form: a value the
        vocabulary has never heard of stops at the boundary, so no reader downstream
        has to decide what it means.

        It has to stop here. The wire projection maps persisted statuses to protocol
        ones, and an unmapped value used to be reported as a generic serving state:
        the buyer received ``active`` for a state nobody defined, with no
        ``confirmed_at`` to go with it — a combination the pinned response schema
        forbids. Refusing the write is what makes that document unrepresentable
        rather than merely unlikely.

        The refusal is TYPED, and the type carries the wire contract: an unmapped
        persisted value is a defect in the seller's own store, so it surfaces as
        ``CONFIGURATION_ERROR`` / ``terminal`` (pinned 3.1.1 ``enums/error-code.json``
        metadata: "surface to a human at the seller ... MUST NOT auto-retry"). The
        bare ``ValueError`` it replaced reached the buyer as
        ``VALIDATION_ERROR`` / ``correctable`` — telling the buyer to "fix field
        values" it neither sent nor owns, and inviting a retry that fails identically.
        The code and recovery are asserted here, not just the exception type, because
        the type without them is the half that was already right.

        The subject of the refusal is read from ``details``, not from ``str(exc)``: the
        buyer-facing message is a function of the code (``CODE_TABLE``), so no raise
        site authors it and asserting on its text would grade the table rather than
        this door. The values the operator needs — which row, which value was refused,
        which set was legal — are exactly what ``ConfigurationDetails`` carries.

        Both halves are asserted because each catches what the other cannot: that the
        write is refused, and that the row is untouched by the attempt.
        """
        repo, media_buy = repo_env
        before = _reread(repo, media_buy.media_buy_id)

        with pytest.raises(AdCPPersistedStateError) as refusal:
            repo.update_status(media_buy.media_buy_id, _UNRECOGNISED_STATUS)

        assert refusal.value.error_code == "CONFIGURATION_ERROR"
        assert refusal.value.recovery == "terminal"
        assert refusal.value.field == "status"
        assert refusal.value.details.rejected_value == _UNRECOGNISED_STATUS
        # The refusal names the ROW, not just the offending value. An operator reading
        # this in a log has no other way to find which buy carries the defect, and the
        # wrapper this door used to route through could not carry the id at all — its
        # signature had no parameter for it.
        assert refusal.value.details.media_buy_id == media_buy.media_buy_id

        after = _reread(repo, media_buy.media_buy_id)
        assert after.status == before.status, f"the refused write still moved the status to {after.status!r}"
        assert after.confirmed_at is None, (
            f"{_UNRECOGNISED_STATUS!r} is not in the vocabulary, yet a seller-commitment instant "
            f"was stamped — a refused write must leave the row entirely alone"
        )
        assert after.revision == before.revision, (
            f"the refused write bumped the concurrency token {before.revision} -> {after.revision}; "
            "a write that did not happen must not move the buyer's token"
        )

    @pytest.mark.parametrize("door", ["update_fields", "create_from_request", "create"])
    def test_every_write_door_refuses_a_status_outside_the_vocabulary(self, repo_env, door):
        """The vocabulary is closed at EVERY door that writes the column, not just one.

        update_status is graded above. These are the other three. A vocabulary enforced
        on one path and bypassable on another is not enforced: a row that got in through
        an unguarded door is one the wire projection cannot describe, so the buyer would
        receive either an invented serving state or a 500 — the reader's problem either
        way, created by a write nobody checked.

        Every door must raise the SAME typed refusal, not merely "some error": the doors
        share one coercion (``PersistedMediaBuyStatus.parse``) precisely so a caller
        cannot get a terminal CONFIGURATION_ERROR from one path and a correctable
        VALIDATION_ERROR from another for the identical defect.

        "The SAME refusal" is read off the code and the typed details rather than off
        the message. This assertion used to be a ``match=`` on the sentence "not a
        member of the media_buys.status vocabulary", which the raise site can no
        longer author — buyer-facing text is a function of the code. The three things
        that sentence conveyed are asserted directly instead: which column
        (``field``), which value was refused (``details.rejected_value``), and which
        member set was legal (``details.accepted_values``). A different
        ``AdCPPersistedStateError`` — the revision door, say — fails all three, so the
        discrimination the ``match=`` provided is kept rather than traded away.
        """
        repo, media_buy = repo_env

        with pytest.raises(AdCPPersistedStateError) as refusal:
            if door == "update_fields":
                repo.update_fields(media_buy.media_buy_id, status=_UNRECOGNISED_STATUS)
            elif door == "create_from_request":
                repo.create_from_request(
                    media_buy_id="mb_bad_status_door",
                    req=_make_request("revision-confirmation-bad-status-door"),
                    principal_id=media_buy.principal_id,
                    advertiser_name="Test Advertiser",
                    budget=Decimal("5000.00"),
                    currency="USD",
                    start_time=datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=1),
                    end_time=datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=8),
                    status=_UNRECOGNISED_STATUS,
                )
            else:
                from tests.factories import MediaBuyFactory

                repo.create(
                    MediaBuyFactory.build(
                        tenant=media_buy.tenant,
                        principal=media_buy.principal,
                        media_buy_id="mb_bad_status_create",
                        status=_UNRECOGNISED_STATUS,
                    )
                )

        assert refusal.value.error_code == "CONFIGURATION_ERROR"
        assert refusal.value.recovery == "terminal"
        assert refusal.value.field == "status"
        assert refusal.value.details.rejected_value == _UNRECOGNISED_STATUS
        assert refusal.value.details.accepted_values == sorted(m.value for m in PersistedMediaBuyStatus)

    def test_a_status_already_in_the_column_is_read_defensively(self, repo_env):
        """The stamp predicate still tolerates an unknown value it merely READS.

        The write boundary stops new unknown values, but the predicate is also
        consulted for whatever a row already holds, and a read must not raise — a
        legacy row with an unexpected status should be reported, not crash the
        reader. It answers "not committed", which is the safe half of fail-closed.
        """

        assert is_media_buy_seller_confirmed(_UNRECOGNISED_STATUS) is False
        assert is_media_buy_seller_confirmed(None) is False

    def test_mixed_case_status_is_normalised_and_still_commits(self, repo_env):
        """A mixed-case status is folded at the write boundary, and the commit still stamps.

        REPOINTED, not deleted. This used to grade case-insensitivity INSIDE the stamp
        decision, because the stamp asked ``is_media_buy_seller_confirmed(status)`` and a
        membership test that dropped the fold turned every mixed-case committed row into a
        silent not-committed. The stamp no longer reads status at all — commitment is
        passed by the writer that knows — so that failure mode cannot occur and a test
        asserting it would grade nothing.

        What still exists, and is what this now pins: the fold lives at the write boundary
        in ``PersistedMediaBuyStatus.parse``, so ``"ACTIVE"`` must persist as ``"active"``
        rather than being refused or stored verbatim. Both halves are asserted, because a
        row stored as ``"ACTIVE"`` would be invisible to every lower-cased query
        downstream even though the stamp landed correctly.
        """
        repo, media_buy = repo_env

        t0 = datetime.datetime.now(datetime.UTC)
        repo.update_status(media_buy.media_buy_id, "ACTIVE", seller_committed=True)
        t1 = datetime.datetime.now(datetime.UTC)

        assert _reread(repo, media_buy.media_buy_id).status == "active", (
            "the write boundary must fold the casing; a row persisted as 'ACTIVE' is "
            "invisible to every lower-cased query downstream"
        )
        _assert_stamped_between(_confirmed_at(repo, media_buy.media_buy_id), t0, t1)

    @pytest.mark.parametrize(
        "kwargs",
        [
            pytest.param({"confirmed_at": datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)}, id="confirmed_at"),
            pytest.param({"revision": 99}, id="revision"),
            pytest.param(
                {"revision": 1, "confirmed_at": datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)}, id="both"
            ),
        ],
    )
    def test_a_row_cannot_be_born_with_a_repository_managed_column_set(self, kwargs):
        """Construction presetting ``confirmed_at``/``revision`` is refused by the model.

        This grades a REMOVAL, not a detection. Before the ``__init__`` override, a row
        could be constructed already committed — never passing
        ``_stamp_confirmation_if_needed`` — and the only thing standing against it was an
        AST fixture that had to recognise every spelling of a constructor call. It did not
        recognise ``MediaBuy(**kwargs)``, which is the spelling the repository itself uses,
        because a double-star call carries one keyword whose ``arg`` is ``None``.

        The point of asserting here rather than in the AST guard is that no spelling is
        enumerated: whatever syntax reaches the constructor, the constructor refuses it.
        """
        with pytest.raises(TypeError, match="repository-managed field"):
            MediaBuy(media_buy_id="mb_seam", tenant_id="t", **kwargs)

    def test_a_clean_construction_is_still_allowed(self):
        """The override must refuse the two columns and nothing else."""
        media_buy = MediaBuy(media_buy_id="mb_clean", tenant_id="t", status="pending_approval")

        assert media_buy.media_buy_id == "mb_clean"
        assert media_buy.confirmed_at is None
        assert media_buy.revision is None

    def test_confirmed_at_is_immutable_to_callers(self, repo_env):
        """Write-once is only a guarantee if the generic field writer refuses it too.

        update_fields would otherwise setattr the caller's value straight over the
        stamp, and the write-once check downstream then no-ops because the field is
        already non-NULL — the guarantee would be bypassable by name.
        """
        repo, media_buy = repo_env
        repo.update_status(media_buy.media_buy_id, "active", seller_committed=True)
        stamped = _confirmed_at(repo, media_buy.media_buy_id)
        # Same guard as the write-once test: a never-persisted stamp would make the
        # post-rejection equality vacuous.
        assert stamped is not None, "the committing transition did not persist a stamp"

        with pytest.raises(ValueError, match="immutable field"):
            repo.update_fields(media_buy.media_buy_id, confirmed_at=_PAST)

        assert _confirmed_at(repo, media_buy.media_buy_id) == stamped


class TestFactorySeedsWhatTheRepositoryWouldWrite:
    """MediaBuyFactory bypasses the repository, so it must reproduce the stamp rule.

    ``MediaBuyFactory`` persists straight to the session
    (``sqlalchemy_session_persistence = "commit"``) — no ``MediaBuyRepository``, so
    none of the write-seam guarantees the rest of this file grades apply to a
    factory-seeded row. A factory that leaves ``confirmed_at`` NULL on a committed
    status therefore seeds a row PRODUCTION CANNOT PRODUCE, and every wire document
    built from it (the whole BDD get_media_buys surface seeds this way) is a
    document the pinned item schema rejects: it forbids ``status: "active"`` with a
    null ``confirmed_at``. Those documents validate today only because a read-time
    fallback fabricates the missing instant — delete the fallback and the fixtures,
    not production, are what goes red.

    This class pins the seam so a future factory edit cannot silently reintroduce
    NULL committed rows.
    """

    # Statuses production can only ever reach through a committing writer, and
    # statuses it never commits. Written out because there is no longer a shared
    # predicate to derive them from — the writer takes an explicit flag — and a
    # derived list would just be this list computed elsewhere.
    _NEVER_COMMITTED = frozenset({"draft", "pending", "pending_approval", "rejected", "failed"})

    @pytest.mark.parametrize("status", sorted(PERSISTED_STATUS_TO_CANONICAL))
    def test_factory_never_seeds_a_row_production_cannot_produce(self, integration_db, bound_factory_session, status):
        """A factory row must be a state some production path can actually reach.

        RE-POINTED. This used to assert the factory stamped exactly when
        ``is_media_buy_seller_confirmed(status)`` said so, and described itself as
        graded "against the writer's OWN predicate". That description stopped being
        true when the writer stopped consulting status: it takes an explicit
        ``seller_committed`` flag now. The assertion also called the same predicate
        the factory calls, so it graded "the factory uses this function" while
        claiming to grade agreement between two seams — a claim about coverage that
        the code no longer supported.

        What is still real, and is what this now pins, are the two directions in which
        a fixture can seed an impossible row:

        * ``active`` with a NULL stamp. The pinned item schema forbids it outright,
          so a fixture like that produces wire documents no buyer could legally
          receive — and they validate today only while a read-time fallback
          fabricates the missing instant.
        * a stamp on a status production NEVER commits. ``draft``/``rejected``/
          ``failed`` and friends have no committing path, so an instant there is
          fabricated by the fixture and nothing else.

        ``pending_creatives`` is deliberately unconstrained here: it is the member
        that names both a committed and an uncommitted state, so BOTH values are
        production-reachable and asserting either would re-introduce the status-keyed
        rule this change removed.
        """
        from tests.factories import MediaBuyFactory

        media_buy = MediaBuyFactory(status=status)
        repo = MediaBuyRepository(bound_factory_session, media_buy.tenant_id)
        stamped = _confirmed_at(repo, media_buy.media_buy_id) is not None

        if status == "active":
            assert stamped, (
                "factory-seeded 'active' carries a NULL confirmed_at, which the pinned item "
                "schema forbids — every wire document built from this fixture is one a buyer "
                "could not legally receive"
            )
        if status in self._NEVER_COMMITTED:
            assert not stamped, (
                f"factory-seeded {status!r} carries a commitment instant, but no production path "
                f"commits a buy in that status — the fixture fabricated it, and any test reading "
                f"it is grading a row production cannot produce"
            )

    def test_factory_stamp_is_a_fresh_clock_reading_not_a_copied_column(self, integration_db, bound_factory_session):
        """The factory reads the clock the way the repository does.

        Deriving the value from ``approved_at``/``created_at`` instead is the
        read-time fabricator's own rule; reproducing it in the factory would import
        that fabrication into every fixture in the suite.
        """
        from tests.factories import MediaBuyFactory

        t0 = datetime.datetime.now(datetime.UTC)
        media_buy = MediaBuyFactory(status="active", created_at=_PAST, approved_at=_PAST)
        t1 = datetime.datetime.now(datetime.UTC)
        repo = MediaBuyRepository(bound_factory_session, media_buy.tenant_id)
        stamped = _confirmed_at(repo, media_buy.media_buy_id)

        _assert_stamped_between(stamped, t0, t1)

    def test_explicit_none_still_wins_over_the_derived_stamp(self, integration_db):
        """The opt-out this file's create-path graders depend on stays available.

        ``_create_prebuilt`` hands an UNSTAMPED row to ``repo.create()`` so that
        ``test_create_in_a_committed_status_stamps_the_create_instant`` grades the
        REPOSITORY's stamp. If an explicit ``confirmed_at=None`` ever stopped
        beating the derived default, that test would silently start grading the
        factory's value instead — passing even with create()'s stamp removed. A
        green-and-vacuous failure mode is invisible, so it is pinned here.
        """
        from tests.factories import MediaBuyFactory

        built = MediaBuyFactory.build(status="active", confirmed_at=None)

        assert built.confirmed_at is None, (
            "an explicit confirmed_at=None no longer overrides the factory's derived stamp — "
            "every test that hands an unstamped committed row to the repository is now vacuous"
        )


# How long writer A holds the row lock so writer B's UPDATE is genuinely
# queued behind it. B needs microseconds to reach the lock.
_LOCK_HOLD_SECONDS = 1.0


@pytest.mark.requires_db
class TestConcurrentBumps:
    """Two OVERLAPPING transactions bumping one row must not lose an update.

    The interleaving is the whole test, and it needs real threads: the second
    UPDATE has to be issued while the first transaction still holds the row lock.
    Sequential commits do not discriminate — the second session simply re-SELECTs
    the already-incremented row, and a read-modify-write produces the right answer
    by accident. That version was written first and passed under mutation.

    Ordering enforced here:
      1. writer A updates and flushes  -> holds the row lock, uncommitted
      2. writer B updates             -> its SELECT sees the OLD committed value,
                                          then its UPDATE blocks on A's lock
      3. A commits                    -> B unblocks and commits

    ``UPDATE ... SET revision = revision + 1`` re-evaluates server-side against the
    row as committed by A, so B lands on top: start + 2. A Python read-modify-write
    writes the absolute value B read in step 2, so A's bump is overwritten and the
    token moves by one for two mutations.

    The ordering above is asserted, not assumed. Nothing in the arithmetic
    distinguishes "B blocked on A's lock" from "B ran after A had already
    committed": in the sequential case a read-modify-write reads the
    already-incremented row and produces start + 2 by accident, so the mutation
    this class exists to kill passes. (Measured: delaying B past
    ``_LOCK_HOLD_SECONDS`` leaves the read-modify-write green.) So writer B times
    its own ``update_fields`` call — which ends in ``flush()``, hence emits the
    UPDATE inside the timed region — and the block is asserted against a floor
    derived from A's hold. A harness that stops overlapping the two transactions
    now fails on the overlap rather than passing on the arithmetic.
    """

    def test_overlapping_transactions_do_not_lose_a_bump(self, integration_db, bound_factory_session):
        import threading
        import time

        from sqlalchemy.orm import Session as SASession

        from src.core.database.database_session import get_engine
        from tests.factories import MediaBuyFactory

        media_buy = MediaBuyFactory(status="pending_approval", created_at=_PAST, approved_at=_PAST)
        bound_factory_session.commit()
        media_buy_id, tenant_id = media_buy.media_buy_id, media_buy.tenant_id
        start = media_buy.revision

        engine = get_engine()
        a_holds_lock = threading.Event()
        errors: list[BaseException] = []
        # Wall time writer B spent inside its own update_fields call. Appended by
        # the thread, read after join — the only cross-thread channel this test
        # needs, and a list append is atomic under the GIL.
        b_blocked_seconds: list[float] = []

        def writer_a() -> None:
            try:
                with SASession(engine) as session:
                    MediaBuyRepository(session, tenant_id).update_fields(media_buy_id, budget=21000)
                    session.flush()
                    a_holds_lock.set()
                    # A HOLD, not a handshake. B cannot signal "I have issued my
                    # UPDATE" because it is blocked inside that UPDATE; an event set
                    # before the call is set too early and releases A first, which
                    # degrades this to the sequential case. (Measured: that version
                    # passed under the read-modify-write mutation.) B needs only
                    # microseconds to reach the lock, so holding it is what makes
                    # the overlap real.
                    time.sleep(_LOCK_HOLD_SECONDS)
                    session.commit()
            except BaseException as exc:  # noqa: BLE001 - surfaced below
                errors.append(exc)
                a_holds_lock.set()

        def writer_b() -> None:
            try:
                with SASession(engine) as session:
                    a_holds_lock.wait(timeout=10)
                    # SELECT sees the old committed value (A is uncommitted); the
                    # UPDATE then blocks on A's row lock until A commits.
                    # update_fields ends in session.flush(), so the UPDATE that
                    # blocks is emitted inside this timed region.
                    started = time.monotonic()
                    MediaBuyRepository(session, tenant_id).update_fields(media_buy_id, budget=22000)
                    b_blocked_seconds.append(time.monotonic() - started)
                    session.commit()
            except BaseException as exc:  # noqa: BLE001 - surfaced below
                errors.append(exc)

        threads = [threading.Thread(target=writer_a), threading.Thread(target=writer_b)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        assert not errors, f"a writer raised: {errors!r}"
        assert not any(thread.is_alive() for thread in threads), "a writer deadlocked"

        # The overlap is graded FIRST. If B never queued behind A, the revision
        # arithmetic below is not evidence about concurrency at all — a
        # read-modify-write reaches start + 2 by re-reading A's committed row —
        # so a green arithmetic assertion on a non-overlapping run is exactly the
        # vacuous pass this assertion exists to convert into a failure.
        assert b_blocked_seconds, "writer B never reached its update_fields call, so nothing was measured"
        blocked = b_blocked_seconds[0]
        # Half of A's hold. A sets the event, then holds for _LOCK_HOLD_SECONDS, so
        # a genuinely queued B blocks for nearly the whole hold; a B that ran after
        # A committed returns in milliseconds. Half leaves room for scheduling
        # jitter without admitting the sequential case.
        overlap_floor = _LOCK_HOLD_SECONDS / 2
        assert blocked >= overlap_floor, (
            f"writer B's update_fields returned in {blocked:.3f}s, under the {overlap_floor:.3f}s floor "
            f"(half of A's {_LOCK_HOLD_SECONDS:.3f}s lock hold): the two transactions DID NOT OVERLAP. "
            f"B's UPDATE was never queued behind A's row lock, so the revision assertion below grades "
            f"two sequential writes and passes under a read-modify-write _bump_revision — the exact "
            f"implementation this class exists to discriminate. Fix the interleaving in the harness; "
            f"do not relax this floor."
        )

        with SASession(engine) as reader:
            final = reader.get(MediaBuy, media_buy_id).revision

        assert final == start + 2, (
            f"two committed mutations moved revision {start} -> {final}. One bump was "
            f"lost: the second writer wrote an absolute value it had read before the "
            f"first committed. revision is the buyer's optimistic-concurrency token, "
            f"so a buyer holding {final} cannot tell which of the two writes it names. "
            f"Check that _bump_revision still assigns MediaBuy.revision + 1 (a SQL "
            f"expression evaluated server-side) rather than reading the Python value."
        )
