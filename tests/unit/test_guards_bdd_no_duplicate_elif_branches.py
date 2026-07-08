"""Guard: no duplicate (unreachable) elif tests in the BDD harness dispatcher.

Regression for salesagent-mnyh (#1417): tests/bdd/conftest.py grew a second
``elif uc == "UC-003"`` later in the SAME if/elif chain. Python evaluates elif
tests top-down, so a branch whose test is syntactically identical to an earlier
branch in the chain can never fire — its harness route silently becomes dead
code while reading as if it were live.

This guard AST-scans ``tests/bdd/conftest.py`` for if/elif chains containing
two branches with the same test expression and fails on any duplicate. Ships
with ZERO violations after the mnyh fix; no allowlist.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCAN_FILES = [REPO_ROOT / "tests" / "bdd" / "conftest.py"]


def _chain_branches(node: ast.If) -> list[ast.If]:
    """The if/elif branches of one chain, starting at its head ``If`` node."""
    branches = [node]
    current = node
    while len(current.orelse) == 1 and isinstance(current.orelse[0], ast.If):
        current = current.orelse[0]
        branches.append(current)
    return branches


def find_duplicate_elif_tests(tree: ast.AST) -> list[str]:
    """`lineno: test` for every branch whose test repeats an earlier one in its chain."""
    # A chain's non-head branches are the orelse of their predecessor; walk()
    # visits them as standalone If nodes too, so track heads only.
    non_heads: set[int] = set()
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If) or id(node) in non_heads:
            continue
        branches = _chain_branches(node)
        non_heads.update(id(b) for b in branches[1:])
        seen: dict[str, int] = {}
        for branch in branches:
            test_src = ast.unparse(branch.test)
            if test_src in seen:
                offenders.append(
                    f"line {branch.lineno}: `elif {test_src}` duplicates the branch at line {seen[test_src]}"
                )
            else:
                seen[test_src] = branch.lineno
    return offenders


def test_no_duplicate_elif_tests_in_bdd_conftest():
    violations: list[str] = []
    for path in SCAN_FILES:
        tree = ast.parse(path.read_text(), filename=str(path))
        for offender in find_duplicate_elif_tests(tree):
            violations.append(f"{path.relative_to(REPO_ROOT)}: {offender}")
    assert not violations, (
        "Duplicate test expression inside one if/elif chain — the later branch is "
        "unreachable (shadowed by the earlier identical test), so its harness route "
        "is dead code (salesagent-mnyh, #1417). Merge or delete the shadowed branch. "
        "Violations:\n  " + "\n  ".join(violations)
    )


# ── Meta-tests: the detector itself ─────────────────────────────────────────


def _detect(snippet: str) -> list[str]:
    return find_duplicate_elif_tests(ast.parse(snippet))


class TestGuardDetector:
    def test_positive_duplicate_elif(self):
        assert _detect("if uc == 'UC-002':\n    a()\nelif uc == 'UC-003':\n    b()\nelif uc == 'UC-003':\n    c()")

    def test_positive_duplicate_separated_by_other_branches(self):
        # The duplicate need not be adjacent — any earlier identical test shadows it.
        assert _detect(
            "if uc == 'A':\n    a()\nelif uc == 'B':\n    b()\nelif uc == 'C':\n    c()\nelif uc == 'B':\n    d()"
        )

    def test_negative_distinct_branches(self):
        assert not _detect("if uc == 'UC-002':\n    a()\nelif uc == 'UC-003':\n    b()\nelse:\n    c()")

    def test_negative_same_test_in_different_chains(self):
        # Two separate chains may legitimately test the same expression.
        assert not _detect("if uc == 'UC-003':\n    a()\n\nif uc == 'UC-003':\n    b()")

    def test_negative_nested_if_not_part_of_chain(self):
        # A nested if inside a branch body is its own chain, not an elif.
        assert not _detect("if uc == 'UC-003':\n    if uc == 'UC-003':\n        a()")
