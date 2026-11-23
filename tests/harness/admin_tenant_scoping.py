"""Admin tenant-scoping harness for the routes of prebid/salesagent#2203 and #2204.

Drives ``GET /api/tenant/<tenant_id>/revenue-chart``, ``GET /api/tenant/<tenant_id>/products``,
``GET /api/tenant/<tenant_id>/products/suggestions``, ``GET,POST /tenant/<tenant_id>/policy/rules``
and ``POST /auth/oidc/test/<tenant_id>`` (#2203), the six GAM reporting routes of
``src/admin/blueprints/gam_reporting_api.py`` (#2204), and ``GET /tenant/<tenant_id>/media-buys``,
which names products from the tenant's own catalogue, on the two admin transports ``AdminAccountEnv``
already provides — Flask ``test_client`` (integration) and ``requests.Session`` against the
Docker stack (e2e) — and seeds the tenants, memberships, catalogue and media buys the
scenarios grade.

Subclasses ``AdminAccountEnv`` rather than extracting a base: the transport plumbing is
inherited unchanged, and the harness lifecycle guard
(``tests/harness/test_harness_base.py::test_harness_envs_define_no_enter_exit``) pins the
one hand-rolled ``__enter__``/``__exit__`` home to ``AdminAccountEnv``, so a second admin
env inherits that lifecycle instead of restating it.

The caller's session is STATED on both transports (``AdminClient.state_session``), with the
keys the deployment's own (Google) sign-in writes for a caller who is not a super admin
(``member_session_payload(..., auth_method=None)``): ``user`` as a bare email and a HOME
tenant that is never the target, with no ``test_user`` key. Not an OIDC session: a sign-in
through a tenant's own SSO is bound to that tenant and reaches no other, so it would be
refused on the target before its membership row was ever read. ``require_tenant_access`` therefore takes its real branch, and the target tenant's
``User`` row is the only thing that decides the outcome: whether it exists, whether it is
active, and the role it holds. A refusal of that session is the decorator's membership or
role check, on the same branch in-process as on the stack.

The contract itself is stated once, in ``tests/bdd/features/BR-ADMIN-TENANT-SCOPING.feature``,
and runs on both transports through the ``admin-tenant-scoping`` row of ``ENV_ROUTES`` in
tests/bdd/conftest.py. The step definitions in tests/bdd/steps/domain/admin_tenant_scoping.py
read the redirect table, the buy's budget and the expected-body builders from this module;
seeding and read-backs are methods of the env.
"""

from __future__ import annotations

from contextlib import ExitStack
from decimal import Decimal
from typing import Any, NamedTuple
from urllib.parse import urlsplit

from src.core.database.database_session import get_db_session
from src.core.database.models import Tenant, UserRole
from src.services.default_products import get_default_products
from tests.factories import MediaBuyFactory, ProductFactory, TenantFactory, UserFactory
from tests.factories.mint import mint_id
from tests.harness.admin_accounts import AdminAccountEnv
from tests.harness.admin_client import AdminResponse
from tests.helpers.admin_session import cached_super_admin, member_session_payload, no_configured_super_admins
from tests.utils.database_helpers import bound_factory_session, delete_tenant, tenant_row_counts

#: The identity the scenarios log in as: a tenant user, never a super admin. Both transports
#: use the same address so they grade identical ``User`` rows.
MEMBER_EMAIL = "test_tenant_user@example.com"
#: Somebody else, who IS an active member of the target tenant in every scenario. Without
#: this row a lookup that forgot the caller's email would still find nobody, and a refusal
#: would prove nothing about whose membership was checked.
OTHER_MEMBER_EMAIL = "target_tenant_member@example.com"
#: The role a caller's active row in the target tenant holds unless the scenario names
#: another: the least one that may send every method the four routes of #2203 serve,
#: because ``role_permits`` asks ``manager`` for a write. The OIDC test route asks for
#: ``admin``, so T-ADMIN-SCOPE-011 and T-ADMIN-SCOPE-012 name the role they seed.
MEMBER_ROLE = UserRole.MANAGER
#: The role of a row the membership lookup must not match: the other member of the target
#: tenant, the caller's row in another tenant, and the caller's inactive row in the target.
#: ``admin`` passes the role check of every route in ``ROUTES``, so a lookup that matched
#: one of these rows would let the caller in and the refusal scenario would fail. Under a
#: lower role the OIDC test route's role check would refuse in the lookup's place.
NON_MATCHING_ROW_ROLE = UserRole.ADMIN
ACTIVE_BUY_BUDGET = Decimal("1500.00")
#: Any numeric id passes ``validate_numeric_id``; the handlers stop before they would use it.
GAM_ADVERTISER_ID = "12345"


class TenantScopedRoute(NamedTuple):
    path: str
    methods: tuple[str, ...]


_GAM_API = "/api/tenant/{tenant_id}"

#: The routes of #2203 and #2204, keyed by the name the Gherkin scenarios use.
ROUTES: dict[str, TenantScopedRoute] = {
    "revenue chart API": TenantScopedRoute("/api/tenant/{tenant_id}/revenue-chart", ("GET",)),
    "products API": TenantScopedRoute("/api/tenant/{tenant_id}/products", ("GET",)),
    "product suggestions API": TenantScopedRoute("/api/tenant/{tenant_id}/products/suggestions", ("GET",)),
    "policy rules page": TenantScopedRoute("/tenant/{tenant_id}/policy/rules", ("GET", "POST")),
    "OIDC test page": TenantScopedRoute("/auth/oidc/test/{tenant_id}", ("POST",)),
    "GAM reporting API": TenantScopedRoute(f"{_GAM_API}/gam/reporting", ("GET",)),
    "GAM advertiser summary API": TenantScopedRoute(
        f"{_GAM_API}/gam/reporting/advertiser/{{advertiser_id}}/summary", ("GET",)
    ),
    "GAM principal reporting API": TenantScopedRoute(f"{_GAM_API}/principals/{{principal_id}}/gam/reporting", ("GET",)),
    "GAM country breakdown API": TenantScopedRoute(f"{_GAM_API}/gam/reporting/countries", ("GET",)),
    "GAM ad unit breakdown API": TenantScopedRoute(f"{_GAM_API}/gam/reporting/ad-units", ("GET",)),
    "GAM principal summary API": TenantScopedRoute(
        f"{_GAM_API}/principals/{{principal_id}}/gam/reporting/summary", ("GET",)
    ),
    "media buys page": TenantScopedRoute("/tenant/{tenant_id}/media-buys", ("GET",)),
}

#: Where a scenario may say a route redirected to, keyed by the name the Gherkin uses. The
#: login page is the TENANT's, not the generic ``/login`` ``require_auth`` sends to; the
#: policy page is where ``policy.rules`` sends an authorized member, and the settings page
#: is where ``oidc.test_initiate`` sends one when the tenant has no OIDC configuration.
REDIRECTS: dict[str, str] = {
    "login page": "/tenant/{tenant_id}/login",
    "policy page": "/tenant/{tenant_id}/policy/",
    "settings page": "/tenant/{tenant_id}/settings",
}


class TargetTenant(NamedTuple):
    tenant_id: str
    principal_id: str
    principal_name: str
    products_payload: list[dict[str, str]]
    media_buy_id: str
    #: The product id the buy's request packages name: one the tenant's own catalogue does not hold.
    media_buy_product_id: str


class TenantState(NamedTuple):
    """What a request can change for a tenant: how many rows it holds per table, and its policy settings."""

    row_counts: dict[str, int]
    policy_settings: Any


def _seed_member(tenant: Tenant, email: str, *, role: UserRole, is_active: bool = True) -> None:
    """A ``User`` row for *email* in *tenant*, holding *role*. Call it inside ``bound_factory_session()``."""
    UserFactory(tenant=tenant, user_id=mint_id("user"), email=email, role=role, is_active=is_active)


def redirect_path(response: AdminResponse) -> str:
    """Path of a redirect's Location, without the ``next`` query the login page carries."""
    return urlsplit(response.headers["Location"]).path


#: The order today's handler emits get_default_products() in when no query parameters are
#: sent (sorted by industry-specificity, average CPM, then format count). Pinned as a literal
#: so the expectation does not re-implement the sort it grades.
_DEFAULT_SUGGESTION_ORDER = (
    "run_of_site_display",
    "contextual_display",
    "video_preroll",
    "native_infeed",
    "mobile_interstitial",
    "homepage_takeover",
)


def expected_suggestions_body() -> dict[str, Any]:
    """Today's body of ``GET .../products/suggestions`` with no query parameters.

    Holds for a tenant whose catalogue shares no id with the default products (the seeded
    product id is unique), so every suggestion is new, industry-neutral and unboosted.
    """
    by_id = {product["product_id"]: product for product in get_default_products()}
    assert set(by_id) == set(_DEFAULT_SUGGESTION_ORDER), sorted(by_id)
    suggestions = [
        {**by_id[product_id], "already_exists": False, "is_industry_specific": False, "match_score": 100}
        for product_id in _DEFAULT_SUGGESTION_ORDER
    ]
    return {
        "suggestions": suggestions,
        "total_count": len(suggestions),
        "criteria": {"industry": None, "delivery_type": None, "max_cpm": None, "formats": []},
    }


class AdminTenantScopingEnv(AdminAccountEnv):
    """Test environment for the tenant-scoping scenarios of #2203 and #2204.

    Same two transports as ``AdminAccountEnv``; adds the target tenant, the three caller
    memberships (elsewhere / inactive here / active here, in the role the scenario names),
    a super admin of another tenant, and the routes in ``ROUTES``.
    """

    def __init__(self, *, mode: str = "integration", base_url: str | None = None) -> None:
        super().__init__(mode=mode, base_url=base_url)
        self._target: TargetTenant | None = None
        self._state_before_request: TenantState | None = None
        self._cleanups = ExitStack()

    def _setup(self) -> None:
        """No ambient super-admin grant for the member identity, on the app in this process."""
        super()._setup()
        self._cleanups.enter_context(no_configured_super_admins())

    def _cleanup(self) -> None:
        """Delete the tenants this env named through ``_new_tenant_id``, with their rows, and restore the settings.

        The e2e stack's database outlives the scenario: ``_reset_e2e_db`` in
        tests/bdd/conftest.py empties it before the next e2e scenario, and no fixture
        empties it after the last one.
        """
        with self._cleanups:
            super()._cleanup()

    def _new_tenant_id(self, prefix: str) -> str:
        """A unique tenant id whose deletion is registered before the tenant exists."""
        tenant_id = mint_id(prefix)
        self._cleanups.callback(delete_tenant, tenant_id)
        return tenant_id

    # ── Target tenant ─────────────────────────────────────────────────────

    @property
    def target(self) -> TargetTenant:
        assert self._target is not None, "seed_target_tenant() has not run"
        return self._target

    def seed_target_tenant(self) -> TargetTenant:
        """A tenant with one catalogue product, one active media buy and an active member who is not the caller."""
        with bound_factory_session():
            tenant = TenantFactory(tenant_id=self._new_tenant_id("scope"))
            product = ProductFactory(tenant=tenant, product_id=mint_id("prod"))
            buy = MediaBuyFactory(
                tenant=tenant,
                media_buy_id=mint_id("mb"),
                status="active",
                budget=ACTIVE_BUY_BUDGET,
                # Only the principal id is set: PrincipalFactory derives token_hash/token_prefix
                # from it (tests/factories/principal.py), so a unique id is a unique credential.
                principal__principal_id=mint_id("principal"),
            )
            _seed_member(tenant, OTHER_MEMBER_EMAIL, role=NON_MATCHING_ROW_ROLE)
            products_payload = [
                {
                    "product_id": product.product_id,
                    "name": product.name,
                    "description": product.description,
                    "delivery_type": product.delivery_type,
                }
            ]
            (package,) = buy.raw_request["packages"]
            assert package["product_id"] != product.product_id, "the buy must name a product id the tenant lacks"
            self._target = TargetTenant(
                tenant.tenant_id,
                buy.principal.principal_id,
                buy.principal.name,
                products_payload,
                buy.media_buy_id,
                package["product_id"],
            )
        return self._target

    def seed_product_of_other_tenant(self, name: str) -> None:
        """Another tenant's product named *name*, under the product id the target tenant's media buy names."""
        with bound_factory_session():
            other = TenantFactory(tenant_id=self._new_tenant_id("other"))
            ProductFactory(tenant=other, product_id=self.target.media_buy_product_id, name=name)

    def target_state(self) -> TenantState:
        """The target tenant's state now, read from the database."""
        with get_db_session() as session:
            tenant = session.get(Tenant, self.target.tenant_id)
            assert tenant is not None
            return TenantState(tenant_row_counts(session, tenant.tenant_id), tenant.policy_settings)

    @property
    def state_before_request(self) -> TenantState:
        """The target tenant's state as ``send()`` found it; compare ``target_state()`` against it after a rejection."""
        assert self._state_before_request is not None, "send() has not run"
        return self._state_before_request

    # ── Caller memberships ────────────────────────────────────────────────

    def login_as_member_of_other_tenant(self) -> None:
        """Active ``User`` row in a fresh home tenant, no row in the target."""
        self._authenticate_member(self._new_home_tenant(with_member=True))

    def login_with_target_membership(self, *, is_active: bool, role: UserRole = MEMBER_ROLE) -> None:
        """A ``User`` row in the TARGET tenant with ``is_active`` and *role* as given; session held via a fresh home tenant."""
        home_tenant_id = self._new_home_tenant(with_member=False)
        with bound_factory_session() as session:
            target = session.get(Tenant, self.target.tenant_id)
            _seed_member(target, MEMBER_EMAIL, is_active=is_active, role=role)
        self._authenticate_member(home_tenant_id)

    def login_as_super_admin_of_other_tenant(self) -> None:
        """A super admin signed in at a fresh home tenant, with no row in the target (#2204, AC4 as amended).

        The session carries the keys ``is_super_admin()`` caches after a configured super
        admin's first check (``cached_super_admin``), so the grant reads the same on both
        transports without touching the stack's ``SUPER_ADMIN_EMAILS``. It is the deployment's
        own (Google) sign-in, as every member session here is: a session from a tenant's SSO
        is bound to that tenant and is never a super admin (``session_is_tenant_scoped``).
        """
        home_tenant_id = self._new_home_tenant(with_member=True)
        payload = member_session_payload(MEMBER_EMAIL, home_tenant_id, auth_method=None)
        self.client.state_session({**payload, **cached_super_admin(MEMBER_EMAIL)})

    def _new_home_tenant(self, *, with_member: bool) -> str:
        """A tenant the session is logged in against. Never the target, so the bypass cannot fire."""
        with bound_factory_session():
            home = TenantFactory(tenant_id=self._new_tenant_id("home"))
            if with_member:
                _seed_member(home, MEMBER_EMAIL, role=NON_MATCHING_ROW_ROLE)
            return home.tenant_id

    def _authenticate_member(self, login_tenant_id: str) -> None:
        """State a tenant user's session for ``MEMBER_EMAIL`` against ``login_tenant_id`` (never the target)."""
        self.client.state_session(member_session_payload(MEMBER_EMAIL, login_tenant_id, auth_method=None))

    # ── Requests ──────────────────────────────────────────────────────────

    def send(self, method: str, route_name: str, *, asks_for_json: bool = False) -> AdminResponse:
        """One request to *route_name* of the target tenant.

        API callers send ``Accept: application/json``. *asks_for_json* sends that header
        on a page route too, so a refusal states its reason instead of rendering the 403 page.
        """
        route = ROUTES[route_name]
        assert method in route.methods, f"{route_name} does not serve {method}"
        path = route.path.format(
            tenant_id=self.target.tenant_id, principal_id=self.target.principal_id, advertiser_id=GAM_ADVERTISER_ID
        )
        # Read here, not at seeding: a Given after the Background adds the caller's own row.
        self._state_before_request = self.target_state()
        return self.client.request(
            method,
            path,
            data={} if method == "POST" else None,
            headers={"Accept": "application/json"} if asks_for_json or path.startswith("/api/") else None,
        )
