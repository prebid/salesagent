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

import sys
from pathlib import Path

from count_ratchet import (
    int_baseline_io,
    parse_ratchet_args,
    resolve_ratchet_paths,
    run_count_ratchet,
    run_counting_tool,
)

BASELINE_FILE = ".mypy-untyped-defs-baseline"
SRC_DIR = "src"
KEY = "check_untyped_defs"
KEYS = (KEY,)
MYPY_ERROR_SENTINEL = ": error:"


def count_untyped_defs_errors(repo_root: Path) -> int:
    """Run mypy with ``--check-untyped-defs`` and count diagnostic error lines."""
    cmd = [
        sys.executable,
        "-m",
        "mypy",
        SRC_DIR,
        "--config-file=mypy.ini",
        "--check-untyped-defs",
        "--no-error-summary",
        "--hide-error-context",
    ]
    result = run_counting_tool(
        cmd,
        cwd=repo_root,
        has_findings=lambda completed: MYPY_ERROR_SENTINEL in (completed.stdout or ""),
        label="mypy",
    )
    return sum(1 for line in (result.stdout or "").splitlines() if MYPY_ERROR_SENTINEL in line)


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
