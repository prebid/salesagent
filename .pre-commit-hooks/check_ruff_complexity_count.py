#!/usr/bin/env python3
"""
Pre-commit / quality-ci hook: ratchet ruff pure-complexity rule counts.

Per ADR-009 / #1228 F1 / #1610:
- Track C901, PLR0912, PLR0915 violation counts in ``src/``
- Fail only when a count increases (new complexity debt)
- Auto-lower the baseline when a count decreases
- ``--update-baseline`` rewrites the tracked baseline (review must contest ↑)

Uses shared ``count_ratchet.run_count_ratchet`` for the create/compare/auto-lower
skeleton; this module owns the ruff count method + JSON baseline path only.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

from count_ratchet import read_json_baseline, run_count_ratchet, write_json_baseline

BASELINE_FILE = ".ruff-complexity-baseline"
SRC_DIR = "src"
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
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=repo_root)
    # ruff exits 1 when violations exist; that's expected. Fatal = other codes,
    # or exit 1 with empty stdout (e.g. `python -m ruff` ModuleNotFoundError) —
    # empty findings must not auto-lower the baseline to zero.
    stdout = (result.stdout or "").strip()
    if result.returncode not in (0, 1) or (result.returncode == 1 and not stdout):
        print("ERROR: ruff failed while counting complexity violations:", file=sys.stderr)
        print(result.stderr[:500] or result.stdout[:500], file=sys.stderr)
        sys.exit(2)

    try:
        findings = json.loads(stdout or "[]")
    except json.JSONDecodeError as e:
        print(f"ERROR: could not parse ruff JSON output: {e}", file=sys.stderr)
        print(result.stdout[:500], file=sys.stderr)
        sys.exit(2)

    counts = dict.fromkeys(RULES, 0)
    for item in findings:
        code = item.get("code")
        if code in counts:
            counts[code] += 1
    return counts


def _read_baseline(baseline_file: Path) -> dict[str, int] | None:
    return read_json_baseline(baseline_file, RULES)


def _write_baseline(baseline_file: Path, counts: Mapping[str, int]) -> None:
    write_json_baseline(baseline_file, counts, RULES)


def main() -> int:
    parser = argparse.ArgumentParser(description="Check that ruff C901/PLR0912/PLR0915 counts do not increase")
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Force update baseline to current counts (↑ must be justified in review)",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).parent.parent
    src_path = repo_root / SRC_DIR
    baseline_file = repo_root / BASELINE_FILE

    if not src_path.exists():
        print(f"Error: {SRC_DIR}/ directory not found", file=sys.stderr)
        return 1

    return run_count_ratchet(
        keys=RULES,
        current=count_rule_violations(repo_root, src_path),
        baseline_file=baseline_file,
        update_baseline=args.update_baseline,
        read_baseline=_read_baseline,
        write_baseline=_write_baseline,
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
