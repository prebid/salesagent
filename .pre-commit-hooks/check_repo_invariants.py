#!/usr/bin/env python3
"""Repo invariants — consolidates grep-based pre-commit hooks.

Each check function returns a list of "<file>:<line>: <message>" strings.
"""

from __future__ import annotations

import os
import re
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
# The beads-id alternation that used to be the third line here was removed: it
# contradicted the paragraph directly above it, and both were added in the SAME
# commit (26783202f), so one of the two was a slip. The comment is the one that
# holds up -- it names a checkable reason, and the count is now 478 beads ids
# under src/ and tests/. A pattern matching 478 existing occurrences is not a
# forward-lock; it made every file carrying one uncommittable, which is how it
# surfaced. The docstring below still says "either form", counting two, which is
# the same design speaking again. Banning beads ids remains "its own sweep".
_UNRESOLVABLE_CITATION_RE = re.compile(r"\.claude/notes/|\b(?:R\d-\d{1,2}|SF-\d{1,2}|Chris-#\d+)\b")


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


# A developer's home directory, hardcoded. `/Users/<name>/` and `/home/<name>/` are
# machine-specific: they break on every other checkout and, when the offender is a
# SYMLINK, they break git itself — a path under a symlinked directory is
# unreachable ("fatal: pathspec ... is beyond a symbolic link"), so a file written
# there cannot be committed from a worktree at all. #2228 removed the last of them,
# including two tracked symlinks; this keeps them out.
_DEV_HOME_RE = re.compile(r"/(?:Users|home)/(?!<|\$)([A-Za-z0-9._-]+)/")

# Generic stand-ins that are documentation, not someone's machine.
_PLACEHOLDER_USERS = frozenset({"user", "username", "you", "youruser", "your-user", "me", "name", "runner", "root"})


def _dev_home_hit(text: str) -> str | None:
    """The first machine-specific home path in *text*, or None."""
    for match in _DEV_HOME_RE.finditer(text):
        if match.group(1).lower() not in _PLACEHOLDER_USERS:
            return match.group(0)
    return None


def check_no_developer_paths(files: list[Path]) -> list[str]:
    """Forbid hardcoded developer home directories, in file CONTENT and in SYMLINK TARGETS.

    Two distinct failure modes, and the second is the one that bites hardest:

    * content — a path under a named home directory in a doc, formula or
      script is unrunnable for everyone else.
    * symlink target — a tracked symlink pointing into one machine makes its whole
      subtree unreachable to git from any other worktree. That is not a portability
      nit; files under it cannot be committed at all.

    Placeholders (``/home/user/``, ``/Users/<you>/``, ``$HOME``) are allowed: they
    are documentation, not a machine.
    """
    out: list[str] = []
    for filepath in files:
        if filepath.is_symlink():
            target = os.readlink(filepath)
            if (hit := _dev_home_hit(target + "/")) is not None:
                out.append(
                    f"{filepath}:0: symlink points into a developer home ({hit}) — "
                    f"its subtree becomes unreachable to git from other worktrees"
                )
            continue
        try:
            text = filepath.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if (hit := _dev_home_hit(line)) is not None:
                out.append(
                    f"{filepath}:{lineno}: hardcoded developer path ({hit}) — use a repo-relative or sibling path"
                )
    return out


def main(argv: list[str]) -> int:
    given = [Path(p) for p in argv[1:]]
    files = [p for p in given if p.suffix == ".py"]
    if not given:
        # When invoked with no filenames (always_run-style), scan tests/ and src/
        repo = Path(__file__).resolve().parents[1]
        files = list((repo / "tests").rglob("test_*.py")) + list((repo / "src").rglob("*.py"))
        given = files
    all_errors: list[str] = []
    for check in CHECKS:
        all_errors.extend(check(files))
    # Portability is not a Python-only concern: docs, formulas, shell scripts and
    # symlinks carry these too, so this one check sees every file it is handed.
    all_errors.extend(check_no_developer_paths(given))
    if all_errors:
        for error in all_errors:
            print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
