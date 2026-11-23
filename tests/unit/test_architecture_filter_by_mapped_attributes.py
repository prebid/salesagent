"""Guard: ``filter_by()`` names only attributes the model maps.

Disease pattern (GH #2206): ``select(User).filter_by(..., is_admin=True)`` in
``src/admin/utils/helpers.py`` named a column ``User`` never had. SQLAlchemy raises
``InvalidRequestError`` at CALL time, not import time, so nothing short of executing
the query notices. The function wrapped its own query in ``except Exception: return
False``, so it answered False for every non-super-admin for fourteen months, and it had
no callers, so nothing noticed even that. ``filter_by`` takes attribute NAMES as keyword
strings, which is exactly what a static scan can check against the live mapper.

Which entity a keyword is looked up on follows ``Select._filter_by_zero``: the last
``join`` / ``outerjoin`` / ``join_from`` target, else the first ``select_from``, else
the entity the statement root addresses (``statement_entity``) -- ``select(M)``,
``select(M.column)`` or a legacy ``<session>.query(M)``. Resolution is closed-world, on
the helpers every ORM guard shares (``tests/unit/_architecture_helpers.py``):

- ``select`` and ``M`` resolve by IMPORT (``query_bindings`` / ``statement_entity``):
  ``select`` must come from ``sqlalchemy``, ``M`` from ``models.py`` (``... import User as
  DBUser`` included), and neither may be rebound in the module or the scope. A Pydantic
  ``Product``, a module-local ``def select``, ``sa.select(M)``, ``select(models.M)`` and
  ``select(func.sum(...))`` are unresolved.
- The chain is crossed only through the ``Select`` methods in ``SELECT_CHAIN_METHODS``
  (``select_chain``), which keep the entity; ``with_only_columns``, ``add_columns``,
  ``subquery`` and anything else not named there ends resolution.
- A local name resolves through ``fold_name``, the fold the creative-lookup guard reads
  too: only when every binding of it in the scope is a plain ``name = value`` statement
  (``scope_bindings``) and together they hold one statement, ``stmt = stmt.where(...)``
  folded onto it. A parameter, a loop or ``with`` target, ``+=``, an annotated or walrus
  assignment leave it unresolved, and so does an opaque fold: a second statement bound to
  the name, or a rebind the fold cannot read (``s = wrap(s)``). Functions, lambdas,
  classes and comprehensions are scopes (``iter_scopes``); a nested scope does not read
  the enclosing one's local names, and a name it or an enclosing function binds shadows
  the import of the same spelling.

Unresolved means skipped, never guessed. Each keyword must be in
``sqlalchemy.inspect(M).all_orm_descriptors`` -- columns, relationships, synonyms and
hybrids, the names ``filter_by`` can look up on the entity -- and only the installed
mapper (``orm_classes``) is consulted, so the inventory cannot go stale.

Scanned: every scope under ``src/`` and ``scripts/`` (``iter_module_trees``; vendored
source binds no model and grades nothing). A row names the function ``iter_scopes`` gives
the site's scope: a site in a lambda, comprehension or class body is reported under the
function that holds it. ``TestDetectorMetaTests`` pins one row per shape above, including
the valid queries an earlier version of this scan misreported.

No allowlist: the only violation at creation was the #2206 site, deleted in the same
change. A row added here needs a ``# FIXME(#<gh-issue>)`` at the source.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import inspect

from tests.unit._architecture_helpers import (
    SELECT_CHAIN_METHODS,
    SELECT_JOIN_METHODS,
    QueryBindings,
    assert_violations_match_allowlist,
    call_callee_name,
    fold_name,
    iter_module_trees,
    iter_scopes,
    orm_classes,
    parse_module,
    query_bindings,
    repo_root,
    scope_bindings,
    scope_nodes,
    select_chain,
    statement_entity,
    statement_root,
)

# (relpath, enclosing function per ``iter_scopes``, "Model.kwarg"). Shrink-only.
ALLOWLIST: set[tuple[str, str, str]] = set()


def _anchor(calls: list[ast.Call]) -> tuple[bool, ast.expr | None]:
    """What ``Select._filter_by_zero`` takes from a statement's *calls*, outermost first, ahead
    of the entity its root addresses.

    ``(True, target)``: the first join met is the LAST in source order, so it decides; else the
    last ``select_from`` met, which is the FIRST in source order. ``(False, None)`` when the
    calls hold neither, and the root decides (``statement_entity``).
    """
    select_from: ast.expr | None = None
    for call in calls:
        method, args = call_callee_name(call), call.args
        if method in SELECT_JOIN_METHODS:
            position = 1 if method == "join_from" else 0  # join_from(left, right)
            return True, args[position] if len(args) > position else None
        if method == "select_from" and args:
            select_from = args[0]
    return select_from is not None, select_from


class _Scope:
    """One scope's view: its plain bindings, and the imports it sees (``iter_scopes``)."""

    def __init__(self, scope: ast.AST, imports: QueryBindings) -> None:
        self._imports = imports
        self._assigned = scope_bindings(scope)
        self.nodes = list(scope_nodes(scope))

    def _imported(self, node: ast.expr | None) -> str | None:
        """The ORM class a bare name denotes by import, else None (an attribute never does)."""
        return self._imports.orm.get(node.id) if isinstance(node, ast.Name) else None

    def target(self, expr: ast.expr) -> type[Any] | None:
        """The entity ``filter_by`` addresses on *expr*, or None when it cannot be told."""
        base, calls = select_chain(expr, SELECT_CHAIN_METHODS)
        if isinstance(base, ast.Name):
            fold = fold_name(base.id, self._assigned, self._imports)
            if fold is None or fold.opaque:
                return None  # the name may hold another statement: skipped, never guessed
            base, calls = fold.root, calls + fold.calls
        anchored, node = _anchor(calls)
        if anchored:
            name = self._imported(node)
        elif (root := statement_root(base, self._imports)) is not None:
            name = statement_entity(root, self._imports)
        else:
            name = None
        return orm_classes().get(name) if name else None


def _graded_sites(tree: ast.Module, relpath: str) -> Iterator[tuple[str, type[Any], ast.Call]]:
    """``(function, model, filter_by call)`` for every site whose target resolves; *function*
    is the one ``iter_scopes`` gives the site's scope."""
    for scope_node, imports, function in iter_scopes(tree, query_bindings(relpath, tree)):
        scope = _Scope(scope_node, imports)
        for node in scope.nodes:
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "filter_by":
                model = scope.target(node.func.value)
                if model is not None:
                    yield function, model, node


def _find_in_module(tree: ast.Module, relpath: str) -> set[tuple[str, str, str]]:
    found: set[tuple[str, str, str]] = set()
    for function, model, call in _graded_sites(tree, relpath):
        mapped = set(inspect(model).all_orm_descriptors.keys())
        for kw in call.keywords:
            if kw.arg is not None and kw.arg not in mapped:
                found.add((relpath, function, f"{model.__name__}.{kw.arg}"))
    return found


def _find_phantom_filter_by() -> set[tuple[str, str, str]]:
    repo = repo_root()
    found: set[tuple[str, str, str]] = set()
    for tree, relpath in iter_module_trees([repo / "src", repo / "scripts"]):
        found |= _find_in_module(tree, relpath)
    return found


@pytest.mark.arch_guard
def test_filter_by_names_only_mapped_attributes() -> None:
    """A filter_by keyword that no mapper attribute matches is a call-time InvalidRequestError."""
    assert_violations_match_allowlist(
        _find_phantom_filter_by(),
        ALLOWLIST,
        fix_hint=(
            "filter_by() names an attribute the model does not map; SQLAlchemy raises "
            "InvalidRequestError when the query runs. Fix the name or the model -- do not allowlist."
        ),
    )


_PREAMBLE = "from sqlalchemy import func, select\nfrom src.core.database.models import Tenant, User\n"

# label -> (source after the preamble, rows as (function, "Model.kwarg")). The reported rows
# must match EXACTLY: the phantom on the entity filter_by addresses, nothing on a valid
# query, nothing on a target the scan cannot tell.
_TABLE: dict[str, tuple[str, set[tuple[str, str]]]] = {
    # The #2206 shape, and the spellings that reach the same entity.
    "phantom-column": (
        "def f(e):\n    return select(User).filter_by(email=e, is_active=True, is_admin=True)\n",
        {("f", "User.is_admin")},
    ),
    "accumulator": (
        "def f():\n    stmt = select(User)\n    stmt = stmt.filter_by(is_admin=True)\n    return stmt\n",
        {("f", "User.is_admin")},
    ),
    "accumulator-rebinding-keeps-the-class": (
        "def f():\n    stmt = select(User)\n    stmt = stmt.where(User.is_active)\n    return stmt.filter_by(is_admin=True)\n",
        {("f", "User.is_admin")},
    ),
    "accumulator-through-another-name": (
        "def f():\n    base = select(User)\n    stmt = base.where(User.is_active)\n    return stmt.filter_by(is_admin=True)\n",
        {("f", "User.is_admin")},
    ),
    "alias-import": (
        "from src.core.database.models import User as DBUser\n"
        "def f():\n    return select(DBUser).filter_by(is_admin=True)\n",
        {("f", "User.is_admin")},
    ),
    "select-under-an-alias": (
        "from sqlalchemy import select as sa_select\ndef f():\n    return sa_select(User).filter_by(is_admin=True)\n",
        {("f", "User.is_admin")},
    ),
    "select-from": (
        "def f():\n    return select(func.count()).select_from(User).filter_by(is_admin=True)\n",
        {("f", "User.is_admin")},
    ),
    "column-projection": (
        "def f():\n    return select(User.email).filter_by(is_admin=True)\n",
        {("f", "User.is_admin")},
    ),
    "legacy-query": (
        "def f(session):\n    return session.query(User).filter_by(is_admin=True)\n",
        {("f", "User.is_admin")},
    ),
    "join-targets-the-joined-entity": (
        "def f():\n    return select(Tenant).join(User).filter_by(subdomain='x')\n",
        {("f", "User.subdomain")},
    ),
    "outerjoin": (
        "def f():\n    return select(Tenant).outerjoin(User).filter_by(subdomain='x')\n",
        {("f", "User.subdomain")},
    ),
    "join-folded-through-a-rebind": (
        "def f():\n    s = select(Tenant)\n    s = s.join(User)\n    return s.filter_by(nope=1)\n",
        {("f", "User.nope")},
    ),
    "join-from-targets-the-right-side": (
        "def f():\n    return select(Tenant).join_from(Tenant, User).filter_by(subdomain='x')\n",
        {("f", "User.subdomain")},
    ),
    # A row names the innermost enclosing function, whatever scope holds the site.
    "module-level": ("stmt = select(User).filter_by(is_admin=True)\n", {("<module>", "User.is_admin")}),
    "lambda": ("f = lambda: select(User).filter_by(is_admin=True)\n", {("<module>", "User.is_admin")}),
    "lambda-in-a-function": (
        "def f():\n    return lambda: select(User).filter_by(is_admin=True)\n",
        {("f", "User.is_admin")},
    ),
    "comprehension-is-a-scope": (
        "def f(xs):\n    return [select(User).filter_by(is_admin=True) for x in xs]\n",
        {("f", "User.is_admin")},
    ),
    "comprehensions-in-two-functions-are-two-rows": (
        "def f(xs):\n    return [select(User).filter_by(nope=1) for x in xs]\n\n"
        "def g(xs):\n    return [select(User).filter_by(nope=1) for x in xs]\n",
        {("f", "User.nope"), ("g", "User.nope")},
    ),
    "class-body-in-a-function": (
        "def f():\n    class K:\n        q = select(User).filter_by(nope=1)\n\n    return K\n",
        {("f", "User.nope")},
    ),
    "a-class-body-binding-is-invisible-to-its-methods": (
        "class K:\n    select = None\n\n    def m(self):\n        return select(User).filter_by(is_admin=True)\n",
        {("m", "User.is_admin")},
    ),
    # Valid queries: nothing reported.
    "mapped-column-and-relationship": ("def f(t):\n    return select(User).filter_by(email='a', tenant=t)\n", set()),
    "join-then-a-joined-column": ("def f():\n    return select(Tenant).join(User).filter_by(email='x')\n", set()),
    "joined-accumulator": (
        "def f():\n    s = select(Tenant)\n    s = s.join(User)\n    return s.filter_by(email='x')\n",
        set(),
    ),
    "rebound-after-use": (
        "def f():\n    s = select(User)\n    a = s.filter_by(email='x')\n    s = select(Tenant)\n    return a, s\n",
        set(),
    ),
    # Unresolved targets: skipped, never guessed. The first raises at run time, and the
    # scan still says nothing, because it cannot tell WHICH class the name holds.
    "rebound-after-use-with-a-phantom": (
        "def f():\n    s = select(User)\n    a = s.filter_by(subdomain='x')\n    s = select(Tenant)\n    return a, s\n",
        set(),
    ),
    "opaque-rebind": (
        "def f():\n    s = select(User)\n    s = s.where(User.is_active)\n    s = wrap(s)\n    return s.filter_by(nope=1)\n",
        set(),
    ),
    "branch-bound-name": (
        "def f(c):\n    if c:\n        s = select(User)\n    else:\n        s = select(Tenant)\n"
        "    return s.filter_by(email='x')\n",
        set(),
    ),
    "parameter-of-a-nested-function": (
        "def outer():\n    s = select(User)\n\n    def inner(s):\n        return s.filter_by(subdomain='x')\n\n"
        "    return inner(s)\n",
        set(),
    ),
    "parameter-rebound-in-a-branch": (
        "def f(s, c):\n    if c:\n        s = select(User)\n    return s.filter_by(nope=1)\n",
        set(),
    ),
    "loop-target-and-a-plain-binding": (
        "def f(xs):\n    for s in xs:\n        pass\n    s = select(User)\n    return s.filter_by(nope=1)\n",
        set(),
    ),
    "comprehension-shadows-an-outer-name": (
        "def f(xs):\n    s = select(User)\n    return [s.filter_by(nope=1) for s in xs]\n",
        set(),
    ),
    "with-only-columns": (
        "def f():\n    return select(User).with_only_columns(Tenant.subdomain).filter_by(subdomain=1)\n",
        set(),
    ),
    "module-local-select": (
        "def select(x):\n    return x\n\ndef f():\n    return select(User).filter_by(nope=1)\n",
        set(),
    ),
    "select-shadowed-by-a-parameter": ("def f(select):\n    return select(User).filter_by(nope=1)\n", set()),
    "select-shadowed-by-an-enclosing-function": (
        "def outer(select):\n    def inner():\n        return select(User).filter_by(nope=1)\n\n    return inner\n",
        set(),
    ),
    "model-shadowed-by-an-enclosing-function": (
        "def f(User):\n    return lambda: select(User).filter_by(nope=1)\n",
        set(),
    ),
    "model-rebound-in-the-module": ("User = object()\ndef f():\n    return select(User).filter_by(nope=1)\n", set()),
    "relationship-join": ("def f():\n    return select(User).join(User.tenant).filter_by(nope=1)\n", set()),
    "aggregate": ("def f():\n    return select(func.sum(User.user_id)).filter_by(nope=1)\n", set()),
    "module-attribute": (
        "import src.core.database.models as models\ndef f():\n    return select(models.User).filter_by(nope=1)\n",
        set(),
    ),
    "pydantic-namesake": (
        "from src.core.schemas import Product\ndef f():\n    return select(Product).filter_by(nope=1)\n",
        set(),
    ),
}


class TestDetectorMetaTests:
    """The LIVE detector fires on the #2206 shape and stays quiet on valid queries."""

    @pytest.mark.arch_guard
    def test_mapper_inventory_is_not_empty(self) -> None:
        assert "User" in orm_classes() and "Tenant" in orm_classes()

    @pytest.mark.arch_guard
    def test_the_table_covers_both_verdicts(self) -> None:
        assert any(rows for _, rows in _TABLE.values()) and any(not rows for _, rows in _TABLE.values())

    @pytest.mark.arch_guard
    @pytest.mark.parametrize("label", sorted(_TABLE))
    def test_rows_per_shape(self, label: str) -> None:
        source, rows = _TABLE[label]
        expected = {("x.py", scope, attr) for scope, attr in rows}
        assert _find_in_module(ast.parse(_PREAMBLE + source), "x.py") == expected

    @pytest.mark.arch_guard
    @pytest.mark.parametrize(
        ("relpath", "model"),
        [
            ("src/core/database/repositories/principal.py", "Principal"),  # select(M)
            ("src/admin/blueprints/operations.py", "MediaBuy"),  # legacy db.query(M)
        ],
    )
    def test_live_scan_reaches_a_real_site(self, relpath: str, model: str) -> None:
        """The tree scan grades real sites, through both the 2.0 and the legacy spelling."""
        graded = {m.__name__ for _, m, _ in _graded_sites(parse_module(repo_root() / relpath), relpath)}
        assert model in graded
