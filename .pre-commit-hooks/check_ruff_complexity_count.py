#!/usr/bin/env python3
"""
Pre-commit / quality-ci hook: ratchet ruff pure-complexity rule counts.

Per ADR-009 / #1228 F1 / #1610:
- Track C901, PLR0912, PLR0915 violation counts in ``src/``
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
import subprocess
import sys
from pathlib import Path

from count_ratchet import (
    json_baseline_io,
    parse_ratchet_args,
    read_json_baseline,
    resolve_ratchet_paths,
    run_count_ratchet,
    run_counting_tool,
)

BASELINE_FILE = ".ruff-complexity-baseline"
SRC_DIR = "src"
MAIN_REF = "origin/main"
RULES = ("C901", "PLR0912", "PLR0915")


def count_rule_violations(repo_root: Path, src_path: Path) -> dict[str, int]:
    """Count selected ruff complexity violations under src/ (even if ignored in pyproject)."""
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
        has_findings=lambda completed: bool((completed.stdout or "").strip()),
        label="ruff",
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


def read_main_baseline(repo_root: Path) -> dict[str, int] | None:
    """Load origin/main's complexity baseline, or None if the file is not on main yet."""
    result = subprocess.run(
        ["git", "show", f"{MAIN_REF}:{BASELINE_FILE}"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        # First land: file does not exist on main yet — soft until after merge.
        return None
    try:
        data = json.loads(result.stdout)
        if not isinstance(data, dict):
            raise ValueError(f"baseline must be a JSON object, got {type(data).__name__}")
        return {key: int(data.get(key, 0)) for key in RULES}
    except (ValueError, TypeError) as e:
        print(f"ERROR: {MAIN_REF}:{BASELINE_FILE} is not a valid JSON object: {e}", file=sys.stderr)
        raise SystemExit(1) from e


def raise_probe_counts(
    *,
    baseline: dict[str, int] | None,
    current: dict[str, int],
    update_baseline: bool,
) -> dict[str, int]:
    """Per-key values probed against origin/main before any baseline write."""
    if baseline is None or update_baseline:
        return dict(current)
    return {key: min(baseline.get(key, 0), current[key]) for key in RULES}


def check_baseline_not_raised(repo_root: Path, local: dict[str, int]) -> int:
    """Fail when any local baseline key exceeds origin/main's (once main has the file)."""
    main_baseline = read_main_baseline(repo_root)
    if main_baseline is None:
        return 0
    raised = False
    for key in RULES:
        if local[key] > main_baseline[key]:
            if not raised:
                print("Baseline value raised vs origin/main!", file=sys.stderr)
                raised = True
            print(
                f"  {key}: {MAIN_REF}={main_baseline[key]} local={local[key]} (+{local[key] - main_baseline[key]})",
                file=sys.stderr,
            )
    if raised:
        print("", file=sys.stderr)
        print("The ruff complexity baseline may only shrink. Refactor instead of raising.", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    args = parse_ratchet_args("Check that ruff C901/PLR0912/PLR0915 counts do not increase")
    repo_root, src_path, baseline_file = resolve_ratchet_paths(baseline_name=BASELINE_FILE)
    read_baseline, write_baseline = json_baseline_io(RULES)

    current = count_rule_violations(repo_root, src_path)
    baseline = read_json_baseline(baseline_file, RULES)

    if (
        check_baseline_not_raised(
            repo_root,
            raise_probe_counts(
                baseline=baseline,
                current=current,
                update_baseline=args.update_baseline,
            ),
        )
        != 0
    ):
        return 1

    return run_count_ratchet(
        keys=RULES,
        current=current,
        baseline_file=baseline_file,
        update_baseline=args.update_baseline,
        read_baseline=read_baseline,
        write_baseline=write_baseline,
        increase_header="Ruff complexity count increased! (ADR-009 / #1610)",
        increase_hints=(
            "Refactor the new complexity, or justify a baseline ↑ in review.",
            "",
            "To inspect:",
            f"  uv run ruff check {SRC_DIR}/ --select={','.join(RULES)}",
            "  uv run python .pre-commit-hooks/check_ruff_complexity_count.py --update-baseline",
        ),
    )


if __name__ == "__main__":
    sys.exit(main())
