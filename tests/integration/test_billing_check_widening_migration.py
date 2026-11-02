"""The billing CHECK widening admits the third party, and clears it on the way back down.

Covers alembic revision e381618812f1 (#1521).

Nothing else runs this migration's SQL: the integration suite builds its schema
with ``Base.metadata.create_all``, which reads the CHECK off the ORM model
(derived from the SDK enum) and so can never observe the constraint the
migration replaced. Only a real upgrade over a real database shows that
``billing='advertiser'`` was rejected before the widening and admitted after.

The downgrade clears ``billing='advertiser'`` and narrows the constraint. That
is lossy on purpose: the narrow domain has no value for the third party, and a
later upgrade has nothing to reconstruct it from, so the value cannot survive a
round trip. This is what the rest of the downgrades in this tree do.

What IS worth pinning is the blast radius -- a downgrade that also cleared the
parties the narrow domain CAN hold would be a real defect, and that is the
assertion these tests carry.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from tests.integration.migration_helpers import (
    reset_to_revision,
    run_alembic_downgrade,
    run_alembic_upgrade,
    seed_account,
)

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

_REVISION = "e381618812f1"
_PREVIOUS = "823974a5553e"
_CONSTRAINT = "ck_accounts_billing"

_TENANT = "mig_bill_tenant"


@pytest.fixture
def at_previous(migration_db):
    """Put the module-scoped database at the revision BEFORE the widening.

    Each test seeds its own accounts and the downgrade tests mutate them, so
    every test starts from a clean tenant.
    """
    return reset_to_revision(
        migration_db,
        revision=_PREVIOUS,
        tenant_id=_TENANT,
        tenant_name="Migration Billing",
        subdomain="mig-bill",
    )


def _seed(engine, *, account_id: str, billing: str | None) -> None:
    """Seed into this module's tenant — the shared helper carries the SQL.

    Raw SQL on purpose: these tests must be able to attempt a value the CHECK
    rejects and watch the DATABASE refuse it, which the ORM would pre-empt.
    """
    seed_account(
        engine,
        tenant_id=_TENANT,
        account_id=account_id,
        domain=None,
        operator="example.com",
        billing=billing,
    )


def _constraint_def(engine) -> str | None:
    with engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = :name AND conrelid = 'accounts'::regclass"
            ),
            {"name": _CONSTRAINT},
        ).scalar()


def _billing_of(engine, account_id: str) -> str | None:
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT billing FROM accounts WHERE tenant_id = :tid AND account_id = :aid"),
            {"tid": _TENANT, "aid": account_id},
        ).scalar()


class TestTheDefectTheWideningFixes:
    def test_advertiser_is_rejected_before_the_upgrade(self, at_previous):
        """#1521: a spec-valid billing party could not be stored at all."""
        engine, _ = at_previous

        with pytest.raises(IntegrityError) as excinfo:
            _seed(engine, account_id="acc_pre_advertiser", billing="advertiser")
        assert _CONSTRAINT in str(excinfo.value), f"a different constraint refused the row: {excinfo.value}"


class TestTheUpgradeAdmitsTheFullEnum:
    def test_every_spec_billing_party_is_admitted(self, at_previous):
        engine, db_url = at_previous
        run_alembic_upgrade(db_url, _REVISION)

        for party in ("operator", "agent", "advertiser"):
            _seed(engine, account_id=f"acc_{party}", billing=party)

        for party in ("operator", "agent", "advertiser"):
            assert _billing_of(engine, f"acc_{party}") == party

    def test_a_value_outside_the_enum_is_still_rejected(self, at_previous):
        """Widened, not dropped: the constraint still bounds the column."""
        engine, db_url = at_previous
        run_alembic_upgrade(db_url, _REVISION)

        with pytest.raises(IntegrityError) as excinfo:
            _seed(engine, account_id="acc_bogus", billing="publisher")
        assert _CONSTRAINT in str(excinfo.value), f"a different constraint refused the row: {excinfo.value}"

    def test_an_undeclared_billing_party_stays_admitted(self, at_previous):
        """``billing`` is optional in the spec — NULL must survive the widening."""
        engine, db_url = at_previous
        run_alembic_upgrade(db_url, _REVISION)

        _seed(engine, account_id="acc_undeclared", billing=None)
        assert _billing_of(engine, "acc_undeclared") is None


class TestTheDowngradeRefusesRatherThanDestroys:
    def test_it_clears_the_third_party_it_cannot_represent(self, at_previous):
        """Downgrading past a widened domain is lossy, and that is the contract.

        The narrow constraint has no value for ``advertiser``, and a later
        upgrade has nothing to reconstruct it from, so the downgrade clears
        those rows and narrows. Rows the old domain CAN hold are untouched --
        that is the part worth guarding, since a downgrade that over-clears
        would be a real defect.
        """
        engine, db_url = at_previous
        run_alembic_upgrade(db_url, _REVISION)
        _seed(engine, account_id="acc_keeps_advertiser", billing="advertiser")
        _seed(engine, account_id="acc_untouched_operator", billing="operator")

        run_alembic_downgrade(db_url, _PREVIOUS)

        assert _billing_of(engine, "acc_keeps_advertiser") is None, (
            "the narrow constraint cannot hold 'advertiser', so the row must be cleared"
        )
        assert _billing_of(engine, "acc_untouched_operator") == "operator", (
            "the downgrade cleared a billing party the narrow constraint can hold"
        )

        constraint = _constraint_def(engine)
        assert constraint is not None and "advertiser" not in constraint, (
            f"the downgrade must narrow the constraint: {constraint}"
        )

    def test_it_narrows_the_constraint_when_no_account_needs_the_third_party(self, at_previous):
        """The success branch: with nothing to destroy, the downgrade is faithful."""
        engine, db_url = at_previous
        run_alembic_upgrade(db_url, _REVISION)
        _seed(engine, account_id="acc_survives", billing="operator")
        _seed(engine, account_id="acc_survives_null", billing=None)

        run_alembic_downgrade(db_url, _PREVIOUS)

        constraint = _constraint_def(engine)
        assert constraint is not None, f"{_CONSTRAINT} was dropped rather than narrowed"
        assert "advertiser" not in constraint, f"the constraint was not narrowed: {constraint}"

        assert _billing_of(engine, "acc_survives") == "operator"
        assert _billing_of(engine, "acc_survives_null") is None

        with pytest.raises(IntegrityError) as excinfo:
            _seed(engine, account_id="acc_post_downgrade", billing="advertiser")
        assert _CONSTRAINT in str(excinfo.value), f"a different constraint refused the row: {excinfo.value}"
