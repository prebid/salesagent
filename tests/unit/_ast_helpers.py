"""Shared AST helpers used by multiple structural guards.

Lives next to ``_per_file_cap_guard.py`` and ``_migration_helpers.py``.
Guards that need to do the same AST scan import from here rather than
reach into each other's modules, so a structural-rule refactor doesn't
quietly break a sibling guard.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

# Repo root anchored to this file's location — guards work regardless of CWD.
REPO_ROOT = Path(__file__).resolve().parents[2]
SCAN_DIRS = [REPO_ROOT / "src/core/tools", REPO_ROOT / "src/adapters"]


def rel(path: Path) -> str:
    """Return path relative to repo root for stable allowlist keys."""
    return str(path.relative_to(REPO_ROOT))


def safe_parse(filepath: Path) -> ast.Module | None:
    """Parse a Python file, returning None if it doesn't exist or has a SyntaxError."""
    if not filepath.exists():
        return None
    try:
        return ast.parse(filepath.read_text(), filename=str(filepath))
    except SyntaxError:
        return None


def iter_module_trees(scan_dirs: list[Path]) -> Iterator[tuple[ast.Module, str]]:
    """Yield ``(parsed_tree, repo_relative_path)`` for every parseable ``.py`` under ``scan_dirs``.

    Skips ``__pycache__`` and files with syntax errors. Shared by the structural
    guards so the file-walk boilerplate lives in one place (DRY) rather than being
    re-copied into each guard's ``_find_*`` function.
    """
    for scan_dir in scan_dirs:
        if not scan_dir.exists():
            continue
        for py_file in sorted(scan_dir.rglob("*.py")):
            if "__pycache__" in str(py_file):
                continue
            tree = safe_parse(py_file)
            if tree is not None:
                yield tree, rel(py_file)


def walk_with_enclosing_function(tree: ast.AST) -> Iterator[tuple[ast.AST, str]]:
    """Yield ``(node, enclosing_function_name)`` for every node in ``tree``.

    The enclosing name is the nearest ancestor ``FunctionDef``/``AsyncFunctionDef``
    name, or ``"<module>"`` at module scope. Structural guards that report
    violations keyed by ``(file, function)`` share this walk instead of each
    re-implementing the same ``visit(node, func)`` recursion (DRY) — a guard then
    becomes a predicate over the yielded nodes rather than a copy of the traversal.
    """

    def visit(node: ast.AST, func_name: str) -> Iterator[tuple[ast.AST, str]]:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            func_name = node.name
        yield node, func_name
        for child in ast.iter_child_nodes(node):
            yield from visit(child, func_name)

    yield from visit(tree, "<module>")


def collect_error_aliases(tree: ast.AST) -> set[str]:
    """Collect names that alias the adcp Error type.

    Tracks both module-level and function-level imports of the form::

        from adcp...error import Error
        from adcp...error import Error as <alias>

    Returns the set of local names that refer to the adcp ``Error`` class
    (always includes ``"Error"`` itself, plus any aliases).
    """
    aliases: set[str] = {"Error"}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        module = node.module or ""
        # Track the Error model from any error-named module AND from adcp's
        # public packages: `from adcp.types import Error as X` has no "error"
        # path component but binds the same construction surface.
        if "error" not in module.split(".") and not module.startswith("adcp"):
            continue
        for alias in node.names:
            if alias.name == "Error":
                aliases.add(alias.asname or alias.name)
    return aliases
