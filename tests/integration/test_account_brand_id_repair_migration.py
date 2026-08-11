"""The brand_id repair migration un-mangles poisoned rows, and refuses ambiguity.

Covers alembic revision f4d31a97c058 (salesagent-wkfu).

``BrandId`` is a ``RootModel[str]`` with no ``__str__`` override, so
``str(brand.brand_id)`` yields the repr ``root='brand_one'``. salesagent-myhs
fixed the write path; every row already written before that fix still carries
the repr in ``accounts.brand->>'brand_id'`` — and, because the sync create path
always GENERATES the name, in ``accounts.name`` as well
(``acme.com:root='brand_one' c/o example.com``).

Such a row is not merely wrong, it is unreadable: ``Account.brand`` is
``JSONType(model=BrandReference)``, which validates on READ, and ``BrandId``'s
pattern is ``^[a-z0-9_]+$``. So the load-bearing assertion in this module is
that the repaired row materialises through the ORM at all — a check on
``brand->>'brand_id'`` alone would pass over a partial repair that left the row
just as unloadable.

This module is the migration's ONLY grading. The integration suite builds its
schema with ``Base.metadata.create_all``, so nothing else in the suite executes
a migration's SQL; without a migration test the repair is completely ungraded.

The refusal arms matter as much as the happy one. The Core Invariant is that an
automated repair may rewrite only a provably-mangled representation of a value
the buyer already submitted: it may never choose between two accounts, never
guess at an unrecognised shape, and never silently skip one. Since migrations
run automatically on startup and ``scripts/ops/migrate.py`` exits non-zero on
failure, each refusal is a failed container start — which is the point. A
regression that turned one into a silent skip would leave an ORM-unreadable row
in a database the service reported healthy.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.core.database.models import Account
from src.core.helpers.brand_key import brand_key_parts
from tests.integration.migration_helpers import (
    reset_to_revision,
    run_alembic_downgrade,
    run_alembic_upgrade,
    seed_account,
)

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

_REVISION = "f4d31a97c058"
_PREVIOUS = "b2e94f7c1a03"

_TENANT = "mig_bid_tenant"
_DOMAIN = "acme.com"
_OPERATOR = "example.com"

#: The exact literal the defect wrote: ``str(BrandId(root="brand_one"))``.
_POISONED_ID = "root='brand_one'"
_REPAIRED_ID = "brand_one"

#: ``_generate_account_name(domain, operator, brand_id)`` fed with the mangled
#: value, and the same name once the fragment is un-mangled.
_POISONED_NAME = f"{_DOMAIN}:{_POISONED_ID} c/o {_OPERATOR}"
_REPAIRED_NAME = f"{_DOMAIN}:{_REPAIRED_ID} c/o {_OPERATOR}"


@pytest.fixture
def at_previous(migration_db):
    """Put the module-scoped database at the revision BEFORE this one, with no rows.

    The refusal tests here leave poisoned rows behind that would abort any later
    upgrade, so every test starts from a clean tenant — see ``reset_to_revision``.
    """
    return reset_to_revision(
        migration_db,
        revision=_PREVIOUS,
        tenant_id=_TENANT,
        tenant_name="Migration BrandId",
        subdomain="mig-bid",
    )


def _seed(engine, *, account_id: str, brand_id: str, name: str, operator: str | None = _OPERATOR) -> None:
    seed_account(
        engine,
        tenant_id=_TENANT,
        account_id=account_id,
        domain=_DOMAIN,
        brand_id=brand_id,
        name=name,
        operator=operator,
    )


def _stored(engine, account_id: str):
    """Read the row's poisonable columns as raw text, bypassing the ORM entirely."""
    with engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT brand ->> 'brand_id' AS brand_id, name "
                "  FROM accounts WHERE tenant_id = :tid AND account_id = :aid"
            ),
            {"tid": _TENANT, "aid": account_id},
        ).one()


def _load_through_orm(engine, account_id: str) -> Account:
    """Load the row the way the application does.

    This is the assertion the whole migration exists for: ``JSONType`` validates
    on read, so a row still carrying ``root='...'`` raises ``ValidationError``
    here and cannot be materialised at all.
    """
    with Session(engine) as session:
        account = session.get(Account, {"tenant_id": _TENANT, "account_id": account_id})
        assert account is not None, f"{account_id} is missing from the database"
        # Touch the validated column inside the session so the read (and its
        # validation) actually happens rather than being deferred.
        assert account.brand is not None
        return account


def _xmins(engine) -> dict[str, str]:
    """Per-row ``xmin`` — the transaction that last wrote the row.

    PostgreSQL bumps it on ANY update, including a rewrite of a row to itself,
    which is what makes this the only assertion that can tell "issued no
    statement" apart from "churned every row back to the same value".
    """
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT account_id, xmin::text AS xmin FROM accounts WHERE tenant_id = :tid"),
            {"tid": _TENANT},
        ).all()
    return {row.account_id: row.xmin for row in rows}


class TestTheMigrationRepairsPoisonedRows:
    def test_a_poisoned_row_is_un_mangled_in_both_columns(self, at_previous):
        """The brand key AND the generated name, which is what reached the buyer.

        The name is asserted as an exact string rather than "no longer contains
        ``root=``": the weaker form is satisfied by a repair that blanked the
        column.
        """
        engine, db_url = at_previous
        _seed(engine, account_id="acc_poisoned", brand_id=_POISONED_ID, name=_POISONED_NAME)

        run_alembic_upgrade(db_url, _REVISION)

        row = _stored(engine, "acc_poisoned")
        assert row.brand_id == _REPAIRED_ID, f"brand.brand_id was not repaired: {row.brand_id!r}"
        assert row.name == _REPAIRED_NAME, f"the generated name still carries the repr: {row.name!r}"

    def test_the_repaired_row_loads_through_the_orm(self, at_previous):
        """The defect itself: before the repair this row cannot be read at all."""
        engine, db_url = at_previous
        _seed(engine, account_id="acc_poisoned", brand_id=_POISONED_ID, name=_POISONED_NAME)

        run_alembic_upgrade(db_url, _REVISION)

        account = _load_through_orm(engine, "acc_poisoned")
        domain, brand_id = brand_key_parts(account.brand)
        assert (domain, brand_id) == (_DOMAIN, _REPAIRED_ID), (
            f"the row loaded but its natural key is still wrong: {(domain, brand_id)!r}"
        )

    def test_a_poisoned_row_renamed_by_an_operator_is_still_repaired(self, at_previous):
        """The boundary the name restriction must live in the SET, not the WHERE.

        ``name`` is mutable — the admin ``edit_account`` form writes it — so a
        poisoned row may carry an operator-chosen name that contains no
        ``root=`` fragment. If the "name still contains the fragment"
        restriction were expressed as a WHERE predicate, this row would drop out
        of the UPDATE entirely, its ``brand`` would never be repaired, and it
        would stay ORM-unreadable — the PRIMARY defect the migration exists to
        fix. Restricting inside the SET (``replace()`` is its own no-op) keeps
        the row in scope and leaves the chosen name alone.
        """
        chosen_name = "Acme Programmatic (renamed by ops)"
        engine, db_url = at_previous
        _seed(engine, account_id="acc_renamed", brand_id=_POISONED_ID, name=chosen_name)

        run_alembic_upgrade(db_url, _REVISION)

        row = _stored(engine, "acc_renamed")
        assert row.brand_id == _REPAIRED_ID, (
            f"a renamed poisoned row was skipped and stays ORM-unreadable: {row.brand_id!r}"
        )
        assert row.name == chosen_name, f"the operator's chosen name was rewritten: {row.name!r}"
        _load_through_orm(engine, "acc_renamed")


class TestTheMigrationRefusesAmbiguity:
    def test_a_repaired_key_that_collides_aborts_the_upgrade(self, at_previous):
        """Repairing changes the natural key, and the correct key may be taken.

        The admin path always wrote a plain string, so a correctly-keyed twin can
        legitimately exist alongside the poisoned row. Repairing would move the
        poisoned row onto the twin's key, which ``uq_accounts_natural_key``
        already forbids — so the migration must survey and abort with the rows
        NAMED rather than raise a bare IntegrityError mid-UPDATE.

        It must not choose a survivor: ``brand``/``operator``/``sandbox`` are all
        immutable on ``AccountRepository``, so the losing row cannot be re-keyed,
        and ``closed`` is terminal in the status transitions. The only remedy is
        direct SQL against the poisoned row, which is why the abort must say
        which of the two IS the poisoned one.
        """
        engine, db_url = at_previous
        _seed(engine, account_id="acc_bid_poisoned", brand_id=_POISONED_ID, name=_POISONED_NAME)
        _seed(engine, account_id="acc_bid_occupant", brand_id=_REPAIRED_ID, name=_REPAIRED_NAME)

        with pytest.raises(RuntimeError) as excinfo:
            run_alembic_upgrade(db_url, _REVISION)

        message = str(excinfo.value)
        assert "poisoned=acc_bid_poisoned" in message, (
            f"the abort must label WHICH row is poisoned — it is the only one an operator "
            f"may touch with direct SQL: {message}"
        )
        assert "occupant=acc_bid_occupant" in message, (
            f"the abort must name the row already holding the repaired key: {message}"
        )
        # Two obligations, not one. b2e94f7c1a03 tells the operator to "close or re-key
        # the extras"; that INSTRUCTION must not be inherited, because re-keying is
        # impossible here. But the abort must also SAY so — an operator who is not told
        # will try it and fail, since brand/operator/sandbox are immutable. A bare
        # `"re-key" not in message` cannot express both: it forbids the word, and so
        # forbids the explanation the message is required to carry.
        assert "close or re-key" not in message, (
            f"the sibling migration's remedy must not be inherited verbatim — re-keying is "
            f"impossible (brand/operator/sandbox are immutable on AccountRepository): {message}"
        )
        assert "cannot be re-keyed" in message, (
            f"the abort must state that re-keying is impossible, or the operator will attempt "
            f"the remedy the sibling migration taught them: {message}"
        )

        poisoned = _stored(engine, "acc_bid_poisoned")
        occupant = _stored(engine, "acc_bid_occupant")
        assert (poisoned.brand_id, poisoned.name) == (_POISONED_ID, _POISONED_NAME), (
            "an aborted upgrade must leave the poisoned row exactly as it found it"
        )
        assert (occupant.brand_id, occupant.name) == (_REPAIRED_ID, _REPAIRED_NAME), (
            "an aborted upgrade must not touch the correctly-keyed account"
        )

    def test_an_unrecognised_root_shape_aborts_the_upgrade(self, at_previous):
        """An unparseable variant is named, never skipped.

        ``^root='([a-z0-9_]+)'$`` is a TOTAL extractor for the values the defect
        could produce, so anything matching ``LIKE 'root=%'`` but not that regex
        is a shape this migration does not understand. Guessing at it, or
        stepping over it, would violate the Core Invariant and leave an
        ORM-unreadable row behind a green migration.
        """
        unknown = "root='Brand One'"
        engine, db_url = at_previous
        _seed(engine, account_id="acc_bid_unknown", brand_id=unknown, name=_POISONED_NAME)

        with pytest.raises(RuntimeError) as excinfo:
            run_alembic_upgrade(db_url, _REVISION)

        message = str(excinfo.value)
        assert "acc_bid_unknown" in message, f"the abort must name the row it cannot repair: {message}"
        assert unknown in message, f"the abort must quote the value it does not recognise: {message}"

        row = _stored(engine, "acc_bid_unknown")
        assert (row.brand_id, row.name) == (unknown, _POISONED_NAME), (
            "the migration must rewrite nothing once it has decided to abort"
        )


class TestACleanDatabaseIsUntouched:
    def test_the_upgrade_issues_no_update_when_nothing_is_poisoned(self, at_previous):
        """Zero poisoned rows is the common case and must be a true no-op.

        Asserting the rows merely READ the same afterwards is not enough: a
        repair expressed without a survey would rewrite every account to itself
        and pass that check while churning the whole table on every deploy. Each
        row's ``xmin`` is the system column that tells the two apart.
        """
        engine, db_url = at_previous
        _seed(engine, account_id="acc_clean_keyed", brand_id=_REPAIRED_ID, name=_REPAIRED_NAME)
        _seed(engine, account_id="acc_clean_other", brand_id="brand_two", name=f"{_DOMAIN}:brand_two c/o other.com")
        seed_account(
            engine,
            tenant_id=_TENANT,
            account_id="acc_clean_keyless",
            domain=None,
            operator=None,
            name="Keyless account",
        )
        before = _xmins(engine)

        run_alembic_upgrade(db_url, _REVISION)

        assert _xmins(engine) == before, (
            "the migration rewrote rows on a database with nothing to repair — the survey must "
            "return early and issue no statement that touches a row"
        )

    def test_the_downgrade_leaves_repaired_rows_repaired(self, at_previous):
        """Deliberately asymmetric, and the chain must stay traversable.

        Re-mangling is not a legal inverse: a repaired row is indistinguishable
        from one that was always correct, so a reversing downgrade would poison
        rows the defect never touched. The downgrade must also not RAISE —
        raising would block rolling back past this revision at all, including
        the natural-key index below it.
        """
        engine, db_url = at_previous
        _seed(engine, account_id="acc_poisoned", brand_id=_POISONED_ID, name=_POISONED_NAME)
        run_alembic_upgrade(db_url, _REVISION)

        run_alembic_downgrade(db_url, _PREVIOUS)

        row = _stored(engine, "acc_poisoned")
        assert (row.brand_id, row.name) == (_REPAIRED_ID, _REPAIRED_NAME), (
            f"the downgrade re-poisoned a repaired row: {row!r}"
        )
