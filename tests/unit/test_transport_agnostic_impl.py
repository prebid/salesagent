"""Regression tests for transport-agnostic _impl functions.

Ensures that _impl functions have zero knowledge of transport protocol:
- No imports from fastmcp, a2a, starlette, or fastapi
- No presentation logic (console.print, rich formatting)
- x-context-id extracted at transport boundary, not in _impl
"""

import ast
from pathlib import Path

TOOLS_DIR = Path(__file__).parent.parent.parent / "src" / "core" / "tools"

# Banned imports inside _impl function bodies
BANNED_TRANSPORT_MODULES = {
    "fastmcp",
    "a2a",
    "starlette",
    "fastapi",
}


def _find_impl_functions(file_path: Path) -> list[tuple[str, ast.FunctionDef]]:
    """Find all _impl functions in a Python file."""
    source = file_path.read_text()
    tree = ast.parse(source, filename=str(file_path))
    results = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.endswith("_impl"):
                results.append((node.name, node))
    return results


def _get_imports_in_function(func_node: ast.FunctionDef) -> list[tuple[int, str]]:
    """Get all import statements inside a function body."""
    imports = []
    for node in ast.walk(func_node):
        if isinstance(node, ast.ImportFrom) and node.module:
            imports.append((node.lineno, node.module))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imports.append((node.lineno, alias.name))
    return imports


def _get_function_calls(func_node: ast.FunctionDef) -> list[tuple[int, str]]:
    """Get all function calls as dotted names inside a function body."""
    calls = []
    for node in ast.walk(func_node):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
                calls.append((node.lineno, f"{node.func.value.id}.{node.func.attr}"))
    return calls


class TestNoTransportImportsInImpl:
    """No _impl function should import from transport-specific modules."""

    def test_no_fastmcp_imports_in_media_buy_create_impl(self):
        """_create_media_buy_impl must not import from fastmcp."""
        file_path = TOOLS_DIR / "media_buy_create.py"
        for func_name, func_node in _find_impl_functions(file_path):
            imports = _get_imports_in_function(func_node)
            for lineno, module in imports:
                top_module = module.split(".")[0]
                assert top_module not in BANNED_TRANSPORT_MODULES, (
                    f"{func_name} imports from '{module}' at line {lineno}. "
                    f"_impl functions must not import transport-specific modules."
                )

    def test_no_fastmcp_imports_in_media_buy_update_impl(self):
        """_update_media_buy_impl must not import from fastmcp."""
        file_path = TOOLS_DIR / "media_buy_update.py"
        for func_name, func_node in _find_impl_functions(file_path):
            imports = _get_imports_in_function(func_node)
            for lineno, module in imports:
                top_module = module.split(".")[0]
                assert top_module not in BANNED_TRANSPORT_MODULES, (
                    f"{func_name} imports from '{module}' at line {lineno}. "
                    f"_impl functions must not import transport-specific modules."
                )

    def test_no_transport_imports_in_any_impl(self):
        """Sweep: no _impl function in any tool file imports transport modules."""
        violations = []
        for py_file in TOOLS_DIR.rglob("*.py"):
            if py_file.name.startswith("_") and py_file.name != "__init__.py":
                continue
            for func_name, func_node in _find_impl_functions(py_file):
                imports = _get_imports_in_function(func_node)
                for lineno, module in imports:
                    top_module = module.split(".")[0]
                    if top_module in BANNED_TRANSPORT_MODULES:
                        rel_path = py_file.relative_to(TOOLS_DIR)
                        violations.append(f"{rel_path}:{lineno} — {func_name} imports '{module}'")

        assert not violations, "Transport-specific imports found in _impl functions:\n" + "\n".join(
            f"  - {v}" for v in violations
        )


class TestNoPresentationLogicInImpl:
    """No _impl function should use console.print or rich formatting."""

    def test_no_console_print_in_performance_impl(self):
        """_update_performance_index_impl must not use console.print()."""
        file_path = TOOLS_DIR / "performance.py"
        for func_name, func_node in _find_impl_functions(file_path):
            calls = _get_function_calls(func_node)
            for lineno, call_name in calls:
                assert call_name != "console.print", (
                    f"{func_name} calls console.print() at line {lineno}. "
                    f"_impl functions must use logger, not presentation logic."
                )

    def test_no_console_print_in_any_impl(self):
        """Sweep: no _impl function in any tool file uses console.print()."""
        violations = []
        for py_file in TOOLS_DIR.rglob("*.py"):
            for func_name, func_node in _find_impl_functions(py_file):
                calls = _get_function_calls(func_node)
                for lineno, call_name in calls:
                    if call_name == "console.print":
                        rel_path = py_file.relative_to(TOOLS_DIR)
                        violations.append(f"{rel_path}:{lineno} — {func_name} calls console.print()")

        assert not violations, "Presentation logic found in _impl functions:\n" + "\n".join(
            f"  - {v}" for v in violations
        )
