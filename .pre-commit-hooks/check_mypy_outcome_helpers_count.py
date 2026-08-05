#!/usr/bin/env python3
"""
Pre-commit / quality-ci hook: ratchet mypy error count on tests/bdd/steps/_outcome_helpers.py.

Per salesagent-hwji (step 7): this file feeds every BDD step module (the typed
accessors -- wire_dict, dispatched_request, assert_media_buy_created, etc. -- that
oracles across all UCs read through), so a type regression here is a class of
regression, not a local one. Baseline 0: unlike the untyped-defs ratchet (which
tracks pre-existing debt that shrinks over time), this file starts and stays at
zero errors -- any error here is new debt introduced in the same change.

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

BASELINE_FILE = ".mypy-outcome-helpers-baseline"
TARGET_FILE = "tests/bdd/steps/_outcome_helpers.py"
KEY = "outcome_helpers_mypy_errors"
KEYS = (KEY,)
MYPY_ERROR_SENTINEL = ": error:"


def count_outcome_helpers_errors(repo_root: Path) -> int:
    """Run mypy on _outcome_helpers.py and count diagnostic error lines."""
    cmd = [
        sys.executable,
        "-m",
        "mypy",
        TARGET_FILE,
        "--config-file=mypy.ini",
        "--follow-imports=silent",
        "--cache-dir=.mypy_cache_tests_gate",
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
    args = parse_ratchet_args(f"Check that mypy error count on {TARGET_FILE} does not increase")
    repo_root, _src_path, baseline_file = resolve_ratchet_paths(baseline_name=BASELINE_FILE)
    read_baseline, write_baseline = int_baseline_io(KEY)

    print(f"Counting mypy errors on {TARGET_FILE}...")
    return run_count_ratchet(
        keys=KEYS,
        current={KEY: count_outcome_helpers_errors(repo_root)},
        baseline_file=baseline_file,
        update_baseline=args.update_baseline,
        read_baseline=read_baseline,
        write_baseline=write_baseline,
        increase_header=f"mypy error count on {TARGET_FILE} increased! (salesagent-hwji)",
        increase_hints=(
            "This file feeds every BDD step module -- fix the new type error, don't baseline it up.",
            "",
            "To inspect:",
            f"  uv run mypy {TARGET_FILE} --config-file=mypy.ini --follow-imports=silent"
            " --cache-dir=.mypy_cache_tests_gate",
        ),
    )


if __name__ == "__main__":
    sys.exit(main())
