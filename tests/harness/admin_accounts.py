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

from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from enum import StrEnum
from typing import Any, NamedTuple

from sqlalchemy import delete
from sqlalchemy.orm import Session

from src.core.database.database_session import get_db_session
from src.core.database.models import Account, MediaBuy, Product, Tenant
from tests.harness.admin_client import AdminClient, AdminResponse
from tests.helpers.admin_session import admin_session_payload
from tests.utils.database_helpers import bind_factories_to_session, create_tenant_with_timestamps


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


class PendingApproval(NamedTuple):
    """The ids of a media buy awaiting the seller's approval, and of its approval step."""

    tenant_id: str
    media_buy_id: str
    context_id: str
    step_id: str


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
        #: (trigger name, table) of every fault :meth:`fail_writes_of` installed.
        self._database_faults: list[tuple[str, str]] = []
        #: The in-process scripted model origin (:meth:`script_ai_connection_test`), once started.
        self._llm_origin: str | None = None
        #: What ``__exit__`` closes besides the client: the in-process origin and its variable,
        #: and every deployment setting a Given patched onto this process's settings.
        self._in_process_cleanups = ExitStack()

    @property
    def mode(self) -> str:
        """Current transport mode: 'integration' or 'e2e'."""
        return self._mode

    def __enter__(self) -> AdminAccountEnv:
        # Not a BaseTestEnv, so it keeps its own __enter__ — but it owes the same
        # guarantee. Python does not call __exit__ when __enter__ raises, so a
        # failure in _setup would strand the admin client for the rest of
        # the process. __exit__ is None-safe, so calling it here is what releases it.
        try:
            self._client = AdminClient(self._base_url if self._mode == "e2e" else None)
            self._setup()
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *exc: object) -> None:
        try:
            self._cleanup()
        finally:
            if self._client is not None:
                self._client.close()
                self._client = None

    def _setup(self) -> None:
        """Put in place what the env needs before its first step, once the client is open.

        A subclass extends this, and releases whatever it acquires here in ``_cleanup``,
        which also runs when this raises part-way.
        """
        self._ensure_tenant()

    def _cleanup(self) -> None:
        """Release what ``_setup`` and the scenario's steps left behind."""
        self._in_process_cleanups.close()
        self._llm_origin = None
        self._drop_database_faults()
        self._cleanup_accounts()

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    @property
    def client(self) -> AdminClient:
        """The admin client this env drives, open between ``__enter__`` and ``__exit__``."""
        assert self._client is not None, "AdminAccountEnv is not entered"
        return self._client

    # ── Auth ──────────────────────────────────────────────────────────────

    def authenticate(self, tenant_id: str | None = None, *, auth_method: str | None = None) -> None:
        """Set up authenticated admin session; *auth_method* ``oidc`` states an SSO sign-in."""
        self.client.authenticate(tenant_id or self._tenant_id, auth_method=auth_method)

    def authenticate_as_tenant_user(self, email: str) -> None:
        """Make *email* an active admin user of this tenant, unless it is one, and state its session.

        Not a super admin, so ``require_tenant_access`` reaches the route through the
        tenant's ``users`` row, the path every tenant's own operators take.
        """
        from sqlalchemy import select

        from src.core.database.models import User
        from tests.factories import UserFactory

        with self._seeding() as (session, tenant):
            if session.scalars(select(User).filter_by(tenant_id=self._tenant_id, email=email)).first() is None:
                UserFactory(tenant=tenant, email=email, role="admin")
        self.client.authenticate(self._tenant_id, tenant_user=email)

    def authenticate_as_super_admin_checked_at(self, email: str, checked_at: float | None) -> None:
        """State the session a sign-in as *email* left, whose super-admin answer was cached at *checked_at*.

        *email* need be on no configured list: until the cache is older than
        ``SUPER_ADMIN_RECHECK_SECONDS`` the session alone says it is a super admin.
        """
        self.client.state_session(admin_session_payload(self._tenant_id, email=email, checked_at=checked_at))

    def clear_auth(self) -> None:
        """Clear the authenticated session."""
        self.client.clear_auth()

    # ── The deployment ────────────────────────────────────────────────────
    #
    # A fact the live stack's server already has (docker-compose.e2e.yml sets it on
    # adcp-server, and the feature names the same value, so the two change together) is
    # patched onto this process's settings only, which the app reads on each request.

    def _patch_in_process_setting(self, field: str, value: object) -> None:
        if self._mode == "e2e":
            return
        from unittest.mock import patch

        from src.core.config import get_settings

        self._in_process_cleanups.enter_context(patch.object(get_settings().runtime, field, value))

    def name_super_admin_domain(self, domain: str) -> None:
        """Make *domain* the deployment's ``SUPER_ADMIN_DOMAIN``."""
        self._patch_in_process_setting("super_admin_domain", domain)

    def serve_many_tenants(self) -> None:
        """Make this a multi-tenant deployment (``ADCP_MULTI_TENANT``), which lists its tenants to a super admin."""
        self._patch_in_process_setting("adcp_multi_tenant", True)

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
        return self._recording_new_accounts(lambda: self.client.request("post", self._url("create"), data=form_data))

    def _recording_new_accounts(self, send: Callable[[], AdminResponse]) -> AdminResponse:
        """*send* a request, and record every account it created in this tenant for cleanup."""
        before = self._account_ids_in_tenant()
        response = send()
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

    # ── Super-admin forms and APIs that answer with an operator refusal ──

    #: The create-tenant form's page, which is also where the form posts.
    CREATE_TENANT_PAGE = "/create_tenant"

    def submit_create_tenant(
        self,
        form_data: dict[str, str],
        *,
        from_page: str | None,
        browser_headers: dict[str, str | None] | None = None,
    ) -> AdminResponse:
        """POST the create-tenant form as a browser does from *from_page*, or from nowhere.

        *from_page* and *browser_headers* are as :meth:`AdminClient.submit` takes them.
        """
        return self.client.submit(
            self.CREATE_TENANT_PAGE, form_data, from_page=from_page, browser_headers=browser_headers
        )

    def submit_create_account(self, form_data: dict[str, str], *, from_create_page: bool) -> AdminResponse:
        """POST the create-account form as a browser does, recording any row it created for cleanup."""
        path = self._url("create")
        return self._recording_new_accounts(
            lambda: self.client.submit(path, form_data, from_page=path if from_create_page else None)
        )

    def get_recent_activities(self) -> AdminResponse:
        """GET this tenant's recent activity as the activity feed's fallback poll reads it."""
        return self.client.request(
            "get", f"/tenant/{self._tenant_id}/activities", headers={"Accept": "application/json"}
        )

    def submit_create_product(self, form_data: dict[str, Any]) -> AdminResponse:
        """POST the add-product form as a browser does from its page."""
        path = self.products_path("add")
        return self.client.submit(path, form_data, from_page=path)

    def submit_product_edit(self, tenant_id: str, product_id: str, form_data: dict[str, Any]) -> AdminResponse:
        """POST *tenant_id*'s product *product_id* edit form as a browser does from its page."""
        path = f"/tenant/{tenant_id}/products/{product_id}/edit"
        return self.client.submit(path, form_data, from_page=path)

    def submit_inventory_profile_add(self, form_data: dict[str, Any]) -> AdminResponse:
        """POST the add-profile form as a browser does from the inventory page, and follow the answer."""
        return self.client.submit(
            self.inventory_profiles_path("add"), form_data, from_page=f"/tenant/{self._tenant_id}/inventory"
        )

    def submit_inventory_profile_edit(self, profile_id: str, form_data: dict[str, Any]) -> AdminResponse:
        """POST *profile_id*'s edit form as a browser does from its page, and follow the answer."""
        path = self.inventory_profiles_path(f"{self.inventory_profile_row_id(profile_id)}/edit")
        return self.client.submit(path, form_data, from_page=path)

    def delete_inventory_profile_row(self, row_id: int) -> AdminResponse:
        """DELETE the profile stored under *row_id*, as the list page's delete button does with fetch()."""
        return self.client.request(
            "delete",
            self.inventory_profiles_path(f"{row_id}/delete"),
            headers={"Content-Type": "application/json"},
        )

    def submit_inventory_profile_delete(self, profile_id: str) -> AdminResponse:
        """POST *profile_id*'s delete as a browser form does from the profiles list, and follow the answer."""
        path = self.inventory_profiles_path(f"{self.inventory_profile_row_id(profile_id)}/delete")
        return self.client.submit(path, {}, from_page=self.inventory_profiles_path())

    def start_inventory_sync(self) -> AdminResponse:
        """POST an inventory sync, as the settings page's sync button does with fetch()."""
        return self.client.request(
            "post", f"/api/tenant/{self._tenant_id}/inventory/sync", json={"mode": "incremental"}
        )

    def start_inventory_sync_as_script(self, tenant_id: str, accept: str | None) -> AdminResponse:
        """POST an inventory sync of *tenant_id* as a script does: no body, and *accept* (``None``: no ``Accept``)."""
        return self.client.request("post", f"/api/tenant/{tenant_id}/inventory/sync", headers={"Accept": accept})

    def save_adapter_config_as_script(self, accept: str | None) -> AdminResponse:
        """POST this tenant's adapter config as a script does: no body, and *accept* (``None``: no ``Accept``)."""
        return self.client.request("post", f"/api/tenant/{self._tenant_id}/adapter-config", headers={"Accept": accept})

    def create_gam_service_account(self, tenant_id: str | None = None) -> AdminResponse:
        """POST a GAM service account for *tenant_id* (this env's by default), as the settings page does with fetch()."""
        return self.client.request(
            "post",
            f"/tenant/{tenant_id or self._tenant_id}/gam/create-service-account",
            headers={"Content-Type": "application/json"},
        )

    def detect_gam_network(self, tenant_id: str, refresh_token: str) -> AdminResponse:
        """POST *refresh_token* to *tenant_id*'s GAM network detection, as the settings page does with fetch()."""
        return self.client.request(
            "post", f"/tenant/{tenant_id}/gam/detect-network", json={"refresh_token": refresh_token}
        )

    def post_adapter_update(self, tenant_id: str, body: dict[str, Any]) -> AdminResponse:
        """POST *body* to *tenant_id*'s adapter choice, as the settings page does with fetch()."""
        return self.client.request("post", f"/tenant/{tenant_id}/settings/adapter", json=body)

    def post_adapter_config(self, body: dict[str, Any]) -> AdminResponse:
        """POST this tenant's adapter connection config, as the adapter settings page does with fetch()."""
        return self.client.request("post", f"/api/tenant/{self._tenant_id}/adapter-config", json=body)

    def post_oidc_config(self, body: dict[str, str]) -> AdminResponse:
        """POST this tenant's OIDC configuration, as the users page does with fetch()."""
        return self.client.request("post", f"/auth/oidc/tenant/{self._tenant_id}/config", json=body)

    def post_management_api_tenant(self, body: dict[str, str]) -> AdminResponse:
        """POST *body* to the tenant management API's create route, with a key it accepts."""
        return self.client.request(
            "post",
            "/api/v1/tenant-management/tenants",
            json=body,
            headers={"X-Tenant-Management-API-Key": self._stored_api_key("tenant_management_api_key")},
        )

    def _stored_api_key(self, config_key: str) -> str:
        """Store the digest of a fixed key under *config_key*, the way production stores one, and return the key.

        Fixed rather than minted, so two scenarios storing it at once store the same digest.
        """
        from src.core.credentials import hash_token, token_prefix
        from src.core.database.repositories.tenant_management_config import TenantManagementConfigRepository

        key = f"sk-bdd-{config_key.replace('_', '-')}"
        with get_db_session() as session:
            TenantManagementConfigRepository(session).store_api_key(
                config_key, digest=hash_token(key), prefix=token_prefix(key), description="BDD admin scenarios"
            )
            session.commit()
        return key

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
        from src.core.database.repositories.uow import AccountUoW
        from tests.factories.account import AccountFactory
        from tests.factories.mint import mint_id

        account_id = mint_id("acc")

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

    # ── Faults ────────────────────────────────────────────────────────────

    def fail_writes_of(self, table: str, *, column: str, value: str, message: str) -> None:
        """Make the database refuse to write the *table* row whose *column* is *value*, raising *message*.

        A ``BEFORE INSERT OR UPDATE`` trigger, so the fault is real on both transports: the
        route's own write fails in the database this env seeds, which the live server shares,
        and nothing in ``src/`` knows a test is running. Scoped to one row, because other
        scenarios write *table* in the same tenant at the same time. The trigger advances a
        sequence before it raises, and a rollback does not undo ``nextval``, so
        :meth:`database_faults_raised` shows the refusal was this one. ``__exit__`` drops it.
        """
        self.fail_writes_of_rows(table, where={column: value}, message=message)

    def fail_writes_of_rows(self, table: str, *, where: dict[str, str], message: str) -> None:
        """:meth:`fail_writes_of` for the *table* rows matching every ``column: value`` of *where*.

        For a fault one column cannot scope: a job's ``running`` write refused while its
        ``failed`` write, on the same row, is let through.
        """
        import uuid

        # Spliced into a plpgsql body: no quote, no format placeholder, no dollar quote.
        for literal in (*where, *where.values(), message):
            assert not set(literal) & set("'%$"), f"{literal!r} cannot be spliced into the trigger body"
        condition = " AND ".join(f"NEW.{column} = '{value}'" for column, value in where.items())
        name = f"bdd_fault_{uuid.uuid4().hex[:12]}"
        with get_db_session() as session:
            # exec_driver_sql, not text(): text() reads every ":word" in *message* as a bind parameter.
            connection = session.connection()
            connection.exec_driver_sql(f"CREATE SEQUENCE {name}_seq")
            connection.exec_driver_sql(
                f"CREATE FUNCTION {name}() RETURNS trigger LANGUAGE plpgsql AS $fault$\n"
                f"BEGIN\n"
                f"    IF {condition} THEN\n"
                f"        PERFORM nextval('{name}_seq');\n"
                f"        RAISE EXCEPTION '{message}';\n"
                f"    END IF;\n"
                f"    RETURN NEW;\n"
                f"END\n"
                f"$fault$"
            )
            connection.exec_driver_sql(
                f"CREATE TRIGGER {name} BEFORE INSERT OR UPDATE ON {table} FOR EACH ROW EXECUTE FUNCTION {name}()"
            )
            session.commit()
        self._database_faults.append((name, table))

    def database_faults_raised(self) -> int:
        """How many times the faults :meth:`fail_writes_of` installed have refused a write."""
        with get_db_session() as session:
            connection = session.connection()
            return sum(
                connection.exec_driver_sql(
                    f"SELECT CASE WHEN is_called THEN last_value ELSE 0 END FROM {name}_seq"
                ).scalar_one()
                for name, _ in self._database_faults
            )

    def _drop_database_faults(self) -> None:
        if not self._database_faults:
            return
        with get_db_session() as session:
            connection = session.connection()
            for name, table in self._database_faults:
                connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS {name} ON {table}")
                connection.exec_driver_sql(f"DROP FUNCTION IF EXISTS {name}()")
                connection.exec_driver_sql(f"DROP SEQUENCE IF EXISTS {name}_seq")
            session.commit()
        self._database_faults.clear()

    def inventory_profiles_path(self, path: str = "") -> str:
        """The inventory-profile routes of this tenant, which the page's modal posts to."""
        return f"/tenant/{self._tenant_id}/inventory-profiles/{path}"

    def _inventory_profile(self, profile_id: str) -> Any:
        from sqlalchemy import select

        from src.core.database.models import InventoryProfile

        with get_db_session() as session:
            stmt = select(InventoryProfile).filter_by(tenant_id=self._tenant_id, profile_id=profile_id)
            return session.scalars(stmt).first()

    def inventory_profile_format_options(self, profile_id: str) -> Any:
        """The stored declarations of *profile_id*, or None when there is no such profile."""
        profile = self._inventory_profile(profile_id)
        return None if profile is None else profile.format_options

    def inventory_profile_row_id(self, profile_id: str) -> int:
        """The integer id the edit route takes."""
        return self._inventory_profile(profile_id).id

    def authorize_publisher(self, domain: str) -> None:
        """Store *domain*'s adagents.json authorizing this tenant for one website, unless it is stored.

        The profile forms accept a publisher selector only for a publisher whose stored file
        authorizes the seller (``selector_selection``), as ``get_products`` sells it. Stored
        without a fetch, through the ingest service's apply half, as a seed stores it.
        """
        from scripts.setup.seed_products import seed_verified_website

        with get_db_session() as session:
            if seed_verified_website(session, self._tenant_id, domain):
                session.commit()

    def link_product_to_inventory_profile(self, product_id: str, profile_id: str) -> None:
        """Store a product *product_id* of this tenant that is built from the profile *profile_id*."""
        from tests.factories import ProductFactory

        row_id = self.inventory_profile_row_id(profile_id)
        with self._seeding() as (_, tenant):
            ProductFactory(tenant=tenant, product_id=product_id, inventory_profile_id=row_id)

    def seed_product(self, product_id: str, *, tenant_id: str) -> None:
        """Store a product *product_id* of *tenant_id* with one fixed CPM pricing option, as its edit form shows it."""
        from tests.factories import PricingOptionFactory, ProductFactory

        with self._seeding(tenant_id) as (_, tenant):
            product = ProductFactory(tenant=tenant, product_id=product_id)
            PricingOptionFactory(product=product, pricing_model="cpm", currency="USD", is_fixed=True)

    def onboard_gam(self, *, tenant_id: str | None = None, service_account_email: str | None = None) -> None:
        """Leave *tenant_id* (this env's tenant by default) with the rows GAM onboarding writes.

        The service account email is stored if given; GAM onboarding stores none.
        """
        from tests.utils.tenant_setup import onboard_gam

        with self._seeding(tenant_id) as (_, tenant):
            config = onboard_gam(tenant, trafficker_id="bdd_trafficker")
            config.gam_service_account_email = service_account_email

    def seed_running_inventory_sync(self, sync_id: str, *, tenant_id: str | None = None) -> None:
        """Store an inventory sync *sync_id* of *tenant_id* (this env's by default), started a moment ago, still running."""
        from tests.factories import SyncJobFactory

        with self._seeding(tenant_id) as (_, tenant):
            SyncJobFactory(tenant=tenant, sync_id=sync_id)

    def products_path(self, path: str = "") -> str:
        """The product routes of this tenant, which the product forms post to."""
        return f"/tenant/{self._tenant_id}/products/{path}"

    def agent_host(self) -> str:
        """The hostname this tenant's agent answers at (``Tenant.virtual_host_name``)."""
        from src.core.database.models import Tenant

        with get_db_session() as session:
            tenant = session.get(Tenant, self._tenant_id)
            assert tenant is not None, f"no tenant {self._tenant_id!r}"
            return tenant.virtual_host_name

    def product_is_stored(self, product_id: str) -> bool:
        """Whether this tenant holds a product *product_id*."""
        from sqlalchemy import select

        from src.core.database.models import Product

        with get_db_session() as session:
            stmt = select(Product.product_id).filter_by(tenant_id=self._tenant_id, product_id=product_id)
            return session.scalars(stmt).first() is not None

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

    @contextmanager
    def _factory_session(self) -> Iterator[Session]:
        """A session the factories write through, committed when the block exits."""
        with get_db_session() as session, bind_factories_to_session(session):
            yield session
            session.commit()

    @contextmanager
    def _seeding(self, tenant_id: str | None = None) -> Iterator[tuple[Session, Tenant]]:
        """:meth:`_factory_session`, with the tenant *tenant_id* (this env's by default) read in it."""
        with self._factory_session() as session:
            tenant = session.get(Tenant, tenant_id or self._tenant_id)
            assert tenant is not None, f"no tenant {tenant_id or self._tenant_id!r}"
            yield session, tenant

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

    # ── API callers, the business-rules fetch, sync jobs (BR-ADMIN-UNEXPECTED-ERRORS) ──

    def seed_tenant(self, *, gam: bool = False, ad_server: str | None = "mock", **columns: Any) -> str:
        """Store a tenant of its own, for a scenario whose fault or write must not reach the shared one.

        With *gam*, the tenant is onboarded to Google Ad Manager (:meth:`onboard_gam`), so the
        sync API builds its adapter without calling Google. *ad_server* is the tenant's adapter;
        ``None`` is a tenant that has chosen none. *columns* are further ``Tenant`` columns.
        Returns the tenant id.
        """
        import uuid

        from tests.factories import TenantFactory

        tenant_id = f"bdd_admin_{uuid.uuid4().hex[:10]}"
        with self._factory_session():
            TenantFactory(
                tenant_id=tenant_id,
                virtual_host=f"{tenant_id.replace('_', '-')}.example.com",
                ad_server=ad_server,
                **columns,
            )
        if gam:
            self.onboard_gam(tenant_id=tenant_id)
        return tenant_id

    def delete_tenant_by_management_api(self, tenant_id: str) -> AdminResponse:
        """DELETE *tenant_id* through the tenant management API as curl does: no body and ``Accept: */*``."""
        return self.client.request(
            "delete",
            f"/api/v1/tenant-management/tenants/{tenant_id}",
            headers={
                "Accept": "*/*",
                "X-Tenant-Management-API-Key": self._stored_api_key("tenant_management_api_key"),
            },
        )

    def post_business_rules_by_fetch(self, form_data: dict[str, str]) -> AdminResponse:
        """POST the business-rules form as the settings page's script does: its FormData through ``fetch()``."""
        return self.client.request(
            "post",
            f"/tenant/{self._tenant_id}/settings/business-rules",
            data=form_data,
            headers={"Accept": "*/*", "Sec-Fetch-Mode": "cors", "Sec-Fetch-Site": "same-origin"},
        )

    def trigger_sync_by_api(self, tenant_id: str, body: dict[str, Any]) -> AdminResponse:
        """POST *body* to the sync API's trigger for *tenant_id*, as its scripted callers do."""
        return self.client.request(
            "post", f"/api/sync/trigger/{tenant_id}", json=body, headers={"X-API-Key": self._stored_api_key("api_key")}
        )

    def sync_status_by_api(self, sync_id: str) -> AdminResponse:
        """GET the sync job *sync_id* from the sync API's status route."""
        return self.client.request(
            "get", f"/api/sync/status/{sync_id}", headers={"X-API-Key": self._stored_api_key("api_key")}
        )

    def fetch_tenant_route(self, tenant_id: str, path: str, body: dict[str, Any] | None = None) -> AdminResponse:
        """POST *body* (none: ``{}``) to *tenant_id*'s ``/tenant/<id>/<path>``, as its page's script does with fetch()."""
        return self.fetch("post", f"/tenant/{tenant_id}/{path}", body or {})

    def fetch(self, method: str, path: str, body: dict[str, Any] | None = None) -> AdminResponse:
        """Send *method* to *path* as a page's script does with fetch(): *body* as JSON, if any, and ``Accept: */*``."""
        return self.client.request(method, path, json=body, headers={"Accept": "*/*"})

    # ── Media buy approval (BR-ADMIN-OPERATOR-REFUSALS) ──

    @contextmanager
    def _approval_seeding(self) -> Iterator[Session]:
        """A session of its own for the factories, committed when the block exits.

        Not :meth:`_factory_session`: ``seed_pending_buy`` writes the approval step through
        ``ContextManager``, which opens ``get_db_session()``, and that exit closes the
        thread-scoped session :meth:`_factory_session` would still be writing through.
        """
        from src.core.database.database_session import get_engine

        session = Session(bind=get_engine())
        try:
            with bind_factories_to_session(session):
                yield session
                session.commit()
        finally:
            session.close()

    def seed_media_buy_awaiting_approval(
        self, *, accepted_image_size: tuple[int, int] | None = None
    ) -> PendingApproval:
        """A tenant of its own on the mock ad server, holding a media buy that awaits the seller's approval.

        With *accepted_image_size*, the buy's product also publishes an image format option
        accepting only that size (formats/canonical/image.json ``sizes``). Returns the seeded
        rows' ids (``seed_pending_buy``), read while their session is open: the rows detach when
        it closes.
        """
        from tests.helpers.media_buy_approval import seed_pending_buy

        with self._approval_seeding() as session:
            seeded = seed_pending_buy(starts_in_days=7)
            if accepted_image_size is not None:
                (package,) = seeded.media_buy.packages
                product = session.get(Product, (seeded.tenant_id, package.package_config["product_id"]))
                assert product is not None, "the seeded buy names no stored product"
                width, height = accepted_image_size
                option = {
                    "format_option_id": f"image_{width}x{height}",
                    "format_kind": "image",
                    "params": {"sizes": [{"width": width, "height": height}]},
                }
                product.format_options = [*(product.format_options or []), option]
            pending = PendingApproval(seeded.tenant_id, seeded.media_buy_id, seeded.context_id, seeded.step_id)
        return pending

    def assign_image_creative_to_pending_buy(
        self, pending: PendingApproval, creative_id: str, *, size: tuple[int, int]
    ) -> None:
        """Store the buyer's approved canonical image creative *creative_id* at *size*, assigned to the buy's package."""
        from tests.factories import CreativeAssignmentFactory, CreativeFactory
        from tests.factories.creative_asset import build_assets, image_spec

        width, height = size
        with self._approval_seeding() as session:
            media_buy = session.get(MediaBuy, pending.media_buy_id)
            assert media_buy is not None, f"no media buy {pending.media_buy_id!r}"
            creative = CreativeFactory(
                tenant=media_buy.tenant,
                principal=media_buy.principal,
                creative_id=creative_id,
                status="approved",
                canonical_kind="image",
                data={
                    "assets": build_assets(
                        image_spec(
                            "image_main", url=f"https://cdn.example.com/{creative_id}.png", width=width, height=height
                        )
                    )
                },
            )
            CreativeAssignmentFactory(creative=creative, media_buy=media_buy)

    def fail_ad_server_creation(self, tenant_id: str) -> None:
        """The mock ad server of *tenant_id* fails every media buy it is asked to create.

        Through the row the live server's mock adapter reads its injected behavior from
        (``AdapterConfig.config_json["test_behavior"]``, ``mock_ad_server._read_test_behavior``),
        so both admin transports reach the same failure.
        """
        from tests.factories import AdapterConfigFactory

        with self._approval_seeding() as session:
            tenant = session.get(Tenant, tenant_id)
            assert tenant is not None, f"no tenant {tenant_id!r}"
            AdapterConfigFactory(
                tenant=tenant, adapter_type="mock", config_json={"test_behavior": {"fail_on_create": True}}
            )

    def approve_workflow_step(self, pending: PendingApproval) -> AdminResponse:
        """Approve the buy's approval step as the workflow review page's script does with fetch()."""
        return self.fetch_tenant_route(
            pending.tenant_id, f"workflows/{pending.context_id}/steps/{pending.step_id}/approve"
        )

    def media_buy_status(self, media_buy_id: str) -> str:
        """The stored status of *media_buy_id*, read fresh."""
        with get_db_session() as session:
            media_buy = session.get(MediaBuy, media_buy_id)
            assert media_buy is not None, f"no media buy {media_buy_id!r}"
            return str(media_buy.status)

    def open_admin_home(self) -> AdminResponse:
        """GET ``/admin/``, the page a sign-in lands on."""
        return self.client.request("get", "/admin/")

    def submit_tenant_form(self, tenant_id: str, path: str, form_data: dict[str, str]) -> AdminResponse:
        """POST *form_data* to *tenant_id*'s ``/tenant/<id>/<path>`` as a browser's form post from no page."""
        return self.client.submit(f"/tenant/{tenant_id}/{path}", form_data)

    def authorized_domains_of(self, tenant_id: str) -> list[str]:
        """The domains *tenant_id* authorizes, as stored."""
        with get_db_session() as session:
            tenant = session.get(Tenant, tenant_id)
            assert tenant is not None, f"no tenant {tenant_id!r}"
            return list(tenant.authorized_domains)

    def post_ai_connection_test(self, body: Any) -> AdminResponse:
        """POST *body* as JSON to this tenant's AI connection test, as the settings page does with fetch()."""
        return self.client.request("post", f"/tenant/{self._tenant_id}/settings/ai/test", json=body)

    def script_ai_connection_test(self, entry: dict[str, Any]) -> str:
        """A fresh Gemini API key whose connection-test call the scripted model answers with *entry*.

        *entry* is a script entry of ``tests/e2e/scripted_llm_origin_service.py``, such as
        ``{"fail_status": 429}``. Production builds its real Gemini model and calls the origin
        the server's ``GOOGLE_GEMINI_BASE_URL`` names: over e2e the compose ``scripted-llm``
        service; in this process an origin of this env's own, which the variable is pointed at
        until ``__exit__``. Nothing in ``src/`` is replaced.
        """
        import uuid

        from tests.e2e._scripted_llm import install_script, scripted_llm_origin

        origin = scripted_llm_origin() if self._mode == "e2e" else self._in_process_llm_origin()
        api_key = f"bdd-admin-ai-{uuid.uuid4().hex[:12]}"
        install_script(api_key, origin=origin, connection_test=entry)
        return api_key

    def _in_process_llm_origin(self) -> str:
        """This env's scripted model origin, started on first use, with ``GOOGLE_GEMINI_BASE_URL`` naming it."""
        if self._llm_origin is None:
            import os
            from unittest.mock import patch

            from tests.e2e.scripted_llm_origin_service import run_scripted_llm_origin

            self._llm_origin = self._in_process_cleanups.enter_context(
                run_scripted_llm_origin(host="127.0.0.1", port=0)
            )
            self._in_process_cleanups.enter_context(
                patch.dict(os.environ, {"GOOGLE_GEMINI_BASE_URL": self._llm_origin})
            )
        return self._llm_origin
