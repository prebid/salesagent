#!/usr/bin/env python3
"""Repo invariants — consolidates grep-based pre-commit hooks.

Each check function returns a list of "<file>:<line>: <message>" strings.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


def check_no_skip_tests(files: list[Path]) -> list[str]:
    """Forbid @pytest.mark.skip without skip_ci justification."""
    pattern = re.compile(r"@pytest\.mark\.skip(?!_ci)")
    out: list[str] = []
    for filepath in files:
        if "tests/" not in str(filepath) or not filepath.name.startswith("test_"):
            continue
        for lineno, line in enumerate(filepath.read_text().splitlines(), 1):
            if pattern.search(line):
                out.append(f"{filepath}:{lineno}: @pytest.mark.skip forbidden (use skip_ci with justification)")
    return out


def check_no_fn_calls(files: list[Path]) -> list[str]:
    """Detect .fn() call patterns in src/ (excluding test files)."""
    out: list[str] = []
    for filepath in files:
        parts = filepath.parts
        if "src" not in parts:
            continue
        if "test" in filepath.name:
            continue
        for lineno, line in enumerate(filepath.read_text().splitlines(), 1):
            if ".fn(" in line:
                out.append(f"{filepath}:{lineno}: .fn() calls forbidden — use direct function calls")
    return out


CHECKS = [check_no_skip_tests, check_no_fn_calls]


def main(argv: list[str]) -> int:
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
