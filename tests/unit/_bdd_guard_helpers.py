"""Shared helpers for BDD structural guard tests.

Extracts common AST scanning patterns used by multiple guard test files
to avoid code duplication (DRY invariant).
"""

from __future__ import annotations

import ast
from pathlib import Path

BDD_STEPS_DIR = Path(__file__).resolve().parents[1] / "bdd" / "steps"


def is_then_decorated(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Check if function is decorated with @then(...)."""
    for dec in func.decorator_list:
        if isinstance(dec, ast.Call):
            func_node = dec.func
            if isinstance(func_node, ast.Name) and func_node.id == "then":
                return True
        if isinstance(dec, ast.Name) and dec.id == "then":
            return True
    return False


def iter_then_functions() -> list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    """Return (relative_path:lineno func_name, func_node) for all Then steps."""
    results = []
    for py_file in sorted(BDD_STEPS_DIR.rglob("*.py")):
        if py_file.name.startswith("_"):
            continue
        source = py_file.read_text()
        tree = ast.parse(source, filename=str(py_file))
        relative = py_file.relative_to(BDD_STEPS_DIR.parent.parent)

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not is_then_decorated(node):
                continue
            key = f"{relative}:{node.lineno} {node.name}"
            results.append((key, node))
    return results


def scan_then_steps_for_violations(
    detector: object,
) -> list[str]:
    """Run a detector across all Then steps, return violation keys."""
    violations = []
    for key, func in iter_then_functions():
        if detector(func):  # type: ignore[operator]
            violations.append(key)
    return violations
