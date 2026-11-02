"""Integration tests for duplicate handling in the publisher-partners blueprint.

Reproduces salesagent-cdw2 on one of its six sites
(``src/admin/blueprints/publisher_partners.py``).

``add_publisher_partner`` pre-checks for an existing ``(tenant_id,
publisher_domain)`` row and answers a clean ``409 {"error": "Publisher already
exists"}`` when it finds one. That pre-check runs inside the request's own
transaction, so it cannot see a row a *concurrent* transaction has staged but not
yet committed. When the concurrent writer commits first, the ``uq_tenant_publisher``
unique constraint fires at ``session.commit()`` and the handler's blanket
``except Exception`` turns the loser's response into a ``500`` carrying a raw
psycopg2 message — for exactly the condition it explains politely when it wins.

Requires PostgreSQL (``integration_db`` via the ``factory_session`` fixture).
Uses factory-boy factories per ``tests/CLAUDE.md`` — no inline ``session.add()``
in test bodies.
"""

from __future__ import annotations

import pytest
from sqlalchemy import delete

from src.admin.app import create_app
from src.admin.blueprints import publisher_partners as publisher_partners_module
from src.core.database.models import Tenant
from tests.factories import PublisherPartnerFactory, TenantFactory
from tests.helpers import concurrent_commit_in_write_window

app = create_app()

pytestmark = [pytest.mark.admin, pytest.mark.requires_db]

DUPLICATE_DOMAIN = "contested-publisher.example.com"


@pytest.fixture
def client():
    """Flask test client with CSRF disabled for POST testing."""
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["SESSION_COOKIE_PATH"] = "/"
    with app.test_client() as client:
        yield client


class TestAddPublisherPartnerDuplicate:
    """POST /tenant/<id>/publisher-partners — duplicate publisher_domain."""

    def test_duplicate_already_committed_returns_409(self, client, factory_session):
        """Control: when the pre-check sees the existing row, the operator gets a clean 409.

        This is the polite answer the same code path gives when it wins the race.
        """
        tenant = TenantFactory()
        PublisherPartnerFactory(tenant=tenant, publisher_domain=DUPLICATE_DOMAIN)

        response = client.post(
            f"/tenant/{tenant.tenant_id}/publisher-partners",
            json={"publisher_domain": DUPLICATE_DOMAIN, "display_name": "Contested"},
        )

        assert response.status_code == 409
        assert response.get_json() == {"error": "Publisher already exists"}

    def test_duplicate_committed_after_precheck_returns_409(self, client, factory_session):
        """The loser of the race must get the same clean 409, not a 500.

        Reproduces salesagent-cdw2: the pre-check passes (no row yet), a concurrent
        transaction commits the row, and our commit hits uq_tenant_publisher. The
        handler has no IntegrityError branch, so the blanket ``except Exception``
        answers 500 with a raw driver message.
        """
        tenant = TenantFactory()

        def commit_conflicting_row():
            PublisherPartnerFactory(tenant=tenant, publisher_domain=DUPLICATE_DOMAIN)

        with concurrent_commit_in_write_window(publisher_partners_module, commit_conflicting_row):
            response = client.post(
                f"/tenant/{tenant.tenant_id}/publisher-partners",
                json={"publisher_domain": DUPLICATE_DOMAIN, "display_name": "Contested"},
            )

        assert response.status_code == 409
        assert response.get_json() == {"error": "Publisher already exists"}

    def test_unrelated_constraint_violation_is_not_answered_as_duplicate(self, client, factory_session):
        """A constraint violation that is NOT the duplicate must still propagate.

        Mechanism, with no production patching: an independent transaction
        DELETEs the tenant inside the write window. The handler read it before
        that, so its INSERT trips ``publisher_partners_tenant_id_fkey`` — a
        foreign-key violation, not a duplicate.

        This grades the ROUTE's answer, not the narrowing: here the re-resolve
        finds nothing either, so the recovery would re-raise even unnarrowed.
        The narrowing itself needs a resolvable conflict AND a different
        constraint at once, which no route can stage — that is graded in
        ``tests/integration/test_integrity_resolve_or_write.py``.
        """
        tenant = TenantFactory()

        def delete_the_tenant():
            factory_session.execute(delete(Tenant).where(Tenant.tenant_id == tenant.tenant_id))
            factory_session.commit()

        with concurrent_commit_in_write_window(publisher_partners_module, delete_the_tenant):
            response = client.post(
                f"/tenant/{tenant.tenant_id}/publisher-partners",
                json={"publisher_domain": DUPLICATE_DOMAIN, "display_name": "Contested"},
            )

        assert (response.status_code, response.get_json()) != (409, {"error": "Publisher already exists"})
        assert response.status_code == 500


class TestUnexpectedDbErrorDoesNotLeakDriverInternals:
    """A non-duplicate DB error must answer with a generic message, not the driver dump.

    The handler's blanket ``except Exception`` puts ``str(e)`` into the 500 body,
    so any database error the duplicate-narrowing does not cover shows the
    operator raw psycopg2 text — the failing statement and its parameter values
    included (parameter values in a DETAIL line can carry tenant data).
    """

    def test_data_error_500_carries_no_driver_internals(self, client, factory_session):
        """Deterministic non-duplicate DB error: a value the column cannot hold.

        ``publisher_domain`` is String(255); an oversized value passes every
        handler check and dies at flush with a DataError the handler does not
        narrow. The operator may be told the write failed — but never shown the
        driver name, the SQL statement, or the bound parameters.
        """
        tenant = TenantFactory()
        oversized = ("a" * 300) + ".example.com"

        response = client.post(
            f"/tenant/{tenant.tenant_id}/publisher-partners",
            json={"publisher_domain": oversized, "display_name": "Oversized"},
        )

        assert response.status_code == 500
        body = response.get_data(as_text=True)
        for marker in ("psycopg2", "INSERT INTO", "[SQL", "[parameters", "DETAIL:"):
            assert marker not in body, f"500 body leaks driver internals ({marker!r}): {body[:300]}"
