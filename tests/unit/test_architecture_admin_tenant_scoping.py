"""Guard: every admin route parameterised by ``<tenant_id>`` proves tenant membership.

A rule that takes ``<tenant_id>`` from the URL must prove the caller belongs to that
tenant, and exactly one component does that:
``require_tenant_access`` (``src/admin/utils/helpers.py``). ``require_auth`` only checks
that a user is logged in, which is how the four routes of #2203 leaked one tenant's revenue
and catalogue to any logged-in user of another.

Scanning approach: walk the LIVE ``create_app().url_map`` rather than the source, so a
route is graded by whatever actually serves it — blueprint prefix, conditional
registration, module-local decorator spellings included. A rule is guarded when the
``functools.wraps`` chain of its view function (``__wrapped__`` links) contains the
wrapper ``require_tenant_access`` installs. Every other ``<tenant_id>`` rule must sit in
``EXEMPT`` with a one-line reason. A new rule fails immediately; a row that becomes
guarded, or disappears, fails as stale. The set may only shrink.

Seeded from the measurement on ``main`` @ fd90b69a — 178 rules carry ``<tenant_id>``, 141
go through ``require_tenant_access``, 37 do not — minus the four routes #2203 itself guards,
so 33 rows remain. The set only shrinks.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from flask import Flask
from werkzeug.routing import Rule

from src.admin.app import create_app
from src.admin.utils.helpers import require_auth, require_tenant_access
from tests.unit._architecture_helpers import assert_violations_match_allowlist

# Every application of require_tenant_access() builds a new wrapper function, but all of
# them share this one code object — so identity on __code__ recognises the decorator no
# matter what else wraps the view or in which order.
_TENANT_ACCESS_WRAPPER = require_tenant_access()(lambda tenant_id: None).__code__

_LOGIN_ENTRY = "login entry point: the caller has no session yet, by definition"
_API_KEY = "machine caller authenticated by require_api_key_auth (src/admin/auth_helpers.py); no user session to scope"
_SUPER_ADMIN_ONLY = (
    "require_auth(admin_only=True): super-admin action where the tenant is the subject, not the caller's scope"
)
_INLINE_DEAD_ROLE_CHECK = "#2233: inline session['role'] viewer/tenant_admin check that no production login sets"
_INLINE_SESSION_TENANT_CHECK = "#2233: inline session['tenant_id'] == tenant_id compare instead of the decorator"
_ISSUE_2234 = "#2234: GAM OAuth initiation proves the tenant exists, not that the caller belongs to it"
_PR_2075 = "PR #2075 (open) adds require_tenant_access(api_mode=True) to publisher partners; remove when it merges"
_ISSUE_2204 = "#2204: the module-local bare @require_auth in gam_reporting_api has to be removed first"

# (endpoint, rule) -> why it may skip the decorator. One line each; only ever shrinks.
EXEMPT: dict[tuple[str, str], str] = {
    ("auth.tenant_login", "/tenant/<tenant_id>/login"): _LOGIN_ENTRY,
    ("auth.tenant_google_auth", "/tenant/<tenant_id>/auth/google"): _LOGIN_ENTRY,
    ("oidc.login", "/auth/oidc/login/<tenant_id>"): _LOGIN_ENTRY,
    ("oidc.test_initiate", "/auth/oidc/test/<tenant_id>"): _LOGIN_ENTRY,
    ("auth.gam_authorize", "/auth/gam/authorize/<tenant_id>"): _ISSUE_2234,
    ("sync_api.trigger_sync", "/api/sync/trigger/<tenant_id>"): _API_KEY,
    ("sync_api.get_sync_history", "/api/sync/history/<tenant_id>"): _API_KEY,
    ("sync_api.sync_tenant_orders", "/api/sync/tenant/<tenant_id>/orders/sync"): _API_KEY,
    ("sync_api.get_tenant_orders", "/api/sync/tenant/<tenant_id>/orders"): _API_KEY,
    ("sync_api.get_order_details", "/api/sync/tenant/<tenant_id>/orders/<order_id>"): _API_KEY,
    ("sync_api.get_tenant_line_items", "/api/sync/tenant/<tenant_id>/line-items"): _API_KEY,
    ("tenant_management_api.get_tenant", "/api/v1/tenant-management/tenants/<tenant_id>"): _API_KEY,
    ("tenant_management_api.update_tenant", "/api/v1/tenant-management/tenants/<tenant_id>"): _API_KEY,
    ("tenant_management_api.delete_tenant", "/api/v1/tenant-management/tenants/<tenant_id>"): _API_KEY,
    ("core.reactivate_tenant", "/admin/tenant/<tenant_id>/reactivate"): _SUPER_ADMIN_ONLY,
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
        "/tenant/<tenant_id>/publisher-partners/<int:partner_id>",
    ): _PR_2075,
    ("publisher_partners.sync_publisher_partners", "/tenant/<tenant_id>/publisher-partners/sync"): _PR_2075,
    (
        "publisher_partners.get_publisher_properties",
        "/tenant/<tenant_id>/publisher-partners/<int:partner_id>/properties",
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
    "Put @require_tenant_access(api_mode=True for JSON routes) directly under the @<bp>.route(...) line "
    "(src/admin/utils/helpers.py). Add a row to EXEMPT only when the route has no caller-tenant relationship "
    "to prove — a login entry point, an API-key service route, a super-admin-only action — and say why in one "
    "line. EXEMPT only shrinks: a row whose route is now guarded is reported as stale and must be removed."
)


def _enforces_membership(view: Callable[..., Any]) -> bool:
    """True when ``require_tenant_access`` sits anywhere in the view's wrapper chain."""
    current: Any = view
    while current is not None:
        if getattr(current, "__code__", None) is _TENANT_ACCESS_WRAPPER:
            return True
        current = getattr(current, "__wrapped__", None)
    return False


def _tenant_rules(app: Flask) -> list[Rule]:
    return [rule for rule in app.url_map.iter_rules() if "tenant_id" in rule.arguments]


@pytest.fixture(scope="module")
def app() -> Flask:
    return create_app()


def test_detector_recognises_the_decorator_and_nothing_else() -> None:
    """A detector that matched every view would report zero violations — grade it first."""

    def view(tenant_id: str) -> None:
        return None

    assert _enforces_membership(require_tenant_access(api_mode=True)(view))
    assert _enforces_membership(require_auth()(require_tenant_access()(view)))
    assert not _enforces_membership(require_auth()(view))
    assert not _enforces_membership(view)


def test_every_tenant_rule_enforces_membership_or_is_exempt(app: Flask) -> None:
    rules = _tenant_rules(app)
    guarded = [rule for rule in rules if _enforces_membership(app.view_functions[rule.endpoint])]
    assert guarded, "non-vacuity: the url_map walk found no rule wrapped by require_tenant_access"

    unguarded = {(rule.endpoint, rule.rule) for rule in rules if rule not in guarded}
    assert_violations_match_allowlist(unguarded, set(EXEMPT), fix_hint=_FIX_HINT)
