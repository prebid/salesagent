"""Admin account management harness for BDD tests.

Provides two transports for admin account management BDD scenarios:
- **integration**: Flask test_client (in-process, no Docker)
- **e2e**: requests.Session against Docker stack (full deployment)

Transport selection is automatic based on ``ADCP_SALES_PORT`` env var.

beads: salesagent-oj0.1.2, salesagent-oj0.1.3
"""

from __future__ import annotations

import logging
import os
from typing import Any

from sqlalchemy import delete

from src.core.database.database_session import get_db_session
from src.core.database.models import Account, Tenant
from tests.utils.database_helpers import create_tenant_with_timestamps

logger = logging.getLogger(__name__)


class _AdminResponse:
    """Unified response wrapper for Flask test_client and requests.Session.

    Normalizes the response interface so step definitions don't need to
    know which transport is active.
    """

    def __init__(self, status_code: int, data: bytes, headers: dict[str, str], json_data: Any = None) -> None:
        self.status_code = status_code
        self._data = data
        self.headers = headers
        self._json_data = json_data

    @property
    def data(self) -> bytes:
        return self._data

    def get_json(self) -> Any:
        if self._json_data is not None:
            return self._json_data
        import json

        return json.loads(self._data)

    @classmethod
    def from_flask(cls, response: Any) -> _AdminResponse:
        """Wrap a Flask/werkzeug test response."""
        return cls(
            status_code=response.status_code,
            data=response.data,
            headers=dict(response.headers),
        )

    @classmethod
    def from_requests(cls, response: Any) -> _AdminResponse:
        """Wrap a requests.Response."""
        return cls(
            status_code=response.status_code,
            data=response.content,
            headers=dict(response.headers),
            json_data=response.json()
            if response.headers.get("content-type", "").startswith("application/json")
            else None,
        )


class AdminAccountEnv:
    """Test environment for admin account management BDD scenarios.

    Manages Flask test client lifecycle, authentication, and test data setup.
    Used as a context manager inside the _harness_env BDD fixture.

    Supports two modes:
    - ``integration``: Flask test_client (default, in-process)
    - ``e2e``: requests.Session against Docker stack (when ADCP_SALES_PORT set)
    """

    DEFAULT_TENANT_ID = "bdd_admin_tenant"

    def __init__(self, *, mode: str | None = None) -> None:
        self._e2e_port = os.environ.get("ADCP_SALES_PORT")
        # Explicit mode overrides auto-detection
        if mode is not None:
            self._mode = mode
        else:
            self._mode = "e2e" if self._e2e_port else "integration"

        # Integration mode: Flask app + test_client
        self._app: Any = None
        self._flask_client: Any = None

        # E2E mode: requests.Session
        self._session: Any = None
        self._base_url: str = ""

        self._tenant_id: str = self.DEFAULT_TENANT_ID
        self._created_account_ids: list[str] = []

    @property
    def mode(self) -> str:
        """Current transport mode: 'integration' or 'e2e'."""
        return self._mode

    def __enter__(self) -> AdminAccountEnv:
        if self._mode == "integration":
            self._setup_integration()
        else:
            self._setup_e2e()
        self._ensure_tenant()
        return self

    def __exit__(self, *exc: object) -> None:
        self._cleanup_accounts()
        if self._mode == "integration" and self._flask_client is not None:
            self._flask_client.__exit__(*exc)
            self._flask_client = None
        elif self._mode == "e2e" and self._session is not None:
            self._session.close()
            self._session = None

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    # ── Setup ─────────────────────────────────────────────────────────────

    def _setup_integration(self) -> None:
        """Set up Flask test_client for integration transport."""
        from src.admin.app import create_app

        self._app = create_app()
        self._app.config["TESTING"] = True
        self._app.config["WTF_CSRF_ENABLED"] = False
        self._app.config["SESSION_COOKIE_PATH"] = "/"
        self._flask_client = self._app.test_client().__enter__()

    def _setup_e2e(self) -> None:
        """Set up requests.Session for e2e transport against Docker stack."""
        import requests

        self._base_url = f"http://localhost:{self._e2e_port}"
        self._session = requests.Session()
        logger.info("Admin e2e transport: %s", self._base_url)

    # ── Auth ──────────────────────────────────────────────────────────────

    def authenticate(self, tenant_id: str | None = None) -> None:
        """Set up authenticated admin session."""
        tid = tenant_id or self._tenant_id
        if self._mode == "integration":
            self._auth_integration(tid)
        else:
            self._auth_e2e(tid)

    def _auth_integration(self, tenant_id: str) -> None:
        """Session-based auth for Flask test_client."""
        with self._flask_client.session_transaction() as sess:
            sess["authenticated"] = True
            sess["user"] = {"email": "test@example.com", "is_super_admin": True}
            sess["email"] = "test@example.com"
            sess["tenant_id"] = tenant_id
            sess["test_user"] = "test@example.com"
            sess["test_user_role"] = "super_admin"
            sess["test_user_name"] = "Test User"
            sess["test_tenant_id"] = tenant_id

    def _auth_e2e(self, tenant_id: str) -> None:
        """Cookie-based auth via /test/auth endpoint on Docker stack."""
        assert self._session is not None
        resp = self._session.post(
            f"{self._base_url}/test/auth",
            data={
                "email": "test_super_admin@example.com",
                "password": "test123",
                "tenant_id": tenant_id,
            },
            allow_redirects=False,
        )
        # /test/auth redirects on success (302) — session cookie is stored
        if resp.status_code not in (200, 302):
            raise RuntimeError(f"E2E auth failed: {resp.status_code} {resp.text[:200]}")

    def clear_auth(self) -> None:
        """Clear the authenticated session."""
        if self._mode == "integration":
            with self._flask_client.session_transaction() as sess:
                sess.clear()
        else:
            # E2E: create a fresh session (drops cookies)
            import requests

            if self._session is not None:
                self._session.close()
            self._session = requests.Session()

    # ── Routes ────────────────────────────────────────────────────────────

    def _url(self, path: str = "") -> str:
        prefix = self._base_url if self._mode == "e2e" else ""
        return f"{prefix}/tenant/{self._tenant_id}/accounts/{path}"

    def get_list_page(self, status_filter: str | None = None) -> _AdminResponse:
        """GET the accounts list page."""
        url = self._url()
        if status_filter:
            url += f"?status={status_filter}"
        return self._get(url)

    def get_create_page(self) -> _AdminResponse:
        """GET the create account form."""
        return self._get(self._url("create"))

    def post_create(self, form_data: dict[str, str]) -> _AdminResponse:
        """POST the create account form."""
        return self._post_form(self._url("create"), form_data)

    def get_detail_page(self, account_id: str) -> _AdminResponse:
        """GET the account detail page."""
        return self._get(self._url(account_id))

    def get_edit_page(self, account_id: str) -> _AdminResponse:
        """GET the account edit form."""
        return self._get(self._url(f"{account_id}/edit"))

    def post_edit(self, account_id: str, form_data: dict[str, str]) -> _AdminResponse:
        """POST the account edit form."""
        return self._post_form(self._url(f"{account_id}/edit"), form_data)

    def post_status_change(self, account_id: str, new_status: str) -> _AdminResponse:
        """POST a status change via JSON API."""
        return self._post_json(self._url(f"{account_id}/status"), {"status": new_status})

    # ── HTTP helpers ──────────────────────────────────────────────────────

    def _get(self, url: str) -> _AdminResponse:
        if self._mode == "integration":
            return _AdminResponse.from_flask(self._flask_client.get(url))
        return _AdminResponse.from_requests(self._session.get(url, allow_redirects=False))

    def _post_form(self, url: str, data: dict[str, str]) -> _AdminResponse:
        if self._mode == "integration":
            return _AdminResponse.from_flask(self._flask_client.post(url, data=data, follow_redirects=False))
        return _AdminResponse.from_requests(self._session.post(url, data=data, allow_redirects=False))

    def _post_json(self, url: str, data: dict[str, Any]) -> _AdminResponse:
        if self._mode == "integration":
            return _AdminResponse.from_flask(self._flask_client.post(url, json=data))
        return _AdminResponse.from_requests(self._session.post(url, json=data))

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
        """Ensure the default test tenant exists."""
        self._ensure_tenant_for_id(self._tenant_id)

    def _ensure_tenant_for_id(self, tenant_id: str) -> None:
        """Ensure a tenant with the given ID exists in the database."""
        with get_db_session() as session:
            from sqlalchemy import select

            existing = session.scalars(select(Tenant).where(Tenant.tenant_id == tenant_id)).first()
            if not existing:
                tenant = create_tenant_with_timestamps(
                    tenant_id=tenant_id,
                    name=f"BDD Test Tenant {tenant_id}",
                    subdomain=f"bdd-{tenant_id}".replace("_", "-"),
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
