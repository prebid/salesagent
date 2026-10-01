"""Admin tenant-scoping harness for the routes of prebid/salesagent#2203 and #2204.

Drives ``GET /api/tenant/<tenant_id>/revenue-chart``, ``GET /api/tenant/<tenant_id>/products``,
``GET /api/tenant/<tenant_id>/products/suggestions`` and ``GET,POST /tenant/<tenant_id>/policy/rules``
(#2203), and the six GAM reporting routes of ``src/adapters/gam_reporting_api.py`` (#2204),
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

In process the caller can also sign in through the two single-sign-on paths production
uses, OIDC and Google OAuth (``login=`` on ``integration()``). Those drive the real login
and callback routes with only the identity provider's two network calls stubbed, on an
app built without ``ADCP_AUTH_TEST_MODE``, so neither ``/test/auth`` nor the decorator's
test-mode branch exists. Both write ``session["user"]`` as a plain email string and set no
``session["role"]`` — the session shape #2204 is about.

The module-level helpers (route table, case expansion, seeding, state snapshot, assertion
helpers) are shared by the contract classes in
``tests/helpers/admin_tenant_scoping_contract.py`` and the BDD steps, so the three layers
grade one contract.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from typing import Any, Literal, NamedTuple
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from authlib.integrations.flask_client import FlaskOAuth2App
from sqlalchemy.orm import Session

from src.core.database.database_session import get_db_session
from src.core.database.models import Tenant
from src.services.default_products import get_default_products
from tests.factories import MediaBuyFactory, ProductFactory, TenantAuthConfigFactory, TenantFactory, UserFactory
from tests.harness.admin_accounts import AdminAccountEnv, _AdminResponse
from tests.utils.database_helpers import bound_factory_session

#: The one non-admin identity ``/test/auth`` accepts on the Docker stack. The integration
#: transport uses the same address so both transports grade identical ``User`` rows.
MEMBER_EMAIL = "test_tenant_user@example.com"
MEMBER_PASSWORD = "test123"
ACTIVE_BUY_BUDGET = Decimal("1500.00")
#: Any numeric id passes ``validate_numeric_id``; the handlers stop before they would use it.
GAM_ADVERTISER_ID = "12345"

#: How the caller signs in. ``test_auth`` runs on both transports; the two single-sign-on
#: paths run in process only (the stack has no identity provider).
Login = Literal["test_auth", "oidc", "google"]
SSO_LOGINS: tuple[Login, ...] = ("oidc", "google")


class TenantScopedRoute(NamedTuple):
    path: str
    methods: tuple[str, ...]
    api_mode: bool  # JSON 401/403 from require_tenant_access(api_mode=True); else redirect / abort(403)


_GAM_API = "/api/tenant/{tenant_id}"

#: The routes of #2203 and #2204, keyed by the name the Gherkin scenarios use.
ROUTES: dict[str, TenantScopedRoute] = {
    "revenue chart API": TenantScopedRoute("/api/tenant/{tenant_id}/revenue-chart", ("GET",), True),
    "products API": TenantScopedRoute("/api/tenant/{tenant_id}/products", ("GET",), True),
    "product suggestions API": TenantScopedRoute("/api/tenant/{tenant_id}/products/suggestions", ("GET",), True),
    "policy rules page": TenantScopedRoute("/tenant/{tenant_id}/policy/rules", ("GET", "POST"), False),
    "GAM reporting API": TenantScopedRoute(f"{_GAM_API}/gam/reporting", ("GET",), True),
    "GAM advertiser summary API": TenantScopedRoute(
        f"{_GAM_API}/gam/reporting/advertiser/{{advertiser_id}}/summary", ("GET",), True
    ),
    "GAM principal reporting API": TenantScopedRoute(
        f"{_GAM_API}/principals/{{principal_id}}/gam/reporting", ("GET",), True
    ),
    "GAM country breakdown API": TenantScopedRoute(f"{_GAM_API}/gam/reporting/countries", ("GET",), True),
    "GAM ad unit breakdown API": TenantScopedRoute(f"{_GAM_API}/gam/reporting/ad-units", ("GET",), True),
    "GAM principal summary API": TenantScopedRoute(
        f"{_GAM_API}/principals/{{principal_id}}/gam/reporting/summary", ("GET",), True
    ),
}

_NOT_GAM = {"error": "GAM reporting is only available for tenants using Google Ad Manager"}
_NO_GAM_ADVERTISER = {"error": "Principal does not have a GAM advertiser ID configured"}

#: The first answer each GAM reporting handler gives once the guard lets the caller through.
#: The target runs the mock adapter, so the tenant routes stop at the ad-server check and the
#: principal routes at the principal's missing GAM advertiser id: the caller reached the
#: handler, and no GAM call was made.
GAM_ADMITTED: dict[str, tuple[int, dict[str, str]]] = {
    "GAM reporting API": (400, _NOT_GAM),
    "GAM advertiser summary API": (400, _NOT_GAM),
    "GAM principal reporting API": (400, _NO_GAM_ADVERTISER),
    "GAM country breakdown API": (400, _NOT_GAM),
    "GAM ad unit breakdown API": (400, _NOT_GAM),
    "GAM principal summary API": (400, _NO_GAM_ADVERTISER),
}
GAM_ROUTE_NAMES: tuple[str, ...] = tuple(GAM_ADMITTED)


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
    principal_id: str
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
        # Only the principal id is set: PrincipalFactory derives token_hash/token_prefix
        # from it (tests/factories/principal.py), so a unique id is a unique credential.
        principal__principal_id=unique_id("principal"),
    )
    products_payload = [
        {
            "product_id": product.product_id,
            "name": product.name,
            "description": product.description,
            "delivery_type": product.delivery_type,
        }
    ]
    return TargetTenant(tenant.tenant_id, buy.principal.principal_id, buy.principal.name, products_payload)


def tenant_state(session: Session, tenant_id: str) -> tuple:
    """Everything the handlers under test can reach for a tenant, read fresh from the DB."""
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


#: The discovery document a real provider serves; only the two endpoints authlib reads.
_PROVIDER_METADATA = {
    "issuer": "https://idp.test",
    "authorization_endpoint": "https://idp.test/authorize",
    "token_endpoint": "https://idp.test/token",
}


@contextmanager
def _stubbed_identity_provider() -> Iterator[None]:
    """Answer authlib's two network calls: the discovery document and the token exchange.

    The token carries ``userinfo`` and no ``id_token``, so authlib skips the JWKS fetch.
    Everything else — the ``state`` authlib stored at login, the ``User`` lookup, the
    ``is_active`` check and the session writes — runs as in production.
    """
    token = {
        "access_token": "test-access-token",
        "token_type": "Bearer",
        "userinfo": {"email": MEMBER_EMAIL, "name": "Tenant Member"},
    }
    with (
        mock.patch.object(FlaskOAuth2App, "load_server_metadata", return_value=_PROVIDER_METADATA),
        mock.patch.object(FlaskOAuth2App, "fetch_access_token", return_value=token),
    ):
        yield


def _authorization_state(response: Any) -> str:
    """The ``state`` the login route put on its redirect to the provider."""
    assert response.status_code == 302, f"login did not redirect to the provider: {response.status_code}"
    location = response.headers["Location"]
    assert location.startswith(_PROVIDER_METADATA["authorization_endpoint"]), location
    return parse_qs(urlsplit(location).query)["state"][0]


class AdminTenantScopingEnv(AdminAccountEnv):
    """Test environment for the tenant-scoping scenarios of #2203 and #2204.

    Same two transports as ``AdminAccountEnv``; adds the target tenant, the three caller
    memberships (elsewhere / inactive here / active here), the routes, and — in process —
    the choice of login path.
    """

    def __init__(self, *, mode: str = "integration", base_url: str | None = None, login: Login = "test_auth") -> None:
        super().__init__(mode=mode, base_url=base_url)
        assert login == "test_auth" or mode == "integration", f"{login} login needs an identity provider; e2e has none"
        self._login = login
        self._target: TargetTenant | None = None
        self._state: tuple | None = None

    @classmethod
    @contextmanager
    def integration(
        cls, *, login: Login = "test_auth", super_admin_email: str | None = None
    ) -> Iterator[AdminTenantScopingEnv]:
        """The in-process env, with no ambient super-admin grant for the member identity.

        ``MEMBER_EMAIL`` lives at ``example.com``; an inherited ``SUPER_ADMIN_DOMAINS=example.com``
        would turn every rejection case into a super-admin pass, and Google login reads the
        singular ``SUPER_ADMIN_DOMAIN`` the same way. The stack's environment is the server's own
        (docker-compose.e2e.yml forwards ``SUPER_ADMIN_EMAILS`` only), so this is the one transport
        the test process controls, and both places that build the in-process env
        (tests/admin/conftest.py, tests/bdd/conftest.py) come through here.

        Every setting below is read when ``create_app()`` loads settings, which happens on entry.
        ``login`` other than ``test_auth`` builds the app without ``ADCP_AUTH_TEST_MODE``, so
        ``/test/auth`` is not registered and ``require_tenant_access`` has no test-mode branch to
        take. ``super_admin_email`` grants super-admin through ``SUPER_ADMIN_EMAILS``, a setting a
        deployment sets and ``is_super_admin`` reads; nothing about the session is forged.
        """
        with mock.patch.dict(os.environ):
            os.environ.pop("SUPER_ADMIN_DOMAINS", None)
            os.environ.pop("SUPER_ADMIN_DOMAIN", None)
            if login != "test_auth":
                os.environ.pop("ADCP_AUTH_TEST_MODE", None)
            if login == "google":
                # The generic OAUTH_* settings take precedence over GOOGLE_* (auth.get_oauth_config).
                for name in ("OAUTH_DISCOVERY_URL", "OAUTH_CLIENT_ID", "OAUTH_CLIENT_SECRET"):
                    os.environ.pop(name, None)
                os.environ["GOOGLE_CLIENT_ID"] = "test-client.apps.googleusercontent.com"
                os.environ["GOOGLE_CLIENT_SECRET"] = "test-client-secret"
            if super_admin_email is not None:
                os.environ["SUPER_ADMIN_EMAILS"] = super_admin_email
            with cls(mode="integration", login=login) as env:
                if login != "test_auth":
                    assert "test_auth" not in env._app.blueprints, "test login must not exist on an SSO app"
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
        """A tenant the session is logged in against. Never the target, so the bypass cannot fire.

        The single-sign-on callbacks admit only a user with an active row in the tenant they log
        into, so those paths always get one. The home tenant authorizes no email domain, so no
        login path can reach a tenant through ``authorized_domains`` instead of a ``User`` row.
        """
        with bound_factory_session():
            home = TenantFactory(tenant_id=unique_id("home"), authorized_domains=[])
            if with_member or self._login != "test_auth":
                UserFactory(tenant=home, user_id=unique_id("user"), email=MEMBER_EMAIL)
            if self._login == "oidc":
                TenantAuthConfigFactory(tenant=home, oidc_enabled=True)
            return home.tenant_id

    def _authenticate_member(self, login_tenant_id: str) -> None:
        """Log in as the non-admin member identity against ``login_tenant_id`` (never the target)."""
        if self._login == "oidc":
            self._login_via_oidc(login_tenant_id)
        elif self._login == "google":
            self._login_via_google(login_tenant_id)
        else:
            self._login_via_test_auth(login_tenant_id, email=MEMBER_EMAIL, password=MEMBER_PASSWORD)

    def _login_via_oidc(self, tenant_id: str) -> None:
        """Sign in through the tenant's OIDC login and callback; ``oidc.py`` writes the session."""
        with _stubbed_identity_provider():
            login = self._flask_client.get(f"/auth/oidc/login/{tenant_id}")
            callback = self._flask_client.get(f"/auth/oidc/callback?code=test-code&state={_authorization_state(login)}")
        assert callback.status_code == 302 and redirect_path(callback) == f"/tenant/{tenant_id}", (
            f"OIDC callback did not sign the member in: {callback.status_code} {callback.headers.get('Location')}"
        )
        self._assert_sso_session(tenant_id, auth_method="oidc")

    def _login_via_google(self, tenant_id: str) -> None:
        """Sign in through Google OAuth at ``tenant_id``, choosing that tenant if asked to pick one."""
        with _stubbed_identity_provider():
            login = self._flask_client.get(f"/tenant/{tenant_id}/auth/google")
            callback = self._flask_client.get(
                f"/auth/google/callback?code=test-code&state={_authorization_state(login)}"
            )
        assert callback.status_code == 302, f"Google callback failed: {callback.status_code}"
        if redirect_path(callback) == "/auth/select-tenant":
            callback = self._flask_client.post("/auth/select-tenant", data={"tenant_id": tenant_id})
        assert callback.status_code == 302 and redirect_path(callback) == f"/tenant/{tenant_id}", (
            f"Google login did not land on the tenant: {callback.status_code} {callback.headers.get('Location')}"
        )
        self._assert_sso_session(tenant_id, auth_method=None)

    def _assert_sso_session(self, tenant_id: str, *, auth_method: str | None) -> None:
        """The session production writes for an ordinary single-sign-on user: a str user and no role."""
        with self._flask_client.session_transaction() as sess:
            session = dict(sess)
        assert session["user"] == MEMBER_EMAIL, session.get("user")
        assert session["tenant_id"] == tenant_id
        assert session.get("auth_method") == auth_method
        forbidden = {"role", "test_user", "test_user_role", "test_tenant_id"} & session.keys()
        assert not forbidden, f"SSO session carries {sorted(forbidden)}"
        # Google login asks is_super_admin(), which caches its False verdict in the session.
        assert not session.get("is_super_admin"), "SSO member is a super-admin"

    # ── Requests ──────────────────────────────────────────────────────────

    def send(self, method: str, route_name: str) -> _AdminResponse:
        route = ROUTES[route_name]
        assert method in route.methods, f"{route_name} does not serve {method}"
        path = route.path.format(
            tenant_id=self.target.tenant_id, principal_id=self.target.principal_id, advertiser_id=GAM_ADVERTISER_ID
        )
        url = f"{self._base_url}{path}" if self._mode == "e2e" else path
        if method == "GET":
            return self._get(url)
        return self._post_form(url, {})
