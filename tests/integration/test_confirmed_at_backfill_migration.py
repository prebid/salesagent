"""Integration test for migration 7f3a1c9e2b04 — backfill media_buys.confirmed_at.

`2c4e6a7b8d9e` added `confirmed_at` nullable and left historical rows NULL. The
pinned get-media-buys-response schema forbids a seller-confirmed item from
carrying a null `confirmed_at`, so those rows are a DATA defect, and the ruling
(plan §6 ALTERATIONS A1) is that data defects are corrected by migration — never
by a read-time compatibility branch.

This grades the correction itself:

  * a seller-confirmed row with a NULL stamp and an approval instant takes
    `approved_at` (the manual-approval path),
  * one with no approval instant falls back to `created_at` (the synchronous
    auto-approve path),
  * a row in an UNCONFIRMED status keeps NULL — it has no commitment instant, so
    NULL is its correct value and not missing data, even when it happens to have
    an `approved_at` the naive UPDATE would have copied,
  * a row that already carries a stamp is untouched (write-once holds),
  * ZERO rows remain in the defective state afterwards — asserted directly,
    because the bar is zero and not "the rows I happened to name",
  * downgrade re-NULLs only what the backfill would have written, leaving a
    genuinely-observed stamp alone.

Every case is gated on non-vacuity: the seeded rows are asserted to be IN the
defective state before the migration runs, otherwise each post-condition is
trivially true.

Setup is raw SQL rather than factories, following the precedent of the other
migration tests in this directory (`test_timestamptz_migration.py`,
`test_composite_pk_orphan_migration.py`, `test_delivery_measurement_migration.py`):
a migration test legitimately operates below the ORM, and the ORM model describes
the schema at HEAD rather than at the revision under test.
"""

from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from sqlalchemy import text

from tests.integration.migration_helpers import run_alembic_downgrade, run_alembic_upgrade, seed_media_buy

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

# The migration under test, and the revision immediately before it (which adds
# the nullable column but deliberately leaves historical rows NULL).
BACKFILL_REV = "7f3a1c9e2b04"
PRE_BACKFILL_REV = "2c4e6a7b8d9e"

# Restated here on purpose rather than imported from the migration or from
# models._SELLER_COMMITTED_STATUSES: a grader that reads its expectation out of
# the thing it grades cannot fail. This is the partition the migration is
# required to honour.
#
# The COMMITTED side is restated, not its complement. Selecting the defective rows
# as "NOT IN (<unconfirmed>)" is the same inversion the migration itself carried:
# it makes committed the DEFAULT for any status in NEITHER list, so a legacy value
# the vocabulary never had would be counted as a row the backfill OWES a stamp —
# and the oracle would then demand exactly the wrong outcome.
COMMITTED_STATUSES = (
    "active",
    "approved",
    "ready",
    "scheduled",
    "pending_activation",
    "pending_creatives",
    "pending_start",
    "paused",
    "completed",
    "canceled",
)

TENANT_ID = "t_backfill"
PRINCIPAL_ID = "p_backfill"

# Three distinct instants so every equality assertion below is unambiguous about
# WHICH value was written.
T_CREATED = datetime(2026, 1, 5, 9, 0, 0, tzinfo=UTC)
T_APPROVED = datetime(2026, 1, 6, 15, 30, 0, tzinfo=UTC)
T_STAMPED = datetime(2026, 1, 7, 11, 45, 0, tzinfo=UTC)

# (media_buy_id, status, approved_at, confirmed_at)  — created_at is T_CREATED for all.
SEED_ROWS = [
    # Defective: seller-confirmed, no stamp, has an approval instant.
    ("mb_active_approved", "active", T_APPROVED, None),
    # Defective: seller-confirmed, no stamp, never approved (auto-approve path).
    ("mb_completed_no_approval", "completed", None, None),
    # Correctly NULL: unconfirmed, nothing to record.
    ("mb_pending_approval", "pending_approval", None, None),
    # Correctly NULL and the sharp case: unconfirmed but carrying an approved_at,
    # which a naive "UPDATE ... WHERE confirmed_at IS NULL" would have copied.
    ("mb_failed_after_approval", "failed", T_APPROVED, None),
    # Correctly NULL: the partition is case-insensitive (migration lowers status).
    ("mb_pending_upper", "PENDING_APPROVAL", None, None),
    # Already stamped, and stamped at an instant equal to neither approved_at nor
    # created_at, so "untouched" is distinguishable from "rewritten".
    ("mb_already_stamped", "active", T_APPROVED, T_STAMPED),
    # THE POLARITY ROW: a legacy status in NEITHER partition, carrying an
    # approved_at so the naive predicate would have something to copy. Under the
    # inverted "NOT IN (<unconfirmed>)" predicate this row was backfilled AS
    # COMMITTED — a seller-commitment instant minted for a state nobody defined.
    ("mb_legacy_unknown_status", "legacy_state", T_APPROVED, None),
]

# The rows the backfill is required to correct, and what it must write.
EXPECTED_BACKFILL = {
    "mb_active_approved": T_APPROVED,
    "mb_completed_no_approval": T_CREATED,
}
# The rows it must leave NULL.
EXPECTED_STILL_NULL = [
    "mb_pending_approval",
    "mb_failed_after_approval",
    "mb_pending_upper",
    "mb_legacy_unknown_status",
]


def _table_exists(engine, table_name: str) -> bool:
    with engine.connect() as conn:
        return bool(conn.execute(text("SELECT to_regclass(:t)"), {"t": table_name}).scalar())


def _seed(engine, rows=SEED_ROWS) -> None:
    """Wipe and re-seed the tenant/principal/media_buys rows under test.

    ``rows`` is a parameter because the row set is not one fixture. The seed
    always carried ``mb_legacy_unknown_status``, which is exactly the row
    ``9b2d4f6c1a37`` aborts on — so with a fixed seed the cases that must reach
    that migration's NORMALISATION branch could never get past its ABORT branch,
    and only one of the two decisions was expressible.
    """
    with engine.connect() as conn:
        conn.execute(text("TRUNCATE TABLE media_buys, principals, tenants CASCADE"))
        conn.execute(
            text(
                "INSERT INTO tenants (tenant_id, name, subdomain, created_at, updated_at) "
                "VALUES (:tid, 'Backfill Tenant', 'backfill-test', NOW(), NOW())"
            ),
            {"tid": TENANT_ID},
        )
        conn.execute(
            text(
                "INSERT INTO principals (tenant_id, principal_id, name, platform_mappings, "
                "access_token, created_at, updated_at) "
                "VALUES (:tid, :pid, 'Backfill Principal', '{}', 'tok_backfill', NOW(), NOW())"
            ),
            {"tid": TENANT_ID, "pid": PRINCIPAL_ID},
        )
        for media_buy_id, status, approved_at, confirmed_at in rows:
            seed_media_buy(
                conn,
                tenant_id=TENANT_ID,
                principal_id=PRINCIPAL_ID,
                media_buy_id=media_buy_id,
                advertiser_name="Advertiser",
                start_date="2026-01-10",
                end_date="2026-01-20",
                status=status,
                created_at=T_CREATED,
                updated_at=T_CREATED,
                approved_at=approved_at,
                confirmed_at=confirmed_at,
            )
        conn.commit()


def _confirmed_at(engine, media_buy_id: str):
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT confirmed_at FROM media_buys WHERE media_buy_id = :mbid"),
            {"mbid": media_buy_id},
        ).fetchone()
    assert row is not None, f"{media_buy_id} disappeared from media_buys"
    return row[0]


def _status(engine, media_buy_id: str) -> str:
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT status FROM media_buys WHERE media_buy_id = :mbid"),
            {"mbid": media_buy_id},
        ).fetchone()
    assert row is not None, f"{media_buy_id} disappeared from media_buys"
    return row[0]


def _defective_ids(engine) -> set[str]:
    """Rows in the defective state: seller-confirmed AND confirmed_at IS NULL."""
    stmt = sa.text(
        "SELECT media_buy_id FROM media_buys WHERE confirmed_at IS NULL AND lower(status) IN :committed"
    ).bindparams(sa.bindparam("committed", value=COMMITTED_STATUSES, expanding=True))
    with engine.connect() as conn:
        return {r[0] for r in conn.execute(stmt).fetchall()}


def _prepare_pre_backfill(engine, db_url, rows=SEED_ROWS) -> None:
    """Put the DB at the revision before the backfill, seeded with defective rows.

    Self-contained per test: `migration_db` is module-scoped, so tests must not
    assume which revision a sibling left behind (see the xdist note in
    test_timestamptz_migration.py).
    """
    if _table_exists(engine, "media_buys"):
        run_alembic_downgrade(db_url, PRE_BACKFILL_REV)
    else:
        run_alembic_upgrade(db_url, PRE_BACKFILL_REV)
    _seed(engine, rows)


def _assert_defective_before_upgrade(engine) -> None:
    """Non-vacuity gate — without this every post-condition below is trivially true."""
    assert _defective_ids(engine) == set(EXPECTED_BACKFILL), (
        "seeded rows are not in the defective state before the migration runs; the post-conditions would pass vacuously"
    )
    for media_buy_id in EXPECTED_STILL_NULL:
        assert _confirmed_at(engine, media_buy_id) is None
    assert _confirmed_at(engine, "mb_already_stamped") == T_STAMPED


def _prepare_and_upgrade(engine, db_url) -> None:
    _prepare_pre_backfill(engine, db_url)
    _assert_defective_before_upgrade(engine)
    run_alembic_upgrade(db_url, BACKFILL_REV)


class TestConfirmedAtBackfillUpgrade:
    """Migration 7f3a1c9e2b04 must correct the legacy NULLs and only those."""

    def test_seeded_rows_are_defective_before_upgrade(self, migration_db):
        """The gate itself: the fixture really does produce the defect being fixed."""
        engine, db_url = migration_db
        _prepare_pre_backfill(engine, db_url)
        _assert_defective_before_upgrade(engine)

    def test_backfills_approved_at_when_manually_approved(self, migration_db):
        """A seller-confirmed row with an approval instant takes approved_at."""
        engine, db_url = migration_db
        _prepare_and_upgrade(engine, db_url)
        assert _confirmed_at(engine, "mb_active_approved") == T_APPROVED

    def test_falls_back_to_created_at_when_never_approved(self, migration_db):
        """A seller-confirmed row with no approval instant takes created_at."""
        engine, db_url = migration_db
        _prepare_and_upgrade(engine, db_url)
        assert _confirmed_at(engine, "mb_completed_no_approval") == T_CREATED

    @pytest.mark.parametrize("media_buy_id", EXPECTED_STILL_NULL)
    def test_unconfirmed_rows_keep_null(self, migration_db, media_buy_id):
        """An unconfirmed row has no commitment instant — NULL is its correct value.

        Includes a row that carries an `approved_at` (so COALESCE would have had
        something to write) and a row whose status differs only in case.
        """
        engine, db_url = migration_db
        _prepare_and_upgrade(engine, db_url)
        assert _confirmed_at(engine, media_buy_id) is None

    def test_existing_confirmed_at_is_untouched(self, migration_db):
        """Write-once is not violated: an already-stamped row keeps its own instant."""
        engine, db_url = migration_db
        _prepare_and_upgrade(engine, db_url)
        assert _confirmed_at(engine, "mb_already_stamped") == T_STAMPED

    def test_a_status_in_neither_partition_is_not_backfilled_as_committed(self, migration_db):
        """The polarity oracle: an unknown legacy status gets no commitment instant.

        This is the case that separates the two predicates. For every status the
        vocabulary DOES define, "IN (committed)" and "NOT IN (unconfirmed)" select
        identically — which is why the inversion survived review. They diverge only
        for a value in NEITHER list, and that is exactly the population a backfill
        exists to meet: rows written before the vocabulary was closed.

        Under the complement predicate such a row was stamped with
        COALESCE(approved_at, created_at) — a seller-commitment instant for a state
        nobody defined, published to the buyer as fact. `models.py` argues the same
        fail-closed rule for the in-memory partition ("reading an unknown state as
        committed would mint a seller-commitment instant that reaches the buyer's
        wire"); this asserts the migration obeys it too.

        The row carries an `approved_at` deliberately: without one the naive
        predicate would find nothing to copy and the test would pass under both
        polarities.
        """
        engine, db_url = migration_db
        _prepare_and_upgrade(engine, db_url)

        assert _confirmed_at(engine, "mb_legacy_unknown_status") is None, (
            "a status in neither partition was backfilled as seller-committed; the predicate "
            "is selecting by the complement of the unconfirmed list instead of the committed list"
        )

    def test_zero_defective_rows_remain(self, migration_db):
        """The bar is zero seller-confirmed rows with a NULL confirmed_at."""
        engine, db_url = migration_db
        _prepare_and_upgrade(engine, db_url)
        assert _defective_ids(engine) == set()


class TestConfirmedAtBackfillDowngrade:
    """Downgrade must re-NULL what the backfill wrote and nothing else."""

    def test_downgrade_renulls_backfilled_rows_only(self, migration_db):
        engine, db_url = migration_db
        _prepare_and_upgrade(engine, db_url)

        run_alembic_downgrade(db_url, PRE_BACKFILL_REV)

        # The two rows this migration stamped are back to NULL...
        for media_buy_id in EXPECTED_BACKFILL:
            assert _confirmed_at(engine, media_buy_id) is None
        # ...the genuinely-observed stamp (≠ COALESCE(approved_at, created_at))
        # survives, which is exactly what the downgrade docstring promises...
        assert _confirmed_at(engine, "mb_already_stamped") == T_STAMPED
        # ...and rows the upgrade never touched are still NULL.
        for media_buy_id in EXPECTED_STILL_NULL:
            assert _confirmed_at(engine, media_buy_id) is None

    def test_roundtrip_restores_the_backfill(self, migration_db):
        """down → up leaves the same corrected state, so the pair is replayable."""
        engine, db_url = migration_db
        _prepare_and_upgrade(engine, db_url)

        run_alembic_downgrade(db_url, PRE_BACKFILL_REV)
        assert _defective_ids(engine) == set(EXPECTED_BACKFILL), "downgrade should restore the defect"

        run_alembic_upgrade(db_url, BACKFILL_REV)
        assert _defective_ids(engine) == set()
        for media_buy_id, expected in EXPECTED_BACKFILL.items():
            assert _confirmed_at(engine, media_buy_id) == expected
        assert _confirmed_at(engine, "mb_already_stamped") == T_STAMPED


# ---------------------------------------------------------------------------
# 9b2d4f6c1a37 — normalize legacy media_buys.status
# ---------------------------------------------------------------------------
# The revision after the backfill, and the only one of the four in this chain
# that makes DECISIONS rather than moving schema: it lowercases legacy casing and
# it ABORTS on a status the vocabulary never defined. Both were reachable from no
# test — scripts/ci/migration_roundtrip.py seeds nothing, so both ran against zero
# rows, and this file stopped one revision short. Measured: deleting BOTH branches
# left all 30 migration tests in tests/integration/ green.
NORMALIZE_REV = "9b2d4f6c1a37"

# The out-of-vocabulary row is the ABORT trigger, so it cannot be present when the
# NORMALISATION branch is under test — the migration would never reach the second
# statement. This is why _seed() takes its rows as a parameter.
IN_VOCABULARY_SEED_ROWS = [row for row in SEED_ROWS if row[0] != "mb_legacy_unknown_status"]

# The seeded row whose status differs from its own lowercase form, and the value
# the normalisation must leave behind. Restated rather than computed from the row
# tuple: an oracle that derives its expectation by calling lower() is grading
# Python's str.lower, not the migration.
UPPERCASE_ROW_ID = "mb_pending_upper"
UPPERCASE_SEEDED_STATUS = "PENDING_APPROVAL"
UPPERCASE_NORMALIZED_STATUS = "pending_approval"

# The row whose status is in neither the vocabulary nor any casing of it.
UNMAPPED_ROW_ID = "mb_legacy_unknown_status"
UNMAPPED_STATUS = "legacy_state"


def _alembic_version(engine) -> str | None:
    with engine.connect() as conn:
        return conn.execute(text("SELECT version_num FROM alembic_version")).scalar()


class TestNormalizeLegacyStatusUpgrade:
    """Migration 9b2d4f6c1a37 lowercases legacy casing and refuses unknown values.

    Every test here upgrades from PRE_BACKFILL_REV to NORMALIZE_REV in ONE
    ``alembic upgrade`` call rather than two. That is not a shortcut: alembic/env.py
    wraps ``context.run_migrations()`` in a single ``context.begin_transaction()``,
    so the whole chain shares one transaction. Upgrading in two calls would commit
    7f3a1c9e2b04's backfill first and make the abort's blast radius unobservable.
    """

    def test_legacy_casing_is_normalized(self, migration_db):
        """A row spelled in the wrong case ends up spelled in the right one.

        Deleting the ``UPDATE ... SET status = lower(status)`` statement does not
        merely leave this row as it was: 'PENDING_APPROVAL' is then outside the
        (all-lowercase) vocabulary, so the very next statement aborts the whole
        migration. Either way this test reddens.
        """
        engine, db_url = migration_db
        _prepare_pre_backfill(engine, db_url, IN_VOCABULARY_SEED_ROWS)

        assert _status(engine, UPPERCASE_ROW_ID) == UPPERCASE_SEEDED_STATUS, (
            "the seeded row is not in the mis-cased state, so the post-condition below would pass vacuously"
        )

        run_alembic_upgrade(db_url, NORMALIZE_REV)

        assert _status(engine, UPPERCASE_ROW_ID) == UPPERCASE_NORMALIZED_STATUS, (
            f"{UPPERCASE_ROW_ID} still holds a status outside the closed vocabulary's spelling. "
            f"9b2d4f6c1a37 exists to correct legacy casing in place — the read path refuses a "
            f"value it cannot parse, so a row left mis-cased fails a buyer's request instead."
        )

    def test_an_unmapped_status_aborts_the_migration(self, migration_db):
        """A status nobody defined stops the upgrade and names itself.

        Naming the value and a sample id is the whole point of aborting rather than
        guessing: the migration cannot decide whether an unknown state is serving or
        retired, so it hands an operator the two facts needed to decide.
        """
        engine, db_url = migration_db
        _prepare_pre_backfill(engine, db_url, SEED_ROWS)

        assert _status(engine, UNMAPPED_ROW_ID) == UNMAPPED_STATUS, (
            "the seeded row does not carry the out-of-vocabulary status, so the abort below would not be triggered by it"
        )

        with pytest.raises(RuntimeError) as excinfo:
            run_alembic_upgrade(db_url, NORMALIZE_REV)

        message = str(excinfo.value)
        assert UNMAPPED_STATUS in message, f"the abort does not name the offending value {UNMAPPED_STATUS!r}: {message}"
        assert UNMAPPED_ROW_ID in message, (
            f"the abort does not name a sample media_buy_id for the operator to inspect: {message}"
        )

    def test_the_abort_leaves_the_data_untouched(self, migration_db):
        """The aborted upgrade rolls back — including the PREVIOUS revision's writes.

        This is the case that separates "raises" from "raises safely". env.py runs
        the whole chain in one transaction, so 7f3a1c9e2b04's backfill is rolled back
        with 9b2d4f6c1a37's own UPDATE: after the abort the database is byte-for-byte
        the pre-backfill state, and alembic_version still names PRE_BACKFILL_REV.

        An operator re-running after correcting the offending rows therefore gets the
        backfill applied once, on the successful run — not a half-migrated database
        stamped at a revision whose decisions never completed.
        """
        engine, db_url = migration_db
        _prepare_pre_backfill(engine, db_url, SEED_ROWS)
        _assert_defective_before_upgrade(engine)

        with pytest.raises(RuntimeError):
            run_alembic_upgrade(db_url, NORMALIZE_REV)

        # 9b2d4f6c1a37's own normalisation is rolled back...
        assert _status(engine, UPPERCASE_ROW_ID) == UPPERCASE_SEEDED_STATUS, (
            "the aborted migration left its own lowercase UPDATE committed; the abort must change no data"
        )
        # ...and so is 7f3a1c9e2b04's backfill, which ran earlier in the SAME
        # transaction. Asserting the defective set is exactly what it was before is
        # the strong form: "nothing was written", not "the rows I named survived".
        assert _defective_ids(engine) == set(EXPECTED_BACKFILL), (
            "rows the previous revision backfilled stayed backfilled after the abort. env.py wraps the "
            "whole chain in one transaction, so an abort in 9b2d4f6c1a37 must roll 7f3a1c9e2b04 back too — "
            "otherwise the database is half-migrated at a revision that never completed"
        )
        assert _confirmed_at(engine, "mb_already_stamped") == T_STAMPED
        # ...and the schema version was never advanced.
        assert _alembic_version(engine) == PRE_BACKFILL_REV, (
            f"alembic_version advanced past {PRE_BACKFILL_REV} despite the abort; a re-run would skip "
            f"the revisions whose work was rolled back"
        )
