"""Guard: no duplicate (unreachable) dispatch tests in the BDD harness dispatcher.

Regression for salesagent-mnyh + salesagent-3k0v (#1417): tests/bdd/conftest.py
grew a second ``elif uc == "UC-003"`` in the SAME if/elif chain (mnyh), and —
after that was fixed with an elif-only guard — a second identical
``if any(t.startswith("T-UC-003") ...): return "UC-003"`` in the EARLY-RETURN
form (3k0v), which the elif-only guard could not see.

The disease is duplicate scenario-dispatch RESOLUTION, not "duplicate elif":
two code paths that resolve the same test expression, where the later one can
never fire. That happens in two forms:

- **Within one if/elif chain**: Python evaluates elif tests top-down, so a
  branch whose test is syntactically identical to an earlier branch in the
  chain is dead regardless of what its body does.
- **Across statements in one block**: an earlier ``if <test>`` whose body
  TERMINATES (returns/raises on its final statement) shadows any later
  statement-level branch with the identical test — the early return means
  control never reaches the duplicate. A non-terminating earlier branch does
  NOT shadow (control falls through), so two chains may legitimately test the
  same expression when the first merely mutates state.

This guard AST-scans ``tests/bdd/conftest.py`` for both forms and fails on any
duplicate. Ships with ZERO violations after the 3k0v fix; no allowlist.
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


def _body_terminates(body: list[ast.stmt]) -> bool:
    """True when the branch body always leaves the enclosing block."""
    return bool(body) and isinstance(body[-1], ast.Return | ast.Raise)


def _statement_lists(tree: ast.AST):
    """Every statement list in the tree, skipping elif-continuation orelse lists.

    An ``orelse`` holding exactly one ``If`` is the elif continuation of a
    chain — its branches are handled via ``_chain_branches`` on the chain
    head, so yielding it separately would double-count.
    """
    for parent in ast.walk(tree):
        for field in ("body", "orelse", "finalbody"):
            stmts = getattr(parent, field, None)
            if not isinstance(stmts, list) or not stmts:
                continue
            if (
                field == "orelse"
                and isinstance(parent, ast.If)
                and len(stmts) == 1
                and isinstance(stmts[0], ast.If)
            ):
                continue
            yield stmts


def find_duplicate_dispatch_tests(tree: ast.AST) -> list[str]:
    """`lineno: test` for every branch whose test repeats a shadowing earlier one.

    Two shadowing relations per statement list:
    - same-chain: any earlier branch in the chain (elif semantics);
    - cross-statement: an earlier statement-level branch whose body terminates.
    """
    offenders: list[str] = []
    for stmts in _statement_lists(tree):
        terminal_seen: dict[str, int] = {}
        for stmt in stmts:
            if not isinstance(stmt, ast.If):
                continue
            chain_seen: dict[str, int] = {}
            for branch in _chain_branches(stmt):
                test_src = ast.unparse(branch.test)
                shadowing = chain_seen.get(test_src, terminal_seen.get(test_src))
                if shadowing is not None:
                    offenders.append(
                        f"line {branch.lineno}: `{test_src}` duplicates the dispatch branch at line {shadowing}"
                    )
                    continue
                chain_seen[test_src] = branch.lineno
                if _body_terminates(branch.body):
                    terminal_seen[test_src] = branch.lineno
    return offenders


def test_no_duplicate_dispatch_tests_in_bdd_conftest():
    violations: list[str] = []
    for path in SCAN_FILES:
        tree = ast.parse(path.read_text(), filename=str(path))
        for offender in find_duplicate_dispatch_tests(tree):
            violations.append(f"{path.relative_to(REPO_ROOT)}: {offender}")
    assert not violations, (
        "Duplicate dispatch test expression — the later branch is unreachable "
        "(shadowed by an earlier identical test in the same chain, or by an "
        "earlier early-return branch in the same block), so its harness route is "
        "dead code (salesagent-mnyh elif form / salesagent-3k0v early-return "
        "form, #1417). Merge or delete the shadowed branch. "
        "Violations:\n  " + "\n  ".join(violations)
    )


# ── Meta-tests: the detector itself ─────────────────────────────────────────


def _detect(snippet: str) -> list[str]:
    return find_duplicate_dispatch_tests(ast.parse(snippet))


class TestGuardDetector:
    # -- elif-chain form (salesagent-mnyh) --

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
        # Two separate chains may test the same expression when the earlier
        # branch does NOT terminate — control falls through, both are live.
        assert not _detect("if uc == 'UC-003':\n    a()\n\nif uc == 'UC-003':\n    b()")

    def test_negative_nested_if_not_part_of_chain(self):
        # A nested if inside a branch body is its own block, not an elif.
        assert not _detect("if uc == 'UC-003':\n    if uc == 'UC-003':\n        a()")

    # -- early-return form (salesagent-3k0v) --

    def test_positive_duplicate_early_return_if(self):
        # The 3k0v shape: `if X: return "A"` ... `if X: return "B"` in the
        # same block — the second can never fire.
        assert _detect(
            "def f():\n"
            "    if any(t.startswith('T-UC-003') for t in m):\n        return 'UC-003'\n"
            "    if any(t.startswith('T-UC-006') for t in m):\n        return 'UC-006'\n"
            "    if any(t.startswith('T-UC-003') for t in m):\n        return 'UC-003'\n"
        )

    def test_positive_early_return_then_duplicate_in_chain(self):
        # A terminal statement-level branch shadows the same test appearing
        # later inside an if/elif chain of the same block.
        assert _detect(
            "def f():\n"
            "    if x == 1:\n        return 'a'\n"
            "    if y == 2:\n        return 'b'\n    elif x == 1:\n        return 'c'\n"
        )

    def test_positive_raise_counts_as_terminal(self):
        assert _detect(
            "def f():\n"
            "    if x == 1:\n        raise ValueError('x')\n"
            "    if x == 1:\n        return 'dead'\n"
        )

    def test_negative_non_terminal_first_branch_falls_through(self):
        # First branch mutates and falls through — the second IS reachable.
        assert not _detect(
            "def f():\n"
            "    if x == 1:\n        y = 2\n"
            "    if x == 1:\n        return y\n"
        )

    def test_negative_same_test_in_sibling_blocks(self):
        # Same test in two different statement lists (two functions) is fine.
        assert not _detect(
            "def f():\n    if x == 1:\n        return 'a'\n\n"
            "def g():\n    if x == 1:\n        return 'a'\n"
        )
