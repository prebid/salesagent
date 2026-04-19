"""Structural guard: ``src/app.py`` composes MCP and app lifespans via
``combine_lifespans``.

Per CLAUDE.md / foundation-modules.md:

> The root FastAPI app must use ``lifespan=combine_lifespans(app_lifespan,
> mcp_app.lifespan)`` so MCP scheduler startup hooks (delivery webhooks,
> media-buy status polling) fire alongside app-level hooks.

The scanner:

1. Parses ``src/app.py``.
2. Finds the first ``FastAPI(...)`` call.
3. Asserts it has a ``lifespan=`` keyword whose value is a ``Call`` to
   ``combine_lifespans``.

Meta-guard: planted fixture with a bare ``FastAPI(title='...')`` trips the
detector.

Per `.claude/notes/flask-to-fastapi/L0-implementation-plan-v2.md` §L0-01
row #12 of the §5.5 Structural Guards Inventory.
"""

from __future__ import annotations

import ast

import pytest

from tests.unit.architecture._ast_helpers import (
    FIXTURES_DIR,
    SRC,
)

FIXTURE = FIXTURES_DIR / "test_scheduler_lifespan_composition_meta_fixture.py.txt"
APP_PY = SRC / "app.py"


def _first_fastapi_call(tree: ast.AST) -> ast.Call | None:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "FastAPI":
                return node
            if isinstance(func, ast.Attribute) and func.attr == "FastAPI":
                return node
    return None


def _lifespan_uses_combine(call: ast.Call) -> bool:
    for kw in call.keywords:
        if kw.arg != "lifespan":
            continue
        value = kw.value
        if isinstance(value, ast.Call):
            func = value.func
            if isinstance(func, ast.Name) and func.id == "combine_lifespans":
                return True
            if isinstance(func, ast.Attribute) and func.attr == "combine_lifespans":
                return True
    return False


def test_src_app_lifespan_uses_combine_lifespans() -> None:
    assert APP_PY.exists(), f"{APP_PY} not found — L0 baseline changed unexpectedly."
    tree = ast.parse(APP_PY.read_text(encoding="utf-8"))
    call = _first_fastapi_call(tree)
    assert call is not None, "No FastAPI(...) call found in src/app.py."
    assert _lifespan_uses_combine(call), (
        "src/app.py must construct FastAPI with "
        "`lifespan=combine_lifespans(app_lifespan, mcp_app.lifespan)`. "
        "Without it, the MCP scheduler hooks never fire."
    )


def test_meta_fixture_exists() -> None:
    assert FIXTURE.exists(), f"Meta-fixture missing at {FIXTURE}."


def test_detector_catches_meta_fixture() -> None:
    tree = ast.parse(FIXTURE.read_text(encoding="utf-8"))
    call = _first_fastapi_call(tree)
    assert call is not None, f"Detector failed to locate FastAPI(...) in {FIXTURE.name}."
    assert not _lifespan_uses_combine(call), f"Detector FAILED to notice missing combine_lifespans in {FIXTURE.name}."


@pytest.mark.parametrize(
    "snippet,expected",
    [
        ("app = FastAPI()\n", False),
        ("app = FastAPI(title='x')\n", False),
        ("app = FastAPI(lifespan=my_lifespan)\n", False),
        ("app = FastAPI(lifespan=combine_lifespans(a, b))\n", True),
        ("app = FastAPI(lifespan=mod.combine_lifespans(a, b))\n", True),
    ],
)
def test_detector_behavior(snippet: str, expected: bool) -> None:
    tree = ast.parse(snippet)
    call = _first_fastapi_call(tree)
    assert call is not None
    assert _lifespan_uses_combine(call) is expected
