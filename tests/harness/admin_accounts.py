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

from enum import StrEnum

from sqlalchemy import delete

from src.core.database.database_session import get_db_session
from src.core.database.models import Account, Tenant
from tests.harness.admin_client import AdminClient, AdminResponse
from tests.utils.database_helpers import create_tenant_with_timestamps


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
        self._base_url: str = base_url or ""
        self._client: AdminClient | None = None

        self._tenant_id: str = tenant_id or self.DEFAULT_TENANT_ID
        self._created_account_ids: list[str] = []

    @property
    def mode(self) -> str:
        """Current transport mode: 'integration' or 'e2e'."""
        return self._mode

    def __enter__(self) -> AdminAccountEnv:
        # Not a BaseTestEnv, so it keeps its own __enter__ — but it owes the same
        # guarantee. Python does not call __exit__ when __enter__ raises, so a
        # failure in _ensure_tenant would strand the admin client for the rest of
        # the process. __exit__ is None-safe, so calling it here is what releases it.
        try:
            self._client = AdminClient(self._base_url if self._mode == "e2e" else None)
            self._ensure_tenant()
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *exc: object) -> None:
        self._cleanup_accounts()
        if self._client is not None:
            self._client.close()
            self._client = None

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    @property
    def client(self) -> AdminClient:
        """The admin client this env drives, open between ``__enter__`` and ``__exit__``."""
        assert self._client is not None, "AdminAccountEnv is not entered"
        return self._client

    # ── Auth ──────────────────────────────────────────────────────────────

    def authenticate(self, tenant_id: str | None = None) -> None:
        """Set up authenticated admin session."""
        self.client.authenticate(tenant_id or self._tenant_id)

    def clear_auth(self) -> None:
        """Clear the authenticated session."""
        self.client.clear_auth()

    # ── Routes ────────────────────────────────────────────────────────────

    def _url(self, path: str = "") -> str:
        return f"/tenant/{self._tenant_id}/accounts/{path}"

    def get_list_page(self, status_filter: str | None = None) -> AdminResponse:
        """GET the accounts list page."""
        url = self._url()
        if status_filter:
            url += f"?status={status_filter}"
        return self.client.request("get", url)

    def get_create_page(self) -> AdminResponse:
        """GET the create account form."""
        return self.client.request("get", self._url("create"))

    def post_create(self, form_data: dict[str, str]) -> AdminResponse:
        """POST the create account form, recording any row it created for cleanup."""
        before = self._account_ids_in_tenant()
        response = self.client.request("post", self._url("create"), data=form_data)
        self._created_account_ids.extend(self._account_ids_in_tenant() - before)
        return response

    def get_detail_page(self, account_id: str) -> AdminResponse:
        """GET the account detail page."""
        return self.client.request("get", self._url(account_id))

    def get_edit_page(self, account_id: str) -> AdminResponse:
        """GET the account edit form."""
        return self.client.request("get", self._url(f"{account_id}/edit"))

    def post_edit(self, account_id: str, form_data: dict[str, str]) -> AdminResponse:
        """POST the account edit form."""
        return self.client.request("post", self._url(f"{account_id}/edit"), data=form_data)

    def post_status_change(self, account_id: str, new_status: str) -> AdminResponse:
        """POST a status change via JSON API."""
        return self.client.request("post", self._url(f"{account_id}/status"), json={"status": new_status})

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
        ``_find_natural_key_conflict``. Seeding straight into the table would leave
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
        from tests.factories.mint import mint

        account_id = mint(f"acc_{uuid.uuid4().hex[:12]}")

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
        in ``post_create``). A form-created row surviving the scenario OCCUPIES its
        natural key, so the next scenario creating the same brand+operator would be
        refused by a collision it did not cause.
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
