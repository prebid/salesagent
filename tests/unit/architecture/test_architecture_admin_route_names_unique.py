"""Structural guard (DORMANT at L0): no two admin routes share a ``name=``.

Duplicate route names make ``url_for('name')`` resolve ambiguously.
Starlette picks one arbitrarily, which silently breaks reverse-URL
generation. This guard enforces global uniqueness of the ``name=`` keyword
across every ``@router.<method>`` decorator under ``src/admin/routers/``.

At L0 no FastAPI admin routers exist yet — the scanner returns nothing,
the test passes vacuously. Meta-guard plants a fixture with two route
decorators that share ``name=``.

Per `.claude/notes/flask-to-fastapi/L0-implementation-plan-v2.md` §L0-01
row #10 of the §5.5 Structural Guards Inventory.
"""

from __future__ import annotations

import ast
from collections import Counter

import pytest

from tests.unit.architecture._ast_helpers import (
    FIXTURES_DIR,
    SRC,
    iter_python_files,
    relpath,
)

FIXTURE = FIXTURES_DIR / "test_admin_route_names_unique_meta_fixture.py.txt"
ADMIN_ROUTERS_ROOT = SRC / "admin" / "routers"
# FastAPI route decorator methods only — see sibling guard
# test_architecture_admin_routes_named.py for rationale. Flask's
# `@bp.route(...)` is out of scope until the router is ported.
HTTP_METHODS = {"get", "post", "put", "delete", "patch", "head", "options", "api_route"}


def _collect_route_names(tree: ast.AST, relpath_str: str) -> list[tuple[str, str, int]]:
    """Return (name, relpath, lineno) for every route decorator carrying a string ``name=``."""
    out: list[tuple[str, str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for deco in node.decorator_list:
            if not isinstance(deco, ast.Call):
                continue
            func = deco.func
            if not isinstance(func, ast.Attribute) or func.attr not in HTTP_METHODS:
                continue
            for kw in deco.keywords:
                if kw.arg == "name" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                    out.append((kw.value.value, relpath_str, deco.lineno))
    return out


def test_admin_route_names_are_unique() -> None:
    all_names: list[tuple[str, str, int]] = []
    for path in iter_python_files([ADMIN_ROUTERS_ROOT]):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        all_names.extend(_collect_route_names(tree, relpath(path)))

    counts = Counter(name for name, _, _ in all_names)
    dupes = {name for name, count in counts.items() if count > 1}
    if not dupes:
        return
    report = []
    for name in sorted(dupes):
        sites = [f"{p}:{ln}" for n, p, ln in all_names if n == name]
        report.append(f"  - {name}:\n" + "\n".join(f"      at {s}" for s in sorted(sites)))
    raise AssertionError(
        "Admin route names must be globally unique — duplicates make url_for() "
        "resolve arbitrarily. Offending names:\n" + "\n".join(report)
    )


def test_meta_fixture_exists() -> None:
    assert FIXTURE.exists(), f"Meta-fixture missing at {FIXTURE}."


def test_detector_catches_meta_fixture() -> None:
    tree = ast.parse(FIXTURE.read_text(encoding="utf-8"))
    names = _collect_route_names(tree, FIXTURE.name)
    counts = Counter(n for n, _, _ in names)
    dupes = [n for n, c in counts.items() if c > 1]
    assert dupes == [
        "admin_dupe"
    ], f"Detector FAILED to flag duplicate name in {FIXTURE.name} (got dupes={dupes}). Guard is broken."


@pytest.mark.parametrize(
    "snippet,expected_dupes",
    [
        (
            "@router.get('/a', name='x')\ndef f(): pass\n@router.get('/b', name='x')\ndef g(): pass\n",
            {"x"},
        ),
        (
            "@router.get('/a', name='x')\ndef f(): pass\n@router.get('/b', name='y')\ndef g(): pass\n",
            set(),
        ),
        ("def f(): pass\n", set()),
    ],
)
def test_detector_behavior(snippet: str, expected_dupes: set[str]) -> None:
    tree = ast.parse(snippet)
    names = _collect_route_names(tree, "snippet")
    counts = Counter(n for n, _, _ in names)
    dupes = {n for n, c in counts.items() if c > 1}
    assert dupes == expected_dupes
