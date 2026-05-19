"""Shared AST helpers used by multiple structural guards.

Lives next to ``_per_file_cap_guard.py`` and ``_migration_helpers.py``.
Guards that need to do the same AST scan import from here rather than
reach into each other's modules, so a structural-rule refactor doesn't
quietly break a sibling guard.
"""

from __future__ import annotations

import ast


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
        if "error" not in module.split("."):
            continue
        for alias in node.names:
            if alias.name == "Error":
                aliases.add(alias.asname or alias.name)
    return aliases
