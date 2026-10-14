"""Guard: ``filter_by()`` names only attributes the model maps.

Disease pattern (GH #2206): ``select(User).filter_by(..., is_admin=True)`` in
``src/admin/utils/helpers.py`` named a column ``User`` never had. SQLAlchemy raises
``InvalidRequestError`` at CALL time, not import time, so nothing short of executing
the query notices -- and the one caller wrapped it in ``except Exception: return
False``, so the function denied every non-super-admin for fourteen months.
``filter_by`` takes attribute NAMES as keyword strings, which is exactly what a static
scan can check against the live mapper.

Detector: every ``filter_by(**kw)`` call under ``src/`` whose receiver chain bottoms
out in ``select(<OrmModel>)`` -- spelled ``Model``, ``mod.Model`` or an import alias
such as ``DBUser`` -- directly, or through a same-function accumulator
(``stmt = select(M)...; stmt = stmt.filter_by(...)``). Each keyword must be in
``sqlalchemy.inspect(M).attrs``: columns and relationships. (A hybrid property or
synonym is accepted by ``filter_by`` but absent from ``attrs``; none exists in
``models.py`` today, and the first one gets a row below.) Only the installed mapper is
consulted, so the inventory cannot go stale.

Skipped, stated rather than hidden (measured 2026-10-14: 318 ``filter_by`` sites, 298
graded): a chain through ``join`` / ``outerjoin`` / ``join_from`` / ``select_from`` (10),
because ``filter_by`` then targets the last joined entity; a legacy ``session.query(M)``
chain (4, the SQLAlchemy 1.x pre-commit hook's territory); a ``select(M.column)``
projection (3); a ``select(model)`` whose model is a parameter (2); a
``select(func.count())`` aggregate (1). Resolving a parameter-fed receiver is a
follow-up; this guard is the one that catches the #2206 shape.

No allowlist: the only violation at creation was the #2206 site, deleted in the same
change. A row added here needs a ``# FIXME(#<gh-issue>)`` at the source.
"""

from __future__ import annotations

import ast

import pytest
from sqlalchemy import inspect

import src.core.database.models as _models
from tests.unit._architecture_helpers import (
    assert_detector_catches_ast_snippets,
    assert_violations_match_allowlist,
    import_map,
    parse_module,
    repo_root,
    select_call_model_name,
    src_python_files,
)

# Every mapped class the models module exports; a plain base/mixin has no __table__.
_ORM: dict[str, type] = {
    name: cls for name, cls in vars(_models).items() if isinstance(cls, type) and hasattr(cls, "__table__")
}

# (relpath, enclosing function, "Model.kwarg"). Shrink-only.
ALLOWLIST: set[tuple[str, str, str]] = set()

# After one of these, ``Select.filter_by`` targets the LAST joined entity, which this scan
# cannot tell apart from the select target: such a chain is skipped, never misattributed.
_JOIN_CALLS = frozenset({"join", "outerjoin", "join_from", "select_from"})


def _chain_root(node: ast.expr) -> ast.expr | None:
    """``select(M).where(...).filter_by(...)`` -> ``select(M)``; ``stmt.filter_by(...)`` -> ``stmt``.

    None for a chain that joins another entity (see ``_JOIN_CALLS``).
    """
    while isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        if node.func.attr in _JOIN_CALLS:
            return None
        node = node.func.value
    return node


def _model_of(root: ast.expr | None, env: dict[str, type], aliases: dict[str, str]) -> type | None:
    """The ORM class a chain root selects, or None when it cannot be told statically.

    ``aliases`` maps a module's local import names back to the models module's own
    (``from ...models import User as DBUser`` -> ``{"DBUser": "User"}``), the spelling
    that hid four raw selects from the raw-select guard until #2128.
    """
    if isinstance(root, ast.Call):
        name = select_call_model_name(root)
        return _ORM.get(aliases.get(name, name)) if name else None
    if isinstance(root, ast.Name):
        return env.get(root.id)
    return None


def _phantom_kwargs_in(
    fn: ast.FunctionDef | ast.AsyncFunctionDef, relpath: str, aliases: dict[str, str]
) -> set[tuple[str, str, str]]:
    # First pass: ``name = select(M)...`` and ``name = name.filter_by(...)`` bind name -> M.
    env: dict[str, type] = {}
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            model = _model_of(_chain_root(node.value), env, aliases)
            if model is not None:
                env[node.targets[0].id] = model
    # Second pass: every filter_by whose receiver resolves to a model.
    found: set[tuple[str, str, str]] = set()
    for node in ast.walk(fn):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "filter_by"):
            continue
        model = _model_of(_chain_root(node.func.value), env, aliases)
        if model is None:
            continue
        mapped = set(inspect(model).attrs.keys())
        for kw in node.keywords:
            if kw.arg is not None and kw.arg not in mapped:
                found.add((relpath, fn.name, f"{model.__name__}.{kw.arg}"))
    return found


def _find_in_module(tree: ast.Module, relpath: str) -> set[tuple[str, str, str]]:
    aliases = {
        local: original for local, (module, original) in import_map(relpath, tree).items() if module == _models.__name__
    }
    found: set[tuple[str, str, str]] = set()
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found |= _phantom_kwargs_in(fn, relpath, aliases)
    return found


def _find_phantom_filter_by() -> set[tuple[str, str, str]]:
    repo = repo_root()
    found: set[tuple[str, str, str]] = set()
    for path in src_python_files(repo):
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


_PREAMBLE = "from sqlalchemy import select\nfrom src.core.database.models import Tenant, User\n"

_KNOWN_BAD = {
    "phantom-column": _PREAMBLE
    + "def f(e):\n    return select(User).filter_by(email=e, is_active=True, is_admin=True)\n",
    "accumulator": _PREAMBLE
    + "def f():\n    stmt = select(User)\n    stmt = stmt.filter_by(is_admin=True)\n    return stmt\n",
    "alias-import": (
        "from sqlalchemy import select\nfrom src.core.database.models import User as DBUser\n"
        "def f():\n    return select(DBUser).filter_by(is_admin=True)\n"
    ),
}


class TestDetectorMetaTests:
    """The LIVE detector fires on the #2206 shape and stays quiet on valid queries."""

    @pytest.mark.arch_guard
    def test_mapper_inventory_is_not_empty(self) -> None:
        assert "User" in _ORM and "Tenant" in _ORM

    @pytest.mark.arch_guard
    def test_detector_catches_known_bad(self) -> None:
        assert_detector_catches_ast_snippets(lambda tree: sorted(_find_in_module(tree, "x.py")), snippets=_KNOWN_BAD)

    @pytest.mark.arch_guard
    def test_known_bad_rows_name_the_function_and_attribute(self) -> None:
        for source in _KNOWN_BAD.values():
            assert _find_in_module(ast.parse(source), "x.py") == {("x.py", "f", "User.is_admin")}

    @pytest.mark.arch_guard
    def test_mapped_column_and_relationship_are_not_flagged(self) -> None:
        source = _PREAMBLE + "def f(t):\n    return select(User).filter_by(email='a', tenant=t)\n"
        assert _find_in_module(ast.parse(source), "x.py") == set()

    @pytest.mark.arch_guard
    def test_joined_chain_is_skipped_not_misattributed(self) -> None:
        # Select.filter_by targets the last joined entity; the guard cannot tell which,
        # so a joined chain is unresolved rather than graded against the select target.
        source = _PREAMBLE + "def f():\n    return select(Tenant).join(User).filter_by(email='x')\n"
        assert _find_in_module(ast.parse(source), "x.py") == set()
