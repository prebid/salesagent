"""Admin tenant-scoping harness for the four routes of prebid/salesagent#2203.

Drives ``GET /api/tenant/<tenant_id>/revenue-chart``, ``GET /api/tenant/<tenant_id>/products``,
``GET /api/tenant/<tenant_id>/products/suggestions`` and ``GET,POST /tenant/<tenant_id>/policy/rules``
on the two admin transports ``AdminAccountEnv`` already provides — Flask ``test_client``
(integration) and ``requests.Session`` against the Docker stack (e2e) — and seeds the
tenants, memberships, catalogue and media buys the scenarios grade.

Subclasses ``AdminAccountEnv`` rather than extracting a base: the transport plumbing is
inherited unchanged, and the harness lifecycle guard
(``tests/harness/test_harness_base.py::test_harness_envs_define_no_enter_exit``) pins the
one hand-rolled ``__enter__``/``__exit__`` home to ``AdminAccountEnv``, so a second admin
env inherits that lifecycle instead of restating it.

The caller logs in through ``/test/auth`` on both transports, always against a HOME
tenant that is never the target. That endpoint stamps ``test_tenant_id`` with the login
tenant, and ``require_tenant_access`` waives the membership lookup only when that equals
the requested tenant — logging in elsewhere is what makes the target tenant's ``User``
row the only thing that decides the outcome, so a rejection here is the real membership
check and not the test-mode bypass, and the in-process leg takes the same branch of the
decorator the stack does.

The module-level helpers (route table, case expansion, seeding, state snapshot, assertion
helpers) are shared with ``tests/admin/test_tenant_scoped_routes_auth.py`` (whose classes
``tests/e2e/test_admin_tenant_scoping_e2e.py`` reuses) so the three layers grade one contract.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from typing import Any, NamedTuple
from unittest import mock
from urllib.parse import urlsplit

from sqlalchemy.orm import Session

from src.core.database.database_session import get_db_session
from src.core.database.models import Tenant
from src.services.default_products import get_default_products
from tests.factories import MediaBuyFactory, ProductFactory, TenantFactory, UserFactory
from tests.harness.admin_accounts import AdminAccountEnv, _AdminResponse
from tests.utils.database_helpers import bound_factory_session

#: The one non-admin identity ``/test/auth`` accepts on the Docker stack. The integration
#: transport uses the same address so both transports grade identical ``User`` rows.
MEMBER_EMAIL = "test_tenant_user@example.com"
MEMBER_PASSWORD = "test123"
ACTIVE_BUY_BUDGET = Decimal("1500.00")


class TenantScopedRoute(NamedTuple):
    path: str
    methods: tuple[str, ...]
    api_mode: bool  # JSON 401/403 from require_tenant_access(api_mode=True); else redirect / abort(403)


#: The four routes of #2203, keyed by the name the Gherkin scenarios use.
ROUTES: dict[str, TenantScopedRoute] = {
    "revenue chart API": TenantScopedRoute("/api/tenant/{tenant_id}/revenue-chart", ("GET",), True),
    "products API": TenantScopedRoute("/api/tenant/{tenant_id}/products", ("GET",), True),
    "product suggestions API": TenantScopedRoute("/api/tenant/{tenant_id}/products/suggestions", ("GET",), True),
    "policy rules page": TenantScopedRoute("/tenant/{tenant_id}/policy/rules", ("GET", "POST"), False),
}


#: ``pytest.mark.parametrize`` argnames for the rows ``route_cases()`` returns.
ROUTE_CASE_PARAMS = "method,route,api_mode"


def route_cases() -> tuple[list[str], list[tuple[str, str, bool]]]:
    """``(ids, rows)`` for ``parametrize(ROUTE_CASE_PARAMS, rows, ids=ids)``: one row per route and method."""
    cases = [
        (f"{name}-{method}", (method, name, route.api_mode))
        for name, route in ROUTES.items()
        for method in route.methods
    ]
    return [case[0] for case in cases], [case[1] for case in cases]


class TargetTenant(NamedTuple):
    tenant_id: str
    principal_name: str
    products_payload: list[dict[str, str]]


def unique_id(prefix: str) -> str:
    """A collision-free id: the e2e stack DB persists across runs, so factory sequences would repeat."""
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def seed_target_tenant(session: Session) -> TargetTenant:
    """A tenant with one catalogue product and one active media buy. Factories must be bound to ``session``."""
    tenant = TenantFactory(tenant_id=unique_id("scope"))
    product = ProductFactory(tenant=tenant, product_id=unique_id("prod"))
    buy = MediaBuyFactory(
        tenant=tenant,
        media_buy_id=unique_id("mb"),
        status="active",
        budget=ACTIVE_BUY_BUDGET,
        principal__principal_id=unique_id("principal"),
        principal__access_token=unique_id("token"),
    )
    products_payload = [
        {
            "product_id": product.product_id,
            "name": product.name,
            "description": product.description,
            "delivery_type": product.delivery_type,
        }
    ]
    return TargetTenant(tenant.tenant_id, buy.principal.name, products_payload)


def tenant_state(session: Session, tenant_id: str) -> tuple:
    """Everything the four handlers can reach for a tenant, read fresh from the DB."""
    session.expire_all()
    tenant = session.get(Tenant, tenant_id)
    assert tenant is not None
    return (
        sorted(product.product_id for product in tenant.products),
        sorted(buy.media_buy_id for buy in tenant.media_buys),
        tenant.policy_settings,
    )


def redirect_path(response: Any) -> str:
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


def assert_anonymous_rejected(response: Any, api_mode: bool, tenant_id: str) -> None:
    """What ``require_tenant_access`` answers when there is no session.

    For the HTML route the target path is pinned on purpose: ``require_auth`` sends anonymous
    callers to the generic ``/login``, and with no decorator at all the handler's own redirect
    to ``policy.index`` would answer. Only the tenant login page means the tenant check ran.
    """
    if api_mode:
        assert response.status_code == 401
        assert response.get_json() == {"error": "Authentication required"}
        return
    assert response.status_code == 302
    assert redirect_path(response) == f"/tenant/{tenant_id}/login"


def assert_membership_rejected(response: Any, api_mode: bool) -> None:
    """What ``require_tenant_access`` answers when the session user is not an active member."""
    assert response.status_code == 403
    if api_mode:
        assert response.get_json() == {"error": "Access denied"}


class AdminTenantScopingEnv(AdminAccountEnv):
    """Test environment for the tenant-scoping scenarios of #2203.

    Same two transports as ``AdminAccountEnv``; adds the target tenant, the three caller
    memberships (elsewhere / inactive here / active here) and the four routes.
    """

    def __init__(self, *, mode: str | None = None) -> None:
        super().__init__(mode=mode)
        self._target: TargetTenant | None = None
        self._state: tuple | None = None

    @classmethod
    @contextmanager
    def integration(cls) -> Iterator[AdminTenantScopingEnv]:
        """The in-process env, with no ambient super-admin grant for the member identity.

        ``MEMBER_EMAIL`` lives at ``example.com``; an inherited ``SUPER_ADMIN_DOMAINS=example.com``
        would turn every rejection case into a super-admin pass. The stack's environment is the
        server's own (docker-compose.e2e.yml forwards ``SUPER_ADMIN_EMAILS`` only), so this is the
        one transport the test process controls, and both places that build the in-process env
        (tests/admin/conftest.py, tests/bdd/conftest.py) come through here.
        """
        with mock.patch.dict(os.environ):
            os.environ.pop("SUPER_ADMIN_DOMAINS", None)
            with cls(mode="integration") as env:
                yield env

    # ── Target tenant ─────────────────────────────────────────────────────

    @property
    def target(self) -> TargetTenant:
        assert self._target is not None, "seed_target_tenant() has not run"
        return self._target

    def seed_target_tenant(self) -> TargetTenant:
        with bound_factory_session() as session:
            self._target = seed_target_tenant(session)
        self._state = self.target_state()
        return self._target

    def target_state(self) -> tuple:
        with get_db_session() as session:
            return tenant_state(session, self.target.tenant_id)

    @property
    def seeded_state(self) -> tuple:
        """The target tenant's state as seeded; compare ``target_state()`` against it after a rejection."""
        assert self._state is not None, "seed_target_tenant() has not run"
        return self._state

    # ── Caller memberships ────────────────────────────────────────────────

    def login_as_member_of_other_tenant(self) -> None:
        """Active ``User`` row in a fresh home tenant, no row in the target."""
        self._authenticate_member(self._new_home_tenant(with_member=True))

    def login_with_target_membership(self, *, is_active: bool) -> None:
        """A ``User`` row in the TARGET tenant with ``is_active`` as given; session held via a fresh home tenant."""
        home_tenant_id = self._new_home_tenant(with_member=False)
        with bound_factory_session() as session:
            target = session.get(Tenant, self.target.tenant_id)
            UserFactory(tenant=target, user_id=unique_id("user"), email=MEMBER_EMAIL, is_active=is_active)
        self._authenticate_member(home_tenant_id)

    def _new_home_tenant(self, *, with_member: bool) -> str:
        """A tenant the session is logged in against. Never the target, so the bypass cannot fire."""
        with bound_factory_session():
            home = TenantFactory(tenant_id=unique_id("home"))
            if with_member:
                UserFactory(tenant=home, user_id=unique_id("user"), email=MEMBER_EMAIL)
            return home.tenant_id

    def _authenticate_member(self, login_tenant_id: str) -> None:
        """Log in as the non-admin test identity against ``login_tenant_id`` (never the target)."""
        self._login_via_test_auth(login_tenant_id, email=MEMBER_EMAIL, password=MEMBER_PASSWORD)

    # ── Requests ──────────────────────────────────────────────────────────

    def send(self, method: str, route_name: str) -> _AdminResponse:
        route = ROUTES[route_name]
        assert method in route.methods, f"{route_name} does not serve {method}"
        path = route.path.format(tenant_id=self.target.tenant_id)
        url = f"{self._base_url}{path}" if self._mode == "e2e" else path
        if method == "GET":
            return self._get(url)
        return self._post_form(url, {})
