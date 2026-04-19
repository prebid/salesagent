"""Structural guard (Captured→shrink): no NEW module-scope ``create_app()``
calls in ``tests/``.

Flask tests historically called ``create_app()`` at module scope to cache a
singleton app. That pattern:

* Fires the Flask extension stack at import time, breaking FastAPI TestClient
  composition.
* Pins the test to the Flask build of ``src/admin/app``, blocking the
  migration.

This guard seeds the current three call sites into
``tests/unit/architecture/allowlists/module_scope_create_app.txt`` and
enforces that the list MAY shrink but MUST NOT grow. Retires at L2 when
the Flask admin app is removed entirely.

Meta-guard: planted fixture with a module-scope ``create_app()`` call fires
the detector.

Per `.claude/notes/flask-to-fastapi/L0-implementation-plan-v2.md` §L0-01
row #11 of the §5.5 Structural Guards Inventory.
"""

from __future__ import annotations

import ast

import pytest

from tests.unit.architecture._ast_helpers import (
    FIXTURES_DIR,
    TESTS,
    find_stale_allowlist_entries,
    iter_python_files,
    read_allowlist,
    relpath,
)

ALLOWLIST_FILE = "module_scope_create_app.txt"
FIXTURE = FIXTURES_DIR / "test_no_module_scope_create_app_meta_fixture.py.txt"


def _has_module_scope_create_app(tree: ast.AST) -> bool:
    if not isinstance(tree, ast.Module):
        return False
    for node in tree.body:
        # Look at every top-level expression / assignment value.
        candidates: list[ast.AST] = []
        if isinstance(node, ast.Expr):
            candidates.append(node.value)
        elif isinstance(node, ast.Assign):
            candidates.append(node.value)
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            candidates.append(node.value)
        for c in candidates:
            if _is_create_app_call(c):
                return True
    return False


def _is_create_app_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name) and func.id == "create_app":
        return True
    if isinstance(func, ast.Attribute) and func.attr == "create_app":
        return True
    return False


def _file_has_module_scope_create_app(path) -> bool:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return False
    return _has_module_scope_create_app(tree)


def test_no_new_module_scope_create_app() -> None:
    allowlist = read_allowlist(ALLOWLIST_FILE)
    violations = {relpath(p) for p in iter_python_files([TESTS]) if _file_has_module_scope_create_app(p)}
    new_violations = violations - allowlist
    assert not new_violations, (
        "Module-scope `create_app()` calls found in tests/. This Flask-era "
        "pattern breaks FastAPI TestClient composition. Use a fixture that "
        "builds the app per-test instead. Offending files:\n" + "\n".join(f"  - {v}" for v in sorted(new_violations))
    )


def test_allowlist_shrinks_never_grows() -> None:
    stale = find_stale_allowlist_entries(
        ALLOWLIST_FILE,
        still_violates=_file_has_module_scope_create_app,
        removal_reason="no longer has module-scope create_app()",
    )
    assert not stale, "Stale entries in module_scope_create_app.txt:\n" + "\n".join(f"  - {s}" for s in stale)


def test_meta_fixture_exists() -> None:
    assert FIXTURE.exists(), f"Meta-fixture missing at {FIXTURE}."


def test_detector_catches_meta_fixture() -> None:
    tree = ast.parse(FIXTURE.read_text(encoding="utf-8"))
    assert _has_module_scope_create_app(
        tree
    ), f"AST scanner FAILED to detect module-scope create_app() in {FIXTURE.name}."


@pytest.mark.parametrize(
    "snippet,expected",
    [
        ("app = create_app()\n", True),
        ("app = mod.create_app()\n", True),
        ("x = create_app()\n", True),
        ("def f():\n    app = create_app()\n", False),  # nested, not module scope
        ("class C:\n    app = create_app()\n", False),  # class body, not module
        ("def create_app(): pass\n", False),  # defining, not calling
        ("from x import create_app\n", False),
    ],
)
def test_detector_behavior(snippet: str, expected: bool) -> None:
    assert _has_module_scope_create_app(ast.parse(snippet)) is expected
