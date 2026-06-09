"""Guard: build per-package field paths with ``package_field_path()``, not literals.

The ``_impl`` layer validates the package collection as a whole and reports the
offending field as a no-index bracket path — ``packages[].budget``,
``packages[].product_id``, ``packages[].targeting_overlay.property_list``. The
single source of that prefix is :func:`src.core.validation_helpers.package_field_path`;
the boundary-derived path (which carries a concrete index, ``packages[0].budget``)
comes from :func:`first_validation_error_field`.

A hand-rolled ``field="packages[].X"`` string literal duplicates the prefix and
drifts the moment the convention changes (e.g. to ``package[]`` or a different
notation). This guard forbids any string literal that *is* a no-index packages
field path — i.e. starts with ``packages[]`` — anywhere in ``src/``, with the
sole exception of ``package_field_path`` itself (the producer, where the prefix
legitimately lives).

Discriminator: ``value.startswith("packages[]")``. A field-path literal starts
with the prefix; docstring/AdCP prose that merely *mentions* ``packages[]`` mid
sentence (the create_media_buy tool descriptions, the helper docstrings) does
not, and is correctly ignored. Indexed forms (``media_buys[].packages[{id}]``)
start with a different collection and carry a concrete index, so they are not
no-index packages paths and are not flagged.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import TypeGuard

from tests.unit._ast_helpers import iter_module_trees, walk_with_enclosing_function

REPO_ROOT = Path(__file__).resolve().parents[2]
SCAN_DIRS = [REPO_ROOT / "src"]

# The one sanctioned home of the ``packages[]`` prefix — the helper that every
# other call site must route through. Exempt by identity, not allowlist: this is
# the definition, not tolerated debt.
PRODUCER = ("src/core/validation_helpers.py", "package_field_path")

# Keyed by (relative_path, enclosing_function). Must only shrink.
KNOWN_VIOLATIONS: set[tuple[str, str]] = set()


def _is_packages_field_literal(node: ast.AST) -> TypeGuard[ast.Constant]:
    """True for a str literal (incl. f-string segment) that starts with ``packages[]``."""
    return isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.startswith("packages[]")


def _find_packages_field_literals() -> list[tuple[str, str, int]]:
    out: list[tuple[str, str, int]] = []
    for tree, rel_path in iter_module_trees(SCAN_DIRS):
        for node, func in walk_with_enclosing_function(tree):
            if _is_packages_field_literal(node) and (rel_path, func) != PRODUCER:
                out.append((rel_path, func, node.lineno))
    return out


def test_no_packages_field_literal():
    """No hand-rolled ``packages[]`` field-path literal outside ``package_field_path``."""
    violations = [
        f"  {rel}:{lineno} in {func}()"
        for rel, func, lineno in _find_packages_field_literals()
        if (rel, func) not in KNOWN_VIOLATIONS
    ]
    assert not violations, (
        f"Found {len(violations)} hand-rolled packages[] field literal(s).\n"
        "Build the path with package_field_path(...) from src.core.validation_helpers "
        "so the prefix lives in one place:\n\n" + "\n".join(violations)
    )


def test_known_violations_not_stale():
    """Every allowlisted (file, function) must still hold a packages[] literal."""
    actual = {(rel, func) for rel, func, _ in _find_packages_field_literals()}
    stale = KNOWN_VIOLATIONS - actual
    assert not stale, "Stale allowlist entries (no longer hold a packages[] literal):\n" + "\n".join(
        f"  {rel} :: {func}" for rel, func in sorted(stale)
    )
