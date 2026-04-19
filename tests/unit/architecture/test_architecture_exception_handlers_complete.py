"""Structural guard (DORMANT at L0): ``src/app.py`` registers the full set of
exception handlers required by L1a.

Per CLAUDE.md invariant #3:

> ``@app.exception_handler(AdCPError)`` must be Accept-aware (render HTML for
> ``/admin/*`` browsers, JSON otherwise).

The L1a target is SIX handlers: AdCPError + 5 framework-level handlers
(HTTPException, RequestValidationError, StarletteHTTPException, generic
``Exception``, and one migration-specific). At L0 the repo has exactly ONE
(``AdCPError``). This guard is DORMANT at L0: it passes when the count is in
``{0, 1}`` (documenting the pre-L1a state) and activates in L1a by requiring
exactly six.

Meta-guard: scanner correctly counts ``@app.exception_handler(...)``
decorators on a planted fixture.

Per `.claude/notes/flask-to-fastapi/L0-implementation-plan-v2.md` §L0-01
row #5 of the §5.5 Structural Guards Inventory.
"""

from __future__ import annotations

import ast

import pytest

from tests.unit.architecture._ast_helpers import (
    FIXTURES_DIR,
    SRC,
)

FIXTURE = FIXTURES_DIR / "test_exception_handlers_complete_meta_fixture.py.txt"
APP_PY = SRC / "app.py"
L0_ALLOWED_COUNTS = frozenset({0, 1})
L1A_REQUIRED_COUNT = 6


def _count_exception_handlers(tree: ast.AST) -> int:
    """Count ``@app.exception_handler(...)`` decorators on any async or sync def."""
    count = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for deco in node.decorator_list:
            if not isinstance(deco, ast.Call):
                continue
            func = deco.func
            # Accept both ``app.exception_handler(...)`` and any ``X.exception_handler(...)``
            # — the attribute name is the distinguishing feature.
            if isinstance(func, ast.Attribute) and func.attr == "exception_handler":
                count += 1
    return count


def test_exception_handler_count_is_dormant_at_l0() -> None:
    """At L0, ``src/app.py`` MAY have 0 or 1 handlers. L1a will require 6."""
    assert APP_PY.exists(), f"{APP_PY} not found — L0 baseline changed unexpectedly."
    tree = ast.parse(APP_PY.read_text(encoding="utf-8"))
    count = _count_exception_handlers(tree)
    assert count in L0_ALLOWED_COUNTS, (
        f"src/app.py has {count} @app.exception_handler decorators. At L0 the "
        f"allowed range is {sorted(L0_ALLOWED_COUNTS)}. If you are landing L1a, "
        f"update this guard to require exactly {L1A_REQUIRED_COUNT}."
    )


def test_meta_fixture_exists() -> None:
    assert FIXTURE.exists(), f"Meta-fixture missing at {FIXTURE}."


def test_detector_catches_meta_fixture() -> None:
    tree = ast.parse(FIXTURE.read_text(encoding="utf-8"))
    count = _count_exception_handlers(tree)
    assert (
        count == 1
    ), f"AST scanner miscounted exception handlers in {FIXTURE.name} (expected 1, got {count}). Guard is broken."


@pytest.mark.parametrize(
    "snippet,expected",
    [
        (
            "@app.exception_handler(X)\ndef h(r, e): pass\n",
            1,
        ),
        (
            "@app.exception_handler(X)\nasync def h(r, e): pass\n@other.exception_handler(Y)\ndef g(r, e): pass\n",
            2,
        ),
        ("def plain(): pass\n", 0),
        # Bare decorator without a call is NOT counted.
        ("@exception_handler\ndef h(): pass\n", 0),
    ],
)
def test_detector_behavior(snippet: str, expected: int) -> None:
    tree = ast.parse(snippet)
    assert _count_exception_handlers(tree) == expected
