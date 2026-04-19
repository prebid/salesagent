"""Structural guard: no ``async with get_db_session()`` under ``src/admin/``.

Per Flask→FastAPI v2.0 invariant #4: admin handlers are sync through L4
and use sync SQLAlchemy. ``async with get_db_session()`` is a sign that
someone has prematurely flipped a handler to async SQLAlchemy — which is
a Layer 5+ change.

The scanner walks every ``src/admin/**/*.py`` for ``AsyncWith`` nodes whose
context-manager call is ``get_db_session``.

Meta-guard: planted fixture trips the detector.

Per `.claude/notes/flask-to-fastapi/L0-implementation-plan-v2.md` §L0-01
row #3 of the §5.5 Structural Guards Inventory.
"""

from __future__ import annotations

import ast

import pytest

from tests.unit.architecture._ast_helpers import (
    FIXTURES_DIR,
    SRC,
    iter_python_files,
    relpath,
)

FIXTURE = FIXTURES_DIR / "test_no_async_db_access_meta_fixture.py.txt"
ADMIN_ROOTS = [SRC / "admin"]


def _call_name(call: ast.Call) -> str | None:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _has_async_get_db_session(tree: ast.AST) -> list[int]:
    """Return line numbers of every ``async with get_db_session(...)`` in the tree."""
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncWith):
            for item in node.items:
                expr = item.context_expr
                if isinstance(expr, ast.Call) and _call_name(expr) == "get_db_session":
                    lines.append(node.lineno)
    return lines


def test_no_async_db_access_in_admin() -> None:
    violations: list[str] = []
    for path in iter_python_files(ADMIN_ROOTS):
        for lineno in _has_async_get_db_session(_parse_or_empty(path)):
            violations.append(f"{relpath(path)}:{lineno}")
    assert not violations, (
        "`async with get_db_session()` found under src/admin/. Admin handlers "
        "stay on sync SQLAlchemy through L4 (migration invariant #4). "
        "Offending sites:\n" + "\n".join(f"  - {v}" for v in sorted(violations))
    )


def _parse_or_empty(path) -> ast.AST:
    try:
        return ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return ast.Module(body=[], type_ignores=[])


def test_meta_fixture_exists() -> None:
    assert FIXTURE.exists(), f"Meta-fixture missing at {FIXTURE}."


def test_detector_catches_meta_fixture() -> None:
    tree = ast.parse(FIXTURE.read_text(encoding="utf-8"))
    assert _has_async_get_db_session(
        tree
    ), f"AST scanner FAILED to detect async get_db_session usage in {FIXTURE.name}."


@pytest.mark.parametrize(
    "snippet,should_match",
    [
        ("async def f():\n    async with get_db_session() as s: pass\n", True),
        ("async def f():\n    async with mod.get_db_session() as s: pass\n", True),
        # Sync with is fine.
        ("def f():\n    with get_db_session() as s: pass\n", False),
        # Async with for a different resource is fine.
        ("async def f():\n    async with httpx.AsyncClient() as c: pass\n", False),
    ],
)
def test_detector_behavior(snippet: str, should_match: bool) -> None:
    tree = ast.parse(snippet)
    matched = bool(_has_async_get_db_session(tree))
    assert matched is should_match, f"Mismatch on snippet: {snippet!r}"
