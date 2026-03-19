"""Guard: BDD Given steps must not store raw dicts in ctx["registry_formats"].

Given steps should construct real Format objects directly via factories,
not raw dicts that require a bespoke deserializer (_dict_to_format).

Scanning approach: AST — find Given step functions that append dicts
(``{...}``) to ``ctx["registry_formats"]`` instead of Format objects.

beads: beads-7ka
"""

from __future__ import annotations

import ast
from pathlib import Path

_BDD_STEPS_DIR = Path(__file__).resolve().parents[1] / "bdd" / "steps"

# Files that contain Given steps populating registry_formats
_GIVEN_FILES = [
    _BDD_STEPS_DIR / "generic" / "given_entities.py",
    _BDD_STEPS_DIR / "generic" / "given_config.py",
]


def _is_given_decorated(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Check if function is decorated with @given(...)."""
    for dec in func.decorator_list:
        if isinstance(dec, ast.Call):
            func_node = dec.func
            if isinstance(func_node, ast.Name) and func_node.id == "given":
                return True
        if isinstance(dec, ast.Name) and dec.id == "given":
            return True
    return False


def _body_appends_dict_to_registry(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Check if function body appends a dict literal to ctx["registry_formats"].

    Detects patterns like:
        ctx["registry_formats"] = [{"name": ...}, ...]
        ctx.setdefault("registry_formats", []).append({"name": ...})
        ctx.setdefault("registry_formats", []).extend([{"name": ...}])
    """
    for node in ast.walk(func):
        # Assignment: ctx["registry_formats"] = [{...}, ...]
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if _is_registry_formats_access(target) and _value_contains_dict(node.value):
                    return True

        # .append({...}) or .extend([{...}])
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in ("append", "extend") and node.args:
                if _value_contains_dict(node.args[0]):
                    return True

    return False


def _is_registry_formats_access(node: ast.AST) -> bool:
    """Check if node accesses ctx["registry_formats"]."""
    if isinstance(node, ast.Subscript):
        if isinstance(node.slice, ast.Constant) and node.slice.value == "registry_formats":
            return True
    return False


def _value_contains_dict(node: ast.AST) -> bool:
    """Check if an expression contains a dict literal (at any nesting level)."""
    for child in ast.walk(node):
        if isinstance(child, ast.Dict):
            return True
    return False


def _scan_given_steps() -> list[str]:
    """Find Given steps that store raw dicts in registry_formats."""
    violations = []
    for py_file in _GIVEN_FILES:
        if not py_file.exists():
            continue
        source = py_file.read_text()
        tree = ast.parse(source, filename=str(py_file))
        relative = py_file.relative_to(_BDD_STEPS_DIR.parent.parent)

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not _is_given_decorated(node):
                continue
            if _body_appends_dict_to_registry(node):
                violations.append(f"{relative}:{node.lineno} {node.name}")

    return violations


class TestBddNoDictRegistry:
    """Structural guard: Given steps must construct Format objects, not dicts."""

    def test_no_dict_literals_in_registry_formats(self):
        """Given steps must not store raw dict literals in ctx["registry_formats"].

        Use FormatFactory.build() to construct real Format objects instead.
        """
        violations = _scan_given_steps()
        assert not violations, (
            f"Found {len(violations)} Given step(s) storing raw dicts in registry_formats:\n"
            + "\n".join(f"  {v}" for v in violations)
            + "\n\nUse FormatFactory.build(...) to construct real Format objects."
        )
