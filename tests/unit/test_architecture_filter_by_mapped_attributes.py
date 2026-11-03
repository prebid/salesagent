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
the selected entity -- ``select(M)``, ``select(M.column)`` or a legacy
``<session>.query(M)``. ``M`` counts only when the module binds it by an import from
``models.py`` (``from ...models import User as DBUser`` included), so a Pydantic
``Product`` never grades against the ORM one. A local name (``stmt = select(M)``, then
``stmt = stmt.filter_by(...)``) resolves when every ``name = ...`` binding of it in the
enclosing scope agrees on one class; rebinding the name to a chain on itself keeps the
class, and a scope stops at nested functions, lambdas and classes. Everything else is
UNRESOLVED and skipped rather than guessed: a parameter, a join onto a relationship
attribute, ``select(models.M)``, ``sa.select(M)``, ``select(func.sum(...))``, bindings
that disagree, and a name bound only by an annotated, walrus, unpacking or
``with ... as`` form (none has a live site). Each keyword must be in
``sqlalchemy.inspect(M).all_orm_descriptors`` -- columns, relationships, synonyms and
hybrids, the names ``filter_by`` can look up on the entity -- and only the installed
mapper is consulted, so the inventory cannot go stale.

Scanned: every module-level, function and lambda scope under ``src/`` (less
``src/vendor``) and ``scripts/``. Class bodies are not scanned (no live site). ``TestDetectorMetaTests``
pins one row per shape above, including the valid queries an earlier version of this
scan misreported.

No allowlist: the only violation at creation was the #2206 site, deleted in the same
change. A row added here needs a ``# FIXME(#<gh-issue>)`` at the source.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import inspect

import src.core.database.models as _models
from tests.unit._architecture_helpers import (
    assert_violations_match_allowlist,
    orm_bindings,
    parse_module,
    repo_root,
    src_python_files,
)

# Every mapped class by name: what an import from models.py can bind a local name to.
_ORM: dict[str, type[Any]] = {mapper.class_.__name__: mapper.class_ for mapper in _models.Base.registry.mappers}

# (relpath, enclosing scope, "Model.kwarg"). Shrink-only.
ALLOWLIST: set[tuple[str, str, str]] = set()

_JOINS = frozenset({"join", "outerjoin", "join_from"})
_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)


def _scope_nodes(scope: ast.AST) -> Iterator[ast.AST]:
    """*scope*'s own nodes in source order; a nested function, lambda or class is another scope."""
    for child in ast.iter_child_nodes(scope):
        if isinstance(child, (*_SCOPES, ast.ClassDef)):
            continue
        yield child
        yield from _scope_nodes(child)


def _anchor(expr: ast.expr) -> tuple[str, ast.expr | None]:
    """What ``Select._filter_by_zero`` picks on *expr*, walking the call chain outermost-in.

    ``("join", target)``: the first join met is the LAST in source order, so it decides.
    ``("select_from", entity)``: the last met is the FIRST in source order, so it
    overwrites. Otherwise ``("select", call)`` for ``select(...)`` / ``<x>.query(...)``,
    ``("name", Name)`` for a local accumulator, ``("other", node)`` for anything else.
    """
    select_from: ast.expr | None = None
    node = expr
    while isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr != "query":
        attr, args = node.func.attr, node.args
        if attr in _JOINS:
            position = 1 if attr == "join_from" else 0  # join_from(left, right)
            return "join", args[position] if len(args) > position else None
        if attr == "select_from" and args:
            select_from = args[0]
        node = node.func.value
    if select_from is not None:
        return "select_from", select_from
    if isinstance(node, ast.Call) and (
        isinstance(node.func, ast.Attribute) or (isinstance(node.func, ast.Name) and node.func.id == "select")
    ):
        return "select", node
    if isinstance(node, ast.Name):
        return "name", node
    return "other", node


class _Scope:
    """One scope's ``name = ...`` bindings, resolved against the module's model imports."""

    def __init__(self, scope: ast.AST, bindings: dict[str, str]) -> None:
        self._bindings = bindings
        self._assigned: dict[str, list[ast.expr]] = {}
        self.nodes = list(_scope_nodes(scope))
        for node in self.nodes:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                self._assigned.setdefault(node.targets[0].id, []).append(node.value)

    def class_named(self, node: ast.expr | None) -> type[Any] | None:
        """The ORM class an import-bound name denotes, else None."""
        if isinstance(node, ast.Name):
            return _ORM.get(self._bindings.get(node.id, ""))
        return None

    def target(self, expr: ast.expr, resolving: frozenset[str] = frozenset()) -> type[Any] | None:
        """The entity ``filter_by`` addresses on *expr*, or None when it cannot be told."""
        kind, node = _anchor(expr)
        if kind in {"join", "select_from"}:
            return self.class_named(node)
        if kind == "select" and isinstance(node, ast.Call):
            arg = node.args[0] if node.args else None
            # select(M.column): the entity of the first selected column
            return self.class_named(arg.value if isinstance(arg, ast.Attribute) else arg)
        if kind == "name" and isinstance(node, ast.Name):
            return self._resolve(node.id, resolving)
        return None

    def _resolve(self, name: str, resolving: frozenset[str]) -> type[Any] | None:
        """The one class every binding of *name* agrees on, else None."""
        if name in resolving:
            return None  # a = b.where(...); b = a.where(...)
        agreed: set[type[Any]] = set()
        for value in self._assigned.get(name, ()):
            kind, node = _anchor(value)
            if kind == "name" and isinstance(node, ast.Name) and node.id == name:
                continue  # stmt = stmt.where(...) keeps the class
            model = self.target(value, resolving | {name})
            if model is None:
                return None
            agreed.add(model)
        return agreed.pop() if len(agreed) == 1 else None


def _graded_sites(tree: ast.Module, relpath: str) -> Iterator[tuple[str, type[Any], ast.Call]]:
    """``(scope name, model, filter_by call)`` for every site whose target resolves."""
    bindings = orm_bindings(relpath, tree)
    scopes: list[ast.AST] = [tree, *(node for node in ast.walk(tree) if isinstance(node, _SCOPES))]
    for scope_node in scopes:
        scope = _Scope(scope_node, bindings)
        scope_name = getattr(scope_node, "name", "<module>" if scope_node is tree else "<lambda>")
        for node in scope.nodes:
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "filter_by":
                model = scope.target(node.func.value)
                if model is not None:
                    yield scope_name, model, node


def _find_in_module(tree: ast.Module, relpath: str) -> set[tuple[str, str, str]]:
    found: set[tuple[str, str, str]] = set()
    for scope_name, model, call in _graded_sites(tree, relpath):
        mapped = set(inspect(model).all_orm_descriptors.keys())
        for kw in call.keywords:
            if kw.arg is not None and kw.arg not in mapped:
                found.add((relpath, scope_name, f"{model.__name__}.{kw.arg}"))
    return found


def _find_phantom_filter_by() -> set[tuple[str, str, str]]:
    repo = repo_root()
    found: set[tuple[str, str, str]] = set()
    for path in (*src_python_files(repo), *(repo / "scripts").rglob("*.py")):
        found |= _find_in_module(parse_module(path), str(path.relative_to(repo)))
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

# label -> (source after the preamble, rows as (scope, "Model.kwarg")). The reported rows
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
    "join-from-targets-the-right-side": (
        "def f():\n    return select(Tenant).join_from(Tenant, User).filter_by(subdomain='x')\n",
        {("f", "User.subdomain")},
    ),
    "module-level": ("stmt = select(User).filter_by(is_admin=True)\n", {("<module>", "User.is_admin")}),
    "lambda": ("f = lambda: select(User).filter_by(is_admin=True)\n", {("<lambda>", "User.is_admin")}),
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
        assert "User" in _ORM and "Tenant" in _ORM

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
    def test_live_scan_reaches_a_real_site(self) -> None:
        """The tree scan grades real sites: the principal repository's count query."""
        relpath = "src/core/database/repositories/principal.py"
        graded = {model.__name__ for _, model, _ in _graded_sites(parse_module(repo_root() / relpath), relpath)}
        assert "Principal" in graded
