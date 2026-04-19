"""Structural guard (DORMANT at L0): every admin ``APIRouter`` declares
``include_in_schema=False``.

Per CLAUDE.md invariant: admin routes must be omitted from the OpenAPI
schema — they are an internal surface and should not appear alongside
AdCP tools in public docs.

At L0 ``src/admin/routers/*.py`` files are still Flask-era ``Blueprint``
declarations. The scanner only flags a violation if an ``APIRouter(...)``
call under ``src/admin/routers/`` omits ``include_in_schema=False``. Until
FastAPI routers actually land under that tree, this test passes vacuously.

Meta-guard: planted fixture with a bare ``APIRouter(prefix='/admin')``
trips the detector.

Per `.claude/notes/flask-to-fastapi/L0-implementation-plan-v2.md` §L0-01
row #8 of the §5.5 Structural Guards Inventory.
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

FIXTURE = FIXTURES_DIR / "test_admin_routes_excluded_from_openapi_meta_fixture.py.txt"
ADMIN_ROUTERS_ROOT = SRC / "admin" / "routers"


def _find_apirouter_calls(tree: ast.AST) -> list[ast.Call]:
    """Return every ``APIRouter(...)`` call node at any nesting level."""
    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "APIRouter":
                calls.append(node)
            elif isinstance(func, ast.Attribute) and func.attr == "APIRouter":
                calls.append(node)
    return calls


def _has_include_in_schema_false(call: ast.Call) -> bool:
    for kw in call.keywords:
        if kw.arg == "include_in_schema":
            if isinstance(kw.value, ast.Constant) and kw.value.value is False:
                return True
    return False


def test_admin_apirouters_exclude_schema() -> None:
    violations: list[str] = []
    for path in iter_python_files([ADMIN_ROUTERS_ROOT]):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for call in _find_apirouter_calls(tree):
            if not _has_include_in_schema_false(call):
                violations.append(f"{relpath(path)}:{call.lineno}")
    assert not violations, (
        "Admin APIRouter declarations must pass `include_in_schema=False`. "
        "Admin routes are internal and must not appear in OpenAPI docs. "
        "Offending sites:\n" + "\n".join(f"  - {v}" for v in sorted(violations))
    )


def test_meta_fixture_exists() -> None:
    assert FIXTURE.exists(), f"Meta-fixture missing at {FIXTURE}."


def test_detector_catches_meta_fixture() -> None:
    tree = ast.parse(FIXTURE.read_text(encoding="utf-8"))
    calls = _find_apirouter_calls(tree)
    assert calls, f"Detector failed to locate APIRouter(...) in {FIXTURE.name}."
    assert not any(
        _has_include_in_schema_false(c) for c in calls
    ), f"Detector FAILED to notice missing `include_in_schema=False` in {FIXTURE.name}. Guard is broken."


@pytest.mark.parametrize(
    "snippet,should_flag",
    [
        ("router = APIRouter()\n", True),
        ("router = APIRouter(prefix='/admin')\n", True),
        ("router = APIRouter(prefix='/admin', include_in_schema=False)\n", False),
        ("router = APIRouter(include_in_schema=False, prefix='/x')\n", False),
        # Boolean True value — should still flag.
        ("router = APIRouter(include_in_schema=True)\n", True),
    ],
)
def test_detector_behavior(snippet: str, should_flag: bool) -> None:
    calls = _find_apirouter_calls(ast.parse(snippet))
    assert calls, "Snippet must contain APIRouter(...)."
    flagged = not _has_include_in_schema_false(calls[0])
    assert flagged is should_flag
