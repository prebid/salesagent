#!/usr/bin/env python3
"""
Pre-commit / quality-ci hook: ratchet ruff pure-complexity rule counts.

Per ADR-009 / #1228 F1 / #1610:
- Track C901, PLR0912, PLR0915 violation counts in ``src/``
- Fail only when a count increases (new complexity debt)
- Auto-lower the baseline when a count decreases
- ``--update-baseline`` rewrites the tracked baseline (review must contest ↑)

Near-copy of ``check_type_ignore_count.py`` with per-rule JSON baseline
(same shape idea as ``.duplication-baseline``).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

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
    # ruff exits 1 when violations exist; that's expected. Fatal = other codes.
    if result.returncode not in (0, 1):
        print("ERROR: ruff failed while counting complexity violations:", file=sys.stderr)
        print(result.stderr[:500] or result.stdout[:500], file=sys.stderr)
        sys.exit(2)

    try:
        findings = json.loads(result.stdout or "[]")
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


def read_baseline(baseline_file: Path) -> dict[str, int] | None:
    if not baseline_file.exists():
        return None
    try:
        data = json.loads(baseline_file.read_text())
        return {rule: int(data.get(rule, 0)) for rule in RULES}
    except (ValueError, OSError, TypeError) as e:
        print(f"Warning: Could not read baseline from {baseline_file}: {e}", file=sys.stderr)
        return None


def write_baseline(baseline_file: Path, counts: dict[str, int]) -> None:
    payload = {rule: counts[rule] for rule in RULES}
    baseline_file.write_text(json.dumps(payload, indent=2) + "\n")


def _format_counts(counts: dict[str, int]) -> str:
    return ", ".join(f"{rule}={counts[rule]}" for rule in RULES)


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

    current = count_rule_violations(repo_root, src_path)
    baseline = read_baseline(baseline_file)

    if baseline is None:
        print(f"No baseline found. Creating {BASELINE_FILE}: {_format_counts(current)}")
        write_baseline(baseline_file, current)
        return 0

    if args.update_baseline:
        print(f"Updating baseline: {_format_counts(baseline)} -> {_format_counts(current)}")
        write_baseline(baseline_file, current)
        return 0

    failed = False
    for rule in RULES:
        base = baseline[rule]
        cur = current[rule]
        if cur > base:
            print(
                f"  {rule}: {cur} (+{cur - base} NEW vs baseline {base})",
                file=sys.stderr,
            )
            failed = True
        elif cur < base:
            print(f"  {rule}: {cur} (-{base - cur} fixed vs baseline {base})")
        else:
            print(f"  {rule}: {cur} (unchanged)")

    if failed:
        print("", file=sys.stderr)
        print("Ruff complexity count increased! (ADR-009 / #1610)", file=sys.stderr)
        print("Refactor the new complexity, or justify a baseline ↑ in review.", file=sys.stderr)
        print("", file=sys.stderr)
        print("To inspect:", file=sys.stderr)
        print(
            f"  uv run ruff check {SRC_DIR}/ --select={','.join(RULES)}",
            file=sys.stderr,
        )
        print(
            "  uv run python .pre-commit-hooks/check_ruff_complexity_count.py --update-baseline",
            file=sys.stderr,
        )
        return 1

    if current != baseline:
        print(f"Automatically updating {BASELINE_FILE}...")
        write_baseline(baseline_file, current)

    return 0


if __name__ == "__main__":
    sys.exit(main())
