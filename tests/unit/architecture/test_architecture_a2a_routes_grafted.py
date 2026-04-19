"""Structural guard: A2A routes are TOP-LEVEL routes on ``app.router.routes``,
not a Mount.

Per `.claude/notes/flask-to-fastapi/`: A2A endpoints
(``/a2a``, ``/.well-known/agent-card.json``, ``/agent.json``) MUST be
grafted onto the root FastAPI app's route table. Mounting A2A as a
sub-application breaks middleware scope propagation — ``scope['state']``
populated by ``UnifiedAuthMiddleware`` would be lost at the Mount boundary.

The scanner checks ``src/app.py`` for:

1. An ``add_routes_to_app(app, ...)`` call (the sanctioned grafting API
   provided by ``A2AStarletteApplication``), AND
2. No ``Mount("/a2a", ...)`` append/insert on ``app.router.routes`` for the
   A2A paths.

Meta-guard: planted fixture that uses ``Mount("/a2a", ...)`` trips the
detector.

Per `.claude/notes/flask-to-fastapi/L0-implementation-plan-v2.md` §L0-01
row #13 of the §5.5 Structural Guards Inventory.
"""

from __future__ import annotations

import ast

import pytest

from tests.unit.architecture._ast_helpers import (
    FIXTURES_DIR,
    SRC,
)

FIXTURE = FIXTURES_DIR / "test_a2a_routes_grafted_meta_fixture.py.txt"
APP_PY = SRC / "app.py"
A2A_PATHS: frozenset[str] = frozenset({"/a2a", "/.well-known/agent-card.json", "/agent.json"})


def _has_add_routes_to_app(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "add_routes_to_app":
            return True
        if isinstance(func, ast.Name) and func.id == "add_routes_to_app":
            return True
    return False


def _has_a2a_mount(tree: ast.AST) -> bool:
    """Return True iff a ``Mount(<a2a-path>, ...)`` call is present."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = None
        if isinstance(func, ast.Name):
            name = func.id
        elif isinstance(func, ast.Attribute):
            name = func.attr
        if name != "Mount":
            continue
        if node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value in A2A_PATHS:
            return True
    return False


def test_src_app_grafts_a2a_routes() -> None:
    assert APP_PY.exists(), f"{APP_PY} not found."
    tree = ast.parse(APP_PY.read_text(encoding="utf-8"))
    assert _has_add_routes_to_app(
        tree
    ), "src/app.py must call `a2a_app.add_routes_to_app(app, ...)` to graft A2A routes onto the root FastAPI app."
    assert not _has_a2a_mount(tree), (
        "src/app.py must NOT Mount an A2A sub-application. A2A routes are "
        "top-level on app.router.routes; Mount() breaks middleware scope "
        "propagation."
    )


def test_meta_fixture_exists() -> None:
    assert FIXTURE.exists(), f"Meta-fixture missing at {FIXTURE}."


def test_detector_catches_meta_fixture() -> None:
    tree = ast.parse(FIXTURE.read_text(encoding="utf-8"))
    assert _has_a2a_mount(tree), f"Detector FAILED to notice Mount('/a2a', ...) in {FIXTURE.name}."


@pytest.mark.parametrize(
    "snippet,mount_expected",
    [
        ('Mount("/a2a", app=sub)\n', True),
        ('Mount("/.well-known/agent-card.json", app=sub)\n', True),
        ('Mount("/api/v1", app=sub)\n', False),
        ("Mount()\n", False),
    ],
)
def test_mount_detector_behavior(snippet: str, mount_expected: bool) -> None:
    tree = ast.parse(snippet)
    assert _has_a2a_mount(tree) is mount_expected
