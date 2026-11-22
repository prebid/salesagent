"""Guard: every admin route parameterised by ``<tenant_id>`` proves the caller may act on that tenant.

A rule that takes ``<tenant_id>`` from the URL must prove the caller belongs to that
tenant, and exactly one component does that for a user session:
``require_tenant_access`` (``src/admin/utils/helpers.py``). ``require_auth`` only checks
that a user is logged in, which is how the four routes of #2203 leaked one tenant's revenue
and catalogue to any logged-in user of another.

Scanning approach: walk the LIVE ``url_map`` rather than the source, so a route is graded by
whatever actually serves it — blueprint prefix, conditional registration, module-local
decorator spellings included. The guard reads what authorizes a rule from the
``functools.wraps`` chain of its view function (``__wrapped__`` links): the wrapper
``require_tenant_access`` installs, the one ``require_api_key_auth`` installs (a machine
caller, no user session to scope), or ``require_auth(admin_only=True)`` (a super-admin
action where the tenant is the subject, not the caller's scope). Those three are found by
identity, so a route that loses its decorator is reported on the next run.

``EXEMPT`` holds only the rules whose chain shows none of them: a login entry point, or a
route that should prove membership and does not yet. The second kind names the issue that
removes it, and the same number has to stand in a ``# FIXME(#n)`` above the route. A new
rule fails immediately; a row that becomes guarded, or disappears, fails as stale. The set
may only shrink.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any, NamedTuple

import pytest
from flask import Flask
from werkzeug.routing import Rule

from src.admin.auth_helpers import require_api_key_auth
from src.admin.utils.helpers import require_auth, require_tenant_access
from tests.helpers.admin_session import admin_test_app
from tests.unit._architecture_helpers import assert_violations_match_allowlist

# Every application of a decorator builds a new wrapper function, but all wrappers of one
# decorator share one code object — so identity on __code__ recognises the decorator no
# matter what else wraps the view or in which order.
_TENANT_ACCESS_WRAPPER = require_tenant_access()(lambda tenant_id: None).__code__
_API_KEY_WRAPPER = require_api_key_auth(setting="unused", config_key="unused", header="unused")(lambda: None).__code__
_REQUIRE_AUTH_WRAPPER = require_auth()(lambda: None).__code__

MEMBERSHIP = "membership"
API_KEY = "api key"
SUPER_ADMIN_ONLY = "super admin only"


class LoginEntry(NamedTuple):
    """A login entry point: the caller has no session yet, by definition, so there is nothing to scope."""


class Debt(NamedTuple):
    """A route that should prove membership and does not yet. *issue* is the GitHub issue or PR that fixes it."""

    issue: int
    reason: str


_LOGIN_ENTRY = LoginEntry()
_INLINE_DEAD_ROLE_CHECK = Debt(2233, "inline session['role'] viewer/tenant_admin check that no production login sets")
_INLINE_SESSION_TENANT_CHECK = Debt(2233, "inline session['tenant_id'] == tenant_id compare instead of the decorator")
_PR_2075 = Debt(2075, "that PR adds require_tenant_access() to publisher partners; remove when it merges")
_ISSUE_2204 = Debt(2204, "the module-local bare @require_auth in gam_reporting_api has to be removed first")

# (endpoint, rule) -> why nothing in its wrapper chain authorizes the caller. Only ever shrinks.
EXEMPT: dict[tuple[str, str], LoginEntry | Debt] = {
    ("auth.tenant_login", "/tenant/<tenant_id>/login"): _LOGIN_ENTRY,
    ("auth.tenant_google_auth", "/tenant/<tenant_id>/auth/google"): _LOGIN_ENTRY,
    ("oidc.login", "/auth/oidc/login/<tenant_id>"): _LOGIN_ENTRY,
    ("policy.index", "/tenant/<tenant_id>/policy/"): _INLINE_DEAD_ROLE_CHECK,
    ("policy.update", "/tenant/<tenant_id>/policy/update"): _INLINE_DEAD_ROLE_CHECK,
    ("policy.review_task", "/tenant/<tenant_id>/policy/review/<task_id>"): _INLINE_DEAD_ROLE_CHECK,
    ("inventory.analyze_ad_server_inventory", "/tenant/<tenant_id>/analyze-ad-server"): _INLINE_DEAD_ROLE_CHECK,
    ("inventory.orders_browser", "/tenant/<tenant_id>/orders"): _INLINE_SESSION_TENANT_CHECK,
    ("inventory.check_inventory_sync", "/tenant/<tenant_id>/check-inventory-sync"): _INLINE_SESSION_TENANT_CHECK,
    ("operations.reporting", "/tenant/<tenant_id>/reporting"): _INLINE_SESSION_TENANT_CHECK,
    ("publisher_partners.list_publisher_partners", "/tenant/<tenant_id>/publisher-partners"): _PR_2075,
    ("publisher_partners.add_publisher_partner", "/tenant/<tenant_id>/publisher-partners"): _PR_2075,
    (
        "publisher_partners.delete_publisher_partner",
        "/tenant/<tenant_id>/publisher-partners/<path:domain>",
    ): _PR_2075,
    ("publisher_partners.sync_publisher_partners", "/tenant/<tenant_id>/publisher-partners/sync"): _PR_2075,
    (
        "publisher_partners.get_publisher_properties",
        "/tenant/<tenant_id>/publisher-partners/<path:domain>/properties",
    ): _PR_2075,
    ("gam_reporting_api.get_gam_reporting", "/api/tenant/<tenant_id>/gam/reporting"): _ISSUE_2204,
    (
        "gam_reporting_api.get_advertiser_summary",
        "/api/tenant/<tenant_id>/gam/reporting/advertiser/<advertiser_id>/summary",
    ): _ISSUE_2204,
    (
        "gam_reporting_api.get_principal_reporting",
        "/api/tenant/<tenant_id>/principals/<principal_id>/gam/reporting",
    ): _ISSUE_2204,
    ("gam_reporting_api.get_country_breakdown", "/api/tenant/<tenant_id>/gam/reporting/countries"): _ISSUE_2204,
    ("gam_reporting_api.get_ad_unit_breakdown", "/api/tenant/<tenant_id>/gam/reporting/ad-units"): _ISSUE_2204,
    (
        "gam_reporting_api.get_principal_summary",
        "/api/tenant/<tenant_id>/principals/<principal_id>/gam/reporting/summary",
    ): _ISSUE_2204,
}

_FIX_HINT = (
    "Put @require_tenant_access() directly under the @<bp>.route(...) line "
    "(src/admin/utils/helpers.py). A machine route takes its require_api_key_auth decorator and a super-admin "
    "action takes @require_auth(admin_only=True); the guard reads both from the view, so neither needs a row. "
    "Add a row to EXEMPT only for a login entry point (LoginEntry), or for a route that cannot be guarded in "
    "this change (Debt, with the issue that fixes it and a # FIXME(#n) above the route). EXEMPT only shrinks: a "
    "row whose route is now authorized is reported as stale and must be removed."
)


def _authorized_by(view: Callable[..., Any]) -> str | None:
    """The mechanism in the view's wrapper chain that authorizes the caller for the tenant, or None."""
    current: Any = view
    while current is not None:
        code = getattr(current, "__code__", None)
        if code is _TENANT_ACCESS_WRAPPER:
            return MEMBERSHIP
        if code is _API_KEY_WRAPPER:
            return API_KEY
        if code is _REQUIRE_AUTH_WRAPPER and inspect.getclosurevars(current).nonlocals["admin_only"]:
            return SUPER_ADMIN_ONLY
        current = getattr(current, "__wrapped__", None)
    return None


def _tenant_rules(app: Flask) -> list[Rule]:
    return [rule for rule in app.url_map.iter_rules() if "tenant_id" in rule.arguments]


def _unguarded(app: Flask) -> set[tuple[str, str]]:
    """``(endpoint, rule)`` of every ``<tenant_id>`` rule whose own view shows no authorization.

    Each rule is graded by its own view. ``Rule.__eq__`` compares the path alone, so testing
    membership in a list of guarded rules would pass an unguarded view that shares its path
    with a guarded one.
    """
    return {
        (rule.endpoint, rule.rule)
        for rule in _tenant_rules(app)
        if _authorized_by(app.view_functions[rule.endpoint]) is None
    }


@pytest.fixture(scope="module")
def app() -> Flask:
    return admin_test_app()


def test_detector_recognises_each_decorator_and_nothing_else() -> None:
    """A detector that matched every view would report zero violations — grade it first."""

    def view(tenant_id: str) -> None:
        return None

    api_key = require_api_key_auth(setting="sync_api_key", config_key="sync_api_key", header="X-API-Key")

    assert _authorized_by(require_tenant_access()(view)) == MEMBERSHIP
    assert _authorized_by(require_auth()(require_tenant_access()(view))) == MEMBERSHIP
    assert _authorized_by(api_key(view)) == API_KEY
    assert _authorized_by(require_auth(admin_only=True)(view)) == SUPER_ADMIN_ONLY
    # A logged-in user is not a member, and not a super admin either.
    assert _authorized_by(require_auth()(view)) is None
    assert _authorized_by(view) is None


def test_an_unguarded_view_on_a_guarded_path_is_reported() -> None:
    """Each rule is graded by its own view: a path with one guarded view can still carry an unguarded one."""
    app = Flask(__name__)
    path = "/tenant/<tenant_id>/thing"
    app.add_url_rule(path, "read", require_tenant_access()(lambda tenant_id: ""), methods=["GET"])
    app.add_url_rule(path, "write", lambda tenant_id: "", methods=["PUT"])

    assert _unguarded(app) == {("write", path)}


def test_every_tenant_rule_is_authorized_or_is_exempt(app: Flask) -> None:
    seen = {_authorized_by(app.view_functions[rule.endpoint]) for rule in _tenant_rules(app)}
    assert seen >= {MEMBERSHIP, API_KEY, SUPER_ADMIN_ONLY}, (
        f"non-vacuity: the url_map walk found only {sorted(filter(None, seen))}; a detector that finds no rule "
        f"of its kind is not reading the wrapper chain"
    )

    assert_violations_match_allowlist(_unguarded(app), set(EXEMPT), fix_hint=_FIX_HINT)


def test_every_debt_row_is_cited_at_its_route(app: Flask) -> None:
    """A debt row and the ``# FIXME(#n)`` above its route state one fact; this is what keeps them one."""
    uncited = sorted(
        f"{endpoint}: no '# FIXME(#{row.issue})' above the route"
        for (endpoint, _rule), row in EXEMPT.items()
        if isinstance(row, Debt)
        and f"FIXME(#{row.issue})" not in (inspect.getcomments(inspect.unwrap(app.view_functions[endpoint])) or "")
    )

    assert uncited == []
