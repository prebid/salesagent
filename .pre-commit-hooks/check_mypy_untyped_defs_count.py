#!/usr/bin/env python3
"""
Pre-commit / quality-ci hook: ratchet mypy ``--check-untyped-defs`` error count.

Per ADR-009 / #1228 F2 / #1611:
- Track errors produced by ``mypy src/ --check-untyped-defs`` (flag overrides mypy.ini)
- Fail only when the count increases (new untyped-defs debt)
- Auto-lower the baseline when the count decreases
- ``--update-baseline`` rewrites the tracked baseline (review must contest ↑)

Caveat (ADR-009): counts drift with mypy / plugin versions. A mypy or
SQLAlchemy/Pydantic plugin bump that changes diagnostics can trip this ratchet
without any source change — justify a baseline rewrite in that PR, or pin
tooling first.

Uses shared ``count_ratchet`` for the create/compare/auto-lower skeleton, CLI
prelude, int baseline codec, and tooling-failure guard; this module owns the
mypy count method only.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from count_ratchet import (
    int_baseline_io,
    parse_ratchet_args,
    refuse_unmeasured,
    resolve_ratchet_paths,
    run_count_ratchet,
    run_counting_tool,
)

BASELINE_FILE = ".mypy-untyped-defs-baseline"
SRC_DIR = "src"
KEY = "check_untyped_defs"
KEYS = (KEY,)
MYPY_ERROR_SENTINEL = ": error:"
LABEL = "mypy --check-untyped-defs"

#: 0 = nothing to report, 1 = errors reported. 2 is a fatal or usage error, and
#: a signal death is negative; neither has checked everything it was asked to.
COMPLETE_RETURNCODES = frozenset({0, 1})

#: mypy prints exactly one of these, and only after checking everything. The
#: hook used to pass ``--no-error-summary``, which SUPPRESSED the one line that
#: proves the run finished — and carries the denominator besides. Accepting
#: ``returncode == 1`` whenever ": error:" appeared anywhere in stdout meant a
#: mypy that aborted partway through src/ was accepted, and its short count
#: written to the baseline (salesagent-b341x.20).
FOUND_SUMMARY = re.compile(r"^Found (\d+) errors? in \d+ files? \(checked \d+ source files?\)$")
CLEAN_SUMMARY = re.compile(r"^Success: no issues found in \d+ source files?$")


def _mypy_summary(stdout: str) -> str | None:
    """The trailing summary line, or ``None`` when mypy never printed one."""
    for line in reversed(stdout.splitlines()):
        stripped = line.strip()
        if FOUND_SUMMARY.match(stripped) or CLEAN_SUMMARY.match(stripped):
            return stripped
    return None


def count_untyped_defs_errors(repo_root: Path) -> int:
    """Count mypy ``--check-untyped-defs`` errors, or refuse to return a number."""
    cmd = [
        sys.executable,
        "-m",
        "mypy",
        SRC_DIR,
        "--config-file=mypy.ini",
        "--check-untyped-defs",
        "--hide-error-context",
    ]
    result = run_counting_tool(
        cmd,
        cwd=repo_root,
        label=LABEL,
        accepts_returncode=COMPLETE_RETURNCODES.__contains__,
        completion_marker=_mypy_summary,
    )
    stdout = result.stdout or ""
    tally = sum(1 for line in stdout.splitlines() if MYPY_ERROR_SENTINEL in line)

    # mypy states its own total, so the parse is checkable rather than trusted.
    # A disagreement means the output shape moved under us (a version bump, a
    # new diagnostic format) and the tally is measuring something else.
    summary = _mypy_summary(stdout) or ""
    found = FOUND_SUMMARY.match(summary)
    declared = int(found.group(1)) if found else 0
    if declared != tally:
        refuse_unmeasured(
            LABEL,
            f"it reported {declared} errors but {tally} error lines parsed — the output shape moved",
            summary,
        )
    return tally


def main() -> int:
    args = parse_ratchet_args("Check that mypy --check-untyped-defs error count does not increase")
    repo_root, _src_path, baseline_file = resolve_ratchet_paths(baseline_name=BASELINE_FILE)
    read_baseline, write_baseline = int_baseline_io(KEY)

    print("Counting mypy --check-untyped-defs errors (may take a minute)...")
    return run_count_ratchet(
        keys=KEYS,
        current={KEY: count_untyped_defs_errors(repo_root)},
        baseline_file=baseline_file,
        update_baseline=args.update_baseline,
        repo_root=repo_root,
        parse_upstream=lambda text: {KEY: int(text.strip())},
        # No count_upstream: re-running mypy over an extracted upstream tree
        # costs a full type-check per hook invocation. The upstream baseline
        # FILE is the ceiling; it exists on main, which is the case that
        # matters here (this baseline was committed at 237 over main's 227).
        read_baseline=read_baseline,
        write_baseline=write_baseline,
        increase_header="mypy --check-untyped-defs error count increased! (ADR-009 / #1611)",
        increase_hints=(
            "Fix the new type errors, or justify a baseline ↑ in review.",
            "Caveat: mypy/plugin version bumps can shift counts without source changes.",
            "",
            "To inspect:",
            f"  uv run mypy {SRC_DIR}/ --config-file=mypy.ini --check-untyped-defs",
            "  uv run python .pre-commit-hooks/check_mypy_untyped_defs_count.py --update-baseline",
        ),
    )


if __name__ == "__main__":
    sys.exit(main())
