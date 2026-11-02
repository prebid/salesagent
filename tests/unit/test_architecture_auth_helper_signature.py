"""Guard: adapter ``_require_*`` accessors declare their return type.

Helpers like ``_require_config`` / ``_require_creatives_manager`` return a value with
``None`` stripped (they raise ``AdCPConfigurationError`` when it is absent), so callers
can rebind to narrow the type. The return annotation is what makes that narrowing real.
mypy does not enforce it here (``disallow_untyped_defs = False`` project-wide), so this
guard is the only thing that keeps the contract.

This module used to also scan ``src/core/auth.py`` for ``require_*`` helpers lacking a
keyword-only ``context=`` parameter. That half is gone: the two helpers that exist,
``require_principal`` and ``require_tenant``, declare ``context`` keyword-only with NO
default, so every call site names it and Python refuses one that does not.

The allowlist is empty: every accessor conforms. A new violation fails the build.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.unit._architecture_helpers import assert_violations_match_allowlist, iter_module_trees

REPO_ROOT = Path(__file__).resolve().parents[2]
ADAPTERS_DIR = REPO_ROOT / "src" / "adapters"

# Allowlist must only shrink, never grow. Keyed by (relative_path, function_name).
ADAPTER_KNOWN_VIOLATIONS: set[tuple[str, str]] = set()

FuncDef = (ast.FunctionDef, ast.AsyncFunctionDef)


def _iter_functions(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, FuncDef):
            yield node


def _find_adapter_require_helpers_missing_return() -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    for tree, rel_path in iter_module_trees([ADAPTERS_DIR]):
        for node in _iter_functions(tree):
            if node.name.startswith("_require_") and node.returns is None:
                out.add((rel_path, node.name))
    return out


@pytest.mark.arch_guard
def test_adapter_require_helpers_have_return_annotation():
    """Every adapter _require_* accessor declares a return type (the narrow-and-raise contract)."""
    assert_violations_match_allowlist(
        _find_adapter_require_helpers_missing_return(),
        ADAPTER_KNOWN_VIOLATIONS,
        fix_hint="Annotate the return type on adapter `_require_*` accessors.",
    )
