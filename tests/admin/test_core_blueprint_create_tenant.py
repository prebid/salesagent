"""Integration tests for POST /create_tenant — duplicate tenant answers.

``create_tenant`` derives ``tenant_id`` from the submitted subdomain, but the
tenant management API mints a uuid-derived one. A tenant created there holding
subdomain ``acme`` therefore leaves this route's tenant_id pre-check clean, and
the INSERT trips ``tenants_subdomain_key`` instead of ``tenants_pkey`` — with no
concurrency involved at all. So this route needs BOTH indexes visible to its
pre-check and both named in its narrowing, and both are graded here: the
non-race widened pre-check, and the winner/loser pair.

The answer is a rendered page, and ``base.html`` consumes the flash while
rendering, so the answer is graded on the body: the polite message must be in it
and the driver's message must not.

Uses factory-boy factories per ``tests/CLAUDE.md``.
"""

from __future__ import annotations

import pytest

from src.admin.app import create_app
from src.admin.blueprints import core as core_module
from tests.factories import TenantFactory
from tests.helpers import concurrent_commit_in_write_window

app = create_app()

pytestmark = [pytest.mark.admin, pytest.mark.requires_db]

SUBDOMAIN = "contestedtenant"
TENANT_ID = f"tenant_{SUBDOMAIN}"
POLITE = f"Tenant with ID {TENANT_ID} already exists".encode()
DRIVER_TEXT = b"duplicate key value violates unique constraint"


@pytest.fixture
def client():
    """Flask test client with CSRF disabled for POST testing."""
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["SESSION_COOKIE_PATH"] = "/"
    with app.test_client() as client:
        yield client


@pytest.fixture(autouse=True)
def _enable_test_mode(monkeypatch):
    """Enable global test auth so require_auth accepts the test session."""
    monkeypatch.setenv("ADCP_AUTH_TEST_MODE", "true")


def _auth_session(client) -> None:
    """Populate a super-admin test-mode session."""
    with client.session_transaction() as sess:
        sess["authenticated"] = True
        sess["user"] = {"email": "test@example.com", "is_super_admin": True}
        sess["email"] = "test@example.com"
        sess["test_user"] = "test@example.com"
        sess["test_user_role"] = "super_admin"
        sess["test_user_name"] = "Test User"


def _post_create(client):
    return client.post(
        "/create_tenant",
        data={"name": "Contested Publisher", "subdomain": SUBDOMAIN, "ad_server": "mock"},
        follow_redirects=False,
    )


def _answer(response) -> tuple[int, bool, bool]:
    """(status, says the polite thing, leaks the driver's message)."""
    return response.status_code, POLITE in response.data, DRIVER_TEXT in response.data


class TestCreateTenantDuplicateSubdomain:
    """POST /create_tenant — the subdomain is taken by an API-created tenant."""

    def test_subdomain_owned_by_uuid_tenant_gets_the_polite_answer(self, client, factory_session):
        """No concurrency at all: the tenant_id-only pre-check never saw this.

        The API mints ``tenant_<uuid>``, so a tenant holding this subdomain is
        invisible to a ``filter_by(tenant_id=...)`` check and the INSERT collides
        on the subdomain index.
        """
        TenantFactory(tenant_id="tenant_ab12cd34", subdomain=SUBDOMAIN)
        _auth_session(client)

        assert _answer(_post_create(client)) == (200, True, False)

    def test_winner_and_loser_get_the_same_answer(self, client, factory_session):
        """The loser runs first, so its pre-check is genuinely clean."""
        _auth_session(client)

        def commit_conflicting_row():
            TenantFactory(tenant_id=TENANT_ID, subdomain=SUBDOMAIN)

        with concurrent_commit_in_write_window(core_module, commit_conflicting_row):
            loser = _answer(_post_create(client))

        winner = _answer(_post_create(client))

        assert winner == (200, True, False)
        assert loser == winner
