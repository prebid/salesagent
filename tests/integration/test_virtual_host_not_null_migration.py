"""The NOT NULL revision refuses a host-less tenant rather than inventing a host for it.

Covers alembic revision 7f31c0ab94d2.

This is the half of the change that could repeat the defect it fixes. A backfill has only
two expressions available — ``<subdomain>.<SALES_AGENT_DOMAIN>`` and
``<subdomain>.example.com`` — and either puts a host on a tenant's card that nothing on the
network serves (#1845). So the revision refuses, names the offending rows, and leaves them
for an operator who knows where the deployment answers.

Runs against a MIGRATED database (``migration_db``), not the ordinary integration fixtures:
those build the schema with ``Base.metadata.create_all``, which reads ``nullable=False``
off the mapped column and would report NOT NULL whether or not this revision ever runs.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from tests.integration.migration_helpers import run_alembic_downgrade, run_alembic_upgrade

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

_REVISION = "7f31c0ab94d2"
_PREVIOUS = "390461e816ea"

_HOSTED = "mig_vhost_hosted"
_HOSTLESS = "mig_vhost_hostless"


def _at_previous(migration_db):
    """The database one revision back, with tenants cleared.

    ``migration_db`` is module-scoped and test order is randomized, so each test states its
    own starting revision rather than inheriting whatever ran before it.
    """
    engine, db_url = migration_db
    run_alembic_upgrade(db_url, _PREVIOUS)
    run_alembic_downgrade(db_url, _PREVIOUS)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM principals"))
        conn.execute(text("DELETE FROM tenants"))
    return engine, db_url


def _insert_tenant(engine, tenant_id: str, virtual_host: str | None) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO tenants (tenant_id, name, subdomain, virtual_host, ad_server, is_active) "
                "VALUES (:tid, :tid, :tid, :vhost, 'mock', true)"
            ),
            {"tid": tenant_id, "vhost": virtual_host},
        )


def _is_nullable(engine) -> bool:
    with engine.begin() as conn:
        answer = conn.execute(
            text(
                "SELECT is_nullable FROM information_schema.columns "
                "WHERE table_name = 'tenants' AND column_name = 'virtual_host'"
            )
        ).scalar_one()
    return answer == "YES"


def test_the_upgrade_refuses_and_names_the_rows_it_cannot_fix(migration_db):
    """A NULL row stops the upgrade, and the message carries the tenant_id and the fix.

    Naming them is the whole deliverable of the refusal: migrations run automatically on
    container startup, so an operator meeting this sees it instead of a booted app, and the
    message has to be enough to act on without reading the revision.
    """
    engine, db_url = _at_previous(migration_db)
    _insert_tenant(engine, _HOSTLESS, None)

    with pytest.raises(RuntimeError) as raised:
        run_alembic_upgrade(db_url, _REVISION)

    message = str(raised.value)
    assert _HOSTLESS in message, f"the refusal did not name the offending tenant: {message}"
    assert "UPDATE tenants SET virtual_host" in message, f"the refusal carried no fix to run: {message}"

    # Refused means refused: the constraint is not applied, and the row is still there.
    assert _is_nullable(engine), "the column was made NOT NULL despite a NULL row"
    with engine.begin() as conn:
        assert conn.execute(text("SELECT count(*) FROM tenants")).scalar_one() == 1, (
            "the revision deleted a tenant — losing a publisher's row is not a migration's decision"
        )


@pytest.mark.parametrize(
    ("stored", "why"),
    [
        ("https://evil.com", "a scheme"),
        ("user@host.com", "userinfo"),
        ("evil.com/path", "a path"),
        ("", "blank"),
        ("[::1", "an unterminated IPv6 bracket"),
        ("a b.com", "whitespace"),
        ("host.example.com:", "a trailing colon"),
    ],
)
def test_the_upgrade_refuses_a_stored_host_that_is_not_a_host(migration_db, stored, why):
    """A malformed origin stops the upgrade and is NAMED, like a NULL one.

    Shape has to be refused because this revision derives the routing key from this column,
    and a derived name makes the row REACHABLE: ``user@host.com`` derives ``host.com``, so a
    request naming that host would be served a card advertising ``https://user@host.com/a2a``.
    Before the derived column such a row was unroutable and therefore harmless.

    The unterminated-bracket case is here for a second reason: ``urlsplit`` raises its own
    ``ValueError`` for it, which would abort the upgrade with urllib's text and no tenant
    named — a refusal an operator cannot act on.
    """
    engine, db_url = _at_previous(migration_db)
    tenant_id = f"vh_shape_{abs(hash(stored)) % 10**6}"
    _insert_tenant(engine, tenant_id, stored)

    with pytest.raises(RuntimeError) as raised:
        run_alembic_upgrade(db_url, _REVISION)

    message = str(raised.value)
    assert tenant_id in message, f"the refusal did not name the tenant holding {stored!r} ({why}): {message}"
    assert _is_nullable(engine), f"the column was made NOT NULL despite a row holding {stored!r}"


def test_the_upgrade_invents_no_host_for_the_row_it_refuses(migration_db):
    """After the refusal the NULL is still NULL — nothing was derived into it.

    The assertion this module exists for. A backfill would leave the upgrade GREEN and the
    row holding a plausible-looking name that nothing serves, which is the failure mode of
    #1845 and is invisible to a test that only checks the constraint.
    """
    engine, db_url = _at_previous(migration_db)
    _insert_tenant(engine, _HOSTLESS, None)

    with pytest.raises(RuntimeError):
        run_alembic_upgrade(db_url, _REVISION)

    with engine.begin() as conn:
        stored = conn.execute(
            text("SELECT virtual_host FROM tenants WHERE tenant_id = :tid"), {"tid": _HOSTLESS}
        ).scalar_one()
    assert stored is None, f"the migration fabricated a host: {stored!r}"


def test_the_column_refuses_null_once_every_tenant_declares_a_host(migration_db):
    """With no NULL rows the upgrade applies the constraint, and the database then refuses one."""
    from sqlalchemy.exc import IntegrityError

    engine, db_url = _at_previous(migration_db)
    _insert_tenant(engine, _HOSTED, "mig-vhost-hosted.adcp.test")

    run_alembic_upgrade(db_url, _REVISION)

    assert not _is_nullable(engine), "the revision did not make virtual_host NOT NULL"
    with pytest.raises(IntegrityError):
        _insert_tenant(engine, _HOSTLESS, None)


def test_the_downgrade_reverts_the_constraint(migration_db):
    """``downgrade`` puts the column back to nullable — structure only, in both directions."""
    engine, db_url = _at_previous(migration_db)
    _insert_tenant(engine, _HOSTED, "mig-vhost-hosted.adcp.test")

    run_alembic_upgrade(db_url, _REVISION)
    assert not _is_nullable(engine)

    run_alembic_downgrade(db_url, _PREVIOUS)
    assert _is_nullable(engine), "the downgrade left the column NOT NULL"
