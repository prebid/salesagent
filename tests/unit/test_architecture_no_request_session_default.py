"""Guard: a Flask session lookup never falls back to a request-supplied value.

``session.pop("gam_oauth_tenant_id", state)`` was the whole of
https://github.com/prebid/salesagent/issues/2205: with no session the tenant came
from the ``state`` query parameter, so anyone completing a Google consent could name
a victim tenant. The default of a session lookup is taken exactly when the request
carries no session, which is the request that must not be trusted — so the default
may never be something the request supplied: a ``request.*`` expression, a route
parameter, or a name bound from ``request.*``. The ``session.get(...) or
request.args.get(...)`` spelling is the same fallback written with ``or``.
"""

from __future__ import annotations

import ast
from collections import defaultdict
from collections.abc import Iterator

import pytest

from tests.unit._architecture_helpers import (
    assert_detector_catches_ast_snippets,
    assert_violations_match_allowlist,
    iter_module_trees,
    repo_root,
    walk_with_enclosing_function,
)

# Pre-existing violations, keyed on (path, enclosing function).
KNOWN_VIOLATIONS: set[tuple[str, str]] = set()

_FIX_HINT = (
    "Default a session lookup to a literal (None, {}, False, ...) and reject the request when the "
    "key is absent. A value the request supplied is what the session is there to replace."
)


def _is_flask_session(node: ast.expr) -> bool:
    if isinstance(node, ast.Name):
        return node.id in {"session", "flask_session"}
    return isinstance(node, ast.Attribute) and node.attr == "session" and isinstance(node.value, ast.Name)


def _is_session_lookup(node: ast.AST) -> bool:
    """``session.get(<str>, ...)`` or ``session.pop(<str>, ...)`` — a string key rules out ``Session.get(Model, pk)``."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"get", "pop"}
        and _is_flask_session(node.func.value)
        and bool(node.args)
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    )


def _is_request_rooted(node: ast.expr) -> bool:
    """True for ``request``, ``request.args``, ``request.args.get(...)``, ``request.form["x"]``, ..."""
    while isinstance(node, ast.Attribute | ast.Call | ast.Subscript):
        node = node.func if isinstance(node, ast.Call) else node.value
    return isinstance(node, ast.Name) and node.id == "request"


def _mentions_request(node: ast.expr, request_names: set[str]) -> bool:
    return any(
        (isinstance(sub, ast.Name) and sub.id in request_names) or _is_request_rooted(sub) for sub in ast.walk(node)
    )


def _request_names_by_function(tree: ast.Module) -> dict[str, set[str]]:
    """Per function: its parameters plus every name bound from a ``request.*`` expression."""
    names: dict[str, set[str]] = defaultdict(set)
    for func in ast.walk(tree):
        if not isinstance(func, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        names[func.name].update(arg.arg for arg in ast.walk(func.args) if isinstance(arg, ast.arg))
        for node in ast.walk(func):
            if isinstance(node, ast.Assign | ast.AnnAssign | ast.NamedExpr) and node.value is not None:
                if any(_is_request_rooted(sub) for sub in ast.walk(node.value)):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    names[func.name].update(t.id for t in targets if isinstance(t, ast.Name))
    return names


def _violations(tree: ast.Module) -> Iterator[tuple[ast.AST, str]]:
    request_names = _request_names_by_function(tree)
    for node, enclosing in walk_with_enclosing_function(tree):
        if (
            _is_session_lookup(node)
            and len(node.args) > 1
            and _mentions_request(node.args[1], request_names[enclosing])
        ):
            yield node, enclosing
        elif (
            isinstance(node, ast.BoolOp)
            and isinstance(node.op, ast.Or)
            and _is_session_lookup(node.values[0])
            and any(_is_request_rooted(value) for value in node.values[1:])
        ):
            yield node, enclosing


def _lineno_violations(tree: ast.Module) -> list[int]:
    return [node.lineno for node, _ in _violations(tree)]


@pytest.mark.arch_guard
def test_detector_catches_known_bad() -> None:
    assert_detector_catches_ast_snippets(
        _lineno_violations,
        snippets={
            "route parameter": 'def f(tenant_id):\n    return session.get("t", tenant_id)\n',
            "name bound from request": (
                'def f():\n    state = request.args.get("state")\n    return session.pop("t", state)\n'
            ),
            "request expression": 'def f():\n    return session.get("t", request.args.get("t"))\n',
            "or request": 'def f():\n    return session.get("t") or request.args.get("t")\n',
            "flask_session alias": 'def f(x):\n    return flask_session.get("t", x)\n',
        },
    )


@pytest.mark.arch_guard
def test_detector_ignores_session_derived_defaults_and_orm_sessions() -> None:
    clean = (
        "def f(db_session, tenant_id):\n"
        '    email = session["user"]\n'
        '    a = session.get("user_name", email.split("@")[0].title())\n'
        '    b = session.pop("flow", None)\n'
        '    c = session.pop("t", None) or session.get("u")\n'
        "    return a, b, c, db_session.get(Tenant, tenant_id)\n"
    )
    assert _lineno_violations(ast.parse(clean)) == []


@pytest.mark.arch_guard
def test_no_request_supplied_session_default() -> None:
    found = {
        (path, enclosing)
        for tree, path in iter_module_trees([repo_root() / "src"])
        for _, enclosing in _violations(tree)
    }
    assert_violations_match_allowlist(found, KNOWN_VIOLATIONS, fix_hint=_FIX_HINT)
