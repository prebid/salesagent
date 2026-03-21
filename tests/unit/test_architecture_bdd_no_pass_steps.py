"""Guard: BDD Then step functions must not have empty bodies.

A Then step with ``pass`` or no statements (only docstring) claims to verify
behavior but asserts nothing. This is the #1 BDD step quality failure mode.

Scanning approach: AST — find functions decorated with ``@then(...)`` in
``tests/bdd/steps/`` and check that the body contains at least one ``assert``
statement or a function call (delegation to a helper that asserts).

beads: beads-5rt
"""

from __future__ import annotations

import ast
from pathlib import Path

_BDD_STEPS_DIR = Path(__file__).resolve().parents[1] / "bdd" / "steps"


def _is_then_decorated(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Check if function is decorated with @then(...)."""
    for dec in func.decorator_list:
        if isinstance(dec, ast.Call):
            func_node = dec.func
            if isinstance(func_node, ast.Name) and func_node.id == "then":
                return True
        if isinstance(dec, ast.Name) and dec.id == "then":
            return True
    return False


def _body_is_empty(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Check if function body is effectively empty (pass, ellipsis, or docstring-only)."""
    stmts = func.body
    # Filter out docstring (first Expr with Constant str)
    effective = []
    for i, stmt in enumerate(stmts):
        if (
            i == 0
            and isinstance(stmt, ast.Expr)
            and isinstance(stmt.value, ast.Constant)
            and isinstance(stmt.value.value, str)
        ):
            continue  # skip docstring
        effective.append(stmt)

    if not effective:
        return True
    # Only pass or Ellipsis
    if len(effective) == 1:
        s = effective[0]
        if isinstance(s, ast.Pass):
            return True
        if isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant) and s.value.value is ...:
            return True
    return False


def _body_has_assert_or_call(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Check if function body contains an assert statement or a function call."""
    for node in ast.walk(func):
        if isinstance(node, ast.Assert):
            return True
        if isinstance(node, ast.Call):
            return True
        if isinstance(node, ast.Raise):
            return True
    return False


def _scan_bdd_steps() -> list[str]:
    """Find Then steps with empty or assertion-free bodies."""
    violations = []
    for py_file in sorted(_BDD_STEPS_DIR.rglob("*.py")):
        if py_file.name.startswith("_"):
            continue
        source = py_file.read_text()
        tree = ast.parse(source, filename=str(py_file))
        relative = py_file.relative_to(_BDD_STEPS_DIR.parent.parent)

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not _is_then_decorated(node):
                continue
            if _body_is_empty(node):
                violations.append(f"{relative}:{node.lineno} {node.name} — empty body (pass/docstring-only)")
            elif not _body_has_assert_or_call(node):
                violations.append(f"{relative}:{node.lineno} {node.name} — no assert or function call")

    return violations


class TestBddNoPassSteps:
    """Structural guard: Then steps must assert something."""

    def test_no_empty_then_steps(self):
        """Every @then step must contain an assert, function call, or raise."""
        violations = _scan_bdd_steps()
        assert not violations, f"Found {len(violations)} Then step(s) with empty/assertion-free bodies:\n" + "\n".join(
            f"  {v}" for v in violations
        )
