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
``select(M.column)``, ``select(models.M)`` or a legacy ``<session>.query(M)``. That rule is
``filter_by_entity``, read over the queries ``iter_queries`` gives; resolution is
closed-world, on the helpers every ORM guard shares (``tests/unit/_architecture_helpers.py``):

- ``select`` and ``M`` resolve by IMPORT, as the scope that reads them binds it
  (``query_bindings`` / ``iter_scopes``): ``select`` must come from ``sqlalchemy``, ``M`` from
  ``models.py`` (``... import User as DBUser`` and ``models.User`` included), and neither may
  be rebound in the scope or an enclosing one; an import inside one function binds nothing
  in another. A Pydantic ``Product``, a module-local ``def select``, ``sa.select(M)`` and
  ``select(func.sum(...))`` are unresolved, and so is a join target that is no class
  reference (``M.tenant``).
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

Scanned: every scope under ``src/`` and ``scripts/`` (``scan_rows``; vendored source binds
no model and grades nothing). A row names the function ``iter_scopes`` gives the site's
scope: a site in a lambda, comprehension or class body is reported under the function
that holds it. ``TestDetectorMetaTests`` pins one row per shape above, including the
valid queries an earlier version of this scan misreported.

No allowlist: the only violation at creation, the #2206 site, was deleted on main by
#2689. A row added here needs a ``# FIXME(#<gh-issue>)`` at the source.
"""

from __future__ import annotations

import ast
from collections.abc import Callable, Iterable, Iterator

import pytest
from sqlalchemy import inspect

from tests.unit._architecture_helpers import (
    SELECT_CHAIN_METHODS,
    assert_violations_match_allowlist,
    call_callee_name,
    filter_by_entity,
    iter_queries,
    orm_classes,
    repo_root,
    scan_rows,
    select_chain,
)

# (relpath, enclosing function per ``iter_scopes``, "Model.kwarg"). Shrink-only.
ALLOWLIST: set[tuple[str, str, str]] = set()

#: The trees the guard grades. The reach test scans the same ones.
_ROOTS = ("src", "scripts")


def _scan[Row](detector: Callable[[ast.Module, str], Iterable[Row]]) -> list[Row]:
    return scan_rows([repo_root() / root for root in _ROOTS], detector)


def _graded_sites(tree: ast.Module, relpath: str) -> Iterator[tuple[str, str, ast.Call]]:
    """``(function, model, filter_by call)`` for every site whose entity resolves; *function*
    is the one ``iter_scopes`` gives the site's scope.

    In an opaque query a site reached through the name may see another statement, so it is
    skipped, never guessed; one written on the statement root itself reads no name and is
    graded (``stmt = select(M).filter_by(...)`` in a name bound in two branches).
    """
    for query in iter_queries(tree, relpath):
        for call in query.filters:
            if call_callee_name(call) != "filter_by":
                continue
            if query.opaque and select_chain(call, SELECT_CHAIN_METHODS)[0] is not query.root:
                continue
            if (model := filter_by_entity(query, call)) is not None:
                yield query.holder, model, call


def _find_in_module(tree: ast.Module, relpath: str) -> set[tuple[str, str, str]]:
    found: set[tuple[str, str, str]] = set()
    for function, model, call in _graded_sites(tree, relpath):
        mapped = set(inspect(orm_classes()[model]).all_orm_descriptors.keys())
        for kw in call.keywords:
            if kw.arg is not None and kw.arg not in mapped:
                found.add((relpath, function, f"{model}.{kw.arg}"))
    return found


def _find_phantom_filter_by() -> set[tuple[str, str, str]]:
    return set(_scan(_find_in_module))


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
    "module-attribute": (
        "import src.core.database.models as models\ndef f():\n    return select(models.User).filter_by(nope=1)\n",
        {("f", "User.nope")},
    ),
    "join-on-a-module-attribute": (
        "from src.core.database import models\n"
        "def f():\n    return select(Tenant).join(models.User).filter_by(subdomain='x')\n",
        {("f", "User.subdomain")},
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
    "written-on-the-root-of-a-name-bound-in-two-branches": (
        "def f(c):\n    if c:\n        s = select(User).filter_by(nope=1)\n    else:\n"
        "        s = select(Tenant).filter_by(nope=2)\n    return s\n",
        {("f", "User.nope"), ("f", "Tenant.nope")},
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
    # An import binds the scope that holds it, as Python binds it.
    "an-import-in-one-function-binds-nothing-in-another": (
        "def f():\n    from src.core.database.models import Product\n    return select(Product).filter_by(nope=1)\n\n"
        "def g():\n    return select(Tenant).join(Product).filter_by(nope=2)\n",
        {("f", "Product.nope")},
    ),
    "a-local-import-shadows-the-module-import-in-its-function-only": (
        "def f():\n    from src.core.schemas import User\n    return select(User).filter_by(nope=1)\n\n"
        "def g():\n    return select(User).filter_by(nope=2)\n",
        {("g", "User.nope")},
    ),
    # Valid queries: nothing reported.
    "mapped-column-and-relationship": ("def f(t):\n    return select(User).filter_by(email='a', tenant=t)\n", set()),
    "join-then-a-joined-column": ("def f():\n    return select(Tenant).join(User).filter_by(email='x')\n", set()),
    "joined-accumulator": (
        "def f():\n    s = select(Tenant)\n    s = s.join(User)\n    return s.filter_by(email='x')\n",
        set(),
    ),
    # A keyword is looked up on the entity at the call, from the calls before it.
    "a-later-join-does-not-move-an-earlier-filter_by": (
        "def f():\n    s = select(Tenant)\n    s = s.filter_by(subdomain='x')\n    s = s.join(User)\n    return s\n",
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
    "pydantic-namesake": (
        "from src.core.schemas import Product\ndef f():\n    return select(Product).filter_by(nope=1)\n",
        set(),
    ),
}

#: Real sites the live scan must grade: a 2.0 ``select(M)`` and a legacy ``db.query(M)`` under
#: ``src/``, and a site under ``scripts/``, so a root dropped from ``_ROOTS`` fails here.
_REACHED = {
    ("src/core/database/repositories/principal.py", "Principal"),  # select(M)
    ("src/admin/blueprints/operations.py", "MediaBuy"),  # legacy db.query(M)
    ("scripts/setup/init_database_ci.py", "Tenant"),
}


class TestDetectorMetaTests:
    """The LIVE detector fires on the #2206 shape and stays quiet on valid queries."""

    @pytest.mark.arch_guard
    def test_mapper_inventory_is_not_empty(self) -> None:
        assert "User" in orm_classes() and "Tenant" in orm_classes()

    @pytest.mark.arch_guard
    @pytest.mark.parametrize("label", sorted(_TABLE))
    def test_rows_per_shape(self, label: str) -> None:
        source, rows = _TABLE[label]
        expected = {("x.py", scope, attr) for scope, attr in rows}
        assert _find_in_module(ast.parse(_PREAMBLE + source), "x.py") == expected

    @pytest.mark.arch_guard
    def test_live_scan_reaches_real_sites(self) -> None:
        """The scan the guard runs grades real sites, through both spellings and both roots."""
        graded = set(_scan(lambda tree, relpath: ((relpath, model) for _, model, _ in _graded_sites(tree, relpath))))
        assert _REACHED <= graded, f"the live scan no longer grades {sorted(_REACHED - graded)}"
