"""Guard: allowlist stale-detection must use assert_violations_match_allowlist.

Hand-rolled ``stale = ALLOWLIST - found`` set-diffs duplicate the helper and
drift when one copy is updated. All guard allowlist comparisons route through
``tests.unit._architecture_helpers.assert_violations_match_allowlist``.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.unit._architecture_helpers import repo_root

_EXEMPT = {
    Path("tests/unit/_architecture_helpers.py"),
    Path("tests/unit/test_architecture_helpers_contract.py"),
    Path("tests/unit/test_architecture_no_handrolled_allowlist_diff.py"),
}

_ALLOWLIST_DIFF_TARGET_NAMES = frozenset({"stale", "fixed"})


def _is_set_subtraction(node: ast.expr) -> bool:
    """True when *node* is a set subtraction or ``sorted(set_subtraction)``."""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Sub):
        return True
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "sorted"
        and node.args
        and isinstance(node.args[0], ast.BinOp)
        and isinstance(node.args[0].op, ast.Sub)
    ):
        return True
    return False


def _assigns_handrolled_allowlist_diff(node: ast.AST) -> bool:
    """True when *node* is ``stale = … - …`` or ``fixed = … - …``."""
    if not isinstance(node, ast.Assign):
        return False
    if not _is_set_subtraction(node.value):
        return False
    for target in node.targets:
        if isinstance(target, ast.Name) and target.id in _ALLOWLIST_DIFF_TARGET_NAMES:
            return True
    return False


def _find_handrolled_allowlist_diffs() -> list[str]:
    repo = repo_root()
    violations: list[str] = []
    for path in sorted((repo / "tests" / "unit").glob("test_architecture_*.py")):
        rel = path.relative_to(repo)
        if rel in _EXEMPT:
            continue
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if _assigns_handrolled_allowlist_diff(node):
                lineno = getattr(node, "lineno", "?")
                violations.append(f"{rel}:{lineno}: hand-rolled allowlist set-diff")
    return violations


def _find_ast_helpers_imports() -> list[str]:
    repo = repo_root()
    violations: list[str] = []
    for path in sorted((repo / "tests" / "unit").glob("test_architecture_*.py")):
        rel = path.relative_to(repo)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "tests.unit._ast_helpers":
                violations.append(f"{rel}:{node.lineno}: imports tests.unit._ast_helpers")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "tests.unit._ast_helpers":
                        violations.append(f"{rel}:{node.lineno}: imports tests.unit._ast_helpers")
    return violations


@pytest.mark.arch_guard
def test_no_handrolled_allowlist_set_diff() -> None:
    """Guard tests must not inline allowlist set-diff logic."""
    violations = _find_handrolled_allowlist_diffs()
    assert not violations, (
        "Hand-rolled allowlist set-diff found — use assert_violations_match_allowlist():\n"
        + "\n".join(f"  {v}" for v in violations)
    )


@pytest.mark.arch_guard
def test_no_ast_helpers_imports() -> None:
    """Architecture guards must import from tests.unit._architecture_helpers only."""
    violations = _find_ast_helpers_imports()
    assert not violations, (
        "Import tests.unit._architecture_helpers instead of the removed _ast_helpers shim:\n"
        + "\n".join(f"  {v}" for v in violations)
    )


@pytest.mark.arch_guard
def test_allowlist_diff_guard_catches_direct_set_diff() -> None:
    """Self-test: the scanner flags inline stale = ALLOWLIST - found."""
    repo = repo_root()
    probe = repo / "tests" / "unit" / "test_architecture_probe_handrolled_allowlist_diff.py"
    probe.write_text("stale = ALLOWLIST - found\n", encoding="utf-8")
    try:
        violations = _find_handrolled_allowlist_diffs()
        assert any("test_architecture_probe_handrolled_allowlist_diff.py" in v for v in violations)
    finally:
        probe.unlink(missing_ok=True)


@pytest.mark.arch_guard
def test_allowlist_diff_guard_catches_sorted_set_diff() -> None:
    """Self-test: the scanner flags stale = sorted(ALLOWLIST - found)."""
    repo = repo_root()
    probe = repo / "tests" / "unit" / "test_architecture_probe_handrolled_allowlist_diff.py"
    probe.write_text("stale = sorted(ALLOWLIST - found)\n", encoding="utf-8")
    try:
        violations = _find_handrolled_allowlist_diffs()
        assert any("test_architecture_probe_handrolled_allowlist_diff.py" in v for v in violations)
    finally:
        probe.unlink(missing_ok=True)
