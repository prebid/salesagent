"""The natural-key uniqueness migration creates the index, and refuses dirty data.

Covers alembic revision b2e94f7c1a03 (salesagent-0njj).

Two paths, both otherwise unexercised. The integration suite builds its schema
with ``Base.metadata.create_all``, so nothing else runs this migration's SQL at
all; and the survey-and-abort arm only fires against a database that already
holds a collision, which by construction no other test produces.

The abort arm matters more than the happy one. Accounts are source-of-truth
rows, so the migration deliberately refuses to remediate: it reports the
colliding keys and stops, leaving the operator to decide which account survives
(owner decision, 2026-07-27). A regression that turned that into a silent
success would create the index over dirty data — impossible — or, worse, a
future edit that "helpfully" closed the extras would destroy live accounts with
no way back, since ``closed`` is terminal in the admin status transitions.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from tests.integration.migration_helpers import (
    reset_to_revision,
    run_alembic_downgrade,
    run_alembic_upgrade,
    seed_account,
)

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

_REVISION = "b2e94f7c1a03"
_PREVIOUS = "d3f7a5c81e46"
_INDEX = "uq_accounts_natural_key"

_TENANT = "mig_nk_tenant"


@pytest.fixture
def at_previous(migration_db):
    """Put the module-scoped database at the revision BEFORE this one, with no rows.

    The abort test here leaves colliding rows behind that would poison any later
    upgrade, so every test starts from a clean tenant — see ``reset_to_revision``.
    The downgrade also drops the index when a previous test left the DB above this
    revision.
    """
    return reset_to_revision(
        migration_db,
        revision=_PREVIOUS,
        tenant_id=_TENANT,
        tenant_name="Migration NK",
        subdomain="mig-nk",
    )


def _seed_account(engine, *, account_id: str, domain: str | None, operator: str | None) -> None:
    """Seed into this module's tenant — the shared helper carries the SQL."""
    seed_account(engine, tenant_id=_TENANT, account_id=account_id, domain=domain, operator=operator)


def _index_def(engine) -> str | None:
    with engine.connect() as conn:
        return conn.execute(text("SELECT indexdef FROM pg_indexes WHERE indexname = :name"), {"name": _INDEX}).scalar()


class TestTheMigrationCreatesTheIndex:
    def test_upgrade_creates_a_partial_unique_index_over_the_whole_key(self, at_previous):
        engine, db_url = at_previous
        run_alembic_upgrade(db_url, _REVISION)

        indexdef = _index_def(engine)
        assert indexdef is not None, f"{_INDEX} was not created by revision {_REVISION}"
        assert "UNIQUE INDEX" in indexdef, f"index is not unique: {indexdef}"
        for component in ("tenant_id", "operator", "'domain'", "'brand_id'", "COALESCE"):
            assert component in indexdef, f"key component {component!r} missing: {indexdef}"
        assert "NULLS NOT DISTINCT" in indexdef, f"NULL operator/brand_id would escape: {indexdef}"
        assert "WHERE" in indexdef, f"index must stay partial on brand.domain: {indexdef}"

    def test_the_index_actually_rejects_a_duplicate_key(self, at_previous):
        """The point of the index: a second row on one key cannot be inserted.

        Inserted with raw SQL on purpose — the repository check is not in the
        path here, so what is graded is the DATABASE refusing, which is the only
        thing that closes the race between two concurrent creates.
        """
        from sqlalchemy.exc import IntegrityError

        engine, db_url = at_previous
        run_alembic_upgrade(db_url, _REVISION)
        _seed_account(engine, account_id="acc_first", domain="acme.com", operator="example.com")

        with pytest.raises(IntegrityError):
            _seed_account(engine, account_id="acc_second", domain="acme.com", operator="example.com")

    def test_keyless_accounts_are_left_unconstrained(self, at_previous):
        """Rows with no brand domain have no key — the partial clause spares them."""
        engine, db_url = at_previous
        run_alembic_upgrade(db_url, _REVISION)

        _seed_account(engine, account_id="acc_keyless_1", domain=None, operator=None)
        _seed_account(engine, account_id="acc_keyless_2", domain=None, operator=None)

        with engine.connect() as conn:
            count = conn.execute(
                text("SELECT count(*) FROM accounts WHERE tenant_id = :tid"), {"tid": _TENANT}
            ).scalar()
        assert count == 2, "brand-less accounts carry no natural key and must not collide"

    def test_downgrade_removes_the_index(self, at_previous):
        engine, db_url = at_previous
        run_alembic_upgrade(db_url, _REVISION)
        assert _index_def(engine) is not None

        run_alembic_downgrade(db_url, _PREVIOUS)
        assert _index_def(engine) is None, f"{_INDEX} survived the downgrade"


class TestTheMigrationRefusesDirtyData:
    def test_a_preexisting_collision_aborts_the_upgrade(self, at_previous):
        """It reports and stops. It must never choose a survivor on its own."""
        engine, db_url = at_previous
        _seed_account(engine, account_id="acc_dupe_a", domain="acme.com", operator="example.com")
        _seed_account(engine, account_id="acc_dupe_b", domain="acme.com", operator="example.com")

        with pytest.raises(RuntimeError) as excinfo:
            run_alembic_upgrade(db_url, _REVISION)

        message = str(excinfo.value)
        assert "acc_dupe_a" in message and "acc_dupe_b" in message, (
            f"the abort must name the colliding accounts so an operator can act on it: {message}"
        )
        assert _index_def(engine) is None, "the index must not exist after an aborted upgrade"

        with engine.connect() as conn:
            surviving = conn.execute(
                text("SELECT count(*) FROM accounts WHERE tenant_id = :tid"), {"tid": _TENANT}
            ).scalar()
        assert surviving == 2, (
            "the migration must not remediate: closing an account is terminal and the downgrade "
            "could not undo it, so the choice belongs to the operator"
        )
