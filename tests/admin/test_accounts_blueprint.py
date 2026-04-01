"""Integration tests for the accounts admin blueprint.

Tests account list, create, edit, and status management via Flask test client.
Requires PostgreSQL (integration_db fixture).

beads: salesagent-7kn
"""

import pytest
from sqlalchemy import delete

from src.admin.app import create_app
from src.core.database.database_session import get_db_session
from src.core.database.models import Account, Tenant
from tests.utils.database_helpers import create_tenant_with_timestamps

app = create_app()

pytestmark = [pytest.mark.admin, pytest.mark.requires_db]


@pytest.fixture
def client():
    """Flask test client with test configuration."""
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["SESSION_COOKIE_PATH"] = "/"
    with app.test_client() as client:
        yield client


@pytest.fixture
def test_tenant(integration_db):
    """Create a test tenant for account tests."""
    with get_db_session() as session:
        try:
            session.execute(delete(Account).where(Account.tenant_id == "acct_test_tenant"))
            session.execute(delete(Tenant).where(Tenant.tenant_id == "acct_test_tenant"))
            session.commit()
        except Exception:
            session.rollback()

        tenant = create_tenant_with_timestamps(
            tenant_id="acct_test_tenant",
            name="Account Test Tenant",
            subdomain="acct-test",
            ad_server="mock",
            is_active=True,
        )
        session.add(tenant)
        session.commit()

    return "acct_test_tenant"


def _auth_session(client, tenant_id):
    """Set up authenticated session for test client."""
    with client.session_transaction() as sess:
        sess["authenticated"] = True
        sess["user"] = {"email": "test@example.com", "is_super_admin": True}
        sess["email"] = "test@example.com"
        sess["tenant_id"] = tenant_id
        sess["test_user"] = "test@example.com"
        sess["test_user_role"] = "super_admin"
        sess["test_user_name"] = "Test User"
        sess["test_tenant_id"] = tenant_id


class TestAccountsListPage:
    """Test the accounts list page."""

    def test_list_page_returns_200(self, client, test_tenant):
        """GET /tenant/<tid>/accounts/ returns 200."""
        _auth_session(client, test_tenant)
        response = client.get(f"/tenant/{test_tenant}/accounts/")
        assert response.status_code == 200

    def test_list_page_contains_accounts_heading(self, client, test_tenant):
        """List page contains 'Accounts' heading."""
        _auth_session(client, test_tenant)
        response = client.get(f"/tenant/{test_tenant}/accounts/")
        html = response.data.decode()
        assert "Accounts" in html

    def test_list_page_shows_created_account(self, client, test_tenant):
        """After creating an account, the list page shows it."""
        _auth_session(client, test_tenant)

        # Create an account directly in DB
        with get_db_session() as session:
            account = Account(
                tenant_id=test_tenant,
                account_id="acc_ui_test",
                name="UI Test Account",
                status="active",
                operator="example.com",
                brand={"domain": "example.com"},
            )
            session.add(account)
            session.commit()

        response = client.get(f"/tenant/{test_tenant}/accounts/")
        html = response.data.decode()
        assert "UI Test Account" in html


class TestAccountCreatePage:
    """Test the account create page."""

    def test_create_form_returns_200(self, client, test_tenant):
        """GET /tenant/<tid>/accounts/create returns 200."""
        _auth_session(client, test_tenant)
        response = client.get(f"/tenant/{test_tenant}/accounts/create")
        assert response.status_code == 200

    def test_create_account_via_post(self, client, test_tenant):
        """POST /tenant/<tid>/accounts/create creates an account."""
        _auth_session(client, test_tenant)
        response = client.post(
            f"/tenant/{test_tenant}/accounts/create",
            data={
                "name": "New Test Account",
                "brand_domain": "newbrand.com",
                "operator": "newbrand.com",
                "billing": "operator",
            },
            follow_redirects=False,
        )
        # Should redirect to list page on success
        assert response.status_code in (302, 303)

        # Verify account was created in DB
        with get_db_session() as session:
            from sqlalchemy import select

            account = session.scalars(
                select(Account).where(
                    Account.tenant_id == test_tenant,
                    Account.name == "New Test Account",
                )
            ).first()
            assert account is not None
            assert account.status == "active"
            assert account.operator == "newbrand.com"


class TestAccountStatusManagement:
    """Test account status transitions."""

    def test_suspend_account(self, client, test_tenant):
        """POST status change to 'suspended' works."""
        _auth_session(client, test_tenant)

        # Create account
        with get_db_session() as session:
            account = Account(
                tenant_id=test_tenant,
                account_id="acc_status_test",
                name="Status Test",
                status="active",
                brand={"domain": "test.com"},
            )
            session.add(account)
            session.commit()

        response = client.post(
            f"/tenant/{test_tenant}/accounts/acc_status_test/status",
            json={"status": "suspended"},
        )
        assert response.status_code == 200

        # Verify in DB
        with get_db_session() as session:
            from sqlalchemy import select

            account = session.scalars(
                select(Account).where(
                    Account.tenant_id == test_tenant,
                    Account.account_id == "acc_status_test",
                )
            ).first()
            assert account is not None
            assert account.status == "suspended"
