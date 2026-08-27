"""Admin account management harness for BDD tests.

Provides two transports for admin account management BDD scenarios:
- **integration**: Flask test_client (in-process, no Docker)
- **e2e**: requests.Session against the live stack (full deployment)

The transport and, for e2e, the server address are TOLD to this env by its
caller — they are never inferred from the environment. The
BDD parametrization picks the transport at collection time and the ``e2e_stack``
fixture supplies the address; both arrive as arguments.

"""

from __future__ import annotations

import logging
from enum import StrEnum
from typing import Any

from sqlalchemy import delete

from src.core.database.database_session import get_db_session
from src.core.database.models import Account, Tenant
from tests.utils.database_helpers import create_tenant_with_timestamps

logger = logging.getLogger(__name__)


class AdminTransport(StrEnum):
    """The two transports BR-ADMIN-ACCOUNTS.feature declares for admin scenarios.

    Deliberately NOT members of ``tests.harness.transport.Transport``:
    ``TRANSPORT_PROTOCOL`` maps every ``Transport`` member to an AdCP
    ``ResolvedIdentity.protocol`` consumed by ``_base.call_via``, and the admin
    UI is an HTML form surface with no AdCP protocol — a member there would have
    to be given a fabricated one, which is a lie in the AdCP enum rather than a
    naming inconvenience. Because ``StrEnum`` members ARE ``str``, a value that
    leaks into ``dispatch_request`` misses ``transport_map`` and raises
    "unrecognized wire transport" loudly instead of dispatching somewhere wrong.

    The ``e2e_`` prefix on ``E2E`` is load-bearing: the ``ctx`` fixture stashes
    ``e2e_config`` (and hard-errors on an unreachable stack) for any param whose
    value starts with it, and ``_outcome_helpers.is_e2e()`` keys on the same
    prefix.
    """

    INTEGRATION = "admin_integration"  # Flask test_client, in-process
    E2E = "e2e_admin"  # requests.Session against the live stack


class _CaseInsensitiveHeaders(dict):
    """Header mapping that looks up regardless of case, on either transport.

    HTTP header names are case-insensitive (RFC 9110 §5.1), and the two
    transports genuinely differ: werkzeug hands back canonical ``Location``,
    while the live server emits lowercase ``location`` (ASGI normalizes header
    names). Both source objects model that correctly — werkzeug ``Headers`` and
    requests ``CaseInsensitiveDict`` — but ``dict(response.headers)`` threw the
    property away, so ``headers.get("Location")`` silently returned "" over real
    HTTP and every redirect assertion read as "no redirect happened".
    Six BR-ADMIN-ACCOUNTS scenarios failed on this the first
    time they ran over the wire.
    """

    def __init__(self, headers: Any) -> None:
        super().__init__({str(k).lower(): v for k, v in dict(headers).items()})

    def get(self, key: str, default: Any = None) -> Any:
        return super().get(key.lower(), default)

    def __getitem__(self, key: str) -> Any:
        return super().__getitem__(key.lower())

    def __contains__(self, key: object) -> bool:
        return super().__contains__(str(key).lower())


class _AdminResponse:
    """Unified response wrapper for Flask test_client and requests.Session.

    Normalizes the response interface so step definitions don't need to
    know which transport is active.
    """

    def __init__(self, status_code: int, data: bytes, headers: Any, json_data: Any = None) -> None:
        self.status_code = status_code
        self._data = data
        self.headers = _CaseInsensitiveHeaders(headers)
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
    - ``e2e``: requests.Session against the live stack
    """

    DEFAULT_TENANT_ID = "bdd_admin_tenant"

    def __init__(self, *, mode: str = "integration", tenant_id: str | None = None, base_url: str | None = None) -> None:
        """
        Args:
            mode: ``integration`` (Flask test_client) or ``e2e`` (live stack).
                Defaults to the in-process transport; there is no auto-detection.
                An env that guesses its own transport from a
                process-global cannot tell "my caller wants e2e" from "the
                container exports a port for unrelated reasons", and a global
                cannot carry a different address per xdist worker at all.
            tenant_id: Tenant to operate on. Defaults to ``DEFAULT_TENANT_ID``.
                Pass an explicit id to drive the admin surface for a tenant some
                OTHER env already seeded — e.g. pairing with ``AccountSyncEnv`` to
                check that an admin edit does not orphan an account from the
                buyer's sync (salesagent-8sfr). ``_ensure_tenant_for_id`` already
                handled arbitrary ids; this just exposes it at construction.
            base_url: Where the live server is, e.g.
                ``http://myproj-server-gw2:8080``. REQUIRED for ``mode="e2e"``
                and meaningless otherwise. Supplied by whoever knows it — under
                BDD that is ``e2e_stack``, which synthesises a per-worker address.
        """
        if mode not in ("integration", "e2e"):
            raise ValueError(f"mode must be 'integration' or 'e2e', got {mode!r}")
        if mode == "e2e" and not base_url:
            raise ValueError(
                "mode='e2e' requires base_url — the caller knows the address, this env does not discover it"
            )

        self._mode = mode

        # Integration mode: Flask app + test_client
        self._app: Any = None
        self._flask_client: Any = None

        # E2E mode: requests.Session
        self._session: Any = None
        self._base_url: str = base_url or ""

        self._tenant_id: str = tenant_id or self.DEFAULT_TENANT_ID
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
        """Set up requests.Session for e2e transport against the live stack.

        ``base_url`` was supplied at construction — this env resolves no
        addresses of its own.
        """
        import requests

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
        from tests.helpers.admin_session import admin_auth_session

        admin_auth_session(self._flask_client, tenant_id)

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
        """POST the create account form, recording any row it created for cleanup."""
        before = self._account_ids_in_tenant()
        response = self._post_form(self._url("create"), form_data)
        self._created_account_ids.extend(self._account_ids_in_tenant() - before)
        return response

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
        """Seed a test account through the production write path. Returns account_id.

        Goes through ``AccountUoW`` -> ``AccountRepository.create()`` rather than a
        raw ``session.add``, so a harness-seeded row obeys the same invariants a
        production-created one does — above all the natural-key collision refusal in
        ``_require_natural_key_free``. Seeding straight into the table would leave
        this helper as the one seam through which a test could establish a state
        production forbids (two accounts on one natural key), and a test that asserts
        on an impossible state proves nothing.

        Propagates ``NaturalKeyConflict`` deliberately: a scenario that seeds a
        duplicate key should fail loudly here rather than quietly produce a database
        the buyer's ``sync_accounts`` could never have created.
        """
        import uuid

        from src.core.database.repositories.uow import AccountUoW
        from tests.factories.account import AccountFactory

        account_id = f"acc_{uuid.uuid4().hex[:12]}"

        with AccountUoW(self._tenant_id) as uow:
            assert uow.accounts is not None
            account = AccountFactory.build(
                tenant_id=self._tenant_id,
                account_id=account_id,
                name=name,
                status=status,
                brand={"domain": brand_domain} if brand_domain else None,
                operator=operator,
                billing=billing,
                payment_terms=payment_terms,
            )
            uow.accounts.create(account)

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

    def accounts_on_natural_key(self, *, domain: str, operator: str) -> list[Account]:
        """Accounts a natural key resolves to, through the production query.

        Uses ``AccountRepository.list_by_natural_key`` — the same call
        ``_resolve_by_natural_key`` makes for ambiguity detection — so a scenario
        asserting on the key grades what a buyer's ``sync_accounts`` entry would
        actually see. ``limit`` is above production's 2 so a failure message can
        report how many rows really collide.
        """
        from src.core.database.repositories.account import AccountRepository

        with get_db_session() as session:
            repo = AccountRepository(session, self._tenant_id)
            accounts = repo.list_by_natural_key(operator=operator, brand_domain=domain, limit=5)
            for account in accounts:
                session.expunge(account)
            return accounts

    def accounts_with_brand_domain(self, domain: str) -> list[Account]:
        """Accounts in this tenant whose brand carries ``domain``.

        Owned by the env rather than read with a raw session in the step, so the
        lookup follows whichever DB the transport selected.
        """
        from sqlalchemy import select

        with get_db_session() as session:
            accounts = session.scalars(select(Account).where(Account.tenant_id == self._tenant_id)).all()
            matches = [a for a in accounts if a.brand and a.brand.domain == domain]
            for account in matches:
                session.expunge(account)
            return matches

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
        """Remove the accounts THIS env created, by id.

        Deliberately id-scoped rather than "everything in the tenant". A
        tenant-wide DELETE deadlocks: the integration tests pair this env with
        ``AccountSyncEnv``, whose session still holds an open transaction on
        those rows when this one exits first, so the DELETE blocks on its locks
        and the suite hangs rather than fails. Ids also keep this env from
        deleting rows another env owns.

        ``_created_account_ids`` covers both ways a row appears: seeded through
        ``create_account`` and created by POSTing the real admin form (recorded
        in ``post_create``). Form-created rows used to survive the scenario,
        which was harmless while a natural key could hold any number of accounts
        — since salesagent-0njj a leaked row OCCUPIES its key, and the next
        scenario creating the same brand+operator would be refused by a
        collision it did not cause.
        """
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

    def _account_ids_in_tenant(self) -> set[str]:
        """Committed account ids for this tenant.

        A read, so it never blocks on another env's uncommitted writes — and by
        the same token it does not see them, which is what keeps ``post_create``
        from claiming rows this env did not create.
        """
        from sqlalchemy import select

        with get_db_session() as session:
            return set(session.scalars(select(Account.account_id).where(Account.tenant_id == self._tenant_id)).all())
