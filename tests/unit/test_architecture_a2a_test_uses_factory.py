"""Structural guard: A2A test files must use PrincipalFactory.make_identity, not inline ResolvedIdentity.

Inline ``ResolvedIdentity(...)`` constructions in A2A test files (``tests/unit/test_a2a*.py``,
``tests/integration/test_a2a*.py``) bypass the single source of truth at
``tests.factories.principal.PrincipalFactory.make_identity``. Each inline construction is
a future drift point — the factory's signature can evolve (e.g., new spec-mandated fields)
without inline callers tracking the change.

This guard scans the A2A test surface and fails the build on any direct call
(``ResolvedIdentity(...)``) or attribute call (``mod.ResolvedIdentity(...)``).
Aliased imports (``from src.core.resolved_identity import ResolvedIdentity as RI; RI(...)``)
are NOT caught — they would require type-inference across the import graph. In
practice, A2A tests construct directly or via the module attribute, so this
covers the realistic regression vector.
"""

import ast
from pathlib import Path


def _a2a_test_files() -> list[Path]:
    repo_root = Path(__file__).resolve().parents[2]
    paths: list[Path] = []
    for pattern in ("tests/unit/test_a2a*.py", "tests/integration/test_a2a*.py"):
        paths.extend(repo_root.glob(pattern))
    return paths


def _find_resolved_identity_calls(path: Path) -> list[int]:
    tree = ast.parse(path.read_text(), filename=str(path))
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "ResolvedIdentity":
                lines.append(node.lineno)
            elif isinstance(func, ast.Attribute) and func.attr == "ResolvedIdentity":
                lines.append(node.lineno)
    return lines


def test_a2a_test_files_use_principal_factory_make_identity():
    """No inline ``ResolvedIdentity(...)`` construction in A2A test files.

    Use ``PrincipalFactory.make_identity(...)`` from ``tests.factories.principal``
    so the harness has a single source of truth for ResolvedIdentity defaults.
    Adding a field to ``ResolvedIdentity`` should require updating exactly one
    place — not every A2A test file in the tree.
    """
    violations: list[str] = []
    for path in _a2a_test_files():
        for lineno in _find_resolved_identity_calls(path):
            violations.append(f"{path.relative_to(path.parents[2])}:{lineno}")

    assert not violations, (
        f"Found {len(violations)} inline ResolvedIdentity(...) construction(s) in "
        f"A2A test files. Replace with PrincipalFactory.make_identity(...):\n  " + "\n  ".join(violations)
    )
