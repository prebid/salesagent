"""Admin account management harness for BDD tests.

Provides a Flask test client with authenticated session for testing
the admin accounts blueprint. Used by BDD step definitions in
tests/bdd/steps/domain/admin_accounts.py.

beads: salesagent-oj0.1.2
"""

from __future__ import annotations

from typing import Any

from flask.testing import FlaskClient
from sqlalchemy import delete

from src.admin.app import create_app
from src.core.database.database_session import get_db_session
from src.core.database.models import Account, Tenant
from tests.utils.database_helpers import create_tenant_with_timestamps


class AdminAccountEnv:
    """Test environment for admin account management BDD scenarios.

    Manages Flask test client lifecycle, authentication, and test data setup.
    Used as a context manager inside the _harness_env BDD fixture.
    """

    DEFAULT_TENANT_ID = "bdd_admin_tenant"

    def __init__(self) -> None:
        self._app = create_app()
        self._app.config["TESTING"] = True
        self._app.config["WTF_CSRF_ENABLED"] = False
        self._app.config["SESSION_COOKIE_PATH"] = "/"
        self._client: FlaskClient | None = None
        self._tenant_id: str = self.DEFAULT_TENANT_ID
        self._created_account_ids: list[str] = []

    def __enter__(self) -> AdminAccountEnv:
        self._client = self._app.test_client().__enter__()
        self._ensure_tenant()
        return self

    def __exit__(self, *exc: object) -> None:
        self._cleanup_accounts()
        if self._client is not None:
            self._client.__exit__(*exc)
            self._client = None

    @property
    def client(self) -> FlaskClient:
        assert self._client is not None, "AdminAccountEnv not entered as context manager"
        return self._client

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    # ── Auth ──────────────────────────────────────────────────────────────

    def authenticate(self, tenant_id: str | None = None) -> None:
        """Set up authenticated admin session."""
        tid = tenant_id or self._tenant_id
        with self.client.session_transaction() as sess:
            sess["authenticated"] = True
            sess["user"] = {"email": "test@example.com", "is_super_admin": True}
            sess["email"] = "test@example.com"
            sess["tenant_id"] = tid
            sess["test_user"] = "test@example.com"
            sess["test_user_role"] = "super_admin"
            sess["test_user_name"] = "Test User"
            sess["test_tenant_id"] = tid

    def clear_auth(self) -> None:
        """Clear the authenticated session."""
        with self.client.session_transaction() as sess:
            sess.clear()

    # ── Routes ────────────────────────────────────────────────────────────

    def _url(self, path: str = "") -> str:
        return f"/tenant/{self._tenant_id}/accounts/{path}"

    def get_list_page(self, status_filter: str | None = None) -> Any:
        """GET the accounts list page."""
        url = self._url()
        if status_filter:
            url += f"?status={status_filter}"
        return self.client.get(url)

    def get_create_page(self) -> Any:
        """GET the create account form."""
        return self.client.get(self._url("create"))

    def post_create(self, form_data: dict[str, str]) -> Any:
        """POST the create account form."""
        return self.client.post(self._url("create"), data=form_data, follow_redirects=False)

    def get_detail_page(self, account_id: str) -> Any:
        """GET the account detail page."""
        return self.client.get(self._url(account_id))

    def get_edit_page(self, account_id: str) -> Any:
        """GET the account edit form."""
        return self.client.get(self._url(f"{account_id}/edit"))

    def post_edit(self, account_id: str, form_data: dict[str, str]) -> Any:
        """POST the account edit form."""
        return self.client.post(self._url(f"{account_id}/edit"), data=form_data, follow_redirects=False)

    def post_status_change(self, account_id: str, new_status: str) -> Any:
        """POST a status change via JSON API."""
        return self.client.post(
            self._url(f"{account_id}/status"),
            json={"status": new_status},
        )

    # ── Data setup ────────────────────────────────────────────────────────

    def create_account(
        self,
        name: str,
        status: str = "active",
        brand_domain: str | None = None,
        operator: str | None = None,
        billing: str | None = None,
        payment_terms: str | None = None,
    ) -> str:
        """Create a test account directly in DB. Returns account_id."""
        import uuid

        account_id = f"acc_{uuid.uuid4().hex[:12]}"
        brand = {"domain": brand_domain} if brand_domain else None

        with get_db_session() as session:
            account = Account(
                tenant_id=self._tenant_id,
                account_id=account_id,
                name=name,
                status=status,
                brand=brand,
                operator=operator,
                billing=billing,
                payment_terms=payment_terms,
            )
            session.add(account)
            session.commit()

        self._created_account_ids.append(account_id)
        return account_id

    def get_account_from_db(self, *, name: str | None = None, account_id: str | None = None) -> Account | None:
        """Look up an account in the database."""
        from sqlalchemy import select

        with get_db_session() as session:
            stmt = select(Account).where(Account.tenant_id == self._tenant_id)
            if name:
                stmt = stmt.where(Account.name == name)
            if account_id:
                stmt = stmt.where(Account.account_id == account_id)
            return session.scalars(stmt).first()

    def get_account_id_by_name(self, name: str) -> str | None:
        """Get account_id by name."""
        account = self.get_account_from_db(name=name)
        return account.account_id if account else None

    # ── Internal ──────────────────────────────────────────────────────────

    def _ensure_tenant(self) -> None:
        """Ensure the test tenant exists."""
        with get_db_session() as session:
            from sqlalchemy import select

            existing = session.scalars(select(Tenant).where(Tenant.tenant_id == self._tenant_id)).first()
            if not existing:
                tenant = create_tenant_with_timestamps(
                    tenant_id=self._tenant_id,
                    name="BDD Admin Test Tenant",
                    subdomain="bdd-admin",
                    ad_server="mock",
                    is_active=True,
                )
                session.add(tenant)
                session.commit()

    def _cleanup_accounts(self) -> None:
        """Remove test accounts created during this scenario."""
        if not self._created_account_ids:
            return
        with get_db_session() as session:
            session.execute(
                delete(Account).where(
                    Account.tenant_id == self._tenant_id,
                    Account.account_id.in_(self._created_account_ids),
                )
            )
            session.commit()
        self._created_account_ids.clear()
