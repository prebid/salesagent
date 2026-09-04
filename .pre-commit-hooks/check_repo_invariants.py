#!/usr/bin/env python3
"""Repo invariants — consolidates grep-based pre-commit hooks.

Each check function returns a list of "<file>:<line>: <message>" strings.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

_SKIP_RE = re.compile(r"@pytest\.mark\.skip(?!if|_ci)")


def is_forbidden_skip_line(line: str) -> bool:
    """True when *line* contains a bare ``@pytest.mark.skip`` decorator.

    ``@pytest.mark.skipif`` and ``@pytest.mark.skip_ci`` are allowed.
    Shared by the pre-commit hook and the Smoke Tests gate.
    """
    return bool(_SKIP_RE.search(line))


def check_no_skip_tests(files: list[Path]) -> list[str]:
    """Forbid bare @pytest.mark.skip in test files.

    ``@pytest.mark.skipif`` and ``@pytest.mark.skip_ci`` are allowed (conditional /
    CI-specific skips). Bare ``skip`` without justification is forbidden.
    """
    out: list[str] = []
    for filepath in files:
        if "tests/" not in str(filepath) or not filepath.name.startswith("test_"):
            continue
        for lineno, line in enumerate(filepath.read_text().splitlines(), 1):
            if is_forbidden_skip_line(line):
                out.append(f"{filepath}:{lineno}: @pytest.mark.skip forbidden (use skip_ci with justification)")
    return out


def check_no_fn_calls(files: list[Path]) -> list[str]:
    """Detect .fn() call patterns in src/ (excluding test files)."""
    out: list[str] = []
    for filepath in files:
        parts = filepath.parts
        if "src" not in parts:
            continue
        if "tests" in parts:
            continue
        for lineno, line in enumerate(filepath.read_text().splitlines(), 1):
            if ".fn(" in line:
                out.append(f"{filepath}:{lineno}: .fn() calls forbidden — use direct function calls")
    return out


# A citation only helps if the reader can open it. These two forms cannot be:
# a path into .claude/notes/ points at a working note that is deleted when the
# work lands, and a review-round finding id (R1-19, SF-7, Chris-#2) exists only
# inside such a note. Both make an internal artifact load-bearing for the suite.
# Beads ids are deliberately NOT covered here: 733 of them already exist under
# src/ and tests/, so banning them is its own sweep rather than a forward-lock,
# and CLAUDE.md already directs FIXMEs at GitHub numbers.
_UNRESOLVABLE_CITATION_RE = re.compile(
    r"\.claude/notes/|\b(?:R\d-\d{1,2}|SF-\d{1,2}|Chris-#\d+)\b"
    r"|\b(?:salesagent|beads)-[a-z0-9]{2,7}(?:\.[0-9]+)*(?![\w-])"
)


def check_no_unresolvable_citations(files: list[Path]) -> list[str]:
    """Forbid citations an outside contributor cannot open, from src/ or tests/.

    Measured green at the commit that introduced it: zero occurrences of either
    form. It is a forward-lock, so its grade is the mutation — add one and this
    must fail.
    """
    out: list[str] = []
    for filepath in files:
        parts = filepath.parts
        if "src" not in parts and "tests" not in parts:
            continue
        for lineno, line in enumerate(filepath.read_text().splitlines(), 1):
            if _UNRESOLVABLE_CITATION_RE.search(line):
                out.append(
                    f"{filepath}:{lineno}: cite something a contributor can open "
                    f"(a GitHub issue or PR), not a working note or a review-round finding id"
                )
    return out


CHECKS = [check_no_skip_tests, check_no_fn_calls, check_no_unresolvable_citations]


def _merge_in_progress() -> bool:
    """True while a merge is being concluded.

    A merge STAGES THE OTHER SIDE WHOLESALE -- every file the incoming branch
    touched, whether or not the person merging wrote a line of it. This hook
    grades AUTHORED changes: a citation is a thing its author chose. Run it over
    a merge and it grades the other branch's entire backlog instead, and the
    person merging is handed a list they cannot honestly fix (they do not know
    which issue each of someone else's notes meant).

    Measured when this exemption was added: merging origin/main reported 283
    violations, none of them written by the merge.

    The guard is not weakened for authored work -- the same files are graded on
    the next ordinary commit that touches them, by whoever touches them. What is
    given up is catching a violation at the moment it ARRIVES via a merge rather
    than when it is next edited.
    """
    git_dir = subprocess.run(
        ["git", "rev-parse", "--git-dir"], capture_output=True, text=True, check=False
    ).stdout.strip()
    return bool(git_dir) and (Path(git_dir) / "MERGE_HEAD").exists()


def main(argv: list[str]) -> int:
    if _merge_in_progress():
        print("repo-invariants: skipped (merge in progress; grades authored changes)", file=sys.stderr)
        return 0

    files = [Path(p) for p in argv[1:] if p.endswith(".py")]
    if not files:
        # When invoked with no filenames (always_run-style), scan tests/ and src/
        repo = Path(__file__).resolve().parents[1]
        files = list((repo / "tests").rglob("test_*.py")) + list((repo / "src").rglob("*.py"))
    all_errors: list[str] = []
    for check in CHECKS:
        all_errors.extend(check(files))
    if all_errors:
        for error in all_errors:
            print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
