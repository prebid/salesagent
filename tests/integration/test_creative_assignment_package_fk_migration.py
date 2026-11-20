"""A creative assignment names a package its buy holds, and the DATABASE is what guarantees it.

Covers alembic revision 502abb701275. Before it, ``creative_assignments`` had a foreign key
to ``media_buys`` and none to ``media_packages``, so a row could name a package its buy does
not hold, and the approval upload failed on it after the order was booked.

Run against a MIGRATED database (``migration_db``), for the reason
``test_principal_tenant_cascade_migration`` gives: the integration fixtures build the schema
with ``Base.metadata.create_all``, which reads the model's constraint whether or not any
revision creates it. Rows are raw SQL, so no ORM check stands in front of the constraint.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from tests.integration.migration_helpers import reset_to_revision, run_alembic_downgrade, run_alembic_upgrade

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

_REVISION = "502abb701275"
_PREVIOUS = "9fdf18a56063"
_CONSTRAINT = "fk_creative_assignments_package_composite"

_TENANT = "mig_asgn_pkg_tenant"
_PRINCIPAL = "mig_asgn_pkg_principal"
_CREATIVE = "mig_asgn_pkg_creative"
#: Two buys, each holding one package: the other buy's package is the one this buy does not hold.
_BUY, _PACKAGE = "mig_asgn_pkg_buy", "pkg_held"
_OTHER_BUY, _OTHER_PACKAGE = "mig_asgn_pkg_other_buy", "pkg_of_the_other_buy"


@pytest.fixture
def at_previous(migration_db):
    """The module-scoped database at the revision BEFORE this one, with two buys and a creative."""
    engine, db_url = reset_to_revision(
        migration_db,
        revision=_PREVIOUS,
        tenant_id=_TENANT,
        tenant_name="Assignment Package FK",
        subdomain="mig-asgn-pkg",
    )
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO principals "
                "(tenant_id, principal_id, name, platform_mappings, token_hash, token_prefix) "
                "VALUES (:tid, :pid, 'Assignment Package FK Advertiser', '{}'::jsonb, :hash, 'mig_')"
            ),
            {"tid": _TENANT, "pid": _PRINCIPAL, "hash": "0" * 64},
        )
        conn.execute(
            text(
                "INSERT INTO creatives (creative_id, tenant_id, principal_id, name, format_kind, status, data) "
                "VALUES (:cid, :tid, :pid, 'Creative', 'display_image', 'approved', '{}'::jsonb)"
            ),
            {"cid": _CREATIVE, "tid": _TENANT, "pid": _PRINCIPAL},
        )
        for buy, package in ((_BUY, _PACKAGE), (_OTHER_BUY, _OTHER_PACKAGE)):
            conn.execute(
                text(
                    "INSERT INTO media_buys (media_buy_id, tenant_id, principal_id, order_name, "
                    "advertiser_name, start_date, end_date, status, raw_request) "
                    "VALUES (:mbid, :tid, :pid, 'Order', 'Advertiser', "
                    "DATE '2026-01-10', DATE '2026-01-20', 'pending_approval', '{}'::jsonb)"
                ),
                {"mbid": buy, "tid": _TENANT, "pid": _PRINCIPAL},
            )
            conn.execute(
                text(
                    "INSERT INTO media_packages (media_buy_id, package_id, package_config) "
                    "VALUES (:mbid, :pkg, '{}'::jsonb)"
                ),
                {"mbid": buy, "pkg": package},
            )
    return engine, db_url


def _assign(engine, package_id: str) -> None:
    """One assignment of the creative to *package_id* of ``_BUY``."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO creative_assignments "
                "(assignment_id, tenant_id, creative_id, principal_id, media_buy_id, package_id, weight) "
                "VALUES (:aid, :tid, :cid, :pid, :mbid, :pkg, 100)"
            ),
            {
                "aid": f"asgn_{package_id}",
                "tid": _TENANT,
                "cid": _CREATIVE,
                "pid": _PRINCIPAL,
                "mbid": _BUY,
                "pkg": package_id,
            },
        )


def _stored_packages(engine) -> list[str]:
    """The packages ``_BUY``'s stored assignments name."""
    with engine.connect() as conn:
        return list(
            conn.execute(
                text("SELECT package_id FROM creative_assignments WHERE media_buy_id = :mbid"), {"mbid": _BUY}
            ).scalars()
        )


def _constraint_exists(engine) -> bool:
    with engine.connect() as conn:
        return bool(
            conn.execute(
                text(
                    "SELECT count(*) FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid "
                    "WHERE t.relname = 'creative_assignments' AND c.conname = :name AND c.contype = 'f'"
                ),
                {"name": _CONSTRAINT},
            ).scalar()
        )


class TestTheConstraintBeforeAndAfter:
    def test_before_this_revision_an_assignment_can_name_a_package_its_buy_does_not_hold(self, at_previous):
        """The defect the revision fixes, stated as a test."""
        engine, _ = at_previous
        _assign(engine, _OTHER_PACKAGE)

        assert _stored_packages(engine) == [_OTHER_PACKAGE]

    def test_after_upgrade_the_database_refuses_a_package_its_buy_does_not_hold(self, at_previous):
        """The other buy's package: the key is the PAIR, so a package id alone does not satisfy it."""
        engine, db_url = at_previous
        run_alembic_upgrade(db_url, _REVISION)
        assert _constraint_exists(engine)

        with pytest.raises(IntegrityError, match=_CONSTRAINT):
            _assign(engine, _OTHER_PACKAGE)
        assert _stored_packages(engine) == []

    def test_after_upgrade_an_assignment_to_a_held_package_is_stored(self, at_previous):
        engine, db_url = at_previous
        run_alembic_upgrade(db_url, _REVISION)

        _assign(engine, _PACKAGE)

        assert _stored_packages(engine) == [_PACKAGE]

    def test_upgrade_fails_on_a_stored_assignment_naming_a_package_its_buy_does_not_hold(self, at_previous):
        """The revision changes structure only: it carries no data step, so such a row stops it."""
        engine, db_url = at_previous
        _assign(engine, _OTHER_PACKAGE)

        with pytest.raises(IntegrityError, match=_CONSTRAINT):
            run_alembic_upgrade(db_url, _REVISION)
        assert not _constraint_exists(engine)

    def test_downgrade_drops_the_constraint(self, at_previous):
        engine, db_url = at_previous
        run_alembic_upgrade(db_url, _REVISION)
        run_alembic_downgrade(db_url, _PREVIOUS)

        assert not _constraint_exists(engine)
        _assign(engine, _OTHER_PACKAGE)
        assert _stored_packages(engine) == [_OTHER_PACKAGE]
