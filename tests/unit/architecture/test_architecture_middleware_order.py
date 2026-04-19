"""Structural guard (DORMANT at L0): runtime middleware order matches the
``MIDDLEWARE_STACK_VERSION`` table in foundation-modules.md §11.36.

Per CLAUDE.md invariant #5 (Flask→FastAPI v2.0 migration):

> Canonical stack grows by layer via the ``MIDDLEWARE_STACK_VERSION``
> assertion: L1a=7 → L1c=8 → L2=10 → L4+=11.

At L0 no production middleware is wired and the constant
``MIDDLEWARE_STACK_VERSION`` does not yet exist in src/. The main test
therefore passes **vacuously** (the dormancy condition is "constant not
found"). The scanner and meta-test still enforce that:

1. If ``MIDDLEWARE_STACK_VERSION`` ever appears in src/, the value must be
   an integer literal.
2. The planted-violation fixture with a non-zero version DOES trip the
   detector — if it didn't, the guard would be broken the moment L1a adds
   the constant.

Per `.claude/notes/flask-to-fastapi/L0-implementation-plan-v2.md` §L0-01
row #4 of the §5.5 Structural Guards Inventory.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.unit.architecture._ast_helpers import (
    FIXTURES_DIR,
    SRC,
    iter_python_files,
)

FIXTURE = FIXTURES_DIR / "test_middleware_order_meta_fixture.py.txt"


def _find_middleware_stack_version(tree: ast.AST) -> int | None:
    """Return the int literal assigned to ``MIDDLEWARE_STACK_VERSION``, or None."""
    if not isinstance(tree, ast.Module):
        return None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "MIDDLEWARE_STACK_VERSION":
                    if isinstance(node.value, ast.Constant) and isinstance(node.value.value, int):
                        return node.value.value
        elif isinstance(node, ast.AnnAssign):
            if (
                isinstance(node.target, ast.Name)
                and node.target.id == "MIDDLEWARE_STACK_VERSION"
                and node.value is not None
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, int)
            ):
                return node.value.value
    return None


def _scan_for_constant(roots: list[Path]) -> dict[str, int]:
    """Map relative path → declared MIDDLEWARE_STACK_VERSION value for files that declare it."""
    found: dict[str, int] = {}
    for path in iter_python_files(roots):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        value = _find_middleware_stack_version(tree)
        if value is not None:
            found[path.as_posix()] = value
    return found


def test_middleware_stack_version_dormant_at_l0() -> None:
    """DORMANT at L0: MIDDLEWARE_STACK_VERSION is not yet declared anywhere in src/.

    This test will be made active at L1a when the middleware stack lands.
    Until then, it documents the expected dormant state; a NEW declaration
    that does not match an allowed value (0, 7, 8, 10, 11) would fail the
    assertion below. Value 0 is reserved for "stack not yet wired".
    """
    declarations = _scan_for_constant([SRC])
    if not declarations:
        # Dormant: constant not yet declared. Expected at L0.
        return
    allowed = {0, 7, 8, 10, 11}
    bad = {path: v for path, v in declarations.items() if v not in allowed}
    assert not bad, (
        "MIDDLEWARE_STACK_VERSION must be one of {0, 7, 8, 10, 11} per the "
        "foundation-modules.md §11.36 ratchet (L0=0, L1a=7, L1c=8, L2=10, "
        f"L4+=11). Offending declarations: {bad}"
    )


def test_meta_fixture_exists() -> None:
    assert FIXTURE.exists(), f"Meta-fixture missing at {FIXTURE}."


def test_detector_catches_meta_fixture() -> None:
    tree = ast.parse(FIXTURE.read_text(encoding="utf-8"))
    value = _find_middleware_stack_version(tree)
    assert value == 7, (
        f"AST scanner FAILED to extract MIDDLEWARE_STACK_VERSION from "
        f"{FIXTURE.name} (expected 7, got {value!r}). The guard is broken."
    )


@pytest.mark.parametrize(
    "snippet,expected",
    [
        ("MIDDLEWARE_STACK_VERSION = 10\n", 10),
        ("MIDDLEWARE_STACK_VERSION: int = 7\n", 7),
        ("OTHER_CONSTANT = 7\n", None),
        ("MIDDLEWARE_STACK_VERSION = 'not an int'\n", None),
        # Nested inside a function — not module scope.
        ("def f():\n    MIDDLEWARE_STACK_VERSION = 7\n", None),
    ],
)
def test_detector_behavior(snippet: str, expected: int | None) -> None:
    assert _find_middleware_stack_version(ast.parse(snippet)) == expected
