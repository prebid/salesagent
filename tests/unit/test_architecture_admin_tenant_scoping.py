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

Two more ways a view names a tenant are graded here too. ``require_tenant_access`` on a rule
WITHOUT ``<tenant_id>`` fails every call, so it is refused there. And a view that reads the
tenant from the query string or the body (``request.args.get("tenant_id")``) has no URL
argument to scope, so the source is scanned for those reads: each such view calls
``tenant_access_refusal`` (directly or through a helper in its module), or sits in
``REQUEST_TENANT_EXEMPT`` with the reason it checks some other way. That set only shrinks too.
"""

from __future__ import annotations

import ast
import inspect
from collections.abc import Callable, Iterator
from typing import Any, NamedTuple

import pytest
from flask import Flask
from werkzeug.routing import Rule

from src.admin.auth_helpers import require_api_key_auth
from src.admin.utils.helpers import require_auth, require_tenant_access
from tests.helpers.admin_session import admin_test_app
from tests.unit._architecture_helpers import assert_violations_match_allowlist, called_function_names, repo_root

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

# (endpoint, rule) -> why nothing in its wrapper chain authorizes the caller. Only ever shrinks.
EXEMPT: dict[tuple[str, str], LoginEntry | Debt] = {
    ("auth.tenant_login", "/tenant/<tenant_id>/login"): _LOGIN_ENTRY,
    ("auth.tenant_google_auth", "/tenant/<tenant_id>/auth/google"): _LOGIN_ENTRY,
    ("oidc.login", "/auth/oidc/login/<tenant_id>"): _LOGIN_ENTRY,
}

_FIX_HINT = (
    "Put @require_tenant_access() directly under the @<bp>.route(...) line "
    "(src/admin/utils/helpers.py). A machine route takes its require_api_key_auth decorator and a super-admin "
    "action takes @require_auth(admin_only=True); the guard reads both from the view, so neither needs a row. "
    "Add a row to EXEMPT only for a login entry point (LoginEntry), or for a route that cannot be guarded in "
    "this change (Debt, with the issue that fixes it and a # FIXME(#n) above the route). EXEMPT only shrinks: a "
    "row whose route is now authorized is reported as stale and must be removed."
)


def _wrapper_chain(view: Callable[..., Any]) -> Iterator[Any]:
    """The view and each function it wraps, outermost first (the ``__wrapped__`` links)."""
    current: Any = view
    while current is not None:
        yield current
        current = getattr(current, "__wrapped__", None)


def _authorized_by(view: Callable[..., Any]) -> str | None:
    """The mechanism in the view's wrapper chain that authorizes the caller for the tenant, or None."""
    for current in _wrapper_chain(view):
        code = getattr(current, "__code__", None)
        if code is _TENANT_ACCESS_WRAPPER:
            return MEMBERSHIP
        if code is _API_KEY_WRAPPER:
            return API_KEY
        if code is _REQUIRE_AUTH_WRAPPER and inspect.getclosurevars(current).nonlocals["admin_only"]:
            return SUPER_ADMIN_ONLY
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


def _membership_checks_without_a_tenant(app: Flask) -> set[tuple[str, str]]:
    """``(endpoint, rule)`` of every rule WITHOUT ``<tenant_id>`` whose view carries ``require_tenant_access``.

    The decorator reads ``tenant_id`` from the URL, so on such a rule every call fails.
    """
    return {
        (rule.endpoint, rule.rule)
        for rule in app.url_map.iter_rules()
        if "tenant_id" not in rule.arguments
        and any(
            getattr(wrapper, "__code__", None) is _TENANT_ACCESS_WRAPPER
            for wrapper in _wrapper_chain(app.view_functions[rule.endpoint])
        )
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


def test_a_membership_check_is_only_on_a_rule_that_names_the_tenant(app: Flask) -> None:
    assert _membership_checks_without_a_tenant(app) == set()


def test_the_admin_app_serves_no_debug_routes(app: Flask) -> None:
    """/debug/headers echoed every request header, the HttpOnly session cookie included, to anyone.

    A script injected into an admin page could read the cookie back from it. Debug routes
    belong on the FastAPI side, behind ``debug_routes_enabled``; the admin app has none.
    """
    debug_rules = [rule.rule for rule in app.url_map.iter_rules() if rule.rule.startswith("/debug")]
    assert debug_rules == []
    assert app.test_client().get("/debug/headers").status_code == 404


# --- the tenant taken from the query string or the body ------------------------------------

#: (module, view) of the views that read a tenant id from the request and check it some
#: other way, each with the reason. Only ever shrinks.
REQUEST_TENANT_EXEMPT: dict[tuple[str, str], str] = {
    ("src/admin/blueprints/auth.py", "select_tenant"): (
        "accepts only a tenant listed in the session's own available_tenants, set at sign-in"
    ),
}

_REQUEST_SOURCES = {"args", "form", "values", "json"}

#: Request keys that name a tenant.
_TENANT_KEYS = {"tenant_id", "tenant", "tid"}

_REQUEST_TENANT_FIX_HINT = (
    "A view that reads the tenant id from the query string or the body calls "
    "tenant_access_refusal(tenant_id) (src/admin/utils/helpers.py) before using it, and returns the refusal "
    "when there is one. REQUEST_TENANT_EXEMPT only shrinks: a row whose view now checks access, or is gone, "
    "is reported as stale and must be removed."
)


def _is_request_source(node: ast.AST, aliases: set[str]) -> bool:
    """``request.args/form/values/json``, ``request.get_json(...)``, a name bound to one, or ``x or {}`` of those."""
    if isinstance(node, ast.BoolOp):
        return any(_is_request_source(value, aliases) for value in node.values)
    if isinstance(node, ast.Name):
        return node.id in aliases
    if isinstance(node, ast.Call):
        return ast.unparse(node.func) == "request.get_json"
    source = ast.unparse(node)
    return source.startswith("request.") and source.split(".", 1)[1] in _REQUEST_SOURCES


def _request_aliases(function: ast.AST) -> set[str]:
    """Names the function binds to the request's parameters or body (``data = request.get_json() or {}``)."""
    aliases: set[str] = set()
    for node in ast.walk(function):
        if isinstance(node, ast.Assign) and _is_request_source(node.value, aliases):
            aliases.update(t.id for t in node.targets if isinstance(t, ast.Name))
    return aliases


def _reads_request_tenant_id(function: ast.AST) -> bool:
    """Whether *function* reads a tenant key from the request's parameters or body, directly or through a local name."""
    aliases = _request_aliases(function)
    for node in ast.walk(function):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get":
            target, key = node.func.value, node.args[0] if node.args else None
        elif isinstance(node, ast.Subscript):
            target, key = node.value, node.slice
        else:
            continue
        if isinstance(key, ast.Constant) and key.value in _TENANT_KEYS and _is_request_source(target, aliases):
            return True
    return False


def request_tenant_views_without_access_check(sources: dict[str, str]) -> set[tuple[str, str]]:
    """``(module, view)`` of every route view that reads a tenant id from the request and never checks access to it.

    A view checks access when it carries ``require_tenant_access``, or calls
    ``tenant_access_refusal`` itself or through a function of its module that does
    (``format_search`` reads the id in the view and checks it in a helper).
    """
    found: set[tuple[str, str]] = set()
    for path, source in sources.items():
        tree = ast.parse(source)
        functions = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        checkers = {n.name for n in functions if "tenant_access_refusal" in called_function_names(n)} | {
            "tenant_access_refusal"
        }
        for node in functions:
            if not any(".route(" in ast.unparse(d) for d in node.decorator_list):
                continue
            decorators = {ast.unparse(d).split("(", 1)[0] for d in node.decorator_list}
            if "require_tenant_access" in decorators or called_function_names(node) & checkers:
                continue
            if _reads_request_tenant_id(node):
                found.add((path, node.name))
    return found


def _admin_sources() -> dict[str, str]:
    root = repo_root()
    return {
        p.relative_to(root).as_posix(): p.read_text(encoding="utf-8")
        for p in sorted((root / "src" / "admin").rglob("*.py"))
        if "tests" not in p.relative_to(root).parts
    }


def test_a_view_taking_the_tenant_from_the_request_checks_access_to_it() -> None:
    assert_violations_match_allowlist(
        request_tenant_views_without_access_check(_admin_sources()),
        set(REQUEST_TENANT_EXEMPT),
        fix_hint=_REQUEST_TENANT_FIX_HINT,
    )
