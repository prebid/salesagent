"""Structural guard (Captured→shrink): no NEW module-scope engine creation.

Per the Flask→FastAPI v2.0 foundation-modules plan: engines must be
constructed inside a factory function, never at module scope. Module-scope
``create_engine(...)`` calls execute at import time, pinning connection
pool parameters to whatever the environment looks like at the moment
``import src.services.xxx`` fires — which is a frequent source of test
flakes (e.g. wrong DATABASE_URL cached into the pool).

The scanner looks for module-scope ``create_engine``,
``create_async_engine``, or ``AsyncEngine(...)`` calls anywhere under
``src/``. Current violations (two cached legacy engines in
``src/services/gam_*_service.py``) are seeded into
``tests/unit/architecture/allowlists/no_module_level_engine.txt``.

Meta-guard: planted fixture with a module-scope ``create_engine(...)`` call
trips the detector.

Per `.claude/notes/flask-to-fastapi/L0-implementation-plan-v2.md` §L0-01
row #15 of the §5.5 Structural Guards Inventory.
"""

from __future__ import annotations

import ast

import pytest

from tests.unit.architecture._ast_helpers import (
    FIXTURES_DIR,
    REPO_ROOT,
    SRC,
    iter_python_files,
    read_allowlist,
    relpath,
)

ALLOWLIST_FILE = "no_module_level_engine.txt"
FIXTURE = FIXTURES_DIR / "test_no_module_level_engine_meta_fixture.py.txt"

ENGINE_FACTORY_NAMES: frozenset[str] = frozenset({"create_engine", "create_async_engine", "AsyncEngine"})


def _is_engine_factory_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name) and func.id in ENGINE_FACTORY_NAMES:
        return True
    if isinstance(func, ast.Attribute) and func.attr in ENGINE_FACTORY_NAMES:
        return True
    return False


def _has_module_scope_engine(tree: ast.AST) -> bool:
    if not isinstance(tree, ast.Module):
        return False
    for node in tree.body:
        candidates: list[ast.AST] = []
        if isinstance(node, ast.Assign):
            candidates.append(node.value)
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            candidates.append(node.value)
        elif isinstance(node, ast.Expr):
            candidates.append(node.value)
        for c in candidates:
            if _is_engine_factory_call(c):
                return True
    return False


def _file_has(path) -> bool:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return False
    return _has_module_scope_engine(tree)


def test_no_new_module_scope_engine() -> None:
    allowlist = read_allowlist(ALLOWLIST_FILE)
    violations = {relpath(p) for p in iter_python_files([SRC]) if _file_has(p)}
    new_violations = violations - allowlist
    assert not new_violations, (
        "Module-scope `create_engine(...)`/`create_async_engine(...)` calls "
        "detected. Engines must be built inside a factory function so the "
        "DATABASE_URL at call time wins. Offending files:\n" + "\n".join(f"  - {v}" for v in sorted(new_violations))
    )


def test_allowlist_shrinks_never_grows() -> None:
    allowlist = read_allowlist(ALLOWLIST_FILE)
    stale: list[str] = []
    for rel in allowlist:
        path = REPO_ROOT / rel
        if not path.exists():
            stale.append(f"{rel} (file does not exist)")
            continue
        if not _file_has(path):
            stale.append(f"{rel} (no longer has module-scope engine — remove)")
    assert not stale, "Stale entries in no_module_level_engine.txt:\n" + "\n".join(f"  - {s}" for s in stale)


def test_meta_fixture_exists() -> None:
    assert FIXTURE.exists(), f"Meta-fixture missing at {FIXTURE}."


def test_detector_catches_meta_fixture() -> None:
    tree = ast.parse(FIXTURE.read_text(encoding="utf-8"))
    assert _has_module_scope_engine(tree), f"AST scanner FAILED to detect module-scope engine in {FIXTURE.name}."


@pytest.mark.parametrize(
    "snippet,expected",
    [
        ("engine = create_engine('postgresql://...')\n", True),
        ("engine = sa.create_async_engine('...')\n", True),
        ("engine: Engine = create_engine('...')\n", True),
        ("def f():\n    engine = create_engine('...')\n", False),  # factory-scoped
        ("class C:\n    engine = create_engine('...')\n", False),  # class body
        ("from sqlalchemy import create_engine\n", False),
    ],
)
def test_detector_behavior(snippet: str, expected: bool) -> None:
    assert _has_module_scope_engine(ast.parse(snippet)) is expected
