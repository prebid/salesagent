"""A principal cannot outlive its tenant, and the DATABASE is what guarantees it.

Covers alembic revision 390461e816ea (salesagent-3cs7o.26).

``initial_schema`` created ``principals.tenant_id`` as
``sa.ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"])`` — no ``ondelete``, which
PostgreSQL records as NO ACTION — and no later revision changed it. The mapped column has
declared ``ondelete="CASCADE"`` all along, but a declaration does not alter a constraint
that is already created, so the model and the migrated schema disagreed. What actually
deleted principals with their tenant was the ORM relationship ``Tenant.principals`` and
its ``cascade="all, delete-orphan"``, which runs only when a Tenant object is loaded and
deleted through a session. salesagent-3cs7o.26 deleted that relationship, and deleting a
tenant began failing with a foreign-key violation.

These tests must run against a MIGRATED database, which is why they use ``migration_db``
and not the ordinary integration fixtures. The integration suite builds its schema with
``Base.metadata.create_all``, so it reads the mapped column's ``ondelete`` and gets CASCADE
whether or not any migration ever sets it — a test written against that fixture passes
identically before and after this revision, and grades nothing. Reading the rule out of
``pg_constraint`` on a migrated database is the only thing that says what a deployed
database will do.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from tests.integration.migration_helpers import (
    run_alembic_downgrade,
    run_alembic_upgrade,
)

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

_REVISION = "390461e816ea"
_PREVIOUS = "c7e2a5b40d18"
#: PostgreSQL's default name for the unnamed constraint ``initial_schema`` declared.
_CONSTRAINT = "principals_tenant_id_fkey"

_TENANT = "mig_cascade_tenant"
_PRINCIPAL = "mig_cascade_principal"


@pytest.fixture
def at_previous(migration_db):
    """Put the module-scoped database at the revision BEFORE this one, with one tenant.

    ``migration_db`` is module-scoped and test order is randomized, so each test states
    its own starting revision rather than inheriting whatever ran before it. Principals
    are cleared as well as tenants: a leftover principal blocks ``DELETE FROM tenants``
    at exactly the revision these tests care about.
    """
    engine, db_url = migration_db
    run_alembic_upgrade(db_url, _PREVIOUS)
    run_alembic_downgrade(db_url, _PREVIOUS)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM principals"))
        conn.execute(text("DELETE FROM tenants"))
        conn.execute(
            text(
                "INSERT INTO tenants (tenant_id, name, subdomain, ad_server, is_active) "
                "VALUES (:tid, 'Migration Cascade', 'mig-cascade', 'mock', true)"
            ),
            {"tid": _TENANT},
        )
    return engine, db_url


def _delete_rule(engine) -> str | None:
    """The delete rule PostgreSQL will actually apply, as its ``confdeltype`` code.

    ``'a'`` is NO ACTION, ``'c'`` is CASCADE.
    """
    with engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT c.confdeltype FROM pg_constraint c "
                "JOIN pg_class t ON t.oid = c.conrelid "
                "WHERE t.relname = 'principals' AND c.conname = :name"
            ),
            {"name": _CONSTRAINT},
        ).scalar()


def _seed_principal(engine) -> None:
    """One principal in this module's tenant. Raw SQL: no ORM, so no ORM cascade."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO principals "
                "(tenant_id, principal_id, name, platform_mappings, token_hash, token_prefix) "
                "VALUES (:tid, :pid, 'Migration Cascade Advertiser', '{}'::jsonb, :hash, 'mig_')"
            ),
            {"tid": _TENANT, "pid": _PRINCIPAL, "hash": "0" * 64},
        )


def _counts(engine) -> tuple[int, int]:
    """(tenant rows, principal rows) for this module's tenant, counted in SQL."""
    with engine.connect() as conn:
        return (
            conn.execute(text("SELECT count(*) FROM tenants WHERE tenant_id = :t"), {"t": _TENANT}).scalar(),
            conn.execute(text("SELECT count(*) FROM principals WHERE tenant_id = :t"), {"t": _TENANT}).scalar(),
        )


class TestTheDeleteRuleBeforeAndAfter:
    def test_before_this_revision_the_rule_is_no_action(self, at_previous):
        """The state every database migrated before 390461e816ea is in."""
        engine, _ = at_previous
        assert _delete_rule(engine) == "a", (
            f"expected NO ACTION at revision {_PREVIOUS} — this test states the starting "
            f"point the revision changes, so a different value means the schema history moved"
        )

    def test_upgrade_sets_the_rule_to_cascade(self, at_previous):
        engine, db_url = at_previous
        run_alembic_upgrade(db_url, _REVISION)

        assert _delete_rule(engine) == "c", f"{_CONSTRAINT} is not ON DELETE CASCADE after {_REVISION}"

    def test_downgrade_restores_no_action(self, at_previous):
        engine, db_url = at_previous
        run_alembic_upgrade(db_url, _REVISION)
        run_alembic_downgrade(db_url, _PREVIOUS)

        assert _delete_rule(engine) == "a", f"downgrade of {_REVISION} did not restore NO ACTION"


class TestDeletingATenantDeletesItsPrincipals:
    def test_after_upgrade_the_database_removes_the_principal_with_the_tenant(self, at_previous):
        """The property the revision exists for, with no object graph anywhere in reach.

        ``Tenant.principals`` no longer exists, so no session could cascade this even if
        one were open. The DELETE and both counts are SQL for that reason.
        """
        engine, db_url = at_previous
        run_alembic_upgrade(db_url, _REVISION)
        _seed_principal(engine)
        assert _counts(engine) == (1, 1), "seed did not land"

        with engine.begin() as conn:
            conn.execute(text("DELETE FROM tenants WHERE tenant_id = :t"), {"t": _TENANT})

        assert _counts(engine) == (0, 0), "the principal outlived its tenant"

    def test_before_this_revision_the_tenant_could_not_be_deleted_at_all(self, at_previous):
        """The defect the revision fixes, stated as a test.

        With NO ACTION and no ORM relationship carrying the cascade, this is what
        deleting a tenant did: it raised, and left both rows behind.
        """
        from sqlalchemy.exc import IntegrityError

        engine, _ = at_previous
        _seed_principal(engine)

        with pytest.raises(IntegrityError, match=_CONSTRAINT):
            with engine.begin() as conn:
                conn.execute(text("DELETE FROM tenants WHERE tenant_id = :t"), {"t": _TENANT})

        assert _counts(engine) == (1, 1), "the refused delete should have changed nothing"
