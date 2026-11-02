#!/usr/bin/env python3
"""
Pre-commit / quality-ci hook: ratchet counts for ruff rules we ignore wholesale.

Per ADR-009 / #1228 F1 / #1610:
- Track C901, PLR0912, PLR0915, F841 violation counts in ``src/``
- Fail only when a count increases (new complexity debt)
- Auto-lower the baseline when a count decreases
- ``--update-baseline`` rewrites the tracked baseline (review must contest ↑)
- Compares each baseline key against origin/main once that file exists there
  (hard raise-guard; skipped only on first land before the file is on main)

Uses shared ``count_ratchet`` for the create/compare/auto-lower skeleton, CLI
prelude, JSON baseline codec, and tooling-failure guard; this module owns the
ruff count method + origin/main raise guard only.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from count_ratchet import (
    json_baseline_io,
    parse_ratchet_args,
    resolve_ratchet_paths,
    run_count_ratchet,
    run_counting_tool,
)

BASELINE_FILE = ".ruff-complexity-baseline"
SRC_DIR = "src"
MAIN_REF = "origin/main"
RULES = ("C901", "PLR0912", "PLR0915", "F841")

#: 0 = clean, 1 = violations found. 2 is a usage/IO error; negative is a signal.
COMPLETE_RETURNCODES = frozenset({0, 1})


def _ruff_emitted_a_whole_report(stdout: str) -> str | None:
    """``--output-format=json`` writes one complete array, ``[]`` when clean.

    A run that died partway leaves a truncated array, so "it parses" is this
    tool's completion marker — the same job pylint's score line and mypy's
    summary do for counters whose output is line-oriented.
    """
    try:
        findings = json.loads(stdout.strip() or "")
    except json.JSONDecodeError:
        return None
    if not isinstance(findings, list):
        return None
    return f"{len(findings)} findings in a complete JSON report"


def count_rule_violations(repo_root: Path, src_path: Path) -> dict[str, int]:
    """Count selected ruff violations under src/ (even if ignored in pyproject)."""
    cmd = [
        sys.executable,
        "-m",
        "ruff",
        "check",
        str(src_path),
        f"--select={','.join(RULES)}",
        "--output-format=json",
        "--no-cache",
    ]
    result = run_counting_tool(
        cmd,
        cwd=repo_root,
        label="ruff",
        accepts_returncode=COMPLETE_RETURNCODES.__contains__,
        completion_marker=_ruff_emitted_a_whole_report,
    )

    try:
        findings = json.loads((result.stdout or "").strip() or "[]")
    except json.JSONDecodeError as e:
        print(f"ERROR: could not parse ruff JSON output: {e}", file=sys.stderr)
        print((result.stdout or "")[:500], file=sys.stderr)
        raise SystemExit(2) from e

    counts = dict.fromkeys(RULES, 0)
    for item in findings:
        code = item.get("code")
        if code in counts:
            counts[code] += 1
    return counts


def main() -> int:
    args = parse_ratchet_args(f"Check that ruff {'/'.join(RULES)} counts do not increase")
    repo_root, src_path, baseline_file = resolve_ratchet_paths(baseline_name=BASELINE_FILE)
    read_baseline, write_baseline = json_baseline_io(RULES)

    return run_count_ratchet(
        keys=RULES,
        current=count_rule_violations(repo_root, src_path),
        baseline_file=baseline_file,
        update_baseline=args.update_baseline,
        repo_root=repo_root,
        # Upstream SOURCE under TODAY's thresholds: the ceiling must answer
        # "how many did the merge base have under the rules we grade by now",
        # otherwise loosening a threshold in pyproject would silently raise it.
        count_upstream=lambda tree: count_rule_violations(repo_root, tree / SRC_DIR),
        read_baseline=read_baseline,
        write_baseline=write_baseline,
        increase_header="Ruff ratchet count increased! (ADR-009 / #1610)",
        increase_hints=(
            "Fix the new violation (refactor the complexity, delete the unused local),",
            "or justify a baseline ↑ in review.",
            "",
            "To inspect:",
            f"  uv run ruff check {SRC_DIR}/ --select={','.join(RULES)}",
            "  uv run python .pre-commit-hooks/check_ruff_complexity_count.py --update-baseline",
        ),
    )


if __name__ == "__main__":
    sys.exit(main())
